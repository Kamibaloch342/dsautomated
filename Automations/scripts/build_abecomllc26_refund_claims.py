#!/usr/bin/env python3
"""
Build the comprehensive Zoro Undelivered / Damaged-in-Transit Refund Claims Report
for abecomllc26@gmail.com (Hathoro / DM_RESELLER / CUST20597789)
for orders placed Sep 20, 2026 – Nov 20, 2026.
"""
import os
import re
import json
import sqlite3
from datetime import datetime
from collections import defaultdict
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SCRATCH_DIR = "/Users/kamibaloch342/.gemini/antigravity/brain/7176937a-223c-4b69-9125-54858c463ac0/scratch"
UNIFIED_DB = "file:Automations/data/automations_unified.db?mode=ro"
ARCHIVE_DB = "file:Automations/data/gmail_archive.db?mode=ro&immutable=1"

ACCOUNT_INFO = {
    "account_key": "abecomllc26",
    "email": "abecomllc26@gmail.com",
    "cust_no": "CUST20597789",
    "bill_to": "Qurat Samoon / AB ECOM LLC",
    "store": "Hathoro (Discounted Maal)",
    "profile": "DM_RESELLER",
    "card_last4": "0886",
}

TODAY_STR = datetime.now().strftime("%Y-%m-%d")
# Stalled cutoff: last scan 5+ days ago (Nov 3 or earlier when today is Nov 8)
STALLED_CUTOFF_DATE = "2026-11-03"

BILL_PREFIXES = [
    "Qurat Samoon ",
    "AB ECOM LLC ",
    "AB ECOM ",
    "Hathoro ",
]
BILL_ZIPS = {"33702", "79014"}


