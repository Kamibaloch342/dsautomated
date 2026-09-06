# ZoroStore — ZORO Clone (Final)

Full-stack industrial-supply storefront: React (Vite) frontend + Flask/PostgreSQL backend.
Unlike zoro.com, this store supports **bulk order placement** — many orders in one shot,
each with its own ship-to address — plus live email threads, notifications and
shipment-tracking emails.

## Run

```bash
# Backend (Flask on :5001) — needs PostgreSQL running with zoro_db
cd "Backend(Task)"
./venv_mac/bin/python app.py

# Frontend (Vite on :5173)
cd frontend
npm run dev
```

Optional one-time seeding of size/pack/length variation families:

```bash
cd "Backend(Task)" && ./venv_mac/bin/python seed_variations.py
```

## Features

### Bulk Order Pad (`/bulk-order`)
- Spreadsheet-style rows: SKU, qty, recipient name, full ship-to address per row
- Paste CSV, upload CSV, downloadable template, sample rows
- Live validation against the catalog (product title, price, stock, min-order qty)
- One click places every row as its own order → per-row **Order IDs** returned
- Copy order IDs / download results CSV
- Batch summary email + per-order confirmation emails, bell notification
- Draft auto-saved locally; one billing address + notification email per batch

### Billing address
- Checkout has "billing same as shipping" toggle with full billing form
- Stored on every order (`bill_*` columns) and shown on confirmations

### Order lifecycle & comms
- Fulfillment worker advances orders: confirmed → processing → shipped
  (dev timings: 45 s / 2.5 min), generating carrier + tracking numbers
- Emails per stage (confirmation / processing / shipment w/ tracking) are
  **really sent** when `SMTP_*` env vars are set in `Backend(Task)/.env`;
  otherwise they're recorded as `simulated` so the whole flow still works
- Every order has a visible **email thread** (order confirmation page +
  account order history); confirmation page live-polls until shipped
- Header **notification bell** for signed-in users (batch placed,
  processing, shipped)

### Listings: variations & price comparison
- Product page "Available options" table: sizes/packs/lengths with prices,
  "+$x vs lowest", lowest-price badge, per-row add-to-cart
- "Compare" checkbox on listing cards → sticky compare bar → side-by-side
  modal (price w/ lowest badge, specs, availability, add-to-cart)
- Products sharing `variant_group` are options of one another
  (`seed_variations.py` creates 5 families; title-prefix heuristic covers
  ungrouped products)

### Cart UX
- Global "Added to cart — please check your cart" toast with product info,
  Check Cart CTA and auto-dismiss

### Extraction pipeline (zoro.com data)
- `extract_from_dump.py` — parses a zoro.com page-state dump (like
  `zoro_dump.json`) and extracts **everything**: full attribute/spec sheet,
  4-level category tree, unit price + priceUnit + min/package qty,
  lead time, hazmat/LTL/dropship flags, restricted states, variant groups.
  Run it on any new dump: `./venv_mac/bin/python extract_from_dump.py my_dump.json`
- `backfill_specs.py` — structured spec sheets (title/TSV-derived + baseline
  rows) for products that don't have a dump yet; never overwrites dump data.
- Product page renders the full sheet zoro-style: striped specification
  table w/ show-all toggle, Product Details bullets, Min/Package Qty facts.

### Admin panel (`/admin`)
Sign in as an admin (auto-created dev account: `admin@zorostore.dev` /
`admin1234` — override with `ADMIN_EMAIL`/`ADMIN_PASSWORD` env vars).
- **Dashboard** — revenue/orders/today/open-cases KPIs, status breakdown,
  recent orders & cases (auto-refreshes)
- **Orders** — filter by status/source, search (id, email, batch,
  tracking); manage drawer: items, both addresses, mark
  processing/shipped/delivered/cancel (sends the matching email +
  notification), manual carrier/tracking, resend emails, full email thread,
  linked cases
- **Cases** — support-ticket queue w/ conversation view; replies email the
  customer, ring their bell, and appear in their account; status/priority
  controls
- **Products** — inline edit price/availability/min-qty/lead-time
- **Customers** — accounts w/ order counts + lifetime spend
- **Email Log** — every outbound email, filterable, expandable bodies

### Support cases (customer side)
- Contact page = case form (guest or signed-in, optional order link)
- "Report an issue" on order confirmations / order history
- Account → Support Cases: full conversation threads + replies

## Key endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/orders` | single checkout (billing supported) |
| POST | `/api/orders/bulk/validate` | validate rows, returns catalog info |
| POST | `/api/orders/bulk` | place batch, returns per-row order IDs |
| GET | `/api/orders/batch/<batch_id>` | orders in a batch |
| GET | `/api/orders/<id>/emails` | the order's email thread |
| GET | `/api/notifications` | bell feed (auth) |
| POST | `/api/notifications/mark-read` | mark read (auth) |
| GET | `/api/products/<code>/variations` | size/pack options w/ prices |

Schema changes are applied automatically at backend startup
(`run_migrations()` — idempotent `ALTER TABLE ... IF NOT EXISTS`).
