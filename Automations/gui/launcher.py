#!/usr/bin/env python3
"""
ZUHRA Software — GUI Launcher v4
Full-page dashboard with sidebar navigation.
Every section gets its own page with proper spacing and scrollable content.
"""

import sys, subprocess, os

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
# `python gui/launcher.py` puts gui/ on sys.path, not Automations/: every `from utils…` needs this.
# _adopt_closing_runs runs 0.5 s after start, before anything else had added it, and failed with
# "No module named 'utils'" (8 Nov 2026), which also made the launcher drop the closing run's record.
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
# "Live" window for the Unmapped / Needs-Priority panels: only consider placer run logs written
# (modified) within this many seconds — the currently-active runs. Must match LIVE_WINDOW_S in
# dashboard/server.py so the GUI and dashboard always show the SAME live/current pending set.
LIVE_WINDOW_S = 2700  # 45 minutes


def _resolve_chrome_exe():
    """Path to the Chrome binary for THIS OS (mac hardcoded path fails on Windows)."""
    if platform.system() == "Windows":
        for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")):
            if os.path.exists(p):
                return p
        return "chrome.exe"   # rely on PATH as a last resort
    if platform.system() == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return "google-chrome"
COUPON_SCRIPT = os.path.join(SCRIPT_DIR, "engine/coupons.py")

# Memory softening for every GUI-launched Chrome — keep in sync with
# utils.helpers.CHROME_SOFT_FLAGS (stealth-neutral; caps renderer count,
# background services, telemetry and disk cache while 7+ Chromes run).
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

# Keep in sync with utils.helpers.HEADLESS_FLAGS. A closing Chrome still needs a window size:
# Seller Central lays its tables out responsively, and a default-sized headless viewport hides
# columns the scrape looks for.
HEADLESS_FLAGS = ["--headless=new", "--window-size=1920,1080", "--disable-gpu"]


def _resolve_default_runner_python():
    candidates = [
        os.path.join(PROJECT_ROOT, ".venv", "bin", "python"),
        os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe"),
        os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return sys.executable


DEFAULT_RUNNER_PYTHON = _resolve_default_runner_python()
RUNNER_PYTHON = (
    os.environ.get("AUTOMATION_RUNNER_PYTHON")
    or DEFAULT_RUNNER_PYTHON
)

# ── Auto-install ───────────────────────────────────────────────────────────────
REQUIRED = {
    "pandas": "pandas", "selenium": "selenium", "webdriver_manager": "webdriver-manager",
    "google.auth": "google-auth", "google_auth_oauthlib": "google-auth-oauthlib",
    "googleapiclient": "google-api-python-client", "pdfplumber": "pdfplumber",
    "requests": "requests", "openpyxl": "openpyxl", "tkcalendar": "tkcalendar",
}
def _auto_install():
    if RUNNER_PYTHON != sys.executable:
        return
    miss = [p for m, p in REQUIRED.items() if not _can_import(m)]
    if miss:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet",
                               "--user", "--break-system-packages"] + miss,
                              stdout=subprocess.DEVNULL)
def _can_import(m):
    try: __import__(m); return True
    except ImportError: return False
_auto_install()

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from tkcalendar import DateEntry
import json, signal, platform, re, socket, random, webbrowser, csv, glob, time, sqlite3
from collections import deque
from datetime import datetime, timedelta
CFG_PATH = os.path.join(SCRIPT_DIR, "bot_config.json")

# ── Remote control (dashboard → cloud agent → this GUI) ──────────────────────
# scripts/cloud_agent.py drops one JSON per command into data/control/queue/;
# _tick_control executes it on the Tk thread and answers in data/control/done/.
# _tick_state publishes what is running to data/launcher_state.json every 5 s —
# the agent heartbeats that file to R2 so every colleague's dashboard shows this
# launcher live (and can stop it). A state file older than 20 s = launcher closed.
CONTROL_QUEUE = os.path.join(SCRIPT_DIR, "data", "control", "queue")
CONTROL_DONE = os.path.join(SCRIPT_DIR, "data", "control", "done")
LAUNCHER_STATE = os.path.join(SCRIPT_DIR, "data", "launcher_state.json")
# Closing-side runs outlive the launcher (owner, 3 Nov 2026): a restart used to kill the chain, often
# hours in. Their output goes to a file and they run in their own session, so they only have to be
# left alone. This file tells the next launcher about them (_adopt_closing_runs). To stop one, use
# its Stop button: closing the launcher does not stop it.
CLOSING_RUNS = os.path.join(SCRIPT_DIR, "data", "closing_runs.json")
SURVIVES_CLOSE = ("bulk_", "messages_", "delivery_")


def _read_cfg_file():
    """bot_config.json as a dict ({} if missing/corrupt). Always re-read: the
    dashboard / cloud agent may change routing, chrome or automation keys live."""
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_cfg_file(cfg):
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)
    os.replace(tmp, CFG_PATH)


def _machine_id():
    try:
        if SCRIPT_DIR not in sys.path:
            sys.path.insert(0, SCRIPT_DIR)
        from utils.r2_store import machine_id
        return machine_id()
    except Exception:
        return socket.gethostname().split(".")[0]


def _git_sha():
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT,
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


PROFILES = ["DM_COUPON", "DM_RESELLER", "HS_COUPON", "HS_RESELLER", "WR_RESELLER"]
# The Amazon stores, in closing order (config/accounts.json). Wrenchly was added on 6 Nov 2026:
# reseller-only (WR_RESELLER, no coupon profile), its own closing Chrome 9337 and tracker 9339.
STORES = ("HOANK", "Hathoro", "Wrenchly")
STORE_BY_KEY = {s.lower(): s for s in STORES}          # 'wrenchly' → 'Wrenchly'
PORTS    = {"DM_COUPON": 9221, "DM_RESELLER": 9222, "HS_COUPON": 9223, "HS_RESELLER": 9224, "WR_RESELLER": 9226,
            "HOANK_CLOSING": 9331, "HATHORO_CLOSING": 9333, "WRENCHLY_CLOSING": 9337,
            "HOANK_TRACKER": 9335, "HATHORO_TRACKER": 9336, "WRENCHLY_TRACKER": 9339}
ICONS    = {"DM_COUPON": "🏷️", "DM_RESELLER": "📦", "HS_COUPON": "🏷️", "HS_RESELLER": "📦", "WR_RESELLER": "📦",
            "HOANK_CLOSING": "💰", "HATHORO_CLOSING": "💰", "WRENCHLY_CLOSING": "💰",
            "HOANK_TRACKER": "🚚", "HATHORO_TRACKER": "🚚", "WRENCHLY_TRACKER": "🚚"}
# The Chrome rows under the placers on the dashboard: every store's closing, then every tracker.
STORE_PORT_KEYS = [f"{s.upper()}_CLOSING" for s in STORES] + [f"{s.upper()}_TRACKER" for s in STORES]
PROFILE_CHOICES = {pk: f"{i} — {pk}" for i, pk in enumerate(PROFILES, 1)}
PROFILE_LABELS = list(PROFILE_CHOICES.values())
CONSOLE_VIEW_CHOICES = (["All", "Closing"] + [f"Closing {s}" for s in STORES] + ["Walmart",
                         "Orders", "Tracking"] + [f"Tracking {s}" for s in STORES] +
                        ["Coupons", "Listings"] + PROFILES)


def _bulk_accounts(name):
    """The stores a closing process covers: 'bulk_all' → every store, 'bulk_wrenchly' / 'messages_hoank'
    → that one. A multi-store chain started by the scheduler for some stores is still 'bulk_all'."""
    n = str(name or "").lower()
    if n == "bulk_all":
        return STORES
    return tuple(s for s in STORES if n.endswith("_" + s.lower()))
# Full-height pages: the console pane is hidden while one of these is open (the Dashboard
# needs the room for CURRENT RUN; Logs / Loss Orders have their own full views).
NO_CONSOLE_PAGES = ("dashboard", "logs", "loss_orders")


def _profile_key_from_choice(choice):
    value = str(choice or "").strip()
    if value in PROFILES:
        return value
    if "— " in value:
        _, value = value.split("— ", 1)
        value = value.strip()
        if value in PROFILES:
            return value
    if value.isdigit():
        idx = int(value) - 1
        if 0 <= idx < len(PROFILES):
            return PROFILES[idx]
    return PROFILES[0]


DEFAULT_CFG = {
    "priority_coupons": ["CLEANUP426", "ORGANIZE326"],
    "profiles": {
        "DM_COUPON":   {"target_label": "MR",      "priority_asins": [], "min_margin": -20, "order_limit": 0, "focus_priority_coupon": "FACILITY8M26", "focus_priority_coupon_enabled": False},
        "DM_RESELLER": {"target_label": "AB ECOM", "priority_asins": [], "min_margin": -20, "order_limit": 0},
        "HS_COUPON":   {"target_label": "MR",      "priority_asins": [], "min_margin": -20, "order_limit": 0, "focus_priority_coupon": "FACILITY8M26", "focus_priority_coupon_enabled": False},
        "HS_RESELLER": {"target_label": "Rajar",   "priority_asins": [], "min_margin": -20, "order_limit": 0, "payment_method": "NET30"},
        "WR_RESELLER": {"target_label": "WR",      "priority_asins": [], "min_margin": 0,   "order_limit": 0},
    },
    "velocity": {"max_per_cycle": 0, "inter_order_delay": 0, "cycle_sleep": 120},
    "blocked_order_ids": [],
    "telegram": {"enabled": True},
}


# ── Watch list (🛑 Watch list, right end of the bar under the header) ──────────────────────────────
# Owner, 11 Nov 2026: "in the launcher, I would like to see what ASINs are in the watch list". A listing goes
# on the list (engine/listing_guard.py → table listing_oos) when the placer finds its Zoro code and every
# alternative out of stock; while that store's placer runs it asks Zoro again every restock_check_minutes and
# the listing leaves the list when Zoro has the item again (status 'restored' or 'restock_told').
# Read-only here, never written.
WATCH_LIST_DB = os.path.join(SCRIPT_DIR, "data", "automations_unified.db")
_WATCH_LIST_CLOSED = ("restored", "restock_told")       # = listing_guard._CLOSED: off the list
_WATCH_LIST_COLS = ("store", "asin", "seller_sku", "title", "zoro_code", "alt_codes", "kind", "brand", "kind_from",
                    "orders", "last_order", "first_seen", "last_seen", "status", "action", "action_at", "note",
                    "prev_value", "changed_to", "restock_checked_at", "restored_at")
# (row key, column heading, width) of the window's table
_WATCH_LIST_TREE = (("store", "Store", 70), ("asin", "ASIN", 105), ("sku", "SKU", 130), ("kind", "PL/DS", 48),
                    ("title", "Title", 250), ("zoro", "Zoro code (+ alternatives)", 160),
                    ("did", "What the placer did", 200), ("since", "On the list since", 112),
                    ("checked", "Last Zoro check", 118), ("orders", "OOS orders", 72))
# The second table, "Back in stock — needs a person": listings Zoro restocked in the last _WATCH_LIST_BACK_DAYS
# days (status 'restock_told': every restock the placer did not put back itself). Its note picks the 'To do'
# text. Same first columns as the table above.
_WATCH_LIST_BACK_DAYS = 3
_WATCH_LIST_BACK_TREE = (("store", "Store", 70), ("asin", "ASIN", 105), ("sku", "SKU", 130), ("kind", "PL/DS", 48),
                         ("title", "Title", 250), ("code", "Zoro code back", 160), ("todo", "To do on Amazon", 200),
                         ("back", "Back at Zoro since", 112), ("why", "Placer's note", 190))
_WATCH_LIST_STATE = {
    "new": "Waiting for the placer: it decides at the end of its next cycle.",
    "working": "The placer is changing it on Seller Central right now.",
    "recheck": "Orders kept coming, so the placer will look at Seller Central again.",
    "changed": "Done on Seller Central. Waiting for Zoro to have it again.",
    "alerted": "Nothing was changed on Seller Central. Waiting for Zoro to have it again.",
    "restoring": "Zoro has it again: the placer is putting the listing back now.",
}


def _watch_list_settings():
    """bot_config.json → listing_guard, read the way engine.listing_guard.settings() reads it: {'every': minutes
    between the placer's Zoro checks (30 by default, 5 at least, as in _restock_pass), 'restore', 'enabled'}.
    Never raises (it runs while the launcher's header is built): a missing or odd value keeps the default."""
    out = {"every": 30, "restore": True, "enabled": True}
    try:
        cfg = _read_cfg_file()
        raw = cfg.get("listing_guard") if isinstance(cfg, dict) else None
        if not isinstance(raw, dict):
            return out
        for k in ("restore", "enabled"):
            v = raw.get(k)
            if v is not None and v != "":
                out[k] = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
        v = raw.get("restock_check_minutes")
        if v is not None and v != "":
            out["every"] = max(5, int(float(v)))
    except Exception:
        pass
    return out


def _watch_list_every():
    """bot_config.json → listing_guard.restock_check_minutes (30 by default, as in listing_guard). Never raises."""
    try:
        return int(_watch_list_settings()["every"])
    except Exception:
        return 30


def _watch_list_intro(cfg):
    """The line under the window's heading: how a listing gets on the list and leaves it."""
    try:
        every = int(cfg.get("every") or 30)
        on = bool(cfg.get("restore", True)) and bool(cfg.get("enabled", True))
        off = "enabled" if not cfg.get("enabled", True) else "restore"
    except Exception:
        every, on, off = 30, True, ""
    check = (f"While that store's placer is running it checks Zoro every {every} minutes; when Zoro has the item "
             "again it puts back the quantity or handling time it changed itself, and the listing leaves the list. "
             f"⚠️ in 'Last Zoro check' = no check for more than {2 * every} minutes." if on else
             f"The restock check is switched off (bot_config.json → listing_guard.{off} is false): when Zoro has "
             "the item again, nothing is put back, so please do it by hand.")
    return ("The placer puts a listing here when Zoro is out of its item and of every alternative in the sheet. "
            + check + " Click a row for its details, double-click to open it in Seller Central.")


def _watch_list_age(ts, now=None):
    """Minutes since a local 'YYYY-mm-dd HH:MM:SS' time (listing_guard._now), None when empty or unreadable."""
    try:
        t = datetime.strptime(str(ts or "").strip()[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return ((now or datetime.now()) - t).total_seconds() / 60.0


def _watch_list_ago(minutes):
    """95 → '1 h 35 min', 3000 → '2 days 2 h'."""
    m = int(max(0.0, float(minutes or 0)))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} days {h} h" if h else f"{d} days"


def _watch_list_url(asin):
    """The listing in Seller Central's Manage Inventory (the link engine.listing_guard.listing_url builds)."""
    import urllib.parse
    return ("https://sellercentral.amazon.com/myinventory/inventory?fulfilledBy=all&page=1&pageSize=25"
            f"&searchField=all&searchTerm={urllib.parse.quote(str(asin or ''))}&sort=date_created_desc&status=all")


def _watch_list_short_title(title, limit=48):
    """'Eagle Plastic Drum, 30 Gallon Drum, DOT Compliant' — the title's first comma parts, cut at a word."""
    t = " ".join(str(title or "").split()).split(" | ")[0].strip(" ,.-")
    out = ""
    for part in (p.strip() for p in t.split(",")):
        if not part:
            continue
        if out and len(out) + 2 + len(part) > limit:
            break
        out = f"{out}, {part}" if out else part
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].rstrip(" ,.-") + "…"
    return out


def _watch_list_when(ts):
    """'2026-11-11 14:26:18' → '11 Nov 14:26' ('' when empty)."""
    s = str(ts or "").strip()
    if not s:
        return ""
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").strftime("%d %b %H:%M")
    except ValueError:
        return s[:16]


def _watch_list_did(r):
    """What the placer did on Seller Central, in a few words: 'Quantity 11 → 0', 'Handling 1 → 7 days',
    'Already 0', 'Already 7 days', 'Could not change …', 'Error — see details', 'Alert only (guard not armed)',
    'Not decided yet'. prev_value / changed_to are the guard's own change (what a restore puts back); without
    them the field already held the value, or the change failed (status 'alerted'): the note tells which —
    'error: …' when handle_pending caught an exception, '… switched off (listing_guard.armed)' when the guard
    only alerts."""
    st = str(r.get("status") or "new").strip()
    if st == "new":
        return "Not decided yet"
    if st == "working":
        return "Being changed now"
    act = str(r.get("action") or "").strip()
    prev, to = str(r.get("prev_value") or "").strip(), str(r.get("changed_to") or "").strip()
    note = " ".join(str(r.get("note") or "").split())
    m = re.search(r"already (\d+)", note)
    if note.lower().startswith("error:"):
        did = "Error — see details"
    elif "switched off" in note.lower():
        did = "Alert only (guard not armed)"
    elif act not in ("qty0", "handling"):
        did = "Could not tell PL or DS"
    elif to:
        did = f"Quantity {prev or '?'} → {to}" if act == "qty0" else f"Handling {prev or 'default'} → {to} days"
    elif st in ("changed", "recheck", "restoring"):
        # 'quantity was already N' / 'handling time was already N days' are written only on a success
        # (status 'changed'); on a failed row 'already 3 tries' would read as success
        n = m.group(1) if m else ""
        did = f"Already {n or '0'}" if act == "qty0" else (f"Already {n} days" if n else "Already raised")
    else:
        did = "Could not change quantity" if act == "qty0" else "Could not change handling time"
    if st == "recheck":
        did += " · checking again"
    elif st == "restoring":
        did += " · putting back now"
    return did


def _watch_list_detail(r):
    """The lines under the tables for the selected listing: full title, PL / DS, where it stands, the note."""
    kind, brand, src = r.get("kind") or "", r.get("brand") or "", r.get("kind_from") or ""
    if r.get("section") == "back":              # "Back in stock — needs a person"
        mine = ("Our own brand" if kind == "PL" else "Not our brand" if kind == "DS" else "PL or DS was not decided")
        why = str(r.get("why") or "").rstrip(" .")
        lines = [f"{r.get('store', '')} · {r.get('asin', '')} · {r.get('full_title') or r.get('title') or ''}",
                 f"{mine}{f' ({brand})' if brand and kind in ('PL', 'DS') else ''}. Zoro has {r.get('code') or '?'} "
                 f"again since {r.get('back') or '?'}. {why}.",
                 f"To do: {r.get('todo') or 'check the listing'} on Seller Central — double-click the row to open it."]
        if r.get("last_order"):
            lines[-1] += f" · Last order marked out of stock: {r['last_order']}"
        return "\n".join(lines)
    if kind == "PL":
        what = f"Our own brand{f' ({brand})' if brand else ''}: it stays on sale with a longer handling time."
    elif kind == "DS":
        what = f"Not our brand{f' ({brand})' if brand else ''}: it goes off sale at Amazon quantity 0."
    else:
        what = "PL or DS is not decided yet."
    if kind and src:
        what += f" Brand from the {src}."
    lines = [f"{r.get('store', '')} · {r.get('asin', '')} · {r.get('full_title') or r.get('title') or ''}",
             f"{what} {_WATCH_LIST_STATE.get(r.get('status') or '', r.get('status') or '')}"]
    tail = []
    if r.get("last_order"):
        tail.append(f"Last order marked out of stock: {r['last_order']}"
                    + (f" ({r['last_seen']})" if r.get("last_seen") else ""))
    if r.get("note"):
        tail.append(f"Placer's note: {r['note']}")
    if tail:
        lines.append(" · ".join(tail))
    if r.get("stale"):
        lines.append(f"⚠️ No Zoro check for {_watch_list_ago(r.get('check_age'))}, more than twice the "
                     f"{r.get('every') or 30} minutes between checks: the {r.get('store') or ''} placer has not asked "
                     "Zoro since. It asks only while it runs, at the end of a placing cycle.")
    elif r.get("check_on") is False:
        lines.append("The restock check is switched off in bot_config.json (listing_guard): when Zoro has the item "
                     "again, nothing is put back, so please do it by hand.")
    return "\n".join(lines)


def _watch_list_query(where, params, order, db_path=None, timeout=3.0):
    """listing_oos rows (dicts with the _WATCH_LIST_COLS keys; a column the table lacks reads None):
    (rows, problem). rows is None when the table is not there yet; problem starts with '⚠️' when the
    database could not be read. Read-only connection; never raises sqlite3 errors."""
    path = db_path or WATCH_LIST_DB
    if not os.path.exists(path):
        return [], f"⚠️ The database is not there: {path}"
    try:
        import urllib.parse
        con = sqlite3.connect(f"file:{urllib.parse.quote(path)}?mode=ro", uri=True, timeout=timeout)
    except sqlite3.Error as e:
        return [], f"⚠️ Could not open the database: {e}"
    try:
        have = {c[1] for c in con.execute("PRAGMA table_info(listing_oos)")}
        if not have:
            return None, ""
        con.row_factory = sqlite3.Row
        pick = ", ".join(c if c in have else f"NULL AS {c}" for c in _WATCH_LIST_COLS)
        return [dict(x) for x in con.execute(f"SELECT {pick} FROM listing_oos WHERE {where} ORDER BY {order}",
                                             params)], ""
    except sqlite3.Error as e:
        return [], f"⚠️ Could not read the watch list: {e}"
    finally:
        con.close()


def _watch_list_rows(db_path=None, timeout=3.0, cfg=None):
    """The listings on the watch list, newest first: (rows, note). Each row is a dict with the table's
    columns (_WATCH_LIST_TREE keys) plus the details. note says why the list is empty, and starts with
    '⚠️' when the database could not be read. cfg = _watch_list_settings() (read here when not given).
    Read-only connection; never raises."""
    raw, problem = _watch_list_query("COALESCE(status, 'new') NOT IN (?, ?)", _WATCH_LIST_CLOSED,
                                     "COALESCE(first_seen, '') DESC, COALESCE(last_seen, '') DESC", db_path, timeout)
    if problem:
        return [], problem
    if raw is None:
        return [], "Nothing on the watch list yet: the placer has not found a listing out of stock at Zoro."
    cfg = cfg if isinstance(cfg, dict) else _watch_list_settings()
    every = int(cfg.get("every") or 30)
    on = bool(cfg.get("restore", True)) and bool(cfg.get("enabled", True))
    now = datetime.now()
    rows = []
    for r in raw:
        alts = ", ".join(a.strip() for a in str(r.get("alt_codes") or "").split(",") if a.strip())
        try:
            orders = int(r.get("orders") or 0)
        except (TypeError, ValueError):
            orders = 0
        title = " ".join(str(r.get("title") or "").split())
        did = _watch_list_did(r)
        st = r.get("status") or "new"
        # 'Last Zoro check': the restock pass asks Zoro about 'changed' / 'alerted' rows every `every` minutes,
        # counted from the change (action_at) until the first check. Older than twice that = the store's placer
        # has not run a cycle for a while. A ttk.Treeview cell has no colour of its own: ⚠️ marks it.
        ref = r.get("restock_checked_at") or (r.get("action_at") if st in ("changed", "alerted") else "")
        age = _watch_list_age(ref, now) if ref else None
        # only rows the restock pass really asks about (listing_guard._restock_pass: 'changed' / 'alerted'
        # with a Zoro code) can be overdue
        checked_rows = st in ("changed", "alerted") and bool(str(r.get("zoro_code") or "").strip())
        stale = bool(on and checked_rows and age is not None and age > 2 * every)
        checked = _watch_list_when(r.get("restock_checked_at")) or ("not yet" if on else "restock check off")
        rows.append({
            "section": "list",
            "store": r.get("store") or "", "asin": r.get("asin") or "", "sku": r.get("seller_sku") or "",
            "kind": r.get("kind") or "?", "title": _watch_list_short_title(title) or "—",
            "zoro": (str(r.get("zoro_code") or "").strip() or "?") + (f" + {alts}" if alts else ""),
            "did": did, "since": _watch_list_when(r.get("first_seen")),
            "checked": f"⚠️ {checked}" if stale else checked, "orders": orders,
            # details (the lines under the tables)
            "full_title": title, "brand": r.get("brand") or "", "kind_from": r.get("kind_from") or "",
            "status": st, "note": r.get("note") or "", "last_order": r.get("last_order") or "",
            "last_seen": _watch_list_when(r.get("last_seen")),
            "attention": did.startswith(("Could not", "Error", "Alert only")),
            "stale": stale, "check_age": age, "every": every, "check_on": on,
        })
    return rows, ("" if rows else "The watch list is empty: no listing is waiting for Zoro to restock.")


def _watch_list_note_tail(note, main=""):
    """'Zoro G2388429 is in stock again; the placer had not changed …' → ('G2388429', 'The placer had not
    changed …'): the Zoro code that came back and what the placer said after it (listing_guard._restore)."""
    m = re.match(r"Zoro (\S+) is in stock again\s*(?:[;,—–-]\s*(?:but\s+)?)?(.*)$", str(note or ""), re.S)
    if not m:
        return (str(main or "").strip() or "?"), str(note or "")
    tail = m.group(2).strip()
    return m.group(1), ((tail[:1].upper() + tail[1:]) if tail else str(note or ""))


def _watch_list_back_rows(db_path=None, timeout=3.0, days=_WATCH_LIST_BACK_DAYS):
    """The table 'Back in stock — needs a person': listings Zoro restocked in the last `days` days (status 'restock_told',
    restored_at since then) whose note says the placer left the Amazon listing as it was — it had not changed
    it, did not know the old value, could not put it back, or found it changed by hand since. Newest first:
    (rows, note); note starts with '⚠️' when the database could not be read. Read-only; never raises."""
    raw, problem = _watch_list_query("COALESCE(status, '') = ?", ("restock_told",),
                                     "COALESCE(restored_at, '') DESC", db_path, timeout)
    if problem:
        return [], problem.replace("the watch list", "the restocked listings")
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for r in raw or []:
        note = " ".join(str(r.get("note") or "").split())
        low = note.lower()
        # every restock_told row needs a person (listing_guard._restore gives that status to every outcome
        # but a real restore); the phrases only pick the 'To do' text below
        if str(r.get("restored_at") or "")[:19] < since:
            continue
        code, why = _watch_list_note_tail(note, r.get("zoro_code"))
        act, prev = str(r.get("action") or "").strip(), str(r.get("prev_value") or "").strip()
        what = {"qty0": "quantity", "handling": "handling time"}.get(act, "")
        if not what:
            todo = "Check the listing by hand"
        elif "changed by hand since" in low:
            todo = f"Check the {what}: changed by hand"
        elif "could not be put back" in low and prev.isdigit() and (act == "handling" or int(prev) > 0):
            todo = f"Set the {what} back to {prev}" + ("" if act != "handling" else " day" if prev == "1" else " days")
        else:
            todo = f"Set the {what} by hand"
        title = " ".join(str(r.get("title") or "").split())
        rows.append({
            "section": "back",
            "store": r.get("store") or "", "asin": r.get("asin") or "", "sku": r.get("seller_sku") or "",
            "kind": r.get("kind") or "?", "title": _watch_list_short_title(title) or "—",
            "code": code, "todo": todo, "back": _watch_list_when(r.get("restored_at")), "why": why,
            # details (the lines under the tables)
            "full_title": title, "brand": r.get("brand") or "", "kind_from": r.get("kind_from") or "",
            "status": r.get("status") or "", "note": note, "last_order": r.get("last_order") or "",
            "attention": "could not be put back" in low,
        })
    return rows, ""


class _Tip:
    """Minimal themed hover tooltip. Shows after a short delay, follows theme."""
    def __init__(self, widget, text, app):
        self.w, self.text, self.app = widget, text, app
        self.tw = None
        self.after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self.after_id = self.w.after(550, self._show)

    def _cancel(self):
        if self.after_id:
            try:
                self.w.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    def _show(self):
        if self.tw:
            return
        try:
            x = self.w.winfo_rootx() + 10
            y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        except Exception:
            return
        self.tw = tk.Toplevel(self.w)
        self.tw.wm_overrideredirect(True)
        self.tw.configure(bg=self.app.BORDER)
        tk.Label(self.tw, text=self.text, justify="left",
                 font=(self.app.FONT, 9), bg="#1e2030", fg=self.app.TEXT,
                 wraplength=340, padx=9, pady=6).pack(padx=1, pady=1)
        self.tw.wm_geometry(f"+{x}+{y}")
        try:
            self.tw.attributes("-topmost", True)
        except Exception:
            pass

    def _hide(self, _e=None):
        self._cancel()
        if self.tw:
            try:
                self.tw.destroy()
            except Exception:
                pass
            self.tw = None


# ═══════════════════════════════════════════════════════════════════════════════
class App(tk.Tk):
    # ── Theme ─────────────────────────────────────────────────────────────
    BG         = "#0a0c10"
    PANEL      = "#12141c"
    CARD       = "#161822"
    BORDER     = "#1c1f2e"
    ACCENT     = "#7aa2f7"
    GREEN      = "#73daca"
    RED        = "#f7768e"
    AMBER      = "#e0af68"
    CYAN       = "#7dcfff"
    PURPLE     = "#bb9af7"
    TEXT       = "#c0caf5"
    DIM        = "#565f89"
    MUTED      = "#363b54"
    INP_BG     = "#0d0f16"
    FONT       = "Helvetica Neue"
    MONO       = "SF Mono"
    NAV_HOVER  = "#1a1d2e"
    NAV_ACTIVE = "#1e2030"

    def __init__(self):
        super().__init__()
        self.title("ZUHRA Software")
        self.geometry("1440x900")
        self.minsize(1200, 750)
        self.configure(bg=self.BG)
        self._resolve_mono()

        self._procs = {}
        self._readers = {}
        self._tags = {}
        self._polling = False
        self._stats = {}
        self._start_times = {}
        # Full session retention (was 8000): the user must be able to scroll back
        # to LAUNCHER START at any time. data/console_log.txt remains the
        # permanent on-disk record across sessions.
        self._console_history = deque(maxlen=150000)
        self._console_log_path = os.path.join(SCRIPT_DIR, "data", "console_log.txt")
        self._console_log_date = datetime.now().strftime("%Y-%m-%d")
        self._total_placed = 0
        self._total_profit = 0.0
        self._last_wb = ""

        # Raw debug logs file (verbose API / dup-check output goes here, not console)
        self._raw_log_path = os.path.join(SCRIPT_DIR, "data", "raw_logs.txt")
        # While _poll hands over a tick's worth of lines, the two log files are written and the
        # boxes scrolled once for the whole tick instead of once per line (_end_log_batch).
        self._in_poll = False
        self._poll_logged = False
        self._console_log_buf = []
        self._raw_log_buf = []

        self._current_page = "dashboard"
        # Attention panels (Unmapped / Needs Priority) are LIVE, session-scoped: they show only
        # runs started during THIS launcher session. Empty on launch → fill as the placer runs →
        # clear as you map/label and it re-runs → wiped on close (next launch = new session).
        self._session_start = time.time()
        self._nav_items = {}
        self._pages = {}

        # Log-tab auto-refresh state
        self._log_file_mtime = 0
        self._log_watch_id = None

        self._closing_down = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._install_quit_hooks()
        self._build()

    def _resolve_mono(self):
        for f in ("SF Mono", "JetBrains Mono", "Fira Code", "Menlo", "Consolas", "Courier"):
            try:
                import tkinter.font as tkfont
                if f in tkfont.families():
                    self.MONO = f
                    return
            except Exception:
                pass

    # ── Widget factories ──────────────────────────────────────────────────
    def _panel(self, parent, **kw):
        outer = tk.Frame(parent, bg=self.BORDER, **kw)
        inner = tk.Frame(outer, bg=self.CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return outer, inner

    def _heading(self, parent, text, color=None, subtitle=None):
        color = color or self.ACCENT
        f = tk.Frame(parent, bg=self.CARD)
        f.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(f, text=text, font=(self.FONT, 11, "bold"),
                 bg=self.CARD, fg=color).pack(side="left")
        if subtitle:
            tk.Label(f, text=f"  {subtitle}", font=(self.FONT, 9),
                     bg=self.CARD, fg=self.DIM).pack(side="left")
        return f

    def _lbl(self, parent, text, **kw):
        o = dict(font=(self.FONT, 10), bg=self.CARD, fg=self.TEXT)
        o.update(kw)
        return tk.Label(parent, text=text, **o)

    def _dim(self, parent, text, **kw):
        o = dict(font=(self.FONT, 9), bg=self.CARD, fg=self.DIM)
        o.update(kw)
        return tk.Label(parent, text=text, **o)

    def _btn(self, parent, text, color, cmd, small=False, **kw):
        sz = 9 if small else 10
        bg = self.CARD
        b = tk.Button(parent, text=text, command=cmd,
                      font=(self.FONT, sz, "bold"), fg=color, bg=bg,
                      activeforeground=color, activebackground="#1e2030",
                      relief="flat", bd=0, padx=12, pady=5, cursor="hand2",
                      highlightthickness=1, highlightbackground=self.BORDER,
                      highlightcolor=color, **kw)
        b.bind("<Enter>", lambda e, b=b, c=color: b.config(bg="#1a1d2e", highlightbackground=c))
        b.bind("<Leave>", lambda e, b=b: b.config(bg=bg, highlightbackground=self.BORDER))
        return b

    def _entry(self, parent, var=None, w=10):
        return tk.Entry(parent, textvariable=var, width=w,
                        font=(self.MONO, 10), bg=self.INP_BG, fg=self.TEXT,
                        insertbackground=self.ACCENT, relief="flat", bd=0,
                        highlightthickness=1, highlightbackground=self.BORDER,
                        highlightcolor=self.ACCENT)

    def _combo(self, parent, vals, w=12):
        sn = f"C{id(parent)}.TCombobox"
        ttk.Style(self).configure(sn, font=(self.FONT, 10))
        return ttk.Combobox(parent, values=vals, state="readonly", width=w,
                            style=sn, font=(self.FONT, 10))

    def _tip(self, widget, text):
        """Attach a hover tooltip to any widget. Returns the widget."""
        _Tip(widget, text, self)
        return widget

    def _sep(self, parent):
        tk.Frame(parent, bg=self.BORDER, height=1).pack(fill="x", padx=14, pady=6)

    # ══════════════════════════════════════════════════════════════════════
    #  BUILD — sidebar + content (pages top / console bottom)
    # ══════════════════════════════════════════════════════════════════════
    def _build(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TCombobox", fieldbackground=self.INP_BG, background=self.CARD,
                     foreground=self.TEXT, arrowcolor=self.DIM)
        s.map("TCombobox", fieldbackground=[("readonly", self.INP_BG)],
              selectbackground=[("readonly", self.INP_BG)],
              selectforeground=[("readonly", self.TEXT)])
        s.configure("TCheckbutton", background=self.CARD, foreground=self.TEXT,
                     font=(self.FONT, 10))
        s.map("TCheckbutton", background=[("active", self.CARD)])
        s.configure("Vertical.TScrollbar", background=self.BORDER,
                     troughcolor=self.CARD, arrowcolor=self.DIM, borderwidth=0)

        # ── HEADER ────────────────────────────────────────────────────
        self._build_header()
        tk.Frame(self, bg=self.ACCENT, height=2).pack(fill="x")
        self._build_toast()

        # ── FOOTER (pack before body so body claims the middle) ───────
        footer = tk.Frame(self, bg=self.CARD, height=26)
        footer.pack(side="bottom", fill="x")
        footer.pack_propagate(False)
        self._global_msg = tk.Label(footer, text="", font=(self.FONT, 9),
                                    bg=self.CARD, fg=self.DIM)
        self._global_msg.pack(side="left", padx=16)

        # ── BODY: sidebar + main area ─────────────────────────────────
        body = tk.Frame(self, bg=self.BG)
        body.pack(fill="both", expand=True)

        sidebar = tk.Frame(body, bg=self.PANEL, width=170)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self._build_sidebar(sidebar)

        # Main area: PanedWindow for page + console split
        self._main_pw = tk.PanedWindow(body, orient=tk.VERTICAL, bg=self.BORDER,
                                        sashwidth=5, sashrelief="flat",
                                        opaqueresize=True, bd=0)
        self._main_pw.pack(side="left", fill="both", expand=True)

        # Pages (top pane)
        self._page_container = tk.Frame(self._main_pw, bg=self.BG)
        self._main_pw.add(self._page_container, minsize=280, stretch="always")

        # Console (bottom pane)
        self._console_frame = tk.Frame(self._main_pw, bg=self.BG)
        self._main_pw.add(self._console_frame, minsize=150, stretch="never")
        self._build_console(self._console_frame)

        # ── Build all pages ───────────────────────────────────────────
        self._pages["dashboard"]     = self._build_page_dashboard(self._page_container)
        self._pages["orders"]        = self._build_page_orders(self._page_container)
        self._pages["tracking"]      = self._build_page_tracking(self._page_container)
        self._pages["coupons"]       = self._build_page_coupons(self._page_container)
        self._pages["post_delivery"] = self._build_page_post_delivery(self._page_container)
        self._pages["closing"]       = self._build_page_closing(self._page_container)
        self._pages["listings"]      = self._build_page_listings(self._page_container)
        self._pages["private_label"] = self._build_page_private_label(self._page_container)
        self._pages["repricer"]      = self._build_page_repricer(self._page_container)
        self._pages["walmart"]       = self._build_page_walmart(self._page_container)
        self._pages["price_log"]     = self._build_page_price_log(self._page_container)
        self._pages["logs"]          = self._build_page_logs(self._page_container)
        self._pages["loss_orders"]   = self._build_page_loss_orders(self._page_container)
        self._pages["unmapped"]      = self._build_page_unmapped(self._page_container)
        self._pages["needs_priority"] = self._build_page_needs_priority(self._page_container)
        self._pages["locked"]        = self._build_page_locked_orders(self._page_container)
        self._pages["settings"]      = self._build_page_settings(self._page_container)

        self._pages["dashboard"].pack(fill="both", expand=True)
        self._highlight_nav("dashboard")
        if self._current_page in NO_CONSOLE_PAGES:
            self._main_pw.forget(self._console_frame)

        self._tick_clock()
        self._tick_toast()
        self._load_config()
        self.after(500, self._check_ports)
        self.after(2000, self._refresh_unmapped)   # populate UNMAPPED ASINs from run logs on launch
        self.after(2200, self._refresh_needs_priority)  # populate NEEDS-PRIORITY ASINs on launch
        self.after(2400, self._refresh_locked_orders)   # populate LOCKED ORDERS on launch
        self.after(45000, self._periodic_attention_refresh)  # auto-refresh the visible attention panel
        self._autosync_running = False
        self.after(15000, self._tick_autosync)   # first gmail_archive sync 15s after launch
        self._fast_sync_running = False
        self.after(20000, self._tick_fast_sync)  # lightweight placer-log sync every 60s
        self._watch_running = False
        self.after(30000, self._tick_watch)      # hourly Zoro restock/handling-time check
        self.after(500, self._adopt_closing_runs)  # closing still running from before a restart
        self.after(1500, self._tick_state)       # publish running processes → data/launcher_state.json
        self.after(3000, self._tick_control)     # remote commands from the dashboard (via cloud agent)
        self.after(60000, self._tick_auto_closing)  # scheduled closing (bot_config automation.auto_closing)

    # ══════════════════════════════════════════════════════════════════════
    #  REMOTE CONTROL + STATE  (dashboard ⇄ cloud agent ⇄ this GUI)
    # ══════════════════════════════════════════════════════════════════════
    def _tick_state(self):
        """Every 5 s: publish what is running to data/launcher_state.json. The
        cloud agent heartbeats it to R2 so every colleague's dashboard shows this
        launcher live; a file older than 20 s means the launcher is closed."""
        if self._closing_down:      # _on_close removed the file: don't announce us as open again
            return
        try:
            if not hasattr(self, "_git_sha_cache"):
                self._git_sha_cache = _git_sha()
            procs = {}
            for name, p in list(self._procs.items()):
                if p.poll() is None:
                    st = self._start_times.get(name)
                    procs[name] = {"pid": p.pid, "tag": self._tags.get(name, name),
                                   "since": st.strftime("%Y-%m-%dT%H:%M:%S") if st else None}
            cfg = _read_cfg_file()
            state = {
                "pid": os.getpid(), "host": _machine_id(),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "started_at": datetime.fromtimestamp(self._session_start).strftime("%Y-%m-%dT%H:%M:%S"),
                "procs": procs,
                "closing": {"auto": (cfg.get("automation") or {}).get("auto_closing", {}),
                            "last_auto_runs": getattr(self, "_auto_closing_last", {})},
                "headless": bool((cfg.get("chrome") or {}).get("headless")),
                "version": self._git_sha_cache,
            }
            os.makedirs(os.path.dirname(LAUNCHER_STATE), exist_ok=True)
            tmp = LAUNCHER_STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, LAUNCHER_STATE)
            self._save_closing_runs()
        except Exception:
            pass
        finally:
            self._state_id = self.after(5000, self._tick_state)

    def _save_closing_runs(self):
        """Write what the next launcher needs to pick up the running closing-side runs. Every 5 s
        (_tick_state) as well as on close, so a launcher crash repeats at most 5 s of log."""
        runs = {}
        for name, proc in list(self._procs.items()):
            if name.startswith(SURVIVES_CLOSE) and name in self._readers:
                st = self._start_times.get(name)
                runs[name] = {"pid": proc.pid, "args": [str(a) for a in proc.args],
                              "tag": self._tags.get(name, name), "log": self._readers[name][2],
                              "offset": self._readers[name][1].tell(),
                              "since": st.isoformat() if st else None}
        if runs:
            tmp = CLOSING_RUNS + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(runs, f)
            os.replace(tmp, CLOSING_RUNS)
        elif os.path.exists(CLOSING_RUNS):
            os.remove(CLOSING_RUNS)

    def _adopt_closing_runs(self):
        """Pick up the closing-side runs the last launcher left: their log, their buttons, their
        Stop. One that ended while no launcher was open takes the same path: _poll shows the rest
        of its log, files it in data/logs and marks it finished."""
        try:
            with open(CLOSING_RUNS, encoding="utf-8") as f:
                runs = json.load(f)
        except (OSError, ValueError):
            return
        from utils.proc import AdoptedProcess
        for name, r in runs.items():
            if name in self._procs:
                continue
            try:
                proc = AdoptedProcess(r["pid"], r.get("args") or [])
                rfh = open(r["log"], "r", encoding="utf-8", errors="replace")
                rfh.seek(r.get("offset") or 0)
                tag = r.get("tag") or name
                self._tags[name] = tag
                self._start_times[name] = datetime.fromisoformat(r["since"]) if r.get("since") else datetime.now()
                self._procs[name] = proc
                self._readers[name] = (open(os.devnull, "w"), rfh, r["log"])
            except Exception as e:
                self._feed_line(f"Could not pick up {name}: {e}", "warn")
                continue
            if proc.poll() is None:
                accts = _bulk_accounts(name)
                for a in accts:
                    self._set_cls_state(a, running=True)
                self._log_line(f"▶  {tag.upper()} still running from before the launcher restart  |  PID {proc.pid}", source=name)
                self._feed_line(f"{tag.upper()} still running from before the restart: picked up", "ok")
        self._refresh_cls_lbl()
        self._update_hdr()
        if self._readers and not self._polling:
            self._polling = True
            self.after(250, self._poll)

    def _tick_control(self):
        """Every 2 s: execute commands the cloud agent dropped in data/control/queue/
        (they originate from the dashboard's Ops panel) and answer in done/."""
        try:
            if os.path.isdir(CONTROL_QUEUE):
                # ORDER MATTERS: file names are random ids, so sorting by name executed
                # commands in arbitrary order — a stop queued before a start could run after
                # it and kill the process that had just been started (seen 18 Sep 00:48:53).
                # Oldest file first is the order they were actually queued in.
                def _queued_at(name):
                    try:
                        return os.path.getmtime(os.path.join(CONTROL_QUEUE, name))
                    except OSError:
                        return 0.0
                for fn in sorted((f for f in os.listdir(CONTROL_QUEUE) if f.endswith(".json")),
                                 key=_queued_at):
                    if not fn.endswith(".json"):
                        continue
                    path = os.path.join(CONTROL_QUEUE, fn)
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            cmd = json.load(f)
                    except Exception:
                        cmd = {}
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    if not isinstance(cmd, dict) or not cmd.get("action"):
                        continue
                    try:
                        ok, result = self._exec_control(cmd)
                    except Exception as e:
                        ok, result = False, f"error: {e}"
                    who = cmd.get("author") or "dashboard"
                    self._feed_line(f"Remote {cmd['action']} by {who}: {result}", "ok" if ok else "warn")
                    self._log_line(f"🛰️  remote command '{cmd['action']}' from {who} → "
                                   f"{'ok' if ok else 'FAILED'}: {result}", source="remote")
                    try:
                        os.makedirs(CONTROL_DONE, exist_ok=True)
                        with open(os.path.join(CONTROL_DONE, f"{cmd.get('id', fn[:-5])}.json"), "w",
                                  encoding="utf-8") as f:
                            json.dump({"id": cmd.get("id"), "ok": ok, "result": result,
                                       "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f)
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            self._control_id = self.after(2000, self._tick_control)

    def _exec_control(self, cmd):
        """Run one remote command on the Tk thread. Returns (ok, message)."""
        a = str(cmd.get("action") or "")
        args = cmd.get("args") or {}
        prof = _profile_key_from_choice(args["profile"]) if args.get("profile") else None
        acct = str(args.get("account") or "").strip()
        acct_title = STORE_BY_KEY.get(acct.lower(), acct)
        if a == "stop_all":
            n = len(self._procs)
            self._stop_all()
            return True, f"stopping {n} process(es)"
        if a == "stop_orders":
            names = [f"orders_{prof}"] if prof else [f"orders_{pk}" for pk in PROFILES]
            hit = [n for n in names if n in self._procs]
            for n in hit:
                self._stop(n)
            return True, f"stopping {', '.join(hit) if hit else 'nothing (no placer running)'}"
        if a == "start_orders":
            if not prof:
                return False, "profile required"
            holder = self._lease_blocks_start()
            if holder:
                return False, f"placers are running on '{holder}' — refused (duplicate protection)"
            return (True, f"{prof} started") if self._start_orders_key(prof) else (True, f"{prof} already running")
        if a == "start_all_orders":
            holder = self._lease_blocks_start()
            if holder:
                return False, f"placers are running on '{holder}' — refused (duplicate protection)"
            started = [pk for pk in PROFILES if self._start_orders_key(pk)]
            return True, f"started {', '.join(started) if started else 'nothing (all running)'}"
        if a == "stop_process":
            name = str(args.get("name") or "")
            if name in self._procs:
                self._stop(name)
                return True, f"stopping {name}"
            return True, f"{name} is not running"
        if a == "start_tracking":
            key = acct.lower()
            if key not in self._trk_btns:
                return False, f"unknown account '{acct}' (use {' / '.join(STORE_BY_KEY)})"
            if f"tracking_{key}" in self._procs:
                return True, "tracking already running"
            self._start_tracking_acct(key)
            return True, f"tracking started ({key})"
        if a == "stop_tracking":
            key = acct.lower()
            names = [f"tracking_{key}"] if key else [n for n in self._procs if n.startswith("tracking_")]
            for n in names:
                if n in self._procs:
                    self._stop(n)
            return True, f"stopping {', '.join(names) or 'nothing'}"
        if a == "run_closing":
            if str(acct).strip().upper() in ("ALL", "BOTH"):
                if any(p.startswith("bulk_") for p in self._procs):
                    return True, "a closing run is already active"
                self._start_bulk("ALL")
                return True, f"closing started ({' + '.join(STORES)}, sequential)"
            if acct_title not in STORES:
                return False, f"account must be {', '.join(STORES)} or ALL"
            if f"bulk_{acct_title.lower()}" in self._procs or "bulk_all" in self._procs:
                return True, f"{acct_title} closing already running"
            self._start_bulk(acct_title)
            return True, f"closing started ({acct_title})"
        if a == "stop_closing":
            for t in ((acct_title,) if acct_title in STORES else STORES):
                self._stop_closing_procs(t)
            return True, "stopping closing/messaging"
        if a == "run_messages":
            if acct_title not in STORES:
                return False, f"account must be {' or '.join(STORES)}"
            self._start_messaging(acct_title)
            return True, f"messaging started ({acct_title})"
        if a == "run_delivery_check":
            if "delivery_all" in self._procs:
                return True, "a delivery check is already running"
            self._start_delivery_check("ALL")
            return True, "delivery check started (Zoro tracking, every store)"
        if a == "stop_delivery_check":
            if "delivery_all" in self._procs:
                self._stop("delivery_all")
                return True, "stopping the delivery check"
            return True, "no delivery check running"
        if a == "reload_config":
            self._load_config()
            return True, "config reloaded"
        if a == "set_headless":
            cfg = _read_cfg_file()
            cfg.setdefault("chrome", {})["headless"] = bool(args.get("on"))
            _write_cfg_file(cfg)
            if hasattr(self, "_headless_var"):
                self._headless_var.set(bool(args.get("on")))      # keep the checkbox honest
            return True, f"headless {'ON' if args.get('on') else 'OFF'} (applies to placers started from now)"
        if a == "shutdown_launcher":
            self.after(800, self._on_close)
            return True, "launcher shutting down (all processes stopped, a closing run keeps going)"
        return False, f"unknown action '{a}'"

    def _lease_blocks_start(self):
        """Cross-machine duplicate protection: another computer holding a fresh
        placement lease in R2 means its placers are live — don't start ours."""
        try:
            if SCRIPT_DIR not in sys.path:
                sys.path.insert(0, SCRIPT_DIR)
            from utils.r2_store import store, machine_id
            s = store()
            if not s.available():
                return None
            holder = s.lease_holder()
            if holder and holder != machine_id():
                return holder
        except Exception:
            return None
        return None

    # ── scheduled closing ─────────────────────────────────────────────────
    def _tick_auto_closing(self):
        """Every 60 s: bot_config.json → automation.auto_closing =
        {"enabled": true, "times": ["03:30"], "accounts": ["HOANK","Hathoro","Wrenchly"],
         "every_hours": 0}. At each listed HH:MM (or every N hours) start the same
        closing chain the ▶ Run Closing button runs — once per account per slot."""
        try:
            ac = (_read_cfg_file().get("automation") or {}).get("auto_closing") or {}
            if ac.get("enabled"):
                if not hasattr(self, "_auto_closing_last"):
                    self._auto_closing_last = {}      # account -> slot key last started
                now = datetime.now()
                accounts = [x for x in (ac.get("accounts") or ["HOANK", "Hathoro"]) if x in STORES]
                slot = None
                hhmm = now.strftime("%H:%M")
                if hhmm in [str(t).strip() for t in (ac.get("times") or [])]:
                    slot = f"{now.strftime('%Y-%m-%d')} {hhmm}"
                every = float(ac.get("every_hours") or 0)
                if slot is None and every > 0:
                    if time.time() - float(getattr(self, "_auto_closing_epoch", 0) or 0) >= every * 3600:
                        slot = now.strftime("%Y-%m-%d %H:%M")
                        self._auto_closing_epoch = time.time()
                if slot and "bulk_all" not in self._procs:
                    due = [acct for acct in accounts
                           if self._auto_closing_last.get(acct) != slot and f"bulk_{acct.lower()}" not in self._procs]
                    for acct in due:
                        self._auto_closing_last[acct] = slot
                    if len(due) >= 2:
                        # One chain for the due stores, sequential: the Gmail fetch/sync runs once and
                        # the accounts no longer contend for the DB (26 Sep 2026). Only the stores in
                        # automation.auto_closing.accounts, in STORES order.
                        due = [s for s in STORES if s in due]
                        self._log_line(f"⏰ auto-closing: starting {' + '.join(due)} sequentially ({slot})", source="scheduler")
                        self._start_bulk(due)
                    elif due:
                        self._log_line(f"⏰ auto-closing: starting {due[0]} ({slot})", source="scheduler")
                        self._start_bulk(due[0])
        except Exception:
            pass
        finally:
            self._auto_closing_id = self.after(60000, self._tick_auto_closing)

    def _tick_autosync(self):
        """Every 2 HOURS while the launcher is open: refresh gmail_archive → unified
        (continuous refund/return capture), sync the placer log, and pre-fill
        closing_orders from placer. Runs as an off-thread subprocess (single-flight)
        so it never blocks the Tk loop or collides with itself."""
        try:
            if not getattr(self, "_autosync_running", False):
                self._autosync_running = True
                import threading, subprocess

                def _run():
                    try:
                        logf = open(os.path.join(SCRIPT_DIR, ".autosync_output.log"), "a",
                                    encoding="utf-8", errors="replace")
                        env = os.environ.copy()
                        env["PYTHONPATH"] = SCRIPT_DIR + os.pathsep + env.get("PYTHONPATH", "")
                        env["PYTHONUNBUFFERED"] = "1"
                        env["PYTHONUTF8"] = "1"; env["PYTHONIOENCODING"] = "utf-8"  # Windows: emoji-safe
                        subprocess.run([RUNNER_PYTHON, "scripts/auto_sync_10min.py"],
                                       cwd=SCRIPT_DIR, env=env, stdout=logf,
                                       stderr=subprocess.STDOUT, timeout=1200)
                    except Exception:
                        pass
                    finally:
                        self._autosync_running = False

                threading.Thread(target=_run, daemon=True).start()
        except Exception:
            pass
        finally:
            self._autosync_id = self.after(3600000, self._tick_autosync)   # 1 hour (build_gmail_archive --days 2 --force, max_total=0 → gap-proof returns/refunds)

    def _tick_fast_sync(self):
        """Every ~60s while the launcher is open: lightweight placer-log →
        order_placer_orders incremental sync, so orders you JUST placed appear in
        the dashboard (v_orders_all) within a minute — without running the heavy
        10-min Gmail refresh. Off-thread, single-flight; safe alongside a running
        closing job (WAL + busy_timeout)."""
        try:
            if not getattr(self, "_fast_sync_running", False):
                self._fast_sync_running = True
                import threading

                def _run():
                    try:
                        if SCRIPT_DIR not in sys.path:
                            sys.path.insert(0, SCRIPT_DIR)
                        from scripts.sync_order_placer_logs import sync_incremental
                        sync_incremental(verbose=False)
                    except Exception:
                        pass
                    finally:
                        self._fast_sync_running = False

                threading.Thread(target=_run, daemon=True).start()
        except Exception:
            pass
        finally:
            self._fast_sync_id = self.after(60000, self._tick_fast_sync)   # 60 seconds

    def _tick_watch(self):
        """Every hour: run the Zoro restock / handling-time watcher (scripts/zoro_watch_check.py)
        as an off-thread subprocess. Uses the dedicated UA stealth scraper (port 9225) so Zoro
        doesn't captcha. Single-flight; logs to .zoro_watch.log."""
        try:
            if not getattr(self, "_watch_running", False):
                self._watch_running = True
                import threading, subprocess

                def _run():
                    try:
                        env = os.environ.copy()
                        env["PYTHONPATH"] = SCRIPT_DIR + os.pathsep + env.get("PYTHONPATH", "")
                        env["PYTHONUNBUFFERED"] = "1"
                        env["PYTHONUTF8"] = "1"; env["PYTHONIOENCODING"] = "utf-8"  # Windows: emoji-safe
                        logf = open(os.path.join(SCRIPT_DIR, ".zoro_watch.log"), "a",
                                    encoding="utf-8", errors="replace")
                        subprocess.run([RUNNER_PYTHON, "scripts/zoro_watch_check.py"],
                                       cwd=SCRIPT_DIR, env=env, stdout=logf,
                                       stderr=subprocess.STDOUT, timeout=1800)
                    except Exception:
                        pass
                    finally:
                        self._watch_running = False

                threading.Thread(target=_run, daemon=True).start()
        except Exception:
            pass
        finally:
            self._watch_id = self.after(3600000, self._tick_watch)   # 1 hour

    # ── HEADER BAR ────────────────────────────────────────────────────
    def _build_header(self):
        hdr = tk.Frame(self, bg=self.CARD, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        hi = tk.Frame(hdr, bg=self.CARD)
        hi.pack(fill="both", expand=True, padx=20)

        bl = tk.Frame(hi, bg=self.CARD)
        bl.pack(side="left", pady=8)
        tk.Label(bl, text="◆", font=(self.FONT, 16), bg=self.CARD, fg=self.ACCENT).pack(side="left")
        tk.Label(bl, text=" ZUHRA", font=(self.FONT, 13, "bold"),
                 bg=self.CARD, fg=self.TEXT).pack(side="left")
        tk.Label(bl, text="  Software", font=(self.MONO, 10),
                 bg=self.CARD, fg=self.DIM).pack(side="left", pady=(2, 0))

        self._btn(bl, "Save", self.GREEN, self._save_config, small=True).pack(side="left", padx=(16, 4))
        self._btn(bl, "Reload", self.CYAN, self._load_config, small=True).pack(side="left", padx=(0, 4))

        self.v_tg_enabled = tk.BooleanVar(value=True)
        tg_cb = ttk.Checkbutton(bl, text="📱 Telegram Alerts", variable=self.v_tg_enabled, command=self._save_config)
        tg_cb.pack(side="left", padx=(14, 4))
        self._tip(tg_cb, "TELEGRAM MOBILE ALERTS (@kamrandsbot):\n"
                         "• All Placed Orders: Amazon SC page screenshot + Copy for Boss\n"
                         "• Low Margin (< 8%): Flagged with pre-tax warning & Copy for Boss\n"
                         "• Mega Profit (> 50%): High-profit celebration insight\n"
                         "• Low-profit skips & Out-of-Stock (OOS) alerts with full item title\n"
                         "Toggle ON on days you want alerts on mobile, OFF when you don't.")

        self.v_wa_enabled = tk.BooleanVar(value=True)
        wa_cb = ttk.Checkbutton(bl, text="💬 WhatsApp Alerts", variable=self.v_wa_enabled, command=self._save_config)
        wa_cb.pack(side="left", padx=(8, 4))
        self._tip(wa_cb, "WHATSAPP MOBILE ALERTS (Self-Hosted Gateway):\n"
                         "• All Placed Orders: Amazon SC page screenshot + pure monospaced note\n"
                         "• Out-of-Stock (OOS) & Low-profit skip alerts\n"
                         "Runs locally via Baileys gateway (http://127.0.0.1:3005).\n"
                         "Toggle ON on days you want alerts on WhatsApp, OFF when you don't.")
        wa_senders = ttk.Button(bl, text="📲 Senders", command=self._open_whatsapp_senders)
        wa_senders.pack(side="left", padx=(0, 4))
        self._tip(wa_senders, "WHATSAPP SENDERS:\n"
                              "Link Kamran's and Hifza's WhatsApp and choose which number\n"
                              "sends each store's alerts.")
        try:
            from gui.wa_inbox import WhatsAppInbox
            self._wa_inbox = WhatsAppInbox(self, bl)
        except Exception as e:
            print(f"  [WhatsApp inbox] not started: {e}")
        # 🛑 Watch list sits at the right end of the bar under the header (_build_toast): the header has no
        # room left at the window's 1200 px minimum width (11 Nov 2026: 1148 px used; a button here → 1267).

        rf = tk.Frame(hi, bg=self.CARD)
        rf.pack(side="right", pady=8)

        self._clock = tk.Label(rf, font=(self.MONO, 9), bg=self.CARD, fg=self.MUTED)
        self._clock.pack(side="right", padx=(16, 0))

        self._hdr_profit = tk.Label(rf, text="$0.00", font=(self.MONO, 14, "bold"),
                                    bg=self.CARD, fg=self.AMBER)
        self._hdr_profit.pack(side="right", padx=(16, 0))
        tk.Label(rf, text="PROFIT", font=(self.FONT, 8), bg=self.CARD, fg=self.DIM).pack(side="right")

        self._hdr_placed = tk.Label(rf, text="0", font=(self.MONO, 14, "bold"),
                                    bg=self.CARD, fg=self.GREEN)
        self._hdr_placed.pack(side="right", padx=(16, 0))
        tk.Label(rf, text="PLACED", font=(self.FONT, 8), bg=self.CARD, fg=self.DIM).pack(side="right")

        self._hdr_pill = tk.Frame(rf, bg=self.MUTED, padx=8, pady=2)
        self._hdr_pill.pack(side="right", padx=(0, 16))
        self._hdr_status = tk.Label(self._hdr_pill, text="IDLE", font=(self.FONT, 8, "bold"),
                                    bg=self.MUTED, fg=self.BG)
        self._hdr_status.pack()

    # ── TOAST ─────────────────────────────────────────────────────────
    def _build_toast(self):
        self._toast_frame = tk.Frame(self, bg="#1a1040", height=28)
        self._toast_frame.pack(fill="x")
        self._toast_frame.pack_propagate(False)
        self._toast_lbl = tk.Label(self._toast_frame, text="", font=(self.FONT, 10, "bold"),
                                   bg="#1a1040", fg="#f5c542")
        self._toast_lbl.pack(expand=True)
        # 🛑 Watch list (the listing guard's list): placed at the bar's right end, so the message stays centred
        # and the header keeps its width.
        watch = tk.Label(self._toast_frame, text="🛑 Watch list", font=(self.FONT, 9, "bold"), bg="#2a1f5c",
                         fg=self.TEXT, padx=10, pady=1, cursor="hand2")
        watch.place(relx=1.0, rely=0.5, anchor="e", x=-12)
        # on release: the press stays with the tooltip's <ButtonPress> (it hides the tip); one binding per event
        watch.bind("<ButtonRelease-1>", lambda _e: self._open_watch_list(), add="+")
        watch.bind("<Enter>", lambda _e: watch.config(bg="#3b2d7a"), add="+")
        watch.bind("<Leave>", lambda _e: watch.config(bg="#2a1f5c"), add="+")
        self._tip(watch, "WATCH LIST:\n"
                         "The listings the placer found out of stock at Zoro (main code and\n"
                         f"every alternative). It checks Zoro again every {_watch_list_every()} minutes while\n"
                         "that store's placer is running, and puts the listing back when Zoro\n"
                         "has it. Click to see which ASINs are on it.")
        self._toast_msgs = [
            "Hello Mama! 💖", "Hello Papa! 💪",
            "Work hard Papa to get Mama! 🔥",
            "Mama be decent, Mama you are so good! ✨",
            "Papa grinding mode ON 🚀", "Mama says: ship faster! 📦",
            "Papa the hustle never stops 💰", "Mama proud of you today! 🌟",
            "Papa stop staring at the screen, ship orders! 👀",
            "Mama energy = unlimited ☕",
            "Papa: 1 more order then sleep... (lies) 😂",
            "Mama vibes only 🦋", "Papa legend in the making 🏆",
            "Mama says hydrate! 💧", "Papa profits go brrrrr 📈",
            "Mama monitoring from above 👁️", "Papa you got this! 💎",
            "Mama approves this grind ✅", "ZUHRA says: ship it! 🎯",
            "Papa vs Sleep — Papa wins 🥊",
        ]

    # ── SIDEBAR ───────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        tk.Frame(parent, bg=self.PANEL, height=16).pack()

        nav_data = [
            ("dashboard",      "📊", "Dashboard"),
            ("orders",         "📦", "Orders"),
            ("closing",        "💰", "Closing"),
            ("listings",       "🧾", "Listings"),
            ("private_label",  "🏭", "Private Label"),
            ("repricer",       "🏷️", "Repricer"),
            ("walmart",        "🛒", "Walmart"),
            ("tracking",       "🚚", "Tracking"),
            ("coupons",        "🎫", "Coupons"),
            ("post_delivery",  "💬", "Post-Delivery"),
            ("price_log",      "📈", "Price Log"),
            ("logs",           "📋", "Logs"),
            ("loss_orders",    "🛑", "Skipped Orders"),
            ("unmapped",       "🏷️", "Unmapped ASINs"),
            ("needs_priority", "⭐", "Needs Priority"),
            ("locked",         "🔓", "Locked Orders"),
            ("settings",       "⚙️", "Settings"),
        ]
        for key, icon, label in nav_data:
            self._make_nav_item(parent, key, icon, label)

        tk.Frame(parent, bg=self.PANEL, height=20).pack()
        self._sidebar_running = tk.Label(parent, text="", font=(self.MONO, 9),
                                         bg=self.PANEL, fg=self.GREEN)
        self._sidebar_running.pack()

        tk.Frame(parent, bg=self.PANEL).pack(fill="both", expand=True)

        sf = tk.Frame(parent, bg=self.PANEL)
        sf.pack(fill="x", padx=14, pady=(0, 14))
        self._btn(sf, "⏹  Stop All", self.RED, self._stop_all).pack(fill="x")

    def _make_nav_item(self, parent, key, icon, label):
        item = tk.Frame(parent, bg=self.PANEL, cursor="hand2")
        item.pack(fill="x", pady=1)

        accent = tk.Frame(item, bg=self.PANEL, width=3)
        accent.pack(side="left", fill="y")

        lbl = tk.Label(item, text=f"  {icon}  {label}", font=(self.FONT, 10),
                       bg=self.PANEL, fg=self.DIM, anchor="w", pady=11)
        lbl.pack(side="left", fill="x", expand=True)

        def on_click(e):
            self._switch_page(key)

        def on_enter(e):
            if self._current_page != key:
                for w in (item, lbl):
                    w.configure(bg=self.NAV_HOVER)
                lbl.configure(fg=self.TEXT)

        def on_leave(e):
            if self._current_page != key:
                for w in (item, lbl):
                    w.configure(bg=self.PANEL)
                lbl.configure(fg=self.DIM)

        for w in (item, lbl, accent):
            w.bind("<Button-1>", on_click)
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)

        self._nav_items[key] = {"frame": item, "accent": accent, "label": lbl}

    def _switch_page(self, name):
        if name == self._current_page:
            return
        self._pages[self._current_page].pack_forget()
        self._pages[name].pack(fill="both", expand=True)

        # Hide the console on the full-height pages; restore it on the others
        if name in NO_CONSOLE_PAGES:
            self._main_pw.forget(self._console_frame)
        elif self._current_page in NO_CONSOLE_PAGES:
            self._main_pw.add(self._console_frame, minsize=150, stretch="never")
        if name == "logs":
            self.after(100, self._refresh_logs_view)
        elif name == "loss_orders":
            self.after(100, self._refresh_loss_orders_table)

        # Refresh the attention panels the moment they're opened.
        if name == "needs_priority":
            self.after(100, self._refresh_needs_priority)
        elif name == "unmapped":
            self.after(100, self._refresh_unmapped)
        elif name == "locked":
            self.after(100, self._refresh_locked_orders)
        elif name == "repricer":
            self.after(100, self._refresh_repricer_table)

        self._current_page = name
        self._highlight_nav(name)

    def _highlight_nav(self, active):
        for key, nav in self._nav_items.items():
            if key == active:
                nav["frame"].configure(bg=self.NAV_ACTIVE)
                nav["accent"].configure(bg=self.ACCENT)
                nav["label"].configure(bg=self.NAV_ACTIVE, fg=self.TEXT)
            else:
                nav["frame"].configure(bg=self.PANEL)
                nav["accent"].configure(bg=self.PANEL)
                nav["label"].configure(bg=self.PANEL, fg=self.DIM)

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Dashboard
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_dashboard(self, container):
        page = tk.Frame(container, bg=self.BG)

        # Top row: Stats + Chrome+Unmapped
        top = tk.Frame(page, bg=self.BG)
        top.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        top.columnconfigure(0, weight=3)
        top.columnconfigure(1, weight=2)
        top.rowconfigure(0, weight=1)

        # ── Left: Live Stats ──────────────────────────────────────────
        so, si = self._panel(top)
        so.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self._heading(si, "LIVE STATS", self.AMBER)

        pf = tk.Frame(si, bg=self.CARD)
        pf.pack(fill="x", padx=14, pady=(4, 8))
        self._big_profit = tk.Label(pf, text="$0.00", font=(self.MONO, 28, "bold"),
                                    bg=self.CARD, fg=self.AMBER)
        self._big_profit.pack(anchor="w")
        self._dim(pf, "Session Profit").pack(anchor="w")

        self._sep(si)

        sg = tk.Frame(si, bg=self.CARD)
        sg.pack(fill="x", padx=14, pady=(0, 14))
        sg.columnconfigure(0, weight=1)
        sg.columnconfigure(1, weight=1)
        sg.columnconfigure(2, weight=1)
        sg.columnconfigure(3, weight=1)

        self._dash_labels = {}
        stats = [
            ("pending",    "Pending",    self.TEXT,  0, 0),
            ("processed",  "Processed",  self.TEXT,  0, 1),
            ("success",    "Placed",     self.GREEN, 0, 2),
            ("failed",     "Failed",     self.RED,   0, 3),
            ("low_profit", "Low Profit", self.AMBER, 1, 0),
            ("duplicates", "Duplicates", self.CYAN,  1, 1),
            ("oos",        "OOS",        self.DIM,   1, 2),
            ("last_wb",    "Last WB",    self.GREEN, 1, 3),
        ]
        for key, label, color, r, c in stats:
            cell = tk.Frame(sg, bg=self.CARD)
            cell.grid(row=r, column=c, sticky="ew", pady=6, padx=(0, 12))
            tk.Label(cell, text=label, font=(self.FONT, 9), bg=self.CARD,
                     fg=self.DIM, anchor="w").pack(anchor="w")
            vl = tk.Label(cell, text="—", font=(self.MONO, 13, "bold"),
                          bg=self.CARD, fg=color)
            vl.pack(anchor="w")
            self._dash_labels[key] = vl

        # ── Right: Chrome + Unmapped ──────────────────────────────────
        right_col = tk.Frame(top, bg=self.BG)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        right_col.rowconfigure(1, weight=1)

        po, pi = self._panel(right_col)
        po.pack(fill="x", pady=(0, 4))
        h = self._heading(pi, "CHROME", self.PURPLE)
        self._btn(h, "↻", self.CYAN, self._check_ports, small=True).pack(side="right")

        wb_row = tk.Frame(pi, bg=self.CARD)
        wb_row.pack(fill="x", padx=14, pady=(0, 4))
        self._btn(wb_row, "🛒 Walmart Zoro", self.CYAN,
                  self._launch_chrome_walmart, small=True).pack(side="left", padx=(0, 6))
        self._btn(wb_row, "🛒 Walmart SC", self.PURPLE,
                  self._launch_chrome_walmart_sc, small=True).pack(side="left")

        pf2 = tk.Frame(pi, bg=self.CARD)
        pf2.pack(fill="x", padx=14, pady=(0, 10))
        self._port_lbl = {}
        for pk in list(PROFILES) + STORE_PORT_KEYS:
            r = tk.Frame(pf2, bg=self.CARD)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=f"{pk} :{PORTS[pk]}", font=(self.MONO, 9),
                     bg=self.CARD, fg=self.DIM, width=22, anchor="w").pack(side="left")
            l = tk.Label(r, text="?", font=(self.MONO, 9, "bold"), bg=self.CARD, fg=self.MUTED)
            l.pack(side="left")
            self._port_lbl[pk] = l

        uo, ui = self._panel(right_col)
        uo.pack(fill="both", expand=True)
        uh = self._heading(ui, "UNMAPPED ASINs", self.RED)
        self._btn(uh, "↻", self.CYAN, self._refresh_unmapped, small=True).pack(side="right")
        self._unmapped_lbl = tk.Label(uh, text="", font=(self.MONO, 9), bg=self.CARD, fg=self.DIM)
        self._unmapped_lbl.pack(side="right", padx=(0, 8))
        self._unmapped_set = set()
        self._unmapped_orders = {}      # tree item id → amazon_order_id (for SC open)
        utw = tk.Frame(ui, bg=self.CARD)
        utw.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self._unmapped_tree = ttk.Treeview(utw, show="tree", selectmode="browse", height=8)
        _uvsb = ttk.Scrollbar(utw, orient="vertical", command=self._unmapped_tree.yview)
        self._unmapped_tree.configure(yscrollcommand=_uvsb.set)
        self._unmapped_tree.pack(side="left", fill="both", expand=True)
        _uvsb.pack(side="right", fill="y")
        self._unmapped_tree.bind("<Double-1>", self._on_unmapped_dblclick)
        tk.Label(ui, text="double-click an ASIN → Amazon · an order → Seller Central (to label it)",
                 font=(self.MONO, 8), bg=self.CARD, fg=self.MUTED).pack(anchor="w", padx=10, pady=(0, 6))

        # ── Middle: CURRENT RUN — one live card per profile/process ──────
        # The stat box the placer prints ("Pending/Processed/Low Profit |
        # Success/Failed") pinned and auto-updating, so nobody has to scroll
        # the logs to find the latest DASHBOARD block.
        cro, cri = self._panel(page)
        cro.pack(fill="x", padx=8, pady=(0, 4))
        self._heading(cri, "CURRENT RUN — PER PROFILE", self.PURPLE)
        self._pcards_frame = tk.Frame(cri, bg=self.CARD)
        self._pcards_frame.pack(fill="x", padx=14, pady=(2, 10))
        for c in range(4):
            self._pcards_frame.columnconfigure(c, weight=1)
        self._pcards = {}

        # ── Bottom: Activity Feed ─────────────────────────────────────
        ao, ai = self._panel(page)
        ao.pack(fill="x", padx=8, pady=(0, 8))
        ah = self._heading(ai, "ACTIVITY", self.CYAN)
        self._btn(ah, "Logs", self.DIM, lambda: self._switch_page("logs"), small=True).pack(side="right", padx=(0, 8))

        self._feed = scrolledtext.ScrolledText(
            ai, wrap="word", font=(self.MONO, 9),
            bg=self.INP_BG, fg=self.TEXT,
            relief="flat", bd=0, padx=6, pady=6,
            state="disabled", height=6)
        self._feed.pack(fill="x", padx=10, pady=(0, 8))
        self._feed.tag_configure("ok", foreground=self.GREEN)
        self._feed.tag_configure("err", foreground=self.RED)
        self._feed.tag_configure("warn", foreground=self.AMBER)
        self._feed.tag_configure("info", foreground=self.CYAN)

        return page

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Orders — controls top, 2×2 profile cards below
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_orders(self, container):
        page = tk.Frame(container, bg=self.BG)

        # ── Controls bar ──────────────────────────────────────────────
        co, ci = self._panel(page)
        co.pack(fill="x", padx=8, pady=(8, 2))
        
        # Single row: Profile selector + buttons + multi checkboxes
        r1 = tk.Frame(ci, bg=self.CARD)
        r1.pack(fill="x", padx=14, pady=(8, 4))
        self._dim(r1, "Profile").pack(side="left")
        self.ord_profile = self._combo(r1, PROFILE_LABELS, 18)
        self.ord_profile.set(PROFILE_CHOICES[PROFILES[0]])
        self.ord_profile.pack(side="left", padx=(6, 12))
        self._btn(r1, "▶ Start", self.GREEN, self._start_orders).pack(side="left", padx=(0, 4))
        self._btn(r1, "⚡ Multi", self.AMBER, self._start_multi).pack(side="left", padx=(0, 4))
        self.btn_ord_stop = self._btn(r1, "■ Stop All", self.RED, self._stop_all_orders)
        self.btn_ord_stop.pack(side="left")
        self.btn_ord_stop.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)

        # Headless toggle — placers AND trackers launched from now on run without a window.
        # Measured 17 Sep 2026 on one identical tracking cycle: headed 1660 MB vs headless
        # 889 MB of system memory. On a 16 GB Mac running 4 placers + 2 trackers that is the
        # difference between swapping and not.
        tk.Frame(r1, bg=self.MUTED, width=1).pack(side="left", fill="y", padx=(14, 10), pady=4)
        self._headless_var = tk.BooleanVar(
            value=bool((_read_cfg_file().get("chrome") or {}).get("headless")))
        self._headless_cb = ttk.Checkbutton(r1, text="🫥 Headless", variable=self._headless_var,
                                            command=self._toggle_headless)
        self._headless_cb.pack(side="left", padx=(0, 4))
        self._tip(self._headless_cb,
                  "HEADLESS MODE — Chrome runs with no window.\n"
                  "Roughly half the memory per browser (measured: 1660 MB → 889 MB).\n"
                  "Applies to placers and trackers STARTED FROM NOW; already-running\n"
                  "processes keep their current mode until you restart them.")

        self._pagechk_var = tk.BooleanVar(
            value=bool((_read_cfg_file().get("checkout") or {}).get("page_checkout")))
        self._pagechk_cb = ttk.Checkbutton(r1, text="🧾 Attributed checkout", variable=self._pagechk_var,
                                           command=self._toggle_page_checkout)
        self._pagechk_cb.pack(side="left", padx=(0, 4))
        self._tip(self._pagechk_cb,
                  "ATTRIBUTED CHECKOUT — place the final order on Zoro's real /checkout page\n"
                  "instead of posting it to their order API.\n\n"
                  "Zoro's marketing analytics (Google Tag Manager / GA4) fires on PAGE events.\n"
                  "API-placed orders render no page, so their system records the order with no\n"
                  "channel attached — the 'unattributed web order activity' their reseller team\n"
                  "asked about. Finishing on the page lets their own tags record the sale.\n\n"
                  "Cart, coupons, address and shipping still go through the API, so the coupon\n"
                  "logic is unchanged. Costs about 27 s more per order (measured: 21 s → 48 s).\n"
                  "Applies to placers STARTED FROM NOW.")

        # Multi checkboxes inline
        tk.Frame(r1, bg=self.MUTED, width=1).pack(side="left", fill="y", padx=(14, 10), pady=4)
        self._dim(r1, "Multi:").pack(side="left", padx=(0, 4))
        self._multi_vars = {}
        for pk in PROFILES:
            var = tk.BooleanVar()
            ttk.Checkbutton(r1, text=f"{pk.split('_')[0]}·{pk.split('_')[1][:1]}", variable=var).pack(side="left", padx=(0, 6))
            self._multi_vars[pk] = var

        # Profile status row (compact)
        r3 = tk.Frame(ci, bg=self.CARD)
        r3.pack(fill="x", padx=14, pady=(0, 6))
        self._pstat = {}
        self._pstop = {}
        for pk in PROFILES:
            pf = tk.Frame(r3, bg=self.CARD)
            pf.pack(side="left", padx=(0, 14))
            tk.Label(pf, text=f"{ICONS[pk]} {pk}", font=(self.MONO, 8),
                     bg=self.CARD, fg=self.DIM).pack(side="left")
            lbl = tk.Label(pf, text=" · ", font=(self.MONO, 8),
                           bg=self.CARD, fg=self.MUTED)
            lbl.pack(side="left")
            stop_btn = self._btn(pf, "■", self.RED,
                                 lambda k=pk: self._stop(f"orders_{k}"), small=True)
            stop_btn.pack(side="left", padx=(2, 0))
            stop_btn.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
            self._pstat[pk] = lbl
            self._pstop[pk] = stop_btn

        # ── Sunday mode: hold what Monday's priority coupons could improve ──
        r4 = tk.Frame(ci, bg=self.CARD)
        r4.pack(fill="x", padx=14, pady=(0, 8))
        _sun = _read_cfg_file().get("sunday_mode") or {}

        def _g(v, d):
            try:
                return f"{float(v):g}"
            except (TypeError, ValueError):
                return d
        self._sun_var = tk.BooleanVar(value=bool(_sun.get("enabled")))
        self._sun_cart = tk.StringVar(value=_g(_sun.get("max_cart"), "50"))
        self._sun_margin = tk.StringVar(value=_g(_sun.get("min_margin_pct"), "20"))
        _sun_cb = ttk.Checkbutton(r4, text="🗓️ Sunday mode", variable=self._sun_var,
                                  command=self._save_sunday_mode)
        _sun_cb.pack(side="left")
        self._tip(_sun_cb,
                  "SUNDAY MODE — the weekly priority coupons arrive on Monday (~10:00 PKT).\n"
                  "Until then, place only what those coupons could not have improved:\n\n"
                  "  • RESELLER placers place an order only when its Zoro COST — the cart after\n"
                  "    any Zoro Select discount PLUS shipping — is UNDER the amount here AND its\n"
                  "    estimated margin is AT LEAST the % here.\n"
                  "    e.g. $34.99 + $5.99 shipping = $40.98 → under $50; $46 + $5.99 = $51.99 → waits.\n"
                  "  • COUPON placers use priority coupons only — never a regular coupon, since\n"
                  "    an item bought with one today may take a priority code tomorrow. They hand\n"
                  "    an order to the reseller only if it would pass the rule above, and never\n"
                  "    mark an order '20' just because no priority coupon applied today.\n"
                  "  • Items Zoro marks 'not eligible for any other offers or promotions' are\n"
                  "    placed normally — no coupon can ever apply, so waiting gains nothing.\n"
                  "  • Wrenchly ignores Sunday mode: it has no coupons to wait for.\n"
                  "  • Everything else WAITS, unlabelled. Nothing is written to Amazon, so\n"
                  "    unticking this releases every held order at once.\n\n"
                  "Held orders are not re-checked every cycle (no re-scrape, no re-pricing) until\n"
                  "the numbers change, the priority coupons change, or 6 hours pass.\n"
                  "A Manual-C / Manual-R tag still goes straight through.\n\n"
                  "Running placers pick this up within 15 seconds — no restart needed.")
        self._dim(r4, "  Reseller places only: Zoro cost incl. shipping under $").pack(side="left", padx=(8, 2))
        _e_cart = tk.Entry(r4, textvariable=self._sun_cart, width=6, font=(self.MONO, 10),
                           bg=self.INP_BG, fg=self.TEXT, insertbackground=self.TEXT,
                           relief="flat", highlightthickness=1, highlightbackground=self.BORDER)
        _e_cart.pack(side="left")
        self._dim(r4, "  and est. margin ≥").pack(side="left", padx=(6, 2))
        _e_mgn = tk.Entry(r4, textvariable=self._sun_margin, width=4, font=(self.MONO, 10),
                          bg=self.INP_BG, fg=self.TEXT, insertbackground=self.TEXT,
                          relief="flat", highlightthickness=1, highlightbackground=self.BORDER)
        _e_mgn.pack(side="left")
        self._dim(r4, "%").pack(side="left", padx=(2, 12))
        for _e in (_e_cart, _e_mgn):
            _e.bind("<Return>", lambda _ev: self._save_sunday_mode())
            _e.bind("<FocusOut>", lambda _ev: self._save_sunday_mode(quiet=True))
        self._sun_lbl = tk.Label(r4, text="", font=(self.MONO, 9), bg=self.CARD, fg=self.MUTED)
        self._sun_lbl.pack(side="left")
        self._render_sunday_label()

        # ── 2×2 Profile cards ────────────────────────────────────────
        cards = tk.Frame(page, bg=self.BG)
        cards.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)
        cards.rowconfigure(0, weight=1)
        cards.rowconfigure(1, weight=1)
        cards.rowconfigure(2, weight=1)

        self._plbl = {}
        self._pmargin = {}
        self._plbl2 = {}
        self._pmargin2 = {}
        self._plimit = {}
        self._pskip_priority = {}
        self._pest_margin_only = {}
        self._plabels_only = {}
        self._prush = {}
        self._pultra = {}
        self._prush_batch = {}
        self._pfocus_priority_enabled = {}
        self._pfocus_priority_code = {}

        positions = [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]
        for (row, col), pk in zip(positions, PROFILES):
            card_outer = tk.Frame(cards, bg=self.BORDER)
            card_outer.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
            card = tk.Frame(card_outer, bg=self.CARD)
            card.pack(fill="both", expand=True, padx=1, pady=1)
            card.columnconfigure(0, weight=1)

            # ── Row 0: Header ─────────────────────────────────────
            ch = tk.Frame(card, bg=self.CARD)
            ch.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
            tk.Label(ch, text=f"{ICONS[pk]} {pk}", font=(self.FONT, 11, "bold"),
                     bg=self.CARD, fg=self.ACCENT).pack(side="left")
            # Est. Margin Only checkbox — when ON, only the pre-checkout
            # estimated margin is checked (checkout total includes tax
            # which gets refunded, so it shouldn't block orders)
            em_var = tk.BooleanVar()
            em_cb = ttk.Checkbutton(ch, text="Est Margin Only", variable=em_var)
            em_cb.pack(side="right", padx=(8, 0))
            self._tip(em_cb, "Gate orders on the pre-checkout ESTIMATED margin only.\n"
                             "The final checkout total includes sales tax (which gets "
                             "refunded), so the final-margin gate can wrongly block "
                             "orders — turn this on to skip it.")
            self._pest_margin_only[pk] = em_var
            lo_var = tk.BooleanVar()
            lo_cb = ttk.Checkbutton(ch, text="Labels Only", variable=lo_var)
            lo_cb.pack(side="right", padx=(8, 0))
            self._tip(lo_cb, "Process ONLY orders already labelled with this profile's "
                             "label(s).\nSkips Priority-ASIN discovery AND Deciding-ASIN "
                             "routing — nothing is pulled in from the "
                             "Priority_ASIN_Control sheet while this is on.")
            self._plabels_only[pk] = lo_var
            rb_combo = self._combo(ch, ["5", "10", "15", "20", "25", "30", "40", "50", "60", "80", "100"], w=4)
            rb_combo.set("10")
            rb_combo.pack(side="right", padx=(4, 0))
            self._tip(rb_combo, "Rush batch size — how many placements before the "
                                "batched Amazon write-back flush.")
            self._prush_batch[pk] = rb_combo
            rush_var = tk.BooleanVar()
            rush_cb = ttk.Checkbutton(ch, text="⚡Rush", variable=rush_var)
            rush_cb.pack(side="right", padx=(8, 0))
            self._tip(rush_cb, "RUSH MODE: place orders back-to-back; the Amazon "
                               "write-backs (WB label + seller note) are journaled and "
                               "flushed in batches of the size on the right.\n"
                               "Duplicate checks, margin gates and the local re-buy "
                               "lock all stay per-order. Crash-safe: pending "
                               "write-backs replay automatically on the next run.")
            self._prush[pk] = rush_var
            ultra_var = tk.BooleanVar()
            ultra_cb = ttk.Checkbutton(ch, text="🚀Ultra", variable=ultra_var)
            ultra_cb.pack(side="right", padx=(8, 0))
            self._tip(ultra_cb, "ULTRA MODE: Rush, but placing never stops for Amazon. Two extra "
                                "Chrome windows work beside it: one reads the next orders' pages, "
                                "one writes the WB labels and seller notes back.\n"
                                "Every check stays on the placing side, one order at a time. If a "
                                "window fails, the placer falls back to Rush by itself.\n"
                                "Out-of-stock listings are held, not marked OOS: Zoro is asked again "
                                "every cycle and their orders go through when it is back.\n"
                                "Takes effect when the placer next starts.")
            self._pultra[pk] = ultra_var
            if "COUPON" in pk:
                spv = tk.BooleanVar()
                # Named "…Coupons" deliberately: plain "Skip Priority" reads as
                # "skip priority ASINs" and has been misread that way. It has
                # nothing to do with which orders get processed.
                sp_cb = ttk.Checkbutton(ch, text="Skip Prio Coupons", variable=spv)
                sp_cb.pack(side="right")
                self._tip(sp_cb, "Skip PRIORITY COUPONS (the reusable %-off / $-off codes "
                                 "from column B of the Coupons sheet).\nCheckout then uses "
                                 "only the regular single-use signup coupons.\nDoes NOT "
                                 "change which orders are processed.")
                self._pskip_priority[pk] = spv

            # ── Row 1: Fields in a proper grid ────────────────────
            fg = tk.Frame(card, bg=self.CARD)
            fg.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))
            fg.columnconfigure(1, weight=1)  # label entry stretches
            fg.columnconfigure(3, weight=0)
            fg.columnconfigure(5, weight=0)

            # Label field
            tk.Label(fg, text="Label", font=(self.FONT, 9),
                     bg=self.CARD, fg=self.DIM).grid(row=0, column=0, sticky="w", padx=(0, 6))
            lbl_var = tk.StringVar()
            self._tip(self._entry(fg, var=lbl_var, w=12),
                      "Amazon 'Your Seller Order ID' label this profile scrapes and "
                      "places (e.g. MR / AB ECOM / RAJAR).")\
                .grid(row=0, column=1, sticky="ew", padx=(0, 16))
            self._plbl[pk] = lbl_var

            # Min% field
            tk.Label(fg, text="Min%", font=(self.FONT, 9),
                     bg=self.CARD, fg=self.DIM).grid(row=0, column=2, sticky="w", padx=(0, 6))
            margin_var = tk.StringVar(value="-20")
            self._tip(self._entry(fg, var=margin_var, w=5),
                      "Minimum profit margin % to place an order.\n"
                      "-20 means orders down to a 20% loss are allowed.")\
                .grid(row=0, column=3, sticky="w", padx=(0, 16))
            self._pmargin[pk] = margin_var

            # Limit field
            tk.Label(fg, text="Limit", font=(self.FONT, 9),
                     bg=self.CARD, fg=self.DIM).grid(row=0, column=4, sticky="w", padx=(0, 6))
            limit_var = tk.StringVar(value="0")
            self._tip(self._entry(fg, var=limit_var, w=5),
                      "Max orders this profile places per cycle. 0 = unlimited.")\
                .grid(row=0, column=5, sticky="w")
            self._plimit[pk] = limit_var

            # ── Row 1b: Payment (HS_RESELLER only) ─────────
            # This Zoro account pays on Net 30 terms, ALWAYS (owner, 8 Nov 2026): Net 30 is the
            # checkout's default and the placer never switches it to the saved card. It used to be
            # a Credit Card / Net 30 choice; the choice fell back to Credit Card and every order
            # from 15 Oct to 7 Nov went on the AMEX ****6723. Fixed in utils.config.NET30_PROFILES.
            if pk == "HS_RESELLER":
                fgp = tk.Frame(card, bg=self.CARD)
                fgp.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))
                tk.Label(fgp, text="Payment", font=(self.FONT, 9),
                         bg=self.CARD, fg=self.DIM).pack(side="left", padx=(0, 6))
                self._tip(tk.Label(fgp, text="Net 30 (always)", font=(self.FONT, 9, "bold"),
                                   bg=self.CARD, fg=self.GREEN),
                          "This Zoro account always pays on Net 30 terms — the checkout's default. "
                          "The placer never picks a card for it.").pack(side="left")

            # ── Row 1b: Secondary Label/Margin (COUPON only) ──────
            if "COUPON" in pk:
                fg2 = tk.Frame(card, bg=self.CARD)
                fg2.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))
                fg2.columnconfigure(1, weight=1)
                fg2.columnconfigure(3, weight=0)

                tk.Label(fg2, text="Label 2", font=(self.FONT, 9),
                         bg=self.CARD, fg=self.DIM).grid(row=0, column=0, sticky="w", padx=(0, 6))
                lbl2_var = tk.StringVar()
                self._entry(fg2, var=lbl2_var, w=12).grid(row=0, column=1, sticky="ew", padx=(0, 16))
                self._plbl2[pk] = lbl2_var

                tk.Label(fg2, text="Min% 2", font=(self.FONT, 9),
                         bg=self.CARD, fg=self.DIM).grid(row=0, column=2, sticky="w", padx=(0, 6))
                margin2_var = tk.StringVar(value="-20")
                self._entry(fg2, var=margin2_var, w=5).grid(row=0, column=3, sticky="w", padx=(0, 16))
                self._pmargin2[pk] = margin2_var

                # ── Row 2: Focus Priority Coupon (COUPON only) ──────
                fg3 = tk.Frame(card, bg=self.CARD)
                fg3.grid(row=3, column=0, sticky="ew", padx=12, pady=(2, 6))

                foc_en_var = tk.BooleanVar(value=False)
                foc_cb = ttk.Checkbutton(fg3, text="🎯 Place with Priority", variable=foc_en_var, command=self._save_config)
                foc_cb.pack(side="left", padx=(0, 6))
                self._tip(foc_cb, "FOCUS PRIORITY COUPON MODE:\n"
                                  "When checked, loads and places ONLY orders whose ASIN has this\n"
                                  "specific priority coupon working (e.g. FACILITY8M26 expiring soon).\n"
                                  "When unchecked, normal order processing runs across all orders.")
                self._pfocus_priority_enabled[pk] = foc_en_var

                foc_code_var = tk.StringVar(value="FACILITY8M26")
                foc_combo = ttk.Combobox(
                    fg3,
                    textvariable=foc_code_var,
                    values=[
                        "FACILITY8M26",
                        "HARDWARE8926",
                        "facility, hardware",
                        "POWER8X726",
                        "CLEAN9U26",
                        "DEWALTG3926",
                        "FACILITY",
                        "HARDWARE",
                        "POWER",
                        "CLEAN",
                        "DEWALT",
                    ],
                    width=14,
                    font=(self.FONT, 9)
                )
                foc_combo.pack(side="left")
                foc_combo.bind("<<ComboboxSelected>>", lambda e: self._save_config())
                foc_combo.bind("<FocusOut>", lambda e: self._save_config())
                foc_combo.bind("<Return>", lambda e: self._save_config())
                self._tip(foc_combo, "Enter or select the priority coupon to focus on (e.g. FACILITY8M26, HARDWARE8926, or shorthand facility, hardware).\n"
                                     "Only orders for ASINs where this coupon works will be loaded and placed.")
                self._pfocus_priority_code[pk] = foc_code_var

            # (Priority ASINs now loaded from Google Sheets — no local UI)

        return page

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Tracking
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_tracking(self, container):
        page = tk.Frame(container, bg=self.BG)

        to, ti = self._panel(page)
        to.pack(fill="x", padx=8, pady=8)
        self._heading(ti, "TRACKING", self.CYAN)

        r1 = tk.Frame(ti, bg=self.CARD)
        r1.pack(fill="x", padx=14, pady=(8, 6))
        self._dim(r1, "Sleep interval (sec)").pack(side="left")
        self.trk_sleep = self._combo(r1, ["30", "60", "70", "90", "120", "180", "300"], 5)
        self.trk_sleep.set("70")
        self.trk_sleep.pack(side="left", padx=(6, 20))
        self.trk_single = tk.BooleanVar()
        ttk.Checkbutton(r1, text="Single cycle", variable=self.trk_single).pack(side="left")

        # Trackers get their OWN headless switch, independent of the placers': tracking only
        # drives Seller Central and was verified working headless (auto-login, report download,
        # upload and buyer messages all fine), while placer checkout has not been proven yet.
        tk.Frame(r1, bg=self.MUTED, width=1).pack(side="left", fill="y", padx=(16, 12), pady=4)
        self._trk_headless_var = tk.BooleanVar(
            value=bool((_read_cfg_file().get("chrome") or {}).get("headless_tracking")))
        self._trk_headless_cb = ttk.Checkbutton(r1, text="🫥 Headless (trackers)",
                                                variable=self._trk_headless_var,
                                                command=self._toggle_tracking_headless)
        self._trk_headless_cb.pack(side="left")
        self._tip(self._trk_headless_cb,
                  "HEADLESS TRACKERS — the two tracking Chromes run with no window.\n"
                  "Separate from the placers' headless switch on the Orders page.\n\n"
                  "Measured on one identical HOANK cycle: 1660 MB headed vs 889 MB headless.\n"
                  "Verified headless: Amazon auto-login, order report, tracking upload and\n"
                  "buyer messages all work.\n\n"
                  "Applies to trackers STARTED FROM NOW.")

        self._sep(ti)

        self._trk_btns = {}
        for acct_key, acct_label in [("hathoro", "Hathoro"), ("hoank", "HOANK"), ("wrenchly", "Wrenchly")]:
            ar = tk.Frame(ti, bg=self.CARD)
            ar.pack(fill="x", padx=14, pady=(4, 6))

            tk.Label(ar, text=acct_label, font=(self.FONT, 11, "bold"),
                     bg=self.CARD, fg=self.TEXT, width=10, anchor="w").pack(side="left")

            go_btn = self._btn(ar, "▶ Start", self.GREEN,
                               lambda k=acct_key: self._start_tracking_acct(k))
            go_btn.pack(side="left", padx=(0, 6))
            stop_btn = self._btn(ar, "■ Stop", self.RED,
                                 lambda k=acct_key: self._stop(f"tracking_{k}"))
            stop_btn.pack(side="left", padx=(0, 12))
            stop_btn.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)

            skip_msg_var = tk.BooleanVar()
            ttk.Checkbutton(ar, text="Skip Messages", variable=skip_msg_var).pack(side="left", padx=(8, 0))

            lbl = tk.Label(ar, text="Idle", font=(self.MONO, 9),
                           bg=self.CARD, fg=self.MUTED)
            lbl.pack(side="right")

            self._trk_btns[acct_key] = {
                "go": go_btn, "stop": stop_btn, "lbl": lbl,
                "skip_msg": skip_msg_var,
            }

        tk.Frame(ti, bg=self.CARD, height=12).pack()
        return page

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Coupons
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_coupons(self, container):
        page = tk.Frame(container, bg=self.BG)

        co, ci = self._panel(page)
        co.pack(fill="x", padx=8, pady=8)
        self._heading(ci, "COUPON GENERATOR", self.AMBER)

        r1 = tk.Frame(ci, bg=self.CARD)
        r1.pack(fill="x", padx=14, pady=(8, 6))
        self._dim(r1, "Count").pack(side="left")
        self.cpn_count = tk.StringVar(value="5")
        self._tip(self._entry(r1, var=self.cpn_count, w=6),
                  "How many temp emails / coupons to generate this run.")\
            .pack(side="left", padx=(6, 20))
        self.cpn_bulk = tk.BooleanVar()
        self._tip(ttk.Checkbutton(r1, text="Bulk mode", variable=self.cpn_bulk),
                  "Create ALL temp emails up front, you sign them all up on Zoro in "
                  "one go, then every inbox is watched in parallel.\nOff = one email "
                  "at a time.")\
            .pack(side="left")
        self._dim(r1, "Provider").pack(side="left", padx=(16, 0))
        self.cpn_provider = tk.StringVar(value="auto")
        cb_prov = ttk.Combobox(r1, textvariable=self.cpn_provider,
                               values=["auto", "temptf", "tempmail100", "mailtm", "tempmailplus"],
                               width=12, state="readonly")
        cb_prov.pack(side="left", padx=(6, 0))
        self._tip(cb_prov,
                  "Temp email provider:\n"
                  "• auto: intelligent rotation across all working services (.edu.pl, uz8.net, uberip.com, mailto.plus)\n"
                  "• temptf: uses academic @high.edu.pl (best deliverability)\n"
                  "• tempmail100: uses @uz8.net and partner domains\n"
                  "• mailtm: uses @uberip.com\n"
                  "• tempmailplus: uses @mailto.plus")

        r2 = tk.Frame(ci, bg=self.CARD)
        r2.pack(fill="x", padx=14, pady=(0, 6))
        self._dim(r2, "Bulk wait").pack(side="left")
        self.cpn_wait = tk.StringVar(value="180")
        self._tip(self._entry(r2, var=self.cpn_wait, w=6),
                  "Seconds you get to sign all the emails up on zoro.com before "
                  "processing starts.\nAllow ~5s per email — addresses not signed up "
                  "in time just time out as failures.")\
            .pack(side="left", padx=(6, 6))
        self._dim(r2, "seconds").pack(side="left")
        self._dim(r2, "Parallel").pack(side="left", padx=(20, 0))
        self.cpn_workers = tk.StringVar(value="10")
        self._tip(self._entry(r2, var=self.cpn_workers, w=6),
                  "How many inboxes are watched at the same time in bulk mode.\n"
                  "10 is tested-safe against the temp-mail API; higher finishes "
                  "sooner but polls it harder.")\
            .pack(side="left", padx=(6, 6))
        self._dim(r2, "at a time").pack(side="left")

        self._sep(ci)

        br = tk.Frame(ci, bg=self.CARD)
        br.pack(fill="x", padx=14, pady=(4, 12))
        self.btn_cpn_go = self._btn(br, "▶ Start", self.GREEN, self._start_coupon)
        self.btn_cpn_go.pack(side="left", padx=(0, 6))
        self.btn_cpn_stop = self._btn(br, "■ Stop", self.RED, lambda: self._stop("coupon"))
        self.btn_cpn_stop.pack(side="left", padx=(0, 12))
        self.btn_cpn_stop.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        self.lbl_cpn = tk.Label(br, text="Idle", font=(self.MONO, 9),
                                bg=self.CARD, fg=self.MUTED)
        self.lbl_cpn.pack(side="left", padx=(12, 0))
        self._btn(br, "Clear Coupon Cache", self.AMBER,
                  self._clear_coupon_cache, small=True).pack(side="right")

        return page

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Post-Delivery
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_post_delivery(self, container):
        page = tk.Frame(container, bg=self.BG)
        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        self._heading(pi, "POST-DELIVERY ACTIONS", self.PURPLE)

        # Action bar
        ab = tk.Frame(pi, bg=self.CARD)
        ab.pack(fill="x", padx=14, pady=(8, 6))
        # Read-only status table. Messaging is RUN from the Closing page
        # (✉ Run Messaging → scripts/send_delivery_messages.py) — the old run
        # buttons here messaged from the legacy `orders` table and are gone.
        self._btn(ab, "↻ Refresh", self.CYAN, self._refresh_post_delivery_table, small=True).pack(side="left")
        self._pd_stats = tk.Label(ab, text="", font=(self.FONT, 9), bg=self.CARD, fg=self.DIM)
        self._pd_stats.pack(side="right", padx=(0, 12))

        # Treeview
        cols = ("order", "account", "delivery_date", "days_ago", "msg_sent", "review_sent", "return_req", "refund")
        tw = tk.Frame(pi, bg=self.BORDER, bd=1, relief="solid")
        tw.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        tw.rowconfigure(0, weight=1); tw.columnconfigure(0, weight=1)

        self._pd_tree = ttk.Treeview(tw, columns=cols, show="headings", selectmode="browse", height=24)
        style = ttk.Style()
        style.configure("Treeview", background=self.BORDER, foreground=self.TEXT,
                        fieldbackground=self.BORDER, rowheight=28, font=(self.FONT, 10))
        style.configure("Treeview.Heading", background=self.CARD, foreground=self.ACCENT,
                        font=(self.FONT, 10, "bold"), relief="flat", borderwidth=1)
        style.map("Treeview", background=[("selected", "#33467c")], foreground=[("selected", self.TEXT)])

        headings = {"order":"Amazon Order", "account":"Account", "delivery_date":"Delivery Date",
                    "days_ago":"Days", "msg_sent":"Msg Sent?", "review_sent":"Review Sent?",
                    "return_req":"Return?", "refund":"Refund?"}
        widths = {"order":140, "account":80, "delivery_date":130, "days_ago":50,
                  "msg_sent":80, "review_sent":90, "return_req":60, "refund":60}
        for c in cols:
            self._pd_tree.heading(c, text=headings.get(c, c))
            self._pd_tree.column(c, width=widths.get(c, 80), anchor="w", minwidth=40)

        vsb = ttk.Scrollbar(tw, orient="vertical", command=self._pd_tree.yview)
        hsb = ttk.Scrollbar(tw, orient="horizontal", command=self._pd_tree.xview)
        self._pd_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._pd_tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._pd_tree.tag_configure("even", background=self.INP_BG)
        self._pd_tree.tag_configure("odd",  background="#11131a")
        self._pd_tree.tag_configure("needs_action", foreground=self.RED)
        self._pd_tree.tag_configure("ok", foreground=self.GREEN)
        self._pd_tree.bind("<Double-1>", self._on_tree_double_click)

        self.after(2000, self._refresh_post_delivery_table)
        return page

    def _refresh_post_delivery_table(self):
        try:
            from utils.unified_db import get_conn
            from engine.post_delivery import _delivery_days_ago
            with get_conn() as conn:
                rows = conn.execute("""
                    SELECT amazon_order_id, account, delivery_date, delivery_status,
                           delivered_msg_sent, review_requested, return_requested, refund_done
                    FROM closing_orders
                    WHERE delivery_status IN ('AllDelivered', 'Delivered')
                      AND delivery_date != ''
                      AND delivery_date IS NOT NULL
                    ORDER BY delivery_date DESC
                    LIMIT 500
                """).fetchall()

            self._pd_tree.delete(*self._pd_tree.get_children())
            needs_msg = 0
            needs_review = 0
            for idx, r in enumerate(rows):
                days = str(_delivery_days_ago(r["delivery_date"])) if r["delivery_date"] else ""
                msg = r["delivered_msg_sent"] or "No"
                rev = r["review_requested"] or "No"
                ret = r["return_requested"] or "No"
                ref = r["refund_done"] or "No"

                if msg not in ("Yes", "No Permission"):
                    needs_msg += 1
                if rev not in ("Yes", "Already Requested", "Expired"):
                    needs_review += 1

                tag = "ok" if (msg == "Yes" and rev in ("Yes", "Already Requested", "Expired")) else "needs_action"
                self._pd_tree.insert("", "end", values=(
                    r["amazon_order_id"], r["account"] or "",
                    r["delivery_date"] or "", days, msg, rev, ret, ref
                ), tags=("even" if idx % 2 == 0 else "odd", tag))

            self._pd_stats.config(text=f"{len(rows)} delivered │ {needs_msg} need msg │ {needs_review} need review")
        except Exception as e:
            self._pd_stats.config(text=f"Error: {e}")

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Closing
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_closing(self, container):
        page = tk.Frame(container, bg=self.BG)

        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        self._heading(pi, "CLOSING PIPELINE", self.AMBER)

        # ── Controls ────────────────────────────────────────────────
        # TWO actions only (per selected account):
        #   ▶ Run Closing  = the WHOLE chain via scripts/daily_bulk.py
        #     (Gmail fetch → closing ingest → reconcile → SC scrape → FedEx →
        #      reconcile → coverage). Ports/months are automatic from config.
        #   ✉ Run Messaging = decoupled delivery check-ins + review requests
        #     for everything the DB shows delivered (run when you're free).
        # Shared messaging knobs (apply to whichever account's ✉ button is used)
        ctrl = tk.Frame(pi, bg=self.CARD)
        ctrl.pack(fill="x", padx=14, pady=(8, 6))
        self._dim(ctrl, "Msg limit").pack(side="left")
        self.cls_msg_limit = self._combo(ctrl, ["100", "300", "500", "1000", "0"], 5)
        self.cls_msg_limit.set("0")              # per-run cap; 0 = unlimited
        self.cls_msg_limit.pack(side="left", padx=(4, 8))
        self.cls_msg_dry = tk.BooleanVar()
        ttk.Checkbutton(ctrl, text="Msg Dry Run", variable=self.cls_msg_dry).pack(side="left", padx=(8, 0))

        # Chain messaging onto the end of ▶ Run Closing. daily_bulk already has the step — the
        # launcher has simply been switching it off with SKIP_MESSAGES=1.
        #
        # Worth doing for more than convenience: closing and messaging share one Chrome per
        # account (report_port == sc_port == 9331 / 9333) and lock under DIFFERENT names, so
        # pressing both buttons at once has them navigating each other's tabs. That is what
        # halted the HOANK tracker at 17:53 on 19 Sep, mid report-download, on a review page it
        # never opened. Run as one chain they are sequential, and cannot collide at all.
        self._cls_chain_var = tk.BooleanVar(
            value=bool((_read_cfg_file().get("closing") or {}).get("message_after_closing")))
        self._cls_chain_cb = ttk.Checkbutton(ctrl, text="✉ Message after closing",
                                             variable=self._cls_chain_var,
                                             command=self._toggle_chain_messaging)
        self._cls_chain_cb.pack(side="left", padx=(12, 0))
        self._tip(self._cls_chain_cb,
                  "CHAIN MESSAGING — ▶ Run Closing continues straight into delivery\n"
                  "check-ins and review requests when the closing chain finishes.\n\n"
                  "Uses the Msg limit and Msg Dry Run set here, per account.\n\n"
                  "Safer than pressing ▶ and ✉ together: closing and messaging share\n"
                  "one Chrome per account, so run at the same time they navigate each\n"
                  "other's pages. Chained, they run one after the other.")

        # One switch for the whole chain, both accounts. Every later step attaches to the
        # Chrome the first one launches, so this single choice carries all of them.
        tk.Frame(ctrl, bg=self.MUTED, width=1).pack(side="left", fill="y", padx=(16, 12), pady=4)
        self._cls_headless_var = tk.BooleanVar(value=self._closing_headless())
        self._cls_headless_cb = ttk.Checkbutton(ctrl, text="🫥 Headless (closing)",
                                                variable=self._cls_headless_var,
                                                command=self._toggle_closing_headless)
        self._cls_headless_cb.pack(side="left")
        self._tip(self._cls_headless_cb,
                  "HEADLESS CLOSING — the whole chain runs with no windows:\n"
                  "report download, ingest, Seller Central scrape, FedEx,\n"
                  "delivery messages and review requests.\n\n"
                  "It works because only the FIRST step launches a Chrome; the rest\n"
                  "attach to it, so they inherit whatever this is set to.\n\n"
                  "Applies to the next Chrome the chain launches. One already open on\n"
                  "9331/9333 keeps its window until you close it and start again.\n"
                  "You still need to be LOGGED IN on that profile — do that headed once,\n"
                  "the session persists in the profile, then switch this on.")

        # Both stores in ONE sequential chain: Gmail fetch/sync once, no DB contention between
        # two parallel runs, one set of Chromes at a time. This is what the scheduler uses.
        both_btn = self._btn(ctrl, "▶ Run Closing (all)", self.GREEN,
                             lambda: self._start_bulk("ALL"), small=True)
        both_btn.pack(side="left", padx=(16, 0))
        self._tip(both_btn,
                  f"RUN EVERY STORE IN ONE CHAIN — {' then '.join(STORES)}, sequentially.\n"
                  "The Gmail archive fetch/sync runs once and the stores never contend\n"
                  "for the database. Same steps as the per-store buttons; the per-store\n"
                  "Stop buttons stop the whole chain.")
        # Delivery on its own (27 Sep 2026): Zoro's order tracking for every shipped,
        # undelivered order of both stores — the step the chain runs as step 5, runnable
        # any time without the report/scrape/messaging around it. No Chrome needed.
        dlv_btn = self._btn(ctrl, "🚚 Delivery check", self.CYAN,
                            lambda: self._start_delivery_check(), small=True)
        dlv_btn.pack(side="left", padx=(8, 0))
        self._tip(dlv_btn,
                  "DELIVERY CHECK (both stores) — asks Zoro's own order tracking for every\n"
                  "shipped, undelivered order of the last 60 days (rechecked every 20 h):\n"
                  "delivery status + date, last scan, stalled days, missing tracking IDs,\n"
                  "then Completed labels. Feeds the dashboard's Attention → Delivery lists\n"
                  "(late / stalled) and the messaging run (check-in on delivery, delay\n"
                  "notice after 3 days without movement). No Chrome, ~0.5 s per order.")
        # Undelivered list (12 Nov 2026): the undelivered orders of chosen Zoro accounts as an
        # Excel file, each order first re-checked: found (delivered now), cancelled, refunded.
        und_btn = self._btn(ctrl, "📋 Undelivered", self.AMBER,
                            self._open_undelivered_dialog, small=True)
        und_btn.pack(side="left", padx=(8, 0))
        self._tip(und_btn,
                  "UNDELIVERED LIST — the orders a Zoro account bought (robstark771,\n"
                  "abecomllc26, …) in a date range that are not delivered, as an Excel file.\n\n"
                  "Before an order goes on the list it is checked:\n"
                  "  • FOUND — Zoro's tracking is asked again now (delivered → own sheet)\n"
                  "  • CANCELLED — at Zoro or on Amazon (own sheet)\n"
                  "  • REFUNDED — Zoro 'Cash Refund' e-mails in that account's Gmail\n"
                  "    (own sheet); Amazon's refund to the buyer is shown as a column.\n\n"
                  "The file opens when it is ready (reports/undelivered/). No Chrome;\n"
                  "~1 s per order to ask Zoro.")

        self.lbl_cls = tk.Label(ctrl, text="Idle", font=(self.MONO, 9),
                                bg=self.CARD, fg=self.MUTED)
        self.lbl_cls.pack(side="right")

        # ── One full control box PER STORE (HOANK | Hathoro | Wrenchly) ─────────
        self._cls_btns = {}
        acct_row = tk.Frame(pi, bg=self.CARD)
        acct_row.pack(fill="x", padx=14, pady=(0, 6))
        _store_colors = {"HOANK": self.CYAN, "Hathoro": self.PURPLE, "Wrenchly": self.AMBER}
        for col in range(len(STORES)):
            acct_row.columnconfigure(col, weight=1)
        for col, (acct, color) in enumerate((a, _store_colors.get(a, self.GREEN)) for a in STORES):
            box = tk.Frame(acct_row, bg=self.INP_BG,
                           highlightbackground=self.DIM, highlightthickness=1)
            box.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
            tk.Label(box, text=acct.upper(), font=(self.MONO, 11, "bold"),
                     bg=self.INP_BG, fg=color).pack(anchor="w", padx=10, pady=(6, 2))
            bb = tk.Frame(box, bg=self.INP_BG)
            bb.pack(fill="x", padx=10, pady=(0, 4))
            self._btn(bb, "🚀 Chrome", color,
                      lambda a=acct: self._launch_chrome_closing(a), small=True).pack(side="left", padx=(0, 6))
            go = self._btn(bb, "▶ Run Closing", self.GREEN,
                           lambda a=acct: self._start_bulk(a), small=True)
            go.pack(side="left", padx=(0, 6))
            msg = self._btn(bb, "✉ Messaging", self.CYAN,
                            lambda a=acct: self._start_messaging(a), small=True)
            msg.pack(side="left", padx=(0, 6))
            stop = self._btn(bb, "■ Stop", self.RED,
                             lambda a=acct: self._stop_closing_procs(a), small=True)
            stop.pack(side="left", padx=(0, 6))
            stop.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
            lbl = tk.Label(box, text="Idle", font=(self.MONO, 9), bg=self.INP_BG, fg=self.MUTED)
            lbl.pack(anchor="w", padx=10, pady=(0, 6))
            self._cls_btns[acct] = {"go": go, "msg": msg, "stop": stop, "lbl": lbl}

        self._sep(pi)

        # ── Stats ───────────────────────────────────────────────────
        st = tk.Frame(pi, bg=self.CARD)
        st.pack(fill="x", padx=14, pady=(4, 12))
        self.cls_stat_total = tk.Label(st, text="—", font=(self.MONO, 11, "bold"),
                                       bg=self.CARD, fg=self.TEXT)
        self.cls_stat_total.pack(side="left", padx=(0, 16))
        self.cls_stat_completed = tk.Label(st, text="—", font=(self.MONO, 11, "bold"),
                                           bg=self.CARD, fg=self.GREEN)
        self.cls_stat_completed.pack(side="left", padx=(0, 16))
        self.cls_stat_incomplete = tk.Label(st, text="—", font=(self.MONO, 11, "bold"),
                                            bg=self.CARD, fg=self.AMBER)
        self.cls_stat_incomplete.pack(side="left", padx=(0, 16))
        self.cls_stat_cancelled = tk.Label(st, text="—", font=(self.MONO, 11, "bold"),
                                           bg=self.CARD, fg=self.RED)
        self.cls_stat_cancelled.pack(side="left", padx=(0, 16))
        self.cls_stat_errors = tk.Label(st, text="—", font=(self.MONO, 11, "bold"),
                                        bg=self.CARD, fg=self.DIM)
        self.cls_stat_errors.pack(side="left")

        return page

    def _launch_chrome_closing(self, account: str = "HOANK"):
        """Launch a dedicated Chrome for closing on account-specific port."""
        import subprocess
        port = PORTS.get(f"{str(account).upper()}_CLOSING", 9331)
        profile_dir = os.path.expanduser(f"~/Library/Application Support/Google/Chrome/{account}_Closing_Profile")
        os.makedirs(profile_dir, exist_ok=True)
        self._mark_profile_clean_exit(profile_dir)
        cmd = [
            _resolve_chrome_exe(),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--hide-crash-restore-bubble",
            "--no-first-run",
            "--no-default-browser-check",
            *CHROME_SOFT_FLAGS,
        ]
        # Everything downstream — report download, SC scrape, messaging, review requests —
        # only ATTACHES to this Chrome, so whether it has a window is decided right here.
        hl = self._closing_headless()
        cmd += HEADLESS_FLAGS if hl else ["--start-maximized"]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            how = " (headless)" if hl else ""
            self._flash_msg(f"Chrome launched for {account} on port {port}{how}", self.GREEN)
            self._feed_line(f"Chrome launched for {account} Closing (port {port}){how}", "ok")
            self.after(3000, self._check_ports)
        except Exception as e:
            self._flash_msg(f"Failed to launch Chrome: {e}", self.RED)

    def _start_bulk(self, account):
        """▶ Run Closing = the WHOLE completion chain via scripts/daily_bulk.py (Gmail fetch →
        closing ingest → reconcile → SC scrape → delivery → gap-fill → reconcile → messaging
        when chained → coverage). account = "HOANK" | "Hathoro" | "Wrenchly" | "ALL" (every
        store) | a list of stores (the scheduler) — several stores run sequentially in ONE
        process, so the Gmail fetch runs once and the stores never contend for the DB."""
        if isinstance(account, (list, tuple)):
            accts = [a for a in STORES if a in account]
        elif str(account).strip().upper() in ("ALL", "BOTH"):
            accts = list(STORES)
        else:
            accts = [str(account)]
        if not accts:
            return
        both = len(accts) > 1
        account = accts[0] if not both else "ALL"
        name = "bulk_all" if both else f"bulk_{account.lower()}"
        if name in self._procs or "bulk_all" in self._procs or (both and any(p.startswith("bulk_") for p in self._procs)):
            messagebox.showinfo("Info", "A closing run is already active.")
            return
        cmd = [RUNNER_PYTHON, "-u", os.path.join(SCRIPT_DIR, "scripts", "daily_bulk.py")]
        chain = bool(self._cls_chain_var.get())
        extra_env = {"ACCOUNTS": ",".join(accts), "SKIP_MESSAGES": "0" if chain else "1"}
        if chain:
            # daily_bulk's step 7 reads these; same knobs the ✉ button uses.
            extra_env["MSG_LIMIT"] = (self.cls_msg_limit.get() or "0").strip()
            if self.cls_msg_dry.get():
                extra_env["MSG_DRY"] = "1"
        self._filter.set("All")
        self._launch(name, cmd, "both" if both else account, extra_env=extra_env)
        # Start stays enabled so the OTHER account can also be launched. Stop targets
        # whichever account is selected in the dropdown.
        for a in accts:
            self._set_cls_state(a, running=True)
        self._refresh_cls_lbl()
        self._feed_line(f"Closing bulk started ({' + '.join(accts) + ', sequential' if both else account})"
                        + (f" → then messaging, limit {extra_env['MSG_LIMIT']}"
                           + (" DRY" if self.cls_msg_dry.get() else "") if chain else ""), "ok")
        self._update_hdr()

    def _start_delivery_check(self, account="ALL"):
        """🚚 Delivery check = scripts/zoro_delivery_check.py: Zoro's own order tracking for
        every shipped, undelivered order (both stores in one run, one lock). The closing
        chain runs the same script as its step 5; this button runs it alone."""
        name = "delivery_all"
        if name in self._procs:
            messagebox.showinfo("Info", "A delivery check is already running.")
            return
        cmd = [RUNNER_PYTHON, "-u", os.path.join(SCRIPT_DIR, "scripts", "zoro_delivery_check.py")]
        extra_env = {"ZORO_TRACK_ACCOUNT": str(account or "ALL")}
        self._filter.set("All")
        self._launch(name, cmd, "both", extra_env=extra_env)
        self._feed_line("Delivery check started (Zoro tracking, both stores)", "ok")
        self._update_hdr()

    _UNDELIVERED_PREFS = os.path.join(SCRIPT_DIR, "data", "undelivered_report.json")
    _UNDELIVERED_DEFAULT = [{"account": "robstark771", "from": "2026-10-01", "to": ""},
                            {"account": "abecomllc26", "from": "2026-09-01", "to": ""}]

    def _open_undelivered_dialog(self):
        """📋 Undelivered: pick the Zoro accounts and purchase-date ranges, then run
        scripts/undelivered_report.py. The ranges are kept in data/undelivered_report.json
        (the script reads the same file when run without arguments)."""
        if "undelivered_report" in self._procs:
            messagebox.showinfo("Info", "The undelivered list is already being made — it opens when ready.")
            return
        try:
            with open(self._UNDELIVERED_PREFS, "r", encoding="utf-8") as f:
                prefs = json.load(f) or {}
        except Exception:
            prefs = {}
        saved = prefs.get("ranges") or self._UNDELIVERED_DEFAULT
        win = tk.Toplevel(self)
        win.title("Undelivered orders list")
        win.configure(bg=self.BG)
        win.transient(self)
        po, pi = self._panel(win)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        self._heading(pi, "UNDELIVERED ORDERS — per Zoro account", self.AMBER)
        tk.Label(pi, text="Zoro account = its Gmail name (robstark771) or address; Amazon purchase dates. "
                          "Leave a row's account empty to skip it.",
                 font=(self.FONT, 9), bg=self.CARD, fg=self.MUTED, justify="left").pack(anchor="w", padx=14, pady=(0, 6))
        grid = tk.Frame(pi, bg=self.CARD)
        grid.pack(fill="x", padx=14, pady=(0, 6))
        for c, h in enumerate(("Zoro account", "From", "To")):
            tk.Label(grid, text=h, font=(self.FONT, 9, "bold"), bg=self.CARD, fg=self.DIM).grid(
                row=0, column=c, sticky="w", padx=(0, 8))

        def date_box(value):
            de = DateEntry(grid, width=11, background=self.CARD, foreground=self.TEXT,
                           borderwidth=1, date_pattern='y-mm-dd',
                           selectbackground=self.ACCENT, selectforeground=self.BG,
                           normalbackground=self.INP_BG, normalforeground=self.TEXT,
                           weekendbackground=self.INP_BG, weekendforeground=self.TEXT,
                           othermonthbackground=self.INP_BG, othermonthforeground=self.DIM)
            try:
                de.set_date(datetime.strptime(value, "%Y-%m-%d").date())
            except Exception:
                de.set_date(datetime.now().date())
            return de

        rows = []
        for i in range(4):
            r = saved[i] if i < len(saved) else {}
            acct_v = tk.StringVar(value=str(r.get("account") or ""))
            self._entry(grid, acct_v, 20).grid(row=i + 1, column=0, sticky="w", padx=(0, 8), pady=2, ipady=3)
            frm = date_box(str(r.get("from") or ""))
            frm.grid(row=i + 1, column=1, sticky="w", padx=(0, 8), pady=2)
            to = date_box(str(r.get("to") or ""))       # no saved end date → today
            to.grid(row=i + 1, column=2, sticky="w", padx=(0, 8), pady=2)
            rows.append((acct_v, frm, to))
        live_var = tk.BooleanVar(value=prefs.get("live", True))
        gmail_var = tk.BooleanVar(value=prefs.get("gmail", True))
        opts = tk.Frame(pi, bg=self.CARD)
        opts.pack(fill="x", padx=14, pady=(2, 6))
        ttk.Checkbutton(opts, text="Ask Zoro first (found / cancelled)", variable=live_var).pack(anchor="w")
        ttk.Checkbutton(opts, text="Read Zoro refund e-mails (already refunded)", variable=gmail_var).pack(anchor="w")
        bar = tk.Frame(pi, bg=self.CARD)
        bar.pack(fill="x", padx=14, pady=(4, 10))

        def run():
            ranges, bad = [], []
            today = datetime.now().date()
            for acct_v, frm_box, to_box in rows:
                acct = acct_v.get().strip()
                if not acct:
                    continue
                try:
                    frm, to = frm_box.get_date(), to_box.get_date()
                except Exception:
                    bad.append(acct)
                    continue
                if frm > to:
                    bad.append(acct)
                    continue
                # an end date of today is saved as "" so the next run ends on ITS today
                ranges.append({"account": acct, "from": frm.isoformat(),
                               "to": "" if to >= today else to.isoformat()})
            if bad:
                messagebox.showerror("Undelivered list", f"Check the dates of: {', '.join(bad)} "
                                     "('From' must not be after 'To').", parent=win)
                return
            if not ranges:
                messagebox.showerror("Undelivered list", "Enter at least one Zoro account.", parent=win)
                return
            data = {"ranges": ranges, "live": bool(live_var.get()), "gmail": bool(gmail_var.get())}
            try:
                tmp = self._UNDELIVERED_PREFS + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=1)
                os.replace(tmp, self._UNDELIVERED_PREFS)
            except Exception:
                pass
            win.destroy()
            self._start_undelivered_report(ranges, data["live"], data["gmail"])

        self._btn(bar, "▶ Make list", self.GREEN, run, small=True).pack(side="left")
        self._btn(bar, "Cancel", self.MUTED, win.destroy, small=True).pack(side="left", padx=(8, 0))
        try:
            win.geometry(f"+{self.winfo_rootx() + 80}+{self.winfo_rooty() + 80}")
        except Exception:
            pass

    def _start_undelivered_report(self, ranges, live=True, gmail=True):
        name = "undelivered_report"
        if name in self._procs:
            messagebox.showinfo("Info", "The undelivered list is already being made.")
            return
        specs = [f"{r['account']}:{r.get('from') or ''}:{r.get('to') or ''}" for r in ranges]
        cmd = [RUNNER_PYTHON, "-u", os.path.join(SCRIPT_DIR, "scripts", "undelivered_report.py"), *specs, "--open"]
        if not live:
            cmd.append("--no-live")
        if not gmail:
            cmd.append("--no-gmail")
        self._filter.set("All")
        self._launch(name, cmd)
        self._feed_line("Undelivered list started (" + ", ".join(
            f"{r['account']} {r.get('from') or '…'} → {r.get('to') or 'today'}" for r in ranges) + ")", "ok")
        self._update_hdr()

    def _start_messaging(self, account):
        """✉ Run Messaging = scripts/send_delivery_messages.py for the selected
        account: delivery check-ins + review requests for every delivered order
        still owed one (6–30 days after delivery, capped by Msg limit per run).
        Needs the account's logged-in Chrome (🚀 Launch button)."""
        name = f"messages_{account.lower()}"
        if name in self._procs:
            messagebox.showinfo("Info", f"{account} messaging is already running.")
            return
        limit = (self.cls_msg_limit.get() or "0").strip()
        cmd = [RUNNER_PYTHON, "-u", os.path.join(SCRIPT_DIR, "scripts", "send_delivery_messages.py")]
        extra_env = {"MSG_ACCOUNT": str(account), "MSG_LIMIT": limit}
        if self.cls_msg_dry.get():
            extra_env["MSG_DRY"] = "1"
        self._launch(name, cmd, account, extra_env=extra_env)
        self._set_cls_state(account, running=True)
        self._refresh_cls_lbl()
        self._feed_line(f"Messaging started ({account}, limit {limit}"
                        + (", DRY" if self.cls_msg_dry.get() else "") + ")", "ok")
        self._update_hdr()

    def _stop_closing_procs(self, account):
        """Stop this account's bulk and/or messaging runs (a combined both-store chain too)."""
        acct = account.lower()
        for name in (f"bulk_{acct}", f"messages_{acct}", "bulk_all", "delivery_all"):
            if name in self._procs:
                self._stop(name)

    def _set_cls_state(self, account, running, status_text=None, color=None):
        b = self._cls_btns.get(account)
        if not b:
            return
        if running:
            b["stop"].config(state="normal", fg=self.RED, highlightbackground=self.RED)
            b["lbl"].config(text="Running", fg=self.GREEN)
        else:
            active = any(n.startswith(("bulk_", "messages_")) and n.endswith(account.lower())
                         for n in self._procs)
            if not active:
                b["stop"].config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
                b["lbl"].config(text=status_text or "Idle", fg=color or self.MUTED)

    def _refresh_cls_lbl(self):
        running = sorted(p.upper() for p in self._procs
                         if p.startswith(("bulk_", "messages_")))
        if running:
            self.lbl_cls.config(text="Running: " + ", ".join(running), fg=self.GREEN)

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Listings ETL (Zoro feed → catalog DB → listing health)
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_listings(self, container):
        page = tk.Frame(container, bg=self.BG)
        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        self._heading(pi, "LISTINGS REFRESH", self.CYAN,
                      subtitle="Zoro feed → catalog DB → listing health (never edits Amazon)")
        ctrl = tk.Frame(pi, bg=self.CARD)
        ctrl.pack(fill="x", padx=14, pady=(8, 6))
        self.btn_lst_go = self._btn(ctrl, "▶ Refresh Listings", self.GREEN,
                                    self._start_listings, small=True)
        self.btn_lst_go.pack(side="left", padx=(0, 6))
        self._tip(self.btn_lst_go,
                  "Full daily refresh: pull the Zoro S3 feed (only files that "
                  "changed), rebuild the catalog DB if needed, then recompute "
                  "listing health into the unified DB.\nSafe alongside the "
                  "placers — it only computes and stores; nothing on Amazon "
                  "or Zoro is touched.")
        _b_force = self._btn(ctrl, "⟳ Force DB Rebuild", self.AMBER,
                             lambda: self._start_listings(("--force-db",)), small=True)
        _b_force.pack(side="left", padx=(0, 6))
        self._tip(_b_force, "Rebuild the catalog DB even when the feed looks unchanged.")
        _b_rec = self._btn(ctrl, "📊 Recompute Only", self.PURPLE,
                           lambda: self._start_listings(("--skip-download",)), small=True)
        _b_rec.pack(side="left", padx=(0, 6))
        self._tip(_b_rec, "Skip the S3 download — recompute listing health from "
                          "the current catalog + reports only (fast).")
        self.btn_lst_stop = self._btn(ctrl, "■ Stop", self.RED,
                                      lambda: self._stop("listings"), small=True)
        self.btn_lst_stop.pack(side="left", padx=(0, 6))
        self.btn_lst_stop.config(state="disabled", fg=self.MUTED,
                                 highlightbackground=self.MUTED)
        self.lbl_lst = tk.Label(ctrl, text="Idle", font=(self.MONO, 9),
                                bg=self.CARD, fg=self.MUTED)
        self.lbl_lst.pack(side="right")
        self._dim(pi, "Runtime: ~2-5 min on a no-change day; ~30-50 min when the "
                      "Zoro feed republished (1.17 GB download + catalog rebuild). "
                      "Results land in listing_health / listing_relist for the "
                      "dashboard — review there before acting on Amazon."
                  ).pack(anchor="w", padx=14, pady=(2, 10))
        po.pack_configure(fill="x", expand=False)
        self._build_listings_report_panel(page)
        return page

    # ── Listings report (13 Nov 2026): every listing with the handling time Amazon really has ──
    _LR_DIR = os.path.join(SCRIPT_DIR, "data", "listings_report")
    _LR_XLSX = os.path.join(os.path.dirname(SCRIPT_DIR), "reports", "listings")
    _LR_TREE = (("store", "Store", 80), ("sku", "SKU", 190), ("asin", "ASIN", 105), ("title", "Product", 330),
                ("own", "PL/DS", 55), ("status", "Status", 75), ("qty", "Qty", 60), ("price", "Price", 75),
                ("handling", "Handling time", 120), ("guard", "Listing guard", 300))

    def _build_listings_report_panel(self, page):
        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._heading(pi, "LISTINGS REPORT", self.AMBER,
                      subtitle="fresh All Listings Report + every listing's handling time, read from Seller Central "
                               "(never edits Amazon)")
        ctrl = tk.Frame(pi, bg=self.CARD)
        ctrl.pack(fill="x", padx=14, pady=(8, 6))
        self.btn_lr_go = self._btn(ctrl, "▶ Get Fresh Report", self.GREEN, self._start_listings_report, small=True)
        self.btn_lr_go.pack(side="left", padx=(0, 6))
        self._tip(self.btn_lr_go,
                  "For each ticked store, in its own Seller Central Chrome (HOANK 9331, Hathoro 9333,\n"
                  "Wrenchly 9337, taking turns with closing): download a fresh All Listings Report, then read\n"
                  "every listing's offer the way the Edit Listing page does — handling time, quantity, price,\n"
                  "brand, active or not. About 1 s per listing (HOANK ≈ 15–20 min).\n"
                  "Read-only: nothing is changed on Amazon. The list opens here when it is done,\n"
                  "and an Excel copy lands in reports/listings/.")
        self._lr_store_vars = {}
        for s in STORES:
            v = tk.BooleanVar(value=s in ("HOANK", "Wrenchly"))
            self._lr_store_vars[s] = v
            ttk.Checkbutton(ctrl, text=s, variable=v).pack(side="left", padx=(4, 4))
        self._btn(ctrl, "👁 Show", self.CYAN, self._open_listings_report, small=True).pack(side="left", padx=(10, 6))
        self._btn(ctrl, "📄 Excel", self.PURPLE, self._open_listings_excel, small=True).pack(side="left", padx=(0, 6))
        self.btn_lr_stop = self._btn(ctrl, "■ Stop", self.RED, lambda: self._stop("listings_report"), small=True)
        self.btn_lr_stop.pack(side="left", padx=(0, 6))
        self.btn_lr_stop.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        self.lbl_lr = tk.Label(ctrl, text="Idle", font=(self.MONO, 9), bg=self.CARD, fg=self.MUTED)
        self.lbl_lr.pack(side="right")
        self.lbl_lr_sum = tk.Label(pi, text="", font=(self.MONO, 10), bg=self.CARD, fg=self.TEXT,
                                   justify="left", anchor="w", wraplength=1100)
        self.lbl_lr_sum.pack(anchor="w", fill="x", padx=14, pady=(4, 12))
        self._refresh_listings_report_summary()

    def _lr_reports(self):
        """The saved reports (data/listings_report/<store>.json), HOANK first."""
        out = []
        for s in STORES:
            p = os.path.join(self._LR_DIR, f"{s}.json")
            try:
                with open(p, "r", encoding="utf-8") as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                pass
        return out

    @staticmethod
    def _lr_days(n):
        return f"{n} day" + ("" if str(n) == "1" else "s")

    def _lr_handling(self, l, default):
        if l.get("error"):
            return "? (not read)"
        if l.get("handling") is None:
            return f"{self._lr_days(default)} (default)" if default else "default"
        return self._lr_days(l["handling"])

    def _lr_summary(self, rep):
        """'HOANK — 991 listings, 880 active · read 13 Nov 14:40\n   1 day (default) 940 · 7 days 25 · …'"""
        from collections import Counter
        ls = rep.get("listings") or []
        active = sum(1 for l in ls if (l.get("status") or "").lower() == "active")
        c = Counter(self._lr_handling(l, rep.get("default_days")) for l in ls)
        order = sorted(c.items(), key=lambda kv: ("default" not in kv[0], kv[0].startswith("?"),
                                                  int(kv[0].split()[0]) if kv[0].split()[0].isdigit() else 99))
        return (f"{rep['store']} — {len(ls):,} listings, {active:,} active · read {rep.get('made_at', '?')}\n"
                f"   handling time: " + " · ".join(f"{k} {v:,}" for k, v in order))

    def _refresh_listings_report_summary(self):
        reps = self._lr_reports()
        if not hasattr(self, "lbl_lr_sum"):
            return
        self.lbl_lr_sum.config(text="\n".join(self._lr_summary(r) for r in reps) if reps else
                               "No report yet — tick the stores and press ▶ Get Fresh Report.",
                               fg=self.TEXT if reps else self.DIM)

    def _start_listings_report(self):
        name = "listings_report"
        if name in self._procs:
            messagebox.showinfo("Info", "The listings report is already running — it opens when ready.")
            return
        stores = [s for s, v in self._lr_store_vars.items() if v.get()]
        if not stores:
            messagebox.showinfo("Listings report", "Tick at least one store.")
            return
        cmd = [RUNNER_PYTHON, "-u", os.path.join(SCRIPT_DIR, "scripts", "listings_report.py")]
        for s in stores:
            cmd += ["--store", s]
        self._filter.set("All")
        self._launch(name, cmd)
        self.btn_lr_go.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        self.btn_lr_stop.config(state="normal", fg=self.RED, highlightbackground=self.RED)
        self.lbl_lr.config(text="Running", fg=self.GREEN)
        self._feed_line(f"Listings report started ({', '.join(stores)})", "ok")
        self._update_hdr()

    def _open_listings_excel(self):
        files = sorted(glob.glob(os.path.join(self._LR_XLSX, "Listings_*.xlsx")), key=os.path.getmtime)
        if not files:
            messagebox.showinfo("Listings report", "No Excel file yet — run ▶ Get Fresh Report first.")
            return
        subprocess.Popen(["open", files[-1]])

    def _open_listings_report(self):
        """The list window: every listing of the saved reports, longest / non-default handling first."""
        reps = self._lr_reports()
        if not reps:
            messagebox.showinfo("Listings report", "No report yet — run ▶ Get Fresh Report first.")
            return
        old = getattr(self, "_lr_win", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        win = tk.Toplevel(self)
        self._lr_win = win
        win.title("Listings report — handling time of every listing")
        win.configure(bg=self.BG)
        try:
            win.geometry(f"1400x760+{self.winfo_rootx() + 30}+{self.winfo_rooty() + 40}")
        except Exception:
            win.geometry("1400x760")
        win.minsize(900, 420)
        po, pi = self._panel(win)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        h = self._heading(pi, "LISTINGS REPORT — handling time of every listing", self.AMBER)
        self._btn(h, "📄 Excel", self.PURPLE, self._open_listings_excel, small=True).pack(side="right")
        count = tk.Label(h, text="", font=(self.MONO, 10, "bold"), bg=self.CARD, fg=self.AMBER)
        count.pack(side="right", padx=(0, 12))

        tk.Label(pi, text="\n".join(self._lr_summary(r) for r in reps), font=(self.MONO, 10), bg=self.CARD,
                 fg=self.TEXT, justify="left", anchor="w").pack(anchor="w", fill="x", padx=14, pady=(2, 6))

        bar = tk.Frame(pi, bg=self.CARD)
        bar.pack(fill="x", padx=14, pady=(0, 6))
        tk.Label(bar, text="Store:", font=(self.MONO, 9), bg=self.CARD, fg=self.DIM).pack(side="left")
        store = self._combo(bar, ["All"] + [r["store"] for r in reps], 10)
        store.set("All")
        store.pack(side="left", padx=(4, 12))
        tk.Label(bar, text="Handling time:", font=(self.MONO, 9), bg=self.CARD, fg=self.DIM).pack(side="left")
        hsel = self._combo(bar, ["All", "Not the default", "Over the default", "0 days"], 16)
        hsel.set("All")
        hsel.pack(side="left", padx=(4, 12))
        tk.Label(bar, text="Show:", font=(self.MONO, 9), bg=self.CARD, fg=self.DIM).pack(side="left")
        ssel = self._combo(bar, ["All", "Active only", "Own brand (PL) only", "Listing guard only"], 18)
        ssel.set("All")
        ssel.pack(side="left", padx=(4, 12))
        tk.Label(bar, text="Search:", font=(self.MONO, 9), bg=self.CARD, fg=self.DIM).pack(side="left")
        q = tk.StringVar()
        self._entry(bar, q, 24).pack(side="left", padx=(4, 0))
        tk.Label(bar, text="double-click a row: open it on Seller Central", font=(self.FONT, 9),
                 bg=self.CARD, fg=self.MUTED).pack(side="right")

        st = ttk.Style(self)
        st.configure("Lr.Treeview", background=self.BORDER, foreground=self.TEXT, fieldbackground=self.BORDER,
                     rowheight=24, font=(self.FONT, 10))
        st.configure("Lr.Treeview.Heading", background=self.CARD, foreground=self.ACCENT,
                     font=(self.FONT, 10, "bold"), relief="flat", borderwidth=1)
        st.map("Lr.Treeview", background=[("selected", "#33467c")], foreground=[("selected", self.TEXT)])
        tw = tk.Frame(pi, bg=self.CARD)
        tw.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        tw.rowconfigure(0, weight=1)
        tw.columnconfigure(0, weight=1)
        tree = ttk.Treeview(tw, columns=[k for k, _h, _w in self._LR_TREE], show="headings",
                            selectmode="browse", style="Lr.Treeview")
        for k, head, w in self._LR_TREE:
            tree.heading(k, text=head)
            tree.column(k, width=w, minwidth=40, anchor="e" if k in ("qty", "price") else "w",
                        stretch=k in ("title", "guard"))
        vsb = ttk.Scrollbar(tw, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tree.tag_configure("even", background=self.INP_BG)
        tree.tag_configure("odd", background="#11131a")
        tree.tag_configure("odd_h", foreground=self.AMBER)       # handling time is not the store default
        tree.tag_configure("off", foreground=self.DIM)           # not active / quantity 0
        tree.tag_configure("err", foreground=self.RED)

        rows = []
        for rep in reps:
            d = rep.get("default_days")
            for l in rep.get("listings") or []:
                rows.append((rep["store"], d, l))

        def fill(*_):
            tree.delete(*tree.get_children())
            want_s, want_h, show = store.get(), hsel.get(), ssel.get()
            needle = q.get().strip().lower()
            picked = []
            for s, d, l in rows:
                if want_s != "All" and s != want_s:
                    continue
                h = l.get("handling")
                if want_h == "Not the default" and (h is None or h == d) and not l.get("error"):
                    continue
                if want_h == "Over the default" and not (h is not None and d is not None and h > d):
                    continue
                if want_h == "0 days" and h != 0:
                    continue
                if show == "Active only" and (l.get("status") or "").lower() != "active":
                    continue
                if show == "Own brand (PL) only" and l.get("own") != "PL":
                    continue
                if show == "Listing guard only" and not l.get("guard"):
                    continue
                if needle and needle not in " ".join(str(l.get(k) or "") for k in ("sku", "asin", "title", "brand")).lower():
                    continue
                picked.append((s, d, l))
            # non-default handling first (longest first), then active, then SKU
            picked.sort(key=lambda t: (t[2].get("handling") is None or t[2].get("handling") == t[1],
                                       -(t[2].get("handling") or 0),
                                       (t[2].get("status") or "").lower() != "active", t[0], t[2]["sku"]))
            for i, (s, d, l) in enumerate(picked):
                tags = ["even" if i % 2 == 0 else "odd"]
                if l.get("error"):
                    tags.append("err")
                elif (l.get("status") or "").lower() != "active" or l.get("qty") == 0:
                    tags.append("off")
                elif l.get("handling") is not None and l.get("handling") != d:
                    tags.append("odd_h")
                price = l.get("price")
                tree.insert("", "end", iid=f"{s}|{l['sku']}|{i}", tags=tags, values=(
                    s, l["sku"], l["asin"], (l.get("title") or "")[:90], l.get("own") or "",
                    l.get("status") or "", "" if l.get("qty") is None else f"{l['qty']:,}",
                    "" if price is None else f"${price:,.2f}", self._lr_handling(l, d),
                    l.get("guard") or (f"not read: {l['error']}" if l.get("error") else "")))
            count.config(text=f"{len(picked):,} of {len(rows):,} listings")

        def open_row(_e=None):
            sel = tree.selection()
            if not sel:
                return
            v = tree.item(sel[0], "values")
            import urllib.parse as _u
            webbrowser.open("https://sellercentral.amazon.com/interactive/listing/workflow/edit/offer?"
                            f"sku={_u.quote_plus(v[1])}&asin={_u.quote(v[2])}&marketplaceID=ATVPDKIKX0DER")

        for w in (store, hsel, ssel):
            w.bind("<<ComboboxSelected>>", fill)
        q.trace_add("write", lambda *_: fill())
        tree.bind("<Double-1>", open_row)
        fill()

    def _start_listings(self, extra_args=()):
        """Daily growth-side refresh: S3 feed pull → catalog rebuild (only when
        the feed changed) → listing-health recompute into the unified DB.
        Compute-and-store only — it never edits Amazon."""
        name = "listings"
        if name in self._procs:
            messagebox.showinfo("Info", "Listings refresh is already running.")
            return
        cmd = [RUNNER_PYTHON, "-u",
               os.path.join(SCRIPT_DIR, "scripts", "refresh_zoro_and_listings.py"),
               *extra_args]
        self._filter.set("All")
        self._launch(name, cmd)          # no label → console tag == "listings"
        self.btn_lst_go.config(state="disabled", fg=self.MUTED,
                               highlightbackground=self.MUTED)
        self.btn_lst_stop.config(state="normal", fg=self.RED,
                                 highlightbackground=self.RED)
        self.lbl_lst.config(text="Running", fg=self.GREEN)
        self._feed_line("Listings refresh started"
                        + (f" ({' '.join(extra_args)})" if extra_args else ""), "ok")
        self._update_hdr()

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Private label — our own ASINs, fulfilled from Zoro
    # ══════════════════════════════════════════════════════════════════════
    _PL_COLS = ("zoro_no", "zoro_title", "mpn", "pack", "landed_cost", "list_price",
                "margin", "monthly_sold", "asin", "images", "status", "sku")
    _PL_HEADINGS = {"zoro_no": "Zoro", "zoro_title": "Product", "mpn": "MPN", "pack": "Pack",
                    "landed_cost": "Cost", "list_price": "List at", "margin": "Margin",
                    "monthly_sold": "Demand/mo", "asin": "Comparable", "images": "Images",
                    "status": "Status", "sku": "SKU"}
    _PL_WIDTHS = {"zoro_no": 95, "zoro_title": 300, "mpn": 95, "pack": 50, "landed_cost": 70,
                  "list_price": 70, "margin": 70, "monthly_sold": 80, "asin": 100,
                  "images": 70, "status": 80, "sku": 170}

    def _build_page_private_label(self, container):
        page = tk.Frame(container, bg=self.BG)
        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        self._heading(pi, "PRIVATE LABEL", self.PURPLE,
                      subtitle="our own ASIN, our own brand, fulfilled from Zoro (never edits Amazon)")

        ctrl = tk.Frame(pi, bg=self.CARD)
        ctrl.pack(fill="x", padx=14, pady=(8, 6))
        self.btn_pl_score = self._btn(ctrl, "▶ Re-score Candidates", self.GREEN,
                                      self._start_pl_rescore, small=True)
        self.btn_pl_score.pack(side="left", padx=(0, 6))
        self._tip(self.btn_pl_score,
                  "Re-read everything the hunter has found and keep only what can carry OUR "
                  "label: real demand, Zoro has it in stock, the price the market bears, and — "
                  "the one that matters — Zoro ships it from its own warehouse in a plain box "
                  "(is_blind_ship).\nAn item that ships in the maker's own carton is excluded "
                  "however generic the title looks: the binders that read as unbranded carry "
                  "MPN AVE79199, i.e. Avery.\nRead-only on hunter.db, spends no Keepa tokens, "
                  "safe while the hunter and the placers are running.")
        self.btn_pl_build = self._btn(ctrl, "🏗 Build Listings", self.CYAN,
                                      self._start_pl_build, small=True)
        self.btn_pl_build.pack(side="left", padx=(0, 6))
        self._tip(self.btn_pl_build,
                  "Write a ready-to-upload listing for the top N candidates: title, five "
                  "bullets and description from Zoro's own specs, plus a main image on pure "
                  "white with the HOANK wordmark.\nLands in private_label/out_pl/<SKU>/ — "
                  "nothing is uploaded anywhere.")
        tk.Label(ctrl, text="How many:", font=(self.MONO, 9),
                 bg=self.CARD, fg=self.DIM).pack(side="left", padx=(8, 4))
        self.v_pl_limit = tk.StringVar(value="5")
        tk.Entry(ctrl, textvariable=self.v_pl_limit, width=5, font=(self.MONO, 10),
                 bg=self.INP_BG, fg=self.TEXT, insertbackground=self.TEXT,
                 relief="flat", highlightthickness=1,
                 highlightbackground=self.BORDER).pack(side="left", padx=(0, 10))
        self.btn_pl_images = self._btn(ctrl, "🎨 Make Images", self.PURPLE,
                                       self._start_pl_images, small=True)
        self.btn_pl_images.pack(side="left", padx=(0, 6))
        self._tip(self.btn_pl_images,
                  "Render the SECONDARY listing images for every listing already built: the "
                  "specification, the pack count, the real dimensions and an at-a-glance card.\n"
                  "Drawn here from the real Zoro photograph and the real feed specs — free, "
                  "offline, no watermark, and it cannot state anything the feed does not say. A "
                  "page with no data behind it is skipped rather than invented.\nThe main image "
                  "is never touched: Amazon requires a real photograph of the real product "
                  "there.\nAI scenes are available instead with a QwenCloud key — run "
                  "pl_images.py --provider qwen.")
        self.btn_pl_stop = self._btn(ctrl, "■ Stop", self.RED,
                                     lambda: self._stop("private_label"), small=True)
        self.btn_pl_stop.pack(side="left", padx=(0, 6))
        self.btn_pl_stop.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        self._btn(ctrl, "📂 Open Output", self.AMBER, self._open_pl_output, small=True
                  ).pack(side="left", padx=(0, 6))
        self._btn(ctrl, "↻ Refresh", self.CYAN, self._refresh_pl, small=True).pack(side="left")
        self.lbl_pl = tk.Label(ctrl, text="Idle", font=(self.MONO, 9),
                               bg=self.CARD, fg=self.MUTED)
        self.lbl_pl.pack(side="right")

        self._dim(pi, "Private label is the other half of the business: instead of competing on "
                      "somebody else's listing, you create the ASIN, put your own brand and "
                      "images on it, and fulfil from Zoro. Only goods that leave Zoro's own "
                      "warehouse in a plain carton can carry the label — that is what the "
                      "re-score gates on, and every product HOANK already private-labels passes "
                      "it. Results also show on the dashboard under Private label.",
                  wraplength=900, justify="left").pack(anchor="w", padx=14, pady=(2, 8))

        tw = tk.Frame(pi, bg=self.CARD)
        tw.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        tw.grid_rowconfigure(0, weight=1)
        tw.grid_columnconfigure(0, weight=1)
        self._pl_tree = ttk.Treeview(tw, columns=self._PL_COLS, show="headings",
                                     selectmode="browse")
        for c in self._PL_COLS:
            self._pl_tree.heading(c, text=self._PL_HEADINGS.get(c, c),
                                  command=lambda _c=c: self._treeview_sort_column(self._pl_tree, _c, False))
            self._pl_tree.column(c, width=self._PL_WIDTHS.get(c, 80), anchor="w", minwidth=40)
        vsb = ttk.Scrollbar(tw, orient="vertical", command=self._pl_tree.yview)
        hsb = ttk.Scrollbar(tw, orient="horizontal", command=self._pl_tree.xview)
        self._pl_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._pl_tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self._pl_tree.tag_configure("even", background=self.INP_BG)
        self._pl_tree.tag_configure("odd", background="#11131a")
        self._pl_tree.tag_configure("built", foreground=self.GREEN)
        self._pl_tree.tag_configure("dropped", foreground=self.MUTED)
        self._pl_tree.bind("<Double-1>", self._on_pl_dblclick)
        self.after(1200, self._refresh_pl)
        return page

    def _pl_script(self, *parts):
        return os.path.join(os.path.dirname(SCRIPT_DIR), *parts)

    def _start_pl_rescore(self):
        """Re-score the hunter's finds as private-label opportunities. Read-only on hunter.db."""
        if not self._pl_free():
            return
        cmd = [RUNNER_PYTHON, "-u", self._pl_script("Amazon_DS_Hunting", "pl_rescore.py")]
        self._launch_pl(cmd, "Re-scoring candidates")

    def _start_pl_build(self):
        """Write listings for the top N candidates. Produces files only — nothing is uploaded."""
        if not self._pl_free():
            return
        try:
            limit = max(1, int(self.v_pl_limit.get().strip() or "5"))
        except ValueError:
            messagebox.showwarning("Private Label", "How many: enter a number.")
            return
        cmd = [RUNNER_PYTHON, "-u",
               os.path.join(SCRIPT_DIR, "private_label", "build_pl_listings.py"),
               "--limit", str(limit)]
        self._launch_pl(cmd, f"Building {limit} listing(s)")

    def _start_pl_images(self):
        """Secondary images only — the main one stays the real photograph Amazon requires."""
        if not self._pl_free():
            return
        try:
            shots = max(1, min(int(self.v_pl_limit.get().strip() or "4"), 4))
        except ValueError:
            messagebox.showwarning("Private Label", "How many: enter a number.")
            return
        built = os.path.join(SCRIPT_DIR, "private_label", "out_pl")
        if not os.path.isdir(built) or not [d for d in os.listdir(built) if not d.startswith("_")]:
            messagebox.showinfo("Private Label", "Nothing built yet — run 🏗 Build Listings first.")
            return
        if not messagebox.askyesno(
                "Private Label",
                f"Render up to {shots} image(s) for each built listing?"):
            return
        cmd = [RUNNER_PYTHON, "-u",
               os.path.join(SCRIPT_DIR, "private_label", "pl_images.py"),
               "--all", "--limit", str(shots)]
        self._launch_pl(cmd, f"Rendering up to {shots} image(s) per listing")

    def _pl_free(self):
        if "private_label" in self._procs:
            messagebox.showinfo("Info", "A private-label run is already going.")
            return False
        return True

    def _launch_pl(self, cmd, what):
        self._launch("private_label", cmd)
        self.btn_pl_score.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        self.btn_pl_build.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        self.btn_pl_images.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        self.btn_pl_stop.config(state="normal", fg=self.RED, highlightbackground=self.RED)
        self.lbl_pl.config(text="Running", fg=self.GREEN)
        self._feed_line(f"Private label: {what}", "ok")
        self._update_hdr()

    def _open_pl_output(self):
        out = os.path.join(SCRIPT_DIR, "private_label", "out_pl")
        if not os.path.isdir(out):
            messagebox.showinfo("Private Label", "Nothing built yet — run ▶ Build Listings first.")
            return
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", out])
            elif platform.system() == "Windows":
                os.startfile(out)                                   # noqa: S606
            else:
                subprocess.Popen(["xdg-open", out])
        except Exception as e:
            messagebox.showerror("Private Label", str(e))

    def _refresh_pl(self):
        """Read the scored rows straight from the unified DB — the same table the dashboard uses,
        so the two can never disagree."""
        for i in self._pl_tree.get_children():
            self._pl_tree.delete(i)
        db = os.path.join(SCRIPT_DIR, "data", "automations_unified.db")
        try:
            c = sqlite3.connect(db, timeout=15)
            c.execute("PRAGMA query_only=ON")
            c.row_factory = sqlite3.Row
            if not c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                             "AND name='pl_candidates'").fetchone():
                self.lbl_pl.config(text="Never scored", fg=self.MUTED)
                c.close()
                return
            rows = c.execute("SELECT * FROM pl_candidates ORDER BY monthly_sold DESC").fetchall()
            c.close()
        except Exception as e:
            self.lbl_pl.config(text=f"DB error: {e}", fg=self.RED)
            return
        built = 0
        for n, r in enumerate(rows):
            st = r["status"] or "candidate"
            built += st == "built"
            cost, price = r["landed_cost"] or 0, r["list_price"] or 0
            ref = r["referral_pct"] if "referral_pct" in r.keys() else None
            ref = float(ref) if ref not in (None, "") else 0.15
            margin = price * (1 - ref) - cost if price and cost else 0
            tags = ["even" if n % 2 == 0 else "odd"]
            if st in ("built", "dropped"):
                tags.append(st)
            self._pl_tree.insert("", "end", values=(
                r["zoro_no"], (r["zoro_title"] or "")[:70], r["mpn"] or "", r["pack"] or 1,
                f"${cost:,.2f}", f"${price:,.2f}", f"${margin:,.2f}",
                f"{r['monthly_sold'] or 0:,}", r["asin"] or "",
                f"{1 if r['image_ok'] else 0}+{r['ai_images'] or 0}", st, r["sku"] or "",
            ), tags=tags)
        self.lbl_pl.config(text=f"{len(rows)} scored · {built} built",
                           fg=self.GREEN if rows else self.MUTED)

    def _on_pl_dblclick(self, _e):
        """Open the Zoro product page for the selected row — the fastest way to sanity-check
        that the photo and the pack are what the listing claims."""
        sel = self._pl_tree.selection()
        if not sel:
            return
        code = self._pl_tree.item(sel[0], "values")[0]
        webbrowser.open(f"https://www.zoro.com/product/?zoro={code}")

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Walmart → Zoro manual orders
    # ══════════════════════════════════════════════════════════════════════
    WALMART_QUEUE_PATH = os.path.join(SCRIPT_DIR, "data", "walmart_queue.json")

    def _walmart_cfg(self):
        """{'zoro_port': 9361, 'profile_dir': 'port_9361', 'sc_port': 9362,
        'sc_profile_dir': 'port_9362'} from config/accounts.json via the shared
        accounts_config loader (built-in fallback if unreadable)."""
        try:
            if SCRIPT_DIR not in sys.path:
                sys.path.insert(0, SCRIPT_DIR)
            from utils.accounts_config import walmart
            return walmart()
        except Exception:
            return {"zoro_port": 9361, "profile_dir": "port_9361",
                    "sc_port": 9362, "sc_profile_dir": "port_9362"}


    # ══════════════════════════════════════════════════════════════════════

    #  PAGE: Repricer & Competitor Opportunities
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_repricer(self, container):
        page = tk.Frame(container, bg=self.BG)
        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=8)

        self._heading(pi, "REPRICER & COMPETITOR INTELLIGENCE", self.GREEN,
                      subtitle="Profit Maximization (Raise Price) · Buy Box Reclaim (<10% Drop) · PL Hijacker Detection")

        ctrl = tk.Frame(pi, bg=self.CARD)
        ctrl.pack(fill="x", padx=14, pady=(8, 6))

        tk.Label(ctrl, text="Account:", font=(self.FONT, 10, "bold"), bg=self.CARD, fg=self.TEXT).pack(side="left", padx=(0, 4))
        self._repricer_acct_var = tk.StringVar(value="Hathoro")
        acct_combo = ttk.Combobox(ctrl, textvariable=self._repricer_acct_var, values=["Hathoro", "HOANK"], width=10, state="readonly")
        acct_combo.pack(side="left", padx=(0, 10))
        acct_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_repricer_table())

        self.btn_repricer_scrape = self._btn(ctrl, "🔄 Refresh & Scrape Live", self.GREEN,
                                             self._start_repricer_scrape, small=True)
        self.btn_repricer_scrape.pack(side="left", padx=(0, 6))
        self._tip(self.btn_repricer_scrape, "Scrapes current live competitor offers from Amazon using undetected Chrome.")

        _b_reload = self._btn(ctrl, "⚡ Reload View", self.CYAN,
                              self._refresh_repricer_table, small=True)
        _b_reload.pack(side="left", padx=(0, 6))

        _b_excel = self._btn(ctrl, "📊 Open Excel Report", self.AMBER,
                             self._open_repricer_excel, small=True)
        _b_excel.pack(side="left", padx=(0, 6))

        _b_web = self._btn(ctrl, "🌐 Open Web Repricer", self.PURPLE,
                           lambda: webbrowser.open("http://127.0.0.1:8000/#repricer"), small=True)
        _b_web.pack(side="left", padx=(0, 6))

        self.lbl_repricer_status = tk.Label(ctrl, text="Ready", font=(self.MONO, 9),
                                            bg=self.CARD, fg=self.MUTED)
        self.lbl_repricer_status.pack(side="right")

        # KPI Strip
        kpi_frame = tk.Frame(pi, bg=self.BG)
        kpi_frame.pack(fill="x", padx=14, pady=(4, 8))

        self._repricer_kpis = {}
        kpi_defs = [
            ("asins", "TOTAL ASINS", "0", self.TEXT),
            ("orders", "ORDERS · 30 D", "0", self.CYAN),
            ("raise", "🚀 RAISE OPPS", "0", self.GREEN),
            ("gain", "POTENTIAL GAIN", "$0", self.GREEN),
            ("drop", "🎯 <10% DROP OPPS", "0", self.PURPLE),
            ("hijack", "🚨 PL HIJACKED", "0", self.RED),
            ("bb_rate", "BUY BOX WIN %", "0%", self.AMBER),
        ]
        for key, title, def_val, col in kpi_defs:
            f = tk.Frame(kpi_frame, bg=self.CARD, padx=10, pady=6, highlightthickness=1, highlightbackground=self.BORDER)
            f.pack(side="left", fill="x", expand=True, padx=2)
            lbl_v = tk.Label(f, text=def_val, font=(self.FONT, 13, "bold"), bg=self.CARD, fg=col)
            lbl_v.pack(anchor="w")
            lbl_t = tk.Label(f, text=title, font=(self.FONT, 8, "bold"), bg=self.CARD, fg=self.MUTED)
            lbl_t.pack(anchor="w")
            self._repricer_kpis[key] = lbl_v

        # Strategy Filters & Search Bar
        filt_frame = tk.Frame(pi, bg=self.CARD)
        filt_frame.pack(fill="x", padx=14, pady=(0, 6))

        self._repricer_strat_var = tk.StringVar(value="ALL")
        strats = [("All", "ALL"), ("🚀 Raise Price", "RAISE_PRICE"), ("🎯 <10% Drop", "DROP_PRICE_10PCT"),
                  ("🚨 PL Hijacked", "PL_HIJACKED"), ("🛡️ PL Protected", "PL_PROTECTED"), ("🏆 Winning BB", "WINNING_BB")]
        for lbl_s, val_s in strats:
            rb = tk.Radiobutton(filt_frame, text=lbl_s, variable=self._repricer_strat_var, value=val_s,
                                bg=self.CARD, fg=self.TEXT, selectcolor=self.BG, font=(self.FONT, 9, "bold"),
                                command=self._render_repricer_rows)
            rb.pack(side="left", padx=4)

        tk.Label(filt_frame, text="🔍", bg=self.CARD, fg=self.MUTED).pack(side="right", padx=(4, 0))
        self._repricer_search_var = tk.StringVar()
        self._repricer_search_var.trace_add("write", lambda *a: self._render_repricer_rows())
        search_entry = tk.Entry(filt_frame, textvariable=self._repricer_search_var, width=22,
                                bg=self.BG, fg=self.TEXT, insertbackground=self.TEXT, relief="flat")
        search_entry.pack(side="right", padx=6, ipady=3)

        # Table Treeview
        tbl_frame = tk.Frame(pi, bg=self.CARD)
        tbl_frame.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        cols = ("asin", "sku", "strategy", "rec_price", "our_price", "zoro_cost", "profit", "low1", "low2", "sellers", "bb_win", "orders")
        self._repricer_tree = ttk.Treeview(tbl_frame, columns=cols, show="headings", height=14)

        headings = [
            ("asin", "ASIN", 100, "center"),
            ("sku", "SKU", 130, "w"),
            ("strategy", "Recommendation", 160, "w"),
            ("rec_price", "Target Price", 90, "e"),
            ("our_price", "Our Price", 85, "e"),
            ("zoro_cost", "Zoro Cost", 85, "e"),
            ("profit", "Gross Profit", 90, "e"),
            ("low1", "Lowest 1 Seller & Price", 160, "w"),
            ("low2", "Lowest 2 Seller & Price", 160, "w"),
            ("sellers", "Sellers", 60, "center"),
            ("bb_win", "In BB?", 60, "center"),
            ("orders", "Orders 30d", 80, "e"),
        ]
        for cid, title, w, align in headings:
            self._repricer_tree.heading(cid, text=title)
            self._repricer_tree.column(cid, width=w, anchor=align)

        ysb = ttk.Scrollbar(tbl_frame, orient="vertical", command=self._repricer_tree.yview)
        xsb = ttk.Scrollbar(tbl_frame, orient="horizontal", command=self._repricer_tree.xview)
        self._repricer_tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

        self._repricer_tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        tbl_frame.rowconfigure(0, weight=1)
        tbl_frame.columnconfigure(0, weight=1)

        self._repricer_tree.tag_configure("raise", background="#e8f5e9", foreground="#1b5e20")
        self._repricer_tree.tag_configure("drop", background="#e3f2fd", foreground="#0d47a1")
        self._repricer_tree.tag_configure("hijack", background="#ffebee", foreground="#b71c1c")
        self._repricer_tree.tag_configure("pl_prot", background="#e0f7fa", foreground="#006064")
        self._repricer_tree.tag_configure("zebra", background="#f9fafb")

        self._repricer_tree.bind("<Double-1>", self._on_repricer_dblclick)
        self._repricer_cached_data = None

        return page

    def _refresh_repricer_table(self):
        """Fetch repricer calculation from repricer_engine and populate tree."""
        acct = getattr(self, "_repricer_acct_var", None)
        acct_val = acct.get() if acct else "Hathoro"
        if hasattr(self, "lbl_repricer_status"):
            self.lbl_repricer_status.config(text="Loading…", fg=self.AMBER)

        try:
            d_dir = os.path.join(SCRIPT_DIR, "dashboard")
            if d_dir not in sys.path:
                sys.path.insert(0, d_dir)
            import repricer_engine as REPRICER
            res = REPRICER.analyze_repricing_for_account(account=acct_val)
            self._repricer_cached_data = res

            if hasattr(self, "_repricer_kpis"):
                self._repricer_kpis["asins"].config(text=f"{res.get('total_asins', 0):,}")
                self._repricer_kpis["orders"].config(text=f"{res.get('total_august_orders', 0):,}")
                self._repricer_kpis["raise"].config(text=f"{res.get('raise_price_count', 0):,}")
                gain = res.get('raise_price_total_potential_monthly', 0.0)
                self._repricer_kpis["gain"].config(text=f"+${gain:,.2f}/mo")
                self._repricer_kpis["drop"].config(text=f"{res.get('drop_price_count', 0):,}")
                self._repricer_kpis["hijack"].config(text=f"{res.get('pl_hijacked_count', 0):,}")
                self._repricer_kpis["bb_rate"].config(text=f"{res.get('buy_box_win_rate', 0.0):.1f}%")

            if hasattr(self, "lbl_repricer_status"):
                self.lbl_repricer_status.config(text=f"Loaded {len(res.get('items', []))} ASINs", fg=self.GREEN)

            self._render_repricer_rows()
        except Exception as e:
            if hasattr(self, "lbl_repricer_status"):
                self.lbl_repricer_status.config(text=f"Error: {e}", fg=self.RED)

    def _render_repricer_rows(self):
        """Render filtered items into treeview."""
        if not hasattr(self, "_repricer_tree") or not self._repricer_cached_data:
            return

        strat = getattr(self, "_repricer_strat_var", None)
        strat_val = strat.get() if strat else "ALL"
        q = (self._repricer_search_var.get() if hasattr(self, "_repricer_search_var") else "").lower().strip()

        self._repricer_tree.delete(*self._repricer_tree.get_children())
        items = self._repricer_cached_data.get("items", [])

        for idx, it in enumerate(items):
            if strat_val == "RAISE_PRICE" and it.get("strategy") != "RAISE_PRICE":
                continue
            if strat_val == "DROP_PRICE_10PCT" and it.get("strategy") != "DROP_PRICE_10PCT":
                continue
            if strat_val == "PL_HIJACKED" and not it.get("is_hijacked"):
                continue
            if strat_val == "PL_PROTECTED" and it.get("strategy") != "PL_PROTECTED":
                continue
            if strat_val == "WINNING_BB" and not it.get("is_our_buy_box"):
                continue

            if q:
                match = (q in (it.get("asin") or "").lower() or
                         q in (it.get("sku") or "").lower() or
                         q in (it.get("title") or "").lower() or
                         q in (it.get("lowest_1_seller") or "").lower() or
                         q in (it.get("lowest_2_seller") or "").lower())
                if not match:
                    continue

            tag = "zebra" if idx % 2 == 0 else ""
            if it.get("is_hijacked"):
                tag = "hijack"
            elif it.get("strategy") == "RAISE_PRICE":
                tag = "raise"
            elif it.get("strategy") == "DROP_PRICE_10PCT":
                tag = "drop"
            elif it.get("strategy") == "PL_PROTECTED":
                tag = "pl_prot"

            rec_p = f"${it['recommended_price']:.2f}" if it.get("recommended_price") else "—"
            our_p = f"${it['our_sale_price']:.2f}" if it.get("our_sale_price") else "—"
            zoro_c = f"${it['zoro_cost_notax']:.2f}" if it.get("zoro_cost_notax") else "—"
            prof = f"${it['gross_profit']:.2f}" if it.get("gross_profit") is not None else "—"

            low1 = f"${it['lowest_1_price']:.2f} ({it['lowest_1_seller']})" if it.get("lowest_1_price") else "—"
            low2 = f"${it['lowest_2_price']:.2f} ({it['lowest_2_seller']})" if it.get("lowest_2_price") else "—"

            bb_str = "YES" if it.get("is_our_buy_box") is True else ("NO" if it.get("is_our_buy_box") is False else "—")

            row_vals = (
                it.get("asin"),
                it.get("sku"),
                it.get("strategy_badge"),
                rec_p,
                our_p,
                zoro_c,
                prof,
                low1,
                low2,
                it.get("total_sellers", 1),
                bb_str,
                it.get("august_orders", 0),
            )
            self._repricer_tree.insert("", "end", values=row_vals, tags=(tag,))

    def _on_repricer_dblclick(self, event):
        """Open Amazon listing in browser on double click."""
        item = self._repricer_tree.identify_row(event.y)
        if not item:
            return
        vals = self._repricer_tree.item(item, "values")
        if vals and len(vals) > 0:
            asin = vals[0]
            if asin:
                webbrowser.open(f"https://www.amazon.com/dp/{asin}")

    def _start_repricer_scrape(self):
        """Launch background scraping script for the active account."""
        acct = getattr(self, "_repricer_acct_var", None)
        acct_val = acct.get() if acct else "Hathoro"
        script_name = "generate_hathoro_offers_report.py" if acct_val == "Hathoro" else "generate_hoank_offers_report.py"
        script_path = os.path.join(SCRIPT_DIR, "scripts", script_name)

        if hasattr(self, "lbl_repricer_status"):
            self.lbl_repricer_status.config(text=f"Scraping {acct_val}…", fg=self.AMBER)

        def _worker():
            try:
                subprocess.run([RUNNER_PYTHON, script_path, "--force"], check=False)
                self.after(100, self._refresh_repricer_table)
            except Exception as e:
                self.after(100, lambda: messagebox.showerror("Scraping Error", str(e)))

        import threading
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        messagebox.showinfo("Repricer Scraper Started",
                            f"Live competitor scraping started in the background for {acct_val}.\n"
                            f"The table will refresh automatically upon completion.")

    def _open_repricer_excel(self):
        """Open the formatted Excel report in default application."""
        acct = getattr(self, "_repricer_acct_var", None)
        acct_val = acct.get() if acct else "Hathoro"
        fname = "Hathoro_August_Amazon_Offers_Report.xlsx" if acct_val == "Hathoro" else "HOANK_August_Amazon_Offers_Report.xlsx"
        fpath = os.path.join(SCRIPT_DIR, fname)
        if os.path.exists(fpath):
            if platform.system() == "Darwin":
                subprocess.Popen(["open", fpath])
            elif platform.system() == "Windows":
                os.startfile(fpath)
            else:
                subprocess.Popen(["xdg-open", fpath])
        else:
            messagebox.showwarning("Report Not Found", f"Report file {fname} does not exist yet. Click 'Refresh & Scrape' first.")

    def _build_page_walmart(self, container):

        page = tk.Frame(container, bg=self.BG)

        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        self._heading(pi, "WALMART → ZORO ORDERS", self.CYAN,
                      subtitle="Seller Center scrape or manual entry — placed on Zoro "
                               "via the proven checkout engine")

        # ── PRIMARY = import the downloaded Walmart order file and place from it
        #    (no Seller Center automation → no bot wall). Scrape stays as a
        #    secondary option for when the wall isn't up.
        br = tk.Frame(pi, bg=self.CARD)
        br.pack(fill="x", padx=14, pady=(8, 6))
        self.btn_wm_import = self._btn(br, "📂 Import File & Place Orders", self.GREEN,
                                       self._start_walmart_import)
        self.btn_wm_import.pack(side="left", padx=(0, 10))
        self.btn_wm_scrape = self._btn(br, "🔄 Scrape Unshipped", self.CYAN,
                                       self._start_walmart_scrape, small=True)
        self.btn_wm_scrape.pack(side="left", padx=(0, 10))
        self.btn_wm_stop = self._btn(br, "■ Stop", self.RED, lambda: self._stop("walmart"))
        self.btn_wm_stop.pack(side="left", padx=(0, 10))
        self.btn_wm_stop.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        self.wm_dry = tk.BooleanVar()
        ttk.Checkbutton(br, text="Dry Run", variable=self.wm_dry).pack(side="left", padx=(4, 0))
        self.wm_startproc = tk.BooleanVar(value=True)
        ttk.Checkbutton(br, text="Start processing after WB",
                        variable=self.wm_startproc).pack(side="left", padx=(12, 0))
        self.lbl_wm = tk.Label(br, text="Idle", font=(self.MONO, 9),
                               bg=self.CARD, fg=self.MUTED)
        self.lbl_wm.pack(side="left", padx=(12, 0))
        self.wm_count_lbl = tk.Label(br, text="0 rows", font=(self.MONO, 9),
                                     bg=self.CARD, fg=self.DIM)
        self.wm_count_lbl.pack(side="right")

        # Secondary row: manual-entry helpers (rarely needed — scrape covers it)
        ctrl = tk.Frame(pi, bg=self.CARD)
        ctrl.pack(fill="x", padx=14, pady=(0, 6))
        self.btn_wm_run = self._btn(ctrl, "▶ Place manual rows only", self.CYAN,
                                    self._start_walmart, small=True)
        self.btn_wm_run.pack(side="left")
        self._btn(ctrl, "＋ Add Row", self.PURPLE, self._wm_add_row,
                  small=True).pack(side="left", padx=(8, 0))
        self._btn(ctrl, "↻ Refresh Status", self.ACCENT, self._refresh_walmart_status,
                  small=True).pack(side="left", padx=(8, 0))
        tk.Label(ctrl, text="First run: log into zoro.com + seller.walmart.com in the "
                 "windows that open — remembered after that.",
                 font=(self.FONT, 9), bg=self.CARD, fg=self.DIM).pack(side="left", padx=(12, 0))

        self._sep(pi)

        # ── Column headers ──────────────────────────────────────────
        hdr = tk.Frame(pi, bg=self.CARD)
        hdr.pack(fill="x", padx=14)
        for text, w in (("SKU (Zoro G-code)", 16), ("Qty", 5),
                        ("Walmart Order #", 14),
                        ("Walmart shipping address (paste)", 42), ("Status", 28)):
            tk.Label(hdr, text=text, font=(self.FONT, 9), bg=self.CARD,
                     fg=self.DIM, width=w, anchor="w").pack(side="left", padx=(0, 8))

        # ── Scrollable entry grid ───────────────────────────────────
        wrap = tk.Frame(pi, bg=self.CARD)
        wrap.pack(fill="both", expand=True, padx=14, pady=(4, 12))
        self._wm_canvas = tk.Canvas(wrap, bg=self.CARD, highlightthickness=0)
        wm_sb = ttk.Scrollbar(wrap, orient="vertical", command=self._wm_canvas.yview)
        self._wm_canvas.configure(yscrollcommand=wm_sb.set)
        wm_sb.pack(side="right", fill="y")
        self._wm_canvas.pack(side="left", fill="both", expand=True)
        self._wm_grid = tk.Frame(self._wm_canvas, bg=self.CARD)
        self._wm_grid_win = self._wm_canvas.create_window((0, 0), window=self._wm_grid, anchor="nw")
        self._wm_grid.bind(
            "<Configure>",
            lambda e: self._wm_canvas.configure(scrollregion=self._wm_canvas.bbox("all")))
        self._wm_canvas.bind(
            "<Configure>",
            lambda e: self._wm_canvas.itemconfigure(self._wm_grid_win, width=e.width))

        self._wm_rows = []
        self._wm_removed = set()
        self._wm_poll_id = None

        # Pre-fill from the persisted queue so WBs/status survive restarts
        for q in self._wm_load_queue():
            self._wm_add_row(prefill=q)
        if not self._wm_rows:
            self._wm_add_row()
        self._wm_update_count()
        return page

    def _launch_chrome_walmart(self):
        """Dedicated Zoro-account Chrome for Walmart orders (attach target for
        walmart_orders.py) — same pattern as _launch_chrome_closing."""
        cfg = self._walmart_cfg()
        port = int(cfg.get("zoro_port", 9361))
        profile_dir = os.path.expanduser(
            f"~/.closing_chrome_profiles/{cfg.get('profile_dir', f'port_{port}')}")
        os.makedirs(profile_dir, exist_ok=True)
        self._mark_profile_clean_exit(profile_dir)
        cmd = [
            _resolve_chrome_exe(),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--hide-crash-restore-bubble",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
            *CHROME_SOFT_FLAGS,
        ]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._flash_msg(f"Walmart Chrome launched on port {port} — log into zoro.com", self.GREEN)
            self._feed_line(f"Walmart Chrome launched (port {port}) — log into zoro.com "
                            "with the dedicated Walmart Zoro account", "ok")
        except Exception as e:
            self._flash_msg(f"Failed to launch Chrome: {e}", self.RED)

    def _launch_chrome_walmart_sc(self):
        """Dedicated Walmart SELLER CENTER Chrome (attach target for the
        WALMART_SCRAPE=1 scraper) — second browser, own port + profile."""
        cfg = self._walmart_cfg()
        port = int(cfg.get("sc_port", 9362))
        profile_dir = os.path.expanduser(
            f"~/.closing_chrome_profiles/{cfg.get('sc_profile_dir', f'port_{port}')}")
        os.makedirs(profile_dir, exist_ok=True)
        self._mark_profile_clean_exit(profile_dir)
        cmd = [
            _resolve_chrome_exe(),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--hide-crash-restore-bubble",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
            *CHROME_SOFT_FLAGS,
            "https://seller.walmart.com",
        ]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._flash_msg(f"Walmart SC Chrome launched on port {port} — "
                            "log into seller.walmart.com", self.GREEN)
            self._feed_line(f"Walmart SC Chrome launched (port {port}) — log into "
                            "seller.walmart.com (Seller Center)", "ok")
        except Exception as e:
            self._flash_msg(f"Failed to launch Chrome: {e}", self.RED)

    def _wm_add_row(self, prefill=None):
        import uuid
        rid = (prefill or {}).get("id") or uuid.uuid4().hex[:10]
        rf = tk.Frame(self._wm_grid, bg=self.CARD)
        rf.pack(fill="x", pady=3)
        sku_e = self._entry(rf, w=14)
        sku_e.pack(side="left", padx=(0, 8), anchor="n")
        qty_sb = tk.Spinbox(rf, from_=1, to=99, width=4, font=(self.MONO, 10),
                            bg=self.INP_BG, fg=self.TEXT, insertbackground=self.ACCENT,
                            relief="flat", bd=0, highlightthickness=1,
                            highlightbackground=self.BORDER, buttonbackground=self.CARD)
        qty_sb.pack(side="left", padx=(0, 8), anchor="n")
        po_e = self._entry(rf, w=14)
        po_e.pack(side="left", padx=(0, 8), anchor="n")
        addr_t = tk.Text(rf, height=4, width=42, font=(self.MONO, 9),
                         bg=self.INP_BG, fg=self.TEXT, insertbackground=self.ACCENT,
                         relief="flat", bd=0, highlightthickness=1,
                         highlightbackground=self.BORDER, highlightcolor=self.ACCENT)
        addr_t.pack(side="left", padx=(0, 8), anchor="n")
        status_l = tk.Label(rf, text="—", font=(self.MONO, 9), bg=self.CARD,
                            fg=self.DIM, width=28, anchor="w", justify="left",
                            wraplength=210)
        status_l.pack(side="left", padx=(0, 8), anchor="n")
        row = {"id": rid, "frame": rf, "sku": sku_e, "qty": qty_sb, "po": po_e,
               "addr": addr_t, "status": status_l, "locked": False}
        self._btn(rf, "✕", self.RED, lambda r=row: self._wm_remove_row(r),
                  small=True).pack(side="left", anchor="n")
        if prefill:
            sku_e.insert(0, prefill.get("sku", ""))
            qty_sb.delete(0, "end")
            qty_sb.insert(0, str(prefill.get("wm_qty", 1)))
            po_e.insert(0, prefill.get("walmart_po", "") or "")
            addr_t.insert("1.0", prefill.get("address_raw", ""))
            self._wm_paint_status(row, prefill)
        self._wm_rows.append(row)
        self._wm_update_count()

    def _wm_paint_status(self, row, q):
        st = (q or {}).get("status", "pending")
        wb = ((q or {}).get("wb") or "").strip()
        # earnings suffix — shown as soon as the pipeline knows the payout
        earn = (q or {}).get("earnings")
        earn_txt = f" · ${earn:.2f} earn" if isinstance(earn, (int, float)) else ""
        warn_txt = " ⚠" if (q or {}).get("warn") else ""
        if wb or st in ("placed", "processing_clicked"):
            base = wb or st
            if st == "processing_clicked":
                base = f"{wb or 'placed'} ▶proc"
            row["status"].config(text=base + earn_txt + warn_txt, fg=self.GREEN)
            if not row["locked"]:
                row["locked"] = True
                # freeze a placed row so it can't be edited or re-queued
                row["sku"].config(state="disabled")
                row["qty"].config(state="disabled")
                row["po"].config(state="disabled")
                row["addr"].config(state="disabled")
        elif st == "failed":
            row["status"].config(text=((q.get("error") or "failed")[:70] + earn_txt),
                                 fg=self.RED)
        else:
            row["status"].config(text="pending" + earn_txt + warn_txt, fg=self.AMBER)

    def _wm_remove_row(self, row):
        # Placed rows are never dropped from the queue file — only hidden.
        if not row.get("locked"):
            self._wm_removed.add(row["id"])
        row["frame"].destroy()
        if row in self._wm_rows:
            self._wm_rows.remove(row)
        self._wm_update_count()

    def _wm_load_queue(self):
        try:
            with open(self.WALMART_QUEUE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _wm_write_queue(self, rows):
        os.makedirs(os.path.dirname(self.WALMART_QUEUE_PATH), exist_ok=True)
        tmp = self.WALMART_QUEUE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
        os.replace(tmp, self.WALMART_QUEUE_PATH)

    def _wm_serialize_grid(self):
        """Serialize grid rows → queue, MERGING by row id. Rows that already
        have a WB / status placed are NEVER overwritten (idempotent money
        path); scraped extras (earnings/prices/PO metadata) on an existing
        queue row are PRESERVED. Returns (rows_out, serialized_count)."""
        existing = {q.get("id"): q for q in self._wm_load_queue() if q.get("id")}
        merged = dict(existing)
        serialized = 0
        for row in self._wm_rows:
            rid = row["id"]
            old = existing.get(rid)
            if old and ((old.get("wb") or "").strip()
                        or old.get("status") in ("placed", "processing_clicked")):
                continue
            sku = row["sku"].get().strip()
            addr = row["addr"].get("1.0", "end").strip()
            po = row["po"].get().strip()
            if not sku and not addr:
                continue  # empty row — skipped
            if not sku or not addr:
                row["status"].config(text="needs SKU + address — skipped", fg=self.AMBER)
                continue
            try:
                qty = max(1, min(99, int(row["qty"].get() or 1)))
            except (ValueError, TypeError):
                qty = 1
            base = dict(old) if old else {}   # keep scraped extras (earnings, prices…)
            base.update({
                "id": rid, "sku": sku, "wm_qty": qty, "address_raw": addr,
                "walmart_po": po,
                "order_number": base.get("order_number") or po,
                "status": "pending", "wb": "", "error": "",
                "zoro_qty": None, "parsed": None,
                "source": base.get("source") or "manual",
                "created_at": (old or {}).get("created_at")
                              or datetime.now().isoformat(timespec="seconds"),
                "placed_at": (old or {}).get("placed_at", ""),
            })
            merged[rid] = base
            row["status"].config(text="queued", fg=self.AMBER)
            serialized += 1
        # Drop rows removed in the GUI — but never placed ones
        for rid in list(self._wm_removed):
            q = merged.get(rid)
            if q and not ((q.get("wb") or "").strip()
                          or q.get("status") in ("placed", "processing_clicked")):
                merged.pop(rid, None)
        self._wm_removed.clear()
        return list(merged.values()), serialized

    def _wm_launch_runner(self, scrape, serialized, import_file=""):
        """Shared runner spawn for manual / scrape / file-import."""
        busy = self._zoro_account_busy("walmart")
        if busy:
            messagebox.showwarning("Same Zoro account",
                                   f"{busy} is running on Walmart's Zoro account. Stop it first.")
            return
        dry = bool(self.wm_dry.get())
        startproc = bool(self.wm_startproc.get())
        cmd = [RUNNER_PYTHON, "-u", os.path.join(SCRIPT_DIR, "walmart_orders.py")]
        self._filter.set("Walmart")
        self._launch("walmart", cmd, "",
                     extra_env={"WALMART_DRY": "1" if dry else "",
                                "WALMART_SCRAPE": "1" if scrape else "",
                                "WALMART_IMPORT": import_file or "",
                                "WALMART_START_PROCESSING": "1" if startproc else "0"})
        self.btn_wm_run.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        self.btn_wm_scrape.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        self.btn_wm_import.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        self.btn_wm_stop.config(state="normal", fg=self.RED, highlightbackground=self.RED)
        mode = "Importing" if import_file else "Scraping" if scrape else "Running"
        self.lbl_wm.config(text=f"{mode} (dry)" if dry else mode, fg=self.GREEN)
        what = ("file " + os.path.basename(import_file)) if import_file else \
               "Seller Center scrape" if scrape else "orders"
        self._feed_line(f"Walmart {what} started ({serialized} manual row(s) queued"
                        + (", DRY" if dry else "") + ")", "ok")
        self._update_hdr()
        self._wm_schedule_poll()

    def _start_walmart_import(self):
        """📂 Import a downloaded Walmart order file and place from it — no Seller
        Center automation, so no bot wall. User picks the .xlsx/.csv they exported."""
        if "walmart" in self._procs:
            messagebox.showinfo("Info", "Walmart orders already running.")
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Choose the Walmart order export",
            initialdir=os.path.expanduser("~/Downloads"),
            filetypes=[("Order export", "*.xlsx *.xls *.csv"), ("All files", "*.*")])
        if not path:
            return
        rows_out, serialized = self._wm_serialize_grid()
        try:
            self._wm_write_queue(rows_out)
        except Exception as e:
            messagebox.showerror("Queue write failed", str(e))
            return
        self._wm_launch_runner(scrape=False, serialized=serialized, import_file=path)

    def _start_walmart(self):
        if "walmart" in self._procs:
            messagebox.showinfo("Info", "Walmart orders already running.")
            return
        rows_out, serialized = self._wm_serialize_grid()
        if not any(r.get("status") == "pending" for r in rows_out):
            messagebox.showinfo("Info", "No pending rows to run (fill in SKU + address).")
            return
        try:
            self._wm_write_queue(rows_out)
        except Exception as e:
            messagebox.showerror("Queue write failed", str(e))
            return
        self._wm_launch_runner(scrape=False, serialized=serialized)

    def _start_walmart_scrape(self):
        """Spawn the runner in Seller Center mode (WALMART_SCRAPE=1): scrape
        Unshipped → auto-build queue rows → normal placement loop. Any pending
        manual grid rows are serialized first so they run in the same pass."""
        if "walmart" in self._procs:
            messagebox.showinfo("Info", "Walmart orders already running.")
            return
        rows_out, serialized = self._wm_serialize_grid()
        try:
            self._wm_write_queue(rows_out)
        except Exception as e:
            messagebox.showerror("Queue write failed", str(e))
            return
        self._wm_launch_runner(scrape=True, serialized=serialized)

    def _wm_schedule_poll(self):
        """Auto-refresh row statuses every 5s while the runner is active."""
        if self._wm_poll_id:
            try:
                self.after_cancel(self._wm_poll_id)
            except Exception:
                pass
            self._wm_poll_id = None
        if "walmart" in self._procs:
            self._refresh_walmart_status()
            self._wm_poll_id = self.after(5000, self._wm_schedule_poll)

    def _refresh_walmart_status(self):
        queue = self._wm_load_queue()
        qmap = {q.get("id"): q for q in queue}
        seen = set()
        for row in self._wm_rows:
            seen.add(row["id"])
            q = qmap.get(row["id"])
            if q:
                self._wm_paint_status(row, q)
        # Rows the SCRAPER added to the queue (source='scraped') appear in the
        # grid automatically — unless the user removed them this session.
        for q in queue:
            if q.get("id") and q["id"] not in seen and q["id"] not in self._wm_removed:
                self._wm_add_row(prefill=q)
        self._wm_update_count()

    def _wm_update_count(self):
        try:
            queue = self._wm_load_queue()
            placed = sum(1 for q in queue
                         if (q.get("wb") or "").strip()
                         or q.get("status") in ("placed", "processing_clicked"))
            self.wm_count_lbl.config(
                text=f"{len(self._wm_rows)} rows · queue {len(queue)} ({placed} placed)")
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Price Log
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_price_log(self, container):
        page = tk.Frame(container, bg=self.BG)

        co, ci = self._panel(page)
        co.pack(fill="x", padx=8, pady=8)
        self._heading(ci, "PRICE & STOCK CHANGES", self.GREEN)

        br = tk.Frame(ci, bg=self.CARD)
        br.pack(fill="x", padx=14, pady=(4, 12))
        self._btn(br, "↻ Refresh Log", self.CYAN, self._refresh_price_log).pack(side="left")

        self._price_log_text = scrolledtext.ScrolledText(
            page, wrap="word", font=(self.MONO, 10),
            bg=self.INP_BG, fg=self.TEXT,
            relief="flat", bd=0, padx=10, pady=10,
            state="disabled")
        self._price_log_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        
        # Load initially
        self.after(1000, self._refresh_price_log)

        return page

    def _refresh_price_log(self):
        log_file = os.path.join(SCRIPT_DIR, "data", "dataset", "price_log.txt")
        self._price_log_text.config(state="normal")
        self._price_log_text.delete(1.0, tk.END)
        if os.path.exists(log_file):
            try:
                with open(log_file, "r") as f:
                    content = f.read()
                    if content.strip():
                        self._price_log_text.insert(tk.END, content)
                    else:
                        self._price_log_text.insert(tk.END, "No price changes logged yet.")
            except Exception as e:
                self._price_log_text.insert(tk.END, f"Error reading log: {e}")
        else:
            self._price_log_text.insert(tk.END, "Log file does not exist yet. Changes will appear here when SKUs change price or go OOS.")
        
        self._price_log_text.see(tk.END)
        self._price_log_text.config(state="disabled")

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Log — Order history table with filters (ALL columns)
    # ══════════════════════════════════════════════════════════════════════
    _LOG_COLS = (
        "date", "time", "amazon_id", "wb_id", "profile", "asin", "title",
        "originalsku", "aqty", "sku", "zmultiple", "zmultiplier", "zqty",
        "earnings", "shippingamt", "amazonfees",
        "subtotal", "discount", "shipping", "tax",
        "cost", "profit", "finalmargin", "estmargin",
        "coupon", "status", "payment",
        "shipname", "shipstreet", "shipcity", "shipstate", "shipzip",
        "billname", "time_sec",
    )
    _LOG_HEADINGS = {
        "date": "Amazon Order Date", "time": "Order Time", "amazon_id": "Amazon Order ID", "wb_id": "Zoro WB Order ID",
        "profile": "Bot Profile", "asin": "Amazon ASIN", "title": "Product Title", "originalsku": "Amazon Original SKU",
        "aqty": "Amazon Qty", "sku": "Zoro SKU", "zmultiple": "Zoro Multiple", "zmultiplier": "Zoro Multiplier", "zqty": "Zoro Qty",
        "earnings": "Amazon Earnings", "shippingamt": "Amazon Shipping", "amazonfees": "Amazon Fees",
        "subtotal": "Zoro Subtotal", "discount": "Zoro Discount", "shipping": "Zoro Shipping", "tax": "Zoro Tax",
        "cost": "Zoro Total Cost", "profit": "Net Profit", "finalmargin": "Final Margin %", "estmargin": "Est Margin %",
        "coupon": "Coupon Used", "status": "Zoro Order Status", "payment": "Payment Method",
        "shipname": "Ship To Name", "shipstreet": "Ship To Street", "shipcity": "Ship To City",
        "shipstate": "Ship To State", "shipzip": "Ship To ZIP", "billname": "Bill To Name", "time_sec": "Checkout Time (s)",
    }
    _LOG_WIDTHS = {
        "date": 82, "time": 60, "amazon_id": 150, "wb_id": 100, "profile": 90,
        "asin": 110, "title": 180, "originalsku": 100, "aqty": 42,
        "sku": 85, "zmultiple": 52, "zmultiplier": 65, "zqty": 45,
        "earnings": 62, "shippingamt": 70, "amazonfees": 70,
        "subtotal": 62, "discount": 62, "shipping": 62, "tax": 50,
        "cost": 62, "profit": 62, "finalmargin": 80, "estmargin": 70,
        "coupon": 85, "status": 55, "payment": 110,
        "shipname": 90, "shipstreet": 140, "shipcity": 90, "shipstate": 45, "shipzip": 75,
        "billname": 90, "time_sec": 55,
    }

    # ══════════════════════════════════════════════════════════════════════
    #  SESSION LOGS page — every process stream, categorized + searchable.
    #  History covers the WHOLE launcher session (150k lines); the permanent
    #  cross-session record is data/console_log.txt (written on every line).
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_logs(self, container):
        page = tk.Frame(container, bg=self.BG)
        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        self._heading(pi, "SESSION LOGS", self.GREEN)

        fb = tk.Frame(pi, bg=self.CARD)
        fb.pack(fill="x", padx=14, pady=(8, 6))

        self._dim(fb, "Stream").pack(side="left")
        self._logs_cat = self._combo(fb, ["All"], 20)
        self._logs_cat.set("All")
        self._logs_cat.pack(side="left", padx=(4, 14))
        self._logs_cat.bind("<<ComboboxSelected>>", lambda *_: self._refresh_logs_view())

        self._dim(fb, "Search").pack(side="left")
        self._logs_search = tk.StringVar(value="")
        se = tk.Entry(fb, textvariable=self._logs_search, width=28,
                      bg=self.INP_BG, fg=self.TEXT, insertbackground=self.ACCENT,
                      relief="flat", font=(self.MONO, 10))
        se.pack(side="left", padx=(4, 6), ipady=3)
        self._logs_search.trace_add("write", lambda *_: self._schedule_logs_refresh())

        self._logs_count = tk.Label(fb, text="", font=(self.MONO, 9),
                                    bg=self.CARD, fg=self.MUTED)
        self._logs_count.pack(side="left", padx=(6, 0))

        self._logs_follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(fb, text="Follow", variable=self._logs_follow).pack(side="right", padx=4)
        self._btn(fb, "⬇ End", self.CYAN,
                  lambda: self._logs_text.see("end"), small=True).pack(side="right", padx=4)
        self._btn(fb, "Open log file", self.DIM, self._open_console_log_file,
                  small=True).pack(side="right", padx=4)

        tf = tk.Frame(pi, bg=self.INP_BG)
        tf.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self._logs_text = scrolledtext.ScrolledText(
            tf, wrap="word", font=(self.MONO, 10),
            bg=self.INP_BG, fg=self.TEXT, insertbackground=self.ACCENT,
            selectbackground="#33467c", selectforeground=self.TEXT,
            relief="flat", bd=0, padx=8, pady=6, state="disabled")
        self._logs_text.pack(fill="both", expand=True)
        for tagname, opts in (("ok", dict(foreground=self.GREEN)),
                              ("err", dict(foreground=self.RED)),
                              ("warn", dict(foreground=self.AMBER)),
                              ("info", dict(foreground=self.CYAN)),
                              ("time", dict(foreground=self.MUTED)),
                              ("src", dict(foreground=self.PURPLE)),
                              ("hit", dict(background="#5a4a00")),
                              ("header", dict(foreground=self.ACCENT)),
                              ("dash", dict(foreground=self.PURPLE)),
                              ("profit", dict(foreground=self.AMBER)),
                              ("wb", dict(foreground=self.GREEN)),
                              ("dim", dict(foreground=self.DIM))):
            self._logs_text.tag_configure(tagname, **opts)
        self._logs_refresh_job = None
        return page

    def _schedule_logs_refresh(self):
        # debounce typing in the search box
        if getattr(self, "_logs_refresh_job", None):
            try:
                self.after_cancel(self._logs_refresh_job)
            except Exception:
                pass
        self._logs_refresh_job = self.after(250, self._refresh_logs_view)

    def _logs_matches(self, source, needle, text):
        cat = str(self._logs_cat.get() or "All")
        if cat != "All" and source != cat:
            return False
        if needle and needle not in text.lower():
            return False
        return True

    def _refresh_logs_view(self):
        if not hasattr(self, "_logs_text"):
            return
        # keep the stream list current
        seen = sorted({src for _, _, src in self._console_history if src})
        vals = ["All"] + seen
        if list(self._logs_cat.cget("values")) != vals:
            cur = self._logs_cat.get()
            self._logs_cat.configure(values=vals)
            if cur not in vals:
                self._logs_cat.set("All")
        needle = (self._logs_search.get() or "").strip().lower()
        matches = [e for e in self._console_history
                   if self._logs_matches(e[2], needle, e[1])]
        total = len(matches)
        clipped = total > 20000
        if clipped:
            matches = matches[-20000:]     # Tk chokes past this; file has the rest
        self._logs_text.configure(state="normal")
        self._logs_text.delete("1.0", "end")
        if clipped:
            self._logs_text.insert(
                "end", f"… {total - 20000} earlier matching line(s) not rendered — "
                       f"narrow the search or open the log file …\n", "warn")
        for ts, text, source in matches:
            self._append_logs_line(ts, text, source, needle)
        self._logs_text.configure(state="disabled")
        if self._logs_follow.get():
            self._logs_text.see("end")
        self._logs_count.config(text=f"{total} match(es)")

    def _append_logs_line(self, ts, text, source, needle=""):
        self._logs_text.insert("end", f"{ts} ", "time")
        if source:
            self._logs_text.insert("end", f"[{source}] ", "src")
        tag = self._console_tag(text)
        start_idx = self._logs_text.index("end-1c")
        self._logs_text.insert("end", text + "\n", tag)
        if needle:
            lo = text.lower()
            pos = lo.find(needle)
            while pos >= 0:
                a = f"{start_idx}+{pos}c"
                b = f"{start_idx}+{pos + len(needle)}c"
                self._logs_text.tag_add("hit", a, b)
                pos = lo.find(needle, pos + 1)

    def _logs_live_append(self, ts, text, source):
        """Called on every new line: keep the Logs page current when it exists."""
        if not hasattr(self, "_logs_text"):
            return
        needle = (self._logs_search.get() or "").strip().lower()
        if not self._logs_matches(source, needle, text):
            return
        self._logs_text.configure(state="normal")
        self._append_logs_line(ts, text, source, needle)
        self._trim_text(self._logs_text, self.LOGS_LIVE_MAX)
        self._logs_text.configure(state="disabled")
        if self._in_poll:
            self._poll_logged = True     # _end_log_batch scrolls once for the whole tick
            return
        if self._logs_follow.get():
            self._logs_text.see("end")

    def _open_console_log_file(self):
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", self._console_log_path])
            elif platform.system() == "Windows":
                os.startfile(self._console_log_path)
            else:
                subprocess.Popen(["xdg-open", self._console_log_path])
        except Exception as e:
            self._flash_msg(f"Could not open log file: {e}", self.RED)

    def _build_page_log(self, container):
        page = tk.Frame(container, bg=self.BG)

        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        self._heading(pi, "ORDER LOG", self.GREEN)

        # ── Filter bar ──────────────────────────────────────────────
        fb = tk.Frame(pi, bg=self.CARD)
        fb.pack(fill="x", padx=14, pady=(8, 6))

        self._dim(fb, "ASIN").pack(side="left")
        self._log_filter_asin = self._entry(fb, w=14)
        self._log_filter_asin.pack(side="left", padx=(4, 14))

        self._dim(fb, "From").pack(side="left")
        self._log_filter_from = DateEntry(
            fb, width=11, background=self.CARD, foreground=self.TEXT,
            borderwidth=1, date_pattern='y-mm-dd',
            selectbackground=self.ACCENT, selectforeground=self.BG,
            normalbackground=self.INP_BG, normalforeground=self.TEXT,
            weekendbackground=self.INP_BG, weekendforeground=self.TEXT,
            othermonthbackground=self.INP_BG, othermonthforeground=self.DIM,
            headersbackground=self.CARD, headersforeground=self.ACCENT)
        self._log_filter_from.set_date(datetime.now() - timedelta(days=30))  # default: 30 days ago
        self._log_filter_from.pack(side="left", padx=(4, 8))

        self._dim(fb, "To").pack(side="left")
        self._log_filter_to = DateEntry(
            fb, width=11, background=self.CARD, foreground=self.TEXT,
            borderwidth=1, date_pattern='y-mm-dd',
            selectbackground=self.ACCENT, selectforeground=self.BG,
            normalbackground=self.INP_BG, normalforeground=self.TEXT,
            weekendbackground=self.INP_BG, weekendforeground=self.TEXT,
            othermonthbackground=self.INP_BG, othermonthforeground=self.DIM,
            headersbackground=self.CARD, headersforeground=self.ACCENT)
        self._log_filter_to.pack(side="left", padx=(4, 14))

        self._dim(fb, "Account").pack(side="left")
        self._log_filter_acct = self._combo(fb, ["All"] + PROFILES, 12)
        self._log_filter_acct.set("All")
        self._log_filter_acct.pack(side="left", padx=(4, 14))

        self._btn(fb, "↻ Refresh", self.CYAN, self._refresh_log_table, small=True).pack(side="left")
        self._btn(fb, "🗑 Clear Filters", self.DIM, self._clear_log_filters, small=True).pack(side="left", padx=(8, 0))

        # ── Treeview table (Excel-like with borders + zebra striping) ──
        tw = tk.Frame(pi, bg=self.BORDER, bd=1, relief="solid")
        tw.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        tw.rowconfigure(0, weight=1)
        tw.columnconfigure(0, weight=1)

        self._log_tree = ttk.Treeview(
            tw, columns=self._LOG_COLS, show="headings",
            selectmode="browse", height=24
        )
        style = ttk.Style()
        style.configure("Treeview",
                        background=self.BORDER, foreground=self.TEXT,
                        fieldbackground=self.BORDER, rowheight=32,
                        font=(self.FONT, 10))
        style.configure("Treeview.Heading",
                        background=self.CARD, foreground=self.ACCENT,
                        font=(self.FONT, 10, "bold"), relief="flat", borderwidth=1)
        style.map("Treeview",
                  background=[("selected", "#33467c")],
                  foreground=[("selected", self.TEXT)])

        for c in self._LOG_COLS:
            self._log_tree.heading(c, text=self._LOG_HEADINGS.get(c, c),
                                   command=lambda _c=c: self._treeview_sort_column(self._log_tree, _c, False))
            self._log_tree.column(c, width=self._LOG_WIDTHS.get(c, 80), anchor="w", minwidth=35)

        vsb = ttk.Scrollbar(tw, orient="vertical", command=self._log_tree.yview)
        hsb = ttk.Scrollbar(tw, orient="horizontal", command=self._log_tree.xview)
        self._log_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._log_tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # Zebra striping and conditional tags
        # The order of configuration is important: last defined overrides previous if applied together.
        self._log_tree.tag_configure("even", background=self.INP_BG)
        self._log_tree.tag_configure("odd",  background="#11131a")
        self._log_tree.tag_configure("profit_pos", foreground=self.GREEN)
        self._log_tree.tag_configure("profit_neg", foreground=self.RED)
        self._log_tree.tag_configure("margin_low", background=self.RED, foreground="white")

        self._log_tree.bind("<Double-1>", self._on_tree_double_click)

        self._log_filter_asin.bind("<Return>", lambda e: self._refresh_log_table())
        self._log_filter_acct.bind("<<ComboboxSelected>>", lambda e: self._refresh_log_table())

        self.after(1500, self._refresh_log_table)
        return page

    def _on_tree_double_click(self, event):
        tree = event.widget
        region = tree.identify("region", event.x, event.y)
        if region == "cell":
            column = tree.identify_column(event.x)
            col_idx = int(column[1:]) - 1
            item = tree.identify_row(event.y)
            if item:
                val = tree.item(item, "values")[col_idx]
                if val:
                    self.clipboard_clear()
                    self.clipboard_append(val)
                    self.update()
                    self._flash_msg(f"Copied: {val}", self.GREEN)

    def _clear_log_filters(self):
        self._log_filter_asin.delete(0, tk.END)
        self._log_filter_from.set_date(datetime.now() - timedelta(days=30))
        self._log_filter_to.set_date(datetime.now())
        self._log_filter_acct.set("All")
        self._refresh_log_table()

    def _treeview_sort_column(self, tv, col, reverse):
        l = [(tv.set(k, col), k) for k in tv.get_children('')]
        try:
            l.sort(key=lambda t: float(re.sub(r'[^\d.-]', '', t[0])) if t[0] and re.search(r'\d', t[0]) else 0, reverse=reverse)
        except Exception:
            l.sort(reverse=reverse)
        for index, (val, k) in enumerate(l):
            tv.move(k, '', index)
        for idx, item in enumerate(tv.get_children('')):
            tags = list(tv.item(item, "tags"))
            if "even" in tags: tags.remove("even")
            if "odd" in tags: tags.remove("odd")
            # Insert stripe at index 0 so condition tags win
            tags.insert(0, "even" if idx % 2 == 0 else "odd")
            tv.item(item, tags=tags)
        tv.heading(col, command=lambda: self._treeview_sort_column(tv, col, not reverse))

    def _refresh_log_table(self):
        """Parse order_log.txt and populate the Treeview with filtered rows."""
        log_path = os.path.join(SCRIPT_DIR, "data", "rajar_logs", "order_log.txt")
        rows = []
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                rows = self._parse_order_log(content)
            except Exception as e:
                print(f"[Log Tab] Error reading order log: {e}")

        # Apply filters
        asin_f = (self._log_filter_asin.get() or "").strip().upper()
        from_f = self._log_filter_from.get_date().strftime("%Y-%m-%d")
        to_f   = self._log_filter_to.get_date().strftime("%Y-%m-%d")
        acct_f = (self._log_filter_acct.get() or "All").strip()

        filtered = []
        for r in rows:
            if asin_f and asin_f not in (r.get("asin") or "").upper():
                continue
            if from_f and (r.get("date") or "") < from_f:
                continue
            if to_f and (r.get("date") or "") > to_f:
                continue
            if acct_f != "All" and acct_f != (r.get("profile") or ""):
                continue
            filtered.append(r)

        for item in self._log_tree.get_children():
            self._log_tree.delete(item)
        for idx, r in enumerate(filtered):
            vals = tuple(r.get(c, "") for c in self._LOG_COLS)
            profit_raw = (r.get("profit") or "").replace("$", "").replace("+", "").replace(",", "").strip()
            margin_raw = (r.get("finalmargin") or "").replace("%", "").strip()
            profit_tag = ""
            
            if margin_raw and "-" not in margin_raw and margin_raw != "N/A":
                try:
                    m_val = float(margin_raw)
                    if m_val < 5.0:
                        profit_tag = "margin_low"
                    else:
                        profit_tag = "profit_pos"
                except ValueError:
                    profit_tag = "profit_pos"
            elif profit_raw and "-" in profit_raw:
                profit_tag = "profit_neg"

            stripe_tag = "even" if idx % 2 == 0 else "odd"
            
            # Pack tags so that margin/profit overrides stripe
            tags = [stripe_tag]
            if profit_tag:
                tags.append(profit_tag)
                
            self._log_tree.insert("", tk.END, values=vals, tags=tuple(tags))

    # ── Log-tab auto-refresh (watches file mtime) ──────────────────────────
    def _start_log_watcher(self):
        self._stop_log_watcher()
        self._watch_log_file()

    def _stop_log_watcher(self):
        if self._log_watch_id is not None:
            self.after_cancel(self._log_watch_id)
            self._log_watch_id = None

    def _watch_log_file(self):
        """Check if order_log.txt changed and refresh the table if so."""
        try:
            log_path = os.path.join(SCRIPT_DIR, "data", "rajar_logs", "order_log.txt")
            if os.path.exists(log_path):
                mtime = os.path.getmtime(log_path)
                if mtime != self._log_file_mtime:
                    self._log_file_mtime = mtime
                    self._refresh_log_table()
        except Exception:
            pass
        self._log_watch_id = self.after(3000, self._watch_log_file)

    def _parse_order_log(self, content):
        """Parse order_log.txt — handles new one-liner, old compact, and old audit formats."""
        entries = []

        # ── NEW one-liner format ──
        # Robust: some older entries were written without a trailing newline, so
        # splitlines() merges them. Use regex to isolate each entry by its header.
        one_liner_pattern = r'\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \| WB:.+?(?=\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \| WB:|\Z)'
        for raw in re.findall(one_liner_pattern, content, re.DOTALL):
            raw = raw.strip()
            m = re.match(r'^\[([^\]]+)\]\s+\|\s+(.+)', raw)
            if not m:
                continue
            dt_str, rest = m.groups()
            parts = rest.split(" | ")
            entry = {}
            dt_parts = dt_str.strip().split()
            entry["date"] = dt_parts[0] if dt_parts else ""
            entry["time"] = dt_parts[1] if len(dt_parts) > 1 else ""
            for part in parts:
                part = part.strip()
                if ":" in part:
                    key, val = part.split(":", 1)
                    key = key.strip().lower()
                    # The log line has "Time: 22.1s" for duration.
                    # Don't overwrite the HH:MM:SS timestamp we already stored.
                    if key == "time":
                        key = "time_sec"
                    entry[key] = val.strip()
            key_map = {
                "wb": "wb_id",
                "amazon": "amazon_id", "margin": "finalmargin",
            }
            normalized = {key_map.get(k, k): v for k, v in entry.items()}
            entries.append(normalized)

        # If regex found entries, skip legacy line-by-line scanning.
        if entries:
            return entries

        lines = content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # ── OLD compact format (legacy, only first entry in old logs) ──
            if line.startswith("[") and "Profile:" in line:
                m = re.match(
                    r'^\[(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\]\s+(WB\d+)\s+\|\s+Profile:([^|]+)\|\s+Amazon:([^|]*)\|\s+ASIN:([^|]+)\|\s+SKU:([^|]+)\|\s+Earnings:([^|]+)\|\s+Zoro:([^|]+)\|\s+Profit:([^|]+)\|\s+Coupon:([^|]+)\|\s+(?:Items:[^|]+\|\s+)?Ship:([^|]+)\|\s+Time:([^|]+)$',
                    line)
                if m:
                    date, time, wb_id, profile, amazon_id, asin, sku, earnings, cost, profit_margin, coupon, ship_to, timing = m.groups()
                    profit, margin = "", ""
                    if "(" in profit_margin:
                        profit = profit_margin.split("(")[0].strip()
                        margin = profit_margin.split("(")[1].replace(")", "").strip()
                    entries.append({
                        "date": date.strip(), "time": time.strip(),
                        "wb_id": wb_id.strip(), "profile": profile.strip(),
                        "amazon_id": amazon_id.strip(), "asin": asin.strip(),
                        "sku": sku.strip(), "earnings": earnings.strip(),
                        "cost": cost.strip(), "profit": profit, "finalmargin": margin,
                        "coupon": coupon.strip(),
                    })
                    i += 1
                    continue

            # ── OLD audit block format (legacy entries) ──
            m2 = re.match(r'ORDER AUDIT \| (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) \| (WB\d+) \| Profile:\s*(.+)', line)
            if m2:
                date, time, wb_id, profile = m2.groups()
                entry = {
                    "date": date.strip(), "time": time.strip(),
                    "wb_id": wb_id.strip(), "profile": profile.strip(),
                }
                i += 1
                while i < len(lines):
                    sub = lines[i]
                    if sub.startswith("ORDER AUDIT"):
                        break
                    if "Amazon Order ID" in sub and ":" in sub:
                        entry["amazon_id"] = sub.split(":", 1)[1].strip()
                    elif "ASIN" in sub and ":" in sub and "Existing" not in sub:
                        entry["asin"] = sub.split(":", 1)[1].strip()
                    elif "Zoro SKU" in sub and ":" in sub:
                        entry["sku"] = sub.split(":", 1)[1].strip()
                    elif "Amazon Earnings" in sub and ":" in sub:
                        entry["earnings"] = sub.split(":", 1)[1].strip()
                    elif "TOTAL PAID" in sub and ":" in sub:
                        entry["cost"] = sub.split(":", 1)[1].strip()
                    elif "Final Profit" in sub and ":" in sub:
                        pm = sub.split(":", 1)[1].strip()
                        if "(" in pm:
                            entry["profit"] = pm.split("(")[0].strip()
                            entry["finalmargin"] = pm.split("(")[1].replace(")", "").strip()
                        else:
                            entry["profit"] = pm
                    elif "Coupon Used" in sub and ":" in sub:
                        entry["coupon"] = sub.split(":", 1)[1].strip()
                    elif "Name    :" in sub:
                        name_part = sub.split(":", 1)[1].strip()
                        if name_part:
                            entry["shipname"] = name_part
                    elif "City    :" in sub and entry.get("shipname"):
                        city_part = sub.split(":", 1)[1].strip()
                        entry["shipcity"] = city_part
                    elif "State   :" in sub and entry.get("shipname"):
                        entry["shipstate"] = sub.split(":", 1)[1].strip()
                    elif "ZIP     :" in sub and entry.get("shipname"):
                        entry["shipzip"] = sub.split(":", 1)[1].strip()
                    i += 1
                entries.append(entry)
                continue

            i += 1
        return entries

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Loss Orders — orders skipped due to LOW_PROFIT gate
    # ══════════════════════════════════════════════════════════════════════
    _LOSS_COLS = (
        "date", "time", "amazon_id", "wb_id", "profile", "asin", "title",
        "originalsku", "aqty", "sku", "zmultiple", "zmultiplier", "zqty",
        "earnings", "shippingamt", "amazonfees",
        "subtotal", "discount", "shipping", "tax",
        "cost", "profit", "finalmargin", "estmargin",
        "coupon", "status", "payment",
        "shipname", "shipstreet", "shipcity", "shipstate", "shipzip",
        "billname", "time_sec",
    )

    def _build_page_loss_orders(self, container):
        page = tk.Frame(container, bg=self.BG)

        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        self._heading(pi, "SKIPPED ORDERS (LOW PROFIT)", self.RED)

        # ── Filter bar ──────────────────────────────────────────────
        fb = tk.Frame(pi, bg=self.CARD)
        fb.pack(fill="x", padx=14, pady=(8, 6))

        self._dim(fb, "ASIN").pack(side="left")
        self._loss_filter_asin = self._entry(fb, w=14)
        self._loss_filter_asin.pack(side="left", padx=(4, 14))

        self._dim(fb, "From").pack(side="left")
        self._loss_filter_from = DateEntry(
            fb, width=11, background=self.CARD, foreground=self.TEXT,
            borderwidth=1, date_pattern='y-mm-dd',
            selectbackground=self.ACCENT, selectforeground=self.BG,
            normalbackground=self.INP_BG, normalforeground=self.TEXT,
            weekendbackground=self.INP_BG, weekendforeground=self.TEXT,
            othermonthbackground=self.INP_BG, othermonthforeground=self.DIM,
            headersbackground=self.CARD, headersforeground=self.ACCENT)
        self._loss_filter_from.set_date(datetime.now() - timedelta(days=30))
        self._loss_filter_from.pack(side="left", padx=(4, 8))

        self._dim(fb, "To").pack(side="left")
        self._loss_filter_to = DateEntry(
            fb, width=11, background=self.CARD, foreground=self.TEXT,
            borderwidth=1, date_pattern='y-mm-dd',
            selectbackground=self.ACCENT, selectforeground=self.BG,
            normalbackground=self.INP_BG, normalforeground=self.TEXT,
            weekendbackground=self.INP_BG, weekendforeground=self.TEXT,
            othermonthbackground=self.INP_BG, othermonthforeground=self.DIM,
            headersbackground=self.CARD, headersforeground=self.ACCENT)
        self._loss_filter_to.pack(side="left", padx=(4, 14))

        self._dim(fb, "Account").pack(side="left")
        self._loss_filter_acct = self._combo(fb, ["All"] + PROFILES, 12)
        self._loss_filter_acct.set("All")
        self._loss_filter_acct.pack(side="left", padx=(4, 14))

        self._btn(fb, "↻ Refresh", self.CYAN, self._refresh_loss_orders_table, small=True).pack(side="left")
        self._btn(fb, "🗑 Clear Filters", self.DIM, self._clear_loss_filters, small=True).pack(side="left", padx=(8, 0))

        # ── Treeview table ───────────────────────────────────────────
        tw = tk.Frame(pi, bg=self.BORDER, bd=1, relief="solid")
        tw.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        tw.rowconfigure(0, weight=1)
        tw.columnconfigure(0, weight=1)

        self._loss_tree = ttk.Treeview(
            tw, columns=self._LOSS_COLS, show="headings",
            selectmode="browse", height=24
        )
        style = ttk.Style()
        style.configure("Treeview",
                        background=self.BORDER, foreground=self.TEXT,
                        fieldbackground=self.BORDER, rowheight=32,
                        font=(self.FONT, 10))
        style.configure("Treeview.Heading",
                        background=self.CARD, foreground=self.ACCENT,
                        font=(self.FONT, 10, "bold"), relief="flat", borderwidth=1)
        style.map("Treeview",
                  background=[("selected", "#33467c")],
                  foreground=[("selected", self.TEXT)])

        for c in self._LOSS_COLS:
            self._loss_tree.heading(c, text=self._LOG_HEADINGS.get(c, c),
                                    command=lambda _c=c: self._treeview_sort_column(self._loss_tree, _c, False))
            self._loss_tree.column(c, width=self._LOG_WIDTHS.get(c, 80), anchor="w", minwidth=35)

        vsb = ttk.Scrollbar(tw, orient="vertical", command=self._loss_tree.yview)
        hsb = ttk.Scrollbar(tw, orient="horizontal", command=self._loss_tree.xview)
        self._loss_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._loss_tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._loss_tree.tag_configure("even", background=self.INP_BG)
        self._loss_tree.tag_configure("odd",  background="#11131a")
        self._loss_tree.tag_configure("profit_neg", foreground=self.RED)
        self._loss_tree.tag_configure("margin_low", background=self.RED, foreground="white")

        self._loss_tree.bind("<Double-1>", self._on_tree_double_click)
        self._loss_filter_asin.bind("<Return>", lambda e: self._refresh_loss_orders_table())
        self._loss_filter_acct.bind("<<ComboboxSelected>>", lambda e: self._refresh_loss_orders_table())

        self.after(1500, self._refresh_loss_orders_table)
        return page

    def _clear_loss_filters(self):
        self._loss_filter_asin.delete(0, tk.END)
        self._loss_filter_from.set_date(datetime.now() - timedelta(days=30))
        self._loss_filter_to.set_date(datetime.now())
        self._loss_filter_acct.set("All")
        self._refresh_loss_orders_table()

    def _refresh_loss_orders_table(self):
        """Parse order_log.txt and show only orders skipped by LOW_PROFIT gate."""
        log_path = os.path.join(SCRIPT_DIR, "data", "rajar_logs", "order_log.txt")
        rows = []
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                rows = self._parse_order_log(content)
            except Exception as e:
                print(f"[Loss Tab] Error reading order log: {e}")

        # Apply filters
        asin_f = (self._loss_filter_asin.get() or "").strip().upper()
        from_f = self._loss_filter_from.get_date().strftime("%Y-%m-%d")
        to_f   = self._loss_filter_to.get_date().strftime("%Y-%m-%d")
        acct_f = (self._loss_filter_acct.get() or "All").strip()

        filtered = []
        for r in rows:
            # ── Loss filter: status = LOW_PROFIT (skipped by profit gate) ──
            if (r.get("status") or "").upper() != "LOW_PROFIT":
                continue

            if asin_f and asin_f not in (r.get("asin") or "").upper():
                continue
            if from_f and (r.get("date") or "") < from_f:
                continue
            if to_f and (r.get("date") or "") > to_f:
                continue
            if acct_f != "All" and acct_f != (r.get("profile") or ""):
                continue
            filtered.append(r)

        for item in self._loss_tree.get_children():
            self._loss_tree.delete(item)
        for idx, r in enumerate(filtered):
            vals = tuple(r.get(c, "") for c in self._LOSS_COLS)
            stripe_tag = "even" if idx % 2 == 0 else "odd"
            # LOW_PROFIT rows are highlighted in red
            self._loss_tree.insert("", tk.END, values=vals, tags=(stripe_tag, "profit_neg"))

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Unmapped ASINs — orders the placer skipped (ASIN not in SKU sheet)
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_unmapped(self, container):
        page = tk.Frame(container, bg=self.BG)
        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        h = self._heading(pi, "UNMAPPED ASINs — need labelling", self.RED)
        self._btn(h, "↻ Refresh", self.CYAN, self._refresh_unmapped, small=True).pack(side="right")
        self._btn(h, "📋 Copy ALL for Slack", self.GREEN,
                  lambda: self._copy_orders_for_slack(self._unmapped_page_tree, self._unmapped_page_orders,
                                                      "UNMAPPED ASINs"),
                  small=True).pack(side="right", padx=(0, 6))
        self._unmapped_page_profile = self._combo(h, ["All"] + list(PROFILES), 13)
        self._unmapped_page_profile.set("All")
        self._unmapped_page_profile.pack(side="right", padx=(0, 8))
        self._unmapped_page_profile.bind("<<ComboboxSelected>>", lambda e: self._refresh_unmapped())
        tk.Label(h, text="Profile:", font=(self.MONO, 9), bg=self.CARD, fg=self.DIM).pack(side="right", padx=(0, 4))
        self._unmapped_page_lbl = tk.Label(h, text="", font=(self.MONO, 10, "bold"),
                                           bg=self.CARD, fg=self.AMBER)
        self._unmapped_page_lbl.pack(side="right", padx=(0, 10))
        tk.Label(pi, text="These orders couldn't be placed — their ASIN isn't in the verified SKU sheet. "
                          "⚠ NEED ACTION = not in Priority_ASIN_Control → the script will never place these, "
                          "so label them (or add the ASIN to priority control). "
                          "ℹ IN PRIORITY CONTROL = already assigned → the placer auto-maps & places them over time. "
                          "Double-click an ASIN → open its unshipped orders in Seller Central to label it. "
                          "Expand an ASIN and double-click an order → open that order in SC. "
                          "Auto-updates every run (resolved ASINs drop off).",
                 font=(self.FONT, 9), bg=self.CARD, fg=self.MUTED, wraplength=900, justify="left").pack(anchor="w", padx=14, pady=(2, 8))
        tw = tk.Frame(pi, bg=self.CARD)
        tw.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._unmapped_page_tree = ttk.Treeview(tw, show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(tw, orient="vertical", command=self._unmapped_page_tree.yview)
        self._unmapped_page_tree.configure(yscrollcommand=vsb.set)
        self._unmapped_page_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._unmapped_page_tree.bind("<Double-1>", self._on_unmapped_dblclick)
        self._unmapped_page_orders = {}
        return page

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Needs Priority — unlabelled orders whose ASIN isn't in priority control
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_needs_priority(self, container):
        page = tk.Frame(container, bg=self.BG)
        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        h = self._heading(pi, "NEEDS PRIORITY — unlabelled orders, ASIN not in priority control", self.AMBER)
        self._btn(h, "↻ Refresh", self.CYAN, self._refresh_needs_priority, small=True).pack(side="right")
        self._btn(h, "📋 Copy ALL for Slack", self.GREEN,
                  lambda: self._copy_orders_for_slack(self._needs_priority_tree, self._needs_priority_orders,
                                                      "NEEDS PRIORITY"),
                  small=True).pack(side="right", padx=(0, 6))
        self._needs_priority_profile = self._combo(h, ["All"] + list(PROFILES), 13)
        self._needs_priority_profile.set("All")
        self._needs_priority_profile.pack(side="right", padx=(0, 8))
        self._needs_priority_profile.bind("<<ComboboxSelected>>", lambda e: self._refresh_needs_priority())
        tk.Label(h, text="Profile:", font=(self.MONO, 9), bg=self.CARD, fg=self.DIM).pack(side="right", padx=(0, 4))
        self._needs_priority_lbl = tk.Label(h, text="", font=(self.MONO, 10, "bold"),
                                             bg=self.CARD, fg=self.AMBER)
        self._needs_priority_lbl.pack(side="right", padx=(0, 10))
        tk.Label(pi, text="These orders are UNLABELLED and their ASIN isn't in Priority_ASIN_Control, so the "
                          "placer skips them (they never get placed). To fix: add the ASIN to priority control, "
                          "or label the orders. Double-click an ASIN → open its unshipped orders in Seller "
                          "Central. Expand an ASIN and double-click an order → open that order in SC. "
                          "Auto-updates every run (resolved ASINs drop off).",
                 font=(self.FONT, 9), bg=self.CARD, fg=self.MUTED, wraplength=900, justify="left").pack(anchor="w", padx=14, pady=(2, 8))
        tw = tk.Frame(pi, bg=self.CARD)
        tw.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._needs_priority_tree = ttk.Treeview(tw, show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(tw, orient="vertical", command=self._needs_priority_tree.yview)
        self._needs_priority_tree.configure(yscrollcommand=vsb.set)
        self._needs_priority_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._needs_priority_tree.bind("<Double-1>", self._on_needs_priority_dblclick)
        self._needs_priority_orders = {}
        return page

    def _run_started_epoch(self, path):
        """Epoch of a run's START time, parsed from its filename run_YYYY-MM-DD_HHMMSS.csv."""
        m = re.search(r'run_(\d{4}-\d{2}-\d{2}_\d{6})', os.path.basename(path))
        if not m:
            return None
        try:
            return time.mktime(time.strptime(m.group(1), "%Y-%m-%d_%H%M%S"))
        except Exception:
            return None

    def _latest_status_by_order(self):
        """{amazon_id: {s, asin, buyer, state, profile}} — latest status per order across the
        CURRENTLY-ACTIVE placer run logs only: run_*.csv files WRITTEN within the last
        LIVE_WINDOW_S seconds. This makes the attention panels a true LIVE view (only what the
        running placer is producing now) and — because the dashboard uses the identical mtime rule
        (LIVE_WINDOW_S in dashboard/server.py) — the GUI and dashboard always show the SAME set.
        Old/finished runs age out automatically. Profile comes from the sibling run_<ts>.json."""
        rajar = os.path.join(SCRIPT_DIR, "data", "rajar_logs")
        cutoff = time.time() - LIVE_WINDOW_S
        latest = {}
        for f in sorted(glob.glob(os.path.join(rajar, "run_*.csv"))):
            try:
                if os.path.getmtime(f) < cutoff:
                    continue   # only actively-written runs → live view, matches the dashboard
            except OSError:
                continue
            prof = ""
            try:
                prof = (json.load(open(f[:-4] + ".json", encoding="utf-8")).get("profile") or "").strip()
            except Exception:
                prof = ""
            try:
                for r in csv.DictReader(open(f, encoding="utf-8", errors="replace")):
                    aid = (r.get("Amazon_ID") or "").strip()
                    if aid:
                        latest[aid] = {"s": (r.get("Status") or "").strip(),
                                       "asin": (r.get("ASIN") or "").strip(),
                                       "buyer": (r.get("Buyer") or "").strip(),
                                       "state": (r.get("State") or "").strip(),
                                       "profile": prof}
            except Exception:
                continue
        return latest

    def _refresh_needs_priority(self):
        """Rebuild the NEEDS-PRIORITY tree from placer run logs (data/rajar_logs/run_*.csv):
        unlabelled orders whose ASIN isn't in Priority ASIN Control (Status=NEEDS_PRIORITY),
        latest-status-per-order → the CURRENT set. ASIN × N parents, order children.
        Double-click an order → Seller Central. Self-clears once the ASIN is added to priority
        (or the order is labelled) and the placer re-runs — that order then places (SUCCESS) or
        stops being re-logged, so its latest status is no longer NEEDS_PRIORITY."""
        t = getattr(self, "_needs_priority_tree", None)
        if t is None:
            return
        sel = "All"
        combo = getattr(self, "_needs_priority_profile", None)
        if combo is not None:
            try:
                sel = (combo.get() or "All").strip()
            except Exception:
                sel = "All"
        latest = self._latest_status_by_order()
        groups = {}
        for aid, d in latest.items():
            if d["s"] == "NEEDS_PRIORITY" and (sel == "All" or d.get("profile", "") == sel):
                groups.setdefault(d["asin"] or "(no ASIN)", []).append((aid, d["buyer"], d["state"]))
        n_orders = sum(len(v) for v in groups.values())
        lbl = getattr(self, "_needs_priority_lbl", None)
        if lbl:
            txt = f"⚠ {n_orders} orders · {len(groups)} ASINs to prioritise or label"
            if sel != "All":
                txt += f"      · filter: {sel}"
            lbl.config(text=txt)
        t.delete(*t.get_children())
        omap = {}
        for asin, orders in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            parent = t.insert("", "end", text=f"{asin}   ×{len(orders)}", open=False)
            for aid, buyer, state in orders:
                child = t.insert(parent, "end", text=f"      {aid}   {buyer} ({state})   ↗ SC")
                omap[child] = aid
        self._needs_priority_orders = omap

    def _on_needs_priority_dblclick(self, event):
        tree = event.widget
        item = tree.identify_row(event.y)
        if not item:
            return
        oid = getattr(self, "_needs_priority_orders", {}).get(item)
        if oid:  # order child → open that order in Seller Central
            webbrowser.open(f"https://sellercentral.amazon.com/orders-v3/order/{oid}")
        else:  # ASIN parent → open SC unshipped-MFN search filtered to this ASIN
            asin = (tree.item(item, "text") or "").split()[0]
            if asin.startswith("B"):
                webbrowser.open("https://sellercentral.amazon.com/orders-v3/search"
                                f"?fulfillmentType=mfn&orderStatus=unshipped&page=1&q={asin}&qt=asin")

    def _copy_orders_for_slack(self, tree, omap, title="ORDERS"):
        """Copy ALL currently-shown ASINs + their orders → clipboard, as a paste-ready Slack block
        for the team to verify/label. Respects the profile filter (whatever the tree shows is copied).
        Plain Seller Central URLs auto-link when pasted into Slack. Format:

            <TITLE> — please verify & label  (N ASINs · M orders)

            ASIN: <asin>   (<n> orders)
            Order 1: https://sellercentral.amazon.com/orders-v3/order/<id>
            Order 2: ...

            ASIN: <asin2>   ...
        """
        base = "https://sellercentral.amazon.com/orders-v3/order/"
        # Collect every ASIN node (an item whose children are order-children in omap), walking past
        # any section headers (the Unmapped tree has NEED ACTION / IN PRIORITY headers above ASINs).
        asin_nodes = []

        def _walk(parent):
            for it in tree.get_children(parent):
                kids = tree.get_children(it)
                if kids and any(k in omap for k in kids):
                    asin_nodes.append(it)
                else:
                    _walk(it)
        try:
            _walk("")
        except Exception:
            asin_nodes = []

        blocks, total = [], 0
        for node in asin_nodes:
            asin = (tree.item(node, "text") or "").split()[0]
            lines = []
            for k in tree.get_children(node):
                oid = omap.get(k)
                if oid:
                    lines.append(f"Order {len(lines) + 1}: {base}{oid}")
            if lines:
                total += len(lines)
                blocks.append(f"ASIN: {asin}   ({len(lines)} orders)\n" + "\n".join(lines))

        if not blocks:
            self._flash_msg("Nothing to copy — the list is empty", self.AMBER)
            return
        text = (f"{title} — please verify & label  ({len(blocks)} ASINs · {total} orders)\n\n"
                + "\n\n".join(blocks))
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
            self._flash_msg(f"Copied ALL — {len(blocks)} ASINs · {total} orders → paste in Slack", self.GREEN)
        except Exception as e:
            self._flash_msg(f"Copy failed: {e}", self.RED)

    def _periodic_attention_refresh(self):
        """Auto-refresh whichever attention panel is currently open, so 'Needs Priority',
        'Unmapped ASINs' and 'Locked Orders' reflect the latest placer run without a manual click."""
        try:
            if self._current_page == "needs_priority":
                self._refresh_needs_priority()
            elif self._current_page == "unmapped":
                self._refresh_unmapped()
            elif self._current_page == "locked":
                self._refresh_locked_orders()
        except Exception:
            pass
        self.after(45000, self._periodic_attention_refresh)

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Locked Orders — orders held after an unknown checkout failure (review + unlock)
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_locked_orders(self, container):
        page = tk.Frame(container, bg=self.BG)
        po, pi = self._panel(page)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        h = self._heading(pi, "LOCKED ORDERS — held after an unknown checkout failure", self.RED)
        self._btn(h, "↻ Refresh", self.CYAN, self._refresh_locked_orders, small=True).pack(side="right")
        self._btn(h, "🔓 Unlock selected", self.GREEN, self._unlock_selected_locked,
                  small=True).pack(side="right", padx=(0, 6))
        self._btn(h, "🔓 Unlock ALL", self.AMBER, self._unlock_all_locked,
                  small=True).pack(side="right", padx=(0, 6))
        self._locked_lbl = tk.Label(h, text="", font=(self.MONO, 10, "bold"), bg=self.CARD, fg=self.AMBER)
        self._locked_lbl.pack(side="right", padx=(0, 10))
        tk.Label(pi, text="These orders failed checkout with an UNKNOWN error — money may have been spent but no "
                          "WB came back, so the placer is HOLDING them (won't auto-rebuy). Verify on Zoro: if it "
                          "did NOT charge, select the row and click Unlock → the placer retries it next cycle (no "
                          "restart needed). If it DID charge, paste that WB into Amazon's seller-id by hand instead. "
                          "Double-click a row → open the order in Seller Central. "
                          "(Duplicates are auto-marked '<WB> - D' and never reach here.)",
                 font=(self.FONT, 9), bg=self.CARD, fg=self.MUTED, wraplength=900, justify="left").pack(
                     anchor="w", padx=14, pady=(2, 8))
        tw = tk.Frame(pi, bg=self.CARD)
        tw.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        cols = ("order", "profile", "when", "note")
        self._locked_tree = ttk.Treeview(tw, columns=cols, show="headings", selectmode="browse")
        for c, w, t in (("order", 190, "Amazon Order"), ("profile", 110, "Profile"),
                        ("when", 150, "Locked At"), ("note", 420, "Reason")):
            self._locked_tree.heading(c, text=t)
            self._locked_tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(tw, orient="vertical", command=self._locked_tree.yview)
        self._locked_tree.configure(yscrollcommand=vsb.set)
        self._locked_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._locked_tree.bind("<Double-1>", self._on_locked_dblclick)
        return page

    def _refresh_locked_orders(self):
        """Populate the Locked Orders tree from placement_lock.locked_orders() (FAILED_KEPT claims)."""
        t = getattr(self, "_locked_tree", None)
        if t is None:
            return
        rows = []
        try:
            if SCRIPT_DIR not in sys.path:
                sys.path.insert(0, SCRIPT_DIR)
            from utils import placement_lock as plock
            rows = plock.locked_orders()
        except Exception as e:
            self._feed_line(f"Locked-orders read failed: {e}", "info")
        t.delete(*t.get_children())
        for r in rows:
            when = (r.get("reconciled_at") or r.get("claimed_at") or "")[:19].replace("T", " ")
            t.insert("", "end", values=(r.get("amazon_order_id", ""), r.get("profile", ""),
                                        when, r.get("note", "")))
        lbl = getattr(self, "_locked_lbl", None)
        if lbl:
            lbl.config(text=(f"🔒 {len(rows)} locked — verify & unlock" if rows else "none locked ✅"))

    def _unlock_selected_locked(self):
        t = getattr(self, "_locked_tree", None)
        if t is None:
            return
        sel = t.selection()
        if not sel:
            self._flash_msg("Select a locked order first, then Unlock", self.AMBER)
            return
        vals = t.item(sel[0], "values")
        oid = vals[0] if vals else ""
        if not oid:
            return
        try:
            if SCRIPT_DIR not in sys.path:
                sys.path.insert(0, SCRIPT_DIR)
            from utils import placement_lock as plock
            plock.override(oid, note="unlocked via launcher (verified safe to re-place)")
            self._flash_msg(f"Unlocked {oid} — placer retries next cycle (no restart needed)", self.GREEN)
        except Exception as e:
            self._flash_msg(f"Unlock failed: {e}", self.RED)
        self._refresh_locked_orders()

    def _unlock_all_locked(self):
        """Release EVERY held order for re-placement — after an explicit confirm,
        because each row may represent money already spent on Zoro."""
        t = getattr(self, "_locked_tree", None)
        if t is None:
            return
        oids = [t.item(i, "values")[0] for i in t.get_children()
                if t.item(i, "values") and t.item(i, "values")[0]]
        if not oids:
            self._flash_msg("Nothing locked — list is empty", self.DIM)
            return
        if not messagebox.askyesno(
                "Unlock ALL locked orders?",
                f"This releases {len(oids)} held order(s) so the placer may RE-BUY them "
                f"next cycle.\n\nOnly do this after verifying on Zoro that none of them "
                f"actually charged.\n\nUnlock all {len(oids)}?"):
            return
        done = 0
        try:
            if SCRIPT_DIR not in sys.path:
                sys.path.insert(0, SCRIPT_DIR)
            from utils import placement_lock as plock
            for oid in oids:
                plock.override(oid, note="bulk-unlocked via launcher Unlock ALL")
                done += 1
            self._flash_msg(f"Unlocked {done} order(s) — placer retries next cycle", self.GREEN)
            self._feed_line(f"🔓 Unlock ALL: released {done} held order(s)", "warn")
        except Exception as e:
            self._flash_msg(f"Unlock ALL failed after {done} of {len(oids)}: {e}", self.RED)
        self._refresh_locked_orders()

    def _on_locked_dblclick(self, event):
        t = event.widget
        item = t.identify_row(event.y)
        if not item:
            return
        vals = t.item(item, "values")
        oid = vals[0] if vals else ""
        if oid:
            webbrowser.open(f"https://sellercentral.amazon.com/orders-v3/order/{oid}")

    # ══════════════════════════════════════════════════════════════════════
    #  PAGE: Settings — scrollable, all config fields
    # ══════════════════════════════════════════════════════════════════════
    def _build_page_settings(self, container):
        page = tk.Frame(container, bg=self.BG)

        # Scrollable canvas
        canvas = tk.Canvas(page, bg=self.BG, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        scroll = tk.Frame(canvas, bg=self.BG)
        scroll.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll, anchor="nw", tags="scroll_win")
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure("scroll_win", width=e.width))

        def _mw(event):
            canvas.yview_scroll(-1 * event.delta if platform.system() == "Darwin"
                                else -1 * (event.delta // 120), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _mw))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # ── Velocity ──────────────────────────────────────────────────
        vo, vi = self._panel(scroll)
        vo.pack(fill="x", padx=8, pady=(8, 4))
        self._heading(vi, "VELOCITY & ROUTING", self.PURPLE,
                      subtitle="applies to all order profiles")
        vr = tk.Frame(vi, bg=self.CARD)
        vr.pack(fill="x", padx=14, pady=(4, 12))
        _vel_tips = {
            "v_max": "Max orders placed per cycle across a profile run. 0 = unlimited.",
            "v_delay": "Pause (seconds) between two consecutive order placements.",
            "v_sleep": "Pause (seconds) between full scrape+place cycles.",
            "v_deciding_min": "Deciding-ASIN routing threshold: an order whose Zoro cart "
                              "total (qty × price, undiscounted) is AT or ABOVE this is "
                              "labelled for the coupon profile; below it, the reseller "
                              "places it. Zoro Select items always stay with the reseller.",
            "v_pi_target": "Target profit margin % for the seller note's PI line — the "
                           "Amazon price a listing would need to hit this margin, e.g. "
                           "'PI: 41.99 -> 44.16 | 10%'. Only shown when the price must "
                           "go up. Set 12 for 12%, etc.",
        }
        for label, var_name, default, w in [
            ("Max / Cycle", "v_max", "0", 6),
            ("Delay (sec)", "v_delay", "0", 6),
            ("Sleep (sec)", "v_sleep", "120", 6),
            ("Coupon route ≥ $", "v_deciding_min", "50", 6),
            ("PI target %", "v_pi_target", "10", 6),
        ]:
            self._dim(vr, label).pack(side="left")
            sv = tk.StringVar(value=default)
            setattr(self, var_name, sv)
            self._tip(self._entry(vr, var=sv, w=w), _vel_tips[var_name])\
                .pack(side="left", padx=(4, 20))

        # ── Blocked Order IDs ─────────────────────────────────────────
        bo, bi = self._panel(scroll)
        bo.pack(fill="x", padx=8, pady=(0, 4))
        self._heading(bi, "BLOCKED ORDER IDs", self.RED, subtitle="One per line")
        self.txt_blocked = tk.Text(bi, height=4, font=(self.MONO, 10),
                                   bg=self.INP_BG, fg=self.RED,
                                   insertbackground=self.ACCENT,
                                   relief="flat", bd=0, padx=6, pady=4,
                                   highlightthickness=1,
                                   highlightbackground=self.BORDER,
                                   highlightcolor=self.ACCENT)
        self.txt_blocked.pack(fill="x", padx=14, pady=(2, 12))

        # ── Proxy ─────────────────────────────────────────────────────
        # ── Quick Actions ─────────────────────────────────────────────
        qo, qi = self._panel(scroll)
        qo.pack(fill="x", padx=8, pady=(0, 8))
        self._heading(qi, "QUICK ACTIONS", self.DIM)
        qa = tk.Frame(qi, bg=self.CARD)
        qa.pack(fill="x", padx=14, pady=(4, 12))
        self._btn(qa, "📂 Open Logs Folder", self.DIM, self._open_logs).pack(side="left", padx=(0, 12))
        self._btn(qa, "🗑  Clear Coupon Cache", self.AMBER, self._clear_coupon_cache).pack(side="left")

        return page

    # ══════════════════════════════════════════════════════════════════════
    #  CONSOLE (always visible at bottom)
    # ══════════════════════════════════════════════════════════════════════
    def _build_console(self, parent):
        co, ci = self._panel(parent)
        co.pack(fill="both", expand=True)

        hdr = tk.Frame(ci, bg=self.CARD)
        hdr.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(hdr, text="CONSOLE", font=(self.FONT, 10, "bold"),
                 bg=self.CARD, fg=self.ACCENT).pack(side="left")

        self._auto_scroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(hdr, text="Auto-scroll", variable=self._auto_scroll).pack(side="right", padx=4)

        self._filter = tk.StringVar(value="All")
        self._filter.trace_add("write", lambda *_: self._refresh_console_view())
        self.console_view = self._combo(hdr, CONSOLE_VIEW_CHOICES, 12)
        self.console_view.configure(textvariable=self._filter)
        self.console_view.pack(side="right", padx=(4, 6))
        self._dim(hdr, "View").pack(side="right")
        self._btn(hdr, "Clear", self.DIM, self._clear_console, small=True).pack(side="right", padx=(0, 8))

        cf = tk.Frame(ci, bg=self.INP_BG)
        cf.pack(fill="both", expand=True, padx=10, pady=(4, 4))

        self.console = scrolledtext.ScrolledText(
            cf, wrap="word", font=(self.MONO, 10),
            bg=self.INP_BG, fg=self.TEXT,
            insertbackground=self.ACCENT,
            selectbackground="#33467c", selectforeground=self.TEXT,
            relief="flat", bd=0, padx=8, pady=6,
            state="disabled")
        self.console.pack(fill="both", expand=True)

        ftr = tk.Frame(ci, bg=self.CARD)
        ftr.pack(fill="x", padx=10, pady=(0, 6))
        self._con_lines = tk.Label(ftr, text="0 lines", font=(self.MONO, 8),
                                   bg=self.CARD, fg=self.MUTED)
        self._con_lines.pack(side="right")

        self.console.tag_configure("ok", foreground=self.GREEN)
        self.console.tag_configure("err", foreground=self.RED)
        self.console.tag_configure("warn", foreground=self.AMBER)
        self.console.tag_configure("info", foreground=self.CYAN)
        self.console.tag_configure("time", foreground=self.MUTED)
        self.console.tag_configure("profit", foreground=self.AMBER, font=(self.MONO, 10, "bold"))
        self.console.tag_configure("wb", foreground=self.GREEN, font=(self.MONO, 10, "bold"))
        self.console.tag_configure("dash", foreground=self.PURPLE)
        self.console.tag_configure("dim", foreground=self.DIM)
        self.console.tag_configure("header", foreground=self.ACCENT, font=(self.MONO, 10, "bold"))
        # Per-source badges. Under a view that spans several processes (All / Orders /
        # Tracking / Closing) every line carries a short colour-coded tag, so all four
        # placers can be read in one stream instead of one profile at a time.
        for _src, (_lbl, _col) in self._source_badges().items():
            self.console.tag_configure(f"src::{_src}", foreground=_col,
                                       font=(self.MONO, 9, "bold"))

    # ══════════════════════════════════════════════════════════════════════
    #  CONFIG load / save
    # ══════════════════════════════════════════════════════════════════════
    def _load_config(self):
        try:
            with open(CFG_PATH, "r") as f:
                cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            cfg = DEFAULT_CFG.copy()

        vel = cfg.get("velocity", {})
        self.v_max.set(str(vel.get("max_per_cycle", 0)))
        self.v_delay.set(str(vel.get("inter_order_delay", 0)))
        self.v_sleep.set(str(vel.get("cycle_sleep", 120)))
        self.v_deciding_min.set(str(cfg.get("deciding_coupon_min", 50)))
        self.v_pi_target.set(str(cfg.get("pi_target_margin", 10)))

        tg_cfg = cfg.get("telegram", {})
        if hasattr(self, "v_tg_enabled"):
            self.v_tg_enabled.set(bool(tg_cfg.get("enabled", True)))

        wa_cfg = cfg.get("whatsapp", {})
        if hasattr(self, "v_wa_enabled"):
            self.v_wa_enabled.set(bool(wa_cfg.get("enabled", True)))

        self.txt_blocked.delete("1.0", "end")
        self.txt_blocked.insert("1.0", "\n".join(cfg.get("blocked_order_ids", [])))

        profiles = cfg.get("profiles", {})
        for pk in PROFILES:
            pd_ = profiles.get(pk, DEFAULT_CFG["profiles"].get(pk, {}))
            self._plbl[pk].set(pd_.get("target_label", ""))
            self._pmargin[pk].set(str(pd_.get("min_margin", -20)))
            self._plimit[pk].set(str(pd_.get("order_limit", 0)))
            
            if pk in self._plbl2:
                self._plbl2[pk].set(pd_.get("target_label_2", ""))
                self._pmargin2[pk].set(str(pd_.get("min_margin_2", -20)))
                
            if pk in self._pskip_priority:
                self._pskip_priority[pk].set(pd_.get("skip_priority_coupons", False))
            if pk in self._pfocus_priority_enabled:
                self._pfocus_priority_enabled[pk].set(pd_.get("focus_priority_coupon_enabled", False))
            if pk in self._pfocus_priority_code:
                self._pfocus_priority_code[pk].set(str(pd_.get("focus_priority_coupon", "FACILITY8M26")))
            if pk in self._plabels_only:
                self._plabels_only[pk].set(pd_.get("labels_only", False))
            if pk in self._prush:
                self._prush[pk].set(pd_.get("rush_mode", False))
            if pk in self._pultra:
                self._pultra[pk].set(pd_.get("ultra_mode", False))
            if pk in self._prush_batch:
                self._prush_batch[pk].set(str(pd_.get("rush_batch", 10)))
            self._pest_margin_only[pk].set(pd_.get("est_margin_only", False))

        self._flash_msg("Config loaded")

    def _save_config(self):
        blocked = [b.strip() for b in self.txt_blocked.get("1.0", "end").strip().splitlines() if b.strip()]

        profiles = {}
        for pk in PROFILES:
            try:
                margin = int(self._pmargin[pk].get().strip())
            except ValueError:
                margin = -20
            try:
                limit = int(self._plimit[pk].get().strip())
            except ValueError:
                limit = 0
            pdata = {
                "target_label": self._plbl[pk].get().strip(),
                "min_margin": margin,
                "order_limit": limit,
            }
            
            if pk in self._plbl2:
                pdata["target_label_2"] = self._plbl2[pk].get().strip()
                try:
                    pdata["min_margin_2"] = int(self._pmargin2[pk].get().strip())
                except ValueError:
                    pdata["min_margin_2"] = -20

            if pk in self._pskip_priority:
                pdata["skip_priority_coupons"] = self._pskip_priority[pk].get()
            if pk in self._pfocus_priority_enabled:
                pdata["focus_priority_coupon_enabled"] = self._pfocus_priority_enabled[pk].get()
            if pk in self._pfocus_priority_code:
                pdata["focus_priority_coupon"] = self._pfocus_priority_code[pk].get().strip()
            pdata["est_margin_only"] = self._pest_margin_only[pk].get()
            pdata["labels_only"] = self._plabels_only[pk].get()
            pdata["rush_mode"] = self._prush[pk].get()
            pdata["ultra_mode"] = self._pultra[pk].get()
            try:
                pdata["rush_batch"] = max(1, int(self._prush_batch[pk].get() or 10))
            except ValueError:
                pdata["rush_batch"] = 10

            if pk == "HS_RESELLER":
                pdata["payment_method"] = "NET30"     # always (utils.config.NET30_PROFILES)
            profiles[pk] = pdata

        try:
            deciding_min = float(self.v_deciding_min.get().strip() or 50)
        except ValueError:
            deciding_min = 50.0
        try:
            pi_target = float(self.v_pi_target.get().strip() or 10)
        except ValueError:
            pi_target = 10.0

        # MERGE, don't overwrite: keys this page doesn't edit (routing, chrome,
        # automation, priority_coupons, per-profile extras) must survive a Save.
        cfg = _read_cfg_file()
        for pk, pdata in profiles.items():
            merged = dict((cfg.get("profiles") or {}).get(pk, {}))
            merged.update(pdata)
            profiles[pk] = merged
        cfg.update({
            "profiles": profiles,
            "velocity": {
                "max_per_cycle": int(self.v_max.get() or 0),
                "inter_order_delay": int(self.v_delay.get() or 0),
                "cycle_sleep": int(self.v_sleep.get() or 120),
            },
            "blocked_order_ids": blocked,
            "deciding_coupon_min": deciding_min,
            "pi_target_margin": pi_target,
        })
        if hasattr(self, "v_tg_enabled"):
            tg = dict(cfg.get("telegram") or {})
            tg["enabled"] = self.v_tg_enabled.get()
            cfg["telegram"] = tg
        if hasattr(self, "v_wa_enabled"):
            wa = dict(cfg.get("whatsapp") or {})
            wa["enabled"] = self.v_wa_enabled.get()
            cfg["whatsapp"] = wa
        _write_cfg_file(cfg)
        self._flash_msg("✅ Config saved!", self.GREEN)

    def _open_whatsapp_senders(self):
        """Open the gateway's page, starting the gateway first if nothing has needed it yet."""
        import threading

        def go():
            import requests
            from utils.whatsapp import ensure_gateway_running
            url = "http://127.0.0.1:3005"
            ensure_gateway_running()
            for _ in range(20):
                try:
                    requests.get(f"{url}/status", timeout=1)
                    break
                except Exception:
                    time.sleep(0.5)
            webbrowser.open(url)
        threading.Thread(target=go, daemon=True).start()

    # ── Watch list window (🛑 Watch list in the bar under the header): the listings the listing guard watches ──
    # Its timers run on the root (self.after), never on the Toplevel: destroying a Toplevel deletes the Tcl
    # commands of the timers scheduled on it, and one still pending then fires into "invalid command name …"
    # (an error dialog on macOS). The ids are kept in _wl_top / _wl_poll / _wl_auto and cancelled at close.
    def _wl_alive(self):
        """The watch-list window while it exists, else None. Never raises."""
        win = getattr(self, "_wl_win", None)
        try:
            return win if win is not None and win.winfo_exists() else None
        except tk.TclError:
            return None

    def _wl_cancel(self, *names):
        """Cancel the window's pending timers (all three when no name is given)."""
        for name in names or ("_wl_top", "_wl_poll", "_wl_auto"):
            tid = getattr(self, name, None)
            setattr(self, name, None)
            if tid:
                try:
                    self.after_cancel(tid)
                except Exception:
                    pass

    def _wl_untop(self):
        self._wl_top = None
        win = self._wl_alive()
        if win is None:
            self._wl_cancel()
            return
        try:
            win.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _open_watch_list(self):
        """Show the window (built once, then raised) and read the list again."""
        win = self._wl_alive()
        if win is None:
            self._build_watch_list_window()
            win = self._wl_win
        win.deiconify()
        win.lift()
        win.attributes("-topmost", True)     # in front once, not pinned there
        self._wl_cancel("_wl_top")
        self._wl_top = self.after(1500, self._wl_untop)
        self._refresh_watch_list()

    def _build_watch_list_window(self):
        self._wl_cancel()                    # timers of an earlier window, if one was destroyed some other way
        win = tk.Toplevel(self)
        win.title("Watch list — listings out of stock at Zoro")
        win.configure(bg=self.BG)
        try:
            win.geometry(f"1320x700+{self.winfo_rootx() + 40}+{self.winfo_rooty() + 50}")
        except Exception:
            win.geometry("1320x700")
        win.minsize(900, 440)
        win.protocol("WM_DELETE_WINDOW", self._close_watch_list)
        cfg = _watch_list_settings()

        po, pi = self._panel(win)
        po.pack(fill="both", expand=True, padx=8, pady=8)
        h = self._heading(pi, "WATCH LIST — listings out of stock at Zoro", self.RED)
        self._btn(h, "↻ Refresh", self.CYAN, self._refresh_watch_list, small=True).pack(side="right")
        store = self._combo(h, ["All"] + list(STORES), 10)
        store.set("All")
        store.pack(side="right", padx=(0, 8))
        store.bind("<<ComboboxSelected>>", lambda e: self._fill_watch_list(keep_view=False))
        tk.Label(h, text="Store:", font=(self.MONO, 9), bg=self.CARD, fg=self.DIM).pack(side="right", padx=(0, 4))
        count = tk.Label(h, text="", font=(self.MONO, 10, "bold"), bg=self.CARD, fg=self.AMBER)
        count.pack(side="right", padx=(0, 10))
        intro = tk.Label(pi, text=_watch_list_intro(cfg), font=(self.FONT, 9), bg=self.CARD, fg=self.MUTED,
                         wraplength=1240, justify="left")
        intro.pack(anchor="w", padx=14, pady=(2, 8))
        detail = tk.Label(pi, text="", font=(self.FONT, 10), bg=self.CARD, fg=self.TEXT, wraplength=1240,
                          justify="left", anchor="w")
        detail.pack(side="bottom", fill="x", padx=14, pady=(0, 10))

        st = ttk.Style(self)
        st.configure("Watch.Treeview", background=self.BORDER, foreground=self.TEXT,
                     fieldbackground=self.BORDER, rowheight=26, font=(self.FONT, 10))
        st.configure("Watch.Treeview.Heading", background=self.CARD, foreground=self.ACCENT,
                     font=(self.FONT, 10, "bold"), relief="flat", borderwidth=1)
        st.map("Watch.Treeview", background=[("selected", "#33467c")], foreground=[("selected", self.TEXT)])

        # Back in stock — needs a person: Zoro has the item again, the Amazon listing was left as it was
        # (above the detail line; packed before the list so a short window squeezes the list, not this)
        bk = tk.Frame(pi, bg=self.CARD)
        bk.pack(side="bottom", fill="x", padx=12, pady=(6, 8))
        bh = tk.Frame(bk, bg=self.CARD)
        bh.pack(fill="x", padx=2, pady=(0, 4))
        tk.Label(bh, text="BACK IN STOCK — NEEDS A PERSON", font=(self.FONT, 10, "bold"), bg=self.CARD,
                 fg=self.GREEN).pack(side="left")
        back_count = tk.Label(bh, text="", font=(self.MONO, 10, "bold"), bg=self.CARD, fg=self.AMBER)
        back_count.pack(side="left", padx=(10, 0))
        tk.Label(bh, text=f"Zoro has the item again (last {_WATCH_LIST_BACK_DAYS} days), but the placer left the "
                          "Amazon listing as it was.", font=(self.FONT, 9), bg=self.CARD, fg=self.DIM).pack(
                     side="left", padx=(10, 0))
        back_none = tk.Label(bk, text="", font=(self.FONT, 10), bg=self.CARD, fg=self.DIM, anchor="w", justify="left")
        bw = tk.Frame(bk, bg=self.CARD)
        bw.columnconfigure(0, weight=1)
        back = ttk.Treeview(bw, columns=[k for k, _h, _w in _WATCH_LIST_BACK_TREE], show="headings",
                            selectmode="browse", style="Watch.Treeview", height=3)
        for k, head, w in _WATCH_LIST_BACK_TREE:
            back.heading(k, text=head)
            back.column(k, width=w, minwidth=40, anchor="w", stretch=(k == "title"))
        bsb = ttk.Scrollbar(bw, orient="vertical", command=back.yview)
        back.configure(yscrollcommand=bsb.set)
        back.grid(row=0, column=0, sticky="nsew")
        bsb.grid(row=0, column=1, sticky="ns")
        back.tag_configure("even", background=self.INP_BG)
        back.tag_configure("odd", background="#11131a")
        back.tag_configure("attention", foreground=self.AMBER)
        back.bind("<<TreeviewSelect>>", lambda e: self._on_watch_list_select(e, "back"))
        back.bind("<Double-1>", self._on_watch_list_dblclick)

        tw = tk.Frame(pi, bg=self.CARD)
        tw.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        tw.rowconfigure(0, weight=1)
        tw.columnconfigure(0, weight=1)
        tree = ttk.Treeview(tw, columns=[k for k, _h, _w in _WATCH_LIST_TREE], show="headings",
                            selectmode="browse", style="Watch.Treeview")
        for k, head, w in _WATCH_LIST_TREE:
            tree.heading(k, text=head)
            tree.column(k, width=w, minwidth=40, anchor="e" if k == "orders" else "w", stretch=(k == "title"))
        vsb = ttk.Scrollbar(tw, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(tw, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree.tag_configure("even", background=self.INP_BG)
        tree.tag_configure("odd", background="#11131a")
        tree.tag_configure("attention", foreground=self.AMBER)
        tree.bind("<<TreeviewSelect>>", self._on_watch_list_select)
        tree.bind("<Double-1>", self._on_watch_list_dblclick)

        self._wl_win, self._wl_tree, self._wl_store, self._wl_count, self._wl_detail = win, tree, store, count, detail
        self._wl_intro, self._wl_back_tree, self._wl_back_wrap, self._wl_back_none, self._wl_back_count = (
            intro, back, bw, back_none, back_count)
        self._wl_rows, self._wl_note, self._wl_back, self._wl_back_note, self._wl_cfg = [], "", [], "", cfg
        self._wl_by_item, self._wl_back_by_item, self._wl_sel, self._wl_busy = {}, {}, None, False

    def _refresh_watch_list(self):
        """Read both lists off the Tk thread (two small read-only SELECTs) and fill the window when they are
        back. While the window is open it reads again every 2 minutes."""
        win = self._wl_alive()
        if win is None:
            self._wl_cancel()
            return
        if self._wl_busy:                    # a read is on its way; it schedules the next one
            return
        import threading
        self._wl_busy = True
        self._wl_count.config(text="reading…", fg=self.DIM)
        box = {}

        def work():
            cfg = _watch_list_settings()
            try:
                res = _watch_list_rows(cfg=cfg)
            except Exception as e:               # the readers do not raise; belt and braces
                res = ([], f"⚠️ Could not read the watch list: {e}")
            try:
                back = _watch_list_back_rows()
            except Exception as e:
                back = ([], f"⚠️ Could not read the restocked listings: {e}")
            box["cfg"], box["back"] = cfg, back
            box["res"] = res                     # last: wait() takes the box when this is there

        def wait(tries=0):
            self._wl_poll = None
            if self._wl_alive() is not win:      # closed (the close cancels this timer; belt and braces)
                self._wl_busy = False
                self._wl_cancel()
                return
            if "res" not in box and tries < 150:            # up to 15 s
                self._wl_poll = self.after(100, wait, tries + 1)
                return
            self._wl_busy = False
            self._wl_rows, self._wl_note = box.get("res") or (
                [], "⚠️ The database did not answer within 15 seconds. Press Refresh to try again.")
            self._wl_back, self._wl_back_note = box.get("back") or ([], "")
            self._wl_cfg = box.get("cfg") or self._wl_cfg
            self._fill_watch_list()
            self._wl_cancel("_wl_auto")
            self._wl_auto = self.after(120000, self._refresh_watch_list)

        threading.Thread(target=work, daemon=True, name="watch-list").start()
        self._wl_cancel("_wl_poll")
        self._wl_poll = self.after(100, wait)

    def _fill_watch_list(self, keep_view=True):
        """Fill both tables from the last read. The row whose details are shown stays selected (and its details
        shown) when it is still there; keep_view keeps the tables' scroll position (a refresh), otherwise the
        tables start at the top (the store was changed)."""
        win, t, bt = self._wl_alive(), getattr(self, "_wl_tree", None), getattr(self, "_wl_back_tree", None)
        if win is None or t is None or bt is None:
            return
        self._wl_intro.config(text=_watch_list_intro(self._wl_cfg or {}))
        rows, back_rows = self._wl_rows or [], self._wl_back or []
        store = (self._wl_store.get() or "All").strip()
        shown = [r for r in rows if store == "All" or r["store"] == store]
        back_shown = [r for r in back_rows if store == "All" or r["store"] == store]
        sel = self._wl_sel                       # (section, store, asin) of the row whose details are shown
        views = (t.yview()[0], bt.yview()[0]) if keep_view else (0.0, 0.0)
        hit = None                               # (tree, iid, row) of that row after the refill

        def put(tree, items, cols, section):
            nonlocal hit
            tree.delete(*tree.get_children())
            by = {}
            for i, r in enumerate(items):
                tags = ("even" if i % 2 == 0 else "odd",) + (("attention",) if r.get("attention") else ())
                iid = tree.insert("", "end", values=tuple(r.get(k, "") for k, _h, _w in cols), tags=tags)
                by[iid] = r
                if sel == (section, r.get("store"), r.get("asin")):
                    hit = (tree, iid, r)
            return by

        self._wl_by_item = put(t, shown, _WATCH_LIST_TREE, "list")
        self._wl_back_by_item = put(bt, back_shown, _WATCH_LIST_BACK_TREE, "back")

        # the "Back in stock — needs a person" table, or one line when there is none
        bad_back = (self._wl_back_note or "").startswith("⚠")
        if back_shown:
            self._wl_back_none.pack_forget()
            bt.configure(height=min(len(back_shown), 4))
            self._wl_back_wrap.pack(fill="x")
            self._wl_back_count.config(text=str(len(back_shown)), fg=self.AMBER)
        else:
            self._wl_back_wrap.pack_forget()
            self._wl_back_none.config(
                text=self._wl_back_note if bad_back else
                f"None: no listing{'' if store == 'All' else ' of ' + store} that Zoro restocked in the last "
                f"{_WATCH_LIST_BACK_DAYS} days is waiting for a person.",
                fg=self.RED if bad_back else self.DIM)
            self._wl_back_none.pack(fill="x", padx=2)
            self._wl_back_count.config(text="not read" if bad_back else "0",
                                       fg=self.RED if bad_back else self.GREEN)

        # the same row selected again, the tables where they were
        if hit:
            hit[0].selection_set(hit[1])
        else:
            self._wl_sel = None
        if keep_view or not hit:
            win.update_idletasks()               # the tables' new length, before scrolling them back
            t.yview_moveto(views[0])
            bt.yview_moveto(views[1])
        else:
            hit[0].see(hit[1])
        per = {}
        for r in rows:
            per[r["store"]] = per.get(r["store"], 0) + 1
        if store == "All":
            text = f"{len(rows)} on the list" + (" · " + " · ".join(f"{s} {n}" for s, n in per.items())
                                                 if len(per) > 1 else "")
        else:
            text = f"{len(shown)} of {len(rows)} on the list"
        bad = (self._wl_note or "").startswith("⚠")
        self._wl_count.config(text="not read" if bad else text,
                              fg=self.RED if bad else (self.AMBER if rows else self.GREEN))
        if hit:
            self._wl_detail.config(text=_watch_list_detail(hit[2]), fg=self.TEXT)
        else:
            self._wl_detail.config(
                text=self._wl_note if (bad or not rows) else
                (f"No listing of {store} is on the list." if not shown else
                 "Click a row to see its full title, whether it is our own brand, and where it stands."),
                fg=self.RED if bad else self.TEXT)

    def _on_watch_list_select(self, _e=None, section="list"):
        if self._wl_alive() is None:
            return
        t = self._wl_back_tree if section == "back" else self._wl_tree
        by = self._wl_back_by_item if section == "back" else self._wl_by_item
        sel = t.selection() if t is not None else ()
        r = by.get(sel[0]) if sel else None
        if not r:
            return
        self._wl_sel = (section, r.get("store"), r.get("asin"))
        other = self._wl_tree if section == "back" else self._wl_back_tree
        if other is not None and other.selection():        # one row selected in the two tables
            other.selection_remove(*other.selection())
        self._wl_detail.config(text=_watch_list_detail(r), fg=self.TEXT)

    def _on_watch_list_dblclick(self, event):
        item = event.widget.identify_row(event.y)
        by = self._wl_back_by_item if event.widget is getattr(self, "_wl_back_tree", None) else self._wl_by_item
        r = by.get(item) if item else None
        if r and r.get("asin"):
            # off the Tk thread: on macOS webbrowser runs osascript, which would freeze the launcher a moment
            import threading
            threading.Thread(target=webbrowser.open, args=(_watch_list_url(r["asin"]),), daemon=True).start()

    def _close_watch_list(self):
        self._wl_cancel()                     # the root's timers for this window: none may fire after it is gone
        self._wl_busy = False
        win = self._wl_alive()
        if win is not None:
            win.destroy()
        self._wl_win = self._wl_tree = self._wl_back_tree = None

    def _flash_msg(self, text, color=None):
        color = color or self.DIM
        self._global_msg.configure(text=text, fg=color)
        self.after(3000, lambda: self._global_msg.configure(text=""))

    # ══════════════════════════════════════════════════════════════════════
    #  CONSOLE logic
    # ══════════════════════════════════════════════════════════════════════
    def _console_filter_value(self):
        value = str(self._filter.get() or "All").strip()
        return value if value in CONSOLE_VIEW_CHOICES else "All"

    def _console_matches_filter(self, source):
        # Closing / messaging / gap-fill are whole-account operations the user
        # always wants to watch — NEVER hide them behind a filter (this is the
        # bug that made 'Run Closing' look frozen: bulk_* matched no filter case
        # so it only showed under 'All'). They show under every filter.
        filt = self._console_filter_value()
        # Closing/walmart runs stay visible under the GENERIC filters so "Run
        # Closing" never looks frozen — but a per-account choice is explicit
        # and must actually narrow to that account.
        if (source.startswith(("bulk_", "messages_", "closing_", "walmart"))
                and not (filt.startswith(("Tracking ", "Closing ")) and filt.split(" ", 1)[1] in STORES)):
            return True
        if filt == "All":
            return True
        if filt == "Closing":
            return source.startswith(("bulk_", "messages_", "closing_"))
        if filt == "Walmart":
            return source.startswith("walmart")
        if filt == "Orders":
            return source.startswith("orders_")
        if filt == "Tracking":
            return source.startswith("tracking_")
        if filt.startswith("Tracking ") and filt.split(" ", 1)[1] in STORES:
            return source == "tracking_" + filt.split(" ", 1)[1].lower()
        if filt.startswith("Closing ") and filt.split(" ", 1)[1] in STORES:
            _k = "_" + filt.split(" ", 1)[1].lower()
            return source in ("bulk_all", "delivery_all") or (source.endswith(_k) and source.startswith(("bulk_", "messages_", "closing_")))
        if filt == "Coupons":
            return source == "coupon"
        if filt == "Listings":
            return source in ("listings", "listings_report")
        return source == f"orders_{filt}"

    def _console_tag(self, text):
        lo = text.lower()
        if "╔" in text or "╚" in text or "═" in text:
            return "ok" if "✅" in text else "header"
        if "─" in text and ("ORDER:" in text or "PRE-CHECKOUT" in text):
            return "header"
        if "✅" in text or "success" in lo:
            return "ok"
        if "❌" in text or "error" in lo or "failed" in lo or "restricted" in lo:
            return "err"
        if "⚠" in text or "🛑" in text or "skip" in lo:
            return "warn"
        if "💵" in text or "Profit" in text or "Margin" in text:
            return "profit"
        if "WB-" in text or "WB" in text:
            return "wb"
        if "DASHBOARD" in text:
            return "dash"
        if "┌" in text or "│" in text or "└" in text:
            return "dim"
        return None

    # source → (badge, colour). Kept short so the badge column stays narrow.
    def _source_badges(self):
        return {
            "orders_DM_COUPON":   ("DM·CPN", self.CYAN),
            "orders_DM_RESELLER": ("DM·RSL", self.GREEN),
            "orders_HS_COUPON":   ("HS·CPN", self.PURPLE),
            "orders_HS_RESELLER": ("HS·RSL", self.AMBER),
            "orders_WR_RESELLER": ("WR·RSL", self.GREEN),
            "tracking_hoank":     ("TRK·HO", self.ACCENT),
            "tracking_hathoro":   ("TRK·HA", "#f7a072"),
            "tracking_wrenchly":  ("TRK·WR", self.GREEN),
            "coupon":             ("COUPON", self.AMBER),
            "listings":           ("LIST  ", self.DIM),
            "listings_report":    ("LSTRPT", self.AMBER),
            "walmart":            ("WMART ", self.PURPLE),
            "remote":             ("REMOTE", self.DIM),
        }

    # Views that mix several processes — only these need a per-line badge.
    _MULTI_SOURCE_VIEWS = ("All", "Orders", "Tracking", "Closing")

    def _badge_for(self, source):
        """(label, tag) for a console source, or None when no badge is wanted."""
        src = str(source or "").strip()
        if not src:
            return None
        table = self._source_badges()
        if src in table:
            return table[src][0], f"src::{src}"
        if src.startswith(("bulk_", "messages_", "closing_")):
            acct = ("HO" if src.endswith("_hoank") else ("HA" if src.endswith("_hathoro") else
                    ("WR" if src.endswith("_wrenchly") else "  ")))
            return f"CLOSE·{acct}", "dim"
        if src.startswith("orders_"):
            return src.replace("orders_", "")[:6].upper().ljust(6), "dim"
        if src.startswith("tracking_"):
            return src.replace("tracking_", "TRK·")[:6].ljust(6), "dim"
        return None

    _BOX_CHARS = set("│┌┐└┘├┤┬┴┼─═║╔╗╚╝╠╣╦╩╬")
    _SUMMARY_PREFIXES = ("ASIN", "SKU", "Payout", "Est Cost", "Est Mgn", "Est Pft", "With ")

    def _insert_console_line(self, ts, text, source=""):
        tag = self._console_tag(text)
        stripped = text.lstrip() if text else ""
        is_box = stripped and stripped[0] in self._BOX_CHARS
        is_summary = stripped.startswith(self._SUMMARY_PREFIXES)
        # Skip timestamp for box-drawing / summary lines so they can be copy-pasted cleanly
        if not is_box and not is_summary:
            self.console.insert("end", f"{ts} ", "time")
            # Who said it — only under a view that mixes processes.
            if self._console_filter_value() in self._MULTI_SOURCE_VIEWS:
                badge = self._badge_for(source)
                if badge:
                    self.console.insert("end", f"{badge[0]} ", badge[1])
        if tag:
            self.console.insert("end", text, tag)
        else:
            self.console.insert("end", text)
        if not text.endswith("\n"):
            self.console.insert("end", "\n")

    def _update_console_line_count(self):
        try:
            lines = int(self.console.index("end-1c").split(".")[0])
            self._con_lines.config(text=f"{lines} lines")
        except Exception:
            pass

    # How many lines the live boxes keep. Before 12 Nov 2026 a new line was only ever ADDED:
    # the console and the Logs page grew for the launcher's whole life (200k–500k lines a
    # day on 9–11 Nov 2026), so its memory climbed and every insert and redraw got slower
    # until the Mac lagged. Nothing is lost by trimming: _console_history (150k) still feeds
    # the Logs page search and every filter change, and data/console_log.txt has every line.
    CONSOLE_LIVE_MAX = 20000
    LOGS_LIVE_MAX = 20000          # the Logs page renders at most this many (_refresh_logs_view)
    FEED_LIVE_MAX = 2000

    @staticmethod
    def _trim_text(widget, max_lines, slack=1000):
        """Drop a Text widget's oldest lines once it passes max_lines + slack (the widget must
        be in state normal). Deleting in slack-sized chunks keeps it to one delete per
        ~1,000 lines; Tk keeps the view on the line being read when lines above it go."""
        try:
            n = int(widget.index("end-1c").split(".")[0])
            if n > max_lines + slack:
                widget.delete("1.0", f"{n - max_lines}.0")
        except Exception:
            pass

    def _log(self, text, tag=None):
        self.console.configure(state="normal")
        if tag:
            self.console.insert("end", text, tag)
        else:
            self.console.insert("end", text)
        self._trim_text(self.console, self.CONSOLE_LIVE_MAX)
        if self._auto_scroll.get():
            self.console.see("end")
        self.console.configure(state="disabled")
        self._update_console_line_count()

    def _log_line(self, text, source=""):
        ts = datetime.now().strftime("%H:%M:%S")
        self._console_history.append((ts, text, source))
        self._write_console_log(ts, text, source)
        self._logs_live_append(ts, text, source)
        if not self._console_matches_filter(source):
            return
        self.console.configure(state="normal")
        self._insert_console_line(ts, text, source)
        self._trim_text(self.console, self.CONSOLE_LIVE_MAX)
        self.console.configure(state="disabled")
        if self._in_poll:
            self._poll_logged = True     # _end_log_batch scrolls + counts once for the tick
            return
        if self._auto_scroll.get():
            self.console.see("end")
        self._update_console_line_count()

    def _refresh_console_view(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        # Session history is huge (150k); the bottom console shows the newest
        # 5,000 matches — the Logs page + data/console_log.txt hold everything.
        matches = [e for e in self._console_history if self._console_matches_filter(e[2])]
        if len(matches) > 5000:
            self._insert_console_line("--:--:--", f"… {len(matches) - 5000} earlier line(s) — see the Logs page …")
            matches = matches[-5000:]
        for ts, text, source in matches:
            self._insert_console_line(ts, text, source)
        if self._auto_scroll.get():
            self.console.see("end")
        self.console.configure(state="disabled")
        self._update_console_line_count()

    def _feed_line(self, text, tag=None):
        self._feed.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self._feed.insert("end", f"{ts}  ", "info")
        if tag:
            self._feed.insert("end", text + "\n", tag)
        else:
            self._feed.insert("end", text + "\n")
        self._trim_text(self._feed, self.FEED_LIVE_MAX, slack=200)
        self._feed.see("end")
        self._feed.configure(state="disabled")

    def _clear_console(self):
        filt = self._console_filter_value()
        if filt == "All":
            self._console_history.clear()
        else:
            kept = [entry for entry in self._console_history
                    if not self._console_matches_filter(entry[2])]
            self._console_history = deque(kept, maxlen=self._console_history.maxlen)
        self._refresh_console_view()

    # ══════════════════════════════════════════════════════════════════════
    #  START / STOP
    # ══════════════════════════════════════════════════════════════════════
    def _start_tracking_acct(self, acct_key):
        pkey = f"tracking_{acct_key}"
        if pkey in self._procs:
            messagebox.showinfo("Info", f"Tracking ({acct_key}) already running.")
            return
        cmd = [RUNNER_PYTHON, "-u", os.path.join(SCRIPT_DIR, "engine/tracking.py"),
               "--accounts", acct_key, "--sleep-seconds", self.trk_sleep.get()]
        if self.trk_single.get():
            cmd.append("--single-cycle")
        if self._trk_btns[acct_key]["skip_msg"].get():
            cmd.append("--skip-messages")
        self._filter.set("Tracking")
        self._launch(pkey, cmd, acct_key)
        btns = self._trk_btns[acct_key]
        btns["go"].config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        btns["stop"].config(state="normal", fg=self.RED, highlightbackground=self.RED)
        btns["lbl"].config(text="Running", fg=self.GREEN)
        self._feed_line(f"Tracking started ({acct_key})", "ok")
        self._update_hdr()

    # Placers on one Zoro account (one cart) by the owner's design: they take turns. HS_RESELLER
    # builds its cart before the checkout guard, so only this keeps it out of a running Walmart.
    ONE_ZORO_ACCOUNT = ("orders_HS_RESELLER", "walmart")

    def _zoro_account_busy(self, name):
        """The other placer on this one's Zoro account, if it is running; else None."""
        if name in self.ONE_ZORO_ACCOUNT:
            for other in self.ONE_ZORO_ACCOUNT:
                if other != name and other in self._procs:
                    return other.replace("orders_", "")
        return None

    def _start_orders(self):
        key = _profile_key_from_choice(self.ord_profile.get())
        if f"orders_{key}" in self._procs:
            messagebox.showinfo("Info", f"{key} already running.")
            return
        busy = self._zoro_account_busy(f"orders_{key}")
        if busy:
            messagebox.showwarning("Same Zoro account",
                                   f"{busy} is running on {key}'s Zoro account. Stop it first.")
            return
        holder = self._lease_blocks_start()
        if holder:
            messagebox.showwarning("Placers running elsewhere",
                                   f"Placers are running on '{holder}'. Stop them there (or from the "
                                   f"dashboard) before starting here — this prevents duplicate orders.")
            return
        self._start_orders_key(key)

    def _toggle_headless(self):
        """Persist the headless choice to bot_config.json. Nothing is restarted: a running
        Chrome keeps its mode, so the change lands on the next start (same rule as the
        remote set_headless command)."""
        on = bool(self._headless_var.get())
        try:
            cfg = _read_cfg_file()
            cfg.setdefault("chrome", {})["headless"] = on
            _write_cfg_file(cfg)
        except Exception as e:
            self._feed_line(f"Could not save the headless setting: {e}", "warn")
            return
        self._feed_line(f"Headless {'ON' if on else 'OFF'} — applies to processes started from now",
                        "ok" if on else "warn")
        self._log_line(f"🫥 Headless {'ON' if on else 'OFF'} (placers + trackers started from now; "
                       f"restart a running one to switch it)", source="remote")

    def _sunday_values(self):
        """(max_cart, min_margin_pct) from the two boxes, or None if either is not a number."""
        try:
            cap = float(self._sun_cart.get().strip().lstrip("$").replace(",", "") or "50")
            mm = float(self._sun_margin.get().strip().rstrip("%") or "20")
        except ValueError:
            return None
        if cap <= 0:
            return None
        return cap, mm

    def _save_sunday_mode(self, quiet=False):
        """Persist Sunday mode into bot_config.json → sunday_mode. Running placers read it on
        every order (cached 15 s), so nothing is restarted."""
        vals = self._sunday_values()
        if vals is None:
            # the box must show what the placers will actually do: put it back to the saved state
            self._sun_var.set(bool((_read_cfg_file().get("sunday_mode") or {}).get("enabled")))
            self._render_sunday_label()
            if not quiet:
                messagebox.showwarning("Sunday mode",
                                       "The cart limit must be a positive number of dollars and the "
                                       "margin a number of percent (e.g. 50 and 20).")
            return
        cap, mm = vals
        on = bool(self._sun_var.get())
        new = {"enabled": on, "max_cart": cap, "min_margin_pct": mm}
        try:
            cfg = _read_cfg_file()
            if (cfg.get("sunday_mode") or {}) == new:
                return                          # focus left a box without a change
            cfg["sunday_mode"] = new
            _write_cfg_file(cfg)
        except Exception as e:
            self._feed_line(f"Could not save Sunday mode: {e}", "warn")
            return
        self._render_sunday_label()
        if on:
            msg = (f"Sunday mode ON — reseller places orders costing under ${cap:g} incl. shipping at ≥ {mm:g}% est. margin; "
                   f"coupon placers use priority coupons only; everything else waits, unlabelled")
        else:
            msg = "Sunday mode OFF — held orders go back to the normal rules from the next order"
        self._feed_line(msg, "warn" if on else "ok")
        self._log_line(f"🗓️ {msg} (running placers pick it up within 15 s)", source="remote")

    def _render_sunday_label(self):
        vals = self._sunday_values()
        if self._sun_var.get() and vals:
            self._sun_lbl.config(text=f"ON — holding costs ≥ ${vals[0]:g} (incl. shipping) and margins < {vals[1]:g}% "
                                      f"for Monday's priority coupons", fg=self.AMBER)
        else:
            self._sun_lbl.config(text="off — normal rules", fg=self.MUTED)

    def _toggle_page_checkout(self):
        """Persist the attributed-checkout choice. Like headless, nothing is restarted: a
        running placer keeps its current mode until it is restarted."""
        on = bool(self._pagechk_var.get())
        try:
            cfg = _read_cfg_file()
            cfg.setdefault("checkout", {})["page_checkout"] = on
            _write_cfg_file(cfg)
        except Exception as e:
            self._feed_line(f"Could not save the attributed-checkout setting: {e}", "warn")
            return
        self._feed_line(
            ("Attributed checkout ON — orders finish on Zoro's real checkout page (~27 s slower each)"
             if on else "Attributed checkout OFF — orders are placed through the API"),
            "ok" if on else "warn")
        self._log_line(f"🧾 Attributed checkout {'ON' if on else 'OFF'} "
                       f"(placers started from now; restart a running one to switch it)", source="remote")

    def _toggle_tracking_headless(self):
        """Persist the trackers' own headless choice (chrome.headless_tracking). Restarts
        nothing — a running tracker keeps its mode until restarted."""
        on = bool(self._trk_headless_var.get())
        try:
            cfg = _read_cfg_file()
            cfg.setdefault("chrome", {})["headless_tracking"] = on
            _write_cfg_file(cfg)
        except Exception as e:
            self._feed_line(f"Could not save the tracker headless setting: {e}", "warn")
            return
        self._feed_line(f"Trackers headless {'ON' if on else 'OFF'} — applies to trackers started from now",
                        "ok" if on else "warn")
        self._log_line(f"🫥 Trackers headless {'ON' if on else 'OFF'} "
                       f"(restart a running tracker to switch it)", source="remote")

    def _toggle_chain_messaging(self):
        """Persist whether ▶ Run Closing continues into messaging. Changes nothing in flight."""
        on = bool(self._cls_chain_var.get())
        try:
            cfg = _read_cfg_file()
            cfg.setdefault("closing", {})["message_after_closing"] = on
            _write_cfg_file(cfg)
        except Exception as e:
            self._feed_line(f"Could not save the chain-messaging setting: {e}", "warn")
            return
        self._feed_line("Closing will " + ("continue into messaging" if on else
                                           "stop after the closing chain")
                        + " — applies to the next run", "ok" if on else "warn")
        self._log_line(f"✉ Message-after-closing {'ON' if on else 'OFF'}", source="remote")

    def _closing_headless(self):
        """Is the closing chain set to run without windows? Read fresh, never cached."""
        try:
            return bool((_read_cfg_file().get("chrome") or {}).get("headless_closing"))
        except Exception:
            return False

    def _toggle_closing_headless(self):
        """Persist the closing chain's headless choice (chrome.headless_closing).

        Restarts nothing. It applies to the next Chrome the chain LAUNCHES — every later step
        (SC scrape, messaging, review requests) only attaches to that one, so they follow it.
        A Chrome already open on 9331/9333 keeps its window until it is closed and relaunched.
        """
        on = bool(self._cls_headless_var.get())
        try:
            cfg = _read_cfg_file()
            cfg.setdefault("chrome", {})["headless_closing"] = on
            _write_cfg_file(cfg)
        except Exception as e:
            self._feed_line(f"Could not save the closing headless setting: {e}", "warn")
            return
        self._feed_line(f"Closing headless {'ON' if on else 'OFF'} — applies to the next Chrome "
                        f"the chain launches", "ok" if on else "warn")
        self._log_line(f"🫥 Closing headless {'ON' if on else 'OFF'} "
                       f"(close the closing Chrome to switch a running one)", source="remote")

    def _start_orders_key(self, key):
        """Start one placer profile (the GUI button and remote commands share this).
        Returns True if started, False if it was already running."""
        try:
            self._save_config()
        except Exception:
            pass
        pkey = f"orders_{key}"
        if pkey in self._procs:
            return False
        busy = self._zoro_account_busy(pkey)
        if busy:
            self._feed_line(f"{key} NOT started: {busy} is running on its Zoro account", "warn")
            return False
        cmd = [RUNNER_PYTHON, "-u", os.path.join(SCRIPT_DIR, "engine/orders.py"), key]
        self._filter.set(key)
        self._launch(pkey, cmd, key)
        self._pstat[key].config(text="Running", fg=self.GREEN)
        self._pstop[key].config(state="normal", fg=self.RED, highlightbackground=self.RED)
        self.btn_ord_stop.config(state="normal", fg=self.RED, highlightbackground=self.RED)
        self._feed_line(f"Orders started: {key}", "ok")
        self._update_hdr()
        return True

    def _start_coupon(self):
        if "coupon" in self._procs:
            messagebox.showinfo("Info", "Coupon automation already running.")
            return
        if not os.path.exists(COUPON_SCRIPT):
            messagebox.showerror("Missing script", COUPON_SCRIPT)
            return
        try:
            count = int((self.cpn_count.get() or "5").strip())
        except ValueError:
            messagebox.showerror("Invalid count", "Coupon count must be a number.")
            return
        if count < 1:
            messagebox.showerror("Invalid count", "Coupon count must be at least 1.")
            return
        try:
            bulk_wait = int((self.cpn_wait.get() or "180").strip())
        except ValueError:
            messagebox.showerror("Invalid bulk wait", "Bulk wait must be a number of seconds.")
            return
        if bulk_wait < 0:
            messagebox.showerror("Invalid bulk wait", "Bulk wait cannot be negative.")
            return
        try:
            workers = int((self.cpn_workers.get() or "10").strip())
        except ValueError:
            messagebox.showerror("Invalid parallel count", "Parallel must be a number.")
            return
        if workers < 1:
            messagebox.showerror("Invalid parallel count", "Parallel must be at least 1.")
            return

        provider = (getattr(self, "cpn_provider", None) and self.cpn_provider.get() or "auto").strip().lower()
        cmd = [RUNNER_PYTHON, "-u", COUPON_SCRIPT,
               "--count", str(count), "--launcher-mode",
               "--bulk-signup-wait", str(bulk_wait),
               "--workers", str(workers),
               "--provider", provider]
        if self.cpn_bulk.get():
            cmd.append("--bulk")

        self._filter.set("Coupons")
        mode = "bulk" if self.cpn_bulk.get() else "interactive"
        self._launch("coupon", cmd, f"{mode}_{count}")
        self.btn_cpn_go.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
        self.btn_cpn_stop.config(state="normal", fg=self.RED, highlightbackground=self.RED)
        self.lbl_cpn.config(text=f"Running ({mode})", fg=self.GREEN)
        self._feed_line(f"Coupon automation started ({mode}, count={count})", "ok")
        self._update_hdr()

    def _start_multi(self):
        try:
            self._save_config()
        except Exception:
            pass
        holder = self._lease_blocks_start()
        if holder:
            messagebox.showwarning("Placers running elsewhere",
                                   f"Placers are running on '{holder}'. Stop them there (or from the "
                                   f"dashboard) before starting here — this prevents duplicate orders.")
            return
        n = 0
        for pk in PROFILES:
            if self._multi_vars[pk].get() and f"orders_{pk}" not in self._procs:
                busy = self._zoro_account_busy(f"orders_{pk}")
                if busy:
                    self._feed_line(f"{pk} NOT started: {busy} is running on its Zoro account", "warn")
                    continue
                cmd = [RUNNER_PYTHON, "-u", os.path.join(SCRIPT_DIR, "engine/orders.py"), pk]
                self._launch(f"orders_{pk}", cmd, pk)
                self._pstat[pk].config(text="Running", fg=self.GREEN)
                self._pstop[pk].config(state="normal", fg=self.RED, highlightbackground=self.RED)
                n += 1
        if n:
            self.btn_ord_stop.config(state="normal", fg=self.RED, highlightbackground=self.RED)
            self._feed_line(f"Multi-launch: {n} profiles started", "ok")
            self._update_hdr()
        else:
            messagebox.showinfo("Info", "No profiles checked or all running.")

    def _stop_all_orders(self):
        for pk in PROFILES:
            k = f"orders_{pk}"
            if k in self._procs:
                self._stop(k)

    # ══════════════════════════════════════════════════════════════════════
    #  PROCESS MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════
    def _launch(self, name, cmd, label="", extra_env=None):
        if self._closing_down:      # a timer or remote command must not start anything now
            return
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"   # Windows: make children default to UTF-8 for ALL file I/O too
        # Ensure subprocess scripts in engine/ can import sibling packages (utils, platforms, checkout)
        env["PYTHONPATH"] = SCRIPT_DIR + os.pathsep + env.get("PYTHONPATH", "")
        if extra_env:
            env.update(extra_env)
        if name.startswith("orders_") and (_read_cfg_file().get("chrome") or {}).get("headless"):
            env["ZUHRA_HEADLESS"] = "1"   # utils.helpers.setup_driver → --headless=new (memory saver)
        # The closing chain: bulk_<acct> (report → ingest → SC scrape → FedEx) and
        # messages_<acct> (delivery messages + review requests). closing.ensure_chrome and
        # post_delivery both read this, so the chain stays windowless end to end.
        if name.startswith(("bulk_", "messages_", "delivery_")) and self._closing_headless():
            env["ZUHRA_HEADLESS_CLOSING"] = "1"
        tag = f"{name}_{label}" if label else name
        self._tags[name] = tag
        self._start_times[name] = datetime.now()
        self._stats[tag] = {
            "pending": 0, "processed": 0, "success": 0, "failed": 0,
            "low_profit": 0, "duplicates": 0, "oos": 0,
            "last_wb": "", "last_profit": "",
        }

        log_path = os.path.join(SCRIPT_DIR, f".{tag}_output.log")
        wfh = open(log_path, "w", encoding="utf-8")
        kw = dict(
            stdout=wfh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, cwd=SCRIPT_DIR, env=env,
        )
        if platform.system() != "Windows":
            kw["preexec_fn"] = os.setsid
        else:
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        proc = subprocess.Popen(cmd, **kw)
        self._procs[name] = proc
        rfh = open(log_path, "r", encoding="utf-8", errors="replace")
        self._readers[name] = (wfh, rfh, log_path)
        if name.startswith(SURVIVES_CLOSE):
            self._save_closing_runs()

        if not self._polling:
            self._polling = True
            self.after(250, self._poll)

        self._log_line(f"▶  {tag.upper()} started  |  PID {proc.pid}", source=name)

    def _is_verbose_line(self, line):
        """Return True if line should go to raw logs only (not console)."""
        patterns = [
            "[DUP_CHECK]", "[DEBUG", "[TIME]", "⏱️",
            ">>>", "<<<", "RESPONSE preview", "_api]",
            "Cart ID=", "Cart now has", "Item verified:",
            "Address body:", "payload:", "Cart after coupon:",
            "methods count=", "Found", "saved card", "Card:",
            "Built payment method:", "Using hardcoded",
            "_clear_cart", "_add_item_api", "_update_or_create",
            "_set_cart_shipping", "_get_shipping_methods",
            "_select_shipping_method", "_apply_coupon",
            "_submit_order", "_get_cart()", "Cart cleared",
            "addr_dict id=", "coupon_result=", "[ZORO API]",
            "[ZORO] Brand", "Brand detected", "ensure_on_site",
            "ensure_token", "[DEBUG _api]", "[DEBUG] _get_cart",
            "[DEBUG] Cart", "[DEBUG] Address body",
            "[DEBUG] POST /v1/cart", "[DEBUG] PUT /v1/cart",
            "[DEBUG] DELETE /v1/cart", "[DEBUG] GET /v1/cart",
            "[DEBUG] GET /customer", "[DEBUG] PUT /customer",
            "[DEBUG] FULL PAYLOAD", "[DEBUG] >>> ORDER",
            "[DEBUG] Order submit", "[DEBUG] Built payment",
            "[DEBUG] Found", "[DEBUG] No hardcoded",
            "[DEBUG] Using pre_price", "[DEBUG] _clear_cart",
            "[DEBUG] _get_cart()", "[DEBUG] _add_item_api",
            "[DEBUG] _update_or_create", "[DEBUG] _set_cart",
            "[DEBUG] _get_shipping", "[DEBUG] _select_shipping",
            "[DEBUG] _apply_coupon", "[DEBUG] _submit_order",
            "[DEBUG] Cart after", "[DEBUG] Cart now",
            "[DEBUG] Item verified", "[DEBUG] addr_dict",
            "[12] PLACE ORDER", "Profile:", "[DEBUG] _api",
            "COUPON mode:", "RESELLER mode:",
        ]
        return any(p in line for p in patterns)

    def _write_raw_log(self, line):
        """Append a line to the raw debug log file."""
        try:
            entry = f"{datetime.now().strftime('%H:%M:%S')}  {line}\n"
            if self._in_poll:
                self._raw_log_buf.append(entry)
                return
            with open(self._raw_log_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            pass

    def _write_console_log(self, ts, text, source):
        """Append a line to the persistent console log file with daily rotation."""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self._console_log_date:
                self._console_log_date = today
            src_tag = f"[{source}] " if source else ""
            entry = f"{today} {ts}  {src_tag}{text}\n"
            if self._in_poll:
                self._console_log_buf.append(entry)
                return
            with open(self._console_log_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            pass

    def _end_log_batch(self):
        """Close a _poll batch: write the buffered lines to both log files (one open each, not
        two per line — up to a million a day) and scroll / count the boxes once."""
        self._in_poll = False
        for attr, path in (("_console_log_buf", self._console_log_path),
                           ("_raw_log_buf", self._raw_log_path)):
            buf = getattr(self, attr)
            if not buf:
                continue
            setattr(self, attr, [])          # swap first: a line added meanwhile is never lost
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.writelines(buf)
            except Exception:
                pass
        if self._poll_logged:
            self._poll_logged = False
            try:
                if self._auto_scroll.get():
                    self.console.see("end")
                self._update_console_line_count()
                if hasattr(self, "_logs_text") and self._logs_follow.get():
                    self._logs_text.see("end")
            except Exception:
                pass

    def _poll(self):
        self._in_poll = True
        try:
            self._poll_readers()
        finally:
            self._end_log_batch()
        if self._readers:
            self.after(250, self._poll)
        else:
            self._polling = False

    def _poll_readers(self):
        for name in list(self._readers):
            wfh, rfh, lp = self._readers[name]
            tag = self._tags.get(name, name)
            data = rfh.read()
            if data:
                for line in data.splitlines():
                    line_s = line.strip()
                    if not line_s:
                        continue
                    self._write_raw_log(line_s)
                    if self._is_verbose_line(line_s):
                        continue
                    self._log_line(line_s, source=name)
                    self._parse(tag, line_s)

            proc = self._procs.get(name)
            if proc and proc.poll() is not None:
                rem = rfh.read()
                if rem:
                    for line in rem.splitlines():
                        line_s = line.strip()
                        if line_s:
                            self._write_raw_log(line_s)
                            if not self._is_verbose_line(line_s):
                                self._log_line(line_s, source=name)
                rfh.close()
                wfh.close()
                # KEEP the finished run's full log (it used to be deleted here,
                # so "show me the last closing log" had no answer) →
                # data/logs/<tag>_<timestamp>.log, pruned to the newest 40.
                try:
                    logs_dir = os.path.join(SCRIPT_DIR, "data", "logs")
                    os.makedirs(logs_dir, exist_ok=True)
                    dest = os.path.join(
                        logs_dir, f"{tag}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.log")
                    os.replace(lp, dest)
                    old = sorted(glob.glob(os.path.join(logs_dir, "*.log")),
                                 key=os.path.getmtime)
                    for f in old[:-40]:
                        os.remove(f)
                except OSError:
                    pass
                del self._readers[name]
                # _on_done may log, open a dialog or start the next run: it runs outside the
                # batch exactly as before, with everything read so far already written.
                self._end_log_batch()
                self._on_done(name, proc.returncode)
                self._in_poll = True

    # ══════════════════════════════════════════════════════════════════════
    #  PARSE output → stats
    # ══════════════════════════════════════════════════════════════════════
    def _parse(self, tag, line):
        st = self._stats.get(tag)
        if not st:
            return

        m = re.search(r'Pending\s+(\d+)', line)
        if m:
            st["pending"] = int(m.group(1))
        m = re.search(r'Processed\s+(\d+)', line)
        if m:
            st["processed"] = int(m.group(1))
        m = re.search(r'Success\s+(\d+)', line)
        if m:
            st["success"] = int(m.group(1))
        m = re.search(r'Failed\s+(\d+)', line)
        if m:
            st["failed"] = int(m.group(1))
        m = re.search(r'Low Profit\s+(\d+)', line)
        if m:
            st["low_profit"] = int(m.group(1))

        if "DUPLICATE" in line.upper() or "Already ordered:" in line or "🔁" in line:
            st["duplicates"] += 1
            m = re.search(r'(WB-?\d+)', line)
            if m:
                self._feed_line(f"🔁 Duplicate → {m.group(1)}", "warn")
        if "OUT OF STOCK" in line.upper() or "→ OOS" in line:
            st["oos"] += 1

        m = re.search(r'ORDER PLACED:\s*(WB-?\d+)', line)
        if m:
            wb = m.group(1)
            st["last_wb"] = wb
            self._last_wb = wb
            self._total_placed += 1
            self._feed_line(f"✅ ORDER: {wb}", "ok")

        m = re.search(r'Final Profit[^:]*:\s*[\+\-]?\$?([\-\d.]+)', line)
        if m:
            try:
                val = float(m.group(1))
                st["last_profit"] = f"${val:+.2f}"
                self._total_profit += val
            except ValueError:
                pass

        m = re.search(r'Profit\s*:\s*\+?\$?([\-\d.]+)', line)
        if m and "Est." not in line and "PRE-CHECKOUT" not in line:
            try:
                val = float(m.group(1))
                st["last_profit"] = f"${val:+.2f}"
            except ValueError:
                pass

        if "RESTRICTED" in line.upper() and "🚨" in line:
            self._feed_line("🚨 Zoro RESTRICTED!", "err")
        if "Restriction cleared" in line:
            self._feed_line("✅ Restriction cleared", "ok")
        if "STATE_RESTRICTED" in line:
            self._feed_line("⚠️ State restricted", "warn")
        if "STARTING CYCLE" in line:
            self._feed_line("⚙️ New cycle", "info")
        if "Cycle Complete" in line:
            self._feed_line("🔄 Cycle done — sleeping", "info")
        if "No Pending orders" in line:
            self._feed_line("📭 No pending orders", "info")

        m = re.search(r'UNMAPPED_ASIN:\s*(\S+)', line)
        if m:
            self._add_unmapped_asin(m.group(1))

        # ── Closing pipeline parsing ──
        m = re.search(r'Progress:\s*\d+/(\d+)', line)
        if m:
            st["pending"] = int(m.group(1))
        m = re.search(r'Completed:\s*(\d+)', line)
        if m:
            st["success"] = int(m.group(1))
        m = re.search(r'Incomplete:\s*(\d+)', line)
        if m:
            st["processed"] = int(m.group(1))
        m = re.search(r'Cancelled:\s*(\d+)', line)
        if m:
            st["failed"] = int(m.group(1))
        m = re.search(r'Errors:\s*(\d+)', line)
        if m:
            st["oos"] = int(m.group(1))

        self._refresh_dash()

    def _ensure_profile_card(self, tag):
        if tag in self._pcards:
            return self._pcards[tag]
        idx = len(self._pcards)
        card = tk.Frame(self._pcards_frame, bg=self.INP_BG,
                        highlightbackground=self.DIM, highlightthickness=1)
        card.grid(row=idx // 4, column=idx % 4, sticky="nsew", padx=4, pady=4)
        hdr = tk.Frame(card, bg=self.INP_BG)
        hdr.pack(fill="x", padx=8, pady=(6, 2))
        dot = tk.Label(hdr, text="●", font=(self.MONO, 10), bg=self.INP_BG, fg=self.DIM)
        dot.pack(side="left")
        # tags double the label ("orders_DM_COUPON_DM_COUPON", "tracking_hoank_HOANK")
        # — collapse the repeated suffix for a clean card title
        parts = tag.split("_")
        for L in range(len(parts) // 2, 0, -1):
            if [x.lower() for x in parts[-L:]] == [x.lower() for x in parts[-2 * L:-L]]:
                parts = parts[:-L]
                break
        disp = "_".join(parts)
        if disp.lower().startswith("orders_"):
            disp = disp[7:]
        tk.Label(hdr, text=disp.upper(), font=(self.MONO, 10, "bold"),
                 bg=self.INP_BG, fg=self.ACCENT).pack(side="left", padx=(6, 0))
        body = tk.Frame(card, bg=self.INP_BG)
        body.pack(fill="x", padx=8, pady=(0, 6))
        labels = {"dot": dot}
        rows = [("Pending", "pending", self.TEXT, "Success", "success", self.GREEN),
                ("Processed", "processed", self.TEXT, "Failed", "failed", self.RED),
                ("Low Profit", "low_profit", self.AMBER, "Last WB", "last_wb", self.GREEN)]
        for r, (l1, k1, c1, l2, k2, c2) in enumerate(rows):
            tk.Label(body, text=l1, font=(self.MONO, 9), bg=self.INP_BG,
                     fg=self.MUTED, width=10, anchor="w").grid(row=r, column=0, sticky="w")
            v1 = tk.Label(body, text="—", font=(self.MONO, 10, "bold"), bg=self.INP_BG,
                          fg=c1, width=5, anchor="w")
            v1.grid(row=r, column=1, sticky="w")
            tk.Label(body, text=l2, font=(self.MONO, 9), bg=self.INP_BG,
                     fg=self.MUTED, width=8, anchor="w").grid(row=r, column=2, sticky="w")
            v2 = tk.Label(body, text="—", font=(self.MONO, 10, "bold"), bg=self.INP_BG,
                          fg=c2, anchor="w")
            v2.grid(row=r, column=3, sticky="w")
            labels[k1], labels[k2] = v1, v2
        self._pcards[tag] = labels
        return labels

    def _refresh_profile_cards(self):
        if not hasattr(self, "_pcards_frame"):
            return
        live_tags = set(self._tags.values())
        for tag, st in self._stats.items():
            L = self._ensure_profile_card(tag)
            for k in ("pending", "processed", "success", "failed", "low_profit"):
                L[k].config(text=str(st.get(k, 0)))
            wb = st.get("last_wb") or "—"
            L["last_wb"].config(text=wb[-10:] if len(wb) > 10 else wb)
            L["dot"].config(fg=self.GREEN if tag in live_tags else self.DIM)

    def _refresh_dash(self):
        totals = {"pending": 0, "processed": 0, "success": 0, "failed": 0,
                  "low_profit": 0, "duplicates": 0, "oos": 0, "last_wb": ""}
        for tag, st in self._stats.items():
            for k in ("pending", "processed", "success", "failed",
                      "low_profit", "duplicates", "oos"):
                totals[k] += st.get(k, 0)
            if st.get("last_wb"):
                totals["last_wb"] = st["last_wb"]

        for key, val in totals.items():
            lbl = self._dash_labels.get(key)
            if lbl:
                lbl.configure(text=str(val) if val else "—")

        self._refresh_profile_cards()
        self._hdr_placed.configure(text=str(self._total_placed))
        profit_str = f"${self._total_profit:+.2f}" if self._total_profit != 0 else "$0.00"
        self._hdr_profit.configure(text=profit_str)
        color = self.GREEN if self._total_profit >= 0 else self.RED
        self._hdr_profit.configure(fg=color)
        self._big_profit.configure(text=profit_str, fg=color)

    def _on_done(self, name, code):
        self._procs.pop(name, None)
        tag = self._tags.pop(name, name)
        self._start_times.pop(name, None)
        status = "Finished" if code == 0 else f"Stopped ({code})"
        color = self.DIM if code == 0 else self.RED
        self._log_line(f"■  {tag.upper()} {status}", source=name)
        self._feed_line(f"{'✅' if code == 0 else '❌'} {tag.upper()} {status}",
                        "ok" if code == 0 else "err")

        if name.startswith("tracking_"):
            acct_key = name.split("tracking_", 1)[1]
            if acct_key in self._trk_btns:
                btns = self._trk_btns[acct_key]
                btns["go"].config(state="normal", fg=self.GREEN, highlightbackground=self.GREEN)
                btns["stop"].config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
                btns["lbl"].config(text=status, fg=color)
        elif name == "coupon":
            self.btn_cpn_go.config(state="normal", fg=self.GREEN, highlightbackground=self.GREEN)
            self.btn_cpn_stop.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
            self.lbl_cpn.config(text=status, fg=color)
        elif name == "walmart":
            self.btn_wm_run.config(state="normal", fg=self.CYAN, highlightbackground=self.CYAN)
            self.btn_wm_scrape.config(state="normal", fg=self.CYAN, highlightbackground=self.CYAN)
            self.btn_wm_import.config(state="normal", fg=self.GREEN, highlightbackground=self.GREEN)
            self.btn_wm_stop.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
            self.lbl_wm.config(text=status, fg=color)
            self._refresh_walmart_status()
        elif name == "listings":
            self.btn_lst_go.config(state="normal", fg=self.GREEN, highlightbackground=self.GREEN)
            self.btn_lst_stop.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
            self.lbl_lst.config(text=status, fg=color)
        elif name == "listings_report":
            self.btn_lr_go.config(state="normal", fg=self.GREEN, highlightbackground=self.GREEN)
            self.btn_lr_stop.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
            self.lbl_lr.config(text=status, fg=color)
            self._refresh_listings_report_summary()
            if code in (0, 1) and self._lr_reports():     # 1 = some store not read: show what there is
                self._open_listings_report()
        elif name == "private_label":
            self.btn_pl_score.config(state="normal", fg=self.GREEN, highlightbackground=self.GREEN)
            self.btn_pl_build.config(state="normal", fg=self.CYAN, highlightbackground=self.CYAN)
            self.btn_pl_images.config(state="normal", fg=self.PURPLE, highlightbackground=self.PURPLE)
            self.btn_pl_stop.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)
            self._refresh_pl()        # the run just rewrote pl_candidates — show it
        elif name.startswith(("bulk_", "messages_", "closing_")):
            accts = _bulk_accounts(name)
            for acct in accts:
                self._set_cls_state(acct, running=False, status_text=status, color=color)
            remaining = sorted(p.upper() for p in self._procs
                               if p.startswith(("bulk_", "messages_", "closing_")) and p != name)
            self.lbl_cls.config(text=("Running: " + ", ".join(remaining)) if remaining else status,
                                fg=self.GREEN if remaining else color)
        else:
            for pk in PROFILES:
                if pk in name:
                    self._pstat[pk].config(text=status, fg=color)
                    self._pstop[pk].config(state="disabled", fg=self.MUTED,
                                           highlightbackground=self.MUTED)
                    break

        if not any(k.startswith("orders_") for k in self._procs):
            self.btn_ord_stop.config(state="disabled", fg=self.MUTED, highlightbackground=self.MUTED)

        self._update_hdr()

    @staticmethod
    def _mark_profile_clean_exit(user_data_dir):
        """Clear the "Restore pages? Chrome didn't shut down correctly" state and
        remove stale Singleton / DevToolsActivePort entries before launching Chrome on
        a profile (macOS 27 left them behind after the upgrade)."""
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
                data = json.load(f)
            prof = data.setdefault("profile", {})
            if prof.get("exit_type") != "Normal" or prof.get("exited_cleanly") is not True:
                prof["exit_type"] = "Normal"
                prof["exited_cleanly"] = True
                with open(prefs_path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
        except Exception:
            pass

    @staticmethod
    def _ports_for(name):
        """Chrome debugging port(s) a launcher process owns (its browser may outlive it:
        since macOS 27 Chrome is started through LaunchServices, not as a child)."""
        n = (name or "").lower()
        if n.startswith("orders_"):
            pk = name[len("orders_"):]
            return [PORTS[pk]] if pk in PORTS else []
        if n.startswith("tracking_"):       # the tracker's own Chrome, never the one closing is using
            return [PORTS[f"{s.upper()}_TRACKER"] for s in STORES if s.lower() in n][:1]
        for s in STORES:
            if s.lower() in n:
                return [PORTS[f"{s.upper()}_CLOSING"]]
        return []

    def _sweep_chrome(self, name, wait=True):
        """Close the Chrome window(s) that belonged to `name` (graceful, then forced)."""
        ports = self._ports_for(name)
        if not ports:
            return
        def _run():
            try:
                from utils.proc import close_chrome
                for p in ports:
                    asked, forced = close_chrome(port=p, timeout=8.0 if wait else 0.0, wait=wait)
                    if asked:
                        self.after(0, lambda p=p, a=asked, f=forced: self._feed_line(
                            f"{name.upper()}: closed Chrome on :{p}" + (" (forced)" if f else ""), "ok"))
            except Exception as e:
                self.after(0, lambda e=e: self._feed_line(f"{name.upper()}: Chrome sweep failed: {e}", "warn"))
        import threading
        threading.Thread(target=_run, daemon=True, name=f"sweep-{name}").start()

    def _stop(self, name):
        proc = self._procs.get(name)
        if proc:
            try:
                if platform.system() == "Windows":
                    subprocess.call(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    # SAFE STOP: SIGTERM first so the child's cleanup handler can
                    # quit its Chrome gracefully (that's what leaves profiles
                    # without the "didn't shut down correctly" mark), then
                    # escalate to SIGKILL only if it's still alive after 12s.
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    def _escalate(p=proc, g=pgid, tries=24, n=name):
                        if p.poll() is not None:
                            self._sweep_chrome(n)          # the child is gone: close what it left behind
                            return
                        if tries <= 0:
                            try:
                                os.killpg(g, signal.SIGKILL)
                                self._feed_line(f"{n.upper()} did not exit in 12s — force-killed", "warn")
                            except Exception:
                                pass
                            self._sweep_chrome(n)
                            return
                        self.after(500, lambda: _escalate(p, g, tries - 1, n))
                    self.after(500, _escalate)
            except Exception:
                proc.kill()
            self._feed_line(f"Stopping {name.upper()}...", "warn")

    def _stop_all(self):
        for n in list(self._procs):
            self._stop(n)

    # ══════════════════════════════════════════════════════════════════════
    #  UTILS
    # ══════════════════════════════════════════════════════════════════════
    def _update_hdr(self):
        running = list(self._procs)
        if not running:
            self._hdr_pill.configure(bg=self.MUTED)
            self._hdr_status.config(text="IDLE", bg=self.MUTED, fg=self.BG)
            self._sidebar_running.config(text="")
        else:
            n = len(running)
            self._hdr_pill.configure(bg=self.GREEN)
            self._hdr_status.config(text=f"{n} ACTIVE", bg=self.GREEN, fg=self.BG)
            self._sidebar_running.config(text=f"● {n} running")

    def _tick_clock(self):
        self._clock.config(text=datetime.now().strftime("%H:%M:%S"))
        self.after(1000, self._tick_clock)

    def _tick_toast(self):
        msg = random.choice(self._toast_msgs)
        self._toast_lbl.config(text=msg)
        self.after(4000, self._tick_toast)

    def _check_ports(self):
        for pk in list(PROFILES) + STORE_PORT_KEYS:
            p = PORTS[pk]
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            ok = s.connect_ex(('127.0.0.1', p)) == 0
            s.close()
            self._port_lbl[pk].config(
                text="● ON" if ok else "● OFF",
                fg=self.GREEN if ok else self.RED)

    def _clear_coupon_cache(self):
        # The live cache is COUPON_METADATA_FILE (utils/config.py) — the old
        # ~/Downloads path was never what the engine reads; the button was a no-op.
        p = os.path.join(SCRIPT_DIR, "data", "dataset", "coupon_metadata.json")
        if os.path.exists(p):
            os.remove(p)
            self._feed_line("Coupon cache cleared", "ok")
        else:
            self._feed_line("No coupon cache found", "info")

    def _refresh_unmapped(self):
        """Rebuild the UNMAPPED ASINs tree from the placer run logs (data/rajar_logs/run_*.csv),
        latest-status-per-order → the CURRENT unmapped set. ASIN × N parents, order children.
        Double-click an order → Seller Central (to label it). Auto-clears once an ASIN is mapped
        and the placer re-runs (that order flips to SUCCESS and drops off)."""
        if getattr(self, "_unmapped_tree", None) is None and getattr(self, "_unmapped_page_tree", None) is None:
            return
        latest = self._latest_status_by_order()
        groups_all = {}
        for aid, d in latest.items():
            if d["s"] == "UNMAPPED_ASIN":
                groups_all.setdefault(d["asin"] or "(no ASIN)", []).append(
                    (aid, d["buyer"], d["state"], d.get("profile", "")))

        prio = self._get_priority_asins_cached()

        def _fill_section(t, omap, header_text, header_tag, data):
            hdr = t.insert("", "end", text=header_text, open=True, tags=(header_tag,))
            for asin, orders in sorted(data.items(), key=lambda kv: -len(kv[1])):
                parent = t.insert(hdr, "end", text=f"{asin}   ×{len(orders)}", open=False)
                for aid, buyer, state, prof in orders:
                    child = t.insert(parent, "end", text=f"      {aid}   {buyer} ({state})   ↗ SC")
                    omap[child] = aid

        # Populate BOTH the dashboard mini-panel tree (always unfiltered) and the dedicated
        # page tree (respects its profile dropdown). Two SEGREGATED buckets vs Priority_ASIN_Control:
        #   NEED ACTION  = unmapped AND NOT in priority control → never placed → label them.
        #   IN PRIORITY  = unmapped but already assigned → placer maps/places over time.
        for tree_attr, omap_attr, lbl_attr, prof_attr in (
            ("_unmapped_tree", "_unmapped_orders", "_unmapped_lbl", None),
            ("_unmapped_page_tree", "_unmapped_page_orders", "_unmapped_page_lbl", "_unmapped_page_profile"),
        ):
            t = getattr(self, tree_attr, None)
            if t is None:
                continue
            # Per-tree profile filter (dashboard mini-panel = All; dedicated page = its dropdown).
            sel = "All"
            combo = getattr(self, prof_attr, None) if prof_attr else None
            if combo is not None:
                try:
                    sel = (combo.get() or "All").strip()
                except Exception:
                    sel = "All"
            groups = {}
            for asin, orders in groups_all.items():
                kept = [o for o in orders if sel == "All" or o[3] == sel]
                if kept:
                    groups[asin] = kept
            need    = {a: v for a, v in groups.items() if a not in prio}
            handled = {a: v for a, v in groups.items() if a in prio}
            need_orders    = sum(len(v) for v in need.values())
            handled_orders = sum(len(v) for v in handled.values())
            lbl_text = f"⚠ {need_orders} orders · {len(need)} ASINs to label"
            if handled:
                lbl_text += f"      ℹ {len(handled)} ASINs in priority control"
            if not prio:
                lbl_text += "      (⚠ priority sheet unavailable — all shown as need-action)"
            if sel != "All":
                lbl_text += f"      · filter: {sel}"

            t.delete(*t.get_children())
            try:
                t.tag_configure("need_hdr", foreground=self.RED)
                t.tag_configure("prio_hdr", foreground=self.MUTED)
            except Exception:
                pass
            omap = {}
            _fill_section(t, omap,
                          f"⚠  NEED ACTION — not in priority control  ({need_orders} orders · {len(need)} ASINs)",
                          "need_hdr", need)
            if handled:
                _fill_section(t, omap,
                              f"ℹ  IN PRIORITY CONTROL — auto-handled, no action  ({handled_orders} orders · {len(handled)} ASINs)",
                              "prio_hdr", handled)
            setattr(self, omap_attr, omap)
            lbl = getattr(self, lbl_attr, None)
            if lbl:
                lbl.config(text=lbl_text)

    def _get_priority_asins_cached(self):
        """Set of ALL ASINs across every Priority_ASIN_Control column (union), cached ~5 min.
        Self-contained Sheets read (no engine import) → empty set on any failure = show all."""
        import time
        now = time.time()
        if getattr(self, "_prio_cache", None) is not None and (now - getattr(self, "_prio_cache_t", 0)) < 300:
            return self._prio_cache
        s = set()
        try:
            sys.path.insert(0, SCRIPT_DIR)
            from utils.config import (GOOGLE_SHEET_ID, GOOGLE_SHEET_PRIORITY_ASIN_TAB,
                                      GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE)
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            creds = service_account.Credentials.from_service_account_file(
                GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE,
                scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
            svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
            res = svc.spreadsheets().values().get(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"'{GOOGLE_SHEET_PRIORITY_ASIN_TAB}'!A1:Z2000").execute()
            for row in res.get("values", [])[1:]:
                for v in row:
                    v = (v or "").strip()
                    if v.startswith("B") and len(v) == 10:
                        s.add(v)
        except Exception as e:
            self._feed_line(f"Priority sheet read failed: {e}", "info")
        self._prio_cache = s
        self._prio_cache_t = now
        return s

    def _on_unmapped_dblclick(self, event):
        tree = event.widget
        item = tree.identify_row(event.y)
        if not item:
            return
        omap = (self._unmapped_orders if tree is getattr(self, "_unmapped_tree", None)
                else getattr(self, "_unmapped_page_orders", {}))
        oid = omap.get(item)
        if oid:  # order child → open that order in Seller Central
            webbrowser.open(f"https://sellercentral.amazon.com/orders-v3/order/{oid}")
        else:  # ASIN parent → open SC unshipped-MFN search filtered to this ASIN (to label it)
            asin = (tree.item(item, "text") or "").split()[0]
            if asin.startswith("B"):
                webbrowser.open("https://sellercentral.amazon.com/orders-v3/search"
                                f"?fulfillmentType=mfn&orderStatus=unshipped&page=1&q={asin}&qt=asin")

    def _clear_unmapped(self):
        self._unmapped_set.clear()
        for tree_attr, omap_attr, lbl_attr in (
            ("_unmapped_tree", "_unmapped_orders", "_unmapped_lbl"),
            ("_unmapped_page_tree", "_unmapped_page_orders", "_unmapped_page_lbl"),
        ):
            t = getattr(self, tree_attr, None)
            if t:
                t.delete(*t.get_children())
                setattr(self, omap_attr, {})
                lbl = getattr(self, lbl_attr, None)
                if lbl:
                    lbl.config(text="")

    def _add_unmapped_asin(self, asin):
        """Live signal from the placer console — debounce a tree refresh so it updates during a run."""
        asin = (asin or "").strip()
        if asin:
            self._unmapped_set.add(asin)
        if getattr(self, "_unmapped_tree", None):
            if getattr(self, "_um_refresh_id", None):
                try:
                    self.after_cancel(self._um_refresh_id)
                except Exception:
                    pass
            self._um_refresh_id = self.after(2500, self._refresh_unmapped)

    def _open_logs(self):
        if platform.system() == "Darwin":
            subprocess.Popen(["open", SCRIPT_DIR])
        elif platform.system() == "Windows":
            os.startfile(SCRIPT_DIR)

    def _install_quit_hooks(self):
        """Every way of quitting runs _on_close. WM_DELETE_WINDOW is only the window's X: Cmd+Q and
        Dock > Quit end Tk on the spot, and closing the Terminal kills the launcher. Each of those
        left the placers and trackers running with nothing to stop them (9 Nov 2026: six
        survivors, and not one of their logs showed a stop signal)."""
        try:
            if platform.system() == "Darwin":
                self.createcommand("::tk::mac::Quit", self._on_close)
        except Exception:
            pass

        def _signalled(signum, frame):
            try:                    # the Terminal may be gone, and printing to it raises
                sys.stdout = sys.stderr = open(os.devnull, "w")
            except Exception:
                pass
            self.after(0, self._on_close)

        for sig in ("SIGHUP", "SIGTERM", "SIGINT"):
            if hasattr(signal, sig):
                signal.signal(getattr(signal, sig), _signalled)

    def _stop_leftover_workers(self):
        """Placers, trackers and Walmart left behind by a launcher that died (crash, force quit).
        This launcher has no record of them, so closing it would otherwise leave them running.
        Closing-side runs are not on the list. Returns them for _on_close to wait on."""
        if platform.system() == "Windows":
            return []
        try:
            from utils.proc import AdoptedProcess, orphaned_processes
            scripts = [os.path.join(SCRIPT_DIR, s)
                       for s in ("engine/orders.py", "engine/tracking.py", "walmart_orders.py")]
            found = orphaned_processes(scripts)
        except Exception:
            return []
        left = []
        for pid, _cmd in found:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                left.append(AdoptedProcess(pid))
            except Exception:
                pass
        return left

    def _on_close(self):
        if self._closing_down:      # Cmd+Q pressed twice, or a signal while already closing
            return
        self._closing_down = True
        try:
            self.withdraw()         # gone for the user; the process stays until its workers exited
            try:
                self._close_watch_list()        # its own window and timers go with the launcher
            except Exception:
                pass
        except Exception:
            pass
        # A closing-side run carries on with its browsers (SURVIVES_CLOSE); everything else stops.
        keep = [] if platform.system() == "Windows" else [n for n in self._procs if n.startswith(SURVIVES_CLOSE)]
        workers = [p for n, p in self._procs.items() if n not in keep]
        for n in list(self._procs):
            if n not in keep:
                self._stop(n)
        workers += self._stop_leftover_workers()
        for n in keep:
            self._log_line(f"{self._tags.get(n, n).upper()} keeps running with the launcher closed "
                           f"(reopen the launcher to see it or stop it)", source=n)
        try:
            self._save_closing_runs()
        except Exception:
            pass
        try:
            os.remove(LAUNCHER_STATE)       # tells the cloud agent (and colleagues) we're closed
        except OSError:
            pass
        # Every automation browser: ask each to quit now, force whatever is still there in 4 s,
        # then close the window. (Placers, tracking and closing all use the ports in PORTS.)
        ports = set(PORTS.values())
        if keep:
            ports -= {PORTS[f"{s.upper()}_CLOSING"] for s in STORES}
        try:
            from utils.proc import close_chrome
            for port in sorted(ports):
                close_chrome(port=port, wait=False)
        except Exception:
            pass
        deadline = time.time() + 13           # what _stop gives a worker before its SIGKILL
        def _finish():
            # Not done until every worker is really gone: _stop's SIGKILL for one that ignores
            # SIGTERM runs on this window's timers, so destroying the window sooner cancels it.
            alive = [p for p in workers if p.poll() is None]
            if alive and time.time() < deadline:
                self.after(500, _finish)
                return
            for p in alive:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
            try:
                from utils.proc import close_chrome
                for port in sorted(ports):
                    close_chrome(port=port, timeout=0.0)
            except Exception:
                pass
            try:
                self._save_closing_runs()     # the log was still being read until now
            except Exception:
                pass
            self.destroy()
        self.after(4000, _finish)


if __name__ == "__main__":
    App().mainloop()
