#!/usr/bin/env python3
"""
scrape_b2b_brands.py — Live offer scraper for Georgia-Pacific, Purell, Nu-Calgon, and Keson.
Scrapes competing offers via persistent undetected Chrome with NYC ZIP 10001.
"""

import os
import sys
import json

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
from batch_scrape_offers import scrape_asins

MATCHED_JSON = os.path.join(CURRENT_DIR, "b2b_matched_items.json")

def get_target_asins():
    with open(MATCHED_JSON, "r") as f:
        items = json.load(f)
    # Target all exact pack matches first, then high-velocity mismatches
    return [x["asin"] for x in items]

if __name__ == "__main__":
    asins = get_target_asins()
    print(f"[*] Starting live scrape for {len(asins)} Georgia-Pacific, Purell, & Nu-Calgon ASINs...")
    results = scrape_asins(asins, zipcode="10001", force=False)
    print(f"[✓] Successfully scraped and cached {len(results)} ASINs.")
