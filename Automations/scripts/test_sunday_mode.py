#!/usr/bin/env python3
"""
test_sunday_mode.py — Sunday mode holds back exactly what Monday's priority coupons could improve.

The owner's rule (11 Oct 2026): the weekly priority coupons land on Monday around 10:00 PKT and
there are 700-800 orders to place by 16:00 PKT, so waiting for everything is not an option — but
an order bought on Sunday with a regular coupon, or at reseller price, may be one a priority code
would have covered. So on Sunday:
  reseller   places only a Zoro cart (list) under the set amount at the set margin or better;
  coupon     uses priority coupons only, never a regular one;
  the rest   waits, UNLABELLED, so unticking the box releases everything at once.

Run:  python scripts/test_sunday_mode.py   (no Chrome, no network, no orders)
"""
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   ({detail})" if detail and not ok else ""))


def cfg_file(block):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump({"profiles": {}, "sunday_mode": block} if block is not None else {"profiles": {}}, fh)
    return path


def main():
    import engine.orders as O

    print("\n── reading the launcher's setting ──")
    check("absent → off", O._sunday_read(cfg_file(None)) is None)
    check("unticked → off", O._sunday_read(cfg_file({"enabled": False, "max_cart": 50})) is None)
    v = O._sunday_read(cfg_file({"enabled": True}))
    check("ticked with no numbers → $50 and 20 %", v and v["max_cart"] == 50 and abs(v["min_margin"] - 0.20) < 1e-9, str(v))
    v = O._sunday_read(cfg_file({"enabled": True, "max_cart": "75", "min_margin_pct": 30}))
    check("the owner's numbers are used", v and v["max_cart"] == 75 and abs(v["min_margin"] - 0.30) < 1e-9, str(v))

    print("\n── a bad read never switches it off ──")
    good = {"max_cart": 50.0, "min_margin": 0.2, "sig": "50.00|0.2000"}
    O._SUNDAY_CFG.update(at=0.0, val=good, loaded=True)
    real = O._sunday_read
    O._sunday_read = lambda path=None: (_ for _ in ()).throw(ValueError("half-written file"))
    check("an unreadable bot_config keeps the last setting", O._sunday_mode() == good)
    O._sunday_read = real

    print("\n── the reseller rule: Zoro COST including shipping (owner: shipping is a cost) ──")
    sun = good
    check("$34.99 + $5.99 shipping = $40.98 at 34 % is placed", O._sunday_gate(40.98, 0.342, sun, 5.99) is None)
    g = O._sunday_gate(51.99, 0.40, sun, 5.99) or ""
    check("$46.00 + $5.99 shipping = $51.99 waits", "51.99" in g, g)
    check("…and the reason names the shipping", "incl. $5.99 shipping" in g, g)
    check("exactly $50.00 waits (under means under)", O._sunday_gate(50.00, 0.35, sun) is not None)
    check("$42 cost at 25 % is placed", O._sunday_gate(42.0, 0.25, sun) is None)
    check("$42 cost at 12 % waits", "margin" in (O._sunday_gate(42.0, 0.12, sun) or ""))
    check("unknown earnings: only the cart decides (their own gate stops them)", O._sunday_gate(42.0, None, sun) is None)
    check("Sunday mode off: nothing waits", O._sunday_gate(500.0, -0.5, None) is None)

    print("\n── what the reseller would pay on a coupon profile's hand-over ──")
    c, sh = O._sunday_reseller_cost(46.0, all_zoro_select=False)
    check("$46 list pays $5.99 shipping → $51.99", abs(c - 51.99) < 1e-6 and sh == 5.99, str((c, sh)))
    c, sh = O._sunday_reseller_cost(54.75, all_zoro_select=True)
    check("Zoro Select $54.75 list → $49.27 after Select, ships free", abs(c - 49.275) < 1e-6 and sh == 0, str((c, sh)))
    check("a $46 hand-over waits on the cost", O._sunday_handover_gate(46.0, 90.0, False, good) is not None)
    check("the Select $54.75 hand-over at a healthy margin goes", O._sunday_handover_gate(54.75, 70.0, True, good) is None)
    m = O._sunday_reseller_margin(54.75, 70.0, all_zoro_select=True)
    check("Zoro Select at 10 % off, no fee on a $54.75 list", abs(m - (70.0 - 49.275) / 49.275) < 1e-6, str(m))
    m = O._sunday_reseller_margin(40.0, 60.0, all_zoro_select=False)
    check("a $40 list carries the $5.99 fee", abs(m - (60.0 - 45.99) / 45.99) < 1e-6, str(m))

    print("\n── held orders stay held, and only for good reason ──")
    pool = ([{"code": "FALL20"}], ["EMZM1", "EMZM2"])
    O._SUNDAY_CFG.update(at=time.time(), val=good, loaded=True)
    O._SUNDAY_HOLDS.clear()
    O._sunday_park("A1", "cart", pool, good)
    check("a held order is skipped next cycle", O._sunday_parked("A1", pool) is True)
    check("a new regular-coupon scrape does not wake it",
          O._sunday_parked("A1", ([{"code": "FALL20"}], ["EMZM9"])) is True)
    check("a new PRIORITY code wakes it (Monday morning)",
          O._sunday_parked("A1", ([{"code": "FALL20"}, {"code": "TOOLS15"}], [])) is False)
    O._sunday_park("A2", "cart", pool, good)
    check("a Manual-R tag wakes it", O._sunday_parked("A2", pool, "Manual-R") is False)
    O._sunday_park("A3", "cart", pool, good)
    O._SUNDAY_CFG.update(at=time.time(), val=dict(good, sig="75.00|0.2000"), loaded=True)
    check("changing the numbers wakes it", O._sunday_parked("A3", pool) is False)
    O._SUNDAY_CFG.update(at=time.time(), val=good, loaded=True)
    O._sunday_park("A4", "cart", pool, good)
    O._SUNDAY_HOLDS["A4"]["at"] -= O.SUNDAY_HOLD_MAX_AGE + 1
    check("after 6 hours it is re-priced", O._sunday_parked("A4", pool) is False)
    O._sunday_park("A5", "cart", pool, good)
    O._SUNDAY_CFG.update(at=time.time(), val=None, loaded=True)
    check("unticking Sunday mode releases it", O._sunday_parked("A5", pool) is False)

    print("\n── coupon-blocked items are not held (no coupon can ever apply) ──")
    O.PROMO_INELIGIBLE_ASINS.clear(); O.PROMO_INELIGIBLE_CODES.clear()
    O.PROMO_INELIGIBLE_ASINS.add("B0LISTED"); O.PROMO_INELIGIBLE_CODES.add("G111")
    badge = {"G222": True, "G333": False}
    real_pdp = O.pdp_promo_ineligible
    O.pdp_promo_ineligible = lambda sku: badge.get(sku)
    check("an ASIN on the promo-ineligible list", O._all_coupon_blocked([{"asin": "B0LISTED", "sku": "G999"}]) is True)
    check("a Zoro code on the learned list", O._all_coupon_blocked([{"asin": "B0X", "sku": "G111"}]) is True)
    check("Zoro's 'not eligible for any promotions' page badge", O._all_coupon_blocked([{"asin": "B0Y", "sku": "G222"}]) is True)
    check("an ordinary item is still held", O._all_coupon_blocked([{"asin": "B0Z", "sku": "G333"}]) is False)
    check("an item never checked is still held", O._all_coupon_blocked([{"asin": "B0Q", "sku": "G444"}]) is False)
    check("two items, only one blocked → still held (Monday could improve the other)",
          O._all_coupon_blocked([{"asin": "B0LISTED", "sku": "G111"}, {"asin": "B0Z", "sku": "G333"}]) is False)
    check("both items blocked → placed", O._all_coupon_blocked([{"sku": "G111"}, {"sku": "G222"}]) is True)
    check("no items → not exempt", O._all_coupon_blocked([]) is False)
    O.pdp_promo_ineligible = real_pdp
    O.PROMO_INELIGIBLE_ASINS.clear(); O.PROMO_INELIGIBLE_CODES.clear()

    print("\n── stripping regular coupons can never brand an item promo-ineligible ──")
    lf = {"total": 2, "item": 2, "priority": 2, "regular_available": 0,
          "priority_item": 2, "priority_dead": 0, "regular_dead": 0}
    r = O._coupon_fail_decision(lf, 60.0, 80.0, False, 5.99, 50.0, 0.05)
    check("both priority codes rejected the item, no regulars tried → not definitive",
          not r.get("definitive"), str(r))

    print("\n── the order of operations in process_order ──")
    src = open(os.path.join(AUTO, "engine", "orders.py"), encoding="utf-8").read()
    body = src[src.index("def process_order("):src.index("#  COUPON SHEET LOADER")]
    i_strip = body.index("priority coupons only — {len(regular_coupon_codes)} regular code(s) kept for Monday")
    check("regulars are stripped before the supervisor sees the list",
          i_strip < body.index("_supervise_coupon_decision("))
    check("…and before the checkout's coupon list is built",
          i_strip < body.index("coupon_arg = priority_codes_for_checkout + regular_coupon_codes"))
    i_res = body.index("_sun_r = _sunday_mode()")
    check("the reseller rule runs before the placement claim is taken",
          i_res < body.index("plock.reserve(amazon_id, PROFILE_NAME)"))
    check("…and before the coupon-profile checkout block",
          i_res < body.index("# ── Coupon strategy"))
    # The Sunday block itself must not return: the claim is held there and is released by the
    # pre-payment reconcile further down. The '20' branch between them returns, but it releases
    # the claim first — and Sunday mode switches it off by clearing _cf_action_20.
    seg = body[body.index("# SUNDAY MODE: no priority code applied"):body.index('status_note = f"Placement failed: {result_status}"')]
    check("on a checkout coupon failure (claim held) the Sunday block never returns",
          re.search(r"^\s*return\b", seg, re.M) is None)
    check("…and it clears the '20' flag rather than let that branch label the order",
          "_cf_action_20 = False" in seg)
    check("…and clears a reseller hand-over the Sunday rule refuses", "_cf_route = None" in seg)
    check("the failure row is logged SUNDAY_HOLD", '"SUNDAY_HOLD" if _sunday_status else' in body)
    i_gate = body.index("_g = _sunday_gate(estimated_total_cost, margin if earnings else None, _sun_r, estimated_shipping)")
    i_hold = body.index("return _sunday_hold(amazon_id, _g, coupon_codes, _sun_r,")
    i_exempt = body.index("if _g and _all_coupon_blocked(resolved_items):")
    check("the reseller checks 'coupon-blocked' between the rule and the hold", i_gate < i_exempt < i_hold)
    check("both coupon-profile hand-overs exempt coupon-blocked items",
          body.count("None if _all_coupon_blocked(resolved_items) else _sunday_handover_gate(") == 2)
    check("the reseller gate uses the cost the placer prints, shipping included",
          "estimated_total_cost" in body[i_gate - 10:i_gate + 120] and body.index("estimated_total_cost = strategy") < i_gate)
    check("a Manual-C/-R order skips Sunday mode on both profiles",
          "_sunday_mode() if (IS_COUPON_PROFILE and not manual_override)" in body
          and "_sunday_mode() if (IS_RESELLER_PROFILE and not manual_override)" in body)

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
