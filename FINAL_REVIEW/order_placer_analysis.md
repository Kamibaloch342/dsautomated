# Order Placer (Worker A) — Code Analysis

Scope: `Automations/engine/orders.py` (the live placer) + everything it imports
(`utils/config.py`, `utils/placement_lock.py`, `platforms/{amazon,zoro,gmail,auth}.py`,
`checkout/{true_api,api,dom}.py`, `utils/{unified_db,helpers,pricing,accounts_config}.py`),
spawned by `gui/launcher.py`. Read-only review, 2026-07-05. All claims verified against
current code with file:line references; paths below are relative to `Automations/`.

---

## 1. ARCHITECTURE

### Process model
- `gui/launcher.py:2429-2442` (`_start_orders`) and `:2483-2497` (`_start_multi`) spawn
  `python -u engine/orders.py <PROFILE_KEY>` per profile (`_launch` at `gui/launcher.py:2508-2536`,
  env: `PYTHONPATH`, `PYTHONUNBUFFERED`; profile is an argv key, not an env var).
- Profiles defined in `utils/config.py:142-187` (`DM_COUPON` 9221, `DM_RESELLER` 9222,
  `HS_COUPON` 9223, `HS_RESELLER` 9224 — each attaches to its own debug-port Chrome).
- GUI overrides come from `bot_config.json` via `load_gui_config`/`apply_gui_overrides`
  (`utils/config.py:271-324`).
- Per-profile PID lockfile `engine/.script_<PROFILE>.lock` (`engine/orders.py:253-264`).

### Flow (one cycle)

```
main() engine/orders.py:2741
 ├─ _acquire_lock()                       PID lock, one instance per profile      :2744
 ├─ connect_chrome()                      attach to debug port / launch managed   :431
 ├─ Gmail auth: earnings inbox + 9 dup-check inboxes                              :2764-2770
 ├─ plock.init(seed_log=True)             FAIL-CLOSED: refuse to run w/o lock     :2775-2781
 └─ while True:                                                                    :2794
     ├─ plock.refresh_releases() + reconcile_orphans()                            :2812-2814
     ├─ load_sku_map()                    Sheets 'Verified_SKU_Dataset' (ASIN→G-code,
     │                                    qty_multiplier, zoro_multiple, 2nd_part) :1285
     ├─ load_coupon_codes()               Sheets 'Coupons' (priority %/fixed + regular);
     │                                    None → cycle aborts (fail-closed)        :2627-2734
     ├─ load_priority_asins_from_sheet()  'Priority_ASIN_Control' per profile      :1383
     ├─ scrape_order_ids(label)           SC orders-v3 search by seller-order-id label,
     │                                    filter validation (raises on mismatch)   :497-767
     ├─ scrape_order_ids(label_2)         optional secondary label (own margin)    :2854-2862
     ├─ favorites discovery               one buyer-action-view scrape, keep only
     │                                    unlabelled orders whose ASIN is priority :2864-2910
     ├─ _sort_orders_for_processing()     priority ASINs pinned, ASIN-freq batching :770-801
     └─ per order:
         ├─ scrape_order_details()        detail page: ASIN(s), qty (multi-strategy),
         │                                merchant id, dates, address              :986-1250
         └─ process_order()                                                        :1632
             ├─ [LOCK] plock.already_seen() hard local dedup, checked FIRST        :1646-1668
             ├─ merchant-id skip labels (WB…, OOS/SR/NET30/DUPLICATE/LOSS…)        :1671-1680
             ├─ ASIN→SKU resolve; final_qty = qty × qty_multiplier × zoro_multiple :1485-1521
             ├─ address validate (name/phone cleanup)  platforms/amazon.py:192-213
             ├─ earnings from Gmail "Sold, ship now" (no email → PENDING skip)     :1774-1801
             ├─ [DUP] Gmail cross-inbox check, FAIL-CLOSED sentinel                :1803-1837
             │        platforms/gmail.py:128-249 (DUP_CHECK_FAILED at :25)
             ├─ Zoro Select brand detect → 10% reseller discount on unit price     :1856-1976
             ├─ inventory + cart build (platforms/zoro.py:172,398,444)             :1904-1980
             ├─ coupon strategy estimate (_evaluate_order_strategy)                :1567-1629
             ├─ [LOCK] plock.reserve() atomic claim BEFORE money; ERROR→refuse     :2213-2235
             ├─ checkout chain: place_order_fast_true_api (checkout/true_api.py:1011)
             │     → place_order_fast_vuex (checkout/api.py:436)
             │     → place_order_fast DOM  (checkout/dom.py:1767)                  :2167-2211
             │   true_api steps: clear/add cart → filler-SKU top-up (:939) →
             │   address (:272) → cart ship addr, PO-box sentinel (:360) →
             │   ship methods, restrictedSkus → STATE_RESTRICTED (:429) →
             │   coupons first-match-wins (:601) → ratio-split check (:1146) →
             │   FINAL PRE-TAX MARGIN GATE (:1201-1214) →
             │   POST /v1/cart/{id}/order (:794) → audit line (:850)
             └─ result handling                                                    :2355-2620
                 ├─ SUCCESS: plock.confirm() FIRST, then verified WB write-back to
                 │   Amazon seller-id (platforms/amazon.py:244-311), seller note,
                 │   coupon-sheet cleanup, unified-DB upsert                       :2358-2493
                 ├─ LOW_PROFIT: release claim + label "Loss-Kamran checking"       :2495-2535
                 ├─ pre-payment fails (OOS/SR/POBOX/NET30/coupon): release, retry  :2537-2565
                 └─ unknown fail: dup-recheck → confirm as "<WB> - D", else
                     keep_failed → FAILED_KEPT (Locked Orders tab)                 :2566-2604
```

