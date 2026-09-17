import functools
import pandas as pd
import time
import os
import base64
import io
import re
import html
import random
import json
import argparse
import platform
import sys
from datetime import datetime

# Allow sibling packages (utils, platforms, checkout) to be found when this
# script is executed directly (python engine/tracking.py) rather than as a module.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_AUTOMATIONS_DIR = os.path.dirname(_SCRIPT_DIR)
if _AUTOMATIONS_DIR not in sys.path:
    sys.path.insert(0, _AUTOMATIONS_DIR)

print = functools.partial(print, flush=True)
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pdfplumber
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from platforms.auth import auto_login, is_login_page, ensure_logged_in
from platforms.amazon import write_seller_note

try:
    from utils.unified_db import update_order
    _HAS_UNIFIED_DB = True
except Exception:
    _HAS_UNIFIED_DB = False

# --- START: Script Configuration ---

print("\n" + "═" * 60)
print("  🤖  AUTO TRACKING ID UPLOADER  |  Initializing...")
print("═" * 60)

try:
    script_directory = os.path.dirname(os.path.abspath(__file__))
except NameError:
    script_directory = os.getcwd()

TOKEN_DIRECTORY = os.path.join(os.path.dirname(script_directory), "data", "All_Tokens")
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
MASTER_CREDENTIALS_FILE = os.path.join(TOKEN_DIRECTORY, "AB ECOM Credentials.json")
MESSAGE_LOG_FILE = os.path.join(os.path.dirname(script_directory), "data", "message_log.json")
TRACKING_UPLOAD_LOG_FILE = os.path.join(os.path.dirname(script_directory), "data", "tracking_upload_log.json")

account_names = [
    "qurtdis", "abecomllc26", "asad_amazon_us", "salesmercentile", "salesmercentile1",
    "toolzsolutionsllc220", "inhur_llc", "wrenchlyllc"
]

ACCOUNTS = []
for name in account_names:
    ACCOUNTS.append({
        "name": name,
        "credentials_file": MASTER_CREDENTIALS_FILE,
        "token_file": os.path.join(TOKEN_DIRECTORY, f"{name}_token.json")
    })

DOWNLOAD_DIRECTORY = script_directory  # default; overridden per-account in main_workflow

AMAZON_ACCOUNTS = [
    {
        "key": "hathoro",
        "name": "Hathoro™️",
        "profile_dir": "hathoro_tracking_ids",
        "legacy_profile_dir": "HathoroProfile",
        "send_messages": True  # ✅ This account WILL send messages
    },
    {
        "key": "hoank",
        "name": "HOANK™️",
        "profile_dir": "hoank_tracking_ids",
        "legacy_profile_dir": "hoank_normal_orders",
        "send_messages": True  # ✅ This account WILL send messages
    },
]

# --- Selectors and URLs ---
REPORT_DOWNLOAD_URL = "https://sellercentral.amazon.com/reportcentral/FlatFileAllOrdersReport/1"
ORDERS_PAGE_URL = "https://sellercentral.amazon.com/orders-v3/ref=xx_myo_head_xx"
DROPDOWN_HOST_SELECTOR = 'kat-dropdown.daily-time-picker-kat-dropdown-normal'
REQUEST_REPORT_BUTTON_SELECTOR = 'kat-button[label="Request Download"]'
LATEST_DOWNLOAD_BUTTON_SELECTOR = 'kat-table-row[role="row"]:first-of-type kat-button[label="Download"]'
INITIAL_CONFIRM_SHIPMENT_BUTTON_SELECTOR = '[data-test-id="confirm-shipment-button"]'
TRACKING_ID_FIELD_SELECTOR = 'input[data-test-id="text-input-tracking-id"]'
FINAL_CONFIRM_SHIPMENT_BUTTON_SELECTOR = 'span[data-test-id="confirm-shipment-button-action"]'

# ── Multi-package ("Add a Shipment") selectors ──────────────────────────────
# Confirmed against the live DOM (data/confirm_shipment_structure.json, captured
# 2026-08-07). Each package Amazon renders carries its own numeric suffix, e.g.
# CarrierListDropdown-378215 — but the suffix is opaque and only allocated when
# the package is created, so everything below is matched by prefix and used in
# DOM order (package 1 first) rather than by parsing the id.
ADD_PACKAGE_BUTTON_SELECTOR = 'span[data-test-id="add-package-button-action"]'
PKG_QTY_SELECT_SELECTOR = 'select[id^="selected-item-"]'
PKG_CARRIER_SELECT_SELECTOR = 'select[id^="CarrierListDropdown-"]'
PKG_SERVICE_SELECT_SELECTOR = 'select[id^="shipping-service-dropdown"]'
# --- NEW: Exclusion File Configuration ---
# Mutable holders (not bare globals): these are written from inside deeply
# nested run loops, where assigning to a plain module global would make the
# name local to that function and raise UnboundLocalError on read.
_STRUCTURE_CAPTURED = []      # non-empty once the confirm-shipment DOM is dumped
_multi_package_orders = []    # orders whose extra tracking IDs weren't uploaded

EXCLUSION_FILE_PATH = os.path.join(os.path.dirname(script_directory), "DS_Closing", "Discounted_Closing", "Urgent_Calculations.xlsx")
MANUAL_TRACKING_BLOCKER_FILE = os.path.join(script_directory, "manual_tracking_blocker_ids.txt")

print("✅  Configuration loaded successfully.")


def _default_chrome_base_dir():
    """Returns an OS-appropriate base directory for Chrome user-data folders."""
    home = os.path.expanduser("~")
    system = platform.system().lower()

    if system == "darwin":
        return os.path.join(home, "Library", "Application Support", "Google", "Chrome")
    if system == "windows":
        return os.path.join(home, "AppData", "Local", "Google", "Chrome", "User Data")
    return os.path.join(home, ".config", "google-chrome")


def resolve_user_data_dir(account_config):
    """Resolves a per-account Chrome user-data directory; creates default tracking dirs if needed."""
    base_dir = os.environ.get("CHROME_USER_DATA_BASE_DIR", "").strip() or _default_chrome_base_dir()
    key_upper = account_config["key"].upper()
    allow_legacy_fallback = os.environ.get("TRACKING_ALLOW_LEGACY_FALLBACK", "0").strip().lower() in {"1", "true", "yes"}

    # Highest priority: explicit absolute path, e.g. TRACKING_HOANK_USER_DATA_DIR
    explicit_path = os.environ.get(f"TRACKING_{key_upper}_USER_DATA_DIR", "").strip()
    if explicit_path:
        if os.path.isdir(explicit_path):
            return explicit_path
        raise FileNotFoundError(
            f"Configured path does not exist for {account_config['name']}: {explicit_path}"
        )

    # Next: subdirectory override, e.g. TRACKING_HOANK_PROFILE_DIR=hoank_tracking_ids
    override_profile_dir = os.environ.get(f"TRACKING_{key_upper}_PROFILE_DIR", "").strip()

    # Priority:
    # 1) explicit profile-dir override
    # 2) dedicated tracking profile (auto-create if missing)
    # 3) optional legacy fallback only when TRACKING_ALLOW_LEGACY_FALLBACK=1
    if override_profile_dir:
        override_path = os.path.join(base_dir, override_profile_dir)
        os.makedirs(override_path, exist_ok=True)
        return override_path

    tracking_profile_dir = account_config.get("profile_dir", "")
    if tracking_profile_dir:
        tracking_path = os.path.join(base_dir, tracking_profile_dir)
        os.makedirs(tracking_path, exist_ok=True)
        return tracking_path

    if allow_legacy_fallback:
        legacy_profile_dir = account_config.get("legacy_profile_dir", "")
        if legacy_profile_dir:
            legacy_path = os.path.join(base_dir, legacy_profile_dir)
            if os.path.isdir(legacy_path):
                return legacy_path

    raise FileNotFoundError(
        f"No usable Chrome profile directory found for {account_config['name']} under {base_dir}. "
        f"Set TRACKING_{key_upper}_USER_DATA_DIR or TRACKING_{key_upper}_PROFILE_DIR."
    )


def parse_runtime_args():
    parser = argparse.ArgumentParser(description="Auto Tracking ID Uploader")
    parser.add_argument(
        "--run-mode",
        choices=["sequential", "parallel"],
        default=os.environ.get("TRACKING_RUN_MODE", "sequential").strip().lower() or "sequential",
        help="Run accounts one-by-one (sequential) or side-by-side (parallel).",
    )
    parser.add_argument(
        "--accounts",
        choices=["both", "hathoro", "hoank"],
        default=os.environ.get("TRACKING_ACCOUNTS", "both").strip().lower() or "both",
        help="Choose which store account(s) to run.",
    )
    parser.add_argument(
        "--parallel-style",
        choices=["synchronized", "independent"],
        default=os.environ.get("TRACKING_PARALLEL_STYLE", "independent").strip().lower() or "independent",
        help="In parallel mode: synchronized waits for all accounts, independent lets each account loop on its own timer.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=int,
        default=int(os.environ.get("TRACKING_SLEEP_SECONDS", "70")),
        help="Pause between cycles. Used globally in sequential/synchronized, or per account in independent mode.",
    )
    parser.add_argument(
        "--single-cycle",
        action="store_true",
        help="Run once and exit (skip the 70-second repeating loop).",
    )
    parser.add_argument(
        "--skip-messages",
        action="store_true",
        default=False,
        help="Skip sending customer messages even if account has send_messages=True.",
    )
    parser.add_argument(
        "--max-uploads",
        type=int,
        default=0,
        help="Stop after N successful tracking uploads (0 = no limit). Use --max-uploads 1 "
             "with --single-cycle to verify the whole flow on exactly one order.",
    )
    args = parser.parse_args()
    if args.sleep_seconds < 0:
        parser.error("--sleep-seconds must be 0 or greater")
    if args.max_uploads < 0:
        parser.error("--max-uploads must be 0 or greater")
    return args


def select_accounts(account_option):
    if account_option == "both":
        return AMAZON_ACCOUNTS
    return [cfg for cfg in AMAZON_ACCOUNTS if cfg.get("key") == account_option]


def _harden_driver(driver):
    try:
        from utils.helpers import apply_cdp_stealth
        apply_cdp_stealth(driver)          # replaces the banner-painting launch flag
    except Exception:
        pass
    return __harden_timeouts(driver)


def __harden_timeouts(driver):
    """One stalled page load must not wedge the session: fail the navigation at
    60s instead of every later command eating the client's 120s read timeout."""
    try:
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(30)
    except Exception:
        pass
    return driver


def create_chrome_driver(options):
    """
    Creates a Chrome webdriver session.
    Primary path uses Selenium Manager (usually best version match).
    Falls back to webdriver-manager if needed.
    """
    try:
        return _harden_driver(webdriver.Chrome(options=options))
    except Exception as primary_error:
        print(f"  ⚠️  Selenium Manager launch failed: {primary_error}")
        print("  ↪️  Retrying with webdriver-manager fallback...")

    service = Service(ChromeDriverManager().install())
    return _harden_driver(webdriver.Chrome(service=service, options=options))


