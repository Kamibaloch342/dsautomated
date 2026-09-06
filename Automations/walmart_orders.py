#!/usr/bin/env python3
"""
walmart_orders.py — Walmart → Zoro order placer (manual + Seller Center pipeline).

The owner resells on Walmart. Orders reach this runner two ways:
  • MANUAL: typed into the GUI launcher → Walmart page grid → data/walmart_queue.json.
  • SCRAPED (WALMART_SCRAPE=1): a SECOND dedicated Chrome sits logged into
    seller.walmart.com (attach-only, port 9362); engine/walmart_sc.py scrapes
    the Orders → Unshipped tab + each PO's detail page, auto-builds queue rows
    (source='scraped', earnings computed, address parsed), and the normal
    placement loop below runs unchanged.

Either way, orders are placed on Zoro using the EXISTING proven checkout engine
(checkout.true_api.place_order_fast_true_api — the same money path the Amazon
placer uses). Nothing about checkout is reimplemented here.

THE maintained log is the walmart_orders table in data/automations_unified.db —
every row the pipeline touches (scraped or manual) is upserted there
(non-destructive: a filled wb/cost is never blanked). The queue JSON stays the
GUI⇄runner contract; the table is the source of truth for history + dashboard.

Earnings model: earnings = round((item_price + shipping_fee) * (1 - 12%), 2)
(WALMART_REFERRAL_PCT, env-overridable). After a WB success,
profit = earnings - Zoro checkout total (zoro_cost NULL for closing to backfill
when checkout returns no total).

Safety model (mirrors engine/orders.py):
  • Idempotent queue: only rows with status=="pending" and no WB are touched.
  • Gmail duplicate check by address — FAIL-CLOSED (an errored check means
    "do NOT place", never "no duplicate found").
  • Belt-and-braces LOCAL dup check: walmart_orders is also checked for the
    same PO# or the same normalized street+zip already placed — fail-closed.
  • utils.placement_lock claim keyed "WALMART:<row id>" is RESERVED before any
    money is spent; released only on the placer's _SAFE_PREPAYMENT statuses,
    kept (FAILED_KEPT) on any unknown/post-submit failure.
  • ATTACH-ONLY Chrome (both of them): Zoro Chrome on WALMART_PORT (9361) and
    Seller Center Chrome on WALMART_SC_PORT (9362) must already be running —
    this runner NEVER launches a browser. Missing/logged-out Chrome → exit 2
    with instructions, before any money path is touched.
  • 'Start processing' in Seller Center is clicked ONCE after a WB success
    (WALMART_START_PROCESSING=1, default). A failed click NEVER fails the row —
    the Zoro order is already placed; it just stays status='placed' + warning.

Env:
  WALMART_PORT              Zoro Chrome port (default: accounts.json walmart.zoro_port, 9361)
  WALMART_SC_PORT           Seller Center Chrome port (default: walmart.sc_port, 9362)
  WALMART_SCRAPE=1          Seller Center mode: scrape Unshipped → build queue
                            rows → then the normal placement loop.
  WALMART_START_PROCESSING  default 1 — click 'Start processing' after WB success
                            on rows that have a Walmart PO#.
  WALMART_REFERRAL_PCT      Walmart referral fee for earnings calc (default 0.12).
  WALMART_DRY=1             Dry run (covers scrape mode too): scrape + parse +
                            earnings + estimated cart cost, NO placement, NO
                            Start-processing click, queue/DB NOT modified.
  WALMART_QUEUE             Override queue path (default: data/walmart_queue.json)

Usage:
  python walmart_orders.py                    # manual mode: pending queue rows
  WALMART_SCRAPE=1 python walmart_orders.py   # full Seller Center pipeline
  python walmart_orders.py --test-parser      # address-parser unit tests
  python walmart_orders.py --test-units       # zoro-code-from-SKU + earnings tests
"""

import os
import re
import sys
import json
import socket
import time
import functools
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

print = functools.partial(print, flush=True)

DEFAULT_QUEUE_PATH = os.path.join(_SCRIPT_DIR, "data", "walmart_queue.json")
WALMART_LOG_PATH = os.path.join(_SCRIPT_DIR, "data", "rajar_logs", "walmart_log.txt")

# Same guard pattern the placer uses (platforms/amazon.py scrape_amazon_address).
_PO_BOX_RE = re.compile(r'\bP\.?\s*O\.?\s*BOX\b')

# Statuses detected BEFORE Zoro payment — safe to release the placement-lock
# claim and allow a retry. Mirrors engine/orders.py _SAFE_PREPAYMENT (plus
# LOW_PROFIT / SPLIT_RETRY, which the engine also releases/aborts pre-payment).
_SAFE_PREPAYMENT = ("OOS", "STATE_RESTRICTED", "PO_BOX_RESTRICTED", "NET30_RESTRICTED",
                    "NO_COUPONS_SKIP", "COUPON FAIL", "LOW_PROFIT", "SPLIT_RETRY")

# Walmart referral fee (12%): earnings = (item_price + shipping_fee) * (1 - pct).
# Named constant, env-overridable (WALMART_REFERRAL_PCT=0.12).
WALMART_REFERRAL_PCT = float(os.environ.get("WALMART_REFERRAL_PCT") or "0.12")


def compute_earnings(item_price, shipping_fee=0.0, referral_pct=None):
    """Walmart payout for one order line:
    round((item_price + shipping_fee) * (1 - WALMART_REFERRAL_PCT), 2).
    Returns None when item_price is unknown (never guesses money)."""
    if item_price is None:
        return None
    pct = WALMART_REFERRAL_PCT if referral_pct is None else referral_pct
    try:
        return round((float(item_price) + float(shipping_fee or 0.0)) * (1.0 - pct), 2)
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════
#  ADDRESS PARSER  (pure — no selenium/config imports, unit-testable)
# ══════════════════════════════════════════════════════════════════

# City line: "Los Angeles, CA 90017" or "City, ST 90017-1234" (must END the line)
_CITY_RE = re.compile(r'^(.*?),\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)\s*$')
# A phone line contains ONLY digits + phone punctuation (stray *, dashes, dots,
# parens, spaces, leading +1) — never letters.
_PHONE_CHARS_RE = re.compile(r'^[\d\s()*.+\-]+$')


def _extract_phone(line):
    """Return a normalized 10-digit phone from a line, or '' if it isn't one.
    Strips non-digits, drops a leading 1 (from +1 / 1-...)."""
    s = (line or "").strip()
    if not s or not _PHONE_CHARS_RE.match(s):
        return ""
    digits = re.sub(r"\D", "", s)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def parse_walmart_address(raw):
    """Parse a pasted Walmart shipping address block.

    Rules:
      • first non-empty line = name
      • last line matching a phone pattern = phone (optional; normalized to
        10 digits, leading 1 dropped, stray * / dashes / dots / +1 tolerated)
      • the line matching  , ST 12345[-6789]$  = city/state/zip
      • everything between name and the city line = street (line 1 = street,
        any further lines — "Apt 4B" / "Unit 12" / "STE 100" — joined into unit)
      • blank lines / trailing spaces ignored

    Returns {"name","street","unit","city","state","zip","phone"} or None when
    the block has no city/state/zip line (or no street / no name).
    """
    if not raw or not str(raw).strip():
        return None
    lines = [ln.strip() for ln in str(raw).splitlines()]
    lines = [ln for ln in lines if ln]
    if len(lines) < 3:
        return None

    name = lines[0]
    rest = lines[1:]

    # Phone: LAST line matching the phone pattern (usually after the city line).
    phone = ""
    phone_idx = None
    for i in range(len(rest) - 1, -1, -1):
        p = _extract_phone(rest[i])
        if p:
            phone, phone_idx = p, i
            break
    if phone_idx is not None:
        rest = rest[:phone_idx] + rest[phone_idx + 1:]

    # City/state/zip: scan from the END (street lines can contain commas but
    # can't match ", ST 12345$").
    city_idx = None
    for i in range(len(rest) - 1, -1, -1):
        if _CITY_RE.match(rest[i]):
            city_idx = i
            break
    if city_idx is None:
        return None
    m = _CITY_RE.match(rest[city_idx])
    city = m.group(1).strip()
    state = m.group(2).upper()
    zip_code = m.group(3)
    if not city:
        return None

    street_lines = rest[:city_idx]
    if not street_lines:
        return None  # nothing between name and city line — unusable

    return {
        "name": name,
        "street": street_lines[0],
        "unit": ", ".join(street_lines[1:]),
        "city": city,
        "state": state,
        "zip": zip_code,
        "phone": phone,
    }


# ══════════════════════════════════════════════════════════════════
#  PARSER UNIT TESTS  (python walmart_orders.py --test-parser)
# ══════════════════════════════════════════════════════════════════

