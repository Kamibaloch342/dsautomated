#!/usr/bin/env python3
"""
test_cancel_requested.py — an order whose buyer asked to cancel is never bought (owner, 30 Oct 2026).

Seller Central shows "Buyer requested cancel — The buyer has requested that this order be canceled." on
an order that is still Unshipped (112-4663939-9265025). The placer's status check only knew 'order
cancelled' / 'buyer cancellation' / 'cancellation reason', so such orders were placed.

The banner script itself was checked in a real browser on 30 Oct 2026: plain text, Katal attributes only,
and shadow-root text → detected; an ordinary page and "Cancellation reason: …" → not.

Run:  python scripts/test_cancel_requested.py   (no Chrome, no network)
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


class Drv:
    """Answers the status check with '' and the banner check with *banner*."""

    def __init__(self, banner):
        self.banner = banner

    def execute_script(self, script, *a):
        if script == O._CANCEL_REQUESTED_JS:
            if isinstance(self.banner, Exception):
                raise self.banner
            return self.banner
        return ""


print("\nthe banner")
check("banner shown → True", O._buyer_requested_cancel(Drv(True)) is True)
check("no banner → False", O._buyer_requested_cancel(Drv(False)) is False)
check("a page error never raises (→ False)", O._buyer_requested_cancel(Drv(RuntimeError("tab gone"))) is False)
js = O._CANCEL_REQUESTED_JS
check("reads the page text, the alert attributes and shadow roots",
      "innerText" in js and "getAttribute('header')" in js and "getAttribute('description')" in js and "shadowRoot" in js)
check("matches Amazon's two sentences",
      "buyer requested cancel" in js and "requested that this order be cancel" in js)

print("\nthe order page read")
d = O._extract_order_details(Drv(True), "112-4663939-9265025")
check("a cancel request stops the read at once: status 'cancel_requested', no items",
      d == {"amazon_id": "112-4663939-9265025", "status": "cancel_requested"}, str(d))
src = open(os.path.join(AUTO, "engine", "orders.py"), encoding="utf-8").read()
ext = src[src.index("def _extract_order_details("):src.index("#  STEP 3: LOAD SKU MAP")]
check("checked twice: on arrival, and again after the address is read (late-drawn banner)",
      ext.count("if _buyer_requested_cancel(driver):") == 2
      and ext.index("scrape_amazon_address(") < ext.rindex("if _buyer_requested_cancel(driver):"))

print("\nboth placer paths skip it")
n = src.count('if detail_status == "cancel_requested":\n')
check("classic and rush loops both handle 'cancel_requested' before anything else", n == 2, str(n))
for at in [i for i in range(len(src)) if src.startswith('if detail_status == "cancel_requested":', i)]:
    blk = src[at:at + 300]
    check("…logs, alerts and skips (never placed)", "_cancel_requested_skip(amazon_id, asin, sku)" in blk and "continue" in blk)

print("\nthe alert")
sent, logged = [], []
import utils.whatsapp as W  # noqa: E402
import utils.telegram as T  # noqa: E402
_w, _t, _l = W.send_whatsapp_message, T.send_telegram_message, O._log_order
W.send_whatsapp_message = lambda text, *a, **k: sent.append(("wa", text)) or True
T.send_telegram_message = lambda text, *a, **k: sent.append(("tg", text)) or True
O._log_order = lambda d: logged.append(d)
try:
    O._CANCEL_REQ_ALERTED.discard("112-4663939-9265025")
    for _ in range(3):
        O._cancel_requested_skip("112-4663939-9265025", "B0GTG8N67M", "G416487430")
finally:
    W.send_whatsapp_message, T.send_telegram_message, O._log_order = _w, _t, _l
check("one WhatsApp + one Telegram per order per run, saying to cancel it",
      [k for k, _ in sent] == ["wa", "tg"] and all("Cancel it in Seller Central" in t for _, t in sent), str(sent))
check("logged CANCEL_REQUESTED every time it is seen",
      len(logged) == 3 and all(x["status"] == "CANCEL_REQUESTED" for x in logged))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
