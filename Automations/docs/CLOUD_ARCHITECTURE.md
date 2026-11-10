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
| `db/master_backup.db.gz` | cloud agent (hourly; `quick_check` first; local copy `~/.zuhra/backups/master_latest.db.gz`) | you, when a machine dies | consistent snapshot of the whole master DB |
| `db/daily/master_<yyyymmdd>.db.gz` | cloud agent (server-side copy of the first backup each day, `backup_keep_days` 7) | you, when the latest backup is already damaged | one master snapshot per day |
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
`shutdown_launcher` leaves a running closing chain going (the next launcher picks it up); send
`stop_closing` or `stop_all` first to stop it.

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

**Wrenchly (third store, 6 Nov 2026) is outside these rules.** It has one placer, `WR_RESELLER`
(port 9226, Zoro account whose mail lands in abecomllc02, Amazon mail in wrenchlyllc). There is no
WR_COUPON, so nothing is routed: `utils.config.has_coupon_sibling()` is False, which switches off
Deciding routing, the late Coupon-column hand-off, the coupon-sweep hold and the '- D' coupon note.
`place_all_mapped`: every unlabelled Wrenchly order whose ASIN is in Verified_SKU_Dataset is placed
(no Priority_ASIN_Control column); an unmapped one is logged UNMAPPED_ASIN. Every other gate
(placement lock, duplicate check, margin, $800 '20', PO box, delivery time, OOS) is the same.
Store of a profile: `utils.accounts_config.store_for_profile()` (prefix → `profile_prefix` in
config/accounts.json) — never "not DM → HOANK".

**The owner's rules (7 Sep 2026), in the order the bot applies them to an unlabelled order:**

1. Product page says *"Zoro's best price: not eligible for any other offers or promotions"*
   → reseller, permanently (`platforms/zoro.py: check_pdp_promo`, cached per SKU in
   `data/dataset/pdp_promo.json`; read for free during the delivery-estimate page visit).
2. A **priority coupon** (the weekly codes) applies on the live cart → coupon profile. On a
   **Zoro Select** item only when it saves **more** than the reseller's 10 % Select discount
   (owner, 28 Sep 2026): a 10 % priority code such as ORGANIZE9T26 / FALL93A26 saves the same
   money → reseller (rule 3); a 15–20 % code → coupon. Both the supervisor
   (`_supervise_coupon_decision`) and the checker (`priority_check.check_one`) apply it through
   `utils.config.coupon_beats_select`, trying the next code when one only matches the 10 %.
   ASINs sorted under the old rule are re-tested with
   `python engine/priority_check.py HS_COUPON|DM_COUPON --queue ASIN,…` (queue only, no Chrome;
   the running coupon placer drains it between cycles — restart the placers first so they run
   the current rule).
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
  stays; Zoro Select or the page badge → Reseller; otherwise the price of ONE AMAZON UNIT (Zoro price × the pack size in the verified SKU sheet — corrected 20 Oct 2026, it was the single Zoro unit, so a $16 4-pack sat in Deciding) decides, as everywhere
  else — unit ≥ `supervise_min_cart_for_regular` ($50) → Coupon (a regular 10 % code is worth
  spending on every order of it), below it → Deciding (quantity and the reseller's margin decide
  per order). **Corrected 29 Sep 2026**: from 14 to 29 Sep this branch answered Deciding whatever
  the unit price, which contradicted rule 4 and the labelling flow, and the sweep kept pulling
  $50-plus ASINs back out of Coupon — 22 of them (all bot-placed, up to $224.99 a unit), each
  order of those taking a needless trip through the reseller. A further 18 Deciding ASINs reach
  $50 only through quantity and stay Deciding, which is what the column is for; the reseller
  made 443 ≥$50 hand-offs in the week to 29 Sep between the two groups. While the sweep runs, a store-level flag
  (`data/dataset/priority_sweep_<STORE>.json`) makes the RESELLER hold its priority/deciding
  discovery, so no order is bought at reseller price minutes before its ASIN moves; labelled orders
  still run, and a flag older than `sweep_hold_max_min` (90) is ignored. A sweep that changed the
  sheet restarts the coupon placer's cycle with fresh columns. Moves carry a Sheets cell note
  ("ZUHRA · time · reason · by profile"). Every checker call first makes sure Chrome is on zoro.com
  with a live token (cold start used to answer `no_token` for the first batch). Realistic speed:
  about 10–25 s per ASIN, so a 200-ASIN store takes roughly 30–80 minutes, during which the reseller
  waits rather than guesses.
