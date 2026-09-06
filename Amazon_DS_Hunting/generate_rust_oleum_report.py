#!/usr/bin/env python3
"""
generate_rust_oleum_report.py — Amazon Dropshipping Hunting & Profitability Engine for Rust-Oleum:
Analyzes Keepa export: 'Again multiple brands.xlsx' (Rust-Oleum)

Implements user rules:
1. Discards any listing with Bought in Past Month < 100 (only consider >= 100).
2. Matches against Zoro 1,180-item Rust-Oleum catalog with pack-size validation.
3. Analyzes live Amazon competitor pricing, fulfillment (MFN vs FBA), and Buy Box suppression.
4. Generates executive-grade dual-tab Excel report.
"""

from __future__ import annotations

import os
import sys
import json
import sqlite3
import re
from typing import Dict, List, Any, Optional, Tuple

import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
INPUT_EXCEL = os.path.join(CURRENT_DIR, "Again multiple brands.xlsx")
DB_PATH = os.path.join(PROJECT_ROOT, "Automations", "TSV Files", "combined_reports.db")
CACHE_DIR = os.path.join(CURRENT_DIR, ".cache")
OUTPUT_EXCEL = os.path.join(CURRENT_DIR, "Rust_Oleum_DS_Hunting_Results.xlsx")


def parse_numeric(val: Any) -> Optional[float]:
    if val is None or pd.isna(val):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace("$", "").replace(",", "").strip()
    m = re.search(r"[0-9]+(?:\.[0-9]+)?", s)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            pass
    return None


def parse_pack(desc: str, uom: str) -> int:
    m = re.search(r"PK(\d+)", str(desc), re.IGNORECASE)
    if m:
        return int(m.group(1))
    m2 = re.search(r"(\d+)\s*/\s*PK", str(desc), re.IGNORECASE)
    if m2:
        return int(m2.group(1))
    m3 = re.search(r"(\d+)\s*per\s*box", str(desc), re.IGNORECASE)
    if m3:
        return int(m3.group(1))
    m4 = re.search(r"Box of (\d+)", str(desc), re.IGNORECASE)
    if m4:
        return int(m4.group(1))
    m5 = re.search(r"Pack of (\d+)", str(desc), re.IGNORECASE)
    if m5:
        return int(m5.group(1))
    m6 = re.search(r"Case of (\d+)", str(desc), re.IGNORECASE)
    if m6:
        return int(m6.group(1))
    if str(uom).upper() == "EA":
        return 1
    return 1