def launch_or_attach_chrome(account_config, profile_path, download_dir=None):
    """
    On macOS, launching Chrome via LaunchServices ('open -a') avoids macOS 27+
    TCC / com.apple.provenance sandbox crashes that occur when ChromeDriver spawns
    Chrome directly as a child of a non-terminal GUI app (Launcher).
    Once Chrome opens its remote-debugging-port, Selenium connects cleanly via debuggerAddress.
    """
    import socket
    import subprocess
    from utils.helpers import CHROME_SOFT_FLAGS, mark_profile_clean_exit
    mark_profile_clean_exit(profile_path)
    port = 9331 if account_config.get("key") == "hoank" else 9333

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    port_open = sock.connect_ex(('127.0.0.1', port)) == 0
    sock.close()

    if not port_open:
        if sys.platform == "darwin":
            cmd = [
                "open", "-a", "Google Chrome", "-n",
                "--args",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_path}",
                "--window-size=1920,1080",
                "--hide-crash-restore-bubble",
                "--no-first-run",
                "--no-default-browser-check",
            ] + CHROME_SOFT_FLAGS
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            from utils.helpers import get_chrome_paths
            _, chrome_exe = get_chrome_paths(os.path.basename(profile_path))
            cmd = [
                chrome_exe,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_path}",
                "--window-size=1920,1080",
                "--hide-crash-restore-bubble",
                "--no-first-run",
                "--no-default-browser-check",
            ] + CHROME_SOFT_FLAGS
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        for _ in range(30):
            time.sleep(0.5)
            s = socket.socket()
            s.settimeout(0.5)
            if s.connect_ex(('127.0.0.1', port)) == 0:
                s.close()
                port_open = True
                break
            s.close()
        if port_open:
            # WE launched it → it is closed again when this process stops (launcher Stop /
            # window close send SIGTERM; see the handler in __main__). Between cycles it stays.
            from utils.helpers import register_owned_chrome
            register_owned_chrome(profile_path, port)

    if port_open:
        opts = Options()
        opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
        try:
            d = _harden_driver(webdriver.Chrome(options=opts))
            if download_dir:
                try:
                    d.execute_cdp_cmd("Page.setDownloadBehavior", {
                        "behavior": "allow",
                        "downloadPath": download_dir
                    })
                except Exception:
                    pass
            return d
        except Exception as e:
            print(f"  ⚠️  Debugger attach on port {port} failed: {e}")

    return None


# Cycle health shared with the loops below: a cycle that could not reach Gmail, or that
# halted (order report not downloadable, session gone), must not be retried every minute —
# on 16 Sep 2026 one halt repeated 342 times in 6.5 h and on 17 Sep two runs printed
# 300,000 lines against a broken TLS bundle. Backoff: Gmail down → ≥15 min; halts → 2 min
# per consecutive halt, capped at 30 min.
_CYCLE_HEALTH = {"halts": 0, "gmail_down": False}


def cycle_sleep_seconds(base_seconds):
    extra = 0
    if _CYCLE_HEALTH.get("gmail_down"):
        extra = max(extra, 900)
    h = int(_CYCLE_HEALTH.get("halts") or 0)
    if h:
        extra = max(extra, min(120 * h, 1800))
    if extra > base_seconds:
        why = "Gmail unreachable" if _CYCLE_HEALTH.get("gmail_down") else f"{h} consecutive halted run(s)"
        print(f"  ⏸️   backing off: {why} — next run in {extra // 60} min instead of {base_seconds}s")
        return extra
    return base_seconds


