#!/usr/bin/env python3
"""
gmail_archive.py — Comprehensive Gmail Archive Database (v5)

One-time bulk build + incremental sync. Extracts MAXIMUM detail from every
email type: earnings, zoro orders, tracking, receipts, returns, refunds,
buyer inquiries, seller messages, and buyer replies.
"""

from __future__ import annotations

import json
import logging
import os
import re as _re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("gmail_archive")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_INIT_SQL = """
-- 1. EARNINGS: Every field from "Sold, ship now" emails
CREATE TABLE IF NOT EXISTS earnings_emails (
    amazon_order_id      TEXT PRIMARY KEY,
    gmail_account        TEXT,
    email_timestamp      TEXT,
    order_date           TEXT,
    ship_by_date         TEXT,
    shipping_method      TEXT,
    num_items            INTEGER DEFAULT 1,
    product_names        TEXT,        -- JSON array
    skus                 TEXT,        -- JSON array
    asins                TEXT,        -- JSON array
    quantities           TEXT,        -- JSON array
    item_prices          TEXT,        -- JSON array
    conditions           TEXT,        -- JSON array
    amazon_fees_per_item TEXT,        -- JSON array
    earnings_per_item    TEXT,        -- JSON array
    tax_per_item         TEXT,        -- JSON array
    shipping_per_item    TEXT,        -- JSON array
    mft_per_item         TEXT,        -- JSON array
    total_tax            REAL,
    total_shipping       REAL,
    marketplace_facilitator_tax REAL,
    total_amazon_fees    REAL,
    total_your_earnings  REAL,
    order_subtotal       REAL,
    raw_body_snippet     TEXT,
    fetched_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. ZORO ORDERS: Every field from "Has Been Received" emails
CREATE TABLE IF NOT EXISTS zoro_emails (
    wb_order_id        TEXT PRIMARY KEY,
    gmail_account      TEXT,
    zoro_account_name  TEXT,
    email_timestamp    TEXT,
    order_date         TEXT,
    po_number          TEXT,
    order_status       TEXT DEFAULT 'Active',
    product_name       TEXT,
    sku                TEXT,
    qty                INTEGER,
    unit_price         REAL,
    line_total         REAL,
    subtotal           REAL,
    tax                REAL,
    shipping_cost      REAL,
    order_total        REAL,
    coupon_savings     REAL DEFAULT 0.0,
    ship_name          TEXT,
    ship_street        TEXT,
    ship_city          TEXT,
    ship_state         TEXT,
    ship_zip           TEXT,
    bill_company       TEXT,
    bill_name          TEXT,
    bill_street        TEXT,
    bill_city          TEXT,
    bill_state         TEXT,
    bill_zip           TEXT,
    payment_method     TEXT,
    payment_card_last4 TEXT,
    raw_body_snippet   TEXT,
    fetched_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. TRACKING: From Zoro shipped emails
CREATE TABLE IF NOT EXISTS tracking_emails (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    wb_order_id       TEXT,
    tracking_ids      TEXT,        -- comma-separated
    carrier           TEXT,
    ship_date         TEXT,
    delivery_method   TEXT,
    ship_to_name      TEXT,
    ship_to_street    TEXT,
    ship_to_city      TEXT,
    ship_to_state     TEXT,
    ship_to_zip       TEXT,
    num_items         INTEGER,
    item_skus         TEXT,        -- JSON array
    item_names        TEXT,        -- JSON array
    item_quantities   TEXT,        -- JSON array
    gmail_account     TEXT,
    email_timestamp   TEXT,
    raw_body_snippet  TEXT,
    fetched_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. RECEIPTS: From Zoro receipt PDFs
CREATE TABLE IF NOT EXISTS receipts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    wb_order_id        TEXT,
    receipt_number     TEXT,
    bill_to_name       TEXT,
    bill_to_company    TEXT,
    ship_to_name       TEXT,
    ship_to_address    TEXT,
    date               TEXT,
    amount_paid        REAL,
    po_number          TEXT,
    payment_method     TEXT,
    payment_card_last4 TEXT,
    customer_number    TEXT,
    shipping_method    TEXT,
    ship_date          TEXT,
    tracking_numbers   TEXT,
    subtotal           REAL,
    shipping_cost      REAL,
    tax                REAL,
    total              REAL,
    raw_text           TEXT,
    fetched_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. RETURNS & REFUNDS: Combined table with full details
CREATE TABLE IF NOT EXISTS return_emails (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id         TEXT,
    email_type              TEXT,  -- return_auth | refund_initiated
    return_requested        TEXT DEFAULT 'No',
    refund_done             TEXT DEFAULT 'No',
    return_auth_id          TEXT,
    return_request_date     TEXT,
    return_policy_check     TEXT,
    authorization_type      TEXT,
    asin                    TEXT,
    sku                     TEXT,
    return_qty              INTEGER,
    return_reason           TEXT,
    customer_comment        TEXT,
    refund_amount           REAL,
    refund_reason           TEXT,
    customer_name           TEXT,
    item_price              REAL,
    item_price_refund       REAL,
    outbound_shipping_refund REAL,
    fulfillment_network     TEXT,
    refund_resolution       TEXT,
    gmail_account           TEXT,
    email_timestamp         TEXT,
    raw_body_snippet        TEXT,
    fetched_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. MESSAGES: All buyer↔seller communication
CREATE TABLE IF NOT EXISTS message_emails (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id        TEXT,
    message_type           TEXT,
    customer_name          TEXT,
    subject                TEXT,
    direction              TEXT,  -- outbound (us→buyer), inbound (buyer→us)
    has_attachment         INTEGER DEFAULT 0,
    attachment_types       TEXT,  -- JSON array ["image", "pdf"]
    attachment_count       INTEGER DEFAULT 0,
    message_body           TEXT,
    tracking_ids_mentioned TEXT,
    is_oos                 INTEGER DEFAULT 0,
    is_loss                INTEGER DEFAULT 0,
    is_sr                  INTEGER DEFAULT 0,
    is_unable_to_fulfill   INTEGER DEFAULT 0,
    sent_tracking_details  INTEGER DEFAULT 0,
    communicated_with_customer INTEGER DEFAULT 0,
    customer_wrote         INTEGER DEFAULT 0,
    has_pictures           INTEGER DEFAULT 0,
    other_message          INTEGER DEFAULT 0,
    gmail_account          TEXT,
    email_timestamp        TEXT,
    message_id             TEXT,
    raw_body_snippet       TEXT,
    fetched_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 7. SYNC STATE
CREATE TABLE IF NOT EXISTS sync_state (
    table_name   TEXT PRIMARY KEY,
    last_sync    TIMESTAMP,
    record_count INTEGER DEFAULT 0
);
"""


def init_archive(db_path: str = "gmail_archive.db") -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(_INIT_SQL)
    conn.commit()
    conn.close()
    logger.info("[Archive] Initialized at %s", db_path)


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------

def _filter_unseen(conn: sqlite3.Connection, acct_name: str, msgs: list, label: str = "") -> list:
    """Drop Gmail message ids this account already fetched into the archive, so
    re-running a window (auto-sync every 10 min, daily_bulk --force) downloads
    only NEW mail instead of the whole window every time.
    GMAIL_REFETCH_SEEN=1 bypasses the skip (use after a parser change)."""
    conn.execute("CREATE TABLE IF NOT EXISTS seen_messages ("
                 "gmail_account TEXT NOT NULL, message_id TEXT NOT NULL, "
                 "PRIMARY KEY (gmail_account, message_id))")
    if not msgs or os.environ.get("GMAIL_REFETCH_SEEN", "").strip().lower() in ("1", "true", "yes"):
        return msgs
    seen = {r[0] for r in conn.execute(
        "SELECT message_id FROM seen_messages WHERE gmail_account=?", (acct_name,))}
    fresh = [m for m in msgs if m["id"] not in seen]
    if len(fresh) != len(msgs):
        logger.info("[Archive] %s%d of %d already archived — fetching only %d new",
                    label, len(msgs) - len(fresh), len(msgs), len(fresh))
    return fresh


