# DEPTH AUDIT REPORT V2 — Full Estate Audit

> Date: 2026-06-13
> Scope: `Automations/` (new stack), `Final Automation/` (production closing), `DS_Closing/` (legacy), `TSV Files/`, all three databases
> Method: line-level code reading of all pipeline files + read-only SQL forensics on `automations_unified.db`, `gmail_archive.db`, `combined_reports.db`
> Supersedes: AUDIT_REPORT.md (2026-06-09)

---

## 0. EXECUTIVE SUMMARY

**Your architecture plan (Worker A = order placer with own DB, Worker B = closing that enriches from archive → placer DB → live Gmail → FedEx bulk) is correct, and ~80% of it is already built in `Automations/`.** The problem is not the design — it is that the migrated pipeline has **never successfully produced data**:

- `closing_orders` has **0 rows**. The 7 closing runs on Jun 10 all processed **0 orders** (3 are stuck in `status='running'`).
- The archive mirror tables in the unified DB are **exactly double-loaded**: `archive_tracking` = 222,056 rows = 2 × the source's 111,028 (every single group duplicated); `archive_messages` = 114,084 = 2 × 57,042.
- `order_placer_orders` (Worker A) has only **47 of 3,538 rows with a WB order ID (1.3%)** and 1,605 empty purchase dates — the placement DB is not reliably capturing what it should.
- The live-Gmail fallback exists but is **all-or-nothing** (only fires when archive found *zero* fields), so partially-enriched orders never get their gaps filled.
- Raw logging is a stub: `raw_api_log` has 1 test row, `utils/logger.py` is a no-op.

So: the schema fixes from the June 9 audit landed (lossy sync fixed, `tracking_id_uploaded` + refund columns added and wired), but the pipeline execution layer is broken/untested at any scale beyond 50 orders.

**Verdict in one line:** keep the architecture, fix the execution: dedupe the archive mirror, fix the WB-ID capture in Worker A, make the live-Gmail fallback per-field, run one real report-driven closing for June, then backfill Jan–May from order reports + `gmail_archive.db` (which fully covers Jan–May 2026 — see §3).

---

## 1. YOUR 6 QUESTIONS — VERIFIED ANSWERS (current state, not the old report)

### Q1 — Where does `order_status` come from? And `tracking_id_uploaded`?
- `order_status` comes from the **Amazon FlatFileAllOrdersReport TSV** (`order-status` column), parsed in `engine/closing_runner.py` → `parse_report_tsv()`. When no report row exists, it stays at the DB default `'Unshipped'`. The Jun test runs never downloaded a report, which is why you saw defaults.
- `tracking_id_uploaded`: **you already won this one.** Since the last audit, the column exists in `closing_orders` (plus `tracking_upload_date`, `tracking_carrier_uploaded`), the SC page scraper extracts it (`engine/amazon_scraper.py:422–453` — looks for the confirmed-shipment tracking ID, FedEx/UPS/USPS patterns), and `closing_runner.py:318,328` maps it into the closing record. It has simply never run for real because `closing_orders` is empty.

### Q2 — `title`, `qty`, `item_price`, `shipping_price`, ship city/state/zip
- Confirmed: `qty` maps from `archive_earnings.num_items`; **`title`, `item_price`, `shipping_price` come from the order report TSV**, not the earnings email mapping. `ship_city/state/postal_code` also come from the report. Your read is correct — these resolve automatically once a real report-driven run happens. (Note: `archive_earnings` *does* carry `product_names` and `item_prices` as JSON — a cheap improvement is to also map title/item_price from archive as a fallback when the report row is missing.)

