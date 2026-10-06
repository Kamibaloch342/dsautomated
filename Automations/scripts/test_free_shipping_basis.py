#!/usr/bin/env python3
"""
test_free_shipping_basis.py — Zoro's $50 free shipping is measured on the LIST price.

WB1228729288 (6 Oct 2026, DM_RESELLER): G3057148 x25 at $2.19 is $54.75 list. The reseller's
Zoro Select 10% made it $49.27, the placer saw "under $50", added 2x G500699916 filler to dodge
a $5.99 fee — and Zoro would have shipped it free anyway.

The order log proves the rule. Of the orders where the list total was >= $50 but the total
after the discount was under $50, 1,251 shipped free and none paid — 110 of them Zoro Select on
a reseller, exactly this case. Where the list itself was under $50, 27,420 paid and one did not.
So neither the Select discount nor a coupon ever moves the threshold; only the list price does.

The same wrong number fed three other decisions: the strategy's shipping estimate (which also
re-tested the threshold AFTER a coupon), the cost estimate the margin gate reads, and the
Deciding-ASIN routing margin. A phantom $5.99 there can make a profitable order look like a
low-margin one. All five now use the list total.

Run:  python scripts/test_free_shipping_basis.py   (no Chrome, no network, no orders)
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


def main():
    from engine.orders import _deciding_verdict, _evaluate_order_strategy
    import checkout.true_api as T

    LIST, AFTER = 54.75, 49.275          # G3057148 x25: list, and after the 10% Select discount

    print("\n── the strategy estimate ──")
    items = [{"final_qty": 25}]
    st = _evaluate_order_strategy(items, [], unit_price=AFTER / 25, list_total=LIST)
    check("listed at $54.75, $49.27 after Select → no shipping fee estimated",
          st["estimated_shipping"] == 0, str(st))
    check("…so the estimated cost is the discounted cost, no $5.99 on top",
          abs(st["estimated_cost"] - AFTER) < 0.01, str(st["estimated_cost"]))
    st = _evaluate_order_strategy(items, [], unit_price=40.0 / 25, list_total=40.0)
    check("a $40 list cart still pays the fee", st["estimated_shipping"] == 5.99, str(st))
    st = _evaluate_order_strategy([{"final_qty": 1}], [{"code": "X20", "pct": 20}],
                                  unit_price=55.0, list_total=55.0)
    check("a 20% coupon taking $55 to $44 does not add the fee",
          abs(st["estimated_cost"] - 44.0) < 0.01, str(st["estimated_cost"]))
    st = _evaluate_order_strategy(items, [], unit_price=AFTER / 25)
    check("callers that pass no list total keep the old behaviour",
          st["estimated_shipping"] == 5.99, str(st))

    print("\n── the Deciding-ASIN routing margin ──")
    r = _deciding_verdict(raw_cart_cost=LIST, real_cost=AFTER, earnings=55.24, promo_bad=False,
                          all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    check("reseller margin is computed without a phantom fee",
          abs(r["est_margin"] - (55.24 - AFTER) / AFTER) < 1e-6, str(r))
    r = _deciding_verdict(raw_cart_cost=40.0, real_cost=36.0, earnings=55.0, promo_bad=False,
                          all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    check("a $40 list cart still carries the fee in its margin",
          abs(r["est_margin"] - (55.0 - 41.99) / 41.99) < 1e-6, str(r))

    print("\n── the filler at checkout ──")
    added = []
    T._add_item_api = lambda drv, sku, qty: (added.append((sku, qty)) or True, 0.78)
    T.zoro.check_inventory = lambda drv, sku, qty: (True, "ok")

    T._get_cart = lambda drv: {"priceTotal": 49.27, "discountTotal": 5.48}   # WB1228729288's cart
    ok, q, _ = T._maybe_add_shipping_filler(None, "cart")
    check("WB1228729288's cart gets no filler", ok is False and not added, str(added))

    added.clear()
    T._get_cart = lambda drv: {"priceTotal": 48.80, "discountTotal": 0}      # genuinely under
    ok, q, _ = T._maybe_add_shipping_filler(None, "cart")
    check("a $48.80 list cart still gets filler", ok is True and added, str(added))
    check("…and only enough to reach $50 (2 units at $0.78)", q == 2, str(q))

    added.clear()
    T._get_cart = lambda drv: {"priceTotal": 44.10, "discountTotal": 4.90}   # $49.00 list
    ok, q, _ = T._maybe_add_shipping_filler(None, "cart")
    # Before, this cart read as $44.10, under the $47 trigger, so no filler was tried and
    # Zoro charged $5.99 on the $49.00 list. Measured on the list, $1.56 of filler saves it.
    check("a Select cart listed at $49.00 ($44.10 after) now gets filler instead of a $5.99 fee",
          ok is True and q == 2, str(q))

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