def norm_str(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def norm_zip(z):
    m = re.search(r"(\d{5})", str(z or ""))
    return m.group(1) if m else ""


def parse_cash_refund(cr):
    """Parse raw text of Cash Refund PDF."""
    raw = cr.get("raw", "")
    ship_name = ""
    ship_street = ""
    zip5 = ""
    cash_refund_no = cr.get("cash_refund_no", "")
    if not cash_refund_no:
        m_cr = re.search(r"(?:Cash Refund|Refund #)\s+(\d{5,9})", raw)
        if m_cr:
            cash_refund_no = m_cr.group(1)

    so = cr.get("so", "")
    if not so:
        m_so = re.search(r"\b(SO\d{7,10})\b", raw)
        if m_so:
            so = m_so.group(1)

    dt_str = cr.get("date", "")
    if not dt_str:
        m_dt = re.search(r"(?:Date|Cash Refund Date)\s+(\d{1,2}/\d{1,2}/\d{4})", raw)
        if m_dt:
            dt_str = m_dt.group(1)

    amount = cr.get("amount", 0.0)
    if not amount or amount == 0.0:
        m_amt = re.search(r"Total\s*\$?([\d,]+\.\d{2})", raw)
        if m_amt:
            try:
                amount = float(m_amt.group(1).replace(",", ""))
            except Exception:
                pass

    if "Bill To Ship To" in raw:
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        try:
            idx = lines.index("Bill To Ship To")
            block = lines[idx + 1 : idx + 8]
            name_line = block[0] if block else ""
            ship_name = name_line
            for p in BILL_PREFIXES:
                if ship_name.startswith(p):
                    ship_name = ship_name[len(p) :].strip()
                    break
            else:
                words = ship_name.split()
                if len(words) >= 2 and len(words) % 2 == 0:
                    half = len(words) // 2
                    if words[:half] == words[half:]:
                        ship_name = " ".join(words[:half])
            if len(block) >= 2:
                ship_street = block[1]
            for b in block[1:]:
                matches = re.findall(r"\b([A-Z]{2})\s+(\d{5})\b", b)
                for st, z5 in reversed(matches):
                    if z5 not in BILL_ZIPS:
                        zip5 = z5
                        break
                if zip5:
                    break
        except Exception:
            pass
    else:
        m = re.search(r"Ship To\s+(.+?)\s+United States\s+Cash Refund Date", raw, re.DOTALL)
        if m:
            ship_block = m.group(1).strip()
            m_zip = re.findall(r"\b([A-Z]{2})\s+(\d{5})\b", ship_block)
            if m_zip:
                zip5 = m_zip[-1][1]
            m_name = re.match(r"^([^\d]+?)(?:\s+\d+|\s+PO\s+BOX|\s+P\.?O\.?\s+BOX)", ship_block, re.IGNORECASE)
            if m_name:
                ship_name = m_name.group(1).strip()
            else:
                ship_name = " ".join(ship_block.split()[:2])

    # Fallback zip search
    if not zip5:
        all_zips = re.findall(r"\b([A-Z]{2})\s*(\d{5})(?:\d{4})?\b", raw)
        for st, z5 in reversed(all_zips):
            if z5 not in BILL_ZIPS:
                zip5 = z5
                break

    if not ship_name:
        for ln in raw.splitlines():
            if "Qurat Samoon" in ln:
                ship_name = ln.replace("Qurat Samoon", "").strip()
                break
            elif "Ship To" in ln and not ship_name:
                cleaned = ln.replace("Ship To", "").replace("Bill To", "").strip()
                if cleaned and not any(p in cleaned for p in ["Qurat", "AB ECOM", "United States"]):
                    ship_name = cleaned
                    break

    skus = re.findall(r"\b(G\d{7,10})\b", raw)
    dt_iso = ""
    if dt_str:
        try:
            dt_iso = datetime.strptime(dt_str, "%m/%d/%Y").strftime("%Y-%m-%d")
        except Exception:
            dt_iso = dt_str

    return {
        "acct": "abecomllc26",
        "cash_refund_no": cash_refund_no,
        "so": so,
        "date": dt_str,
        "date_iso": dt_iso,
        "amount": amount,
        "ship_name": ship_name,
        "ship_name_norm": norm_str(ship_name),
        "ship_street": ship_street,
        "ship_street_norm": norm_str(ship_street),
        "zip5": zip5,
        "skus": list(dict.fromkeys(skus)),
        "raw": raw,
    }


def is_terminal_exception_or_damaged(text):
    t = (text or "").lower()
    keywords = [
        "damage",
        "returning to sender",
        "return to sender",
        "returning to shipper",
        "returned to shipper",
        "returned to sender",
        "undeliverable",
        "refused",
        "unable to deliver",
        "lost",
        "missing",
        "destroyed",
        "disposed",
        "improperly packaged",
    ]
    return any(k in t for k in keywords)


_SCAN_DATE_RE = re.compile(r" on (\d{4}-\d{2}-\d{2})")


def run_analysis():
    print(f"=== Starting Refund Claims Analysis for {ACCOUNT_INFO['email']} ===")
    
    # 1. Load Cash Refunds
    crs_to_load = []
    # Load abecomllc26_cash_refunds.json
    abe_cr_path = os.path.join(SCRATCH_DIR, "abecomllc26_cash_refunds.json")
    if os.path.exists(abe_cr_path):
        with open(abe_cr_path) as f:
            crs_to_load.extend(json.load(f))
            
    # Load mkamrankhan_cash_refunds_nov.json
    m_cr_path = os.path.join(SCRATCH_DIR, "mkamrankhan_cash_refunds_nov.json")
    if os.path.exists(m_cr_path):
        with open(m_cr_path) as f:
            for c in json.load(f):
                raw = c.get("raw", "")
                if "0886" in raw or "20597789" in raw or "AB ECOM" in raw:
                    crs_to_load.append(c)
                    
    # Load cash_refunds_since_sep20.json
    all_cr_path = os.path.join(SCRATCH_DIR, "cash_refunds_since_sep20.json")
    if os.path.exists(all_cr_path):
        with open(all_cr_path) as f:
            for c in json.load(f):
                raw = c.get("raw", "")
                if "0886" in raw or "20597789" in raw or "AB ECOM" in raw:
                    crs_to_load.append(c)

    parsed_crs = [parse_cash_refund(c) for c in crs_to_load]
    # Deduplicate by cash_refund_no
    cr_map = {}
    for cr in parsed_crs:
        cno = cr.get("cash_refund_no")
        if cno:
            cr_map[cno] = cr
        else:
            cr_map[f"so_{cr.get('so')}_{cr.get('amount')}"] = cr
    unique_crs = list(cr_map.values())
    print(f"Loaded {len(unique_crs)} unique Cash Refunds for abecomllc26.")

    # Index Cash Refunds
    cr_by_zip_name = defaultdict(list)
    cr_by_zip = defaultdict(list)
    for cr in unique_crs:
        if cr["zip5"] and cr["ship_name_norm"]:
            cr_by_zip_name[(cr["zip5"], cr["ship_name_norm"])].append(cr)
        if cr["zip5"]:
            cr_by_zip[cr["zip5"]].append(cr)

    # 2. Database connections
    uconn = sqlite3.connect(UNIFIED_DB, uri=True)
    uconn.row_factory = sqlite3.Row
    aconn = sqlite3.connect(ARCHIVE_DB, uri=True)
    aconn.row_factory = sqlite3.Row

    # zoro_emails lookup
    zoro_emails_map = {}
    for r in aconn.execute("SELECT * FROM zoro_emails WHERE email_timestamp >= '2026-09-15'"):
        zoro_emails_map[r["wb_order_id"]] = dict(r)

    # archive_zoro lookup
    archive_zoro_map = {}
    for r in uconn.execute("SELECT * FROM archive_zoro WHERE email_timestamp >= '2026-09-15'"):
        archive_zoro_map[r["wb_order_id"]] = dict(r)

    # archive_tracking lookup
    trk_by_wb = defaultdict(lambda: {"tids": [], "accts": [], "row": None})
    for r in uconn.execute("SELECT * FROM archive_tracking WHERE email_timestamp >= '2026-09-15'"):
        wb = r["wb_order_id"]
        if not wb:
            continue
        entry = trk_by_wb[wb]
        if entry["row"] is None:
            entry["row"] = dict(r)
        ga = r["gmail_account"]
        if ga and ga not in entry["accts"]:
            entry["accts"].append(ga)
        for t in (r["tracking_ids"] or "").replace(";", ",").split(","):
            t = t.strip()
            if t and t not in entry["tids"]:
                entry["tids"].append(t)

    # zoro_package_tracking lookup
    pkg_by_wb = defaultdict(list)
    for r in uconn.execute("SELECT * FROM zoro_package_tracking"):
        pkg_by_wb[r["wb_order_id"]].append(dict(r))

    # Load closing_orders placed >= 2026-09-20
    closing_rows = uconn.execute("""
        SELECT * FROM closing_orders
        WHERE zoro_email_ts >= '2026-09-20'
          AND COALESCE(wb_order_id, '') LIKE 'WB%'
        ORDER BY zoro_email_ts DESC
    """).fetchall()

    # Load placer_orders
    placer_rows = uconn.execute("""
        SELECT * FROM order_placer_orders
        WHERE placed_at >= '2026-09-20'
          AND COALESCE(wb_order_id, '') LIKE 'WB%'
        ORDER BY placed_at DESC
    """).fetchall()
    placer_by_wb = {r["wb_order_id"]: dict(r) for r in placer_rows}

    # Gather all candidate WB orders for abecomllc26
    candidates = {}
    for crow in closing_rows:
        c = dict(crow)
        wb = c["wb_order_id"]
        zg = (c.get("zoro_gmail_account") or "").strip()
        prof = (c.get("profile_name") or "").strip()
        card = (c.get("zoro_payment_card") or "").strip()
        acct_nm = (c.get("zoro_account_name") or "").strip()
        
        # Check if abecomllc26
        if zg == "abecomllc26" or prof == "DM_RESELLER" or card == "0886" or acct_nm == "abecomllc26":
            candidates[wb] = c

    for r in placer_rows:
        wb = r["wb_order_id"]
        prof = (r["profile_name"] or "").strip()
        if prof == "DM_RESELLER" and wb not in candidates:
            # Create synthetic closing-like dict from placer_order
            candidates[wb] = {
                "wb_order_id": wb,
                "amazon_order_id": r["amazon_order_id"],
                "account": r["account"],
                "profile_name": r["profile_name"],
                "asin": r["asin"],
                "sku": r["sku"],
                "title": r["title"],
                "quantity": r["quantity"],
                "ship_name": r["ship_name"],
                "ship_street": r["ship_street"],
                "ship_city": r["ship_city"],
                "ship_state": r["ship_state"],
                "ship_postal_code": r["ship_postal_code"],
                "ship_country": r["ship_country"],
                "ship_phone": r["ship_phone"],
                "purchase_date": r["purchase_date"],
                "ship_by_date": r["ship_by_date"],
                "deliver_by_date": r["deliver_by_date"],
                "zoro_order_total": r["zoro_order_total"],
                "zoro_tax": r["zoro_tax"],
                "zoro_subtotal": r["zoro_subtotal"],
                "zoro_shipping": r["zoro_shipping"],
                "zoro_sku": r["main_sku"] or r["sku"],
                "zoro_qty": r["zoro_qty"] or r["quantity"],
                "tracking_ids": r["tracking_ids"],
                "tracking_id_uploaded": r["tracking_id_uploaded"],
                "tracking_carrier_uploaded": r["tracking_carrier_uploaded"],
                "zoro_email_ts": r["placed_at"],
                "delivery_status": None,
                "delivery_date": None,
                "delivery_note": None,
                "last_scan_location": None,
                "last_scan_time": None,
                "zoro_gmail_account": "abecomllc26",
                "zoro_account_name": "abecomllc26",
                "zoro_payment_card": "0886",
            }

    for wb, az in archive_zoro_map.items():
        ga = (az.get("gmail_account") or "").strip()
        card = (az.get("payment_card_last4") or "").strip()
        if (ga == "abecomllc26" or card == "0886") and wb not in candidates:
            candidates[wb] = {
                "wb_order_id": wb,
                "amazon_order_id": "",
                "account": "Hathoro",
                "profile_name": "DM_RESELLER",
                "asin": "",
                "sku": az.get("sku"),
                "title": az.get("zoro_item_desc"),
                "quantity": az.get("qty"),
                "ship_name": az.get("ship_name"),
                "ship_street": az.get("ship_street"),
                "ship_city": az.get("ship_city"),
                "ship_state": az.get("ship_state"),
                "ship_postal_code": az.get("ship_zip"),
                "ship_country": "US",
                "ship_phone": "",
                "purchase_date": az.get("order_date"),
                "ship_by_date": "",
                "deliver_by_date": "",
                "zoro_order_total": az.get("order_total"),
                "zoro_tax": az.get("tax"),
                "zoro_subtotal": az.get("subtotal"),
                "zoro_shipping": az.get("shipping_cost"),
                "zoro_sku": az.get("sku"),
                "zoro_qty": az.get("qty"),
                "tracking_ids": "",
                "tracking_id_uploaded": "",
                "tracking_carrier_uploaded": "",
                "zoro_email_ts": az.get("email_timestamp"),
                "delivery_status": None,
                "delivery_date": None,
                "delivery_note": None,
                "last_scan_location": None,
                "last_scan_time": None,
                "zoro_gmail_account": "abecomllc26",
                "zoro_account_name": "abecomllc26",
                "zoro_payment_card": "0886",
            }

    print(f"Total candidate orders for abecomllc26: {len(candidates)}")

    all_orders = []
    for wb, c in candidates.items():
        ze = zoro_emails_map.get(wb, {})
        az = archive_zoro_map.get(wb, {})
        tk_info = trk_by_wb.get(wb, {"tids": [], "accts": [], "row": None})
        tk_row = tk_info["row"] or {}
        pl = placer_by_wb.get(wb, {})

        # Address fields
        ship_name = c.get("ship_name") or ze.get("ship_name") or az.get("ship_name") or tk_row.get("ship_to_name") or pl.get("ship_name") or ""
        ship_street = c.get("ship_street") or ze.get("ship_street") or az.get("ship_street") or tk_row.get("ship_to_street") or pl.get("ship_street") or ""
        ship_city = c.get("ship_city") or ze.get("ship_city") or az.get("ship_city") or tk_row.get("ship_to_city") or pl.get("ship_city") or ""
        ship_state = c.get("ship_state") or ze.get("ship_state") or az.get("ship_state") or tk_row.get("ship_to_state") or pl.get("ship_state") or ""
        ship_zip = c.get("ship_postal_code") or ze.get("ship_zip") or az.get("ship_zip") or tk_row.get("ship_to_zip") or c.get("zoro_buyer_zip") or pl.get("ship_postal_code") or ""
        zip5 = norm_zip(ship_zip)

        # Financial fields
        zoro_total = c.get("zoro_order_total")
        if zoro_total is None or zoro_total == 0.0:
            zoro_total = ze.get("order_total") or az.get("order_total") or pl.get("zoro_order_total") or 0.0
        zoro_subtotal = c.get("zoro_subtotal")
        if zoro_subtotal is None or zoro_subtotal == 0.0:
            zoro_subtotal = ze.get("subtotal") or az.get("subtotal") or pl.get("zoro_subtotal") or 0.0
        zoro_tax = c.get("zoro_tax")
        if zoro_tax is None:
            zoro_tax = ze.get("tax") or az.get("tax") or pl.get("zoro_tax") or 0.0
        zoro_shipping = c.get("zoro_shipping")
        if zoro_shipping is None:
            zoro_shipping = ze.get("shipping_cost") or az.get("shipping_cost") or pl.get("zoro_shipping") or 0.0

        zoro_sku = c.get("zoro_sku") or ze.get("sku") or az.get("sku") or tk_row.get("item_skus") or c.get("sku") or pl.get("sku") or ""
        zoro_qty = c.get("zoro_qty") or ze.get("qty") or az.get("qty") or c.get("quantity") or pl.get("quantity") or 1
        item_desc = c.get("zoro_item_desc") or ze.get("zoro_item_desc") or az.get("zoro_item_desc") or tk_row.get("item_names") or c.get("title") or pl.get("title") or ""

        # Package tracking
        pkgs = list(pkg_by_wb.get(wb, []))
        pkg_map = {str(p.get("tracking_number") or "").strip(): p for p in pkgs if p.get("tracking_number")}

        zoro_tids = list(tk_info["tids"])
        for t in (c.get("tracking_ids") or "").replace(";", ",").split(","):
            t = t.strip()
            if not t or t in zoro_tids:
                continue
            p = pkg_map.get(t)
            if p:
                pst = p.get("status") or ""
                pdt = (p.get("delivered_at") or p.get("last_event_at") or p.get("shipped_at") or "")[:10]
                if pst in ("Untracked", "Unknown"):
                    continue
                if pdt and pdt < "2026-09-01":
                    continue
                zoro_tids.append(t)
            elif not tk_info["tids"] and t != (c.get("tracking_id_uploaded") or "").strip():
                zoro_tids.append(t)

        for p in pkgs:
            tn = str(p.get("tracking_number") or "").strip()
            pst = p.get("status") or ""
            pdt = (p.get("delivered_at") or p.get("last_event_at") or p.get("shipped_at") or "")[:10]
            if tn and tn not in zoro_tids and pst not in ("Untracked", "Unknown") and (not pdt or pdt >= "2026-09-01"):
                zoro_tids.append(tn)

        ds = c.get("delivery_status") or ""
        dnote = c.get("delivery_note") or ""
        last_loc = c.get("last_scan_location") or ""
        last_time = c.get("last_scan_time") or ""
        deliv_date = c.get("delivery_date") or ""

        delivered_tids = []
        undelivered_tids = []
        undelivered_details = []
        undelivered_scan_dates = []
        undelivered_statuses = []

        if zoro_tids:
            for tid in zoro_tids:
                p = pkg_map.get(tid)
                if p:
                    pst = p.get("status") or "Unknown"
                    ple = p.get("last_event") or ""
                    plex = p.get("last_exception") or ""
                    ploc = p.get("last_location") or ""
                    pdt = (p.get("delivered_at") or p.get("last_event_at") or p.get("shipped_at") or "")[:10]
                    detail_str = f"{tid}: {pst}"
                    if plex and plex != ple:
                        detail_str += f" ({ple} | {plex})"
                    elif ple:
                        detail_str += f" ({ple})"
                    if ploc:
                        detail_str += f" [{ploc}]"
                    if pdt:
                        detail_str += f" on {pdt}"
                    if pst in ("Delivered", "AllDelivered"):
                        delivered_tids.append(tid)
                    else:
                        undelivered_tids.append(tid)
                        undelivered_details.append(detail_str)
                        undelivered_statuses.append(pst)
                        if pdt:
                            undelivered_scan_dates.append(pdt)
                else:
                    if ds in ("Delivered", "AllDelivered"):
                        delivered_tids.append(tid)
                    else:
                        undelivered_tids.append(tid)
                        st_label = ds or "Unconfirmed"
                        undelivered_statuses.append(st_label)
                        note_part = f" ({dnote})" if dnote else ""
                        loc_part = f" [{last_loc}]" if last_loc else ""
                        dt_part = f" on {last_time[:10]}" if last_time else ""
                        d_str = f"{tid}: {st_label}{note_part}{loc_part}{dt_part}"
                        undelivered_details.append(d_str)
                        if last_time:
                            undelivered_scan_dates.append(last_time[:10])

        if zoro_tids and delivered_tids and not undelivered_tids:
            ds = "AllDelivered" if len(zoro_tids) > 1 else "Delivered"

        # Match against Cash Refunds
        zoro_order_dt = (c.get("zoro_email_ts") or "")[:10]
        sname_norm = norm_str(ship_name)
        matched_crs = []
        if zip5 and sname_norm and (zip5, sname_norm) in cr_by_zip_name:
            for cr in cr_by_zip_name[(zip5, sname_norm)]:
                if cr["date_iso"] and zoro_order_dt and cr["date_iso"] < zoro_order_dt:
                    continue
                matched_crs.append(cr)

        db_ref_status = (c.get("zoro_refund_status") or "").strip()
        db_ref_amt = float(c.get("zoro_refund_amount") or 0.0)
        zoro_case = (c.get("zoro_case_number") or "").strip()
        zoro_ra = (c.get("zoro_ra_number") or "").strip()

        is_already_refunded = False
        refund_source_note = ""
        refund_amt = 0.0
        refund_dt = ""
        refund_doc = ""
        refund_so = ""

        if matched_crs:
            is_already_refunded = True
            refund_amt = sum(x["amount"] for x in matched_crs)
            refund_dt = ", ".join(dict.fromkeys(x["date"] for x in matched_crs if x["date"]))
            refund_doc = ", ".join(dict.fromkeys(x["cash_refund_no"] for x in matched_crs if x["cash_refund_no"]))
            refund_so = ", ".join(dict.fromkeys(x["so"] for x in matched_crs if x["so"]))
            refund_source_note = f"Cash Refund PDF #{refund_doc} ({refund_so} on {refund_dt}: ${refund_amt:.2f})"
        elif db_ref_amt > 0 or db_ref_status.lower() in ("refunded", "completed", "done", "issued", "credited"):
            is_already_refunded = True
            refund_amt = db_ref_amt
            refund_doc = zoro_ra or zoro_case or db_ref_status
            refund_source_note = f"DB Recorded Refund ({db_ref_status}: ${db_ref_amt:.2f})"

        latest_undeliv_scan = max(undelivered_scan_dates) if undelivered_scan_dates else (last_time[:10] if last_time else "")

        order_dict = {
            "wb_order_id": wb,
            "amazon_order_id": c.get("amazon_order_id") or "",
            "zoro_po_number": c.get("zoro_po_number") or ze.get("po_number") or az.get("po_number") or "",
            "amazon_account": c.get("account") or "Hathoro",
            "profile_name": c.get("profile_name") or "DM_RESELLER",
            "true_acct": "abecomllc26",
            "zoro_email": ACCOUNT_INFO["email"],
            "cust_no": ACCOUNT_INFO["cust_no"],
            "zoro_order_date": zoro_order_dt,
            "zoro_email_ts": c.get("zoro_email_ts") or "",
            "purchase_date": (c.get("purchase_date") or "")[:10],
            "ship_by_date": (c.get("ship_by_date") or ""),
            "deliver_by_date": (c.get("deliver_by_date") or ""),
            "zoro_order_total": round(float(zoro_total or 0.0), 2),
            "zoro_subtotal": round(float(zoro_subtotal or 0.0), 2),
            "zoro_tax": round(float(zoro_tax or 0.0), 2),
            "zoro_shipping": round(float(zoro_shipping or 0.0), 2),
            "zoro_sku": zoro_sku,
            "zoro_qty": zoro_qty,
            "item_desc": item_desc,
            "ship_name": ship_name,
            "ship_street": ship_street,
            "ship_city": ship_city,
            "ship_state": ship_state,
            "ship_zip": ship_zip,
            "ship_phone": c.get("ship_phone") or pl.get("ship_phone") or "",
            "all_tids": zoro_tids,
            "delivered_tids": delivered_tids,
            "undelivered_tids": undelivered_tids,
            "undelivered_details": undelivered_details,
            "latest_undeliv_scan": latest_undeliv_scan,
            "delivery_status": ds,
            "delivery_note": dnote,
            "last_scan_location": last_loc,
            "last_scan_time": last_time,
            "delivery_date": deliv_date,
            "is_already_refunded": is_already_refunded,
            "refund_source_note": refund_source_note,
            "refund_amt": refund_amt,
            "refund_dt": refund_dt,
            "refund_doc": refund_doc,
            "refund_so": refund_so,
            "carrier": c.get("carrier") or c.get("tracking_carrier_uploaded") or ("FedEx" if zoro_tids and len(zoro_tids[0]) == 12 else "Carrier"),
        }

        # Classification
        notes_all = (dnote + " " + " ".join(undelivered_details)).lower()
        has_damage = "damage" in notes_all or "improperly packaged" in notes_all
        has_return = any(k in notes_all for k in ["returning to sender", "return to sender", "returning to shipper", "returned to shipper", "returned to sender"])
        has_exception = any(k in notes_all for k in ["undeliverable", "refused", "unable to deliver", "delivery exception", "incorrect address", "barcode unreadable", "customer not available", "closed"])

        category = "Other"
        is_actionable = False

        if is_already_refunded:
            category = "Already Refunded by Zoro"
        elif not zoro_tids and not ds:
            category = "Freight / Untracked / Cancelled"
        elif ds in ("Delivered", "AllDelivered") and not undelivered_tids:
            category = "Delivered (Complete)"
        elif has_damage:
            category = "1A. Damaged in transit"
            is_actionable = True
        elif has_return:
            category = "1B. Returned / returning to sender"
            is_actionable = True
        elif has_exception and ((latest_undeliv_scan and latest_undeliv_scan <= STALLED_CUTOFF_DATE) or (not latest_undeliv_scan and zoro_order_dt <= STALLED_CUTOFF_DATE) or is_terminal_exception_or_damaged(notes_all)):
            category = "1C. Carrier exception (unable to deliver)"
            is_actionable = True
        elif delivered_tids and undelivered_tids:
            # Multi-box partial delivery
            category = "2. Partial delivery (box missing or damaged)"
            is_actionable = True
        elif all(s in ("LabelCreated",) for s in undelivered_statuses) and ((latest_undeliv_scan and latest_undeliv_scan <= STALLED_CUTOFF_DATE) or (not latest_undeliv_scan and zoro_order_dt <= STALLED_CUTOFF_DATE)):
            category = "3. Ghost label (never picked up by carrier)"
            is_actionable = True
        elif undelivered_tids and ((latest_undeliv_scan and latest_undeliv_scan <= STALLED_CUTOFF_DATE) or (not latest_undeliv_scan and zoro_order_dt <= STALLED_CUTOFF_DATE)):
            category = "4. Lost or stalled in transit"
            is_actionable = True
        else:
            category = "In Transit (Active Window)"

        order_dict["category"] = category
        order_dict["is_actionable"] = is_actionable

        # Claim calculation
        n_all = len(zoro_tids) or 1
        n_claim = len(undelivered_tids) if is_actionable else 0
        total = order_dict["zoro_order_total"] or 0.0
        if n_claim >= n_all:
            est_claim = total
        elif n_claim > 0:
            est_claim = round(total * n_claim / n_all, 2)
        else:
            est_claim = 0.0
        order_dict["est_claim_value"] = est_claim

        all_orders.append(order_dict)

    # Print summary
    cat_counts = Counter([o["category"] for o in all_orders])
    print("\n=== SUMMARY BY CATEGORY ===")
    for cat, count in sorted(cat_counts.items(), key=lambda x: str(x[0])):
        total_val = sum(o["zoro_order_total"] for o in all_orders if o["category"] == cat)
        claim_val = sum(o["est_claim_value"] for o in all_orders if o["category"] == cat)
        print(f"  {cat:45s}: {count:5d} orders | Order Total: ${total_val:10.2f} | Est Claim: ${claim_val:10.2f}")

    actionable = [o for o in all_orders if o["is_actionable"]]
    print(f"\nTotal Actionable Unrefunded Claims: {len(actionable)} orders")
    print(f"Total Order Value: ${sum(o['zoro_order_total'] for o in actionable):,.2f}")
    print(f"Total Est. Claim Value: ${sum(o['est_claim_value'] for o in actionable):,.2f}")

    # Save to scratch
    out_json = os.path.join(SCRATCH_DIR, "abecomllc26_refund_claims_compiled.json")
    with open(out_json, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "account": ACCOUNT_INFO,
            "total_orders": len(all_orders),
            "actionable_count": len(actionable),
            "actionable_order_total": sum(o["zoro_order_total"] for o in actionable),
            "actionable_claim_total": sum(o["est_claim_value"] for o in actionable),
            "actionable_orders": actionable,
            "all_orders": all_orders,
        }, f, indent=2)
    print(f"Saved compiled data to {out_json}")


if __name__ == "__main__":
    from collections import Counter
    run_analysis()
