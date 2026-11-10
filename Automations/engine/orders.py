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
import html          # _notify_big_order_20 escapes the title for Telegram
import threading
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
    has_coupon_sibling, profile_payment_method,
)
from utils.accounts_config import store_for_profile as _store_for_profile, store_prefix as _store_prefix
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
    first_alt_in_stock,
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
    all_matched_wbs,
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
from engine.ultra_crew import UltraCrew, LoginWall
from platforms.auth import is_login_page, auto_login, on_account_switcher, choose_store_if_asked

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
CLI_FOCUS_COUPON = None
_skip_next = False
for _idx, _a in enumerate(sys.argv[1:]):
    if _skip_next:
        _skip_next = False
        continue
    if _a in ("--focus-coupon", "--focus-prio", "--focus-priority-coupon") and _idx + 2 < len(sys.argv):
        CLI_FOCUS_COUPON = sys.argv[_idx + 2].strip()
        _skip_next = True                      # the code itself is not a profile / ASIN argument
        continue
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
# PO boxes. Since 10 Nov 2026 (owner) a PO-box order IS placed, with the box renamed "#<number>"
# behind its street (platforms.amazon.rename_po_box; recorded in po_box_orders as "renamed"). This
# label is now only for what cannot be shipped at all: an address that is nothing but a box, or one
# Zoro still refuses after the rename. (12 Oct – 10 Nov 2026 every PO-box order got it and was not
# placed; before that the label was "POBOX" and an unlabelled PO-box order was re-scraped forever.)
PO_BOX_LABEL = "BB"
# Zoro's own delivery estimate this many days (or more) past Amazon's deliver-by: the buyer would get
# it late, so the order is NOT placed — it is labelled with this for the team (owner, 15 Oct 2026,
# after 114-6478697-4599444: Amazon deliver-by 16 Oct, Zoro "delivery on or before" 4 Nov). Measured
# against deliver-by, not ship-by: Zoro routinely delivers 1-3 days after ship-by and still on time.
# bot_config.json → "delivery_gap_block_days" (default 4; 0 turns the check off).
DELIVERY_GAP_LABEL = "Delivery time high"
MIN_MARGIN = CONFIG.get("min_profit_margin", -0.2)
MIN_MARGIN_2 = CONFIG.get("min_profit_margin_2", -0.2) if "COUPON" in PROFILE_NAME else MIN_MARGIN
# The launcher's per-profile "Order Limit" box writes profiles.<P>.order_limit into
# bot_config.json, and apply_gui_overrides copies it onto PROFILES[...]["order_limit"] — but
# nothing ever READ it: the placer used the module-level ORDER_LIMIT (hard-coded 0 = unlimited).
# So the limit silently did nothing. Proved on 17 Sep 2026: order_limit=1 placed 5 orders.
# 0 keeps the old meaning (no limit); a positive value wins over the global.
try:
    _profile_order_limit = int(CONFIG.get("order_limit") or 0)
except (TypeError, ValueError):
    _profile_order_limit = 0
if _profile_order_limit > 0:
    ORDER_LIMIT = _profile_order_limit
MARGIN_CHECK_EST_ONLY = CONFIG.get("margin_check_est_only", False)
EARNINGS_GMAIL = CONFIG["gmail_earnings_account"]
DUPLICATE_GMAIL_LIST = CONFIG["gmail_duplicates_list"]
PRIORITY_ASINS = CONFIG.get("priority_asins", [])
BLOCKED_ORDER_IDS = CONFIG.get("blocked_order_ids", [])
PRIORITY_COUPONS = get_priority_coupons(gui_cfg)

IS_COUPON_PROFILE = "COUPON" in PROFILE_NAME
IS_RESELLER_PROFILE = "RESELLER" in PROFILE_NAME
# The Amazon store this profile places for ('HOANK' / 'Hathoro' / 'Wrenchly') — from
# config/accounts.json. Before 6 Nov 2026 every non-DM profile was booked as HOANK.
STORE_ACCOUNT = _store_for_profile(PROFILE_NAME, default="HOANK")
try:
    from utils.whatsapp import set_alert_store as _wa_set_alert_store
    _wa_set_alert_store(STORE_ACCOUNT)   # which WhatsApp number sends this placer's notices
except Exception as _wa_err:
    print(f"  [WhatsApp] ⚠️ could not set the alert store: {_wa_err}")
# Reseller-only stores (Wrenchly) have no coupon profile: every hand-off to a sibling is off.
HAS_COUPON_SIBLING = has_coupon_sibling(PROFILE_NAME)
# Every unlabelled order whose ASIN is mapped is this profile's (Wrenchly, owner 6 Nov 2026):
# discovery treats the whole Verified_SKU_Dataset as the priority list instead of a sheet column.
PLACE_ALL_MAPPED = bool(CONFIG.get("place_all_mapped", False))
# Labels-only mode (launcher checkbox): process ONLY already-labelled orders.
# Priority-ASIN discovery and Deciding-ASIN routing are fully disabled. A Manual-C/-R tag is a label,
# with or without a Zoro code after it.
LABELS_ONLY = bool(CONFIG.get("labels_only", False))
# Ultra is rush with the Amazon work moved off the placing thread (engine/ultra_crew.py).
ULTRA_MODE = bool(CONFIG.get("ultra_mode", False))
RUSH_MODE = bool(CONFIG.get("rush_mode", False)) or ULTRA_MODE
RUSH_BATCH = max(1, int(CONFIG.get("rush_batch", 10) or 10))
# Deciding-ASIN routing: refreshed from the sheet each cycle (reseller profiles
# only). An order for one of these ASINs is routed per-order by Zoro cart total:
# ≥ threshold → labelled for the sibling coupon profile; below → placed here.
DECIDING_ASINS = []
DECIDING_COUPON_MIN = float(
    CONFIG.get("deciding_coupon_min", DECIDING_COUPON_MIN_DEFAULT)
    or DECIDING_COUPON_MIN_DEFAULT)
# RESELLER payment: "CREDIT_CARD" or "NET30". HS_RESELLER is always NET30 (utils.config.NET30_PROFILES).
PAYMENT_METHOD = profile_payment_method(PROFILE_NAME, CONFIG.get("payment_method"))

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
print(f"   -> Store: {STORE_ACCOUNT}" + ("" if HAS_COUPON_SIBLING else " (reseller only — no coupon profile, nothing is routed)"))
if IS_RESELLER_PROFILE and HAS_COUPON_SIBLING:
    print(f"   -> Deciding rule: cart ≥ threshold OR reseller margin < {DECIDING_MIN_MARGIN:.0%} → coupon profile")
if PLACE_ALL_MAPPED:
    print(f"   -> Discovery: EVERY unlabelled order whose ASIN is in {GOOGLE_SHEET_SKU_TAB} (no Priority column)")
print(f"   -> Min Margin: {MIN_MARGIN:.0%} ({MIN_MARGIN})")
if ORDER_LIMIT > 0:
    print(f"   -> 🔒 Order Limit: {ORDER_LIMIT} successful order(s) this run, then stop")
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
_ultra_crew = None
_lockfile_path = os.path.join(SCRIPT_DIR, f".script_{PROFILE_NAME}.lock")


def _cleanup():
    global _active_driver, _active_chrome_process
    print("\n🧹 [CLEANUP] Shutting down...")
    try:
        if _ultra_crew:
            _ultra_crew.kill()
    except Exception:
        pass
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
        # Only OUR lock: 12 Oct 2026, an instance that refused to start ("Another instance is
        # already running") deleted the running one's lock on its way out, so the next start
        # ran as a second HS_COUPON on the same Chrome — both fighting over one browser.
        if os.path.exists(_lockfile_path):
            with open(_lockfile_path) as _lf:
                _owner = _lf.read().strip()
            if _owner == str(os.getpid()):
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
        account = STORE_ACCOUNT
        # Multi-item orders carry qty as a comma list ("1, 1") — int() on that
        # raised ValueError and silently dropped the whole unified-DB write
        # (every multi-item order was missing from the dashboard). Sum the
        # per-item quantities instead; a plain int is unchanged.
        qty_total = sum(int(n) for n in re.findall(r"\d+", str(detail.get("qty", 0) or 0)))
        # detail["qty"] is what we ordered on ZORO (a 25-pack listing buys 25 boxes). The
        # Amazon quantity — units the buyer bought — travels in amazon_qty. Writing the Zoro
        # count into `quantity` made every per-unit price downstream 25x too small (the
        # "$36.99 was $1.48, +2399%" alert of 21 Sep 2026).
        _amz_units = extra.pop("amazon_qty", None)
        try:
            amazon_units = int(_amz_units) if _amz_units not in (None, "") else qty_total
        except (TypeError, ValueError):
            amazon_units = qty_total
        data = {
            "amazon_order_id": amazon_id,
            "merchant_order_id": detail.get("wb_id", ""),
            "wb_order_id": detail.get("wb_id", ""),
            "account": account,
            "asin": detail.get("asin", ""),
            "sku": detail.get("sku", ""),
            "quantity": amazon_units,
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
        # Raw Zoro price facts live on order_placer_orders only. insert_order builds its
        # column list from data.keys(), so an unknown key would fail the orders-table write.
        _placer_only = {k: extra.pop(k, None) for k in ("zoro_list_price", "zoro_subtotal", "zoro_shipping", "zoro_deliver_by")}
        # Merge extra fields (amazon_fees, shipping_amt, coupon, dates, etc.)
        for key, val in extra.items():
            if val is not None and val != "":
                data[key] = val

        result = insert_order(data)
        if not result:
            # Already exists — try to enrich
            updates = {k: v for k, v in data.items() if k not in ("amazon_order_id", "account", "source")}
            update_order(amazon_id, updates, changed_by="orders_engine")

        try:
            from utils.unified_db import upsert_placer_order
            placer_data = {
                "amazon_order_id": amazon_id,
                "wb_order_id": data.get("wb_order_id", ""),
                "account": account,
                "asin": data.get("asin", ""),
                "sku": data.get("sku", ""),
                "quantity": data.get("quantity", 0),
                "earnings": data.get("earnings"),
                "amazon_fees": data.get("amazon_fees"),
                "shipping_amt": data.get("shipping_amt"),
                "zoro_order_total": data.get("zoro_order_total"),
                "zoro_tax": data.get("zoro_tax"),
                "zoro_list_price": _placer_only.get("zoro_list_price"),
                "zoro_subtotal": _placer_only.get("zoro_subtotal"),
                "zoro_shipping": _placer_only.get("zoro_shipping"),
                "zoro_deliver_by": _placer_only.get("zoro_deliver_by"),
                "profit_dollars": data.get("profit_dollars"),
                "coupon_used": data.get("coupon_used", ""),
                "coupon_savings": data.get("coupon_savings", 0.0),
                "ship_city": data.get("ship_city", ""),
                "ship_state": data.get("ship_state", ""),
                "ship_postal_code": data.get("ship_postal_code", ""),
                "purchase_date": data.get("purchase_date", ""),
                "ship_by_date": data.get("ship_by_date", ""),
                "deliver_by_date": data.get("deliver_by_date", ""),
                "zoro_qty": data.get("zoro_qty", 0),
            }
            upsert_placer_order(placer_data)
        except Exception:
            pass
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


def _first_line(e):
    """An error in one line. A Selenium error carries a 17-line chromedriver stack that says nothing
    about the page; printed five times a cycle it buried the one line that mattered."""
    s = str(e).strip()
    return (s.splitlines()[0] if s else type(e).__name__)[:300]


SIGNIN_RETRY_S = 300                 # after a sign-in that did not get through
SIGNIN_RESET_RETRY_S = 1800          # when Amazon wants a new password: nothing changes until a person acts


def _signin_needed():
    """Seller Central still shows a sign-in page after the automatic sign-in: nothing in this cycle can
    work. Say why in ONE line, in this pane, then wait rather than try Amazon's sign-in every 40
    seconds, and end the cycle. No WhatsApp message: the pane is where the owner looks for this
    (owner, 10 Nov 2026 — an alert to the Orders group that nobody asked for was taken out again).

    10 Nov 2026: both Hathoro placers stood on Amazon's "Switch accounts" page after the account's
    password had been changed. Each cycle went on to the order filters and printed five stack
    traces; the cause was one line among a hundred."""
    try:
        from platforms import auth as _auth
        why = str(getattr(_auth, "last_problem", "") or "")
    except Exception:
        why = ""
    wait_s = SIGNIN_RESET_RETRY_S if "NEW PASSWORD" in why else SIGNIN_RETRY_S
    print(f"  🛑 SELLER CENTRAL SIGN-IN NEEDED — {PROFILE_NAME} cannot read orders. "
          + (why if why else "The automatic sign-in did not get through (see the 🔐 lines above).")
          + f" Or sign in by hand in this profile's Chrome (port {PORT}). Trying again in {wait_s // 60} minutes.")
    time.sleep(wait_s)
    raise RuntimeError(f"Seller Central sign-in needed for {PROFILE_NAME}")


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
    # Same page-load strategy as a managed Chrome (helpers.page_load_strategy): 12 Oct 2026, an
    # HS_COUPON that attached to a leftover Chrome ran on "normal", waited on zoro.com's 'load'
    # event that never comes, and placed nothing for half an hour while the others placed.
    from utils.helpers import page_load_strategy
    opts.page_load_strategy = page_load_strategy("orders")
    try:
        service = ChromeService(ensure_chromedriver())
        driver = webdriver.Chrome(service=service, options=opts)
        harden_driver(driver)
        print(f"✅ Connected to Chrome on port {PORT} — {driver.current_url}"
              f" (page load: {opts.page_load_strategy})")
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
    print(f"\n♻️  BROWSER RECYCLE ({reason}) — relaunching Chrome for a fresh session...")
    if _ultra_crew:
        _ultra_crew.pause()
    try:
        return _recycle_browser_now()
    finally:
        if _ultra_crew:
            _ultra_crew.resume()


def _recycle_browser_now():
    global _active_driver, _active_chrome_process
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
_rush_lock = threading.RLock()   # in ultra mode the writer window shares this journal


def _rush_journal_save():
    try:
        with _rush_lock:
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
    with _rush_lock:
        entry = _rush_pending.setdefault(
            amazon_id, {"amazon_id": amazon_id, "label": "", "notes": []})
        entry["label"] = label
        _rush_journal_save()
    print(f"  ⚡ RUSH: {label} for {amazon_id} journaled — written back after the batch")


def _rush_defer_note(amazon_id, note_text):
    with _rush_lock:
        entry = _rush_pending.setdefault(
            amazon_id, {"amazon_id": amazon_id, "label": "", "notes": []})
        entry["notes"].append(note_text)
        _rush_journal_save()


_RUSH_CANCELLED_JS = (
    "return !document.querySelector(arguments[0]) && "
    "/order cancelled|this order has been cancel+ed/i.test((document.body && document.body.innerText) || '')")


def _rush_order_cancelled(drv):
    """Is this order page a cancelled order? Then there is no Edit button and a label can never be
    written: 3 orders placed 31 Oct and cancelled afterwards stayed journaled for days, each costing
    ~42 s of doomed retries at every flush. Both signs are required, so a live order that merely
    mentions a cancellation is never taken for one. Never raises."""
    try:
        return bool(drv.execute_script(_RUSH_CANCELLED_JS, AMAZON_EDIT_BUTTON_SELECTOR))
    except Exception:
        return False


def _rush_drop_cancelled(amazon_id, label):
    """Off the journal, and if money was spent on it a person is told once: the Zoro order may still
    be cancellable."""
    with _rush_lock:
        _rush_pending.pop(amazon_id, None)
        _rush_journal_save()
    if not str(label or "").startswith("WB"):
        print(f"  ⏭️  {amazon_id}: cancelled on Amazon — '{label or 'note'}' not written")
        return
    text = (f"Amazon order {amazon_id} is CANCELLED, but Zoro order {label} was placed for it. "
            f"Cancel {label} on Zoro if it has not shipped.")
    print(f"  🛑 {text}")
    try:
        _DLOG.log("outcome", profile=PROFILE_NAME, amazon_id=amazon_id, action="placed_then_cancelled",
                  reason=f"Amazon cancelled after {label} was placed")
    except Exception:
        pass
    try:
        from utils.whatsapp import send_whatsapp_message
        send_whatsapp_message("🛑 *PLACED, THEN CANCELLED*\n" + text)
    except Exception as e:
        print(f"  ⚠️ WhatsApp alert error: {e}")
    try:
        from utils.telegram import send_telegram_message
        send_telegram_message("🛑 <b>PLACED, THEN CANCELLED</b>\n" + html.escape(text))
    except Exception as e:
        print(f"  ⚠️ Telegram alert error: {e}")


def _rush_flush_amazon_writes(driver):
    """Write every journaled WB/label + its notes back to Amazon — BATCHED:
    open tabs in parallel (modeled after Closing) so page loads overlap without
    memory exhaustion, then each pre-loaded tab gets its seller-id + notes
    written with skip_navigate. Both WB and seller note are written on the
    same loaded tab without page reloads. Entries that fail stay journaled
    and retry on the next flush or the next run."""
    if not _rush_pending or _ultra_writer_alive():     # the writer window owns the journal
        return driver
    entries = [dict(e) for e in _rush_pending.values()]
    print(f"\n⚡ RUSH FLUSH: {len(entries)} deferred order(s) — loading order pages in parallel tabs...")
    base = driver.current_window_handle
    CHUNK_SIZE = 8

    try:
        for chunk_start in range(0, len(entries), CHUNK_SIZE):
            chunk = entries[chunk_start:chunk_start + CHUNK_SIZE]
            handle_map = {}
            for e in chunk:
                try:
                    driver.switch_to.new_window("tab")
                    handle_map[driver.current_window_handle] = e
                    driver.execute_script("window.location.href = arguments[0];",
                                          f"{AMAZON_BASE_ORDER_URL}{e['amazon_id']}")
                except Exception as oe:
                    print(f"  ⚠️  Could not open flush tab for {e['amazon_id']}: {oe}")

            if not handle_map:
                continue

            # Overlapping readiness polling (modeled on Closing)
            pending_handles = list(handle_map.keys())
            pending = set(range(len(pending_handles)))
            deadline = time.time() + 12
            while pending and time.time() < deadline:
                for i in list(pending):
                    try:
                        h = pending_handles[i]
                        driver.switch_to.window(h)
                        u = (driver.current_url or "").lower()
                        aid = handle_map[h]["amazon_id"].lower()
                        if aid in u or "signin" in u or "/ap/" in u:
                            if driver.execute_script("return document.readyState") == "complete":
                                pending.discard(i)
                    except Exception:
                        pass
                if pending:
                    time.sleep(0.3)

            for h in pending_handles:
                e = handle_map[h]
                aid = e["amazon_id"]
                try:
                    driver.switch_to.window(h)
                    # The account switcher's URL carries the order id too (returnTo=…)
                    on_page = (aid in (driver.current_url or "")) and not is_login_page(driver) \
                        and not on_account_switcher(driver)
                    if not e.get("label") and not on_page:
                        continue                      # notes-only + wrong page → retry later
                    if on_page and _rush_order_cancelled(driver):
                        _rush_drop_cancelled(aid, e.get("label"))
                        continue
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
                        return driver

            # Close worker tabs for this chunk
            for h in pending_handles:
                try:
                    driver.switch_to.window(h)
                    driver.close()
                except Exception:
                    pass
            try:
                driver.switch_to.window(base)
            except Exception:
                try:
                    driver.switch_to.window(driver.window_handles[0])
                except Exception:
                    pass
    finally:
        try:
            driver.switch_to.window(base)
        except Exception:
            try:
                driver.switch_to.window(driver.window_handles[0])
            except Exception:
                pass
    return driver


# ── ULTRA MODE: the Amazon work on windows of its own (engine/ultra_crew.py) ──
# The crew does the same page work as phases A and C above; only WHERE changes. The functions below
# run on its threads, so they touch nothing but their own driver and the locked journal.
ULTRA_SCRAPE_AHEAD = 12
_ultra_retry_at = {}       # amazon_id -> no write attempt before this time
_ultra_write_fails = {}    # amazon_id -> failed write attempts


def _ultra_writer_alive():
    return bool(_ultra_crew and _ultra_crew.writer_alive())


def _ultra_release(drv, close=True):
    """End a crew session. It closes only the window it opened: a fresh session starts out attached
    to an EXISTING window, which may be the placing one, so anything else is left alone. Killing
    its chromedriver never affects Chrome (a session attached by debuggerAddress doesn't own it)."""
    own = getattr(drv, "_zuhra_own_window", None)
    if close and own:
        try:
            if drv.current_window_handle == own:
                drv.close()
        except Exception:
            pass
    try:
        drv.service.process.kill()
        drv.service.process.wait(timeout=3)
    except Exception:
        pass


def _ultra_attach():
    """A chromedriver session of its own on this placer's Chrome, in a new window, dressed like
    connect_chrome's attach path. No Zoro identity: this window only ever sees Seller Central."""
    from utils.helpers import page_load_strategy, _build_stealth_js, _apply_stealth_now
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"localhost:{PORT}")
    opts.page_load_strategy = page_load_strategy("orders")
    drv = webdriver.Chrome(service=ChromeService(ensure_chromedriver()), options=opts)
    try:
        harden_driver(drv)
        drv.switch_to.new_window("window")
        drv._zuhra_own_window = drv.current_window_handle
        # Seller notes save on blur, and a window without focus gets no blur event.
        drv.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})
        drv.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": "America/New_York"})
        drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _build_stealth_js()})
        _apply_stealth_now(drv)
    except Exception:
        _ultra_release(drv)
        raise
    return drv


def _ultra_settle(drv):
    """Wait for the order page like phases A and C do. The crew never signs in: only the main
    thread does (scrape_order_details), and only one thread may."""
    end = time.time() + 12
    while time.time() < end:
        if is_login_page(drv) or on_account_switcher(drv):
            raise LoginWall()
        if drv.execute_script("return document.readyState") == "complete":
            return
        time.sleep(0.3)
    if is_login_page(drv) or on_account_switcher(drv):
        raise LoginWall()


def _ultra_scrape_one(drv, order):
    """scrape_order_details on the scrape window. A page with no items or no address is read once
    more; if it stays that way the main thread scrapes it, as for any failed batch tab."""
    aid = order["amazon_id"]
    for _ in range(2):
        safe_get(drv, f"{AMAZON_BASE_ORDER_URL}{aid}")
        _ultra_settle(drv)
        time.sleep(2)
        if aid not in (drv.current_url or ""):
            continue
        d = _extract_order_details(drv, aid, fallback_qty=order.get("qty", 1))
        if d and (d.get("status") or (d.get("items") and (d.get("address") or {}).get("street_address"))):
            return d
    return None


def _ultra_next_write(skip_id=None):
    """Orders not yet tried come first, so one that keeps failing can never hold up the rest
    (2 Nov 2026: 3 stuck entries at the head of the journal, 0 written back while 30 waited)."""
    now = time.time()
    with _rush_lock:
        retry = None
        for aid, e in _rush_pending.items():
            if aid == skip_id:
                continue
            if aid not in _ultra_write_fails:
                retry = aid
                break
            if retry is None and _ultra_retry_at.get(aid, 0) <= now:
                retry = aid
        if retry is None:
            return None
        e = _rush_pending[retry]
        return {"amazon_id": retry, "label": e.get("label") or "", "notes": list(e.get("notes") or [])}


def _rush_forget(amazon_id, label=None, note=None):
    """Take what Amazon now shows off the journal — only that: the placer may have added to this
    order's entry since it was read."""
    with _rush_lock:
        e = _rush_pending.get(amazon_id)
        if not e:
            return
        if label and e.get("label") == label:
            e["label"] = ""
        if note is not None and note in (e.get("notes") or []):
            e["notes"].remove(note)
        if not e.get("label") and not e.get("notes"):
            _rush_pending.pop(amazon_id, None)
        _rush_journal_save()


def _ultra_write_later(aid, why):
    n = _ultra_write_fails[aid] = _ultra_write_fails.get(aid, 0) + 1
    _ultra_retry_at[aid] = time.time() + min(600, 30 * n)
    print(f"  ⚠️  {aid}: {why} — kept journaled, try {n + 1} in {min(600, 30 * n)} s")
    return False


def _ultra_write_one(drv, e):
    """One journaled order on the write window: the same two calls as a rush flush. A label stays
    journaled until Amazon's page reads it back. A note that still won't save after 3 visits is
    dropped with a warning: the order is safe, and the note is rebuilt from order_log.txt anyway."""
    aid, label, notes = e["amazon_id"], e["label"], e["notes"]
    try:
        if label or notes:
            safe_get(drv, f"{AMAZON_BASE_ORDER_URL}{aid}")
            _ultra_settle(drv)
            if aid not in (drv.current_url or ""):
                return _ultra_write_later(aid, "order page did not open")
            if _rush_order_cancelled(drv):
                _rush_drop_cancelled(aid, label)
                _ultra_write_fails.pop(aid, None)
                _ultra_retry_at.pop(aid, None)
                return True
            if label:
                if not update_amazon_seller_id(drv, aid, label, PROFILE_NAME, skip_navigate=True):
                    if _rush_order_cancelled(drv):       # the banner came late
                        _rush_drop_cancelled(aid, label)
                        _ultra_write_fails.pop(aid, None)
                        _ultra_retry_at.pop(aid, None)
                        return True
                    return _ultra_write_later(aid, f"{label} did not save")
                _rush_forget(aid, label=label)
            for note in notes:
                if write_seller_note(drv, aid, note_text=note, skip_navigate=True):
                    _rush_forget(aid, note=note)
                elif _ultra_write_fails.get(aid, 0) >= 2:
                    print(f"  ⚠️  {aid}: seller note would not save after 3 visits — dropped, add it by hand")
                    _rush_forget(aid, note=note)
                else:
                    return _ultra_write_later(aid, "seller note did not save")
        else:
            _rush_forget(aid)
    except Exception as err:
        _ultra_write_later(aid, f"{type(err).__name__}")
        raise
    _ultra_write_fails.pop(aid, None)
    _ultra_retry_at.pop(aid, None)
    print(f"  ✅ {aid}: {label or '(notes only)'} written back")
    return True


def _ultra_install_print():
    """One line at a time from three threads, and each worker's lines say whose they are. An RLock:
    the signal handler prints on the main thread, possibly while that thread is inside a print."""
    import builtins
    if getattr(builtins.print, "_ultra", False):
        return
    raw, lock = builtins.print, threading.RLock()

    def _print(*a, **k):
        name = threading.current_thread().name
        with lock:
            if name.startswith("AMZ-") and a:
                raw(f"{name}:", *a, **k)
            else:
                raw(*a, **k)
    _print._ultra = True
    builtins.print = _print
    # Most modules keep their own print = functools.partial(print, flush=True), bound at import to the
    # builtin. Those copies have to change too, or the lock and the tags miss nearly every line.
    for mod in list(sys.modules.values()):
        try:
            p = vars(mod).get("print")
        except TypeError:
            continue
        if isinstance(p, functools.partial) and p.func is raw:
            mod.print = functools.partial(_print, *p.args, **p.keywords)


def _ultra_start(orders):
    """The crew for this cycle, or None if Chrome won't take a second session: rush then runs as usual."""
    global _ultra_crew
    _ultra_install_print()
    crew = UltraCrew(_ultra_attach, _ultra_release, _ultra_scrape_one, _ultra_write_one,
                     _ultra_next_write, ahead=ULTRA_SCRAPE_AHEAD)
    if not crew.start(orders):
        print("  ⚠️  ULTRA could not open its windows — this cycle runs as plain RUSH")
        return None
    _ultra_crew = crew
    return crew


def _ultra_stop():
    global _ultra_crew
    if _ultra_crew:
        _ultra_crew.stop()
        print(f"⚡⚡ ULTRA windows closed: {_ultra_crew.status_line()} | {len(_rush_pending)} still journaled")
        _ultra_crew = None


ULTRA_STOCK_CHECK_BUDGET_S = 90
_ultra_oos_asins = set()     # this cycle: listings out of stock, so their orders are left alone
_ultra_oos_told = set()      # SKUs a person has been told are out; cleared when they come back


def _ultra_is_held(order):
    """Left alone this cycle: its listing is out of stock. A Manual-C/-R order never is, since a person
    decided it and it may buy another code."""
    return (str(order.get("asin") or "").strip() in _ultra_oos_asins
            and not _parse_manual_tag(str(order.get("seller_order_id") or "")))


def _ultra_tell(text, html_text):
    """One message when a listing runs out and one when it is back. Replaces one alert per order, which
    would be hundreds for a single listing. Never raises."""
    print(f"  📣 {text}")
    try:
        from utils.whatsapp import send_whatsapp_message
        send_whatsapp_message(text)
    except Exception as e:
        print(f"  ⚠️ WhatsApp alert error: {e}")
    try:
        from utils.telegram import send_telegram_message
        send_telegram_message(html_text)
    except Exception as e:
        print(f"  ⚠️ Telegram alert error: {e}")


