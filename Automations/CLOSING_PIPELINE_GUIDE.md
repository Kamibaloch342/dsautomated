# Closing Pipeline — Complete Guide & Roadmap

> **SUPERSEDED (26 Sep 2026).** This guide describes `engine/closing_pipeline.py` and a
> `Final Automation/` folder that no longer exist. The live chain is `closing.py` driven by
> `scripts/daily_bulk.py` — see `docs/OPERATIONS_AUDIT_2026-09-26.md` §3.2 for the current
> steps and `AGENTS.md` §3 for the one-page overview. Kept for the table-by-table background.

> This document explains the **"One Car" architecture**: how data flows from order placement → database → enriched closing records.

---

## 🏗️ Big Picture Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA SOURCES                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  Worker A              Gmail Archive            Legacy Data                 │
│  (Order Placement)     (Final Automation/)      (Previous Tracker)          │
│                                                                              │
│  ┌─────────────┐      ┌─────────────────┐      ┌─────────────────┐         │
│  │ order_log   │      │ gmail_archive.db│      │ tracker_test_   │         │
│  │ .txt        │      │ (1.2 GB)        │      │ local.db        │         │
│  └──────┬──────┘      └────────┬────────┘      └─────────────────┘         │
│         │                      │                                            │
│         ▼                      ▼                                            │
│  ┌─────────────────┐  ┌─────────────────┐                                   │
│  │ sync_order_     │  │ sync_gmail_     │                                   │
│  │ placer_logs.py  │  │ archive.py      │                                   │
│  │ (every 5 min)   │  │ (nightly)       │                                   │
│  └────────┬────────┘  └────────┬────────┘                                   │
│           │                    │                                            │
└───────────┼────────────────────┼────────────────────────────────────────────┘
            │                    │
            ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         UNIFIED DATABASE                                     │
│                    data/automations_unified.db                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────┐    ┌─────────────────────────────────────┐        │
│  │ order_placer_orders │    │ archive_* tables                    │        │
│  │ (3,538 rows)        │    │ • archive_earnings   (136,696)      │        │
│  │                     │    │ • archive_zoro       (111,217)      │        │
│  │ Worker A writes     │    │ • archive_tracking   (111,028)      │        │
│  │ via sync script     │    │ • archive_returns     (11,954)      │        │
│  │                     │    │ • archive_messages    (57,042)      │        │
│  └──────────┬──────────┘    └─────────────────┬───────────────────┘        │
│             │                                  │                            │
│             └────────────────┬─────────────────┘                            │
│                              ▼                                              │
│                   ┌─────────────────────┐                                   │
│                   │ closing_pipeline.py │                                   │
│                   │ (Worker B)          │                                   │
│                   └──────────┬──────────┘                                   │
│                              ▼                                              │
│                   ┌─────────────────────┐                                   │
│                   │ closing_orders      │                                   │
│                   │ (44,739 rows)       │                                   │
│                   │                     │                                   │
│                   │ THE SINGLE SOURCE   │                                   │
│                   │ OF TRUTH            │                                   │
│                   └─────────────────────┘                                   │
│                              ▲                                              │
│                              │                                              │
│                   ┌──────────┴──────────┐                                   │
│                   │  FedEx Tracker      │                                   │
│                   │  (delivery status)  │                                   │
│                   └─────────────────────┘                                   │
│                                                                              │
│  Supporting Tables:                                                          │
│  • enrichment_log     (63,927)  — every field change logged                 │
│  • closing_run_log    (1)       — one row per pipeline run                  │
│  • fedex_tracking_cache (1)     — FedEx API responses (6h TTL)              │
│  • raw_api_log        (1)       — every HTTP call logged                    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 📋 Table-by-Table Breakdown

### 1. `order_placer_orders` — Worker A Output
**Source:** `order_log.txt` (written by `engine/orders.py` during checkout)

