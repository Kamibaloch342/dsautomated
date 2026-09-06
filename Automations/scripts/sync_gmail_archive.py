#!/usr/bin/env python3
"""
sync_gmail_archive.py — Lossless sync from gmail_archive.db → automations_unified.db

Syncs ALL fields (not a subset) from the rich gmail_archive.db into the unified DB.
Use this after schema changes or to backfill missing data.

Usage:
    cd /Users/kamranbaloch342/Desktop/Dropshipping/Development_Unit/Automations
    source ../.venv/bin/activate
    python scripts/sync_gmail_archive.py
"""

from __future__ import annotations

import os
import sys
import sqlite3
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

# The archive now lives in Automations/data/ (written by gmail_fetch/build_gmail_archive.py
# — see gmail_fetch/config.py GMAIL_ARCHIVE_DB_PATH). The old sibling "Final Automation"
# folder was deleted during that migration; this path was left pointing at it, so every
# sync aborted with "Source DB not found". The legacy location is kept as a fallback so an
# older copy of the archive still syncs if one is present.
GMAIL_ARCHIVE_DB = os.path.join(SCRIPT_DIR, "data", "gmail_archive.db")
if not os.path.exists(GMAIL_ARCHIVE_DB):
    _legacy = os.path.join(SCRIPT_DIR, "..", "Final Automation", "gmail_archive.db")
    if os.path.exists(_legacy):
        GMAIL_ARCHIVE_DB = _legacy
UNIFIED_DB = os.path.join(SCRIPT_DIR, "data", "automations_unified.db")

# ── Table mappings ───────────────────────────────────────────────────────────

EARNINGS_COLS = [
    "amazon_order_id", "gmail_account", "email_timestamp", "order_date",
    "ship_by_date", "shipping_method", "num_items", "product_names", "skus",
    "asins", "quantities", "item_prices", "total_tax", "total_shipping",
    "total_amazon_fees", "total_your_earnings", "order_subtotal", "raw_body_snippet",
    "fetched_at"
]

ZORO_COLS = [
    "wb_order_id", "gmail_account", "zoro_account_name", "email_timestamp",
    "order_date", "po_number", "order_status", "sku", "qty", "unit_price",
    "line_total", "subtotal", "tax", "shipping_cost", "order_total",
    "coupon_savings", "ship_name", "ship_street", "ship_city", "ship_state",
    "ship_zip", "payment_method", "payment_card_last4", "raw_body_snippet",
    "fetched_at"
]

TRACKING_COLS = [
    "wb_order_id", "tracking_ids", "carrier", "ship_date", "delivery_method",
    "ship_to_name", "ship_to_street", "ship_to_city", "ship_to_state", "ship_to_zip",
    "num_items", "item_skus", "item_names", "item_quantities", "gmail_account",
    "email_timestamp", "raw_body_snippet", "fetched_at"
]

RETURN_COLS = [
    "amazon_order_id", "email_type", "return_requested", "refund_done",
    "return_auth_id", "return_request_date", "return_policy_check", "authorization_type",
    "asin", "sku", "return_qty", "return_reason", "customer_comment",
    "refund_amount", "refund_reason", "customer_name", "item_price",
    "item_price_refund", "outbound_shipping_refund", "fulfillment_network",
    "refund_resolution", "gmail_account", "email_timestamp", "raw_body_snippet",
    "fetched_at"
]

MESSAGE_COLS = [
    "amazon_order_id", "message_type", "customer_name", "subject", "direction",
    "has_attachment", "attachment_types", "attachment_count", "message_body",
    "tracking_ids_mentioned", "is_oos", "is_loss", "is_sr", "is_unable_to_fulfill",
    "sent_tracking_details", "communicated_with_customer", "customer_wrote",
    "has_pictures", "other_message", "gmail_account", "email_timestamp",
    "message_id", "raw_body_snippet", "fetched_at"
]


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _sync_table(
    src_conn: sqlite3.Connection,
    dst_conn: sqlite3.Connection,
    src_table: str,
    dst_table: str,
    columns: list[str],
    key_cols: list[str],
    batch_size: int = 1000,
) -> int:
    """Sync one table. Returns number of rows upserted."""
    # Check if source table exists
    src_exists = src_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (src_table,)
    ).fetchone()
    if not src_exists:
        print(f"  ⚠️  Source table '{src_table}' not found, skipping")
        return 0

    # Check if dest table exists
    dst_exists = dst_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (dst_table,)
    ).fetchone()
    if not dst_exists:
        print(f"  ⚠️  Dest table '{dst_table}' not found, skipping")
        return 0

    # Get actual columns from dest table (to handle schema drift)
    dst_info = dst_conn.execute(f"PRAGMA table_info({dst_table})").fetchall()
    dst_col_names = {c["name"] for c in dst_info}

    # Filter columns to only those that exist in dest
    usable_cols = [c for c in columns if c in dst_col_names]
    skipped_cols = [c for c in columns if c not in dst_col_names]
    if skipped_cols:
        print(f"  ℹ️  Columns skipped (not in dest): {', '.join(skipped_cols)}")

    placeholders = ", ".join("?" for _ in usable_cols)
    col_list = ", ".join(usable_cols)

    pk_info = dst_conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (dst_table,)
    ).fetchone()
    has_pk = pk_info and "PRIMARY KEY" in pk_info["sql"].split("AUTOINCREMENT")[0]
    # Tables with a business-key PK (archive_earnings, archive_zoro) take
    # INSERT OR REPLACE. The id-AUTOINCREMENT tables rely on the UNIQUE
    # business-key indexes (created in main()) so INSERT OR IGNORE makes
    # re-runs idempotent — the June 2026 double-load happened because the
    # old per-row existence check missed legacy rows with NULL extra fields.
    verb = "INSERT OR REPLACE" if has_pk else "INSERT OR IGNORE"
    sql = f"{verb} INTO {dst_table} ({col_list}) VALUES ({placeholders})"

    total = 0
    cursor = src_conn.execute(f"SELECT {col_list} FROM {src_table}")

    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        dst_conn.executemany(sql, [[row[c] for c in usable_cols] for row in rows])
        total += len(rows)
        dst_conn.commit()
        print(f"  ... {total} rows synced", end="\r")

    print(f"  ✓ {total} rows synced from {src_table} → {dst_table}")
    return total


