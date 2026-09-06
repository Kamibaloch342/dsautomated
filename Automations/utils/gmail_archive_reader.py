"""
gmail_archive_reader.py — Read-only interface to gmail_archive.db (Automations/data/).

This lets Automations/ query 427K cached emails without hitting Gmail API live.
The archive DB lives in Automations/data/ and is maintained by Automations/gmail_fetch.
We only READ from it — never write.
"""

from __future__ import annotations

import os
import sqlite3
import json
import logging
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger("gmail_archive_reader")

# Archive DB path — maintained by Automations/gmail_fetch (the old sibling
# "Final Automation" folder was deleted; the DB now lives in Automations/data/).
_ARCHIVE_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "gmail_archive.db"
)


@contextmanager
def _get_archive_conn():
    """Yield a read-only connection to the gmail archive DB."""
    if not os.path.exists(_ARCHIVE_DB_PATH):
        raise FileNotFoundError(f"Gmail archive DB not found: {_ARCHIVE_DB_PATH}")
    conn = sqlite3.connect(f"file:{_ARCHIVE_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ── Earnings emails ──────────────────────────────────────────────────────────
def get_earnings(amazon_order_id: str) -> dict | None:
    """Fetch parsed earnings data for an Amazon order."""
    with _get_archive_conn() as conn:
        row = conn.execute(
            "SELECT * FROM earnings_emails WHERE amazon_order_id=?",
            (amazon_order_id,)
        ).fetchone()
        if not row:
            return None
        return _row_to_dict(row)


def search_earnings_by_date_range(start_date: str, end_date: str) -> list[dict]:
    """
    Search earnings emails by order date range.
    Dates in YYYY-MM-DD format.
    """
    with _get_archive_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM earnings_emails WHERE order_date BETWEEN ? AND ? ORDER BY order_date",
            (start_date, end_date)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ── Zoro emails ──────────────────────────────────────────────────────────────
def get_zoro_order(wb_order_id: str) -> dict | None:
    """Fetch Zoro order confirmation data by WB Order ID."""
    with _get_archive_conn() as conn:
        row = conn.execute(
            "SELECT * FROM zoro_emails WHERE wb_order_id=?",
            (wb_order_id,)
        ).fetchone()
        if not row:
            return None
        return _row_to_dict(row)


def search_zoro_by_amazon_id(amazon_order_id: str) -> list[dict]:
    """
    Find Zoro orders that might match an Amazon order.
    (Not a direct FK — uses PO number or date proximity.)
    """
    # First get the earnings record for date reference
    earnings = get_earnings(amazon_order_id)
    if not earnings:
        return []

    order_date = earnings.get("order_date")
    if not order_date:
        return []

    # Search Zoro emails within ±2 days of the order date
    with _get_archive_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM zoro_emails WHERE order_date BETWEEN date(?, '-2 days') AND date(?, '+2 days')",
            (order_date, order_date)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ── Tracking emails ──────────────────────────────────────────────────────────
def get_tracking(wb_order_id: str) -> list[dict]:
    """Fetch all tracking email records for a WB Order ID."""
    with _get_archive_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tracking_emails WHERE wb_order_id=? ORDER BY email_timestamp",
            (wb_order_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_tracking_by_tracking_id(tracking_id: str) -> list[dict]:
    """Find tracking emails containing a specific tracking ID."""
    with _get_archive_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tracking_emails WHERE tracking_ids LIKE ?",
            (f"%{tracking_id}%",)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ── Returns / Refunds ────────────────────────────────────────────────────────
def get_returns(amazon_order_id: str) -> list[dict]:
    """Fetch all return/refund records for an Amazon order."""
    with _get_archive_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM return_emails WHERE amazon_order_id=? ORDER BY email_timestamp",
            (amazon_order_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def has_return_or_refund(amazon_order_id: str) -> tuple[bool, bool]:
    """
    Quick check: (has_return, has_refund) for an Amazon order.
    """
    with _get_archive_conn() as conn:
        ret = conn.execute(
            "SELECT 1 FROM return_emails WHERE amazon_order_id=? AND email_type='return_auth' LIMIT 1",
            (amazon_order_id,)
        ).fetchone()
        ref = conn.execute(
            "SELECT 1 FROM return_emails WHERE amazon_order_id=? AND email_type='refund_initiated' LIMIT 1",
            (amazon_order_id,)
        ).fetchone()
        return (ret is not None, ref is not None)


# ── Buyer messages ───────────────────────────────────────────────────────────
def get_buyer_messages(amazon_order_id: str) -> list[dict]:
    """Fetch all buyer↔seller messages for an Amazon order."""
    with _get_archive_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM message_emails WHERE amazon_order_id=? ORDER BY email_timestamp",
            (amazon_order_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def has_customer_message(amazon_order_id: str) -> bool:
    """Quick check if buyer sent any message."""
    with _get_archive_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM message_emails WHERE amazon_order_id=? AND direction='inbound' LIMIT 1",
            (amazon_order_id,)
        ).fetchone()
        return row is not None


# ── Archive stats ────────────────────────────────────────────────────────────
def get_archive_stats() -> dict:
    """Return row counts and date ranges for all tables."""
    with _get_archive_conn() as conn:
        tables = ["earnings_emails", "zoro_emails", "tracking_emails",
                  "message_emails", "return_emails", "receipts"]
        stats = {}
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            stats[table] = {"count": count}

        # Date ranges
        for table in ["earnings_emails", "zoro_emails", "tracking_emails"]:
            min_date = conn.execute(f"SELECT MIN(email_timestamp) FROM {table}").fetchone()[0]
            max_date = conn.execute(f"SELECT MAX(email_timestamp) FROM {table}").fetchone()[0]
            stats[table]["min_date"] = min_date
            stats[table]["max_date"] = max_date

    return stats


# ── Batch lookups ────────────────────────────────────────────────────────────
def batch_get_earnings(amazon_order_ids: list[str]) -> dict[str, dict]:
    """Fetch earnings for multiple Amazon order IDs efficiently."""
    if not amazon_order_ids:
        return {}
    with _get_archive_conn() as conn:
        placeholders = ",".join("?" for _ in amazon_order_ids)
        rows = conn.execute(
            f"SELECT * FROM earnings_emails WHERE amazon_order_id IN ({placeholders})",
            amazon_order_ids
        ).fetchall()
        return {r["amazon_order_id"]: _row_to_dict(r) for r in rows}


def batch_get_zoro(wb_order_ids: list[str]) -> dict[str, dict]:
    """Fetch Zoro data for multiple WB Order IDs efficiently."""
    if not wb_order_ids:
        return {}
    with _get_archive_conn() as conn:
        placeholders = ",".join("?" for _ in wb_order_ids)
        rows = conn.execute(
            f"SELECT * FROM zoro_emails WHERE wb_order_id IN ({placeholders})",
            wb_order_ids
        ).fetchall()
        return {r["wb_order_id"]: _row_to_dict(r) for r in rows}


def batch_get_tracking(wb_order_ids: list[str]) -> dict[str, list[dict]]:
    """Fetch tracking data for multiple WB Order IDs. Returns {wb_id: [records]}."""
    if not wb_order_ids:
        return {}
    with _get_archive_conn() as conn:
        placeholders = ",".join("?" for _ in wb_order_ids)
        rows = conn.execute(
            f"SELECT * FROM tracking_emails WHERE wb_order_id IN ({placeholders}) ORDER BY email_timestamp",
            wb_order_ids
        ).fetchall()
        result = {}
        for r in rows:
            wid = r["wb_order_id"]
            result.setdefault(wid, []).append(_row_to_dict(r))
        return result


# ── Helpers ──────────────────────────────────────────────────────────────────
def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict, parsing JSON arrays."""
    d = dict(row)
    # Parse JSON arrays where applicable
    for key in ["product_names", "skus", "asins", "quantities", "item_prices",
                "conditions", "amazon_fees_per_item", "earnings_per_item",
                "tax_per_item", "shipping_per_item", "mft_per_item",
                "item_skus", "item_names", "item_quantities",
                "attachment_types"]:
        if key in d and d[key]:
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def find_wb_id_for_amazon_order(amazon_order_id: str) -> str | None:
    """
    Try to find the WB Order ID for an Amazon order by searching the archive.
    Strategy: look at earnings date → find Zoro order on same date with matching ZIP.
    """
    earnings = get_earnings(amazon_order_id)
    if not earnings:
        return None

    order_date = earnings.get("order_date")
    ship_zip = earnings.get("ship_postal_code") or earnings.get("ship_zip")

    if not order_date:
        return None

    with _get_archive_conn() as conn:
        # Try exact date match first
        rows = conn.execute(
            "SELECT wb_order_id, ship_zip FROM zoro_emails WHERE order_date = ?",
            (order_date,)
        ).fetchall()

        if len(rows) == 1:
            return rows[0]["wb_order_id"]

        # If multiple, try ZIP match
        if ship_zip and len(rows) > 1:
            for r in rows:
                if r["ship_zip"] and r["ship_zip"].startswith(ship_zip[:5]):
                    return r["wb_order_id"]

        # Fallback: return first match
        if rows:
            return rows[0]["wb_order_id"]

    return None
