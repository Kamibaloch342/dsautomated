"""
generate_zoro_tax_refund_full_2026.py

Generates executive-grade Excel workbooks for FULL YEAR 2026 sales tax refund claims to Zoro.
Includes ALL orders of 2026 (January 1, 2026 – September 11, 2026).

Covered Accounts (Operating under M&R Market, LLC):
  1. inuhrecomllc   | Customer #25217105 | inuhrecomllc5@gmail.com
  2. salesmercentile| Customer #25017919 | salesmercentile@gmail.com
  3. salesmercentile1| Customer #25061417 | salesmercentil1@gmail.com
"""

import os
import sqlite3
import pandas as pd
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

CUTOFF_6M = "2026-03-11"
CURRENT_DATE = "2026-09-11"

KELLY_APPROVED_STATES = {
    "AL", "AK", "AZ", "AR", "CT", "CA", "CO", "GA", "FL", "IN", "IA", "ID",
    "IL", "KS", "KY", "LA", "MI", "ME", "MO", "MN", "MD", "MA", "NE", "NV",
    "NJ", "NY", "NC", "ND", "NM", "OH", "OK", "PA", "RI", "SD", "SC", "TN",
    "TX", "UT", "VT", "VA", "WA", "WI", "WV", "WY"
}

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming"
}

ACCOUNTS_META = {
    "inhur_llc": {
        "cust_id": "25217105",
        "cust_str": "CUST25217105",
        "name": "inuhrecomllc",
        "email": "inuhrecomllc5@gmail.com",
        "entity": "M&R Market, LLC",
        "tab_name": "inuhrecomllc",
    },
    "salesmercentile": {
        "cust_id": "25017919",
        "cust_str": "CUST25017919",
        "name": "salesmercentile",
        "email": "salesmercentile@gmail.com",
        "entity": "M&R Market, LLC",
        "tab_name": "salesmercentile",
    },
    "salesmercentile1": {
        "cust_id": "25061417",
        "cust_str": "CUST25061417",
        "name": "salesmercentile1",
        "email": "salesmercentil1@gmail.com",
        "entity": "M&R Market, LLC",
        "tab_name": "salesmercentile1",
    },
}

FONT_FAMILY = "Segoe UI"
FONT_TITLE = Font(name=FONT_FAMILY, size=15, bold=True, color="FFFFFF")
FONT_SUBTITLE = Font(name=FONT_FAMILY, size=10, italic=True, color="E2E8F0")
FONT_SECTION = Font(name=FONT_FAMILY, size=11.5, bold=True, color="1E3A8A")
FONT_TH = Font(name=FONT_FAMILY, size=10, bold=True, color="FFFFFF")
FONT_TD = Font(name=FONT_FAMILY, size=9.5, color="1E293B")
FONT_TD_BOLD = Font(name=FONT_FAMILY, size=9.5, bold=True, color="1E293B")
FONT_TOTAL = Font(name=FONT_FAMILY, size=10, bold=True, color="0F172A")
FONT_TAX_HIGHLIGHT = Font(name=FONT_FAMILY, size=9.5, bold=True, color="15803D")

FILL_TITLE = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
FILL_TH = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
FILL_TH_SUB = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
FILL_ZEBRA = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
FILL_WHITE = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
FILL_TOTAL = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
FILL_TAX_CELL = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
FILL_ALERT_CELL = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")

BORDER_THIN_GRAY = Side(style="thin", color="CBD5E1")
BORDER_MEDIUM_NAVY = Side(style="medium", color="1E3A8A")
BORDER_DOUBLE_NAVY = Side(style="double", color="1E3A8A")

BORDER_CELL = Border(left=BORDER_THIN_GRAY, right=BORDER_THIN_GRAY, top=BORDER_THIN_GRAY, bottom=BORDER_THIN_GRAY)
BORDER_HEADER = Border(left=BORDER_THIN_GRAY, right=BORDER_THIN_GRAY, top=BORDER_MEDIUM_NAVY, bottom=BORDER_MEDIUM_NAVY)
BORDER_TOTAL_ROW = Border(left=BORDER_THIN_GRAY, right=BORDER_THIN_GRAY, top=BORDER_THIN_GRAY, bottom=BORDER_DOUBLE_NAVY)

ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
ALIGN_HEADER = Alignment(horizontal="center", vertical="center", wrap_text=True)

FMT_CURRENCY = "$#,##0.00"
FMT_INT = "#,##0"
FMT_PCT = "0.0%"

