#!/usr/bin/env python3
"""
test_fedex_status.py — FedEx lookups that fail must say so, and must never be written as facts.

What happened on 19 Sep 2026: FedEx's public tracking began serving its bot-defence page
("We can't find that tracking number") to this machine's automated sessions, and its in-page
API call failed with "Failed to fetch". Three separate weaknesses turned that into bad data:

  1. FedEx had renamed its statuses ("On the way", "We have your package", "Delivery
     updated", "We tried to deliver"). The CDP tier stored any unrecognised keyStatus
     verbatim; the DOM tier did not know the words at all.
  2. The DOM scraper took the FIRST status word in document order — "Label created" from the
     travel history — instead of the highest-ranked one on the page.
  3. Nothing distinguished "FedEx refused us" from "not delivered yet". closing wrote
     "Unknown" onto 4,703 live orders and stamped them enriched_from_fedex=1; fedex_recheck
     persisted every order in every batch; the run printed "0 fully delivered / 30 in batch"
     118 times and daily_bulk counted the timeout as a critical failure of the whole chain.

Run:  python scripts/test_fedex_status.py
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


def func_src(text, name):
    tree = ast.parse(text)
    lines = text.split("\n")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{name} not found")


def main():
    import engine.fedex_api as FA
    from engine.fedex_api import normalize_status, status_from_page_text, STATUS_RANK

    # ── 1. one vocabulary ────────────────────────────────────────────────────
    print("\n── every FedEx wording maps to one canonical status ──")
    table = {
        "Delivered": "Delivered", "DELIVERED": "Delivered",
        "In transit": "InTransit", "On the way": "InTransit", "We have your package": "InTransit",
        "Picked up": "InTransit", "Departed FedEx location": "InTransit", "At destination sort facility": "InTransit",
        "Out for delivery": "OutForDelivery", "On FedEx vehicle for delivery": "OutForDelivery",
        "Label created": "LabelCreated", "Shipment information sent to FedEx": "LabelCreated",
        "We tried to deliver": "DeliveryException", "Delivery exception": "DeliveryException",
        "Delivery updated": "DeliveryException", "Shipment exception": "DeliveryException",
    }
    bad = {k: normalize_status(k) for k, v in table.items() if normalize_status(k) != v}
    check("old and 2026 phrases all normalise", not bad, str(bad))
    check("status codes win over text", normalize_status("whatever", "DL") == "Delivered"
          and normalize_status("", "OD") == "OutForDelivery" and normalize_status("", "IT") == "InTransit")
    check("unrecognised wording becomes Unknown, never the raw text",
          normalize_status("Some brand new FedEx phrase") == "Unknown")
    check("empty is Unknown", normalize_status("") == "Unknown" and normalize_status(None) == "Unknown")
    check("the CDP parser no longer stores keyStatus verbatim",
          "status = key_status" not in func_src(src("engine/fedex_api.py"), "_parse_package"))
    check("Delivered outranks everything, LabelCreated ranks lowest of the real statuses",
          STATUS_RANK["Delivered"] > STATUS_RANK["OutForDelivery"] > STATUS_RANK["InTransit"] > STATUS_RANK["LabelCreated"] > STATUS_RANK["Unknown"])

    # ── 2. precedence over the whole page, not position ──────────────────────
    print("\n── the page is read by precedence, not by the first word found ──")
    page = ("Tracking  Delivered  Tuesday 9/16/2026 at 2:20 pm  Signed for by: J.SMITH\n"
            "Travel history\n9/16 Out for delivery\n9/15 In transit\n9/14 Label created")
    check("a delivered parcel's page reads Delivered even with 'Label created' in its history",
          status_from_page_text(page) == "Delivered")
    hist_first = "Travel history\n9/14 Label created\n9/15 In transit\n9/16 On the way\nStatus: In transit"
    check("history listed before the header still yields the highest status, not the oldest",
          status_from_page_text(hist_first) == "InTransit")
    check("a page with nothing recognisable is Unknown", status_from_page_text("FedEx ® Support") == "Unknown")
    err = ("FedEx\nTracking\nWe can’t find that tracking number. Please check with the shipper "
           "to make sure it’s the correct number.")
    check("FedEx's bot-defence page is Blocked — not Unknown, not a status",
          status_from_page_text(err) == "Blocked")
    d = FA.FedExTracker._parse_browser_body(page, "123")
    check("_parse_browser_body uses the precedence reader", d["status"] == "Delivered")
    check("_wait_for_status now stops on the bot-defence sentence and knows the new words",
          "can't find that tracking number" in " ".join(FA.FedExTracker._STATUS_WORDS).lower()
          and "On the way" in FA.FedExTracker._STATUS_WORDS)

    # ── 3. closing never writes an unresolved lookup, never regresses ────────
    print("\n── closing: unresolved is not a status; history never runs backwards ──")
    import closing

    class FakeFedEx:
        blocked = False
        def __init__(self, answers): self.answers = answers
        def _empty_detail(self): return {"status": "Unknown", "delivery_date": "", "estimated_window": "",
                                         "last_scan_location": "", "last_scan_time": "", "signed_by": "",
                                         "left_at": "", "delivery_note": "", "from_loc": "", "to_loc": ""}
        def track_batch(self, tids):
            return [dict(self._empty_detail(), status=self.answers.get(t, "Unknown")) for t in tids]

    def run(answers, orders, blocked=False):
        fx = FakeFedEx(answers); fx.blocked = blocked
        batch = [dict(o) for o in orders]
        summ = closing.bulk_fedex_check(batch, fx)
        return summ, batch

    summ, b = run({"T1": "Unknown"}, [{"amazon_order_id": "A", "tracking_ids": "T1", "delivery_status": ""}])
    check("Unknown is not written onto a blank row", b[0].get("delivery_status") == "" and not b[0].get("_fedex_touched"))
    check("...and is counted as unresolved", summ["unresolved"] == 1 and summ["resolved"] == 0)
    summ, b = run({"T1": "Blocked"}, [{"amazon_order_id": "A", "tracking_ids": "T1", "delivery_status": "InTransit"}])
    check("Blocked leaves an existing status untouched", b[0]["delivery_status"] == "InTransit" and not b[0].get("_fedex_touched"))
    summ, b = run({"T1": "LabelCreated"}, [{"amazon_order_id": "A", "tracking_ids": "T1", "delivery_status": "OutForDelivery"}])
    check("a lower-ranked answer never overwrites a higher one (no regression)",
          b[0]["delivery_status"] == "OutForDelivery" and not b[0].get("_fedex_touched"))
    summ, b = run({"T1": "Delivered"}, [{"amazon_order_id": "A", "tracking_ids": "T1", "delivery_status": "LabelCreated"}])
    check("a real Delivered answer is written and counted",
          b[0]["delivery_status"] == "Delivered" and b[0].get("_fedex_touched") and summ["delivered"] == 1)
    summ, b = run({"T1": "Delivered", "T2": "InTransit"},
                  [{"amazon_order_id": "A", "tracking_ids": "T1, T2", "delivery_status": ""}])
    check("a multi-parcel order with one still moving is PartialDelivered, not Delivered",
          b[0]["delivery_status"] == "PartialDelivered")
    summ, b = run({"T1": "Unknown", "T2": "Unknown"},
                  [{"amazon_order_id": "A", "tracking_ids": "T1", "delivery_status": ""},
                   {"amazon_order_id": "B", "tracking_ids": "T2", "delivery_status": ""}], blocked=True)
    check("the summary reports blocked, so the caller can stop", summ["blocked"] is True and summ["unresolved"] == 2)
    check("it returns a summary even when the batch has no tracking ids at all",
          closing.bulk_fedex_check([{"amazon_order_id": "Z", "tracking_ids": ""}], FakeFedEx({}))["orders"] == 1)

    # ── 4. the CDP tier stops when FedEx is refusing it ──────────────────────
    print("\n── a refused session stops early instead of burning every batch ──")

    class FakeDriver:
        current_url = "https://www.fedex.com/wtrk/track/"
        def __init__(self): self.calls = 0
        def get(self, *a): pass
        def execute_async_script(self, *a):
            self.calls += 1
            return '{"ok": false, "status": -1, "error": "TypeError: Failed to fetch"}'
        def execute_script(self, *a): return None

    t = FA.FedExTracker(headless=True)
    t.driver = FakeDriver(); t._owns_driver = False
    ids = [str(100000000000 + i) for i in range(FA._FEDEX_CDP_BATCH_SIZE * 6)]   # 6 chunks
    res = t.track_batch_via_cdp(ids)
    check("after two blocked chunks the tier stops (2 calls, not 6)", t.driver.calls == 2, f"{t.driver.calls} calls")
    check("the tracker flags itself blocked", t.blocked is True and t.blocked_chunks == 2)
    check("every id comes back unresolved, none invented", len(res) == len(ids) and all(r["status"] == "Unknown" for r in res))
    fn = func_src(src("engine/fedex_api.py"), "track_batch")
    check("track_batch skips the DOM fallback once blocked", "self.blocked" in fn and "skipping DOM fallback" in fn)

    # ── 5. the recheck script and the chain ──────────────────────────────────
    print("\n── fedex_recheck and daily_bulk ──")
    rc = src("scripts/fedex_recheck.py")
    check("persist() only writes rows FedEx answered", 'if not o.get("_fedex_touched"):\n        return False' in rc)
    check("a worker stops after two dead batches or a blocked flag",
          "_dead_batches += 1" in rc and "_dead_batches >= 2 or summary.get(\"blocked\")" in rc)
    check("a refused run exits 2 — distinct from success (0) and from a crash (1)",
          'sys.exit(2)' in rc and '_counter["blocked"]' in rc)
    check("the summary names unresolved orders", "unresolved." in rc)
    db = src("scripts/daily_bulk.py")
    fx_call = db[db.index('run(f"FedEx re-check {a}"'):db.index('run(f"FedEx re-check {a}"') + 400]
    check("FedEx is no longer a critical step of the closing chain", "critical=False" in fx_call)
    check("the remedy is named in the code: the official Track API",
          "apis.fedex.com" in rc and "config/accounts.json" in rc)

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
