# Dashboard change requests — noted 2026-07-04 (Kamran)

Status legend: [ ] todo · [x] done

## 1. [x] Fix the headers in the Data tab
Column headers in the Data tab render wrong — inspect and fix.

## 2. [x] Profile name on comments (multi-user via Tailscale)
Two more employees join over Tailscale. Each browser picks/stores a profile
name; every comment (and CRM edit) is attributed — comments display under the
author's name. Implementation: profile name in localStorage + sent with
update_order; comments stored as a thread with author + timestamp.

## 3. [x] P&L: Investment = $180,000
The P&L tab's investment figure is 180,000 (drives ROI %).

## 4. [x] Move "Refund issues" tab INTO the "⚠ Severe" tab
One less top-level tab; refund tickets render as a section inside Severe.

## 5. [x] Tags on orders (like the flag colors)
- Standard tag options: e.g. Replacement, plus "Other…" → prompts for a custom tag.
- Stored in order_crm (new `tags` column, comma list).
- Tracker gets a filter listing all tags in use (incl. custom ones).

## 6. [x] Placed tab: show the tax column (zoro_tax from placement log).

## 7. [x] INSIGHTS TAB — "what changed" engine (the big one)
Detect day-over-day changes per ASIN across placement history
(order_placer_orders + closing_orders). Categories:

a) **Zoro code changed** — same ASIN, different zoro SKU/code vs previous
   orders (OOS → alternative code swaps). Show ASIN, old code → new code,
   dates, order counts.

b) **Item price changed** — normalize: unit_price = item_price / quantity.
   If unit price for an ASIN changes vs its previous orders → flag.
   (qty 2 at same unit price is NOT a change.)

c) **Earnings/unit changed** — earnings / qty per ASIN; catches Amazon
   category/referral-fee changes. Same for **shipping_amt / qty**.

d) **Handling time changed** — ship_by_date − purchase_date (business days,
   Fri purchase → Mon ship-by is normal weekend skew, not a change).
   Track per ASIN; if the listing's handling time changes → flag.
   Cross-reference (future): Zoro lead_time from the daily TSV
   (TSV Files/combined_reports.db, table zoro_data: zoro_no, lead_time,
   availabilityStatus…) and zoro_deliver_by captured at placement.

e) **Zoro shipping changed** — rule: subtotal ≥ $50 → free, else $5.99.
   Flag orders where zoro_shipping deviates from the rule (usually a manual
   carrier change: USPS etc.).

f) **Zoro unit cost changed** — zoro_subtotal / zoro_qty per ASIN (and per
   zoro code); if the normalized unit cost changes → flag (price rise/drop
   at Zoro).

UI: Insights tab gets a "Changes" section: one card per category with count,
expandable table (ASIN, before → after, when, sample orders).
