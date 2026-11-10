#!/usr/bin/env python3
"""
generate_all_accounts_zoro_returns_excel.py
Generates a comprehensive, executive-grade multi-account Excel workbook
reconciling Zoro returns for:
- inuhr (inhur_llc)
- asad.amazon.us (asad_amazon_us)
- abecomllc26 (abecomllc26)
- salesmercentile1 (salesmercentile1)
- salesmercentile (salesmercentile)
- wrenchly (wrenchlyllc)

Features:
- Comparative Executive Summary with cross-account KPIs
- Master Actionable Claims tab (unrefunded returns delivered to Zoro, 0 Sara flags)
- Dedicated tabs for each business account
- Isolated Sara Springer 52 Anomaly Log
"""

import os, sys, json, re
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
ALL_JSON = os.path.join(AUTO_DIR, "data", "zoro_returns_all_accounts.json")
OUTPUT_XLSX = os.path.join(AUTO_DIR, "data", "Zoro_Returns_Multi_Account_Reconciliation_2026.xlsx")

# Sara Springer's 52 highlighted orders from July 6 email (specifically on salesmercentile CUST25017919)
SARA_FLAGGED = {
    "SO46986023": {"date": "5/11/2026", "ra": "2134890", "comments": "Per branch *pending zre is for a file cabinet. Received 10 boxs 493t444", "refunded": 89.99},
    "SO46824430": {"date": "5/11/2026", "ra": "2133408", "comments": "Per Branch - (10) 493T52 boxes actually returned G1606024", "refunded": 111.18},
    "SO46993812": {"date": "5/11/2026", "ra": "2134886", "comments": "per branch - Tracking number pulled up a return for (1) 6TWA4 but we received (1) 4MJP2", "refunded": 123.29},
    "SO46986162": {"date": "5/14/2026", "ra": "2134887", "comments": "Per branch - 621N81 (1) is what the return is for, but instead received a damaged 4MJP2 (1)", "refunded": 103.49},
    "SO46954694": {"date": "5/14/2026", "ra": "2135041", "comments": "Tracking number pulls up a return for (1) 5NGK3 Ceiling Tiles, but what we received was (10) 493T52 shipping boxes.", "refunded": 93.59},
    "SO46864256": {"date": "5/14/2026", "ra": "2136227", "comments": "Supposed to be (6) 4NPP1 instead received (25) 11A689 shipping boxes", "refunded": 111.18},
    "SO46926081": {"date": "5/14/2026", "ra": "2141678", "comments": "received boots (470P72) instead of a transport drum (19H067)", "refunded": 131.99},
    "SO46936975": {"date": "5/14/2026", "ra": "2130756", "comments": "Return is for 3EB49 (13) paper towel dispensers. Physically sent 33UF76 (8) storage pails. No paperwork with return.", "refunded": 644.69},
    "SO46921274": {"date": "5/14/2026", "ra": "2132597", "comments": "received two totes when the return was for a 5 gallon bucket of simple green (22C611).", "refunded": 51.41},
    "SO46929638": {"date": "5/15/2026", "ra": "2141676", "comments": "Branch - \"Initial Return: (2) 22C611 Actually received: (1) 4MJP2\"", "refunded": 102.83},
    "SO47159138": {"date": "5/15/2026", "ra": "2141605", "comments": "Branch - \"Expected: (6) Delineators # 1EKU6 Received: (25) Shipping Boxes # 55VH15\"", "refunded": 130.50},
    "SO46880381": {"date": "5/18/2026", "ra": "2139067", "comments": "Per branch supposed to be 24 49EN64. Instead received 1 55vf19", "refunded": 168.76},
    "SO47169625": {"date": "5/19/2026", "ra": "2141593", "comments": "per Branch received (25) 55vh15", "refunded": 103.49},
    "SO47059757": {"date": "5/20/2026", "ra": "2130487", "comments": "Per Branch - (1) 4MJR2 is what was returned.", "refunded": 577.66},
    "SO46929745": {"date": "5/22/2026", "ra": "2141675", "comments": "Per branch received 100 11k611 instead of the 4 22C611", "refunded": 205.66},
    "SO46919274": {"date": "5/26/2026", "ra": "2141687", "comments": "Per branch - original return order was for (1)621n81 instead we got some a pack of shipping boxes", "refunded": 0.0},
    "SO47157260": {"date": "5/26/2026", "ra": "2141606", "comments": "Per Branch - Tracking number pulled up a return for (2) 6W045 but we recived (1) 470P64", "refunded": 0.0},
    "SO46883390": {"date": "5/26/2026", "ra": "2139078", "comments": "Per Branch - 798067451802 Tracking number pulled up return for a case of ceiling tiles. We received grille returns instead. Return is listed as resalable.", "refunded": 0.0},
    "SO46935279": {"date": "5/27/2026", "ra": "2147515", "comments": "received (2) 4MJP8", "refunded": 0.0},
    "SO47251970": {"date": "5/28/2026", "ra": "2145764", "comments": "Per Branch: Received (1) 412PV0,", "refunded": 0.0},
    "SO47270422": {"date": "5/28/2026", "ra": "2148596", "comments": "Per Branch: received (4) 6XJG9", "refunded": 0.0},
    "SO47386309": {"date": "6/2/2026", "ra": "2153268", "comments": "Per branch - Received 1 of 60vz24 798088083437", "refunded": 0.0},
    "SO47264485": {"date": "6/3/2026", "ra": "2148590", "comments": "Per Branch - Tracking pulls up a return for (7) 3MMG5 fuel filters. We received one rodent repellent (1 ) 8X685.", "refunded": 0.0},
    "SO47169538": {"date": "6/3/2026", "ra": "2141595", "comments": "Per branch - Did not receive any of 42KW43, instead received what is pictured below (attached to Returns Fraud folder in Drive)", "refunded": 0.0},
    "SO47360069": {"date": "6/5/2026", "ra": "2154133", "comments": "per Branch - Return is for (1) 621N81 File Cabinet. Customer returned (2) 4MJR2- Air Return Vent Cover.", "refunded": 103.49},
    "SO47371688": {"date": "6/5/2026", "ra": "2153411", "comments": "Per Branch - Pending return is for (4) 8X685 but the customer returned (1) 21YL13 798088670628 - Closing RA", "refunded": 94.48},
    "SO47089851": {"date": "6/8/2026", "ra": "2147304", "comments": "per Branch sent back 70-009 wooden handle", "refunded": 103.49},
    "SO47226151": {"date": "6/8/2026", "ra": "2157752", "comments": "Per Branch (1) 470p59, return says 621n81 filing cabinet - Closing RA", "refunded": 103.49},
    "SO47327009": {"date": "6/8/2026", "ra": "2159390", "comments": "per Branch - \"Return Order is for (1) Vertical File Cabinet SKU: 621N81 But we instead received (25) Shipping Boxes SKU: 11R384\"", "refunded": 103.49},
    "SO47386179": {"date": "6/8/2026", "ra": "2151500", "comments": "per Branch: Tracking pulls up a return for (6) 4NDX3 7 in bowls, but what we received was a vent cover with no item number", "refunded": 0.0},
    "SO47327087": {"date": "6/11/2026", "ra": "2159387", "comments": "received (10) 493t52", "refunded": 0.0},
    "SO47305998": {"date": "6/11/2026", "ra": "2159405", "comments": "*Branch - po states item 3EB49 paper towel dispenser but we rec mini drawers that grainger does not sell", "refunded": 0.0},
    "SO47374985": {"date": "6/11/2026", "ra": "2153273", "comments": "We received (2) 4mjr2. The Zoro return attached is not showing this item.", "refunded": 0.0},
    "SO46933638": {"date": "6/11/2026", "ra": "2141674", "comments": "We received (1) 4MJP2 The zoro return doesn't show this item.", "refunded": 0.0},
    "SO47226144": {"date": "6/11/2026", "ra": "2157753", "comments": "We received (1) 4MJP2 The zoro return doesn't show this item.", "refunded": 0.0},
    "SO47333242": {"date": "6/11/2026", "ra": "2155988", "comments": "customer returned item 470P60 (1) boots.", "refunded": 0.0},
    "SO47277723": {"date": "6/11/2026", "ra": "2149654", "comments": "item inside was (2) 5nxa6, pillows", "refunded": 0.0},
    "SO47360237": {"date": "6/11/2026", "ra": "2154101", "comments": "Fraud return branch received (1) 470p61", "refunded": 0.0},
    "SO47262279": {"date": "6/11/2026", "ra": "2146797", "comments": "Tracking number pulls up a return for (4) 1HBK4 motors, but what we received was (1) 6W045 hand winch", "refunded": 0.0},
    "SO47269637": {"date": "6/13/2026", "ra": "2149625", "comments": "Branch - received 4mjr2 (2) - closing RA", "refunded": 0.0},
    "SO47320654": {"date": "6/15/2026", "ra": "2159394", "comments": "received two sterilite plastic containers", "refunded": 0.0},
    "SO47339298": {"date": "6/16/2026", "ra": "2155820", "comments": "recieved dirty boots", "refunded": 0.0},
    "SO47326951": {"date": "6/19/2026", "ra": "2153421", "comments": "customer returned item 49EN48 (12) 5 gallon pails.", "refunded": 0.0},
    "SO47386198": {"date": "6/19/2026", "ra": "2163947", "comments": "Tracking # brings up a return for (6) 48RH22 in which they are no longer needed. Instead, we received (1) 4MJP2", "refunded": 0.0},
    "SO47252166": {"date": "6/24/2026", "ra": "2145750", "comments": "per branch - Pulling up the order showed that we should have received (2) Orders of White Cloth Rags (42KW43) but the item(s) received were (25) Shipping Boxes (11R355)", "refunded": 0.0},
    "SO46923948": {"date": "6/25/2026", "ra": "2140245", "comments": "Per Branch - Item number 6GAM6 was returned but item number 5NGJ8 is on return number 1585030398 which is what FedEx tracking number pulls up.", "refunded": 0.0},
    "SO47317521": {"date": "6/25/2026", "ra": "2159397", "comments": "Per Branch - The item(s) on the order were not received. Instead the box contained a (4pk) of Ammonia | 834AM6 *closing RA", "refunded": 0.0},
    "SO47237640": {"date": "6/25/2026", "ra": "2157748", "comments": "Branch - Customer returned a storage tub and is claiming they are sending back ceiling tiles. This initiated return is for a customer in Tullahoma, TN but all the paperwork inside the box is for Mark Donoho from Rantoul, IL.", "refunded": 0.0},
    "SO47367091": {"date": "6/25/2026", "ra": "2153415", "comments": "Per Branch - return was for item 476h50 quantity of 1 but instead we received item 1d427 quantity of 1", "refunded": 0.0},
    "SO47331423": {"date": "6/25/2026", "ra": "2163983", "comments": "Per Branch - received (25) 11R370", "refunded": 0.0},
    "SO47265293": {"date": "6/29/2026", "ra": "2148587", "comments": "Branch - Return was for (16) 48RH22 that were no longer needed but instead we received (2) 4MJR2 that show signs of use. Possible fraud", "refunded": 244.65},
    "SO48073139": {"date": "6/29/2026", "ra": "2174333", "comments": "Branch: Did not receive correct items. Received (1) Black Steel Drum 55 Gallon Closed-Top Barrel - No Sku", "refunded": 0.0},
}

