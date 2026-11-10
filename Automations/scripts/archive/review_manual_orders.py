#!/usr/bin/env python3
"""
review_manual_orders.py — Show orders needing manual review for correction.

Usage:
    python scripts/review_manual_orders.py [--run-id <id>]
    python scripts/review_manual_orders.py --latest-run
"""

from __future__ import annotations

import os
import sys
import argparse

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from utils.unified_db import get_conn


def get_latest_run_id() -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT run_id FROM closing_run_log WHERE status = 'completed' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return row["run_id"] if row else None


def show_manual_orders(run_id: str | None = None, limit: int = 50):
    with get_conn() as conn:
        if run_id:
            rows = conn.execute("""
                SELECT * FROM closing_orders
                WHERE last_enrichment_run = ?
                  AND comments LIKE '%Needs manual%'
                ORDER BY amazon_order_id
                LIMIT ?
            """, (run_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM closing_orders
                WHERE comments LIKE '%Needs manual%'
                ORDER BY updated_at DESC
                LIMIT ?
            """, (limit,)).fetchall()

        if not rows:
            print("✅ No orders need manual review!")
            return

        print(f"\n📋 Orders Needing Manual Review ({len(rows)} shown):")
        print("─" * 100)
        print(f"{'Amazon Order ID':20s} | {'WB ID':15s} | {'Account':8s} | {'Missing Fields':30s} | {'Current Profit':12s}")
        print("─" * 100)

        for r in rows:
            r = dict(r)
            amazon = r["amazon_order_id"]
            wb = r.get("wb_order_id") or "N/A"
            account = r.get("account", "?")
            comments = r.get("comments", "")
            profit = r.get("profit_dollars")
            profit_str = f"${profit}" if profit is not None else "N/A"

            # Extract missing fields from comment
            missing = comments.replace("Needs manual: ", "").strip()

            print(f"{amazon:20s} | {wb:15s} | {account:8s} | {missing:30s} | {profit_str:12s}")

        print("─" * 100)
        print(f"\nTo fix an order, run:")
        print(f"  python scripts/update_order.py --amazon-id <id> --field <field> --value <value>")


def show_all_stats(run_id: str | None = None):
    with get_conn() as conn:
        if run_id:
            total = conn.execute(
                "SELECT COUNT(*) FROM closing_orders WHERE last_enrichment_run = ?",
                (run_id,)
            ).fetchone()[0]
            manual = conn.execute(
                "SELECT COUNT(*) FROM closing_orders WHERE last_enrichment_run = ? AND comments LIKE '%Needs manual%'",
                (run_id,)
            ).fetchone()[0]
            completed = conn.execute(
                "SELECT COUNT(*) FROM closing_orders WHERE last_enrichment_run = ? AND order_label = 'Completed'",
                (run_id,)
            ).fetchone()[0]
            incomplete = conn.execute(
                "SELECT COUNT(*) FROM closing_orders WHERE last_enrichment_run = ? AND order_label = 'Incomplete'",
                (run_id,)
            ).fetchone()[0]
        else:
            total = conn.execute("SELECT COUNT(*) FROM closing_orders").fetchone()[0]
            manual = conn.execute("SELECT COUNT(*) FROM closing_orders WHERE comments LIKE '%Needs manual%'").fetchone()[0]
            completed = conn.execute("SELECT COUNT(*) FROM closing_orders WHERE order_label = 'Completed'").fetchone()[0]
            incomplete = conn.execute("SELECT COUNT(*) FROM closing_orders WHERE order_label = 'Incomplete'").fetchone()[0]

        print(f"\n📊 Stats:")
        print(f"  Total orders:     {total}")
        print(f"  Completed:        {completed}")
        print(f"  Incomplete:       {incomplete}")
        print(f"  Needs manual:     {manual}")


def main():
    parser = argparse.ArgumentParser(description="Review orders needing manual correction")
    parser.add_argument("--run-id", help="Filter by specific run ID")
    parser.add_argument("--latest-run", action="store_true", help="Use the most recent completed run")
    parser.add_argument("--limit", type=int, default=50, help="Max orders to show")
    parser.add_argument("--stats-only", action="store_true", help="Only show counts, not details")
    args = parser.parse_args()

    run_id = args.run_id
    if args.latest_run:
        run_id = get_latest_run_id()
        if run_id:
            print(f"Latest run: {run_id}")
        else:
            print("No completed runs found.")
            return

    if args.stats_only:
        show_all_stats(run_id)
    else:
        show_all_stats(run_id)
        show_manual_orders(run_id, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
