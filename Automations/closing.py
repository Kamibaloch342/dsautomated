#!/usr/bin/env python3
"""
closing.py — Single-file closing pipeline (replaces closing_runner + closing_pipeline).

The business rule, exactly:
  download 30-day report → keep WB orders → consolidate multi-item →
  FOR EACH BATCH OF 30:
     1. look up the ORDER-PLACER LOG first, take what it already has
     2. fill the rest: archive → live-Gmail (per-field) → optional SC scrape
     3. compute profit
     4. bulk FedEx-check the 30 in ONE call → delivery status/date/signed/left-at
     5. compute the label (AFTER FedEx, so delivered → Completed)
     6. persist the 30 in ONE short transaction
     7. send delivery / tracking messages + review requests for the delivered ones
  → next batch

No CLI flags, no Google Sheets. Run it with:  python closing.py
Optional env overrides (all have sane defaults):
  CLOSING_ACCOUNT=HOANK|Hathoro   CLOSING_DAYS=30        CLOSING_LIMIT=0 (0 = all)
  CLOSING_REPORT=/path/report.txt (reuse an already-downloaded report, skip the 10-min wait)
  CLOSING_DRY_RUN=0|1   CLOSING_FEDEX=1|0   CLOSING_SCRAPE=0|1   CLOSING_MESSAGES=1|0
  CLOSING_DEBUG_RAW=1   (dump every scraped SC page — handled inside amazon_scraper)
"""

from __future__ import annotations

import os
import re
import sys
import csv
import time
import uuid
import json
import base64
import logging
import subprocess
from datetime import datetime

# The Chrome connect helpers log a full 60-line chromedriver stacktrace on each
# "no Chrome yet" attempt before we launch our own — pure noise. Mute warnings
# from those modules so the closing log stays readable; real errors still show.
for _noisy in ("amazon_scraper", "fedex_api", "fedex_tracker", "selenium", "WDM"):
    logging.getLogger(_noisy).setLevel(logging.ERROR)

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

# ── KEEP modules (stable; imported, never deleted) ──────────────────────────
from utils.unified_db import (
    get_conn, init_db,
    get_placer_order, get_closing_order, upsert_closing_order, log_enrichment,
)
from utils.accounts_config import (
    accounts as _cfg_accounts, gmail_tokens as _cfg_gmail_tokens,
    preferred_inboxes, acquire_lock, release_lock,
)
from engine.fedex_api import FedExTracker, FedExSessionExpired, browser_tier_allowed
from engine.live_gmail_per_order import (
    search_order_in_gmail, search_zoro_issue, search_zoro_cost, search_zoro_tracking,
)
from engine import closing_signals as _CS
from engine.post_delivery import (
    process_single_order as pd_process_order,
    _get_gmail_services,
    ACCOUNT_CONFIGS as PD_ACCOUNT_CONFIGS,
)

print = __import__("functools").partial(print, flush=True)

# ── Config (env overrides; no argparse) ─────────────────────────────────────
def _env(name, default=""):
    v = os.environ.get(name)
    return default if v is None else v.strip()

def _env_bool(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "y", "on")

def _env_int(name, default):
    try:
        return int(_env(name) or default)
    except ValueError:
        return default

ACCOUNT       = _env("CLOSING_ACCOUNT", "HOANK")
DAYS          = _env_int("CLOSING_DAYS", 30)
LIMIT         = _env_int("CLOSING_LIMIT", 0)            # 0 = process every WB order
REPORT_PATH   = _env("CLOSING_REPORT", "") or None     # reuse an existing report
MANUAL_REPORT = _env_bool("CLOSING_MANUAL_REPORT", False)  # open SC, YOU pick dates + download, closing picks up the file
DRY_RUN       = _env_bool("CLOSING_DRY_RUN", False)
ENABLE_FEDEX  = _env_bool("CLOSING_FEDEX", True)
FEDEX_HEADLESS = _env_bool("CLOSING_FEDEX_HEADLESS", True)
ENABLE_SCRAPE = _env_bool("CLOSING_SCRAPE", False)     # optional SC fallback (+ tracking_id_uploaded)
ENABLE_MSGS   = _env_bool("CLOSING_MESSAGES", True)
MSG_DRY       = _env_bool("CLOSING_MESSAGES_DRY", False)  # full pipeline, but only PREVIEW who'd be messaged
ENABLE_GMAIL  = _env_bool("CLOSING_LIVE_GMAIL", True)   # live-Gmail per-field fallback
VERBOSE       = _env_bool("CLOSING_VERBOSE", False)     # per-order, per-field provenance log
DEBUG_EML     = _env_bool("CLOSING_DEBUG_EML", False)   # save typed .eml + SC page html per order
HARVEST       = _env_bool("CLOSING_HARVEST", False)     # save ALL emails for the amazon id + each WB id
START_DATE    = _env("CLOSING_START_DATE") or None      # YYYY-MM-DD exact-range (backfill >30 days)
END_DATE      = _env("CLOSING_END_DATE") or None
RESUME        = _env_bool("CLOSING_RESUME", True)       # skip orders already closed (crash-safe restart)
FORCE         = _env_bool("CLOSING_FORCE", False)       # reprocess everything, ignore resume
PRIORITY      = _env_bool("CLOSING_PRIORITY", True)     # process orders WITH tracking first (completable), no-tracking last
SKIP_NO_TRACK = _env_bool("CLOSING_SKIP_NO_TRACKING", False)  # fast pass: process only orders that have tracking anywhere
TERMINAL_LABELS = ("Completed", "Refunded", "Cancelled", "Loss")
RUN_LOG_DIR   = os.path.join(_DIR, "Final_Testing_Logs", "runs")
# Process an explicit comma-separated list of Amazon order IDs instead of the
# report (debug/backfill of specific orders). WB is scraped from the SC page.
ORDER_IDS     = [x.strip() for x in _env("CLOSING_ORDER_IDS").replace("\n", ",").split(",") if x.strip()]
BATCH_SIZE    = 30                                      # FedEx bulk limit
DEBUG_DIR     = os.path.join(_DIR, "Final_Testing_Logs", "debug")

# Per-account ports + profile. Report + SC scraping reuse one Chrome; FedEx its own.
# Both lists live in config/accounts.json (single source of truth) — edit THERE.
ACCOUNTS = _cfg_accounts()
GMAIL_TOKENS = _cfg_gmail_tokens()
# Live-Gmail preference tiers for THIS store (archive → preferred inbox(es) →
# every inbox). HOANK: qurtdis / salesmercentile1+inhur; Hathoro: abecomllc26 /
# salesmercentile+wrenchlyllc — from config/accounts.json.
PREF = preferred_inboxes(ACCOUNT)
# How long a headless/cron run waits for you to log into SC before giving up
# (interactive runs still wait for Enter). Prevents the old infinite signin
# loop that burned the whole daily_bulk closing timeout doing nothing.
LOGIN_WAIT_MINS = _env_int("CLOSING_LOGIN_WAIT_MINS", 5)
# ── Report-diff ingest (26 Sep 2026) ────────────────────────────────────────
# Resume only ever skipped terminal labels, and with delivery unconfirmed nothing became
# terminal — so every run re-enriched the whole 30-day window (10,378 HOANK orders on
# 26 Sep, every row rewritten, ~380k audit rows appended). With DIFF on, an order gets a
# pass only when it is new to closing_orders, its report row changed, new email evidence
# arrived since its last pass, the placer/tracker know something the row lacks, or a
# core field is still missing and live Gmail may retry (at most every GAP_RETRY_HOURS).
DIFF          = _env_bool("CLOSING_DIFF", True)
ENRICH_LOG    = _env_bool("CLOSING_ENRICH_LOG", False)   # enrichment_log rows are opt-in now
EVIDENCE_MARGIN_H = _env_int("CLOSING_EVIDENCE_MARGIN_H", 2)
GAP_RETRY_HOURS   = _env_int("CLOSING_GAP_RETRY_HOURS", 24)

CLOSING_PROFILE_ROOT = os.path.expanduser("~/.closing_chrome_profiles")
# Per-account so HOANK and Hathoro can run in parallel without download_report's
# ".txt wipe" clobbering each other's freshly-downloaded report.
REPORTS_DIR = os.path.join(_DIR, "data", "reports", ACCOUNT)
def _empty(v):
    """Emptiness for gap-fill: None / '' / 'None' are empty; a numeric 0 or 0.0
    is a REAL value (zoro_tax, coupon_savings, feedback_rating can legitimately be 0)."""
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == "" or v.strip() == "None"
    return False


# ═══════════════════════════════════════════════════════════════════════════
#  Coercion helpers + report parsing  (folded from closing_runner)
# ═══════════════════════════════════════════════════════════════════════════
def _s(v):
    return str(v).strip() if v is not None else ""

def _fl(v):
    if not v or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return None

def _int(v):
    if not v or str(v).strip() == "":
        return 0
    try:
        return int(float(str(v).replace(",", "")))
    except (ValueError, TypeError):
        return 0

def _safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

# ── Timezones (Amazon US = US/Pacific for order dates; our inbox = PKT) ───────
try:
    from zoneinfo import ZoneInfo
    _TZ_PT = ZoneInfo("America/Los_Angeles")
    _TZ_PK = ZoneInfo("Asia/Karachi")
except Exception:                      # fallback: fixed offsets (PKT has no DST)
    from datetime import timezone, timedelta
    _TZ_PT = timezone(timedelta(hours=-7))
    _TZ_PK = timezone(timedelta(hours=5))

from datetime import timezone as _tz, timedelta as _td

_DATE_IN = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%a, %b %d, %Y, %I:%M %p", "%A, %b %d, %Y, %I:%M %p",  # SC purchase datetime
            "%m/%d/%Y %I:%M %p", "%m/%d/%Y",
            "%a, %b %d, %Y", "%A, %b %d, %Y", "%A, %m/%d/%Y",
            "%b %d, %Y", "%Y-%m-%d")


def _parse_any(s):
    """Parse our many date/timestamp string formats → naive/aware datetime, or None."""
    if not s:
        return None
    s = str(s).strip()
    s = re.sub(r"\s+(PST|PDT|EST|EDT|CST|CDT|MST|MDT|UTC|GMT)\b.*$", "", s).strip()
    for f in _DATE_IN:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _fmt_dt(d):
    """datetime → 'YYYY-MM-DD HH:MM' when it carries a real time, else 'YYYY-MM-DD'.
    Never includes a weekday name."""
    return d.strftime("%Y-%m-%d %H:%M") if (d.hour or d.minute) else d.strftime("%Y-%m-%d")


def amazon_order_date(report_utc=None, archive_order_date=None):
    """Real Amazon order date/time in US/Pacific (Amazon's own timezone), e.g.
    '2026-05-19 18:41'. Report purchase-date is UTC → convert; the email-body
    'Order date' is already Amazon-local."""
    d = _parse_any(report_utc)
    if d:
        if d.tzinfo is None:
            d = d.replace(tzinfo=_tz.utc)
        return _fmt_dt(d.astimezone(_TZ_PT))
    d = _parse_any(archive_order_date)
    return _fmt_dt(d) if d else ""


def norm_date(s, range_last=False):
    """Any date/datetime string → synchronized 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'
    (keeps the time when present, never a weekday). For an Amazon delivery WINDOW
    ('… to …') take the deadline end when range_last=True."""
    if not s:
        return s or ""
    s = str(s)
    if re.search(r"\s+to\s+", s, re.I):
        parts = re.split(r"\s+to\s+", s, flags=re.I)
        s = parts[-1] if range_last else parts[0]
    d = _parse_any(s)
    return _fmt_dt(d) if d else s


def norm_pkt(s):
    """A PKT email timestamp → 'YYYY-MM-DD HH:MM' (already PKT, just tidy)."""
    d = _parse_any(s)
    return d.strftime("%Y-%m-%d %H:%M") if d else (s or "")


def duration(start_s, end_s):
    """(human 'Xh Ym', hours_float) between two PKT timestamps; (\"\", None) if bad/negative."""
    a, b = _parse_any(start_s), _parse_any(end_s)
    if not a or not b:
        return "", None
    secs = (b - a).total_seconds()
    if secs < 0 or secs > 90 * 86400:
        return "", None
    h = secs / 3600
    return (f"{int(secs // 3600)}h {int((secs % 3600) // 60)}m", round(h, 2))

def _join_json_list(val):
    """'["G123", ""]' → 'G123'; '[""]'/'[]'/None → ''; plain strings pass through."""
    if not val:
        return ""
    s = str(val).strip()
    if s.startswith("["):
        try:
            import json
            return ", ".join(str(x).strip() for x in json.loads(s) if str(x).strip())
        except Exception:
            return ""
    return s

def _extract_wb_ids(merchant_order_id):
    # WB = Website Order ID (bot-placed); SO = Sales Order ID (placed by phone call to Zoro).
    # Both are real placed Zoro orders with cost — treat them identically everywhere.
    if not merchant_order_id:
        return []
    return re.findall(r"(?:WB|SO)\d+", str(merchant_order_id).upper())

