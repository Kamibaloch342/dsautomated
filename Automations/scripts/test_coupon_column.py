#!/usr/bin/env python3
"""
test_coupon_column.py — a non-Select ASIN whose unit price clears the threshold belongs in _Coupon.

The labelling flow agreed 7 Sep 2026 says it twice: "ASIN moves to _Coupon if unit is $50 or
more, otherwise _Deciding", and "$50-plus rows never churn". The supervisor has always done
this (engine/orders.py: col_by_price). The checker stopped: the 14 Sep coupon-lifecycle commit
made its no-priority-coupon branch answer Deciding whatever the price, and since the checker
runs on every sweep and re-check it kept dragging those ASINs back out of _Coupon.

Cost: the reseller reserved, priced and labelled 'MR' on every single order of an ASIN that was
always going to the coupon profile — 443 such hand-offs in the week to 29 Sep, and 31 ASINs
were sitting in _Deciding at a unit price above the threshold, one of them at $316.20.

Below the threshold Deciding is still right: whether the order needs a coupon depends on the
quantity (a $16.99 item at qty 4 is a $67.96 cart) and on the reseller's margin, and only the
order knows those.

Run:  python scripts/test_coupon_column.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


def main():
    import engine.priority_check as PC

    print("\n── the threshold comes from the same place the supervisor reads ──")
    mn = PC.coupon_column_min()
    check("it is the configured routing threshold", mn == 50.0, str(mn))
    src_o = open(os.path.join(AUTO, "engine", "orders.py"), encoding="utf-8").read()
    check("the supervisor uses the same rule for its column",
          'col_by_price = "Coupon" if up >= SUPERVISE_MIN_CART else "Deciding"' in src_o)

    print("\n── the checker's verdict, no priority coupon and not Zoro Select ──")
    verdicts = []

    def verdict(unit, is_select=False, worked=None, min_order=0):
        """Replay check_one's verdict branch — the same conditions, without Chrome or Zoro."""
        row = {}
        if worked:
            row.update(Check_Status="COUPON_OK", Check_Suggest="Coupon")
        elif is_select:
            row.update(Check_Status="SELECT", Check_Suggest="Reseller")
        else:
            _min = PC.coupon_column_min()
            big = unit >= _min
            row.update(Check_Status="NO_PRIORITY", Check_Suggest="Coupon" if big else "Deciding",
                       Check_Note=(f"no priority coupon; unit ${unit:.2f} ≥ ${_min:.0f} → regular coupon every time"
                                   if big else
                                   f"no priority coupon; unit ${unit:.2f} < ${_min:.0f} → per-order (qty / regular coupon / margin)"))
        verdicts.append((unit, row))
        return row

    check("a $316.20 item goes to Coupon, not Deciding", verdict(316.20)["Check_Suggest"] == "Coupon")
    check("an $89.00 item goes to Coupon", verdict(89.00)["Check_Suggest"] == "Coupon")
    check("exactly $50.00 goes to Coupon (the threshold is inclusive)", verdict(50.00)["Check_Suggest"] == "Coupon")
    check("$49.99 stays Deciding", verdict(49.99)["Check_Suggest"] == "Deciding")
    check("a $16.99 item stays Deciding — quantity decides that one", verdict(16.99)["Check_Suggest"] == "Deciding")
    check("the note says why, with the number in it",
          "≥ $50" in verdict(89.00)["Check_Note"] and "< $50" in verdict(16.99)["Check_Note"])

    print("\n── the other branches are untouched ──")
    check("Zoro Select with no priority coupon is still Reseller, whatever the price",
          verdict(300.0, is_select=True)["Check_Suggest"] == "Reseller")
    check("a working priority coupon is still Coupon",
          verdict(9.99, worked="FALL93A26")["Check_Suggest"] == "Coupon")

    print("\n── the real source, and what may actually move ──")
    src = open(os.path.join(AUTO, "engine", "priority_check.py"), encoding="utf-8").read()
    branch = src.split("row.update(Check_Status=\"NO_PRIORITY\"")[1][:400]
    check("check_one chooses the column by unit price", 'Check_Suggest="Coupon" if _big else "Deciding"' in branch)
    check("the old 'whatever the unit price' rule is gone", "Deciding) whatever the unit price" not in src)
    check("a bot-placed Deciding ASIN may be re-sorted",
          PC.may_move("NO_PRIORITY", "DM_Deciding", bot_owned=True, mode="recheck") is True)
    check("a human-placed one is left alone",
          PC.may_move("NO_PRIORITY", "DM_Deciding", bot_owned=False, mode="recheck") is False)

    print("\n── the per-order rule still catches quantity ──")
    from engine.orders import _deciding_verdict
    r = _deciding_verdict(raw_cart_cost=67.96, real_cost=67.96, earnings=95.0, promo_bad=False,
                          all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    check("a $16.99 item at qty 4 is still handed to the coupon profile", r["route"] is True, str(r))
    r = _deciding_verdict(raw_cart_cost=16.99, real_cost=16.99, earnings=60.0, promo_bad=False,
                          all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    check("the same item at qty 1 with a healthy margin stays with the reseller", r["route"] is False, str(r))

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
