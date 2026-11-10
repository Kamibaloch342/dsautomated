#!/usr/bin/env python3
"""
Generate Zoro Undelivered & Damaged-In-Transit Refund Claims Workbook for:
  salesmercentile1@gmail.com (and salesmercentile predecessor)
Scope: Orders placed Sep 20, 2026 – Nov 20, 2026
Output: Automations/data/Zoro_Refund_Claims_salesmercentile1_Sep20_Nov20_2026.xlsx
"""
import os
import json
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SCRATCH_DIR = "/Users/kamibaloch342/.gemini/antigravity/brain/7176937a-223c-4b69-9125-54858c463ac0/scratch"
DATA_DIR = "/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/Automations/data"
OUTPUT_XLSX = os.path.join(DATA_DIR, "Zoro_Refund_Claims_salesmercentile1_Sep20_Nov20_2026.xlsx")

def main():
    compiled_path = os.path.join(SCRATCH_DIR, "salesmercentile_compiled_results.json")
    with open(compiled_path) as f:
        data = json.load(f)

    orders_sm1 = data["salesmercentile1"]
    orders_sm0 = data["salesmercentile"]

    act_sm1 = [o for o in orders_sm1 if o["is_actionable"]]
    act_sm0 = [o for o in orders_sm0 if o["is_actionable"]]
    all_act = act_sm1 + act_sm0

    all_act.sort(key=lambda x: (x["category"], x["zoro_order_date"]))

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # Fonts & Borders
    title_font = Font(name="Calibri", size=16, bold=True, color="1F497D")
    sec_font = Font(name="Calibri", size=12, bold=True, color="1F497D")
    header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_regular = Font(name="Calibri", size=10)
    font_bold = Font(name="Calibri", size=10, bold=True)
    font_red_bold = Font(name="Calibri", size=10, bold=True, color="9C0006")

    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9")
    )
    total_border = Border(
        top=Side(style="thin", color="000000"),
        bottom=Side(style="double", color="000000"),
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9")
    )

    # Color Fills
    fill_header_navy = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    fill_header_slate = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
    fill_header_teal = PatternFill(start_color="1F6E78", end_color="1F6E78", fill_type="solid")
    
    fill_rose = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")       # Damaged / Returned
    fill_amber = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")      # Exceptions / Partial
    fill_blue = PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid")       # Stalled / Lost
    fill_ghost = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")      # Ghost
    fill_green = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")      # Refunded / Delivered
    fill_total = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")

    headers = [
        "Claim Category",
        "Zoro Order # (WB)",
        "Amazon Order ID",
        "Email Used",
        "Customer Account # (CUST)",
        "Order Date",
        "Deliver-By Date",
        "Zoro Order Total ($)",
        "Est. Claim Value ($)",
        "Zoro Subtotal ($)",
        "Zoro Tax ($)",
        "Zoro Shipping ($)",
        "Multi-Tracking?",
        "UNDELIVERED Tracking ID(s) [CLAIM THIS]",
        "Undelivered Tracking Scan / Exception Details",
        "Delivered Tracking ID(s) (If Partial)",
        "All Zoro Tracking ID(s)",
        "Carrier",
        "Delivery Status",
        "Last Scan Date",
        "Recipient Name",
        "Street Address",
        "City",
        "State",
        "Zip Code",
        "Full Shipping Address",
        "Phone Number",
        "Zoro SKU",
        "Qty",
        "Item Description",
        "Zoro PO #",
        "Recommended Action / Script Notes"
    ]
    widths = [
        36, 16, 22, 26, 18, 13, 14, 15, 15, 14, 12, 13, 16,
        28, 52, 24, 28, 10, 18, 14, 22, 30, 16, 8, 12, 48,
        16, 14, 7, 38, 16, 40
    ]

    def build_sheet_table(ws, order_list, sheet_title, header_fill=fill_header_navy):
        ws.title = sheet_title
        ws.views.sheetView[0].showGridLines = True
        # Header Row
        for col_idx, (h, w) in enumerate(zip(headers, widths), start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border
            ws.column_dimensions[get_column_letter(col_idx)].width = w

        ws.row_dimensions[1].height = 28
        ws.freeze_panes = "A2"

        for r_idx, o in enumerate(order_list, start=2):
            cat = o.get("category", "")
            is_multi = len(o.get("all_tids", [])) > 1
            n_claim = len(o.get("undelivered_tids", []))
            n_all = len(o.get("all_tids", []))
            multi_str = f"YES ({n_claim}/{n_all} missing)" if is_multi else ("NO (1 Pkg)" if n_all == 1 else "No Tracking")

            full_addr = f"{o.get('ship_name', '')}, {o.get('ship_street', '')}, {o.get('ship_city', '')}, {o.get('ship_state', '')} {o.get('ship_zip', '')}"
            claim_tids_str = ", ".join(o.get("undelivered_tids", [])) or "NO TRACKING ID"
            deliv_tids_str = ", ".join(o.get("delivered_tids", []))
            all_tids_str = ", ".join(o.get("all_tids", []))
            details_str = " | ".join(o.get("undelivered_details", [])) or o.get("delivery_note", "")

            # Action note
            if "Damaged" in cat:
                action_note = "Call Zoro: Request immediate full refund due to damage in transit."
            elif "Returned" in cat:
                action_note = "Call Zoro: Package returned to sender/shipper. Request full refund."
            elif "Carrier exception" in cat:
                action_note = "Call Zoro: Terminal carrier delivery exception. Request full refund."
            elif "Partial" in cat:
                action_note = f"Call Zoro: Multi-box shipment - Box {claim_tids_str} missing/undelivered. Request partial refund."
            elif "Ghost" in cat:
                action_note = "Call Zoro: Label generated but never received carrier origin scan. Request replacement/refund."
            elif "Lost or stalled" in cat:
                action_note = "Call Zoro: Package stalled 5+ days with zero movement. File lost in transit claim."
            elif "Already Refunded" in cat:
                action_note = o.get("refund_source_note", "Refunded by Zoro")
            else:
                action_note = "Active in transit / manual review."

            row_data = [
                cat,
                o.get("wb_order_id", ""),
                o.get("amazon_order_id", ""),
                o.get("zoro_email", ""),
                o.get("cust_no", ""),
                o.get("zoro_order_date", ""),
                o.get("deliver_by_date", ""),
                float(o.get("zoro_order_total", 0.0) or 0.0),
                float(o.get("est_claim_value", 0.0) or 0.0),
                float(o.get("zoro_subtotal", 0.0) or 0.0),
                float(o.get("zoro_tax", 0.0) or 0.0),
                float(o.get("zoro_shipping", 0.0) or 0.0),
                multi_str,
                claim_tids_str,
                details_str,
                deliv_tids_str,
                all_tids_str,
                o.get("carrier", "FedEx"),
                o.get("delivery_status", "") or "Unconfirmed",
                o.get("latest_undeliv_scan", ""),
                o.get("ship_name", ""),
                o.get("ship_street", ""),
                o.get("ship_city", ""),
                o.get("ship_state", ""),
                o.get("ship_zip", ""),
                full_addr,
                o.get("ship_phone", ""),
                o.get("zoro_sku", ""),
                o.get("zoro_qty", 1),
                o.get("item_desc", ""),
                o.get("zoro_po_number", ""),
                action_note
            ]

            row_fill = None
            if "Damaged" in cat or "Returned" in cat:
                row_fill = fill_rose
            elif "Carrier exception" in cat or "Ghost" in cat or "Partial" in cat:
                row_fill = fill_amber
            elif "Lost or stalled" in cat:
                row_fill = fill_blue
            elif "Already Refunded" in cat or "Delivered" in cat:
                row_fill = fill_green

            ws.row_dimensions[r_idx].height = 20
            for c_idx, val in enumerate(row_data, start=1):
                cell = ws.cell(row=r_idx, column=c_idx, value=val)
                cell.font = font_regular
                cell.border = thin_border
                if row_fill:
                    cell.fill = row_fill

                if c_idx in (8, 9, 10, 11, 12):
                    cell.number_format = "$#,##0.00"
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    if c_idx == 9: cell.font = font_bold
                elif c_idx in (6, 7, 20):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif c_idx in (2, 4, 18, 24, 25, 27, 29):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif c_idx == 14 and is_multi:
                    cell.font = font_red_bold
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")

        tot_row = len(order_list) + 2
        ws.row_dimensions[tot_row].height = 24
        tot_label = ws.cell(row=tot_row, column=1, value="TOTAL")
        tot_label.font = font_bold
        tot_label.alignment = Alignment(horizontal="center", vertical="center")
        tot_label.fill = fill_total
        tot_label.border = total_border

        for c_idx in range(2, len(headers) + 1):
            cell = ws.cell(row=tot_row, column=c_idx)
            cell.fill = fill_total
            cell.border = total_border
            if c_idx in (8, 9, 10, 11, 12):
                col_letter = get_column_letter(c_idx)
                cell.value = f"=SUM({col_letter}2:{col_letter}{tot_row - 1})"
                cell.font = font_bold
                cell.number_format = "$#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="center")

        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(order_list) + 1}"

    # 1. Executive Summary Sheet
    ws_sum = wb.create_sheet("Executive_Summary")
    ws_sum.views.sheetView[0].showGridLines = True
    ws_sum.column_dimensions["A"].width = 5
    ws_sum.column_dimensions["B"].width = 44
    ws_sum.column_dimensions["C"].width = 16
    ws_sum.column_dimensions["D"].width = 22
    ws_sum.column_dimensions["E"].width = 22
    ws_sum.column_dimensions["F"].width = 40

    ws_sum["B2"] = "ZORO UNDELIVERED & DAMAGED-IN-TRANSIT REFUND CLAIMS REPORT"
    ws_sum["B2"].font = title_font
    ws_sum["B3"] = f"Account: salesmercentile1@gmail.com & salesmercentile | Date Range: Sep 20 – Nov 20, 2026 | Refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ws_sum["B3"].font = Font(name="Calibri", size=10, italic=True, color="595959")

    # Account Profile Block
    ws_sum["B5"] = "ACCOUNT PROFILES & REVENUE RECOVERY CONTACT"
    ws_sum["B5"].font = sec_font

    acct_meta_rows = [
        ("Zoro Account (Current)", "salesmercentile1 (Email: salesmercentile1@gmail.com)"),
        ("Customer Account # (salesmercentile1)", "CUST25061417 (Bill To: INUHR LLC / M & R Market LLC / Mark Booth)"),
        ("Payment Cards (salesmercentile1)", "American Express ending in 7746 & 1515"),
        ("Amazon Seller Store (salesmercentile1)", "HOANK ('HYPER SALE STORE' / HS_COUPON)"),
        ("Zoro Account (Predecessor)", "salesmercentile (Email: salesmercentile@gmail.com, Customer #: CUST25017919)"),
        ("Payment Card (salesmercentile)", "American Express ending in 7524 | Store: Hathoro (DM_COUPON)"),
        ("Zoro Support Phone", "(855) 289-9676 (Option 1 for Orders/Claims)"),
        ("Support Hours", "Monday–Friday 6:00 AM – 9:00 PM CT | Saturday 7:00 AM – 5:00 PM CT"),
    ]

    for idx, (lbl, val) in enumerate(acct_meta_rows, start=6):
        c_lbl = ws_sum.cell(row=idx, column=2, value=lbl)
        c_lbl.font = font_bold
        c_lbl.fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
        c_lbl.border = thin_border
        
        ws_sum.merge_cells(start_row=idx, start_column=3, end_row=idx, end_column=6)
        c_val = ws_sum.cell(row=idx, column=3, value=val)
        c_val.font = font_regular
        c_val.border = thin_border

    # KPI Breakdown Table - salesmercentile1
    start_kpi1 = 16
    ws_sum.cell(row=start_kpi1, column=2, value="1. salesmercentile1 (CUST25061417) — ACTIONABLE CLAIMS").font = sec_font

    kpi_headers = ["Claim Category", "Orders Count", "Full Order Value ($)", "Est. Claim Value ($)", "Action Required"]
    for col_idx, h in enumerate(kpi_headers, start=2):
        cell = ws_sum.cell(row=start_kpi1 + 1, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = fill_header_navy
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
    ws_sum.row_dimensions[start_kpi1 + 1].height = 24

    kpi_rows_sm1 = [
        ("1B. Returned / returning to sender", len([o for o in act_sm1 if "Returned" in o["category"]]), sum(o["zoro_order_total"] for o in act_sm1 if "Returned" in o["category"]), sum(o["est_claim_value"] for o in act_sm1 if "Returned" in o["category"]), "WB5157293447: Returned to shipper. Request full refund."),
        ("2. Partial delivery (box missing or damaged)", len([o for o in act_sm1 if "Partial" in o["category"]]), sum(o["zoro_order_total"] for o in act_sm1 if "Partial" in o["category"]), sum(o["est_claim_value"] for o in act_sm1 if "Partial" in o["category"]), "WB7576892945: Box 522592794267 missing. Request 50% partial refund."),
    ]

    curr_row = start_kpi1 + 2
    for cat_name, cnt, tot_val, claim_val, action_desc in kpi_rows_sm1:
        c2 = ws_sum.cell(row=curr_row, column=2, value=cat_name)
        c3 = ws_sum.cell(row=curr_row, column=3, value=cnt)
        c4 = ws_sum.cell(row=curr_row, column=4, value=tot_val)
        c5 = ws_sum.cell(row=curr_row, column=5, value=claim_val)
        c6 = ws_sum.cell(row=curr_row, column=6, value=action_desc)

        for cell in (c2, c3, c4, c5, c6):
            cell.font = font_regular
            cell.border = thin_border
            cell.fill = fill_amber if "Partial" in cat_name else fill_rose

        c3.alignment = Alignment(horizontal="center", vertical="center")
        c4.number_format = "$#,##0.00"
        c4.alignment = Alignment(horizontal="right", vertical="center")
        c5.number_format = "$#,##0.00"
        c5.alignment = Alignment(horizontal="right", vertical="center")
        c5.font = font_bold
        curr_row += 1

    # Total SM1
    ws_sum.row_dimensions[curr_row].height = 24
    c_tot_lbl = ws_sum.cell(row=curr_row, column=2, value="SUBTOTAL (salesmercentile1 CLAIMS)")
    c_tot_cnt = ws_sum.cell(row=curr_row, column=3, value=len(act_sm1))
    c_tot_ord = ws_sum.cell(row=curr_row, column=4, value=sum(o["zoro_order_total"] for o in act_sm1))
    c_tot_clm = ws_sum.cell(row=curr_row, column=5, value=sum(o["est_claim_value"] for o in act_sm1))
    c_tot_act = ws_sum.cell(row=curr_row, column=6, value="2 Actionable Claims ($70.18 Refund Due)")

    for cell in (c_tot_lbl, c_tot_cnt, c_tot_ord, c_tot_clm, c_tot_act):
        cell.font = font_bold
        cell.fill = fill_total
        cell.border = total_border

    c_tot_cnt.alignment = Alignment(horizontal="center", vertical="center")
    c_tot_ord.number_format = "$#,##0.00"
    c_tot_ord.alignment = Alignment(horizontal="right", vertical="center")
    c_tot_clm.number_format = "$#,##0.00"
    c_tot_clm.alignment = Alignment(horizontal="right", vertical="center")

    # KPI Breakdown Table - salesmercentile (Predecessor)
    curr_row += 3
    start_kpi0 = curr_row
    ws_sum.cell(row=start_kpi0, column=2, value="2. salesmercentile (CUST25017919) — PREDECESSOR ACTIONABLE CLAIMS").font = sec_font
    curr_row += 1

    for col_idx, h in enumerate(kpi_headers, start=2):
        cell = ws_sum.cell(row=curr_row, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = fill_header_slate
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
    ws_sum.row_dimensions[curr_row].height = 24

    kpi_categories_sm0 = [
        ("1A. Damaged in transit", "Carrier scan confirmed package damaged / returning to shipper. Request full refund."),
        ("1B. Returned / returning to sender", "Undeliverable / refused / not picked up, returned to sender. Request full refund."),
        ("2. Partial delivery (box missing or damaged)", "Multi-box shipment: 1 box delivered, 1 box missing in transit. Request partial refund."),
        ("3. Ghost label (never picked up by carrier)", "Label created but never scanned by carrier. File lost shipment claim."),
        ("4. Lost or stalled in transit", "Carrier scan stalled 5+ days with zero transit progress. File lost transit claim.")
    ]

    curr_row += 1
    for cat_name, action_desc in kpi_categories_sm0:
        cat_orders = [o for o in act_sm0 if o["category"] == cat_name]
        cnt = len(cat_orders)
        tot_val = sum(o["zoro_order_total"] for o in cat_orders)
        claim_val = sum(o["est_claim_value"] for o in cat_orders)

        c2 = ws_sum.cell(row=curr_row, column=2, value=cat_name)
        c3 = ws_sum.cell(row=curr_row, column=3, value=cnt)
        c4 = ws_sum.cell(row=curr_row, column=4, value=tot_val)
        c5 = ws_sum.cell(row=curr_row, column=5, value=claim_val)
        c6 = ws_sum.cell(row=curr_row, column=6, value=action_desc)

        for cell in (c2, c3, c4, c5, c6):
            cell.font = font_regular
            cell.border = thin_border

        c3.alignment = Alignment(horizontal="center", vertical="center")
        c4.number_format = "$#,##0.00"
        c4.alignment = Alignment(horizontal="right", vertical="center")
        c5.number_format = "$#,##0.00"
        c5.alignment = Alignment(horizontal="right", vertical="center")
        c5.font = font_bold

        if "Damaged" in cat_name or "Returned" in cat_name:
            for cell in (c2, c3, c4, c5, c6): cell.fill = fill_rose
        elif "Partial" in cat_name or "Ghost" in cat_name:
            for cell in (c2, c3, c4, c5, c6): cell.fill = fill_amber
        elif "Lost" in cat_name:
            for cell in (c2, c3, c4, c5, c6): cell.fill = fill_blue

        curr_row += 1

    # Total SM0
    ws_sum.row_dimensions[curr_row].height = 24
    c_tot_lbl0 = ws_sum.cell(row=curr_row, column=2, value="SUBTOTAL (salesmercentile CLAIMS)")
    c_tot_cnt0 = ws_sum.cell(row=curr_row, column=3, value=len(act_sm0))
    c_tot_ord0 = ws_sum.cell(row=curr_row, column=4, value=sum(o["zoro_order_total"] for o in act_sm0))
    c_tot_clm0 = ws_sum.cell(row=curr_row, column=5, value=sum(o["est_claim_value"] for o in act_sm0))
    c_tot_act0 = ws_sum.cell(row=curr_row, column=6, value="43 Actionable Claims ($2,578.67 Refund Due)")

    for cell in (c_tot_lbl0, c_tot_cnt0, c_tot_ord0, c_tot_clm0, c_tot_act0):
        cell.font = font_bold
        cell.fill = fill_total
        cell.border = total_border

    c_tot_cnt0.alignment = Alignment(horizontal="center", vertical="center")
    c_tot_ord0.number_format = "$#,##0.00"
    c_tot_ord0.alignment = Alignment(horizontal="right", vertical="center")
    c_tot_clm0.number_format = "$#,##0.00"
    c_tot_clm0.alignment = Alignment(horizontal="right", vertical="center")

    # Grand Total Combined
    curr_row += 2
    ws_sum.row_dimensions[curr_row].height = 26
    c_gt_lbl = ws_sum.cell(row=curr_row, column=2, value="COMBINED TOTAL ACTIONABLE REFUND CLAIMS")
    c_gt_cnt = ws_sum.cell(row=curr_row, column=3, value=len(all_act))
    c_gt_ord = ws_sum.cell(row=curr_row, column=4, value=sum(o["zoro_order_total"] for o in all_act))
    c_gt_clm = ws_sum.cell(row=curr_row, column=5, value=sum(o["est_claim_value"] for o in all_act))
    c_gt_act = ws_sum.cell(row=curr_row, column=6, value="45 Claims to File with Zoro ($2,648.85 Due)")

    for cell in (c_gt_lbl, c_gt_cnt, c_gt_ord, c_gt_clm, c_gt_act):
        cell.font = Font(name="Calibri", size=11, bold=True, color="1F497D")
        cell.fill = PatternFill(start_color="BDD7EE", end_color="BDD7EE", fill_type="solid")
        cell.border = total_border

    c_gt_cnt.alignment = Alignment(horizontal="center", vertical="center")
    c_gt_ord.number_format = "$#,##0.00"
    c_gt_ord.alignment = Alignment(horizontal="right", vertical="center")
    c_gt_clm.number_format = "$#,##0.00"
    c_gt_clm.alignment = Alignment(horizontal="right", vertical="center")

    # 2. Master Tabs
    ws_act_sm1 = wb.create_sheet("salesmercentile1_Claims")
    build_sheet_table(ws_act_sm1, act_sm1, "salesmercentile1_Claims", header_fill=fill_header_navy)

    ws_all_claims = wb.create_sheet("All_Actionable_Claims")
    build_sheet_table(ws_all_claims, all_act, "All_Actionable_Claims", header_fill=fill_header_teal)

    ws_act_sm0 = wb.create_sheet("salesmercentile_Claims")
    build_sheet_table(ws_act_sm0, act_sm0, "salesmercentile_Claims", header_fill=fill_header_slate)

    # 3. Categorical Tabs
    ws_damaged = wb.create_sheet("1A_Damaged_in_Transit")
    build_sheet_table(ws_damaged, [o for o in all_act if "Damaged" in o["category"]], "1A_Damaged_in_Transit")

    ws_returns = wb.create_sheet("1B_Returns_to_Sender")
    build_sheet_table(ws_returns, [o for o in all_act if "Returned" in o["category"]], "1B_Returns_to_Sender")

    ws_partial = wb.create_sheet("2_Partial_Delivery_MultiBox")
    build_sheet_table(ws_partial, [o for o in all_act if "Partial" in o["category"]], "2_Partial_Delivery_MultiBox")

    ws_ghost = wb.create_sheet("3_Ghost_Labels")
    build_sheet_table(ws_ghost, [o for o in all_act if "Ghost" in o["category"]], "3_Ghost_Labels")

    ws_stalled = wb.create_sheet("4_Lost_Stalled_in_Transit")
    build_sheet_table(ws_stalled, [o for o in all_act if "Lost" in o["category"]], "4_Lost_Stalled_in_Transit")

    # 4. Reference Tabs
    all_combined = orders_sm1 + orders_sm0
    already_ref = [o for o in all_combined if o["category"] == "Already Refunded by Zoro"]
    ws_ref = wb.create_sheet("Already_Refunded")
    build_sheet_table(ws_ref, already_ref, "Already_Refunded")

    freight_orders = [o for o in all_combined if o["category"] == "Freight / Untracked / Cancelled"]
    ws_frt = wb.create_sheet("Freight_and_Untracked")
    build_sheet_table(ws_frt, freight_orders, "Freight_and_Untracked")

    wb.save(OUTPUT_XLSX)
    print(f"Successfully generated Excel workbook at: {OUTPUT_XLSX}")
    print(f"Workbook size: {os.path.getsize(OUTPUT_XLSX):,} bytes")

if __name__ == "__main__":
    main()