### Data outputs
- `data/rajar_logs/run_<ts>.csv` + `.json` — per-run log (`engine/orders.py:270-328`).
- `data/rajar_logs/order_log.txt` — one-liner audit per placement
  (`checkout/true_api.py:850-936`), synced to Sheets async; this file also seeds the
  placement lock at startup (`utils/placement_lock.py:76-113`).
- `data/rajar_logs/raw_api_log.txt` — raw submit payload/response (`true_api.py:46-63`).
- `data/automations_unified.db`:
  - `orders` table — written live by the placer via `insert_order/update_order`
    (`engine/orders.py:340-397` → `utils/unified_db.py:470-562`), with a dead-letter
    file `data/unified_db_write_failures.jsonl` on write failure.
  - `placer_claims` table — the dedup lock (`utils/placement_lock.py:61-72`).
  - `order_placer_orders` — NOT written by the placer directly; it is refreshed from
    `order_log.txt` by `scripts/sync_order_placer_logs.py`, invoked from
    `closing.py:1681-1690`. Dashboard reads it (`dashboard/server.py:271-286`).

---

## 2. BUGS (verified in code)

### 2.0 Incident fix status — IMPLEMENTED (all three root causes)
| Root cause (2026-07-01) | Fix | Verified at |
|---|---|---|
| No local lock | Atomic `placer_claims` reserve-before-spend, fail-closed on DB error, seeded from order_log, orphan healing, cross-process safe (PRIMARY KEY) | `utils/placement_lock.py:150-211,256-281`; `engine/orders.py:1646,2217-2235,2361,2775-2781` |
| `salesmercentile` missing from dup list | Both profile families now check all 9 confirmation inboxes incl. `salesmercentile`, `abecomllc02` | `utils/config.py:66-76` |
| Fail-open Gmail dup check | `DUP_CHECK_FAILED` sentinel with retry/backoff; engine skips order as `DUP_CHECK_UNVERIFIED` (retries next cycle) | `platforms/gmail.py:25,166-247`; `engine/orders.py:1809-1820` |

Extra rails also in: verified WB write-back (`platforms/amazon.py:299-311`), hard 300/cycle
safety cap (`engine/orders.py:123,2973`), duplicate-block circuit-breaker alert (`:3105-3113`),
confirm-before-write-back ordering (`:2361-2368`), unknown-failure dup-recheck then
FAILED_KEPT (`:2566-2604`). The design in the approved fix memo is fully present.

### 2.1 HIGH — money / fulfillment

**B1. Partial split placement is reported as full SUCCESS (under-fulfillment).**
`engine/orders.py:2248-2289` (pre-planned split) and `:2296-2343` (SPLIT_RETRY): the split
loop `break`s on the first failed sub-order; if at least one WB was placed,
`result_status = "SUCCESS"` (`:2279`, `:2335`) and the order returns `"success"` with only
the placed WBs written to the seller-id. Scenario: qty-4 order split [2,2]; split 2 hits
`LOW_PROFIT`/`FAILED` → only 2 units bought on Zoro, Amazon customer paid for 4, nothing
flags the shortfall (only a console line "Splits: 1/2"). Nobody is routed to buy the rest.

**B2. Split earnings divided evenly across unequal split quantities.**
`engine/orders.py:2250` and `:2311`: `split_earnings = earnings / split_count`, regardless
of each split's qty. A [3,1] plan gates the qty-3 sub-order against half the payout →
false `LOW_PROFIT` abort mid-split (which then triggers B1), and the qty-1 sub-order is
gated far too leniently. Earnings should be weighted by qty share.

