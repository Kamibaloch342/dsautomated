#!/usr/bin/env python3
"""
test_auto_refunds.py — the Auto Refunds tab counts every refund once, on the right day.

Built on a throw-away SQLite with the three tables the tab reads, so it needs no Gmail, no network
and never touches automations_unified.db. What it pins down:
  * days are Seller Central's (US/Pacific): the archive stamps e-mails in Karachi time, and a refund
    at 04:43 on 1 Sep in Karachi is still 31 Aug in Seattle;
  * an order refunded twice shows both refunds but its net is counted once;
  * "net after refund" is computed from the e-mails, not read from a closing row that may be a day
    stale (112-8896393-9790656 read +$42.90 on a $293.98 refund);
  * a product title that the parser dropped into refund_reason is not shown as a reason;
  * the store comes from the order, or from the inbox when the order is unknown.

Run:  python scripts/test_auto_refunds.py
"""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(AUTO, "dashboard"))
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   ({detail})" if detail and not ok else ""))


def build(path):
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE archive_returns (amazon_order_id TEXT, gmail_account TEXT, email_timestamp TEXT, email_type TEXT,
        refund_amount REAL, item_price_refund REAL, outbound_shipping_refund REAL, refund_reason TEXT,
        refund_resolution TEXT, customer_name TEXT, asin TEXT, sku TEXT, return_reason TEXT,
        customer_comment TEXT, fetched_at TEXT);
    CREATE TABLE closing_orders (amazon_order_id TEXT, account TEXT, title TEXT, asin TEXT, sku TEXT, quantity INT,
        item_price REAL, earnings REAL, purchase_date TEXT, wb_order_id TEXT, zoro_order_total REAL,
        zoro_order_status TEXT, profit_dollars REAL, net_profit_after_refund REAL, order_label TEXT,
        delivery_status TEXT, delivery_date TEXT, case_status TEXT, case_type TEXT, zoro_case_number TEXT,
        zoro_refund_amount REAL, flag_color TEXT, ship_city TEXT, ship_state TEXT);
    CREATE TABLE amazon_report_orders (amazon_order_id TEXT, account TEXT, title TEXT, asins TEXT, skus TEXT,
        units INT, sales REAL, purchase_day TEXT, order_status TEXT);
    """)
    R = lambda *v: c.execute("INSERT INTO archive_returns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", v)
    # A: HOANK, delivered, refunded twice (partial + rest), no return
    R("111-A", "qurtdis", "2026-09-10 21:00:00", "refund_initiated", 20.0, 20.0, 0, "Damaged", "Customer Services", "Ann", "B0A", "G1", "", "", "x")
    R("111-A", "qurtdis", "2026-09-12 21:00:00", "refund_initiated", 30.0, 30.0, 0, "", "Customer Services", "Ann", "B0A", "G1", "", "", "x")
    # B: Hathoro, returned, the parser put the product title in refund_reason
    R("222-B", "abecomllc26", "2026-09-11 22:00:00", "refund_initiated", 293.98, 293.98, 0,
      "Simple Green Concentrated All-Purpose Cleaner/Degreaser", "Seller Issued Refund", "Bob", "B0B", "G2", "", "", "x")
    R("222-B", "abecomllc26", "2026-09-05 10:00:00", "return_auth", None, None, None, "", "", "Bob", "B0B", "G2",
      "Item defective", "broken on arrival", "x")
    # C: 04:43 on 1 Sep in Karachi = 31 Aug in Seattle → outside a 1 Sep start
    R("333-C", "qurtdis", "2026-09-01 04:43:07", "refund_initiated", 10.0, 10.0, 0, "", "Returnless Refund", "Cy", "B0C", "G3", "", "", "x")
    # D: order we never placed and closing never saw → store from the inbox
    R("444-D", "abecomllc26", "2026-09-11 20:00:00", "refund_initiated", 12.0, 12.0, 0, "", "A-to-Z Guarantee Claim", "Dee", "B0D", "G4", "", "", "x")
    O = lambda *v: c.execute("INSERT INTO closing_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", v)
    O("111-A", "HOANK", "Widget", "B0A", "G1", 1, 60, 51, "2026-09-01", "WB1", 40, "Shipped", 11.0, 11.0, "Completed",
      "Delivered", "2026-09-05", "", "", "", None, "", "Austin", "TX")
    # B's closing row is stale: it has not absorbed the refund yet (+42.90)
    O("222-B", "Hathoro", "Simple Green Concentrated All-Purpose Cleaner/Degreaser", "B0B", "G2", 1, 300, 250, "2026-09-02",
      "WB2", 207, "Shipped", 42.9, 42.9, "Completed", "Delivered", "2026-09-04", "Open", "Return", "", None, "", "Reno", "NV")
    O("333-C", "HOANK", "Thing", "B0C", "G3", 1, 20, 17, "2026-08-28", "WB3", 12, "Shipped", 5.0, 5.0, "Refunded",
      "Delivered", "", "", "", "", None, "", "", "")
    c.execute("INSERT INTO amazon_report_orders VALUES ('444-D','','Gadget','B0D','G4',1,15,'2026-09-09','Cancelled')")
    c.commit(); c.close()


def main():
    import auto_refunds as A
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    build(path)
    A.DB = path
    d = A.auto_refunds({"from": ["2026-09-01"], "to": ["2026-09-30"]})
    rows = {(r["order_id"], r["refunded_at"]): r for r in d["rows"]}
    ids = [r["order_id"] for r in d["rows"]]

    print("\n── days are Seller Central's (US/Pacific) ──")
    check("04:43 on 1 Sep in Karachi is 31 Aug in Seattle → not in September", "333-C" not in ids)
    a1 = next(r for r in d["rows"] if r["order_id"] == "111-A" and r["amount"] == 20.0)
    check("21:00 Karachi on 10 Sep is the morning of 10 Sep Pacific", a1["day"] == "2026-09-10", a1["day"])
    check("every day of the range is listed, refunds or not", len(d["days"]) == 30, str(len(d["days"])))
    sep10 = next(x for x in d["days"] if x["day"] == "2026-09-10")
    check("…and a day carries its store split", sep10["hoank"] == 1 and sep10["hathoro"] == 0, str(sep10))

    print("\n── every refund once, every order once ──")
    check("both refunds on the twice-refunded order are listed", ids.count("111-A") == 2, str(ids))
    check("each knows it is one of two, $50 in all", a1["refunds_on_order"] == 2 and a1["refunded_on_order"] == 50.0)
    check("the order's net is counted once in the total",
          abs(d["totals"]["net_after_refund"] - ((11 - 50) + (42.9 - 293.98))) < 0.01, str(d["totals"]["net_after_refund"]))
    check("refunds 4, orders 3", d["totals"]["count"] == 4 and d["totals"]["orders"] == 3, str(d["totals"]))

    print("\n── the numbers come from the e-mails, not a stale closing row ──")
    b = next(r for r in d["rows"] if r["order_id"] == "222-B")
    check("net = profit − refund: 42.90 − 293.98 = −251.08", b["net_after_refund"] == -251.08, str(b["net_after_refund"]))
    check("a product title in refund_reason is not shown as the reason", b["reason"] == "", repr(b["reason"]))
    check("a real reason is kept", a1["reason"] == "Damaged", repr(a1["reason"]))
    check("the return request is found, with its reason", b["returned"] and b["return_reason"] == "Item defective")
    check("…and a refund with nothing coming back says so", a1["returned"] is False)

    print("\n── store ──")
    dd = next(r for r in d["rows"] if r["order_id"] == "444-D")
    check("from the order when closing knows it", b["store"] == "Hathoro" and a1["store"] == "HOANK")
    check("from the inbox when it does not (abecomllc26 → Hathoro)", dd["store"] == "Hathoro", dd["store"])
    check("a never-placed order still shows Amazon's title", dd["title"] == "Gadget" and dd["wb"] == "")

    print("\n── filters ──")
    f = lambda **k: [r["order_id"] for r in A.auto_refunds(dict({"from": ["2026-09-01"], "to": ["2026-09-30"]},
                                                                **{x: [y] for x, y in k.items()}))["rows"]]
    check("store=HOANK", set(f(store="HOANK")) == {"111-A"})
    check("store=Hathoro", set(f(store="Hathoro")) == {"222-B", "444-D"})
    check("who refunded = A-to-Z", f(resolution="A-to-Z Guarantee Claim") == ["444-D"])
    check("returned = yes", f(returned="yes") == ["222-B"])
    check("returned = no", set(f(returned="no")) == {"111-A", "444-D"})
    check("search by WB number", f(q="wb2") == ["222-B"])
    check("search by customer", set(f(q="ann")) == {"111-A"})
    check("dates swapped by mistake still work",
          len(A.auto_refunds({"from": ["2026-09-30"], "to": ["2026-09-01"]})["rows"]) == 4)

    print("\n── CSV ──")
    text, name = A.auto_refunds_csv({"from": ["2026-09-01"], "to": ["2026-09-30"]})
    lines = [l for l in text.splitlines() if l]
    check("a header and one line per refund", len(lines) == 5, str(len(lines)))
    check("named for the range", name == "auto_refunds_2026-09-01_to_2026-09-30.csv", name)
    os.remove(path)

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
