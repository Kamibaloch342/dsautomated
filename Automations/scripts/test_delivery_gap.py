#!/usr/bin/env python3
"""
test_delivery_gap.py — an order Zoro would deliver 4+ days after Amazon's deliver-by is not placed.

15 Oct 2026: 114-6478697-4599444 (BayTec drum ×4) promised delivery by Fri 16 Oct; Zoro's product
page said "delivery on or before" 4 Nov, and DM_COUPON bought it anyway. Owner: when the difference is
4 days or more, don't place — write "Delivery time high" in the Seller order ID.

Run:  python scripts/test_delivery_gap.py   (no Chrome, no network; a temporary DB, never the real one)
"""
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)
sys.path.insert(0, os.path.join(AUTO, "dashboard"))

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   ({detail})" if detail and not ok else ""))


import engine.orders as O  # noqa: E402

print("\nthe gap")
check("114-6478697-4599444: Amazon 'Fri, Oct 16, 2026' vs Zoro 2026-11-04 → 19 days",
      O._delivery_gap_days("Fri, Oct 16, 2026", "2026-11-04") == 19)
check("4 days late (Oct 16 → Oct 20) is blocked at the default limit",
      O._delivery_gap_days("Fri, Oct 16, 2026", "2026-10-20") >= O._delivery_gap_block_days() == 4)
check("3 days late is placed", O._delivery_gap_days("Fri, Oct 16, 2026", "2026-10-19") == 3)
check("Zoro earlier than Amazon → negative, placed", O._delivery_gap_days("Wed, Oct 28, 2026", "2026-10-20") == -8)
check("unknown on either side → None (the order goes ahead as before)",
      O._delivery_gap_days("", "2026-11-04") is None and O._delivery_gap_days("Fri, Oct 16, 2026", "") is None)
check("dates as the DB stores them ('...| ZoroDeliverBy:') and without a weekday parse",
      O._as_date("Thu, Oct 15, 2026| ZoroDeliverBy:2026-10-19") == date(2026, 10, 15)
      and O._as_date("Oct 16, 2026") == date(2026, 10, 16) and O._as_date("2026-10-14 22:10:48") == date(2026, 10, 14)
      and O._as_date("2026-10-14T03:14") == date(2026, 10, 14))

print("\nthe limit")
_cfg = O.gui_cfg
try:
    O.gui_cfg = {"delivery_gap_block_days": 6}
    six = O._delivery_gap_block_days()
    O.gui_cfg = {"delivery_gap_block_days": 0}
    off = O._delivery_gap_block_days()
    O.gui_cfg = {}
    dflt = O._delivery_gap_block_days()
finally:
    O.gui_cfg = _cfg
check("bot_config delivery_gap_block_days overrides; 0 turns it off; default 4", (six, off, dflt) == (6, 0, 4),
      str((six, off, dflt)))

print("\nrush mode: the SKU's recent lead time")
db = os.path.join(tempfile.mkdtemp(), "u.db")
c = sqlite3.connect(db)
c.execute("CREATE TABLE order_placer_orders (amazon_order_id TEXT, sku TEXT, placed_at TEXT, zoro_deliver_by TEXT, "
          "main_sku TEXT)")
y = date.today() - timedelta(days=1)
c.executemany("INSERT INTO order_placer_orders VALUES (?,?,?,?,?)", [
    ("A", "G9084824", f"{y} 22:10:48", (y + timedelta(days=21)).isoformat(), ""),        # lead 21 days
    ("B", "G1111111", f"{date.today() - timedelta(days=6)} 10:00:00", "2026-01-01", ""), # too old
    ("C", "G2222222", f"{y} 09:00:00", "", ""),                                          # no estimate
    ("D", "G1967707", f"{y}T13:35", (y + timedelta(days=11)).isoformat(), "G105340985")]) # ALT row: main's date
c.commit()
c.close()
est = O._recent_zoro_estimate("G9084824", db_path=db)
check("a placement yesterday with a 21-day lead → today + 21", est == (date.today() + timedelta(days=21)).isoformat(), est)
check("older than 3 days, or no estimate recorded → '' (no guess)",
      O._recent_zoro_estimate("G1111111", db_path=db) == "" and O._recent_zoro_estimate("G2222222", db_path=db) == "")
check("a broken DB path never raises", O._recent_zoro_estimate("G9084824", db_path="/nonexistent/x.db") == "")
check("an ALT placement (it carries the out-of-stock main's backorder date) is never learned from",
      O._recent_zoro_estimate("G1967707", db_path=db) == "")

