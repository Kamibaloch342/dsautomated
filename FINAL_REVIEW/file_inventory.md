# File Inventory — Repo Cleanup (2026-07-05)

All moves went to `_DELETE_REVIEW/` with **relative paths preserved**. Nothing was deleted.
To restore anything: `mv "_DELETE_REVIEW/<path>" "<path>"`.

**Total moved: 1,225,652 KB ≈ 1.2 GB (50 items)**

Reference-proof method: `grep -r` for each basename/module name across `Automations/`,
`Final Automation/`, `TSV Files/`, `DS_Closing/` (excluding `.git`, `.venv`, `__pycache__`,
`backup/` folders, `_DELETE_REVIEW/`), plus checks of `crontab -l` (empty) and
`~/Library/LaunchAgents/` (only `com.dropship.dashboard.plist`, which runs
`Automations/dashboard/server.py` — untouched).

---

## 1. MOVED (safe — verified unreferenced)

### Root of Development_Unit/
| Original → `_DELETE_REVIEW/<same path>` | Size | Proof it's dead |
|---|---|---|
| `.venv.broken-py314-20260405-1/` | 510 MB | Broken venv (name says so); no script references it; live venv is `.venv/` |
| `.aider.chat.history.md`, `.aider.input.history`, `.aider.tags.cache.v4/` | 252 KB | Aider AI-tool leftovers from Apr 28; regenerable caches, referenced by nothing |
| `check_order_msg.py` | 8 KB | Zero references in all three stacks; one-off debug script (Apr 24) |
| `testing 1 feb report.txt` | 48 KB | Zero references; ad-hoc test report dump |
| `.tracker_hathoro.lock`, `.tracker_hoank.lock` | 8 KB | Stale 5-byte lock files (May 2) from the retired FA tracker; locks are recreated at runtime if ever needed |
| `.DS_Store` | 20 KB | Finder metadata, regenerable |

### Automations/
| Original | Size | Proof |
|---|---|---|
| `Automations/backup/` (whole folder: `legacy/*.py.bak`, `development/`, `archives/`, `bot.log`, `report_closing.py.retired-20260613`) | 102 MB | No file outside `backup/` references anything inside it (grep for `Automations/backup`, `from backup`, and each retired basename) |
| `Automations/dashboard/index_OLD.html.bak` | 40 KB | grep for `index_OLD` in dashboard code: zero hits; `index.html` does NOT reference it |
| `Automations/dashboard/index_v2.html.bak` | 32 KB | grep `index_v2`: zero hits |
| `Automations/dashboard/index_cockpit_20260622.html.bak` | 48 KB | grep `index_cockpit`: zero hits |
| `Automations/dashboard/server_OLD.py.bak` | 4 KB | grep `server_OLD`: zero hits; launchd runs `server.py` |
| `Automations/engine/closing_runner.py` | 40 KB | Replaced by `closing.py` (its own header says "replaces closing_runner + closing_pipeline"). Only references are comments in `closing.py` and its own docstring. Not imported by `gui/launcher.py`, `scripts/reconcile_test.py`, or anything else. NOTE: `closing_pipeline.py` was **kept** — see §3 |
| `Automations/update_sheet_headers.py` | 4 KB | Zero references anywhere (code, configs, cron, launchd) |
| `Automations/zoro_pdp_dump.html` | 832 KB | Output artifact written by `scripts/dump_zoro_pdp.py` — regenerated on next run |
| `Automations/scratch_zoro.pdf` | 48 KB | Scratch output written by `scripts/fetch_zoro_pdf_receipts.py` each run — regenerated |
| `Automations/.DS_Store` | 16 KB | Finder metadata |

### Final Automation/ (retired v1–v3 tracker tooling — superseded by Automations/)
All below have **zero code references** (grep across all stacks incl. `*.sh`, `*.service`,
`daily_bulk.py` subprocess calls, and the FA live modules `config/utils/gmail_archive/
build_gmail_archive/full_archive_build`):

