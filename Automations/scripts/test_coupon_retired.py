#!/usr/bin/env python3
"""
test_coupon_retired.py — a regular coupon that died or was used this cycle is not tried again.

12 Oct 2026: the coupon sheet is read once per cycle. ~40 dead EMZM codes sat ahead of the live
ones, and every DM_COUPON order re-tried all of them (~80 s an order) — each was marked INVALID
again and again (398 marks in one afternoon). _apply_coupons_api now drops any regular code the
metadata file already calls USED or INVALID. Priority codes are untouched (they are reusable).

Run:  python scripts/test_coupon_retired.py   (no Chrome, no network, nothing written to data/)
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


from checkout import true_api as ta  # noqa: E402
from utils import helpers  # noqa: E402

META = {}
helpers.load_coupon_metadata = lambda: dict(META)                 # the function _apply_coupons_api imports
helpers.update_coupon_status = lambda code, status: META.__setitem__(code, status)
ta.update_coupon_status = helpers.update_coupon_status            # true_api's own reference (never the real file)
ta._get_cart = lambda d: {"discountTotal": 5.0}
ta._time = lambda *a, **k: None
try:
    from utils import decision_log
    decision_log.log = lambda *a, **k: None
except Exception:
    pass


def run(regular, priority=(), live=(), dead=()):
    tried = []

    def fake_apply(driver, cart_id, code):
        tried.append(code)
        if code in live:
            return True, 5.0
        return False, "This promo code is no longer available." if code in dead else "Promo code is not valid for the items in your cart"
    ta._apply_coupon_api = fake_apply
    res = ta._apply_coupons_api(None, "C1", list(regular), list(priority), "B0TEST", {}, zoro_code="G1")
    return res, tried


print("\nregular coupons")
META.clear(); META.update({"D1": "INVALID", "D2": "INVALID", "U1": "USED"})
res, tried = run(["D1", "D2", "U1", "L1", "L2"], live={"L1", "L2"})
check("codes already dead / used are not tried", tried == ["L1"], str(tried))
check("…and the first live code wins", res == ("SUCCESS", "L1"), str(res))

META.clear()
res, tried = run(["X1", "X2", "L1"], live={"L1"}, dead={"X1", "X2"})
check("first order: unknown dead codes are tried once and marked INVALID",
      tried == ["X1", "X2", "L1"] and META.get("X1") == "INVALID" and META.get("X2") == "INVALID", f"{tried} {META}")
res, tried = run(["X1", "X2", "L1"], live={"L1"}, dead={"X1", "X2"})
check("next order in the same cycle skips them", tried == ["L1"], str(tried))

META.clear(); META.update({"P1": "INVALID"})
res, tried = run(["R1"], priority=["P1"], live={"P1"})
check("priority codes are not filtered by the regular rule", tried == ["P1"] and res == ("SUCCESS", "P1"), f"{tried} {res}")

META.clear(); META.update({"D1": "INVALID"})
res, tried = run(["D1"])
check("all regulars gone → nothing tried, a plain COUPON FAIL", tried == [] and res[0] == "COUPON FAIL", f"{tried} {res}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
