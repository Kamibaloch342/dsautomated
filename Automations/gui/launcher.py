#!/usr/bin/env python3
"""
ZUHRA Software — GUI Launcher v4
Full-page dashboard with sidebar navigation.
Every section gets its own page with proper spacing and scrollable content.
"""

import sys, subprocess, os

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
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
import json, signal, platform, re, socket, random, webbrowser, csv, glob, time
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


PROFILES = ["DM_COUPON", "DM_RESELLER", "HS_COUPON", "HS_RESELLER"]
PORTS    = {"DM_COUPON": 9221, "DM_RESELLER": 9222, "HS_COUPON": 9223, "HS_RESELLER": 9224, "HOANK_CLOSING": 9331, "HATHORO_CLOSING": 9333}
ICONS    = {"DM_COUPON": "🏷️", "DM_RESELLER": "📦", "HS_COUPON": "🏷️", "HS_RESELLER": "📦", "HOANK_CLOSING": "💰", "HATHORO_CLOSING": "💰"}
PROFILE_CHOICES = {pk: f"{i} — {pk}" for i, pk in enumerate(PROFILES, 1)}
PROFILE_LABELS = list(PROFILE_CHOICES.values())
CONSOLE_VIEW_CHOICES = (["All", "Closing", "Closing HOANK", "Closing Hathoro", "Walmart",
                         "Orders", "Tracking", "Tracking HOANK", "Tracking Hathoro",
                         "Coupons", "Listings"] + PROFILES)


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
        "HS_RESELLER": {"target_label": "Rajar",   "priority_asins": [], "min_margin": -20, "order_limit": 0, "payment_method": "CREDIT_CARD"},
    },
    "velocity": {"max_per_cycle": 0, "inter_order_delay": 0, "cycle_sleep": 120},
    "blocked_order_ids": [],
    "telegram": {"enabled": True},
}


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

        self.protocol("WM_DELETE_WINDOW", self._on_close)
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
        except Exception:
            pass
        finally:
            self._state_id = self.after(5000, self._tick_state)

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
        acct_title = "HOANK" if acct.lower() == "hoank" else ("Hathoro" if acct.lower() == "hathoro" else acct)
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
                return False, f"unknown account '{acct}' (use hoank / hathoro)"
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
                return True, "closing started (HOANK + Hathoro, sequential)"
            if acct_title not in ("HOANK", "Hathoro"):
                return False, "account must be HOANK, Hathoro or ALL"
            if f"bulk_{acct_title.lower()}" in self._procs or "bulk_all" in self._procs:
                return True, f"{acct_title} closing already running"
            self._start_bulk(acct_title)
            return True, f"closing started ({acct_title})"
        if a == "stop_closing":
            for t in ((acct_title,) if acct_title in ("HOANK", "Hathoro") else ("HOANK", "Hathoro")):
                self._stop_closing_procs(t)
            return True, "stopping closing/messaging"
        if a == "run_messages":
            if acct_title not in ("HOANK", "Hathoro"):
                return False, "account must be HOANK or Hathoro"
            self._start_messaging(acct_title)
            return True, f"messaging started ({acct_title})"
        if a == "run_delivery_check":
            if "delivery_all" in self._procs:
                return True, "a delivery check is already running"
            self._start_delivery_check("ALL")
            return True, "delivery check started (Zoro tracking, both stores)"
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
            return True, "launcher shutting down (all processes stopped)"
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
        {"enabled": true, "times": ["03:30"], "accounts": ["HOANK","Hathoro"],
         "every_hours": 0}. At each listed HH:MM (or every N hours) start the same
        closing chain the ▶ Run Closing button runs — once per account per slot."""
        try:
            ac = (_read_cfg_file().get("automation") or {}).get("auto_closing") or {}
            if ac.get("enabled"):
                if not hasattr(self, "_auto_closing_last"):
                    self._auto_closing_last = {}      # account -> slot key last started
                now = datetime.now()
                accounts = [x for x in (ac.get("accounts") or ["HOANK", "Hathoro"]) if x in ("HOANK", "Hathoro")]
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
                        # One chain for both stores, sequential: the Gmail fetch/sync runs once and
                        # the two accounts no longer contend for the DB (26 Sep 2026).
                        self._log_line(f"⏰ auto-closing: starting HOANK + Hathoro sequentially ({slot})", source="scheduler")
                        self._start_bulk("ALL")
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

        # Hide console on Logs / Loss Orders tabs for full-height view; restore on others
        if name in ("logs", "loss_orders"):
            self._main_pw.forget(self._console_frame)
            if name == "logs":
                self.after(100, self._refresh_logs_view)
            elif name == "loss_orders":
                self.after(100, self._refresh_loss_orders_table)
        elif self._current_page in ("logs", "loss_orders"):
            self._main_pw.add(self._console_frame, minsize=150, stretch="never")

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
        for pk in list(PROFILES) + ["HOANK_CLOSING", "HATHORO_CLOSING"]:
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
            ttk.Checkbutton(r1, text=pk.split("_")[1][:3], variable=var).pack(side="left", padx=(0, 6))
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

        # ── 2×2 Profile cards ────────────────────────────────────────
        cards = tk.Frame(page, bg=self.BG)
        cards.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)
        cards.rowconfigure(0, weight=1)
        cards.rowconfigure(1, weight=1)

        self._plbl = {}
        self._pmargin = {}
        self._plbl2 = {}
        self._pmargin2 = {}
        self._plimit = {}
        self._pskip_priority = {}
        self._pest_margin_only = {}
        self._plabels_only = {}
        self._prush = {}
        self._prush_batch = {}
        self._ppayment = {}  # HS_RESELLER only: "Credit Card" | "Net 30"
        self._pfocus_priority_enabled = {}
        self._pfocus_priority_code = {}

        positions = [(0, 0), (0, 1), (1, 0), (1, 1)]
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

            # ── Row 1b: Payment method (HS_RESELLER only) ─────────
            # This Zoro account has BOTH a credit card and Net30 terms
            # connected — let the user pick which one places orders.
            if pk == "HS_RESELLER":
                fgp = tk.Frame(card, bg=self.CARD)
                fgp.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))
                tk.Label(fgp, text="Payment", font=(self.FONT, 9),
                         bg=self.CARD, fg=self.DIM).pack(side="left", padx=(0, 6))
                pay_var = tk.StringVar(value="Credit Card")
                pay_combo = self._combo(fgp, ["Credit Card", "Net 30"], 12)
                pay_combo.configure(textvariable=pay_var)
                pay_combo.pack(side="left")
                self._ppayment[pk] = pay_var

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
        for acct_key, acct_label in [("hathoro", "Hathoro"), ("hoank", "HOANK")]:
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
        self.cls_msg_limit.set("1000")           # per-run cap; 0 = unlimited
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
        both_btn = self._btn(ctrl, "▶ Run Closing (both)", self.GREEN,
                             lambda: self._start_bulk("ALL"), small=True)
        both_btn.pack(side="left", padx=(16, 0))
        self._tip(both_btn,
                  "RUN BOTH STORES IN ONE CHAIN — HOANK then Hathoro, sequentially.\n"
                  "The Gmail archive fetch/sync runs once instead of twice and the two\n"
                  "accounts never contend for the database. Same steps as the per-store\n"
                  "buttons; the per-store Stop buttons stop the whole chain.")
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

        self.lbl_cls = tk.Label(ctrl, text="Idle", font=(self.MONO, 9),
                                bg=self.CARD, fg=self.MUTED)
        self.lbl_cls.pack(side="right")

        # ── One full control row PER ACCOUNT (HOANK | Hathoro) ─────────
        self._cls_btns = {}
        acct_row = tk.Frame(pi, bg=self.CARD)
        acct_row.pack(fill="x", padx=14, pady=(0, 6))
        acct_row.columnconfigure(0, weight=1)
        acct_row.columnconfigure(1, weight=1)
        for col, (acct, color) in enumerate((("HOANK", self.CYAN), ("Hathoro", self.PURPLE))):
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
        port = 9331 if account == "HOANK" else 9333
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
        when chained → coverage). account = "HOANK" | "Hathoro" | "ALL" (both, sequential,
        in ONE process — the Gmail fetch runs once and the stores never contend for the DB)."""
        both = str(account).strip().upper() in ("ALL", "BOTH")
        name = "bulk_all" if both else f"bulk_{account.lower()}"
        if name in self._procs or "bulk_all" in self._procs or (both and any(p.startswith("bulk_") for p in self._procs)):
            messagebox.showinfo("Info", "A closing run is already active.")
            return
        cmd = [RUNNER_PYTHON, "-u", os.path.join(SCRIPT_DIR, "scripts", "daily_bulk.py")]
        chain = bool(self._cls_chain_var.get())
        extra_env = {"ACCOUNTS": "HOANK,Hathoro" if both else str(account), "SKIP_MESSAGES": "0" if chain else "1"}
        if chain:
            # daily_bulk's step 7 reads these; same knobs the ✉ button uses.
            extra_env["MSG_LIMIT"] = (self.cls_msg_limit.get() or "1000").strip()
            if self.cls_msg_dry.get():
                extra_env["MSG_DRY"] = "1"
        self._filter.set("All")
        self._launch(name, cmd, "both" if both else account, extra_env=extra_env)
        # Start stays enabled so the OTHER account can also be launched. Stop targets
        # whichever account is selected in the dropdown.
        for a in (("HOANK", "Hathoro") if both else (account,)):
            self._set_cls_state(a, running=True)
        self._refresh_cls_lbl()
        self._feed_line(f"Closing bulk started ({'HOANK + Hathoro, sequential' if both else account})"
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

    def _start_messaging(self, account):
        """✉ Run Messaging = scripts/send_delivery_messages.py for the selected
        account: delivery check-ins + review requests for every delivered order
        still owed one (6–30 days after delivery, capped by Msg limit per run).
        Needs the account's logged-in Chrome (🚀 Launch button)."""
        name = f"messages_{account.lower()}"
        if name in self._procs:
            messagebox.showinfo("Info", f"{account} messaging is already running.")
            return
        limit = (self.cls_msg_limit.get() or "1000").strip()
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
        return page

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
            ("orders", "AUG ORDERS", "0", self.CYAN),
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
            ("orders", "Aug Orders", 80, "e"),
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
        self._logs_text.configure(state="disabled")
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
            if pk in self._prush_batch:
                self._prush_batch[pk].set(str(pd_.get("rush_batch", 10)))
            self._pest_margin_only[pk].set(pd_.get("est_margin_only", False))

            if pk in self._ppayment:
                self._ppayment[pk].set(
                    "Net 30" if str(pd_.get("payment_method", "CREDIT_CARD")).upper() == "NET30"
                    else "Credit Card")

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
            try:
                pdata["rush_batch"] = max(1, int(self._prush_batch[pk].get() or 10))
            except ValueError:
                pdata["rush_batch"] = 10

            if pk in self._ppayment:
                pdata["payment_method"] = ("NET30" if self._ppayment[pk].get().strip() == "Net 30"
                                           else "CREDIT_CARD")
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
                and filt not in ("Tracking HOANK", "Tracking Hathoro",
                                 "Closing HOANK", "Closing Hathoro")):
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
        if filt == "Tracking HOANK":
            return source == "tracking_hoank"
        if filt == "Tracking Hathoro":
            return source == "tracking_hathoro"
        if filt == "Closing HOANK":
            return source in ("bulk_all", "delivery_all") or (source.endswith("_hoank") and source.startswith(("bulk_", "messages_", "closing_")))
        if filt == "Closing Hathoro":
            return source in ("bulk_all", "delivery_all") or (source.endswith("_hathoro") and source.startswith(("bulk_", "messages_", "closing_")))
        if filt == "Coupons":
            return source == "coupon"
        if filt == "Listings":
            return source == "listings"
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
            "tracking_hoank":     ("TRK·HO", self.ACCENT),
            "tracking_hathoro":   ("TRK·HA", "#f7a072"),
            "coupon":             ("COUPON", self.AMBER),
            "listings":           ("LIST  ", self.DIM),
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
            acct = "HO" if src.endswith("_hoank") else ("HA" if src.endswith("_hathoro") else "  ")
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

    def _log(self, text, tag=None):
        self.console.configure(state="normal")
        if tag:
            self.console.insert("end", text, tag)
        else:
            self.console.insert("end", text)
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
        if self._auto_scroll.get():
            self.console.see("end")
        self.console.configure(state="disabled")
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

    def _start_orders(self):
        key = _profile_key_from_choice(self.ord_profile.get())
        if f"orders_{key}" in self._procs:
            messagebox.showinfo("Info", f"{key} already running.")
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

        cmd = [RUNNER_PYTHON, "-u", COUPON_SCRIPT,
               "--count", str(count), "--launcher-mode",
               "--bulk-signup-wait", str(bulk_wait),
               "--workers", str(workers)]
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
            with open(self._raw_log_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().strftime('%H:%M:%S')}  {line}\n")
        except Exception:
            pass

    def _write_console_log(self, ts, text, source):
        """Append a line to the persistent console log file with daily rotation."""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self._console_log_date:
                self._console_log_date = today
            path = self._console_log_path
            with open(path, "a", encoding="utf-8") as f:
                src_tag = f"[{source}] " if source else ""
                f.write(f"{today} {ts}  {src_tag}{text}\n")
        except Exception:
            pass

    def _poll(self):
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
                self._on_done(name, proc.returncode)

        if self._readers:
            self.after(250, self._poll)
        else:
            self._polling = False

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
        elif name.startswith(("bulk_", "messages_", "closing_")):
            if name == "bulk_all":
                accts = ("HOANK", "Hathoro")
            else:
                accts = ("HOANK",) if name.endswith("hoank") else (("Hathoro",) if name.endswith("hathoro") else ())
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
        if "hoank" in n:
            return [PORTS["HOANK_CLOSING"]]
        if "hathoro" in n:
            return [PORTS["HATHORO_CLOSING"]]
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
        for pk in list(PROFILES) + ["HOANK_CLOSING", "HATHORO_CLOSING"]:
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

    def _on_close(self):
        self._stop_all()
        try:
            os.remove(LAUNCHER_STATE)       # tells the cloud agent (and colleagues) we're closed
        except OSError:
            pass
        # Every automation browser: ask each to quit now, force whatever is still there in 4 s,
        # then close the window. (Placers, tracking and closing all use the ports in PORTS.)
        try:
            from utils.proc import close_chrome
            for port in sorted(set(PORTS.values())):
                close_chrome(port=port, wait=False)
        except Exception:
            pass
        def _finish():
            try:
                from utils.proc import close_chrome
                for port in sorted(set(PORTS.values())):
                    close_chrome(port=port, timeout=0.0)
            except Exception:
                pass
            self.destroy()
        self.after(4000, _finish)


if __name__ == "__main__":
    App().mainloop()
