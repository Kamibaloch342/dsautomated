#!/usr/bin/env python3
"""
generate_undelivered_orders_excel.py
Generates comprehensive, executive-grade multi-account and per-account Excel workbooks
reconciling all undelivered and partial delivered Zoro orders using the latest, authoritative
delivery tracking details (Zoro order tracking sweep + FedEx carrier scan events) across:
- Inhur LLC (inuhrecomllc5@gmail.com)
- Asad Amazon US (asad.amazon.us@gmail.com)
- Salesmercentile 1 (salesmercentile1@gmail.com)

Features:
- Executive Summary with cross-account KPIs and financial exposure
- Master Actionable Claims tab (Partial Deliveries, Carrier Exceptions & Ghost Labels)
- Dedicated tabs per account with parcel counts, scan history, exception notes, and earnings
- Individual standalone workbooks per account for distribution to team leads / VAs
"""

import os
import sys
import sqlite3
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(AUTO_DIR, "data")

DB_PATH_MASTER = os.path.join(DATA_DIR, "automations_unified.db")
DB_PATH_SLIM = os.path.join(DATA_DIR, "dashboard_slim.db")

OUTPUT_COMBINED_XLSX = os.path.join(DATA_DIR, "Undelivered_Orders_Asad_Inuhr_Salesmercentile1_2026.xlsx")
OUTPUT_LEGACY_XLSX = os.path.join(DATA_DIR, "Zoro_Undelivered_Orders_Reconciliation_2026.xlsx")

# Styling Definitions
NAVY_HEADER = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid")
HEADER_FONT = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
TITLE_FONT = Font(name="Segoe UI", size=15, bold=True, color="1B365D")
SUBTITLE_FONT = Font(name="Segoe UI", size=9, italic=True, color="475569")
BOLD_FONT = Font(name="Segoe UI", size=9, bold=True, color="0F172A")
REGULAR_FONT = Font(name="Segoe UI", size=9, color="1E293B")
MONO_FONT = Font(name="Consolas", size=9, color="0F172A")

BORDER_THIN = Border(
    left=Side(style="thin", color="E2E8F0"),
    right=Side(style="thin", color="E2E8F0"),
    top=Side(style="thin", color="E2E8F0"),
    bottom=Side(style="thin", color="E2E8F0")
)
BORDER_TOP_BOTTOM = Border(
    top=Side(style="thin", color="CBD5E1"),
    bottom=Side(style="double", color="0F172A")
)

ZEBRA_FILL = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
WHITE_FILL = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

# Status badge fills & fonts
STATUS_STYLES = {
    "PartialDelivered": (
        PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="92400E")
    ),
    "DeliveryException": (
        PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="991B1B")
    ),
    "Delivery exception": (
        PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="991B1B")
    ),
    "Shipment exception": (
        PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="991B1B")
    ),
    "Returning to FedEx facility": (
        PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="991B1B")
    ),
    "Cancelled": (
        PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="7F1D1D")
    ),
    "InTransit": (
        PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="1E40AF")
    ),
    "On the way": (
        PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="1E40AF")
    ),
    "Delivery updated": (
        PatternFill(start_color="E0E7FF", end_color="E0E7FF", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="3730A3")
    ),
    "We have your package": (
        PatternFill(start_color="E0F2FE", end_color="E0F2FE", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="0369A1")
    ),
    "OutForDelivery": (
        PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="166534")
    ),
    "Out for delivery": (
        PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="166534")
    ),
    "We tried to deliver": (
        PatternFill(start_color="FFEDD5", end_color="FFEDD5", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="9A3412")
    ),
    "LabelCreated": (
        PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="475569")
    ),
    "Ready for pickup": (
        PatternFill(start_color="E0E7FF", end_color="E0E7FF", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="3730A3")
    )
}

DEFAULT_BADGE = (
    PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid"),
    Font(name="Segoe UI", size=9, bold=True, color="334155")
)