def _mark_seen(conn: sqlite3.Connection, acct_name: str, fulls: list) -> None:
    conn.executemany("INSERT OR IGNORE INTO seen_messages (gmail_account, message_id) VALUES (?, ?)",
                     [(acct_name, f["id"]) for f in fulls if f.get("id")])


def _update_sync_state(conn: sqlite3.Connection, table_name: str, added_count: int) -> None:
    now = datetime.now().isoformat()
    conn.execute(
        """
        INSERT INTO sync_state (table_name, last_sync, record_count)
        VALUES (?, ?, ?)
        ON CONFLICT(table_name) DO UPDATE SET
            last_sync    = excluded.last_sync,
            record_count = sync_state.record_count + excluded.record_count
        """,
        (table_name, now, added_count),
    )


def get_last_sync(db_path: str, table_name: str) -> Optional[datetime]:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT last_sync FROM sync_state WHERE table_name = ?", (table_name,)
    ).fetchone()
    conn.close()
    if row and row[0]:
        return datetime.fromisoformat(row[0])
    return None


def get_record_count(db_path: str, table_name: str) -> int:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT record_count FROM sync_state WHERE table_name = ?", (table_name,)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# DETAILED EXTRACTION PARSERS
# ---------------------------------------------------------------------------

def _html_to_text_preserve_lines(html: str) -> str:
    """Strip HTML tags but preserve line breaks for structured parsing."""
    import html as _html
    # Remove style/script tags
    text = _re.sub(r"<(style|script).*?</\1>", "", html, flags=_re.DOTALL | _re.IGNORECASE)
    # Replace block-level tags with newlines
    text = _re.sub(r'<\s*br\s*/?\s*>', '\n', text, flags=_re.IGNORECASE)
    text = _re.sub(r'<\s*/?\s*(?:p|div|tr|h[1-6]|li)\s*>', '\n', text, flags=_re.IGNORECASE)
    text = _re.sub(r'<\s*/?\s*(?:td|th)\s*>', ' ', text, flags=_re.IGNORECASE)
    text = _re.sub(r'<\s*/?\s*span\s*>', ' ', text, flags=_re.IGNORECASE)
    # Remove remaining tags
    text = _re.sub(r'<[^>]+>', ' ', text)
    text = _html.unescape(text)
    # Collapse multiple spaces but preserve newlines
    lines = [_re.sub(r'\s+', ' ', line).strip() for line in text.split('\n')]
    return '\n'.join(line for line in lines if line)


def _message_ts(full_msg: dict) -> str:
    ts_ms = int(full_msg.get("internalDate", 0))
    if ts_ms:
        return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    return ""


