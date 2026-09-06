"""
propagate_returns_to_closing.py — push newly-synced archive_returns into closing_orders.
Mirrors closing.py enrich_from_archive (660-675) + determine_label refund branch (1069-1075).

For every order that has archive_returns row(s):
  - return_requested = 'Yes'
  - if a refund email exists (email_type='refund_initiated'): refund_done='Yes' +
    refund_amount/refund_date/refund_reason (from the refund row)
  - return_reason / return_customer_comment gap-filled from the return_auth row
  - order_label recomputed ONLY for refunded orders: 'Refunded' if shipped else 'Cancelled'
    (the refund branch is the FIRST check in determine_label and returns early, so this
     is exactly what the pipeline would decide; non-refund orders' labels are untouched)

Usage:
  ../.venv/bin/python scripts/propagate_returns_to_closing.py Hathoro          # preview
  ../.venv/bin/python scripts/propagate_returns_to_closing.py Hathoro --go
"""
import os, sys, sqlite3
ACCT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "Hathoro"
GO = "--go" in sys.argv
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "automations_unified.db")

con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
con.row_factory = sqlite3.Row
orders = con.execute(
    "SELECT amazon_order_id, order_status, tracking_ids, delivery_status, order_label, "
    "return_requested, refund_done, refund_amount, refund_date, refund_reason, "
    "return_reason, return_customer_comment "
    "FROM closing_orders WHERE account=? AND amazon_order_id IN "
    "(SELECT DISTINCT amazon_order_id FROM archive_returns)", (ACCT,)).fetchall()
print(f"{ACCT}: orders with archive_returns rows = {len(orders)}")

def empty(v): return v is None or str(v).strip() == ""
DELIVERED = ("Delivered", "AllDelivered", "PartialDelivered", "OutForDelivery")
w = sqlite3.connect(DB, timeout=60); w.row_factory = sqlite3.Row
req = refund = label_ref = label_canc = 0
for o in orders:
    aid = o["amazon_order_id"]
    ars = w.execute("SELECT * FROM archive_returns WHERE amazon_order_id=? ORDER BY email_timestamp DESC", (aid,)).fetchall()
    if not ars:
        continue
    sets, vals = [], []
    if o["return_requested"] != "Yes":
        sets.append("return_requested=?"); vals.append("Yes"); req += 1
    # refund row?
    rf = next((r for r in ars if (r["email_type"] == "refund_initiated") or (r["refund_done"] == "Yes")), None)
    is_refunded = o["refund_done"] == "Yes"
    if rf and o["refund_done"] != "Yes":
        sets += ["refund_done=?"]; vals += ["Yes"]; refund += 1; is_refunded = True
        if empty(o["refund_amount"]) and not empty(rf["refund_amount"]): sets.append("refund_amount=?"); vals.append(rf["refund_amount"])
        if empty(o["refund_date"]) and not empty(rf["refund_date"]): sets.append("refund_date=?"); vals.append(rf["refund_date"])
        if empty(o["refund_reason"]) and (rf["refund_reason"] or rf["return_reason"]): sets.append("refund_reason=?"); vals.append(rf["refund_reason"] or rf["return_reason"])
    # return_reason / comment gap-fill from a return_auth row
    ra = next((r for r in ars if r["email_type"] == "return_auth"), ars[0])
    if empty(o["return_reason"]) and not empty(ra["return_reason"]): sets.append("return_reason=?"); vals.append(ra["return_reason"])
    if empty(o["return_customer_comment"]) and not empty(ra["customer_comment"]): sets.append("return_customer_comment=?"); vals.append(ra["customer_comment"])
    # order_label ONLY for refunded orders (refund branch returns first in determine_label)
    if is_refunded:
        shipped = o["order_status"] == "Shipped" or bool((o["tracking_ids"] or "").strip()) or o["delivery_status"] in DELIVERED
        new_label = "Refunded" if shipped else "Cancelled"
        if o["order_label"] != new_label:
            sets.append("order_label=?"); vals.append(new_label)
            if new_label == "Refunded": label_ref += 1
            else: label_canc += 1
    if sets and GO:
        con.execute(f"UPDATE closing_orders SET {','.join(sets)} WHERE amazon_order_id=?", vals + [aid])
if GO: con.commit()
print(f"  return_requested set +{req}, refund_done set +{refund}, "
      f"label→Refunded {label_ref}, label→Cancelled {label_canc}  [{'APPLIED' if GO else 'dry-run'}]")
if not GO: print("  re-run with --go to apply")
con.close()
