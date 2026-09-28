# ZUHRA Operations Audit — 26 September 2026

> **Purpose:** record, with evidence, where the live stack (`Automations/`) spends its time and
> disk, what is duplicated or dead, and what an implementation plan should contain.
> **Scope:** placer → tracking → closing → FedEx → messaging, the DB, and the background jobs.
> Nothing was changed. Every number below was measured on this Mac on 26 Sep 2026 (read-only
> SQLite queries, today's `.bulk_*` / tracking / autosync logs, and the code as of commit `117ed9d`).
> **Guardrails honoured:** Appendix F of `automations_complete_documentation.md` and the owner's
> "reliability over speed" rule for coupons/priority routing. Nothing here proposes weakening
> the placement lock, the fail-closed duplicate check, the Sheets dependency, the coupon flow,
> the label logic or the messaging guards.

---

## 0. Implementation status (evening of 26 Sep 2026)

Everything in the plan that is code was implemented the same day; what remains needs the
owner (credentials, a maintenance window, a folder move). Measured after the change: the
same 10,447-order HOANK report that took ~25 min and rewrote 10,378 rows in the afternoon
run now selects **40 orders with new evidence, skips 10,292 and finishes in 18 s** (dry run).

| Plan item | Status | Where |
|---|---|---|
| 0.1 Skip the dead FedEx browser tier | ✅ | `engine/fedex_api.py` `browser_tier_allowed()`, `scripts/fedex_recheck.py`, `scripts/daily_bulk.py` step 5, `closing.py` |
| 0.2 FedEx Track API key | ⏳ **owner** — paste `client_id` / `client_secret` into `config/accounts.json → fedex_api` (free at developer.fedex.com). A loader bug that dropped that block is fixed; a UPS tier (`ups_api`) was added for `1Z…` numbers | `utils/accounts_config.py`, `engine/fedex_api.py` |
| 0.3 `sync_incremental` instead of `sync_all` | ✅ `sync_smart` (incremental; full once a day) | `scripts/sync_order_placer_logs.py`, `closing.py`, `scripts/daily_bulk.py` |
| 0.4 Enrichment log off by default | ✅ `CLOSING_ENRICH_LOG=1` to enable | `closing.py` |
| 0.5 / 1.3 Scrape exception-only | ✅ Shipped + no upload record anywhere, 7-day retry, `SCRAPE_LIMIT` 400 | `scripts/scrape_sc_fields.py` |
| 0.6 Upload-log flood | ✅ one `no_tracking` line per order per day | `engine/tracking.py` |
| 0.7 Log rotation | ✅ `scripts/rotate_logs.py`, hourly from `auto_sync_10min.py` | |
| 0.8 Backup outside the Drive mirror | ✅ `~/.zuhra/backups/automations_unified.db.bak.20260926_2035` | |
| 1.1 / 1.2 Tracker → DB hand-off | ✅ `record_tracking_upload` → `order_placer_orders`, `closing_orders`, `action_log`; closing + reconcile trust it | `utils/unified_db.py`, `engine/tracking.py`, `closing.py`, `scripts/reconcile_and_reenrich.py` |
| 1.4 Report-diff ingest | ✅ `_select_work` (new / changed / evidence / gap_placer / gap_archive / fedex / gap_retry), unchanged rows not rewritten, rows preloaded once | `closing.py` |
| 1.5 Derived columns instead of raw bodies | ✅ `zoro_promo_savings` / `zoro_promo_label` / `zoro_item_desc` / `zoro_eta` at sync time; sync is incremental; bodies no longer copied | `scripts/sync_gmail_archive.py`, readers in `closing.py`, `reconcile` |
| 1.6 Telemetry | ✅ `closing_run_log` written by both `closing.py` and `daily_bulk.py`; `GET /api/runs` | `dashboard/analytics.py`, `server.py` |
| 2.1 Delivery via official APIs | ✅ code path (FedEx + UPS) — ⏳ needs the keys (0.2) | |
| 2.2 Messaging guards batched | ✅ archive pre-filter + `prefetch_guards` (OR-queries per 20 orders); per-order checks kept for orders with mail | `engine/post_delivery.py`, `scripts/send_delivery_messages.py`, `closing.py` |
| 2.3 "Expected by" on the dashboard | ✅ `expected_by` / `expected_delivered` states (display only) | `dashboard/daybook.py`, `v2.html` |
| 2.4 Zoro order-status spike | ⏳ needs one idle placer Chrome | |
| 3.1 Slim + VACUUM | ⏳ **owner's go** — `python scripts/slim_database.py --apply --keep-enrichment-days=7` with placers stopped (backup goes to `~/.zuhra/backups`) | |
| 3.2 WAL hygiene | ✅ agent checkpoint every 30 min + `journal_size_limit` on every connection | `scripts/cloud_agent.py`, `utils/unified_db.py` |
| 3.3 Incremental archive sync | ✅ | `scripts/sync_gmail_archive.py` |
| 3.4 Cheap fast-sync | ✅ skipped when `order_log.txt` is unchanged | `scripts/sync_order_placer_logs.py` |
| 3.5 Legacy `orders` writes | partial — the tracker now also writes the placer table; the placer's legacy write is left alone deliberately (money path) | `engine/tracking.py` |
| 3.6 Tracker log rewrite | ✅ one rewrite per batch (`_flush_order_log_tracking`) | `engine/tracking.py` |
| 4.1 One sequential bulk for both stores | ✅ launcher button "▶ Run Closing (both)", `run_closing ALL`, scheduler starts one chain — ⏳ enabling `automation.auto_closing` is the owner's call | `gui/launcher.py` |
| 4.2 Tracker cadence | ✅ report reused for 30 min (`TRACKING_REPORT_MAX_AGE_MIN`), live re-sweep of a not-found WB at most every 30 min (`TRACKING_NOTFOUND_TTL_MIN`) | `engine/tracking.py` |
| 4.3 Move out of the Drive mirror | ⏳ **owner** | |
| 4.4 Docs | ✅ `AGENTS.md` rewritten; stale items marked in the complete documentation and the closing guide | |
| **28 Sep — daily report backfill** | ✅ Opens on Last 30 days; Amazon's all-orders report downloaded for 1 Apr – 31 Aug 2026 (both stores) with `scripts/sync_amazon_report.py --download` after fixing closing's exact-date picker (it had been writing the dates into the Order Date / Last Updated radios). Every day Apr–Sep matches Amazon. ⏳ **owner**: 1–~19 Apr predates automated closing — 9,067 placed orders have no closing row (no delivery / profit); run closing for those dates or accept | `closing.py`, `scripts/sync_amazon_report.py`, `dashboard/month_report.py`, `v2.html` |
| **28 Sep — daily report** (owner: "one dashboard, month by month, day by day, matching Amazon") | ✅ The dashboard opens on **Daily report**: pick a month, one row per day (orders / units / sales, cancelled, not placed, the labels, delivery states, late, profit), a ✓ where the day matches Amazon's report, click a day or a number for its orders and the reason each open one is open. Counts come from Amazon's own all-orders report, captured whole into `amazon_report_orders` — closing keeps only WB orders, so 6.5 % of Amazon's orders (cancelled / never placed) were missing from every earlier view. Also fixed: the placer's deliver-by `Thu, Sep 24, 2026\| ZoroDeliverBy:…` was unparsed by the delivery alerts, the day book and messaging (late deliveries showed 77 instead of 470 for September); the messaging queue now alternates reviews (oldest first) with check-ins | `scripts/sync_amazon_report.py`, `utils/unified_db.py`, `dashboard/month_report.py`, `dashboard/server.py`, `v2.html`, `scripts/auto_sync_10min.py`, `scripts/daily_bulk.py`, `scripts/cloud_agent.py` (slim tables), `scripts/send_delivery_messages.py`, `engine/post_delivery.py`, `dashboard/daybook.py` |
| **27 Sep — delivery via Zoro** (supersedes 0.2 / 2.1: no carrier key needed) | ✅ Zoro's guest order tracking is the delivery source: sweep, chain step 5, launcher button, dashboard Attention → Delivery (Zoro cancelled / late / stalled / freight), messaging re-timed (check-in on delivery, review at 6 d, delay notice at 3 d without movement) | `engine/zoro_tracking.py`, `scripts/zoro_delivery_check.py`, `scripts/daily_bulk.py`, `gui/launcher.py`, `dashboard/server.py`, `v2.html`, `engine/post_delivery.py`, `scripts/send_delivery_messages.py` — see `docs/ZORO_TRACKING.md` |

Running processes pick up the new code on their next start (the placers and trackers were
not running during the change; the HOANK bulk that was mid-run spawned the new step
scripts as it reached them).

---

## 1. Executive summary

**Your specific question — "why does closing scrape ship-by / deliver-by again when the placer
already has them?" — has a clear answer: it doesn't, any more.** Since the 7 Sep rebuild
(`120a396`) the placer scrapes `purchase_date`, `ship_by_date`, `deliver_by_date` from the SC
order page (`engine/orders.py:2623-2633`), writes them to `order_placer_orders`
(`engine/orders.py:511-513`) and to `order_log.txt` (`PurchaseDate`/`ShipBy`/`DeliverBy`), and
closing takes them from there first (`closing.py:861-882`, `_PLACER_TRUST`). Coverage in the
last 30 days is **100 %** in the placer table (16,648 / 16,648 rows have all three dates) and
**99.3 %** in `closing_orders`. The Amazon all-orders report has *no* date columns at all
(37 columns; `earliest-ship-date` / `latest-delivery-date` do not exist), so the placer is the
primary source and the design you describe is already in place.

**What is actually slow is a different thing wearing the same label.** The step the launcher
calls "SC scrape (deliver_by/feedback)" loads one Seller Central order page per order
(0.42 orders/s, 8 tabs) — today **3,258 HOANK + 1,443 Hathoro pages ≈ 3 hours** — and its
queue is driven **entirely by a missing `tracking_id_uploaded`** (2,066 of 2,066 queued HOANK
orders; only 6 lack `deliver_by`). That field is known the moment the tracking uploader
confirms a shipment, but the only place it is recorded (`data/tracking_upload_log.json`) is
capped at 5,000 entries and is flooded by ~4,900 "no_tracking" entries every few hours, so
closing cannot see the uploads and re-reads them from Amazon. **Fix the hand-off (tracker →
DB → closing) and this step disappears.**

The ten findings that matter, ranked by impact:

| # | Finding | Evidence (today) | Fix class |
|---|---------|------------------|-----------|
| 1 | **Delivery confirmation is dead → messaging is silent → nothing ever "Completes".** FedEx public tracking is behind Akamai since 19 Sep; official API creds are empty. | 30-day window: 4 delivered of 17,240 rows; 0 delivery messages, 0 review requests sent; 16,791 rows labelled Incomplete | Register FedEx Track API (already wired), skip the dead browser tier |
| 2 | **Closing re-processes the whole 30-day window every run** because "resume" skips only terminal labels and nothing can become terminal (see #1). | HOANK: 10,447 report orders, 69 skipped, **10,378 enriched**, Completed=0. Hathoro: 5,861 enriched | Report-diff ingest (new / changed / new-evidence only) |
| 3 | **SC page scrape exists only to learn `tracking_id_uploaded` (+ feedback that almost never exists).** | Queue driver: 2,066/2,066 missing upload id, 6 missing deliver_by; feedback present on 14 of 32,419 Aug–Sep orders; ≈3 h of page loads | Tracker writes upload facts to the DB; scrape becomes exception-only |
| 4 | **`enrichment_log` is a write-only table growing ~380k rows per run day.** No code reads it. Each row is its own SQLite connection open/close. | 2,760,010 rows; +380,003 on 26 Sep, +340,938 on 19 Sep; `get_enrichment_history` has no caller | Default off, purge, VACUUM |
| 5 | **The DB is 5.4 GB + a 4.0 GB WAL; 62 % of the file is free pages; no checkpoint anywhere.** | page_count 1,375,907, freelist 848,328; no `wal_checkpoint` call in the codebase | Slim + VACUUM (script exists), WAL hygiene in the agent |
| 6 | **The placer log (60,425 lines / 56 MB) is fully re-upserted four times per bulk run** (closing.py + daily_bulk, per account). | "Placer log synced: 60158 updated, 0 new"; 57,483 placer rows touched today | `sync_incremental` (exists) instead of `sync_all` |
| 7 | **The Gmail archive is fully re-copied into the unified DB every hour** (216k rows incl. raw email bodies) though the fetch itself found 0–1 new emails. | autosync log: 216,048 rows/hour; fetch "0 new" | Incremental sync by `fetched_at` |
| 8 | **The tracking uploader downloads a fresh 30-day report every ~10 min and re-sweeps the same ~131 "no tracking" WBs across 8 inboxes every cycle** for 4 uploads. | Cycles 11:39, 11:49, 11:59, 12:10; 131–133 not-found each cycle; 4 successful/cycle | Report every 30 min; re-sweep only on new evidence |
| 9 | **Messaging costs ~25–50 Gmail calls + 2 page loads (~30–40 s) per order** and ignores the archive tables that already hold returns/inquiries. | `post_delivery.process_single_order` — 5 tokens × (inquiry + 2 return/refund + already-sent + cancellation) searches | Batch/OR queries, archive pre-filter, one live confirm before Send |
| 10 | **3.3 GB of console/raw logs with no rotation; launcher re-parses the whole 56 MB order log every 60 s.** | `console_log.txt` 1.24 GB, `raw_logs.txt` 2.06 GB; `_tick_fast_sync` → `sync_incremental` every minute | Rotate; tail-offset parsing |

Net effect of the plan in §5: a bulk run drops from ~4–5 h (today) to well under an hour, the
DB from 9.4 GB (file + WAL) to a few hundred MB, and — the part that matters commercially —
delivery check-ins, review requests and "Completed" labels come back.

---

## 2. What runs today (process inventory)

| Process | Started by | Cadence | Chrome | What it costs |
|---|---|---|---|---|
| 4 placers (`engine/orders.py`) | launcher | cycle ≈ 75–90 s (10 s sleep) | 9221–9224 headless | ~10–12 s per routing decision; SC scrape + Gmail earnings + dup check per order |
| 2 tracking uploaders (`engine/tracking.py`) | launcher | ≈ every 10 min per account | 9331 / 9333 (shared with closing) | 30-day report download + Gmail sweeps + uploads + shipped messages |
| Closing chain (`scripts/daily_bulk.py`) | launcher ▶ Run Closing (per account, run in parallel today at 18:02) | manual (auto-closing disabled) | 9331/9333 + FedEx 9341–9353 | see §3.2 |
| Hourly autosync (`scripts/auto_sync_10min.py`) | launcher `_tick_autosync` | 60 min (docstring says 10) | – | Gmail fetch (fast) + **full archive re-copy** + placer sync + WhatsApp insights |
| Fast sync | launcher `_tick_fast_sync` | 60 s | – | parses whole `order_log.txt` + `SELECT *` of 57k placer rows |
| Zoro watch | launcher `_tick_watch` | 60 min | 9225 | idle ("nothing to check") |
| Cloud agent (`scripts/cloud_agent.py --daemon`) | LaunchAgent | 15 s heartbeat, 5 min slim export, 60 min backup (skipped: DB > 600 MB) | – | reads several full tables from the 5.4 GB DB every 5 min |
| Dashboard (`dashboard/server.py :8000`) | LaunchAgent | on request | – | direct reads of the master DB |
| Hunter (`Amazon_DS_Hunting/hunter.py`) | LaunchAgent | hourly | own | out of scope here |

Two databases: `data/automations_unified.db` (master, WAL) and `data/gmail_archive.db` (718 MB,
the email archive — fine). Plus `dashboard_slim.db` (165 MB, derived).

---

## 3. Findings in detail

### 3.1 Placer → closing hand-off (your question)

**Verdict: already implemented for the dates; the remaining hand-off gaps are `tracking_id_uploaded`,
`feedback` and `delivery_status`.**

* Placer extraction: `engine/orders.py:2623-2633` (JS on the SC order page), carried through
  `process_order` (`:3475-3506`), persisted at `:4523-4525` / `:4656-4658` and written to two
  tables in `_upsert_unified_db` (`:430-538`, `purchase_date/ship_by_date/deliver_by_date` at
  `:511-513`). `order_log.txt` carries `PurchaseDate | ShipBy | DeliverBy` (parsed by
  `scripts/sync_order_placer_logs.py:147-149`).
* Closing consumption: `closing.py:1024-1028` gap-fills from `placer_lookup()` before anything
  else; `_PLACER_TRUST` (`:863-870`) includes the three dates and `zoro_deliver_by`.
* The report gives nothing here: `closing.py:316-317` reads `earliest-ship-date` /
  `latest-delivery-date`, which the FlatFileAllOrdersReport does not contain (header checked:
  `data/reports/HOANK/147340020722.txt`, 37 columns).
* Measured coverage (last 30 days): `order_placer_orders` 16,648/16,648 for all three dates;
  `closing_orders` purchase 16,544/16,544, ship_by 16,478, deliver_by 16,427 (99.3 %). The
  0.7 % gap is orders placed by hand / before 7 Sep.
* Docs drift: `automations_complete_documentation.md` §13 item 8 ("dates not extracted during
  placement") and `AGENTS.md` §2–4 (describes the deleted `Final Automation/` stack) are stale
  and are what makes this look unsolved.

### 3.2 The closing chain, step by step (today's HOANK run, started 18:02:37)

| Step (`scripts/daily_bulk.py`) | Measured | Waste |
|---|---|---|
| 1 Gmail archive fetch (`build_gmail_archive.py --days 2`) | 18:03:54 → 18:04:32, **0 new** emails (1 message) | fine |
| 1 Sync archives → unified (`sync_gmail_archive.py`) | ≈ 2 min, **216,048 rows** `INSERT OR REPLACE/IGNORE` incl. `raw_body_snippet` | full re-copy for 1 new row (also runs hourly) |
| 1b Closing ingest (`closing.py`) | 18:06:18 → ≈18:31. `sync_all` placer log: "60158 updated, 0 new" (≈ 5–8 min under contention with the Hathoro run); Chrome + report ≈ 3 min (file at 18:17); enrichment of **10,378 orders ≈ 14 min** (≈ 12/s); result `Completed=0 Loss=0 Incomplete=10332 Cancelled=7` | resume skipped 69 of 10,447; ~36 `enrichment_log` inserts per order (each its own connection); every order row rewritten; FedEx/scrape/messages off by design here |
| 2 Placer-log sync (`sync_all` again) | second full 60k-line upsert | duplicate of 1b's own sync |
| 3 Reconcile (`reconcile_and_reenrich.py`) | loads all archives + all ~40k account rows into memory, gap-fills, recomputes | overlaps 1b's archive pass almost entirely |
| 4 SC scrape (`scrape_sc_fields.py`) | queue **3,258** (Hathoro 1,443), 0.42/s, 8 tabs, 14 s wait per round → **≈ 130 min** (Hathoro ≈ 57 min) | driven by missing `tracking_id_uploaded`, see §3.3 |
| 5 FedEx re-check (`fedex_recheck.py`) | Hathoro: 6,723 Incomplete-with-tracking; 3 headless Chromes launched; blocked after 90 lookups; exit 2 | dead since 19 Sep; still launches 3 browsers/account |
| 5b Gmail gap-fill | Hathoro: 236 Shipped orders with gaps, live search across 16 inboxes | legitimate |
| 6 Reconcile again | same as 3 | overlap |
| 7 Messaging (`send_delivery_messages.py`) | needs `delivery_status = Delivered` → 4 candidates in 30 days | silent because of §3.4 |
| 8 Coverage report | SQL only | fine |

Two runs (HOANK + Hathoro) were started together and contended for the DB write lock during
the two `sync_all` passes and the persist batches (WAL + 60 s busy_timeout keeps them alive,
but each waits for the other).

**Why resume never shrinks the work:** `closing.py:1838-1850` skips only
`Completed/Refunded/Cancelled/Loss`. In the bulk, closing runs with `CLOSING_FEDEX=0`, so no
order can turn `Completed` there; the only path to `Completed` is `fedex_recheck`, which is
blocked. Result: the same ~10k orders are re-enriched every run, and each re-enrichment rewrites
the row and appends ~36 audit rows.

**What actually changed today:** 3,262 HOANK + 1,444 Hathoro rows were genuinely *new* to
`closing_orders` (created today — the backlog since the previous closing run). Everything
else was a no-op rewrite. A report-diff ingest (new ids, changed `order_status`, or new
evidence since `last_enrichment_run`) is the structural fix; `reconcile_and_reenrich.py`
already covers "late-arriving archive data" for old rows, so closing does not need to.

### 3.3 The SC scrape: what really drives it

`scripts/scrape_sc_fields.py:231-242` queues every order of the previous+current month with
`enriched_from_scrape = 0` and (`deliver_by_date` empty **OR** `tracking_id_uploaded` empty).

* HOANK queue right now: 2,066 orders — **2,066 missing `tracking_id_uploaded`, 6 missing
  `deliver_by_date`.** Composition: 1,995 Shipped (1,983 of them already have Zoro tracking in
  the DB), 13 Pending (cannot have an upload yet), 2 Cancelled.
* The source closing uses instead of the page is `data/tracking_upload_log.json`
  (`closing.py:989-1007`, `reconcile_and_reenrich.py:57-66`). That file is capped at the last
  5,000 entries (`engine/tracking.py:728-729`) and the uploader appends a `no_tracking` entry
  **for every unresolved WB on every cycle** (`:1975-1983`). Today the file holds 5,000 entries
  all from 09:06–12:22: 4,909 `no_tracking`, 88 `success`, 3 `failed`. So closing can see
  ~90 uploads and must scrape the rest.
* Scraped-too-early: an order scraped while still Pending gets `enriched_from_scrape = 1` and
  is never re-queued, so its `tracking_id_uploaded` stays empty after it ships — **6,061**
  Shipped Aug–Sep rows are in that state.
* The other output of the scrape, seller feedback, exists on **14 of 32,419** Aug–Sep orders.
* The uploader already knows everything the scrape looks for at the moment of upload
  (`engine/tracking.py:2193-2202`), and even loads the order page twice per upload (pre-check
  `:2017`, upload `:2077`). It writes the tracking to `order_log.txt` (rewriting the whole 56 MB
  file per WB, `:1458-1502`) and to the legacy `orders` table, but never to
  `order_placer_orders.tracking_id_uploaded` / `closing_orders`.

### 3.4 Delivery confirmation → messaging → labels (the dead chain)

* FedEx public endpoints answer automation with Akamai's decoy page since 19 Sep
  (`engine/fedex_api.py:446-529`, `BLOCKED_PAGE_MARKERS`). Today: every worker stopped after two
  blocked chunks; 0 of 90 resolved. Official REST tier (`:221-347`) is fully implemented and
  selected automatically when `config/accounts.json → fedex_api.client_id/secret` are set —
  **both are empty.** UPS ids (`1Z…`) are uploaded too but never tracked.
* Consequences in the 30-day window: `delivery_status` set on **4** rows; `delivered_msg_sent
  = Yes` **0**; `review_requested = Yes` **0**; labels HOANK Incomplete 10,713 / Refunded 92 /
  Cancelled 86 / Loss 67 / Completed 2; Hathoro Incomplete 6,078 / Refunded 132 / Cancelled 50
  / Loss 20. The Zoro ETA replacement (`zoro_deliver_by`, `engine/closing_signals.py:112-147`)
  covers 67 % (HOANK) / 62 % (Hathoro) of shipped orders and is, correctly, never used as
  proof of delivery.
* The messaging pass itself (`engine/post_delivery.py:733-843`) does per order, against 5
  Gmail tokens: inquiry search (+ a `messages.get` per hit), return + refund searches,
  "already sent" search (+ up to 5 body fetches), cancellation search, then two page loads
  with 3–5 s and 3–6 s random sleeps. ≈ 25–50 API calls and ≈ 30–40 s per order; at
  `MSG_LIMIT=300` that is 2–3 h per account per run — while `archive_returns` /
  `archive_messages` (synced hourly) and the DB flags already answer most of those questions
  for free. The one live check that Rule 5 requires (already-sent re-check) can stay and be
  batched.

### 3.5 Database and storage

| Item | Size | Note |
|---|---|---|
| `automations_unified.db` | 5,374.6 MB | 1,375,907 pages, **848,328 on the freelist (61.6 % free)** |
| `automations_unified.db-wal` | 3,963.9 MB | WAL never truncates: no `wal_checkpoint` in the codebase, constant readers (dashboard, agent every 5 min, launcher every 60 s) plus huge write bursts |
| `enrichment_log` | 2,760,010 rows + 3 indexes | write-only; +380,003 rows today; `unified_db.py:1034-1052` opens a connection per row when called without `conn` (closing does) |
| `archive_*` with `raw_body_snippet` | ~500 MB (Sep 5 figure; more now) | re-copied hourly; bodies also live in `gmail_archive.db` |
| `console_log.txt` / `raw_logs.txt` | 1,237 MB / 2,061 MB | no rotation (doc Phase 4 never done) |
| `.autosync_output.log` | 24 MB | appended hourly, includes 216k "rows synced" lines |
| legacy `orders` table | 45,946 rows + `audit_log` (+9,371 rows / 7 days) | still written by the placer (`_upsert_unified_db` → `insert_order/update_order`, one audit row per changed field) and by the tracker; read only by `insights_engine.py:121`, `tracking.py:1445` and the superseded `closing_pipeline.py` |
| `closing_run_log` | 0 rows | the telemetry table is never written — no step durations exist anywhere (this audit had to infer them from file mtimes) |
| Disk | 44 GB free of 228 GB | DB + WAL + logs ≈ 13 GB and growing ~1 GB/week |

`scripts/slim_database.py` already implements backup → drop `raw_body_snippet` → purge
`enrichment_log`/`audit_log` → VACUUM (dry-run by default); it has not been applied. The agent's
hourly master backup is skipped because the DB exceeds `backup_max_mb` (600) — **there is no
recent backup of the master DB** (the newest `.backup_repair` is from 8 Sep), while the whole
folder sits inside a two-way Google Drive mirror (see §3.8).

### 3.6 Background jobs doing repeated full work

* `_tick_autosync` (launcher, hourly): `sync_gmail_archive.main()` copies **all** 216,048
  archive rows into the unified DB every hour (61,360 earnings, 39,607 zoro, 61,152 tracking,
  3,367 returns, 50,562 messages) although the fetch found 0–1 new emails. `sync_placer_to_closing`
  then loads every placer row and every closing id to gap-fill four address columns.
* `_tick_fast_sync` (launcher, every 60 s): `sync_incremental` reads the entire 60,425-line
  `order_log.txt` and `SELECT * FROM order_placer_orders` (57k rows) each minute, to discover the
  handful of new lines.
* `closing.py` and `daily_bulk.py` both call `sync_all` (full 60k upsert) — per account, so
  four times per bulk. `upsert_placer_order` uses `ON CONFLICT DO UPDATE`, which rewrites the row
  (and its WAL pages) even when nothing changed; only the sync-journal trigger has a change guard.
* The cloud agent's `_db_fingerprint` runs `COUNT(*), MAX(col)` on eight tables every 5 min and
  `export_slim_db` streams 22 tables when anything changed — reasonable, but it is one of the
  permanent readers that keeps the WAL from truncating.

### 3.7 The tracking uploader

* Each cycle (≈ every 10 min per account, `--sleep-seconds 70` + work) requests and downloads a
  **30-day** all-orders report (`engine/tracking.py:1567-1612`, request → 30 s polls → download):
  the report generation is the dominant cost of a cycle.
* The same ~131–148 unresolved WBs are swept again every cycle: archive tiers resolve the
  known ones instantly (`:1192-1249`), but the leftovers still trigger the batched Gmail sweep
  (`:1257-1330`) across 8 inboxes for every batch of 10 — ≈ 100+ `messages.list` calls per
  cycle, per account, to re-learn "still not shipped". Yield: 4 uploads per cycle.
* Every unresolved WB appends a `no_tracking` entry to `tracking_upload_log.json` (5,000-entry
  cap) — the flood that blinds closing (§3.3).
* Every found WB rewrites the whole 56 MB `order_log.txt` in place (`:1458-1502`) and calls
  `update_order` on the legacy table with an audit row.
* Positive: the "already shipped" pre-check (`:2012-2031`), the 14-day skip list, note + upload on
  one page load (`:2076-2083`), CDP text insertion and multi-package handling are all in place —
  the doc's "Phase 5 / two tabs" item is done.

### 3.8 Risks that are not performance

* **Google Drive mirrors `~/Downloads`** (memory: it deleted `.venv` on 17 Sep). A WAL-mode SQLite
  master DB, Chrome profiles, lock files and `.git` inside a two-way mirror is a corruption and
  duplicate-order risk, not just an inconvenience.
* **No usable master backup** (§3.5) while placers write real orders.
* **`daily_bulk` per account in parallel** shares one DB and contends; closing and messaging
  share one Chrome per account (already documented as the 19 Sep tracker halt).
* **Stale docs** (`AGENTS.md`, `CLOSING_PIPELINE_GUIDE.md`, doc §13) describe deleted or
  superseded code; an agent following them will "fix" solved problems.

### 3.9 Placer notes (kept short — it works, and the owner's rule is reliability first)

* Per-order cost is dominated by deliberate live checks (priority-coupon probing ≈ 10–12 s per
  decision in today's log; API checkout ≈ 21 s median, attributed page checkout ≈ 48 s). Not a
  target for shortcuts.
* Structural waste only: each placement is written to **five sinks** — `order_log.txt`, a CSV,
  the JSON run log (full `json.dump` of the whole run on every order, `engine/orders.py:383-386`),
  the legacy `orders` table (+ audit rows), and `order_placer_orders`. Two of those (`orders`,
  the JSON run log) have no consumer that could not read the others.
* The placer loads the SKU sheet, priority columns and coupons from Google Sheets every cycle
  — required by Rule 2/26, keep.

---

## 4. Answers to the three things you named

**Closing.** The dates are already borrowed from the placer. Closing is slow because (a) it
re-enriches ~16k rows per run instead of the ~5k new / few hundred changed ones, (b) it writes
~600k rows of audit and ~16k rewritten rows per run into a 9 GB WAL-mode file, (c) it re-syncs
a 60k-line log four times, and (d) the "scrape" step spends ~3 h reading a fact
(`tracking_id_uploaded`) the tracker already had.

**Messaging.** Not slow — silent. No order becomes Delivered, so `send_delivery_messages`
finds 4 candidates. When delivery returns, the per-order Gmail fan-out (≈ 30–40 s) will make a
300-order backlog take hours; the fix is to answer the guard questions from the archive tables
first and batch the mandatory live confirmation.

**FedEx.** The browser tiers are dead (Akamai). The official Track API tier is implemented and
idle for want of two credential strings; until they exist the chain should skip the step
instead of launching three headless Chromes per account. `zoro_deliver_by` is the right
dashboard signal for "expected by", never for messaging.

---

## 5. Implementation plan

Effort: S = hours, M = 1–2 days, L = several days. Impact is on bulk-run time / DB / business
outcome. Items are ordered so each phase is safe on its own and no later item depends on an
unfinished earlier one unless stated.

### Phase 0 — stop the bleeding (all S, config-level or a few lines; no behaviour change for placers)

| # | Change | Where | Impact |
|---|---|---|---|
| 0.1 | Skip the FedEx step when `fedex_api` creds are empty **or** the previous run exited 2 within N hours; never launch the browser tier automatically again | `scripts/daily_bulk.py:219-228`, `scripts/fedex_recheck.py` | −6 Chromes, −10–20 min per bulk, no more Akamai deepening |
| 0.2 | Register a FedEx Developer account and put `client_id/client_secret` in `config/accounts.json → fedex_api` (owner action; free Track API). Add UPS Track for `1Z` ids the same way later | config only | delivery confirmation returns → messaging + Completed labels return (**highest business value item in this plan**) |
| 0.3 | `closing.py:1793-1799` and `daily_bulk.py:198-199`: call `sync_incremental` instead of `sync_all` (keep `sync_all` behind an env flag for repairs) | 3 lines | −240k row rewrites per bulk, −5–10 min, less WAL |
| 0.4 | `log_enrichment` off by default (`CLOSING_ENRICH_LOG=1` to enable); pass `conn=` when enabled | `closing.py:747-858`, `utils/unified_db.py:1034` | −380k rows/run, −360k connection opens/run |
| 0.5 | Scrape queue: only `order_status='Shipped'`, exclude Pending/Cancelled, and don't stamp `enriched_from_scrape=1` on a page that showed no shipment yet (re-queue later) | `scripts/scrape_sc_fields.py:231-242, 337-345` | removes the 6,061 "scraped too early" trap; smaller queue immediately |
| 0.6 | Stop appending `no_tracking` to the upload log every cycle (log once per WB per day, or keep only success/failed/already_shipped in the capped file) | `engine/tracking.py:1975-1983` | closing sees real uploads again → scrape queue shrinks by itself |
| 0.7 | `scripts/rotate_logs.py`: rotate `console_log.txt`, `raw_logs.txt`, `.autosync_output.log`, `.bulk_*` to 15 days; run from the agent daily | new script | −3.3 GB, stops growth |
| 0.8 | Take one consistent master backup now (`sqlite3 .backup` / agent `backup_now` after raising `backup_max_mb` temporarily) and store it outside the Drive-mirrored folder | ops | safety before Phase 3 |

### Phase 1 — finish the hand-off you asked for (M)

| # | Change | Where | Impact |
|---|---|---|---|
| 1.1 | Tracker writes `tracking_id_uploaded`, `tracking_upload_date`, `tracking_carrier_uploaded` (+ `action_log` row) into `order_placer_orders` and `closing_orders` (if the row exists) right after a successful/already-shipped confirmation; also append `TrackingUploaded:` to the `order_log.txt` line (Rule 6 allows appended keys) | `engine/tracking.py:2193-2202`, `utils/unified_db.py` | closing/reconcile get the fact for free |
| 1.2 | Closing/reconcile trust those columns from the placer table (`_PLACER_TRUST` + `reconcile_and_reenrich.py:143-148`); `_upload_log_lookup` becomes a fallback only | `closing.py:863-870, 989-1007` | — |
| 1.3 | SC scrape becomes exception-only: orders shipped ≥ 2 days with no upload record, plus an optional weekly feedback pass (14 hits in 32k orders does not justify a daily page load per order) | `scripts/daily_bulk.py:206-213`, `scrape_sc_fields.py` | ≈ 3 h → minutes per bulk |
| 1.4 | Report-diff ingest in `closing.py`: process an order only if it is new to `closing_orders`, its report `order_status`/`merchant_order_id` changed, it is non-terminal **and** new evidence exists since `last_enrichment_run` (archive `fetched_at`, placer `synced_at`, upload date), or `CLOSING_FORCE=1`. Keep writing all report-derived columns for changed rows. Persist only changed columns | `closing.py:1836-1882, 1935-2018`, `upsert_closing_order` | 10,378 → a few hundred orders per run; rewrite storm gone |
| 1.5 | Extract at archive-build time the three things closing still parses from `raw_body_snippet` (`coupon_savings`/label, `zoro_deliver_by`, `zoro_item_desc`) into columns of `gmail_archive.db` and the `archive_*` mirrors; then closing/reconcile stop touching bodies | `gmail_fetch/gmail_archive.py`, `engine/closing_signals.py`, `closing.py:796-825`, `reconcile_and_reenrich.py:121-133` | unblocks dropping bodies from the unified DB (Phase 3) |
| 1.6 | Write per-step durations and counts to `closing_run_log` from `daily_bulk.run()` and show them on the dashboard Health tab | `scripts/daily_bulk.py:125-140`, `dashboard/ops.py` | bottlenecks visible without an audit |

### Phase 2 — delivery signal and messaging revival (M; 2.1 depends on 0.2)

| # | Change | Where | Impact |
|---|---|---|---|
| 2.1 | `fedex_recheck` over the official REST tier only (no Chrome): batch 30, all Incomplete-with-tracking of the last 60 days, then the 6–30-day messaging window drains at `MSG_LIMIT` per run. Add UPS for `1Z` ids | `engine/fedex_api.py:246-347`, `scripts/fedex_recheck.py` | Completed labels + messaging backlog resolves over a few days |
| 2.2 | Messaging pre-filter from the DB: skip orders whose `archive_returns` / `archive_messages` / `message_log.json` / DB flags already answer return-refund-inquiry-sent; batch the remaining live checks with OR-queries (as `_prefetch_batch_tracking` does) per 20 orders; keep exactly one live "already sent?" confirmation immediately before Send (Rule 5) | `engine/post_delivery.py:733-843`, `scripts/send_delivery_messages.py:57-92` | ≈ 30–40 s → ≈ 10 s per order; Gmail quota risk gone |
| 2.3 | Dashboard: "expected delivered" state from `zoro_deliver_by` + `shipped_email_time` (display only; never feeds messaging or labels) | `dashboard/daybook.py`, `v2.html` | visibility while 2.1 catches up |
| 2.4 | Spike (optional, needs one idle placer Chrome): does Zoro's authenticated account expose per-order shipment status? If yes it is a second delivery signal that does not depend on carriers | `platforms/zoro.py` | decision input only |

### Phase 3 — database and background load (M; do 3.1 with placers stopped, after 0.8 and 1.5)

| # | Change | Where | Impact |
|---|---|---|---|
| 3.1 | `scripts/slim_database.py --apply` (backup → drop `raw_body_snippet` → purge `enrichment_log` (all, or > 7 days) and `audit_log` > 60 d → VACUUM). Then set `wal_autocheckpoint` sensibly and `PRAGMA journal_size_limit` on `get_conn` | script exists; `utils/unified_db.py:486-506` | 9.4 GB → a few hundred MB; backups possible again (< 600 MB) |
| 3.2 | Agent: `PRAGMA wal_checkpoint(TRUNCATE)` every 30 min when no `closing_*`/`daily_bulk_*` lock is held; log WAL size in the heartbeat | `scripts/cloud_agent.py` | WAL stays small; readers unaffected |
| 3.3 | `sync_gmail_archive.py`: incremental by `fetched_at` watermark per table (keep `--full` for rebuilds) | `scripts/sync_gmail_archive.py:88-155` | 216k rows/hour → tens |
| 3.4 | `sync_incremental`: remember the `order_log.txt` byte offset and parse only appended lines; skip the `SELECT *` when nothing new | `scripts/sync_order_placer_logs.py:219-294` | launcher no longer parses 56 MB every minute |
| 3.5 | Retire the legacy `orders` table writes: point `insights_engine.py:121`, `tracking.py:1445` at `order_placer_orders`, stop `insert_order/update_order` (+ `audit_log`) from the placer and tracker; drop the per-order full JSON `_save_log()` dump (keep the CSV) | `engine/orders.py:430-538, 383-386`, `engine/tracking.py:1439-1455` | placer writes 5 sinks → 3; no audit_log growth |
| 3.6 | Tracker: stop rewriting the whole `order_log.txt` per WB — append the tracking as a new line or update via the DB only (format rule 6 respected) | `engine/tracking.py:1458-1502` | no 56 MB rewrite per upload |

### Phase 4 — orchestration (S–M)

| # | Change | Where | Impact |
|---|---|---|---|
| 4.1 | One `daily_bulk` for both accounts, sequential per step (shared Gmail fetch/sync once, then per-account report → diff ingest → reconcile → FedEx API → messaging); enable `automation.auto_closing` (03:30 already configured) once Phase 1 makes a run cheap; consider every 2 h | `scripts/daily_bulk.py`, `bot_config.json` | no lock contention; closing becomes continuous instead of a manual 5-hour event |
| 4.2 | Tracker cadence: download the report every 30 min (or reuse the placer's unshipped list), keep the 10-min loop for archive-tier resolution only; re-sweep live Gmail for a WB only when `archive_tracking` shows a new email for it or every 60 min | `engine/tracking.py:1567-1612, 1257-1330` | −80 % Gmail calls, −5 report generations/hour/account |
| 4.3 | Move `Development_Unit` out of the Drive-mirrored `~/Downloads` (or exclude it); never mirror `data/` | ops | removes the corruption / duplicate-order risk |
| 4.4 | Docs: rewrite `AGENTS.md` §2–4 for the current stack, mark `CLOSING_PIPELINE_GUIDE.md` and doc §13 items 8/21 as resolved, add this audit and `closing_run_log` telemetry as the source of truth for performance | docs | future sessions stop re-solving solved problems |

### What this plan deliberately does **not** do

* No change to `utils/placement_lock.py`, the fail-closed duplicate check, the Sheets loads, the
  priority-coupon sweep/re-check, the label logic (only extended by data), the seller-note limit,
  the port map, or the `order_log.txt` line format (only appended keys).
* No "presumed delivered" messaging from ETAs — Rule 5.
* No speed shortcuts in the coupon path — the owner's standing directive.
* No purge or VACUUM without a verified backup and stopped placers — Rule 8.

---

## 6. Appendix — evidence

### 6.1 Today's bulk timeline (HOANK, from `.bulk_hoank_HOANK_output.log` and file mtimes)

```
18:02:37  launcher starts bulk_hoank (Hathoro started 18:02:54, in parallel)
18:03:54  Gmail archive fetch (2 days)            → 18:04:32, totals earnings=80149 zoro=63564 tracking=61152, 0 new
18:04:32  Sync archives → unified                 216,048 rows re-copied (~2 min)
18:06:18  closing.py HOANK run HOANK-20260926-180618
          "Placer log synced: 60158 updated, 0 new"
          headless Chrome 9331, 30-day report requested, polled 150 s
18:17     report 147340020722.txt (4.9 MB): 12,036 rows → 10,551 WB rows → 10,447 orders
          Resume: 69 already closed → 10,378 left; Priority: 10,195 with tracking / 183 without
18:31     last batch 346/346: Completed=0 Loss=0 Incomplete=10332 Cancelled=7
          Placer-log sync (again), Reconcile HOANK
≈18:35    SC scrape HOANK: 21,010 orders in Aug+Sep, 3,258 still missing columns, 0.42/s, ETA ~125 min
(19:2x)   still scraping (1,160/3,258)
Hathoro:  5,861 orders 18:06→18:22; scrape 1,443 done; FedEx: 6,723 candidates, blocked after 90, exit 2;
          gap-fill 236 Shipped orders
```

### 6.2 Read-only queries used (all against `file:data/automations_unified.db?mode=ro`)

```sql
-- placer date coverage, last 30 days
SELECT COUNT(*), SUM(COALESCE(purchase_date,'')<>''), SUM(COALESCE(ship_by_date,'')<>''),
       SUM(COALESCE(deliver_by_date,'')<>'') FROM order_placer_orders WHERE placed_at >= date('now','-30 day');
-- → 16648 | 16648 | 16648 | 16648

-- what drives the HOANK scrape queue (same predicate as scrape_sc_fields.build_queue)
SELECT SUM(deliver_by_date IS NULL OR TRIM(CAST(deliver_by_date AS TEXT)) IN ('','None')),
       SUM(tracking_id_uploaded IS NULL OR TRIM(CAST(tracking_id_uploaded AS TEXT)) IN ('','None')), COUNT(*)
FROM closing_orders WHERE account='HOANK' AND substr(purchase_date,1,7) IN ('2026-08','2026-09')
  AND COALESCE(enriched_from_scrape,0)=0 AND (deliver_by_date IS NULL OR TRIM(CAST(deliver_by_date AS TEXT)) IN ('','None')
  OR tracking_id_uploaded IS NULL OR TRIM(CAST(tracking_id_uploaded AS TEXT)) IN ('','None'));
-- → 6 | 2066 | 2066

-- scraped too early
SELECT COUNT(*) FROM closing_orders WHERE enriched_from_scrape=1 AND order_status='Shipped'
  AND (tracking_id_uploaded IS NULL OR TRIM(CAST(tracking_id_uploaded AS TEXT)) IN ('','None'))
  AND substr(purchase_date,1,7) IN ('2026-08','2026-09');
-- → 6061

-- 30-day outcome
SELECT account, COALESCE(order_label,'?'), COUNT(*) FROM closing_orders
 WHERE substr(purchase_date,1,10) >= date('now','-31 day') GROUP BY 1,2;
-- HOANK: Incomplete 10713, Refunded 92, Cancelled 86, Loss 67, Completed 2; Hathoro: Incomplete 6078, Refunded 132, Cancelled 50, Loss 20
SELECT account, SUM(delivered_msg_sent='Yes'), SUM(review_requested='Yes'),
       SUM(delivery_status IN ('Delivered','AllDelivered')), COUNT(*) FROM closing_orders
 WHERE substr(purchase_date,1,10) >= date('now','-31 day') GROUP BY 1;
-- HOANK: 0 | 0 | 4 | 10960 ; Hathoro: 0 | 0 | 0 | 6280

-- growth
SELECT substr(timestamp,1,10), COUNT(*) FROM enrichment_log WHERE timestamp >= date('now','-10 day') GROUP BY 1;
-- 2026-09-19 340938 ; 2026-09-26 380003          (total 2,760,010)
SELECT COUNT(*) FROM order_placer_orders WHERE synced_at >= date('now');   -- 57483
SELECT account, COUNT(*) FROM closing_orders WHERE created_at >= date('now') GROUP BY 1;  -- HOANK 3262, Hathoro 1444
PRAGMA page_count; PRAGMA freelist_count; PRAGMA wal_autocheckpoint;      -- 1375907 | 848328 | 1000
```

### 6.3 Files and sizes

```
data/automations_unified.db        5,374.6 MB      data/automations_unified.db-wal   3,963.9 MB
data/gmail_archive.db                718.2 MB      data/dashboard_slim.db              165.4 MB
data/console_log.txt               1,236.8 MB      data/raw_logs.txt                 2,060.7 MB
data/rajar_logs/order_log.txt         56 MB / 60,425 lines
data/tracking_upload_log.json      5,000 entries, all 26 Sep 09:06–12:22: no_tracking 4909, success 88, failed 3
```

### 6.4 Tracking uploader, one account, one afternoon (`data/logs/tracking_hoank_hoank_20260926-122211.log`)

```
NEW CYCLE 11:39:13  report → 135 WB / 181 SR   … Successful 4, 131 WB with no tracking
NEW CYCLE 11:49:25  report → 135 WB / 181 SR   … Successful 4, 131 WB with no tracking
NEW CYCLE 11:59:47  report → 135 WB / 181 SR   … Successful 4, 131 WB with no tracking
NEW CYCLE 12:10:44  report → 137 WB / 177 SR   … Successful 4, 133 WB with no tracking
```