def run_account_loop(account_config, sleep_seconds):
    """Runs one account forever with its own cadence."""
    driver = None
    while True:
        print("\n" + "─" * 60)
        print(f"  🏪  ACCOUNT: {account_config['name'].upper()}  |  {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("─" * 60)
        driver = main_workflow(account_config, passed_driver=driver)
        _s = cycle_sleep_seconds(sleep_seconds)
        print(f"\n  ⏸️   {account_config['name']} sleeping {_s}s before next run...")
        time.sleep(_s)

# --- Message Log Helpers ---

def load_message_log():
    """Loads the persistent message log from JSON. Returns an empty dict if not found."""
    if os.path.exists(MESSAGE_LOG_FILE):
        try:
            with open(MESSAGE_LOG_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Could not read message log: {e}")
    return {}

def save_message_log(log):
    """Saves the message log dict to JSON."""
    try:
        with open(MESSAGE_LOG_FILE, "w") as f:
            json.dump(log, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save message log: {e}")


def split_qty_across_packages(total_qty, n_packages):
    """Spread `total_qty` units over `n_packages` as evenly as possible.

    Remainder goes to the earliest packages, so qty 3 in 2 packages → [2, 1]
    (never [1, 1] — every unit must be assigned or Amazon leaves the order
    partially unshipped).
    """
    if n_packages < 1 or total_qty < 1:
        return []
    base, rem = divmod(total_qty, n_packages)
    return [base + (1 if i < rem else 0) for i in range(n_packages)]


def remaining_qty_on_form(driver):
    """Units still available to ship, read from the package qty dropdown.

    Amazon only lists options for what's left (an already-confirmed order shows
    just "Qty: 0"), so the highest option value is the authoritative remaining
    quantity — more reliable than any count carried in from elsewhere.
    Returns 0 when the form isn't present or nothing is left.
    """
    try:
        sels = driver.find_elements(By.CSS_SELECTOR, PKG_QTY_SELECT_SELECTOR)
        if not sels:
            return 0
        values = []
        for o in Select(sels[0]).options:
            raw = (o.get_attribute("value") or "").strip()
            if raw.isdigit():
                values.append(int(raw))
            else:
                m = re.search(r'(\d+)', o.text or "")
                if m:
                    values.append(int(m.group(1)))
        return max(values) if values else 0
    except Exception:
        return 0


def _select_by_value_or_text(el, wanted):
    """Set a <select> by exact value, else exact text, else substring of text."""
    sel = Select(el)
    opts = sel.options
    for o in opts:
        if (o.get_attribute("value") or "") == str(wanted):
            sel.select_by_value(o.get_attribute("value"))
            return True
    for o in opts:
        if (o.text or "").strip().lower() == str(wanted).strip().lower():
            sel.select_by_visible_text(o.text)
            return True
    for o in opts:
        if str(wanted).strip().lower() in (o.text or "").strip().lower():
            sel.select_by_visible_text(o.text)
            return True
    return False


def fill_multi_package_tracking(driver, tracking_ids, total_qty, carrier=None, service=None):
    """Put one tracking ID per package on the confirm-shipment form.

    Amazon renders "Add a Shipment" (add-package-button-action); each click adds
    a package block with its own qty select, carrier/service dropdowns and
    tracking input. Units are split across packages by
    split_qty_across_packages().

    Returns True only when every package was fully populated. On ANY mismatch it
    returns False WITHOUT clicking Confirm, so the caller falls back to the
    existing single-tracking path — a half-filled form must never be submitted
    against a real customer order.
    """
    tids = [t.strip() for t in tracking_ids if t and t.strip()]
    n = len(tids)
    if n < 2:
        return False

    try:
        total_qty = int(total_qty or 0)
    except (TypeError, ValueError):
        total_qty = 0
    if total_qty < n:
        # Amazon can only split when there's at least one unit per package.
        print(f"     ⏭️  Cannot split: qty {total_qty} < {n} tracking IDs "
              f"(Amazon needs ≥1 unit per package)")
        return False

    shares = split_qty_across_packages(total_qty, n)
    print(f"     📦  Multi-package: {n} packages, qty split {shares}")

    # A package added via "Add a Shipment" starts with its carrier/service on
    # "Select an option" — Amazon refuses to confirm until both are set. Copy
    # whatever package 1 is using; if package 1 has none either, bail out rather
    # than guess a carrier (a wrong carrier breaks buyer-facing tracking).
    def _selected_text(el):
        try:
            v = Select(el).first_selected_option
            t = (v.text or "").strip()
            return "" if t.lower().startswith("select an option") else t
        except Exception:
            return ""

    try:
        _c0 = driver.find_elements(By.CSS_SELECTOR, PKG_CARRIER_SELECT_SELECTOR)
        _s0 = driver.find_elements(By.CSS_SELECTOR, PKG_SERVICE_SELECT_SELECTOR)
        carrier = carrier or (_selected_text(_c0[0]) if _c0 else "")
        service = service or (_selected_text(_s0[0]) if _s0 else "")
    except Exception:
        pass
    if not carrier:
        print("     ⚠️  No carrier selected on package 1 — refusing to guess one; "
              "falling back to single tracking")
        return False
    print(f"     🚚  Carrier '{carrier}'"
          + (f" / service '{service}'" if service else " (no service set)")
          + " will be applied to every package")

    try:
        for i, tid in enumerate(tids):
            if i > 0:
                before = len(driver.find_elements(By.CSS_SELECTOR, TRACKING_ID_FIELD_SELECTOR))
                add_btn = driver.find_element(By.CSS_SELECTOR, ADD_PACKAGE_BUTTON_SELECTOR)
                driver.execute_script("arguments[0].click();", add_btn)
                # Wait for the new package block to actually mount.
                WebDriverWait(driver, 10).until(
                    lambda d: len(d.find_elements(
                        By.CSS_SELECTOR, TRACKING_ID_FIELD_SELECTOR)) > before)

            qty_sels = driver.find_elements(By.CSS_SELECTOR, PKG_QTY_SELECT_SELECTOR)
            car_sels = driver.find_elements(By.CSS_SELECTOR, PKG_CARRIER_SELECT_SELECTOR)
            svc_sels = driver.find_elements(By.CSS_SELECTOR, PKG_SERVICE_SELECT_SELECTOR)
            tid_inputs = driver.find_elements(By.CSS_SELECTOR, TRACKING_ID_FIELD_SELECTOR)

            if len(tid_inputs) <= i or len(qty_sels) <= i:
                print(f"     ⚠️  Package {i+1}: form did not render "
                      f"({len(tid_inputs)} tracking inputs, {len(qty_sels)} qty selects)")
                return False

            # Quantity for this package
            if not _select_by_value_or_text(qty_sels[i], shares[i]):
                avail = [o.text for o in Select(qty_sels[i]).options]
                print(f"     ⚠️  Package {i+1}: qty {shares[i]} not selectable (options: {avail})")
                return False

            # Carrier/service: only the packages we just added need setting —
            # package 1 already carries whatever the page defaulted to.
            if i > 0:
                if len(car_sels) > i and not _select_by_value_or_text(car_sels[i], carrier):
                    print(f"     ⚠️  Package {i+1}: carrier '{carrier}' not selectable")
                    return False
                if service and len(svc_sels) > i:
                    _select_by_value_or_text(svc_sels[i], service)

            fld = tid_inputs[i]
            fld.click()
            fld.clear()
            fld.send_keys(tid)
            print(f"     ✅  Package {i+1}: qty {shares[i]}  tracking {tid}")
            time.sleep(1)

        return True
    except Exception as e:
        print(f"     ⚠️  Multi-package fill failed ({e}) — falling back to single tracking")
        return False


def capture_confirm_shipment_structure(driver, amazon_id):
    """Dump the confirm-shipment DOM so the multi-package controls can be mapped.

    Amazon renders this page as a SPA, so its real markup (the per-package
    tracking inputs, the 'add package' control and the per-item quantity
    selects) only exists after the widget mounts — it can't be read from a
    static fetch. Saved once per run to data/confirm_shipment_structure.*.
    Never raises: capture is diagnostic and must not break an upload.
    """
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
    try:
        os.makedirs(out_dir, exist_ok=True)
        html_path = os.path.normpath(os.path.join(out_dir, "confirm_shipment_structure.html"))
        json_path = os.path.normpath(os.path.join(out_dir, "confirm_shipment_structure.json"))

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(f"<!-- order {amazon_id} | {datetime.now().isoformat()} -->\n")
            f.write(driver.page_source)

        # Every candidate control, with the attributes selectors are built from.
        probe = driver.execute_script("""
            function attrs(el) {
                var o = {tag: el.tagName.toLowerCase()};
                ['data-test-id','id','name','type','placeholder','aria-label',
                 'value','class'].forEach(function(a){
                    var v = el.getAttribute(a);
                    if (v) o[a] = String(v).slice(0, 120);
                });
                var t = (el.innerText || el.textContent || '').trim();
                if (t) o.text = t.slice(0, 80);
                return o;
            }
            var out = {inputs: [], selects: [], buttons: [], testIds: []};
            document.querySelectorAll('input').forEach(function(e){ out.inputs.push(attrs(e)); });
            document.querySelectorAll('select, kat-dropdown').forEach(function(e){ out.selects.push(attrs(e)); });
            document.querySelectorAll('button, kat-button, a[role=button], span[data-test-id]')
                .forEach(function(e){ out.buttons.push(attrs(e)); });
            document.querySelectorAll('[data-test-id]').forEach(function(e){
                out.testIds.push(e.getAttribute('data-test-id'));
            });
            out.testIds = Array.from(new Set(out.testIds));
            return out;
        """) or {}
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(probe, f, indent=2)

        print(f"     🔍  Confirm-shipment structure captured → "
              f"{os.path.basename(html_path)} / {os.path.basename(json_path)} "
              f"({len(probe.get('inputs', []))} inputs, "
              f"{len(probe.get('selects', []))} selects, "
              f"{len(probe.get('testIds', []))} test-ids)")
    except Exception as e:
        print(f"     ⚠️  Structure capture skipped: {e}")


def load_tracking_upload_log():
    """Loads the tracking upload audit log. Returns list of entries.
    Both tracking processes (HOANK + Hathoro) share this file: reads go through the
    same file lock the writers use, so a half-written file is never seen (that race
    produced 377 'Expecting value' reads and wiped the log between 14 and 17 Sep 2026)."""
    try:
        from utils.jsonstore import locked, read_json
        with locked(TRACKING_UPLOAD_LOG_FILE):
            data = read_json(TRACKING_UPLOAD_LOG_FILE, [], quarantine=True)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ Could not read tracking upload log: {e}")
        return []


def save_tracking_upload_log(entries):
    """Saves the tracking upload audit log (atomic: temp file + rename, under the lock)."""
    try:
        from utils.jsonstore import locked, write_json_atomic
        with locked(TRACKING_UPLOAD_LOG_FILE):
            write_json_atomic(TRACKING_UPLOAD_LOG_FILE, entries, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save tracking upload log: {e}")


def _page_shows_already_shipped(driver):
    """True when the order page shows a CONFIRMED shipment instead of a
    'Confirm shipment' button — tracking was already uploaded (previous run,
    by hand, or a stale report row). Without this check the flow burned the
    20s confirm-button wait + 20s tracking-field wait and logged a FAILED
    with a chromedriver stacktrace on every cycle for such orders.
    A partial shipment still shows a confirm button → returns False → upload
    proceeds for the remaining units."""
    try:
        return bool(driver.execute_script("""
            if (document.querySelector('[data-test-id="confirm-shipment-button"]'))
                return false;                     // still confirmable → not done
            const t = (document.body && document.body.innerText) || '';
            if (t.indexOf('Edit shipment') !== -1) return true;
            if (t.indexOf('Print packing slip') !== -1 && /Ship date/i.test(t)) return true;
            return false;
        """))
    except Exception:
        return False


def log_tracking_upload(amazon_id, wb_ids, tracking_ids, message_sent, message_type, status, note=""):
    """Append one tracking upload event to the audit log (one locked read-modify-write,
    so the two account processes never overwrite each other's entries)."""
    entry = {
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
        "amazon_order_id": amazon_id,
        "wb_order_ids": wb_ids if isinstance(wb_ids, list) else [wb_ids],
        "tracking_ids": tracking_ids if isinstance(tracking_ids, list) else [tracking_ids] if tracking_ids else [],
        "message_sent": message_sent,
        "message_type": message_type,
        "status": status,  # "success" | "failed" | "skipped_dup" | "no_tracking" | "already_shipped"
        "note": note,
    }
    try:
        from utils.jsonstore import mutate_json
        with mutate_json(TRACKING_UPLOAD_LOG_FILE, [], indent=2) as entries:   # locked read-modify-write
            if not isinstance(entries, list):
                entries.clear() if hasattr(entries, "clear") else None
            entries.append(entry)
            if len(entries) > 5000:
                del entries[:-5000]             # keep the last 5000 entries (in place)
    except Exception as e:
        print(f"⚠️ Could not save tracking upload log: {e}")

# --- Gmail Helper Functions ---

def get_gmail_service(credentials_path, token_path):
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                print(f"- Refreshing expired token: {os.path.basename(token_path)}")
                creds.refresh(Request())
                with open(token_path, "w") as token:
                    token.write(creds.to_json())
            except Exception as e:
                print(f"⚠️  [GMAIL WARNING] Token refresh failed for {os.path.basename(token_path)}: {e}. Skipping inbox.")
                return None
        else:
            print(f"⚠️  [GMAIL WARNING] No valid token found for {os.path.basename(token_path)}. Skipping without blocking.")
            return None
    try:
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        print(f"⚠️  [GMAIL WARNING] Could not build service for {os.path.basename(token_path)}: {e}. Skipping.")
        return None

def get_email_body(message):
    payload = message.get('payload', {})
    if 'parts' in payload:
        for part in payload['parts']:
            if part.get('mimeType') == 'text/html':
                data = part['body'].get('data', '')
                return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    if 'body' in payload and 'data' in payload['body']:
        return base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')
    return ""

def clean_html(raw_html):
    clean = re.sub(r'<(style|script).*?</\1>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    clean = html.unescape(clean)
    return re.sub(r'\s+', ' ', clean).strip()

def _click_radio_and_type(driver, wait, order_id, message_text, radio_priority=None):
    """
    Shared helper: clicks the best available radio button and types the message.
    radio_priority: list of kat-radiobutton values to try in order.
    Returns True on success, False if messaging is disabled or fails.
    """
    if radio_priority is None:
        radio_priority = ['contactBuyer', 'unexpectedProblem']

    # Page load buffer — let Amazon's JS fully render the options
    time.sleep(random.uniform(3, 5))

    # Check for access issues
    if "home" in driver.current_url.lower() or "not authorized" in driver.page_source.lower():
        print(f"     ⚠️  Order {order_id}: Page not accessible (access denied or not found).")
        return False

    # --- STEP 1: Click radio button (try each value in priority order) ---
    js_select_option = """
    function clickRadio(values) {
        for (let val of values) {
            let radio = document.querySelector('kat-radiobutton[value="' + val + '"]');
            if (radio) {
                radio.click();
                if (radio.shadowRoot) {
                    const input = radio.shadowRoot.querySelector('input');
                    const label = radio.shadowRoot.querySelector('label');
                    if (input) input.click();
                    if (label) label.click();
                }
                return { success: true, chosen: val };
            }
        }
        return { success: false };
    }
    return clickRadio(arguments[0]);
    """
    try:
        result = wait.until(lambda d: d.execute_script(js_select_option, radio_priority) or {})
        chosen = result.get('chosen', '?') if isinstance(result, dict) else '?'
        print(f"     ✔  Radio button selected: [{chosen}]")
        time.sleep(2)
    except Exception:
        print(f"     ⚠️  Order {order_id}: Messaging appears disabled (no clickable option found). Skipping.")
        return False

    # --- STEP 2: Inject the message and PROVE it landed ------------------------
    # document.execCommand('insertText') is deprecated and fails SILENTLY when the
    # textarea (inside kat-textarea's shadow root) is not truly focused or not laid out
    # yet. The old code never checked, then clicked Send anyway — that is how buyers
    # received EMPTY messages. Now: insert, read the value back, fall back to the native
    # setter + composed events (the same technique that fixed the seller-order-id field),
    # read back again, and refuse to send if the box is still empty.
    if not (message_text or "").strip():
        print(f"     ⛔  Order {order_id}: refusing to send — the message body is empty")
        return False

    def _textarea_value():
        try:
            return driver.execute_script(
                "var t=document.querySelector('kat-textarea');"
                "if(!t||!t.shadowRoot)return null;"
                "var a=t.shadowRoot.querySelector('textarea');"
                "return a ? a.value : null;")
        except Exception:
            return None

    try:
        js_ta = "return document.querySelector('kat-textarea').shadowRoot.querySelector('textarea');"
        textarea = wait.until(lambda d: d.execute_script(js_ta))

        # attempt 1 — execCommand (keeps Amazon's own input handling intact)
        driver.execute_script("""
            var ta = arguments[0];
            var msg = arguments[1];
            ta.focus();
            ta.select();
            document.execCommand('insertText', false, msg);
        """, textarea, message_text)
        time.sleep(0.6)

        got = _textarea_value()
        if not (got or "").strip():
            # attempt 2 — native value setter + events that cross the shadow boundary
            print(f"     ↻  Textarea still empty after insertText — retrying with the native setter")
            driver.execute_script("""
                var ta = arguments[0], msg = arguments[1];
                ta.focus();
                var d = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
                if (d && d.set) { d.set.call(ta, msg); } else { ta.value = msg; }
                ta.dispatchEvent(new Event('input',  {bubbles: true, composed: true}));
                ta.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
                ta.blur(); ta.focus();
            """, textarea, message_text)
            time.sleep(0.8)
            got = _textarea_value()

        if not (got or "").strip():
            print(f"     ⛔  Order {order_id}: the message box is STILL empty — NOT clicking Send "
                  f"(an empty message would reach the buyer). Retried next cycle.")
            return False

        _want = " ".join((message_text or "").split())
        _have = " ".join((got or "").split())
        if _have != _want:
            if _want[:60] not in _have:
                print(f"     ⛔  Order {order_id}: the box holds different text than intended "
                      f"({len(_have)} chars vs {len(_want)}) — NOT clicking Send")
                return False
            print(f"     ⚠️  Order {order_id}: box text differs slightly ({len(_have)} vs {len(_want)} chars) — proceeding")
        print(f"     ✍️  Message text verified in the box ({len(_have)} chars)")
    except Exception as e:
        err_str = str(e).lower()
        if "not interactable" in err_str or "not reachable" in err_str:
            print(f"     ⚠️  Order {order_id}: Textarea not interactable (No Permission): {e}")
        else:
            print(f"     ❌  Typing error for {order_id}: {e}")
        return False

    time.sleep(2)

    # --- STEP 3: Click Send ---
    js_click_send = """
    function clickSend() {
        const candidates = document.querySelectorAll('kat-button, button, input[type="submit"], a[role="button"]');
        for (let el of candidates) {
            const label = (el.getAttribute('label') || '').trim().toLowerCase();
            const text = (el.textContent || el.innerText || '').trim().toLowerCase();
            const val = (el.getAttribute('value') || '').trim().toLowerCase();
            const ariaLabel = (el.getAttribute('aria-label') || '').trim().toLowerCase();

            if (label === 'send' || label.startsWith('send ') ||
                text === 'send' || text.startsWith('send ') ||
                val === 'send' || val.startsWith('send ') ||
                ariaLabel === 'send' || ariaLabel.startsWith('send ')) {

                if (el.shadowRoot) {
                    const inner = el.shadowRoot.querySelector('button');
                    if (inner && !inner.disabled) {
                        inner.click();
                        return true;
                    }
                }
                if (!el.disabled) {
                    el.click();
                    return true;
                }
            }
        }

        // Fallback: Any primary kat-button on the page or in the footer
        const primaryKat = document.querySelector('kat-button[variant="primary"]') ||
                            document.querySelector('.ayb-contact-buyer-footer kat-button');
        if (primaryKat) {
            if (primaryKat.shadowRoot) {
                const inner = primaryKat.shadowRoot.querySelector('button');
                if (inner && !inner.disabled) {
                    inner.click();
                    return true;
                }
            }
            if (!primaryKat.disabled) {
                primaryKat.click();
                return true;
            }
        }

        // Fallback: Standard submit button
        const submitBtn = document.querySelector('button[type="submit"], button.a-button-primary');
        if (submitBtn && !submitBtn.disabled) {
            submitBtn.click();
            return true;
        }

        return false;
    }
    return clickSend();
    """
    try:
        wait.until(lambda d: d.execute_script(js_click_send))
        time.sleep(random.uniform(2.5, 4.0))
    except Exception as e:
        print(f"     ❌  Send button error for {order_id}: {e}")
        return False
    return True


def send_delay_message(driver, wait, order_id, store_name):
    """Sends a stock delay message for SR/OOS/Discontinued orders."""
    CONTACT_URL = f"https://sellercentral.amazon.com/messaging/contact?orderID={order_id}&marketplaceID=ATVPDKIKX0DER"
    print(f"     → Navigating to messaging page...")
    driver.get(CONTACT_URL)
    # Same wrong-page hazard as the 2026-08-19 "-D" label incident: never type
    # into a messaging thread without proof the page belongs to THIS order —
    # a silent redirect would message a different customer.
    from platforms.amazon import _confirm_on_order_page
    if not _confirm_on_order_page(driver, order_id, label="[Msg] ", renav_url=CONTACT_URL):
        return False

    try:
        customer_name = "Valued Customer"
        try:
            el = WebDriverWait(driver, 10).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, "div.ayb-contact-buyer-header h2"))
            )
            name = el.text.split()[-1].capitalize()
            if name: customer_name = name
        except TimeoutException:
            pass

        message_text = f"""Hello {customer_name},

Thank you for your order with {store_name}.

We hope this message finds you well.

We are writing to inform you that while processing your order, we discovered that the item you purchased is currently temporarily out of stock. Unfortunately, this is due to unexpected stock damage at our warehouse, which affected the available inventory.

Please rest assured that we have already placed a priority re-order with our supplier to replenish the stock. As soon as the new shipment arrives at our warehouse, we will dispatch your order immediately. We kindly ask you to allow approximately 10–15 business days for your order to be shipped.

We completely understand that delays can be frustrating, and we sincerely apologize for any inconvenience this may cause. Your satisfaction is very important to us.

If you urgently need the item and are unable to wait for the restock, please do not hesitate to let us know. We will be more than happy to arrange a full refund immediately upon your request.

Thank you very much for your patience, understanding, and continued support. If you have any questions or concerns, please feel free to reach out to us at any time.

Kind regards,
Customer Support Team
{store_name}"""

        # SR/OOS delay: prefer "Notify of a problem" → fallback "Other"
        success = _click_radio_and_type(driver, wait, order_id, message_text,
                                        radio_priority=['unexpectedProblem', 'contactBuyer'])
        if success:
            print(f"     ✅  Stock delay message sent for: {order_id}")
        return success
    except Exception as e:
        print(f"     ❌  Failed to send delay message for {order_id}: {e}")
        return False

def send_cancellation_denial_message(driver, wait, order_id, store_name, tracking_id_string=""):
    """Sends a cancellation denial message — item already shipped, free return offered.
    Optionally includes the tracking ID so the customer knows what to expect."""
    CONTACT_URL = f"https://sellercentral.amazon.com/messaging/contact?orderID={order_id}&marketplaceID=ATVPDKIKX0DER"
    print(f"     → Navigating to messaging page...")
    driver.get(CONTACT_URL)
    # Same wrong-page hazard as the 2026-08-19 "-D" label incident: never type
    # into a messaging thread without proof the page belongs to THIS order —
    # a silent redirect would message a different customer.
    from platforms.amazon import _confirm_on_order_page
    if not _confirm_on_order_page(driver, order_id, label="[Msg] ", renav_url=CONTACT_URL):
        return False

    try:
        # Build tracking section if tracking IDs are available
        tracking_section = ""
        if tracking_id_string:
            tracking_list = [t.strip() for t in tracking_id_string.split(',')]
            if len(tracking_list) == 1:
                tracking_section = f"\nYour package has been shipped with tracking ID: {tracking_list[0]}\nYou can track your shipment on the FedEx website using this number.\n"
            else:
                formatted = "".join(f"{i}) {tid}\n" for i, tid in enumerate(tracking_list, 1))
                tracking_section = f"\nYour package has been shipped with the following tracking ID(s):\n{formatted}You can track your shipment on the FedEx website using these numbers.\n"

        message_text = f"""Hello,

Thank you for contacting us. We received your cancellation request for order {order_id}.

We are sorry to inform you that we were unable to cancel the order because our turnaround time is very quick, and your package has already been processed and dispatched to the carrier.
{tracking_section}
However, please rest assured that we provide prepaid return labels. Once you receive the item, if you still do not wish to keep it, simply contact us and you can return it for free for a full refund.

If you prefer, you may also refuse the delivery when the carrier attempts to deliver the package, and it will automatically be returned to us.

We apologize for any inconvenience and thank you for your understanding.

Kind regards,
Customer Support Team
{store_name}"""

        # Cancellation: prefer "Notify of a problem" → fallback "Other"
        success = _click_radio_and_type(driver, wait, order_id, message_text,
                                        radio_priority=['unexpectedProblem', 'contactBuyer'])
        if success:
            print(f"     ✅  Cancellation denial message sent for: {order_id}")
        return success
    except Exception as e:
        print(f"     ❌  Failed to send cancellation message for {order_id}: {e}")
        return False
def check_for_cancellation_request(service, amazon_order_id):
    """
    Checks if there is an email with the specific 'Order cancelation request' subject.
    """
    if not service:
        return False

    # Search for the specific subject line including the Amazon Order ID
    query = f'subject:"Order cancelation request for Order ID: {amazon_order_id}"'
    
    try:
        response = service.users().messages().list(userId='me', q=query, maxResults=1).execute()
        messages = response.get('messages', [])
        
        if messages:
            print(f"⚠️ CANCELLATION REQUEST FOUND for Amazon Order ID: {amazon_order_id}")
            return True
        return False
    except HttpError as error:
        print(f"- ⚠️ Gmail API Error while checking cancellation: {error}")
        return False

def _prefetch_batch_tracking(batch_wbs, gmail_services):
    """Resolve a whole batch of WBs at once instead of one live Gmail search
    per WB per account:
      tier 1 — archive_tracking in the unified DB (instant; the Gmail-archive
               sync already extracted these tracking IDs);
      tier 2 — ONE Gmail query per account OR-ing the remaining WBs, bodies
               fetched once, tracking extracted with the same regexes.
    Returns (found, matched_no_ids, live_complete):
      found          {wb: (tracking_list, shipped_time)}
      matched_no_ids WBs that matched a message but yielded no body tracking
                     (caller runs the classic per-WB search → PDF extraction)
      live_complete  True when the batched sweep ran cleanly, so a WB absent
                     everywhere is genuinely not-found (skip live search)."""
    import os as _os, sqlite3 as _sq
    found, matched_no_ids = {}, set()
    remaining = [w for w in dict.fromkeys(batch_wbs)]

    # tier 1: archive DB. MERGE across rows — Zoro sends one "has shipped" email
    # PER BOX, so a split shipment is several archive rows; the old per-row
    # assignment kept only the last row and uploaded ONE tracking id of a
    # multi-box order (401 WBs affected at the 2026-08-18 audit).
    _db = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                        "data", "automations_unified.db")
    try:
        conn = _sq.connect(f"file:{_db}?mode=ro", uri=True, timeout=3)
        ph = ",".join("?" * len(remaining))
        rows = conn.execute(
            f"SELECT wb_order_id, tracking_ids, ship_date FROM archive_tracking "
            f"WHERE wb_order_id IN ({ph}) AND tracking_ids != '' "
            f"ORDER BY email_timestamp", remaining).fetchall()
        conn.close()
        for wb, tids, ship_date in rows:
            ids = [t.strip() for t in str(tids).split(",") if t.strip()]
            if not ids:
                continue
            prev = found.get(wb)
            if prev:
                merged = prev[0] + [i for i in ids if i not in prev[0]]
                found[wb] = (merged, prev[1] or (ship_date or None))   # keep FIRST box's ship time
            else:
                found[wb] = (ids, (ship_date or None))
        remaining = [w for w in remaining if w not in found]
        if found:
            n_ids = sum(len(v[0]) for v in found.values())
            print(f"     💾  {len(found)} WB(s) resolved from the archive DB "
                  f"({n_ids} tracking id(s), all boxes — no Gmail needed)")
    except Exception:
        pass

    # tier 1b: closing_orders — tracking also lands here WITHOUT any archived email
    # (FedEx tracker, backfills, placer write-backs; 939 such WBs at last count).
    # A hit here applies straight away too — Gmail is never touched for this WB.
    if remaining:
        try:
            conn = _sq.connect(f"file:{_db}?mode=ro", uri=True, timeout=3)
            ph = ",".join("?" * len(remaining))
            rows = conn.execute(
                f"SELECT wb_order_id, tracking_ids, shipped_email_time FROM closing_orders "
                f"WHERE wb_order_id IN ({ph}) AND COALESCE(tracking_ids,'') != ''", remaining).fetchall()
            conn.close()
            n_before = len(found)
            # closing_orders is multi-source (FedEx tracker, backfills) and holds some
            # junk ids (9-digit numbers, 'U…' refs — 54 rows at last audit). Only shapes
            # a real carrier uses may be uploaded: UPS 1Z + 16, or 12-22 digits.
            _valid = re.compile(r"^(1Z[0-9A-Z]{16}|\d{12,22})$")
            for wb, tids, ship_t in rows:
                ids = [t.strip() for t in str(tids).split(",")
                       if _valid.fullmatch(t.strip())]
                if ids and wb not in found:
                    found[wb] = (ids, (ship_t or None))
            remaining = [w for w in remaining if w not in found]
            if len(found) > n_before:
                print(f"     💾  {len(found) - n_before} more WB(s) resolved from closing_orders (no Gmail needed)")
        except Exception:
            pass

    # tier 2: one batched Gmail sweep per account for the leftovers
    live_complete = True
    if remaining and not gmail_services:
        # DB-only mode: no sweep ran, so absence from the DB proves nothing —
        # these WBs must stay eligible for retry, not be marked "not found".
        live_complete = False
    if remaining:
        TRACKING_ID_REGEX = r"\b\d{12}\b"
        or_block = " OR ".join(f'"{w}"' for w in remaining)
        for account_name, service in gmail_services.items():
            if not service:
                continue
            todo = [w for w in remaining if w not in found]
            if not todo:
                break
            try:
                resp = service.users().messages().list(
                    userId='me', q=f'({or_block})', maxResults=30).execute()
                messages = resp.get('messages', [])
                if resp.get('nextPageToken'):
                    live_complete = False
            except Exception as e:
                print(f"     ⚠️  Batched tracking sweep failed in [{account_name}]: {e}")
                live_complete = False
                continue
            acc = {}   # wb -> (set_of_ids, shipped_time) — UNION across messages
            for mi in messages:
                try:
                    msg = service.users().messages().get(userId='me', id=mi['id']).execute()
                    body_html = get_email_body(msg) or ""
                    clean_text = clean_html(body_html) if body_html else ""
                    hay = clean_text + " " + body_html
                    for wb in todo:
                        if wb not in hay:
                            continue
                        ids = set()
                        block = re.search(r"Tracking Number(?:s)?:?\s*(.*?)Items in this Shipment",
                                          clean_text, re.IGNORECASE | re.DOTALL)
                        if block:
                            ids.update(re.findall(TRACKING_ID_REGEX, block.group(1)))
                        ids.update(re.findall(r'esp_trackingnumber[=:]\s*(\d{12})', body_html, re.IGNORECASE))
                        for tid in re.findall(r'(?:FedEx|UPS|USPS)[^\d]*?(\d{12,22})', clean_text, re.IGNORECASE):
                            if len(tid) == 12:
                                ids.add(tid)
                        # Fix 4: Also check PDF attachments in the batched sweep
                        if not ids:
                            for pdf_part in _walk_pdf_parts(msg.get('payload', {})):
                                att_id = pdf_part['body'].get('attachmentId')
                                if not att_id:
                                    continue
                                try:
                                    attachment = service.users().messages().attachments().get(
                                        userId='me', messageId=mi['id'], id=att_id).execute()
                                    file_data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))
                                    with pdfplumber.open(io.BytesIO(file_data)) as pdf:
                                        pdf_text = ""
                                        for page in pdf.pages:
                                            pdf_text += page.extract_text() or ""
                                    if _pdf_has_tracking_context(pdf_text):
                                        pdf_ids = re.findall(TRACKING_ID_REGEX, pdf_text)
                                        ids.update(pdf_ids)
                                except Exception:
                                    pass
                        if ids:
                            # Split shipments send one email per package — union
                            # ALL messages' ids, never stop at the first match.
                            prev_ids, prev_time = acc.get(wb, (set(), None))
                            ts_ms = int(msg.get("internalDate", 0))
                            st = (datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
                                  if ts_ms else None)
                            acc[wb] = (prev_ids | ids, prev_time or st)
                        else:
                            matched_no_ids.add(wb)   # message matched — PDF path may apply
                except Exception:
                    live_complete = False
            for wb, (ids, st) in acc.items():
                found[wb] = (sorted(ids), st)
                matched_no_ids.discard(wb)
                print(f"     ✅  {wb}: {len(ids)} tracking ID(s) via batched sweep [{account_name}]")
    return found, matched_no_ids, live_complete


