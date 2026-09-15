#!/usr/bin/env python3
"""CRM dashboard server — zero-dependency stdlib HTTP.

Read endpoints serve aggregated views (overview KPIs, ASIN analysis, orders,
insights); write endpoints back the CRM (notes, case status, reminders,
flags, manual-delivered, insight-resolve). Reads use a read-only WAL
connection; writes use a short-timeout connection with lock-retry so they
never collide with a running closing job.

Run:  ../.venv/bin/python dashboard/server.py 8000
"""
import os, sys, json, sqlite3, time, urllib.parse, re, gzip as _gzip
from datetime import date, timedelta, datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
try:
    from zoneinfo import ZoneInfo
    _TZ_PK = ZoneInfo("Asia/Karachi")
    _TZ_PT = ZoneInfo("America/Los_Angeles")
except Exception:
    _TZ_PK = _TZ_PT = None


def _ship_verdict(shipped_email_time, ship_by_date):
    """Early / On Time / Late from the placer's own data — mirrors closing's
    compute_ship_timeliness: shipped_email_time is PKT, converted to US/Pacific,
    then its date compared to the Amazon ship-by (already a Pacific date).
    Returns 'early' | 'ontime' | 'late' | None (not shipped / unparseable)."""
    if not shipped_email_time or not ship_by_date:
        return None
    try:
        shp = datetime.strptime(str(shipped_email_time)[:19], "%Y-%m-%d %H:%M:%S")
        sd = shp.replace(tzinfo=_TZ_PK).astimezone(_TZ_PT).date() if _TZ_PK else shp.date()
        bd = datetime.strptime(str(ship_by_date)[:10], "%Y-%m-%d").date()
        return "early" if sd < bd else ("ontime" if sd == bd else "late")
    except Exception:
        return None

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.abspath(os.path.join(HERE, "..", "data", "automations_unified.db"))
# Placer price tracker — last-known price/stock status per Zoro SKU. Used to show
# whether an ALT order's MAIN code is back in stock.
PRICE_TRACKER = os.path.abspath(os.path.join(HERE, "..", "data", "dataset", "price_tracker.json"))
sys.path.insert(0, HERE)
import ops as OPS
if OPS.CLOUD_MODE:
    # Render / any machine WITHOUT the master DB: serve the slim DB the cloud agent
    # uploads to R2 (refreshed every 2 min when its manifest changes). CRM writes
    # are pushed to R2 as events and replayed into the master DB by that agent.
    OPS.cloud_db_refresh(force=True)
    OPS.cloud_db_start_refresher()
    DB = OPS.CLOUD_DB_PATH
    if not os.path.exists(PRICE_TRACKER):
        PRICE_TRACKER = os.path.join(os.path.dirname(OPS.CLOUD_DB_PATH), "price_tracker.json")
try:
    with sqlite3.connect(DB, timeout=30) as _init_c:
        _init_c.execute("PRAGMA journal_mode=WAL")
        _init_c.execute("PRAGMA synchronous=NORMAL")
        _init_c.execute("PRAGMA busy_timeout=30000")
except Exception:
    pass
import insights as INS
import analytics as AN
INS.DB = DB
AN.DB = DB
try:
    import repricer_engine as REPRICER      # needs pandas + the local offers reports
except ImportError as _e:                   # cloud mode ships without pandas: repricer is local-only
    REPRICER = None
    print(f"[dashboard] repricer disabled: {_e}")
import threading
import subprocess

_REPRICER_SCRAPING = {"Hathoro": False, "HOANK": False, "last_msg": ""}

def _start_repricer_scrape(account: str = "Hathoro"):
    global _REPRICER_SCRAPING
    acct = "Hathoro" if "HATHORO" in (account or "").upper() else "HOANK"
    if _REPRICER_SCRAPING.get(acct):
        return {"status": "already_running", "message": f"Scraper is already running for {acct}"}

    def _worker():
        _REPRICER_SCRAPING[acct] = True
        _REPRICER_SCRAPING["last_msg"] = f"Scraping started for {acct} at {datetime.now().strftime('%H:%M:%S')}"
        try:
            script_name = "generate_hathoro_offers_report.py" if acct == "Hathoro" else "generate_hoank_offers_report.py"
            script_path = os.path.join(os.path.dirname(HERE), "scripts", script_name)
            venv_py = os.path.abspath(os.path.join(os.path.dirname(HERE), "..", ".venv", "bin", "python"))
            py_exec = venv_py if os.path.exists(venv_py) else sys.executable
            subprocess.run([py_exec, script_path, "--force"], check=False)
            _REPRICER_SCRAPING["last_msg"] = f"Scraping completed for {acct} at {datetime.now().strftime('%H:%M:%S')}"
        except Exception as e:
            _REPRICER_SCRAPING["last_msg"] = f"Scraping error: {e}"
        finally:
            _REPRICER_SCRAPING[acct] = False

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"status": "started", "message": f"Live scraping started for {acct}"}

_env_port = int(os.environ.get("PORT") or 0)
# Render (cloud mode) assigns the port through $PORT and must win even if a start
# command still passes a number; locally the command-line port wins as before.
PORT = (_env_port if (OPS.CLOUD_MODE and _env_port)
        else next((int(a) for a in sys.argv[1:] if a.isdigit()), _env_port or 8000))
DELIVERED = ("Delivered", "AllDelivered")

# "Live" window for the Unmapped / Needs-Priority tabs: only consider placer run logs that were
# WRITTEN (modified) within this many seconds — i.e. the currently-active runs — so the dashboard
# shows the same live/current pending set as the GUI launcher and never surfaces stale old-run
# items. Must match LIVE_WINDOW_S in gui/launcher.py.
LIVE_WINDOW_S = 2700  # 45 minutes



# ── SIMPLE PASSWORD GATE ─────────────────────────────────────────────────────
# Required because the dashboard can be exposed via a public Cloudflare tunnel for the
# Windows-7 colleague (no Tailscale). One shared password (data/dashboard_password.txt,
# auto-generated on first run; override with DASH_PASSWORD env). Login once → year cookie.
# Requests from localhost (127.0.0.1) are exempt so local scripts/health-checks keep working.
import hashlib as _hashlib, secrets as _secrets

_PW_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "dashboard_password.txt")

def _dash_password():
    pw = (os.environ.get("DASH_PASSWORD") or "").strip()
    if pw:
        return pw
    try:
        pw = open(_PW_FILE).read().strip()
        if pw:
            return pw
    except Exception:
        pass
    pw = "zuhra-" + _secrets.token_hex(3)
    try:
        open(_PW_FILE, "w").write(pw + "\n")
    except Exception:
        pass
    return pw

DASH_PW = _dash_password()
_AUTH_COOKIE = _hashlib.sha256(("zuhra-dash:" + DASH_PW).encode()).hexdigest()

# Restricted role password: env DASH_RESTRICTED_PASSWORD → data/dashboard_restricted_password.txt
# (auto-generated on first run). Never hardcoded in source again.
_RESTRICTED_PW_FILE = os.path.join(os.path.dirname(_PW_FILE), "dashboard_restricted_password.txt")


def _restricted_password():
    pw = (os.environ.get("DASH_RESTRICTED_PASSWORD") or "").strip()
    if pw:
        return pw
    try:
        pw = open(_RESTRICTED_PW_FILE).read().strip()
        if pw:
            return pw
    except Exception:
        pass
    pw = "zuhra-r-" + _secrets.token_hex(3)
    try:
        open(_RESTRICTED_PW_FILE, "w").write(pw + "\n")
    except Exception:
        pass
    return pw


RESTRICTED_PW = _restricted_password()
_RESTRICTED_COOKIE = _hashlib.sha256(("zuhra-dash:" + RESTRICTED_PW).encode()).hexdigest()

# ── ROLES ────────────────────────────────────────────────────────────────────
# Two passwords → two roles. The team password above = VIEWER (colleague): full dashboard
# but NO team chat and NO Reminder Center (private to the owner). A separate ADMIN password
# (data/dashboard_admin_password.txt) = everything. Genuinely-local requests are admin.
_ADMIN_PW_FILE = os.path.join(os.path.dirname(_PW_FILE), "dashboard_admin_password.txt")

def _admin_password():
    pw = (os.environ.get("DASH_ADMIN_PASSWORD") or "").strip()
    if pw:
        return pw
    try:
        pw = open(_ADMIN_PW_FILE).read().strip()
        if pw:
            return pw
    except Exception:
        pass
    pw = "zuhra-admin-" + _secrets.token_hex(3)
    try:
        open(_ADMIN_PW_FILE, "w").write(pw + "\n")
    except Exception:
        pass
    return pw

ADMIN_PW = _admin_password()
_ADMIN_COOKIE = _hashlib.sha256(("zuhra-admin:" + ADMIN_PW).encode()).hexdigest()

# Endpoints private to the admin (owner): Reminder Center, P&L, bank reconcile.
# (Team chat was removed at the owner's request.)
_ADMIN_ONLY_GET = ("/api/reminders", "/api/pnl", "/api/pnl_hoank", "/api/travel_export")
_ADMIN_ONLY_POST = ("/api/reminder", "/api/reminder_update", "/api/pnl",
                    "/api/pnl_hoank", "/api/bank_reconcile")
# Ops controls (start/stop the launcher fleet, edit routing/automation config) are for
# the owner AND the viewer-level colleagues, never for the restricted role.
_RESTRICTED_DENY_POST = ("/api/ops/command", "/api/config", "/api/cell")

_LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>DS — sign in</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#0e1018;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
form{background:#171a26;padding:34px 38px;border-radius:14px;box-shadow:0 10px 40px rgba(0,0,0,.5);text-align:center}
h2{color:#e6e9f2;margin:0 0 4px;font-size:20px}p{color:#8b93a7;font-size:12.5px;margin:0 0 18px}
input{padding:11px 13px;border-radius:9px;border:1px solid #2a2f42;background:#0e1018;color:#e6e9f2;font-size:14px;width:220px}
button{margin-top:12px;padding:11px 0;width:100%%;border:0;border-radius:9px;background:#4d6bfe;color:#fff;font-weight:700;font-size:14px;cursor:pointer}
.err{color:#ff7b72;font-size:12px;margin-top:10px}</style></head><body>
<form method="POST" action="/login"><h2>🔷 DS Dashboard</h2><p>Enter the team password</p>
<input type="password" name="pw" autofocus placeholder="password"><button>Sign in</button>%s</form></body></html>"""


# ── LIST-VIEW COLUMN WHITELIST ───────────────────────────────────────────────
# v_orders_all has ~115 columns; the browse table + its client-side filters/KPIs read only
# these. Sending just them cuts the orders payload ~2.5x (and JSON.parse time with it).
# The "all columns" checkbox re-fetches with cols=all. Intersected with the live schema so
# a missing column can never break the query.
_LIST_COLS = [
    "amazon_order_id", "merchant_order_id", "wb_order_id", "order_label", "order_status",
    "account", "profile_name", "purchase_date", "placed_at", "asin", "sku", "zoro_sku",
    "title", "quantity", "earnings", "item_price", "shipping_amt", "zoro_order_total",
    "zoro_subtotal", "zoro_shipping", "zoro_tax", "profit_dollars", "profit_percent",
    "roi_percent", "net_profit", "net_profit_after_refund", "coupon_used", "coupon_savings",
    "zoro_account_name", "zoro_payment_method", "zoro_payment_card", "zoro_buyer_zip",
    "tracking_ids", "tracking_id_uploaded", "delivery_status", "delivery_date",
    "deliver_by_date", "ship_by_date", "shipped_email_time", "sold_email_time", "on_time",
    "shipped_on_time", "placement_speed", "zoro_ship_speed", "return_requested",
    "return_reason", "refund_done", "refund_amount", "refund_date", "customer_messaged",
    "customer_inquiry", "delivered_msg_sent", "review_requested", "review_request_sent",
    "ship_name", "ship_city", "ship_state", "ship_postal_code", "ship_phone",
    "alt_used", "main_sku", "main_sku_status", "main_sku_status_at", "notes",
]
_VIEW_COLS_CACHE = {}

def _live_cols(c, table):
    """Column names of a table/view, cached for the process lifetime."""
    if table not in _VIEW_COLS_CACHE:
        _VIEW_COLS_CACHE[table] = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    return _VIEW_COLS_CACHE[table]

def _select_cols(c, table, q):
    """SELECT list for list views: pruned whitelist by default; '*' when cols=all."""
    if q.get("cols", [""])[0] == "all":
        return "*"
    cols = [x for x in _LIST_COLS if x in _live_cols(c, table)]
    return ", ".join(cols) if cols else "*"


def _run_account(path):
    """Map a run_<ts>.csv to the dashboard account name via its sibling run_<ts>.json 'profile':
    DM_* → 'Hathoro', HS_* → 'HOANK'. Lets the Unmapped / Needs-Priority tabs honour the account
    filter (matching the 'account' column used everywhere else)."""
    try:
        prof = (json.load(open(path[:-4] + ".json", encoding="utf-8")).get("profile") or "")
    except Exception:
        prof = ""
    return "Hathoro" if prof.startswith("DM_") else ("HOANK" if prof.startswith("HS_") else "")


def ro():
    # Normal connection + query_only (NOT mode=ro): a WAL database can fail to open
    # read-only when the -wal/-shm are active. query_only gives read safety without that.
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA query_only=ON")
    c.row_factory = sqlite3.Row
    return c


def rw():
    c = sqlite3.connect(DB, timeout=30)
    try:
        c.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    c.execute("PRAGMA busy_timeout=30000")
    c.row_factory = sqlite3.Row
    return c


def _retry_write(fn):
    for i in range(12):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and i < 11:
                time.sleep(0.3 * (i + 1)); continue
            raise


def _filters(q, date_col="purchase_date"):
    # date_col is whitelisted by the caller (it is interpolated into SQL).
    acct = q.get("account", [""])[0]
    frm = q.get("from", [""])[0]
    to = q.get("to", [""])[0]
    where, args = ["1=1"], []
    if acct and acct.lower() != "all":
        where.append("account=?"); args.append(acct)
    if frm:
        frm_val = frm.replace("T", " ")
        where.append(f"substr(replace({date_col}, 'T', ' '),1,{len(frm_val)})>=?"); args.append(frm_val)
    if to:
        to_val = to.replace("T", " ")
        where.append(f"substr(replace({date_col}, 'T', ' '),1,{len(to_val)})<=?"); args.append(to_val)
    return " AND ".join(where), args, acct, frm, to


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ── read views ────────────────────────────────────────────────────────────
def overview(q):
    if not _has_table("closing_orders"):
        return {"kpi": {"earnings": 0, "profit": 0, "margin": 0, "units": 0, "orders": 0},
                "series": [], "top": [], "labels": {}, "status": "syncing"}
    where, args, *_ = _filters(q)
    c = ro()
    try:
        rows = [dict(r) for r in c.execute(f"SELECT * FROM closing_orders WHERE {where}", args)]
    finally:
        c.close()
    earn = sum(_f(r.get("earnings")) for r in rows)
    profit = sum(_f(r.get("profit_dollars")) for r in rows)
    units = sum(_f(r.get("quantity")) for r in rows)
    daily = {}
    for r in rows:
        d = (r.get("purchase_date") or "")[:10]
        if not d:
            continue
        s = daily.setdefault(d, {"date": d, "earnings": 0, "profit": 0, "units": 0, "orders": 0})
        s["earnings"] += _f(r.get("earnings")); s["profit"] += _f(r.get("profit_dollars"))
        s["units"] += _f(r.get("quantity")); s["orders"] += 1
    series = sorted(daily.values(), key=lambda x: x["date"])
    agg = {}
    for r in rows:
        k = (r.get("asin") or "?", r.get("sku") or "?")
        a = agg.setdefault(k, {"asin": k[0], "sku": k[1], "title": r.get("title"), "units": 0,
                               "orders": 0, "earnings": 0, "profit": 0})
        a["units"] += _f(r.get("quantity")); a["orders"] += 1
        a["earnings"] += _f(r.get("earnings")); a["profit"] += _f(r.get("profit_dollars"))
    for a in agg.values():
        a["margin"] = round(a["profit"] / a["earnings"] * 100, 1) if a["earnings"] else 0
        a["earnings"] = round(a["earnings"], 2); a["profit"] = round(a["profit"], 2)
    top = sorted(agg.values(), key=lambda x: -x["units"])[:50]
    labels = {}
    for r in rows:
        labels[r.get("order_label") or "?"] = labels.get(r.get("order_label") or "?", 0) + 1
    return {"kpi": {"earnings": round(earn, 2), "profit": round(profit, 2),
                    "margin": round(profit / earn * 100, 1) if earn else 0,
                    "units": int(units), "orders": len(rows)},
            "series": series, "top": top, "labels": labels}


def asin_list(q):
    if not _has_table("closing_orders"):
        return {"asins": []}
    where, args, *_ = _filters(q)
    c = ro()
    try:
        rows = [dict(r) for r in c.execute(f"SELECT * FROM closing_orders WHERE {where}", args)]
    finally:
        c.close()
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    agg = {}
    for r in rows:
        a = r.get("asin") or "?"
        g = agg.setdefault(a, {"asin": a, "sku": r.get("sku"), "title": r.get("title"),
                               "zoro_account": r.get("zoro_account_name"), "orders": 0, "units": 0,
                               "earnings": 0, "profit": 0, "returns": 0, "refunds": 0, "m_all": [], "m_7": [], "tts": []})
        g["orders"] += 1; g["units"] += _f(r.get("quantity"))
        g["earnings"] += _f(r.get("earnings")); g["profit"] += _f(r.get("profit_dollars"))
        if r.get("return_requested") == "Yes": g["returns"] += 1
        if r.get("refund_done") == "Yes": g["refunds"] += 1
        if r.get("profit_percent") is not None:
            g["m_all"].append(_f(r.get("profit_percent")))
            if (r.get("purchase_date") or "")[:10] >= cutoff:
                g["m_7"].append(_f(r.get("profit_percent")))
        if r.get("time_to_ship_hrs") is not None:
            g["tts"].append(_f(r.get("time_to_ship_hrs")))

    def avg(x):
        return round(sum(x) / len(x), 1) if x else None
    out = []
    for g in agg.values():
        n = g["orders"] or 1
        out.append({"asin": g["asin"], "sku": g["sku"], "title": g["title"],
                    "zoro_account": g["zoro_account"], "orders": g["orders"], "units": int(g["units"]),
                    "earnings": round(g["earnings"], 2), "profit": round(g["profit"], 2),
                    "margin_all": avg(g["m_all"]), "margin_7d": avg(g["m_7"]),
                    "avg_ship_hrs": avg(g["tts"]),
                    "return_rate": round(g["returns"] / n * 100, 1), "refund_rate": round(g["refunds"] / n * 100, 1),
                    "returns": g["returns"], "refunds": g["refunds"]})
    return {"asins": sorted(out, key=lambda x: -x["units"])}


def asin_detail(asin, q):
    acct = q.get("account", [""])[0]
    c = ro()
    # v_orders_all so the ASIN history includes placed-but-not-closed orders too.
    where = "asin=?"; args = [asin]
    if acct and acct.lower() != "all":
        where += " AND account=?"; args.append(acct)
    rows = [dict(r) for r in c.execute(f"SELECT * FROM v_orders_all WHERE {where} ORDER BY purchase_date", args)]
    c.close()
    daily = {}
    for r in rows:
        d = (r.get("purchase_date") or "")[:10]
        if not d:
            continue
        s = daily.setdefault(d, {"date": d, "units": 0, "earnings": [], "ppct": [], "zunit": [], "profit": [], "codes": {}})
        aq = _f(r.get("quantity")) or 1
        s["units"] += (_f(r.get("quantity")) or 0)
        if r.get("earnings") is not None:
            s["earnings"].append(_f(r.get("earnings")))
        if r.get("profit_percent") is not None:
            s["ppct"].append(_f(r.get("profit_percent")))
        if r.get("profit_dollars") is not None:        # per-unit profit
            pv = _f(r.get("profit_dollars"))
            if pv is not None:
                s["profit"].append(pv / aq if aq else pv)
        # per-unit Zoro price (cost / units), tolerant of missing zoro_qty
        units = _f(r.get("zoro_qty")) or _f(r.get("quantity")) or 1
        sub = _f(r.get("zoro_subtotal")) or _f(r.get("zoro_order_total"))
        if sub and units:
            s["zunit"].append(sub / units)
        code = (r.get("zoro_sku") or r.get("sku") or "").strip()
        if code:
            s["codes"][code] = s["codes"].get(code, 0) + 1
    series = []
    prev_code = None
    prev_price = None
    for d in sorted(daily):
        s = daily[d]
        m = lambda x: round(sum(v for v in x if v is not None) / len([v for v in x if v is not None]), 2) if any(v is not None for v in x) else 0
        code = max(s["codes"], key=s["codes"].get) if s["codes"] else ""
        zp = m(s["zunit"])
        price_changed = bool(prev_price and zp and abs(zp - prev_price) / prev_price >= 0.05)
        series.append({"date": d, "units": s["units"], "earnings": m(s["earnings"]),
                       "margin": round(m(s["ppct"]), 1), "zoro_price": zp, "profit": m(s["profit"]),
                       "zoro_code": code, "code_changed": bool(prev_code and code and code != prev_code),
                       "price_changed": price_changed})
        if code:
            prev_code = code
        if zp:
            prev_price = zp
    states, ontime, late = {}, 0, 0
    returns = refunds = 0
    return_reasons, refund_reasons = {}, {}
    for r in rows:
        states[r.get("ship_state") or "?"] = states.get(r.get("ship_state") or "?", 0) + 1
        if r.get("on_time") == "On Time":
            ontime += 1
        elif r.get("on_time") == "Late":
            late += 1
        if r.get("return_requested") == "Yes":
            returns += 1
            rr = (r.get("return_reason") or "").strip()
            if rr:
                return_reasons[rr] = return_reasons.get(rr, 0) + 1
        if r.get("refund_done") == "Yes":
            refunds += 1
            fr = (r.get("refund_reason") or r.get("return_reason") or "").strip()
            if fr:
                refund_reasons[fr] = refund_reasons.get(fr, 0) + 1
    n = len(rows) or 1
    top = lambda d: sorted([{"reason": k, "n": v} for k, v in d.items()], key=lambda x: -x["n"])
    return {"asin": asin, "orders": rows, "series": series,
            "states": sorted([{"state": k, "n": v} for k, v in states.items()], key=lambda x: -x["n"]),
            "on_time": ontime, "late": late, "count": len(rows),
            "returns": returns, "refunds": refunds,
            "return_rate": round(returns / n * 100, 1), "refund_rate": round(refunds / n * 100, 1),
            "return_reasons": top(return_reasons), "refund_reasons": top(refund_reasons)}


def _has_table(name):
    """True if the table OR VIEW exists (CRM tables are created lazily; v_orders_all / v_closing
    are views — checking type='table' alone hid every order list from 8 to 14 Sep 2026)."""
    c = ro()
    try:
        ok = c.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
                       (name,)).fetchone() is not None
    except Exception:
        ok = False
    c.close()
    return ok


def _q_search_extra(like):
    """Extra OR-clauses so the search box also matches CRM comments, note log and tags."""
    sql, args = "", []
    if _has_table("order_crm"):
        sql += (" OR amazon_order_id IN (SELECT amazon_order_id FROM order_crm "
                "WHERE comments LIKE ? OR tags LIKE ?)")
        args += [like, like]
    if _has_table("crm_notes"):
        sql += " OR amazon_order_id IN (SELECT amazon_order_id FROM crm_notes WHERE note LIKE ?)"
        args.append(like)
    return sql, args


def _tag_filter_sql(tag):
    """WHERE clause for an exact tag match inside the comma-list order_crm.tags.
    Server-side (not client-side) so the row LIMIT can't hide tagged old orders."""
    if not tag or not _has_table("order_crm"):
        return "", []
    return (" AND amazon_order_id IN (SELECT amazon_order_id FROM order_crm "
            "WHERE (','||REPLACE(COALESCE(tags,''),', ',',')||',') LIKE '%,'||?||',%')"), [tag]


def placed(q):
    """Placement log = every row in order_placer_orders (what DB Browser shows),
    date-filtered by placed_at (the date WE placed it on Zoro). Independent of closing
    status, so an order stays here even after a closing run touches it."""
    where, args, *_ = _filters(q, date_col="placed_at")
    prof = q.get("profile", [""])[0]
    if prof and prof.lower() != "all":
        where += " AND profile_name=?"; args.append(prof)
    search = (q.get("q", [""])[0] or "").strip()
    if search:
        like = f"%{search}%"
        xsql, xargs = _q_search_extra(like)
        where += (" AND (amazon_order_id LIKE ? OR wb_order_id LIKE ? OR sku LIKE ? "
                  "OR asin LIKE ? OR ship_name LIKE ?" + xsql + ")")
        args += [like, like, like, like, like] + xargs
    tsql, targs = _tag_filter_sql((q.get("tag", [""])[0] or "").strip())
    where += tsql; args += targs
    c = ro()
    sel = _select_cols(c, "order_placer_orders", q)
    rows = [dict(r) for r in c.execute(
        f"SELECT {sel} FROM order_placer_orders WHERE {where} ORDER BY placed_at DESC LIMIT 5000", args)]
    _apply_crm_overlay(c, rows)   # overlay flag_color/case_status/comments so labels show on the Placed tab
    c.close()
    # ?alt=1 → only orders placed with the ALTERNATIVE Zoro code (main was OOS).
    # Python-side so it can't error before the alt_used column exists in the DB.
    if (q.get("alt", [""])[0] or "").lower() in ("1", "yes", "true"):
        rows = [r for r in rows if (r.get("alt_used") or "") == "Yes"]
    # Annotate ALT rows with the MAIN SKU's last-known stock status from the placer's
    # price tracker — answers "is the main code back in stock?" right on the dashboard.
    alt_rows = [r for r in rows if (r.get("alt_used") or "") == "Yes" and (r.get("main_sku") or "").strip()]
    if alt_rows:
        try:
            with open(PRICE_TRACKER, encoding="utf-8") as f:
                trk = json.load(f)
        except Exception:
            trk = {}
        for r in alt_rows:
            t = trk.get((r.get("main_sku") or "").strip()) or {}
            if t:
                r["main_sku_status"] = t.get("status", "")
                r["main_sku_status_at"] = str(t.get("last_updated") or "")[:16]
    return {"orders": rows, "count": len(rows)}


_WM_EMPTY_KPIS = {"count": 0, "placed": 0, "pending": 0, "failed": 0,
                  "earnings_sum": 0, "cost_sum": 0, "profit_sum": 0}


def walmart(q):
    """Walmart orders tab — reads walmart_orders (filled by the launcher's Walmart page:
    scraper + order placer). The table is created lazily by those tools, so it may not
    exist yet → degrade gracefully to an empty list instead of a 500."""
    where, args = ["1=1"], []
    frm = q.get("from", [""])[0]
    to = q.get("to", [""])[0]
    if frm:
        where.append("substr(order_date,1,10)>=?"); args.append(frm)
    if to:
        where.append("substr(order_date,1,10)<=?"); args.append(to)
    status = (q.get("status", [""])[0] or "").strip()
    if status and status.lower() != "all":
        where.append("status=?"); args.append(status)
    search = (q.get("q", [""])[0] or "").strip()
    if search:
        like = f"%{search}%"
        where.append("(po_number LIKE ? OR order_number LIKE ? OR zoro_code LIKE ? "
                     "OR wb_order_id LIKE ? OR ship_name LIKE ?)")
        args += [like] * 5
    c = ro()
    try:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM walmart_orders WHERE " + " AND ".join(where) +
            " ORDER BY order_date DESC LIMIT 5000", args)]
    except sqlite3.OperationalError:
        # table not created yet (scraper/placer haven't run) → graceful empty shape
        c.close()
        return {"orders": [], "note": "no walmart orders yet", "kpis": dict(_WM_EMPTY_KPIS)}
    c.close()
    st = lambda r: (r.get("status") or "").strip().lower()
    kpis = {"count": len(rows),
            # processing_clicked = the placer clicked through checkout = effectively placed
            "placed": sum(1 for r in rows if st(r) in ("placed", "processing_clicked")),
            "pending": sum(1 for r in rows if st(r) == "pending"),
            "failed": sum(1 for r in rows if st(r) == "failed"),
            "earnings_sum": round(sum(_f(r.get("earnings")) for r in rows), 2),
            "cost_sum": round(sum(_f(r.get("zoro_cost")) for r in rows), 2),
            "profit_sum": round(sum(_f(r.get("profit")) for r in rows), 2)}
    return {"orders": rows, "kpis": kpis}


def orders(q):
    if q.get("src", [""])[0] == "placed":
        return placed(q)
    # Placed tab filters/sorts by placed_at (placement date); everything else by
    # purchase_date (Amazon order date). Whitelisted to block SQL injection.
    dc = q.get("datecol", ["purchase_date"])[0]
    if dc not in ("purchase_date", "placed_at", "shipped_email_time"):
        dc = "purchase_date"
    where, args, *_ = _filters(q, date_col=dc)
    flag = q.get("flag", [""])[0]
    if flag == "undelivered":
        where += " AND delivery_status NOT IN ('Delivered','AllDelivered') AND COALESCE(manual_delivered,'')!='Yes'"
    elif flag == "refunded":
        where += " AND refund_done='Yes'"
    elif flag == "late":
        where += " AND on_time='Late'"
    elif flag == "incomplete":
        where += " AND order_label='Incomplete'"
    elif flag == "unresolved":
        # everything needing attention: refunded, incomplete, delivered-overdue,
        # or address inconsistency (5-digit ship-zip != Zoro-zip). Excludes Pending.
        where += (" AND order_label!='Pending' AND ("
                  "refund_done='Yes' "
                  "OR order_label='Incomplete' "
                  "OR (delivery_status NOT IN ('Delivered','AllDelivered') "
                  "    AND COALESCE(manual_delivered,'')!='Yes' AND COALESCE(deliver_by_date,'')!='' "
                  "    AND substr(deliver_by_date,1,10) < date('now')) "
                  "OR (COALESCE(zoro_buyer_zip,'')!='' AND COALESCE(ship_postal_code,'')!='' "
                  "    AND substr(replace(ship_postal_code,'-',''),1,5) != substr(replace(zoro_buyer_zip,'-',''),1,5)) "
                  # Gmail refund detected (archive_returns) even if closing hasn't run yet → act now
                  "OR amazon_order_id IN (SELECT amazon_order_id FROM archive_returns WHERE refund_done='Yes')"
                  ")")
    # direct status filter (Completed/Loss/Incomplete/Refunded/Cancelled/Pending)
    label = q.get("label", [""])[0]
    if label and label.lower() != "all":
        where += " AND order_label=?"; args.append(label)
    asin = q.get("asin", [""])[0]
    if asin:
        where += " AND asin=?"; args.append(asin)
    search = (q.get("q", [""])[0] or "").strip()
    if search:
        like = f"%{search}%"
        xsql, xargs = _q_search_extra(like)
        where += (" AND (amazon_order_id LIKE ? OR wb_order_id LIKE ? OR merchant_order_id LIKE ? "
                  "OR ship_name LIKE ? OR sku LIKE ? OR zoro_sku LIKE ? OR asin LIKE ? OR title LIKE ?" + xsql + ")")
        args += [like, like, like, like, like, like, like, like] + xargs
    tsql, targs = _tag_filter_sql((q.get("tag", [""])[0] or "").strip())
    where += tsql; args += targs
    za = q.get("zoro_account", [""])[0]
    if za and za.lower() != "all":
        where += " AND zoro_account_name=?"; args.append(za)
    prof = q.get("profile", [""])[0]
    if prof and prof.lower() != "all":
        where += " AND profile_name=?"; args.append(prof)
    # On Unresolved, attach a gmail_refund flag (refund seen in archive_returns) so the
    # UI can badge it even when closing hasn't set refund_done on the row yet.
    if not _has_table("v_orders_all"):
        return {"orders": [], "count": 0}
    c = ro()
    try:
        sel = _select_cols(c, "v_orders_all", q)
        if flag == "unresolved":
            sel += (", (SELECT 1 FROM archive_returns ar WHERE ar.amazon_order_id=v_orders_all.amazon_order_id "
                    "AND ar.refund_done='Yes') AS gmail_refund")
        rows = [dict(r) for r in c.execute(
            f"SELECT {sel} FROM v_orders_all WHERE {where} ORDER BY {dc} DESC LIMIT 3000", args)]
        _apply_crm_overlay(c, rows)
    finally:
        c.close()
    return {"orders": rows, "count": len(rows)}


def order_detail(oid):
    if not _has_table("v_orders_all"):
        return {"order": None, "notes": [], "returns": []}
    c = ro()
    try:
        r = c.execute("SELECT * FROM v_orders_all WHERE amazon_order_id=?", (oid,)).fetchone()
        order = dict(r) if r else None
        if order:
            _apply_crm_overlay(c, [order])
            seen = set(); wb = []
            for w in re.findall(r"WB\d+", str(order.get("merchant_order_id") or "").upper()):
                if w not in seen:
                    seen.add(w); wb.append(w)
            tseen = set(); tids = []
            for t in str(order.get("tracking_ids") or "").replace(";", ",").split(","):
                t = t.strip()
                if t and t not in tseen:
                    tseen.add(t); tids.append(t)
            order["wb_ids_all"] = wb
            order["tracking_ids_all"] = tids
        try:
            notes = [dict(x) for x in c.execute(
                "SELECT * FROM crm_notes WHERE amazon_order_id=? ORDER BY created_at DESC", (oid,))]
        except sqlite3.OperationalError:
            notes = []
        try:
            returns = [dict(x) for x in c.execute(
                "SELECT refund_done, refund_amount, refund_date, return_reason, return_requested, email_timestamp "
                "FROM archive_returns WHERE amazon_order_id=? ORDER BY email_timestamp DESC", (oid,))]
        except sqlite3.OperationalError:
            returns = []
    finally:
        c.close()
    return {"order": order, "notes": notes, "returns": returns}


_FACETS_CACHE = {"ts": 0, "data": None}


def facets():
    """Distinct values for the dashboard dropdowns (accounts + Zoro buying accounts + tags)."""
    now = time.time()
    if _FACETS_CACHE["data"] and (now - _FACETS_CACHE["ts"] < 300):
        return _FACETS_CACHE["data"]
    try:
        c = ro()
        try:
            accts = [r[0] for r in c.execute(
                "SELECT DISTINCT account FROM closing_orders WHERE COALESCE(account,'')!='' ORDER BY account")] if _has_table("closing_orders") else []
            zaccts = [r[0] for r in c.execute(
                "SELECT DISTINCT zoro_account_name FROM closing_orders WHERE COALESCE(zoro_account_name,'')!='' ORDER BY zoro_account_name")] if _has_table("closing_orders") else []
            profs = [r[0] for r in c.execute(
                "SELECT DISTINCT profile_name FROM order_placer_orders WHERE COALESCE(profile_name,'')!='' ORDER BY profile_name")] if _has_table("order_placer_orders") else []
            states = [r[0] for r in c.execute(
                "SELECT DISTINCT ship_state FROM v_orders_all WHERE COALESCE(ship_state,'')!='' ORDER BY ship_state")] if _has_table("v_orders_all") else []
            tags, seen = [], set()
            try:
                if _has_table("order_crm"):
                    for (t,) in c.execute("SELECT tags FROM order_crm WHERE COALESCE(tags,'')!=''"):
                        for x in str(t).split(","):
                            x = x.strip()
                            if x and x.lower() not in seen:
                                seen.add(x.lower()); tags.append(x)
                    tags.sort(key=str.lower)
            except sqlite3.OperationalError:
                pass
        finally:
            c.close()
        res = {"accounts": accts, "zoro_accounts": zaccts, "profiles": profs, "states": states, "tags": tags}
        _FACETS_CACHE["ts"] = now
        _FACETS_CACHE["data"] = res
        return res
    except Exception:
        if _FACETS_CACHE["data"]:
            return _FACETS_CACHE["data"]
        return {"accounts": [], "zoro_accounts": [], "profiles": [], "states": [], "tags": []}


_WATCH_DDL = ("CREATE TABLE IF NOT EXISTS zoro_watch(zoro_code TEXT PRIMARY KEY, label TEXT, "
              "added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_availability TEXT, "
              "last_lead_days INTEGER, last_lead_text TEXT, last_checked TIMESTAMP, "
              "alert TEXT, alert_at TIMESTAMP)")


def ship_perf(q):
    """Day-by-day SHIPPED-EARLY detector, grouped by PKT PLACEMENT day (placed_at is the
    placer's local PKT time). Computed live from the placer's own ship_by_date +
    shipped_email_time (PKT→Pacific), no closing run required.

    The metric the owner cares about: of the orders we did NOT have to ship that day
    (Amazon gave us a later ship-by = we had buffer/relaxation), how many did we still
    ship out ahead of the deadline. Orders whose ship-by IS the day's deadline (we were
    obligated to ship) are EXCLUDED from the rate — shipping those is required, not early.

    Per day we report:
      placed         - total orders placed that PKT day
      must_ship      - ship-by == the day's earliest deadline (obligated; excluded from rate)
      relaxed        - ship-by later than the deadline (had buffer)  ← the rate's denominator
      shipped_early  - relaxed orders shipped before their ship-by   ← the rate's numerator
      relaxed_pending- relaxed orders not shipped yet
      early_rate     - shipped_early / relaxed  (%)
      early/ontime/late - absolute verdicts over ALL shipped orders (context)

    The "deadline" per cohort is the MINIMUM ship_by_date among that day's orders — it
    self-calibrates (no hardcoded date) and reproduces the owner's hand-count
    (6/22 Hathoro: 301 must-ship / 145 relaxed / 124 early; 6/23: 4 / 127 / 29)."""
    acct = q.get("account", [""])[0]
    frm = q.get("from", [""])[0]
    to = q.get("to", [""])[0]
    where, args = ["substr(placed_at,1,10)!=''"], []
    if acct and acct.lower() != "all":
        where.append("account=?"); args.append(acct)
    if frm:
        where.append("substr(placed_at,1,10)>=?"); args.append(frm)
    if to:
        where.append("substr(placed_at,1,10)<=?"); args.append(to)
    w = " AND ".join(where)
    c = ro()
    rows = c.execute(f"""
        SELECT substr(placed_at,1,10) day, ship_by_date, shipped_email_time
        FROM order_placer_orders WHERE {w}""", args).fetchall()
    c.close()
    byday = {}
    for r in rows:
        byday.setdefault(r["day"], []).append(r)
    days = []
    for day, rs in byday.items():
        valid_sb = [str(r["ship_by_date"])[:10] for r in rs if r["ship_by_date"]]
        deadline = min(valid_sb) if valid_sb else None   # the day's soonest ship-by
        d = {"day": day, "placed": len(rs), "must_ship": 0, "relaxed": 0,
             "shipped_early": 0, "relaxed_pending": 0,
             "early": 0, "ontime": 0, "late": 0, "early_rate": 0}
        for r in rs:
            sb = str(r["ship_by_date"])[:10] if r["ship_by_date"] else None
            v = _ship_verdict(r["shipped_email_time"], r["ship_by_date"])
            if v:
                d[v] += 1                       # absolute verdict over shipped orders
            if sb is None or deadline is None:
                continue                        # no ship-by → can't classify obligation
            if sb <= deadline:
                d["must_ship"] += 1             # obligated to ship that day → not "early"
            else:
                d["relaxed"] += 1               # had buffer → eligible to be "early"
                if v == "early":
                    d["shipped_early"] += 1     # used the buffer to ship ahead anyway
                elif not r["shipped_email_time"]:
                    d["relaxed_pending"] += 1   # buffered, no tracking yet
        d["early_rate"] = round(d["shipped_early"] / d["relaxed"] * 100, 1) if d["relaxed"] else 0
        days.append(d)
    days.sort(key=lambda x: x["day"], reverse=True)
    return {"days": days[:90]}


def ship_orders(q):
    """The actual orders behind a ship_perf cell. Same classification as ship_perf
    (per-day deadline = min ship-by; relaxed = ship-by later; early = relaxed + shipped
    before ship-by). Returns the drill-down list when the user clicks a day's
    shipped-early count. kind: early (default) | buffer | mustship."""
    acct = q.get("account", [""])[0]
    frm = q.get("from", [""])[0]
    to = q.get("to", [""])[0]
    day = q.get("day", [""])[0]
    kind = q.get("kind", ["early"])[0]
    where, args = ["substr(placed_at,1,10)!=''"], []
    if acct and acct.lower() != "all":
        where.append("account=?"); args.append(acct)
    if day and day.lower() != "all":
        where.append("substr(placed_at,1,10)=?"); args.append(day)
    else:
        if frm:
            where.append("substr(placed_at,1,10)>=?"); args.append(frm)
        if to:
            where.append("substr(placed_at,1,10)<=?"); args.append(to)
    c = ro()
    rows = c.execute(
        "SELECT amazon_order_id,account,asin,sku,substr(placed_at,1,10) day,"
        "ship_by_date,shipped_email_time,deliver_by_date "
        "FROM order_placer_orders WHERE " + " AND ".join(where), args).fetchall()
    c.close()
    byday = {}
    for r in rows:
        byday.setdefault(r["day"], []).append(r)
    out = []
    for d, rs in byday.items():
        valid = [str(r["ship_by_date"])[:10] for r in rs if r["ship_by_date"]]
        deadline = min(valid) if valid else None
        for r in rs:
            sb = str(r["ship_by_date"])[:10] if r["ship_by_date"] else None
            relaxed = sb is not None and deadline is not None and sb > deadline
            mustship = sb is not None and deadline is not None and sb <= deadline
            v = _ship_verdict(r["shipped_email_time"], r["ship_by_date"])
            if kind == "early" and not (relaxed and v == "early"):
                continue
            if kind == "buffer" and not relaxed:
                continue
            if kind == "mustship" and not mustship:
                continue
            days_early = None
            try:
                shp = datetime.strptime(str(r["shipped_email_time"])[:19], "%Y-%m-%d %H:%M:%S")
                sd = shp.replace(tzinfo=_TZ_PK).astimezone(_TZ_PT).date() if _TZ_PK else shp.date()
                days_early = (datetime.strptime(sb, "%Y-%m-%d").date() - sd).days
            except Exception:
                pass
            out.append({"amazon_order_id": r["amazon_order_id"], "account": r["account"],
                        "asin": r["asin"], "sku": r["sku"], "day": d,
                        "ship_by_date": sb, "shipped_email_time": r["shipped_email_time"],
                        "deliver_by_date": r["deliver_by_date"], "days_early": days_early,
                        "verdict": v or ("pending" if not r["shipped_email_time"] else "")})
    out.sort(key=lambda x: (x["day"], x["days_early"] if x["days_early"] is not None else -99), reverse=True)
    return {"orders": out, "count": len(out), "kind": kind}


def handling_perf(q):
    """Per-ASIN handling-time opportunity, from the placer's Zoro delivery estimate vs
    Amazon's promised deliver-by. slack = Amazon_deliver_by − Zoro_eta (days):
      slack large +  → Zoro arrives well before our promise → we could PROMISE FASTER
                       (cut handling time → faster badge → more sales).
      slack negative → Zoro arrives AFTER our promise → late risk → ADD handling time.
    Reads order_placer_orders directly (zoro_deliver_by is captured at placement).
    NOTE: thin while the Zoro-ETA capture is young — only ASINs with >=MINN samples shown."""
    acct = q.get("account", [""])[0]
    frm = q.get("from", [""])[0]
    to = q.get("to", [""])[0]
    MINN = int((q.get("minn", ["1"])[0]) or 1)
    where = ["zoro_deliver_by!='' AND zoro_deliver_by IS NOT NULL AND deliver_by_date!=''"]
    args = []
    if acct and acct.lower() != "all":
        where.append("account=?"); args.append(acct)
    if frm:
        where.append("substr(placed_at,1,10)>=?"); args.append(frm)
    if to:
        where.append("substr(placed_at,1,10)<=?"); args.append(to)
    c = ro()
    rows = c.execute("SELECT asin, deliver_by_date, zoro_deliver_by FROM order_placer_orders "
                     "WHERE " + " AND ".join(where), args).fetchall()
    c.close()

    def _dt(s):
        try:
            return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
        except Exception:
            return None

    by = {}
    tot = []
    for r in rows:
        a, z = _dt(r["deliver_by_date"]), _dt(r["zoro_deliver_by"])
        if not (a and z):
            continue
        slack = (a - z).days
        by.setdefault(r["asin"], []).append(slack)
        tot.append(slack)
    out = []
    for asin, sl in by.items():
        if len(sl) < MINN:
            continue
        avg = sum(sl) / len(sl)
        if avg >= 2:
            rec = "Cut handling time"          # Zoro consistently faster than our promise
        elif avg <= -1:
            rec = "Add handling time"          # Zoro slower than our promise → late risk
        else:
            rec = "Keep"
        out.append({"asin": asin, "n": len(sl), "avg_slack": round(avg, 1),
                    "min": min(sl), "max": max(sl), "rec": rec})
    out.sort(key=lambda x: (x["rec"] != "Add handling time", -x["avg_slack"]))
    summary = {"orders": len(tot),
               "avg_slack": round(sum(tot) / len(tot), 1) if tot else 0,
               "faster": sum(1 for s in tot if s >= 2),
               "risk": sum(1 for s in tot if s < 0)}
    return {"asins": out[:60], "summary": summary}


def watch_list():
    """Zoro restock/handling-time watchlist (codes the hourly checker monitors)."""
    c = ro()
    try:
        rows = [dict(r) for r in c.execute(
            "SELECT zoro_code,label,last_availability,last_lead_days,last_lead_text,"
            "last_checked,alert,alert_at FROM zoro_watch "
            "ORDER BY (alert IS NOT NULL AND alert!='') DESC, zoro_code")]
    except sqlite3.OperationalError:
        rows = []   # table not created yet (checker hasn't run)
    c.close()
    return {"watch": rows}


def watch_add(body):
    code = (body.get("code") or "").strip().upper()
    if not code:
        return {"ok": False, "error": "code required"}
    label = (body.get("label") or "").strip()
    def w():
        c = rw()
        try:
            c.execute(_WATCH_DDL)
            c.execute("INSERT OR IGNORE INTO zoro_watch(zoro_code,label) VALUES(?,?)", (code, label))
            c.commit()
        finally:
            c.close()
    _retry_write(w)
    return {"ok": True}


def watch_del(body):
    code = (body.get("code") or "").strip().upper()
    def w():
        c = rw()
        try:
            c.execute("DELETE FROM zoro_watch WHERE zoro_code=?", (code,))
            c.commit()
        finally:
            c.close()
    _retry_write(w)
    return {"ok": True}


def watch_clear(body):
    code = (body.get("code") or "").strip().upper()
    def w():
        c = rw()
        try:
            c.execute("UPDATE zoro_watch SET alert=NULL, alert_at=NULL WHERE zoro_code=?", (code,))
            c.commit()
        finally:
            c.close()
    _retry_write(w)
    return {"ok": True}


# ── writes ──────────────────────────────────────────────────────────────────
def _resolve_amazon_oid(oid):
    if not oid:
        return oid
    oid_s = str(oid).strip()
    import re as _re
    if _re.match(r"^(?:WB|SO)\d+$", oid_s.upper()):
        c = ro()
        try:
            row = c.execute("SELECT amazon_order_id FROM closing_orders WHERE wb_order_id=? OR merchant_order_id LIKE ? LIMIT 1",
                            (oid_s.upper(), f"%{oid_s.upper()}%")).fetchone()
            if not row and _has_table("order_placer_orders"):
                row = c.execute("SELECT amazon_order_id FROM order_placer_orders WHERE wb_order_id=? LIMIT 1",
                                (oid_s.upper(),)).fetchone()
            if row and row[0]:
                return row[0]
        except Exception:
            pass
        finally:
            c.close()
    return oid_s


def add_note(body):
    oid = _resolve_amazon_oid(body.get("order_id")); note = (body.get("note") or "").strip()
    if not oid or not note:
        return {"ok": False, "error": "order_id+note required"}
    author = str(body.get("author") or "me")[:60]
    def w():
        c = rw()
        try:
            c.execute("INSERT INTO crm_notes(amazon_order_id, author, note) VALUES (?,?,?)",
                      (oid, author, note))
            c.commit()
        finally:
            c.close()
    try:
        _retry_write(w)              # cloud mode: also lands in the local slim copy (instant feedback)
    except Exception:
        if not OPS.CLOUD_MODE:
            raise
    if OPS.CLOUD_MODE:
        return OPS.push_crm_event("add_note", oid, {"note": note}, author)
    try:
        OPS.record_history(DB, oid, "note", None, None, note, author)
    except Exception:
        pass
    return {"ok": True}


ALLOWED = {"case_status", "case_type", "case_resolution", "resolved_at", "reminder_at",
           "reminder_note", "flag_color", "manual_delivered", "comments", "tags"}

_CRM_DDL = ("CREATE TABLE IF NOT EXISTS order_crm("
            "amazon_order_id TEXT PRIMARY KEY, case_status TEXT, case_type TEXT, "
            "case_resolution TEXT, resolved_at TEXT, reminder_at TEXT, reminder_note TEXT, "
            "flag_color TEXT, manual_delivered TEXT, comments TEXT, tags TEXT, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")


def _apply_crm_overlay(c, rows):
    """Overlay CRM fields (flag/case/comments/reminder/…) from order_crm onto rows so
    annotations work for ANY order — including Pending ones that aren't in closing_orders."""
    if not rows:
        return rows
    try:
        crm = {x["amazon_order_id"]: dict(x) for x in c.execute("SELECT * FROM order_crm")}
    except sqlite3.OperationalError:
        return rows
    for o in rows:
        ov = crm.get(o.get("amazon_order_id"))
        if ov:
            for k, v in ov.items():
                if k in ALLOWED and v not in (None, ""):
                    o[k] = v
    return rows


def update_order(body):
    oid = _resolve_amazon_oid(body.get("order_id"))
    if not oid:
        return {"ok": False, "error": "order_id required"}
    fields = {k: v for k, v in body.items() if k in ALLOWED}
    if not fields:
        return {"ok": False, "error": "no valid fields"}
    author = str(body.get("author") or "me")[:60]
    old = {}
    def w():
        nonlocal old
        c = rw()
        try:
            c.execute(_CRM_DDL)
            try:                                   # live table predates the tags column
                c.execute("ALTER TABLE order_crm ADD COLUMN tags TEXT")
            except Exception:
                pass
            try:                                   # previous values → append-only history
                r = c.execute("SELECT * FROM order_crm WHERE amazon_order_id=?", (oid,)).fetchone()
                old = dict(r) if r else {}
            except Exception:
                old = {}
            cols = ["amazon_order_id"] + list(fields)
            ph = ",".join("?" * len(cols))
            upd = ", ".join(f"{k}=excluded.{k}" for k in fields)
            c.execute(f"INSERT INTO order_crm({','.join(cols)}) VALUES({ph}) "
                      f"ON CONFLICT(amazon_order_id) DO UPDATE SET {upd}, updated_at=CURRENT_TIMESTAMP",
                      [oid] + list(fields.values()))
            # mirror onto closing_orders too (a no-op for Pending) so existing closing reads stay in sync
            try:
                sets = ", ".join(f"{k}=?" for k in fields)
                c.execute(f"UPDATE closing_orders SET {sets} WHERE amazon_order_id=?",
                          list(fields.values()) + [oid])
            except Exception:
                pass
            c.commit()
        finally:
            c.close()
    try:
        _retry_write(w)
    except Exception:
        if not OPS.CLOUD_MODE:
            raise
    if OPS.CLOUD_MODE:
        return OPS.push_crm_event("update_order", oid, fields, author)
    try:
        for k, v in fields.items():
            if str("" if old.get(k) is None else old.get(k)) != str("" if v is None else v):
                OPS.record_history(DB, oid, "field", k, old.get(k), v, author)
    except Exception:
        pass
    return {"ok": True}


# ── multi-user extras: persistent name, team chat (with media), reminders ──
_PREFS_DDL = ("CREATE TABLE IF NOT EXISTS dash_prefs(client_key TEXT PRIMARY KEY, name TEXT, "
              "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
_REM_DDL = ("CREATE TABLE IF NOT EXISTS dash_reminders(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "title TEXT, notes TEXT, due_at TEXT, assignee TEXT, done INTEGER DEFAULT 0, "
            "created_by TEXT, created_at TEXT, done_at TEXT, done_by TEXT)")
MEDIA_DIR = os.path.abspath(os.path.join(HERE, "..", "data", "chat_media"))
_MAX_MEDIA = 25 * 1024 * 1024   # 25 MB per file


def get_whoami(key, fallback_key=None):
    """Server-side copy of the display name, keyed by the per-BROWSER dash_uid cookie (so each
    person has their own name even when everyone arrives via the funnel/tunnel as 127.0.0.1).
    Falls back to the old IP-keyed row so a name saved before the uid scheme isn't lost."""
    name = ""
    if _has_table("dash_prefs"):
        c = ro()
        try:
            row = c.execute("SELECT name FROM dash_prefs WHERE client_key=?", (key,)).fetchone()
            if (not row or not row[0]) and fallback_key and fallback_key != key:
                row = c.execute("SELECT name FROM dash_prefs WHERE client_key=?", (fallback_key,)).fetchone()
            name = row[0] if row else ""
        finally:
            c.close()
    return {"name": name or ""}


def set_whoami(body, client_ip):
    name = (body.get("name") or "").strip()[:40]
    if not name:
        return {"ok": False, "error": "name required"}
    def w():
        c = rw()
        try:
            c.execute(_PREFS_DDL)
            c.execute("INSERT INTO dash_prefs(client_key,name) VALUES(?,?) "
                      "ON CONFLICT(client_key) DO UPDATE SET name=excluded.name, updated_at=CURRENT_TIMESTAMP",
                      (client_ip, name))
            c.commit()
        finally:
            c.close()
    try:
        _retry_write(w)
    except Exception as e:
        if not OPS.CLOUD_MODE:
            raise
        print(f"[set_whoami] Local write warning: {e}")
    if OPS.CLOUD_MODE:
        try:
            OPS.push_crm_event("set_whoami", client_ip, {"name": name}, name)
        except Exception:
            pass
    return {"ok": True, "name": name}


# (team chat — chat_list / chat_send — removed at the owner's request; the dash_chat
#  table is no longer read, written, or exported to the cloud.)


def serve_media(handler, p):
    """GET /chat_media/<file> — basename-only lookup so paths can't escape MEDIA_DIR."""
    import mimetypes
    fn = os.path.basename(urllib.parse.unquote(p.split("/chat_media/", 1)[1]))
    fp = os.path.join(MEDIA_DIR, fn)
    if not fn or not os.path.isfile(fp):
        return handler._send({"error": "not found"}, 404)
    with open(fp, "rb") as f:
        return handler._send(f.read(), ctype=mimetypes.guess_type(fn)[0] or "application/octet-stream")


# ── CUSTOMER PICTURES — Gmail image attachments tied to an ASIN's orders ─────
# The archive sync stores only attachment METADATA; the bytes stay in Gmail.
# First request per message pulls the images via the Gmail API (existing token
# files, refresh-only — never an interactive OAuth flow) and caches them under
# data/msg_media/<gmail_account>/<message_id>/ permanently.
MSG_MEDIA_DIR = os.path.abspath(os.path.join(HERE, "..", "data", "msg_media"))
_TOKEN_DIR = os.path.abspath(os.path.join(HERE, "..", "data", "All_Tokens"))
_ORDER_ID_RX = re.compile(r"\b(\d{3}-\d{7}-\d{7})\b")


def _link_attachment_messages():
    """Fill archive_messages.amazon_order_id from the order id Amazon embeds in the
    subject/body ('… (Order: 114-…)') — the sync leaves it empty on these rows."""
    def w():
        c = rw()
        rows = c.execute("SELECT id, subject, message_body, raw_body_snippet FROM archive_messages "
                         "WHERE has_attachment=1 AND COALESCE(amazon_order_id,'')=''").fetchall()
        n = 0
        for r in rows:
            m = _ORDER_ID_RX.search(" ".join(filter(None, (r["subject"], r["message_body"], r["raw_body_snippet"]))))
            if m:
                c.execute("UPDATE archive_messages SET amazon_order_id=? WHERE id=?", (m.group(1), r["id"]))
                n += 1
        c.commit(); c.close(); return n
    try:
        return _retry_write(w)
    except Exception:
        return 0


def _gmail_service_readonly(acct):
    """Gmail API service from the account's existing token file. Refreshes a stale
    token in place but NEVER starts an interactive OAuth flow (headless server)."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    tok = os.path.join(_TOKEN_DIR, f"{acct}_token.json")
    if not os.path.isfile(tok):
        raise RuntimeError(f"no Gmail token file for {acct}")
    creds = Credentials.from_authorized_user_file(tok)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(tok, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError(f"Gmail token for {acct} needs re-auth (run any placer/sync login once)")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _walk_msg_parts(part, out):
    if part.get("filename") and (part.get("body") or {}).get("attachmentId"):
        out.append(part)
    for sp in part.get("parts") or []:
        _walk_msg_parts(sp, out)


def _fetch_msg_images(acct, msg_id):
    """Image attachments of one Gmail message, from cache or downloaded now.
    A .done marker means the message was already fetched (even if it had zero
    images), so Gmail is hit at most once per message ever."""
    import base64
    safe = lambda s: re.sub(r"[^A-Za-z0-9_-]", "", s or "")
    d = os.path.join(MSG_MEDIA_DIR, safe(acct), safe(msg_id))
    if os.path.isfile(os.path.join(d, ".done")):
        return [os.path.join(d, f) for f in sorted(os.listdir(d)) if not f.startswith(".")]
    svc = _gmail_service_readonly(acct)
    msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
    parts = []
    _walk_msg_parts(msg.get("payload") or {}, parts)
    os.makedirs(d, exist_ok=True)
    saved = []
    for i, part in enumerate(parts):
        mt = (part.get("mimeType") or "").lower()
        fn = part.get("filename") or f"attachment_{i}"
        if not (mt.startswith("image/") or re.search(r"\.(jpe?g|png|gif|webp|heic)$", fn, re.I)):
            continue
        att = svc.users().messages().attachments().get(
            userId="me", messageId=msg_id, id=part["body"]["attachmentId"]).execute()
        fp = os.path.join(d, f"{i:02d}_" + re.sub(r"[^A-Za-z0-9._-]", "_", fn)[:80])
        with open(fp, "wb") as f:
            f.write(base64.urlsafe_b64decode(att["data"]))
        saved.append(fp)
    with open(os.path.join(d, ".done"), "w") as f:
        f.write("ok")
    return saved


def asin_pics(q):
    """GET /api/asin_pics?asin=… — every customer picture received on messages of
    this ASIN's orders, grouped per order/message, with cached image URLs."""
    asin = (q.get("asin", [""])[0] or "").strip()
    if not asin:
        return {"error": "asin required", "groups": [], "total": 0}
    _link_attachment_messages()
    c = ro()
    oids = [r[0] for r in c.execute("SELECT DISTINCT amazon_order_id FROM v_orders_all WHERE asin=?", (asin,))]
    msgs = []
    if oids:
        ph = ",".join("?" * len(oids))
        msgs = [dict(r) for r in c.execute(
            f"SELECT amazon_order_id, gmail_account, email_timestamp, subject, customer_name, message_id "
            f"FROM archive_messages WHERE has_attachment=1 AND COALESCE(message_id,'')!='' "
            f"AND amazon_order_id IN ({ph}) ORDER BY email_timestamp", oids)]
    c.close()
    groups, errors = [], []
    for m in msgs:
        try:
            files = _fetch_msg_images(m["gmail_account"], m["message_id"])
        except Exception as e:
            errors.append(f'{m["amazon_order_id"]} ({m["gmail_account"]}): {e}')
            continue
        if not files:
            continue
        groups.append({"order_id": m["amazon_order_id"], "ts": m["email_timestamp"] or "",
                       "customer": m["customer_name"] or "", "subject": m["subject"] or "",
                       "account": m["gmail_account"] or "",
                       "images": [{"url": "/msg_media/" + os.path.relpath(f, MSG_MEDIA_DIR).replace(os.sep, "/"),
                                   "name": os.path.basename(f)} for f in files]})
    return {"asin": asin, "groups": groups,
            "total": sum(len(g["images"]) for g in groups), "errors": errors}


def asin_pics_zip(handler, q):
    """GET /api/asin_pics_zip?asin=… — one zip of all the ASIN's customer pictures."""
    import io, zipfile
    r = asin_pics(q)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for g in r.get("groups", []):
            for im in g["images"]:
                fp = os.path.join(MSG_MEDIA_DIR, *im["url"].split("/msg_media/", 1)[1].split("/"))
                if os.path.isfile(fp):
                    z.write(fp, arcname=f'{g["order_id"] or "no_order"}/{im["name"]}')
    name = re.sub(r"[^A-Za-z0-9_-]", "", r.get("asin") or "pictures") or "pictures"
    return handler._send(buf.getvalue(), ctype="application/zip",
                         hdrs=[("Content-Disposition", f'attachment; filename="{name}_customer_pictures.zip"')])


_PL_IMG_DIRS = [os.path.abspath(os.path.join(HERE, "..", "private_label", "output", "images")),
                os.path.abspath(os.path.join(HERE, "..", "private_label", "constant_images"))]


def serve_pl_images(handler, p):
    """GET /pl_images/<file> — PUBLIC (no login): Amazon's feed processor must be
    able to fetch listing images by URL. Serves ONLY the two private-label image
    folders, basename-only so paths can't escape."""
    import mimetypes
    fn = os.path.basename(urllib.parse.unquote(p.split("/pl_images/", 1)[1]))
    if not fn or fn.startswith("."):
        return handler._send({"error": "bad path"}, 400)
    for d in _PL_IMG_DIRS:
        fp = os.path.join(d, fn)
        if os.path.isfile(fp):
            with open(fp, "rb") as f:
                return handler._send(f.read(), ctype=mimetypes.guess_type(fn)[0] or "image/jpeg")
    return handler._send({"error": "not found"}, 404)


def serve_msg_media(handler, p):
    """GET /msg_media/<acct>/<msgid>/<file> — every component whitelisted, no traversal."""
    import mimetypes
    parts = [s for s in urllib.parse.unquote(p.split("/msg_media/", 1)[1]).split("/") if s]
    if len(parts) != 3 or any(s.startswith(".") or not re.fullmatch(r"[A-Za-z0-9._-]+", s) for s in parts):
        return handler._send({"error": "bad path"}, 400)
    fp = os.path.join(MSG_MEDIA_DIR, *parts)
    if not os.path.isfile(fp):
        return handler._send({"error": "not found"}, 404)
    with open(fp, "rb") as f:
        return handler._send(f.read(), ctype=mimetypes.guess_type(parts[-1])[0] or "application/octet-stream")


def reminders_list():
    if not _has_table("dash_reminders"):
        return {"reminders": []}
    c = ro()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM dash_reminders ORDER BY done ASC, "
        "CASE WHEN COALESCE(due_at,'')='' THEN 1 ELSE 0 END, due_at ASC, id DESC")]
    c.close()
    return {"reminders": rows}


def reminder_add(body):
    title = (body.get("title") or "").strip()[:300]
    if not title:
        return {"ok": False, "error": "title required"}
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    def w():
        c = rw()
        try:
            c.execute(_REM_DDL)
            c.execute("INSERT INTO dash_reminders(title,notes,due_at,assignee,created_by,created_at) "
                      "VALUES(?,?,?,?,?,?)",
                      (title, (body.get("notes") or "").strip()[:2000],
                       (body.get("due_at") or "").strip(), (body.get("assignee") or "").strip()[:40],
                       (body.get("created_by") or "").strip()[:40], ts))
            c.commit()
        finally:
            c.close()
    _retry_write(w)
    return {"ok": True}


def reminder_update(body):
    rid = body.get("id")
    if not rid:
        return {"ok": False, "error": "id required"}
    if body.get("delete"):
        def w():
            c = rw()
            try:
                c.execute("DELETE FROM dash_reminders WHERE id=?", (rid,))
                c.commit()
            finally:
                c.close()
        _retry_write(w)
        return {"ok": True}
    sets, vals = [], []
    if "done" in body:
        done = 1 if body.get("done") else 0
        sets += ["done=?", "done_at=?", "done_by=?"]
        vals += [done, datetime.now().strftime("%Y-%m-%d %H:%M:%S") if done else "",
                 (body.get("done_by") or "").strip()[:40] if done else ""]
    for k in ("title", "notes", "due_at", "assignee"):
        if k in body:
            sets.append(f"{k}=?"); vals.append((body.get(k) or "").strip())
    if not sets:
        return {"ok": False, "error": "nothing to update"}
    def w():
        c = rw()
        try:
            c.execute(f"UPDATE dash_reminders SET {', '.join(sets)} WHERE id=?", vals + [rid])
            c.commit()
        finally:
            c.close()
    _retry_write(w)
    return {"ok": True}


def resolve_insight(body):
    t = body.get("insight_type"); k = body.get("ref_key")
    if not t or not k:
        return {"ok": False, "error": "insight_type+ref_key required"}
    def w():
        c = rw(); c.execute(
            "INSERT OR REPLACE INTO insight_resolutions(insight_type, ref_key, note) VALUES (?,?,?)",
            (t, k, body.get("note", ""))); c.commit(); c.close()
    _retry_write(w)
    return {"ok": True}


# ── MONTHLY P&L (manual close + stakeholder split) ───────────────────────────
_PNL_DDL = ("CREATE TABLE IF NOT EXISTS monthly_pnl("
            "month TEXT PRIMARY KEY, payoneer REAL DEFAULT 0, investment REAL DEFAULT 180000, "
            "personal REAL DEFAULT 0, amazon REAL DEFAULT 0, splits TEXT, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
_DEFAULT_SPLITS = [{"name": "Imran", "pct": 75}, {"name": "Kam", "pct": 18},
                   {"name": "Uncle", "pct": 3}, {"name": "Asad", "pct": 4}]

def _pnl_view(r):
    pay, inv = _f(r["payoneer"]) or 0, _f(r["investment"]) or 0
    per, amz = _f(r["personal"]) or 0, _f(r["amazon"]) or 0
    final = round(amz + pay - inv - per, 2)   # Amazon + Payoneer − Investment − Personal
    try:
        splits = json.loads(r["splits"]) if r["splits"] else list(_DEFAULT_SPLITS)
    except Exception:
        splits = list(_DEFAULT_SPLITS)
    splits = [{"name": s.get("name"), "pct": _f(s.get("pct")) or 0,
               "amount": round(final * (_f(s.get("pct")) or 0) / 100, 2)} for s in splits]
    return {"month": r["month"], "payoneer": pay, "investment": inv, "personal": per,
            "amazon": amz, "final_profit": final, "splits": splits, "updated_at": r["updated_at"]}

def get_pnl(q):
    month = (q.get("month", [""])[0] or "").strip()
    def rd():
        c = rw()
        try:
            c.execute(_PNL_DDL)
            c.commit()
            return [_pnl_view(r) for r in c.execute("SELECT * FROM monthly_pnl ORDER BY month DESC")]
        finally:
            c.close()
    hist = _retry_write(rd)
    cur = next((h for h in hist if h["month"] == month), None) if month else (hist[0] if hist else None)
    if not cur:
        cur = {"month": month, "payoneer": 0, "investment": 180000, "personal": 0, "amazon": 0,
               "final_profit": 0.0, "updated_at": None,
               "splits": [{"name": s["name"], "pct": s["pct"], "amount": 0.0} for s in _DEFAULT_SPLITS]}
    return {"current": cur, "history": hist, "default_splits": _DEFAULT_SPLITS}

def save_pnl(body):
    month = (body.get("month") or "").strip()
    if not month:
        return {"ok": False, "error": "month required"}
    vals = {"payoneer": _f(body.get("payoneer")) or 0, "investment": _f(body.get("investment")) or 0,
            "personal": _f(body.get("personal")) or 0, "amazon": _f(body.get("amazon")) or 0,
            "splits": json.dumps(body.get("splits") or _DEFAULT_SPLITS)}
    def w():
        c = rw()
        try:
            c.execute(_PNL_DDL)
            cols = ["month"] + list(vals)
            upd = ", ".join(f"{k}=excluded.{k}" for k in vals)
            c.execute(f"INSERT INTO monthly_pnl({','.join(cols)}) VALUES({','.join('?'*len(cols))}) "
                      f"ON CONFLICT(month) DO UPDATE SET {upd}, updated_at=CURRENT_TIMESTAMP",
                      [month] + list(vals.values()))
            c.commit()
        finally:
            c.close()
    _retry_write(w)
    return get_pnl({"month": [month]})


# ── HOANK P&L (balance-sheet style — different fields + formula than Hathoro) ─
# Profit = (Amazon balance + Wise balance) − (Net30 payable + Amex payable)
#          − Personal balance − Other total costs
# (Owner confirmed 2026-08-13: BOTH payables subtracted — Amex and Net30 are paid
#  separately — and Other costs is a real expense line, also subtracted.)
_PNL_HOANK_DDL = ("CREATE TABLE IF NOT EXISTS hoank_pnl("
                  "month TEXT PRIMARY KEY, amazon REAL DEFAULT 0, wise REAL DEFAULT 0, "
                  "net30 REAL DEFAULT 0, amex REAL DEFAULT 0, personal REAL DEFAULT 0, "
                  "other_costs REAL DEFAULT 0, "
                  "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")

_PNL_HOANK_FIELDS = ("amazon", "wise", "net30", "amex", "personal", "other_costs")


def _pnl_hoank_view(r):
    v = {k: (_f(r[k]) or 0) for k in _PNL_HOANK_FIELDS}
    total = round(v["amazon"] + v["wise"], 2)
    profit = round(total - (v["net30"] + v["amex"]) - v["personal"] - v["other_costs"], 2)
    return {"month": r["month"], **v, "total_balance": total, "final_profit": profit,
            "updated_at": r["updated_at"]}


def get_pnl_hoank(q):
    month = (q.get("month", [""])[0] or "").strip()
    def rd():
        c = rw()
        try:
            c.execute(_PNL_HOANK_DDL)
            c.commit()
            return [_pnl_hoank_view(r) for r in c.execute("SELECT * FROM hoank_pnl ORDER BY month DESC")]
        finally:
            c.close()
    hist = _retry_write(rd)
    cur = next((h for h in hist if h["month"] == month), None) if month else (hist[0] if hist else None)
    if not cur:
        cur = {"month": month, **{k: 0 for k in _PNL_HOANK_FIELDS},
               "total_balance": 0.0, "final_profit": 0.0, "updated_at": None}
    return {"current": cur, "history": hist}


def save_pnl_hoank(body):
    month = (body.get("month") or "").strip()
    if not month:
        return {"ok": False, "error": "month required"}
    vals = {k: (_f(body.get(k)) or 0) for k in _PNL_HOANK_FIELDS}
    def w():
        c = rw()
        try:
            c.execute(_PNL_HOANK_DDL)
            cols = ["month"] + list(vals)
            upd = ", ".join(f"{k}=excluded.{k}" for k in vals)
            c.execute(f"INSERT INTO hoank_pnl({','.join(cols)}) VALUES({','.join('?'*len(cols))}) "
                      f"ON CONFLICT(month) DO UPDATE SET {upd}, updated_at=CURRENT_TIMESTAMP",
                      [month] + list(vals.values()))
            c.commit()
        finally:
            c.close()
    _retry_write(w)
    return get_pnl_hoank({"month": [month]})


# ── BANK / PAYONEER RECONCILIATION (Zoro charges vs DB) ──────────────────────
_ZINBOX = {"Hathoro": ("abecomllc26", "salesmercentile", "wrenchlyllc"),
           "HOANK": ("asad_amazon_us", "inhur_llc")}

def bank_reconcile(body):
    """Parse an uploaded Payoneer activity CSV, total Zoro paid/refunded, and reconcile
    against archive_zoro / closing_orders to surface Zoro orders placed but never captured."""
    import csv as _csv, io, re as _re
    txt = body.get("csv") or ""
    account = (body.get("account") or "Hathoro").strip()
    if account.lower() == "all":
        account = "Hathoro"
    if not txt:
        return {"ok": False, "error": "no CSV provided"}
    def num(x):
        try:
            return round(float((x or "0").replace(",", "")), 2)
        except Exception:
            return 0.0
    rows = list(_csv.DictReader(io.StringIO(txt)))
    zoro = [r for r in rows if "ZORO" in (str(r.get("Description") or "")).upper()]
    if not zoro:
        return {"ok": False, "error": "no ZORO charges found in CSV (is this the right statement?)"}
    paid = sum(abs(num(r.get("Debit amount"))) for r in zoro)
    refund = sum(num(r.get("Credit amount")) for r in zoro)
    ncharge = sum(1 for r in zoro if num(r.get("Debit amount")) != 0)
    nrefund = sum(1 for r in zoro if num(r.get("Credit amount")) > 0)
    months = set()
    for r in zoro:
        m = _re.match(r"(\d{2})-(\d{2})-(\d{4})", (r.get("Transaction date") or "").strip())
        if m:
            months.add(f"{m.group(3)}-{m.group(1)}")
    months = sorted(months)
    c = ro()
    inboxes = _ZINBOX.get(account, _ZINBOX["Hathoro"])
    ph = ",".join("?" * len(inboxes))
    az = {}
    if months:
        mwhere = " OR ".join("substr(email_timestamp,1,7)=?" for _ in months)
        for wb, tot in c.execute(
                f"SELECT wb_order_id, order_total FROM archive_zoro WHERE gmail_account IN ({ph}) "
                f"AND ({mwhere}) ORDER BY email_timestamp", list(inboxes) + months):
            if wb:
                az[wb] = round(tot or 0, 2)
    ID = _re.compile(r"(?:WB|SO)\d+")
    cl = set()
    for moid, wb in c.execute("SELECT merchant_order_id, wb_order_id FROM closing_orders WHERE account=?", (account,)):
        for x in ID.findall((moid or "").upper()):
            cl.add(x)
        if wb:
            cl.add(wb)
    c.close()
    receipts_sum = round(sum(az.values()), 2)
    gap = sorted([(wb, az[wb]) for wb in az if wb not in cl], key=lambda x: -(x[1] or 0))
    return {"ok": True, "account": account, "months": months,
            "paid": round(paid, 2), "refund": round(refund, 2), "net": round(paid - refund, 2),
            "charges": ncharge, "refunds": nrefund,
            "receipts_n": len(az), "receipts_sum": receipts_sum,
            "bank_minus_receipts": round(paid - receipts_sum, 2),
            "uncaptured_n": len(gap), "uncaptured_sum": round(sum(a or 0 for _, a in gap), 2),
            "uncaptured": [{"wb": w, "amount": a} for w, a in gap[:1000]]}


# ── ZORO ORDER DETAIL (per-WB-leg receipt + tracking; handles multi-WB) ──────
def order_zoro(q):
    import re as _re
    key = (q.get("id", [""])[0] or "").strip()
    if not key:
        return {"error": "id required"}
    c = ro()
    
    # 1. Look up in closing_orders (by Amazon ID or WB ID)
    co = None
    try:
        co = c.execute("SELECT * FROM closing_orders WHERE amazon_order_id=?", (key,)).fetchone()
        if not co and _re.match(r"(?:WB|SO)\d+$", key.upper()):
            co = c.execute("SELECT * FROM closing_orders WHERE wb_order_id=? OR merchant_order_id LIKE ? LIMIT 1",
                           (key.upper(), f"%{key.upper()}%")).fetchone()
    except Exception:
        co = None

    # If still not found in closing_orders, check order_placer_orders
    po = None
    if not co and _has_table("order_placer_orders"):
        try:
            po = c.execute("SELECT * FROM order_placer_orders WHERE amazon_order_id=? OR wb_order_id=? LIMIT 1",
                           (key, key.upper())).fetchone()
        except Exception:
            po = None

    if co:
        co = dict(co)
        ids = _re.findall(r"(?:WB|SO)\d+", (co.get("merchant_order_id") or "").upper())
        if not ids and co.get("wb_order_id"):
            ids = [co["wb_order_id"]]
    elif po:
        po = dict(po)
        co = {
            "amazon_order_id": po.get("amazon_order_id"),
            "merchant_order_id": po.get("wb_order_id"),
            "account": po.get("account"),
            "profile_name": po.get("profile_name"),
            "order_status": "Placed",
            "order_label": "Incomplete",
            "asin": po.get("asin"),
            "sku": po.get("sku"),
            "title": po.get("title"),
            "quantity": po.get("quantity"),
            "earnings": po.get("earnings"),
            "zoro_order_total": po.get("zoro_order_total"),
            "profit_dollars": po.get("profit_dollars"),
            "profit_percent": po.get("profit_percent"),
            "tracking_ids": po.get("tracking_ids"),
            "ship_name": po.get("ship_name"),
            "ship_street": po.get("ship_street"),
            "ship_city": po.get("ship_city"),
            "ship_state": po.get("ship_state"),
            "ship_postal_code": po.get("ship_postal_code"),
            "ship_phone": po.get("ship_phone"),
            "purchase_date": po.get("purchase_date"),
            "wb_order_id": po.get("wb_order_id"),
        }
        ids = [po["wb_order_id"]] if po.get("wb_order_id") else []
    else:
        co = None
        ids = [key.upper()] if _re.match(r"(?:WB|SO)\d+$", key.upper()) else []

    target_oid = (co.get("amazon_order_id") if co else None) or key

    has_zoro = _has_table("archive_zoro")
    has_trk = _has_table("archive_tracking")

    legs, seen = [], set()
    for leg in ids:
        if leg in seen:
            continue
        seen.add(leg)
        z = None
        if has_zoro:
            try:
                rz = c.execute("SELECT wb_order_id, gmail_account, zoro_account_name, order_total, subtotal, tax, "
                               "shipping_cost, coupon_savings, sku, qty, payment_method, payment_card_last4, ship_zip, "
                               "order_date, email_timestamp FROM archive_zoro WHERE wb_order_id=? ORDER BY email_timestamp DESC LIMIT 1", (leg,)).fetchone()
                z = dict(rz) if rz else None
            except Exception:
                z = None

        t = None
        if has_trk:
            try:
                rt = c.execute("SELECT tracking_ids, carrier, email_timestamp FROM archive_tracking WHERE wb_order_id=? "
                               "ORDER BY email_timestamp DESC LIMIT 1", (leg,)).fetchone()
                t = dict(rt) if rt else None
            except Exception:
                t = None

        # Fallback synthesis from closing_orders or order_placer_orders (Cloud Mode / slim DB)
        if not z and co and (co.get("wb_order_id") == leg or leg in (co.get("merchant_order_id") or "")):
            z = {
                "wb_order_id": leg,
                "gmail_account": co.get("zoro_gmail_account") or co.get("zoro_account_name"),
                "zoro_account_name": co.get("zoro_account_name") or "Zoro",
                "order_total": co.get("zoro_order_total"),
                "subtotal": co.get("zoro_subtotal"),
                "tax": co.get("zoro_tax"),
                "shipping_cost": co.get("zoro_shipping"),
                "coupon_savings": co.get("coupon_savings"),
                "sku": co.get("zoro_sku") or co.get("sku"),
                "qty": co.get("zoro_qty") or co.get("quantity"),
                "payment_method": co.get("zoro_payment_method"),
                "payment_card_last4": co.get("zoro_payment_card"),
                "ship_zip": co.get("zoro_buyer_zip") or co.get("ship_postal_code"),
                "order_date": co.get("zoro_order_date") or (str(co.get("purchase_date") or "")[:10]),
                "email_timestamp": co.get("zoro_email_ts") or co.get("sold_email_time"),
            }
        if not t and co and (co.get("wb_order_id") == leg or leg in (co.get("merchant_order_id") or "")) and co.get("tracking_ids"):
            t = {
                "tracking_ids": co.get("tracking_ids"),
                "carrier": co.get("carrier") or "FedEx",
                "email_timestamp": co.get("shipped_email_time") or co.get("delivery_date"),
            }

        cap_id = co.get("amazon_order_id") if co else None
        if not cap_id:
            try:
                cap = c.execute("SELECT amazon_order_id FROM closing_orders WHERE merchant_order_id LIKE ? OR wb_order_id=? LIMIT 1",
                                (f"%{leg}%", leg)).fetchone()
                cap_id = cap[0] if cap else None
            except Exception:
                cap_id = None

        legs.append({"wb": leg, "zoro": z, "tracking": t, "captured_in": cap_id})

    crm = None
    if _has_table("order_crm"):
        try:
            rcrm = c.execute("SELECT * FROM order_crm WHERE amazon_order_id=?", (target_oid,)).fetchone()
            crm = dict(rcrm) if rcrm else None
        except Exception:
            crm = None

    notes = []
    if _has_table("crm_notes"):
        try:
            notes = [dict(r) for r in c.execute("SELECT author, note, created_at FROM crm_notes WHERE amazon_order_id=? ORDER BY created_at DESC", (target_oid,))]
        except Exception:
            notes = []

    c.close()
    return {"key": key, "order": co, "legs": legs, "crm": crm, "notes": notes,
            "zoro_total": round(sum(((l.get("zoro") or {}).get("order_total") or 0) for l in legs if l.get("zoro")), 2)}


# ── ZORO CATALOG (daily TSV build → combined_reports.db) ────────────────────
ZORO_TSV_DB = os.path.abspath(os.path.join(HERE, "..", "..", "TSV Files", "combined_reports.db"))

def zoro_tsv(q):
    """Live Zoro catalog lookup for the ASIN modal / order drawer.
    /api/zoro_tsv?codes=G123,G456 → {"items": {code: {lead_time, availability, …}}}.

    combined_reports.db (zoro_data, ~2M rows) is REBUILT daily by quick_download.py via
    pandas to_sql(if_exists="replace"), which drops any index. insights.py already
    re-creates ix_zoro_no best-effort on every /api/insights load; we do the same here
    so the endpoint is self-healing right after a rebuild. Measured: indexed lookup
    ≈0.2 ms/code (batch IN(6) ≈0 ms) — no LIMIT tricks needed.
    The file may be ABSENT or mid-rebuild → everything wrapped in try/except; any
    failure returns {"items": {}} so the UI just omits the section."""
    codes = [c.strip().upper() for c in (q.get("codes", [""])[0] or "").split(",") if c.strip()]
    codes = list(dict.fromkeys(codes))[:60]     # dedupe, cap
    if not codes or not os.path.exists(ZORO_TSV_DB):
        return {"items": {}}
    try:
        c = sqlite3.connect(f"file:{ZORO_TSV_DB}?mode=ro", uri=True, timeout=5)
        try:
            c.execute("CREATE INDEX IF NOT EXISTS ix_zoro_no ON zoro_data(zoro_no)")  # no-op on RO
        except Exception:
            pass
        ph = ",".join("?" * len(codes))
        rows = c.execute(
            "SELECT zoro_no, lead_time, availabilityStatus, sales_price, unit_price, "
            f"min_order_qty, restricted_states FROM zoro_data WHERE zoro_no IN ({ph})",
            codes).fetchall()
        c.close()
    except Exception:
        return {"items": {}}   # absent / mid-rebuild / locked → UI omits the section
    items = {}
    for r in rows:
        if r[0] in items:
            continue
        items[r[0]] = {"code": r[0], "lead_time": r[1], "availability": r[2],
                       "sales_price": r[3], "unit_price": r[4],
                       "min_order_qty": r[5], "restricted_states": r[6]}
    return {"items": items}


# ── LISTINGS (growth side: what to delete / pause / revive / relist) ──────────
_LISTING_ACTIONS = ("DELETE_NEVER_SOLD", "DELETE_DORMANT",
                    "MAKE_INACTIVE", "MAKE_ACTIVE", "KEEP")


# Operator labels live in their OWN table, never in listing_health: the nightly
# engine run deletes and rebuilds every listing_health row, which would wipe them.
# Keyed by (account, sku) so a label survives re-runs, price changes and status
# flips — the listing is the same listing.
_LISTING_LABELS = ("", "Deleted", "Kept", "Inactivated", "Activated", "Relisted", "Later")

_LISTING_LABEL_DDL = """
CREATE TABLE IF NOT EXISTS listing_labels (
    account    TEXT NOT NULL,
    sku        TEXT NOT NULL,
    label      TEXT,
    note       TEXT,
    updated_at TEXT,
    updated_by TEXT,
    PRIMARY KEY (account, sku)
);
CREATE INDEX IF NOT EXISTS ix_listing_labels_label ON listing_labels(label);
"""


def listing_label_set(body):
    """Set/clear one listing's operator label. Empty label removes the row."""
    account = (body.get("account") or "").strip()
    sku = (body.get("sku") or "").strip()
    if not account or not sku:
        return {"error": "account and sku required"}
    label = (body.get("label") or "").strip()
    if label and label not in _LISTING_LABELS:
        return {"error": f"unknown label {label!r}"}
    note = (body.get("note") or "").strip()
    who = (body.get("by") or "").strip()[:60]

    def _w():
        c = rw()
        try:
            c.executescript(_LISTING_LABEL_DDL)
            if not label and not note:
                c.execute("DELETE FROM listing_labels WHERE account=? AND sku=?",
                          (account, sku))
            else:
                c.execute(
                    "INSERT INTO listing_labels (account,sku,label,note,updated_at,updated_by) "
                    "VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(account,sku) DO UPDATE SET "
                    "label=excluded.label, note=excluded.note, "
                    "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                    (account, sku, label, note,
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     who))
            c.commit()
        finally:
            c.close()
        return {"ok": True, "account": account, "sku": sku, "label": label}

    return _retry_write(_w)


def listings(q):
    """Listing-health action lists, computed by engine/listings.py.

    Read-only: this endpoint never edits Amazon. It surfaces the reviewed lists
    the operator acts on by hand, plus the freshness of the three inputs so a
    stale ALR or a stale supplier feed is visible rather than silently skewing
    the numbers.
    """
    action = (q.get("action", [""])[0] or "").upper()
    account = q.get("account", [""])[0]
    search = (q.get("q", [""])[0] or "").strip()
    limit = max(1, min(int(q.get("limit", ["500"])[0] or 500), 10000))

    c = ro()
    try:
        if not c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                         "AND name='listing_health'").fetchone():
            return {"ready": False,
                    "error": "listing_health table missing — run "
                             "scripts/refresh_zoro_and_listings.py first",
                    "counts": {}, "rows": []}

        counts = {}
        for acct, act, n in c.execute(
                "SELECT account, action, COUNT(*) FROM listing_health GROUP BY 1,2"):
            counts.setdefault(acct, {})[act] = n
        # Card totals must honour the account filter, otherwise picking HOANK
        # shows its rows under Hathoro-inclusive headline numbers.
        scope = ({account: counts.get(account, {})} if account else counts)
        totals = {a: sum(v.get(a, 0) for v in scope.values()) for a in _LISTING_ACTIONS}

        where, args = [], []
        if action in _LISTING_ACTIONS:
            where.append("action = ?"); args.append(action)
        if account:
            where.append("account = ?"); args.append(account)
        if search:
            where.append("(sku LIKE ? OR asin LIKE ? OR title LIKE ? OR zoro_code LIKE ?)")
            args += [f"%{search}%"] * 4
        c.executescript(_LISTING_LABEL_DDL)      # first call creates it
        label_f = (q.get("label", [""])[0] or "").strip()
        if label_f == "__none__":
            where.append("COALESCE(lb.label,'') = ''")
        elif label_f:
            where.append("lb.label = ?"); args.append(label_f)

        sql = ("SELECT h.*, COALESCE(lb.label,'') AS op_label, "
               "COALESCE(lb.note,'') AS op_note, lb.updated_at AS op_at "
               "FROM listing_health h "
               "LEFT JOIN listing_labels lb "
               "  ON lb.account = h.account AND lb.sku = h.sku")
        where = [w.replace("account = ?", "h.account = ?")
                  .replace("action = ?", "h.action = ?")
                  .replace("(sku LIKE ?", "(h.sku LIKE ?")
                  .replace("asin LIKE ?", "h.asin LIKE ?")
                  .replace("title LIKE ?", "h.title LIKE ?")
                  .replace("zoro_code LIKE ?", "h.zoro_code LIKE ?") for w in where]
        if where:
            sql += " WHERE " + " AND ".join(where)
        # Biggest sellers first — within any list, the high-volume SKUs are the
        # ones worth a human's attention.
        sql += " ORDER BY h.units_ever DESC, h.units_365d DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in c.execute(sql, args)]

        relist = []
        if c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                     "AND name='listing_relist'").fetchone():
            rsql = "SELECT * FROM listing_relist"
            rargs = []
            if account:                       # same filter as the main table
                rsql += " WHERE account = ?"
                rargs.append(account)
            rsql += " ORDER BY units_365d DESC LIMIT 10000"
            relist = [dict(r) for r in c.execute(rsql, rargs)]

        label_counts = {r[0] or "": r[1] for r in c.execute(
            "SELECT COALESCE(label,''), COUNT(*) FROM listing_labels GROUP BY 1")}
        run = c.execute("SELECT run_at, settings, meta, zoro_note FROM listing_runs "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        c.close()

    info = {}
    if run:
        info = {"run_at": run["run_at"], "zoro_note": run["zoro_note"]}
        for k, raw in (("settings", run["settings"]), ("meta", run["meta"])):
            try:
                info[k] = json.loads(raw or "")
            except (ValueError, TypeError):
                info[k] = {}

    # Feed age drives whether the stock actions are trustworthy at all.
    feed_age = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from engine.listings import zoro_feed_age_days
        feed_age = zoro_feed_age_days()
    except Exception:
        pass

    return {"ready": True, "counts": counts, "totals": totals,
            "rows": rows, "relist": relist, "info": info,
            "feed_age_days": feed_age, "action": action, "account": account,
            "labels": [l for l in _LISTING_LABELS if l],
            "label_counts": label_counts, "label": label_f}


# ── UNMAPPED ASINS (orders the placer skipped: ASIN not in the verified SKU sheet) ──
def unmapped_asins(q):
    """From the placer run logs (data/rajar_logs/run_*.csv), the orders whose LATEST status
    is UNMAPPED_ASIN — i.e. the ASIN isn't in the verified SKU sheet yet, so they couldn't be
    placed and need YOU to label/map the ASIN. Latest-status-per-order means once you map an
    ASIN and the placer re-runs, those orders drop off automatically."""
    import csv as _csv, glob, time as _time
    rajar = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "rajar_logs")
    _cutoff = _time.time() - LIVE_WINDOW_S
    _acct = q.get("account", [""])[0]   # "Hathoro" | "HOANK" | "" (all)
    latest = {}
    for f in sorted(glob.glob(os.path.join(rajar, "run_*.csv"))):     # filename sorts chronologically
        try:
            if os.path.getmtime(f) < _cutoff:   # only currently-active runs → live/current view
                continue
        except OSError:
            continue
        if _acct and _run_account(f) != _acct:   # account filter (Hathoro=DM / HOANK=HS)
            continue
        try:
            for r in _csv.DictReader(open(f, encoding="utf-8", errors="replace")):
                aid = (r.get("Amazon_ID") or "").strip()
                if aid:
                    latest[aid] = {"status": (r.get("Status") or "").strip(),
                                   "asin": (r.get("ASIN") or "").strip(),
                                   "buyer": (r.get("Buyer") or "").strip(),
                                   "city": (r.get("City") or "").strip(),
                                   "state": (r.get("State") or "").strip(),
                                   "run": os.path.basename(f).replace("run_", "").replace(".csv", "")}
        except Exception:
            continue
    groups = {}
    for aid, d in latest.items():
        if d["status"] != "UNMAPPED_ASIN":
            continue
        groups.setdefault(d["asin"] or "(no ASIN)", []).append(
            {"order_id": aid, "buyer": d["buyer"], "city": d["city"], "state": d["state"], "run": d["run"]})
    out = sorted([{"asin": a, "count": len(v), "orders": sorted(v, key=lambda x: -1)} for a, v in groups.items()],
                 key=lambda x: -x["count"])
    return {"asins": out, "total_asins": len(out), "total_orders": sum(x["count"] for x in out)}


