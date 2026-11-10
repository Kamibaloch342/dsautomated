#!/usr/bin/env python3
import sys, os, re, json, base64, time
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
FA_DIR = os.path.join(AUTO_DIR, "gmail_fetch")
sys.path.insert(0, FA_DIR)
sys.path.insert(0, AUTO_DIR)

from gmail_fetch.utils import get_gmail_service, search_gmail, batch_get_messages, get_email_body_html, clean_html
import pdfplumber

OUTPUT_JSON = os.path.join(AUTO_DIR, "data", "zoro_returns_data.json")
svc = get_gmail_service("salesmercentile")

def parse_label_msg(msg):
    payload = msg.get('payload', {})
    headers = {h['name'].lower(): h['value'] for h in payload.get('headers', [])}
    subj = headers.get('subject', '')
    
    so_match = re.search(r'SO\d+', subj)
    if not so_match:
        return None
    so_num = so_match.group(0).upper()
    
    ms = msg.get('internalDate')
    dt = datetime.fromtimestamp(int(ms)/1000, tz=timezone.utc) if ms else None
    date_str = dt.strftime('%Y-%m-%d %H:%M') if dt else headers.get('date', '')
    
    body = get_email_body_html(payload)
    text = clean_html(body)
    
    cust_m = re.search(r'Hi\s+([^,]+),', text)
    cust_name = cust_m.group(1).strip() if cust_m else ""
    
    item_m = re.search(r'Return(?:s)? Details\s+(.+?)(?=\s+Quantity:|\s+\$\d)', text)
    item_desc = item_m.group(1).strip() if item_m else ""
    
    qty_m = re.search(r'Quantity:\s*(\d+)', text)
    qty = int(qty_m.group(1)) if qty_m else 1
    
    reason_m = re.search(r'Return Reason:\s*([^\$\n]+?)(?=\s*\$\d|\s*Subtotal|\s*Return Method)', text)
    reason = reason_m.group(1).strip() if reason_m else ""
    
    est_m = re.search(r'Total Estimated Refund[^\$]*\$([\d,]+\.\d{2})', text, re.I)
    est_refund = float(est_m.group(1).replace(',', '')) if est_m else 0.0
    
    return {
        'so_number': so_num,
        'label_date': date_str,
        'label_ts': int(ms) if ms else 0,
        'customer_name': cust_name,
        'item_description': item_desc,
        'quantity': qty,
        'return_reason': reason,
        'estimated_refund': est_refund,
        'msg_id': msg.get('id')
    }

def parse_on_way_msg(msg):
    payload = msg.get('payload', {})
    headers = {h['name'].lower(): h['value'] for h in payload.get('headers', [])}
    subj = headers.get('subject', '')
    so_match = re.search(r'SO\d+', subj)
    if not so_match:
        return None
    so_num = so_match.group(0).upper()
    ms = msg.get('internalDate')
    dt = datetime.fromtimestamp(int(ms)/1000, tz=timezone.utc) if ms else None
    date_str = dt.strftime('%Y-%m-%d %H:%M') if dt else headers.get('date', '')
    return {
        'so_number': so_num,
        'shipped_date': date_str,
        'shipped_ts': int(ms) if ms else 0,
        'msg_id': msg.get('id')
    }

def parse_received_msg(msg):
    payload = msg.get('payload', {})
    headers = {h['name'].lower(): h['value'] for h in payload.get('headers', [])}
    subj = headers.get('subject', '')
    so_match = re.search(r'SO\d+', subj)
    if not so_match:
        return None
    so_num = so_match.group(0).upper()
    ms = msg.get('internalDate')
    dt = datetime.fromtimestamp(int(ms)/1000, tz=timezone.utc) if ms else None
    date_str = dt.strftime('%Y-%m-%d %H:%M') if dt else headers.get('date', '')
    return {
        'so_number': so_num,
        'delivered_date': date_str,
        'delivered_ts': int(ms) if ms else 0,
        'msg_id': msg.get('id')
    }

