#!/usr/bin/env python3
"""
verify_split_costs.py — READ-ONLY preview of the split-order cost fix impact.

A split order = one Amazon order fulfilled by 2-4 Zoro (WB) legs. closing.py used to
take only the FIRST leg's Zoro cost → understated cost → overstated profit. This script
sums every leg's cost from archive_zoro and shows, per split order, the current vs the
corrected cost/profit and whether the label would flip (e.g. Completed → Loss).

Changes NOTHING. Run:  ../.venv/bin/python scripts/verify_split_costs.py
"""
import os, re, sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "automations_unified.db")


def wb_ids(s):
    seen, out = set(), []
    for w in re.findall(r"WB\d+", str(s or "").upper()):
        if w not in seen:
            seen.add(w); out.append(w)
    return out


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT amazon_order_id, merchant_order_id, earnings, zoro_order_total, "
    "profit_dollars, order_label, delivery_status FROM closing_orders").fetchall()

splits = [(r, wb_ids(r["merchant_order_id"])) for r in rows]
splits = [(r, w) for (r, w) in splits if len(w) >= 2]
print(f"Split orders (>=2 WB legs) in closing_orders: {len(splits)}")
print("=" * 96)

changed = flips = 0
shown = 0
for r, wbs in splits:
    tot = found = 0
    tot = 0.0
    for wb in wbs:
        row = con.execute(
            "SELECT order_total FROM archive_zoro WHERE wb_order_id=? "
            "ORDER BY email_timestamp DESC LIMIT 1", (wb,)).fetchone()
        ot = f(row[0]) if row else None
        if ot is not None:
            found += 1
            tot += ot
    if found <= 1:
        continue                      # fix wouldn't change this one
    new_cost = round(tot, 2)
    cur_cost = f(r["zoro_order_total"])
    earn = f(r["earnings"])
    if cur_cost is None or new_cost == cur_cost:
        continue
    changed += 1
    cur_profit = round(earn - cur_cost, 2) if earn is not None else None
    new_profit = round(earn - new_cost, 2) if earn is not None else None
    flip = ""
    if cur_profit is not None and new_profit is not None:
        if cur_profit >= 0 > new_profit:
            flip = "  ⚠️ FLIPS profit POSITIVE→NEGATIVE (was hiding a loss)"
            flips += 1
        elif cur_profit < 0 <= new_profit:
            flip = "  (flips negative→positive)"
    if shown < 25:
        print(f"{r['amazon_order_id']}  legs={len(wbs)} priced={found}  "
              f"label={r['order_label']}")
        print(f"    cost {cur_cost} → {new_cost} | "
              f"profit {cur_profit} → {new_profit}{flip}")
        shown += 1

print("=" * 96)
print(f"Split orders whose cost CHANGES with the fix: {changed}")
print(f"  of which profit flips positive→negative (loss was hidden): {flips}")
print("(read-only — nothing was modified)")
con.close()
