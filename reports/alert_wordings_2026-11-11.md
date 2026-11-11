# ZUHRA alert inventory: every message that leaves the Mac for a person (11 Nov 2026)

**How to use this:** each alert below shows its exact text as it arrives today. Change the wording right
in this file (edit the text inside the grey blocks) or paste your version in chat with the alert's number
(like you did for the listing guard), and I'll put it in the code. Lines starting with "When / Where /
Live / Text" are for reference; you don't need to edit them.

Already changed today: #10 / #11 (price alert ends at the old price's date), #22 / #23 (your wording),
and no grey code boxes in any WhatsApp alert.

Orders Alerts groups: 4 · Insights groups: 19 (1 never fires) · Tracking groups: 1 · Delivery Messages + Review Requests groups: 1 · recipient_phone (general notices group): 8 · Telegram only: 2 · Never sent (dead code): 5 entries (the last one bundles 10 unsent placer-insight texts). Total numbered: 40.

Read this first:
- WhatsApp texts are shown as they arrive: backticks removed (utils/whatsapp.py:158 `_plain`), `*bold*` kept as asterisks. Sample values are made up (order 113-4461595-0151456, WB3938136187, ASIN B00ED3K1VI = Simple Green 5 gal, Zoro G4602297, seller SKU kam-G4602297, buyer "John Carter (Dallas, TX)").
- "recipient_phone" in bot_config.json is NOT a phone number today: it holds a WhatsApp **group** id (ends in @g.us). The gateway calls it the mixed "general" group (whatsapp_gateway/server.js:78). DM placers' notices to it go out from Hifza's number (lane `hathoro_notices`), everything else in it from Kamran's.
- Group keys resolve through `get_whatsapp_group(store_or_profile, kind)` (utils/whatsapp.py:52). bot_config.json → whatsapp.groups sets hoank/hathoro orders+insights and wrenchly_orders/_tracking/_messages; hoank/hathoro _tracking/_messages come from DEFAULT_GROUPS in the code. **There is no `wrenchly_insights`**, so every Wrenchly "Insights" alert lands in the general group.
- Telegram is **switched off** (bot_config.json → telegram.enabled = false), so every Telegram text below is currently not sent. Telegram twins of WhatsApp alerts are shown inside the WhatsApp entry. Telegram uses HTML; shown here with tags removed (the words in `<b>` are bold).
- The gateway holds every alert while the launcher is closed, and drops an identical text to the same group within 6 h (server.js:469, 487-508).
- The WhatsApp gateway (server.js) composes no message of its own. walmart_orders.py sends nothing. engine/post_delivery.py buyer messages are not listed (not alerts).

---

## A. Orders Alerts groups (hoank_orders / hathoro_orders / wrenchly_orders)

### 1. Order placed — `🟢 PROFIT ORDER` / `🟡 ORDER PLACED` / `🔴 LOW MARGIN ORDER`
- **When:** every order the placer buys on Zoro (single, multi-item and split orders alike).
- **Where:** the profile's store Orders Alerts group (`get_whatsapp_group(PROFILE_NAME, "orders")`); photo = Seller Central order page, scrolled to the top (text only in rush/ultra mode). Telegram twin (off).
- **Live:** yes — 12,315 SUCCESS order rows in placer run logs since 28 Oct.
- **Text:** header utils/whatsapp.py:324-332, caption :352; body = `order_card` utils/telegram.py:206-304 (owner-locked layout 15 Oct 2026). Called from engine/orders.py:6683 and :6826 via utils/telegram.py:338 `notify_order_placed` → :438.
- Header rules: profit/cost < 8 % (or < 8 % before tax) → red; 8–20 % → yellow; > 20 % → green. Title is cut to ~44 characters (first part before " - ", else the first 44 chars). Zoro code G→K. Shipping in brackets only when the buyer paid shipping. A non-Standard ship service is bold.

Yellow (8–20 %):
```
*🟡 ORDER PLACED (+8.2%)*
113-4461595-0151456 { WB3938136187 }
Simple Green, SMP13006, Industrial Cleaner/D

*SKU*: kam-G4602297 | *ASIN*: B00ED3K1VI
*Zoro Code:* K4602297 (Qty: 1)

*Zoro_Cost*: $57.32 (Tax $0.00) |
*Amz_Earnings:* $62.03
*Profit:* $4.71 (8.2%) | Coupon: EMZMMV3JYKVJ (-$6.37)

*Amazon:* [Ship by Nov 12 | Deliver by Nov 16 ] {Standard}
*Zoro_Delivery*: Nov 13
```
Green (> 20 %), ALT code used, buyer paid shipping, Second Day:
```
*🟢 PROFIT ORDER (+52.3%)*
113-4461595-0151456 { WB3938136187 }
Simple Green, SMP13006, Industrial Cleaner/D

*SKU*: kam-G4602297 | *ASIN*: B00ED3K1VI
*Zoro Code:* K4602297 - ALT (Qty: 1)

*Zoro_Cost*: $29.30 (Tax $0.00) |
*Amz_Earnings:* $44.62 (*Shipping*: $13.00)
*Profit:* $15.32 (52.3%) | Coupon: None

*Amazon:* [Ship by Nov 12 | Deliver by Nov 16 ] {*Second Day*}
*Zoro_Delivery*: Nov 13
```
Red, no tax / with tax (the body is the same card):
```
*🔴 LOW MARGIN ORDER (< 8%): 1.5%*
```
```
*🔴 Taxed(TX) Low Margin Order (< 8%): 1.5%*
113-4461595-0151456 { WB3938136187 }
Simple Green, SMP13006, Industrial Cleaner/D

*SKU*: kam-G4602297 | *ASIN*: B00ED3K1VI
*Zoro Code:* K4602297 (Qty: 1)

*Zoro_Cost*: $61.10 (Tax $4.45) |
*Amz_Earnings:* $62.03
*Profit:* $0.93 (1.5%) | Coupon: EMZMMV3JYKVJ (-$6.37)

*Amazon:* [Ship by Nov 12 | Deliver by Nov 16 ] {Standard}
*Zoro_Delivery*: Nov 13
```
(Missing cost → header reads `*🔴 LOW MARGIN ORDER (< 8%): N/A*`.)

