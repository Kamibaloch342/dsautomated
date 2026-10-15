#!/usr/bin/env python3
"""
test_alert_format.py — the order layout the owner LOCKED on 15 Oct 2026, for every order alert
(WhatsApp, Telegram) and the Amazon seller note. If this test fails, the layout changed: change it
only with the owner's say-so, and update the expected text here together with it.

Run:  python scripts/test_alert_format.py   (no network, nothing is sent)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"\n{detail}" if detail and not ok else ""))


from utils.telegram import order_card, format_boss_note_text  # noqa: E402
from utils.seller_notes import format_seller_note  # noqa: E402

ORDER = dict(
    amazon_id="114-5760875-7672210", wb_id="WB5894251624",
    title='Flat Corrugated Cardboard Shipping Boxes 20"L x 20"W x 4"H - Single-Wall Medium Kraft Boxes for Moving',
    sku="Hath_G1606970", asin="B0GD8Y19SD", code="G1606970", qty=1,
    tc=29.30, tax=0.0, earnings=44.62, pm=15.32, pp=52.3, coupon_code="", shipping_amt=13.0,
    ship_by_date="Thu, Oct 15, 2026 PDT", deliver_by_date="Sat, Oct 17, 2026 PDT",
    ship_service="Second Day", zoro_deliver_by="2026-10-19")

# the owner's sample, verbatim (its curly ” is the straight " of the real title)
WA = """114-5760875-7672210 { WB5894251624 }
Flat Corrugated Cardboard Shipping Boxes 20"

*SKU*: Hath_G1606970 | *ASIN*: B0GD8Y19SD
*Zoro Code:* K1606970 (Qty: 1)

*Zoro_Cost*: $29.30 (Tax $0.00) |
*Amz_Earnings:* $44.62 (*Shipping*: $13.00)
*Profit:* $15.32 (52.3%) | Coupon: None

*Amazon:* [Ship by Oct 15 | Deliver by Oct 17 ] {*Second Day*}
*Zoro_Delivery*: Oct 19"""

print("\nWhatsApp")
got = format_boss_note_text(ORDER, markup="wa")
check("WhatsApp body is exactly the owner's sample", got == WA, f"--- got ---\n{got}\n--- want ---\n{WA}")
check("WhatsApp header bolds the whole line, emoji included",
      'wa_header = f"*🟢 PROFIT ORDER (+{pp_display})*"' in open(os.path.join(os.path.dirname(HERE), "utils", "whatsapp.py"),
                                                              encoding="utf-8").read())

print("\nTelegram (copy block — no asterisks)")
tg = format_boss_note_text(ORDER)
check("same layout, no asterisks, fast service in capitals",
      tg == WA.replace("*", "").replace("{Second Day}", "{SECOND DAY}"), tg)

print("\nvariants")
std = order_card(dict(ORDER, ship_service="Standard", shipping_amt=None, coupon_code="EMZMPWFDFWLT", coupon_off=17.0), markup="wa")
check("Standard service is not bold", "{Standard}" in std and "{*Standard*}" not in std, std)
check("no customer shipping → no '(Shipping: …)'", "(Shipping:" not in std, std)
check("coupon shows code and saving", "Coupon: EMZMPWFDFWLT (-$17.00)" in std, std)
miss = order_card(dict(ORDER, ship_by_date="", deliver_by_date="", ship_service="", zoro_deliver_by=""), markup="wa")
check("missing dates keep the layout ('—')", "*Amazon:* [Ship by — | Deliver by — ] {—}\n*Zoro_Delivery*: —" in miss, miss)
alt = order_card(dict(ORDER, alt_used=True), markup="wa")
check("ALT order marks the code", "K1606970 - ALT (Qty: 1)" in alt, alt)

print("\nseller note — stays the OLD format (owner, 15 Oct 2026: only the alerts change)")
note = format_seller_note(dict(ORDER, tc=29.30, earnings=44.62, pm=15.32))
check("seller note is the old line-per-field note, not the alert layout",
      "Zoro_Cost" not in note and "Amz_Earnings" not in note and "ASIN: B0GD8Y19SD" in note and "TC: $29.30" in note, note)
check("…within its own 230-character cap", len(note) <= 230, f"{len(note)}")

sg = order_card(dict(ORDER, title="Simple Green, SMP13006, Industrial Cleaner/Degreaser, 1 / Each, Dark Green, 5 Gallons"), markup="wa")
check("a title with commas is not cut at the first comma", "\nSimple Green, SMP13006, Industrial Cleaner/D\n" in sg, sg)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