def _walk_pdf_parts(payload):
    """Recursively walk MIME parts and yield all PDF attachment parts.
    Handles nested multipart structures (e.g. multipart/mixed → multipart/alternative + PDF)."""
    if payload.get('filename', '').lower().endswith('.pdf') and payload.get('body', {}).get('attachmentId'):
        yield payload
    for sub_part in payload.get('parts', []) or []:
        yield from _walk_pdf_parts(sub_part)


def _pdf_has_tracking_context(pdf_text):
    """Check if a PDF contains tracking-related context (not just any 12-digit number).
    Accepts 'Tracking Number' header OR carrier keywords near digits."""
    if re.search(r"Tracking Number", pdf_text, re.IGNORECASE):
        return True
    # Carrier keywords suggest the PDF is a shipment/receipt doc with tracking data
    if re.search(r"\b(?:FedEx|UPS|USPS)\b", pdf_text, re.IGNORECASE):
        return True
    # Zoro receipt PDFs: contain 'Ship Via' or 'Shipped' near a 12-digit number
    if re.search(r"(?:Ship Via|Shipped|Shipment)", pdf_text, re.IGNORECASE):
        return True
    return False


def find_all_tracking_in_emails(service, order_number):
    """
    Searches Gmail for a specific order number and extracts ALL tracking IDs.
    Returns: (list_of_tracking_ids, shipped_email_time_str or None)
    """
    if not service:
        return [], None

    query = f'"{order_number}"'
    all_tracking_ids = set() # Use a set to avoid duplicates
    shipped_email_time = None
    
    # Regex looks for EXACTLY 12 digits.
    TRACKING_ID_REGEX = r"\b\d{12}\b"

    try:
        response = service.users().messages().list(userId='me', q=query, maxResults=5).execute()
        messages = response.get('messages', [])
        if not messages:
            return [], None
        
        for message_info in messages:
            msg = service.users().messages().get(userId='me', id=message_info['id']).execute()
            
            # Capture shipped email time from the first message that contains tracking
            if shipped_email_time is None:
                ts_ms = int(msg.get("internalDate", 0))
                if ts_ms:
                    shipped_email_time = datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
            
            # 1. Check Email Body
            body_html = get_email_body(msg)
            if body_html:
                clean_text = clean_html(body_html)
                
                # Check for Zoro-format "Tracking Number...Items in this Shipment" block
                block_match = re.search(r"Tracking Number(?:s)?:?\s*(.*?)Items in this Shipment", clean_text, re.IGNORECASE | re.DOTALL)
                
                if block_match:
                    tracking_block = block_match.group(1)
                    matches = re.findall(TRACKING_ID_REGEX, tracking_block)
                    if matches:
                        for tid in matches:
                            all_tracking_ids.add(tid)

                # Grainger-format: tracking IDs embedded in esp_trackingnumber URL params
                grainger_url_matches = re.findall(r'esp_trackingnumber[=:]\s*(\d{12})', body_html, re.IGNORECASE)
                for tid in grainger_url_matches:
                    all_tracking_ids.add(tid)

                # Grainger-format: tracking ID as plain text near carrier name (FedEx / UPS)
                carrier_block = re.findall(r'(?:FedEx|UPS|USPS)[^\d]*?(\d{12,22})', clean_text, re.IGNORECASE)
                for tid in carrier_block:
                    if len(tid) == 12:
                        all_tracking_ids.add(tid)

            # 2. Check PDF Attachments (recursive walk handles nested MIME parts)
            for pdf_part in _walk_pdf_parts(msg.get('payload', {})):
                att_id = pdf_part['body'].get('attachmentId')
                if att_id:
                    try:
                        attachment = service.users().messages().attachments().get(userId='me', messageId=msg['id'], id=att_id).execute()
                        file_data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))
                        
                        with pdfplumber.open(io.BytesIO(file_data)) as pdf:
                            full_pdf_text = ""
                            for page in pdf.pages:
                                full_pdf_text += page.extract_text() or ""
                            
                            if _pdf_has_tracking_context(full_pdf_text):
                                matches = re.findall(TRACKING_ID_REGEX, full_pdf_text)
                                if matches:
                                    for tid in matches:
                                        all_tracking_ids.add(tid)
                    except Exception as pdf_error:
                        print(f"---- ⚠️ Could not parse PDF. Error: {pdf_error}")

    except HttpError as error:
        print(f"- ⚠️ Gmail API Error for {order_number}: {error}")
    
    return list(all_tracking_ids), shipped_email_time


