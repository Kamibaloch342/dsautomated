#!/usr/bin/env python3
"""
cloud_agent.py — ZUHRA Cloud Agent: one always-on daemon per computer.

It is the bridge between THIS machine and the shared Cloudflare R2 bus
(utils/r2_store.py), so the dashboard is always current and any colleague can
see and control the launcher from anywhere:

  HEARTBEAT    every 15 s   → state/machines/<host>.json   (launcher up? placers?
                                memory, git version, last placement, dashboard URLs)
  LIVE LOGS    every 10 s   → logs/live/<host>.json        (last 400 launcher
                                console lines — the same lines the GUI shows)
  COMMANDS     every 10 s   ← control/queue/*              dashboard → launcher
                                (stop/start placers, run closing, shutdown ...)
  SLIM DB      every 5 min  → db/dashboard.db.gz           only when data changed
  CRM REPLAY   every 30 s   ← crm/events/*                 cloud dashboard edits →
                                master DB, idempotent (receipts in crm/applied/)
  LEASE        every 15 s   → state/active.json            "placers run HERE" —
                                other machines refuse to start placers meanwhile
  BACKUP       hourly       → db/master_backup.db.gz       consistent snapshot of
                                the master DB (skipped while it is still huge)
  DASHBOARD    every 30 s   keeps dashboard/server.py up on :8000 (adopts a server
                                that is already running — never crash-loops)
  TUNNEL       optional     supervises localtunnel / cloudflared and publishes
                                the public URL in the heartbeat

Raw logs (data/raw_logs.txt, data/console_log.txt) NEVER leave the machine —
only the 400-line console tail is published.

Usage:
    python scripts/cloud_agent.py --daemon          # run forever (LaunchAgent / Task Scheduler)
    python scripts/cloud_agent.py --once            # one pass of every task, then exit
    python scripts/cloud_agent.py --status          # what R2 knows about every machine
    python scripts/cloud_agent.py --install         # macOS LaunchAgent / Windows logon task
    python scripts/cloud_agent.py --role secondary  # this computer is NOT the master (closing / spare launcher)
    python scripts/cloud_agent.py --seed-from-cloud # new machine: start from the slim DB in R2
    python scripts/cloud_agent.py --sync-now        # push/pull DB changes once and show the sync state
    python scripts/cloud_agent.py --uninstall
    python scripts/cloud_agent.py --check           # credentials + bucket usage

Config (optional): data/agent_config.json
    {"dashboard_port": 8000, "keep_dashboard_up": true,
     "localtunnel_subdomain": "", "cloudflared": false,
     "slim_interval_s": 300, "backup_max_mb": 600, "backup_interval_s": 3600}
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import platform
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)                       # .../Automations
ROOT = os.path.dirname(AUTO)                       # .../Development_Unit
sys.path.insert(0, AUTO)

from utils.r2_store import (R2Store, machine_id, utcnow, KEY_DB_MANIFEST,  # noqa: E402
                            KEY_MASTER_BACKUP, PREFIX_CMD_DONE, PREFIX_CRM_APPLIED)
from utils import sync_journal  # noqa: E402

IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
PY = os.path.join(ROOT, ".venv", "Scripts" if IS_WIN else "bin", "python.exe" if IS_WIN else "python")
if not os.path.exists(PY):
    PY = sys.executable

DATA = os.path.join(AUTO, "data")
MASTER_DB = os.path.join(DATA, "automations_unified.db")
SLIM_DB = os.path.join(DATA, "dashboard_slim.db")
CONSOLE_LOG = os.path.join(DATA, "console_log.txt")
ORDER_LOG = os.path.join(DATA, "rajar_logs", "order_log.txt")
LAUNCHER_STATE = os.path.join(DATA, "launcher_state.json")
CONTROL_DIR = os.path.join(DATA, "control")
CONTROL_QUEUE = os.path.join(CONTROL_DIR, "queue")
CONTROL_DONE = os.path.join(CONTROL_DIR, "done")
AGENT_CFG = os.path.join(DATA, "agent_config.json")
AGENT_STATE = os.path.join(DATA, "agent_state.json")
KEY_SLIM_GZ = "db/dashboard.db.gz"

LABEL = "com.zuhra.agent"
PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
AGENT_LOG = os.path.expanduser("~/Library/Logs/zuhra-agent.log") if IS_MAC else os.path.join(DATA, "agent.log")
LEGACY_LABELS = ("com.zuhra.cloudsync", "com.zuhra.dashboard")

# Tables the dashboard needs. dash_chat is intentionally excluded (feature removed).
SLIM_TABLES = [
    "closing_orders", "order_placer_orders", "crm_notes", "crm_history", "order_crm",
    "dash_prefs", "dash_reminders", "listing_health", "listing_labels", "listing_relist",
    "listing_runs", "order_feedback", "order_messages", "order_returns", "walmart_orders",
    "insight_resolutions", "action_log", "zoro_watch", "monthly_pnl", "hoank_pnl",
    "amazon_order_details", "placer_claims", "closing_run_log", "bot_decisions",
]
SLIM_VIEWS = ("v_orders_all", "v_closing")

_DEFAULT_CFG = {
    "dashboard_port": 8000,
    "keep_dashboard_up": True,
    "localtunnel_subdomain": "",
    "cloudflared": False,
    "slim_interval_s": 300,
    "backup_max_mb": 600,
    "backup_interval_s": 3600,
    "heartbeat_s": 15,
    "livelog_s": 10,
    "command_s": 10,
    "crm_s": 30,
    # ROLE: "master" = the machine whose DB feeds the dashboard (slim export, backups, CRM
    # replay). "secondary" = any other computer running the launcher / closing; it only
    # exchanges DB changes with the master through the sync journal. Exactly ONE master.
    "role": "master",
    "sync_s": 60,
}
MASTER_ONLY = ("slim_sync", "backup", "crm_replay")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_cfg() -> dict:
    cfg = dict(_DEFAULT_CFG)
    try:
        with open(AGENT_CFG, "r", encoding="utf-8") as f:
            cfg.update(json.load(f) or {})
    except Exception:
        pass
    return cfg


def _git(*args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=8)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


def tail_lines(path: str, n: int = 400, max_bytes: int = 256 * 1024) -> list[str]:
    """Last n lines of a (possibly huge) text file, reading only its tail."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            chunk = f.read()
        lines = chunk.decode("utf-8", errors="replace").splitlines()
        if size > max_bytes and lines:
            lines = lines[1:]          # drop the partial first line
        return lines[-n:]
    except Exception:
        return []


