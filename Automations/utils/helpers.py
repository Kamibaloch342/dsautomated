"""
order_helpers.py — Browser setup, CSV logging, coupon metadata, and utilities.
"""

import os
import sys
import csv
import json
import time
import random
import signal
import socket
import inspect
import contextlib
import subprocess
import functools

import requests
from datetime import datetime
from urllib.parse import urlparse
# pandas is imported lazily inside download_read_and_delete_csv (its only user) so
# that importing this module never loads numpy/pandas — those compiled DLLs are
# blocked by Windows 11 Smart App Control and must not stop the placer from starting.

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.common.exceptions import WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

from utils.config import (
    ACTION_TIMEOUT, DOWNLOAD_DIRECTORY, COUPON_METADATA_FILE,
    ZORO_CART_URL, ZORO_REMOVE_ITEM_BUTTON_SELECTOR,
    ZORO_EMPTY_CART_MESSAGE_SELECTOR, ZORO_PRODUCT_JSON_SELECTOR,
    get_chrome_paths,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

print = functools.partial(print, flush=True)

# ── Session-survival timeouts ─────────────────────────────────────────────────
# Chromedriver processes commands one at a time per session, so a navigation
# with no page-load timeout that stalls (slow page, bot-wall tarpit) wedges the
# whole session: every queued command then burns the selenium client's full
# 120s read timeout — the "HTTPConnectionPool(host='localhost', ...) Read
# timed out (read timeout=120)" cascades in the console log. With a page-load
# timeout, a stalled page costs ONE TimeoutException after 60s and the session
# stays usable.
PAGE_LOAD_TIMEOUT_S = 60
ASYNC_SCRIPT_TIMEOUT_S = 30


def apply_cdp_stealth(driver):
    """Hide navigator.webdriver & friends via CDP on every new document.

    REPLACES the --disable-blink-features=AutomationControlled LAUNCH FLAG: on
    Chrome 147 that flag paints the visible "unsupported command-line flag"
    banner on every window — and the banner itself is an automation tell
    (Walmart PerimeterX had it on the blocked page). The CDP route hides the
    same signals and leaves no banner. Safe on any driver; never raises."""
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",
                               {"source": _build_stealth_js()})
    except Exception:
        pass
    return driver


# ── Browsers this process launched (closed again at exit, like a child process would be) ──
_OWNED_CHROMES = []          # [(profile_path, port)]
_OWNED_ATEXIT = []


def register_owned_chrome(profile_path, port):
    """Remember a Chrome THIS process launched so it is closed when the process ends —
    'open -a' makes Chrome a child of launchd, so nothing else would close it."""
    import atexit
    key = (str(profile_path), int(port) if port else None)
    if key not in _OWNED_CHROMES:
        _OWNED_CHROMES.append(key)
    if not _OWNED_ATEXIT:
        _OWNED_ATEXIT.append(True)
        atexit.register(close_owned_chromes)


def close_owned_chromes(timeout=8.0):
    """Close every browser this process launched (idempotent)."""
    from utils.proc import close_chrome
    n = 0
    for profile_path, port in list(_OWNED_CHROMES):
        try:
            asked, forced = close_chrome(profile_path, port, timeout=timeout)
            n += asked
        except Exception:
            pass
    _OWNED_CHROMES.clear()
    return n


class ChromeHandle:
    """Process-like handle for a Chrome launched through 'open -a' (or a direct Popen).
    poll()/terminate()/kill()/wait() act on the real BROWSER process, so callers written for
    a child-process Popen (`_cleanup` in the placer) keep working unchanged."""
    def __init__(self, proc, profile_path, port):
        self.proc, self.profile_path, self.port = proc, str(profile_path), int(port)
        self.returncode = None

    def _pids(self):
        from utils.proc import chrome_browser_pids
        return chrome_browser_pids(self.profile_path, self.port)

    @property
    def pid(self):
        p = self._pids()
        return min(p) if p else (self.proc.pid if self.proc is not None else None)

    def poll(self):
        if self._pids():
            return None
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode

    def wait(self, timeout=None):
        import time as _t
        end = None if timeout is None else _t.time() + timeout
        while self.poll() is None:
            if end is not None and _t.time() > end:
                raise subprocess.TimeoutExpired("chrome", timeout)
            _t.sleep(0.25)
        return self.returncode

    def terminate(self):
        from utils.proc import close_chrome
        close_chrome(self.profile_path, self.port, timeout=8.0)
        try:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass

    def kill(self):
        from utils.proc import close_chrome
        close_chrome(self.profile_path, self.port, timeout=0.0)
        try:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.kill()
        except Exception:
            pass