def _run_parser_tests():
    tests = [
        # 1. The exact reference sample (4 lines, phone with stray *)
        ("sample-4line",
         "Andrea Alcorn\n810 south Flower St, 316\nLos Angeles, CA 90017\n(213) 915-2963*",
         {"name": "Andrea Alcorn", "street": "810 south Flower St, 316", "unit": "",
          "city": "Los Angeles", "state": "CA", "zip": "90017", "phone": "2139152963"}),
        # 2. 5 lines with an Apt unit line + dashed phone
        ("5line-apt",
         "John Smith\n123 Main St\nApt 4B\nSpringfield, IL 62704\n217-555-0182",
         {"name": "John Smith", "street": "123 Main St", "unit": "Apt 4B",
          "city": "Springfield", "state": "IL", "zip": "62704", "phone": "2175550182"}),
        # 3. ZIP+4 and a STE unit line
        ("zip+4-ste",
         "Acme Corp\n900 Commerce Way\nSTE 100\nDallas, TX 75001-1234\n(972) 555-1000",
         {"name": "Acme Corp", "street": "900 Commerce Way", "unit": "STE 100",
          "city": "Dallas", "state": "TX", "zip": "75001-1234", "phone": "9725551000"}),
        # 4. Phone with +1 and dots
        ("plus1-dots",
         "Maria Lopez\n55 Ocean Ave\nMiami, FL 33139\n+1 305.555.7788",
         {"name": "Maria Lopez", "street": "55 Ocean Ave", "unit": "",
          "city": "Miami", "state": "FL", "zip": "33139", "phone": "3055557788"}),
        # 5. No phone at all (3 lines)
        ("no-phone",
         "Bob Ray\n42 Elm Street\nDenver, CO 80202",
         {"name": "Bob Ray", "street": "42 Elm Street", "unit": "",
          "city": "Denver", "state": "CO", "zip": "80202", "phone": ""}),
        # 6. Extra blank lines + trailing spaces everywhere
        ("blank-lines",
         "  Jane Doe  \n\n  77 Pine Rd  \n\n  Unit 12 \n\n Portland, OR 97201 \n\n 1 (503) 555-2200 \n",
         {"name": "Jane Doe", "street": "77 Pine Rd", "unit": "Unit 12",
          "city": "Portland", "state": "OR", "zip": "97201", "phone": "5035552200"}),
        # 7. No city/state/zip line → None
        ("no-city-line",
         "Sam Hill\n10 Nowhere Blvd\n(404) 555-1234",
         None),
        # 8. City line directly after name (no street line) → None
        ("no-street",
         "Tim Cook\nCupertino, CA 95014\n(408) 555-1010",
         None),
        # 9. Two extra street lines are joined into unit with ', '
        ("multi-unit",
         "Zed Corp\n1 Warehouse Pkwy\nBuilding 7\nDock 3\nReno, NV 89501",
         {"name": "Zed Corp", "street": "1 Warehouse Pkwy", "unit": "Building 7, Dock 3",
          "city": "Reno", "state": "NV", "zip": "89501", "phone": ""}),
    ]

    failed = 0
    for label, raw, expected in tests:
        got = parse_walmart_address(raw)
        if got == expected:
            print(f"  ✅ {label}")
        else:
            failed += 1
            print(f"  ❌ {label}")
            print(f"     expected: {expected}")
            print(f"     got:      {got}")
    total = len(tests)
    print(f"\n{'✅' if not failed else '❌'} parser tests: {total - failed}/{total} passed")
    return failed == 0


def _run_unit_tests():
    """Unit tests for the Seller Center pipeline pure helpers:
    zoro-code-from-SKU extraction (the owner's 3 real samples) + earnings calc."""
    from engine.walmart_sc import zoro_code_from_walmart_sku

    sku_tests = [
        # (label, walmart_sku, expected zoro code) — first 3 are real samples
        ("sample-spaces",   "HOA- G1967707 -1",   "G1967707"),
        ("sample-plain",    "HON-G4302365",       "G4302365"),
        ("sample-longest",  "HO- G416487430-6",   "G416487430"),
        ("no-code",         "ABC-12345",          ""),
        ("too-short",       "X-G12345-2",         ""),      # G+5 digits: not a code
        ("two-codes",       "G1234567 G123456789", "G123456789"),  # longest wins
        ("lowercase",       "hoa-g1967707-1",     "G1967707"),
        ("empty",           "",                   ""),
        ("none",            None,                 ""),
    ]
    earn_tests = [
        # (label, item_price, shipping_fee, pct-override, expected)
        ("ref-28.99",   28.99, 0.0,  None, 25.51),   # the spec sample
        ("with-ship",   28.99, 5.00, None, 29.91),   # (33.99)*0.88
        ("rounding",    10.00, 0.0,  None, 8.80),
        ("pct-override", 100.0, 0.0, 0.15, 85.00),
        ("none-price",  None,  0.0,  None, None),    # unknown price → no guess
        ("none-ship",   28.99, None, None, 25.51),   # missing fee treated as 0
    ]

    failed = 0
    print("── zoro_code_from_walmart_sku ──")
    for label, raw, expected in sku_tests:
        got = zoro_code_from_walmart_sku(raw)
        if got == expected:
            print(f"  ✅ {label}: {raw!r} → {got!r}")
        else:
            failed += 1
            print(f"  ❌ {label}: {raw!r} → {got!r} (expected {expected!r})")

    print("── compute_earnings ──")
    for label, price, ship, pct, expected in earn_tests:
        got = compute_earnings(price, ship, referral_pct=pct)
        if got == expected:
            print(f"  ✅ {label}: ({price}, {ship}) → {got}")
        else:
            failed += 1
            print(f"  ❌ {label}: ({price}, {ship}) → {got} (expected {expected})")

    total = len(sku_tests) + len(earn_tests)
    print(f"\n{'✅' if not failed else '❌'} unit tests: {total - failed}/{total} passed")
    return failed == 0


# ══════════════════════════════════════════════════════════════════
#  QUEUE I/O
# ══════════════════════════════════════════════════════════════════

def load_queue(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"❌ Could not read queue {path}: {e}")
        return None


def save_queue(path, rows):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"⚠️  Could not save queue: {e}")


# ══════════════════════════════════════════════════════════════════
#  DATA LOADERS  (same sheets, same semantics as the placer)
# ══════════════════════════════════════════════════════════════════

def _sheets_service():
    """Read-only Google Sheets client (same service account as engine/orders.py)."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from utils.config import GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE
    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _norm_sku(s):
    """Normalize a Walmart SKU for matching: collapse all whitespace, uppercase.
    'HOA- G1967707 -1' and 'HOA-G1967707-1' both → 'HOA-G1967707-1'. This makes
    the sheet match tolerant of the inconsistent spacing Walmart shows."""
    return re.sub(r"\s+", "", str(s or "")).upper()


def load_walmart_sku_map():
    """The Walmart_SKUs_Verified tab: Walmart_SKU (A) → {zoro_code, qty} — the
    SINGLE authority for both the Zoro code AND the per-unit multiple, keyed by
    the FULL Walmart SKU (the SAME Zoro code appears on different pack sizes, so
    the Walmart SKU is the only unambiguous key — that's why Amazon keys by ASIN
    and Walmart must key by its own SKU).

    Matching is whitespace/case-insensitive (_norm_sku). A row with a bad/missing
    Zoro code or qty is skipped. Returns {norm_sku: {'zoro_code','qty','raw_sku'}},
    or None if the sheet cannot be read (caller must fail closed on None)."""
    from utils.config import GOOGLE_SHEET_ID, GOOGLE_SHEET_WALMART_TAB
    print(f"\n📦 Loading Walmart SKU map ('{GOOGLE_SHEET_WALMART_TAB}' tab)...")
    try:
        svc = _sheets_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{GOOGLE_SHEET_WALMART_TAB}'!A:C",
        ).execute()
        rows = result.get("values", [])
        if len(rows) < 2:
            print("  ⚠️  Walmart SKU sheet returned no data rows")
            return {}
        out, dupes = {}, 0
        for row in rows[1:]:
            row = list(row) + [""] * (3 - len(row))
            raw_sku, code, qty_s = str(row[0]).strip(), str(row[1]).strip(), str(row[2]).strip()
            if not raw_sku:
                continue
            code = code.upper()
            if not re.match(r"^G\d+(?:\s*,\s*G\d+)*$", code):
                print(f"  ⚠️  '{raw_sku}': bad/blank Zoro code '{code}' — row skipped")
                continue
            try:
                qty = int(float(qty_s or 1))
            except ValueError:
                qty = 1
            if qty < 1:
                qty = 1
            key = _norm_sku(raw_sku)
            if key in out and out[key]["zoro_code"] != code:
                print(f"  ⚠️  duplicate Walmart SKU '{raw_sku}' with differing code "
                      f"({out[key]['zoro_code']} vs {code}) — keeping first")
                dupes += 1
                continue
            out[key] = {"zoro_code": code, "qty": qty, "raw_sku": raw_sku}
        print(f"  ✅ {len(out)} Walmart SKU(s) mapped"
              + (f" ({dupes} duplicate(s) ignored)" if dupes else ""))
        return out
    except Exception as e:
        print(f"  ❌ Walmart SKU sheet failed to load: {e}")
        return None


def load_coupons():
    """Coupon lists loaded the same way the placer's load_coupon_codes() loads
    them: Coupons tab A:B — rows WITH a pct/fixed value in column B are priority
    (reusable), the rest regular single-use; codes marked USED/INVALID in the
    coupon metadata are skipped; legacy bot_config.json priority coupons are
    appended as fallback. Checkout applies them first-match-wins: priority
    first, then regular (preferred none).

    Returns (priority_codes, regular_codes) or None on sheet failure
    (caller must STOP — same fail-closed rule as the placer)."""
    from utils.config import GOOGLE_SHEET_ID, GOOGLE_SHEET_COUPON_TAB
    print("\n🏷️  Loading coupon sheet...")
    try:
        from utils.helpers import load_coupon_metadata
        metadata = load_coupon_metadata() or {}
    except Exception:
        metadata = {}

    try:
        svc = _sheets_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{GOOGLE_SHEET_COUPON_TAB}'!A:B",
        ).execute()
        rows = result.get("values", [])
        priority, regular = [], []
        for row in rows[1:]:
            if not row or not str(row[0]).strip():
                continue
            code = str(row[0]).strip()
            val_str = str(row[1]).strip() if len(row) > 1 else ""
            is_priority = bool(re.match(r'^(?:FIXED:)?(\d+):(\d+)$', val_str))
            if not is_priority:
                try:
                    is_priority = float(val_str) > 0
                except (ValueError, TypeError):
                    is_priority = False
            if is_priority:
                if metadata.get(code) == "INVALID":
                    continue
                if code not in priority:
                    priority.append(code)
            else:
                if metadata.get(code) in ("USED", "INVALID"):
                    continue
                regular.append(code)

        # Legacy priority coupons from bot_config.json (same fallback the placer keeps)
        try:
            from utils.config import load_gui_config, get_priority_coupons
            for lc in get_priority_coupons(load_gui_config()):
                if lc and lc not in priority and metadata.get(lc) != "USED":
                    priority.append(lc)
        except Exception:
            pass

        print(f"  ✅ {len(priority)} priority + {len(regular)} regular coupons")
        return priority, regular
    except Exception as e:
        print(f"  ❌ CRITICAL: coupon sheet failed to load ({e})")
        return None


def build_duplicate_services():
    """Authenticate every duplicate-check inbox from the config list
    (utils.config.DUPLICATE_CHECK_LIST ← config/accounts.json union of
    zoro_accounts + gmail_accounts — no hardcoded lists here).
    Returns (services, failed_accounts)."""
    from utils.config import DUPLICATE_CHECK_LIST
    from platforms.gmail import authenticate_gmail_account
    services, failed = [], []
    print(f"\n🔐 Authenticating {len(DUPLICATE_CHECK_LIST)} duplicate-check Gmail inbox(es)...")
    for acct in DUPLICATE_CHECK_LIST:
        try:
            svc = authenticate_gmail_account(acct)
            if svc:
                services.append(svc)
            else:
                failed.append(acct)
        except Exception as e:
            failed.append(acct)
            print(f"  ⚠️  Gmail auth failed for {acct}: {e}")
    print(f"  ✅ {len(services)} inbox(es) ready" +
          (f"  (⚠️ {len(failed)} failed: {', '.join(failed)})" if failed else ""))
    return services, failed


# ══════════════════════════════════════════════════════════════════
#  CHROME — ATTACH ONLY (never launches a browser)
# ══════════════════════════════════════════════════════════════════

def attach_chrome(port):
    """Attach to the already-running dedicated Walmart-Zoro Chrome.
    Reuses the closing pipeline's proven connector (engine.amazon_scraper
    _connect_chrome — pure debuggerAddress attach). Returns driver or None."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    port_open = s.connect_ex(("127.0.0.1", port)) == 0
    s.close()
    if not port_open:
        return None
    try:
        from engine.amazon_scraper import _connect_chrome
        return _connect_chrome(port)
    except Exception as e:
        print(f"❌ Chrome attach on port {port} failed: {e}")
        return None


