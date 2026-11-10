"""
sync_ship_from_log.py — recover ship_name/street/city/state/zip/phone from the online
order-placer log (Google-Sheets export .xlsx) for closing_orders missing them.

The placer ran on 2 computers; the LOCAL placer log lacked some orders' details, but the
ONLINE log (this xlsx) has both accounts. Gap-fill only — never overwrites a present value.

Usage:
  ../.venv/bin/python scripts/sync_ship_from_log.py "<xlsx>" Hathoro          # preview
  ../.venv/bin/python scripts/sync_ship_from_log.py "<xlsx>" Hathoro --go
"""
import os, sys, sqlite3, openpyxl

XLSX = sys.argv[1]
ACCT = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "Hathoro"
GO = "--go" in sys.argv
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "automations_unified.db")

MAP = {"Ship To Name": "ship_name", "Ship To Street": "ship_street", "Ship To City": "ship_city",
       "Ship To State": "ship_state", "Ship To ZIP": "ship_postal_code", "Ship To Phone": "ship_phone"}

ws = openpyxl.load_workbook(XLSX, read_only=True)["Order_Log"]
it = ws.iter_rows(values_only=True)
hdr = list(next(it)); ix = {h: i for i, h in enumerate(hdr)}
def cell(r, k):
    v = r[ix[k]] if k in ix else None
    return str(v).strip() if v not in (None, "") else ""

log = {}
for r in it:
    aid = cell(r, "Amazon Order ID")
    if aid and (cell(r, "Ship To Name") or cell(r, "Ship To Street")):
        log[aid] = {dest: cell(r, src) for src, dest in MAP.items()}

con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
cols = list(MAP.values())
rows = con.execute(
    f"SELECT amazon_order_id, {', '.join(cols)} FROM closing_orders "
    f"WHERE account=? AND (ship_name IS NULL OR ship_name='')", (ACCT,)).fetchall()

updated = 0
for row in rows:
    aid = row[0]; cur = dict(zip(cols, row[1:]))
    d = log.get(aid)
    if not d:
        continue
    sets, vals = [], []
    for col in cols:
        if d.get(col) and not (cur.get(col) or "").strip():
            sets.append(f"{col}=?"); vals.append(d[col])
    if not sets:
        continue
    updated += 1
    if GO:
        vals += [ACCT, aid]
        con.execute(f"UPDATE closing_orders SET {', '.join(sets)} WHERE account=? AND amazon_order_id=?", vals)
if GO:
    con.commit()
print(f"{ACCT}: orders filled from online log = {updated}  [{'APPLIED' if GO else 'dry-run'}]")
con.close()