def _ultra_tell_out(sku, asin, n=None):
    if sku in _ultra_oos_told:
        return
    _ultra_oos_told.add(sku)
    held = f"{n} order(s) held" if n else "its orders held"
    _ultra_tell(f"⏸️ {sku} ({asin}) is out of stock on Zoro — {PROFILE_NAME}: {held}, nothing marked on Amazon. "
                f"Checked again every cycle.",
                f"⏸️ <b>{sku}</b> ({asin}) is out of stock on Zoro — {PROFILE_NAME}: {held}, nothing marked on "
                f"Amazon. Checked again every cycle.")


def _ultra_hold_oos(driver, orders, sku_map):
    """Ask Zoro about each listing ONCE per cycle and leave the orders of an out-of-stock one alone.

    Owner, 2 Nov 2026: G5695541 ran out at 20:51 with 366 orders queued ahead of ~208 others. For an hour
    nothing could be placed while each one was opened and stamped OOS, and nothing puts an OOS order back
    when stock returns. Now those orders cost no time and get no label; the next cycle asks again, and
    when the listing is back they go through the normal way, every per-order check included.

    Stock is asked once per SKU, biggest listing first. Only a clear "not in stock" holds an order: an API
    error, an ALT that is in stock or unknown, or a listing not reached in the time allowed all stay in
    the queue. Any failure returns the queue as it came. Returns the orders to work on this cycle."""
    _ultra_oos_asins.clear()
    counts = Counter(str(o.get("asin") or "").strip() for o in orders)
    counts.pop("", None)
    if not counts:
        return orders
    t0 = time.time()
    try:
        driver._zuhra_ultra = True
        ensure_on_site(driver)
        ensure_token(driver)
    except Exception as e:
        print(f"  ⚠️  Stock check skipped, queue unchanged ({type(e).__name__})")
        return orders

    stock = {}          # sku -> True out of stock / False buyable / None unknown

    def _is_out(sku):
        if sku not in stock:
            cartable, status = check_inventory(driver, sku, 1)
            stock[sku] = None if status in ("API_ERROR", "NO_DATA") else (not cartable)
        return stock[sku]

    out_asins, unknown = {}, 0
    for asin, _n in counts.most_common():
        if time.time() - t0 > ULTRA_STOCK_CHECK_BUDGET_S or unknown >= 3:
            print("  ⚠️  Stock check stopped early; the listings not reached stay in the queue")
            break
        info = sku_map.get(asin) or {}
        main = str(info.get("zoro_code") or "").strip()
        if not main:
            continue
        try:
            out = _is_out(main)
            for alt in info.get("alt_skus") or []:
                if not out:     # buyable or unknown: the order stays in the queue
                    break
                out = _is_out(alt)
        except Exception as e:
            print(f"  ⚠️  Stock check stopped ({type(e).__name__}); the listings not reached stay in the queue")
            break
        if out is None:
            unknown += 1
        elif out:
            out_asins[asin] = main

    for sku in sorted(_ultra_oos_told):
        if stock.get(sku) is False:
            _ultra_oos_told.discard(sku)
            _ultra_tell(f"✅ {sku} is back in stock on Zoro — {PROFILE_NAME}: its held orders go through from this cycle.",
                        f"✅ <b>{sku}</b> is back in stock on Zoro — {PROFILE_NAME}: its held orders go through from "
                        f"this cycle.")

    _ultra_oos_asins.update(out_asins)
    keep = [o for o in orders if not _ultra_is_held(o)]
    if len(keep) == len(orders):
        print(f"⚡⚡ Stock check: nothing out of stock ({len(stock)} SKU(s) asked, {time.time() - t0:.0f} s)")
        return orders
    held = Counter(str(o.get("asin") or "").strip() for o in orders if _ultra_is_held(o))
    for asin, n in held.items():
        log_price_or_status(out_asins[asin], current_price=0.0, status="OOS")
        _ultra_tell_out(out_asins[asin], asin, n)
        try:        # main and every alternative out for one unit: the Amazon listing must stop selling
            from engine import listing_guard
            _o = next((o for o in orders if str(o.get("asin") or "").strip() == asin), {})
            _m = sku_map.get(asin) or {}
            listing_guard.note_out(STORE_ACCOUNT, asin, str(_o.get("seller_sku") or ""), out_asins[asin],
                                   _m.get("alt_skus") or [],
                                   str(_o.get("order_id") or _o.get("amazon_order_id") or ""),
                                   str(_o.get("title") or ""), held_now=n,
                                   unit_qty=max(1, int(float(_m.get("qty_multiplier") or 1) * int(_m.get("zoro_multiple") or 1))))
        except Exception as e:
            print(f"  ⚠️  listing guard note skipped: {type(e).__name__}: {e}")
    print(f"⚡⚡ Out of stock right now: {sum(held.values())} order(s) held, not marked — "
          + ", ".join(f"{a} x{n} ({out_asins[a]})" for a, n in held.most_common(8))
          + f". {len(keep)} order(s) to work on. Checked again next cycle. ({time.time() - t0:.0f} s)")
    return keep


def _listing_out_note(driver, item, amazon_id, alt_tried=None):
    """Tell the listing guard (engine/listing_guard.py) that this listing's Zoro code and every
    alternative are out of stock, so the Amazon listing stops selling (DS) or gets a longer handling
    time (PL) — owner, 10 Nov 2026, after B00TY4AMZQ kept taking orders with Zoro out.

    Only a clear "out": no alternative that failed to answer, not a code a person chose in a Manual tag
    (that says nothing about the sheet's code), and out for ONE unit of the listing — an order for 3
    can fail where an order for 1 goes through. One database row; the browser is not used here beyond
    the stock question. Never raises."""
    try:
        if item.get("manual_code") or any(s in _INV_UNKNOWN for _, s in (alt_tried or [])):
            return
        asin = str(item.get("asin") or "").strip()
        main = str(item.get("main_sku") or item.get("sku") or "").strip()
        if not asin or not main:
            return
        order_qty = max(1, int(item.get("order_qty") or 1))
        one = max(1, int(item.get("final_qty") or 1) // order_qty)      # Zoro units behind ONE unit of the listing
        if order_qty > 1:
            cartable, status = check_inventory(driver, main, one)
            if cartable or status in _INV_UNKNOWN:
                return
            if item.get("alt_skus"):
                alt_code, _st, tried = first_alt_in_stock(driver, item["alt_skus"], one)
                if alt_code or any(s in _INV_UNKNOWN for _, s in tried):
                    return
        from engine import listing_guard
        listing_guard.note_out(STORE_ACCOUNT, asin, item.get("seller_sku") or "", main,
                               item.get("alt_skus") or [], amazon_id, item.get("title") or "", unit_qty=one)
    except Exception as e:
        print(f"  ⚠️  listing guard note skipped: {type(e).__name__}: {e}")


def _ultra_hold_listing(driver, item):
    """A listing that ran out mid-cycle: leave the rest of its orders alone until the next check. Only if
    it is out even for one unit, since an order for 3 can fail where an order for 1 goes through."""
    sku, asin = item["sku"], str(item.get("asin") or "").strip()
    try:
        if int(item.get("final_qty") or 1) > 1:
            cartable, status = check_inventory(driver, sku, 1)
            if cartable or status in ("API_ERROR", "NO_DATA"):
                return
    except Exception:
        return
    if asin:
        _ultra_oos_asins.add(asin)
    _ultra_tell_out(sku, asin)


def _ultra_dup_prefill(crew, order, detail, dup_map, tried, duplicate_services):
    """The batched duplicate check, on the main thread only (Gmail clients are not thread-safe): one
    pass over this order and whatever is scraped ahead of it. A key it can't vouch for stays out of
    dup_map, so process_order runs the per-order fail-closed check as always. An order cleared a few
    orders ago is asked again against the placer's own records, which have no lag: it may have
    been placed to this address since."""
    aid = order["amazon_id"]
    if not duplicate_services:
        return
    if aid in tried:
        if dup_map.get(aid) == "":
            try:
                from utils.dup_check_local import check_address_local
                a = detail.get("address") or {}
                hit = check_address_local(a.get("street_address", ""), a.get("zip_code", ""),
                                          current_asin=order.get("asin"), current_sku=order.get("sku"),
                                          current_amazon_id=aid)
                if hit:
                    dup_map[aid] = hit["wb"]
            except Exception:
                dup_map.pop(aid, None)
        return
    spec = []
    for o, d in [(order, detail)] + crew.ahead_details():
        a = d.get("address") or {}
        if o["amazon_id"] not in tried and a.get("street_address"):
            tried.add(o["amazon_id"])
            spec.append({"key": o["amazon_id"], "name": o.get("buyer", ""),
                         "address": a.get("street_address", ""), "zip": a.get("zip_code", ""),
                         "asin": o.get("asin", ""), "sku": o.get("sku", "")})
    if spec:
        try:
            from platforms.gmail import batch_check_addresses_gmail
            dup_map.update(batch_check_addresses_gmail(duplicate_services, spec))
        except Exception as e:
            print(f"  ⚠️  Batched dup check failed ({e}) — per-order checks will run")


def _ultra_wait_for_zoro(driver):
    """Zoro went quiet. Running on would release order after order, so hold here until a cart read
    comes back quickly. The Amazon windows keep working meanwhile."""
    try:
        driver._zuhra_net_stall = False
    except Exception:
        pass
    from platforms.zoro import _zoro_api_once, ensure_on_site
    print("  🌐 Waiting for Zoro to answer again before the next order...")
    start = time.time()
    while time.time() - start < 300:
        t0 = time.time()
        try:
            ensure_on_site(driver)
            r = _zoro_api_once(driver, "GET", "/v1/cart", abort_ms=5000)
            # Any HTTP answer will do, and so will no_token: the connection is fine, and the
            # normal checkout path deals with those.
            if r.get("d") == "no_token" or (r.get("s", -1) != -1 and time.time() - t0 < 5):
                print(f"  🌐 Zoro answers again after {int(time.time() - start)} s — placing resumes")
                return driver
        except ZoroAccountRestricted:
            return driver            # the order loop's own handling takes it from here
        except Exception:
            pass
        time.sleep(10)
    return _recycle_browser("Zoro not answering for 5 minutes")


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
def _append_asin_to_sheet_column(col_name, asin, profile_name=""):
    """Best-effort: add an ASIN to a Priority_ASIN_Control column (e.g. the
    Promo_Ineligible list the team reviews). Silently skipped if the column
    doesn't exist — the local memory still routes the order.
    Also stamps a Google Sheets Cell Note: 'ZUHRA'.
    """
    try:
        svc = _get_sheets_write_service()
        meta = svc.spreadsheets().get(spreadsheetId=GOOGLE_SHEET_ID, fields="sheets.properties").execute()
        sheet_id = None
        for s in meta.get("sheets", []):
            if s.get("properties", {}).get("title") == GOOGLE_SHEET_PRIORITY_ASIN_TAB:
                sheet_id = s.get("properties", {}).get("sheetId")
                break
        rows = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{GOOGLE_SHEET_PRIORITY_ASIN_TAB}'!A1:AZ500").execute().get("values", [])
        if not rows or sheet_id is None:
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

        req = {
            "updateCells": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": last,
                    "endRowIndex": last + 1,
                    "startColumnIndex": ci,
                    "endColumnIndex": ci + 1
                },
                "rows": [{
                    "values": [{
                        "userEnteredValue": {"stringValue": asin.upper()},
                        "note": "ZUHRA"
                    }]
                }],
                "fields": "userEnteredValue,note"
            }
        }
        svc.spreadsheets().batchUpdate(
            spreadsheetId=GOOGLE_SHEET_ID,
            body={"requests": [req]}).execute()
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
    # A coupon has worked on this code before, so the item is not promo-ineligible — whatever a
    # single restricted code replies. On 28 Sep 2026 the day's universal EMZM codes ran out and
    # only brand/category-restricted ones were left; each answered "not valid for this item",
    # which read as "every live coupon rejected it" and flagged 12 coupon-able items for good —
    # G4017277 (BlueDEF) among them, after 1,474 coupon orders. The flag never expires by
    # itself, because a flagged item is never sent to a coupon profile again. Only Zoro's own
    # product-page badge (source="pdp") may make that call.
    if source != "pdp" and (cache.get("working_pairs") or {}).get(zoro_code):
        print(f"  🧠 not flagging {zoro_code}: a coupon has worked on it before — "
              f"only the product-page badge can call it promo-ineligible")
        _DLOG.log("learn", profile=PROFILE_NAME, asin=asin, sku=zoro_code, action="strike_refused",
                  reason="coupon history exists; only the PDP badge may flag it", source=source)
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
    # Shipping follows the LIST total (raw_cart_cost); the Select discount does not cost a fee.
    est_cost = real_cost + (small_fee if raw_cart_cost < free_threshold else 0.0)
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
    if sibling not in PROFILES:
        # A reseller-only store (Wrenchly) has nobody to hand to: writing the other store's
        # label would park the order where no placer reads it. Every caller is gated on
        # HAS_COUPON_SIBLING; this is the backstop. Nothing is written, the order stays as it was.
        print(f"  ⚠️ no {sibling} profile for the {STORE_ACCOUNT} store — not routing ({reason}); order left unlabelled")
        return False
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
      1. a PRIORITY coupon applies (always tried first, highest % first) → place here with it —
         on Zoro Select only when it saves MORE than the reseller's 10 % Select discount
         (a 10 % priority code saves the same → rule 2; 15–20 % → here). Owner, 28 Sep 2026.
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
        print(f"     🏷️  [Supervisor] Trying {tier} coupon: {code}...")
        ok, res = _apply_coupon_api(driver, cart_id, code)
        if ok and (res or 0) > 0:
            print(f"     ✅ [Supervisor] {tier.capitalize()} coupon applied: {code} (discount: ${float(res):.2f})")
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
        print(f"     ⏭️  [Supervisor] {tier.capitalize()} coupon {code} rejected: {kind}"
              + (f" (min order ${extra:.0f})" if extra else ""))
        return False

    def _ret(d):
        d["attempts"] = attempts
        d["min_orders"] = min_orders
        return d

    from utils.config import ZORO_SELECT_DISCOUNT_RATE as _sel_rate, coupon_beats_select as _beats_select
    sel_saving = round(raw_cart_cost * _sel_rate, 2)
    matched = None      # (code, discount): a priority code that applies but saves only what Select saves
    print(f"  🧑‍⚖️ SUPERVISOR: testing {len(pri)} priority coupon(s) on the live cart (${raw_cart_cost:.2f})"
          + (" — Zoro Select item(s)" if all_zoro_select else ""))
    for code in pri:
        if _try(code, "priority"):
            _learn_promo_outcome(code0, asin0, True, cache=cache)
            disc = float(attempts[-1].get("discount") or 0)
            if all_zoro_select and not _beats_select(disc, raw_cart_cost):
                # Owner, 28 Sep 2026: a 10 % priority code (ORGANIZE9T26, FALL93A26) on a Zoro Select
                # item saves exactly what the reseller's Select discount saves → equal money, the
                # reseller places it. Only a code that saves MORE (15–20 %) is worth the coupon
                # profile. Codes run highest % first, so the next ones are tried for a bigger saving
                # (applying a code replaces the one on the cart).
                matched = matched or (code, disc)
                print(f"     ↔️  [Supervisor] {code} saves ${disc:.2f} — no more than the reseller's "
                      f"{_sel_rate:.0%} Zoro Select discount (${sel_saving:.2f}); not worth a coupon")
                continue
            return _ret({"action": "place", "coupon": code, "tier": "priority",
                         "reason": f"priority coupon {code} applies (cart ${raw_cart_cost:.2f})",
                         "verdict": _v("COUPON_OK", "Coupon", f"priority {code} works")})
    if tried and transient == tried:
        return _ret({"action": "retry", "coupon": None, "tier": None,
                     "reason": f"Zoro coupon API transient/session errors on all {tried} attempt(s)", "verdict": None})
    if min_orders:
        lo = min(min_orders.values())
        print(f"  🧑‍⚖️ SUPERVISOR: a priority coupon would apply from a ${lo:.0f} cart (this cart ${raw_cart_cost:.2f})")
    # Big Order Gate ($800+): if no priority coupon applies, NEVER test regular 10% EM coupons.
    # Return "call_zoro_20" so the order is marked '20' on Amazon for manual 20% Zoro phone discount.
    big_threshold = float(((gui_cfg if "gui_cfg" in globals() else {}).get("big_order_threshold", 800.0) or 800.0))
    if raw_cart_cost >= big_threshold:
        why = f"Big Order (${raw_cart_cost:.2f} >= ${big_threshold:.0f}) — no priority coupon applies; mark '20' for manual 20% Zoro phone discount"
        return _ret({"action": "call_zoro_20", "coupon": None, "tier": None, "reason": why,
                     "verdict": _v("NO_PRIORITY", "Deciding", why)})
    if all_zoro_select:
        if matched:
            why = (f"Zoro Select — priority coupon {matched[0]} saves ${matched[1]:.2f}, no more than the "
                   f"reseller's {_sel_rate:.0%} Select discount (${sel_saving:.2f}) → reseller")
            return _ret({"action": "reseller", "coupon": None, "tier": None, "reason": why,
                         "verdict": _v("SELECT", "Reseller",
                                       f"Zoro Select; priority {matched[0]} only matches the {_sel_rate:.0%} Select discount")})
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
                     "verdict": _v("NO_PRIORITY", col_by_price, "no priority coupon; sorted by the price of one Amazon unit (Zoro price × pack)")})
    why = (f"no priority coupon applies; cart ${raw_cart_cost:.2f} < ${SUPERVISE_MIN_CART:.0f}"
           + (f"; reseller margin {reseller_est_margin:.1%} is fine" if reseller_est_margin is not None else ""))
    return _ret({"action": "reseller", "coupon": None, "tier": None, "reason": why,
                 "verdict": _v("NO_PRIORITY", col_by_price, "no priority coupon; sorted by the price of one Amazon unit (Zoro price × pack)")})


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
_PC_CFG = ((gui_cfg.get("automation") or {}).get("priority_check") or {})
RECHECK_PER_CYCLE = max(1, int(_PC_CFG.get("recheck_per_cycle", 15) or 15))     # legacy trickle (unused by sweeps)
SWEEP_MAX = max(1, int(_PC_CFG.get("sweep_max", 250) or 250))                     # one coupon-change sweep = the whole store
SWEEP_HOLD_MAX_MIN = float(_PC_CFG.get("sweep_hold_max_min", 90) or 90)           # reseller ignores a sweep flag older than this
_STORE = _store_prefix(STORE_ACCOUNT) or ("DM" if PROFILE_NAME.upper().startswith("DM") else "HS")
_SWEEP_FLAG = os.path.join(os.path.dirname(SCRIPT_DIR), "data", "dataset", f"priority_sweep_{_STORE}.json")


def _sweep_mark(active: bool, reason: str = "", pending: int = 0) -> None:
    """Store-level 'sweep in progress' flag. The coupon profile sets it while it re-tests the
    store after a coupon change; the reseller profile HOLDS its priority/deciding discovery
    while it is set, so no order is bought at reseller price minutes before its ASIN is
    promoted to the Coupon column."""
    try:
        from utils.jsonstore import write_json_atomic
        os.makedirs(os.path.dirname(_SWEEP_FLAG), exist_ok=True)
        write_json_atomic(_SWEEP_FLAG, {"active": bool(active), "reason": reason, "pending": int(pending),
                                        "profile": PROFILE_NAME, "at": time.time(),
                                        "at_text": time.strftime("%Y-%m-%d %H:%M:%S")})
    except Exception as e:
        print(f"  ⚠️ sweep flag not written: {e}")


_SHEET_PRI = {"at": 0.0, "codes": None}


def _sheet_priority_codes():
    """The priority codes in the Coupons tab right now (read-only, cached 5 min; last good value
    kept on a failed read). A reseller uses it only to notice codes its coupon placer has not
    swept yet."""
    if _SHEET_PRI["codes"] is not None and time.time() - _SHEET_PRI["at"] < 300:
        return _SHEET_PRI["codes"]
    codes = None
    try:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            res = load_coupon_codes(force=True)
        if isinstance(res, tuple):                      # a real read — an empty list means "no codes"
            codes = sorted({str((c or {}).get("code") or "").strip() for c in (res[0] or [])} - {""})
    except Exception:
        codes = None
    if codes is not None:
        _SHEET_PRI.update(at=time.time(), codes=codes)
        return codes
    return _SHEET_PRI["codes"] or []


def _sweep_active():
    """Reason text when this store's reseller must hold its priority/deciding discovery, else None:
      • the coupon placer's sweep flag is fresh (a flag older than SWEEP_HOLD_MAX_MIN is ignored), or
      • the store's re-check queue still has ASINs — the sweep is unfinished, whatever the flag's
        age (19 Oct 2026: the flag ran out after 90 min with 94 ASINs untested), or
      • the Coupons tab has priority codes the store's coupon placer has not swept (19 Oct:
        HS_COUPON was stopped from 10:30 to 19:05 after the new codes were added).
    Labelled orders still run while it holds.
    A store with no coupon profile (Wrenchly) never holds: nobody would ever finish that sweep,
    so the "codes not swept yet" test would stop its discovery for good."""
    if not HAS_COUPON_SIBLING:
        return None
    try:
        from utils.jsonstore import read_json
        d = read_json(_SWEEP_FLAG, None)
        if isinstance(d, dict) and d.get("active") and time.time() - float(d.get("at") or 0) < SWEEP_HOLD_MAX_MIN * 60:
            return f"{d.get('reason', 'coupon change')} ({d.get('pending', '?')} ASIN(s) pending, started {d.get('at_text', '')})"
    except Exception:
        pass
    try:
        from engine import priority_check as _pc
        return _pc.sweep_hold_reason(_STORE, _sheet_priority_codes())
    except Exception as e:
        print(f"  ⚠️ sweep-hold check: {e}")
    return None


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
            # Scenario 1 — a code was ADDED: only Reseller/Deciding ASINs can change (promotion).
            # Scenario 2 — a code EXPIRED: only Coupon-column ASINs can change (re-sort).
            # Both at once: the whole store.
            if new and not gone:
                cols, mode, scope = ("_Reseller", "_Deciding"), "sweep_added", "every Reseller + Deciding ASIN"
            elif gone and not new:
                cols, mode, scope = ("_Coupon",), "sweep_removed", "every Coupon-column ASIN"
            else:
                cols, mode, scope = None, "recheck", "every ASIN of the store"
            print(f"  🎟️  Priority coupon set changed ({what}) — sweeping {scope} NOW, before any order is placed")
            from engine import priority_check as _pc
            n = _pc.enqueue_recheck(PROFILE_NAME, f"priority coupon change — {what}", mod=sys.modules[__name__],
                                    columns=cols, mode=mode)
            _sweep_mark(True, f"priority coupon change — {what}", n)
            print(f"     {n} ASIN(s) queued — swept in one pass (up to {SWEEP_MAX}); the reseller holds its "
                  f"priority/deciding discovery until the sweep is done")
        if cur != prev:
            with open(_PRI_SET_PATH, "w", encoding="utf-8") as f:
                json.dump({"codes": cur, "at": time.strftime("%Y-%m-%d %H:%M")}, f)
    except Exception as e:
        print(f"  ⚠️ priority coupon-set watch: {e}")


_PRIORITY_CHECK_LAST = [time.time()]    # first scheduled check only after every_hours
_CART_FAILS = {}                        # amazon_id -> {"n": consecutive cart-add failures, "last": epoch}
_COUPON_WAITS = {}                      # amazon_id -> {"last": epoch, "pool": coupon-pool signature, "reason"}
COUPON_WAIT_MIN = float(ROUTING.get("coupon_wait_cooldown_min", 45) or 0)


def _coupon_pool_sig(coupon_codes):
    """Signature of today's coupon pool — a parked 'waiting for a coupon' order is retried as
    soon as the pool changes (a new scrape / a new priority code), not before."""
    try:
        pri, reg = coupon_codes if isinstance(coupon_codes, tuple) else ([], coupon_codes or [])
        return "|".join(sorted({str((c or {}).get("code") or "") for c in (pri or [])}) + sorted(str(c) for c in (reg or [])))
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════
#  SUNDAY MODE  (launcher checkbox — owner, 11 Oct 2026)
# ══════════════════════════════════════════════════════════════════
# The weekly priority coupons land on Monday around 10:00 PKT. An order bought on Sunday with a
# regular coupon, or at reseller price, may be one Monday's priority code would have covered —
# so on Sunday the placers only do what tomorrow cannot improve:
#   reseller profiles  place an order whose Zoro COST — what is paid: the cart after any Zoro
#                      Select discount, PLUS shipping (or the filler that avoids it) — is under
#                      `max_cart` and whose estimated margin is at least `min_margin_pct`; every
#                      other order waits, UNLABELLED (owner, 11 Oct 2026: "shipping is a cost");
#   coupon profiles    use priority coupons only, never a regular one; they hand an order to the
#                      reseller only when it would pass the reseller rule above, and never mark
#                      one '20' on the strength of "no priority coupon today".
#   coupon-blocked     an order whose every item Zoro marks "not eligible for any other offers or
#                      promotions" is placed normally: no coupon can ever apply, so waiting gains
#                      nothing.
# Sunday mode never does anything a placer would not do on a normal day — it only holds back. It
# writes no label, so unticking it releases every held order at once. A Manual-C/-R order is a
# human decision and goes straight through. Held orders are not re-scraped or re-priced until the
# numbers change, the priority coupons change, or SUNDAY_HOLD_MAX_AGE passes.
# bot_config.json → "sunday_mode": {"enabled": bool, "max_cart": 50, "min_margin_pct": 20}
_SUNDAY_HOLDS = {}                    # amazon_id -> {"sig", "pool", "at", "reason"}
_SUNDAY_CFG = {"at": 0.0, "val": None, "loaded": False}
SUNDAY_HOLD_MAX_AGE = 6 * 3600        # prices move: re-check a held order at least this often


def _sunday_read(path=None):
    """Parse the sunday_mode block of bot_config.json. Pure apart from the file read; raises on a
    read/parse error so the caller can keep its last good value."""
    from utils.config import CONFIG_PATH as _cfg_path
    try:
        with open(path or _cfg_path, "r", encoding="utf-8") as fh:
            blk = (json.load(fh) or {}).get("sunday_mode") or {}
    except FileNotFoundError:
        return None
    if not blk.get("enabled"):
        return None

    def _num(v, d):
        try:
            return float(v)
        except (TypeError, ValueError):
            return d
    cap = _num(blk.get("max_cart"), 50.0)
    mm = _num(blk.get("min_margin_pct"), 20.0) / 100.0
    return {"max_cart": cap, "min_margin": mm, "sig": f"{cap:.2f}|{mm:.4f}"}


def _sunday_mode():
    """{"max_cart", "min_margin", "sig"} while Sunday mode is ticked in the launcher, else None.

    Asked once per order, so cached for 15 s. It reads the file itself rather than through
    load_gui_config(): that returns {} on any read error, which would switch Sunday mode OFF for
    an order if the launcher happened to be rewriting bot_config.json at that instant — and the
    order placed in that instant is exactly the kind Sunday mode exists to hold. On a read error
    the last good value is kept.

    Wrenchly is exempt whatever the checkbox says (owner, 9 Nov 2026): it buys from a reseller
    account only, so it has no Monday coupons to wait for."""
    if STORE_ACCOUNT == "Wrenchly":
        return None
    now = time.time()
    if _SUNDAY_CFG["loaded"] and now - _SUNDAY_CFG["at"] < 15:
        return _SUNDAY_CFG["val"]
    try:
        val = _sunday_read()
    except Exception as e:
        print(f"  ⚠️ Sunday mode: bot_config.json unreadable ({e}) — keeping the last setting")
        _SUNDAY_CFG["at"] = now
        return _SUNDAY_CFG["val"]
    prev = _SUNDAY_CFG["val"]
    if _SUNDAY_CFG["loaded"] and (prev or {}).get("sig") != (val or {}).get("sig"):
        if val:
            print(f"\n🗓️  SUNDAY MODE ON — {_sunday_rule_text(val)}")
        else:
            print(f"\n🗓️  SUNDAY MODE OFF — {len(_SUNDAY_HOLDS)} held order(s) released, normal rules again")
            _SUNDAY_HOLDS.clear()
    _SUNDAY_CFG.update(at=now, val=val, loaded=True)
    return val


def _sunday_rule_text(sun):
    return (f"reseller places orders whose Zoro cost incl. shipping is under ${sun['max_cart']:.2f} at "
            f"≥ {sun['min_margin']:.0%} est. margin; coupon placers use priority coupons only; "
            f"everything else waits for Monday")


