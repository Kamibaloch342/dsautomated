#!/usr/bin/env python3
"""
order_engine.py — Universal order pipeline for all profiles.
Scrapes Amazon Seller Central orders page directly (no report download).

Supports all profiles: DM_COUPON, DM_RESELLER, HS_COUPON, HS_RESELLER

Usage:
    python order_engine.py              # interactive profile select, infinite loop
    python order_engine.py 1            # DM_COUPON, infinite loop
    python order_engine.py 3            # HS_COUPON, infinite loop
    python order_engine.py DM_RESELLER  # exact profile key, infinite loop
    python order_engine.py --dry-run    # scrape + log but don't place orders
    python order_engine.py --single-cycle   # one cycle then exit
"""

import os
import re
import sys
import csv

# Allow sibling packages (utils, platforms, checkout) to be found when this
# script is executed directly (python engine/orders.py) rather than as a module.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_AUTOMATIONS_DIR = os.path.dirname(_SCRIPT_DIR)
if _AUTOMATIONS_DIR not in sys.path:
    sys.path.insert(0, _AUTOMATIONS_DIR)
import json
import time
import signal
import atexit
import platform
import functools
from collections import Counter
from urllib.parse import quote_plus
from datetime import datetime

# NOTE: pandas is intentionally NOT imported here. It was previously imported at
# module top but never used, and its compiled (numpy) DLLs get blocked by Windows
# 11 Smart App Control — which would stop the whole placer from even starting.
# The only pandas use in the SKU pipeline is a rare CSV *fallback* in
# utils/helpers.download_read_and_delete_csv, where the import is now lazy.
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchWindowException
from webdriver_manager.chrome import ChromeDriverManager

# ── Local imports ─────────────────────────────────────────────────
from utils.config import (
    PROFILES, SKU_SHEET_URL, COUPON_SHEET_URL, DOWNLOAD_DIRECTORY,
    GOOGLE_SHEET_ID, GOOGLE_SHEET_SKU_TAB, GOOGLE_SHEET_COUPON_TAB,
    GOOGLE_SHEET_PRIORITY_ASIN_TAB,
    GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE,
    AMAZON_BASE_ORDER_URL, AMAZON_ADDRESS_BLOCK_SELECTOR,
    AMAZON_PHONE_SELECTOR, AMAZON_EDIT_BUTTON_SELECTOR,
    AMAZON_ID_INPUT_FIELD_SELECTOR, AMAZON_SAVE_BUTTON_SELECTOR,
    STATE_MAP, FULL_STATE_NAME_MAP, BLOCKED_STATES,
    ORDER_LIMIT, MAX_ORDERS_PER_CYCLE, CLEAR_CACHE_INTERVAL,
    RESTRICTION_LONG_COOLDOWN, ZORO_SMALL_ORDER_FEE,
    DECIDING_COUPON_MIN_DEFAULT,
    load_gui_config, apply_gui_overrides, select_profile, get_priority_coupons,
)
from platforms.amazon import (
    scrape_amazon_address,
    validate_and_prepare_address,
    update_amazon_seller_id,
    write_seller_note,
)
from platforms.zoro import (
    ZoroAccountRestricted,
    ensure_on_site,
    ensure_token,
    check_inventory,
    clear_cart_and_add,
    add_second_item,
    reset_restriction,
    get_product_brand,
    check_pdp_promo,
    pdp_promo_ineligible,
)
from checkout.dom import place_order_fast
from utils import decision_log as _DLOG      # structured log of every routing decision
try:
    from checkout.true_api import place_order_fast_true_api
except ImportError:
    place_order_fast_true_api = None
try:   # live-cart coupon tests for the supervisor / priority check (same API the checkout uses)
    from checkout.true_api import (_get_cart_id, _apply_coupon_api, _is_transient_coupon_error,
                                   classify_promo_error, _note_reject)
except ImportError:
    _get_cart_id = _apply_coupon_api = _is_transient_coupon_error = classify_promo_error = _note_reject = None
from platforms.gmail import (
    authenticate_gmail_account,
    get_amazon_earnings_from_gmail,
    check_if_address_already_ordered_gmail,
    DUP_CHECK_FAILED,
)
from utils.helpers import (
    download_read_and_delete_csv,
    ensure_chromedriver,
    filter_kwargs_for_callee,
    harden_driver,
    reapply_stealth,
    safe_get,
    setup_driver,
    print_rich_summary,
)
from utils.pricing import log_price_or_status
from platforms.auth import is_login_page, auto_login

try:
    from utils.unified_db import insert_order, update_order
    _HAS_UNIFIED_DB = True
except Exception as _udb_import_err:
    _HAS_UNIFIED_DB = False
    print("=" * 70)
    print("⚠️  CRITICAL: utils.unified_db import FAILED — orders placed in this")
    print(f"⚠️  session will NOT be recorded in the database! Cause: {_udb_import_err}")
    print("=" * 70)

# Hard, local, authoritative de-duplication lock (prevents the duplicate-order incident:
# a claim is reserved BEFORE money is spent; a claim that already exists hard-blocks re-buy).
try:
    from utils import placement_lock as plock
    _HAS_LOCK = True
except Exception as _lock_import_err:
    _HAS_LOCK = False
    print("=" * 70)
    print("🛑 CRITICAL: utils.placement_lock import FAILED — duplicate protection is OFF.")
    print(f"🛑 Refusing-to-place is safer; cause: {_lock_import_err}")
    print("=" * 70)

# Hard backstop on placements per cycle when the configured cap is 0 (unlimited).
# After the first cycle only currently-unshipped orders are re-scraped, so this only
# ever trips on a genuine runaway — it can never quietly buy dozens of times again.
SAFETY_MAX_ORDERS_PER_CYCLE = 300

# Snapshot of this file's mtime at process start — the cycle loop compares against it
# to detect "code was fixed on disk but this process was never restarted".
try:
    _CODE_MTIME_AT_START = os.path.getmtime(os.path.abspath(__file__))
except OSError:
    _CODE_MTIME_AT_START = float("inf")   # can't stat ourselves -> never warn

print = functools.partial(print, flush=True)

# ══════════════════════════════════════════════════════════════════
#  CONFIGURATION — profile-aware
# ══════════════════════════════════════════════════════════════════
gui_cfg = load_gui_config()
apply_gui_overrides(gui_cfg)

cli_arg = None
for _a in sys.argv[1:]:
    if _a.startswith("--"):
        continue
    if _a.isdigit() or _a.upper() in PROFILES:
        cli_arg = _a
        break

PROFILE_NAME, CONFIG = select_profile(cli_arg)
PORT = CONFIG["port"]
PO_NUMBER = CONFIG["po_number"]
TARGET_LABEL = CONFIG.get("target_label", "")
TARGET_LABEL_2 = CONFIG.get("target_label_2", "") if "COUPON" in PROFILE_NAME else ""
MIN_MARGIN = CONFIG.get("min_profit_margin", -0.2)
MIN_MARGIN_2 = CONFIG.get("min_profit_margin_2", -0.2) if "COUPON" in PROFILE_NAME else MIN_MARGIN
MARGIN_CHECK_EST_ONLY = CONFIG.get("margin_check_est_only", False)
EARNINGS_GMAIL = CONFIG["gmail_earnings_account"]
DUPLICATE_GMAIL_LIST = CONFIG["gmail_duplicates_list"]
PRIORITY_ASINS = CONFIG.get("priority_asins", [])
BLOCKED_ORDER_IDS = CONFIG.get("blocked_order_ids", [])
PRIORITY_COUPONS = get_priority_coupons(gui_cfg)

IS_COUPON_PROFILE = "COUPON" in PROFILE_NAME
IS_RESELLER_PROFILE = "RESELLER" in PROFILE_NAME
# Labels-only mode (launcher checkbox): process ONLY already-labelled orders.
# Priority-ASIN discovery and Deciding-ASIN routing are fully disabled.
LABELS_ONLY = bool(CONFIG.get("labels_only", False))
RUSH_MODE = bool(CONFIG.get("rush_mode", False))
RUSH_BATCH = max(1, int(CONFIG.get("rush_batch", 10) or 10))
# Deciding-ASIN routing: refreshed from the sheet each cycle (reseller profiles
# only). An order for one of these ASINs is routed per-order by Zoro cart total:
# ≥ threshold → labelled for the sibling coupon profile; below → placed here.
DECIDING_ASINS = []
DECIDING_COUPON_MIN = float(
    CONFIG.get("deciding_coupon_min", DECIDING_COUPON_MIN_DEFAULT)
    or DECIDING_COUPON_MIN_DEFAULT)
# RESELLER payment: "CREDIT_CARD" (default) or "NET30" — set per-profile in the GUI.
PAYMENT_METHOD = str(CONFIG.get("payment_method") or "CREDIT_CARD").upper()

# ── Routing rules (bot_config.json "routing") ──────────────────────────────
ROUTING = dict(gui_cfg.get("routing") or {})
# Deciding orders UNDER the cart threshold still go to the coupon profile when the
# reseller's own estimated margin is below this (default 5%).
DECIDING_MIN_MARGIN = float(ROUTING.get("deciding_min_margin_pct", 5) or 0) / 100.0
# Supervisor (coupon profiles): UNLABELLED orders whose ASIN is mapped but not in
# Priority ASIN Control are queued LAST — after every labelled/priority order —
# and decided per order: priority coupon applies → place; else cart ≥ $50 → place
# with a regular coupon; else → labelled for the reseller profile.
# Promo-ineligible ASINs (config / sheet / learned) → reseller, always.
SUPERVISE_UNLABELLED = bool(IS_COUPON_PROFILE and ROUTING.get("supervise_unlabelled", True) and not LABELS_ONLY)
SUPERVISE_MIN_CART = float(ROUTING.get("supervise_min_cart_for_regular", 50) or 50)
# Drain the unlabelled backlog gradually: at most this many supervised orders per
# cycle (the rest stay flagged Needs-Priority until the next cycle).
SUPERVISE_MAX_PER_CYCLE = max(1, int(ROUTING.get("supervise_max_per_cycle", 20) or 20))
PROMO_INELIGIBLE_ASINS = set()     # rebuilt every cycle by _refresh_promo_ineligible()
PROMO_INELIGIBLE_CODES = set()
STORE_SORTED_ASINS = set()         # every ASIN in ANY column of this store — the supervisor leaves those alone
_LAST_PRIORITY_ASINS = []          # last successfully loaded priority column (kept when a sheet read fails)
_CYCLE_STARTED_AT = [time.time()]  # when this cycle loaded its sheet lists (late column check compares to it)
if SUPERVISE_UNLABELLED:
    print(f"   -> Supervisor: ON — unlabelled mapped orders handled last, max {SUPERVISE_MAX_PER_CYCLE}/cycle "
          f"(regular coupon needs cart ≥ ${SUPERVISE_MIN_CART:.0f})")
if IS_RESELLER_PROFILE:
    print(f"   -> Deciding rule: cart ≥ threshold OR reseller margin < {DECIDING_MIN_MARGIN:.0%} → coupon profile")
print(f"   -> Min Margin: {MIN_MARGIN:.0%} ({MIN_MARGIN})")
print(f"   -> Margin Mode: FINAL pre-tax margin decides placement "
      f"(pre-checkout estimate gate removed; tax excluded since it's refunded)")
if IS_RESELLER_PROFILE:
    print(f"   -> Reseller mode: Zoro Select 10% discount will be pre-applied")
    print(f"   -> Payment: {'Net30 terms' if PAYMENT_METHOD == 'NET30' else 'Credit Card'}")

SEARCH_URL = (
    "https://sellercentral.amazon.com/orders-v3/search"
    "?q={label}&qt=orderid"
)

# The "favorite" Manage-Orders saved view (buyer-action / unshipped orders).
# Scraped ONCE per cycle to discover priority-ASIN orders, instead of opening a
# separate ASIN-search page for each priority ASIN (50+ page loads).
FAVORITES_URL = "https://sellercentral.amazon.com/orders-v3/ref=xx_myo_favb_xx?page=1"
# Discovery mode for priority ASINs: True = scrape the favorites page once and match
# ASINs against the Priority_ASIN_Control sheet; False = legacy per-ASIN search.
USE_FAVORITES_DISCOVERY = CONFIG.get("use_favorites_discovery", True)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOMATIONS_DIR = os.path.dirname(SCRIPT_DIR)
LOG_DIR = os.path.join(AUTOMATIONS_DIR, "data", "rajar_logs")
os.makedirs(LOG_DIR, exist_ok=True)

DRY_RUN = "--dry-run" in sys.argv
SINGLE_CYCLE = "--single-cycle" in sys.argv or DRY_RUN

ASIN_SEARCH = None
for _arg in sys.argv:
    if _arg.startswith("--asin="):
        ASIN_SEARCH = _arg.split("=", 1)[1].strip()
        break

# ── Lock file & cleanup ───────────────────────────────────────────
_active_chrome_process = None
_active_driver = None
_lockfile_path = os.path.join(SCRIPT_DIR, f".script_{PROFILE_NAME}.lock")


def _cleanup():
    global _active_driver, _active_chrome_process
    print("\n🧹 [CLEANUP] Shutting down...")
    try:
        if _active_driver:
            _active_driver.quit()
            _active_driver = None
    except Exception:
        pass
    try:
        if _active_chrome_process and _active_chrome_process.poll() is None:
            _active_chrome_process.terminate()
            _active_chrome_process.wait(timeout=5)
            _active_chrome_process = None
    except Exception:
        pass
    try:
        if os.path.exists(_lockfile_path):
            os.remove(_lockfile_path)
    except Exception:
        pass
    print("   -> ✅ Cleanup done.")


def _signal_handler(signum, frame):
    print(f"\n⚠️  Signal {signum} received — cleaning up...")
    _cleanup()
    sys.exit(0)


atexit.register(_cleanup)
signal.signal(signal.SIGINT, _signal_handler)
if platform.system() != "Windows":
    signal.signal(signal.SIGTERM, _signal_handler)


def _pid_exists(pid):
    """Cross-platform check whether a process with *pid* is alive."""
    if platform.system() == "Windows":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # process exists but we can't signal it


def _acquire_lock():
    if os.path.exists(_lockfile_path):
        try:
            with open(_lockfile_path, 'r') as f:
                old_pid = int(f.read().strip())
            if _pid_exists(old_pid):
                print(f"❌ Another instance is already running (PID {old_pid}). Exiting.")
                sys.exit(1)
        except (ValueError, OSError):
            pass
    with open(_lockfile_path, 'w') as f:
        f.write(str(os.getpid()))


# ══════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════
_run_ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
# Per-profile file names: four placers started in the same second used to share ONE
# run_<ts>.json/.csv and overwrite each other's run log (seen 7 Sep 2026 — every
# panel then attributed all events to whichever profile saved last).
_log_file = os.path.join(LOG_DIR, f"run_{_run_ts}_{PROFILE_NAME}.json")
_csv_file = os.path.join(LOG_DIR, f"run_{_run_ts}_{PROFILE_NAME}.csv")

_run_log = {
    "started": _run_ts,
    "profile": PROFILE_NAME,
    "dry_run": DRY_RUN,
    "orders_scraped": 0,
    "orders_processed": 0,
    "success": 0,
    "failed": 0,
    "skipped": 0,
    "details": [],
}

CSV_HEADERS = [
    "Timestamp", "Amazon_ID", "ASIN", "SKU", "Qty",
    "Buyer", "City", "State", "Status", "WB_ID",
    "Earnings", "Zoro_Price", "Profit", "Notes",
]


def _save_log():
    """Persist the run log to JSON."""
    with open(_log_file, "w") as f:
        json.dump(_run_log, f, indent=2)


def _init_csv():
    with open(_csv_file, "w", newline="") as f:
        csv.writer(f).writerow(CSV_HEADERS)


def _log_csv(row_dict):
    with open(_csv_file, "a", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            datetime.now().strftime("%H:%M:%S"),
            row_dict.get("amazon_id", ""),
            row_dict.get("asin", ""),
            row_dict.get("sku", ""),
            row_dict.get("qty", ""),
            row_dict.get("buyer", ""),
            row_dict.get("city", ""),
            row_dict.get("state", ""),
            row_dict.get("status", ""),
            row_dict.get("wb_id", ""),
            row_dict.get("earnings", ""),
            row_dict.get("zoro_price", ""),
            row_dict.get("profit", ""),
            row_dict.get("notes", ""),
        ])


def _log_order(detail):
    _run_log["details"].append(detail)
    _save_log()
    _log_csv(detail)


def _safe_float(val):
    if not val or val in ("N/A", "", "None"):
        return None
    try:
        return float(str(val).replace("$", "").replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def _upsert_unified_db(amazon_id, detail, **extra):
    """Write or update order in unified DB. Best-effort — never crashes the order flow.
    Pass extra fields (amazon_fees, shipping_amt, coupon, purchase_date, etc.) as kwargs.
    """
    if not _HAS_UNIFIED_DB:
        return
    try:
        from utils.unified_db import insert_order, update_order
        # Map log fields to DB schema
        account = "Hathoro" if PROFILE_NAME.startswith("DM_") else "HOANK"
        # Multi-item orders carry qty as a comma list ("1, 1") — int() on that
        # raised ValueError and silently dropped the whole unified-DB write
        # (every multi-item order was missing from the dashboard). Sum the
        # per-item quantities instead; a plain int is unchanged.
        qty_total = sum(int(n) for n in re.findall(r"\d+", str(detail.get("qty", 0) or 0)))
        data = {
            "amazon_order_id": amazon_id,
            "merchant_order_id": detail.get("wb_id", ""),
            "wb_order_id": detail.get("wb_id", ""),
            "account": account,
            "asin": detail.get("asin", ""),
            "sku": detail.get("sku", ""),
            "quantity": qty_total,
            "ship_city": detail.get("city", ""),
            "ship_state": detail.get("state", ""),
            "ship_postal_code": detail.get("zip", ""),
            "earnings": _safe_float(detail.get("earnings", "")),
            "zoro_order_total": _safe_float(detail.get("zoro_price", "")),
            "profit_dollars": _safe_float(detail.get("profit", "")),
            "zoro_sku": detail.get("sku", ""),
            "zoro_qty": qty_total,
            "order_status": detail.get("status", "Open"),
            "source": "automations",
        }
        # Merge extra fields (amazon_fees, shipping_amt, coupon, dates, etc.)
        for key, val in extra.items():
            if val is not None and val != "":
                data[key] = val

        result = insert_order(data)
        if not result:
            # Already exists — try to enrich
            updates = {k: v for k, v in data.items() if k not in ("amazon_order_id", "account", "source")}
            update_order(amazon_id, updates, changed_by="orders_engine")
    except Exception as e:
        # Order flow must never break — but a lost DB write must never be silent.
        # Warn + append to a dead-letter file so the order can be re-synced later.
        print(f"  ⚠️ unified_db write FAILED for {amazon_id}: {e}")
        try:
            dead_letter = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "unified_db_write_failures.jsonl",
            )
            payload = data if "data" in locals() else {"detail": str(detail)}
            with open(dead_letter, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": datetime.now().isoformat(),
                    "amazon_order_id": amazon_id,
                    "error": str(e),
                    "data": {k: str(v) for k, v in payload.items()},
                }) + "\n")
        except Exception as e2:
            print(f"  ⚠️ dead-letter write also failed: {e2}")


def _print_section(title):
    print(f"\n  {'─' * 14} {title} {'─' * 14}")


def _print_kv(label, value):
    print(f"  {label:<14}: {value}")


def _print_full_address(addr):
    name = (addr.get("name") or addr.get("original_name") or "?").strip()
    line1 = (addr.get("street_address") or "").strip()
    line2 = (addr.get("street_address_2") or "").strip()
    city = (addr.get("city") or "?").strip()
    state = (addr.get("state_abbr") or "?").strip()
    zipcode = (addr.get("zip_code") or "?").strip()
    phone = str(addr.get("phone") or "").strip()

    _print_section("SHIP TO")
    _print_kv("Name", name)
    _print_kv("Line 1", line1 or "?")
    if line2:
        _print_kv("Line 2", line2)
    _print_kv("City/State/ZIP", f"{city}, {state} {zipcode}".strip())
    if phone:
        _print_kv("Phone", phone)


# ══════════════════════════════════════════════════════════════════
#  CONNECT TO CHROME
# ══════════════════════════════════════════════════════════════════

def connect_chrome():
    global _active_chrome_process

    def _managed():
        global _active_chrome_process
        driver, chrome_process = setup_driver(PORT, CONFIG["profile_folder_name"])
        if driver is None:
            raise SystemExit(f"❌ Could not start or connect to Chrome on port {PORT}. "
                            "Kill any stale Chrome processes and try again.")
        _active_chrome_process = chrome_process
        print(f"✅ Connected via managed Chrome on port {PORT} — {driver.current_url}")
        return driver

    # Fast path: nothing listening on the port means the attach attempt below
    # would only burn seconds failing with "session not created" before falling
    # back anyway — launch the managed Chrome directly.
    import socket as _socket
    _s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    _s.settimeout(1)
    _port_open = _s.connect_ex(("127.0.0.1", PORT)) == 0
    _s.close()
    if not _port_open:
        print(f"   -> No Chrome on port {PORT} yet — launching managed Chrome directly...")
        return _managed()

    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"localhost:{PORT}")
    try:
        service = ChromeService(ensure_chromedriver())
        driver = webdriver.Chrome(service=service, options=opts)
        harden_driver(driver)
        print(f"✅ Connected to Chrome on port {PORT} — {driver.current_url}")
        # Apply stealth patches even when attaching to pre-launched Chrome
        print("   -> 🕵️  Applying stealth patches to existing session...")
        from utils.helpers import _build_stealth_js, _apply_stealth_now
        driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": "America/New_York"})
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": _build_stealth_js()
        })
        _apply_stealth_now(driver)
        return driver
    except Exception as e:
        print(f"⚠️  Could not attach to Chrome on port {PORT}: {e}")
        print("   -> Starting managed Chrome for this profile...")
        return _managed()


# Every run of every profile was wedging after 10-16 orders (renderer/page
# timeouts on every navigation until a human restarted it): the profile
# Chrome's session degrades after ~a dozen order flows and never comes back.
# Recycling the browser resets that. The user-data-dir keeps Amazon/Zoro
# logins, so a recycle costs ~20s and loses nothing.
BROWSER_RECYCLE_INTERVAL = 10   # proactive recycle every N processed orders


def _is_stall_error(e):
    """The signature of a poisoned browser session (vs an ordinary order
    failure): renderer stopped answering or the driver connection timed out."""
    s = str(e).lower()
    return (isinstance(e, TimeoutException)
            or "timed out receiving message from renderer" in s
            or "read timed out" in s)


def _recycle_browser(reason):
    """Tear down the profile Chrome + driver and bring up a fresh pair."""
    global _active_driver, _active_chrome_process
    print(f"\n♻️  BROWSER RECYCLE ({reason}) — relaunching Chrome for a fresh session...")
    try:
        if _active_driver:
            _active_driver.quit()
    except Exception:
        pass
    _active_driver = None
    try:
        if _active_chrome_process and _active_chrome_process.poll() is None:
            _active_chrome_process.terminate()
            _active_chrome_process.wait(timeout=10)
    except Exception:
        pass
    _active_chrome_process = None
    try:
        # Also frees the port in attach mode (Chrome this process didn't spawn).
        from utils.proc import kill_pids_on_port
        kill_pids_on_port(PORT)
    except Exception:
        pass
    time.sleep(3)
    driver = connect_chrome()
    _active_driver = driver
    return driver


# ── RUSH MODE: deferred Amazon write-backs ─────────────────────────
# In rush mode the per-order Amazon writes (WB/label + seller notes) are
# journaled to disk at placement time and flushed in batches, so placements
# run back-to-back instead of bouncing Amazon↔Zoro every order. The local
# placement lock (plock.confirm) prevents re-buys the moment money is spent,
# independent of the Amazon write-back — so a crash between placement and
# flush loses nothing: pending write-backs replay from the journal on the
# next start (of any mode).
_RUSH_PENDING_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), "data",
                                  f"rush_pending_{PROFILE_NAME}.json")
_rush_pending = {}   # amazon_id -> {"amazon_id", "label", "notes": [...]}


def _rush_journal_save():
    try:
        os.makedirs(os.path.dirname(_RUSH_PENDING_PATH), exist_ok=True)
        tmp = _RUSH_PENDING_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(list(_rush_pending.values()), f, indent=1)
        os.replace(tmp, _RUSH_PENDING_PATH)
    except Exception as e:
        print(f"  ⚠️  rush journal save failed: {e}")


def _rush_journal_load():
    global _rush_pending
    try:
        with open(_RUSH_PENDING_PATH, encoding="utf-8") as f:
            _rush_pending = {e["amazon_id"]: e for e in json.load(f)}
    except Exception:
        _rush_pending = {}
    return _rush_pending


def _rush_defer_wb(amazon_id, label):
    entry = _rush_pending.setdefault(
        amazon_id, {"amazon_id": amazon_id, "label": "", "notes": []})
    entry["label"] = label
    _rush_journal_save()
    print(f"  ⚡ RUSH: {label} for {amazon_id} journaled — written back after the batch")


def _rush_defer_note(amazon_id, note_text):
    entry = _rush_pending.setdefault(
        amazon_id, {"amazon_id": amazon_id, "label": "", "notes": []})
    entry["notes"].append(note_text)
    _rush_journal_save()


def _rush_flush_amazon_writes(driver):
    """Write every journaled WB/label + its notes back to Amazon — BATCHED:
    one tab per order so all the page loads overlap (same technique as the
    phase-A scrape and the closing SC scrape), then each pre-loaded tab gets
    its seller-id + notes written with skip_navigate. A tab that didn't land
    on its order page just falls back to the navigating write for that one
    order. Entries that fail stay journaled and retry on the next flush or
    the next run. Returns the driver (recycled if a stall hit mid-flush)."""
    if not _rush_pending:
        return driver
    entries = [dict(e) for e in _rush_pending.values()]
    print(f"\n⚡ RUSH FLUSH: {len(entries)} deferred order(s) — loading all order pages in parallel tabs...")
    base = driver.current_window_handle
    handle_map = {}
    try:
        for e in entries:
            try:
                driver.switch_to.new_window("tab")
                handle_map[driver.current_window_handle] = e
                driver.execute_script("window.location.href = arguments[0];",
                                      f"{AMAZON_BASE_ORDER_URL}{e['amazon_id']}")
            except Exception as oe:
                print(f"  ⚠️  Could not open flush tab for {e['amazon_id']}: {oe}")
        time.sleep(4)
        for h, e in handle_map.items():
            aid = e["amazon_id"]
            try:
                driver.switch_to.window(h)
                for _ in range(20):   # up to 10s; usually already complete
                    try:
                        if driver.execute_script("return document.readyState") == "complete":
                            break
                    except Exception:
                        pass
                    time.sleep(0.5)
                on_page = (aid in (driver.current_url or "")) and not is_login_page(driver)
                if not e.get("label") and not on_page:
                    continue                      # notes-only + wrong page → retry later
                ok = True
                if e.get("label"):
                    ok = update_amazon_seller_id(driver, aid, e["label"], PROFILE_NAME,
                                                 skip_navigate=on_page)
                if not ok:
                    print(f"  ⚠️  Deferred WB write FAILED for {aid} — kept journaled for retry")
                    continue
                _notes_ok = True
                for note_text in e.get("notes", []):
                    try:
                        write_seller_note(driver, aid, note_text=note_text, skip_navigate=True)
                    except Exception as note_err:
                        _notes_ok = False
                        print(f"   [SellerNote] ⚠️  deferred note failed for {aid}: {note_err} "
                              f"— kept journaled for retry")
                if not _notes_ok:
                    continue   # label rewrite next retry is idempotent; note must land
                _rush_pending.pop(aid, None)
                _rush_journal_save()
                print(f"  ✅ {aid}: {e.get('label') or '(notes only)'} written back")
            except Exception as we:
                print(f"  ⚠️  Deferred write error for {aid}: {we}")
                if _is_stall_error(we):
                    driver = _recycle_browser(f"stalled during rush flush {aid}")
                    return driver     # recycle closed the tabs; the rest retry next flush
    finally:
        try:
            for h in list(handle_map.keys()):
                try:
                    driver.switch_to.window(h)
                    driver.close()
                except Exception:
                    pass
            driver.switch_to.window(base)
        except Exception:
            try:
                driver.switch_to.window(driver.window_handles[0])
            except Exception:
                pass
    return driver