| Original | Size | Proof |
|---|---|---|
| `Final Automation/backup/` (`unified_tracker.py.bak`, `*.backup.2026-05-24` of config/db/utils/unified_tracker) | 556 KB | Backup snapshots; nothing imports from `backup/` |
| `Refunds_Received.py` | 24 KB | 0 refs (Mar 1, pre-dates Automations stack) |
| `amazon_earnings.py`, `amazon_report.py`, `zoro_database.py` | 28 KB | 0 refs. Docstrings say "Ported from DS_Closing/..."; logic re-ported into Automations engine. Not imported by `unified_tracker.py` or `tracker_test_local.py` (checked incl. dynamic imports) |
| `dashboard.py`, `dashboard_server.py` | 40 KB | 0 refs. Old FA dashboard (Apr 28); live dashboard is `Automations/dashboard/server.py` (launchd) |
| `fedex_checker.py`, `fedex_requests.py` | 24 KB | Only ref is a comment in `Automations/engine/fedex_api.py` ("Ported from Final Automation/fedex_requests.py"). `unified_tracker.py` imports only `config`, `db`, `utils` |
| `health_check.py` | 4 KB | Referenced only by `docs/LINUX_DEPLOYMENT.md` (a never-executed Linux deployment guide with `YOUR_USERNAME_HERE` placeholders) |
| `analyze_sheet.py`, `clear_sheet.py`, `verify_sheet.py`, `sheet_cleanup.py`, `compare_dups.py` | 40 KB | 0 refs; one-off Google-Sheet maintenance scripts from the Sheets era (repo moved to SQLite) |
| `sample_archive.py` | 8 KB | 0 refs; was the only importer of `gmail_prefetch.py` (which was kept, see §2) |
| `build_gmail_archive_mass.py` | 16 KB | 0 refs; superseded by `build_gmail_archive.py` (live) + `full_archive_build.py` (live) |
| `test_extract_v4.py` | 20 KB | 0 refs; one-off extraction test (its fixtures in `testing/eml_samples/` were kept in place) |
| `tracking_finder.py` | 8 KB | 0 refs (Feb 11) |
| `.DS_Store`, `All_Tokens/.DS_Store` | 20 KB | Finder metadata |

### TSV Files/
| Original | Size | Proof |
|---|---|---|
| `TSV Files/zoro_datafeed_00.tsv.c66dA4F6` | **582 MB** | Leftover partial-download temp file (random-suffix atomic write), timestamped 1 min before the final `zoro_datafeed_00.tsv` of identical size. The real `.tsv` is untouched |
| `TSV Files/.DS_Store` | 12 KB | Finder metadata |

### DS_Closing/ + Listings_Hunting/ (metadata only)
`.DS_Store` from: `DS_Closing/`, `Hypersale_Closing/`, `Discounted_Closing/`,
`Hypersale_Closing/Amex Statements/`, `Hypersale_Closing/Order_Reports/`,
`Listings_Hunting/`, `Keepa Files/`, `Smartscout_Extractor/` — 84 KB total.
No other DS_Closing file was moved (see §2 — it is still referenced by live code).

---

## 2. PROBABLY-DEAD — kept in place (why uncertain)

| Path | Size | Why not moved |
|---|---|---|
| `DS_Closing/` (entire tree minus .DS_Store) | ~16 MB | **Live code references it**: `Automations/dashboard/insights.py:20` reads `DS_Closing/Hypersale_Closing/verified_skus_cache.csv`; `Automations/scripts/backfill_unified_db.py:44-45` reads both `Order_Reports/` dirs; `Automations/engine/tracking.py:108` points at `DS_Closing/Discounted_Closing/Urgent_Calculations.xlsx` (file currently absent — path is dangling). The Python scripts inside are legacy, but the data files are consumed |
| `Final Automation/unified_tracker.py` (340 KB), `tracker_test_local.py`, `db.py`, `sheet_cache.py`, `gmail_prefetch.py`, `run.sh`, `run_tracker.sh`, `run_v3_jan1_2026.sh`, `run_v3_messaging.sh` | ~450 KB | Interconnected legacy-tracker cluster with runnable entry points (`run_tracker.sh`/`run_v3_*.sh` exec `tracker_test_local.py`, which imports `unified_tracker` → `db`). `tracker_test_local.db` is read by live `backfill_unified_db.py` (source 4). Task marked these "?" — left for manual confirmation |
| `Final Automation/.uc_profile` (1.6 GB), `.uc_profile_hathoro` (1.9 GB), `.fedex_selenium_profile` (374 MB), `.fedex_uc_profile` (217 MB) | **~4.1 GB** | Chrome/undetected-chromedriver profiles used by the kept-in-place FA tracker cluster (last touched May 10–Jun 6). Biggest reclaimable space in the repo if you confirm the FA tracker is retired |
| `Final Automation/hoank-tracker.service` | 4 KB | systemd unit template (Linux, `YOUR_USERNAME_HERE` placeholders — never deployed on this Mac) but referenced by `docs/LINUX_DEPLOYMENT.md` |
| `Final Automation/hoank_tracker.db` + `-shm/-wal`, `tracker.log`, `tracker_run.log`, `logs/` (44 MB), `testing/` (8.8 MB) | ~49 MB | Data/logs/fixtures of the kept cluster; per the "never move data" rule |
| `Automations/engine/live_gmail_enrichment.py` | 24 KB | Not in the live engine list (superseded by `live_gmail_per_order.py`) and no code imports it, but `CLOSING_PIPELINE_GUIDE.md:368` documents running it manually |
| `Auto_Lister.py`, `Complete_Daily_Listing_Automation.py` (root) | 30 KB | Old mtimes (Sep/Nov 2025) but `Final Automation/docs/CODEBASE_VISUAL_MAP.md` explicitly lists both as "Not consolidated (listing tool / GUI tool)" — i.e. standalone tools possibly still run by hand |
| `ZORO Clone.zip` (root) | 102 MB | Redundant with the live `ZORO Clone/` directory, but created Jul 4 (yesterday) — looks like a deliberate recent archive |
| `AUDIT_REPORT.md` (root) | 14 KB | Superseded by `AUDIT_REPORT_V2.md` but kept as documentation history |
| `Automations/data/automations_unified.db.bak_20260628_1519`, `Automations/data/All_Tokens/kamibaloch342_token.json.corrupt.bak` | ~? | `.bak` files, but under `Automations/data/` — the "never move anything under data/" rule wins. Prune manually if desired |
| `Automations/Final_Testing_Logs/` root `*.log` files (Jun 19–22) | ~few MB | Only `debug/` subfolders older than 2026-06-20 were authorized for moving — **`debug/` turned out to be empty (0 B)**, so nothing qualified. `runs/` (204 MB) kept per instructions |
| `Automations/engine/_dl_hathoro/`, `_dl_hoank/` | 92 MB | **Not dead** — `engine/tracking.py:869` actively downloads into `_dl_{account}` (newest files dated Jul 4). Old `.txt` report downloads inside (Jun) are prunable, but the dirs must stay |