**B3. Multi-item extras re-purchased on every split.**
`_run_single_checkout` (`engine/orders.py:2167-2211`) always passes `extra_skus`
(built at `:2129-2144`); the SPLIT_RETRY loop (`:2312-2317`) calls it once per split →
every split order re-adds ALL the extra SKUs (`checkout/true_api.py:1081-1089`), i.e. a
2-way split buys the extra items twice. Trigger: multi-item Amazon order + a fixed-amount
coupon whose ratio-split beats the single-order discount (`true_api.py:1150-1172`).
Related: `_compute_ratio_split`'s `unit_price = priceTotal/qty` (`true_api.py:1157-1158`)
is polluted by the extras' cost, so the split decision itself is computed on a wrong
unit price for multi-item carts. Cheapest safe fix: only allow SPLIT_RETRY when
`extra_skus`/`second_sku` are empty.

**B4. NameError crash on PO-box rejection at checkout.**
`engine/orders.py:2545`: `status_note = f"PO Box not allowed by Zoro ({address.get('city', '')}, {state})"` —
the variable is `addr`; `address` does not exist in `process_order`. When Zoro returns the
`PO_BOX_RESTRICTED` sentinel: the "POBOX" label is written (`:2544`), then the f-string
raises NameError → escapes to the generic handler (`:3133`) → the claim is never
`release()`d (the reconcile block at `:2561-2565`, which lists PO_BOX_RESTRICTED as safe,
is never reached) → next cycle `reconcile_orphans` converts it to FAILED_KEPT. Effect:
every PO-box checkout rejection produces a spurious Locked Orders entry and no CSV log
row. No money spent, but it pollutes the exact human-review queue built for real
possible-spend failures. One-word fix (`addr`).

**B5. DOM-fallback state-restriction returns a dict that is treated as a WB id.**
`_dom_place_order_fallback` returns
`_format_checkout_result("STATE_RESTRICTED", return_details=return_details)`
(`checkout/true_api.py:1417-1421`) — a dict when `return_details=True` — but its callers
assign the return to `zoro_id` (`true_api.py:1269-1274` reseller-no-cards path,
`:1292-1299` coupon API-fail path) and truthiness-check it: the dict is truthy →
"ORDER RESULT: {dict}", the used coupon is marked USED (`:1307-1308`), a garbage
`WB:{'status': ...}` line is appended to order_log.txt (`:1310`), and the engine then
crashes at `result_id.startswith("WB")` (`engine/orders.py:2358`, AttributeError on dict)
→ claim orphaned → FAILED_KEPT. No money spent, but a coupon is burned, the audit log is
polluted, and a restricted-state order is permanently locked instead of labeled "SR".

**B6. Fallback checkout paths gate margin on the tax-INCLUSIVE total.**
`checkout/dom.py:1944-1956` and `checkout/api.py:571-580` compute
`margin = (earnings - checkout_total)/checkout_total` on the grand total and compare it to
`min_margin`, while the primary path deliberately gates pre-tax because tax is refunded
(`checkout/true_api.py:1201-1214`). Whenever true_api throws and the run degrades to
Vuex/DOM (`engine/orders.py:2194-2210`), profitable orders near the threshold are falsely
rejected as LOW_PROFIT — which also labels them "Loss-Kamran checking" (`:2499`), blocking
automatic retry until a human clears the label.

### 2.2 MEDIUM — hardcoded lists that bypass `config/accounts.json` (the declared single source of truth)

**B7. The dup-check inbox list is hardcoded again.**
`utils/config.py:66-69` (`ALL_CONFIRMATION_INBOXES`) is a literal list; the placer never
imports `utils/accounts_config.py` (grep: no engine/config reference), so editing
`config/accounts.json` — whose own `_comment` says "edit ONLY this file … hardcoded
per-file lists are what caused the 2026-07-01 duplicate-order incident" — does NOT extend
the placer's duplicate check. `accounts.json:13-16` lists 15 `zoro_accounts`;
`ALL_CONFIRMATION_INBOXES` has 9. Add a new Zoro inbox to accounts.json and the drift that
caused the incident recurs (mitigated now by the local lock, but the Gmail layer is the
only defense against duplicates placed outside this machine/process).

**B8. Account attribution hardcoded.**
`engine/orders.py:349`: `account = "Hathoro" if PROFILE_NAME.startswith("DM_") else "HOANK"`.
Should be derived from accounts.json / profile config. A new store or renamed profile
mis-attributes every DB row (closing + dashboard join on `account`).

