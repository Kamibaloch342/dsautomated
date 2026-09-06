#!/usr/bin/env python3
"""
closing_pipeline.py — Worker B: Enrich order_placer_orders → closing_orders

Phase 1 (archive-only enrichment):
  1. Read orders from order_placer_orders not yet in closing_orders
  2. Enrich from archive_* tables (earnings, zoro, tracking, returns, messages)
  3. Calculate profit
  4. Determine label (Completed / Incomplete / Cancelled)
  5. Write to closing_orders + enrichment_log

Phase 2 (live enrichment):
  - Live Gmail API for returns/refunds/messages
  - FedEx API for delivery status
  - Amazon SC scrape for missing dates

Usage:
    python engine/closing_pipeline.py --run-id 2026-06-07-001
"""

from __future__ import annotations

import os
import sys
import uuid
import argparse
from datetime import datetime
from typing import Any

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from utils.unified_db import (
    get_conn,
    upsert_closing_order,
    log_enrichment,
    get_placer_order,
    get_closing_order,
    start_closing_run,
    update_closing_run,
    finish_closing_run,
)

# ---------------------------------------------------------------------------
# Enrichment helpers
# ---------------------------------------------------------------------------


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _coalesce(*values) -> Any:
    """Return first non-None, non-empty value."""
    for v in values:
        if v is not None and v != "":
            return v
    return None


# ---------------------------------------------------------------------------
# Archive enrichment
# ---------------------------------------------------------------------------


def _join_json_list(val) -> str:
    """'["G123", ""]' → 'G123'; '[""]'/'[]'/None → ''; plain strings pass through."""
    if not val:
        return ""
    s = str(val).strip()
    if s.startswith("["):
        try:
            import json
            items = [str(x).strip() for x in json.loads(s) if str(x).strip()]
            return ", ".join(items)
        except Exception:
            return ""
    return s


