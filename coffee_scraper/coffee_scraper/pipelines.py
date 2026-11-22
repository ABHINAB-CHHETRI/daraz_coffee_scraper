import json
import re
import sqlite3
from pathlib import Path
from scrapy.exceptions import DropItem

# Resolves to daraz_coffee_scraper/ regardless of where you run the command
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "coffee_history.db"
JSON_OUT_PATH = PROJECT_ROOT / "public" / "coffee_listings.json"

HARDWARE_PATTERN = re.compile(
    r"\b(coffee\s*maker|espresso\s*machine|grinder|frother|tamper|kettle|filter\s*paper|travel\s*mug|coffee\s*cup|vacuum\s*flask|moka\s*pot|reusable\s*capsule|pitcher)\b",
    re.IGNORECASE,
)
WEIGHT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|gm|gms|gram|grams)\b", re.IGNORECASE)


class CoffeeCleaningPipeline:
    def __init__(self):
        self.seen_ids = set()

    def process_item(self, item):
        item_id = item.get("item_id")
        name = item.get("name", "")

        # 1. Deduplicate within the same crawl
        if not item_id or item_id in self.seen_ids:
            raise DropItem(f"Duplicate or missing item_id: {item_id}")
        self.seen_ids.add(item_id)

        # 2. Filter out non-coffee equipment/accessories
        if HARDWARE_PATTERN.search(name):
            raise DropItem(f"Filtered accessory: {name}")

        # 3. Parse numeric prices & discount
        price = self._parse_float(item.get("price_raw"))
        if not price or price <= 0:
            raise DropItem(f"Invalid price for: {name}")

        orig_price = self._parse_float(item.get("original_price_raw"))
        discount_pct = 0
        if orig_price and orig_price > price:
            discount_pct = round(((orig_price - price) / orig_price) * 100)

        # 4. Parse sold count ("1.3K sold" -> 1300), rating, and review count
        sold = self._parse_sold(item.get("sold_raw"))
        rating = self._parse_float(item.get("rating_raw"))
        reviews = int(self._parse_float(item.get("reviews_raw")) or 0)

        # 5. Feature engineering: weight in grams & normalized price_per_100g
        weight_grams = self._extract_weight_grams(name)
        price_per_100g = round((price / weight_grams) * 100, 2) if weight_grams else None

        # 6. Classify product type & roast level
        coffee_type = self._classify_type(name)
        roast_level = self._classify_roast(name)

        return {
            "item_id": item_id,
            "name": name,
            "price": price,
            "price_display": f"Rs. {int(round(price)):,}",
            "original_price": orig_price,
            "discount_pct": discount_pct,
            "weight_grams": weight_grams,
            "price_per_100g": price_per_100g,
            "sold": sold,
            "sold_display": item.get("sold_raw") or "0 sold",
            "rating": round(rating, 2) if rating else None,
            "reviews": reviews,
            "coffee_type": coffee_type,
            "roast_level": roast_level,
            "seller": item.get("seller"),
            "location": item.get("location"),
            "image": item.get("image"),
            "link": item.get("link"),
            "scraped_date": item.get("scraped_date"),
        }

    @staticmethod
    def _parse_float(val):
        if val is None:
            return None
        # Strip "Rs." currency prefix and commas before extracting the number
        cleaned = re.sub(r"(?i)rs\.?", "", str(val)).replace(",", "")
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        return float(match.group(0)) if match else None

    @staticmethod
    def _parse_sold(sold_str):
        if not sold_str:
            return 0
        match = re.search(r"([\d.]+)\s*([kKmM]?)", str(sold_str))
        if not match:
            return 0
        num = float(match.group(1))
        unit = match.group(2).upper()
        if unit == "K":
            num *= 1000
        elif unit == "M":
            num *= 1_000_000
        return int(round(num))

    @staticmethod
    def _extract_weight_grams(name):
        match = WEIGHT_PATTERN.search(name)
        if not match:
            return None
        val = float(match.group(1))
        unit = match.group(2).lower()
        grams = val * 1000 if unit == "kg" else val
        return int(round(grams)) if 5 <= grams <= 10000 else None

    @staticmethod
    def _classify_type(name):
        n = name.lower()
        if "drip" in n:
            return "Drip Bag"
        if "3 in 1" in n or "3-in-1" in n or "premix" in n:
            return "3-in-1 / Premix"
        if "instant" in n or "soluble" in n or "freeze dried" in n:
            return "Instant"
        if "bean" in n:
            return "Whole Beans"
        if "ground" in n or "powder" in n or "french press" in n or "espresso" in n:
            return "Ground Coffee"
        return "Other"

    @staticmethod
    def _classify_roast(name):
        n = name.lower()
        if "medium-dark" in n or "medium dark" in n:
            return "Medium-Dark"
        if "light" in n:
            return "Light"
        if "medium" in n or "omni" in n:
            return "Medium"
        if "dark" in n:
            return "Dark"
        return "Unspecified"