def load_data(db_path):
    conn = sqlite3.connect(db_path)
    q = """
        SELECT 
            wb_order_id,
            gmail_account,
            order_date,
            email_timestamp,
            subtotal,
            tax,
            order_total,
            ship_city,
            ship_state,
            ship_zip
        FROM archive_zoro
        WHERE gmail_account IN ('inhur_llc', 'salesmercentile', 'salesmercentile1')
          AND tax > 0
        ORDER BY email_timestamp DESC
    """
    df = pd.read_sql_query(q, conn)
    conn.close()

    df["dt_order"] = pd.to_datetime(df["order_date"], format="mixed", errors="coerce")
    df["dt_email"] = pd.to_datetime(df["email_timestamp"], errors="coerce")
    
    # 2026 flag: order date in 2026 OR email in 2026
    df["is_2026"] = (df["dt_email"].dt.year == 2026) | (df["dt_order"].dt.year == 2026)
    
    # Clean order date string
    df["order_date_clean"] = df["dt_order"].dt.strftime("%Y-%m-%d")
    df["order_date_clean"] = df["order_date_clean"].fillna(df["dt_email"].dt.strftime("%Y-%m-%d"))
    
    # Month string for 2026
    df["month_str"] = df["dt_email"].dt.strftime("%Y-%m")

    # State cleaning
    df["ship_state_clean"] = df["ship_state"].fillna("").astype(str).str.strip().str.upper()
    df["state_name"] = df["ship_state_clean"].map(STATE_NAMES).fillna(df["ship_state_clean"])
    
    df["ship_city_clean"] = df["ship_city"].fillna("").astype(str).str.strip().str.title()
    df["ship_zip_clean"] = df["ship_zip"].fillna("").astype(str).str.strip()
    
    df["subtotal"] = pd.to_numeric(df["subtotal"], errors="coerce").fillna(0.0)
    df["tax"] = pd.to_numeric(df["tax"], errors="coerce").fillna(0.0)
    df["order_total"] = pd.to_numeric(df["order_total"], errors="coerce").fillna(0.0)

    # Status flags
    df["is_6m"] = (df["dt_email"] >= CUTOFF_6M) | (df["dt_order"] >= CUTOFF_6M)
    df["is_approved_state"] = df["ship_state_clean"].isin(KELLY_APPROVED_STATES)
    
    df["exemption_status"] = df["is_approved_state"].apply(
        lambda x: "Approved 44-State List" if x else "Pending Resale Exemption"
    )
    
    def get_period_label(row):
        if not row["is_2026"]:
            return "2025 Historical Order"
        elif row["is_6m"]:
            return "Eligible (Within 6 Months: Mar 11 – Sep 11, 2026)"
        else:
            return "Eligible (Q1 2026: Jan 1 – Mar 10, 2026)"
            
    df["accounting_window"] = df.apply(get_period_label, axis=1)

    df["cust_id"] = df["gmail_account"].apply(lambda a: ACCOUNTS_META[a]["cust_id"])
    df["cust_str"] = df["gmail_account"].apply(lambda a: ACCOUNTS_META[a]["cust_str"])
    df["account_name"] = df["gmail_account"].apply(lambda a: ACCOUNTS_META[a]["name"])
    df["account_email"] = df["gmail_account"].apply(lambda a: ACCOUNTS_META[a]["email"])
    df["entity_name"] = df["gmail_account"].apply(lambda a: ACCOUNTS_META[a]["entity"])

    return df


def create_title_banner(ws, title_text, subtitle_text, max_col=12):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)
    cell1 = ws.cell(row=1, column=1, value=title_text)
    cell1.font = FONT_TITLE
    cell1.fill = FILL_TITLE
    cell1.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max_col)
    cell2 = ws.cell(row=2, column=1, value=subtitle_text)
    cell2.font = FONT_SUBTITLE
    cell2.fill = FILL_TITLE
    cell2.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20
    ws.row_dimensions[3].height = 10


def style_table_header(ws, row_idx, headers, fill=FILL_TH):
    ws.row_dimensions[row_idx].height = 24
    for col_idx, h in enumerate(headers, start=1):
        c = ws.cell(row=row_idx, column=col_idx, value=h)
        c.font = FONT_TH
        c.fill = fill
        c.alignment = ALIGN_HEADER
        c.border = BORDER_HEADER


def auto_fit_columns(ws, max_len_cap=46, min_len=11):
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or '')
            if cell.coordinate in ws.merged_cells:
                continue
            if len(val_str) > max_len and "\n" not in val_str:
                max_len = len(val_str)
        col_width = min(max_len_cap, max(min_len, max_len + 3))
        ws.column_dimensions[col_letter].width = col_width


def add_kpi_card(ws, start_row, start_col, end_col, title, value_str, note, val_color="1E3A8A", bg_color="F1F5F9"):
    ws.merge_cells(start_row=start_row, start_column=start_col, end_row=start_row, end_column=end_col)
    c_title = ws.cell(row=start_row, column=start_col, value=title.upper())
    c_title.font = Font(name=FONT_FAMILY, size=8.5, bold=True, color="64748B")
    c_title.alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells(start_row=start_row+1, start_column=start_col, end_row=start_row+1, end_column=end_col)
    c_val = ws.cell(row=start_row+1, column=start_col, value=value_str)
    c_val.font = Font(name=FONT_FAMILY, size=15, bold=True, color=val_color)
    c_val.alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells(start_row=start_row+2, start_column=start_col, end_row=start_row+2, end_column=end_col)
    c_note = ws.cell(row=start_row+2, column=start_col, value=note)
    c_note.font = Font(name=FONT_FAMILY, size=8, italic=True, color="64748B")
    c_note.alignment = Alignment(horizontal="center", vertical="center")

    card_fill = PatternFill(start_color=bg_color, end_color=bg_color, fill_type="solid")
    for r in range(start_row, start_row + 3):
        for c in range(start_col, end_col + 1):
            cell = ws.cell(row=r, column=c)
            cell.fill = card_fill
            cell.border = BORDER_CELL
    
    ws.row_dimensions[start_row].height = 15
    ws.row_dimensions[start_row+1].height = 24
    ws.row_dimensions[start_row+2].height = 14


