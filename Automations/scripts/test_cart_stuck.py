#!/usr/bin/env python3
"""
test_cart_stuck.py — a leftover Zoro cart item must not stall the coupon placer for an hour.

12 Oct 2026: one item sat in HS_COUPON's cart. Every clear went straight to the /cart page, which
the headless Chrome never finished loading (60 s renderer timeout), so all 57 priority re-checks
of the new JANITORIAL71026 sweep failed "cart add failed", one minute each, before any order.
  • clear_cart_and_add now deletes the lines through the API first; the page is only the fallback;
  • the sweep stops after CART_STUCK_STOP such failures in a row, and those ASINs stay queued.

Run:  python scripts/test_cart_stuck.py   (no Chrome, no network, nothing written to data/)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   ({detail})" if detail and not ok else ""))


# ── 1. clear_cart_and_add ────────────────────────────────────────────────────────
from platforms import zoro  # noqa: E402


class FakeZoro:
    """A cart on Zoro's API. delete_works=False reproduces 21 Sep's HTTP 500 on DELETE."""

    def __init__(self, items, delete_works=True):
        self.items = list(items)
        self.delete_works = delete_works
        self.calls = []

    def api(self, driver, method, endpoint, body=None, content_type="application/json"):
        self.calls.append((method, endpoint))
        if method == "GET" and endpoint == "/v1/cart":
            return {"s": 200, "d": {"cartId": "C1", "items": [dict(x) for x in self.items]}}
        if method == "DELETE":
            if not self.delete_works:
                return {"s": 500, "d": "error"}
            zno = endpoint.rsplit("/", 1)[-1]
            self.items = [x for x in self.items if x["zoroNo"] != zno]
            return {"s": 200, "d": {}}
        if method == "POST":
            self.items.append({"zoroNo": body["item"]["zoroNo"], "qty": body["item"]["qty"]})
            return {"s": 200, "d": {"cartId": "C1", "items": list(self.items)}}
        return {"s": 404, "d": ""}


def run_add(fake, selenium_result=False):
    sel = []
    saved = (zoro.zoro_api, zoro.clear_cart_selenium, zoro._resolve_cart_item_state,
             zoro._extract_cart_item_qty, zoro._extract_cart_item_price, zoro.time.sleep)
    zoro.zoro_api = fake.api
    zoro.clear_cart_selenium = lambda d, timeout=90: (sel.append(1), selenium_result)[1]
    zoro._resolve_cart_item_state = lambda d, sku, r: {"zoroNo": sku, "qty": 2}
    zoro._extract_cart_item_qty = lambda it: it["qty"]
    zoro._extract_cart_item_price = lambda it: 12.5
    zoro.time.sleep = lambda s: None
    try:
        return zoro.clear_cart_and_add(None, "G1", 2), sel
    finally:
        (zoro.zoro_api, zoro.clear_cart_selenium, zoro._resolve_cart_item_state,
         zoro._extract_cart_item_qty, zoro._extract_cart_item_price, zoro.time.sleep) = saved


print("\nclear_cart_and_add")
fake = FakeZoro([{"zoroNo": "G999", "qty": 1}])
(ok, price), sel = run_add(fake)
check("leftover item is deleted through the API", ("DELETE", "/v1/cart/C1/items/G999") in fake.calls)
check("…and the cart page is never opened", not sel, f"selenium calls {len(sel)}")
check("the new item is added and priced", ok and price == 12.5, f"{ok} {price}")
check("only the new item is in the cart", [x["zoroNo"] for x in fake.items] == ["G1"], str(fake.items))

fake = FakeZoro([{"zoroNo": "G999", "qty": 1}], delete_works=False)
(ok, price), sel = run_add(fake, selenium_result=False)
check("API refuses the delete → the cart page is the fallback", len(sel) == 1)
check("…and if that fails too, nothing is added", (ok, price) == (False, 0)
      and not any(m == "POST" for m, _ in fake.calls))

fake = FakeZoro([])
(ok, price), sel = run_add(fake)
check("empty cart: no delete, no page, straight to add",
      ok and not sel and not any(m == "DELETE" for m, _ in fake.calls))

# ── 2. the sweep stops on a stuck cart ───────────────────────────────────────────
from engine import priority_check as pc  # noqa: E402
from utils import decision_log  # noqa: E402

decision_log.log = lambda *a, **k: None          # never write data/decisions in a test
pc._sheet_notes = lambda mod: False
pc.zoro_session_ready = lambda d: True
pc.load_verdicts = lambda: {}
pc.save_verdict = lambda *a, **k: None
pc.suggested_column = lambda p, s: ""
pc.write_json_atomic = lambda *a, **k: None


def sweep(stuck_set, n=6):
    asins = [(f"A{i}", "HS_Coupon") for i in range(1, n + 1)]
    tested, done = [], []

    def fake_check(driver, asin, sku_map, pri, cache=None, mod=None):
        tested.append(asin)
        if asin in stuck_set:
            return {"Check_ASIN": asin, "Check_Status": "TRANSIENT", "Check_Suggest": "Re-check later",
                    "Check_Note": "G1: cart add failed"}
        return {"Check_ASIN": asin, "Check_Status": "COUPON_OK", "Check_Suggest": "Coupon", "Check_Note": "ok"}

    pc.check_one = fake_check
    res = pc._run_batch(None, "HS_COUPON", asins, {}, ["JANITORIAL71026"], {}, None, None, None, [], [],
                        False, mode="recheck", on_done=done.append)
    return tested, done, res


print("\npriority sweep")
tested, done, res = sweep({"A1", "A2", "A3", "A4", "A5", "A6"})
check(f"stuck cart: stops after {pc.CART_STUCK_STOP} failures", tested == ["A1", "A2"], str(tested))
check("…and the failed ASINs stay queued", done == [], str(done))

tested, done, res = sweep({"A1", "A3", "A5"})
check("a failure between good ones does not stop the sweep", len(tested) == 6, str(tested))
check("…good ones leave the queue, cart failures stay", done == ["A2", "A4", "A6"], str(done))

tested, done, res = sweep(set())
check("healthy cart: every ASIN tested and dequeued", len(tested) == 6 and len(done) == 6)

tested, done, res = sweep({"A5", "A6"})
check("two failures at the very end: all tested, nothing to stop", len(tested) == 6 and done == ["A1", "A2", "A3", "A4"])

# ── 3. a page that never finishes loading ────────────────────────────────────────
from selenium.common.exceptions import TimeoutException  # noqa: E402
from utils.helpers import safe_get  # noqa: E402


class StallDriver:
    """driver.get times out (the 'load' event never fires) but the page may still be there."""

    def __init__(self, lands_on, ready="interactive", stall=True):
        self.lands_on, self.ready, self.stall = lands_on, ready, stall
        self.current_url, self.gets, self.stops = "chrome://new-tab-page/", 0, 0

    def get(self, url):
        self.gets += 1
        self.current_url = self.lands_on
        if self.stall:
            raise TimeoutException("timeout: Timed out receiving message from renderer: 58.3")

    def execute_script(self, js):
        if "window.stop" in js:
            self.stops += 1
            return None
        if "readyState" in js:
            return self.ready
        return None


def run_get(d, url):
    try:
        safe_get(d, url)
        return "ok"
    except TimeoutException:
        return "raised"


print("\nsafe_get on a stalled page")
d = StallDriver("https://www.zoro.com/")
check("zoro.com rendered but never 'loaded' → stopped and used, no 2nd 60 s wait",
      run_get(d, "https://www.zoro.com") == "ok" and d.gets == 1 and d.stops == 1, f"gets {d.gets} stops {d.stops}")
d = StallDriver("https://www.zoro.com/i/G1/", ready="complete")
check("PDP page: same", run_get(d, "https://www.zoro.com/i/G1") == "ok" and d.gets == 1)
d = StallDriver("chrome://new-tab-page/")
check("never left the start page → retried, then the timeout is raised",
      run_get(d, "https://www.zoro.com") == "raised" and d.gets == 2, f"gets {d.gets}")
d = StallDriver("https://www.zoro.com/", ready="loading")
check("document not even parsed → not used, retried and raised",
      run_get(d, "https://www.zoro.com") == "raised" and d.gets == 2)
d = StallDriver("https://geo.captcha-delivery.com/x")
check("landed on another site (a block page) → not used", run_get(d, "https://www.zoro.com") == "raised")
d = StallDriver("https://www.zoro.com/", stall=False)
check("normal load → returns at once", run_get(d, "https://www.zoro.com") == "ok" and d.stops == 0)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