# ── Cross-run coupon intelligence ──────────────────────────────────
# Which (coupon, SKU) pairs are known-bad (category restrictions are
# deterministic) and which coupon each SKU is known to accept. Shared by all
# profiles via one JSON file; failed pairs only ever accumulate.
_COUPON_INTEL_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), "data", "dataset",
                                  "coupon_intel.json")


def _load_coupon_intel():
    cache = {"failed_pairs": {}, "working_pairs": {}, "no_coupon_codes": set(),
             "priority_exhausted": set(), "promo_ineligible": {}}
    try:
        with open(_COUPON_INTEL_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        # Always try every coupon: keep failed_pairs and no_coupon_codes empty
        cache["failed_pairs"] = {}
        cache["working_pairs"] = dict(raw.get("working_pairs", {}))
        # promo_ineligible is the ONE negative memory we keep: {zoro_code: {fails, asins,
        # last, ineligible}} — strikes ≥ 10 min apart, so a bad API window can't poison it.
        cache["promo_ineligible"] = dict(raw.get("promo_ineligible", {}) or {})
        if cache["working_pairs"]:
            print(f"🏷️  Coupon intel loaded: {len(cache['working_pairs'])} known-working (failed_pairs blacklisting disabled)")
        _bad = [c for c, r in cache["promo_ineligible"].items() if (r or {}).get("ineligible")]
        if _bad:
            print(f"🚫 Promo-ineligible memory: {len(_bad)} Zoro code(s) route to the reseller profile")
    except Exception:
        pass
    return cache


def _save_coupon_intel(cache):
    """Merge this process's coupon memory into the shared file under a cross-process
    lock — two coupon placers write it, and a plain merge-and-replace lost strikes."""
    try:
        from utils.jsonstore import locked, read_json, write_json_atomic
        with locked(_COUPON_INTEL_PATH):
            raw = read_json(_COUPON_INTEL_PATH, None, quarantine=True)
            raw = raw if isinstance(raw, dict) else {}
            merged_working = dict(raw.get("working_pairs", {}) or {})
            merged_bad = dict(raw.get("promo_ineligible", {}) or {})
            merged_working.update(cache.get("working_pairs", {}))
            merged_bad.update(cache.get("promo_ineligible", {}) or {})
            # a code that later WORKED was popped from the in-memory dict — honour that
            for code in list(merged_bad):
                if code not in (cache.get("promo_ineligible") or {}) and cache.get("_promo_cleared", set()) \
                        and code in cache["_promo_cleared"]:
                    merged_bad.pop(code, None)
            merged_min = dict(raw.get("coupon_min_order", {}) or {})
            merged_min.update(cache.get("coupon_min_order", {}) or {})
            write_json_atomic(_COUPON_INTEL_PATH, {"failed_pairs": [], "working_pairs": merged_working,
                                                   "promo_ineligible": merged_bad, "coupon_min_order": merged_min})
    except Exception as e:
        print(f"  ⚠️ coupon intel not saved: {e}")


# ══════════════════════════════════════════════════════════════════════════
#  ROUTING RULES — promo-ineligible memory, sibling hand-off, supervisor
# ══════════════════════════════════════════════════════════════════════════
def _append_asin_to_sheet_column(col_name, asin):
    """Best-effort: add an ASIN to a Priority_ASIN_Control column (e.g. the
    Promo_Ineligible list the team reviews). Silently skipped if the column
    doesn't exist — the local memory still routes the order."""
    try:
        svc = _get_sheets_write_service()
        rows = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{GOOGLE_SHEET_PRIORITY_ASIN_TAB}'!A1:AZ500").execute().get("values", [])
        if not rows:
            return False
        headers = [h.strip() for h in rows[0]]
        if col_name not in headers:
            return False
        ci = headers.index(col_name)
        last = 1
        for ri, r in enumerate(rows[1:], start=2):
            if ci < len(r) and (r[ci] or "").strip():
                if r[ci].strip().upper() == asin.upper():
                    return True                  # already listed
                last = ri
        col = ""
        n = ci + 1
        while n:
            n, rem = divmod(n - 1, 26)
            col = chr(65 + rem) + col
        svc.spreadsheets().values().update(
            spreadsheetId=GOOGLE_SHEET_ID, range=f"'{GOOGLE_SHEET_PRIORITY_ASIN_TAB}'!{col}{last + 1}",
            valueInputOption="RAW", body={"values": [[asin.upper()]]}).execute()
        return True
    except Exception as e:
        print(f"  ⚠️ could not add {asin} to sheet column {col_name}: {e}")
        return False


def _learn_promo_outcome(zoro_code, asin, worked, cache=None, definitive=False, source=None):
    """Promo-ineligible LEARNING (coupon profiles only call this).
    worked=True  → wipe the code's strikes (a coupon just applied to it).
    worked=False → one strike (at most one per routing.promo_strike_hours, default 24 h,
                   so a busy day with used-up coupons can't poison a code); at routing.promo_ineligible_min_fails
                   strikes — or at once when definitive=True (every coupon rejected
                   with a non-transient error, e.g. by the priority check) — the
                   code is marked ineligible: its orders are routed to the reseller
                   profile from the next cycle on. Persisted in coupon_intel.json and
                   mirrored to the sheet's Promo_Ineligible column (if it exists)."""
    if not zoro_code or not ROUTING.get("auto_learn_promo_ineligible", True):
        return
    cache = cache if cache is not None else {}
    store = cache.setdefault("promo_ineligible", {})
    rec = dict(store.get(zoro_code) or {"fails": 0, "asins": [], "last": "", "ineligible": False})
    if worked:
        if rec.get("fails") or rec.get("ineligible"):
            print(f"  🧠 coupon worked on {zoro_code} — clearing {rec.get('fails', 0)} promo strike(s)")
            _DLOG.log("learn", profile=PROFILE_NAME, asin=asin, sku=zoro_code, action="cleared",
                      reason=f"a coupon worked; {rec.get('fails', 0)} strike(s) cleared", fails=0)
            store.pop(zoro_code, None)
            cache.setdefault("_promo_cleared", set()).add(zoro_code)
            PROMO_INELIGIBLE_CODES.discard(zoro_code)
            if asin:
                PROMO_INELIGIBLE_ASINS.discard(str(asin).upper())
            _save_coupon_intel(cache)
        return
    last_ts = 0.0
    try:
        if rec.get("last"):
            last_ts = time.mktime(time.strptime(rec["last"][:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        last_ts = 0.0
    gap_h = float(ROUTING.get("promo_strike_hours", 24) or 24)
    if last_ts and time.time() - last_ts < gap_h * 3600 and not definitive:
        return                                   # one strike per day at most: a busy day with
                                                 # used-up coupons must not look like ineligibility
    need = int(ROUTING.get("promo_ineligible_min_fails", 2) or 2)
    rec["fails"] = int(rec.get("fails", 0)) + (need if definitive else 1)
    rec["last"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if source:
        rec["source"] = source                    # "pdp" = Zoro's best-price badge (permanent)
    if asin and str(asin).upper() not in rec["asins"]:
        rec["asins"].append(str(asin).upper())
    if rec["fails"] >= need and not rec.get("ineligible"):
        rec["ineligible"] = True
        PROMO_INELIGIBLE_CODES.add(zoro_code)
        PROMO_INELIGIBLE_ASINS.update(rec["asins"])
        print(f"  🧠 {zoro_code} ({', '.join(rec['asins']) or 'no ASIN'}) is now PROMO-INELIGIBLE "
              f"after {rec['fails']} strike(s) — reseller profile from now on")
        _DLOG.log("learn", profile=PROFILE_NAME, asin=asin, sku=zoro_code, action="ineligible",
                  reason=f"{rec['fails']} strike(s)" + (f" (source: {source})" if source else ""), fails=rec["fails"],
                  source=source, asins=rec["asins"])
        for a in rec["asins"]:
            _append_asin_to_sheet_column("Promo_Ineligible", a)
    elif not rec.get("ineligible"):
        print(f"  🧠 promo strike {rec['fails']}/{need} for {zoro_code}")
        _DLOG.log("learn", profile=PROFILE_NAME, asin=asin, sku=zoro_code, action="strike",
                  reason=f"strike {rec['fails']}/{need}", fails=rec["fails"], source=source)
    store[zoro_code] = rec
    _save_coupon_intel(cache)


def _refresh_promo_ineligible(sku_map, cache):
    """Rebuild the promo-ineligible sets each cycle from all three sources:
    bot_config routing.promo_ineligible_asins + the sheet's Promo_Ineligible
    column + what the coupon profiles learned (coupon_intel.json)."""
    asins = {str(a).strip().upper() for a in (ROUTING.get("promo_ineligible_asins") or []) if str(a).strip()}
    sheet = _load_asin_column_from_sheet("Promo_Ineligible", "promo-ineligible")
    if sheet:
        asins |= {a.strip().upper() for a in sheet}
    codes = set()
    for code, rec in ((cache or {}).get("promo_ineligible") or {}).items():
        if (rec or {}).get("ineligible"):
            codes.add(code)
            asins |= {str(a).upper() for a in rec.get("asins", [])}
    for a in list(asins):                    # ASIN → its Zoro code too (alt / 2nd items)
        zc = ((sku_map or {}).get(a) or {}).get("zoro_code")
        if zc:
            codes.add(zc)
    PROMO_INELIGIBLE_ASINS.clear()
    PROMO_INELIGIBLE_ASINS.update(asins)
    PROMO_INELIGIBLE_CODES.clear()
    PROMO_INELIGIBLE_CODES.update(codes)
    if asins:
        print(f"  🚫 Promo-ineligible: {len(asins)} ASIN(s) / {len(codes)} Zoro code(s) → always reseller")


def _deciding_verdict(raw_cart_cost, real_cost, earnings, promo_bad, all_zoro_select,
                      small_fee, free_threshold):
    """Deciding-ASIN rule (reseller profiles). Pure — no side effects.
      promo-ineligible ASIN        → stay (coupons never apply to it)
      all items Zoro Select        → stay (reseller already gets the Select discount)
      cart ≥ DECIDING_COUPON_MIN   → coupon profile
      cart <  DECIDING_COUPON_MIN  → coupon profile ONLY when the reseller's own
                                     estimated margin (Select-discounted cart +
                                     small-order fee) is below DECIDING_MIN_MARGIN;
                                     otherwise stay.
    Returns {"route": bool, "reason": str, "est_margin": float | None}."""
    est_cost = real_cost + (small_fee if real_cost < free_threshold else 0.0)
    est_margin = ((earnings - est_cost) / est_cost) if (earnings and est_cost > 0) else None
    if promo_bad:
        return {"route": False, "est_margin": est_margin,
                "reason": f"promo-ineligible ASIN — coupons never apply (cart ${raw_cart_cost:.2f})"}
    if all_zoro_select:
        return {"route": False, "est_margin": est_margin,
                "reason": f"Zoro Select — reseller already gets the Select discount, no coupon spent (cart ${raw_cart_cost:.2f})"}
    if raw_cart_cost >= DECIDING_COUPON_MIN:
        return {"route": True, "est_margin": est_margin,
                "reason": f"Deciding ASIN: Zoro cart ${raw_cart_cost:.2f} ≥ ${DECIDING_COUPON_MIN:.2f}"}
    if est_margin is not None and est_margin < DECIDING_MIN_MARGIN:
        return {"route": True, "est_margin": est_margin,
                "reason": (f"Deciding ASIN: cart ${raw_cart_cost:.2f} < ${DECIDING_COUPON_MIN:.2f} "
                           f"but reseller est. margin {est_margin:.1%} < {DECIDING_MIN_MARGIN:.0%}")}
    return {"route": False, "est_margin": est_margin,
            "reason": (f"cart ${raw_cart_cost:.2f} < ${DECIDING_COUPON_MIN:.2f}"
                       + (f", reseller est. margin {est_margin:.1%} ≥ {DECIDING_MIN_MARGIN:.0%}"
                          if est_margin is not None else ", earnings unknown"))}


def _is_promo_ineligible(resolved_items):
    for it in resolved_items or []:
        if str(it.get("asin") or "").strip().upper() in PROMO_INELIGIBLE_ASINS:
            return True
        if (it.get("sku") or "") in PROMO_INELIGIBLE_CODES or (it.get("main_sku") or "") in PROMO_INELIGIBLE_CODES:
            return True
    return False


def _route_to_sibling(driver, amazon_id, kind, reason, f):
    """Hand an order to this store's OTHER profile by writing that profile's label
    into Amazon's seller-order-id (the sibling scrapes its label every cycle).
    kind = "coupon" | "reseller". This never places anything."""
    if kind == "coupon":
        sibling = PROFILE_NAME.replace("_RESELLER", "_COUPON")
        default, status = "MR", "ROUTED_TO_COUPON"
    else:
        sibling = PROFILE_NAME.replace("_COUPON", "_RESELLER")
        default, status = ("Rajar" if sibling.startswith("HS") else "AB ECOM"), "ROUTED_TO_RESELLER"
    label = (PROFILES.get(sibling) or {}).get("target_label") or default
    ok = update_amazon_seller_id(driver, amazon_id, label, PROFILE_NAME)
    print(f"  🔀 ROUTE → {sibling}: {reason} — labelled '{label}' (not placing here)"
          + ("" if ok else "  ⚠️ label write not verified — set it by hand"))
    _DLOG.log("route", profile=PROFILE_NAME, amazon_id=amazon_id, asin=f.get("asin"), sku=f.get("sku"), qty=f.get("qty"),
              action=status, reason=reason, label=label, sibling=sibling, label_written=bool(ok))
    _log_order({
        "amazon_id": amazon_id, "asin": f.get("asin", ""), "sku": f.get("sku", ""), "qty": f.get("qty", ""),
        "buyer": f.get("buyer", ""), "city": f.get("city", ""), "state": f.get("state", ""),
        "status": status,
        "notes": "; ".join(filter(None, [f"{reason} → routed to {sibling} ('{label}')", f.get("order_note", "")])),
    })
    return ok


def _supervise_coupon_decision(driver, resolved_items, raw_cart_cost, priority_with_pct, regular_codes, cache,
                               all_zoro_select=False, reseller_est_margin=None, unit_price=None,
                               pdp_ineligible=None):
    """Supervisor rule for an UNLABELLED order whose cart is already built.

      0. PDP badge "not eligible for any other offers or promotions" → reseller (permanent)
      1. a PRIORITY coupon applies (always tried first — they are 15–20 %, better
         than the reseller's 10 % even on Zoro Select)      → place here with it
      2. else all items Zoro Select (reseller already gets 10 %) → reseller
      3. else cart ≥ SUPERVISE_MIN_CART and a REGULAR coupon applies → place here with it
      4. else cart < threshold BUT the reseller's estimated margin < DECIDING_MIN_MARGIN
         and a REGULAR coupon applies (avoid a loss)          → place here with it
      5. else                                                  → reseller profile

    Coupons are tested on the LIVE cart through the same API the checkout uses, so
    the verdict is real. Zoro's replies are classified (item / min-order / dead /
    token / transient): only item-level rejections count as evidence.
    Returns {"action": place|reseller|retry, "coupon", "tier", "reason",
             "verdict": {status, suggest, note}}  — verdict = what to record for the ASIN."""
    def _v(status, suggest, note=""):
        return {"status": status, "suggest": suggest, "note": note}

    if _get_cart_id is None or _apply_coupon_api is None:
        return {"action": "retry", "coupon": None, "tier": None, "reason": "true_api checkout module unavailable",
                "verdict": None}
    code0 = (resolved_items[0].get("sku") if resolved_items else None) or None
    asin0 = (resolved_items[0].get("asin") if resolved_items else None) or None
    up = float(unit_price or 0)
    if pdp_ineligible:
        return {"action": "reseller", "coupon": None, "tier": None,
                "reason": "Zoro's best price — not eligible for any offers or promotions (PDP badge)",
                "verdict": _v("PROMO_INELIGIBLE", "Reseller", "Zoro's best price badge (PDP)")}
    cart_id = _get_cart_id(driver)
    if not cart_id:
        return {"action": "retry", "coupon": None, "tier": None, "reason": "no Zoro cart id", "verdict": None}
    seen, pri = set(), []
    for c in (priority_with_pct or []):
        code = str((c or {}).get("code") or "").strip()
        if code and code not in seen:
            seen.add(code)
            pri.append(code)
    tried = transient = item_rejects = 0
    min_orders = {}
    attempts = []          # every coupon tried, with Zoro's classified reply — for the decision log

    def _try(code, tier="priority"):
        nonlocal tried, transient, item_rejects
        tried += 1
        t0 = time.time()
        ok, res = _apply_coupon_api(driver, cart_id, code)
        if ok and (res or 0) > 0:
            attempts.append({"code": code, "tier": tier, "ok": True, "discount": round(float(res), 2),
                             "ms": int((time.time() - t0) * 1000)})
            return True
        kind = (_note_reject(code, res, cache, priority=(tier == "priority")) if _note_reject
                else ("transient" if _is_transient_coupon_error(res) else "item"))
        extra = None
        if kind in ("token", "transient"):
            transient += 1
        elif kind == "item":
            item_rejects += 1
        elif kind == "min_order":
            try:
                extra = float(classify_promo_error(res)[1] or 0)
                min_orders[code] = extra
            except Exception:
                pass
        attempts.append({"code": code, "tier": tier, "ok": False, "kind": kind, "min_order": extra,
                         "reply": str(res)[:160], "ms": int((time.time() - t0) * 1000)})
        return False

    def _ret(d):
        d["attempts"] = attempts
        d["min_orders"] = min_orders
        return d

    print(f"  🧑‍⚖️ SUPERVISOR: testing {len(pri)} priority coupon(s) on the live cart (${raw_cart_cost:.2f})"
          + (" — Zoro Select item(s)" if all_zoro_select else ""))
    for code in pri:
        if _try(code, "priority"):
            _learn_promo_outcome(code0, asin0, True, cache=cache)
            return _ret({"action": "place", "coupon": code, "tier": "priority",
                         "reason": f"priority coupon {code} applies (cart ${raw_cart_cost:.2f})",
                         "verdict": _v("COUPON_OK", "Coupon", f"priority {code} works")})
    if tried and transient == tried:
        return _ret({"action": "retry", "coupon": None, "tier": None,
                     "reason": f"Zoro coupon API transient/session errors on all {tried} attempt(s)", "verdict": None})
    if min_orders:
        lo = min(min_orders.values())
        print(f"  🧑‍⚖️ SUPERVISOR: a priority coupon would apply from a ${lo:.0f} cart (this cart ${raw_cart_cost:.2f})")
    if all_zoro_select:
        why = "Zoro Select — no priority coupon applies; reseller already gets the 10 % Select discount"
        return _ret({"action": "reseller", "coupon": None, "tier": None, "reason": why,
                     "verdict": _v("SELECT", "Reseller", "Zoro Select, no priority coupon")})
    reg = [str(c).strip() for c in (regular_codes or []) if str(c).strip() and str(c).strip() not in seen]
    thin = reseller_est_margin is not None and reseller_est_margin < DECIDING_MIN_MARGIN
    needs_coupon = raw_cart_cost >= SUPERVISE_MIN_CART or thin
    col_by_price = "Coupon" if up >= SUPERVISE_MIN_CART else "Deciding"
    why_reg = ""
    reg_item_rejects = 0
    if needs_coupon:
        why_reg = (f"cart ${raw_cart_cost:.2f} ≥ ${SUPERVISE_MIN_CART:.0f}" if raw_cart_cost >= SUPERVISE_MIN_CART
                   else f"cart ${raw_cart_cost:.2f} < ${SUPERVISE_MIN_CART:.0f} but reseller margin "
                        f"{reseller_est_margin:.1%} < {DECIDING_MIN_MARGIN:.0%}")
        if reg:
            print(f"  🧑‍⚖️ SUPERVISOR: no priority coupon; {why_reg} → testing {len(reg)} regular coupon(s)")
        else:
            print(f"  🧑‍⚖️ SUPERVISOR: no priority coupon; {why_reg} — but NO regular coupon is left right now")
        before = item_rejects
        for code in reg:
            seen.add(code)
            if _try(code, "regular"):
                return _ret({"action": "place", "coupon": code, "tier": "regular",
                             "reason": f"no priority coupon; {why_reg} → regular coupon {code}",
                             "verdict": _v("REGULAR_OK", col_by_price, f"regular coupon works ({why_reg})")})
        reg_item_rejects = item_rejects - before
    if tried and transient == tried:
        return _ret({"action": "retry", "coupon": None, "tier": None,
                     "reason": f"Zoro coupon API transient/session errors on all {tried} attempt(s)", "verdict": None})
    if needs_coupon:
        # This order NEEDS a coupon (≥ $50, or a thin margin) and none applied right now.
        # Regular coupons are single-use and scraped daily, so "none left" or "all used
        # up" is normal on a busy day — the order WAITS (left unlabelled, retried next
        # cycle / after the next coupon scrape). It is NOT sent to the reseller at full
        # price and the ASIN is NOT called ineligible. A strike is recorded only when
        # regular coupons WERE available and every one was rejected at item level, and
        # strikes must fall on different days (routing.promo_strike_hours) before an
        # ASIN becomes ineligible — the PDP badge is the primary ineligibility signal.
        if reg and reg_item_rejects == len(reg) and transient == 0 and not min_orders:
            _learn_promo_outcome(code0, asin0, False, cache=cache)
            why = (f"every available coupon ({tried}) was rejected for this item today — "
                   f"waiting for the next coupon scrape (strike recorded)")
        elif not reg:
            why = f"no priority coupon applies and no regular coupon is left today ({why_reg}) — waiting for the next coupon scrape"
        else:
            why = f"no coupon applied ({why_reg}) — waiting for the next coupon scrape"
        return _ret({"action": "wait", "coupon": None, "tier": None, "reason": why,
                     "verdict": _v("NO_PRIORITY", col_by_price, "no priority coupon; sorted by unit price")})
    why = (f"no priority coupon applies; cart ${raw_cart_cost:.2f} < ${SUPERVISE_MIN_CART:.0f}"
           + (f"; reseller margin {reseller_est_margin:.1%} is fine" if reseller_est_margin is not None else ""))
    return _ret({"action": "reseller", "coupon": None, "tier": None, "reason": why,
                 "verdict": _v("NO_PRIORITY", col_by_price, "no priority coupon; sorted by unit price")})


def _record_asin_verdict(asin, verdict, cart_text="", coupon=""):
    """Write the supervisor's verdict for this ASIN into Priority_ASIN_Control (moves
    the ASIN into the suggested column + Check_* provenance) so the next order of the
    same ASIN is pre-sorted. Best effort and OFF-THREAD — never delays placement."""
    if not asin or not verdict or not IS_COUPON_PROFILE or DRY_RUN:
        return
    import threading

    def _w():
        try:
            from engine import priority_check as _pc
            res = _pc.record_verdict(PROFILE_NAME, str(asin).upper(), verdict["status"], coupon or "", cart_text or "",
                                     verdict["suggest"], verdict.get("note", ""), mod=sys.modules[__name__])
            _DLOG.log("sheet", profile=PROFILE_NAME, asin=str(asin).upper(), action=verdict["status"], coupon=coupon or "",
                      cart=None, reason=verdict.get("note", ""), verdict=verdict, moved=bool((res or {}).get("moved")),
                      column=_pc.suggested_column(PROFILE_NAME, verdict["suggest"]), ok=bool((res or {}).get("ok")))
        except Exception as e:
            print(f"  ⚠️ sheet verdict not written for {asin}: {e}")
            _DLOG.log("sheet", profile=PROFILE_NAME, asin=str(asin).upper(), action="error", reason=str(e)[:200],
                      verdict=verdict)
    threading.Thread(target=_w, daemon=False, name="asin-verdict").start()   # finishes even during a stop


_PRI_SET_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), "data", "dataset", f"priority_coupon_set_{PROFILE_NAME}.json")
RECHECK_PER_CYCLE = max(1, int(((gui_cfg.get("automation") or {}).get("priority_check") or {}).get("recheck_per_cycle", 15) or 15))


def _watch_priority_coupon_set(coupon_codes):
    """The weekly coupon change: when a priority coupon appears in or disappears from
    the Coupons tab, every coupon-dependent ASIN of this store is queued for a re-test
    with the current coupons (permanent PDP-ineligible ones excluded). The queue is drained a few
    ASINs per cycle between placements, and each verdict moves the ASIN in the sheet."""
    if not IS_COUPON_PROFILE or LABELS_ONLY or DRY_RUN:
        return
    try:
        cur = sorted({str((c or {}).get("code") or "").strip()
                      for c in (coupon_codes[0] if isinstance(coupon_codes, tuple) else [])} - {""})
        prev = None
        if os.path.exists(_PRI_SET_PATH):
            with open(_PRI_SET_PATH, encoding="utf-8") as f:
                prev = (json.load(f) or {}).get("codes")
        if prev is None:
            os.makedirs(os.path.dirname(_PRI_SET_PATH), exist_ok=True)
            with open(_PRI_SET_PATH, "w", encoding="utf-8") as f:
                json.dump({"codes": cur, "at": time.strftime("%Y-%m-%d %H:%M")}, f)
            return
        new = sorted(set(cur) - set(prev))
        gone = sorted(set(prev) - set(cur))
        if prev and not cur:
            print(f"  ⚠️  Priority coupon set read back EMPTY (was {', '.join(prev)}) — treated as a bad read: "
                  f"no re-check queued, memory kept. Remove codes one at a time in the Coupons tab.")
            return
        if new or gone:
            # A new code can promote ASINs to _Coupon; an expired/removed code means the
            # ASINs that were in _Coupon because of it must be re-sorted (the owner's
            # rule: "when FACILITY expires, all those 100 have to be checked again").
            what = (f"new: {', '.join(new)}" if new else "") + (" | " if new and gone else "") + \
                   (f"expired/removed: {', '.join(gone)}" if gone else "")
            print(f"  🎟️  Priority coupon set changed ({what}) — queueing a re-check of every coupon-dependent ASIN")
            from engine import priority_check as _pc
            n = _pc.enqueue_recheck(PROFILE_NAME, f"priority coupon change — {what}", mod=sys.modules[__name__])
            print(f"     {n} ASIN(s) queued — {RECHECK_PER_CYCLE} re-tested per cycle between placements")
        if cur != prev:
            with open(_PRI_SET_PATH, "w", encoding="utf-8") as f:
                json.dump({"codes": cur, "at": time.strftime("%Y-%m-%d %H:%M")}, f)
    except Exception as e:
        print(f"  ⚠️ priority coupon-set watch: {e}")


_PRIORITY_CHECK_LAST = [time.time()]    # first scheduled check only after every_hours
_CART_FAILS = {}                        # amazon_id -> {"n": consecutive cart-add failures, "last": epoch}


def _maybe_run_priority_check(driver, sku_map, coupon_codes, cache):
    """Coupon profiles: verify new Priority-ASIN-Control ASINs between cycles —
    every automation.priority_check.every_hours, or whenever the dashboard drops
    data/control/priority_check.request. Uses THIS placer's Zoro session, so it
    never fights a running checkout for the cart."""
    if not IS_COUPON_PROFILE or DRY_RUN or ASIN_SEARCH:
        return
    pc = (gui_cfg.get("automation") or {}).get("priority_check") or {}
    try:
        from engine import priority_check as _pc
    except Exception as e:
        print(f"  ⚠️ priority check module unavailable: {e}")
        return
    # 1) drain the re-check queue (new weekly coupon → every coupon-dependent ASIN)
    try:
        if _pc.queue_size(PROFILE_NAME):
            _pc.run_queue(driver, PROFILE_NAME, sku_map=sku_map, coupon_codes=coupon_codes, cache=cache,
                          mod=sys.modules[__name__], max_asins=RECHECK_PER_CYCLE)
    except Exception as e:
        print(f"  ⚠️ priority re-check queue: {e}")
    # 2) scheduled / on-demand check of new ASINs
    req = os.path.join(os.path.dirname(SCRIPT_DIR), "data", "control", f"priority_check.{PROFILE_NAME}.request")
    args = {}
    if os.path.exists(req):
        try:
            with open(req, encoding="utf-8") as f:
                args = (json.load(f) or {}).get("args") or {}
        except Exception:
            args = {}
    every = float(pc.get("every_hours", 6) or 0)
    due = os.path.exists(req) or (pc.get("enabled", True) and every > 0
                                  and time.time() - _PRIORITY_CHECK_LAST[0] >= every * 3600)
    if not due:
        return
    _PRIORITY_CHECK_LAST[0] = time.time()
    try:
        os.remove(req)
    except OSError:
        pass
    try:
        if args.get("reset"):
            n = _pc.enqueue_recheck(PROFILE_NAME, f"reset & rebuild requested by {args.get('author', 'dashboard')}",
                                    mod=sys.modules[__name__], include_ineligible=True)
            print(f"  🔁 RESET: {n} ASIN(s) queued for a full re-check ({RECHECK_PER_CYCLE} per cycle)")
            return
        _pc.run_check(driver, PROFILE_NAME, sku_map=sku_map, coupon_codes=coupon_codes, cache=cache,
                      mod=sys.modules[__name__], only_new=not args.get("all"),
                      max_asins=int(args.get("max") or pc.get("max_per_run", 25) or 25))
    except Exception as e:
        print(f"  ⚠️ priority check failed: {e}")


def _rush_scrape_batch(driver, batch):
    """RUSH phase A: open one tab per order so Amazon loads every detail page
    in PARALLEL, then harvest each tab with the same extraction code as the
    classic scrape. Returns {amazon_id: detail}. Orders missing from the map
    (load failed, login wall, extract error) are scraped serially by the
    caller — correctness never depends on the batch path."""
    details = {}
    base = driver.current_window_handle
    handle_to_order = {}
    try:
        for o in batch:
            url = f"{AMAZON_BASE_ORDER_URL}{o['amazon_id']}"
            try:
                # switch_to.new_window creates a REAL tab via the driver —
                # window.open() from a script gets eaten by Chrome's popup
                # blocker (that made phase A silently return 0/10). Setting
                # location via JS returns immediately, so all tabs keep
                # loading in parallel while we open the next one.
                driver.switch_to.new_window("tab")
                handle_to_order[driver.current_window_handle] = o
                driver.execute_script("window.location.href = arguments[0];", url)
            except Exception as e:
                print(f"  ⚠️  Could not open batch tab for {o['amazon_id']}: {e}")
        time.sleep(4)   # all tabs load concurrently; give the slowest a head start
        login_hit = False
        for h, o in handle_to_order.items():
            aid = o["amazon_id"]
            try:
                driver.switch_to.window(h)
            except Exception:
                continue
            for _ in range(20):   # up to 10s per tab (usually already complete)
                try:
                    if driver.execute_script("return document.readyState") == "complete":
                        break
                except Exception:
                    pass
                time.sleep(0.5)
            if is_login_page(driver):
                login_hit = True
                break
            if aid not in (driver.current_url or ""):
                continue          # navigation didn't take — serial fallback covers it
            try:
                d = _extract_order_details(driver, aid, fallback_qty=o.get("qty", 1))
                if d:
                    details[aid] = d
            except Exception as e:
                print(f"  ⚠️  Batch-tab extract failed for {aid}: {e} — will scrape serially")
        if login_hit:
            print("  🔐 Login wall inside batch tabs — whole batch falls back to serial scrape")
            details.clear()
    finally:
        try:
            for h in list(handle_to_order.keys()):
                try:
                    driver.switch_to.window(h)
                    driver.close()
                except Exception:
                    pass
            driver.switch_to.window(base)
        except Exception:
            try:
                driver.switch_to.window(driver.window_handles[0])
            except Exception:
                pass
    return details


# ══════════════════════════════════════════════════════════════════
#  STEP 1: SCRAPE ORDER IDs FROM SEARCH PAGE
# ══════════════════════════════════════════════════════════════════

def _wait_for_orders_to_load(driver, expected=100, timeout=30, settle=5, poll=1.0,
                             prev_first_id=None):
    """
    Wait for the orders table to finish lazily rendering its rows.

    Seller Central's orders-v3 table streams rows in *chunks*: after a page load
    or a "Next" click the first chunk (~15 rows) appears, then there is a
    multi-second pause before the remaining rows arrive. A naive "row count
    stopped changing" check returns during that pause and parses only ~15 of the
    100 rows on the page.

    We already know how many rows the page should hold (`expected`, derived from
    the total order count), so the fast path just waits until the visible row
    count reaches `expected` — a full page — which a mid-render pause cannot fool.
    A stabilization fallback (count unchanged for `settle` consecutive polls)
    covers the final page, which legitimately holds fewer rows, and the case
    where the total count is unknown. `settle` is deliberately long so a normal
    render pause can't end the wait early.

    After a "Next" click, `prev_first_id` is the previous page's top order id;
    the previous page's rows linger briefly during the swap, so the table is
    ignored until its first order id differs from `prev_first_id`.

    Returns the final visible row count.
    """
    js = (
        "var ids=[],ls=(document.body.innerText||'').split('\\n');"
        "for(var i=0;i<ls.length;i++){var t=ls[i].trim();"
        "if(/^\\d{3}-\\d{7}-\\d{7}$/.test(t))ids.push(t);}"
        "return [ids.length, ids.length?ids[0]:''];"
    )
    prev = -1
    stable = 0
    waited = 0.0
    count = 0
    while waited < timeout:
        count, first = driver.execute_script(js)
        # After a Next click the old page's 100 rows linger for a moment; treat
        # the table as the new page only once its top order id has changed.
        swapped = prev_first_id is None or (bool(first) and first != prev_first_id)

        if swapped:
            # Fast path: the full page (or the known last-page remainder) is in.
            if expected and count >= expected:
                return count
            # Fallback: the count has genuinely stopped growing (final page, or
            # unknown total). Requires a long, stable plateau so a mid-render
            # pause can't end the wait early.
            if count > 0 and count == prev:
                stable += 1
                if stable >= settle:
                    return count
            else:
                stable = 0
            prev = count

        time.sleep(poll)
        waited += poll
    return count


def scrape_order_ids(driver, label=TARGET_LABEL, max_pages=20, asin_search=None,
                     favorites=False):
    """
    Search Amazon SC for orders matching the seller-order-id label or ASIN.
    If asin_search is provided, searches by ASIN instead of label.
    If favorites=True, opens the saved "favorite" Manage-Orders view (buyer-action)
    and scrapes every order on it (ASIN is read inline from the table) — caller then
    matches ASINs against the priority sheet. Saved-view filters are left untouched.
    Otherwise sets filters: Last 30 days, Order date (descending), 100 per page.
    """
    from selenium.webdriver.support.ui import Select
    all_orders = []
    seen_ids = set()

    # Build URL and set dropdown
    if favorites:
        url = FAVORITES_URL
        search_type = "Favorites (buyer action)"
    elif asin_search:
        url = f"https://sellercentral.amazon.com/orders-v3/search?q={quote_plus(asin_search)}&qt=asin"
        search_type = "ASIN"
    else:
        url = SEARCH_URL.format(label=label)
        search_type = "Order ID"
    print(f"\n📋 Searching orders: {url} (by {search_type})")
    driver.get(url)

    if is_login_page(driver):
        print("  🔐 Login required — auto-logging in...")
        auto_login(driver, PROFILE_NAME)
        driver.get(url)
    time.sleep(5)

    # Set filters: dropdown, date range, sort, results per page
    # Favorites = a saved buyer-action view; don't touch the search-type dropdown
    # (there is no q/qt) — just read what the view already shows.
    if not favorites:
        try:
            sel = Select(driver.find_element(By.CSS_SELECTOR, "#myo-search-type"))
            sel.select_by_value("asin" if asin_search else "orderid")
            print(f"  Set search type: {search_type}")
            time.sleep(1.5)
        except Exception as e:
            print(f"  ⚠️  Could not set search type dropdown: {e}")

    try:
        # Set date range to Last 30 days
        sel = Select(driver.find_element(By.CSS_SELECTOR, "#myo-table-date-range"))
        sel.select_by_value("last-30")
        print("  Set date range: Last 30 days")
        time.sleep(1.5)
    except Exception as e:
        print(f"  ⚠️  Could not set date range: {e}")

    try:
        # Set sort to Order date (descending)
        sel = Select(driver.find_element(By.CSS_SELECTOR, "#myo-table-sort-order"))
        sel.select_by_value("order_date_desc")
        print("  Set sort: Order date (descending)")
        time.sleep(1.5)
    except Exception as e:
        print(f"  ⚠️  Could not set sort: {e}")

    try:
        # Set 100 results per page
        sel = Select(driver.find_element(By.CSS_SELECTOR, "#myo-table-results-per-page"))
        sel.select_by_value("100")
        print("  Set 100 results per page")
        time.sleep(2)
    except Exception as e:
        print(f"  ⚠️  Could not set results per page: {e}")

    # Validate filter settings (required) — skipped for the favorites saved view,
    # which keeps its own date filter and has no search-type dropdown.
    if not favorites:
      try:
        current_filters = {
            "searchtype": driver.find_element(By.CSS_SELECTOR, "#myo-search-type").get_attribute("value") or "",
            "date": driver.find_element(By.CSS_SELECTOR, "#myo-table-date-range").get_attribute("value") or "",
            "sort": driver.find_element(By.CSS_SELECTOR, "#myo-table-sort-order").get_attribute("value") or "",
            "perpage": driver.find_element(By.CSS_SELECTOR, "#myo-table-results-per-page").get_attribute("value") or "",
        }
        print(
            f"  Filters now: Date={current_filters['date']}, "
            f"Sort={current_filters['sort']}, PerPage={current_filters['perpage']}, "
            f"SearchType={current_filters['searchtype']}"
        )

        expected_search = "asin" if asin_search else "orderid"
        if (
            current_filters["date"] != "last-30"
            or current_filters["sort"] != "order_date_desc"
            or current_filters["perpage"] != "100"
            or current_filters["searchtype"] != expected_search
        ):
            raise RuntimeError(
                "Filter validation failed. "
                f"Expected Date=last-30, Sort=order_date_desc, PerPage=100, SearchType={expected_search}. "
                f"Got Date={current_filters['date']}, Sort={current_filters['sort']}, "
                f"PerPage={current_filters['perpage']}, SearchType={current_filters['searchtype']}"
            )
      except Exception as e:
        print(f"  ❌ Filter validation failed: {e}")
        raise

    # Get total order count
    snippet = driver.execute_script("return document.body.innerText.substring(0, 800)")
    total_match = re.search(r'(\d+)\s*orders?', snippet)
    total_orders = int(total_match.group(1)) if total_match else 0
    print(f"  Total '{label if not asin_search else asin_search}' orders found: {total_orders}")

    if total_match and total_orders == 0:
        # Amazon itself says zero matches — polling for rows would only burn
        # the full 30s render-timeout (it did, on every empty Manual-C/-R
        # search, every cycle). Nothing to parse.
        print("  ✅ 0 matches — skipping page scan")
        return []

    # ASIN searches: first page only (100 results is enough)
    effective_max = 1 if asin_search else max_pages
    prev_first_id = None  # top order id of the previous page, for swap detection

    for page in range(1, effective_max + 1):
        if page > 1:
            has_next = driver.execute_script("""
                // Try multiple selectors for the Next button across different SC layouts
                var selectors = [
                    '.a-pagination a',
                    '.pagination-controls a',
                    'button.a-last',
                    '[class*="pagination"] a',
                    '[class*="pagination"] button',
                    'a[aria-label*="Next"]',
                    'button[aria-label*="Next"]',
                    'a[aria-label*="next"]',
                ];
                for (var s = 0; s < selectors.length; s++) {
                    var els = document.querySelectorAll(selectors[s]);
                    for (var i = 0; i < els.length; i++) {
                        var txt = els[i].textContent.trim();
                        if (txt.indexOf('Next') === 0 || txt === '›' || txt === '»') {
                            // Check it's not disabled
                            if (els[i].classList.contains('a-disabled') ||
                                els[i].getAttribute('aria-disabled') === 'true' ||
                                els[i].disabled) continue;
                            els[i].click();
                            return true;
                        }
                    }
                }
                return false;
            """)
            if not has_next:
                print(f"  ✅ No more pages — done")
                break
            time.sleep(2)  # let the Next click register before we start polling

        # How many rows this page should hold: 100 until the last page, which
        # holds the remainder (total_orders is Amazon's own match count).
        if total_orders:
            expected = max(1, min(100, total_orders - (page - 1) * 100))
        else:
            expected = 100

        # Wait for the table to finish lazily rendering all rows before parsing.
        # Without this the table often yields only the first ~15 rows of 100.
        loaded = _wait_for_orders_to_load(
            driver, expected=expected, prev_first_id=prev_first_id
        )
        if loaded < expected:
            print(f"  ⚠️  Page {page}: only {loaded}/{expected} rows rendered before timeout")

        # Parse orders from the page text
        page_orders = driver.execute_script("""
            var orders = [];
            var lines = (document.body.innerText || '').split('\\n');
            var current = null;

            function detectStatus(text) {
                if (!text) return '';
                if (/buyer cancellation|cancellation reason|order cancelled/i.test(text)) return 'Cancelled';
                var sm = text.match(/\\b(Cancelled|Canceled|Unshipped|Shipped|Pending)\\b/i);
                if (!sm) return '';
                var s = sm[1].toLowerCase();
                if (s === 'cancelled' || s === 'canceled') return 'Cancelled';
                if (s === 'unshipped') return 'Unshipped';
                if (s === 'shipped') return 'Shipped';
                if (s === 'pending') return 'Pending';
                return '';
            }

            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (!line) continue;

                var orderMatch = line.match(/^(\\d{3}-\\d{7}-\\d{7})$/);
                if (orderMatch) {
                    if (current) orders.push(current);
                    current = {
                        amazon_id: orderMatch[1],
                        seller_order_id: '',
                        asin: '',
                        sku: '',
                        qty: 1,
                        buyer: '',
                        status: ''
                    };
                } else if (current) {
                    var m;
                    if ((m = line.match(/Seller order ID:\\s*(.+)/))) {
                        current.seller_order_id = m[1].trim();
                    } else if ((m = line.match(/Buyer name:\\s*(.+)/))) {
                        current.buyer = m[1].trim();
                    } else if ((m = line.match(/ASIN:\\s*(B[0-9][A-Z0-9]{8})/))) {
                        current.asin = m[1];
                    } else if ((m = line.match(/SKU:\\s*(.+)/))) {
                        current.sku = m[1].trim();
                    } else if ((m = line.match(/Quantity:\\s*(\\d+)/))) {
                        current.qty = parseInt(m[1]);
                    }

                    // Only detect status from lines AFTER the order ID
                    if (!current.status) {
                        var inlineStatus = detectStatus(line);
                        if (inlineStatus) current.status = inlineStatus;
                    }
                }
            }
            if (current) orders.push(current);

            // DOM-based status enrichment: use structured elements
            // that are tied to each order row (more reliable than text flow).
            var orderLinks = document.querySelectorAll('a[href*="/orders-v3/order/"]');
            for (var link of orderLinks) {
                var idMatch = link.textContent.trim().match(/^(\\d{3}-\\d{7}-\\d{7})$/);
                if (!idMatch) continue;
                var rowId = idMatch[1];
                // Walk up to find the order row container
                var row = link.closest('tr, [class*="order-card"], [class*="myo-table"]');
                if (!row) row = link.parentElement && link.parentElement.parentElement;
                if (!row) continue;
                var rowText = row.textContent;
                var domStatus = detectStatus(rowText);
                if (!domStatus) continue;
                for (var o of orders) {
                    if (o.amazon_id === rowId && !o.status) {
                        o.status = domStatus;
                        break;
                    }
                }
            }

            return orders;
        """)

        # Remember this page's top order id so the next page's wait can tell
        # when the table has actually swapped over from these rows.
        if page_orders:
            prev_first_id = page_orders[0]["amazon_id"]

        # Deduplicate; skip cancelled/shipped/pending and already-processed (WB) orders
        new_count = 0
        skipped_reasons = {}
        for o in page_orders:
            oid = o["amazon_id"]
            if oid in seen_ids:
                continue
            seen_ids.add(oid)
            
            status = o.get("status", "")
            seller_id = o.get("seller_order_id", "").strip()
            
            # Skip non-unshipped orders
            if status in ("Cancelled", "Shipped", "Pending"):
                skipped_reasons[status] = skipped_reasons.get(status, 0) + 1
                continue
            
            if asin_search:
                # ASIN search: only pick up orders with NO seller order ID — EXCEPT the
                # Manual-C/-R override tags, which live in that very field and exist
                # precisely to get the order picked up again after it was flagged.
                if seller_id and not _parse_manual_tag(seller_id):
                    skipped_reasons["Has Label"] = skipped_reasons.get("Has Label", 0) + 1
                    continue
            
            all_orders.append(o)
            new_count += 1
        
        skip_summary = ", ".join(f"{v} {k}" for k, v in skipped_reasons.items()) if skipped_reasons else ""
        skip_str = f" (skipped: {skip_summary})" if skip_summary else ""
        print(f"  Page {page}: {len(page_orders)} orders, {new_count} new{skip_str} — total: {len(all_orders)}")
        
        if len(page_orders) == 0:
            break
    
    print(f"\n  📋 '{label}' orders to process: {len(all_orders)}")
    return all_orders


def _sort_orders_for_processing(orders):
    """Group queue by ASIN frequency so repeated ASINs run back-to-back."""
    indexed_orders = list(enumerate(orders))
    if not indexed_orders:
        return orders, Counter()

    priority_rank = {
        str(asin).strip(): index
        for index, asin in enumerate(PRIORITY_ASINS)
        if str(asin).strip()
    }
    asin_counts = Counter()
    first_seen_index = {}

    for index, order in indexed_orders:
        asin = str(order.get("asin") or "").strip()
        if not asin:
            continue
        asin_counts[asin] += 1
        first_seen_index.setdefault(asin, index)

    def sort_key(entry):
        index, order = entry
        asin = str(order.get("asin") or "").strip()
        if order.get("_supervised"):
            # unlabelled / non-priority orders run LAST — after every labelled and
            # priority order (supervisor rule)
            return (3, 0, 0, first_seen_index.get(asin, index), index)
        if asin in priority_rank:
            return (0, priority_rank[asin], 0, first_seen_index.get(asin, index), index)
        if asin:
            return (1, 0, -asin_counts.get(asin, 0), first_seen_index.get(asin, index), index)
        return (2, 0, 0, index, index)

    sorted_orders = [order for _, order in sorted(indexed_orders, key=sort_key)]
    return sorted_orders, asin_counts


def _format_asin_batch_summary(orders, asin_counts, limit=10):
    seen = set()
    parts = []
    for order in orders:
        asin = str(order.get("asin") or "").strip()
        if not asin or asin in seen:
            continue
        seen.add(asin)
        count = asin_counts.get(asin, 0)
        if count > 1:
            parts.append(f"{asin} x{count}")
        else:
            parts.append(asin)
        if len(parts) >= limit:
            break
    if not parts:
        return ""
    remaining = max(len({str(order.get('asin') or '').strip() for order in orders if str(order.get('asin') or '').strip()}) - len(parts), 0)
    return ", ".join(parts) + (" ..." if remaining else "")


# ══════════════════════════════════════════════════════════════════
#  STEP 2: SCRAPE ORDER DETAILS (ASIN, QTY, ADDRESS)
# ══════════════════════════════════════════════════════════════════

def _coerce_positive_int(value):
    try:
        qty = int(float(value))
    except (TypeError, ValueError):
        return None
    return qty if qty > 0 else None


def _normalize_scraped_text(text):
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


_QTY_PATTERNS = (
    re.compile(r"\b(?:qty|quantity)\s*[:x-]?\s*(\d+)\b", re.I),
    re.compile(r"\b(\d+)\s*(?:qty|quantity)\b", re.I),
)


def _extract_qty_from_text(text):
    normalized = _normalize_scraped_text(text)
    if not normalized:
        return None
    for pattern in _QTY_PATTERNS:
        match = pattern.search(normalized)
        if match:
            qty = _coerce_positive_int(match.group(1))
            if qty is not None:
                return qty
    return None


def _finalize_item_qty(detected_qty, fallback_qty=None):
    detected = _coerce_positive_int(detected_qty)
    fallback = _coerce_positive_int(fallback_qty)
    if fallback and fallback > 1 and (detected is None or detected <= 1):
        return fallback
    return detected or 1


def _find_qty_near_anchor(lines, anchor, window=6):
    anchor_norm = _normalize_scraped_text(anchor).lower()
    if not anchor_norm:
        return None

    for index, line in enumerate(lines):
        if anchor_norm not in line.lower():
            continue
        for offset in range(window + 1):
            candidate_indexes = [index] if offset == 0 else [index + offset, index - offset]
            for candidate_index in candidate_indexes:
                if 0 <= candidate_index < len(lines):
                    qty = _extract_qty_from_text(lines[candidate_index])
                    if qty is not None:
                        return qty
    return None


def _infer_item_qty(page_text, asin, title="", context_texts=None, fallback_qty=None):
    context_texts = context_texts or []
    normalized_page = _normalize_scraped_text(page_text)
    lines = [_normalize_scraped_text(line) for line in (page_text or "").splitlines()]
    lines = [line for line in lines if line]

    for text in context_texts:
        qty = _extract_qty_from_text(text)
        if qty is not None:
            return _finalize_item_qty(qty, fallback_qty)

    anchors = [asin, (title or "")[:80]]
    for anchor in anchors:
        qty = _find_qty_near_anchor(lines, anchor)
        if qty is not None:
            return _finalize_item_qty(qty, fallback_qty)

    for anchor in anchors:
        anchor_norm = _normalize_scraped_text(anchor)
        if not anchor_norm or not normalized_page:
            continue
        escaped = re.escape(anchor_norm)
        patterns = (
            re.compile(rf"{escaped}.{{0,400}}?\b(?:qty|quantity)\s*[:x-]?\s*(\d+)\b", re.I),
            re.compile(rf"\b(?:qty|quantity)\s*[:x-]?\s*(\d+)\b.{{0,400}}?{escaped}", re.I),
        )
        for pattern in patterns:
            match = pattern.search(normalized_page)
            if match:
                return _finalize_item_qty(match.group(1), fallback_qty)

    global_quantities = []
    for line in lines:
        qty = _extract_qty_from_text(line)
        if qty is not None:
            global_quantities.append(qty)
    if len(global_quantities) == 1:
        return _finalize_item_qty(global_quantities[0], fallback_qty)

    return _finalize_item_qty(None, fallback_qty)


def _extract_table_qty_for_asin(driver, asin):
    if not asin:
        return None

    try:
        qty = driver.execute_script(r"""
            var asin = arguments[0];

            function cleanText(txt) {
                return (txt || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
            }

            function getVisibleChildren(node) {
                return Array.from((node && node.children) || []).filter(function(child) {
                    if (!child) return false;
                    if (child.classList && child.classList.contains('hidden-table-cell')) return false;
                    var style = window.getComputedStyle ? window.getComputedStyle(child) : null;
                    return !style || (style.display !== 'none' && style.visibility !== 'hidden');
                });
            }

            function parseQtyValue(text) {
                var cleaned = cleanText(text);
                if (!cleaned) return null;
                var match = cleaned.match(/^\d+$/) || cleaned.match(/\b(\d+)\b/);
                if (!match) return null;
                var parsed = parseInt(match[1] || match[0], 10);
                return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
            }

            var rows = document.querySelectorAll('table.a-keyvalue tbody tr');
            for (var i = 0; i < rows.length; i++) {
                var row = rows[i];
                var rowText = cleanText(row.innerText || row.textContent || '');
                if (!rowText || rowText.indexOf(asin) === -1) continue;

                var cells = getVisibleChildren(row);
                if (cells.length >= 6) {
                    var directQty = parseQtyValue(cells[4].innerText || cells[4].textContent || '');
                    if (directQty !== null) return directQty;
                }

                for (var cellIndex = 0; cellIndex < cells.length - 1; cellIndex++) {
                    var candidateQty = parseQtyValue(cells[cellIndex].innerText || cells[cellIndex].textContent || '');
                    if (candidateQty === null) continue;
                    var nextText = cleanText(cells[cellIndex + 1].innerText || cells[cellIndex + 1].textContent || '');
                    if (/^(?:US)?\$/.test(nextText)) return candidateQty;
                }
            }

            return null;
        """, asin)
    except Exception:
        return None

    return _coerce_positive_int(qty)


def scrape_order_details(driver, amazon_order_id, fallback_qty=None):
    """Navigate to order detail page and extract ASIN, qty, and merchant-order-id.
    
    Returns dict:
        {
            "amazon_id": "...",
            "items": [{"asin": "B0...", "qty": 1, "title": "..."}, ...],
            "merchant_order_id": "Rajar" | "WB..." | "",
            "address": {...},  # from scrape_amazon_address
        }
    or None on failure.
    """
    url = f"{AMAZON_BASE_ORDER_URL}{amazon_order_id}"
    safe_get(driver, url)

    if is_login_page(driver):
        auto_login(driver, PROFILE_NAME)
        safe_get(driver, url)

    time.sleep(2)
    return _extract_order_details(driver, amazon_order_id, fallback_qty=fallback_qty)


def _extract_order_details(driver, amazon_order_id, fallback_qty=None):
    """Extract order info from the CURRENT page (already on the order-detail
    URL). Shared by the classic per-order scrape and the RUSH parallel-tab
    batch scrape."""
    # Fast status safety-check from order page text (prevents processing cancelled/shipped).
    status_hint = driver.execute_script(r"""
        var txt = (document.body && document.body.innerText ? document.body.innerText : '').toLowerCase();
        if (txt.indexOf('order cancelled') !== -1 || txt.indexOf('buyer cancellation') !== -1 || txt.indexOf('cancellation reason') !== -1) {
            return 'cancelled';
        }
        if (txt.indexOf('order is pending') !== -1) {
            return 'pending';
        }
        if (/\bshipped\b/.test(txt) && txt.indexOf('edit shipment') !== -1) {
            return 'shipped';
        }
        return '';
    """)
    if status_hint:
        return {"amazon_id": amazon_order_id, "status": status_hint}

    # Check for pending
    try:
        WebDriverWait(driver, 2).until(
            EC.visibility_of_element_located(
                (By.XPATH, "//h4[contains(text(), 'Order is Pending')]")))
        return {"amazon_id": amazon_order_id, "status": "pending"}
    except TimeoutException:
        pass
    
    # Extract order info from the page
    info = driver.execute_script(r"""
        function cleanText(txt) {
            return (txt || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
        }

        function getVisibleChildren(node) {
            return Array.from((node && node.children) || []).filter(function(child) {
                if (!child) return false;
                if (child.classList && child.classList.contains('hidden-table-cell')) return false;
                var style = window.getComputedStyle ? window.getComputedStyle(child) : null;
                return !style || (style.display !== 'none' && style.visibility !== 'hidden');
            });
        }

        function parseQtyValue(text) {
            var cleaned = cleanText(text);
            if (!cleaned) return null;
            var match = cleaned.match(/^\d+$/) || cleaned.match(/\b(\d+)\b/);
            if (!match) return null;
            var qty = parseInt(match[1] || match[0], 10);
            return Number.isFinite(qty) && qty > 0 ? qty : null;
        }

        function getContextTexts(node, asin) {
            var texts = [];
            var seen = new Set();
            var current = node;
            for (var depth = 0; current && depth < 6; depth++, current = current.parentElement) {
                var txt = cleanText(current.innerText || current.textContent || '');
                if (!txt || txt.length > 2500 || seen.has(txt)) continue;
                if (asin && txt.indexOf(asin) === -1) continue;
                seen.add(txt);
                texts.push(txt);
            }
            return texts;
        }

        function getQtyFromRow(row) {
            if (!row) return null;

            var explicitCell = row.querySelector('[data-test-id="item-quantity-cell"]');
            var qty = parseQtyValue(explicitCell && explicitCell.innerText);
            if (qty !== null) return qty;

            var table = row.closest('table');
            if (table) {
                var headerRow = table.querySelector('thead tr');
                var headerCells = getVisibleChildren(headerRow);
                var qtyIndex = -1;

                headerCells.forEach(function(cell, index) {
                    if (qtyIndex !== -1) return;
                    var text = cleanText(cell.innerText || cell.textContent || '').toLowerCase();
                    var testId = (cell.getAttribute('data-test-id') || '').toLowerCase();
                    if (testId === 'item-quantity-heading' || text === 'quantity' || text.indexOf('quantity') !== -1) {
                        qtyIndex = index;
                    }
                });

                if (qtyIndex !== -1) {
                    var rowCells = getVisibleChildren(row);
                    if (qtyIndex < rowCells.length) {
                        qty = parseQtyValue(rowCells[qtyIndex].innerText || rowCells[qtyIndex].textContent || '');
                        if (qty !== null) return qty;
                    }
                }
            }

            var visibleCells = getVisibleChildren(row);
            for (var i = 0; i < visibleCells.length; i++) {
                qty = parseQtyValue(visibleCells[i].innerText || visibleCells[i].textContent || '');
                if (qty === null) continue;
                var nextText = cleanText(visibleCells[i + 1] ? (visibleCells[i + 1].innerText || visibleCells[i + 1].textContent || '') : '');
                if (/^(?:US)?\$/.test(nextText)) {
                    return qty;
                }
            }

            return null;
        }

        var result = {items: [], merchant_order_id: ''};
        var seenAsins = new Set();

        // The order-item card shows the seller SKU right under the ASIN, e.g.
        // "ASIN: B0XXXXXXXXX  SKU: G6442406". Pull the SKU that appears AFTER the
        // ASIN. \bSKU avoids matching FNSKU; allow letters/digits/._-/ in the code.
        function extractSku(txt, asin) {
            if (!txt) return '';
            var after = txt;
            if (asin) {
                var idx = txt.indexOf(asin);
                if (idx >= 0) after = txt.slice(idx + asin.length);
            }
            var m = after.match(/\bSKU\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._\/-]{1,40})/i);
            return m ? m[1].trim() : '';
        }

        // Prefer the order-contents table because quantity is often a plain cell value.
        Array.from(document.querySelectorAll('table tr')).forEach(function(row) {
            var rowText = cleanText(row.innerText || row.textContent || '');
            var match = rowText.match(/\b(B[0-9][A-Z0-9]{8})\b/);
            if (match && !seenAsins.has(match[1])) {
                var rowLink = row.querySelector('a[href*="/dp/"], a[href*="/product/"]');
                var title = cleanText(rowLink ? rowLink.textContent : '').substring(0, 100);
                seenAsins.add(match[1]);
                result.items.push({
                    asin: match[1],
                    qty: getQtyFromRow(row),
                    title: title,
                    seller_sku: extractSku(rowText, match[1]),
                    context_texts: getContextTexts(row, match[1])
                });
            }
        });

        // Fallback: infer ASIN(s) from product links if the order table layout changes.
        // Amazon order detail has links like /dp/B0xxxxx or /product/B0xxxxx
        if (result.items.length === 0) {
            var links = document.querySelectorAll('a[href*="/dp/"], a[href*="/product/"]');
            links.forEach(function(a) {
                var match = a.href.match(/\/(?:dp|product)\/(B[0-9][A-Z0-9]{8})/);
                if (match && !seenAsins.has(match[1])) {
                    seenAsins.add(match[1]);
                    var title = cleanText(a.textContent).substring(0, 100);
                    result.items.push({
                        asin: match[1],
                        qty: null,
                        title: title,
                        context_texts: getContextTexts(a, match[1])
                    });
                }
            });
        }
        
        // Fallback: search page text for ASINs if no links found
        if (result.items.length === 0) {
            var text = document.body.innerText;
            var asinMatches = text.match(/\b(B[0-9][A-Z0-9]{8})\b/g);
            if (asinMatches) {
                var seen = new Set();
                asinMatches.forEach(function(a) {
                    if (!seen.has(a)) {
                        seen.add(a);
                        result.items.push({asin: a, qty: null, title: '', context_texts: []});
                    }
                });
            }
        }
        
        // Get merchant-order-id (the label field)
        // Look for "Your seller order ID" or "Merchant Order ID" section
        var sellerIdEl = document.querySelector(
            '[data-test-id="your-seller-order-id"], '
            + '[data-test-id="merchant-order-id"]');
        if (sellerIdEl) {
            result.merchant_order_id = sellerIdEl.textContent.trim();
        }
        // Fallback: search text
        if (!result.merchant_order_id) {
            var bodyText = document.body.innerText;
            var moMatch = bodyText.match(/(?:Your seller order ID|Merchant order ID)[:\s]+([^\n]+)/i);
            if (moMatch) result.merchant_order_id = moMatch[1].trim();
        }

        // Extract dates from page text
        var pageText = document.body.innerText || '';
        function extractDate(pattern) {
            var m = pageText.match(pattern);
            return m ? m[1].trim() : '';
        }
        result.purchase_date = extractDate(/(?:Order date|Purchase date)[:\s]+([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})/i);
        if (!result.purchase_date) {
            result.purchase_date = extractDate(/(?:Order date|Purchase date)[:\s]+([A-Za-z]+\s+\d{1,2},\s+\d{4})/i);
        }
        result.ship_by_date = extractDate(/Ship by[:\s]+([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})/i);
        if (!result.ship_by_date) {
            result.ship_by_date = extractDate(/Ship by[:\s]+([A-Za-z]+\s+\d{1,2},\s+\d{4})/i);
        }
        result.deliver_by_date = extractDate(/Deliver by[:\s]+([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4})/i);
        if (!result.deliver_by_date) {
            result.deliver_by_date = extractDate(/Deliver by[:\s]+([A-Za-z]+\s+\d{1,2},\s+\d{4})/i);
        }

        return result;
    """)
    
    if not info or not info.get("items"):
        print(f"  ⚠️  Could not extract ASIN(s) from order {amazon_order_id}")
        return None

    try:
        page_text = driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        page_text = ""

    raw_items = info.get("items", [])
    item_fallback_qty = fallback_qty if len(raw_items) == 1 else None
    parsed_items = []
    for item in raw_items:
        table_qty = _extract_table_qty_for_asin(driver, item.get("asin", ""))
        direct_qty = table_qty or _coerce_positive_int(item.get("qty"))
        if direct_qty is not None:
            parsed_qty = _finalize_item_qty(direct_qty, item_fallback_qty)
        else:
            parsed_qty = _infer_item_qty(
                page_text,
                item.get("asin", ""),
                item.get("title", ""),
                item.get("context_texts") or [],
                item_fallback_qty,
            )
        parsed_items.append({
            "asin": item.get("asin", ""),
            "qty": parsed_qty,
            "title": item.get("title", ""),
            "seller_sku": item.get("seller_sku", ""),
        })
    
    # Scrape address using existing proven function (already on the page)
    addr = scrape_amazon_address(driver, amazon_order_id, PROFILE_NAME, skip_navigate=True)
    
    result = {
        "amazon_id": amazon_order_id,
        "items": parsed_items,
        "merchant_order_id": info.get("merchant_order_id", ""),
        "address": addr,
        "purchase_date": info.get("purchase_date", ""),
        "ship_by_date": info.get("ship_by_date", ""),
        "deliver_by_date": info.get("deliver_by_date", ""),
    }
    
    return result


# ══════════════════════════════════════════════════════════════════
#  STEP 3: LOAD SKU MAP
# ══════════════════════════════════════════════════════════════════

def _get_sheets_service():
    """Build Google Sheets API v4 client (service account, cached — read-only)."""
    if not hasattr(_get_sheets_service, "_svc"):
        from google.oauth2 import service_account as _sa
        from googleapiclient.discovery import build as _build
        creds = _sa.Credentials.from_service_account_file(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        _get_sheets_service._svc = _build(
            "sheets", "v4", credentials=creds, cache_discovery=False)
    return _get_sheets_service._svc


def _get_sheets_write_service():
    """Build Google Sheets API v4 client with WRITE access (for coupon deletion)."""
    if not hasattr(_get_sheets_write_service, "_svc"):
        from google.oauth2 import service_account as _sa
        from googleapiclient.discovery import build as _build
        creds = _sa.Credentials.from_service_account_file(
            GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        _get_sheets_write_service._svc = _build(
            "sheets", "v4", credentials=creds, cache_discovery=False)
    return _get_sheets_write_service._svc


# Accepted header spellings for the alternative-SKU column (col G of the sheet)
_ALT_SKU_HEADERS = {"alternative_sku", "alt_sku", "alternative_zoro_code", "alt_zoro_code"}


def _extract_alt_sku(row_dict, zoro_code, asin):
    """Pull the alternative_SKU cell (any common header spelling) and validate it.
    The alternative is the fallback Zoro code used when the main code is OOS.
    Returns a G-code or '' — an invalid or self-referencing alternative is ignored
    (with a warning) so it never breaks the row's main mapping."""
    raw = ""
    for key, val in row_dict.items():
        if str(key).strip().lower().replace(" ", "_") in _ALT_SKU_HEADERS:
            raw = str(val or "").strip()
            break
    if not raw or raw.lower() in ("nan", "none"):
        return ""
    if not re.match(r'^G\d+$', raw):
        print(f"  ⚠️  Invalid alternative_SKU '{raw}' for ASIN {asin} — ignoring alternative")
        return ""
    if raw == zoro_code:
        return ""
    return raw


def load_sku_map():
    """Load ASIN→Zoro mapping via Google Sheets API (fast, 1-2s).
    Falls back to CSV download if API fails."""
    print("\n📦 Loading SKU map from Google Sheets...")
    try:
        svc = _get_sheets_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{GOOGLE_SHEET_SKU_TAB}'!A:H",
        ).execute()
        rows = result.get("values", [])
        if len(rows) < 2:
            print("  ⚠️  Sheet returned no data rows")
            return {}
        headers = [h.strip() for h in rows[0]]
        sku_map = {}
        for row in rows[1:]:
            row += [""] * (len(headers) - len(row))
            d = dict(zip(headers, row))
            asin = d.get("ASIN", "").strip()
            if not asin:
                continue
            try:
                qty_m = float(d.get("Qty to Place", "").strip() or 1)
            except ValueError:
                qty_m = 1.0
            try:
                z_m = int(d.get("zoro_multiple", "").strip() or 1)
            except ValueError:
                z_m = 1
            zoro_code = d.get("Zoro Code", "").strip()
            # Validate: must start with G followed by digits
            if not re.match(r'^G\d+$', zoro_code):
                print(f"  ⚠️  Invalid Zoro Code '{zoro_code}' for ASIN {asin} — skipping")
                continue
            sku_map[asin] = {
                "zoro_code": zoro_code,
                "qty_multiplier": qty_m,
                "zoro_multiple": z_m,
                "2nd_part": d.get("2nd_part", "").strip(),
                "brand": d.get("Brand", "").strip(),
                "alt_sku": _extract_alt_sku(d, zoro_code, asin),
            }
        print(f"  ✅ Loaded {len(sku_map)} ASIN→SKU mappings")
        return sku_map
    except Exception as e:
        print(f"  ⚠️  Sheets API failed ({e}) — falling back to CSV...")

    # Fallback: slow CSV download
    df = download_read_and_delete_csv(SKU_SHEET_URL, "verified_skus.csv", DOWNLOAD_DIRECTORY)
    if df is None:
        print("  ❌ Failed to download SKU sheet")
        return {}
    df.columns = df.columns.str.strip()
    rename_map = {
        'Zoro Code': 'zoro_code', 'Zoro_Code': 'zoro_code',
        'Qty to Place': 'qty_multiplier', 'Qty To Place': 'qty_multiplier',
        'Zoro_Multiple': 'zoro_multiple', 'Zoro Multiple': 'zoro_multiple',
        '2nd_part': '2nd_part', '2nd_Part': '2nd_part', '2nd Part': '2nd_part',
    }
    df.rename(columns=rename_map, inplace=True)
    df = df.loc[:, ~df.columns.duplicated()]
    for col, default in [('zoro_multiple', 1), ('qty_multiplier', 1.0), ('2nd_part', '')]:
        if col not in df.columns:
            df[col] = default
    df['zoro_multiple'] = df['zoro_multiple'].fillna(1).astype(int)
    df['qty_multiplier'] = df['qty_multiplier'].fillna(1).astype(float)
    sku_map = {}
    for _, row in df.iterrows():
        asin = str(row.get('ASIN', '')).strip()
        if asin:
            zoro_code = str(row.get('zoro_code', '')).strip()
            if not re.match(r'^G\d+$', zoro_code):
                print(f"  ⚠️  Invalid Zoro Code '{zoro_code}' for ASIN {asin} — skipping")
                continue
            sku_map[asin] = {
                'zoro_code': zoro_code,
                'qty_multiplier': float(row.get('qty_multiplier', 1)),
                'zoro_multiple': int(row.get('zoro_multiple', 1)),
                '2nd_part': str(row.get('2nd_part', '')).strip(),
                'brand': str(row.get('Brand', row.get('brand', ''))).strip(),
                'alt_sku': _extract_alt_sku(dict(row), zoro_code, asin),
            }
    print(f"  ✅ Loaded {len(sku_map)} ASIN→SKU mappings (CSV fallback)")
    return sku_map


# ══════════════════════════════════════════════════════════════════
#  CLOUD PRIORITY ASINs (from Google Sheets)
# ══════════════════════════════════════════════════════════════════

# Amazon's "Your Seller Order ID" field silently refuses values longer than
# ~40 chars: the save clicks fine but the value never persists. A 6-way split
# (6 WBs space-joined = 77 chars) therefore failed write-back forever and the
# order kept re-scraping every cycle until the lock's duplicate-block caught it.
_AMZ_SELLER_ID_MAX = 40


def _fit_seller_label(wb_string, suffix=""):
    """Compose a seller-order-id label that fits Amazon's field limit.

    Keeps as many full WB ids as fit and compresses the rest to '+N'
    (e.g. 'WB8575794243 WB6988394848 +4'). The complete list is preserved in
    the local lock DB, the Google Sheet, and the seller note — the Amazon
    field only needs to mark the order as placed and stop the re-scrape."""
    wbs = [w for w in (wb_string or "").split() if w]
    budget = _AMZ_SELLER_ID_MAX - len(suffix)
    if not wbs:
        return (wb_string or "")[:max(0, budget)] + suffix
    for keep in range(len(wbs), 0, -1):
        extra = len(wbs) - keep
        cand = " ".join(wbs[:keep]) + (f" +{extra}" if extra else "")
        if len(cand) <= budget:
            return cand + suffix
    return wbs[0][:max(0, budget)] + suffix


# Map profile names to Priority_ASIN_Control sheet column headers
_PROFILE_TO_SHEET_COLUMN = {
    "DM_RESELLER": "DM_Reseller",
    "DM_COUPON":   "DM_Coupon",
    "HS_RESELLER": "HS_Reseller",
    "HS_COUPON":   "HS_Coupon",
}

# Deciding columns: ASINs whose profile can't be fixed statically — the reseller
# profile of each store routes their orders per-order by Zoro cart total.
_PROFILE_TO_DECIDING_COLUMN = {
    "DM_RESELLER": "DM_Deciding",
    "HS_RESELLER": "HS_Deciding",
}


def _load_asin_column_from_sheet(col_name, what):
    """Read one column of ASINs from the 'Priority_ASIN_Control' tab by header name."""
    try:
        svc = _get_sheets_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{GOOGLE_SHEET_PRIORITY_ASIN_TAB}'!A1:AZ500",
        ).execute()
        rows = result.get("values", [])
        if len(rows) < 2:
            return []
        headers = [h.strip() for h in rows[0]]
        if col_name not in headers:
            # Deciding columns are optional — only warn for the core profile columns.
            if what == "priority":
                print(f"  ⚠️  Column '{col_name}' not found in Priority_ASIN_Control (found: {headers})")
            return []
        col_idx = headers.index(col_name)
        asins = []
        for row in rows[1:]:
            if col_idx < len(row):
                val = row[col_idx].strip()
                if val and val.startswith("B") and len(val) == 10:
                    asins.append(val)
        if asins:
            print(f"  ⭐ Loaded {len(asins)} {what} ASINs from sheet (col: {col_name})")
        return asins
    except Exception as e:
        # Return None (not []) so callers can tell "API failed" from "column empty".
        # Deciding routing keeps its LAST-KNOWN list on failure — returning [] there
        # let one rate-limited call wipe the list and place a deciding order un-routed.
        print(f"  ⚠️  Could not load {what} ASINs from sheet: {e}")
        return None


def load_priority_asins_from_sheet(profile_name):
    """Load priority ASINs from Google Sheets 'Priority_ASIN_Control' tab.
    Columns: DM_Reseller, DM_Coupon, HS_Reseller, HS_Coupon.
    Returns list of ASIN strings for the given profile."""
    col_name = _PROFILE_TO_SHEET_COLUMN.get(profile_name)
    if not col_name:
        print(f"  ⚠️  No sheet column mapping for profile {profile_name}")
        return []
    return _load_asin_column_from_sheet(col_name, "priority")


def load_store_sorted_asins(profile_name):
    """Every ASIN that is ALREADY sorted somewhere for this store — all <DM|HS>_*
    columns of Priority_ASIN_Control — so the supervisor never touches an ASIN a
    human (or the bot) has placed in Reseller / Deciding / Coupon. Returns None on
    API failure (caller keeps the last-known set)."""
    pref = "DM" if profile_name.upper().startswith("DM") else "HS"
    try:
        svc = _get_sheets_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{GOOGLE_SHEET_PRIORITY_ASIN_TAB}'!A1:AZ500",
        ).execute()
        rows = result.get("values", [])
        if len(rows) < 2:
            return set()
        headers = [h.strip() for h in rows[0]]
        out = set()
        for ci, h in enumerate(headers):
            if not h.upper().startswith(pref + "_"):
                continue
            for row in rows[1:]:
                if ci < len(row):
                    val = (row[ci] or "").strip().upper()
                    if val.startswith("B") and len(val) == 10:
                        out.add(val)
        return out
    except Exception as e:
        print(f"  ⚠️  Could not load the store's sorted ASINs from sheet: {e}")
        return None


def load_deciding_asins_from_sheet(profile_name):
    """Load Deciding ASINs (per-order coupon/reseller routing) for a reseller
    profile from the 'DM_Deciding' / 'HS_Deciding' sheet columns."""
    col_name = _PROFILE_TO_DECIDING_COLUMN.get(profile_name)
    if not col_name:
        return []
    return _load_asin_column_from_sheet(col_name, "deciding")


def _delete_coupon_from_sheet(code):
    """Delete a used coupon row from the Google Sheet 'Coupons' tab.
    Only call this after a successful order (WB ID confirmed)."""
    try:
        svc = _get_sheets_write_service()
        # Find the row containing this coupon code
        result = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{GOOGLE_SHEET_COUPON_TAB}'!A:A",
        ).execute()
        rows = result.get("values", [])
        row_index = None
        code_upper = code.strip().upper()
        for i, r in enumerate(rows):
            if not r:
                continue
            cell = r[0].strip().upper()
            # Match exact code, or code followed by space/hyphen/percentage
            if cell == code_upper or cell.startswith(code_upper + " ") or cell.startswith(code_upper + "-"):
                row_index = i
                break
        if row_index is None:
            return  # Not found — maybe already deleted
        # Get the sheet's gid (sheetId) for the Coupons tab
        sheet_meta = svc.spreadsheets().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            fields="sheets.properties",
        ).execute()
        sheet_id = None
        for s in sheet_meta.get("sheets", []):
            if s["properties"]["title"] == GOOGLE_SHEET_COUPON_TAB:
                sheet_id = s["properties"]["sheetId"]
                break
        if sheet_id is None:
            print(f"  ⚠️  Could not find sheet ID for '{GOOGLE_SHEET_COUPON_TAB}'")
            return
        # Delete the row
        svc.spreadsheets().batchUpdate(
            spreadsheetId=GOOGLE_SHEET_ID,
            body={"requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_index,
                        "endIndex": row_index + 1,
                    }
                }
            }]},
        ).execute()
        print(f"  🗑️  Deleted coupon '{code}' from sheet (row {row_index + 1})")
    except Exception as e:
        print(f"  ⚠️  Could not delete coupon from sheet: {e}")


