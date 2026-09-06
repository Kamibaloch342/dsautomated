# RAJAR Pipeline — Test Notes & Process Documentation

## Overview

The RAJAR Pipeline (`rajar_pipeline.py`) scrapes Amazon Seller Central's orders page directly — no report download needed. It processes all orders with the "Rajar" label and places them on Zoro via the HS_RESELLER Chrome profile.

## How It Works

```
Amazon SC Search Page  →  Scrape Order IDs  →  Scrape Each Order Detail
      ↓                                              ↓
  (ASIN, Qty, Address)  ←──────────────────────────┘
      ↓
  Map ASIN → Zoro SKU (Google Sheets)
      ↓
  Gmail Earnings Check ("Sold, ship now")
      ↓
  Duplicate Check (Gmail address search)
      ↓
  Zoro: Inventory → Cart → Address → Checkout → Place Order
      ↓
  Update Amazon merchant-order-id with WB number
      ↓
  Log to JSON + CSV
```

## Usage

```bash
cd Automations/

# Full run — places real orders
python rajar_pipeline.py

# Dry run — scrape + log without placing orders
python rajar_pipeline.py --dry-run
```

## Prerequisites

1. **Chrome running** on port `9224` (HS_RESELLER profile)
   - Must be logged into Zoro.com
   - Must be logged into Amazon Seller Central (or have TOTP creds in `All_Tokens/seller_central_creds.json`)
2. **Gmail tokens** in `All_Tokens/` for: `qurtdis`, `abecomllc26`, `salesmercentile`, `asad_amazon_us`, `salesmercentile1`
3. **SKU sheet** accessible at the Google Sheets URL in `order_config.py`
4. **Label orders "Rajar"** on Amazon SC before running

## Configuration

| Setting | Value | Source |
|---------|-------|--------|
| Chrome port | 9224 | `PROFILES["HS_RESELLER"]` |
| PO Number | HOANK | `order_config.py` |
| Target label | Rajar | `order_config.py` |
| Margin limit | -20% | `RESELLER_MARGIN_LIMIT` |
| Earnings Gmail | qurtdis | `order_config.py` |

## Amazon SC Search URL

```
https://sellercentral.amazon.com/orders-v3/search
  ?fulfillmentType=mfn
  &orderStatus=unshipped
  &page=1
  &q=Rajar       ← the label
  &qt=orderid
```

The scraper paginates through results automatically.

## Order Processing Flow

For each order:

1. **Scrape from Amazon SC** — ASIN, qty, buyer address, merchant-order-id
2. **Skip if already processed** — WB ID, OOS, Loss, etc.
3. **Map ASIN → Zoro SKU** — via Google Sheets CSV
4. **Check earnings** — "Sold, ship now" email in qurtdis Gmail
5. **Check duplicates** — street address search across all Gmail accounts
6. **Check inventory** — Zoro API `GET /v1/products/{sku}`
7. **Add to cart** — API: clear cart → add item
8. **Update address** — API: PUT default shipping address
9. **Checkout** — navigate, verify, click Place Order
10. **Capture WB ID** — from confirmation URL
11. **Update Amazon** — set merchant-order-id = WB number

## Logging

Logs are saved to `Automations/rajar_logs/`:

- `run_YYYY-MM-DD_HHMMSS.json` — structured run log with full details
- `run_YYYY-MM-DD_HHMMSS.csv` — spreadsheet-friendly log

### JSON Log Structure
```json
{
  "started": "2025-01-15_143022",
  "profile": "HS_RESELLER",
  "dry_run": false,
  "orders_scraped": 25,
  "orders_processed": 20,
  "success": 15,
  "failed": 3,
  "skipped": 2,
  "details": [
    {
      "amazon_id": "114-1234567-8901234",
      "asin": "B0XXXXXXXX",
      "sku": "G1234567",
      "qty": 1,
      "buyer": "John Smith",
      "city": "Austin",
      "state": "TX",
      "earnings": 45.99,
      "zoro_price": 32.50,
      "profit": 13.49,
      "status": "SUCCESS",
      "wb_id": "WB7654321098"
    }
  ]
}
```

## Status Codes

| Status | Meaning | Amazon Label |
|--------|---------|--------------|
| SUCCESS | Order placed | WB number |
| OOS | Out of stock | "OOS" |
| STATE_RESTRICTED | Blocked state (HI/AK/PR) | "SR" |
| NET30_RESTRICTED | Net30 account issue | "Net30" |
| LOW_PROFIT | Below margin threshold | "Loss" |
| DUPLICATE | Address already ordered | existing WB |
| PENDING_EARNINGS | No "Sold" email yet | (unchanged) |
| UNMAPPED_ASIN | ASIN not in SKU sheet | (unchanged) |
| ADDR_FAIL | Address scrape failed | (unchanged) |
| CART_FAIL | Zoro cart operation failed | (unchanged) |
| SCRAPE_FAIL | Could not read order page | (unchanged) |

## Known Issues & Edge Cases

### Amazon SC Page Structure
- Order IDs are in format `\d{3}-\d{7}-\d{7}` (3-7-7 digits)
- ASINs are extracted from `/dp/` or `/product/` links on detail pages
- The quantity is extracted from nearby text ("Qty: N")
- Merchant-order-id field requires click Edit → clear → type → Save

### Zoro Side
- Cart must be cleared before each order (API clear + Selenium fallback)
- Address must fit within 35-char line limits (handled by `_fit_address_lines()`)
- Old checkout layout loads in read-only mode when address is set via API first
- `_recover_via_renavigate()` handles stale checkout (cart → re-navigate)

### Common Failures
| Problem | Symptom | Fix |
|---------|---------|-----|
| Amazon login expired | Redirected to signin page | Auto-login handles this via TOTP |
| Zoro token expired | API returns 401 | `ensure_token()` refreshes from localStorage |
| Zoro restricted | 403 or restriction page | `reset_restriction()` with cooldown |
| Stale checkout | Address form open or wrong data | Re-navigate via `_recover_via_renavigate()` |

## Differences from auto_orders.py

| Feature | auto_orders.py | rajar_pipeline.py |
|---------|---------------|-------------------|
| Order source | TSV report download | Direct page scrape |
| Filtering | pandas DataFrame merge | In-page JavaScript |
| Coupons | Full coupon strategy | None (reseller mode) |
| Loop | Infinite cycle (2min sleep) | Single run |
| Multi-item | Dedicated handler | Uses place_order_fast directly |
| Logging | CSV only | JSON + CSV |

## Testing Checklist

- [ ] Dry run: `python rajar_pipeline.py --dry-run`
- [ ] Verify order IDs scraped correctly from search page
- [ ] Verify ASIN extraction from detail pages
- [ ] Verify address scraping matches existing flow
- [ ] Single order live test
- [ ] Multi-order live test
- [ ] Check all log files generated correctly
- [ ] Verify Amazon merchant-order-id updated with WB number
- [ ] Test with unmapped ASIN (should log and skip)
- [ ] Test with already-processed order (should skip)
- [ ] Test with no earnings email (should skip as pending)

## Improvement Ideas

- [ ] Add pagination detection improvement (Amazon's kat-pagination component)
- [ ] Add ASIN auto-lookup on Zoro if not in sheet
- [ ] Add Slack/Telegram notification on completion
- [ ] Add multi-item order support (multiple ASINs per order)
- [ ] Add retry logic for transient API failures
- [ ] Add real-time dashboard (like `print_rich_summary()`)
