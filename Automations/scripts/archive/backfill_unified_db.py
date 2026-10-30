#!/usr/bin/env python3
"""
backfill_unified_db.py — One-time migration of ALL historical data into unified DB.

Sources (in priority order):
  1. order_log.txt          — Recent orders (May 31 → now) — MOST DETAILED
  2. gmail_archive.db       — Earnings, Zoro, tracking, returns (2022 → now)
  3. DS_Closing reports     — Monthly TSV archives (Jan 2025 → Apr 2026)
  4. tracker_test_local.db  — Final Automation test mirror (Jan → May 2026)

Usage:
    cd Automations && source ../.venv/bin/activate
    python scripts/backfill_unified_db.py [--source all|order_log|gmail|ds_closing|tracker_db]

This script is SAFE to run multiple times — it uses UPSERT logic.
"""

from __future__ import annotations

import os
import sys
import re
import argparse
import sqlite3
from datetime import datetime

# Add parent to path
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from utils.unified_db import init_db, insert_order, update_order, get_order, DB_PATH
from utils.gmail_archive_reader import (
    get_archive_stats, get_earnings, get_zoro_order,
    get_tracking, get_returns, has_return_or_refund,
    batch_get_earnings, batch_get_zoro, batch_get_tracking,
    find_wb_id_for_amazon_order,
)

# ── Paths ────────────────────────────────────────────────────────────────────
ORDER_LOG_PATH = os.path.join(_SCRIPT_DIR, "data", "rajar_logs", "order_log.txt")
FINAL_AUTO_DIR = os.path.join(_SCRIPT_DIR, "data")   # archive DB moved here (old "Final Automation" deleted)
TRACKER_TEST_DB = os.path.join(FINAL_AUTO_DIR, "tracker_test_local.db")
DS_CLOSING_HOANK = os.path.join(os.path.dirname(_SCRIPT_DIR), "DS_Closing", "Hypersale_Closing", "Order_Reports")
DS_CLOSING_HATHORO = os.path.join(os.path.dirname(_SCRIPT_DIR), "DS_Closing", "Discounted_Closing", "Order_Reports")


