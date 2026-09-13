#!/usr/bin/env python3
"""
fetch_all_accounts_returns.py
Ultra-reliable, high-throughput Zoro return lifecycle extractor across:
- inhur_llc (inuhrecomllc5@gmail.com) -> 'inuhr'
- asad_amazon_us (asad.amazon.us@gmail.com) -> 'asad.amazon.us'
- abecomllc26 (abecomllc26@gmail.com) -> 'abecomllc26'
- salesmercentile1 (salesmercentile1@gmail.com) -> 'salesmercentile1'
- salesmercentile (salesmercentile@gmail.com) -> 'salesmercentile' (already fetched / updated)

Key features:
- Global socket timeout (12s) prevents any dropped TCP connect/read from stalling.
- as_completed() processing ensures slow requests never block available results.
- Thread-isolated Gmail services prevent httplib2 concurrency collisions.
"""

import sys, os, re, json, base64, time, threading, socket
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# Set aggressive 12s socket timeout to avoid hung TCP connections
socket.setdefaulttimeout(12.0)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
FA_DIR = os.path.join(AUTO_DIR, "gmail_fetch")
sys.path.insert(0, FA_DIR)
sys.path.insert(0, AUTO_DIR)

from gmail_fetch.utils import get_gmail_service, search_gmail, get_email_body_html, clean_html
import pdfplumber

_local = threading.local()

def get_thread_svc(account_key: str):
    if not hasattr(_local, "svc_cache"):
        _local.svc_cache = {}
    if account_key not in _local.svc_cache:
        _local.svc_cache[account_key] = get_gmail_service(account_key)
    return _local.svc_cache[account_key]

def fetch_label_message(mid_and_acc):
    mid, acc = mid_and_acc
    for attempt in range(2):
        try:
            svc = get_thread_svc(acc)
            msg = svc.users().messages().get(userId="me", id=mid, format="full").execute()
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
                'msg_id': mid
            }
        except Exception:
            if attempt == 0:
                time.sleep(0.5)
                continue
    return None

def fetch_metadata_message(mid_and_acc):
    mid, acc = mid_and_acc
    for attempt in range(2):
        try:
            svc = get_thread_svc(acc)
            msg = svc.users().messages().get(userId="me", id=mid, format="metadata", metadataHeaders=["Subject"]).execute()
            headers = {h['name'].lower(): h['value'] for h in msg.get('payload', {}).get('headers', [])}
            subj = headers.get('subject', '')
            so_match = re.search(r'SO\d+', subj)
            if not so_match:
                return None
            so_num = so_match.group(0).upper()
            ms = msg.get('internalDate')
            dt = datetime.fromtimestamp(int(ms)/1000, tz=timezone.utc) if ms else None
            date_str = dt.strftime('%Y-%m-%d %H:%M') if dt else ""
            return {
                'so_number': so_num,
                'date_str': date_str,
                'ts': int(ms) if ms else 0,
                'msg_id': mid
            }
        except Exception:
            if attempt == 0:
                time.sleep(0.5)
                continue
    return None

def fetch_refund_pdf(mid_and_acc):
    mid, acc = mid_and_acc
    tmp_pdf = f"/tmp/scratch_refund_{threading.get_ident()}_{mid}.pdf"
    for attempt in range(2):
        try:
            svc = get_thread_svc(acc)
            msg = svc.users().messages().get(userId="me", id=mid).execute()
            payload = msg.get('payload', {})
            att_id = None
            for p in payload.get('parts', []) or []:
                if p.get('filename', '').lower().endswith('.pdf'):
                    att_id = p.get('body', {}).get('attachmentId')
                    break
            if not att_id:
                return None

            data = svc.users().messages().attachments().get(userId="me", messageId=mid, id=att_id).execute()
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
                    return {
                        'so_number': so_num,
                        'refund_amount': ref_amount,
                        'refund_date': ref_date,
                        'ra_number': ra_num,
                        'cash_refund_number': ref_num
                    }
            break
        except Exception:
            if attempt == 0:
                time.sleep(0.5)
                continue
        finally:
            if os.path.exists(tmp_pdf):
                try:
                    os.remove(tmp_pdf)
                except:
                    pass
    return None

