#!/usr/bin/env python3
"""
analyze_decisions.py — is the supervisor doing the right thing?

Reads the decision log (utils/decision_log.py → table bot_decisions, or the daily
JSONL files with --jsonl) and prints:

  1. volumes            decisions by kind / action / profile
  2. supervisor         actions, which column each ASIN was sent to, wait reasons
  3. coupons            per code: tried / worked / rejected-for-item / cart-minimum / dead
  4. outcomes           what the checkout finally did with supervised & deciding orders
  5. RULE AUDIT         every decision is re-checked against the agreed rules; any
                        violation is listed with the order id so it can be inspected

Usage:
    python scripts/analyze_decisions.py                 # today
    python scripts/analyze_decisions.py --days 7        # last 7 days
    python scripts/analyze_decisions.py --date 2026-09-08
    python scripts/analyze_decisions.py --jsonl         # read the daily file instead of the DB
    python scripts/analyze_decisions.py --show 20       # print the last 20 supervisor decisions in full
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)
from utils import decision_log as dl  # noqa: E402

MIN_CART = 50.0
MIN_MARGIN = 0.05


def _arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


def load() -> list[dict]:
    if "--jsonl" in sys.argv:
        date = _arg("--date") or datetime.now().strftime("%Y-%m-%d")
        return dl.read_jsonl(date)
    days = int(_arg("--days", "1"))
    date = _arg("--date")
    if date:
        rows = dl.read(date=date, limit=100000)
    else:
        since = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d 00:00:00")
        rows = dl.read(since=since, limit=100000)
    out = []
    for r in rows:
        d = dict(r.get("data") or {})
        d.setdefault("ts", r.get("ts")); d.setdefault("kind", r.get("kind"))
        out.append(d)
    return list(reversed(out))          # chronological


def pct(a, b):
    return f"{(100.0 * a / b):.0f}%" if b else "—"


def main():
    recs = load()
    if not recs:
        print("no decisions recorded yet (the coupon profiles write them when they reach the supervised orders)")
        return
    print(f"decisions: {len(recs)}   {recs[0]['ts']} → {recs[-1]['ts']}\n")

    # 1. volumes
    print("── 1. volumes ──")
    print("  by kind   :", dict(Counter(r["kind"] for r in recs)))
    print("  by profile:", dict(Counter(r.get("profile") or "?" for r in recs)))
    sup = [r for r in recs if r["kind"] == "supervisor"]
    dec = [r for r in recs if r["kind"] == "deciding"]
    out = {r.get("amazon_id"): r for r in recs if r["kind"] == "outcome"}

    # 2. supervisor
    print("\n── 2. supervisor ──")
    print("  actions   :", dict(Counter(r.get("action") for r in sup)))
    print("  sent to   :", dict(Counter((r.get("verdict") or {}).get("suggest") for r in sup)))
    print("  tiers used:", dict(Counter(r.get("tier") for r in sup if r.get("action") == "place")))
    waits = Counter((r.get("reason") or "").split(" (")[0][:70] for r in sup if r.get("action") == "wait")
    if waits:
        print("  wait reasons:")
        for k, n in waits.most_common(6):
            print(f"    {n:4}  {k}")
    if sup:
        att = [len(r.get("attempts") or []) for r in sup]
        print(f"  coupon attempts per order: avg {sum(att) / len(att):.1f}, max {max(att)}")

    # 3. coupons
    print("\n── 3. coupons (supervisor attempts) ──")
    stats = defaultdict(lambda: Counter())
    for r in sup:
        for a in r.get("attempts") or []:
            c = stats[(a.get("tier"), a.get("code"))]
            c["tried"] += 1
            c["worked" if a.get("ok") else (a.get("kind") or "other")] += 1
    if stats:
        print(f"  {'tier':8} {'code':16} {'tried':>5} {'worked':>6} {'hit':>5} {'item':>5} {'min$':>5} {'dead':>5} {'busy':>5}")
        for (tier, code), c in sorted(stats.items(), key=lambda kv: (-kv[1]['tried'], kv[0])):
            print(f"  {tier or '?':8} {code or '?':16} {c['tried']:5} {c['worked']:6} {pct(c['worked'], c['tried']):>5} "
                  f"{c['item']:5} {c['min_order']:5} {c['dead']:5} {c['transient'] + c['token']:5}")

    # 4. outcomes
    print("\n── 4. outcomes of supervised / deciding orders ──")
    for label, group in (("supervisor → place", [r for r in sup if r.get("action") == "place"]),
                         ("deciding → place_here", [r for r in dec if r.get("action") == "place_here"])):
        res = Counter((out.get(r.get("amazon_id")) or {}).get("status", "no outcome yet") for r in group)
        print(f"  {label:22} {len(group):4} orders → {dict(res)}")

    # 5. rule audit
    print("\n── 5. rule audit ──")
    issues = []
    for r in sup:
        oid, act, tier = r.get("amazon_id"), r.get("action"), r.get("tier")
        cart = float(r.get("cart") or 0); margin = r.get("margin"); sel = bool(r.get("zoro_select"))
        v = (r.get("verdict") or {}); att = r.get("attempts") or []
        pri_worked = any(a.get("ok") and a.get("tier") == "priority" for a in att)
        if r.get("pdp_ineligible") and act != "reseller":
            issues.append((oid, "PDP badge but not sent to reseller"))
        if act == "place" and tier == "regular":
            if sel:
                issues.append((oid, "Zoro Select placed with a REGULAR coupon (wasted: reseller has 10% anyway)"))
            thin = margin is not None and margin < MIN_MARGIN
            if cart < MIN_CART and not thin:
                issues.append((oid, f"regular coupon spent on a ${cart:.2f} cart with margin {margin}"))
        if act == "reseller" and not sel and not r.get("pdp_ineligible") and cart >= MIN_CART:
            issues.append((oid, f"${cart:.2f} non-Select cart sent to reseller (should wait for coupons)"))
        if act == "place" and tier == "regular" and pri_worked:
            issues.append((oid, "priority coupon worked but a regular one was used"))
        if act == "wait" and (cart < MIN_CART and not (margin is not None and margin < MIN_MARGIN)) and not sel:
            issues.append((oid, f"waiting on a ${cart:.2f} cart that needed no coupon"))
        want_col = "Coupon" if pri_worked else ("Reseller" if (sel or r.get("pdp_ineligible")) else
                                                ("Coupon" if float(r.get("unit_price") or 0) >= MIN_CART else "Deciding"))
        if v.get("suggest") and v.get("suggest") != want_col and v.get("status") != "PROMO_INELIGIBLE":
            issues.append((oid, f"sheet column {v.get('suggest')} but rules say {want_col}"))
    for r in dec:
        oid = r.get("amazon_id"); cart = float(r.get("cart") or 0); margin = r.get("margin")
        sel = bool(r.get("zoro_select")); bad = bool(r.get("pdp_ineligible"))
        routed = r.get("action") == "route_to_coupon"
        if routed and (sel or bad):
            issues.append((oid, "deciding: routed a Zoro Select / ineligible order to coupon"))
        if routed and cart < MIN_CART and not (margin is not None and margin < MIN_MARGIN):
            issues.append((oid, f"deciding: routed a ${cart:.2f} cart with margin {margin}"))
        if not routed and not sel and not bad and cart >= MIN_CART:
            issues.append((oid, f"deciding: kept a ${cart:.2f} non-Select cart on reseller"))
    if issues:
        print(f"  ⚠️ {len(issues)} decision(s) to look at:")
        for oid, why in issues[:40]:
            print(f"    {oid}  {why}")
    else:
        print(f"  ✅ every supervisor and deciding decision follows the rules ({len(sup)} + {len(dec)} checked)")

    n = int(_arg("--show", "0") or 0)
    if n:
        print(f"\n── last {n} supervisor decisions ──")
        for r in sup[-n:]:
            print(json.dumps(r, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
