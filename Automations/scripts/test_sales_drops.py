#!/usr/bin/env python3
"""
test_sales_drops.py — Attention → Sales drops flags good sellers that fell, and only them.

The owner's rule (11 Oct 2026): an ASIN with at least 10 orders in the earlier 48 h whose orders in
the latest 48 h fell by MORE than 25 %. On a throw-away DB; pins down that the windows end on the
last day the data covers in full (a report downloaded mid-day must not look like a drop), that
cancelled orders are not demand, and that a two-ASIN order counts for both.

Run:  python scripts/test_sales_drops.py
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(AUTO, "dashboard"))
import sales_drops as D

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   ({detail})" if detail and not ok else ""))


def main():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE amazon_report_orders (account TEXT, amazon_order_id TEXT, purchase_day TEXT,
        order_status TEXT, asins TEXT, units REAL, sales REAL, title TEXT, skus TEXT);
        CREATE TABLE amazon_report_files (account TEXT, mtime REAL);""")
    # the newest report was downloaded at 15:39 PT on 10 Oct → complete through 9 Oct
    mt = datetime(2026, 10, 10, 15, 39, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()
    c.execute("INSERT INTO amazon_report_files VALUES ('HOANK', ?)", (mt,))
    n = [0]

    def orders(asin, day, k, status="Shipped", price=10.0, asins=None):
        for _ in range(k):
            n[0] += 1
            c.execute("INSERT INTO amazon_report_orders VALUES ('HOANK',?,?,?,?,1,?,?,?)",
                      (f"O{n[0]}", day, status, asins or asin, price, "Item " + asin, "SKU-" + asin))
    # earlier window = 6–7 Oct, latest = 8–9 Oct
    orders("B_DROP", "2026-10-06", 5); orders("B_DROP", "2026-10-07", 5)       # 10 → 6 = −40 %
    orders("B_DROP", "2026-10-08", 3, price=12.0); orders("B_DROP", "2026-10-09", 3, price=12.0)
    orders("B_DROP", "2026-10-10", 1)                                         # half-reported day: ignored
    orders("B_EDGE", "2026-10-06", 10); orders("B_EDGE", "2026-10-08", 15)    # 20 → 15 = −25 % exactly
    orders("B_EDGE", "2026-10-07", 10)
    orders("B_SMALL", "2026-10-06", 9); orders("B_SMALL", "2026-10-08", 1)    # under 10 earlier: ignored
    orders("B_CXL", "2026-10-06", 10); orders("B_CXL", "2026-10-07", 5, status="Cancelled")
    orders("B_CXL", "2026-10-08", 9)                                          # 10 real → 9 = −10 %
    orders("B_PAIR", "2026-10-06", 10, asins="B_PAIR, B_MATE")               # both ASINs get 10
    orders("B_PAIR", "2026-10-08", 10, asins="B_PAIR, B_MATE")               # …and keep selling
    c.commit(); c.close()
    D.DB = path
    d = D.sales_drops({"account": ["HOANK"]})
    rows = {r["asin"]: r for r in d["rows"]}

    print("\n── windows ──")
    w = d["windows"]["HOANK"]
    check("latest window is 8–9 Oct: 10 Oct was only half reported", w["latest"] == ["2026-10-08", "2026-10-09"], str(w))
    check("earlier window is 6–7 Oct", w["earlier"] == ["2026-10-06", "2026-10-07"])

    print("\n── the rule ──")
    r = rows["B_DROP"]
    check("10 → 6 is −40 % and flagged", r["alert"] and r["change_pct"] == -40.0 and r["lost"] == 4, str(r))
    check("the half day's order did not count", r["latest"] == 6)
    check("our price rising is pointed out", r["price_up"] is True)
    check("exactly −25 % is not 'more than 25 %'", rows["B_EDGE"]["alert"] is False, str(rows["B_EDGE"]["change_pct"]))
    check("an ASIN under 10 orders earlier is not watched", "B_SMALL" not in rows)
    check("cancelled orders are not demand: 10 → 9, no alert",
          rows["B_CXL"]["earlier"] == 10 and rows["B_CXL"]["alert"] is False, str(rows["B_CXL"]))
    check("a two-ASIN order counts for both", rows.get("B_PAIR", {}).get("earlier") == 10 and rows.get("B_MATE", {}).get("earlier") == 10)
    flags = [x["alert"] for x in d["rows"]]
    check("alerts come first", flags == sorted(flags, reverse=True) and d["rows"][0]["asin"] == "B_DROP",
          str([(x["asin"], x["alert"]) for x in d["rows"]]))
    check("the 14-day trend ends on the cut-off day", len(r["trend"]) == 14 and r["trend"][-2:] == [3, 3], str(r["trend"]))

    print("\n── settings ──")
    d2 = D.sales_drops({"account": ["HOANK"], "drop_pct": ["20"]})
    check("at 20 % the −25 % edge is flagged", {x["asin"]: x for x in d2["rows"]}["B_EDGE"]["alert"] is True)
    d3 = D.sales_drops({"account": ["HOANK"], "min_orders": ["5"]})
    check("min orders 5 watches the small one", any(x["asin"] == "B_SMALL" for x in d3["rows"]))
    os.remove(path)

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL)); sys.exit(1)


if __name__ == "__main__":
    main()
