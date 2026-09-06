"""
backfill_shipped_time.py — gap-fill shipped_email_time (+ tracking_ids/carrier) from
archive_tracking for orders whose 'has shipped' email IS archived but never got pulled
into closing_orders (terminal/late-archive loophole). Mirrors closing.py:653-654.
Keyed by the order's WB/SO leg(s); uses the LATEST tracking email across legs.

Usage:
  ../.venv/bin/python scripts/backfill_shipped_time.py Hathoro          # preview
  ../.venv/bin/python scripts/backfill_shipped_time.py Hathoro --go
"""
import os, sys, re, sqlite3
_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "Hathoro"
GO = "--go" in sys.argv
DB = os.path.join(_AUTO, "data", "automations_unified.db")
ID = re.compile(r"(?:WB|SO)\d+")

con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
# index archive_tracking by wb leg: leg -> (email_timestamp, tracking_ids, carrier)
trk = {}
for wb, ts, tids, car in con.execute(
        "SELECT wb_order_id, email_timestamp, tracking_ids, carrier FROM archive_tracking "
        "WHERE wb_order_id IS NOT NULL ORDER BY email_timestamp"):
    if wb:
        trk[wb] = (ts, tids, car)   # later (newer) row wins

rows = con.execute(
    "SELECT amazon_order_id, wb_order_id, merchant_order_id, tracking_ids, carrier "
    "FROM closing_orders WHERE account=? AND (shipped_email_time IS NULL OR shipped_email_time='')", (ACCT,)).fetchall()
print(f"{ACCT}: orders missing shipped_email_time = {len(rows)}")

def empty(v): return v is None or str(v).strip() == ""
st = tid = carr = 0
for aid, wb, moid, cur_t, cur_c in rows:
    legs = ID.findall((moid or "").upper())
    if wb and wb not in legs:
        legs = [wb] + legs
    best = None
    for leg in legs:
        r = trk.get(leg)
        if r and r[0] and (best is None or r[0] > best[0]):
            best = r
    if not best:
        continue
    sets, vals = ["shipped_email_time=?"], [best[0]]; st += 1
    if empty(cur_t) and not empty(best[1]):
        sets.append("tracking_ids=?"); vals.append(best[1]); tid += 1
    if empty(cur_c) and not empty(best[2]):
        sets.append("carrier=?"); vals.append(best[2]); carr += 1
    if GO:
        con.execute(f"UPDATE closing_orders SET {','.join(sets)} WHERE amazon_order_id=?", vals + [aid])
if GO: con.commit()
print(f"  shipped_email_time +{st}, tracking_ids +{tid}, carrier +{carr}  [{'APPLIED' if GO else 'dry-run'}]")
if not GO: print("  re-run with --go to apply")
con.close()