def _sunday_gate(cost, margin, sun, shipping=0.0):
    """Pure. Why an order must wait under Sunday mode, or None when it may be placed now.
    `cost` is what Zoro will be paid: the cart after any Zoro Select discount PLUS shipping (or
    the filler that avoids it) — the owner's rule: shipping is a cost. `shipping` is only for the
    message. `margin` is the estimated margin (profit / cost), or None when earnings are unknown
    — those are never placed anyway, by their own gate."""
    if not sun:
        return None
    if (cost or 0.0) >= sun["max_cart"]:
        ship = f" incl. ${shipping:.2f} shipping" if shipping else ""
        return f"Zoro cost ${cost:.2f}{ship} ≥ ${sun['max_cart']:.2f}"
    if margin is not None and margin < sun["min_margin"]:
        return f"est. margin {margin:.1%} < {sun['min_margin']:.0%}"
    return None


def _sunday_reseller_cost(raw_cart_cost, all_zoro_select):
    """(cost, shipping) the RESELLER would pay for a cart a coupon profile is thinking of handing
    it: Zoro Select items 10 % off, the small-order fee only when the LIST total is under $50."""
    from utils.config import (ZORO_SMALL_ORDER_FEE as _fee, FREE_SHIPPING_THRESHOLD as _free,
                              ZORO_SELECT_DISCOUNT_RATE as _sel)
    raw = float(raw_cart_cost or 0.0)
    ship = _fee if 0 < raw < _free else 0.0
    return raw * ((1 - _sel) if all_zoro_select else 1.0) + ship, ship


def _sunday_reseller_margin(raw_cart_cost, earnings, all_zoro_select):
    """The margin the RESELLER would make on that cart (profit / cost)."""
    cost, _ship = _sunday_reseller_cost(raw_cart_cost, all_zoro_select)
    if not earnings or cost <= 0:
        return None
    return (earnings - cost) / cost


def _sunday_handover_gate(raw_cart_cost, earnings, all_zoro_select, sun):
    """The reseller rule, applied by a coupon profile before handing an order over."""
    cost, ship = _sunday_reseller_cost(raw_cart_cost, all_zoro_select)
    return _sunday_gate(cost, _sunday_reseller_margin(raw_cart_cost, earnings, all_zoro_select), sun, ship)


def _all_coupon_blocked(resolved_items):
    """True when NO item in the order can ever take a coupon — so holding it for Monday's
    priority codes gains nothing and Sunday mode lets it through (owner, 11 Oct 2026).

    An item counts as blocked when Zoro's product page says "Zoro's best price — not eligible
    for any other offers or promotions" (cached per SKU; read here, no page visit), or it is on
    the promo-ineligible list (bot_config, the sheet's Promo_Ineligible column, or what the
    coupon placers learned). EVERY item must be blocked: if one could still take a coupon,
    Monday's code could still improve the cart."""
    items = list(resolved_items or [])
    if not items:
        return False
    for it in items:
        asin = str(it.get("asin") or "").strip().upper()
        codes = {str(it.get("sku") or "").strip(), str(it.get("main_sku") or "").strip()} - {""}
        if asin in PROMO_INELIGIBLE_ASINS or (codes & PROMO_INELIGIBLE_CODES):
            continue
        try:
            if any(pdp_promo_ineligible(c) for c in codes):
                continue
        except Exception:
            pass
        return False
    return True


def _priority_pool_sig(coupon_codes):
    """Only the PRIORITY codes. Sunday mode never uses a regular code, so a regular-coupon scrape
    must not wake every held order; a new priority code (Monday morning) must."""
    try:
        pri = coupon_codes[0] if isinstance(coupon_codes, tuple) else []
        return "|".join(sorted({str((c or {}).get("code") or "") for c in (pri or [])}))
    except Exception:
        return ""


def _sunday_park(amazon_id, why, coupon_codes, sun):
    _SUNDAY_HOLDS[amazon_id] = {"sig": sun["sig"], "pool": _priority_pool_sig(coupon_codes),
                                "at": time.time(), "reason": why}


def _sunday_parked(amazon_id, coupon_codes, label=""):
    """True while an order held under Sunday mode should be left alone: same numbers, same
    priority coupons, held under SUNDAY_HOLD_MAX_AGE, and not since tagged Manual-C/-R."""
    h = _SUNDAY_HOLDS.get(amazon_id)
    if not h:
        return False
    sun = _sunday_mode()
    if (sun and not str(label or "").startswith("Manual-") and sun["sig"] == h["sig"]
            and _priority_pool_sig(coupon_codes) == h["pool"]
            and time.time() - h["at"] < SUNDAY_HOLD_MAX_AGE):
        return True
    _SUNDAY_HOLDS.pop(amazon_id, None)
    return False


def _sunday_hold(amazon_id, why, coupon_codes, sun, f):
    """Hold an order that has NOT been claimed yet: print, park, log SUNDAY_HOLD, write nothing
    to Amazon. Returns the process_order status."""
    print(f"  🗓️  SUNDAY MODE: waiting — {why}")
    print(f"      ({_sunday_rule_text(sun)}; nothing is labelled — it is placed as soon as Sunday mode is off)")
    _sunday_park(amazon_id, why, coupon_codes, sun)
    _log_order({"amazon_id": amazon_id, "asin": f.get("asin", ""), "sku": f.get("sku", ""),
                "qty": f.get("qty", ""), "buyer": f.get("buyer", ""), "city": f.get("city", ""),
                "state": f.get("state", ""), "earnings": f.get("earnings") or "",
                "zoro_price": f.get("cart") or "", "status": "SUNDAY_HOLD",
                "notes": f"Sunday mode: {why}"})
    return "skipped"


def _coupon_fail_decision(last_fail, raw_cart_cost, earnings, all_zoro_select, small_fee, free_threshold, min_margin,
                          big_order_20=True):
    """What a checkout 'COUPON FAIL' means for a Coupon-column order (pure; unit-tested):
      • every code says 'not valid for this item' while regular codes existed → this item can
        never take a coupon → reseller, and it is definitive (promo-ineligible memory);
      • every attempt was a session/API error → wait (retry next cycle);
      • all Zoro Select and no priority code applies → reseller (it already has 10 %);
      • cart under the free-shipping line, nothing fits it, reseller margin ≥ min_margin → reseller;
      • otherwise → wait for the next coupon scrape (the owner's rule: never list price)."""
    lf = last_fail or {}
    g = lambda k: int(lf.get(k) or 0)
    total, item, mo, tr, dead = g("total"), g("item"), g("min_order"), g("transient"), g("dead")
    regs, pri = g("regular_available"), g("priority")
    res_cost = (raw_cart_cost or 0.0) + (small_fee if (raw_cart_cost or 0.0) < free_threshold else 0.0)
    res_margin = ((earnings - res_cost) / res_cost) if (earnings and res_cost > 0) else None
    mtxt = f"{res_margin:.1%}" if res_margin is not None else "unknown"
    if total and tr == total:
        return {"action": "wait", "definitive": False, "reason": "coupon API trouble on every code — retry next cycle"}
    big_threshold = float(((gui_cfg if "gui_cfg" in globals() else {}).get("big_order_threshold", 800.0) or 800.0))
    if big_order_20 and (raw_cart_cost or 0.0) >= big_threshold:      # False for a Manual-C order
        return {"action": "call_zoro_20", "definitive": False,
                "reason": f"Big Order (${raw_cart_cost:.2f} >= ${big_threshold:.0f}) — no priority coupon applied; mark '20' for manual 20% Zoro phone discount"}
    tiered = "regular_dead" in lf or "priority_dead" in lf
    if not tiered:                                   # summaries written before 14 Sep 2026
        if total and item == total and regs > 0:
            return {"action": "route_reseller", "definitive": True,
                    "reason": f"all {total} coupons rejected this item — coupons never apply; reseller places it"}
        if all_zoro_select and total and (item + mo) == total:
            return {"action": "route_reseller", "definitive": False,
                    "reason": "Zoro Select and no priority coupon applies — the reseller already has 10 % off"}
        if (raw_cart_cost or 0.0) < free_threshold and total and (item + mo) == total and res_margin is not None and res_margin >= min_margin:
            return {"action": "route_reseller", "definitive": False,
                    "reason": f"cart ${raw_cart_cost:.2f} is below every coupon minimum; reseller margin {mtxt} is fine"}
        why = "no regular coupon left in today's pool" if regs == 0 else "no coupon fits this cart yet"
        return {"action": "wait", "definitive": False, "reason": why + " — waiting for the next coupon scrape"}
    # ── tiered summary: a DEAD (expired) code is not a verdict on the item ──
    r_item, r_dead, r_mo, r_tr = g("regular_item"), g("regular_dead"), g("regular_min_order"), g("regular_transient")
    p_item, p_dead, p_mo, p_tr = g("priority_item"), g("priority_dead"), g("priority_min_order"), g("priority_transient")
    live_regs = max(0, regs - r_dead)                # regular codes Zoro did not declare dead
    live_pri = max(0, pri - p_dead)
    valid = total - tr - dead                        # attempts that were a real answer for THIS cart
    pri_rejected = live_pri == 0 or (p_tr == 0 and (p_item + p_mo) >= live_pri)
    if live_regs > 0 and r_tr == 0 and r_item > 0 and r_item >= live_regs and pri_rejected:
        return {"action": "route_reseller", "definitive": True,
                "reason": f"every live coupon ({r_item} regular, {p_item + p_mo} priority) rejected this item — "
                          f"coupons never apply; reseller places it"}
    if r_tr or p_tr:
        # a live code answered with a session/API error: not a verdict either way → next cycle
        return {"action": "wait", "definitive": False,
                "reason": f"{r_tr + p_tr} live coupon(s) answered with API errors — retry next cycle"}
    if all_zoro_select and pri_rejected and valid > 0:
        return {"action": "route_reseller", "definitive": False,
                "reason": "Zoro Select and no priority coupon applies — the reseller already has 10 % off"}
    if live_regs == 0 and pri_rejected and valid > 0:
        if res_margin is not None and res_margin >= min_margin:
            return {"action": "route_reseller", "definitive": False,
                    "reason": f"no live regular coupon in the pool ({r_dead} dead) and no priority code applies; "
                              f"reseller margin {mtxt} is fine"}
        return {"action": "wait", "definitive": False,
                "reason": f"no live regular coupon in the pool ({r_dead} dead); reseller margin {mtxt} too thin — "
                          f"waiting for the next coupon scrape"}
    if (raw_cart_cost or 0.0) < free_threshold and valid > 0 and (item + mo) >= valid and res_margin is not None and res_margin >= min_margin:
        return {"action": "route_reseller", "definitive": False,
                "reason": f"cart ${raw_cart_cost:.2f} is below every coupon minimum; reseller margin {mtxt} is fine"}
    why = "no live regular coupon in the pool" if live_regs == 0 else "no coupon fits this cart yet"
    return {"action": "wait", "definitive": False, "reason": why + " — waiting for the next coupon scrape"}


def _maybe_run_priority_check(driver, sku_map, coupon_codes, cache):
    """Coupon profiles: verify new Priority-ASIN-Control ASINs between cycles —
    every automation.priority_check.every_hours, or whenever the dashboard drops
    data/control/priority_check.request. Uses THIS placer's Zoro session, so it
    never fights a running checkout for the cart."""
    if not IS_COUPON_PROFILE or DRY_RUN or ASIN_SEARCH:
        return False
    pc = (gui_cfg.get("automation") or {}).get("priority_check") or {}
    try:
        from engine import priority_check as _pc
    except Exception as e:
        print(f"  ⚠️ priority check module unavailable: {e}")
        return False
    moved_any = False

    def _drain(reason):
        """Sweep the WHOLE queue now (not 15 per cycle): the sheet must be right before the
        first order of this cycle is placed. Returns True if any ASIN changed column."""
        nonlocal moved_any
        pending = _pc.queue_size(PROFILE_NAME)
        if not pending:
            _sweep_mark(False)
            return False
        _sweep_mark(True, reason, pending)
        t0 = time.time()
        try:
            res = _pc.run_queue(driver, PROFILE_NAME, sku_map=sku_map, coupon_codes=coupon_codes, cache=cache,
                                mod=sys.modules[__name__], max_asins=SWEEP_MAX) or []
        finally:
            left = _pc.queue_size(PROFILE_NAME)
            if left:
                _sweep_mark(True, reason, left)          # interrupted / transient: still pending
            else:
                _sweep_mark(False)
        moved = [r for r in res if r.get("Check_Moved")]
        if moved:
            moved_any = True
        print(f"  🧹 sweep: {len(res)} ASIN(s) tested in {(time.time() - t0) / 60:.1f} min, "
              f"{len(moved)} moved" + (f", {left} still pending" if left else "") + "  — " + reason)
        return bool(moved)

    # 1) drain the re-check queue (coupon change / reset) — the whole thing, right now
    try:
        _q = _pc.read_json(_pc._queue_path(PROFILE_NAME), None) if os.path.exists(_pc._queue_path(PROFILE_NAME)) else None
        _drain((_q or {}).get("reason") or "re-check queue" if isinstance(_q, dict) else "re-check queue")
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
        return moved_any
    _PRIORITY_CHECK_LAST[0] = time.time()
    try:
        os.remove(req)
    except OSError:
        pass
    try:
        if args.get("reset"):
            n = _pc.enqueue_recheck(PROFILE_NAME, f"reset & rebuild requested by {args.get('author', 'dashboard')}",
                                    mod=sys.modules[__name__], include_ineligible=True)
            print(f"  🔁 RESET: {n} ASIN(s) queued — rebuilding the store in one sweep now")
            _drain(f"reset & rebuild ({args.get('author', 'dashboard')})")
            return moved_any
        res = _pc.run_check(driver, PROFILE_NAME, sku_map=sku_map, coupon_codes=coupon_codes, cache=cache,
                            mod=sys.modules[__name__], only_new=not args.get("all"),
                            max_asins=int(args.get("max") or pc.get("max_per_run", 25) or 25)) or []
        if any(r.get("Check_Moved") for r in res):
            moved_any = True
    except Exception as e:
        print(f"  ⚠️ priority check failed: {e}")
    return moved_any


def _rush_scrape_batch(driver, batch):
    """RUSH phase A: open tabs in parallel (modeled after Closing) so Amazon loads
    detail pages concurrently without memory exhaustion, then harvest each tab
    with the same extraction code as the classic scrape. Returns {amazon_id: detail}.
    Orders missing from the map are scraped serially by the caller."""
    details = {}
    base = driver.current_window_handle
    CHUNK_SIZE = 8

    try:
        for chunk_start in range(0, len(batch), CHUNK_SIZE):
            chunk = batch[chunk_start:chunk_start + CHUNK_SIZE]
            handle_to_order = {}
            for o in chunk:
                url = f"{AMAZON_BASE_ORDER_URL}{o['amazon_id']}"
                try:
                    driver.switch_to.new_window("tab")
                    handle_to_order[driver.current_window_handle] = o
                    driver.execute_script("window.location.href = arguments[0];", url)
                except Exception as e:
                    print(f"  ⚠️  Could not open batch tab for {o['amazon_id']}: {e}")

            if not handle_to_order:
                continue

            # Overlapping readiness polling (modeled on Closing: scrape_sc_fields.py)
            pending_handles = list(handle_to_order.keys())
            pending = set(range(len(pending_handles)))
            deadline = time.time() + 12
            while pending and time.time() < deadline:
                for i in list(pending):
                    try:
                        h = pending_handles[i]
                        driver.switch_to.window(h)
                        u = (driver.current_url or "").lower()
                        aid = handle_to_order[h]["amazon_id"].lower()
                        if aid in u or "signin" in u or "/ap/" in u:
                            if driver.execute_script("return document.readyState") == "complete":
                                pending.discard(i)
                    except Exception:
                        pass
                if pending:
                    time.sleep(0.3)

            login_hit = False
            for h in pending_handles:
                o = handle_to_order[h]
                aid = o["amazon_id"]
                try:
                    driver.switch_to.window(h)
                except Exception:
                    continue
                if is_login_page(driver) or on_account_switcher(driver):
                    login_hit = True
                    break
                if aid not in (driver.current_url or ""):
                    continue
                try:
                    d = _extract_order_details(driver, aid, fallback_qty=o.get("qty", 1))
                    if d:
                        details[aid] = d
                except Exception as e:
                    print(f"  ⚠️  Batch-tab extract failed for {aid}: {e} — will scrape serially")

            # Close worker tabs for this chunk
            for h in pending_handles:
                try:
                    driver.switch_to.window(h)
                    driver.close()
                except Exception:
                    pass
            try:
                driver.switch_to.window(base)
            except Exception:
                try:
                    driver.switch_to.window(driver.window_handles[0])
                except Exception:
                    pass

            if login_hit:
                print("  🔐 Login wall inside batch tabs — remaining batch falls back to serial scrape")
                break
    finally:
        try:
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