**B9. Other magic values that belong in config:**
- `TARGET_LABEL == 'Rajar 1'` disables coupons (`engine/orders.py:2083`).
- Earnings inboxes duplicated: `utils/config.py:57-58` vs `accounts.json` `earnings_inbox`.
- HS_COUPON billing address embeds `"FDLE HQ 813 LAKE BRADFORD RD"` (`utils/config.py:126-136`) —
  the state police HQ as billing address is at minimum a config smell worth confirming.

### 2.3 MEDIUM — stale money constants (Zoro fee change June 2026: $5.00 → $5.99; free-ship threshold $50 pre-coupon)

**B10. $5.00 small-order fee hardcoded in 5+ places.**
- `engine/orders.py:1612` `ship = 5.0 if total < 50.0 else 0.0`; `:1618`; `:2019`
  (`estimated_shipping = 5.0 if real_cost < 50.0 else 0.0`); `:2043` prints "+$5 ship".
- `utils/config.py:85` `ZORO_SELECT_SHIPPING_FLAT = 5.0` (defined, currently unused in engine math).
- `checkout/dom.py:1143` and `:1224`: checkout-readiness JS matches the literal strings
  `'$5.00'`/`'$8.00'` — if the page now renders "$5.99", the DOM fallback's
  shipping-ready detection can fail entirely, degrading the (already last-resort) DOM path.
Estimates under-count cost by $0.99 per sub-$50 order. The FINAL gate uses real cart totals
(`true_api.py:1186-1214`) so placement decisions are safe, but strategy prints, filler
economics and `est_shipping` written to logs are all wrong by $0.99.

**B11. Free-shipping threshold applied to the POST-coupon total in estimates.**
`engine/orders.py:1617-1619`: `single_ship = 5.0 if single_pct_cost < 50.0` — the $50
threshold is on the PRE-coupon subtotal, so a $60 order with a 20% coupon ($48) is
estimated with +$5.99 shipping it will not actually pay. (The real filler logic in
`checkout/true_api.py:939-977` runs before coupons on `priceTotal` — correct.)

**B12. Filler price assumption.**
`utils/config.py:89-93`: `SHIPPING_FILLER_PRICE = 0.71` is an expectation, not a live
price. `true_api.py:959` caps qty at `FILLER_MAX_QTY=5` using the assumed price; if the
filler SKU's real price changed, the capped qty may leave the cart under $50 → the order
pays the shipping fee AND the filler cost. The engine-side estimate (`engine/orders.py:2028-2038`)
also uses the assumed price and does NOT apply the `FILLER_MAX_QTY` cap the checkout uses
(`math.ceil(gap/price)` uncapped at `:2031`) — estimate and execution can diverge.

### 2.4 MEDIUM — duplicate-protection edge cases (residual)

**B13. Gmail dup window is 3 days; the docstring claims 7.**
`platforms/gmail.py:172` (`newer_than:3d`) vs docstring `:135` ("7-day lookback").
Duplicates whose confirmation email is 3–7 days old are invisible to the Gmail layer.
The local lock covers this machine's own placements; manual/Worker-B placements older
than 3 days are not covered.

**B14. Lock seeding only recognizes `WB` ids.**
`utils/placement_lock.py:89`: `if wb.startswith("WB")` — `WW` (Grainger-side) placements
in `order_log.txt` do not seed the block set. Low impact while the placer is Zoro-only,
but the dup-check and engine both treat WW as a valid "already ordered" id
(`gmail.py:226-233`, `engine/orders.py:1659,2224`), so the asymmetry is inconsistent.

**B15. PID lockfile is check-then-write (non-atomic).**
`engine/orders.py:253-264`: two same-profile processes starting simultaneously can both
pass the check; PID-reuse can also false-block. The placement lock prevents double BUYING,
but double processing / cart clobbering on the same Chrome is possible. Same pattern in
`utils/accounts_config.py:113-133` (and that one continues WITHOUT a lock on error, `:130-133`).

**B16. No cross-profile serialization on the Zoro account itself.**
Cart ops (`platforms/zoro.py:398-441`, `true_api.py:130-163`) and the default-shipping-
address overwrite (`true_api.py:272-353`) mutate account-level state. If two Chrome
profiles are ever logged into the SAME Zoro account, concurrent placers can clear each
other's carts mid-checkout or swap the shipping address between address-set and submit —
worst case an order ships to the wrong buyer. Nothing asserts that the four profiles use
distinct Zoro logins. (UNCLEAR whether this can happen operationally; there is no guard.)

### 2.5 MEDIUM — controls that silently do nothing

