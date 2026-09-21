#!/usr/bin/env python3
"""
test_placer_sync.py — order_placer_orders must end up carrying everything the order log has.

21 Sep 2026: 359 of the last 400 placed orders had no profile_name, no title, no margin and no
buyer phone, although order_log.txt carried all of them. Two writers fill this table — the
placer writes its own row the moment an order is placed (only what checkout knows), and
sync_order_placer_logs fills the rest from the log — and they fought:

  * sync_incremental skipped every amazon_order_id already in the DB, and the placer's row was
    always already there, so the log's extra fields were never written. They arrived only at
    the next closing (sync_all), which last ran on 19 Sep.
  * upsert_placer_order wrote every column it was handed, so whichever writer ran last could
    blank what the other had stored (the placer knows no tracking; the log gets it later).

Run:  python scripts/test_placer_sync.py
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


LINE = ("[2026-09-21 03:47:56] | WB:WB1111111111 | Profile:DM_COUPON | Amazon:{aid} | ASIN:B0GXL2JC3V"
        " | Title:Shipping Boxes 16x10x6 (25-Pack) | AQty:1 | SKU:G3056560 | ZQty:25 | ZMultiple:25"
        " | Earnings:$31.44 | Cost:$25.79 | ListPrice:$0.99 | Profit:$5.65 | FinalMargin:21.9%"
        " | EstMargin:21.9% | Coupon:ZSELECT9MA26 | Status:SUCCESS | Subtotal:$24.75 | Discount:$4.95"
        " | Shipping:$5.99 | Tax:$0.00 | ShipName:{name} | ShipStreet:21863 CHANEY RD | ShipCity:DALLAS"
        " | ShipState:TX | ShipZIP:75001 | ShipPhone:1415419861630411 | ShippingAmt:$0.00"
        " | AmazonFees:$5.55 | PurchaseDate:Sun, Sep 21, 2026 | ShipBy:Tue, Sep 23, 2026"
        " | DeliverBy:Thu, Sep 24, 2026 | TrackingIDs:{trk} | ZoroDeliverBy:2026-09-25 | ZoroStock:\n")


def main():
    import utils.unified_db as U
    tmp = os.path.join(tempfile.mkdtemp(), "unified.db")
    U.init_db(tmp)
    with sqlite3.connect(tmp) as c:
        for col in ("zoro_qty INTEGER", "zoro_deliver_by TEXT", "zoro_stock INTEGER", "alt_used TEXT",
                    "main_sku TEXT", "zoro_list_price REAL", "zoro_subtotal REAL", "zoro_shipping REAL"):
            try:
                c.execute(f"ALTER TABLE order_placer_orders ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
    U.DB_PATH = tmp
    sp = importlib.util.spec_from_file_location("spl", os.path.join(HERE, "sync_order_placer_logs.py"))
    SY = importlib.util.module_from_spec(sp); sp.loader.exec_module(SY)
    SY.get_conn, SY.upsert_placer_order = U.get_conn, U.upsert_placer_order

    def row(aid):
        with sqlite3.connect(tmp) as c:
            c.row_factory = sqlite3.Row
            r = c.execute("SELECT * FROM order_placer_orders WHERE amazon_order_id=?", (aid,)).fetchone()
        return dict(r) if r else None

    def write_log(*lines):
        p = os.path.join(tempfile.mkdtemp(), "order_log.txt")
        open(p, "w", encoding="utf-8").write("".join(lines))
        return p

    # ── 1. an upsert never blanks what is already stored ─────────────────────────────────
    print("\n── a write never erases a value another writer stored ──")
    U.upsert_placer_order({"amazon_order_id": "A-1", "account": "Hathoro", "profile_name": "DM_COUPON",
                           "title": "Shipping Boxes", "tracking_ids": "539601215050", "earnings": 31.44})
    U.upsert_placer_order({"amazon_order_id": "A-1", "account": "Hathoro", "tracking_ids": "",
                           "title": None, "zoro_order_total": 25.79})
    r = row("A-1")
    check("a blank ('' and None) leaves the stored value alone",
          r["tracking_ids"] == "539601215050" and r["title"] == "Shipping Boxes", str(r)[:120])
    check("a real value from the second write is applied", r["zoro_order_total"] == 25.79)
    U.upsert_placer_order({"amazon_order_id": "A-1", "tracking_ids": "999", "shipping_amt": 0.0})
    r = row("A-1")
    check("a real value does replace an older one", r["tracking_ids"] == "999")
    check("zero is a value, not a blank", r["shipping_amt"] == 0.0, str(r["shipping_amt"]))

    # ── 2. the placer's partial row is enriched, not skipped ─────────────────────────────
    print("\n── the placer writes first; the log sync fills the rest ──")
    aid = "111-2223334-4445556"
    U.upsert_placer_order({"amazon_order_id": aid, "account": "Hathoro", "asin": "B0GXL2JC3V",
                           "sku": "G3056560", "quantity": 1, "zoro_qty": 25, "earnings": 31.44,
                           "zoro_order_total": 25.79, "ship_city": "DALLAS", "ship_state": "TX",
                           "purchase_date": "Sun, Sep 21, 2026"})
    before = row(aid)
    check("the placer's row starts without the log-only fields",
          all(not before[k] for k in ("profile_name", "title", "profit_percent", "ship_phone", "ship_name")))
    lp = write_log(LINE.format(aid=aid, name="William Bishop", trk=""))
    st = SY.sync_incremental(lp, verbose=False)
    after = row(aid)
    check("one existing row is enriched, none inserted", st["enriched"] == 1 and st["inserted"] == 0, str(st))
    check("profile, title, margin, phone, name and street are now filled",
          after["profile_name"] == "DM_COUPON" and after["title"].startswith("Shipping Boxes")
          and after["profit_percent"] == 21.9 and after["ship_phone"] == "1415419861630411"
          and after["ship_name"] == "William Bishop" and after["ship_street"] == "21863 CHANEY RD",
          str({k: after[k] for k in ("profile_name", "title", "profit_percent", "ship_phone", "ship_name")}))
    check("what the placer already wrote is untouched",
          after["earnings"] == 31.44 and after["zoro_order_total"] == 25.79 and after["ship_city"] == "DALLAS")
    check("running again writes nothing", SY.sync_incremental(lp, verbose=False)["total"] == 0)

    # ── 3. tracking arrives later, and is not blanked in between ─────────────────────────
    print("\n── tracking that the log gains later is picked up, and never wiped ──")
    lp2 = write_log(LINE.format(aid=aid, name="William Bishop", trk="539601215050"))
    SY.sync_incremental(lp2, verbose=False)
    check("a tracking id added to the log later is stored", row(aid)["tracking_ids"] == "539601215050")
    SY.sync_incremental(lp, verbose=False)            # the older, tracking-less line again
    check("re-reading a line without tracking does not wipe it", row(aid)["tracking_ids"] == "539601215050")

    # ── 4. new orders, and rows this log is not responsible for ──────────────────────────
    print("\n── inserts, and lines that are not Amazon orders ──")
    new_id = "114-9998887-7776665"
    wm = ("[2026-07-16 23:05:20] | WB:WB4227719744 | Profile:WALMART_RESELLER | Amazon:WALMART:PO129119898402108"
          " | ASIN:N/A | Title:X | AQty:1 | SKU:G1 | Earnings:$1.00 | Cost:$1.00 | Status:SUCCESS\n")
    st = SY.sync_incremental(write_log(LINE.format(aid=new_id, name="Ann Lee", trk=""), wm), verbose=False)
    check("an order the DB has never seen is inserted", st["inserted"] == 1 and row(new_id) is not None, str(st))
    check("the inserted row carries the log's fields", row(new_id)["profile_name"] == "DM_COUPON")
    check("a Walmart placement is skipped, not counted as an error",
          st["errors"] == 0 and st["skipped_non_amazon"] == 1, str(st))

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL)); sys.exit(1)


if __name__ == "__main__":
    main()
