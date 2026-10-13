#!/usr/bin/env python3
"""
test_manual_big_order.py — a Manual-C/-R order is placed, never labelled '20'.

13 Oct 2026: 114-0959090-1125021 (BayTec 55 gal drum ×6, $881.94 on Zoro) was tagged Manual-C twice;
both times DM_COUPON took it, tried only the priority coupons (a $800+ cart loses the regular ones),
none applied, and the checkout wrote '20' over the tag. Owner: "once I write Manual-C or R, place it —
no need to write 20".

Run:  python scripts/test_manual_big_order.py   (no Chrome, no network, no messages sent)
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


import engine.orders as O  # noqa: E402

BIG, CART = 800.0, 881.94
REG = ["EMA10", "EMB10"]

print("\nregular coupons on a big cart")
check("untagged $881.94 cart: regular coupons removed (team phones Zoro for 20 %)",
      O._big_order_regular_codes(REG, CART, BIG, False) == [])
check("Manual-C $881.94 cart: regular coupons kept", O._big_order_regular_codes(REG, CART, BIG, True) == REG)
check("a $120 cart keeps them either way", O._big_order_regular_codes(REG, 120.0, BIG, False) == REG)

print("\nthe '20' label")
check("untagged big cart no coupon covered → '20'", O._big_order_marks_20(CART, BIG, False) is True)
check("Manual-C/-R big cart → never '20'", O._big_order_marks_20(CART, BIG, True) is False)
check("small cart → never '20'", O._big_order_marks_20(49.0, BIG, False) is False)

# what the checkout reported for 114-0959090-1125021: priority codes tried, none applied
lf = {"total": 3, "item": 0, "min_order": 3, "priority": 3, "regular_available": 0,
      "priority_item": 0, "priority_min_order": 3, "priority_dead": 0, "regular_dead": 0}
r = O._coupon_fail_decision(lf, CART, 1076.04, False, 5.99, 50.0, 0.05)
check("decision for an untagged big order is still call_zoro_20", r["action"] == "call_zoro_20", str(r))
r = O._coupon_fail_decision(lf, CART, 1076.04, False, 5.99, 50.0, 0.05, big_order_20=False)
check("decision for a Manual-C big order is never call_zoro_20", r["action"] != "call_zoro_20", str(r))

print("\nalert when a Manual-C order still has no coupon")
sent = []
import utils.whatsapp as W  # noqa: E402
import utils.telegram as T  # noqa: E402
_w, _t = W.send_whatsapp_message, T.send_telegram_message
W.send_whatsapp_message = lambda text, *a, **k: sent.append(("wa", text)) or True
T.send_telegram_message = lambda text, *a, **k: sent.append(("tg", text)) or True
try:
    O._MANUAL_NO_COUPON_ALERTED.discard("114-0959090-1125021")
    O._notify_manual_no_coupon("114-0959090-1125021", "B07B6B69G4", "G9084824", CART, "no coupon fits this cart yet",
                               "Francis C", "Louisa", "VA")
    O._notify_manual_no_coupon("114-0959090-1125021", "B07B6B69G4", "G9084824", CART, "again")
finally:
    W.send_whatsapp_message, T.send_telegram_message = _w, _t
check("one WhatsApp + one Telegram message, not one per retry", [k for k, _ in sent] == ["wa", "tg"], str(sent))
check("…telling the owner Manual-R places it without a coupon",
      all("Manual-R" in t and "114-0959090-1125021" in t for _, t in sent))

print("\nwiring in process_order")
src = open(os.path.join(AUTO, "engine", "orders.py"), encoding="utf-8").read()
body = src[src.index("def process_order("):src.index("#  COUPON SHEET LOADER")]
check("the coupon list goes through _big_order_regular_codes(…, manual_override)",
      "_big_order_regular_codes(regular_coupon_codes, raw_cart_cost, big_threshold, manual_override)" in body
      and "\n            regular_coupon_codes = []\n\n        coupon_arg" not in body)
check("checkout COUPON FAIL marks '20' only through _big_order_marks_20",
      "if _big_order_marks_20(raw_cart_cost, big_threshold, manual_override):" in body
      and "big_order_20=not manual_override" in body
      and 'if _cf["action"] == "call_zoro_20" and not manual_override:' in body)
# every place that writes '20' on Amazon must sit inside an `if` that excludes a Manual tag
lines = body.split("\n")


def enclosing_ifs(n):
    """The if/elif headers whose block contains line n (walk up, keep each shallower header)."""
    ind = len(lines[n]) - len(lines[n].lstrip())
    out = []
    for j in range(n - 1, -1, -1):
        t = lines[j]
        if not t.strip() or t.lstrip().startswith("#"):
            continue
        k = len(t) - len(t.lstrip())
        if k < ind:
            ind = k
            if t.lstrip().startswith(("if ", "elif ")):
                out.append(t.strip())
            if k <= 4:
                break
    return out


writes = [n for n, t in enumerate(lines) if 'update_amazon_seller_id(driver, amazon_id, "20"' in t]
guards = [any("not manual_override" in h or h.startswith("if _cf_action_20") for h in enclosing_ifs(n)) for n in writes]
check(f"all {len(writes)} '20' writes sit behind a Manual-tag check (reseller gate, supervisor, empty pool, checkout)",
      len(writes) == 4 and all(guards), str([(n, enclosing_ifs(n)[:3]) for n, g in zip(writes, guards) if not g]))
check("the contract comment lists the big-order '20' as bypassed", "3. the big-order '20' label" in src)
blk = body[body.index('if result_status == "COUPON FAIL" and IS_COUPON_PROFILE and not skip_coupons:'):
           body.index("# SUNDAY MODE: no priority code applied")]
check("a Manual-C COUPON FAIL is never handed to the reseller (it keeps its tag)",
      'elif _cf["action"] == "route_reseller" and not manual_override:' in blk)
tail = blk[blk.index('elif _cf["action"] == "route_reseller" and not manual_override:'):]
check("…it is parked for the next coupon change and alerts once, unless the failure was an API blip",
      "_COUPON_WAITS[amazon_id]" in tail and "_notify_manual_no_coupon(" in tail
      and '("transient", "priority_transient", "regular_transient")' in tail)
check("…and the alert never repeats the reseller hand-off wording", "reseller places it" not in tail
      and "a coupon can never apply" in tail)
foc = blk[blk.index("elif _foc_codes:"):blk.index("else:", blk.index("elif _foc_codes:"))]
check("a Manual-C order in focus-coupon mode alerts too", "if manual_override:" in foc and "_notify_manual_no_coupon(" in foc)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