**B17. GUI order-limit and all velocity settings are dead.**
- `order_limit`: launcher writes it to `bot_config.json` (`gui/launcher.py:2250-2251`);
  `apply_gui_overrides` stores it into `PROFILES[p]["order_limit"]` (`utils/config.py:310-313`);
  but the engine only reads the module constant `ORDER_LIMIT = 0` (`utils/config.py:36`,
  imported at `engine/orders.py:58`, used at `:2977`). Setting a limit in the GUI has no effect.
- `velocity.max_per_cycle`, `cycle_sleep`, `inter_order_delay` (`bot_config.json`,
  written at `gui/launcher.py:2267-2271`) are read by NOTHING (grep confirms). The engine
  uses `MAX_ORDERS_PER_CYCLE = 0` (`utils/config.py:45`) → only the hardcoded 300-order
  safety cap applies (`engine/orders.py:123,2973`).
- Cycle sleep: the engine prints "Waiting 2 mins..." (`:3151`) then `time.sleep(10)`
  (`:3157`). The operator believes a 120s throttle exists; the real cadence is ~10s+scrape.
- `est_margin_only` toggle: parsed into `MARGIN_CHECK_EST_ONLY` (`engine/orders.py:148`)
  and never used (obsolete per comment `:2118-2124`) — the GUI switch is decorative.
Post-incident, velocity caps are a named safety rail; having them displayed but inert is
itself a risk.

### 2.6 LOW — error handling / bookkeeping

**B18. Coupon "applied with $0 discount" counts as success.**
`checkout/true_api.py:596-598`: `_apply_coupon_api` returns `(True, 0)` when the promo PUT
succeeded but the cart shows no discount. `_apply_coupons_api` then returns SUCCESS
(`:634-642, :659-667, :696-704`), caches it as the working pair, stops trying other
coupons, marks it USED after the order (`:1307-1308`) and the engine deletes non-priority
codes from the sheet (`engine/orders.py:2488-2492`). A dud coupon burns a real code and
blocks better ones for that zoro_code for the rest of the run.

**B19. LOW_PROFIT log prints the wrong threshold.**
`engine/orders.py:2500` uses `MIN_MARGIN` in the message, but the gate used the order's
own `_margin_thresh` (`:2123`), which is `MIN_MARGIN_2` for secondary-label orders.
Cosmetic, but it misleads the review of "Loss-Kamran checking" orders.

**B20. Duplicated code / double work.**
- `extra_skus` block is built twice back-to-back (`engine/orders.py:2129-2134` and `:2136-2144`).
- Split-success path writes the seller note twice (`:2409-2428` inside the split branch,
  then unconditionally again at `:2466-2487`).

**B21. Startup Gmail auth can hang or kill the placer.**
`platforms/gmail.py:37-51`: a failed token refresh deletes the token and calls
`InstalledAppFlow.run_local_server()` — under the GUI-spawned headless subprocess
(stdin=DEVNULL, `gui/launcher.py:2529`) this blocks forever waiting for an OAuth browser
round-trip; any other auth exception at `engine/orders.py:2764-2770` (no try/except)
kills the run. Fail-closed (good) but brittle: one stale token silently stops all placing.

**B22. `insert_order` on an existing row overwrites all provided fields.**
`utils/unified_db.py:486-500`: when the row exists it UPDATEs every column present in
`data`, so a re-processed order can regress enriched fields (tracking, delivery) back to
placement-time values. The engine's own `_upsert_unified_db` avoids the worst of this by
calling `update_order` only when `insert_order` returns False (`engine/orders.py:374-378`) —
but `insert_order` itself already did the overwrite in that case, so the guard is illusory:
the "already exists — try to enrich" branch never runs (insert_order returns False *after*
updating). Behavior is "last write wins", not "insert or enrich".

**B23. `pd` shadowing.**
`engine/orders.py:38` imports `pandas as pd`; `:1734` rebinds `pd` as a local
(purchase-date string) inside `process_order`. Currently harmless (pandas is not used in
that function), but any future pandas call inside `process_order` becomes a latent bug.

---

## 3. IMPROVEMENTS — ranked by money-risk

### Fix first (direct money/fulfillment exposure)
1. **Split-order trio (B1+B2+B3).** Quick wins: (a) weight `split_earnings` by qty share;
   (b) restrict SPLIT_RETRY to single-item orders (`if extra_skus or second_sku: skip split`);
   (c) on partial split failure, do NOT return "success" — write a distinct
   `PARTIAL_SPLIT` status and `plock.keep_failed`-style review entry for the unplaced
   remainder. These are the only paths in the current code that can silently spend the
   wrong amount or under-fulfill a paid order.
2. **PO-box NameError (B4).** One-word fix (`address` → `addr`) stops spurious
   FAILED_KEPT locks and restores the intended release/retry semantics.
