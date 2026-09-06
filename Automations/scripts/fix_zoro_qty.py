#!/usr/bin/env python3
"""
fix_zoro_qty.py — populate zoro_qty (Zoro units actually ordered, e.g. 6 jugs) everywhere.

The placer logs ZQty but it never landed in the DB. This:
  1) sync_all → fills order_placer_orders.zoro_qty from the log (ensures the column),
  2) backfills closing_orders.zoro_qty from the placer where it's missing/0,
  3) rebuilds v_orders_all so placed (Pending) rows expose zoro_qty too.

Idempotent. Run once:  ../.venv/bin/python scripts/fix_zoro_qty.py
"""
import os, sys, sqlite3

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _DIR)
DB = os.path.join(_DIR, "data", "automations_unified.db")

from scripts.sync_order_placer_logs import sync_all

print("1) syncing placer log → order_placer_orders.zoro_qty …")
sync_all(verbose=True)

con = sqlite3.connect(DB, timeout=60)
con.isolation_level = None
con.execute("PRAGMA busy_timeout=60000")

print("2) backfilling closing_orders.zoro_qty from the placer …")
n = con.execute("""
    UPDATE closing_orders
       SET zoro_qty = (SELECT p.zoro_qty FROM order_placer_orders p
                        WHERE p.amazon_order_id = closing_orders.amazon_order_id)
     WHERE (zoro_qty IS NULL OR zoro_qty = 0)
       AND amazon_order_id IN (SELECT amazon_order_id FROM order_placer_orders
                                WHERE zoro_qty IS NOT NULL AND zoro_qty > 0)
""").rowcount
print(f"   updated {n} closing rows")

print("3) rebuilding v_orders_all (so Pending rows expose zoro_qty + placed_at) …")
closing_cols = [r[1] for r in con.execute("PRAGMA table_info(closing_orders)")]
placer_cols = {r[1] for r in con.execute("PRAGMA table_info(order_placer_orders)")}


def expr(c):
    if c == "order_label":
        return "'Pending' AS order_label"
    if c == "id":
        return "NULL AS id"
    if c == "merchant_order_id":
        return "p.wb_order_id AS merchant_order_id" if "wb_order_id" in placer_cols else "NULL AS merchant_order_id"
    if c in placer_cols:
        return f"p.{c} AS {c}"
    return f"NULL AS {c}"


sel_c = ", ".join(f'"{c}"' for c in closing_cols) + ", NULL AS placed_at"
sel_p = ", ".join(expr(c) for c in closing_cols) + ", p.placed_at AS placed_at"
con.execute("DROP VIEW IF EXISTS v_orders_all")
con.execute("CREATE VIEW v_orders_all AS\n  SELECT " + sel_c + " FROM closing_orders\n"
            "  UNION ALL\n  SELECT " + sel_p + " FROM order_placer_orders p\n"
            "  WHERE p.amazon_order_id NOT IN (SELECT amazon_order_id FROM closing_orders)")
have = con.execute("SELECT COUNT(*) FROM order_placer_orders WHERE zoro_qty IS NOT NULL AND zoro_qty>0").fetchone()[0]
con.close()
print(f"✅ done — {have} placer rows now have zoro_qty; closing backfilled; view rebuilt")
