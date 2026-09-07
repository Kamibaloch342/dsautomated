"""
dashboard/ops.py — the "operations" layer behind the v2 dashboard.

Everything here works in BOTH modes:

  LOCAL  (default)  — server.py runs on the machine that owns the master DB.
                      Machines/logs/commands come from R2 (plus this machine's
                      own launcher_state.json / console_log.txt read directly,
                      which is fresher than the R2 copy).
  CLOUD  (CLOUD_MODE=1, e.g. Render) — no master DB. The slim DB is downloaded
                      from R2 (db/dashboard.db.gz) and refreshed every 2 minutes
                      whenever the manifest changes; CRM writes are pushed to R2
                      as events and replayed into the master DB by the cloud
                      agent of the machine that owns it.

Public API used by server.py:
    machines(), live_log(host), send_command(action, target, args, author),
    command_results(limit), config_get(), config_set(patch, author),
    order_timeline(db, order_id), crm_history(db, order_id, limit),
    reconcile(db, days), cloud_status(), cloud_db_refresh(force),
    record_history(db, order_id, kind, field, old, new, author, host),
    push_crm_event(...)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
if AUTO not in sys.path:
    sys.path.insert(0, AUTO)

from utils.r2_store import (R2Store, machine_id, utcnow, KEY_DB_MANIFEST)  # noqa: E402

CLOUD_MODE = os.environ.get("CLOUD_MODE", "").strip().lower() in ("1", "true", "yes")
DATA = os.path.join(AUTO, "data")
LAUNCHER_STATE = os.path.join(DATA, "launcher_state.json")
CONSOLE_LOG = os.path.join(DATA, "console_log.txt")
CONTROL_QUEUE = os.path.join(DATA, "control", "queue")
CONTROL_DONE = os.path.join(DATA, "control", "done")
PRIORITY_REQUEST = os.path.join(DATA, "control", "priority_check.request")   # legacy name (unused)


def priority_request_paths(args):
    """One request file per coupon profile. profile = HS_COUPON / DM_COUPON (also
    accepts HS, DM, HOANK, Hathoro); missing or 'all' → both stores."""
    prof = str((args or {}).get("profile") or "").strip().upper()
    if prof.startswith("HS") or prof.startswith("HOANK"):
        profs = ["HS_COUPON"]
    elif prof.startswith("DM") or prof.startswith("HATH"):
        profs = ["DM_COUPON"]
    else:
        profs = ["HS_COUPON", "DM_COUPON"]
    return [os.path.join(DATA, "control", f"priority_check.{p}.request") for p in profs]
CFG_PATH = os.path.join(AUTO, "bot_config.json")
KEY_SLIM_GZ = "db/dashboard.db.gz"
CLOUD_DB_PATH = os.environ.get("CLOUD_DB_PATH") or os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "zuhra_dashboard.db")

_r2: R2Store | None = None
_r2_checked = 0.0


def r2() -> R2Store | None:
    """Shared R2 client, or None when no credentials are configured."""
    global _r2, _r2_checked
    if _r2 is None and time.time() - _r2_checked > 60:
        _r2_checked = time.time()
        s = R2Store()
        _r2 = s if s.available() else None
    return _r2


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


def _age(iso: str | None):
    if not iso:
        return None
    try:
        t = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
        if iso.endswith("Z"):
            return (datetime.now(timezone.utc) - t.replace(tzinfo=timezone.utc)).total_seconds()
        return (datetime.now() - t).total_seconds()
    except Exception:
        return None


def _tail(path: str, n: int = 400, max_bytes: int = 256 * 1024) -> list[str]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            chunk = f.read()
        lines = chunk.decode("utf-8", errors="replace").splitlines()
        if size > max_bytes and lines:
            lines = lines[1:]
        return lines[-n:]
    except Exception:
        return []


# ── cloud-mode DB ─────────────────────────────────────────────────────────
_cloud = {"fingerprint": None, "refreshed_at": None, "manifest": None, "error": None,
          "lock": threading.Lock(), "thread": None}


def cloud_db_refresh(force: bool = False) -> dict:
    s = r2()
    if not s:
        _cloud["error"] = "R2 not configured"
        return cloud_status()
    with _cloud["lock"]:
        try:
            man = s.get_json(KEY_DB_MANIFEST) or {}
            fp = man.get("fingerprint") or man.get("uploaded_at")
            if force or fp != _cloud["fingerprint"] or not os.path.exists(CLOUD_DB_PATH):
                ok = s.get_file(man.get("key") or KEY_SLIM_GZ, CLOUD_DB_PATH, gunzip=True)
                if ok:
                    try:
                        with sqlite3.connect(CLOUD_DB_PATH, timeout=15) as conn:
                            conn.execute("PRAGMA journal_mode=WAL")
                            conn.execute("PRAGMA synchronous=NORMAL")
                            conn.execute("PRAGMA busy_timeout=30000")
                    except Exception:
                        pass
                    _cloud["fingerprint"] = fp
                    _cloud["refreshed_at"] = utcnow()
                    _cloud["manifest"] = man
                    _cloud["error"] = None
                else:
                    _cloud["error"] = "slim DB not found in R2 yet"
        except Exception as e:
            _cloud["error"] = str(e)[:300]
    return cloud_status()


def cloud_db_start_refresher(interval: int = 120) -> None:
    if _cloud["thread"]:
        return

    def loop():
        while True:
            time.sleep(interval)
            try:
                cloud_db_refresh()
            except Exception:
                pass
    t = threading.Thread(target=loop, daemon=True, name="cloud-db-refresher")
    t.start()
    _cloud["thread"] = t


def cloud_status() -> dict:
    return {"cloud_mode": CLOUD_MODE, "db_path": CLOUD_DB_PATH if CLOUD_MODE else None,
            "db_mb": round(os.path.getsize(CLOUD_DB_PATH) / 1048576, 1)
            if CLOUD_MODE and os.path.exists(CLOUD_DB_PATH) else None,
            "manifest": _cloud["manifest"], "refreshed_at": _cloud["refreshed_at"],
            "error": _cloud["error"], "r2": bool(r2()), "host": machine_id()}


# ── machines / logs / commands ────────────────────────────────────────────
def _local_launcher() -> dict:
    st = _read_json(LAUNCHER_STATE, {}) or {}
    a = _age(st.get("at"))
    st["alive"] = a is not None and a < 20
    st["age_s"] = None if a is None else int(a)
    return st


def machines() -> list[dict]:
    out = []
    s = r2()
    if s:
        try:
            out = s.machines()
        except Exception as e:
            out = [{"host": machine_id(), "online": False, "error": str(e)[:200]}]
    me = machine_id()
    if not CLOUD_MODE:
        # this machine: prefer the fresh local view over the ≤15 s old R2 copy
        st = _local_launcher()
        mine = next((m for m in out if m.get("host") == me), None)
        if mine is None:
            mine = {"host": me, "online": True, "age_s": 0, "local": True}
            out.insert(0, mine)
        mine["local"] = True
        mine.setdefault("launcher", {})
        mine["launcher"].update({"alive": bool(st.get("alive")), "pid": st.get("pid"),
                                 "procs": st.get("procs", {}) if st.get("alive") else {},
                                 "started_at": st.get("started_at"), "headless": st.get("headless"),
                                 "closing": st.get("closing", {})})
        mine["placers_running"] = sorted(n.replace("orders_", "") for n in (mine["launcher"]["procs"] or {})
                                         if n.startswith("orders_"))
    for m in out:
        m["is_me"] = m.get("host") == me
    return out


def live_log(host: str | None = None, n: int = 400) -> dict:
    me = machine_id()
    host = host or me
    if host == me and not CLOUD_MODE:
        st = _local_launcher()
        return {"host": me, "at": utcnow(), "lines": _tail(CONSOLE_LOG, n),
                "launcher_alive": bool(st.get("alive")),
                "procs": sorted((st.get("procs") or {}).keys()) if st.get("alive") else [],
                "source": "local"}
    s = r2()
    if not s:
        return {"host": host, "lines": [], "error": "R2 not configured"}
    d = s.live_log(host) or {"host": host, "lines": []}
    d["source"] = "r2"
    d["age_s"] = _age(d.get("at"))
    return d


_LAUNCHER_ACTIONS = {"stop_all", "stop_orders", "start_orders", "start_all_orders", "stop_process",
                     "start_tracking", "stop_tracking", "run_closing", "stop_closing", "run_messages",
                     "reload_config", "shutdown_launcher", "set_headless"}
_AGENT_ACTIONS = {"ping", "start_launcher", "sync_now", "backup_now", "restart_dashboard",
                  "run_priority_check", "set_config"}
ACTIONS = sorted(_LAUNCHER_ACTIONS | _AGENT_ACTIONS)


def _resolve_target(target: str | None, action: str) -> tuple[str | None, str]:
    """Pick the machine a command should go to when the caller didn't say."""
    if target:
        return target, "explicit"
    ms = machines()
    s = r2()
    holder = None
    try:
        holder = s.lease_holder() if s else None
    except Exception:
        holder = None
    if holder:
        return holder, "placement lease holder"
    me = machine_id()
    live = [m for m in ms if (m.get("launcher") or {}).get("alive")]
    if live:
        mine = next((m for m in live if m.get("host") == me), None)
        return (mine or live[0])["host"], "launcher is running there"
    if not CLOUD_MODE:
        return me, "this machine"
    online = [m for m in ms if m.get("online")]
    if online:
        return online[0]["host"], "first online machine"
    return None, "no machine online"


