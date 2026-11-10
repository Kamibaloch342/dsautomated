"""
backfill_zoro_base.py — gap-fill the Zoro "base" fields from archive_zoro (by WB) for
orders that never got re-enriched (terminal / late archive). Mirrors closing's
enrich_from_archive archive_zoro mapping exactly. Non-destructive.

Fields: zoro_account_name, zoro_gmail_account, zoro_po_number, zoro_buyer_zip,
zoro_email_ts, zoro_order_date, zoro_payment_card, zoro_payment_method, zoro_sku,
+ coupon_savings/coupon_used + zoro_item_desc (parsed from the Zoro email body).

Usage:
  ../.venv/bin/python scripts/backfill_zoro_base.py Hathoro          # preview
  ../.venv/bin/python scripts/backfill_zoro_base.py Hathoro --go
"""
import os, sys, re, sqlite3
_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)
from engine import closing_signals as CS

ACCT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "Hathoro"
GO = "--go" in sys.argv
DB = os.path.join(_AUTO, "data", "automations_unified.db")
WB = re.compile(r"WB\d+")

SIMPLE = {  # closing_orders dest : archive_zoro src
    "zoro_account_name": "zoro_account_name", "zoro_gmail_account": "gmail_account",
    "zoro_po_number": "po_number", "zoro_buyer_zip": "ship_zip",
    "zoro_email_ts": "email_timestamp", "zoro_order_date": "order_date",
    "zoro_payment_card": "payment_card_last4", "zoro_payment_method": "payment_method",
    "zoro_sku": "sku",
}
DEST = list(SIMPLE) + ["coupon_savings", "coupon_used", "zoro_item_desc"]

con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
con.row_factory = sqlite3.Row
# orders missing the base data (zoro_account_name empty = not archive-enriched) that have a WB
rows = con.execute(
    f"SELECT amazon_order_id, wb_order_id, merchant_order_id, {', '.join(DEST)} "
    f"FROM closing_orders WHERE account=? AND (zoro_account_name IS NULL OR zoro_account_name='')", (ACCT,)).fetchall()
print(f"{ACCT}: candidates missing zoro base = {len(rows)}")

def empty(v): return v is None or str(v).strip() == ""
filled = 0; field_hits = {d: 0 for d in DEST}
con2 = sqlite3.connect(DB, timeout=60); con2.row_factory = sqlite3.Row
for r in rows:
    wb = (r["wb_order_id"] or "").strip()
    if not wb:
        m = WB.findall((r["merchant_order_id"] or "").upper())
        wb = m[0] if m else ""
    if not wb:
        continue
    a = con2.execute("SELECT * FROM archive_zoro WHERE wb_order_id=? ORDER BY email_timestamp DESC LIMIT 1", (wb,)).fetchone()
    if not a:
        continue
    new = {}
    for dest, src in SIMPLE.items():
        if empty(r[dest]) and a[src] not in (None, ""):
            new[dest] = a[src]
    body = a["raw_body_snippet"]
    if empty(r["coupon_savings"]) or empty(r["coupon_used"]):
        cs, clabel = CS.extract_zoro_promo(body)
        if cs:
            if empty(r["coupon_savings"]): new["coupon_savings"] = cs
            if empty(r["coupon_used"]): new["coupon_used"] = clabel
    if empty(r["zoro_item_desc"]):
        zd = CS.extract_zoro_item(body)
        if zd: new["zoro_item_desc"] = zd
    if not new:
        continue
    for d in new: field_hits[d] += 1
    filled += 1
    if GO:
        sets = ", ".join(f"{k}=?" for k in new)
        con.execute(f"UPDATE closing_orders SET {sets} WHERE amazon_order_id=?", [*new.values(), r["amazon_order_id"]])
if GO:
    con.commit()
print(f"orders gap-filled from archive_zoro: {filled}  [{'APPLIED' if GO else 'dry-run'}]")
print("  per-field fills:", {k: v for k, v in field_hits.items() if v})
if not GO: print("  re-run with --go to apply")
con.close()
