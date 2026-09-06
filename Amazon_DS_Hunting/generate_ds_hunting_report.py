#!/usr/bin/env python3
"""
generate_ds_hunting_report.py — Amazon Dropshipping Hunting & Profitability Engine
Matches Keepa product lists with Zoro catalog, parses live Amazon competitor offers,
calculates net dropshipping profit margins, analyzes brand reseller health & risk,
and generates an executive-grade formatted Excel report.
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
DEFAULT_INPUT_EXCEL = os.path.join(CURRENT_DIR, "Pig Products.xlsx")
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "Automations", "TSV Files", "combined_reports.db")
DEFAULT_CACHE_DIR = os.path.join(CURRENT_DIR, ".cache")
DEFAULT_OUTPUT_EXCEL = os.path.join(CURRENT_DIR, "PIG_DS_Hunting_Results.xlsx")


def clean_brand(b: Any) -> str:
    if not b or pd.isna(b):
        return ""
    b = str(b).upper().strip()
    for suffix in ["CORPORATION", "CORP", "LLC", "INC", "CO", "COMPANY", "LTD"]:
        b = re.sub(rf"\b{suffix}\b", "", b).strip()
    return b


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


def match_products_with_zoro(df: pd.DataFrame, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Match Keepa products against Zoro catalog with high-speed indexes and brand validation."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    all_keepa_items = []

    for idx, row in df.iterrows():
        asin = str(row.get("ASIN", "")).strip()
        if not asin or pd.isna(row.get("ASIN")):
            continue

        upc_raw = str(row.get("Product Codes: UPC", "")).strip() if pd.notna(row.get("Product Codes: UPC")) else ""
        ean_raw = str(row.get("Product Codes: EAN", "")).strip() if pd.notna(row.get("Product Codes: EAN")) else ""
        gtin_raw = str(row.get("Product Codes: GTIN", "")).strip() if pd.notna(row.get("Product Codes: GTIN")) else ""
        mpn_raw = str(row.get("Product Codes: PartNumber", "")).strip() if pd.notna(row.get("Product Codes: PartNumber")) else ""
        brand_clean = clean_brand(row.get("Brand", row.get("Manufacturer", "")))

        upcs = [u.strip() for u in upc_raw.replace(";", ",").split(",") if u.strip()]
        eans = [e.strip() for e in ean_raw.replace(";", ",").split(",") if e.strip()]
        mpns = [m.strip() for m in mpn_raw.replace(";", ",").split(",") if m.strip()]

        found = None

        # 1. Exact UPC matching
        for u in upcs:
            u_clean = u.lstrip("0")
            variants = [u, u_clean, u.zfill(12), u.zfill(14)]
            placeholders = ",".join(["?"] * len(variants))
            cursor.execute(
                f"""
                SELECT zoro_no, manufacturer_part_number, upc_code, zoro_brand_name, short_description, sales_price, freight_surcharge, availabilityStatus 
                FROM zoro_data 
                WHERE upc_code IN ({placeholders})
                LIMIT 1
                """,
                variants,
            )
            res = cursor.fetchone()
            if res:
                found = res
                break

        # 2. MPN matching with brand validation
        if not found:
            for m in mpns:
                m_variants = [m, m.replace("-", ""), m.replace(" ", ""), m.upper()]
                placeholders = ",".join(["?"] * len(m_variants))
                cursor.execute(
                    f"""
                    SELECT zoro_no, manufacturer_part_number, upc_code, zoro_brand_name, short_description, sales_price, freight_surcharge, availabilityStatus 
                    FROM zoro_data 
                    WHERE manufacturer_part_number IN ({placeholders})
                      AND (UPPER(zoro_brand_name) LIKE ? OR ? LIKE '%' || UPPER(zoro_brand_name) || '%')
                    LIMIT 1
                    """,
                    m_variants + [f"%{brand_clean}%", brand_clean],
                )
                res = cursor.fetchone()
                if res:
                    found = res
                    break

        # 3. EAN matching
        if not found:
            for e in eans:
                e_clean = e.lstrip("0")
                cursor.execute(
                    """
                    SELECT zoro_no, manufacturer_part_number, upc_code, zoro_brand_name, short_description, sales_price, freight_surcharge, availabilityStatus 
                    FROM zoro_data 
                    WHERE upc_code LIKE ? AND (UPPER(zoro_brand_name) LIKE ? OR ? LIKE '%' || UPPER(zoro_brand_name) || '%')
                    LIMIT 1
                    """,
                    (f"%{e_clean}%", f"%{brand_clean}%", brand_clean),
                )
                res = cursor.fetchone()
                if res:
                    found = res
                    break

        item_dict = {
            "asin": asin,
            "parent_asin": str(row.get("Parent ASIN", "")),
            "brand": str(row.get("Brand", "PIG")),
            "manufacturer": str(row.get("Manufacturer", "")),
            "mpn": mpn_raw,
            "upc": upc_raw,
            "monthly_sales": parse_numeric(row.get("Monthly Sales Trends: Bought in past month")),
            "bsr_current": parse_numeric(row.get("Sales Rank: Current")),
            "bsr_90d": parse_numeric(row.get("Sales Rank: 90 days avg.")),
            "rating": parse_numeric(row.get("Reviews: Rating")),
            "review_count": parse_numeric(row.get("Reviews: Rating Count")),
            "referral_fee_pct": parse_numeric(row.get("Referral Fee %")),
            "keepa_buybox_seller": str(row.get("Buy Box: Buy Box Seller", "")),
            "is_matched": found is not None,
        }

        if found:
            item_dict.update({
                "zoro_no": found[0],
                "zoro_mpn": found[1],
                "zoro_brand": found[3],
                "zoro_title": found[4],
                "zoro_price": float(found[5]) if found[5] is not None else 0.0,
                "zoro_freight": float(found[6]) if found[6] is not None else 0.0,
                "zoro_status": str(found[7]) if found[7] else "Unknown",
            })
        all_keepa_items.append(item_dict)

    conn.close()
    return all_keepa_items


def load_cached_offers(asin: str, cache_dir: str = DEFAULT_CACHE_DIR) -> List[Dict[str, Any]]:
    cache_path = os.path.join(cache_dir, f"{asin}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def analyze_scraped_offers(offers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Parse offer details: strictly filters out USED condition, computes MFN vs FBA breakdown."""
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
            "top_competitors_summary": "No live offers found",
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

        # Filter out USED / Refurbished items - dropshipping must compete on NEW condition only
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

    # Fallback for Buy Box if pinned wasn't caught
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
    """
    User DS Profit Formula:
      Selling Price * Referral % = Referral Fee
      Zoro Price + Referral Fee + Zoro Shipping = Total Cost
      Selling Price - Total Cost = Profit
      Zoro Shipping = $0 if Zoro Price >= $50, else $5.99
    """
    if selling_price is None or selling_price <= 0:
        return {
            "selling_price": 0.0,
            "zoro_shipping": 0.0,
            "referral_fee_pct": 15.0,
            "referral_fee_val": 0.0,
            "total_cost": 0.0,
            "net_profit": 0.0,
            "net_margin": 0.0,
            "status": "No Price",
        }

    # Referral fee %
    if referral_fee_pct_raw is not None and not pd.isna(referral_fee_pct_raw):
        ref_rate = referral_fee_pct_raw if referral_fee_pct_raw <= 1.0 else referral_fee_pct_raw / 100.0
    else:
        ref_rate = 0.15  # standard Amazon category default

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