def _normalize_merchant_order_id(value):
    merchant_id = _normalize_scraped_text(value)
    merchant_id = re.sub(r"^#\s*", "", merchant_id).strip()
    merchant_id = re.sub(r"\bEdit\b$", "", merchant_id, flags=re.I).strip()
    return merchant_id


def _normalize_second_part_sku(value):
    second_sku = str(value or "").strip()
    return second_sku if second_sku.upper().startswith("G") else ""


# ══════════════════════════════════════════════════════════════════
#  MANUAL OVERRIDE  ("Manual-C" / "Manual-R" in Amazon's Seller order ID)
# ══════════════════════════════════════════════════════════════════
# WHY: real customers do legitimately re-order the same item to the same address.
# The Gmail address-history duplicate search cannot tell that apart from a genuine
# duplicate, so it flags them and we write "<WB> - D". That behaviour is correct and
# stays. This tag is the human override for the false positives: after a person has
# eyeballed the order, they write "Manual-C" or "Manual-R" into Amazon's Seller order
# ID field, and the placer will buy it ONCE.
#
# IT BYPASSES EXACTLY TWO GATES:
#   1. the Gmail address-duplicate search   (engine/orders.py, "Duplicate check")
#   2. the final margin / loss gate         (checkout_min_margin -> None)
# and, as a consequence of (1), the post-failure duplicate re-check before keep_failed().
# Nothing else. In particular it does NOT bypass the placement lock
# (utils/placement_lock.py): that lock is keyed on this exact amazon_order_id, so even
# with the tag the order can only ever be placed once — a second attempt is refused by
# placer_claims like any other. The "only one time" guarantee comes from the lock, not
# from the tag.
#
# RESIDUAL RISK, stated plainly: the Gmail search is the only check that can see a
# duplicate placed from the OTHER Zoro account, or one placed before placer_claims
# existed. Waiving it means the human who writes the tag is taking on that check.
#
# -C routes to the COUPON profiles, -R to the RESELLER profiles. Account routing
# (Hathoro/DM vs HOANK/HS) is implicit: a profile only ever scrapes its own account's
# orders, so an HS order is never even seen by a DM profile.
_MANUAL_KIND_SUFFIX = {"C": "COUPON", "R": "RESELLER"}


