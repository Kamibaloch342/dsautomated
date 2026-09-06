#!/usr/bin/env python3
"""
test_pipeline_orders.py -- FULL closing pipeline on 6 HOANK test orders.

Populates:
  - closing_orders (archive + SC + FedEx + profit + label)
  - amazon_order_details (rich SC page data)
  - order_messages (all messages from archive)
  - order_returns (return/refund details from archive)
  - order_feedback (feedback scraped from SC page)

Logs all raw outputs to Final_Testing_Logs/ for debugging.

Usage:
    cd /Users/kamranbaloch342/Desktop/Dropshipping/Development_Unit/Automations
    source ../.venv/bin/activate
    python scripts/test_pipeline_orders.py
"""

from __future__ import annotations

import os
import sys
import json
import time
import logging
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

from utils.unified_db import get_conn, upsert_closing_order
from engine.amazon_scraper import AmazonScraper
from engine.fedex_api import FedExTracker
from engine.closing_pipeline import enrich_from_archive as _pipeline_enrich_from_archive

# Live Gmail fallback import
from engine.live_gmail_per_order import search_order_in_gmail

# -- Test orders --------------------------------------------------------------
TEST_ORDERS = [
    ("112-8246579-0124267", "WB4897952132"),
    ("112-5802763-4772250", "WB2135751482"),
    ("114-6547048-4310631", "WB6252819735"),
    ("113-1804611-6381827", "WB3869297458"),
    ("114-0632174-2905805", "WB7784892967"),
    ("111-1105746-6925831", "WB5944614132"),
]

LOG_ROOT = os.path.join(SCRIPT_DIR, "Final_Testing_Logs")
RUN_ID = datetime.now().strftime("%Y%m%d-%H%M%S")

# -- Setup logging ------------------------------------------------------------
os.makedirs(LOG_ROOT, exist_ok=True)
run_log_dir = os.path.join(LOG_ROOT, f"run_{RUN_ID}")
os.makedirs(run_log_dir, exist_ok=True)