def _update_unified_db_tracking(wb_id, tracking_ids, shipped_email_time):
    """Update unified DB with tracking IDs. Best-effort — never crashes flow."""
    if not _HAS_UNIFIED_DB:
        return
    try:
        from utils.unified_db import update_order, get_order_by_wb
        row = get_order_by_wb(wb_id)
        if row:
            amazon_id = row["amazon_order_id"]
            updates = {
                "tracking_ids": ", ".join(tracking_ids) if tracking_ids else "",
                "shipped_email_time": shipped_email_time or "",
            }
            update_order(amazon_id, updates, changed_by="tracking_uploader")
            print(f"     📝 Updated unified DB with tracking for {wb_id}")
    except Exception:
        pass


def _update_order_log_tracking(wb_id, tracking_ids, shipped_email_time):
    """Update the order_log.txt entry for wb_id with tracking IDs and shipped email time.
    Modifies the existing line in-place."""
    try:
        log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "rajar_logs")
        log_path = os.path.join(log_dir, "order_log.txt")
        if not os.path.exists(log_path):
            return
        
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        updated = False
        new_lines = []
        for line in lines:
            if f"| WB:{wb_id} " in line or f"| WB:{wb_id}|" in line:
                # Update existing fields or append new ones
                tids_str = ", ".join(tracking_ids) if tracking_ids else ""
                # Remove old TrackingIDs and ShippedEmailTime if present
                line = re.sub(r"\s*\|\s*TrackingIDs:[^|]*", "", line)
                line = re.sub(r"\s*\|\s*ShippedEmailTime:[^|]*", "", line)
                # Append new fields before the final newline
                line = line.rstrip("\n")
                if tids_str:
                    line += f" | TrackingIDs:{tids_str}"
                if shipped_email_time:
                    line += f" | ShippedEmailTime:{shipped_email_time}"
                line += "\n"
                updated = True
            new_lines.append(line)
        
        if updated:
            with open(log_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            print(f"     📝 Updated order_log.txt with tracking for {wb_id}")
            # Also update unified DB
            _update_unified_db_tracking(wb_id, tracking_ids, shipped_email_time)
            # Trigger background sheet sync
            try:
                from utils.sheet_sync import sync_order_log_async
                sync_order_log_async()
            except Exception:
                pass
    except Exception as e:
        print(f"     ⚠️ Failed to update order_log.txt: {e}")


# --- Send Message Function ---

def send_message_to_customer(driver, wait, order_id, tracking_id_string, store_name):
    """Sends a shipping confirmation message with tracking IDs to the customer."""
    CONTACT_URL = f"https://sellercentral.amazon.com/messaging/contact?orderID={order_id}&marketplaceID=ATVPDKIKX0DER"
    print(f"     → Navigating to messaging page...")
    driver.get(CONTACT_URL)
    # Same wrong-page hazard as the 2026-08-19 "-D" label incident: never type
    # into a messaging thread without proof the page belongs to THIS order —
    # a silent redirect would message a different customer.
    from platforms.amazon import _confirm_on_order_page
    if not _confirm_on_order_page(driver, order_id, label="[Msg] ", renav_url=CONTACT_URL):
        return False

    try:
        customer_name = "Valued Customer"
        try:
            el = WebDriverWait(driver, 10).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, "div.ayb-contact-buyer-header h2"))
            )
            name = el.text.split()[-1].capitalize()
            if name: customer_name = name
        except TimeoutException:
            pass

        tracking_list = [t.strip() for t in tracking_id_string.split(',')]
        count = len(tracking_list)
        formatted_list = "".join(f"{i}) {tid}\n" for i, tid in enumerate(tracking_list, 1))

        message_template = f"""Hi {customer_name},

Great news — your order has been shipped and is on its way to you!

We know you're looking forward to receiving it, so we processed and handed it off to FedEx as quickly as possible. Here {'is' if count == 1 else 'are'} your tracking number{'s' if count > 1 else ''} so you can follow along:

{formatted_list}

You can check the latest status anytime on the FedEx website.

A quick heads up — while we always make sure your package leaves our warehouse in perfect condition and on time, once it's in FedEx's hands, rare things like transit delays or rough handling can occasionally happen that are outside of our control. If your package arrives late or damaged in any way, please reach out to us directly here — we'll take care of it right away with a replacement, refund, or whatever works best for you.

Best,
The {store_name} Team"""
        # NOTE: no review/feedback solicitation here, deliberately. Amazon's
        # Communication Guidelines forbid asking buyers for a review in a way
        # that suggests a positive one ("if everything looks good…") or appeals
        # to sympathy. Use Amazon's own "Request a Review" button instead — it
        # sends their neutral, policy-compliant template. Do not re-add it.

        # Tracking confirmation: prefer "Notify of a problem" → fallback "Other"
        success = _click_radio_and_type(driver, wait, order_id, message_template,
                                        radio_priority=['unexpectedProblem', 'contactBuyer'])
        if success:
            print(f"     ✅  Tracking confirmation message sent for: {order_id}")
        return success

    except Exception as e:
        print(f"     ❌  FAILED to send message for order {order_id}. Reason: {e}")
        return False

