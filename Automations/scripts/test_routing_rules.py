#!/usr/bin/env python3
"""
test_routing_rules.py — unit tests for the placer's routing rules WITHOUT
importing engine/orders.py as a placer (importing it selects a profile). Like
test_checkout_kwargs_compat.py, the functions under test are lifted from the
source with `ast` and executed in a stub namespace, so nothing here touches
Amazon, Zoro, Google Sheets or the live DB. checkout.true_api's real
classify_promo_error is imported (pure function).

Covers:
  • classify_promo_error       — Zoro's real rejection texts → item / min_order / dead / token / transient
  • _deciding_verdict          — ≥ $50 → coupon; < $50 & margin < 5% → coupon; else stay; Select/ineligible never route
  • _supervise_coupon_decision — PDP badge → reseller; priority coupon → place (Select too);
                                 Select w/o priority → reseller (no regular test); ≥ $50 or thin margin → regular;
                                 all item-rejected → PROMO_INELIGIBLE; token/transient → retry; min-order ≠ rejection
  • _learn_promo_outcome       — strikes ≥ 10 min apart, ineligible at N, cleared on success, pdp source
  • _refresh_promo_ineligible / _is_promo_ineligible — config + sheet + learned merge
  • _route_to_sibling          — sibling label per store / kind
  • _sort_orders_for_processing — supervised orders always run last

Run:  python scripts/test_routing_rules.py
"""
import ast
import json
import os
import sys
import tempfile
import threading
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
SRC = os.path.join(AUTO, "engine", "orders.py")
sys.path.insert(0, AUTO)
from checkout.true_api import classify_promo_error  # noqa: E402  (pure function)

WANT = ["_deciding_verdict", "_supervise_coupon_decision", "_learn_promo_outcome",
        "_refresh_promo_ineligible", "_is_promo_ineligible", "_route_to_sibling",
        "_sort_orders_for_processing", "_load_coupon_intel", "_save_coupon_intel",
        "_append_asin_to_sheet_column"]

ITEM = "{'cartErrors': [{'detail': {'error': 'Promo code is not valid for the item(s) in your cart.'}, 'status': 400}]}"
CAT = "{'cartErrors': [{'detail': {'error': 'To qualify for this 15% off coupon, your order needs to include at least one eligible Electrical Supplies item.'}}]}"
MIN50 = "{'cartErrors': [{'detail': {'error': 'To qualify for this coupon, your order needs to include at least $50 of eligible items.'}}]}"
DEAD = "{'cartErrors': [{'detail': {'error': 'Promo code is no longer available.'}, 'status': 400}]}"
TOKEN = "{'message': 'Invalid Bearer Token'}"
CONFLICT = "conflict while modifying resource, try again"


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
        self.coupon_calls = []
        self.verdicts = []

    def update_amazon_seller_id(self, driver, amazon_id, value, profile, **kw):
        self.labels.append((amazon_id, value))
        return True

    def log_order(self, d):
        self.logs.append(d)

    def apply_coupon(self, driver, cart_id, code):
        self.coupon_calls.append(code)
        return self.coupon_results.get(code, (False, ITEM))

    def record_verdict(self, asin, verdict, cart_text="", coupon=""):
        self.verdicts.append((asin, verdict, coupon))


def _note_reject_stub(code, result, cache=None):
    kind, extra = classify_promo_error(result)
    if kind == "min_order" and cache is not None and extra:
        cache.setdefault("coupon_min_order", {})[code] = extra
    return kind


