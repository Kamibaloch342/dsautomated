#!/usr/bin/env python3
"""
test_backorder_and_alt_dates.py — the four fixes of 27 Oct 2026.

  1. A backordered Zoro item is NOT in stock (G7467735, the pink hard hat: Zoro takes the order and
     only promises "an estimated ship date will be emailed to you within 2 business days").
  2. The delivery date comes from Zoro's expected-date API for the buyer's ZIP, and an ALT swap reads
     the ALT's own date and is held under the same 4-day rule — no more DELIVERY_UNKNOWN on every cycle
     (27 orders, 218 tries, 24–26 Oct, all but one ALT swaps).
  3. The priority checker tests the ALT when the main is out (B0HH6Q9FF3: main G410877336 OOS, ALT
     G310877337 takes VEHICLE10926, yet the ASIN stayed in DM_Deciding).
  4. The "your order has shipped" message goes under "Other", never "Notify of a problem".

Run:  python scripts/test_backorder_and_alt_dates.py   (no Chrome, no network, no sheet)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   ({detail})" if detail and not ok else ""))


class FakeDriver:
    """Answers zoro_api()'s in-browser fetch with a canned {s, d} reply and records each call."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def execute_script(self, script, *args):
        self.calls.append(args)
        return self.reply(*args) if callable(self.reply) else self.reply


def avail(status, typ, cartable=True, back=False):
    return {"s": 200, "d": {"payload": [{"availabilityStatus": status, "availabilityType": typ,
                                          "canBackOrder": back, "isCartable": cartable,
                                          "inventoryQuantity": None, "zoroNo": "G1"}]}}


import platforms.zoro as Z  # noqa: E402

print("\n1. backordered = not in stock")
# Zoro's real replies, 27 Oct 2026 (PUT /scm/v1/inventory/availability)
check("G7467735 'Backordered' / backOrder / isCartable true → (False, 'Backordered')",
      Z.check_inventory(FakeDriver(avail("Backordered", "backOrder", True, True)), "G7467735", 1) == (False, "Backordered"))
check("G5803393 'In Stock' → (True, 'In Stock')",
      Z.check_inventory(FakeDriver(avail("In Stock", "inStock")), "G5803393", 1) == (True, "In Stock"))
check("'Limited Stock' and 'Drop Ship' stay orderable",
      Z.check_inventory(FakeDriver(avail("Limited Stock", "limitedStock")), "G1", 1)[0] is True
      and Z.check_inventory(FakeDriver(avail("Drop Ship", "dropShip")), "G1", 1)[0] is True)
check("'Out of Stock' (isCartable false) unchanged",
      Z.check_inventory(FakeDriver(avail("Out of Stock", "outOfStock", False)), "G1", 1) == (False, "Out of Stock"))
check("either backorder marker is enough",
      Z.is_backordered({"availabilityType": "backOrder"}) and Z.is_backordered({"availabilityStatus": "Backordered"})
      and not Z.is_backordered({"availabilityStatus": "In Stock", "canBackOrder": False}) and not Z.is_backordered(None))
check("an API error is still an API error (never read as OOS)",
      Z.check_inventory(FakeDriver({"s": 500, "d": "x"}), "G1", 1) == (False, "API_ERROR"))

print("\n2. Zoro's delivery estimate (POST /scm/v1/expected-date/bulk-expected-dates)")
reply = {"s": 200, "d": {"payload": [
    {"zoroNo": "G5803393", "quantity": 1, "zipCode": "90001", "leadTime": 1.0, "shipDate": "2026-10-26",
     "deliveryDate": "2026-10-27", "customerDateTime": "2026-10-26T12:22:04"},
    {"zoroNo": "G221882659", "quantity": 6, "zipCode": "90001", "leadTime": 2.0, "shipDate": "2026-10-28",
     "deliveryDate": "2026-11-04"}], "errors": []}}
