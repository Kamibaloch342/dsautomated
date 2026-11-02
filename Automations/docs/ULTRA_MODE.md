# Ultra mode (2 Nov 2026)

Rush mode with the Amazon work moved off the placing thread. Launcher: **🚀Ultra** on a profile's card;
takes effect when that placer next starts. Off by default. Ultra implies Rush's rules.

## Why

Measured 2 Nov 2026, HS_RESELLER, 995-order queue, per 100 orders:

| Phase | Time | Placing? |
|---|---|---|
| A: scrape order pages | 10–17 min | stopped |
| B: place | 40–47 min | yes |
| C: write WB labels + notes | 27–36 min | stopped |

## How it works

`engine/orders.py` keeps every money decision on the main thread, one order at a time: duplicate
check, placement lock, margin, checkout. `engine/ultra_crew.py` runs two threads, each with its own
chromedriver session and its own **window** on the placer's Chrome:

- `AMZ-SCRAPE` reads the next 12 orders' pages ahead of the placer.
- `AMZ-WRITE` drains the same rush journal (`data/rush_pending_<PROFILE>.json`) behind it.

Windows, not tabs: a new tab makes the Zoro tab `hidden` and Chrome throttles its timers (4 ticks in
3 s against 30). Two windows, not one: a scrape costs ~10 s and a write-back ~13 s.

Also in ultra mode only:

- The batched duplicate check runs per few orders instead of per 100, and an order cleared earlier is
  asked again against the placer's own zero-lag records just before it is placed.
- The cart is not rebuilt when it already holds exactly this one item with no coupon
  (`_cart_holds_exactly`), about 5 s per order. Reseller orders with no coupons only.
- **Out-of-stock listings are held, not marked** (`_ultra_hold_oos`, owner, 2 Nov 2026). At the start of
  each cycle Zoro is asked once per SKU. Orders for a listing that is out of stock (and whose ALT, if it
  has one, is too) are left alone: no page opened, no label written, no time spent. The next cycle asks
  again, and when it is back they go through the normal way. One WhatsApp/Telegram message when a listing
  runs out and one when it is back.
  - Only a clear "not in stock" holds an order. An API error or a listing not reached in the time allowed
    stays in the queue. Manual-C/-R orders are never held at the queue, since a person decided them.
  - A listing that runs out mid-cycle is held from that moment (`_ultra_hold_listing`); that order logs
    `OOS_HELD`. An order for 3 that fails where 1 would pass holds only itself.
  - Held orders carry no `OOS` label, so Seller Central and the Daily report show them as still open.
    Nothing stamps them if the listing never comes back: that stays a person's decision.
- Zoro GETs and PUTs (never POST, never the coupon PUT) are cut off at 12 s and asked again, 3 tries.
- Zoro silent before the order was sent: status `ZORO_NET`, nothing bought, claim released, retried
  next cycle, and the placer waits for Zoro to answer. No page-checkout fallback.
- `POST /order` gets 90 s instead of 30.

In **every** mode: once the order submit has started, an exception never falls back to the page
checkout. The order may exist, so it fails closed to `FAILED_KEPT`.

## Placing orders already stamped OOS

Works in every mode, not only Ultra. When you have checked a listing is back, write its ASIN in the
`OOS_Release_HS` or `OOS_Release_DM` column of `Priority_ASIN_Control`. It is read every cycle, and no restart
is needed. Its orders stamped `OOS` are then treated exactly as if they had never been stamped:

- Routing is unchanged: the ASIN's column decides reseller, coupon or Deciding, as for an unlabelled order. An
  ASIN in no column goes to the coupon placer's supervisor.
- Every check still runs: duplicate check, margin, placement lock, delivery date, stock. If Zoro still says
  out of stock, Ultra holds them and the label stays `OOS`.
- Only a label reading exactly `OOS` is released. Delete the ASIN from the column when its orders are done.
- Off in Labels-only mode, which skips discovery.
- The column names must not start with `HS_` or `DM_`: `load_store_sorted_asins` reads every such column as
  "this ASIN is sorted", which would change routing.

## When something goes wrong

Every failure degrades to plain rush:

| Failure | What happens |
|---|---|
| Chrome refuses a second session | That cycle runs as plain rush |
| No scrape result for an order | The main thread scrapes it, as for a failed batch tab |
| Login page in a crew window | Crew waits 30 s; the main thread's next scrape signs in |
| 5 failures in a row in a window | That window stops; rush's serial scrape / batched flush take over |
| Browser recycle | Main thread only; the crew parks around it, then re-attaches |
| Label won't save | Stays journaled, retried with backoff behind every untried order, never reported done |
| Stock check fails or runs out of time | Those listings stay in the queue, as if it had not run |
| Order cancelled on Amazon | No Edit button, so it can't be written: off the journal at once; if it has a WB, one `PLACED, THEN CANCELLED` alert so someone can cancel it on Zoro |
| Crash or stop | Journal survives; the next start's writer finishes it, and those orders are skipped |

## What to read in the log

```
⚡⚡ ULTRA: placing 300 orders here; Amazon pages and write-backs run on their own windows
⚡⚡ ULTRA: scraped 40 (12 ready ahead) | written back 28 | 2 write-back(s) queued
AMZ-WRITE:   ✅ 112-…: WB… written back
[3-4] ⚡ cart already holds exactly G… x2 @ $… — not rebuilt
🌐 Zoro stopped answering before the order was sent — nothing bought. Released; …
```

`⚠️ scrape window down` / `⚠️ write window down` in the status line means that window gave up and
the placer is doing its work the rush way.

## Limits

Placing is one order at a time per Zoro account (one account, one cart), so the floor is the
checkout itself: about 19 s per order on a good connection. The next step up in speed is a second
account, not more threads.