# UNIQUE indexes that make INSERT OR IGNORE idempotent for the
# id-AUTOINCREMENT archive tables. Same email fetched twice in the source
# also collapses to one row here, which is the desired behavior.
UNIQUE_INDEXES = [
    ("idx_uq_archive_tracking", "archive_tracking",
     "wb_order_id, tracking_ids, email_timestamp, gmail_account"),
    ("idx_uq_archive_messages", "archive_messages",
     "amazon_order_id, email_timestamp, message_type, gmail_account"),
    ("idx_uq_archive_returns", "archive_returns",
     "amazon_order_id, email_timestamp, email_type, gmail_account"),
]


def ensure_unique_indexes(dst_conn: sqlite3.Connection) -> None:
    for name, table, cols in UNIQUE_INDEXES:
        try:
            dst_conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({cols})"
            )
        except sqlite3.IntegrityError:
            print(f"  ❌ Cannot create {name}: {table} still contains duplicates.")
            print(f"     Dedupe first (keep MIN(id) per ({cols})), then re-run.")
            raise
    dst_conn.commit()


def main():
    print("=" * 70)
    print(f"GMAIL ARCHIVE SYNC")
    print(f"  Source: {GMAIL_ARCHIVE_DB}")
    print(f"  Dest:   {UNIFIED_DB}")
    print(f"  Time:   {datetime.now().isoformat()}")
    print("=" * 70)

    if not os.path.exists(GMAIL_ARCHIVE_DB):
        print(f"❌ Source DB not found: {GMAIL_ARCHIVE_DB}")
        sys.exit(1)

    src = _connect(GMAIL_ARCHIVE_DB)
    dst = _connect(UNIFIED_DB)

    print("\n🔒 Ensuring unique indexes (idempotency guard)...")
    ensure_unique_indexes(dst)

    stats = {}

    # 1. Earnings
    print("\n📧 Earnings...")
    stats["earnings"] = _sync_table(
        src, dst, "earnings_emails", "archive_earnings",
        EARNINGS_COLS, ["amazon_order_id", "email_timestamp"]
    )

    # 2. Zoro
    print("\n🏭 Zoro...")
    stats["zoro"] = _sync_table(
        src, dst, "zoro_emails", "archive_zoro",
        ZORO_COLS, ["wb_order_id", "email_timestamp"]
    )

    # 3. Tracking
    print("\n📦 Tracking...")
    stats["tracking"] = _sync_table(
        src, dst, "tracking_emails", "archive_tracking",
        TRACKING_COLS, ["wb_order_id", "email_timestamp"]
    )

    # 4. Returns
    print("\n↩️  Returns...")
    stats["returns"] = _sync_table(
        src, dst, "return_emails", "archive_returns",
        RETURN_COLS, ["amazon_order_id", "email_timestamp"]
    )

    # 5. Messages
    print("\n💬 Messages...")
    stats["messages"] = _sync_table(
        src, dst, "message_emails", "archive_messages",
        MESSAGE_COLS, ["amazon_order_id", "email_timestamp"]
    )

    src.close()
    dst.close()

    print("\n" + "=" * 70)
    print("SYNC COMPLETE")
    print("=" * 70)
    total = sum(stats.values())
    print(f"  Total rows synced: {total}")
    for k, v in stats.items():
        print(f"    {k:<12} {v:>8,}")


if __name__ == "__main__":
    main()