def enrich_from_archive(
    conn,
    amazon_order_id: str,
    wb_order_id: str,
    run_id: str,
) -> dict:
    """
    Query archive_* tables for supplemental data.
    Returns dict of {field: value} and logs each enrichment.
    """
    enriched = {}
    flags = {
        "enriched_from_archive": 0,
    }

    # --- archive_earnings ---
    row = conn.execute(
        "SELECT * FROM archive_earnings WHERE amazon_order_id = ?",
        (amazon_order_id,),
    ).fetchone()
    if row:
        flags["enriched_from_archive"] = 1
        mapping = {
            "earnings": "total_your_earnings",
            "amazon_fees": "total_amazon_fees",
            "shipping_amt": "total_shipping",
            "ship_by_date": "ship_by_date",
            "purchase_date": "order_date",
            "sold_email_time": "email_timestamp",
            "sku": "skus",
            "asin": "asins",
            "quantity": "num_items",
        }
        for dest_field, src_field in mapping.items():
            val = row[src_field]
            # skus/asins are stored as JSON arrays (often ["", ""] when the
            # email parser failed) and num_items can be a bogus 0 — clean
            # before enriching so junk never reaches closing_orders.
            if dest_field in ("sku", "asin"):
                val = _join_json_list(val)
            elif dest_field == "quantity" and not val:
                val = None
            if val is not None and val != "":
                enriched[dest_field] = val
                log_enrichment(run_id, amazon_order_id, "archive_earnings", dest_field, None, val, confidence=0.9, conn=conn)

    # --- archive_zoro ---
    if wb_order_id:
        row = conn.execute(
            "SELECT * FROM archive_zoro WHERE wb_order_id = ? ORDER BY email_timestamp DESC LIMIT 1",
            (wb_order_id,),
        ).fetchone()
        if row:
            flags["enriched_from_archive"] = 1
            mapping = {
                "zoro_account_name": "zoro_account_name",
                "zoro_gmail_account": "gmail_account",
                "zoro_po_number": "po_number",
                "zoro_order_total": "order_total",
                "zoro_tax": "tax",
                "zoro_sku": "sku",
                "zoro_qty": "qty",
                "zoro_buyer_zip": "ship_zip",
                "zoro_email_ts": "email_timestamp",
                "zoro_order_date": "order_date",
                "zoro_payment_card": "payment_card_last4",
                "coupon_savings": "coupon_savings",
            }
            for dest_field, src_field in mapping.items():
                val = row[src_field]
                if val is not None and val != "":
                    enriched[dest_field] = val
                    log_enrichment(run_id, amazon_order_id, "archive_zoro", dest_field, None, val, confidence=0.9, conn=conn)

    # --- archive_tracking ---
    if wb_order_id:
        # ALL rows, not LIMIT 1: Zoro emails once per box, so a split shipment is
        # several rows — taking only the newest stored ONE tracking id of a
        # multi-box order (and the tracker's DB-first upload then applied just that).
        rows_trk = conn.execute(
            "SELECT * FROM archive_tracking WHERE wb_order_id = ? ORDER BY email_timestamp DESC",
            (wb_order_id,),
        ).fetchall()
        if rows_trk:
            flags["enriched_from_archive"] = 1
            row = rows_trk[0]                      # newest row: scalar fields
            all_ids = []                            # every box's tracking, newest first
            for r in rows_trk:
                for t in str(r["tracking_ids"] or "").split(","):
                    t = t.strip()
                    if t and t not in all_ids:
                        all_ids.append(t)
            mapping = {
                "tracking_ids": "tracking_ids",
                "carrier": "carrier",
                "shipped_email_time": "email_timestamp",
            }
            for dest_field, src_field in mapping.items():
                val = ", ".join(all_ids) if dest_field == "tracking_ids" else row[src_field]
                if val is not None and val != "":
                    enriched[dest_field] = val
                    log_enrichment(run_id, amazon_order_id, "archive_tracking", dest_field, None, val, confidence=0.9, conn=conn)

    # --- archive_returns ---
    rows = conn.execute(
        "SELECT * FROM archive_returns WHERE amazon_order_id = ? ORDER BY email_timestamp DESC",
        (amazon_order_id,),
    ).fetchall()
    if rows:
        flags["enriched_from_archive"] = 1
        # Aggregate: any return = Yes
        enriched["return_requested"] = "Yes"
        log_enrichment(run_id, amazon_order_id, "archive_returns", "return_requested", None, "Yes", confidence=0.9, conn=conn)

        # Check for refund using the new refund_done column + fallback to body text
        for r in rows:
            rdict = dict(r)
            # New rich column: refund_done
            if rdict.get("refund_done") == "Yes":
                enriched["refund_done"] = "Yes"
                enriched["refund_amount"] = rdict.get("refund_amount")
                enriched["refund_date"] = rdict.get("refund_date")
                enriched["refund_reason"] = rdict.get("refund_reason") or rdict.get("return_reason")
                log_enrichment(run_id, amazon_order_id, "archive_returns", "refund_done", None, "Yes", confidence=0.95, conn=conn)
                break
            # Fallback: check raw_body_snippet for "Refund Initiated"
            body = (rdict.get("raw_body_snippet") or "").lower()
            if "refund initiated" in body or "refund of" in body:
                enriched["refund_done"] = "Yes"
                enriched["refund_amount"] = rdict.get("refund_amount")
                enriched["refund_date"] = rdict.get("refund_date")
                enriched["refund_reason"] = rdict.get("refund_reason") or rdict.get("return_reason")
                log_enrichment(run_id, amazon_order_id, "archive_returns", "refund_done", None, "Yes", confidence=0.85, conn=conn)
                break

    # --- archive_messages ---
    # customer_messaged means the CUSTOMER wrote to us. Only inbound rows
    # count — most archive rows are our own outbound tracking emails, which
    # previously flagged 60%+ of orders as "customer messaged".
    rows = conn.execute(
        "SELECT * FROM archive_messages WHERE amazon_order_id = ? ORDER BY email_timestamp DESC",
        (amazon_order_id,),
    ).fetchall()
    inbound = [
        r for r in rows
        if dict(r).get("direction") == "inbound"
        or dict(r).get("message_type") in ("buyer_inquiry", "buyer_reply")
    ]
    if inbound:
        flags["enriched_from_archive"] = 1
        enriched["customer_messaged"] = "Yes"
        enriched["customer_inquiry_count"] = len(inbound)
        log_enrichment(run_id, amazon_order_id, "archive_messages", "customer_messaged", None, "Yes", confidence=0.85, conn=conn)

    enriched.update(flags)
    return enriched


# ---------------------------------------------------------------------------
# Profit & label
# ---------------------------------------------------------------------------