def _clean_currency(value: str) -> float:
    if not value:
        return 0.0
    cleaned = value.replace("$", "").replace(",", "").replace("USD", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _extract_address_lines(text: str) -> dict:
    """Extract name, street, city, state, zip from a multi-line address block."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    result = {"name": "", "street": "", "city": "", "state": "", "zip": ""}
    if not lines:
        return result
    result["name"] = lines[0]
    # Find city/state/zip by scanning lines from bottom up
    csz_line_idx = -1
    for i in range(len(lines) - 1, 0, -1):
        line = lines[i]
        m = _re.search(r'([A-Za-z]+(?:\s+[A-Za-z]+)?),?\s+([A-Z]{2})\s+(\d{5}(?:[-]?\d{4})?)', line)
        if m:
            result["city"] = m.group(1).strip()
            result["state"] = m.group(2)
            result["zip"] = m.group(3)
            csz_line_idx = i
            break
    if csz_line_idx > 0:
        # Street is everything between name and city/state/zip line
        street_lines = lines[1:csz_line_idx]
        result["street"] = ' '.join(street_lines)
    else:
        result["street"] = ' '.join(lines[1:])
    return result


def parse_earnings_email(body: str, full_msg: dict) -> Optional[dict]:
    """
    Parse a 'Sold, ship now' email and extract EVERY field.
    """
    oid_match = _re.search(r"Order ID:\s*(\d{3}-\d{7}-\d{7})", body, _re.IGNORECASE)
    if not oid_match:
        return None
    oid = oid_match.group(1)

    order_date = ""
    m = _re.search(r"Order date:\s*(\d{2}/\d{2}/\d{4})", body, _re.IGNORECASE)
    if m:
        order_date = m.group(1)

    ship_by = ""
    m = _re.search(r"Ship by:\s*(\d{2}/\d{2}/\d{4})", body, _re.IGNORECASE)
    if m:
        ship_by = m.group(1)

    shipping_method = ""
    m = _re.search(r"Please ship this order using:\s*(.+?)(?:\n|Ship by|Item:)", body, _re.IGNORECASE)
    if m:
        shipping_method = m.group(1).strip()

    # Find all item blocks - make Tax, Shipping, MFT all optional
    item_blocks = _re.findall(
        r"Item:\s*(.+?)Condition:\s*(.+?)SKU:\s*(\S+)\s*Quantity:\s*(\d+)\s*Price:\s*[^\d]*?([\d,]+\.\d{2})(?:\s*Tax:\s*[^\d]*?([\d,]+\.\d{2}))?(?:\s*Shipping:\s*[^\d]*?([\d,]+\.\d{2}))?\s*Amazon fees:\s*[^\d]*?([\d,]+\.\d{2})(?:\s*Marketplace Facilitator Tax:\s*[^\d]*?([\d,]+\.\d{2}))?\s*Your earnings:\s*[^\d]*?([\d,]+\.\d{2})",
        body, _re.DOTALL | _re.IGNORECASE
    )
    
    if not item_blocks:
        # Fallback: looser pattern without tax/fees/mft
        item_blocks = _re.findall(
            r"Item:\s*(.+?)Condition:\s*(.+?)SKU:\s*(\S+)\s*Quantity:\s*(\d+)\s*Price:\s*[^\d]*?([\d,]+\.\d{2})",
            body, _re.DOTALL | _re.IGNORECASE
        )

    product_names = []
    skus = []
    asins = []
    quantities = []
    item_prices = []
    conditions = []
    fees_per_item = []
    earnings_per_item = []
    tax_per_item = []
    shipping_per_item = []
    mft_per_item = []

    for block in item_blocks:
        if len(block) >= 10:
            # Full pattern with optional tax, shipping, mft
            product_name, condition, sku, qty, price = block[0], block[1], block[2], block[3], block[4]
            tax = block[5] if block[5] else "0.00"
            shipping = block[6] if block[6] else "0.00"
            fees = block[7] if block[7] else "0.00"
            mft = block[8] if block[8] else "0.00"
            earnings = block[9] if block[9] else "0.00"
        elif len(block) >= 5:
            product_name, condition, sku, qty, price = block[0], block[1], block[2], block[3], block[4]
            tax = shipping = fees = mft = earnings = "0.00"
        else:
            continue
        product_names.append(product_name.strip().replace('\n', ' '))
        conditions.append(condition.strip())
        skus.append(sku.strip())
        quantities.append(int(qty))
        item_prices.append(_clean_currency(price))
        tax_per_item.append(_clean_currency(tax))
        shipping_per_item.append(_clean_currency(shipping))
        fees_per_item.append(_clean_currency(fees))
        mft_per_item.append(_clean_currency(mft))
        earnings_per_item.append(_clean_currency(earnings))
        # Extract ASIN if present in product name
        asin_match = _re.search(r'\[ASIN:\s*([A-Z0-9]+)\]', product_name)
        asins.append(asin_match.group(1) if asin_match else "")

    # Totals
    total_tax = 0.0
    m = _re.search(r"Tax:\s*[^\d]*?([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if m:
        total_tax = _clean_currency(m.group(1))
    
    total_shipping = 0.0
    m = _re.search(r"Shipping:\s*[^\d]*?([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if m:
        total_shipping = _clean_currency(m.group(1))
    
    mft = 0.0
    m = _re.search(r"Marketplace Facilitator Tax:\s*[^\d]*?([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if m:
        mft = _clean_currency(m.group(1))
    
    total_fees = 0.0
    m = _re.search(r"Amazon fees:\s*[^\d]*?([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if m:
        total_fees = _clean_currency(m.group(1))
    
    total_earnings = 0.0
    m = _re.search(r"Your earnings:\s*[^\d]*?([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if m:
        total_earnings = _clean_currency(m.group(1))

    # Multi-item emails have one "Your earnings:"/"Amazon fees:"/... line PER
    # ITEM and no order-level total, so the first-match regexes above captured
    # only ITEM 1's value. Prefer the per-item sums whenever the per-item parse
    # actually captured financials (the loose fallback parse leaves them 0.0,
    # in which case the body-wide first match stays as the best available).
    if len(item_blocks) > 1:
        if sum(earnings_per_item) > 0:
            total_earnings = round(sum(earnings_per_item), 2)
        if sum(fees_per_item) > 0:
            total_fees = round(sum(fees_per_item), 2)
        if sum(tax_per_item) > 0:
            total_tax = round(sum(tax_per_item), 2)
        if sum(shipping_per_item) > 0:
            total_shipping = round(sum(shipping_per_item), 2)
        if sum(mft_per_item) > 0:
            mft = round(sum(mft_per_item), 2)

    subtotal = sum(item_prices) if item_prices else 0.0

    ts = _message_ts(full_msg)

    return {
        "amazon_order_id": oid,
        "email_timestamp": ts,
        "order_date": order_date,
        "ship_by_date": ship_by,
        "shipping_method": shipping_method,
        "num_items": len(product_names),
        "product_names": json.dumps(product_names),
        "skus": json.dumps(skus),
        "asins": json.dumps(asins),
        "quantities": json.dumps(quantities),
        "item_prices": json.dumps(item_prices),
        "conditions": json.dumps(conditions),
        "amazon_fees_per_item": json.dumps(fees_per_item),
        "earnings_per_item": json.dumps(earnings_per_item),
        "tax_per_item": json.dumps(tax_per_item),
        "shipping_per_item": json.dumps(shipping_per_item),
        "mft_per_item": json.dumps(mft_per_item),
        "total_tax": total_tax,
        "total_shipping": total_shipping,
        "marketplace_facilitator_tax": mft,
        "total_amazon_fees": total_fees,
        "total_your_earnings": total_earnings,
        "order_subtotal": subtotal,
        "raw_body_snippet": body[:2000],
    }


def parse_zoro_email(body: str, full_msg: dict, account_name: str = "") -> Optional[dict]:
    """
    Parse a Zoro 'Has Been Received' email and extract EVERY field.
    """
    wb_match = _re.search(r"Order Number:\s*(WB\d+)", body, _re.IGNORECASE)
    if not wb_match:
        wb_match = _re.search(r"#(WB\d+)", body)
    if not wb_match:
        return None
    wb = wb_match.group(1)

    order_date = ""
    m = _re.search(r"Order Date:\s*(\d{1,2}/\d{1,2}/\d{4})", body, _re.IGNORECASE)
    if m:
        order_date = m.group(1)

    po_number = ""
    m = _re.search(r"PO Number:\s*(.+?)(?:\n|Shipping Address)", body, _re.IGNORECASE)
    if m:
        po_number = m.group(1).strip()

    # Shipping Address
    ship_name = ship_street = ship_city = ship_state = ship_zip = ""
    sam = _re.search(r"Shipping Address:\s*(.+?)(?:Billing Address|Payment Method)", body, _re.DOTALL | _re.IGNORECASE)
    if sam:
        ship_text = sam.group(1).strip()
        addr = _extract_address_lines(ship_text)
        ship_name = addr["name"]
        ship_street = addr["street"]
        ship_city = addr["city"]
        ship_state = addr["state"]
        ship_zip = addr["zip"]

    # Billing Address
    bill_name = bill_company = bill_street = bill_city = bill_state = bill_zip = ""
    bam = _re.search(r"Billing Address:\s*(.+?)(?:Payment Method|Amount)", body, _re.DOTALL | _re.IGNORECASE)
    if bam:
        bill_text = bam.group(1).strip()
        addr = _extract_address_lines(bill_text)
        bill_name = addr["name"]
        bill_company = addr["name"]  # First line often company
        bill_street = addr["street"]
        bill_city = addr["city"]
        bill_state = addr["state"]
        bill_zip = addr["zip"]

    # Payment
    payment_method = ""
    card_last4 = ""
    pm = _re.search(r"Payment Method:\s*(.+?)(?:Your Order|Item\s+Unit|Subtotal)", body, _re.DOTALL | _re.IGNORECASE)
    if pm:
        pay_text = pm.group(1).strip()
        # Extract card last 4
        c4m = _re.search(r"(\d{4})", pay_text)
        if c4m:
            card_last4 = c4m.group(1)
        # Payment method name
        lines = [l.strip() for l in pay_text.split('\n') if l.strip()]
        for line in lines:
            if 'ending' in line.lower() or 'card' in line.lower() or 'master' in line.lower() or 'visa' in line.lower() or 'amex' in line.lower():
                payment_method = line
                break
        if not payment_method and lines:
            payment_method = lines[0]

    # Item details
    product_name = ""
    sku = ""
    qty = 0
    unit_price = 0.0
    line_total = 0.0

    # Look for Zoro # pattern
    zm = _re.search(r"Zoro #:\s*(\S+)\s*Qty:\s*(\d+)\s*Unit Price:\s*\$?([\d,]+\.\d{2})\s*\$?([\d,]+\.\d{2})\s*(\d+)\s*\$?([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if zm:
        sku = zm.group(1)
        qty = int(zm.group(2))
        unit_price = _clean_currency(zm.group(3))
        line_total = _clean_currency(zm.group(6))
        # Product name is before Zoro #
        pnm = _re.search(r"Your Order\s*Item\s*Unit Price\s*Qty\s*Total Price\s*(.+?)\s*Zoro #:\s*" + _re.escape(sku), body, _re.DOTALL | _re.IGNORECASE)
        if pnm:
            product_name = pnm.group(1).strip().replace('\n', ' ')
    else:
        # Fallback patterns
        zm2 = _re.search(r"Zoro\s*#\s*[:#]?\s*(\S+)", body, _re.IGNORECASE)
        if zm2:
            sku = zm2.group(1)
        qm = _re.search(r"Qty\s*[:#]?\s*(\d+)", body, _re.IGNORECASE)
        if qm:
            qty = int(qm.group(1))

    # Financials
    subtotal = 0.0
    m = _re.search(r"Subtotal\s*\$?([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if m:
        subtotal = _clean_currency(m.group(1))

    tax = 0.0
    m = _re.search(r"Tax\s*\$?([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if m:
        tax = _clean_currency(m.group(1))

    shipping = 0.0
    m = _re.search(r"Shipping\s*\$?([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if m:
        shipping = _clean_currency(m.group(1))

    order_total = 0.0
    m = _re.search(r"Order Total\s*\$?([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if m:
        order_total = _clean_currency(m.group(1))

    coupon = 0.0
    m = _re.search(r"[Cc]oupon\s*[-\$]?([\d,]+\.\d{2})", body)
    if m:
        coupon = _clean_currency(m.group(1))

    ts = _message_ts(full_msg)

    return {
        "wb_order_id": wb,
        "zoro_account_name": account_name,
        "email_timestamp": ts,
        "order_date": order_date,
        "po_number": po_number,
        "order_status": "Active",
        "product_name": product_name,
        "sku": sku,
        "qty": qty,
        "unit_price": unit_price,
        "line_total": line_total,
        "subtotal": subtotal,
        "tax": tax,
        "shipping_cost": shipping,
        "order_total": order_total,
        "coupon_savings": coupon,
        "ship_name": ship_name,
        "ship_street": ship_street,
        "ship_city": ship_city,
        "ship_state": ship_state,
        "ship_zip": ship_zip,
        "bill_company": bill_company,
        "bill_name": bill_name,
        "bill_street": bill_street,
        "bill_city": bill_city,
        "bill_state": bill_state,
        "bill_zip": bill_zip,
        "payment_method": payment_method,
        "payment_card_last4": card_last4,
        "raw_body_snippet": body[:2000],
    }

def parse_tracking_email(body: str, full_msg: dict, account_name: str = "") -> Optional[dict]:
    """
    Parse a Zoro 'has shipped' email and extract ALL tracking + shipment details.
    """
    wb_match = _re.search(r"Order Number:\s*(WB\d+)", body, _re.IGNORECASE)
    if not wb_match:
        wb_match = _re.search(r"#(WB\d+)", body)
    if not wb_match:
        return None
    wb = wb_match.group(1)

    order_date = ""
    m = _re.search(r"Order Date:\s*(\d{1,2}/\d{1,2}/\d{4})", body, _re.IGNORECASE)
    if m:
        order_date = m.group(1)

    # Ship date
    ship_date = ""
    m = _re.search(r"Shipped\s*(\w{3},?\s*\w{3}\s*\d{1,2}|\d{1,2}/\d{1,2}/\d{4})", body, _re.IGNORECASE)
    if m:
        ship_date = m.group(1)

    # Delivery method / carrier
    delivery_method = ""
    m = _re.search(r"Delivery Method:\s*(.+?)(?:\n|Tracking|Shipping Address)", body, _re.IGNORECASE)
    if m:
        delivery_method = m.group(1).strip()

    carrier = ""
    if "fed ex" in delivery_method.lower() or "fedex" in delivery_method.lower():
        carrier = "FedEx"
    elif "ups" in delivery_method.lower():
        carrier = "UPS"
    elif "usps" in delivery_method.lower():
        carrier = "USPS"

    # Tracking numbers. Zoro lists MULTI-BOX shipments one number PER LINE:
    #     Tracking Numbers:
    #     537016724393
    #     537016727186
    # The old pattern terminated on "\n", so it captured only the first line and
    # a 2-box order uploaded a single tracking id to Amazon (found on
    # 113-0036200-6022655, 2026-08-19). DOTALL + a bounded window reads the whole
    # block; the section headers below bound it so no unrelated digits creep in.
    def _dedupe(seq):
        out = []
        for x in seq:
            if x not in out:
                out.append(x)
        return out

    tracking_ids = ""
    tm = _re.search(
        r"Tracking Numbers?:\s*(.{0,400}?)"
        r"(?:Items in this|Need to Return|Delivery Method|Recommended Products|Order Details|\Z)",
        body, _re.IGNORECASE | _re.DOTALL)
    if tm:
        tids = _dedupe(_re.findall(r"\b(\d{12,22})\b", tm.group(1)))
        tracking_ids = ",".join(tids)
    if not tracking_ids:
        tids = _dedupe(_re.findall(r"\b(\d{12,22})\b", body))
        if tids:
            tracking_ids = ",".join(tids)

    # Shipping address
    ship_to_name = ship_to_street = ship_to_city = ship_to_state = ship_to_zip = ""
    sam = _re.search(r"Shipping Address:\s*(.+?)(?:Delivery Method|Tracking Numbers|Order Details|Items in this)", body, _re.DOTALL | _re.IGNORECASE)
    if sam:
        ship_text = sam.group(1).strip()
        addr = _extract_address_lines(ship_text)
        ship_to_name = addr["name"]
        ship_to_street = addr["street"]
        ship_to_city = addr["city"]
        ship_to_state = addr["state"]
        ship_to_zip = addr["zip"]

    # Items in shipment
    item_names = []
    item_skus = []
    item_quantities = []

    # Find "Items in this Shipment" section
    items_section = _re.search(r"Items in this Shipment\s*(.+?)(?:Need to Return|Recommended Products|Follow Us)", body, _re.DOTALL | _re.IGNORECASE)
    if items_section:
        section = items_section.group(1)
        # Find each item block
        item_blocks = _re.findall(r"(.+?)\s*Zoro\s*#:\s*(\S+)\s*Qty:\s*(\d+)\s*\d+", section, _re.IGNORECASE)
        for block in item_blocks:
            item_names.append(block[0].strip().replace('\n', ' '))
            item_skus.append(block[1])
            item_quantities.append(int(block[2]))

    ts = _message_ts(full_msg)

    return {
        "wb_order_id": wb,
        "tracking_ids": tracking_ids,
        "carrier": carrier,
        "ship_date": ship_date,
        "delivery_method": delivery_method,
        "ship_to_name": ship_to_name,
        "ship_to_street": ship_to_street,
        "ship_to_city": ship_to_city,
        "ship_to_state": ship_to_state,
        "ship_to_zip": ship_to_zip,
        "num_items": len(item_names),
        "item_skus": json.dumps(item_skus),
        "item_names": json.dumps(item_names),
        "item_quantities": json.dumps(item_quantities),
        "email_timestamp": ts,
        "raw_body_snippet": body[:2000],
    }


def parse_message_email(body: str, full_msg: dict, subject: str, account_name: str) -> Optional[dict]:
    """
    Parse ANY buyer↔seller communication email and classify it.
    """
    # Detect order ID
    oid = ""
    om = _re.search(r"Order\s*#?\s*(\d{3}-\d{7}-\d{7})", body)
    if not om:
        om = _re.search(r"order\s*(\d{3}-\d{7}-\d{7})", body, _re.IGNORECASE)
    if not om:
        om = _re.search(r"Order ID:\s*(\d{3}-\d{7}-\d{7})", body, _re.IGNORECASE)
    if om:
        oid = om.group(1)

    # Detect customer name
    customer_name = ""
    if "Your e-mail to" in subject:
        cm = _re.search(r"Your e-mail to\s+(.+)", subject)
        if cm:
            customer_name = cm.group(1).strip()
    elif "Inquiry from Amazon customer" in subject:
        cm = _re.search(r"Inquiry from Amazon customer\s+(.+?)\s*\(", subject)
        if cm:
            customer_name = cm.group(1).strip()

    # Detect direction
    direction = "outbound"
    if "Your e-mail to" in subject:
        direction = "outbound"
    elif "Inquiry from" in subject or "Unexpected problem" in subject:
        direction = "inbound"

    # Extract actual message body content
    message_body = ""
    # Pattern 1: Begin message / End message (outbound confirmation copies)
    mb = _re.search(r"-+\s*Begin message\s*-+\s*(.+?)-+\s*End message\s*-+", body, _re.DOTALL | _re.IGNORECASE)
    if mb:
        msg_text = mb.group(1).strip()
        # Remove the "Message from seller [Store]:" prefix
        msg_text = _re.sub(r"Message from seller\s+\w+:\s*", "", msg_text, flags=_re.IGNORECASE)
        msg_text = _re.sub(r"Hi\s+\w+[:,]\s*", "", msg_text, flags=_re.IGNORECASE)
        msg_text = _re.sub(r"Seller\s+\w+\s+has a message for you\.\s*", "", msg_text, flags=_re.IGNORECASE)
        msg_text = _re.sub(r"-+\s*", "\n", msg_text)
        message_body = msg_text.strip()
    else:
        # Pattern 2: For buyer messages (inbound)
        mb2 = _re.search(r"-+\s*Message:\s*-+\s*(.+?)-+\s*End message\s*-+", body, _re.DOTALL | _re.IGNORECASE)
        if mb2:
            message_body = mb2.group(1).strip()
        else:
            # Pattern 3: Just use cleaned body
            clean = body
            clean = _re.sub(r'^\d+\s*', '', clean)
            clean = _re.sub(r'Dear\s+\w+?,', '', clean, flags=_re.IGNORECASE)
            clean = _re.sub(r'Here is a copy of the e-mail.*?Order ID:\s*\d{3}-\d{7}-\d{7}.*?Begin message', '', clean, flags=_re.IGNORECASE | _re.DOTALL)
            message_body = clean[:1500].strip()

    # Detect message type by content analysis
    message_type = "other_outbound"
    lower_body = body.lower()
    lower_subject = subject.lower()
    msg_lower = message_body.lower() if message_body else lower_body

    # Inbound classification
    if direction == "inbound":
        if "inquiry from amazon customer" in lower_subject:
            message_type = "buyer_inquiry"
        elif "unexpected problem" in lower_subject:
            message_type = "buyer_reply"
        elif "cancel" in lower_body:
            message_type = "buyer_cancellation"
        else:
            message_type = "other_inbound"
    else:
        # Outbound classification (check more specific types first)
        if "cancellation request" in msg_lower or "unable to cancel" in msg_lower:
            message_type = "cancellation_ack"
        elif "delivered" in msg_lower or "arrived" in msg_lower or "delivery" in msg_lower:
            message_type = "delivery_checkin"
        elif "out of stock" in msg_lower or "temporarily out of stock" in msg_lower:
            message_type = "oos_notice"
        elif "package lost" in msg_lower or "shipment lost" in msg_lower or "missing package" in msg_lower or "never arrived" in msg_lower:
            message_type = "loss_notice"
        elif "shipping restriction" in msg_lower:
            message_type = "sr_notice"
        elif "unable to fulfill" in msg_lower or "cannot fulfill" in msg_lower:
            message_type = "unable_to_fulfill"
        elif ("tracking" in msg_lower and ("fedex" in msg_lower or "shipped" in msg_lower or "ship" in msg_lower)) or \
           "already been shipped" in msg_lower or \
           "tracking number" in msg_lower or \
           "tracking id" in msg_lower:
            message_type = "tracking_sent"
        else:
            message_type = "other_outbound"

    # Detect attachments from Gmail payload
    has_attachment = 0
    attachment_types = []
    attachment_count = 0
    payload = full_msg.get("payload", {})
    parts = payload.get("parts", [])
    for part in parts:
        filename = part.get("filename", "")
        if filename:
            attachment_count += 1
            has_attachment = 1
            if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')):
                attachment_types.append("image")
            elif filename.lower().endswith('.pdf'):
                attachment_types.append("pdf")
            else:
                attachment_types.append("other")

    # Tracking IDs mentioned
    tracking_ids_mentioned = ",".join(list(set(_re.findall(r"\b(\d{12,22})\b", body))))

    # Boolean flags
    is_oos = 1 if "out of stock" in body.lower() or "temporarily out of stock" in body.lower() else 0
    is_loss = 1 if ("package lost" in body.lower() or "shipment lost" in body.lower() or
                    "missing package" in body.lower() or "never arrived" in body.lower()) else 0
    is_sr = 1 if "shipping restriction" in body.lower() or " sr " in body.lower() else 0
    is_unable_to_fulfill = 1 if "unable to fulfill" in body.lower() or "cannot fulfill" in body.lower() else 0

    # New flags per user requirements
    sent_tracking_details = 1 if message_type == "tracking_sent" else 0
    communicated_with_customer = 1  # All message emails = communication
    customer_wrote = 1 if direction == "inbound" else 0
    has_pictures = 1 if has_attachment and "image" in attachment_types else 0
    other_message = 1 if message_type == "other_outbound" else 0

    ts = _message_ts(full_msg)
    msg_id = full_msg.get("id", "")

    return {
        "amazon_order_id": oid,
        "message_type": message_type,
        "customer_name": customer_name,
        "subject": subject,
        "direction": direction,
        "has_attachment": has_attachment,
        "attachment_types": json.dumps(attachment_types),
        "attachment_count": attachment_count,
        "message_body": message_body,
        "tracking_ids_mentioned": tracking_ids_mentioned,
        "is_oos": is_oos,
        "is_loss": is_loss,
        "is_sr": is_sr,
        "is_unable_to_fulfill": is_unable_to_fulfill,
        "sent_tracking_details": sent_tracking_details,
        "communicated_with_customer": communicated_with_customer,
        "customer_wrote": customer_wrote,
        "has_pictures": has_pictures,
        "other_message": other_message,
        "gmail_account": account_name,
        "email_timestamp": ts,
        "message_id": msg_id,
        "raw_body_snippet": body[:2000],
    }

def parse_return_email(body: str, full_msg: dict, account_name: str) -> Optional[dict]:
    """
    Parse Return Authorization OR Refund Initiated emails.
    Handles both email types with comprehensive extraction.
    """
    oid = ""
    om = _re.search(r"Order\s*#?\s*(\d{3}-\d{7}-\d{7})", body)
    if om:
        oid = om.group(1)
    if not oid:
        return None

    is_return_auth = "Return authorization" in body or "return authorization" in body.lower()
    is_refund = "Refund Initiated" in body or "refund initiated" in body.lower()

    if not is_return_auth and not is_refund:
        return None

    email_type = "return_auth" if is_return_auth else "refund_initiated"
    return_requested = "Yes" if is_return_auth else "No"
    refund_done = "Yes" if is_refund else "No"

    # Return Request Date
    return_request_date = ""
    rdm = _re.search(r"Return Request Date:\s*(\d{4}-\d{2}-\d{2})", body, _re.IGNORECASE)
    if rdm:
        return_request_date = rdm.group(1)

    # Return Policy Check
    return_policy_check = ""
    pcm = _re.search(r"Return Policy Check:\s*(.+?)(?:\n|Authorization)", body, _re.IGNORECASE)
    if pcm:
        return_policy_check = pcm.group(1).strip()

    # Authorization type
    authorization_type = ""
    am = _re.search(r"Authorization:\s*(.+?)(?:\n|Order Item)", body, _re.IGNORECASE)
    if am:
        authorization_type = am.group(1).strip()

    # ASIN
    asin = ""
    # Look for ASIN in the item details table
    asm = _re.search(r"(?:ASIN|B\w{9,10})\s+(B\w{9,10})", body, _re.IGNORECASE)
    if asm:
        asin = asm.group(1)
    else:
        asm2 = _re.search(r"\b(B[A-Z0-9]{9,10})\b", body)
        if asm2:
            asin = asm2.group(1)

    # SKU
    sku = ""
    # Pattern in return/refund table: ASIN SKU Qty Reason...
    sm = _re.search(r"(?:ASIN|B\w{9,10})\s+\S+\s+([A-Za-z]+-[A-Za-z0-9_-]+)\s+\d+", body, _re.IGNORECASE)
    if sm:
        sku = sm.group(1)
    if not sku or sku in ["In-Policy", "Auto", "Customer", "Order"]:
        # Try stricter pattern: after BXXXXXXXXXX line
        sm2 = _re.search(r"B[A-Z0-9]{9,10}\s+([A-Za-z]+-[A-Za-z0-9_-]+)\s+\d+\s+(?:No longer needed|Inaccurate|Defective|Damaged|Different|Missing|Arrived|Expired|Unwanted|Ordered by mistake|Not compatible|Quality|Wrong|Product is not|Other|Order not received)", body, _re.IGNORECASE)
        if sm2:
            sku = sm2.group(1)
    if not sku or sku in ["In-Policy", "Auto", "Customer", "Order"]:
        # Try finding any SKU-like pattern
        sm3 = _re.search(r"\b([a-z]+-[A-Za-z0-9_-]+)\b", body, _re.IGNORECASE)
        if sm3:
            sku = sm3.group(1)

    # Return Quantity - handle vertical table where labels and values are in separate blocks
    return_qty = 0
    # Find ASIN line, then qty is 2 lines after SKU line
    lines = [l.strip() for l in body.split('\n')]
    for i, line in enumerate(lines):
        if _re.match(r'^B[A-Z0-9]{9,10}$', line):
            # ASIN found at line i, SKU at i+1, Qty at i+2
            if i + 2 < len(lines):
                candidate = lines[i + 2].strip()
                if candidate.isdigit():
                    return_qty = int(candidate)
                    break
            # For refund emails: ASIN, SKU, qty, item_price, shipping, reason
            # Try i+3 if i+2 is a price
            if i + 3 < len(lines):
                candidate = lines[i + 3].strip()
                if candidate.isdigit():
                    return_qty = int(candidate)
                    break

    # Return Reason
    return_reason = ""
    reasons = [
        "No longer needed", "Inaccurate website description", "Defective", "Damaged",
        "Different from what was ordered", "Missing parts or accessories", "Arrived too late",
        "Expired item", "Unwanted item", "Ordered by mistake", "Not compatible",
        "Quality not adequate", "Wrong item was sent", "Item does not match description",
        "Product is not as described on the website", "Other", "Order not received"
    ]
    for reason in reasons:
        if reason.lower() in body.lower():
            return_reason = reason
            break

    # Customer Comment
    customer_comment = ""
    ccm = _re.search(r"Customer.s Comment\s+(.+?)(?:Using your|Thank you|Please note|Regards)", body, _re.DOTALL | _re.IGNORECASE)
    if ccm:
        raw = _re.sub(r'\s+', ' ', ccm.group(1)).strip()
        # Clean up: if raw is very long, it probably includes product name
        if len(raw) > 100:
            # Try to find just the comment after the reason
            if return_reason:
                idx = raw.lower().find(return_reason.lower())
                if idx >= 0:
                    after = raw[idx + len(return_reason):].strip()
                    after = after.split("|")[0].strip()
                    if after and len(after) < 300:
                        customer_comment = after
            if not customer_comment:
                # Fallback: take last part after pipe or last 100 chars
                parts = raw.split("|")
                if len(parts) > 1:
                    customer_comment = parts[-1].strip()
                else:
                    customer_comment = raw[-200:].strip()
        else:
            customer_comment = raw

    # Refund amount
    refund_amount = 0.0
    ram = _re.search(r"refund of\s+USD\s+([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if not ram:
        ram = _re.search(r"refund of\s*\$?\s*([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if ram:
        refund_amount = _clean_currency(ram.group(1))

    # Refund reason
    refund_reason = ""
    # Vertical table: label on one line, reason on next line(s)
    lines = [l.strip() for l in body.split('\n')]
    for i, line in enumerate(lines):
        if line.lower() == "refund reason":
            if i + 1 < len(lines):
                candidate = lines[i+1].strip()
                # Make sure it's not the product name (too long)
                if candidate and len(candidate) < 60:
                    refund_reason = candidate
                    break
    if not refund_reason:
        rrm = _re.search(r"Refund Reason\s+([A-Za-z\s]+?)(?:\n|$)", body, _re.IGNORECASE)
        if rrm:
            candidate = rrm.group(1).strip()
            if candidate and len(candidate) < 60:
                refund_reason = candidate
    if not refund_reason and is_refund:
        for reason in reasons:
            if reason.lower() in body.lower():
                refund_reason = reason
                break

    # Customer name (from refund email)
    customer_name = ""
    cnm = _re.search(r"refund of\s+(?:USD\s+)?\$?[\d,]+\.\d{2}\s+to\s+(.+?)\s+for", body, _re.IGNORECASE)
    if cnm:
        customer_name = cnm.group(1).strip()

    # Item price refund & outbound shipping refund - handle vertical tables
    item_price_refund = 0.0
    osr = 0.0
    item_price = 0.0
    lines = [l.strip() for l in body.split('\n')]
    # Find ASIN line, then extract values from the value block
    for i, line in enumerate(lines):
        if _re.match(r'^B[A-Z0-9]{9,10}$', line):
            # Value block starts after all labels
            # Look for numeric values after SKU line
            value_candidates = []
            for j in range(i + 1, min(i + 10, len(lines))):
                candidate = lines[j]
                if _re.match(r'^\d+(\.\d{2})?$', candidate):
                    value_candidates.append(candidate)
                elif candidate.lower() in ["order not received", "no longer needed", "inaccurate website description"]:
                    break
            # For refund: values are [qty, item_price_refund, outbound_shipping_refund]
            if len(value_candidates) >= 3:
                return_qty = int(value_candidates[0].split('.')[0])
                item_price_refund = _clean_currency(value_candidates[1])
                osr = _clean_currency(value_candidates[2])
            elif len(value_candidates) >= 2:
                return_qty = int(value_candidates[0].split('.')[0])
                item_price_refund = _clean_currency(value_candidates[1])
            elif len(value_candidates) >= 1:
                return_qty = int(value_candidates[0].split('.')[0])
            break

    # Fallback regex for item price refund
    if not item_price_refund:
        ipm = _re.search(r"Item Price Refund\s+([\d,]+\.\d{2})", body, _re.IGNORECASE)
        if ipm:
            item_price_refund = _clean_currency(ipm.group(1))

    # Item price (from refund table)
    item_price = 0.0
    ip = _re.search(r"Item Price\s+([\d,]+\.\d{2})", body, _re.IGNORECASE)
    if ip:
        item_price = _clean_currency(ip.group(1))
    if ip:
        item_price = _clean_currency(ip.group(1))

    # Fulfillment network
    fulfillment_network = ""
    fnm = _re.search(r"Fulfillment Network:\s*(.+?)(?:\n|Refund)", body, _re.IGNORECASE)
    if fnm:
        fulfillment_network = fnm.group(1).strip()

    # Refund resolution
    refund_resolution = ""
    rrm2 = _re.search(r"Refund Resolution/Issuer:\s*(.+?)(?:\n|Order Item)", body, _re.IGNORECASE)
    if rrm2:
        refund_resolution = rrm2.group(1).strip()

    ts = _message_ts(full_msg)

    return {
        "amazon_order_id": oid,
        "email_type": email_type,
        "return_requested": return_requested,
        "refund_done": refund_done,
        "return_request_date": return_request_date,
        "return_policy_check": return_policy_check,
        "authorization_type": authorization_type,
        "asin": asin,
        "sku": sku,
        "return_qty": return_qty,
        "return_reason": return_reason,
        "customer_comment": customer_comment,
        "refund_amount": refund_amount,
        "refund_reason": refund_reason,
        "customer_name": customer_name,
        "item_price": item_price,
        "item_price_refund": item_price_refund,
        "outbound_shipping_refund": osr,
        "fulfillment_network": fulfillment_network,
        "refund_resolution": refund_resolution,
        "email_timestamp": ts,
        "raw_body_snippet": body[:2000],
    }


def sync_earnings(service, db_path: str, account_name: str, days_back: int = 1, max_total: int = 50) -> int:
    """Fetch earnings emails and write to archive with FULL detail."""
    from utils import get_email_body_html, clean_html, search_gmail, batch_get_messages

    logger.info("[Build]   Earnings account: %s (max %d)", account_name, max_total)
    query = f'subject:"Sold, ship now:" newer_than:{days_back}d'
    msgs = search_gmail(service, query, max_results=max_total if max_total and max_total > 0 else None)
    if not msgs:
        return 0

    conn = sqlite3.connect(db_path)
    msgs = _filter_unseen(conn, account_name, msgs, f"[{account_name} earnings] ")
    msg_ids = [m["id"] for m in msgs]
    fulls = batch_get_messages(service, msg_ids, progress_label=f"[{account_name} earnings] ") if msg_ids else []
    _mark_seen(conn, account_name, fulls)

    count = 0
    for full in fulls:
        body = _html_to_text_preserve_lines(get_email_body_html(full.get("payload", {})))
        if not body:
            continue
        data = parse_earnings_email(body, full)
        if not data:
            continue

        conn.execute(
            """
            INSERT OR REPLACE INTO earnings_emails
            (amazon_order_id, gmail_account, email_timestamp, order_date, ship_by_date,
             shipping_method, num_items, product_names, skus, asins, quantities,
             item_prices, conditions, amazon_fees_per_item, earnings_per_item,
             tax_per_item, shipping_per_item, mft_per_item,
             total_tax, total_shipping, marketplace_facilitator_tax,
             total_amazon_fees, total_your_earnings, order_subtotal, raw_body_snippet, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["amazon_order_id"], account_name, data["email_timestamp"],
                data["order_date"], data["ship_by_date"], data["shipping_method"],
                data["num_items"], data["product_names"], data["skus"], data["asins"],
                data["quantities"], data["item_prices"], data["conditions"],
                data["amazon_fees_per_item"], data["earnings_per_item"],
                data["tax_per_item"], data["shipping_per_item"], data["mft_per_item"],
                data["total_tax"], data["total_shipping"], data["marketplace_facilitator_tax"],
                data["total_amazon_fees"], data["total_your_earnings"],
                data["order_subtotal"], data["raw_body_snippet"], datetime.now().isoformat(),
            ),
        )
        count += 1

    _update_sync_state(conn, "earnings", count)
    conn.commit()
    conn.close()
    logger.info("[Archive] Synced %d earnings emails", count)
    return count


