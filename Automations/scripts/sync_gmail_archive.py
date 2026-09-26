#!/usr/bin/env python3
"""
sync_gmail_archive.py — sync from gmail_archive.db → automations_unified.db archive_* tables

INCREMENTAL since 26 Sep 2026: only source rows fetched after the destination table's newest
fetched_at are copied (the hourly run used to re-copy all 216k rows — with their email
bodies — for 0–1 new emails). --full / SYNC_FULL=1 copies everything again.

The raw email body is NOT copied any more (SYNC_RAW_BODIES=1 to keep doing so): it lives in
gmail_archive.db and was ~500 MB of the unified DB. What closing needs from a body is
derived here into columns instead:
  archive_zoro     → zoro_promo_savings, zoro_promo_label, zoro_item_desc
  archive_tracking → zoro_eta  (Zoro's own "Estimated Delivery" line)

Usage:
    python scripts/sync_gmail_archive.py            # incremental
    python scripts/sync_gmail_archive.py --full     # everything (schema change / backfill)
"""

from __future__ import annotations

import os
import sys
import sqlite3
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

from engine import closing_signals as _CS  # noqa: E402

FULL = "--full" in sys.argv or os.environ.get("SYNC_FULL", "").strip().lower() in ("1", "true", "yes")
COPY_BODIES = os.environ.get("SYNC_RAW_BODIES", "").strip().lower() in ("1", "true", "yes")

# Derived columns per destination table: name → (type, fn(source_row_dict) -> value).
# NULL means "not derived yet"; '' means "derived, nothing in the body" — that is what makes
# --derive-existing idempotent and lets slim_database.py refuse to drop bodies too early.
DERIVED = {
    "archive_zoro": {
        "zoro_promo_savings": ("REAL", lambda r: _CS.extract_zoro_promo(r.get("raw_body_snippet") or "")[0]),
        "zoro_promo_label": ("TEXT", lambda r: _CS.extract_zoro_promo(r.get("raw_body_snippet") or "")[1] or ""),
        "zoro_item_desc": ("TEXT", lambda r: _CS.extract_zoro_item(r.get("raw_body_snippet") or "") or ""),
    },
    "archive_tracking": {
        "zoro_eta": ("TEXT", lambda r: _CS.extract_zoro_eta(r.get("raw_body_snippet") or "",
                                                            r.get("email_timestamp") or "") or ""),
    },
}
# The column whose NULL marks "not derived yet", per table.
DERIVED_MARKER = {"archive_zoro": "zoro_promo_label", "archive_tracking": "zoro_eta"}


