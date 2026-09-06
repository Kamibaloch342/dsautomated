"""
recompute_zoro_timing.py — rebuild the DERIVED Zoro timing fields from their base
timestamps (which were backfilled after closing ran, leaving the derived ones stale).

  order_received_pkt = norm_pkt(sold_email_time)        # Amazon 'sold, ship now'
  zoro_placed_pkt    = norm_pkt(zoro_email_ts)          # Zoro 'has been received'
  zoro_shipped_pkt   = norm_pkt(shipped_email_time)     # Zoro 'has shipped'
  placement_speed/_hrs = duration(sold_email_time, zoro_email_ts)   # how fast WE placed it
  zoro_ship_speed/_hrs = duration(zoro_email_ts, shipped_email_time)

Gap-fill only (never overwrites a present value). Uses closing.py's own norm_pkt/duration.

Usage:
  ../.venv/bin/python scripts/recompute_zoro_timing.py Hathoro          # preview
  ../.venv/bin/python scripts/recompute_zoro_timing.py Hathoro --go
"""
import os, sys, sqlite3
_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)
os.environ.setdefault("CLOSING_ACCOUNT", "Hathoro")
from closing import norm_pkt, duration

ACCT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "Hathoro"
GO = "--go" in sys.argv
DB = os.path.join(_AUTO, "data", "automations_unified.db")

con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
rows = con.execute(
    "SELECT amazon_order_id, sold_email_time, zoro_email_ts, shipped_email_time, "
    "order_received_pkt, zoro_placed_pkt, zoro_shipped_pkt, placement_speed, zoro_ship_speed "
    "FROM closing_orders WHERE account=?", (ACCT,)).fetchall()

def empty(v): return v is None or str(v).strip() == ""
recv = placed = shipped = pspeed = speed = 0
for aid, sold, zet, ship, orp, zpp, zsp, pspd, zss in rows:
    sets, vals = [], []
    if empty(orp) and not empty(sold):
        v = norm_pkt(sold)
        if v: sets.append("order_received_pkt=?"); vals.append(v); recv += 1
    if empty(zpp) and not empty(zet):
        v = norm_pkt(zet)
        if v: sets.append("zoro_placed_pkt=?"); vals.append(v); placed += 1
    if empty(zsp) and not empty(ship):
        v = norm_pkt(ship)
        if v: sets.append("zoro_shipped_pkt=?"); vals.append(v); shipped += 1
    if empty(pspd) and not empty(sold) and not empty(zet):
        human, hrs = duration(sold, zet)
        if human:
            sets.append("placement_speed=?"); vals.append(human)
            sets.append("placement_speed_hrs=?"); vals.append(hrs); pspeed += 1
    if empty(zss) and not empty(zet) and not empty(ship):
        human, hrs = duration(zet, ship)
        if human:
            sets.append("zoro_ship_speed=?"); vals.append(human)
            sets.append("zoro_ship_speed_hrs=?"); vals.append(hrs); speed += 1
    if sets and GO:
        con.execute(f"UPDATE closing_orders SET {','.join(sets)} WHERE amazon_order_id=?", vals + [aid])
if GO: con.commit()
print(f"{ACCT}: order_received_pkt +{recv}, zoro_placed_pkt +{placed}, zoro_shipped_pkt +{shipped}, "
      f"placement_speed(_hrs) +{pspeed}, zoro_ship_speed(_hrs) +{speed}  [{'APPLIED' if GO else 'dry-run'}]")
if not GO: print("  re-run with --go to apply")
con.close()