def mark_profile_clean_exit(user_data_dir):
    """Clear the "Chrome didn't shut down correctly / Restore pages?" state in a
    profile BEFORE launching Chrome with it. Also removes stale SingletonLock /
    SingletonSocket / SingletonCookie / DevToolsActivePort entries left behind by a
    killed Chrome — on macOS 27 a stale lock made every launch fail with "Could not
    remove old devtools port file" (230 times on 17 Sep 2026). Only call this when no
    Chrome is running on the profile (setup_driver checks the debugging port first).

    Missing/new profiles and malformed files are ignored."""
    import json as _json
    expanded_dir = os.path.expanduser(str(user_data_dir))
    for lock in ("SingletonLock", "SingletonSocket", "SingletonCookie", "DevToolsActivePort"):
        try:
            lock_path = os.path.join(expanded_dir, lock)
            if os.path.islink(lock_path) or os.path.exists(lock_path):
                os.unlink(lock_path)
        except OSError:
            pass

    try:
        prefs_path = os.path.join(expanded_dir,
                                  "Default", "Preferences")
        if not os.path.isfile(prefs_path):
            return
        with open(prefs_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        prof = data.setdefault("profile", {})
        if prof.get("exit_type") != "Normal" or prof.get("exited_cleanly") is not True:
            prof["exit_type"] = "Normal"
            prof["exited_cleanly"] = True
            with open(prefs_path, "w", encoding="utf-8") as f:
                _json.dump(data, f)
    except Exception:
        pass


# Memory-softening flags for every fleet Chrome (placers, trackers, closing,
# FedEx). Stealth-neutral — none of these alter the JS/navigator surface a
# site can fingerprint. --renderer-process-limit is the big one: rush mode
# opens 10 tabs at once, and without a cap each gets its own renderer process.
# The rest stop background services, telemetry and unbounded disk cache from
# eating RAM/CPU while 7+ Chromes run side by side.
CHROME_SOFT_FLAGS = [
    "--renderer-process-limit=4",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-default-apps",
    "--disable-component-update",
    "--metrics-recording-only",
    "--mute-audio",
    "--disk-cache-size=104857600",
]
CHROME_SOFT_FLAGS_STR = " ".join(CHROME_SOFT_FLAGS)

# Headless placers (memory saver — 4 headed Chromes hurt an 8 GB machine).
# Opt-in only: ZUHRA_HEADLESS=1 in the environment (the launcher sets it from
# bot_config.json {"chrome": {"headless": true}}) or that config key directly.
# Only affects a Chrome WE launch; an already-running Chrome on the port is reused as-is.
HEADLESS_FLAGS = ["--headless=new", "--window-size=1440,900", "--disable-gpu"]
HEADLESS_FLAGS_STR = " ".join(HEADLESS_FLAGS)


def headless_wanted(component: str = "orders"):
    """Should THIS component's Chrome run without a window?

    Placers and trackers are switched separately: a tracker can run headless to save memory
    while the placers keep visible windows for checkout (or the other way round).
      orders   → env ZUHRA_HEADLESS          / bot_config chrome.headless
      tracking → env ZUHRA_HEADLESS_TRACKING / bot_config chrome.headless_tracking
    An unknown component falls back to the orders setting.
    """
    if str(component or "").strip().lower().startswith("track"):
        env_key, cfg_key = "ZUHRA_HEADLESS_TRACKING", "headless_tracking"
    else:
        env_key, cfg_key = "ZUHRA_HEADLESS", "headless"
    v = os.environ.get(env_key, "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    try:
        from utils.config import load_gui_config
        return bool((load_gui_config().get("chrome") or {}).get(cfg_key))
    except Exception:
        return False


# Zoro approved this reseller account, so the placer says who it is: the UA carries the company
# name, which lets Zoro attribute this traffic to the account they approved instead of logging it
# as an unidentified client — the same reason the attributed-checkout switch exists. It also
# replaces the string a headless Chrome sends by default, which literally reads "HeadlessChrome".
#
# Scope is deliberately narrow. It applies to the PLACER's Chrome only (via harden_driver), and
# the two Seller Central navigations wrap themselves in amazon_identity() so Amazon keeps seeing
# the exact browser it has always seen. Trackers build their own driver and are untouched.
RESELLER_USER_AGENT = ("Mozilla/5.0 (RajarEnterpriseLLC/2026) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/147.0.7727.138 Safari/537.36")


def zoro_user_agent():
    """The UA the placer presents to Zoro.

    env ZUHRA_ZORO_UA, then bot_config chrome.zoro_user_agent, then the reseller default.
    Set either to an empty string to send nothing and keep Chrome's own UA.
    """
    v = os.environ.get("ZUHRA_ZORO_UA")
    if v is not None:
        return v.strip()
    try:
        from utils.config import load_gui_config
        cfg = (load_gui_config().get("chrome") or {})
        if "zoro_user_agent" in cfg:
            return str(cfg.get("zoro_user_agent") or "").strip()
    except Exception:
        pass
    return RESELLER_USER_AGENT


def apply_zoro_identity(driver):
    """Present the reseller UA on this browser's requests — page loads and fetch() alike."""
    ua = zoro_user_agent()
    if not ua:
        return driver
    try:
        driver.execute_cdp_cmd("Network.setUserAgentOverride",
                               {"userAgent": ua, "acceptLanguage": "en-US,en"})
    except Exception:
        pass
    return driver


def browser_user_agent(driver):
    """Chrome's own UA for this browser, as it would send with no override applied.

    Cached per driver: it cannot change while the browser lives, and this is read on every
    navigation away from Zoro.
    """
    cached = getattr(driver, "_zuhra_stock_ua", None)
    if cached is not None:
        return cached
    try:
        ua = (driver.execute_cdp_cmd("Browser.getVersion", {}) or {}).get("userAgent", "") or ""
    except Exception:
        return ""
    try:
        driver._zuhra_stock_ua = ua
    except Exception:
        pass
    return ua


def apply_stock_identity(driver):
    """Hand the browser back its own UA — for every host that is not Zoro."""
    stock = browser_user_agent(driver)
    if not stock:
        return driver
    try:
        driver.execute_cdp_cmd("Network.setUserAgentOverride",
                               {"userAgent": stock, "acceptLanguage": "en-US,en"})
    except Exception:
        pass
    return driver


@contextlib.contextmanager
def amazon_identity(driver):
    """Chrome's own UA for the duration, the Zoro one restored on the way out.

    Amazon has no part in the Zoro arrangement, so Seller Central must never start seeing a
    reseller UA it has not seen from this profile before.
    """
    apply_stock_identity(driver)
    try:
        yield driver
    finally:
        apply_zoro_identity(driver)


def bind_identity_to_navigation(driver):
    """Choose the identity from the host being navigated to, at the one choke point.

    Every Zoro request the placer makes begins with a navigation to zoro.com, and the XHRs that
    page then fires — including the whole internal order API — inherit the UA that was in force
    when it loaded. Switching here therefore covers the API calls too, and covers them without
    touching the twenty-odd driver.get() call sites or risking one being missed. Anything that
    is not Zoro, Seller Central above all, gets Chrome's own UA exactly as before.
    """
    if getattr(driver, "_zuhra_identity_bound", False):
        return driver
    original_get = driver.get

    def get(url, *args, **kwargs):
        try:
            parts = urlparse(url or "")
            # Only a real web navigation picks an identity. chrome://settings parses with a
            # hostname of "settings", and about:blank, file:// and data: pages send nothing to
            # anyone — none of them should disturb whichever UA is in force.
            host = ((parts.hostname or "").lower()
                    if parts.scheme in ("http", "https") else "")
        except Exception:
            host = ""
        if host == "zoro.com" or host.endswith(".zoro.com"):
            apply_zoro_identity(driver)
        elif host:
            apply_stock_identity(driver)
        return original_get(url, *args, **kwargs)

    try:
        driver.get = get
        driver._zuhra_identity_bound = True
    except Exception:
        pass
    return driver


def page_checkout_wanted():
    """Finish orders on Zoro's REAL /checkout page instead of POSTing them to the order API.

    Why it exists: Zoro's marketing attribution runs on Google Tag Manager / GA4, which fires
    on PAGE events. Placing through the API renders no page, so no view_item, begin_checkout or
    purchase event ever reaches them — the order shows up in their order system with no channel
    attached, which is the "unattributed web order activity" their reseller team asked about on
    17 Sep 2026. Finishing on the checkout page lets their own tags record the sale.

    Cart, address, shipping and coupons still go through the API; only the final Place Order
    moves to the page. Measured cost on 18 Sep: 21 s median per order via the API vs 48 s via
    the page.

    ZUHRA_PAGE_CHECKOUT=1/0, or bot_config {"checkout": {"page_checkout": true}}.
    """
    v = os.environ.get("ZUHRA_PAGE_CHECKOUT", "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    try:
        from utils.config import load_gui_config
        return bool((load_gui_config().get("checkout") or {}).get("page_checkout"))
    except Exception:
        return False


def harden_driver(driver):
    """Apply the survival timeouts and the Zoro identity to a fresh placer driver."""
    try:
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_S)
        driver.set_script_timeout(ASYNC_SCRIPT_TIMEOUT_S)
    except Exception:
        pass
    return bind_identity_to_navigation(driver)


def safe_get(driver, url, retries=1):
    """driver.get for READ-ONLY navigations: on page-load timeout, stop the
    stalled load and retry once so one slow page doesn't fail the order.
    Never use for navigations that submit or check out — a retry could
    re-submit."""
    from selenium.common.exceptions import TimeoutException
    last = None
    for _ in range(retries + 1):
        try:
            driver.get(url)
            return
        except TimeoutException as e:
            last = e
            try:
                driver.execute_script("window.stop()")
            except Exception:
                pass
    raise last


# ==============================================================================
#  KWARGS COMPATIBILITY FILTER
# ==============================================================================

def filter_kwargs_for_callee(func, kwargs, warn_label=None):
    """Return the subset of `kwargs` that `func` can accept by keyword.

    The checkout backends (true_api → vuex → DOM) share one kwargs dict built
    by the engine, but their signatures have drifted — splatting the full dict
    into a narrower fallback raises TypeError at call time, before the order
    is even attempted. If `func` declares **kwargs, everything passes through.
    Dropped keys are printed when `warn_label` is set so signature drift stays
    visible in run logs instead of failing silently.
    """
    params = inspect.signature(func).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(kwargs)
    accepted = {
        k: v for k, v in kwargs.items()
        if k in params and params[k].kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    dropped = sorted(set(kwargs) - set(accepted))
    if dropped and warn_label:
        print(f"   [{warn_label}] {func.__name__} does not accept "
              f"{dropped} — dropped for compatibility")
    return accepted


# ==============================================================================
#  CSV LOGGING
# ==============================================================================

LOG_HEADERS = [
    "Timestamp", "Amazon_ID", "SKU", "Status", "Zoro_Price",
    "Amazon_Payout", "Margin_%", "Profit_$", "Zoro_Order_ID", "Notes"
]


def init_csv(active_profile):
    """Create CSV log file for this cycle if it doesn't exist. Returns filename."""
    filename = f"Order_Logs_{active_profile}_{datetime.now().strftime('%Y-%m-%d')}.csv"
    if not os.path.exists(filename):
        with open(filename, 'w', newline='') as f:
            csv.writer(f).writerow(LOG_HEADERS)
    return filename


def log_to_csv(log_filename, data_dict):
    """Append one row to the CSV log."""
    with open(log_filename, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%H:%M:%S"),
            data_dict.get("Amazon_ID", "N/A"),
            data_dict.get("SKU", "N/A"),
            data_dict.get("Status", "N/A"),
            data_dict.get("Zoro_Price", 0.0),
            data_dict.get("Amazon_Payout", 0.0),
            data_dict.get("Margin_%", 0.0),
            data_dict.get("Profit_$", 0.0),
            data_dict.get("Zoro_Order_ID", "N/A"),
            data_dict.get("Notes", "")
        ])


# ==============================================================================
#  DASHBOARD PRINTING
# ==============================================================================

def print_rich_summary(active_profile, total, processed, placed, failed, low_profit, last_action=""):
    """Print a clean dashboard (plain text for piped output)."""
    w = 40
    print(f"\n╔{'═'*w}╗")
    print(f"║{'DASHBOARD':^{w}}║")
    print(f"║{active_profile:^{w}}║")
    print(f"╠{'═'*w}╣")
    print(f"║  Pending    {total:>5}  │  Success  {placed:>5}  ║")
    print(f"║  Processed  {processed:>5}  │  Failed   {failed:>5}  ║")
    print(f"║  Low Profit {low_profit:>5}  │           {'':>5}  ║")
    print(f"╚{'═'*w}╝")
    if last_action:
        print(f"  ↳ {last_action}")


# ==============================================================================
#  DATA DOWNLOAD
# ==============================================================================

def download_read_and_delete_csv(url, filename, download_dir):
    """Download CSV from URL, read into DataFrame, delete temp file."""
    file_path = os.path.join(download_dir, filename)
    try:
        import pandas as pd  # lazy: keep numpy/pandas out of module import (SAC-safe)
        print(f"   -> Downloading {filename}...")
        response = requests.get(url)
        response.raise_for_status()
        with open(file_path, 'wb') as f:
            f.write(response.content)
        df = pd.read_csv(file_path)
        return df
    except ImportError as e:
        print(f"❌ CSV fallback needs pandas, which is blocked/absent ({e}). "
              f"The Sheets API is the primary path; turn off Windows Smart App Control "
              f"if you need this fallback.")
        return None
    except Exception as e:
        print(f"❌ CSV Error: {e}")
        return None
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


# ==============================================================================
#  COUPON METADATA
# ==============================================================================

def load_coupon_metadata():
    """Load history of used/invalid coupons."""
    if not os.path.exists(COUPON_METADATA_FILE):
        return {}
    try:
        with open(COUPON_METADATA_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def update_coupon_status(code, status):
    """Update a coupon's status and save immediately."""
    data = load_coupon_metadata()
    data[code] = status
    try:
        with open(COUPON_METADATA_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"   [METADATA] Marked '{code}' as {status}")
    except Exception as e:
        print(f"   [METADATA] Error saving: {e}")


# ==============================================================================
#  STEALTH HELPERS
# ==============================================================================

def _build_stealth_js():
    """Return the JavaScript payload injected into every new document via CDP.

    This runs BEFORE any page JS executes, so DataDome's collector never sees
    the chromedriver artefacts.
    """
    return r"""
    (function(){
        // --- 1. Remove ALL chromedriver window artefacts (cdc_, $cdc_) ---
        // ChromeDriver injects properties like  cdc_adoQpoasnfa76pfcZLmcfl_Array
        // on the window object. DataDome simply iterates window keys for /^[$]?cdc_/.
        try {
            for (var key in window) {
                if (/^[$]?cdc_/.test(key)) {
                    try { delete window[key]; } catch(_) {}
                }
            }
        } catch(_) {}

        // --- 2. Properly hide navigator.webdriver ---
        // Real Chrome sets webdriver = false with {configurable: false} on the prototype.
        // A naïve defineProperty on navigator leaves configurable: true (detectable).
        // We define on the prototype AND make it non-configurable.
        try {
            var proto = Object.getPrototypeOf(navigator);
            Object.defineProperty(proto, 'webdriver', {
                get: function(){ return false; },
                configurable: false,
                enumerable: true
            });
        } catch(_) {
            try {
                Object.defineProperty(navigator, 'webdriver', {
                    get: function(){ return undefined; },
                    configurable: false
                });
            } catch(_2) {}
        }

        // --- 3. Ensure chrome.runtime exists (non-headless signal) ---
        try {
            if (!window.chrome) window.chrome = {};
            if (!window.chrome.runtime) {
                window.chrome.runtime = {
                    connect: function(){},
                    sendMessage: function(){}
                };
            }
        } catch(_) {}

        // --- 4. Patch navigator.permissions.query ---
        // DataDome queries {name:'notifications'} and checks for automation markers.
        try {
            var origQuery = navigator.permissions.query.bind(navigator.permissions);
            navigator.permissions.query = function(params) {
                if (params && params.name === 'notifications') {
                    return Promise.resolve({state: Notification.permission});
                }
                return origQuery(params);
            };
        } catch(_) {}

        // --- 5. Patch navigator.plugins to look non-empty ---
        // An empty PluginArray is a headless/automation signal.
        try {
            if (navigator.plugins.length === 0) {
                Object.defineProperty(navigator, 'plugins', {
                    get: function() {
                        return [
                            {name:'Chrome PDF Plugin', filename:'internal-pdf-viewer',
                             description:'Portable Document Format', length:1},
                            {name:'Chrome PDF Viewer', filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                             description:'', length:1},
                            {name:'Native Client', filename:'internal-nacl-plugin',
                             description:'', length:2}
                        ];
                    }
                });
            }
        } catch(_) {}

        // --- 6. Patch navigator.languages ---
        try {
            Object.defineProperty(navigator, 'languages', {
                get: function(){ return ['en-US', 'en']; }
            });
        } catch(_) {}

    })();
    """


def _apply_stealth_now(driver):
    """Apply stealth patches to the CURRENT page (cdc_ removal + webdriver hide).
    Called once on attach and after cache clears."""
    try:
        driver.execute_script("""
            // cdc_ removal
            for (var key in window) {
                if (/^[$]?cdc_/.test(key)) {
                    try { delete window[key]; } catch(e) {}
                }
            }
            // navigator.webdriver
            try {
                var proto = Object.getPrototypeOf(navigator);
                Object.defineProperty(proto, 'webdriver', {
                    get: function(){ return false; },
                    configurable: false,
                    enumerable: true
                });
            } catch(_) {}
        """)
    except Exception:
        pass


def reapply_stealth(driver):
    """Public helper — call after any action that might re-expose cdc_ keys
    (cookie clears, cache resets, profile reloads)."""
    _apply_stealth_now(driver)


def simulate_human(driver, intensity="light"):
    """Inject random mouse movements + scroll events to simulate human interaction.

    intensity: "light"  — few movements (before API calls)
               "medium" — more movements (before page interactions)
               "full"   — many movements + scrolls (first load / restriction clear)
    """
    try:
        vw = driver.execute_script("return window.innerWidth || 1280")
        vh = driver.execute_script("return window.innerHeight || 800")
    except Exception:
        vw, vh = 1280, 800

    moves = {"light": 2, "medium": 4, "full": 8}.get(intensity, 2)
    scrolls = {"light": 1, "medium": 2, "full": 3}.get(intensity, 1)

    js_parts = []
    for _ in range(moves):
        x = random.randint(100, vw - 100)
        y = random.randint(100, vh - 100)
        js_parts.append(
            f"document.dispatchEvent(new MouseEvent('mousemove',"
            f"{{clientX:{x},clientY:{y},bubbles:true}}));"
        )

    for _ in range(scrolls):
        dy = random.randint(50, 300) * random.choice([1, -1])
        js_parts.append(f"window.scrollBy({{top:{dy},behavior:'smooth'}});")

    try:
        driver.execute_script("\n".join(js_parts))
    except Exception:
        pass

    time.sleep(random.uniform(0.2, 0.6))


def human_delay(low=0.4, high=1.8):
    """Sleep a random duration — call between rapid sequential operations."""
    time.sleep(random.uniform(low, high))


# ==============================================================================
#  BROWSER / DRIVER SETUP
# ==============================================================================

_DRIVER_CACHE = os.path.join(os.path.expanduser("~"), ".wdm", "resolved_driver.json")


def _probe_chromedriver(p):
    try:
        r = subprocess.run([p, "--version"], capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def _installed_chrome_major():
    """Major version of the installed Chrome, or None if unknown (Windows
    chrome.exe prints nothing for --version, so Windows always returns None
    and keeps the pre-cache behavior)."""
    import re as _re
    try:
        _, chrome_exe = get_chrome_paths("_version_probe")
        out = subprocess.run([chrome_exe, "--version"], capture_output=True,
                             timeout=8, text=True).stdout or ""
        m = _re.search(r"(\d+)\.", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def ensure_chromedriver():
    """Install chromedriver via webdriver_manager, hardened against the two
    failure modes behind the recurring 'Status code was: -9' (SIGKILL) crashes:

    1. Several placer profiles launching together race the download of a new
       driver version into the same ~/.wdm path; one process execs a
       half-written binary. Serialized with an exclusive file lock.
    2. That poisoned exec makes macOS Gatekeeper cache a permanent per-FILE
       'rejected' verdict — the finished file passes codesign, yet every launch
       is SIGKILLed (observed 2026-07-16 with driver 150.0.7871.124). Detected
       here with a --version probe; cured by rewriting the binary onto a fresh
       inode, which resets the cached verdict.

    FAST PATH: the last resolved driver is cached in ~/.wdm/resolved_driver.json.
    If it still launches and still matches the installed Chrome's major version,
    it's reused with NO network call and NO lock — so all profiles starting
    together come up in parallel instead of queuing behind the install lock.
    """
    try:
        c = json.load(open(_DRIVER_CACHE))
        path = c.get("path") or ""
        major = _installed_chrome_major()
        if (path and os.path.exists(path) and major is not None
                and major == c.get("chrome_major") and _probe_chromedriver(path)):
            return path
    except Exception:
        pass

    lock_fh = None
    try:
        try:
            import fcntl
            lock_dir = os.path.join(os.path.expanduser("~"), ".wdm")
            os.makedirs(lock_dir, exist_ok=True)
            lock_fh = open(os.path.join(lock_dir, ".driver_install.lock"), "w")
            fcntl.flock(lock_fh, fcntl.LOCK_EX)   # released when lock_fh closes
        except ImportError:
            pass  # Windows has no fcntl; the Gatekeeper issue is macOS-only

        path = ChromeDriverManager().install()

        if not _probe_chromedriver(path):
            print("   ⚠️  chromedriver failed its launch probe (Gatekeeper kill?) — refreshing binary...")
            tmp = path + ".refresh"
            with open(path, "rb") as src, open(tmp, "wb") as dst:
                dst.write(src.read())
            os.chmod(tmp, 0o755)
            os.replace(tmp, path)   # new inode → fresh Gatekeeper evaluation
            if _probe_chromedriver(path):
                print("   ✅ chromedriver recovered after binary refresh")
            else:
                print("   ❌ chromedriver still failing after refresh — Selenium will report the error")

        try:
            major = _installed_chrome_major()
            if major is not None:
                with open(_DRIVER_CACHE, "w") as f:
                    json.dump({"path": path, "chrome_major": major}, f)
        except Exception:
            pass
        return path
    finally:
        if lock_fh:
            lock_fh.close()


def setup_driver(debugging_port, profile_folder_name):
    """Initialize WebDriver with stealth patches. Returns (driver, chrome_process) or (None, None)."""
    print("🚀 Initializing WebDriver (Stealth + Region Optimized)...")

    profile_path, chrome_exe = get_chrome_paths(profile_folder_name)
    chrome_process = None

    # Check if Chrome is already running on this port
    _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _sock.settimeout(2)
    port_open = _sock.connect_ex(('127.0.0.1', debugging_port)) == 0
    _sock.close()

    headless = headless_wanted()
    if headless:
        print("   -> 🫥 headless mode (--headless=new) — memory saver, no window")

    def _launch_chrome():
        try:
            mark_profile_clean_exit(profile_path)

            chrome_args = [
                f'--remote-debugging-port={debugging_port}',
                f'--user-data-dir={profile_path}',
                '--hide-crash-restore-bubble',
                '--no-first-run',
                '--no-default-browser-check',
                '--lang=en-US',
            ] + CHROME_SOFT_FLAGS
            if headless:
                chrome_args += HEADLESS_FLAGS

            # Capture Chrome stderr to a temp log so we can diagnose crashes
            import tempfile
            chrome_log = os.path.join(tempfile.gettempdir(),
                                      f"chrome_debug_{debugging_port}.log")

            proc = None
            if sys.platform == "darwin":
                # macOS 27+: com.apple.provenance prevents Chrome from creating
                # SingletonLock when launched as a subprocess of Python. Using
                # 'open -a' launches Chrome through LaunchServices, giving it its
                # own app identity and proper TCC context.
                open_cmd = [
                    "open", "-a", "Google Chrome", "-n", "-W",
                    "--args",
                ] + chrome_args
                _chrome_log_fh = open(chrome_log, "w", encoding="utf-8")
                proc = subprocess.Popen(open_cmd,
                                       stderr=_chrome_log_fh,
                                       stdout=subprocess.DEVNULL)
            else:
                command = (
                    f'"{ chrome_exe}"'
                    + ' ' + ' '.join(f'"{a}"' if ' ' in a else a for a in chrome_args)
                )
                _chrome_log_fh = open(chrome_log, "w", encoding="utf-8")
                proc = subprocess.Popen(command, shell=True,
                                       stderr=_chrome_log_fh,
                                       stdout=subprocess.DEVNULL)

            # Wait for Chrome to start listening (poll port, max 15s)
            port_opened = False
            for _ in range(30):
                time.sleep(0.5)
                # Check if Chrome already exited (crashed)
                # NOTE: with 'open -W' the proc stays alive as long as Chrome runs,
                # so poll() returning non-None means a real failure.
                if proc.poll() is not None:
                    _chrome_log_fh.close()
                    print(f"   ❌ Chrome exited immediately with code {proc.returncode}")
                    try:
                        with open(chrome_log, "r") as f:
                            err = f.read(2000).strip()
                        if err:
                            print(f"   📋 Chrome stderr:\n{err}")
                    except Exception:
                        pass
                    return proc
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                if s.connect_ex(('127.0.0.1', debugging_port)) == 0:
                    s.close()
                    port_opened = True
                    break
                s.close()
            if port_opened:
                try:
                    _chrome_log_fh.close()
                except Exception:
                    pass
                register_owned_chrome(profile_path, debugging_port)
                return ChromeHandle(proc, profile_path, debugging_port)
            if not port_opened:
                _chrome_log_fh.close()
                alive = proc.poll() is None
                print(f"   ⚠️  Chrome port {debugging_port} not open after 15s "
                      f"(process {'alive' if alive else 'dead'})")
                try:
                    with open(chrome_log, "r") as f:
                        err = f.read(2000).strip()
                    if err:
                        print(f"   📋 Chrome stderr:\n{err}")
                except Exception:
                    pass
            return proc
        except Exception as e:
            print(f"❌ Chrome Launch Error: {e}")
            return None

    if port_open:
        print(f"   -> ✅ Chrome already running on port {debugging_port} — reusing.")
    else:
        chrome_process = _launch_chrome()
        if chrome_process is None:
            return None, None

    try:
        chrome_options = ChromeOptions()
        chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{debugging_port}")
        chrome_options.add_argument("--lang=en-US")
        # NOTE: excludeSwitches / useAutomationExtension cannot be used with
        # debuggerAddress (attach mode). The CDP stealth JS handles detection.

        prefs = {
            "download.default_directory": DOWNLOAD_DIRECTORY,
            "intl.accept_languages": "en-US,en",
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False
        }
        chrome_options.add_experimental_option("prefs", prefs)

        service = ChromeService(ensure_chromedriver())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        harden_driver(driver)

        # Stealth CDP commands
        print("   -> 🕵️  Applying Anti-Detect Patches...")
        driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": "America/New_York"})

        # Comprehensive stealth script — injected into every new document
        # Removes chromedriver fingerprints, hardens navigator.webdriver,
        # and patches detectable property descriptors.
        stealth_js = _build_stealth_js()
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": stealth_js
        })

        # Also apply to the CURRENT page immediately
        _apply_stealth_now(driver)

        # Verification
        print("\n   🔍 --- STEALTH CHECK ---")
        try:
            tz = driver.execute_script("return Intl.DateTimeFormat().resolvedOptions().timeZone")
            lang = driver.execute_script("return navigator.language")
            wd_flag = driver.execute_script("return navigator.webdriver")
            cdc_count = driver.execute_script(
                "var c=0;for(var k in window){if(/^cdc_/.test(k))c++;}return c;"
            )
            print(f"   ✅ Timezone      : {tz}")
            print(f"   ✅ Language      : {lang}")
            print(f"   ✅ Robot Flag    : {wd_flag}")
            print(f"   ✅ CDC artifacts : {cdc_count}")
            if lang != "en-US":
                print("   ⚠️ WARNING: Language is still not en-US. Check Mac System Settings.")
            if tz != "America/New_York":
                print("   ⚠️ WARNING: Timezone mismatch!")
            if cdc_count > 0:
                print(f"   ⚠️ WARNING: {cdc_count} cdc_ keys still present — will be cleaned on next page load")
            print("   ------------------------\n")
        except Exception as e:
            print(f"   ⚠️ Verification Failed: {e}")

        print("✅ Selenium Connected.")
        return driver, chrome_process

    except WebDriverException as e:
        print(f"❌ Selenium Error: {e}")
        # If the port was "open" but we couldn't connect, the Chrome process
        # is stale/zombie. Kill it and launch fresh.
        if port_open and chrome_process is None:
            print("   -> 🔄 Stale Chrome detected — killing processes on port and retrying...")
            try:
                from utils.proc import kill_pids_on_port
                killed = kill_pids_on_port(debugging_port)   # cross-platform (netstat/taskkill on Win)
                if killed:
                    print(f"      killed {killed} process(es) on port {debugging_port}")
                time.sleep(3)
            except Exception as kill_err:
                print(f"   -> ⚠️ Could not kill stale processes: {kill_err}")

            chrome_process = _launch_chrome()
            if chrome_process is None:
                return None, None

            # One more connection attempt after fresh launch
            try:
                chrome_options2 = ChromeOptions()
                chrome_options2.add_experimental_option("debuggerAddress", f"127.0.0.1:{debugging_port}")
                chrome_options2.add_argument("--lang=en-US")
                prefs2 = {
                    "download.default_directory": DOWNLOAD_DIRECTORY,
                    "intl.accept_languages": "en-US,en",
                    "credentials_enable_service": False,
                    "profile.password_manager_enabled": False
                }
                chrome_options2.add_experimental_option("prefs", prefs2)
                service2 = ChromeService(ensure_chromedriver())
                driver = webdriver.Chrome(service=service2, options=chrome_options2)
                harden_driver(driver)
                print("   -> 🕵️  Applying Anti-Detect Patches (retry)...")
                driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": "America/New_York"})
                stealth_js = _build_stealth_js()
                driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js})
                _apply_stealth_now(driver)
                print("✅ Selenium Connected (after stale-Chrome recovery).")
                return driver, chrome_process
            except WebDriverException as e2:
                print(f"❌ Retry also failed: {e2}")
                return None, None

        return None, None


def clear_browser_cache(driver):
    """Clear browser cache via chrome://settings."""
    print("\n🧹 [STABILITY] Clearing cache...")
    try:
        driver.get('chrome://settings/clearBrowserData')
        time.sleep(2)
        js_script = """
            var root = document.querySelector('settings-ui').shadowRoot;
            var dialog = root.querySelector('settings-main').shadowRoot.querySelector('settings-basic-page').shadowRoot.querySelector('settings-section > settings-privacy-page').shadowRoot.querySelector('settings-clear-browsing-data-dialog').shadowRoot;
            var clearButton = dialog.querySelector('#clearBrowsingDataConfirm');
            clearButton.click();
        """
        driver.execute_script(js_script)
        time.sleep(4)
        return True
    except Exception:
        return False


# ==============================================================================
#  ZORO CART (SELENIUM)
# ==============================================================================

def clear_zoro_cart(driver):
    """Clear all items from Zoro cart using Selenium."""
    wait = WebDriverWait(driver, 10)
    driver.get(ZORO_CART_URL)
    try:
        wait.until(EC.presence_of_element_located(ZORO_EMPTY_CART_MESSAGE_SELECTOR))
        return True
    except Exception:
        attempts = 0
        while attempts < 10:
            try:
                btns = driver.find_elements(*ZORO_REMOVE_ITEM_BUTTON_SELECTOR)
                if not btns:
                    return True
                driver.execute_script("arguments[0].click();", btns[0])
                time.sleep(3)
                attempts += 1
            except Exception:
                driver.refresh()
                time.sleep(4)
    return True


def get_product_details_from_json(driver):
    """Extract product price and brand from JSON-LD microdata."""
    try:
        el = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(ZORO_PRODUCT_JSON_SELECTOR))
        data = json.loads(el.get_attribute('innerHTML'))
        price = float(data.get('offers', {}).get('price', 0.0))
        brand = data.get('brand', {}).get('name', 'UNKNOWN').strip()
        if price > 0:
            return {'price': price, 'brand': brand}
        return None
    except Exception:
        return None
