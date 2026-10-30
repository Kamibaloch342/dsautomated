#!/usr/bin/env python3
"""
backfill_2026_from_archive_fast.py — Optimized DB-to-DB backfill.

Pre-loads all archive data into memory, then bulk inserts.
~50-100x faster than per-query approach.

Usage:
    python scripts/backfill_2026_from_archive_fast.py [--dry-run] [--batch-size 5000]
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

DB_PATH = os.path.join(SCRIPT_DIR, "data", "automations_unified.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def preload_data(conn: sqlite3.Connection) -> dict:
    """Pre-load all archive data into memory for fast lookup."""
    print("Pre-loading archive data into memory...")
    start = time.time()
    
    data = {
        "earnings": {},
        "zoro": {},
        "tracking": {},
        "returns": {},
        "messages": {},
        "placer_wb": {},
        "existing_complete": set(),
    }
    
    # Earnings
    for r in conn.execute("SELECT * FROM archive_earnings WHERE order_date >= '2026-01-01' OR email_timestamp >= '2026-01-01'"):
        oid = r["amazon_order_id"]
        if oid not in data["earnings"]:
            data["earnings"][oid] = dict(r)
    print(f"  Earnings: {len(data['earnings'])} records")
    
    # Zoro — keyed by wb_order_id
    for r in conn.execute("SELECT * FROM archive_zoro"):
        wb = r["wb_order_id"]
        if wb and wb not in data["zoro"]:
            data["zoro"][wb] = dict(r)
    print(f"  Zoro: {len(data['zoro'])} records")
    
    # Tracking — keyed by wb_order_id
    for r in conn.execute("SELECT * FROM archive_tracking"):
        wb = r["wb_order_id"]
        if wb and wb not in data["tracking"]:
            data["tracking"][wb] = dict(r)
    print(f"  Tracking: {len(data['tracking'])} records")
    
    # Returns — keyed by amazon_order_id, list of rows
    for r in conn.execute("SELECT * FROM archive_returns"):
        oid = r["amazon_order_id"]
        if oid:
            data["returns"].setdefault(oid, []).append(dict(r))
    print(f"  Returns: {len(data['returns'])} orders")
    
    # Messages — keyed by amazon_order_id, list of rows
    for r in conn.execute("SELECT * FROM archive_messages"):
        oid = r["amazon_order_id"]
        if oid:
            data["messages"].setdefault(oid, []).append(dict(r))
    print(f"  Messages: {len(data['messages'])} orders")
    
    # WB order IDs from placer
    for r in conn.execute("SELECT amazon_order_id, wb_order_id FROM order_placer_orders WHERE wb_order_id IS NOT NULL"):
        data["placer_wb"][r["amazon_order_id"]] = r["wb_order_id"]
    print(f"  Placer WB mappings: {len(data['placer_wb'])} records")
    
    # Existing complete orders
    for r in conn.execute("SELECT amazon_order_id FROM closing_orders WHERE order_label = 'Completed'"):
        data["existing_complete"].add(r["amazon_order_id"])
    print(f"  Already complete: {len(data['existing_complete'])} orders")
    
    elapsed = time.time() - start
    print(f"Pre-load complete in {elapsed:.1f}s\n")
    return data


def build_order_row(amazon_id: str, data: dict) -> dict | None:
    """Build a closing_orders row from preloaded data."""
    earnings = data["earnings"].get(amazon_id)
    if not earnings:
        return None  # Skip if no earnings (shouldn't happen for our list)
    
    # Find WB order ID
    wb_id = data["placer_wb"].get(amazon_id)
    if not wb_id:
        # Try to find from zoro body snippets
        for z in data["zoro"].values():
            if amazon_id in (z.get("raw_body_snippet") or ""):
                wb_id = z.get("wb_order_id")
                break
    
    row = {
        "amazon_order_id": amazon_id,
        "merchant_order_id": wb_id or "",
        "account": "HOANK",
        "wb_order_id": wb_id or "",
    }
    
    # Earnings
    row.update({
        "earnings": earnings.get("total_your_earnings"),
        "amazon_fees": earnings.get("total_amazon_fees"),
        "shipping_amt": earnings.get("total_shipping"),
        "sold_email_time": earnings.get("email_timestamp"),
        "ship_by_date": earnings.get("ship_by_date"),
        "purchase_date": earnings.get("order_date"),
        "sku": earnings.get("skus"),
        "asin": earnings.get("asins"),
        "quantity": earnings.get("num_items"),
    })
    
    # Zoro
    z = data["zoro"].get(wb_id) if wb_id else None
    if z:
        row.update({
            "zoro_account_name": z.get("zoro_account_name"),
            "zoro_gmail_account": z.get("gmail_account"),
            "zoro_po_number": z.get("po_number"),
            "zoro_order_status": z.get("order_status"),
            "zoro_sku": z.get("sku"),
            "zoro_qty": z.get("qty"),
            "zoro_order_date": z.get("order_date"),
            "zoro_email_ts": z.get("email_timestamp"),
            "zoro_payment_card": z.get("payment_card_last4"),
            "zoro_tax": z.get("tax"),
            "zoro_order_total": z.get("order_total"),
            "zoro_buyer_zip": z.get("ship_zip"),
            "coupon_savings": z.get("coupon_savings"),
        })
    
    # Tracking
    t = data["tracking"].get(wb_id) if wb_id else None
    if t:
        row.update({
            "tracking_ids": t.get("tracking_ids"),
            "carrier": t.get("carrier"),
            "shipped_email_time": t.get("email_timestamp"),
        })
    
    # Returns
    returns = data["returns"].get(amazon_id, [])
    if returns:
        row["return_requested"] = "Yes"
        for ret in returns:
            if ret.get("refund_done") == "Yes" or "refund initiated" in (ret.get("raw_body_snippet") or "").lower():
                row["refund_done"] = "Yes"
                row["refund_amount"] = ret.get("refund_amount")
                row["refund_date"] = ret.get("refund_date")
                row["refund_reason"] = ret.get("refund_reason") or ret.get("return_reason")
                break
    
    # Messages
    messages = data["messages"].get(amazon_id, [])
    if messages:
        row["customer_messaged"] = "Yes"
        inbound = sum(1 for m in messages if m.get("direction") == "inbound")
        if inbound > 0:
            row["customer_inquiry_count"] = inbound
    
    # Profit
    earnings_val = row.get("earnings") or 0
    zoro_total = row.get("zoro_order_total") or 0
    zoro_tax = row.get("zoro_tax") or 0
    coupon = row.get("coupon_savings") or 0
    profit = earnings_val - zoro_total - zoro_tax + coupon
    row["profit_dollars"] = round(profit, 2)
    row["profit_percent"] = round((profit / earnings_val * 100), 2) if earnings_val > 0 else 0
    
    # Label
    if row.get("return_requested") == "Yes" and row.get("refund_done") == "Yes":
        row["order_label"] = "Cancelled"
    elif earnings_val and zoro_total and row.get("tracking_ids"):
        row["order_label"] = "Incomplete"  # Delivery status unknown without FedEx
    else:
        row["order_label"] = "Incomplete"
    
    row["last_enrichment_run"] = "backfill_2026_archive_fast"
    return row


def bulk_insert(conn: sqlite3.Connection, rows: list[dict]):
    """Bulk insert/update closing_orders rows."""
    if not rows:
        return
    
    # Get all columns that appear in any row
    all_cols = set()
    for r in rows:
        all_cols.update(r.keys())
    all_cols.discard("id")
    cols = sorted(all_cols)
    
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    
    # Build upsert: insert or replace
    upsert_cols = [c for c in cols if c != "amazon_order_id"]
    set_clause = ", ".join(f"{c}=excluded.{c}" for c in upsert_cols)
    
    sql = f"""
        INSERT INTO closing_orders ({col_list})
        VALUES ({placeholders})
        ON CONFLICT(amazon_order_id) DO UPDATE SET
            {set_clause},
            updated_at=CURRENT_TIMESTAMP
    """
    
    params = []
    for r in rows:
        params.append([r.get(c) for c in cols])
    
    conn.executemany(sql, params)


def main():
    parser = argparse.ArgumentParser(description="Fast 2026 backfill from archive")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--batch-size", type=int, default=5000, help="Bulk insert batch size")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N orders (0 = all)")
    args = parser.parse_args()
    
    print("=" * 70)
    print("2026 FAST BACKFILL FROM ARCHIVE")
    print("=" * 70)
    
    conn = get_conn()
    data = preload_data(conn)
    
    order_ids = list(data["earnings"].keys())
    print(f"Total 2026 orders to process: {len(order_ids)}")
    
    if args.limit > 0:
        order_ids = order_ids[:args.limit]
        print(f"Limited to: {args.limit}")
    
    # Filter already complete
    order_ids = [oid for oid in order_ids if oid not in data["existing_complete"]]
    print(f"After skipping complete: {len(order_ids)}")
    
    run_id = f"backfill_fast_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    batch = []
    processed = 0
    skipped = 0
    errors = 0
    
    print(f"\nProcessing...")
    start = time.time()
    
    for amazon_id in order_ids:
        try:
            row = build_order_row(amazon_id, data)
            if not row:
                skipped += 1
                continue
            
            batch.append(row)
            processed += 1
            
            if len(batch) >= args.batch_size:
                if not args.dry_run:
                    bulk_insert(conn, batch)
                    conn.commit()
                batch = []
                
                elapsed = time.time() - start
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"  ... {processed} processed ({rate:.0f}/sec), {skipped} skipped, {errors} errors", end="\r")
        
        except Exception as e:
            errors += 1
            print(f"\n  ⚠️ Error on {amazon_id}: {e}")
    
    # Final batch
    if batch and not args.dry_run:
        bulk_insert(conn, batch)
        conn.commit()
    
    conn.close()
    
    elapsed = time.time() - start
    rate = processed / elapsed if elapsed > 0 else 0
    print(f"\n\nDone in {elapsed:.1f}s ({rate:.0f} orders/sec)")
    print(f"  Processed: {processed}")
    print(f"  Skipped:   {skipped}")
    print(f"  Errors:    {errors}")
    print("=" * 70)


if __name__ == "__main__":
    main()
