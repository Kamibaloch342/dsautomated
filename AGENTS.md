# AGENTS.md — Dropshipping Development Unit

> **Update (7 September 2026):** the `Final Automation/` stack described below no longer exists — `Automations/` IS the live production stack ("ZUHRA Software"). Ignore any line here that says not to modify `Automations/`. Before changing anything there read `Automations/automations_complete_documentation.md` (Appendix F guardrails) and `Automations/docs/CLOUD_ARCHITECTURE.md` (R2 cloud layer, launcher remote control, routing rules).


> This file is the **authoritative guide** for AI agents working on this codebase.  
> Read it in full before making any changes. Human instructions in conversation always take precedence.

---

## 1. Project Overview

This is a **mature, end-to-end Amazon → Zoro dropshipping operation** spanning five business phases:

| Phase | Directory | Purpose |
|-------|-----------|---------|
| **Data Ingestion** | `TSV Files/` | Sync Zoro supplier catalog from AWS S3 into local SQLite |
| **Product Hunting** | `Listings_Hunting/` | Research competitors, match products, calculate profitability, auto-list on Amazon |
| **Daily Operations** | `Root level` | Maintain active listings (activate/inactivate/reprice/delete) via GUI |
| **Order Fulfillment** | `Automations/` + `Final Automation/` | Scrape Amazon orders, buy on Zoro, upload tracking, message customers |
| **Financial Closing** | `DS_Closing/` | Reconcile Amazon revenue vs Zoro costs into P&L reports |

**Two Amazon seller accounts:**
- **HOANK STORE** (`qurtdis` Gmail for earnings)
- **Hathoro** (`abecomllc26` Gmail for earnings)

**Supplier:** Zoro.com (industrial supplies, dropshipped via FedEx)

---

## 2. Architecture Philosophy

### 2.1 Two Parallel Automation Stacks

This codebase contains **two independent order-fulfillment stacks** that evolved separately:

**Stack A — `Automations/` ("ZUHRA Software")**
- GUI-driven (Tkinter dashboard: `launcher.py`)
- Scrapes Amazon SC search pages directly (no report download)
- Places orders on Zoro in real-time via CDP-based API calls
- 4 Chrome profiles running on ports 9221–9224
- Writes WB order IDs back to Amazon merchant-order-id field
- ~7,000 lines, highly interactive

**Stack B — `Final Automation/` ("Unified Tracker")**
- Headless, cron/systemd-driven
- Downloads Amazon FlatFileAllOrdersReport TSV
- Processes order-by-order or in batch
- Writes to Google Sheets (primary) or SQLite (legacy)
- Single Chrome profile per account
- ~6,500 lines in `unified_tracker.py` alone

> **Critical rule:** `.github/copilot-instructions.md` mandates that **all new automation code must live in `Final Automation/`**. Do not modify files in `Automations/` unless explicitly asked.

### 2.2 Source of Truth

- **Production runtime:** Google Sheets (`GOOGLE_SHEET_ID` in `config.py`)
- **Local cache:** SQLite (`hoank_tracker.db`) — used for batch mode and historical queries
- **Email as audit trail:** Gmail stores earnings confirmations, Zoro receipts, tracking emails, return notices

### 2.3 Order Lifecycle

```
Amazon Sale → Zoro Purchase → Zoro Ships → Tracking Found → FedEx Delivers
     ↓              ↓              ↓              ↓                ↓
  Earnings      Cost/tax    Shipped email   Tracking ID    Delivery status
  (Gmail)       (Gmail)     (Gmail)         (Gmail/PDF)    (FedEx CDP)
     ↓              ↓              ↓              ↓                ↓
  Profit calc   Profit calc   Shipped_time   Sheet update   Post-delivery msg
                                                                + review req
```

**Terminal state:** `Label = "Completed"` (green row in sheet)

---

## 3. Directory Structure