print("\nthe decision itself")
calls = []
fast = lambda v: (lambda: (calls.append(1), (v, f"fast={v}"))[1])
check("19 days late, SKU has no history → held", O._delivery_gap_hold("Fri, Oct 16, 2026", "2026-11-04", 4, fast(None))[0] is True)
check("19 days late, SKU's last order slow → held", O._delivery_gap_hold("Fri, Oct 16, 2026", "2026-11-04", 4, fast(False))[0] is True)
check("19 days late, SKU's last order shipped within 2 days → placed",
      O._delivery_gap_hold("Fri, Oct 16, 2026", "2026-11-04", 4, fast(True))[0] is False)
calls.clear()
r3 = O._delivery_gap_hold("Fri, Oct 16, 2026", "2026-10-19", 4, fast(None))
r4 = O._delivery_gap_hold("Fri, Oct 16, 2026", "2026-10-20", 4, fast(None))
check("3 days late placed, 4 days late held — compared with DELIVER-BY", r3[0] is False and r4[0] is True, f"{r3} {r4}")
check("the history is only looked up when the gap reaches the limit", len(calls) == 1, str(calls))
check("limit 0 = off; no estimate = placed",
      O._delivery_gap_hold("Fri, Oct 16, 2026", "2026-11-04", 0, fast(None))[0] is False
      and O._delivery_gap_hold("Fri, Oct 16, 2026", "", 4, fast(None))[0] is False)

print("\nZoro's date is only believed when the SKU has not just shipped fast")
from datetime import datetime as _dt
db2 = os.path.join(tempfile.mkdtemp(), "h.db")
c = sqlite3.connect(db2)
c.execute("CREATE TABLE order_placer_orders (amazon_order_id TEXT, sku TEXT, placed_at TEXT)")
c.execute("CREATE TABLE closing_orders (amazon_order_id TEXT, zoro_shipped_pkt TEXT, shipped_email_time TEXT)")
T = date(2026, 10, 15)
d = lambda n: (T - timedelta(days=n)).isoformat()
c.executemany("INSERT INTO order_placer_orders VALUES (?,?,?)", [
    ("F1", "G5803393", d(2) + "T02:04"),        # placed 13 Oct, shipped same day  → fast
    ("S1", "G9084824", d(3) + "T13:44"),        # placed 12 Oct, still unshipped   → slow
    ("Y1", "G7000001", d(1) + " 10:00:00"),     # 1 day old, unshipped             → says nothing
    ("L1", "G7000002", d(10) + " 10:00:00")])   # shipped 6 days after             → slow
c.executemany("INSERT INTO closing_orders VALUES (?,?,?)", [
    ("F1", d(2) + " 04:51", ""), ("S1", "", ""), ("Y1", "", ""), ("L1", "", d(4) + " 09:00:00")])
c.execute("INSERT INTO order_placer_orders VALUES ('N1', 'G7000003', ?)", (d(4) + "T08:00",))
c.execute("INSERT INTO closing_orders VALUES ('N1', 'Sun, Oct 11', '')")          # ship date with no year
c.commit()
c.close()
now = _dt(2026, 10, 15, 9, 0)
f = lambda sku: O._sku_ships_fast(sku, db_path=db2, now=now)[0]
check("G5803393 shipped the day it was ordered → Zoro's late date is not believed (placed)", f("G5803393") is True)
check("G9084824 ordered 3 days ago and still unshipped → believed (held)", f("G9084824") is False)
check("only a 1-day-old unshipped order → no verdict (held on Zoro's date)", f("G7000001") is None)
check("shipped 6 days after ordering → believed (held)", f("G7000002") is False)
check("unknown SKU → no verdict", f("G0000000") is None)
check("a ship date written without a year ('Sun, Oct 11') is read, not taken as 'not shipped'", f("G7000003") is True,
      str(O._sku_ships_fast("G7000003", db_path=db2, now=now)))
check("unreadable history never raises", O._sku_ships_fast("G9084824", db_path="/nonexistent/x.db")[0] is None)

print("\nwiring in process_order")
src = open(os.path.join(AUTO, "engine", "orders.py"), encoding="utf-8").read()
body = src[src.index("def process_order("):src.index("#  COUPON SHEET LOADER")]
g = body.index("# ── Zoro would deliver too late for Amazon's promise")
check("the check runs before the cart is built and before the placement lock is taken",
      g < body.index("first_cart_item = True") and g < body.index("plock.reserve(amazon_id"))
