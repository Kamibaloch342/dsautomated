"""
backfill_earnings_from_archive.py — fix the loophole where orders closed BEFORE their
earnings email was archived (then went terminal / are non-WB) never got re-enriched,
so earnings=None even though archive_earnings now has it.

Fills earnings/amazon_fees/shipping_amt from archive_earnings by amazon_order_id.
Computes profit ONLY where a Zoro cost exists (no cost basis = leave profit None).
Recomputes net_profit_after_refund for refunded orders. PRESERVES order_label
(incl. custom NotPlaced/Manual). Skips Cancelled by default (their earnings were
reversed on cancellation). DRY-RUN unless --go.

Usage:
  ../.venv/bin/python scripts/backfill_earnings_from_archive.py Hathoro            # preview
  ../.venv/bin/python scripts/backfill_earnings_from_archive.py Hathoro --go
  ../.venv/bin/python scripts/backfill_earnings_from_archive.py Hathoro --go --include-cancelled
"""
import os, sys, sqlite3
ACCOUNT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "Hathoro"
GO = "--go" in sys.argv
INC_CANC = "--include-cancelled" in sys.argv
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "automations_unified.db")

def f(v):
    try: return float(v)
    except (TypeError, ValueError): return None

con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
rows = con.execute("""
   SELECT co.amazon_order_id, co.order_label, co.zoro_order_total, co.refund_done,
          co.refund_amount, co.zoro_refund_amount,
          ae.total_your_earnings, ae.total_amazon_fees, ae.total_shipping
   FROM closing_orders co JOIN archive_earnings ae ON ae.amazon_order_id=co.amazon_order_id
   WHERE co.account=? AND substr(co.purchase_date,1,7) IN ('2026-05','2026-06')
     AND (co.earnings IS NULL OR co.earnings='') AND ae.total_your_earnings>0""", (ACCOUNT,)).fetchall()

fill = prof = 0; skipped_canc = 0
for aid, lab, zoro, rf, ramt, zref, earn, fees, ship in rows:
    if lab == "Cancelled" and not INC_CANC:
        skipped_canc += 1; continue
    earn = f(earn)
    sets, vals = ["earnings=?", "amazon_fees=?", "shipping_amt=?"], [earn, f(fees), f(ship)]
    z = f(zoro)
    if z and z > 0:
        p = round(earn - z, 2)
        sets += ["profit_dollars=?", "profit_percent=?"]; vals += [p, round(p/earn*100, 2) if earn else None]
        net = p
        if rf == "Yes":
            net = round(p - (f(ramt) or 0) + (f(zref) or 0), 2)
        sets += ["net_profit_after_refund=?"]; vals += [net]
        prof += 1
    vals.append(aid)
    if GO:
        con.execute(f"UPDATE closing_orders SET {', '.join(sets)} WHERE amazon_order_id=?", vals)
    fill += 1
if GO: con.commit()
print(f"account={ACCOUNT}  [{'APPLIED' if GO else 'dry-run'}]")
print(f"  earnings filled: {fill}  (profit computed where cost exists: {prof})")
print(f"  Cancelled skipped: {skipped_canc}" + ("" if not INC_CANC else " (included)"))
if not GO: print("  re-run with --go to apply")
con.close()