def make_ns(rec, tmpdir, profile="HS_COUPON", routing=None):
    ns = {
        "os": os, "json": json, "time": time, "sys": sys, "Counter": Counter, "threading": threading,
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
        "classify_promo_error": classify_promo_error,
        "_note_reject": _note_reject_stub,
        "_record_asin_verdict": rec.record_verdict,
        "pdp_promo_ineligible": lambda sku: None,
        "check_pdp_promo": lambda d, sku: None,
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

    # ── classify_promo_error (real Zoro texts from the logs) ─────────────
    check("classify: 'not valid for the item(s)' → item", classify_promo_error(ITEM)[0] == "item")
    check("classify: category minimum ('at least one eligible … item') → item", classify_promo_error(CAT)[0] == "item")
    check("classify: '$50 of eligible' → min_order 50", classify_promo_error(MIN50) == ("min_order", 50.0))
    check("classify: 'no longer available' → dead", classify_promo_error(DEAD)[0] == "dead")
    check("classify: 'Invalid Promo Code' → dead (not item)", classify_promo_error("{'title': 'Invalid Promo Code', 'type': 'invalidPromoCode'}")[0] == "dead")
    check("classify: 'Invalid Bearer Token' → token (was 'invalid for item')", classify_promo_error(TOKEN)[0] == "token")
    check("classify: no_token / Jwt expired → token", classify_promo_error("no_token")[0] == "token" and classify_promo_error("{'detail': {'message': 'Jwt is expired'}}")[0] == "token")
    check("classify: conflict / failed to fetch / 502 → transient",
          classify_promo_error(CONFLICT)[0] == "transient" and classify_promo_error("Failed to fetch")[0] == "transient"
          and classify_promo_error("{'message': 'Bad Gateway', 'status': '502'}")[0] == "transient")

    # ── _deciding_verdict ────────────────────────────────────────────────
    dv = ns["_deciding_verdict"]
    v = dv(60.0, 54.0, 70.0, promo_bad=False, all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    check("deciding: cart ≥ $50 → coupon profile", v["route"] is True)
    v = dv(40.0, 40.0, 41.0, promo_bad=False, all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    check("deciding: cart < $50 but reseller margin < 5% → coupon profile", v["route"] is True and v["est_margin"] < 0.05)
    v = dv(40.0, 40.0, 60.0, promo_bad=False, all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    check("deciding: cart < $50 and reseller margin ≥ 5% → stays reseller", v["route"] is False)
    v = dv(40.0, 40.0, None, promo_bad=False, all_zoro_select=False, small_fee=5.99, free_threshold=50.0)
    check("deciding: earnings unknown → stays reseller", v["route"] is False and v["est_margin"] is None)
    check("deciding: promo-ineligible never routes", dv(80.0, 72.0, 90.0, True, False, 5.99, 50.0)["route"] is False)
    check("deciding: Zoro Select never routes", dv(80.0, 72.0, 90.0, False, True, 5.99, 50.0)["route"] is False)

    # ── _supervise_coupon_decision ───────────────────────────────────────
    sup = ns["_supervise_coupon_decision"]
    items = [{"sku": "G111", "asin": "B0SUPER0001"}]
    pri = [{"code": "PRI20", "pct": 20}, {"code": "PRI15", "pct": 15}]
    reg = ["REG1", "REG2"]
    cache = ns["_load_coupon_intel"]()

    rec.coupon_calls.clear()
    d = sup(None, items, 30.0, pri, reg, cache, pdp_ineligible=True)
    check("supervisor: PDP badge → reseller before any coupon call",
          d["action"] == "reseller" and not rec.coupon_calls and d["verdict"]["status"] == "PROMO_INELIGIBLE")

    rec.coupon_results = {"PRI20": (False, ITEM), "PRI15": (True, 4.2)}
    d = sup(None, items, 30.0, pri, reg, cache, all_zoro_select=True)
    check("supervisor: Zoro Select + priority coupon works → place (15% beats reseller 10%)",
          d["action"] == "place" and d["coupon"] == "PRI15" and d["verdict"]["suggest"] == "Coupon")

    rec.coupon_results = {"PRI20": (False, ITEM), "PRI15": (False, ITEM), "REG1": (True, 5.0)}
    rec.coupon_calls.clear()
    d = sup(None, items, 80.0, pri, reg, cache, all_zoro_select=True)
    check("supervisor: Zoro Select, no priority coupon → reseller, regular NOT tested even at $80",
          d["action"] == "reseller" and "REG1" not in rec.coupon_calls and d["verdict"]["status"] == "SELECT")

    d = sup(None, items, 75.0, pri, reg, cache, unit_price=75.0)
    check("supervisor: no priority, cart ≥ $50, regular works → place; ASIN → Coupon column",
          d["action"] == "place" and d["tier"] == "regular" and d["verdict"]["suggest"] == "Coupon")

    d = sup(None, items, 30.0, pri, reg, cache, reseller_est_margin=0.30, unit_price=30.0)
    check("supervisor: cart < $50, reseller margin healthy → reseller; ASIN → Deciding column",
          d["action"] == "reseller" and d["verdict"]["status"] == "NO_PRIORITY" and d["verdict"]["suggest"] == "Deciding")

    d = sup(None, items, 30.0, pri, reg, cache, reseller_est_margin=0.02, unit_price=30.0)
    check("supervisor: cart < $50 but reseller margin < 5% → regular coupon → place (avoid loss)",
          d["action"] == "place" and d["coupon"] == "REG1" and "margin" in d["reason"])

    rec.coupon_results = {"PRI20": (False, ITEM), "PRI15": (False, ITEM), "REG1": (False, ITEM), "REG2": (False, CAT)}
    cache = ns["_load_coupon_intel"](); ns["PROMO_INELIGIBLE_CODES"].clear()
    d = sup(None, items, 75.0, pri, reg, cache, unit_price=75.0)
    check("supervisor: ≥ $50, every available coupon rejected → WAIT (not reseller), one strike, not yet ineligible",
          d["action"] == "wait" and d["verdict"]["suggest"] == "Coupon"
          and cache["promo_ineligible"].get("G111", {}).get("fails") == 1
          and not cache["promo_ineligible"].get("G111", {}).get("ineligible"))

    rec.coupon_results = {"PRI20": (False, ITEM), "PRI15": (False, ITEM)}
    rec.coupon_calls.clear()
    items_c = [{"sku": "G777", "asin": "B0SUPER0003"}]
    d = sup(None, items_c, 75.0, pri, [], cache, unit_price=75.0)         # 30 coupons, 40 orders: none left
    check("supervisor: ≥ $50, NO regular coupon left today → WAIT for tomorrow's coupons, no strike, ASIN → Coupon column",
          d["action"] == "wait" and "no regular coupon is left" in d["reason"]
          and d["verdict"]["suggest"] == "Coupon" and not cache["promo_ineligible"].get("G777"))

    d = sup(None, items_c, 30.0, pri, [], cache, reseller_est_margin=0.02, unit_price=30.0)
    check("supervisor: < $50, thin margin, no regular coupon left → WAIT (not a full-price reseller loss)",
          d["action"] == "wait" and d["verdict"]["suggest"] == "Deciding")

    rec.coupon_results = {"PRI20": (False, MIN50), "PRI15": (False, ITEM), "REG1": (True, 3.0)}
    cache = ns["_load_coupon_intel"]()
    items_b = [{"sku": "G555", "asin": "B0SUPER0002"}]      # fresh SKU: G111 is on disk as ineligible now
    d = sup(None, items_b, 75.0, pri, reg, cache, unit_price=75.0)
    check("supervisor: '$50 minimum' reply is not a rejection (min recorded), regular still tried",
          d["action"] == "place" and cache.get("coupon_min_order", {}).get("PRI20") == 50.0
          and not cache["promo_ineligible"].get("G555"))

    rec.coupon_results = {"PRI20": (False, TOKEN), "PRI15": (False, CONFLICT)}
    d = sup(None, items, 30.0, pri, reg, cache)
    check("supervisor: token/transient on every attempt → retry, no label write", d["action"] == "retry")

    rec.coupon_results = {"PRI20": (True, 0)}
    d = sup(None, items, 30.0, [{"code": "PRI20", "pct": 20}], [], cache, reseller_est_margin=0.3)
    check("supervisor: coupon 'applied' with $0 discount does not count", d["action"] == "reseller")

    # ── _learn_promo_outcome ─────────────────────────────────────────────
    ns["PROMO_INELIGIBLE_CODES"].clear(); ns["PROMO_INELIGIBLE_ASINS"].clear()
    cache = ns["_load_coupon_intel"]()
    learn = ns["_learn_promo_outcome"]
    learn("G222", "B0LEARN0001", False, cache=cache)
    learn("G222", "B0LEARN0001", False, cache=cache)
    cache["promo_ineligible"]["G222"]["last"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3600 * 5))
    learn("G222", "B0LEARN0001", False, cache=cache)
    check("learn: more strikes the same day are not counted (one per 24 h)", cache["promo_ineligible"]["G222"]["fails"] == 1)
    cache["promo_ineligible"]["G222"]["last"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3600 * 25))
    learn("G222", "B0LEARN0001", False, cache=cache)
    check("learn: a strike on another day → 2/2 → PROMO-INELIGIBLE",
          cache["promo_ineligible"]["G222"]["ineligible"] is True and "G222" in ns["PROMO_INELIGIBLE_CODES"])
    saved = json.load(open(ns["_COUPON_INTEL_PATH"]))
    check("learn: persisted with working_pairs + coupon_min_order intact",
          saved["promo_ineligible"]["G222"]["ineligible"] is True and "working_pairs" in saved and "coupon_min_order" in saved)
    learn("G222", "B0LEARN0001", True, cache=cache)
    check("learn: a working coupon clears the code", "G222" not in cache["promo_ineligible"] and "G222" not in ns["PROMO_INELIGIBLE_CODES"])
    learn("G333", "B0LEARN0002", False, cache=cache, definitive=True, source="pdp")
    check("learn: PDP badge → ineligible at once, source recorded",
          cache["promo_ineligible"]["G333"]["ineligible"] is True and cache["promo_ineligible"]["G333"]["source"] == "pdp")
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
    check("is_promo_ineligible: by ASIN / by code / by main_sku / clean",
          isb([{"asin": "b0cfgbad01", "sku": "G999"}]) and isb([{"asin": "B0OK000001", "sku": "G600"}])
          and isb([{"asin": "B0OK000001", "sku": "G777", "main_sku": "G333"}]) and not isb([{"asin": "B0OK000001", "sku": "G700"}]))

    # ── _route_to_sibling ────────────────────────────────────────────────
    rec.labels.clear(); rec.logs.clear()
    ns["_route_to_sibling"](None, "111-000", "reseller", "test", {"asin": "B0X", "order_note": ""})
    check("route: HS_COUPON → reseller writes 'Rajar' + ROUTED_TO_RESELLER",
          rec.labels[-1] == ("111-000", "Rajar") and rec.logs[-1]["status"] == "ROUTED_TO_RESELLER")
    ns_dm = make_ns(rec, tmp, profile="DM_RESELLER")
    ns_dm["_route_to_sibling"](None, "111-001", "coupon", "test", {"asin": "B0X"})
    check("route: DM_RESELLER → coupon writes 'MR' + ROUTED_TO_COUPON",
          rec.labels[-1] == ("111-001", "MR") and rec.logs[-1]["status"] == "ROUTED_TO_COUPON")
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
    check("sort: priority first, supervised strictly last", ids[0] == "p1" and ids[-2:] == ["s1", "s2"], str(ids))

    # ── static guard: no function-local import may shadow a name read earlier ──
    # (a `from x import NAME` anywhere inside a function makes NAME local to the WHOLE
    #  function; reading it before that line raises UnboundLocalError at runtime —
    #  this took every placer down on 7 Sep 2026)
    src = open(SRC, encoding="utf-8").read()
    tree = ast.parse(src)
    problems = []

    def scan(fn):
        imported = {}   # name -> lineno of the (first) in-function import
        loads = []      # (name, lineno)

        def walk(node):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                    continue                    # nested scope: its own rules
                if isinstance(child, ast.ImportFrom):
                    for a in child.names:
                        nm = a.asname or a.name
                        imported.setdefault(nm, child.lineno)
                elif isinstance(child, ast.Import):
                    for a in child.names:
                        nm = a.asname or a.name.split(".")[0]
                        imported.setdefault(nm, child.lineno)
                elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                    loads.append((child.id, child.lineno))
                walk(child)
        walk(fn)
        for nm, ln in loads:
            if nm in imported and ln < imported[nm]:
                problems.append(f"{fn.name}: '{nm}' read at line {ln} but imported locally at line {imported[nm]}")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan(node)
    check("static: no function-local import shadows a name that is read earlier in the same function",
          not problems, "; ".join(problems[:5]))

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
