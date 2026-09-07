# ZUHRA Dashboard v2 — API contract

Served by `dashboard/server.py` (stdlib HTTP, JSON). Same origin as the page.
`GET /` serves `dashboard/v2.html` (this page); `GET /legacy` serves the previous UI.

## Auth / roles
Cookie-based. A request from `127.0.0.1` with a `localhost` Host header is **admin**
automatically (no login). Remote users log in at `/` (POST `/login`, field `pw`) and get
one of three roles: `admin`, `viewer`, `restricted`. `GET /api/whoami` → `{name, role, ...}`.
`POST /api/whoami {name}` saves this browser's display name (used as `author` on writes).
Admin-only: `/api/reminders`, `/api/pnl*`, `/api/travel_export`, `POST /api/reminder*`,
`/api/bank_reconcile`. `restricted` cannot POST `/api/ops/command`, `/api/config`, `/api/cell`.

## NEW — ops layer (dashboard/ops.py)

### `GET /api/ops/machines`
```json
{"host": "kamis-macbook", "cloud": {"cloud_mode": false, "r2": true, "host": "...", "manifest": {...}|null,
                                   "refreshed_at": null, "error": null},
 "machines": [ {
   "host": "kamis-macbook", "os": "Darwin", "online": true, "age_s": 3, "is_me": true, "local": true,
   "git_sha": "3c0e698", "git_branch": "main", "agent_started": "2026-09-07T01:00:00Z",
   "launcher": {"alive": true, "pid": 123, "started_at": "2026-09-07T00:10:00",
                "procs": {"orders_HS_COUPON": {"pid": 456, "tag": "orders_HS_COUPON_HS_COUPON", "since": "..."},
                          "tracking_hoank": {...}, "bulk_hoank": {...}},
                "closing": {"auto": {"enabled": false, "times": ["03:30"], "accounts": ["HOANK","Hathoro"], "every_hours": 0},
                            "last_auto_runs": {"HOANK": "2026-09-07 03:30"}},
                "headless": false},
   "placers_running": ["HS_COUPON", "HS_RESELLER"],
   "memory": {"total_mb": 8192, "used_pct": 71.2, "available_mb": 2300, "chrome_rss_mb": 2100, "python_rss_mb": 600},
   "dashboard": {"local_up": true, "port": 8000, "urls": {"cloudflared": "https://xxx.trycloudflare.com"}},
   "db": {"master_mb": 1643.2, "slim_mb": 119.5, "last_slim_upload": "2026-09-07T01:05:00Z", "last_backup": null},
   "last_placement": {"raw_time": "2026-09-06 23:09:37", "wb": "WB6424134927", "profile": "HS_RESELLER",
                      "amazon": "112-6641246-0709040", "asin": "B0GJG2QCQC"},
   "lease_holder": "kamis-macbook"
 } ] }
```
Process-name conventions in `launcher.procs`: `orders_<PROFILE>` (PROFILE ∈ DM_COUPON, DM_RESELLER,
HS_COUPON, HS_RESELLER), `tracking_hoank` / `tracking_hathoro`, `bulk_hoank` / `bulk_hathoro`
(closing), `messages_hoank` / `messages_hathoro`, `coupon`, `listings`, `walmart`.
DM_* profiles = Hathoro store, HS_* = HOANK store.

### `GET /api/ops/live_log?host=<host>&n=400`
`{"host": "...", "at": "2026-09-07T01:05:10Z", "lines": ["2026-09-07 01:05:09  [orders_HS_COUPON] ▶ …", ...],
  "launcher_alive": true, "procs": ["orders_HS_COUPON"], "source": "local"|"r2", "age_s": 4}`
Lines are the launcher console (`[source]` tag after the timestamp). Poll every 5 s.

### `POST /api/ops/command`  `{"action": "...", "target": "<host>|null", "args": {...}}`
→ `{"ok": true, "id": "abc123def456", "target": "kamis-macbook", "result": "…"}` or `{"ok": false, "error": "…"}`.
`target: null` = auto (placement-lease holder → machine with a live launcher → first online).
Actions (`GET /api/ops/actions` lists them):
- launcher: `stop_all`, `stop_orders` (args `{profile}` optional), `start_orders` `{profile}`, `start_all_orders`,
  `stop_process` `{name}`, `start_tracking`/`stop_tracking` `{account: "hoank"|"hathoro"}`,
  `run_closing`/`stop_closing`/`run_messages` `{account: "HOANK"|"Hathoro"}`, `reload_config`,
  `shutdown_launcher`, `set_headless` `{on: bool}`
