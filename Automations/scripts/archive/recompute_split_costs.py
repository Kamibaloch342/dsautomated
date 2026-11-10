#!/usr/bin/env python3
"""
recompute_split_costs.py — correct the Zoro cost/profit/label of ALREADY-CLOSED split
orders (one Amazon order = 2-4 Zoro/WB legs).

closing.py now sums cost across all legs going forward (_sum_leg_costs), but RESUME skips
terminal orders, so existing closed split orders keep their wrong single-leg cost. This
fixes those rows in place: sum order_total/tax/subtotal/shipping across every WB leg found
in archive_zoro, then recompute profit_dollars / profit_percent / net_profit_after_refund
and re-derive order_label (a now-negative delivered order flips Completed → Loss).

Only touches rows where >=2 legs are priced in archive_zoro (a real, trustworthy sum).
DRY-RUN by default; pass --go to write. One transaction; backup already taken by the
migration, but re-back-up if you're unsure.

Run:  ../.venv/bin/python scripts/recompute_split_costs.py        # preview
      ../.venv/bin/python scripts/recompute_split_costs.py --go   # apply
"""
import os, re, sys, sqlite3

GO = "--go" in sys.argv
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "automations_unified.db")
DELIV = ("Delivered", "AllDelivered")


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


def label_of(row, earn, zoro, profit):
    """Mirror closing.determine_label."""
    if row["return_requested"] == "Yes" and row["refund_done"] == "Yes":
        shipped = bool(row["tracking_ids"]) or row["delivery_status"] in (
            "Delivered", "AllDelivered", "PartialDelivered", "OutForDelivery")
        return "Refunded" if shipped else "Cancelled"
    if earn and zoro and row["tracking_ids"] and row["delivery_status"] in DELIV:
        if profit is not None and profit < 0:
            return "Loss"
        return "Completed"
    return "Incomplete"


con = sqlite3.connect(DB, timeout=60)
con.row_factory = sqlite3.Row
con.isolation_level = None
con.execute("PRAGMA busy_timeout=60000")

rows = con.execute(
    "SELECT amazon_order_id, merchant_order_id, earnings, zoro_order_total, "
    "profit_dollars, order_label, delivery_status, tracking_ids, return_requested, "
    "refund_done, refund_amount, zoro_refund_amount FROM closing_orders").fetchall()

plan = []
for r in rows:
    wbs = wb_ids(r["merchant_order_id"])
    if len(wbs) < 2:
        continue
    tot = tax = sub = ship = 0.0
    found = 0
    for wb in wbs:
        lr = con.execute(
            "SELECT order_total, tax, subtotal, shipping_cost FROM archive_zoro "
            "WHERE wb_order_id=? ORDER BY email_timestamp DESC LIMIT 1", (wb,)).fetchone()
        if not lr:
            continue
        ot = f(lr[0])
        if ot is None:
            continue
        found += 1
        tot += ot
        tax += f(lr[1]) or 0.0
        sub += f(lr[2]) or 0.0
        ship += f(lr[3]) or 0.0
    if found < 2:
        continue
    new_cost = round(tot, 2)
    cur_cost = f(r["zoro_order_total"])
    if cur_cost is not None and abs(new_cost - cur_cost) < 0.005:
        continue                              # already correct
    earn = f(r["earnings"])
    profit = round(earn - new_cost, 2) if earn is not None else None
    pct = round(profit / earn * 100, 2) if (profit is not None and earn) else None
    net = profit
    if r["refund_done"] == "Yes" and profit is not None:
        net = round(profit - (f(r["refund_amount"]) or 0.0) + (f(r["zoro_refund_amount"]) or 0.0), 2)
    new_label = label_of(r, earn, new_cost, profit)
    plan.append({
        "aid": r["amazon_order_id"], "legs": len(wbs), "priced": found,
        "cost": (cur_cost, new_cost), "tax": round(tax, 2), "sub": round(sub, 2),
        "ship": round(ship, 2), "profit": (f(r["profit_dollars"]), profit),
        "pct": pct, "net": net, "label": (r["order_label"], new_label),
    })

print(f"Split orders to recompute: {len(plan)}  ({'APPLYING' if GO else 'dry-run'})")
print("=" * 96)
flips = 0
for p in plan:
    lab = f"{p['label'][0]}→{p['label'][1]}" if p['label'][0] != p['label'][1] else p['label'][0]
    mark = ""
    if p['label'][0] != p['label'][1]:
        flips += 1
        mark = "  ⚠️ LABEL CHANGE"
    print(f"{p['aid']}  legs={p['legs']} priced={p['priced']}  [{lab}]{mark}")
    print(f"    cost {p['cost'][0]} → {p['cost'][1]} | profit {p['profit'][0]} → {p['profit'][1]}")

print("=" * 96)
print(f"{len(plan)} rows would change; {flips} label change(s)")

if not GO:
    print("\nDRY-RUN — nothing written. Re-run with --go to apply.")
    con.close()
    sys.exit(0)

try:
    con.execute("BEGIN")
    for p in plan:
        con.execute(
            "UPDATE closing_orders SET zoro_order_total=?, zoro_tax=?, zoro_subtotal=?, "
            "zoro_shipping=?, profit_dollars=?, profit_percent=?, net_profit_after_refund=?, "
            "order_label=? WHERE amazon_order_id=?",
            (p["cost"][1], p["tax"], p["sub"], p["ship"], p["profit"][1], p["pct"],
             p["net"], p["label"][1], p["aid"]))
    con.execute("COMMIT")
    print(f"\n✅ updated {len(plan)} split orders ({flips} label change(s))")
except Exception as e:
    con.execute("ROLLBACK")
    print(f"❌ failed, rolled back: {e}")
finally:
    con.close()
