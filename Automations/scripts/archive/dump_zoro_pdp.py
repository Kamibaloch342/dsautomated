#!/usr/bin/env python3
"""
dump_zoro_pdp.py — open a Zoro product page in the already-connected Chrome (port 9221)
and save the FULL rendered HTML so we can find the "Delivery on or before <date>" element
and any embedded delivery/stock data. Places NO order.

Usage:
  ../.venv/bin/python scripts/dump_zoro_pdp.py G9109803         # by Zoro SKU
  ../.venv/bin/python scripts/dump_zoro_pdp.py "https://www.zoro.com/.../i/G9109803/"   # by URL
"""
import sys, os, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
arg = sys.argv[1] if len(sys.argv) > 1 else "G9109803"

opts = Options()
opts.add_experimental_option("debuggerAddress", "127.0.0.1:9221")
d = webdriver.Chrome(options=opts)

if arg.startswith("http"):
    d.get(arg)
    time.sleep(6)
else:
    d.get(f"https://www.zoro.com/search?q={arg}")
    time.sleep(5)
    if "/i/" not in d.current_url:           # not auto-redirected → open first product result
        links = d.find_elements(By.CSS_SELECTOR, "a[href*='/i/']")
        if links:
            d.get(links[0].get_attribute("href"))
            time.sleep(6)

html = d.page_source
out = os.path.join(_DIR, "zoro_pdp_dump.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("URL  :", d.current_url)
print("saved:", out, f"({len(html):,} bytes)")
print("done — tell me it's saved and I'll analyze it.")