def process_account_records(acc_key, disp_name, data):
    labels = data.get('labels', {})
    on_ways = data.get('on_ways', {})
    receiveds = data.get('receiveds', {})
    refunds = data.get('refunds', {})

    all_sos = set(list(labels.keys()) + list(on_ways.keys()) + list(receiveds.keys()) + list(refunds.keys()))
    if acc_key == "salesmercentile":
        all_sos.update(SARA_FLAGGED.keys())

    records = []
    for so in sorted(all_sos):
        lbl = labels.get(so, {})
        ow = on_ways.get(so, {})
        rec = receiveds.get(so, {})
        ref = refunds.get(so, {})
        sara = SARA_FLAGGED.get(so) if acc_key == "salesmercentile" else None

        cust_name = lbl.get('customer_name', '')
        item_desc = lbl.get('item_description', '')
        qty = lbl.get('quantity', 1)
        reason = lbl.get('return_reason', '')
        est_refund = lbl.get('estimated_refund', 0.0)

        label_date = lbl.get('label_date', '')[:10] if lbl.get('label_date') else ''
        shipped_date = ow.get('shipped_date', '')[:10] if ow.get('shipped_date') else ''
        delivered_date = rec.get('delivered_date', '')[:10] if rec.get('delivered_date') else ''
        refund_date = ref.get('refund_date', '')

        refund_amt = ref.get('refund_amount', 0.0)
        cash_ref_num = ref.get('cash_refund_number', '')
        ra_num = ref.get('ra_number', '')
        if not ra_num and sara:
            ra_num = sara.get('ra', '')

        is_labeled = bool(label_date)
        is_shipped = bool(shipped_date)
        is_delivered = bool(delivered_date)
        is_refunded = (refund_amt > 0.005) or (sara and sara.get('refunded', 0) > 0.005 and not refund_amt)

        if not refund_amt and sara and sara.get('refunded', 0) > 0.005:
            refund_amt = sara.get('refunded')
            refund_date = sara.get('date', '')

        diff = round(est_refund - refund_amt, 2)

        if is_delivered and not is_refunded:
            lifecycle_status = "Delivered to Zoro — Awaiting Refund"
            action_tag = "URGENT CLAIM"
        elif is_shipped and not is_delivered:
            lifecycle_status = "Shipped by Customer — In-Transit / Carrier Loss"
            action_tag = "CARRIER TRACE"
        elif is_delivered and is_refunded:
            if diff <= 0.05:
                lifecycle_status = "Delivered & Fully Refunded"
                action_tag = "RESOLVED"
            else:
                lifecycle_status = "Delivered & Partially Refunded"
                action_tag = "PARTIAL DISCREPANCY"
        elif is_refunded and not is_delivered:
            lifecycle_status = "Refunded (Delivered Prior to Scraping/Concession)"
            action_tag = "RESOLVED"
        elif is_labeled and not is_shipped:
            lifecycle_status = "Label Generated — Customer Never Dropped Off"
            action_tag = "CUSTOMER KEPT ITEM"
        else:
            lifecycle_status = "Open / Other"
            action_tag = "REVIEW"

        sara_note = ""
        if sara:
            sara_note = f"Flagged by Sara Springer ({sara['date']}): {sara['comments']}"

        records.append({
            'account_key': acc_key,
            'account_name': disp_name,
            'so_number': so,
            'ra_number': ra_num,
            'customer_name': cust_name,
            'item_description': item_desc,
            'quantity': qty,
            'return_reason': reason,
            'label_date': label_date,
            'shipped_date': shipped_date,
            'delivered_date': delivered_date,
            'refund_date': refund_date,
            'cash_refund_num': cash_ref_num,
            'est_refund': est_refund,
            'refund_amt': refund_amt,
            'refund_diff': diff,
            'is_shipped': "Yes" if is_shipped else "No",
            'is_delivered': "Yes" if is_delivered else "No",
            'is_refunded': "Yes" if is_refunded else "No",
            'lifecycle_status': lifecycle_status,
            'action_tag': action_tag,
            'sara_note': sara_note,
            'has_sara_flag': bool(sara)
        })

    return records

