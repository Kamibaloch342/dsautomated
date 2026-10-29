#!/usr/bin/env python3
"""
test_manual_upgrades.py — the owner's three upgrades of 30 Oct 2026.

  1. An order marked '<WB> - D' gets a Seller Notes line saying whether a priority coupon works on the
     item ("C active - VEHICLE10926" / "C none" / "C untested"), so the reviewer can write Manual-C or
     Manual-R at once.
  2. Manual-C/-R on a multi-item order with an item out of stock: buy what is in stock (the buyer agreed
     to receive the other item later).
  3. "Manual-C G7467735" / "Manual-R G7467735": buy that Zoro code instead of the sheet's (another colour
     or size the buyer accepted).

Run:  python scripts/test_manual_upgrades.py   (no Chrome, no network, no sheet)
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


import engine.orders as O  # noqa: E402
import engine.priority_check as P  # noqa: E402

print("\nthe tag")
cases = {
    "Manual-C": ("C", ""), "manual-r": ("R", ""), "Manual C": ("C", ""),
    "Manual-C G7467735": ("C", "G7467735"), "manual-r g7467735": ("R", "G7467735"),
    "Manual-C-G7467735": ("C", "G7467735"), "Manual C: G7467735 ": ("C", "G7467735"),
    "Manual-CG7467735": ("C", "G7467735"),
    "Manual-C White": (None, ""), "Manual-C G7467735 White": (None, ""), "G7467735": (None, ""),
    "Manual": (None, ""), "": (None, ""), None: (None, ""),
}
bad = {k: (O._parse_manual_tag(k), O._parse_manual_code(k)) for k in cases
       if (O._parse_manual_tag(k), O._parse_manual_code(k)) != cases[k]}
check("Manual-C/-R with or without a Zoro code after it; anything else is not a tag", not bad, str(bad))
check("the code is only read from a real tag (a bare G-code never places an order)",
      O._parse_manual_code("G7467735") == "" and O._parse_manual_tag("G7467735") is None)


def item(asin, sku, alt="", qty=1):
    return {"asin": asin, "sku": sku, "alt_sku": alt, "final_qty": qty, "title": asin}


print("\n3. the tag's Zoro code")
it = item("B0G14ZRRNH", "G7467787", alt="G7467735")
O._swap_manual_code(it, "G7467820")
check("single item: the code replaces the sheet's, the ALT is dropped, the sheet's code is kept for the log",
      it["sku"] == "G7467820" and it["alt_sku"] == "" and it["sheet_sku"] == "G7467787" and it["manual_code"])
check("order log note names both codes",
      O._manual_code_notes([it]) == ["MANUAL CODE: placed G7467820 for B0G14ZRRNH (sheet G7467787)"])
check("order fields: one item plain, several joined",
      O._order_fields([item("A", "G1", qty=2)]) == ("A", "G1", 2)
      and O._order_fields([item("A", "G1", qty=2), item("B", "G2")]) == ("A, B", "G1 x2, G2 x1", "2, 1"))


def plan(items, code, stock):
    calls = []
    def in_stock(sku, qty):
        calls.append(sku)
        return stock.get(sku, (True, "In Stock"))
    return O._manual_multi_item_plan(items, code, in_stock), calls


OUT, BACK, ERR = (False, "Out of Stock"), (False, "Backordered"), (False, "API_ERROR")

(k, d, p), _ = plan([item("A", "G1"), item("B", "G2"), item("C", "G3")], "G9", {"G2": OUT})
check("multi-item + code: it replaces the ONE item that is out", p is None and [i["sku"] for i in k] == ["G1", "G9", "G3"]
      and not d and k[1]["sheet_sku"] == "G2", str((k, d, p)))
(k, d, p), _ = plan([item("A", "G1"), item("B", "G2", alt="G22")], "G9", {"G2": OUT, "G22": BACK})
check("…main and ALT both out counts as out", p is None and k[1]["sku"] == "G9")
(k, d, p), _ = plan([item("A", "G1"), item("B", "G2")], "G9", {})
check("…every item in stock → not placed: which item is the code for?", p and "every item is in stock" in p, str(p))
(k, d, p), _ = plan([item("A", "G1"), item("B", "G2"), item("C", "G3")], "G9", {"G1": OUT, "G3": BACK})
check("…two items out → not placed, both named", p and "2 items are out" in p and "A G1" in p and "C G3" in p, str(p))

print("\n2. Manual-C/-R multi-item: buy what is in stock")
(k, d, p), _ = plan([item("A", "G1"), item("B", "G2")], "", {"G2": BACK})
check("one item backordered → the other is bought, the backordered one is reported",
      p is None and [i["sku"] for i in k] == ["G1"] and [(i["sku"], s) for i, s in d] == [("G2", "Backordered")])
(k, d, p), _ = plan([item("A", "G1"), item("B", "G2", alt="G22")], "", {"G2": OUT})
check("an item whose ALT is in stock is NOT dropped (the cart loop swaps to the ALT)", not d and len(k) == 2)
(k, d, p), _ = plan([item("A", "G1"), item("B", "G2")], "", {"G1": OUT, "G2": OUT})
check("everything out → unchanged (the normal flow labels it OOS)", not d and len(k) == 2 and p is None)
(k, d, p), calls = plan([item("A", "G1"), item("B", "G2"), item("C", "G3")], "", {"G2": ERR, "G3": OUT})
check("an inventory API error anywhere → unchanged, never split on a failed check", not d and len(k) == 3 and p is None)
(k, d, p), _ = plan([item("A", "G1"), item("B", "G2")], "", {})
check("all in stock → unchanged", not d and len(k) == 2)

print("\n1. the coupon line for a '<WB> - D' order")
V = {
    "B1": {"status": "COUPON_OK", "coupon": "VEHICLE10926 (priority)", "at": "2026-10-28 14:05", "profile": "DM_COUPON"},
    "B2": {"status": "SELECT", "at": "2026-10-28 14:06", "profile": "DM_COUPON"},
    "B3": {"status": "NO_PRIORITY", "at": "2026-10-28 14:07", "profile": "DM_COUPON"},
    "B4": {"status": "COUPON_OK", "coupon": "OLDCODE (priority)", "at": "2026-10-28 14:08", "profile": "DM_COUPON"},
    "B5": {"status": "COUPON_OK", "coupon": "VEHICLE10926 (priority)", "at": "2026-10-20 09:00", "profile": "DM_COUPON"},
    "B6": {"status": "TRANSIENT", "at": "2026-10-28 14:09", "profile": "DM_COUPON"},
    "B7": {"status": "COUPON_OK", "coupon": "VEHICLE10926 (priority)", "at": "2026-10-28 14:05", "profile": "HS_COUPON"},
    "B8": {"status": "PROMO_INELIGIBLE", "at": "2026-10-28 14:05", "profile": "DM_COUPON"},
    "B9": {"status": "OOS", "at": "2026-10-28 14:05", "profile": "DM_COUPON"},
}
_seen = P.coupon_set_seen
P.coupon_set_seen = lambda store: {"codes": ["VEHICLE10926"], "at": "2026-10-26 10:00"}
try:
    note = lambda a, codes=("VEHICLE10926", "SUPPLIES11726"): P.coupon_status_note(a, "DM", list(codes), V)
    check("works with a current code → 'C active - CODE (checked …)'",
          note("B1") == "C active - VEHICLE10926 (checked 28 Oct 14:05)", note("B1"))
    check("Zoro Select → C none, the reseller gets 10 %", note("B2").startswith("C none - Zoro Select"), note("B2"))
    check("no priority coupon → C none", note("B3").startswith("C none"), note("B3"))
    check("Zoro's best price → C none", note("B8") == "C none - Zoro's best price, no coupon applies")
    check("its code has left the Coupons tab → C untested", note("B4").startswith("C untested"), note("B4"))
    check("checked before the current coupons arrived → C untested", note("B5").startswith("C untested - checked before"))
    check("Zoro did not answer → C untested", note("B6").startswith("C untested"))
    check("another store's check does not count → C untested", note("B7").startswith("C untested"))
    check("never checked → C untested", note("B0").startswith("C untested"))
    check("out of stock at the check → C unknown", note("B9").startswith("C unknown"))
    check("no priority codes in the Coupons tab → C none", note("B1", codes=()).startswith("C none - no priority"))
    txt = O._dup_coupon_lines(["B1", "B3", "B1"], "DM", ["VEHICLE10926"], V)
    check("multi-item: one line per ASIN, prefixed; repeats once",
          txt == "B1: C active - VEHICLE10926 (checked 28 Oct 14:05)\nB3: C none (checked 28 Oct 14:07)", txt)
    check("single item: just the line", O._dup_coupon_lines(["B1"], "DM", ["VEHICLE10926"], V).startswith("C active"))
    check("Coupons tab unreadable → C unknown (never 'none')",
          O._dup_coupon_lines(["B1"], "DM", None, V) == "C unknown - the Coupons tab could not be read")
finally:
    P.coupon_set_seen = _seen

print("\n   the note is added below what is already there")
import platforms.amazon as A  # noqa: E402


class El:
    def __init__(self, value=""):
        self.value, self.typed = value, []

    def get_attribute(self, n):
        return self.value if n == "value" else None

    def click(self):
        pass

    def send_keys(self, *keys):
        self.typed.append(keys)


class Drv:
    def __init__(self, el):
        self.el = el

    def find_element(self, *a):
        return self.el

    def find_elements(self, *a):
        return [self.el]

    def execute_script(self, *a):
        return None


_conf = A._confirm_on_order_page
A._confirm_on_order_page = lambda *a, **k: True
try:
    from selenium.webdriver.common.keys import Keys

    def typed_text(el):
        """What the box holds after the keystrokes: select-all + Backspace clears, Return is a new line."""
        out = ""
        for k in el.typed:
            if len(k) != 1:
                continue                                   # Cmd/Ctrl+A
            if k[0] == Keys.BACK_SPACE:
                out = ""
            elif k[0] == Keys.RETURN:
                out += "\n"
            else:
                out += k[0]
        return out
    e1 = El("Customer called, OK to wait")
    A.write_seller_note(Drv(e1), "113-1", note_text="C none", skip_navigate=True, append=True)
    check("existing note kept, the line added under it", typed_text(e1) == "Customer called, OK to wait\nC none", typed_text(e1))
    e2 = El("C none")
    ok = A.write_seller_note(Drv(e2), "113-1", note_text="C none", skip_navigate=True, append=True)
    check("already there → nothing typed", ok and not e2.typed)
    e3 = El("old text")
    A.write_seller_note(Drv(e3), "113-1", note_text="new", skip_navigate=True)
    check("without append it still replaces (every other caller unchanged)", typed_text(e3) == "new", typed_text(e3))
finally:
    A._confirm_on_order_page = _conf

print("\nwiring in process_order")
src = open(os.path.join(AUTO, "engine", "orders.py"), encoding="utf-8").read()
body = src[src.index("def process_order("):src.index("#  COUPON SHEET LOADER")]
dup = body[body.index('dup_label = _fit_seller_label(existing_wb, " - D")'):][:500]
check("the Gmail-duplicate '- D' writes the coupon line right after the label",
      "update_amazon_seller_id(driver, amazon_id, dup_label" in dup and "_dup_coupon_note(driver, amazon_id," in dup)
check("…and only there (an order the lock knows was already bought cannot be overridden)",
      body.count("_dup_coupon_note(") == 1)
check("the tag's code is read only for a Manual-C/-R order",
      '_manual_code = _parse_manual_code(merchant_id) if manual_override else ""' in body)
check("single item: swapped before the order fields, the dates and the cart are built",
      body.index("_swap_manual_code(resolved_items[0], _manual_code)") < body.index("primary_item = resolved_items[0]"))
mp = body.index("_manual_multi_item_plan(")
check("multi-item plan: after the browser is on zoro.com, before the delivery date, the cart and the lock",
      body.index("ensure_token(driver)") < mp < body.index("_pcart = False") < body.index("first_cart_item = True")
      < body.index("plock.reserve(amazon_id"))
check("…only for a Manual-C/-R order with several items", "if manual_override and len(resolved_items) > 1:" in body)
blk = body[mp:mp + 2200]
check("…a code that cannot be placed → not placed, logged, one alert, tag kept",
      '"MANUAL_CODE_UNCLEAR"' in blk and "_notify_manual_code_unclear(" in blk and 'return "skipped"' in blk)
check("…dropped items: OOS in the price tracker, PARTIAL in the order note, fields rebuilt",
      'status="OOS"' in blk and "PARTIAL:" in blk and "_order_fields(resolved_items)" in blk)
check("Seller Notes Code line says ALT / MANUAL / PARTIAL", body.count("+ _code_suffix,") == 3 and '" - PARTIAL", _partial' in body)
disc = src[src.index("for fo in fav_orders:"):][:1200]
check("a tagged order the 'Manual-C' search misses is picked up from the buyer-action view",
      "_parse_manual_tag(" in disc and "_manual_tag_is_for_profile(_fk, PROFILE_NAME)" in disc)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
