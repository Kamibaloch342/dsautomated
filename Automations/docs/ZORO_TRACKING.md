# Delivery tracking through Zoro (27 Sep 2026)

FedEx's public tracking went behind Akamai on 19 Sep 2026 and FedEx refuses a production
Track API key to a non-shipper. Since 27 Sep the delivery source is **Zoro's own order
tracking** — the guest "Track My Order" lookup — which shows, for a WB order and the e-mail
of the Zoro account that placed it, every parcel's FedEx scan history. Zoro whitelisted the
reseller user agent (`utils.helpers.zoro_user_agent()`): no captcha, no limit, headless OK.

## Pieces

| Piece | File | What it does |
|---|---|---|
| Client + parser | `engine/zoro_tracking.py` | `ZoroTracker.track_order(WB, email)` → order facts, one package record per parcel, order-level aggregate in closing's vocabulary (`Delivered / AllDelivered / PartialDelivered / InTransit / OutForDelivery / DeliveryException / LabelCreated / Untracked`). Transports: `http` (requests + reseller UA, default) and `browser` (headless Chrome on port 9225 carrying the reseller identity, `apply_zoro_identity`); `auto` switches after two refusals. |
| Sweep | `scripts/zoro_delivery_check.py` | Every shipped, undelivered order of the last 60 days (rechecked every 20 h), 4 workers, 0.4 s global pace. Writes `closing_orders` + `zoro_package_tracking`, then profit + label through `closing.calculate_profit` / `determine_label`. Lock `zoro_delivery_<ACCOUNT>`. Exit 2 = Zoro refused the transport (nothing marked). |
| Chain | `scripts/daily_bulk.py` step 5 | Runs the sweep for both stores (`DELIVERY_SOURCE=carrier` restores the FedEx/UPS API path). |
| Launcher | `gui/launcher.py` | "🚚 Delivery check" button (process `delivery_all`); remote commands `run_delivery_check` / `stop_delivery_check`. |
| Dashboard | `dashboard/server.py` `GET /api/delivery_watch?account=&counts=1`, `v2.html` Attention → **Delivery** | Buckets: **Zoro cancelled**, **Late** (undelivered ≥ `late_days` past the Amazon deliver-by), **Stalled** (no carrier scan for ≥ `stalled_days`), **Freight / untracked** (context, not an alert). Home "Attention" card carries the counts. |
| Messaging | `engine/post_delivery.py`, `scripts/send_delivery_messages.py` | Check-in **on the delivery day** (0–7 days), review request **6–30 days** after delivery, **one delay notice** when a shipment has had no carrier scan for 3–10 days (older stalls are a person's case). `bot_config.json → "messaging"` overrides the numbers. |
| Config | `config/accounts.json` → `gmail_addresses`, `zoro_profile_inboxes`, `zoro_tracking` | token → mailbox address; placer profile → Zoro inbox; `{transport, port, delay_s, days, recheck_hours, late_days, stalled_days, workers}`. |

## The contract (captured from the page, verified on live orders)

1. `GET https://api.prod.zoro.com/v1/order/<WB>?email=<sha256 hex of the lower-cased e-mail>`
   with header `apikey: <Zoro's public front-end key>` (the one `platforms/zoro.py` has always sent).
   401 without the hash, 404 `{"type":"orderNotFound"}` for a wrong order/e-mail pair.
   Payload: `orderStatus` (`Complete` | `Cancelled`), `shippingAddress`, `orderSummary`
   (`subTotal, shippingTotal, freightTotal, discountTotal, taxTotal, total, promoCode`),
   `shippingSummary.deliveries[].parcels[{trackingNumber, carrier}]` (codes seen: `FDEG`,
   `FDX GROUND`, `FDX GROUND TMS`, and freight names such as `WARD TRUCKING`, `DAYTON FREIGHT`,
   `US CARGO`, `DELIVERY SOL OF AMERICA`), `lineItems[].latestExpectedDeliveryDate`.
2. `GET https://api.prod.zoro.com/customer/v1/shipment-tracking/package/<TRACKING>?carrier=<code>`
   Payload: `stepTimestamps[{stepName: shipped|delivered, timestamp?}]` (a `delivered` step
   **without** a timestamp is not a delivery), `scanEventsDetails[{date, eventDescription,
   scanLocation{city, stateOrProvinceCode, postalCode}, exceptionDescription}]` newest first,
   `latestLocation | null`, `estimatedDeliveryDay`, optional `error`. A "Delivered" scan
   carries the drop-off note in `exceptionDescription` ("Left at front door…"); a real
   exception reads e.g. "Unable to deliver - Damaged package". Freight carriers answer
   400 "Carrier not support".

Timestamps keep the carrier's **wall-clock** time (the offset is dropped, not converted) so the
buyer's "Delivered 11:43 AM" is what we store; `delivery_date` keeps the FedEx-page wording
(`Wednesday, 6/03/2026 3:07 pm`) that closing, messaging and the dashboard already parse.

## Rules the sweep follows

- An order is **Delivered only when every parcel is** (`AllDelivered` for multi-parcel);
  some delivered → `PartialDelivered`; otherwise the best-ranked parcel status.
- `delivery_status` never moves backwards unless the new answer is a delivery.
- Which e-mail: `closing_orders.zoro_gmail_account` / `zoro_account_name` →
  `archive_zoro.gmail_account` → placer profile's inbox → the busiest Zoro inboxes
  (`archive_zoro` frequency), at most 4 tries; `not_found` after all of them is recorded on the
  row as `delivery_note = "Zoro: order not found under …"`.
- Zoro's `deliveries[]` lags the shipping e-mail by a day or more: when it lists no parcel,
  closing's own `tracking_ids` (FedEx-shaped numbers) are looked up directly with code `FDEG`.
- Freight parcels are recorded as `Untracked` with the carrier name on the order
  (`delivery_note`, `carrier`), never late, never stalled, never messaged. About 1 order in 8.
- `zoro_order_status = Cancelled` with Amazon still "Shipped" → loud note, own dashboard
  bucket: the buyer will not receive anything.
- Gap-fills from Zoro's own statement of the order (only where closing had nothing):
  `tracking_ids`, `coupon_used` (promo code), `coupon_savings`, `zoro_order_total /
  subtotal / tax / shipping`, `zoro_buyer_zip`, `zoro_sku / zoro_qty`, `zoro_deliver_by`.
- Recheck of a fully shipped order (`zoro_order_status = Complete` with parcels on file)
  skips the order call and refreshes the parcels only.

## Running it

```bash
../.venv/bin/python scripts/zoro_delivery_check.py                       # both stores
ZORO_TRACK_DRY=1 ZORO_TRACK_LIMIT=20 ../.venv/bin/python scripts/zoro_delivery_check.py
ZORO_TRACK_ORDERS=WB4184658338 ZORO_TRACK_FULL=1 ../.venv/bin/python scripts/zoro_delivery_check.py
../.venv/bin/python scripts/zoro_delivery_check.py --probe WB4184658338 salesmercentile@gmail.com
```

Env: `ZORO_TRACK_ACCOUNT` (ALL) · `ZORO_TRACK_DAYS` (60) · `ZORO_TRACK_LIMIT` · `ZORO_TRACK_RECHECK_HOURS`
(20) · `ZORO_TRACK_DELAY` (0.4) · `ZORO_TRACK_WORKERS` (4) · `ZORO_TRACK_TRANSPORT` (auto|http|browser) ·
`ZORO_TRACK_DRY` · `ZORO_TRACK_ORDERS` · `ZORO_TRACK_FULL` · `ZORO_TRACK_DUMP_DIR`.

First full sweep (27 Sep 2026): 19,749 shipped-undelivered orders of the last 60 days,
6 workers at 0.15 s → ≈0.35 s per order. Steady state is a few hundred orders per run.

## Messaging knobs (`bot_config.json → "messaging"`)

| Key | Default | Meaning |
|---|---|---|
| `delivery_message_days` | 0 | days after delivery before the check-in |
| `delivery_message_max_days` | 7 | no check-in for older deliveries |
| `review_days` | 6 | days after delivery before the review request |
| `review_max_days` | 30 | Amazon's window |
| `delay_notice_enabled` | true | |
| `delay_notice_stalled_days` | 3 | no carrier scan for this long → one delay notice |
| `delay_notice_max_stalled_days` | 10 | older stalls stay on the dashboard for a person |
| `delay_notice_max_days` | 45 | purchase age limit for a delay notice |
| `delay_notice_fresh_hours` | 36 | the tracking answer must be at most this old |

`MSG_DELAY_LIMIT` (100) caps delay notices per messaging run, next to `MSG_LIMIT` (300).
The notice never goes to a buyer who wrote to us, has a return/refund/cancellation in flight,
whose zip does not match Zoro's, or who already received one (Gmail "Your e-mail to" copy).

## What still needs a person

- Freight orders (Attention → Delivery → Freight / untracked): confirm by hand or through the
  freight carrier's own site; they stay `Incomplete` until then, exactly as before.
- "Zoro cancelled" orders: refund the buyer or re-place.
- Stalls older than 10 days and delivery exceptions (damaged, returned to shipper).
