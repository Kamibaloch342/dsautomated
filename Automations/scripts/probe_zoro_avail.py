#!/usr/bin/env python3
"""
probe_zoro_avail.py — dump Zoro's FULL availability object for a SKU, so we can see the
lead-time / ship-estimate / stock-quantity field names and wire them. Places NO order.

It connects to an already-open, logged-in Zoro Chrome (one of the placer profiles' debug
ports: 9221 DM_COUPON, 9222 DM_RESELLER, 9223 HS_COUPON, 9224 HS_RESELLER). That Chrome
must be on a zoro.com page / logged in.

Run:  ../.venv/bin/python scripts/probe_zoro_avail.py 9221 G7646065
"""
import os, sys

os.environ["ZORO_INV_DEBUG"] = "1"   # makes check_inventory print the full avail object
_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _DIR)

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import platforms.zoro as zoro

port = sys.argv[1] if len(sys.argv) > 1 else "9221"
sku = sys.argv[2] if len(sys.argv) > 2 else "G7646065"

opts = Options()
opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
driver = webdriver.Chrome(options=opts)
print(f"Connected to Chrome on {port}; URL: {driver.current_url}")
print(f"Probing availability for SKU {sku} ...")
res = zoro.check_inventory(driver, sku, 1)
print("check_inventory() returned:", res)
print("\n^ paste the [ZORO INV RAW] line above to me.")