3. **DOM-fallback dict-as-WB (B5).** Make `_dom_place_order_fallback` return
   `(status, wb_id)` or plain `None`/str; never a details dict. Prevents coupon burn,
   log pollution, wrong terminal status.
4. **Fallback margin gates pre-tax (B6).** Pass the pre-tax basis (cart `priceTotal`)
   into the dom/api gates or read it via the cart API before gating, matching
   `true_api.py:1206`.

### Fix second (drift / config poisoning — the incident class)
5. **Read inbox + account lists from `config/accounts.json` (B7, B8, B9).** The loader
   (`utils/accounts_config.py`) already exists; wire `ALL_CONFIRMATION_INBOXES`,
   earnings inboxes, and the profile→account mapping through it. This is the exact
   drift that caused the $11.7k incident; the local lock reduces but does not eliminate
   the exposure (it cannot see orders placed by other machines/workers).
6. **Wire or remove the GUI velocity/order-limit controls (B17).** Either honor
   `order_limit`, `max_per_cycle`, `cycle_sleep` in the engine (read `CONFIG` /
   `gui_cfg["velocity"]` at cycle start) or delete the controls; also fix the
   "Waiting 2 mins" vs `sleep(10)` lie. A safety rail the operator believes exists but
   doesn't is worse than none.

### Fix third (stale values / estimates)
7. **Centralize fee + threshold (B10, B11, B12).** Add `ZORO_SMALL_ORDER_FEE = 5.99` and
   use it everywhere (engine 1612/1618/2019/2043); apply the free-ship threshold to the
   pre-coupon subtotal in estimates; replace the `'$5.00'`/`'$8.00'` literals in
   `dom.py:1143,1224` with a fee-agnostic check ("Standard Ground" text only); read the
   filler's live price before computing filler qty.
