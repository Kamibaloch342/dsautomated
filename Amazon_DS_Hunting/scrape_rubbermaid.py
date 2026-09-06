#!/usr/bin/env python3
"""
scrape_rubbermaid.py — Live offer scraper for matched Rubbermaid ASINs.
"""

import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
from batch_scrape_offers import scrape_asins

target_asins = [
    "B0009JQRYA",
    "B004BAKLKO",
    "B07C461QPH",
    "B0009J5NTA",
    "B004BAKJPQ",
    "B0009J5NTK",
    "B0009J5NV8",
    "B0009J5NSQ",
    "B000R8JOUC",
    "B0006384LI",
    "B00BEUDUHU",
    "B00006IA5D",
]

print(f"[*] Scraping live offers for {len(target_asins)} matched Rubbermaid ASINs...")
results = scrape_asins(target_asins, zipcode="10001", force=False)
print(f"[✓] Successfully scraped {len(results)} listings.")
