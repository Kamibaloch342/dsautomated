"""
fetch_zoro_pdf_receipts.py — unified Zoro receipt recovery. Handles all 4 cases:
  {WB = website order, SO = phone/sales order} x {OLD 'has been received' email body,
   NEW 'Your Receipt for Order' email with a CC Sales Receipt PDF attachment}.

For every WB/SO order whose order id has NO receipt row in archive_zoro yet, it searches
the Zoro inbox by id (batched OR), parses the receipt (email body OR the PDF attachment via
pdfplumber), writes archive_zoro, and gap-fills closing_orders incl. cost + payment card.

After this, only Grainger (WW) orders should have empty zoro_* columns (by design).

Usage:
  ../.venv/bin/python scripts/fetch_zoro_pdf_receipts.py Hathoro            # preview counts
  ../.venv/bin/python scripts/fetch_zoro_pdf_receipts.py Hathoro --go
"""
import os, sys, re, sqlite3, base64
from datetime import datetime, timezone
import pdfplumber

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FA = os.path.join(_AUTO, "gmail_fetch")   # fetch code lives here now (old sibling "Final Automation" was deleted)
sys.path.insert(0, _AUTO); sys.path.insert(0, _FA)  # _FA front so FA's utils wins; engine resolves in _AUTO
DB = os.path.join(_AUTO, "data", "automations_unified.db")
from utils import get_gmail_service, get_email_body_html, search_gmail, batch_get_messages
from gmail_archive import parse_zoro_email, _html_to_text_preserve_lines
from engine import closing_signals as CS

ACCT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "Hathoro"
GO = "--go" in sys.argv
INBOXES = {"Hathoro": ["abecomllc26", "robstark771", "salesmercentile", "wrenchlyllc"],
           "HOANK": ["asad_amazon_us", "inhur_llc"]}.get(ACCT, ["abecomllc26", "robstark771", "salesmercentile", "wrenchlyllc"])
ID = re.compile(r"(?:WB|SO)\d+")
_PDF = os.path.join(_AUTO, "scratch_zoro.pdf")

# ---------- PDF (CC Sales Receipt) parser — validated on WB5571426114 ----------
def parse_pdf(txt):
    def g(pat, grp=1):
        x = re.search(pat, txt, re.I); return x.group(grp) if x else None
    def money(pat):
        v = g(pat); return float(v.replace(",", "")) if v else None
    d = {"wb_order_id": g(r"#?\s*((?:WB|SO)\d+)"),
         "order_total": money(r"Total Amount\s*\$([\d,]+\.\d{2})") or money(r"Amount Paid:?\s*\$([\d,]+\.\d{2})"),
         "subtotal": money(r"Subtotal\s*\$([\d,]+\.\d{2})"),
         "shipping_cost": money(r"Shipping Cost\s*\$([\d,]+\.\d{2})"),
         "tax": money(r"Total Tax[^$]*\$([\d,]+\.\d{2})"),
         "payment_method": g(r"(Master ?Card|Visa|American Express|Amex|Discover|Cash)"),
         "payment_card_last4": g(r"\*{2,}\s*(\d{4})"),
         "sku": g(r"\b(G\d{4,})\b"),
         "unit_price": money(r"G\d{4,}.*?\$([\d,]+\.\d{2})"),
         "order_date": g(r"Date:\s*(\d{1,2}/\d{1,2}/\d{4})"),
         "ship_zip": g(r"\b(\d{5}(?:-\d{4})?)\b(?=[^\d]*United States)"),
         "po_number": g(r"PO#?\s*\n?\s*([A-Za-z0-9]+)\s*#?(?:WB|SO)"),
         "raw_body_snippet": txt[:4000]}
    dn = g(r"Partner Discount[^$]*\$-?([\d,]+\.\d{2})")
    d["coupon_savings"] = float(dn) if dn else None
    # item desc: text between the sku and the price column on the item line
    im = re.search(r"G\d{4,}\s+(.+?)\s+\d+\s+\$", txt, re.S)
    d["item_desc"] = re.sub(r"\s+", " ", im.group(1)).strip() if im else None
    return d

# ---------- OLD 'has been received' email-body parser (handles WB AND SO; parse_zoro_email rejects SO) ----------
def parse_old_body(txt):
    def g(pat, grp=1):
        x = re.search(pat, txt, re.I); return x.group(grp) if x else None
    def money(pat):
        v = g(pat); return float(v.replace(",", "")) if v else None
    qty = g(r"Qty:?\s*(\d+)")
    return {"wb_order_id": g(r"Order Number:?\s*\n?\s*((?:WB|SO)\d+)") or g(r"((?:WB|SO)\d+)"),
            "order_total": money(r"Order Total\s*\$?\s*([\d,]+\.\d{2})"),
            "subtotal": money(r"Subtotal\s*\$?\s*([\d,]+\.\d{2})"),
            "shipping_cost": money(r"Shipping\s*\$?\s*([\d,]+\.\d{2})"),
            "tax": money(r"Tax\s*\$?\s*([\d,]+\.\d{2})"),
            "sku": g(r"Zoro #:?\s*(G\d{4,})"),
            "qty": int(qty) if qty else None,
            "payment_method": g(r"(Master ?Card|Visa|American Express|Amex|Discover|Cash)"),
            "payment_card_last4": g(r"\*{2,}\s*(\d{4})") or g(r"ending in\s*(\d{4})"),
            "ship_zip": g(r"\b(\d{5}(?:-\d{4})?)\b"),
            "raw_body_snippet": txt[:4000]}