def _age_s(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
        if iso.endswith("Z"):
            t = t.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - t).total_seconds()
        return (datetime.now() - t).total_seconds()
    except Exception:
        return None


# ── machine facts ─────────────────────────────────────────────────────────
def launcher_state() -> dict:
    st = _read_json(LAUNCHER_STATE, {}) or {}
    age = _age_s(st.get("at"))
    st["alive"] = age is not None and age < 20
    st["age_s"] = None if age is None else int(age)
    return st


def memory_stats() -> dict:
    out = {}
    try:
        import psutil
        vm = psutil.virtual_memory()
        out = {"total_mb": int(vm.total / 1048576), "used_pct": vm.percent,
               "available_mb": int(vm.available / 1048576)}
        chrome = python = 0
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                n = (p.info["name"] or "").lower()
                rss = p.info["memory_info"].rss if p.info["memory_info"] else 0
                if "chrome" in n:
                    chrome += rss
                elif "python" in n:
                    python += rss
            except Exception:
                continue
        out["chrome_rss_mb"] = int(chrome / 1048576)
        out["python_rss_mb"] = int(python / 1048576)
    except Exception:
        pass
    return out


def last_placement() -> dict:
    for ln in reversed(tail_lines(ORDER_LOG, 5, 16 * 1024)):
        if "| WB:" in ln:
            parts = [p.strip() for p in ln.split("|")]
            d = {"raw_time": parts[0].strip("[] ")}
            for p in parts[1:]:
                if ":" in p:
                    k, v = p.split(":", 1)
                    if k.strip() in ("WB", "Profile", "Amazon", "ASIN", "Profit", "FinalMargin"):
                        d[k.strip().lower()] = v.strip()
            return d
    return {}


def dashboard_up(port: int) -> bool:
    import urllib.request
    import urllib.error
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


# ── the agent ─────────────────────────────────────────────────────────────

_TUNNEL_URL_RE = {
    "cloudflared": re.compile(r"https://([a-z0-9-]+)\.trycloudflare\.com"),
    "localtunnel": re.compile(r"https://([a-z0-9-]+)\.loca\.lt"),
}


def pick_tunnel_url(name: str, text: str) -> str:
    """The public address a tunnel process printed, or ''. Takes the LAST one in the text.
    For cloudflared, api.trycloudflare.com is the control endpoint it talks to, not our
    tunnel — the old scrape took the first match it saw and wrote exactly that into
    cloudflared.url, so the file pointed at a host that answers 405 to everything."""
    rx = _TUNNEL_URL_RE.get(name)
    if not rx:
        return ""
    hits = [m.group(0) for m in rx.finditer(text or "") if m.group(1) != "api"]
    return hits[-1] if hits else ""


def stale_tunnel_pids(ps_lines, port, own_pids=()):
    """PIDs of tunnel processes for `port` that this agent did not start.

    A restarted agent used to leave the previous one's tunnels running (they are started in
    their own session so a launchd restart does not take them down): two localtunnels then
    fought over the same subdomain — 79 address changes on 21 Sep 2026 — and the old QUIC
    cloudflared kept failing next to the new one, so *.url could point at either."""
    out = set()
    for pid, cmd in ps_lines:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        if pid in own_pids:
            continue
        low = (cmd or "").lower()
        if ("cloudflared" in low and "tunnel" in low and f"127.0.0.1:{port}" in low) or \
                ("localtunnel" in low and f"--port {port}" in low):
            out.add(pid)
    return out


def _tg_send(text: str) -> None:
    from utils.telegram import send_telegram_message
    send_telegram_message(text, background=False)