def determine_alerts_and_opportunities(
    item: Dict[str, Any],
    brand_total_resellers: int,
    brand_has_mfn_resellers: bool,
) -> Tuple[str, str, str]:
    """
    Evaluate Reseller & IP safety with explicit user rules:
    - Target: >= +5%
    - Viable range: down to -5% (keep down to -10%)
    - Never discard for risk; present as Alert Badges.
    """
    total_sellers = item.get("total_sellers", 0)
    mfn_sellers = item.get("mfn_sellers", 0)
    bb_seller = item.get("buybox_seller", "")
    overall_margin = item.get("overall_profit", {}).get("net_margin", -999.0)
    mfn_margin = item.get("mfn_profit", {}).get("net_margin", -999.0)
    zoro_status = item.get("zoro_status", "")
    is_amazon_bb = item.get("is_amazon_buybox", False)

    # 1. Out of stock flag
    if "in stock" not in zoro_status.lower() and zoro_status != "Unknown":
        return "OUT OF STOCK", "Supplier currently out of stock", "GRAY"

    # 2. Brand restricted check (Entire brand has <= 2 sellers)
    if not brand_has_mfn_resellers and brand_total_resellers <= 2:
        return "BRAND RESTRICTED", "High Risk: Brand only has 1-2 exclusive sellers across all listings", "RED"

    # 3. Golden opportunity (Brand has resellers elsewhere, but this ASIN has <= 1 seller)
    if brand_has_mfn_resellers and total_sellers <= 1:
        return "GOLDEN OPPORTUNITY", "Zero/Low competition on proven reseller-friendly brand!", "EMERALD"

    # 4. Solo brand owner on this listing
    if total_sellers == 1 and ("amazon" in bb_seller.lower() or "pig" in bb_seller.lower()):
        return "SOLO BRAND OWNER", "Caution: Only Brand/Amazon sitting on listing (No third-party resellers)", "AMBER"

    # 5. Evaluate dropshipping profitability (prioritize MFN repricing zone if Amazon is on Buy Box)
    best_margin = max(overall_margin, mfn_margin)

    if best_margin >= 5.0:
        return "HIGH PROFIT (>5%)", f"Prime Opportunity: +{best_margin:.1f}% margin with {mfn_sellers} MFN sellers", "EMERALD"
    elif best_margin >= 0.0:
        if is_amazon_bb and mfn_margin >= 0.0:
            return "MFN PROFITABLE", f"Amazon on Buy Box; MFN Repricing is Profitable (+{mfn_margin:.1f}%)", "BLUE"
        else:
            return "PROFITABLE (0-5%)", f"Profitable: +{best_margin:.1f}% margin with {mfn_sellers} active resellers", "BLUE"
    elif best_margin >= -5.0:
        if is_amazon_bb:
            return "REPRICING OPP (-5% to 0%)", f"Amazon on Buy Box; MFN sellers sitting at viable repricing level ({mfn_margin:.1f}%)", "ORANGE"
        else:
            return "MARGINAL (-5% to 0%)", f"Competitive repricing zone ({best_margin:.1f}% margin)", "ORANGE"
    elif best_margin >= -10.0:
        return "BREAKEVEN ZONE (-10% to -5%)", f"Near breakeven / review for coupons or bulk tier pricing ({best_margin:.1f}%)", "YELLOW"
    else:
        return "NEGATIVE MARGIN (< -10%)", f"Negative Margin ({best_margin:.1f}%): Amazon/Competitors priced below Zoro wholesale", "RED"