# --- Helper Functions (Report Download & Filtering) ---

def download_amazon_order_report(driver, wait, account_config=None, download_dir=None):
    dl_dir = download_dir or DOWNLOAD_DIRECTORY
    print("\n📦 DOWNLOADING AMAZON ORDER REPORT 📦")
    try:
        driver.get(REPORT_DOWNLOAD_URL)
        time.sleep(2)
        
        if is_login_page(driver):
            if account_config and auto_login(driver, account_config):
                driver.get(REPORT_DOWNLOAD_URL)
                time.sleep(2)
            elif is_login_page(driver):
                print("\nACTION REQUIRED: Auto-login failed. Please log in manually.")
                print("   ⏳ Waiting 30 seconds for manual login...")
                time.sleep(30)
                if is_login_page(driver):
                    print("   ⏳ Still on login page — waiting 30 more seconds...")
                    time.sleep(30)
                driver.get(REPORT_DOWNLOAD_URL)
        
        dropdown_host = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, DROPDOWN_HOST_SELECTOR)))
        dropdown_host.click()
        time.sleep(1)
        driver.execute_script("arguments[0].shadowRoot.querySelector('kat-option[value=\"30\"]').click()", dropdown_host)
        time.sleep(1)

        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, REQUEST_REPORT_BUTTON_SELECTOR))).click()
        
        max_wait_time = 600; check_interval = 30; time_waited = 0
        while time_waited < max_wait_time:
            try:
                driver.refresh()
                time.sleep(5)
                download_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, LATEST_DOWNLOAD_BUTTON_SELECTOR)))
                download_button.click()
                time.sleep(15)

                files_in_dir = [os.path.join(dl_dir, f) for f in os.listdir(dl_dir) if f.endswith('.txt')]
                if not files_in_dir: raise FileNotFoundError("No .txt report file found.")
                
                return max(files_in_dir, key=os.path.getctime)
            except TimeoutException:
                time_waited += check_interval
                time.sleep(check_interval - 5)

        raise TimeoutException("Report did not generate within the 10-minute time limit.")
    except Exception as e:
        print(f"\n❌ CRITICAL ERROR during report download: {type(e).__name__}: {e}")
        try:
            print(f"   Current URL at failure: {driver.current_url}")
        except Exception:
            pass
        return None

def clean_and_filter_amazon_report(df):
    print("\n  🧹  Cleaning & Filtering Report...")
    if 'item-status' not in df.columns or 'merchant-order-id' not in df.columns:
        print("  ❌  FATAL: Report missing required columns ('item-status', 'merchant-order-id').")
        return None, None
    unshipped = df[df['item-status'] == 'Unshipped'].copy()
    unshipped.dropna(subset=['merchant-order-id'], inplace=True)
    merchant_ids = unshipped['merchant-order-id'].astype(str)
    wb_orders = unshipped[merchant_ids.str.startswith('WB') | merchant_ids.str.startswith('WW')].copy()

    # Exclude duplicate-marked orders (e.g. "WB123 - D", "WB123-D", "WB123 DUPLICATE")
    dup_mask = (
        merchant_ids.str.contains(r'-\s*D\b', case=False, na=False, regex=True) |
        merchant_ids.str.contains('DUPLICATE', case=False, na=False)
    )
    wb_orders = wb_orders[~dup_mask].copy()

    # Delay group: SR (State Restricted), OOS, Loss, Discontinued
    # Matched anywhere in the merchant ID, case-insensitive.
    # NOTE: "Loss-Kamran" is a temporary hold label used by the order
    # placement script — NOT a true loss/OOS. Exclude it from delay group.
    delay_mask = (
        merchant_ids.str.contains('SR',           case=False, na=False) |
        merchant_ids.str.contains('OOS',          case=False, na=False) |
        merchant_ids.str.contains(r'\bNS\b', case=False, na=False, regex=True) |
        merchant_ids.str.contains('Discontinued', case=False, na=False) |
        merchant_ids.str.contains('Unfulfillable', case=False, na=False) |
        (
            merchant_ids.str.contains('Loss', case=False, na=False) &
            ~merchant_ids.str.contains('Loss-Kamran', case=False, na=False)
        )
    )
    sr_orders = unshipped[delay_mask].copy()

    # Sort orders so that the oldest orders are processed first
    if 'purchase-date' in df.columns:
        wb_orders.sort_values(by='purchase-date', ascending=True, inplace=True)
        sr_orders.sort_values(by='purchase-date', ascending=True, inplace=True)
    else:
        # Fallback: Amazon reports are newest-first, so reversing gives oldest-first
        wb_orders = wb_orders.iloc[::-1].copy()
        sr_orders = sr_orders.iloc[::-1].copy()

    print(f"  📋  WB/WW (normal) orders               : {len(wb_orders)}")
    print(f"  🔴  SR/OOS/Loss/Discontinued/NS orders  : {len(sr_orders)}")
    return wb_orders[['amazon-order-id', 'merchant-order-id']].copy(), sr_orders[['amazon-order-id', 'merchant-order-id']].copy()
def load_exclusion_list(file_path):
    """Loads Amazon Order IDs from the exclusion Excel file."""
    print(f"\n  📂  Loading exclusion list...")
    exclusion_set = set()
    try:
        df = pd.read_excel(file_path)
        if 'Amazon Order Num' in df.columns:
            target_col = df['Amazon Order Num']
        else:
            target_col = df.iloc[:, 0]
        exclusion_set = set(target_col.astype(str).str.strip().tolist())
        print(f"  🚫  Exclusion list loaded — {len(exclusion_set)} orders will be skipped.")
    except FileNotFoundError:
        print(f"  ⚠️  Exclusion file not found. Continuing without exclusions.")
    except Exception as e:
        print(f"  ⚠️  Error loading exclusion file: {e}")
    return exclusion_set


def load_manual_tracking_blockers(file_path):
    """Loads blocker IDs from a plain text file (one ID per line)."""
    blocker_set = set()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                blocker_set.add(line)
        print(f"  🧱  Manual blocker list loaded — {len(blocker_set)} ID(s).")
    except FileNotFoundError:
        print("  ℹ️  Manual blocker file not found. Continuing without manual blockers.")
    except Exception as e:
        print(f"  ⚠️  Error loading manual blocker list: {e}")
    return blocker_set