def send_command(action: str, target: str | None, args: dict | None, author: str) -> dict:
    if action not in ACTIONS:
        return {"ok": False, "error": f"unknown action '{action}'", "actions": ACTIONS}
    target, why = _resolve_target(target, action)
    if not target:
        return {"ok": False, "error": "no machine is online to receive the command"}
    args = args or {}
    me = machine_id()
    # Fast path: local machine, no R2 round-trip needed for launcher actions.
    if target == me and not CLOUD_MODE and action in _LAUNCHER_ACTIONS:
        st = _local_launcher()
        if not st.get("alive"):
            if action.startswith("stop") or action == "shutdown_launcher":
                return {"ok": True, "id": None, "target": target, "result": "launcher is not running — nothing to stop"}
            return {"ok": False, "target": target, "error": "launcher is not running on this machine (use start_launcher)"}
        cid = R2Store.new_id()
        _write_json(os.path.join(CONTROL_QUEUE, f"{cid}.json"),
                    {"id": cid, "action": action, "target": target, "args": args, "author": author,
                     "created_at": utcnow()})
        done = os.path.join(CONTROL_DONE, f"{cid}.json")
        for _ in range(24):
            time.sleep(0.5)
            if os.path.exists(done):
                res = _read_json(done, {}) or {}
                try:
                    os.remove(done)
                except OSError:
                    pass
                return {"ok": bool(res.get("ok")), "id": cid, "target": target, "result": res.get("result", "")}
        return {"ok": True, "id": cid, "target": target, "result": "queued (launcher busy — check results)"}
    if target == me and not CLOUD_MODE and action == "run_priority_check":
        for _p in priority_request_paths(args):
            _write_json(_p, {"author": author, "at": utcnow(), "args": args})
    if target == me and not CLOUD_MODE and action == "set_config":
        return config_set(args, author)
    s = r2()
    if not s:
        return {"ok": False, "error": "R2 not configured — cannot reach other machines"}
    cmd = s.enqueue_command(action, target, args, author)
    return {"ok": True, "id": cmd["id"], "target": target, "why": why,
            "result": f"queued for {target} (the agent there polls every 10 s)"}


