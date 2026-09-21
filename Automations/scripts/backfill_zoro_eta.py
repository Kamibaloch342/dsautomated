#!/usr/bin/env python3
"""
backfill_zoro_eta.py — put Zoro's own delivery estimate on orders that already have the email.

Every Zoro shipping confirmation states "Estimated Delivery Monday, September 21". Those emails
have been archived all along (archive_tracking.raw_body_snippet) and nothing read the line, so
closing_orders.zoro_deliver_by was filled on 6.9% of shipped orders while ~29,000 more had an
email that could fill it. closing now stores it on every future run (engine.closing_signals
.extract_zoro_eta); this puts it on the orders already closed.

It is an expectation, not proof of delivery: it is written to zoro_deliver_by and never to
delivery_status. Blanks only — an estimate already stored is left alone.

Usage:
    python scripts/backfill_zoro_eta.py            # dry run
    python scripts/backfill_zoro_eta.py --apply
    python scripts/backfill_zoro_eta.py --undo data/repairs/eta_<stamp>.json
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

DB = os.path.join(AUTO, "data", "automations_unified.db")
REPAIR_DIR = os.path.join(AUTO, "data", "repairs")

SQL = """
SELECT co.amazon_order_id AS oid, t.raw_body_snippet AS body, t.email_timestamp AS ts
FROM closing_orders co
JOIN archive_tracking t ON t.wb_order_id = co.wb_order_id
WHERE COALESCE(co.zoro_deliver_by,'') = ''
  AND COALESCE(t.raw_body_snippet,'') != ''
"""


def plan(c):
    from engine.closing_signals import extract_zoro_eta
    best = {}
    for r in c.execute(SQL):
        eta = extract_zoro_eta(r["body"], r["ts"])
        if not eta:
            continue
        # a split order has one email per leg; the latest promise is the one that matters
        cur = best.get(r["oid"])
        if cur is None or eta > cur:
            best[r["oid"]] = eta
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", metavar="FILE")
    a = ap.parse_args()

    if a.undo:
        ids = json.load(open(a.undo, encoding="utf-8"))["filled"]
        c = sqlite3.connect(DB, timeout=60)
        c.execute("PRAGMA busy_timeout=60000")
        with c:
            c.executemany("UPDATE closing_orders SET zoro_deliver_by='' WHERE amazon_order_id=?",
                          [(i,) for i in ids])
        print(f"  cleared the estimate again on {len(ids)} order(s)")
        return

    if a.apply:
        c = sqlite3.connect(DB, timeout=60)
    else:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=60000")

    best = plan(c)
    total = c.execute("SELECT COUNT(*) FROM closing_orders WHERE order_status='Shipped'").fetchone()[0]
    have = c.execute("SELECT COUNT(*) FROM closing_orders WHERE order_status='Shipped' "
                     "AND COALESCE(zoro_deliver_by,'')!=''").fetchone()[0]
    print(f"  shipped orders with a Zoro estimate now : {have}/{total} ({have / total * 100:.1f}%)")
    print(f"  orders this pass can fill               : {len(best)}")
    for oid, eta in list(best.items())[:5]:
        print(f"     {oid} -> {eta}")

    if not a.apply:
        print("\n  dry run — nothing written. Re-run with --apply.")
        c.close()
        return

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(REPAIR_DIR, exist_ok=True)
    path = os.path.join(REPAIR_DIR, f"eta_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"created": stamp, "filled": sorted(best)}, f, indent=1)
    with c:
        c.executemany("UPDATE closing_orders SET zoro_deliver_by=? WHERE amazon_order_id=?",
                      [(eta, oid) for oid, eta in best.items()])
    have2 = c.execute("SELECT COUNT(*) FROM closing_orders WHERE order_status='Shipped' "
                      "AND COALESCE(zoro_deliver_by,'')!=''").fetchone()[0]
    print(f"\n  undo written: {os.path.relpath(path, AUTO)}")
    print(f"  shipped orders with a Zoro estimate now : {have2}/{total} ({have2 / total * 100:.1f}%)")

    od = c.execute("""SELECT COUNT(*) FROM closing_orders
                      WHERE order_status='Shipped' AND COALESCE(zoro_deliver_by,'')!=''
                        AND COALESCE(delivery_status,'') NOT IN ('Delivered','AllDelivered')
                        AND COALESCE(manual_delivered,'')!='Yes'
                        AND zoro_deliver_by < date('now','-2 day')""").fetchone()[0]
    print(f"  of those, still not confirmed delivered and past their estimate: {od}")
    c.close()


if __name__ == "__main__":
    main()