| Field | Example | Description |
|-------|---------|-------------|
| `amazon_order_id` | 112-3270846-6421036 | Amazon's order ID |
| `wb_order_id` | WB8977496539 | Zoro WB order ID |
| `account` | HOANK | Which seller account |
| `asin` | B0CTD7T8P7 | Product ASIN |
| `sku` | G7646065 | Product SKU |
| `earnings` | 35.95 | Your earnings from Amazon |
| `zoro_order_total` | 36.92 | Total paid to Zoro |
| `ship_city` | ALFORD | Customer city |
| `ship_by_date` | Thu, Jun 4, 2026 | Amazon ship-by deadline |
| `tracking_ids` | 417346880598 | FedEx tracking (added by tracking uploader) |

**How it gets populated:**
1. `engine/orders.py` places order → writes to `order_log.txt`
2. `scripts/sync_order_placer_logs.py` reads log → inserts into this table
3. `engine/tracking.py` finds tracking → updates this table

---

### 2. `archive_*` tables — Gmail Cache
**Source:** `../Final Automation/gmail_archive.db`

These are **read-only mirrors** of Gmail data. They exist so the closing pipeline can query locally instead of hitting the 1.2 GB archive DB over and over.

| Table | Primary Key | What it stores |
|-------|-------------|----------------|
| `archive_earnings` | `amazon_order_id` | "Sold, ship now" emails: earnings, fees, ship_by_date |
| `archive_zoro` | `wb_order_id` | "Has Been Received" emails: cost, tax, SKU, qty |
| `archive_tracking` | auto-increment | "Your Order Has Shipped" emails: tracking IDs |
| `archive_returns` | auto-increment | Return/refund emails |
| `archive_messages` | auto-increment | Buyer-seller messages |

**How it gets populated:**
```bash
python scripts/sync_gmail_archive.py
```
This runs `INSERT OR REPLACE` for all rows from `gmail_archive.db`.

---

### 3. `closing_orders` — Worker B Output (THE FINAL TABLE)
**This is your dashboard table.** Everything flows here.

| Field | Example | Source |
|-------|---------|--------|
| `amazon_order_id` | 112-3270846-6421036 | order_placer_orders |
| `wb_order_id` | WB8977496539 | order_placer_orders |
| `earnings` | 35.95 | order_placer_orders OR archive_earnings |
| `zoro_order_total` | 36.92 | order_placer_orders OR archive_zoro |
| `zoro_tax` | 2.23 | archive_zoro |
| `profit_dollars` | -3.2 | **Calculated:** earnings - zoro_total - zoro_tax |
| `profit_percent` | -8.9 | **Calculated:** (profit / earnings) * 100 |
| `ship_by_date` | Thu, Jun 4, 2026 | order_placer_orders OR archive_earnings |
| `tracking_ids` | 417346880598 | order_placer_orders OR archive_tracking |
| `delivery_status` | (empty) | FedEx tracker |
| `order_label` | Incomplete | **Calculated** (see below) |
| `return_requested` | None | archive_returns OR live Gmail |
| `customer_messaged` | None | archive_messages OR live Gmail |
| `enriched_from_archive` | 1 | Was archive data used? |
| `enriched_from_placer_db` | 1 | Was placer data used? |
| `last_enrichment_run` | 20260607-172502 | Which run last touched this row |

#### Label Logic
```python
if return_requested == "Yes" and refund_done == "Yes":
    label = "Cancelled"
elif earnings and zoro_total and tracking and delivery == "Delivered":
    label = "Completed"
else:
    label = "Incomplete"
```

---

### 4. `enrichment_log` — Audit Trail
Every single field change is logged here.

| Column | Example |
|--------|---------|
| `run_id` | 20260607-172502 |
| `amazon_order_id` | 112-3270846-6421036 |
| `source` | archive_tracking |
| `field` | tracking_ids |
| `old_value` | NULL |
| `new_value` | 417346880598 |
| `confidence` | 0.9 |
| `timestamp` | 2026-06-07 17:25:02 |

---

## 🔄 Complete Order Lifecycle (Step by Step)

### PHASE 1: Order Placement (Worker A)
```
1. Amazon sale comes in
2. engine/orders.py places order on Zoro
3. Writes to order_log.txt:
   [2026-06-05 15:30:00] | WB:WB8977496539 | Profile:HS_RESELLER | Amazon:112-3270846-6421036 | ...
4. Tracking uploader finds tracking in Gmail
5. Updates order_log.txt with TrackingIDs:417346880598
```