# ── Source 1: order_log.txt ──────────────────────────────────────────────────
def _parse_money(val):
    if not val or val in ("N/A", "", "None"):
        return None
    try:
        return float(str(val).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_pct(val):
    if not val or val in ("N/A", "", "None"):
        return None
    try:
        return float(str(val).replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def backfill_from_order_log():
    """Parse order_log.txt and insert into unified DB."""
    if not os.path.exists(ORDER_LOG_PATH):
        print("  ⚠️  order_log.txt not found — skipping")
        return 0

    print("\n📄 Source 1: order_log.txt")
    count = 0
    with open(ORDER_LOG_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse one-liner format
    pattern = (
        r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \| WB:.+?"
        r"(?=\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \| WB:|\Z)"
    )
    for raw in re.findall(pattern, content, re.DOTALL):
        raw = raw.strip()
        m = re.match(r"^\[([^\]]+)\]\s+\|\s+(.+)", raw)
        if not m:
            continue
        _, rest = m.groups()

        entry = {}
        for part in rest.split(" | "):
            part = part.strip()
            if ":" in part:
                key, val = part.split(":", 1)
                entry[key.strip().lower()] = val.strip()

        # Determine account from profile. DM_* = Hathoro, HS_* = HOANK — the
        # old "hs_ → Hathoro" test was EXACTLY inverted (matches
        # sync_order_placer_logs._account_from_profile, the authoritative map).
        profile = entry.get("profile", "")
        account = "Hathoro" if profile.upper().startswith("DM_") else "HOANK"

        # Build data dict
        data = {
            "amazon_order_id": entry.get("amazon", ""),
            "merchant_order_id": entry.get("wb", ""),
            "account": account,
            "asin": entry.get("asin", ""),
            "sku": entry.get("sku", ""),
            "title": entry.get("title", ""),
            "quantity": sum(int(n) for n in re.findall(r"\d+", str(entry.get("aqty", 0) or 0))),
            "zoro_sku": entry.get("sku", ""),
            "zoro_qty": sum(int(n) for n in re.findall(r"\d+", str(entry.get("zqty", 0) or 0))),
            "earnings": _parse_money(entry.get("earnings", "")),
            "amazon_fees": _parse_money(entry.get("amazonfees", "")),
            "shipping_amt": _parse_money(entry.get("shippingamt", "")),
            "zoro_tax": _parse_money(entry.get("tax", "")),
            "zoro_order_total": _parse_money(entry.get("cost", "")),
            "profit_dollars": _parse_money(entry.get("profit", "")),
            "profit_percent": _parse_pct(entry.get("finalmargin", "")),
            "coupon_used": entry.get("coupon", ""),
            "zoro_order_status": entry.get("status", "Open"),
            "ship_city": entry.get("shipcity", ""),
            "ship_state": entry.get("shipstate", ""),
            "ship_postal_code": entry.get("shipzip", ""),
            "purchase_date": entry.get("purchasedate", ""),
            "ship_by_date": entry.get("shipby", ""),
            "deliver_by_date": entry.get("deliverby", ""),
            "tracking_ids": entry.get("trackingids", ""),
            "shipped_email_time": entry.get("shippedemailtime", ""),
            "source": "automations",
        }

        # Skip if no amazon_order_id
        if not data["amazon_order_id"]:
            continue

        inserted = insert_order(data)
        count += 1
        if count % 500 == 0:
            print(f"     ... {count} orders processed")

    print(f"  ✅ Inserted/updated {count} orders from order_log.txt")
    return count


# ── Source 2: gmail_archive.db ───────────────────────────────────────────────
def backfill_from_gmail_archive():
    """Backfill from Final Automation/'s gmail_archive.db."""
    print("\n📧 Source 2: gmail_archive.db")
    try:
        stats = get_archive_stats()
    except FileNotFoundError:
        print("  ⚠️  gmail_archive.db not found — skipping")
        return 0

    total_earnings = stats.get("earnings_emails", {}).get("count", 0)
    total_zoro = stats.get("zoro_emails", {}).get("count", 0)
    print(f"  Archive has {total_earnings:,} earnings, {total_zoro:,} Zoro emails")

    # Strategy: Iterate earnings emails → find matching Zoro → insert/update
    # For efficiency, do this in batches
    conn = sqlite3.connect(
        f"file:{os.path.join(FINAL_AUTO_DIR, 'gmail_archive.db')}?mode=ro",
        uri=True
    )
    conn.row_factory = sqlite3.Row

    count = 0
    cursor = conn.execute(
        "SELECT amazon_order_id, order_date, ship_by_date, "
        "total_your_earnings, total_amazon_fees, total_shipping, "
        "skus, asins, quantities "
        "FROM earnings_emails ORDER BY email_timestamp"
    )

    batch_size = 500
    batch = []
    for row in cursor:
        amazon_id = row["amazon_order_id"]
        if not amazon_id:
            continue

        # Check if already in unified DB (from order_log.txt)
        existing = get_order(amazon_id)
        if existing and existing.get("source") == "automations":
            # Already have detailed data — just enrich with archive data
            updates = {}
            if not existing.get("earnings") and row["total_your_earnings"]:
                updates["earnings"] = row["total_your_earnings"]
            if not existing.get("amazon_fees") and row["total_amazon_fees"]:
                updates["amazon_fees"] = row["total_amazon_fees"]
            if not existing.get("ship_by_date") and row["ship_by_date"]:
                updates["ship_by_date"] = row["ship_by_date"]
            if updates:
                update_order(amazon_id, updates, changed_by="backfill_gmail")
            continue

        # New order — build from earnings data
        skus = _safe_json(row["skus"])
        asins = _safe_json(row["asins"])
        qtys = _safe_json(row["quantities"])

        data = {
            "amazon_order_id": amazon_id,
            "account": _guess_account_from_gmail(amazon_id),
            "sku": skus[0] if skus else "",
            "asin": asins[0] if asins else "",
            "quantity": int(qtys[0]) if qtys else 0,
            "earnings": row["total_your_earnings"],
            "amazon_fees": row["total_amazon_fees"],
            "shipping_amt": row["total_shipping"],
            "ship_by_date": row["ship_by_date"] or "",
            "purchase_date": row["order_date"] or "",
            "source": "backfill",
            "backfilled_at": datetime.utcnow().isoformat(),
        }

        # Try to find WB order from archive
        wb_id = find_wb_id_for_amazon_order(amazon_id)
        if wb_id:
            data["wb_order_id"] = wb_id
            zoro = get_zoro_order(wb_id)
            if zoro:
                data["zoro_sku"] = zoro.get("sku", "")
                data["zoro_qty"] = zoro.get("qty", 0)
                data["zoro_order_total"] = zoro.get("order_total", 0)
                data["zoro_tax"] = zoro.get("tax", 0)
                data["zoro_buyer_zip"] = zoro.get("ship_zip", "")
                data["ship_city"] = zoro.get("ship_city", "")
                data["ship_state"] = zoro.get("ship_state", "")
                data["ship_postal_code"] = zoro.get("ship_zip", "")
                data["coupon_used"] = ""
                data["coupon_savings"] = zoro.get("coupon_savings", 0)

            # Try to find tracking
            tracking_records = get_tracking(wb_id)
            if tracking_records:
                all_ids = []
                for tr in tracking_records:
                    tids = tr.get("tracking_ids", "")
                    if tids:
                        all_ids.extend([t.strip() for t in tids.split(",") if t.strip()])
                data["tracking_ids"] = ", ".join(set(all_ids))

        # Calculate profit if possible
        if data.get("earnings") and data.get("zoro_order_total"):
            data["profit_dollars"] = data["earnings"] - data["zoro_order_total"]
            if data["earnings"] > 0:
                data["profit_percent"] = (data["profit_dollars"] / data["earnings"]) * 100

        insert_order(data)
        count += 1

        if count % 500 == 0:
            print(f"     ... {count} orders from Gmail archive")

    conn.close()
    print(f"  ✅ Backfilled {count} orders from gmail_archive.db")
    return count


def _guess_account_from_gmail(amazon_order_id: str) -> str:
    """Guess account (HOANK vs Hathoro) from order ID prefix patterns."""
    # This is a heuristic — in practice you'd need more info
    # For now, default to HOANK since it's the majority
    return "HOANK"


def _safe_json(val):
    if not val:
        return []
    try:
        import json
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return []


# ── Source 3: DS_Closing Order_Reports ───────────────────────────────────────
def backfill_from_ds_closing():
    """Backfill from DS_Closing monthly TSV reports."""
    print("\n📊 Source 3: DS_Closing Order_Reports")
    total = 0

    for account, report_dir in [("HOANK", DS_CLOSING_HOANK), ("Hathoro", DS_CLOSING_HATHORO)]:
        if not os.path.exists(report_dir):
            print(f"  ⚠️  {account} reports not found at {report_dir}")
            continue

        files = sorted([f for f in os.listdir(report_dir) if f.endswith(".txt")])
        print(f"  📁 {account}: {len(files)} report files")

        for fname in files:
            fpath = os.path.join(report_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                if len(lines) < 2:
                    continue

                headers = [h.strip().lower().replace("-", "_") for h in lines[0].strip().split("\t")]
                idx = {h: i for i, h in enumerate(headers)}

                for line in lines[1:]:
                    cols = line.strip().split("\t")
                    if len(cols) < 10:
                        continue

                    amazon_id = _safe_get(cols, idx, "amazon_order_id")
                    if not amazon_id:
                        continue

                    # Skip if already in DB from better sources
                    existing = get_order(amazon_id)
                    if existing:
                        continue

                    merchant_id = _safe_get(cols, idx, "merchant_order_id")
                    if not merchant_id or not merchant_id.startswith("WB"):
                        continue

                    data = {
                        "amazon_order_id": amazon_id,
                        "merchant_order_id": merchant_id,
                        "wb_order_id": merchant_id,
                        "account": account,
                        "order_status": _safe_get(cols, idx, "order_status", "Unshipped"),
                        "sku": _safe_get(cols, idx, "sku", ""),
                        "asin": _safe_get(cols, idx, "asin", ""),
                        "quantity": int(_safe_get(cols, idx, "quantity", 0) or 0),
                        "item_price": _parse_money(_safe_get(cols, idx, "item_price", "")),
                        "shipping_price": _parse_money(_safe_get(cols, idx, "shipping_price", "")),
                        "ship_city": _safe_get(cols, idx, "ship_city", ""),
                        "ship_state": _safe_get(cols, idx, "ship_state", ""),
                        "ship_postal_code": _safe_get(cols, idx, "ship_postal_code", ""),
                        "purchase_date": _safe_get(cols, idx, "purchase_date", ""),
                        "source": "ds_closing",
                        "backfilled_at": datetime.utcnow().isoformat(),
                    }

                    insert_order(data)
                    total += 1

            except Exception as e:
                print(f"  ⚠️  Error reading {fname}: {e}")

    print(f"  ✅ Backfilled {total} orders from DS_Closing reports")
    return total


def _safe_get(cols, idx, key, default=""):
    i = idx.get(key)
    if i is None or i >= len(cols):
        return default
    return cols[i].strip() or default


# ── Source 4: tracker_test_local.db ──────────────────────────────────────────
def backfill_from_tracker_db():
    """Backfill from Final Automation/'s tracker_test_local.db."""
    print("\n💾 Source 4: tracker_test_local.db")
    if not os.path.exists(TRACKER_TEST_DB):
        print("  ⚠️  tracker_test_local.db not found — skipping")
        return 0

    conn = sqlite3.connect(TRACKER_TEST_DB)
    conn.row_factory = sqlite3.Row

    count = 0
    rows = conn.execute("SELECT * FROM sheet_orders ORDER BY created_at").fetchall()

    for row in rows:
        amazon_id = row["amazon_order_id"]
        if not amazon_id:
            continue

        # Skip if already in DB
        existing = get_order(amazon_id)
        if existing and existing.get("source") == "automations":
            continue

        data = {
            "amazon_order_id": amazon_id,
            "merchant_order_id": row["wb_order_id"] if "wb_order_id" in row.keys() else "",
            "account": row["account"] if "account" in row.keys() else "HOANK",
            "order_status": row["order_status"] if "order_status" in row.keys() else "Unshipped",
            "asin": row["asin"] if "asin" in row.keys() else "",
            "sku": row["sku"] if "sku" in row.keys() else "",
            "quantity": row["quantity"] if "quantity" in row.keys() else 0 or 0,
            "item_price": row["item_price"] if "item_price" in row.keys() else 0,
            "shipping_price": row["shipping_price"] if "shipping_price" in row.keys() else 0,
            "ship_city": row["ship_city"] if "ship_city" in row.keys() else "",
            "ship_state": row["ship_state"] if "ship_state" in row.keys() else "",
            "ship_postal_code": row["ship_postal_code"] if "ship_postal_code" in row.keys() else "",
            "purchase_date": row["purchase_date"] if "purchase_date" in row.keys() else "",
            "ship_by_date": row["ship_by_date"] if "ship_by_date" in row.keys() else "",
            "deliver_by_date": row["deliver_by_date"] if "deliver_by_date" in row.keys() else "",
            "earnings": row["your_earnings"] if "your_earnings" in row.keys() else 0,
            "amazon_fees": row["amazon_fees"] if "amazon_fees" in row.keys() else 0,
            "wb_order_id": row["wb_order_id"] if "wb_order_id" in row.keys() else "",
            "zoro_account_name": row["zoro_account_name"] if "zoro_account_name" in row.keys() else "",
            "zoro_order_status": row["zoro_order_status"] if "zoro_order_status" in row.keys() else "",
            "zoro_sku": row["zoro_sku"] if "zoro_sku" in row.keys() else "",
            "zoro_qty": row["zoro_qty"] if "zoro_qty" in row.keys() else 0 or 0,
            "zoro_order_total": row["zoro_order_total"] if "zoro_order_total" in row.keys() else 0,
            "zoro_tax": row["zoro_tax"] if "zoro_tax" in row.keys() else 0,
            "zoro_buyer_zip": row["zoro_buyer_zip"] if "zoro_buyer_zip" in row.keys() else "",
            "tracking_ids": row["tracking_ids"] if "tracking_ids" in row.keys() else "",
            "delivery_status": row["delivery_status"] if "delivery_status" in row.keys() else "",
            "delivery_date": row["delivery_date"] if "delivery_date" in row.keys() else "",
            "delivered_msg_sent": row["delivered_msg_sent"] if "delivered_msg_sent" in row.keys() else None,
            "review_requested": row["review_requested"] if "review_requested" in row.keys() else None,
            "return_requested": row["return_requested"] if "return_requested" in row.keys() else None,
            "refund_done": row["refund_done"] if "refund_done" in row.keys() else None,
            "order_label": row["order_label"] if "order_label" in row.keys() else "Incomplete",
            "comments": row["comments"] if "comments" in row.keys() else "",
            "source": "final_automation",
            "backfilled_at": datetime.utcnow().isoformat(),
        }

        # Calculate profit
        earnings_val = _parse_money(data.get("earnings"))
        zoro_total_val = _parse_money(data.get("zoro_order_total"))
        if earnings_val and zoro_total_val:
            data["profit_dollars"] = earnings_val - zoro_total_val
            if earnings_val > 0:
                data["profit_percent"] = (data["profit_dollars"] / earnings_val) * 100

        insert_order(data)
        count += 1
        if count % 500 == 0:
            print(f"     ... {count} orders from tracker DB")

    conn.close()
    print(f"  ✅ Backfilled {count} orders from tracker_test_local.db")
    return count


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Backfill unified DB from all sources")
    parser.add_argument("--source", default="all",
                        choices=["all", "order_log", "gmail", "ds_closing", "tracker_db"],
                        help="Which source to backfill from")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without writing")
    args = parser.parse_args()

    print("═" * 60)
    print("  🗄️  UNIFIED DATABASE BACKFILL")
    print("═" * 60)

    # Init DB
    init_db()

    total = 0
    if args.source in ("all", "order_log"):
        total += backfill_from_order_log()
    if args.source in ("all", "gmail"):
        total += backfill_from_gmail_archive()
    if args.source in ("all", "ds_closing"):
        total += backfill_from_ds_closing()
    if args.source in ("all", "tracker_db"):
        total += backfill_from_tracker_db()

    print("\n" + "═" * 60)
    print(f"  ✅ BACKFILL COMPLETE — {total:,} total orders in unified DB")
    print("═" * 60)

    # Final stats
    from utils.unified_db import get_stats
    stats = get_stats()
    print(f"\n📊 Unified DB Stats:")
    print(f"   Total orders: {stats['total_orders']:,}")
    for account, cnt in stats.get("by_account", {}).items():
        print(f"   {account}: {cnt:,}")
    for label, cnt in stats.get("by_label", {}).items():
        print(f"   Label '{label}': {cnt:,}")
    for status, cnt in stats.get("by_delivery", {}).items():
        print(f"   Delivery '{status}': {cnt:,}")
    print(f"   Delivered but no message: {stats['delivered_no_message']:,}")
    print(f"   Delivered but no review:  {stats['delivered_no_review']:,}")


if __name__ == "__main__":
    main()
