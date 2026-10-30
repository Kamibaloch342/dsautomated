#!/usr/bin/env python3
"""
backfill_2026.py — One-shot backfill of ALL 2026 WB orders into closing_orders.

This script:
  1. Downloads Amazon FlatFileAllOrdersReport (or uses existing TSV)
  2. Parses all rows, keeps only WB orders (merchant-order-id starts with "WB")
  3. Consolidates multi-item orders
  4. Enriches from archive_* tables (now fixed with refund/message columns)
  5. Scrapes Amazon SC for dates, buyer info, tracking uploaded
  6. Checks FedEx delivery status
  7. Calculates profit & label
  8. Saves to closing_orders + amazon_order_details
  9. Logs everything

Usage:
    cd /Users/kamranbaloch342/Desktop/Dropshipping/Development_Unit/Automations
    source ../.venv/bin/activate
    python scripts/backfill_2026.py [--report-path PATH] [--dry-run] [--limit N]
"""

from __future__ import annotations

import os
import sys
import re
import csv
import time
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

from utils.unified_db import get_conn, upsert_closing_order
from engine.closing_pipeline import calculate_profit, determine_label
from engine.amazon_scraper import AmazonScraper
from engine.fedex_api import FedExTracker

logger = logging.getLogger("backfill_2026")

# ── Paths ────────────────────────────────────────────────────────────────────
REPORT_DOWNLOAD_URL = "https://sellercentral.amazon.com/reportcentral/FlatFileAllOrdersReport/1"
DEFAULT_DOWNLOAD_DIR = str(Path.home() / "Downloads")

# ── Parse report ─────────────────────────────────────────────────────────────


def find_existing_report(download_dir: str = DEFAULT_DOWNLOAD_DIR) -> str | None:
    """Find the most recent FlatFileAllOrdersReport TSV in Downloads."""
    candidates = []
    for f in os.listdir(download_dir):
        if "FlatFileAllOrdersReport" in f and f.endswith(".txt"):
            candidates.append(os.path.join(download_dir, f))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def parse_report_tsv(path: str) -> list[dict]:
    """Parse Amazon FlatFileAllOrdersReport TSV. Returns list of row dicts."""
    rows = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        # Amazon reports have a header section before the actual TSV
        # Look for the line that starts the TSV header
        header_found = False
        reader = None
        for line in f:
            if not header_found and "order-id" in line.lower():
                header_found = True
                # Reset to read from this line
                f.seek(0)
                # Skip lines until we hit the header
                for skip_line in f:
                    if "order-id" in skip_line.lower():
                        reader = csv.DictReader(f, delimiter="\t")
                        break
                break
        
        if reader is None:
            # Fallback: try reading from start as TSV
            f.seek(0)
            reader = csv.DictReader(f, delimiter="\t")
        
        for row in reader:
            if not row:
                continue
            rows.append(row)
    
    return rows


def filter_wb_orders(rows: list[dict]) -> list[dict]:
    """Keep only rows where merchant-order-id starts with 'WB'."""
    wb_rows = []
    for r in rows:
        merchant_id = r.get("merchant-order-id", "") or r.get("merchant-order-item-id", "")
        if merchant_id and merchant_id.startswith("WB"):
            wb_rows.append(r)
    return wb_rows


def consolidate_multi_item_orders(rows: list[dict]) -> dict[str, dict]:
    """Group by amazon-order-id, sum quantities/prices, collect SKUs/ASINs."""
    orders: dict[str, dict] = {}
    for r in rows:
        oid = r.get("order-id", "").strip()
        if not oid:
            continue
        
        if oid not in orders:
            orders[oid] = {
                "amazon_order_id": oid,
                "merchant_order_id": (r.get("merchant-order-id", "") or r.get("merchant-order-item-id", "")).strip(),
                "account": "HOANK",  # Default; infer from email if needed
                "order_status": r.get("order-status", "Unshipped"),
                "purchase_date": r.get("purchase-date", ""),
                "ship_by_date": r.get("ship-by", ""),
                "deliver_by_date": r.get("deliver-by", ""),
                "ship_city": r.get("ship-city", ""),
                "ship_state": r.get("ship-state", ""),
                "ship_postal_code": r.get("ship-postal-code", ""),
                "ship_country": r.get("ship-country", "US"),
                "sku": [],
                "asin": [],
                "quantity": 0,
                "item_price": 0.0,
                "shipping_price": 0.0,
            }
        
        o = orders[oid]
        sku = r.get("sku", "").strip()
        asin = r.get("asin", "").strip()
        qty_str = r.get("quantity", "0").strip()
        price_str = r.get("item-price", "0").strip()
        ship_str = r.get("shipping-price", "0").strip()
        
        if sku and sku not in o["sku"]:
            o["sku"].append(sku)
        if asin and asin not in o["asin"]:
            o["asin"].append(asin)
        
        try:
            o["quantity"] += int(qty_str) if qty_str else 0
        except ValueError:
            pass
        try:
            o["item_price"] += float(price_str) if price_str else 0.0
        except ValueError:
            pass
        try:
            o["shipping_price"] += float(ship_str) if ship_str else 0.0
        except ValueError:
            pass
    
    # Convert lists to strings
    for o in orders.values():
        o["sku"] = ", ".join(o["sku"])
        o["asin"] = ", ".join(o["asin"])
    
    return orders