* **Place with Priority (focus coupon).** Launcher checkbox per coupon profile (`profiles.<P>.
  focus_priority_coupon_enabled` + `focus_priority_coupon`, or `--focus-coupon CODE` on the command
  line). While on, the coupon placer queues ONLY orders whose ASIN takes that coupon: kept when the
  bot's memory says so (placed first), then EVERY ASIN without fresh memory is probed live on a
  scratch cart in one pass and the passers are placed immediately; deferred when a recent verdict
  says another coupon / Select / none. The
  code is resolved against today's Coupons tab (shorthand `facility` works; an unknown code places
  nothing and says so). At checkout the focus coupon is tried first; if it fails on the live cart
  the normal order still applies. Use it when a code is about to expire; untick it afterwards —
  deferred orders are placed normally then.
* **Sunday mode (11 Oct 2026).** Launcher checkbox on the Orders page (`bot_config.json →
  sunday_mode: {enabled, max_cart, min_margin_pct}`), for the day before the weekly priority
  coupons land (Monday ~10:00 PKT). It never does anything a placer would not do on a normal day —
  it only holds back: a **reseller** places an order only when its Zoro **cost** — the cart after
  any Zoro Select discount PLUS shipping or filler, the placer's "Est Cost" (owner: "shipping is a
  cost") — is under `max_cart` AND its estimated margin is at least `min_margin_pct`; a **coupon** placer strips
  regular codes before the supervisor or the checkout sees them (so no item can be called
  promo-ineligible either — that needs live regular codes), hands an order to the reseller only if
  it would pass the reseller rule, and never marks one '20' because no priority code applied today.
  An order whose EVERY item is coupon-blocked (the product-page badge, or the promo-ineligible
  list) is placed normally — no coupon can ever apply (`_all_coupon_blocked`).
  Everything else is logged `SUNDAY_HOLD` and waits **unlabelled**, so unticking releases it all at
  once. The reseller rule is judged on the pre-checkout estimate, not the checkout gate, because a
  failure there labels the order `Loss-Kamran checking` and it would never come back. Held orders
  are filtered out before the detail scrape and not re-priced until the numbers change, the
  PRIORITY pool changes (a regular scrape does not wake them), or 6 h pass. Manual-C/-R orders
  bypass it. Placers re-read the setting every order (15 s cache, last good value kept on a read
  error) — no restart. `engine/orders.py: _sunday_mode / _sunday_gate / _sunday_parked`.
  Wrenchly is exempt whatever the checkbox says (owner, 9 Nov 2026): reseller-only, no coupons to wait for.
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
* **A sweep is finished before the reseller guesses (20 Oct 2026).** On 19 Oct the new codes
  (ZSELECTJ1026 20 %, SUPPLIES11726 / MATERIAL81026 15 %, TOOLSG41026 10 %) were added at 10:42; the
  DM sweep ran logged out of Zoro and was restarted for hours, its 90-minute flag ran out with 94 ASINs
  untested, and HS_COUPON was not running at all until 19:05 — so DM_RESELLER (15:22) and HS_RESELLER
  (23:18) bought Zoro Select ASINs a 20 % code covered. Now: the reseller holds its priority/deciding
  discovery while the store's re-check queue is not empty (whatever the flag's age) or while the
  Coupons tab has priority codes the store's coupon placer has not swept yet (stopped, or labels-only);
  and it never buys an unlabelled order whose ASIN this store tested only before the current coupons,
  or whose last test got no answer — it queues the ASIN for the coupon placer and leaves the order
  unlabelled (`priority_check.sweep_hold_reason / reseller_must_wait`). With no priority code in the
  tab nothing is held (Sunday mode works as before). Labelled orders are never held.