SCAN_PAGE_LIMIT = 100    # 10,000 orders; only a Next button that never greys out could reach it


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
        if is_login_page(driver):
            _signin_needed()             # says so once, waits, ends the cycle
    time.sleep(5)
    if on_account_switcher(driver):      # signed in, no store chosen yet
        choose_store_if_asked(driver, PROFILE_NAME, target_url=url)
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
            print(f"  ⚠️  Could not set search type dropdown: {_first_line(e)}")

    try:
        # Set date range to Last 30 days
        sel = Select(driver.find_element(By.CSS_SELECTOR, "#myo-table-date-range"))
        sel.select_by_value("last-30")
        print("  Set date range: Last 30 days")
        time.sleep(1.5)
    except Exception as e:
        print(f"  ⚠️  Could not set date range: {_first_line(e)}")

    try:
        # Set sort to Order date (descending)
        sel = Select(driver.find_element(By.CSS_SELECTOR, "#myo-table-sort-order"))
        sel.select_by_value("order_date_desc")
        print("  Set sort: Order date (descending)")
        time.sleep(1.5)
    except Exception as e:
        print(f"  ⚠️  Could not set sort: {_first_line(e)}")

    try:
        # Set 100 results per page
        sel = Select(driver.find_element(By.CSS_SELECTOR, "#myo-table-results-per-page"))
        sel.select_by_value("100")
        print("  Set 100 results per page")
        time.sleep(2)
    except Exception as e:
        print(f"  ⚠️  Could not set results per page: {_first_line(e)}")

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
        print(f"  ❌ Filter validation failed: {_first_line(e)}")
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

    # ASIN searches: first page only (100 results is enough). Anything else: every page Amazon counts,
    # plus one for orders arriving during the scan. A flat 20 left the oldest ~1,900 of HOANK's 3,929
    # orders unread, Manual tags with a Zoro code among them (3 Nov 2026).
    effective_max = 1 if asin_search else max(max_pages, min(total_orders // 100 + 2, SCAN_PAGE_LIMIT))
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


# ── FOCUS PRIORITY COUPON ("Place with Priority") ────────────────────────
# Owner's purpose (14 Sep 2026): a priority coupon is about to expire → place EVERY order that
# can be placed with it, and only those, until the box is unticked. Reliability rules:
#   • the code is resolved against TODAY's Coupons tab (shorthand 'facility' → FACILITY8M26),
#     never against a hard-coded list — a typo or an expired code places nothing and says so;
#   • an ASIN is kept when the bot's memory says this coupon works on it; an ASIN with no
#     memory (or a stale one) is PROBED live on a scratch cart with the focus coupon before
#     its orders are queued — memory alone would miss every human-placed Coupon ASIN;
#   • an ASIN whose recent verdict is another coupon / Select / no-priority is deferred
#     (nothing is lost: its orders place normally once focus is off);
#   • at checkout the focus coupon is tried FIRST; if it fails on the live cart the normal
#     order (other priority codes, then regular) still applies — the order is never dropped.
FOCUS_PROBE_TTL_S = 24 * 3600                       # a probe answer is trusted for a day (survives restarts)
FOCUS_VERDICT_MAX_AGE_DAYS = 7
_FOCUS_TO_PROBE = []                                # (asin, zoro_code, qty) waiting for the one-pass probe
_FOCUS_PROBE_PATH = os.path.join(os.path.dirname(SCRIPT_DIR), "data", "dataset", "focus_probes.json")


def _focus_probe_cache() -> dict:
    try:
        from utils.jsonstore import read_json
        return read_json(_FOCUS_PROBE_PATH, {}) or {}
    except Exception:
        return {}


def _focus_probe_remember(key: str, ok: bool) -> None:
    try:
        from utils.jsonstore import mutate_json
        with mutate_json(_FOCUS_PROBE_PATH, {}) as d:      # locked read-modify-write, in place
            d[key] = {"ok": bool(ok), "at": time.time()}
            cutoff = time.time() - 7 * 86400
            for k in [k for k, v in d.items() if float((v or {}).get("at") or 0) <= cutoff]:
                del d[k]
    except Exception as e:
        print(f"  ⚠️ focus probe memory: {e}")


def _focus_query() -> str:
    """The focus query if 'Place with Priority' is on for this profile (CLI flag wins), else ''.
    Read from bot_config every time so ticking the box in the launcher applies at the next cycle."""
    if CLI_FOCUS_COUPON:
        return CLI_FOCUS_COUPON
    try:
        p = (load_gui_config().get("profiles") or {}).get(PROFILE_NAME) or {}
        if p.get("focus_priority_coupon_enabled"):
            return str(p.get("focus_priority_coupon") or "").strip()
    except Exception:
        pass
    return ""


def _focus_active_codes(loaded_priority_codes) -> list:
    q = _focus_query()
    return _focus_codes(q, loaded_priority_codes) if q else []


def _focus_codes(focus_query: str, loaded_priority_codes) -> list:
    """Resolve 'FACILITY8M26' / 'facility' / 'facility, hardware' against the priority codes
    loaded from the Coupons tab this cycle. Unknown queries resolve to nothing."""
    codes = [str((c or {}).get("code") if isinstance(c, dict) else c or "").strip().upper()
             for c in (loaded_priority_codes or [])]
    codes = [c for c in codes if c]
    out = []
    for q in re.split(r"[,;\s]+", (focus_query or "").upper()):
        q = q.strip()
        if not q:
            continue
        hit = [c for c in codes if c == q] or [c for c in codes if c.startswith(q)] or [c for c in codes if q in c]
        for c in hit:
            if c not in out:
                out.append(c)
    return out


def _focus_decision(verdict, codes, now=None) -> str:
    """'keep' | 'skip' | 'probe' for one ASIN from the bot's memory (pure; unit-tested)."""
    now = now if now is not None else time.time()
    codes = [c.upper() for c in (codes or [])]
    if not isinstance(verdict, dict) or not verdict:
        return "probe"
    status = str(verdict.get("status") or "").upper()
    coupon = str(verdict.get("coupon") or "").upper()
    if status == "PROMO_INELIGIBLE":
        return "skip"
    if status == "COUPON_OK" and any(c in coupon for c in codes):
        return "keep"
    try:
        age_days = (now - time.mktime(time.strptime(str(verdict.get("at") or "")[:16], "%Y-%m-%d %H:%M"))) / 86400.0
    except Exception:
        age_days = 999.0
    if age_days > FOCUS_VERDICT_MAX_AGE_DAYS:
        return "probe"                       # tested before this coupon (or too long ago)
    if status == "COUPON_OK":
        return "skip"                        # another priority coupon works — nothing is lost
    if any("SELECT" in c for c in codes) and status == "SELECT":
        return "probe"                       # Zoro Select coupon: MUST probe Zoro Select items!
    if status in ("SELECT", "NO_PRIORITY", "REGULAR_OK"):
        return "skip"                        # recently tested with every priority code: no
    return "probe"


def _focus_probe(driver, zoro_code, qty, codes) -> bool:
    """Does one of the focus coupons apply to this SKU right now? Scratch cart, then cleared.
    No order, no single-use coupon touched (priority codes are multi-use)."""
    from platforms.zoro import clear_cart_and_add
    from checkout.true_api import _get_cart_id, _apply_coupon_api, _clear_cart_api
    try:
        from engine.priority_check import zoro_session_ready
        if not zoro_session_ready(driver):
            return False
        ok, _price = clear_cart_and_add(driver, zoro_code, max(1, int(qty or 1)))
        if not ok:
            return False
        cart_id = _get_cart_id(driver)
        if not cart_id:
            return False
        for c in codes:
            okc, res = _apply_coupon_api(driver, cart_id, c)
            if okc and (res or 0) > 0:
                return True
        return False
    except Exception as e:
        print(f"  ⚠️ focus probe {zoro_code}: {e}")
        return False
    finally:
        try:
            _clear_cart_api(driver)
        except Exception:
            pass


def _focus_filter(driver, scraped_orders, codes, sku_map):
    """Keep only the orders whose ASIN takes one of the focus coupons.
    Per ASIN (not per order): memory says the coupon works → kept, no test; a fresh memory of
    anything else → deferred, no test; no fresh memory → queued for the ONE-PASS probe that runs
    right after this cycle's known-good orders are placed (owner: place the known ones, then
    check all the rest in one go). Probe answers are remembered for a day.
    Returns (kept, deferred, to_probe_count)."""
    from engine import priority_check as _pc
    verdicts = _pc.load_verdicts()
    probe_mem = _focus_probe_cache()
    ckey = "|".join(codes)
    by_asin = {}
    for o in scraped_orders:
        by_asin.setdefault((o.get("asin") or "").strip().upper(), []).append(o)
    kept, deferred = [], []
    _FOCUS_TO_PROBE.clear()
    now = time.time()
    for asin, orders in by_asin.items():
        v = verdicts.get(asin) if asin else None
        d = _focus_decision(v, codes, now) if asin else "skip"
        if d == "probe":
            pr = probe_mem.get(f"{asin}|{ckey}")
            if pr and now - float(pr.get("at") or 0) < FOCUS_PROBE_TTL_S:
                d = "keep" if pr.get("ok") else "skip"
            else:
                m = (sku_map or {}).get(asin) or {}
                code = str(m.get("zoro_code") or "").strip()
                if code:
                    try:
                        qty = max(1, int(float(m.get("qty_multiplier") or 1) * int(m.get("zoro_multiple") or 1)))
                    except Exception:
                        qty = 1
                    _FOCUS_TO_PROBE.append((asin, code, qty))
                d = "skip"                                         # this cycle: known-good first
        (kept if d == "keep" else deferred).extend(orders)
    return kept, deferred, len(_FOCUS_TO_PROBE)


def _focus_probe_all(driver, codes) -> tuple:
    """After the known-good orders are placed: test EVERY unknown ASIN in one pass (no cap),
    remember each answer, and record a Coupon verdict where the coupon works. Returns (ok, no)."""
    ckey = "|".join(codes)
    todo = list(_FOCUS_TO_PROBE)
    _FOCUS_TO_PROBE.clear()
    if not todo:
        return 0, 0
    print(f"\n  🎯 FOCUS: known-good orders done — testing {len(todo)} unknown ASIN(s) with {'/'.join(codes)} in one pass")
    t0 = time.time()
    ok_n = no_n = 0
    for i, (asin, code, qty) in enumerate(todo, 1):
        ok = _focus_probe(driver, code, qty, codes)
        _focus_probe_remember(f"{asin}|{ckey}", ok)
        print(f"    [{i}/{len(todo)}] {asin} ({code}): {'✅ applies' if ok else '✖ no'}")
        if ok:
            ok_n += 1
            _record_asin_verdict(asin, {"status": "COUPON_OK", "suggest": "Coupon",
                                        "note": f"priority {codes[0]} works (focus probe)"}, "", codes[0])
        else:
            no_n += 1
    print(f"  🎯 FOCUS probe pass: {ok_n} ASIN(s) take the coupon, {no_n} do not — {(time.time() - t0) / 60:.1f} min; "
          f"placing the new ones now")
    return ok_n, no_n


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
    if on_account_switcher(driver):
        if not choose_store_if_asked(driver, PROFILE_NAME, target_url=url):
            return None                  # never read an order off this page
        time.sleep(2)
    return _extract_order_details(driver, amazon_order_id, fallback_qty=fallback_qty)


# Seller Central's "Buyer requested cancel" banner: "The buyer has requested that this order be canceled."
# The order is still Unshipped, so nothing else on the page says so — the status check below only knew
# 'order cancelled' / 'buyer cancellation' / 'cancellation reason', and such orders were bought (owner,
# 30 Oct 2026, 112-4663939-9265025). The banner is a Katal alert whose text can live in attributes or a
# shadow root, outside body.innerText, so those are read too.
_CANCEL_REQUESTED_JS = r"""
    var re = /buyer requested cancel|requested that this order be cancel/i;
    if (re.test((document.body && document.body.innerText) || '')) return true;
    var els = document.querySelectorAll('kat-alert, [role="alert"], .a-alert, [class*="alert" i]');
    for (var i = 0; i < els.length; i++) {
        var e = els[i];
        var s = (e.getAttribute('header') || '') + ' ' + (e.getAttribute('description') || '') + ' '
              + (e.textContent || '') + ' ' + (e.shadowRoot ? (e.shadowRoot.textContent || '') : '');
        if (re.test(s)) return true;
    }
    return false;
"""


def _buyer_requested_cancel(driver):
    """True when the order page shows Amazon's 'Buyer requested cancel' banner. Never raises."""
    try:
        return bool(driver.execute_script(_CANCEL_REQUESTED_JS))
    except Exception:
        return False


_CANCEL_REQ_ALERTED = set()             # amazon_ids already alerted this run


def _cancel_requested_skip(amazon_id, asin="", sku=""):
    """A buyer asked to cancel: never bought. Logged, and one WhatsApp/Telegram alert per order per run so
    someone cancels it on Amazon (the placer itself never cancels an order)."""
    print(f"  🛑 {amazon_id}: the BUYER REQUESTED CANCELLATION — NOT placed. Cancel it on Amazon.")
    _log_order({"amazon_id": amazon_id, "asin": asin, "sku": sku, "status": "CANCEL_REQUESTED",
                "notes": "Amazon shows 'Buyer requested cancel' — not placed"})
    if amazon_id in _CANCEL_REQ_ALERTED:
        return
    _CANCEL_REQ_ALERTED.add(amazon_id)
    text = (f"Order {amazon_id} ({asin} {sku}) NOT placed — the buyer requested cancellation. "
            f"Cancel it in Seller Central (it does not count against the cancellation rate).")
    try:
        from utils.whatsapp import get_whatsapp_group, send_whatsapp_message
        # Owner's exact wording, 9 Nov 2026. Hathoro's go to its Orders Alerts group: the one thing there
        # that is not a placed order. Every other store's stay in the general group.
        to = get_whatsapp_group(STORE_ACCOUNT, kind="orders") if STORE_ACCOUNT == "Hathoro" else None
        send_whatsapp_message(f"🛑 Not Placing this Order {amazon_id}\n"
                              f"The buyer requested cancellation. Cancel krdun?", to=to)
    except Exception as e:
        print(f"  ⚠️ WhatsApp alert error: {e}")
    try:
        from utils.telegram import send_telegram_message
        send_telegram_message("🛑 <b>BUYER REQUESTED CANCEL</b>\n" + html.escape(text))
    except Exception as e:
        print(f"  ⚠️ Telegram alert error: {e}")


# "See all 2 orders from this buyer" on the order page opens Manage Orders searched by the buyer's e-mail,
# with each order's Seller order ID on its row (owner, 4 Nov 2026).
# Seller Central draws it as <a><span data-test-id="shipping-section-buyer-history">…</span></a> with NO href
# (every real page, 5-7 Nov 2026); clicking it loads orders-v3/search?qt=email&q=<buyer relay e-mail>&date-range=…
# in the same tab. The old reader looked only for a[href] / kat-link[href], never found it, and every one of the
# 17 orders it was asked about stayed "- D" (14 were repeat orders the owner then placed by hand with Manual-C).
_BUYER_ORDERS_SEL = '[data-test-id="shipping-section-buyer-history"]'
_BUYER_ORDERS_LINK_JS = r"""
    var re = /see\s+all\s+(?:(\d+)\s+)?orders?\s+from\s+this\s+buyer/i;
    function words(e) { return (e.innerText || e.textContent || e.getAttribute('label') || '').replace(/\s+/g, ' '); }
    function hit(e, m) {
        var a = e.closest('a[href], kat-link[href]');
        return {count: m[1] ? parseInt(m[1], 10) : 0,
                url: a ? new URL(a.getAttribute('href'), location.href).href : '', click: !a};
    }
    var els = document.querySelectorAll('a[href], kat-link[href]');
    for (var i = 0; i < els.length; i++) {
        var m = words(els[i]).match(re);
        if (m) return hit(els[i], m);
    }
    var s = document.querySelector(arguments[0]);
    if (s) { var m2 = words(s).match(re); if (m2) return hit(s, m2); }
    return null;
"""
_BUYER_ORDERS_TEXT_JS = r"""
    var re = /orders?\s+from\s+this\s+buyer/i, last = null;
    document.querySelectorAll('body *').forEach(function(e) { if (re.test(e.textContent || '')) last = e; });
    return last ? last.outerHTML.slice(0, 400) : '';
"""
_BUYER_LINK_WAIT_S = 6
_SC_ORDERS_PREFIX = "https://sellercentral.amazon.com/orders-v3/"


def _buyer_orders_link(driver):
    """{"count", "url", "click"} of the page's "See all N orders from this buyer", or None. url is '' and
    click True when Amazon drew it without an href (it must be clicked on the order page). Never raises."""
    try:
        link = driver.execute_script(_BUYER_ORDERS_LINK_JS, _BUYER_ORDERS_SEL)
    except Exception:
        return None
    if not link:
        return None
    if str(link.get("url") or "").startswith(_SC_ORDERS_PREFIX):
        return link
    if link.get("click"):
        return {"count": link.get("count") or 0, "url": "", "click": True}
    return None


def _click_buyer_orders(driver, opened):
    """Click "See all N orders from this buyer" on the order page this window stands on and follow it to the
    buyer's Manage Orders list, whether it loads here or in a new tab (that tab goes into `opened` so the
    caller closes it). Returns the list's URL, or '' when nothing led to one."""
    from selenium.webdriver.common.by import By
    els = driver.find_elements(By.CSS_SELECTOR, _BUYER_ORDERS_SEL)
    if not els:
        return ""
    before, url0 = set(driver.window_handles), driver.current_url
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", els[0])
        els[0].click()
    except Exception:
        driver.execute_script("arguments[0].click();", els[0])
    end = time.time() + 15
    while time.time() < end:
        time.sleep(0.5)
        new = [h for h in driver.window_handles if h not in before]
        if new:
            opened.extend(new)
            driver.switch_to.window(new[-1])
            end2 = time.time() + 10
            while not driver.current_url.startswith(_SC_ORDERS_PREFIX) and time.time() < end2:
                time.sleep(0.5)
            break
        if driver.current_url != url0 and "/orders-v3/order/" not in driver.current_url:
            break
    url = driver.current_url
    return url if url.startswith(_SC_ORDERS_PREFIX) and "/orders-v3/order/" not in url else ""


def _read_buyer_orders(driver, amazon_id, link=None):
    """This buyer's orders, read on a tab or window of its own, so the page the placer stands on (the order
    page in the classic loop, Zoro in rush and ultra) is exactly as it was afterwards. Ultra takes a window
    because a tab would hide the Zoro one. Returns rows, or None when they can't be read. Raises only if the
    placer's own window can't be got back, which is a dead browser and the order loop's to handle."""
    from utils.dup_check_local import parse_order_rows
    base = driver.current_window_handle
    worker = None
    opened = []                      # tabs a click may open: closed with the worker
    try:
        driver.switch_to.new_window("window" if ULTRA_MODE else "tab")
        worker = driver.current_window_handle

        def _open(url):
            safe_get(driver, url)
            if is_login_page(driver):
                auto_login(driver, PROFILE_NAME)
                safe_get(driver, url)
            if on_account_switcher(driver):
                choose_store_if_asked(driver, PROFILE_NAME, target_url=url)

        if not link or not link.get("url"):
            # A span to click (the link the scrape saw can't be clicked from here), not drawn yet when the
            # page was scraped, or never there: the order page, on this tab.
            _open(f"{AMAZON_BASE_ORDER_URL}{amazon_id}")
            link, end = None, time.time() + _BUYER_LINK_WAIT_S
            while not link and time.time() < end:
                time.sleep(0.5)
                link = _buyer_orders_link(driver)
            if not link:
                # If the words are there but no link was read, the log shows what Amazon drew instead.
                seen = driver.execute_script(_BUYER_ORDERS_TEXT_JS)
                print("   [DUP_OTHER] no 'See all orders from this buyer' link on the order page"
                      + (f"; the page has: {seen}" if seen else ""))
                return None
        if link.get("url"):
            _open(link["url"])
        else:
            url = _click_buyer_orders(driver, opened)
            if not url:
                print(f"   [DUP_OTHER] clicking 'See all orders from this buyer' led nowhere ({driver.current_url[:120]})")
                return None
            print(f"   [DUP_OTHER] opened the buyer's {link.get('count') or ''} orders".replace("  ", " "))
            if is_login_page(driver) or on_account_switcher(driver):
                _open(url)
        _wait_for_orders_to_load(driver, expected=min(int(link.get("count") or 0), 100), timeout=25)
        return parse_order_rows(driver.execute_script("return document.body.innerText || ''"))
    except Exception as e:
        print(f"   [DUP_OTHER] could not read the buyer's orders ({type(e).__name__}: {str(e)[:120]})")
        return None
    finally:
        for h in opened + [worker]:
            if h and h != base:
                try:
                    driver.switch_to.window(h)
                    driver.close()
                except Exception:
                    pass
        driver.switch_to.window(base)


def _dup_is_buyers_other_order(driver, amazon_id, existing_wb, link, duplicate_services,
                               street, zip_code, buyer, asin, sku):
    """The duplicate check matched by address. Does that WB only belong to this buyer's OTHER order?
    Owner, 4 Nov 2026: customers really do order the same thing again within a day or two, and each repeat
    was stamped "<WB> - D" and never bought. True only with proof for EVERY WB the check can match
    (utils.dup_check_local.wbs_on_other_orders). No link, a page that won't load, a list that won't parse,
    a search error: False, and the caller writes "- D" as it always has."""
    proved, why = False, ""
    try:
        from utils.dup_check_local import placer_wbs_for_orders, wbs_on_other_orders
        first = re.search(r"\bW[BW]\d{6,}\b", str(existing_wb or ""))
        if not first:
            return False
        print(f"   [DUP_OTHER] {first.group(0)} matched — is it on this buyer's other order?")
        matched = all_matched_wbs(duplicate_services, street, zip_code, buyer, asin, sku, amazon_id,
                                  first=first.group(0))
        if matched is None:
            why = "the matches can't all be named"
        else:
            rows = _read_buyer_orders(driver, amazon_id, link)
            if rows is None:
                why = "the buyer's orders could not be read"
            else:
                proved, why = wbs_on_other_orders(
                    matched, rows, amazon_id, placer_wbs_for_orders([r["amazon_id"] for r in rows]))
    except WebDriverException:
        raise                     # the placer's window is gone: the order loop's to handle
    except Exception as e:
        why = f"{type(e).__name__}: {str(e)[:120]}"
    print(f"   [DUP_OTHER] {'NOT a duplicate' if proved else 'the duplicate stands'} — {why}")
    _DLOG.log("dup_other_order", profile=PROFILE_NAME, amazon_id=amazon_id, asin=asin, sku=sku,
              action="cleared" if proved else "stands", reason=why)
    return proved


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
    if _buyer_requested_cancel(driver):
        return {"amazon_id": amazon_order_id, "status": "cancel_requested"}

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
        // "Shipping service: Second Day" — the buyer's chosen speed (Standard / Expedited / Second Day …)
        result.ship_service = extractDate(/Shipping service[:\s]*([A-Za-z][A-Za-z0-9 \-]{1,30})/i);

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

    # Again, now the page has had time to draw its banners (the alert can render after the order data).
    if _buyer_requested_cancel(driver):
        return {"amazon_id": amazon_order_id, "status": "cancel_requested"}

    result = {
        "amazon_id": amazon_order_id,
        "items": parsed_items,
        "merchant_order_id": info.get("merchant_order_id", ""),
        "address": addr,
        "purchase_date": info.get("purchase_date", ""),
        "ship_by_date": info.get("ship_by_date", ""),
        "deliver_by_date": info.get("deliver_by_date", ""),
        "ship_service": (info.get("ship_service") or "").strip(),
        "buyer_orders_link": _buyer_orders_link(driver),
    }
    
    return result


# ══════════════════════════════════════════════════════════════════
#  STEP 3: LOAD SKU MAP
# ══════════════════════════════════════════════════════════════════

_SKU_MAP_CACHE = {}
_SKU_MAP_LAST_LOAD = 0.0

def _get_sheets_service(force_new=False):
    """Build Google Sheets API v4 client (service account, cached — read-only)."""
    if force_new or not hasattr(_get_sheets_service, "_svc") or _get_sheets_service._svc is None:
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


def _extract_alt_skus(row_dict, zoro_code, asin):
    """Pull the alternative_SKU cell (any common header spelling) and validate it.
    The alternatives are the fallback Zoro codes used when the main code is OOS, tried left to right:
    one code, or several with commas — "G5803393,G5612117" (owner, 4 Nov 2026).
    Returns a list of G-codes, possibly empty — an invalid or self-referencing code is ignored
    (with a warning) so it never breaks the row's main mapping or the codes beside it."""
    raw = ""
    for key, val in row_dict.items():
        if str(key).strip().lower().replace(" ", "_") in _ALT_SKU_HEADERS:
            raw = str(val or "").strip()
            break
    alts = []
    for code in re.split(r"[,\s]+", raw):
        if not code or code in ("None", "N/A", "nan", "-"):
            continue
        if not re.match(r'^G\d+$', code):
            if code != "OOS":
                print(f"  ⚠️  Invalid alternative Zoro Code '{code}' for ASIN {asin} — ignored")
            continue
        if code != zoro_code and code not in alts:
            alts.append(code)
    return alts


def _execute_sheets_get(spreadsheetId, range_name, force_new=False):
    """Execute a Google Sheets values.get with automatic SSL reconnect retry."""
    for attempt in range(2):
        try:
            svc = _get_sheets_service(force_new=(force_new or attempt > 0))
            return svc.spreadsheets().values().get(
                spreadsheetId=spreadsheetId,
                range=range_name,
            ).execute()
        except Exception as e:
            if hasattr(_get_sheets_service, "_svc"):
                _get_sheets_service._svc = None
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise e


def load_sku_map():
    """Load ASIN→Zoro mapping via Google Sheets API (fast, 1-2s).
    Caches in memory for 5 minutes (300s) to prevent constant sheet querying.
    Falls back to CSV download if API fails."""
    global _SKU_MAP_CACHE, _SKU_MAP_LAST_LOAD
    now = time.time()
    if _SKU_MAP_CACHE and (now - _SKU_MAP_LAST_LOAD < 300):
        return dict(_SKU_MAP_CACHE)

    print("\n📦 Loading SKU map from Google Sheets...")
    try:
        result = _execute_sheets_get(GOOGLE_SHEET_ID, f"'{GOOGLE_SHEET_SKU_TAB}'!A:H")
        rows = result.get("values", [])
        if len(rows) < 2:
            print("  ⚠️  Sheet returned no data rows")
            return dict(_SKU_MAP_CACHE) if _SKU_MAP_CACHE else {}
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
                "alt_skus": _extract_alt_skus(d, zoro_code, asin),
            }
        print(f"  ✅ Loaded {len(sku_map)} ASIN→SKU mappings")
        _SKU_MAP_CACHE = sku_map
        _SKU_MAP_LAST_LOAD = time.time()
        return dict(_SKU_MAP_CACHE)
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
                'alt_skus': _extract_alt_skus(dict(row), zoro_code, asin),
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


def _label_for_locked_wb(amazon_id, wb, claim=None):
    """(label, is_own) for an order whose WB the placement lock already holds but Amazon does not show.

    The lock holds a WB for two different reasons. Nearly always it is this order's OWN purchase whose
    write-back never reached Amazon — the placer was stopped between "order placed" and the write
    (112-2660099-2409003, 10 Nov 2026: bought 04:22:25, stopped 04:22:28). That order needs the plain
    WB: the tracker skips every "- D" label, so with "<WB> - D" its tracking was never uploaded and the
    buyer got nothing. " - D" stays only where the WB is not proven to be this order's own: a claim
    recorded as "resolved as duplicate" (the WB belongs to the buyer's other order), a claim seeded from
    old log lines, or a claim that cannot be read."""
    note = ""
    try:
        info = claim if (claim and "note" in claim) else (plock.claim_info(amazon_id) or {})
        note = str(info.get("note") or "").strip().lower()
    except Exception:
        note = ""
    is_own = note.startswith("placed") or "zoro receipt verified" in note
    return _fit_seller_label(wb, "" if is_own else " - D"), is_own


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
        result = _execute_sheets_get(GOOGLE_SHEET_ID, f"'{GOOGLE_SHEET_PRIORITY_ASIN_TAB}'!A1:AZ500")
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
    pref = _store_prefix(_store_for_profile(profile_name)) or ("DM" if profile_name.upper().startswith("DM") else "HS")
    try:
        result = _execute_sheets_get(GOOGLE_SHEET_ID, f"'{GOOGLE_SHEET_PRIORITY_ASIN_TAB}'!A1:AZ500")
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


def _oos_released_asins():
    """ASINs a person has written in OOS_Release_HS / OOS_Release_DM on Priority_ASIN_Control: "it is back,
    place the orders stamped OOS" (owner, 2 Nov 2026). The name must not start with HS_ or DM_:
    load_store_sorted_asins reads every such column as "this ASIN is sorted", which would change routing.
    A missing column or a failed read releases nothing."""
    return {a.strip().upper() for a in (_load_asin_column_from_sheet(f"OOS_Release_{_STORE}", "OOS release") or [])}


def _oos_release_mark(order, released):
    """An order stamped OOS whose ASIN was released is treated from here on exactly as if it had never been
    stamped, so routing and every check apply as for any unlabelled order. Any other label stays."""
    if (str(order.get("asin") or "").strip().upper() in released
            and _normalize_merchant_order_id(order.get("seller_order_id")).upper() == "OOS"):
        order["seller_order_id"] = ""
        order["_oos_released"] = True
        return True
    return False


def _oos_release_open(order, detail):
    """The label of a scraped order page. A released order's OOS reads as no label; if the page now shows
    anything else (someone changed it since the list was read), that stands."""
    mid = str(detail.get("merchant_order_id") or "").strip()
    if order.get("_oos_released") and _normalize_merchant_order_id(mid).upper() == "OOS":
        detail["merchant_order_id"] = ""
        return ""
    return mid


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
# IT BYPASSES:
#   1. the Gmail address-duplicate search   (engine/orders.py, "Duplicate check")
#   2. the final margin / loss gate         (checkout_min_margin -> None)
#   3. the big-order '20' label ($800+)     (owner, 13 Oct 2026: "once I write Manual-C or R,
#      place it — no need to write 20"). Manual-R places at reseller price; Manual-C keeps the
#      regular coupons a big cart normally loses. If coupons are tried and none applies, a
#      Manual-C order WAITS with its tag (retried when the coupons change) and alerts once —
#      Manual-R places it without a coupon. With NO coupon loaded at all it is placed at list
#      price, as since 7 Sep (482884b). _big_order_regular_codes / _big_order_marks_20.
#   4. the delivery-time check                 (owner, 15 Oct 2026: Zoro's estimate >= 4 days past
#      Amazon's deliver-by → "Delivery time high", not placed — a tag places it anyway)
#   5. an unmapped ASIN, ONLY when a Zoro code follows the tag (owner, 9 Nov 2026: "Manual-C G5522807";
#      a tag with no code still stops at UNMAPPED_ASIN). _fill_unmapped_from_tag.
#
# "Manual-C <coupon>" (owner, 11 Nov 2026: the 20 % code Zoro gives by phone for a '20' order):
# the coupon profile tries THAT code and no other — not the sheet's priority or regular coupons, not
# the focus coupon. If Zoro does not accept it nothing is bought: the order keeps its tag, waits
# (COUPON_WAIT_MIN, or until another code is written) and the one Manual-C alert names the code.
# The code may stand with a Zoro code and a quantity, in either order: "Manual-C G7467735 x25 SAVE20NOW".
# Nothing is learned from it (no working pair, no promo strike cleared): it is one person's choice for
# one order. The profit gate stays waived as for every tagged order, so whatever the code takes off,
# the order is placed. Manual-R never carries a coupon. _parse_manual / _tag_coupon_codes.
# and, as a consequence of (1), the post-failure duplicate re-check before keep_failed(); a
# human's tag also skips the routing helpers (supervisor, Deciding hand-off, Sunday mode).
# It does NOT bypass the placement lock
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


def _delivery_gap_block_days():
    try:
        return int(((gui_cfg if "gui_cfg" in globals() else {}) or {}).get("delivery_gap_block_days", 4))
    except (TypeError, ValueError):
        return 4


def _as_date(text, year_hint=None):
    """'Fri, Oct 16, 2026' / 'Oct 16, 2026' / '2026-10-16' (anything after a '|' ignored) → date or None.
    With *year_hint*, a date written without a year ('Mon, Oct 12' — some ship dates) takes that year."""
    s = str(text or "").split("|")[0].strip()
    m = re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", s)      # also '2026-10-14T03:14'

    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            return None
    m = re.search(r"([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
        except ValueError:
            return None
    if year_hint:
        m = re.search(r"([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2})(?!\d)", s)
        if m:
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} {year_hint}", "%b %d %Y").date()
            except ValueError:
                return None
    return None


def _delivery_gap_hold(amazon_deliver_by, zoro_estimate, limit, ships_fast):
    """The delivery-time decision, pure: (hold, gap_days, why). *ships_fast* is called only when the gap
    reaches the limit, and returns (True | False | None, text) as _sku_ships_fast does — a SKU that just
    shipped fast is placed whatever Zoro's page says."""
    gap = _delivery_gap_days(amazon_deliver_by, zoro_estimate)
    if limit <= 0 or gap is None or gap < limit:
        return False, gap, ""
    fast, why = ships_fast()
    return (not fast), gap, why


_DELIVERY_GAP_ALERTED = set()           # amazon_ids already alerted this run (one alert per order)
_DELIVERY_UNKNOWN_TRIES = {}            # amazon_id -> tries with no Zoro delivery date (not placed)
_PAYMENT_DECLINED_TRIES = {}            # amazon_id -> cycles Zoro declined the card (not placed)
_CART_BUSY_TRIES = {}                   # amazon_id -> cycles Zoro said the cart was busy (not placed)
_SHARED_ACCOUNT_ALERTED = [0.0]         # last alert: this profile shares its Zoro account with another


def _notify_alt_main_oos(driver, main_skus, account=""):
    """After an order placed with the ALT SKU: a screenshot of each out-of-stock MAIN SKU's Zoro page,
    captioned '<code> OOS', to the store's orders group (WhatsApp) and Telegram (owner, 15 Oct 2026).
    Sent a few seconds after the order alert so it lands right below it. Never raises."""
    import threading
    shots = []
    for sku in [s for s in dict.fromkeys(main_skus or []) if s]:
        png = None
        try:
            safe_get(driver, f"https://www.zoro.com/search?q={sku}")
            time.sleep(4)
            if "/i/" not in (driver.current_url or ""):
                links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/i/']")
                if links:
                    safe_get(driver, links[0].get_attribute("href"))
                    time.sleep(4)
            png = driver.get_screenshot_as_png()
        except Exception as e:
            print(f"  ⚠️ Zoro page screenshot for {sku} failed: {e}")
        shots.append((sku, png))
    if not shots:
        return

    def _send():
        time.sleep(8)                       # after the order alert's own background send
        for sku, png in shots:
            cap = f"{sku} OOS"
            try:
                from utils.whatsapp import get_whatsapp_group, send_whatsapp_photo, send_whatsapp_message
                to = get_whatsapp_group(account, kind="orders")
                (send_whatsapp_photo(png, caption=cap, to=to, background=False) if png
                 else send_whatsapp_message(cap, to=to, background=False))
            except Exception as e:
                print(f"  ⚠️ WhatsApp OOS photo error: {e}")
            try:
                from utils.telegram import send_telegram_photo, send_telegram_message
                (send_telegram_photo(png, caption=cap, background=False) if png
                 else send_telegram_message(cap, background=False))
            except Exception as e:
                print(f"  ⚠️ Telegram OOS photo error: {e}")
    threading.Thread(target=_send, daemon=True, name="alt-main-oos").start()
    print(f"  📸 ALT order: Zoro page of {', '.join(s for s, _ in shots)} → '<code> OOS' sent after the order alert")


def _notify_delivery_gap(amazon_id, asin, sku, why, ship_by="", buyer="", city="", state=""):
    """One WhatsApp + Telegram alert per held order — the ship-by date keeps running while it waits."""
    if amazon_id in _DELIVERY_GAP_ALERTED:
        return
    _DELIVERY_GAP_ALERTED.add(amazon_id)
    loc = f"{city}, {state}".strip(", ")
    text = (f"Order {amazon_id} NOT placed — {why} ({asin} {sku}{', ' + buyer if buyer else ''}"
            f"{' (' + loc + ')' if loc else ''}). Labelled '{DELIVERY_GAP_LABEL}' on Amazon"
            f"{'; ship-by ' + ship_by if ship_by else ''}. Cancel it, or write Manual-C/-R to place it anyway.")
    try:
        from utils.whatsapp import send_whatsapp_message
        send_whatsapp_message("🐢 *DELIVERY TIME HIGH*\n" + text)
    except Exception as e:
        print(f"  ⚠️ WhatsApp alert error: {e}")
    try:
        from utils.telegram import send_telegram_message
        send_telegram_message("🐢 <b>DELIVERY TIME HIGH</b>\n" + html.escape(text))
    except Exception as e:
        print(f"  ⚠️ Telegram alert error: {e}")


def _delivery_gap_days(amazon_deliver_by, zoro_deliver_by):
    """Days Zoro's estimate lands after Amazon's deliver-by (negative = early); None if either is unknown."""
    a, z = _as_date(amazon_deliver_by), _as_date(zoro_deliver_by)
    return (z - a).days if (a and z) else None


def _zip5(addr):
    return re.sub(r"\D", "", str((addr or {}).get("zip_code") or ""))[:5]


def _zoro_api_delivery(driver, sku, qty, addr):
    """Zoro's delivery estimate for *sku* × *qty* to the buyer's ZIP ('YYYY-MM-DD', '' when unknown).
    Only for a SKU check_inventory() just called in stock — the API dates out-of-stock items too."""
    try:
        from platforms.zoro import get_expected_delivery
        d = get_expected_delivery(driver, [(sku, qty)], _zip5(addr)).get(sku, "")
        if d:
            print(f"  📅 Zoro's estimate for {sku} x{qty} to {_zip5(addr)}: delivery {d}")
        return d
    except Exception as e:
        print(f"  ⚠️ Zoro delivery estimate for {sku} skipped: {e}")
        return ""


def _recent_zoro_estimate(sku, max_age_days=3, db_path=None):
    """Rush mode skips the product page, so Zoro's estimate is unknown there. Reuse the lead time this
    SKU showed on a placement in the last few days (today + those days) — '' when there is none."""
    import sqlite3
    from datetime import date, timedelta
    try:
        if db_path is None:
            from utils.unified_db import DB_PATH as db_path
        c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            # ALT rows (main_sku set) carry the out-of-stock MAIN SKU's page date, not this SKU's
            row = c.execute("SELECT placed_at, zoro_deliver_by FROM order_placer_orders WHERE sku=? "
                            "AND COALESCE(zoro_deliver_by,'')!='' AND COALESCE(main_sku,'') IN ('', sku) "
                            "AND placed_at >= ? ORDER BY placed_at DESC LIMIT 1",
                            (sku, (date.today() - timedelta(days=max_age_days)).isoformat())).fetchone()
        finally:
            c.close()
        if not row:
            return ""
        placed, est = _as_date(row[0]), _as_date(row[1])
        if not (placed and est):
            return ""
        return (date.today() + (est - placed)).isoformat()
    except Exception:
        return ""


def _sku_ships_fast(sku, db_path=None, now=None):
    """Zoro's product-page date is often far too pessimistic: G5803393 and G1967707 said 'on or before'
    30 Oct-5 Nov and were delivered on 14 Oct, the day after ordering. Over the placements of 1-12 Oct,
    a hold on the date alone would have stopped 126 orders of which 80 Zoro shipped within 2 days. So a
    late date is believed only when this SKU has not just shipped fast. Walks its placements of the
    last 21 days, newest first (closing has the shipping e-mail within hours — 81 % of yesterday's
    orders already had one):
      (True, why)   the newest telling one shipped within 2 days — the date is not believed, placed
      (False, why)  it shipped later, or is 3+ days old and still unshipped — the date is believed
      (None, why)   nothing telling yet — the date is believed
    An order under 3 days old that has not shipped yet says nothing either way and is skipped."""
    import sqlite3
    from datetime import timedelta
    now = now or datetime.now()
    today = now.date()
    try:
        if db_path is None:
            from utils.unified_db import DB_PATH as db_path
        c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            rows = c.execute(
                "SELECT p.amazon_order_id, p.placed_at, COALESCE(NULLIF(co.zoro_shipped_pkt,''), co.shipped_email_time) "
                "FROM order_placer_orders p JOIN closing_orders co ON co.amazon_order_id = p.amazon_order_id "
                "WHERE p.sku = ? AND p.placed_at >= ? AND p.placed_at < ? ORDER BY p.placed_at DESC LIMIT 40",
                (sku, (today - timedelta(days=21)).isoformat(), today.isoformat())).fetchall()
        finally:
            c.close()
    except Exception as e:
        return None, f"shipping history unreadable ({e})"
    for oid, placed_at, shipped_at in rows:
        placed = _as_date(placed_at)
        if not placed:
            continue
        shipped = _as_date(shipped_at, year_hint=placed.year)       # 'Mon, Oct 12' has no year
        if shipped and shipped < placed - timedelta(days=7):
            shipped = shipped.replace(year=placed.year + 1)          # 'Fri, Jan 2' for a 30 Dec order
        if str(shipped_at or "").strip() and not shipped:
            continue                                                 # unreadable: says nothing
        if shipped:
            if (shipped - placed).days <= 2:
                return True, f"its order {oid} (placed {placed}) shipped {shipped}"
            return False, f"its order {oid} (placed {placed}) took until {shipped} to ship"
        if (today - placed).days >= 3:
            return False, f"its order {oid} (placed {placed}) has not shipped"
    return None, "no recent order of this SKU to judge by"


def _po_box_park(driver, amazon_id, addr, detected_by, defer=False):
    """Record a PO-box order's address, then label it PO_BOX_LABEL on Amazon (deferred in rush).

    12 Oct 2026: a Manual-R order to a PO box was scraped, refused and left untouched 285 times
    in four days — nothing ever moved it off the label the placer searches for (the run logs since
    June hold 39 such orders, one re-scraped 2,240 times). The address is saved FIRST, so a later
    label write cannot lose it; a write Amazon does not confirm is reported, not claimed — the order
    then comes back next cycle and the write is tried again."""
    addr = addr or {}
    street_line = ", ".join(x for x in (addr.get("street_address", ""), addr.get("street_address_2", ""),
                                        addr.get("city", ""), addr.get("state_abbr", ""), addr.get("zip_code", "")) if x)
    try:
        from utils.unified_db import record_po_box, set_po_box_label
        recorded = record_po_box({"amazon_order_id": amazon_id,
                                  "account": STORE_ACCOUNT,
                                  "profile": PROFILE_NAME, "street": addr.get("street_address", ""),
                                  "street2": addr.get("street_address_2", ""), "city": addr.get("city", ""),
                                  "state": addr.get("state_abbr", ""), "zip": addr.get("zip_code", ""),
                                  "detected_by": detected_by, "label_written": ""})
    except Exception as e:
        recorded, set_po_box_label = False, None
        print(f"  ⚠️  PO-box record failed for {amazon_id}: {e}")
    if not recorded:
        print(f"  ⚠️  PO-box address NOT saved (database busy) — {amazon_id}: {street_line}")
    if defer:
        _rush_defer_wb(amazon_id, PO_BOX_LABEL)
        ok = True
    else:
        try:
            ok = bool(update_amazon_seller_id(driver, amazon_id, PO_BOX_LABEL, PROFILE_NAME))
        except Exception as e:
            ok = False
            print(f"  ⚠️  could not write '{PO_BOX_LABEL}' on {amazon_id}: {e}")
    if ok and recorded and set_po_box_label:
        set_po_box_label(amazon_id, PO_BOX_LABEL)
    if ok:
        print(f"  📮 PO box — labelled '{PO_BOX_LABEL}' on Amazon{' (queued for the rush write-back)' if defer else ''}, "
              f"NOT placed (dashboard: Attention → PO boxes)")
    else:
        print(f"  📮 PO box — NOT placed; '{PO_BOX_LABEL}' write NOT confirmed — set it by hand on {amazon_id} "
              f"(it comes back next cycle and the write is tried again)")
    return ok


def _po_box_note_renamed(amazon_id, addr):
    """A PO-box order that IS being placed, its box renamed "#<number>" (owner, 10 Nov 2026): keep the
    address as Amazon showed it in po_box_orders so Attention → PO boxes lists what was rewritten.
    Best-effort — never in the way of the order."""
    orig = str((addr or {}).get("po_box_original") or "")
    if not orig:
        return
    try:
        from utils.unified_db import record_po_box
        o1, _, o2 = orig.partition(" | ")
        record_po_box({"amazon_order_id": amazon_id, "account": STORE_ACCOUNT, "profile": PROFILE_NAME,
                       "street": o1, "street2": o2, "city": addr.get("city", ""),
                       "state": addr.get("state_abbr", ""), "zip": addr.get("zip_code", ""),
                       "detected_by": "renamed", "label_written": ""})
    except Exception as e:
        print(f"  ⚠️  PO-box (renamed) record failed for {amazon_id}: {e}")


def _only_labelled(orders, label):
    """Keep the orders whose Seller order ID IS the label (case and spaces ignored).

    Amazon's order search is fuzzy, and when it drops the query it returns every unshipped
    order: 12 Oct 2026, 20:55, the 'MR' search came back with 1,004 orders and HS_COUPON set out
    to work all of them as its own. The Manual-C/-R pass already re-checks its tag; the main
    label search now does too — an order without this exact label is never this profile's."""
    want = str(label or "").strip().upper()
    if not want:
        return orders
    kept = [o for o in orders if str(o.get("seller_order_id") or "").strip().upper() == want]
    dropped = len(orders) - len(kept)
    if dropped:
        print(f"  🛡️  '{label}' search returned {dropped} order(s) WITHOUT that label — ignored "
              f"(Amazon's search is fuzzy or dropped the query); {len(kept)} kept")
    return kept


def _parse_manual_tag(merchant_id):
    """Return 'C' or 'R' if *merchant_id* is a manual-override tag, else None.

    Deliberately tolerant — a tired human types this into Amazon by hand, so
    "manual-c", "Manual C", "MANUAL_R ", "manual  -  R" all count. Anything else
    (including "Manual" with no suffix) is NOT a tag and is ignored, so a partial
    or mistyped label can never silently place an order.

    A Zoro code may follow the tag — "Manual-C G7467735" — to place that code instead of the
    sheet's (a colour / size the buyer agreed to; owner, 30 Oct 2026). _parse_manual_code reads it.
    A coupon code may follow "Manual-C" — "Manual-C SAVE20NOW" — to place the order with that
    coupon and no other (owner, 11 Nov 2026). _parse_manual_coupon reads it.
    """
    t = _parse_manual(merchant_id)
    return t["kind"] if t else None


_MT_HEAD = r"^\s*manual\s*[-_ ]?\s*([cr])"
_MT_ZORO = r"(G\d{5,12})(?:\s*[-_:,/]?\s*[x×*]\s*([1-9]\d{0,2}))?"
# A coupon needs a real gap before it: "Manual-Cancel" must never read as Manual-C + coupon "ancel".
_MT_GAP = r"(?:\s+|\s*[-_:,/=]\s*)"
_MT_COUPON = r"(?:coupon\s*[-_:,/=]?\s*)?([A-Za-z0-9]{4,24})"
_MANUAL_TAG_RE = re.compile(
    _MT_HEAD + rf"(?:\s*[-_:,/]?\s*{_MT_ZORO})?(?:{_MT_GAP}{_MT_COUPON})?\s*$", re.I)
_MANUAL_TAG_COUPON_FIRST_RE = re.compile(                      # "Manual-C SAVE20NOW G7467735 x25"
    _MT_HEAD + rf"{_MT_GAP}{_MT_COUPON}{_MT_GAP}{_MT_ZORO}\s*$", re.I)


def _parse_manual(merchant_id):
    """Everything a manual-override tag says, or None when the text is not a tag:
    {'kind': 'C' | 'R', 'code': Zoro code or '', 'qty': total quantity or 0, 'coupon': coupon or ''}.

    The coupon is kept as it was typed. It must hold a letter and be 4–24 letters / digits; a word
    that looks like a Zoro code ('G123'), a quantity ('x1000') or the word 'coupon' alone is not one,
    and Manual-R never carries one (a reseller profile applies no coupon). Any of those makes the
    whole label not a tag, so a mistyped label leaves the order alone."""
    s = str(merchant_id or "")
    m = _MANUAL_TAG_RE.match(s)
    if m:
        kind, code, qty, coupon = m.group(1), m.group(2), m.group(3), m.group(4)
    else:
        m = _MANUAL_TAG_COUPON_FIRST_RE.match(s)
        if not m:
            return None
        kind, coupon, code, qty = m.group(1), m.group(2), m.group(3), m.group(4)
    kind, coupon = kind.upper(), (coupon or "").strip()
    if coupon and (kind != "C" or not re.search(r"[A-Za-z]", coupon)
                   or re.fullmatch(r"[GgXx]\d+", coupon) or coupon.upper() == "COUPON"):
        return None
    return {"kind": kind, "code": (code or "").upper(), "qty": int(qty) if qty else 0, "coupon": coupon}


def _parse_manual_code(merchant_id):
    """The Zoro code written after a Manual-C/-R tag ('Manual-R G7467735' → 'G7467735'), else ''."""
    t = _parse_manual(merchant_id)
    return t["code"] if t else ""


def _parse_manual_qty(merchant_id):
    """The quantity written after a coded tag ('Manual-C G7467735 x25' → 25), else 0. It is only read
    after a code, 1–999: 'Manual-C x25', 'x0' or 'x1000' make the whole label not a tag, so a mistyped
    quantity leaves the order alone instead of buying the wrong amount (owner, 9 Nov 2026)."""
    t = _parse_manual(merchant_id)
    return t["qty"] if t else 0


def _parse_manual_coupon(merchant_id):
    """The coupon code written after a Manual-C tag ('Manual-C SAVE20NOW' → 'SAVE20NOW'), else ''."""
    t = _parse_manual(merchant_id)
    return t["coupon"] if t else ""


def _tag_coupon_codes(coupon):
    """What the checkout tries for a coupon written in the tag: the code as typed, then in capitals
    when that differs (Zoro's own codes are all capitals; the field is typed by hand)."""
    c = str(coupon or "").strip()
    return ([c] + ([c.upper()] if c.upper() != c else [])) if c else []


def _tag_coupon_refusal(last_fail, cache=None):
    """(why, transient) for a coupon written in the tag that did not apply, from the checkout's own
    counters. transient = Zoro's coupon service did not answer, which says nothing about the code."""
    lf = last_fail or {}
    g = lambda k: int(lf.get(k) or 0)
    if g("transient") or g("priority_transient"):
        return "Zoro's coupon service did not answer", True
    if g("item"):
        return "Zoro says it is not valid for this item", False
    if g("min_order"):
        mins = [float(v) for v in ((cache or {}).get("coupon_min_order") or {}).values() if v]
        return (f"the cart is under its ${max(mins):.2f} minimum" if mins else "the cart is under its minimum"), False
    if g("dead"):
        return "Zoro says the code does not exist or has expired", False
    return "Zoro did not accept it", False


def _big_order_regular_codes(regular_codes, raw_cart_cost, big_threshold, manual_override):
    """The regular (10 % EM) codes a checkout may try. A cart at/above *big_threshold* gets none —
    the team phones Zoro for 20 % instead — unless the order is tagged Manual-C: a human chose a
    coupon over the phone call (owner, 13 Oct 2026)."""
    if (raw_cart_cost or 0.0) >= big_threshold and not manual_override:
        return []
    return list(regular_codes or [])


def _big_order_marks_20(raw_cart_cost, big_threshold, manual_override):
    """True when a cart the coupons could not cover is labelled '20' (call Zoro for 20 %).
    Never for a Manual-C/-R order — the human already decided how it is placed."""
    return (raw_cart_cost or 0.0) >= big_threshold and not manual_override


_MANUAL_NO_COUPON_ALERTED = set()       # amazon_ids already alerted this run (one alert per order)


def _notify_manual_no_coupon(amazon_id, asin, sku, cart_cost, why, buyer="", city="", state="", tag_coupon=""):
    """One alert per order: a Manual-C order no coupon worked on waits — say so, and how to place it.
    tag_coupon: the coupon written in the tag (the only one tried) — one alert per order AND code, so a
    second code that is refused too is reported as well."""
    _key = f"{amazon_id}|{tag_coupon}" if tag_coupon else amazon_id
    if _key in _MANUAL_NO_COUPON_ALERTED:
        return
    _MANUAL_NO_COUPON_ALERTED.add(_key)
    loc = f"{city}, {state}".strip(", ")
    _who = f"(${cart_cost:.2f}, {asin} {sku}{', ' + buyer if buyer else ''}{' (' + loc + ')' if loc else ''})"
    if tag_coupon:
        text = (f"Manual-C order {amazon_id} NOT placed — the coupon {tag_coupon} written in its tag did not apply "
                f"{_who}. {why}. It keeps its tag. Write another code after Manual-C, plain Manual-C to use the "
                f"sheet's coupons, or Manual-R to place it without a coupon.")
    else:
        text = (f"Manual-C order {amazon_id} NOT placed — no coupon worked {_who}. {why}. It keeps its Manual-C tag and "
                f"is tried again when the coupons change. Write Manual-R to place it now without a coupon.")
    try:
        from utils.whatsapp import send_whatsapp_message
        send_whatsapp_message("🖐️ *MANUAL-C — NO COUPON*\n" + text)
    except Exception as e:
        print(f"  ⚠️ WhatsApp alert error: {e}")
    try:
        from utils.telegram import send_telegram_message
        send_telegram_message("🖐️ <b>MANUAL-C — NO COUPON</b>\n" + html.escape(text))
    except Exception as e:
        print(f"  ⚠️ Telegram alert error: {e}")


_INV_UNKNOWN = ("API_ERROR", "NO_DATA")


def _apply_manual_qty(item, qty):
    """'Manual-C G… x25': exactly *qty* units are bought for this line, however many Amazon sold.
    final_qty is what gets bought; the two pack fields only go to the log and are kept in step."""
    if qty:
        item["final_qty"] = qty
        item["qty_multiplier"] = qty / item["order_qty"]
        item["zoro_multiple"] = 1
        item["manual_qty"] = True


def _swap_manual_code(item, code, qty=0):
    """Buy *code* for this item instead of the sheet's Zoro code ('Manual-C/-R G…' tag). The sheet's
    pack size stays unless the tag gives a quantity; the sheet's ALT is dropped — a person chose this code."""
    item["sheet_sku"] = item["sku"]
    item["sku"] = code
    item["alt_skus"] = []
    item["manual_code"] = True
    _apply_manual_qty(item, qty)


def _manual_code_notes(items):
    return [f"MANUAL CODE: placed {it['sku']} for {it['asin']} ("
            + (f"sheet {it['sheet_sku']}" if it.get("sheet_sku") else "not in the SKU sheet")
            + (f", qty {it['final_qty']}" if it.get("manual_qty") or not it.get("sheet_sku") else "") + ")"
            for it in items if it.get("manual_code")]


def _order_fields(items):
    """asin_field, sku_field, qty_field for the order log — one item plain, several joined."""
    if len(items) == 1:
        return items[0]["asin"], items[0]["sku"], items[0]["final_qty"]
    return (", ".join(it["asin"] for it in items),
            ", ".join(f"{it['sku']} x{it['final_qty']}" for it in items),
            ", ".join(str(it["final_qty"]) for it in items))


def _manual_multi_item_plan(items, code, in_stock, qty=0):
    """A Manual-C/-R multi-item order: what to buy. *in_stock(sku, qty)* → (cartable, status).
    Returns (keep, dropped, problem); dropped = [(item, status)].
      • Zoro code in the tag: it replaces the ONE item that is out (main and every ALT out). None or
        several out → *problem*: not placed, a person must say which item the code is for.
      • No code: buy what is in stock, drop the rest — the buyer was told and agreed to get the other
        item later (owner, 30 Oct 2026). All in stock or all out → unchanged; the normal flow decides.
    An inventory API error on any item → unchanged: never split an order on a failed check."""
    out = []
    for it in items:
        ok, st = in_stock(it["sku"], it["final_qty"])
        if not ok and st not in _INV_UNKNOWN:
            for alt in it.get("alt_skus") or []:
                ok, st = in_stock(alt, it["final_qty"])
                if ok or st in _INV_UNKNOWN:
                    break
        if not ok and st in _INV_UNKNOWN:
            return list(items), [], None
        if not ok:
            out.append((it, st))
    if code:
        if len(out) == 1:
            _swap_manual_code(out[0][0], code, qty)
            return list(items), [], None
        names = ", ".join(f"{it['asin']} {it['sku']}" for it, _ in out)
        why = f"{len(out)} items are out ({names})" if out else "every item is in stock"
        return list(items), [], f"cannot tell which item {code} replaces — {why}"
    if out and len(out) < len(items):
        gone = {id(it) for it, _ in out}
        return [it for it in items if id(it) not in gone], out, None
    return list(items), [], None


_MANUAL_CODE_ALERTED = set()            # amazon_ids already alerted this run


def _notify_manual_code_unclear(amazon_id, why):
    """One alert per order: a 'Manual-C/-R G…' multi-item order the placer cannot map the code onto."""
    if amazon_id in _MANUAL_CODE_ALERTED:
        return
    _MANUAL_CODE_ALERTED.add(amazon_id)
    text = (f"Order {amazon_id} NOT placed — {why}. It keeps its tag and is tried again every cycle; "
            f"remove the code to place only the items in stock, or place it by hand.")
    try:
        from utils.whatsapp import send_whatsapp_message
        send_whatsapp_message("🖐️ *MANUAL CODE — WHICH ITEM?*\n" + text)
    except Exception as e:
        print(f"  ⚠️ WhatsApp alert error: {e}")
    try:
        from utils.telegram import send_telegram_message
        send_telegram_message("🖐️ <b>MANUAL CODE — WHICH ITEM?</b>\n" + html.escape(text))
    except Exception as e:
        print(f"  ⚠️ Telegram alert error: {e}")


def _dup_coupon_lines(asins, store, sheet_codes, verdicts=None):
    """The Seller Notes text for a '<WB> - D' order: one 'C active / C none / C untested' line per item."""
    from engine import priority_check as _pc
    asins = [a for a in dict.fromkeys(asins or []) if a]
    if sheet_codes is None:
        lines = ["C unknown - the Coupons tab could not be read"] * len(asins)
    else:
        verdicts = _pc.load_verdicts() if verdicts is None else verdicts
        lines = [_pc.coupon_status_note(a, store, sheet_codes, verdicts) for a in asins]
    if len(asins) > 1:
        lines = [f"{a}: {l}" for a, l in zip(asins, lines)]
    return "\n".join(lines)


def _dup_coupon_note(driver, amazon_id, asins):
    """After '<WB> - D': say in the order's Seller Notes whether a priority coupon works on the item, so
    whoever reviews the duplicate can write Manual-C or Manual-R straight away (owner, 30 Oct 2026).
    Added below any note already there. Never raises — the duplicate label is what matters.
    Not for a reseller-only store (Wrenchly): there is no Manual-C there to choose."""
    if not HAS_COUPON_SIBLING:
        return
    try:
        codes = _sheet_priority_codes()
        text = _dup_coupon_lines(asins, _STORE, None if _SHEET_PRI["codes"] is None else codes)
        if text:
            ok = write_seller_note(driver, amazon_id, note_text=text, skip_navigate=True, append=True)
            print(f"  🏷️  Seller Notes: {text.replace(chr(10), ' | ')}" + ("" if ok else "  ⚠️ not written"))
    except Exception as e:
        print(f"  ⚠️ coupon status note for {amazon_id} skipped: {e}")


def _manual_tag_is_for_profile(kind, profile_name):
    """True if a '-C'/'-R' tag belongs to *profile_name* (…COUPON / …RESELLER)."""
    want = _MANUAL_KIND_SUFFIX.get((kind or "").upper())
    return bool(want) and (profile_name or "").upper().endswith(want)


def _queue_manual_from_view(order, scraped_orders, seen_ids):
    """A buyer-action-view order carrying a Manual-C/-R tag: queue it if the tag is this profile's.
    True when it carries a tag at all, since the caller has nothing else to do with such an order."""
    kind = _parse_manual_tag((order.get("seller_order_id") or "").strip())
    if not kind:
        return False
    if _manual_tag_is_for_profile(kind, PROFILE_NAME):
        order["_label_matched"] = order.get("seller_order_id", "").strip()
        order["_margin_thresh"] = MIN_MARGIN
        scraped_orders.append(order)
        seen_ids.add(order["amazon_id"])
        print(f"    🖐️  {order['amazon_id']}: '{order['_label_matched']}' found in the buyer-action view")
    return True


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
                "seller_sku": str(item.get("seller_sku") or "").strip(),
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
            "alt_skus": list((sku_info or {}).get("alt_skus") or []),
            "alt_used": False,
            "main_sku": "",
            "final_qty": final_qty,
        })

    return resolved_items, missing_items


