-- ============================================================
-- CLEAN SCHEMA v2 -- Dropshipping Development Unit
-- Rebuilt: 2026-06-08
-- Focus: Scalable, organized, ready for 100K+ orders
-- ============================================================

-- -- CORE: closing_orders (slim, only essential fields) -----
CREATE TABLE closing_orders (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id             TEXT NOT NULL UNIQUE,
    merchant_order_id           TEXT,
    account                     TEXT NOT NULL DEFAULT 'HOANK',

    -- Amazon core
    order_status                TEXT DEFAULT 'Unshipped',
    asin                        TEXT,
    sku                         TEXT,
    title                       TEXT,
    quantity                    INTEGER DEFAULT 0,
    item_price                  REAL DEFAULT 0.0,
    shipping_price              REAL DEFAULT 0.0,

    -- Shipping
    ship_city                   TEXT,
    ship_state                  TEXT,
    ship_postal_code            TEXT,
    ship_country                TEXT DEFAULT 'US',

    -- Dates (from report or SC scrape)
    purchase_date               TEXT,
    ship_by_date                TEXT,
    deliver_by_date             TEXT,

    -- Earnings (from Gmail archive)
    earnings                    REAL,
    amazon_fees                 REAL,
    shipping_amt                REAL,
    sold_email_time             TEXT,

    -- Zoro data (from Gmail archive)
    wb_order_id                 TEXT,
    zoro_account_name           TEXT,
    zoro_gmail_account          TEXT,
    zoro_po_number              TEXT,
    zoro_order_status           TEXT DEFAULT 'Open',
    zoro_sku                    TEXT,
    zoro_qty                    INTEGER DEFAULT 0,
    zoro_order_date             TEXT,
    zoro_email_ts               TEXT,
    zoro_payment_card           TEXT,
    zoro_tax                    REAL DEFAULT 0.0,
    zoro_order_total            REAL DEFAULT 0.0,
    zoro_buyer_zip              TEXT,

    -- Coupons
    coupon_used                 TEXT,
    coupon_savings              REAL DEFAULT 0.0,

    -- Profit
    profit_dollars              REAL,
    profit_percent              REAL,

    -- Tracking / Delivery (from archive + FedEx)
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

    -- Post-delivery flags
    delivered_msg_sent          TEXT,
    review_requested            TEXT,
    return_requested            TEXT,
    refund_done                 TEXT,
    customer_messaged           TEXT,

    -- Labeling
    order_label                 TEXT DEFAULT 'Incomplete',
    comments                    TEXT DEFAULT '',

    -- Enrichment metadata
    enriched_from_archive       INTEGER DEFAULT 0,
    enriched_from_live_gmail    INTEGER DEFAULT 0,
    enriched_from_placer_db     INTEGER DEFAULT 0,
    enriched_from_fedex         INTEGER DEFAULT 0,
    enriched_from_scrape        INTEGER DEFAULT 0,
    last_enrichment_run         TEXT,
    scraped_at                  TEXT,

    -- Timestamps
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- -- RICH SC DATA: amazon_order_details ---------------------
CREATE TABLE amazon_order_details (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id             TEXT NOT NULL UNIQUE,
    account                     TEXT,

    -- Buyer info
    buyer_name                  TEXT,
    buyer_email                 TEXT,

    -- Ship to (full address)
    ship_to_name                TEXT,
    ship_to_street              TEXT,
    ship_to_city                TEXT,
    ship_to_state               TEXT,
    ship_to_zip                 TEXT,
    ship_to_country             TEXT,

    -- Bill to (full address)
    bill_to_name                TEXT,
    bill_to_street              TEXT,
    bill_to_city                TEXT,
    bill_to_state               TEXT,
    bill_to_zip                 TEXT,
    bill_to_country             TEXT,

    -- Order attributes
    shipping_service            TEXT,
    fulfillment_channel         TEXT,
    sales_channel               TEXT,
    payment_method              TEXT,
    is_gift                     INTEGER DEFAULT 0,
    gift_message                TEXT,

    -- Financials from page
    order_subtotal              REAL,
    shipping_cost               REAL,
    tax_amount                  REAL,
    discount_amount             REAL,
    order_total                 REAL,
    currency                    TEXT DEFAULT 'USD',

    -- Raw page data
    page_body_snippet           TEXT,
    scraped_at                  TEXT,

    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- -- MESSAGES: order_messages -------------------------------
CREATE TABLE order_messages (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id             TEXT NOT NULL,
    account                     TEXT,

    message_type                TEXT,
    direction                   TEXT,
    subject                     TEXT,
    body_snippet                TEXT,
    sender                      TEXT,
    timestamp                   TEXT,

    -- Gmail source
    gmail_message_id            TEXT,
    gmail_account               TEXT,
    source                      TEXT DEFAULT 'gmail_archive',

    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- -- RETURNS: order_returns ---------------------------------
CREATE TABLE order_returns (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id             TEXT NOT NULL,
    wb_order_id                 TEXT,
    account                     TEXT,

    return_reason               TEXT,
    return_tracking             TEXT,
    return_carrier              TEXT,
    return_initiated_date       TEXT,
    return_received_date        TEXT,
    return_window_expired       INTEGER DEFAULT 0,

    refund_amount               REAL,
    refund_tax                  REAL,
    refund_date                 TEXT,
    refund_status               TEXT,

    replacement_sent            INTEGER DEFAULT 0,
    replacement_tracking        TEXT,

    source                      TEXT DEFAULT 'gmail_archive',
    raw_email_snippet           TEXT,

    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- -- FEEDBACK: order_feedback -------------------------------
CREATE TABLE order_feedback (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    amazon_order_id             TEXT NOT NULL,
    account                     TEXT,

    -- Seller feedback (on order)
    feedback_rating             INTEGER,
    feedback_text               TEXT,
    feedback_date               TEXT,
    seller_response             TEXT,
    seller_response_date        TEXT,

    -- Product review (if visible)
    review_rating               INTEGER,
    review_text                 TEXT,
    review_date                 TEXT,
    review_title                TEXT,
    verified_purchase           INTEGER DEFAULT 0,

    source                      TEXT DEFAULT 'scrape',
    scraped_at                  TEXT,

    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- -- INDEXES ------------------------------------------------
CREATE INDEX idx_closing_amazon          ON closing_orders(amazon_order_id);
CREATE INDEX idx_closing_wb              ON closing_orders(wb_order_id);
CREATE INDEX idx_closing_account         ON closing_orders(account);
CREATE INDEX idx_closing_delivery        ON closing_orders(delivery_status);
CREATE INDEX idx_closing_purchase        ON closing_orders(purchase_date);
CREATE INDEX idx_closing_enriched        ON closing_orders(last_enrichment_run);
CREATE INDEX idx_closing_label           ON closing_orders(order_label);
CREATE INDEX idx_closing_scraped         ON closing_orders(scraped_at);

CREATE INDEX idx_details_amazon          ON amazon_order_details(amazon_order_id);
CREATE INDEX idx_details_account         ON amazon_order_details(account);

CREATE INDEX idx_messages_amazon         ON order_messages(amazon_order_id);
CREATE INDEX idx_messages_type           ON order_messages(message_type);
CREATE INDEX idx_messages_timestamp      ON order_messages(timestamp);

CREATE INDEX idx_returns_amazon          ON order_returns(amazon_order_id);
CREATE INDEX idx_returns_wb              ON order_returns(wb_order_id);

CREATE INDEX idx_feedback_amazon         ON order_feedback(amazon_order_id);
CREATE INDEX idx_feedback_rating         ON order_feedback(feedback_rating);