def write_order_table(ws, df_orders, start_row=4, is_single_account=False):
    if is_single_account:
        headers = [
            "Zoro Order #", "Order Date", "Ship State", "Ship City", "Ship Zip",
            "Item Subtotal ($)", "Sales Tax Charged ($)", "Order Total ($)",
            "Approved Resale State?", "2026 Eligibility Period"
        ]
    else:
        headers = [
            "Customer #", "Account Name", "Account Email", "Zoro Order #",
            "Order Date", "Ship State", "Ship City", "Ship Zip",
            "Item Subtotal ($)", "Sales Tax Charged ($)", "Order Total ($)",
            "Approved Resale State?", "2026 Eligibility Period"
        ]

    style_table_header(ws, start_row, headers)
    
    row_idx = start_row + 1
    for _, r in df_orders.iterrows():
        is_even = ((row_idx - start_row) % 2 == 0)
        row_fill = FILL_ZEBRA if is_even else FILL_WHITE
        ws.row_dimensions[row_idx].height = 18

        if is_single_account:
            vals = [
                (r["wb_order_id"], ALIGN_CENTER, None, FONT_TD_BOLD),
                (r["order_date_clean"], ALIGN_CENTER, None, FONT_TD),
                (r["ship_state_clean"], ALIGN_CENTER, None, FONT_TD_BOLD),
                (r["ship_city_clean"], ALIGN_LEFT, None, FONT_TD),
                (r["ship_zip_clean"], ALIGN_CENTER, None, FONT_TD),
                (r["subtotal"], ALIGN_RIGHT, FMT_CURRENCY, FONT_TD),
                (r["tax"], ALIGN_RIGHT, FMT_CURRENCY, FONT_TAX_HIGHLIGHT),
                (r["order_total"], ALIGN_RIGHT, FMT_CURRENCY, FONT_TD),
                (r["exemption_status"], ALIGN_CENTER, None, FONT_TD),
                (r["accounting_window"], ALIGN_CENTER, None, FONT_TD),
            ]
        else:
            vals = [
                (r["cust_id"], ALIGN_CENTER, None, FONT_TD),
                (r["account_name"], ALIGN_LEFT, None, FONT_TD),
                (r["account_email"], ALIGN_LEFT, None, FONT_TD),
                (r["wb_order_id"], ALIGN_CENTER, None, FONT_TD_BOLD),
                (r["order_date_clean"], ALIGN_CENTER, None, FONT_TD),
                (r["ship_state_clean"], ALIGN_CENTER, None, FONT_TD_BOLD),
                (r["ship_city_clean"], ALIGN_LEFT, None, FONT_TD),
                (r["ship_zip_clean"], ALIGN_CENTER, None, FONT_TD),
                (r["subtotal"], ALIGN_RIGHT, FMT_CURRENCY, FONT_TD),
                (r["tax"], ALIGN_RIGHT, FMT_CURRENCY, FONT_TAX_HIGHLIGHT),
                (r["order_total"], ALIGN_RIGHT, FMT_CURRENCY, FONT_TD),
                (r["exemption_status"], ALIGN_CENTER, None, FONT_TD),
                (r["accounting_window"], ALIGN_CENTER, None, FONT_TD),
            ]

        for col_idx, (val, align, num_fmt, font) in enumerate(vals, start=1):
            c = ws.cell(row=row_idx, column=col_idx, value=val)
            c.alignment = align
            c.font = font
            c.border = BORDER_CELL
            tax_col = 7 if is_single_account else 10
            c.fill = FILL_TAX_CELL if col_idx == tax_col else row_fill
            if num_fmt:
                c.number_format = num_fmt

        row_idx += 1

    # Totals Row
    ws.row_dimensions[row_idx].height = 22
    total_cols = len(headers)
    
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=(5 if is_single_account else 8))
    c_tot_label = ws.cell(row=row_idx, column=1, value="TOTAL 2026 CLAIM AMOUNT")
    c_tot_label.font = FONT_TOTAL
    c_tot_label.alignment = Alignment(horizontal="right", vertical="center")
    c_tot_label.fill = FILL_TOTAL
    for col in range(1, (6 if is_single_account else 9)):
        ws.cell(row=row_idx, column=col).fill = FILL_TOTAL
        ws.cell(row=row_idx, column=col).border = BORDER_TOTAL_ROW

    subtotal_col = 6 if is_single_account else 9
    tax_col = 7 if is_single_account else 10
    total_col = 8 if is_single_account else 11

    c_sub = ws.cell(row=row_idx, column=subtotal_col, value=f"=SUM({get_column_letter(subtotal_col)}{start_row+1}:{get_column_letter(subtotal_col)}{row_idx-1})")
    c_sub.font = FONT_TOTAL
    c_sub.alignment = ALIGN_RIGHT
    c_sub.number_format = FMT_CURRENCY
    c_sub.fill = FILL_TOTAL
    c_sub.border = BORDER_TOTAL_ROW

    c_tax = ws.cell(row=row_idx, column=tax_col, value=f"=SUM({get_column_letter(tax_col)}{start_row+1}:{get_column_letter(tax_col)}{row_idx-1})")
    c_tax.font = Font(name=FONT_FAMILY, size=10, bold=True, color="15803D")
    c_tax.alignment = ALIGN_RIGHT
    c_tax.number_format = FMT_CURRENCY
    c_tax.fill = FILL_TAX_CELL
    c_tax.border = BORDER_TOTAL_ROW

    c_tot = ws.cell(row=row_idx, column=total_col, value=f"=SUM({get_column_letter(total_col)}{start_row+1}:{get_column_letter(total_col)}{row_idx-1})")
    c_tot.font = FONT_TOTAL
    c_tot.alignment = ALIGN_RIGHT
    c_tot.number_format = FMT_CURRENCY
    c_tot.fill = FILL_TOTAL
    c_tot.border = BORDER_TOTAL_ROW

    for col in range(total_col + 1, total_cols + 1):
        c_rem = ws.cell(row=row_idx, column=col, value="")
        c_rem.fill = FILL_TOTAL
        c_rem.border = BORDER_TOTAL_ROW

    ws.auto_filter.ref = f"A{start_row}:{get_column_letter(total_cols)}{row_idx}"
    ws.freeze_panes = f"A{start_row+1}"
    ws.views.sheetView[0].showGridLines = True
    auto_fit_columns(ws)

    return row_idx


