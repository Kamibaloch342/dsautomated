# FINAL REVIEW & CLEANUP — master plan (2026-07-04, Kamran's spec)

User's words: analyse order placers → dashboard → Zoro TSV integration ("into the
ASIN profile too"), clean legacy files, simplify GUI (remove FedEx page & unused),
better dashboard UI, perfect tag system + tag filtering alongside color flags,
move ALL backups/legacy/useless files into a delete-review folder (user reviews,
then deletes), debug + validate every logic, produce the full document.

Delete-review folder: `Development_Unit/_DELETE_REVIEW/` (mirrors original paths
so anything can be moved back). NOTHING is hard-deleted — moves only.

## Phases
- [x] P0 This plan written.
- [x] P1a Placer analysis DONE (order_placer_analysis.md): incident fix VERIFIED in (atomic lock+fail-closed gmail+9-inbox list). BUGS: split-order dup-spend path (worst), NameError orders.py:2545, STATE_RESTRICTED dict-as-WB, dup-list drift 9 vs 15 accounts.json, dead GUI safety knobs, stale $5.00 fee ×5 sites, fallback margin gate tax-inconsistent, gmail window 3d vs 7d doc. Orig: → FINAL_REVIEW/order_placer_analysis.md
- [x] P1b File inventory DONE: 50 items / ~1.2GB moved to _DELETE_REVIEW (582MB partial TSV temp, 510MB broken venv, 102MB Automations/backup, 4 dashboard .baks, closing_runner.py, 18 dead FA scripts). KEPT: DS_Closing (insights.py reads its CSV!), closing_pipeline.py (3 live importers), _dl_* (live). FLAGS: 2 Google SA keys in-repo actively used → rotate; 4.1GB FA Chrome profiles if tracker confirmed dead. Orig:: legacy/backup/dead files classified SAFE-MOVE /
        PROBABLY / KEEP → FINAL_REVIEW/file_inventory.md; SAFE-MOVE ones moved to
        _DELETE_REVIEW/ preserving relative paths.
- [x] P2a GUI launcher simplify (DONE: FedEx page removed — was already broken, SQL column mismatch; Settings page was UNREACHABLE, now in sidebar; 13 pages compile OK): remove FedEx page (dashboard covers it) + any page
        whose feature is dead; keep Dashboard/Orders/Closing(2-button)/Tracking/
        Coupons/Post-Delivery(view)/Unmapped/Needs-Priority/Log/Settings unless dead.
- [x] P2b DONE (tag chips col in Orders table, Tag dropdown + Flag filter in filter bar from extended /api/facets tags, .tagchip class unified, tabs flex-wrap; live-browser verified). Orig: tag chips + tag filter on Orders tab & Tracker (already has),
        tags column optional, tag filter combined with color-flag filter; general UI
        polish (consistent chips, spacing). KEY_COLS gets tags render as chips.
- [x] P2c DONE (/api/zoro_tsv batched ro lookup 1.7ms, self-healing ix_zoro_no index, ASIN-modal Zoro catalog section w/ availability/lead/price + Δ% drift warning — live test caught G7646065 +5.9%; order-drawer 🏭 Zoro-now line; absent-DB degrades silently). Orig: TSV Files/combined_reports.db (table zoro_data:
        zoro_no, lead_time, availabilityStatus, sales_price, unit_price…, 2M rows,
        rebuilt daily). Integrate into the dashboard ASIN modal ("profile") + order
        drawer + insights handling panel: for the order's/ASIN's zoro_sku show live
        lead_time, availability, current Zoro price vs our last placed unit cost
        (price-drift early warning). Server attaches TSV DB read-only per lookup
        (data may be absent → degrade gracefully).
- [x] P3 DONE: placer audit verdicts (profit gate PASS primary/FAIL fallback-consistency, coupons PASS w/caveats, address PASS, qty PASS, priority PASS, fleet dedup UNCLEAR local-lock-only) + 5 safe fixes applied (PO-box NameError, dup-inbox list now from accounts.json 15 unique fail-closed, $5.99 fee constant + dom $5.xx regex, gmail dup window 7d, honest sleep log). PENDING OWNER SIGN-OFF: split-order redesign, fallback margin gate, GUI knob wiring. Orig: agents validate profit/label/timezone/dedup/resume/
        messaging-guard logics; fix real bugs found; report.
- [x] P4 DONE: FULL_SYSTEM_DOCUMENT.md written (4,567 words, 8 sections, facts verified vs live code/DBs; NEW FIND: quick_download.py has hardcoded AWS keys → pending decision #4). Orig: architecture, every pipeline step, every script's
        purpose, dashboards, GUI, configs, DBs/tables, run-books, known limitations.

## Constraints
- Move, never delete. _DELETE_REVIEW/ + file_inventory.md for user review.
- Do not break: daily_bulk chain, closing.py, order placer (orders.py runs live!),
  dashboard server, launcher. Compile-verify everything touched.
- Live processes may be running — no schema-destructive ops on the unified DB.
- Candidate legacy roots (verify before moving): DS_Closing/ (superseded),
  Final Automation/ LEGACY parts (gmail_archive/build/config still USED by
  daily_bulk — keep those!), dashboard/*.bak, gui backups, Automations/backup/,
  zoro_pdp_dump.html, scratch_zoro.pdf, engine/closing_pipeline.py +
  closing_runner.py (replaced by closing.py — verify no imports), root-level
  deleted-in-git strays, Learning.py etc.