* **"Zoro did not answer" is not a verdict.** The checker refuses to test when the profile is logged
  out (`zoro_session_ready` now honours `ensure_token`); a TRANSIENT / ERROR result keeps the ASIN in
  the queue (back of the line, try counted), never overwrites its last real verdict, and three in a
  row pause the sweep. After 6 tries the ASIN leaves the queue as UNTESTABLE, column unchanged.
* **The columns stay compact.** A move deletes the ASIN's old cell and shifts the column up (notes
  travel with their cells) and writes the ASIN into the first empty cell of the target column, in
  one atomic batchUpdate — it used to blank the old cell, leaving holes with orphan notes (256 of them
  by 20 Oct). `scripts/compact_priority_sheet.py` (dry run; `--apply` with the coupon placers stopped,
  backup of values + notes to `~/.zuhra/backups`) closes old gaps.
* **One Zoro account per profile (20 Oct 2026).** One account = one cart. On 19 Oct HS_COUPON was
  logged in to DM_COUPON's account (robstark771) and 3 orders went out with the other placer's item
  and/or address (WB7699391166, WB7993433455, WB8529119739). Every checkout now records its Zoro
  customerId (`data/dataset/zoro_accounts_live.json`); a profile that finds another running profile
  on the same account stops before touching the cart — `SHARED_ACCOUNT`, nothing bought, order
  retried, one alert an hour. "Running" is the recorded process itself, so a stopped placer never
  blocks; with no process to check (older record, other computer) it means seen in the last 15 min.
  **Exception by design:** Walmart and HS_RESELLER share `asad_amazon_us` and take turns. The
  launcher refuses to start one while the other runs, because HS_RESELLER's first cart build comes
  before this check (3 Nov 2026).
* **Dashboard checks reach both stores.** `Priority check / Re-check all / Reset` write one
  request file per coupon profile (`priority_check.<PROFILE>.request`).
* **The checker tests the SKU an order would buy (27 Oct 2026).** When the main Zoro code is out of
  stock or backordered and the sheet has an `alternative_SKU` in stock, the ALT is tested (its own
  PDP badge, brand and priority coupons) and the note starts `main G… Out of Stock → tested ALT G…`.
  It used to stop at "main OOS / Wait": B0HH6Q9FF3 stayed in DM_Deciding while every order shipped the
  ALT G310877337, which VEHICLE10926 covers. Main back in stock → the next check tests the main again.
