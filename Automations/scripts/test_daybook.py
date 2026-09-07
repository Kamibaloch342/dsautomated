#!/usr/bin/env python3
"""
test_daybook.py — pure-function tests for dashboard/daybook.py (no database, no network).

The order lifecycle in the DB is spread over four date formats and a dozen status
columns; these tests pin down how each becomes one of the day-book flags.

Run:  python scripts/test_daybook.py
"""
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(AUTO, "dashboard"))
import daybook as D  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


def main():
    # ── dates: every format seen in closing_orders / order_placer_orders ──
    check("date: ISO date", D._d10("2026-09-07") == "2026-09-07")
    check("date: ISO datetime with T", D._d10("2026-09-07T18:11:20") == "2026-09-07")
    check("date: 'YYYY-MM-DD HH:MM' (closing purchase_date)", D._d10("2026-04-20 00:22") == "2026-04-20")
    check("date: 'Sun, Sep 6, 2026' (Amazon page)", D._d10("Sun, Sep 6, 2026") == "2026-09-06")
    check("date: 'Tue, Apr 21, 2026' (tracking_upload_date)", D._d10("Tue, Apr 21, 2026") == "2026-04-21")
    check("date: 'Friday, 4/24/2026 12:26 pm' (FedEx delivery_date)", D._d10("Friday, 4/24/2026 12:26 pm") == "2026-04-24")
    check("date: 'M/D/YYYY' (zoro_order_date)", D._d10("4/24/2026") == "2026-04-24")
    check("date: empty / None → ''", D._d10("") == "" and D._d10(None) == "")
    check("hours: purchase→delivered", D._hours("2026-04-20 00:22", "Friday, 4/24/2026 12:26 pm") == 108.1)

    # ── period defaults ──
    frm, to = D._period({}, 14)
    check("period: default = last 14 days ending today", to == date.today().isoformat() and frm == (date.today() - timedelta(days=13)).isoformat())
    frm, to = D._period({"from": ["2026-08-01"], "to": ["2026-08-31"]})
    check("period: explicit from/to honoured", (frm, to) == ("2026-08-01", "2026-08-31"))

    # ── lifecycle flags ──
    today = date.today().isoformat()
    yday = (date.today() - timedelta(days=1)).isoformat()
    base = {"amazon_order_id": "111-1", "order_label": "Completed", "order_status": "Shipped", "wb_order_id": "WB123",
            "shipped_email_time": "2026-09-01 10:00:00", "delivery_status": "Delivered", "tracking_ids": "7777",
            "deliver_by_date": "2026-09-05", "ship_by_date": "2026-09-02", "profit_dollars": 5.0}
    s = D._states(dict(base), {})
    check("state: completed+delivered order → placed/shipped/delivered/tracking, nothing pending or late",
          s["placed"] and s["shipped"] and s["delivered"] and s["tracking"] and not s["pending"] and not s["late_ship"]
          and not s["late_delivery"] and not s["cancelled"] and not s["refunded"], str(s))
    pend = {"amazon_order_id": "111-2", "order_label": "Pending", "order_status": None, "wb_order_id": "WB9",
            "deliver_by_date": today, "ship_by_date": today}
    s = D._states(pend, {})
    check("state: placer-only row (label Pending) → placed + pending, not late while ship-by is today",
          s["placed"] and s["pending"] and not s["late_ship"] and not s["late_delivery"], str(s))
    late = dict(pend, amazon_order_id="111-3", deliver_by_date=yday, ship_by_date=yday)
    s = D._states(late, {})
    check("state: unshipped past ship-by/deliver-by → late_ship + late_delivery", s["late_ship"] and s["late_delivery"])
    canc = {"amazon_order_id": "111-4", "order_label": "Cancelled", "order_status": "Cancelled", "wb_order_id": "WB5",
            "deliver_by_date": yday, "ship_by_date": yday}
    s = D._states(canc, {})
    check("state: cancelled order is never 'late' and never 'pending'", s["cancelled"] and not s["late_ship"] and not s["late_delivery"] and not s["pending"])
    msgs = {"111-1": {"tracking_sent": {"at": "2026-09-01 11:00:00", "n": 1}, "buyer_inquiry": {"at": "2026-09-02 09:00:00", "n": 2},
                      "delivery_checkin": {"at": "2026-09-06 08:00:00", "n": 1}}}
    s = D._states(dict(base), msgs)
    check("state: Gmail archive → tracking msg sent (+time), check-in sent, buyer wrote ×2",
          s["tracking_msg_sent"] and s["tracking_msg_at"] == "2026-09-01 11:00:00" and s["delivery_checkin"]
          and s["buyer_wrote"] and s["buyer_msgs"] == 2)
    rets = {"111-1": {"refund_initiated": {"at": "2026-09-03", "amount": 12.5, "reason": ""},
                      "return_auth": {"at": "2026-09-02", "amount": 0, "reason": "Damaged"}}}
    s = D._states(dict(base), {}, rets)
    check("state: archive_returns → refunded $12.50 on 2026-09-03, returned on 2026-09-02, reason Damaged",
          s["refunded"] and s["refund_amount"] == 12.5 and s["refund_at"] == "2026-09-03" and s["returned"]
          and s["return_at"] == "2026-09-02" and s["return_reason"] == "Damaged", str(s))
    lossy = dict(base, amazon_order_id="111-5", profit_dollars=-3.2)
    check("state: negative profit on a completed order counts as a loss", D._states(lossy, {})["loss"])
    crm = dict(base, amazon_order_id="111-6", flag_color="#b42318")
    check("state: a flag/tag/case marks has_case", D._states(crm, {})["has_case"] and not D._states(dict(base), {})["has_case"])

    # ── money + counts ──
    rows = [dict(base, earnings=100, zoro_order_total=80, profit_dollars=20, coupon_savings=5, quantity=2),
            dict(base, amazon_order_id="111-7", earnings=50, zoro_order_total=45, profit_dollars=5, coupon_savings=0, quantity=1,
                 refund_done="Yes", refund_amount=10)]
    m = D._money(rows)
    check("money: sums, margin on cost, refunds, units", m["earnings"] == 150 and m["cost"] == 125 and m["profit"] == 25
          and m["margin_pct"] == 20.0 and m["coupon_saved"] == 5 and m["refunds"] == 10 and m["units"] == 3, str(m))
    c = D._counts([D._states(r, {}) for r in rows])
    check("counts: 2 orders, 2 placed, 1 refunded", c["orders"] == 2 and c["placed"] == 2 and c["refunded"] == 1, str(c))
    agg = D._agg([(r, D._states(r, {})) for r in rows])
    check("agg: rates are percentages of orders", agg["refund_rate"] == 50.0 and agg["cancel_rate"] == 0.0)
    d = D._delta({"profit": 120.0, "orders": 10, "name": "x"}, {"profit": 100.0, "orders": 0})
    check("delta: diff/pct per numeric metric, pct None when the previous value is 0",
          d["profit"] == {"now": 120.0, "prev": 100.0, "diff": 20.0, "pct": 20.0} and d["orders"]["pct"] is None and "name" not in d)

    # ── CSV shape ──
    check("csv: date first, then the order id, then the derived flags", [c for c, _ in D.CSV_COLS][:3] == ["date", "amazon_order_id", "account"]
          and ("tracking_msg_sent", "s.tracking_msg_sent") in D.CSV_COLS)

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL)); sys.exit(1)


if __name__ == "__main__":
    main()