class SQLiteAndJsonPipeline:
    def open_spider(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        JSON_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(DB_PATH)
        self.cursor = self.conn.cursor()
        self._init_tables()
        self.exported_items = []

    def _init_tables(self):
        self.cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                item_id TEXT PRIMARY KEY,
                name TEXT,
                seller TEXT,
                location TEXT,
                coffee_type TEXT,
                roast_level TEXT,
                weight_grams INTEGER,
                link TEXT,
                image TEXT,
                first_seen TEXT,
                last_seen TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT,
                scraped_date TEXT,
                price REAL,
                original_price REAL,
                discount_pct INTEGER,
                price_per_100g REAL,
                sold INTEGER,
                rating REAL,
                reviews INTEGER,
                UNIQUE(item_id, scraped_date),
                FOREIGN KEY(item_id) REFERENCES products(item_id)
            );
            """
        )
        self.conn.commit()

    def process_item(self, item):
        item_id = item["item_id"]
        today = item["scraped_date"]

        self.cursor.execute(
            """
            SELECT price, sold FROM daily_snapshots
            WHERE item_id = ? AND scraped_date < ?
            ORDER BY scraped_date DESC LIMIT 1
            """,
            (item_id, today),
        )
        prev = self.cursor.fetchone()
        price_change = round(item["price"] - prev[0], 2) if prev else 0.0
        sold_delta = max(0, item["sold"] - prev[1]) if prev else 0

        self.cursor.execute(
            """
            INSERT INTO products (item_id, name, seller, location, coffee_type, roast_level, weight_grams, link, image, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                name = excluded.name,
                coffee_type = excluded.coffee_type,
                roast_level = excluded.roast_level,
                weight_grams = excluded.weight_grams,
                last_seen = excluded.last_seen
            """,
            (
                item_id,
                item["name"],
                item["seller"],
                item["location"],
                item["coffee_type"],
                item["roast_level"],
                item["weight_grams"],
                item["link"],
                item["image"],
                today,
                today,
            ),
        )

        self.cursor.execute(
            """
            INSERT INTO daily_snapshots (item_id, scraped_date, price, original_price, discount_pct, price_per_100g, sold, rating, reviews)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id, scraped_date) DO UPDATE SET
                price = excluded.price,
                original_price = excluded.original_price,
                discount_pct = excluded.discount_pct,
                price_per_100g = excluded.price_per_100g,
                sold = excluded.sold,
                rating = excluded.rating,
                reviews = excluded.reviews
            """,
            (
                item_id,
                today,
                item["price"],
                item["original_price"],
                item["discount_pct"],
                item["price_per_100g"],
                item["sold"],
                item["rating"],
                item["reviews"],
            ),
        )

        item["price_change"] = price_change
        item["sold_delta"] = sold_delta
        self.exported_items.append(item)
        return item

    def close_spider(self):
        self.conn.commit()
        self.conn.close()

        with open(JSON_OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(self.exported_items, f, ensure_ascii=False, indent=2)