def sync_zoro(zoro_services: Dict[str, object], db_path: str, days_back: int = 1, max_total: int = 50) -> int:
    """Fetch Zoro emails and write to archive with FULL detail."""
    from utils import get_email_body_html, clean_html, search_gmail, batch_get_messages

    logger.info("[Build]   Zoro emails (max %d)", max_total)
    total_count = 0
    conn = sqlite3.connect(db_path)

    for acct_name, svc in zoro_services.items():
        query = f'from:(zoro@e.zoro.com) subject:("has been received") newer_than:{days_back}d'
        msgs = search_gmail(svc, query, max_results=max_total if max_total and max_total > 0 else None)
        msgs = _filter_unseen(conn, acct_name, msgs, f"[{acct_name} zoro] ")
        if not msgs:
            continue

        msg_ids = [m["id"] for m in msgs]
        fulls = batch_get_messages(svc, msg_ids, progress_label=f"[{acct_name} zoro] ")
        _mark_seen(conn, acct_name, fulls)

        for full in fulls:
            body = _html_to_text_preserve_lines(get_email_body_html(full.get("payload", {})))
            if not body:
                continue
            data = parse_zoro_email(body, full, acct_name)
            if not data:
                continue

            conn.execute(
                """
                INSERT OR REPLACE INTO zoro_emails
                (wb_order_id, gmail_account, zoro_account_name, email_timestamp, order_date,
                 po_number, order_status, product_name, sku, qty, unit_price, line_total,
                 subtotal, tax, shipping_cost, order_total, coupon_savings,
                 ship_name, ship_street, ship_city, ship_state, ship_zip,
                 bill_company, bill_name, bill_street, bill_city, bill_state, bill_zip,
                 payment_method, payment_card_last4, raw_body_snippet, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["wb_order_id"], acct_name, data["zoro_account_name"], data["email_timestamp"], data["order_date"],
                    data["po_number"], data["order_status"], data["product_name"], data["sku"], data["qty"],
                    data["unit_price"], data["line_total"], data["subtotal"], data["tax"], data["shipping_cost"],
                    data["order_total"], data["coupon_savings"],
                    data["ship_name"], data["ship_street"], data["ship_city"], data["ship_state"], data["ship_zip"],
                    data["bill_company"], data["bill_name"], data["bill_street"], data["bill_city"], data["bill_state"], data["bill_zip"],
                    data["payment_method"], data["payment_card_last4"], data["raw_body_snippet"], datetime.now().isoformat(),
                ),
            )
            total_count += 1

    _update_sync_state(conn, "zoro", total_count)
    conn.commit()
    conn.close()
    logger.info("[Archive] Synced %d zoro emails", total_count)
    return total_count


def sync_tracking(zoro_services: Dict[str, object], db_path: str, days_back: int = 1, max_total: int = 50) -> int:
    """Fetch Zoro shipped emails and extract tracking."""
    from utils import get_email_body_html, clean_html, search_gmail, batch_get_messages

    logger.info("[Build]   Tracking emails (max %d)", max_total)
    total_count = 0
    conn = sqlite3.connect(db_path)

    for acct_name, svc in zoro_services.items():
        query = f'from:(zoro@e.zoro.com) subject:("has shipped") newer_than:{days_back}d'
        msgs = search_gmail(svc, query, max_results=max_total if max_total and max_total > 0 else None)
        msgs = _filter_unseen(conn, acct_name, msgs, f"[{acct_name} tracking] ")
        if not msgs:
            continue

        msg_ids = [m["id"] for m in msgs]
        fulls = batch_get_messages(svc, msg_ids, progress_label=f"[{acct_name} tracking] ")
        _mark_seen(conn, acct_name, fulls)

        for full in fulls:
            body = _html_to_text_preserve_lines(get_email_body_html(full.get("payload", {})))
            if not body:
                continue
            data = parse_tracking_email(body, full, acct_name)
            if not data:
                continue

            conn.execute(
                """
                INSERT OR REPLACE INTO tracking_emails
                (wb_order_id, tracking_ids, carrier, ship_date, delivery_method,
                 ship_to_name, ship_to_street, ship_to_city, ship_to_state, ship_to_zip,
                 num_items, item_skus, item_names, item_quantities,
                 gmail_account, email_timestamp, raw_body_snippet, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["wb_order_id"], data["tracking_ids"], data["carrier"], data["ship_date"], data["delivery_method"],
                    data["ship_to_name"], data["ship_to_street"], data["ship_to_city"], data["ship_to_state"], data["ship_to_zip"],
                    data["num_items"], data["item_skus"], data["item_names"], data["item_quantities"],
                    acct_name, data["email_timestamp"], data["raw_body_snippet"], datetime.now().isoformat(),
                ),
            )
            total_count += 1

    _update_sync_state(conn, "tracking", total_count)
    conn.commit()
    conn.close()
    logger.info("[Archive] Synced %d tracking emails", total_count)
    return total_count


