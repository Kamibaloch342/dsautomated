"""
generate_single_workbook_with_top_summaries.py

Builds 1 master Excel workbook containing dedicated tabs for:
  1. Master Overview (All Accounts 2026 - Summary at Top + All 15,854 WB orders below)
  2. inuhrecomllc (Summary at Top + 13,531 WB orders below)
  3. salesmercentile (Summary at Top + 1,281 WB orders below)
  4. salesmercentile1 (Summary at Top + 1,042 WB orders below)
  5. 2025 Historical (Summary at Top + 113 WB orders below)

On every tab:
  - Top: Title banner & 4 Executive KPI Stat Cards (Total Tax Refund, Orders, Spend, 6-Mo vs Q1)
  - Row 8: Table Header starting prominently with "Zoro Order # (WB)"
  - Row 9+: Itemized order ledger starting with "WB..."
  - Bottom: Bold Total Claim Row with Excel SUM formulas
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
        "tab_name": "inuhrecomllc (CUST25217105)",
    },
    "salesmercentile": {
        "cust_id": "25017919",
        "cust_str": "CUST25017919",
        "name": "salesmercentile",
        "email": "salesmercentile@gmail.com",
        "entity": "M&R Market, LLC",
        "tab_name": "salesmercentile (CUST25017919)",
    },
    "salesmercentile1": {
        "cust_id": "25061417",
        "cust_str": "CUST25061417",
        "name": "salesmercentile1",
        "email": "salesmercentil1@gmail.com",
        "entity": "M&R Market, LLC",
        "tab_name": "salesmercentile1 (CUST25061417)",
    },
}

FONT_FAMILY = "Segoe UI"
FONT_TITLE = Font(name=FONT_FAMILY, size=13, bold=True, color="FFFFFF")
FONT_SUBTITLE = Font(name=FONT_FAMILY, size=9.5, italic=True, color="E2E8F0")
FONT_TH = Font(name=FONT_FAMILY, size=9.5, bold=True, color="FFFFFF")
FONT_TD = Font(name=FONT_FAMILY, size=9, color="1E293B")
FONT_TD_BOLD = Font(name=FONT_FAMILY, size=9, bold=True, color="1E293B")
FONT_TOTAL = Font(name=FONT_FAMILY, size=10, bold=True, color="0F172A")
FONT_TAX_HIGHLIGHT = Font(name=FONT_FAMILY, size=9.5, bold=True, color="15803D")

FILL_TITLE = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
FILL_TH = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
FILL_ZEBRA = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
FILL_WHITE = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
FILL_TOTAL = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
FILL_TAX_CELL = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")

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
    
    df["is_2026"] = (df["dt_email"].dt.year == 2026) | (df["dt_order"].dt.year == 2026)
    
    df["order_date_clean"] = df["dt_order"].dt.strftime("%Y-%m-%d")
    df["order_date_clean"] = df["order_date_clean"].fillna(df["dt_email"].dt.strftime("%Y-%m-%d"))
    
    df["ship_state_clean"] = df["ship_state"].fillna("").astype(str).str.strip().str.upper()
    df["ship_city_clean"] = df["ship_city"].fillna("").astype(str).str.strip().str.title()
    df["ship_zip_clean"] = df["ship_zip"].fillna("").astype(str).str.strip()
    
    df["subtotal"] = pd.to_numeric(df["subtotal"], errors="coerce").fillna(0.0)
    df["tax"] = pd.to_numeric(df["tax"], errors="coerce").fillna(0.0)
    df["order_total"] = pd.to_numeric(df["order_total"], errors="coerce").fillna(0.0)

    df["is_6m"] = (df["dt_email"] >= CUTOFF_6M) | (df["dt_order"] >= CUTOFF_6M)
    df["is_approved_state"] = df["ship_state_clean"].isin(KELLY_APPROVED_STATES)
    
    df["exemption_status"] = df["is_approved_state"].apply(
        lambda x: "Approved 44-State List" if x else "Pending State Exemption"
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


def add_kpi_card(ws, start_row, start_col, end_col, title, value_str, note, val_color="1E3A8A", bg_color="F1F5F9"):
    ws.merge_cells(start_row=start_row, start_column=start_col, end_row=start_row, end_column=end_col)
    c_title = ws.cell(row=start_row, column=start_col, value=title.upper())
    c_title.font = Font(name=FONT_FAMILY, size=8, bold=True, color="64748B")
    c_title.alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells(start_row=start_row+1, start_column=start_col, end_row=start_row+1, end_column=end_col)
    c_val = ws.cell(row=start_row+1, column=start_col, value=value_str)
    c_val.font = Font(name=FONT_FAMILY, size=13, bold=True, color=val_color)
    c_val.alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells(start_row=start_row+2, start_column=start_col, end_row=start_row+2, end_column=end_col)
    c_note = ws.cell(row=start_row+2, column=start_col, value=note)
    c_note.font = Font(name=FONT_FAMILY, size=7.5, italic=True, color="64748B")
    c_note.alignment = Alignment(horizontal="center", vertical="center")

    card_fill = PatternFill(start_color=bg_color, end_color=bg_color, fill_type="solid")
    for r in range(start_row, start_row + 3):
        for c in range(start_col, end_col + 1):
            cell = ws.cell(row=r, column=c)
            cell.fill = card_fill
            cell.border = BORDER_CELL
    
    ws.row_dimensions[start_row].height = 14
    ws.row_dimensions[start_row+1].height = 20
    ws.row_dimensions[start_row+2].height = 13


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


def build_tab_with_top_summary(ws, df_tab, title_main, subtitle_main, is_all_accounts=False):
    """
    Builds a tab that has:
      - Rows 1-2: Title banner
      - Row 3: Spacer
      - Rows 4-6: 4 KPI Cards (Total Tax Refund, Total Orders, Spend, 6-Mo Portion)
      - Row 7: Spacer
      - Row 8: Table Header (Starting with Zoro Order # WB...)
      - Row 9+: Data rows starting with WB order
      - Bottom: Total row
    """
    total_tax = df_tab["tax"].sum()
    total_orders = len(df_tab)
    total_spend = df_tab["order_total"].sum()
    
    df_6m = df_tab[df_tab["is_6m"]]
    tax_6m = df_6m["tax"].sum()
    orders_6m = len(df_6m)

    df_q1 = df_tab[~df_tab["is_6m"]]
    tax_q1 = df_q1["tax"].sum()
    orders_q1 = len(df_q1)

    max_col = 12 if is_all_accounts else 10

    # 1. Banner
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)
    c1 = ws.cell(row=1, column=1, value=title_main)
    c1.font = FONT_TITLE
    c1.fill = FILL_TITLE
    c1.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 24

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max_col)
    c2 = ws.cell(row=2, column=1, value=subtitle_main)
    c2.font = FONT_SUBTITLE
    c2.fill = FILL_TITLE
    c2.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 18

    ws.row_dimensions[3].height = 6

    # 2. KPI Cards (Rows 4-6)
    if is_all_accounts:
        # 12 columns -> 4 cards of 3 cols each
        add_kpi_card(ws, start_row=4, start_col=1, end_col=3,
                     title="Total 2026 Tax Refund",
                     value_str=f"${total_tax:,.2f}",
                     note=f"Across {total_orders:,} Orders",
                     val_color="15803D", bg_color="DCFCE7")

        add_kpi_card(ws, start_row=4, start_col=4, end_col=6,
                     title="6-Month Window (Mar 11+)",
                     value_str=f"${tax_6m:,.2f}",
                     note=f"{orders_6m:,} Zoro 'WB' Orders",
                     val_color="1D4ED8", bg_color="EFF6FF")

        add_kpi_card(ws, start_row=4, start_col=7, end_col=9,
                     title="Q1 2026 Portion",
                     value_str=f"${tax_q1:,.2f}",
                     note=f"{orders_q1:,} Orders (Jan 1–Mar 10)",
                     val_color="B45309", bg_color="FEF3C7")

        add_kpi_card(ws, start_row=4, start_col=10, end_col=12,
                     title="Total 2026 Spend",
                     value_str=f"${total_spend:,.2f}",
                     note="Zoro Purchasing Volume",
                     val_color="1E293B", bg_color="F1F5F9")
    else:
        # 10 columns -> card spans: (1-2), (3-4), (5-7), (8-10)
        add_kpi_card(ws, start_row=4, start_col=1, end_col=2,
                     title="Total 2026 Tax",
                     value_str=f"${total_tax:,.2f}",
                     note=f"{total_orders:,} Orders",
                     val_color="15803D", bg_color="DCFCE7")

        add_kpi_card(ws, start_row=4, start_col=3, end_col=4,
                     title="6-Mo Portion",
                     value_str=f"${tax_6m:,.2f}",
                     note=f"{orders_6m:,} Orders",
                     val_color="1D4ED8", bg_color="EFF6FF")

        add_kpi_card(ws, start_row=4, start_col=5, end_col=7,
                     title="Q1 2026 Portion",
                     value_str=f"${tax_q1:,.2f}",
                     note=f"{orders_q1:,} Orders (Jan-Mar)",
                     val_color="B45309", bg_color="FEF3C7")

        add_kpi_card(ws, start_row=4, start_col=8, end_col=10,
                     title="Total Spend",
                     value_str=f"${total_spend:,.2f}",
                     note="Gross Volume",
                     val_color="1E293B", bg_color="F1F5F9")

    ws.row_dimensions[7].height = 8

    # 3. Table Header (Row 8) - Starts prominently with Zoro Order # WB
    if is_all_accounts:
        headers = [
            "Zoro Order # (WB)", "Order Date", "Customer #", "Account Name", "Account Email",
            "Ship State", "Ship City", "Ship Zip",
            "Item Subtotal ($)", "Sales Tax Charged ($)", "Order Total ($)",
            "Approved Resale State?"
        ]
    else:
        headers = [
            "Zoro Order # (WB)", "Order Date", "Ship State", "Ship City", "Ship Zip",
            "Item Subtotal ($)", "Sales Tax Charged ($)", "Order Total ($)",
            "Approved Resale State?", "2026 Eligibility Period"
        ]

    ws.row_dimensions[8].height = 24
    for col_idx, h in enumerate(headers, start=1):
        c = ws.cell(row=8, column=col_idx, value=h)
        c.font = FONT_TH
        c.fill = FILL_TH
        c.alignment = ALIGN_HEADER
        c.border = BORDER_HEADER

    # 4. Data Rows starting at Row 9
    start_row = 8
    row_idx = 9
    for _, r in df_tab.iterrows():
        is_even = ((row_idx - start_row) % 2 == 0)
        row_fill = FILL_ZEBRA if is_even else FILL_WHITE
        ws.row_dimensions[row_idx].height = 18

        if is_all_accounts:
            vals = [
                (r["wb_order_id"], ALIGN_CENTER, None, FONT_TD_BOLD),
                (r["order_date_clean"], ALIGN_CENTER, None, FONT_TD),
                (r["cust_id"], ALIGN_CENTER, None, FONT_TD),
                (r["account_name"], ALIGN_LEFT, None, FONT_TD),
                (r["account_email"], ALIGN_LEFT, None, FONT_TD),
                (r["ship_state_clean"], ALIGN_CENTER, None, FONT_TD_BOLD),
                (r["ship_city_clean"], ALIGN_LEFT, None, FONT_TD),
                (r["ship_zip_clean"], ALIGN_CENTER, None, FONT_TD),
                (r["subtotal"], ALIGN_RIGHT, FMT_CURRENCY, FONT_TD),
                (r["tax"], ALIGN_RIGHT, FMT_CURRENCY, FONT_TAX_HIGHLIGHT),
                (r["order_total"], ALIGN_RIGHT, FMT_CURRENCY, FONT_TD),
                (r["exemption_status"], ALIGN_CENTER, None, FONT_TD),
            ]
        else:
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

        for col_idx, (val, align, num_fmt, font) in enumerate(vals, start=1):
            c = ws.cell(row=row_idx, column=col_idx, value=val)
            c.alignment = align
            c.font = font
            c.border = BORDER_CELL
            tax_col = 10 if is_all_accounts else 7
            c.fill = FILL_TAX_CELL if col_idx == tax_col else row_fill
            if num_fmt:
                c.number_format = num_fmt

        row_idx += 1

    # 5. Bottom Total Row
    ws.row_dimensions[row_idx].height = 22
    total_cols = len(headers)
    
    label_end_col = 8 if is_all_accounts else 5
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=label_end_col)
    c_tot_label = ws.cell(row=row_idx, column=1, value="TOTAL CLAIM AMOUNT")
    c_tot_label.font = FONT_TOTAL
    c_tot_label.alignment = Alignment(horizontal="right", vertical="center")
    c_tot_label.fill = FILL_TOTAL
    for col in range(1, label_end_col + 1):
        ws.cell(row=row_idx, column=col).fill = FILL_TOTAL
        ws.cell(row=row_idx, column=col).border = BORDER_TOTAL_ROW

    subtotal_col = 9 if is_all_accounts else 6
    tax_col = 10 if is_all_accounts else 7
    total_col = 11 if is_all_accounts else 8

    c_sub = ws.cell(row=row_idx, column=subtotal_col, value=f"=SUM({get_column_letter(subtotal_col)}9:{get_column_letter(subtotal_col)}{row_idx-1})")
    c_sub.font = FONT_TOTAL
    c_sub.alignment = ALIGN_RIGHT
    c_sub.number_format = FMT_CURRENCY
    c_sub.fill = FILL_TOTAL
    c_sub.border = BORDER_TOTAL_ROW

    c_tax = ws.cell(row=row_idx, column=tax_col, value=f"=SUM({get_column_letter(tax_col)}9:{get_column_letter(tax_col)}{row_idx-1})")
    c_tax.font = Font(name=FONT_FAMILY, size=10, bold=True, color="15803D")
    c_tax.alignment = ALIGN_RIGHT
    c_tax.number_format = FMT_CURRENCY
    c_tax.fill = FILL_TAX_CELL
    c_tax.border = BORDER_TOTAL_ROW

    c_tot = ws.cell(row=row_idx, column=total_col, value=f"=SUM({get_column_letter(total_col)}9:{get_column_letter(total_col)}{row_idx-1})")
    c_tot.font = FONT_TOTAL
    c_tot.alignment = ALIGN_RIGHT
    c_tot.number_format = FMT_CURRENCY
    c_tot.fill = FILL_TOTAL
    c_tot.border = BORDER_TOTAL_ROW

    for col in range(total_col + 1, total_cols + 1):
        c_rem = ws.cell(row=row_idx, column=col, value="")
        c_rem.fill = FILL_TOTAL
        c_rem.border = BORDER_TOTAL_ROW

    ws.auto_filter.ref = f"A8:{get_column_letter(total_cols)}{row_idx}"
    ws.freeze_panes = "A9"  # Freeze top 8 rows so summary & headers stay pinned!
    ws.views.sheetView[0].showGridLines = True
    auto_fit_columns(ws)


def main():
    db_path = "Automations/data/automations_unified.db"
    out_path = "Automations/data/Zoro_Sales_Tax_Refund_2026_All_Accounts.xlsx"

    print("Loading data from database...")
    df = load_data(db_path)
    df_2026 = df[df["is_2026"]].copy()
    df_2025 = df[~df["is_2026"]].copy()

    total_2026_tax = df_2026["tax"].sum()
    total_2026_orders = len(df_2026)

    print(f"Loaded {total_2026_orders:,} total 2026 orders | Tax: ${total_2026_tax:,.2f}")

    wb = openpyxl.Workbook()

    # ----------------------------------------------------
    # TAB 1: ALL ACCOUNTS MASTER (FULL 2026)
    # ----------------------------------------------------
    print("Building Tab 1: All Accounts Master...")
    ws1 = wb.active
    ws1.title = "All Accounts (Full 2026)"
    build_tab_with_top_summary(
        ws1,
        df_2026,
        title_main="M&R MARKET, LLC — ALL ACCOUNTS 2026 SALES TAX REFUND CLAIM ($55,192.05)",
        subtitle_main="Master Consolidated Ledger | 3 Customer Accounts | 44 States Resale Exemption Approved by Zoro",
        is_all_accounts=True
    )

    # ----------------------------------------------------
    # TAB 2: inuhrecomllc (CUST25217105)
    # ----------------------------------------------------
    print("Building Tab 2: inuhrecomllc...")
    df_inuhr = df_2026[df_2026["gmail_account"] == "inhur_llc"].copy()
    ws2 = wb.create_sheet(title="inuhrecomllc")
    build_tab_with_top_summary(
        ws2,
        df_inuhr,
        title_main=f"INUHRECOMLLC — 2026 SALES TAX REFUND CLAIM (${df_inuhr['tax'].sum():,.2f})",
        subtitle_main="Customer #25217105 | inuhrecomllc5@gmail.com | M&R Market, LLC | 44 States Approved Exemption",
        is_all_accounts=False
    )

    # ----------------------------------------------------
    # TAB 3: salesmercentile (CUST25017919)
    # ----------------------------------------------------
    print("Building Tab 3: salesmercentile...")
    df_sm = df_2026[df_2026["gmail_account"] == "salesmercentile"].copy()
    ws3 = wb.create_sheet(title="salesmercentile")
    build_tab_with_top_summary(
        ws3,
        df_sm,
        title_main=f"SALESMERCENTILE — 2026 SALES TAX REFUND CLAIM (${df_sm['tax'].sum():,.2f})",
        subtitle_main="Customer #25017919 | salesmercentile@gmail.com | M&R Market, LLC | 44 States Approved Exemption",
        is_all_accounts=False
    )

    # ----------------------------------------------------
    # TAB 4: salesmercentile1 (CUST25061417)
    # ----------------------------------------------------
    print("Building Tab 4: salesmercentile1...")
    df_sm1 = df_2026[df_2026["gmail_account"] == "salesmercentile1"].copy()
    ws4 = wb.create_sheet(title="salesmercentile1")
    build_tab_with_top_summary(
        ws4,
        df_sm1,
        title_main=f"SALESMERCENTILE1 — 2026 SALES TAX REFUND CLAIM (${df_sm1['tax'].sum():,.2f})",
        subtitle_main="Customer #25061417 | salesmercentil1@gmail.com | M&R Market, LLC | 44 States Approved Exemption",
        is_all_accounts=False
    )

    # ----------------------------------------------------
    # TAB 5: 2025 Historical Orders (113 orders)
    # ----------------------------------------------------
    if len(df_2025) > 0:
        print(f"Building Tab 5: 2025 Historical Orders ({len(df_2025)} orders)...")
        ws5 = wb.create_sheet(title="2025 Historical Orders")
        build_tab_with_top_summary(
            ws5,
            df_2025,
            title_main=f"2025 HISTORICAL TRANSACTIONS (STATE DIRECT FILING REFERENCE: ${df_2025['tax'].sum():,.2f})",
            subtitle_main=f"Orders Prior to Calendar Year 2026 | {len(df_2025)} Orders | M&R Market, LLC",
            is_all_accounts=True
        )

    print(f"Saving workbook to {out_path}...")
    wb.save(out_path)
    print(f"Saved: {out_path} ({os.path.getsize(out_path)/1024/1024:.2f} MB)")

    # Also overwrite Zoro_Sales_Tax_Refund_Master_MR_Market_LLC.xlsx
    master_path = "Automations/data/Zoro_Sales_Tax_Refund_Master_MR_Market_LLC.xlsx"
    wb.save(master_path)
    print(f"Also updated: {master_path}")

if __name__ == "__main__":
    main()