### PHASE 2: Sync to Database
```bash
# Every 5 minutes (or on demand)
python scripts/sync_order_placer_logs.py
```
**What happens:**
1. Reads `order_log.txt` line by line
2. Parses each line into key:value pairs
3. Calls `upsert_placer_order(data)`
4. Inserts new order into `order_placer_orders`

Result in DB:
```sql
SELECT * FROM order_placer_orders WHERE amazon_order_id = '112-3270846-6421036';
-- Row exists with earnings=35.95, zoro_order_total=36.92, tracking_ids=417346880598
```

### PHASE 3: Nightly Archive Sync
```bash
# Once per night
python scripts/sync_gmail_archive.py
```
**What happens:**
1. Connects to `../Final Automation/gmail_archive.db`
2. Reads `earnings_emails`, `zoro_emails`, `tracking_emails`, etc.
3. Copies into `archive_earnings`, `archive_zoro`, `archive_tracking`, etc.
4. Uses `INSERT OR REPLACE` (idempotent — safe to re-run)

Result in DB:
```sql
SELECT * FROM archive_tracking WHERE wb_order_id = 'WB8977496539';
-- Row exists with tracking_ids=417346880598
```

### PHASE 4: Closing Pipeline (Worker B)
```bash
python engine/closing_pipeline.py
```
**What happens — order by order:**

#### Step 4a: Find Pending Orders
```sql
SELECT p.* FROM order_placer_orders p
LEFT JOIN closing_orders c ON p.amazon_order_id = c.amazon_order_id
WHERE c.amazon_order_id IS NULL;
```
*Finds orders in placer table but NOT yet in closing table.*

#### Step 4b: Create Base Record
Takes all fields from `order_placer_orders` as the starting point:
```python
closing_data = {
    "amazon_order_id": "112-3270846-6421036",
    "earnings": 35.95,
    "zoro_order_total": 36.92,
    "ship_by_date": "Thu, Jun 4, 2026",
    ...
}
```

#### Step 4c: Enrich from Archives
Queries 5 archive tables for supplemental data:

**archive_earnings:**
```sql
SELECT * FROM archive_earnings WHERE amazon_order_id = '112-3270846-6421036';
-- Result: NULL (no earnings email found for this order in archive)
```

**archive_tracking:**
```sql
SELECT * FROM archive_tracking WHERE wb_order_id = 'WB8977496539' ORDER BY email_timestamp DESC LIMIT 1;
-- Result: tracking_ids=417346880598, email_timestamp=2026-06-05 03:52:58
```

Found tracking! Logs to enrichment_log:
```sql
INSERT INTO enrichment_log (run_id, amazon_order_id, source, field, old_value, new_value, confidence)
VALUES ('20260607-172502', '112-3270846-6421036', 'archive_tracking', 'tracking_ids', NULL, '417346880598', 0.9);
```

#### Step 4d: Coalesce Missing Fields
For each key field, if placer data is empty, use archive data:
```python
if closing_data["tracking_ids"] is None:
    closing_data["tracking_ids"] = archive_data["tracking_ids"]  # 417346880598
```

#### Step 4e: Calculate Profit
```python
profit = earnings - zoro_order_total - zoro_tax
profit = 35.95 - 36.92 - 2.23 = -3.2
margin = (-3.2 / 35.95) * 100 = -8.9%
```

#### Step 4f: Determine Label
```python
# return_requested = None
# refund_done = None
# earnings = 35.95 ✓
# zoro_total = 36.92 ✓
# tracking = "417346880598" ✓
# delivery_status = "" (empty)
# → NOT Delivered → label = "Incomplete"
```

#### Step 4g: Write to closing_orders
```sql
INSERT INTO closing_orders (amazon_order_id, earnings, zoro_order_total, ...)
VALUES ('112-3270846-6421036', 35.95, 36.92, ...)
ON CONFLICT(amazon_order_id) DO UPDATE SET ...;
```

