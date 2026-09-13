#!/usr/bin/env python3
"""
scrape_brady.py — Scrapes live Amazon competing offers for top Brady ASINs.
"""

import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
from batch_scrape_offers import scrape_asins

BRADY_ASINS = [
    "B00IELFF52",
    "B005HE0S88",
    "B003XU7T58",
    "B0063JLU88",
    "B0063JLQFK",
    "B003XU7T4Y",
]

if __name__ == "__main__":
    print(f"[*] Starting live scrape for {len(BRADY_ASINS)} Brady ASINs...")
    results = scrape_asins(BRADY_ASINS, zipcode="10001", force=False)
    print(f"[✓] Successfully scraped and cached {len(results)} Brady ASINs.")