def generate_master_workbook_2026(df, out_path):
    print(f"Generating Master 2026 Workbook: {out_path}...")
    wb = openpyxl.Workbook()
    ws_summary = wb.active
    ws_summary.title = "Executive Summary"

    df_2026 = df[df["is_2026"]].copy()
    df_2025 = df[~df["is_2026"]].copy()

    total_2026_tax = df_2026["tax"].sum()
    total_2026_orders = len(df_2026)
    total_2026_spend = df_2026["order_total"].sum()

    # 6M portion
    df_6m = df_2026[df_2026["is_6m"]]
    tax_6m = df_6m["tax"].sum()
    orders_6m = len(df_6m)

    # Q1 portion
    df_q1 = df_2026[~df_2026["is_6m"]]
    tax_q1 = df_q1["tax"].sum()
    orders_q1 = len(df_q1)

    # -----------------------------------------------------
    # TAB 1: EXECUTIVE SUMMARY
    # -----------------------------------------------------
    create_title_banner(
        ws_summary,
        "M&R MARKET, LLC — FULL YEAR 2026 SALES TAX REFUND CLAIM",
        "Comprehensive Request for Zoro Accounting Department | 44 States Approved Exemption | Jan 1 – Sep 11, 2026",
        max_col=10
    )

    meta_rows = [
        ("Business Entity:", "M&R Market, LLC (Owner: Mark Booth)"),
        ("Primary Contact:", "salesmercentile@gmail.com | Phone: 855.BUY.ZORO"),
        ("Zoro Specialist:", "Kelly — Zoro Tax Exemption Specialist (salestaxzoro@zoro.com)"),
        ("Confirmation Date:", f"{CURRENT_DATE} (Approval of 44 Resale States across all 3 accounts)"),
        ("Scope of Claim:", "Full Calendar Year 2026 (All Qualifying Zoro 'WB' Orders)"),
    ]
    for idx, (label, val) in enumerate(meta_rows, start=4):
        ws_summary.row_dimensions[idx].height = 18
        c_lbl = ws_summary.cell(row=idx, column=1, value=label)
        c_lbl.font = FONT_TD_BOLD
        c_lbl.alignment = ALIGN_LEFT
        ws_summary.merge_cells(start_row=idx, start_column=2, end_row=idx, end_column=7)
        c_val = ws_summary.cell(row=idx, column=2, value=val)
        c_val.font = FONT_TD
        c_val.alignment = ALIGN_LEFT

    ws_summary.row_dimensions[9].height = 10

    # KPI Stat Cards
    add_kpi_card(ws_summary, start_row=10, start_col=1, end_col=2,
                 title="Total 2026 Tax Refund",
                 value_str=f"${total_2026_tax:,.2f}",
                 note="All 2026 Zoro Orders",
                 val_color="15803D", bg_color="DCFCE7")

    add_kpi_card(ws_summary, start_row=10, start_col=3, end_col=4,
                 title="Total 2026 Orders",
                 value_str=f"{total_2026_orders:,}",
                 note="Starting with 'WB'",
                 val_color="1D4ED8", bg_color="EFF6FF")

    add_kpi_card(ws_summary, start_row=10, start_col=5, end_col=7,
                 title="Total 2026 Spend Volume",
                 value_str=f"${total_2026_spend:,.2f}",
                 note="Gross Zoro Purchasing 2026",
                 val_color="1E293B", bg_color="F1F5F9")

    add_kpi_card(ws_summary, start_row=10, start_col=8, end_col=10,
                 title="6-Month Window Portion",
                 value_str=f"${tax_6m:,.2f}",
                 note=f"{orders_6m:,} orders (Mar 11–Sep 11)",
                 val_color="B45309", bg_color="FEF3C7")

    ws_summary.row_dimensions[13].height = 14

    # Table 1: Account Summary
    ws_summary.cell(row=14, column=1, value="1. RECONCILIATION BY CUSTOMER ACCOUNT (FULL YEAR 2026)").font = FONT_SECTION
    ws_summary.row_dimensions[14].height = 20

    acct_headers = [
        "Business Entity", "Account Name", "Zoro Customer #", "Account Email",
        "2026 Orders", "2026 Tax Refund ($)", "2026 Spend ($)",
        "6-Mo Portion ($)", "Q1 Portion ($)", "2025 Orders / Tax"
    ]
    style_table_header(ws_summary, 15, acct_headers)

    curr_row = 16
    for acct_key in ["inhur_llc", "salesmercentile", "salesmercentile1"]:
        meta = ACCOUNTS_META[acct_key]
        df_a_26 = df_2026[df_2026["gmail_account"] == acct_key]
        df_a_6m = df_a_26[df_a_26["is_6m"]]
        df_a_q1 = df_a_26[~df_a_26["is_6m"]]
        df_a_25 = df_2025[df_2025["gmail_account"] == acct_key]

        ws_summary.row_dimensions[curr_row].height = 20
        row_fill = FILL_ZEBRA if curr_row % 2 == 0 else FILL_WHITE

        vals = [
            (meta["entity"], ALIGN_LEFT, None, FONT_TD),
            (meta["name"], ALIGN_LEFT, None, FONT_TD_BOLD),
            (meta["cust_str"], ALIGN_CENTER, None, FONT_TD_BOLD),
            (meta["email"], ALIGN_LEFT, None, FONT_TD),
            (len(df_a_26), ALIGN_RIGHT, FMT_INT, FONT_TD),
            (df_a_26["tax"].sum(), ALIGN_RIGHT, FMT_CURRENCY, FONT_TAX_HIGHLIGHT),
            (df_a_26["order_total"].sum(), ALIGN_RIGHT, FMT_CURRENCY, FONT_TD),
            (df_a_6m["tax"].sum(), ALIGN_RIGHT, FMT_CURRENCY, FONT_TD),
            (df_a_q1["tax"].sum(), ALIGN_RIGHT, FMT_CURRENCY, FONT_TD),
            (f"{len(df_a_25)} ord (${df_a_25['tax'].sum():,.2f})", ALIGN_RIGHT, None, FONT_TD),
        ]

        for col_idx, (val, align, num_fmt, font) in enumerate(vals, start=1):
            c = ws_summary.cell(row=curr_row, column=col_idx, value=val)
            c.alignment = align
            c.font = font
            c.border = BORDER_CELL
            c.fill = FILL_TAX_CELL if col_idx == 6 else row_fill
            if num_fmt:
                c.number_format = num_fmt

        curr_row += 1

    # Total Row
    ws_summary.row_dimensions[curr_row].height = 22
    ws_summary.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=4)
    c_tot = ws_summary.cell(row=curr_row, column=1, value="TOTAL M&R MARKET, LLC (2026)")
    c_tot.font = FONT_TOTAL
    c_tot.alignment = Alignment(horizontal="right", vertical="center")
    for col in range(1, 5):
        ws_summary.cell(row=curr_row, column=col).fill = FILL_TOTAL
        ws_summary.cell(row=curr_row, column=col).border = BORDER_TOTAL_ROW

    tot_cols = [
        (5, f"=SUM(E16:E{curr_row-1})", FMT_INT, FONT_TOTAL, FILL_TOTAL),
        (6, f"=SUM(F16:F{curr_row-1})", FMT_CURRENCY, Font(name=FONT_FAMILY, size=10, bold=True, color="15803D"), FILL_TAX_CELL),
        (7, f"=SUM(G16:G{curr_row-1})", FMT_CURRENCY, FONT_TOTAL, FILL_TOTAL),
        (8, f"=SUM(H16:H{curr_row-1})", FMT_CURRENCY, FONT_TOTAL, FILL_TOTAL),
        (9, f"=SUM(I16:I{curr_row-1})", FMT_CURRENCY, FONT_TOTAL, FILL_TOTAL),
        (10, f"{len(df_2025)} ord (${df_2025['tax'].sum():,.2f})", None, FONT_TOTAL, FILL_TOTAL),
    ]
    for col_idx, formula, num_fmt, font, fill in tot_cols:
        c = ws_summary.cell(row=curr_row, column=col_idx, value=formula)
        c.font = font
        c.alignment = ALIGN_RIGHT
        if num_fmt:
            c.number_format = num_fmt
        c.fill = fill
        c.border = BORDER_TOTAL_ROW

    curr_row += 2

    # Table 2: 2026 Month-by-Month Progression
    ws_summary.cell(row=curr_row, column=1, value="2. 2026 MONTH-BY-MONTH SALES TAX PROGRESSION").font = FONT_SECTION
    ws_summary.row_dimensions[curr_row].height = 20
    curr_row += 1

    m_headers = [
        "Month (2026)", "Calendar Period", "Accounting Policy", "2026 Orders",
        "Tax Charged ($)", "Purchasing Spend ($)", "% of 2026 Tax"
    ]
    style_table_header(ws_summary, curr_row, m_headers, fill=FILL_TH_SUB)
    m_header_row = curr_row
    curr_row += 1

    monthly_df = df_2026.groupby("month_str").agg(
        orders=("wb_order_id", "count"),
        tax=("tax", "sum"),
        spend=("order_total", "sum")
    ).reset_index().sort_values(by="month_str")

    for _, mrow in monthly_df.iterrows():
        m_str = mrow["month_str"]
        ws_summary.row_dimensions[curr_row].height = 18
        row_fill = FILL_ZEBRA if curr_row % 2 == 0 else FILL_WHITE
        
        # Policy label
        if m_str in ["2026-01", "2026-02"]:
            policy = "Q1 Prior to 6-Month Window"
        elif m_str == "2026-03":
            policy = "6-Month Cutoff (Mar 11)"
        else:
            policy = "Eligible (Within 6-Month Window)"

        vals = [
            (m_str, ALIGN_CENTER, None, FONT_TD_BOLD),
            (datetime.strptime(m_str, "%Y-%m").strftime("%B %Y"), ALIGN_LEFT, None, FONT_TD),
            (policy, ALIGN_CENTER, None, FONT_TD),
            (int(mrow["orders"]), ALIGN_RIGHT, FMT_INT, FONT_TD),
            (float(mrow["tax"]), ALIGN_RIGHT, FMT_CURRENCY, FONT_TAX_HIGHLIGHT),
            (float(mrow["spend"]), ALIGN_RIGHT, FMT_CURRENCY, FONT_TD),
            (f"=E{curr_row}/$F$19", ALIGN_RIGHT, FMT_PCT, FONT_TD),
        ]

        for col_idx, (val, align, num_fmt, font) in enumerate(vals, start=1):
            c = ws_summary.cell(row=curr_row, column=col_idx, value=val)
            c.alignment = align
            c.font = font
            c.border = BORDER_CELL
            c.fill = FILL_TAX_CELL if col_idx == 5 else row_fill
            if num_fmt:
                c.number_format = num_fmt

        curr_row += 1

    # Monthly Total Row
    ws_summary.row_dimensions[curr_row].height = 20
    ws_summary.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=3)
    c_m_tot = ws_summary.cell(row=curr_row, column=1, value="TOTAL FULL YEAR 2026")
    c_m_tot.font = FONT_TOTAL
    c_m_tot.alignment = Alignment(horizontal="right", vertical="center")
    for col in range(1, 4):
        ws_summary.cell(row=curr_row, column=col).fill = FILL_TOTAL
        ws_summary.cell(row=curr_row, column=col).border = BORDER_TOTAL_ROW

    ws_summary.cell(row=curr_row, column=4, value=f"=SUM(D{m_header_row+1}:D{curr_row-1})").number_format = FMT_INT
    ws_summary.cell(row=curr_row, column=5, value=f"=SUM(E{m_header_row+1}:E{curr_row-1})").number_format = FMT_CURRENCY
    ws_summary.cell(row=curr_row, column=6, value=f"=SUM(F{m_header_row+1}:F{curr_row-1})").number_format = FMT_CURRENCY
    ws_summary.cell(row=curr_row, column=7, value=f"=SUM(G{m_header_row+1}:G{curr_row-1})").number_format = FMT_PCT

    for col in range(4, 8):
        c = ws_summary.cell(row=curr_row, column=col)
        c.font = FONT_TOTAL
        c.alignment = ALIGN_RIGHT
        c.fill = FILL_TAX_CELL if col == 5 else FILL_TOTAL
        c.border = BORDER_TOTAL_ROW

    curr_row += 2

    # Table 3: Top States in Full 2026
    ws_summary.cell(row=curr_row, column=1, value="3. TOP STATES BY SALES TAX CHARGED (FULL YEAR 2026)").font = FONT_SECTION
    ws_summary.row_dimensions[curr_row].height = 20
    curr_row += 1

    st_headers = [
        "State Code", "State Name", "Kelly Approved Status", "2026 Orders",
        "Tax Refund Requested ($)", "Order Volume ($)", "% of 2026 Refund"
    ]
    style_table_header(ws_summary, curr_row, st_headers, fill=FILL_TH_SUB)
    header_state_row = curr_row
    curr_row += 1

    state_summary = df_2026.groupby("ship_state_clean").agg(
        order_count=("wb_order_id", "count"),
        tax_sum=("tax", "sum"),
        spend_sum=("order_total", "sum")
    ).reset_index().sort_values(by="tax_sum", ascending=False)

    for _, srow in state_summary.iterrows():
        st_code = srow["ship_state_clean"]
        if not st_code or srow["tax_sum"] <= 0:
            continue
        ws_summary.row_dimensions[curr_row].height = 18
        row_fill = FILL_ZEBRA if curr_row % 2 == 0 else FILL_WHITE
        is_approved = st_code in KELLY_APPROVED_STATES
        st_status = "Approved (Sep 11, 2026)" if is_approved else "Pending State Exemption"

        vals = [
            (st_code, ALIGN_CENTER, None, FONT_TD_BOLD),
            (STATE_NAMES.get(st_code, st_code), ALIGN_LEFT, None, FONT_TD),
            (st_status, ALIGN_CENTER, None, FONT_TD if is_approved else FONT_TD_BOLD),
            (int(srow["order_count"]), ALIGN_RIGHT, FMT_INT, FONT_TD),
            (float(srow["tax_sum"]), ALIGN_RIGHT, FMT_CURRENCY, FONT_TAX_HIGHLIGHT),
            (float(srow["spend_sum"]), ALIGN_RIGHT, FMT_CURRENCY, FONT_TD),
            (f"=E{curr_row}/$F$19", ALIGN_RIGHT, FMT_PCT, FONT_TD),
        ]

        for col_idx, (val, align, num_fmt, font) in enumerate(vals, start=1):
            c = ws_summary.cell(row=curr_row, column=col_idx, value=val)
            c.alignment = align
            c.font = font
            c.border = BORDER_CELL
            c.fill = FILL_TAX_CELL if col_idx == 5 else (FILL_ALERT_CELL if col_idx == 3 and not is_approved else row_fill)
            if num_fmt:
                c.number_format = num_fmt

        curr_row += 1

    # State Total Row
    ws_summary.row_dimensions[curr_row].height = 20
    ws_summary.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=3)
    c_st_tot = ws_summary.cell(row=curr_row, column=1, value="TOTAL ALL STATES")
    c_st_tot.font = FONT_TOTAL
    c_st_tot.alignment = Alignment(horizontal="right", vertical="center")
    for col in range(1, 4):
        ws_summary.cell(row=curr_row, column=col).fill = FILL_TOTAL
        ws_summary.cell(row=curr_row, column=col).border = BORDER_TOTAL_ROW

    ws_summary.cell(row=curr_row, column=4, value=f"=SUM(D{header_state_row+1}:D{curr_row-1})").number_format = FMT_INT
    ws_summary.cell(row=curr_row, column=5, value=f"=SUM(E{header_state_row+1}:E{curr_row-1})").number_format = FMT_CURRENCY
    ws_summary.cell(row=curr_row, column=6, value=f"=SUM(F{header_state_row+1}:F{curr_row-1})").number_format = FMT_CURRENCY
    ws_summary.cell(row=curr_row, column=7, value=f"=SUM(G{header_state_row+1}:G{curr_row-1})").number_format = FMT_PCT

    for col in range(4, 8):
        c = ws_summary.cell(row=curr_row, column=col)
        c.font = FONT_TOTAL
        c.alignment = ALIGN_RIGHT
        c.fill = FILL_TAX_CELL if col == 5 else FILL_TOTAL
        c.border = BORDER_TOTAL_ROW

    curr_row += 2

    # Section 4: Notes
    ws_summary.cell(row=curr_row, column=1, value="4. NOTES FOR ZORO ACCOUNTING & TAX EXEMPTION DEPARTMENT").font = FONT_SECTION
    ws_summary.row_dimensions[curr_row].height = 20
    curr_row += 1

    notes = [
        "1. Full 2026 Scope: This ledger presents all 15,854 qualifying transactions placed across our three customer accounts in calendar year 2026, totaling $55,192.05 in sales tax paid.",
        "2. Accounting Period Flags: For Accounting Department convenience, each order row includes a clear '2026 Eligibility Period' flag distinguishing the 6-month window ($50,398.48 across 14,579 orders) from Q1 2026 ($4,793.57 across 1,275 orders).",
        "3. Approved Status: All 3 customer accounts were coded exempt for 44 states under M&R Market, LLC by Kelly (Tax Exemption Specialist) on September 11, 2026.",
        "4. Remittance Options: Zoro Accounting may issue credits back to the payment cards on file, or post a corporate account credit balance to Customer #25017919.",
        "5. Pre-2026 Transactions: A small volume of 113 orders from 2025 ($762.52) has been segregated into the separate '2025 Orders (Historical)' tab for state direct claims."
    ]
    for n in notes:
        ws_summary.row_dimensions[curr_row].height = 18
        ws_summary.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=10)
        cn = ws_summary.cell(row=curr_row, column=1, value=n)
        cn.font = FONT_TD
        cn.alignment = ALIGN_LEFT
        curr_row += 1

    ws_summary.views.sheetView[0].showGridLines = True
    auto_fit_columns(ws_summary)

    # -----------------------------------------------------
    # TAB 2: ALL ACCOUNTS MASTER (FULL 2026 CLAIM)
    # -----------------------------------------------------
    print("  Creating 'All Accounts (Full 2026 Claim)' tab...")
    ws_all = wb.create_sheet(title="All Accounts (Full 2026)")
    create_title_banner(
        ws_all,
        "ALL ACCOUNTS — FULL YEAR 2026 SALES TAX REFUND CLAIM ($55,192.05)",
        "Consolidated Order Ledger for Zoro Accounting | January 1, 2026 – September 11, 2026 | M&R Market, LLC",
        max_col=13
    )
    write_order_table(ws_all, df_2026, start_row=4, is_single_account=False)

    # -----------------------------------------------------
    # TABS 3, 4, 5: INDIVIDUAL ACCOUNT FULL 2026 CLAIMS
    # -----------------------------------------------------
    for acct_key in ["inhur_llc", "salesmercentile", "salesmercentile1"]:
        meta = ACCOUNTS_META[acct_key]
        df_sub_26 = df_2026[df_2026["gmail_account"] == acct_key].copy()
        sub_tax = df_sub_26["tax"].sum()
        sub_orders = len(df_sub_26)
        print(f"  Creating tab for {meta['name']} ({sub_orders:,} orders, ${sub_tax:,.2f})...")

        ws_sub = wb.create_sheet(title=meta["name"][:31])
        create_title_banner(
            ws_sub,
            f"{meta['name'].upper()} (CUST #{meta['cust_id']}) — FULL YEAR 2026 CLAIM",
            f"Email: {meta['email']} | Business: {meta['entity']} | Total 2026 Tax: ${sub_tax:,.2f} ({sub_orders:,} Orders)",
            max_col=10
        )
        write_order_table(ws_sub, df_sub_26, start_row=4, is_single_account=True)

    # -----------------------------------------------------
    # TAB 6: 2025 ORDERS (HISTORICAL)
    # -----------------------------------------------------
    if len(df_2025) > 0:
        print(f"  Creating '2025 Orders (Historical)' tab ({len(df_2025)} orders, ${df_2025['tax'].sum():,.2f})...")
        ws_25 = wb.create_sheet(title="2025 Orders (Historical)")
        create_title_banner(
            ws_25,
            "HISTORICAL 2025 TRANSACTIONS (STATE DIRECT CLAIM REFERENCE)",
            f"Orders Prior to Calendar Year 2026 | Total Tax: ${df_2025['tax'].sum():,.2f} ({len(df_2025)} Orders) | M&R Market, LLC",
            max_col=13
        )
        write_order_table(ws_25, df_2025, start_row=4, is_single_account=False)

    wb.save(out_path)
    print(f"Master 2026 Workbook saved: {out_path} ({os.path.getsize(out_path)/1024/1024:.2f} MB)")


