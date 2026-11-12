# AGENTS.md — Dropshipping Development Unit

> Authoritative guide for AI agents working in this repository. Rewritten 26 September 2026 —
> the previous version described a `Final Automation/` stack that no longer exists and sent
> agents to fix problems that were already solved. Human instructions in conversation always win.

---

## 1. What this repository is

Three Amazon seller accounts — **HOANK STORE** ("HYPER SALE STORE" on Seller Central, earnings
inbox `qurtdis`), **Hathoro** ("Discounted Maal", earnings inbox `abecomllc26`) and, since
6 Nov 2026, **Wrenchly** ("Wrenchly Solutions LLC", earnings inbox `wrenchlyllc`, reseller-only:
`WR_RESELLER`) — dropship industrial supplies bought on **Zoro.com** and shipped by FedEx/UPS.
The store of a profile comes from `utils.accounts_config.store_for_profile()`, never from
"not DM → HOANK". `Automations/` ("ZUHRA
Software") is the **live production stack that handles real money**; everything else is tooling
around it.

| Directory | Purpose | Status |
|---|---|---|
| `Automations/` | placers, tracking uploader, closing chain, dashboard, cloud agent | **LIVE** — read the guardrails before touching |
| `Amazon_DS_Hunting/` | `hunter.py`: the always-on product hunter + portal (:5055) | live, independent |
| `TSV Files/` | Zoro catalog feed → `combined_reports.db` | used by the hunter and listing tools |
| `DS_Closing/` | legacy monthly P&L scripts (Excel) | historical |
| `Listings_Hunting/`, `Auto_Lister.py`, `Complete_Daily_Listing_Automation.py` | older listing tools | dormant |
| `Hathoro Website/` | web project (EKUIPT storefront moved to `~/Developer/EKUIPT`, 31 Oct 2026) | unrelated to the order flow |
| `reports/`, `docs/archive/`, `Automations/scripts/archive/` | one-off reports, superseded audits, one-off backfill/repair scripts | reference only |

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

* **Placers** — `engine/orders.py`, five profiles / Chrome ports: DM_COUPON 9221, DM_RESELLER 9222,
  HS_COUPON 9223, HS_RESELLER 9224, WR_RESELLER 9226 (Wrenchly, reseller-only, places every mapped
  unlabelled order — `docs/CLOUD_ARCHITECTURE.md` §5). Scrape Seller Central orders, Gmail earnings + duplicate check,
  Zoro cart / coupons / checkout via `checkout/true_api.py` (DOM fallback), write the WB id back to
  Amazon, log to `data/rajar_logs/order_log.txt`, `order_placer_orders` and the legacy `orders`
  table. **Placement captures purchase / ship-by / deliver-by dates** — closing takes them from
  there. Routing/coupon rules: `docs/CLOUD_ARCHITECTURE.md` §5.
  **Ultra mode** (per profile, launcher 🚀Ultra, 2 Nov 2026): the Amazon scraping and WB write-backs run
  on two extra Chrome windows beside the placing (`engine/ultra_crew.py`, `docs/ULTRA_MODE.md`).
  **Combined order** (reseller profiles, 12 Nov 2026): one buyer's orders to one address in the same
  cycle are bought as ONE Zoro order when that saves Zoro shipping and each order clears its own margin
  on its share. Every Amazon order keeps the plain WB, its own `order_log.txt` line (share of the cost,
  `CombinedWith:`) and `order_placer_orders.combined_with`; closing keeps the share
  (`closing.placer_share`). `bot_config.json → combine_same_buyer: false` turns it off
  (docs §5.1 "Combined order").
  **OOS orders on restock** (12 Nov 2026): once an hour per item the placer asks Zoro about the items of
  OOS-stamped orders; when one is back, its OOS orders with 24+ h left before Amazon's ship-by deadline
  are released (earliest first, as many as Zoro's stock covers) and go through every normal check
  (`_oos_restock_release`, `oos_restock_check_min`, `oos_restock_min_hours`). The sheet's
  `OOS_Release_<store>` column still releases anything by hand.
  **PO boxes** (10 Nov 2026): placed with the box renamed "#number" behind the street
  (`platforms.amazon.rename_po_box`); label BB only when the address has no street at all or Zoro
  still refuses it. **Listing guard** (`engine/listing_guard.py`, 10 Nov 2026): when the Zoro code
  and every alternative are out, the placer notes the listing (`listing_oos`) and at the end of the
  cycle decides PL / DS from the product's brand, makes the change on Seller Central in its own
  Chrome (DS → quantity 0; PL → handling time 7 days, never 0 — `engine/offer_edit.py`, driven and
  tried on the live Edit Listing → Offer page on 10 Nov 2026), says what was done in the
  store's Insights group only, never in Orders Alerts (a listing that still holds what the guard
  set is not announced again — both 11 Nov 2026) and adds the code to Restock Watch. Every 30 min it asks Zoro again, and
  when the main code or an alternative is buyable for 3 units of the listing it puts back what it
  changed itself (quantity → `prev_value`; handling time → `prev_value` or the store's default) —
  only while the field still holds what the guard set. Two placers of one store take a row with
  an atomic status UPDATE. `listing_guard.armed: false` in `bot_config.json` makes it alert only,
  `restore: false` stops the putting back.
* **Tracking uploaders** — `engine/tracking.py`, HOANK on 9335, Hathoro on 9336, Wrenchly on 9339 (their own Chrome
  since 3 Nov 2026; 9331 / 9333 / 9337 are the closing chain's, and closing never waits for a tracker). All-orders report
  (reused for 30 min), `archive_tracking` → one batched Gmail sweep → PDF, upload on Confirm
  Shipment, "shipped" buyer message. Since 26 Sep 2026 it records what it uploaded in
  `order_placer_orders.tracking_id_uploaded` and `action_log` (`action_type='tracking_upload'`).
  An **OOS** order gets the out-of-stock buyer message only once its ship-by date is at most 1 day away
  (`messaging.oos_message_days_before_ship_by`, 12 Nov 2026) — Zoro may restock before then.
  **Fast mode** (whenever Skip messages is ticked, 3 Nov 2026): one finder thread and two upload windows
  run side by side (`engine/tracking_crew.py`); no buyer message, seller note or WhatsApp alert, no
  1,000-order cap. `TRACKING_FAST=0` keeps the normal cycle.
* **Closing chain** — `scripts/daily_bulk.py` (▶ Run Closing, or auto-closing from `bot_config.json`).
  It keeps running when the launcher closes and the next launcher picks it up (since 3 Nov 2026,
  `data/closing_runs.json`), so closing the launcher does NOT stop closing: use its Stop button.
  Every other worker stops however the launcher is quit (window X, Cmd+Q, Dock Quit, closing the
  Terminal, Ctrl+C), and placers, trackers and Walmart left by a launcher that died are stopped at
  the next close (9 Nov 2026).
  Steps:
  Gmail archive fetch + incremental sync → `closing.py` **report-diff ingest** (only new / changed /
  new-evidence orders get a pass) → placer sync → `scripts/reconcile_and_reenrich.py` →
  exception-only SC scrape (`scripts/scrape_sc_fields.py`) → **delivery check through Zoro's own
  order tracking** (`scripts/zoro_delivery_check.py`, since 27 Sep 2026 — no carrier key, no
  Chrome; `docs/ZORO_TRACKING.md`) → live-Gmail gap-fill → reconcile →
  messaging (`scripts/send_delivery_messages.py`) → coverage. Telemetry: `closing_run_log`
  (`/api/runs` on the dashboard).
* **Dashboard** `dashboard/server.py` (:8000, `v2.html`) — opens on the **Daily report**
  (`dashboard/month_report.py`, 28 Sep 2026): one month, one row per Amazon (US/Pacific) day,
  counted from Amazon's own all-orders report so the totals match Seller Central, with labels,
  delivery states, the reason each open order is open, and a drill-down per day. Its counts come
  from `amazon_report_orders`, which `scripts/sync_amazon_report.py` fills from every report the
  tracker and closing download (closing itself keeps only WB orders). **Cloud agent**
  `scripts/cloud_agent.py` (heartbeat, slim DB export to R2, remote commands, WAL checkpoint every
  30 min); **hourly** `scripts/auto_sync_10min.py` (Gmail fetch, incremental archive sync, placer
  sync, WhatsApp insights, log rotation, Amazon report capture, the daily Zoro delivery sweep,
  the Gmail organizer). **Repricer scrape + listing watch**: every 12 h the dashboard runs
  `scripts/generate_<store>_offers_report.py` over the listings that sold in 30 days; since 10 Nov
  2026 `utils/listing_watch.py` reads brand, rating, number of ratings and category off the same
  page and sends one WhatsApp message per store with what changed on the own-brand (PL) listings,
  other sellers included (`data/pl_watch_state.json`). **Gmail organizer** `scripts/gmail_organizer.py` (31 Oct 2026): ZUHRA/…
  labels on every inbox from `config/gmail_rules.json`, routine mail archived; in the forwarding hub
  mkamrankhan342 one folder per source Gmail. Acts only on inboxes with a
  `<inbox>_modify_token.json` (`--authorize`); never deletes; no reader searches by inbox or label.
* **Data** — `data/automations_unified.db` (master, WAL), `data/gmail_archive.db` (email archive),
  the Google Sheet (SKU map / coupons / priority — team-edited, sacred), `config/accounts.json`
  (accounts, inboxes, carrier API keys), `bot_config.json` (runtime knobs).

Order lifecycle: Amazon sale → placer buys on Zoro (WB id) → Zoro "received" / "shipped" emails
→ tracker uploads tracking + messages the buyer → Zoro's tracking confirms delivery → closing labels
the order (Completed / Loss / Refunded / Cancelled / Incomplete) → delivery check-in the day of
delivery, review request 7 days later and not before Amazon's deliver-by date + 6 days (Amazon
counts its 5–30 day window from that date, not from the delivery; an early request is refused and
is stored "Not Yet", never "Expired" — `engine.post_delivery.review_wait_days`, 10 Nov 2026), and
one delay notice when a shipment has had no carrier
scan for 3 days (all in `bot_config.json → messaging`). Late (2 d past deliver-by) and stalled
(3 d) shipments, Zoro-cancelled orders and untrackable freight sit under Attention → Delivery.

---

## 4. Working rules for agents

* Inspect the DB read-only: `sqlite3 "file:Automations/data/automations_unified.db?mode=ro"`.
  Any purge / VACUUM: backup first (`scripts/slim_database.py --apply` does), placers stopped.
* Never open a tab in a running placer's Chrome (9221–9224). There are no test scripts: the
  AI-written `scripts/test_*.py` were removed on 31 Oct 2026 — don't add new ones to `scripts/`.
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
| Delivery status source | `engine/zoro_tracking.py` + `scripts/zoro_delivery_check.py` — Zoro's guest order tracking (`docs/ZORO_TRACKING.md`); `engine/fedex_api.py` (FedEx/UPS APIs) only with `DELIVERY_SOURCE=carrier` and keys |
| Messaging guards | `engine/post_delivery.py` (`prefetch_guards`, `process_single_order`) |
| DB size / WAL / backups | `scripts/slim_database.py`, agent `wal_checkpoint`, `~/.zuhra/backups` |
| Which WhatsApp number sends an alert | page http://127.0.0.1:3005, `whatsapp_gateway/server.js` (`LANES`, `routes.json`), `docs/WHATSAPP_SENDERS.md` |
| Run durations | `closing_run_log`, `/api/runs` |
| How many orders Amazon took on a day, placed or not | `amazon_report_orders` (US/Pacific `purchase_day`), `/api/month_report` |
| Alerts | hourly: `dashboard/insights.py` + `scripts/send_insights_alerts.py` (rules at the top of `docs/ALERTS_AND_INSIGHTS_SPEC.md`); at placement: `utils/insights_engine.py`; sending: `utils/whatsapp.py`, `utils/telegram.py` |
| A listing still selling with Zoro out of stock | `listing_oos` table, `python -m engine.listing_guard` (the list), `engine/listing_guard.py` |
| Change one listing's quantity or handling time by hand, the way the placer does | `python -m engine.offer_edit --port 9331 --asin … --sku … --field quantity|handling_days --value N` (`--dry-run` saves nothing); its docstring has the page's ids and what the live runs showed |
| Other sellers / rating / reviews / category on our own-brand listings | `utils/listing_watch.py`, `data/pl_watch_state.json`, the line `[listing watch]` in `data/repricer_live.log` |
| Listing changes on the Insights tab (off sale / back on sale, own-brand sellers, rating, reviews, category) | `listing_events` table ← `utils/listing_events.py`; shown by `dashboard/insights.py` CATEGORY 8; `scripts/send_insights_alerts.py` skips these kinds because their writers message the Insights group at once |
| Undelivered orders of a Zoro account (robstark771, abecomllc26 …) for a date range, each first re-checked: found / cancelled / already refunded | launcher 📋 Undelivered (closing panel) → `scripts/undelivered_report.py` → `reports/undelivered/*.xlsx`; Zoro "Cash Refund" e-mails matched by SO number or the buyer's Amazon phone code, cached in `data/zoro_cash_refunds_cache.json` |
| What the 10 Nov 2026 audit found and what was fixed | `Automations/data/audit/AUDIT_2026-11-10.md` (§9 = applied; kept out of git on purpose) |
