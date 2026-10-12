#!/usr/bin/env python3
"""
test_po_box.py — a PO-box order is labelled "BB" on Amazon, recorded, and never placed.

The owner's rule (12 Oct 2026): Zoro ships by UPS/FedEx and neither delivers to a PO box. Before,
the address scrape refused such an order but left its label alone, so a Manual-R PO-box order was
re-scraped 285 times in four days; when Zoro itself refused one, it was labelled "POBOX". Now both
paths write "BB", the address goes into po_box_orders, and Attention → PO boxes lists them.

Run:  python scripts/test_po_box.py   (no Chrome, no network; a temporary DB, never the real one)
"""
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   ({detail})" if detail and not ok else ""))


TMP = tempfile.mkdtemp()
DBP = os.path.join(TMP, "unified.db")

from utils import unified_db as U  # noqa: E402
U.DB_PATH = DBP                    # record_po_box writes HERE, never to data/automations_unified.db

# ── 1. detection keeps the address ───────────────────────────────────────────────
from platforms import amazon as A  # noqa: E402


class El:
    def __init__(self, text): self.text = text
    def is_displayed(self): return True


class FakeDriver:
    """An Amazon order page: address block + phone; no 'pending' banner."""
    def __init__(self, lines): self.lines = lines
    def get(self, url): pass
    def find_element(self, by, sel):
        if sel == A.AMAZON_ADDRESS_BLOCK_SELECTOR:
            return El("\n".join(self.lines))
        if sel == A.AMAZON_PHONE_SELECTOR:
            return El("Phone: 555 0100")
        from selenium.common.exceptions import NoSuchElementException
        raise NoSuchElementException(sel)
    def find_elements(self, by, sel): return []


A.is_login_page = lambda d: False
print("\ndetection")
r = A.scrape_amazon_address(FakeDriver(["Jane Doe", "PO Box 4471", "Meadow Vista, CA 95722"]), "111-0000000-0000001",
                            skip_navigate=True)
check("PO Box address → skip with reason PO_BOX", bool(r) and r.get("skip_order") and r.get("reason") == "PO_BOX", str(r))
check("…and the parsed address rides along",
      r.get("street_address") == "PO Box 4471" and r.get("city") == "Meadow Vista" and r.get("state_abbr") == "CA"
      and r.get("zip_code") == "95722", str(r))
r2 = A.scrape_amazon_address(FakeDriver(["John Roe", "Box 12", "Rock River, MI 49801"]), "111-0000000-0000002",
                             skip_navigate=True)
check("a bare 'Box 12' is still placed (no skip)", bool(r2) and not r2.get("skip_order"), str(r2))

# ── 2. the placer labels BB and records it ───────────────────────────────────────
import engine.orders as O  # noqa: E402

writes, journal = [], []
O.update_amazon_seller_id = lambda d, aid, label, prof=None, **k: writes.append((aid, label)) or True
O._rush_defer_wb = lambda aid, label: journal.append((aid, label))

print("\nplacer")
O._po_box_park(None, "111-0000000-0000001", r, "amazon_address", defer=False)
check(f"label '{O.PO_BOX_LABEL}' written on Amazon", writes == [("111-0000000-0000001", "BB")], str(writes))
c = sqlite3.connect(DBP)
row = c.execute("SELECT street, city, state, zip, detected_by, label_written, times_seen, profile, account "
                "FROM po_box_orders WHERE amazon_order_id='111-0000000-0000001'").fetchone()
check("address recorded in po_box_orders",
      row is not None and row[:6] == ("PO Box 4471", "Meadow Vista", "CA", "95722", "amazon_address", "BB"), str(row))
O._po_box_park(None, "111-0000000-0000001", {}, "zoro_rejected", defer=False)
row = c.execute("SELECT street, times_seen, detected_by FROM po_box_orders WHERE amazon_order_id='111-0000000-0000001'").fetchone()
check("seen again: count goes up, a blank address never overwrites the recorded one",
      row == ("PO Box 4471", 2, "zoro_rejected"), str(row))
writes.clear()
O._po_box_park(None, "111-0000000-0000003", {"street_address": "P.O. Box 9", "city": "Austin", "state_abbr": "TX",
                                              "zip_code": "78701"}, "amazon_address", defer=True)
check("rush mode: the BB label is journaled, not written mid-batch", journal == [("111-0000000-0000003", "BB")] and not writes,
      f"{journal} {writes}")

src = open(os.path.join(AUTO, "engine", "orders.py"), encoding="utf-8").read()
check("Zoro-refused PO box now labels BB (no more 'POBOX')",
      '_po_box_park(driver, amazon_id, addr, "zoro_rejected"' in src and 'amazon_id, "POBOX"' not in src)
