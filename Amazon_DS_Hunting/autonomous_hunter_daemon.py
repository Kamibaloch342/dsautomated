#!/usr/bin/env python3
"""
autonomous_hunter_daemon.py — Autonomous 24/7 Keepa & Zoro Dropshipping Hunting Daemon

Continuous 3-Stage Pipeline:
1. Token-Governed Keepa Scout: Automatically extracts top dropshippable brands from Zoro,
   monitors Keepa API token budget (/token?key=...), and queries Product Finder for high-demand listings.
2. Fast-Match & 1:1 Pack Validator: Matches discovered ASINs against Zoro wholesale catalog
   (combined_reports.db) in < 1ms, validating exact 1:1 unit pack counts.
3. 0-Token Stealth Offer Scraper: Scrapes live Amazon competing offers via persistent Chrome (NYC ZIP 10001),
   computes 3 dropshipping profit models, and populates `Keepa_Zoro_Profitable_Listings.xlsx`
   with a dedicated Tab 1: ONLY PROFITABLE LISTINGS.
"""

from __future__ import annotations

import os
import sys
import json
import time
import sqlite3
import re
import urllib.request
import urllib.parse
import ssl
import gzip
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
sys.path.insert(0, CURRENT_DIR)

DB_PATH = os.path.join(PROJECT_ROOT, "Automations", "TSV Files", "combined_reports.db")
if not os.path.exists(DB_PATH):
    alt_db = os.path.join(CURRENT_DIR, "combined_reports.db")
    if os.path.exists(alt_db):
        DB_PATH = alt_db