```
Development_Unit/
├── .github/copilot-instructions.md    # AI agent rules (read this)
├── .vscode/settings.json              # Minimal VS Code config
├── AGENTS.md                          # ← This file
│
├── Final Automation/                  # ⭐ PRIMARY automation stack
│   ├── unified_tracker.py             # Main orchestrator (~6500 lines)
│   ├── config.py                      # All constants, paths, accounts
│   ├── utils.py                       # Gmail auth, Selenium, PDF, HTML
│   ├── db.py                          # SQLite schema & access layer
│   ├── amazon_earnings.py             # Earnings parser (legacy)
│   ├── amazon_report.py               # Report downloader (legacy)
│   ├── zoro_database.py               # Zoro cost parser (legacy)
│   ├── tracking_finder.py             # Tracking ID finder (legacy)
│   ├── fedex_checker.py               # FedEx scraper (legacy/testing)
│   ├── sheet_cleanup.py               # Sheet dedup/blank-row cleanup
│   ├── analyze_sheet.py               # Sheet backlog analyzer
│   ├── compare_dups.py                # Duplicate row comparator
│   ├── verify_sheet.py                # Sheet verification utility
│   ├── clear_sheet.py                 # Sheet clear utility
│   ├── health_check.py                # Heartbeat monitor
│   ├── Refunds_Received.py            # Zoro refund PDF scanner
│   ├── ARCHITECTURE_CURRENT_STATE.md  # Current architecture docs
│   ├── PROCESSING_MODEL.md            # Batch vs order-by-order model
│   ├── RETURN_HANDLING.md             # Return/refund handling guide
│   ├── LINUX_DEPLOYMENT.md            # Ubuntu deployment guide
│   ├── CODEBASE_VISUAL_MAP.md         # ⚠️ Outdated (5-table schema)
│   ├── PROJECT_EVOLUTION.txt          # ⚠️ Outdated historical log
│   ├── EML Files For Testing/         # Template emails for message detection
│   └── All_Tokens/                    # Gmail OAuth tokens + SC creds
│
├── Automations/                       # "ZUHRA Software" (GUI stack)
│   ├── launcher.py                    # Tkinter dashboard
│   ├── order_engine.py                # Universal order pipeline
│   ├── order_config.py                # 4-profile config
│   ├── order_amazon.py                # Amazon SC scraping
│   ├── order_zoro.py                  # Zoro API via CDP fetch
│   ├── order_checkout.py              # Zoro checkout flow (~1800 lines)
│   ├── order_gmail.py                 # Gmail earnings + duplicate check
│   ├── order_helpers.py               # Stealth JS, CSV logging, coupons
│   ├── login_helper.py                # Amazon SC auto-login with TOTP
│   ├── auto_tracking.py               # Tracking upload + customer messages
│   ├── coupon_generator.py            # Zoro 10% coupon generator
│   ├── test_textarea_injection.py     # Messaging injection test
│   ├── bot_config.json                # Live runtime config
│   ├── dataset/coupon_metadata.json   # Coupon cache
│   ├── README.md                      # ZUHRA architecture docs
│   └── RAJAR_PIPELINE.md              # Legacy pipeline docs
│
├── Listings_Hunting/                  # Product research & listing
│   ├── Keepa Files/Keepa.py           # Profitability matcher
│   ├── Smartscout_Extractor/
│   │   ├── Smartscout.py              # Competitor scraper
│   │   └── Autohunter_lister.py       # Auto-lister on Amazon SC
│   └── ...
│
├── TSV Files/                         # Supplier data ingestion
│   ├── auto_download.py               # S3 sync → SQLite
│   ├── combined_reports.db            # Zoro catalog DB
│   └── zoro_datafeed_*.tsv            # Raw supplier feeds
│
├── DS_Closing/                        # Financial reconciliation
│   ├── Discounted_Closing/            # Hathoro account closing
│   │   ├── Final_Closing_Script.py
│   │   ├── extracting_earning.py
│   │   ├── Zoro_orders_database.py
│   │   ├── main_profit_calculator.py
│   │   ├── PPC_SP-analysis.py         # ⚠️ Empty placeholder
│   │   ├── Amex Statements/
│   │   └── Order_Reports/
│   └── Hypersale_Closing/             # HOANK account closing
│       ├── Final_Closing_Script.py
│       ├── extracting_earning.py
│       ├── Zoro_orders_database.py
│       ├── main_profit_calculator.py
│       ├── yearly_closing.py          # Annual consolidated report
│       ├── Amex Statements/
│       └── Order_Reports/
│
├── Auto_Lister.py                     # Bulk relisting tool
└── Complete_Daily_Listing_Automation.py  # Daily listing maintenance GUI
```

