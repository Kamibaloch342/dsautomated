# Automations — HOANK STORE Order & Tracking System

> **Last cleaned:** 2026-04-05 — all legacy, debug, and test files removed.

## Overview

Selenium + CDP automation suite for the HOANK STORE Amazon → Zoro dropshipping workflow.
Handles the full lifecycle: Amazon order ingestion → Zoro placement → tracking upload → customer messaging.

### Architecture at a Glance

```
launcher.py                    ← GUI control panel (Tkinter), spawns everything
  ├── auto_orders.py           ← Order pipeline (DM_COUPON / DM_RESELLER / HS_COUPON)
  ├── rajar_pipeline.py        ← RAJAR pipeline (HS_RESELLER, scraped from SC search)
  └── auto_tracking.py         ← Tracking upload + customer messaging
      │
      ├── order_config.py      ← Profiles, ports, selectors, Gmail accounts
      ├── order_helpers.py     ← Browser stealth, human sim, CSV, coupon cache
      ├── login_helper.py      ← Seller Central auto-login + TOTP 2FA
      ├── order_amazon.py      ← Report download, address scraping, seller-ID update
      ├── order_gmail.py       ← OAuth, earnings lookup, duplicate detection
      ├── order_zoro.py        ← Inventory, cart, address CRUD, restriction recovery
      └── order_checkout.py    ← Checkout flow, coupon application, payment switch
```

