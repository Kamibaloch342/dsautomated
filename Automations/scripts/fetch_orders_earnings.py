"""
fetch_orders_earnings.py — TARGETED earnings backfill. Searches one inbox for ONLY
the specific order IDs that are missing earnings (not the whole "Sold, ship now"
firehose), writes them straight into unified archive_earnings, and fills WW profit.

Default: the WW (Grainger) orders missing earnings. Light on rate limits — it pulls
~N emails, not thousands.

Usage:
  ../.venv/bin/python scripts/fetch_orders_earnings.py            # WW missing-earnings, abecomllc26
  ../.venv/bin/python scripts/fetch_orders_earnings.py abecomllc26
"""
import os, sys, sqlite3
from datetime import datetime

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FA = os.path.join(_AUTO, "gmail_fetch")   # fetch code lives here now (old sibling "Final Automation" was deleted)
sys.path.insert(0, _FA)
DB = os.path.join(_AUTO, "data", "automations_unified.db")
ACCT = sys.argv[1] if len(sys.argv) > 1 else "abecomllc26"

from utils import get_gmail_service, get_email_body_html, search_gmail, batch_get_messages
from gmail_archive import parse_earnings_email, _html_to_text_preserve_lines

# 1) which orders need earnings (WW with no earnings)
con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
need = [r[0] for r in con.execute(
    "SELECT amazon_order_id FROM closing_orders WHERE merchant_order_id LIKE 'WW%' AND earnings IS NULL")]
print(f"orders needing earnings: {len(need)}")
if not need:
    print("nothing to do."); sys.exit(0)

svc = get_gmail_service(ACCT)
if not svc:
    print(f"❌ no Gmail service for {ACCT}"); sys.exit(1)

# 2) targeted search: batch order IDs with OR (only these emails are fetched)
COLS = ("amazon_order_id","gmail_account","email_timestamp","order_date","ship_by_date",
        "shipping_method","num_items","product_names","skus","asins","quantities","item_prices",
        "total_tax","total_shipping","total_amazon_fees","total_your_earnings","order_subtotal",
        "raw_body_snippet","fetched_at")
got = 0
def chunk(s, n=15):
    for i in range(0, len(s), n): yield s[i:i+n]
for batch in chunk(need):
    q = 'subject:"Sold, ship now" (' + " OR ".join(f'"{o}"' for o in batch) + ")"
    msgs = search_gmail(svc, q, max_results=None) or []
    if not msgs:
        continue
    for full in batch_get_messages(svc, [m["id"] for m in msgs], progress_label="[earnings] "):
        body = _html_to_text_preserve_lines(get_email_body_html(full.get("payload", {})))
        d = parse_earnings_email(body, full) if body else None
        if not d or not d.get("amazon_order_id"):
            continue
        con.execute(f"INSERT OR REPLACE INTO archive_earnings ({','.join(COLS)}) VALUES ({','.join('?'*len(COLS))})",
                    (d["amazon_order_id"], ACCT, d["email_timestamp"], d["order_date"], d["ship_by_date"],
                     d["shipping_method"], d["num_items"], d["product_names"], d["skus"], d["asins"],
                     d["quantities"], d["item_prices"], d["total_tax"], d["total_shipping"],
                     d["total_amazon_fees"], d["total_your_earnings"], d["order_subtotal"],
                     d["raw_body_snippet"], datetime.now().isoformat()))
        got += 1
con.commit()
print(f"fetched + stored earnings for {got} orders")

# 3) fill WW earnings + profit from what we just stored
filled = profited = 0
for aid in need:
    e = con.execute("SELECT total_your_earnings, total_amazon_fees, total_shipping FROM archive_earnings WHERE amazon_order_id=?", (aid,)).fetchone()
    if not e or not e[0]:
        continue
    earn = float(e[0])
    cost = con.execute("SELECT zoro_order_total FROM closing_orders WHERE amazon_order_id=?", (aid,)).fetchone()[0]
    sets, vals = ["earnings=?","amazon_fees=?","shipping_amt=?"], [earn, e[1], e[2]]
    if cost and cost > 0:
        profit = round(earn - float(cost), 2)
        sets += ["profit_dollars=?","profit_percent=?","net_profit_after_refund=?"]
        vals += [profit, round(profit/earn*100,2) if earn else None, profit]
        profited += 1
    vals.append(aid)
    con.execute(f"UPDATE closing_orders SET {', '.join(sets)} WHERE amazon_order_id=?", vals)
    filled += 1
con.commit()
con.execute("PRAGMA query_only=ON")
tot = con.execute("SELECT COUNT(*), ROUND(SUM(profit_dollars),2) FROM closing_orders WHERE merchant_order_id LIKE 'WW%' AND profit_dollars IS NOT NULL").fetchone()
still = con.execute("SELECT COUNT(*) FROM closing_orders WHERE merchant_order_id LIKE 'WW%' AND earnings IS NULL").fetchone()[0]
print(f"filled earnings: {filled} | profit computed: {profited}")
print(f"WW with profit now: {tot[0]} orders, total ${tot[1]} | still missing earnings: {still}")
con.close()
