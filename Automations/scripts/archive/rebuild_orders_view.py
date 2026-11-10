#!/usr/bin/env python3
"""
rebuild_orders_view.py — recreate the v_orders_all VIEW with a `placed_at` column.

The Placed tab needs the PLACEMENT date (when we placed the order on Zoro), which
lives in order_placer_orders.placed_at. v_orders_all didn't expose it, so the Placed
tab could only use purchase_date (the Amazon order date). This rebuilds the view —
reading the live closing_orders columns dynamically so it stays in sync — and appends
placed_at (NULL for closed rows, p.placed_at for placed/Pending rows).

Idempotent and safe to re-run. Run:
  ../.venv/bin/python scripts/rebuild_orders_view.py
"""
import os, sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "automations_unified.db")

con = sqlite3.connect(DB, timeout=60)
con.isolation_level = None
con.execute("PRAGMA busy_timeout=60000")

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


sel_closing = ", ".join(f'"{c}"' for c in closing_cols) + ", NULL AS placed_at"
sel_placer = ", ".join(expr(c) for c in closing_cols) + ", p.placed_at AS placed_at"
sql = (
    "CREATE VIEW v_orders_all AS\n"
    f"  SELECT {sel_closing} FROM closing_orders\n"
    "  UNION ALL\n"
    f"  SELECT {sel_placer} FROM order_placer_orders p\n"
    "  WHERE p.amazon_order_id NOT IN (SELECT amazon_order_id FROM closing_orders)"
)
con.execute("DROP VIEW IF EXISTS v_orders_all")
con.execute(sql)
total = con.execute("SELECT COUNT(*) FROM v_orders_all").fetchone()[0]
pend = con.execute("SELECT COUNT(*) FROM v_orders_all WHERE order_label='Pending' AND placed_at IS NOT NULL").fetchone()[0]
con.close()
print(f"✅ v_orders_all rebuilt with placed_at — {total} rows ({pend} placed rows have placed_at)")