## __pycache__ — regenerable, safe to delete directly (NOT moved; they'd regenerate)
`Final Automation/__pycache__`, `TSV Files/__pycache__`, `Automations/{platforms,utils,checkout,dashboard,scripts,gui,engine}/__pycache__`, `ZORO Clone/Backend(Task)/__pycache__` (plus many inside `ZORO Clone`'s bundled venvs). Delete with:
`find . -type d -name __pycache__ -not -path "./.venv/*" -not -path "./_DELETE_REVIEW/*" -exec rm -rf {} +`

---

## 3. KEEP — notable mentions (verified live)

- **`Automations/gen-lang-client-0493912626-fbfdbabc0441.json` and `Final Automation/gen-lang-client-0493912626-fbfdbabc0441.json`** — Google service-account keys. **BOTH ARE LIVE**: loaded by `Automations/utils/config.py:202`, `Automations/engine/coupons.py:70-72`, and `Final Automation/config.py:280`. NOT moved. ⚠️ **Credential flag**: two copies of a private key sitting in a git repo — consider consolidating to one path outside the repo and rotating the key.
- `Automations/engine/closing_pipeline.py` — **kept despite the "replaced by closing.py" note**: still imported by live scripts `backfill_2026.py:39`, `update_order.py:45,84`, `test_pipeline_orders.py:35`.
- `Final Automation/` live core: `config.py`, `utils.py`, `gmail_archive.py` (imported by 9+ `Automations/scripts/fetch_*.py`), `build_gmail_archive.py` (subprocess-invoked by `daily_bulk.py:145`), `full_archive_build.py` (mtime Jul 4), `gmail_archive.db` (5.1 GB — the archive powering the Automations stack), `All_Tokens/`, `tracker_test_local.db` (backfill source), `docs/`, `data/`.
- `TSV Files/`: `quick_download.py`, `combined_reports.db` (1.3 GB), `zoro_datafeed_00/01/02.tsv` (~1.2 GB, rebuilt daily) — kept per instructions. Note: `auto_download.py` mentioned in the brief **does not exist** (already gone). `enrich_listings.py`, `live_enrich.py`, `extract_full_page.py`, `extracted_data.json`, `PL_HOANK_Listings_Cost.xlsx` also kept (not in scope, recent mtimes).
- All of `Automations/` live surface confirmed untouched: `closing.py`, `scripts/*`, `engine/*` (minus `closing_runner.py`), `utils/*`, `platforms/*`, `checkout/*`, `dashboard/*` (minus `.bak`s), `gui/launcher.py`, `data/**`, `config/accounts.json`, `schema_v2.sql`, `bot_config.json`.
- `ZORO Clone/`, `Listings_Hunting/`, `.venv/`, `.git/`, `.claude/`, `.github/`, `.vscode/` — untouched.
- Root strays from the brief (`Learning.py`, `SKU_Correction.py`, `Tracking_ID_Fedex/`, `Zoro things/`, `DS_Closing/Chat_processing.py`, etc.) — **already deleted** before this cleanup (per `git status`); nothing left to move.
- `com.dropship.dashboard.plist` (launchd) runs `Automations/dashboard/server.py` via `.venv` — both intact.