# ── Archive enrichment ───────────────────────────────────────────────────────


def enrich_from_archive(conn, amazon_id: str, wb_id: str) -> dict:
    """Enrich from archive_* tables (Phase 1 fixed version)."""
    enriched: dict = {}
    
    # Earnings
    r = conn.execute(
        "SELECT * FROM archive_earnings WHERE amazon_order_id = ? ORDER BY email_timestamp DESC LIMIT 1",
        (amazon_id,),
    ).fetchone()
    if r:
        enriched.update({
            "earnings": r["total_your_earnings"],
            "amazon_fees": r["total_amazon_fees"],
            "shipping_amt": r["total_shipping"],
            "sold_email_time": r["email_timestamp"],
        })
    
    # Zoro
    if wb_id:
        r = conn.execute(
            "SELECT * FROM archive_zoro WHERE wb_order_id = ? ORDER BY email_timestamp DESC LIMIT 1",
            (wb_id,),
        ).fetchone()
        if r:
            enriched.update({
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
            (wb_id,),
        ).fetchone()
        if r:
            enriched.update({
                "tracking_ids": r["tracking_ids"],
                "shipped_email_time": r["email_timestamp"],
                "carrier": r["carrier"],
            })
    
    # Returns (FIXED)
    rows = conn.execute(
        "SELECT * FROM archive_returns WHERE amazon_order_id = ? ORDER BY email_timestamp DESC",
        (amazon_id,),
    ).fetchall()
    if rows:
        enriched["return_requested"] = "Yes"
        for row in rows:
            if row["refund_done"] == "Yes" or "refund initiated" in (row["raw_body_snippet"] or "").lower():
                enriched["refund_done"] = "Yes"
                enriched["refund_amount"] = row["refund_amount"]
                enriched["refund_date"] = row["refund_date"]
                enriched["refund_reason"] = row["refund_reason"] or row["return_reason"]
                break
    
    # Messages (FIXED)
    rows = conn.execute(
        "SELECT * FROM archive_messages WHERE amazon_order_id = ? ORDER BY email_timestamp DESC",
        (amazon_id,),
    ).fetchall()
    if rows:
        enriched["customer_messaged"] = "Yes"
        inbound = sum(1 for r in rows if r["direction"] == "inbound")
        if inbound > 0:
            enriched["customer_inquiry_count"] = inbound
    
    return enriched


# ── SC scrape + FedEx ────────────────────────────────────────────────────────


def run_scrape_and_fedex(amazon_id: str, wb_id: str, scraper: AmazonScraper, tracker: FedExTracker) -> dict:
    """Scrape SC page and check FedEx. Returns dict with scraped data."""
    result = {}
    
    # SC scrape
    try:
        scrape = scraper.scrape_order(amazon_id)
        result["purchase_date"] = scrape.get("purchase_date")
        result["ship_by_date"] = scrape.get("ship_by_date")
        result["deliver_by_date"] = scrape.get("deliver_by_date")
        result["buyer_name"] = scrape.get("buyer_name")
        result["buyer_email"] = scrape.get("buyer_email")
        result["ship_to_name"] = scrape.get("ship_to_name")
        result["ship_to_city"] = scrape.get("ship_to_city")
        result["ship_to_state"] = scrape.get("ship_to_state")
        result["ship_to_zip"] = scrape.get("ship_to_zip")
        result["payment_method"] = scrape.get("payment_method")
        result["order_subtotal"] = scrape.get("order_subtotal")
        result["order_total"] = scrape.get("order_total")
        result["tracking_id_uploaded"] = scrape.get("tracking_id_uploaded")
        result["tracking_carrier_uploaded"] = scrape.get("tracking_carrier_uploaded")
        result["scraped_at"] = scrape.get("scraped_at")
        result["enriched_from_scrape"] = 1
    except Exception as e:
        logger.warning("SC scrape failed for %s: %s", amazon_id, e)
    
    # FedEx check
    tracking_id = result.get("tracking_ids") or wb_id  # fallback
    if tracking_id:
        try:
            # Use browser fallback for 403 errors
            fedex_result = tracker.track_batch_via_browser([tracking_id])[0]
            result["delivery_status"] = fedex_result.get("status", "Unknown")
            result["delivery_date"] = fedex_result.get("delivery_date")
            result["left_at"] = fedex_result.get("left_at")
            result["signed_by"] = fedex_result.get("signed_by")
            result["enriched_from_fedex"] = 1
        except Exception as e:
            logger.warning("FedEx check failed for %s: %s", amazon_id, e)
    
    return result


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Backfill 2026 WB orders into closing_orders")
    parser.add_argument("--report-path", help="Path to existing FlatFileAllOrdersReport TSV")
    parser.add_argument("--download", action="store_true", help="Download report via Selenium (requires login)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--limit", type=int, default=0, help="Limit to N orders (0 = all)")
    parser.add_argument("--scrape", action="store_true", help="Scrape Amazon SC for each order (slow)")
    parser.add_argument("--fedex", action="store_true", help="Check FedEx for each order (slow)")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    
    print("=" * 70)
    print("2026 BACKFILL")
    print("=" * 70)
    
    # 1. Get report
    report_path = args.report_path
    if not report_path:
        report_path = find_existing_report()
    
    if not report_path:
        if args.download:
            print("Downloading report via Selenium...")
            # TODO: implement download logic
            print("Download not yet implemented. Please download manually.")
            sys.exit(1)
        else:
            print("No report found. Please specify --report-path or use --download")
            sys.exit(1)
    
    print(f"Using report: {report_path}")
    
    # 2. Parse
    print("\nParsing report...")
    all_rows = parse_report_tsv(report_path)
    print(f"  Total rows: {len(all_rows)}")
    
    wb_rows = filter_wb_orders(all_rows)
    print(f"  WB orders: {len(wb_rows)}")
    
    orders = consolidate_multi_item_orders(wb_rows)
    print(f"  Unique orders: {len(orders)}")
    
    # 3. Filter 2026
    orders_2026 = {}
    for oid, o in orders.items():
        pd = o.get("purchase_date", "")
        if pd.startswith("2026") or (pd and "2026" in pd):
            orders_2026[oid] = o
    
    print(f"  2026 orders: {len(orders_2026)}")
    
    if args.limit > 0:
        limited = dict(list(orders_2026.items())[:args.limit])
        orders_2026 = limited
        print(f"  Limited to: {args.limit}")
    
    # 4. Process
    scraper = None
    tracker = None
    if args.scrape or args.fedex:
        print("\nLaunching Chrome...")
        scraper = AmazonScraper(port=9224)
        if args.fedex:
            tracker = FedExTracker()
    
    conn = get_conn().__enter__()
    
    processed = 0
    enriched = 0
    errors = 0
    skipped = 0
    
    run_id = f"backfill_2026_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    print("\nProcessing orders...")
    for amazon_id, order_data in orders_2026.items():
        try:
            # Check if already complete
            existing = conn.execute(
                "SELECT order_label FROM closing_orders WHERE amazon_order_id = ?",
                (amazon_id,)
            ).fetchone()
            if existing and existing["order_label"] == "Completed":
                skipped += 1
                if args.verbose:
                    print(f"  Skip {amazon_id} — already Completed")
                continue
            
            # Build closing data
            closing_data = dict(order_data)
            closing_data["last_enrichment_run"] = run_id
            
            # Archive enrichment
            wb_id = closing_data.get("merchant_order_id", "")
            archive_data = enrich_from_archive(conn, amazon_id, wb_id)
            closing_data.update(archive_data)
            
            # SC scrape + FedEx
            if scraper:
                scrape_data = run_scrape_and_fedex(amazon_id, wb_id, scraper, tracker)
                closing_data.update(scrape_data)
                if scrape_data.get("enriched_from_scrape"):
                    scraper.save_to_db({"amazon_order_id": amazon_id, **scrape_data}, conn=conn)
            
            # Profit + label
            pd, pp = calculate_profit(closing_data)
            if pd is not None:
                closing_data["profit_dollars"] = pd
            if pp is not None:
                closing_data["profit_percent"] = pp
            closing_data["order_label"] = determine_label(closing_data)
            
            # Save
            if not args.dry_run:
                upsert_closing_order(closing_data, conn=conn)
            
            processed += 1
            if archive_data.get("enriched_from_archive"):
                enriched += 1
            
            if processed % 100 == 0:
                print(f"  ... {processed} processed, {enriched} enriched, {skipped} skipped, {errors} errors", end="\r")
        
        except Exception as e:
            errors += 1
            logger.exception("Error processing %s: %s", amazon_id, e)
    
    print(f"\n  Done: {processed} processed, {enriched} enriched, {skipped} skipped, {errors} errors")
    
    if not args.dry_run:
        conn.commit()
    conn.close()
    
    if scraper:
        scraper.close()
    if tracker:
        tracker.close()
    
    print("\n" + "=" * 70)
    print("BACKFILL COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