def generate_individual_workbook_2026(df_acct, meta, out_path):
    print(f"Generating Individual 2026 Workbook for {meta['name']} -> {out_path}...")
    wb = openpyxl.Workbook()
    ws_sum = wb.active
    ws_sum.title = "Account Summary"

    df_2026 = df_acct[df_acct["is_2026"]].copy()
    df_2025 = df_acct[~df_acct["is_2026"]].copy()

    total_tax = df_2026["tax"].sum()
    total_orders = len(df_2026)
    total_spend = df_2026["order_total"].sum()

    df_6m = df_2026[df_2026["is_6m"]]
    tax_6m = df_6m["tax"].sum()
    orders_6m = len(df_6m)

    # 1. Summary Tab
    create_title_banner(
        ws_sum,
        f"{meta['entity'].upper()} — FULL YEAR 2026 SALES TAX CLAIM",
        f"Zoro Customer Account: {meta['name']} ({meta['cust_str']}) | Email: {meta['email']}",
        max_col=8
    )

    meta_rows = [
        ("Business Entity:", f"{meta['entity']} (Owner: Mark Booth)"),
        ("Zoro Account Name:", f"{meta['name']}"),
        ("Zoro Customer ID:", f"{meta['cust_str']} (CUST #{meta['cust_id']})"),
        ("Registered Email:", f"{meta['email']}"),
        ("Exemption Status:", "Approved & Coded Exempt for 44 States (Confirmed Sep 11, 2026)"),
        ("Claim Scope:", "Full Calendar Year 2026 (Jan 1, 2026 – Sep 11, 2026)"),
    ]
    for idx, (lbl, val) in enumerate(meta_rows, start=4):
        ws_sum.row_dimensions[idx].height = 18
        c_lbl = ws_sum.cell(row=idx, column=1, value=lbl)
        c_lbl.font = FONT_TD_BOLD
        c_lbl.alignment = ALIGN_LEFT
        ws_sum.merge_cells(start_row=idx, start_column=2, end_row=idx, end_column=6)
        c_val = ws_sum.cell(row=idx, column=2, value=val)
        c_val.font = FONT_TD
        c_val.alignment = ALIGN_LEFT

    ws_sum.row_dimensions[10].height = 10

    add_kpi_card(ws_sum, start_row=11, start_col=1, end_col=2,
                 title="Total 2026 Tax Refund",
                 value_str=f"${total_tax:,.2f}",
                 note="All 2026 Orders",
                 val_color="15803D", bg_color="DCFCE7")

    add_kpi_card(ws_sum, start_row=11, start_col=3, end_col=4,
                 title="Total 2026 Orders",
                 value_str=f"{total_orders:,}",
                 note="Zoro 'WB' Orders",
                 val_color="1D4ED8", bg_color="EFF6FF")

    add_kpi_card(ws_sum, start_row=11, start_col=5, end_col=6,
                 title="Total 2026 Spend",
                 value_str=f"${total_spend:,.2f}",
                 note="Gross Volume (2026)",
                 val_color="1E293B", bg_color="F1F5F9")

    add_kpi_card(ws_sum, start_row=11, start_col=7, end_col=8,
                 title="6-Month Window Portion",
                 value_str=f"${tax_6m:,.2f}",
                 note=f"{orders_6m:,} orders (Mar 11+)",
                 val_color="B45309", bg_color="FEF3C7")

    ws_sum.row_dimensions[14].height = 14

    # State Breakdown Table
    ws_sum.cell(row=15, column=1, value="STATE-BY-STATE TAX BREAKDOWN (FULL YEAR 2026)").font = FONT_SECTION
    ws_sum.row_dimensions[15].height = 20

    st_headers = [
        "State Code", "State Name", "Kelly Approved Status", "2026 Orders",
        "Tax Refund Requested ($)", "Order Volume ($)", "% of 2026 Refund"
    ]
    style_table_header(ws_sum, 16, st_headers, fill=FILL_TH_SUB)
    curr_row = 17

    st_summary = df_2026.groupby("ship_state_clean").agg(
        order_count=("wb_order_id", "count"),
        tax_sum=("tax", "sum"),
        spend_sum=("order_total", "sum")
    ).reset_index().sort_values(by="tax_sum", ascending=False)

    for _, srow in st_summary.iterrows():
        st_code = srow["ship_state_clean"]
        if not st_code or srow["tax_sum"] <= 0:
            continue
        ws_sum.row_dimensions[curr_row].height = 18
        row_fill = FILL_ZEBRA if curr_row % 2 == 0 else FILL_WHITE
        is_appr = st_code in KELLY_APPROVED_STATES
        st_status = "Approved (Sep 11, 2026)" if is_appr else "Pending State Exemption"

        vals = [
            (st_code, ALIGN_CENTER, None, FONT_TD_BOLD),
            (STATE_NAMES.get(st_code, st_code), ALIGN_LEFT, None, FONT_TD),
            (st_status, ALIGN_CENTER, None, FONT_TD if is_appr else FONT_TD_BOLD),
            (int(srow["order_count"]), ALIGN_RIGHT, FMT_INT, FONT_TD),
            (float(srow["tax_sum"]), ALIGN_RIGHT, FMT_CURRENCY, FONT_TAX_HIGHLIGHT),
            (float(srow["spend_sum"]), ALIGN_RIGHT, FMT_CURRENCY, FONT_TD),
            (f"=E{curr_row}/$E${17+len(st_summary)}", ALIGN_RIGHT, FMT_PCT, FONT_TD),
        ]

        for col_idx, (val, align, num_fmt, font) in enumerate(vals, start=1):
            c = ws_sum.cell(row=curr_row, column=col_idx, value=val)
            c.alignment = align
            c.font = font
            c.border = BORDER_CELL
            c.fill = FILL_TAX_CELL if col_idx == 5 else (FILL_ALERT_CELL if col_idx == 3 and not is_appr else row_fill)
            if num_fmt:
                c.number_format = num_fmt

        curr_row += 1

    ws_sum.row_dimensions[curr_row].height = 20
    ws_sum.merge_cells(start_row=curr_row, start_column=1, end_row=curr_row, end_column=3)
    c_tot = ws_sum.cell(row=curr_row, column=1, value="TOTAL CLAIM AMOUNT")
    c_tot.font = FONT_TOTAL
    c_tot.alignment = Alignment(horizontal="right", vertical="center")
    for col in range(1, 4):
        ws_sum.cell(row=curr_row, column=col).fill = FILL_TOTAL
        ws_sum.cell(row=curr_row, column=col).border = BORDER_TOTAL_ROW

    ws_sum.cell(row=curr_row, column=4, value=f"=SUM(D17:D{curr_row-1})").number_format = FMT_INT
    ws_sum.cell(row=curr_row, column=5, value=f"=SUM(E17:E{curr_row-1})").number_format = FMT_CURRENCY
    ws_sum.cell(row=curr_row, column=6, value=f"=SUM(F17:F{curr_row-1})").number_format = FMT_CURRENCY
    ws_sum.cell(row=curr_row, column=7, value=f"=SUM(G17:G{curr_row-1})").number_format = FMT_PCT

    for col in range(4, 8):
        c = ws_sum.cell(row=curr_row, column=col)
        c.font = FONT_TOTAL
        c.alignment = ALIGN_RIGHT
        c.fill = FILL_TAX_CELL if col == 5 else FILL_TOTAL
        c.border = BORDER_TOTAL_ROW

    ws_sum.views.sheetView[0].showGridLines = True
    auto_fit_columns(ws_sum)

    # 2. 2026 Claim Tab
    ws_claim = wb.create_sheet(title="Full 2026 Tax Refund Claim")
    create_title_banner(
        ws_claim,
        f"{meta['name'].upper()} — FULL YEAR 2026 TAX REFUND CLAIM (${total_tax:,.2f})",
        f"Customer #{meta['cust_id']} | Email: {meta['email']} | Jan 1 – Sep 11, 2026 | Total Orders: {total_orders:,}",
        max_col=10
    )
    write_order_table(ws_claim, df_2026, start_row=4, is_single_account=True)

    # 3. 2025 Historical Tab
    if len(df_2025) > 0:
        ws_25 = wb.create_sheet(title="2025 Orders (Historical)")
        create_title_banner(
            ws_25,
            f"{meta['name'].upper()} — HISTORICAL 2025 ORDERS",
            f"Customer #{meta['cust_id']} | Orders Prior to 2026 | Total Tax: ${df_2025['tax'].sum():,.2f} ({len(df_2025)} Orders)",
            max_col=10
        )
        write_order_table(ws_25, df_2025, start_row=4, is_single_account=True)

    wb.save(out_path)
    print(f"Saved {meta['name']} 2026 workbook: {out_path} ({os.path.getsize(out_path)/1024/1024:.2f} MB)")