def needs_priority_asins(q):
    """From the placer run logs (data/rajar_logs/run_*.csv), orders whose LATEST status is
    NEEDS_PRIORITY — UNLABELLED orders whose ASIN isn't in Priority ASIN Control, so the placer
    skips them. Someone must LABEL the order (or add the ASIN to priority control). Latest-status-
    per-order means they drop off automatically once handled and the placer re-runs."""
    import csv as _csv, glob, time as _time
    rajar = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "rajar_logs")
    _cutoff = _time.time() - LIVE_WINDOW_S
    _acct = q.get("account", [""])[0]   # "Hathoro" | "HOANK" | "" (all)
    latest = {}
    for f in sorted(glob.glob(os.path.join(rajar, "run_*.csv"))):
        try:
            if os.path.getmtime(f) < _cutoff:   # only currently-active runs → live/current view
                continue
        except OSError:
            continue
        if _acct and _run_account(f) != _acct:   # account filter (Hathoro=DM / HOANK=HS)
            continue
        try:
            for r in _csv.DictReader(open(f, encoding="utf-8", errors="replace")):
                aid = (r.get("Amazon_ID") or "").strip()
                if aid:
                    latest[aid] = {"status": (r.get("Status") or "").strip(),
                                   "asin": (r.get("ASIN") or "").strip(),
                                   "buyer": (r.get("Buyer") or "").strip(),
                                   "city": (r.get("City") or "").strip(),
                                   "state": (r.get("State") or "").strip(),
                                   "run": os.path.basename(f).replace("run_", "").replace(".csv", "")}
        except Exception:
            continue
    groups = {}
    for aid, d in latest.items():
        if d["status"] != "NEEDS_PRIORITY":
            continue
        groups.setdefault(d["asin"] or "(no ASIN)", []).append(
            {"order_id": aid, "buyer": d["buyer"], "city": d["city"], "state": d["state"], "run": d["run"]})
    out = sorted([{"asin": a, "count": len(v), "orders": v} for a, v in groups.items()], key=lambda x: -x["count"])
    return {"asins": out, "total_asins": len(out), "total_orders": sum(x["count"] for x in out)}