def _parse_manual_tag(merchant_id):
    """Return 'C' or 'R' if *merchant_id* is a manual-override tag, else None.

    Deliberately tolerant — a tired human types this into Amazon by hand, so
    "manual-c", "Manual C", "MANUAL_R ", "manual  -  R" all count. Anything else
    (including "Manual" with no suffix) is NOT a tag and is ignored, so a partial
    or mistyped label can never silently place an order.
    """
    if not merchant_id:
        return None
    m = re.match(r"^\s*manual\s*[-_ ]?\s*([cr])\s*$", str(merchant_id), re.I)
    return m.group(1).upper() if m else None


def _manual_tag_is_for_profile(kind, profile_name):
    """True if a '-C'/'-R' tag belongs to *profile_name* (…COUPON / …RESELLER)."""
    want = _MANUAL_KIND_SUFFIX.get((kind or "").upper())
    return bool(want) and (profile_name or "").upper().endswith(want)


def _build_resolved_order_items(items, sku_map):
    resolved_items = []
    missing_items = []

    for item in items:
        asin = str(item.get("asin") or "").strip()
        title = str(item.get("title") or "").strip()
        order_qty = _coerce_positive_int(item.get("qty")) or 1
        sku_info = sku_map.get(asin)
        zoro_code = str((sku_info or {}).get("zoro_code") or "").strip()

        if not zoro_code:
            missing_items.append({
                "asin": asin,
                "title": title,
                "qty": order_qty,
            })
            continue

        qty_multiplier = float((sku_info or {}).get("qty_multiplier", 1) or 1)
        zoro_multiple = _coerce_positive_int((sku_info or {}).get("zoro_multiple")) or 1
        final_qty = int(order_qty * qty_multiplier * zoro_multiple)
        if final_qty < 1:
            final_qty = 1

        resolved_items.append({
            "asin": asin,
            "title": title,
            "order_qty": order_qty,
            "sku": zoro_code,
            "seller_sku": str(item.get("seller_sku") or "").strip(),  # real Amazon SKU (under the ASIN on the order page)
            "qty_multiplier": qty_multiplier,
            "zoro_multiple": zoro_multiple,
            "second_sku": _normalize_second_part_sku((sku_info or {}).get("2nd_part", "")),
            "alt_sku": str((sku_info or {}).get("alt_sku") or "").strip(),
            "alt_used": False,
            "main_sku": "",
            "final_qty": final_qty,
        })

    return resolved_items, missing_items


# ══════════════════════════════════════════════════════════════════
#  STEP 4: PROCESS SINGLE ORDER
# ══════════════════════════════════════════════════════════════════

def _estimate_best_coupon_pct(priority_coupons_with_pct, zoro_code, asin_coupon_cache):
    """Return the best coupon percentage to use for profit estimation.

    If we know a working coupon for this zoro_code, use its exact pct.
    Otherwise, use the best pct among coupons that haven't failed on this
    zoro_code yet. Returns None if no reliable estimate exists.
    Skips fixed-amount coupons (they are handled by split logic instead).
    """
    if not zoro_code or not asin_coupon_cache:
        return None
    cache = asin_coupon_cache
    working_pairs = cache.get("working_pairs", {})
    failed_pairs = cache.get("failed_pairs", {})
    priority_exhausted = cache.get("priority_exhausted", set())

    # All priority coupons exhausted for this zoro_code — no estimate
    if zoro_code in priority_exhausted:
        return None

    # Known working coupon for this zoro_code — use its exact percentage
    if zoro_code in working_pairs:
        known_code = working_pairs[zoro_code]
        for c in priority_coupons_with_pct:
            if c["code"] == known_code and "pct" in c:
                return c["pct"]

    # Best percentage among coupons not yet failed on this zoro_code
    untried_pcts = []
    for c in priority_coupons_with_pct:
        if "pct" not in c:
            continue  # skip fixed-amount coupons
        if (c["code"], zoro_code) not in failed_pairs:
            untried_pcts.append(c["pct"])
    if untried_pcts:
        return max(untried_pcts)

    return None


def _evaluate_order_strategy(resolved_items, priority_coupons, unit_price):
    """Estimate profit with the best percentage coupon.  
    
    Splitting is NO LONGER pre-computed here.  Splits are decided at 
    checkout time ONLY when a ratio/fixed-amount coupon actually works.
    
    Returns dict:
      {
          "mode": "single",
          "orders": [{"qty": final_qty}],
          "estimated_cost": float,
          "estimated_shipping": float,
          "discount_description": str,
          "preferred_coupon": str or None,
      }
    """
    strategy = {
        "mode": "single",
        "orders": [{"qty": 1}],
        "estimated_cost": 0,
        "estimated_shipping": 0,
        "discount_description": "No coupon",
        "preferred_coupon": None,
    }

    if not resolved_items or len(resolved_items) > 1:
        return strategy

    item = resolved_items[0]
    final_qty = item.get("final_qty", 1)
    if final_qty < 1:
        return strategy

    if unit_price <= 0:
        return strategy

    total = unit_price * final_qty

    # ── Find best percentage coupon for profit estimation ───────
    best_pct = None
    for c in (priority_coupons or []):
        if "pct" in c:
            if best_pct is None or c["pct"] > best_pct["pct"]:
                best_pct = c

    ship = ZORO_SMALL_ORDER_FEE if total < 50.0 else 0.0
    strategy["orders"] = [{"qty": final_qty}]
    strategy["estimated_shipping"] = ship

    if best_pct:
        single_pct_cost = total * (1 - best_pct["pct"] / 100.0)
        single_ship = ZORO_SMALL_ORDER_FEE if single_pct_cost < 50.0 else 0.0
        single_pct_cost += single_ship
        strategy["estimated_cost"] = single_pct_cost
        strategy["discount_description"] = f"Single order + {best_pct['code']} ({best_pct['pct']:.0f}%)"
        strategy["preferred_coupon"] = best_pct["code"]
        print(f"   [STRATEGY] {strategy['discount_description']}")
        print(f"   [STRATEGY] Est. cost: ${single_pct_cost:.2f}")
    else:
        strategy["estimated_cost"] = total + ship
        print(f"   [DEBUG] No percentage coupon — full price ${strategy['estimated_cost']:.2f}")

    return strategy


