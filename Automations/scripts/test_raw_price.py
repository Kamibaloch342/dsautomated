#!/usr/bin/env python3
"""
test_raw_price.py — record Zoro's prices raw, and alert on the list price alone.

What was wrong (found 20 Sep 2026 from order 111-1549917-2638602's sibling, G3057148):

  * Tax was 97% captured until 17 Sep and 0% from the 19th. The page-checkout path has no API
    order response, so the audit line said Tax:N/A and "Cost" fell back to the cart total,
    which excludes tax and shipping. The Placer-log sync then wrote NULL/understated values.
  * The price-change alert reverse-engineered a "base unit" from an order total: divide by a
    quantity, un-apply a coupon by matching "20" in its name. G3057148 is MOQ 25 — Amazon qty
    1, Zoro qty 25, total $43.80 — so that math gave $43.80/unit one order and $1.75/unit the
    next, and the alerts were noise.
  * The raw per-unit list price ($2.19) was already returned by Zoro at cart-add (api_price)
    and never persisted per order.

The fix records five raw facts per order — list price, subtotal, shipping, tax, total — as
Zoro states them, and alerts when the LIST price moves ≥ 2% against the last one recorded for
the same Zoro code. These tests cover the page-summary reader, the audit line, the log sync,
the schema helper, and the rule itself.

Run:  python scripts/test_raw_price.py
"""
import ast
import importlib.util
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
    # ── 1. the checkout page's order summary, read raw ──────────────────────
    print("\n── the order summary is read off the page as separate numbers ──")
    from checkout.dom import _extract_checkout_summary
    page = """<html><body>
      <div>Order Summary</div>
      <div>Merchandise Subtotal</div><div>$329.99</div>
      <div>Shipping</div><div>Free</div>
      <div>Discount</div><div>-$33.00</div>
      <div>Estimated Tax</div><div>$21.42</div>
      <div data-za="order-summary-total">Total $318.41</div>
      </body></html>"""
    f = os.path.join(tempfile.mkdtemp(), "co.html")
    open(f, "w").write(page)
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        o = Options(); o.add_argument("--headless=new")
        o.add_argument("--user-data-dir=" + tempfile.mkdtemp())
        d = webdriver.Chrome(options=o)
    except Exception as e:
        print(f"  ⏭️  page reader skipped — Chrome would not start ({str(e)[:50]})")
        d = None
    if d is not None:
        try:
            d.get("file://" + f)
            s = _extract_checkout_summary(d)
            check("subtotal read raw", s["subtotal"] == 329.99, str(s))
            check("tax read raw", s["tax"] == 21.42, str(s))
            check("discount read as a positive amount", s["discount"] == 33.00, str(s))
            check("total taken from Zoro's own data-za hook", s["total"] == 318.41, str(s))
            check("'Free' shipping is $0.00 — Zoro charged nothing for that line", s["shipping"] == 0.0, str(s))
            check("a tax line on the page is read, not derived", s["tax_derived"] is False, str(s))
            d.get("data:text/html,<html><body>nothing here</body></html>")
            s2 = _extract_checkout_summary(d)
            check("a page with no summary yields all None, never raises",
                  all(s2[k] is None for k in ("subtotal", "shipping", "tax", "discount", "total")), str(s2))

            # 20 Sep 2026, order WB1965328687: Zoro's checkout showed no tax line at all, yet the
            # total carried $2.21 of tax — the placer logged Tax:N/A and lost it.
            page_notax = """<html><body><div>Order Summary</div>
              <div>Subtotal</div><div>$29.70</div>
              <div>Shipping</div><div>$5.99</div>
              <div>Total</div><div>$37.90</div></body></html>"""
            f2 = os.path.join(tempfile.mkdtemp(), "notax.html"); open(f2, "w").write(page_notax)
            d.get("file://" + f2)
            s3 = _extract_checkout_summary(d)
            check("no tax line: tax is the remainder of Zoro's total (37.90 - 29.70 - 5.99 = 2.21)",
                  s3["tax"] == 2.21 and s3["tax_derived"] is True, str(s3))
            page_labels = """<html><body><div>Order Summary</div>
              <div>Merchandise Subtotal: $29.70</div>
              <div>Shipping: Free</div>
              <div>Est. Sales Tax: $1.78</div>
              <div>Order Total: $31.48</div></body></html>"""
            f3 = os.path.join(tempfile.mkdtemp(), "labels.html"); open(f3, "w").write(page_labels)
            d.get("file://" + f3)
            s4 = _extract_checkout_summary(d)
            check("'Est. Sales Tax' on the label's own line is read raw, shipping 'Free' is 0",
                  s4["tax"] == 1.78 and s4["tax_derived"] is False and s4["shipping"] == 0.0
                  and s4["subtotal"] == 29.70 and s4["total"] == 31.48, str(s4))
            page_absurd = """<html><body><div>Subtotal</div><div>$29.70</div>
              <div>Shipping</div><div>$5.99</div><div>Total</div><div>$99.00</div></body></html>"""
            f4 = os.path.join(tempfile.mkdtemp(), "absurd.html"); open(f4, "w").write(page_absurd)
            d.get("file://" + f4)
            s5 = _extract_checkout_summary(d)
            check("an absurd remainder (63.31 on a 29.70 subtotal) is a misread page → tax stays None",
                  s5["tax"] is None and s5["tax_derived"] is False, str(s5))

            # Every piece of JavaScript checkout/ ships must at least parse in Chrome. (20 Sep
            # 2026: a control character inside one regex literal made every DM_RESELLER checkout
            # throw "Invalid regular expression: missing /" before Place Order.)
            import ast as _ast
            import re as _re
            _blobs = []
            for _rel in ("checkout/dom.py", "checkout/true_api.py"):
                for _n in _ast.walk(_ast.parse(src(_rel))):
                    if (isinstance(_n, _ast.Constant) and isinstance(_n.value, str) and "\n" in _n.value
                            and ("document." in _n.value or "arguments[" in _n.value)):
                        _js = _re.sub(r"%\([^)]*\)[sdrf]|%[sdrf]", "0", _n.value)   # %-templates
                        _blobs.append((f"{_rel}:{_n.lineno}", _js))
            _fails = []
            for _where, _js in _blobs:
                _err = d.execute_script(
                    "try { new Function(arguments[0]); return null; } catch (e) { return String(e); }", _js)
                if _err:
                    _fails.append(f"{_where} → {str(_err)[:80]}")
            check(f"all {len(_blobs)} JavaScript blobs in checkout/ parse in Chrome", not _fails, " | ".join(_fails[:4]))
        finally:
            d.quit()

    # ── 2. the attributed path carries the summary into the result ─────────
    print("\n── page checkout no longer loses tax and the charged total ──")
    ta = src("checkout/true_api.py")
    fb = func_src(ta, "_dom_place_order_fallback")
    check("the summary is read BEFORE Place Order is clicked",
          "_extract_checkout_summary(driver)" in fb
          and fb.index("_extract_checkout_summary") < fb.index("_click_place_order(driver)"))
    check("it is stashed on the driver so no call site's contract changes",
          "driver._zuhra_checkout_summary" in fb)
    main_fn = func_src(ta, "place_order_fast_true_api")
    check("with no API response, tax/discount/subtotal/shipping come from the page summary",
          "_zuhra_checkout_summary" in main_fn and "checkout_subtotal" in main_fn
          and "checkout_shipping" in main_fn)
    check("the page total replaces the cart total as what was charged",
          'checkout_total = float(_page["total"])' in main_fn)
    check("the result carries subtotal and shipping as their own fields",
          "subtotal=checkout_subtotal, shipping=checkout_shipping" in main_fn)
    dm = src("checkout/dom.py")
    check("_format_checkout_result exposes them",
          '"subtotal": subtotal' in dm and '"shipping": shipping' in dm)

    # ── 3. the audit line — the source of truth the log sync reads ──────────
    print("\n── the order log line carries the raw facts ──")
    au = func_src(ta, "_save_order_audit")
    check("ListPrice is its own field on the line", "ListPrice:{s(fmt_money(list_price))}" in au)
    check("Cost is what Zoro charged: response total, else page total, else cart total",
          "Cost:{s(fmt_money(_charged))}" in au and "_pg.get('total')" in au)
    check("Tax falls back to the page summary when there is no API response",
          "_pg.get('tax')" in au)
    check("Subtotal and Shipping fall back the same way",
          "_pg.get('subtotal')" in au and "_pg.get('shipping')" in au)
    check("the placer passes the raw unit list price into checkout",
          'list_price=primary_item.get("api_price")' in src("engine/orders.py"))
    check("and checkout passes it, with the page summary, into the audit",
          "list_price=list_price," in ta and 'page_summary=getattr(driver, "_zuhra_checkout_summary", None)' in ta)

    # ── 4. the log sync parses them, N/A → NULL ─────────────────────────────
    print("\n── the Placer-log sync carries the new fields ──")
    sp = importlib.util.spec_from_file_location("sync_pl", os.path.join(HERE, "sync_order_placer_logs.py"))
    sync = importlib.util.module_from_spec(sp); sp.loader.exec_module(sync)
    line = ("[2026-09-20 01:00:00] | WB:WB1298515239 | Amazon:111-1549917-2638602 | Profile:DM_COUPON "
            "| SKU:G3057148 | ASIN:B0TEST | AQty:1 | ZQty:25 | Earnings:$60.00 "
            "| Cost:$47.31 | ListPrice:$2.19 | Coupon:ZSELECT9MA26 | Subtotal:$43.80 "
            "| Discount:$10.95 | Shipping:$0.00 | Tax:$3.51 | Profit:$12.69 | FinalMargin:21.2%")
    entry = sync.parse_log_line(line)
    check("the line parses", bool(entry) and entry.get("ListPrice") == "$2.19", str(entry)[:80])
    check("_parse_money: ListPrice → 2.19", sync._parse_money(entry.get("ListPrice")) == 2.19)
    check("_parse_money: N/A → None (a missing tax must stay NULL, not become 0)",
          sync._parse_money("N/A") is None and sync._parse_money("") is None)
    ss = src("scripts/sync_order_placer_logs.py")
    check("the sync maps ListPrice/Subtotal/Shipping to the three columns",
          '"zoro_list_price": _parse_money(entry.get("ListPrice", ""))' in ss
          and '"zoro_subtotal": _parse_money(entry.get("Subtotal", ""))' in ss
          and '"zoro_shipping": _parse_money(entry.get("Shipping", ""))' in ss)
    check("the sync ensures the columns exist before writing",
          '("zoro_list_price", "REAL")' in ss and '("zoro_shipping", "REAL")' in ss)

    # ── 5. schema helper + baseline lookup, on a scratch DB ─────────────────
    print("\n── schema and the baseline lookup ──")
    import utils.unified_db as U
    dbp = os.path.join(tempfile.mkdtemp(), "t.db")
    con = sqlite3.connect(dbp)
    con.execute("CREATE TABLE order_placer_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "amazon_order_id TEXT UNIQUE, sku TEXT, zoro_order_total REAL, zoro_tax REAL, synced_at TEXT)")
    con.commit()
    added = U.ensure_placer_price_columns(conn=con)
    check("adds the three columns to an older table", set(added) == set(U.PLACER_PRICE_COLUMNS), str(added))
    check("running it again adds nothing", U.ensure_placer_price_columns(conn=con) == [])
    con.execute("INSERT INTO order_placer_orders (amazon_order_id, sku, zoro_list_price) VALUES ('A1','G3057148',2.19)")
    con.execute("INSERT INTO order_placer_orders (amazon_order_id, sku, zoro_list_price) VALUES ('A2','G3057148',NULL)")
    con.execute("INSERT INTO order_placer_orders (amazon_order_id, sku, zoro_list_price) VALUES ('A3','G3057148',2.29)")
    con.commit()
    check("baseline is the newest row that HAS a list price",
          U.last_list_price("G3057148", conn=con) == 2.29)
    check("the order being evaluated is excluded (a first placement seeds, never self-compares)",
          U.last_list_price("G3057148", exclude_amazon_id="A3", conn=con) == 2.19)
    check("case-insensitive on the code", U.last_list_price("g3057148", conn=con) == 2.29)
    check("unknown code → None", U.last_list_price("G0000000", conn=con) is None)
    check("the placer keeps these off the orders-table write (its columns come from data.keys())",
          '_placer_only = {k: extra.pop(k, None) for k in ("zoro_list_price", "zoro_subtotal", "zoro_shipping", "zoro_deliver_by")}'
          in src("engine/orders.py"))
    con.close()

    # ── 6. the rule: raw vs raw, 2% ─────────────────────────────────────────
    print("\n── the alert: list price vs last list price, ≥ 2% ──")
    import utils.insights_engine as IE
    real = U.last_list_price
    baseline = {"v": None}
    U.last_list_price = lambda sku, exclude_amazon_id="", db_path=None, conn=None: baseline["v"]

    def alerts(cur, last, zqty=1):
        baseline["v"] = last
        out = IE.analyze_order_insights({"zoro_code": "G3057148", "zoro_catalog_price": cur,
                                         "amazon_id": "X1", "zoro_qty": zqty, "quantity": 1,
                                         "asin": "B0TEST", "account": "Hathoro"})
        return [i for i in out if i.get("code") == "ZORO_LIST_PRICE_CHANGED"]
    try:
        check("first placement (no baseline) → no alert", alerts(2.19, None) == [])
        check("same price → no alert", alerts(2.19, 2.19) == [])
        check("+1.9% (a rise of 4¢) → alert — any rise counts", len(alerts(2.19 * 1.019, 2.19)) == 1)
        a = alerts(2.19 * 1.021, 2.19)
        check("+2.1% → alert", len(a) == 1 and "UP" in a[0]["summary"], str(a)[:90])
        a = alerts(2.19 * 0.97, 2.19)
        check("-3% (a drop) → no alert — only rises are alerts", a == [])
        a = alerts(2.30, 2.19)
        check("the alert names both prices and the delta",
              "$2.19" in a[0]["summary"] and "$2.30" in a[0]["summary"] and "+$0.11" in a[0]["summary"] and "/ea" in a[0]["summary"])
        a1 = alerts(2.19, 2.19, zqty=25)
        a2 = alerts(2.19, 2.19, zqty=1)
        check("an MOQ-25 order and a qty-1 order at the same list price both stay silent",
              a1 == [] and a2 == [])
        check("one cent is enough; a sub-cent float artefact is not",
              len(alerts(2.20, 2.19)) == 1 and alerts(2.194, 2.19) == [] and alerts(10.552000000000001, 10.55) == [])
        check("the old derived-from-totals rule is off unless explicitly re-enabled",
              IE._LEGACY_BASE_PRICE_ALERT is False
              and "if _LEGACY_BASE_PRICE_ALERT and current_base_unit > 0 and zoro_code:" in src("utils/insights_engine.py"))
        check("the alert dict has what notify_insight renders (summary, account, asin)",
              all(k in a[0] for k in ("summary", "account", "asin", "code")))
    finally:
        U.last_list_price = real

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