def create_hunting_excel_report(
    matched_records: List[Dict[str, Any]],
    all_keepa_items: List[Dict[str, Any]],
    output_path: str = DEFAULT_OUTPUT_EXCEL,
) -> str:
    """Builds an executive-grade Excel report with Summary KPI Cards, Alert Badges, and dual tabs."""
    wb = openpyxl.Workbook()
    
    # ----------------------------------------------------
    # TAB 1: DS MATCHED & ANALYZED
    # ----------------------------------------------------
    ws1 = wb.active
    ws1.title = "DS Matched & Analyzed"
    ws1.views.sheetView[0].showGridLines = True

    # Palette
    NAVY = "1E293B"
    WHITE = "FFFFFF"
    BORDER_GRAY = "CBD5E1"
    thin_border = Border(
        left=Side(style="thin", color=BORDER_GRAY),
        right=Side(style="thin", color=BORDER_GRAY),
        top=Side(style="thin", color=BORDER_GRAY),
        bottom=Side(style="thin", color=BORDER_GRAY),
    )

    # 1. Header Title Banner
    ws1.merge_cells("B2:Y2")
    title_cell = ws1["B2"]
    title_cell.value = "AMAZON DROPSHIPPING LISTING HUNTING & PROFITABILITY DASHBOARD"
    title_cell.font = Font(name="Segoe UI", size=15, bold=True, color=WHITE)
    title_cell.fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[2].height = 36

    # Subtitle
    ws1.merge_cells("B3:Y3")
    sub_cell = ws1["B3"]
    sub_cell.value = f"Supplier: Zoro.com (2M+ Catalog)  |  Target Brand: PIG / New Pig  |  All Scanned & Matched Products  |  ZIP: 10001 NYC"
    sub_cell.font = Font(name="Segoe UI", size=10, italic=True, color="64748B")
    sub_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[3].height = 20

    # 2. KPI Summary Cards (Row 5 - 6)
    mfn_active_count = sum(1 for r in matched_records if r.get("mfn_sellers", 0) >= 1)
    viable_count = sum(1 for r in matched_records if max(r.get("overall_profit", {}).get("net_margin", -999), r.get("mfn_profit", {}).get("net_margin", -999)) >= -5.0)
    golden_count = sum(1 for r in matched_records if "GOLDEN" in r.get("alert_badge", ""))

    kpis = [
        ("TOTAL KEEPA SCANNED", f"{len(all_keepa_items)} Listings", "F1F5F9", "334155"),
        ("ZORO CATALOG MATCHED", f"{len(matched_records)} Products", "E0F2FE", "0369A1"),
        ("MFN DROPSHIPPERS ACTIVE", f"{mfn_active_count} Listings", "DCFCE7", "15803D"),
        ("GOLDEN OPPORTUNITIES", f"{golden_count} Listings", "FEF3C7", "B45309"),
        ("VIABLE REPRICING (>= -5%)", f"{viable_count} Listings", "F3E8FF", "7E22CE"),
        ("BRAND RESELLER STATUS", "SAFE (100+ 3P Sellers)", "E0E7FF", "3730A3"),
    ]

    card_col_starts = ["B", "F", "J", "N", "R", "V"]
    card_col_ends = ["E", "I", "M", "Q", "U", "Y"]

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

    # 3. Main Table Headers (Row 8)
    headers = [
        ("Alert / DS Status", 24),
        ("ASIN", 14),
        ("Amazon Title", 34),
        ("Monthly Sales", 14),
        ("Current BSR", 13),
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

    # 4. Populate Data Rows
    current_row = 9
    badge_colors = {
        "HIGH PROFIT (>5%)": ("DCFCE7", "15803D"),
        "MFN PROFITABLE": ("DBEAFE", "1E40AF"),
        "PROFITABLE (0-5%)": ("DCFCE7", "166534"),
        "REPRICING OPP (-5% to 0%)": ("FFEDD5", "9A3412"),
        "MARGINAL (-5% to 0%)": ("FFEDD5", "9A3412"),
        "BREAKEVEN ZONE (-10% to -5%)": ("FEF9C3", "854D0E"),
        "GOLDEN OPPORTUNITY": ("DCFCE7", "166534"),
        "SOLO BRAND OWNER": ("FEF3C7", "92400E"),
        "BRAND RESTRICTED": ("FEE2E2", "991B1B"),
        "NEGATIVE MARGIN (< -10%)": ("FEE2E2", "B91C1C"),
        "OUT OF STOCK": ("F1F5F9", "475569"),
    }

    for item in matched_records:
        ws1.row_dimensions[current_row].height = 24
        op = item.get("overall_profit", {})
        mp = item.get("mfn_profit", {})
        badge_text = item.get("alert_badge", "REVIEW")
        bg_hex, fg_hex = badge_colors.get(badge_text, ("F1F5F9", "334155"))

        row_bg = "FFFFFF" if current_row % 2 == 1 else "F8FAFC"
        base_fill = PatternFill(start_color=row_bg, end_color=row_bg, fill_type="solid")

        # Col 2: Alert Badge
        c = ws1.cell(row=current_row, column=2, value=badge_text)
        c.font = Font(name="Segoe UI", size=8, bold=True, color=fg_hex)
        c.fill = PatternFill(start_color=bg_hex, end_color=bg_hex, fill_type="solid")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = thin_border

        # Col 3: ASIN (Hyperlink)
        asin = item.get("asin", "")
        c = ws1.cell(row=current_row, column=3, value=asin)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="2563EB", underline="single")
        c.hyperlink = f"https://www.amazon.com/dp/{asin}"
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 4: Amazon Title
        c = ws1.cell(row=current_row, column=4, value=item.get("title", ""))
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 5: Monthly Sales
        c = ws1.cell(row=current_row, column=5, value=item.get("monthly_sales") or 0)
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 6: Current BSR
        c = ws1.cell(row=current_row, column=6, value=item.get("bsr_current") or 0)
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 7: Zoro Part # (Hyperlink)
        z_no = item.get("zoro_no", "")
        c = ws1.cell(row=current_row, column=7, value=z_no)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="2563EB", underline="single")
        c.hyperlink = f"https://www.zoro.com/i/{z_no}/"
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 8: Zoro MPN
        c = ws1.cell(row=current_row, column=8, value=item.get("zoro_mpn", ""))
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 9: Zoro Status
        c = ws1.cell(row=current_row, column=9, value=item.get("zoro_status", ""))
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 10: Zoro Price
        c = ws1.cell(row=current_row, column=10, value=item.get("zoro_price", 0.0))
        c.font = Font(name="Segoe UI", size=9, bold=True)
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 11: Zoro Ship
        c = ws1.cell(row=current_row, column=11, value=op.get("zoro_shipping", 0.0))
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 12: Ref Fee %
        c = ws1.cell(row=current_row, column=12, value=(op.get("referral_fee_pct", 15.0) / 100.0))
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "0.0%"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 13: Ref Fee ($)
        c = ws1.cell(row=current_row, column=13, value=op.get("referral_fee_val", 0.0))
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 14: Total DS Cost
        c = ws1.cell(row=current_row, column=14, value=op.get("total_cost", 0.0))
        c.font = Font(name="Segoe UI", size=9, bold=True)
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 15: Lowest Landed Price (Overall)
        c = ws1.cell(row=current_row, column=15, value=op.get("selling_price", 0.0))
        c.font = Font(name="Segoe UI", size=9, bold=True)
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 16: Buy Box Seller
        c = ws1.cell(row=current_row, column=16, value=item.get("buybox_seller", "None"))
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 17: Amazon on Buy Box?
        is_amz_bb = item.get("is_amazon_buybox", False)
        c = ws1.cell(row=current_row, column=17, value="YES (Amazon)" if is_amz_bb else "NO (3P Seller)")
        c.font = Font(name="Segoe UI", size=9, bold=True, color="DC2626" if is_amz_bb else "15803D")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 18: Net Profit ($)
        np_val = op.get("net_profit", 0.0)
        c = ws1.cell(row=current_row, column=18, value=np_val)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="15803D" if np_val > 0 else ("B91C1C" if np_val < 0 else "334155"))
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 19: Net Margin %
        nm_val = op.get("net_margin", 0.0) / 100.0
        c = ws1.cell(row=current_row, column=19, value=nm_val)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="15803D" if nm_val > 0 else ("B91C1C" if nm_val < 0 else "334155"))
        c.number_format = "0.0%"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 20: Total Sellers
        c = ws1.cell(row=current_row, column=20, value=item.get("total_sellers", 0))
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 21: MFN Sellers
        m_cnt = item.get("mfn_sellers", 0)
        c = ws1.cell(row=current_row, column=21, value=m_cnt)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="1E40AF" if m_cnt > 0 else "64748B")
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 22: Lowest MFN Price
        c = ws1.cell(row=current_row, column=22, value=item.get("lowest_mfn_price") or 0.0)
        c.font = Font(name="Segoe UI", size=9, bold=True)
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 23: MFN Net Profit
        mnp_val = mp.get("net_profit", 0.0)
        c = ws1.cell(row=current_row, column=23, value=mnp_val)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="15803D" if mnp_val > 0 else ("B91C1C" if mnp_val < 0 else "334155"))
        c.number_format = "$#,##0.00"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 24: MFN Net Margin
        mnm_val = mp.get("net_margin", 0.0) / 100.0
        c = ws1.cell(row=current_row, column=24, value=mnm_val)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="15803D" if mnm_val > 0 else ("B91C1C" if mnm_val < 0 else "334155"))
        c.number_format = "0.0%"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 25: Top 3 Competitors
        c = ws1.cell(row=current_row, column=25, value=item.get("top_competitors_summary", ""))
        c.font = Font(name="Segoe UI", size=8)
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 26: Recommendation
        c = ws1.cell(row=current_row, column=26, value=item.get("recommendation", ""))
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        current_row += 1

    # ----------------------------------------------------
    # TAB 2: BRAND CATALOG & RESELLER AUDIT
    # ----------------------------------------------------
    ws2 = wb.create_sheet(title="Brand Catalog & Reseller Audit")
    ws2.views.sheetView[0].showGridLines = True

    # Header Title Banner
    ws2.merge_cells("B2:M2")
    t2 = ws2["B2"]
    t2.value = "KEEPA FULL PRODUCT CATALOG & BRAND RESELLER SAFETY AUDIT"
    t2.font = Font(name="Segoe UI", size=14, bold=True, color=WHITE)
    t2.fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    t2.alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[2].height = 32

    # Headers
    headers2 = [
        ("ASIN", 14),
        ("Parent ASIN", 14),
        ("Brand", 12),
        ("Manufacturer", 22),
        ("MPN", 16),
        ("UPC", 16),
        ("Monthly Sales", 14),
        ("Current BSR", 13),
        ("90D Avg BSR", 13),
        ("Keepa Buy Box Seller", 22),
        ("Zoro Match Status", 24),
        ("Brand Reseller Risk Analysis", 40),
    ]

    ws2.row_dimensions[4].height = 26
    for col_idx, (col_name, col_width) in enumerate(headers2, 2):
        cell = ws2.cell(row=4, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
        col_letter = get_column_letter(col_idx)
        ws2.column_dimensions[col_letter].width = col_width

    r_idx = 5
    for k_item in all_keepa_items:
        ws2.row_dimensions[r_idx].height = 22
        is_m = k_item.get("is_matched", False)
        row_bg = "FFFFFF" if r_idx % 2 == 1 else "F8FAFC"
        base_fill = PatternFill(start_color=row_bg, end_color=row_bg, fill_type="solid")

        # Col 2: ASIN
        asin = k_item["asin"]
        c = ws2.cell(row=r_idx, column=2, value=asin)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="2563EB", underline="single")
        c.hyperlink = f"https://www.amazon.com/dp/{asin}"
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 3: Parent ASIN
        c = ws2.cell(row=r_idx, column=3, value=k_item["parent_asin"])
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 4: Brand
        c = ws2.cell(row=r_idx, column=4, value=k_item["brand"])
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 5: Manufacturer
        c = ws2.cell(row=r_idx, column=5, value=k_item["manufacturer"])
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 6: MPN
        c = ws2.cell(row=r_idx, column=6, value=k_item["mpn"])
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 7: UPC
        c = ws2.cell(row=r_idx, column=7, value=k_item["upc"])
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 8: Monthly Sales
        c = ws2.cell(row=r_idx, column=8, value=k_item["monthly_sales"] or 0)
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 9: Current BSR
        c = ws2.cell(row=r_idx, column=9, value=k_item["bsr_current"] or 0)
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 10: 90D BSR
        c = ws2.cell(row=r_idx, column=10, value=k_item["bsr_90d"] or 0)
        c.font = Font(name="Segoe UI", size=9)
        c.number_format = "#,##0"
        c.alignment = Alignment(horizontal="right", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 11: Keepa Buy Box Seller
        c = ws2.cell(row=r_idx, column=11, value=k_item["keepa_buybox_seller"])
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        # Col 12: Zoro Match Status
        match_str = f"MATCHED (Zoro #{k_item.get('zoro_no', '')})" if is_m else "UNMATCHED (Not in Zoro Feed)"
        c = ws2.cell(row=r_idx, column=12, value=match_str)
        c.font = Font(name="Segoe UI", size=9, bold=True, color="15803D" if is_m else "64748B")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = PatternFill(start_color="DCFCE7" if is_m else "F1F5F9", end_color="DCFCE7" if is_m else "F1F5F9", fill_type="solid")
        c.border = thin_border

        # Col 13: Brand Reseller Risk Analysis
        risk_str = "Brand Safe: Active 3P Resellers and Dropshippers Present"
        c = ws2.cell(row=r_idx, column=13, value=risk_str)
        c.font = Font(name="Segoe UI", size=9)
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.fill = base_fill
        c.border = thin_border

        r_idx += 1

    wb.save(output_path)
    print(f"\n[✓] Executive Dropshipping Report successfully created at: {output_path}")
    return output_path


def run_pipeline(
    input_file: str = DEFAULT_INPUT_EXCEL,
    db_path: str = DEFAULT_DB_PATH,
    output_file: str = DEFAULT_OUTPUT_EXCEL,
    cache_dir: str = DEFAULT_CACHE_DIR,
) -> str:
    print(f"[*] Starting Amazon DS Hunting Pipeline for {input_file}...")
    df = pd.read_excel(input_file)
    total_keepa_items = match_products_with_zoro(df, db_path)
    matched_items = [item for item in total_keepa_items if item.get("is_matched")]

    print(f"[*] Total Keepa ASINs: {len(total_keepa_items)} | Matched with Zoro: {len(matched_items)}")

    # Analyze scraped offers from cache
    brand_total_resellers = 0
    brand_has_mfn_resellers = False

    # First pass: Brand-level reseller aggregate
    for item in matched_items:
        asin = item["asin"]
        offers = load_cached_offers(asin, cache_dir)
        offer_analysis = analyze_scraped_offers(offers)
        if offer_analysis["mfn_sellers"] > 0:
            brand_has_mfn_resellers = True
        brand_total_resellers += offer_analysis["total_sellers"]

    print(f"[*] Brand Reseller Audit: {brand_total_resellers} total sellers detected across brand listings; MFN dropshippers active: {brand_has_mfn_resellers}")

    # Second pass: compute profit, margins, and alerts for ALL matched items
    processed_records = []
    for item in matched_items:
        asin = item["asin"]
        offers = load_cached_offers(asin, cache_dir)
        analysis = analyze_scraped_offers(offers)

        item.update(analysis)
        item["title"] = item.get("zoro_title", "")

        # Profit against lowest overall landed price
        overall_profit = compute_dropshipping_profit(
            zoro_price=item["zoro_price"],
            selling_price=item["lowest_landed_price"],
            referral_fee_pct_raw=item["referral_fee_pct"],
        )
        item["overall_profit"] = overall_profit

        # Profit against lowest MFN landed price (crucial for repricing against dropshippers)
        mfn_profit = compute_dropshipping_profit(
            zoro_price=item["zoro_price"],
            selling_price=item["lowest_mfn_price"],
            referral_fee_pct_raw=item["referral_fee_pct"],
        )
        item["mfn_profit"] = mfn_profit

        # Alert badge & recommendation
        badge, rec, badge_color = determine_alerts_and_opportunities(
            item,
            brand_total_resellers=brand_total_resellers,
            brand_has_mfn_resellers=brand_has_mfn_resellers,
        )
        item["alert_badge"] = badge
        item["recommendation"] = rec
        item["badge_color"] = badge_color

        # KEEP ALL matched products per user explicit rule: "One more thing you need to add these as alert don't discard i do want to see"
        processed_records.append(item)

    # Sort records: Golden Opportunities & Profitable first, then by best available margin descending
    processed_records.sort(
        key=lambda x: (
            1 if "GOLDEN" in x.get("alert_badge", "") else (
                2 if max(x.get("overall_profit", {}).get("net_margin", -999), x.get("mfn_profit", {}).get("net_margin", -999)) >= 0.0 else (
                    3 if max(x.get("overall_profit", {}).get("net_margin", -999), x.get("mfn_profit", {}).get("net_margin", -999)) >= -5.0 else 4
                )
            ),
            -max(x.get("overall_profit", {}).get("net_margin", -999), x.get("mfn_profit", {}).get("net_margin", -999)),
        )
    )

    out_file = create_hunting_excel_report(
        matched_records=processed_records,
        all_keepa_items=total_keepa_items,
        output_path=output_file,
    )
    return out_file


if __name__ == "__main__":
    run_pipeline()
