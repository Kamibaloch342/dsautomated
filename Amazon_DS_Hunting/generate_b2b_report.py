#!/usr/bin/env python3
"""
generate_b2b_report.py — Amazon Dropshipping Hunting & Profitability Engine for B2B Commercial Brands:
1. Georgia-Pacific (Commercial Paper & Dispensers)
2. Purell / GOJO (Hand Hygiene & Sanitizing Wipes)
3. Nu-Calgon (HVAC / Coil Cleaners & Chemicals)
4. Keson (Measuring & Marking)

Implements User Rules & Deliverables:
1. Discards any listing with Bought in Past Month < 100 (only considers >= 100).
2. Matches against Zoro supplier catalog (`combined_reports.db`) with strict 1:1 unit pack validation.
3. Analyzes live Amazon competitor offers, fulfillment methods (MFN vs FBA), and reseller pricing.
4. Generates executive-grade Excel reports with:
   - TAB 1: ONLY PROFITABLE LISTINGS (Strict user directive: "keep a tab of only prifitable listings")
   - TAB 2: ALL MATCHED & ANALYZED
   - TAB 3: FULL CATALOG AUDIT
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
DB_PATH = os.path.join(PROJECT_ROOT, "Automations", "TSV Files", "combined_reports.db")
CACHE_DIR = os.path.join(CURRENT_DIR, ".cache")

GEORGIA_PACIFIC_INPUT = os.path.join(CURRENT_DIR, "GEORGIA-PACIFIC.xlsx")
MULTIPLE_BRANDS_INPUT = os.path.join(CURRENT_DIR, "Multiple brands .xlsx")

GEORGIA_PACIFIC_OUTPUT = os.path.join(CURRENT_DIR, "Georgia_Pacific_DS_Hunting_Results.xlsx")
MULTIPLE_BRANDS_OUTPUT = os.path.join(CURRENT_DIR, "Multiple_Brands_B2B_DS_Hunting_Results.xlsx")


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
    s = str(desc)
    # Match patterns like "PK80", "80 PK", "80/PK", "80 per box", "Case of 80", "Pack of 80"
    m = re.search(r"PK\s*(\d+)", s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m2 = re.search(r"(\d+)\s*(?:/|-)?\s*PK\b", s, re.IGNORECASE)
    if m2:
        return int(m2.group(1))
    m3 = re.search(r"(\d+)\s*per\s*(?:box|case|carton)", s, re.IGNORECASE)
    if m3:
        return int(m3.group(1))
    m4 = re.search(r"Box of (\d+)", s, re.IGNORECASE)
    if m4:
        return int(m4.group(1))
    m5 = re.search(r"Pack of (\d+)", s, re.IGNORECASE)
    if m5:
        return int(m5.group(1))
    m6 = re.search(r"Case of (\d+)", s, re.IGNORECASE)
    if m6:
        return int(m6.group(1))
    m7 = re.search(r"(\d+)\s*rolls?\b", s, re.IGNORECASE)
    if m7:
        return int(m7.group(1))
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
            "reseller_offers": [],
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

        # Filter out used/refurbished
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
        "mfn_offers": sorted_mfn,
        "top_competitors_summary": top_competitors_summary,
    }


def compute_dropshipping_profit(
    zoro_price: float,
    selling_price: Optional[float],
    referral_fee_pct_raw: Optional[float],
    free_shipping: bool = False,
    apply_coupon: bool = False,
) -> Dict[str, Any]:
    if selling_price is None or selling_price <= 0:
        return {
            "selling_price": 0.0,
            "zoro_cost": 0.0,
            "zoro_shipping": 0.0,
            "referral_fee_pct": 15.0,
            "referral_fee_val": 0.0,
            "total_cost": 0.0,
            "net_profit": 0.0,
            "net_margin": None,
            "status": "No Price",
        }

    if referral_fee_pct_raw is not None and not pd.isna(referral_fee_pct_raw):
        ref_rate = referral_fee_pct_raw if referral_fee_pct_raw <= 1.0 else referral_fee_pct_raw / 100.0
    else:
        ref_rate = 0.15

    ref_pct_display = round(ref_rate * 100.0, 1)

    effective_zoro = round(zoro_price * 0.90, 2) if apply_coupon else zoro_price

    if free_shipping or effective_zoro >= 50.0:
        zoro_shipping = 0.0
    else:
        zoro_shipping = 5.99

    referral_fee_val = round(selling_price * ref_rate, 2)
    total_cost = round(effective_zoro + referral_fee_val + zoro_shipping, 2)
    net_profit = round(selling_price - total_cost, 2)
    net_margin = round((net_profit / selling_price) * 100.0, 2)

    return {
        "selling_price": selling_price,
        "zoro_cost": effective_zoro,
        "zoro_shipping": zoro_shipping,
        "referral_fee_pct": ref_pct_display,
        "referral_fee_val": referral_fee_val,
        "total_cost": total_cost,
        "net_profit": net_profit,
        "net_margin": net_margin,
    }


def determine_alerts_and_opportunities(item: Dict[str, Any]) -> Tuple[str, str, str]:
    is_pm = item.get("is_pack_match", False)
    tot_sellers = item.get("total_sellers", 0)
    mfn_sellers = item.get("mfn_sellers", 0)
    is_amz_bb = item.get("is_amazon_buybox", False)

    # Standard MFN profit
    std_mfn = item.get("std_mfn_profit", {})
    mfn_margin = std_mfn.get("net_margin")
    mfn_profit = std_mfn.get("net_profit", 0.0)

    # Free shipping / cart profit
    fs_mfn = item.get("fs_mfn_profit", {})
    fs_margin = fs_mfn.get("net_margin")
    fs_profit = fs_mfn.get("net_profit", 0.0)

    # 10% coupon profit
    cp_mfn = item.get("cp_mfn_profit", {})
    cp_margin = cp_mfn.get("net_margin")
    cp_profit = cp_mfn.get("net_profit", 0.0)

    # Reseller Reprice profit (Rancho / Megamart / ToolTrades)
    rr_mfn = item.get("rr_profit", {})
    rr_margin = rr_mfn.get("net_margin")
    rr_profit = rr_mfn.get("net_profit", 0.0)
    rr_seller = item.get("rr_seller", "")

    if not is_pm:
        return "PACK MISMATCH", f"Unit pack mismatch: Amazon {item.get('item_pack')} vs Zoro {item.get('zoro_pack')}", "GRAY"

    if tot_sellers == 0:
        return "AWAITING SCRAPE", "Queued for live browser scraping", "GRAY"

    # Profitable checks
    if mfn_margin is not None and mfn_margin >= 5.0:
        return "HIGH PROFIT (>5%)", f"Standard MFN Dropship Profitable: +${mfn_profit:.2f} (+{mfn_margin:.1f}%)", "GREEN"

    if mfn_margin is not None and mfn_margin >= 0.0:
        return "PROFITABLE (0-5%)", f"Standard MFN Dropship Profitable: +${mfn_profit:.2f} (+{mfn_margin:.1f}%)", "GREEN"

    if fs_margin is not None and fs_margin >= 0.0:
        return "FREE-SHIP PROFIT", f"Profitable on Cart/Free-Ship (>= $50 or multi-pack): +${fs_profit:.2f} (+{fs_margin:.1f}%)", "BLUE"

    if cp_margin is not None and cp_margin >= 0.0:
        return "COUPON PROFIT", f"Profitable with Zoro 10% Coupon: +${cp_profit:.2f} (+{cp_margin:.1f}%)", "PURPLE"

    if rr_margin is not None and rr_margin >= 0.0:
        return "RESELLER REPRICE", f"Profitable matching active reseller {rr_seller}: +${rr_profit:.2f} (+{rr_margin:.1f}%)", "BLUE"

    if mfn_margin is not None and mfn_margin >= -10.0:
        return "REPRICING ZONE", f"Competitive within 10%: MFN margin is {mfn_margin:.1f}%", "ORANGE"

    return "NEGATIVE MARGIN", f"Unprofitable: MFN margin is {mfn_margin:.1f}%", "RED"


MATCHED_JSON = os.path.join(CURRENT_DIR, "b2b_matched_items.json")


def load_zoro_brand_items(db_path: str = DB_PATH) -> Dict[str, List[Dict[str, Any]]]:
    """Preloads Zoro items for target brands into memory from zoro_data table."""
    brand_items: Dict[str, List[Dict[str, Any]]] = {}
    if not os.path.exists(db_path):
        return brand_items

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT zoro_no, manufacturer_part_number, short_description, zoro_brand_name, 
                   sales_price, availabilityStatus, uom, upc_code
            FROM zoro_data
            WHERE UPPER(zoro_brand_name) IN ('GEORGIA-PACIFIC', 'PURELL', 'GOJO', 'NU-CALGON', 'KESON')
        """)
        rows = cur.fetchall()
        conn.close()

        for r in rows:
            b = str(r[3]).upper().strip()
            item = {
                "item_number": str(r[0]),
                "mpn": str(r[1]).strip() if r[1] else "",
                "title": str(r[2]).strip() if r[2] else "",
                "brand": b,
                "price": float(r[4]) if r[4] else 0.0,
                "availability": str(r[5]) if r[5] else "",
                "uom": str(r[6]) if r[6] else "EA",
                "upc": str(r[7]).strip() if r[7] else "",
                "pack": parse_pack(r[2], r[6]),
            }
            brand_items.setdefault(b, []).append(item)
    except Exception as e:
        print(f"[!] Warning reading zoro_data: {e}")

    return brand_items


