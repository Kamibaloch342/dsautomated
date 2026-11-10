#!/usr/bin/env python3
"""
run_repricer_check.py — 12-Hour Background Repricer, S3 Catalog Sync & Listing Hijack Scanner.

Schedule / Cadence:
  Runs automatically every 12 hours (or on-demand with --once).

Pipeline Steps:
  1. Sync S3 Zoro TSV files (auto_download.py) and rebuild catalog DB (build_zoro_db.py).
  2. Download latest Amazon "All Listings Report" via Seller Central (download_all_listings_report.py).
  3. Parse active Private Label (PL) listings (Hathoro: 'hath', 'ht-*', HOANK: 'hoa', 'ho-*', etc.).
  4. Check live competitor offers for each PL listing to verify Buy Box ranking.
  5. Alert Condition: If our account is in 2nd (#2) or 3rd (#3) position, dispatch immediate WhatsApp alert.
"""

import os
import sys
import time
import json
import argparse
import functools
import subprocess
from typing import Dict, Any, List, Optional
import pandas as pd

# Force unbuffered output for logging and daemons
print = functools.partial(print, flush=True)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TSV_DIR = os.path.join(BASE_DIR, "TSV Files")
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
ALL_LISTINGS_TSV = os.path.join(REPORTS_DIR, "all_listings_report.tsv")
HIJACK_CACHE_FILE = os.path.join(DATA_DIR, "pl_hijack_cache.json")

# Ensure subdirectories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# Authorized store names that represent us (not competitors)
AUTHORIZED_PL_SELLERS = (
    "hathoro", "hoank", "wrenchly", "wrenchly solutions",
    "qurtdis", "hypersale", "abecomllc26", "abecomllc02", "inuhr"
)


def is_authorized_seller(seller_name: Optional[str]) -> bool:
    """Check if seller name matches one of our authorized store accounts."""
    if not seller_name:
        return False
    s = seller_name.strip().lower()
    return any(auth in s for auth in AUTHORIZED_PL_SELLERS)


def is_pl_sku(sku: Optional[str]) -> bool:
    """Detect if SKU is Private Label (Hathoro or HOANK)."""
    if not sku:
        return False
    s = str(sku).strip().lower()
    return (
        s.startswith("hath")
        or s.startswith("ht-")
        or s.startswith("ht_")
        or s.startswith("ht ")
        or s.startswith("hoa")
        or s.startswith("ho-")
        or s.startswith("ho_")
        or s.startswith("ho ")
        or s.startswith("hon")
        or "hoank" in s
    )