- agent: `ping`, `start_launcher`, `sync_now` (slim DB → R2 now), `backup_now`, `restart_dashboard`,
  `run_priority_check` `{profile: "HS_COUPON"|"DM_COUPON"}`, `set_config` `{set: {path: value}}`
Commands to another machine are queued in R2 and picked up within ~10 s; results appear in
`GET /api/ops/results?limit=40` → `{"results": [{"id","action","ok","result","host","done_at","created_at"}]}`.

### `GET /api/config` / `POST /api/config`
GET → `{"routing": {"deciding_min_margin_pct": 5, "supervise_unlabelled": true, "supervise_min_cart_for_regular": 50,
        "promo_ineligible_asins": [], "auto_learn_promo_ineligible": true, "promo_ineligible_min_fails": 2},
        "chrome": {"headless": false},
        "automation": {"auto_closing": {"enabled": false, "times": ["03:30"], "accounts": ["HOANK","Hathoro"], "every_hours": 0},
                       "priority_check": {"enabled": true, "every_hours": 6, "auto_assign": false}},
        "deciding_coupon_min": 50, "pi_target_margin": 10,
        "profiles": {"DM_COUPON": {"target_label": "MR", "min_margin": -2, ...}, ...},
        "editable": ["automation.auto_closing.enabled", ...], "cloud_mode": false, "host": "..."}`
POST `{"path": "automation.auto_closing.enabled", "value": true}` or `{"set": {path: value, ...}}`
→ `{"ok": true, "applied": {...}}` (cloud mode: `{"ok": true, "id": ..., "result": "queued …"}`).

### `GET /api/timeline/<amazon_order_id>`
The order-centric view. `{"order_id", "found": true, "summary": {...}, "events": [...], "details": {...}, "placer": {...}}`
- `events[]`: `{"at": "2026-06-05 15:30", "at_display": "Thursday, 5 June 2026 15:30", "kind": "amazon|zoro|ship|fedex|message|return|feedback|action|note|case", "title": "...", "detail": "...", "icon": "🛒", "ok": true}` — chronological.
- `summary`: account, profile_name, order_label (Completed/Incomplete/Cancelled/Loss/Refunded/Manual/NotPlaced/Pending),
  order_status, asin, sku, title, quantity, earnings, amazon_fees, zoro_order_total, zoro_tax, profit_dollars, profit_percent,
  net_profit_after_refund, coupon_used, coupon_savings, wb_order_id, tracking_ids, delivery_status, delivery_date,
  ship_name/street/city/state/postal_code, purchase_date, ship_by_date, deliver_by_date, return_requested, refund_done,
  case_status, case_type, case_resolution, flag_color, tags (comma list), comments, manual_review, placed_via, placed_at, pending (bool)

### `GET /api/crm_history?order=<id>&limit=200`
`{"history": [{"id", "amazon_order_id", "event_id", "kind": "note|field|cell", "field", "old_value", "new_value", "author", "host", "created_at"}]}`
Append-only audit of every CRM change (who changed what, when, from which machine). Without `order` → latest across all orders.

### `GET /api/reconcile?days=30`
`{"days": 30, "since": "2026-08-08", "counts": {"gmail_sold_emails": 100|null, "closing_orders": 99, "placer_orders": 98,
  "closing_with_wb": 97, "tracking_found": 95, "tracking_uploaded": 95, "delivered": 87, "delivery_msg_sent": 82,
  "review_requested": 71, "returns": 3, "refunds": 2, "earnings_missing": 4, "not_yet_closed": 12},
  "missing": {"in_gmail_not_in_closing": [...ids], "in_closing_not_in_gmail": [...], "placed_not_closed": [...]}, "notes": [...]}`

### `GET /api/cloud_status` → same as `machines.cloud`.