_SKU_PACK_RE = re.compile(r"(?<![0-9A-Za-z])(\d{1,2})\s*-?\s*(?:pk|pack)\b", re.I)


def _fill_unmapped_from_tag(resolved, missing, code, qty=0):
    """'Manual-C G5522807' on an order line with no row in the SKU sheet: a person chose the code, so the
    missing mapping is no reason to stop (owner, 9 Nov 2026: some listings stay unmapped on purpose, because
    the right Zoro code differs from order to order). Only with exactly ONE unmapped line, or the code could
    be meant for any of them. No sheet row means no pack size: a quantity in the tag ('x25') is bought as
    written, otherwise it comes from the Amazon SKU's '-4pk' ending (matched what was bought on 97 % of past
    orders), else 1 per unit. No ALT, no 2nd part.
    Returns (resolved, missing, code still to use): the code is spent here, the swap below must not reuse it."""
    if not code or len(missing) != 1 or not missing[0].get("asin"):
        return resolved, missing, code
    line = missing[0]
    m = _SKU_PACK_RE.search(line.get("seller_sku") or "")
    pack = max(int(m.group(1)), 1) if m else 1
    item = _build_resolved_order_items([line], {line["asin"]: {"zoro_code": code, "qty_multiplier": pack}})[0][0]
    item.update(manual_code=True, sheet_sku="")
    _apply_manual_qty(item, qty)
    print(f"  🖐️  {line['asin']} is not in the SKU sheet — buying {code} x{item['final_qty']} from the tag "
          + ("(quantity written in the tag)" if qty else f"(pack of {pack}, Amazon qty {line['qty']})"))
    return resolved + [item], [], ""


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


def _evaluate_order_strategy(resolved_items, priority_coupons, unit_price, list_total=None):
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
    # Zoro decides the $50 free-shipping threshold on the LIST price, before the Zoro Select
    # discount and before any coupon. Proven on 1,251 orders (list >= $50, under $50 after the
    # discount): every one shipped free. `unit_price` here is already Select-discounted on
    # reseller profiles, so the caller passes the list total separately.
    ship_basis = list_total if list_total else total

    # ── Find best percentage coupon for profit estimation ───────
    best_pct = None
    for c in (priority_coupons or []):
        if "pct" in c:
            if best_pct is None or c["pct"] > best_pct["pct"]:
                best_pct = c

    ship = ZORO_SMALL_ORDER_FEE if ship_basis < 50.0 else 0.0
    strategy["orders"] = [{"qty": final_qty}]
    strategy["estimated_shipping"] = ship

    if best_pct:
        single_pct_cost = total * (1 - best_pct["pct"] / 100.0)
        single_pct_cost += ship          # a coupon never makes Zoro charge shipping — list decides
        strategy["estimated_cost"] = single_pct_cost
        strategy["discount_description"] = f"Single order + {best_pct['code']} ({best_pct['pct']:.0f}%)"
        strategy["preferred_coupon"] = best_pct["code"]
        print(f"   [STRATEGY] {strategy['discount_description']}")
        print(f"   [STRATEGY] Est. cost: ${single_pct_cost:.2f}")
    else:
        strategy["estimated_cost"] = total + ship
        print(f"   [DEBUG] No percentage coupon — full price ${strategy['estimated_cost']:.2f}")

    return strategy


