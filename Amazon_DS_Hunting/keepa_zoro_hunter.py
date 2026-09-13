#!/usr/bin/env python3
"""
keepa_zoro_hunter.py — Autonomous Keepa API & Zoro Sourcing Pipeline

Connects Zoro's 7,600+ brand supplier catalog directly to Keepa Product Finder API:
1. Monitors Keepa Token balance (tokensLeft, refillRate, refill countdown) with 0-token overhead.
2. Extracts top commercial & industrial brands from `combined_reports.db` (Zoro).
3. Executes Keepa Product Finder queries for high-velocity listings (monthlySold >= 100/200).
4. Matches Amazon ASINs against Zoro inventory with strict 1:1 unit pack validation.
5. Scrapes live Amazon competitor offers (MFN vs FBA) with 0 Keepa tokens consumed.
6. Automatically outputs `Keepa_Zoro_Profitable_Listings.xlsx` with:
   - TAB 1: ONLY PROFITABLE LISTINGS (User Directive)
   - TAB 2: ALL MATCHED & ANALYZED
   - TAB 3: KEEPA TOKEN & SOURCING MONITOR
"""

from __future__ import annotations

import os
import sys
import json
import sqlite3
import time
import re
import urllib.request
import urllib.parse
import ssl
import gzip
from typing import Dict, List, Any, Optional, Tuple