def derive_existing(dst_conn: sqlite3.Connection, batch_size: int = 500) -> dict:
    """One-time backfill: compute the derived columns for rows synced BEFORE they existed,
    from the raw body those rows still carry in the unified DB. Run it before
    slim_database.py drops raw_body_snippet (which checks that nothing is pending)."""
    out = {}
    for table, cols in DERIVED.items():
        have = {c["name"] for c in dst_conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, (typ, _fn) in cols.items():
            if name not in have:
                dst_conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
        dst_conn.commit()
        if "raw_body_snippet" not in have:
            print(f"  {table}: no raw bodies left — nothing to derive")
            out[table] = 0
            continue
        marker = DERIVED_MARKER[table]
        rowids = [r[0] for r in dst_conn.execute(
            f"SELECT rowid FROM {table} WHERE {marker} IS NULL AND raw_body_snippet IS NOT NULL AND raw_body_snippet != ''")]
        print(f"  {table}: {len(rowids):,} rows to derive")
        names = list(cols.keys())
        sql = f"UPDATE {table} SET {', '.join(f'{n}=?' for n in names)} WHERE rowid=?"
        done = 0
        for i in range(0, len(rowids), batch_size):
            chunk = rowids[i:i + batch_size]
            ph = ",".join("?" * len(chunk))
            rows = dst_conn.execute(
                f"SELECT rowid, raw_body_snippet, email_timestamp FROM {table} WHERE rowid IN ({ph})", chunk).fetchall()
            payload = []
            for rid, body, ts in rows:
                rd = {"raw_body_snippet": body, "email_timestamp": ts}
                vals = []
                for n in names:
                    try:
                        vals.append(cols[n][1](rd))
                    except Exception:
                        vals.append("" if n != "zoro_promo_savings" else None)
                payload.append(vals + [rid])
            dst_conn.executemany(sql, payload)
            dst_conn.commit()
            done += len(rows)
            print(f"  ... {done:,}/{len(rowids):,}", end="\r")
        print(f"  ✓ {table}: {done:,} rows derived")
        out[table] = done
    return out


def pending_derivations(conn: sqlite3.Connection) -> dict:
    """{table: rows that still carry a body but were never derived} — must be 0 before the
    bodies are dropped."""
    out = {}
    for table, marker in DERIVED_MARKER.items():
        try:
            have = {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "raw_body_snippet" not in have:
                out[table] = 0
            elif marker not in have:
                out[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE raw_body_snippet IS NOT NULL AND raw_body_snippet != ''").fetchone()[0]
            else:
                out[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {marker} IS NULL AND raw_body_snippet IS NOT NULL "
                    f"AND raw_body_snippet != ''").fetchone()[0]
        except sqlite3.OperationalError:
            out[table] = -1
    return out

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
    src_col_names = {c["name"] for c in src_conn.execute(f"PRAGMA table_info({src_table})").fetchall()}

    # Derived columns (promo / item description / Zoro ETA) replace the raw body copy.
    derived = DERIVED.get(dst_table, {})
    for name, (typ, _fn) in derived.items():
        if name not in dst_col_names:
            dst_conn.execute(f"ALTER TABLE {dst_table} ADD COLUMN {name} {typ}")
            dst_col_names.add(name)
    dst_conn.commit()

    # Filter columns to only those that exist in dest (and in the source)
    usable_cols = [c for c in columns if c in dst_col_names and c in src_col_names
                   and (c != "raw_body_snippet" or COPY_BODIES)]
    skipped_cols = [c for c in columns if c not in dst_col_names]
    if skipped_cols:
        print(f"  ℹ️  Columns skipped (not in dest): {', '.join(skipped_cols)}")
    # The body is still READ from the source (for the derived columns) even when not copied.
    read_cols = list(dict.fromkeys(usable_cols + (["raw_body_snippet"] if "raw_body_snippet" in src_col_names else [])))
    insert_cols = usable_cols + list(derived.keys())

    placeholders = ", ".join("?" for _ in insert_cols)
    col_list = ", ".join(insert_cols)

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

    # Incremental: only source rows fetched after the newest row we already hold.
    since = None
    if not FULL and "fetched_at" in src_col_names and "fetched_at" in dst_col_names:
        since = dst_conn.execute(f"SELECT MAX(fetched_at) FROM {dst_table}").fetchone()[0]
    where, args = "", []
    if since:
        where, args = " WHERE fetched_at > ?", [since]
    cursor = src_conn.execute(f"SELECT {', '.join(read_cols)} FROM {src_table}{where}", args)

    total = 0
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        payload = []
        for row in rows:
            rd = {c: row[c] for c in read_cols}
            vals = [rd[c] for c in usable_cols]
            for _name, (_typ, fn) in derived.items():
                try:
                    vals.append(fn(rd))
                except Exception:
                    vals.append(None)
            payload.append(vals)
        dst_conn.executemany(sql, payload)
        total += len(rows)
        dst_conn.commit()
        print(f"  ... {total} rows synced", end="\r")

    print(f"  ✓ {total} rows synced from {src_table} → {dst_table}"
          + (f" (since {since[:19]})" if since else " (full)"))
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
    print(f"GMAIL ARCHIVE SYNC ({'FULL' if FULL else 'incremental'}, bodies {'copied' if COPY_BODIES else 'derived only'})")
    print(f"  Source: {GMAIL_ARCHIVE_DB}")
    print(f"  Dest:   {UNIFIED_DB}")
    print(f"  Time:   {datetime.now().isoformat()}")
    print("=" * 70)

    if not os.path.exists(GMAIL_ARCHIVE_DB):
        print(f"❌ Source DB not found: {GMAIL_ARCHIVE_DB}")
        sys.exit(1)

    src = _connect(GMAIL_ARCHIVE_DB)
    dst = _connect(UNIFIED_DB)
    dst.execute("PRAGMA busy_timeout=60000")

    if "--derive-existing" in sys.argv:
        print("\n🧮 Deriving promo / item / ETA columns for rows that still carry a body...")
        derive_existing(dst)
        print("   pending after run:", pending_derivations(dst))
        src.close()
        dst.close()
        return

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
