#!/usr/bin/env python3
"""
test_label_filter.py — a label search only hands the placer orders that carry the label.

12 Oct 2026, 20:55: Seller Central's 'MR' search dropped the query and returned all 1,004
unshipped orders; HS_COUPON set out to work every one of them as its own. _only_labelled keeps
an order only when its Seller order ID IS the label, like the Manual-C/-R pass already did.
The attach-to-running-Chrome path also gets the same page-load strategy as a managed Chrome.

Run:  python scripts/test_label_filter.py   (no Chrome, no network, no orders)
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

rows = [{"amazon_id": "1", "seller_order_id": "MR"}, {"amazon_id": "2", "seller_order_id": " mr "},
        {"amazon_id": "3", "seller_order_id": ""}, {"amazon_id": "4", "seller_order_id": "WB1234567890"},
        {"amazon_id": "5", "seller_order_id": "Rajar"}, {"amazon_id": "6", "seller_order_id": "MR-old"},
        {"amazon_id": "7"}]

print("\nlabel filter")
kept = O._only_labelled(rows, "MR")
check("only rows labelled MR are kept (case/space ignored)", [r["amazon_id"] for r in kept] == ["1", "2"],
      str([r["amazon_id"] for r in kept]))
check("unlabelled, WB, other-label and near-miss rows are dropped",
      not any(r["amazon_id"] in ("3", "4", "5", "6", "7") for r in kept))
check("a search that returned everything keeps nothing that is not ours",
      O._only_labelled([{"amazon_id": str(i), "seller_order_id": ""} for i in range(1004)], "MR") == [])
check("multi-word label (AB ECOM) matches exactly",
      [r["amazon_id"] for r in O._only_labelled([{"amazon_id": "a", "seller_order_id": "AB ECOM"},
                                                 {"amazon_id": "b", "seller_order_id": "AB"}], "AB ECOM")] == ["a"])
check("no label configured → nothing filtered", O._only_labelled(rows, "") == rows)

print("\nattach path")
src = open(os.path.join(AUTO, "engine", "orders.py"), encoding="utf-8").read()
seg = src[src.index("def connect_chrome"):src.index("BROWSER_RECYCLE_INTERVAL")]
check("attaching to a running Chrome sets the page-load strategy", "opts.page_load_strategy = page_load_strategy(" in seg)
from utils.helpers import page_load_strategy  # noqa: E402
check("…which is 'eager' for placers", page_load_strategy("orders") in ("eager", "normal") and
      (os.environ.get("ZUHRA_PAGE_LOAD_STRATEGY") or page_load_strategy("orders") == "eager"))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
