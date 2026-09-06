# FULL SYSTEM DOCUMENT — Dropshipping Automation Estate

**Date:** 2026-07-05 · **For:** Kamran (owner) + the 2 new employees
**Root:** `~/Desktop/Dropshipping/Development_Unit/`
**Status of this doc:** written after the July 2026 cleanup (see `FINAL_REVIEW/PLAN.md`). Everything below was verified against the live code and databases on the date above.

This is the handbook. If you only read two sections, read **1. System Map** and **4. Daily Runbook**.

---

## 1. SYSTEM MAP

The business: sell on two Amazon stores (**HOANK** and **Hathoro**), fulfill from **Zoro.com** (plus occasional Grainger "WW" orders placed by hand). Two software workers run on Kamran's always-on MacBook Air:

- **Worker A — the Order Placer** (`Automations/engine/orders.py`). Watches Amazon Seller Central for new orders, buys the matching item on Zoro, writes the Zoro order number (WB…) back into the Amazon order. Launched from the tkinter GUI (`Automations/gui/launcher.py`) as one process per profile: `DM_COUPON` (Chrome port 9221), `DM_RESELLER` (9222), `HS_COUPON` (9223), `HS_RESELLER` (9224). DM_* profiles belong to Hathoro, HS_* to HOANK.
- **Worker B — the Closing chain** (`Automations/scripts/daily_bulk.py`). One command that takes every placed order to a final verdict: enriched with earnings/cost/tracking, FedEx-checked for delivery, labeled (Completed / Loss / Refunded / …), and reported. Buyer **messaging is deliberately decoupled** from this chain — it runs as its own pass (`scripts/send_delivery_messages.py`) so slow Selenium messaging never blocks data extraction.

Everything lands in **one SQLite database**: `Automations/data/automations_unified.db`. The web dashboard reads it; nothing else is the source of truth.

```
                         WORKER A — PLACER                       WORKER B — CLOSING (scripts/daily_bulk.py)
┌──────────────────────────────────────────────┐   ┌─────────────────────────────────────────────────────────┐
│ gui/launcher.py spawns per profile:          │   │ 1  Gmail archive fetch (Final Automation/                │
│   engine/orders.py DM_COUPON   (Chrome 9221) │   │      build_gmail_archive.py, window from                 │
│   engine/orders.py DM_RESELLER (Chrome 9222) │   │      data/gmail_fetch_state.json, skip if <60 min fresh) │
│   engine/orders.py HS_COUPON   (Chrome 9223) │   │ 2  sync_gmail_archive.py  (gmail_archive.db → archive_*) │
│   engine/orders.py HS_RESELLER (Chrome 9224) │   │ 3  closing.py per account (EXTRACTION-ONLY: download     │
│                                              │   │      30-day SC report → ingest → enrich → label)         │
│ SC scrape → dedup checks → Zoro checkout     │   │ 4  sync_order_placer_logs.py (order_log.txt →            │
│  → WB written back to Amazon seller-order-id │   │      order_placer_orders)                                 │
│                                              │   │ 5  reconcile_and_reenrich.py per account                 │
│ writes:                                      │   │ 6  scrape_sc_fields.py (deliver_by / uploaded tracking / │
│  data/rajar_logs/order_log.txt   (audit)     │   │      feedback — Seller Central page is the ONLY source)  │
│  data/rajar_logs/run_<ts>.csv/.json          │   │ 7  fedex_recheck.py (parallel FedEx delivery check)      │
│  placer_claims (the placement LOCK)          │   │ 8  gmail_gapfill.py (live-Gmail tiered rescue for        │
│  orders table (best-effort live write)       │   │      anything the archive still lacks)                    │
└──────────────┬───────────────────────────────┘   │ 9  reconcile_and_reenrich.py again (final labels)        │
               │                                   │ 10 coverage report + REPORT-WINDOW VERDICT               │
               │ order_log.txt ──── synced ──────► │      (+ data/missing_report_<acct>.txt)                   │
               ▼                                   └───────────────┬─────────────────────────────────────────┘
      ┌─────────────────────┐                                      │
      │ order_placer_orders │◄─────────────────────────────────────┘
      └────────┬────────────┘        Gmail (16 inboxes) ─► Final Automation/gmail_archive.db (4.8 GB)
               │                                            └─► archive_earnings/zoro/tracking/returns/messages
               ▼                                                              │
      ┌────────────────────────────────────────────────────────────┐         │
      │ data/automations_unified.db  →  closing_orders (33k rows,  │◄────────┘
      │ THE source of truth: earnings, Zoro cost, tracking,        │
      │ delivery, refunds, labels, profit)                         │
      └───────┬───────────────────────────────────────┬────────────┘
              │                                       │
              ▼                                       ▼
   WEB DASHBOARD (dashboard/server.py :8000,   ✉ MESSAGING (decoupled):
   launchd + Tailscale for employees)          scripts/send_delivery_messages.py
   12 tabs: Orders · ⚠Severe · 📌Tracker ·      (delivery check-in + review request,
   🏷Unmapped · ⭐Needs Priority · Placed ·      MSG_LIMIT=300/run, all guards on)
   Unresolved · Insights · Health · P&L ·
   Bank reconcile · Data
              ▲
              │  /api/zoro_tsv (read-only, self-healing index)
   ZORO CATALOG FEED: TSV Files/quick_download.py (daily) → 3 TSVs from S3
   → TSV Files/combined_reports.db, table zoro_data (~2.0M rows: lead_time,
   availability, sales_price…) → ASIN-profile modal + order drawer + insights
```