8. **Coupon $0-discount = failure (B18).** In `_apply_coupon_api`, treat
   `applied && discountTotal == 0` as a failed pair (don't cache, don't mark USED,
   don't delete from sheet) — a 3-line change.
9. **Gmail dup window (B13).** Bump `newer_than:3d` → `7d` (or make it a config const)
   to match the documented intent; retry/backoff already limits the rate-limit cost.

### Bigger refactors (worth scheduling, not urgent)
10. **One checkout result contract.** `_format_checkout_result`'s dual string/dict return
    (`dom.py:1739-1760`) is the root of B5 and of the copy-pasted result handling in the
    engine. Always return the dict; adapt the two legacy callers.
11. **Decompose `process_order` (~1,000 lines) into stages** (dedup → resolve → price →
    gate → checkout → reconcile) with explicit state, so the reconcile block can never be
    skipped by an exception mid-elif-chain (the B4 failure mode by construction).
12. **Single write path for placement records.** Today: order_log.txt (authoritative for
    lock seeding) + `orders` table (live) + `order_placer_orders` (derived by closing's
    sync). Write the DB row first and derive the text log, or at least have the placer
    write `order_placer_orders` itself (`upsert_placer_order` already exists,
    `utils/unified_db.py:808`).
13. **Per-Zoro-account mutex (B16)** if profile↔Zoro-login mapping is not guaranteed 1:1.

---

## 4. CORE LOGIC VALIDATION

### 4.1 Profit gate — PASS (primary path), FAIL (fallback consistency)
- Primary: gate runs AFTER coupons on the real cart, pre-tax basis, before submission —
  `checkout/true_api.py:1201-1214`; threshold is per-label (`_margin_thresh` set at
  `engine/orders.py:2851,2861`, consumed at `:2123-2124`); GUI margins flow through
  `bot_config.json` → `apply_gui_overrides` (`utils/config.py:290-307`) → `CONFIG` at
  startup (`engine/orders.py:146-147`). Currently DM_COUPON -5%, DM_RESELLER 0%,
  HS -5% (bot_config.json).
- LOW_PROFIT is detected BEFORE payment; claim released; order labeled — no money spent
  (`engine/orders.py:2495-2535`). Evidence: submission happens only after the gate in
  `true_api.py` (`:1218+` follows `:1210`).
- No-earnings orders are skipped, not placed blind (`:1780-1792`) — fail-safe.
- FAIL component: Vuex/DOM fallbacks gate tax-inclusive (B6) — inconsistent policy.
- Note: the pre-checkout ESTIMATE no longer rejects (removed by request, comment at
  `engine/orders.py:2075-2080`) — placement decisions ride entirely on the final gate.

### 4.2 Coupon application — PASS with caveats
- Order: strategy-preferred → priority (sheet rows with % / fixed values) → regular;
  first match wins; per-zoro_code failed-pair cache persists across cycles within a run
  (`checkout/true_api.py:601-726`; cache built at `engine/orders.py:2786-2791`).
- Expired codes are globally invalidated; "invalid/not valid" only per-item
  (`true_api.py:669-684` — correct distinction).
- Regular (single-use) codes are deleted from the sheet only after a confirmed WB and only
  if not a priority code (`engine/orders.py:2488-2492`) — correct.
- Coupon load failure aborts the cycle instead of placing coupon-less (`:2826-2832`) — correct.
- Caveats: $0-discount false success (B18); legacy bot_config priority coupons assumed
  10% (`:2713-2715`) which can skew the estimate (informational only).

### 4.3 Address handling — PASS
- PO Box: blocked at Amazon scrape (`platforms/amazon.py:161-163`,
  regex `\bP\.?\s*O\.?\s*BOX\b`) AND at Zoro via the API error sentinel with one
  sanitize-retry (`checkout/true_api.py:394-419` → `PO_BOX_RESTRICTED` handled at
  `:1110-1111`). Labeling of the Amazon order after the Zoro-side detection is broken by
  B4 (crash), though the "POBOX" label itself is written first.
- Restricted states: PR/HI/AK blocked pre-checkout (`utils/config.py:52`,
  `platforms/amazon.py:150-151`); Zoro's own state restrictions detected via
  `restrictedSkus` in the shipping-methods response (`true_api.py:464-468` →
  `STATE_RESTRICTED` → "SR" label, `engine/orders.py:2540-2542`) and via page text in the
  DOM path (`dom.py:1900-1906`).
- 35-char line limits handled with word-boundary spill to line2/company
  (`true_api.py:284-312`); the zoro.py variant hard-truncates overflow (`zoro.py:530-534`)
  — a very long address can silently lose its tail in the legacy path (minor misdelivery risk).
- Name normalization keeps only the first two words (`amazon.py:199-207`): "Mary Jane
  Watson Parker" ships as "Mary Jane" — deliberate simplification, acceptable; phone falls
  back to the office number when invalid (`:211`).

### 4.4 Qty handling — PASS
- Detail-page extraction is defense-in-depth: explicit qty cell → header-indexed column →
  qty-followed-by-$ heuristic → regex near ASIN/title anchor → whole-page single-qty
  (`engine/orders.py:928-983,1034-1113,886-925`); list-view qty is a backstop only when
  the detail page says ≤1 (`:860-865`, `:3067-3074`).
- `final_qty = order_qty × qty_multiplier × zoro_multiple`, floored at 1
  (`:1504-1508`) — matches the sheet contract.
- Cart add verifies the actual cart qty and REJECTS mismatches instead of proceeding
  (`platforms/zoro.py:433-438`, `true_api.py:202-215`) — prevents silent under/over-buys.
- Caveat: the ≤1-detected-then-trust-fallback rule (`:860-865`) means a mis-parsed
  list-view qty >1 can override a genuine qty-1 detail page; both read the same UI so the
  realistic risk is low.

### 4.5 Priority / favorites logic — PASS
- Priority ASINs reload from the sheet every cycle; sheet is the only source
  (`engine/orders.py:2834-2841`, loader `:1383-1415` with column-per-profile mapping `:1375-1380`).
- Favorites discovery: ONE scrape of the saved buyer-action view (`:2869-2910`,
  URL `:171`); adds only unlabelled orders whose ASIN is in the priority set; labelled
  orders and non-priority ASINs skipped; unlabelled non-priority is logged
  `NEEDS_PRIORITY` (report-only, `:2887-2895`) for the launcher/dashboard queue —
  it does NOT place them.
- Sorting pins priority ASINs in sheet order, then batches repeated ASINs by frequency
  (`:770-801`) — matches intent.
- Caveat (by design, worth knowing): both profiles of one store scan the same favorites
  view, so an unlabelled priority order can be claimed by either profile; the placement
  lock arbitrates (`placement_lock.py:150-211`), so no duplicate — but which margin
  threshold/PO applies is first-come-first-served.

### 4.6 Duplicate protection overall — PASS (single machine), UNCLEAR (fleet)
- On this machine, the layered defense (label skip → local lock → Gmail cross-inbox →
  reserve-before-spend → confirm-before-write-back → FAILED_KEPT on unknown) closes the
  incident loop; the circuit-breaker alert (`engine/orders.py:3105-3113`) surfaces the
  re-scrape signature.
- Across machines/workers, the only defenses are the Amazon seller-id label and the
  Gmail check — the latter now 3-day-limited (B13) and inbox-list-drift-prone (B7).
  The lock DB is local; if a second worker ever runs a placer, the same order could be
  bought twice. Currently Worker B is dashboard-only, so acceptable — but document it.

---

## Fixes applied (2026-07-04)

Surgical fixes applied to the live placer; each finding re-verified against current code
before editing. All four edited files pass `py_compile` (venv Python 3.9, no syntax
warnings). Paths relative to `Automations/`.

1. **B4 — PO-box NameError (`engine/orders.py:2545`).** `address.get('city', '')` →
   `addr.get('city', '')`. Verified: `process_order` defines only `addr` (`:1757`); no
   module-level `address` exists. The `PO_BOX_RESTRICTED` branch now reaches the
   `_SAFE_PREPAYMENT` release block (`:2561-2565`) instead of crashing into FAILED_KEPT.

2. **B7 — dup-check inboxes now read from `config/accounts.json`
   (`utils/config.py:59-96`).** `ALL_CONFIRMATION_INBOXES` is built by
   `_load_confirmation_inboxes()` (`:77-93`): order-preserving dedup union of
   `zoro_accounts` + `gmail_accounts` via `utils/accounts_config.py` (stdlib-only module —
   no circular import). FAIL-CLOSED: unreadable JSON or empty lists fall back to the
   previous frozen 9-inbox list (`_FALLBACK_CONFIRMATION_INBOXES`, `:71-74`); both paths
   tested. Resolved list today: **15 unique inboxes** (was 9); `DM_/HS_/DUPLICATE_CHECK_LIST`
   copies inherit it unchanged.

3. **B10 — stale $5.00 small-order fee.**
   - `utils/config.py:114` — new `ZORO_SMALL_ORDER_FEE = 5.99`; `:115`
     `ZORO_SELECT_SHIPPING_FLAT` kept as an alias (= 5.99) for the legacy import at
     `engine/orders.py:1857` (otherwise unused).
   - `engine/orders.py:59` imports the constant; estimate sites `:1612`, `:1618`, `:2019`
     now use it (5.0 literals removed); `:2043` prints the real fee
     (`+$5.99 ship`) instead of the hardcoded "+$5 ship".
   - `checkout/dom.py:1143` and `:1224` — readiness match `t.includes('$5.00')` replaced
     with the JS regex `/\$5\.\d\d/.test(t)` (accepts $5.00, $5.99, any $5.xx; verified
     rendered JS and match semantics). `$8.00`/`Standard Ground` alternatives unchanged.
   - Grep-verified: no `5.0`-fee or `'$5.00'` literals remain in the touched money paths.
   - NOT changed (out of scope, still open): B11 post-coupon threshold at `:1618`, B12
     filler price assumption, `dom.py:192/1552` comments.

4. **B13 — Gmail dup-check window (`platforms/gmail.py:173`).** `newer_than:3d` →
   `newer_than:7d`, matching the documented 7-day design (docstring `:135` is now true);
   comment updated. More conservative — catches day-4..7 confirmation emails.

5. **B17 (log line only) — `engine/orders.py:3151`.** "Waiting 2 mins..." →
   "Waiting 10 seconds...", matching the actual `time.sleep(10)` at `:3157`. Sleep
   duration deliberately NOT changed.

Not touched (need owner sign-off, documented above): split-order trio B1/B2/B3,
DOM-fallback dict return B5, fallback tax-inclusive margin gates B6, GUI velocity/limit
wiring B17 (rest), account attribution B8, coupon $0-discount B18.


## Fix applied (2026-07-05): transient coupon-error cache poisoning
Live case G306004662 (Arocep ammonia): at 19:44-19:45 ALL 34 coupons "failed" with
Zoro's transient `'conflict while modifying resource, try again'` (409). The code
counted these as real rejections, declared COUPON FAIL, and added the item to
`no_coupon_codes` — permanently skipping it ("All coupons exhausted") even though
SUPPLIES626 provably applies to this item ($3.15 at 02:41 same day, and manually
on the website). FIXED in checkout/true_api.py:
- `_is_transient_coupon_error()` classifier (conflict/try-again/timeout/429/5xx).
- `_apply_coupon_api`: retries transient PUTs 3× with backoff (and DELETE→PUT gap
  0.2s→0.5s, the likely conflict source); returns `TRANSIENT:`-prefixed error.
- `_apply_coupons_api`: transient errors never enter `failed_pairs`; when any
  transient occurred, the item is NOT added to `no_coupon_codes` — full retry
  next cycle. Verified: py_compile + 5-case classifier unit test PASS.
NOTE: a placer process running before this fix still holds the poisoned in-memory
cache — restart the profile to clear it.