### Q3 — `earnings`, `amazon_fees`, `shipping_amt` empty → should fall back to live Gmail
- Root cause confirmed: the archive ends **Jun 6**; of 3,538 placer orders only **188** have a matching `archive_earnings` row (3,350 missing — they're June orders, archive is stale).
- The fallback you asked for **exists and is wired**: `closing_runner.py:380` calls `live_gmail_per_order.search_order_in_gmail()` across all 5 Gmail accounts. **But** it only triggers when `enriched_from_archive == 0` — if the archive supplied even one field, live Gmail is skipped and earnings stay empty. **Fix: make the fallback per-field** ("if earnings is NULL after archive, search live"), not per-order.

### Q4 — Zoro KPIs only searched in archive, never live Gmail
- Same root cause and same all-or-nothing trigger as Q3. `live_gmail_per_order.py` *does* extract Zoro fields (order total, tax, SKU, coupon) when it fires. Additionally: only **47/3,538** placer rows have a `wb_order_id` — and without a WB ID, the Zoro archive lookup (keyed on `wb_order_id`) can't even start. So Q4 has **two** bugs: the all-or-nothing fallback AND the WB-ID capture failure in Worker A (`orders.py` swallows DB write errors at lines 349–351, and the Zoro-response → WB-ID write path is clearly not landing).

### Q5 — `111-1105746-6925831` says customer never messaged, but an inquiry exists
- Verified at the data level: `gmail_archive.db → message_emails` has **zero rows** for this order (checked order-id, body, and subject columns). The archive *does* capture inbound messages in general (971 `buyer_inquiry` + 575 `buyer_reply` rows exist), so the parser supports it — this specific email was never fetched.
- **Likely root cause found:** the live inquiry check (`engine/post_delivery.py:63`) searches subject `"Order inquiry from Amazon customer"`, but actual inbound inquiries in your archive carry the subject `"Inquiry from Amazon customer <name>"` — no "Order" prefix. Amazon uses both subject lines; the narrow query misses one of them. The archive builder has a similar narrow-query problem.
- **Fix:** search both `subject:"Order inquiry from Amazon customer"` OR `subject:"Inquiry from Amazon customer"` (and ideally `from:marketplace-messages` + the order ID), in both the archive sync and the live check. Your instinct — don't trust the archive for messages, re-query live Gmail at closing time — is right, and 1,546 inbound messages across 3 years vs 57k outbound suggests inbound coverage in the archive is badly incomplete.

### Q6 — `114-0632174-2905805` refund done but showed "No"
- **This is now fixed at the data level.** Both DBs agree: `email_type=refund_initiated`, `refund_done=Yes`, `refund_amount=114.44` (item 74.18 + outbound shipping 40.26, "Seller Issued Refund", Betty Porter, 2026-05-11). The old "No" came from the lossy sync that dropped `refund_amount/refund_done` — that sync has been repaired and re-run.
- Two residues: (a) `refund_date` is empty in both DBs (parser doesn't extract it — minor); (b) `sku` parsed as the literal string `"e-mail"` — a parser artifact worth a guard. And since `closing_orders` is empty, the order-level rollup that the GUI shows still can't reflect this until a real run happens.

---

## 2. DATABASE FORENSICS (the numbers)

### automations_unified.db (1.83 GB)
| Table | Rows | State |
|---|---|---|
| closing_orders | **0** | 78 columns, schema complete, never populated |
| order_placer_orders | 3,538 | placed_at May 31 → Jun 7 only; **47 with wb_order_id**; 2,043 with tracking_ids; 1,605 empty purchase_date |
| archive_earnings | 136,696 | matches source ✓ |
| archive_zoro | 111,217 | matches source ✓ |
| archive_tracking | **222,056** | **= 2 × source (111,028). 100% of groups duplicated; 112,766 surplus rows** |
| archive_messages | **114,084** | **= 2 × source (57,042). One copy has NULL `direction` (two ingest passes)** |
| archive_returns | 11,954 | matches source; 285 dup rows; now carries refund_amount/refund_done ✓ |
| enrichment_log | 65,680 | working; top sources archive_earnings (41,727), archive_messages (15,388) |
| closing_run_log | 9 | 1 real run (Jun 8: 50/50 enriched, 50 FedEx hits ✓); 7 runs Jun 10 processed 0; **3 stuck 'running'** |
| raw_api_log | 1 | test stub only — raw logging not real |
| fedex_tracking_cache | 1 | effectively unused |

**Dedup fix:** delete dupes keeping the row with populated `direction`/lowest id, then add `UNIQUE` indexes (`archive_tracking(wb_order_id, tracking_ids, email_timestamp)`, `archive_messages(amazon_order_id, email_timestamp, message_type)`) and make `sync_gmail_archive.py` use `INSERT OR REPLACE`/`ON CONFLICT` so it can never double-load again.

### gmail_archive.db (1.28 GB) — your single most valuable asset
- earnings_emails 136,696 (2022-06 → 2026-06-06) · zoro_emails 111,217 · tracking_emails 111,028 · return_emails 11,954 · message_emails 57,042 · receipts **0** (table defined, never populated).
- **2026 coverage is complete for Jan–May** (earnings by month: 10,966 / 7,144 / 10,598 / 13,967 / 12,257, June only 210 → archive stale since Jun 6). **This is your backfill source for the missing 5 months.**
- message_emails types: tracking_sent 39,821 · delivery_checkin 8,296 · other_outbound 4,519 · oos_notice 2,268 · **buyer_inquiry 971 · buyer_reply 575 (inbound)** · cancellation_ack 560 · unable_to_fulfill 25 · loss_notice 7.

### combined_reports.db (1.18 GB) — ⚠️ NOT what you may think
- Single table `zoro_data`, 1,995,782 rows = the **Zoro product catalog feed** (SKU, price, lead time, dims) synced from S3. It contains **no orders and no purchase dates**. It **cannot** backfill Jan–May orders. It *is* useful for validating Zoro costs and for listing intelligence (hoank_dashboard uses it).
- The Jan–May backfill therefore needs: **Amazon order reports (download 5 monthly FlatFile reports) + gmail_archive.db enrichment** — exactly your stated plan, and `scripts/backfill_2026.py` already scaffolds it.

---

## 3. PIPELINE AUDIT — what's actually built

### Worker A — order placement (`Automations/engine/orders.py`, 2,829 lines)
Scrape SC orders → order detail (address, dates) → SKU map (Google Sheets, CSV fallback) → Zoro checkout (`checkout/true_api.py` preferred, `dom.py` fallback) → write `order_placer_orders` → JSON/CSV run logs.
**Defects:**
1. **Silent DB-write failure** (`orders.py:349–351` bare `except: pass`) — an order can be placed on Zoro and never recorded. This is almost certainly part of why only 47 rows have WB IDs.
2. `_HAS_UNIFIED_DB` import-failure flag silently disables all DB writes (`orders.py:96–99`).
3. SKU map still depends on Google Sheets at placement time (stall risk) — cache it in SQLite, refresh on a schedule.
4. purchase_date stored as display text ("Wed, Jun 3, 2026"), 1,605 empty — normalize to ISO at write time.

### Worker B — closing (`engine/closing_runner.py` 676 + `closing_pipeline.py` 616 + `report_closing.py` 627)
Report download → parse/filter WB orders → consolidate multi-item → archive enrichment (5 tables, per-field confidence logged to `enrichment_log`) → live-Gmail fallback (all-or-nothing bug) → SC scrape fallback (incl. `tracking_id_uploaded`) → FedEx batch (API, batch=30, browser fallback on 403) → profit + label → `closing_orders`.
**Defects:**
1. Live-Gmail fallback per-order not per-field (§1 Q3/Q4).
2. `report_closing.py` duplicates `closing_runner.py` logic — two divergent copies of the same pipeline; keep one.
3. Multi-item consolidation concatenates SKUs/ASINs into one string while summing prices — store items as JSON or a child table.
4. No transaction per batch; failed orders just increment a counter.
5. Jun 10 runs processed 0 orders and 3 runs never closed out (`status='running'`) — no watchdog/finalizer.
6. SC scraper is optional (None if Chrome port unavailable) → orders silently stay incomplete; and scraping is sequential (~2–3 s/order).

### Post-delivery (`engine/post_delivery.py`, ~500 lines) — **built but NOT wired**
Delivery check-in (6-day rule), review request (12-day rule, **Hathoro-only**), return/refund check all exist — but nothing in `closing_runner.py` calls them; only `tracking.py` (the tracking uploader) triggers them manually. The old stack runs these automatically every cycle. **This is the biggest functional gap vs Final Automation**, alongside:
- "Msg already sent?" Gmail fingerprinting (`unified_tracker.py:3722–3848`, SHIPPED_STRONG/DELIVERED_STRONG phrase lists) — prevents double-messaging; not migrated.
- Stuck-in-transit alerts (past deliver-by + 3 days → proactive message) — not migrated.
- On-Time computation, qty validation vs Verified_SKU_Dataset, maintenance mode (false-positive cleanup passes), FedEx-only mode, dry-run mode — not migrated.

### FedEx
New stack `engine/fedex_api.py` mirrors the proven `fedex_requests.py` approach (Chrome-cookie + public OAuth token, `track/v2/shipments`, batch 30, browser fallback). Fine. Needs: retry/backoff + cookie re-prime between batches, and result caching keyed by tracking ID + day (cache table exists, unused). At 100k historical orders ≈ 3,400 batch calls — run the one-time backfill in throttled chunks over a few days.

### Logging — currently NOT meeting your "raw logs of everything" requirement
- `enrichment_log` ✓ (good design — per-field, source, confidence).
- `raw_api_log` exists but is written by nothing real. `utils/logger.py` is a 6-line stub.
- `message_log.json` / `tracking_upload_log.json` are side-files not tied to `closing_orders`.
**Fix:** route every external interaction (Gmail query + message-id list, FedEx request/response, SC scrape, Zoro checkout response, message send, review request) through `raw_api_log` + an `action_log`, and replace the JSON side-files with DB tables.

---

## 4. LEGACY (`DS_Closing/`, root scripts) — disposition
- `Hypersale_Closing` / `Discounted_Closing`: superseded by Final Automation → archive. Two things worth salvaging before archiving:
  - **Profit/closing aggregates** (daily/monthly summaries, profit-margin banding) from `main_profit_calculator.py` / `yearly_closing.py` — these belong as SQL views over `closing_orders`.
  - **hoank_dashboard.py + Dashboard.html** — the listing-intelligence dashboard (velocity, days-of-stock, activate/deactivate/restock actions) is genuinely good and is a separate product from order closing; keep it, point it at the unified DB later.
- `PPC_SP-analysis.py` is a 0-byte file — delete.
- `check_order_msg.py` — one-off diagnostic, archive.
- `TSV Files/auto_download.py` — keep (catalog sync) but see SECURITY below.

## 5. SECURITY — act on these
1. **`TSV Files/auto_download.py` contains hardcoded AWS keys** (access key + secret, bucket `zoro-preferred-seller-096`) in a git repo with a remote. **Rotate the key and move to env vars / AWS profile.**
2. Gmail OAuth token JSONs (`All_Tokens/…`) are tracked in git (visible in git status history). Add to `.gitignore`, purge from history when convenient.

---

## 6. ARCHITECTURE VERDICT ON YOUR PLAN

| Your plan element | Verdict |
|---|---|
| Worker A (placer) owns its own DB, captures everything it can at placement | ✅ Correct, exists (`order_placer_orders`) — fix WB-ID capture + silent failures |
| Worker B downloads 30-day report, keys on WB merchant orders | ✅ Correct, built (`closing_runner.download_report`) — never run for real yet |
| Enrich from `gmail_archive.db` first | ✅ Built (archive_* mirror) — dedupe it + sync more often than nightly |
| Then check placer DB for ship-by/deliver-by | ✅ Built (coalesce logic in `closing_pipeline.py`) |
| Scrape from scratch only if still missing | ✅ Built (amazon_scraper) — make it mandatory + batched |
| Don't trust archive for messages/returns → re-query live Gmail | ✅ Right instinct, confirmed by Q5 data. Make live fallback per-field and always-live for messages/returns on open orders |
| FedEx bulk after the batch is ready, then messages + reviews | ✅ FedEx bulk built; messages/reviews built but **not wired** — wire post_delivery into the runner |
| Raw logs of everything | ❌ Not yet real — see §3 Logging |
| One "DS Database" with organized tables, drop Google Sheets | ✅ Right call. `automations_unified.db` is that database. Keep Sheets read-only during transition |
| HTML/CSS/JS grid UI instead of Sheets (100k+ rows) | ✅ Right call. Serve `closing_orders` via a small local server (Flask/FastAPI) with **server-side pagination + filters** — do not render 100k rows client-side. `Final Automation/dashboard_server.py` is a starting skeleton; add a "FedEx Tracker" view backed by the same DB rather than a separate tab bolted onto Tkinter |

**One codebase, not two:** your complaint "the codebase is so long for no reason" is validated — `unified_tracker.py` alone is 7,662 lines and `Automations/` re-implements most of it in ~17k lines across clean modules. The path is: finish wiring `Automations/` (post-delivery + fallback fixes), run it in parallel with Final Automation for 1–2 weeks comparing outputs, then freeze Final Automation as read-only legacy. Don't maintain both.

---

## 7. ORDERED ACTION PLAN

**Phase 0 — hygiene (1 day)**
1. Rotate AWS keys; untrack token JSONs.
2. Dedupe `archive_tracking` / `archive_messages`; add UNIQUE indexes; make `sync_gmail_archive.py` idempotent.
3. Fix `orders.py:349–351` (log + retry instead of `pass`); fail loudly on `_HAS_UNIFIED_DB` import failure.
4. Close out the 3 stuck closing runs; add a finalizer (mark run failed on crash).

**Phase 1 — make Worker B real (this week)**
5. Per-field live-Gmail fallback (earnings, zoro, tracking, returns, messages each independently).
6. Broaden inquiry queries to both subject variants; always-live message/return check for open orders.
7. Wire `post_delivery.py` into `closing_runner` (after FedEx step), port the msg-already-sent fingerprint check from `unified_tracker.py` first so you never double-message; enable reviews for both accounts.
8. Delete `report_closing.py` (keep `closing_runner.py` as the single pipeline).
9. Run one full report-driven closing for June; verify `closing_orders` populates; verify Q1/Q2 columns fill (order_status, title, item_price, ship city/state/zip, tracking_id_uploaded).

**Phase 2 — 2026 backfill (next week)**
10. Download Jan–May FlatFile order reports (5 months, ~50–60k WB orders by archive counts).
11. Run `scripts/backfill_2026.py` flow: report rows → archive enrichment (archive fully covers Jan–May) → live Gmail only for the residue → FedEx bulk in throttled chunks (≈2k batches; spread over days) → labels.
12. Sanity-check: refund totals vs `return_emails`, monthly profit vs the old Profit_Analysis_Report numbers.

**Phase 3 — UI + retirement (next 2–4 weeks)**
13. Local web UI over `closing_orders` (server-side paging, column filters, FedEx tracker view, action queue for pending messages/reviews).
14. Port daily/monthly profit summaries as SQL views; point hoank dashboard at the unified DB.
15. Freeze `Final Automation/` (read-only), archive `DS_Closing/`, keep `gmail_archive.db` as the immutable historical archive with a scheduled incremental sync.

---

*Compiled from line-level reads of: Automations/engine/* (orders, closing_runner, closing_pipeline, report_closing, live_gmail_per_order, live_gmail_enrichment, fedex_api, fedex_tracker, tracking, post_delivery, amazon_scraper), platforms/*, utils/unified_db.py, scripts/*; Final Automation/unified_tracker.py, gmail_archive.py, fedex_requests.py, fedex_checker.py, config.py, utils.py, db.py; DS_Closing/* (both closings); TSV Files/auto_download.py; plus SQL forensics on all three databases.*