HUNTER_DB = os.path.join(CURRENT_DIR, "hunter_queue.db")
CACHE_DIR = os.path.join(CURRENT_DIR, ".cache")
KEEPA_CACHE_DIR = os.path.join(CURRENT_DIR, ".keepa_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(KEEPA_CACHE_DIR, exist_ok=True)

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
# 1. DATABASE QUEUE & STATE MANAGEMENT
# ----------------------------------------------------------------------
def init_hunter_db(db_path: str = HUNTER_DB):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Brands Queue
    cur.execute("""
        CREATE TABLE IF NOT EXISTS brands_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_name TEXT UNIQUE,
            priority INTEGER DEFAULT 10,
            status TEXT DEFAULT 'PENDING',
            items_in_zoro INTEGER DEFAULT 0,
            asins_found INTEGER DEFAULT 0,
            asins_matched INTEGER DEFAULT 0,
            asins_profitable INTEGER DEFAULT 0,
            last_scanned_at TEXT,
            error_message TEXT
        )
    """)

    # 2. ASINs Discovered
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hunting_asins (
            asin TEXT PRIMARY KEY,
            brand TEXT,
            monthly_sold INTEGER DEFAULT 0,
            bsr INTEGER DEFAULT 0,
            title TEXT,
            mpn TEXT,
            upc TEXT,
            status TEXT DEFAULT 'DISCOVERED',
            discovered_at TEXT,
            last_updated_at TEXT
        )
    """)

    # 3. Matched Zoro Listings
    cur.execute("""
        CREATE TABLE IF NOT EXISTS matched_listings (
            asin TEXT PRIMARY KEY,
            zoro_no TEXT,
            zoro_title TEXT,
            zoro_price REAL,
            zoro_pack INTEGER,
            item_pack INTEGER,
            is_pack_match BOOLEAN,
            match_method TEXT,
            matched_at TEXT
        )
    """)

    # 4. Profitable Listings (Tab 1 Master Store)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS profitable_listings (
            asin TEXT PRIMARY KEY,
            brand TEXT,
            title TEXT,
            mpn TEXT,
            zoro_no TEXT,
            zoro_price REAL,
            lowest_mfn_price REAL,
            lowest_mfn_seller TEXT,
            buybox_price REAL,
            monthly_sales INTEGER,
            std_net_profit REAL,
            std_net_margin REAL,
            cart_net_profit REAL,
            cart_net_margin REAL,
            coupon_net_profit REAL,
            coupon_net_margin REAL,
            alert_badge TEXT,
            playbook TEXT,
            top_competitors TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def seed_top_zoro_brands(limit: int = 60, min_items: int = 50):
    """Auto-seeds high-potential dropshipping brands from combined_reports.db."""
    if not os.path.exists(DB_PATH):
        return

    conn_zoro = sqlite3.connect(DB_PATH)
    cur_z = conn_zoro.cursor()
    cur_z.execute("""
        SELECT zoro_brand_name, COUNT(*) as cnt
        FROM zoro_data
        WHERE sales_price BETWEEN 18.0 AND 250.0
          AND zoro_brand_name IS NOT NULL
          AND LENGTH(zoro_brand_name) > 1
        GROUP BY zoro_brand_name
        HAVING cnt >= ?
        ORDER BY cnt DESC
        LIMIT ?
    """, (min_items, limit))
    rows = cur_z.fetchall()
    conn_zoro.close()

    conn_h = sqlite3.connect(HUNTER_DB)
    cur_h = conn_h.cursor()

    # Explicit high-priority brands to ensure front of queue
    priority_front = [
        "BRADY", "NU-CALGON", "GEORGIA-PACIFIC", "PURELL", "GOJO",
        "KRAFT TOOL", "KESON", "DAYTON", "LENOX", "3M",
        "MILWAUKEE TOOL", "WESTWARD", "RUST-OLEUM", "RUBBERMAID",
    ]

    for brand in priority_front:
        cur_z = sqlite3.connect(DB_PATH).cursor()
        cur_z.execute("SELECT COUNT(*) FROM zoro_data WHERE UPPER(zoro_brand_name) = ?", (brand,))
        b_cnt = cur_z.fetchone()[0]
        cur_h.execute("""
            INSERT INTO brands_queue (brand_name, priority, status, items_in_zoro)
            VALUES (?, 1, 'PENDING', ?)
            ON CONFLICT(brand_name) DO UPDATE SET priority = 1, items_in_zoro = ?
        """, (brand, b_cnt, b_cnt))

    for b, cnt in rows:
        b_clean = b.strip().upper()
        if b_clean not in priority_front:
            cur_h.execute("""
                INSERT OR IGNORE INTO brands_queue (brand_name, priority, items_in_zoro)
                VALUES (?, 10, ?)
            """, (b_clean, cnt))

    conn_h.commit()
    conn_h.close()


# ----------------------------------------------------------------------
# 2. TOKEN GOVERNOR & KEEPA API INTERFACE
# ----------------------------------------------------------------------
def check_keepa_tokens(api_key: str = DEFAULT_KEEPA_KEY) -> Dict[str, Any]:
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


def run_keepa_query_for_brand(
    brand: str,
    monthly_sold_gte: int = 100,
    api_key: str = DEFAULT_KEEPA_KEY,
    min_reserve: int = 15,
) -> Tuple[bool, List[str], str]:
    token_status = check_keepa_tokens(api_key)
    tokens_left = token_status["tokens_left"]

    if tokens_left < min_reserve:
        wait_sec = int(token_status["refill_in_sec"]) + ((min_reserve - tokens_left) * 60)
        return False, [], f"Token reserve low ({tokens_left} left). Wait {wait_sec}s for refill."

    clean_b = brand.lower().strip()
    selection = {
        "productType": ["0"],
        "monthlySold_gte": monthly_sold_gte,
        "brand": [clean_b],
        "sort": [["current_SALES", "asc"], ["monthlySold", "desc"]],
        "perPage": 500,
        "page": 0,
    }

    url = f"https://api.keepa.com/query?key={api_key}&domain=1&selection={urllib.parse.quote(json.dumps(selection))}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})

    try:
        with urllib.request.urlopen(req, context=get_ssl_context(), timeout=35) as resp:
            raw = gzip.decompress(resp.read())
            data = json.loads(raw.decode("utf-8"))
            asin_list = data.get("asinList", [])
            cache_file = os.path.join(KEEPA_CACHE_DIR, f"{clean_b}_{monthly_sold_gte}.json")
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"brand": brand, "asins": asin_list, "total": len(asin_list)}, f, indent=2)
            return True, asin_list, f"Found {len(asin_list)} ASINs (Tokens left: {data.get('tokensLeft')})"
    except Exception as e:
        return False, [], str(e)


# ----------------------------------------------------------------------
# 3. ZORO MATCHING & EXACT 1:1 PACK VALIDATION
# ----------------------------------------------------------------------
def parse_pack(desc: str, uom: str = "EA") -> int:
    s = str(desc)
    m = re.search(r"PK\s*(\d+)", s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m2 = re.search(r"(\d+)\s*(?:/|-)?\s*PK\b", s, re.IGNORECASE)
    if m2:
        return int(m2.group(1))
    m3 = re.search(r"(\d+)\s*per\s*(?:box|case|carton)", s, re.IGNORECASE)
    if m3:
        return int(m3.group(1))
    m4 = re.search(r"Pack of (\d+)", s, re.IGNORECASE)
    if m4:
        return int(m4.group(1))
    m5 = re.search(r"Case of (\d+)", s, re.IGNORECASE)
    if m5:
        return int(m5.group(1))
    m6 = re.search(r"(\d+)\s*rolls?\b", s, re.IGNORECASE)
    if m6:
        return int(m6.group(1))
    return 1


def match_brand_asins_with_zoro(brand: str) -> int:
    """Matches discovered ASINs for a brand against Zoro's combined_reports.db."""
    if not os.path.exists(DB_PATH):
        return 0

    conn_h = sqlite3.connect(HUNTER_DB)
    cur_h = conn_h.cursor()
    cur_h.execute("""
        SELECT asin, brand, mpn, upc, title, monthly_sold
        FROM hunting_asins
        WHERE UPPER(brand) = UPPER(?) AND status = 'DISCOVERED'
    """, (brand,))
    pending_asins = cur_h.fetchall()

    if not pending_asins:
        conn_h.close()
        return 0

    conn_z = sqlite3.connect(DB_PATH)
    cur_z = conn_z.cursor()

    cur_z.execute("""
        SELECT zoro_no, manufacturer_part_number, short_description, sales_price, 
               availabilityStatus, uom, upc_code
        FROM zoro_data
        WHERE UPPER(zoro_brand_name) = UPPER(?)
    """, (brand,))
    zoro_items = cur_z.fetchall()
    conn_z.close()

    # Index Zoro items by clean MPN and clean UPC
    mpn_map = {}
    upc_map = {}
    for r in zoro_items:
        z_no, z_mpn, z_title, z_price, z_stat, z_uom, z_upc = r
        item = {
            "zoro_no": str(z_no),
            "mpn": str(z_mpn) if z_mpn else "",
            "title": str(z_title) if z_title else "",
            "price": float(z_price) if z_price else 0.0,
            "status": str(z_stat) if z_stat else "In Stock",
            "uom": str(z_uom) if z_uom else "EA",
            "pack": parse_pack(z_title, z_uom),
        }
        if z_mpn:
            clean_m = re.sub(r"[^A-Za-z0-9]", "", str(z_mpn)).upper()
            mpn_map[clean_m] = item
        if z_upc:
            clean_u = re.sub(r"[^0-9]", "", str(z_upc))
            upc_map[clean_u] = item

    matched_count = 0
    now_str = datetime.now().isoformat()

    for asin, b, mpn, upc, title, sales in pending_asins:
        matched_z = None
        match_method = "NONE"

        # 1. Match by MPN
        if mpn:
            clean_amz_mpn = re.sub(r"[^A-Za-z0-9]", "", str(mpn)).upper()
            if clean_amz_mpn in mpn_map:
                matched_z = mpn_map[clean_amz_mpn]
                match_method = "MPN_EXACT"

        # 2. Match by UPC
        if not matched_z and upc:
            clean_amz_upc = re.sub(r"[^0-9]", "", str(upc))
            if clean_amz_upc in upc_map:
                matched_z = upc_map[clean_amz_upc]
                match_method = "UPC_EXACT"

        if matched_z:
            amz_pack = parse_pack(title, "EA")
            z_pack = matched_z["pack"]
            is_pm = (amz_pack == z_pack)

            cur_h.execute("""
                INSERT OR REPLACE INTO matched_listings 
                (asin, zoro_no, zoro_title, zoro_price, zoro_pack, item_pack, is_pack_match, match_method, matched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (asin, matched_z["zoro_no"], matched_z["title"], matched_z["price"], z_pack, amz_pack, is_pm, match_method, now_str))

            new_status = "MATCHED_EXACT" if is_pm else "PACK_MISMATCH"
            cur_h.execute("UPDATE hunting_asins SET status = ? WHERE asin = ?", (new_status, asin))
            matched_count += 1
        else:
            cur_h.execute("UPDATE hunting_asins SET status = 'UNMATCHED' WHERE asin = ?", (asin,))

    conn_h.commit()
    conn_h.close()
    return matched_count


# ----------------------------------------------------------------------
# 4. PROFIT CALCULATION & REPORT PUBLISHING
# ----------------------------------------------------------------------
def compute_profit(zoro_price: float, selling_price: Optional[float], free_shipping: bool = False, apply_coupon: bool = False) -> Dict[str, Any]:
    """Strict standard profit: no coupons, shipping 0 if >= 50 else 5.99."""
    if selling_price is None or selling_price <= 0:
        return {"net_profit": 0.0, "net_margin": None}
    ref_fee = round(selling_price * 0.15, 2)
    shipping = 0.0 if zoro_price >= 50.0 else 5.99
    total_cost = round(zoro_price + ref_fee + shipping, 2)
    net_profit = round(selling_price - total_cost, 2)
    net_margin = round((net_profit / selling_price) * 100.0, 2)
    return {"net_profit": net_profit, "net_margin": net_margin}


def compute_std_profit(zoro_price: float, selling_price: Optional[float]) -> Dict[str, Any]:
    """
    Standard dropshipping profit calculation (Strict User Rule):
    - No coupons (100% full wholesale cost)
    - Zoro shipping: $0.00 if zoro_price >= 50.0 else $5.99
    - Amazon referral fee: 15% (selling_price * 0.15)
    - Total cost = zoro_price + shipping + referral_fee
    - Net profit = selling_price - total_cost
    - Net margin = (net_profit / selling_price) * 100.0
    - Profit threshold: net_margin >= 1.0% at lowest price
    """
    if selling_price is None or selling_price <= 0:
        return {
            "selling_price": 0.0,
            "zoro_price": zoro_price,
            "zoro_shipping": 0.0,
            "referral_fee": 0.0,
            "total_cost": 0.0,
            "net_profit": 0.0,
            "net_margin": None,
            "is_profitable": False,
        }
    shipping = 0.0 if zoro_price >= 50.0 else 5.99
    ref_fee = round(selling_price * 0.15, 2)
    total_cost = round(zoro_price + shipping + ref_fee, 2)
    net_profit = round(selling_price - total_cost, 2)
    net_margin = round((net_profit / selling_price) * 100.0, 2)
    is_profitable = (net_margin >= 1.0)
    return {
        "selling_price": selling_price,
        "zoro_price": zoro_price,
        "zoro_shipping": shipping,
        "referral_fee": ref_fee,
        "total_cost": total_cost,
        "net_profit": net_profit,
        "net_margin": net_margin,
        "is_profitable": is_profitable,
    }


def evaluate_and_publish_profitable_listings():
    """Evaluates all scraped matched listings and publishes master Excel report with strict standard profit."""
    conn_h = sqlite3.connect(HUNTER_DB)
    cur_h = conn_h.cursor()

    cur_h.execute("""
        SELECT m.asin, a.brand, a.title, a.mpn, m.zoro_no, m.zoro_price, a.monthly_sold
        FROM matched_listings m
        JOIN hunting_asins a ON m.asin = a.asin
        WHERE m.is_pack_match = 1
    """)
    rows = cur_h.fetchall()

    from generate_b2b_report import analyze_scraped_offers, load_cached_offers

    now_str = datetime.now().isoformat()
    profitable_count = 0

    for asin, brand, title, mpn, z_no, z_price, monthly_sales in rows:
        cached = load_cached_offers(asin)
        if not cached:
            continue

        analysis = analyze_scraped_offers(cached)
        low_all_p = analysis.get("lowest_landed_price")
        low_all_s = analysis.get("lowest_landed_seller", "Unknown")
        bb_p = analysis.get("buybox_price")
        is_amz = analysis.get("is_amazon_buybox", False) or "amazon" in str(low_all_s).lower()

        # Rule 1: We MUST be profitable at the ABSOLUTE LOWEST price on the listing
        selling_price = low_all_p
        if not selling_price or selling_price <= 0:
            continue

        # Rule 2: If Amazon itself is the lowest seller / on listing, discard (no buy box / impossible to compete)
        if is_amz:
            continue

        prof = compute_std_profit(z_price, selling_price)
        if not prof["is_profitable"]:
            continue

        badge = "HIGH PROFIT (>5%)" if prof["net_margin"] >= 5.0 else "PROFITABLE (1-5%)"
        playbook = f"Absolute Lowest: +${prof['net_profit']:.2f} (+{prof['net_margin']:.1f}% margin at lowest competitor price)"

        cur_h.execute("""
            INSERT OR REPLACE INTO profitable_listings
            (asin, brand, title, mpn, zoro_no, zoro_price, lowest_mfn_price, lowest_mfn_seller,
             buybox_price, monthly_sales, std_net_profit, std_net_margin, cart_net_profit,
             cart_net_margin, coupon_net_profit, coupon_net_margin, alert_badge, playbook,
             top_competitors, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            asin, brand, title, mpn, z_no, z_price, selling_price, low_all_s,
            bb_p, monthly_sales or 100, prof["net_profit"], prof["net_margin"],
            prof["net_profit"], prof["net_margin"],
            prof["net_profit"], prof["net_margin"],
            badge, playbook, analysis.get("top_competitors_summary", "None"), now_str
        ))
        profitable_count += 1

    conn_h.commit()

    # Generate master Excel
    export_master_excel(conn_h)
    conn_h.close()
    return profitable_count


def export_master_excel(conn_h: sqlite3.Connection):
    """Builds Keepa_Zoro_Profitable_Listings.xlsx with Tab 1: Only Profitable Listings."""
    wb = openpyxl.Workbook()
    NAVY = "1E293B"
    WHITE = "FFFFFF"
    BORDER_GRAY = "CBD5E1"
    thin_border = Border(
        left=Side(style="thin", color=BORDER_GRAY), right=Side(style="thin", color=BORDER_GRAY),
        top=Side(style="thin", color=BORDER_GRAY), bottom=Side(style="thin", color=BORDER_GRAY)
    )

    # ----------------------------------------------------
    # TAB 1: ONLY PROFITABLE LISTINGS (USER DIRECTIVE)
    # ----------------------------------------------------
    ws1 = wb.active
    ws1.title = "Only Profitable Listings"
    ws1.views.sheetView[0].showGridLines = True

    ws1.merge_cells("B2:Z2")
    t1 = ws1["B2"]
    t1.value = "⭐ 24/7 AUTONOMOUS HUNTING — ONLY PROFITABLE DROPSHIPPING LISTINGS"
    t1.font = Font(name="Segoe UI", size=16, bold=True, color=WHITE)
    t1.fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    t1.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[2].height = 40

    ws1.merge_cells("B3:Q3")
    sub1 = ws1["B3"]
    sub1.value = "Standard Profit Only (No Coupons) | Free Shipping if Zoro Wholesale ≥ $50.00 else $5.99 | Evaluated at Lowest Competitor Price (Margin ≥ 1.0%)"
    sub1.font = Font(name="Segoe UI", size=10, italic=True, color="64748B")
    sub1.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[3].height = 20

    cur = conn_h.cursor()
    cur.execute("SELECT COUNT(*) FROM profitable_listings WHERE std_net_margin >= 1.0")
    prof_cnt = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM hunting_asins")
    total_asins = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM brands_queue WHERE status = 'COMPLETED'")
    brands_done = cur.fetchone()[0]

    kpis = [
        ("PROFITABLE LISTINGS", f"{prof_cnt} Listings", "DCFCE7", "15803D"),
        ("TOTAL ASINS HUNTED", f"{total_asins} ASINs", "E0F2FE", "0369A1"),
        ("ZORO BRANDS SCANNED", f"{brands_done} Brands", "FEF3C7", "B45309"),
        ("MINIMUM MARGIN", "≥ 1.0% Std Margin", "F3E8FF", "7E22CE"),
        ("SHIPPING RULE", "Zoro ≥ $50 Free / $5.99", "E0E7FF", "3730A3"),
    ]

    card_starts = ["B", "E", "H", "K", "N"]
    card_ends = ["D", "G", "J", "M", "Q"]
    ws1.row_dimensions[5].height = 18
    ws1.row_dimensions[6].height = 26

    for (label, val, bg_c, txt_c), sc, ec in zip(kpis, card_starts, card_ends):
        ws1.merge_cells(f"{sc}5:{ec}5")
        ws1.merge_cells(f"{sc}6:{ec}6")
        l = ws1[f"{sc}5"]
        l.value = label
        l.font = Font(name="Segoe UI", size=8, bold=True, color="64748B")
        l.fill = PatternFill(start_color=bg_c, end_color=bg_c, fill_type="solid")
        l.alignment = Alignment(horizontal="center", vertical="center")
        v = ws1[f"{sc}6"]
        v.value = val
        v.font = Font(name="Segoe UI", size=11, bold=True, color=txt_c)
        v.fill = PatternFill(start_color=bg_c, end_color=bg_c, fill_type="solid")
        v.alignment = Alignment(horizontal="center", vertical="center")
        for r in range(5, 7):
            for c in range(openpyxl.utils.column_index_from_string(sc), openpyxl.utils.column_index_from_string(ec) + 1):
                ws1.cell(row=r, column=c).border = thin_border

    headers = [
        ("Badge / Status", 22), ("ASIN", 14), ("Brand", 16), ("Product Title", 40),
        ("Monthly Sales", 14), ("Zoro Part #", 14), ("Zoro Wholesale ($)", 18),
        ("Zoro Shipping ($)", 16), ("Amazon Lowest Price ($)", 22), ("Amazon Referral Fee ($)", 22),
        ("Total Sourcing Cost ($)", 22), ("Std Net Profit ($)", 18), ("Std Net Margin %", 16),
        ("Lowest Competitor / Seller", 24), ("Amazon Link", 15), ("Zoro Search Link", 15),
    ]

    ws1.row_dimensions[8].height = 30
    h_font = Font(name="Segoe UI", size=9, bold=True, color=WHITE)
    h_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")

    for col_idx, (col_name, col_width) in enumerate(headers, 2):
        cell = ws1.cell(row=8, column=col_idx, value=col_name)
        cell.font = h_font
        cell.fill = h_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
        col_letter = get_column_letter(col_idx)
        ws1.column_dimensions[col_letter].width = col_width

    cur.execute("""
        SELECT alert_badge, asin, brand, title, monthly_sales, zoro_no, zoro_price,
               lowest_mfn_price, lowest_mfn_seller, buybox_price, std_net_profit,
               std_net_margin
        FROM profitable_listings
        WHERE std_net_margin >= 1.0
        ORDER BY std_net_margin DESC, monthly_sales DESC
    """)
    prof_rows = cur.fetchall()

    badge_colors = {
        "HIGH PROFIT (>5%)": ("DCFCE7", "15803D"),
        "PROFITABLE (1-5%)": ("DCFCE7", "166534"),
    }

    current_row = 9
    for r in prof_rows:
        ws1.row_dimensions[current_row].height = 22
        badge = r[0] if "HIGH" in str(r[0]) else "PROFITABLE (1-5%)"
        bg_c, txt_c = badge_colors.get(badge, ("DCFCE7", "166534"))

        asin = r[1]
        brand = r[2]
        title = r[3]
        sales = r[4] or 100
        z_no = r[5]
        z_price = r[6]
        low_price = r[7]
        low_seller = r[8] or "Competitor MFN"

        z_ship = 0.0 if z_price >= 50.0 else 5.99
        ref_fee = round(low_price * 0.15, 2)
        tot_cost = round(z_price + z_ship + ref_fee, 2)
        net_prof = round(low_price - tot_cost, 2)
        net_mrg = round((net_prof / low_price) * 100.0, 2)

        row_data = [
            (badge, "center", Font(name="Segoe UI", size=9, bold=True, color=txt_c), bg_c),
            (asin, "center", Font(name="Segoe UI", size=9, bold=True), None),
            (brand, "center", Font(name="Segoe UI", size=9), None),
            (title, "left", Font(name="Segoe UI", size=9), None),
            (sales, "center", Font(name="Segoe UI", size=9, bold=True), None),
            (z_no, "center", Font(name="Segoe UI", size=9), None),
            (z_price, "right", Font(name="Segoe UI", size=9, bold=True), None),
            (z_ship, "right", Font(name="Segoe UI", size=9), "F0FDF4" if z_ship == 0.0 else None),
            (low_price, "right", Font(name="Segoe UI", size=9, bold=True), None),
            (ref_fee, "right", Font(name="Segoe UI", size=9), None),
            (tot_cost, "right", Font(name="Segoe UI", size=9), None),
            (net_prof, "right", Font(name="Segoe UI", size=9, bold=True, color="15803D"), "DCFCE7"),
            (f"{net_mrg:.1f}%", "center", Font(name="Segoe UI", size=9, bold=True, color="15803D"), "DCFCE7"),
            (low_seller, "left", Font(name="Segoe UI", size=9), None),
            (f"https://www.amazon.com/dp/{asin}", "center", Font(name="Segoe UI", size=9, underline="single", color="2563EB"), None),
            (f"https://www.zoro.com/search?q={z_no}", "center", Font(name="Segoe UI", size=9, underline="single", color="2563EB"), None),
        ]

        for col_idx, (val, align, font_obj, cell_bg) in enumerate(row_data, 2):
            cell = ws1.cell(row=current_row, column=col_idx, value=val)
            cell.alignment = Alignment(horizontal=align, vertical="center")
            cell.font = font_obj
            cell.border = thin_border
            if cell_bg:
                cell.fill = PatternFill(start_color=cell_bg, end_color=cell_bg, fill_type="solid")
            if col_idx in [8, 9, 10, 11, 12, 13] and isinstance(val, (int, float)):
                cell.number_format = "$#,##0.00"
            elif col_idx == 6 and isinstance(val, (int, float)):
                cell.number_format = "#,##0"
            if col_idx == 16 and val:
                cell.hyperlink = str(val)
                cell.value = "View on Amazon"
            elif col_idx == 17 and val:
                cell.hyperlink = str(val)
                cell.value = "View on Zoro"

        current_row += 1

    ws1.freeze_panes = "D9"

    # ----------------------------------------------------
    # TAB 2: HUNTER DASHBOARD & BRAND QUEUE
    # ----------------------------------------------------
    ws2 = wb.create_sheet(title="Hunter Dashboard & Queue")
    ws2.views.sheetView[0].showGridLines = True

    ws2.merge_cells("B2:I2")
    t2 = ws2["B2"]
    t2.value = "📊 AUTONOMOUS HUNTING QUEUE & LIVE PROGRESS"
    t2.font = Font(name="Segoe UI", size=14, bold=True, color=WHITE)
    t2.fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    t2.alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[2].height = 36

    queue_headers = [("Priority", 10), ("Brand Name", 24), ("Status", 16), ("Items in Zoro", 14),
                     ("ASINs Found", 14), ("Matched", 14), ("Profitable", 14), ("Last Scanned", 22)]
    ws2.row_dimensions[4].height = 26
    for col_idx, (col_name, col_width) in enumerate(queue_headers, 2):
        cell = ws2.cell(row=4, column=col_idx, value=col_name)
        cell.font = h_font
        cell.fill = h_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        col_letter = get_column_letter(col_idx)
        ws2.column_dimensions[col_letter].width = col_width

    cur.execute("""
        SELECT priority, brand_name, status, items_in_zoro, asins_found, asins_matched, asins_profitable, last_scanned_at
        FROM brands_queue
        ORDER BY priority ASC, items_in_zoro DESC
    """)
    q_rows = cur.fetchall()

    current_row = 5
    for r in q_rows:
        ws2.row_dimensions[current_row].height = 18
        st = r[2]
        st_bg = "DCFCE7" if st == "COMPLETED" else ("FEF3C7" if st == "IN_PROGRESS" else "F1F5F9")
        st_txt = "15803D" if st == "COMPLETED" else ("B45309" if st == "IN_PROGRESS" else "64748B")

        q_data = [
            (r[0], "center", Font(name="Segoe UI", size=9, bold=True)),
            (r[1], "left", Font(name="Segoe UI", size=9, bold=True)),
            (r[2], "center", Font(name="Segoe UI", size=9, bold=True, color=st_txt)),
            (r[3], "center", Font(name="Segoe UI", size=9)),
            (r[4], "center", Font(name="Segoe UI", size=9)),
            (r[5], "center", Font(name="Segoe UI", size=9)),
            (r[6], "center", Font(name="Segoe UI", size=9, bold=True, color="15803D" if (r[6] or 0) > 0 else "64748B")),
            (str(r[7])[:19] if r[7] else "-", "center", Font(name="Segoe UI", size=8)),
        ]

        for col_idx, (val, align, font_obj) in enumerate(q_data, 2):
            cell = ws2.cell(row=current_row, column=col_idx, value=val)
            cell.alignment = Alignment(horizontal=align, vertical="center")
            cell.font = font_obj
            cell.border = thin_border
            if col_idx == 4 and st != "PENDING":
                cell.fill = PatternFill(start_color=st_bg, end_color=st_bg, fill_type="solid")

        current_row += 1

    wb.save(OUTPUT_EXCEL)
    print(f"[✓] Master Excel Published: {OUTPUT_EXCEL}")
    print(f"    - Tab 1: Only Profitable Listings ({len(prof_rows)} rows)")
    print(f"    - Tab 2: Hunter Dashboard & Queue ({len(q_rows)} brands queued)")


# ----------------------------------------------------------------------
# 5. MASTER DAEMON LOOP (24/7 EXECUTION)
# ----------------------------------------------------------------------
def run_daemon_cycle(api_key: str = DEFAULT_KEEPA_KEY, min_reserve: int = 15) -> bool:
    """Executes a single end-to-end hunting cycle across the next priority brand."""
    init_hunter_db()
    seed_top_zoro_brands()

    conn_h = sqlite3.connect(HUNTER_DB)
    cur_h = conn_h.cursor()

    # Get next brand in queue
    cur_h.execute("""
        SELECT id, brand_name, priority
        FROM brands_queue
        WHERE status = 'PENDING'
        ORDER BY priority ASC, items_in_zoro DESC
        LIMIT 1
    """)
    row = cur_h.fetchone()

    if not row:
        print("[*] All brands completed or queue empty. Resetting priority queue...")
        cur_h.execute("UPDATE brands_queue SET status = 'PENDING'")
        conn_h.commit()
        conn_h.close()
        return False

    brand_id, brand_name, priority = row
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] >>> Starting Hunter Cycle for Brand: {brand_name} (Priority {priority})")

    # 1. Check token governor
    tokens = check_keepa_tokens(api_key)
    print(f"[*] Live Token Status: {tokens['tokens_left']} Tokens available (Refill in {tokens['refill_in_sec']}s)")

    if tokens["tokens_left"] < min_reserve:
        wait_time = int(tokens["refill_in_sec"]) + ((min_reserve - tokens["tokens_left"]) * 60)
        print(f"[!] Token balance below safety reserve ({tokens['tokens_left']} < {min_reserve}).")
        print(f"    Sleeping {wait_time}s to allow token replenishment...")
        conn_h.close()
        time.sleep(min(wait_time, 120))  # sleep in safe chunks
        return True

    # Mark brand in progress
    cur_h.execute("UPDATE brands_queue SET status = 'IN_PROGRESS' WHERE id = ?", (brand_id,))
    conn_h.commit()

    # 2. Query Keepa Product Finder
    success, asins, msg = run_keepa_query_for_brand(brand_name, monthly_sold_gte=100, api_key=api_key, min_reserve=min_reserve)
    print(f"[*] Keepa Product Finder: {msg}")

    if not success:
        cur_h.execute("UPDATE brands_queue SET status = 'PENDING', error_message = ? WHERE id = ?", (msg, brand_id))
        conn_h.commit()
        conn_h.close()
        return True

    now_str = datetime.now().isoformat()
    for a in asins:
        cur_h.execute("""
            INSERT OR IGNORE INTO hunting_asins (asin, brand, status, discovered_at)
            VALUES (?, ?, 'DISCOVERED', ?)
        """, (a, brand_name, now_str))

    cur_h.execute("UPDATE brands_queue SET asins_found = ? WHERE id = ?", (len(asins), brand_id))
    conn_h.commit()
    conn_h.close()

    # 3. Zoro Matching & 1:1 Pack Validation
    matched_cnt = match_brand_asins_with_zoro(brand_name)
    print(f"[*] Zoro Fast-Match & Pack Validation: {matched_cnt} products matched exactly to Zoro inventory.")

    conn_h = sqlite3.connect(HUNTER_DB)
    cur_h = conn_h.cursor()
    cur_h.execute("UPDATE brands_queue SET asins_matched = ? WHERE id = ?", (matched_cnt, brand_id))
    conn_h.commit()

    # 4. Scrape Live Amazon Offers (0-Token Browser Scraper)
    cur_h.execute("""
        SELECT asin FROM hunting_asins 
        WHERE UPPER(brand) = UPPER(?) AND status = 'MATCHED_EXACT'
    """, (brand_name,))
    to_scrape = [r[0] for r in cur_h.fetchall()]
    conn_h.close()

    if to_scrape:
        print(f"[*] Launching 0-Token Persistent Chrome to scrape {len(to_scrape)} matched ASINs...")
        try:
            from batch_scrape_offers import scrape_asins
            scraped = scrape_asins(to_scrape[:15], zipcode="10001", force=False)
            print(f"[✓] Scraped {len(scraped)} live offer sets.")
        except Exception as e:
            print(f"[!] Warning during offer scraping: {e}")

    # 5. Evaluate and Publish to Master Excel
    prof_cnt = evaluate_and_publish_profitable_listings()
    print(f"[✓] Master Excel Updated: {prof_cnt} total profitable listings currently live in Tab 1.")

    conn_h = sqlite3.connect(HUNTER_DB)
    cur_h = conn_h.cursor()
    cur_h.execute("""
        UPDATE brands_queue 
        SET status = 'COMPLETED', asins_profitable = ?, last_scanned_at = ? 
        WHERE id = ?
    """, (prof_cnt, datetime.now().isoformat(), brand_id))
    conn_h.commit()
    conn_h.close()

    return True


def main():
    print("================================================================================")
    print("  AUTONOMOUS 24/7 KEEPA & ZORO DROPSHIPPING HUNTING DAEMON")
    print("================================================================================")

    init_hunter_db()
    seed_top_zoro_brands()

    tokens = check_keepa_tokens()
    print(f"[*] Initial Token Status: {tokens['tokens_left']} Tokens available (Refill rate: {tokens['refill_rate']} tok/min)")

    # If CLI flag `--once` is passed, run single cycle and exit
    if "--once" in sys.argv:
        print("[*] Running single cycle (--once mode)...")
        run_daemon_cycle()
        return

    # Continuous 24/7 daemon loop
    print("[*] Starting continuous 24/7 daemon loop. Press Ctrl+C to gracefully stop.")
    cycle_num = 1
    while True:
        try:
            print(f"\n--- Starting Cycle #{cycle_num} ---")
            run_daemon_cycle()
            cycle_num += 1
            print("[*] Cycle complete. Resting 15s before next brand evaluation...")
            time.sleep(15)
        except KeyboardInterrupt:
            print("\n[!] Daemon paused by user.")
            break
        except Exception as e:
            print(f"[!] Error in daemon loop: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