def main():
    print("1/4 Fetching Shipping Label emails (after 2026/03/31)...")
    label_search = search_gmail(svc, 'from:zoro subject:"Your shipping label for Zoro return" after:2026/03/31')
    label_ids = [m['id'] for m in label_search]
    print(f"Found {len(label_ids)} label emails. Fetching batches...")
    label_msgs = batch_get_messages(svc, label_ids)
    labels = {}
    for m in label_msgs:
        res = parse_label_msg(m)
        if res:
            so = res['so_number']
            if so not in labels or res['label_ts'] < labels[so]['label_ts']:
                labels[so] = res
    print(f"Parsed {len(labels)} unique SOs with shipping labels.")

    print("\n2/4 Fetching 'On Its Way' emails (after 2026/03/31)...")
    on_way_search = search_gmail(svc, 'from:zoro subject:"Your Zoro return is on its way" after:2026/03/31')
    on_way_ids = [m['id'] for m in on_way_search]
    print(f"Found {len(on_way_ids)} on-way emails. Fetching batches...")
    on_way_msgs = batch_get_messages(svc, on_way_ids)
    on_ways = {}
    for m in on_way_msgs:
        res = parse_on_way_msg(m)
        if res:
            so = res['so_number']
            if so not in on_ways or res['shipped_ts'] < on_ways[so]['shipped_ts']:
                on_ways[so] = res
    print(f"Parsed {len(on_ways)} unique SOs with on-way status.")

    print("\n3/4 Fetching 'We Received' emails (after 2026/04/30)...")
    rec_search = search_gmail(svc, 'from:zoro subject:"We received your Zoro return" after:2026/04/30')
    rec_ids = [m['id'] for m in rec_search]
    print(f"Found {len(rec_ids)} received emails. Fetching batches...")
    rec_msgs = batch_get_messages(svc, rec_ids)
    receiveds = {}
    for m in rec_msgs:
        res = parse_received_msg(m)
        if res:
            so = res['so_number']
            if so not in receiveds or res['delivered_ts'] < receiveds[so]['delivered_ts']:
                receiveds[so] = res
    print(f"Parsed {len(receiveds)} unique SOs with received status.")

    print("\n4/4 Fetching 'Cash Refund' emails and parsing PDFs (after 2026/04/30)...")
    refund_search = search_gmail(svc, 'subject:"Cash Refund" after:2026/04/30')
    refund_ids = [m['id'] for m in refund_search]
    print(f"Found {len(refund_ids)} refund emails. Fetching batches...")
    refund_msgs = batch_get_messages(svc, refund_ids)

    refunds = {}
    tmp_pdf = "/tmp/scratch_refund.pdf"

    for m in refund_msgs:
        payload = m.get('payload', {})
        att_box = [None]
        def walk(p):
            if p.get("filename", "").lower().endswith(".pdf"):
                att_box[0] = p.get("body", {}).get("attachmentId")
            for sp in p.get("parts", []) or []:
                walk(sp)
        walk(payload)
        
        if att_box[0]:
            try:
                data = svc.users().messages().attachments().get(userId="me", messageId=m['id'], id=att_box[0]).execute()
                pdf_bytes = base64.urlsafe_b64decode(data["data"])
                with open(tmp_pdf, "wb") as f:
                    f.write(pdf_bytes)
                with pdfplumber.open(tmp_pdf) as pdf:
                    txt = "\n".join(page.extract_text() or "" for page in pdf.pages)
                    
                    so_m = re.search(r'SO\s*#?\s*(?:Sales Order\s*#?)?\s*(SO\d+)', txt, re.I)
                    ra_m = re.search(r'Return Authorization\s*#?\s*(\d+)', txt, re.I)
                    tot_m = re.search(r'Total\s*\$([\d,]+\.\d{2})', txt)
                    date_m = re.search(r'Date\s*(\d{1,2}/\d{1,2}/\d{4})', txt)
                    ref_num_m = re.search(r'Cash Refund\s*#?\s*(\d+)', txt)
                    
                    if so_m:
                        so_num = so_m.group(1).upper()
                        ref_amount = float(tot_m.group(1).replace(',', '')) if tot_m else 0.0
                        ra_num = ra_m.group(1) if ra_m else ""
                        ref_date = date_m.group(1) if date_m else ""
                        ref_num = ref_num_m.group(1) if ref_num_m else ""
                        
                        if so_num not in refunds:
                            refunds[so_num] = {
                                'so_number': so_num,
                                'refund_amount': ref_amount,
                                'refund_date': ref_date,
                                'ra_number': ra_num,
                                'cash_refund_number': ref_num,
                                'refund_count': 1
                            }
                        else:
                            refunds[so_num]['refund_amount'] += ref_amount
                            refunds[so_num]['refund_count'] += 1
            except Exception as e:
                pass

    print(f"Parsed {len(refunds)} unique SOs with Cash Refund PDFs.")

    data = {
        'labels': labels,
        'on_ways': on_ways,
        'receiveds': receiveds,
        'refunds': refunds
    }
    with open(OUTPUT_JSON, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\nSaved raw parsed return data to {OUTPUT_JSON}")

if __name__ == "__main__":
    main()
