"""
refresh_order_status.py — make closing_orders.order_status match the LATEST report
(order_status changes over time Pending→Shipped→Cancelled, and closing's gap-fill
persist won't overwrite it). Authoritative overwrite from the report.

If an order's new status is Cancelled and it isn't delivered, also set order_label
='Cancelled' to stay consistent.

Usage:
  ../.venv/bin/python scripts/refresh_order_status.py Hathoro "<May.txt>" "<June.txt>" [--go]
"""
import os, sys, csv, sqlite3
csv.field_size_limit(10**7)
ACCOUNT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "Hathoro"
FILES = [a for a in sys.argv[2:] if not a.startswith("--")]
GO = "--go" in sys.argv
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "automations_unified.db")

# report status per order (status is order-level; prefer non-cancelled if a line differs)
rep = {}
for f in FILES:
    for r in csv.DictReader(open(f, newline="", encoding="utf-8", errors="replace"), delimiter="\t"):
        aid = (r.get("amazon-order-id") or "").strip()
        st = (r.get("order-status") or "").strip()
        if not aid or not st:
            continue
        cur = rep.get(aid)
        if cur is None or (cur.lower() == "cancelled" and st.lower() != "cancelled"):
            rep[aid] = st

con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
db = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
    "SELECT amazon_order_id, order_status, order_label, delivery_status FROM closing_orders WHERE account=?", (ACCOUNT,))}

status_upd = []; label_upd = []
for aid, st in rep.items():
    if aid not in db:
        continue
    cur_st, cur_lab, dstat = db[aid]
    if (cur_st or "") != st:
        status_upd.append((aid, cur_st, st))
        if st.lower() == "cancelled" and dstat not in ("Delivered", "AllDelivered") and cur_lab != "Cancelled":
            label_upd.append(aid)

print(f"account={ACCOUNT}  order_status changes: {len(status_upd)}  (+{len(label_upd)} → label Cancelled)  [{'APPLY' if GO else 'dry-run'}]")
from collections import Counter
trans = Counter((o, n) for _, o, n in status_upd)
for (o, n), c in trans.most_common():
    print(f"   {o or '(blank)'} → {n}: {c}")

if GO:
    for aid, _, st in status_upd:
        con.execute("UPDATE closing_orders SET order_status=? WHERE account=? AND amazon_order_id=?", (st, ACCOUNT, aid))
    for aid in label_upd:
        con.execute("UPDATE closing_orders SET order_label='Cancelled' WHERE account=? AND amazon_order_id=?", (ACCOUNT, aid))
    con.commit()
    print(f"\n✅ updated {len(status_upd)} order_status, {len(label_upd)} order_label→Cancelled")
else:
    print("\nDRY-RUN — nothing written.")
con.close()