def calculate_profit(data: dict) -> tuple[float | None, float | None]:
    """Calculate profit_dollars and profit_percent."""
    earnings = _safe_float(data.get("earnings"))
    zoro_total = _safe_float(data.get("zoro_order_total"))
    zoro_tax = _safe_float(data.get("zoro_tax")) or 0.0

    if earnings is None or zoro_total is None:
        return None, None

    profit = earnings - zoro_total - zoro_tax
    margin = (profit / earnings * 100) if earnings > 0 else None
    return round(profit, 2), round(margin, 2) if margin is not None else None


def determine_label(data: dict) -> str:
    """
    Determine order label based on data completeness.
    
    Completed: has earnings, zoro cost, tracking, delivery status delivered
    Incomplete: missing critical data
    Cancelled: return + refund
    """
    return_requested = data.get("return_requested")
    refund_done = data.get("refund_done")

    if return_requested == "Yes" and refund_done == "Yes":
        return "Cancelled"

    earnings = _safe_float(data.get("earnings"))
    zoro_total = _safe_float(data.get("zoro_order_total"))
    tracking = data.get("tracking_ids", "")
    delivery = data.get("delivery_status", "")

    if earnings and zoro_total and tracking and delivery in ("Delivered", "AllDelivered"):
        return "Completed"

    return "Incomplete"


# ---------------------------------------------------------------------------
# Single order pipeline
# ---------------------------------------------------------------------------


def process_single_order(
    conn,
    placer_row: dict,
    run_id: str,
    dry_run: bool = False,
) -> dict:
    """
    Process one order from order_placer_orders into closing_orders.
    Returns dict with stats about what was done.
    """
    amazon_id = placer_row["amazon_order_id"]
    wb_id = placer_row.get("wb_order_id") or ""

    # Start with placer data as the base
    closing_data = {
        "amazon_order_id": amazon_id,
        "merchant_order_id": wb_id,
        "account": placer_row.get("account", "HOANK"),
        "wb_order_id": wb_id,
        "asin": placer_row.get("asin", ""),
        "sku": placer_row.get("sku", ""),
        "title": placer_row.get("title", ""),
        "quantity": placer_row.get("quantity", 0),
        "earnings": placer_row.get("earnings"),
        "amazon_fees": placer_row.get("amazon_fees"),
        "shipping_amt": placer_row.get("shipping_amt"),
        "ship_city": placer_row.get("ship_city", ""),
        "ship_state": placer_row.get("ship_state", ""),
        "ship_postal_code": placer_row.get("ship_postal_code", ""),
        "purchase_date": placer_row.get("purchase_date", ""),
        "ship_by_date": placer_row.get("ship_by_date", ""),
        "deliver_by_date": placer_row.get("deliver_by_date", ""),
        "zoro_order_total": placer_row.get("zoro_order_total"),
        "zoro_tax": placer_row.get("zoro_tax"),
        "profit_dollars": placer_row.get("profit_dollars"),
        "profit_percent": placer_row.get("profit_percent"),
        "coupon_used": placer_row.get("coupon_used", ""),
        "coupon_savings": placer_row.get("coupon_savings", 0.0),
        "tracking_ids": placer_row.get("tracking_ids", ""),
        "shipped_email_time": placer_row.get("shipped_email_time", ""),
        "enriched_from_placer_db": 1,
        "last_enrichment_run": run_id,
    }

    # Enrich from archives
    archive_data = enrich_from_archive(conn, amazon_id, wb_id, run_id)
    closing_data.update(archive_data)

    # Coalesce: archive data wins over placer data if placer data is missing
    # (archive has more complete historical data)
    coalesce_fields = [
        "earnings", "amazon_fees", "shipping_amt",
        "ship_by_date", "purchase_date",
        "zoro_order_total", "zoro_tax", "zoro_buyer_zip",
        "tracking_ids", "shipped_email_time",
    ]
    for field in coalesce_fields:
        current = closing_data.get(field)
        if current is None or current == "":
            archive_val = archive_data.get(field)
            if archive_val is not None and archive_val != "":
                closing_data[field] = archive_val

    # Calculate profit
    profit_dollars, profit_percent = calculate_profit(closing_data)
    if profit_dollars is not None:
        closing_data["profit_dollars"] = profit_dollars
    if profit_percent is not None:
        closing_data["profit_percent"] = profit_percent

    # Determine label
    closing_data["order_label"] = determine_label(closing_data)

    # Write
    if not dry_run:
        upsert_closing_order(closing_data)

    return {
        "amazon_order_id": amazon_id,
        "enriched_from_archive": closing_data.get("enriched_from_archive", 0),
        "label": closing_data["order_label"],
        "profit": closing_data.get("profit_dollars"),
    }