drv = FakeDriver(reply)
got = Z.get_expected_delivery(drv, [("G5803393", 1), ("G221882659", 6)], "90001-3602")
check("dates per SKU", got == {"G5803393": "2026-10-27", "G221882659": "2026-11-04"}, str(got))
url, method, body = drv.calls[0][0], drv.calls[0][1], json.loads(drv.calls[0][2])
check("the call the product page makes: POST, ZIP cut to 5 digits, real quantity",
      url.endswith("/scm/v1/expected-date/bulk-expected-dates") and method == "POST"
      and [(b["zoroNo"], b["quantity"], b["zipCode"]) for b in body] == [("G5803393", 1, "90001"), ("G221882659", 6, "90001")]
      and all("localTime" in b and b["featureFlag"] is None for b in body), str((url, method, body)))
check("no usable ZIP → {} and no call", Z.get_expected_delivery(FakeDriver(reply), [("G1", 1)], "") == {}
      and Z.get_expected_delivery(FakeDriver(reply), [("G1", 1)], "123") == {})
check("HTTP error / odd payload / exception → {}",
      Z.get_expected_delivery(FakeDriver({"s": 401, "d": {}}), [("G1", 1)], "90001") == {}
      and Z.get_expected_delivery(FakeDriver({"s": 200, "d": {"payload": [{"zoroNo": "G1", "deliveryDate": None}]}}),
                                  [("G1", 1)], "90001") == {}
      and Z.get_expected_delivery(FakeDriver(lambda *a: 1 / 0), [("G1", 1)], "90001") == {})

import engine.orders as O  # noqa: E402
check("placer helper: buyer ZIP from the address, '' on failure",
      O._zip5({"zip_code": "08825-3704"}) == "08825"
      and O._zoro_api_delivery(FakeDriver(reply), "G5803393", 1, {"zip_code": "90001-3602"}) == "2026-10-27"
      and O._zoro_api_delivery(FakeDriver({"s": 500}), "G5803393", 1, {"zip_code": "90001"}) == "")

src = open(os.path.join(AUTO, "engine", "orders.py"), encoding="utf-8").read()
body = src[src.index("def process_order("):src.index("#  COUPON SHEET LOADER")]
pre = body[body.index("_pcart = False"):body.index("# ── Zoro would deliver too late")]
check("main SKU: the API date is asked only when check_inventory said in stock, and wins over the page",
      "if _pcart:" in pre and pre.index("if _pcart:") < pre.index("_zoro_api_delivery(driver, _psku")
      and pre.index('addr["_zoro_deliver_by"] = _zdel') < pre.index("_zoro_api_delivery(driver, _psku"))
alt_at = body.index('item["alt_used"] = True')
alt = body[alt_at:alt_at + 1400]
check("ALT swap: main's date dropped, ALT's own date read, same 4-day rule, held → skipped",
      'addr.pop("_zoro_deliver_by", None)' in alt and '_zoro_api_delivery(driver, item["sku"]' in alt
      and "_gap_limit > 0 and not manual_override and _delivery_gate_holds(" in alt and 'return "skipped"' in alt)
check("…before the ALT goes into the cart and before the placement lock",
      alt_at < body.index("cart_ok, api_price = clear_cart_and_add(") < body.index("plock.reserve(amazon_id"))
check("…only for the primary item (the one the gate has always judged)",
      "if item is resolved_items[0] and isinstance(addr, dict):" in alt)

print("\n   checkout page without a date: say once what it shows")
import checkout.dom as D  # noqa: E402
import io
import contextlib
D._DATELESS_NOTED.clear()
lines = {"lines": ["Arrives Thu, Oct 29", "Standard Ground"], "hidden": ""}
out = io.StringIO()
with contextlib.redirect_stdout(out):
    D._note_dateless_checkout(FakeDriver(lines))
    D._note_dateless_checkout(FakeDriver(lines))
check("the page's delivery lines are printed once per process", out.getvalue().count("Arrives Thu, Oct 29") == 1, out.getvalue())
D._DATELESS_NOTED.clear()
with contextlib.redirect_stdout(io.StringIO()):
    D._note_dateless_checkout(FakeDriver(lambda *a: 1 / 0))
