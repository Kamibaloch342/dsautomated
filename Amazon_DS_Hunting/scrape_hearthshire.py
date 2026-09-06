#!/usr/bin/env python3
"""
scrape_hearthshire.py — Live offer scraper for matched Hearthshire competitor ASINs.
Scrapes all 29 matched commercial items (Channellock, Surecan, JT Eaton, IRON OUT, 3M, Oregon, etc.).
"""

import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
from batch_scrape_offers import scrape_asins

target_asins = [
    "B00DLKGD3Y",
    "B013OVC9K0",
    "B01DZQT3IU",
    "B0CQ8VR9XL",
    "B0B2HXZG5Q",
    "B00LHZFKC0",
    "B011C34EVE",
    "B07GBQKLWZ",
    "B009RWUZPW",
    "B00004SBCV",
    "B00002N5JF",
    "B00004SBCR",
    "B0006694A0",
    "B000Y8499G",
    "B07LC38RMK",
    "B0049PJR1U",
    "B00080OCS8",
    "B00004SBCS",
    "B000040OUZ",
    "B00BYQD39Y",
    "B000TG80SY",
    "B01NAKAS40",
    "B07PNQPFG6",
    "B00ZIM9XPI",
    "B005H3PG7M",
    "B000IF77R6",
    "B00TSJWD7C",
    "B000VUAZB8",
    "B01VGW24E",
]

print(f"[*] Scraping live offers for {len(target_asins)} matched Hearthshire ASINs...")
results = scrape_asins(target_asins, zipcode="10001", force=False)
print(f"[✓] Successfully scraped {len(results)} listings.")