def _notify_big_order_20(amazon_id, asin, sku, cart_cost, buyer="", city="", state="", title=""):
    """Send WhatsApp and Telegram alert when an order >= $800 is marked '20'."""
    loc = f"{city}, {state}".strip(", ")
    binfo = f"{buyer} ({loc})" if (buyer or loc) else "Customer"

    title = str(title or "").strip()
    sku = str(sku or "").strip()
    if (not title or not sku or sku in ("N/A", "None", "")) and asin:
        try:
            from utils.telegram import _lookup_title_and_sku
            db_t, db_s = _lookup_title_and_sku(asin)
            title = title or db_t
            sku = sku or db_s
        except Exception:
            pass

    title_str = title[:80] if title else "Product Listing"
    sku_str = sku if sku else "N/A"

    wa_msg = (
        f"🚨 *BIG ORDER ($800+) — MARKED '20'*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Title: *{title_str}*\n"
        f"• SKU: `{sku_str}`\n"
        f"• ASIN: `{asin}`\n"
        f"📦 *Order ID*: `{amazon_id}`\n"
        f"💰 *Zoro Purchase*: *${cart_cost:.2f}*\n"
        f"👤 *Buyer*: {binfo}\n"
        f"📝 *Amazon Tag*: Marked *'20'* in Seller Order ID\n"
        f"📞 *Action*: No priority coupon applies. Call Zoro customer service for manual 20% phone discount!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    tg_msg = (
        f"🚨 <b>BIG ORDER ($800+) — MARKED '20'</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Title: <b>{html.escape(title_str)}</b>\n"
        f"• SKU: <code>{sku_str}</code>\n"
        f"• ASIN: <code>{asin}</code>\n"
        f"📦 <b>Order ID</b>: <code>{amazon_id}</code>\n"
        f"💰 <b>Zoro Purchase</b>: <b>${cart_cost:.2f}</b>\n"
        f"👤 <b>Buyer</b>: {binfo}\n"
        f"📝 <b>Amazon Tag</b>: Marked <b>'20'</b> in Seller Order ID\n"
        f"📞 <b>Action</b>: No priority coupon applies. Call Zoro customer service for manual 20% phone discount!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    try:
        from utils.whatsapp import send_whatsapp_message
        send_whatsapp_message(wa_msg)
    except Exception as e:
        print(f"  ⚠️ WhatsApp alert error: {e}")
    try:
        from utils.telegram import send_telegram_message
        send_telegram_message(tg_msg)
    except Exception as e:
        print(f"  ⚠️ Telegram alert error: {e}")


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

    # ── Coupon-wait parking: an order waiting for a coupon is NOT rebuilt every cycle ──
    # (one order was carted and coupon-tested 66 times in a night). It is retried when the
    # cooldown passes OR as soon as the coupon pool changes (new scrape / new priority code).
    # A coupon written in the tag since ("Manual-C SAVE20NOW"), or a different one, is tried now.
    _cw = _COUPON_WAITS.get(amazon_id)
    if _cw and COUPON_WAIT_MIN > 0:
        if (_coupon_pool_sig(coupon_codes) == _cw.get("pool") and time.time() - _cw["last"] < COUPON_WAIT_MIN * 60
                and _cw.get("tag_coupon", "") == _parse_manual_coupon(merchant_id)):
            if not _cw.get("announced"):
                _cw["announced"] = True
                print(f"  ⏸️  {amazon_id}: {_cw.get('reason', 'waiting for a coupon')} — parked "
                      f"{COUPON_WAIT_MIN:.0f} min or until the coupon list changes")
                _log_order({"amazon_id": amazon_id, "status": "COUPON_WAIT_PARKED",
                            "notes": f"{_cw.get('reason', '')}; retried after {COUPON_WAIT_MIN:.0f} min or when coupons change"})
            return "skipped"
        _COUPON_WAITS.pop(amazon_id, None)          # pool changed or cooldown over → try again now

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
            # Amazon's seller-id (else the list scrape would have skipped it), so write it now:
            # the plain WB when the lock recorded this order's own purchase, "<WB> - D" otherwise
            # (see _label_for_locked_wb). Either way it stops the order being re-scraped every
            # cycle. This is a LABEL write only — it never re-buys.
            _wb = (_ci.get("wb") or "").strip()
            if _wb.startswith("WB") or _wb.startswith("WW"):
                _dlabel, _own = _label_for_locked_wb(amazon_id, _wb, _ci)
                _ok = update_amazon_seller_id(driver, amazon_id, _dlabel, PROFILE_NAME)
                print(f"  ⏭️  {amazon_id} already placed ({_wb}) — marked Amazon '{_dlabel}'"
                      + (" (its own order: the earlier write-back never landed)" if _own else "")
                      + ("" if _ok else "  ⚠️ write not verified — set it by hand"))
                _log_order({"amazon_id": amazon_id, "status": "WB_RESTORED" if _own else "DUPLICATE", "wb_id": _wb,
                            "notes": ("already placed as this order — WB written to Amazon again by local lock"
                                      if _own else "already placed — marked '<WB> - D' on Amazon by local lock")})
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
    _manual_code = _parse_manual_code(merchant_id) if manual_override else ""
    _manual_qty = _parse_manual_qty(merchant_id) if manual_override else 0
    _manual_coupon = _parse_manual_coupon(merchant_id) if manual_override else ""
    if _manual_code:
        print(f"  🖐️  Zoro code in the tag: {_manual_code} — placed instead of the sheet's code"
              + (f", quantity {_manual_qty} as written" if _manual_qty else ""))
    if _manual_coupon:
        print(f"  🖐️  Coupon in the tag: {_manual_coupon} — the only coupon tried on this order; "
              f"if Zoro does not accept it, the order is NOT placed")

    # ── SUNDAY MODE: an order already held is not re-priced (a Manual tag always goes through) ──
    if not manual_override and _sunday_parked(amazon_id, coupon_codes):
        return "skipped"

    # ── Already processed? ────────────────────────────────────────
    if merchant_id and merchant_id.startswith("WB"):
        print(f"  ⏭️  Already has WB ID: {merchant_id} — skipping")
        return "skipped"

    if merchant_id and merchant_id.upper() in ("OOS", "DISCONTINUED", "DUPLICATE", "SR", "NET30", "20",
                                               DELIVERY_GAP_LABEL.upper()):
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
    resolved_items, missing_items, _manual_code = _fill_unmapped_from_tag(
        resolved_items, missing_items, _manual_code, _manual_qty)
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

    # "Manual-C G7467735": the buyer agreed to another colour / size — buy that Zoro code, with the
    # sheet's pack size, and no ALT (the human chose the code). A multi-item order is resolved once
    # Zoro's stock is known (_manual_multi_item_plan, below): the code replaces the item that is out.
    if _manual_code and len(resolved_items) == 1:
        _swap_manual_code(resolved_items[0], _manual_code, _manual_qty)

    primary_item = resolved_items[0]
    is_multi_item = len(resolved_items) > 1
    asin_field, sku_field, qty_field = _order_fields(resolved_items)
    order_notes = []
    if is_multi_item:
        order_notes.append(f"Multi-item order ({len(resolved_items)} items)")
    order_notes += _manual_code_notes(resolved_items)
    if _manual_coupon:
        order_notes.append(f"Coupon from the tag: {_manual_coupon}")
    order_note = "; ".join(order_notes)

    _print_section("ORDER DETAILS")
    _print_kv("Amazon ID", amazon_id)
    for index, item in enumerate(resolved_items, start=1):
        label = "Item" if not is_multi_item else f"Item {index}"
        _print_kv(label, f"{item['asin']} -> {item['sku']} x{item['final_qty']}")
        if item["second_sku"]:
            second_label = "2nd Part" if not is_multi_item else f"2nd Part {index}"
            _print_kv(second_label, item["second_sku"])

    # ── Focus Priority Coupon guard (per-order check) ─────────────
    # If "Place with Priority" is active for this profile, ONLY process orders that match the focus coupon.
    # If the user toggled it mid-cycle or an order was already queued, skip/defer any non-matching order immediately.
    _foc_q = _focus_query()
    if _foc_q and IS_COUPON_PROFILE and not manual_override:
        _pri_loaded = coupon_codes[0] if isinstance(coupon_codes, tuple) else []
        _foc_codes = _focus_codes(_foc_q, _pri_loaded)
        if _foc_codes:
            from engine import priority_check as _pc
            verdicts = _pc.load_verdicts()
            v = verdicts.get(primary_item.get("asin")) if primary_item.get("asin") else None
            d = _focus_decision(v, _foc_codes, time.time())
            if d == "skip":
                print(f"  ⏭️ Focus coupon active ({', '.join(_foc_codes)}): {primary_item.get('asin')} does not match focus coupon — deferred")
                return "skipped"
            elif d == "probe":
                probe_mem = _focus_probe_cache()
                ckey = "|".join(_foc_codes)
                pr = probe_mem.get(f"{primary_item.get('asin')}|{ckey}")
                now = time.time()
                if pr and now - float(pr.get("at") or 0) < FOCUS_PROBE_TTL_S and not pr.get("ok"):
                    print(f"  ⏭️ Focus coupon active ({', '.join(_foc_codes)}): {primary_item.get('asin')} tested earlier (no discount) — deferred")
                    return "skipped"
    
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
        if addr_raw and reason == "PO_BOX":
            _po_box_park(driver, amazon_id, addr_raw, "amazon_address", defer=defer_amazon_writes)
            _log_order({
                "amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                "status": "PO_BOX",
                "notes": "; ".join(filter(None, [f"PO box — labelled {PO_BOX_LABEL}, not placed", order_note])),
            })
            return "skipped"
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
    addr["_ship_service"] = order_detail.get("ship_service", "")
    # The checkout page re-checks delivery against Zoro's own "Delivery expected by" for THIS
    # address and THIS cart (checkout/true_api.py). 0 = off; a Manual-C/-R tag turns it off.
    addr["_delivery_gap_limit"] = 0 if manual_override else _delivery_gap_block_days()
    addr["_rush_mode"] = bool(defer_amazon_writes)
    addr["_ultra_mode"] = ULTRA_MODE
    if ULTRA_MODE:
        driver._zuhra_ultra = True           # read by platforms.zoro.zoro_api and the checkout
    
    city = addr.get("city", "?")
    state = addr.get("state_abbr", "?")
    zipcode = addr.get("zip_code", "?")
    buyer = addr.get("name", "?")
    _print_full_address(addr)
    if addr.get("po_box_original"):
        # A PO-box address: placed with the box renamed "#<number>" (owner, 10 Nov 2026), not labelled BB
        _print_kv("PO box", f"renamed — Amazon shows: {addr['po_box_original']}")
        _po_box_note_renamed(amazon_id, addr)
        order_note = "; ".join(filter(None, [order_note, "PO box renamed for Zoro"]))

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

    # ── HARD RULE (owner, 8 Sep 2026): an order is NEVER placed without a known profit ──
    # No earnings (Gmail service down, email not parsed, zero) → no checkout, no blind buy,
    # whatever the label or override says. The order is retried next cycle.
    try:
        _earn_ok = earnings is not None and float(earnings) > 0
    except (TypeError, ValueError):
        _earn_ok = False
    if not _earn_ok:
        print(f"  ⛔ Earnings unknown ({earnings!r}) — never placed blind; retried when the 'Sold, ship now' data is available")
        _log_order({"amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                    "buyer": buyer, "city": city, "state": state, "status": "EARNINGS_UNKNOWN",
                    "notes": "no earnings data at checkout time — never placed blind (owner's rule)"})
        return "pending"

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
                customer_name=buyer,
                current_asin=asin_field,
                current_sku=sku_field,
                current_amazon_id=amazon_id)
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
        if existing_wb and _dup_is_buyers_other_order(
                driver, amazon_id, existing_wb, order_detail.get("buyer_orders_link"), duplicate_services,
                addr["street_address"], addr.get("zip_code", ""), buyer, asin_field, sku_field):
            _log_order({
                "amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                "buyer": buyer, "city": city, "state": state,
                "status": "DUP_OTHER_ORDER",
                "notes": f"{existing_wb} matched by address but is on this buyer's other order — not a duplicate",
            })
            existing_wb = None
        if existing_wb:
            dup_label = _fit_seller_label(existing_wb, " - D")
            print(f"  🔁 DUPLICATE — already ordered: {existing_wb}")
            update_amazon_seller_id(driver, amazon_id, dup_label, PROFILE_NAME,
                                    skip_navigate=True)
            _dup_coupon_note(driver, amazon_id, [it["asin"] for it in resolved_items])
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

    # ── Manual-C/-R on a multi-item order: buy what is in stock / put the tag's code on the item
    #    that is out (owner, 30 Oct 2026). Decided here, on Zoro's stock, before anything is carted.
    _partial = False
    if manual_override and len(resolved_items) > 1:
        _keep, _dropped, _problem = _manual_multi_item_plan(
            resolved_items, _manual_code, lambda s, q: check_inventory(driver, s, q), _manual_qty)
        if _problem:
            print(f"  ⏸️  MANUAL CODE: {_problem} — NOT placed (tag kept, tried again next cycle)")
            _log_order({"amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                        "buyer": buyer, "city": city, "state": state, "status": "MANUAL_CODE_UNCLEAR",
                        "notes": "; ".join(filter(None, [_problem, order_note]))})
            _notify_manual_code_unclear(amazon_id, _problem)
            return "skipped"
        for _it, _st in _dropped:
            log_price_or_status(_it["sku"], current_price=0.0, status="OOS")
            order_notes.append(f"PARTIAL: {_it['asin']} not placed ({_it['sku']}: {_st}) — buyer agreed")
            print(f"  ✂️  PARTIAL (Manual-{_manual_kind}): {_it['asin']} {_it['sku']} is {_st} — "
                  f"left out; buying the other item(s)")
        order_notes += [n for n in _manual_code_notes(_keep) if n not in order_notes]
        _partial = bool(_dropped)
        resolved_items = _keep
        primary_item = resolved_items[0]
        is_multi_item = len(resolved_items) > 1
        asin_field, sku_field, qty_field = _order_fields(resolved_items)
        order_note = "; ".join(order_notes)
        note_amazon_sku = (primary_item.get("seller_sku") or "").strip() or (original_sku or "")

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
    _pcart = False      # is the primary SKU itself in stock for this quantity? (the delivery check needs it)
    _zsrc0 = "Zoro product page"
    try:
        if resolved_items:
            from platforms.zoro import get_zoro_delivery_date, last_inventory_qty
            _psku = resolved_items[0]["sku"]
            _pcart, _ = check_inventory(driver, _psku, resolved_items[0]["final_qty"])  # + stock cache
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
            # Zoro's own estimate for the buyer's ZIP and quantity (the call behind the page's line) —
            # preferred over the page, which dates the Chrome's saved ZIP and is sometimes not rendered
            # in time. One API call, no page, so rush mode gets a date too. Only for an in-stock SKU:
            # the API dates anything, backordered or not.
            if _pcart:
                _api = _zoro_api_delivery(driver, _psku, resolved_items[0]["final_qty"], addr)
                if _api:
                    addr["_zoro_deliver_by"], _zsrc0 = _api, f"Zoro's estimate for ZIP {_zip5(addr)}"
    except Exception as _e:
        print(f"  ⚠️ Zoro estimate capture skipped: {_e}")

    # ── Zoro would deliver too late for Amazon's promise → not placed, labelled (owner, 15 Oct 2026) ──
    # Before the cart and before the placement lock is taken, so nothing has to be undone. Only when the
    # primary SKU itself is in stock: an out-of-stock main's page shows its backorder date, and the cart
    # flow below either places the in-stock ALT or labels it OOS exactly as before (13 Oct: 27 ALT orders
    # for G105340985 → G1967707 would otherwise have been held, all delivered on time). Rush mode has no
    # page estimate; it reuses this SKU's lead time from the last 3 days if there is one. No estimate at
    # all → the order goes ahead as before. A SKU that just shipped fast is placed whatever the page says.
    # A Manual-C/-R tag places it anyway. The same check runs again on an ALT swap (in the cart loop below),
    # with the ALT's own date — until 27 Oct 2026 the swap only dropped the main's date and the order then
    # sat in DELIVERY_UNKNOWN on every cycle (27 orders, 218 tries, 24–26 Oct).
    _gap_limit = _delivery_gap_block_days()

    def _delivery_gate_holds(_gsku, _zest, _zsrc, _sku_text):
        """True when the order was held (labelled, logged, alerted) — the caller returns 'skipped'."""
        if not _zest and defer_amazon_writes:
            _zest, _zsrc = _recent_zoro_estimate(_gsku), "this SKU's lead time in the last 3 days"
        _hold, _gap, _hist = _delivery_gap_hold(order_detail.get("deliver_by_date", ""), _zest, _gap_limit,
                                                lambda: _sku_ships_fast(_gsku))
        if not _hold and _hist:
            print(f"  🐇 Zoro's date {_zest} is {_gap} days past Amazon's deliver-by, but {_hist} — "
                  f"Zoro's estimate is not believed, placing")
        if not _hold:
            return False
        _why = (f"Zoro delivers by {_zest} ({_zsrc}) — {_gap} days after Amazon's deliver-by "
                f"{order_detail.get('deliver_by_date', '')}; {_hist}")
        if defer_amazon_writes:
            _rush_defer_wb(amazon_id, DELIVERY_GAP_LABEL)
            _ok = True
        else:
            try:
                _ok = bool(update_amazon_seller_id(driver, amazon_id, DELIVERY_GAP_LABEL, PROFILE_NAME))
            except Exception as _e:
                _ok = False
                print(f"  ⚠️  could not write '{DELIVERY_GAP_LABEL}' on {amazon_id}: {_e}")
        print(f"  🐢 DELIVERY TIME HIGH: {_why} — NOT placed; "
              + (f"labelled '{DELIVERY_GAP_LABEL}' on Amazon" if _ok else f"'{DELIVERY_GAP_LABEL}' write NOT confirmed — set it by hand")
              + " (write Manual-C/-R to place it anyway)")
        _log_order({"amazon_id": amazon_id, "asin": asin_field, "sku": _sku_text, "qty": qty_field,
                    "buyer": buyer, "city": city, "state": state, "status": "DELIVERY_TIME_HIGH",
                    "notes": "; ".join(filter(None, [_why + f" — labelled {DELIVERY_GAP_LABEL}, not placed", order_note]))})
        _notify_delivery_gap(amazon_id, asin_field, _sku_text,
                             f"Zoro delivers by {_zest}, {_gap} days after Amazon's deliver-by "
                             f"{order_detail.get('deliver_by_date', '')}", order_detail.get("ship_by_date", ""),
                             buyer, city, state)
        return True

    if _gap_limit > 0 and not manual_override and resolved_items and _pcart:
        if _delivery_gate_holds(resolved_items[0]["sku"], addr.get("_zoro_deliver_by") or "", _zsrc0, sku_field):
            return "skipped"

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
        alt_tried = []
        if not cartable and item.get("alt_skus") and inv_status not in ("API_ERROR", "NO_DATA"):
            # Main SKU genuinely OOS and the sheet lists alternatives — try them left to right
            # before marking the order OOS. The main SKU's OOS still goes to the price tracker
            # so "is the main back in stock?" stays answerable from price_tracker.json.
            log_price_or_status(item["sku"], current_price=0.0, status="OOS")
            print(f"  🔄 {item['sku']} OOS — checking alternative(s) {', '.join(item['alt_skus'])}")
            alt_code, alt_status, alt_tried = first_alt_in_stock(driver, item["alt_skus"], item["final_qty"])
            for _tried, _tried_status in alt_tried:
                if _tried_status not in ("API_ERROR", "NO_DATA"):
                    log_price_or_status(_tried, current_price=0.0, status="OOS")
                print(f"  ❌ Alternative {_tried} also unavailable ({_tried_status})")
            alt_fail_note = "; ".join(f"alt {_c}: {_s}" for _c, _s in alt_tried)
            if alt_code:
                item["main_sku"] = item["sku"]
                item["sku"] = alt_code
                item["alt_used"] = True
                item["alt_passed_over"] = alt_fail_note
                cartable, inv_status = True, alt_status
                print(f"  ✅ ALT SUBSTITUTION: placing {item['sku']} instead of {item['main_sku']} (main OOS)")
                if item is resolved_items[0] and isinstance(addr, dict):
                    # The date read so far was the out-of-stock main's backorder date. Read the ALT's own
                    # (Zoro's estimate for this ZIP) and hold it under the same rule — before the cart is built.
                    addr.pop("_zoro_deliver_by", None)
                    _alt_date, _alt_src = (_zoro_api_delivery(driver, item["sku"], item["final_qty"], addr),
                                           f"Zoro's estimate for ZIP {_zip5(addr)}")
                    if not _alt_date and not defer_amazon_writes:
                        from platforms.zoro import get_zoro_delivery_date
                        _alt_date, _alt_src = get_zoro_delivery_date(driver, item["sku"]), "Zoro product page"
                    if _alt_date:
                        addr["_zoro_deliver_by"] = _alt_date
                    if (_gap_limit > 0 and not manual_override and _delivery_gate_holds(
                            item["sku"], _alt_date, _alt_src, f"{item['sku']} (ALT for {item['main_sku']})")):
                        return "skipped"
            elif alt_tried and alt_tried[-1][1] in ("API_ERROR", "NO_DATA"):
                # Zoro did not answer about an alternative, so this is not a clear OOS and nothing
                # would ever put a stamped order back: no label, the next cycle asks again.
                inv_status = alt_tried[-1][1]
        if not cartable:
            # Only label OOS for genuine out-of-stock, not API/network errors
            is_genuine_oos = inv_status not in ("API_ERROR", "NO_DATA")

            # Log status change in price_tracker
            log_price_or_status(item["sku"], current_price=0.0, status="OOS" if is_genuine_oos else inv_status)

            label = "OOS" if is_genuine_oos else None
            status_tag = "OOS" if is_genuine_oos else "INVENTORY_CHECK_FAIL"
            print(f"  ❌ {'OUT OF STOCK' if is_genuine_oos else 'INVENTORY CHECK FAILED'}: {item['sku']} ({inv_status})")
            if label and ULTRA_MODE:
                # Held, not marked: no label on Amazon, and it comes back next cycle (owner, 2 Nov 2026).
                _ultra_hold_listing(driver, item)
                _listing_out_note(driver, item, amazon_id, alt_tried)
                _log_order({
                    "amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                    "buyer": buyer, "city": city, "state": state, "status": "OOS_HELD",
                    "notes": "; ".join(filter(None, [f"{item['sku']}: {inv_status} — held, not marked",
                                                     alt_fail_note, order_note])),
                })
                return "skipped"
            if label:
                update_amazon_seller_id(driver, amazon_id, label, PROFILE_NAME)
                # Main and every alternative out: the listing itself must stop taking orders. Noted
                # here (one row); dealt with at the end of the cycle (engine/listing_guard.py).
                _listing_out_note(driver, item, amazon_id, alt_tried)
                try:
                    from utils.telegram import notify_oos
                    item_title = primary_item.get("title", "") if "primary_item" in locals() else (items[0].get("title", "") if items else "")
                    notify_oos(
                        amazon_id=amazon_id,
                        title=item_title,
                        asin=asin_field,
                        sku=sku_field,
                        zoro_code=item.get("sku", ""),
                        account=PROFILE_NAME,
                        driver=None if defer_amazon_writes else driver,   # rush: text only (wrong page)
                    )
                except Exception as _oose:
                    print(f"   [Telegram] ⚠️ OOS alert error: {_oose}")
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
        item["api_price"] = api_price
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
    # Seller Notes marks on the Code line: ALT (sheet's alternative), MANUAL (the tag's code),
    # PARTIAL (Manual-C/-R multi-item order, an item left out)
    _code_suffix = "".join(t for t, on in ((" - ALT", alt_used_any),
                                           (" - MANUAL", any(it.get("manual_code") for it in resolved_items)),
                                           (" - PARTIAL", _partial)) if on)
    if alt_used_any:
        sku_field = primary_item["sku"] if not is_multi_item else ", ".join(
            f"{it['sku']} x{it['final_qty']}" for it in resolved_items)
        _alt_pairs = ", ".join(
            f"{it['sku']} for {it['main_sku']}" for it in resolved_items if it.get("alt_used"))
        order_note = "; ".join(filter(None, [order_note, f"ALT: placed {_alt_pairs} (main OOS)",
                                             *(it.get("alt_passed_over") for it in resolved_items)]))

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
    # Big Order Gate ($800+) for Reseller profile: never buy $800+ orders at reseller price.
    # Mark '20' on Amazon and alert so Kamran can call Zoro for 20% manual discount.
    big_threshold = float(((gui_cfg if "gui_cfg" in globals() else {}).get("big_order_threshold", 800.0) or 800.0))
    if IS_RESELLER_PROFILE and raw_cart_cost >= big_threshold and not manual_override:
        ok = update_amazon_seller_id(driver, amazon_id, "20", PROFILE_NAME)
        reason = f"Big Order (${raw_cart_cost:.2f} >= ${big_threshold:.0f}) on Reseller profile — marked '20' for manual 20% Zoro phone discount"
        print(f"  📞 {reason} — marked '20' on Amazon" + ("" if ok else "  ⚠️ write not verified — set it by hand"))
        _log_order({"amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                    "buyer": buyer, "city": city, "state": state, "status": "MARKED_20_CALL_ZORO",
                    "notes": reason})
        _notify_big_order_20(amazon_id, asin_field, sku_field, raw_cart_cost, buyer, city, state)
        return "skipped"

    _is_deciding_order = bool(order_detail.get("_deciding"))
    if not _is_deciding_order and IS_RESELLER_PROFILE and DECIDING_ASINS:
        _dset = {d.strip().upper() for d in DECIDING_ASINS if d and d.strip()}
        _is_deciding_order = any((it.get("asin") or "").strip().upper() in _dset
                                 for it in resolved_items)
    if _is_deciding_order and IS_RESELLER_PROFILE and HAS_COUPON_SIBLING and not manual_override:
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

    # ── "Manual-C <coupon>": the code in the tag is the only coupon this order may use ──
    # (owner, 11 Nov 2026: the 20 % code Zoro gives by phone for a '20' order.) The sheet's coupons are
    # taken out here, before the strategy, the estimate, the focus mode and the big-order rule look at
    # them; the tag's code goes into the checkout list further down. The checkout gets a cache of its
    # own: one hand-picked code says nothing about the item, so nothing is learned from it either way.
    _tag_cache = {}
    _sheet_regular_codes = {str(c) for c in (regular_coupon_codes or [])}
    if _manual_coupon:
        priority_coupons_with_pct, regular_coupon_codes = [], []

    # ── SUNDAY MODE (coupon profiles): priority coupons only ──
    # A regular code spent today may be on an item Monday's priority code would have covered.
    # Stripped here, before the supervisor and the checkout see the list, so neither can use one.
    # With no regular code tried, the checkout can never call an item promo-ineligible either
    # (_coupon_fail_decision needs live regular codes for that).
    _sunday_status = None
    _sun_c = _sunday_mode() if (IS_COUPON_PROFILE and not manual_override) else None
    _sun_f = dict(asin=asin_field, sku=sku_field, qty=qty_field, buyer=buyer, city=city,
                  state=state, earnings=earnings, cart=raw_cart_cost)
    if _sun_c and regular_coupon_codes:
        print(f"  🗓️  SUNDAY MODE: priority coupons only — {len(regular_coupon_codes)} regular code(s) kept for Monday")
        regular_coupon_codes = []

    # ── Evaluate optimal coupon strategy ────────────────────────
    strategy = _evaluate_order_strategy(
        resolved_items, priority_coupons_with_pct, primary_item.get("unit_price", 0),
        list_total=raw_cart_cost)
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
            reseller_est_margin=_res_margin,
            # one AMAZON unit = the Zoro price × the pack size (owner, 20 Oct 2026: a $16 item sold
            # as a 4-pack costs $64 an order and belongs in Coupon, not Deciding)
            unit_price=float(primary_item.get("unit_price") or 0)
                       * float(primary_item.get("qty_multiplier") or 1)
                       * int(primary_item.get("zoro_multiple") or 1),
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
        print(f"\n  🧑‍⚖️ ═══════════ SUPERVISOR DECISION ═══════════")
        print(f"  │ Order ID   : {amazon_id}")
        print(f"  │ ASIN / SKU : {primary_item.get('asin')} / {sku_field}")
        print(f"  │ Cart Total : ${raw_cart_cost:.2f} (Qty: {qty_field})")
        print(f"  │ Action     : {_dec['action'].upper()}")
        print(f"  │ Reason     : {_dec['reason']}")
        if _dec.get("coupon"):
            print(f"  │ Coupon     : {_dec['coupon']} ({str(_dec.get('tier', '')).capitalize()})")
        if _dec.get("verdict"):
            print(f"  │ Sheet Rec  : {_dec['verdict'].get('status')} → Column: {_dec['verdict'].get('suggest')}")
        print(f"  └────────────────────────────────────────────────\n")

        if _dec.get("verdict") and priority_coupons_with_pct:
            _record_asin_verdict(primary_item.get("asin"), _dec["verdict"], _cart_txt, _dec.get("coupon") or "")
        elif _dec.get("verdict"):
            print("  🧑‍⚖️ SUPERVISOR: no priority coupons loaded — order handled, ASIN left unsorted until they are")
        if _sun_c and _dec["action"] in ("reseller", "call_zoro_20", "wait"):
            if _dec["action"] == "reseller":
                _g = None if _all_coupon_blocked(resolved_items) else _sunday_handover_gate(
                    raw_cart_cost, earnings, all(it.get("is_zoro_select") for it in resolved_items), _sun_c)
                _held = f"supervisor would hand it to the reseller, but {_g}" if _g else None
            elif _dec["action"] == "call_zoro_20":
                _held = f"big order (${raw_cart_cost:.2f}) with no priority coupon today — not marked '20'"
            else:
                _held = f"supervisor: {_dec['reason']}"
            if _held:
                return _sunday_hold(amazon_id, _held, coupon_codes, _sun_c, _sun_f)
        if _dec["action"] == "call_zoro_20":
            ok = update_amazon_seller_id(driver, amazon_id, "20", PROFILE_NAME)
            print(f"  📞 BIG ORDER (${raw_cart_cost:.2f} >= ${big_threshold:.0f}): {_dec['reason']} — marked '20' on Amazon"
                  + ("" if ok else "  ⚠️ write not verified — set it by hand"))
            _log_order({"amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                        "buyer": buyer, "city": city, "state": state, "status": "MARKED_20_CALL_ZORO",
                        "notes": _dec["reason"]})
            _notify_big_order_20(amazon_id, asin_field, sku_field, raw_cart_cost, buyer, city, state)
            return "skipped"
        if _dec["action"] == "reseller":
            _route_to_sibling(driver, amazon_id, "reseller", "Supervisor: " + _dec["reason"],
                              dict(asin=asin_field, sku=sku_field, qty=qty_field, buyer=buyer,
                                   city=city, state=state, order_note=order_note))
            return "skipped"
        if _dec["action"] in ("retry", "wait"):
            # left UNLABELLED on purpose: picked up again next cycle (wait = needs a coupon
            # and none is left today; retry = Zoro API/session trouble)
            print(f"  🧑‍⚖️ SUPERVISOR: {_dec['reason']} — left unlabelled, next cycle")
            if _dec["action"] == "wait":
                _COUPON_WAITS[amazon_id] = {"last": time.time(), "pool": _coupon_pool_sig(coupon_codes),
                                            "reason": "supervisor: " + _dec["reason"]}
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
            # list price decides shipping, not the Select-discounted cost (WB1228729288)
            estimated_shipping = ZORO_SMALL_ORDER_FEE if raw_cart_cost < 50.0 else 0.0
            estimated_total_cost = real_cost + estimated_shipping

    # ── Filler-aware shipping: if cart is close to $50, adding filler unlocks free shipping ──
    if not is_split_order and earnings:
        from utils.config import (
            SHIPPING_FILLER_SKU, SHIPPING_FILLER_PRICE,
            FREE_SHIPPING_THRESHOLD, FILLER_TRIGGER_MIN
        )
        # Zoro measures the $50 on the list total. A Zoro Select cart listed at $54.75 is $49.27
        # after the reseller discount and already ships free — topping it up with filler was
        # money spent for nothing (WB1228729288, 6 Oct 2026).
        if SHIPPING_FILLER_SKU and FILLER_TRIGGER_MIN <= raw_cart_cost < FREE_SHIPPING_THRESHOLD:
            gap = FREE_SHIPPING_THRESHOLD - raw_cart_cost
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

    # ── SUNDAY MODE (reseller profiles): only what Monday's coupons cannot improve ──
    # Judged on the estimate printed above — Est Cost INCLUDING shipping or filler, which is what
    # Zoro will be paid (owner: "shipping is a cost") — before anything is claimed. Not on the checkout
    # gate: a failure THERE labels the order "Loss-Kamran checking", which stops it coming back
    # on Monday. Here a miss only means the order waits.
    _sun_r = _sunday_mode() if (IS_RESELLER_PROFILE and not manual_override) else None
    _g = _sunday_gate(estimated_total_cost, margin if earnings else None, _sun_r, estimated_shipping)
    if _g and _all_coupon_blocked(resolved_items):
        print(f"  🗓️  SUNDAY MODE: would wait ({_g}), but Zoro marks this item not eligible for any "
              f"promotions — no coupon can ever apply, so it is placed now")
        _g = None
    if _g:
        return _sunday_hold(amazon_id, _g, coupon_codes, _sun_r,
                            dict(asin=asin_field, sku=sku_field, qty=qty_field, buyer=buyer, city=city,
                                 state=state, earnings=earnings, cart=round(estimated_total_cost, 2)))

    # ── Coupon strategy ─────────────────────────────────────────
    # A coupon written in the tag is always tried: neither the 'Rajar 1' label nor the profile's
    # "skip priority coupons" switch may turn it into a purchase without it.
    skip_coupons = not IS_COUPON_PROFILE or (TARGET_LABEL == 'Rajar 1' and not _manual_coupon)
    _pcfg = gui_cfg.get("profiles", {}).get(PROFILE_NAME, {})
    skip_priority = _pcfg.get("skip_priority_coupons", False) and not _manual_coupon

    # Build the flat coupon list for the checkout function
    _foc_codes = []
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

        # Focus Priority Coupon: in focus mode, strictly try ONLY the focus coupon(s) at checkout!
        # Do NOT fall back to other priority coupons (e.g. POWER) or regular coupons.
        _foc_codes = _focus_active_codes(priority_coupons_with_pct)
        if _foc_codes and not skip_priority:
            priority_codes_for_checkout = list(_foc_codes)
            regular_coupon_codes = []
            print(f"  🎯 Focus coupon mode ACTIVE: trying ONLY [{', '.join(_foc_codes)}] at checkout (no fallback to other coupons)")

        # Big Order Gate ($800+): never place with regular 10% EM coupons — unless tagged Manual-C
        if raw_cart_cost >= big_threshold and regular_coupon_codes:
            if manual_override:
                print(f"  🖐️  BIG ORDER (${raw_cart_cost:.2f} >= ${big_threshold:.0f}) tagged Manual-{_manual_kind} — "
                      f"regular coupons allowed, never marked '20'")
            else:
                print(f"  📞 BIG ORDER (${raw_cart_cost:.2f} >= ${big_threshold:.0f}): regular 10% EM coupons disabled — trying ONLY priority coupons")
        regular_coupon_codes = _big_order_regular_codes(regular_coupon_codes, raw_cart_cost, big_threshold, manual_override)

        coupon_arg = priority_codes_for_checkout + regular_coupon_codes
        if _manual_coupon:
            # the checkout tries its "priority" list first and stops at the first code that applies
            priority_codes_for_checkout = _tag_coupon_codes(_manual_coupon)
            regular_coupon_codes = []
            coupon_arg = list(priority_codes_for_checkout)
            print(f"  🖐️  Coupon from the tag: trying ONLY {_manual_coupon} — the sheet's coupons are not used")

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
        if _sun_c:
            return _sunday_hold(amazon_id, "no priority coupon in today's pool"
                                + (f" (big order ${raw_cart_cost:.2f} — not marked '20')" if raw_cart_cost >= big_threshold else ""),
                                coupon_codes, _sun_c, _sun_f)
        if raw_cart_cost >= big_threshold:
            ok = update_amazon_seller_id(driver, amazon_id, "20", PROFILE_NAME)
            reason = f"Big Order (${raw_cart_cost:.2f} >= ${big_threshold:.0f}) — no priority coupon in pool; marked '20' for manual 20% Zoro phone discount"
            print(f"  📞 {reason} — marked '20' on Amazon" + ("" if ok else "  ⚠️ write not verified — set it by hand"))
            _log_order({"amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                        "buyer": buyer, "city": city, "state": state, "status": "MARKED_20_CALL_ZORO",
                        "notes": reason})
            _notify_big_order_20(amazon_id, asin_field, sku_field, raw_cart_cost, buyer, city, state)
            return "skipped"
        # No coupon at all in today's pool: this profile exists to place WITH coupons, so it
        # waits for the next coupon load instead of buying at list price.
        print("  ⏸️  No coupons available (priority or regular) — not placing at full price; waiting for the next coupon load")
        _log_order({"amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                    "buyer": buyer, "city": city, "state": state, "status": "NO_COUPONS_WAIT",
                    "notes": "; ".join(filter(None, ["no priority or regular coupons loaded — waiting", order_note]))})
        _COUPON_WAITS[amazon_id] = {"last": time.time(), "pool": _coupon_pool_sig(coupon_codes), "reason": "no coupons in the pool"}
        return "failed"
    if _manual_coupon and list(coupon_arg or []) != _tag_coupon_codes(_manual_coupon):
        # Fail closed: the tag names a coupon, so the checkout list must hold that code and nothing
        # else. Anything different would buy without it, or with another coupon.
        print(f"  🛑 {amazon_id}: the tag's coupon {_manual_coupon} did not reach the checkout list — NOT placed")
        _log_order({"amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                    "buyer": buyer, "city": city, "state": state, "status": "TAG_COUPON_NOT_USED",
                    "notes": "; ".join(filter(None, [f"coupon {_manual_coupon} from the tag was not in the checkout "
                                                      f"list — not placed", order_note]))})
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

    # DEBUG: trace coupon list sizes
    print(f"   [DEBUG] coupon_arg len={len(coupon_arg) if coupon_arg else 0} (priority={len(priority_codes_for_checkout)}, regular={len(regular_coupon_codes)})")
    if coupon_arg:
        print(f"   [DEBUG] coupon_arg first 10: {coupon_arg[:10]}")
        print(f"   [DEBUG] coupon_arg last 10: {coupon_arg[-10:]}")

    _checkout_kwargs = dict(
        earnings=earnings, pre_price=real_cost,
        # Zoro's undiscounted per-unit list price, as returned when the item was added to the
        # cart ($2.19 for G3057148, $329.99 for a G7747482). Recorded raw so price changes can
        # be seen without dividing totals by quantities or un-applying coupons.
        list_price=primary_item.get("api_price"),
        original_sku=note_amazon_sku,  # page SKU (under ASIN) preferred, email SKU fallback — keeps log/sheet/note in sync
        shipping_amt=shipping_amt,
        amazon_fees=amazon_fees,
        coupon_list=coupon_arg, asin=primary_item["asin"],
        zoro_code=primary_item["sku"],
        asin_coupon_cache=_tag_cache if _manual_coupon else asin_coupon_cache,
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
                    preferred_coupon=split_coupon or (_foc_codes[0] if _foc_codes else (supervised_coupon or strategy.get("preferred_coupon"))),
                    coupon_meta_by_code=coupon_meta_by_code,
                    alt_of=alt_main_sku,
                    payment_method=PAYMENT_METHOD,
                    split_coupon_only=split_coupon,
                    # Zoro Select brand detection: True if the item is Zoro Select
                    zoro_select=bool(all(it.get("is_zoro_select") for it in resolved_items)),
                    only_coupon=_manual_coupon or None,
                    **filter_kwargs_for_callee(place_order_fast_true_api,
                                               checkout_kwargs, warn_label="ENGINE"))
            except Exception as true_api_err:
                print(f"   [ENGINE] True API failed: {true_api_err}")
                result = None
        if result is None and _manual_coupon:
            # The two page checkouts below keep whatever promo is already on the cart when the code
            # they try fails. An order with a coupon in its tag may only be bought with that coupon.
            print(f"   [ENGINE] no page-checkout fallback for a coupon written in the tag ({_manual_coupon}) "
                  f"— nothing bought, tried again next cycle")
            return {"status": "COUPON_RETRY"}
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
    # Not for a reseller-only store (Wrenchly): there is no coupon placer to promote or test the
    # ASIN, so reseller_must_wait would hold every unlabelled order for good.
    if IS_RESELLER_PROFILE and HAS_COUPON_SIBLING and not (merchant_id or "").strip() and not manual_override:
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
            # Not properly tested with the CURRENT priority coupons (no answer from Zoro, or tested
            # before the coupon change): queue it for the coupon placer and leave the order
            # unlabelled — never bought at reseller price on a guess (owner, 20 Oct 2026).
            _pasin = str(primary_item.get("asin") or "").strip().upper()
            _wait = _pc_late.reseller_must_wait(_pasin, _STORE, sheet_priority_codes=_sheet_priority_codes())
            if _wait:
                _pc_late.enqueue_asins(f"{_STORE}_COUPON", [_pasin], f"reseller: {_wait}")
                print(f"  ⏳ {_pasin}: not placed at reseller price — {_wait}. Queued for {_STORE}_COUPON's "
                      f"priority-coupon test; the order stays unlabelled until then.")
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
                _dl, _own = _label_for_locked_wb(amazon_id, _bwb)     # plain WB only for its own purchase
                update_amazon_seller_id(driver, amazon_id, _dl, PROFILE_NAME)
                print(f"  ⏭️  {amazon_id} already placed ({_bwb}) — marked Amazon '{_dl}' (not placing again)")
                _log_order({"amazon_id": amazon_id, "status": "WB_RESTORED" if _own else "DUPLICATE", "wb_id": _bwb,
                            "notes": ("already placed as this order — WB written to Amazon again by local lock"
                                      if _own else "already placed — marked '<WB> - D' on Amazon by local lock")})
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
            logged_subtotal = checkout_result.get("subtotal")
            logged_shipping = checkout_result.get("shipping")
            if checkout_result.get("profit") is not None:
                profit = checkout_result.get("profit")
            if checkout_result.get("margin") is not None:
                margin = checkout_result.get("margin")
            logged_cost = checkout_total if checkout_total is not None else estimated_total_cost

    # ── Result handling ───────────────────────────────────────────
    margin_str = str(int(round(margin * 100))) if earnings else "0"
    _cf_route = None            # set when a COUPON FAIL means "the reseller should place this"
    _cf_action_20 = False       # set when cart >= $800 and coupons fail -> mark '20' for phone discount

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
                    amazon_qty=primary_item.get("order_qty"),
                    amazon_fees=amazon_fees,
                    shipping_amt=shipping_amt,
                    purchase_date=pd,
                    ship_by_date=sb,
                    deliver_by_date=db,
                    zoro_tax=split_taxes[idx] if idx < len(split_taxes) else (logged_tax / len(all_wb_ids) if logged_tax else None),
                    zoro_list_price=primary_item.get("api_price"),
                zoro_deliver_by=(addr.get("_zoro_deliver_by") if isinstance(addr, dict) else None),
                    zoro_subtotal=logged_subtotal,
                    zoro_shipping=logged_shipping,
                    coupon_used=(last_checkout_result.get("used_coupon_code") if last_checkout_result else None),
                    coupon_savings=split_discounts[idx] if idx < len(split_discounts) else (logged_discount / len(all_wb_ids) if logged_discount else None))
            # Write seller note for split order (driver already on Amazon page)
            try:
                from utils.seller_notes import format_seller_note, enrich_note_basis
                note_data = {
                    "title": primary_item.get("title", ""),
                    "asin": asin_field,
                    "sku": note_amazon_sku,
                    "code": (primary_item["sku"].replace("G", "K", 1) if primary_item["sku"].startswith("G") else primary_item["sku"]) + _code_suffix,
                    "qty": qty_field,
                    "tracking_sent": "No",
                    "tc": logged_cost,
                    "tax": logged_tax,
                    "earnings": earnings,
                    "pm": profit,
                    "pp": (margin * 100) if margin is not None else None,

                    "ship_by_date": sb, "deliver_by_date": db, "ship_service": order_detail.get("ship_service", ""),

                    "zoro_deliver_by": (addr.get("_zoro_deliver_by") if isinstance(addr, dict) else "") or "",

                    "shipping_amt": shipping_amt, "amazon_id": amazon_id,
                    "coupon": bool(last_checkout_result.get("used_coupon_code")) if last_checkout_result else False,
                    "coupon_code": (last_checkout_result.get("used_coupon_code") or "") if last_checkout_result else "",
                    "coupon_off": logged_discount,
                }
                enrich_note_basis(
                    note_data, profile_name=PROFILE_NAME,
                    zoro_select=bool(all(it.get("is_zoro_select") for it in resolved_items)),
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

            try:
                from utils.telegram import notify_order_placed
                tg_data = dict(split_detail)
                if 'note_data' in locals():
                    tg_data.update(note_data)
                tg_data.update({"ship_by_date": sb, "deliver_by_date": db,
                                "ship_service": order_detail.get("ship_service", ""),
                                "zoro_deliver_by": (addr.get("_zoro_deliver_by") if isinstance(addr, dict) else "") or "",
                                "shipping_amt": shipping_amt})
                # Rush (deferred Amazon writes): the driver is not on THIS order's page, so a screenshot
                # would show another order (owner, 12 Oct 2026) — the alert goes as text only.
                notify_order_placed(tg_data, driver=None if defer_amazon_writes else driver, account=PROFILE_NAME)
            except Exception as _tge:
                print(f"   [Telegram] ⚠️ Notification error: {_tge}")
            if alt_used_any:
                _notify_alt_main_oos(driver, [it.get("main_sku") for it in resolved_items if it.get("alt_used")],
                                     account=PROFILE_NAME)

            # Operational Insights & Anomaly Analysis
            try:
                from utils.insights_engine import placer_alerts
                from utils.whatsapp import notify_insight
                insight_data = dict(split_detail)
                if 'note_data' in locals():
                    insight_data.update(note_data)
                insight_data["purchase_date"] = pd
                insight_data["ship_by_date"] = sb
                insight_data["deliver_by_date"] = db
                insight_data["amazon_fees"] = amazon_fees
                insight_data["shipping_amt"] = shipping_amt
                insight_data["amazon_qty"] = primary_item.get("order_qty") or 1
                insight_data["quantity"] = primary_item.get("order_qty") or 1
                insight_data["zoro_qty"] = primary_item.get("final_qty") or qty_field or 1
                insight_data["qty_multiplier"] = primary_item.get("qty_multiplier") or 1
                insight_data["alt_used"] = alt_used_any
                insight_data["alt_of"] = alt_main_sku
                insight_data["main_sku"] = alt_main_sku
                insight_data["zoro_code"] = primary_item.get("sku") or ""
                insight_data["seller_sku"] = note_amazon_sku
                insight_data["zoro_catalog_price"] = primary_item.get("api_price") or primary_item.get("unit_price")
                insight_data["unit_cost"] = primary_item.get("api_price") or primary_item.get("unit_price")
                insight_data["wb_order_id"] = wb_id
                insight_data["wb_id"] = wb_id
                insight_data["zoro_tax"] = split_taxes[idx] if 'split_taxes' in locals() and idx < len(split_taxes) else logged_tax
                insight_data["coupon_savings"] = split_discounts[idx] if 'split_discounts' in locals() and idx < len(split_discounts) else logged_discount
                insight_data["coupon_used"] = (last_checkout_result.get("used_coupon_code") if last_checkout_result else None)
                order_acct = STORE_ACCOUNT
                insight_data["account"] = order_acct
                # Zoro's "Delivery by" date was scraped from the PDP before the cart flow (addr).
                insight_data["zoro_deliver_by"] = ((addr.get("_zoro_deliver_by") if isinstance(addr, dict) else "")
                                                   or insight_data.get("zoro_deliver_by") or "")
                triggered_insights = placer_alerts(insight_data)
                for ins in triggered_insights:
                    ins["account"] = order_acct
                    print(f"   💡 [Insight] Triggered: {ins.get('title')} ({ins.get('code')})")
                    notify_insight(ins, account=order_acct)
            except Exception as _ie:
                print(f"   💡 [Insight] ⚠️ Analysis error: {_ie}")
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
                amazon_qty=primary_item.get("order_qty"),
                amazon_fees=amazon_fees,
                shipping_amt=shipping_amt,
                purchase_date=pd,
                ship_by_date=sb,
                deliver_by_date=db,
                zoro_tax=logged_tax,
                zoro_list_price=primary_item.get("api_price"),
                zoro_deliver_by=(addr.get("_zoro_deliver_by") if isinstance(addr, dict) else None),
                zoro_subtotal=logged_subtotal,
                zoro_shipping=logged_shipping,
                coupon_used=(last_checkout_result.get("used_coupon_code") if last_checkout_result else None),
                coupon_savings=logged_discount)
        # Write seller note (driver already on Amazon page from update_amazon_seller_id)
        try:
            from utils.seller_notes import format_seller_note, enrich_note_basis
            primary_sku = primary_item["sku"] if not is_multi_item else sku_field
            note_data = {
                "title": primary_item.get("title", ""),
                "asin": asin_field,
                "sku": note_amazon_sku,
                "code": (primary_sku.replace("G", "K", 1) if primary_sku.startswith("G") else primary_sku) + _code_suffix,
                "qty": qty_field,
                "tracking_sent": "No",
                "tc": logged_cost,
                "tax": logged_tax,
                "earnings": earnings,
                "pm": profit,
                "pp": (margin * 100) if margin is not None else None,

                "ship_by_date": sb, "deliver_by_date": db, "ship_service": order_detail.get("ship_service", ""),

                "zoro_deliver_by": (addr.get("_zoro_deliver_by") if isinstance(addr, dict) else "") or "",

                "shipping_amt": shipping_amt, "amazon_id": amazon_id,
                "coupon": bool(last_checkout_result.get("used_coupon_code")) if last_checkout_result else False,
                "coupon_code": (last_checkout_result.get("used_coupon_code") or "") if last_checkout_result else "",
                "coupon_off": logged_discount,
            }
            enrich_note_basis(
                note_data, profile_name=PROFILE_NAME,
                zoro_select=bool(all(it.get("is_zoro_select") for it in resolved_items)),
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

        # Send Telegram notification (handles < 8% screenshot, > 50% celebration, or normal placement)
        try:
            from utils.telegram import notify_order_placed
            tg_data = dict(normal_detail)
            if 'note_data' in locals():
                tg_data.update(note_data)
            tg_data.update({"ship_by_date": sb, "deliver_by_date": db,
                            "ship_service": order_detail.get("ship_service", ""),
                            "zoro_deliver_by": (addr.get("_zoro_deliver_by") if isinstance(addr, dict) else "") or "",
                            "shipping_amt": shipping_amt})
            # Rush: no screenshot — the driver is not on this order's page (text-only alert)
            notify_order_placed(tg_data, driver=None if defer_amazon_writes else driver, account=PROFILE_NAME)
        except Exception as _tge:
            print(f"   [Telegram] ⚠️ Notification error: {_tge}")
        if alt_used_any:
            _notify_alt_main_oos(driver, [it.get("main_sku") for it in resolved_items if it.get("alt_used")],
                                 account=PROFILE_NAME)

        # Operational Insights & Anomaly Analysis
        try:
            from utils.insights_engine import placer_alerts
            from utils.whatsapp import notify_insight
            insight_data = dict(normal_detail)
            if 'note_data' in locals():
                insight_data.update(note_data)
            insight_data["purchase_date"] = pd
            insight_data["ship_by_date"] = sb
            insight_data["deliver_by_date"] = db
            insight_data["amazon_fees"] = amazon_fees
            insight_data["shipping_amt"] = shipping_amt
            insight_data["amazon_qty"] = primary_item.get("order_qty") or 1
            insight_data["quantity"] = primary_item.get("order_qty") or 1
            insight_data["zoro_qty"] = primary_item.get("final_qty") or qty_field or 1
            insight_data["qty_multiplier"] = primary_item.get("qty_multiplier") or 1
            insight_data["alt_used"] = alt_used_any
            insight_data["alt_of"] = alt_main_sku
            insight_data["main_sku"] = alt_main_sku
            insight_data["zoro_code"] = primary_item.get("sku") or ""
            insight_data["seller_sku"] = note_amazon_sku
            insight_data["zoro_catalog_price"] = primary_item.get("api_price") or primary_item.get("unit_price")
            insight_data["unit_cost"] = primary_item.get("api_price") or primary_item.get("unit_price")
            insight_data["wb_order_id"] = result_id
            insight_data["wb_id"] = result_id
            insight_data["zoro_tax"] = logged_tax
            insight_data["coupon_savings"] = logged_discount
            insight_data["coupon_used"] = (last_checkout_result.get("used_coupon_code") if last_checkout_result else None)
            if last_checkout_result:
                insight_data["zoro_stock"] = last_checkout_result.get("stock_level") or last_checkout_result.get("zoro_stock")
                insight_data["zoro_deliver_by"] = last_checkout_result.get("est_delivery") or last_checkout_result.get("zoro_deliver_by")
            order_acct = STORE_ACCOUNT
            insight_data["account"] = order_acct
            # Zoro's "Delivery by" date was scraped from the PDP before the cart flow (addr).
            insight_data["zoro_deliver_by"] = ((addr.get("_zoro_deliver_by") if isinstance(addr, dict) else "")
                                               or insight_data.get("zoro_deliver_by") or "")
            triggered_insights = placer_alerts(insight_data)
            for ins in triggered_insights:
                ins["account"] = order_acct
                print(f"   💡 [Insight] Triggered: {ins.get('title')} ({ins.get('code')})")
                notify_insight(ins, account=order_acct)
        except Exception as _ie:
            print(f"   💡 [Insight] ⚠️ Analysis error: {_ie}")
        # Delete used regular coupon from Google Sheet so colleagues don't reuse it.
        # Priority coupons are reusable and stay in the sheet.
        used_coupon = last_checkout_result.get("used_coupon_code") if last_checkout_result else None
        _all_priority_codes = {c["code"] for c in (priority_coupons_with_pct or [])} | set(priority_codes_for_checkout or [])
        if _manual_coupon:
            # a single-use sheet code written in the tag is used up like any other (its row goes);
            # a code that is not in the sheet stays in this set and has no row to delete
            _all_priority_codes -= _sheet_regular_codes
        if used_coupon and used_coupon != "AUTO_APPLIED" and used_coupon not in _all_priority_codes:
            _delete_coupon_from_sheet(used_coupon)
        # a code a person picked for one order says nothing about the item: no strikes are cleared
        if used_coupon and IS_COUPON_PROFILE and not _manual_coupon:
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
        try:
            from utils.telegram import notify_low_profit_rejected
            notify_low_profit_rejected(
                amazon_id=amazon_id,
                title=primary_item.get("title", "") if "primary_item" in locals() else "",
                asin=asin_field,
                sku=sku_field,
                earnings=earnings or 0.0,
                cost=logged_cost or 0.0,
                margin=margin,
                reason="Loss-Kamran checking",
                min_margin=checkout_min_margin or MIN_MARGIN,
                account=PROFILE_NAME
            )
        except Exception as _tge:
            print(f"   [Telegram] ⚠️ Low profit alert error: {_tge}")
        # Write the loss details to the Amazon seller note so the loss is visible
        # at a glance (cost, earnings, profit/loss, margin) without digging logs.
        try:
            from utils.seller_notes import format_seller_note, enrich_note_basis
            note_data = {
                "title": primary_item.get("title", ""),
                "asin": asin_field,
                "sku": note_amazon_sku,
                "code": (sku_field.replace("G", "K", 1) if sku_field.startswith("G") else sku_field) + _code_suffix,
                "qty": qty_field,
                "tracking_sent": "No",
                "tc": logged_cost,
                "tax": logged_tax,
                "earnings": earnings,
                "pm": profit,
                "pp": (margin * 100) if margin is not None else None,

                "ship_by_date": sb, "deliver_by_date": db, "ship_service": order_detail.get("ship_service", ""),

                "zoro_deliver_by": (addr.get("_zoro_deliver_by") if isinstance(addr, dict) else "") or "",

                "shipping_amt": shipping_amt, "amazon_id": amazon_id,
            }
            enrich_note_basis(
                note_data, profile_name=PROFILE_NAME,
                zoro_select=bool(all(it.get("is_zoro_select") for it in resolved_items)),
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
    elif result_status == "SHARED_ACCOUNT":
        # Another running profile is logged in to the same Zoro account — one cart for two placers.
        # Nothing was touched; the order waits until each profile is on its own account (19 Oct 2026).
        status_note = "Zoro account shared with another running profile — NOT placed, retried next cycle"
        if time.time() - _SHARED_ACCOUNT_ALERTED[0] > 3600:
            _SHARED_ACCOUNT_ALERTED[0] = time.time()
            _notify_delivery_gap(amazon_id, asin_field, sku_field,
                                 f"{PROFILE_NAME} is logged in to the same Zoro account as another running "
                                 f"profile — orders stop until each is on its own account",
                                 order_detail.get("ship_by_date", ""), buyer, city, state)
    elif result_status == "CART_BUSY":
        # Zoro refused the order because the account's cart was still tied up with the previous
        # order (409 RESOURCE_CONFLICT / 422 "already being processed"). Nothing was bought, so the
        # order is released and retried next cycle instead of being locked (owner, 16 Oct 2026).
        _CART_BUSY_TRIES[amazon_id] = _CART_BUSY_TRIES.get(amazon_id, 0) + 1
        status_note = (f"Zoro cart busy with the previous order (try {_CART_BUSY_TRIES[amazon_id]}) — "
                       f"NOT placed, retried next cycle")
        if _CART_BUSY_TRIES[amazon_id] == 6:
            _notify_delivery_gap(amazon_id, asin_field, sku_field,
                                 f"Zoro kept refusing the {PROFILE_NAME} cart (busy) on 6 tries",
                                 order_detail.get("ship_by_date", ""), buyer, city, state)
    elif result_status == "ZORO_NET":
        # Ultra mode: Zoro stopped answering before the order was sent (checkout/true_api.py). Nothing
        # was bought, so it is released and comes back next cycle instead of being locked.
        status_note = "Zoro stopped answering before the order was sent — NOT placed, retried next cycle"
    elif result_status == "PAYMENT_DECLINED":
        # Zoro showed "unable to receive authorization for your payment method" on every try (with a
        # checkout refresh between) — no order exists. Released and retried next cycle; a person
        # is told when the same order keeps being declined (the card may need attention).
        _PAYMENT_DECLINED_TRIES[amazon_id] = _PAYMENT_DECLINED_TRIES.get(amazon_id, 0) + 1
        status_note = (f"Zoro could not authorize the payment (cycle {_PAYMENT_DECLINED_TRIES[amazon_id]}) — "
                       f"NOT placed, retried next cycle")
        if _PAYMENT_DECLINED_TRIES[amazon_id] == 3:
            _notify_delivery_gap(amazon_id, asin_field, sku_field,
                                 f"Zoro declined the {PROFILE_NAME} card on 3 cycles (3 tries each) — check the card",
                                 order_detail.get("ship_by_date", ""), buyer, city, state)
    elif result_status == "DELIVERY_UNKNOWN":
        # Neither Zoro page showed a delivery date: never buy blind (owner, 15 Oct 2026). Nothing
        # is written to Amazon, so the order comes back next cycle; after 3 tries a person is told.
        _DELIVERY_UNKNOWN_TRIES[amazon_id] = _DELIVERY_UNKNOWN_TRIES.get(amazon_id, 0) + 1
        status_note = (f"Zoro delivery date unreadable (try {_DELIVERY_UNKNOWN_TRIES[amazon_id]}) — "
                       f"NOT placed, retried next cycle")
        if _DELIVERY_UNKNOWN_TRIES[amazon_id] == 3:
            _notify_delivery_gap(amazon_id, asin_field, sku_field,
                                 "Zoro showed no delivery date on 3 tries, so it was not bought",
                                 order_detail.get("ship_by_date", ""), buyer, city, state)
    elif result_status == "DELIVERY_TIME_HIGH":
        # Zoro's checkout page, for this address and cart, said "Delivery expected by" 4+ days after
        # Amazon's deliver-by — Place Order was NOT clicked (owner, 15 Oct 2026).
        _zd = addr.get("_zoro_deliver_by", "") if isinstance(addr, dict) else ""
        if defer_amazon_writes:
            _rush_defer_wb(amazon_id, DELIVERY_GAP_LABEL)
        else:
            update_amazon_seller_id(driver, amazon_id, DELIVERY_GAP_LABEL, PROFILE_NAME)
        status_note = (f"Zoro checkout: delivery expected by {_zd}, past Amazon's deliver-by "
                       f"{order_detail.get('deliver_by_date', '')} — labelled {DELIVERY_GAP_LABEL}, not placed")
        _notify_delivery_gap(amazon_id, asin_field, sku_field,
                             f"Zoro's checkout says delivery by {_zd}; Amazon's deliver-by is "
                             f"{order_detail.get('deliver_by_date', '')}", order_detail.get("ship_by_date", ""),
                             buyer, city, state)
    elif result_status == "PO_BOX_RESTRICTED":
        _po_box_park(driver, amazon_id, addr, "zoro_rejected", defer=defer_amazon_writes)
        status_note = f"PO Box not allowed by Zoro ({addr.get('city', '')}, {state}) — labelled {PO_BOX_LABEL}"
    elif result_status == "NET30_RESTRICTED":
        update_amazon_seller_id(driver, amazon_id, "Net30", PROFILE_NAME)
        status_note = "Net30 restricted"
    elif result_status in ("NO_COUPONS_SKIP", "COUPON FAIL", "COUPON_RETRY"):
        # Coupon failed — leave the order untouched so it retries next cycle.
        # Do NOT relabel as "MR Profit Margin" or "Rajar" — that blocks retries.
        status_note = (f"Coupon API / session trouble on every priority coupon — retry next cycle"
                       if result_status == "COUPON_RETRY" else f"No coupon worked — skipping (will retry next cycle)")
        if result_status == "COUPON_RETRY" and _manual_coupon:
            status_note = (f"Manual-C: Zoro's checkout or coupon service did not answer — nothing bought; "
                           f"the tag's coupon {_manual_coupon} is tried again next cycle")
        if result_status == "COUPON FAIL" and _manual_coupon:
            # The coupon a person wrote in the tag did not apply: nothing is bought, no other coupon is
            # tried and the order is never marked '20'. It keeps its tag and is tried again after the
            # cooldown, or at once when another code is written.
            _lf = _tag_cache.get("last_fail") or {}
            _why, _blip = _tag_coupon_refusal(_lf, _tag_cache)
            status_note = (f"Manual-C: coupon {_manual_coupon} from the tag did not apply — {_why} — NOT placed; "
                           f"keeps its tag (write another code, plain Manual-C for the sheet's coupons, or "
                           f"Manual-R to place it without a coupon)")
            _DLOG.log("coupon_fail", profile=PROFILE_NAME, amazon_id=amazon_id, asin=asin_field, sku=sku_field,
                      qty=qty_field, cart=raw_cart_cost, earnings=earnings, action="wait",
                      reason=f"tag coupon {_manual_coupon}: {_why}", definitive=False, last_fail=_lf)
            _COUPON_WAITS[amazon_id] = {"last": time.time(), "pool": _coupon_pool_sig(coupon_codes),
                                        "reason": status_note, "tag_coupon": _manual_coupon}
            if not _blip:                   # an API blip says nothing about the code: the next try decides
                _notify_manual_no_coupon(amazon_id, asin_field, sku_field, raw_cart_cost, _why[0].upper() + _why[1:],
                                         buyer, city, state, tag_coupon=_manual_coupon)
        elif result_status == "COUPON FAIL" and IS_COUPON_PROFILE and not skip_coupons:
            if _big_order_marks_20(raw_cart_cost, big_threshold, manual_override):
                _cf_action_20 = True
                status_note = f"Big Order (${raw_cart_cost:.2f} >= ${big_threshold:.0f}) — no priority coupon worked at checkout"
            elif _foc_codes:
                status_note = f"Focus coupon ({', '.join(_foc_codes)}) did not apply — deferred"
                _COUPON_WAITS[amazon_id] = {"last": time.time(), "pool": _coupon_pool_sig(coupon_codes), "reason": status_note}
                if manual_override:
                    _notify_manual_no_coupon(amazon_id, asin_field, sku_field, raw_cart_cost,
                                             f"focus-coupon mode is on and {', '.join(_foc_codes)} does not apply "
                                             f"(untick it to try the other coupons)", buyer, city, state)
            else:
                from utils.config import ZORO_SMALL_ORDER_FEE as _cf_fee, FREE_SHIPPING_THRESHOLD as _cf_free
                _lf = (asin_coupon_cache or {}).get("last_fail") or {}
                _cf = _coupon_fail_decision(_lf, raw_cart_cost, earnings,
                                            all(it.get("is_zoro_select") for it in resolved_items),
                                            _cf_fee, _cf_free, DECIDING_MIN_MARGIN,
                                            big_order_20=not manual_override)
                _DLOG.log("coupon_fail", profile=PROFILE_NAME, amazon_id=amazon_id, asin=asin_field, sku=sku_field,
                          qty=qty_field, cart=raw_cart_cost, earnings=earnings, action=_cf["action"], reason=_cf["reason"],
                          definitive=bool(_cf.get("definitive")), last_fail=_lf)
                status_note = f"No coupon worked — {_cf['reason']}"
                if _cf.get("definitive"):
                    # every code rejected the item itself → promo-ineligible now, not after two strikes
                    _learn_promo_outcome(primary_item.get("sku"), primary_item.get("asin"), False,
                                         cache=asin_coupon_cache, definitive=True, source="checkout")
                if _cf["action"] == "call_zoro_20" and not manual_override:
                    _cf_action_20 = True
                elif _cf["action"] == "route_reseller" and not manual_override:
                    _cf_route = _cf["reason"]
                else:
                    _COUPON_WAITS[amazon_id] = {"last": time.time(), "pool": _coupon_pool_sig(coupon_codes), "reason": _cf["reason"]}
                    if manual_override:
                        # the reseller hand-off is not taken for a tagged order, so do not repeat its wording
                        _mwhy = ("every coupon rejected this item — a coupon can never apply" if
                                 (_cf["action"] == "route_reseller" and _cf.get("definitive")) else
                                 "no coupon applies to this cart" if _cf["action"] == "route_reseller" else _cf["reason"])
                        status_note = (f"Manual-{_manual_kind}: no coupon worked — {_mwhy} — NOT placed, not "
                                       f"marked '20'; keeps its tag (write Manual-R to place it without a coupon)")
                        # an API/session blip says nothing about the coupons: the next cycle decides,
                        # and the one alert is kept for a real "no coupon"
                        if not any(int(_lf.get(k) or 0) for k in ("transient", "priority_transient", "regular_transient")):
                            _notify_manual_no_coupon(amazon_id, asin_field, sku_field, raw_cart_cost, _mwhy,
                                                     buyer, city, state)
        # SUNDAY MODE: no priority code applied — act only as a normal day would AND the reseller
        # rule allows. The claim is held at this point, so nothing returns early: the flags are
        # cleared and the usual pre-payment release below runs.
        if _sun_c and result_status == "COUPON FAIL" and IS_COUPON_PROFILE and not skip_coupons and not _foc_codes:
            if _cf_action_20:
                _cf_action_20 = False
                _sunday_status = f"big order (${raw_cart_cost:.2f}) with no priority coupon today — not marked '20'"
            elif _cf_route:
                _g = None if _all_coupon_blocked(resolved_items) else _sunday_handover_gate(
                    raw_cart_cost, earnings, all(it.get("is_zoro_select") for it in resolved_items), _sun_c)
                if _g:
                    _cf_route = None
                    _sunday_status = f"would go to the reseller, but {_g}"
            else:
                # A code that answered with a session/API error said nothing about the item: leave
                # that order on the normal 45-min retry instead of parking it until Monday.
                _lf_s = (asin_coupon_cache or {}).get("last_fail") or {}
                if not any(int(_lf_s.get(k) or 0) for k in ("transient", "priority_transient", "regular_transient")):
                    _sunday_status = "no priority coupon applied"
            if _sunday_status:
                status_note = f"Sunday mode: {_sunday_status} — waiting for Monday's priority coupons"
                _sunday_park(amazon_id, _sunday_status, coupon_codes, _sun_c)
    else:
        status_note = f"Placement failed: {result_status}"
    
    print(f"  ❌ {status_note}")

    if _cf_action_20:
        if _HAS_LOCK and not DRY_RUN:
            plock.release(amazon_id, f"big order -> marked 20")
        ok = update_amazon_seller_id(driver, amazon_id, "20", PROFILE_NAME)
        reason = f"Big Order (${raw_cart_cost:.2f} >= ${big_threshold:.0f}) — no priority coupon worked at checkout; marked '20' for manual 20% Zoro phone discount"
        print(f"  📞 {reason} — marked '20' on Amazon" + ("" if ok else "  ⚠️ write not verified — set it by hand"))
        _log_order({"amazon_id": amazon_id, "asin": asin_field, "sku": sku_field, "qty": qty_field,
                    "buyer": buyer, "city": city, "state": state, "status": "MARKED_20_CALL_ZORO",
                    "notes": reason})
        _notify_big_order_20(amazon_id, asin_field, sku_field, raw_cart_cost, buyer, city, state)
        return "skipped"

    # ── Reconcile the local claim on failure ──────────────────────
    # These statuses are all detected BEFORE Zoro payment, so it is safe to release the
    # claim and let the order retry next cycle (matches the pre-existing retry intent).
    _SAFE_PREPAYMENT = ("OOS", "STATE_RESTRICTED", "PO_BOX_RESTRICTED", "NET30_RESTRICTED", "DELIVERY_TIME_HIGH", "DELIVERY_UNKNOWN", "PAYMENT_DECLINED",
                        "CART_BUSY", "ZORO_NET", "SHARED_ACCOUNT", "NO_COUPONS_SKIP", "COUPON FAIL", "COUPON_RETRY")
    if _HAS_LOCK and not DRY_RUN:
        if result_status in _SAFE_PREPAYMENT:
            plock.release(amazon_id, f"pre-payment failure: {result_status}")
            if _cf_route:
                # claim released → hand the order to the reseller profile (label only, never a buy)
                _route_to_sibling(driver, amazon_id, "reseller", "Coupons: " + _cf_route,
                                  dict(asin=asin_field, sku=sku_field, qty=qty_field, buyer=buyer,
                                       city=city, state=state, order_note=order_note))
                _lf2 = (asin_coupon_cache or {}).get("last_fail") or {}
                _sel = all(it.get("is_zoro_select") for it in resolved_items)
                _record_asin_verdict(primary_item.get("asin"),
                                     {"status": "PROMO_INELIGIBLE", "suggest": "Reseller", "note": "every coupon rejected the item at checkout"}
                                     if _lf2 and _lf2.get("item") == _lf2.get("total") and _lf2.get("regular_available")
                                     else {"status": "SELECT", "suggest": "Reseller", "note": "Zoro Select, no priority coupon at checkout"}
                                     if _sel else {"status": "NO_PRIORITY", "suggest": "Deciding", "note": "no coupon fits this cart at checkout"},
                                     f"${raw_cart_cost:.2f} ({sku_field})", "")
                return "skipped"
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
                        zip_code=addr.get("zip_code", ""), customer_name=buyer,
                        current_asin=asin_field,
                        current_sku=sku_field,
                        current_amazon_id=amazon_id)
                except Exception as _dup_err:
                    print(f"   [dup-recheck] error: {_dup_err}")
                    existing_wb = None
            # Only AUTO-RESOLVE on a real WB/WW order id. A bare "Duplicate" (name+zip match with
            # no extractable id) or DUP_CHECK_FAILED is NOT trustworthy enough to silently
            # permanent-block a possible-spend — those fall through to keep_failed() so they show
            # in the Locked Orders tab for a human to verify against Zoro.
            _real_wb = bool(existing_wb) and existing_wb not in (DUP_CHECK_FAILED, "Duplicate") \
                and (existing_wb.startswith("WB") or existing_wb.startswith("WW"))
            # The WB of the buyer's other order says nothing about this checkout. Stamping it would record
            # this order as placed when it may not be, so a person looks instead (the lock below).
            if _real_wb:
                try:
                    if _dup_is_buyers_other_order(driver, amazon_id, existing_wb, order_detail.get("buyer_orders_link"),
                                                  duplicate_services, addr["street_address"], addr.get("zip_code", ""),
                                                  buyer, asin_field, sku_field):
                        _real_wb = False
                except Exception as _dup_err:
                    print(f"   [dup-recheck] other-order check error: {_dup_err}")
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
        "status": "SUNDAY_HOLD" if _sunday_status else (result_status or "FAILED"),
        "notes": "; ".join(filter(None, [status_note, order_note])),
    })
    return "failed"