# ── TRACKER / DIARY (everything flagged, commented, or with a follow-up) ─────
def tracker(q):
    c = ro()
    try:
        crm = [dict(r) for r in c.execute("SELECT * FROM order_crm")]
    except Exception:
        crm = []
    try:
        ncnt = {r[0]: r[1] for r in c.execute("SELECT amazon_order_id, COUNT(*) FROM crm_notes GROUP BY amazon_order_id")}
        lastn = {r[0]: r[1] for r in c.execute("SELECT amazon_order_id, MAX(created_at) FROM crm_notes GROUP BY amazon_order_id")}
        lasttxt = {r[0]: r[1] for r in c.execute("SELECT amazon_order_id, note FROM crm_notes WHERE id IN (SELECT MAX(id) FROM crm_notes GROUP BY amazon_order_id)")}
    except Exception:
        ncnt, lastn, lasttxt = {}, {}, {}
    crm_by = {x["amazon_order_id"]: x for x in crm}
    out = []
    for k in set(crm_by) | set(ncnt):
        cr = crm_by.get(k, {})
        flag = cr.get("flag_color") or ""; cs = cr.get("case_status") or ""
        rem = cr.get("reminder_at") or ""; remn = cr.get("reminder_note") or ""
        tags = cr.get("tags") or ""
        nc = ncnt.get(k, 0)
        if not (flag or cs or rem or nc or tags):
            continue
        co = c.execute("SELECT account, order_label, asin FROM closing_orders WHERE amazon_order_id=?", (k,)).fetchone()
        out.append({"id": k, "flag": flag, "case_status": cs, "reminder_at": rem, "reminder_note": remn,
                    "tags": tags, "updated_at": cr.get("updated_at") or "",
                    "notes": nc, "last_note": lasttxt.get(k, ""), "last_note_at": lastn.get(k, ""),
                    "account": co[0] if co else "", "label": co[1] if co else "WB / uncaptured",
                    "asin": co[2] if co else ""})
    c.close()
    out.sort(key=lambda x: (0 if x["reminder_at"] else 1, x["reminder_at"] or "9999", 0 if x["case_status"] not in ("", "Resolved", "Won", "Lost", "Closed") else 1))
    return {"items": out, "count": len(out)}


