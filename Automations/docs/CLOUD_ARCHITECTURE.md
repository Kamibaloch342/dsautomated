# ZUHRA Cloud — how the pieces fit (September 2026 rebuild)

> The business logic (placers, tracking, closing) is unchanged and documented in
> `automations_complete_documentation.md`. This file describes the cloud layer
> added on top: **any computer can run the launcher, the dashboard is always
> current, colleagues can see the launcher console live and stop/start it from
> the dashboard, and everything except raw logs lives in Cloudflare R2.**

## 1. The bus: Cloudflare R2 (bucket `dsautomated`)

`utils/r2_store.py` is the only code that talks to R2. Key layout:

| Key | Written by | Read by | Purpose |
|---|---|---|---|
| `db/dashboard.db.gz` + `db/manifest.json` | cloud agent (every 5 min, only when data changed) | cloud dashboard (Render) | slim DB: 22 tables the dashboard needs, no raw email bodies |
| `db/master_backup.db.gz` | cloud agent (hourly, once the master DB ≤ 600 MB) | you, when a machine dies | consistent snapshot of the whole master DB |
| `state/machines/<host>.json` | cloud agent (every 15 s) | dashboard Ops page | launcher up? placers? memory, git version, last placement, dashboard URLs |
| `state/active.json` | cloud agent while placers run | launcher on every machine | **placement lease** — a second machine refuses to start placers while another holds it |
| `logs/live/<host>.json` | cloud agent (every 10 s) | dashboard live console | last 400 launcher console lines (`data/console_log.txt` tail) |
| `control/queue/*.json` → `control/done/*.json` | dashboard → agent | agent → dashboard | remote commands + acknowledgements |
| `crm/events/*.json` → `crm/applied/*.json` | cloud dashboard → agent | agent → master DB | notes / case edits made on Render, replayed idempotently |

Credentials are never in git: env `R2_*`, or `data/All_Tokens/r2.json`, or (macOS) the
LaunchAgent's `EnvironmentVariables`. `python utils/r2_store.py` prints usage.

## 2. Per-machine daemon: `scripts/cloud_agent.py`

Installed with `python scripts/cloud_agent.py --install` (macOS LaunchAgent
`com.zuhra.agent`; Windows logon task `ZuhraCloudAgent`). It replaced the two old
daemons (`com.zuhra.cloudsync`, `com.zuhra.dashboard`) which were retired by the installer.

It: heartbeats, publishes the console tail, executes/forwards commands, exports and
uploads the slim DB, replays cloud CRM events, holds the placement lease, backs up the
master DB, keeps `dashboard/server.py` alive on :8000 **without crash-looping** (it adopts
a server that is already up), and optionally supervises a tunnel
(`data/agent_config.json`: `localtunnel_subdomain`, `cloudflared`).

`python scripts/cloud_agent.py --status` shows what every machine reports.
Log: `~/Library/Logs/zuhra-agent.log`.

## 3. Launcher ⇄ agent protocol (`gui/launcher.py`)

* `data/launcher_state.json` — written every 5 s (pid, running processes, closing
  state, headless flag, git sha). Older than 20 s = launcher closed.
* `data/control/queue/<id>.json` — one command; the launcher executes it on its Tk
  thread within 2 s and answers in `data/control/done/<id>.json`.
* Before starting placers the launcher checks the R2 lease: if another computer's
  placers are live it refuses (dialog / command error). This is the cross-machine
  duplicate-order protection.