def fetch_account_returns(account_key: str, display_name: str, workers: int = 10):
    print(f"\n=======================================================", flush=True)
    print(f"PROCESSING ACCOUNT: {display_name} ({account_key})", flush=True)
    print(f"=======================================================", flush=True)
    
    out_file = os.path.join(AUTO_DIR, "data", f"zoro_returns_{account_key}.json")
    
    # Fast-load salesmercentile if already present
    if account_key == "salesmercentile":
        existing_sm = os.path.join(AUTO_DIR, "data", "zoro_returns_data.json")
        if os.path.exists(existing_sm):
            print(f"Loading cached data from {existing_sm} for {account_key}...", flush=True)
            with open(existing_sm, 'r') as f:
                data = json.load(f)
                with open(out_file, 'w') as out_f:
                    json.dump(data, out_f, indent=2)
                return data

    svc = get_gmail_service(account_key)
    if not svc:
        print(f"❌ Failed to authenticate Gmail service for {account_key}", flush=True)
        return {'labels': {}, 'on_ways': {}, 'receiveds': {}, 'refunds': {}}

    # 1. Labels
    print(f"1/4 [{display_name}] Searching Shipping Label emails...", flush=True)
    label_search = search_gmail(svc, 'from:zoro subject:"Your shipping label for Zoro return" after:2026/03/31')
    label_ids = [m['id'] for m in label_search]
    print(f"  Found {len(label_ids)} label emails. Extracting details concurrently ({workers} workers)...", flush=True)
    
    t0 = time.time()
    labels = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fetch_label_message, (mid, account_key)) for mid in label_ids]
        for fut in as_completed(futures):
            res = fut.result()
            if res:
                so = res['so_number']
                if so not in labels or res['label_ts'] < labels[so]['label_ts']:
                    labels[so] = res
    print(f"  Parsed {len(labels)} unique SOs with shipping labels in {time.time()-t0:.1f}s.", flush=True)

    # 2. On Its Way
    print(f"2/4 [{display_name}] Searching 'On Its Way' emails...", flush=True)
    on_way_search = search_gmail(svc, 'from:zoro subject:"Your Zoro return is on its way" after:2026/03/31')
    on_way_ids = [m['id'] for m in on_way_search]
    print(f"  Found {len(on_way_ids)} on-way emails. Extracting metadata...", flush=True)
    
    t0 = time.time()
    on_ways = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fetch_metadata_message, (mid, account_key)) for mid in on_way_ids]
        for fut in as_completed(futures):
            res = fut.result()
            if res:
                so = res['so_number']
                if so not in on_ways or res['ts'] < on_ways[so]['shipped_ts']:
                    on_ways[so] = {
                        'so_number': so,
                        'shipped_date': res['date_str'],
                        'shipped_ts': res['ts'],
                        'msg_id': res['msg_id']
                    }
    print(f"  Parsed {len(on_ways)} unique SOs with on-way status in {time.time()-t0:.1f}s.", flush=True)

    # 3. We Received
    print(f"3/4 [{display_name}] Searching 'We Received' emails...", flush=True)
    rec_search = search_gmail(svc, 'from:zoro subject:"We received your Zoro return" after:2026/03/31')
    rec_ids = [m['id'] for m in rec_search]
    print(f"  Found {len(rec_ids)} received emails. Extracting metadata...", flush=True)
    
    t0 = time.time()
    receiveds = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fetch_metadata_message, (mid, account_key)) for mid in rec_ids]
        for fut in as_completed(futures):
            res = fut.result()
            if res:
                so = res['so_number']
                if so not in receiveds or res['ts'] < receiveds[so]['delivered_ts']:
                    receiveds[so] = {
                        'so_number': so,
                        'delivered_date': res['date_str'],
                        'delivered_ts': res['ts'],
                        'msg_id': res['msg_id']
                    }
    print(f"  Parsed {len(receiveds)} unique SOs with received status in {time.time()-t0:.1f}s.", flush=True)

    # 4. Cash Refunds & PDFs
    print(f"4/4 [{display_name}] Searching 'Cash Refund' emails...", flush=True)
    refund_search = search_gmail(svc, 'subject:"Cash Refund" after:2026/03/31')
    refund_ids = [m['id'] for m in refund_search]
    print(f"  Found {len(refund_ids)} refund emails. Downloading & parsing PDFs concurrently...", flush=True)
    
    t0 = time.time()
    refunds = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fetch_refund_pdf, (mid, account_key)) for mid in refund_ids]
        for fut in as_completed(futures):
            res = fut.result()
            if res:
                so_num = res['so_number']
                if so_num not in refunds:
                    refunds[so_num] = {
                        'so_number': so_num,
                        'refund_amount': res['refund_amount'],
                        'refund_date': res['refund_date'],
                        'ra_number': res['ra_number'],
                        'cash_refund_number': res['cash_refund_number'],
                        'refund_count': 1
                    }
                else:
                    refunds[so_num]['refund_amount'] += res['refund_amount']
                    refunds[so_num]['refund_count'] += 1
    print(f"  Parsed {len(refunds)} unique SOs with Cash Refund PDFs in {time.time()-t0:.1f}s.", flush=True)

    acc_data = {
        'account_key': account_key,
        'display_name': display_name,
        'labels': labels,
        'on_ways': on_ways,
        'receiveds': receiveds,
        'refunds': refunds
    }

    with open(out_file, 'w') as f:
        json.dump(acc_data, f, indent=2)
    print(f"✅ Saved account data to {out_file}", flush=True)

    return acc_data

def main():
    ACCOUNTS = [
        ("inhur_llc", "inuhr (inhur_llc)"),
        ("asad_amazon_us", "asad.amazon.us"),
        ("abecomllc26", "abecomllc26"),
        ("salesmercentile1", "salesmercentile1"),
        ("salesmercentile", "salesmercentile (Wrenchly LLC)")
    ]

    master_json = os.path.join(AUTO_DIR, "data", "zoro_returns_all_accounts.json")
    all_accounts_data = {}

    start_all = time.time()
    for acc_key, disp_name in ACCOUNTS:
        t0 = time.time()
        data = fetch_account_returns(acc_key, disp_name, workers=10)
        all_accounts_data[acc_key] = data
        print(f"✨ {disp_name} completed in {time.time()-t0:.1f}s!\n", flush=True)

    with open(master_json, 'w') as f:
        json.dump(all_accounts_data, f, indent=2)

    print(f"=======================================================", flush=True)
    print(f"🎉 All {len(ACCOUNTS)} accounts successfully processed in {time.time()-start_all:.1f}s!", flush=True)
    print(f"Master output: {master_json}", flush=True)
    print(f"=======================================================", flush=True)

if __name__ == "__main__":
    main()