**The tkinter GUI launcher** (`gui/launcher.py`, "ZUHRA Software") is the operator cockpit for Worker A and for kicking off Worker B: 13 pages — Dashboard, Orders, Tracking, Coupons, Post-Delivery (read-only status), **Closing (2 buttons: ▶ Run Closing / ✉ Run Messaging)**, Price Log, Log, Loss Orders, 🏷 Unmapped, ⭐ Needs Priority, 🔓 Locked Orders, Settings. The old FedEx page was removed in the cleanup (it was broken; the web dashboard covers delivery).

---

## 2. DATABASES & TABLES

### 2.1 `Automations/data/automations_unified.db` — THE database (WAL mode, busy_timeout everywhere)

| Table | Rows (2026-07-05) | What it is / who fills it |
|---|---|---|
| **closing_orders** | 33,095 | **The single source of truth.** One row per Amazon order, ~120 columns: earnings, Zoro cost breakdown (subtotal/shipping/tax/coupon), tracking, delivery status/date, refunds/returns, feedback, PKT/Pacific timing fields, profit, `order_label`. Filled by `closing.py`, `reconcile_and_reenrich.py`, `gmail_gapfill.py`, backfill scripts. Dashboard reads it. |
| **order_placer_orders** | 11,846 | Worker A's placements. **Not written by the placer directly** — refreshed from `data/rajar_logs/order_log.txt` by `scripts/sync_order_placer_logs.py` (called at closing startup, from daily_bulk, and every 60 s by the launcher's fast-sync tick). 100% WB/earnings/cost/address since the June parser fix. |
| **placer_claims** | 11,875 | The **placement lock** (`utils/placement_lock.py`): amazon_order_id PRIMARY KEY, status NEW/PLACING/CONFIRMED/RELEASED/FAILED_KEPT. Reserve-before-spend; this is what stops duplicate buying. |
| **archive_earnings** | 153,765 | Read-only mirror of Gmail "Sold, ship now" emails (earnings, fees, ship_by). Filled only by `sync_gmail_archive.py`. |
| **archive_zoro** | 127,668 | Zoro receipt emails ("has been received" old format **and** the new "Your Receipt for Order" PDF format; WB **and** SO ids). Cost, SKU, payment card, buyer zip. |
| **archive_tracking** | 126,404 | Zoro "has shipped" emails: tracking ids, carrier, shipped timestamp. |
| **archive_returns** | 12,901 | Return-authorization + refund-initiated emails (`email_type` distinguishes them). |
| **archive_messages** | 69,271 | Buyer↔seller message emails. |
| **order_crm** | 30 | Dashboard CRM overlay, keyed by Amazon id **or** WB: `flag_color`, `case_status`, `reminder_at/note`, `manual_delivered`, and the new **`tags`** column (comma list — Replacement, Reship, Damaged, Wrong Item, Chargeback, custom). Works even for orders not yet in closing_orders. |
| **crm_notes** | 16 | Comment threads with **author** (each employee's browser stores a profile name) + timestamp. |
| **monthly_pnl** | 0 (fill monthly) | Manual monthly close: payoneer / **investment (default 180,000)** / personal / amazon + stakeholder split JSON. Final Profit = amazon + payoneer − investment − personal. |
| **insight_resolutions** | 0 | "Resolve" clicks on Insights alerts (keyed `ref_key` so a dismissed alert stays dismissed). |
| **zoro_watch** | 0 | Restock/handling-time watchlist (hourly checker via stealth Chrome 9225). |
| orders | 5,947 | **Vestigial** live write from the placer (best-effort). Pipeline ignores it; kept because the placer still writes it. |
| enrichment_log / closing_run_log / fedex_tracking_cache / raw_api_log / audit_log / action_log / order_returns / order_messages / order_feedback / amazon_order_details | — | Audit/legacy support tables. `enrichment_log` = per-field change audit. |
| v_closing / v_orders_all | views | Friendly column order; `v_orders_all` = closing_orders ∪ not-yet-closed placer orders as "Pending". |

**Key closing_orders columns to know:** `order_label` (Completed / Loss / Refunded / Cancelled / Incomplete / Pending / NotPlaced / Manual), `profit_dollars` (= earnings − zoro_order_total; zoro total is the **grand** total, tax included once), `net_profit_after_refund`, `manual_review` (WW-Grainger / WB-D / C-WB / OOS…), `placed_via`, `shipped_on_time` (Early/On Time/Late, PKT→Pacific converted), `enriched_from_*` provenance flags.

### 2.2 `Final Automation/gmail_archive.db` — the Gmail archive (4.8 GB)

Raw email extraction store, tables `earnings_emails`, `zoro_emails`, `tracking_emails`, `return_emails`, `message_emails`, `receipts`, `sync_state`. Written by `Final Automation/build_gmail_archive.py` (incremental, all 5 email types, no per-run cap) and one-time by `full_archive_build.py` (the 180-day/16-inbox sweep done 2026-07-04). `scripts/sync_gmail_archive.py` copies it into the unified `archive_*` mirrors (idempotent, unique-indexed). **Rule: nothing writes `archive_*` except that sync.** The archive is the *rate-limit optimization*; live Gmail (gapfill) is the *guarantee*.

### 2.3 `TSV Files/combined_reports.db` — the Zoro catalog (1.3 GB)

One table **`zoro_data`**, **2,003,396 rows**, rebuilt daily by `TSV Files/quick_download.py` (downloads `zoro_datafeed_00/01/02.tsv` ≈1.2 GB from S3, then `pandas.to_sql(if_exists="replace")`). Columns: `zoro_no`, `sales_price`, `unit_price`, `lead_time`, `availabilityStatus`-family, brand, category levels, descriptions. **The rebuild drops indexes** — both `dashboard/insights.py` and the `/api/zoro_tsv` endpoint re-create `ix_zoro_no` on first use (self-healing, ~0.2 ms/lookup after). This is a *product catalog*, not order data. The dashboard uses it for: the ASIN-profile modal's "Zoro catalog now" section (availability, lead time, current price vs our last paid unit cost with a Δ% drift warning), the order drawer's 🏭 Zoro-now line, and the sourcing/insight engines. If the DB is absent or mid-rebuild the dashboard silently omits those sections.

---

## 3. CONFIG — SINGLE SOURCE OF TRUTH

**`Automations/config/accounts.json`** is the one file to edit when anything about accounts changes. Its own header says why: hardcoded per-file account lists are what caused the 2026-07-01 duplicate-order incident.

| Key | Current value | Meaning |
|---|---|---|
| `accounts.HOANK` | ports 9331 (report/SC) + 9332 (FedEx), `pd_key: hoank`, `earnings_inbox: qurtdis`, `zoro_preferred: [salesmercentile1, inhur_llc]` | Store definition: which Chrome ports, where its "Sold, ship now" emails land, which Zoro inboxes to try first. |
| `accounts.Hathoro` | ports 9333/9334, `pd_key: hathoro`, `earnings_inbox: abecomllc26`, `zoro_preferred: [salesmercentile, wrenchlyllc]` | Same for Hathoro. |
| `gmail_tokens` | 5 | Tokens that must exist for messaging guards. |
| `gmail_accounts` | 6 | General Gmail inboxes (returns/messages). |
| `zoro_accounts` | **15** | Every inbox that can receive a Zoro order confirmation — this list feeds the placer's duplicate check. |

**Consumers** (all via `Automations/utils/accounts_config.py` unless noted):

| Consumer | What it reads |
|---|---|
| `closing.py` | accounts, ports, lockfiles |
| `engine/live_gmail_per_order.py` | preferred-inbox tiering (archive → preferred → ALL) |
| `engine/post_delivery.py` | account config, guards |
| `scripts/daily_bulk.py` | account list, `default_months()`, locks |
| `scripts/fedex_recheck.py`, `scripts/scrape_sc_fields.py` | accounts/ports/months |
| `scripts/send_delivery_messages.py`, `scripts/gmail_gapfill.py` | accounts, tokens, locks |
| **Placer dup-list** — `utils/config.py` `_load_confirmation_inboxes()` | union of `zoro_accounts` + `gmail_accounts` → **15 unique dup-check inboxes** (fail-closed fallback to a frozen 9-inbox list if the JSON is unreadable) |
| `Final Automation/config.py` | reads accounts.json for `EARNINGS_GMAIL_ACCOUNTS` / ZORO / ALL lists; token dir points at the shared `Automations/data/All_Tokens` |

**Rule for employees: add or change accounts in `config/accounts.json` ONLY. Never edit account lists inside scripts.**

**One known mismatch:** `Automations/data/All_Tokens/` holds **16** Gmail tokens; accounts.json lists **15** — `excurellc` is excluded (it places no Zoro orders and had zero 2026 activity). Owner may add it or leave it; nothing breaks either way.

Other config files: `Automations/bot_config.json` (GUI overrides for the placer: margins per profile, coupon settings — note some velocity knobs are currently inert, see §7), `Automations/data/All_Tokens/` (Gmail OAuth tokens — the single shared token directory), `gen-lang-client-…json` (Google service-account key for Sheets — see §7 security).

---

## 4. DAILY RUNBOOK (for the employees)

Everything below happens on Kamran's MacBook Air. Remote employees use the web dashboard over Tailscale (`http://100.75.77.36:8000`); the GUI launcher steps need someone at the machine.

### 4.1 Morning: launch Chrome + log in

1. Open the launcher (ZUHRA GUI). Go to the **Closing** page.
2. Click **🚀 Launch HOANK** and **🚀 Launch Hathoro**. Each opens a dedicated headed Chrome (HOANK port 9331, Hathoro 9333).
3. **Log into Seller Central in each window** (2FA if asked). The SC session lives only inside that running Chrome — never close it mid-day; headless does not work for SC.

### 4.2 ▶ Run Closing (per account)

Select the account, click **▶ Run Closing**. This runs the whole `daily_bulk.py` chain (with messaging skipped): Gmail archive refresh (skipped if fetched <60 min ago) → sync → 30-day SC report download + extraction-only closing → placer sync → reconcile → SC scrape (deliver-by/uploaded-tracking/feedback) → FedEx recheck → live-Gmail gapfill → final reconcile → coverage report. Both accounts can run side by side (locks are per account).

**How long:** roughly 1–2 hours depending on how many new orders and how many FedEx checks are due. The Gmail fetch and FedEx steps are fast; the SC scrape is the slow part (~0.3 orders/s).

**Read the end-of-run summary — it is the verdict:**
- **Failure block**: `🚨 daily_bulk finished WITH N FAILED critical step(s)` = something real broke (exit code 1); a `⚠ best-effort step(s) did not finish` list means deliver_by/feedback/messages just lag until next run (usually Chrome wasn't logged in). All-green ✅ = good run.
- **Coverage report**: per-column missing counts for Shipped orders. Anything non-zero writes the exact order ids to `data/missing_report_<ACCOUNT>.txt` — that file is your to-do list, not a mystery.
- **Report-window verdict**: "the last-31-day report had N orders — X fully closed", then problem buckets with example ids: in-transit (normal, self-heals on delivery), no-tracking, missing cost/earnings, delivered-but-Incomplete (investigate these).

### 4.3 ✉ Run Messaging (per account, when you have time)

Select account → set **Msg-limit** (default **300** per run — deliberately capped so a backlog drains gradually; thousands at once looks like spam to Amazon) → optionally tick **Dry-Run** first (previews, sends nothing) → click **✉ Run Messaging**. It attaches to the already-logged-in SC Chrome (never opens a blank one), and for every delivered order that still needs it sends the delivery check-in + review request. Guards are automatic (see §5) — it will silently skip anyone who returned, refunded, complained, or was already messaged. `MSG_MAX_DAYS=30`: deliveries older than 30 days are never messaged.

### 4.4 Where the logs are

| What | Where |
|---|---|
| Latest closing run | `Automations/data/logs/bulk_<acct>_<timestamp>.log` — newest via `ls -t Automations/data/logs | head` (launcher keeps the newest 40) |
| Messaging runs | `data/logs/messages_<acct>_…log` |
| Placer runs | `data/logs/orders_<PROFILE>_…log`; per-run CSV/JSON in `data/rajar_logs/run_<ts>.csv/.json`; the permanent audit line-per-placement in `data/rajar_logs/order_log.txt` |
| Everything interleaved | `data/raw_logs.txt` (older history) |
| Missing-data worklists | `data/missing_report_HOANK.txt` / `…Hathoro.txt` |

### 4.5 Gapfill and backlog behavior

`gmail_gapfill.py` (step inside Run Closing) rescues at most **500 rows per run**, newest first — a big backlog drains over several daily runs by design. It searches live Gmail tiered (preferred inboxes → all 15) and never downgrades a terminal label. If the Health tab shows a stubborn missing-field count, just run closing again tomorrow; if it doesn't shrink, tell Kamran.

### 4.6 Zoro TSV daily download

Once a day (any time): `cd "TSV Files" && python quick_download.py`. It downloads the three datafeed TSVs and rebuilds `combined_reports.db`. Don't run it while you actively need the dashboard's Zoro-catalog panels (the table is briefly absent mid-rebuild; the dashboard degrades gracefully and self-heals its index afterwards).

### 4.7 Dashboard restart (when needed)

The dashboard normally runs via launchd (`com.dropship.dashboard.plist`) / `scripts/setup_shared_dashboard.sh`. **Rule:** changes to `server.py`/`analytics.py`/`insights.py` need a server restart; changes to `index.html` only need a browser hard-refresh (Cmd-Shift-R). Manual start: `cd Automations && ../.venv/bin/python dashboard/server.py 8000`. Remote access: Tailscale IP `100.75.77.36:8000` (no auth — private tailnet only).

### 4.8 Dashboard daily working habits

- **Orders / Data tabs** = browse and edit; every cell in Data is editable and saves live.
- **⚠ Severe** = the money-leak action center (placed-but-never-captured Zoro spend, refunds without return, refunds netting negative). Resolve with the status dropdowns.
- **📌 Tracker** = everything you flagged/tagged/commented. Use flag colors + the **tags** (Replacement, Reship, …) — both are filterable.
- **Set your profile name** (👤 button, stored in your browser) so your comments carry your name.
- **Insights** = the change engine (see §6.3): one alert per real change per ASIN (price, earnings/unit, handling time, Zoro code/cost/shipping) — resolve what you've acted on.
- **Health** = data-integrity scoreboard per account (green/amber/red). Amber residuals are documented and OK; new red = tell Kamran.

---

## 5. SAFETY SYSTEMS

These exist because of the 2026-07-01 incident (~$11.7k of duplicate Zoro orders). Know them; never disable them.

| Rail | What it does | Where |
|---|---|---|
| **Placement lock (reserve-before-spend)** | `placer_claims` table: the placer atomically claims an Amazon id BEFORE any money moves; already-seen ids are skipped instantly; DB error = fail-closed (refuse to place). Seeded at startup from `order_log.txt`, orphaned claims auto-reconciled to FAILED_KEPT for human review (GUI 🔓 Locked Orders tab / `scripts/unlock_order.py`). | `utils/placement_lock.py`, `engine/orders.py` |
| **Fail-closed Gmail dup check** | Before buying, the placer searches all **15** confirmation inboxes (from accounts.json) for an existing WB for that buyer, 7-day window. If the search can't complete (rate limit, network) it returns `DUP_CHECK_FAILED` and the order is **skipped**, not placed. | `platforms/gmail.py`, `utils/config.py` |
| **300/cycle hard cap + circuit breaker** | Safety ceiling on placements per cycle; repeated duplicate-blocks trigger a loud alert. | `engine/orders.py` |
| **Verified write-back** | The WB id written to Amazon's seller-order-id field is re-read and retried until verified — a labeled order is never re-bought. | `platforms/amazon.py` |
| **Per-account run locks** | closing (`closing_<acct>.lock`), daily_bulk (per account set), gapfill, messaging — same-account double runs are blocked; different accounts run concurrently (WAL + busy_timeout). | `utils/accounts_config.py`, `data/*.lock` |
| **Resume** | Closing skips orders already at a terminal label (Completed/Refunded/Cancelled/Loss); crash-safe restarts; `reconcile_and_reenrich.py` is the idempotent catch-up that heals terminal rows when archive data arrives late. | `closing.py`, `scripts/reconcile_and_reenrich.py` |
| **Non-destructive persist** | Closing writes are gap-fill: an empty value never overwrites a good one; per-row try/except so one poison order can't kill a batch. | `closing.py` |
| **FedEx "Unknown" guard** | A flaky FedEx batch returning Unknown can never downgrade an existing informative delivery status. | `closing.py bulk_fedex_check` |
| **Login-wait cap** | Report download waits max `CLOSING_LOGIN_WAIT_MINS=5` for an SC login instead of hanging forever. | `closing.py` |
| **Messaging guards** | Before any buyer message: Gmail already-sent fingerprint check, return/refund skip, customer-inquiry skip (language-proof: matches the marketplace.amazon.com sender), zip-mismatch skip, 6/12-day timing windows, 30-day max age, never-downgrade-terminal-status, dry-run mode, 300/run cap. | `engine/post_delivery.py`, `scripts/send_delivery_messages.py` |
| **No-browser-popup Gmail** | Background Gmail auth can never open an OAuth browser popup; broken tokens are logged and skipped (interactive re-auth only from a real terminal). | `Final Automation/utils.py` |

**Known limit (documented, accepted):** the placement lock is local to this machine. If a placer ever runs on a second machine, only the Amazon label + the 7-day Gmail check protect against cross-machine duplicates. Currently all placing happens on this one Mac.

---

## 6. VALIDATION VERDICTS

Collected from the placer audit (`FINAL_REVIEW/order_placer_analysis.md`) and the closing-pipeline hardening sessions. "PASS" = logic verified correct in code against real data.

| # | Logic | Verdict | Notes |
|---|---|---|---|
| 1 | **Placer profit gate** (primary path) | **PASS** | Gates AFTER coupons on the real cart, **pre-tax** basis (tax is refunded on resale accounts), per-label threshold, before any payment. LOW_PROFIT = no money spent, claim released, "Loss-Kamran checking" label. |
| 2 | Placer profit gate (Vuex/DOM **fallback** paths) | **FAIL (consistency)** | Fallbacks gate on the tax-inclusive total → can falsely reject profitable orders near threshold. Pending owner sign-off (§7). |
| 3 | **Coupon application** | **PASS w/ caveats** | Priority→regular, first-match-wins, expired-vs-invalid handled, single-use codes deleted only after confirmed WB, coupon-load failure aborts the cycle (never places coupon-less). Caveat: a coupon that applies with $0 discount still counts as success (B18, pending). |
| 4 | **Address handling** | **PASS** | PO Box blocked at Amazon scrape AND at Zoro (sentinel + sanitize-retry); PR/HI/AK blocked pre-checkout; Zoro state restrictions → "SR" label; 35-char line spill handled. |
| 5 | **Quantity handling** | **PASS** | Multi-strategy detail-page extraction; `final_qty = qty × qty_multiplier × zoro_multiple`; cart quantity verified after add — mismatch rejects the order. |
| 6 | **Priority / favorites** | **PASS** | Priority ASINs from the sheet every cycle; ONE favorites-view scrape; only unlabelled priority orders placed; unlabelled non-priority → NEEDS_PRIORITY queue (report-only). |
| 7 | **Duplicate protection** | **PASS (this machine) / UNCLEAR (multi-machine)** | Layered: label skip → local lock → 15-inbox Gmail check (7d) → reserve-before-spend → verified write-back → FAILED_KEPT on unknowns. Live-verified blocking the exact incident pattern. |
| 8 | **Profit formula (closing)** | **PASS** | `profit = earnings − zoro_order_total` where zoro total is the **grand** total (tax included once). The old double-tax-subtraction bug was fixed and all rows recomputed; verified against both archive_zoro and placer conventions. |
| 9 | **Label rules** | **PASS** | Refund branch first (Refunded if shipped, Cancelled if never shipped — `order_status='Shipped'` accepted as shipped evidence); Completed needs earnings+cost+tracking+Delivered (all packages of a split); delivered+negative = Loss; Amazon-cancelled honored; WW/NotPlaced/Manual preserved. |
| 10 | **Timezones** | **PASS** | Email timestamps = PKT; report purchase dates converted UTC→US/Pacific (Amazon's date); `placed_at` = PKT machine time; ship-on-time verdict converts PKT ship time → Pacific date vs the Pacific ship-by date (3-state Early/On Time/Late). |
| 11 | **Ship-by weekend skew** | **PASS** | Handling-time change detection uses **business days** (Fri purchase → Mon ship-by = 1 day, not a change). |
| 12 | **Zoro fee rules** | **PASS (updated)** | $5.99 small-order fee constant (was stale $5.00 in 5 places — fixed); free shipping at $50 **pre-coupon** subtotal; the insights ship-rule checker accepts both $5.00 (pre-May-2026) and $5.99 eras. |
| 13 | **Split orders (closing side)** | **PASS (fixed)** | Cost = SUM over all WB/SO legs (`_sum_leg_costs` + reconcile), tracking = UNION over legs, Delivered only when ALL legs delivered. 29 historical orders recomputed (25 hidden losses surfaced). |
| 14 | **Split orders (placer side)** | **KNOWN ISSUE — open** | B1/B2/B3: a partially-failed split still reports SUCCESS (under-fulfillment risk); split earnings divided evenly across unequal quantities; multi-item extras re-purchased per split. Owner sign-off pending (§7). |
| 15 | **FedEx delivery statuses** | **PASS** | Real page-parsed statuses; a 404 can only ever be "Unknown", never a fake Delivered. |
| 16 | **Applied placer fixes (2026-07-04)** | **DONE** | PO-box NameError fixed; dup-inbox list now 15 from accounts.json (fail-closed); $5.99 fee constant + DOM `$5.xx` regex; Gmail dup window 3d→7d; honest "Waiting 10 seconds" log. All compile-verified. |

### 6.3 The Insights "what changed" engine (shipped 2026-07-04)

Per-ASIN **change-point** detection over `order_placer_orders` + `closing_orders` (one alert per real transition, not per order; A→B→A reported once; 60-day default window): **zoro_code_change** (OOS → alternative code swaps), **item_price_change**, **earnings_unit_change** (catches Amazon fee-category changes), **shipping_amt_change**, **handling_time_change** (business days), **zoro_ship_rule** (order deviates from the $50/$5.99 rule → usually a manual carrier change), **zoro_unit_cost_change** (subtotal/qty per code). All per-unit normalized (qty 2 at the same unit price is NOT a change).

---

## 7. PENDING OWNER DECISIONS

Nothing here blocks daily operations; each needs Kamran's sign-off because it changes money behavior, security posture, or deletes things.

| # | Decision | Detail |
|---|---|---|
| 1 | **Split-order redesign (placer)** — the only remaining dup-spend/under-fulfillment path | Approve the 3-part fix: weight split earnings by qty share; forbid SPLIT_RETRY on multi-item carts; a partial split returns `PARTIAL_SPLIT` + review entry instead of SUCCESS. (Also B5: DOM-fallback returns a dict treated as a WB id — burns a coupon and locks the order.) |
| 2 | **Fallback margin-gate consistency** | Make the Vuex/DOM fallback checkouts gate pre-tax like the primary path, or accept occasional false LOW_PROFIT rejections when the primary API path degrades. |
| 3 | **GUI safety-knob wiring** | The launcher's order-limit / max-per-cycle / cycle-sleep / est-margin-only controls are currently **decorative** (engine ignores them; real cadence ≈10 s + scrape, cap = the hardcoded 300). Wire them or remove them — an inert safety rail is worse than none. |
| 4 | **Rotate credentials** | (a) The Google service-account key `gen-lang-client-0493912626-fbfdbabc0441.json` sits **in the repo twice** (Automations/ + Final Automation/) and is live — consolidate outside the repo + rotate. (b) Gmail OAuth tokens exist in pushed **git history** — revoke/re-auth. (c) `TSV Files/quick_download.py` still contains **hardcoded AWS keys** — move to env vars (like the retired auto_download.py did) and rotate the key. |
| 5 | **4.1 GB Final Automation Chrome profiles** | `.uc_profile*`, `.fedex_*_profile` belong to the retired FA tracker (last touched May–June). Biggest reclaimable space; delete once the FA tracker is confirmed dead forever. |
| 6 | **DS_Closing dependency migration** | `DS_Closing/` is legacy but **live code still reads it**: `dashboard/insights.py` needs `DS_Closing/Hypersale_Closing/verified_skus_cache.csv`; `scripts/backfill_unified_db.py` reads its Order_Reports; `engine/tracking.py` points at a now-absent xlsx. Move the CSV into Automations/data and repoint, then DS_Closing can be archived. |
| 7 | **`_DELETE_REVIEW/` final deletion** | 50 items ≈ 1.2 GB moved there (broken venv 510 MB, partial TSV temp 582 MB, Automations/backup 102 MB, dashboard .baks, closing_runner.py, 18 dead FA scripts…). Everything is restorable (`mv "_DELETE_REVIEW/<path>" "<path>"`). Review `FINAL_REVIEW/file_inventory.md`, then delete the folder. |
| 8 | **excurellc inbox** | 16th token, not in accounts.json (zero Zoro activity). Add to `zoro_accounts` for belt-and-braces dup coverage, or ignore. |
| 9 | Smaller placer items awaiting the same sign-off round | B8 account attribution hardcoded (`DM_*→Hathoro, HS_*→HOANK` literal); B18 $0-discount coupon counted as success; B12 filler price assumption; per-Zoro-account mutex if two profiles ever share a Zoro login. |

---

## 8. FILE TREE — what everything is

```
Development_Unit/
├── Automations/                ★ THE LIVE STACK — everything current lives here
│   ├── closing.py              Worker B core: report → enrich → FedEx → label → persist (env-driven)
│   ├── engine/
│   │   ├── orders.py           Worker A placer (LIVE — treat with care)
│   │   ├── tracking.py         Tracking-ID uploader (downloads into _dl_hathoro/_dl_hoank — live)
│   │   ├── post_delivery.py    Messaging engine + all buyer-message guards
│   │   ├── live_gmail_per_order.py  Tiered live-Gmail per-field search
│   │   ├── fedex_api.py        FedEx via in-browser CDP fetch (beats Akamai)
│   │   ├── amazon_scraper.py / closing_signals.py   SC page + email extractors
│   │   └── closing_pipeline.py KEPT for 3 old importer scripts (superseded by closing.py)
│   ├── scripts/                ~50 operational scripts; the ones that matter daily:
│   │   ├── daily_bulk.py         ★ the one-command closing chain (GUI ▶ Run Closing)
│   │   ├── send_delivery_messages.py  ★ decoupled messaging (GUI ✉ Run Messaging)
│   │   ├── reconcile_and_reenrich.py  idempotent catch-up/heal (the keystone)
│   │   ├── gmail_gapfill.py / fedex_recheck.py / scrape_sc_fields.py
│   │   ├── sync_gmail_archive.py / sync_order_placer_logs.py
│   │   ├── unlock_order.py       CLI unlock for FAILED_KEPT claims
│   │   └── fetch_*.py, backfill_*.py, recompute_*.py   targeted one-off repair tools (keep)
│   ├── dashboard/              server.py (:8000, launchd) + index.html (12 tabs) + analytics.py + insights.py
│   ├── gui/launcher.py         ZUHRA tkinter launcher, 13 pages, 2-button closing
│   ├── platforms/ checkout/ utils/   placer internals (gmail/amazon/zoro, checkout chain, lock, config)
│   ├── config/accounts.json    ★ SINGLE SOURCE for accounts/ports/inboxes
│   ├── bot_config.json         GUI placer overrides (margins etc.)
│   ├── data/                   automations_unified.db ★ + All_Tokens/ (16 gmail tokens) +
│   │                           rajar_logs/ (order_log.txt, run CSVs) + logs/ + reports/ +
│   │                           missing_report_*.txt + *.lock + gmail_fetch_state.json
│   └── CLOSING_PIPELINE_GUIDE.md   older deep-dive on the closing data model (partly historical)
├── Final Automation/           MOSTLY RETIRED tracker stack — but 5 pieces are LIVE:
│   │                           config.py, utils.py, gmail_archive.py, build_gmail_archive.py,
│   │                           full_archive_build.py + gmail_archive.db (4.8 GB archive)
│   │                           (unified_tracker.py etc. = kept-for-reference; 4.1 GB Chrome
│   │                            profiles reclaimable — see §7.5)
├── TSV Files/                  quick_download.py (daily S3 feed) → combined_reports.db (zoro_data 2M)
│   │                           + live_enrich.py (stealth Zoro PDP checker, port 9225)
├── DS_Closing/                 LEGACY (2025 era). Kept ONLY because insights.py reads
│   │                           Hypersale_Closing/verified_skus_cache.csv — see §7.6
├── ZORO Clone/ + ZORO Clone.zip   Separate Zoro-clone project (out of scope; zip is a Jul-4 archive)
├── Listings_Hunting/           Listing-research tooling (out of scope, kept)
├── Auto_Lister.py, Complete_Daily_Listing_Automation.py   standalone listing tools (run by hand)
├── AUDIT_REPORT.md / AUDIT_REPORT_V2.md / AGENTS.md / README.md   history & docs
├── FINAL_REVIEW/               PLAN.md, order_placer_analysis.md, file_inventory.md + THIS DOCUMENT
├── _DELETE_REVIEW/             1.2 GB of verified-dead files awaiting Kamran's final delete (§7.7)
└── .venv/                      THE Python venv (always run scripts with ../.venv/bin/python)
```

**Live vs reference, in one line:** `Automations/` is live; `Final Automation/` is live only for the five Gmail-archive files + the archive DB; `TSV Files/` is a live daily feed; `DS_Closing/` is reference plus one live CSV; `_DELETE_REVIEW/` is dead pending deletion.

---

*Questions this doc doesn't answer → check `FINAL_REVIEW/order_placer_analysis.md` (placer internals), `Automations/dashboard/DASHBOARD_CHANGES.md` (latest dashboard batch), and `FINAL_REVIEW/file_inventory.md` (what was moved where). When in doubt: don't edit account lists in code, don't run two same-account closings, and never kill the logged-in SC Chromes.*
