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
* **Local mode** (the machine with the master DB): reads the DB directly; other machines'
  state comes from R2.
* **Cloud mode** (`CLOUD_MODE=1`, `render.yaml`): downloads the slim DB from R2 and
  refreshes it when the manifest changes; CRM writes become R2 events; commands go
  through R2. Deploy by connecting Render to the GitHub repo with `rootDir: Automations`
  and setting the secret env vars listed in `render.yaml`.
* Every CRM change (note, case field, cell edit) is appended to `crm_history`
  (who, what, old → new, when, from which machine). Team chat was removed.

## 5. Placement routing changes (`engine/orders.py`, `bot_config.json → routing`)

| Rule | Where | Config |
|---|---|---|
| **Promo-ineligible ASIN → always the reseller profile.** Sources: `routing.promo_ineligible_asins`, sheet column `Promo_Ineligible` in *Priority_ASIN_Control* (optional), and what coupon profiles learn (all coupons rejected on ≥ N separate occasions → `data/dataset/coupon_intel.json` → also appended to the sheet column) | `_is_promo_ineligible`, `_learn_promo_outcome` | `auto_learn_promo_ineligible`, `promo_ineligible_min_fails` |
| **Deciding ASINs**: cart ≥ `deciding_coupon_min` → coupon profile; cart below it → coupon profile **only if the reseller's estimated margin < `deciding_min_margin_pct`** (default 5 %), else reseller | `_deciding_verdict` | `deciding_min_margin_pct` |
| **Supervisor** (coupon profiles): unlabelled orders whose ASIN is mapped but not in Priority ASIN Control are queued **last** and decided on the live cart: a priority coupon applies → place; else cart ≥ `supervise_min_cart_for_regular` and a regular coupon applies → place with it; else → labelled for the reseller profile. Unmapped ASINs are never placed (unchanged rule). At most `supervise_max_per_cycle` (20) such orders per cycle so the backlog drains gradually and can be watched in the dashboard's *Bot decisions* feed. | `_supervise_coupon_decision`, `SUPERVISED` / `ROUTED_TO_RESELLER` run-log statuses | `supervise_unlabelled`, `supervise_min_cart_for_regular`, `supervise_max_per_cycle` |
| **Priority ASIN auto-check**: coupon placers verify new sheet ASINs between cycles (mapped? in stock? which coupon applies on a real cart? cart ≥ $50?) and write `Check_*` verdict columns into *Priority_ASIN_Control*; `auto_assign` can move the ASIN into the suggested column. Also on demand from the dashboard. | `engine/priority_check.py` | `automation.priority_check` |

Unit tests: `python scripts/test_routing_rules.py` (29 cases, no network).

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
