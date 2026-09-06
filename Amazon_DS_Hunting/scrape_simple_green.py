#!/usr/bin/env python3
"""
scrape_simple_green.py — Live offer scraper for matched Simple Green ASINs.
Scrapes all 18 matched high-velocity commercial listings.
"""

import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
from batch_scrape_offers import scrape_asins

target_asins = [
    "B0000AXNO5",
    "B00103YQ88",
    "B079SJH7LH",
    "B07DVVJQCG",
    "B000VBA8FK",
    "B001VXU7OE",
    "B07DVJSNJF",
    "B07DV21X32",
    "B00ED3K1VI",
    "B0DW736Y99",
    "B0013CIVVE",
    "B07DVQPW11",
    "B0CXJQ93F4",
    "B0DW6PC7YV",
    "B0DW6NYWP6",
    "B0937M5YK8",
    "B0DW6YMZQP",
    "B004E3L13O",
]

if __name__ == "__main__":
    print(f"[*] Scraping live offers for {len(target_asins)} matched Simple Green ASINs...")
    results = scrape_asins(target_asins, zipcode="10001", force=False)
    print(f"[✓] Successfully scraped {len(results)} listings.")
