#!/usr/bin/env python3
"""
scrape_multiple_brands.py — Live offer scraper for matched Multiple Brands ASINs.
Scrapes RIDGID, Timberland PRO, and Buyers Products Company listings.
"""

import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
from batch_scrape_offers import scrape_asins

target_asins = [
    "B001P307PO",
    "B078YYD66B",
    "B009R9EJO8",
    "B009R9EK6A",
    "B0000302U0",
    "B009R9EJ3E",
    "B000H5XSS4",
    "B001P81OKG",
    "B06XR278C5",
    "B000WT6U8U",
    "B06XRJHFF1",
    "B07FHRP1SL",
    "B01FZ55F7C",
    "B079Q2Q3TT",
    "B0002YPMUM",
    "B0009W6T8G",
    "B06XRHB7GH",
    "B07B4KB9JG",
    "B0000224JF",
    "B000E3Z0EY",
    "B000LDGNCU",
    "B001P81OK6",
    "B003IEPCXA",
    "B0CVNMR1HS",
    "B0CVNN61S5",
    "B0CVNN81PK",
    "B0CVNNRRDW",
    "B076BGBRDF",
    "B01HS8VRKS",
    "B0029NY9XK",
    "B000VZC9XA",
    "B07F4XWNRD",
    "B0006PJNVQ",
    "B0000224JG",
    "B0009W9BT0",
    "B0000224JE",
    "B07GKVKV5W",
    "B0087PEKTC",
    "B001HWQAI6",
    "B0038G3RCQ",
    "B001P81OJW",
    "B00BTLN52O",
]

print(f"[*] Scraping live offers for {len(target_asins)} matched Multiple Brands ASINs...")
results = scrape_asins(target_asins, zipcode="10001", force=False)
print(f"[✓] Successfully scraped {len(results)} listings.")
