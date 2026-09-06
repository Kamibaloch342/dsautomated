#!/usr/bin/env python3
"""
backfill_2026_from_archive.py — Backfill ALL 2026 orders using archive_* tables as master list.

No report download needed. Uses archive_earnings (55K+ orders) as the canonical list.
For each order:
  1. Look up WB order ID from archive_zoro or order_placer_orders
  2. Enrich from all archive_* tables
  3. Calculate profit & label
  4. Save to closing_orders

Usage:
    cd /Users/kamranbaloch342/Desktop/Dropshipping/Development_Unit/Automations
    source ../.venv/bin/activate
    python scripts/backfill_2026_from_archive.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import os
import sys
import time
import sqlite3
import argparse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

from utils.unified_db import DB_PATH, upsert_closing_order

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_all_2026_order_ids(conn: sqlite3.Connection) -> list[str]:
    """Get all unique amazon_order_id from archive_earnings for 2026."""
    rows = conn.execute("""
        SELECT DISTINCT amazon_order_id FROM archive_earnings
        WHERE order_date >= '2026-01-01' OR email_timestamp >= '2026-01-01'
        ORDER BY email_timestamp
    """).fetchall()
    return [r["amazon_order_id"] for r in rows]


def get_wb_order_id(conn: sqlite3.Connection, amazon_id: str) -> str | None:
    """Find wb_order_id from archive_zoro or order_placer_orders."""
    # Try order_placer_orders first (most reliable)
    r = conn.execute(
        "SELECT wb_order_id FROM order_placer_orders WHERE amazon_order_id = ? LIMIT 1",
        (amazon_id,)
    ).fetchone()
    if r and r["wb_order_id"]:
        return r["wb_order_id"]
    
    # Fallback: search archive_zoro by looking for amazon_order_id in raw_body
    r = conn.execute(
        "SELECT wb_order_id FROM archive_zoro WHERE raw_body_snippet LIKE ? LIMIT 1",
        (f"%{amazon_id}%",)
    ).fetchone()
    if r and r["wb_order_id"]:
        return r["wb_order_id"]
    
    return None


def enrich_order(conn: sqlite3.Connection, amazon_id: str, wb_id: str | None) -> dict:
    """Enrich from all archive_* tables."""
    d: dict = {}
    
    # Earnings
    r = conn.execute(
        "SELECT * FROM archive_earnings WHERE amazon_order_id = ? ORDER BY email_timestamp DESC LIMIT 1",
        (amazon_id,)
    ).fetchone()
    if r:
        d.update({
            "earnings": r["total_your_earnings"],
            "amazon_fees": r["total_amazon_fees"],
            "shipping_amt": r["total_shipping"],
            "sold_email_time": r["email_timestamp"],
            "ship_by_date": r["ship_by_date"],
            "purchase_date": r["order_date"],
            "sku": r["skus"],
            "asin": r["asins"],
            "quantity": r["num_items"],
        })
    
    # Zoro
    if wb_id:
        r = conn.execute(
            "SELECT * FROM archive_zoro WHERE wb_order_id = ? ORDER BY email_timestamp DESC LIMIT 1",
            (wb_id,)
        ).fetchone()
        if r:
            d.update({
                "zoro_account_name": r["zoro_account_name"],
                "zoro_gmail_account": r["gmail_account"],
                "zoro_po_number": r["po_number"],
                "zoro_order_status": r["order_status"],
                "zoro_sku": r["sku"],
                "zoro_qty": r["qty"],
                "zoro_order_date": r["order_date"],
                "zoro_email_ts": r["email_timestamp"],
                "zoro_payment_card": r["payment_card_last4"],
                "zoro_tax": r["tax"],
                "zoro_order_total": r["order_total"],
                "zoro_buyer_zip": r["ship_zip"],
                "coupon_savings": r["coupon_savings"],
            })
    
    # Tracking
    if wb_id:
        r = conn.execute(
            "SELECT * FROM archive_tracking WHERE wb_order_id = ? ORDER BY email_timestamp DESC LIMIT 1",
            (wb_id,)
        ).fetchone()
        if r:
            d.update({
                "tracking_ids": r["tracking_ids"],
                "carrier": r["carrier"],
                "shipped_email_time": r["email_timestamp"],
            })
    
    # Returns (FIXED)
    rows = conn.execute(
        "SELECT * FROM archive_returns WHERE amazon_order_id = ? ORDER BY email_timestamp DESC",
        (amazon_id,)
    ).fetchall()
    if rows:
        d["return_requested"] = "Yes"
        for row in rows:
            if row["refund_done"] == "Yes" or "refund initiated" in (row["raw_body_snippet"] or "").lower():
                d["refund_done"] = "Yes"
                d["refund_amount"] = row["refund_amount"]
                d["refund_date"] = row["refund_date"]
                d["refund_reason"] = row["refund_reason"] or row["return_reason"]
                break
    
    # Messages (FIXED)
    rows = conn.execute(
        "SELECT * FROM archive_messages WHERE amazon_order_id = ? ORDER BY email_timestamp DESC",
        (amazon_id,)
    ).fetchall()
    if rows:
        d["customer_messaged"] = "Yes"
        inbound = sum(1 for r in rows if r["direction"] == "inbound")
        if inbound > 0:
            d["customer_inquiry_count"] = inbound
    
    return d


def calculate_profit(data: dict) -> tuple:
    earnings = data.get("earnings") or 0
    zoro_total = data.get("zoro_order_total") or 0
    zoro_tax = data.get("zoro_tax") or 0
    coupon = data.get("coupon_savings") or 0
    profit = earnings - zoro_total - zoro_tax + coupon
    margin = round((profit / earnings * 100), 2) if earnings > 0 else 0
    return round(profit, 2), margin


def determine_label(data: dict) -> str:
    if data.get("return_requested") == "Yes" and data.get("refund_done") == "Yes":
        return "Cancelled"
    if data.get("earnings") and data.get("zoro_order_total") and data.get("tracking_ids"):
        if data.get("delivery_status") in ("Delivered", "AllDelivered"):
            return "Completed"
    return "Incomplete"


def main():
    parser = argparse.ArgumentParser(description="Backfill 2026 orders from archive")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N orders (0 = all)")
    parser.add_argument("--skip-complete", action="store_true", default=True, help="Skip already-Completed orders")
    args = parser.parse_args()
    
    print("=" * 70)
    print("2026 BACKFILL FROM ARCHIVE")
    print("=" * 70)
    
    conn = get_conn()
    
    order_ids = get_all_2026_order_ids(conn)
    print(f"Total 2026 orders in archive_earnings: {len(order_ids)}")
    
    if args.limit > 0:
        order_ids = order_ids[:args.limit]
        print(f"Limited to: {args.limit}")
    
    # Pre-load existing order IDs for fast lookup
    existing = set()
    if args.skip_complete:
        for r in conn.execute("SELECT amazon_order_id, order_label FROM closing_orders"):
            if r["order_label"] == "Completed":
                existing.add(r["amazon_order_id"])
        print(f"Already complete (will skip): {len(existing)}")
    
    run_id = f"backfill_2026_archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    processed = 0
    enriched = 0
    skipped = 0
    errors = 0
    
    print(f"\nProcessing {len(order_ids)} orders...")
    start = time.time()
    
    for amazon_id in order_ids:
        try:
            if amazon_id in existing:
                skipped += 1
                continue
            
            wb_id = get_wb_order_id(conn, amazon_id)
            
            closing_data = {
                "amazon_order_id": amazon_id,
                "merchant_order_id": wb_id or "",
                "account": "HOANK",
                "wb_order_id": wb_id or "",
                "last_enrichment_run": run_id,
            }
            
            archive_data = enrich_order(conn, amazon_id, wb_id)
            closing_data.update(archive_data)
            
            pd, pp = calculate_profit(closing_data)
            closing_data["profit_dollars"] = pd
            closing_data["profit_percent"] = pp
            closing_data["order_label"] = determine_label(closing_data)
            
            if not args.dry_run:
                upsert_closing_order(closing_data, conn=conn)
            
            processed += 1
            if archive_data:
                enriched += 1
            
            if processed % 500 == 0:
                elapsed = time.time() - start
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"  ... {processed} processed ({rate:.0f}/sec), {enriched} enriched, {skipped} skipped, {errors} errors", end="\r")
        
        except Exception as e:
            errors += 1
            print(f"\n  ⚠️ Error on {amazon_id}: {e}")
    
    if not args.dry_run:
        conn.commit()
    conn.close()
    
    elapsed = time.time() - start
    print(f"\n\nDone in {elapsed:.1f}s")
    print(f"  Processed: {processed}")
    print(f"  Enriched:  {enriched}")
    print(f"  Skipped:   {skipped}")
    print(f"  Errors:    {errors}")
    print("=" * 70)


if __name__ == "__main__":
    main()
