#!/usr/bin/env python3
"""
update_order.py — Manually correct an order in closing_orders.

Usage:
    python scripts/update_order.py --amazon-id 112-xxx --field earnings --value 45.50
    python scripts/update_order.py --amazon-id 112-xxx --field ship_by_date --value "Jun 5, 2026"
    python scripts/update_order.py --amazon-id 112-xxx --json '{"earnings": 45.50, "zoro_order_total": 38.00}'
"""

from __future__ import annotations

import os
import sys
import argparse
import json

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from utils.unified_db import get_conn, upsert_closing_order, log_enrichment


def update_single_field(amazon_order_id: str, field: str, value: str, run_id: str = "manual"):
    with get_conn() as conn:
        # Get old value
        row = conn.execute(
            f"SELECT {field} FROM closing_orders WHERE amazon_order_id=?",
            (amazon_order_id,)
        ).fetchone()
        old_val = row[field] if row else None

        # Update
        upsert_closing_order({
            "amazon_order_id": amazon_order_id,
            field: value,
        })

        # Log
        log_enrichment(run_id, amazon_order_id, "manual", field, old_val, value, confidence=1.0)

        # Recalculate profit if earnings or zoro changed
        if field in ("earnings", "zoro_order_total", "zoro_tax"):
            from engine.closing_pipeline import calculate_profit
            row = conn.execute(
                "SELECT earnings, zoro_order_total, zoro_tax FROM closing_orders WHERE amazon_order_id=?",
                (amazon_order_id,)
            ).fetchone()
            if row:
                pd, pp = calculate_profit(dict(row))
                if pd is not None:
                    upsert_closing_order({
                        "amazon_order_id": amazon_order_id,
                        "profit_dollars": pd,
                        "profit_percent": pp,
                    })
                    log_enrichment(run_id, amazon_order_id, "manual", "profit_dollars", None, pd, confidence=1.0)

        print(f"✅ Updated {amazon_order_id}: {field} = {value} (was: {old_val})")


def update_from_json(amazon_order_id: str, data: dict, run_id: str = "manual"):
    with get_conn() as conn:
        # Get current row for old values
        row = conn.execute("SELECT * FROM closing_orders WHERE amazon_order_id=?", (amazon_order_id,)).fetchone()
        if not row:
            print(f"❌ Order {amazon_order_id} not found in closing_orders")
            return

        old = dict(row)
        updates = {"amazon_order_id": amazon_order_id}

        for field, value in data.items():
            updates[field] = value
            old_val = old.get(field)
            log_enrichment(run_id, amazon_order_id, "manual", field, old_val, value, confidence=1.0)
            print(f"  {field}: {old_val} → {value}")

        upsert_closing_order(updates)

        # Recalculate profit
        if any(k in data for k in ("earnings", "zoro_order_total", "zoro_tax")):
            from engine.closing_pipeline import calculate_profit
            row = conn.execute(
                "SELECT earnings, zoro_order_total, zoro_tax FROM closing_orders WHERE amazon_order_id=?",
                (amazon_order_id,)
            ).fetchone()
            if row:
                pd, pp = calculate_profit(dict(row))
                if pd is not None:
                    upsert_closing_order({
                        "amazon_order_id": amazon_order_id,
                        "profit_dollars": pd,
                        "profit_percent": pp,
                    })
                    print(f"  profit_dollars: → {pd}")

        print(f"✅ Updated {amazon_order_id} with {len(data)} fields")


def main():
    parser = argparse.ArgumentParser(description="Manually correct an order")
    parser.add_argument("--amazon-id", required=True, help="Amazon order ID")
    parser.add_argument("--field", help="Field name to update")
    parser.add_argument("--value", help="New value")
    parser.add_argument("--json", help="JSON dict of fields to update")
    parser.add_argument("--run-id", default="manual", help="Run ID for enrichment log")
    args = parser.parse_args()

    if args.json:
        data = json.loads(args.json)
        update_from_json(args.amazon_id, data, run_id=args.run_id)
    elif args.field and args.value is not None:
        update_single_field(args.amazon_id, args.field, args.value, run_id=args.run_id)
    else:
        print("❌ Provide either --field + --value OR --json")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
