"""
unified_db.py — Single source of truth for ALL order data across both accounts.

Merges data from:
  - order_log.txt (checkout-time data)
  - gmail_archive.db (earnings, Zoro, tracking, returns)
  - DS_Closing Order_Reports (monthly TSV archives)
  - FedEx tracking (delivery status)
  - Post-delivery actions (messages, reviews)

Designed for the Automations/ stack. Replaces the scattered storage
(order_log.txt, message_log.json, tracking_upload_log.json, per-run CSVs).
"""

from __future__ import annotations

import os
import sqlite3
import logging
from datetime import datetime
from contextlib import contextmanager

logger = logging.getLogger("unified_db")

# ── Paths ────────────────────────────────────────────────────────────────────
_AUTOMATIONS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_AUTOMATIONS_DIR, "data", "automations_unified.db")


# ── Schema ───────────────────────────────────────────────────────────────────
# 45 columns covering the full order lifecycle
_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id         TEXT NOT NULL UNIQUE,
    merchant_order_id       TEXT,
    account                 TEXT NOT NULL DEFAULT 'HOANK',  -- 'HOANK' or 'Hathoro'

    -- Amazon order data
    order_status            TEXT DEFAULT 'Unshipped',
    asin                    TEXT,
    sku                     TEXT,
    title                   TEXT,
    quantity                INTEGER DEFAULT 0,
    item_price              REAL DEFAULT 0.0,
    shipping_price          REAL DEFAULT 0.0,
    ship_city               TEXT,
    ship_state              TEXT,
    ship_postal_code        TEXT,
    ship_country            TEXT DEFAULT 'US',
    purchase_date           TEXT,
    ship_by_date            TEXT,
    deliver_by_date         TEXT,

    -- Earnings (from Gmail)
    earnings                REAL,
    amazon_fees             REAL,
    shipping_amt            REAL,
    sold_email_time         TEXT,

    -- Zoro data
    wb_order_id             TEXT,
    zoro_account_name       TEXT,
    zoro_order_status       TEXT DEFAULT 'Open',
    zoro_sku                TEXT,
    zoro_qty                INTEGER DEFAULT 0,
    zoro_order_date         TEXT,
    zoro_email_ts           TEXT,
    zoro_payment_card       TEXT,
    zoro_tax                REAL DEFAULT 0.0,
    zoro_order_total        REAL DEFAULT 0.0,
    zoro_buyer_zip          TEXT,
    coupon_used             TEXT,
    coupon_savings          REAL DEFAULT 0.0,

    -- Profit
    profit_dollars          REAL,
    profit_percent          REAL,

    -- Tracking
    tracking_ids            TEXT DEFAULT '',      -- comma-separated
    shipped_email_time      TEXT,
    carrier                 TEXT DEFAULT 'FedEx',

    -- FedEx / Delivery
    delivery_status         TEXT DEFAULT '',      -- Delivered / InTransit / Exception / etc.
    delivery_date           TEXT,
    estimated_window        TEXT,
    last_scan_location      TEXT,
    last_scan_time          TEXT,
    signed_by               TEXT,
    left_at                 TEXT,
    delivery_note           TEXT,
    delivery_from           TEXT,
    delivery_to             TEXT,

    -- Post-delivery flags
    delivered_msg_sent      TEXT DEFAULT NULL,    -- Yes / No / No Permission
    review_requested        TEXT DEFAULT NULL,    -- Yes / Already / Expired
    return_requested        TEXT DEFAULT NULL,    -- Yes / No
    refund_done             TEXT DEFAULT NULL,    -- Yes / No
    customer_messaged       TEXT DEFAULT NULL,    -- Yes / No

    -- Meta
    order_label             TEXT DEFAULT 'Incomplete',  -- Completed / Incomplete / Cancelled
    comments                TEXT DEFAULT '',
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Source tracking (for backfill)
    source                  TEXT DEFAULT 'automations',  -- automations / final_automation / backfill
    backfilled_at           TEXT
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_orders_amazon      ON orders(amazon_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_wb          ON orders(wb_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_account     ON orders(account);
CREATE INDEX IF NOT EXISTS idx_orders_delivery    ON orders(delivery_status);
CREATE INDEX IF NOT EXISTS idx_orders_label       ON orders(order_label);
CREATE INDEX IF NOT EXISTS idx_orders_purchase    ON orders(purchase_date);
CREATE INDEX IF NOT EXISTS idx_orders_status      ON orders(order_status);

-- Audit log: every change to an order (who changed what, when)
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id TEXT NOT NULL,
    field_changed   TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    changed_by      TEXT DEFAULT 'system',  -- system / bot / manual
    changed_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_amazon ON audit_log(amazon_order_id);

-- Post-delivery action log (messages sent, reviews requested, etc.)
CREATE TABLE IF NOT EXISTS action_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id TEXT NOT NULL,
    action_type     TEXT NOT NULL,  -- delivery_message / review_request / transit_alert / tracking_upload
    status          TEXT DEFAULT 'success',  -- success / failed / skipped
    detail          TEXT,  -- error message or note
    performed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_action_amazon ON action_log(amazon_order_id);
CREATE INDEX IF NOT EXISTS idx_action_type   ON action_log(action_type);

-- ═══════════════════════════════════════════════════════════════════════════════
--  NEW TABLES: Multi-table architecture (Phase 1)
-- ═══════════════════════════════════════════════════════════════════════════════

-- Worker A: Order Placement data (populated by sync script reading order_log.txt)
CREATE TABLE IF NOT EXISTS order_placer_orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id     TEXT NOT NULL UNIQUE,
    wb_order_id         TEXT,
    account             TEXT NOT NULL DEFAULT 'HOANK',
    asin                TEXT,
    sku                 TEXT,
    title               TEXT,
    quantity            INTEGER DEFAULT 0,
    earnings            REAL,
    amazon_fees         REAL,
    shipping_amt        REAL,
    zoro_order_total    REAL,
    zoro_tax            REAL,
    profit_dollars      REAL,
    profit_percent      REAL,
    coupon_used         TEXT,
    coupon_savings      REAL DEFAULT 0.0,
    ship_name           TEXT,
    ship_street         TEXT,
    ship_city           TEXT,
    ship_state          TEXT,
    ship_postal_code    TEXT,
    ship_country        TEXT DEFAULT 'US',
    ship_phone          TEXT,
    purchase_date       TEXT,
    ship_by_date        TEXT,
    deliver_by_date     TEXT,
    profile_name        TEXT,
    tracking_ids        TEXT,
    shipped_email_time  TEXT,
    zoro_response_json  TEXT,
    placed_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    synced_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    version             INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_placer_amazon  ON order_placer_orders(amazon_order_id);
CREATE INDEX IF NOT EXISTS idx_placer_wb      ON order_placer_orders(wb_order_id);
CREATE INDEX IF NOT EXISTS idx_placer_account ON order_placer_orders(account);
CREATE INDEX IF NOT EXISTS idx_placer_placed  ON order_placer_orders(placed_at);

-- Worker B: Closing / Tracker enriched orders
CREATE TABLE IF NOT EXISTS closing_orders (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id             TEXT NOT NULL UNIQUE,
    merchant_order_id           TEXT,
    account                     TEXT NOT NULL DEFAULT 'HOANK',
    report_download_date        TEXT,
    order_status                TEXT DEFAULT 'Unshipped',
    asin                        TEXT,
    sku                         TEXT,
    title                       TEXT,
    quantity                    INTEGER DEFAULT 0,
    item_price                  REAL DEFAULT 0.0,
    shipping_price              REAL DEFAULT 0.0,
    ship_city                   TEXT,
    ship_state                  TEXT,
    ship_postal_code            TEXT,
    ship_country                TEXT DEFAULT 'US',
    purchase_date               TEXT,
    ship_by_date                TEXT,
    deliver_by_date             TEXT,
    earnings                    REAL,
    amazon_fees                 REAL,
    shipping_amt                REAL,
    sold_email_time             TEXT,
    wb_order_id                 TEXT,
    zoro_account_name           TEXT,
    zoro_order_status           TEXT DEFAULT 'Open',
    zoro_sku                    TEXT,
    zoro_qty                    INTEGER DEFAULT 0,
    zoro_order_date             TEXT,
    zoro_email_ts               TEXT,
    zoro_payment_card           TEXT,
    zoro_tax                    REAL DEFAULT 0.0,
    zoro_order_total            REAL DEFAULT 0.0,
    zoro_buyer_zip              TEXT,
    coupon_used                 TEXT,
    coupon_savings              REAL DEFAULT 0.0,
    profit_dollars              REAL,
    profit_percent              REAL,
    tracking_ids                TEXT DEFAULT '',
    shipped_email_time          TEXT,
    carrier                     TEXT DEFAULT 'FedEx',
    delivery_status             TEXT DEFAULT '',
    delivery_date               TEXT,
    estimated_window            TEXT,
    last_scan_location          TEXT,
    last_scan_time              TEXT,
    signed_by                   TEXT,
    left_at                     TEXT,
    delivery_note               TEXT,
    delivery_from               TEXT,
    delivery_to                 TEXT,
    delivered_msg_sent          TEXT DEFAULT NULL,
    review_requested            TEXT DEFAULT NULL,
    return_requested            TEXT DEFAULT NULL,
    refund_done                 TEXT DEFAULT NULL,
    customer_messaged           TEXT DEFAULT NULL,
    order_label                 TEXT DEFAULT 'Incomplete',
    comments                    TEXT DEFAULT '',
    enriched_from_archive       INTEGER DEFAULT 0,
    enriched_from_live_gmail    INTEGER DEFAULT 0,
    enriched_from_placer_db     INTEGER DEFAULT 0,
    enriched_from_fedex         INTEGER DEFAULT 0,
    enriched_from_scrape        INTEGER DEFAULT 0,
    scraped_at                  TEXT,
    last_enrichment_run         TEXT,
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_closing_amazon      ON closing_orders(amazon_order_id);
CREATE INDEX IF NOT EXISTS idx_closing_wb          ON closing_orders(wb_order_id);
CREATE INDEX IF NOT EXISTS idx_closing_account     ON closing_orders(account);
CREATE INDEX IF NOT EXISTS idx_closing_delivery    ON closing_orders(delivery_status);
CREATE INDEX IF NOT EXISTS idx_closing_purchase    ON closing_orders(purchase_date);
CREATE INDEX IF NOT EXISTS idx_closing_enriched    ON closing_orders(last_enrichment_run);

-- Gmail Archive Cache: local mirror of Final Automation/gmail_archive.db
CREATE TABLE IF NOT EXISTS archive_earnings (
    amazon_order_id         TEXT PRIMARY KEY,
    gmail_account           TEXT,
    email_timestamp         TEXT,
    order_date              TEXT,
    ship_by_date            TEXT,
    shipping_method         TEXT,
    num_items               INTEGER DEFAULT 1,
    product_names           TEXT,
    skus                    TEXT,
    asins                   TEXT,
    quantities              TEXT,
    item_prices             TEXT,
    total_tax               REAL,
    total_shipping          REAL,
    total_amazon_fees       REAL,
    total_your_earnings     REAL,
    order_subtotal          REAL,
    raw_body_snippet        TEXT,
    fetched_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_arch_earnings_amazon ON archive_earnings(amazon_order_id);

CREATE TABLE IF NOT EXISTS archive_zoro (
    wb_order_id         TEXT PRIMARY KEY,
    gmail_account       TEXT,
    zoro_account_name   TEXT,
    email_timestamp     TEXT,
    order_date          TEXT,
    po_number           TEXT,
    order_status        TEXT DEFAULT 'Active',
    sku                 TEXT,
    qty                 INTEGER,
    unit_price          REAL,
    line_total          REAL,
    subtotal            REAL,
    tax                 REAL,
    shipping_cost       REAL,
    order_total         REAL,
    coupon_savings      REAL DEFAULT 0.0,
    ship_name           TEXT,
    ship_street         TEXT,
    ship_city           TEXT,
    ship_state          TEXT,
    ship_zip            TEXT,
    payment_method      TEXT,
    payment_card_last4  TEXT,
    raw_body_snippet    TEXT,
    fetched_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_arch_zoro_wb ON archive_zoro(wb_order_id);
CREATE INDEX IF NOT EXISTS idx_arch_zoro_po  ON archive_zoro(po_number);

CREATE TABLE IF NOT EXISTS archive_tracking (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    wb_order_id         TEXT,
    tracking_ids        TEXT,
    carrier             TEXT,
    ship_date           TEXT,
    delivery_method     TEXT,
    ship_to_name        TEXT,
    ship_to_street      TEXT,
    ship_to_city        TEXT,
    ship_to_state       TEXT,
    ship_to_zip         TEXT,
    num_items           INTEGER,
    item_skus           TEXT,
    item_names          TEXT,
    item_quantities     TEXT,
    gmail_account       TEXT,
    email_timestamp     TEXT,
    raw_body_snippet    TEXT,
    fetched_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_arch_track_wb ON archive_tracking(wb_order_id);
CREATE INDEX IF NOT EXISTS idx_arch_track_tid ON archive_tracking(tracking_ids);

CREATE TABLE IF NOT EXISTS archive_returns (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id     TEXT,
    gmail_account       TEXT,
    email_timestamp     TEXT,
    return_reason       TEXT,
    raw_body_snippet    TEXT,
    fetched_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_arch_ret_amazon ON archive_returns(amazon_order_id);

CREATE TABLE IF NOT EXISTS archive_messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id     TEXT,
    gmail_account       TEXT,
    email_timestamp     TEXT,
    message_type        TEXT,
    raw_body_snippet    TEXT,
    fetched_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_arch_msg_amazon ON archive_messages(amazon_order_id);

-- Enrichment Log: every enrichment step for every order
CREATE TABLE IF NOT EXISTS enrichment_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    amazon_order_id TEXT NOT NULL,
    source          TEXT NOT NULL,
    field           TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    confidence      REAL DEFAULT 1.0,
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_enrich_run    ON enrichment_log(run_id);
CREATE INDEX IF NOT EXISTS idx_enrich_amazon ON enrichment_log(amazon_order_id);
CREATE INDEX IF NOT EXISTS idx_enrich_source ON enrichment_log(source);

-- FedEx Tracking Cache: API responses cached to avoid re-hitting FedEx
CREATE TABLE IF NOT EXISTS fedex_tracking_cache (
    tracking_id     TEXT PRIMARY KEY,
    response_json   TEXT NOT NULL,
    fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at      TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fedex_expires ON fedex_tracking_cache(expires_at);

-- Raw API Log: every external API call
CREATE TABLE IF NOT EXISTS raw_api_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    request_body    TEXT,
    response_body   TEXT,
    status_code     INTEGER,
    duration_ms     INTEGER,
    error           TEXT,
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_api_run      ON raw_api_log(run_id);
CREATE INDEX IF NOT EXISTS idx_api_endpoint ON raw_api_log(endpoint);

-- Closing Run Log: one row per closing pipeline run
CREATE TABLE IF NOT EXISTS closing_run_log (
    run_id                  TEXT PRIMARY KEY,
    date_from               TEXT NOT NULL,
    date_to                 TEXT NOT NULL,
    accounts                TEXT,
    orders_processed        INTEGER DEFAULT 0,
    orders_enriched         INTEGER DEFAULT 0,
    archive_hits            INTEGER DEFAULT 0,
    live_gmail_hits         INTEGER DEFAULT 0,
    placer_db_hits          INTEGER DEFAULT 0,
    fedex_hits              INTEGER DEFAULT 0,
    messages_sent           INTEGER DEFAULT 0,
    reviews_requested       INTEGER DEFAULT 0,
    errors                  INTEGER DEFAULT 0,
    error_details           TEXT,
    started_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at            TIMESTAMP,
    status                  TEXT DEFAULT 'running'
);
CREATE INDEX IF NOT EXISTS idx_closing_run_dates ON closing_run_log(date_from, date_to);

"""

# ── Walmart → Zoro pipeline table (own constant so the runner can create it
#    idempotently without executing the whole schema) ─────────────────────────
# THE maintained log for the Walmart Seller Center pipeline: every row the
# pipeline touches (scraped from seller.walmart.com OR entered manually in the
# GUI grid) is upserted here. The queue JSON (data/walmart_queue.json) stays
# the GUI⇄runner contract; THIS table is the source of truth for history +
# the dashboard. po_number is the Walmart PO# ('MANUAL:<row id>' for manual
# rows entered without one).
WALMART_ORDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS walmart_orders (
    po_number       TEXT PRIMARY KEY,
    order_number    TEXT,
    order_date      TEXT,
    ship_by         TEXT,
    deliver_by      TEXT,
    title           TEXT,
    walmart_sku     TEXT,
    zoro_code       TEXT,
    wm_qty          INTEGER,
    zoro_qty        INTEGER,
    item_price      REAL,
    shipping_fee    REAL,
    tax             REAL,
    order_total     REAL,
    earnings        REAL,
    ship_name       TEXT,
    ship_street     TEXT,
    ship_city       TEXT,
    ship_state      TEXT,
    ship_zip        TEXT,
    ship_phone      TEXT,
    wb_order_id     TEXT,
    zoro_cost       REAL,
    profit          REAL,
    status          TEXT,
    error           TEXT,
    source          TEXT,
    coupon_used     TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    placed_at       TEXT,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_walmart_wb     ON walmart_orders(wb_order_id);
CREATE INDEX IF NOT EXISTS idx_walmart_status ON walmart_orders(status);
CREATE INDEX IF NOT EXISTS idx_walmart_zip    ON walmart_orders(ship_zip);
"""

_SCHEMA += WALMART_ORDERS_SCHEMA


# ── Connection helper ────────────────────────────────────────────────────────
@contextmanager
def get_conn(db_path=None):
    """Yield a sqlite3 connection with WAL mode and Row factory."""
    path = str(db_path or DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # Wait up to 60s for a held write-lock instead of raising "database is locked".
    # This is what lets both placers + both closing accounts + the dashboard all
    # write to the one DB concurrently without failing under contention.
    conn.execute("PRAGMA busy_timeout=60000")
    # Cap the WAL at 256 MB once a checkpoint completes. Nothing ever truncated it and the
    # file reached 4 GB on 26 Sep 2026; harmless on a connection that only reads.
    try:
        conn.execute("PRAGMA journal_size_limit=268435456")
    except Exception:
        pass
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=None):
    """Create tables if they don't exist. Safe to call multiple times."""
    target = db_path or DB_PATH
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with get_conn(target) as conn:
        conn.executescript(_SCHEMA)
    logger.info("Unified DB ready: %s", target)


def init_walmart_orders(db_path=None):
    """Create ONLY the walmart_orders table (idempotent, cheap). The Walmart
    runner calls this on every start so the table exists before any upsert."""
    with get_conn(db_path) as conn:
        conn.executescript(WALMART_ORDERS_SCHEMA)


# ── Order CRUD ───────────────────────────────────────────────────────────────
def insert_order(data: dict, db_path=None) -> bool:
    """
    Insert or replace an order. `data` is a flat dict matching column names.
    Returns True if inserted, False if row already existed unchanged.
    """
    with get_conn(db_path) as conn:
        # Check if exists
        existing = conn.execute(
            "SELECT updated_at FROM orders WHERE amazon_order_id=?",
            (data.get("amazon_order_id"),)
        ).fetchone()

        now = datetime.utcnow().isoformat()
        columns = list(data.keys())
        values = [data.get(c) for c in columns]

        if existing:
            # UPDATE existing
            set_clause = ", ".join(f"{c}=?" for c in columns)
            values.append(data.get("amazon_order_id"))
            conn.execute(
                f"UPDATE orders SET {set_clause}, updated_at=? WHERE amazon_order_id=?",
                values[:-1] + [now, data.get("amazon_order_id")]
            )
            # Audit log
            for col in columns:
                if col in ("updated_at", "created_at"):
                    continue
                _log_audit(conn, data.get("amazon_order_id"), col,
                           None, data.get(col), "system")
            return False
        else:
            # INSERT new
            columns.append("created_at")
            values.append(now)
            placeholders = ", ".join("?" for _ in values)
            conn.execute(
                f"INSERT INTO orders ({', '.join(columns)}) VALUES ({placeholders})",
                values
            )
            return True


def get_order(amazon_order_id: str, db_path=None) -> dict | None:
    """Fetch a single order by Amazon Order ID. Returns dict or None."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE amazon_order_id=?", (amazon_order_id,)
        ).fetchone()
        return dict(row) if row else None


def get_order_by_wb(wb_order_id: str, db_path=None) -> dict | None:
    """Fetch a single order by WB Order ID."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE wb_order_id=?", (wb_order_id,)
        ).fetchone()
        return dict(row) if row else None


def update_order(amazon_order_id: str, updates: dict, changed_by="system", db_path=None):
    """
    Partial update. `updates` is a dict of {column: new_value}.
    Logs changes to audit_log automatically.
    """
    with get_conn(db_path) as conn:
        # Get old values for audit
        old_row = conn.execute(
            "SELECT * FROM orders WHERE amazon_order_id=?", (amazon_order_id,)
        ).fetchone()
        if not old_row:
            logger.warning("update_order: %s not found", amazon_order_id)
            return False

        old = dict(old_row)
        now = datetime.utcnow().isoformat()

        set_clause = ", ".join(f"{k}=?" for k in updates.keys())
        values = list(updates.values()) + [now, amazon_order_id]

        conn.execute(
            f"UPDATE orders SET {set_clause}, updated_at=? WHERE amazon_order_id=?",
            values
        )

        # Audit log
        for col, new_val in updates.items():
            old_val = old.get(col)
            if old_val != new_val:
                _log_audit(conn, amazon_order_id, col, old_val, new_val, changed_by)

        return True


def list_orders(
    account: str = None,
    status: str = None,
    delivery_status: str = None,
    label: str = None,
    limit: int = 1000,
    offset: int = 0,
    db_path=None
) -> list[dict]:
    """List orders with optional filters. Returns list of dicts."""
    with get_conn(db_path) as conn:
        where_clauses = []
        params = []

        if account:
            where_clauses.append("account = ?")
            params.append(account)
        if status:
            where_clauses.append("order_status = ?")
            params.append(status)
        if delivery_status:
            where_clauses.append("delivery_status = ?")
            params.append(delivery_status)
        if label:
            where_clauses.append("order_label = ?")
            params.append(label)

        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        sql = f"SELECT * FROM orders {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_orders_needing_fedex_check(db_path=None, hours_since_last: int = 2) -> list[dict]:
    """
    Return orders that need FedEx re-checking:
      - Have tracking IDs
      - delivery_status is NOT 'Delivered'
      - updated_at is older than `hours_since_last` OR delivery_status is empty
    """
    with get_conn(db_path) as conn:
        cutoff = datetime.utcnow().isoformat()
        rows = conn.execute("""
            SELECT * FROM orders
            WHERE tracking_ids != ''
              AND (delivery_status NOT IN ('Delivered', 'AllDelivered') OR delivery_status = '')
              AND (updated_at < datetime('now', '-{} hours') OR delivery_status = '')
            ORDER BY created_at DESC
            LIMIT 500
        """.format(hours_since_last)).fetchall()
        return [dict(r) for r in rows]


def get_orders_needing_delivery_message(db_path=None, days_after_delivery: int = 6) -> list[dict]:
    """
    Return orders eligible for delivery check-in message:
      - delivery_status = 'Delivered'
      - delivery_date is at least `days_after_delivery` days ago
      - delivered_msg_sent IS NULL
      - return_requested != 'Yes'
      - refund_done != 'Yes'
    """
    with get_conn(db_path) as conn:
        rows = conn.execute("""
            SELECT * FROM orders
            WHERE delivery_status IN ('Delivered', 'AllDelivered')
              AND delivery_date <= date('now', '-{} days')
              AND (delivered_msg_sent IS NULL OR delivered_msg_sent = 'No')
              AND (return_requested IS NULL OR return_requested != 'Yes')
              AND (refund_done IS NULL OR refund_done != 'Yes')
            ORDER BY delivery_date ASC
            LIMIT 200
        """.format(days_after_delivery)).fetchall()
        return [dict(r) for r in rows]


def get_orders_needing_review_request(db_path=None, days_after_delivery: int = 12) -> list[dict]:
    """
    Return orders eligible for review request:
      - delivery_status = 'Delivered'
      - delivery_date is at least `days_after_delivery` days ago
      - review_requested IS NULL
      - return_requested != 'Yes'
      - refund_done != 'Yes'
    """
    with get_conn(db_path) as conn:
        rows = conn.execute("""
            SELECT * FROM orders
            WHERE delivery_status IN ('Delivered', 'AllDelivered')
              AND delivery_date <= date('now', '-{} days')
              AND (review_requested IS NULL OR review_requested = 'No')
              AND (return_requested IS NULL OR return_requested != 'Yes')
              AND (refund_done IS NULL OR refund_done != 'Yes')
            ORDER BY delivery_date ASC
            LIMIT 200
        """.format(days_after_delivery)).fetchall()
        return [dict(r) for r in rows]


def get_orders_with_transit_alert(db_path=None, days_past_deliver_by: int = 3) -> list[dict]:
    """
    Return orders that are stuck in transit past deliver-by date:
      - delivery_status NOT IN ('Delivered', '')
      - deliver_by_date is at least `days_past_deliver_by` days ago
      - customer_messaged IS NULL (haven't been alerted yet)
    """
    with get_conn(db_path) as conn:
        rows = conn.execute("""
            SELECT * FROM orders
            WHERE delivery_status NOT IN ('Delivered', '')
              AND delivery_status != ''
              AND deliver_by_date <= date('now', '-{} days')
              AND (customer_messaged IS NULL OR customer_messaged = 'No')
            ORDER BY deliver_by_date ASC
            LIMIT 100
        """.format(days_past_deliver_by)).fetchall()
        return [dict(r) for r in rows]


# ── Closing Orders query helpers ─────────────────────────────────────────────
def get_closing_orders_needing_fedex_check(db_path=None, hours_since_last: int = 2, limit: int = 500) -> list[dict]:
    """Return closing_orders rows needing FedEx re-check."""
    with get_conn(db_path) as conn:
        rows = conn.execute("""
            SELECT * FROM closing_orders
            WHERE tracking_ids != ''
              AND (delivery_status NOT IN ('Delivered', 'AllDelivered') OR delivery_status = '')
              AND (updated_at < datetime('now', '-{} hours') OR delivery_status = '')
            ORDER BY updated_at ASC
            LIMIT {}
        """.format(hours_since_last, limit)).fetchall()
        return [dict(r) for r in rows]


def get_closing_orders_needing_delivery_message(db_path=None, days_after_delivery: int = 6, limit: int = 200) -> list[dict]:
    """Return closing_orders rows eligible for delivery check-in message."""
    with get_conn(db_path) as conn:
        rows = conn.execute("""
            SELECT * FROM closing_orders
            WHERE delivery_status IN ('Delivered', 'AllDelivered')
              AND delivery_date <= date('now', '-{} days')
              AND (delivered_msg_sent IS NULL OR delivered_msg_sent = 'No')
              AND (return_requested IS NULL OR return_requested != 'Yes')
              AND (refund_done IS NULL OR refund_done != 'Yes')
            ORDER BY delivery_date ASC
            LIMIT {}
        """.format(days_after_delivery, limit)).fetchall()
        return [dict(r) for r in rows]


def get_closing_orders_needing_review_request(db_path=None, days_after_delivery: int = 12, limit: int = 200) -> list[dict]:
    """Return closing_orders rows eligible for review request."""
    with get_conn(db_path) as conn:
        rows = conn.execute("""
            SELECT * FROM closing_orders
            WHERE delivery_status IN ('Delivered', 'AllDelivered')
              AND delivery_date <= date('now', '-{} days')
              AND (review_requested IS NULL OR review_requested = 'No')
              AND (return_requested IS NULL OR return_requested != 'Yes')
              AND (refund_done IS NULL OR refund_done != 'Yes')
            ORDER BY delivery_date ASC
            LIMIT {}
        """.format(days_after_delivery, limit)).fetchall()
        return [dict(r) for r in rows]


# ── Audit & Action logging ───────────────────────────────────────────────────
def _log_audit(conn, amazon_order_id: str, field: str, old_val, new_val, changed_by: str):
    conn.execute("""
        INSERT INTO audit_log (amazon_order_id, field_changed, old_value, new_value, changed_by)
        VALUES (?, ?, ?, ?, ?)
    """, (amazon_order_id, field, str(old_val) if old_val is not None else None,
          str(new_val) if new_val is not None else None, changed_by))


def log_action(amazon_order_id: str, action_type: str, status: str = "success",
               detail: str = "", db_path=None):
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO action_log (amazon_order_id, action_type, status, detail)
            VALUES (?, ?, ?, ?)
        """, (amazon_order_id, action_type, status, detail))


# ── Stats ────────────────────────────────────────────────────────────────────
def get_stats(db_path=None) -> dict:
    """Return quick stats about the database."""
    with get_conn(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        by_account = conn.execute("""
            SELECT account, COUNT(*) FROM orders GROUP BY account
        """).fetchall()
        by_label = conn.execute("""
            SELECT order_label, COUNT(*) FROM orders GROUP BY order_label
        """).fetchall()
        by_delivery = conn.execute("""
            SELECT delivery_status, COUNT(*) FROM orders
            WHERE delivery_status != '' GROUP BY delivery_status
        """).fetchall()
        with_tracking = conn.execute("""
            SELECT COUNT(*) FROM orders
            WHERE tracking_ids IS NOT NULL AND tracking_ids != ''
        """).fetchone()[0]
        delivered = conn.execute("""
            SELECT COUNT(*) FROM orders
            WHERE delivery_status IN ('Delivered', 'AllDelivered')
        """).fetchone()[0]
        with_earnings = conn.execute("""
            SELECT COUNT(*) FROM orders
            WHERE earnings IS NOT NULL AND earnings > 0
        """).fetchone()[0]
        delivered_no_msg = conn.execute("""
            SELECT COUNT(*) FROM orders
            WHERE delivery_status IN ('Delivered', 'AllDelivered')
              AND delivered_msg_sent IS NULL
        """).fetchone()[0]
        delivered_no_review = conn.execute("""
            SELECT COUNT(*) FROM orders
            WHERE delivery_status IN ('Delivered', 'AllDelivered')
              AND review_requested IS NULL
        """).fetchone()[0]

    return {
        "total_orders": total,
        "by_account": {r[0]: r[1] for r in by_account},
        "by_label": {r[0]: r[1] for r in by_label},
        "by_delivery": {r[0]: r[1] for r in by_delivery},
        "with_tracking": with_tracking,
        "delivered": delivered,
        "with_earnings": with_earnings,
        "delivered_no_message": delivered_no_msg,
        "delivered_no_review": delivered_no_review,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  NEW CRUD: Multi-table architecture helpers
# ═══════════════════════════════════════════════════════════════════════════════

# ── order_placer_orders ──────────────────────────────────────────────────────

# Raw Zoro price facts, one column each, never derived from one another:
#   zoro_list_price  — the undiscounted per-unit list price Zoro returned at cart-add
#                      ($2.19 for G3057148, $329.99 for a G7747482)
#   zoro_subtotal    — merchandise subtotal Zoro reported for the checkout
#   zoro_shipping    — shipping Zoro charged
# (zoro_order_total, zoro_tax, zoro_qty, coupon_savings already exist.)
#
# Why: a price-change alert used to be produced by dividing an order total by a quantity and
# un-applying a coupon by name. For G3057148 — MOQ 25, so Amazon qty 1 and Zoro qty 25 on the
# same $43.80 order — that gives $43.80/unit one day and $1.75/unit the next. Recording the
# list price Zoro itself states lets the alert compare like with like.
PLACER_PRICE_COLUMNS = {"zoro_list_price": "REAL", "zoro_subtotal": "REAL", "zoro_shipping": "REAL"}
# What the tracker confirmed on Amazon (26 Sep 2026): written by engine/tracking.py at upload
# time so closing never has to load the Seller Central order page to learn it.
PLACER_UPLOAD_COLUMNS = {"tracking_id_uploaded": "TEXT", "tracking_upload_date": "TEXT",
                         "tracking_carrier_uploaded": "TEXT"}
_placer_cols_ensured_for: set = set()


def ensure_placer_price_columns(db_path=None, conn=None) -> list:
    """Add the raw price columns to order_placer_orders if missing. Idempotent; cached per
    process per database so the PRAGMA runs once, not per order. Returns the columns added."""
    key = str(db_path or "default")
    if key in _placer_cols_ensured_for and conn is None:
        return []
    added = []

    def _do(c):
        have = {r[1] for r in c.execute("PRAGMA table_info(order_placer_orders)")}
        for col, typ in {**PLACER_PRICE_COLUMNS, **PLACER_UPLOAD_COLUMNS}.items():
            if col not in have:
                c.execute(f"ALTER TABLE order_placer_orders ADD COLUMN {col} {typ}")
                added.append(col)

    if conn is not None:
        _do(conn)
    else:
        with get_conn(db_path) as c:
            _do(c)
        _placer_cols_ensured_for.add(key)
    return added


def last_list_price(sku: str, exclude_amazon_id: str = "", db_path=None, conn=None):
    """The most recent recorded zoro_list_price for this Zoro code, or None.

    Excludes the order being evaluated so a first placement seeds the baseline instead of
    comparing with itself. Only rows that carry a list price count — older rows, written
    before the column existed, are not a baseline."""
    sku = str(sku or "").strip().upper()
    if not sku:
        return None
    sql = ("SELECT zoro_list_price FROM order_placer_orders "
           "WHERE UPPER(sku)=? AND zoro_list_price IS NOT NULL AND zoro_list_price > 0 "
           "AND amazon_order_id != ? ORDER BY id DESC LIMIT 1")
    args = (sku, str(exclude_amazon_id or ""))
    try:
        if conn is not None:
            row = conn.execute(sql, args).fetchone()
        else:
            with get_conn(db_path) as c:
                row = c.execute(sql, args).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def upsert_placer_order(data: dict, db_path=None) -> bool:
    # The table predates these columns; make sure they exist before a row that carries them.
    try:
        if any(k in data for k in PLACER_PRICE_COLUMNS) or any(k in data for k in PLACER_UPLOAD_COLUMNS):
            ensure_placer_price_columns(db_path)
    except Exception:
        pass
    """Insert or replace an order_placer_orders row."""
    with get_conn(db_path) as conn:
        cols = list(data.keys())
        placeholders = ", ".join("?" for _ in cols)
        upsert_cols = [c for c in cols if c != "amazon_order_id"]
        # Never blank a stored value with a NULL or '' from the caller. Two writers fill this
        # table: the placer writes what it knows the moment an order is placed, and the log
        # sync fills the rest from order_log.txt (the source of truth) — including fields that
        # only arrive later, such as tracking_ids. Plain excluded.<col> let whichever ran last
        # erase the other's work; a real value always wins over an absent one.
        set_clause = ", ".join(f"{c}=COALESCE(NULLIF(excluded.{c}, ''), {c})" for c in upsert_cols)
        sql = f"""
            INSERT INTO order_placer_orders ({', '.join(cols)})
            VALUES ({placeholders})
            ON CONFLICT(amazon_order_id) DO UPDATE SET {set_clause}, synced_at=CURRENT_TIMESTAMP
        """
        conn.execute(sql, [data.get(c) for c in cols])
        return True


def get_placer_order(amazon_order_id: str, db_path=None) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM order_placer_orders WHERE amazon_order_id=?",
            (amazon_order_id,)
        ).fetchone()
        return dict(row) if row else None


def get_placer_order_by_wb(wb_order_id: str, db_path=None) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM order_placer_orders WHERE wb_order_id=?",
            (wb_order_id,)
        ).fetchone()
        return dict(row) if row else None


_EMPTY_TEXT_SQL = "({c} IS NULL OR TRIM(CAST({c} AS TEXT)) IN ('','None'))"


def record_tracking_upload(amazon_order_id: str, wb_ids, tracking_ids, status: str,
                           carrier: str = "", upload_date: str = "", note: str = "",
                           db_path=None) -> bool:
    """The tracker's durable record of what it confirmed on Amazon (26 Sep 2026).

    Before this, the only trace of an upload was data/tracking_upload_log.json — capped at
    5,000 entries and flooded by ~4,900 'no_tracking' rows a day — so closing could not see
    the uploads and re-read tracking_id_uploaded from ~3,000 Seller Central pages per run.
    Now a 'success' overwrites order_placer_orders.tracking_id_uploaded (the upload is the
    truth), an 'already_shipped' read off the order page only fills a blank, closing_orders
    is gap-filled the same way, and every event lands in action_log. Best-effort: never
    raises into the upload flow."""
    import json
    aid = str(amazon_order_id or "").strip()
    if not aid:
        return False
    raw = tracking_ids if isinstance(tracking_ids, (list, tuple)) else str(tracking_ids or "").split(",")
    tids, seen = [], set()
    for t in raw:
        t = str(t or "").strip()
        if t and t not in seen:
            seen.add(t)
            tids.append(t)
    joined = ", ".join(tids)
    if not carrier and tids:
        carrier = "UPS" if tids[0].upper().startswith("1Z") else ("USPS" if len(tids[0]) >= 20 else "FedEx")
    when = upload_date or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok = True
    if status in ("success", "already_shipped") and joined:
        try:
            ensure_placer_price_columns(db_path)
        except Exception:
            pass
        overwrite = status == "success"
        try:
            with get_conn(db_path) as conn:
                for table in ("order_placer_orders", "closing_orders"):
                    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                    if not PLACER_UPLOAD_COLUMNS.keys() <= cols:
                        continue
                    if overwrite:
                        conn.execute(
                            f"UPDATE {table} SET tracking_id_uploaded=?, tracking_upload_date=?, "
                            f"tracking_carrier_uploaded=? WHERE amazon_order_id=?",
                            (joined, when, carrier, aid))
                    else:
                        conn.execute(
                            f"UPDATE {table} SET "
                            f"tracking_id_uploaded=CASE WHEN {_EMPTY_TEXT_SQL.format(c='tracking_id_uploaded')} THEN ? ELSE tracking_id_uploaded END, "
                            f"tracking_upload_date=CASE WHEN {_EMPTY_TEXT_SQL.format(c='tracking_upload_date')} THEN ? ELSE tracking_upload_date END, "
                            f"tracking_carrier_uploaded=CASE WHEN {_EMPTY_TEXT_SQL.format(c='tracking_carrier_uploaded')} THEN ? ELSE tracking_carrier_uploaded END "
                            f"WHERE amazon_order_id=?",
                            (joined, when, carrier, aid))
        except Exception as e:
            ok = False
            logger.warning("record_tracking_upload %s: %s", aid, e)
    try:
        detail = json.dumps({"wb": list(wb_ids) if isinstance(wb_ids, (list, tuple)) else [wb_ids],
                             "tracking_ids": tids, "carrier": carrier, "note": note})[:2000]
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT INTO action_log (amazon_order_id, action_type, status, detail) VALUES (?, ?, ?, ?)",
                (aid, "tracking_upload", status, detail))
    except Exception as e:
        logger.warning("record_tracking_upload action_log %s: %s", aid, e)
    return ok


def tracking_uploads_from_action_log(amazon_order_ids=None, db_path=None) -> dict:
    """{amazon_order_id: {tracking_id_uploaded, tracking_upload_date, tracking_carrier_uploaded}}
    from action_log rows the tracker wrote (latest success wins, then already_shipped)."""
    import json
    out = {}
    try:
        with get_conn(db_path) as conn:
            sql = ("SELECT amazon_order_id, status, detail, performed_at FROM action_log "
                   "WHERE action_type='tracking_upload' AND status IN ('success','already_shipped')")
            args = []
            ids = list(amazon_order_ids or [])
            rows = []
            if ids:
                for i in range(0, len(ids), 800):
                    chunk = ids[i:i + 800]
                    rows.extend(conn.execute(sql + f" AND amazon_order_id IN ({','.join('?' * len(chunk))}) "
                                             "ORDER BY performed_at", chunk).fetchall())
            else:
                rows = conn.execute(sql + " ORDER BY performed_at", args).fetchall()
        for r in rows:
            try:
                d = json.loads(r["detail"] or "{}")
            except Exception:
                d = {}
            tids = [t for t in (d.get("tracking_ids") or []) if t]
            if not tids:
                continue
            prev = out.get(r["amazon_order_id"])
            if prev and prev.get("_status") == "success" and r["status"] != "success":
                continue
            out[r["amazon_order_id"]] = {"tracking_id_uploaded": ", ".join(tids),
                                         "tracking_upload_date": r["performed_at"],
                                         "tracking_carrier_uploaded": d.get("carrier") or "",
                                         "_status": r["status"]}
    except Exception as e:
        logger.warning("tracking_uploads_from_action_log: %s", e)
    return out


# ── Zoro delivery tracking (27 Sep 2026) ─────────────────────────────────────
ZORO_PACKAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS zoro_package_tracking (
    wb_order_id        TEXT NOT NULL,
    tracking_number    TEXT NOT NULL,
    amazon_order_id    TEXT,
    carrier            TEXT,
    status             TEXT,
    delivered_at       TEXT,
    shipped_at         TEXT,
    last_event         TEXT,
    last_event_at      TEXT,
    last_exception     TEXT,
    last_location      TEXT,
    last_movement_at   TEXT,
    estimated_delivery TEXT,
    delayed            INTEGER DEFAULT 0,
    n_events           INTEGER DEFAULT 0,
    events_json        TEXT,
    url                TEXT,
    checked_at         TEXT,
    PRIMARY KEY (wb_order_id, tracking_number)
);
CREATE INDEX IF NOT EXISTS idx_zpt_amazon ON zoro_package_tracking(amazon_order_id);
CREATE INDEX IF NOT EXISTS idx_zpt_status ON zoro_package_tracking(status);
"""
# closing_orders columns the Zoro delivery check and the delay notices write.
ZORO_TRACKING_COLUMNS = {
    "zoro_order_status": "TEXT",      # Zoro's own order status text
    "delivery_source": "TEXT",        # 'zoro' | 'fedex_api' | 'ups_api' | 'manual'
    "delivery_checked_at": "TEXT",    # last time a delivery source answered for this order
    "last_movement_at": "TEXT",       # latest physical carrier scan across all packages
    "stalled_days": "INTEGER",        # days since last_movement_at while undelivered
    "delay_notice_sent": "TEXT",      # Yes / No Permission / No
    "delay_notice_at": "TEXT",
}


def ensure_zoro_tracking_schema(db_path=None) -> list:
    """Create the package table and add the closing_orders columns (idempotent)."""
    added = []
    with get_conn(db_path) as conn:
        conn.executescript(ZORO_PACKAGE_SCHEMA)
        have = {r[1] for r in conn.execute("PRAGMA table_info(closing_orders)")}
        for col, typ in ZORO_TRACKING_COLUMNS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE closing_orders ADD COLUMN {col} {typ}")
                added.append(col)
    return added


def upsert_package_tracking(rows: list, conn=None, db_path=None) -> int:
    """rows: dicts with the zoro_package_tracking columns (events_json already a string)."""
    if not rows:
        return 0
    cols = ["wb_order_id", "tracking_number", "amazon_order_id", "carrier", "status", "delivered_at",
            "shipped_at", "last_event", "last_event_at", "last_exception", "last_location",
            "last_movement_at", "estimated_delivery", "delayed", "n_events", "events_json", "url", "checked_at"]
    sql = (f"INSERT INTO zoro_package_tracking ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
           f"ON CONFLICT(wb_order_id, tracking_number) DO UPDATE SET "
           + ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("wb_order_id", "tracking_number")))
    vals = [[r.get(c) for c in cols] for r in rows]
    if conn is not None:
        conn.executemany(sql, vals)
        return len(vals)
    with get_conn(db_path) as c:
        c.executemany(sql, vals)
    return len(vals)


# ── Amazon's own order list (27 Sep 2026) ────────────────────────────────────
# closing_orders holds only orders that were placed on Zoro (a WB id), so the orders Amazon
# took that were never placed — cancelled, OOS, low profit, state-restricted, waiting — were
# nowhere in the DB (6.5 % of Hathoro's last 30 days). scripts/sync_amazon_report.py folds
# every downloaded all-orders report into these two tables; the dashboard's daily report
# counts days from them so its numbers are Seller Central's numbers.
AMAZON_REPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS amazon_report_orders (
    amazon_order_id   TEXT PRIMARY KEY,
    account           TEXT NOT NULL,
    purchase_date     TEXT,      -- US/Pacific 'YYYY-MM-DD HH:MM:SS' (Seller Central's clock)
    purchase_day      TEXT,      -- US/Pacific 'YYYY-MM-DD'
    last_updated      TEXT,      -- report last-updated-date, US/Pacific; the newer report wins
    order_status      TEXT,      -- Shipped | Pending | Cancelled | Unshipped | …
    merchant_order_id TEXT,      -- WB… when placed; OOS / SR / Loss / Discontinued … when not
    items             INTEGER,   -- order lines
    units             INTEGER,
    sales             REAL,      -- Σ item-price
    shipping          REAL,      -- Σ shipping-price
    item_status       TEXT,
    skus              TEXT,
    asins             TEXT,
    title             TEXT,
    ship_city         TEXT,
    ship_state        TEXT,
    ship_postal_code  TEXT,
    is_business       TEXT,
    fulfillment       TEXT,
    sales_channel     TEXT,
    report_file       TEXT,
    captured_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_aro_day ON amazon_report_orders(purchase_day, account);
CREATE TABLE IF NOT EXISTS amazon_report_files (
    report_file  TEXT PRIMARY KEY,   -- Amazon's report id file name
    account      TEXT,
    path         TEXT,
    mtime        REAL,
    rows         INTEGER,
    orders       INTEGER,
    span_from    TEXT,               -- first / last US/Pacific purchase day in the file
    span_to      TEXT,
    ingested_at  TEXT
);
"""


def ensure_amazon_report_schema(db_path=None) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(AMAZON_REPORT_SCHEMA)


def wal_checkpoint(mode: str = "TRUNCATE", db_path=None, busy_ms: int = 30000) -> dict:
    """PRAGMA wal_checkpoint(mode) → {'busy', 'wal_pages', 'checkpointed'}. TRUNCATE also
    resets the -wal file to zero bytes when no reader is still using it."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path, timeout=busy_ms / 1000.0)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(busy_ms)}")
        row = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return {"busy": int(row[0]), "wal_pages": int(row[1]), "checkpointed": int(row[2])}
    finally:
        conn.close()


def wal_size_mb(db_path=None) -> float:
    p = str(db_path or DB_PATH) + "-wal"
    return round(os.path.getsize(p) / 1048576, 1) if os.path.exists(p) else 0.0


# ── closing_orders ────────────────────────────────────────────────────────────

def upsert_closing_order(data: dict, db_path=None, conn=None) -> bool:
    """Insert or replace a closing_orders row.
    
    If conn is provided, it is used directly (caller must commit).
    Otherwise a new connection is opened via get_conn().
    """
    # Every closing row goes through here, so this is the one place that can guarantee a
    # tracking number is never stored twice. 360 orders held exact doublings on 19 Sep — 4
    # ids written as 8 — and the delivery message then told the buyer their order shipped in
    # 8 separate packages, listing each number twice (111-1549917-2638602 went out that way).
    # The writers each dedupe their own input; a merge of two already-clean sets does not.
    tids = data.get("tracking_ids")
    if isinstance(tids, str) and "," in tids:
        seen, keep = set(), []
        for t in tids.split(","):
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                keep.append(t)
        cleaned = ", ".join(keep)
        if cleaned != tids:
            data = {**data, "tracking_ids": cleaned}

    cols = list(data.keys())
    placeholders = ", ".join("?" for _ in cols)
    upsert_cols = [c for c in cols if c != "amazon_order_id"]
    set_clause = ", ".join(f"{c}=excluded.{c}" for c in upsert_cols)
    sql = f"""
        INSERT INTO closing_orders ({', '.join(cols)})
        VALUES ({placeholders})
        ON CONFLICT(amazon_order_id) DO UPDATE SET {set_clause}, updated_at=CURRENT_TIMESTAMP
    """
    if conn is not None:
        conn.execute(sql, [data.get(c) for c in cols])
        return True
    with get_conn(db_path) as conn:
        conn.execute(sql, [data.get(c) for c in cols])
        return True


def get_closing_order(amazon_order_id: str, db_path=None) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM closing_orders WHERE amazon_order_id=?",
            (amazon_order_id,)
        ).fetchone()
        return dict(row) if row else None


def list_closing_orders_by_run(run_id: str, db_path=None) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM closing_orders WHERE last_enrichment_run=?",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── enrichment_log ────────────────────────────────────────────────────────────

def log_enrichment(run_id: str, amazon_order_id: str, source: str, field: str,
                   old_value=None, new_value=None, confidence: float = 1.0,
                   db_path=None, conn=None):
    """Log an enrichment event. If conn is provided, uses it directly."""
    vals = (run_id, amazon_order_id, source, field,
            str(old_value) if old_value is not None else None,
            str(new_value) if new_value is not None else None, confidence)
    if conn is not None:
        conn.execute("""
            INSERT INTO enrichment_log (run_id, amazon_order_id, source, field, old_value, new_value, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, vals)
    else:
        with get_conn(db_path) as conn2:
            conn2.execute("""
                INSERT INTO enrichment_log (run_id, amazon_order_id, source, field, old_value, new_value, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, vals)


def get_enrichment_history(amazon_order_id: str, db_path=None) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM enrichment_log WHERE amazon_order_id=? ORDER BY timestamp",
            (amazon_order_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── closing_run_log ───────────────────────────────────────────────────────────

def start_closing_run(run_id: str, date_from: str, date_to: str,
                      accounts: list[str] | None = None, db_path=None) -> str:
    import json
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO closing_run_log (run_id, date_from, date_to, accounts, status)
            VALUES (?, ?, ?, ?, 'running')
        """, (run_id, date_from, date_to, json.dumps(accounts) if accounts else None))
    return run_id


def update_closing_run(run_id: str, updates: dict, db_path=None):
    """updates: dict of column -> value for closing_run_log."""
    with get_conn(db_path) as conn:
        set_clause = ", ".join(f"{k}=?" for k in updates.keys())
        conn.execute(
            f"UPDATE closing_run_log SET {set_clause} WHERE run_id=?",
            list(updates.values()) + [run_id]
        )


def finish_closing_run(run_id: str, status: str = 'completed', db_path=None):
    from datetime import datetime
    update_closing_run(run_id, {
        "status": status,
        "completed_at": datetime.utcnow().isoformat()
    }, db_path=db_path)


# ── fedex_tracking_cache ──────────────────────────────────────────────────────

def get_fedex_cache(tracking_id: str, db_path=None) -> dict | None:
    from datetime import datetime
    with get_conn(db_path) as conn:
        row = conn.execute("""
            SELECT * FROM fedex_tracking_cache
            WHERE tracking_id=? AND (expires_at IS NULL OR expires_at > ?)
        """, (tracking_id, datetime.utcnow().isoformat())).fetchone()
        return dict(row) if row else None


def set_fedex_cache(tracking_id: str, response_json: str,
                    expires_hours: int = 6, db_path=None):
    from datetime import datetime, timedelta
    expires = (datetime.utcnow() + timedelta(hours=expires_hours)).isoformat()
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO fedex_tracking_cache (tracking_id, response_json, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(tracking_id) DO UPDATE SET response_json=excluded.response_json,
                                                   fetched_at=CURRENT_TIMESTAMP,
                                                   expires_at=excluded.expires_at
        """, (tracking_id, response_json, expires))


# ── raw_api_log ───────────────────────────────────────────────────────────────

def log_api_call(run_id: str, endpoint: str, request_body: str = "",
                 response_body: str = "", status_code: int = None,
                 duration_ms: int = None, error: str = None, db_path=None):
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO raw_api_log (run_id, endpoint, request_body, response_body,
                                     status_code, duration_ms, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (run_id, endpoint, request_body[:4000], response_body[:4000],
              status_code, duration_ms, error))


# ── archive cache helpers ─────────────────────────────────────────────────────

def upsert_archive_earnings(data: dict, db_path=None):
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO archive_earnings (amazon_order_id, gmail_account, email_timestamp,
                order_date, ship_by_date, shipping_method, num_items, product_names, skus,
                asins, quantities, item_prices, total_tax, total_shipping,
                total_amazon_fees, total_your_earnings, order_subtotal, raw_body_snippet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(amazon_order_id) DO UPDATE SET
                total_your_earnings=excluded.total_your_earnings,
                total_amazon_fees=excluded.total_amazon_fees,
                fetched_at=CURRENT_TIMESTAMP
        """, (data.get("amazon_order_id"), data.get("gmail_account"),
              data.get("email_timestamp"), data.get("order_date"),
              data.get("ship_by_date"), data.get("shipping_method"),
              data.get("num_items"), data.get("product_names"),
              data.get("skus"), data.get("asins"), data.get("quantities"),
              data.get("item_prices"), data.get("total_tax"),
              data.get("total_shipping"), data.get("total_amazon_fees"),
              data.get("total_your_earnings"), data.get("order_subtotal"),
              data.get("raw_body_snippet")))


def upsert_archive_zoro(data: dict, db_path=None):
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO archive_zoro (wb_order_id, gmail_account, zoro_account_name,
                email_timestamp, order_date, po_number, order_status, sku, qty,
                unit_price, line_total, subtotal, tax, shipping_cost, order_total,
                coupon_savings, ship_name, ship_street, ship_city, ship_state,
                ship_zip, payment_method, payment_card_last4, raw_body_snippet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(wb_order_id) DO UPDATE SET
                order_total=excluded.order_total,
                coupon_savings=excluded.coupon_savings,
                fetched_at=CURRENT_TIMESTAMP
        """, (data.get("wb_order_id"), data.get("gmail_account"),
              data.get("zoro_account_name"), data.get("email_timestamp"),
              data.get("order_date"), data.get("po_number"),
              data.get("order_status"), data.get("sku"), data.get("qty"),
              data.get("unit_price"), data.get("line_total"),
              data.get("subtotal"), data.get("tax"), data.get("shipping_cost"),
              data.get("order_total"), data.get("coupon_savings"),
              data.get("ship_name"), data.get("ship_street"),
              data.get("ship_city"), data.get("ship_state"),
              data.get("ship_zip"), data.get("payment_method"),
              data.get("payment_card_last4"), data.get("raw_body_snippet")))


def upsert_archive_tracking(data: dict, db_path=None):
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO archive_tracking (wb_order_id, tracking_ids, carrier,
                ship_date, delivery_method, ship_to_name, ship_to_street,
                ship_to_city, ship_to_state, ship_to_zip, num_items, item_skus,
                item_names, item_quantities, gmail_account, email_timestamp,
                raw_body_snippet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (data.get("wb_order_id"), data.get("tracking_ids"),
              data.get("carrier"), data.get("ship_date"),
              data.get("delivery_method"), data.get("ship_to_name"),
              data.get("ship_to_street"), data.get("ship_to_city"),
              data.get("ship_to_state"), data.get("ship_to_zip"),
              data.get("num_items"), data.get("item_skus"),
              data.get("item_names"), data.get("item_quantities"),
              data.get("gmail_account"), data.get("email_timestamp"),
              data.get("raw_body_snippet")))


def upsert_archive_return(data: dict, db_path=None):
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO archive_returns (amazon_order_id, gmail_account,
                email_timestamp, return_reason, raw_body_snippet)
            VALUES (?, ?, ?, ?, ?)
        """, (data.get("amazon_order_id"), data.get("gmail_account"),
              data.get("email_timestamp"), data.get("return_reason"),
              data.get("raw_body_snippet")))


def upsert_archive_message(data: dict, db_path=None):
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO archive_messages (amazon_order_id, gmail_account,
                email_timestamp, message_type, raw_body_snippet)
            VALUES (?, ?, ?, ?, ?)
        """, (data.get("amazon_order_id"), data.get("gmail_account"),
              data.get("email_timestamp"), data.get("message_type"),
              data.get("raw_body_snippet")))