# ── RAW DATA BROWSER (view + edit closing_orders like a DB browser) ──────────
def _co_cols():
    c = ro()
    try:
        return [r[1] for r in c.execute("PRAGMA table_info(closing_orders)")]
    finally:
        c.close()

def raw_orders(q):
    where, args, *_ = _filters(q)
    search = (q.get("q", [""])[0] or "").strip()
    if search:
        like = f"%{search}%"
        where += " AND (amazon_order_id LIKE ? OR merchant_order_id LIKE ? OR asin LIKE ? OR sku LIKE ? OR ship_name LIKE ?)"
        args += [like] * 5
    # per-column filters (DB-browser style): params named f_<column>
    colset = set(_co_cols())
    for k, vals in q.items():
        if k.startswith("f_"):
            col = k[2:]; v = (vals[0] or "").strip() if vals else ""
            if col in colset and v:
                where += f" AND CAST({col} AS TEXT) LIKE ?"; args.append(f"%{v}%")
    try:
        lim = max(1, min(2000, int(q.get("limit", ["300"])[0] or 300)))
    except Exception:
        lim = 300
    c = ro()
    cols = [r[1] for r in c.execute("PRAGMA table_info(closing_orders)")]
    rows = [dict(r) for r in c.execute(
        f"SELECT * FROM closing_orders WHERE {where} ORDER BY purchase_date DESC LIMIT ?", args + [lim])]
    total = c.execute(f"SELECT COUNT(*) FROM closing_orders WHERE {where}", args).fetchone()[0]
    c.close()
    return {"columns": cols, "rows": rows, "shown": len(rows), "total": total}

