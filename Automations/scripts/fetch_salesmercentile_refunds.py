#!/usr/bin/env python3
import sys
import os
import re
import json
import base64
import io

sys.path.insert(0, "Automations")
sys.path.insert(0, "Automations/gmail_fetch")
from gmail_fetch.utils import get_gmail_service, search_gmail, get_email_body_html, clean_html
import pdfplumber

def fetch_account_refunds(acct):
    svc = get_gmail_service(acct)
    q = 'from:do-not-reply@zoro.com subject:"Cash Refund" after:2026/09/20'
    msgs = search_gmail(svc, q, max_results=None) or []
    print(f"Fetching {len(msgs)} Cash Refund messages from {acct}...")

    refunds = []
    for idx, m in enumerate(msgs):
        full = svc.users().messages().get(userId="me", id=m["id"]).execute()
        payload = full.get("payload", {})

        txt = ""

        def find_att(p, ext):
            if p.get("filename", "").lower().endswith(ext):
                return p["body"].get("attachmentId")
            for sp in p.get("parts", []):
                res = find_att(sp, ext)
                if res:
                    return res
            return None

        pdf_att = find_att(payload, ".pdf")
        if pdf_att:
            att = svc.users().messages().attachments().get(userId="me", messageId=m["id"], id=pdf_att).execute()
            pdf_bytes = base64.urlsafe_b64decode(att["data"])
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                txt = "\n".join(p.extract_text() or "" for p in pdf.pages)
        else:
            html_att = find_att(payload, ".html")
            if html_att:
                att = svc.users().messages().attachments().get(userId="me", messageId=m["id"], id=html_att).execute()
                html_bytes = base64.urlsafe_b64decode(att["data"]).decode("utf-8", errors="ignore")
                txt = clean_html(html_bytes)
            else:
                txt = clean_html(get_email_body_html(payload))

        so_m = re.search(r"\b(SO\d{7,10})\b", txt)
        wb_m = re.search(r"\b(WB\d{8,12})\b", txt)
        tot_m = re.search(r"Total\s*\$?([\d,]+\.\d{2})", txt)
        ref_m = re.search(r"(?:Cash Refund|Refund #)\s*#?\s*(\d+)", txt)
        date_m = re.search(r"(?:Date|Cash Refund Date)\s*#?\s*(\d{1,2}/\d{1,2}/\d{4})", txt)
        ra_m = re.search(r"Return Authorization\s*#?\s*(\d+)", txt)
        po_m = re.search(r"Cust(?:omer)?\.?\s*PO\s*#?\s*([A-Za-z0-9\-]+)", txt)
        cust_m = re.search(r"Cust(?:omer)?\.?\s*No\.?\s*([A-Za-z0-9\.]+)", txt)

        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        subj = headers.get("Subject", "")
        if not ref_m:
            ref_m = re.search(r"#(\d+)", subj)

        refunds.append({
            "acct": acct,
            "msg_id": m["id"],
            "subject": subj,
            "so": so_m.group(1) if so_m else "",
            "wb": wb_m.group(1) if wb_m else "",
            "amount": float(tot_m.group(1).replace(",", "")) if tot_m else 0.0,
            "cash_refund_no": ref_m.group(1) if ref_m else "",
            "date": date_m.group(1) if date_m else "",
            "ra": ra_m.group(1) if ra_m else "",
            "po": po_m.group(1) if po_m else "",
            "cust": cust_m.group(1) if cust_m else "",
            "raw": txt,
        })
    return refunds

def main():
    all_crs = []
    for acct in ["salesmercentile1", "salesmercentile"]:
        crs = fetch_account_refunds(acct)
        all_crs.extend(crs)

    out_path = "/Users/kamibaloch342/.gemini/antigravity/brain/7176937a-223c-4b69-9125-54858c463ac0/scratch/salesmercentile_cash_refunds.json"
    with open(out_path, "w") as f:
        json.dump(all_crs, f, indent=2)

    print(f"Total Cash Refunds extracted: {len(all_crs)}")
    for c in all_crs:
        print(f"  {c['acct']} | CR#{c['cash_refund_no']} | Date:{c['date']} | Amt:${c['amount']} | SO:{c['so']}")

if __name__ == "__main__":
    main()