def load_hijack_cache() -> Dict[str, Any]:
    """Load previously notified hijacks to avoid spamming the same alert within 24h."""
    if os.path.exists(HIJACK_CACHE_FILE):
        try:
            with open(HIJACK_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_hijack_cache(cache: Dict[str, Any]) -> None:
    """Save hijack alert history."""
    try:
        with open(HIJACK_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"  ⚠️ Could not save hijack cache: {e}")


def step_sync_s3_and_catalog() -> bool:
    """Step 1: Download latest S3 TSVs and rebuild combined_reports.db."""
    print("\n" + "=" * 80)
    print("🚀 [Step 1/4] Syncing S3 TSV Feed & Rebuilding Catalog Database...")
    print("=" * 80)

    # 1. Run auto_download.py
    auto_download_py = os.path.join(TSV_DIR, "auto_download.py")
    if os.path.exists(auto_download_py):
        print(f"[*] Running: python3 '{auto_download_py}'")
        try:
            res = subprocess.run(
                [sys.executable, auto_download_py],
                cwd=TSV_DIR,
                capture_output=True,
                text=True,
                timeout=300
            )
            print(f"  auto_download stdout:\n{res.stdout[-300:] if res.stdout else 'None'}")
            if res.returncode not in (0, 2):
                print(f"  ⚠️ auto_download exited with code {res.returncode}: {res.stderr}")
        except Exception as e:
            print(f"  ⚠️ auto_download failed: {e}")

    # 2. Run build_zoro_db.py
    build_db_py = os.path.join(TSV_DIR, "build_zoro_db.py")
    if os.path.exists(build_db_py):
        print(f"[*] Running: python3 '{build_db_py}'")
        try:
            res = subprocess.run(
                [sys.executable, build_db_py],
                cwd=TSV_DIR,
                capture_output=True,
                text=True,
                timeout=600
            )
            print(f"  build_zoro_db stdout:\n{res.stdout[-300:] if res.stdout else 'None'}")
            if res.returncode == 0:
                print("  ✅ Catalog SQLite DB (combined_reports.db) is up to date.")
                return True
            else:
                print(f"  ⚠️ build_zoro_db exited with code {res.returncode}")
        except Exception as e:
            print(f"  ⚠️ build_zoro_db failed: {e}")
    return False


def step_download_all_listings_report() -> Optional[str]:
    """Step 2: Automate download of All Listings Report from Seller Central."""
    print("\n" + "=" * 80)
    print("📥 [Step 2/4] Downloading Amazon 'All Listings Report' from Seller Central...")
    print("=" * 80)

    try:
        from platforms.download_all_listings_report import download_all_listings_report
        tsv_path = download_all_listings_report(output_tsv=ALL_LISTINGS_TSV)
        if tsv_path and os.path.exists(tsv_path):
            print(f"  ✅ All Listings Report downloaded successfully: {tsv_path}")
            return tsv_path
    except Exception as e:
        print(f"  ❌ Failed to download report via helper: {e}")

    # Check if a recent report already exists as fallback
    if os.path.exists(ALL_LISTINGS_TSV):
        age_hours = (time.time() - os.path.getmtime(ALL_LISTINGS_TSV)) / 3600.0
        if age_hours < 24:
            print(f"  ℹ️ Using existing All Listings Report ({age_hours:.1f}h old): {ALL_LISTINGS_TSV}")
            return ALL_LISTINGS_TSV

    return None


def extract_pl_listings(tsv_path: str) -> List[Dict[str, Any]]:
    """Parse All Listings TSV and extract active Private Label listings."""
    if not os.path.exists(tsv_path):
        return []

    try:
        df = pd.read_csv(tsv_path, sep="\t", on_bad_lines="skip", low_memory=False)
        # Filter for active listings
        if "status" in df.columns:
            active_df = df[df["status"].astype(str).str.strip().str.lower() == "active"]
        else:
            active_df = df

        pl_items = []
        for _, row in active_df.iterrows():
            sku = str(row.get("seller-sku") or "").strip()
            asin = str(row.get("asin1") or row.get("product-id") or "").strip()
            price = str(row.get("price") or "").strip()
            title = str(row.get("item-name") or "").strip()
            qty = str(row.get("quantity") or "0").strip()

            if is_pl_sku(sku) and asin and asin != "nan":
                pl_items.append({
                    "sku": sku,
                    "asin": asin,
                    "price": price,
                    "title": title,
                    "quantity": qty,
                })

        print(f"  📊 Found {len(pl_items)} active Private Label listings in All Listings Report.")
        return pl_items
    except Exception as e:
        print(f"  ❌ Error reading TSV {tsv_path}: {e}")
        return []


def parse_price(val: Any) -> Optional[float]:
    """Parse numeric float from price text."""
    if not val:
        return None
    import re
    s = str(val).replace("$", "").replace(",", "").strip()
    m = re.search(r"(\d+\.?\d*)", s)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def check_offers_for_asin(driver, asin: str) -> List[Dict[str, Any]]:
    """Scrape live Amazon competitor offers for an ASIN using persistent browser driver."""
    from bs4 import BeautifulSoup
    from selenium.webdriver.common.by import By

    url = f"https://www.amazon.com/dp/{asin}"
    driver.get(url)
    time.sleep(2.0)

    # Quick CAPTCHA check
    if "validateCaptcha" in driver.page_source:
        time.sleep(4)
        driver.get(url)
        time.sleep(2)

    # Click "New (X) from" or "See All Buying Options" if present
    try:
        aod_btn = driver.find_elements(
            By.CSS_SELECTOR,
            "#olpLinkWidget_feature_div a, #buybox-see-all-buying-choices a, #aod-pinned-offer-show-more-link"
        )
        if aod_btn:
            aod_btn[0].click()
            time.sleep(2.0)
    except Exception:
        pass

    soup = BeautifulSoup(driver.page_source, "html.parser")
    offers = []

    # 1. Main Buy Box offer
    bb_seller = soup.find(id="sellerProfileTriggerId") or soup.find(id="merchant-info")
    bb_price = soup.find("span", class_="a-price") or soup.find("span", id="price_inside_buybox")
    if bb_seller and bb_price:
        s_name = bb_seller.get_text(strip=True)
        p_val = parse_price(bb_price.get_text(strip=True))
        if s_name and p_val:
            offers.append({
                "seller_name": s_name,
                "price": p_val,
                "is_buybox": True,
            })

    # 2. Offers list in AOD panel
    offer_divs = soup.select("#aod-offer, div[data-action='aod-offer']")
    for div in offer_divs:
        s_elem = div.select_one("#aod-offer-soldBy a, .aod-seller-name")
        p_elem = div.select_one(".a-price .a-offscreen, .a-price")
        if s_elem and p_elem:
            s_name = s_elem.get_text(strip=True)
            p_val = parse_price(p_elem.get_text(strip=True))
            if s_name and p_val:
                offers.append({
                    "seller_name": s_name,
                    "price": p_val,
                    "is_buybox": False,
                })

    # De-duplicate by seller name + price, sort ascending by price
    seen = set()
    unique_offers = []
    for o in offers:
        key = (o["seller_name"].lower(), o["price"])
        if key not in seen:
            unique_offers.append(o)
            seen.add(key)

    unique_offers.sort(key=lambda x: x["price"])
    return unique_offers


def step_check_pl_hijacks(pl_items: List[Dict[str, Any]]) -> int:
    """
    Step 3: Check listing offers for each PL listing.
    Alert via WhatsApp ONLY IF our store is pushed to 2nd (#2) or 3rd (#3) position.
    """
    print("\n" + "=" * 80)
    print("🔍 [Step 3/4] Checking Private Label Listings for Hijacking / Position Drops...")
    print("=" * 80)

    if not pl_items:
        print("  ℹ️ No active PL items to check.")
        return 0

    try:
        from scripts.amazon_listing_offers import get_driver, set_amazon_zipcode
        from utils.whatsapp import notify_hijack
    except ImportError:
        sys.path.insert(0, BASE_DIR)
        from scripts.amazon_listing_offers import get_driver, set_amazon_zipcode
        from utils.whatsapp import notify_hijack

    cache = load_hijack_cache()
    alerts_triggered = 0

    # Start lightweight headless browser
    print("[*] Starting headless Chrome for competitor ranking scan...")
    driver = get_driver(headless=True)
    zip_set = False

    try:
        for idx, item in enumerate(pl_items, 1):
            asin = item["asin"]
            sku = item["sku"]
            title = item.get("title", asin)
            my_price = parse_price(item.get("price"))

            print(f"\n[{idx}/{len(pl_items)}] Checking PL ASIN: {asin} ({sku})...")

            try:
                # Set zip on first run
                if not zip_set:
                    driver.get(f"https://www.amazon.com/dp/{asin}")
                    time.sleep(2)
                    set_amazon_zipcode(driver, "10001")
                    zip_set = True

                offers = check_offers_for_asin(driver, asin)
                if not offers:
                    print(f"  ✓ Only 1 seller or Buy Box sole owner.")
                    continue

                # Determine our rank in the offers list
                our_rank = None
                winner_offer = offers[0]

                for rank_idx, off in enumerate(offers, 1):
                    if is_authorized_seller(off["seller_name"]):
                        our_rank = rank_idx
                        break

                # The user's explicit rule:
                # "and only those listing that if we are on 2nd number for the listing or 3rd number"
                if our_rank in (2, 3):
                    winner_name = winner_offer.get("seller_name", "Unknown Competitor")
                    winner_price = f"${winner_offer.get('price'):.2f}"
                    our_price_str = f"${my_price:.2f}" if my_price else "N/A"

                    # Check 24-hour rate limit in cache
                    now_ts = time.time()
                    last_alerted = cache.get(asin, {}).get("alerted_at", 0)
                    if (now_ts - last_alerted) < (24 * 3600):
                        print(f"  ⏭️ Already alerted for {asin} position #{our_rank} within last 24h — skipping chat alert.")
                        continue

                    print(f"  🚨 HIJACK / BUY BOX LOST! Our store is #{our_rank}! Winner: {winner_name} ({winner_price})")

                    # Capture screenshot of listing
                    screenshot_bytes = None
                    try:
                        screenshot_bytes = driver.get_screenshot_as_png()
                    except Exception:
                        pass

                    details = {
                        "store": "Hathoro" if "hath" in sku.lower() or "ht" in sku.lower() else "HOANK",
                        "asin": asin,
                        "sku": sku,
                        "title": title,
                        "our_rank": str(our_rank),
                        "winner_name": winner_name,
                        "winner_price": winner_price,
                        "our_price": our_price_str,
                    }

                    # Dispatch WhatsApp Alert
                    sent = notify_hijack(details, screenshot_bytes=screenshot_bytes)
                    if sent:
                        print(f"  📲 WhatsApp Hijack Alert sent successfully for {asin} (Rank #{our_rank})!")
                        cache[asin] = {
                            "alerted_at": now_ts,
                            "our_rank": our_rank,
                            "winner": winner_name,
                            "winner_price": winner_price,
                        }
                        save_hijack_cache(cache)
                        alerts_triggered += 1

                elif our_rank == 1:
                    print(f"  🟢 We are #1 (Winning Buy Box at ${winner_offer.get('price', 0):.2f}) — OK.")
                else:
                    print(f"  ℹ️ Position check: Our rank is {our_rank or 'Not in Top'} (Total offers: {len(offers)}).")

            except Exception as e:
                print(f"  ⚠️ Error checking ASIN {asin}: {e}")

    finally:
        driver.quit()

    print(f"\n[Step 3/4 Complete] Scanned {len(pl_items)} PL listings. Hijack alerts triggered: {alerts_triggered}.")
    return alerts_triggered


def run_full_repricer_cycle() -> None:
    """Execute one complete 12-hour cycle."""
    cycle_start = time.time()
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║  12-HOUR BACKGROUND REPRICER & HIJACK SCANNER LAUNCHING                     ║")
    print(f"║  Start Time: {time.strftime('%Y-%m-%d %H:%M:%S')}                                        ║")
    print("╚" + "═" * 78 + "╝")

    # Step 1: S3 Sync & Catalog DB
    step_sync_s3_and_catalog()

    # Step 2: All Listings Report
    tsv_path = step_download_all_listings_report()

    # Step 3: Parse PL Listings and Check Hijacks
    if tsv_path and os.path.exists(tsv_path):
        pl_items = extract_pl_listings(tsv_path)
        alerts_sent = step_check_pl_hijacks(pl_items)
    else:
        print("❌ Could not obtain All Listings Report TSV. Hijack check skipped.")
        alerts_sent = 0

    elapsed_min = (time.time() - cycle_start) / 60.0
    print("\n" + "=" * 80)
    print(f"✅ Repricer & Hijack Check Complete in {elapsed_min:.1f} minutes. Alerts dispatched: {alerts_sent}")
    print("=" * 80 + "\n")


def daemon_loop(interval_hours: float = 12.0) -> None:
    """Run continuously on a 12-hour interval."""
    interval_seconds = int(interval_hours * 3600)
    print(f"🚀 [Daemon Mode] Running every {interval_hours:.1f} hours ({interval_seconds}s)...")

    while True:
        try:
            run_full_repricer_cycle()
        except Exception as e:
            print(f"❌ [Daemon Error] Unhandled exception in repricer cycle: {e}")

        print(f"💤 Sleeping for {interval_hours} hours until next check (Next run at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() + interval_seconds))})...")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="12-Hour Repricer, S3 Sync & Listing Hijack Scanner")
    parser.add_argument("--once", action="store_true", help="Run once and exit immediately")
    parser.add_argument("--daemon", action="store_true", help="Run continuously every 12 hours")
    parser.add_argument("--interval", type=float, default=12.0, help="Interval in hours (default: 12.0)")
    args = parser.parse_args()

    if args.daemon:
        daemon_loop(interval_hours=args.interval)
    else:
        # Default to running one complete pass
        run_full_repricer_cycle()