---

## 4. Core Systems — `Final Automation/`

### 4.1 `unified_tracker.py` — The Orchestrator

**~6,500 lines. This is the most important file in the project.**

**Execution modes (set via CLI flags):**

| Mode | Flag | What it does |
|------|------|-------------|
| **Sheet-direct** | `--sheet-direct` (default) | Order-by-order processing; writes directly to Google Sheets |
| **Batch DB** | (no flag, legacy) | Full pipeline every 10 min; writes to SQLite |
| **FedEx-only** | `--fedex-only` | Re-checks delivery status for in-transit orders from sheet |
| **Backlog** | `--backlog YYYY-MM-DD` | Day-by-day historical processing |
| **Dry run** | `--dry-run` | Extracts data but does not write to sheet |

**Pipeline steps (sheet-direct mode):**

1. **Download** Amazon FlatFileAllOrdersReport via Selenium
2. **Parse** TSV → keep only WB orders (merchant-order-id starts with "WB")
3. **Consolidate** multi-item orders (sum quantities/prices, append SKUs/ASINs)
4. **Fetch earnings** from Gmail ("Sold, ship now" emails)
5. **Fetch Zoro data** from Gmail ("Has Been Received" emails)
6. **Calculate profit** (`earnings - zoro_cost`)
7. **Find tracking IDs** from Gmail bodies + PDF attachments
8. **Check FedEx** delivery status via Chrome CDP
9. **Check returns/refunds** from Gmail
10. **Scrape Amazon dates** (purchase, ship-by, deliver-by)
11. **Apply post-delivery decisions** (message/review eligibility)
12. **Label** order (Completed / Incomplete / Cancelled)
13. **Upsert to Google Sheets**

**Post-delivery actions (triggered separately after step 13):**
- **Delivery check-in message:** Sent 6 days after delivery if `Msg Sent?` ≠ Yes
- **Review request:** Sent 12 days after delivery if `Review Sent?` ≠ Yes
- **Transit alert:** Sent if tracking stuck 3+ days past deliver-by date

**Key functions:**

| Function | Purpose |
|----------|---------|
| `run_sheet_direct()` | Main sheet-direct entry point |
| `download_report()` | Selenium-driven TSV download from Amazon SC |
| `_parse_report_wb_orders()` | Parse TSV, filter WB orders, consolidate multi-item |
| `_fetch_earnings_single()` | Gmail search → parse fees/earnings for one order |
| `_fetch_zoro_single()` | Gmail search → parse Zoro cost/tax/SKU/qty for one order |
| `_find_tracking_single()` | Gmail search → extract tracking IDs from text + PDFs |
| `_check_fedex_single_order()` | FedEx CDP tracking for one order |
| `_check_return_refund_single()` | Gmail search for return/refund emails |
| `_apply_dates_and_decisions_single()` | Compute message/review eligibility, manage pending comments |
| `_process_post_delivery_single()` | Actually send messages/reviews via Amazon SC |
| `_send_amazon_message()` | Selenium: navigate to contact page, inject message, send |
| `_request_amazon_review()` | Selenium: click "Request a Review" button |
| `_label_single_order()` | Compute Completed/Incomplete/Cancelled label |
| `_upsert_order_to_sheet()` | Write one order row to Google Sheets |
| `_apply_conditional_formatting()` | Apply color rules (profit, qty match, zip match, etc.) |
| `_recheck_msg_sent_flags()` | Re-verify all `Msg Sent? = Yes` rows against Gmail |

### 4.2 `config.py` — Central Configuration

**All tunable parameters live here.** Key categories:

**Accounts:**
- `AMAZON_ACCOUNTS` — HOANK STORE + Hathoro configs (messaging permissions, review permissions)
- `ACCOUNT_PROFILES` — Chrome profile dirs, UC ports, display names
- Gmail accounts for earnings, Zoro orders, duplicate checking, refunds

