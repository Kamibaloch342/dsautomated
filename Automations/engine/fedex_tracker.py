"""
fedex_tracker.py — Orchestrator for FedEx delivery tracking.

Reads orders from the unified DB, checks FedEx delivery status,
and updates the DB with results.

Supports:
  - Official FedEx REST API (apis.fedex.com, instant pure REST)
  - Headless Chrome CDP batch tracking
  - Headless DOM scraper fallback

Can be run:
  1. As a standalone script: python engine/fedex_tracker.py
  2. From the GUI launcher (new tab)
  3. After tracking upload (automatic check)
"""

from __future__ import annotations

import os
import sys
import time
import argparse
import logging
from datetime import datetime

# Add parent to path
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from utils.unified_db import (
    init_db, get_orders_needing_fedex_check, update_order, log_action,
    get_order, DB_PATH,
    get_closing_orders_needing_fedex_check, upsert_closing_order,
    get_closing_order,
)
from engine.fedex_api import FedExTracker, FedExSessionExpired

logger = logging.getLogger("fedex_tracker")


# ── Chrome connection ────────────────────────────────────────────────────────
def _connect_chrome(port: int = 9223):
    """Connect to an existing Chrome instance on the given debugging port."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"localhost:{port}")
    try:
        driver = webdriver.Chrome(options=opts)
        logger.info("Connected to Chrome on port %d — %s", port, driver.current_url)
        return driver
    except Exception as e:
        logger.warning("Could not connect to Chrome on port %d: %s", port, e)
        return None


def _launch_fedex_chrome(headless: bool = True):
    """Launch a dedicated Chrome for FedEx tracking (lightweight profile)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    )

    # Memory softening
    for _sf in ("--renderer-process-limit=4", "--disable-background-networking",
                "--disable-sync", "--disable-default-apps",
                "--disable-component-update", "--metrics-recording-only",
                "--mute-audio", "--disk-cache-size=104857600"):
        opts.add_argument(_sf)
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    # macOS: use system Chrome (ARM-friendly)
    system_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(system_chrome):
        opts.binary_location = system_chrome

    # Use a lightweight profile
    profile_dir = os.path.join(os.path.expanduser("~"), ".fedex_tracker_profile")
    try:
        from utils.helpers import mark_profile_clean_exit
        mark_profile_clean_exit(profile_dir)
    except Exception:
        pass
    opts.add_argument(f"user-data-dir={profile_dir}")
    opts.add_argument("--hide-crash-restore-bubble")

    driver = webdriver.Chrome(options=opts)
    try:
        from utils.helpers import apply_cdp_stealth
        apply_cdp_stealth(driver)
    except Exception:
        pass
    logger.info("Launched dedicated %s Chrome for FedEx tracking", "headless" if headless else "headed")
    return driver