# --- Main Workflow ---
def main_workflow(account_config, passed_driver=None):
    driver = passed_driver
    amazon_report_path = None
    # Per-account download directory to avoid collisions when running side-by-side
    acct_download_dir = os.path.join(script_directory, f"_dl_{account_config['key']}")
    os.makedirs(acct_download_dir, exist_ok=True)
    try:
        if not driver:
            print(f"\n  🌐  Launching Chrome for '{account_config['name']}'...")
            profile_path = resolve_user_data_dir(account_config)
            print(f"  👤  Using Chrome profile dir: {profile_path}")
            driver = launch_or_attach_chrome(account_config, profile_path, download_dir=acct_download_dir)
            if not driver:
                options = Options()
                options.add_argument("--window-size=1920,1080")
                options.add_argument("--start-maximized")
                # Memory softening — keep in sync with utils.helpers.CHROME_SOFT_FLAGS
                for _sf in ("--renderer-process-limit=4", "--disable-background-networking",
                            "--disable-sync", "--disable-default-apps",
                            "--disable-component-update", "--metrics-recording-only",
                            "--mute-audio", "--disk-cache-size=104857600"):
                    options.add_argument(_sf)
                from utils.helpers import mark_profile_clean_exit
                mark_profile_clean_exit(profile_path)
                options.add_argument(f"user-data-dir={profile_path}")
                options.add_argument("--hide-crash-restore-bubble")
                options.add_argument("--start-maximized")
                options.add_experimental_option("excludeSwitches", ["enable-automation"])
                options.add_experimental_option('useAutomationExtension', False)
                options.add_argument("--no-first-run")
                options.add_argument("--no-default-browser-check")
                prefs = {"download.default_directory": acct_download_dir}
                options.add_experimental_option("prefs", prefs)

                driver = create_chrome_driver(options)
        else:
            print(f"\n  🌐  Reusing existing Chrome for '{account_config['name']}'...")
            
        wait = WebDriverWait(driver, 20)

        amazon_report_path = download_amazon_order_report(driver, wait, account_config, download_dir=acct_download_dir)
        if not amazon_report_path:
            raise SystemExit(f"Could not download the order report for {account_config['name']}.")

        amazon_df = pd.read_csv(amazon_report_path, sep='\t', on_bad_lines='warn')

        excluded_ids = load_exclusion_list(EXCLUSION_FILE_PATH)
        
        if excluded_ids and 'amazon-order-id' in amazon_df.columns:
            initial_count = len(amazon_df)
            amazon_df = amazon_df[~amazon_df['amazon-order-id'].astype(str).str.strip().isin(excluded_ids)]
            removed_count = initial_count - len(amazon_df)
            print(f"  📉  Excluded {removed_count} orders → {len(amazon_df)} remaining.")

        filtered_orders_df, sr_orders_df = clean_and_filter_amazon_report(amazon_df)

        manual_blockers = load_manual_tracking_blockers(MANUAL_TRACKING_BLOCKER_FILE)
        if manual_blockers and filtered_orders_df is not None and not filtered_orders_df.empty:
            initial_wb_count = len(filtered_orders_df)
            wb_block_mask = (
                filtered_orders_df['merchant-order-id'].astype(str).str.strip().isin(manual_blockers)
                |
                filtered_orders_df['amazon-order-id'].astype(str).str.strip().isin(manual_blockers)
            )
            filtered_orders_df = filtered_orders_df[~wb_block_mask].copy()
            blocked_count = initial_wb_count - len(filtered_orders_df)
            if blocked_count > 0:
                print(f"  🛑  Manual blockers applied — skipped {blocked_count} WB order(s) from auto upload.")

        if filtered_orders_df is not None and not filtered_orders_df.empty:
            if len(filtered_orders_df) > 1000:
                print(f"  ⚠️  Order limit active: trimmed to first 1000 (was {len(filtered_orders_df)}).")
                filtered_orders_df = filtered_orders_df.head(1000)

        has_wb = filtered_orders_df is not None and not filtered_orders_df.empty
        has_sr = sr_orders_df is not None and not sr_orders_df.empty

        if not has_wb and not has_sr:
            print(f"\n  ⏩  No unshipped WB or SR orders found for '{account_config['name']}'. Nothing to do.")
        else:
            print("\n  📧  Connecting to Gmail accounts...")
            gmail_services = {}
            for account in ACCOUNTS:
                try:
                    service = get_gmail_service(account['credentials_file'], account['token_file'])
                    if service:
                        gmail_services[account['name']] = service
                except Exception as e:
                    print(f"  ⚠️  Could not connect to Gmail account '{account['name']}': {e}")

            if not gmail_services:
                # DB-ONLY degraded mode (was a fatal halt — e.g. the 2026-08-18 DNS
                # wedge killed every cycle even though the archive DB had the answers).
                # Tier-1 lookups against archive_tracking need no Gmail at all: the
                # launcher's sync keeps extracting tracking IDs from email into the
                # unified DB. WBs not in the DB are simply retried next cycle.
                # Messages stay OFF though — the cancellation-request check needs
                # Gmail, and messaging blind risks confirming a cancelled order.
                print("  ⚠️  No Gmail account reachable — continuing in DB-ONLY mode:")
                print("      • tracking IDs resolve from the unified archive DB (launcher-synced)")
                print("      • WBs missing from the DB are skipped and retried next cycle")
                print("      • customer messages are paused this cycle (cancellation check needs Gmail)")
            else:
                connected = ', '.join(gmail_services.keys())
                print(f"  ✅  Connected to {len(gmail_services)} Gmail account(s): {connected}")

            message_log = load_message_log()
            print(f"  📋  Message log loaded — {len(message_log)} order(s) already recorded.")
            store_name = account_config['name']
            should_send_messages = account_config.get('send_messages', True)
            # CLI override: --skip-messages wins
            if getattr(_cli_args, 'skip_messages', False):
                should_send_messages = False
            if not gmail_services:
                should_send_messages = False   # DB-only mode: no cancellation check possible

            # ─────────────────────────────────────────────────────────
            #  STEP 1 │ SR Orders (OOS / Discontinued)
            # ─────────────────────────────────────────────────────────
            if has_sr:
                print(f"\n  {'─'*54}")
                print(f"  🔴  SR/LOSS/NS ORDERS — {len(sr_orders_df)} to process (delay message, no upload)")
                print(f"  {'─'*54}")
                for idx, (_, row) in enumerate(sr_orders_df.iterrows(), 1):
                    merchant_id = str(row['merchant-order-id']).strip()
                    amazon_id = str(row['amazon-order-id']).strip()
                    print(f"\n  [{idx}/{len(sr_orders_df)}] 🔴 SR/Loss Order: {merchant_id}  |  Amazon: {amazon_id}")

                    if should_send_messages:
                        if amazon_id in message_log and message_log[amazon_id].get('sent'):
                            print(f"     ⏩  Already messaged ({message_log[amazon_id].get('message_type')}). Skipping.")
                        else:
                            # Check for cancellation request first
                            has_cancellation_request = False
                            for account_name, service in gmail_services.items():
                                if check_for_cancellation_request(service, amazon_id):
                                    has_cancellation_request = True
                                    break

                            if has_cancellation_request:
                                # SR/Loss orders: customer wants to cancel + we can't fulfill = let them cancel
                                # No message needed — just log it and move on
                                print(f"     ⚠️  Cancellation request detected for SR/Loss order — skipping message (order will be cancelled).")
                                message_log[amazon_id] = {
                                    "message_type": "cancellation_skipped",
                                    "sent": False,
                                    "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
                                }
                                save_message_log(message_log)
                            else:
                                sent = send_delay_message(driver, wait, amazon_id, store_name)
                                message_log[amazon_id] = {
                                    "message_type": "stock_delay",
                                    "sent": sent,
                                    "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
                                }
                                save_message_log(message_log)
                    else:
                        print(f"     ⏩  Messaging disabled for {store_name}.")
                    print(f"     ⏩  No tracking upload for SR/Loss orders.")

            # ─────────────────────────────────────────────────────────
            #  STEP 2 │ WB Orders (normal tracking flow)
            # ─────────────────────────────────────────────────────────
            if has_wb:
                BATCH_SIZE = 10
                total_orders = len(filtered_orders_df)
                print(f"\n  {'─'*54}")
                print(f"  📦  WB ORDERS — {total_orders} to process (batches of {BATCH_SIZE})")
                print(f"  {'─'*54}")
                pending_orders = []
                success_count, failure_count = 0, 0
                already_shipped_skips = 0
                global_idx = 0

                rows_list = list(filtered_orders_df.iterrows())

                _max_uploads = int(getattr(_cli_args, "max_uploads", 0) or 0)
                if _max_uploads:
                    print(f"  🔒  UPLOAD CAP: stopping after {_max_uploads} successful upload(s)")

                for batch_start in range(0, total_orders, BATCH_SIZE):
                    if _max_uploads and success_count >= _max_uploads:
                        print(f"\n  🔒  Upload cap reached ({success_count}/{_max_uploads}) — no further batches this run.")
                        break
                    batch_rows = rows_list[batch_start:batch_start + BATCH_SIZE]
                    batch_num = (batch_start // BATCH_SIZE) + 1
                    total_batches = (total_orders + BATCH_SIZE - 1) // BATCH_SIZE
                    print(f"\n  {'═'*54}")
                    print(f"  📦  BATCH {batch_num}/{total_batches}  |  Searching for tracking IDs...")
                    print(f"  {'═'*54}")

                    batch_with_tracking = []

                    # Resolve the whole batch up front: archive DB first, then one
                    # batched Gmail sweep — instead of live searches per WB per account.
                    _batch_wbs = []
                    for _, _brow in batch_rows:
                        _batch_wbs.extend(re.findall(r'WB\d+', str(_brow['merchant-order-id']).strip()))
                    try:
                        _pref_found, _pref_maybe, _pref_complete = _prefetch_batch_tracking(
                            _batch_wbs, gmail_services)
                    except Exception as _pe:
                        print(f"     ⚠️  Batch prefetch failed ({_pe}) — classic per-WB searches will run")
                        _pref_found, _pref_maybe, _pref_complete = {}, set(), False

                    for _, row in batch_rows:
                        global_idx += 1
                        merchant_id = str(row['merchant-order-id']).strip()
                        amazon_order_id = str(row['amazon-order-id']).strip()
                        print(f"\n  [{global_idx}/{total_orders}] 🔍 {merchant_id}  |  Amazon: {amazon_order_id}")

                        # Extract valid WB IDs from merchant_id.
                        # Handles split orders ("WB123 WB456") and ignores
                        # duplicate markers like "WB123 - D".
                        wb_ids = re.findall(r'WB\d+', merchant_id)
                        found_tracking_ids_list = []
                        found_shipped_time = None

                        for wb_id in wb_ids:
                            _pre = _pref_found.get(wb_id)
                            if _pre and _pre[0]:
                                found_tracking_ids_list.extend(_pre[0])
                                if found_shipped_time is None:
                                    found_shipped_time = _pre[1]
                                print(f"     ✅  Found {len(_pre[0])} tracking ID(s) for {wb_id} (batched): {_pre[0]}")
                                continue
                            if _pref_complete and wb_id not in _pref_maybe:
                                # Clean batched sweep saw no email for this WB — a live
                                # per-account search would find nothing either.
                                continue
                            for account_name, service in gmail_services.items():
                                tracking_ids, shipped_time = find_all_tracking_in_emails(service, wb_id)
                                if tracking_ids:
                                    found_tracking_ids_list.extend(tracking_ids)
                                    if found_shipped_time is None:
                                        found_shipped_time = shipped_time
                                    print(f"     ✅  Found {len(tracking_ids)} tracking ID(s) for {wb_id} in [{account_name}]: {tracking_ids}")
                                    break

                        # Deduplicate tracking IDs while preserving order
                        seen = set()
                        deduped_tracking = []
                        for tid in found_tracking_ids_list:
                            if tid not in seen:
                                seen.add(tid)
                                deduped_tracking.append(tid)
                        found_tracking_ids_list = deduped_tracking

                        if found_tracking_ids_list:
                            joined_tracking = ", ".join(found_tracking_ids_list)
                            batch_with_tracking.append({
                                'amazon-order-id': amazon_order_id,
                                'Tracking ID': joined_tracking,
                                'wb_ids': wb_ids,
                            })
                            # Update order_log.txt with tracking IDs and shipped email time
                            # Call for each WB ID so all related log lines get updated
                            for wb_id in wb_ids:
                                _update_order_log_tracking(wb_id, found_tracking_ids_list, found_shipped_time)
                        else:
                            if gmail_services or len(pending_orders) < 3:
                                print(f"     ❌  No tracking ID found in any Gmail account.")
                            elif len(pending_orders) == 3:
                                print(f"     ❌  … (Gmail unreachable this cycle — further 'no tracking' lines suppressed)")
                            pending_orders.append(merchant_id)
                            log_tracking_upload(
                                amazon_id=amazon_order_id,
                                wb_ids=wb_ids,
                                tracking_ids=[],
                                message_sent=False,
                                message_type=None,
                                status="no_tracking",
                                note="No tracking found in Gmail",
                            )

                    # Upload this batch immediately
                    if batch_with_tracking:
                        print(f"\n  {'─'*54}")
                        print(f"  🤖  BATCH {batch_num} — Uploading {len(batch_with_tracking)} tracking ID(s) now...")
                        print(f"  {'─'*54}")

                        for upload_idx, order_data in enumerate(batch_with_tracking, 1):
                            if _max_uploads and success_count >= _max_uploads:
                                print(f"     🔒  Upload cap reached ({success_count}/{_max_uploads}) — "
                                      f"skipping the remaining {len(batch_with_tracking) - upload_idx + 1} order(s).")
                                break
                            amazon_id = order_data["amazon-order-id"]
                            tracking_id_string = order_data["Tracking ID"]
                            order_wb_ids = order_data.get("wb_ids", [])
                            print(f"\n  [Batch {batch_num} — {upload_idx}/{len(batch_with_tracking)}] ▶️  Amazon ID: {amazon_id}")
                            print(f"     📦  Tracking: {tracking_id_string}")

                            # Track message outcome for the audit log
                            _msg_sent = False
                            _msg_type = None
                            _need_seller_note = False

                            if should_send_messages:
                                existing_msg = message_log.get(amazon_id, {})
                                if existing_msg.get('sent') or existing_msg.get('message_type') == 'cancellation_denial':
                                    print(f"     ⏩  Already messaged ({existing_msg.get('message_type')}). Skipping.")
                                else:
                                    has_cancellation_request = False
                                    for account_name, service in gmail_services.items():
                                        if check_for_cancellation_request(service, amazon_id):
                                            has_cancellation_request = True
                                            break

                                    if has_cancellation_request:
                                        print(f"     ⚠️  Cancellation request detected — sending denial message.")
                                        sent = send_cancellation_denial_message(driver, wait, amazon_id, store_name, tracking_id_string)
                                        msg_type = "cancellation_denial"
                                    else:
                                        print(f"     💬  Sending tracking confirmation message...")
                                        sent = send_message_to_customer(driver, wait, amazon_id, tracking_id_string, store_name)
                                        msg_type = "tracking_confirmation"

                                    message_log[amazon_id] = {
                                        "message_type": msg_type,
                                        "sent": sent,
                                        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
                                    }
                                    save_message_log(message_log)
                                    _msg_sent = sent
                                    _msg_type = msg_type

                                    # Update seller note when the buyer was told the
                                    # tracking — either the tracking-confirmation OR the
                                    # cancellation-denial (which also includes the tracking ID).
                                    # We defer note updating to the order page load below so both
                                    # note writing and tracking upload happen on the same page load.
                                    if sent and msg_type in ("tracking_confirmation", "cancellation_denial"):
                                        _need_seller_note = True
                            else:
                                print(f"     ⏩  Messaging disabled for {store_name}.")

                            try:
                                print(f"     🚀  Uploading tracking ID to Amazon...")
                                order_page_url = f"https://sellercentral.amazon.com/orders-v3/order/{amazon_id}"
                                driver.get(order_page_url)

                                # Update seller note on the same page load before confirming shipment
                                if _need_seller_note:
                                    try:
                                        print(f"     📝  Updating seller note for {amazon_id}...")
                                        write_seller_note(driver, amazon_id, skip_navigate=True)
                                    except Exception as e:
                                        print(f"     ⚠️  Could not update seller note: {e}")

                                # Quick check on the freshly loaded page first (saves the
                                # 20s confirm-button wait for stale rows); if the page was
                                # slow to render, the post-timeout re-check still catches it.
                                _already_shipped = _page_shows_already_shipped(driver)
                                if not _already_shipped:
                                    try:
                                        confirm_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, INITIAL_CONFIRM_SHIPMENT_BUTTON_SELECTOR)))
                                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", confirm_btn)
                                        time.sleep(0.2)
                                        try:
                                            confirm_btn.click()
                                        except Exception:
                                            driver.execute_script("arguments[0].click();", confirm_btn)
                                    except TimeoutException:
                                        _already_shipped = _page_shows_already_shipped(driver)
                                if _already_shipped:
                                    already_shipped_skips += 1
                                    print(f"     ⏩  Already shipped on Amazon — skipping upload.")
                                    log_tracking_upload(
                                        amazon_id=amazon_id,
                                        wb_ids=order_wb_ids,
                                        tracking_ids=tracking_id_string.split(", ") if tracking_id_string else [],
                                        message_sent=_msg_sent,
                                        message_type=_msg_type,
                                        status="already_shipped",
                                        note="confirmed shipment already on Amazon (stale report row)",
                                    )
                                    continue

                                tracking_field = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, TRACKING_ID_FIELD_SELECTOR)))

                                # Stable order: the same shipment must yield the same "primary" ID on every
                                # run — on 15 Sep 2026 nine orders were re-uploaded with a DIFFERENT piece's
                                # number because the Gmail sweep returned the pieces in another order.
                                _all_tids = sorted({t.strip() for t in tracking_id_string.split(',') if t.strip()},
                                                   key=lambda t: (len(t), t))
                                # Capture the live DOM the first time a genuinely
                                # multi-package shipment is seen — that is the only
                                # state where the per-package controls are rendered.
                                if len(_all_tids) > 1 and not _STRUCTURE_CAPTURED:
                                    capture_confirm_shipment_structure(driver, amazon_id)
                                    _STRUCTURE_CAPTURED.append(amazon_id)

                                # ── Multi-package: one tracking ID per package ──
                                # Only attempted when Zoro shipped in several packages.
                                # Returns False (form untouched or abandoned before
                                # Confirm) if anything doesn't line up, so the proven
                                # single-tracking path below still runs.
                                _multi_ok = False
                                if len(_all_tids) > 1:
                                    _multi_ok = fill_multi_package_tracking(
                                        driver, _all_tids,
                                        remaining_qty_on_form(driver))

                                if not _multi_ok:
                                    tracking_field = driver.find_element(
                                        By.CSS_SELECTOR, TRACKING_ID_FIELD_SELECTOR)
                                    tracking_field.click()
                                    tracking_field.clear()
                                    primary_tracking_id = _all_tids[0] if _all_tids else ""
                                    tracking_field.send_keys(primary_tracking_id)
                                    time.sleep(2)

                                    # Extra tracking IDs could not be placed — say so loudly
                                    # rather than dropping them silently.
                                    if len(_all_tids) > 1:
                                        _unsent = _all_tids[1:]
                                        print(f"     ⚠️  MULTI-PACKAGE: Zoro shipped {len(_all_tids)} tracking IDs "
                                              f"but Amazon only got {primary_tracking_id}.")
                                        print(f"         Not uploaded: {', '.join(_unsent)} "
                                              f"— add these on Seller Central by hand.")
                                        _multi_package_orders.append({
                                            "amazon_id": amazon_id,
                                            "uploaded": primary_tracking_id,
                                            "not_uploaded": _unsent,
                                        })
                                else:
                                    time.sleep(1)

                                wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, FINAL_CONFIRM_SHIPMENT_BUTTON_SELECTOR))).click()
                                time.sleep(2)
                                
                                # Detect cancellation warning modal and confirm again if it appears
                                try:
                                    driver.execute_script("""
                                        var btns = document.querySelectorAll('kat-button, button, span');
                                        for (var i=0; i<btns.length; i++) {
                                            var text = btns[i].textContent || btns[i].innerText;
                                            if (text && text.toLowerCase().includes('confirm shipment')) {
                                                var rect = btns[i].getBoundingClientRect();
                                                if (rect.width > 0 && rect.height > 0) {
                                                    if (btns[i].shadowRoot) {
                                                        var inner = btns[i].shadowRoot.querySelector('button');
                                                        if (inner) inner.click();
                                                        else btns[i].click();
                                                    } else {
                                                        btns[i].click();
                                                    }
                                                }
                                            }
                                        }
                                    """)
                                    time.sleep(1)
                                except Exception:
                                    pass

                                success_count += 1
                                print(f"     ✅  Tracking uploaded successfully.")
                                log_tracking_upload(
                                    amazon_id=amazon_id,
                                    wb_ids=order_wb_ids,
                                    tracking_ids=tracking_id_string.split(", ") if tracking_id_string else [],
                                    message_sent=_msg_sent,
                                    message_type=_msg_type,
                                    status="success",
                                )
                            except Exception as e:
                                failure_count += 1
                                print(f"     ❌  Upload FAILED: {e}")
                                log_tracking_upload(
                                    amazon_id=amazon_id,
                                    wb_ids=order_wb_ids,
                                    tracking_ids=tracking_id_string.split(", ") if tracking_id_string else [],
                                    message_sent=_msg_sent,
                                    message_type=_msg_type,
                                    status="failed",
                                    note=str(e),
                                )

                        print(f"\n  ✅  Batch {batch_num} complete — {len(batch_with_tracking)} uploaded so far this batch.")
                    else:
                        print(f"\n  ⏩  Batch {batch_num} — no tracking IDs found, nothing to upload.")

                if _multi_package_orders:
                    print(f"\n  {'─'*54}")
                    print(f"  📦  MULTI-PACKAGE ORDERS NEEDING A MANUAL SECOND TRACKING ID")
                    print(f"  {'─'*54}")
                    for _mp in _multi_package_orders:
                        print(f"  • {_mp['amazon_id']}  uploaded={_mp['uploaded']}  "
                              f"missing={', '.join(_mp['not_uploaded'])}")

                print(f"\n  {'─'*54}")
                print(f"  📈  Upload Summary for '{store_name}'")
                print(f"  {'─'*54}")
                print(f"  ✅  Successful : {success_count}")
                print(f"  ❌  Failed     : {failure_count}")
                if already_shipped_skips:
                    print(f"  ⏩  Already shipped on Amazon (skipped): {already_shipped_skips}")

                if pending_orders:
                    print(f"\n  ⚠️  {len(pending_orders)} WB order(s) with no tracking found:")
                    for wb_id in pending_orders[:20]:
                        print(f"       • {wb_id}")
                    if len(pending_orders) > 20:
                        print(f"       • … and {len(pending_orders) - 20} more")
            _CYCLE_HEALTH["halts"] = 0
            _CYCLE_HEALTH["gmail_down"] = not gmail_services

    except (FileNotFoundError, SystemExit) as e:
        print(f"\n  ⛔  Run halted: {e}")
        _CYCLE_HEALTH["halts"] = _CYCLE_HEALTH.get("halts", 0) + 1
    except Exception as e:
        print(f"\n  ❌  Unexpected error: {e}")
        _CYCLE_HEALTH["halts"] = _CYCLE_HEALTH.get("halts", 0) + 1
    finally:
        # User requested to keep the browser open between runs
        # if driver: driver.quit()  <-- Removed
        if amazon_report_path and os.path.exists(amazon_report_path):
            try:
                os.remove(amazon_report_path)
            except:
                pass
    return driver