**Result in closing_orders:**
```
order_label: Incomplete
profit_dollars: -3.2
profit_percent: -8.9
enriched_from_archive: 1
enriched_from_placer_db: 1
```

### PHASE 5: FedEx Delivery Check
```bash
python engine/fedex_tracker.py --closing --max-orders 500
```
**What happens:**
1. Finds `closing_orders` with tracking IDs but `delivery_status != 'Delivered'`
2. Extracts Chrome cookies from existing profile
3. Calls FedEx API in batches of 30
4. Updates `delivery_status`, `delivery_date`, `last_scan_location`, etc.
5. **Re-evaluates label:** If status = "Delivered" → flips to "Completed"

### PHASE 6: Gap Fill (Retroactive)
```bash
python scripts/backfill_closing_from_archive.py
```
**What happens:**
Finds existing `closing_orders` rows with missing fields and fills from archive:
```sql
-- Find orders missing ship_by_date
SELECT c.amazon_order_id, e.ship_by_date
FROM closing_orders c
JOIN archive_earnings e ON c.amazon_order_id = e.amazon_order_id
WHERE c.ship_by_date IS NULL;

-- Update them
UPDATE closing_orders SET ship_by_date = e.ship_by_date ...;
```

---

## 📊 Current Data Coverage

```
Total closing_orders: 44,739

ship_by_date:     96.4%  ✅ (archive filled 33,323 gaps)
deliver_by_date:  15.1%  ⚠️  (only source: Amazon SC or recent logs)
ASIN:            100.0%  ✅
SKU:             100.0%  ✅
zoro_total:      100.0%  ✅
earnings:         25.6%  ⚠️  (archive has it, but many legacy orders don't match)
```

---

## 🚀 Daily Operations Checklist

```bash
# 1. Pull new orders from order_log.txt (cron: every 5 min)
python scripts/sync_order_placer_logs.py

# 2. Refresh Gmail archive cache (cron: nightly at 2 AM)
python scripts/sync_gmail_archive.py

# 3. Process new orders through closing pipeline (cron: every 10 min)
python engine/closing_pipeline.py

# 4. Backfill any gaps from archive (cron: nightly after step 2)
python scripts/backfill_closing_from_archive.py

# 5. Check live Gmail for recent returns/messages (cron: every 30 min)
python engine/live_gmail_enrichment.py --days 3

# 6. Check FedEx delivery status (cron: every 2 hours)
python engine/fedex_tracker.py --closing --max-orders 500
```

---

## 🔍 How to Query Your Data

### Find all incomplete orders
```sql
SELECT amazon_order_id, earnings, zoro_order_total, profit_dollars, order_label
FROM closing_orders
WHERE order_label = 'Incomplete';
```

### Find orders missing earnings
```sql
SELECT amazon_order_id, wb_order_id
FROM closing_orders
WHERE earnings IS NULL;
```

### Find profitable completed orders this month
```sql
SELECT amazon_order_id, profit_dollars, profit_percent
FROM closing_orders
WHERE order_label = 'Completed'
  AND profit_dollars > 0
  AND purchase_date LIKE '2026-06-%';
```

### See enrichment history for one order
```sql
SELECT source, field, old_value, new_value, timestamp
FROM enrichment_log
WHERE amazon_order_id = '112-3270846-6421036'
ORDER BY timestamp;
```

### See all pipeline runs
```sql
SELECT run_id, status, orders_processed, orders_enriched, errors, started_at, completed_at
FROM closing_run_log
ORDER BY started_at DESC;
```

---

## ⚠️ Important Notes

1. **Worker A code is NOT modified.** Order placement still writes to `order_log.txt`. The sync script bridges it to the database.

2. **FedEx API works with existing Chrome profiles.** The 403 error only happens with fresh Chrome instances. Use `--port 9221` (or 9222/9223/9224) to connect to your logged-in profiles.

3. **Archive tables are READ-ONLY copies.** Never write directly to `archive_*` tables except via `sync_gmail_archive.py`.

4. **closing_orders is your single source of truth.** Build your dashboard querying this table.

5. **enrichment_log grows fast.** It has 63,927 rows already. Consider purging old entries (>90 days) if DB size becomes an issue.