def pdf_text_from_msg(svc, mid, payload):
    att = [None]
    def walk(p):
        if p.get("filename", "").lower().endswith(".pdf"):
            att[0] = p.get("body", {}).get("attachmentId")
        for sp in p.get("parts", []) or []: walk(sp)
    walk(payload)
    if not att[0]: return None
    data = svc.users().messages().attachments().get(userId="me", messageId=mid, id=att[0]).execute()
    open(_PDF, "wb").write(base64.urlsafe_b64decode(data["data"]))
    with pdfplumber.open(_PDF) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)

def subj_of(full):
    for h in full.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == "subject": return h.get("value", "")
    return ""

def ts_of(full):
    ms = full.get("internalDate")
    if ms: return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return None

# ---------- gather targets: WB/SO orders with NO receipt in archive_zoro ----------
con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
have = {r[0] for r in con.execute("SELECT DISTINCT wb_order_id FROM archive_zoro")}
id2aids = {}
for aid, moid in con.execute(
        "SELECT amazon_order_id, merchant_order_id FROM closing_orders "
        "WHERE account=? AND (merchant_order_id LIKE 'WB%' OR merchant_order_id LIKE 'SO%')", (ACCT,)):
    m = ID.findall((moid or "").upper())
    if m and m[0] not in have:
        id2aids.setdefault(m[0], []).append(aid)
need = sorted(id2aids)
print(f"{ACCT}: WB/SO receipts to recover (not in archive_zoro): {len(need)}  "
      f"(WB={sum(1 for x in need if x.startswith('WB'))}, SO={sum(1 for x in need if x.startswith('SO'))})")
if not need or not GO:
    if need and not GO: print("  re-run with --go to fetch + apply")
    sys.exit(0)

ARCH = ("wb_order_id","gmail_account","zoro_account_name","email_timestamp","order_date","po_number","order_status",
        "sku","qty","unit_price","line_total","subtotal","tax","shipping_cost","order_total","coupon_savings",
        "ship_name","ship_street","ship_city","ship_state","ship_zip","payment_method","payment_card_last4",
        "raw_body_snippet","fetched_at")
# closing_orders dest : parsed key
FILL = {"zoro_account_name":"gmail_account","zoro_gmail_account":"gmail_account","zoro_po_number":"po_number",
        "zoro_buyer_zip":"ship_zip","zoro_email_ts":"email_timestamp","zoro_order_date":"order_date",
        "zoro_payment_card":"payment_card_last4","zoro_payment_method":"payment_method","zoro_sku":"sku",
        "zoro_order_total":"order_total","zoro_subtotal":"subtotal","zoro_tax":"tax","zoro_shipping":"shipping_cost",
        "coupon_savings":"coupon_savings","zoro_item_desc":"item_desc","zoro_qty":"qty"}
def chunk(s, n=12):
    for i in range(0, len(s), n): yield s[i:i+n]

old_n = new_n = ords_filled = 0; remaining = set(need)
for acct in INBOXES:
    if not remaining: break
    svc = get_gmail_service(acct)
    if not svc:
        print(f"  {acct}: no service, skip"); continue
    for batch in chunk(sorted(remaining)):
        q = 'subject:("has been received" OR "Your Receipt for Order") (' + " OR ".join(f'"{i}"' for i in batch) + ")"
        msgs = search_gmail(svc, q, max_results=None) or []
        for full in batch_get_messages(svc, [m["id"] for m in msgs], progress_label=f"[{acct}] "):
            subj = subj_of(full); idm = ID.findall(subj.upper())
            if not idm: continue
            oid = idm[0]
            if oid not in remaining: continue
            if "your receipt for order" in subj.lower():        # NEW format -> PDF
                txt = pdf_text_from_msg(svc, full["id"], full.get("payload", {}))
                d = parse_pdf(txt) if txt else None
                if d: new_n += 1
            else:                                                # OLD format -> email body (WB or SO)
                body = _html_to_text_preserve_lines(get_email_body_html(full.get("payload", {})))
                d = parse_old_body(body) if body else None
                if d:
                    cs, _ = CS.extract_zoro_promo(body)
                    if cs: d["coupon_savings"] = cs
                    d["item_desc"] = CS.extract_zoro_item(body)
                    old_n += 1
            if not d: continue
            d["wb_order_id"] = oid
            d.setdefault("email_timestamp", None) or d.__setitem__("email_timestamp", d.get("email_timestamp") or ts_of(full))
            d["gmail_account"] = acct
            # write archive_zoro
            con.execute(f"INSERT OR REPLACE INTO archive_zoro ({','.join(ARCH)}) VALUES ({','.join('?'*len(ARCH))})",
                        [(acct if c in ("gmail_account","zoro_account_name") else
                          (datetime.now().isoformat() if c=="fetched_at" else d.get(c))) for c in ARCH])
            # gap-fill every amazon order under this id
            for aid in id2aids[oid]:
                cur = con.execute(f"SELECT {','.join(FILL)} FROM closing_orders WHERE amazon_order_id=?", (aid,)).fetchone()
                sets, vals = [], []
                for j, dest in enumerate(FILL):
                    if (cur[j] is None or str(cur[j]).strip() in ("", "0", "0.0")) and d.get(FILL[dest]) not in (None, ""):
                        sets.append(f"{dest}=?"); vals.append(d[FILL[dest]])
                if sets:
                    con.execute(f"UPDATE closing_orders SET {','.join(sets)} WHERE amazon_order_id=?", vals + [aid])
                    ords_filled += 1
            remaining.discard(oid)
con.commit()
print(f"parsed receipts — OLD body: {old_n}, NEW pdf: {new_n} | closing_orders gap-filled: {ords_filled} | still missing: {len(remaining)}")
con.close()