TARGET_ACCOUNTS = [
    ("inhur_llc", "Inhur LLC (inuhr)", "inuhrecomllc5@gmail.com", "Child Account"),
    ("asad_amazon_us", "Asad Amazon US (asad)", "asad.amazon.us@gmail.com", "Sister Account"),
    ("salesmercentile1", "Salesmercentile 1", "salesmercentile1@gmail.com", "Secondary Pool")
]

ALL_ACCOUNTS = [
    ("inhur_llc", "Inhur LLC (inuhr)", "inuhrecomllc5@gmail.com", "Child Account"),
    ("asad_amazon_us", "Asad Amazon US", "asad.amazon.us@gmail.com", "Sister Account"),
    ("salesmercentile", "Salesmercentile", "salesmercentile@gmail.com", "Primary Zoro Pool"),
    ("abecomllc26", "AB Ecom LLC (DM)", "abecomllc26@gmail.com", "Hathoro Reseller"),
    ("wrenchlyllc", "Wrenchly LLC", "wrenchlyllc@gmail.com", "Wrenchly Account"),
    ("salesmercentile1", "Salesmercentile 1", "salesmercentile1@gmail.com", "Secondary Pool")
]

OTHER_ACCOUNT = ("other_legacy", "Other & Legacy Pools", "abecomllc02 / Unassigned", "Legacy & Placer")

def parse_tids(raw_str):
    if not raw_str:
        return []
    parts = str(raw_str).replace(";", ",").replace("\n", ",").split(",")
    clean = []
    for p in parts:
        s = p.strip()
        if s and s not in clean:
            clean.append(s)
    return clean