Telegram twin (utils/telegram.py:394-413) — bold header, then the card without asterisks in a copy block; non-Standard service in CAPITALS:
```
🟢 PROFIT ORDER (+52.3%)

113-4461595-0151456 { WB3938136187 }
Simple Green, SMP13006, Industrial Cleaner/D

SKU: kam-G4602297 | ASIN: B00ED3K1VI
Zoro Code: K4602297 - ALT (Qty: 1)

Zoro_Cost: $29.30 (Tax $0.00) |
Amz_Earnings: $44.62 (Shipping: $13.00)
Profit: $15.32 (52.3%) | Coupon: None

Amazon: [Ship by Nov 12 | Deliver by Nov 16 ] {SECOND DAY}
Zoro_Delivery: Nov 13
```
Telegram headers: `🟡 ORDER PLACED (+8.2%)`, `🔴 LOW MARGIN ORDER (< 8%): 1.5%`, `🔴 Taxed(TX) Low Margin Order (< 8%): 1.5%`.

### 2. ALT order: "<main code> OOS" photo
- **When:** an order was placed on the alternative Zoro code; about 8 s after alert #1, one photo per out-of-stock main code.
- **Where:** store Orders Alerts group (by profile); photo = the main code's Zoro product page. Telegram twin (off), same caption.
- **Live:** yes, whenever an ALT is used (the hourly insights reported 18 listing-days with ALT orders since 9 Nov).
- **Text:** engine/orders.py:4377 (sent :4381-4382; called :6687, :6830).
```
G5605031 OOS
```

### 3. OUT OF STOCK (OOS) ALERT
- **When:** Zoro (main code and every ALT) is out at the stock check, the order is labelled "OOS" on Amazon, and the placer is NOT in Ultra mode.
- **Where:** store Orders Alerts group (by profile), with a screenshot of the page Chrome is on (text only in rush mode). Telegram twin (off).
- **Live:** yes — 1,663 OOS order rows since 28 Oct (that figure also includes OOS found at checkout, which labels but sends no alert).
- **Text:** utils/telegram.py:538-546 (called from engine/orders.py:5768). SKU and Zoro Code are both the Zoro code here.
```
🚨 *OUT OF STOCK (OOS) ALERT*

• Title: *Simple Green, SMP13006, Industrial Cleaner/Degreaser, 1 / Each, Dark Green, 5 Ga*
• SKU: G4602297
• ASIN: B00ED3K1VI
• Zoro Code: G4602297
• Order: 113-4461595-0151456

• Action: Marked 'OOS' on Amazon Seller Central
```
Telegram twin (utils/telegram.py:520-528):
```
🚨 OUT OF STOCK (OOS) ALERT

Title: Simple Green, SMP13006, Industrial Cleaner/Degreaser, 1 / Each, Dark Green, 5 Ga
SKU: G4602297
ASIN: B00ED3K1VI
Zoro Code: G4602297
Amazon Order: 113-4461595-0151456

Action: Marked 'OOS' on Amazon Seller Central
```