* **Several alternatives in one cell (owner, 4 Nov 2026).** `alternative_SKU` holds one code or several
  with commas (`G5803393,G5612117`). With the main out, they are asked left to right and the first one in
  stock is placed, through every normal check (delivery date, coupons, the profile's profit gate). None in
  stock → OOS. If Zoro does not answer about one, the walk stops: no label, the next cycle asks again. An
  invalid code in the cell is skipped with a warning and the others still count (a comma used to void the
  whole cell). The placer, Ultra's stock check, Manual tags and the priority checker all read the same list.
* **Backordered = not in stock; the delivery date is Zoro's estimate for the buyer's ZIP
  (27 Oct 2026).** Zoro's availability API marks a backordered item `isCartable: true` with
  `availabilityStatus: "Backordered"` — the page only promises that a ship date will be e-mailed
  within 2 business days. `check_inventory` now returns it as not cartable, so the placer tries the
  ALT and otherwise labels the order OOS (G7467735, the pink hard hat for B0G14ZRRNH). For an in-stock
  SKU the delivery date comes from `POST /scm/v1/expected-date/bulk-expected-dates` (SKU, quantity,
  buyer ZIP — the call behind the page's "Delivery expected …" line; `platforms.zoro.get_expected_delivery`),
  falling back to the product page. An ALT swap reads the ALT's own date and is held under the same
  4-day "Delivery time high" rule before the cart is built; it used to drop the main's date and then
  sit in DELIVERY_UNKNOWN every cycle (27 orders, 218 tries, 24–26 Oct). The API dates out-of-stock
  items too, so it is only asked about SKUs the stock check passed. DM_COUPON's checkout page has never
  shown a date the pattern reads (0 since 19 Oct; 20 s wait per order) — `checkout.dom` now prints that
  page's delivery wording once per run so the pattern can learn it.
* **Buyer requested cancel → never placed (30 Oct 2026).** An order the buyer asked to cancel stays
  Unshipped; only Seller Central's banner ("Buyer requested cancel — The buyer has requested that this
  order be canceled") says so, and the order-page status check did not know that wording
  (112-4663939-9265025 was bought). `_extract_order_details` now looks for it on arrival and again after
  the address is read — in the page text, the Katal alert's attributes and its shadow root — and
  returns status `cancel_requested`: both placer loops skip it, log `CANCEL_REQUESTED`, and send one
  WhatsApp/Telegram alert per order per run to cancel it in Seller Central. The placer never cancels an
  order itself, and no tag places it.
* **Manual tags, three upgrades (30 Oct 2026, owner).**
  1. *Coupon line on a duplicate.* When the Gmail duplicate check writes `<WB> - D`, the order's Seller
     Notes get one line per item from the store's last priority check, believed only if made with the
     current priority codes: `C active - VEHICLE10926 (checked 28 Oct 14:05)`, `C none …` (no priority
     code / Zoro Select / Zoro's best price), `C untested …` (never checked, checked before the current
     codes, or Zoro did not answer). It is added below any note already there
     (`priority_check.coupon_status_note`, `orders._dup_coupon_note`, `write_seller_note(append=True)`).
  2. *Partial multi-item.* Manual-C/-R on a multi-item order: after checking Zoro's stock (main, then
     ALT), the items that are out are left out and the rest is bought — the buyer was told and agreed.
     Order log note `PARTIAL: <ASIN> not placed (<code>: <status>)`, Seller Notes code line `- PARTIAL`.
     All out → OOS as before; an inventory API error → no split. The tracker later confirms the whole
     Amazon order (owner's choice).
  3. *Zoro code in the tag.* `Manual-C G7467735` / `Manual-R G7467735` buys that code instead of the
     sheet's (another colour / size the buyer accepted) with the sheet's pack size and no ALT; the tag
     still picks the profile and its waivers. Multi-item: the code replaces the ONE item that is out; none
     or several out → not placed, `MANUAL_CODE_UNCLEAR`, one alert, tag kept. A bare `G7467735` is not a
     tag and places nothing. Amazon's `Manual-C/-R` search does not return a tag with a code after it, so
     these orders are found only in the buyer-action view (all 183 up to 3 Nov 2026). Priority discovery
     reads that view; under Labels Only a separate pass reads it for Manual tags and takes nothing else,
     which adds one scan of the view (about 2 min at 1,800 orders) to every cycle.
     *Unmapped ASIN (9 Nov 2026, owner).* The code in the tag is enough: an order line with no row in the
     SKU sheet is bought on that code instead of stopping at `UNMAPPED_ASIN` (some listings stay unmapped on
     purpose, because the right Zoro code differs from order to order). With no sheet row there is no pack
     size, so the quantity is Amazon qty × the `Npk` ending of the Amazon SKU (`Imran-G9084824-4pk` → 4 each),
     or 1 each when the SKU has no such ending. No ALT, no 2nd part. Multi-item: the code goes on the one
     unmapped line; two or more unmapped lines stay `UNMAPPED_ASIN`, since the code could be for any of them
     (`orders._fill_unmapped_from_tag`).
     *Quantity in the tag (9 Nov 2026, owner).* `Manual-C G5522807 x25` buys exactly 25 of that code for the
     order, whatever Amazon's quantity, the SKU's `Npk` or the sheet's pack size say, on a mapped or unmapped
     ASIN. Only after a code, 1 to 999. `x0`, `x1000`, `Manual-C x25` or `G5522807 25` are not tags, so the
     order is left alone. Multi-item: the quantity goes on the same line as the code (`orders._parse_manual_qty`).
  4. *Coupon in the tag (11 Nov 2026, owner).* `Manual-C SAVE20NOW` places the order with that coupon and
     no other: the sheet's priority and regular coupons, the focus coupon and the profile's
     "skip priority coupons" switch are all out of it. Made for the 20 % code Zoro gives by phone for an
     order the placer labelled `20`: replace `20` with `Manual-C <code>`. If Zoro does not accept the code
     nothing is bought: the order keeps its tag, is tried again after `coupon_wait_cooldown_min` (45 min) or
     at once when another code is written, and the Manual-C "no coupon" alert goes out once per order and
     code, naming the code and Zoro's reason (a reply that is only an API error sends nothing and is retried).
     The code is tried as typed, then in capitals. It may stand with a Zoro code and a quantity, in either
     order (`Manual-C G7467735 x25 SAVE20NOW`). 4–24 letters / digits with at least one letter; a word that
     looks like a Zoro code (`G123`) or a quantity (`x1000`), two words, or a code after `Manual-R` (a
     reseller applies no coupon) make the label not a tag, so the order is left alone. The profit gate stays
     waived as for every tagged order: whatever the code takes off, the order is placed. Nothing is learned
     from it (the checkout gets a cache of its own: no working pair, no promo strike set or cleared), and
     the page-checkout fallbacks are not used for such an order, because they keep whatever promo is already
     on the cart (`true_api … only_coupon`); a checkout that breaks before the order is sent is retried next
     cycle. Found like every coded tag: in the buyer-action view. `orders._parse_manual`,
     `_tag_coupon_codes`, `_tag_coupon_refusal`.
* **A WB on the buyer's other order is not a duplicate (4 Nov 2026, owner).** The duplicate check matches by
  address, so a real repeat purchase matched the WB of the buyer's earlier order and was stamped `<WB> - D`
  and never bought (112-7422969-0994666 got `WB5917587551 - D`, the WB of 112-9339236-7538642). Shrinking the
  lookback does not help, because customers do reorder within a day or two. Now, before `- D` is written, the
  placer opens the order page's "See all N orders from this buyer" link and reads the list
  (`orders._dup_is_buyers_other_order`, decision in `dup_check_local.wbs_on_other_orders`):
  - **Every WB must be accounted for,** not only the one the check answered with
    (`gmail.all_matched_wbs`: all local matches, plus all live-Gmail hits when the archive is stale). One WB
    that is not on another order and `- D` is written: it may be this order's own purchase with the
    write-back lost.
  - **Attached means** a live order on the list, not this one, whose label is nothing but WB ids, or
    whose `order_placer_orders` row has that WB (that covers a write-back still in the rush journal).
    `<WB> - D` on a sibling proves nothing on its own, since it can be the WB of the order being checked. A
    cancelled sibling or one the buyer asked to cancel proves nothing either: its WB may still be shipping
    to this buyer, so buying again is a person's call.
  - **Any doubt writes `- D`, as before:** no link, a page that won't load, a list that won't parse, this
    order missing from the list, this order already carrying or recorded with a WB, a search or database
    error, a Gmail hit with no WB id.
  - **The page in use is never left.** The link is read while scraping the order page (`buyer_orders_link`
    in the detail); the list opens in its own tab (Ultra: its own window, since a tab would hide the Zoro
    one), on the main thread, and closes again, so the classic loop's order page and the rush and Ultra loops'
    Zoro page are untouched. A link not drawn at scrape time gets one more look, 6 s at most.
  - **It is a span, not a link (fixed 7 Nov 2026).** Seller Central draws
    `<a><span data-test-id="shipping-section-buyer-history">See all N orders from this buyer</span></a>`
    with no href; clicking it loads `orders-v3/search?qt=email&q=<buyer relay e-mail>&date-range=…` in the
    same tab. The first reader looked only for `a[href]`, found nothing on any page, and all 17 orders it
    checked from 5 to 7 Nov stayed `- D` (14 were real repeats the owner then placed with Manual-C). Now the
    worker tab opens the order page and clicks the span (`orders._click_buyer_orders`; a new tab, should
    Amazon ever open one, is followed and closed). Verified on real pages for both stores.
  - **A failed checkout** is no longer "resolved" against the buyer's other order: instead of stamping that WB
    and confirming the claim with it, the order goes to Locked Orders for a person.
  - Unchanged: the placement lock (a claim on this Amazon id always wins, before and after this check), Manual
    tags (no duplicate search at all), the Walmart check. The buyer's e-mail differs per store, so a WB
    from the other store is not on the list and `- D` stands.
  Logged: run log status `DUP_OTHER_ORDER`, decision log kind `dup_other_order` (`cleared` / `stands` with the
  reason), console prefix `[DUP_OTHER]`.

## 6. Automation & memory

* **Auto-closing**: `automation.auto_closing` (`enabled`, `times` HH:MM list, `accounts`,
  `every_hours`) — the launcher starts the same chain as ▶ Run Closing.
* **Headless placers**: `chrome.headless: true` (or the Ops toggle) launches placer
  Chromes with `--headless=new`; an already-running Chrome on the port is reused as is.
* **DB slimming**: `python scripts/slim_database.py` (dry run) / `--apply` — backup first
  (to `~/.zuhra/backups`, outside the Drive mirror), offloads raw email bodies, purges old
  logs, VACUUM, WAL checkpoint.
* **WAL hygiene (26 Sep 2026)**: the agent runs `PRAGMA wal_checkpoint(TRUNCATE)` every 30 min
  (`agent_config.json → wal_checkpoint_s`) when no closing / bulk / messaging lock is held, and
  reports `db.wal_mb` / `db.last_checkpoint` in its heartbeat. Every connection sets
  `journal_size_limit` = 256 MB.
* **Run telemetry (26 Sep 2026)**: `closing.py` and `daily_bulk.py` write `closing_run_log`
  (counts, diff reasons, per-step seconds) — `GET /api/runs` on the dashboard.
* **Log rotation (26 Sep 2026)**: `scripts/rotate_logs.py`, run hourly by `auto_sync_10min.py`.

## 6b. Two computers: master + secondary (DB sync, 14 Sep 2026)

One machine is the **master**: its `data/automations_unified.db` feeds the dashboard (slim
export), the hourly backup and the CRM replay. Any other computer that runs the launcher or
closing is a **secondary**. Both run the same agent; the only difference is
`data/agent_config.json → "role"`.

```
python scripts/cloud_agent.py --role secondary   # on the other computer (once)
python scripts/cloud_agent.py --seed-from-cloud  # if it has no data/automations_unified.db yet
python scripts/cloud_agent.py --install          # then the agent runs at login as usual
python scripts/cloud_agent.py --status           # role + sync state of every machine
```

`utils/sync_journal.py` keeps the two databases in step through R2 (`sync/events/<host>/…`):

* triggers journal every INSERT/UPDATE on the order, closing, archive, claim, CRM and
  decision tables; the agent pushes batches every minute and pulls the other machines' batches;
* rows are matched by **natural key** (order id, WB id, run id …), never by autoincrement id;
* an UPDATE applies only the columns that changed there, so closing's tracking columns and the
  placer's placement columns on the same `orders` row never overwrite each other; an INSERT of
  a row both machines already have only fills empty columns; append-only tables (enrichment log,
  decisions, notes) arrive exactly once;
* nothing is ever deleted by sync; a change never bounces back to its origin; each batch is
  applied once (receipts in `sync_applied`).

So: place orders on the master, run closing on the secondary — within about a minute the
master's DB (and therefore the dashboard) has the tracking numbers, delivery states and
enrichment the secondary produced, and the secondary sees the master's new placements.
Not synced (by design): files under `data/` other than the DB (order_log.txt, run files,
console logs), Chrome profiles, Gmail/Sheets tokens — copy those once by hand. Only one
machine may place at a time (the R2 lease), and both must run the same git revision
(columns unknown to the receiver are skipped and reported in the agent log).

## 7. Repository & secrets

* `data/`, `*.db`, logs, TSV feeds, token files and passwords are git-ignored; the
  history was re-created clean (see the commit message).
* The old Render README that embedded R2 keys was **removed**. Rotate those keys in
  Cloudflare (Manage R2 API Tokens) and put the new ones in the LaunchAgent env or
  `data/All_Tokens/r2.json`.