def sync_returns_refunds(services: Dict[str, object], db_path: str, days_back: int = 1, max_total: int = 50) -> int:
    """Fetch return authorization and refund emails."""
    from utils import get_email_body_html, clean_html, search_gmail, batch_get_messages

    logger.info("[Build]   Returns/Refunds (max %d)", max_total)
    total_count = 0
    conn = sqlite3.connect(db_path)

    for acct_name, svc in services.items():
        query = f'subject:("Return authorization notification" OR "Refund Initiated") newer_than:{days_back}d'
        msgs = search_gmail(svc, query, max_results=max_total if max_total and max_total > 0 else None)
        msgs = _filter_unseen(conn, acct_name, msgs, f"[{acct_name} returns] ")
        if not msgs:
            continue

        msg_ids = [m["id"] for m in msgs]
        fulls = batch_get_messages(svc, msg_ids, progress_label=f"[{acct_name} returns] ")
        _mark_seen(conn, acct_name, fulls)

        for full in fulls:
            body = _html_to_text_preserve_lines(get_email_body_html(full.get("payload", {})))
            if not body:
                continue
            data = parse_return_email(body, full, acct_name)
            if not data:
                continue

            conn.execute(
                """
                INSERT OR REPLACE INTO return_emails
                (amazon_order_id, email_type, return_requested, refund_done, return_request_date,
                 return_policy_check, authorization_type, asin, sku, return_qty, return_reason,
                 customer_comment, refund_amount, refund_reason, customer_name, item_price,
                 item_price_refund, outbound_shipping_refund, fulfillment_network, refund_resolution,
                 gmail_account, email_timestamp, raw_body_snippet, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["amazon_order_id"], data["email_type"], data["return_requested"], data["refund_done"],
                    data["return_request_date"], data["return_policy_check"], data["authorization_type"],
                    data["asin"], data["sku"], data["return_qty"], data["return_reason"], data["customer_comment"],
                    data["refund_amount"], data["refund_reason"], data["customer_name"], data["item_price"],
                    data["item_price_refund"], data["outbound_shipping_refund"], data["fulfillment_network"],
                    data["refund_resolution"], acct_name, data["email_timestamp"], data["raw_body_snippet"], datetime.now().isoformat(),
                ),
            )
            total_count += 1

    _update_sync_state(conn, "returns", total_count)
    conn.commit()
    conn.close()
    logger.info("[Archive] Synced %d return/refund emails", total_count)
    return total_count


def sync_messages(services: Dict[str, object], db_path: str, days_back: int = 1, max_total: int = 50) -> int:
    """Fetch ALL buyer↔seller communication emails."""
    from utils import get_email_body_html, clean_html, search_gmail, batch_get_messages

    logger.info("[Build]   Messages (max %d)", max_total)
    total_count = 0
    conn = sqlite3.connect(db_path)

    for acct_name, svc in services.items():
        queries = [
            f'subject:"Your e-mail to" newer_than:{days_back}d',
            f'subject:"Inquiry from Amazon customer" newer_than:{days_back}d',
            f'subject:"Unexpected problem with completing your order" newer_than:{days_back}d',
        ]

        for query in queries:
            msgs = search_gmail(svc, query, max_results=max_total if max_total and max_total > 0 else None)
            msgs = _filter_unseen(conn, acct_name, msgs, f"[{acct_name} msgs] ")
            if not msgs:
                continue

            msg_ids = [m["id"] for m in msgs]
            fulls = batch_get_messages(svc, msg_ids, progress_label=f"[{acct_name} msgs] ")
            _mark_seen(conn, acct_name, fulls)

            for full in fulls:
                payload = full.get("payload", {})
                body = clean_html(get_email_body_html(payload))
                if not body:
                    continue

                subject = ""
                headers = payload.get("headers", [])
                for h in headers:
                    if h.get("name", "").lower() == "subject":
                        subject = h.get("value", "")
                        break

                data = parse_message_email(body, full, subject, acct_name)
                if not data:
                    continue

                conn.execute(
                    """
                    INSERT OR REPLACE INTO message_emails
                    (amazon_order_id, message_type, customer_name, subject, direction, has_attachment,
                     attachment_types, attachment_count, message_body, tracking_ids_mentioned,
                     is_oos, is_loss, is_sr, is_unable_to_fulfill, sent_tracking_details,
                     communicated_with_customer, customer_wrote, has_pictures, other_message,
                     gmail_account, email_timestamp, message_id, raw_body_snippet, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data["amazon_order_id"], data["message_type"], data["customer_name"], data["subject"],
                        data["direction"], data["has_attachment"], data["attachment_types"], data["attachment_count"],
                        data["message_body"], data["tracking_ids_mentioned"], data["is_oos"], data["is_loss"],
                        data["is_sr"], data["is_unable_to_fulfill"], data["sent_tracking_details"],
                        data["communicated_with_customer"], data["customer_wrote"], data["has_pictures"],
                        data["other_message"], acct_name, data["email_timestamp"], data["message_id"],
                        data["raw_body_snippet"], datetime.now().isoformat(),
                    ),
                )
                total_count += 1

    _update_sync_state(conn, "messages", total_count)
    conn.commit()
    conn.close()
    logger.info("[Archive] Synced %d message emails", total_count)
    return total_count


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def lookup_earnings(db_path: str, amazon_order_id: str) -> Optional[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM earnings_emails WHERE amazon_order_id = ?", (amazon_order_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def lookup_zoro(db_path: str, wb_order_id: str) -> Optional[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM zoro_emails WHERE wb_order_id = ?", (wb_order_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def lookup_tracking(db_path: str, wb_order_id: str) -> List[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM tracking_emails WHERE wb_order_id = ? ORDER BY email_timestamp DESC", (wb_order_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def lookup_returns(db_path: str, amazon_order_id: str) -> List[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM return_emails WHERE amazon_order_id = ? ORDER BY email_timestamp DESC", (amazon_order_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def lookup_messages(db_path: str, amazon_order_id: str) -> List[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM message_emails WHERE amazon_order_id = ? ORDER BY email_timestamp DESC", (amazon_order_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── Unified-tracker compatibility wrappers ─────────────────────────────────

def get_earnings(db_path: str, amazon_order_id: str) -> Optional[dict]:
    """Return earnings data in the format unified_tracker expects."""
    row = lookup_earnings(db_path, amazon_order_id)
    if not row:
        return None
    ts = row.get("email_timestamp", "")
    return {
        "pkt_timestamp": ts,
        "sold_email_time": ts,
        "amazon_fees": round(row.get("total_amazon_fees", 0.0), 2),
        "your_earnings": round(row.get("total_your_earnings", 0.0), 2),
    }


def get_zoro(db_path: str, wb_order_id: str) -> Optional[dict]:
    """Return Zoro data in the format unified_tracker expects."""
    row = lookup_zoro(db_path, wb_order_id)
    if not row:
        return None
    return {
        "order_total": row.get("order_total", 0.0),
        "tax": row.get("tax", 0.0),
        "coupon_savings": row.get("coupon_savings", 0.0),
        "buyer_zip": row.get("ship_zip", ""),
        "account_name": row.get("zoro_account_name", ""),
        "order_status": row.get("order_status", ""),
        "sku": row.get("sku", ""),
        "qty": row.get("qty", 0),
        "order_date": row.get("order_date", ""),
        "email_ts": row.get("email_timestamp", ""),
        "payment_card": row.get("payment_card_last4", ""),
    }


def get_tracking(db_path: str, wb_order_id: str) -> Optional[dict]:
    """Return the most recent tracking record for a WB ID."""
    rows = lookup_tracking(db_path, wb_order_id)
    if not rows:
        return None
    first = rows[0]
    return {
        "tracking_ids": first.get("tracking_ids", ""),
        "shipped_email_time": first.get("email_timestamp", ""),
    }


def get_returns(db_path: str, amazon_order_id: str) -> Optional[dict]:
    """Return aggregated return/refund flags for an order."""
    rows = lookup_returns(db_path, amazon_order_id)
    if not rows:
        return None
    return_requested = "No"
    refund_done = "No"
    for r in rows:
        if r.get("return_requested", "").strip().lower() in ("yes", "true", "1"):
            return_requested = "Yes"
        if r.get("refund_done", "").strip().lower() in ("yes", "true", "1"):
            refund_done = "Yes"
    return {
        "return_requested": return_requested,
        "refund_done": refund_done,
    }


def has_delivery_confirmation(db_path: str, amazon_order_id: str) -> bool:
    """Check if a delivery check-in message exists in the archive."""
    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM message_emails WHERE amazon_order_id = ? AND message_type = 'delivery_checkin'",
        (amazon_order_id,),
    ).fetchone()[0]
    conn.close()
    return count > 0


def has_tracking_confirmation(db_path: str, amazon_order_id: str) -> bool:
    """Check if a tracking-sent message exists in the archive."""
    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM message_emails WHERE amazon_order_id = ? AND message_type = 'tracking_sent'",
        (amazon_order_id,),
    ).fetchone()[0]
    conn.close()
    return count > 0


def has_cancellation_ack(db_path: str, amazon_order_id: str) -> bool:
    """Check if a cancellation acknowledgment exists in the archive."""
    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM message_emails WHERE amazon_order_id = ? AND message_type = 'cancellation_ack'",
        (amazon_order_id,),
    ).fetchone()[0]
    conn.close()
    return count > 0


def has_buyer_cancellation_request(db_path: str, amazon_order_id: str) -> bool:
    """Check if a buyer cancellation request exists in the archive."""
    conn = sqlite3.connect(db_path)
    count = conn.execute(
        "SELECT COUNT(*) FROM message_emails WHERE amazon_order_id = ? AND message_type = 'buyer_cancellation'",
        (amazon_order_id,),
    ).fetchone()[0]
    conn.close()
    return count > 0