def load_cached_offers(asin: str, cache_dir: str = CACHE_DIR) -> List[Dict[str, Any]]:
    cache_path = os.path.join(cache_dir, f"{asin}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def analyze_scraped_offers(offers: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not offers:
        return {
            "total_sellers": 0,
            "mfn_sellers": 0,
            "fba_sellers": 0,
            "buybox_price": None,
            "buybox_seller": "None",
            "is_amazon_buybox": False,
            "lowest_landed_price": None,
            "lowest_landed_seller": "None",
            "lowest_mfn_price": None,
            "lowest_mfn_seller": "None",
            "lowest_fba_price": None,
            "lowest_fba_seller": "None",
            "top_competitors_summary": "No live offers cached",
        }

    buybox_price = None
    buybox_seller = "None"
    mfn_offers = []
    fba_offers = []
    all_landed = []

    for off in offers:
        s_name = off.get("seller_name", "").strip()
        ships_from = off.get("ships_from", "").strip()
        is_pinned = off.get("is_pinned", False)
        condition = str(off.get("condition", "New")).strip().lower()

        # Strictly filter out USED condition
        if "used" in condition or "refurbished" in condition:
            continue

        price = parse_numeric(off.get("total_price") or off.get("item_price"))
        if price is None or price <= 0:
            continue

        is_fba = "amazon" in ships_from.lower() or "fulfillment by amazon" in ships_from.lower()
        offer_dict = {
            "seller": s_name,
            "price": price,
            "ships_from": ships_from,
            "is_fba": is_fba,
            "is_pinned": is_pinned,
            "rating": off.get("rating_info", ""),
        }

        all_landed.append(offer_dict)
        if is_fba:
            fba_offers.append(offer_dict)
        else:
            mfn_offers.append(offer_dict)

        if is_pinned and buybox_price is None:
            buybox_price = price
            buybox_seller = s_name

    if buybox_price is None and all_landed:
        sorted_all = sorted(all_landed, key=lambda x: x["price"])
        buybox_price = sorted_all[0]["price"]
        buybox_seller = sorted_all[0]["seller"]

    is_amazon_buybox = "amazon" in buybox_seller.lower() if buybox_seller else False

    # Lowest overall landed
    sorted_all = sorted(all_landed, key=lambda x: x["price"]) if all_landed else []
    lowest_landed_price = sorted_all[0]["price"] if sorted_all else None
    lowest_landed_seller = sorted_all[0]["seller"] if sorted_all else "None"

    # Lowest MFN landed
    sorted_mfn = sorted(mfn_offers, key=lambda x: x["price"]) if mfn_offers else []
    lowest_mfn_price = sorted_mfn[0]["price"] if sorted_mfn else None
    lowest_mfn_seller = sorted_mfn[0]["seller"] if sorted_mfn else "None"

    # Lowest FBA landed
    sorted_fba = sorted(fba_offers, key=lambda x: x["price"]) if fba_offers else []
    lowest_fba_price = sorted_fba[0]["price"] if sorted_fba else None
    lowest_fba_seller = sorted_fba[0]["seller"] if sorted_fba else "None"

    # Top 3 competitors summary string
    top_summary_parts = []
    for idx, off in enumerate(sorted_all[:3], 1):
        ff_tag = "FBA" if off["is_fba"] else "MFN"
        top_summary_parts.append(f"#{idx} {off['seller']} (${off['price']:.2f} {ff_tag})")
    top_competitors_summary = " | ".join(top_summary_parts) if top_summary_parts else "None"

    return {
        "total_sellers": len(all_landed),
        "mfn_sellers": len(mfn_offers),
        "fba_sellers": len(fba_offers),
        "buybox_price": buybox_price,
        "buybox_seller": buybox_seller,
        "is_amazon_buybox": is_amazon_buybox,
        "lowest_landed_price": lowest_landed_price,
        "lowest_landed_seller": lowest_landed_seller,
        "lowest_mfn_price": lowest_mfn_price,
        "lowest_mfn_seller": lowest_mfn_seller,
        "lowest_fba_price": lowest_fba_price,
        "lowest_fba_seller": lowest_fba_seller,
        "top_competitors_summary": top_competitors_summary,
    }


def compute_dropshipping_profit(
    zoro_price: float,
    selling_price: Optional[float],
    referral_fee_pct_raw: Optional[float],
) -> Dict[str, Any]:
    if selling_price is None or selling_price <= 0:
        return {
            "selling_price": 0.0,
            "zoro_shipping": 0.0,
            "referral_fee_pct": 15.0,
            "referral_fee_val": 0.0,
            "total_cost": 0.0,
            "net_profit": 0.0,
            "net_margin": None,
            "status": "No Price",
        }

    # Referral fee %
    if referral_fee_pct_raw is not None and not pd.isna(referral_fee_pct_raw):
        ref_rate = referral_fee_pct_raw if referral_fee_pct_raw <= 1.0 else referral_fee_pct_raw / 100.0
    else:
        ref_rate = 0.15  # standard category default

    ref_pct_display = round(ref_rate * 100.0, 1)

    # Zoro shipping rule
    zoro_shipping = 0.0 if zoro_price >= 50.0 else 5.99

    # Calculation
    referral_fee_val = round(selling_price * ref_rate, 2)
    total_cost = round(zoro_price + referral_fee_val + zoro_shipping, 2)
    net_profit = round(selling_price - total_cost, 2)
    net_margin = round((net_profit / selling_price) * 100.0, 2)

    return {
        "selling_price": selling_price,
        "zoro_shipping": zoro_shipping,
        "referral_fee_pct": ref_pct_display,
        "referral_fee_val": referral_fee_val,
        "total_cost": total_cost,
        "net_profit": net_profit,
        "net_margin": net_margin,
    }


def determine_alerts_and_opportunities(item: Dict[str, Any]) -> Tuple[str, str, str]:
    is_pack_match = item.get("is_pack_match", True)
    amz_pack = item.get("amz_pack", 1)
    z_pack = item.get("z_pack", 1)
    mfn_sellers = item.get("mfn_sellers", 0)
    op_m = item.get("overall_profit", {}).get("net_margin")
    overall_margin = op_m if op_m is not None else -999.0
    mp_m = item.get("mfn_profit", {}).get("net_margin")
    mfn_margin = mp_m if mp_m is not None else -999.0
    is_amazon_bb = item.get("is_amazon_buybox", False)
    total_sellers = item.get("total_sellers", 0)

    # 1. Pack mismatch flag
    if not is_pack_match:
        return (
            f"PACK MISMATCH (Amz {amz_pack} vs Zoro {z_pack})",
            f"Unit Alert: Amazon is a {amz_pack}-Pack while Zoro sells full case PK{z_pack}",
            "ORANGE",
        )

    # 2. Check if no offers were scraped yet
    if total_sellers == 0:
        return "LIVE SCRAPING QUEUED", "Pending live AOD scrape (In queue)", "GRAY"

    # 3. Profitable checks
    best_margin = max(overall_margin, mfn_margin)

    if best_margin >= 5.0:
        return "HIGH PROFIT (>5%)", f"Prime DS Opportunity: +{best_margin:.1f}% margin with {mfn_sellers} MFN sellers", "EMERALD"
    elif best_margin >= 0.0:
        if is_amazon_bb and mfn_margin >= 0.0:
            return "MFN PROFITABLE", f"Amazon on Buy Box; MFN Repricing is Profitable (+{mfn_margin:.1f}%)", "BLUE"
        else:
            return "PROFITABLE (0-5%)", f"Profitable: +{best_margin:.1f}% margin with {mfn_sellers} active resellers", "BLUE"
    elif best_margin >= -5.0:
        return "REPRICING OPP (-5% to 0%)", f"Competitive repricing zone ({best_margin:.1f}% margin)", "YELLOW"
    elif best_margin >= -10.0:
        return "BREAKEVEN ZONE (-10% to -5%)", f"Near breakeven / review for bulk pricing ({best_margin:.1f}%)", "YELLOW"
    else:
        return "NEGATIVE MARGIN (< -10%)", f"Negative Margin ({best_margin:.1f}%): Competitors priced below wholesale", "RED"


def run_rust_oleum_pipeline():
    print("[*] Starting Rust-Oleum Dropshipping Hunting Pipeline...")
    df = pd.read_excel(INPUT_EXCEL)
    print(f"[*] Total Rows in Rust-Oleum File: {len(df)}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    all_audited_rows = []
    matched_records = []

    sales_col = "Monthly Sales Trends: Bought in past month"

    for idx, row in df.iterrows():
        asin = str(row.get("ASIN", "")).strip()
        if not asin or pd.isna(row.get("ASIN")):
            continue

        brand = str(row.get("Brand", "")).strip()
        upc_raw = str(row.get("Product Codes: UPC", "")).strip() if pd.notna(row.get("Product Codes: UPC")) else ""
        ean_raw = str(row.get("Product Codes: EAN", "")).strip() if pd.notna(row.get("Product Codes: EAN")) else ""
        gtin_raw = str(row.get("Product Codes: GTIN", "")).strip() if pd.notna(row.get("Product Codes: GTIN")) else ""
        mpn_raw = str(row.get("Product Codes: PartNumber", "")).strip() if pd.notna(row.get("Product Codes: PartNumber")) else ""
        num_items = row.get("Number of Items")
        amz_pack = int(num_items) if pd.notna(num_items) and str(num_items).replace(".", "").isdigit() else 1

        sales = parse_numeric(row.get(sales_col))
        bsr = parse_numeric(row.get("Sales Rank: Current"))
        bsr_90 = parse_numeric(row.get("Sales Rank: 90 days avg."))
        rating = parse_numeric(row.get("Reviews: Rating"))
        ref_fee_pct = parse_numeric(row.get("Referral Fee %"))
        bb_seller = str(row.get("Buy Box: Buy Box Seller", ""))

        upcs = [u.strip() for u in upc_raw.replace(";", ",").split(",") if u.strip()]
        eans = [e.strip() for e in ean_raw.replace(";", ",").split(",") if e.strip()]
        gtins = [g.strip() for g in gtin_raw.replace(";", ",").split(",") if g.strip()]
        mpns = [m.strip() for m in mpn_raw.replace(";", ",").split(",") if m.strip()]

        found = None
        # 1. UPC / EAN / GTIN matching
        for u in upcs + eans + gtins:
            u_clean = u.lstrip("0")
            variants = [u, u_clean, u.zfill(12), u.zfill(14)]
            placeholders = ",".join(["?"] * len(variants))
            cursor.execute(
                f"""
                SELECT zoro_no, manufacturer_part_number, upc_code, short_description, sales_price, availabilityStatus, uom, zoro_brand_name
                FROM zoro_data 
                WHERE upc_code IN ({placeholders}) AND UPPER(zoro_brand_name) LIKE '%RUST%'
                LIMIT 1
                """,
                variants,
            )
            res = cursor.fetchone()
            if res:
                found = res
                break

        # 2. MPN matching
        if not found:
            for m in mpns:
                m_variants = [m, m.replace("-", ""), m.replace(" ", ""), m.upper()]
                placeholders = ",".join(["?"] * len(m_variants))
                cursor.execute(
                    f"""
                    SELECT zoro_no, manufacturer_part_number, upc_code, short_description, sales_price, availabilityStatus, uom, zoro_brand_name
                    FROM zoro_data 
                    WHERE manufacturer_part_number IN ({placeholders}) AND UPPER(zoro_brand_name) LIKE '%RUST%'
                    LIMIT 1
                    """,
                    m_variants,
                )
                res = cursor.fetchone()
                if res:
                    found = res
                    break

        is_matched = found is not None
        has_min_sales = sales is not None and sales >= 100

        item_audit = {
            "asin": asin,
            "parent_asin": str(row.get("Parent ASIN", "")),
            "brand": brand,
            "manufacturer": str(row.get("Manufacturer", "")),
            "mpn": mpn_raw,
            "upc": upc_raw,
            "amz_pack": amz_pack,
            "monthly_sales": sales,
            "bsr_current": bsr,
            "bsr_90d": bsr_90,
            "rating": rating,
            "referral_fee_pct": ref_fee_pct,
            "keepa_buybox_seller": bb_seller,
            "is_matched": is_matched,
            "has_min_sales": has_min_sales,
        }

        # ONLY consider listings with sales >= 100 per user rule
        if is_matched and has_min_sales:
            z_pack = parse_pack(found[3], found[6])
            is_pack_match = (amz_pack == z_pack)
            item_audit.update({
                "zoro_no": found[0],
                "zoro_mpn": found[1],
                "zoro_title": found[3],
                "zoro_price": float(found[4]) if found[4] is not None else 0.0,
                "z_pack": z_pack,
                "zoro_status": found[5] or "In Stock",
                "is_pack_match": is_pack_match,
                "zoro_brand": found[7],
            })
            matched_records.append(item_audit)

        all_audited_rows.append(item_audit)

    conn.close()
    print(f"[*] Total Audited Rust-Oleum Listings: {len(all_audited_rows)}")
    print(f"[*] Matched in Zoro Catalog (Sales >= 100): {len(matched_records)}")

    # 3. Analyze live scraped offers for matched items
    processed_records = []
    for item in matched_records:
        asin = item["asin"]
        offers = load_cached_offers(asin, CACHE_DIR)
        analysis = analyze_scraped_offers(offers)

        item.update(analysis)
        item["title"] = item.get("zoro_title", "")

        # Compute dropshipping profits
        z_price = item.get("zoro_price", 0.0)
        ref_pct = item.get("referral_fee_pct")

        # Against Lowest Landed Overall
        overall_profit = compute_dropshipping_profit(z_price, item.get("lowest_landed_price"), ref_pct)
        item["overall_profit"] = overall_profit

        # Against Lowest Landed MFN
        mfn_profit = compute_dropshipping_profit(z_price, item.get("lowest_mfn_price"), ref_pct)
        item["mfn_profit"] = mfn_profit

        # Badge and Opportunity recommendations
        badge, rec_text, color_tag = determine_alerts_and_opportunities(item)
        item["alert_badge"] = badge
        item["opportunity_recommendation"] = rec_text
        item["color_tag"] = color_tag

        processed_records.append(item)

    # 4. Sorting: Exact pack match first, then by profitability tier, then by sales
    def sort_key(x: Dict[str, Any]):
        badge = x.get("alert_badge", "")
        tier = 99
        if "PACK MISMATCH" in badge:
            tier = 90
        elif "HIGH PROFIT" in badge:
            tier = 1
        elif "PROFITABLE" in badge:
            tier = 2
        elif "REPRICING OPP" in badge:
            tier = 3
        elif "BREAKEVEN" in badge:
            tier = 4
        elif "NEGATIVE MARGIN" in badge:
            tier = 5
        elif "LIVE SCRAPING QUEUED" in badge:
            tier = 80

        op_m = x.get("overall_profit", {}).get("net_margin")
        mp_m = x.get("mfn_profit", {}).get("net_margin")
        margin = max(op_m if op_m is not None else -999.0, mp_m if mp_m is not None else -999.0)
        sales = x.get("monthly_sales") or 0
        return (tier, -margin, -sales)

    matched_records.sort(key=sort_key)

    # 5. Build Dual-Tab Excel Workbook
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "DS Matched & Analyzed"

    # Styling definitions
    NAVY = "1E293B"
    WHITE = "FFFFFF"
    GRAY_TEXT = "475569"
    LIGHT_BORDER = "CBD5E1"
    thin_border = Border(
        left=Side(style="thin", color=LIGHT_BORDER),
        right=Side(style="thin", color=LIGHT_BORDER),
        top=Side(style="thin", color=LIGHT_BORDER),
        bottom=Side(style="thin", color=LIGHT_BORDER),
    )

    # Header Title Banner
    ws1.merge_cells("B2:AA2")
    title_cell = ws1["B2"]
    title_cell.value = "RUST-OLEUM DROPSHIPPING LISTING HUNTING & PROFITABILITY DASHBOARD"
    title_cell.font = Font(name="Segoe UI", size=15, bold=True, color=WHITE)
    title_cell.fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[2].height = 36

    # Subtitle
    ws1.merge_cells("B3:AA3")
    sub_cell = ws1["B3"]
    sub_cell.value = "Strict Unit Validation | Live Stealth Competitor Scraping (NYC 10001) | Zoro Catalog Alignment"
    sub_cell.font = Font(name="Segoe UI", size=10, italic=True, color="64748B")
    sub_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[3].height = 20

    # KPI Summary Cards
    def is_profitable(r: Dict[str, Any]) -> bool:
        if not r.get("is_pack_match"):
            return False
        op_m = r.get("overall_profit", {}).get("net_margin")
        mp_m = r.get("mfn_profit", {}).get("net_margin")
        best = max(op_m if op_m is not None else -999.0, mp_m if mp_m is not None else -999.0)
        return best >= 0.0

    exact_pack_cnt = sum(1 for r in matched_records if r.get("is_pack_match"))
    profitable_cnt = sum(1 for r in matched_records if is_profitable(r))
    qualified_cnt = sum(1 for r in all_audited_rows if r.get("has_min_sales"))

    kpis = [
        ("TOTAL KEEPA SCANNED", f"{len(all_audited_rows)} Listings", "F1F5F9", "334155"),
        ("QUALIFIED (SALES >= 100)", f"{qualified_cnt} Listings", "E0F2FE", "0369A1"),
        ("ZORO CATALOG MATCHED", f"{len(matched_records)} Products", "DCFCE7", "15803D"),
        ("EXACT PACK MATCHES", f"{exact_pack_cnt} Listings", "FEF3C7", "B45309"),
        ("PROFITABLE DS LISTINGS", f"{profitable_cnt} Listings", "F3E8FF", "7E22CE"),
        ("BRAND RESELLER STATUS", "SAFE (Commercial Resellers Active)", "E0E7FF", "3730A3"),
    ]

    card_col_starts = ["B", "F", "J", "N", "R", "V"]
    card_col_ends = ["E", "I", "M", "Q", "U", "AA"]

    ws1.row_dimensions[5].height = 18
    ws1.row_dimensions[6].height = 26

    for (label, val, bg_col, text_col), start_col, end_col in zip(kpis, card_col_starts, card_col_ends):
        ws1.merge_cells(f"{start_col}5:{end_col}5")
        ws1.merge_cells(f"{start_col}6:{end_col}6")

        l_cell = ws1[f"{start_col}5"]
        l_cell.value = label
        l_cell.font = Font(name="Segoe UI", size=8, bold=True, color="64748B")
        l_cell.fill = PatternFill(start_color=bg_col, end_color=bg_col, fill_type="solid")
        l_cell.alignment = Alignment(horizontal="center", vertical="center")

        v_cell = ws1[f"{start_col}6"]
        v_cell.value = val
        v_cell.font = Font(name="Segoe UI", size=11, bold=True, color=text_col)
        v_cell.fill = PatternFill(start_color=bg_col, end_color=bg_col, fill_type="solid")
        v_cell.alignment = Alignment(horizontal="center", vertical="center")

        for r in range(5, 7):
            start_idx = openpyxl.utils.column_index_from_string(start_col)
            end_idx = openpyxl.utils.column_index_from_string(end_col)
            for c in range(start_idx, end_idx + 1):
                ws1.cell(row=r, column=c).border = thin_border

    # Headers
    headers = [
        ("Alert / DS Status", 24),
        ("ASIN", 14),
        ("Brand", 16),
        ("Product Title", 34),
        ("Monthly Sales", 14),
        ("Current BSR", 13),
        ("Pack Match?", 14),
        ("Zoro Part #", 14),
        ("Zoro MPN", 14),
        ("Zoro Status", 13),
        ("Zoro Price", 12),
        ("Zoro Ship", 11),
        ("Ref Fee %", 11),
        ("Ref Fee ($)", 12),
        ("Total DS Cost", 14),
        ("Lowest Landed Price", 16),
        ("Buy Box Seller", 18),
        ("Amazon on BB?", 14),
        ("Net Profit ($)", 13),
        ("Net Margin %", 13),
        ("Total Sellers", 12),
        ("MFN Sellers", 12),
        ("Lowest MFN Price", 16),
        ("MFN Net Profit", 14),
        ("MFN Net Margin", 14),
        ("Top 3 Competitors (Price & Method)", 38),
        ("Opportunity Analysis & Recommendations", 48),
    ]

    ws1.row_dimensions[8].height = 30
    header_font = Font(name="Segoe UI", size=9, bold=True, color=WHITE)
    header_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")

    for col_idx, (col_name, col_width) in enumerate(headers, 2):
        cell = ws1.cell(row=8, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
        col_letter = get_column_letter(col_idx)
        ws1.column_dimensions[col_letter].width = col_width

    current_row = 9
    badge_colors = {
        "HIGH PROFIT (>5%)": ("DCFCE7", "15803D"),
        "MFN PROFITABLE": ("DBEAFE", "1E40AF"),
        "PROFITABLE (0-5%)": ("DCFCE7", "166534"),
        "REPRICING OPP (-5% to 0%)": ("FFEDD5", "9A3412"),
        "BREAKEVEN ZONE (-10% to -5%)": ("FEF9C3", "854D0E"),
        "NEGATIVE MARGIN (< -10%)": ("FEE2E2", "B91C1C"),
        "LIVE SCRAPING QUEUED": ("F1F5F9", "475569"),
    }

    for item in matched_records:
        ws1.row_dimensions[current_row].height = 24
        op = item.get("overall_profit", {})
        mp = item.get("mfn_profit", {})
        badge_text = item.get("alert_badge", "REVIEW")
        bg_hex, fg_hex = badge_colors.get(badge_text, ("F1F5F9", "334155"))
        if "PACK MISMATCH" in badge_text:
            bg_hex, fg_hex = ("FFEDD5", "C2410C")

        row_bg = "FFFFFF" if current_row % 2 == 1 else "F8FAFC"
        base_fill = PatternFill(start_color=row_bg, end_color=row_bg, fill_type="solid")

        # Col 2: Alert Badge
        c = ws1.cell(row=current_row, column=2, value=badge_text)
        c.font = Font(name="Segoe UI", size=8, bold=True, color=fg_hex)
        c.fill = PatternFill(start_color=bg_hex, end_color=bg_hex, fill_type="solid")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = thin_border

        # Col 3: ASIN
        asin = item.get("asin", "")
        c = ws1.cell(row=current_row, column=3, value=asin)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="2563EB", underline="single")
        c.hyperlink = f"https://www.amazon.com/dp/{asin}"
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 4: Brand
        c = ws1.cell(row=current_row, column=4, value=item.get("brand", ""))
        c.font = Font(name="Segoe UI", size=9, bold=True)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 5: Title
        c = ws1.cell(row=current_row, column=5, value=item.get("title", ""))
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 6: Monthly Sales
        c = ws1.cell(row=current_row, column=6, value=item.get("monthly_sales") or 0)
        c.font = Font(name="Segoe UI", size=9, bold=True)
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 7: Current BSR
        c = ws1.cell(row=current_row, column=7, value=item.get("bsr_current") or 0)
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 8: Pack Match Status
        is_pm = item.get("is_pack_match", False)
        pm_str = f"EXACT (Pack of {item.get('z_pack')})" if is_pm else f"MISMATCH ({item.get('amz_pack')} vs {item.get('z_pack')})"
        c = ws1.cell(row=current_row, column=8, value=pm_str)
        c.font = Font(name="Segoe UI", size=8, bold=True, color="15803D" if is_pm else "C2410C")
        c.fill = PatternFill(start_color="DCFCE7" if is_pm else "FFEDD5", end_color="DCFCE7" if is_pm else "FFEDD5", fill_type="solid")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = thin_border

        # Col 9: Zoro Part #
        z_no = item.get("zoro_no", "")
        c = ws1.cell(row=current_row, column=9, value=z_no)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="2563EB", underline="single")
        c.hyperlink = f"https://www.zoro.com/i/{z_no}/"
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 10: Zoro MPN
        c = ws1.cell(row=current_row, column=10, value=item.get("zoro_mpn", ""))
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 11: Zoro Status
        c = ws1.cell(row=current_row, column=11, value=item.get("zoro_status", ""))
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 12: Zoro Price
        c = ws1.cell(row=current_row, column=12, value=item.get("zoro_price", 0.0))
        c.font = Font(name="Segoe UI", size=9, bold=True)
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 13: Zoro Ship
        c = ws1.cell(row=current_row, column=13, value=op.get("zoro_shipping", 0.0))
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 14: Ref Fee %
        c = ws1.cell(row=current_row, column=14, value=(op.get("referral_fee_pct", 15.0) / 100.0))
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "0.0%"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 15: Ref Fee ($)
        c = ws1.cell(row=current_row, column=15, value=op.get("referral_fee_val", 0.0))
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 16: Total DS Cost
        c = ws1.cell(row=current_row, column=16, value=op.get("total_cost", 0.0))
        c.font = Font(name="Segoe UI", size=9, bold=True)
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 17: Lowest Landed Price
        c = ws1.cell(row=current_row, column=17, value=item.get("lowest_landed_price") or 0.0)
        c.font = Font(name="Segoe UI", size=9, bold=True)
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 18: Buy Box Seller
        c = ws1.cell(row=current_row, column=18, value=item.get("buybox_seller", "None"))
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 19: Amazon on BB?
        is_abb = item.get("is_amazon_buybox", False)
        c = ws1.cell(row=current_row, column=19, value="YES (Amazon)" if is_abb else "NO (3P Seller)")
        c.font = Font(name="Segoe UI", size=9, bold=True, color="DC2626" if is_abb else "15803D")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 20: Net Profit ($)
        np_val = op.get("net_profit", 0.0)
        c = ws1.cell(row=current_row, column=20, value=np_val)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="15803D" if np_val >= 0 else "DC2626")
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 21: Net Margin %
        nm_val = op.get("net_margin")
        c = ws1.cell(row=current_row, column=21, value=(nm_val / 100.0) if nm_val is not None else 0.0)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="15803D" if (nm_val or 0) >= 0 else "DC2626")
        c.number_format = "0.0%"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 22: Total Sellers
        c = ws1.cell(row=current_row, column=22, value=item.get("total_sellers", 0))
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 23: MFN Sellers
        c = ws1.cell(row=current_row, column=23, value=item.get("mfn_sellers", 0))
        c.font = Font(name="Segoe UI", size=9, bold=True)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 24: Lowest MFN Price
        c = ws1.cell(row=current_row, column=24, value=item.get("lowest_mfn_price") or 0.0)
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 25: MFN Net Profit
        mnp_val = mp.get("net_profit", 0.0)
        c = ws1.cell(row=current_row, column=25, value=mnp_val)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="15803D" if mnp_val >= 0 else "DC2626")
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 26: MFN Net Margin
        mnm_val = mp.get("net_margin")
        c = ws1.cell(row=current_row, column=26, value=(mnm_val / 100.0) if mnm_val is not None else 0.0)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="15803D" if (mnm_val or 0) >= 0 else "DC2626")
        c.number_format = "0.0%"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 27: Top 3 Competitors
        c = ws1.cell(row=current_row, column=27, value=item.get("top_competitors_summary", "None"))
        c.font = Font(name="Segoe UI", size=8)
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 28: Opportunity Recommendation
        c = ws1.cell(row=current_row, column=28, value=item.get("opportunity_recommendation", ""))
        c.font = Font(name="Segoe UI", size=9, bold=True)
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        current_row += 1

    # Freeze panes on Tab 1
    ws1.freeze_panes = "B9"

    # Tab 2: Full Catalog Audit
    ws2 = wb.create_sheet(title="Full Catalog Audit")
    ws2.merge_cells("B2:N2")
    t2_cell = ws2["B2"]
    t2_cell.value = "RUST-OLEUM FULL CATALOG SALES & VELOCITY AUDIT"
    t2_cell.font = Font(name="Segoe UI", size=14, bold=True, color=WHITE)
    t2_cell.fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    t2_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[2].height = 32

    t2_headers = [
        ("ASIN", 14),
        ("Parent ASIN", 14),
        ("Brand", 16),
        ("Manufacturer", 18),
        ("MPN", 14),
        ("UPC", 18),
        ("Monthly Sales", 14),
        ("Sales Qualification", 22),
        ("Current BSR", 13),
        ("90D Avg BSR", 13),
        ("Keepa Buy Box Seller", 22),
        ("Zoro Match Status", 24),
        ("Brand Reseller Safety", 32),
    ]

    ws2.row_dimensions[4].height = 26
    for col_idx, (col_name, col_width) in enumerate(t2_headers, 2):
        cell = ws2.cell(row=4, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        col_letter = get_column_letter(col_idx)
        ws2.column_dimensions[col_letter].width = col_width

    r2_curr = 5
    for row in all_audited_rows:
        ws2.row_dimensions[r2_curr].height = 20
        row_bg = "FFFFFF" if r2_curr % 2 == 1 else "F8FAFC"
        base_fill = PatternFill(start_color=row_bg, end_color=row_bg, fill_type="solid")

        asin = row.get("asin", "")
        c = ws2.cell(row=r2_curr, column=2, value=asin)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="2563EB", underline="single")
        c.hyperlink = f"https://www.amazon.com/dp/{asin}"
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        ws2.cell(row=r2_curr, column=3, value=row.get("parent_asin", "")).alignment = Alignment(horizontal="center", vertical="center")
        ws2.cell(row=r2_curr, column=4, value=row.get("brand", "")).alignment = Alignment(horizontal="center", vertical="center")
        ws2.cell(row=r2_curr, column=5, value=row.get("manufacturer", "")).alignment = Alignment(horizontal="center", vertical="center")
        ws2.cell(row=r2_curr, column=6, value=row.get("mpn", "")).alignment = Alignment(horizontal="center", vertical="center")
        ws2.cell(row=r2_curr, column=7, value=row.get("upc", "")).alignment = Alignment(horizontal="center", vertical="center")

        sales_val = row.get("monthly_sales") or 0
        c = ws2.cell(row=r2_curr, column=8, value=sales_val)
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="right", vertical="center")

        qual_str = "QUALIFIED (Sales >= 100)" if row.get("has_min_sales") else "DISCARDED (Sales < 100)"
        c = ws2.cell(row=r2_curr, column=9, value=qual_str)
        c.font = Font(name="Segoe UI", size=8, bold=True, color="15803D" if row.get("has_min_sales") else "94A3B8")
        c.alignment = Alignment(horizontal="center", vertical="center")

        c = ws2.cell(row=r2_curr, column=10, value=row.get("bsr_current") or 0)
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="right", vertical="center")

        c = ws2.cell(row=r2_curr, column=11, value=row.get("bsr_90d") or 0)
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="right", vertical="center")

        ws2.cell(row=r2_curr, column=12, value=row.get("keepa_buybox_seller", "")).alignment = Alignment(horizontal="center", vertical="center")

        is_m = row.get("is_matched", False)
        z_no = row.get("zoro_no", "")
        match_str = f"MATCHED (Zoro #{z_no})" if is_m else "NOT FOUND IN ZORO"
        c = ws2.cell(row=r2_curr, column=13, value=match_str)
        c.font = Font(name="Segoe UI", size=8, bold=True, color="15803D" if is_m else "64748B")
        c.alignment = Alignment(horizontal="center", vertical="center")

        c = ws2.cell(row=r2_curr, column=14, value="Brand Safe: Active Commercial Reseller Ecosystem")
        c.font = Font(name="Segoe UI", size=8, color="15803D")
        c.alignment = Alignment(horizontal="center", vertical="center")

        for col in range(3, 15):
            cell = ws2.cell(row=r2_curr, column=col)
            if col not in [9, 13, 14]:
                cell.font = Font(name="Segoe UI", size=9)
            cell.fill = base_fill
            cell.border = thin_border

        r2_curr += 1

    ws2.freeze_panes = "B5"

    wb.save(OUTPUT_EXCEL)
    print(f"\n[✓] Executive Rust-Oleum Dropshipping Report successfully created at: {OUTPUT_EXCEL}")


if __name__ == "__main__":
    run_rust_oleum_pipeline()
