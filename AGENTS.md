# AGENTS.md — Dropshipping Development Unit

> Authoritative guide for AI agents working in this repository. Rewritten 26 September 2026 —
> the previous version described a `Final Automation/` stack that no longer exists and sent
> agents to fix problems that were already solved. Human instructions in conversation always win.

---

## 1. What this repository is

Two Amazon seller accounts — **HOANK STORE** ("HYPER SALE STORE" on Seller Central, earnings
inbox `qurtdis`) and **Hathoro** ("Discounted Maal", earnings inbox `abecomllc26`) — dropship
industrial supplies bought on **Zoro.com** and shipped by FedEx/UPS. `Automations/` ("ZUHRA
Software") is the **live production stack that handles real money**; everything else is tooling
around it.

| Directory | Purpose | Status |
|---|---|---|
| `Automations/` | placers, tracking uploader, closing chain, dashboard, cloud agent | **LIVE** — read the guardrails before touching |
| `Amazon_DS_Hunting/` | `hunter.py`: the always-on product hunter + portal (:5055) | live, independent |
| `TSV Files/` | Zoro catalog feed → `combined_reports.db` | used by the hunter and listing tools |
| `DS_Closing/` | legacy monthly P&L scripts (Excel) | historical |
| `Listings_Hunting/`, `Auto_Lister.py`, `Complete_Daily_Listing_Automation.py` | older listing tools | dormant |
| `ZORO Clone/`, `Hathoro Website/` | web projects | unrelated to the order flow |

---

## 2. Must-read documents, in this order

1. `Automations/automations_complete_documentation.md` — **Appendix F "NEVER-DO rules"** (placement
   lock, fail-closed duplicate check, Google Sheets authority, `order_log.txt` format, label logic,
   messaging guards, port map …). Sections 1–12 describe the modules; §13–15 are partly stale —
   the audit below says what changed.
2. `Automations/docs/CLOUD_ARCHITECTURE.md` — R2 cloud layer, launcher remote control, the
   placement routing rules (7–14 Sep 2026), two-machine DB sync.
3. `Automations/docs/OPERATIONS_AUDIT_2026-09-26.md` — measured bottlenecks, the phased plan, and
   **what was implemented on 26 Sep 2026** (section 0).
4. `Automations/docs/ALERTS_AND_INSIGHTS_SPEC.md`, `Automations/dashboard/V2_API.md`.

---

## 3. The live stack on one page

All worker processes are started from the Tk launcher `Automations/gui/launcher.py`; the cloud
agent and the dashboard run as macOS LaunchAgents.

* **Placers** — `engine/orders.py`, four profiles / Chrome ports: DM_COUPON 9221, DM_RESELLER 9222,
  HS_COUPON 9223, HS_RESELLER 9224. Scrape Seller Central orders, Gmail earnings + duplicate check,
  Zoro cart / coupons / checkout via `checkout/true_api.py` (DOM fallback), write the WB id back to
  Amazon, log to `data/rajar_logs/order_log.txt`, `order_placer_orders` and the legacy `orders`
  table. **Placement captures purchase / ship-by / deliver-by dates** — closing takes them from
  there. Routing/coupon rules: `docs/CLOUD_ARCHITECTURE.md` §5.
* **Tracking uploaders** — `engine/tracking.py`, HOANK on 9331, Hathoro on 9333. All-orders report
  (reused for 30 min), `archive_tracking` → one batched Gmail sweep → PDF, upload on Confirm
  Shipment, "shipped" buyer message. Since 26 Sep 2026 it records what it uploaded in
  `order_placer_orders.tracking_id_uploaded` and `action_log` (`action_type='tracking_upload'`).
* **Closing chain** — `scripts/daily_bulk.py` (▶ Run Closing, or auto-closing from `bot_config.json`):
  Gmail archive fetch + incremental sync → `closing.py` **report-diff ingest** (only new / changed /
  new-evidence orders get a pass) → placer sync → `scripts/reconcile_and_reenrich.py` →
  exception-only SC scrape (`scripts/scrape_sc_fields.py`) → delivery re-check
  (`scripts/fedex_recheck.py`, only with carrier API keys) → live-Gmail gap-fill → reconcile →
  messaging (`scripts/send_delivery_messages.py`) → coverage. Telemetry: `closing_run_log`
  (`/api/runs` on the dashboard).
* **Dashboard** `dashboard/server.py` (:8000, `v2.html`); **cloud agent** `scripts/cloud_agent.py`
  (heartbeat, slim DB export to R2, remote commands, WAL checkpoint every 30 min); **hourly**
  `scripts/auto_sync_10min.py` (Gmail fetch, incremental archive sync, placer sync, WhatsApp
  insights, log rotation).
* **Data** — `data/automations_unified.db` (master, WAL), `data/gmail_archive.db` (email archive),
  the Google Sheet (SKU map / coupons / priority — team-edited, sacred), `config/accounts.json`
  (accounts, inboxes, carrier API keys), `bot_config.json` (runtime knobs).

Order lifecycle: Amazon sale → placer buys on Zoro (WB id) → Zoro "received" / "shipped" emails
→ tracker uploads tracking + messages the buyer → carrier confirms delivery → closing labels the
order (Completed / Loss / Refunded / Cancelled / Incomplete) → delivery check-in (6 d) and review
request (12 d).

---

## 4. Working rules for agents

* Inspect the DB read-only: `sqlite3 "file:Automations/data/automations_unified.db?mode=ro"`.
  Any purge / VACUUM: backup first (`scripts/slim_database.py --apply` does), placers stopped.
* Never run `scripts/test_pipeline_orders.py` (it drives a live placer Chrome). Never open a tab in
  a running placer's Chrome (9221–9224). Tests are run by explicit name; there is no full suite.
* Never weaken: the placement lock, the fail-closed duplicate check, the Google Sheets loads, the
  coupon / priority logic (owner's rule: reliability over speed), the label criteria, the messaging
  guards (no "presumed delivered" messaging), the `order_log.txt` line format (keys may only be
  appended), the port map. Appendix F is the full list.
* Start long-lived processes through the launcher or the agent, never from a tool shell.
* The project folder sits inside a two-way Google Drive mirror: expect `(1)` conflict copies and
  never run anything from the Windows mirror. Backups belong outside it (`~/.zuhra/backups`).
* Git: `main` is the deployed branch (Render deploys the dashboard from it). Commit with a
  message that says what changed and why; the working tree may carry the owner's uncommitted
  config edits — leave those alone.

---

## 5. Where to look for what

| Question | Where |
|---|---|
| How is an order placed / routed | `engine/orders.py` (`process_order`), `docs/CLOUD_ARCHITECTURE.md` §5 |
| Why did closing (not) touch an order | `closing.py` `_select_work` (reasons printed per run); `Final_Testing_Logs/runs/<run>.jsonl` |
| What the tracker uploaded | `order_placer_orders.tracking_id_uploaded`, `action_log` |
| Delivery status source | `engine/fedex_api.py` — official FedEx Track API and UPS API; the browser tiers are off (Akamai) |
| Messaging guards | `engine/post_delivery.py` (`prefetch_guards`, `process_single_order`) |
| DB size / WAL / backups | `scripts/slim_database.py`, agent `wal_checkpoint`, `~/.zuhra/backups` |
| Run durations | `closing_run_log`, `/api/runs` |
| Alerts | `utils/insights_engine.py`, `utils/whatsapp.py`, `utils/telegram.py` |