# Fixed desktop UA (env WALMART_UA overrides): the bare automation profile was
# tripping Walmart's bot wall; Zoro accepts this UA fine.
WALMART_UA = os.environ.get(
    "WALMART_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")


def _focus_tab(driver, domain, open_url=None):
    """ONE browser, multiple tabs (like the Amazon placer): switch to the tab
    already on `domain`, or open a new tab there. Returns the driver focused."""
    try:
        cur = driver.current_window_handle
    except Exception:
        cur = None
    for h in driver.window_handles:
        try:
            driver.switch_to.window(h)
            if domain in (driver.current_url or ""):
                return driver
        except Exception:
            continue
    try:
        driver.switch_to.new_window("tab")
    except Exception:                       # older selenium fallback
        driver.execute_script("window.open('about:blank','_blank')")
        driver.switch_to.window(driver.window_handles[-1])
    driver.get(open_url or f"https://{domain}")
    time.sleep(2)
    return driver


def ensure_chrome_walmart(port, profile_dir, url):
    """ONE-BUTTON UX: attach if a Chrome is already on `port`, otherwise LAUNCH
    one with the persistent profile (logins survive restarts) opened at `url`,
    then attach. Returns driver or None after retries."""
    d = attach_chrome(port)
    if d is not None:
        _harden_driver(d)
        return d
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    prof = os.path.expanduser(f"~/.closing_chrome_profiles/{profile_dir}")
    os.makedirs(prof, exist_ok=True)
    for lockf in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            os.remove(os.path.join(prof, lockf))
        except OSError:
            pass
    print(f"🚀 Launching Chrome (port {port}, profile {profile_dir}) → {url}")
    try:
        import subprocess
        # NOTE: deliberately NOT passing --disable-blink-features=AutomationControlled
        # — on Chrome 147 it produces the visible 'unsupported command-line flag'
        # banner that Walmart's PerimeterX reads as an automation signal (it was
        # literally on the blocked page). navigator.webdriver is hidden via CDP in
        # _harden_driver instead, which leaves no banner.
        try:
            from utils.helpers import mark_profile_clean_exit
            mark_profile_clean_exit(prof)
        except Exception:
            pass
        _soft = ["--renderer-process-limit=4", "--disable-background-networking",
                 "--disable-sync", "--disable-default-apps",
                 "--disable-component-update", "--metrics-recording-only",
                 "--mute-audio", "--disk-cache-size=104857600"]
        subprocess.Popen([chrome, f"--remote-debugging-port={port}",
                          "--hide-crash-restore-bubble",
                          f"--user-data-dir={prof}", "--no-first-run",
                          f"--user-agent={WALMART_UA}",
                          "--no-default-browser-check", "--start-maximized", *_soft, url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"❌ Could not launch Chrome: {e}")
        return None
    for _ in range(8):
        time.sleep(3)
        d = attach_chrome(port)
        if d is not None:
            _harden_driver(d)
            return d
    return None


def _harden_driver(driver):
    """Apply anti-bot overrides via CDP — works even when we ATTACHED to an
    already-running Chrome (where the --user-agent launch flag has no effect):
      1) force the desktop UA on every request (Network.setUserAgentOverride)
      2) hide navigator.webdriver=true — the #1 tell chromedriver leaves — on
         every new document (Page.addScriptToEvaluateOnNewDocument).
    Best-effort: any failure is non-fatal (older Chrome / CDP disabled)."""
    try:
        driver.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": WALMART_UA})
    except Exception as e:
        print(f"   [harden] UA override skipped: {e}")
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        })
    except Exception as e:
        print(f"   [harden] webdriver-hide skipped: {e}")


def bot_check(port=None):
    """Self-check the user can run: open seller.walmart.com in the (auto-launched,
    hardened) Chrome and report what the page actually sees + whether the bot wall
    is up. Prints navigator.webdriver / UA as the page reads them. Places nothing."""
    from utils.accounts_config import walmart as _wcfg
    cfg = _wcfg()
    port = int(port or cfg.get("zoro_port") or 9361)
    prof = cfg.get("profile_dir") or f"port_{port}"
    print("═" * 66)
    print("🔎 WALMART BOT-DETECTION SELF-CHECK (nothing is placed)")
    print("═" * 66)
    driver = ensure_chrome_walmart(port, prof, "https://seller.walmart.com")
    if driver is None:
        print("❌ Could not open Chrome on port", port); return 2
    try:
        driver.get("https://seller.walmart.com")
    except Exception:
        pass
    time.sleep(6)
    try:
        wd = driver.execute_script("return navigator.webdriver")
        ua = driver.execute_script("return navigator.userAgent")
    except Exception:
        wd = ua = "?"
    from engine import walmart_sc
    walled = False
    try:
        walled = walmart_sc.is_login_page(driver)
    except Exception:
        pass
    url = ""
    try:
        url = driver.current_url
    except Exception:
        pass
    print(f"  navigator.webdriver : {wd}   (want: None/False — True = detectable)")
    print(f"  navigator.userAgent : {ua}")
    print(f"  current URL         : {url}")
    if walled or "blocked" in (url or "").lower():
        print("  🚨 BOT WALL / LOGIN PAGE IS UP — the automation flags were not enough,")
        print("     OR this profile isn't logged in. Log in manually in this window once")
        print("     (pass the 'Robot or human?' check); the warmed profile then persists.")
        return 1
    print("  ✅ NO bot wall detected — Seller Center loaded normally.")
    return 0


def _wait_for_sc_login(sc_driver, port, max_mins=12):
    """The SC window is open but on a login page / robot wall. Tell the user
    ONCE, then poll every 10s until they've logged in (profile persists it, so
    this is a one-time step). Returns True when logged in, False on timeout."""
    from engine import walmart_sc
    print("═" * 66)
    print(f"🔑 ACTION NEEDED — log into seller.walmart.com in the Chrome window")
    print(f"   just opened (port {port}). Complete any 'Robot or human?' check.")
    print(f"   I'll continue automatically as soon as you're in (waiting up to {max_mins} min)…")
    print("═" * 66)
    waited = 0
    while waited < max_mins * 60:
        time.sleep(10); waited += 10
        try:
            if not walmart_sc.is_login_page(sc_driver):
                print(f"  ✅ Logged in — continuing ({waited}s).")
                return True
        except Exception:
            pass
        if waited % 60 == 0:
            print(f"  …still waiting for Seller Center login ({waited//60}/{max_mins} min)")
    return False


def _print_no_chrome_and_exit(port):
    from utils.accounts_config import walmart as walmart_cfg
    cfg = walmart_cfg()
    if int(cfg.get("zoro_port") or 0) == int(port):
        profile_dir = cfg.get("profile_dir") or f"port_{port}"
    else:  # env-overridden port — suggest the matching per-port profile dir
        profile_dir = f"port_{port}"
    print("═" * 66)
    print(f"🛑 NO CHROME LISTENING ON PORT {port}")
    print("   This runner ATTACHES ONLY — it never launches a browser itself.")
    print("   To fix:")
    print("     1) Click '🚀 Launch Walmart Chrome' on the launcher's Walmart page")
    print("        (or run manually:")
    print(f"         /Applications/Google Chrome.app/Contents/MacOS/Google\\ Chrome \\")
    print(f"           --remote-debugging-port={port} \\")
    print(f"           --user-data-dir=$HOME/.closing_chrome_profiles/{profile_dir} )")
    print("     2) Log into zoro.com with the dedicated Walmart Zoro account")
    print("        (saved credit card required for checkout).")
    print("     3) Re-run Walmart Orders.")
    print("   No queue row was touched.")
    print("═" * 66)
    sys.exit(2)


def _sc_profile_dir_for(port):
    from utils.accounts_config import walmart as walmart_cfg
    cfg = walmart_cfg()
    if int(cfg.get("sc_port") or 0) == int(port):
        return cfg.get("sc_profile_dir") or f"port_{port}"
    return f"port_{port}"  # env-overridden port — matching per-port profile dir