seg = src[src.index('if not addr_raw or addr_raw.get("skip_order")'):][:900]
check("address-scrape PO box → labelled and returned as 'skipped' (no endless retry)",
      '_po_box_park(driver, amazon_id, addr_raw, "amazon_address"' in seg and 'return "skipped"' in seg)

print("\nreview fixes")
order = []
_real_record = U.record_po_box
U.record_po_box = lambda data, db_path=None: (order.append("record"), _real_record(data, db_path))[1]
O.update_amazon_seller_id = lambda d, aid, label, prof=None, **k: (order.append("label"), False)[1]
ok = O._po_box_park(None, "111-0000000-0000004", {"street_address": "PO Box 77", "city": "Reno", "state_abbr": "NV",
                                                  "zip_code": "89501"}, "amazon_address", defer=False)
row = c.execute("SELECT street, label_written FROM po_box_orders WHERE amazon_order_id='111-0000000-0000004'").fetchone()
check("address saved BEFORE the Amazon label is written", order[:2] == ["record", "label"], str(order))
check("a label write Amazon does not confirm is NOT recorded as 'BB'", ok is False and row == ("PO Box 77", ""), f"{ok} {row}")
O.update_amazon_seller_id = lambda d, aid, label, prof=None, **k: True
O._po_box_park(None, "111-0000000-0000004", {}, "amazon_address", defer=False)
row = c.execute("SELECT street, label_written, times_seen FROM po_box_orders WHERE amazon_order_id='111-0000000-0000004'").fetchone()
check("…and next cycle's successful write marks it 'BB'", row == ("PO Box 77", "BB", 2), str(row))
U.record_po_box = _real_record
src2 = open(os.path.join(AUTO, "engine", "orders.py"), encoding="utf-8").read()
check("Zoro-refused path respects rush (defer)", '_po_box_park(driver, amazon_id, addr, "zoro_rejected", defer=defer_amazon_writes)' in src2)
jsrc = open(os.path.join(AUTO, "utils", "sync_journal.py"), encoding="utf-8").read()
check("po_box_orders travels in the two-machine sync journal", '"po_box_orders": ["amazon_order_id"]' in jsrc)

# ── 3. the dashboard tab ─────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(AUTO, "dashboard"))
import po_boxes as P  # noqa: E402
c.execute("CREATE TABLE amazon_report_orders (amazon_order_id TEXT, account TEXT, purchase_day TEXT, order_status TEXT, "
          "merchant_order_id TEXT, ship_city TEXT, ship_state TEXT, ship_postal_code TEXT, title TEXT)")
c.executemany("INSERT INTO amazon_report_orders VALUES (?,?,?,?,?,?,?,?,?)", [
    ("111-0000000-0000001", "HOANK", "2026-10-08", "Pending", "BB", "Meadow Vista", "CA", "95722", "55 Gallon Barrel"),
    ("222-0000000-0000009", "Hathoro", "2026-09-05", "Unshipped", "POBOX", "Reno", "NV", "89501", "Cleaner"),
    ("333-0000000-0000005", "HOANK", "2026-10-01", "Shipped", "WB123", "X", "TX", "75001", "Other")])
c.commit()
P.DB = DBP
d = P.po_boxes({})
ids = [x["amazon_order_id"] for x in d["rows"]]
check("tab lists recorded PO boxes and older POBOX/BB-labelled orders", set(ids) ==
      {"111-0000000-0000001", "111-0000000-0000003", "111-0000000-0000004", "222-0000000-0000009"}, str(ids))
check("box forms are counted with numbers hidden", dict(d["kinds"]) == {"PO BOX #": 2, "P.O. BOX #": 1}, str(d["kinds"]))
check("account filter", {x["account"] for x in P.po_boxes({"account": ["Hathoro"]})["rows"]} == {"Hathoro"})
check("purchase day and product come from Amazon's report",
      next(x for x in d["rows"] if x["amazon_order_id"] == "111-0000000-0000001")["purchase_day"] == "2026-10-08")

check("box form: Zoro-refused spellings are named, not '(other)'",
      P.box_form("P0 Box 12") == "P0 BOX #" and P.box_form("Box # 2599") == "BOX # #" and P.box_form("PO Box123") == "PO BOX#"
      or (P.box_form("P0 Box 12"), P.box_form("Box # 2599"), P.box_form("PO Box123")) is None,
      f"{P.box_form('P0 Box 12')!r} {P.box_form('Box # 2599')!r} {P.box_form('PO Box123')!r}")
vsrc = open(os.path.join(AUTO, "dashboard", "v2.html"), encoding="utf-8").read()
seg2 = vsrc[vsrc.index("r_po_boxes(d){"):vsrc.index("bind(){")]
check("rows open the order in Seller Central (no dead drawer)", "U.sc(r.amazon_order_id)" in seg2 and "data-open" not in seg2)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
