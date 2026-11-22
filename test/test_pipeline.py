import sys
from pathlib import Path
import pytest
from scrapy.exceptions import DropItem

# Allow importing from coffee_scraper/coffee_scraper
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "coffee_scraper"))

from coffee_scraper.pipelines import CoffeeCleaningPipeline


def make_raw_item(**overrides):
    base = {
        "item_id": "1001",
        "name": "Himalayan Arabica Medium Roast Coffee Beans 500gm",
        "price_raw": "Rs. 1,500",
        "original_price_raw": "Rs. 2,000",
        "sold_raw": "1.3K sold",
        "rating_raw": "4.856",
        "reviews_raw": "42",
        "seller": "Nepal Roasters",
        "location": "Bagmati Province",
        "image": "https://example.com/coffee.jpg",
        "link": "https://www.daraz.com.np/products/test-i1001.html",
        "scraped_date": "2026-11-22",
    }
    base.update(overrides)
    return base


def test_normalizes_prices_weights_and_metrics():
    pipeline = CoffeeCleaningPipeline()
    cleaned = pipeline.process_item(make_raw_item())

    assert cleaned["price"] == 1500.0
    assert cleaned["discount_pct"] == 25
    assert cleaned["weight_grams"] == 500
    assert cleaned["price_per_100g"] == 300.0
    assert cleaned["sold"] == 1300
    assert cleaned["rating"] == 4.86
    assert cleaned["coffee_type"] == "Whole Beans"
    assert cleaned["roast_level"] == "Medium"


def test_parses_kilogram_weights():
    pipeline = CoffeeCleaningPipeline()
    cleaned = pipeline.process_item(make_raw_item(item_id="1002", name="NepaBeans Espresso 1kg", price_raw="Rs. 2,600"))

    assert cleaned["weight_grams"] == 1000
    assert cleaned["price_per_100g"] == 260.0


def test_drops_duplicates_and_hardware():
    pipeline = CoffeeCleaningPipeline()
    pipeline.process_item(make_raw_item(item_id="1003"))

    with pytest.raises(DropItem):
        pipeline.process_item(make_raw_item(item_id="1003"))

    with pytest.raises(DropItem):
        pipeline.process_item(make_raw_item(item_id="1004", name="Electric Coffee Grinder 500W"))