check("never raises", True)

print("\n3. the priority checker tests the SKU an order would buy")
import engine.priority_check as P  # noqa: E402
stock = {}
tested = []
_ready, _code, _inv = P.zoro_session_ready, P._check_code, Z.check_inventory
P.zoro_session_ready = lambda d: True
P._check_code = lambda driver, asin, code, qty, m, alt_note, *a: (
    tested.append((code, alt_note)) or {"Check_Status": "COUPON_OK", "Check_Note": "priority VEHICLE10926 works"})
Z.check_inventory = lambda d, sku, q: stock.get(sku, (True, "In Stock"))
try:
    sku_map = {"B0HH6Q9FF3": {"zoro_code": "G410877336", "alt_sku": "G310877337", "qty_multiplier": 1, "zoro_multiple": 1}}
    stock.update({"G410877336": (False, "Out of Stock"), "G310877337": (True, "In Stock")})
    r = P.check_one(None, "B0HH6Q9FF3", sku_map, ["VEHICLE10926"])
    check("B0HH6Q9FF3: main OOS, ALT in stock → the ALT is tested, and the note says so",
          tested[-1][0] == "G310877337" and r["Check_Status"] == "COUPON_OK"
          and r["Check_Note"].startswith("main G410877336 Out of Stock → tested ALT G310877337; priority"), str((tested, r)))
    stock["G410877336"] = (False, "Backordered")
    P.check_one(None, "B0HH6Q9FF3", sku_map, ["VEHICLE10926"])
    check("a backordered main also sends the test to the ALT", tested[-1][0] == "G310877337")
    stock["G410877336"] = (True, "In Stock")
    r = P.check_one(None, "B0HH6Q9FF3", sku_map, ["VEHICLE10926"])
    check("main back in stock → the main is tested again, no ALT note",
          tested[-1] == ("G410877336", "") and "ALT" not in r["Check_Note"])
    stock.update({"G410877336": (False, "Out of Stock"), "G310877337": (False, "Backordered")})
    n = len(tested)
    r = P.check_one(None, "B0HH6Q9FF3", sku_map, ["VEHICLE10926"])
    check("both out → OOS naming both, nothing tested",
          len(tested) == n and r["Check_Status"] == "OOS" and "G410877336" in r["Check_Note"] and "G310877337" in r["Check_Note"], str(r))
    stock.update({"G410877336": (False, "API_ERROR")})
    P.check_one(None, "B0HH6Q9FF3", sku_map, ["VEHICLE10926"])
    check("an inventory API error never switches to the ALT (the main's test reports TRANSIENT)", tested[-1][0] == "G410877336")
    P.check_one(None, "B0X", {"B0X": {"zoro_code": "G1", "alt_sku": ""}}, [])
    check("no ALT in the sheet → the main, as before", tested[-1] == ("G1", ""))
finally:
    P.zoro_session_ready, P._check_code, Z.check_inventory = _ready, _code, _inv
check("the ALT's brand is looked up, not the sheet's (that is the main's brand)",
      'brand = "" if alt_note else (m.get("brand") or "").strip()' in open(P.__file__, encoding="utf-8").read())

print("\n4. the shipped message is not filed as a problem")
tsrc = open(os.path.join(AUTO, "engine", "tracking.py"), encoding="utf-8").read()
ship = tsrc[tsrc.index("def send_message_to_customer("):tsrc.index("# --- Helper Functions (Report Download")]
check("send_message_to_customer asks for 'Other' (contactBuyer) only",
      "radio_priority=['contactBuyer'])" in ship and "'unexpectedProblem'" not in ship.split("radio_priority=")[1])
deny = tsrc[tsrc.index("def send_cancellation_denial_message("):tsrc.index("def send_message_to_customer(")]
check("the cancellation reply tries 'Other' first", "radio_priority=['contactBuyer', 'unexpectedProblem'])" in deny)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