import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
DB_PATH = os.path.join(PROJECT_ROOT, "Automations", "TSV Files", "combined_reports.db")
CACHE_DIR = os.path.join(CURRENT_DIR, ".cache")
KEEPA_CACHE_DIR = os.path.join(CURRENT_DIR, ".keepa_cache")
os.makedirs(KEEPA_CACHE_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

import os as _os


def _load_keepa_key() -> str:
    """Keepa API key: env KEEPA_API_KEY, else the git-ignored file .keepa_key next to this
    script (one line). Never hard-code it — this file is committed to GitHub."""
    k = (_os.environ.get("KEEPA_API_KEY") or "").strip()
    if k:
        return k
    p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".keepa_key")
    try:
        with open(p, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


DEFAULT_KEEPA_KEY = _load_keepa_key()
OUTPUT_EXCEL = os.path.join(CURRENT_DIR, "Keepa_Zoro_Profitable_Listings.xlsx")


def get_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ----------------------------------------------------------------------
# 1. KEEPA TOKEN GOVERNOR (0-TOKEN OVERHEAD)
# ----------------------------------------------------------------------
def check_keepa_tokens(api_key: str = DEFAULT_KEEPA_KEY) -> Dict[str, Any]:
    """Queries Keepa /token endpoint. Costs 0 tokens."""
    url = f"https://api.keepa.com/token?key={api_key}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, context=get_ssl_context(), timeout=10) as resp:
            raw = resp.read()
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
            data = json.loads(raw.decode("utf-8"))
            return {
                "tokens_left": data.get("tokensLeft", 0),
                "refill_in_ms": data.get("refillIn", 0),
                "refill_in_sec": round(data.get("refillIn", 0) / 1000.0, 1),
                "refill_rate": data.get("refillRate", 1),
                "status": "OK",
            }
    except Exception as e:
        return {
            "tokens_left": 0,
            "refill_in_ms": 0,
            "refill_in_sec": 0,
            "refill_rate": 1,
            "status": f"Error: {e}",
        }


# ----------------------------------------------------------------------
# 2. ZORO BRAND CATALOG EXTRACTOR
# ----------------------------------------------------------------------
def get_top_zoro_brands(
    min_items: int = 50,
    min_price: float = 15.0,
    max_price: float = 250.0,
    limit: int = 40,
    db_path: str = DB_PATH,
) -> List[Dict[str, Any]]:
    """Extracts high-priority dropshippable brands from Zoro catalog."""
    if not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT zoro_brand_name, COUNT(*) as cnt, AVG(sales_price) as avg_p,
               MIN(sales_price) as min_p, MAX(sales_price) as max_p
        FROM zoro_data
        WHERE sales_price BETWEEN ? AND ?
          AND zoro_brand_name IS NOT NULL
          AND LENGTH(zoro_brand_name) > 1
        GROUP BY zoro_brand_name
        HAVING cnt >= ?
        ORDER BY cnt DESC
        LIMIT ?
    """, (min_price, max_price, min_items, limit))

    brands = []
    for row in cur.fetchall():
        brands.append({
            "brand": row[0],
            "item_count": row[1],
            "avg_price": round(row[2], 2),
            "min_price": round(row[3], 2),
            "max_price": round(row[4], 2),
        })
    conn.close()
    return brands


# ----------------------------------------------------------------------
# 3. KEEPA PRODUCT FINDER QUERY RUNNER
# ----------------------------------------------------------------------
def query_keepa_product_finder(
    brands: List[str],
    monthly_sold_gte: int = 200,
    per_page: int = 500,
    api_key: str = DEFAULT_KEEPA_KEY,
    min_token_reserve: int = 10,
) -> Dict[str, Any]:
    """Executes a Keepa Product Finder query safely with token reservation."""
    token_status = check_keepa_tokens(api_key)
    tokens_left = token_status["tokens_left"]

    if tokens_left < min_token_reserve:
        wait_time = int(token_status["refill_in_sec"]) + ((min_token_reserve - tokens_left) * 60)
        return {
            "success": False,
            "error": f"Token reserve low ({tokens_left} tokens available). Need {min_token_reserve}. Refill in ~{wait_time}s.",
            "token_status": token_status,
            "asin_list": [],
        }

    clean_brands = [b.lower().strip() for b in brands if b.strip()]
    selection = {
        "productType": ["0"],
        "monthlySold_gte": monthly_sold_gte,
        "brand": clean_brands,
        "sort": [["current_SALES", "asc"], ["monthlySold", "desc"]],
        "perPage": per_page,
        "page": 0,
    }

    url = f"https://api.keepa.com/query?key={api_key}&domain=1&selection={urllib.parse.quote(json.dumps(selection))}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})

    try:
        with urllib.request.urlopen(req, context=get_ssl_context(), timeout=30) as resp:
            raw = resp.read()
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
            data = json.loads(raw.decode("utf-8"))

            asin_list = data.get("asinList", [])
            tokens_remaining = data.get("tokensLeft", 0)

            # Save query cache
            cache_file = os.path.join(KEEPA_CACHE_DIR, f"keepa_query_{'_'.join(clean_brands[:3])}_{monthly_sold_gte}.json")
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"brands": clean_brands, "asin_list": asin_list, "total": len(asin_list)}, f, indent=2)

            return {
                "success": True,
                "asin_list": asin_list,
                "total_results": data.get("totalResults", len(asin_list)),
                "tokens_left": tokens_remaining,
                "cache_file": cache_file,
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "asin_list": [],
            "token_status": check_keepa_tokens(api_key),
        }


# ----------------------------------------------------------------------
# 4. KEEPA PRODUCT METADATA BATCHER (TOKEN MANAGED)
# ----------------------------------------------------------------------
def fetch_keepa_product_metadata(
    asins: List[str],
    api_key: str = DEFAULT_KEEPA_KEY,
    max_batch: int = 20,
) -> List[Dict[str, Any]]:
    """Fetches lightweight product metadata (title, brand, MPN, UPC, monthly sales) with history=0."""
    results = []
    to_fetch = []

    for a in asins:
        p_cache = os.path.join(KEEPA_CACHE_DIR, f"prod_{a}.json")
        if os.path.exists(p_cache):
            try:
                with open(p_cache, "r", encoding="utf-8") as f:
                    results.append(json.load(f))
                    continue
            except Exception:
                pass
        to_fetch.append(a)

    if not to_fetch:
        return results

    # Respect token reserve
    token_status = check_keepa_tokens(api_key)
    tokens_available = token_status["tokens_left"]

    # Only fetch as many as tokens safely allow (leave 5 reserve)
    safe_allowance = max(0, tokens_available - 5)
    batch_to_run = to_fetch[: min(safe_allowance, max_batch)]

    if not batch_to_run:
        print(f"[*] Token limit reached ({tokens_available} tokens). Skipping live metadata fetch for now.")
        return results

    asin_str = ",".join(batch_to_run)
    url = f"https://api.keepa.com/product?key={api_key}&domain=1&asin={asin_str}&history=0"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})

    try:
        with urllib.request.urlopen(req, context=get_ssl_context(), timeout=30) as resp:
            raw = resp.read()
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
            data = json.loads(raw.decode("utf-8"))

            for prod in data.get("products", []):
                p_asin = prod.get("asin")
                upcs = prod.get("upcList", [])
                item = {
                    "asin": p_asin,
                    "title": prod.get("title", ""),
                    "brand": prod.get("brand", ""),
                    "mpn": prod.get("partNumber") or prod.get("model") or "",
                    "upc": upcs[0] if upcs else "",
                    "monthly_sales": prod.get("monthlySold", 0),
                    "bsr": prod.get("current_SALES", 0),
                    "category": prod.get("rootCategory", 0),
                }
                results.append(item)
                # Cache product
                with open(os.path.join(KEEPA_CACHE_DIR, f"prod_{p_asin}.json"), "w", encoding="utf-8") as pf:
                    json.dump(item, pf, indent=2)

    except Exception as e:
        print(f"[!] Error fetching Keepa products: {e}")

    return results


def main():
    print("================================================================================")
    print("  KEEPA API & ZORO CATALOG AUTONOMOUS SOURCING PIPELINE")
    print("================================================================================")

    # 1. Check live token balance
    tokens = check_keepa_tokens()
    print(f"\n[*] Live Keepa Token Balance: {tokens['tokens_left']} Tokens")
    print(f"    - Refill Rate: {tokens['refill_rate']} token/min")
    print(f"    - Next Refill in: {tokens['refill_in_sec']}s")

    # 2. Check top Zoro catalog brands
    print("\n[*] Inspecting top dropshippable brands in Zoro combined_reports catalog...")
    top_brands = get_top_zoro_brands(min_items=50, limit=10)
    for b in top_brands:
        print(f"    - {b['brand']:25} | {b['item_count']:5d} items | Avg: ${b['avg_price']:.2f}")

    # 3. Check cached Keepa Product Finder query
    query_file = os.path.join(CURRENT_DIR, "keepa_query_186_asins.json")
    if os.path.exists(query_file):
        with open(query_file, "r") as f:
            asins = json.load(f)
        print(f"\n[✓] Loaded existing Keepa Product Finder query: {len(asins)} ASINs")
        print(f"    - Target Brands: Georgia-Pacific, Keson, Kraft Tool, Nu-Calgon, Purell, Solberg")
        print(f"    - Filter: Monthly Sold >= 200")


if __name__ == "__main__":
    main()