## What Is Actually Live

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [File Inventory](#file-inventory)
3. [Entry Points (Runners)](#entry-points-runners)
4. [Core Modules (Libraries)](#core-modules-libraries)
5. [Configuration & State Files](#configuration--state-files)
6. [Directories](#directories)
7. [Profile Model](#profile-model)
8. [Execution Flows](#execution-flows)
9. [Stealth & Anti-Detection](#stealth--anti-detection)
10. [Checkout Architecture](#checkout-architecture)
11. [Credential & Token Layout](#credential--token-layout)
12. [Runtime Artifacts & Cleanup](#runtime-artifacts--cleanup)
13. [Troubleshooting](#troubleshooting)

---

## Quick Start

```bash
# 1. Start Chrome with remote debugging (must be running before launcher)
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/chrome-profiles/hoank_normal_orders"

# 2. Launch the GUI control panel
python launcher.py
```

The launcher GUI lets you:
- Select a profile (DM_COUPON, DM_RESELLER, HS_COUPON, HS_RESELLER)
- Start/stop order automation, RAJAR pipeline, or tracking upload
- Edit `bot_config.json` settings in real-time

---

## File Inventory

After the 2026-04-05 cleanup, the folder contains **only production files**:

| File | Type | Lines | Purpose |
|------|------|-------|---------|
| `launcher.py` | Runner | ~710 | Tkinter GUI — spawns all other runners |
| `auto_orders.py` | Runner | ~900 | General order pipeline (3 profiles) |
| `rajar_pipeline.py` | Runner | ~1200 | RAJAR-specific order pipeline |
| `auto_tracking.py` | Runner | ~1400 | Tracking upload + customer messaging |
| `order_config.py` | Module | ~380 | Profiles, ports, selectors, Gmail config |
| `order_helpers.py` | Module | ~650 | Browser stealth, human sim, CSV, coupons |
| `order_amazon.py` | Module | ~550 | Report download, address scrape, ID update |
| `order_gmail.py` | Module | ~200 | OAuth, earnings lookup, duplicate check |
| `order_zoro.py` | Module | ~650 | Zoro API: inventory, cart, restriction fix |
| `order_checkout.py` | Module | ~900 | Checkout flow, coupons, payment, PO |
| `login_helper.py` | Module | ~400 | Seller Central login + TOTP 2FA |
| `bot_config.json` | Config | — | User-editable runtime settings |
| `message_log.json` | State | — | Tracking message dedup (450+ entries) |
| `README.md` | Docs | — | This file |
| `RAJAR_PIPELINE.md` | Docs | — | RAJAR flow documentation |

**Total production code:** ~7,000 lines across 11 Python files.

---

## Entry Points (Runners)

### `launcher.py` — GUI Control Panel

The single human-facing entry point. A Tkinter application that:

- Manages multi-profile subprocess orchestration
- Routes profiles to the correct runner:
  - `DM_COUPON`, `DM_RESELLER`, `HS_COUPON` → `auto_orders.py`
  - `HS_RESELLER` → `rajar_pipeline.py`
  - Tracking → `auto_tracking.py`
- Reads/writes `bot_config.json` for live setting changes
- Shows real-time stdout from child processes

### `auto_orders.py` — Standard Order Pipeline

The main order orchestrator for non-RAJAR profiles.

**Step-by-step flow:**
1. Load profile from `order_config.py`
2. Authenticate Gmail accounts via `order_gmail.py`
3. Download Amazon order report via `order_amazon.py`
4. Merge SKU data and filter by target label
5. For each qualifying order:
   - Scrape and validate shipping address (`order_amazon.py`)
   - Check for duplicate orders (`order_gmail.py`)
   - Check Zoro inventory (`order_zoro.py`)
   - Add to cart and set shipping address (`order_zoro.py`)
   - Apply coupons and place order (`order_checkout.py`)
   - Write `WB-*` order ID (or status code) back to Amazon seller-order-id

### `rajar_pipeline.py` — RAJAR Pipeline

A specialized runner for `HS_RESELLER` that **does not use the report download flow**:

1. Scrapes Amazon Seller Central search results directly
2. Loads ASIN → SKU mapping from a Google Sheet (URL in `order_config.py`)
3. Reuses shared modules for: address scraping, Gmail earnings/duplicate checks, Zoro placement
4. Writes timestamped CSV + JSON run logs to `rajar_logs/`

See [RAJAR_PIPELINE.md](RAJAR_PIPELINE.md) for the full 11-step process and status codes.

### `auto_tracking.py` — Tracking & Customer Messaging

Handles the post-order lifecycle:

1. Authenticate Gmail accounts
2. Search emails for Zoro shipping/tracking confirmations
3. Parse tracking IDs (FedEx 12-digit, UPS, etc.)
4. Upload tracking to Amazon Seller Central
5. Send customer messages:
   - **Tracking confirmation** — order shipped with tracking link
   - **Stock delay** — item backordered, expected ship date
   - **Cancel denial** — order already shipped, cannot cancel
6. Persist sent-message state in `message_log.json` (prevents re-messaging)
7. Supports both parallel and sequential execution modes

---

## Core Modules (Libraries)

### `order_config.py` — Central Configuration

The single source of truth for all runtime settings:

- **4 profiles:** `DM_COUPON`, `DM_RESELLER`, `HS_COUPON`, `HS_RESELLER`
- **Chrome ports:** 9221–9224 (one per profile)
- **Gmail accounts:** profile-specific send/receive accounts
- **Token paths:** points to `All_Tokens/` for OAuth JSON files
- **Selectors:** CSS/XPath selectors for Amazon SC pages
- **Thresholds:** min margin, order limits, velocity settings

**Any change to ports, labels, tokens, or profiles starts here.**

### `order_helpers.py` — Browser Infrastructure

The utility layer used by all three runners:

- `setup_driver()` — connect to Chrome via CDP remote debugging
- `_build_stealth_js()` — generate anti-detection JavaScript payload
- `simulate_human()` — random mouse moves, scrolls, typing delays
- `load_coupon_metadata()` / `save_coupon_metadata()` — persistent coupon cache
- `print_rich_summary()` — formatted run summary with profit/loss
- CSV download and parsing helpers

### `order_amazon.py` — Amazon Seller Central Operations

- `download_amazon_order_report()` — pull unshipped order TSV from SC
- `scrape_amazon_address()` — extract buyer address from order detail page
- `validate_and_prepare_address()` — normalize state/zip, detect PO boxes
- `update_amazon_seller_id()` — write WB order ID or status code back to SC

### `order_gmail.py` — Gmail Integration

- `authenticate_gmail_account()` — OAuth2 with JSON tokens, auto-refresh
- `get_amazon_earnings_from_gmail()` — parse "Sold, ship now" email for revenue/fees
- `check_if_address_already_ordered_gmail()` — duplicate detection by searching mailbox

### `order_zoro.py` — Zoro API Layer

Operates via in-browser `fetch()` calls (not direct HTTP), staying within the Zoro session:

- `check_inventory()` — verify SKU availability and price
- `clear_cart_and_add()` — empty cart, add item with quantity
- `update_default_address()` — set shipping address via API
- `reset_restriction()` — handle DataDome blocks: clear cookies, rotate IP
- `zoro_api()` — generic wrapper for all Zoro REST endpoints

### `order_checkout.py` — Checkout Flow Engine

The most complex module — handles two distinct Zoro checkout layouts:

- `place_order_fast()` — main entry: address → coupons → payment → PO → submit
- `_ensure_address_and_shipping()` — validate/correct address modal
- `_apply_coupons()` — detect old (text input) vs new (dropdown) coupon UI
- `_click_place_order()` — final submission with retry and confirmation detection
- Payment method switching between stored cards
- Stale checkout recovery (re-navigate if page state is lost)

### `login_helper.py` — Seller Central Authentication

- `auto_login()` — full login flow: email → password → TOTP 2FA
- `is_login_page()` — detect redirect to Amazon sign-in
- `ensure_logged_in()` — wrapper that checks and re-authenticates if needed
- `resolve_account_key()` — map profile name to credential entry
- Reads credentials from `All_Tokens/seller_central_creds.json`

---

## Configuration & State Files

### `bot_config.json`

User-editable JSON read by the launcher at runtime:

```json
{
  "priority_coupons": ["COUPON1", "COUPON2", "COUPON3"],
  "DM_COUPON": {
    "target_label": "dm-label",
    "min_margin": -20,
    "order_limit": 0,
    "priority_asins": []
  },
  "velocity": { ... },
  "blocked_order_ids": []
}
```

Each profile block controls: target label filter, minimum margin percentage, max orders per run, and priority ASINs.

### `message_log.json`

Persistent dictionary mapping Amazon order IDs to messaging state:

```json
{
  "111-2345678-9012345": {
    "message_type": "tracking_confirm",
    "sent": true,
    "timestamp": "2026-03-15T14:30:00"
  }
}
```

Prevents `auto_tracking.py` from re-sending messages to the same customer.

---

## Directories

| Directory | Purpose | Can Contents Be Deleted? |
|-----------|---------|--------------------------|
| `All_Tokens/` | Gmail OAuth tokens + SC credentials | **NO** — required for auth |
| `rajar_logs/` | Timestamped CSV/JSON run artifacts | Yes (regenerated each run) |

### `All_Tokens/` Contents

- 16 Gmail OAuth token files (one per account)
- `AB ECOM Credentials.json` — Google OAuth client secret
- `seller_central_creds.json` — Amazon SC login credentials + TOTP secrets

---

## Profile Model

Four profiles defined in `order_config.py`, each with dedicated Chrome port and Gmail accounts:

| Profile | Chrome Port | Runner | Description |
|---------|-------------|--------|-------------|
| `DM_COUPON` | 9221 | `auto_orders.py` | DM store, coupon-based margin |
| `DM_RESELLER` | 9222 | `auto_orders.py` | DM store, reseller margin |
| `HS_COUPON` | 9223 | `auto_orders.py` | HOANK store, coupon-based |
| `HS_RESELLER` | 9224 | `rajar_pipeline.py` | HOANK store, RAJAR scrape flow |

**Key:** `HS_RESELLER` is the only profile routed to the RAJAR pipeline. All others go through the standard `auto_orders.py` report-based flow.

---

## Execution Flows

### Flow 1: Standard Order Automation

```
launcher.py
  └─ subprocess ─→ auto_orders.py (profile: DM_COUPON|DM_RESELLER|HS_COUPON)
                     │
                     ├─ order_config.py      load profile settings
                     ├─ order_gmail.py        authenticate Gmail
                     ├─ order_amazon.py       download report + scrape addresses
                     ├─ order_gmail.py        check earnings + duplicates
                     ├─ order_zoro.py         check inventory + add to cart
                     ├─ order_checkout.py     apply coupons + place order
                     └─ order_amazon.py       write WB ID back to Amazon
```

**Status codes written to Amazon merchant-order-id:**
- `WB-12345678` — successfully placed on Zoro
- `OOS` — out of stock on Zoro
- `Discontinued` — item removed from Zoro
- `Loss` — margin below threshold
- `Duplicate` — already ordered (detected via Gmail)

### Flow 2: RAJAR Pipeline

```
launcher.py
  └─ subprocess ─→ rajar_pipeline.py (profile: HS_RESELLER)
                     │
                     ├─ order_config.py      load profile + sheet URL
                     ├─ login_helper.py       ensure SC login
                     ├─ [SC search scrape]    find RAJAR orders directly
                     ├─ [Google Sheet]        ASIN → SKU mapping
                     ├─ order_gmail.py        earnings + duplicates
                     ├─ order_amazon.py       address scraping
                     ├─ order_zoro.py         inventory + cart
                     ├─ order_checkout.py     coupons + placement
                     └─ rajar_logs/           write CSV + JSON artifacts
```

### Flow 3: Tracking Upload

```
launcher.py
  └─ subprocess ─→ auto_tracking.py
                     │
                     ├─ All_Tokens/           Gmail OAuth tokens
                     ├─ [Gmail search]        find Zoro shipping emails
                     ├─ [parse tracking]      extract FedEx/UPS IDs
                     ├─ [Amazon SC]           upload tracking numbers
                     ├─ [Amazon SC]           send customer messages
                     └─ message_log.json      persist sent-message state
```

---

## Stealth & Anti-Detection

The system uses multiple layers to avoid bot detection:

1. **CDP Stealth Injection** (`order_helpers.py`):
   - Removes `navigator.webdriver` flag
   - Removes ChromeDriver CDC artifacts (`cdc_` variables)
   - Overrides `navigator.plugins` and `navigator.languages`
   - Sets consistent timezone via Chrome DevTools Protocol

2. **Human Simulation** (`order_helpers.py`):
   - Randomized mouse movements and scroll events
   - Variable typing speed with occasional pauses
   - Random delays between actions (not fixed `time.sleep`)

3. **DataDome Recovery** (`order_zoro.py`):
   - Detects restriction/block responses
   - Clears DataDome cookies
   - Rotates IP (if proxy configured)
   - Retries with fresh session

---

## Checkout Architecture

`order_checkout.py` handles **two distinct Zoro checkout UI layouts**:

### Old Layout (text input coupons)
- Coupon code entered in a text input field
- "Apply" button clicked
- Success/failure detected by DOM changes

### New Layout (dropdown coupons)
- Coupon selection via dropdown menu
- Different selectors and interaction pattern
- Module auto-detects which layout is active

### Checkout Sequence
1. Navigate to checkout page
2. Validate/correct shipping address (modal handling)
3. Select payment method (switch between stored cards)
4. Apply coupon codes (try priority coupons in order)
5. Enter PO number (Amazon order ID)
6. Click "Place Order" with retry logic
7. Confirm order placement (detect confirmation page)
8. Extract WB order ID from confirmation

---

## Credential & Token Layout

```
All_Tokens/
  ├── AB ECOM Credentials.json         ← Google OAuth client secret
  ├── seller_central_creds.json        ← Amazon SC login + TOTP keys
  ├── token_qurtdis.json               ← Earnings Gmail
  ├── token_asad_amazon_us.json        ← Zoro order Gmail
  ├── token_salesmercentile1.json      ← Zoro order Gmail
  └── [13 more Gmail token files]      ← Various account tokens
```

**Security notes:**
- Tokens are OAuth2 JSON files with auto-refresh
- Never commit `All_Tokens/` to version control
- `AB ECOM Credentials.json` is the OAuth client ID/secret — treat as secret
- `seller_central_creds.json` contains plaintext passwords and TOTP seeds

---

## Runtime Artifacts & Cleanup

These files are regenerated automatically and can be safely deleted:

| Pattern | Source | Safe to Delete? |
|---------|--------|:---:|
| `__pycache__/` | Python bytecode | Yes |
| `.DS_Store` | macOS Finder | Yes |
| `.script_*.lock` | Launcher lock files | Yes |
| `.orders_*_output.log` | Runner stdout logs | Yes |
| `rajar_logs/run_*.csv` | RAJAR run output | Yes |
| `rajar_logs/run_*.json` | RAJAR run output | Yes |
| `*.bak` | Backup files | Yes |

Quick cleanup command:
```bash
cd Automations
rm -rf __pycache__ .DS_Store .script_*.lock .orders_*_output.log *.bak
# Optionally clear old RAJAR logs:
rm -f rajar_logs/run_*
```

---

## Troubleshooting

### Chrome won't connect
- Ensure Chrome is running with `--remote-debugging-port=XXXX` matching the profile port
- Only one Chrome instance can use a given port
- Check port with: `lsof -i :9222`

### Gmail authentication fails
- Token files expire — delete the token JSON and re-authenticate
- Ensure `AB ECOM Credentials.json` is present in `All_Tokens/`
- Check Google Cloud Console for OAuth consent screen issues

### Zoro restriction / DataDome block
- The system auto-recovers by clearing cookies and retrying
- If persistent: manually clear Chrome profile cookies for zoro.com
- Check if IP is flagged — try a different network/proxy

### Orders not being placed
- Check `bot_config.json` → `order_limit` (0 = unlimited)
- Check `min_margin` — orders below this threshold get `Loss` status
- Check Amazon SC for `OOS`/`Discontinued` status codes already written

### Launcher won't start a runner
- Check for stale `.script_*.lock` files and delete them
- Ensure no zombie Python processes: `ps aux | grep auto_orders`
- most remaining Python files are legacy or debugging leftovers, not the active system