def process_order(driver, amazon_id, order_detail, sku_map,
                  earnings_service, duplicate_services,
                  coupon_codes=None, asin_coupon_cache=None,
                  defer_amazon_writes=False, dup_prefetch=None):
    """Process one order through the full pipeline.
    
    Returns status string: "success", "failed", "skipped", "pending", "duplicate", "low_profit", "api_fail"
    """
    items = order_detail.get("items", [])
    addr_raw = order_detail.get("address")
    merchant_id = _normalize_merchant_order_id(order_detail.get("merchant_order_id", ""))

    # ── Repeated cart failures: don't burn a minute every cycle on the same order ──
    # (one SKU failed 43 cart adds in a row on 7 Sep 2026). After 3 failures the order
    # is parked for 2 hours and shows up as CART_FAIL_PARKED for a human to look at.
    _cf = _CART_FAILS.get(amazon_id)
    if _cf and _cf["n"] >= 3 and time.time() - _cf["last"] < 7200:
        if not _cf.get("announced"):
            _cf["announced"] = True
            print(f"  ⏸️  {amazon_id}: cart add failed {_cf['n']}× — parked for 2 h (check the item on Zoro)")
            _log_order({"amazon_id": amazon_id, "status": "CART_FAIL_PARKED",
                        "notes": f"cart add failed {_cf['n']} times in a row — parked 2 h; check the Zoro item / quantity"})
        return "skipped"

    # ── HARD LOCAL DEDUP LOCK (authoritative, checked FIRST) ──────
    # Our own record of what we've already bought — deterministic and immune to the
    # Amazon-UI re-scrape flakiness / Gmail lag that let the same order be re-bought 41x.
    if _HAS_LOCK and not DRY_RUN and plock.already_seen(amazon_id):
        _ci = plock.claim_info(amazon_id) or {}
        _st = _ci.get("status", "")
        if _st == "FAILED_KEPT":
            print(f"  🔒 HELD FOR REVIEW — {amazon_id} is in the Locked Orders tab — skipping")
        elif _st == "PLACING":
            print(f"  ⏳ {amazon_id} placement in progress / held — skipping")
        else:
            # CONFIRMED / history → already bought (we have the WB). Its WB never made it onto
            # Amazon's seller-id (else the list scrape would have skipped it), so write "<WB> - D"
            # now: marks it as a caught duplicate AND stops it being re-scraped every cycle.
            # This is a LABEL write only — it never re-buys.
            _wb = (_ci.get("wb") or "").strip()
            if _wb.startswith("WB") or _wb.startswith("WW"):
                _dlabel = _fit_seller_label(_wb, " - D")
                _ok = update_amazon_seller_id(driver, amazon_id, _dlabel, PROFILE_NAME)
                print(f"  ⏭️  {amazon_id} already placed ({_wb}) — marked Amazon '{_dlabel}'"
                      + ("" if _ok else "  ⚠️ write not verified — set it by hand"))
                _log_order({"amazon_id": amazon_id, "status": "DUPLICATE", "wb_id": _wb,
                            "notes": "already placed — marked '<WB> - D' on Amazon by local lock"})
            else:
                print(f"  ⏭️  {amazon_id} already placed — skipping duplicate")
        return "duplicate"

    # ── MANUAL OVERRIDE TAG ───────────────────────────────────────
    # Checked AFTER the placement lock above, never before: if the lock already knows
    # this order, it stays a duplicate no matter what the tag says.
    _manual_kind = _parse_manual_tag(merchant_id)
    manual_override = False
    if _manual_kind:
        if not _manual_tag_is_for_profile(_manual_kind, PROFILE_NAME):
            _want = _MANUAL_KIND_SUFFIX[_manual_kind]
            print(f"  ⏭️  {amazon_id} tagged Manual-{_manual_kind} (for the {_want} profile) "
                  f"— not this one ({PROFILE_NAME}) — skipping")
            return "skipped"
        manual_override = True
        print(f"  🖐️  MANUAL OVERRIDE (Manual-{_manual_kind}) — a human approved this order; "
              f"the Gmail duplicate search will be SKIPPED for it. "
              f"The placement lock still applies, so it can only be placed once.")

    # ── Already processed? ────────────────────────────────────────
    if merchant_id and merchant_id.startswith("WB"):
        print(f"  ⏭️  Already has WB ID: {merchant_id} — skipping")
        return "skipped"

    if merchant_id and merchant_id.upper() in ("OOS", "DISCONTINUED", "DUPLICATE", "SR", "NET30"):
        print(f"  ⏭️  Already marked: {merchant_id} — skipping")
        return "skipped"
    if merchant_id and merchant_id.upper().startswith("LOSS"):
        print(f"  ⏭️  Already marked: {merchant_id} — skipping")
        return "skipped"
    
    # ── Resolve ASIN → SKU ────────────────────────────────────────
    if not items:
        print(f"  ❌ No items found for {amazon_id}")
        return "failed"
    
    resolved_items, missing_items = _build_resolved_order_items(items, sku_map)
    if missing_items:
        missing_asins = ", ".join(item.get("asin") or "UNKNOWN" for item in missing_items)
        missing_qtys = ", ".join(str(item.get("qty") or 1) for item in missing_items)
        print(f"  ❌ Unmapped ASIN(s): {missing_asins}")
        for mi in missing_items:
            print(f"  UNMAPPED_ASIN: {mi.get('asin', 'UNKNOWN')}")
        _log_order({
            "amazon_id": amazon_id,
            "asin": missing_asins,
            "sku": "UNKNOWN",
            "qty": missing_qtys,
            "status": "UNMAPPED_ASIN",
            "notes": "ASIN not in verified SKU sheet",
        })
        return "failed"

    if not resolved_items:
        print(f"  ❌ No mapped items found for {amazon_id}")
        return "failed"

    primary_item = resolved_items[0]
    is_multi_item = len(resolved_items) > 1
    asin_field = primary_item["asin"] if not is_multi_item else ", ".join(
        item["asin"] for item in resolved_items
    )
    sku_field = primary_item["sku"] if not is_multi_item else ", ".join(
        f"{item['sku']} x{item['final_qty']}" for item in resolved_items
    )
    qty_field = primary_item["final_qty"] if not is_multi_item else ", ".join(
        str(item["final_qty"]) for item in resolved_items
    )
    order_notes = []
    if is_multi_item:
        order_notes.append(f"Multi-item order ({len(resolved_items)} items)")
    order_note = "; ".join(order_notes)
    
    _print_section("ORDER DETAILS")
    _print_kv("Amazon ID", amazon_id)
    for index, item in enumerate(resolved_items, start=1):
        label = "Item" if not is_multi_item else f"Item {index}"
        _print_kv(label, f"{item['asin']} -> {item['sku']} x{item['final_qty']}")
        if item["second_sku"]:
            second_label = "2nd Part" if not is_multi_item else f"2nd Part {index}"
            _print_kv(second_label, item["second_sku"])
    
    # Dates from Amazon order page
    pd = order_detail.get("purchase_date", "")
    sb = order_detail.get("ship_by_date", "")
    db = order_detail.get("deliver_by_date", "")
    if pd or sb or db:
        print(f"")
        if pd: print(f"  Order Date  : {pd}")
        if sb: print(f"  Ship By     : {sb}")
        if db: print(f"  Deliver By  : {db}")
    
    # ── Address ───────────────────────────────────────────────────
    if not addr_raw or addr_raw.get("skip_order"):
        reason = addr_raw.get("reason", "Scrape Fail") if addr_raw else "Scrape Fail"
        print(f"  ❌ Address: {reason}")
        _log_order({
            "amazon_id": amazon_id,
            "asin": asin_field,
            "sku": sku_field,
            "qty": qty_field,
            "status": "ADDR_FAIL",
            "notes": "; ".join(filter(None, [reason, order_note])),
        })
        return "failed"
    
    addr = validate_and_prepare_address(addr_raw)
    if not addr:
        print(f"  ❌ Address validation failed")
        return "failed"
    
    # Attach Amazon order dates to address dict so they flow through to order_log
    addr["_purchase_date"] = order_detail.get("purchase_date", "")
    addr["_ship_by_date"] = order_detail.get("ship_by_date", "")
    addr["_deliver_by_date"] = order_detail.get("deliver_by_date", "")
    
    city = addr.get("city", "?")
    state = addr.get("state_abbr", "?")
    zipcode = addr.get("zip_code", "?")
    buyer = addr.get("name", "?")
    _print_full_address(addr)
    
    # ── Earnings check ────────────────────────────────────────────
    earnings = None
    original_sku = None
    shipping_amt = None
    amazon_fees = None
    if earnings_service:
        earnings_data = get_amazon_earnings_from_gmail(earnings_service, amazon_id)
        if not earnings_data:
            print(f"  ⏳ No 'Sold, Ship Now' email yet — skipping")
            _log_order({
                "amazon_id": amazon_id,
                "asin": asin_field,
                "sku": sku_field,
                "qty": qty_field,
                "buyer": buyer,
                "city": city,
                "state": state,
                "status": "PENDING_EARNINGS", "notes": "No sold email found",
            })
            return "pending"
        earnings = earnings_data.get("earnings")
        original_sku = earnings_data.get("original_sku")
        shipping_amt = earnings_data.get("shipping")
        amazon_fees = earnings_data.get("amazon_fees")
        print(f"  💰 Earnings: ${earnings:.2f}" + (f" | SKU: {original_sku}" if original_sku else ""))
        if shipping_amt:
            print(f"  📦 Shipping: ${shipping_amt:.2f}")
        if amazon_fees:
            print(f"  🏷️  Amazon Fees: ${amazon_fees:.2f}")

    # Amazon seller SKU for the note: prefer the SKU scraped from the order page
    # (shown right under the ASIN); fall back to the Sold-Ship-Now email's SKU.
    note_amazon_sku = (primary_item.get("seller_sku") or "").strip() or (original_sku or "")

    # ── Duplicate check ───────────────────────────────────────────
    # THE ONE GATE the Manual-C/-R tag bypasses. Everything else — the placement
    # lock above, the reserve() below, profit/loss, SKU mapping, address validation —
    # still runs exactly as normal.
    if duplicate_services and manual_override:
        print(f"  🖐️  skipping the Gmail address-duplicate search for {amazon_id} "
              f"(Manual-{_manual_kind}) — human-approved repeat customer")
        _log_order({
            "amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
            "buyer": buyer, "city": city, "state": state,
            "status": "MANUAL_OVERRIDE",
            "notes": f"Manual-{_manual_kind} tag — Gmail dup search bypassed by operator; "
                     f"placement lock still enforced",
        })
    elif duplicate_services:
        _pre = (dup_prefetch or {}).get(amazon_id)
        if _pre is not None:
            # Resolved by the RUSH batched Gmail search — same 7-day
            # name/zip evidence, one query for the whole batch.
            existing_wb = _pre or None
        else:
            existing_wb = check_if_address_already_ordered_gmail(
                duplicate_services, addr["street_address"],
                zip_code=addr.get("zip_code", ""),
                customer_name=buyer)
        if existing_wb == DUP_CHECK_FAILED:
            # The Gmail duplicate search could not complete (rate-limit / API error).
            # FAIL CLOSED: do NOT place — the old behaviour placed anyway, which is exactly
            # how a throttled check "found no duplicate" and kept re-buying.
            print(f"  🛑 Duplicate check could not be verified (Gmail error) — NOT placing "
                  f"{amazon_id} this cycle; will retry.")
            _log_order({
                "amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                "buyer": buyer, "city": city, "state": state,
                "status": "DUP_CHECK_UNVERIFIED", "notes": "Gmail dup-check failed — fail-closed skip",
            })
            return "pending"
        if existing_wb:
            dup_label = _fit_seller_label(existing_wb, " - D")
            print(f"  🔁 DUPLICATE — already ordered: {existing_wb}")
            update_amazon_seller_id(driver, amazon_id, dup_label, PROFILE_NAME,
                                    skip_navigate=True)
            _log_order({
                "amazon_id": amazon_id,
                "asin": asin_field,
                "sku": sku_field,
                "qty": qty_field,
                "buyer": buyer,
                "city": city,
                "state": state,
                "status": "DUPLICATE", "wb_id": existing_wb,
            })
            return "duplicate"
        print(f"  ✅ No duplicates")
    
    # ── DRY RUN STOP ──────────────────────────────────────────────
    if DRY_RUN:
        print(f"  🏁 DRY RUN — would place order for {sku_field}")
        _log_order({
            "amazon_id": amazon_id,
            "asin": asin_field,
            "sku": sku_field,
            "qty": qty_field,
            "buyer": buyer,
            "city": city,
            "state": state,
            "earnings": earnings or "",
            "status": "DRY_RUN",
            "notes": order_note,
        })
        return "skipped"
    
    # ── PROMO-INELIGIBLE ROUTING (coupon profiles) ────────────────────────
    # Coupons never apply to these ASINs (config / sheet / learned), so a coupon
    # profile hands the order to its reseller sibling instead of burning a cycle.
    if IS_COUPON_PROFILE and not manual_override and _is_promo_ineligible(resolved_items):
        _route_to_sibling(driver, amazon_id, "reseller",
                          "Promo-ineligible ASIN — coupons never apply; reseller profile places it",
                          dict(asin=asin_field, sku=sku_field, qty=qty_field, buyer=buyer,
                               city=city, state=state, order_note=order_note))
        # Keep the sheet in step with the memory: every ineligible ASIN of this order
        # belongs in <store>_Reseller (a PROMO_INELIGIBLE verdict may move it from any
        # column, and re-adds it if someone removed it). Off-thread, idempotent.
        for _it in resolved_items or []:
            _ia = str(_it.get("asin") or "").strip().upper()
            if _ia in PROMO_INELIGIBLE_ASINS or (_it.get("sku") or "") in PROMO_INELIGIBLE_CODES \
                    or (_it.get("main_sku") or "") in PROMO_INELIGIBLE_CODES:
                _record_asin_verdict(_ia, {"status": "PROMO_INELIGIBLE", "suggest": "Reseller",
                                           "note": "promo-ineligible memory (coupon_intel)"})
        return "skipped"

    # ── Zoro Select brand detection (before cart ops) ────────────────
    from utils.config import ZORO_SELECT_DISCOUNT_RATE, ZORO_SELECT_SHIPPING_FLAT
    for item in resolved_items:
        # Fast path: use brand from Google Sheet if available (skips ~4s Selenium page load)
        sheet_brand = (sku_map.get(item.get("asin")) or {}).get("brand", "").strip().lower()
        if sheet_brand:
            item["is_zoro_select"] = "zoro select" in sheet_brand
            if item["is_zoro_select"]:
                print(f"  🏷️  Zoro Select (from sheet): {item['sku']}")
        else:
            # Fallback: load product page in Selenium to detect brand
            brand = get_product_brand(driver, item["sku"])
            item["is_zoro_select"] = bool(brand and "zoro select" in brand.lower())
            if item["is_zoro_select"]:
                print(f"  🏷️  Zoro Select detected: {item['sku']} (brand: {brand})")
            elif brand:
                print(f"  🏷️  Brand: {brand} (not Zoro Select)")
            else:
                print(f"  🏷️  Brand: unknown (not Zoro Select)")

    # Return to base Zoro page for clean cart operations
    # ALWAYS navigate to zoro.com before any API calls — localStorage is origin-scoped,
    # so the token (and Vue app) are only accessible while on zoro.com.
    ensure_on_site(driver)
    ensure_token(driver)

    real_cost = 0.0
    # Undiscounted cart total (pre-Zoro-Select). Deciding-ASIN routing must be
    # judged on this: the coupon profile pays full price, so its coupons' cart
    # minimum has to clear at the undiscounted number.
    raw_cart_cost = 0.0
    margin = 0.0
    profit = None

    # ── Zoro delivery estimate + stock for the primary item (FAIL-SAFE; never blocks a
    #    placement). Done BEFORE the cart flow so the PDP navigation can't disturb checkout
    #    state (the cart is built fresh by clear_cart_and_add below). Values ride along in
    #    addr → order_log (ZoroDeliverBy / ZoroStock), like the Amazon ship/deliver dates.
    try:
        if resolved_items:
            from platforms.zoro import get_zoro_delivery_date, last_inventory_qty
            _psku = resolved_items[0]["sku"]
            check_inventory(driver, _psku, resolved_items[0]["final_qty"])  # populate stock cache
            _zstk = last_inventory_qty(_psku)
            if _zstk is not None:
                addr["_zoro_stock"] = _zstk
            # RUSH: skip the PDP delivery-estimate scrape — it's the only
            # per-order Zoro page load left (stock above is API), and the
            # closing/FedEx pipeline fills real delivery data later anyway.
            if not defer_amazon_writes:
                _zdel = get_zoro_delivery_date(driver, _psku)               # navigates PDP + scrapes
                if _zdel:
                    addr["_zoro_deliver_by"] = _zdel
    except Exception as _e:
        print(f"  ⚠️ Zoro estimate capture skipped: {_e}")

    # ── PDP promo badge: "Zoro's best price — not eligible for any other offers or
    #    promotions" = definitive coupon ineligibility, read from the rendered page.
    #    Free when the delivery-estimate visit above just happened (cached per SKU on
    #    disk); supervised orders force one visit per unknown SKU even in rush mode.
    _pdp_bad = None
    try:
        _psku0 = resolved_items[0]["sku"] if resolved_items else None
        if _psku0:
            _pdp_bad = pdp_promo_ineligible(_psku0)
            if _pdp_bad is None and IS_COUPON_PROFILE and order_detail.get("_supervised"):
                _pdp_bad = check_pdp_promo(driver, _psku0)
        if _pdp_bad:
            _DLOG.log("pdp", profile=PROFILE_NAME, amazon_id=amazon_id, asin=primary_item.get("asin"), sku=_psku0,
                      action="ineligible", reason="Zoro's best price — not eligible for any other offers or promotions")
            _learn_promo_outcome(_psku0, primary_item.get("asin"), False, cache=asin_coupon_cache,
                                 definitive=True, source="pdp")
            if IS_COUPON_PROFILE and not manual_override:
                _route_to_sibling(driver, amazon_id, "reseller",
                                  "Zoro's best price — not eligible for any offers or promotions (PDP badge)",
                                  dict(asin=asin_field, sku=sku_field, qty=qty_field, buyer=buyer,
                                       city=city, state=state, order_note=order_note))
                _record_asin_verdict(primary_item.get("asin"),
                                     {"status": "PROMO_INELIGIBLE", "suggest": "Reseller",
                                      "note": "Zoro's best price badge (PDP)"})
                return "skipped"
    except Exception as _e:
        print(f"  ⚠️ PDP promo check skipped: {_e}")

    first_cart_item = True

    for item in resolved_items:
        cartable, inv_status = check_inventory(driver, item["sku"], item["final_qty"])
        alt_fail_note = ""
        if not cartable and item.get("alt_sku") and inv_status not in ("API_ERROR", "NO_DATA"):
            # Main SKU genuinely OOS and the sheet lists an alternative — try it before
            # marking the order OOS. The main SKU's OOS still goes to the price tracker
            # so "is the main back in stock?" stays answerable from price_tracker.json.
            log_price_or_status(item["sku"], current_price=0.0, status="OOS")
            print(f"  🔄 {item['sku']} OOS — checking alternative {item['alt_sku']}")
            alt_cartable, alt_status = check_inventory(driver, item["alt_sku"], item["final_qty"])
            if alt_cartable:
                item["main_sku"] = item["sku"]
                item["sku"] = item["alt_sku"]
                item["alt_used"] = True
                cartable, inv_status = alt_cartable, alt_status
                print(f"  ✅ ALT SUBSTITUTION: placing {item['sku']} instead of {item['main_sku']} (main OOS)")
            else:
                if alt_status not in ("API_ERROR", "NO_DATA"):
                    log_price_or_status(item["alt_sku"], current_price=0.0, status="OOS")
                alt_fail_note = f"alt {item['alt_sku']}: {alt_status}"
                print(f"  ❌ Alternative {item['alt_sku']} also unavailable ({alt_status})")
        if not cartable:
            # Only label OOS for genuine out-of-stock, not API/network errors
            is_genuine_oos = inv_status not in ("API_ERROR", "NO_DATA")

            # Log status change in price_tracker
            log_price_or_status(item["sku"], current_price=0.0, status="OOS" if is_genuine_oos else inv_status)

            label = "OOS" if is_genuine_oos else None
            status_tag = "OOS" if is_genuine_oos else "INVENTORY_CHECK_FAIL"
            print(f"  ❌ {'OUT OF STOCK' if is_genuine_oos else 'INVENTORY CHECK FAILED'}: {item['sku']} ({inv_status})")
            if label:
                update_amazon_seller_id(driver, amazon_id, label, PROFILE_NAME)
            _log_order({
                "amazon_id": amazon_id,
                "asin": asin_field,
                "sku": sku_field,
                "qty": qty_field,
                "buyer": buyer,
                "city": city,
                "state": state,
                "status": status_tag,
                "notes": "; ".join(filter(None, [f"{item['sku']}: {inv_status}", alt_fail_note, order_note])),
            })
            return "failed"

        if first_cart_item:
            cart_ok, api_price = clear_cart_and_add(driver, item["sku"], item["final_qty"])
            first_cart_item = False
        else:
            cart_ok, api_price = add_second_item(driver, item["sku"], item["final_qty"])

        if not cart_ok:
            print(f"  ❌ Cart/Add failed for {item['sku']}")
            _rec = _CART_FAILS.setdefault(amazon_id, {"n": 0, "last": 0.0})
            _rec["n"] += 1
            _rec["last"] = time.time()
            _rec.pop("announced", None)
            _log_order({
                "amazon_id": amazon_id,
                "asin": asin_field,
                "sku": sku_field,
                "qty": qty_field,
                "buyer": buyer,
                "city": city,
                "state": state,
                "status": "CART_FAIL",
                "notes": "; ".join(filter(None, [f"Failed cart add for {item['sku']}", order_note])),
            })
            return "failed"

        item_unit_price = api_price
        print(f"  🛒 Cart: {item['sku']} x{item['final_qty']} @ ${api_price:.2f} each")
        
        # Log price/status change in price_tracker
        log_price_or_status(item["sku"], current_price=api_price, status="IN_STOCK")

        if item["second_sku"]:
            s_cartable, _ = check_inventory(driver, item["second_sku"], item["final_qty"])
            if s_cartable:
                s_ok, s_price = add_second_item(driver, item["second_sku"], item["final_qty"])
                if s_ok:
                    item_unit_price += s_price
                    print(f"  🛒 2nd: {item['second_sku']} x{item['final_qty']} @ ${s_price:.2f} each")
                    log_price_or_status(item["second_sku"], current_price=s_price, status="IN_STOCK")

        raw_cart_cost += item_unit_price * item["final_qty"]

        # Apply Zoro Select 10% discount to the unit price for margin calculation
        # (RESELLER profiles only — coupon profiles do NOT get this discount)
        if item["is_zoro_select"] and IS_RESELLER_PROFILE:
            from utils.config import ZORO_SELECT_DISCOUNT_RATE
            discount = item_unit_price * ZORO_SELECT_DISCOUNT_RATE
            adjusted_unit = item_unit_price - discount
            print(f"  🏷️  Zoro Select discount: ${item_unit_price:.2f} → ${adjusted_unit:.2f} (-{ZORO_SELECT_DISCOUNT_RATE:.0%})")
            item_unit_price = adjusted_unit

        item["unit_price"] = item_unit_price
        item["line_cost"] = item_unit_price * item["final_qty"]
        real_cost += item["line_cost"]

    # ── Alternative substitutions: refresh log fields + audit note ──
    # (sku_field/order_note were built before the inventory loop; a swap to the
    # alternative Zoro code must show the code actually placed, everywhere.)
    alt_used_any = any(it.get("alt_used") for it in resolved_items)
    alt_main_sku = next((it["main_sku"] for it in resolved_items if it.get("alt_used")), None)
    if alt_used_any:
        sku_field = primary_item["sku"] if not is_multi_item else ", ".join(
            f"{it['sku']} x{it['final_qty']}" for it in resolved_items)
        _alt_pairs = ", ".join(
            f"{it['sku']} for {it['main_sku']}" for it in resolved_items if it.get("alt_used"))
        order_note = "; ".join(filter(None, [order_note, f"ALT: placed {_alt_pairs} (main OOS)"]))

    # ── DECIDING-ASIN ROUTING (reseller profiles only) ──────────────
    # ASINs in the sheet's *_Deciding columns can't be statically assigned to a
    # profile: coupon eligibility depends on THIS order's Zoro cart total
    # (final qty × live price, all items). At/above the threshold the order is
    # labelled for the sibling coupon profile — which scrapes that label every
    # cycle — and NOT placed here. Below it, it's a normal reseller order.
    # EXCEPTION: Zoro Select carts never route. The reseller already gets the
    # Select discount on those items while the coupon profile pays full price,
    # so routing would burn a single-use coupon for the same ~10% off.
    # Belt AND suspenders: the discovery stamp (fast path) OR a direct membership
    # check against the cycle's Deciding list. The stamp alone proved fragile — it
    # has to survive the list→detail dict hop, and when it silently didn't, this
    # routing never fired at all. The membership check makes the gate correct even
    # if a discovery path forgets to stamp.
    _is_deciding_order = bool(order_detail.get("_deciding"))
    if not _is_deciding_order and IS_RESELLER_PROFILE and DECIDING_ASINS:
        _dset = {d.strip().upper() for d in DECIDING_ASINS if d and d.strip()}
        _is_deciding_order = any((it.get("asin") or "").strip().upper() in _dset
                                 for it in resolved_items)
    if _is_deciding_order and IS_RESELLER_PROFILE:
        # Aliased on purpose: a plain `from … import ZORO_SMALL_ORDER_FEE` here would make
        # that name LOCAL to the whole function and break its module-level use further
        # down (UnboundLocalError on every order — happened on 7 Sep 2026).
        from utils.config import ZORO_SMALL_ORDER_FEE as _dec_fee, FREE_SHIPPING_THRESHOLD as _dec_free
        _verdict = _deciding_verdict(
            raw_cart_cost, real_cost, earnings,
            promo_bad=_is_promo_ineligible(resolved_items),
            all_zoro_select=all(it.get("is_zoro_select") for it in resolved_items),
            small_fee=_dec_fee, free_threshold=_dec_free)
        _DLOG.log("deciding", profile=PROFILE_NAME, amazon_id=amazon_id, asin=asin_field, sku=sku_field, qty=qty_field,
                  unit_price=primary_item.get("unit_price"), cart=raw_cart_cost, real_cost=real_cost, earnings=earnings,
                  margin=_verdict.get("est_margin"), zoro_select=all(it.get("is_zoro_select") for it in resolved_items),
                  pdp_ineligible=_is_promo_ineligible(resolved_items),
                  action="route_to_coupon" if _verdict["route"] else "place_here", reason=_verdict["reason"],
                  buyer=buyer, state=state)
        if _verdict["route"]:
            _route_to_sibling(driver, amazon_id, "coupon", _verdict["reason"],
                              dict(asin=asin_field, sku=sku_field, qty=qty_field, buyer=buyer,
                                   city=city, state=state, order_note=order_note))
            return "skipped"
        print(f"  🔀 DECIDING: {_verdict['reason']} — stays with {PROFILE_NAME} (reseller)")

    # ── Unpack coupon data early (needed for split detection) ───
    priority_coupons_with_pct = []
    regular_coupon_codes = []
    if isinstance(coupon_codes, tuple) and len(coupon_codes) == 2:
        priority_coupons_with_pct, regular_coupon_codes = coupon_codes
    elif isinstance(coupon_codes, list):
        regular_coupon_codes = coupon_codes  # legacy fallback

    # ── Evaluate optimal coupon strategy ────────────────────────
    strategy = _evaluate_order_strategy(
        resolved_items, priority_coupons_with_pct, primary_item.get("unit_price", 0))
    orders = strategy["orders"]
    is_split_order = len(orders) > 1
    split_count = len(orders)
    original_final_qty = primary_item.get("final_qty", 1)

    # ── SUPERVISOR DECISION (unlabelled, non-priority orders — coupon profiles) ──
    supervised_coupon = None
    if IS_COUPON_PROFILE and order_detail.get("_supervised") and not manual_override:
        from utils.config import ZORO_SMALL_ORDER_FEE as _FEE, FREE_SHIPPING_THRESHOLD as _FREE
        _res_cost = raw_cart_cost + (_FEE if raw_cart_cost < _FREE else 0.0)   # reseller pays list price
        _res_margin = ((earnings - _res_cost) / _res_cost) if (earnings and _res_cost > 0) else None
        _dec = _supervise_coupon_decision(
            driver, resolved_items, raw_cart_cost, priority_coupons_with_pct, regular_coupon_codes,
            asin_coupon_cache,
            all_zoro_select=all(it.get("is_zoro_select") for it in resolved_items),
            reseller_est_margin=_res_margin, unit_price=primary_item.get("unit_price"),
            pdp_ineligible=_pdp_bad)
        _cart_txt = f"${raw_cart_cost:.2f} ({sku_field})"
        _DLOG.log("supervisor", profile=PROFILE_NAME, amazon_id=amazon_id, asin=primary_item.get("asin"),
                  sku=sku_field, qty=qty_field, unit_price=primary_item.get("unit_price"), cart=raw_cart_cost,
                  real_cost=real_cost, earnings=earnings, margin=_res_margin,
                  zoro_select=all(it.get("is_zoro_select") for it in resolved_items), pdp_ineligible=bool(_pdp_bad),
                  priority_codes=[(c or {}).get("code") for c in (priority_coupons_with_pct or [])],
                  regular_available=len(regular_coupon_codes or []),
                  action=_dec["action"], coupon=_dec.get("coupon"), tier=_dec.get("tier"), reason=_dec["reason"],
                  verdict=_dec.get("verdict"), attempts=_dec.get("attempts", []), min_orders=_dec.get("min_orders", {}),
                  buyer=buyer, city=city, state=state, items=[{"sku": it.get("sku"), "asin": it.get("asin"),
                                                                "qty": it.get("final_qty"), "unit": it.get("unit_price"),
                                                                "select": bool(it.get("is_zoro_select"))}
                                                               for it in resolved_items])
        if _dec.get("verdict") and priority_coupons_with_pct:
            _record_asin_verdict(primary_item.get("asin"), _dec["verdict"], _cart_txt, _dec.get("coupon") or "")
        elif _dec.get("verdict"):
            print("  🧑‍⚖️ SUPERVISOR: no priority coupons loaded — order handled, ASIN left unsorted until they are")
        if _dec["action"] == "reseller":
            _route_to_sibling(driver, amazon_id, "reseller", "Supervisor: " + _dec["reason"],
                              dict(asin=asin_field, sku=sku_field, qty=qty_field, buyer=buyer,
                                   city=city, state=state, order_note=order_note))
            return "skipped"
        if _dec["action"] in ("retry", "wait"):
            # left UNLABELLED on purpose: picked up again next cycle (wait = needs a coupon
            # and none is left today; retry = Zoro API/session trouble)
            print(f"  🧑‍⚖️ SUPERVISOR: {_dec['reason']} — left unlabelled, next cycle")
            _log_order({"amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                        "buyer": buyer, "city": city, "state": state,
                        "status": "SUPERVISOR_WAIT_COUPONS" if _dec["action"] == "wait" else "SUPERVISOR_RETRY",
                        "notes": "; ".join(filter(None, [_dec["reason"], order_note]))})
            return "failed"
        supervised_coupon = _dec.get("coupon")
        print(f"  🧑‍⚖️ SUPERVISOR: {_dec['reason']} → placing here")
        order_note = "; ".join(filter(None, [order_note, f"Supervisor: {_dec['reason']}"]))

    if is_split_order:
        qtys = " ".join(str(o["qty"]) for o in orders)
        print(f"\n  ┌─── ✂️  ORDER SPLIT ─────────────────────┐")
        print(f"  │  Original qty : {original_final_qty}")
        print(f"  │  Split into   : {split_count} order(s) [qty: {qtys}]")
        print(f"  │  Strategy     : {strategy['discount_description']}")
        print(f"  └──────────────────────────────────────────┘")

    # ── Estimated profit preview ──────────────────────────────────
    # Use strategy's estimated cost (includes coupon discount) for accurate gating
    estimated_shipping = strategy["estimated_shipping"]
    estimated_total_cost = strategy["estimated_cost"]
    if is_split_order:
        real_cost = estimated_total_cost - estimated_shipping
    else:
        # For single orders, keep strategy cost if it includes a coupon;
        # otherwise fall back to raw cost + shipping
        if estimated_total_cost > 0 and estimated_total_cost < (real_cost + estimated_shipping):
            # Strategy found a cheaper cost (coupon applied)
            real_cost = estimated_total_cost - estimated_shipping
        else:
            estimated_shipping = ZORO_SMALL_ORDER_FEE if real_cost < 50.0 else 0.0
            estimated_total_cost = real_cost + estimated_shipping

    # ── Filler-aware shipping: if cart is close to $50, adding filler unlocks free shipping ──
    if not is_split_order and earnings:
        from utils.config import (
            SHIPPING_FILLER_SKU, SHIPPING_FILLER_PRICE,
            FREE_SHIPPING_THRESHOLD, FILLER_TRIGGER_MIN
        )
        if SHIPPING_FILLER_SKU and FILLER_TRIGGER_MIN <= real_cost < FREE_SHIPPING_THRESHOLD:
            gap = FREE_SHIPPING_THRESHOLD - real_cost
            import math
            filler_qty = math.ceil(gap / SHIPPING_FILLER_PRICE)
            filler_cost = filler_qty * SHIPPING_FILLER_PRICE
            new_total = real_cost + filler_cost  # FREE shipping since >= $50
            if new_total < estimated_total_cost:
                print(f"  📦 Filler strategy: +{filler_qty}x {SHIPPING_FILLER_SKU} (${filler_cost:.2f}) → FREE shipping")
                real_cost = new_total
                estimated_shipping = 0.0
                estimated_total_cost = new_total

    if earnings:
        profit = round(earnings - estimated_total_cost, 2)
        margin = (earnings - estimated_total_cost) / estimated_total_cost if estimated_total_cost > 0 else 0
        ship_note = f" +${estimated_shipping:.2f} ship" if estimated_shipping > 0 else ""
        title = primary_item.get('title', '')
        if title:
            print(f"  Title   : {title[:90]}{'...' if len(title) > 90 else ''}")
        print(f"  ASIN    : {primary_item.get('asin', 'N/A')}")
        print(f"  SKU     : {sku_field}")
        print(f"")
        print(f"  Payout  : ${earnings:.2f}")
        print(f"  Est Cost: ${real_cost:.2f}{ship_note}")
        print(f"  Est Mgn : {margin:.1%}")
        print(f"  Est Pft : ${profit:.2f}")

        # If priority coupons are available, show coupon-adjusted estimate.
        # Use cache data for this specific zoro_code instead of blindly
        # assuming the best coupon among all priority coupons.
        coupon_adjusted_margin = margin
        if priority_coupons_with_pct and IS_COUPON_PROFILE and not is_split_order:
            est_pct = _estimate_best_coupon_pct(
                priority_coupons_with_pct, primary_item["sku"], asin_coupon_cache)
            if est_pct is not None:
                best_pct = est_pct / 100.0
                coupon_discount = real_cost * best_pct
                adjusted_cost = real_cost - coupon_discount + estimated_shipping
                adjusted_profit = round(earnings - adjusted_cost, 2)
                coupon_adjusted_margin = (earnings - adjusted_cost) / adjusted_cost if adjusted_cost > 0 else 0
                print(f"  │  With {best_pct:.0%} coupon: Est Pft ${adjusted_profit:.2f} ({coupon_adjusted_margin:.1%})")
            else:
                print(f"  │  No coupon estimate for this zoro_code yet")
                coupon_adjusted_margin = margin

        print(f"  └──────────────────────────────────────────┘")

        # NOTE: the pre-checkout ESTIMATED-margin reject was removed by request —
        # estimates (especially before coupons apply) wrongly rejected profitable
        # orders as "Loss-Kamran checking". The decision now happens AFTER checkout
        # on the real FINAL margin (pre-tax) in the checkout gate (true_api.py).
        # The estimate above is informational only. (coupon_adjusted_margin kept for
        # logging/coupon-strategy below.)

    # ── Coupon strategy ─────────────────────────────────────────
    skip_coupons = not IS_COUPON_PROFILE or (TARGET_LABEL == 'Rajar 1')
    _pcfg = gui_cfg.get("profiles", {}).get(PROFILE_NAME, {})
    skip_priority = _pcfg.get("skip_priority_coupons", False)

    # Build the flat coupon list for the checkout function
    if skip_coupons:
        coupon_arg = None
        priority_codes_for_checkout = []
    else:
        # Merge priority + regular into a single flat list for _apply_coupons
        # Priority coupons go first in the list
        # Deduplicate: same code may appear with different tiers (e.g. 10:50 and 50:200)
        seen_codes = set()
        priority_codes_for_checkout = []
        for c in (priority_coupons_with_pct if not skip_priority else []):
            code = c["code"]
            if code not in seen_codes:
                seen_codes.add(code)
                priority_codes_for_checkout.append(code)
        coupon_arg = priority_codes_for_checkout + regular_coupon_codes

    # Build coupon metadata map for checkout-time ratio-split detection
    coupon_meta_by_code = {}
    for c in (priority_coupons_with_pct or []):
        code = c["code"]
        if code not in coupon_meta_by_code:
            coupon_meta_by_code[code] = []
        coupon_meta_by_code[code].append(c)

    if IS_COUPON_PROFILE and not skip_coupons:
        print(f"  🏷️  Coupon: ON ({len(priority_codes_for_checkout)} priority + {len(regular_coupon_codes)} regular)" + (" (no priority)" if skip_priority else ""))
    elif IS_COUPON_PROFILE:
        print(f"  🏷️  Coupon: OFF ({TARGET_LABEL} profile)")
    if IS_COUPON_PROFILE and not skip_coupons and not manual_override and not coupon_arg:
        # No coupon at all in today's pool: this profile exists to place WITH coupons, so it
        # waits for the next coupon load instead of buying at list price.
        print("  ⏸️  No coupons available (priority or regular) — not placing at full price; waiting for the next coupon load")
        _log_order({"amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                    "buyer": buyer, "city": city, "state": state, "status": "NO_COUPONS_WAIT",
                    "notes": "; ".join(filter(None, ["no priority or regular coupons loaded — waiting", order_note]))})
        return "failed"

    # ── Checkout ──────────────────────────────────────────────────
    # FINAL-MARGIN GATE: now computed on the PRE-TAX checkout total in true_api.py
    # (sales tax is refunded → the grand total over-reports cost). Because it's now
    # accurate, it is ALWAYS enabled when earnings are known (the old
    # MARGIN_CHECK_EST_ONLY tax workaround is obsolete; the pre-checkout estimate
    # reject was removed). Uses THIS order's own threshold (primary vs secondary label).
    active_margin_thresh = order_detail.get("_margin_thresh", MIN_MARGIN)
    checkout_min_margin = active_margin_thresh if earnings is not None else None
    if manual_override:
        # SECOND (and last) gate the Manual-C/-R tag bypasses. Without this the operator
        # would have to LOWER THE GLOBAL THRESHOLD to push one accepted-loss order
        # through — which would silently drop the bar for every other order in the same
        # cycle. Waiving it for this one human-approved order is far more contained.
        # min_margin=None disables the gate in checkout (see checkout/api.py:575).
        if checkout_min_margin is not None:
            print(f"  🖐️  profit gate waived for {amazon_id} (Manual-{_manual_kind}) — "
                  f"would have required {checkout_min_margin:.0%}")
        checkout_min_margin = None

    # Checkout priority: True REST API → Vuex dispatch → DOM Selenium
    # Build extra_skus for multi-item Amazon orders (everything after primary)
    extra_skus = None
    if len(resolved_items) > 1:
        extra_skus = [
            (item["sku"], item["final_qty"])
            for item in resolved_items[1:]
        ]
        print(f"  📦 Multi-item order: passing {len(extra_skus)} extra SKU(s) to checkout")

    # Checkout priority: True REST API → Vuex dispatch → DOM Selenium
    # Build extra_skus for multi-item Amazon orders (everything after primary)
    extra_skus = None
    if len(resolved_items) > 1:
        extra_skus = [
            (item["sku"], item["final_qty"])
            for item in resolved_items[1:]
        ]
        print(f"  📦 Multi-item order: passing {len(extra_skus)} extra SKU(s) to checkout")

    # DEBUG: trace coupon list sizes
    print(f"   [DEBUG] coupon_arg len={len(coupon_arg) if coupon_arg else 0} (priority={len(priority_codes_for_checkout)}, regular={len(regular_coupon_codes)})")
    if coupon_arg:
        print(f"   [DEBUG] coupon_arg first 10: {coupon_arg[:10]}")
        print(f"   [DEBUG] coupon_arg last 10: {coupon_arg[-10:]}")

    _checkout_kwargs = dict(
        earnings=earnings, pre_price=real_cost,
        original_sku=note_amazon_sku,  # page SKU (under ASIN) preferred, email SKU fallback — keeps log/sheet/note in sync
        shipping_amt=shipping_amt,
        amazon_fees=amazon_fees,
        coupon_list=coupon_arg, asin=primary_item["asin"],
        zoro_code=primary_item["sku"],
        asin_coupon_cache=asin_coupon_cache,
        skip_priority_coupons=skip_priority,
        priority_coupons=priority_codes_for_checkout,
        min_margin=checkout_min_margin,
        return_details=True,
        second_sku=primary_item.get("second_sku") or None,
    )

    def _run_single_checkout(qty_override=None, earnings_override=None, split_coupon=None):
        """Attempt checkout once. Returns checkout_result dict or None.

        split_coupon: set for split sub-orders — the ONLY coupon this checkout
        may use (the one whose ratio discount justified the split). If it can't
        apply (typically the small remainder split), the order places couponless
        instead of burning a single-use signup code for a trivial discount."""
        qty = qty_override if qty_override is not None else primary_item["final_qty"]
        # For split orders, divide earnings so per-split profit is accurate
        checkout_kwargs = dict(_checkout_kwargs)
        if earnings_override is not None:
            checkout_kwargs["earnings"] = earnings_override
        if split_coupon:
            # Also restricts the vuex/DOM fallbacks, which don't know split mode:
            # with only this code in the book they can't reach the signup coupons.
            checkout_kwargs["coupon_list"] = [split_coupon]
            checkout_kwargs["priority_coupons"] = [split_coupon]
        result = None
        # 1. True API (REST endpoints)
        if place_order_fast_true_api is not None:
            try:
                result = place_order_fast_true_api(
                    driver, primary_item["sku"], qty,
                    addr, PO_NUMBER, PROFILE_NAME,
                    extra_skus=extra_skus,
                    amazon_id=amazon_id,
                    merchant_id=merchant_id,
                    amazon_order_qty=primary_item.get("order_qty"),
                    zoro_multiple=primary_item.get("zoro_multiple"),
                    qty_multiplier=primary_item.get("qty_multiplier"),
                    item_title=primary_item.get("title", ""),
                    est_shipping=estimated_shipping,
                    est_profit=profit if earnings else None,
                    est_margin=margin if earnings else None,
                    preferred_coupon=split_coupon or supervised_coupon or strategy.get("preferred_coupon"),
                    coupon_meta_by_code=coupon_meta_by_code,
                    alt_of=alt_main_sku,
                    payment_method=PAYMENT_METHOD,
                    split_coupon_only=split_coupon,
                    # Was the Zoro Select discount actually applied to this
                    # order's cost? Only reseller profiles receive it, so the
                    # seller note can say whether the margin already includes it.
                    zoro_select=bool(
                        IS_RESELLER_PROFILE
                        and all(it.get("is_zoro_select") for it in resolved_items)),
                    **filter_kwargs_for_callee(place_order_fast_true_api,
                                               checkout_kwargs, warn_label="ENGINE"))
            except Exception as true_api_err:
                print(f"   [ENGINE] True API failed: {true_api_err}")
                result = None
        # 2. Vuex fast path
        if result is None:
            try:
                from checkout.api import place_order_fast_vuex
                result = place_order_fast_vuex(
                    driver, primary_item["sku"], qty,
                    addr, PO_NUMBER, PROFILE_NAME,
                    **filter_kwargs_for_callee(place_order_fast_vuex,
                                               checkout_kwargs, warn_label="ENGINE"))
            except (ImportError, TypeError) as vuex_err:
                # TypeError = signature drift the kwargs filter couldn't absorb.
                # Broader exceptions must still propagate: vuex may have already
                # placed the order, and falling through to DOM would re-buy it.
                print(f"   [ENGINE] Vuex checkout unavailable ({vuex_err}) — falling back to DOM")
                result = None
        # 3. Legacy DOM
        if result is None:
            result = place_order_fast(
                driver, primary_item["sku"], qty,
                addr, PO_NUMBER, PROFILE_NAME,
                **filter_kwargs_for_callee(place_order_fast,
                                           checkout_kwargs, warn_label="ENGINE"))
        return result

    # ── Last-second column check (reseller profiles, unlabelled discovery orders) ──
    # The coupon placer's checker may have promoted this ASIN to <store>_Coupon AFTER this
    # cycle loaded its sheet lists (a priority coupon works on it now). Never place it at
    # list price then: hand it to the coupon profile. Labelled orders are left alone — a
    # person or the sibling profile decided those.
    if IS_RESELLER_PROFILE and not (merchant_id or "").strip() and not manual_override:
        try:
            from engine import priority_check as _pc_late
            _late = _pc_late.load_verdicts().get(str(primary_item.get("asin") or "").strip().upper()) or {}
            _cycle_ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(_CYCLE_STARTED_AT[0]))
            if (str(_late.get("column") or "").upper().endswith("_COUPON")
                    and _late.get("owner") == "bot" and (_late.get("at") or "") >= _cycle_ts):
                _route_to_sibling(driver, amazon_id, "coupon",
                                  f"ASIN promoted to the Coupon column at {_late.get('at')} "
                                  f"(priority coupon {_late.get('coupon') or ''} works) — after this cycle's sheet load",
                                  dict(asin=asin_field, sku=sku_field, qty=qty_field, buyer=buyer,
                                       city=city, state=state, order_note=order_note))
                return "skipped"
        except Exception as _e:
            print(f"  ⚠️ late column check skipped: {_e}")

    # ── RESERVE the claim BEFORE spending a single cent ───────────
    # This is the atomic gate. If a claim already exists (placed this run, in history,
    # or by another profile) we HARD-STOP. If the lock DB is unreachable we FAIL CLOSED
    # and do NOT place. The claim is reconciled to CONFIRMED/RELEASED/FAILED_KEPT below.
    if _HAS_LOCK and not DRY_RUN:
        _lock_state, _lock_info = plock.reserve(amazon_id, PROFILE_NAME)
        if _lock_state == plock.BLOCKED:
            _bst = (_lock_info or {}).get("status", "")
            _bwb = ((_lock_info or {}).get("wb") or "").strip()
            if _bst == "FAILED_KEPT":
                print(f"  🔒 HELD FOR REVIEW — {amazon_id} is in the Locked Orders tab — not placing")
            elif _bwb.startswith("WB") or _bwb.startswith("WW"):
                _dl = _fit_seller_label(_bwb, " - D")
                update_amazon_seller_id(driver, amazon_id, _dl, PROFILE_NAME)
                print(f"  ⏭️  {amazon_id} already placed ({_bwb}) — marked Amazon '{_dl}' (not placing again)")
                _log_order({"amazon_id": amazon_id, "status": "DUPLICATE", "wb_id": _bwb,
                            "notes": "already placed — marked '<WB> - D' on Amazon by local lock"})
            else:
                print(f"  ⏭️  {amazon_id} already placed/claimed — not placing again (duplicate)")
            return "duplicate"
        if _lock_state == plock.ERROR:
            print(f"  🛑 LOCK DB unreachable — FAIL CLOSED, refusing to place {amazon_id}")
            return "failed"

    # ── Execute checkout (single or split) ────────────────────────
    all_wb_ids = []
    split_profits = []
    split_costs = []
    split_taxes = []
    split_discounts = []
    final_result_status = "FAILED"
    last_checkout_result = None
    logged_tax = None
    logged_discount = None

    if is_split_order:
        # Place orders according to the optimal partition. Sub-orders are locked
        # to the strategy's coupon (when known) so no split can raid the wider
        # coupon book — see _run_single_checkout(split_coupon=...).
        _plan_coupon = strategy.get("preferred_coupon") or None
        split_earnings = earnings / split_count if earnings else None
        for split_idx, order_cfg in enumerate(orders):
            split_qty = order_cfg["qty"]
            print(f"\n  [SPLIT {split_idx + 1}/{split_count}] Placing qty={split_qty} order...")
            checkout_result = _run_single_checkout(
                qty_override=split_qty, earnings_override=split_earnings,
                split_coupon=_plan_coupon)
            last_checkout_result = checkout_result

            if checkout_result and checkout_result.get("status") == "SUCCESS":
                wb_id = checkout_result.get("wb_id", "")
                if wb_id.startswith("WB"):
                    all_wb_ids.append(wb_id)
                    split_profits.append(checkout_result.get("profit", 0))
                    split_costs.append(checkout_result.get("checkout_total", 0))
                    split_taxes.append(checkout_result.get("tax"))
                    split_discounts.append(checkout_result.get("discount"))
                    print(f"  ✅ Split {split_idx + 1} placed: {wb_id}")
                    # Small delay between split orders to avoid rate limits
                    if split_idx < split_count - 1:
                        time.sleep(2)
                    continue

            # Failed — stop splitting, record the failure reason
            final_result_status = checkout_result.get("status") if checkout_result else "FAILED"
            print(f"  ❌ Split {split_idx + 1} failed: {final_result_status}")
            break

        # Aggregate results
        if all_wb_ids:
            result_status = "SUCCESS"
            result_id = " ".join(all_wb_ids)
            profit = sum(split_profits) if split_profits else None
            logged_cost = sum(split_costs) if split_costs else estimated_total_cost
            logged_tax = sum(t for t in split_taxes if t is not None) if any(t is not None for t in split_taxes) else None
            logged_discount = sum(d for d in split_discounts if d is not None) if any(d is not None for d in split_discounts) else None
            margin = (earnings - logged_cost) / logged_cost if (earnings and logged_cost > 0) else 0
        else:
            result_status = final_result_status
            result_id = ""
            logged_cost = estimated_total_cost
    else:
        # Normal single checkout
        checkout_result = _run_single_checkout()
        last_checkout_result = checkout_result
        result_status = checkout_result.get("status") or "FAILED"

        # ── Checkout-time split retry: ratio coupon says splitting saves money ──
        if result_status == "SPLIT_RETRY":
            split_orders = checkout_result.get("split_plan", [])
            # If the split plan is just a single order, retrying is pointless.
            if len(split_orders) <= 1:
                print(f"\n  ⚠️  Split retry requested but plan { [o.get('qty') for o in split_orders] } is not a real split.")
                print(f"     Proceeding with the applied coupon instead of retrying.")
                result_status = "SUCCESS"
            else:
                print(f"\n  🔄 Single order aborted — retrying with split strategy...")
                print(f"     Split plan: {[o.get('qty') for o in split_orders]}")
                # The coupon whose ratio discount justified this split. Each
                # sub-order may use ONLY this code; the remainder split places
                # couponless when it can't meet the coupon's minimum.
                _split_coupon = checkout_result.get("used_coupon_code") or None
                if _split_coupon:
                    print(f"     Split coupon: {_split_coupon} (sub-orders use only this; "
                          f"no fallback to the signup-coupon book)")
                # Switch to split mode
                is_split_order = True
                orders = split_orders
                split_count = len(orders)
                split_earnings = earnings / split_count if earnings else None
                for split_idx, order_cfg in enumerate(orders):
                    split_qty = order_cfg.get("qty", 1)
                    print(f"\n  [SPLIT {split_idx + 1}/{split_count}] Placing qty={split_qty} order...")
                    checkout_result = _run_single_checkout(
                        qty_override=split_qty, earnings_override=split_earnings,
                        split_coupon=_split_coupon)
                    last_checkout_result = checkout_result

                    if checkout_result and checkout_result.get("status") == "SUCCESS":
                        wb_id = checkout_result.get("wb_id", "")
                        if wb_id.startswith("WB"):
                            all_wb_ids.append(wb_id)
                            split_profits.append(checkout_result.get("profit", 0))
                            split_costs.append(checkout_result.get("checkout_total", 0))
                            print(f"  ✅ Split {split_idx + 1} placed: {wb_id}")
                            if split_idx < split_count - 1:
                                time.sleep(2)
                            continue

                    final_result_status = checkout_result.get("status") if checkout_result else "FAILED"
                    print(f"  ❌ Split {split_idx + 1} failed: {final_result_status}")
                    break

                if all_wb_ids:
                    result_status = "SUCCESS"
                    result_id = " ".join(all_wb_ids)
                    profit = sum(split_profits) if split_profits else None
                    logged_cost = sum(split_costs) if split_costs else estimated_total_cost
                    margin = (earnings - logged_cost) / logged_cost if (earnings and logged_cost > 0) else 0
                else:
                    result_status = final_result_status
                    result_id = ""
                    logged_cost = estimated_total_cost
        else:
            result_id = checkout_result.get("wb_id") or ""
            checkout_total = checkout_result.get("checkout_total")
            logged_tax = checkout_result.get("tax")
            logged_discount = checkout_result.get("discount")
            if checkout_result.get("profit") is not None:
                profit = checkout_result.get("profit")
            if checkout_result.get("margin") is not None:
                margin = checkout_result.get("margin")
            logged_cost = checkout_total if checkout_total is not None else estimated_total_cost

    # ── Result handling ───────────────────────────────────────────
    margin_str = str(int(round(margin * 100))) if earnings else "0"

    if result_status == "SUCCESS" and (result_id.startswith("WB") or " " in result_id):
        # Money has been spent. CONFIRM the local claim FIRST so this order can never be
        # re-bought, regardless of whether the Amazon write-back below sticks.
        if _HAS_LOCK and not DRY_RUN:
            plock.confirm(amazon_id, result_id)
        if defer_amazon_writes:
            _rush_defer_wb(amazon_id, _fit_seller_label(result_id))
        else:
            _wb_writeback_ok = update_amazon_seller_id(
                driver, amazon_id, _fit_seller_label(result_id), PROFILE_NAME)
            if not _wb_writeback_ok:
                # Write-back to Amazon failed AFTER a successful purchase. The local claim already
                # protects against re-buy; surface it loudly so the WB can be set by hand.
                print(f"  ⚠️  WB write-back to Amazon FAILED for {amazon_id} ({result_id}) — "
                      f"local lock is protecting against re-buy; set the seller-id manually.")
        if is_split_order:
            qtys_str = " ".join(str(o["qty"]) for o in orders)
            print(f"\n  ╔══════════════════════════════════════════╗")
            print(f"  ║  ✅ SPLIT ORDER PLACED                    ║")
            print(f"  ║  Amazon: {amazon_id:<30}║")
            print(f"  ║  WB IDs: {result_id[:28]:<28}  ║")
            print(f"  ║  Qtys:   {qtys_str:<30}║")
            print(f"  ║  Splits: {len(all_wb_ids)}/{split_count:<28}║")
            print(f"  ║  SKU:    {primary_item['sku']:<30}║")
            if profit is not None:
                pfx = "+" if profit >= 0 else ""
                print(f"  ║  Profit: {pfx}${profit:.2f}{' ':>28}║")
            print(f"  ╚══════════════════════════════════════════╝")
            # Log each split sub-order separately
            for idx, wb_id in enumerate(all_wb_ids):
                split_qty = orders[idx]["qty"] if idx < len(orders) else 1
                split_detail = {
                    "amazon_id": amazon_id,
                    "asin": asin_field,
                    "sku": primary_item["sku"],
                    "qty": split_qty,
                    "buyer": buyer,
                    "city": city,
                    "state": state,
                    "zip": zipcode,
                    "earnings": earnings or "",
                    "zoro_price": split_costs[idx] if idx < len(split_costs) else logged_cost / len(all_wb_ids),
                    "profit": split_profits[idx] if idx < len(split_profits) else "",
                    "status": "SUCCESS",
                    "wb_id": wb_id,
                    "notes": f"Split {idx+1}/{split_count} (qty={split_qty}); {order_note}",
                }
                _log_order(split_detail)
                _upsert_unified_db(amazon_id, split_detail,
                    amazon_fees=amazon_fees,
                    shipping_amt=shipping_amt,
                    purchase_date=pd,
                    ship_by_date=sb,
                    deliver_by_date=db)
            # Write seller note for split order (driver already on Amazon page)
            try:
                from utils.seller_notes import format_seller_note, enrich_note_basis
                note_data = {
                    "title": primary_item.get("title", ""),
                    "asin": asin_field,
                    "sku": note_amazon_sku,
                    "code": (primary_item["sku"].replace("G", "K", 1) if primary_item["sku"].startswith("G") else primary_item["sku"]) + (" - ALT" if alt_used_any else ""),
                    "qty": qty_field,
                    "tracking_sent": "No",
                    "tc": logged_cost,
                    "tax": logged_tax,
                    "earnings": earnings,
                    "pm": profit,
                    "pp": (margin * 100) if margin is not None else None,
                    "coupon": bool(last_checkout_result.get("used_coupon_code")) if last_checkout_result else False,
                    "coupon_code": (last_checkout_result.get("used_coupon_code") or "") if last_checkout_result else "",
                    "coupon_off": logged_discount,
                }
                enrich_note_basis(
                    note_data, profile_name=PROFILE_NAME,
                    zoro_select=bool(IS_RESELLER_PROFILE and all(
                        it.get("is_zoro_select") for it in resolved_items)),
                    earnings=earnings, amazon_fees=amazon_fees,
                    shipping_amt=shipping_amt, total_cost=logged_cost)
                _note_text = format_seller_note(note_data)
                if defer_amazon_writes:
                    _rush_defer_note(amazon_id, _note_text)
                else:
                    write_seller_note(driver, amazon_id, note_text=_note_text,
                                      skip_navigate=True)
            except Exception as e:
                print(f"   [SellerNote] ⚠️  Could not write note: {e}")
        else:
            print(f"\n  ╔══════════════════════════════════════════╗")
            if is_multi_item:
                print(f"  ║  ✅ MULTI-ITEM ORDER: {result_id:<19}║")
                print(f"  ║  Amazon: {amazon_id:<30}║")
                print(f"  ║  Items:  {len(resolved_items):<30}║")
            else:
                print(f"  ║  ✅ ORDER PLACED: {result_id:<22}║")
                print(f"  ║  Amazon: {amazon_id:<30}║")
                print(f"  ║  SKU:    {primary_item['sku']:<30}║")
            if profit is not None:
                pfx = "+" if profit >= 0 else ""
                print(f"  ║  Profit: {pfx}${profit:.2f}{' ':>28}║")
            print(f"  ╚══════════════════════════════════════════╝")
            normal_detail = {
                "amazon_id": amazon_id,
                "asin": asin_field,
                "sku": sku_field,
                "qty": qty_field,
                "buyer": buyer,
                "city": city,
                "state": state,
                "zip": zipcode,
                "earnings": earnings or "",
                "zoro_price": logged_cost,
                "profit": profit if profit is not None else "",
                "status": "SUCCESS",
                "wb_id": result_id,
                "notes": order_note,
            }
            _log_order(normal_detail)
            _upsert_unified_db(amazon_id, normal_detail,
                amazon_fees=amazon_fees,
                shipping_amt=shipping_amt,
                purchase_date=pd,
                ship_by_date=sb,
                deliver_by_date=db)
        # Write seller note (driver already on Amazon page from update_amazon_seller_id)
        try:
            from utils.seller_notes import format_seller_note, enrich_note_basis
            primary_sku = primary_item["sku"] if not is_multi_item else sku_field
            note_data = {
                "title": primary_item.get("title", ""),
                "asin": asin_field,
                "sku": note_amazon_sku,
                "code": (primary_sku.replace("G", "K", 1) if primary_sku.startswith("G") else primary_sku) + (" - ALT" if alt_used_any else ""),
                "qty": qty_field,
                "tracking_sent": "No",
                "tc": logged_cost,
                "tax": logged_tax,
                "earnings": earnings,
                "pm": profit,
                "pp": (margin * 100) if margin is not None else None,
                "coupon": bool(last_checkout_result.get("used_coupon_code")) if last_checkout_result else False,
                "coupon_code": (last_checkout_result.get("used_coupon_code") or "") if last_checkout_result else "",
                "coupon_off": logged_discount,
            }
            enrich_note_basis(
                note_data, profile_name=PROFILE_NAME,
                zoro_select=bool(IS_RESELLER_PROFILE and all(
                    it.get("is_zoro_select") for it in resolved_items)),
                earnings=earnings, amazon_fees=amazon_fees,
                shipping_amt=shipping_amt, total_cost=logged_cost)
            _note_text = format_seller_note(note_data)
            if defer_amazon_writes:
                _rush_defer_note(amazon_id, _note_text)
            else:
                write_seller_note(driver, amazon_id, note_text=_note_text,
                                  skip_navigate=True)
        except Exception as e:
            print(f"   [SellerNote] ⚠️  Could not write note: {e}")
        # Delete used regular coupon from Google Sheet so colleagues don't reuse it.
        # Priority coupons are reusable and stay in the sheet.
        used_coupon = last_checkout_result.get("used_coupon_code") if last_checkout_result else None
        _all_priority_codes = {c["code"] for c in (priority_coupons_with_pct or [])} | set(priority_codes_for_checkout or [])
        if used_coupon and used_coupon != "AUTO_APPLIED" and used_coupon not in _all_priority_codes:
            _delete_coupon_from_sheet(used_coupon)
        if used_coupon and IS_COUPON_PROFILE:
            _learn_promo_outcome(primary_item.get("sku"), primary_item.get("asin"), True,
                                 cache=asin_coupon_cache)
        _CART_FAILS.pop(amazon_id, None)
        return "success"
    
    elif result_status == "LOW_PROFIT":
        # Rejected BEFORE payment — release the claim (the Amazon "Loss" label prevents re-entry).
        if _HAS_LOCK and not DRY_RUN:
            plock.release(amazon_id, "low profit — no money spent")
        update_amazon_seller_id(driver, amazon_id, "Loss-Kamran checking", PROFILE_NAME)
        status_note = f"Low profit at checkout ({margin:.1%} < {MIN_MARGIN:.0%})"
        print(f"  ❌ {status_note}")
        _log_order({
            "amazon_id": amazon_id,
            "asin": asin_field,
            "sku": sku_field,
            "qty": qty_field,
            "buyer": buyer,
            "city": city,
            "state": state,
            "earnings": earnings or "",
            "zoro_price": logged_cost,
            "profit": profit if profit is not None else "",
            "status": "LOW_PROFIT",
            "notes": "; ".join(filter(None, [status_note, order_note])),
        })
        # Write the loss details to the Amazon seller note so the loss is visible
        # at a glance (cost, earnings, profit/loss, margin) without digging logs.
        try:
            from utils.seller_notes import format_seller_note, enrich_note_basis
            note_data = {
                "title": primary_item.get("title", ""),
                "asin": asin_field,
                "sku": note_amazon_sku,
                "code": (sku_field.replace("G", "K", 1) if sku_field.startswith("G") else sku_field) + (" - ALT" if alt_used_any else ""),
                "qty": qty_field,
                "tracking_sent": "No",
                "tc": logged_cost,
                "tax": logged_tax,
                "earnings": earnings,
                "pm": profit,
                "pp": (margin * 100) if margin is not None else None,
            }
            enrich_note_basis(
                note_data, profile_name=PROFILE_NAME,
                zoro_select=bool(IS_RESELLER_PROFILE and all(
                    it.get("is_zoro_select") for it in resolved_items)),
                earnings=earnings, amazon_fees=amazon_fees,
                shipping_amt=shipping_amt, total_cost=logged_cost)
            write_seller_note(driver, amazon_id, note_text=format_seller_note(note_data),
                              skip_navigate=True)
        except Exception as e:
            print(f"   [SellerNote] ⚠️  Could not write note: {e}")
        return "low_profit"

    elif result_status == "OOS":
        update_amazon_seller_id(driver, amazon_id, "OOS", PROFILE_NAME)
        status_note = "Out of stock at checkout"
    elif result_status == "STATE_RESTRICTED":
        update_amazon_seller_id(driver, amazon_id, "SR", PROFILE_NAME)
        status_note = f"State restricted ({state})"
    elif result_status == "PO_BOX_RESTRICTED":
        update_amazon_seller_id(driver, amazon_id, "POBOX", PROFILE_NAME)
        status_note = f"PO Box not allowed by Zoro ({addr.get('city', '')}, {state})"
    elif result_status == "NET30_RESTRICTED":
        update_amazon_seller_id(driver, amazon_id, "Net30", PROFILE_NAME)
        status_note = "Net30 restricted"
    elif result_status in ("NO_COUPONS_SKIP", "COUPON FAIL", "COUPON_RETRY"):
        # Coupon failed — leave the order untouched so it retries next cycle.
        # Do NOT relabel as "MR Profit Margin" or "Rajar" — that blocks retries.
        status_note = (f"Coupon API / session trouble on every priority coupon — retry next cycle"
                       if result_status == "COUPON_RETRY" else f"No coupon worked — skipping (will retry next cycle)")
        if result_status == "COUPON FAIL" and IS_COUPON_PROFILE and not skip_coupons:
            # one strike towards promo-ineligible (strikes ≥ 10 min apart; at
            # routing.promo_ineligible_min_fails the ASIN routes to the reseller)
            _learn_promo_outcome(primary_item.get("sku"), primary_item.get("asin"), False,
                                 cache=asin_coupon_cache)
    else:
        status_note = f"Placement failed: {result_status}"
    
    print(f"  ❌ {status_note}")

    # ── Reconcile the local claim on failure ──────────────────────
    # These statuses are all detected BEFORE Zoro payment, so it is safe to release the
    # claim and let the order retry next cycle (matches the pre-existing retry intent).
    _SAFE_PREPAYMENT = ("OOS", "STATE_RESTRICTED", "PO_BOX_RESTRICTED", "NET30_RESTRICTED",
                        "NO_COUPONS_SKIP", "COUPON FAIL", "COUPON_RETRY")
    if _HAS_LOCK and not DRY_RUN:
        if result_status in _SAFE_PREPAYMENT:
            plock.release(amazon_id, f"pre-payment failure: {result_status}")
        else:
            # Unknown / post-submit failure — money MAY have been spent (Zoro may have committed
            # the order but returned no WB). Before locking, run the STANDARD duplicate check:
            # if an order to this address/buyer already exists, mark it "<WB> - D" (as usual) and
            # treat it as resolved/placed instead of locking. Only genuinely-unresolved failures
            # get locked (FAILED_KEPT) for one-click review in the launcher's "Locked Orders" tab.
            existing_wb = None
            if duplicate_services and manual_override:
                # A Manual-C/-R order is a KNOWN repeat customer — an earlier order to this
                # address exists by definition, so this recheck would always "resolve" the
                # failure against the customer's PREVIOUS order, stamp "<old WB> - D" and
                # confirm the claim with the wrong WB. That would silently record this
                # order as placed when it was not. Skip it and lock for human review.
                print(f"   [dup-recheck] skipped — {amazon_id} is Manual-{_manual_kind}; "
                      f"an earlier order to this address is expected and would be a false match")
            elif duplicate_services:
                try:
                    existing_wb = check_if_address_already_ordered_gmail(
                        duplicate_services, addr["street_address"],
                        zip_code=addr.get("zip_code", ""), customer_name=buyer)
                except Exception as _dup_err:
                    print(f"   [dup-recheck] error: {_dup_err}")
                    existing_wb = None
            # Only AUTO-RESOLVE on a real WB/WW order id. A bare "Duplicate" (name+zip match with
            # no extractable id) or DUP_CHECK_FAILED is NOT trustworthy enough to silently
            # permanent-block a possible-spend — those fall through to keep_failed() so they show
            # in the Locked Orders tab for a human to verify against Zoro.
            _real_wb = bool(existing_wb) and existing_wb not in (DUP_CHECK_FAILED, "Duplicate") \
                and (existing_wb.startswith("WB") or existing_wb.startswith("WW"))
            if _real_wb:
                dup_label = _fit_seller_label(existing_wb, " - D")
                print(f"  🔁 Failed checkout resolves to an existing order — marking {dup_label}")
                update_amazon_seller_id(driver, amazon_id, dup_label, PROFILE_NAME, skip_navigate=True)
                plock.confirm(amazon_id, existing_wb,
                              note=f"resolved as duplicate ('{existing_wb} - D') after '{result_status}' failure")
                _log_order({
                    "amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                    "buyer": buyer, "city": city, "state": state,
                    "status": "DUPLICATE", "wb_id": existing_wb,
                    "notes": "; ".join(filter(None, [
                        f"resolved as duplicate after checkout failure '{result_status}'", order_note])),
                })
                return "duplicate"
            # Not a duplicate (or dup-check couldn't complete) — lock for manual review.
            plock.keep_failed(amazon_id, f"unknown failure '{result_status}' — verify on Zoro before re-order")
            print(f"  🛑 {amazon_id} locked as FAILED_KEPT — will NOT auto-retry. "
                  f"Review it in the launcher's 'Locked Orders' tab and Unlock if safe.")

    _log_order({
        "amazon_id": amazon_id,
        "asin": asin_field,
        "sku": sku_field,
        "qty": qty_field,
        "buyer": buyer,
        "city": city,
        "state": state,
        "earnings": earnings or "",
        "zoro_price": logged_cost,
        "profit": profit if profit is not None else "",
        "status": result_status or "FAILED",
        "notes": "; ".join(filter(None, [status_note, order_note])),
    })
    return "failed"