* `bot_config.json` is now **merged** on Save (keys the GUI doesn't edit survive).

Commands: `stop_all`, `stop_orders {profile}`, `start_orders {profile}`, `start_all_orders`,
`stop_process {name}`, `start_tracking / stop_tracking {account}`, `run_closing /
stop_closing / run_messages {account}`, `reload_config`, `set_headless {on}`,
`shutdown_launcher`; agent-side: `start_launcher`, `sync_now`, `backup_now`,
`restart_dashboard`, `run_priority_check {profile}`, `set_config {set:{path:value}}`, `ping`.

## 4. Dashboard (`dashboard/server.py` + `dashboard/ops.py` + `dashboard/v2.html`)

* `/` = v2 (order-centric timeline, case recording with full history, Ops page with
  machine cards, live console and settings); `/legacy` = the previous UI.
* New endpoints are documented in `dashboard/V2_API.md`.
* **Analytics** (nav item, was "Insights"; `dashboard/daybook.py`): *Day book* = orders grouped by
  day with every lifecycle state per order (placed · shipped · tracking · delivered · late ·
  cancelled · refunded/returned · tracking message sent · delivery check-in · buyer wrote · case)
  and a CSV download for the range or a single day; *Growth*, *Coupons*, *ASINs*, *Shipping*,
  *Refunds & cancels*, *Buyers & places* = period-vs-previous-period analytics; *Changes* = the
  anomaly feed; *P&L* = the monthly P&L forms for both stores. Attention → *Tracker* groups the
  team's flagged/tagged/commented orders by day of last activity with inline flag and case-status
  edits. Every analytics view shows how fresh each data source is: orders after the last closing
  run look pending/not shipped until closing runs — that is data lag, not a late shipment.
* **Local mode** (the machine with the master DB): reads the DB directly; other machines'
  state comes from R2.
* **Cloud mode** (`CLOUD_MODE=1`, `render.yaml`): downloads the slim DB from R2 and
  refreshes it when the manifest changes; CRM writes become R2 events; commands go
  through R2. Deploy by connecting Render to the GitHub repo with `rootDir: Automations`
  and setting the secret env vars listed in `render.yaml`.
* Every CRM change (note, case field, cell edit) is appended to `crm_history`
  (who, what, old → new, when, from which machine). Team chat was removed.

## 5. Placement routing changes (`engine/orders.py`, `bot_config.json → routing`)

**The owner's rules (7 Sep 2026), in the order the bot applies them to an unlabelled order:**

1. Product page says *"Zoro's best price: not eligible for any other offers or promotions"*
   → reseller, permanently (`platforms/zoro.py: check_pdp_promo`, cached per SKU in
   `data/dataset/pdp_promo.json`; read for free during the delivery-estimate page visit).
2. A **priority coupon** (the weekly 15–20 % codes) applies on the live cart → coupon
   profile — *also for Zoro Select*, because 15–20 % beats the reseller's 10 %.
3. No priority coupon and the item is **Zoro Select** → reseller (already 10 % off; a
   regular 10 % coupon would be wasted).
4. No priority coupon, cart ≥ $50 → regular 10 % coupon, coupon profile.
5. No priority coupon, cart < $50 **but** the reseller's estimated margin < 5 % → regular
   coupon anyway (avoid a loss).
6. Needs a coupon (rule 4 or 5) but none applied — typically because the day's single-use
   regular coupons are used up → the order **waits** (left unlabelled, retried next cycle /
   after the next coupon scrape). It is never sent to the reseller at full price and the
   ASIN is never called ineligible for that; a strike is recorded only when coupons were
   available and every one was rejected at item level, at most one strike per day
   (`routing.promo_strike_hours`), two strikes → ineligible. The PDP badge stays the
   primary ineligibility signal.
7. Otherwise (cart < $50, healthy margin) → reseller.

**Who the supervisor touches.** Only an ASIN that is in *no* column of its store
(`HS_*` for HOANK, `DM_*` for Hathoro). An ASIN a person put in `_Reseller` or `_Deciding`
is that profile's order, even when the coupon profile sees it first: it is skipped (counted as
"sorted in another column" in the discovery summary) and never coupon-tested on a live order.
If the sheet cannot be read and there is no last-known copy, the supervisor pauses for that
cycle rather than guess.

**Who may move a column.** A new ASIN is always placed (`_Coupon` / `_Reseller` / `_Deciding`
by the verdict). For an ASIN that is already in a column the checker (`engine/priority_check.py:
may_move`) moves it only when: a **priority coupon works** (promoted to `_Coupon` from anywhere —
the owner's rule), the **product page says ineligible** (→ `_Reseller`), the **bot itself** put it
there (it may re-sort its own placements), it sits in `_Coupon` and the run is a **re-check after
a coupon change** or the dashboard's *Re-check all*, or the run is *Reset & rebuild* (everything is
rebuilt). Otherwise a human placement stays and only the verdict is recorded. Who placed what
(`owner: bot|human`, column, coupon, cart, date) lives in `data/priority_verdicts.json`; the
reasons are in the decision log (`bot_decisions`, dashboard → Attention → Supervisor log).
Testing a `_Reseller` ASIN with the weekly priority coupons is harmless: it happens in a scratch
cart that is cleared afterwards, no order is placed and no single-use coupon is consumed.
Setting `automation.priority_check.write_sheet_notes: true` additionally mirrors the verdicts
into `Check_*` columns of the sheet (off by default — the sheet keeps its six columns).
When a **new priority coupon** appears in the Coupons tab, every ASIN of the store is queued
and re-tested with the new codes, 15 per placer cycle (`enqueue_recheck / run_queue`); permanent
PDP-ineligible ASINs are skipped unless *Reset & rebuild* is used. Zoro's coupon replies are
classified (`checkout/true_api.py: classify_promo_error`): only *"not valid for the item(s)"* and
*"needs to include at least one eligible <Category> item"* count as item rejections; *"$X of
eligible"* is a cart minimum (a Deciding signal); *"no longer available / has expired / Invalid
Promo Code"* retires the coupon; token and transient errors are retried and never counted.

