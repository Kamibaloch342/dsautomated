# Project Rules — Dropshipping Automation (HOANK STORE)

> **Update (7 September 2026):** the `Final Automation/` stack described below no longer exists — `Automations/` IS the live production stack ("ZUHRA Software"). Ignore any line here that says not to modify `Automations/`. Before changing anything there read `Automations/automations_complete_documentation.md` (Appendix F guardrails) and `Automations/docs/CLOUD_ARCHITECTURE.md` (R2 cloud layer, launcher remote control, routing rules).


## Architecture
- All new automation code lives in `Final Automation/` folder
- Do NOT modify any existing `.py` files outside `Final Automation/`
- Single SQLite database (`hoank_tracker.db`) is the source of truth — no Excel output
- One main entry point: `Final Automation/unified_tracker.py`

## Accounts & Credentials
- **Amazon Store:** HOANK STORE only
- **Earnings Gmail:** `qurtdis` (receives "Sold, ship now" emails)
- **Zoro Order Gmails:** `asad_amazon_us`, `salesmercentile1` (receive Zoro order confirmations)
- **All Gmail tokens:** `Zoro things/All_Tokens/` (single source, never duplicate)
- **OAuth credentials file:** `AB ECOM Credentials.json`
- **Chrome profile:** `hoank_normal_orders` (macOS), port `9223`

## Data Conventions
- Zoro order IDs start with `WB` (e.g., `WB-12345678`)
- Amazon merchant-order-id field stores the WB ID (or "OOS"/"Discontinued"/"Loss"/"Duplicate")
- FedEx tracking IDs are 12 digits, scraped from FedEx.com with `FDEG` qualifier
- Marketplace ID: `ATVPDKIKX0DER` (Amazon US)

## SQLite Schema Tables
- `amazon_orders` — source of truth for all Amazon orders
- `zoro_orders` — Zoro cost data linked via `wb_order_id`
- `tracking_ids` — all tracking numbers with FedEx delivery status
- `amazon_earnings` — revenue/fees/earnings per order
- `run_log` — audit trail of each automation cycle

## Lifecycle Statuses
- `Pending` → `Shipped` → `AllDelivered` → `Completed`
- Also: `Cancelled`, `Problem`

## Key Patterns
- Gmail auth: OAuth2 with JSON token files, auto-refresh
- Selenium: Chrome remote debugging, anti-detection (remove navigator.webdriver, timezone override)
- Incremental processing: only process new/changed orders each cycle
- Duplicate detection: group by (ASIN + zip + buyer_name) within 3-day window
- FedEx re-check: only if not "Delivered" and last checked > 2 hours ago

## Don'ts
- Never hardcode Windows paths — use macOS paths or `os.path` / `pathlib`
- Never overwrite the SQLite DB — always UPSERT/INSERT incrementally
- Never store credentials in source code — reference token files only
