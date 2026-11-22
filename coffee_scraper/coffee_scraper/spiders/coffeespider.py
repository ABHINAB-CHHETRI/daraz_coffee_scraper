from datetime import datetime, timezone
import scrapy


class CoffeeSpider(scrapy.Spider):
    name = "coffee_spider"
    allowed_domains = ["daraz.com.np"]
    custom_settings = {
        "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 2.5,
        "DOWNLOAD_DELAY_JITTER": 0.5,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "RETRY_TIMES": 1,
    }

    def __init__(self, max_items=500, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_items = int(max_items)
        self.scraped_count = 0
        self.scrape_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def start(self):
        yield scrapy.Request(
            "https://www.daraz.com.np/catalog/?q=coffee&ajax=true&page=1",
            callback=self.parse,
            cb_kwargs={"page": 1},
        )

    def parse(self, response, page):
        try:
            products = response.json().get("mods", {}).get("listItems", [])
        except Exception:
            self.logger.warning("Non-JSON response on page %s; stopping crawl.", page)
            return

        if not products:
            return

        for p in products:
            if self.scraped_count >= self.max_items:
                return

            self.scraped_count += 1
            yield {
                "item_id": str(p.get("itemId", "")).strip(),
                "name": (p.get("name") or "").strip(),
                "price_raw": p.get("priceShow") or p.get("price"),
                "original_price_raw": p.get("originalPriceShow") or p.get("originalPrice"),
                "sold_raw": p.get("itemSoldCntShow") or "0 sold",
                "rating_raw": p.get("ratingScore"),
                "reviews_raw": p.get("review"),
                "seller": (p.get("sellerName") or "Unknown").strip(),
                "location": (p.get("location") or "Nepal").strip(),
                "image": response.urljoin(p.get("image") or ""),
                "link": response.urljoin(p.get("itemUrl") or ""),
                "scraped_date": self.scrape_date,
            }

        if self.scraped_count < self.max_items:
            next_page = page + 1
            yield scrapy.Request(
                f"https://www.daraz.com.np/catalog/?q=coffee&ajax=true&page={next_page}",
                callback=self.parse,
                cb_kwargs={"page": next_page},
            )