def get_db_connection():
    target_path = DB_PATH_MASTER if os.path.exists(DB_PATH_MASTER) else DB_PATH_SLIM
    conn = sqlite3.connect(f"file:{target_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_package_tracking_map(conn):
    """Map wb_order_id -> list of package records from zoro_package_tracking."""
    pkgs_map = {}
    try:
        rows = conn.execute("""
            SELECT wb_order_id, tracking_number, carrier, status, 
                   delivered_at, shipped_at, last_event, last_event_at, 
                   last_exception, last_location, last_movement_at, url
            FROM zoro_package_tracking
        """).fetchall()
        for r in rows:
            wb = r["wb_order_id"]
            if wb not in pkgs_map:
                pkgs_map[wb] = []
            pkgs_map[wb].append(dict(r))
    except Exception as e:
        print(f"  ⚠️ Warning reading zoro_package_tracking: {e}")
    return pkgs_map

def fetch_undelivered_orders(conn, target_keys=None):
    """Fetch all undelivered/partial delivered orders from closing_orders."""
    cur = conn.cursor()

    where_clauses = [
        "delivery_status NOT IN ('Delivered', 'AllDelivered')",
        "delivery_status IS NOT NULL",
        "delivery_status != ''",
        "COALESCE(manual_delivered, '') != 'Yes'",
        "tracking_ids IS NOT NULL",
        "tracking_ids != ''"
    ]
    params = []

    if target_keys:
        ph = ",".join(["?"] * len(target_keys))
        where_clauses.append(f"zoro_gmail_account IN ({ph})")
        params.extend(target_keys)

    where_sql = " AND ".join(where_clauses)
    query = f"""
        SELECT 
            amazon_order_id,
            wb_order_id,
            COALESCE(zoro_gmail_account, 'unassigned') as zoro_gmail_account,
            account,
            purchase_date,
            deliver_by_date,
            delivery_status,
            tracking_ids,
            quantity,
            sku,
            title,
            ship_name,
            ship_city,
            ship_state,
            ship_postal_code,
            zoro_order_total,
            earnings,
            profit_dollars,
            last_scan_location,
            last_scan_time,
            delivery_note,
            carrier,
            zoro_order_status
        FROM closing_orders
        WHERE {where_sql}
        ORDER BY purchase_date DESC
    """
    rows = cur.execute(query, params).fetchall()
    cols = [d[0] for d in cur.description]

    pkgs_map = fetch_package_tracking_map(conn)

    records = []
    for r in rows:
        d = dict(zip(cols, r))
        wb = d.get("wb_order_id")
        tids = parse_tids(d.get("tracking_ids"))
        d["clean_tids"] = tids
        d["pkg_count"] = len(tids)

        # Enrich from zoro_package_tracking if available
        p_list = pkgs_map.get(wb, [])
        status = d.get("delivery_status") or "Unknown"

        if p_list:
            total_pkgs = len(p_list)
            deliv_pkgs = sum(1 for p in p_list if p.get("status") == "Delivered")
            d["pkg_count"] = max(total_pkgs, len(tids))
            
            if status == "PartialDelivered":
                d["pkg_ratio_text"] = f"Partial Delivery: {deliv_pkgs} of {total_pkgs} pkgs delivered"
            else:
                d["pkg_ratio_text"] = f"All {total_pkgs} pkgs undelivered ({status})" if total_pkgs > 1 else f"1 of 1 pkg undelivered ({status})"

            exceptions = [p["last_exception"] for p in p_list if p.get("last_exception")]
            if exceptions and not d.get("delivery_note"):
                d["delivery_note"] = "; ".join(set(exceptions))

            # Pick latest scan location and time if missing
            if not d.get("last_scan_location"):
                locs = [p["last_location"] for p in p_list if p.get("last_location")]
                if locs:
                    d["last_scan_location"] = locs[0]

            if not d.get("last_scan_time"):
                movs = [p["last_movement_at"] or p["last_event_at"] for p in p_list if (p.get("last_movement_at") or p.get("last_event_at"))]
                if movs:
                    d["last_scan_time"] = movs[0]
        else:
            if status == "PartialDelivered":
                if len(tids) > 1:
                    d["pkg_ratio_text"] = f"Partial Delivery: At least 1 of {len(tids)} pkgs undelivered"
                else:
                    d["pkg_ratio_text"] = "Partial Delivery: Multi-piece split / partial scan"
            else:
                if len(tids) > 1:
                    d["pkg_ratio_text"] = f"All {len(tids)} pkgs undelivered ({status})"
                elif len(tids) == 1:
                    d["pkg_ratio_text"] = f"1 of 1 pkg undelivered ({status})"
                else:
                    d["pkg_ratio_text"] = f"Undelivered ({status})"

        records.append(d)

    return records

def populate_orders_sheet(ws, orders, title_text, subtitle_text):
    # Title
    ws.merge_cells("A1:R1")
    ws["A1"] = title_text
    ws["A1"].font = TITLE_FONT
    ws["A1"].alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 28

    ws.merge_cells("A2:R2")
    ws["A2"] = subtitle_text
    ws["A2"].font = SUBTITLE_FONT
    ws["A2"].alignment = Alignment(vertical="center")
    ws.row_dimensions[2].height = 18

    # Headers
    headers = [
        ("Amazon Order ID", 22, Alignment(horizontal="center")),
        ("WB Order ID", 15, Alignment(horizontal="center")),
        ("Store", 10, Alignment(horizontal="center")),
        ("Gmail Account", 18, Alignment(horizontal="center")),
        ("Purchase Date", 14, Alignment(horizontal="center")),
        ("Deliver By", 14, Alignment(horizontal="center")),
        ("Delivery Status", 20, Alignment(horizontal="center")),
        ("Package Breakdown", 32, Alignment(horizontal="left")),
        ("Total Pkgs", 11, Alignment(horizontal="center")),
        ("Tracking Numbers", 36, Alignment(horizontal="left")),
        ("Qty", 8, Alignment(horizontal="center")),
        ("Zoro Cost ($)", 14, Alignment(horizontal="right")),
        ("Amazon Earnings ($)", 15, Alignment(horizontal="right")),
        ("Buyer Name", 20, Alignment(horizontal="left")),
        ("Destination City, State", 24, Alignment(horizontal="left")),
        ("Carrier", 14, Alignment(horizontal="center")),
        ("Last Scan Location", 22, Alignment(horizontal="left")),
        ("Last Scan Time / Event", 18, Alignment(horizontal="center")),
        ("Carrier Exception / Note", 42, Alignment(horizontal="left"))
    ]

    header_row = 4
    ws.row_dimensions[header_row].height = 26
    for col_idx, (h_name, width, align) in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=h_name)
        cell.font = HEADER_FONT
        cell.fill = NAVY_HEADER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = width

    if not orders:
        ws.cell(row=header_row + 1, column=1, value="No undelivered orders found for this account (All orders confirmed delivered).").font = SUBTITLE_FONT
        ws.row_dimensions[header_row + 1].height = 24
        return

    # Data Rows
    for r_idx, o in enumerate(orders, header_row + 1):
        ws.row_dimensions[r_idx].height = 20
        fill = ZEBRA_FILL if (r_idx % 2 == 0) else WHITE_FILL

        tids_str = ", ".join(o["clean_tids"])
        p_date = str(o.get("purchase_date") or "")[:10]
        d_date = str(o.get("deliver_by_date") or "").split("|")[0].strip()[:10]
        cost = float(o.get("zoro_order_total") or 0.0)
        earn = float(o.get("earnings") or 0.0)
        status = o.get("delivery_status") or "Unknown"

        loc = o.get("last_scan_location") or ""
        scan_time = str(o.get("last_scan_time") or "")[:16]
        note = o.get("delivery_note") or ""
        carrier = o.get("carrier") or "FedEx"
        buyer_dest = f"{o.get('ship_city') or ''}, {o.get('ship_state') or ''} {o.get('ship_postal_code') or ''}".strip()

        row_vals = [
            o.get("amazon_order_id"),
            o.get("wb_order_id"),
            o.get("account"),
            o.get("zoro_gmail_account"),
            p_date,
            d_date,
            status,
            o["pkg_ratio_text"],
            o["pkg_count"],
            tids_str,
            o.get("quantity") or 1,
            cost,
            earn,
            o.get("ship_name"),
            buyer_dest,
            carrier,
            loc,
            scan_time,
            note
        ]

        for c_idx, val in enumerate(row_vals, 1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.font = REGULAR_FONT
            cell.fill = fill
            cell.border = BORDER_THIN

            _, _, align = headers[c_idx - 1]
            cell.alignment = align

            if c_idx == 7:  # Status Badge
                badge_fill, badge_font = STATUS_STYLES.get(status, DEFAULT_BADGE)
                cell.fill = badge_fill
                cell.font = badge_font
            elif c_idx == 8:  # Package Breakdown
                if "Partial" in str(val):
                    cell.font = Font(name="Segoe UI", size=9, bold=True, color="92400E")
                elif "exception" in status.lower():
                    cell.font = Font(name="Segoe UI", size=9, bold=True, color="991B1B")
                else:
                    cell.font = Font(name="Segoe UI", size=9, color="1E293B")
            elif c_idx in (12, 13):  # Cost / Earnings Currency
                cell.number_format = "$#,##0.00"

    # Total Row
    tot_row = len(orders) + header_row + 1
    ws.row_dimensions[tot_row].height = 24
    ws.cell(row=tot_row, column=1, value="TOTALS").font = BOLD_FONT
    ws.cell(row=tot_row, column=1).alignment = Alignment(horizontal="center")
    ws.cell(row=tot_row, column=1).border = BORDER_TOP_BOTTOM

    for c in range(2, len(headers) + 1):
        cell = ws.cell(row=tot_row, column=c)
        cell.border = BORDER_TOP_BOTTOM
        if c == 9:  # Pkg Count Sum
            cell.value = f"=SUM(I{header_row+1}:I{tot_row-1})"
            cell.font = BOLD_FONT
            cell.alignment = Alignment(horizontal="center")
        elif c == 11:  # Qty Sum
            cell.value = f"=SUM(K{header_row+1}:K{tot_row-1})"
            cell.font = BOLD_FONT
            cell.alignment = Alignment(horizontal="center")
        elif c == 12:  # Zoro Cost Sum
            cell.value = f"=SUM(L{header_row+1}:L{tot_row-1})"
            cell.font = BOLD_FONT
            cell.number_format = "$#,##0.00"
            cell.alignment = Alignment(horizontal="right")
        elif c == 13:  # Amazon Earnings Sum
            cell.value = f"=SUM(M{header_row+1}:M{tot_row-1})"
            cell.font = BOLD_FONT
            cell.number_format = "$#,##0.00"
            cell.alignment = Alignment(horizontal="right")

    ws.freeze_panes = f"A{header_row+1}"

def build_executive_summary(ws, all_orders, account_list, title_str, subtitle_str):
    # Title
    ws.merge_cells("A1:L1")
    ws["A1"] = title_str
    ws["A1"].font = TITLE_FONT
    ws.row_dimensions[1].height = 30

    ws.merge_cells("A2:L2")
    ws["A2"] = subtitle_str
    ws["A2"].font = SUBTITLE_FONT
    ws.row_dimensions[2].height = 20

    # Top KPI Cards
    tot_orders = len(all_orders)
    tot_cost = sum(float(o.get("zoro_order_total") or 0.0) for o in all_orders)
    tot_earn = sum(float(o.get("earnings") or 0.0) for o in all_orders)
    tot_partial = sum(1 for o in all_orders if o["delivery_status"] == "PartialDelivered")
    tot_exceptions = sum(1 for o in all_orders if o["delivery_status"] in ("DeliveryException", "Delivery exception", "Shipment exception", "Returning to FedEx facility"))
    tot_in_transit = sum(1 for o in all_orders if o["delivery_status"] in ("InTransit", "On the way", "Delivery updated", "We have your package", "We tried to deliver", "Ready for pickup"))
    tot_out_for_delivery = sum(1 for o in all_orders if o["delivery_status"] in ("OutForDelivery", "Out for delivery"))
    tot_label_created = sum(1 for o in all_orders if o["delivery_status"] == "LabelCreated")

    kpis = [
        ("TOTAL UNDELIVERED", f"{tot_orders:,} Orders", "A4:B5", "FEF2F2", "991B1B"),
        ("CAPITAL AT RISK ($)", f"${tot_cost:,.2f}", "C4:D5", "FEF2F2", "991B1B"),
        ("AMAZON EARNINGS ($)", f"${tot_earn:,.2f}", "E4:F5", "EFF6FF", "1E40AF"),
        ("PARTIAL DELIVERIES", f"{tot_partial:,} Orders", "G4:H5", "FEF3C7", "92400E"),
        ("CARRIER EXCEPTIONS", f"{tot_exceptions:,} Orders", "I4:J5", "FEE2E2", "B91C1C"),
        ("GHOST LABELS (OOS)", f"{tot_label_created:,} Orders", "K4:L5", "F1F5F9", "475569")
    ]

    for title, val, rng, bg, fg in kpis:
        top_left, _ = rng.split(":")
        ws.merge_cells(rng)
        cell = ws[top_left]
        cell.value = f"{title}\n{val}"
        cell.fill = PatternFill(start_color=bg, end_color=bg, fill_type="solid")
        cell.font = Font(name="Segoe UI", size=12, bold=True, color=fg)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    ws.row_dimensions[4].height = 25
    ws.row_dimensions[5].height = 25

    # Account Breakdown Table
    start_row = 8
    ws.cell(row=start_row, column=1, value="CROSS-ACCOUNT BREAKDOWN").font = Font(name="Segoe UI", size=12, bold=True, color="1B365D")
    
    headers_acct = [
        ("Account Key", 18),
        ("Account Display Name", 24),
        ("Gmail Address", 28),
        ("Total Undelivered", 18),
        ("Partial Delivered", 18),
        ("Exceptions", 15),
        ("In Transit", 15),
        ("Out For Delivery", 16),
        ("Label Created", 15),
        ("Total Zoro Cost ($)", 20)
    ]

    h_row = start_row + 1
    ws.row_dimensions[h_row].height = 26
    for c_idx, (h_name, width) in enumerate(headers_acct, 1):
        cell = ws.cell(row=h_row, column=c_idx, value=h_name)
        cell.font = HEADER_FONT
        cell.fill = NAVY_HEADER
        cell.alignment = Alignment(horizontal="center", vertical="center")
        col_letter = get_column_letter(c_idx)
        ws.column_dimensions[col_letter].width = width

    cur_r = h_row + 1
    for key, disp, gmail, _ in account_list:
        acct_orders = [o for o in all_orders if o.get("zoro_gmail_account") == key]
        cnt = len(acct_orders)
        cost = sum(float(o.get("zoro_order_total") or 0.0) for o in acct_orders)
        part = sum(1 for o in acct_orders if o["delivery_status"] == "PartialDelivered")
        exc = sum(1 for o in acct_orders if o["delivery_status"] in ("DeliveryException", "Delivery exception", "Shipment exception", "Returning to FedEx facility"))
        trans = sum(1 for o in acct_orders if o["delivery_status"] in ("InTransit", "On the way", "Delivery updated", "We have your package", "We tried to deliver", "Ready for pickup"))
        ofd = sum(1 for o in acct_orders if o["delivery_status"] in ("OutForDelivery", "Out for delivery"))
        lbl = sum(1 for o in acct_orders if o["delivery_status"] == "LabelCreated")

        fill = ZEBRA_FILL if (cur_r % 2 == 0) else WHITE_FILL
        ws.row_dimensions[cur_r].height = 22

        row_vals = [key, disp, gmail, cnt, part, exc, trans, ofd, lbl, cost]
        for c_idx, v in enumerate(row_vals, 1):
            cell = ws.cell(row=cur_r, column=c_idx, value=v)
            cell.font = REGULAR_FONT
            cell.fill = fill
            cell.border = BORDER_THIN
            if c_idx in (1, 2, 3):
                cell.alignment = Alignment(horizontal="left", vertical="center")
            elif c_idx == 10:
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.number_format = "$#,##0.00"
            else:
                cell.alignment = Alignment(horizontal="center", vertical="center")
        cur_r += 1

    # Total row for account table
    ws.row_dimensions[cur_r].height = 24
    ws.cell(row=cur_r, column=1, value="CONSOLIDATED TOTALS").font = BOLD_FONT
    ws.cell(row=cur_r, column=1).alignment = Alignment(horizontal="left")
    ws.cell(row=cur_r, column=1).border = BORDER_TOP_BOTTOM

    for c in range(2, len(headers_acct) + 1):
        cell = ws.cell(row=cur_r, column=c)
        cell.border = BORDER_TOP_BOTTOM
        col_let = get_column_letter(c)
        if c in (4, 5, 6, 7, 8, 9):
            cell.value = f"=SUM({col_let}{h_row+1}:{col_let}{cur_r-1})"
            cell.font = BOLD_FONT
            cell.alignment = Alignment(horizontal="center")
        elif c == 10:
            cell.value = f"=SUM({col_let}{h_row+1}:{col_let}{cur_r-1})"
            cell.font = BOLD_FONT
            cell.number_format = "$#,##0.00"
            cell.alignment = Alignment(horizontal="right")

    # Delivery Status Breakdown Table
    st_start = cur_r + 3
    ws.cell(row=st_start, column=1, value="BREAKDOWN BY CARRIER DELIVERY STATUS").font = Font(name="Segoe UI", size=12, bold=True, color="1B365D")
    
    headers_st = [("Delivery Status", 24), ("Order Count", 15), ("Capital Exposure ($)", 22), ("Action Recommendation", 45)]
    sh_row = st_start + 1
    ws.row_dimensions[sh_row].height = 24
    for c_idx, (h_name, width) in enumerate(headers_st, 1):
        cell = ws.cell(row=sh_row, column=c_idx, value=h_name)
        cell.font = HEADER_FONT
        cell.fill = NAVY_HEADER
        cell.alignment = Alignment(horizontal="center", vertical="center")
        col_letter = get_column_letter(c_idx)
        ws.column_dimensions[col_letter].width = width

    st_map = {
        "PartialDelivered": "Multi-package split: 1+ items delivered, remainder stuck. File Zoro replacement/refund for missing units.",
        "DeliveryException": "Customer address issue, damaged in transit, or business closed. Review scan note & claim Zoro refund.",
        "Delivery exception": "Customer address issue, damaged in transit, or business closed. Claim Zoro refund.",
        "Shipment exception": "Barcode unreadable, damaged package, or return initiated. Claim Zoro refund.",
        "InTransit": "Package stalled in transit beyond delivery SLA. Contact carrier / request Zoro trace & refund.",
        "On the way": "Package stalled in transit beyond delivery SLA. Contact carrier / request Zoro trace & refund.",
        "OutForDelivery": "Scanned out for delivery but missing final delivery confirmation. Check for carrier loss.",
        "Out for delivery": "Scanned out for delivery but missing final delivery confirmation. Check for carrier loss.",
        "LabelCreated": "Label generated by Zoro but package never tendered to carrier. Ghost order — claim immediate Zoro refund.",
        "Cancelled": "Zoro cancelled order while Amazon marked as shipped. High risk — buyer will not receive items.",
        "We have your package": "Carrier accepted package but movement stalled at origin hub. Claim carrier loss.",
        "We tried to deliver": "Delivery attempt failed. Customer hasn't picked up or package returning.",
        "Returning to FedEx facility": "Return to sender initiated.",
        "Ready for pickup": "Held at FedEx location."
    }

    s_row = sh_row + 1
    for st_name, rec in st_map.items():
        st_orders = [o for o in all_orders if o["delivery_status"] == st_name]
        cnt = len(st_orders)
        if cnt == 0:
            continue
        cost = sum(float(o.get("zoro_order_total") or 0.0) for o in st_orders)
        ws.row_dimensions[s_row].height = 20
        fill = ZEBRA_FILL if (s_row % 2 == 0) else WHITE_FILL

        ws.cell(row=s_row, column=1, value=st_name).alignment = Alignment(horizontal="left", vertical="center")
        ws.cell(row=s_row, column=1).fill, ws.cell(row=s_row, column=1).font = STATUS_STYLES.get(st_name, DEFAULT_BADGE)
        ws.cell(row=s_row, column=1).border = BORDER_THIN

        cell2 = ws.cell(row=s_row, column=2, value=cnt)
        cell2.alignment = Alignment(horizontal="center", vertical="center")
        cell2.font = BOLD_FONT
        cell2.fill = fill
        cell2.border = BORDER_THIN

        cell3 = ws.cell(row=s_row, column=3, value=cost)
        cell3.alignment = Alignment(horizontal="right", vertical="center")
        cell3.font = BOLD_FONT
        cell3.fill = fill
        cell3.border = BORDER_THIN
        cell3.number_format = "$#,##0.00"

        cell4 = ws.cell(row=s_row, column=4, value=rec)
        cell4.alignment = Alignment(horizontal="left", vertical="center")
        cell4.font = REGULAR_FONT
        cell4.fill = fill
        cell4.border = BORDER_THIN

        s_row += 1

def generate_targeted_workbook(orders, account_list, output_file):
    """Generate master workbook with Executive Summary, Master Actionable, and tabs for target accounts."""
    wb = openpyxl.Workbook()
    ws_exec = wb.active
    ws_exec.title = "Executive Summary"
    build_executive_summary(
        ws_exec, orders, account_list,
        "EXECUTIVE SUMMARY — UNDELIVERED ORDERS RECONCILIATION",
        "Authoritative audit of Zoro orders with unconfirmed or partial delivery across Asad, Inuhr, and Salesmercentile 1"
    )

    # Master Actionable tab (Partial Delivered, DeliveryException, LabelCreated, Cancelled)
    actionable_orders = [
        o for o in orders 
        if o["delivery_status"] in ("PartialDelivered", "DeliveryException", "Delivery exception", "Shipment exception", "Returning to FedEx facility", "LabelCreated", "Cancelled")
    ]
    ws_act = wb.create_sheet(title="Actionable Claims & Partial")
    populate_orders_sheet(
        ws_act,
        actionable_orders,
        "MASTER ACTIONABLE CLAIMS — PARTIAL DELIVERIES & EXCEPTIONS",
        f"Critical priority: {len(actionable_orders)} orders requiring Zoro refunds, replacements, or carrier claims"
    )

    # Dedicated tabs for each target account
    for key, disp, gmail, _ in account_list:
        acct_orders = [o for o in orders if o.get("zoro_gmail_account") == key]
        tab_name = disp.split("(")[0].strip()[:31]
        ws_acct = wb.create_sheet(title=tab_name)
        populate_orders_sheet(
            ws_acct,
            acct_orders,
            f"UNDELIVERED ORDERS — {disp.upper()}",
            f"Zoro Account: {gmail} | Total Undelivered Orders: {len(acct_orders)}"
        )

    print(f"Saving targeted workbook to {output_file}...")
    wb.save(output_file)
    print(f"  ✅ Saved: {output_file}")

def generate_individual_account_workbooks(orders, account_list):
    """Generate separate standalone workbooks for each account."""
    for key, disp, gmail, _ in account_list:
        acct_orders = [o for o in orders if o.get("zoro_gmail_account") == key]
        clean_name = key.replace("_amazon_us", "").replace("_llc", "").replace("1", "_1")
        filename = f"Undelivered_Orders_{clean_name.capitalize()}_2026.xlsx"
        filepath = os.path.join(DATA_DIR, filename)

        wb = openpyxl.Workbook()
        ws_exec = wb.active
        ws_exec.title = "Summary"
        build_executive_summary(
            ws_exec, acct_orders, [(key, disp, gmail, "")],
            f"UNDELIVERED ORDERS AUDIT — {disp.upper()}",
            f"Account: {gmail} | Total Undelivered: {len(acct_orders)}"
        )

        ws_data = wb.create_sheet(title="Undelivered Orders")
        populate_orders_sheet(
            ws_data,
            acct_orders,
            f"DETAILED ORDER RECORDS — {disp.upper()}",
            f"All {len(acct_orders)} active undelivered/partial delivered orders"
        )

        wb.save(filepath)
        print(f"  ✅ Saved standalone file: {filepath}")

def main():
    print("=" * 65)
    print(" 🚚 GENERATING UNDELIVERED ORDERS RECONCILIATION WORKBOOKS")
    print("=" * 65)

    conn = get_db_connection()
    try:
        # 1. Fetch targeted accounts: asad, inuhr, salesmercentile1
        target_keys = [a[0] for a in TARGET_ACCOUNTS]
        print(f"Fetching undelivered orders for target accounts: {target_keys}...")
        target_orders = fetch_undelivered_orders(conn, target_keys=target_keys)
        print(f"  Found {len(target_orders)} matching orders across {len(target_keys)} accounts.")

        # 2. Build the primary consolidated workbook for asad, inuhr, salesmercentile1
        generate_targeted_workbook(target_orders, TARGET_ACCOUNTS, OUTPUT_COMBINED_XLSX)

        # 3. Generate individual standalone files for each of the 3 accounts
        print("Generating individual account workbooks...")
        generate_individual_account_workbooks(target_orders, TARGET_ACCOUNTS)

        # 4. Also refresh the full portfolio reconciliation if requested or as reference
        if "--all" in sys.argv:
            print("\nFetching undelivered orders across ALL accounts...")
            all_orders = fetch_undelivered_orders(conn, target_keys=None)
            print(f"  Found {len(all_orders)} matching orders across all accounts.")
            generate_targeted_workbook(all_orders, ALL_ACCOUNTS + [OTHER_ACCOUNT], OUTPUT_LEGACY_XLSX)

    finally:
        conn.close()

    print("\n✅ Reconciliation generation complete!")

if __name__ == "__main__":
    main()