# ---------------------------------------------------------------------------
# Batch pipeline
# ---------------------------------------------------------------------------


def run_pipeline(
    run_id: str | None = None,
    dry_run: bool = False,
    verbose: bool = True,
    batch_size: int = 500,
) -> dict:
    """
    Main entry point: process all order_placer_orders not yet in closing_orders.
    Returns stats dict.
    """
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    today = datetime.now().strftime("%Y-%m-%d")

    if not dry_run:
        start_closing_run(run_id, date_from=today, date_to=today, accounts=["HOANK", "Hathoro"])

    try:
        with get_conn() as conn:
            # Count pending
            pending = conn.execute("""
                SELECT COUNT(*) FROM order_placer_orders p
                LEFT JOIN closing_orders c ON p.amazon_order_id = c.amazon_order_id
                WHERE c.amazon_order_id IS NULL
            """).fetchone()[0]

            if verbose:
                print(f"Run ID: {run_id}")
                print(f"Pending orders to process: {pending}")

            if pending == 0:
                if not dry_run:
                    finish_closing_run(run_id)
                return {"run_id": run_id, "processed": 0, "enriched": 0, "errors": 0}

            # Process in batches
            processed = 0
            enriched = 0
            errors = 0

            while True:
                rows = conn.execute("""
                    SELECT p.* FROM order_placer_orders p
                    LEFT JOIN closing_orders c ON p.amazon_order_id = c.amazon_order_id
                    WHERE c.amazon_order_id IS NULL
                    LIMIT ?
                """, (batch_size,)).fetchall()

                if not rows:
                    break

                for row in rows:
                    try:
                        placer_dict = dict(row)
                        result = process_single_order(conn, placer_dict, run_id, dry_run=dry_run)
                        processed += 1
                        if result.get("enriched_from_archive"):
                            enriched += 1
                    except Exception as e:
                        errors += 1
                        if verbose:
                            rowdict = dict(row)
                            print(f"  ⚠️  Error processing {rowdict.get('amazon_order_id', '?')}: {e}")

                if verbose:
                    print(f"  Processed {processed}/{pending}...", end="\r")

        if not dry_run:
            update_closing_run(run_id, {
                "orders_processed": processed,
                "orders_enriched": enriched,
                "archive_hits": enriched,
                "placer_db_hits": processed,
                "errors": errors,
            })
            finish_closing_run(run_id)

        if verbose:
            print(f"\n✅ Pipeline complete: {processed} processed, {enriched} enriched, {errors} errors")

        return {
            "run_id": run_id,
            "processed": processed,
            "enriched": enriched,
            "errors": errors,
        }

    except Exception as e:
        if not dry_run:
            finish_closing_run(run_id, status="failed")
        raise