# ══════════════════════════════════════════════════════════════════
#  COUPON SHEET LOADER
# ══════════════════════════════════════════════════════════════════

def load_coupon_codes(force=False):
    """Load coupon codes with percentage/fixed-amount info from Google Sheets.
    force=True: read it on a reseller profile too (only to see which priority codes exist —
    resellers never apply a coupon).

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
    if not IS_COUPON_PROFILE and not force:
        return [], []
    print("\n🏷️  Loading coupon sheet...")
    metadata = None
    try:
        from utils.helpers import load_coupon_metadata
        metadata = load_coupon_metadata()
    except Exception:
        metadata = {}

    try:
        result = _execute_sheets_get(GOOGLE_SHEET_ID, f"'{GOOGLE_SHEET_COUPON_TAB}'!A:B")
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
        if ULTRA_MODE:      # the writer window finishes them while placing; the queue skips them
            print(f"⚡⚡ {len(_rush_pending)} journaled write-back(s) from the last run — the write window takes them")
        else:
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
            elif PLACE_ALL_MAPPED:
                # Reseller-only store (Wrenchly): every mapped ASIN is this profile's. sku_map is the
                # sheet load that just succeeded above (an empty map already stopped the cycle), so
                # discovery can never run on a half-read list. Unmapped ASINs stay out, as everywhere.
                PRIORITY_ASINS.extend(sorted({str(a).strip().upper() for a, v in sku_map.items()
                                              if str((v or {}).get("zoro_code") or "").strip()}))
                print(f"  ⭐ Place-all-mapped: {len(PRIORITY_ASINS)} mapped ASIN(s) from {GOOGLE_SHEET_SKU_TAB} "
                      f"count as this store's — no Priority column for {PROFILE_NAME}")
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

            # ── Priority-ASIN sweep / auto-check (coupon profiles) — BEFORE any placement ──
            if _maybe_run_priority_check(driver, sku_map, coupon_codes, asin_coupon_cache):
                print("  🔁 the sweep changed Priority ASIN Control — restarting the cycle with fresh columns")
                continue

            # ── Scrape orders from Amazon SC ──────────────────────
            if ASIN_SEARCH:
                print(f"\n🔍 Searching Amazon SC by ASIN: {ASIN_SEARCH}")
            else:
                print(f"\n🔍 Scraping '{TARGET_LABEL}' orders from Amazon Seller Central...")
            scraped_orders = scrape_order_ids(driver, label=TARGET_LABEL, asin_search=ASIN_SEARCH)
            if not ASIN_SEARCH:
                scraped_orders = _only_labelled(scraped_orders, TARGET_LABEL)
            for o in scraped_orders:
                o["_label_matched"] = TARGET_LABEL
                o["_margin_thresh"] = MIN_MARGIN
                
            # If a second target label is defined (e.g. Coupon profile), scrape it too
            if TARGET_LABEL_2 and not ASIN_SEARCH:
                print(f"\n🔍 Scraping secondary label '{TARGET_LABEL_2}' from Amazon Seller Central...")
                scraped_2 = _only_labelled(scrape_order_ids(driver, label=TARGET_LABEL_2, asin_search=None),
                                           TARGET_LABEL_2)
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
                # Amazon's search does not return a tag with a Zoro code after it ("Manual-R G7467735":
                # all 183 such orders up to 3 Nov 2026 came from the buyer-action view). Priority discovery
                # reads that view, but Labels Only turns it off, so read it here for the tags and nothing
                # else: every other order on it stays out of a labels-only run.
                if LABELS_ONLY and USE_FAVORITES_DISCOVERY:
                    print("\n🖐️  Labels only: looking for Manual tags with a Zoro code in the buyer-action view…")
                    try:
                        _view_orders = scrape_order_ids(driver, favorites=True)
                    except Exception as _me:
                        _view_orders = []
                        print(f"    ⚠️  buyer-action view failed ({_me}) — those tags wait for the next cycle")
                    _queued_before = len(scraped_orders)
                    for _fo in _view_orders:
                        if _fo["amazon_id"] not in _seen_so_far:
                            _queue_manual_from_view(_fo, scraped_orders, _seen_so_far)
                    print(f"    🖐️  {len(scraped_orders) - _queued_before} coded manual-override order(s) queued "
                          f"from {len(_view_orders)} in the view")

            # ── Discover priority-ASIN orders (pending/unlabelled) ──────────────
            _hold_reason = _sweep_active() if IS_RESELLER_PROFILE else None
            if _hold_reason:
                print(f"\n  ⏸️  Coupon sweep in progress for the {_STORE} store — {_hold_reason}. "
                      f"Priority/Deciding discovery is HELD this cycle so nothing is bought at reseller price "
                      f"minutes before its ASIN moves to the Coupon column; labelled orders still run.")
            if (PRIORITY_ASINS or DECIDING_ASINS or SUPERVISE_UNLABELLED) and not ASIN_SEARCH and not _priority_load_failed \
                    and not _hold_reason:
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
                    _released_asins = _oos_released_asins()
                    _released_n = Counter()
                    matched = skipped_label = skipped_asin = 0
                    deciding_matched = 0
                    needs_priority_flagged = 0   # unlabelled + non-priority → Needs Priority panel
                    supervised_added = 0         # unlabelled + mapped → supervisor queue (coupon profiles)
                    sorted_elsewhere = 0         # ASIN sits in another column of this store → not ours
                    unmapped_priority = 0        # priority/deciding ASIN with no Zoro code → skipped cheaply
                    _unmapped_seen = set()
                    _mapped_upper = {str(k).strip().upper() for k, v in (sku_map or {}).items()
                                     if str((v or {}).get("zoro_code") or "").strip()}
                    _supervise_ok = SUPERVISE_UNLABELLED and bool(STORE_SORTED_ASINS)
                    for fo in fav_orders:
                        if fo["amazon_id"] in seen_ids:
                            continue
                        # A Manual tag the 'Manual-C/-R' search did not return — "Manual-C G7467735"
                        # carries a Zoro code after it (30 Oct 2026), and Amazon's search does not
                        # match the tag inside a longer Seller order ID.
                        if _queue_manual_from_view(fo, scraped_orders, seen_ids):
                            continue
                        _fo_asin = (fo.get("asin") or "").strip().upper()
                        if _oos_release_mark(fo, _released_asins):
                            _released_n[_fo_asin] += 1
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
                            if not (fo.get("seller_order_id") or "").strip() and PLACE_ALL_MAPPED:
                                # Place-all-mapped (Wrenchly): every mapped ASIN is in the set, so this
                                # one has no Zoro code — it belongs on the Unmapped panel, once per cycle.
                                needs_priority_flagged += 1
                                if _fo_asin and _fo_asin not in _unmapped_seen:
                                    _unmapped_seen.add(_fo_asin)
                                    _log_order({"amazon_id": fo.get("amazon_id", ""), "asin": _fo_asin, "sku": "UNKNOWN",
                                                "qty": fo.get("qty") or 1, "buyer": fo.get("buyer", ""),
                                                "status": "UNMAPPED_ASIN",
                                                "notes": "ASIN not in verified SKU sheet — map it and the next cycle places it"})
                            elif not (fo.get("seller_order_id") or "").strip():
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
                        if _fo_asin and _fo_asin not in _mapped_upper:
                            # An unmapped priority ASIN cannot be placed; scraping its order pages every
                            # cycle only burns time (926 page loads for one ASIN on 8 Sep). Flag it ONCE per
                            # cycle so the Unmapped panel still shows it, and move on.
                            unmapped_priority += 1
                            if _fo_asin not in _unmapped_seen:
                                _unmapped_seen.add(_fo_asin)
                                print(f"    ⚠️  priority ASIN {_fo_asin} has no Zoro code in the verified SKU sheet — "
                                      f"its orders are skipped until it is mapped")
                                _log_order({"amazon_id": fo.get("amazon_id", ""), "asin": _fo_asin, "sku": "UNKNOWN",
                                            "qty": fo.get("qty") or 1, "buyer": fo.get("buyer", ""), "status": "UNMAPPED_ASIN",
                                            "notes": "priority ASIN not in verified SKU sheet — map it (orders skipped without page loads)"})
                            continue
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
                    if _released_n:
                        print(f"    🔓 Released from OOS by the sheet: {sum(_released_n.values())} order(s) — "
                              + ", ".join(f"{a} x{n}" for a, n in _released_n.most_common(8))
                              + ". Handled as unlabelled orders from here.")
                    print(f"    ✅ {matched} priority order(s) added "
                          f"[{deciding_matched} deciding] "
                          f"+ {supervised_added} unlabelled → supervisor "
                          f"(skipped {skipped_asin} non-priority [{needs_priority_flagged} unlabelled → "
                          f"flagged Needs-Priority, {sorted_elsewhere} sorted in another column], "
                          f"{skipped_label} already labelled"
                          + (f", {unmapped_priority} priority order(s) of unmapped ASINs skipped" if unmapped_priority else "") + ")")
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

            # ── Focus Priority Coupon ("Place with Priority"): only orders this coupon can take ──
            _foc_q = _focus_query()
            if _foc_q and IS_COUPON_PROFILE:
                _pri_loaded = coupon_codes[0] if isinstance(coupon_codes, tuple) else []
                _foc_codes = _focus_codes(_foc_q, _pri_loaded)
                if not _foc_codes:
                    print(f"\n  🎯 FOCUS PRIORITY COUPON '{_foc_q}' is NOT in today's Coupons tab "
                          f"({', '.join(str((c or {}).get('code')) for c in _pri_loaded) or 'no priority coupons loaded'}) "
                          f"— nothing is placed in focus mode; fix the code or untick 'Place with Priority'")
                    scraped_orders = []
                else:
                    kept, deferred, to_probe = _focus_filter(driver, scraped_orders, _foc_codes, sku_map)
                    print(f"\n  🎯 FOCUS PRIORITY COUPON ACTIVE: {', '.join(_foc_codes)} — placing {len(kept)} known-good order(s) first; "
                          f"{to_probe} unknown ASIN(s) will be tested in one pass right after; "
                          f"{len(deferred) - sum(1 for o in deferred if (o.get('asin') or '').strip().upper() in {a for a, _, _ in _FOCUS_TO_PROBE})} deferred (placed normally once focus is off)")
                    _DLOG.log("focus", profile=PROFILE_NAME, action="filter", coupon=",".join(_foc_codes),
                              reason=f"kept {len(kept)}, deferred {len(deferred)}, to_probe {to_probe}",
                              kept=[o.get("amazon_id") for o in kept], deferred=[o.get("amazon_id") for o in deferred])
                    scraped_orders = kept
                if not scraped_orders:
                    _run_log["orders_scraped"] = 0
                    _save_log()
                    if _FOCUS_TO_PROBE and _foc_codes:
                        _focus_probe_all(driver, _foc_codes)        # nothing known yet → test everything now
                        continue                                    # and place the passers immediately
                    if SINGLE_CYCLE:
                        return
                    print("   -> focus mode: nothing to place right now — next cycle in 60 s")
                    time.sleep(60)
                    continue

            # ── SUNDAY MODE: held orders are not re-scraped or re-priced ──
            _sun_cycle = _sunday_mode()
            if _sun_cycle:
                print(f"\n🗓️  SUNDAY MODE — {_sunday_rule_text(_sun_cycle)}")
            if _SUNDAY_HOLDS and scraped_orders:
                _held_ids = {o["amazon_id"] for o in scraped_orders
                             if _sunday_parked(o["amazon_id"], coupon_codes, o.get("_label_matched", ""))}
                if _held_ids:
                    scraped_orders = [o for o in scraped_orders if o["amazon_id"] not in _held_ids]
                    print(f"   {len(_held_ids)} order(s) already held — not re-checked until Sunday mode is "
                          f"unticked, its numbers change, or the priority coupons change")

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
                _crew = None
                _ultra_dups, _ultra_dup_tried = {}, set()
                if ULTRA_MODE:
                    # An order still journaled with a label was already dealt with; Amazon just
                    # hasn't been told yet, so its old label brought it back into the queue.
                    with _rush_lock:
                        _held = {a for a, e in _rush_pending.items() if e.get("label")}
                    if _held:
                        _before = len(_rq)
                        _rq = [o for o in _rq if o["amazon_id"] not in _held]
                        if len(_rq) != _before:
                            print(f"⚡⚡ {_before - len(_rq)} order(s) skipped: already dealt with, label still journaled")
                    _rq = _ultra_hold_oos(driver, _rq, sku_map)
                    _crew = _ultra_start(_rq)
                    if _crew:
                        print(f"\n⚡⚡ ULTRA: placing {len(_rq)} orders here; Amazon pages and write-backs "
                              f"run on their own windows")
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
                    if _crew:
                        details_map = {}
                    else:
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
                    dup_map = _ultra_dups if _crew else {}
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
                                                   "zip": _a.get("zip_code", ""),
                                                   "asin": _o.get("asin", ""),
                                                   "sku": _o.get("sku", "")})
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
                        if ULTRA_MODE and _ultra_is_held(order):
                            # Its listing ran out earlier in this cycle: no page, no check, no label.
                            print(f"  ⏸️  {amazon_id}: {asin} is out of stock — held, not marked")
                            continue
                        print_rich_summary(PROFILE_NAME, total_orders, processed_count,
                                           success_count, failed_count, low_profit_count,
                                           f"Processing {amazon_id}")
                        print(f"\n{'─'*60}")
                        print(f"  [{_rn:02d}/{len(_rq):02d}] Amazon ID: {amazon_id}")
                        if asin:
                            print(f"  List ASIN: {asin}  Qty: {qty}")
                        print(f"{'─'*60}")

                        detail = _crew.get_detail(amazon_id) if _crew else details_map.get(amazon_id)
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
                        if detail_status == "cancel_requested":
                            _cancel_requested_skip(amazon_id, asin, sku)
                            _run_log["skipped"] += 1
                            continue
                        if detail_status in ("pending", "cancelled", "canceled", "shipped"):
                            print(f"  ⏭️  Order status is {detail_status.title()} — skipping")
                            _run_log["skipped"] += 1
                            continue
                        mid = _oos_release_open(order, detail)
                        if mid.startswith("WB"):
                            print(f"  ⏭️  Already processed ({mid})")
                            _run_log["skipped"] += 1
                            continue
                        if mid and mid.upper() in ("OOS", "DISCONTINUED", "DUPLICATE", "SR", "NET30", "20"):
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

                        if _crew:
                            _ultra_dup_prefill(_crew, order, detail, dup_map, _ultra_dup_tried, duplicate_services)
                            _crew.busy_id = amazon_id      # its journal entry may still grow
                            if _rn % 10 == 0:
                                print(f"  ⚡⚡ ULTRA: {_crew.status_line()} | {len(_rush_pending)} write-back(s) queued")

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
                                if _crew:
                                    driver = _ultra_wait_for_zoro(driver)

                        if _crew:
                            _crew.busy_id = None
                            if getattr(driver, "_zuhra_net_stall", False):
                                driver = _ultra_wait_for_zoro(driver)

                        processed_count += 1
                        cycle_order_count += 1
                        _save_log()

                    # Phase C: write this batch's WBs + notes back to Amazon
                    if _rush_pending and not _ultra_writer_alive():
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
                if detail_status == "cancel_requested":
                    _cancel_requested_skip(amazon_id, asin, sku)
                    _run_log["skipped"] += 1
                    continue
                if detail_status in ("pending", "cancelled", "canceled", "shipped"):
                    print(f"  ⏭️  Order status is {detail_status.title()} — skipping")
                    _run_log["skipped"] += 1
                    continue

                mid = _oos_release_open(order, detail)
                if mid.startswith("WB"):
                    print(f"  ⏭️  Already processed ({mid})")
                    _run_log["skipped"] += 1
                    continue
                if mid and mid.upper() in ("OOS", "DISCONTINUED", "DUPLICATE", "SR", "NET30", "20"):
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
            print(f"❌ Loop Error: {_first_line(e)}")

        _ultra_stop()

        # Rush: whatever is still journaled at cycle end gets written back now
        # (partial batches, error exits). Failures stay journaled for next cycle.
        if _rush_pending:
            try:
                driver = _rush_flush_amazon_writes(driver)
            except Exception as e:
                print(f"  ⚠️  Rush flush error at cycle end: {e}")

        # Listings whose Zoro code and every alternative ran out this cycle: decide PL / DS, tell the
        # team, watch for the restock (engine/listing_guard.py). Here, with the orders done and Chrome
        # idle — never in the middle of an order.
        try:
            from engine import listing_guard
            driver = listing_guard.handle_pending(driver, STORE_ACCOUNT, PROFILE_NAME) or driver
        except Exception as e:
            print(f"  ⚠️  listing guard: {type(e).__name__}: {e}")

        # ── Cycle summary ─────────────────────────────────────────
        _run_log["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_log()
        _save_coupon_intel(asin_coupon_cache)

        print_rich_summary(PROFILE_NAME, total_orders, processed_count,
                           success_count, failed_count, low_profit_count,
                           "Cycle Complete." + ("" if SINGLE_CYCLE else " Waiting 10 seconds..."))

        # Focus coupon: the known-good orders are placed — now test every unknown ASIN in ONE pass
        # and start the next cycle at once so the ones that pass are placed without waiting.
        if _FOCUS_TO_PROBE and IS_COUPON_PROFILE:
            try:
                _fc = _focus_active_codes(coupon_codes[0] if isinstance(coupon_codes, tuple) else [])
                if _fc:
                    _focus_probe_all(driver, _fc)
                    if not SINGLE_CYCLE:
                        continue
            except Exception as e:
                print(f"  ⚠️ focus probe pass: {e}")

        if SINGLE_CYCLE:
            print(f"\n  📋 Log: {_log_file}")
            return

        time.sleep(10)


if __name__ == "__main__":
    main()
