"""
Enhances individual account workbooks with a dedicated Executive Summary tab
before the order claim tabs.
"""
import os
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import pandas as pd

from generate_zoro_tax_refund_excels import (
    load_tax_data, ACCOUNTS_META, KELLY_APPROVED_STATES, STATE_NAMES,
    FONT_FAMILY, FONT_TITLE, FONT_SUBTITLE, FONT_SECTION, FONT_TH, FONT_TD,
    FONT_TD_BOLD, FONT_TOTAL, FONT_TAX_HIGHLIGHT, FILL_TITLE, FILL_TH,
    FILL_TH_SUB, FILL_ZEBRA, FILL_WHITE, FILL_TOTAL, FILL_TAX_CELL,
    FILL_ALERT_CELL, BORDER_CELL, BORDER_HEADER, BORDER_TOTAL_ROW,
    ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT, ALIGN_HEADER,
    FMT_CURRENCY, FMT_INT, FMT_PCT,
    create_title_banner, style_table_header, auto_fit_columns, add_kpi_card,
    write_order_table, CUTOFF_6M, CURRENT_DATE
)

def generate_polished_individual_workbook(df_acct, meta, out_path):
    print(f"Generating Polished Individual Workbook for {meta['name']} -> {out_path}...")
    wb = openpyxl.Workbook()
    
    df_6m = df_acct[df_acct["is_6m"]].copy()
    df_hist = df_acct[~df_acct["is_6m"]].copy()

    total_6m_tax = df_6m["tax"].sum()
    total_6m_orders = len(df_6m)
    total_6m_spend = df_6m["order_total"].sum()
    total_all_tax = df_acct["tax"].sum()

    # =====================================================
    # TAB 1: EXECUTIVE SUMMARY
    # =====================================================
    ws_sum = wb.active
    ws_sum.title = "Account Summary"

    create_title_banner(
        ws_sum,
        f"{meta['entity'].upper()} — SALES TAX REFUND CLAIM",
        f"Zoro Customer Account: {meta['name']} ({meta['cust_str']}) | Email: {meta['email']}",
        max_col=8
    )

    # Metadata
    meta_rows = [
        ("Business Entity:", f"{meta['entity']} (Owner: Mark Booth)"),
        ("Zoro Account Name:", f"{meta['name']}"),
        ("Zoro Customer ID:", f"{meta['cust_str']} (CUST #{meta['cust_id']})"),
        ("Registered Email:", f"{meta['email']}"),
        ("Exemption Status:", "Approved & Coded Exempt for 44 States (Confirmed Sep 11, 2026)"),
        ("Accounting Period:", "6-Month Refund Window: March 11, 2026 – September 11, 2026"),
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

    # KPI Stat Cards
    add_kpi_card(ws_sum, start_row=11, start_col=1, end_col=2,
                 title="Tax Refund Requested",
                 value_str=f"${total_6m_tax:,.2f}",
                 note="Eligible 6-Month Period",
                 val_color="15803D", bg_color="DCFCE7")

    add_kpi_card(ws_sum, start_row=11, start_col=3, end_col=4,
                 title="Total Eligible Orders",
                 value_str=f"{total_6m_orders:,}",
                 note="Zoro 'WB' Orders",
                 val_color="1D4ED8", bg_color="EFF6FF")

    add_kpi_card(ws_sum, start_row=11, start_col=5, end_col=6,
                 title="Total Purchasing Spend",
                 value_str=f"${total_6m_spend:,.2f}",
                 note="Gross Volume (6 Months)",
                 val_color="1E293B", bg_color="F1F5F9")

    add_kpi_card(ws_sum, start_row=11, start_col=7, end_col=8,
                 title="Approved Resale States",
                 value_str="44 States",
                 note="Kelly Approved List",
                 val_color="B45309", bg_color="FEF3C7")

    ws_sum.row_dimensions[14].height = 14

    # State Breakdown Table
    ws_sum.cell(row=15, column=1, value="STATE-BY-STATE TAX BREAKDOWN (6-MONTH REFUND WINDOW)").font = FONT_SECTION
    ws_sum.row_dimensions[15].height = 20

    st_headers = [
        "State Code", "State Name", "Kelly Approved Status", "Eligible Orders",
        "Tax Refund Requested ($)", "Order Volume ($)", "% of Total Refund"
    ]
    style_table_header(ws_sum, 16, st_headers, fill=FILL_TH_SUB)
    curr_row = 17

    st_summary = df_6m.groupby("ship_state_clean").agg(
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

    # State Total Row
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

    # =====================================================
    # TAB 2: 6-MONTH ITEM CLAIM
    # =====================================================
    ws_claim = wb.create_sheet(title="6-Month Tax Refund Claim")
    create_title_banner(
        ws_claim,
        f"{meta['name'].upper()} — 6-MONTH TAX REFUND CLAIM (${total_6m_tax:,.2f})",
        f"Customer #{meta['cust_id']} | Email: {meta['email']} | Window: Mar 11 – Sep 11, 2026 | Total Orders: {total_6m_orders:,}",
        max_col=10
    )
    write_order_table(ws_claim, df_6m, start_row=4, is_single_account=True)

    # =====================================================
    # TAB 3: HISTORICAL (> 6 MONTHS)
    # =====================================================
    if len(df_hist) > 0:
        ws_hist = wb.create_sheet(title="Historical (> 6 Months)")
        create_title_banner(
            ws_hist,
            f"{meta['name'].upper()} — HISTORICAL TRANSACTIONS (> 6 MONTHS)",
            f"Customer #{meta['cust_id']} | Orders Prior to March 11, 2026 | Total Tax: ${df_hist['tax'].sum():,.2f} ({len(df_hist):,} Orders)",
            max_col=10
        )
        write_order_table(ws_hist, df_hist, start_row=4, is_single_account=True)

    wb.save(out_path)
    print(f"Saved enhanced {meta['name']} workbook: {out_path} ({os.path.getsize(out_path)/1024/1024:.2f} MB)")

def main():
    db_path = "Automations/data/automations_unified.db"
    df = load_tax_data(db_path)
    out_dir = "Automations/data"
    for acct_key, meta in ACCOUNTS_META.items():
        df_acct = df[df["gmail_account"] == acct_key].copy()
        ind_path = os.path.join(out_dir, f"Zoro_Sales_Tax_Refund_{meta['name']}_CUST{meta['cust_id']}.xlsx")
        generate_polished_individual_workbook(df_acct, meta, ind_path)

if __name__ == "__main__":
    main()