def run_backfill_legacy(
    run_id: str | None = None,
    dry_run: bool = False,
    verbose: bool = True,
    batch_size: int = 500,
) -> dict:
    """
    Backfill closing_orders from legacy `orders` table.
    Only processes rows not yet in closing_orders.
    """
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    today = datetime.now().strftime("%Y-%m-%d")

    if not dry_run:
        start_closing_run(run_id, date_from=today, date_to=today, accounts=["legacy_backfill"])

    try:
        with get_conn() as conn:
            # Check if legacy table exists
            table_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='orders'"
            ).fetchone()
            if not table_exists:
                if verbose:
                    print("Legacy 'orders' table not found.")
                if not dry_run:
                    finish_closing_run(run_id)
                return {"run_id": run_id, "processed": 0, "errors": 0}

            pending = conn.execute("""
                SELECT COUNT(*) FROM orders o
                LEFT JOIN closing_orders c ON o.amazon_order_id = c.amazon_order_id
                WHERE c.amazon_order_id IS NULL
            """).fetchone()[0]

            if verbose:
                print(f"Run ID: {run_id}")
                print(f"Legacy orders to backfill: {pending}")

            if pending == 0:
                if not dry_run:
                    finish_closing_run(run_id)
                return {"run_id": run_id, "processed": 0, "errors": 0}

            processed = 0
            errors = 0

            while True:
                rows = conn.execute("""
                    SELECT o.* FROM orders o
                    LEFT JOIN closing_orders c ON o.amazon_order_id = c.amazon_order_id
                    WHERE c.amazon_order_id IS NULL
                    LIMIT ?
                """, (batch_size,)).fetchall()

                if not rows:
                    break

                for row in rows:
                    try:
                        legacy = dict(row)
                        # Map legacy fields to closing_orders
                        closing_data = {
                            "amazon_order_id": legacy.get("amazon_order_id", ""),
                            "merchant_order_id": legacy.get("merchant_order_id", ""),
                            "account": legacy.get("account", "HOANK"),
                            "wb_order_id": legacy.get("wb_order_id", ""),
                            "order_status": legacy.get("order_status", "Unshipped"),
                            "asin": legacy.get("asin", ""),
                            "sku": legacy.get("sku", ""),
                            "quantity": legacy.get("quantity", 0),
                            "item_price": legacy.get("item_price", 0.0),
                            "shipping_price": legacy.get("shipping_price", 0.0),
                            "ship_city": legacy.get("ship_city", ""),
                            "ship_state": legacy.get("ship_state", ""),
                            "ship_postal_code": legacy.get("ship_postal_code", ""),
                            "purchase_date": legacy.get("purchase_date", ""),
                            "ship_by_date": legacy.get("ship_by_date", ""),
                            "deliver_by_date": legacy.get("deliver_by_date", ""),
                            "earnings": legacy.get("your_earnings") or legacy.get("earnings"),
                            "amazon_fees": legacy.get("amazon_fees"),
                            "shipping_amt": legacy.get("shipping_amt"),
                            "zoro_order_total": legacy.get("zoro_order_total"),
                            "zoro_tax": legacy.get("zoro_tax"),
                            "zoro_sku": legacy.get("zoro_sku", ""),
                            "zoro_qty": legacy.get("zoro_qty", 0),
                            "zoro_order_status": legacy.get("zoro_order_status", "Open"),
                            "zoro_payment_card": legacy.get("zoro_payment_card", ""),
                            "zoro_buyer_zip": legacy.get("zoro_buyer_zip", ""),
                            "tracking_ids": legacy.get("tracking_ids", ""),
                            "delivery_status": legacy.get("delivery_status", ""),
                            "delivery_date": legacy.get("delivery_date", ""),
                            "shipped_email_time": legacy.get("shipped_email_time", ""),
                            "return_requested": legacy.get("return_requested", ""),
                            "refund_done": legacy.get("refund_done", ""),
                            "customer_messaged": legacy.get("customer_messaged", ""),
                            "delivered_msg_sent": legacy.get("delivered_msg_sent", ""),
                            "review_requested": legacy.get("review_requested", ""),
                            "order_label": legacy.get("order_label", "Incomplete"),
                            "comments": legacy.get("comments", ""),
                            "last_enrichment_run": run_id,
                        }

                        # Recalculate profit if missing
                        profit = closing_data.get("profit_dollars")
                        if profit is None:
                            pd, pp = calculate_profit(closing_data)
                            if pd is not None:
                                closing_data["profit_dollars"] = pd
                            if pp is not None:
                                closing_data["profit_percent"] = pp

                        if not dry_run:
                            upsert_closing_order(closing_data)
                        processed += 1
                    except Exception as e:
                        errors += 1
                        if verbose:
                            rowdict = dict(row)
                            print(f"  ⚠️  Error backfilling {rowdict.get('amazon_order_id', '?')}: {e}")

                if verbose:
                    print(f"  Backfilled {processed}/{pending}...", end="\r")

        if not dry_run:
            update_closing_run(run_id, {
                "orders_processed": processed,
                "errors": errors,
            })
            finish_closing_run(run_id)

        if verbose:
            print(f"\n✅ Legacy backfill complete: {processed} processed, {errors} errors")

        return {
            "run_id": run_id,
            "processed": processed,
            "errors": errors,
        }

    except Exception as e:
        if not dry_run:
            finish_closing_run(run_id, status="failed")
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Closing pipeline: enrich orders → closing_orders")
    parser.add_argument("--run-id", help="Unique run identifier")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--backfill-legacy", action="store_true", help="Also backfill from legacy orders table")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    args = parser.parse_args()

    stats = run_pipeline(
        run_id=args.run_id,
        dry_run=args.dry_run,
        verbose=not args.quiet,
    )

    if args.backfill_legacy:
        legacy_stats = run_backfill_legacy(
            run_id=args.run_id or stats["run_id"],
            dry_run=args.dry_run,
            verbose=not args.quiet,
        )
        stats["legacy_backfill"] = legacy_stats

    return 0 if stats.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