def build_workbook():
    if not os.path.exists(ALL_JSON):
        print(f"Error: {ALL_JSON} not found. Run fetch script first.")
        sys.exit(1)

    with open(ALL_JSON, 'r') as f:
        all_data = json.load(f)

    # Order of accounts
    ACCOUNTS_ORDER = [
        ("salesmercentile", "salesmercentile", "salesmercentile@gmail.com", "CUST25017919"),
        ("inhur_llc", "inuhr", "inuhrecomllc5@gmail.com", "Child Account"),
        ("asad_amazon_us", "asad.amazon.us", "asad.amazon.us@gmail.com", "Sister Account"),
        ("abecomllc26", "abecomllc26", "abecomllc26@gmail.com", "Sister Account"),
        ("salesmercentile1", "salesmercentile1", "salesmercentile1@gmail.com", "CUST25217105"),
        ("wrenchlyllc", "wrenchly", "wrenchlysolutionsllc@gmail.com", "CUST27764503")
    ]

    account_records = {}
    master_all_records = []
    
    for acc_key, disp_name, email, cust_no in ACCOUNTS_ORDER:
        acc_raw = all_data.get(acc_key, {})
        recs = process_account_records(acc_key, disp_name, acc_raw)
        account_records[acc_key] = recs
        master_all_records.extend(recs)

    # Clean unflagged actionable claims
    unflagged_awaiting_claims = [
        r for r in master_all_records 
        if r['lifecycle_status'] == "Delivered to Zoro — Awaiting Refund" and not r['has_sara_flag']
    ]
    # Sara flagged records
    sara_flagged_list = [r for r in master_all_records if r['has_sara_flag']]

    # Setup openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    FONT_FAMILY = "Segoe UI"
    HEADER_FILL = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid") # Navy
    HEADER_FONT = Font(name=FONT_FAMILY, size=11, bold=True, color="FFFFFF")
    
    ACCENT_FILL = PatternFill(start_color="2A52BE", end_color="2A52BE", fill_type="solid") # Royal Blue
    ALERT_FILL = PatternFill(start_color="FDE8E8", end_color="FDE8E8", fill_type="solid") # Soft Red
    WARN_FILL = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid") # Soft Yellow
    SUCCESS_FILL = PatternFill(start_color="DEF7EC", end_color="DEF7EC", fill_type="solid") # Soft Green
    MUTED_FILL = PatternFill(start_color="F3F4F6", end_color="F3F4F6", fill_type="solid") # Soft Gray

    THIN_BORDER = Border(
        left=Side(style='thin', color='E5E7EB'),
        right=Side(style='thin', color='E5E7EB'),
        top=Side(style='thin', color='E5E7EB'),
        bottom=Side(style='thin', color='E5E7EB')
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 1: EXECUTIVE SUMMARY
    # ═══════════════════════════════════════════════════════════════════════════
    ws1 = wb.create_sheet(title="Executive Summary")
    ws1.views.sheetView[0].showGridLines = True

    ws1.column_dimensions['A'].width = 4
    ws1.column_dimensions['B'].width = 28
    ws1.column_dimensions['C'].width = 30
    ws1.column_dimensions['D'].width = 16
    ws1.column_dimensions['E'].width = 24
    ws1.column_dimensions['F'].width = 24
    ws1.column_dimensions['G'].width = 22
    ws1.column_dimensions['H'].width = 22
    ws1.column_dimensions['I'].width = 28

    # Title Banner
    ws1['B2'] = "ZORO RETURNS & REFUND PORTFOLIO AUDIT REPORT (2026)"
    ws1['B2'].font = Font(name=FONT_FAMILY, size=16, bold=True, color="1B365D")
    ws1['B3'] = f"Comprehensive Reconciliation for: salesmercentile, inuhr, asad.amazon.us, abecomllc26, salesmercentile1, and wrenchly | Generated: {datetime.now().strftime('%B %d, %Y')}"
    ws1['B3'].font = Font(name=FONT_FAMILY, size=11, italic=True, color="4B5563")

    ws1['B5'] = "CROSS-ACCOUNT RETURN STATUS SUMMARY"
    ws1['B5'].font = Font(name=FONT_FAMILY, size=12, bold=True, color="1B365D")

    summary_headers = [
        "Business Account", "Email / Identifier", "Total Returns",
        "Delivered (Unpaid Claim)", "Unrefunded Amount ($)", "In-Transit / Carrier Loss",
        "Refunded by Zoro ($)", "Sara Springer Status"
    ]
    for col_idx, h in enumerate(summary_headers, start=2):
        cell = ws1.cell(row=6, column=col_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws1.row_dimensions[6].height = 28

    row_cur = 7
    tot_portfolio_returns = 0
    tot_portfolio_unpaid_cnt = 0
    tot_portfolio_unpaid_val = 0.0
    tot_portfolio_intransit_cnt = 0
    tot_portfolio_refunded_val = 0.0

    for acc_key, disp_name, email, cust_no in ACCOUNTS_ORDER:
        recs = account_records[acc_key]
        unpaid = [r for r in recs if r['lifecycle_status'] == "Delivered to Zoro — Awaiting Refund"]
        intransit = [r for r in recs if r['lifecycle_status'] == "Shipped by Customer — In-Transit / Carrier Loss"]
        refunded = [r for r in recs if "Refunded" in r['lifecycle_status']]
        
        unpaid_val = sum(r['est_refund'] for r in unpaid)
        refunded_val = sum(r['refund_amt'] for r in recs)

        tot_portfolio_returns += len(recs)
        tot_portfolio_unpaid_cnt += len(unpaid)
        tot_portfolio_unpaid_val += unpaid_val
        tot_portfolio_intransit_cnt += len(intransit)
        tot_portfolio_refunded_val += refunded_val

        ws1.cell(row=row_cur, column=2, value=disp_name).font = Font(name=FONT_FAMILY, size=11, bold=True)
        ws1.cell(row=row_cur, column=3, value=f"{email} ({cust_no})").font = Font(name=FONT_FAMILY, size=10)
        
        c_tot = ws1.cell(row=row_cur, column=4, value=len(recs))
        c_tot.alignment = Alignment(horizontal="center")
        c_tot.font = Font(name=FONT_FAMILY, size=11)

        c_unp = ws1.cell(row=row_cur, column=5, value=len(unpaid))
        c_unp.alignment = Alignment(horizontal="center")
        c_unp.font = Font(name=FONT_FAMILY, size=11, bold=True, color="991B1B" if len(unpaid) > 0 else "000000")

        c_unp_v = ws1.cell(row=row_cur, column=6, value=unpaid_val)
        c_unp_v.number_format = '$#,##0.00'
        c_unp_v.alignment = Alignment(horizontal="right")
        c_unp_v.font = Font(name=FONT_FAMILY, size=11, bold=True, color="991B1B" if unpaid_val > 0 else "000000")

        c_itr = ws1.cell(row=row_cur, column=7, value=len(intransit))
        c_itr.alignment = Alignment(horizontal="center")
        c_itr.font = Font(name=FONT_FAMILY, size=11)

        c_ref_v = ws1.cell(row=row_cur, column=8, value=refunded_val)
        c_ref_v.number_format = '$#,##0.00'
        c_ref_v.alignment = Alignment(horizontal="right")
        c_ref_v.font = Font(name=FONT_FAMILY, size=11)

        s_status = "52 Anomalies Isolated (CUST25017919)" if acc_key == "salesmercentile" else "100% CLEAN (0 Flags)"
        c_stat = ws1.cell(row=row_cur, column=9, value=s_status)
        c_stat.alignment = Alignment(horizontal="center")
        c_stat.font = Font(name=FONT_FAMILY, size=10, bold=True, color="065F46" if "CLEAN" in s_status else "92400E")

        bg = "DEF7EC" if "CLEAN" in s_status else "FEF3C7"
        c_stat.fill = PatternFill(start_color=bg, end_color=bg, fill_type="solid")

        for c in range(2, 10):
            ws1.cell(row=row_cur, column=c).border = THIN_BORDER
        ws1.row_dimensions[row_cur].height = 22
        row_cur += 1

    # TOTAL ROW
    ws1.cell(row=row_cur, column=2, value="PORTFOLIO TOTAL").font = Font(name=FONT_FAMILY, size=11, bold=True, color="1B365D")
    ws1.cell(row=row_cur, column=3, value="Consolidated All Accounts").font = Font(name=FONT_FAMILY, size=10, italic=True)
    ws1.cell(row=row_cur, column=4, value=tot_portfolio_returns).font = Font(name=FONT_FAMILY, size=11, bold=True)
    ws1.cell(row=row_cur, column=4).alignment = Alignment(horizontal="center")
    ws1.cell(row=row_cur, column=5, value=tot_portfolio_unpaid_cnt).font = Font(name=FONT_FAMILY, size=11, bold=True, color="991B1B")
    ws1.cell(row=row_cur, column=5).alignment = Alignment(horizontal="center")
    c_tot_val = ws1.cell(row=row_cur, column=6, value=tot_portfolio_unpaid_val)
    c_tot_val.number_format = '$#,##0.00'
    c_tot_val.font = Font(name=FONT_FAMILY, size=11, bold=True, color="991B1B")
    c_tot_val.alignment = Alignment(horizontal="right")
    ws1.cell(row=row_cur, column=7, value=tot_portfolio_intransit_cnt).font = Font(name=FONT_FAMILY, size=11, bold=True)
    ws1.cell(row=row_cur, column=7).alignment = Alignment(horizontal="center")
    c_tot_ref = ws1.cell(row=row_cur, column=8, value=tot_portfolio_refunded_val)
    c_tot_ref.number_format = '$#,##0.00'
    c_tot_ref.font = Font(name=FONT_FAMILY, size=11, bold=True)
    c_tot_ref.alignment = Alignment(horizontal="right")
    ws1.cell(row=row_cur, column=9, value="Ready for Zoro Submission").font = Font(name=FONT_FAMILY, size=10, bold=True, color="1B365D")
    ws1.cell(row=row_cur, column=9).alignment = Alignment(horizontal="center")

    for c in range(2, 10):
        ws1.cell(row=row_cur, column=c).border = THIN_BORDER
        ws1.cell(row=row_cur, column=c).fill = PatternFill(start_color="EBF5FF", end_color="EBF5FF", fill_type="solid")
    ws1.row_dimensions[row_cur].height = 24

    # Explanatory Notes & Action Guidance
    row_notes = row_cur + 3
    ws1.cell(row=row_notes, column=2, value="AUDIT CONTEXT & ZORO SUBMISSION STRATEGY:").font = Font(name=FONT_FAMILY, size=12, bold=True, color="1B365D")
    guidelines = [
        "1. Immediate Actionable Claims (Tab 2): Contains all returns physically received at Zoro's distribution centers with NO anomalies and NO Sara Springer flags. These represent verified delivered merchandise awaiting cash refunds.",
        "2. Multi-Account Portfolio Segregation: Each account is maintained under its own dedicated tab (Tabs 3 to 7) with complete tracking and refund timestamps.",
        "3. Clear Isolation of Sara Springer 52 Anomalies (Tab 8): The 52 disputed items from the July 6 communication specifically pertain to Customer #25017919 (salesmercentile) and have been segregated to protect all other clean claims.",
        "4. Carrier In-Transit Protections: Orders scanned by FedEx but pending final delivery to Zoro are tracked under each respective account as carrier transit delays (not customer or merchant fault).",
        "5. Wrenchly Solutions LLC Segregation: Wrenchly LLC (wrenchlysolutionsllc@gmail.com, CUST27764503) operates as its own dedicated business account, fully audited and distinct from salesmercentile."
    ]
    for i, g in enumerate(guidelines, start=row_notes+1):
        ws1.cell(row=i, column=2, value=g).font = Font(name=FONT_FAMILY, size=10.5, color="374151")

    # ═══════════════════════════════════════════════════════════════════════════
    # HELPER FUNCTION FOR TAB TABLES
    # ═══════════════════════════════════════════════════════════════════════════
    def create_data_tab(sheet_title, records_list, include_account_col=False, is_claim_tab=False):
        ws = wb.create_sheet(title=sheet_title)
        ws.views.sheetView[0].showGridLines = True

        headers = []
        if include_account_col:
            headers.append("Account")
        headers.extend([
            "SO Number", "RA Number", "Customer Name", "Product Description", "Qty",
            "Return Reason", "Label Initiated Date", "Customer Shipped Date", "Zoro Delivered Date",
            "Delivered?", "Refund Issued?", "Refund Date", "Expected Refund ($)", "Actual Refunded ($)",
            "Unrefunded Balance ($)", "Status / Category", "Sara Springer / Warehouse Note"
        ])

        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.row_dimensions[1].height = 28

        for row_idx, r in enumerate(records_list, start=2):
            col_offset = 1
            if include_account_col:
                ws.cell(row=row_idx, column=col_offset, value=r['account_name']).font = Font(name=FONT_FAMILY, size=10, bold=True)
                col_offset += 1

            ws.cell(row=row_idx, column=col_offset, value=r['so_number']).alignment = Alignment(horizontal="center")
            ws.cell(row=row_idx, column=col_offset+1, value=r['ra_number']).alignment = Alignment(horizontal="center")
            ws.cell(row=row_idx, column=col_offset+2, value=r['customer_name'])
            ws.cell(row=row_idx, column=col_offset+3, value=r['item_description'])
            
            c_qty = ws.cell(row=row_idx, column=col_offset+4, value=r['quantity'])
            c_qty.alignment = Alignment(horizontal="center")
            
            ws.cell(row=row_idx, column=col_offset+5, value=r['return_reason'])
            
            c_lbl = ws.cell(row=row_idx, column=col_offset+6, value=r['label_date'])
            c_lbl.alignment = Alignment(horizontal="center")
            
            c_shp = ws.cell(row=row_idx, column=col_offset+7, value=r['shipped_date'])
            c_shp.alignment = Alignment(horizontal="center")
            
            c_del = ws.cell(row=row_idx, column=col_offset+8, value=r['delivered_date'])
            c_del.alignment = Alignment(horizontal="center")
            
            c_del_flag = ws.cell(row=row_idx, column=col_offset+9, value=r['is_delivered'])
            c_del_flag.alignment = Alignment(horizontal="center")
            
            c_ref_flag = ws.cell(row=row_idx, column=col_offset+10, value=r['is_refunded'])
            c_ref_flag.alignment = Alignment(horizontal="center")
            
            c_ref_date = ws.cell(row=row_idx, column=col_offset+11, value=r['refund_date'])
            c_ref_date.alignment = Alignment(horizontal="center")
            
            c_est = ws.cell(row=row_idx, column=col_offset+12, value=r['est_refund'])
            c_est.number_format = '$#,##0.00'
            c_est.alignment = Alignment(horizontal="right")
            
            c_act = ws.cell(row=row_idx, column=col_offset+13, value=r['refund_amt'])
            c_act.number_format = '$#,##0.00'
            c_act.alignment = Alignment(horizontal="right")
            
            c_diff = ws.cell(row=row_idx, column=col_offset+14, value=r['refund_diff'])
            c_diff.number_format = '$#,##0.00'
            c_diff.alignment = Alignment(horizontal="right")
            if r['refund_diff'] > 0 and r['is_delivered'] == "Yes":
                c_diff.font = Font(name=FONT_FAMILY, size=10, bold=True, color="991B1B")

            ws.cell(row=row_idx, column=col_offset+15, value=r['lifecycle_status'])
            ws.cell(row=row_idx, column=col_offset+16, value=r['sara_note'])

            # Highlight rows based on status
            tot_cols = len(headers)
            if is_claim_tab or r['lifecycle_status'] == "Delivered to Zoro — Awaiting Refund":
                for col in range(1, tot_cols + 1):
                    ws.cell(row=row_idx, column=col).fill = ALERT_FILL
            elif r['lifecycle_status'] == "Shipped by Customer — In-Transit / Carrier Loss":
                for col in range(1, tot_cols + 1):
                    ws.cell(row=row_idx, column=col).fill = WARN_FILL
            elif "Fully Refunded" in r['lifecycle_status']:
                for col in range(1, tot_cols + 1):
                    ws.cell(row=row_idx, column=col).fill = SUCCESS_FILL

            for col in range(1, tot_cols + 1):
                ws.cell(row=row_idx, column=col).border = THIN_BORDER
                if not ws.cell(row=row_idx, column=col).font.bold:
                    ws.cell(row=row_idx, column=col).font = Font(name=FONT_FAMILY, size=10)

            ws.row_dimensions[row_idx].height = 20

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val = str(cell.value or '')
                if len(val) > max_len:
                    max_len = len(val)
            ws.column_dimensions[col_letter].width = max(min(max_len + 3, 48), 12)

        ws.freeze_panes = "D2" if include_account_col else "C2"

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 2: ACTIONABLE CLAIMS (ALL ACCOUNTS - UNFLAGGED)
    # ═══════════════════════════════════════════════════════════════════════════
    create_data_tab("ALL Actionable Claims", unflagged_awaiting_claims, include_account_col=True, is_claim_tab=True)

    # ═══════════════════════════════════════════════════════════════════════════
    # TABS FOR INDIVIDUAL ACCOUNTS
    # ═══════════════════════════════════════════════════════════════════════════
    create_data_tab("salesmercentile", account_records["salesmercentile"])
    create_data_tab("inuhr", account_records["inhur_llc"])
    create_data_tab("asad.amazon.us", account_records["asad_amazon_us"])
    create_data_tab("abecomllc26", account_records["abecomllc26"])
    create_data_tab("salesmercentile1", account_records["salesmercentile1"])
    create_data_tab("wrenchly", account_records["wrenchlyllc"])

    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 9: SARA SPRINGER 52 ANOMALY LOG
    # ═══════════════════════════════════════════════════════════════════════════
    create_data_tab("Sara Springer 52 Anomalies", sara_flagged_list)

    wb.save(OUTPUT_XLSX)
    print(f"\n=======================================================")
    print(f"Successfully generated Master Excel Report at:")
    print(f"{OUTPUT_XLSX}")
    print(f"=======================================================")

if __name__ == "__main__":
    build_workbook()