def command_results(limit: int = 40) -> list[dict]:
    s = r2()
    if not s:
        return []
    try:
        return s.command_results(limit)
    except Exception as e:
        return [{"error": str(e)[:200]}]


# ── config (routing / chrome / automation) ────────────────────────────────
_EDITABLE = {
    "routing.deciding_min_margin_pct": float, "routing.supervise_unlabelled": bool,
    "routing.supervise_min_cart_for_regular": float, "routing.auto_learn_promo_ineligible": bool,
    "routing.promo_ineligible_min_fails": int, "routing.promo_ineligible_asins": list,
    "chrome.headless": bool,
    "automation.auto_closing.enabled": bool, "automation.auto_closing.times": list,
    "automation.auto_closing.accounts": list, "automation.auto_closing.every_hours": float,
    "automation.priority_check.enabled": bool, "automation.priority_check.every_hours": float,
    "automation.priority_check.auto_assign": bool, "automation.priority_check.max_per_run": int,
    "deciding_coupon_min": float, "pi_target_margin": float,
}


def config_get() -> dict:
    cfg = _read_json(CFG_PATH, {}) or {}
    return {"routing": cfg.get("routing", {}), "chrome": cfg.get("chrome", {}),
            "automation": cfg.get("automation", {}),
            "deciding_coupon_min": cfg.get("deciding_coupon_min"), "pi_target_margin": cfg.get("pi_target_margin"),
            "profiles": {k: {kk: v.get(kk) for kk in ("target_label", "min_margin", "target_label_2", "min_margin_2",
                                                      "labels_only", "rush_mode", "payment_method",
                                                      "skip_priority_coupons")}
                         for k, v in (cfg.get("profiles") or {}).items()},
            "editable": sorted(_EDITABLE), "cloud_mode": CLOUD_MODE, "host": machine_id()}