log_path = os.path.join(run_log_dir, "pipeline.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("test_pipeline")


# -- Helpers ------------------------------------------------------------------
def log_json(path: str, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def log_text(path: str, text: str):
    with open(path, "w") as f:
        f.write(text)


# -- Archive enrichment -------------------------------------------------------
def enrich_from_archive(conn, amazon_id: str, wb_id: str) -> dict:
    """Enrich closing_orders fields from archive_* tables."""
    enriched: dict = {}

    # Earnings
    row = conn.execute(
        "SELECT * FROM archive_earnings WHERE amazon_order_id = ? ORDER BY email_timestamp DESC LIMIT 1",
        (amazon_id,),
    ).fetchone()
    if row:
        enriched["earnings"] = row["total_your_earnings"]
        enriched["amazon_fees"] = row["total_amazon_fees"]
        enriched["shipping_amt"] = row["total_shipping"]
        enriched["sold_email_time"] = row["email_timestamp"]

    # Zoro
    if wb_id:
        row = conn.execute(
            "SELECT * FROM archive_zoro WHERE wb_order_id = ? ORDER BY email_timestamp DESC LIMIT 1",
            (wb_id,),
        ).fetchone()
        if row:
            enriched["zoro_account_name"] = row["zoro_account_name"]
            enriched["zoro_gmail_account"] = row["gmail_account"]
            enriched["zoro_po_number"] = row["po_number"]
            enriched["zoro_order_status"] = row["order_status"]
            enriched["zoro_sku"] = row["sku"]
            enriched["zoro_qty"] = row["qty"]
            enriched["zoro_order_date"] = row["order_date"]
            enriched["zoro_email_ts"] = row["email_timestamp"]
            enriched["zoro_payment_card"] = row["payment_card_last4"]
            enriched["zoro_tax"] = row["tax"]
            enriched["zoro_order_total"] = row["order_total"]
            enriched["zoro_buyer_zip"] = row["ship_zip"]
            enriched["coupon_savings"] = row["coupon_savings"]

    # Tracking
    if wb_id:
        row = conn.execute(
            "SELECT * FROM archive_tracking WHERE wb_order_id = ? ORDER BY email_timestamp DESC LIMIT 1",
            (wb_id,),
        ).fetchone()
        if row:
            enriched["tracking_ids"] = row["tracking_ids"]
            enriched["shipped_email_time"] = row["email_timestamp"]
            enriched["carrier"] = row["carrier"]

    # Returns — use fixed logic from closing_pipeline
    rows = conn.execute(
        "SELECT * FROM archive_returns WHERE amazon_order_id = ? ORDER BY email_timestamp DESC",
        (amazon_id,),
    ).fetchall()
    if rows:
        enriched["return_requested"] = "Yes"
        for r in rows:
            rdict = dict(r)
            if rdict.get("refund_done") == "Yes":
                enriched["refund_done"] = "Yes"
                enriched["refund_amount"] = rdict.get("refund_amount")
                enriched["refund_date"] = rdict.get("refund_date")
                enriched["refund_reason"] = rdict.get("refund_reason") or rdict.get("return_reason")
                break
            body = (rdict.get("raw_body_snippet") or "").lower()
            if "refund initiated" in body or "refund of" in body:
                enriched["refund_done"] = "Yes"
                enriched["refund_amount"] = rdict.get("refund_amount")
                enriched["refund_date"] = rdict.get("refund_date")
                enriched["refund_reason"] = rdict.get("refund_reason") or rdict.get("return_reason")
                break

    # Messages — use new direction column
    rows = conn.execute(
        "SELECT * FROM archive_messages WHERE amazon_order_id = ? ORDER BY email_timestamp DESC",
        (amazon_id,),
    ).fetchall()
    if rows:
        enriched["customer_messaged"] = "Yes"
        inbound = sum(1 for r in rows if dict(r).get("direction") == "inbound")
        if inbound > 0:
            enriched["customer_inquiry_count"] = inbound

    return enriched


# -- Live Gmail fallback ------------------------------------------------------
def _live_gmail_fallback(amazon_id: str, enriched: dict) -> dict:
    """If archive is missing critical data, query live Gmail for THIS specific order."""
    missing = []
    if enriched.get("earnings") is None:
        missing.append("earnings")
    if enriched.get("zoro_order_total") is None:
        missing.append("zoro")
    if enriched.get("tracking_ids") is None:
        missing.append("tracking")
    if enriched.get("customer_messaged") is None:
        missing.append("messages")
    if enriched.get("return_requested") is None:
        missing.append("returns")
    
    if not missing:
        return enriched
    
    logger.info("  Live Gmail fallback for %s (missing: %s)", amazon_id, ", ".join(missing))
    
    try:
        live_data = search_order_in_gmail(amazon_id)
        if live_data:
            # Remove internal keys
            live_data.pop("_source_account", None)
            enriched.update(live_data)
            logger.info("  Live Gmail found: %s", ", ".join(live_data.keys()))
    except Exception as e:
        logger.debug("Live Gmail fallback failed for %s: %s", amazon_id, e)
    
    return enriched


def save_archive_messages(conn, amazon_id: str, wb_id: str, account: str):
    """Copy all matching messages from archive_messages -> order_messages."""
    rows = conn.execute(
        "SELECT * FROM archive_messages WHERE raw_body_snippet LIKE ? OR raw_body_snippet LIKE ? ORDER BY email_timestamp",
        (f"%{amazon_id}%", f"%{wb_id}%"),
    ).fetchall()
    for r in rows:
        conn.execute("""
            INSERT INTO order_messages (
                amazon_order_id, account, message_type, direction, subject,
                body_snippet, sender, timestamp, gmail_message_id, gmail_account, source
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT DO NOTHING
        """, (
            amazon_id, account, "other", "inbound", "",
            r["raw_body_snippet"][:2000], "", r["email_timestamp"],
            "", r["gmail_account"], "gmail_archive"
        ))
    return len(rows)


def save_archive_returns(conn, amazon_id: str, wb_id: str, account: str):
    """Copy return details from archive_returns -> order_returns."""
    rows = conn.execute(
        "SELECT * FROM archive_returns WHERE amazon_order_id = ? ORDER BY email_timestamp",
        (amazon_id,),
    ).fetchall()
    for r in rows:
        conn.execute("""
            INSERT INTO order_returns (
                amazon_order_id, wb_order_id, account, return_reason, return_tracking,
                return_carrier, refund_amount, refund_date, source, raw_email_snippet
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT DO NOTHING
        """, (
            amazon_id, wb_id, account,
            "", "", "",
            None, "", "gmail_archive",
            r["raw_body_snippet"][:2000]
        ))
    return len(rows)


# -- Math / labels ------------------------------------------------------------
def calculate_profit(data: dict) -> tuple:
    earnings = data.get("earnings") or 0
    zoro_total = data.get("zoro_order_total") or 0
    zoro_tax = data.get("zoro_tax") or 0
    coupon = data.get("coupon_savings") or 0

    profit_dollars = earnings - zoro_total - zoro_tax + coupon
    profit_percent = 0
    if zoro_total > 0:
        profit_percent = round((profit_dollars / zoro_total) * 100, 2)
    elif earnings > 0:
        profit_percent = round((profit_dollars / earnings) * 100, 2)

    return round(profit_dollars, 2), profit_percent


def determine_label(data: dict) -> str:
    if data.get("return_requested") == "Yes" and data.get("refund_done") == "Yes":
        return "Cancelled"
    if data.get("earnings") and data.get("zoro_order_total") and data.get("tracking_ids"):
        if data.get("delivery_status") in ("Delivered", "AllDelivered"):
            return "Completed"
    return "Incomplete"


# -- FedEx --------------------------------------------------------------------
def run_fedex_check(tracking_id: str, order_dir: str) -> dict:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    if not tracking_id:
        return {"status": "NoTracking"}

    opts = Options()
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    system_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(system_chrome):
        opts.binary_location = system_chrome

    driver = webdriver.Chrome(options=opts)
    try:
        tracker = FedExTracker(driver=driver)
        result = tracker.track_batch_via_browser([tracking_id])[0]
        log_json(os.path.join(order_dir, "fedex_result.json"), result)
        return result
    finally:
        driver.quit()


# -- Main per-order processor -------------------------------------------------
def process_order(amazon_id: str, wb_id: str, scraper: AmazonScraper, order_idx: int):
    order_dir = os.path.join(run_log_dir, f"{order_idx:02d}_{amazon_id}")
    os.makedirs(order_dir, exist_ok=True)
    logger.info("=" * 60)
    logger.info("[%d/6] Processing %s | WB %s", order_idx, amazon_id, wb_id)

    with get_conn() as conn:
        # 1. Base record
        closing_data = {
            "amazon_order_id": amazon_id,
            "merchant_order_id": wb_id,
            "account": "HOANK",
            "wb_order_id": wb_id,
            "last_enrichment_run": f"test_{RUN_ID}",
        }

        # 2. Archive enrichment
        archive_data = enrich_from_archive(conn, amazon_id, wb_id)
        closing_data.update(archive_data)

        # 2b. Live Gmail fallback for missing data
        archive_data = _live_gmail_fallback(amazon_id, archive_data)
        closing_data.update(archive_data)

        log_json(os.path.join(order_dir, "archive_enrichment.json"), archive_data)

        # 3. Messages + Returns tables
        msg_count = save_archive_messages(conn, amazon_id, wb_id, "HOANK")
        ret_count = save_archive_returns(conn, amazon_id, wb_id, "HOANK")
        logger.info("  Messages copied: %d | Returns copied: %d", msg_count, ret_count)

        # 4. Amazon SC scrape
        scrape_result = scraper.scrape_order(amazon_id)
        scrape_result["amazon_order_id"] = amazon_id
        log_json(os.path.join(order_dir, "scrape_result.json"), scrape_result)
        log_text(os.path.join(order_dir, "page_body.txt"), scrape_result.get("page_body_snippet", ""))

        for key in ["purchase_date", "ship_by_date", "deliver_by_date", "asin", "sku"]:
            if scrape_result.get(key):
                closing_data[key] = scrape_result[key]
        closing_data["scraped_at"] = scrape_result.get("scraped_at", "")
        closing_data["enriched_from_scrape"] = 1

        # 5. FedEx
        tracking_id = str(closing_data.get("tracking_ids", "") or "").strip()
        fedex_result = run_fedex_check(tracking_id, order_dir)
        closing_data["delivery_status"] = fedex_result.get("status", "Unknown")
        closing_data["delivery_date"] = fedex_result.get("delivery_date", "")
        closing_data["left_at"] = fedex_result.get("left_at", "")
        closing_data["signed_by"] = fedex_result.get("signed_by", "")
        closing_data["enriched_from_fedex"] = 1

        # 6. Profit + label
        pd, pp = calculate_profit(closing_data)
        closing_data["profit_dollars"] = pd
        closing_data["profit_percent"] = pp
        closing_data["order_label"] = determine_label(closing_data)

        # 7. Save everything (reuse open conn to avoid "database is locked")
        upsert_closing_order(closing_data, conn=conn)
        scraper.save_to_db(scrape_result, conn=conn)

        # 8. Final snapshot
        final = dict(conn.execute("SELECT * FROM closing_orders WHERE amazon_order_id = ?", (amazon_id,)).fetchone())
        log_json(os.path.join(order_dir, "closing_final.json"), final)

        logger.info(
            "  DONE -> label=%s profit=$%s del=%s",
            final.get("order_label"),
            final.get("profit_dollars"),
            final.get("delivery_status"),
        )
        return final


def main():
    logger.info("=" * 60)
    logger.info("FULL PIPELINE TEST RUN: %s", RUN_ID)
    logger.info("Orders: %d", len(TEST_ORDERS))
    logger.info("Log directory: %s", run_log_dir)
    logger.info("=" * 60)

    scraper = AmazonScraper(port=9224)

    # ── Login helper ────────────────────────────────────────────────────────────
    def _prompt_login(msg: str):
        print("\n" + "="*60)
        print(msg)
        print("Please log in to the Chrome window that just opened.")
        print("="*60)
        input("\nPress ENTER once you are logged in...\n")

    try:
        current_url = scraper.driver.current_url
        is_new_browser = getattr(scraper, '_own_driver', False)

        # If a brand-new Chrome was launched, navigate to SC and force login prompt
        if is_new_browser:
            logger.info("New Chrome instance detected — navigating to Amazon SC for login")
            scraper.driver.get("https://sellercentral.amazon.com/orders-v3/order/112-8246579-0124267")
            time.sleep(2)
            current_url = scraper.driver.current_url

        # Prompt if we're still on a sign-in or MFA page
        if "signin" in current_url.lower() or "ap/signin" in current_url.lower() or "/mfa" in current_url.lower():
            _prompt_login("Amazon Seller Central login required!")
            # Verify they actually logged in
            time.sleep(1)
            verify_url = scraper.driver.current_url
            if "signin" in verify_url.lower() or "/mfa" in verify_url.lower():
                _prompt_login("Still on login/MFA page. Please complete login.")
    except Exception as e:
        logger.debug("Login check exception (non-critical): %s", e)

    results = []
    try:
        for idx, (amazon_id, wb_id) in enumerate(TEST_ORDERS, 1):
            try:
                result = process_order(amazon_id, wb_id, scraper, idx)
                results.append(result)
            except Exception as e:
                logger.exception("FAILED %s: %s", amazon_id, e)
                results.append({"amazon_order_id": amazon_id, "error": str(e)})
            time.sleep(1)
    finally:
        scraper.close()

    # Summary
    summary = {"run_id": RUN_ID, "orders": results}
    summary_path = os.path.join(run_log_dir, "summary.json")
    log_json(summary_path, summary)

    # Per-table counts
    with get_conn() as conn:
        counts = {
            "closing_orders": conn.execute("SELECT COUNT(*) FROM closing_orders WHERE last_enrichment_run = ?", (f"test_{RUN_ID}",)).fetchone()[0],
            "amazon_order_details": conn.execute("SELECT COUNT(*) FROM amazon_order_details WHERE amazon_order_id IN (" + ",".join("?"*len(TEST_ORDERS)) + ")", [a for a, _ in TEST_ORDERS]).fetchone()[0],
            "order_messages": conn.execute("SELECT COUNT(*) FROM order_messages WHERE amazon_order_id IN (" + ",".join("?"*len(TEST_ORDERS)) + ")", [a for a, _ in TEST_ORDERS]).fetchone()[0],
            "order_returns": conn.execute("SELECT COUNT(*) FROM order_returns WHERE amazon_order_id IN (" + ",".join("?"*len(TEST_ORDERS)) + ")", [a for a, _ in TEST_ORDERS]).fetchone()[0],
            "order_feedback": conn.execute("SELECT COUNT(*) FROM order_feedback WHERE amazon_order_id IN (" + ",".join("?"*len(TEST_ORDERS)) + ")", [a for a, _ in TEST_ORDERS]).fetchone()[0],
        }

    log_json(os.path.join(run_log_dir, "counts.json"), counts)

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info("Rows created:")
    for table, count in counts.items():
        logger.info("  %s: %d", table, count)
    logger.info("Summary: %s", summary_path)


if __name__ == "__main__":
    main()
