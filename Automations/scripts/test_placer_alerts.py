#!/usr/bin/env python3
"""
test_placer_alerts.py — the placer sends exactly two alerts, and both can actually fire.

Chosen by the owner on 21 Sep 2026 after a "+500% clean unit earnings" and a "+2399% Amazon
price" alert, both artefacts of per-unit arithmetic:
  1. Zoro's /ea price for the code went UP against the last one we recorded (any rise).
  2. Zoro's "Delivery by" date is 2+ days after the date Amazon promised the buyer.
Nothing else reaches WhatsApp. The delivery alert had never fired: the PDP scrape did not
recognise "Delivery by Thursday, September 24" (weekday first), and the placer fed the rule a
field the checkout never set.

Run:  python scripts/test_placer_alerts.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


def src(rel):
    return open(os.path.join(AUTO, rel), encoding="utf-8").read()


def main():
    import utils.insights_engine as IE
    import utils.unified_db as U
    base = {"amazon_id": "T-1", "asin": "B0TEST", "zoro_code": "G8545652", "sku": "hath-G8545652",
            "seller_sku": "hath-G8545652", "title": "8 in x 4 in x 4 in Shipping Box", "account": "Hathoro",
            "quantity": 1, "amazon_qty": 1, "zoro_qty": 25}
    IE.get_historical_asin_orders = lambda asin, limit=10: []          # no history → other rules stay quiet
    IE.last_amazon_unit_price = lambda asin, exclude_amazon_id="": None
    U.last_list_price = lambda sku, exclude_amazon_id="", db_path=None, conn=None: None

    print("\n── alert 2: Zoro delivers 2+ days after Amazon's promise ──")
    def late(amz, zoro):
        out = IE.analyze_order_insights(dict(base, deliver_by_date=amz, zoro_deliver_by=zoro))
        return [i for i in out if i.get("code") == "DELIVERY_MISMATCH_LATE"]
    a = late("Tue, Sep 22, 2026", "2026-09-24")
    check("Amazon Tue 22, Zoro Thu 24 (2 days) → alert", len(a) == 1, str(a)[:120])
    check("the message shows both dates and the gap", a and "Sep 22" in a[0]["summary"] and "Sep 24" in a[0]["summary"] and "2 days" in a[0]["summary"])
    check("1 day later → no alert", late("Wed, Sep 23, 2026", "2026-09-24") == [])
    check("same day / earlier → no alert", late("Thu, Sep 24, 2026", "2026-09-24") == [] and late("Mon, Sep 28, 2026", "2026-09-24") == [])
    check("5 days later → alert", len(late("Sat, Sep 19, 2026", "2026-09-24")) == 1)
    check("no Zoro date (not scraped) → silent, never a false alarm", late("Tue, Sep 22, 2026", "") == [] and late("Tue, Sep 22, 2026", None) == [])
    check("the real formats parse: Amazon 'Mon, Sep 28, 2026', Zoro '2026-09-24'",
          IE._parse_date("Mon, Sep 28, 2026") is not None and IE._parse_date("2026-09-24") is not None)
    check("Tuesday and Thursday dates parse too (they used to be split on the letter 'T' and vanish)",
          str(IE._parse_date("Tue, Sep 22, 2026")) == "2026-09-22" and str(IE._parse_date("Thu, Sep 24, 2026")) == "2026-09-24")
    check("ISO with a time, and a trailing time zone, both parse",
          str(IE._parse_date("2026-09-24T10:00:00")) == "2026-09-24" and str(IE._parse_date("Sat, May 23, 2026 PDT")) == "2026-05-23")

    print("\n── the PDP scrape understands Zoro's current wording ──")
    import platforms.zoro as Z
    page = '<div class="foo">Delivery by Thursday, September 24</div><span>Order by 4pm</span>'
    m = Z._DELIV_RE1.search(page) or Z._DELIV_RE2.search(page) or Z._DELIV_RE3.search(page)
    check("'Delivery by Thursday, September 24' → 'September 24'", bool(m) and m.group(1) == "September 24", str(m and m.group(1)))
    check("...which becomes an ISO date in the right year", (Z._parse_deliv(m.group(1)) if m else "")[5:] == "09-24")
    m2 = Z._DELIV_RE3.search("Delivery by tomorrow, Friday, September 25")
    check("'Delivery by tomorrow, Friday, September 25' still parses", bool(m2) and m2.group(1) == "September 25")
    check("the old wording still parses", bool(Z._DELIV_RE2.search("Delivery on or before Sep 25")))
    check("get_zoro_delivery_date tries the new pattern", "_DELIV_RE3.search(html)" in src("platforms/zoro.py"))

    print("\n── only these two alerts leave the placer ──")
    check("the allow-list is exactly the two", tuple(IE.PLACER_ALERT_CODES) == ("ZORO_LIST_PRICE_CHANGED", "DELIVERY_MISMATCH_LATE"))
    real = IE.analyze_order_insights
    IE.analyze_order_insights = lambda o: [{"code": "CLEAN_EARNINGS_SHIFT"}, {"code": "ITEM_PRICE_CHANGED"},
                                          {"code": "DELIVERY_MISMATCH_LATE"}, {"code": "ZORO_LIST_PRICE_CHANGED"},
                                          {"code": "LOW_ZORO_STOCK"}, {"code": "REDUCE_HANDLING_TIME"}]
    try:
        got = [i["code"] for i in IE.placer_alerts({})]
    finally:
        IE.analyze_order_insights = real
    check("placer_alerts() drops everything else", got == ["DELIVERY_MISMATCH_LATE", "ZORO_LIST_PRICE_CHANGED"], str(got))
    o = src("engine/orders.py")
    check("both dispatch sites call placer_alerts, none call analyze_order_insights",
          o.count("triggered_insights = placer_alerts(insight_data)") == 2 and "analyze_order_insights(insight_data)" not in o)
    check("the rule is fed the date the PDP scrape stored (addr), not a field checkout never set",
          o.count('insight_data["zoro_deliver_by"] = ((addr.get("_zoro_deliver_by")') == 2)
    check("Zoro's delivery date is recorded in order_placer_orders at placement (both write sites)",
          o.count('zoro_deliver_by=(addr.get("_zoro_deliver_by")') == 2
          and '"zoro_deliver_by": _placer_only.get("zoro_deliver_by")' in o)
    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL)); sys.exit(1)


if __name__ == "__main__":
    main()