def _print_no_sc_chrome_and_exit(port):
    profile_dir = _sc_profile_dir_for(port)
    print("═" * 66)
    print(f"🛑 NO WALMART SELLER CENTER CHROME LISTENING ON PORT {port}")
    print("   This runner ATTACHES ONLY — it never launches a browser itself.")
    print("   To fix:")
    print("     1) Click '🚀 Launch Walmart SC Chrome' on the launcher's Walmart page")
    print("        (or run manually:")
    print(f"         /Applications/Google Chrome.app/Contents/MacOS/Google\\ Chrome \\")
    print(f"           --remote-debugging-port={port} \\")
    print(f"           --user-data-dir=$HOME/.closing_chrome_profiles/{profile_dir} )")
    print("     2) Log into seller.walmart.com (Walmart Seller Center).")
    print("     3) Re-run with the '🔄 Scrape Unshipped' button (or WALMART_SCRAPE=1).")
    print("   No queue row was touched.")
    print("═" * 66)
    sys.exit(2)


def _print_sc_login_required_and_exit(port):
    print("═" * 66)
    print(f"🛑 WALMART SELLER CENTER IS NOT LOGGED IN (Chrome on port {port})")
    print("   The SC Chrome is running but sits on a login page or a")
    print("   'Robot or human?' verification wall — a human is needed once.")
    print("   To fix:")
    print("     1) Switch to the Walmart SC Chrome window (port "
          f"{port}, profile {_sc_profile_dir_for(port)})")
    print("     2) Complete the verification and/or log into seller.walmart.com")
    print("        manually (persists in the dedicated profile — one-time step).")
    print("     3) Re-run with the '🔄 Scrape Unshipped' button (or WALMART_SCRAPE=1).")
    print("   Nothing was scraped or placed.")
    print("═" * 66)
    sys.exit(2)


# ══════════════════════════════════════════════════════════════════
#  ADDRESS → CHECKOUT DICT
# ══════════════════════════════════════════════════════════════════

def to_checkout_address(parsed):
    """Map the parsed Walmart address into the exact addr dict the checkout
    expects (same shape engine/orders.py feeds place_order_fast_true_api:
    name / original_name / street_address / street_address_2 / city /
    state_abbr / state_full_name / zip_code / phone), reusing the placer's own
    validate_and_prepare_address for name/phone normalization.
    Returns (addr, None) or (None, reason)."""
    from utils.config import STATE_MAP
    from platforms.amazon import validate_and_prepare_address
    state = STATE_MAP.get(parsed["state"].upper())
    if not state:
        return None, f"unknown state '{parsed['state']}'"
    data = {
        "original_name": parsed["name"],
        "street_address": parsed["street"],
        "street_address_2": parsed.get("unit", ""),
        "city": parsed["city"],
        "state_abbr": state,
        "zip_code": parsed["zip"],
        "phone": parsed.get("phone") or "NOT FOUND",
        "skip_order": False,
    }
    addr = validate_and_prepare_address(data)
    if not addr:
        return None, "address validation failed"
    return addr, None


# ══════════════════════════════════════════════════════════════════
#  WALMART_ORDERS TABLE  (data/automations_unified.db — THE maintained log)
# ══════════════════════════════════════════════════════════════════

# Columns whose FILLED values must never be blanked by a later upsert
# (non-destructive rule: never blank a filled wb/cost/earnings/address).
# status/error/updated_at always take the newest provided value.
_WM_DB_PRESERVE = frozenset((
    "order_number", "order_date", "ship_by", "deliver_by", "title",
    "walmart_sku", "zoro_code", "wm_qty", "zoro_qty",
    "item_price", "shipping_fee", "tax", "order_total", "earnings",
    "ship_name", "ship_street", "ship_city", "ship_state", "ship_zip",
    "ship_phone", "wb_order_id", "zoro_cost", "profit", "coupon_used",
    "placed_at", "source",
))
_WM_DB_COLS = (
    "po_number", "order_number", "order_date", "ship_by", "deliver_by",
    "title", "walmart_sku", "zoro_code", "wm_qty", "zoro_qty",
    "item_price", "shipping_fee", "tax", "order_total", "earnings",
    "ship_name", "ship_street", "ship_city", "ship_state", "ship_zip",
    "ship_phone", "wb_order_id", "zoro_cost", "profit", "status", "error",
    "source", "coupon_used", "placed_at", "updated_at",
)


def _ensure_walmart_table():
    """Idempotent create of the walmart_orders table. Returns True/False."""
    try:
        from utils.unified_db import init_walmart_orders
        init_walmart_orders()
        return True
    except Exception as e:
        print(f"⚠️  walmart_orders table init failed: {e}")
        return False


def upsert_walmart_order(data, db_path=None):
    """Non-destructive upsert keyed on po_number. Only the columns present in
    `data` are written; _WM_DB_PRESERVE columns keep their existing value when
    the incoming one is NULL/'' (so a re-scrape can never blank a filled
    wb_order_id / zoro_cost / earnings). Returns True on success."""
    po = str(data.get("po_number") or "").strip()
    if not po:
        return False
    cols = [c for c in _WM_DB_COLS if c in data]
    if "po_number" not in cols:
        cols.insert(0, "po_number")
    if "updated_at" not in cols:
        cols.append("updated_at")
    row = {c: data.get(c) for c in cols}
    row["po_number"] = po
    row["updated_at"] = datetime.now().isoformat(timespec="seconds")
    sets = []
    for c in cols:
        if c == "po_number":
            continue
        if c in _WM_DB_PRESERVE:
            sets.append(f"{c}=COALESCE(NULLIF(excluded.{c}, ''), walmart_orders.{c})")
        else:
            sets.append(f"{c}=excluded.{c}")
    sql = (f"INSERT INTO walmart_orders ({', '.join(cols)}) "
           f"VALUES ({', '.join('?' for _ in cols)}) "
           f"ON CONFLICT(po_number) DO UPDATE SET {', '.join(sets)}")
    try:
        from utils.unified_db import get_conn
        with get_conn(db_path) as conn:
            conn.execute(sql, [row[c] for c in cols])
        return True
    except Exception as e:
        print(f"   ⚠️  walmart_orders upsert failed for {po}: {e}")
        return False


def _row_db_key(row):
    """walmart_orders primary key for a queue row: the real Walmart PO# when
    known, else 'MANUAL:<row id>' for GUI rows entered without one."""
    po = str(row.get("walmart_po") or "").strip()
    return po if po else f"MANUAL:{str(row.get('id') or '').strip()}"


def _row_to_db_record(row, status=None, error=None, **extra):
    """Map a queue row (+ its parsed address) to a walmart_orders record.
    `extra` lets the caller add wb_order_id/zoro_cost/profit/... at
    placement time."""
    parsed = row.get("parsed") or {}
    rec = {
        "po_number": _row_db_key(row),
        "order_number": row.get("order_number") or "",
        "order_date": row.get("order_date") or "",
        "ship_by": row.get("ship_by") or "",
        "deliver_by": row.get("deliver_by") or "",
        "title": row.get("title") or "",
        "walmart_sku": row.get("walmart_sku") or "",
        "zoro_code": row.get("sku") or "",
        "wm_qty": row.get("wm_qty"),
        "zoro_qty": row.get("zoro_qty"),
        "item_price": row.get("item_price"),
        "shipping_fee": row.get("shipping_fee"),
        "tax": row.get("tax"),
        "order_total": row.get("order_total"),
        "earnings": row.get("earnings"),
        "source": row.get("source") or "manual",
        "status": (status if status is not None else row.get("status")) or "pending",
        "error": error if error is not None else (row.get("error") or ""),
    }
    if parsed:
        unit = parsed.get("unit") or ""
        rec.update({
            "ship_name": parsed.get("name", ""),
            "ship_street": parsed.get("street", "") + (f", {unit}" if unit else ""),
            "ship_city": parsed.get("city", ""),
            "ship_state": parsed.get("state", ""),
            "ship_zip": parsed.get("zip", ""),
            "ship_phone": parsed.get("phone", ""),
        })
    rec.update(extra)
    return rec


def _norm_street(s):
    """Normalization for street comparison: uppercase alphanumerics only."""
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


def db_duplicate_check(po_number, street, zip_code, db_path=None):
    """Belt-and-braces LOCAL duplicate check against walmart_orders: has the
    same PO#, OR the same normalized street+zip, already been PLACED (wb set)?
    Returns ('dup', wb) / ('clear', None) / ('error', None) — callers treat
    'error' as FAIL-CLOSED (do not place)."""
    try:
        from utils.unified_db import get_conn
        with get_conn(db_path) as conn:
            po = str(po_number or "").strip()
            if po:
                r = conn.execute(
                    "SELECT wb_order_id FROM walmart_orders "
                    "WHERE po_number=? AND IFNULL(wb_order_id,'')<>''",
                    (po,)).fetchone()
                if r:
                    return "dup", r["wb_order_id"]
            street_n = _norm_street(street)
            zip5 = str(zip_code or "")[:5]
            if street_n and zip5:
                rows = conn.execute(
                    "SELECT po_number, ship_street, ship_zip, wb_order_id "
                    "FROM walmart_orders WHERE IFNULL(wb_order_id,'')<>''"
                ).fetchall()
                for r in rows:
                    if (str(r["ship_zip"] or "")[:5] == zip5
                            and _norm_street(r["ship_street"]) == street_n):
                        return "dup", r["wb_order_id"]
        return "clear", None
    except Exception as e:
        print(f"   [DB_DUP] walmart_orders check errored: {e}")
        return "error", None


# ══════════════════════════════════════════════════════════════════
#  WALMART ORDER LOG  (order_log.txt style, WALMART marker + row id)
# ══════════════════════════════════════════════════════════════════