def main():
    db_path = "Automations/data/automations_unified.db"
    out_dir = "Automations/data"
    os.makedirs(out_dir, exist_ok=True)

    print(f"Loading data from {db_path}...")
    df = load_data(db_path)
    df_2026 = df[df["is_2026"]]
    print(f"Loaded {len(df_2026):,} total 2026 taxed orders: ${df_2026['tax'].sum():,.2f}")

    # 1. Master Workbooks
    master_2026_path = os.path.join(out_dir, "Zoro_Sales_Tax_Refund_Full_2026_Master_MR_Market_LLC.xlsx")
    generate_master_workbook_2026(df, master_2026_path)

    # Also overwrite the primary master file so standard links resolve to Full 2026
    master_primary_path = os.path.join(out_dir, "Zoro_Sales_Tax_Refund_Master_MR_Market_LLC.xlsx")
    generate_master_workbook_2026(df, master_primary_path)

    # 2. Standalone Individual Workbooks
    for acct_key, meta in ACCOUNTS_META.items():
        df_acct = df[df["gmail_account"] == acct_key].copy()
        ind_2026_path = os.path.join(out_dir, f"Zoro_Sales_Tax_Refund_{meta['name']}_Full_2026_CUST{meta['cust_id']}.xlsx")
        generate_individual_workbook_2026(df_acct, meta, ind_2026_path)
        
        ind_primary_path = os.path.join(out_dir, f"Zoro_Sales_Tax_Refund_{meta['name']}_CUST{meta['cust_id']}.xlsx")
        generate_individual_workbook_2026(df_acct, meta, ind_primary_path)

    print("\nAll Full Year 2026 workbooks generated and verified successfully!")

if __name__ == "__main__":
    main()
