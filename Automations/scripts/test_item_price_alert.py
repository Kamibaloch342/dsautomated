#!/usr/bin/env python3
"""
test_item_price_alert.py — the "AMAZON ITEM PRICE INCREASED +2399%" alert of 21 Sep 2026.

Order 113-7562267-3835449: one Amazon unit of a 25-pack of shipping boxes at $36.99. The
placer buys 25 boxes on Zoro for it. Two faults turned that into "$36.99 was $1.48":
  1. engine.orders._upsert_unified_db wrote the ZORO count (25) into `quantity`, the column
     that means units the buyer bought (the order log had it right: AQty:1 | ZQty:25).
  2. the ITEM_PRICE_CHANGED rule, finding no item_price on the placer row, divided
     (earnings + fees) by that quantity: $36.99 / 25 = $1.48.

Run:  python scripts/test_item_price_alert.py
"""
import ast
import os
import re
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


def src(rel):
    return open(os.path.join(AUTO, rel), encoding="utf-8").read()


def func_src(text, name):
    tree = ast.parse(text)
    lines = text.split("\n")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{name} not found")


def main():
    import utils.unified_db as U
    import utils.insights_engine as IE
    tmp = os.path.join(tempfile.mkdtemp(), "unified.db")
    U.init_db(tmp)
    with sqlite3.connect(tmp) as c:
        c.execute("CREATE TABLE IF NOT EXISTS archive_zoro (wb_order_id TEXT, unit_price REAL, subtotal REAL, "
                  "shipping_cost REAL, tax REAL, order_total REAL, coupon_savings REAL, qty INTEGER)")
        # columns the live tables gained through migrations, which init_db's base schema lacks
        for table, cols in (("order_placer_orders", ("zoro_qty INTEGER", "zoro_deliver_by TEXT", "zoro_stock INTEGER",
                                                     "alt_used TEXT", "main_sku TEXT", "zoro_list_price REAL",
                                                     "zoro_subtotal REAL", "zoro_shipping REAL")),
                            ("orders", ("zoro_qty INTEGER", "zoro_sku TEXT", "source TEXT", "merchant_order_id TEXT"))):
            for col in cols:
                try:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass
    IE._DB_PATH = tmp
    ASIN = "B0GXL2JC3V"

    def current(price):
        return {"amazon_id": "A-NEW", "asin": ASIN, "sku": "hath-G3056560", "seller_sku": "hath-G3056560",
                "zoro_code": "G3056560", "title": "Shipping Boxes 16x10x6 (25-Pack)", "item_price": price,
                "amazon_qty": 1, "quantity": 1, "zoro_qty": 25, "earnings": 31.44, "amazon_fees": 5.55,
                "account": "Hathoro"}

    def price_alerts(price):
        return [i for i in IE.analyze_order_insights(current(price)) if i.get("code") == "ITEM_PRICE_CHANGED"]

    # ── 1. the rule, with the exact rows that produced the bad alert ────────────────────────
    print("\n── the rule takes Amazon's previous price from Amazon's own report ──")
    U.upsert_placer_order({"amazon_order_id": "A-PREV", "account": "Hathoro", "asin": ASIN, "sku": "G3056560",
                           "quantity": 25, "zoro_qty": 25, "earnings": 31.44, "amazon_fees": 5.55,
                           "shipping_amt": 0.0, "placed_at": "2026-09-20T10:00:00"}, db_path=tmp)
    with sqlite3.connect(tmp) as c:
        c.execute("INSERT INTO closing_orders (amazon_order_id, account, asin, item_price, quantity, purchase_date) "
                  "VALUES (?,?,?,?,?,?)", ("A-OLD", "Hathoro", ASIN, 147.96, 4, "2026-09-16 11:43"))
    prev = IE.last_amazon_unit_price(ASIN, exclude_amazon_id="A-NEW")
    check("previous unit price = closing_orders item_price / quantity (147.96 / 4 = 36.99)",
          prev and prev["unit_price"] == 36.99, str(prev))
    check("same price again ($36.99) → no alert, even with the wrong placer row present", price_alerts(36.99) == [])
    a = price_alerts(39.99)
    check("a real change (36.99 → 39.99) alerts against $36.99, +8.1%",
          len(a) == 1 and "$36.99" in a[0]["summary"] and "+8.1%" in a[0]["summary"], str(a)[:160])
    check("...and never against $1.48", not a or "$1.48" not in a[0]["summary"])

    # ── 2. no closed order yet: the placer row is used, but only when it can be trusted ────
    print("\n── without a closed order, a pre-fix placer row (quantity == zoro_qty) is not a baseline ──")
    with sqlite3.connect(tmp) as c:
        c.execute("DELETE FROM closing_orders")
    check("quantity 25 == zoro_qty 25 → no baseline → no alert (instead of '+2399%')", price_alerts(36.99) == [] and price_alerts(39.99) == [])
    U.upsert_placer_order({"amazon_order_id": "A-PREV", "quantity": 1, "zoro_qty": 25}, db_path=tmp)
    a = price_alerts(39.99)
    check("a corrected row (quantity 1, zoro_qty 25) gives (31.44 + 5.55) / 1 = $36.99 as the baseline",
          len(a) == 1 and "$36.99" in a[0]["summary"], str(a)[:160])
    check("...and the unchanged price stays silent", price_alerts(36.99) == [])

    # ── 3. the placer writes Amazon units into quantity and Zoro units into zoro_qty ───────
    print("\n── the placer's DB write: quantity = Amazon units, zoro_qty = Zoro units ──")
    osrc = src("engine/orders.py")
    ns = {"_HAS_UNIFIED_DB": True, "PROFILE_NAME": "DM_COUPON", "re": re, "os": os, "json": __import__("json"),
          "datetime": __import__("datetime").datetime}
    exec(func_src(osrc, "_safe_float"), ns)
    exec(func_src(osrc, "_upsert_unified_db"), ns)
    real_path = U.DB_PATH
    U.DB_PATH = tmp
    try:
        detail = {"amazon_id": "A-WRITE", "asin": ASIN, "sku": "G3056560", "qty": 25, "city": "X", "state": "TX",
                  "zip": "75001", "earnings": 31.44, "zoro_price": 25.79, "profit": 5.65, "status": "SUCCESS", "wb_id": "WB1"}
        ns["_upsert_unified_db"]("A-WRITE", detail, amazon_qty=1, amazon_fees=5.55, shipping_amt=0.0)
        with sqlite3.connect(tmp) as c:
            po = c.execute("SELECT quantity, zoro_qty FROM order_placer_orders WHERE amazon_order_id='A-WRITE'").fetchone()
            oo = c.execute("SELECT quantity, zoro_qty FROM orders WHERE amazon_order_id='A-WRITE'").fetchone()
        check("order_placer_orders: quantity 1, zoro_qty 25", po == (1, 25), str(po))
        check("orders: quantity 1, zoro_qty 25", oo == (1, 25), str(oo))
        ns["_upsert_unified_db"]("A-WRITE2", dict(detail, amazon_id="A-WRITE2"), amazon_fees=5.55)
        with sqlite3.connect(tmp) as c:
            po2 = c.execute("SELECT quantity, zoro_qty FROM order_placer_orders WHERE amazon_order_id='A-WRITE2'").fetchone()
        check("a caller that does not know the Amazon quantity gets the old behaviour (both 25)", po2 == (25, 25), str(po2))
    finally:
        U.DB_PATH = real_path
    check("both placer call sites pass amazon_qty=primary_item.get(\"order_qty\")",
          osrc.count('amazon_qty=primary_item.get("order_qty"),') == 2)

    # ── 4. the order log and its sync were right all along; keep them that way ────────────
    print("\n── the order log → DB sync maps AQty to quantity and ZQty to zoro_qty ──")
    import importlib.util
    sp = importlib.util.spec_from_file_location("spl", os.path.join(HERE, "sync_order_placer_logs.py"))
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    line = ("[2026-09-21 03:47:56] | WB:WB1111111111 | Profile:DM_COUPON | Amazon:114-0000000-0000001 | ASIN:B0GXL2JC3V"
            " | Title:Shipping Boxes (25-Pack) | AQty:1 | SKU:G3056560 | ZQty:25 | Earnings:$31.44 | Cost:$25.79"
            " | ListPrice:$0.99 | Profit:$5.65 | FinalMargin:18.0% | Status:SUCCESS")
    e = m.parse_log_line(line); d = m.entry_to_db_data(e) if e else None
    check("AQty:1 → quantity 1", bool(d) and d.get("quantity") == 1, str(d and d.get("quantity")))
    check("ZQty:25 → zoro_qty 25", bool(d) and d.get("zoro_qty") == 25, str(d and d.get("zoro_qty")))

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