### 4. Not Placing this Order (buyer requested cancel) — Hathoro
- **When:** the Seller Central order page shows "Buyer requested cancel"; once per order per placer run. Nothing is bought.
- **Where:** **Hathoro only** → hathoro_orders. (HOANK and Wrenchly: see #26, general group.) Telegram twin (off), different wording.
- **Live:** yes — 45 CANCEL_REQUESTED orders since 28 Oct, last 10 Nov.
- **Text:** engine/orders.py:3374-3375 (owner's exact wording, 9 Nov). Telegram text :3367-3368, :3380.
```
🛑 Not Placing this Order 113-4461595-0151456
The buyer requested cancellation. Cancel krdun?
```
Telegram twin:
```
🛑 BUYER REQUESTED CANCEL
Order 113-4461595-0151456 (B00ED3K1VI G4602297) NOT placed — the buyer requested cancellation. Cancel it in Seller Central (it does not count against the cancellation rate).
```

---

## B. Insights groups (hoank_insights / hathoro_insights; Wrenchly → general group, no wrenchly_insights is set)

### 5. ORDER REJECTED — LOW PROFIT (sent at once by the placer)
- **When:** at checkout the margin is under the profile's minimum; nothing bought, order labelled "Loss-Kamran checking".
- **Where:** store Insights group (by profile; WR_RESELLER → general group). Telegram twin (off).
- **Live:** yes — 15 LOW_PROFIT orders since 28 Oct, last 9 Nov.
- **Text:** utils/telegram.py:487-497 (called engine/orders.py:6916). Note: a loss shows the dollar amount without a minus sign (only the % has it). The same order later also gets the hourly #16 "Loss-Kamran" line.
```
⛔ *ORDER REJECTED — LOW PROFIT*

• Title: *Simple Green, SMP13006, Industrial Cleaner/Degreaser, 1 / Each, Dark Green, 5 Ga*
• SKU: G4602297
• ASIN: B00ED3K1VI
• Order: 113-4461595-0151456

• Projected Profit: *$3.40* (-5.2%)
• Earnings / Cost:  $62.03 / $65.43
• Threshold:        Min required 0%
• Status:           Skipped / Marked Loss-Kamran checking
```
(A small positive margin reads `• Projected Profit: *+$1.12* (+1.8%)`.)

Telegram twin (utils/telegram.py:468-478):
```
⛔ ORDER REJECTED — LOW PROFIT

Title: Simple Green, SMP13006, Industrial Cleaner/Degreaser, 1 / Each, Dark Green, 5 Ga
SKU: G4602297
ASIN: B00ED3K1VI
Amazon ID: 113-4461595-0151456

Earnings: $62.03 | Cost: $65.43
Projected Profit: $3.40 (-5.2%)
Threshold: Min required 0%
Status: Skipped / Marked Loss-Kamran checking
```

### 6. ZORO DELIVERS LATE (sent at once by the placer)
- **When:** right after an order is placed, Zoro's delivery date is 2+ business days after Amazon's deliver-by. One per order, no de-duplication at the source.
- **Where:** store Insights group (`notify_insight(ins, account=STORE_ACCOUNT)`; Wrenchly → general group). No Telegram.
- **Live:** yes, the only code in `PLACER_ALERT_CODES` (utils/insights_engine.py:890). Not counted anywhere. The hourly #13 reports the same problem per listing per week.
- **Text:** utils/insights_engine.py:731-739; sent by utils/whatsapp.py:474-514 (adds nothing because the text already has "• Title:"); called engine/orders.py:6727, :6873. Dates are zero-padded ("Mon, Nov 09"). The SKU here is the Amazon seller SKU, unlike most other alerts.
```
⏰ *ZORO DELIVERS LATE* — G4602297

• Title: *Simple Green, SMP13006, Industrial Cleaner/Degreaser, 1 / Each, Dark Green, 5 Ga*
• SKU: kam-G4602297
• ASIN: B00ED3K1VI
• Order: 113-4461595-0151456

• Amazon deliver by: *Mon, Nov 16*
• Zoro delivery by:  *Thu, Nov 19*  (*3 business days* later)
```

### Hourly Insights (#7–#18): the frame
- **When:** the hourly job scripts/auto_sync_10min.py:95 runs `dispatch_account_insights(days=2, limit=10, summaries=True)` for HOANK, Hathoro and Wrenchly. Only items dated in the last 2 days, never sent before (table whatsapp_dispatched_insights), at most 10 per store per hour.
- **Where:** `get_whatsapp_group(store, "insights")` (Wrenchly → general group).
- **Frame:** scripts/send_insights_alerts.py:342-353; headers :51-64; message from dashboard/insights.py `add(...)`. A message that starts with "ASIN <asin> (<store>):" loses that prefix (:337-340), so the ASIN and store appear only in the frame. The SKU line is usually the Zoro code (the placer's sku wins over closing's seller SKU). Title is cut to 80 characters; missing title → "Product Listing", missing SKU → "N/A". ASIN / Order / Date lines are left out when empty.
- **Counts:** last 14 days from whatsapp_dispatched_insights (rows marked BASELINE_HISTORICAL excluded, they were never sent); "since rebuild" = after 9 Nov 17:00, when the rules were rebuilt.

Frame, shown once (OUT OF STOCK as the example):
```
🚨 *OUT OF STOCK*

• Title: *Simple Green, SMP13006, Industrial Cleaner/Degreaser, 1 / Each, Dark Green, 5 Ga*
• SKU: G4602297
• ASIN: B00ED3K1VI
• Order: 113-4461595-0151456
• Date: Wed, Nov 11

• Alert: Supplier is OUT OF STOCK on 3 orders — deactivate listing on Amazon immediately!
```
Below, each category gives its header line and the "• Alert:" line(s); everything else is the frame above.

### 7. AMAZON EARNINGS INCREASED
- **When:** a listing's earnings per unit (shipping removed) moved up vs. its previous orders (≥ $0.50 or ≥ 2 %; a 2×/3×/4× jump on one order is ignored).
- **Live:** yes — 60 sent in 14 days (5 since rebuild).
- **Text:** header send_insights_alerts.py:52; message dashboard/insights.py:890.
```
📈 *AMAZON EARNINGS INCREASED* 🟢
• Alert: earnings INCREASED ↑ $60.12 → $62.03/unit (+$1.91, 3 orders at new)
```

### 8. AMAZON EARNINGS DECREASED
- **When:** same rule, earnings per unit went down.
- **Live:** yes — 47 sent (6 since rebuild).
- **Text:** header :53; message insights.py:898.
```
📉 *AMAZON EARNINGS DECREASED* 🔴
• Alert: earnings DECREASED ↓ $66.78 → $63.85/unit (-$2.93, 1 order at new)
```

### 9. BUYER SHIPPING
- **When:** (a) a buyer paid shipping on a listing whose last orders were ≥ 70 % free; (b) an order is free after 3+ buyer-paid orders in a row.
- **Live:** path live, but 0 sent since rebuild; the 19 sent in 14 days (last 30 Oct) used older wording.
- **Text:** header :54; messages insights.py:921 (a), :929 (b).
```
🚚 *BUYER SHIPPING*
• Alert: Buyer paid $13.00 for faster shipping on Order 113-4461595-0151456 (this listing usually ships free) — make sure it goes out fast
```
```
🚚 *BUYER SHIPPING*
• Alert: Order 113-4461595-0151456 has FREE shipping, but the previous 4 orders had buyer-paid shipping — verify shipping template!
```

### 10. ZORO PRICE INCREASED
- **When:** the Unit Price on a Zoro order e-mail is higher than on that code's previous e-mail. The frame's Order/Date are the order and e-mail that first showed the new price; SKU = the Zoro code.
- **Live:** yes — 102 sent (7 since rebuild). The text was shortened today (commit d5c221a): it now ends at the old price's date, no "Listings:" part.
- **Text:** header :55; message insights.py:949-950.
```
🏷️ *ZORO PRICE INCREASED* 🔴
• Alert: Zoro SKU G4602297: Unit Price INCREASED ↑ $60.49 → $63.69/ea (+$3.20, +5.3%). New price first on WB3938136187 (Wed, Nov 11); old price last on WB1385885229 (Mon, Nov 9)
```

### 11. ZORO PRICE DECREASED
- **When:** same, price went down.
- **Live:** path live — 29 sent in 14 days, 0 since rebuild.
- **Text:** header :56; message insights.py:949-950.
```
🏷️ *ZORO PRICE DECREASED* 🟢
• Alert: Zoro SKU G4602297: Unit Price DECREASED ↓ $63.69 → $60.49/ea (-$3.20, -5.0%). New price first on WB3938136187 (Wed, Nov 11); old price last on WB1385885229 (Mon, Nov 9)
```

### 12. ZORO SHIPPING RULE DEVIATION
- **When:** Zoro charged more shipping than its rule (free at list price × qty ≥ $50, else $5.99), single-item orders only.
- **Live:** path live — 1 sent in 14 days (2 Nov, older wording), 0 since rebuild.
- **Text:** header :57; message insights.py:817.
```
⚠️ *ZORO SHIPPING RULE DEVIATION*
• Alert: Zoro shipping $12.50 on Order 113-4461595-0151456 ≠ standard $0.00 (list price × qty $63.69 — carrier change / freight surcharge)
```

### 13. ZORO LATE AGAINST AMAZON PROMISE
- **When:** a listing's latest order (placed within 1 business day) has Zoro's date 2+ business days after Amazon's deliver-by; one per listing per week, counting that week's late orders.
- **Live:** yes — 744 sent in 14 days (5 since rebuild).
- **Text:** header :58; message insights.py:784.
```
⏱️ *ZORO LATE AGAINST AMAZON PROMISE*
• Alert: Zoro delivers Thu, Nov 19, Amazon promised Mon, Nov 16 — 3 business days late (4 orders). INCREASE handling time on Amazon
```
(One order: no "(4 orders)".)

### 14. OUT OF STOCK
- **When:** a placer logged OOS for a listing, or an order carries "OOS" in its seller order id / comments; one per listing per day. The Zoro code appears only in the SKU line (the "(Zoro G…)" part is cut with the ASIN prefix).
- **Live:** yes — 855 sent (3 since rebuild).
- **Text:** header :59; message insights.py:677.
```
🚨 *OUT OF STOCK*
• Alert: Supplier is OUT OF STOCK on 3 orders — deactivate listing on Amazon immediately!
```
(With no order ids: `Supplier is OUT OF STOCK — deactivate listing on Amazon immediately!`; one order: "on 1 order".)

### 15. ALTERNATIVE SKU USED (order placed)
- **When:** orders were placed on the ALT code because the main code is out; one per listing per day.
- **Live:** yes — 18 sent, all since rebuild.
- **Text:** header :60; message insights.py:683.
```
🔄 *ALTERNATIVE SKU USED (order placed)*
• Alert: Main Zoro SKU G5605031 is out of stock, so 1 order went through on Alternative SKU G5668704
```

### 16. LOW MARGIN WARNING (< 8%)
- **When:** (a) a placer logged a low-profit rejection; (b) an order carries "Loss-Kamran" in its seller order id / comments; (c) an order was placed at under 8 % (one per listing per day, the worst order). (a) and (b) have different keys, so one rejected order can produce both, on top of the instant #5.
- **Live:** yes — 678 sent (43 since rebuild).
- **Text:** header :61; messages insights.py:715 (a), :735 (b), :749 (c).
```
⚠️ *LOW MARGIN WARNING (< 8%)*
• Alert: REJECTED for low margin on Order 113-4461595-0151456 — manual review required before ship deadline!
```
```
⚠️ *LOW MARGIN WARNING (< 8%)*
• Alert: REJECTED for low margin on Order 113-4461595-0151456 (marked 'Loss-Kamran checking') — manual review required!
```
```
⚠️ *LOW MARGIN WARNING (< 8%)*
• Alert: PLACED with low margin 5.4% (< 8%) on Order 113-4461595-0151456 (Profit: $3.10) (2 orders that day, this is the lowest) — review pricing!
```

### 17. BUYER ZIP CODE MISMATCH
- **When:** the Amazon ship-to ZIP (first 5 digits) differs from the ZIP on Zoro's order e-mail.
- **Live:** path live, 0 sent in 14 days (4 were marked baseline).
- **Text:** header :62; message insights.py:969 (no ASIN prefix, so nothing is cut).
```
📍 *BUYER ZIP CODE MISMATCH*
• Alert: Ship zip 75201-4410 != Zoro 752014410
```

### 18. WRONG QUANTITY MULTIPLIER
- **When:** the Zoro quantity placed differs from the verified pack size.
- **Live:** **never fires**: it reads DS_Closing/Hypersale_Closing/verified_skus_cache.csv, which does not exist. 0 sent.
- **Text:** header :63; message insights.py:1004.
```
🔢 *WRONG QUANTITY MULTIPLIER*
• Alert: Placed Zoro qty 3, expected 6 (×6×1)
```

### 19. DAILY SUMMARY (Refunded / Undelivered past due / Shipped late / WB not in placed orders)
- **When:** once a day per store, from 12:00 local, with every not-yet-reported item of these four categories from the last 30 days. Not sent one by one. Nothing new → nothing sent.
- **Where:** store Insights group (Wrenchly → general group).
- **Live:** yes — 4 messages in 14 days (HOANK + Hathoro on 10 and 11 Nov; 28/27 and 57/28 items). Wrenchly: nothing new each day.
- **Text:** title send_insights_alerts.py:238, layout :173-183 and :201; section names :67-72; lines from insights.py:992 (refunded), :981 (undelivered), :1014 (shipped late), :1022 (WB unmatched). At most 12 lines per section, then "…and N more on the dashboard".
```
📋 *DAILY SUMMARY — Hathoro — Wed, Nov 11*

*💵 Refunded (2)*
• 113-4461595-0151456 Refunded $59.99 — NOT delivered → claim from Zoro
• 112-3398410-7765021 Refunded $18.18 — returned by customer

*📬 Undelivered past due (2)*
• 114-2290573-1148209 Not delivered (status InTransit), due 2026-11-10
• 111-6603187-2241950 Not delivered (status DeliveryException), due 2026-11-10

*⏰ Shipped late (1)*
• 112-5521046-3370418 Shipped LATE vs ship-by by 3.3d

*❓ WB not in placed orders (1)*
• 111-7750392-4408617 WB WB3938136187 not in placed orders — verify/cancel
```
(Refund with no amount yet: `Refunded (amount not known yet) — returned by customer`.)

### 20. WEEKLY HANDLING REVIEW
- **When:** once a week per store (Monday's key, from 12:00), the listings that usually take 2+ business days to ship-by while Zoro is not tight against Amazon's date.
- **Where:** store Insights group (Wrenchly → general group).
- **Live:** yes — 2 messages in 14 days (9 Nov: HOANK 8 items, Hathoro 6).
- **Text:** title send_insights_alerts.py:242; section :73; line insights.py:221-226 (the "ASIN … (store):" prefix is cut, the ASIN leads the line).
```
📋 *WEEKLY HANDLING REVIEW — Hathoro*

*⏱️ Handling days that can be cut (6)*
• B00ED3K1VI usually 3 business days to ship-by over 167 orders; Zoro arrives about 2 business days before Amazon's date — handling can come down to 1
• B07B6B8SR8 usually 2 business days to ship-by over 23 orders; Zoro arrives about 1 business day before Amazon's date — handling can come down to 1
```

### 21. Own-brand listings: what changed (listing watch)
- **When:** at the end of each 12-hourly repricer scrape per store (HOANK, Hathoro), if anything changed on an own-brand listing: a new outside seller, star rating, number of ratings, category. On the first look at a listing only the outside sellers already there are reported. At most 15 lines per section.
- **Where:** store Insights group. Also written to listing_events (dashboard), which is why the hourly sender skips these kinds.
- **Live:** yes — runs on 10–11 Nov reported sellers, 31 rating, 11 ratings-count and 12 category changes.
- **Text:** utils/listing_watch.py:232-247; item lines :164, :194-217; sent :264. The title is cut at 48 characters with "…".
```
🏷️ *HOANK — own-brand listings: what changed*
255 listings opened, 156 of them own-brand

🚨 *New seller on our listing* (1)
• B0GD8Y19SD HO-G1606970 HOANK Flat Corrugated Cardboard Shipping Boxes 2… — Tools Depot LLC ($41.99), PrimeSupplyCo ($43.50)

⭐ *Star rating moved* (1)
• 📉 B0GD8Y19SD HO-G1606970 HOANK Flat Corrugated Cardboard Shipping Boxes — 4.6 → 4.4 ★ (37 → 41 ratings)

💬 *Number of ratings changed* (1)
• B0GLHV8499 HO-G3360115 HOANK Mailer Boxes 12x9x4 — 12 → 13 ratings (4.5 ★)

📂 *Category changed* (1)
• B0GLHV8499 HO-G3360115 HOANK Mailer Boxes 12x9x4 — “Industrial & Scientific › Packaging & Shipping Supplies › Boxes” → “Office Products › Office & School Supplies › Mailing Supplies”
```
First-look section:
```
🚨 *Other sellers already on our listings* — first look (1)
• B0HH6PP866 hath-G410877336-6pk Hathoro Heavy Duty Double Wall Corrugated Moving… — BoxWorld Inc ($64.10)
```
(More than 15 lines in a section: `… +N more (Repricer tab)`; a rating going up uses 📈.)

### 22. Listing guard — Zoro out of stock (listing taken off sale, or own brand given a longer handling time)
- **When:** the placer finds a listing's Zoro code and every alternative out of stock; handled at the end of the placer's cycle. Repeated only when something new happens (a new change, or a change that failed).
- **Where:** store Insights group only (Wrenchly → general group). Also a row on the dashboard's Insights tab.
- **Live:** yes, since 10 Nov (15 such events so far). New wording since today (commit fa952c3); placers use it after a restart.
- **Text:** engine/listing_guard.py `_message`.

Somebody else's brand, quantity already 0 (your example):
```
🛑 *Zoro is out of stock for Eagle Plastic Drum, 30 Gallon Drum, DOT Compliant.*
• ASIN B001DSIEL0 · SKU Imran-G0673644
• I couldn't find an alternative in the sheet for Zoro item G0673644.
• One order has been marked out of stock.
• ✅ The Amazon quantity was already 0, so I left it as is.
• I added it to the list to recheck stock every 30 minutes. I'll let you know when Zoro has it back in stock. The Amazon quantity will need to be updated.
```
Quantity set to 0 by the placer:
```
• ✅ I set the Amazon quantity from 11 to 0, so the listing is off sale.
• I'll check Zoro every 30 minutes. When it's back in stock I'll put the quantity back to 11 and let you know.
```
Own brand, with alternatives and 8 orders:
```
🛑 *Zoro is out of stock for HOANK Heavy Duty Shipping Boxes 12x9x3, 25 Pack, Kraft.*
• ASIN B0GV9GHGQD · SKU HOA- G105301740
• Zoro item G105301740 is out, and so is every alternative in the sheet (G105301741, G105301742).
• 8 orders have been marked out of stock.
• ✅ It's our own brand (HOANK), so it stays on sale. I raised the handling time from 1 day to 7 days.
• I'll check Zoro every 30 minutes. When it's back in stock I'll put the handling time back to 1 day and let you know.
```
Other lines that can appear instead:
```
• ✅ It's our own brand (HOANK), so it stays on sale. The handling time was already 7 days, so I left it as is.
• ⚠️ I couldn't set the Amazon quantity to 0 because the Source Offer card's Edit button could not be pressed. Please set it to 0 by hand: <Seller Central link>
• ⚠️ It's our own brand (HOANK), so it should stay on sale with a longer handling time, but I couldn't change it because the offer page did not load. Please raise the handling time to 7 days by hand, and don't set the quantity to 0: <link>
• ⚠️ I couldn't tell whether this is our own brand, so I changed nothing on Amazon. Please check it: <link>
• I'll check Zoro every 30 minutes and let you know when it's back in stock.
• Automatic restore is switched off: when Zoro has it again, please put the Amazon quantity back by hand.
```

### 23. Listing guard — Zoro has it back in stock
- **When:** every 30 minutes the placer asks Zoro again about the listings above; sent once when the main code or an alternative can be bought again.
- **Where:** store Insights group only (Wrenchly → general group).
- **Live:** yes (2 such events so far). New wording since today (commit fa952c3).
- **Text:** engine/listing_guard.py `_restore`.
```
✅ *Zoro has Eagle Plastic Drum, 30 Gallon Drum, DOT Compliant back in stock.*
• ASIN B001DSIEL0 · SKU Imran-G0673644
• Zoro item G0673644 can be bought again.
• ✅ I set the Amazon quantity back from 0 to 11, so the listing is on sale again.
```
Other lines that can appear instead:
```
• Zoro has the alternative G105301741 in stock again (the main item G105301740 is still out).
• ✅ I set the handling time back from 7 days to 1 day.
• I hadn't changed this listing, so I left it alone. Please update the Amazon quantity by hand: <link>
• Someone changed the quantity after I did (it is 5 now, not 0), so I left it alone.
• ⚠️ I couldn't put the quantity back to 11 because the Submit button could not be pressed — nothing saved. Please do it by hand: <link>
• ⚠️ I don't know what the quantity was before, so please set it by hand: <link>
```

---

## C. Tracking groups (hoank_tracking / hathoro_tracking / wrenchly_tracking)

### 24. Tracking ID alert
- **When:** after the tracker uploads tracking and the Seller Central order page shows the shipment as confirmed; one per order. **Not sent in Fast mode** (whenever "Skip messages" is ticked).
- **Where:** `<store>_tracking` (falls back to `<store>_messages` if a store had none); photo = the order page scrolled to the package/tracking section. No Telegram.
- **Live:** yes — about 2,264 confirmed uploads alerted 9–11 Nov (the upload log only goes back to 9 Nov).
- **Text:** utils/tracking_alert.py:121-140; sent engine/tracking.py:116-149 (called :2992). Times are the e-mails' timestamps in PKT. "Tracking Message Sent:" is one of `Yes`, `Yes (cancellation reply)`, `No Permission`, `Already sent earlier`, `Messaging off`, `No` (engine/tracking.py:2982-2991).

One box:
```
*Order:* 113-4461595-0151456
*Order Received PKT:* Nov 10, 1:59 AM
*Order Placed PKT:* Nov 10, 2:01 AM
*Order Shipped PKT:* Nov 10, 11:40 PM
*Time Shipped:* 21h 39m
*Tracking IDs (1 × FedEx):*
522715808643
*Tracking Message Sent:* Yes
```
Several carriers (each id then gets its carrier):
```
*Order:* 113-4461595-0151456
*Order Received PKT:* Nov 10, 1:59 AM
*Order Placed PKT:* Nov 10, 2:01 AM
*Order Shipped PKT:* Nov 10, 11:40 PM
*Time Shipped:* 21h 39m
*Tracking IDs (2 × FedEx, 1 × UPS):*
522715808643 (FedEx)
522715808654 (FedEx)
1Z9A0R870312345678 (UPS)
*Tracking Message Sent:* No Permission
```
Freight line, ship time unknown:
```
*Order:* 113-4461595-0151456
*Order Received PKT:* Nov 10, 1:59 AM
*Order Placed PKT:* Nov 10, 2:01 AM
*Order Shipped PKT:* —
*Time Shipped:* —
*Tracking IDs (1 × Saia (Freight)):*
PRO 1234567
*Tracking Message Sent:* No
```

---

## D. Delivery Messages + Review Requests groups (hoank_messages / hathoro_messages / wrenchly_messages)

### 25. "<store> — Delivery message / Review request sent"
- **When:** during the messaging pass after closing (daily 04:00 chain, `closing.message_after_closing: true`), once for each order where a delivery check-in and/or a review request actually went through on that visit. Errors, No Permission, Already Requested and Expired are never posted. Delay notices post nothing.
- **Where:** `<store>_messages`. No Telegram.
- **Live:** yes (orders delivered 10 Nov are already marked sent).
- **Text:** scripts/send_delivery_messages.py:206-216 (called :344). Dates are printed as stored (the purchase / deliver-by / delivered columns carry different formats; anything after "|" is cut).
```
✅ *Hathoro — Delivery message sent*
Order: 113-4461595-0151456
ASIN: B00ED3K1VI | SKU: kam-G4602297 | Qty: 1
Purchase date: 2026-11-09 14:53
Deliver by: Fri, Nov 13, 2026
Delivered: Tuesday, 11/10/2026 10:00 am
Return requested: No
Refund issued: No
Delivery message sent: Yes
Review request: No
```
Other headers: `✅ *HOANK — Review request sent*` (then `Review request: Yes`), `✅ *Wrenchly — Delivery message + Review request sent*`. "Delivery message sent:" can also read `No Permission`.

---

## E. recipient_phone — the general notices group (bot_config whatsapp.recipient_phone; DM placers send from Hifza's number)

These are the placer's notices: they are sent with no `to`, so they land here. All have a Telegram twin (off).

### 26. Not Placing this Order (buyer requested cancel) — HOANK and Wrenchly
- Same trigger and same text as #4, but for HOANK and Wrenchly placers (`to=None`, engine/orders.py:3373).
```
🛑 Not Placing this Order 113-4461595-0151456
The buyer requested cancellation. Cancel krdun?
```

### 27. Order not placed — five headers (was one "DELIVERY TIME HIGH" for all six reasons)
- **When:** an order is NOT placed for one of the reasons below; once per order and reason per placer run (the shared-account one at most once an hour).
- **Where:** general group. Telegram twin (off).
- **Fixed today:** each reason has its own header, and only the two late-date reasons say "Labelled 'Delivery time high' on Amazon" (they are the only ones that write it). If that label could not be written, the alert says so.

(a) Zoro's date is too late (product page):
```
🐢 *DELIVERY TIME HIGH*
Order 113-4461595-0151456 NOT placed — Zoro delivers by 2026-11-21, 5 days after Amazon's deliver-by Mon, Nov 16, 2026 (B00ED3K1VI G4602297, John Carter (Dallas, TX)). Labelled 'Delivery time high' on Amazon. Cancel it, or write Manual-C/-R to place it anyway. Ship-by Thu, Nov 12, 2026.
```
(b) Zoro's checkout page gives a late date:
```
🐢 *DELIVERY TIME HIGH*
Order 113-4461595-0151456 NOT placed — Zoro's checkout says delivery by 2026-11-21; Amazon's deliver-by is Mon, Nov 16, 2026 (B00ED3K1VI G4602297, John Carter (Dallas, TX)). Labelled 'Delivery time high' on Amazon. Cancel it, or write Manual-C/-R to place it anyway. Ship-by Thu, Nov 12, 2026.
```
(If the label could not be written: `⚠️ The 'Delivery time high' label could not be written on Amazon, so please set it by hand.`)

(c) No Zoro date on 3 tries:
```
🐢 *NO ZORO DELIVERY DATE*
Order 113-4461595-0151456 NOT placed — Zoro showed no delivery date on 3 tries (B00ED3K1VI G4602297, John Carter (Dallas, TX)). Nothing is written on Amazon and it is tried again every cycle. Cancel it, or write Manual-C/-R to place it without the date check. Ship-by Thu, Nov 12, 2026.
```
(d) Zoro cart busy on 6 tries:
```
🛒 *ZORO CART BUSY*
Order 113-4461595-0151456 NOT placed — Zoro kept refusing the HS_COUPON cart (busy) on 6 tries (B00ED3K1VI G4602297, John Carter (Dallas, TX)). Nothing was bought and nothing is written on Amazon; it is tried again every cycle. Ship-by Thu, Nov 12, 2026.
```
(e) Card declined on 3 cycles:
```
💳 *ZORO DECLINED THE CARD*
Order 113-4461595-0151456 NOT placed — Zoro declined the HS_RESELLER card on 3 cycles (3 tries each) — check the card (B00ED3K1VI G4602297, John Carter (Dallas, TX)). Nothing was bought and nothing is written on Amazon; it is tried again every cycle. Ship-by Thu, Nov 12, 2026.
```
(f) Two profiles on one Zoro account:
```
👥 *TWO PROFILES ON ONE ZORO ACCOUNT*
Order 113-4461595-0151456 NOT placed — HS_COUPON is logged in to the same Zoro account as another running profile — orders stop until each is on its own account (B00ED3K1VI G4602297, John Carter (Dallas, TX)). Nothing was bought and nothing is written on Amazon; the order waits. Ship-by Thu, Nov 12, 2026.
```

### 28. MANUAL-C — NO COUPON
- **When:** a Manual-C order where no coupon applied, so it is not placed; once per order (per order and code when the tag names a coupon).
- **Live:** path live; how often it fired is not logged (COUPON FAIL rows of any kind on 4 days since 28 Oct, last 4 Nov).
- **Text:** engine/orders.py:4717-4724, header :4727 (Telegram :4732). Callers :7059-7060 (a), :7069-7071 (b), :7103-7104 (c). Reasons for (a) come from :4673-4687 ("Zoro says it is not valid for this item", "The cart is under its $75.00 minimum", "Zoro says the code does not exist or has expired", "Zoro did not accept it"); for (c) from :7095-7097 / :2267-2332, and they start with a small letter after the full stop.

(a) the coupon written in the tag did not apply:
```
🖐️ *MANUAL-C — NO COUPON*
Manual-C order 113-4461595-0151456 NOT placed — the coupon SAVE20NOW written in its tag did not apply ($912.40, B00ED3K1VI G4602297, John Carter (Dallas, TX)). Zoro says it is not valid for this item. It keeps its tag. Write another code after Manual-C, plain Manual-C to use the sheet's coupons, or Manual-R to place it without a coupon.
```
(b) focus-coupon mode on:
```
🖐️ *MANUAL-C — NO COUPON*
Manual-C order 113-4461595-0151456 NOT placed — no coupon worked ($57.32, B00ED3K1VI G4602297, John Carter (Dallas, TX)). focus-coupon mode is on and WINTER711R26 does not apply (untick it to try the other coupons). It keeps its Manual-C tag and is tried again when the coupons change. Write Manual-R to place it now without a coupon.
```
(c) no coupon fits:
```
🖐️ *MANUAL-C — NO COUPON*
Manual-C order 113-4461595-0151456 NOT placed — no coupon worked ($57.32, B00ED3K1VI G4602297, John Carter (Dallas, TX)). no coupon applies to this cart. It keeps its Manual-C tag and is tried again when the coupons change. Write Manual-R to place it now without a coupon.
```
Other reasons in (c): `every coupon rejected this item — a coupon can never apply`, `no coupon fits this cart yet — waiting for the next coupon scrape`, `no live regular coupon in the pool (2 dead); reseller margin 3.1% too thin — waiting for the next coupon scrape`.

### 29. MANUAL CODE — WHICH ITEM?
- **When:** a multi-item order tagged "Manual-C/-R G…" where the placer cannot tell which item the code replaces; once per order per run.
- **Live:** yes, rare — 1 MANUAL_CODE_UNCLEAR order since 28 Oct (3–4 Nov).
- **Text:** engine/orders.py:4817-4818, header :4821 (Telegram :4826); reason :4800-4802.
```
🖐️ *MANUAL CODE — WHICH ITEM?*
Order 113-4461595-0151456 NOT placed — cannot tell which item G9988684 replaces — 2 items are out (B00ED3K1VI G4602297, B07B6B8SR8 G5605031). It keeps its tag and is tried again every cycle; remove the code to place only the items in stock, or place it by hand.
```
(Other reason: `cannot tell which item G9988684 replaces — every item is in stock`.)

### 30. BIG ORDER ($800+) — MARKED '20'
- **When:** a coupon-profile cart at or above `big_order_threshold` (800 today) where no priority coupon applied; labelled "20" on Amazon for a phone discount.
- **Live:** yes — 10 MARKED_20_CALL_ZORO orders since 28 Oct, last 10 Nov.
- **Text:** engine/orders.py:5082-5094 (Telegram :5095-5107); called :5890, :6025, :6225, :7141. "$800+" is written into the text, not read from the setting.
```
🚨 *BIG ORDER ($800+) — MARKED '20'*
━━━━━━━━━━━━━━━━━━━━━━
• Title: *Simple Green, SMP13006, Industrial Cleaner/Degreaser, 1 / Each, Dark Green, 5 Ga*
• SKU: G4602297
• ASIN: B00ED3K1VI
📦 *Order ID*: 113-4461595-0151456
💰 *Zoro Purchase*: *$912.40*
👤 *Buyer*: John Carter (Dallas, TX)
📝 *Amazon Tag*: Marked *'20'* in Seller Order ID
📞 *Action*: No priority coupon applies. Call Zoro customer service for manual 20% phone discount!
━━━━━━━━━━━━━━━━━━━━━━
```
(No buyer and no city: `👤 *Buyer*: Customer`.) Telegram twin has the same lines.

### 31. PLACED, THEN CANCELLED
- **When:** rush/ultra mode only: writing the WB back to Amazon finds the order cancelled although a Zoro order was placed for it.
- **Live:** rush and ultra are off for every profile in bot_config today; last fired 2 Nov (3 orders, HS_RESELLER).
- **Text:** engine/orders.py:841-842, header :851 (Telegram :856).
```
🛑 *PLACED, THEN CANCELLED*
Amazon order 113-4461595-0151456 is CANCELLED, but Zoro order WB3938136187 was placed for it. Cancel WB3938136187 on Zoro if it has not shipped.
```

### 32. Ultra: listing out of stock, orders held
- **When:** Ultra mode only: a listing's Zoro code and ALTs are out at the start-of-cycle stock check (with the count) or run out mid-cycle (no count); once per code until it is back.
- **Live:** Ultra is off for every profile in bot_config today; 28 OOS_HELD orders on 2–3 Nov.
- **Text:** engine/orders.py:1226-1229 (Telegram same words, code in bold); sent :1205-1218. No bold header.
```
⏸️ G4602297 (B00ED3K1VI) is out of stock on Zoro — DM_COUPON: 12 order(s) held, nothing marked on Amazon. Checked again every cycle.
```
```
⏸️ G4602297 (B00ED3K1VI) is out of stock on Zoro — DM_COUPON: its orders held, nothing marked on Amazon. Checked again every cycle.
```

### 33. Ultra: back in stock
- **When:** Ultra mode only: a code reported in #32 is buyable again.
- **Live:** as #32.
- **Text:** engine/orders.py:1291-1293.
```
✅ G4602297 is back in stock on Zoro — DM_COUPON: its held orders go through from this cycle.
```

Also landing in this group: every Wrenchly "Insights" alert (#5, #6, #7–#20, #22–#23), because no `wrenchly_insights` group is set.

---

## F. Telegram only (all off: telegram.enabled = false; Telegram also drops anything while the launcher is closed)

### 34. Dashboard address
- **When:** the cloud agent's public tunnel (cloudflared or localtunnel) gets a new address.
- **Live:** not sent (Telegram off).
- **Text:** scripts/cloud_agent.py:934.
```
🌐 Dashboard address (cloudflared): https://quiet-river-sample.trycloudflare.com
```

### 35. Hunter: new profitable listings
- **When:** a hunter cycle finds winners (up to 12 listed).
- **Live:** not sent (Telegram off). The "Review" link is a localhost address, so it does not open on a phone.
- **Text:** Amazon_DS_Hunting/hunter.py:1862-1867 (header bold, ASIN monospace, profit bold).
```
🎯 Hunter: 3 new profitable listing(s)
• B00ED3K1VI Simple Green — Zoro $63.69 → $89.99 = +$11.42 (17.9%) 45/mo
• B07B6B8SR8 Rust-Oleum — Zoro $24.10 → $39.95 = +$6.80 (28.2%) 30/mo
• B0GLHV8499  — Zoro $18.40 → $31.00 = +$4.05 (22.0%) 0/mo
Review: http://127.0.0.1:5055
```

---

## G. Never sent (dead code — nothing calls it, or it is filtered out)

### 36. OUT OF STOCK ALERT (OOS) — `notify_order_oos`
- Not called. Would go to the store's Orders Alerts group. utils/whatsapp.py:379-389.
```
🚨 *OUT OF STOCK ALERT (OOS)*

• Title: *Simple Green, SMP13006, Industrial Cleaner/Degreaser, 1 / Each, Dark Green, 5 Ga*
• SKU: G4602297
• ASIN: B00ED3K1VI
• Zoro Code: G4602297
• Order: 113-4461595-0151456

• Quantity: 1
• Buyer: John Carter
• Action: Supplier is OOS — marked on Amazon SC, manual review required
```

### 37. LOW PROFIT MARGIN WARNING (< 8%) — `notify_low_margin`
- Not called. Orders Alerts group. utils/whatsapp.py:418-429.
```
⚠️ *LOW PROFIT MARGIN WARNING (< 8%)*

• Title: *Simple Green, SMP13006, Industrial Cleaner/Degreaser, 1 / Each, Dark Green, 5 Ga*
• SKU: G4602297
• ASIN: B00ED3K1VI
• Order: 113-4461595-0151456

• Profit Margin: *5.4%* (Threshold: 8.0%)
• Net Profit:    *+$3.10*
• Payout / Cost: $60.42 / $57.32
• Coupon:        EMZMMV3JYKVJ
• Status:        Placed (review pricing)
```

### 38. HIGH PROFIT ORDER (> 20%) — `notify_high_profit`
- Not called. Orders Alerts group. utils/whatsapp.py:458-468.
```
🎉 *HIGH PROFIT ORDER (> 20%)* 🔥

• Title: *Simple Green, SMP13006, Industrial Cleaner/Degreaser, 1 / Each, Dark Green, 5 Ga*
• SKU: G4602297
• ASIN: B00ED3K1VI
• Order: 113-4461595-0151456

• Profit Margin: *+52.3%* 🔥
• Net Profit:    *+$15.32* 💵
• Payout / Cost: $44.62 / $29.30
• Coupon Used:   None
```

### 39. LISTING HIJACK ALERT — `notify_hijack`
- Not called. Insights group. utils/whatsapp.py:539-548.
```
🚨 *LISTING HIJACK ALERT: Position Dropped!*
• Title       : *Simple Green, SMP13006, Industrial Cleaner/Degreaser, 1 / Each, Dark Green, 5 Ga*
• SKU         : G4602297
• ASIN        : B00ED3K1VI
• Our Rank    : *#2* (Lost Buy Box!)
• Buy Box Won : Tools Depot LLC ($84.99)
• Our Price   : $89.99
• Action      : Check Repricer / Inventory immediately.
```

### 40. Placer insights filtered out by `PLACER_ALERT_CODES` (utils/insights_engine.py:890)
`analyze_order_insights` builds these texts after every placed order, but only DELIVERY_MISMATCH_LATE (#6) is sent. Headers only (each continues with the Title / SKU / ASIN / Order bullet block):

| Code | Header line | file:line |
|---|---|---|
| ITEM_PRICE_CHANGED | `📈 *AMAZON ITEM PRICE INCREASED* 🟢` / `📉 … DECREASED* 🔴` | utils/insights_engine.py:464 |
| ZORO_LIST_PRICE_CHANGED | `📈 *ZORO PRICE UP* — G4602297` (and down) | :515 |
| ZORO_BASE_PRICE_CHANGED | `… *ZORO BASE COST INCREASED/DECREASED* …` | :562 |
| ALT_CODE_USED | `🔄 *ALTERNATIVE SKU USED (Main OOS)*` | :614 |
| SHIPPING_MISMATCH | `⚠️ *SHIPPING CHARGE MISMATCH (Free Shipping Charged)*` | :650 |
| ZORO_SHIPPING_ANOMALY | `🚚 *ZORO SHIPPING FEE DEVIATION* ⚠️` | :694 |
| REDUCE_HANDLING_TIME | `⚡ *REDUCE HANDLING TIME OPPORTUNITY*` | :758 |
| LOW_ZORO_STOCK | `📦 *LOW SUPPLIER STOCK WARNING*` | :789 |
| CLEAN_EARNINGS_SHIFT | `💰 *CLEAN UNIT EARNINGS INCREASED* 🟢` / `📉 … DECREASED* 🔴` | :830 |
| HANDLING_TIME_REDUCED | `⚡ *AMAZON HANDLING TIME REDUCED*` | :866 |