class Agent:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.role = str(cfg.get("role") or "master").strip().lower()
        self.host = machine_id()
        self.r2 = R2Store()
        self.started = utcnow()
        self.git_sha = _git("rev-parse", "--short", "HEAD") or "unknown"
        self.git_branch = _git("rev-parse", "--abbrev-ref", "HEAD") or ""
        self.state = _read_json(AGENT_STATE, {}) or {}
        self._busy: set[str] = set()
        self._lock = threading.Lock()
        self._tunnel_urls: dict[str, str] = {}
        self._tunnel_procs: dict[str, subprocess.Popen] = {}
        self._tunnel_backoff: dict[str, float] = {}
        os.makedirs(CONTROL_QUEUE, exist_ok=True)
        os.makedirs(CONTROL_DONE, exist_ok=True)

    # ── helpers ───────────────────────────────────────────────────────────
    def _save_state(self) -> None:
        try:
            _write_json(AGENT_STATE, self.state)
        except Exception:
            pass

    def _single_flight(self, name: str, fn) -> None:
        with self._lock:
            if name in self._busy:
                return
            self._busy.add(name)

        def run():
            try:
                fn()
            except Exception as e:
                log(f"⚠️ {name}: {e}")
            finally:
                with self._lock:
                    self._busy.discard(name)
        threading.Thread(target=run, name=name, daemon=True).start()

    # ── 1. heartbeat ──────────────────────────────────────────────────────
    def heartbeat(self) -> None:
        st = launcher_state()
        procs = st.get("procs", {}) if st.get("alive") else {}
        placers = sorted(n.replace("orders_", "") for n in procs if n.startswith("orders_"))
        port = int(self.cfg.get("dashboard_port", 8000))
        payload = {
            "host": self.host, "os": platform.system(), "agent_pid": os.getpid(),
            "agent_started": self.started, "git_sha": self.git_sha, "git_branch": self.git_branch,
            "launcher": {"alive": bool(st.get("alive")), "pid": st.get("pid"), "started_at": st.get("started_at"),
                         "age_s": st.get("age_s"), "procs": procs, "closing": st.get("closing", {}),
                         "headless": st.get("headless")},
            "placers_running": placers,
            "memory": memory_stats(),
            "dashboard": {"local_up": dashboard_up(port), "port": port, "urls": dict(self._tunnel_urls)},
            "role": self.role,
            "sync": self._sync_summary(),
            "db": {"master_mb": round(os.path.getsize(MASTER_DB) / 1048576, 1) if os.path.exists(MASTER_DB) else None,
                   "wal_mb": round(os.path.getsize(MASTER_DB + "-wal") / 1048576, 1) if os.path.exists(MASTER_DB + "-wal") else 0.0,
                   "slim_mb": round(os.path.getsize(SLIM_DB) / 1048576, 1) if os.path.exists(SLIM_DB) else None,
                   "last_slim_upload": self.state.get("last_slim_upload"),
                   "last_backup": self.state.get("last_backup"),
                   "last_checkpoint": self.state.get("last_checkpoint")},
            "last_placement": last_placement(),
            "lease_holder": None,
        }
        # lease: while placers run here, we hold it; otherwise release ours.
        try:
            if placers:
                self.r2.renew_lease(self.host, placers)
            else:
                self.r2.release_lease(self.host)
            payload["lease_holder"] = self.r2.lease_holder()
        except Exception as e:
            log(f"⚠️ lease: {e}")
        self.r2.heartbeat(payload)

    # ── 2. live logs ──────────────────────────────────────────────────────
    def live_logs(self) -> None:
        st = launcher_state()
        lines = tail_lines(CONSOLE_LOG, 400)
        self.r2.push_live_log(lines, self.host, {
            "launcher_alive": bool(st.get("alive")),
            "procs": sorted((st.get("procs") or {}).keys()) if st.get("alive") else [],
            "source": "data/console_log.txt",
        })

    # ── 3. commands ───────────────────────────────────────────────────────
    _LAUNCHER_ACTIONS = {"stop_all", "stop_orders", "start_orders", "start_all_orders", "stop_process",
                         "start_tracking", "stop_tracking", "run_closing", "stop_closing", "run_messages",
                         "reload_config", "shutdown_launcher", "set_headless"}
    _AGENT_ACTIONS = {"ping", "start_launcher", "sync_now", "backup_now", "restart_dashboard",
                      "run_priority_check", "set_config"}

    def commands(self) -> None:
        for cmd in self.r2.pending_commands():
            if cmd.get("target") != self.host:
                continue                      # addressed to another machine
            age = _age_s(cmd.get("created_at"))
            if age is not None and age > 3600:
                self.r2.ack_command(cmd, False, "expired (older than 1 h)", self.host)
                continue
            action = cmd.get("action", "")
            log(f"⌘ command {action} from {cmd.get('author')} (id {cmd.get('id')})")
            try:
                if action in self._AGENT_ACTIONS:
                    ok, result = self._run_agent_action(cmd)
                elif action in self._LAUNCHER_ACTIONS:
                    ok, result = self._forward_to_launcher(cmd)
                else:
                    ok, result = False, f"unknown action '{action}'"
            except Exception as e:
                ok, result = False, f"error: {e}"
            self.r2.ack_command(cmd, ok, result, self.host)
            log(f"  → {'ok' if ok else 'FAILED'}: {result}")

    def _forward_to_launcher(self, cmd: dict) -> tuple[bool, str]:
        st = launcher_state()
        if not st.get("alive"):
            if cmd["action"].startswith("stop") or cmd["action"] == "shutdown_launcher":
                return True, "launcher is not running on this machine — nothing to stop"
            return False, "launcher is not running on this machine (send start_launcher first)"
        local = {k: v for k, v in cmd.items() if not k.startswith("_")}
        path = os.path.join(CONTROL_QUEUE, f"{cmd['id']}.json")
        _write_json(path, local)
        done = os.path.join(CONTROL_DONE, f"{cmd['id']}.json")
        for _ in range(50):                   # ≤ 25 s for the GUI to pick it up
            time.sleep(0.5)
            if os.path.exists(done):
                res = _read_json(done, {}) or {}
                try:
                    os.remove(done)
                except OSError:
                    pass
                return bool(res.get("ok")), str(res.get("result", ""))
        try:
            os.remove(path)
        except OSError:
            pass
        return False, "launcher did not acknowledge within 25 s"

    def _run_agent_action(self, cmd: dict) -> tuple[bool, str]:
        a = cmd["action"]
        args = cmd.get("args") or {}
        if a == "ping":
            return True, f"pong from {self.host}"
        if a == "start_launcher":
            if launcher_state().get("alive"):
                return True, "launcher already running"
            return self._spawn_launcher()
        if a == "sync_now":
            self.slim_sync(force=True)
            return True, f"slim DB uploaded ({self.state.get('last_slim_mb')} MB)"
        if a == "backup_now":
            return self.backup(force=True)
        if a == "restart_dashboard":
            self._stop_dashboard()
            time.sleep(2)
            self.ensure_dashboard()
            return True, "dashboard restarted"
        if a == "set_config":
            # bot_config.json edit from the (cloud) dashboard — validated by dashboard/ops.py
            sys.path.insert(0, os.path.join(AUTO, "dashboard"))
            import ops as _ops
            res = _ops.config_set(args, cmd.get("author") or "dashboard")
            return bool(res.get("ok")), json.dumps(res.get("applied") or res.get("error"))
        if a == "run_priority_check":
            # A running coupon placer picks the request up between cycles (safer: it
            # owns the Zoro cart). Only when no placer is running do we spawn the
            # standalone checker.
            sys.path.insert(0, os.path.join(AUTO, "dashboard"))
            import ops as _ops_pc
            for _p in _ops_pc.priority_request_paths(args):
                _write_json(_p, {"author": cmd.get("author"), "at": utcnow(), "args": args})
            st = launcher_state()
            if st.get("alive") and any(n.startswith("orders_") and "COUPON" in n for n in (st.get("procs") or {})):
                return True, "request queued — the running coupon placer will check between cycles"
            script = os.path.join(AUTO, "engine", "priority_check.py")
            if not os.path.exists(script):
                return False, "engine/priority_check.py not found"
            logf = open(os.path.join(DATA, "priority_check.log"), "a", encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=AUTO, PYTHONUNBUFFERED="1", PYTHONUTF8="1")
            prof = str(args.get("profile") or "HS_COUPON").upper()
            subprocess.Popen([PY, script, prof, "--max", str(args.get("max", 25))], cwd=AUTO, env=env,
                             stdout=logf, stderr=subprocess.STDOUT, start_new_session=not IS_WIN)
            return True, f"standalone priority check started for {prof} (see data/priority_check.log)"
        return False, "unhandled"

    def _spawn_launcher(self) -> tuple[bool, str]:
        gui = os.path.join(AUTO, "gui", "launcher.py")
        env = dict(os.environ, PYTHONPATH=AUTO, PYTHONUNBUFFERED="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        try:
            if IS_WIN:
                pyw = PY.replace("python.exe", "pythonw.exe")
                subprocess.Popen([pyw if os.path.exists(pyw) else PY, gui], cwd=AUTO, env=env,
                                 creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            else:
                # keep the GUI's own stdout/stderr (Tk callback tracebacks live there)
                logf = open(os.path.join(DATA, "launcher_stdout.log"), "a", encoding="utf-8")
                subprocess.Popen([PY, gui], cwd=AUTO, env=env, start_new_session=True,
                                 stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            for _ in range(40):
                time.sleep(0.5)
                if launcher_state().get("alive"):
                    return True, "launcher started"
            return False, "launcher process spawned but no state file within 20 s (display session?)"
        except Exception as e:
            return False, f"could not start launcher: {e}"

    # ── 4. slim DB export + upload ────────────────────────────────────────
    def _db_fingerprint(self) -> str:
        """Cheap change detector so we don't re-upload an identical 100 MB file."""
        try:
            c = sqlite3.connect(f"file:{MASTER_DB}?mode=ro", uri=True, timeout=20)
            c.execute("PRAGMA busy_timeout=20000")
            parts = []
            for t, col in (("closing_orders", "updated_at"), ("order_placer_orders", "synced_at"),
                           ("order_crm", "updated_at"), ("crm_notes", "created_at"),
                           ("crm_history", "created_at"), ("dash_reminders", "created_at"),
                           ("listing_labels", "rowid"), ("zoro_watch", "rowid")):
                try:
                    parts.append(str(c.execute(f"SELECT COUNT(*), MAX({col}) FROM [{t}]").fetchone()))
                except Exception:
                    parts.append(t)
            c.close()
            return hashlib.sha1("|".join(parts).encode()).hexdigest()
        except Exception:
            return str(time.time())

    def export_slim_db(self) -> str:
        if not os.path.exists(MASTER_DB):
            raise RuntimeError(f"master DB not found: {MASTER_DB}")
        tmp = SLIM_DB + ".tmp"
        for p in (tmp, tmp + "-wal", tmp + "-shm"):
            if os.path.exists(p):
                os.unlink(p)
        src = sqlite3.connect(f"file:{MASTER_DB}?mode=ro", uri=True, timeout=30)
        src.execute("PRAGMA busy_timeout=30000")
        dst = sqlite3.connect(tmp, timeout=30)
        dst.execute("PRAGMA journal_mode=OFF")
        dst.execute("PRAGMA synchronous=OFF")
        dst.execute("PRAGMA page_size=4096")
        n_tables = n_rows = 0
        for t in SLIM_TABLES:
            row = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
            if not row or not row[0]:
                continue
            try:
                dst.execute(row[0])
                cur = src.execute(f"SELECT * FROM [{t}]")
                cols = len(cur.description)
                ph = ",".join("?" * cols)
                while True:
                    batch = cur.fetchmany(5000)
                    if not batch:
                        break
                    dst.executemany(f"INSERT OR IGNORE INTO [{t}] VALUES ({ph})", batch)
                    n_rows += len(batch)
                n_tables += 1
            except Exception as e:
                log(f"  ⚠️ slim {t}: {e}")
        for v in SLIM_VIEWS:
            row = src.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name=?", (v,)).fetchone()
            if row and row[0]:
                try:
                    dst.execute(row[0])
                except Exception as e:
                    log(f"  ⚠️ view {v}: {e}")
        for t in SLIM_TABLES:
            for (isql,) in src.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
                                       "AND sql IS NOT NULL", (t,)).fetchall():
                try:
                    dst.execute(isql)
                except Exception:
                    pass
        dst.commit()
        dst.execute("VACUUM")
        dst.close()
        src.close()
        os.replace(tmp, SLIM_DB)
        mb = round(os.path.getsize(SLIM_DB) / 1048576, 1)
        log(f"📦 slim DB ready: {mb} MB ({n_tables} tables, {n_rows:,} rows)")
        self.state["last_slim_mb"] = mb
        return SLIM_DB

    def slim_sync(self, force: bool = False) -> None:
        fp = self._db_fingerprint()
        if not force and fp == self.state.get("last_slim_fingerprint"):
            return
        t0 = time.time()
        path = self.export_slim_db()
        sent = self.r2.put_file(KEY_SLIM_GZ, path, "application/gzip", gzip_it=True,
                                metadata={"host": self.host, "git": self.git_sha})
        self.r2.put_json(KEY_DB_MANIFEST, {"key": KEY_SLIM_GZ, "gz_mb": round(sent / 1048576, 1),
                                           "db_mb": self.state.get("last_slim_mb"), "uploaded_at": utcnow(),
                                           "host": self.host, "git_sha": self.git_sha, "fingerprint": fp})
        self.state["last_slim_fingerprint"] = fp
        self.state["last_slim_upload"] = utcnow()
        self._save_state()
        log(f"☁️ slim DB uploaded: {round(sent / 1048576, 1)} MB gz in {time.time() - t0:.1f}s")

    # ── 5. CRM replay ─────────────────────────────────────────────────────
    def crm_replay(self) -> None:
        events = self.r2.pending_crm_events()
        if not events:
            return
        c = sqlite3.connect(MASTER_DB, timeout=60)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=60000")
        try:
            self._ensure_crm_schema(c)
            for ev in events:
                try:
                    if self.r2.head(f"{PREFIX_CRM_APPLIED}{ev['id']}.json"):
                        self.r2.mark_crm_applied(ev, True, "duplicate — already applied")
                        continue
                    self._apply_crm_event(c, ev)
                    c.commit()
                    self.r2.mark_crm_applied(ev, True)
                    log(f"📝 CRM event {ev.get('op')} {ev.get('key')} by {ev.get('author')} applied")
                except Exception as e:
                    c.rollback()
                    self.r2.mark_crm_applied(ev, False, str(e)[:300])
                    log(f"⚠️ CRM event {ev.get('id')} failed: {e}")
        finally:
            c.close()

    @staticmethod
    def _ensure_crm_schema(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE IF NOT EXISTS order_crm(amazon_order_id TEXT PRIMARY KEY, case_status TEXT, "
                  "case_type TEXT, case_resolution TEXT, resolved_at TEXT, reminder_at TEXT, reminder_note TEXT, "
                  "flag_color TEXT, manual_delivered TEXT, comments TEXT, tags TEXT, "
                  "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS crm_notes(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                  "amazon_order_id TEXT, author TEXT, note TEXT, created_at TEXT DEFAULT (datetime('now')))")
        c.execute("CREATE TABLE IF NOT EXISTS crm_history(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                  "amazon_order_id TEXT NOT NULL, event_id TEXT UNIQUE, kind TEXT NOT NULL, field TEXT, "
                  "old_value TEXT, new_value TEXT, author TEXT, host TEXT, created_at TEXT DEFAULT (datetime('now')))")
        c.execute("CREATE INDEX IF NOT EXISTS ix_crm_history_oid ON crm_history(amazon_order_id, id)")
        cols = {r[1] for r in c.execute("PRAGMA table_info(crm_notes)")}
        if "cloud_id" not in cols:
            c.execute("ALTER TABLE crm_notes ADD COLUMN cloud_id TEXT")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_notes_cloud ON crm_notes(cloud_id) "
                  "WHERE cloud_id IS NOT NULL")

    _CRM_FIELDS = ("case_status", "case_type", "case_resolution", "resolved_at", "reminder_at",
                   "reminder_note", "flag_color", "manual_delivered", "comments", "tags")

    def _apply_crm_event(self, c: sqlite3.Connection, ev: dict) -> None:
        op, key, data = ev.get("op"), ev.get("key"), ev.get("data") or {}
        author, ts = ev.get("author") or "cloud", ev.get("created_at") or utcnow()
        if op == "add_note":
            c.execute("INSERT OR IGNORE INTO crm_notes(amazon_order_id, author, note, created_at, cloud_id) "
                      "VALUES(?,?,?,?,?)", (key, author, data.get("note", ""), ts, ev["id"]))
            c.execute("INSERT OR IGNORE INTO crm_history(amazon_order_id, event_id, kind, field, new_value, "
                      "author, host, created_at) VALUES(?,?,?,?,?,?,?,?)",
                      (key, ev["id"], "note", None, data.get("note", ""), author, ev.get("host", "cloud"), ts))
        elif op == "update_order":
            fields = {k: v for k, v in data.items() if k in self._CRM_FIELDS}
            if not fields:
                return
            old = c.execute("SELECT * FROM order_crm WHERE amazon_order_id=?", (key,)).fetchone()
            oldmap = {}
            if old:
                names = [d[0] for d in c.execute("SELECT * FROM order_crm LIMIT 0").description]
                oldmap = dict(zip(names, old))
            cols = ["amazon_order_id"] + list(fields)
            vals = [key] + list(fields.values())
            upd = ", ".join(f"{k}=excluded.{k}" for k in fields)
            c.execute(f"INSERT INTO order_crm({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
                      f"ON CONFLICT(amazon_order_id) DO UPDATE SET {upd}, updated_at=CURRENT_TIMESTAMP", vals)
            for i, (k, v) in enumerate(fields.items()):
                c.execute("INSERT OR IGNORE INTO crm_history(amazon_order_id, event_id, kind, field, old_value, "
                          "new_value, author, host, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (key, f"{ev['id']}:{i}", "field", k,
                           None if oldmap.get(k) is None else str(oldmap.get(k)),
                           None if v is None else str(v), author, ev.get("host", "cloud"), ts))
            # mirror onto closing_orders like the local dashboard does (best effort)
            try:
                ccols = {r[1] for r in c.execute("PRAGMA table_info(closing_orders)")}
                mirror = {k: v for k, v in fields.items() if k in ccols}
                if mirror:
                    c.execute(f"UPDATE closing_orders SET {', '.join(f'{k}=?' for k in mirror)} "
                              f"WHERE amazon_order_id=?", list(mirror.values()) + [key])
            except Exception:
                pass
        elif op == "reminder":
            c.execute("CREATE TABLE IF NOT EXISTS dash_reminders(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, "
                      "notes TEXT, due_at TEXT, assignee TEXT, done INTEGER DEFAULT 0, created_by TEXT, "
                      "created_at TEXT, done_at TEXT, done_by TEXT)")
            c.execute("INSERT INTO dash_reminders(title, notes, due_at, assignee, created_by, created_at) "
                      "VALUES(?,?,?,?,?,?)",
                      (data.get("title", ""), data.get("notes", ""), data.get("due_at"), data.get("assignee"),
                       author, ts))
        elif op == "set_whoami":
            c.execute("CREATE TABLE IF NOT EXISTS dash_prefs(client_key TEXT PRIMARY KEY, name TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            c.execute("INSERT INTO dash_prefs(client_key,name) VALUES(?,?) "
                      "ON CONFLICT(client_key) DO UPDATE SET name=excluded.name, updated_at=CURRENT_TIMESTAMP",
                      (key, data.get("name", "")))
        else:
            raise ValueError(f"unknown CRM op {op}")

    # ── 6. master DB backup ───────────────────────────────────────────────
    def backup(self, force: bool = False) -> tuple[bool, str]:
        if not os.path.exists(MASTER_DB):
            return False, "no master DB"
        mb = os.path.getsize(MASTER_DB) / 1048576
        limit = float(self.cfg.get("backup_max_mb", 600))
        if mb > limit and not force:
            return False, f"master DB is {mb:.0f} MB > backup_max_mb {limit:.0f} — run scripts/slim_database.py first"
        last = self.state.get("last_backup")
        if not force and last and (_age_s(last) or 0) < float(self.cfg.get("backup_interval_s", 3600)):
            return True, "backup is fresh"
        snap = os.path.join(DATA, "master_snapshot.db")
        src = sqlite3.connect(f"file:{MASTER_DB}?mode=ro", uri=True, timeout=60)
        dst = sqlite3.connect(snap)
        src.backup(dst)                        # consistent snapshot even with writers active (WAL)
        dst.close()
        src.close()
        try:
            sent = self.r2.put_file(KEY_MASTER_BACKUP, snap, "application/gzip", gzip_it=True,
                                    metadata={"host": self.host, "git": self.git_sha, "db_mb": f"{mb:.0f}"})
        finally:
            try:
                os.remove(snap)
            except OSError:
                pass
        self.state["last_backup"] = utcnow()
        self._save_state()
        msg = f"master DB backup uploaded ({round(sent / 1048576, 1)} MB gz of {mb:.0f} MB)"
        log("💾 " + msg)
        return True, msg

    # ── 6b. WAL hygiene ───────────────────────────────────────────────────
    def wal_checkpoint(self) -> None:
        """Every 30 min: PRAGMA wal_checkpoint(TRUNCATE) on the master DB, unless a closing /
        bulk / messaging / gap-fill run holds one of its locks. Nothing ever checkpointed the
        WAL and it reached 4 GB on 26 Sep 2026 (constant readers + huge write bursts kept the
        automatic passive checkpoints from ever finishing). Placers and the dashboard are
        fine: TRUNCATE waits for in-flight readers (busy_timeout) and takes well under a
        second on a 256 MB WAL."""
        if not os.path.exists(MASTER_DB):
            return
        from utils.accounts_config import active_locks
        from utils.unified_db import wal_checkpoint, wal_size_mb
        before = wal_size_mb(MASTER_DB)
        busy = active_locks(("closing_", "daily_bulk_", "messages_", "gapfill_", "gmail_fetch", "scrape_"))
        if busy:
            self.state["wal_mb"] = before
            return
        try:
            res = wal_checkpoint("TRUNCATE", db_path=MASTER_DB, busy_ms=30000)
        except Exception as e:
            log(f"⚠️ wal_checkpoint: {e}")
            return
        after = wal_size_mb(MASTER_DB)
        self.state.update({"wal_mb": after, "last_checkpoint": utcnow(), "last_checkpoint_result": res})
        self._save_state()
        if before >= 64 or res.get("busy"):
            log(f"🧹 WAL checkpoint: {before} → {after} MB ({res})")

    # ── 7. dashboard supervision ──────────────────────────────────────────
    def ensure_dashboard(self) -> None:
        if not self.cfg.get("keep_dashboard_up", True):
            return
        port = int(self.cfg.get("dashboard_port", 8000))
        if dashboard_up(port):
            return
        server = os.path.join(AUTO, "dashboard", "server.py")
        logf = open(os.path.join(DATA, "dashboard_service.log"), "a", encoding="utf-8")
        cmd = [PY, server, str(port)]
        if IS_MAC and os.path.exists("/usr/bin/caffeinate"):
            cmd = ["/usr/bin/caffeinate", "-s"] + cmd
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        p = subprocess.Popen(cmd, cwd=AUTO, env=env, stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                             start_new_session=not IS_WIN)
        for _ in range(20):
            time.sleep(1)
            if dashboard_up(port):
                log(f"🟢 dashboard started (pid {p.pid}) on :{port}")
                return
        log(f"⚠️ dashboard pid {p.pid} did not answer on :{port} within 20 s — see data/dashboard_service.log")

    def _stop_dashboard(self) -> None:
        """Ask the server on the dashboard port to exit (used by restart_dashboard)."""
        try:
            from utils.proc import kill_pids_on_port
            kill_pids_on_port(int(self.cfg.get("dashboard_port", 8000)))
        except Exception as e:
            log(f"⚠️ stop dashboard: {e}")

    # ── 8. tunnels (optional) ─────────────────────────────────────────────
    def ensure_tunnels(self) -> None:
        port = int(self.cfg.get("dashboard_port", 8000))
        sub = (self.cfg.get("localtunnel_subdomain") or "").strip()
        if sub:
            self._ensure_tunnel("localtunnel",
                                ["npx", "--yes", "localtunnel", "--port", str(port), "--subdomain", sub],
                                url_hint=f"https://{sub}.loca.lt")
        if self.cfg.get("cloudflared"):
            cf = os.path.join(AUTO, "bin", "cloudflared")
            if not os.path.exists(cf):
                cf = shutil.which("cloudflared") or ""
            if cf:
                self._ensure_tunnel("cloudflared",
                                    [cf, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate",
                                     "--protocol", "http2"])

    def _ensure_tunnel(self, name: str, cmd: list[str], url_hint: str = "") -> None:
        p = self._tunnel_procs.get(name)
        if p and p.poll() is None:
            return
        if time.time() < self._tunnel_backoff.get(name, 0):
            return
        logp = os.path.join(DATA, f"{name}.log")
        # Ours is not running: before starting another, retire any tunnel for this port that
        # an earlier agent left behind, so there is exactly one of each.
        try:
            from utils.proc import _ps_lines
            own = {q.pid for q in self._tunnel_procs.values() if q and q.poll() is None}
            port = int(self.cfg.get("dashboard_port", 8000))
            for pid in stale_tunnel_pids(_ps_lines(), port, own):
                try:
                    os.kill(pid, signal.SIGTERM)
                    log(f"🧹 retired stale tunnel process {pid} (port {port})")
                except OSError:
                    pass
        except Exception as e:
            log(f"⚠️ stale-tunnel sweep: {e}")
        try:
            if os.path.exists(logp) and os.path.getsize(logp) > 5 * 1048576:
                open(logp, "w").close()
            logf = open(logp, "a", encoding="utf-8")
            start_at = os.path.getsize(logp) if os.path.exists(logp) else 0
            self._tunnel_procs[name] = subprocess.Popen(cmd, cwd=AUTO, stdout=logf, stderr=subprocess.STDOUT,
                                                        stdin=subprocess.DEVNULL, start_new_session=not IS_WIN)
            # exponential backoff between restarts: 30 s → 60 → 120 → … ≤ 10 min
            prev = self.state.get(f"{name}_backoff", 30)
            self._tunnel_backoff[name] = time.time() + prev
            self.state[f"{name}_backoff"] = min(prev * 2, 600)
            log(f"🌐 {name} started (pid {self._tunnel_procs[name].pid})")
            if url_hint:
                self._tunnel_urls[name] = url_hint
            # Both services print the address they actually gave us; the hint is only what we
            # asked for (localtunnel fell back to a random name 79 times on 21 Sep 2026).
            threading.Thread(target=self._scrape_tunnel_url, args=(name, logp, start_at), daemon=True).start()
        except Exception as e:
            log(f"⚠️ {name}: {e}")
            self._tunnel_backoff[name] = time.time() + 300

    def _scrape_tunnel_url(self, name: str, logp: str, start_at: int = 0) -> None:
        """Read only what THIS tunnel process wrote (from start_at) so an earlier run's address
        in the same log is never mistaken for the new one; then publish and announce it."""
        for _ in range(60):
            time.sleep(2)
            try:
                with open(logp, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(start_at)
                    url = pick_tunnel_url(name, f.read())
            except OSError:
                url = ""
            if url:
                self._tunnel_urls[name] = url
                try:
                    with open(os.path.join(DATA, f"{name}.url"), "w") as f:
                        f.write(url)
                except OSError:
                    pass
                self._announce_tunnel(name, url)
                return

    def _announce_tunnel(self, name: str, url: str) -> None:
        """Tell the owner on Telegram when a tunnel's public address changes. The quick tunnel
        and localtunnel hand out a new one on every restart, and nothing else surfaced it."""
        key = f"{name}_last_url"
        if not url or self.state.get(key) == url:
            return
        self.state[key] = url
        try:
            self._save_state()
        except Exception:
            pass
        try:
            _tg_send(f"🌐 Dashboard address ({name}): {url}")
        except Exception as e:
            log(f"⚠️ could not announce {name} url: {e}")

    # ── scheduler ─────────────────────────────────────────────────────────
    # ── DB sync between machines (journal → R2 → other machines) ──────────
    def _sync_summary(self) -> dict:
        try:
            if not os.path.exists(MASTER_DB):
                return {"installed": False}
            s = sync_journal.summary(MASTER_DB)
            s["last_push"] = self.state.get("last_sync_push")
            s["last_pull"] = self.state.get("last_sync_pull")
            return s
        except Exception as e:
            return {"error": str(e)[:80]}

    def sync_install(self) -> None:
        if not os.path.exists(MASTER_DB):
            log("ℹ️ no local DB yet — run `cloud_agent.py --seed-from-cloud` on a new machine")
            return
        r = sync_journal.install(MASTER_DB)
        log(f"🔗 sync journal on {len(r['tables'])} table(s) (role {self.role})")

    def sync_push(self) -> None:
        if not os.path.exists(MASTER_DB):
            return
        r = sync_journal.push(MASTER_DB, self.r2, self.host)
        if r["events"]:
            self.state["last_sync_push"] = utcnow()
            self._save_state()
            log(f"⇡ sync push: {r['events']} change(s) in {r['files']} batch(es)")

    def sync_pull(self) -> None:
        if not os.path.exists(MASTER_DB):
            return
        r = sync_journal.pull(MASTER_DB, self.r2, self.host)
        if r["files"]:
            self.state["last_sync_pull"] = utcnow()
            self._save_state()
            skipped = {t: sorted(c) for t, c in (r.get("skipped_cols") or {}).items()}
            log(f"⇣ sync pull: {r['events']} change(s) from {r['files']} batch(es)"
                + (f", {r['errors']} error(s)" if r["errors"] else "")
                + (f", columns unknown here: {skipped}" if skipped else ""))

    def run_once(self) -> None:
        self.sync_install()
        for name, fn in (("heartbeat", self.heartbeat), ("live_logs", self.live_logs),
                         ("commands", self.commands), ("crm_replay", self.crm_replay),
                         ("sync_push", self.sync_push), ("sync_pull", self.sync_pull)):
            if self.role != "master" and name in MASTER_ONLY:
                continue
            try:
                fn()
                log(f"✓ {name}")
            except Exception as e:
                log(f"⚠️ {name}: {e}")
        self.ensure_dashboard()
        try:
            self.slim_sync(force=True)
        except Exception as e:
            log(f"⚠️ slim_sync: {e}")

    def daemon(self) -> None:
        if not self.r2.available():
            log("❌ R2 credentials not configured — set R2_* env vars, data/All_Tokens/r2.json, "
                "or the LaunchAgent EnvironmentVariables")
        log(f"🚀 cloud agent up on '{self.host}' as {self.role.upper()} (git {self.git_sha}, py {platform.python_version()})")
        try:
            self.sync_install()
        except Exception as e:
            log(f"⚠️ sync journal install: {e}")
        c = self.cfg
        sched = {
            "sync_push": [float(c.get("sync_s", 60)), 20.0, self.sync_push, True],
            "sync_pull": [float(c.get("sync_s", 60)), 40.0, self.sync_pull, True],
            "heartbeat": [float(c["heartbeat_s"]), 0.0, self.heartbeat, False],
            "live_logs": [float(c["livelog_s"]), 0.0, self.live_logs, False],
            "commands": [float(c["command_s"]), 0.0, self.commands, False],
            "crm_replay": [float(c["crm_s"]), 0.0, self.crm_replay, False],
            "slim_sync": [float(c["slim_interval_s"]), 5.0, self.slim_sync, True],
            "backup": [float(c["backup_interval_s"]), 60.0, self.backup, True],
            "dashboard": [30.0, 2.0, self.ensure_dashboard, True],
            "tunnels": [20.0, 5.0, self.ensure_tunnels, False],
            "prune": [1800.0, 120.0, self._prune, True],
            "wal_checkpoint": [float(c.get("wal_checkpoint_s", 1800)), 240.0, self.wal_checkpoint, True],
        }
        t0 = time.time()
        for v in sched.values():
            v[1] = t0 + v[1]
        while True:
            now = time.time()
            for name, (interval, nxt, fn, threaded) in sched.items():
                if now < nxt:
                    continue
                sched[name][1] = now + interval
                if not self.r2.available() and name not in ("dashboard", "tunnels", "wal_checkpoint"):
                    continue
                if self.role != "master" and name in MASTER_ONLY:
                    continue                     # only the master feeds the dashboard / backups
                if threaded:
                    self._single_flight(name, fn)
                else:
                    try:
                        fn()
                    except Exception as e:
                        log(f"⚠️ {name}: {e}")
            time.sleep(1)

    def _prune(self) -> None:
        self.r2.prune(PREFIX_CMD_DONE, 300)
        self.r2.prune(PREFIX_CRM_APPLIED, 2000)
        if self.role == "master":
            try:
                n = sync_journal.prune_remote(self.r2, days=14)
                if n:
                    log(f"🧹 pruned {n} old sync batch(es)")
            except Exception as e:
                log(f"⚠️ sync prune: {e}")


# ── install / status ─────────────────────────────────────────────────────
def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install() -> None:
    if IS_MAC:
        import plistlib
        # Carry the R2 keys over from whichever previous agent has them — they are
        # copied plist → plist and never printed or written anywhere else.
        env = {"PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
               "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
        for lbl in (LABEL, *LEGACY_LABELS):
            p = os.path.expanduser(f"~/Library/LaunchAgents/{lbl}.plist")
            if os.path.exists(p):
                try:
                    with open(p, "rb") as f:
                        old = plistlib.load(f).get("EnvironmentVariables", {}) or {}
                    for k in ("R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_BUCKET", "R2_ENDPOINT"):
                        if old.get(k) and k not in env:
                            env[k] = old[k]
                except Exception:
                    pass
        for lbl in LEGACY_LABELS:                       # retire the old daemons
            _launchctl("bootout", f"{_domain()}/{lbl}")
            p = os.path.expanduser(f"~/Library/LaunchAgents/{lbl}.plist")
            if os.path.exists(p):
                os.rename(p, p + ".retired")
                print(f"  retired {lbl}")
        _launchctl("bootout", f"{_domain()}/{LABEL}")
        args = [PY, os.path.abspath(__file__), "--daemon"]
        if os.path.exists("/usr/bin/caffeinate"):
            args = ["/usr/bin/caffeinate", "-s"] + args
        plist = {"Label": LABEL, "ProgramArguments": args, "WorkingDirectory": AUTO, "RunAtLoad": True,
                 "KeepAlive": True, "ThrottleInterval": 15, "StandardOutPath": AGENT_LOG,
                 "StandardErrorPath": AGENT_LOG, "EnvironmentVariables": env}
        os.makedirs(os.path.dirname(PLIST), exist_ok=True)
        os.makedirs(os.path.dirname(AGENT_LOG), exist_ok=True)
        with open(PLIST, "wb") as f:
            plistlib.dump(plist, f)
        os.chmod(PLIST, 0o600)
        r = _launchctl("bootstrap", _domain(), PLIST)
        if r.returncode != 0:
            r = _launchctl("load", "-w", PLIST)
        print(f"  [OK] LaunchAgent {LABEL} installed → log {AGENT_LOG}")
        print("  R2 keys:", "carried over" if env.get("R2_ACCESS_KEY")
              else "NOT FOUND — add R2_* to the plist EnvironmentVariables or data/All_Tokens/r2.json")
    elif IS_WIN:
        pyw = PY.replace("python.exe", "pythonw.exe")
        tr = f'"{pyw if os.path.exists(pyw) else PY}" "{os.path.abspath(__file__)}" --daemon'
        r = subprocess.run(["schtasks", "/Create", "/TN", "ZuhraCloudAgent", "/TR", tr, "/SC", "ONLOGON",
                            "/RL", "LIMITED", "/F"], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        subprocess.run(["schtasks", "/Run", "/TN", "ZuhraCloudAgent"], capture_output=True)
        print("  [OK] Scheduled task ZuhraCloudAgent installed (runs at logon). "
              "Put R2 keys in data/All_Tokens/r2.json")
    else:
        print("  install: use a systemd --user unit that runs: python scripts/cloud_agent.py --daemon")


def uninstall() -> None:
    if IS_MAC:
        _launchctl("bootout", f"{_domain()}/{LABEL}")
        if os.path.exists(PLIST):
            os.remove(PLIST)
        print(f"  [OK] {LABEL} removed")
    elif IS_WIN:
        subprocess.run(["schtasks", "/Delete", "/TN", "ZuhraCloudAgent", "/F"], capture_output=True)
        print("  [OK] ZuhraCloudAgent task removed")


def status() -> None:
    s = R2Store()
    print(f"host: {machine_id()}   R2 configured: {s.available()}   bucket: {s.bucket}")
    if IS_MAC:
        r = _launchctl("list")
        print("LaunchAgent:", "LOADED" if LABEL in (r.stdout or "") else "not loaded")
    st = launcher_state()
    print(f"launcher on this machine: {'alive' if st.get('alive') else 'not running'} "
          f"(procs: {sorted((st.get('procs') or {}).keys())})")
    if not s.available():
        return
    print("lease holder:", s.lease_holder())
    for m in s.machines():
        ln = m.get("launcher", {})
        sy = m.get("sync") or {}
        print(f"  • {m.get('host'):18} {'ONLINE ' if m.get('online') else 'offline'} age {m.get('age_s')}s  "
              f"role={m.get('role', '?')} launcher={'up' if ln.get('alive') else 'down'} placers={m.get('placers_running')} "
              f"git={m.get('git_sha')} dash={'up' if (m.get('dashboard') or {}).get('local_up') else 'down'} "
              f"sync(pending={sy.get('pending')}, pull={sy.get('last_pull')})")
    man = s.get_json(KEY_DB_MANIFEST) or {}
    print("slim DB in R2:", man.get("db_mb"), "MB, uploaded", man.get("uploaded_at"), "from", man.get("host"))
    print("usage:", json.dumps(s.usage()))


def _set_role(role: str) -> None:
    role = role.strip().lower()
    if role not in ("master", "secondary"):
        raise SystemExit("role must be master or secondary")
    cfg = {}
    try:
        with open(AGENT_CFG, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except Exception:
        pass
    cfg["role"] = role
    os.makedirs(os.path.dirname(AGENT_CFG), exist_ok=True)
    with open(AGENT_CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print(f"role set to {role} in {AGENT_CFG} — restart the agent (or `launchctl kickstart`) to apply")


def main() -> None:
    cfg = load_cfg()
    if "--role" in sys.argv:
        _set_role(sys.argv[sys.argv.index("--role") + 1] if len(sys.argv) > sys.argv.index("--role") + 1 else "")
        return
    if "--seed-from-cloud" in sys.argv:
        s = R2Store()
        print("downloading the slim DB from R2 as the local database…")
        print("→", sync_journal.seed_from_cloud(MASTER_DB, s))
        print(json.dumps(sync_journal.install(MASTER_DB)))
        return
    if "--sync-now" in sys.argv:
        a = Agent(cfg)
        a.sync_install(); a.sync_push(); a.sync_pull()
        print(json.dumps(a._sync_summary(), indent=1))
        return
    if "--install" in sys.argv:
        install()
        return
    if "--uninstall" in sys.argv:
        uninstall()
        return
    if "--status" in sys.argv:
        status()
        return
    if "--check" in sys.argv:
        s = R2Store()
        print("R2 configured:", s.available(), "| bucket:", s.bucket)
        if s.available():
            print(json.dumps(s.usage(), indent=1))
        return
    # rotate an oversized agent log (launchd appends; truncation is safe)
    try:
        if os.path.exists(AGENT_LOG) and os.path.getsize(AGENT_LOG) > 20 * 1048576:
            open(AGENT_LOG, "w").close()
    except OSError:
        pass
    agent = Agent(cfg)
    if "--once" in sys.argv:
        agent.run_once()
        return
    agent.daemon()


if __name__ == "__main__":
    main()