def config_set(patch: dict, author: str = "dashboard") -> dict:
    """patch = {"path": "automation.auto_closing.enabled", "value": true}  or  {"set": {path: value, ...}}"""
    if isinstance(patch.get("set"), dict):
        items = patch["set"]
    else:
        if not patch.get("path") or "value" not in patch:
            return {"ok": False, "error": "path and value required"}
        items = {patch["path"]: patch["value"]}
    if CLOUD_MODE:
        s = r2()
        if not s:
            return {"ok": False, "error": "R2 not configured"}
        target, _ = _resolve_target(None, "set_config")
        cmd = s.enqueue_command("set_config", target, {"set": items}, author)
        return {"ok": True, "id": cmd["id"], "target": target, "result": "queued for the machine that owns bot_config.json"}
    cfg = _read_json(CFG_PATH, {}) or {}
    applied = {}
    for path, value in items.items():
        if path not in _EDITABLE:
            return {"ok": False, "error": f"'{path}' is not editable here"}
        typ = _EDITABLE[path]
        try:
            if typ is bool:
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
            elif typ is list:
                value = value if isinstance(value, list) else [x.strip() for x in str(value).split(",") if x.strip()]
            else:
                value = typ(value)
        except Exception:
            return {"ok": False, "error": f"bad value for {path}"}
        node = cfg
        parts = path.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
        applied[path] = value
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)
    os.replace(tmp, CFG_PATH)
    return {"ok": True, "applied": applied, "by": author}


# ── DB helpers ────────────────────────────────────────────────────────────
def _conn(db: str) -> sqlite3.Connection:
    c = sqlite3.connect(db, timeout=8)
    c.execute("PRAGMA busy_timeout=8000")
    c.execute("PRAGMA query_only=ON")
    c.row_factory = sqlite3.Row
    return c


def _has_table(c: sqlite3.Connection, name: str) -> bool:
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)).fetchone())


def _cols(c: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [r[1] for r in c.execute(f"PRAGMA table_info([{table}])")]
    except Exception:
        return []


_DT_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
               "%m/%d/%Y %I:%M %p", "%A, %m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%m/%d/%Y",
               "%A, %m/%d/%Y", "%a, %b %d, %Y", "%A, %d %B %Y", "%b %d, %Y", "%d %B %Y", "%a, %b %d")


def parse_dt(v) -> datetime | None:
    """Tolerant date parser for the many formats in closing_orders (ISO, US, FedEx
    '7/14/2026 10:48 am', Amazon 'Thu, Aug 27, 2026', …)."""
    if not v:
        return None
    s = str(v).strip()
    if "|" in s:
        s = s.split("|")[0].strip()
    s = s.replace("Z", "").replace("  ", " ")
    s = s.replace(" am", " AM").replace(" pm", " PM")
    for fmt in _DT_FORMATS:
        try:
            d = datetime.strptime(s, fmt)
            if d.year == 1900:                       # '%a, %b %d' has no year → assume this year
                d = d.replace(year=datetime.now().year)
            return d
        except Exception:
            continue
    # last resort: ISO prefix (e.g. '2026-06-05 15:30:00.123')
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except Exception:
            return None


def _fmt_date(v) -> str:
    d = parse_dt(v)
    if not d:
        return str(v or "")
    if d.hour or d.minute:
        return d.strftime("%A, %-d %B %Y %H:%M")
    return d.strftime("%A, %-d %B %Y")