# ── Core tracking loop ──────────────────────────────────────────────────────
def run_fedex_check(
    driver=None,
    port: int = None,
    max_orders: int = 500,
    hours_since_last: int = 2,
    dry_run: bool = False,
    closing_table: bool = False,
    headless: bool = True,
) -> dict:
    """
    Main entry point. Checks FedEx delivery status for orders in unified DB.

    Returns stats dict: {"checked": N, "updated": N, "delivered": N, "errors": N}
    """
    print("\n📦 FEDEX DELIVERY TRACKER")
    print("─" * 50)

    # Init DB
    init_db()

    # Get orders needing check
    if closing_table:
        orders = get_closing_orders_needing_fedex_check(hours_since_last=hours_since_last, limit=max_orders)
    else:
        orders = get_orders_needing_fedex_check(hours_since_last=hours_since_last)
        orders = orders[:max_orders]
    if not orders:
        print("  ⏩ No orders need FedEx checking right now.")
        return {"checked": 0, "updated": 0, "delivered": 0, "errors": 0}

    table_name = "closing_orders" if closing_table else "orders"
    print(f"  🔍 {len(orders)} orders to check from {table_name}")

    stats = {"checked": 0, "updated": 0, "delivered": 0, "errors": 0}

    # Build tracking ID → order mapping
    tracking_to_orders = {}
    for order in orders:
        tids = order.get("tracking_ids", "")
        if not tids:
            continue
        for tid in [t.strip() for t in str(tids).split(",") if t.strip()]:
            tracking_to_orders.setdefault(tid, []).append(order["amazon_order_id"])

    unique_tracking_ids = list(tracking_to_orders.keys())
    print(f"  📋 {len(unique_tracking_ids)} unique tracking IDs")

    if not unique_tracking_ids:
        print("  ⏩ No tracking IDs to check.")
        return stats

    # Initialize tracker
    fedex = FedExTracker(driver=driver, headless=headless)

    if fedex.has_official_api:
        print("  ⚡ Using Official FedEx Developer REST API (zero browser needed)")
    else:
        # If no driver was passed and we have a specific debugging port
        if fedex.driver is None and port is not None:
            fedex.driver = _connect_chrome(port)
        if fedex.driver is None:
            mode_str = "headless" if headless else "headed"
            print(f"  🚀 Initializing dedicated {mode_str} Chrome engine for FedEx...")
            fedex.driver = _launch_fedex_chrome(headless=headless)
            fedex._owns_driver = True

    # Track using the master multi-tier track_batch interface
    print(f"  🔍 Tracking {len(unique_tracking_ids)} packages...")
    results = fedex.track_batch(unique_tracking_ids)

    if not results:
        results = [fedex._empty_detail()] * len(unique_tracking_ids)

    # Update DB with results
    for tid, detail in zip(unique_tracking_ids, results):
        amazon_ids = tracking_to_orders.get(tid, [])
        for amazon_id in amazon_ids:
            stats["checked"] += 1

            if dry_run:
                print(f"     [DRY] {amazon_id} | {tid} → {detail['status']}")
                continue

            # Never downgrade: an unresolved lookup must not overwrite an
            # older, informative status with "Unknown".
            if detail["status"] in ("", "Unknown"):
                continue

            try:
                updates = {
                    "amazon_order_id": amazon_id,
                    "delivery_status": detail["status"],
                    "delivery_date": detail["delivery_date"],
                    "estimated_window": detail["estimated_window"],
                    "last_scan_location": detail["last_scan_location"],
                    "last_scan_time": detail["last_scan_time"],
                    "signed_by": detail["signed_by"],
                    "left_at": detail["left_at"],
                    "delivery_note": detail["delivery_note"],
                    "delivery_from": detail["from_loc"],
                    "delivery_to": detail["to_loc"],
                }
                if closing_table:
                    # Also re-evaluate label
                    current = get_closing_order(amazon_id) or {}
                    label = current.get("order_label", "Incomplete")
                    if detail["status"] == "Delivered":
                        # Check if we have earnings and zoro cost to mark Completed
                        if current.get("earnings") and current.get("zoro_order_total"):
                            label = "Completed"
                    updates["order_label"] = label
                    upsert_closing_order(updates)
                else:
                    update_order(amazon_id, updates, changed_by="fedex_tracker")

                if detail["status"] == "Delivered":
                    stats["delivered"] += 1
                    log_action(amazon_id, "fedex_check", "success",
                               f"Delivered on {detail['delivery_date']} | Tracking: {tid}")
                else:
                    log_action(amazon_id, "fedex_check", "success",
                               f"Status: {detail['status']} | Tracking: {tid}")

                stats["updated"] += 1

            except Exception as e:
                stats["errors"] += 1
                logger.error("Failed to update %s: %s", amazon_id, e)
                log_action(amazon_id, "fedex_check", "failed", str(e))

    # Clean up any browser created by this run
    fedex.close()

    print(f"\n  ✅ Checked: {stats['checked']} | Updated: {stats['updated']} | Delivered: {stats['delivered']} | Errors: {stats['errors']}")
    return stats


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="FedEx delivery tracker for Automations/")
    parser.add_argument("--port", type=int, default=None,
                        help="Chrome debugging port (optional, connects to existing Chrome)")
    parser.add_argument("--max-orders", type=int, default=500,
                        help="Max orders to check per run")
    parser.add_argument("--hours-since-last", type=int, default=2,
                        help="Only check orders not checked in last N hours")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be updated without writing")
    parser.add_argument("--closing", action="store_true",
                        help="Use closing_orders table instead of legacy orders")
    parser.add_argument("--visible", action="store_true",
                        help="Run Chrome in visible (non-headless) mode")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    run_fedex_check(
        port=args.port,
        max_orders=args.max_orders,
        hours_since_last=args.hours_since_last,
        dry_run=args.dry_run,
        closing_table=args.closing,
        headless=not args.visible,
    )


if __name__ == "__main__":
    main()