### `GET /api/placer_events?hours=24&status=SUPERVISED,ROUTED_TO_RESELLER,ROUTED_TO_COUPON&limit=500`
The "why did the bot do that" feed from the placer run logs (local mode only; `available: false` in cloud).
`{"hours": 24, "available": true, "events": [{"time", "run", "profile": "HS_COUPON", "amazon_id", "status": "ROUTED_TO_RESELLER", "asin", "sku", "buyer", "state", "notes": "Supervisor: no priority coupon applies; cart $32.10 < $50 → routed to HS_RESELLER ('Rajar')"}]}`
Statuses worth surfacing: SUPERVISED, SUPERVISOR_RETRY, ROUTED_TO_RESELLER, ROUTED_TO_COUPON, NEEDS_PRIORITY, UNMAPPED_ASIN, LOW_PROFIT, OOS, DUPLICATE, COUPON FAIL, DUP_CHECK_UNVERIFIED, SCRAPE_FAIL.

## EXISTING endpoints you should reuse (read `dashboard/server.py` for exact params / shapes)
- `GET /api/facets` — filter values (accounts, labels, states, tags…)
- `GET /api/orders?…` — order rows from `v_orders_all` (+CRM overlay). Filters via `_filters()`:
  account, label, from, to, search/q, tag, flag… `src=placed` → placer rows (`placed_at` desc), `flag=unresolved`.
  Up to 5000 rows; render incrementally / virtualise.
- `GET /api/order/<id>` — one order (raw columns).  `POST /api/note {order_id, note}`  `POST /api/order_update {order_id, case_status|case_type|case_resolution|resolved_at|reminder_at|reminder_note|flag_color|manual_delivered|comments|tags}`
- `GET /api/severe`, `/api/refund_issues`, `/api/data_health`, `/api/today`, `/api/overview` (KPIs), `/api/ship_perf`, `/api/handling`, `/api/insights`, `/api/watch` (+POST add/del/clear), `/api/resolve`
- `GET /api/unmapped`, `/api/needs_priority` (live placer panels), `/api/tracker`, `/api/listings` (+`POST /api/listing_label`), `/api/walmart`, `/api/repricer?account=`, `/api/asin/<asin>`, `/api/asins`, `/api/raw`, `POST /api/cell`
- `GET /api/reminders`, `POST /api/reminder`, `POST /api/reminder_update` (admin), `GET/POST /api/pnl`, `/api/pnl_hoank`
Removed: `/api/chat` (returns empty / 410) and `/chat_media/`.

## Analytics (added 8 Sep 2026) — `dashboard/daybook.py`

All three honour `account`, `from`, `to` (YYYY-MM-DD), `profile`, `zoro_account`, `label`.

| Endpoint | What it returns |
|---|---|
| `GET /api/daybook?days=14&datecol=purchase\|placed\|shipped\|delivered` | Orders grouped by day (newest first). Each order carries money, dates, tracking, coupon, CRM fields and `s`, the derived lifecycle flags: `placed, pending, shipped, delivered, tracking, late_ship, late_delivery, cancelled, refunded, returned, loss, incomplete, tracking_msg_sent(+tracking_msg_at), delivery_checkin(+at), oos_notice, cancel_ack, buyer_wrote(+buyer_msgs), has_case, refund_at, return_at, refund_amount, return_reason`. Per-day `counts` + `money`, range `totals`, and `freshness` (last placed / last closing enrichment / last buyer, return and tracking emails). Sources: `v_orders_all` (closing ∪ placed-but-unclosed), `order_crm`, `archive_messages` (tracking_sent, delivery_checkin, oos_notice, cancellation_ack, buyer_inquiry/reply), `archive_returns` (dated refunds/returns — `closing_orders.refund_date` is always empty). |
| `GET /api/daybook.csv` (same params) | The same rows as a CSV attachment, `date` column first, one row per order with every flag as a column. |
| `GET /api/growth` (default last 30 days) | Period vs the previous equal period: `summary`/`prev_summary`/`compare`, `daily`, `weekly`, `weekdays`, `hours` (placement hour), `profiles`, `accounts`, `zoro_accounts`, `coupons` + `coupon_total` (priority = multi-use, regular = single-use), `asins` (top_profit, top_volume, losers, trouble, new, growing, fading), `refunds` (reasons, asins, cancel_asins, net_after_refund), `shipping` (on-time %, early/on-time/late, placement / time-to-ship / Zoro ship hours, purchase→delivered days, tracking-message coverage, carriers, weekly late rate), `buyers` (repeat buyers, business share), `states`, `margin_dist`, `freshness`. |

Reading rule for the UI: orders purchased after `freshness.last_closing_enrichment` show as pending / not shipped until the closing pipeline runs — that is data lag, not a late shipment.