# ══════════════════════════════════════════════════════════════════
#  COUPON SHEET LOADER
# ══════════════════════════════════════════════════════════════════

def load_coupon_codes():
    """Load coupon codes with percentage/fixed-amount info from Google Sheets.

    The Coupons tab has two columns:
      A: Coupon code
      B: Percentage Off OR Fixed Amount (optional — coupons WITH a value are priority coupons)

    Column B formats:
      - "20"          → 20% off  (percentage coupon)
      - "10:50"       → $10 off $50+ (fixed-amount coupon)
      - "FIXED:10:50" → $10 off $50+ (fixed-amount coupon, explicit)

    Returns (priority_coupons_with_pct, regular_coupons):
      priority_coupons_with_pct = [
          {"code": "DRILL526", "pct": 15},
          {"code": "JANITORIAL626", "fixed_amount": 10, "min_order": 50},
          ...
      ]
      regular_coupons = ["EMZMXTFY8QR9", ...]
    """
    if not IS_COUPON_PROFILE:
        return [], []
    print("\n🏷️  Loading coupon sheet...")
    metadata = None
    try:
        from utils.helpers import load_coupon_metadata
        metadata = load_coupon_metadata()
    except Exception:
        metadata = {}

    try:
        svc = _get_sheets_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{GOOGLE_SHEET_COUPON_TAB}'!A:B",
        ).execute()
        rows = result.get("values", [])
        if len(rows) < 2:
            print("  ⚠️  Coupon sheet came back EMPTY — treated as a failed load (this profile never places without coupons)")
            return None

        priority = []
        regular = []
        for row in rows[1:]:
            if not row or not row[0].strip():
                continue
            raw_code = row[0].strip()
            val_str = row[1].strip() if len(row) > 1 else ""

            code = raw_code
            coupon_info = None

            # Try parsing fixed-amount format: "10:50" or "FIXED:10:50"
            fixed_match = re.match(r'^(?:FIXED:)?(\d+):(\d+)$', val_str)
            if fixed_match:
                fixed_amt = float(fixed_match.group(1))
                min_order = float(fixed_match.group(2))
                coupon_info = {"code": code, "fixed_amount": fixed_amt, "min_order": min_order}
                print(f"   [DEBUG] Fixed-amount coupon: {code} = ${fixed_amt:.0f} off ${min_order:.0f}+")
            else:
                # Percentage from column B — tolerant of "15%", "15 %", "15% off"
                try:
                    pct = float(val_str)
                except (ValueError, TypeError):
                    _digits = re.sub(r"[^0-9.]", "", val_str or "")
                    try:
                        pct = float(_digits) if _digits else 0
                    except ValueError:
                        pct = 0
                    if val_str and not pct:
                        print(f"  ⚠️  Coupon {code}: column B '{val_str}' is not a number — treated as a REGULAR "
                              f"(single-use) code; fix the sheet if it is a priority coupon")

                if pct > 0:
                    coupon_info = {"code": code, "pct": pct}

            if coupon_info:
                # Priority coupon (has percentage or fixed amount)
                # Skip if globally marked INVALID (e.g. expired) — no point retrying
                if metadata.get(code) == "INVALID":
                    print(f"   [DEBUG] Skipping INVALID priority coupon: {code}")
                    continue
                # They are reusable; per-zoro-code skipping is handled by
                # _apply_coupons() via failed_pairs / working_pairs.
                priority.append(coupon_info)
            else:
                # Regular coupons — skip if already known used or invalid
                if metadata.get(code) in ("USED", "INVALID"):
                    continue
                regular.append(code)

        # Also include legacy priority coupons from bot_config.json as fallback
        legacy_priority_codes = {c["code"] for c in priority}
        for lc in PRIORITY_COUPONS:
            if lc and lc not in legacy_priority_codes and metadata.get(lc) != "USED":
                priority.append({"code": lc, "pct": 10})  # assume 10% for legacy

        # one entry per (code, value); a priority code is never ALSO a regular one
        _seen, _pri = set(), []
        for c in priority:
            k = (c["code"], c.get("pct"), c.get("fixed_amount"), c.get("min_order"))
            if k not in _seen:
                _seen.add(k)
                _pri.append(c)
        priority = _pri
        _pcodes = {c["code"] for c in priority}
        regular = [c for i, c in enumerate(regular) if c not in _pcodes and c not in regular[:i]]
        # BEST coupon first: checkout and supervisor take the first priority code that applies
        priority.sort(key=lambda c: (0, -float(c["pct"])) if "pct" in c else (1, -float(c.get("fixed_amount") or 0)))
        print(f"  ✅ Loaded {len(priority)} priority coupons + {len(regular)} regular coupons")
        if priority:
            summaries = []
            for c in priority[:6]:
                if "pct" in c:
                    summaries.append(f"{c['code']}({c['pct']:.0f}%)")
                else:
                    summaries.append(f"{c['code']}(${c['fixed_amount']:.0f} off)")
            pct_summary = ", ".join(summaries)
            if len(priority) > 6:
                pct_summary += " ..."
            print(f"  🏷️  Priority: {pct_summary}")
        # Debug: show first few raw rows to diagnose empty-column issues
        print(f"   [DEBUG] First 5 sheet rows: {rows[1:6]}")
        return priority, regular
    except Exception as e:
        print(f"  ❌ CRITICAL: Sheets API failed ({e}) — NO CSV FALLBACK. Stopping cycle.")
        return None


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    global _active_driver, _active_chrome_process

    _acquire_lock()

    print(f"\n{'═'*60}")
    print(f"  ORDER PIPELINE — {PROFILE_NAME}")
    print(f"  Label: {TARGET_LABEL}  |  Port: {PORT}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if DRY_RUN:
        print(f"  ⚠️  DRY RUN MODE — no orders will be placed")
    if SINGLE_CYCLE:
        print(f"  ⚠️  SINGLE CYCLE — will exit after one pass")
    print(f"{'═'*60}\n")
    
    _init_csv()
    
    # ── 1. Connect ────────────────────────────────────────────────
    driver = connect_chrome()
    _active_driver = driver

    # Replay any WB/notes journaled by a previous run that died before its
    # flush — those placements are already locked against re-buy; this just
    # completes the Amazon paper trail.
    if _rush_journal_load():
        driver = _rush_flush_amazon_writes(driver)

    # ── 2. Gmail auth ─────────────────────────────────────────────
    print("\n🔐 Authenticating Gmail...")
    earnings_service = authenticate_gmail_account(EARNINGS_GMAIL)
    
    duplicate_services = []
    for acct in DUPLICATE_GMAIL_LIST:
        svc = authenticate_gmail_account(acct)
        if svc:
            duplicate_services.append(svc)

    # ── Hard local dedup lock — seed from history BEFORE any placement ──
    # Fail-closed: if the lock cannot initialize we refuse to run rather than
    # risk re-buying orders with no duplicate protection.
    if not DRY_RUN:
        if not _HAS_LOCK:
            print("🛑 placement_lock unavailable — refusing to run the placer without dedup protection.")
            return
        if not plock.init(seed_log=True):
            print("🛑 placement_lock failed to initialize (DB unreachable) — refusing to run.")
            return

    # ── 3. Persistent coupon intelligence cache ────────────────────
    # Loaded from data/dataset/coupon_intel.json so learned coupon failures
    # persist across RUNS and across profiles (no more re-trying a coupon a
    # SKU's category rejected yesterday). Session-only sets (no_coupon_codes,
    # priority_exhausted) stay in-memory because the coupon pool changes.
    asin_coupon_cache = _load_coupon_intel()

    # ── 4. Main loop ──────────────────────────────────────────────
    while True:
        # Safety check — restart if browser closed
        try:
            driver.title
        except (WebDriverException, AttributeError):
            print("⚠️ Browser appears closed. Restarting...")
            driver = connect_chrome()
            _active_driver = driver

        processed_count = success_count = failed_count = low_profit_count = 0
        total_orders = 0

        _CYCLE_STARTED_AT[0] = time.time()

        print(f"\n⚙️  STARTING CYCLE: {time.strftime('%H:%M:%S')}")

        # STALE-CODE GUARD: a running process keeps the code it started with, so a fix
        # on disk does nothing until relaunch. That exact gap let a Deciding order place
        # un-routed on 2026-08-13 (order 114-7096863-0441815: fix landed 01:12, process
        # had started 01:01, kept running old code all night). Shout every cycle so a
        # pending restart can't be forgotten again.
        try:
            if os.path.getmtime(os.path.abspath(__file__)) > _CODE_MTIME_AT_START:
                print("  " + "!" * 62)
                print("  !!  CODE ON DISK IS NEWER THAN THIS RUNNING PROCESS.")
                print("  !!  This placer is running STALE code — fixes on disk are")
                print("  !!  NOT active. Stop and relaunch it from the launcher.")
                print("  " + "!" * 62)
        except OSError:
            pass

        # Honour any orders unlocked via the launcher / unlock_order.py since startup, so a
        # manual Unlock takes effect on the NEXT cycle without restarting the placer.
        # Also heal any orphaned PLACING claims (reserved but never reconciled) so they surface
        # in the Locked Orders tab instead of silently blocking their order forever.
        if _HAS_LOCK and not DRY_RUN:
            plock.refresh_releases()
            plock.reconcile_orphans()

        try:
            # ── Load data sheets ──────────────────────────────────
            sku_map = load_sku_map()
            if not sku_map:
                print("❌ No SKU mappings loaded — retrying in 10s")
                if SINGLE_CYCLE:
                    return
                time.sleep(10)
                continue

            coupon_codes = load_coupon_codes()
            if coupon_codes is None:
                print("❌ CRITICAL: Coupon sheet failed to load — stopping cycle.")
                if SINGLE_CYCLE:
                    return
                time.sleep(60)
                continue
            _watch_priority_coupon_set(coupon_codes)     # new weekly coupon → re-check queue

            # ── Load Priority ASINs from Google Sheets ─────────
            # Only source of truth — no local fallback.
            _priority_load_failed = False
            PRIORITY_ASINS.clear()
            _prev_deciding = list(DECIDING_ASINS)   # survives a failed sheet load below
            DECIDING_ASINS.clear()
            if LABELS_ONLY:
                print("  🏷️  Labels-only mode: Priority-ASIN & Deciding discovery OFF "
                      "— processing labelled orders only")
            else:
                sheet_priority_asins = load_priority_asins_from_sheet(PROFILE_NAME)
                if sheet_priority_asins is None:
                    # Sheets API failed. An empty list here would make this profile's own
                    # ASINs look "sorted in another column" and drop their orders silently.
                    if _LAST_PRIORITY_ASINS:
                        print(f"  ⚠️  Priority column unreadable this cycle — keeping the last-known "
                              f"{len(_LAST_PRIORITY_ASINS)} ASIN(s)")
                        PRIORITY_ASINS.extend(_LAST_PRIORITY_ASINS)
                    else:
                        print("  ⚠️  Priority column unreadable and no last-known copy — discovery is OFF this cycle")
                        _priority_load_failed = True
                elif sheet_priority_asins:
                    PRIORITY_ASINS.extend(sheet_priority_asins)
                    _LAST_PRIORITY_ASINS[:] = list(sheet_priority_asins)
                else:
                    print(f"  ℹ️  No priority ASINs from sheet")
                    _LAST_PRIORITY_ASINS[:] = []

                # ── Deciding ASINs (reseller profiles route these per-order) ──
                if PROFILE_NAME in _PROFILE_TO_DECIDING_COLUMN:
                    _new_deciding = load_deciding_asins_from_sheet(PROFILE_NAME)
                    if _new_deciding is None:
                        # Sheets API failed this cycle — keep the LAST-KNOWN list rather
                        # than running with none, which would place deciding orders
                        # un-routed at full price. (_prev_deciding captured pre-clear.)
                        DECIDING_ASINS.extend(_prev_deciding)
                        if _prev_deciding:
                            print(f"  ⚠️  Deciding sheet load failed — keeping last-known "
                                  f"{len(_prev_deciding)} ASIN(s)")
                    else:
                        DECIDING_ASINS.extend(_new_deciding)
                    if DECIDING_ASINS:
                        print(f"  🔀 Deciding ASINs (cart ≥ ${DECIDING_COUPON_MIN:.2f} → coupon): "
                              f"{', '.join(DECIDING_ASINS)}")

            # ── Promo-ineligible ASINs (config + sheet column + learned) ──────
            if not LABELS_ONLY:
                _refresh_promo_ineligible(sku_map, asin_coupon_cache)

            # ── Everything already sorted for this store (all its columns): the
            #    supervisor must only take ASINs that are in NO column. An ASIN a
            #    human put in _Reseller or _Deciding is that profile's business.
            if SUPERVISE_UNLABELLED:
                _sorted = load_store_sorted_asins(PROFILE_NAME)
                if _sorted is not None:
                    STORE_SORTED_ASINS.clear()
                    STORE_SORTED_ASINS.update(_sorted)
                elif not STORE_SORTED_ASINS:
                    print("  ⚠️  Sorted-ASIN list unavailable and no last-known copy — supervisor paused this cycle")

            # ── Priority-ASIN auto-check (coupon profiles, between cycles) ────
            _maybe_run_priority_check(driver, sku_map, coupon_codes, asin_coupon_cache)

            # ── Scrape orders from Amazon SC ──────────────────────
            if ASIN_SEARCH:
                print(f"\n🔍 Searching Amazon SC by ASIN: {ASIN_SEARCH}")
            else:
                print(f"\n🔍 Scraping '{TARGET_LABEL}' orders from Amazon Seller Central...")
            scraped_orders = scrape_order_ids(driver, label=TARGET_LABEL, asin_search=ASIN_SEARCH)
            for o in scraped_orders:
                o["_label_matched"] = TARGET_LABEL
                o["_margin_thresh"] = MIN_MARGIN
                
            # If a second target label is defined (e.g. Coupon profile), scrape it too
            if TARGET_LABEL_2 and not ASIN_SEARCH:
                print(f"\n🔍 Scraping secondary label '{TARGET_LABEL_2}' from Amazon Seller Central...")
                scraped_2 = scrape_order_ids(driver, label=TARGET_LABEL_2, asin_search=None)
                seen_primary = {o["amazon_id"] for o in scraped_orders}
                for o2 in scraped_2:
                    if o2["amazon_id"] not in seen_primary:
                        o2["_label_matched"] = TARGET_LABEL_2
                        o2["_margin_thresh"] = MIN_MARGIN_2
                        scraped_orders.append(o2)

            # ── Discover MANUAL-OVERRIDE orders ────────────────────────────────
            # Without this pass the Manual-C/-R tag is inert in normal runs: the main
            # scrape searches for TARGET_LABEL (MR / AB ECOM / RAJAR) so it never matches
            # "Manual-C", and favorites discovery skips every order that already carries a
            # seller-order-id — which is exactly the field the tag lives in. Search for
            # this profile's own tag the same way we search any other label.
            if not ASIN_SEARCH:
                _my_tag = "Manual-C" if PROFILE_NAME.upper().endswith("COUPON") else "Manual-R"
                print(f"\n🖐️  Scraping manual-override '{_my_tag}' orders…")
                try:
                    _manual_scraped = scrape_order_ids(driver, label=_my_tag, asin_search=None)
                except Exception as _me:
                    _manual_scraped = []
                    print(f"    ⚠️  manual-override scrape failed ({_me}) — skipping this cycle")
                _seen_so_far = {o["amazon_id"] for o in scraped_orders}
                _manual_added = 0
                for _mo in _manual_scraped:
                    # Re-verify the label ourselves — Amazon's search is fuzzy and may return
                    # near-matches. Only an exact tag for THIS profile is allowed through.
                    _k = _parse_manual_tag((_mo.get("seller_order_id") or "").strip())
                    if not _k or not _manual_tag_is_for_profile(_k, PROFILE_NAME):
                        continue
                    if _mo["amazon_id"] in _seen_so_far:
                        continue
                    _mo["_label_matched"] = _my_tag
                    _mo["_margin_thresh"] = MIN_MARGIN
                    scraped_orders.append(_mo)
                    _seen_so_far.add(_mo["amazon_id"])
                    _manual_added += 1
                if _manual_added:
                    print(f"    🖐️  {_manual_added} manual-override order(s) queued "
                          f"— duplicate search AND profit gate waived for these")
                else:
                    print(f"    (none)")

            # ── Discover priority-ASIN orders (pending/unlabelled) ──────────────
            if (PRIORITY_ASINS or DECIDING_ASINS or SUPERVISE_UNLABELLED) and not ASIN_SEARCH and not _priority_load_failed:
                seen_ids = {o["amazon_id"] for o in scraped_orders}
                priority_set = {pa.strip().upper() for pa in PRIORITY_ASINS if pa and pa.strip()}
                deciding_set = {da.strip().upper() for da in DECIDING_ASINS if da and da.strip()}

                if USE_FAVORITES_DISCOVERY:
                    # ONE scrape of the favorites (buyer-action) view, then keep only
                    # orders whose ASIN is in the Priority_ASIN_Control sheet and that
                    # aren't already labelled. Replaces 50+ per-ASIN search pages.
                    print(f"\n  ⭐ Favorites discovery: scanning buyer-action view, "
                          f"matching {len(priority_set)} priority + "
                          f"{len(deciding_set)} deciding ASIN(s)…")
                    fav_orders = scrape_order_ids(driver, favorites=True)
                    matched = skipped_label = skipped_asin = 0
                    deciding_matched = 0
                    needs_priority_flagged = 0   # unlabelled + non-priority → Needs Priority panel
                    supervised_added = 0         # unlabelled + mapped → supervisor queue (coupon profiles)
                    sorted_elsewhere = 0         # ASIN sits in another column of this store → not ours
                    _mapped_upper = {str(k).strip().upper() for k in (sku_map or {})}
                    _supervise_ok = SUPERVISE_UNLABELLED and bool(STORE_SORTED_ASINS)
                    for fo in fav_orders:
                        if fo["amazon_id"] in seen_ids:
                            continue
                        _fo_asin = (fo.get("asin") or "").strip().upper()
                        is_deciding = _fo_asin in deciding_set
                        if _fo_asin not in priority_set and not is_deciding:
                            skipped_asin += 1
                            if not (fo.get("seller_order_id") or "").strip():
                                if _fo_asin in STORE_SORTED_ASINS:
                                    sorted_elsewhere += 1       # Reseller/Deciding column: that profile's order
                                    continue
                                if (_supervise_ok and _fo_asin in _mapped_upper
                                        and supervised_added < SUPERVISE_MAX_PER_CYCLE):
                                    # SUPERVISOR: mapped but unlabelled and not in Priority ASIN
                                    # Control → queued LAST (after every labelled/priority order);
                                    # process_order decides coupon-vs-reseller on the live cart.
                                    # Unmapped ASINs are never queued (a human maps them first).
                                    fo["_supervised"] = True
                                    fo["_label_matched"] = "(unlabelled)"
                                    fo["_margin_thresh"] = MIN_MARGIN
                                    scraped_orders.append(fo)
                                    seen_ids.add(fo["amazon_id"])
                                    supervised_added += 1
                                    _log_order({
                                        "amazon_id": fo.get("amazon_id", ""),
                                        "asin": (fo.get("asin") or "").strip(),
                                        "buyer": fo.get("buyer", ""),
                                        "status": "SUPERVISED",
                                        "notes": "Unlabelled mapped ASIN not in Priority ASIN Control → supervisor queue",
                                    })
                                    continue
                            # REPORT ONLY (does NOT change placement): surface unlabelled orders
                            # whose ASIN isn't in Priority ASIN Control so the launcher's
                            # "Needs Priority" panel can list them (ASIN × N → orders → Seller
                            # Central). Only flag UNLABELLED ones — a labelled order is handled.
                            if not (fo.get("seller_order_id") or "").strip():
                                needs_priority_flagged += 1
                                _log_order({
                                    "amazon_id": fo.get("amazon_id", ""),
                                    "asin": (fo.get("asin") or "").strip(),
                                    "buyer": fo.get("buyer", ""),
                                    "status": "NEEDS_PRIORITY",
                                    "notes": "Unlabelled order; ASIN not in Priority ASIN Control",
                                })
                            continue                       # not a priority ASIN → leave it alone
                        if (fo.get("seller_order_id") or "").strip():
                            skipped_label += 1
                            continue                       # already labelled → skip
                        if is_deciding:
                            # Routed per-order later: cart ≥ threshold → relabelled
                            # for the coupon profile instead of placed here.
                            fo["_deciding"] = True
                            deciding_matched += 1
                        fo["_label_matched"] = TARGET_LABEL
                        fo["_margin_thresh"] = MIN_MARGIN
                        scraped_orders.append(fo)
                        seen_ids.add(fo["amazon_id"])
                        matched += 1
                    print(f"    ✅ {matched} priority order(s) added "
                          f"[{deciding_matched} deciding] "
                          f"+ {supervised_added} unlabelled → supervisor "
                          f"(skipped {skipped_asin} non-priority [{needs_priority_flagged} unlabelled → "
                          f"flagged Needs-Priority, {sorted_elsewhere} sorted in another column], "
                          f"{skipped_label} already labelled)")
                    if not fav_orders:
                        print("    ⚠️  Favorites view returned 0 orders — if this is wrong, the saved "
                              "view/URL may have changed; set use_favorites_discovery=False to fall back.")
                else:
                    # Legacy: one ASIN-search page per priority/deciding ASIN.
                    _legacy_asins = ([(pa, False) for pa in PRIORITY_ASINS]
                                     + [(da, True) for da in DECIDING_ASINS])
                    for pa, _is_deciding in _legacy_asins:
                        pa = pa.strip()
                        if not pa:
                            continue
                        print(f"  🔍 Also searching by ASIN: {pa}"
                              + (" (deciding)" if _is_deciding else ""))
                        asin_orders = scrape_order_ids(driver, label=TARGET_LABEL, asin_search=pa)
                        new_count = 0
                        for ao in asin_orders:
                            if ao["amazon_id"] not in seen_ids:
                                if _is_deciding:
                                    ao["_deciding"] = True
                                ao["_label_matched"] = TARGET_LABEL
                                ao["_margin_thresh"] = MIN_MARGIN
                                scraped_orders.append(ao)
                                seen_ids.add(ao["amazon_id"])
                                new_count += 1
                        if new_count:
                            print(f"    ✅ +{new_count} new orders for ASIN {pa}")

            if not scraped_orders:
                labels_str = f"'{TARGET_LABEL}' or '{TARGET_LABEL_2}'" if TARGET_LABEL_2 else f"'{TARGET_LABEL}'"
                print(f"  ℹ️  No orders found with label {labels_str}")
                _run_log["orders_scraped"] = 0
                _save_log()
                if SINGLE_CYCLE:
                    return
                print("   -> Waiting 10 seconds before next cycle...")
                time.sleep(10)
                continue

            # Filter blocked order IDs
            if BLOCKED_ORDER_IDS:
                before = len(scraped_orders)
                scraped_orders = [o for o in scraped_orders if o["amazon_id"] not in BLOCKED_ORDER_IDS]
                if len(scraped_orders) < before:
                    print(f"  ⛔ Filtered {before - len(scraped_orders)} blocked order(s)")

            scraped_orders, asin_counts = _sort_orders_for_processing(scraped_orders)
            if PRIORITY_ASINS:
                print(f"  ⭐ Priority ASINs pinned first: {', '.join(PRIORITY_ASINS)}")
            asin_batch_summary = _format_asin_batch_summary(scraped_orders, asin_counts)
            if asin_batch_summary:
                print(f"  🔢 ASIN batch order: {asin_batch_summary}")

            _run_log["orders_scraped"] = len(scraped_orders)
            total_orders = len(scraped_orders)
            id_list = [o["amazon_id"] for o in scraped_orders[:10]]
            print(f"\n📋 Found {total_orders} '{TARGET_LABEL}' order(s)")
            print(f"   IDs: {', '.join(id_list)}{'...' if total_orders > 10 else ''}")

            # ── Process orders ────────────────────────────────────
            print(f"\n{'═'*60}")
            print(f"  PROCESSING ORDERS (scrape + place)")
            print(f"{'═'*60}")

            consecutive_api_fails = 0
            cycle_order_count = 0
            dup_blocked_count = 0
            restriction_cooldown_done = False

            # Hard safety ceiling on placements per cycle — applies even when the configured
            # caps are 0 (unlimited), so a runaway loop can never rack up dozens of buys again.
            _cycle_cap = MAX_ORDERS_PER_CYCLE if MAX_ORDERS_PER_CYCLE > 0 else SAFETY_MAX_ORDERS_PER_CYCLE

            if RUSH_MODE:
                # ═══ RUSH MODE: phase-batched processing ═══════════════════
                # A) one tab per order — Amazon loads the whole batch's detail
                #    pages in PARALLEL, harvested with the classic extraction;
                # B) placements run back-to-back (Amazon write-backs journaled);
                # C) one flush per batch writes every WB + seller note.
                # Every gate (held-claim, dup check, margin, velocity caps) is
                # the same code as classic mode.
                _rq = list(scraped_orders)
                scraped_orders = []          # the classic loop below no-ops
                _rn = 0
                _rbi = 0
                _stop_cycle = False
                while _rbi < len(_rq) and not _stop_cycle:
                    if ORDER_LIMIT > 0 and success_count >= ORDER_LIMIT:
                        print(f"   -> ORDER_LIMIT ({ORDER_LIMIT}) reached. Stopping.")
                        break
                    if cycle_order_count >= _cycle_cap:
                        print(f"   -> 🛡️ VELOCITY CAP: {_cycle_cap} orders this cycle "
                              f"({'configured' if MAX_ORDERS_PER_CYCLE > 0 else 'safety default'}). Pausing.")
                        break
                    batch = _rq[_rbi:_rbi + RUSH_BATCH]
                    _rbi += RUSH_BATCH
                    print(f"\n⚡ RUSH BATCH ({len(batch)} orders) — Phase A: parallel Amazon scrape…")
                    try:
                        details_map = _rush_scrape_batch(driver, batch)
                    except Exception as e:
                        print(f"  ⚠️  Batch scrape error ({e}) — falling back to serial")
                        details_map = {}
                        if _is_stall_error(e):
                            driver = _recycle_browser("stalled in batch scrape")
                    print(f"⚡ Phase A done: {len(details_map)}/{len(batch)} scraped in "
                          f"parallel — Phase B: placing…")
                    # Batched Gmail duplicate check: one query covers the whole
                    # batch. Orders it can't confidently resolve simply run the
                    # classic per-order (fail-closed) check inside process_order.
                    dup_map = {}
                    if duplicate_services and details_map:
                        try:
                            from platforms.gmail import batch_check_addresses_gmail
                            _dspec = []
                            for _o in batch:
                                _d = details_map.get(_o["amazon_id"]) or {}
                                _a = _d.get("address") or {}
                                if _a.get("street_address"):
                                    _dspec.append({"key": _o["amazon_id"],
                                                   "name": _o.get("buyer", ""),
                                                   "address": _a.get("street_address", ""),
                                                   "zip": _a.get("zip_code", "")})
                            if _dspec:
                                dup_map = batch_check_addresses_gmail(duplicate_services, _dspec)
                                print(f"⚡ Batched dup check: {len(dup_map)}/{len(_dspec)} "
                                      f"resolved in one Gmail pass")
                        except Exception as _de:
                            print(f"  ⚠️  Batched dup check failed ({_de}) — per-order checks will run")
                            dup_map = {}
                    for order in batch:
                        if ORDER_LIMIT > 0 and success_count >= ORDER_LIMIT:
                            _stop_cycle = True
                            break
                        if cycle_order_count >= _cycle_cap:
                            _stop_cycle = True
                            break
                        _rn += 1
                        amazon_id = order["amazon_id"]
                        asin = order.get("asin", "")
                        sku = order.get("sku", "")
                        qty = order.get("qty", 1)
                        print_rich_summary(PROFILE_NAME, total_orders, processed_count,
                                           success_count, failed_count, low_profit_count,
                                           f"Processing {amazon_id}")
                        print(f"\n{'─'*60}")
                        print(f"  [{_rn:02d}/{len(_rq):02d}] Amazon ID: {amazon_id}")
                        if asin:
                            print(f"  List ASIN: {asin}  Qty: {qty}")
                        print(f"{'─'*60}")

                        detail = details_map.get(amazon_id)
                        if detail is None:
                            try:
                                detail = scrape_order_details(driver, amazon_id, fallback_qty=qty)
                            except Exception as e:
                                print(f"  ⚠️  Detail scrape exception: {e}")
                                _run_log["failed"] += 1
                                _log_order({
                                    "amazon_id": amazon_id, "asin": asin, "sku": sku,
                                    "status": "SCRAPE_FAIL",
                                    "notes": f"Detail scrape exception: {e}",
                                })
                                if _is_stall_error(e):
                                    driver = _recycle_browser(f"stalled scraping {amazon_id}")
                                continue
                        if not detail:
                            print(f"  ⚠️  Failed to scrape detail page")
                            _run_log["failed"] += 1
                            _log_order({
                                "amazon_id": amazon_id, "asin": asin, "sku": sku,
                                "status": "SCRAPE_FAIL",
                                "notes": "Could not load order detail page",
                            })
                            continue

                        detail_status = (detail.get("status") or "").strip().lower()
                        if detail_status in ("pending", "cancelled", "canceled", "shipped"):
                            print(f"  ⏭️  Order status is {detail_status.title()} — skipping")
                            _run_log["skipped"] += 1
                            continue
                        mid = detail.get("merchant_order_id", "").strip()
                        if mid.startswith("WB"):
                            print(f"  ⏭️  Already processed ({mid})")
                            _run_log["skipped"] += 1
                            continue
                        if mid and mid.upper() in ("OOS", "DISCONTINUED", "DUPLICATE", "SR", "NET30"):
                            print(f"  ⏭️  Already marked: {mid}")
                            _run_log["skipped"] += 1
                            continue
                        if mid and mid.upper().startswith("LOSS"):
                            print(f"  ⏭️  Already marked: {mid}")
                            _run_log["skipped"] += 1
                            continue

                        detail_items = detail.get("items", [])
                        if detail_items and len(detail_items) == 1:
                            if sku:
                                detail_items[0]["seller_sku"] = sku
                            if qty > 1:
                                detail_qty = _coerce_positive_int(detail_items[0].get("qty")) or 0
                                if detail_qty <= 1:
                                    detail_items[0]["qty"] = qty
                                    print(f"  ↪️  Qty backstop: using list-view qty {qty}")
                        if not detail_items and asin:
                            detail["items"] = [{"asin": asin, "qty": qty, "title": "", "seller_sku": sku}]
                        if order.get("_deciding"):
                            detail["_deciding"] = True
                        if order.get("_supervised"):
                            detail["_supervised"] = True

                        try:
                            status = process_order(
                                driver, amazon_id, detail, sku_map,
                                earnings_service, duplicate_services,
                                coupon_codes=coupon_codes,
                                asin_coupon_cache=asin_coupon_cache,
                                defer_amazon_writes=True,
                                dup_prefetch=dup_map,
                            )
                            _run_log["orders_processed"] += 1
                            if (order.get("_supervised") or order.get("_deciding")) and not (
                                    status == "skipped" and str(detail.get("merchant_order_id") or "").strip().upper().startswith("WB")):
                                _DLOG.log("outcome", profile=PROFILE_NAME, amazon_id=amazon_id, asin=asin, sku=sku,
                                          action=status, reason="checkout result (rush loop)",
                                          supervised=bool(order.get("_supervised")), deciding=bool(order.get("_deciding")))
                            if status == "success":
                                success_count += 1
                                consecutive_api_fails = 0
                            elif status in ("failed",):
                                failed_count += 1
                                consecutive_api_fails = 0
                            elif status == "low_profit":
                                low_profit_count += 1
                                consecutive_api_fails = 0
                            elif status == "api_fail":
                                failed_count += 1
                                consecutive_api_fails += 1
                                if consecutive_api_fails >= 3:
                                    print(f"   🛑 {consecutive_api_fails} consecutive API failures — stopping cycle")
                                    _stop_cycle = True
                            elif status in ("skipped", "pending", "duplicate"):
                                consecutive_api_fails = 0
                                if status == "duplicate":
                                    dup_blocked_count += 1
                                    if dup_blocked_count in (5, 10) or dup_blocked_count % 25 == 0:
                                        print(f"   🚨 CIRCUIT BREAKER: local lock has blocked "
                                              f"{dup_blocked_count} duplicate re-buy attempts this cycle — "
                                              f"investigate why already-placed orders are being re-scraped.")
                        except ZoroAccountRestricted:
                            driver = _rush_flush_amazon_writes(driver)
                            if not restriction_cooldown_done:
                                restriction_cooldown_done = True
                                print(f"\n   🚨 ZORO RESTRICTED — quick resets failed")
                                print(f"   ⏳ Long cooldown: waiting {RESTRICTION_LONG_COOLDOWN//60} minutes...")
                                time.sleep(RESTRICTION_LONG_COOLDOWN)
                                try:
                                    if reset_restriction(driver):
                                        print("   ✅ Restriction cleared after cooldown — resuming...")
                                        continue
                                except Exception:
                                    pass
                                print("   ❌ Still restricted after long cooldown — stopping cycle")
                            else:
                                print(f"\n   🚨 ZORO STILL RESTRICTED — stopping cycle")
                            _stop_cycle = True
                            break
                        except Exception as e:
                            print(f"   -> Error: {e}")
                            failed_count += 1
                            if _is_stall_error(e):
                                driver = _recycle_browser(f"stalled processing {amazon_id}")

                        processed_count += 1
                        cycle_order_count += 1
                        _save_log()

                    # Phase C: write this batch's WBs + notes back to Amazon
                    if _rush_pending:
                        print(f"⚡ Phase C: flushing {len(_rush_pending)} write-back(s) to Amazon…")
                        driver = _rush_flush_amazon_writes(driver)
                    if (processed_count > 0) and (processed_count % BROWSER_RECYCLE_INTERVAL == 0):
                        driver = _recycle_browser(f"proactive after {processed_count} orders")

            for i, order in enumerate(scraped_orders, 1):
                # Velocity / limit checks
                if ORDER_LIMIT > 0 and success_count >= ORDER_LIMIT:
                    print(f"   -> ORDER_LIMIT ({ORDER_LIMIT}) reached. Stopping.")
                    break
                if cycle_order_count >= _cycle_cap:
                    print(f"   -> 🛡️ VELOCITY CAP: {_cycle_cap} orders this cycle "
                          f"({'configured' if MAX_ORDERS_PER_CYCLE > 0 else 'safety default'}). Pausing.")
                    break
                if (processed_count > 0) and (processed_count % BROWSER_RECYCLE_INTERVAL == 0):
                    driver = _recycle_browser(f"proactive after {processed_count} orders")

                amazon_id = order["amazon_id"]
                asin = order.get("asin", "")
                sku = order.get("sku", "")
                qty = order.get("qty", 1)
                buyer = order.get("buyer", "")

                # Dashboard line
                label = f"Processing {amazon_id}"
                print_rich_summary(PROFILE_NAME, total_orders, processed_count,
                                   success_count, failed_count, low_profit_count, label)

                print(f"\n{'─'*60}")
                print(f"  [{i:02d}/{total_orders:02d}] Amazon ID: {amazon_id}")
                if asin:
                    print(f"  List ASIN: {asin}  Qty: {qty}")
                if buyer:
                    print(f"  Buyer    : {buyer}")
                print(f"{'─'*60}")

                # ── Scrape detail page ────────────────────────────
                try:
                    detail = scrape_order_details(driver, amazon_id, fallback_qty=qty)
                except NoSuchWindowException as e:
                    print(f"  ❌ Browser window closed during scrape: {e}")
                    try:
                        driver = connect_chrome()
                        _active_driver = driver
                        detail = scrape_order_details(driver, amazon_id, fallback_qty=qty)
                        print("  ↪️  Reconnected to Chrome and resumed")
                    except Exception as reconnect_err:
                        print(f"  ⚠️  Reconnect failed: {reconnect_err}")
                        _run_log["failed"] += 1
                        _log_order({
                            "amazon_id": amazon_id, "asin": asin, "sku": sku,
                            "status": "SCRAPE_FAIL",
                            "notes": f"Window closed + reconnect failed: {reconnect_err}",
                        })
                        continue
                except Exception as e:
                    print(f"  ⚠️  Detail scrape exception: {e}")
                    _run_log["failed"] += 1
                    _log_order({
                        "amazon_id": amazon_id, "asin": asin, "sku": sku,
                        "status": "SCRAPE_FAIL",
                        "notes": f"Detail scrape exception: {e}",
                    })
                    if _is_stall_error(e):
                        driver = _recycle_browser(f"stalled scraping {amazon_id}")
                    continue

                if not detail:
                    print(f"  ⚠️  Failed to scrape detail page")
                    _run_log["failed"] += 1
                    _log_order({
                        "amazon_id": amazon_id, "asin": asin, "sku": sku,
                        "status": "SCRAPE_FAIL",
                        "notes": "Could not load order detail page",
                    })
                    continue

                detail_status = (detail.get("status") or "").strip().lower()
                if detail_status in ("pending", "cancelled", "canceled", "shipped"):
                    print(f"  ⏭️  Order status is {detail_status.title()} — skipping")
                    _run_log["skipped"] += 1
                    continue

                mid = detail.get("merchant_order_id", "").strip()
                if mid.startswith("WB"):
                    print(f"  ⏭️  Already processed ({mid})")
                    _run_log["skipped"] += 1
                    continue
                if mid and mid.upper() in ("OOS", "DISCONTINUED", "DUPLICATE", "SR", "NET30"):
                    print(f"  ⏭️  Already marked: {mid}")
                    _run_log["skipped"] += 1
                    continue
                if mid and mid.upper().startswith("LOSS"):
                    print(f"  ⏭️  Already marked: {mid}")
                    _run_log["skipped"] += 1
                    continue

                # Merge list-view data with detail-page data
                detail_items = detail.get("items", [])
                if detail_items and len(detail_items) == 1:
                    if sku:
                        detail_items[0]["seller_sku"] = sku
                    if qty > 1:
                        detail_qty = _coerce_positive_int(detail_items[0].get("qty")) or 0
                        if detail_qty <= 1:
                            detail_items[0]["qty"] = qty
                            print(f"  ↪️  Qty backstop: using list-view qty {qty}")
                if not detail_items and asin:
                    detail["items"] = [{"asin": asin, "qty": qty, "title": "", "seller_sku": sku}]

                # Carry the discovery-time routing flag across the list→detail hop.
                # `detail` is a FRESH dict from scrape_order_details(), so flags stamped on
                # the list-scrape dict die here unless copied — which is exactly why the
                # Deciding→coupon routing never fired once (0 ROUTED_TO_COUPON in the entire
                # log history): process_order read order_detail["_deciding"] but the flag
                # only ever existed on the list dict. (Found via order 114-4191800-6819449,
                # an HS_Deciding ASIN placed by HS_RESELLER at $88.56 instead of routed.)
                if order.get("_deciding"):
                    detail["_deciding"] = True
                if order.get("_supervised"):
                    detail["_supervised"] = True

                # ── Place order ───────────────────────────────────
                try:
                    status = process_order(
                        driver, amazon_id, detail, sku_map,
                        earnings_service, duplicate_services,
                        coupon_codes=coupon_codes,
                        asin_coupon_cache=asin_coupon_cache,
                        defer_amazon_writes=RUSH_MODE,
                    )

                    _run_log["orders_processed"] += 1
                    if (order.get("_supervised") or order.get("_deciding")) and not (
                            status == "skipped" and str(detail.get("merchant_order_id") or "").strip().upper().startswith("WB")):
                        # (an already-placed order met again is not an outcome worth recording)
                        _DLOG.log("outcome", profile=PROFILE_NAME, amazon_id=amazon_id, asin=asin, sku=sku,
                                  action=status, reason="checkout result",
                                  supervised=bool(order.get("_supervised")), deciding=bool(order.get("_deciding")))
                    if status == "success":
                        success_count += 1
                        consecutive_api_fails = 0
                    elif status in ("failed",):
                        failed_count += 1
                        consecutive_api_fails = 0
                    elif status == "low_profit":
                        low_profit_count += 1
                        consecutive_api_fails = 0
                    elif status == "api_fail":
                        failed_count += 1
                        consecutive_api_fails += 1
                        if consecutive_api_fails >= 3:
                            print(f"   🛑 {consecutive_api_fails} consecutive API failures — stopping cycle")
                            break
                    elif status in ("skipped", "pending", "duplicate"):
                        consecutive_api_fails = 0
                        if status == "duplicate":
                            # Circuit breaker: the local lock is catching a re-buy attempt.
                            # A few are normal, but a burst means placed orders are being
                            # re-scraped as "needs placing" — the incident signature. Alert loudly.
                            dup_blocked_count += 1
                            if dup_blocked_count in (5, 10) or dup_blocked_count % 25 == 0:
                                print(f"   🚨 CIRCUIT BREAKER: local lock has blocked "
                                      f"{dup_blocked_count} duplicate re-buy attempts this cycle — "
                                      f"investigate why already-placed orders are being re-scraped.")

                except ZoroAccountRestricted:
                    if not restriction_cooldown_done:
                        restriction_cooldown_done = True
                        print(f"\n   🚨 ZORO RESTRICTED — quick resets failed")
                        print(f"   ⏳ Long cooldown: waiting {RESTRICTION_LONG_COOLDOWN//60} minutes...")
                        time.sleep(RESTRICTION_LONG_COOLDOWN)
                        try:
                            if reset_restriction(driver):
                                print("   ✅ Restriction cleared after cooldown — resuming...")
                                continue
                        except Exception:
                            pass
                        print("   ❌ Still restricted after long cooldown — stopping cycle")
                        break
                    else:
                        print(f"\n   🚨 ZORO STILL RESTRICTED — stopping cycle")
                        break

                except Exception as e:
                    print(f"   -> Error: {e}")
                    failed_count += 1
                    if _is_stall_error(e):
                        driver = _recycle_browser(f"stalled processing {amazon_id}")

                processed_count += 1
                cycle_order_count += 1

                if RUSH_MODE and _rush_pending and (cycle_order_count % RUSH_BATCH == 0):
                    driver = _rush_flush_amazon_writes(driver)

                _save_log()

        except Exception as e:
            print(f"❌ Loop Error: {e}")

        # Rush: whatever is still journaled at cycle end gets written back now
        # (partial batches, error exits). Failures stay journaled for next cycle.
        if _rush_pending:
            try:
                driver = _rush_flush_amazon_writes(driver)
            except Exception as e:
                print(f"  ⚠️  Rush flush error at cycle end: {e}")

        # ── Cycle summary ─────────────────────────────────────────
        _run_log["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_log()
        _save_coupon_intel(asin_coupon_cache)

        print_rich_summary(PROFILE_NAME, total_orders, processed_count,
                           success_count, failed_count, low_profit_count,
                           "Cycle Complete." + ("" if SINGLE_CYCLE else " Waiting 10 seconds..."))

        if SINGLE_CYCLE:
            print(f"\n  📋 Log: {_log_file}")
            return

        time.sleep(10)


if __name__ == "__main__":
    main()
