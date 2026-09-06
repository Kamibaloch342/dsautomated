#!/usr/bin/env python3
"""
test_routing_rules.py — unit tests for the placer's routing rules WITHOUT
importing engine/orders.py (importing it selects a profile and takes the
per-profile lock). Like test_checkout_kwargs_compat.py, the functions under
test are lifted from the source with `ast` and executed in a stub namespace,
so nothing here touches Amazon, Zoro, Google Sheets or the live DB.

Covers:
  • _deciding_verdict           — ≥ $50 → coupon; < $50 & margin < 5% → coupon; else stay;
                                  promo-ineligible / Zoro Select never route
  • _supervise_coupon_decision  — priority coupon → place; regular only if cart ≥ $50;
                                  otherwise reseller; all-transient → retry
  • _learn_promo_outcome        — strikes ≥ 10 min apart, ineligible at N, cleared on success
  • _refresh_promo_ineligible / _is_promo_ineligible — config + sheet + learned merge
  • _route_to_sibling           — sibling label per store / kind
  • _sort_orders_for_processing — supervised orders always run last

Run:  python scripts/test_routing_rules.py
"""
import ast
import json
import os
import sys
import tempfile
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
SRC = os.path.join(AUTO, "engine", "orders.py")

WANT = ["_deciding_verdict", "_supervise_coupon_decision", "_learn_promo_outcome",
        "_refresh_promo_ineligible", "_is_promo_ineligible", "_route_to_sibling",
        "_sort_orders_for_processing", "_load_coupon_intel", "_save_coupon_intel",
        "_append_asin_to_sheet_column"]


def load_functions():
    src = open(SRC, encoding="utf-8").read()
    tree = ast.parse(src)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in WANT:
            found[node.name] = ast.get_source_segment(src, node)
    missing = [w for w in WANT if w not in found]
    assert not missing, f"functions not found in orders.py: {missing}"
    return found


class Recorder:
    def __init__(self):
        self.labels = []
        self.logs = []
        self.coupon_results = {}          # code -> (ok, result)

    def update_amazon_seller_id(self, driver, amazon_id, value, profile, **kw):
        self.labels.append((amazon_id, value))
        return True

    def log_order(self, d):
        self.logs.append(d)

    def apply_coupon(self, driver, cart_id, code):
        return self.coupon_results.get(code, (False, "coupon not valid for item"))


def make_ns(rec, tmpdir, profile="HS_COUPON", routing=None):
    ns = {
        "os": os, "json": json, "time": time, "sys": sys, "Counter": Counter,
        "print": lambda *a, **k: None,
        "PROFILE_NAME": profile,
        "PROFILES": {"HS_RESELLER": {"target_label": "Rajar"}, "HS_COUPON": {"target_label": "MR"},
                     "DM_RESELLER": {"target_label": "AB ECOM"}, "DM_COUPON": {"target_label": "MR"}},
        "ROUTING": routing if routing is not None else {"auto_learn_promo_ineligible": True,
                                                        "promo_ineligible_min_fails": 2,
                                                        "promo_ineligible_asins": ["B0CFGBAD01"]},
        "DECIDING_COUPON_MIN": 50.0, "DECIDING_MIN_MARGIN": 0.05, "SUPERVISE_MIN_CART": 50.0,
        "PROMO_INELIGIBLE_ASINS": set(), "PROMO_INELIGIBLE_CODES": set(),
        "PRIORITY_ASINS": ["B0PRIORITY1"],
        "_COUPON_INTEL_PATH": os.path.join(tmpdir, "coupon_intel.json"),
        "_load_asin_column_from_sheet": lambda col, what: (["B0SHEETBAD1"] if col == "Promo_Ineligible" else []),
        "_get_sheets_write_service": lambda: (_ for _ in ()).throw(RuntimeError("no sheets in tests")),
        "GOOGLE_SHEET_ID": "sheet", "GOOGLE_SHEET_PRIORITY_ASIN_TAB": "Priority_ASIN_Control",
        "update_amazon_seller_id": rec.update_amazon_seller_id,
        "_log_order": rec.log_order,
        "_get_cart_id": lambda driver: "cart-1",
        "_apply_coupon_api": rec.apply_coupon,
        "_is_transient_coupon_error": lambda e: any(s in str(e).lower() for s in ("try again", "timed out", "conflict")),
    }
    for name, code in load_functions().items():
        exec(code, ns)
    return ns


PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