# ── order timeline ────────────────────────────────────────────────────────
def order_timeline(db: str, order_id: str) -> dict:
    """Everything known about one Amazon order, as a chronological timeline plus a
    summary — the 'order-centric' view. Reads only tables that exist."""
    c = _conn(db)
    try:
        ev = []
        o = {}
        src = "v_orders_all" if _has_table(c, "v_orders_all") else "closing_orders"
        r = c.execute(f"SELECT * FROM {src} WHERE amazon_order_id=?", (order_id,)).fetchone()
        if r:
            o = dict(r)
        p = {}
        if _has_table(c, "order_placer_orders"):
            r = c.execute("SELECT * FROM order_placer_orders WHERE amazon_order_id=?", (order_id,)).fetchone()
            if r:
                p = dict(r)
        if not o and not p:
            return {"order_id": order_id, "found": False, "events": [], "summary": {}}

        def add(at, kind, title, detail="", icon="•", ok=True):
            ev.append({"at": at, "at_display": _fmt_date(at) if at else "", "kind": kind, "title": title,
                       "detail": detail, "icon": icon, "ok": ok})

        g = lambda k, d="": (o.get(k) if o.get(k) not in (None, "") else p.get(k, d))  # noqa: E731
        add(g("purchase_date"), "amazon", "Amazon sale",
            f"{g('ship_name')} · {g('ship_city')}, {g('ship_state')} {g('ship_postal_code')} · ASIN {g('asin')} · "
            f"SKU {g('sku')} · qty {g('quantity')} · earnings ${g('earnings') or 0} · ship by {_fmt_date(g('ship_by_date'))} · "
            f"deliver by {_fmt_date(g('deliver_by_date'))}", "🛒")
        wb = g("wb_order_id") or p.get("wb_order_id")
        if wb:
            add(p.get("placed_at") or g("zoro_placed_pkt") or g("zoro_order_date"), "zoro", f"Zoro order placed · {wb}",
                f"profile {g('profile_name') or p.get('profile_name')} · cost ${g('zoro_order_total') or p.get('zoro_order_total') or 0} · "
                f"tax ${g('zoro_tax') or p.get('zoro_tax') or 0} · coupon {g('coupon_used') or p.get('coupon_used') or '—'} "
                f"(−${g('coupon_savings') or p.get('coupon_savings') or 0}) · paid with {g('zoro_payment_method') or '—'}"
                + (f" · ALT for {p.get('main_sku')}" if p.get("alt_used") else ""), "📦")
        elif o.get("merchant_order_id") or p.get("wb_order_id"):
            add(g("purchase_date"), "zoro", f"Placement outcome: {o.get('merchant_order_id') or ''}", "", "⚠️", False)
        if g("shipped_email_time") or g("tracking_ids"):
            add(g("shipped_email_time") or g("zoro_shipped_pkt"), "ship", "Zoro shipped",
                f"tracking {g('tracking_ids') or '—'} ({g('carrier') or 'FedEx'}) · "
                f"{'ON TIME' if str(g('on_time') or g('shipped_on_time') or '').lower() in ('yes', '1', 'true', 'ontime', 'early') else (g('on_time') or '')}", "🚚")
        if g("tracking_upload_date") or str(g("tracking_id_uploaded")).lower() in ("yes", "1", "true"):
            add(g("tracking_upload_date"), "amazon", "Tracking uploaded to Amazon", f"{g('tracking_carrier_uploaded') or ''}", "📬")
        if g("delivery_status"):
            st = str(g("delivery_status"))
            add(g("delivery_date") or g("last_scan_time"), "fedex", f"FedEx: {st}",
                f"{('left at ' + str(g('left_at'))) if g('left_at') else ''} {('signed ' + str(g('signed_by'))) if g('signed_by') else ''} "
                f"{('· ' + str(g('last_scan_location'))) if g('last_scan_location') else ''}".strip(),
                "🏠" if st.lower().startswith(("deliver", "alldeliver")) else "🛰️", st.lower().startswith(("deliver", "alldeliver")))
        if str(g("delivered_msg_sent") or g("customer_messaged")).lower() in ("yes", "1", "true"):
            add(g("delivery_date"), "message", "Delivery check-in sent", str(g("customer_message_text") or "")[:160], "💬")
        if str(g("review_request_sent") or g("review_requested")).lower() in ("yes", "1", "true"):
            add(g("delivery_date"), "message", "Review requested", "", "⭐")
        if str(g("customer_inquiry")).lower() in ("yes", "1", "true") or (g("customer_inquiry_count") or 0):
            add(g("delivery_date"), "message", f"Buyer inquiry ({g('customer_inquiry_count') or 1})", "", "❓", False)
        if str(g("return_requested")).lower() in ("yes", "1", "true"):
            add(g("refund_date") or g("delivery_date"), "return", "Return requested", str(g("return_reason") or "") + " " + str(g("return_customer_comment") or ""), "↩️", False)
        if str(g("refund_done")).lower() in ("yes", "1", "true"):
            add(g("refund_date"), "return", f"Refund done · ${g('refund_amount') or 0}", str(g("refund_reason") or ""), "💸", False)
        if g("feedback_rating"):
            add(g("feedback_date"), "feedback", f"Feedback {g('feedback_rating')}★", str(g("feedback_text") or "")[:200], "🌟")
        # messages mirrored from Gmail
        if _has_table(c, "order_messages"):
            for m in c.execute("SELECT * FROM order_messages WHERE amazon_order_id=? ORDER BY timestamp", (order_id,)):
                m = dict(m)
                add(m.get("timestamp"), "message", f"{(m.get('direction') or '').title()} {m.get('message_type') or 'message'}: {m.get('subject') or ''}",
                    str(m.get("body_snippet") or "")[:300], "✉️")
        if _has_table(c, "order_returns"):
            for m in c.execute("SELECT * FROM order_returns WHERE amazon_order_id=?", (order_id,)):
                m = dict(m)
                at = next((m[k] for k in ("timestamp", "created_at", "return_date", "email_timestamp") if m.get(k)), None)
                add(at, "return", "Return/refund record", ", ".join(f"{k}={v}" for k, v in m.items() if v not in (None, "") and k not in ("id", "amazon_order_id"))[:300], "↩️", False)
        if _has_table(c, "action_log"):
            for m in c.execute("SELECT * FROM action_log WHERE amazon_order_id=? ORDER BY performed_at", (order_id,)):
                m = dict(m)
                add(m.get("performed_at"), "action", f"Action: {m.get('action_type')} ({m.get('status')})", str(m.get("detail") or "")[:200], "⚙️", m.get("status") == "success")
        if _has_table(c, "crm_notes"):
            for m in c.execute("SELECT * FROM crm_notes WHERE amazon_order_id=? ORDER BY created_at, id", (order_id,)):
                m = dict(m)
                add(m.get("created_at"), "note", f"Note by {m.get('author') or '?'}", str(m.get("note") or ""), "📝")
        if _has_table(c, "crm_history"):
            for m in c.execute("SELECT * FROM crm_history WHERE amazon_order_id=? AND kind<>'note' ORDER BY created_at, id", (order_id,)):
                m = dict(m)
                add(m.get("created_at"), "case", f"{m.get('field') or m.get('kind')} → {m.get('new_value') or '—'}",
                    f"was {m.get('old_value') or '—'} · by {m.get('author') or '?'}" + (f" @ {m.get('host')}" if m.get("host") else ""), "🗂️")
        # sort chronologically; undated events keep insertion order at the end
        def key(e):
            d = parse_dt(e["at"])
            return (0, d.timestamp()) if d else (1, 0)
        ev.sort(key=key)
        details = {}
        if _has_table(c, "amazon_order_details"):
            r = c.execute("SELECT * FROM amazon_order_details WHERE amazon_order_id=?", (order_id,)).fetchone()
            if r:
                details = {k: v for k, v in dict(r).items() if v not in (None, "")}
        crm = {}
        if _has_table(c, "order_crm"):
            r = c.execute("SELECT * FROM order_crm WHERE amazon_order_id=?", (order_id,)).fetchone()
            if r:
                crm = dict(r)
        summary = {k: g(k) for k in ("account", "profile_name", "order_label", "order_status", "asin", "sku", "title",
                                     "quantity", "earnings", "amazon_fees", "zoro_order_total", "zoro_tax", "profit_dollars",
                                     "profit_percent", "net_profit_after_refund", "coupon_used", "coupon_savings",
                                     "wb_order_id", "tracking_ids", "delivery_status", "delivery_date", "ship_name",
                                     "ship_street", "ship_city", "ship_state", "ship_postal_code", "purchase_date",
                                     "ship_by_date", "deliver_by_date", "return_requested", "refund_done",
                                     "case_status", "case_type", "case_resolution", "flag_color", "tags", "comments",
                                     "manual_review", "placed_via")}
        summary.update({k: v for k, v in crm.items() if v not in (None, "") and k != "amazon_order_id"})
        summary["placed_at"] = p.get("placed_at")
        summary["pending"] = not bool(o) or o.get("order_label") == "Pending"
        return {"order_id": order_id, "found": True, "events": ev, "summary": summary, "details": details,
                "placer": {k: v for k, v in p.items() if v not in (None, "") and k != "zoro_response_json"}}
    finally:
        c.close()