**Paths:**
- `PROJECT_ROOT`, `FINAL_AUTOMATION_DIR`
- `DOWNLOAD_DIRECTORY` — Where Amazon TSV reports land
- `SQLITE_DB_PATH` — `hoank_tracker.db`
- Chrome binary path, Chrome profile dirs

**Timing:**
- `CYCLE_INTERVAL_SECONDS = 600` — 10 minutes between tracker runs
- `FEDEX_RECHECK_INTERVAL_HOURS = 2`
- `RETURNS_LOOKBACK_HOURS = 72`
- `DAYS_AFTER_DELIVERY_TO_MESSAGE = 6`
- `DAYS_AFTER_DELIVERY_TO_REVIEW = 12`
- `DAYS_PAST_DELIVER_BY_STUCK = 3`

**FedEx CDP:**
- `FEDEX_CDP_PORT = 9223`
- `FEDEX_CDP_BATCH_SIZE = 30`
- `FEDEX_WEB_CLIENT_ID` — OAuth client ID for FedEx internal API

**Google Sheets:**
- `GOOGLE_SHEET_ID = "1aNMSPkBjmUUgZNO45KXtMd88ddeBYJbqDxOknaEVooM"`
- `GOOGLE_SHEET_TAB` — Active tab name
- `GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE` — Service account JSON key

**Resilience:**
- `MAX_RETRIES = 3`
- `RETRY_BASE_DELAY = 5`
- `DB_VACUUM_EVERY_N_CYCLES = 100`

### 4.3 `utils.py` — Shared Utilities

**Gmail:**
- `get_gmail_service(account_name)` — OAuth2 auth with token refresh
- `search_gmail(service, query, max_results)` — Paginated search (500/msg page)
- `get_full_message()`, `get_email_body_html()` — Recursive payload extraction

**Parsing:**
- `clean_html(raw)` — Strip tags, decode entities
- `clean_currency(value)` — "$1,234.56" → `float(1234.56)`
- `extract_tracking_ids_from_text(text)` — Find 12-digit FedEx tracking numbers
- `extract_tracking_ids_from_pdf(file_data)` — PDF attachment parsing via `pdfplumber`

**Selenium:**
- `get_report_driver()` — Launch undetected_chromedriver for Amazon
- `kill_report_driver(driver)` — Clean shutdown + process cleanup
- `launch_chrome_with_debugging()` — Launch Chrome with remote debugging port

### 4.4 `db.py` — SQLite Database Layer

**Current schema (single `orders` table, ~45 columns):**

**Amazon fields:**
`amazon_order_id` (PK), `merchant_order_id`, `order_status`, `sku`, `asin`, `quantity`, `item_price`, `shipping_price`, `ship_city`, `ship_state`, `ship_postal_code`, `purchase_date`, `ship_by_date`, `deliver_by_date`

**Earnings:**
`pkt_timestamp`, `amazon_fees`, `your_earnings`

**Zoro fields:**
`wb_order_id`, `zoro_account_name`, `zoro_order_status`, `zoro_sku`, `zoro_qty`, `zoro_order_date`, `zoro_email_ts`, `zoro_payment_card`, `zoro_tax`, `zoro_order_total`, `zoro_buyer_zip`

**Tracking/Delivery:**
`tracking_ids`, `delivery_status`, `delivery_date`, `estimated_window`, `last_scan_location`, `last_scan_time`, `signed_by`, `left_at`, `delivery_note`, `delivery_from`, `delivery_to`

**Post-delivery:**
`return_requested`, `refund_done`, `customer_messaged`, `delivered_msg_sent`, `review_requested`

**Meta:**
`comments`, `order_label`, `created_at`, `processing_status`, `processing_step`, `last_processed_at`, `processing_attempts`, `processing_error`

**Secondary table:**
- `zoro_only_orders` — Tracks WB orders found in Zoro emails but unmatched to any Amazon order

**Note:** The DB is used in batch mode. In sheet-direct mode (current production), Google Sheets is the source of truth and the DB is mostly unused.
d