def main():
    tmp = tempfile.mkdtemp()
    rec = Recorder()
    ns = make_ns(rec, tmp)

    # ── _deciding_verdict ────────────────────────────────────────────────
    dv = ns["_deciding_verdict"]
    v = dv(60.0, 54.0, 70.0, promo_bad=False, all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    check("deciding: cart ≥ $50 → coupon profile", v["route"] is True and "≥" in v["reason"])
    v = dv(40.0, 40.0, 41.0, promo_bad=False, all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    # est cost 45.99, margin (41-45.99)/45.99 = -10.8% < 5% → coupon
    check("deciding: cart < $50 but reseller margin < 5% → coupon profile", v["route"] is True and v["est_margin"] < 0.05)
    v = dv(40.0, 40.0, 60.0, promo_bad=False, all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    # est cost 45.99, margin 30% → stay
    check("deciding: cart < $50 and reseller margin ≥ 5% → stays reseller", v["route"] is False and v["est_margin"] > 0.05)
    v = dv(40.0, 40.0, None, promo_bad=False, all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    check("deciding: cart < $50, earnings unknown → stays reseller", v["route"] is False and v["est_margin"] is None)
    v = dv(80.0, 72.0, 90.0, promo_bad=True, all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    check("deciding: promo-ineligible never routes (even ≥ $50)", v["route"] is False)
    v = dv(80.0, 72.0, 90.0, promo_bad=False, all_zoro_select=True, small_fee=5.99, free_threshold=50.0)
    check("deciding: Zoro Select never routes", v["route"] is False)
    v = dv(20.0, 20.0, 20.5, promo_bad=False, all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    check("deciding: thin order below threshold → coupon", v["route"] is True)

    # ── _supervise_coupon_decision ───────────────────────────────────────
    sup = ns["_supervise_coupon_decision"]
    items = [{"sku": "G111", "asin": "B0SUPER0001"}]
    pri = [{"code": "PRI20", "pct": 20}, {"code": "PRI10", "pct": 10}]
    reg = ["REG1", "REG2"]
    cache = ns["_load_coupon_intel"]()

    rec.coupon_results = {"PRI20": (False, "not valid"), "PRI10": (True, 4.2)}
    d = sup(None, items, 30.0, pri, reg, cache)
    check("supervisor: priority coupon applies → place (even cart < $50)", d["action"] == "place" and d["coupon"] == "PRI10" and d["tier"] == "priority")

    rec.coupon_results = {"PRI20": (False, "not valid"), "PRI10": (False, "invalid"), "REG1": (True, 5.0)}
    d = sup(None, items, 30.0, pri, reg, cache)
    check("supervisor: no priority, cart < $50 → reseller (regular not even tried)", d["action"] == "reseller" and "< $50" in d["reason"])
    d = sup(None, items, 75.0, pri, reg, cache)
    check("supervisor: no priority, cart ≥ $50, regular works → place with regular", d["action"] == "place" and d["coupon"] == "REG1" and d["tier"] == "regular")

    rec.coupon_results = {"PRI20": (False, "not valid"), "PRI10": (False, "invalid"),
                          "REG1": (False, "invalid"), "REG2": (False, "not valid")}
    d = sup(None, items, 75.0, pri, reg, cache)
    check("supervisor: nothing applies at ≥ $50 → reseller", d["action"] == "reseller")

    rec.coupon_results = {"PRI20": (False, "conflict while modifying resource, try again"),
                          "PRI10": (False, "timed out")}
    d = sup(None, items, 30.0, pri, reg, cache)
    check("supervisor: all attempts transient → retry, no label write", d["action"] == "retry")

    rec.coupon_results = {"PRI20": (True, 0)}          # API ok but no discount → not a success
    d = sup(None, items, 30.0, [{"code": "PRI20", "pct": 20}], [], cache)
    check("supervisor: coupon 'applied' with $0 discount does not count", d["action"] == "reseller")

    # ── _learn_promo_outcome ─────────────────────────────────────────────
    ns["PROMO_INELIGIBLE_CODES"].clear(); ns["PROMO_INELIGIBLE_ASINS"].clear()
    cache = ns["_load_coupon_intel"]()
    learn = ns["_learn_promo_outcome"]
    learn("G222", "B0LEARN0001", False, cache=cache)
    learn("G222", "B0LEARN0001", False, cache=cache)          # same 10-minute window → ignored
    check("learn: second strike inside 10 min is not counted", cache["promo_ineligible"]["G222"]["fails"] == 1)
    cache["promo_ineligible"]["G222"]["last"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 1200))
    learn("G222", "B0LEARN0001", False, cache=cache)
    check("learn: strike ≥ 10 min later → 2/2 → PROMO-INELIGIBLE",
          cache["promo_ineligible"]["G222"]["ineligible"] is True and "G222" in ns["PROMO_INELIGIBLE_CODES"]
          and "B0LEARN0001" in ns["PROMO_INELIGIBLE_ASINS"])
    saved = json.load(open(ns["_COUPON_INTEL_PATH"]))
    check("learn: persisted to coupon_intel.json with working_pairs intact",
          saved["promo_ineligible"]["G222"]["ineligible"] is True and "working_pairs" in saved and saved["failed_pairs"] == [])
    learn("G222", "B0LEARN0001", True, cache=cache)
    check("learn: a working coupon clears the code", "G222" not in cache["promo_ineligible"] and "G222" not in ns["PROMO_INELIGIBLE_CODES"])
    saved = json.load(open(ns["_COUPON_INTEL_PATH"]))
    check("learn: clearing is persisted", "G222" not in saved.get("promo_ineligible", {}))
    learn("G333", "B0LEARN0002", False, cache=cache, definitive=True)
    check("learn: definitive verdict (priority check) marks ineligible at once", cache["promo_ineligible"]["G333"]["ineligible"] is True)
    ns2 = make_ns(Recorder(), tempfile.mkdtemp(), routing={"auto_learn_promo_ineligible": False})
    c2 = {"promo_ineligible": {}}
    ns2["_learn_promo_outcome"]("G444", "B0X", False, cache=c2, definitive=True)
    check("learn: disabled by config → no memory written", not c2["promo_ineligible"])

    # ── _refresh_promo_ineligible / _is_promo_ineligible ─────────────────
    sku_map = {"B0CFGBAD01": {"zoro_code": "G500"}, "B0SHEETBAD1": {"zoro_code": "G600"}, "B0OK000001": {"zoro_code": "G700"}}
    ns["_refresh_promo_ineligible"](sku_map, cache)
    A, C = ns["PROMO_INELIGIBLE_ASINS"], ns["PROMO_INELIGIBLE_CODES"]
    check("refresh: config ASIN + sheet ASIN + learned code merged",
          {"B0CFGBAD01", "B0SHEETBAD1", "B0LEARN0002"} <= A and {"G500", "G600", "G333"} <= C and "B0OK000001" not in A)
    isb = ns["_is_promo_ineligible"]
    check("is_promo_ineligible: by ASIN", isb([{"asin": "b0cfgbad01", "sku": "G999"}]))
    check("is_promo_ineligible: by Zoro code (alt/2nd item)", isb([{"asin": "B0OK000001", "sku": "G600"}]))
    check("is_promo_ineligible: by main_sku after ALT swap", isb([{"asin": "B0OK000001", "sku": "G777", "main_sku": "G333"}]))
    check("is_promo_ineligible: clean item → False", not isb([{"asin": "B0OK000001", "sku": "G700"}]))

    # ── _route_to_sibling ────────────────────────────────────────────────
    rec.labels.clear(); rec.logs.clear()
    ns["_route_to_sibling"](None, "111-000", "reseller", "test", {"asin": "B0X", "order_note": ""})
    check("route: HS_COUPON → reseller writes 'Rajar' + ROUTED_TO_RESELLER",
          rec.labels[-1] == ("111-000", "Rajar") and rec.logs[-1]["status"] == "ROUTED_TO_RESELLER")
    ns_dm = make_ns(rec, tmp, profile="DM_RESELLER")
    ns_dm["_route_to_sibling"](None, "111-001", "coupon", "test", {"asin": "B0X"})
    check("route: DM_RESELLER → coupon writes 'MR' + ROUTED_TO_COUPON",
          rec.labels[-1] == ("111-001", "MR") and rec.logs[-1]["status"] == "ROUTED_TO_COUPON")
    ns_dm["PROFILES"]["DM_RESELLER"] = {}
    ns_dc = make_ns(rec, tmp, profile="DM_COUPON")
    ns_dc["PROFILES"]["DM_RESELLER"] = {}
    ns_dc["_route_to_sibling"](None, "111-002", "reseller", "test", {})
    check("route: DM_COUPON → reseller defaults to 'AB ECOM' when no target_label", rec.labels[-1] == ("111-002", "AB ECOM"))

    # ── _sort_orders_for_processing ──────────────────────────────────────
    orders = [
        {"amazon_id": "s1", "asin": "B0ZZZZZZZ1", "_supervised": True},
        {"amazon_id": "n1", "asin": "B0NORMAL01"},
        {"amazon_id": "p1", "asin": "B0PRIORITY1"},
        {"amazon_id": "n2", "asin": "B0NORMAL01"},
        {"amazon_id": "s2", "asin": "B0PRIORITY1", "_supervised": True},
        {"amazon_id": "e1", "asin": ""},
    ]
    srt, counts = ns["_sort_orders_for_processing"](orders)
    ids = [o["amazon_id"] for o in srt]
    check("sort: priority first, supervised strictly last (even with a priority ASIN)",
          ids[0] == "p1" and ids[-2:] == ["s1", "s2"] and ids.index("e1") < ids.index("s1"), str(ids))

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
