# DEPTH AUDIT REPORT — Automations + Final Automation

> Date: 2026-06-09
> Scope: Both order-placement (Automations/) and closing (Final Automation/) stacks
> Database: `Automations/data/automations_unified.db` + `Final Automation/gmail_archive.db`

---

## EXECUTIVE SUMMARY

Your operation has **two independent stacks** that don't share data well:

| Stack | Purpose | Data Store | Records |
|-------|---------|-----------|---------|
| **Automations/** (ZUHRA) | Order placement (Amazon → Zoro) | `order_placer_orders` (3,538), text logs | Real-time, live orders only |
| **Final Automation/** | Closing, tracking, profit calc | `gmail_archive.db` (1.2 GB), Google Sheets | Historical, batch-processed |
| **Unified DB** (NEW) | Attempted merge | `closing_orders` (7 rows) | Just born, mostly empty |

**Critical Finding:** The `archive_returns` table in the unified DB is **lossy** — it drops `refund_amount`, `refund_date`, and `refund_done` that exist in the rich `gmail_archive.db`.

---

## PART 1 — YOUR 6 SPECIFIC ISSUES (ANSWERED)

### Issue 1: `order_status` + Tracking ID Uploaded

**Where does `order_status` come from?**
- In the **test script**, it comes from the **DB default**: `DEFAULT 'Unshipped'`
- In **production** (`closing_pipeline.py`), it would come from the **Amazon FlatFileAllOrdersReport** TSV download
- The test script NEVER sets `order_status` because it doesn't download the report

**Missing: `tracking_id_uploaded`**
- You are 100% correct. The `tracking_ids` column stores what Zoro emailed us, but we never verify what was **actually uploaded to Amazon SC**.
- **Action needed:** Add `tracking_id_uploaded` column to `closing_orders` and scrape it from the Amazon SC order page ("Confirm shipment" section).

### Issue 2: `title`, `qty`, `item_price`, `shipping_price`

**Where do they come from?**
- `closing_pipeline.py` maps from `archive_earnings`:
  - `sku` ← `skus`
  - `asin` ← `asins`
  - `quantity` ← `num_items`
- **But** `title`, `item_price`, `shipping_price` are **NOT mapped** from archive_earnings!
- They come from the **Amazon Order Report TSV** (which the test script doesn't use).
- **Verdict:** Will be auto-populated once you run the full pipeline with report download.

### Issue 3: `earnings`, `amazon_fees`, `shipping_amt` empty

**Root cause:** `archive_earnings` table is missing for recent orders.
- Order `112-8246579-0124267` (Jun 2): **NOT in archive_earnings**
- Order `114-6547048-4310631` (Jun 6): **NOT in archive_earnings**
- Order `111-1105746-6925831` (May 20): **FOUND** ($41.63)

**The fix already exists:** `engine/live_gmail_enrichment.py` has `enrich_earnings_from_live_gmail()` that queries live Gmail for "Sold, ship now" emails.
- **But the test script does NOT call it.**
- **Action:** Integrate live Gmail fallback into the main pipeline.

### Issue 4: All Zoro KPIs missing for some orders

**Root cause:** `archive_zoro` table is missing `WB2135751482` and others.
- Same pattern as earnings — archive is stale for recent orders.

**The fix already exists:** `live_gmail_enrichment.py` has `enrich_zoro_from_live_gmail()` (or similar) but it's not wired into the test script.
- **Action:** Same as #3 — integrate live Gmail fallback.

### Issue 5: `111-1105746-6925831` — customer messaged = NULL but Gmail shows inquiry

**Root cause:** `archive_messages` table has **0 records** for this order.

**Why?** The `archive_messages` table was populated by `build_gmail_archive.py` → `sync_messages()` which searches Gmail for:
```
from:amazon.com subject:(message OR inquiry OR "buyer-seller" OR "order inquiry")
```

But the **parser** in `gmail_archive.py` classifies messages into types like `tracking_sent`, `loss_notice`, `other_outbound`. These are **outbound messages sent BY your system**, not **inbound inquiries FROM customers**.

The customer's inquiry "Inquiry from Amazon customer Janeen" is an **inbound** message. The archive either:
1. Didn't search for it (query too narrow), or
2. Failed to parse it (parser looks for specific patterns)

**The fix already exists:** `live_gmail_enrichment.py` → `enrich_customer_messages()` searches live Gmail for:
```
from:amazon.com subject:message
```
And sets `customer_messaged = "Yes"`.
- **But the test script does NOT call it.**

### Issue 6: `114-0632174-2905805` — refund done = No but Gmail shows "Refund Initiated"

**Root cause:** The `archive_returns` table in unified DB is **lossy**.

**The data flow:**
1. `gmail_archive.py` → `parse_return_email()` extracts **rich data**:
   - `refund_amount = 114.44`
   - `refund_done = "Yes"`
   - `email_type = "refund_initiated"`
   - Stores in `gmail_archive.db` table `return_emails` ✓

2. **Sync to unified DB** drops critical fields:
   - `archive_returns` only has: `id, amazon_order_id, gmail_account, email_timestamp, return_reason, raw_body_snippet, fetched_at`
   - **NO `refund_amount`**
   - **NO `refund_done`**
   - **NO `refund_date`**

3. `closing_pipeline.py` → `enrich_from_archive()` tries to detect refund:
   ```python
   reason = rdict.get("return_reason", "")
   if "refund" in reason.lower():
       enriched["refund_done"] = "Yes"
   ```
   - But `return_reason` is **EMPTY** for refund-initiated emails (the parser couldn't extract it because refund emails don't contain a "return reason" field).

**Result:** `refund_done` stays NULL even though the refund clearly happened.

**Action:** Either:
- (A) Add `refund_amount`, `refund_date`, `refund_done` columns to `archive_returns`
- (B) Query `gmail_archive.db` directly (rich `return_emails` table) instead of the lossy `archive_returns`

---

## PART 2 — ARCHITECTURE AUDIT

### Automations/ Stack (Order Placement)

**Core files:**
| File | Purpose | Lines |
|------|---------|-------|
| `engine/orders.py` | Main order engine — scrapes Amazon SC, places Zoro orders | ~2,828 |
| `checkout/dom.py` | Zoro checkout via DOM manipulation | ~? |
| `checkout/api.py` | Zoro checkout via API calls | ~? |
| `platforms/amazon.py` | Amazon SC scraping helpers | ~? |
| `platforms/zoro.py` | Zoro API (inventory, cart, tokens) | ~? |
| `platforms/gmail.py` | Gmail auth, earnings lookup, duplicate check | ~217 |
| `gui/launcher.py` | Tkinter dashboard with 4 profiles | ~2,393 |
| `utils/unified_db.py` | Unified DB schema + CRUD | ~1,057 |

**Data captured during order placement:**
- `order_placer_orders` table: amazon_order_id, wb_order_id, account, profile, sku, asin, qty, price, earnings, zoro_cost, profit, address, purchase_date, ship_by_date, deliver_by_date, coupon_used, coupon_savings, tracking_id, status, placed_at

**Data NOT captured:**
- ❌ FedEx delivery status
- ❌ Return/refund tracking
- ❌ Customer message history
- ❌ Feedback/reviews
- ❌ Zoro PO number
- ❌ Zoro order status after placement

### Final Automation/ Stack (Closing/Tracking)

**Core files:**
| File | Purpose | Lines |
|------|---------|-------|
| `unified_tracker.py` | Main orchestrator — full pipeline | ~7,663 |
| `gmail_archive.py` | Gmail parser — extracts ALL email types | ~62,230 |
| `build_gmail_archive.py` | One-time bulk + incremental sync | ~62,230 |
| `config.py` | All constants, paths, accounts | ~262 |
| `utils.py` | Gmail auth, Selenium, PDF, HTML | ~24,851 |
| `db.py` | SQLite schema (old single-table) | ~14,690 |
| `fedex_checker.py` | FedEx scraper | ~10,197 |
| `tracking_finder.py` | Tracking ID finder from Gmail | ~5,164 |
| `amazon_earnings.py` | Earnings parser | ~6,896 |

**Data captured in `gmail_archive.db`:**
- `earnings_emails`: 136,696 rows — rich earnings data
- `zoro_emails`: 111,217 rows — rich Zoro order data
- `tracking_emails`: 111,028 rows — rich tracking data
- `return_emails`: 11,954 rows — **rich return/refund data** (refund_amount, refund_reason, etc.)
- `message_emails`: 57,042 rows — message history

### The Gap: Two Databases, No Bridge

```
gmail_archive.db (Final Automation/)
  ├── return_emails      ← rich: refund_amount, refund_reason, refund_done
  ├── message_emails     ← rich: direction, subject, has_attachment
  └── ...

automations_unified.db (Automations/)
  ├── archive_returns    ← lossy: NO refund_amount, NO refund_done
  ├── archive_messages   ← lossy: NO direction, NO subject
  └── closing_orders     ← destination (68 columns)
```

**The sync from `gmail_archive.db` → `automations_unified.db` is LOSSY.**
Critical fields are dropped during sync.

---

## PART 3 — STRATEGIC RECOMMENDATIONS

### Option A: Keep Two Stacks, Build Bridge (Short-term)
- Fix the archive sync to preserve ALL fields
- Add live Gmail fallback to the test pipeline
- Add `tracking_id_uploaded` column + scraper
- Run `live_gmail_enrichment.py` nightly for recent orders

### Option B: Merge Into One Stack (Medium-term)
- Make `Automations/` the single entry point
- `Final Automation/` becomes a "read-only" historical archive
- All new code goes into `Automations/`
- Build a web UI (HTML/CSS/JS) that queries `automations_unified.db`

### Option C: Full Rewrite (Long-term)
- New unified codebase in `Automations/`
- Single SQLite DB (already exists)
- Web dashboard replacing Tkinter + Google Sheets
- Background workers for FedEx tracking, message sending, review requests

### Recommended: Option B with Option A as Phase 1

**Phase 1 (This week):**
1. Fix `archive_returns` schema — add `refund_amount`, `refund_date`, `refund_done`
2. Fix `archive_messages` schema — add `direction`, `subject`
3. Re-sync `gmail_archive.db` → `automations_unified.db` preserving all fields
4. Integrate `live_gmail_enrichment.py` into the main pipeline
5. Add `tracking_id_uploaded` column + SC scraper

**Phase 2 (Next 2 weeks):**
1. Build one-shot backfill script for all 2026 orders
2. Make `closing_pipeline.py` the canonical closing worker
3. Deprecate Google Sheets (keep as read-only backup)

**Phase 3 (Next month):**
1. Build web UI (HTML/CSS/JS) querying SQLite directly
2. Add real-time FedEx tracking tab
3. Add message/review automation tab

---

## PART 4 — DATA POINTS CHECKLIST

### What we SHOULD record (per order)

| Data Point | Source | Currently Captured | In Unified DB |
|-----------|--------|-------------------|---------------|
| Amazon Order ID | Order Report / SC | ✅ | ✅ |
| WB Order ID | Zoro confirmation | ✅ | ✅ |
| Account (HOANK/Hathoro) | Config | ✅ | ✅ |
| Order Status (Shipped/Unshipped) | Order Report | ✅ (from report) | ⚠️ (default only) |
| ASIN | Earnings email / SC | ✅ | ✅ |
| SKU | Earnings email / SC | ✅ | ✅ |
| Title | Earnings email | ✅ | ❌ (not mapped) |
| Qty | Earnings email | ✅ | ✅ |
| Item Price | Earnings email | ✅ | ❌ (not mapped) |
| Shipping Price | Earnings email | ✅ | ❌ (not mapped) |
| Ship City/State/ZIP | Order Report | ✅ | ✅ |
| Purchase Date | Earnings email / SC | ✅ | ✅ |
| Ship By Date | Earnings email / SC | ✅ | ✅ |
| Deliver By Date | SC scrape | ✅ | ✅ |
| Earnings | Earnings email | ✅ | ⚠️ (archive gaps) |
| Amazon Fees | Earnings email | ✅ | ⚠️ (archive gaps) |
| Shipping Amt | Earnings email | ✅ | ⚠️ (archive gaps) |
| Zoro Account Name | Zoro email | ✅ | ✅ |
| Zoro Order Status | Zoro email | ✅ | ✅ |
| Zoro SKU | Zoro email | ✅ | ✅ |
| Zoro Qty | Zoro email | ✅ | ✅ |
| Zoro Order Date | Zoro email | ✅ | ✅ |
| Zoro Tax | Zoro email | ✅ | ✅ |
| Zoro Order Total | Zoro email | ✅ | ✅ |
| Zoro Buyer ZIP | Zoro email | ✅ | ✅ |
| Coupon Used | Zoro email / placer | ✅ | ✅ |
| Coupon Savings | Zoro email | ✅ | ✅ |
| Tracking IDs | Tracking email | ✅ | ✅ |
| Carrier | Tracking email | ✅ | ✅ |
| **Tracking ID Uploaded** | **SC page** | **❌** | **❌ NEEDS ADDING** |
| Delivery Status | FedEx | ✅ | ✅ |
| Delivery Date | FedEx | ✅ | ✅ |
| Left At | FedEx | ✅ | ✅ |
| Signed By | FedEx | ✅ | ✅ |
| Return Requested | Return email | ✅ | ⚠️ (only from archive) |
| **Refund Done** | **Refund email** | **✅ (in gmail_archive.db)** | **❌ (lossy sync)** |
| **Refund Amount** | **Refund email** | **✅ (in gmail_archive.db)** | **❌ (lossy sync)** |
| **Refund Date** | **Refund email** | **✅ (in gmail_archive.db)** | **❌ (lossy sync)** |
| Customer Messaged | Message email | ✅ (outbound only) | ❌ (inbound missing) |
| Delivered Msg Sent | Post-delivery action | ✅ | ✅ |
| Review Requested | Post-delivery action | ✅ | ✅ |
| Feedback Rating | SC page | ✅ | ✅ |
| Feedback Text | SC page | ✅ | ✅ |
| Feedback Date | SC page | ✅ | ✅ |
| Buyer Name | SC page | ✅ | ✅ |
| Buyer Email | SC page | ✅ | ✅ |
| Ship To Address | SC page | ✅ | ✅ |
| Bill To Address | SC page | ✅ | ✅ |
| Payment Method | SC page | ✅ | ✅ |
| Order Subtotal | SC page | ✅ | ✅ |
| Order Total | SC page | ✅ | ✅ |

---

## PART 5 — IMMEDIATE ACTION ITEMS

1. **Fix `archive_returns` schema** — add `refund_amount`, `refund_date`, `refund_done`, `refund_reason`
2. **Fix `archive_messages` schema** — add `direction`, `subject`, `sender`
3. **Re-sync from `gmail_archive.db`** to unified DB preserving all fields
4. **Add `tracking_id_uploaded` column** to `closing_orders`
5. **Add SC scraper** for "tracking ID uploaded to Amazon"
6. **Integrate `live_gmail_enrichment.py`** into the main pipeline as fallback
7. **Fix `customer_messaged`** to detect inbound inquiries, not just outbound tracking
8. **Build one-shot 2026 backfill script** using `unified_tracker.py` report download + archive enrichment

---

*Report compiled from direct code inspection of:*
- `Automations/engine/closing_pipeline.py`
- `Automations/engine/live_gmail_enrichment.py`
- `Automations/engine/orders.py`
- `Automations/platforms/gmail.py`
- `Automations/utils/unified_db.py`
- `Automations/scripts/test_pipeline_orders.py`
- `Final Automation/gmail_archive.py`
- `Final Automation/build_gmail_archive.py`
- `Final Automation/unified_tracker.py`
- Database schema dumps from both SQLite databases
