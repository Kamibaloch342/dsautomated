#!/usr/bin/env python3
"""
batch_scrape_offers.py — High-speed batch scraper for Amazon listing offers.
Uses a single persistent browser session, sets delivery ZIP to 10001 once,
and extracts AOD offers for all provided ASINs.
"""

import os
import sys
import json
import time
import re
from typing import List, Dict, Any

# Ensure project root is in path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "Automations", "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from amazon_listing_offers import (
    get_driver,
    set_amazon_zipcode,
    click_all_offers_button,
    parse_all_offers_from_page,
    clean_text,
)

CACHE_DIR = os.path.join(CURRENT_DIR, ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

def scrape_asins(asins: List[str], zipcode: str = "10001", force: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    results = {}
    to_scrape = []
    
    for asin in asins:
        cache_path = os.path.join(CACHE_DIR, f"{asin}.json")
        if not force and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data:
                        results[asin] = data
                        print(f"[*] Loaded {asin} from cache ({len(data)} offers)")
                        continue
            except Exception:
                pass
        to_scrape.append(asin)

    if not to_scrape:
        print("[*] All ASINs already cached.")
        return results

    print(f"[*] Launching persistent Chrome session to scrape {len(to_scrape)} ASINs...")
    driver = get_driver(headless=True)
    zip_set = False

    try:
        for idx, asin in enumerate(to_scrape, 1):
            url = f"https://www.amazon.com/dp/{asin}"
            print(f"\n[{idx}/{len(to_scrape)}] Navigating to ASIN {asin} ({url})...")
            driver.get(url)
            time.sleep(2.5)

            # Handle captcha check
            if "validateCaptcha" in driver.page_source:
                print("    ⚠️ CAPTCHA detected! Waiting 5s and refreshing...")
                time.sleep(5)
                driver.get(url)
                time.sleep(3)

            # Set delivery ZIP code once on the first ASIN
            if not zip_set:
                set_amazon_zipcode(driver, zipcode)
                zip_set = True
                time.sleep(2.0)

            # Click all offers button
            click_all_offers_button(driver)
            time.sleep(3.5)

            # Parse offers
            html = driver.page_source
            offers = parse_all_offers_from_page(html)

            # If no offers parsed via AOD, check if Buy Box exists
            if not offers:
                print(f"    ⚠️ No AOD offers found for {asin}, checking page elements...")

            print(f"    ✓ Extracted {len(offers)} offers for {asin}")
            results[asin] = offers

            # Cache results
            cache_path = os.path.join(CACHE_DIR, f"{asin}.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(offers, f, indent=2, ensure_ascii=False)

            # Brief pause between listings
            if idx < len(to_scrape):
                time.sleep(2.0)

    finally:
        driver.quit()

    return results

if __name__ == "__main__":
    target_asins = [
        "B01H1VKNUC",
        "B004I665XS",
        "B00BUBUOPO",
        "B00BUBUS96",
        "B00BUBSZ3C",
        "B00BUBHXIU",
        "B00BUBU6Z2",
        "B00BUCB5YM",
    ]
    scrape_asins(target_asins)