# --- Main Execution Loop ---
_cli_args = None  # set in __main__

def _stop_signal(signum, frame):
    """Launcher Stop / window close: close the browsers this run launched, then exit at
    once (worker threads in parallel mode would otherwise keep the process alive until
    the launcher force-kills it, and a SIGKILL would leave the browsers open)."""
    print(f"\n  ⚠️  Signal {signum} — closing this run's Chrome window(s) and exiting")
    try:
        from utils.helpers import close_owned_chromes
        close_owned_chromes(timeout=8.0)
    except Exception as e:
        print(f"  ⚠️  could not close Chrome: {e}")
    os._exit(0)


if __name__ == "__main__":
    import signal as _signal
    _signal.signal(_signal.SIGINT, _stop_signal)
    if hasattr(_signal, "SIGTERM"):
        _signal.signal(_signal.SIGTERM, _stop_signal)
    args = parse_runtime_args()
    _cli_args = args
    selected_accounts = select_accounts(args.accounts)

    if not selected_accounts:
        raise SystemExit("No matching account configuration found for --accounts option.")

    print("\n" + "─" * 60)
    print(f"  ⚙️  Run mode : {args.run_mode}")
    print(f"  🔀  Parallel : {args.parallel_style}")
    print(f"  🏪  Accounts : {', '.join(cfg['name'] for cfg in selected_accounts)}")
    print(f"  ⏱️  Sleep    : {args.sleep_seconds}s")
    print(f"  🔁  Loop     : {'single-cycle' if args.single_cycle else 'continuous'}")
    print("─" * 60)

    while True:
        print("\n" + "═" * 60)
        print(f"  🚀  NEW CYCLE  |  {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("═" * 60)

        if args.run_mode == "parallel" and len(selected_accounts) > 1:
            if args.parallel_style == "independent" and not args.single_cycle:
                print("\n  🧵  Parallel independent mode enabled. Each account runs on its own timer.")
                with ThreadPoolExecutor(max_workers=len(selected_accounts)) as executor:
                    future_map = {
                        executor.submit(run_account_loop, cfg, args.sleep_seconds): cfg
                        for cfg in selected_accounts
                    }
                    for future in as_completed(future_map):
                        cfg = future_map[future]
                        try:
                            future.result()
                        except Exception as e:
                            print(f"  ❌  Account loop crashed for {cfg['name']}: {e}")
                            raise
            else:
                print("\n  🧵  Parallel synchronized mode enabled. Running accounts side-by-side...")
                with ThreadPoolExecutor(max_workers=len(selected_accounts)) as executor:
                    future_map = {executor.submit(main_workflow, cfg): cfg for cfg in selected_accounts}
                    for future in as_completed(future_map):
                        cfg = future_map[future]
                        try:
                            future.result()
                        except Exception as e:
                            print(f"  ❌  Account run crashed for {cfg['name']}: {e}")
        else:
            if not hasattr(_cli_args, 'active_drivers'):
                _cli_args.active_drivers = {}
            for config in selected_accounts:
                print("\n" + "─" * 60)
                print(f"  🏪  ACCOUNT: {config['name'].upper()}")
                print("─" * 60)
                d = main_workflow(config, passed_driver=_cli_args.active_drivers.get(config['name']))
                _cli_args.active_drivers[config['name']] = d

        if args.single_cycle:
            print("\n  ✅  Single-cycle mode complete. Exiting.")
            break

        print("\n" + "═" * 60)
        print("  ⏸️   Cycle complete. Pausing before next run...")
        print("═" * 60)
        time.sleep(cycle_sleep_seconds(args.sleep_seconds))