"""
tag_placed_via.py — add/populate closing_orders.placed_via='Order_placer' for every
order found in EITHER the local placer log (order_placer_orders) OR the online log
(Google-Sheets export .xlsx, sheet 'Order_Log') — covers both computers' placements.

Usage:
  ../.venv/bin/python scripts/tag_placed_via.py "<online_log.xlsx>"          # preview
  ../.venv/bin/python scripts/tag_placed_via.py "<online_log.xlsx>" --go
"""
import os, sys, sqlite3, openpyxl

XLSX = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else None
GO = "--go" in sys.argv
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "automations_unified.db")

con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")

# local placer ids
local = {r[0] for r in con.execute("SELECT amazon_order_id FROM order_placer_orders") if r[0]}
# online log ids
online = set()
if XLSX:
    ws = openpyxl.load_workbook(XLSX, read_only=True)["Order_Log"]
    it = ws.iter_rows(values_only=True); hdr = list(next(it)); A = hdr.index("Amazon Order ID")
    for r in it:
        if r[A]:
            online.add(str(r[A]).strip())
placer = local | online
print(f"placer ids — local placer log: {len(local)}, online log: {len(online)}, union: {len(placer)}")

cols = {r[1] for r in con.execute("PRAGMA table_info(closing_orders)")}
if "placed_via" not in cols:
    if GO:
        con.execute("ALTER TABLE closing_orders ADD COLUMN placed_via TEXT"); con.commit()
        print("added column closing_orders.placed_via TEXT")
    else:
        print("(dry-run) would add column closing_orders.placed_via")

aids = [r[0] for r in con.execute("SELECT amazon_order_id FROM closing_orders")]
match = [(a,) for a in aids if a in placer]
print(f"closing_orders rows to mark Order_placer: {len(match)} / {len(aids)}")
if GO and "placed_via" in (cols | {"placed_via"}):
    con.executemany("UPDATE closing_orders SET placed_via='Order_placer' WHERE amazon_order_id=?", match)
    con.commit()
    con.execute("PRAGMA query_only=ON")
    print("\nby account (placed_via=Order_placer):")
    for acct, n in con.execute("SELECT account, COUNT(*) FROM closing_orders WHERE placed_via='Order_placer' GROUP BY account"):
        print(f"   {acct}: {n}")
else:
    print("re-run with --go to apply")
con.close()