def crm_history(db: str, order_id: str | None = None, limit: int = 200) -> list[dict]:
    c = _conn(db)
    try:
        if not _has_table(c, "crm_history"):
            return []
        if order_id:
            rows = c.execute("SELECT * FROM crm_history WHERE amazon_order_id=? ORDER BY id DESC LIMIT ?", (order_id, limit))
        else:
            rows = c.execute("SELECT * FROM crm_history ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
    finally:
        c.close()


def record_history(db: str, order_id: str, kind: str, field: str | None, old, new, author: str,
                   host: str | None = None, event_id: str | None = None) -> None:
    """Append-only CRM audit (local mode). The cloud agent writes the same rows when
    it replays cloud events, so both paths end in one table."""
    c = sqlite3.connect(db, timeout=30)
    try:
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("CREATE TABLE IF NOT EXISTS crm_history(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                  "amazon_order_id TEXT NOT NULL, event_id TEXT UNIQUE, kind TEXT NOT NULL, field TEXT, "
                  "old_value TEXT, new_value TEXT, author TEXT, host TEXT, created_at TEXT DEFAULT (datetime('now')))")
        c.execute("CREATE INDEX IF NOT EXISTS ix_crm_history_oid ON crm_history(amazon_order_id, id)")
        c.execute("INSERT OR IGNORE INTO crm_history(amazon_order_id, event_id, kind, field, old_value, new_value, author, host, created_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?)",
                  (order_id, event_id, kind, field, None if old is None else str(old), None if new is None else str(new),
                   author, host or machine_id(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
    finally:
        c.close()


def push_crm_event(op: str, order_id: str, data: dict, author: str) -> dict:
    """Cloud mode: hand a CRM write to the machine that owns the master DB."""
    s = r2()
    if not s:
        return {"ok": False, "error": "R2 not configured"}
    ev = s.push_crm_event({"table": "crm", "op": op, "key": order_id, "data": data, "author": author,
                           "host": "cloud:" + machine_id()})
    return {"ok": True, "event_id": ev["id"], "queued": True}


# ── placer decisions (run logs) ───────────────────────────────────────────
def placer_events(hours: float = 24, statuses: list[str] | None = None, limit: int = 500) -> dict:
    """Recent per-order decisions from the placer run logs (data/rajar_logs/run_*.csv):
    SUPERVISED, ROUTED_TO_RESELLER, ROUTED_TO_COUPON, SUPERVISOR_RETRY, NEEDS_PRIORITY,
    UNMAPPED_ASIN, LOW_PROFIT, OOS, DUPLICATE, … — the 'why did the bot do that' feed."""
    import csv
    import glob
    rajar = os.path.join(DATA, "rajar_logs")
    cutoff = time.time() - hours * 3600
    want = {s.strip().upper() for s in (statuses or []) if s.strip()}
    out = []
    for f in sorted(glob.glob(os.path.join(rajar, "run_*.csv")), reverse=True):
        try:
            if os.path.getmtime(f) < cutoff:
                continue
            prof = ""
            try:
                prof = (json.load(open(f[:-4] + ".json", encoding="utf-8")).get("profile") or "")
            except Exception:
                pass
            with open(f, encoding="utf-8", errors="replace") as fh:
                for row in csv.DictReader(fh):
                    st = (row.get("Status") or "").strip().upper()
                    if want and st not in want:
                        continue
                    out.append({"time": row.get("Time") or row.get("Timestamp") or "", "run": os.path.basename(f)[4:-4],
                                "profile": prof, "amazon_id": row.get("Amazon_ID") or row.get("Amazon ID") or "",
                                "status": st, "asin": row.get("ASIN") or "", "sku": row.get("SKU") or "",
                                "buyer": row.get("Buyer") or "", "state": row.get("State") or "",
                                "notes": row.get("Notes") or row.get("Note") or ""})
        except Exception:
            continue
        if len(out) >= limit:
            break
    out.sort(key=lambda d: (d["run"], d["time"]), reverse=True)
    return {"hours": hours, "events": out[:limit], "available": os.path.isdir(rajar)}


# ── decision log (supervisor / deciding / pdp / route / learn / sheet / check / outcome) ──
def decisions(db: str, q: dict) -> dict:
    """Structured routing decisions from bot_decisions (utils/decision_log.py).
    Works in both modes: the table rides to the cloud in the slim DB."""
    from utils import decision_log as dl
    g = lambda k, d=None: (q.get(k, [d])[0] or d)  # noqa: E731
    rows = dl.read(db_path=db, date=g("date"), kind=g("kind"), profile=g("profile"), asin=g("asin"),
                   amazon_id=g("order"), action=g("action"), since=g("since"), limit=int(g("limit", "500") or 500))
    summary = {"by_kind": {}, "by_action": {}, "supervisor_by_action": {}, "coupon_hits": {}}
    for r in rows:
        summary["by_kind"][r["kind"]] = summary["by_kind"].get(r["kind"], 0) + 1
        key = f"{r['kind']}:{r.get('action')}"
        summary["by_action"][key] = summary["by_action"].get(key, 0) + 1
        if r["kind"] == "supervisor":
            summary["supervisor_by_action"][r.get("action")] = summary["supervisor_by_action"].get(r.get("action"), 0) + 1
            for a in (r.get("data") or {}).get("attempts") or []:
                c = summary["coupon_hits"].setdefault(a.get("code"), {"tried": 0, "worked": 0, "item": 0, "min_order": 0,
                                                                        "dead": 0, "transient": 0})
                c["tried"] += 1
                if a.get("ok"):
                    c["worked"] += 1
                else:
                    k = a.get("kind") or "other"
                    if k == "token":
                        k = "transient"
                    if k in c:
                        c[k] += 1
    return {"rows": rows, "summary": summary, "count": len(rows)}


# ── reconciliation ────────────────────────────────────────────────────────
def reconcile(db: str, days: int = 30) -> dict:
    """Gmail says N orders, the report/closing says M — show the gap and the ids."""
    c = _conn(db)
    try:
        since = (datetime.now() - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")
        out = {"days": days, "since": since, "counts": {}, "missing": {}, "notes": []}

        def count(table, date_cols, extra=""):
            if not _has_table(c, table):
                return None
            cols = _cols(c, table)
            dc = next((d for d in date_cols if d in cols), None)
            where = f"WHERE {dc} >= ?" if dc else ""
            if extra:
                where = (where + " AND " if where else "WHERE ") + extra
            try:
                return c.execute(f"SELECT COUNT(DISTINCT amazon_order_id) FROM [{table}] {where}",
                                 ((since,) if dc else ())).fetchone()[0]
            except Exception:
                return None

        def ids(table, date_cols, extra="", limit=200000):
            if not _has_table(c, table):
                return set()
            cols = _cols(c, table)
            dc = next((d for d in date_cols if d in cols), None)
            where = f"WHERE {dc} >= ?" if dc else ""
            if extra:
                where = (where + " AND " if where else "WHERE ") + extra
            try:
                return {r[0] for r in c.execute(f"SELECT DISTINCT amazon_order_id FROM [{table}] {where} LIMIT {limit}",
                                                ((since,) if dc else ()))}
            except Exception:
                return set()

        e_ids = ids("archive_earnings", ("purchase_date", "email_timestamp", "order_date", "created_at"))
        cl_ids = ids("closing_orders", ("purchase_date",))
        pl_ids = ids("order_placer_orders", ("placed_at", "purchase_date"))
        out["counts"] = {
            "gmail_sold_emails": len(e_ids) if _has_table(c, "archive_earnings") else None,
            "closing_orders": len(cl_ids),
            "placer_orders": len(pl_ids),
            "closing_with_wb": count("closing_orders", ("purchase_date",), "wb_order_id LIKE 'WB%'"),
            "tracking_found": count("closing_orders", ("purchase_date",), "tracking_ids IS NOT NULL AND tracking_ids<>''"),
            "tracking_uploaded": count("closing_orders", ("purchase_date",), "(tracking_upload_date IS NOT NULL AND tracking_upload_date<>'') OR lower(tracking_id_uploaded) IN ('yes','1','true')"),
            "delivered": count("closing_orders", ("purchase_date",), "delivery_status IN ('Delivered','AllDelivered')"),
            "delivery_msg_sent": count("closing_orders", ("purchase_date",), "lower(delivered_msg_sent) IN ('yes','1','true') OR lower(customer_messaged) IN ('yes','1','true')"),
            "review_requested": count("closing_orders", ("purchase_date",), "lower(review_request_sent) IN ('yes','1','true') OR lower(review_requested) IN ('yes','1','true')"),
            "returns": count("closing_orders", ("purchase_date",), "lower(return_requested) IN ('yes','1','true')"),
            "refunds": count("closing_orders", ("purchase_date",), "lower(refund_done) IN ('yes','1','true')"),
            "earnings_missing": count("closing_orders", ("purchase_date",), "earnings IS NULL OR earnings='' OR CAST(earnings AS REAL)=0"),
            "not_yet_closed": len(pl_ids - cl_ids),
        }
        if _has_table(c, "archive_earnings"):
            out["missing"]["in_gmail_not_in_closing"] = sorted(e_ids - cl_ids - pl_ids)[:50]
            out["missing"]["in_closing_not_in_gmail"] = sorted(cl_ids - e_ids)[:50]
        else:
            out["notes"].append("Gmail archive tables are not in the slim DB — Gmail vs report reconciliation is available on the machine that owns the master DB.")
        out["missing"]["placed_not_closed"] = sorted(pl_ids - cl_ids)[:50]
        return out
    finally:
        c.close()