gate = body[g:body.index("# ── PDP promo badge", g)]          # the whole check, up to the next step
check("…after Zoro's estimate is read", body.index('addr["_zoro_deliver_by"] = _zdel') < g)
check("only when the primary SKU itself is in stock (an out-of-stock main goes to the ALT / OOS path)",
      "_pcart, _ = check_inventory(driver, _psku" in body and "and resolved_items and _pcart:" in gate)
check("the decision is the tested pure function", "_delivery_gap_hold(order_detail.get(\"deliver_by_date\", \"\"), _zest" in gate)
check("a held order sends one alert", "_notify_delivery_gap(" in gate)
alt = body[body.index('item["alt_used"] = True'):][:300]
check("an ALT swap drops the main's backorder date from the order record", 'addr.pop("_zoro_deliver_by", None)' in alt)
check("a Manual-C/-R order is never held", "not manual_override" in gate.split("\n")[5] or
      "if _gap_limit > 0 and not manual_override" in gate)
check("a late date on a SKU that just shipped fast is placed, with the reason printed",
      "lambda: _sku_ships_fast(_gsku)" in gate and "if not _hold and _hist:" in gate)
check("rush mode: label journaled, estimate from the recent lead time",
      "_rush_defer_wb(amazon_id, DELIVERY_GAP_LABEL)" in gate and "_recent_zoro_estimate(" in gate)
check("label written, order logged DELIVERY_TIME_HIGH and returned as skipped (not failed)",
      "update_amazon_seller_id(driver, amazon_id, DELIVERY_GAP_LABEL" in gate and '"DELIVERY_TIME_HIGH"' in gate
      and 'return "skipped"' in gate)
skip = body[body.index('merchant_id.upper() in ("OOS"'):][:200]
check("a labelled order is not picked up again (with OOS / SR / 20 in the already-marked list)",
      "DELIVERY_GAP_LABEL.upper()" in skip and body.index('merchant_id.upper() in ("OOS"') < g)
check("the label fits Amazon's Seller order ID field", len(O.DELIVERY_GAP_LABEL) <= O._AMZ_SELLER_ID_MAX)

import month_report as M  # noqa: E402
check("Daily report names the reason", M._code_reason("Delivery time high") == "Zoro delivery too late for Amazon's date",
      M._code_reason("Delivery time high"))

print("\nthe alert")
sent = []
import utils.whatsapp as W  # noqa: E402
import utils.telegram as T  # noqa: E402
_w, _t = W.send_whatsapp_message, T.send_telegram_message
W.send_whatsapp_message = lambda text, *a, **k: sent.append(("wa", text)) or True
T.send_telegram_message = lambda text, *a, **k: sent.append(("tg", text)) or True
try:
    O._DELIVERY_GAP_ALERTED.discard("114-6478697-4599444")
    for _ in range(2):
        O._notify_delivery_gap("114-6478697-4599444", "B07B6B69G4", "G9084824",
                               "Zoro delivers by 2026-11-04, 19 days after Amazon's deliver-by Fri, Oct 16, 2026",
                               "Thu, Oct 15, 2026")
finally:
    W.send_whatsapp_message, T.send_telegram_message = _w, _t
check("one WhatsApp + one Telegram per held order, with the ship-by and how to override",
      [k for k, _ in sent] == ["wa", "tg"] and all("Manual-C/-R" in t and "Oct 15" in t for _, t in sent), str(sent))

print("\nrush mode: place even when no delivery date is captured; non-rush never places blind")
import checkout.true_api as TA  # noqa: E402


class _NoDateDriver:
    current_url = "https://www.zoro.com/checkout"

    def get(self, url):
        self.current_url = url

    def execute_script(self, script, *args):
        return True


check("non-rush: no checkout date and no product-page date → DELIVERY_UNKNOWN (not placed)",
      TA._read_checkout_delivery(_NoDateDriver(), {"_delivery_gap_limit": 4, "_rush_mode": False},
                                 lambda d, a: None) == "DELIVERY_UNKNOWN")
check("rush mode: no checkout date and no product-page date → None (proceeds to place)",
      TA._read_checkout_delivery(_NoDateDriver(), {"_delivery_gap_limit": 4, "_rush_mode": True},
                                 lambda d, a: None) is None)
check("process_order stamps addr['_rush_mode'] from defer_amazon_writes",
      'addr["_rush_mode"] = bool(defer_amazon_writes)' in body)
ta_src = open(os.path.join(AUTO, "checkout", "true_api.py"), encoding="utf-8").read()
check("API checkout skips _checkout_page_delivery_status in rush mode",
      'not (address or {}).get("_rush_mode")' in ta_src)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)