def process_dataset(
    excel_path: str,
    brand_filter: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    df = pd.read_excel(excel_path)
    df.columns = [str(c).strip() for c in df.columns]

    zoro_brand_items = load_zoro_brand_items()

    # Pre-load verified matched manifest if present
    prematched_index: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(MATCHED_JSON):
        try:
            with open(MATCHED_JSON, "r", encoding="utf-8") as f:
                p_items = json.load(f)
                prematched_index = {x["asin"]: x for x in p_items}
        except Exception:
            pass

    all_audited_rows = []
    matched_records = []

    for idx, row in df.iterrows():
        asin = str(row.get("ASIN", "")).strip()
        if not asin or asin.lower() == "nan":
            continue

        raw_sales = row.get("Monthly Sales Trends: Bought in past month")
        monthly_sales = int(parse_numeric(raw_sales) or 0)
        has_min_sales = monthly_sales >= 100

        brand = str(row.get("Brand", "")).strip()
        title = str(row.get("Item Highlights", "")).strip()
        mpn = str(row.get("Product Codes: PartNumber", "")).strip()
        upc = str(row.get("Product Codes: UPC", "")).strip()
        ref_fee_pct = parse_numeric(row.get("Referral Fee %"))
        bsr = parse_numeric(row.get("Sales Rank: Current"))
        item_pack = parse_pack(title, "EA")

        if brand_filter and brand.upper() != brand_filter.upper():
            continue

        audit_item = {
            "asin": asin,
            "brand": brand,
            "title": title,
            "mpn": mpn,
            "upc": upc,
            "monthly_sales": monthly_sales,
            "has_min_sales": has_min_sales,
            "bsr": bsr,
            "referral_fee_pct": ref_fee_pct,
            "item_pack": item_pack,
            "match_status": "UNMATCHED",
            "zoro_item_number": None,
            "zoro_price": None,
            "unmatch_reason": "No catalog match",
        }

        if not has_min_sales:
            audit_item["unmatch_reason"] = "Filtered out: Monthly sales < 100"
            all_audited_rows.append(audit_item)
            continue

        # Look for match in Zoro catalog
        brand_key = brand.upper()
        if "PURELL" in brand_key or "GOJO" in brand_key:
            lookup_brands = ["PURELL", "GOJO"]
        else:
            lookup_brands = [brand_key]

        candidate_items = []
        for lb in lookup_brands:
            candidate_items.extend(zoro_brand_items.get(lb, []))

        matched_zoro = None
        if asin in prematched_index:
            p_match = prematched_index[asin]
            matched_zoro = {
                "item_number": p_match["zoro_no"],
                "mpn": mpn,
                "title": p_match["zoro_title"],
                "brand": p_match.get("zoro_brand", brand),
                "price": p_match["zoro_price"],
                "availability": p_match.get("zoro_status", "In Stock"),
                "uom": "EA",
                "upc": upc,
                "pack": p_match["z_pack"],
            }
            item_pack = p_match["amz_pack"]
        
        # 1. Match by MPN
        if not matched_zoro and mpn and mpn.lower() != "nan":
            clean_mpn = re.sub(r"[^A-Za-z0-9]", "", mpn).upper()
            for zi in candidate_items:
                clean_zmpn = re.sub(r"[^A-Za-z0-9]", "", zi["mpn"]).upper()
                if clean_mpn and clean_zmpn and clean_mpn == clean_zmpn:
                    matched_zoro = zi
                    break

        # 2. Match by UPC
        if not matched_zoro and upc and upc.lower() != "nan":
            clean_upc = re.sub(r"[^0-9]", "", upc)
            for zi in candidate_items:
                clean_zupc = re.sub(r"[^0-9]", "", zi["upc"])
                if clean_upc and clean_zupc and (clean_upc == clean_zupc or clean_upc.endswith(clean_zupc) or clean_zupc.endswith(clean_upc)):
                    matched_zoro = zi
                    break

        # 3. Match by MPN in Title
        if not matched_zoro and mpn and len(mpn) >= 4:
            clean_mpn = re.sub(r"[^A-Za-z0-9]", "", mpn).upper()
            for zi in candidate_items:
                clean_ztitle = re.sub(r"[^A-Za-z0-9]", "", zi["title"]).upper()
                if clean_mpn in clean_ztitle:
                    matched_zoro = zi
                    break

        if not matched_zoro:
            audit_item["unmatch_reason"] = "Not found in Zoro catalog for this brand"
            all_audited_rows.append(audit_item)
            continue

        # Check pack match
        zoro_pack = matched_zoro["pack"]
        is_pack_match = (item_pack == zoro_pack)

        audit_item["match_status"] = "MATCHED"
        audit_item["zoro_item_number"] = matched_zoro["item_number"]
        audit_item["zoro_price"] = matched_zoro["price"]
        audit_item["unmatch_reason"] = "Matched successfully" if is_pack_match else f"Pack mismatch (AMZ {item_pack} vs Zoro {zoro_pack})"
        all_audited_rows.append(audit_item)

        # Scraped offers analysis
        cached_offers = load_cached_offers(asin)
        offer_analysis = analyze_scraped_offers(cached_offers)

        lowest_mfn_p = offer_analysis.get("lowest_mfn_price")
        buybox_p = offer_analysis.get("buybox_price")
        zoro_p = matched_zoro["price"]

        # Financial computations
        std_mfn = compute_dropshipping_profit(zoro_p, lowest_mfn_p, ref_fee_pct, free_shipping=False, apply_coupon=False)
        fs_mfn = compute_dropshipping_profit(zoro_p, lowest_mfn_p, ref_fee_pct, free_shipping=True, apply_coupon=False)
        cp_mfn = compute_dropshipping_profit(zoro_p, lowest_mfn_p, ref_fee_pct, free_shipping=True, apply_coupon=True)
        std_bb = compute_dropshipping_profit(zoro_p, buybox_p, ref_fee_pct, free_shipping=False, apply_coupon=False)

        # Check active reseller prices (Rancho Supply, ToolTrades, BelowPriceDeals, Honest Medical, etc.)
        rr_profit = {}
        rr_seller = ""
        mfn_offers = offer_analysis.get("mfn_offers", [])
        for off in mfn_offers:
            s_name = off.get("seller", "")
            p = off.get("price")
            if p and p > (lowest_mfn_p or 0):
                cand_prof = compute_dropshipping_profit(zoro_p, p, ref_fee_pct, free_shipping=True, apply_coupon=True)
                if cand_prof.get("net_profit", 0) > 0:
                    rr_profit = cand_prof
                    rr_seller = s_name
                    break

        record = {
            **audit_item,
            "zoro_mpn": matched_zoro["mpn"],
            "zoro_title": matched_zoro["title"],
            "zoro_pack": zoro_pack,
            "is_pack_match": is_pack_match,
            "zoro_uom": matched_zoro["uom"],
            "zoro_availability": matched_zoro["availability"],
            "total_sellers": offer_analysis["total_sellers"],
            "mfn_sellers": offer_analysis["mfn_sellers"],
            "fba_sellers": offer_analysis["fba_sellers"],
            "buybox_price": offer_analysis["buybox_price"],
            "buybox_seller": offer_analysis["buybox_seller"],
            "is_amazon_buybox": offer_analysis["is_amazon_buybox"],
            "lowest_landed_price": offer_analysis["lowest_landed_price"],
            "lowest_landed_seller": offer_analysis["lowest_landed_seller"],
            "lowest_mfn_price": lowest_mfn_p,
            "lowest_mfn_seller": offer_analysis["lowest_mfn_seller"],
            "top_competitors_summary": offer_analysis["top_competitors_summary"],
            "std_mfn_profit": std_mfn,
            "fs_mfn_profit": fs_mfn,
            "cp_mfn_profit": cp_mfn,
            "std_bb_profit": std_bb,
            "rr_profit": rr_profit,
            "rr_seller": rr_seller,
        }

        badge, rec, badge_color = determine_alerts_and_opportunities(record)
        record["alert_badge"] = badge
        record["recommendation"] = rec
        record["badge_color"] = badge_color

        matched_records.append(record)

    return matched_records, all_audited_rows


def is_record_profitable(r: Dict[str, Any]) -> bool:
    """Strictly determines if a record qualifies for the ONLY PROFITABLE LISTINGS tab."""
    if not r.get("is_pack_match"):
        return False
    if r.get("total_sellers", 0) == 0:
        return False

    std_m = r.get("std_mfn_profit", {}).get("net_margin")
    fs_m = r.get("fs_mfn_profit", {}).get("net_margin")
    cp_m = r.get("cp_mfn_profit", {}).get("net_margin")
    rr_m = r.get("rr_profit", {}).get("net_margin")
    bb_m = r.get("std_bb_profit", {}).get("net_margin")

    best = max(
        std_m if std_m is not None else -999.0,
        fs_m if fs_m is not None else -999.0,
        cp_m if cp_m is not None else -999.0,
        rr_m if rr_m is not None else -999.0,
        bb_m if bb_m is not None else -999.0,
    )
    return best >= 0.0


def create_excel_report(
    matched_records: List[Dict[str, Any]],
    all_audited_rows: List[Dict[str, Any]],
    output_path: str,
    title_brand: str = "Georgia-Pacific & B2B Brands",
):
    wb = openpyxl.Workbook()

    NAVY = "1E293B"
    WHITE = "FFFFFF"
    BORDER_GRAY = "CBD5E1"
    thin_border = Border(
        left=Side(style="thin", color=BORDER_GRAY),
        right=Side(style="thin", color=BORDER_GRAY),
        top=Side(style="thin", color=BORDER_GRAY),
        bottom=Side(style="thin", color=BORDER_GRAY),
    )

    # Filter profitable records
    profitable_records = [r for r in matched_records if is_record_profitable(r)]
    profitable_records.sort(
        key=lambda x: (
            -(x.get("monthly_sales") or 0),
            -(max(
                x.get("cp_mfn_profit", {}).get("net_margin") or -999,
                x.get("fs_mfn_profit", {}).get("net_margin") or -999,
                x.get("std_mfn_profit", {}).get("net_margin") or -999,
            )),
        )
    )

    # Sort all matched records into tiers
    def get_tier(x: Dict[str, Any]) -> int:
        if is_record_profitable(x):
            return 1
        if x.get("is_pack_match"):
            if x.get("total_sellers", 0) > 0:
                m = x.get("std_mfn_profit", {}).get("net_margin") or -999
                return 2 if m >= -10.0 else 3
            return 4  # Awaiting scrape
        return 5  # Pack mismatch

    matched_records.sort(
        key=lambda x: (
            get_tier(x),
            -(x.get("monthly_sales") or 0),
        )
    )

    # ----------------------------------------------------
    # TAB 1: ONLY PROFITABLE LISTINGS (MANDATORY USER DIRECTIVE)
    # ----------------------------------------------------
    ws1 = wb.active
    ws1.title = "Only Profitable Listings"
    ws1.views.sheetView[0].showGridLines = True

    # Title block
    ws1.merge_cells("B2:Z2")
    t_cell = ws1["B2"]
    t_cell.value = f"⭐ {title_brand.upper()} — HIGH PROFIT DROPSHIPPING LISTINGS"
    t_cell.font = Font(name="Segoe UI", size=16, bold=True, color=WHITE)
    t_cell.fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    t_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[2].height = 40

    ws1.merge_cells("B3:Z3")
    sub_cell = ws1["B3"]
    sub_cell.value = "Strict 1:1 Unit Pack Validated | Filtered for Net Profit > $0.00 | B2B Commercial Demand >= 100 Sales/Month"
    sub_cell.font = Font(name="Segoe UI", size=10, italic=True, color="64748B")
    sub_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[3].height = 20

    # KPI Summary Cards
    exact_pack_cnt = sum(1 for r in matched_records if r.get("is_pack_match"))
    profitable_cnt = len(profitable_records)
    qualified_cnt = sum(1 for r in all_audited_rows if r.get("has_min_sales"))

    kpis = [
        ("TOTAL AUDITED", f"{len(all_audited_rows)} Listings", "F1F5F9", "334155"),
        ("QUALIFIED (SALES >= 100)", f"{qualified_cnt} Listings", "E0F2FE", "0369A1"),
        ("ZORO CATALOG MATCHED", f"{len(matched_records)} Products", "DCFCE7", "15803D"),
        ("EXACT 1:1 PACK MATCHES", f"{exact_pack_cnt} Listings", "FEF3C7", "B45309"),
        ("PROFITABLE DS LISTINGS", f"{profitable_cnt} Listings", "F3E8FF", "7E22CE"),
        ("SOURCING INTEGRITY", "Zoro Industrial B2B", "E0E7FF", "3730A3"),
    ]

    card_col_starts = ["B", "F", "J", "N", "R", "V"]
    card_col_ends = ["E", "I", "M", "Q", "U", "Z"]

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

    # Headers for Tab 1
    t1_headers = [
        ("Profit Strategy / Badge", 24),
        ("ASIN", 14),
        ("Brand", 16),
        ("Product Title", 34),
        ("Monthly Sales", 14),
        ("Current BSR", 13),
        ("Pack Match?", 14),
        ("Zoro Part #", 14),
        ("Zoro Base Price", 14),
        ("Amazon Lowest MFN", 16),
        ("Lowest MFN Seller", 18),
        ("Amazon Buy Box", 14),
        ("Total Sellers", 12),
        ("MFN Sellers", 12),
        ("Ref Fee %", 11),
        ("Std Net Profit ($)", 15),
        ("Std Net Margin %", 15),
        ("Free-Ship Net Profit ($)", 18),
        ("Free-Ship Net Margin %", 18),
        ("10% Coupon Net Profit ($)", 20),
        ("10% Coupon Margin %", 18),
        ("Top Resellers (Rancho/ToolTrades/etc)", 32),
        ("Actionable Dropshipping Playbook", 48),
        ("Amazon Link", 15),
        ("Zoro Search Link", 15),
    ]

    ws1.row_dimensions[8].height = 30
    header_font = Font(name="Segoe UI", size=9, bold=True, color=WHITE)
    header_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")

    for col_idx, (col_name, col_width) in enumerate(t1_headers, 2):
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
        "PROFITABLE (0-5%)": ("DCFCE7", "166534"),
        "FREE-SHIP PROFIT": ("DBEAFE", "1E40AF"),
        "COUPON PROFIT": ("F3E8FF", "7E22CE"),
        "RESELLER REPRICE": ("E0E7FF", "3730A3"),
    }

    for r in profitable_records:
        ws1.row_dimensions[current_row].height = 22
        badge = r.get("alert_badge", "PROFITABLE")
        bg_c, txt_c = badge_colors.get(badge, ("DCFCE7", "166534"))

        std_mfn = r.get("std_mfn_profit", {})
        fs_mfn = r.get("fs_mfn_profit", {})
        cp_mfn = r.get("cp_mfn_profit", {})

        row_data = [
            (badge, "center", Font(name="Segoe UI", size=9, bold=True, color=txt_c), bg_c),
            (r.get("asin"), "center", Font(name="Segoe UI", size=9, bold=True), None),
            (r.get("brand"), "center", Font(name="Segoe UI", size=9), None),
            (r.get("title"), "left", Font(name="Segoe UI", size=9), None),
            (r.get("monthly_sales"), "center", Font(name="Segoe UI", size=9, bold=True), None),
            (r.get("bsr"), "center", Font(name="Segoe UI", size=9), None),
            ("1:1 EXACT MATCH", "center", Font(name="Segoe UI", size=9, bold=True, color="15803D"), "DCFCE7"),
            (r.get("zoro_item_number"), "center", Font(name="Segoe UI", size=9), None),
            (r.get("zoro_price"), "right", Font(name="Segoe UI", size=9, bold=True), None),
            (r.get("lowest_mfn_price"), "right", Font(name="Segoe UI", size=9, bold=True), None),
            (r.get("lowest_mfn_seller"), "left", Font(name="Segoe UI", size=9), None),
            (r.get("buybox_price"), "right", Font(name="Segoe UI", size=9), None),
            (r.get("total_sellers"), "center", Font(name="Segoe UI", size=9), None),
            (r.get("mfn_sellers"), "center", Font(name="Segoe UI", size=9, bold=True), None),
            (f"{std_mfn.get('referral_fee_pct', 15.0):.1f}%", "center", Font(name="Segoe UI", size=9), None),
            (std_mfn.get("net_profit"), "right", Font(name="Segoe UI", size=9, bold=True), None),
            (f"{std_mfn.get('net_margin'):.1f}%" if std_mfn.get("net_margin") is not None else "-", "center", Font(name="Segoe UI", size=9, bold=True), None),
            (fs_mfn.get("net_profit"), "right", Font(name="Segoe UI", size=9, bold=True, color="1E40AF"), "DBEAFE"),
            (f"{fs_mfn.get('net_margin'):.1f}%" if fs_mfn.get("net_margin") is not None else "-", "center", Font(name="Segoe UI", size=9, bold=True, color="1E40AF"), "DBEAFE"),
            (cp_mfn.get("net_profit"), "right", Font(name="Segoe UI", size=9, bold=True, color="7E22CE"), "F3E8FF"),
            (f"{cp_mfn.get('net_margin'):.1f}%" if cp_mfn.get("net_margin") is not None else "-", "center", Font(name="Segoe UI", size=9, bold=True, color="7E22CE"), "F3E8FF"),
            (r.get("top_competitors_summary"), "left", Font(name="Segoe UI", size=8), None),
            (r.get("recommendation"), "left", Font(name="Segoe UI", size=9), None),
            (f"https://www.amazon.com/dp/{r.get('asin')}", "center", Font(name="Segoe UI", size=9, underline="single", color="2563EB"), None),
            (f"https://www.zoro.com/search?q={r.get('zoro_item_number')}", "center", Font(name="Segoe UI", size=9, underline="single", color="2563EB"), None),
        ]

        for col_idx, (val, align, font_obj, cell_bg) in enumerate(row_data, 2):
            cell = ws1.cell(row=current_row, column=col_idx)
            cell.value = val
            cell.alignment = Alignment(horizontal=align, vertical="center")
            cell.font = font_obj
            cell.border = thin_border
            if cell_bg:
                cell.fill = PatternFill(start_color=cell_bg, end_color=cell_bg, fill_type="solid")

            # Numeric formatting
            if col_idx in [10, 11, 13, 17, 19, 21] and isinstance(val, (int, float)):
                cell.number_format = "$#,##0.00"
            elif col_idx in [6, 7] and isinstance(val, (int, float)):
                cell.number_format = "#,##0"

            # Hyperlinks
            if col_idx == 25 and val:
                cell.hyperlink = str(val)
                cell.value = "View on Amazon"
            elif col_idx == 26 and val:
                cell.hyperlink = str(val)
                cell.value = "View on Zoro"

        current_row += 1

    ws1.freeze_panes = "D9"

    # ----------------------------------------------------
    # TAB 2: ALL MATCHED & ANALYZED
    # ----------------------------------------------------
    ws2 = wb.create_sheet(title="All Matched & Analyzed")
    ws2.views.sheetView[0].showGridLines = True

    # Title block
    ws2.merge_cells("B2:Z2")
    t2_cell = ws2["B2"]
    t2_cell.value = f"📦 {title_brand.upper()} — COMPLETE CATALOG MATCHING & PRICING"
    t2_cell.font = Font(name="Segoe UI", size=16, bold=True, color=WHITE)
    t2_cell.fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    t2_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[2].height = 40

    ws2.row_dimensions[5].height = 30
    for col_idx, (col_name, col_width) in enumerate(t1_headers, 2):
        cell = ws2.cell(row=5, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
        col_letter = get_column_letter(col_idx)
        ws2.column_dimensions[col_letter].width = col_width

    all_badge_colors = {
        "HIGH PROFIT (>5%)": ("DCFCE7", "15803D"),
        "PROFITABLE (0-5%)": ("DCFCE7", "166534"),
        "FREE-SHIP PROFIT": ("DBEAFE", "1E40AF"),
        "COUPON PROFIT": ("F3E8FF", "7E22CE"),
        "RESELLER REPRICE": ("E0E7FF", "3730A3"),
        "REPRICING ZONE": ("FEF3C7", "B45309"),
        "NEGATIVE MARGIN": ("FEE2E2", "991B1B"),
        "PACK MISMATCH": ("F1F5F9", "64748B"),
        "AWAITING SCRAPE": ("F1F5F9", "475569"),
    }

    current_row = 6
    for r in matched_records:
        ws2.row_dimensions[current_row].height = 20
        badge = r.get("alert_badge", "ANALYZED")
        bg_c, txt_c = all_badge_colors.get(badge, ("F1F5F9", "334155"))

        std_mfn = r.get("std_mfn_profit", {})
        fs_mfn = r.get("fs_mfn_profit", {})
        cp_mfn = r.get("cp_mfn_profit", {})

        is_pm = r.get("is_pack_match")
        pack_text = "1:1 EXACT MATCH" if is_pm else f"MISMATCH ({r.get('item_pack')} vs {r.get('zoro_pack')})"
        pack_bg = "DCFCE7" if is_pm else "FEE2E2"
        pack_txt = "15803D" if is_pm else "991B1B"

        row_data = [
            (badge, "center", Font(name="Segoe UI", size=9, bold=True, color=txt_c), bg_c),
            (r.get("asin"), "center", Font(name="Segoe UI", size=9, bold=True), None),
            (r.get("brand"), "center", Font(name="Segoe UI", size=9), None),
            (r.get("title"), "left", Font(name="Segoe UI", size=9), None),
            (r.get("monthly_sales"), "center", Font(name="Segoe UI", size=9, bold=True), None),
            (r.get("bsr"), "center", Font(name="Segoe UI", size=9), None),
            (pack_text, "center", Font(name="Segoe UI", size=9, bold=True, color=pack_txt), pack_bg),
            (r.get("zoro_item_number"), "center", Font(name="Segoe UI", size=9), None),
            (r.get("zoro_price"), "right", Font(name="Segoe UI", size=9, bold=True), None),
            (r.get("lowest_mfn_price"), "right", Font(name="Segoe UI", size=9, bold=True), None),
            (r.get("lowest_mfn_seller"), "left", Font(name="Segoe UI", size=9), None),
            (r.get("buybox_price"), "right", Font(name="Segoe UI", size=9), None),
            (r.get("total_sellers"), "center", Font(name="Segoe UI", size=9), None),
            (r.get("mfn_sellers"), "center", Font(name="Segoe UI", size=9, bold=True), None),
            (f"{std_mfn.get('referral_fee_pct', 15.0):.1f}%", "center", Font(name="Segoe UI", size=9), None),
            (std_mfn.get("net_profit"), "right", Font(name="Segoe UI", size=9), None),
            (f"{std_mfn.get('net_margin'):.1f}%" if std_mfn.get("net_margin") is not None else "-", "center", Font(name="Segoe UI", size=9), None),
            (fs_mfn.get("net_profit"), "right", Font(name="Segoe UI", size=9), None),
            (f"{fs_mfn.get('net_margin'):.1f}%" if fs_mfn.get("net_margin") is not None else "-", "center", Font(name="Segoe UI", size=9), None),
            (cp_mfn.get("net_profit"), "right", Font(name="Segoe UI", size=9), None),
            (f"{cp_mfn.get('net_margin'):.1f}%" if cp_mfn.get("net_margin") is not None else "-", "center", Font(name="Segoe UI", size=9), None),
            (r.get("top_competitors_summary"), "left", Font(name="Segoe UI", size=8), None),
            (r.get("recommendation"), "left", Font(name="Segoe UI", size=9), None),
            (f"https://www.amazon.com/dp/{r.get('asin')}", "center", Font(name="Segoe UI", size=9, underline="single", color="2563EB"), None),
            (f"https://www.zoro.com/search?q={r.get('zoro_item_number')}", "center", Font(name="Segoe UI", size=9, underline="single", color="2563EB"), None),
        ]

        for col_idx, (val, align, font_obj, cell_bg) in enumerate(row_data, 2):
            cell = ws2.cell(row=current_row, column=col_idx)
            cell.value = val
            cell.alignment = Alignment(horizontal=align, vertical="center")
            cell.font = font_obj
            cell.border = thin_border
            if cell_bg:
                cell.fill = PatternFill(start_color=cell_bg, end_color=cell_bg, fill_type="solid")

            if col_idx in [10, 11, 13, 17, 19, 21] and isinstance(val, (int, float)):
                cell.number_format = "$#,##0.00"
            elif col_idx in [6, 7] and isinstance(val, (int, float)):
                cell.number_format = "#,##0"

            if col_idx == 25 and val:
                cell.hyperlink = str(val)
                cell.value = "View on Amazon"
            elif col_idx == 26 and val:
                cell.hyperlink = str(val)
                cell.value = "View on Zoro"

        current_row += 1

    ws2.freeze_panes = "D6"

    # ----------------------------------------------------
    # TAB 3: FULL CATALOG AUDIT
    # ----------------------------------------------------
    ws3 = wb.create_sheet(title="Full Catalog Audit")
    ws3.views.sheetView[0].showGridLines = True

    ws3.merge_cells("B2:J2")
    t3_cell = ws3["B2"]
    t3_cell.value = f"📋 {title_brand.upper()} — COMPLETE CATALOG AUDIT LOG"
    t3_cell.font = Font(name="Segoe UI", size=14, bold=True, color=WHITE)
    t3_cell.fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    t3_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws3.row_dimensions[2].height = 36

    audit_headers = [
        ("ASIN", 15),
        ("Brand", 16),
        ("Product Title", 42),
        ("Monthly Sales", 14),
        ("Current BSR", 13),
        ("Status", 15),
        ("Zoro Part #", 14),
        ("Zoro Price", 12),
        ("Audit Notes / Reason", 44),
    ]

    ws3.row_dimensions[4].height = 26
    for col_idx, (col_name, col_width) in enumerate(audit_headers, 2):
        cell = ws3.cell(row=4, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        col_letter = get_column_letter(col_idx)
        ws3.column_dimensions[col_letter].width = col_width

    current_row = 5
    for row in all_audited_rows:
        ws3.row_dimensions[current_row].height = 18
        status = row.get("match_status", "UNMATCHED")
        st_bg = "DCFCE7" if status == "MATCHED" else "F1F5F9"
        st_txt = "15803D" if status == "MATCHED" else "64748B"

        audit_data = [
            (row.get("asin"), "center", Font(name="Segoe UI", size=9, bold=True)),
            (row.get("brand"), "center", Font(name="Segoe UI", size=9)),
            (row.get("title"), "left", Font(name="Segoe UI", size=9)),
            (row.get("monthly_sales"), "center", Font(name="Segoe UI", size=9, bold=True)),
            (row.get("bsr"), "center", Font(name="Segoe UI", size=9)),
            (status, "center", Font(name="Segoe UI", size=9, bold=True, color=st_txt)),
            (row.get("zoro_item_number") or "-", "center", Font(name="Segoe UI", size=9)),
            (row.get("zoro_price"), "right", Font(name="Segoe UI", size=9)),
            (row.get("unmatch_reason"), "left", Font(name="Segoe UI", size=9)),
        ]

        for col_idx, (val, align, font_obj) in enumerate(audit_data, 2):
            cell = ws3.cell(row=current_row, column=col_idx)
            cell.value = val
            cell.alignment = Alignment(horizontal=align, vertical="center")
            cell.font = font_obj
            cell.border = thin_border
            if col_idx == 7 and status == "MATCHED":
                cell.fill = PatternFill(start_color=st_bg, end_color=st_bg, fill_type="solid")
            if col_idx == 9 and isinstance(val, (int, float)):
                cell.number_format = "$#,##0.00"
            elif col_idx in [5, 6] and isinstance(val, (int, float)):
                cell.number_format = "#,##0"

        current_row += 1

    ws3.freeze_panes = "D5"

    wb.save(output_path)
    print(f"[✓] Successfully generated workbook: {output_path}")
    print(f"    - Tab 1: Only Profitable Listings ({len(profitable_records)} rows)")
    print(f"    - Tab 2: All Matched & Analyzed ({len(matched_records)} rows)")
    print(f"    - Tab 3: Full Catalog Audit ({len(all_audited_rows)} rows)")


def main():
    print("================================================================================")
    print("  B2B BRANDS DROPSHIPPING REPORT GENERATOR — GEORGIA-PACIFIC & MULTIPLE BRANDS")
    print("================================================================================")

    # 1. Generate Georgia-Pacific specific workbook
    print("\n[*] Processing Georgia-Pacific dataset...")
    gp_matched, gp_audit = process_dataset(GEORGIA_PACIFIC_INPUT, brand_filter="Georgia-Pacific")
    create_excel_report(
        matched_records=gp_matched,
        all_audited_rows=gp_audit,
        output_path=GEORGIA_PACIFIC_OUTPUT,
        title_brand="Georgia-Pacific",
    )

    # 2. Generate Multiple Brands workbook (Georgia-Pacific, Purell, Nu-Calgon, Keson)
    print("\n[*] Processing Multiple Brands dataset (Purell, Georgia-Pacific, Nu-Calgon, Keson)...")
    mb_matched, mb_audit = process_dataset(MULTIPLE_BRANDS_INPUT, brand_filter=None)
    create_excel_report(
        matched_records=mb_matched,
        all_audited_rows=mb_audit,
        output_path=MULTIPLE_BRANDS_OUTPUT,
        title_brand="Commercial B2B Brands (GP, Purell, Nu-Calgon)",
    )

    print("\n[✓] All reports generated successfully!")


if __name__ == "__main__":
    main()
