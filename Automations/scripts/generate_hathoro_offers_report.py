#!/usr/bin/env python3
"""
generate_hathoro_offers_report.py — Hathoro August Orders & Amazon Competitor Analysis Report

Extracts all ASINs ordered under the 'Hathoro' account from `data/automations_unified.db`,
pulls order volume in August, latest sale price, latest Zoro cost without tax,
scrapes current Amazon live offers (all sellers, lowest 1, lowest 2, lowest 3 prices & sellers),
and generates a formatted Excel workbook (Font: Montserrat, No Gridlines, Custom Highlights).

Usage:
    python scripts/generate_hathoro_offers_report.py --limit 10   # test run on top 10 ASINs
    python scripts/generate_hathoro_offers_report.py              # full run on all August ASINs
    python scripts/generate_hathoro_offers_report.py --report-only # re-generate Excel from cache without re-scraping
"""

from __future__ import annotations

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
for p in [_PROJECT_ROOT, _SCRIPT_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Auto re-exec in project .venv if run with system python
_VENV_PY = os.path.abspath(os.path.join(_PROJECT_ROOT, "..", ".venv", "bin", "python"))
if os.path.exists(_VENV_PY) and sys.executable != _VENV_PY:
    try:
        import openpyxl, bs4  # verify libraries in current env
    except ImportError:
        os.execv(_VENV_PY, [_VENV_PY] + sys.argv)

import argparse
import json
import re
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from amazon_listing_offers import (
    get_driver,
    set_amazon_zipcode,
    click_all_offers_button,
    parse_all_offers_from_page,
    parse_price_value,
    clean_text,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "automations_unified.db")
CACHE_FILE = os.path.join(BASE_DIR, "data", "hathoro_august_offers_cache.json")
DEFAULT_OUTPUT_EXCEL = os.path.join(BASE_DIR, "Hathoro_August_Amazon_Offers_Report.xlsx")


def load_cache() -> Dict[str, Any]:
    """Load cached scraping results if available."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache: Dict[str, Any]) -> None:
    """Save scraping results to cache."""
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def get_hathoro_august_asins(db_path: str = DB_PATH, limit: Optional[int] = None) -> pd.DataFrame:
    """Extract all ASINs ordered for Hathoro in August 2026 with Zoro cost and order volume."""
    conn = sqlite3.connect(db_path)
    query = """
    WITH august_orders AS (
        SELECT 
            asin,
            sku,
            title,
            quantity,
            item_price,
            wb_order_id,
            zoro_subtotal,
            zoro_shipping,
            zoro_tax,
            zoro_order_total,
            purchase_date
        FROM closing_orders
        WHERE account = 'Hathoro' 
          AND purchase_date LIKE '2026-08%'
    ),
    asin_metrics AS (
        SELECT 
            asin,
            COUNT(*) as total_august_orders,
            SUM(COALESCE(quantity, 1)) as total_august_units,
            MAX(purchase_date) as max_purchase_date
        FROM august_orders
        GROUP BY asin
    ),
    latest_orders AS (
        SELECT 
            ao.asin,
            ao.sku,
            ao.title,
            ao.item_price as latest_sale_price,
            ao.purchase_date,
            ROW_NUMBER() OVER (PARTITION BY ao.asin ORDER BY ao.purchase_date DESC) as rn
        FROM august_orders ao
    ),
    latest_placed_orders AS (
        SELECT 
            co.asin,
            co.wb_order_id,
            co.zoro_account_name,
            co.zoro_subtotal,
            co.zoro_shipping,
            co.zoro_tax,
            co.zoro_order_total,
            -- Latest order cost without tax:
            CASE 
                WHEN co.zoro_subtotal > 0 THEN ROUND(co.zoro_subtotal + COALESCE(co.zoro_shipping, 0.0), 2)
                WHEN co.zoro_order_total > 0 THEN ROUND(co.zoro_order_total - COALESCE(co.zoro_tax, 0.0), 2)
                ELSE NULL 
            END as latest_zoro_cost_notax,
            co.purchase_date,
            ROW_NUMBER() OVER (
                PARTITION BY co.asin 
                ORDER BY 
                    CASE WHEN (co.zoro_tax IS NULL OR co.zoro_tax = 0) THEN 1 ELSE 2 END,
                    co.purchase_date DESC
            ) as rn_placed
        FROM closing_orders co
        WHERE co.account = 'Hathoro'
          AND (co.wb_order_id LIKE 'WB%' OR co.zoro_order_total > 0)
    )
    SELECT 
        m.asin,
        lo.sku,
        lo.title,
        m.total_august_orders,
        m.total_august_units,
        lo.latest_sale_price,
        lpo.latest_zoro_cost_notax,
        lpo.wb_order_id,
        lpo.zoro_account_name
    FROM asin_metrics m
    JOIN latest_orders lo ON m.asin = lo.asin AND lo.rn = 1
    LEFT JOIN latest_placed_orders lpo ON m.asin = lpo.asin AND lpo.rn_placed = 1
    ORDER BY m.total_august_orders DESC;
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    if limit:
        df = df.head(limit)
    return df


# Authorized sister/partner accounts allowed on our Private Label listings
AUTHORIZED_PL_SELLERS = ("hathoro", "hoank", "wrenchly", "qurtdis", "hypersale", "abecomllc26")


def is_authorized_pl_seller(seller_name: Optional[str]) -> bool:
    """Check if seller is authorized (e.g. Hathoro, HOANK, or Wrenchly LLC)."""
    if not seller_name:
        return False
    s = seller_name.strip().lower()
    return any(auth in s for auth in AUTHORIZED_PL_SELLERS)


def get_unauthorized_sellers(all_offers: List[Dict[str, Any]]) -> List[str]:
    """Find any unauthorized sellers sitting on a Private Label listing."""
    unauth = []
    seen = set()
    for off in all_offers:
        s_name = (off.get("seller_name") or "").strip()
        if s_name and not is_authorized_pl_seller(s_name):
            if s_name not in seen:
                unauth.append(s_name)
                seen.add(s_name)
    return unauth


def get_top_3_lowest_offers(offers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:

    """Sort offers by total price ascending and return top 3 unique offers."""
    valid_offers = []
    seen_keys = set()

    for off in offers:
        p_val = parse_price_value(off.get("total_price")) or parse_price_value(off.get("item_price"))
        if p_val and p_val > 0:
            seller_name = off.get("seller_name", "").strip()
            key = (seller_name, p_val)
            if key not in seen_keys:
                valid_offers.append((p_val, off))
                seen_keys.add(key)

    valid_offers.sort(key=lambda x: x[0])
    return [item[1] for item in valid_offers[:3]]


PROGRESS_FILE = os.path.join(BASE_DIR, "data", "repricer_progress.json")
LIVE_LOG_FILE = os.path.join(BASE_DIR, "data", "repricer_live.log")


def emit_log(msg: str) -> None:
    """Print to console and append to repricer_live.log with timestamp."""
    print(msg, flush=True)
    try:
        os.makedirs(os.path.dirname(LIVE_LOG_FILE), exist_ok=True)
        with open(LIVE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def emit_progress(
    account: str,
    current: int,
    total: int,
    asin: str,
    status: str,
    success_count: int,
    fail_count: int,
    start_time: float,
) -> None:
    """Persist real-time progress state for the live dashboard."""
    try:
        pct = round((current / total * 100), 1) if total > 0 else 0.0
        elapsed = round(time.time() - start_time, 1)
        data = {
            "account": account,
            "is_scraping": current < total,
            "current": current,
            "total": total,
            "percent": pct,
            "current_asin": asin,
            "status": status,
            "successful_count": success_count,
            "failed_count": fail_count,
            "elapsed_seconds": elapsed,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
        tmp = PROGRESS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, PROGRESS_FILE)
    except Exception:
        pass


def is_driver_alive(driver: Optional[webdriver.Chrome]) -> bool:
    """Check if the Selenium WebDriver instance is still alive and responsive."""
    if driver is None:
        return False
    try:
        _ = driver.current_url
        return True
    except Exception:
        return False


def respawn_driver(
    old_driver: Optional[webdriver.Chrome],
    zipcode: str = "10001",
    headless: bool = True,
) -> webdriver.Chrome:
    """Safely terminate crashed driver and spawn a fresh stealth Chrome session."""
    emit_log("    ⚠️ Browser disconnected or crashed. Respawning fresh Chrome session...")
    if old_driver:
        try:
            old_driver.quit()
        except Exception:
            pass
    time.sleep(2.5)
    new_driver = get_driver(headless=headless)
    try:
        set_amazon_zipcode(new_driver, zipcode)
        time.sleep(2)
    except Exception as e:
        emit_log(f"    ⚠️ Warning setting zipcode on respawned driver: {e}")
    emit_log("    ✓ Fresh Chrome session active and ready.")
    return new_driver


def scrape_all_asins_offers(
    asins_df: pd.DataFrame,
    zipcode: str = "10001",
    headless: bool = True,
    force_rescrape: bool = False,
) -> Dict[str, Any]:
    """Scrape live Amazon offers for all ASINs in dataframe using persistent browser session with auto-recovery."""
    cache = load_cache()
    to_scrape = []

    for _, row in asins_df.iterrows():
        asin = row["asin"]
        if not force_rescrape and asin in cache and cache[asin].get("scraped_successfully"):
            continue
        to_scrape.append(asin)

    total_asins = len(asins_df)
    already_cached = total_asins - len(to_scrape)
    emit_log(f"[*] Total ASINs in dataset: {total_asins}")
    emit_log(f"[*] Already cached: {already_cached}")
    emit_log(f"[*] Needing live scraping: {len(to_scrape)}")

    start_time = time.time()
    success_count = sum(1 for v in cache.values() if v.get("scraped_successfully"))
    fail_count = sum(1 for v in cache.values() if not v.get("scraped_successfully"))

    emit_progress("Hathoro", already_cached, total_asins,
                  to_scrape[0] if to_scrape else "", "Initializing live scrape...",
                  success_count, fail_count, start_time)

    if not to_scrape:
        emit_log("    ✓ All ASINs already present in cache.")
        emit_progress("Hathoro", total_asins, total_asins, "", "All ASINs cached",
                      success_count, fail_count, start_time)
        return cache

    driver = get_driver(headless=headless)
    zip_set = False

    try:
        for idx, asin in enumerate(to_scrape, 1):
            curr_progress_idx = already_cached + idx
            url = f"https://www.amazon.com/dp/{asin}"
            emit_log(f"\n[{curr_progress_idx}/{total_asins}] Scraping ASIN {asin} ({url})...")
            emit_progress("Hathoro", curr_progress_idx, total_asins, asin,
                          f"Scraping ASIN {asin}...", success_count, fail_count, start_time)

            max_retries = 2
            for attempt in range(1, max_retries + 1):
                try:
                    if not is_driver_alive(driver):
                        driver = respawn_driver(driver, zipcode=zipcode, headless=headless)
                        zip_set = True

                    driver.get(url)
                    time.sleep(2.5)

                    if not zip_set:
                        set_amazon_zipcode(driver, zipcode)
                        time.sleep(2)
                        zip_set = True

                    # Click offer link if available
                    click_all_offers_button(driver)
                    time.sleep(3)

                    # Parse offers from page
                    html = driver.page_source
                    offers = parse_all_offers_from_page(html)

                    total_sellers = len(offers)
                    buy_box_offer = next((o for o in offers if o.get("is_pinned")), offers[0] if offers else None)

                    top_3 = get_top_3_lowest_offers(offers)
                    lowest_1 = top_3[0] if len(top_3) > 0 else (buy_box_offer or {})
                    lowest_2 = top_3[1] if len(top_3) > 1 else None
                    lowest_3 = top_3[2] if len(top_3) > 2 else None

                    is_hathoro_in_bb = False
                    if buy_box_offer and "hathoro" in buy_box_offer.get("seller_name", "").lower():
                        is_hathoro_in_bb = True

                    cache[asin] = {
                        "asin": asin,
                        "scraped_at": datetime.now().isoformat(),
                        "scraped_successfully": True,
                        "total_sellers_count": total_sellers,
                        "buy_box_seller": buy_box_offer.get("seller_name") if buy_box_offer else "",
                        "buy_box_price": buy_box_offer.get("item_price") if buy_box_offer else "",
                        "buy_box_shipping": buy_box_offer.get("shipping_cost") if buy_box_offer else "",
                        "buy_box_delivery": buy_box_offer.get("shipping_time") if buy_box_offer else "",
                        "is_hathoro_in_buy_box": is_hathoro_in_bb,
                        # Top 3 lowest offers
                        "lowest_1_seller": lowest_1.get("seller_name", ""),
                        "lowest_1_price": lowest_1.get("total_price", lowest_1.get("item_price", "")),
                        "lowest_1_delivery": lowest_1.get("shipping_time", ""),
                        "lowest_2_seller": lowest_2.get("seller_name", "") if lowest_2 else "",
                        "lowest_2_price": lowest_2.get("total_price", lowest_2.get("item_price", "")) if lowest_2 else "",
                        "lowest_2_delivery": lowest_2.get("shipping_time", "") if lowest_2 else "",
                        "lowest_3_seller": lowest_3.get("seller_name", "") if lowest_3 else "",
                        "lowest_3_price": lowest_3.get("total_price", lowest_3.get("item_price", "")) if lowest_3 else "",
                        "lowest_3_delivery": lowest_3.get("shipping_time", "") if lowest_3 else "",
                        "all_offers": offers,
                    }

                    success_count += 1
                    emit_log(
                        f"    ✓ Sellers: {total_sellers} | "
                        f"Lowest 1: {cache[asin]['lowest_1_price']} ({cache[asin]['lowest_1_seller']}) | "
                        f"Lowest 2: {cache[asin]['lowest_2_price']} ({cache[asin]['lowest_2_seller']}) | "
                        f"BB: {cache[asin]['buy_box_seller']} | Hathoro in BB: {'YES' if is_hathoro_in_bb else 'NO'}"
                    )
                    break

                except Exception as e:
                    err_str = str(e)
                    is_fatal_session = any(
                        k in err_str.lower()
                        for k in ["invalid session id", "disconnected", "renderer", "not reachable", "session deleted"]
                    )
                    if is_fatal_session and attempt < max_retries:
                        first_line = err_str.splitlines()[0] if err_str else "Connection lost"
                        emit_log(f"    ⚠️ Session crash on attempt {attempt}: {first_line}. Respawning Chrome...")
                        driver = respawn_driver(driver, zipcode=zipcode, headless=headless)
                        zip_set = True
                        continue
                    else:
                        first_line = err_str.splitlines()[0] if err_str else str(e)
                        emit_log(f"    ⚠️ Error scraping ASIN {asin}: {first_line}")
                        cache[asin] = {
                            "asin": asin,
                            "scraped_at": datetime.now().isoformat(),
                            "scraped_successfully": False,
                            "error": str(e),
                            "total_sellers_count": 0,
                            "all_offers": [],
                        }
                        fail_count += 1
                        break

            # Save cache immediately so dashboard displays live real-time progress
            save_cache(cache)
            emit_progress("Hathoro", curr_progress_idx, total_asins, asin,
                          f"Processed {asin}", success_count, fail_count, start_time)

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        save_cache(cache)
        emit_progress("Hathoro", total_asins, total_asins, "", "Scraping completed",
                      success_count, fail_count, start_time)

    return cache

    return cache


def create_excel_report(
    asins_df: pd.DataFrame,
    offers_cache: Dict[str, Any],
    output_file: str = DEFAULT_OUTPUT_EXCEL,
) -> str:
    """Build a formatted, styled Excel report using openpyxl with Montserrat font and no gridlines."""
    print(f"\n[*] Generating formatted Excel report: {output_file}...")

    wb = openpyxl.Workbook()
    FONT_FAMILY = "Montserrat"

    # -------------------------------------------------------------
    # STYLES & COLOR PALETTE (Montserrat Font)
    # -------------------------------------------------------------
    font_title = Font(name=FONT_FAMILY, size=16, bold=True, color="1B365D")
    font_subtitle = Font(name=FONT_FAMILY, size=10, italic=True, color="5C6F84")
    font_kpi_num = Font(name=FONT_FAMILY, size=15, bold=True, color="1B365D")
    font_kpi_label = Font(name=FONT_FAMILY, size=9, bold=True, color="5C6F84")
    font_header = Font(name=FONT_FAMILY, size=10, bold=True, color="FFFFFF")
    font_data = Font(name=FONT_FAMILY, size=10, color="1C2733")
    font_data_bold = Font(name=FONT_FAMILY, size=10, bold=True, color="1C2733")
    font_link = Font(name=FONT_FAMILY, size=10, color="0066CC", underline="single")

    # Header Color Fills
    fill_header_main = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid")     # Deep Navy
    fill_header_accent = PatternFill(start_color="244B7A", end_color="244B7A", fill_type="solid")   # Medium Navy
    fill_header_sellers = PatternFill(start_color="1F5E6B", end_color="1F5E6B", fill_type="solid")  # Slate Teal
    fill_zebra = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")           # Soft Slate Tint
    fill_kpi_card = PatternFill(start_color="EDF2F7", end_color="EDF2F7", fill_type="solid")

    # Highlighting Fills
    fill_lowest_1 = PatternFill(start_color="D1E7DD", end_color="D1E7DD", fill_type="solid")        # Soft Mint Green
    font_lowest_1 = Font(name=FONT_FAMILY, size=10, bold=True, color="0F5132")

    fill_bb_yes = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")          # Soft Green
    font_bb_yes = Font(name=FONT_FAMILY, size=10, bold=True, color="006100")

    fill_bb_no = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")           # Soft Amber
    font_bb_no = Font(name=FONT_FAMILY, size=10, bold=True, color="856404")

    # Dark Alert Fills for PL Hijacked Listings
    fill_pl_hijacked = PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid")     # Crimson Alert Fill
    font_pl_hijacked = Font(name=FONT_FAMILY, size=10, bold=True, color="842029")                    # Dark Red Bold

    fill_pl_clean = PatternFill(start_color="E8F4FD", end_color="E8F4FD", fill_type="solid")        # Soft Blue Protection Fill
    font_pl_clean = Font(name=FONT_FAMILY, size=10, bold=True, color="0C5460")

    # Clean borders
    thin_border_side = Side(style="thin", color="E2E8F0")
    border_cell = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    border_kpi = Border(
        left=Side(style="medium", color="1B365D"),
        right=thin_border_side,
        top=thin_border_side,
        bottom=thin_border_side,
    )

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    # =============================================================
    # SHEET 1: Main Summary & Comprehensive Analysis Table
    # =============================================================
    ws_main = wb.active
    ws_main.title = "Hathoro Listing Offers"

    # Turn OFF gridlines as requested!
    ws_main.views.sheetView[0].showGridLines = False

    # Title Block
    ws_main["A1"] = "HATHORO AUGUST ORDERS & LIVE AMAZON COMPETITOR OFFERS"
    ws_main["A1"].font = font_title
    ws_main["A2"] = f"Account: Hathoro  |  ZIP: 10001 (New York)  |  Font: Montserrat  |  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ws_main["A2"].font = font_subtitle

    # Compute KPIs
    total_asins = len(asins_df)
    total_august_orders = int(asins_df["total_august_orders"].sum())
    total_august_units = int(asins_df["total_august_units"].sum())

    bb_wins = 0
    total_scraped = 0
    total_sellers_sum = 0
    pl_count = 0
    pl_hijacked_count = 0

    for _, r in asins_df.iterrows():
        sku_s = (r["sku"] or "").strip().lower()
        is_pl = "hath" in sku_s or sku_s.startswith("ht_") or sku_s.startswith("ht-") or sku_s.startswith("ht ") or "ht_" in sku_s or "ht-" in sku_s

        if is_pl:
            pl_count += 1

        c_data = offers_cache.get(r["asin"], {})
        if c_data.get("scraped_successfully"):
            total_scraped += 1
            if c_data.get("is_hathoro_in_buy_box"):
                bb_wins += 1
            sellers_count = c_data.get("total_sellers_count", 0)
            total_sellers_sum += sellers_count
            all_offs = c_data.get("all_offers", [])
            unauth = get_unauthorized_sellers(all_offs) if all_offs else []
            if is_pl and len(unauth) > 0:
                pl_hijacked_count += 1

    bb_win_rate = (bb_wins / total_scraped * 100) if total_scraped > 0 else 0.0
    avg_sellers = (total_sellers_sum / total_scraped) if total_scraped > 0 else 0.0

    # KPI Cards Row (Row 4 & 5)
    kpis = [
        ("TOTAL ASINS", f"{total_asins:,}", "B4", "B5"),
        ("AUGUST ORDERS", f"{total_august_orders:,}", "D4", "D5"),
        ("PL LISTINGS (HATH)", f"{pl_count:,}", "F4", "F5"),
        ("🚨 PL HIJACKED", f"{pl_hijacked_count:,}", "H4", "H5"),
        ("BUY BOX WIN RATE", f"{bb_win_rate:.1f}%", "J4", "J5"),
    ]

    for label, val, c_val, c_lbl in kpis:
        ws_main[c_val] = val
        ws_main[c_val].font = font_kpi_num
        ws_main[c_val].alignment = align_center
        ws_main[c_val].fill = fill_kpi_card
        ws_main[c_val].border = border_kpi

        # Highlight Hijacked card if > 0
        if "HIJACKED" in label and pl_hijacked_count > 0:
            ws_main[c_val].font = Font(name=FONT_FAMILY, size=15, bold=True, color="842029")
            ws_main[c_val].fill = fill_pl_hijacked

        ws_main[c_lbl] = label
        ws_main[c_lbl].font = font_kpi_label
        ws_main[c_lbl].alignment = align_center
        ws_main[c_lbl].fill = fill_kpi_card
        ws_main[c_lbl].border = border_kpi

    # Table Headers with PL Status Column and 6 Lowest Seller Columns (Row 7)
    headers = [
        ("ASIN", 14, align_center, fill_header_main),
        ("SKU", 20, align_left, fill_header_main),
        ("PL / HIJACKER STATUS", 24, align_center, fill_header_main),
        ("PRODUCT TITLE", 38, align_left, fill_header_main),
        ("AUG ORDERS", 13, align_right, fill_header_accent),
        ("AUG UNITS", 12, align_right, fill_header_accent),
        ("HATHORO SALE PRICE", 18, align_right, fill_header_accent),
        ("ZORO COST (NO TAX)", 18, align_right, fill_header_accent),
        ("GROSS PROFIT $", 15, align_right, fill_header_accent),
        ("TOTAL SELLERS", 14, align_center, fill_header_sellers),
        ("BUY BOX WINNER", 22, align_left, fill_header_sellers),
        ("BUY BOX PRICE", 15, align_right, fill_header_sellers),
        ("HATHORO IN BUY BOX?", 20, align_center, fill_header_sellers),
        # --- 6 NEW COLUMNS (Top 3 Lowest Sellers & Prices) ---
        ("LOWEST 1 SELLER NAME", 22, align_left, fill_header_sellers),
        ("LOWEST 1 PRICE", 16, align_right, fill_header_sellers),
        ("LOWEST 2 SELLER NAME", 22, align_left, fill_header_sellers),
        ("LOWEST 2 PRICE", 16, align_right, fill_header_sellers),
        ("LOWEST 3 SELLER NAME", 22, align_left, fill_header_sellers),
        ("LOWEST 3 PRICE", 16, align_right, fill_header_sellers),
        # ----------------------------------------------------
        ("PRICE DIFF (HATHORO - LOWEST 1)", 28, align_right, fill_header_sellers),
        ("PRODUCT LINK", 14, align_center, fill_header_main),
    ]

    header_row = 7
    for col_idx, (h_title, col_width, h_align, h_fill) in enumerate(headers, 1):
        cell = ws_main.cell(row=header_row, column=col_idx)
        cell.value = h_title
        cell.font = font_header
        cell.fill = h_fill
        cell.alignment = h_align
        cell.border = border_cell
        col_letter = get_column_letter(col_idx)
        ws_main.column_dimensions[col_letter].width = col_width

    # Write Data Rows
    row_idx = header_row + 1
    for _, r in asins_df.iterrows():
        asin = r["asin"]
        sku = r["sku"] or ""
        title = r["title"] or ""
        sku_s = (sku or "").strip().lower()
        is_pl = "hath" in sku_s or sku_s.startswith("ht_") or sku_s.startswith("ht-") or sku_s.startswith("ht ") or "ht_" in sku_s or "ht-" in sku_s


        aug_orders = int(r["total_august_orders"]) if pd.notnull(r["total_august_orders"]) else 0
        aug_units = int(r["total_august_units"]) if pd.notnull(r["total_august_units"]) else 0
        sale_price = float(r["latest_sale_price"]) if pd.notnull(r["latest_sale_price"]) else None
        zoro_cost = float(r["latest_zoro_cost_notax"]) if pd.notnull(r["latest_zoro_cost_notax"]) else None

        gross_profit = None
        if sale_price and zoro_cost:
            gross_profit = round(sale_price - zoro_cost, 2)

        # Cache data
        c_data = offers_cache.get(asin, {})
        total_sellers = c_data.get("total_sellers_count", 0)
        bb_seller = c_data.get("buy_box_seller", "")
        bb_price_num = parse_price_value(c_data.get("buy_box_price", ""))
        is_bb = c_data.get("is_hathoro_in_buy_box")

        all_offers_list = c_data.get("all_offers", [])
        unauth_sellers = get_unauthorized_sellers(all_offers_list) if all_offers_list else []
        if not unauth_sellers and not all_offers_list and total_sellers > 1:
            if bb_seller and not is_authorized_pl_seller(bb_seller):
                unauth_sellers = [bb_seller]

        # Check PL Hijacked Condition (Wrenchly is allowed as an authorized sister seller!)
        is_pl_hijacked = is_pl and len(unauth_sellers) > 0

        # Extract Top 3 lowest sellers from offers list
        top_3 = get_top_3_lowest_offers(all_offers_list) if all_offers_list else []

        low1_seller = top_3[0].get("seller_name", "") if len(top_3) > 0 else c_data.get("lowest_1_seller", c_data.get("lowest_price_seller", ""))
        low1_price_num = parse_price_value(top_3[0].get("total_price", top_3[0].get("item_price", ""))) if len(top_3) > 0 else parse_price_value(c_data.get("lowest_1_price", c_data.get("lowest_price", "")))

        low2_seller = top_3[1].get("seller_name", "") if len(top_3) > 1 else c_data.get("lowest_2_seller", "")
        low2_price_num = parse_price_value(top_3[1].get("total_price", top_3[1].get("item_price", ""))) if len(top_3) > 1 else parse_price_value(c_data.get("lowest_2_price", ""))

        low3_seller = top_3[2].get("seller_name", "") if len(top_3) > 2 else c_data.get("lowest_3_seller", "")
        low3_price_num = parse_price_value(top_3[2].get("total_price", top_3[2].get("item_price", ""))) if len(top_3) > 2 else parse_price_value(c_data.get("lowest_3_price", ""))

        price_diff = None
        if sale_price and low1_price_num:
            price_diff = round(sale_price - low1_price_num, 2)

        # Base row fill
        row_fill = fill_zebra if row_idx % 2 == 0 else PatternFill(fill_type=None)

        # 1. ASIN
        c1 = ws_main.cell(row=row_idx, column=1, value=asin)
        c1.alignment = align_center

        # 2. SKU
        c2 = ws_main.cell(row=row_idx, column=2, value=sku)
        c2.alignment = align_left

        # 3. PL / HIJACKER STATUS
        if is_pl_hijacked:
            pl_status_text = f"🚨 HIJACKED ({len(unauth_sellers)} Unauth: {', '.join(unauth_sellers[:2])})"
        elif is_pl:
            has_wrenchly = any("wrenchly" in (o.get("seller_name") or "").lower() for o in all_offers_list)
            pl_status_text = "🛡️ PL Protected (Shared: Wrenchly)" if has_wrenchly else "🛡️ PL Protected"
        else:
            pl_status_text = "Standard Listing"

        c3 = ws_main.cell(row=row_idx, column=3, value=pl_status_text)
        c3.alignment = align_center

        if is_pl_hijacked:
            c3.fill = fill_pl_hijacked
            c3.font = font_pl_hijacked
        elif is_pl:
            c3.fill = fill_pl_clean
            c3.font = font_pl_clean

        # 4. Product Title
        c4 = ws_main.cell(row=row_idx, column=4, value=title)
        c4.alignment = align_left

        # 5. August Orders
        c5 = ws_main.cell(row=row_idx, column=5, value=aug_orders)
        c5.number_format = "#,##0"
        c5.alignment = align_right

        # 6. August Units
        c6 = ws_main.cell(row=row_idx, column=6, value=aug_units)
        c6.number_format = "#,##0"
        c6.alignment = align_right

        # 7. Hathoro Sale Price
        c7 = ws_main.cell(row=row_idx, column=7, value=sale_price)
        c7.number_format = "$#,##0.00"
        c7.alignment = align_right

        # 8. Zoro Cost (No Tax)
        c8 = ws_main.cell(row=row_idx, column=8, value=zoro_cost)
        c8.number_format = "$#,##0.00"
        c8.alignment = align_right

        # 9. Gross Profit $
        c9 = ws_main.cell(row=row_idx, column=9, value=gross_profit)
        c9.number_format = "$#,##0.00"
        c9.alignment = align_right

        # 10. Total Sellers Count
        c10 = ws_main.cell(row=row_idx, column=10, value=total_sellers)
        c10.number_format = "#,##0"
        c10.alignment = align_center
        if is_pl_hijacked:
            c10.fill = fill_pl_hijacked
            c10.font = font_pl_hijacked

        # 11. Buy Box Winner
        c11 = ws_main.cell(row=row_idx, column=11, value=bb_seller)
        c11.alignment = align_left

        # 12. Buy Box Price
        c12 = ws_main.cell(row=row_idx, column=12, value=bb_price_num)
        c12.number_format = "$#,##0.00"
        c12.alignment = align_right

        # 13. Hathoro in Buy Box?
        bb_status = "YES" if is_bb is True else ("NO" if is_bb is False else "N/A")
        c13 = ws_main.cell(row=row_idx, column=13, value=bb_status)
        c13.alignment = align_center
        if bb_status == "YES":
            c13.fill = fill_bb_yes
            c13.font = font_bb_yes
        elif bb_status == "NO":
            c13.fill = fill_bb_no
            c13.font = font_bb_no

        # 14. Lowest 1 Seller Name
        c14 = ws_main.cell(row=row_idx, column=14, value=low1_seller)
        c14.alignment = align_left

        # 15. Lowest 1 Price (HIGHLIGHTED)
        c15 = ws_main.cell(row=row_idx, column=15, value=low1_price_num)
        c15.number_format = "$#,##0.00"
        c15.alignment = align_right
        c15.fill = fill_lowest_1
        c15.font = font_lowest_1

        # 16. Lowest 2 Seller Name
        c16 = ws_main.cell(row=row_idx, column=16, value=low2_seller if low2_seller else "-")
        c16.alignment = align_left

        # 17. Lowest 2 Price
        c17 = ws_main.cell(row=row_idx, column=17, value=low2_price_num if low2_price_num else None)
        if low2_price_num:
            c17.number_format = "$#,##0.00"
        else:
            c17.value = "-"
        c17.alignment = align_right

        # 18. Lowest 3 Seller Name
        c18 = ws_main.cell(row=row_idx, column=18, value=low3_seller if low3_seller else "-")
        c18.alignment = align_left

        # 19. Lowest 3 Price
        c19 = ws_main.cell(row=row_idx, column=19, value=low3_price_num if low3_price_num else None)
        if low3_price_num:
            c19.number_format = "$#,##0.00"
        else:
            c19.value = "-"
        c19.alignment = align_right

        # 20. Price Difference (Hathoro - Lowest 1)
        c20 = ws_main.cell(row=row_idx, column=20, value=price_diff)
        c20.number_format = "$#,##0.00"
        c20.alignment = align_right

        # 21. Product Link
        link_url = f"https://www.amazon.com/dp/{asin}"
        c21 = ws_main.cell(row=row_idx, column=21, value="View Listing")
        c21.hyperlink = link_url
        c21.font = font_link
        c21.alignment = align_center

        # Apply base formatting & dark alert highlighting to hijacked listings
        standard_cells = [c1, c2, c4, c5, c6, c7, c8, c9, c11, c12, c14, c16, c17, c18, c19, c20]
        for c in standard_cells:
            if is_pl_hijacked:
                # Prominently highlight hijacked PL listing SKU & ASIN in dark alert style
                if c in (c1, c2):
                    c.fill = fill_pl_hijacked
                    c.font = font_pl_hijacked
                else:
                    c.font = font_data
                    if row_fill.fill_type:
                        c.fill = row_fill
            else:
                c.font = font_data
                if row_fill.fill_type:
                    c.fill = row_fill
            c.border = border_cell

        c3.border = border_cell
        c10.border = border_cell
        c13.border = border_cell
        c15.border = border_cell
        c21.border = border_cell

        row_idx += 1

    # Freeze Header & Enable Autofilter
    ws_main.freeze_panes = "A8"
    ws_main.auto_filter.ref = f"A7:U{row_idx - 1}"


    # =============================================================
    # SHEET 2: Detailed Seller Offers (Offer-by-Offer)
    # =============================================================
    ws_detail = wb.create_sheet(title="All Seller Offers Breakdown")
    ws_detail.views.sheetView[0].showGridLines = False

    ws_detail["A1"] = "ALL LIVE SELLER OFFERS BREAKDOWN (BY ASIN)"
    ws_detail["A1"].font = font_title
    ws_detail["A2"] = "Complete list of every individual seller sitting on each Hathoro listing."
    ws_detail["A2"].font = font_subtitle

    detail_headers = [
        ("ASIN", 14, align_center),
        ("SELLER NAME", 24, align_left),
        ("BUY BOX / PINNED?", 18, align_center),
        ("ITEM PRICE", 14, align_right),
        ("SHIPPING COST", 14, align_right),
        ("TOTAL PRICE", 14, align_right),
        ("SHIPPING / DELIVERY PROMISE", 32, align_left),
        ("SHIPS FROM", 22, align_left),
        ("CONDITION", 12, align_center),
        ("SELLER FEEDBACK & RATING", 45, align_left),
        ("SELLER STORE LINK", 18, align_center),
    ]

    d_header_row = 4
    for col_idx, (dh_title, dh_width, dh_align) in enumerate(detail_headers, 1):
        cell = ws_detail.cell(row=d_header_row, column=col_idx)
        cell.value = dh_title
        cell.font = font_header
        cell.fill = fill_header_main
        cell.alignment = dh_align
        cell.border = border_cell
        col_letter = get_column_letter(col_idx)
        ws_detail.column_dimensions[col_letter].width = dh_width

    d_row_idx = d_header_row + 1
    for _, r in asins_df.iterrows():
        asin = r["asin"]
        c_data = offers_cache.get(asin, {})
        offers_list = c_data.get("all_offers", [])

        for off in offers_list:
            d_fill = fill_zebra if d_row_idx % 2 == 0 else PatternFill(fill_type=None)
            item_p = parse_price_value(off.get("item_price"))
            ship_p = parse_price_value(off.get("shipping_cost")) or 0.0
            tot_p = parse_price_value(off.get("total_price")) or (item_p + ship_p if item_p else None)

            dc1 = ws_detail.cell(row=d_row_idx, column=1, value=asin)
            dc1.alignment = align_center

            dc2 = ws_detail.cell(row=d_row_idx, column=2, value=off.get("seller_name", ""))
            dc2.alignment = align_left

            dc3 = ws_detail.cell(row=d_row_idx, column=3, value="YES (Buy Box)" if off.get("is_pinned") else "No")
            dc3.alignment = align_center

            dc4 = ws_detail.cell(row=d_row_idx, column=4, value=item_p)
            dc4.number_format = "$#,##0.00"
            dc4.alignment = align_right

            dc5 = ws_detail.cell(row=d_row_idx, column=5, value=off.get("shipping_cost", "FREE"))
            dc5.alignment = align_right

            dc6 = ws_detail.cell(row=d_row_idx, column=6, value=tot_p)
            dc6.number_format = "$#,##0.00"
            dc6.alignment = align_right

            dc7 = ws_detail.cell(row=d_row_idx, column=7, value=off.get("shipping_time", ""))
            dc7.alignment = align_left

            dc8 = ws_detail.cell(row=d_row_idx, column=8, value=off.get("ships_from", ""))
            dc8.alignment = align_left

            dc9 = ws_detail.cell(row=d_row_idx, column=9, value=off.get("condition", "New"))
            dc9.alignment = align_center

            dc10 = ws_detail.cell(row=d_row_idx, column=10, value=off.get("rating_info", ""))
            dc10.alignment = align_left

            slink = off.get("seller_link", "")
            dc11 = ws_detail.cell(row=d_row_idx, column=11, value="Store Link" if slink else "")
            if slink:
                dc11.hyperlink = slink
                dc11.font = font_link
            dc11.alignment = align_center

            for dc in [dc1, dc2, dc3, dc4, dc5, dc6, dc7, dc8, dc9, dc10, dc11]:
                if dc != dc11:
                    dc.font = font_data
                if d_fill.fill_type:
                    dc.fill = d_fill
                dc.border = border_cell

            d_row_idx += 1

    ws_detail.freeze_panes = "A5"
    if d_row_idx > d_header_row + 1:
        ws_detail.auto_filter.ref = f"A4:K{d_row_idx - 1}"

    wb.save(output_file)
    print(f"    ✓ Excel report successfully generated: {output_file}")
    return output_file


def main():
    parser = argparse.ArgumentParser(
        description="Generate Hathoro August Orders & Amazon Competitor Offers Excel Report"
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of ASINs to process (e.g. for testing)")
    parser.add_argument("--zipcode", "-z", default="10001", help="ZIP code for delivery rates (default: 10001)")
    parser.add_argument("--visible", action="store_true", help="Run browser in visible mode")
    parser.add_argument("--force", action="store_true", help="Force re-scraping even if cached")
    parser.add_argument("--report-only", action="store_true", help="Generate Excel report using existing cache only")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT_EXCEL, help="Output Excel file path")

    args = parser.parse_args()

    print("=" * 80)
    print("  📊 HATHORO AUGUST ORDERS & AMAZON COMPETITOR SCRAPER")
    print("=" * 80)

    # 1. Fetch Hathoro ASINs from DB
    df_asins = get_hathoro_august_asins(limit=args.limit)
    print(f"[*] Retrieved {len(df_asins)} Hathoro ASINs from database.")

    # 2. Scrape live Amazon offers
    if args.report_only:
        print("[*] --report-only flag set: using cached scraping data.")
        offers_cache = load_cache()
    else:
        offers_cache = scrape_all_asins_offers(
            df_asins,
            zipcode=args.zipcode,
            headless=not args.visible,
            force_rescrape=args.force,
        )

    # 3. Create formatted Excel workbook
    report_path = create_excel_report(df_asins, offers_cache, output_file=args.output)
    print("\n" + "=" * 80)
    print(f"  🎉 REPORT COMPLETE: {report_path}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