| Rule | Where | Config |
|---|---|---|
| **Promo-ineligible ASIN → always the reseller profile** (and the ASIN is kept in `<store>_Reseller`: a memory-based route re-adds it if it was removed). Sources: `routing.promo_ineligible_asins`, sheet column `Promo_Ineligible` in *Priority_ASIN_Control* (optional), and what coupon profiles learn (all coupons rejected on ≥ N separate occasions → `data/dataset/coupon_intel.json` → also appended to the sheet column) | `_is_promo_ineligible`, `_learn_promo_outcome` | `auto_learn_promo_ineligible`, `promo_ineligible_min_fails` |
| **Deciding ASINs**: cart ≥ `deciding_coupon_min` → coupon profile; cart below it → coupon profile **only if the reseller's estimated margin < `deciding_min_margin_pct`** (default 5 %), else reseller | `_deciding_verdict` | `deciding_min_margin_pct` |
| **Supervisor** (coupon profiles): unlabelled orders whose ASIN is mapped but not in Priority ASIN Control are queued **last** and decided on the live cart: a priority coupon applies → place; else cart ≥ `supervise_min_cart_for_regular` and a regular coupon applies → place with it; else → labelled for the reseller profile. Unmapped ASINs are never placed (unchanged rule). At most `supervise_max_per_cycle` (20) such orders per cycle so the backlog drains gradually and can be watched in the dashboard's *Bot decisions* feed. | `_supervise_coupon_decision`, `SUPERVISED` / `ROUTED_TO_RESELLER` run-log statuses | `supervise_unlabelled`, `supervise_min_cart_for_regular`, `supervise_max_per_cycle` |
| **Priority ASIN auto-check**: coupon placers verify new sheet ASINs between cycles (mapped? in stock? which coupon applies on a real cart? cart ≥ $50?) and write `Check_*` verdict columns into *Priority_ASIN_Control*; `auto_assign` can move the ASIN into the suggested column. Also on demand from the dashboard. | `engine/priority_check.py` | `automation.priority_check` |

Unit tests: `python scripts/test_routing_rules.py` (43 cases) and `python scripts/test_priority_sheet.py`
(sheet writer + move policy against a fake Sheets service) — no network.

### 5a. Reliability rules for priority coupons (8 Sep 2026)

The owner's priority is "not a single order may miss a priority coupon", so these rules
are enforced in code, not by convention:

* **Priority coupons are never retired automatically.** A "no longer available / expired /
  invalid" reply for a weekly 15–20 % code is counted and shown (`priority_dead_replies`
  in the coupon intel), never written as INVALID and never deleted from the Coupons tab —
  only regular single-use codes are. Session/token expiry replies are classified as token
  problems, never as a dead coupon.
* **Best coupon first, priority before regular, always.** The loader sorts priority codes
  by percentage (fixed-amount codes after). Checkout tries every priority code before any
  regular one; a remembered code jumps the queue only when it is itself the top priority
  code, so a cached 10 % regular can never pre-empt a 15–20 % priority code. A code that
  "applies" with a $0 discount is not a success.
* **Bad API windows never downgrade an order.** If every priority coupon fails for
  session/transient reasons the checkout stops with `COUPON_RETRY`; the order stays
  unlabelled and is retried next cycle (claim released, no strike).
* **A coupon profile never places at list price.** An empty Coupons tab is a failed load
  (the cycle waits); an order with no coupon at all in the pool is logged
  `NO_COUPONS_WAIT` and retried.
* **A bad coupon-sheet read never re-sorts the store.** If the priority set reads back empty
  while it was non-empty before, no re-check is queued and the memory is kept; the checker
  never moves anything when it has no priority coupons; the supervisor records no verdict
  without them.
* **Shared memories are cross-process safe.** `utils/jsonstore.py` (flock + atomic write)
  guards `priority_verdicts.json`, `coupon_intel.json`, `pdp_promo.json` and the re-check
  queues; a corrupt file is moved aside, never overwritten in place.
* **The re-check queue survives crashes.** ASINs are removed one by one as they finish, a
  failing ASIN never aborts the batch, columns are judged as they are NOW (not as queued),
  rows are re-read under the lock right before a move, a pending Reset is never downgraded
  by a later coupon change, and an unreadable queue is rebuilt from the full store.
