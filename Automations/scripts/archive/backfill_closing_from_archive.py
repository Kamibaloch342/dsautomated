#!/usr/bin/env python3
"""
backfill_closing_from_archive.py — Fill gaps in closing_orders from archive_* tables

Targets:
  - ship_by_date, purchase_date from archive_earnings
  - ASIN, SKU from archive_earnings
  - zoro_tax, zoro_order_total, zoro_buyer_zip from archive_zoro
  - tracking_ids, carrier from archive_tracking
  - return_requested from archive_returns
  - customer_messaged from archive_messages

Usage:
    python scripts/backfill_closing_from_archive.py [--dry-run]
"""

from __future__ import annotations

import os
import sys
import argparse

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from utils.unified_db import get_conn, upsert_closing_order, log_enrichment

RUN_ID = "archive_gapfill"

# ---------------------------------------------------------------------------
# Gap fill logic
# ---------------------------------------------------------------------------

def _safe_val(val):
    if val is None or val == "":
        return None
    return val


def fill_gaps(dry_run: bool = False, verbose: bool = True) -> dict:
    stats = {
        "ship_by_filled": 0,
        "purchase_date_filled": 0,
        "asin_filled": 0,
        "sku_filled": 0,
        "zoro_filled": 0,
        "tracking_filled": 0,
        "returns_filled": 0,
        "messages_filled": 0,
        "total_orders_touched": 0,
    }
    touched_orders = set()

    with get_conn() as conn:
        # ── 1. archive_earnings → ship_by_date, purchase_date, ASIN, SKU ──
        if verbose:
            print("Filling from archive_earnings...")

        rows = conn.execute("""
            SELECT c.amazon_order_id,
                   e.ship_by_date, e.order_date, e.asins, e.skus,
                   e.total_your_earnings, e.total_amazon_fees, e.total_shipping
            FROM closing_orders c
            JOIN archive_earnings e ON c.amazon_order_id = e.amazon_order_id
            WHERE (c.ship_by_date IS NULL OR c.ship_by_date = '')
               OR (c.purchase_date IS NULL OR c.purchase_date = '')
               OR (c.asin IS NULL OR c.asin = '')
               OR (c.sku IS NULL OR c.sku = '')
        """).fetchall()

        for row in rows:
            amazon_id = row["amazon_order_id"]
            updates = {"amazon_order_id": amazon_id}
            changed = False

            if _safe_val(row["ship_by_date"]):
                old = conn.execute("SELECT ship_by_date FROM closing_orders WHERE amazon_order_id=?", (amazon_id,)).fetchone()["ship_by_date"]
                if not old:
                    updates["ship_by_date"] = row["ship_by_date"]
                    stats["ship_by_filled"] += 1
                    changed = True
                    if not dry_run:
                        log_enrichment(RUN_ID, amazon_id, "archive_earnings", "ship_by_date", old, row["ship_by_date"], confidence=0.9)

            if _safe_val(row["order_date"]):
                old = conn.execute("SELECT purchase_date FROM closing_orders WHERE amazon_order_id=?", (amazon_id,)).fetchone()["purchase_date"]
                if not old:
                    updates["purchase_date"] = row["order_date"]
                    stats["purchase_date_filled"] += 1
                    changed = True
                    if not dry_run:
                        log_enrichment(RUN_ID, amazon_id, "archive_earnings", "purchase_date", old, row["order_date"], confidence=0.9)

            if _safe_val(row["asins"]):
                old = conn.execute("SELECT asin FROM closing_orders WHERE amazon_order_id=?", (amazon_id,)).fetchone()["asin"]
                if not old:
                    # archive stores comma-separated ASINs
                    asin = row["asins"].split(",")[0].strip()
                    updates["asin"] = asin
                    stats["asin_filled"] += 1
                    changed = True
                    if not dry_run:
                        log_enrichment(RUN_ID, amazon_id, "archive_earnings", "asin", old, asin, confidence=0.9)

            if _safe_val(row["skus"]):
                old = conn.execute("SELECT sku FROM closing_orders WHERE amazon_order_id=?", (amazon_id,)).fetchone()["sku"]
                if not old:
                    sku = row["skus"].split(",")[0].strip()
                    updates["sku"] = sku
                    stats["sku_filled"] += 1
                    changed = True
                    if not dry_run:
                        log_enrichment(RUN_ID, amazon_id, "archive_earnings", "sku", old, sku, confidence=0.9)

            if changed:
                touched_orders.add(amazon_id)
                if not dry_run:
                    updates["enriched_from_archive"] = 1
                    upsert_closing_order(updates)

        # ── 2. archive_zoro → zoro_tax, zoro_order_total, zoro_buyer_zip ──
        if verbose:
            print("Filling from archive_zoro...")

        rows = conn.execute("""
            SELECT c.amazon_order_id,
                   z.tax, z.order_total, z.ship_zip, z.sku as zoro_sku, z.qty as zoro_qty
            FROM closing_orders c
            JOIN archive_zoro z ON c.wb_order_id = z.wb_order_id
            WHERE (c.zoro_tax IS NULL OR c.zoro_tax = '')
               OR (c.zoro_order_total IS NULL OR c.zoro_order_total = '')
               OR (c.zoro_buyer_zip IS NULL OR c.zoro_buyer_zip = '')
        """).fetchall()

        for row in rows:
            amazon_id = row["amazon_order_id"]
            updates = {"amazon_order_id": amazon_id}
            changed = False

            cur = conn.execute("SELECT zoro_tax, zoro_order_total, zoro_buyer_zip FROM closing_orders WHERE amazon_order_id=?", (amazon_id,)).fetchone()

            if row["tax"] is not None and not cur["zoro_tax"]:
                updates["zoro_tax"] = row["tax"]
                stats["zoro_filled"] += 1
                changed = True
                if not dry_run:
                    log_enrichment(RUN_ID, amazon_id, "archive_zoro", "zoro_tax", cur["zoro_tax"], row["tax"], confidence=0.9)

            if row["order_total"] is not None and not cur["zoro_order_total"]:
                updates["zoro_order_total"] = row["order_total"]
                changed = True
                if not dry_run:
                    log_enrichment(RUN_ID, amazon_id, "archive_zoro", "zoro_order_total", cur["zoro_order_total"], row["order_total"], confidence=0.9)

            if _safe_val(row["ship_zip"]) and not cur["zoro_buyer_zip"]:
                updates["zoro_buyer_zip"] = row["ship_zip"]
                changed = True
                if not dry_run:
                    log_enrichment(RUN_ID, amazon_id, "archive_zoro", "zoro_buyer_zip", cur["zoro_buyer_zip"], row["ship_zip"], confidence=0.9)

            if changed:
                touched_orders.add(amazon_id)
                if not dry_run:
                    updates["enriched_from_archive"] = 1
                    upsert_closing_order(updates)

        # ── 3. archive_tracking → tracking_ids, carrier ──
        if verbose:
            print("Filling from archive_tracking...")

        rows = conn.execute("""
            SELECT c.amazon_order_id, t.tracking_ids, t.carrier
            FROM closing_orders c
            JOIN archive_tracking t ON c.wb_order_id = t.wb_order_id
            WHERE c.tracking_ids IS NULL OR c.tracking_ids = ''
        """).fetchall()

        for row in rows:
            amazon_id = row["amazon_order_id"]
            if _safe_val(row["tracking_ids"]):
                old = conn.execute("SELECT tracking_ids FROM closing_orders WHERE amazon_order_id=?", (amazon_id,)).fetchone()["tracking_ids"]
                if not old:
                    updates = {
                        "amazon_order_id": amazon_id,
                        "tracking_ids": row["tracking_ids"],
                        "carrier": row["carrier"] or "FedEx",
                        "enriched_from_archive": 1,
                    }
                    stats["tracking_filled"] += 1
                    touched_orders.add(amazon_id)
                    if not dry_run:
                        log_enrichment(RUN_ID, amazon_id, "archive_tracking", "tracking_ids", old, row["tracking_ids"], confidence=0.9)
                        upsert_closing_order(updates)

        # ── 4. archive_returns → return_requested ──
        if verbose:
            print("Filling from archive_returns...")

        rows = conn.execute("""
            SELECT c.amazon_order_id
            FROM closing_orders c
            JOIN archive_returns r ON c.amazon_order_id = r.amazon_order_id
            WHERE c.return_requested IS NULL OR c.return_requested = ''
            GROUP BY c.amazon_order_id
        """).fetchall()

        for row in rows:
            amazon_id = row["amazon_order_id"]
            old = conn.execute("SELECT return_requested FROM closing_orders WHERE amazon_order_id=?", (amazon_id,)).fetchone()["return_requested"]
            if not old:
                updates = {
                    "amazon_order_id": amazon_id,
                    "return_requested": "Yes",
                    "enriched_from_archive": 1,
                }
                stats["returns_filled"] += 1
                touched_orders.add(amazon_id)
                if not dry_run:
                    log_enrichment(RUN_ID, amazon_id, "archive_returns", "return_requested", old, "Yes", confidence=0.9)
                    upsert_closing_order(updates)

        # ── 5. archive_messages → customer_messaged ──
        if verbose:
            print("Filling from archive_messages...")

        rows = conn.execute("""
            SELECT c.amazon_order_id
            FROM closing_orders c
            JOIN archive_messages m ON c.amazon_order_id = m.amazon_order_id
            WHERE c.customer_messaged IS NULL OR c.customer_messaged = ''
            GROUP BY c.amazon_order_id
        """).fetchall()

        for row in rows:
            amazon_id = row["amazon_order_id"]
            old = conn.execute("SELECT customer_messaged FROM closing_orders WHERE amazon_order_id=?", (amazon_id,)).fetchone()["customer_messaged"]
            if not old:
                updates = {
                    "amazon_order_id": amazon_id,
                    "customer_messaged": "Yes",
                    "enriched_from_archive": 1,
                }
                stats["messages_filled"] += 1
                touched_orders.add(amazon_id)
                if not dry_run:
                    log_enrichment(RUN_ID, amazon_id, "archive_messages", "customer_messaged", old, "Yes", confidence=0.8)
                    upsert_closing_order(updates)

    stats["total_orders_touched"] = len(touched_orders)
    if verbose:
        print(f"\n✅ Gap-fill complete:")
        print(f"  Orders touched: {len(touched_orders)}")
        print(f"  ship_by_date filled: {stats['ship_by_filled']}")
        print(f"  purchase_date filled: {stats['purchase_date_filled']}")
        print(f"  ASIN filled: {stats['asin_filled']}")
        print(f"  SKU filled: {stats['sku_filled']}")
        print(f"  Zoro data filled: {stats['zoro_filled']}")
        print(f"  Tracking filled: {stats['tracking_filled']}")
        print(f"  Returns filled: {stats['returns_filled']}")
        print(f"  Messages filled: {stats['messages_filled']}")
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Backfill closing_orders gaps from archive_* tables")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be filled without writing")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    args = parser.parse_args()

    stats = fill_gaps(dry_run=args.dry_run, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