def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def parse_report_tsv(report_path):
    if not os.path.exists(report_path):
        print(f"[CLOSING] ❌ Report not found: {report_path}")
        return []
    rows = []
    with open(report_path, "r", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            rows.append(r)
    print(f"[CLOSING] Report parsed: {len(rows)} total rows")
    return rows


def filter_wb_orders(raw_rows):
    out = []
    for r in raw_rows:
        moid = _s(r.get("merchant-order-id"))
        wb = _extract_wb_ids(moid)
        if not wb:
            continue
        out.append({
            "amazon_order_id": _s(r.get("amazon-order-id")),
            "account": ACCOUNT,                       # the report is account-specific
            "merchant_order_id": moid,
            "order_status": _s(r.get("order-status")),
            "sku": _s(r.get("sku")),
            "asin": _s(r.get("asin")),
            "quantity": _int(r.get("quantity")),
            "title": _s(r.get("product-name")),
            "item_price": _fl(r.get("item-price")),
            "shipping_price": _fl(r.get("shipping-price")),
            # report purchase-date is UTC; store the real Amazon (US/Pacific) date
            "purchase_date": amazon_order_date(report_utc=_s(r.get("purchase-date"))),
            "ship_by_date": _s(r.get("earliest-ship-date") or r.get("ship-by-date")),
            "deliver_by_date": _s(r.get("latest-delivery-date") or r.get("deliver-by-date")),
            "ship_city": _s(r.get("ship-city")),
            "ship_state": _s(r.get("ship-state")),
            "ship_postal_code": _s(r.get("ship-postal-code")),
            "ship_country": _s(r.get("ship-country")) or "US",
            "is_business": "Yes" if _s(r.get("is-business-order")).lower() == "true" else "No",
            "wb_order_id": wb[0],
        })
    print(f"[CLOSING] WB orders (raw): {len(out)}")
    return out


def consolidate_multi_item_orders(rows):
    merged = {}
    for row in rows:
        oid = row["amazon_order_id"]
        if oid not in merged:
            merged[oid] = dict(row)
            continue
        e = merged[oid]
        e["quantity"] = (e.get("quantity") or 0) + (row.get("quantity") or 0)
        e["item_price"] = round((e.get("item_price") or 0.0) + (row.get("item_price") or 0.0), 2)
        e["shipping_price"] = round((e.get("shipping_price") or 0.0) + (row.get("shipping_price") or 0.0), 2)
        if row.get("sku") and row["sku"] not in (e.get("sku") or ""):
            e["sku"] = f"{e.get('sku', '')}, {row['sku']}".strip(", ")
        if row.get("asin") and row["asin"] not in (e.get("asin") or ""):
            e["asin"] = f"{e.get('asin', '')}, {row['asin']}".strip(", ")
    out = list(merged.values())
    if len(out) < len(rows):
        print(f"[CLOSING] Consolidated: {len(rows)} raw → {len(out)} unique orders")
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  Chrome + report download  (folded from closing_runner)
# ═══════════════════════════════════════════════════════════════════════════
def _launch_chrome(port, profile_dir=None, headless=False):
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not os.path.exists(chrome):
        return False
    cmd = [chrome, f"--remote-debugging-port={port}", "--no-first-run",
           "--no-default-browser-check", "--start-maximized",
           "--hide-crash-restore-bubble"]
    if headless:
        cmd.extend(["--headless=new", "--window-size=1920,1080", "--disable-gpu"])
    try:
        from utils.helpers import CHROME_SOFT_FLAGS
        cmd.extend(CHROME_SOFT_FLAGS)
    except Exception:
        pass
    if profile_dir:
        os.makedirs(profile_dir, exist_ok=True)
        try:
            from utils.helpers import mark_profile_clean_exit
            mark_profile_clean_exit(profile_dir)
        except Exception:
            pass
        cmd.append(f"--user-data-dir={profile_dir}")
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(4)
        return True
    except Exception:
        return False


def _kill_stale_chrome(port):
    """Kill ONLY a Chrome bound to this exact debugging port (never the user's main Chrome), and
    wait until it is really gone: a new Chrome started on the same profile while the old one is
    still shutting down hands itself over to the dying one and never opens the port (17 Oct
    2026, 21:34 — messaging for both stores: "would not start after 4 tries")."""
    try:
        subprocess.run(["pkill", "-f", f"--remote-debugging-port={port}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(1)
            if subprocess.run(["pgrep", "-f", f"--remote-debugging-port={port}"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
                break
        time.sleep(1)
    except Exception:
        pass


def ensure_page_target(port):
    """A Chrome whose pages were all closed stays up with NO page target — headless Chrome does
    not exit when its last tab closes, and quitting an attached driver closes every tab. Then
    chromedriver cannot attach ("unable to discover open pages") and burns ~2 minutes failing.
    Open a blank tab through DevTools first. Returns True when Chrome answers on the port."""
    import json as _json
    import urllib.request
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{base}/json/list", timeout=3) as r:
            targets = _json.loads(r.read().decode() or "[]")
    except Exception:
        return False
    if any(t.get("type") == "page" for t in targets):
        return True
    try:
        req = urllib.request.Request(f"{base}/json/new?about:blank", method="PUT")
        urllib.request.urlopen(req, timeout=5).read()
        print(f"[CLOSING] Chrome on {port} had no open page — opened a blank tab to attach to")
        time.sleep(1)
    except Exception as e:
        print(f"[CLOSING] ⚠️ could not open a tab in Chrome on {port}: {type(e).__name__}")
    return True


def _click_retry(driver, el, what, tries=6, pause=2.0):
    """Click, and if something is drawn over it, wait and click again.

    Report Central keeps a #loading-box-style overlay over the whole form while it loads, and
    Selenium's element_to_be_clickable does not look at what is painted on top — so a click a
    moment too early raises ElementClickInterceptedException and kills the run. Seen 18 Sep
    2026 one second after a login: "Other element would receive the click:
    <div id='loading-box-style'>". The overlay clears on its own, so retrying is the whole fix.
    """
    from selenium.common.exceptions import ElementClickInterceptedException
    for attempt in range(1, tries + 1):
        try:
            el.click()
            return True
        except ElementClickInterceptedException:
            if attempt == tries:
                raise
            print(f"[CLOSING]   ⏳ {what} covered (page still loading) — retry {attempt}/{tries - 1}")
            time.sleep(pause)
    return False


def ensure_chrome(port, tries=8, headless=None):
    """Connect to Chrome on `port`; if absent, kill any stale instance, launch a
    dedicated closing profile, and retry the connect several times (the first
    launch often isn't ready yet → 'unable to connect to renderer').

    Tags the driver with `_closing_launched`: True only when WE started this
    Chrome. release_chrome() then quits only our own instances — never the
    user's manually-logged-in headed Chrome we merely attached to.

    headless=None means "ask the closing switch" (ZUHRA_HEADLESS_CLOSING /
    bot_config chrome.headless_closing). That is what carries the whole chain — report
    download, Seller Central scrape, messaging and review requests — into headless
    together, since the later steps only ATTACH to whatever this launched. Pass True or
    False to force it, as the FedEx pass does."""
    if headless is None:
        try:
            from utils.helpers import headless_wanted
            headless = headless_wanted("closing")
        except Exception:
            headless = False
    from engine.amazon_scraper import _connect_chrome
    # Only try to attach if something is actually there. Asking chromedriver to connect to an
    # empty port costs ~2 minutes across its three fallbacks before admitting defeat.
    try:
        from utils.proc import port_open
        occupied = port_open(port)
    except Exception:
        occupied = True                    # cannot tell — attempt the attach as before
    if occupied:
        ensure_page_target(port)
    d = _connect_chrome(port) if occupied else None
    if d:
        d._closing_launched = False        # attached to an existing (logged-in) Chrome
        return d

    profile_dir = os.path.join(CLOSING_PROFILE_ROOT, f"port_{port}")
    os.makedirs(profile_dir, exist_ok=True)
    _kill_stale_chrome(port)
    for lock in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            os.remove(os.path.join(profile_dir, lock))
        except OSError:
            pass
    mode_str = "headless " if headless else ""
    print(f"[CLOSING] Launching dedicated {mode_str}Chrome (port {port}, profile {profile_dir})...")
    _launch_chrome(port, profile_dir, headless=headless)
    for attempt in range(1, tries + 1):
        time.sleep(3)
        d = _connect_chrome(port)
        if d:
            d._closing_launched = True     # ours → safe to quit at the end
            d._closing_headless = headless  # whether THIS browser has a window to log into
            try:
                from utils.helpers import apply_cdp_stealth
                apply_cdp_stealth(d)
            except Exception:
                pass
            return d
        print(f"[CLOSING]   ...waiting for Chrome on {port} ({attempt}/{tries})")
    print(f"[CLOSING] ❌ Chrome on port {port} would not start after {tries} tries")
    return None


def release_chrome(d):
    """Quit a Chrome WE launched; for an attached one, only stop the local
    chromedriver process (leaves the user's logged-in browser untouched — the
    old unconditional quit() could close it and break the later SC scrape)."""
    if not d:
        return
    try:
        if getattr(d, "_closing_launched", False):
            d.quit()
        else:
            d.service.stop()
    except Exception:
        pass


def download_report(port, days=30, start_date=None, end_date=None):
    """Download the FlatFileAllOrdersReport while holding this store's Seller Central Chrome
    (utils/sc_chrome.py): the tracking uploader runs all day on the same Chrome, and a download
    that shared the tab with one of its cycles halted both (28 Sep 2026 — auto-closing at 04:00
    next to the trackers). Waits up to 20 min for the tracker's current cycle, then goes ahead."""
    from utils.sc_chrome import sc_turn
    with sc_turn(port, f"closing {ACCOUNT} report", max_wait=1200):
        return _download_report_inner(port, days=days, start_date=start_date, end_date=end_date)


def _download_report_inner(port, days=30, start_date=None, end_date=None):
    """Download the FlatFileAllOrdersReport. Last `days` (dropdown maxes at 30),
    or an exact start→end range (needed for backfills longer than 30 days)."""
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    if start_date and end_date:
        print(f"[CLOSING] Downloading report {start_date} → {end_date} (port {port})...")
    else:
        print(f"[CLOSING] Downloading {days}-day report (port {port})...")
    driver = ensure_chrome(port)
    if not driver:
        print(f"[CLOSING] ❌ Could not connect to Chrome on port {port}")
        return None

    os.makedirs(REPORTS_DIR, exist_ok=True)
    for f in os.listdir(REPORTS_DIR):
        if f.endswith(".txt"):
            try:
                os.remove(os.path.join(REPORTS_DIR, f))
            except OSError:
                pass
    dl_dir = REPORTS_DIR
    try:
        driver.execute_cdp_cmd("Browser.setDownloadBehavior",
                               {"behavior": "allow", "downloadPath": dl_dir})
    except Exception as e:
        print(f"[CLOSING] ⚠️ Could not redirect downloads to {dl_dir}: {e}")
        dl_dir = os.path.expanduser("~/Downloads")

    url = "https://sellercentral.amazon.com/reportcentral/FlatFileAllOrdersReport/1"
    started = time.time()
    try:
        driver.get(url)
        time.sleep(3)
        print(f"[CLOSING]   Current URL: {driver.current_url}")

        # Log in the way the tracker already does — same credentials, same OTP path
        # (platforms.auth.auto_login, which tracking.py has used for every report download).
        # Closing used to just wait for a human, which is why it could not run unattended and
        # why a headless run had nothing to offer. The manual wait below is now only the
        # fallback for when auto-login itself fails.
        if "signin" in (driver.current_url or "").lower():
            try:
                from platforms.auth import auto_login
                print(f"[CLOSING] 🔐 Login required — auto-logging in as {ACCOUNT}...")
                if auto_login(driver, ACCOUNT):
                    driver.get(url)
                    time.sleep(3)
                    print(f"[CLOSING] ✅ Auto-login OK — back on {driver.current_url[:60]}")
                else:
                    print("[CLOSING] ⚠️ Auto-login did not complete")
            except Exception as _e:
                print(f"[CLOSING] ⚠️ Auto-login unavailable ({type(_e).__name__}: {str(_e)[:70]})")

        # Signed in but no account chosen → Amazon serves "Select an account" instead of the
        # report page, and every selector after it times out. Choose ours and carry on.
        try:
            from platforms.auth import on_account_switcher, pick_account
            if on_account_switcher(driver):
                _acfg = ACCOUNTS.get(ACCOUNT, {})
                if not pick_account(driver, _acfg.get("sc_store_name"),
                                    _acfg.get("sc_marketplace"), target_url=url):
                    print(f"[CLOSING] ❌ Could not get past the account switcher for {ACCOUNT}.")
                    return None
        except Exception as _sw_err:
            print(f"[CLOSING] ⚠️ Account-switcher check failed "
                  f"({type(_sw_err).__name__}: {str(_sw_err)[:70]}) — continuing")

        login_waited = 0
        # If auto-login could not get us in, headless has no window for a human to use, so
        # waiting is pointless: say what actually fixes it instead of burning LOGIN_WAIT_MINS.
        _headless_run = getattr(driver, "_closing_headless", None)
        if _headless_run is None:
            try:
                from utils.helpers import headless_wanted
                _headless_run = headless_wanted("closing")
            except Exception:
                _headless_run = False
        while "signin" in driver.current_url.lower() or "ap/signin" in driver.current_url:
            if _headless_run:
                print("=" * 60)
                print(f"[CLOSING] 🔑 SC LOGIN FAILED on port {port} — auto-login could not get in "
                      f"and this run is HEADLESS, so there is no window for you to use.")
                print(f"[CLOSING]    Check the {ACCOUNT} credentials and OTP secret the tracker uses")
                print("[CLOSING]    (platforms/auth.py), or turn OFF 'Headless (closing)', press")
                print("[CLOSING]    🚀 Chrome and log in by hand once — the session persists in the")
                print("[CLOSING]    profile, so it only has to be done again when it expires.")
                print("=" * 60)
                return None
            if sys.stdin.isatty():
                print("=" * 60)
                print(f"[CLOSING] 🔑 LOGIN REQUIRED — a Chrome window is open on port {port}.")
                print("[CLOSING]    Log into Amazon Seller Central in that window.")
                try:
                    input("[CLOSING]    Press Enter here once you're logged in... ")
                except EOFError:
                    pass
            else:
                # Non-interactive (daily_bulk / cron): nobody is coming to log in.
                # Cap the wait so an expired session fails FAST and LOUD instead of
                # silently burning the whole step timeout on the signin page.
                if login_waited >= LOGIN_WAIT_MINS * 60:
                    print("=" * 60)
                    print(f"[CLOSING] 🚨 SC LOGIN EXPIRED on port {port} — waited "
                          f"{LOGIN_WAIT_MINS} min with no login. Log into Seller Central "
                          f"in the Chrome on port {port} and re-run.")
                    print("=" * 60)
                    return None
                print(f"[CLOSING] ⚠️ LOGIN REQUIRED — log into Amazon SC on port {port}; "
                      f"retrying in 10s ({login_waited}s/{LOGIN_WAIT_MINS * 60}s)")
                time.sleep(10)
                login_waited += 10
            driver.get(url)
            time.sleep(3)

        # ── Manual mode: you set the dates + download yourself (the auto date-picker
        #    is unreliable since Amazon changed the page). closing just waits for the
        #    file to land in dl_dir (downloads are redirected there via CDP) and uses it.
        if MANUAL_REPORT:
            print("=" * 60)
            print("[CLOSING] 🖐  MANUAL REPORT MODE — do the download yourself in the open Chrome:")
            print("[CLOSING]    1) pick your date range   2) Request Download   3) wait, then Download")
            print(f"[CLOSING]    (the file auto-saves to: {dl_dir})")
            if sys.stdin.isatty():
                try:
                    input("[CLOSING]    ⏎  Press Enter here AFTER the file has finished downloading... ")
                except EOFError:
                    pass
            waited = 0
            while waited < 240:
                files = [os.path.join(dl_dir, f) for f in os.listdir(dl_dir) if f.endswith(".txt")]
                files = [f for f in files if os.path.getctime(f) >= started]
                if files:
                    rp = max(files, key=os.path.getctime)
                    s1 = os.path.getsize(rp); time.sleep(2)
                    if os.path.getsize(rp) == s1 and s1 > 0:   # size stable = download finished
                        print(f"[CLOSING] ✅ Using your downloaded report: {os.path.basename(rp)} ({s1} bytes)")
                        return rp
                print(f"[CLOSING]    ...waiting for the .txt to appear in {os.path.basename(dl_dir)} ({waited}s)")
                time.sleep(10); waited += 10
            print("[CLOSING] ❌ No report .txt appeared — did the download finish?")
            return None

        # 45 s, not 15: a freshly launched headless Chrome renders Report Central's Katal widgets
        # slowly, and the backfill of 28 Sep 2026 timed out on a page that was merely loading.
        dropdown = WebDriverWait(driver, 45).until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "kat-dropdown.daily-time-picker-kat-dropdown-normal")))
        wait = WebDriverWait(driver, 15)
        # kat-dropdown options live in shadow DOM — click via shadowRoot or the
        # open menu intercepts the Request Download click.
        if start_date and end_date:
            print(f"[CLOSING]   Selecting Exact dates {start_date} → {end_date}...")
            _click_retry(driver, dropdown, "date dropdown"); time.sleep(1)
            driver.execute_script(
                "arguments[0].shadowRoot.querySelector('kat-option[value=\"-1\"]').click()", dropdown)
            time.sleep(2)
            # Since 2026 the page has ONE <kat-date-range-picker> (dates as M/D/YYYY, en-US) whose
            # two <kat-date-picker> boxes live in its shadow DOM. Each box's emitChangeEvent(value)
            # is what typing a date calls; the range picker listens to it and updates its own
            # startValue / endValue, which the page reads on Request Download. The old code set
            # .value on any <input> whose label mentioned "date" — on this page those are the
            # Order Date / Last Updated Date radios, so it silently requested "orders updated
            # today" (28 Sep 2026: asked for August, got 69 orders). Wrong range = no request.
            def _mdy(s):
                s = str(s).strip()
                try:
                    d0 = datetime.strptime(s[:10], "%Y-%m-%d")
                    return f"{d0.month}/{d0.day}/{d0.year}"
                except ValueError:
                    return s
            want_s, want_e = _mdy(start_date), _mdy(end_date)
            verify = driver.execute_script("""
                var s = arguments[0], e = arguments[1];
                var od = document.querySelector('input[name="report-view-options-radio"][aria-label="Order Date"]');
                if (od && !od.checked) od.click();
                var rp = document.querySelector('kat-date-range-picker');
                if (!rp) return JSON.stringify({ok: false, why: 'no kat-date-range-picker on the page'});
                var sp = rp.startPicker || (rp.shadowRoot && rp.shadowRoot.querySelector('kat-date-picker.start'));
                var ep = rp.endPicker || (rp.shadowRoot && rp.shadowRoot.querySelector('kat-date-picker.end'));
                if (!sp || !ep || !sp.emitChangeEvent || !ep.emitChangeEvent)
                    return JSON.stringify({ok: false, why: 'date boxes not found inside the range picker'});
                sp.emitChangeEvent(s); ep.emitChangeEvent(e);
                var dd = document.querySelector('kat-dropdown.daily-time-picker-kat-dropdown-normal');
                return JSON.stringify({ok: rp.startValue === s && rp.endValue === e, start: rp.startValue,
                    end: rp.endValue, dropdown: dd ? dd.value : null, order_date: od ? od.checked : null});
            """, want_s, want_e)
            time.sleep(1)
            print(f"[CLOSING]   date-picker set → {verify}")
            try:
                _ok = bool(json.loads(verify).get("ok"))
            except Exception:
                _ok = False
            if not _ok:
                print("[CLOSING] ❌ Could not set the exact date range — not requesting a report "
                      "for the wrong dates. (Set CLOSING_DEBUG_PICKER=1 to dump the picker.)")
                return None
            if os.environ.get("CLOSING_DEBUG_PICKER"):
                try:
                    dump = driver.execute_script("""
                        var out='=== ALL INPUTS / SELECTS ===\\n';
                        [].slice.call(document.querySelectorAll('input,select')).forEach(function(i){
                          out+=i.tagName+' type='+i.type+' name='+i.name+' id='+i.id+' ph='+i.placeholder+' aria='+i.getAttribute('aria-label')+' val='+i.value+'\\n';});
                        out+='\\n=== kat-date-picker ===\\n';
                        [].slice.call(document.querySelectorAll('kat-date-picker')).forEach(function(e){out+=e.outerHTML+'\\n||SHADOW||'+(e.shadowRoot?e.shadowRoot.innerHTML:'')+'\\n\\n';});
                        out+='\\n=== date-ish elements ===\\n';
                        [].slice.call(document.querySelectorAll('[class*=date i],[class*=picker i],[data-testid*=date i],kat-date-range-picker')).slice(0,25).forEach(function(e){out+=e.tagName+' class='+e.className+'\\n'+e.outerHTML.slice(0,400)+'\\n\\n';});
                        return out;
                    """)
                    open("/tmp/datepicker_dump.html", "w").write(dump or "")
                    print("[CLOSING]   📋 picker DOM dumped → /tmp/datepicker_dump.html")
                except Exception as _e:
                    print(f"[CLOSING]   picker dump failed: {_e}")
        else:
            print(f"[CLOSING]   Selecting last {days} days...")
            _click_retry(driver, dropdown, "date dropdown"); time.sleep(1)
            driver.execute_script(
                "arguments[0].shadowRoot.querySelector('kat-option[value=\"%d\"]').click()" % days,
                dropdown)
            time.sleep(1)

        print("[CLOSING]   Clicking Request Download...")
        _click_retry(driver, wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'kat-button[label="Request Download"]'))), "Request Download")
        print("[CLOSING]   ✓ Report requested — polling every 30s (max 10 min)...")

        waited, report_path = 0, None
        while waited < 600:
            time.sleep(30)
            waited += 30
            driver.refresh()
            time.sleep(5)
            try:
                clicked = driver.execute_script("""
                    var rows = document.querySelectorAll('kat-table-row[role="row"]');
                    for (var i = 0; i < rows.length; i++) {
                        if (rows[i].classList.contains('table-header-style')) continue;
                        if (rows[i].querySelector('[role="columnheader"]')) continue;
                        var btn = rows[i].querySelector('kat-button[label="Download"]');
                        if (btn) { btn.click(); return true; }
                        return false;
                    }
                    return false;
                """)
                if not clicked:
                    print(f"[CLOSING]   ...report still generating ({waited}s)")
                    continue
                print("[CLOSING]   ✓ Download clicked — waiting for file...")
                time.sleep(15)
                files = [os.path.join(dl_dir, f) for f in os.listdir(dl_dir)
                         if f.endswith(".txt")
                         and os.path.getctime(os.path.join(dl_dir, f)) >= started]
                if files:
                    report_path = max(files, key=os.path.getctime)
                    print(f"[CLOSING] ✅ Report downloaded: {os.path.basename(report_path)}")
                    break
            except Exception as e:
                print(f"[CLOSING]   ...still waiting ({waited}s): {e}")
        if not report_path:
            print("[CLOSING] ❌ Report download timeout")
        return report_path
    except Exception as e:
        print(f"[CLOSING] ❌ Download error: {type(e).__name__}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  Enrichment: placer-log → archive → live Gmail → optional scrape
# ═══════════════════════════════════════════════════════════════════════════
def _log_enrich(run_id, amazon_id, source, field, old, new, confidence=1.0):
    """enrichment_log rows are opt-in (CLOSING_ENRICH_LOG=1) since 26 Sep 2026: the table had
    2.76 M rows that nothing reads, grew ~380k per run day, and every row cost its own SQLite
    connection. Provenance for a run is still in Final_Testing_Logs/runs/<run>.jsonl."""
    if not ENRICH_LOG:
        return
    try:
        log_enrichment(run_id, amazon_id, source, field, old, new, confidence=confidence)
    except Exception:
        pass


def enrich_from_archive(conn, amazon_id, wb_id, run_id):
    """Gap-fill from the archive_* mirror tables. Returns {field: value}."""
    enriched, flags = {}, {"enriched_from_archive": 0}

    row = conn.execute(
        "SELECT * FROM archive_earnings WHERE amazon_order_id = ?", (amazon_id,)
    ).fetchone()
    if row:
        flags["enriched_from_archive"] = 1
        mapping = {
            "earnings": "total_your_earnings", "amazon_fees": "total_amazon_fees",
            "shipping_amt": "total_shipping", "ship_by_date": "ship_by_date",
            "purchase_date": "order_date", "sold_email_time": "email_timestamp",
            "sku": "skus", "asin": "asins", "quantity": "num_items",
        }
        for dest, src in mapping.items():
            val = row[src]
            if dest in ("sku", "asin"):
                val = _join_json_list(val)
            elif dest == "quantity" and not val:   # archive num_items is often a bogus 0
                val = None
            elif dest == "purchase_date":           # email-body 'Order date' = real Amazon date
                val = amazon_order_date(archive_order_date=val)
            if val not in (None, ""):
                enriched[dest] = val
                _log_enrich(run_id, amazon_id, "archive_earnings", dest, None, val, confidence=0.9)

    if wb_id:
        row = conn.execute(
            "SELECT * FROM archive_zoro WHERE wb_order_id = ? ORDER BY email_timestamp DESC LIMIT 1",
            (wb_id,)).fetchone()
        if row:
            flags["enriched_from_archive"] = 1
            mapping = {
                "zoro_account_name": "zoro_account_name", "zoro_gmail_account": "gmail_account",
                "zoro_po_number": "po_number", "zoro_order_total": "order_total",
                "zoro_tax": "tax", "zoro_sku": "sku", "zoro_qty": "qty",
                "zoro_buyer_zip": "ship_zip", "zoro_email_ts": "email_timestamp",
                "zoro_order_date": "order_date", "zoro_payment_card": "payment_card_last4",
                # NOTE: archive coupon_savings is always 0 (parser never captured it) →
                # we extract the real promo from raw_body_snippet below instead.
                "zoro_subtotal": "subtotal", "zoro_shipping": "shipping_cost",
                "zoro_payment_method": "payment_method",
            }
            for dest, src in mapping.items():
                val = row[src]
                if val not in (None, ""):
                    enriched[dest] = val
                    _log_enrich(run_id, amazon_id, "archive_zoro", dest, None, val, confidence=0.9)
            # Coupon / promo and Zoro's own item description. Since 26 Sep 2026 the archive
            # sync derives these into columns (zoro_promo_savings / zoro_promo_label /
            # zoro_item_desc) so the unified DB no longer needs the raw email body; rows
            # synced before that are parsed from the body when it is still present.
            zr = dict(row)
            cs, clabel = zr.get("zoro_promo_savings"), zr.get("zoro_promo_label")
            if cs in (None, "") and zr.get("raw_body_snippet"):
                cs, clabel = _CS.extract_zoro_promo(zr.get("raw_body_snippet"))
            if cs:
                enriched["coupon_savings"] = cs
                enriched["coupon_used"] = clabel or "Promo applied"
                _log_enrich(run_id, amazon_id, "archive_zoro", "coupon_savings", None, cs, confidence=0.9)
            zdesc = zr.get("zoro_item_desc") or (
                _CS.extract_zoro_item(zr.get("raw_body_snippet")) if zr.get("raw_body_snippet") else "")
            if zdesc:
                enriched["zoro_item_desc"] = zdesc

        row = conn.execute(
            "SELECT * FROM archive_tracking WHERE wb_order_id = ? ORDER BY email_timestamp DESC LIMIT 1",
            (wb_id,)).fetchone()
        if row:
            flags["enriched_from_archive"] = 1
            for dest, src in {"tracking_ids": "tracking_ids", "carrier": "carrier",
                              "shipped_email_time": "email_timestamp"}.items():
                val = row[src]
                if val not in (None, ""):
                    enriched[dest] = val
                    _log_enrich(run_id, amazon_id, "archive_tracking", dest, None, val, confidence=0.9)
            # Zoro's own delivery estimate, stated in every shipping email. It is an
            # expectation, not a delivery — so it goes to zoro_deliver_by, never to
            # delivery_status, and it is what makes "overdue" computable without FedEx.
            # Derived at sync time into zoro_eta since 26 Sep 2026; body parse for older rows.
            tr = dict(row)
            eta = tr.get("zoro_eta") or (
                _CS.extract_zoro_eta(tr.get("raw_body_snippet"), tr.get("email_timestamp"))
                if tr.get("raw_body_snippet") else "")
            if eta:
                enriched["zoro_deliver_by"] = eta
                _log_enrich(run_id, amazon_id, "archive_tracking", "zoro_deliver_by",
                            None, eta, confidence=0.9)

    rows = conn.execute(
        "SELECT * FROM archive_returns WHERE amazon_order_id = ? ORDER BY email_timestamp DESC",
        (amazon_id,)).fetchall()
    if rows:
        flags["enriched_from_archive"] = 1
        enriched["return_requested"] = "Yes"
        for r in rows:
            rd = dict(r)
            body = (rd.get("raw_body_snippet") or "").lower()
            if rd.get("refund_done") == "Yes" or rd.get("email_type") == "refund_initiated" \
                    or "refund initiated" in body or "refund of" in body:
                enriched["refund_done"] = "Yes"
                enriched["refund_amount"] = rd.get("refund_amount")
                enriched["refund_date"] = rd.get("refund_date")
                enriched["refund_reason"] = rd.get("refund_reason") or rd.get("return_reason")
                _log_enrich(run_id, amazon_id, "archive_returns", "refund_done", None, "Yes", confidence=0.9)
                break

    # customer_messaged = the CUSTOMER wrote to us → inbound rows only
    rows = conn.execute(
        "SELECT * FROM archive_messages WHERE amazon_order_id = ? ORDER BY email_timestamp DESC",
        (amazon_id,)).fetchall()
    inbound = [r for r in rows
               if dict(r).get("direction") == "inbound"
               or dict(r).get("message_type") in ("buyer_inquiry", "buyer_reply")]
    if inbound:
        flags["enriched_from_archive"] = 1
        enriched["customer_messaged"] = "Yes"
        enriched["customer_inquiry_count"] = len(inbound)
        _log_enrich(run_id, amazon_id, "archive_messages", "customer_messaged", None, "Yes", confidence=0.85)

    enriched.update(flags)
    return enriched


# Placer-log columns we trust (Worker A captures these at order placement).
# NOTE: never copy wb_order_id (placer parser leaves it blank) — use the report.
# tracking_id_uploaded / tracking_upload_date / tracking_carrier_uploaded are written by the
# tracker at upload time (26 Sep 2026) — the fact the SC scrape used to re-read per order.
_PLACER_TRUST = (
    "earnings", "amazon_fees", "shipping_amt", "zoro_order_total", "zoro_tax",
    "profit_dollars", "profit_percent", "asin", "sku", "title", "quantity", "zoro_qty",
    "coupon_used", "coupon_savings", "ship_name", "ship_street", "ship_city",
    "ship_state", "ship_postal_code", "ship_phone", "tracking_ids",
    "shipped_email_time", "ship_by_date", "deliver_by_date", "purchase_date",
    "zoro_deliver_by", "zoro_stock",
    "tracking_id_uploaded", "tracking_upload_date", "tracking_carrier_uploaded",
)

# Bulk-loaded copies of closing_orders / order_placer_orders / tracker uploads for this
# run's order ids (None = not preloaded → the per-order queries, as before). Loading the
# rows once replaces ~32,000 connection opens per run.
_EXISTING_ROWS = None
_PLACER_ROWS = None
_UPLOAD_ROWS = None


def _preload(conn, order_ids):
    global _EXISTING_ROWS, _PLACER_ROWS, _UPLOAD_ROWS
    ids = [i for i in dict.fromkeys(order_ids) if i]
    ex, pl = {}, {}
    for i in range(0, len(ids), 800):
        chunk = ids[i:i + 800]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT * FROM closing_orders WHERE amazon_order_id IN ({ph})", chunk):
            ex[r["amazon_order_id"]] = dict(r)
        for r in conn.execute(f"SELECT * FROM order_placer_orders WHERE amazon_order_id IN ({ph})", chunk):
            pl[r["amazon_order_id"]] = dict(r)
    _EXISTING_ROWS, _PLACER_ROWS = ex, pl
    try:
        from utils.unified_db import tracking_uploads_from_action_log
        _UPLOAD_ROWS = tracking_uploads_from_action_log(ids)
    except Exception:
        _UPLOAD_ROWS = {}
    return ex, pl


def placer_lookup(amazon_id):
    """Pull what Worker A already captured at placement. None if not placed."""
    row = _PLACER_ROWS.get(amazon_id) if _PLACER_ROWS is not None else get_placer_order(amazon_id)
    if not row:
        return None
    out = {}
    for k in _PLACER_TRUST:
        if not _empty(row.get(k)):
            out[k] = row[k]
    return out


def _gapfill(order, updates, source, prov):
    """Copy non-empty `updates` into `order` only where `order` is empty.
    Records the winning source per field in `prov` for the verbose log."""
    n = 0
    for k, v in updates.items():
        if k.startswith("enriched_"):
            order[k] = v
            continue
        if _empty(order.get(k)) and not _empty(v):
            order[k] = v
            prov[k] = source
            n += 1
    return n


def _union_leg_tracking(conn, order):
    """Split orders: ONE Amazon order is often fulfilled by 2-4 separate Zoro (WB)
    orders, each with its own FedEx tracking number. merchant_order_id holds ALL the
    WB ids but enrich_from_archive only ever looked up the FIRST (wb_order_id), so the
    sibling legs' tracking never entered tracking_ids and were never FedEx-checked —
    an order could read 'Delivered' off a single leg while siblings were untracked.
    Here we pull tracking for EVERY WB id and union them (order-preserving, deduped),
    so bulk_fedex_check's existing all-legs aggregation sees the complete package set.
    Returns the number of NEW tracking numbers added."""
    wbs, seen_wb = [], set()
    for w in _extract_wb_ids(order.get("merchant_order_id")):
        if w not in seen_wb:
            seen_wb.add(w); wbs.append(w)
    if len(wbs) <= 1:
        return 0
    have, seen = [], set()
    for t in str(order.get("tracking_ids") or "").replace(";", ",").split(","):
        t = t.strip()
        if t and t not in seen:
            seen.add(t); have.append(t)
    before = len(have)
    carrier = order.get("carrier")
    for wb in wbs:
        for row in conn.execute(
                "SELECT tracking_ids, carrier FROM archive_tracking "
                "WHERE wb_order_id=? AND tracking_ids IS NOT NULL AND tracking_ids!='' "
                "ORDER BY email_timestamp DESC", (wb,)):
            for t in str(row[0]).replace(";", ",").split(","):
                t = t.strip()
                if t and t not in seen:
                    seen.add(t); have.append(t)
            if _empty(carrier) and row[1]:
                carrier = row[1]
    if have:
        order["tracking_ids"] = ", ".join(have)
    if carrier and _empty(order.get("carrier")):
        order["carrier"] = carrier
    return len(have) - before


def _sum_leg_costs(conn, order):
    """Split orders (1 Amazon order = 2-4 separate Zoro/WB orders): the Zoro COST is
    PER-LEG, but enrich_from_archive only ever looked up the FIRST WB's order_total
    (and the placer collapses split legs to a single row), so a multi-leg order's
    zoro_order_total UNDERSTATED the true cost → OVERSTATED profit → a money-losing
    split order could be mislabeled Completed. The tracking side was already fixed
    (_union_leg_tracking); this is the cost side.

    Mirror that helper: sum order_total/tax/subtotal/shipping across EVERY WB leg found
    in archive_zoro and OVERRIDE the single-leg values. Summing ≥2 legs is always ≥ the
    single-leg value, so this can only LOWER (never inflate) reported profit — it can't
    newly hide a loss. Returns the number of legs summed (0/1 = nothing changed).
    NOTE: if some legs' Zoro emails aren't archived, the sum is a partial undercount —
    still strictly better than one leg, and the same missing-data class as single-WB gaps."""
    wbs, seen = [], set()
    for w in _extract_wb_ids(order.get("merchant_order_id")):
        if w not in seen:
            seen.add(w); wbs.append(w)
    if len(wbs) <= 1:
        return 0
    tot = tax = sub = ship = 0.0
    found = 0
    for wb in wbs:
        row = conn.execute(
            "SELECT order_total, tax, subtotal, shipping_cost FROM archive_zoro "
            "WHERE wb_order_id=? ORDER BY email_timestamp DESC LIMIT 1", (wb,)).fetchone()
        if not row:
            continue
        ot = _safe_float(row[0])
        if ot is None:
            continue
        found += 1
        tot += ot
        tax += _safe_float(row[1]) or 0.0
        sub += _safe_float(row[2]) or 0.0
        ship += _safe_float(row[3]) or 0.0
    if found <= 1:
        return found        # 0/1 legs priced → leave the existing single-leg value
    order["zoro_order_total"] = round(tot, 2)
    if tax:
        order["zoro_tax"] = round(tax, 2)
    if sub:
        order["zoro_subtotal"] = round(sub, 2)
    if ship:
        order["zoro_shipping"] = round(ship, 2)
    return found


_UPLOAD_LOG = None
def _upload_log_lookup(aid):
    """tracking_id_uploaded + date from the tracking-uploader log
    (data/tracking_upload_log.json, written by the Auto-Tracking uploader). Lets closing
    capture what we uploaded to Amazon WITHOUT the SC scrape — scrape is then feedback-only."""
    global _UPLOAD_LOG
    if _UPLOAD_LOG is None:
        _UPLOAD_LOG = {}
        try:
            import json
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tracking_upload_log.json")
            for e in json.load(open(p)):
                tids = [t for t in (e.get("tracking_ids") or []) if t]
                a = e.get("amazon_order_id")
                if tids and a:
                    _UPLOAD_LOG[a] = {"tracking_id_uploaded": ", ".join(tids),
                                      "tracking_upload_date": e.get("timestamp", "")}   # latest wins (log is chronological)
        except Exception:
            _UPLOAD_LOG = {}
    return _UPLOAD_LOG.get(aid)


def enrich_one(conn, order, run_id, gmail_ok=True, scraper=None):
    """placer → archive → live-Gmail (per-field) → optional scrape. Read-only on `conn`.
    Precedence (high→low): report row → placer → archive → live Gmail → SC scrape →
    EXISTING closing_orders row. The existing row is seeded LAST/lowest so a weaker
    re-run only ever improves a record — it can never erase data a prior run captured."""
    aid = order["amazon_order_id"]
    wb = order.get("wb_order_id", "")
    prov = {k: "report" for k in order if not _empty(order.get(k))}   # report = the TSV row
    existing = _EXISTING_ROWS.get(aid) if _EXISTING_ROWS is not None else get_closing_order(aid)
    order["_existing"] = existing          # persist_batch skips the write when nothing changed
    # Tag with the account whose report this order came from — authoritative.
    # (Previously this PRESERVED the existing value, but account was never set
    #  from CLOSING_ACCOUNT, so the schema DEFAULT 'HOANK' mislabeled every order.)
    order["account"] = ACCOUNT

    # 1) PLACER LOG FIRST — gap-fill (report row already populated most fields)
    pl = placer_lookup(aid)
    if pl:
        _gapfill(order, pl, "placer_db", prov)
        order["enriched_from_placer_db"] = 1

    # 2a) ARCHIVE — gap-fill
    arc = enrich_from_archive(conn, aid, wb, run_id)
    _gapfill(order, arc, "archive", prov)

    # 2b) LIVE GMAIL — per missing field group only
    if gmail_ok:
        needed = set()
        if _empty(order.get("earnings")):         needed.add("earnings")
        if _empty(order.get("zoro_order_total")): needed.add("zoro")
        if _empty(order.get("tracking_ids")):     needed.add("tracking")
        if order.get("return_requested") != "Yes" or order.get("refund_done") != "Yes":
            needed.add("returns")
        if order.get("customer_messaged") != "Yes":
            needed.add("messages")
        if needed:
            try:
                g = search_order_in_gmail(aid, needed=needed,
                                          preferred=PREF["earnings"] + PREF["zoro"])
                g = {k: v for k, v in g.items() if not k.startswith("_")}
                if _gapfill(order, g, "live_gmail", prov):
                    order["enriched_from_live_gmail"] = 1
            except Exception as e:
                print(f"[CLOSING]   {aid}: live Gmail error — {e}")

    # 2b-tracking) tracking_id_uploaded from the tracker's own record (action_log, written at
    # upload time since 26 Sep 2026), then the older JSON upload log → no SC scrape needed
    # for tracking (gap-fill). With this, SC scrape is feedback-only.
    if _UPLOAD_ROWS:
        ua = _UPLOAD_ROWS.get(aid)
        if ua:
            _gapfill(order, {k: v for k, v in ua.items() if not k.startswith("_")}, "action_log", prov)
    ul = _upload_log_lookup(aid)
    if ul:
        _gapfill(order, ul, "upload_log", prov)

    # 2c) OPTIONAL SC SCRAPE — feedback (+ deliver_by_date) source; tracking_id_uploaded now
    # comes from the uploader log above, so scrape is no longer needed just for tracking.
    # Still a last resort for earnings/ship_by.
    if scraper is not None and any(_empty(order.get(k)) for k in
                                   ("deliver_by_date", "tracking_id_uploaded", "feedback_rating",
                                    "earnings", "ship_by_date")):
        try:
            sc = scraper.scrape_order(aid)
            keep = ("ship_by_date", "deliver_by_date", "purchase_date", "asin", "sku",
                    "tracking_id_uploaded", "tracking_upload_date", "tracking_carrier_uploaded",
                    "feedback_rating", "feedback_text", "feedback_date",
                    "buyer_name", "buyer_email")
            _gapfill(order, {k: sc.get(k) for k in keep}, "sc_scrape", prov)
            # stash the page HTML from THIS visit so the debug step doesn't re-open it
            if sc.get("page_html"):
                order["_page_html"] = sc["page_html"]
            order["enriched_from_scrape"] = 1

            # If we still don't have the WB (explicit-id / backfill mode), take it
            # from the scraped page and re-run the WB-keyed archive lookup that was
            # skipped earlier — this unblocks zoro_order_total/tax/profit.
            if _empty(order.get("wb_order_id")) and sc.get("wb_order_id_scraped"):
                order["wb_order_id"] = sc["wb_order_id_scraped"].split(",")[0]
                prov["wb_order_id"] = "sc_scrape"
                arc2 = enrich_from_archive(conn, aid, order["wb_order_id"], run_id)
                _gapfill(order, arc2, "archive", prov)
        except Exception as e:
            print(f"[CLOSING]   {aid}: scrape failed — {e}")

    # Split order (2-4 WB legs): union EVERY leg's tracking from archive_tracking so
    # FedEx checks all packages, not just the first WB's. (See _union_leg_tracking.)
    added = _union_leg_tracking(conn, order)
    if added:
        prov["tracking_ids"] = (prov.get("tracking_ids", "") + "+legs").lstrip("+")

    # Split order COST: sum Zoro cost across ALL WB legs (was only wb[0]'s → understated
    # cost / overstated profit). Override happens BEFORE the search_zoro_cost fallback and
    # before profit is computed in the batch loop. (See _sum_leg_costs.)
    legs = _sum_leg_costs(conn, order)
    if legs >= 2:
        prov["zoro_order_total"] = (prov.get("zoro_order_total", "") + "+legsum").lstrip("+")

    # Zoro COST fallback — when the archive has no Zoro confirmation for this WB
    # (e.g. the salesmercentile account), search the Zoro inboxes by WB (preferred
    # first). A 0.0 cost counts as missing here (the column default), so it still
    # gets recovered.
    if gmail_ok and order.get("wb_order_id") and not (_safe_float(order.get("zoro_order_total")) or 0):
        try:
            _gapfill(order, search_zoro_cost(order["wb_order_id"], preferred=PREF["zoro"]),
                     "zoro_cost_gmail", prov)
        except Exception as e:
            print(f"[CLOSING]   {aid}: zoro cost search error — {e}")

    # Zoro TRACKING/shipped-time fallback — the 'has shipped' email is keyed by WB
    # (not the Amazon id), so the generic live scan above can never find it. Archive
    # first (_union_leg_tracking above), then this WB-keyed live search, preferred
    # Zoro inboxes first.
    if gmail_ok and (_empty(order.get("tracking_ids")) or _empty(order.get("shipped_email_time"))):
        wbs = _extract_wb_ids(order.get("merchant_order_id")) or \
            ([order["wb_order_id"]] if order.get("wb_order_id") else [])
        for w in wbs:
            try:
                t = search_zoro_tracking(w, preferred=PREF["zoro"])
            except Exception as e:
                print(f"[CLOSING]   {aid}: zoro tracking search error — {e}")
                break
            if t and _gapfill(order, t, "zoro_tracking_gmail", prov):
                order["enriched_from_live_gmail"] = 1
            if not (_empty(order.get("tracking_ids")) or _empty(order.get("shipped_email_time"))):
                break

    # Zoro seller-side recovery (CASE#/RA#/credit) — WB-keyed; only worth a lookup
    # when there's a return/refund in play and we know the WB.
    if gmail_ok and order.get("wb_order_id") and (
            order.get("return_requested") == "Yes" or order.get("refund_done") == "Yes"):
        if _empty(order.get("zoro_case_number")):
            try:
                _gapfill(order, search_zoro_issue(order["wb_order_id"]), "zoro_issue", prov)
            except Exception as e:
                print(f"[CLOSING]   {aid}: zoro issue search error — {e}")

    # LOWEST priority: fill any field still empty from the existing closing_orders
    # row, so a weaker re-run (aged-out email, wrong token) never erases prior data.
    if existing:
        _gapfill(order, {k: existing.get(k) for k in _PERSIST_FIELDS
                         if k in existing and k not in ("order_label",)}, "prior_row", prov)

    # Multi-package / SC-uploaded tracking → merge tracking_id_uploaded into tracking_ids
    # so FedEx checks it. MUST run AFTER the existing-row seed above: on a resume run
    # (scrape off) tracking_id_uploaded exists ONLY in the existing row, so merging
    # earlier (the old bug) left these orders with "no tracking IDs" at FedEx time.
    up = str(order.get("tracking_id_uploaded") or "").strip()
    if up:
        have = _order_tids(order)
        if up not in have:
            order["tracking_ids"] = ", ".join(have + [up])
            prov["tracking_ids"] = (prov.get("tracking_ids", "") + "+sc_uploaded").lstrip("+")

    order["_prov"] = prov
    return order


# ═══════════════════════════════════════════════════════════════════════════
#  Profit + label  (folded from closing_pipeline; label runs AFTER FedEx)
# ═══════════════════════════════════════════════════════════════════════════
def calculate_profit(order):
    earnings = _safe_float(order.get("earnings"))
    zoro = _safe_float(order.get("zoro_order_total"))
    if earnings is None or zoro is None:
        return None, None
    # zoro_order_total is the Zoro GRAND total — it ALREADY includes tax + shipping
    # (verified across both archive_zoro and the placer log). Subtracting zoro_tax
    # again here double-counted tax and understated every taxed order's profit.
    profit = earnings - zoro
    margin = (profit / earnings * 100) if earnings > 0 else None
    return round(profit, 2), (round(margin, 2) if margin is not None else None)


_DT_FORMATS = (
    "%A, %m/%d/%Y %I:%M %p", "%A, %m/%d/%Y", "%a, %b %d, %Y", "%b %d, %Y",
    "%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
    "%a, %b %d, %Y, %I:%M %p", "%Y-%m-%d",
)


def _parse_dt(s):
    """Tolerant datetime parse across the many formats we store (FedEx, archive, SC)."""
    if not s:
        return None
    s = str(s).strip()
    s2 = re.sub(r"\s+(?:PST|PDT|EST|EDT|CST|CDT|MST|MDT|UTC|GMT)\b.*$", "", s).strip()
    for cand in (s2, s):
        for fmt in _DT_FORMATS:
            try:
                return datetime.strptime(cand, fmt)
            except ValueError:
                continue
    try:
        return datetime.fromisoformat(s.replace("Z", "").strip())
    except Exception:
        return None


def compute_timeliness(order):
    """On-time flag (deliver-by vs actual) + Zoro time-to-ship in hours."""
    out = {}
    if order.get("delivery_status") in ("Delivered", "AllDelivered"):
        due = _parse_dt(order.get("deliver_by_date"))
        actual = _parse_dt(order.get("delivery_date"))
        if due and actual:
            out["on_time"] = "On Time" if actual.date() <= due.date() else "Late"
    rcv = _parse_dt(order.get("zoro_email_ts") or order.get("zoro_order_date"))
    shipped = _parse_dt(order.get("shipped_email_time"))
    if rcv and shipped:
        hrs = round((shipped - rcv).total_seconds() / 3600, 1)
        if 0 <= hrs <= 24 * 60:        # sanity bound (<60 days)
            out["time_to_ship_hrs"] = hrs
    return out


def compute_ship_timeliness(order):
    """Did we ship before Amazon's ship-by deadline? shipped_email_time is the Zoro
    'has shipped' time in PKT; ship_by_date is the Amazon deadline in US/Pacific. We
    convert the PKT ship time to Pacific and compare to END-OF-DAY Pacific on the ship-by
    date — so e.g. 'Tue 12:35 PKT' = 'Mon ~23:35 Pacific' correctly counts as shipped EARLY
    against a Tuesday deadline. ship_margin_hrs > 0 = shipped early; < 0 = late."""
    out = {}
    shp = _parse_any(order.get("shipped_email_time"))   # naive, PKT
    sb = _parse_any(order.get("ship_by_date"))          # Amazon (Pacific) date
    if not shp or not sb:
        return out
    try:
        shp_pt = shp.replace(tzinfo=_TZ_PK).astimezone(_TZ_PT)        # PKT → Pacific
        sd, bd = shp_pt.date(), sb.date()                            # compare in Amazon's clock
        if sd < bd:
            out["shipped_on_time"] = "Early"      # shipped before the deadline DAY
        elif sd == bd:
            out["shipped_on_time"] = "On Time"    # shipped on the deadline day
        else:
            out["shipped_on_time"] = "Late"
        sb_eod = sb.replace(hour=23, minute=59, second=59, tzinfo=_TZ_PT)
        secs = (sb_eod - shp_pt).total_seconds()
        if abs(secs) <= 120 * 86400:
            out["ship_margin_hrs"] = round(secs / 3600, 1)   # >0 = before deadline
    except Exception:
        pass
    return out


def derive_fields(o):
    """Normalize all dates to one format + add PKT timestamps and speed KPIs."""
    # synchronized date format (keeps time, no weekday). deliver-by may be a
    # window → keep the deadline (latest) date.
    if o.get("purchase_date"):
        o["purchase_date"] = norm_date(o["purchase_date"])
    if o.get("ship_by_date"):
        o["ship_by_date"] = norm_date(o["ship_by_date"])
    if o.get("deliver_by_date"):
        o["deliver_by_date"] = norm_date(o["deliver_by_date"], range_last=True)
    # PKT email timestamps, clearly named (the source values are already PKT)
    o["order_received_pkt"] = norm_pkt(o.get("sold_email_time"))     # Amazon "Sold, ship now"
    o["zoro_placed_pkt"]    = norm_pkt(o.get("zoro_email_ts"))       # Zoro "Has Been Received"
    o["zoro_shipped_pkt"]   = norm_pkt(o.get("shipped_email_time"))  # Zoro "Has Shipped"
    # speeds: how fast WE placed it on Zoro, and how fast Zoro shipped
    o["placement_speed"], o["placement_speed_hrs"] = duration(o.get("sold_email_time"), o.get("zoro_email_ts"))
    o["zoro_ship_speed"], o["zoro_ship_speed_hrs"] = duration(o.get("zoro_email_ts"), o.get("shipped_email_time"))


def compute_net_profit(order):
    """Realized profit after a refund. For a normal order it equals gross margin;
    for a refunded order: gross - customer_refund + Zoro RA recovery (often $0 until
    the Zoro credit lands), so the column shows the true loss on refunds."""
    gross = _safe_float(order.get("profit_dollars"))
    if gross is None:
        return None
    if order.get("refund_done") == "Yes":
        refund = _safe_float(order.get("refund_amount")) or 0.0
        recovered = _safe_float(order.get("zoro_refund_amount")) or 0.0
        return round(gross - refund + recovered, 2)
    return gross


def determine_label(order):
    # Return + refund: distinguish a true cancellation (never shipped) from a
    # Refunded order (shipped/delivered, money returned) — different for accounting.
    if order.get("return_requested") == "Yes" and order.get("refund_done") == "Yes":
        # "shipped" = it actually went out. The report's order_status='Shipped' is the
        # authoritative signal; tracking_ids can be missing (uncaptured) even though the
        # order shipped — so don't downgrade a Shipped+refunded order to Cancelled.
        shipped = order.get("order_status") == "Shipped" or bool(order.get("tracking_ids")) or \
            order.get("delivery_status") in ("Delivered", "AllDelivered", "PartialDelivered", "OutForDelivery")
        return "Refunded" if shipped else "Cancelled"
    # Amazon-cancelled order (report order-status) that never delivered → Cancelled.
    # If it delivered anyway, fall through so it labels by the delivered outcome.
    if str(order.get("order_status") or "").strip().lower() == "cancelled" \
            and order.get("delivery_status") not in ("Delivered", "AllDelivered"):
        return "Cancelled"
    earnings = _safe_float(order.get("earnings"))
    zoro = _safe_float(order.get("zoro_order_total"))
    deliv_note = str(order.get("delivery_note") or "").lower()
    is_rts = "return" in deliv_note and ("shipper" in deliv_note or "sender" in deliv_note)
    if not is_rts and earnings and zoro and order.get("tracking_ids") \
            and order.get("delivery_status") in ("Delivered", "AllDelivered"):
        profit = _safe_float(order.get("profit_dollars"))
        if profit is not None and profit < 0:
            return "Loss"        # delivered but lost money — surfaced, not hidden as Completed
        return "Completed"
    return "Incomplete"


# ═══════════════════════════════════════════════════════════════════════════
#  Bulk FedEx for one batch  (in-memory; persist_batch writes it)
# ═══════════════════════════════════════════════════════════════════════════
def _order_tids(o):
    """Distinct tracking IDs for an order, order-preserving (dedup so a repeated
    id can't inflate the package count / break the all-delivered test)."""
    seen = []
    for t in str(o.get("tracking_ids") or "").split(","):
        t = t.strip()
        if t and t not in seen:
            seen.append(t)
    return seen


def bulk_fedex_check(batch, fedex):
    """Check every tracking ID of every order in ONE FedEx call, then AGGREGATE
    per order across its packages: an order is Delivered only when ALL its
    tracking IDs are delivered (multi-package orders are checked in full)."""
    all_tids = []
    for o in batch:
        for tid in _order_tids(o):
            if tid not in all_tids:
                all_tids.append(tid)
    if not all_tids:
        print("[CLOSING]   FedEx: no tracking IDs in this batch")
        return {"delivered": 0, "resolved": 0, "unresolved": 0, "blocked": False, "orders": len(batch)}
    print(f"[CLOSING]   FedEx: {len(all_tids)} tracking IDs across {len(batch)} orders")

    # Fast & robust multi-tier track (Official REST API -> Headless CDP -> DOM Scraper)
    results = fedex.track_batch(all_tids)
    if not results:
        results = [fedex._empty_detail()] * len(all_tids)

    by_tid = dict(zip(all_tids, results))
    for t in all_tids:
        if not by_tid.get(t):
            by_tid[t] = fedex._empty_detail()

    delivered = resolved = unresolved = 0
    DELIV = ("Delivered", "AllDelivered")
    try:
        from engine.fedex_api import STATUS_RANK
    except Exception:
        STATUS_RANK = {}
    for o in batch:
        tids = _order_tids(o)
        details = [by_tid[t] for t in tids if t in by_tid]
        if not details:
            continue
        statuses = [d.get("status", "") for d in details]
        n_deliv = sum(1 for s in statuses if s in DELIV)
        if n_deliv == len(details):
            agg = "Delivered" if len(details) == 1 else "AllDelivered"
            pick = [d for d in details if d.get("status") in DELIV][-1]   # latest delivery info
        elif n_deliv > 0:
            agg = "PartialDelivered"               # some packages still moving → stays Incomplete
            pick = [d for d in details if d.get("status") in DELIV][-1]
        else:
            informative = [d for d in details if d.get("status") not in ("", "Unknown", "Blocked")]
            pick = informative[0] if informative else details[0]
            agg = pick.get("status") or "Unknown"

        # An unresolved lookup is not a status. "Unknown" (nothing parsed) and "Blocked"
        # (FedEx served its bot-defence page) are never written — not even onto a blank row.
        # On 19 Sep 2026 the old rule wrote "Unknown" onto 4,703 live orders and stamped
        # them enriched_from_fedex=1, so nothing ever looked at them again.
        if agg in ("", "Unknown", "Blocked"):
            unresolved += 1
            continue
        # Never move an order BACKWARDS. A partial or stale page can answer "Label created"
        # for a parcel already recorded In transit or Out for delivery; FedEx's own history
        # never runs in reverse, so a lower-ranked answer is a worse read, not a change.
        prev = str(o.get("delivery_status") or "").strip()
        if prev and STATUS_RANK.get(agg, 0) < STATUS_RANK.get(prev, 0):
            resolved += 1
            continue
        if agg in DELIV:
            delivered += 1
        resolved += 1
        o["delivery_status"] = agg
        o["delivery_date"] = pick.get("delivery_date", "")
        o["estimated_window"] = pick.get("estimated_window", "")
        o["last_scan_location"] = pick.get("last_scan_location", "")
        o["last_scan_time"] = pick.get("last_scan_time", "")
        o["signed_by"] = pick.get("signed_by", "")
        o["left_at"] = pick.get("left_at", "")
        o["delivery_note"] = pick.get("delivery_note", "")
        o["delivery_from"] = pick.get("from_loc", "")
        o["delivery_to"] = pick.get("to_loc", "")
        o["enriched_from_fedex"] = 1
        o["_fedex_touched"] = True                # only rows FedEx actually answered get persisted
    blocked = bool(getattr(fedex, "blocked", False))
    line = (f"[CLOSING]   FedEx: {delivered} delivered / {resolved} resolved / "
            f"{unresolved} unresolved of {len(batch)} orders")
    if blocked:
        line += "  🚫 FedEx is refusing this session (bot defence) — unresolved orders left as they were"
    print(line)
    return {"delivered": delivered, "resolved": resolved, "unresolved": unresolved,
            "blocked": blocked, "orders": len(batch)}


# ═══════════════════════════════════════════════════════════════════════════
#  Persist + messages
# ═══════════════════════════════════════════════════════════════════════════
_PERSIST_FIELDS = (
    "amazon_order_id", "merchant_order_id", "account", "order_status", "asin", "sku",
    "title", "quantity", "item_price", "shipping_price", "ship_city", "ship_state",
    "ship_postal_code", "ship_country", "is_business", "purchase_date", "ship_by_date", "deliver_by_date",
    "earnings", "amazon_fees", "shipping_amt", "sold_email_time", "wb_order_id",
    "zoro_account_name", "zoro_gmail_account", "zoro_po_number", "zoro_order_status",
    "zoro_sku", "zoro_qty", "zoro_order_date", "zoro_email_ts", "zoro_payment_card",
    "zoro_tax", "zoro_order_total", "zoro_subtotal", "zoro_shipping", "zoro_payment_method", "zoro_item_desc", "zoro_buyer_zip", "zoro_deliver_by", "zoro_stock", "coupon_used", "coupon_savings",
    "profit_dollars", "profit_percent", "net_profit_after_refund", "on_time", "time_to_ship_hrs",
    "shipped_on_time", "ship_margin_hrs",
    "placement_speed", "placement_speed_hrs", "zoro_ship_speed", "zoro_ship_speed_hrs",
    "order_received_pkt", "zoro_placed_pkt", "zoro_shipped_pkt",
    "tracking_ids", "shipped_email_time", "carrier",
    "delivery_status", "delivery_date", "estimated_window", "last_scan_location",
    "last_scan_time", "signed_by", "left_at", "delivery_note", "delivery_from", "delivery_to",
    "return_requested", "refund_done", "refund_amount", "refund_date", "refund_reason",
    "return_reason", "return_customer_comment", "customer_message_text",
    "zoro_case_number", "zoro_ra_number", "zoro_refund_amount", "zoro_refund_status",
    "review_request_sent",
    "customer_messaged", "customer_inquiry_count", "customer_inquiry", "order_label",
    "tracking_id_uploaded", "tracking_upload_date", "tracking_carrier_uploaded",
    "feedback_rating", "feedback_text", "feedback_date",
    "enriched_from_archive", "enriched_from_live_gmail", "enriched_from_placer_db",
    "enriched_from_fedex", "enriched_from_scrape", "last_enrichment_run",
)


# Always written even when empty (they're authoritative every run).
_ALWAYS_WRITE = {"amazon_order_id", "order_label", "last_enrichment_run"}


def _cmp(v):
    """Normalise a value for the changed-row test (None ≡ '', 35.9 ≡ '35.90')."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return f"{float(v):.4f}".rstrip("0").rstrip(".")
    s = str(v).strip()
    try:
        return f"{float(s):.4f}".rstrip("0").rstrip(".")
    except ValueError:
        return s


def _row_changed(data, existing):
    for k, v in data.items():
        if k in ("amazon_order_id", "last_enrichment_run"):
            continue
        if _cmp(v) != _cmp(existing.get(k)):
            return True
    return False


def persist_batch(batch, run_id):
    """Write the whole batch in ONE short transaction — no Selenium/network inside.
    Non-destructive: an empty/None value is NOT written (so it can never clobber a
    richer value already in the row); only columns that exist in the live table are
    sent; one bad row can't abort the batch. A row whose values did not change is not
    rewritten at all (26 Sep 2026 — every run used to rewrite the whole window).
    Returns (written, unchanged)."""
    if DRY_RUN:
        print(f"[CLOSING]   [DRY] would persist {len(batch)} orders")
        return 0, 0
    with get_conn() as conn:
        live = {r[1] for r in conn.execute("PRAGMA table_info(closing_orders)")}
        ok = fail = unchanged = 0
        for o in batch:
            o["last_enrichment_run"] = run_id
            data = {}
            for k in _PERSIST_FIELDS:
                if k not in o or k not in live:
                    continue
                if _empty(o[k]) and k not in _ALWAYS_WRITE:
                    continue        # never overwrite an existing good value with empty
                data[k] = o[k]
            if "amazon_order_id" not in data:
                continue
            ex = o.get("_existing")
            if ex and not _row_changed(data, ex):
                unchanged += 1
                continue
            # Retry on "database is locked" so a concurrent writer (e.g. the
            # other account's closing run) can't cause a silent dropped save.
            for attempt in range(10):
                try:
                    upsert_closing_order(data, conn=conn)
                    ok += 1
                    break
                except Exception as e:
                    if "locked" in str(e).lower() and attempt < 9:
                        time.sleep(0.4 * (attempt + 1))   # backoff, ~22s total budget
                        continue
                    fail += 1
                    print(f"[CLOSING]   ⚠️ persist {o.get('amazon_order_id')} failed: {e}")
                    break
        if fail or unchanged:
            print(f"[CLOSING]   persisted {ok}, {unchanged} unchanged" + (f", {fail} failed" if fail else ""))
        return ok, unchanged


def send_batch_messages(driver, batch, pd_cfg, gmail_services):
    """Delivery check-in (incl. tracking IDs) + review requests for delivered orders.
    Rows already exist in closing_orders (persisted just before), so post_delivery's
    UPDATE lands. All guards (already-sent, return/refund, 6/12-day) live inside; the
    Gmail side of those guards is prefetched for the batch in a few OR-queries instead of
    ~25 searches per order. Returns (sent, reviewed)."""
    sent = reviewed = 0
    delivered = [o for o in batch if o.get("delivery_status") in ("Delivered", "AllDelivered")]
    guards = None
    if delivered and gmail_services:
        try:
            from engine.post_delivery import prefetch_guards
            guards = prefetch_guards(gmail_services, [o["amazon_order_id"] for o in delivered])
        except Exception as e:
            print(f"[CLOSING]   guard prefetch failed ({e}) — per-order Gmail checks will run")
            guards = None
    for o in delivered:
        try:
            res = pd_process_order(driver, o, pd_cfg, gmail_services,
                                   closing_table=True, dry_run=DRY_RUN or MSG_DRY, guards=guards)
            if res.get("delivered_msg_sent") == "Yes":
                sent += 1
            if res.get("review_requested") == "Yes":
                reviewed += 1
        except Exception as e:
            print(f"[CLOSING]   {o['amazon_order_id']}: message step failed — {e}")
    if sent or reviewed:
        print(f"[CLOSING]   Messages: {sent} sent, {reviewed} reviews requested")
    return sent, reviewed


# ═══════════════════════════════════════════════════════════════════════════
#  Debug capture (CLOSING_DEBUG_EML=1) + verbose log (CLOSING_VERBOSE=1)
# ═══════════════════════════════════════════════════════════════════════════
# One .eml per email type + the full SC order-page HTML, per order, for offline
# debugging of the parsers. "cc_email" = the CC copy Amazon sends you of a
# message you sent the buyer ("Your e-mail to ...").
EML_QUERIES = {
    "sold_email":     'subject:"Sold, ship now" "{aid}"',
    "received_email": 'subject:"Has Been Received" "{wb}"',
    "shipping_email": '"{wb}" (subject:(shipped OR tracking OR shipping) OR "on its way" OR "has shipped")',
    "cc_email":       'subject:"Your e-mail to" "{aid}"',
}


def _save_eml(service, query, path):
    """Save the first matching Gmail message as a raw .eml. Returns True if saved."""
    try:
        msgs = service.users().messages().list(
            userId="me", q=query, maxResults=1).execute().get("messages", [])
        if not msgs:
            return False
        raw = service.users().messages().get(
            userId="me", id=msgs[0]["id"], format="raw").execute()
        with open(path, "wb") as f:
            f.write(base64.urlsafe_b64decode(raw["raw"].encode("utf-8")))
        return True
    except Exception as e:
        print(f"      [eml] {os.path.basename(path)} error: {e}")
        return False


def _safe_name(s, n=70):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s))[:n].strip("_") or "msg"


def harvest_all_emails(gmail_services, query, out, prefix):
    """Save EVERY Gmail message matching `query` (across all accounts) as numbered .eml.
    Returns the count saved. Used for deep per-order analysis."""
    saved = 0
    for acct, svc in (gmail_services or {}).items():
        try:
            page_token = None
            while True:
                resp = svc.users().messages().list(
                    userId="me", q=query, maxResults=100, pageToken=page_token).execute()
                for m in resp.get("messages", []):
                    try:
                        full = svc.users().messages().get(
                            userId="me", id=m["id"], format="raw").execute()
                        subj = ""
                        meta = svc.users().messages().get(
                            userId="me", id=m["id"], format="metadata",
                            metadataHeaders=["Subject"]).execute()
                        for h in meta.get("payload", {}).get("headers", []):
                            if h["name"] == "Subject":
                                subj = h["value"]
                        fname = f"{prefix}_{saved+1:02d}_{acct}_{_safe_name(subj)}.eml"
                        with open(os.path.join(out, fname), "wb") as f:
                            f.write(base64.urlsafe_b64decode(full["raw"].encode("utf-8")))
                        saved += 1
                    except Exception:
                        continue
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
        except Exception as e:
            print(f"      [harvest] {acct} error: {e}")
    print(f"      [harvest] {prefix}: {saved} emails")
    return saved


def dump_order_debug(order, gmail_services, sc_driver, run_id):
    """Per order: SC page HTML (+ WB extracted from it), typed .eml, optional full
    email harvest for the Amazon id and every WB id, and record.json."""
    aid = order["amazon_order_id"]
    out = os.path.join(DEBUG_DIR, run_id, aid)
    os.makedirs(out, exist_ok=True)
    print(f"   [debug] {aid} → {out}")

    # SC order page: REUSE the HTML the scrape step already loaded (no second
    # open). Only navigate if scrape didn't run (debug-without-scrape).
    wbs = []
    html = order.get("_page_html")
    if html:
        with open(os.path.join(out, "order_page.html"), "w", encoding="utf-8") as f:
            f.write(html)
        wbs = sorted(set(re.findall(r"WB\d{6,}", html)))
        print(f"      [html] order_page.html ({len(html):,} bytes, reused scrape) | WB: {wbs or 'none'}")
    elif sc_driver is not None:
        try:
            sc_driver.get(f"https://sellercentral.amazon.com/orders-v3/order/{aid}")
            time.sleep(3)
            html = sc_driver.page_source
            with open(os.path.join(out, "order_page.html"), "w", encoding="utf-8") as f:
                f.write(html)
            wbs = sorted(set(re.findall(r"WB\d{6,}", html)))
            print(f"      [html] order_page.html ({len(html):,} bytes) | WB on page: {wbs or 'none'}")
        except Exception as e:
            print(f"      [html] order_page.html error: {e}")
    if order.get("wb_order_id") and order["wb_order_id"] not in wbs:
        wbs.append(order["wb_order_id"])
    wb_primary = wbs[0] if wbs else (order.get("wb_order_id") or aid)

    # Typed sample emails (sold/received/shipping/cc) — first hit each
    for name, qtmpl in EML_QUERIES.items():
        q = qtmpl.format(aid=aid, wb=wb_primary)
        for acct, svc in (gmail_services or {}).items():
            if _save_eml(svc, q, os.path.join(out, f"{name}.eml")):
                print(f"      [eml] {name} ← {acct}")
                break
        else:
            print(f"      [eml] {name}: not found")

    # Full harvest: EVERY email mentioning the Amazon id, and every WB id
    if HARVEST:
        harvest_all_emails(gmail_services, f'"{aid}"', out, "amazon")
        for w in wbs:
            harvest_all_emails(gmail_services, f'"{w}"', out, f"wb_{w}")

    try:
        rec = {k: v for k, v in order.items() if k != "_prov"}
        rec["_provenance"] = order.get("_prov", {})
        rec["_wb_ids_on_page"] = wbs
        with open(os.path.join(out, "record.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2, default=str)
    except Exception as e:
        print(f"      [json] record.json error: {e}")


_VERBOSE_FIELDS = (
    "order_status", "title", "quantity", "item_price", "earnings", "amazon_fees",
    "shipping_amt", "wb_order_id", "zoro_order_total", "zoro_tax", "profit_dollars",
    "profit_percent", "net_profit_after_refund", "zoro_subtotal", "zoro_shipping", "zoro_payment_method", "coupon_used", "coupon_savings", "on_time",
    "placement_speed", "zoro_ship_speed", "order_received_pkt", "zoro_placed_pkt", "zoro_shipped_pkt",
    "tracking_ids", "tracking_id_uploaded", "carrier",
    "delivery_status", "delivery_date", "signed_by", "left_at",
    "return_requested", "refund_done", "refund_amount", "refund_reason",
    "return_reason", "return_customer_comment", "customer_message_text",
    "zoro_case_number", "zoro_refund_amount", "zoro_ra_number",
    "customer_messaged", "review_request_sent",
    "feedback_rating", "feedback_text", "feedback_date",
    "ship_city", "ship_state", "ship_postal_code", "purchase_date",
    "ship_by_date", "deliver_by_date", "order_label",
)


def print_order_verbose(order):
    """Per-field dump with the source that filled each field (report/placer/archive/live_gmail/scrape/fedex)."""
    prov = order.get("_prov", {})
    aid = order.get("amazon_order_id", "?")
    print(f"\n   ┌─ {aid}  [{order.get('order_label', '?')}]")
    for k in _VERBOSE_FIELDS:
        v = order.get(k)
        if _empty(v):
            continue
        src = prov.get(k, "fedex" if k.startswith(("delivery", "signed", "left", "last_scan", "estimated")) else "-")
        sval = str(v)
        if len(sval) > 60:
            sval = sval[:57] + "..."
        print(f"   │  {k:<22} = {sval:<60} ({src})")
    missing = [k for k in ("earnings", "zoro_order_total", "tracking_ids", "delivery_status")
               if _empty(order.get(k))]
    if missing:
        print(f"   │  ⚠️ still missing: {', '.join(missing)}")
    print("   └─")


# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════
def _ensure_columns():
    """Idempotently add every column closing.py persists that an older/fresh
    closing_orders table may lack (the canonical CREATE TABLE lags the code).
    No-op on this machine's live DB; prevents 'no such column' crashes elsewhere."""
    cols = {
        "feedback_rating": "INTEGER", "feedback_text": "TEXT", "feedback_date": "TEXT",
        "refund_amount": "REAL", "refund_date": "TEXT", "refund_reason": "TEXT",
        "customer_inquiry_count": "INTEGER", "customer_inquiry": "TEXT", "zoro_po_number": "TEXT",
        "zoro_gmail_account": "TEXT", "tracking_id_uploaded": "TEXT",
        "tracking_upload_date": "TEXT", "tracking_carrier_uploaded": "TEXT",
        # new signals (chosen 2026-06-18): reasons, Zoro support recovery, msg text
        "return_reason": "TEXT", "return_customer_comment": "TEXT",
        "customer_message_text": "TEXT", "zoro_case_number": "TEXT",
        "zoro_ra_number": "TEXT", "zoro_refund_amount": "REAL", "zoro_refund_status": "TEXT",
        "review_request_sent": "TEXT", "net_profit_after_refund": "REAL",
        # cost breakdown + paying account + delivery/lead-time KPIs
        "zoro_subtotal": "REAL", "zoro_shipping": "REAL", "zoro_payment_method": "TEXT",
        "on_time": "TEXT", "time_to_ship_hrs": "REAL",
        # clearly-named PKT timestamps + speed KPIs (placement & Zoro shipping)
        "order_received_pkt": "TEXT", "zoro_placed_pkt": "TEXT", "zoro_shipped_pkt": "TEXT",
        "is_business": "TEXT", "zoro_item_desc": "TEXT",
        "placement_speed": "TEXT", "placement_speed_hrs": "REAL",
        "zoro_ship_speed": "TEXT", "zoro_ship_speed_hrs": "REAL",
        # Zoro delivery estimate + stock captured at placement
        "zoro_deliver_by": "TEXT", "zoro_stock": "INTEGER",
        # shipped-on-time vs Amazon ship-by (PKT→Pacific)
        "shipped_on_time": "TEXT", "ship_margin_hrs": "REAL",
    }
    with get_conn() as conn:
        have = {r[1] for r in conn.execute("PRAGMA table_info(closing_orders)")}
        for col, typ in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE closing_orders ADD COLUMN {col} {typ}")
                print(f"[CLOSING] schema: added closing_orders.{col}")
    _ensure_view()


# A clean, logically-ordered, readable presentation of closing_orders for
# SQL / exports / BI. The storage table keeps its columns; this view is the
# tidy "business" shape (rename + sequence) — query:  SELECT * FROM v_closing;
def _ensure_view():
    sql = """
    CREATE VIEW IF NOT EXISTS v_closing AS SELECT
      amazon_order_id            AS order_id,
      wb_order_id                AS zoro_order_id,
      account,
      is_business                AS b2b,
      order_label                AS outcome,
      order_status               AS amazon_status,
      asin, sku, title           AS product, zoro_item_desc AS zoro_item, quantity AS qty,
      item_price                 AS amazon_price,
      earnings                   AS amazon_earnings,
      amazon_fees,
      shipping_amt               AS amazon_shipping,
      zoro_account_name          AS zoro_account,
      zoro_payment_method        AS paid_with,
      zoro_payment_card          AS card_last4,
      zoro_subtotal, zoro_shipping, zoro_tax,
      zoro_order_total           AS zoro_total,
      coupon_used                AS coupon,
      coupon_savings,
      profit_dollars             AS gross_profit,
      profit_percent             AS margin_pct,
      net_profit_after_refund    AS net_profit,
      purchase_date              AS amazon_order_date,
      ship_by_date, deliver_by_date,
      order_received_pkt, zoro_placed_pkt, zoro_shipped_pkt,
      placement_speed, zoro_ship_speed,
      delivery_status, delivery_date, on_time, signed_by, left_at,
      tracking_ids, tracking_id_uploaded, carrier,
      return_requested, return_reason, return_customer_comment,
      refund_done, refund_amount, refund_reason,
      zoro_case_number, zoro_ra_number, zoro_refund_amount,
      customer_messaged, customer_message_text,
      feedback_rating, feedback_text,
      delivered_msg_sent, review_request_sent,
      ship_city, ship_state, ship_postal_code
    FROM closing_orders;"""
    with get_conn() as conn:
        conn.execute("DROP VIEW IF EXISTS v_closing")
        conn.execute(sql)


# ═══════════════════════════════════════════════════════════════════════════
#  Report-diff: which orders need a pass this run (26 Sep 2026)
# ═══════════════════════════════════════════════════════════════════════════
def _run_time(run_id):
    """'HOANK-20260926-180618' → local datetime, else None."""
    m = re.search(r"(\d{8})-(\d{6})$", str(run_id or ""))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _evidence_maps(conn, since):
    """Archive evidence: latest fetched_at per key for rows fetched since `since` (the
    gmail_fetch tool stamps local ISO time), plus the full key sets for gap detection.
    fetched_at is compared with 'T' folded to ' ' so both timestamp spellings sort together."""
    def _max(sql, args=()):
        return {r[0]: (r[1] or "") for r in conn.execute(sql, args) if r[0]}

    def _set(sql, args=()):
        return {r[0] for r in conn.execute(sql, args) if r[0]}
    s = (since,)
    return {
        "earn_recent": _max("SELECT amazon_order_id, MAX(REPLACE(fetched_at,'T',' ')) FROM archive_earnings "
                            "WHERE REPLACE(fetched_at,'T',' ') >= ? GROUP BY 1", s),
        "zoro_recent": _max("SELECT wb_order_id, MAX(REPLACE(fetched_at,'T',' ')) FROM archive_zoro "
                            "WHERE REPLACE(fetched_at,'T',' ') >= ? GROUP BY 1", s),
        "trk_recent":  _max("SELECT wb_order_id, MAX(REPLACE(fetched_at,'T',' ')) FROM archive_tracking "
                            "WHERE REPLACE(fetched_at,'T',' ') >= ? GROUP BY 1", s),
        "ret_recent":  _max("SELECT amazon_order_id, MAX(REPLACE(fetched_at,'T',' ')) FROM archive_returns "
                            "WHERE REPLACE(fetched_at,'T',' ') >= ? GROUP BY 1", s),
        "msg_recent":  _max("SELECT amazon_order_id, MAX(REPLACE(fetched_at,'T',' ')) FROM archive_messages "
                            "WHERE REPLACE(fetched_at,'T',' ') >= ? GROUP BY 1", s),
        "earn_all":   _set("SELECT amazon_order_id FROM archive_earnings"),
        "zoro_all":   _set("SELECT wb_order_id FROM archive_zoro"),
        "trk_all":    _set("SELECT DISTINCT wb_order_id FROM archive_tracking "
                           "WHERE tracking_ids IS NOT NULL AND tracking_ids != ''"),
        "ret_all":    _set("SELECT DISTINCT amazon_order_id FROM archive_returns"),
        "msg_in_all": _set("SELECT DISTINCT amazon_order_id FROM archive_messages "
                           "WHERE direction='inbound' OR message_type IN ('buyer_inquiry','buyer_reply')"),
    }


def _select_work(orders, existing, placer, uploads, conn, fedex_active=False):
    """Split the report into the orders that need a closing pass and the ones nothing can
    change. Returns (todo, stats). Reasons, in order:
      new          — not in closing_orders yet
      changed      — report status / WB label / quantity differ from the stored row
      evidence     — an archive email for the order/WB was fetched after the row's last pass
      gap_placer   — the placer or tracker holds a value the row lacks (dates, cost, upload…)
      gap_archive  — an archive row exists that the closing row never absorbed
      fedex        — tracking known, not delivered, and a delivery source is active this run
      gap_retry    — a core field is still missing and live Gmail may retry (≥ GAP_RETRY_HOURS)
    Everything else is skipped without a read, a write or an audit row."""
    now = datetime.now()
    thresholds, global_since = {}, None
    for o in orders:
        ex = existing.get(o["amazon_order_id"])
        if not ex:
            continue
        t = _run_time(ex.get("last_enrichment_run"))
        thresholds[o["amazon_order_id"]] = t
        if t and (global_since is None or t < global_since):
            global_since = t
    if global_since is None:
        global_since = now - _td(days=60)
    global_since = max(global_since - _td(hours=EVIDENCE_MARGIN_H), now - _td(days=60))
    ev = _evidence_maps(conn, global_since.strftime("%Y-%m-%d %H:%M:%S"))

    def newer(ts, thr):
        if not ts:
            return False
        if thr is None:
            return True
        return str(ts)[:19] > (thr - _td(hours=EVIDENCE_MARGIN_H)).strftime("%Y-%m-%d %H:%M:%S")

    todo = []
    stats = {"new": 0, "changed": 0, "evidence": 0, "gap_placer": 0, "gap_archive": 0,
             "fedex": 0, "gap_retry": 0, "skipped": 0}
    for o in orders:
        aid = o["amazon_order_id"]
        ex = existing.get(aid)
        if not ex:
            stats["new"] += 1; todo.append(o); continue
        thr = thresholds.get(aid)
        if thr is None:                       # never enriched by closing.py itself
            stats["changed"] += 1; todo.append(o); continue
        if _s(ex.get("order_status")) != _s(o.get("order_status")) \
                or _s(ex.get("merchant_order_id")) != _s(o.get("merchant_order_id")) \
                or int(o.get("quantity") or 0) != int(ex.get("quantity") or 0):
            stats["changed"] += 1; todo.append(o); continue
        wbs = _extract_wb_ids(o.get("merchant_order_id")) or ([o["wb_order_id"]] if o.get("wb_order_id") else [])
        if newer(ev["earn_recent"].get(aid), thr) or newer(ev["ret_recent"].get(aid), thr) \
                or newer(ev["msg_recent"].get(aid), thr) \
                or any(newer(ev["zoro_recent"].get(w), thr) or newer(ev["trk_recent"].get(w), thr) for w in wbs):
            stats["evidence"] += 1; todo.append(o); continue
        pl = placer.get(aid) or {}
        up = uploads.get(aid) or {}
        if any(not _empty(pl.get(k)) and _empty(ex.get(k)) for k in _PLACER_TRUST) \
                or (not _empty(up.get("tracking_id_uploaded")) and _empty(ex.get("tracking_id_uploaded"))):
            stats["gap_placer"] += 1; todo.append(o); continue
        if (_empty(ex.get("earnings")) and aid in ev["earn_all"]) \
                or (not (_safe_float(ex.get("zoro_order_total")) or 0) and any(w in ev["zoro_all"] for w in wbs)) \
                or (_empty(ex.get("tracking_ids")) and any(w in ev["trk_all"] for w in wbs)) \
                or (ex.get("return_requested") != "Yes" and aid in ev["ret_all"]) \
                or (ex.get("customer_messaged") != "Yes" and aid in ev["msg_in_all"]):
            stats["gap_archive"] += 1; todo.append(o); continue
        if fedex_active and _order_tids(ex) and ex.get("delivery_status") not in ("Delivered", "AllDelivered"):
            stats["fedex"] += 1; todo.append(o); continue
        if ENABLE_GMAIL and (now - thr) >= _td(hours=GAP_RETRY_HOURS) and (
                _empty(ex.get("earnings")) or not (_safe_float(ex.get("zoro_order_total")) or 0)
                or _empty(ex.get("tracking_ids"))):
            stats["gap_retry"] += 1; todo.append(o); continue
        stats["skipped"] += 1
    return todo, stats


def _record_run(run_id, **fields):
    """closing_run_log row for this run — the table existed since June and was never
    written, so no run had a duration or a count anyone could look at."""
    try:
        from utils.unified_db import start_closing_run, update_closing_run, finish_closing_run
        with get_conn() as c:
            exists = c.execute("SELECT 1 FROM closing_run_log WHERE run_id=?", (run_id,)).fetchone()
        date_from = fields.pop("date_from", "") or ""
        date_to = fields.pop("date_to", "") or ""
        status = fields.pop("status", None)
        if not exists:
            start_closing_run(run_id, date_from, date_to, [ACCOUNT])
        if fields:
            update_closing_run(run_id, fields)
        if status:
            finish_closing_run(run_id, status)
    except Exception as e:
        print(f"[CLOSING]   run log: {e}")


def main():
    if ACCOUNT not in ACCOUNTS:
        print(f"[CLOSING] ❌ Unknown account '{ACCOUNT}' (use {' or '.join(ACCOUNTS)})")
        return 2
    # One closing per account at a time: two concurrent runs of the SAME account
    # would double-enrich and could double-message buyers (the already-sent guard
    # is a DB read — two processes can both read "not sent" and both send).
    lock_name = f"closing_{ACCOUNT}"
    got, holder = acquire_lock(lock_name)
    if not got:
        print(f"[CLOSING] ❌ Another closing run for {ACCOUNT} is active (pid {holder}) — aborting.")
        return 3
    try:
        return _main_locked()
    finally:
        release_lock(lock_name)


def _main_locked():
    cfg = ACCOUNTS[ACCOUNT]
    pd_cfg = PD_ACCOUNT_CONFIGS[cfg["pd_key"]]
    # Account in the run_id so two accounts started the same second can't share
    # one id in logs/last_enrichment_run.
    run_id = f"{ACCOUNT}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    print("=" * 60)
    print(f"[CLOSING] closing.py — run {run_id} | account={ACCOUNT} | days={DAYS}")
    print(f"[CLOSING] dry_run={DRY_RUN} fedex={ENABLE_FEDEX} scrape={ENABLE_SCRAPE} "
          f"messages={ENABLE_MSGS} harvest={HARVEST}")
    print("=" * 60)
    init_db()
    _ensure_columns()
    _t_run0 = time.time()
    _today = datetime.now().date()
    _record_run(run_id, date_from=START_DATE or (_today - _td(days=DAYS)).isoformat(),
                date_to=END_DATE or _today.isoformat())

    # Refresh order_placer_orders from Worker A's placement log so closing can
    # reuse everything captured at placement (WB, earnings, Zoro cost, address)
    # → script-placed orders then only need delivery + messages + feedback.
    # Incremental (blanks + new lines) by default; the full 60k-line reconciliation runs
    # once a day inside sync_smart, or on CLOSING_SYNC_PLACER_FULL=1 — it used to run
    # twice per account per bulk, rewriting every placer row each time.
    if _env_bool("CLOSING_SYNC_PLACER", True):
        try:
            from scripts.sync_order_placer_logs import sync_smart
            s = sync_smart(verbose=False, force_full=_env_bool("CLOSING_SYNC_PLACER_FULL", False))
            print(f"[CLOSING] Placer log synced ({s.get('mode', '?')}): {s.get('inserted', 0)} new, "
                  f"{s.get('updated', s.get('enriched', 0))} updated")
        except Exception as e:
            print(f"[CLOSING] ⚠️ placer sync skipped — {e}")

    # ── Phase 0: build the order list ────────────────────────────────────────
    if ORDER_IDS:
        # Explicit-order mode: process exactly these Amazon IDs (skip the report).
        # WB is unknown up front — scrape/harvest pull it from the SC page.
        orders = [{"amazon_order_id": i, "order_status": "", "wb_order_id": ""} for i in ORDER_IDS]
        print(f"[CLOSING] Explicit order mode: {len(orders)} order(s) — {', '.join(ORDER_IDS)}\n")
    else:
        report_path = REPORT_PATH if (REPORT_PATH and os.path.exists(REPORT_PATH)) else None
        if report_path:
            print(f"[CLOSING] Using existing report: {report_path}")
            orders = consolidate_multi_item_orders(filter_wb_orders(parse_report_tsv(report_path)))
        else:
            # The SC report download is intermittently empty (date-picker flakiness:
            # the same date returns thousands of rows one attempt, ~0 the next).
            # Re-request up to 3× when it comes back with no WB orders.
            orders = []
            for attempt in range(1, 4):
                report_path = download_report(cfg["report_port"], days=DAYS,
                                              start_date=START_DATE, end_date=END_DATE)
                if not report_path:
                    print("[CLOSING] ❌ No report — aborting.")
                    return 2
                orders = consolidate_multi_item_orders(filter_wb_orders(parse_report_tsv(report_path)))
                if orders:
                    break
                if attempt < 3:
                    print(f"[CLOSING] ⚠️ Report had 0 WB orders (attempt {attempt}/3) — re-requesting…")
                    time.sleep(5)
        if LIMIT:
            orders = orders[:LIMIT]
        if not orders:
            print("[CLOSING] No WB orders to process.")
            return 0
        print(f"[CLOSING] Orders to process: {len(orders)}")

    # ── Resume: skip orders already closed in a prior run (crash-safe) ───────
    # Explicit-ID mode never skips (you asked for those specifically).
    if RESUME and not FORCE and not ORDER_IDS:
        with get_conn() as c:
            done = {r[0] for r in c.execute(
                "SELECT amazon_order_id FROM closing_orders WHERE order_label IN "
                "('Completed','Refunded','Cancelled','Loss')")}
        if done:
            kept = [o for o in orders if o["amazon_order_id"] not in done]
            print(f"[CLOSING] Resume: {len(orders) - len(kept)} already closed → "
                  f"{len(kept)} left (set CLOSING_FORCE=1 to redo all)")
            orders = kept
        if not orders:
            print("[CLOSING] ✅ Nothing left to process — all closed.")
            return 0

    # ── Priority: orders WITH tracking available complete this pass; orders with no
    #    tracking anywhere (often script-placed whose shipment email isn't archived
    #    yet) can't be confirmed delivered → process them LAST so completions are
    #    front-loaded and the no-tracking stragglers cluster at the end. CLOSING_
    #    SKIP_NO_TRACKING skips them entirely for a fast "close what's closable" pass.
    if PRIORITY and not FORCE and not ORDER_IDS and orders:
        with get_conn() as c:
            tr_aid = {r[0] for r in c.execute(
                "SELECT amazon_order_id FROM closing_orders WHERE tracking_ids IS NOT NULL AND tracking_ids!=''")}
            tr_wb = {r[0] for r in c.execute(
                "SELECT DISTINCT wb_order_id FROM archive_tracking WHERE tracking_ids IS NOT NULL AND tracking_ids!=''")}

        def _has_tracking(o):
            if o.get("amazon_order_id") in tr_aid:
                return True
            wbs = _extract_wb_ids(o.get("merchant_order_id")) or ([o["wb_order_id"]] if o.get("wb_order_id") else [])
            return any(w in tr_wb for w in wbs)

        with_tr = [o for o in orders if _has_tracking(o)]
        no_tr = [o for o in orders if not _has_tracking(o)]
        if SKIP_NO_TRACK:
            print(f"[CLOSING] Skip-no-tracking: processing {len(with_tr)} with tracking, "
                  f"skipping {len(no_tr)} with no tracking anywhere")
            orders = with_tr
        else:
            print(f"[CLOSING] Priority: {len(with_tr)} with-tracking first → {len(no_tr)} no-tracking last")
            orders = with_tr + no_tr
        if not orders:
            print("[CLOSING] ✅ Nothing left to process.")
            _record_run(run_id, orders_processed=0, status="completed")
            return 0

    # ── Preload the rows this run touches, then keep only the orders that can change ──
    fedex_active = False
    if ENABLE_FEDEX and not DRY_RUN:
        _probe = FedExTracker(headless=True)
        fedex_active = bool(_probe.has_official_api or _probe.has_ups_api or browser_tier_allowed())
    diff_stats = None
    with get_conn() as _pc:
        _existing, _placer = _preload(_pc, [o["amazon_order_id"] for o in orders])
        if DIFF and not FORCE and not ORDER_IDS:
            orders, diff_stats = _select_work(orders, _existing, _placer, _UPLOAD_ROWS or {}, _pc,
                                              fedex_active=fedex_active)
            print("[CLOSING] Diff: " + ", ".join(f"{k}={v}" for k, v in diff_stats.items())
                  + "  (CLOSING_DIFF=0 or CLOSING_FORCE=1 to process everything)")
    if not orders:
        print("[CLOSING] ✅ Nothing changed since the last run — no order needs a pass.")
        _record_run(run_id, orders_processed=0, error_details=json.dumps({"diff": diff_stats}),
                    status="completed")
        return 0
    print(f"[CLOSING] Processing {len(orders)} order(s)\n")

    os.makedirs(RUN_LOG_DIR, exist_ok=True)
    run_log_path = os.path.join(RUN_LOG_DIR, f"{run_id}.jsonl")

    # ── Shared resources (built once, reused across batches) ────────────────
    fdriver = None
    fedex = None
    if ENABLE_FEDEX and not DRY_RUN:
        fedex = FedExTracker(headless=FEDEX_HEADLESS)
        if fedex.has_official_api:
            print("[CLOSING] ⚡ Using Official FedEx Developer REST API (zero browser needed)")
        elif not browser_tier_allowed():
            # The page tiers have been refused by Akamai since 19 Sep 2026; launching a
            # Chrome for them only deepens the block. UPS-only credentials still work.
            print("[CLOSING] 🚫 FedEx: no Track API credentials (config/accounts.json → fedex_api) and the "
                  "browser tiers are off — delivery_status is not checked this run "
                  "(set FEDEX_BROWSER=1 to force the old path).")
            if not fedex.has_ups_api:
                fedex = None
        else:
            fdriver = ensure_chrome(cfg["fedex_port"], headless=FEDEX_HEADLESS)
            if fdriver:
                try:
                    fdriver.get("https://www.fedex.com/wtrk/track/")
                    time.sleep(3)
                    fedex.driver = fdriver
                    fedex.prime_session()
                    print("[CLOSING] ✅ FedEx session ready")
                except Exception as e:
                    print(f"[CLOSING] ⚠️ FedEx session prime notice — {e}")
                    fedex.driver = fdriver
            if fedex.driver is None:
                print("=" * 60)
                print("[CLOSING] 🚨 FEDEX DISABLED — Chrome would not start on "
                      f"port {cfg['fedex_port']}. delivery_status will be EMPTY and")
                print("[CLOSING]    NO order can be marked Completed. Fix Chrome and re-run.")
                print("=" * 60)
                fedex = None

    # One SC Chrome (logged in) shared by scrape + messaging + debug page dump.
    need_sc = (ENABLE_MSGS and not DRY_RUN) or DEBUG_EML or ENABLE_SCRAPE
    sc_driver = ensure_chrome(cfg["sc_port"]) if need_sc else None

    scraper = None
    if ENABLE_SCRAPE:
        try:
            from engine.amazon_scraper import AmazonScraper
            scraper = AmazonScraper(driver=sc_driver) if sc_driver \
                else AmazonScraper(port=cfg["sc_port"])
        except Exception as e:
            print(f"[CLOSING] ⚠️ Scraper unavailable — {e}")

    # Gmail services are needed for messaging AND for debug .eml capture.
    need_gmail = (ENABLE_MSGS and not DRY_RUN) or DEBUG_EML
    gmail_services = _get_gmail_services(GMAIL_TOKENS) if need_gmail else {}
    msg_driver = sc_driver   # messaging + debug reuse the one SC window

    # ── Batch loop ──────────────────────────────────────────────────────────
    totals = {"Completed": 0, "Loss": 0, "Incomplete": 0, "Cancelled": 0, "Refunded": 0}
    counters = {"written": 0, "unchanged": 0, "placer_hits": 0, "archive_hits": 0,
                "live_gmail_hits": 0, "fedex_delivered": 0, "messages_sent": 0, "reviews": 0}
    n_batches = (len(orders) + BATCH_SIZE - 1) // BATCH_SIZE
    for bi, batch in enumerate(_chunks(orders, BATCH_SIZE), 1):
        print(f"\n[CLOSING] ── Batch {bi}/{n_batches} ({len(batch)} orders) ──")

        # 1+2: enrich. rconn MUST stay READ-ONLY for the whole batch — it is held
        # open across the slow Gmail/scrape/FedEx work, so if anything WROTE on it
        # (e.g. log_enrichment with conn=rconn) it would hold the DB write-lock for
        # minutes and make a CONCURRENT closing run (other account) collapse to
        # Incomplete. Audit-log writes therefore use their own short connections.
        # Per-order guard: one poison order degrades to Incomplete, never aborts.
        with get_conn() as rconn:
            for o in batch:
                try:
                    enrich_one(rconn, o, run_id, gmail_ok=ENABLE_GMAIL, scraper=scraper)
                except Exception as e:
                    print(f"[CLOSING]   ⚠️ enrich {o.get('amazon_order_id')} failed — {e}")

        # 3: profit — only set keys when computable (None must not clobber prior value).
        # Per-order guard: a single bad value can't abort the batch before persist.
        for o in batch:
            try:
                p, pct = calculate_profit(o)
                if p is not None:
                    o["profit_dollars"] = p
                if pct is not None:
                    o["profit_percent"] = pct
                net = compute_net_profit(o)
                if net is not None:
                    o["net_profit_after_refund"] = net
            except Exception as e:
                print(f"[CLOSING]   ⚠️ profit {o.get('amazon_order_id')} failed — {e}")

        # 4: bulk FedEx (in-memory)
        if fedex is not None:
            try:
                _fx = bulk_fedex_check(batch, fedex) or {}
                counters["fedex_delivered"] += int(_fx.get("delivered") or 0)
            except Exception as e:
                print(f"[CLOSING]   ⚠️ FedEx batch failed — {e}")

        # 5: label (after FedEx) + review_request_sent + on-time/lead-time + derived.
        # Per-order guard: a poison order falls back to Incomplete and is still
        # persisted with whatever was enriched — it can never abort the batch.
        for o in batch:
            try:
                o["order_label"] = determine_label(o)
                o.update(compute_timeliness(o))
                o.update(compute_ship_timeliness(o))
                derive_fields(o)
            except Exception as e:
                print(f"[CLOSING]   ⚠️ label/derive {o.get('amazon_order_id')} failed — {e}")
                o.setdefault("order_label", "Incomplete")
            totals[o["order_label"]] = totals.get(o["order_label"], 0) + 1
            if o.get("enriched_from_placer_db"):
                counters["placer_hits"] += 1
            if o.get("enriched_from_archive"):
                counters["archive_hits"] += 1
            if o.get("enriched_from_live_gmail"):
                counters["live_gmail_hits"] += 1

        # 5b: verbose per-field provenance log
        if VERBOSE:
            for o in batch:
                print_order_verbose(o)

        # 6: persist the 30 in one short txn (rows now exist for messaging)
        _w, _u = persist_batch(batch, run_id)
        counters["written"] += _w
        counters["unchanged"] += _u

        # 6a: append each order's full record to the run JSONL — scalable raw
        # debug trail (every field + provenance, ~1-2 KB/order) without saving
        # 5,500 × 240 KB of HTML. Re-run a specific order with DEBUG_EML for raw eml/html.
        try:
            with open(run_log_path, "a", encoding="utf-8") as rl:
                for o in batch:
                    rec = {k: v for k, v in o.items() if not k.startswith("_page") and k != "_existing"}
                    rl.write(json.dumps(rec, default=str) + "\n")
        except Exception as e:
            print(f"[CLOSING]   ⚠️ run-log write failed: {e}")

        # 6b: debug capture (.eml per type + SC page html + record.json)
        if DEBUG_EML:
            for o in batch:
                dump_order_debug(o, gmail_services, msg_driver, run_id)

        # 7: messages + reviews for delivered orders in this batch
        if ENABLE_MSGS and msg_driver is not None and not DRY_RUN:
            _ms, _rv = send_batch_messages(msg_driver, batch, pd_cfg, gmail_services)
            counters["messages_sent"] += _ms
            counters["reviews"] += _rv

        done = min(bi * BATCH_SIZE, len(orders))
        print(f"[CLOSING] Progress: {done}/{len(orders)} | "
              f"Completed={totals['Completed']} Loss={totals['Loss']} "
              f"Incomplete={totals['Incomplete']} Cancelled={totals['Cancelled']}")

    for d in (fdriver, sc_driver):   # scraper shares sc_driver; don't double-quit
        release_chrome(d)            # quits only Chromes WE launched; detaches from yours

    _secs = round(time.time() - _t_run0, 1)
    _record_run(run_id, orders_processed=len(orders), orders_enriched=counters["written"],
                archive_hits=counters["archive_hits"], live_gmail_hits=counters["live_gmail_hits"],
                placer_db_hits=counters["placer_hits"], fedex_hits=counters["fedex_delivered"],
                messages_sent=counters["messages_sent"], reviews_requested=counters["reviews"],
                error_details=json.dumps({"diff": diff_stats, "totals": totals,
                                          "unchanged": counters["unchanged"], "seconds": _secs}),
                status="completed")
    print("\n" + "=" * 60)
    print(f"[CLOSING] ✅ DONE — {len(orders)} orders in {_secs}s | written={counters['written']} "
          f"unchanged={counters['unchanged']} | "
          f"Completed={totals['Completed']} Loss={totals['Loss']} "
          f"Incomplete={totals['Incomplete']} Cancelled={totals['Cancelled']}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