def update_cell(body):
    oid = body.get("order_id"); col = body.get("column")
    if not oid or not col:
        return {"ok": False, "error": "order_id + column required"}
    if col == "amazon_order_id":
        return {"ok": False, "error": "primary key is not editable"}
    if col not in set(_co_cols()):
        return {"ok": False, "error": f"unknown column {col}"}
    val = body.get("value")
    if val == "":
        val = None
    if OPS.CLOUD_MODE and col not in ALLOWED:
        return {"ok": False, "error": "raw column edits are only possible on the machine that owns the master DB"}
    def w():
        c = rw(); c.execute(f"UPDATE closing_orders SET {col}=? WHERE amazon_order_id=?", (val, oid))
        n = c.total_changes; c.commit(); c.close(); return n
    n = _retry_write(w)
    author = str(body.get("author") or "me")[:60]
    if OPS.CLOUD_MODE:
        return OPS.push_crm_event("update_order", oid, {col: val}, author)
    try:
        OPS.record_history(DB, oid, "cell", col, None, val, author)
    except Exception:
        pass
    return {"ok": True, "updated": n}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200, ctype="application/json", hdrs=None):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        # gzip large responses — the orders payload shrinks ~10x, which is the difference
        # between ~10s and ~1s page loads for remote (funnel/tunnel/tailscale) users.
        if len(body) > 512 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = _gzip.compress(body, 5)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (hdrs or []):
            self.send_header(k, v)
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            pass

    def _genuine_local(self):
        """True only for a real local request (owner's Mac). Public proxies (Cloudflare tunnel,
        Tailscale Funnel) ALSO connect from 127.0.0.1, so loopback alone is not proof of local —
        a genuine local request also (a) carries no proxy headers and (b) has a localhost Host
        header; proxied requests carry their public hostname (…trycloudflare.com / …ts.net)."""
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        return (self.client_address[0] in ("127.0.0.1", "::1")
                and host in ("localhost", "127.0.0.1", "::1", "[::1]")
                and not self.headers.get("Cf-Connecting-Ip")
                and not self.headers.get("X-Forwarded-For"))

    def _role(self):
        """'admin' | 'viewer' | 'restricted' | None. Genuinely-local = admin (owner's Mac); cookie decides
        for remote/proxied requests."""
        if self._genuine_local():
            return "admin"
        cookies = (self.headers.get("Cookie", "") or "").replace(" ", "")
        if ("dash_auth=" + _ADMIN_COOKIE) in cookies:
            return "admin"
        if ("dash_auth=" + _AUTH_COOKIE) in cookies:
            return "viewer"
        if ("dash_auth=" + _RESTRICTED_COOKIE) in cookies:
            return "restricted"
        return None

    def _authed(self):
        return self._role() is not None

    def _uid(self):
        """Per-BROWSER identity from the dash_uid cookie (set on page load). Beats IP-keying,
        which collapses everyone behind the funnel/tunnel into 127.0.0.1 = one shared name."""
        m = re.search(r"dash_uid=([a-f0-9]{16,})", (self.headers.get("Cookie", "") or ""))
        return ("uid:" + m.group(1)) if m else self.client_address[0]

    def _uid_setcookie(self):
        """Header to mint a dash_uid for browsers that don't have one yet (None if present)."""
        if re.search(r"dash_uid=[a-f0-9]{16,}", (self.headers.get("Cookie", "") or "")):
            return None
        return ("Set-Cookie", f"dash_uid={_secrets.token_hex(16)}; Path=/; Max-Age=31536000")

    def _login_page(self, err=""):
        body = (_LOGIN_HTML % (('<div class="err">%s</div>' % err) if err else "")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path
        if p.startswith("/pl_images/"):
            return serve_pl_images(self, p)      # public: Amazon image fetcher
        role = self._role()
        if role is None:
            return self._login_page()
        if role != "admin":
            if role == "restricted" and p in ("/api/pnl", "/api/pnl_hoank"):
                pass
            elif p in _ADMIN_ONLY_GET:
                return self._send({"error": "admin only"}, code=403)
        try:
            if p in ("/", "/index.html", "/legacy", "/v2", "/v2.html"):
                # v2 (order-centric + ops) is the default; the previous UI stays at /legacy.
                ck = self._uid_setcookie()
                v2_exists = os.path.exists(os.path.join(HERE, "v2.html"))
                # dashboard/.v2_disabled keeps "/" on the legacy page while v2 is being verified;
                # /v2 always shows the new page, /legacy always the old one.
                v2_default = v2_exists and not os.path.exists(os.path.join(HERE, ".v2_disabled"))
                if p in ("/v2", "/v2.html"):
                    page = "v2.html" if v2_exists else "index.html"
                elif p == "/legacy":
                    page = "index.html"
                else:
                    page = "v2.html" if v2_default else "index.html"
                with open(os.path.join(HERE, page), "rb") as f:
                    return self._send(f.read(), ctype="text/html; charset=utf-8",
                                      hdrs=[ck] if ck else None)
            # ── v2 ops layer (dashboard/ops.py) ──
            if p == "/api/ops/machines":
                return self._send({"machines": OPS.machines(), "host": OPS.machine_id(), "cloud": OPS.cloud_status()})
            if p == "/api/ops/live_log":
                return self._send(OPS.live_log(q.get("host", [None])[0], int(q.get("n", ["400"])[0] or 400)))
            if p == "/api/ops/results":
                return self._send({"results": OPS.command_results(int(q.get("limit", ["40"])[0] or 40))})
            if p == "/api/ops/actions":
                return self._send({"actions": OPS.ACTIONS})
            if p == "/api/config":
                return self._send(OPS.config_get())
            if p.startswith("/api/timeline/"):
                return self._send(OPS.order_timeline(DB, urllib.parse.unquote(p.split("/api/timeline/", 1)[1])))
            if p == "/api/crm_history":
                return self._send({"history": OPS.crm_history(DB, q.get("order", [None])[0],
                                                              int(q.get("limit", ["200"])[0] or 200))})
            if p == "/api/reconcile":
                return self._send(OPS.reconcile(DB, int(q.get("days", ["30"])[0] or 30)))
            if p == "/api/cloud_status":
                return self._send(OPS.cloud_status())
            if p == "/api/decisions":
                return self._send(OPS.decisions(DB, q))
            if p == "/api/placer_events":
                return self._send(OPS.placer_events(float(q.get("hours", ["24"])[0] or 24),
                                                    (q.get("status", [""])[0] or "").split(","),
                                                    int(q.get("limit", ["500"])[0] or 500)))
            if p == "/api/overview":
                return self._send(overview(q))
            if p == "/api/asins":
                return self._send(asin_list(q))
            if p.startswith("/api/asin/"):
                return self._send(asin_detail(urllib.parse.unquote(p.split("/api/asin/", 1)[1]), q))
            if p == "/api/orders":
                return self._send(orders(q))
            if p == "/api/daybook":
                import daybook as _dbk
                _dbk.DB = DB
                return self._send(_dbk.daybook(q))
            if p == "/api/daybook.csv":
                import daybook as _dbk
                _dbk.DB = DB
                _csv, _name = _dbk.daybook_csv(q)
                return self._send(_csv.encode("utf-8-sig"), ctype="text/csv; charset=utf-8",
                                  hdrs=[("Content-Disposition", f'attachment; filename="{_name}"')])
            if p == "/api/growth":
                import daybook as _dbk
                _dbk.DB = DB
                return self._send(_dbk.growth(q))
            if p == "/api/listings":
                return self._send(listings(q))
            if p == "/api/walmart":
                return self._send(walmart(q))
            if p == "/api/facets":
                return self._send(facets())
            if p == "/api/chat":               # feature removed — the legacy page still polls it
                return self._send({"messages": [], "last_id": 0, "removed": True})
            if p == "/api/asin_pics":
                return self._send(asin_pics(q))
            if p == "/api/asin_pics_zip":
                return asin_pics_zip(self, q)
            if p.startswith("/msg_media/"):
                return serve_msg_media(self, p)
            if p == "/api/reminders":
                return self._send(reminders_list())
            if p == "/api/whoami":
                # IP fallback ONLY for the genuinely-local owner (name continuity from the old
                # IP-keyed scheme). Proxied users must NOT inherit the 127.0.0.1 (owner's) name.
                fb = self.client_address[0] if self._genuine_local() else None
                out = get_whoami(self._uid(), fallback_key=fb)
                out["role"] = role
                return self._send(out)
            if p == "/api/travel_export":
                # Hand the team's work back to the Mac after a trip. Returns ONLY the
                # human-edited tables (comments/flags/chat/reminders) — never orders or
                # placement locks. Admin-only: see _ADMIN_ONLY_GET.
                import sys as _sys
                _sc = os.path.join(os.path.dirname(HERE), "scripts")
                if _sc not in _sys.path:
                    _sys.path.insert(0, _sc)
                from travel_mode import _export_payload
                return self._send(_export_payload(DB))
            if p == "/api/watch":
                return self._send(watch_list())
            if p == "/api/ship_perf":
                return self._send(ship_perf(q))
            if p == "/api/handling":
                return self._send(handling_perf(q))
            if p == "/api/ship_orders":
                return self._send(ship_orders(q))
            if p.startswith("/api/order/"):
                return self._send(order_detail(urllib.parse.unquote(p.split("/api/order/", 1)[1])))
            if p == "/api/insights":
                where, args, acct, frm, to = _filters(q)
                return self._send(INS.compute(account=acct or None, date_from=frm or None,
                                              date_to=to or None, db_path=DB))
            # ── owner's cockpit modules ──
            acct = q.get("account", [""])[0] or None
            zacct = q.get("zoro_account", [""])[0] or None
            frm = q.get("from", [""])[0] or None
            to = q.get("to", [""])[0] or None
            if p == "/api/today":     return self._send(AN.today(acct, zacct, frm, to))
            if p == "/api/sourcing":  return self._send(AN.sourcing(acct, zacct, frm, to))
            if p == "/api/portfolio": return self._send(AN.portfolio(acct, zacct, frm, to))
            if p == "/api/leakage":   return self._send(AN.leakage(acct, zacct, frm, to))
            if p == "/api/health":    return self._send(AN.health(acct, zacct, frm, to))
            if p == "/api/data_health": return self._send(AN.data_health(acct, zacct, frm, to))
            if p == "/api/refund_issues": return self._send(AN.refund_issues(acct, zacct, frm, to))
            if p == "/api/pnl":       return self._send(get_pnl(q))
            if p == "/api/pnl_hoank": return self._send(get_pnl_hoank(q))
            if p == "/api/severe":    return self._send(AN.severe_issues(acct, zacct, frm, to))
            if p == "/api/raw":       return self._send(raw_orders(q))
            if p == "/api/order_zoro": return self._send(order_zoro(q))
            if p == "/api/zoro_tsv":  return self._send(zoro_tsv(q))
            if p == "/api/tracker":   return self._send(tracker(q))
            if p == "/api/unmapped":  return self._send(unmapped_asins(q))
            if p == "/api/needs_priority": return self._send(needs_priority_asins(q))
            if p == "/api/ops":       return self._send(AN.ops(acct, zacct, frm, to))
            if p == "/api/margin":    return self._send(AN.margin_radar(acct, zacct, frm, to))
            # ── Repricer Engine Endpoints ──
            if p == "/api/repricer":
                req_acct = q.get("account", ["Hathoro"])[0] or "Hathoro"
                if REPRICER is None:
                    return self._send({"error": "The repricer runs on the machine that holds the offers reports "
                                                "(it is not available on the cloud dashboard)."}, 503)
                res = REPRICER.analyze_repricing_for_account(account=req_acct, db_path=DB)
                res["is_scraping"] = _REPRICER_SCRAPING.get("Hathoro" if "HATHORO" in req_acct.upper() else "HOANK", False)
                res["last_scrape_status"] = _REPRICER_SCRAPING.get("last_msg", "")
                return self._send(res)
            if p == "/api/repricer_scrape":
                req_acct = q.get("account", ["Hathoro"])[0] or "Hathoro"
                return self._send(_start_repricer_scrape(req_acct))
            if p == "/api/repricer_excel":
                req_acct = q.get("account", ["Hathoro"])[0] or "Hathoro"
                is_hathoro = "HATHORO" in req_acct.upper()
                fname = "Hathoro_August_Amazon_Offers_Report.xlsx" if is_hathoro else "HOANK_August_Amazon_Offers_Report.xlsx"
                fpath = os.path.join(os.path.dirname(HERE), fname)

                # Auto-generate fresh from cache if file does not exist
                if not os.path.exists(fpath):
                    try:
                        if is_hathoro:
                            from scripts.generate_hathoro_offers_report import get_hathoro_asins_from_db, load_cache, create_excel_report
                            df = get_hathoro_asins_from_db()
                            cache = load_cache()
                            create_excel_report(df, cache, output_file=fpath)
                        else:
                            from scripts.generate_hoank_offers_report import get_hoank_asins_from_db, load_cache, create_excel_report
                            df = get_hoank_asins_from_db()
                            cache = load_cache()
                            create_excel_report(df, cache, output_file=fpath)
                    except Exception as gen_err:
                        print(f"Error auto-generating Excel: {gen_err}")

                if os.path.exists(fpath):
                    with open(fpath, "rb") as f:
                        data = f.read()
                    return self._send(data, ctype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                      hdrs=[("Content-Disposition", f'attachment; filename="{fname}"')])
                else:
                    return self._send({"error": f"Report {fname} not found. Please click Refresh to generate it."}, 404)

            return self._send({"error": "not found"}, 404)

        except Exception as e:
            import traceback; traceback.print_exc()
            return self._send({"error": str(e)}, 500)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/login":
            ln = int(self.headers.get("Content-Length", 0))
            form = urllib.parse.parse_qs(self.rfile.read(ln).decode("utf-8", "ignore"))
            pw = (form.get("pw", [""])[0] or "").strip()
            token = _ADMIN_COOKIE if pw == ADMIN_PW else (_AUTH_COOKIE if pw == DASH_PW else (_RESTRICTED_COOKIE if pw == RESTRICTED_PW else None))
            if token:
                self.send_response(302)
                self.send_header("Set-Cookie",
                                 f"dash_auth={token}; Path=/; Max-Age=31536000; HttpOnly")
                ck = self._uid_setcookie()
                if ck:
                    self.send_header(ck[0], ck[1])
                self.send_header("Location", "/")
                self.end_headers()
                return
            return self._login_page("Wrong password — try again")
        role = self._role()
        if role is None:
            return self._send({"error": "auth required"}, code=401)
        if role != "admin" and u.path in _ADMIN_ONLY_POST:
            return self._send({"error": "admin only"}, code=403)
        if role == "restricted" and u.path in _RESTRICTED_DENY_POST:
            return self._send({"error": "not allowed for this role"}, code=403)
        try:
            ln = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(ln) or b"{}")
            # who is acting: the browser's saved display name, else the role
            try:
                _who = (body.get("author") or get_whoami(self._uid()).get("name") or role or "dashboard")
            except Exception:
                _who = body.get("author") or role or "dashboard"
            body.setdefault("author", str(_who)[:40])
            if u.path == "/api/note":
                return self._send(add_note(body))
            if u.path == "/api/chat":
                return self._send({"ok": False, "error": "team chat was removed"}, 410)
            if u.path == "/api/ops/command":
                return self._send(OPS.send_command(str(body.get("action") or ""), body.get("target"),
                                                   body.get("args") or {}, str(_who)[:40]))
            if u.path == "/api/config":
                return self._send(OPS.config_set(body, str(_who)[:40]))
            if u.path == "/api/reminder":
                return self._send(reminder_add(body))
            if u.path == "/api/reminder_update":
                return self._send(reminder_update(body))
            if u.path == "/api/whoami":
                return self._send(set_whoami(body, self._uid()))
            if u.path == "/api/order_update":
                return self._send(update_order(body))
            if u.path == "/api/pnl":
                return self._send(save_pnl(body))
            if u.path == "/api/pnl_hoank":
                return self._send(save_pnl_hoank(body))
            if u.path == "/api/bank_reconcile":
                return self._send(bank_reconcile(body))
            if u.path == "/api/cell":
                return self._send(update_cell(body))
            if u.path == "/api/resolve":
                return self._send(resolve_insight(body))
            if u.path == "/api/watch":
                return self._send(watch_add(body))
            if u.path == "/api/watch_del":
                return self._send(watch_del(body))
            if u.path == "/api/listing_label":
                return self._send(listing_label_set(body))
            if u.path == "/api/watch_clear":
                return self._send(watch_clear(body))
            return self._send({"error": "not found"}, 404)
        except Exception as e:
            return self._send({"error": str(e)}, 500)


if __name__ == "__main__":
    print(f"ZUHRA dashboard → http://localhost:{PORT}   (DB: {DB})"
          + ("   [CLOUD MODE — slim DB from R2]" if OPS.CLOUD_MODE else "")
          + ("   R2: connected" if OPS.r2() else "   R2: not configured (single-machine view)"))
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