def _write_walmart_log(row_id, wb, sku, wm_qty, mult, zoro_qty, addr,
                       checkout_total, used_coupon, timing_seconds):
    """Append a one-liner in the same pipe-delimited style as order_log.txt but
    with the WALMART marker and the queue row id in place of the Amazon id, so
    future closing integration can key on 'Amazon:WALMART:<row id>'."""
    def s(v):
        return str(v if v is not None else "N/A").replace("|", "-").replace("\n", " ").strip()

    def money(v):
        try:
            return f"${float(v):.2f}"
        except (TypeError, ValueError):
            return "N/A"

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = (
        f"[{now}] | WALMART | WB:{s(wb)} | Profile:WALMART | Amazon:WALMART:{s(row_id)}"
        f" | SKU:{s(sku)} | WmQty:{s(wm_qty)} | ZMultiple:{s(mult)} | ZQty:{s(zoro_qty)}"
        f" | Cost:{s(money(checkout_total))} | Coupon:{s(used_coupon or 'None')}"
        f" | ShipName:{s(addr.get('name'))} | ShipStreet:{s(addr.get('street_address'))}"
        f" | ShipStreet2:{s(addr.get('street_address_2'))}"
        f" | ShipCity:{s(addr.get('city'))} | ShipState:{s(addr.get('state_abbr'))}"
        f" | ShipZIP:{s(addr.get('zip_code'))} | ShipPhone:{s(addr.get('phone'))}"
        f" | Time:{timing_seconds:.1f}s\n"
    )
    try:
        os.makedirs(os.path.dirname(WALMART_LOG_PATH), exist_ok=True)
        with open(WALMART_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
        print(f"   📋 Walmart order logged: data/rajar_logs/walmart_log.txt")
    except Exception as e:
        print(f"   ⚠️  Could not write walmart_log.txt: {e}")


# ══════════════════════════════════════════════════════════════════
#  SELLER CENTER SCRAPE PHASE  (WALMART_SCRAPE=1)
# ══════════════════════════════════════════════════════════════════

def _read_tabular(path):
    """Read a Walmart order export (.xlsx / .xls / .csv) → (headers, list-of-dicts).
    No Seller Center, no bot wall — the human downloads the file, we just read it."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        # pick the sheet with the most data cells (rows * cols) so that a
        # single-column reference list like 'carrierMethods' doesn't win
        ws = max(wb.worksheets,
                 key=lambda s: (s.max_row or 0) * (s.max_column or 0))
        rows = [[(""  if c is None else c) for c in r]
                for r in ws.iter_rows(values_only=True)]
        wb.close()
    elif ext == ".csv":
        import csv as _csv
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            rows = [list(r) for r in _csv.reader(f)]
    else:
        raise ValueError(f"unsupported file type '{ext}' (use .xlsx or .csv)")
    # find the header row = first row with ≥3 non-empty cells
    hidx = next((i for i, r in enumerate(rows)
                 if sum(1 for c in r if str(c).strip()) >= 3), None)
    if hidx is None:
        return [], []
    headers = [str(h).strip() for h in rows[hidx]]
    out = []
    for r in rows[hidx + 1:]:
        r = list(r) + [""] * (len(headers) - len(r))
        d = {headers[i]: r[i] for i in range(len(headers))}
        if any(str(v).strip() for v in d.values()):
            out.append(d)
    return headers, out


def _pick(d, *names):
    """Fuzzy column getter: case/space/punct-insensitive header match."""
    norm = {re.sub(r"[^a-z0-9]", "", k.lower()): v for k, v in d.items()}
    for n in names:
        key = re.sub(r"[^a-z0-9]", "", n.lower())
        if key in norm and str(norm[key]).strip():
            return str(norm[key]).strip()
    return ""


def run_import_phase(path, rows, dry):
    """Build queue rows from a downloaded Walmart order file (the no-bot-wall path).
    Flexible header matching handles Walmart's export column names; the address is
    taken from a single 'Customer address'/'Ship to' column (parser needs a
    'City, ST ZIP' line — visible as a fail if the file lacks it). Returns
    (merged_rows, n_new, file_po_set). Skips POs already placed (queue or DB).

    file_po_set is the exact set of PO numbers read from the file so the caller
    can restrict processing to ONLY those orders."""
    print(f"\n📂 Importing Walmart order file: {os.path.basename(path)}")
    file_po_set = set()          # ← every PO found in the file (before any filter)
    try:
        headers, recs = _read_tabular(path)
    except Exception as e:
        print(f"  ❌ could not read file: {e}")
        return rows, 0, file_po_set
    if not recs:
        print("  ⚠️  no data rows found in the file (is it the right export?)")
        return rows, 0, file_po_set
    print(f"  columns: {headers}")
    # Collect ALL PO numbers from the file first (before any placed/dup filter)
    for d in recs:
        po = _pick(d, "Purchase order#", "PO#", "Purchase Order Number", "po", "ponumber")
        if po:
            file_po_set.add(str(po).strip())
    print(f"  📄 File contains {len(file_po_set)} unique PO(s): {file_po_set}")
    by_po = {str(r.get("walmart_po") or "").strip(): r for r in rows if r.get("walmart_po")}
    placed_pos = set()
    try:
        with get_conn() as _c:
            placed_pos = {str(x[0]) for x in _c.execute(
                "SELECT po_number FROM walmart_orders WHERE wb_order_id IS NOT NULL "
                "AND wb_order_id!=''")}
    except Exception:
        pass
    n_new = 0
    for d in recs:
        po = _pick(d, "Purchase order#", "PO#", "Purchase Order Number", "po", "ponumber")
        wm_sku = _pick(d, "SKU", "Seller SKU", "walmart_sku", "item sku")
        if not po and not wm_sku:
            continue
        if po and (po in placed_pos):     # already placed on a prior run — skip
            continue
        qty_s = _pick(d, "Qty", "Quantity", "qty ordered", "order qty") or "1"
        try:
            wm_qty = max(1, int(float(qty_s)))
        except ValueError:
            wm_qty = 1
        addr = _pick(d, "Customer address", "Ship to", "Shipping address", "Customer Address",
                     "address")
        # if the file splits the address into columns, assemble it
        if not addr:
            name = _pick(d, "Customer", "Customer Name", "Ship To Name", "name")
            l1 = _pick(d, "Address Line 1", "Street", "Ship To Address 1", "address1")
            l2 = _pick(d, "Address Line 2", "Ship To Address 2", "address2")
            city = _pick(d, "City", "Ship To City")
            st = _pick(d, "State", "Ship To State", "State/Province")
            zp = _pick(d, "Zip", "Postal Code", "Ship To Zip", "zipcode")
            ph = _pick(d, "Phone", "Customer Phone", "Ship To Phone",
                       "Customer Phone Number", "Phone Number")
            parts = [name, l1, l2, f"{city}, {st} {zp}".strip(", "), ph]
            addr = "\n".join(p for p in parts if p and p.strip(", "))
        item_price = _to_money(_pick(d, "Item price", "Item Cost", "Unit Price", "Price", "item_price"))
        if item_price is not None:
            item_price = round(item_price * wm_qty, 2)
        shipping_fee = _to_money(_pick(d, "Shipping fee", "Shipping Cost", "Shipping", "Ship Cost", "shipping_fee"))
        tax = _to_money(_pick(d, "Taxes", "Tax", "tax"))
        order_total = _to_money(_pick(d, "Order total", "Total", "order_total"))
        earnings = compute_earnings(item_price, shipping_fee)
        zoro_code = ""
        try:
            from engine.walmart_sc import zoro_code_from_walmart_sku
            zoro_code = zoro_code_from_walmart_sku(wm_sku)
        except Exception:
            pass
        rid = po and f"PO{po}" or f"IMP{n_new+1}"
        qrow = by_po.get(po, {})
        new_row = dict(qrow)
        new_row.update({
            "id": qrow.get("id") or rid,
            "sku": zoro_code, "walmart_sku": wm_sku, "wm_qty": wm_qty,
            "address_raw": addr, "walmart_po": po,
            "order_number": _pick(d, "Order#", "Order Number", "order_number"),
            "order_date": _pick(d, "Order date", "Order Date", "order_date"),
            "ship_by": _pick(d, "Ship by", "Ship By", "ship_by"),
            "title": _pick(d, "Item", "Item Name", "Product Name", "Product", "Description", "Item Description", "title"),
            "item_price": item_price, "shipping_fee": shipping_fee, "tax": tax,
            "order_total": order_total, "earnings": earnings,
            # Re-uploading a file means "process these" — so a row that failed on
            # an earlier run goes back to pending. Rows that actually placed keep
            # their WB and are filtered out downstream (never re-bought).
            "source": "imported",
            "status": "placed" if (qrow.get("wb") or "").strip() else "pending",
            "wb": qrow.get("wb", ""), "error": "", "zoro_qty": None,
            "created_at": qrow.get("created_at") or datetime.now().isoformat(timespec="seconds"),
        })
        if po:
            by_po[po] = new_row
        else:
            rows.append(new_row)
        if not qrow:
            n_new += 1
    merged = [r for r in rows if not r.get("walmart_po")] + list(by_po.values())
    print(f"  📥 {len(recs)} file row(s) → {n_new} new order(s) queued"
          + (" (DRY)" if dry else ""))
    return merged, n_new, file_po_set


def _to_money(s):
    if s is None or str(s).strip() == "":
        return None
    try:
        return round(float(re.sub(r"[^\d.\-]", "", str(s))), 2)
    except (ValueError, TypeError):
        return None


def run_scrape_phase(sc_driver, rows, dry):
    """Scrape the Unshipped tab, then each new PO's detail page, and merge
    auto-built queue rows (source='scraped') into `rows`.

    Returns (rows, n_new) — or (LOGIN_REQUIRED, 0) when the SC Chrome isn't
    logged in. POs that already have a WB (queue or walmart_orders table) are
    skipped BEFORE any detail scrape. In dry mode nothing is written to the
    DB (and the caller does not save the queue)."""
    from engine import walmart_sc

    print("\n🛒 SELLER CENTER SCRAPE — Orders → Unshipped")
    unshipped = walmart_sc.scrape_unshipped(sc_driver)
    if unshipped == walmart_sc.LOGIN_REQUIRED:
        return walmart_sc.LOGIN_REQUIRED, 0
    print(f"  ✅ {len(unshipped)} unshipped order(s) on Seller Center")

    queue_by_po = {}
    for r in rows:
        po = str(r.get("walmart_po") or "").strip()
        if po:
            queue_by_po[po] = r

    placed_pos = set()
    try:
        from utils.unified_db import get_conn
        with get_conn() as conn:
            for r in conn.execute("SELECT po_number FROM walmart_orders "
                                  "WHERE IFNULL(wb_order_id,'')<>''"):
                placed_pos.add(str(r["po_number"]))
    except Exception as e:
        print(f"  ⚠️  could not read placed POs from walmart_orders: {e}")

    n_new = 0
    for o in unshipped:
        po = str(o.get("po_number") or "").strip()
        if not po:
            continue
        qrow = queue_by_po.get(po)
        if po in placed_pos or (qrow and ((qrow.get("wb") or "").strip()
                                          or qrow.get("status") == "placed")):
            print(f"  ⏭️  PO {po}: already placed — skipped")
            continue

        detail = walmart_sc.scrape_order_detail(sc_driver, po)
        if detail == walmart_sc.LOGIN_REQUIRED:
            return walmart_sc.LOGIN_REQUIRED, n_new

        walmart_sku = detail.get("walmart_sku") or o.get("walmart_sku") or ""
        zoro_code = (detail.get("zoro_code") or o.get("zoro_code")
                     or walmart_sc.zoro_code_from_walmart_sku(walmart_sku))
        wm_qty = detail.get("qty") or o.get("qty") or 1
        item_price = detail.get("item_price")
        shipping_fee = detail.get("shipping_fee")
        earnings = compute_earnings(item_price, shipping_fee)
        address_raw = detail.get("address_raw") or ""
        if not address_raw:
            # last resort: the list column's one-line address (parser will
            # reject it if it has no City, ST ZIP line — visible as a fail)
            address_raw = str(o.get("customer_address") or "")

        rid = (qrow or {}).get("id") or f"PO{po}"
        new_row = dict(qrow or {})
        new_row.update({
            "id": rid,
            "sku": zoro_code,
            "wm_qty": int(wm_qty),
            "address_raw": address_raw,
            "walmart_po": po,
            "order_number": detail.get("order_number") or o.get("order_number") or "",
            "order_date": detail.get("order_date") or o.get("order_date") or "",
            "ship_by": detail.get("ship_by") or o.get("ship_by") or "",
            "deliver_by": detail.get("deliver_by") or "",
            "title": detail.get("title") or "",
            "walmart_sku": walmart_sku,
            "item_price": item_price,
            "shipping_fee": shipping_fee,
            "tax": detail.get("tax"),
            "order_total": (detail.get("order_total")
                            if detail.get("order_total") is not None
                            else o.get("order_total")),
            "earnings": earnings,
            "source": "scraped",
            "status": "pending",
            "wb": (qrow or {}).get("wb", ""),
            "error": "",
            "zoro_qty": None,
            "created_at": ((qrow or {}).get("created_at")
                           or datetime.now().isoformat(timespec="seconds")),
            "placed_at": (qrow or {}).get("placed_at", ""),
        })

        missing = [m for m in (detail.get("_missing") or []) if m != "title"]
        print(f"  📥 PO {po}: SKU '{walmart_sku}' → {zoro_code or '❓NO G-CODE'}"
              f" ×{wm_qty} | item ${item_price if item_price is not None else '?'}"
              f" + ship ${shipping_fee if shipping_fee is not None else '?'}"
              f" → earnings {'$%.2f' % earnings if earnings is not None else 'unknown'}"
              + (f" | ⚠️ missing: {', '.join(missing)}" if missing else ""))

        if qrow:
            idx = rows.index(qrow)
            rows[idx] = new_row
            queue_by_po[po] = new_row
        else:
            rows.append(new_row)
            queue_by_po[po] = new_row
        n_new += 1

        if not dry:
            new_row["parsed"] = parse_walmart_address(address_raw)
            upsert_walmart_order(_row_to_db_record(new_row, status="pending"))

    return rows, n_new


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def _row_fail(row, reason, results):
    row["status"] = "failed"
    row["error"] = reason
    print(f"  ❌ {reason}")
    results.append((row.get("id"), row.get("sku", ""), row.get("zoro_qty"),
                    row, "failed", reason))
    # Keep THE maintained log in sync (only ever called on the LIVE path).
    upsert_walmart_order(_row_to_db_record(row, status="failed", error=reason))


def main():
    if "--test-parser" in sys.argv:
        sys.exit(0 if _run_parser_tests() else 1)
    if "--test-units" in sys.argv:
        sys.exit(0 if _run_unit_tests() else 1)
    if "--bot-check" in sys.argv:
        sys.exit(bot_check())

    from utils.accounts_config import walmart as walmart_cfg
    cfg = walmart_cfg()
    port = int(os.environ.get("WALMART_PORT") or cfg.get("zoro_port") or 9361)
    sc_port = int(os.environ.get("WALMART_SC_PORT") or cfg.get("sc_port") or 9362)
    queue_path = os.environ.get("WALMART_QUEUE") or DEFAULT_QUEUE_PATH
    dry = os.environ.get("WALMART_DRY") == "1"
    scrape_mode = os.environ.get("WALMART_SCRAPE") == "1"
    import_file = os.environ.get("WALMART_IMPORT", "").strip()   # path to a downloaded order file
    start_processing = (os.environ.get("WALMART_START_PROCESSING") or "1") == "1"

    _mode_label = ("[IMPORT FILE]" if import_file else
                   "[SELLER CENTER PIPELINE]" if scrape_mode else "[MANUAL QUEUE]")
    print("═" * 66)
    print("  WALMART → ZORO ORDER PLACER  " + _mode_label + "  [v2.1]")
    print(f"  Zoro port: {port} (attach-only)"
          + (f"  |  SC port: {sc_port} (attach-only)" if scrape_mode else "")
          + f"  |  Queue: {os.path.basename(queue_path)}")
    print(f"  Mode: {'DRY RUN (no orders placed, nothing clicked/saved)' if dry else 'LIVE'}")
    if import_file:
        print(f"  🔒 FILE-ONLY: will ONLY process orders from: {os.path.basename(import_file)}")
    print(f"  Referral fee: {WALMART_REFERRAL_PCT:.0%}  |  "
          f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═" * 66)

    # ── 0. walmart_orders table — THE maintained log (idempotent) ──
    db_ready = _ensure_walmart_table()
    if not db_ready and not dry:
        print("🛑 walmart_orders table unavailable — refusing a LIVE run without "
              "the maintained log + local dup protection (fail-closed).")
        sys.exit(1)

    # ── 1. ONE Chrome, ONE profile, TWO TABS (like the Amazon placer):
    #      Seller Center tab + zoro.com tab in the SAME browser (port 9361,
    #      fixed desktop UA). First ever run needs a human login in each tab;
    #      the profile remembers both after that.
    from utils.accounts_config import walmart as _wcfg
    _zprof = (_wcfg().get("profile_dir") if int(_wcfg().get("zoro_port") or 0) == int(port)
              else None) or f"port_{port}"
    # IMPORT mode needs only the Zoro tab (Seller Center never touched → no bot wall).
    need_sc = scrape_mode
    driver = ensure_chrome_walmart(port, _zprof,
                                   "https://seller.walmart.com" if need_sc
                                   else "https://www.zoro.com")
    if driver is None:
        _print_no_chrome_and_exit(port)
    sc_driver = driver if need_sc else None
    if need_sc:
        _focus_tab(driver, "seller.walmart.com")
        _focus_tab(driver, "zoro.com", "https://www.zoro.com")   # pre-open the Zoro tab
        _focus_tab(driver, "seller.walmart.com")                 # scrape starts here
    else:
        _focus_tab(driver, "zoro.com", "https://www.zoro.com")

    # ── 2. Queue (+ file import / Seller Center scrape) ──────────
    rows = load_queue(queue_path)
    if rows is None:
        print("❌ Queue file unreadable — aborting (nothing modified).")
        sys.exit(1)

    file_po_set = set()   # POs from the uploaded file (empty = process all)

    if import_file:
        if not os.path.exists(import_file):
            print(f"🛑 Import file not found: {import_file}")
            sys.exit(2)
        rows, n_new, file_po_set = run_import_phase(import_file, rows, dry)
        if not dry:
            save_queue(queue_path, rows)

    if scrape_mode:
        from engine.walmart_sc import LOGIN_REQUIRED
        scraped, n_new = run_scrape_phase(sc_driver, rows, dry)
        if scraped == LOGIN_REQUIRED:
            # One-button UX: wait for the human login instead of bailing,
            # then retry the scrape once.
            if _wait_for_sc_login(sc_driver, sc_port):
                scraped, n_new = run_scrape_phase(sc_driver, rows, dry)
            if scraped == LOGIN_REQUIRED:
                _print_sc_login_required_and_exit(sc_port)
        rows = scraped
        print(f"  📥 {n_new} scraped row(s) merged into the queue"
              + (" (DRY: queue/DB not modified)" if dry else ""))
        # Scrape done — placement happens on the Zoro tab of the SAME browser.
        _focus_tab(driver, "zoro.com", "https://www.zoro.com")
        if not dry and n_new:
            save_queue(queue_path, rows)

    if import_file:
        # STRICT FILE-ONLY MODE: the uploaded file IS the work list. Rows already
        # in the queue from earlier runs are never touched, whatever their status.
        if not file_po_set:
            # Never silently widen to the whole queue — that is how old orders got
            # re-processed. No PO parsed = we cannot scope the run, so refuse.
            print("🛑 FILE-ONLY: no PO numbers could be read from the file "
                  "(is the 'Purchase order#' column present?). Refusing to run — "
                  "processing the existing queue is NOT the fallback.")
            sys.exit(2)
        pending = [r for r in rows
                   if not (r.get("wb") or "").strip()
                   and str(r.get("walmart_po") or "").strip() in file_po_set]
        print(f"  🔒 FILE-ONLY MODE: {len(file_po_set)} PO(s) in file → "
              f"{len(pending)} to process (everything else in the queue is ignored)")
    else:
        pending = [r for r in rows
                   if r.get("status") == "pending" and not (r.get("wb") or "").strip()]
    print(f"\n📋 Queue: {len(rows)} row(s), {len(pending)} to process")
    if not pending:
        print("Nothing to do.")
        sys.exit(0)

    # ── 3. Data sheets ────────────────────────────────────────────
    walmart_map = load_walmart_sku_map()
    if walmart_map is None:
        if dry:
            print("  ⚠️  DRY: Walmart SKU sheet unreadable — every SKU will show as unmapped")
            walmart_map = {}
        else:
            print("🛑 Walmart SKU sheet failed to load — refusing to place (can't map "
                  "SKUs to Zoro codes/qty safely).")
            sys.exit(1)

    coupon_arg = None
    priority_codes = []
    duplicate_services = []
    plock = None

    # Walmart orders are placed WITHOUT coupons — skip coupon loading entirely.
    # coupon_arg stays None, priority_codes stays [] → checkout won't try any.

    if not dry:
        duplicate_services, _dup_failed = build_duplicate_services()

        # Hard local dedup lock — refuse to run without it (fail-closed).
        try:
            from utils import placement_lock as plock_mod
            plock = plock_mod
        except Exception as e:
            print(f"🛑 placement_lock unavailable ({e}) — refusing to place without dedup protection.")
            sys.exit(1)
        if not plock.init(seed_log=True):
            print("🛑 placement_lock failed to initialize (DB unreachable) — refusing to run.")
            sys.exit(1)

    from utils.config import BLOCKED_STATES, ZORO_SMALL_ORDER_FEE, FREE_SHIPPING_THRESHOLD
    import platforms.zoro as zoro
    if not dry:
        from platforms.gmail import check_if_address_already_ordered_gmail, DUP_CHECK_FAILED
        from checkout.true_api import place_order_fast_true_api

    # Per-run coupon intelligence cache (same shape the placer keeps)
    asin_coupon_cache = {
        "failed_pairs": {},
        "working_pairs": {},
        "no_coupon_codes": set(),
        "priority_exhausted": set(),
    }

    results = []   # (row_id, sku_x_qty, name/city, status, detail) for the final table

    # ── 4. Per-row loop — one row's failure NEVER aborts the run ──
    # Iterate the SCOPED list, not the whole queue: in file-only mode `pending`
    # holds exactly the uploaded file's orders. (These are the same dict objects
    # stored in `rows`, so mutating them and saving `rows` still persists.)
    for row in pending:
        if (row.get("wb") or "").strip():
            continue  # idempotent: never touch rows with a WB

        row_id = str(row.get("id") or "").strip()
        sku = str(row.get("sku") or "").strip()
        walmart_po = str(row.get("walmart_po") or "").strip()
        earnings = row.get("earnings")
        try:
            wm_qty = max(1, int(row.get("wm_qty") or 1))
        except (TypeError, ValueError):
            wm_qty = 1

        print(f"\n{'─' * 60}")
        print(f"  ROW {row_id} — SKU {sku} × {wm_qty} (Walmart qty)"
              + (f"  [PO {walmart_po}]" if walmart_po else "")
              + (f"  [earnings ${earnings:.2f}]" if isinstance(earnings, (int, float)) else ""))
        print(f"{'─' * 60}")

        def fail(reason):
            if dry:
                print(f"  ❌ WOULD FAIL: {reason}")
                results.append((row_id, sku, row.get("zoro_qty"), row, "DRY-fail", reason))
            else:
                _row_fail(row, reason, results)
                save_queue(queue_path, rows)

        if not row_id:
            fail("MISSING_ROW_ID — row has no id")
            continue

        # a. Parse address
        parsed = parse_walmart_address(row.get("address_raw", ""))
        if not parsed:
            fail("ADDRESS_PARSE_FAILED — no 'City, ST 12345' line found "
                 "(need: name / street / City, ST ZIP / optional phone)")
            continue
        if not dry:
            row["parsed"] = parsed
        addr, addr_err = to_checkout_address(parsed)
        if not addr:
            fail(f"ADDRESS_INVALID — {addr_err}")
            continue
        print(f"  📮 {addr['name']} — {addr['street_address']}"
              + (f", {addr['street_address_2']}" if addr.get("street_address_2") else "")
              + f", {addr['city']}, {addr['state_abbr']} {addr['zip_code']}"
              + f"  ☎ {addr['phone']}")

        # b. WALMART SKU → Zoro code + qty, straight from the Walmart_SKUs_Verified
        #    sheet (the ONLY authority — the same Zoro code repeats across pack
        #    sizes, so we key by the full Walmart SKU, matched space/case-tolerant).
        #    UNMAPPED SKU → NOT placed; flagged for the GUI + not-placed summary.
        wm_sku = str(row.get("walmart_sku") or row.get("sku") or "").strip()
        hit = walmart_map.get(_norm_sku(wm_sku)) if wm_sku else None
        if not hit:
            fail(f"UNMAPPED_SKU — '{wm_sku or '(blank)'}' is not in the "
                 f"Walmart_SKUs_Verified sheet. PLEASE MAP IT (Walmart SKU → Zoro "
                 f"code → Qty to Place), then re-run. This order was NOT placed.")
            continue
        raw_zoro_code = hit["zoro_code"]
        sku_list = [s.strip() for s in raw_zoro_code.split(",") if s.strip()]
        if not sku_list:
            fail(f"UNMAPPED_SKU — '{wm_sku}' has an empty Zoro code mapped.")
            continue
        
        sku = sku_list[0]
        mult = hit["qty"]
        zoro_qty = wm_qty * mult
        
        extra_skus_list = []
        for extra in sku_list[1:]:
            extra_skus_list.append((extra, zoro_qty))
            
        row["sku"] = raw_zoro_code
        row["zoro_qty"] = zoro_qty
        print(f"  Item: WALMART '{wm_sku}' → {raw_zoro_code} x{zoro_qty}  "
              f"(wm_qty {wm_qty} × qty-to-place {mult})")

        # c. Guards, in order: PO box → restricted state → local DB duplicate
        #    (walmart_orders: same PO# OR same street+zip already placed —
        #    belt and braces) → Gmail duplicate
        street_all = (parsed["street"] + " " + parsed.get("unit", "")).upper()
        if _PO_BOX_RE.search(street_all):
            fail("PO_BOX — Zoro does not ship to PO boxes")
            continue
        if addr["state_abbr"] in BLOCKED_STATES:
            fail(f"RESTRICTED_STATE — {addr['state_abbr']} is blocked")
            continue

        if dry:
            print("  🔎 DRY: local + Gmail duplicate checks skipped")
        else:
            dup_state, dup_wb = db_duplicate_check(
                walmart_po,
                parsed["street"] + (f", {parsed['unit']}" if parsed.get("unit") else ""),
                parsed.get("zip", ""))
            if dup_state == "dup":
                fail(f"DUPLICATE_DB — walmart_orders already has this "
                     f"PO/address placed ({dup_wb})")
                continue
            if dup_state == "error":
                # FAIL-CLOSED: an errored local check is NOT "no duplicate".
                fail("DUP_CHECK_FAILED — walmart_orders DB check errored "
                     "(fail-closed — will retry on next run)")
                continue
            print("  ✅ No local (walmart_orders) duplicates")

            if not duplicate_services:
                # Zero inboxes authenticated → the check cannot run → FAIL CLOSED.
                fail("DUP_CHECK_FAILED — not placed (no duplicate-check Gmail "
                     "inbox authenticated; fail-closed)")
                continue
            try:
                existing_wb = check_if_address_already_ordered_gmail(
                    duplicate_services, addr["street_address"],
                    zip_code=addr.get("zip_code", ""),
                    customer_name=addr.get("name", ""))
            except Exception as e:
                print(f"   [DUP_CHECK] exception: {e}")
                existing_wb = DUP_CHECK_FAILED
            if existing_wb == DUP_CHECK_FAILED:
                # FAIL-CLOSED, exactly like the placer: an errored check is NOT
                # "no duplicate" — do NOT place.
                fail("DUP_CHECK_FAILED — not placed (Gmail duplicate check "
                     "errored; fail-closed — will retry on next run)")
                continue
            if existing_wb:
                fail(f"DUPLICATE — an order to this address already exists ({existing_wb})")
                continue
            print("  ✅ No duplicates")

        # ── DRY RUN: estimated cart cost via cart-build, then revert ──
        if dry:
            try:
                zoro.ensure_on_site(driver)
                zoro.ensure_token(driver)
                all_cartable = True
                est_items = 0
                for i, (check_sku, check_qty) in enumerate([(sku, zoro_qty)] + extra_skus_list):
                    cartable, inv_status = zoro.check_inventory(driver, check_sku, check_qty)
                    if not cartable:
                        print(f"  ❌ WOULD FAIL: OOS/{inv_status} for {check_sku} x{check_qty}")
                        results.append((row_id, raw_zoro_code, zoro_qty, row, "DRY-fail", f"OOS ({inv_status})"))
                        all_cartable = False
                        break
                    
                    if i == 0:
                        ok, unit_price = zoro.clear_cart_and_add(driver, check_sku, check_qty)
                    else:
                        ok, unit_price = zoro.add_second_item(driver, check_sku, check_qty)
                        
                    if not ok:
                        print(f"  ❌ WOULD FAIL: cart add failed for {check_sku} x{check_qty}")
                        results.append((row_id, raw_zoro_code, zoro_qty, row, "DRY-fail", "cart add failed"))
                        all_cartable = False
                        break
                    est_items += unit_price * check_qty
                    
                if not all_cartable:
                    continue
                    
                est_ship = ZORO_SMALL_ORDER_FEE if est_items < FREE_SHIPPING_THRESHOLD else 0.0
                print(f"  🏁 DRY RUN — WOULD place {raw_zoro_code} x{zoro_qty} for {addr['name']}, "
                      f"{addr['city']} {addr['state_abbr']}")
                print(f"     Est cart: ${est_items:.2f} "
                      + (f" + ${est_ship:.2f} small-order fee" if est_ship else " (free shipping)")
                      + "  — before coupons/tax")
                detail_txt = f"est ${est_items + est_ship:.2f}"
                if isinstance(earnings, (int, float)):
                    est_profit = earnings - est_items - est_ship

                    print(f"     Earnings ${earnings:.2f} → est profit "
                          f"${est_profit:+.2f} (before coupons/tax)")
                    detail_txt += f", earn ${earnings:.2f}, est profit ${est_profit:+.2f}"
                zoro.clear_cart_selenium(driver)  # revert the cart
                results.append((row_id, sku, zoro_qty, row, "DRY-ok", detail_txt))
            except Exception as e:
                print(f"  ⚠️  DRY estimate failed: {e}")
                results.append((row_id, sku, zoro_qty, row, "DRY-fail", f"estimate error: {e}"))
            continue

        # d. RESERVE the local placement-lock claim BEFORE spending a cent.
        lock_key = f"WALMART:{row_id}"
        lock_state, lock_info = plock.reserve(lock_key, "WALMART")
        if lock_state == plock.BLOCKED:
            wb_prev = ((lock_info or {}).get("wb") or "").strip()
            if wb_prev.startswith(("WB", "WW")):
                # Already placed in a prior run whose queue write was lost — recover.
                row["status"] = "placed"
                row["wb"] = wb_prev
                row["placed_at"] = row.get("placed_at") or datetime.now().isoformat(timespec="seconds")
                row["error"] = ""
                print(f"  ⏭️  Already placed per placement lock ({wb_prev}) — row marked placed")
                results.append((row_id, sku, zoro_qty, row, "placed", wb_prev))
                upsert_walmart_order(_row_to_db_record(
                    row, status="placed", error="",
                    wb_order_id=wb_prev, placed_at=row["placed_at"]))
                save_queue(queue_path, rows)
            else:
                fail(f"LOCKED ({(lock_info or {}).get('status', '?')}) — a previous attempt is "
                     "unresolved; verify on Zoro and unlock (Locked Orders tab) to retry")
            continue
        if lock_state == plock.ERROR:
            fail("LOCK_DB_UNREACHABLE — fail-closed, not placed")
            continue

        # e. Checkout via the EXISTING engine — NO coupons, saved card payment.
        t0 = time.perf_counter()
        try:
            _focus_tab(driver, "zoro.com", "https://www.zoro.com")   # checkout runs on the Zoro tab
            result = place_order_fast_true_api(
                driver, sku, zoro_qty, addr,
                "",                              # po_number — none for Walmart
                "WALMART_RESELLER",              # reseller-style payment path: saved credit card
                earnings=earnings if isinstance(earnings, (int, float)) else None,
                coupon_list=coupon_arg,
                priority_coupons=priority_codes,
                preferred_coupon=None,
                asin=None,
                zoro_code=sku,
                asin_coupon_cache=asin_coupon_cache,
                min_margin=None,                 # no margin gate (earnings only informs profit)
                return_details=True,
                amazon_id=f"WALMART:{row_id}",   # row id in place of the Amazon id
                item_title=(row.get("title")
                            or (f"Walmart PO {walmart_po}" if walmart_po
                                else f"Walmart manual row {row_id}")),
                amazon_order_qty=wm_qty,
                zoro_multiple=mult,
                qty_multiplier=1,
                payment_method="CREDIT_CARD",
                extra_skus=extra_skus_list,
            )
        except Exception as e:
            # Unknown state — money MAY have been spent. Keep the claim (never auto-retry).
            plock.keep_failed(lock_key, f"exception during checkout: {str(e)[:200]}")
            fail(f"CHECKOUT_EXCEPTION — {str(e)[:120]} (row LOCKED; verify on Zoro "
                 "before unlocking for retry)")
            continue

        elapsed = time.perf_counter() - t0
        status = (result or {}).get("status") or "FAILED"
        wb_id = (result or {}).get("wb_id") or ""

        if status == "SUCCESS" and wb_id:
            plock.confirm(lock_key, wb_id)
            row["status"] = "placed"
            row["wb"] = wb_id
            row["placed_at"] = datetime.now().isoformat(timespec="seconds")
            row["error"] = ""
            # zoro_cost from the checkout result; profit = earnings - cost.
            # No total returned → zoro_cost stays NULL for closing to backfill.
            zoro_cost = result.get("checkout_total")
            profit = None
            if isinstance(earnings, (int, float)) and zoro_cost:
                profit = round(earnings - float(zoro_cost), 2)
            elif result.get("profit") is not None:
                profit = result.get("profit")
            row["zoro_cost"] = zoro_cost
            row["profit"] = profit
            coupon_used = result.get("used_coupon_code") or ""
            print(f"  ✅ PLACED: {wb_id}  (${result.get('checkout_total') or 0:.2f}, "
                  f"coupon: {coupon_used or 'none'}, {elapsed:.1f}s)"
                  + (f"  profit ${profit:+.2f}" if profit is not None else ""))
            _write_walmart_log(row_id, wb_id, sku, wm_qty, mult, zoro_qty, addr,
                               result.get("checkout_total"),
                               result.get("used_coupon_code"), elapsed)
            results.append((row_id, sku, zoro_qty, row, "placed", wb_id))

            # THE maintained log: wb + cost + profit land in walmart_orders.
            db_status = "placed"
            db_error = ""

            upsert_walmart_order(_row_to_db_record(
                row, status=db_status, error=db_error,
                wb_order_id=wb_id, zoro_cost=zoro_cost, profit=profit,
                placed_at=row["placed_at"], coupon_used=coupon_used))
        elif status in _SAFE_PREPAYMENT:
            # Detected BEFORE payment — release the claim so a retry is allowed.
            plock.release(lock_key, f"pre-payment failure: {status}")
            _row_fail(row, f"{status} — no money spent; safe to retry", results)
        else:
            # Unknown / post-submit failure — money may have been spent. Keep locked.
            plock.keep_failed(lock_key, f"unknown failure '{status}' — verify on Zoro before re-order")
            _row_fail(row, f"{status} — held (money may have been spent; verify on "
                           "Zoro, then unlock in Locked Orders to retry)", results)

        save_queue(queue_path, rows)

    # ── 5. Final summary ───────────────────────────────────────────
    placed   = [r for r in results if r[4] == "placed"]
    failed   = [r for r in results if r[4] != "placed"]
    total    = len(results)

    # --- Helper to format a row for display ---
    def _fmt(r):
        row_id, sku, zqty, row, status, detail = r
        parsed = row.get("parsed") or parse_walmart_address(row.get("address_raw", "")) or {}
        name   = parsed.get("name", "?")
        city   = parsed.get("city", "?")
        state  = parsed.get("state", "")
        po     = str(row.get("walmart_po") or "").strip()
        wm_sku = str(row.get("walmart_sku") or sku or "").strip()
        return po, wm_sku, f"{sku} ×{zqty or '?'}", f"{name} — {city}, {state}", detail

    # ══════════════════════ HEADER ══════════════════════
    print(f"\n{'═' * 70}")
    print(f"  📊  RUN SUMMARY")
    print(f"{'═' * 70}")
    print(f"  Total orders processed:  {total}")
    print(f"  ✅ Successfully placed:  {len(placed)}")
    print(f"  ❌ Could not place:      {len(failed)}")
    print(f"{'─' * 70}")

    # ══════════════════════ PLACED ORDERS ══════════════════════
    if placed:
        print(f"\n{'═' * 70}")
        print(f"  ✅  PLACED ORDERS ({len(placed)})  —  Please acknowledge these in Seller Center")
        print(f"{'═' * 70}")
        for r in placed:
            po, wm_sku, sku_q, who, wb = _fmt(r)
            print(f"   ✅ PO {po or '?':>18}  │  {sku_q:<20}  │  {who}")
            print(f"      WB: {wb}")

    # ══════════════════════ FAILED ORDERS ══════════════════════
    if failed:
        # Group failures by type
        unmapped   = [r for r in failed if "UNMAPPED_SKU" in str(r[5])]
        duplicates = [r for r in failed if "DUPLICATE" in str(r[5]).upper()]
        oos        = [r for r in failed if "OOS" in str(r[5]).upper() or "OUT OF STOCK" in str(r[5]).upper()]
        other      = [r for r in failed if r not in unmapped and r not in duplicates and r not in oos]

        print(f"\n{'═' * 70}")
        print(f"  ❌  COULD NOT PLACE ({len(failed)})")
        print(f"{'═' * 70}")

        if unmapped:
            print(f"\n  🔴 UNMAPPED SKUs ({len(unmapped)}) — Please MAP these in the Walmart_SKUs_Verified sheet")
            print(f"     (Add: Walmart SKU → Zoro Code → Qty to Place), then re-import the file.")
            print(f"  {'─' * 66}")
            for r in unmapped:
                po, wm_sku, sku_q, who, detail = _fmt(r)
                print(f"   • PO {po or '?':>18}  │  SKU: {wm_sku:<22}  │  {who}")

        if duplicates:
            print(f"\n  🟡 DUPLICATES ({len(duplicates)}) — Already placed before (no action needed)")
            print(f"  {'─' * 66}")
            for r in duplicates:
                po, wm_sku, sku_q, who, detail = _fmt(r)
                wb_match = ""
                import re as _re
                wb_found = _re.search(r"WB\d+", str(detail))
                if wb_found:
                    wb_match = f"  (existing: {wb_found.group()})"
                print(f"   • PO {po or '?':>18}  │  {sku_q:<20}  │  {who}{wb_match}")

        if oos:
            print(f"\n  🟠 OUT OF STOCK ({len(oos)}) — Please place these manually on Zoro")
            print(f"  {'─' * 66}")
            for r in oos:
                po, wm_sku, sku_q, who, detail = _fmt(r)
                print(f"   • PO {po or '?':>18}  │  {sku_q:<20}  │  {who}")

        if other:
            print(f"\n  🔵 OTHER FAILURES ({len(other)}) — Please place these manually")
            print(f"  {'─' * 66}")
            for r in other:
                po, wm_sku, sku_q, who, detail = _fmt(r)
                print(f"   • PO {po or '?':>18}  │  {sku_q:<20}  │  {who}")
                print(f"     Reason: {str(detail)[:80]}")

    if not failed:
        print(f"\n  🎉 Every order was placed successfully — nothing to do manually!")

    print(f"{'═' * 70}")


if __name__ == "__main__":
    main()
