#!/usr/bin/env python3
"""
generate_undelivered_orders_excel.py
Generates a comprehensive, executive-grade multi-account Excel workbook
reconciling all undelivered Zoro orders placed before September 8th, 2026 across:
- Inhur LLC (inuhrecomllc5@gmail.com)
- Asad Amazon US (asad.amazon.us@gmail.com)
- Salesmercentile (salesmercentile@gmail.com)
- AB Ecom LLC / DM (abecomllc26@gmail.com)
- Wrenchly LLC (wrenchlyllc@gmail.com)
- Salesmercentile 1 (salesmercentile1@gmail.com)
- Other & Legacy Pools (abecomllc02 & unassigned placer orders)

Features:
- Executive Summary with cross-account KPIs and financial exposure ($124,569.28)
- Master Actionable tab (Partial Delivered & Exceptions & Label Created)
- Dedicated tabs for each business Gmail account with tracking IDs, package ratios, and scan details
"""

import os
import sys
import sqlite3
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(AUTO_DIR, "data", "dashboard_slim.db")
OUTPUT_XLSX = os.path.join(AUTO_DIR, "data", "Zoro_Undelivered_Orders_Reconciliation_2026.xlsx")

# Styling Definitions
NAVY_HEADER = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid")
HEADER_FONT = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
TITLE_FONT = Font(name="Segoe UI", size=16, bold=True, color="1B365D")
SUBTITLE_FONT = Font(name="Segoe UI", size=10, italic=True, color="475569")
BOLD_FONT = Font(name="Segoe UI", size=10, bold=True, color="0F172A")
REGULAR_FONT = Font(name="Segoe UI", size=10, color="1E293B")
KPI_TITLE_FONT = Font(name="Segoe UI", size=9, bold=True, color="64748B")
KPI_VAL_FONT = Font(name="Segoe UI", size=18, bold=True, color="1B365D")

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
    "Out for delivery": (
        PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
        Font(name="Segoe UI", size=9, bold=True, color="166534")
    ),
    "OutForDelivery": (
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

ACCOUNTS = [
    ("inhur_llc", "Inhur LLC (inuhr)", "inuhrecomllc5@gmail.com", "Child Account"),
    ("asad_amazon_us", "Asad Amazon US", "asad.amazon.us@gmail.com", "Sister Account"),
    ("salesmercentile", "Salesmercentile", "salesmercentile@gmail.com", "Primary Zoro Pool"),
    ("abecomllc26", "AB Ecom LLC (DM)", "abecomllc26@gmail.com", "Hathoro Reseller"),
    ("wrenchlyllc", "Wrenchly LLC", "wrenchlyllc@gmail.com", "Wrenchly Account"),
    ("salesmercentile1", "Salesmercentile 1", "salesmercentile1@gmail.com", "Secondary Pool")
]

OTHER_ACCOUNT = ("other_legacy", "Other & Legacy Pools", "abecomllc02 / Unassigned", "Legacy & Placer")

ACTIVE_STATUSES = (
    "PartialDelivered", "On the way", "Delivery updated", "Delivery exception",
    "Shipment exception", "LabelCreated", "Out for delivery", "OutForDelivery",
    "We have your package", "We tried to deliver", "Returning to FedEx facility", "Ready for pickup"
)

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

def get_package_detail(status, tids):
    count = len(tids)
    if status == "PartialDelivered":
        if count > 1:
            return f"Partial Delivery: At least 1 of {count} pkgs still undelivered"
        else:
            return "Partial Delivery: 1 pkg tracked (multi-piece split / partial scan)"
    else:
        if count > 1:
            return f"All {count} packages undelivered ({status})"
        elif count == 1:
            return f"1 of 1 package undelivered ({status})"
        else:
            return f"Undelivered ({status})"

def fetch_data():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    ph_s = ",".join(["?"] * len(ACTIVE_STATUSES))

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
            last_scan_location,
            last_scan_time,
            delivery_note
        FROM closing_orders
        WHERE purchase_date < '2026-09-08'
          AND delivery_status IN ({ph_s})
          AND COALESCE(manual_delivered, '') != 'Yes'
          AND tracking_ids IS NOT NULL AND tracking_ids != ''
        ORDER BY purchase_date DESC
    """
    cur.execute(query, list(ACTIVE_STATUSES))
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    conn.close()

    records = []
    for r in rows:
        d = dict(zip(cols, r))
        tids = parse_tids(d.get("tracking_ids"))
        d["clean_tids"] = tids
        d["pkg_count"] = len(tids)
        d["pkg_ratio_text"] = get_package_detail(d.get("delivery_status"), tids)
        records.append(d)

    return records

def populate_orders_sheet(ws, orders, title_text, subtitle_text):
    # Title
    ws.merge_cells("A1:Q1")
    ws["A1"] = title_text
    ws["A1"].font = TITLE_FONT
    ws["A1"].alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 28

    ws.merge_cells("A2:Q2")
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
        ("Buyer Name", 20, Alignment(horizontal="left")),
        ("Destination City, State", 24, Alignment(horizontal="left")),
        ("Last Scan Location", 22, Alignment(horizontal="left")),
        ("Last Scan Time", 16, Alignment(horizontal="center")),
        ("Exception / Delivery Note", 40, Alignment(horizontal="left"))
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
        status = o.get("delivery_status") or "Unknown"

        loc = o.get("last_scan_location") or ""
        scan_time = str(o.get("last_scan_time") or "")[:16]
        note = o.get("delivery_note") or ""
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
            o.get("ship_name"),
            buyer_dest,
            loc,
            scan_time,
            note
        ]

        for c_idx, val in enumerate(row_vals, 1):
            cell = ws.cell(row=r_idx, column=c_idx, value=val)
            cell.font = REGULAR_FONT
            cell.fill = fill
            cell.border = BORDER_THIN

            # Alignment & formatting
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
            elif c_idx == 12:  # Cost Currency
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
        elif c == 12:  # Cost Sum
            cell.value = f"=SUM(L{header_row+1}:L{tot_row-1})"
            cell.font = BOLD_FONT
            cell.number_format = "$#,##0.00"
            cell.alignment = Alignment(horizontal="right")

    ws.freeze_panes = f"A{header_row+1}"

def build_executive_summary(ws, all_orders):
    # Title
    ws.merge_cells("A1:K1")
    ws["A1"] = "EXECUTIVE SUMMARY — MULTI-ACCOUNT UNDELIVERED ORDERS RECONCILIATION"
    ws["A1"].font = TITLE_FONT
    ws.row_dimensions[1].height = 30

    ws.merge_cells("A2:K2")
    ws["A2"] = "Audit of all Zoro orders placed before September 8th, 2026 with unconfirmed or partial delivery across all business Gmail accounts"
    ws["A2"].font = SUBTITLE_FONT
    ws.row_dimensions[2].height = 20

    # Top KPI Cards
    tot_orders = len(all_orders)
    tot_cost = sum(float(o.get("zoro_order_total") or 0.0) for o in all_orders)
    tot_pkgs = sum(o["pkg_count"] for o in all_orders)
    tot_partial = sum(1 for o in all_orders if o["delivery_status"] == "PartialDelivered")
    tot_exceptions = sum(1 for o in all_orders if o["delivery_status"] in ("Delivery exception", "Shipment exception", "Returning to FedEx facility"))
    tot_in_transit = sum(1 for o in all_orders if o["delivery_status"] in ("On the way", "Delivery updated", "We have your package", "Out for delivery", "OutForDelivery", "We tried to deliver", "Ready for pickup"))
    tot_label_created = sum(1 for o in all_orders if o["delivery_status"] == "LabelCreated")

    kpis = [
        ("TOTAL UNDELIVERED ORDERS", f"{tot_orders:,}", "A4:B5", "FEF2F2", "991B1B"),
        ("TOTAL CAPITAL AT RISK ($)", f"${tot_cost:,.2f}", "C4:D5", "FEF2F2", "991B1B"),
        ("PARTIAL DELIVERED (SPLIT)", f"{tot_partial:,}", "E4:F5", "FEF3C7", "92400E"),
        ("DELIVERY EXCEPTIONS", f"{tot_exceptions:,}", "G4:H5", "FEE2E2", "B91C1C"),
        ("IN TRANSIT / DELAYED (>11D)", f"{tot_in_transit:,}", "I4:J5", "DBEAFE", "1E40AF"),
        ("LABEL CREATED ONLY", f"{tot_label_created:,}", "K4:L5", "F1F5F9", "475569")
    ]

    for title, val, rng, bg, fg in kpis:
        top_left, bottom_right = rng.split(":")
        ws.merge_cells(rng)
        cell = ws[top_left]
        cell.value = f"{title}\n{val}"
        cell.fill = PatternFill(start_color=bg, end_color=bg, fill_type="solid")
        cell.font = Font(name="Segoe UI", size=13, bold=True, color=fg)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    ws.row_dimensions[4].height = 25
    ws.row_dimensions[5].height = 25

    # Account Breakdown Table
    start_row = 8
    ws.cell(row=start_row, column=1, value="CROSS-ACCOUNT BREAKDOWN").font = Font(name="Segoe UI", size=12, bold=True, color="1B365D")
    
    headers_acct = [
        ("Zoro Account Key", 18),
        ("Account Display Name", 24),
        ("Gmail Address", 28),
        ("Total Undelivered", 18),
        ("Partial Delivered", 18),
        ("Exceptions", 15),
        ("In Transit / Delayed", 20),
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
    # 6 main accounts
    all_account_entries = list(ACCOUNTS) + [OTHER_ACCOUNT]
    main_keys = [a[0] for a in ACCOUNTS]

    for key, disp, gmail, role in all_account_entries:
        if key == "other_legacy":
            acct_orders = [o for o in all_orders if o.get("zoro_gmail_account") not in main_keys]
        else:
            acct_orders = [o for o in all_orders if o.get("zoro_gmail_account") == key]

        cnt = len(acct_orders)
        cost = sum(float(o.get("zoro_order_total") or 0.0) for o in acct_orders)
        part = sum(1 for o in acct_orders if o["delivery_status"] == "PartialDelivered")
        exc = sum(1 for o in acct_orders if o["delivery_status"] in ("Delivery exception", "Shipment exception", "Returning to FedEx facility"))
        trans = sum(1 for o in acct_orders if o["delivery_status"] in ("On the way", "Delivery updated", "We have your package", "Out for delivery", "OutForDelivery", "We tried to deliver", "Ready for pickup"))
        lbl = sum(1 for o in acct_orders if o["delivery_status"] == "LabelCreated")

        fill = ZEBRA_FILL if (cur_r % 2 == 0) else WHITE_FILL
        ws.row_dimensions[cur_r].height = 22

        row_vals = [key, disp, gmail, cnt, part, exc, trans, lbl, cost]
        for c_idx, v in enumerate(row_vals, 1):
            cell = ws.cell(row=cur_r, column=c_idx, value=v)
            cell.font = REGULAR_FONT
            cell.fill = fill
            cell.border = BORDER_THIN
            if c_idx in (1, 2, 3):
                cell.alignment = Alignment(horizontal="left", vertical="center")
            elif c_idx == 9:
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
        if c in (4, 5, 6, 7, 8):
            cell.value = f"=SUM({col_let}{h_row+1}:{col_let}{cur_r-1})"
            cell.font = BOLD_FONT
            cell.alignment = Alignment(horizontal="center")
        elif c == 9:
            cell.value = f"=SUM({col_let}{h_row+1}:{col_let}{cur_r-1})"
            cell.font = BOLD_FONT
            cell.number_format = "$#,##0.00"
            cell.alignment = Alignment(horizontal="right")

    # Delivery Status Breakdown Table
    st_start = cur_r + 3
    ws.cell(row=st_start, column=1, value="BREAKDOWN BY FEDEX DELIVERY STATUS").font = Font(name="Segoe UI", size=12, bold=True, color="1B365D")
    
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
        "PartialDelivered": "Multi-package shipment split: 1+ items delivered, remainder stuck. File Zoro replacement/refund.",
        "Shipment exception": "Damaged in transit, barcode unreadable, or returned to sender. Claim Zoro refund.",
        "Delivery exception": "Customer address issue, business closed, or damaged. Review buyer message & claim Zoro refund.",
        "On the way": "Package in transit >11 days. Severely past deliver-by window. Presumed lost by FedEx.",
        "Delivery updated": "Delivery date rescheduled multiple times. Package stalled in transit.",
        "LabelCreated": "Label generated by Zoro but never tendered to FedEx. Ghost order — request refund from Zoro.",
        "We have your package": "FedEx accepted package but no subsequent movement. Lost at origin hub.",
        "Out for delivery": "Scanned out for delivery days ago but never scanned as delivered. Likely lost/misplaced.",
        "OutForDelivery": "Scanned out for delivery days ago but never scanned as delivered. Likely lost/misplaced.",
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

def main():
    print("Fetching active undelivered orders from database...")
    all_orders = fetch_data()
    print(f"Total matching orders found: {len(all_orders)}")

    wb = openpyxl.Workbook()
    # Default sheet
    ws_exec = wb.active
    ws_exec.title = "Executive Summary"
    build_executive_summary(ws_exec, all_orders)

    # Master Actionable tab (Partial Delivered & Exceptions & Ghost Labels)
    actionable_orders = [
        o for o in all_orders 
        if o["delivery_status"] in ("PartialDelivered", "Delivery exception", "Shipment exception", "Returning to FedEx facility", "LabelCreated")
    ]
    ws_act = wb.create_sheet(title="Actionable Exceptions & Partial")
    populate_orders_sheet(
        ws_act,
        actionable_orders,
        "MASTER ACTIONABLE CLAIMS — PARTIAL DELIVERIES & EXCEPTIONS",
        f"Critical priority: {len(actionable_orders)} orders requiring Zoro refunds, replacements, or carrier claims (placed before Sep 8, 2026)"
    )

    # Dedicated tabs for each of the 6 core business accounts
    main_keys = [a[0] for a in ACCOUNTS]
    for key, disp, gmail, role in ACCOUNTS:
        acct_orders = [o for o in all_orders if o.get("zoro_gmail_account") == key]
        ws_acct = wb.create_sheet(title=disp[:31])
        populate_orders_sheet(
            ws_acct,
            acct_orders,
            f"UNDELIVERED ORDERS — {disp.upper()}",
            f"Zoro Account: {gmail} | Total Undelivered Orders: {len(acct_orders)} | Orders placed before Sep 8, 2026"
        )

    # Dedicated tab for Other & Legacy pools (abecomllc02 & unassigned placer orders)
    other_orders = [o for o in all_orders if o.get("zoro_gmail_account") not in main_keys]
    if other_orders:
        ws_other = wb.create_sheet(title="Other & Legacy Pools")
        populate_orders_sheet(
            ws_other,
            other_orders,
            "UNDELIVERED ORDERS — OTHER & LEGACY POOLS",
            f"Accounts: abecomllc02 & Unassigned Placer Orders | Total: {len(other_orders)} orders placed before Sep 8, 2026"
        )

    print(f"Saving workbook to {OUTPUT_XLSX}...")
    wb.save(OUTPUT_XLSX)
    print("Workbook successfully created!")

if __name__ == "__main__":
    main()