* **A promoted ASIN is not placed by the reseller.** Right before reserving an unlabelled
  order, the reseller checks the verdict memory: if the coupon placer promoted that ASIN to
  `_Coupon` after this cycle's sheet load, the order is handed to the coupon profile.
* **A sheet outage never hides orders.** If the priority column is unreadable the last
  known list is kept; with no last-known copy, discovery is off for that cycle instead of
  treating the profile's own ASINs as "sorted elsewhere".
* **Priority coupon lifecycle (14 Sep 2026).** The moment the Coupons tab changes, the coupon
  placer sweeps the store in ONE pass before it places anything (`automation.priority_check.sweep_max`,
  default 250): a code was **added** → every Reseller and Deciding ASIN is tested with the codes,
  highest percentage first (20 % before 15 %), and promoted to Coupon where one applies; a code
  **expired or was removed** → every Coupon-column ASIN is re-tested: another priority code works →
  stays; Zoro Select or the page badge → Reseller; otherwise → Deciding (whatever the unit price —
  the reseller hands a cart at/above $50 to the coupon profile per order and gets it back if the
  regular pool is dead, so nothing is left waiting). While the sweep runs, a store-level flag
  (`data/dataset/priority_sweep_<STORE>.json`) makes the RESELLER hold its priority/deciding
  discovery, so no order is bought at reseller price minutes before its ASIN moves; labelled orders
  still run, and a flag older than `sweep_hold_max_min` (90) is ignored. A sweep that changed the
  sheet restarts the coupon placer's cycle with fresh columns. Moves carry a Sheets cell note
  ("ZUHRA · time · reason · by profile"). Every checker call first makes sure Chrome is on zoro.com
  with a live token (cold start used to answer `no_token` for the first batch). Realistic speed:
  about 10–25 s per ASIN, so a 200-ASIN store takes roughly 30–80 minutes, during which the reseller
  waits rather than guesses.
* **Dead codes are not verdicts.** The checkout's failure summary is per tier; an expired code says
  nothing about the item. A priority code that three different carts report as expired is retired
  (marked INVALID) and should be removed from the tab. When no LIVE regular code is left and no
  priority code applies, an order goes to the reseller if the reseller margin is at least
  `deciding_min_margin_pct`, otherwise it waits — this amends rule 6 above (owner, 14 Sep 2026).
* **Never placed blind.** An order whose earnings are unknown (Gmail service down, "Sold, ship
  now" not parsed, zero) is never checked out, whatever the label or override says; it is logged
  `EARNINGS_UNKNOWN` and retried next cycle (owner's rule, 8 Sep 2026).
* **A coupon failure is a decision, not a loop.** When no coupon applies to a Coupon-column
  order the checkout leaves a `last_fail` summary and `_coupon_fail_decision` acts on it: every
  code rejected the item itself (regulars existed) → promo-ineligible now and the reseller places
  it; all Zoro Select with no priority code → reseller; cart below every minimum with a healthy
  reseller margin → reseller; otherwise the order waits. A waiting order is **parked**
  (`routing.coupon_wait_cooldown_min`, default 45 min) and retried the moment the coupon pool
  changes — one order was carted and coupon-tested 66 times in a night before this.
* **Unmapped priority ASINs cost nothing.** Discovery skips orders whose priority/deciding ASIN
  has no Zoro code in the verified SKU sheet, flagging the ASIN once per cycle for the Unmapped
  panel instead of loading every order page (926 loads for one ASIN on 8 Sep).
* **Dashboard checks reach both stores.** `Priority check / Re-check all / Reset` write one
  request file per coupon profile (`priority_check.<PROFILE>.request`).

## 6. Automation & memory

* **Auto-closing**: `automation.auto_closing` (`enabled`, `times` HH:MM list, `accounts`,
  `every_hours`) — the launcher starts the same chain as ▶ Run Closing.
* **Headless placers**: `chrome.headless: true` (or the Ops toggle) launches placer
  Chromes with `--headless=new`; an already-running Chrome on the port is reused as is.
* **DB slimming**: `python scripts/slim_database.py` (dry run) / `--apply` — backup first,
  drops raw email bodies (originals stay in `gmail_archive.db`), purges old logs, VACUUM.

## 7. Repository & secrets

* `data/`, `*.db`, logs, TSV feeds, token files and passwords are git-ignored; the
  history was re-created clean (see the commit message).
* The old Render README that embedded R2 keys was **removed**. Rotate those keys in
  Cloudflare (Manage R2 API Tokens) and put the new ones in the LaunchAgent env or
  `data/All_Tokens/r2.json`.
