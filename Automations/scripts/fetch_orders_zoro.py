"""
fetch_orders_zoro.py — TARGETED Zoro-receipt backfill. For Hathoro WB orders whose
Zoro 'has been received' receipt isn't in archive_zoro yet (fetch-lag on the newest
orders), search the Hathoro Zoro inboxes for ONLY those WB ids (batched OR), parse,
and write straight into unified archive_zoro. Then run backfill_zoro_base.py to fill
the closing_orders columns. Light on rate limits — pulls ~the missing count, not a window.

Usage: ../.venv/bin/python scripts/fetch_orders_zoro.py
"""
import os, sys, re, sqlite3
from datetime import datetime

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FA = os.path.join(_AUTO, "gmail_fetch")   # fetch code lives here now (old sibling "Final Automation" was deleted)
sys.path.insert(0, _FA)
DB = os.path.join(_AUTO, "data", "automations_unified.db")
INBOXES = ["salesmercentile", "abecomllc26", "wrenchlyllc"]
WB = re.compile(r"WB\d+")

from utils import get_gmail_service, get_email_body_html, search_gmail, batch_get_messages
from gmail_archive import parse_zoro_email, _html_to_text_preserve_lines

con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
inarc = {r[0] for r in con.execute("SELECT DISTINCT wb_order_id FROM archive_zoro")}
need = []
for aid, wb, moid in con.execute(
        "SELECT amazon_order_id, wb_order_id, merchant_order_id FROM closing_orders "
        "WHERE account='Hathoro' AND merchant_order_id LIKE 'WB%' AND (zoro_account_name IS NULL OR zoro_account_name='')"):
    w = (wb or "").strip() or (WB.findall((moid or "").upper())[:1] or [""])[0]
    if w and w not in inarc:
        need.append(w)
need = sorted(set(need))
print(f"WB receipts to recover: {len(need)}")
if not need:
    print("nothing to fetch."); sys.exit(0)

COLS = ("wb_order_id","gmail_account","zoro_account_name","email_timestamp","order_date","po_number",
        "order_status","sku","qty","unit_price","line_total","subtotal","tax","shipping_cost","order_total",
        "coupon_savings","ship_name","ship_street","ship_city","ship_state","ship_zip","payment_method",
        "payment_card_last4","raw_body_snippet","fetched_at")
def chunk(s, n=15):
    for i in range(0, len(s), n): yield s[i:i+n]

got = 0; remaining = set(need)
for acct in INBOXES:
    if not remaining:
        break
    svc = get_gmail_service(acct)
    if not svc:
        print(f"  {acct}: no service, skip"); continue
    for batch in chunk(sorted(remaining)):
        q = 'subject:"has been received" (' + " OR ".join(f'"{w}"' for w in batch) + ")"
        msgs = search_gmail(svc, q, max_results=None) or []
        for full in batch_get_messages(svc, [m["id"] for m in msgs], progress_label=f"[{acct} zoro] "):
            body = _html_to_text_preserve_lines(get_email_body_html(full.get("payload", {})))
            d = parse_zoro_email(body, full, acct) if body else None
            if not d or not d.get("wb_order_id"):
                continue
            vals = []
            for col in COLS:
                if col == "gmail_account":
                    vals.append(acct)
                elif col == "fetched_at":
                    vals.append(datetime.now().isoformat())
                else:
                    vals.append(d.get(col))
            con.execute(f"INSERT OR REPLACE INTO archive_zoro ({','.join(COLS)}) VALUES ({','.join('?'*len(COLS))})", vals)
            remaining.discard(d["wb_order_id"]); got += 1
con.commit()
print(f"fetched + stored {got} Zoro receipts into archive_zoro | still missing: {len(remaining)}")
print("Now run: ../.venv/bin/python scripts/backfill_zoro_base.py Hathoro --go")
con.close()
