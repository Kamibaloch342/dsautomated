#!/usr/bin/env python3
"""
scrape_rust_oleum.py — Live offer scraper for matched Rust-Oleum ASINs.
Scrapes all 15 matched high-velocity Painter's Touch 2X spray paint listings.
"""

import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
from batch_scrape_offers import scrape_asins

target_asins = [
    "B002BWOS6C",
    "B002BWOS5I",
    "B002BWOS7Q",
    "B002BWORSG",
    "B002BWOS4O",
    "B002BWOS9Y",
    "B002BWOS44",
    "B002BWOS58",
    "B002BWOS5S",
    "B07W9KPB78",
    "B002BWOS1M",
    "B06X9MWSQW",
    "B002BWOS62",
    "B002BWOS12",
    "B002BWOS4Y",
]

print(f"[*] Scraping live offers for {len(target_asins)} matched Rust-Oleum ASINs...")
results = scrape_asins(target_asins, zipcode="10001", force=False)
print(f"[✓] Successfully scraped {len(results)} listings.")
