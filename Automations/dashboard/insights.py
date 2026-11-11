#!/usr/bin/env python3
"""Business insights engine for the CRM dashboard.

Generates real-time, high-accuracy insights across these categories:
  1. Amazon Earnings/Unit (Split: Increase ↑ vs Decrease ↓) partitioned by (account, asin)
  2. Buyer paid for shipping on a free-shipping listing / template went back to free
  3. Zoro Price (Split: Increase ↑ vs Decrease ↓): the Unit Price on Zoro's order e-mails
  4. Zoro Shipping Fee Deviation (Actual ≠ expected $0/free or $5.99)
  5. Handling time, in business days: Zoro late against Amazon's promise, and a weekly
     review of the few listings that really have handling days to cut
  6. Out of Stock (OOS), and Alternative SKU used (one per ASIN per day)
  7. Low Margin Split (Placed < 8%, one per ASIN per day, vs Unplaced 'Loss-Kamran checking')
Plus critical operational protections (Zip mismatch, Undelivered past due,
Refunded, Wrong quantity multiplier, Shipped late, WB unmatched).

Every "changed" alert compares against the previous value in date order and over the listing's
history (or the Zoro code's), never only inside the window being looked at, so one real change
makes one alert however often this runs. All dates go through _d(): the tables hold several
formats, and comparing them as text put 'Sat, Nov 7, 2026' after '2026-11-09'.

Data sources: order_placer_orders, bot_decisions, archive_zoro, closing_orders.
Safe, query_only read against automations_unified.db (WAL compatible).
"""

import os
import re
import csv
import sqlite3
import json
import statistics
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # Development_Unit
AUTO = os.path.join(ROOT, "Automations")
DB = os.environ.get("CLOUD_DB_PATH") if (os.environ.get("CLOUD_MODE") == "1" and os.environ.get("CLOUD_DB_PATH")) else os.path.join(AUTO, "data", "automations_unified.db")
VERIFIED_CSV = os.path.join(ROOT, "DS_Closing", "Hypersale_Closing", "verified_skus_cache.csv")

DELIVERED = ("Delivered", "AllDelivered")

# How far back a listing's earlier orders are read when the window itself is short.
HISTORY_DAYS = 60
# Windows at least this long already hold their own history.
SHORT_WINDOW_DAYS = 30


def _digits(z: Any) -> str:
    return re.sub(r"\D", "", str(z or ""))


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_ISO_DATE_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})")
_NAMED_DATE_RE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_SLASH_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})")


def _d(s: Any) -> Optional[date]:
    """Read any date the order tables carry: '2026-11-07 17:52', 'Sat, Nov 7, 2026',
    'Wed, May 20, 2026, 1:20 PM PDT', 'Apr 27, 2026 9:05:47 PM PDT', '11/7/2026'. The first date
    in the text wins — the placer's deliver-by can read 'Thu, Nov 12, 2026| ZoroDeliverBy:…'."""
    if not s:
        return None
    s = str(s)
    try:
        m = _ISO_DATE_RE.match(s)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _NAMED_DATE_RE.search(s)
        if m and m.group(1)[:3].lower() in _MONTHS:
            return date(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
        m = _SLASH_DATE_RE.match(s)
        if m:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        pass
    return None


def _day(v: Any) -> str:
    """The date as 'YYYY-MM-DD', '' when it cannot be read."""
    d = _d(v)
    return d.isoformat() if d else ""


def _order_day(o: Dict[str, Any]) -> str:
    return _day(o.get("purchase_date")) or _day(o.get("placed_at"))


def _biz_days(a: date, b: date) -> int:
    """Mon–Fri days from a to b: Friday to the next Monday is 1, Saturday to Monday is 1.
    Negative when b is before a. Amazon's handling days and most carrier promises skip weekends."""
    if a == b:
        return 0
    sign = 1 if b > a else -1
    cur, end = min(a, b), max(a, b)
    n = 0
    while cur < end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n * sign


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _short_day(v: Any) -> str:
    """'Sat, Nov 7' for a message; '' when the date cannot be read."""
    d = _d(v)
    return f"{d:%a, %b} {d.day}" if d else ""


def _date_sql(col: str, date_from: Optional[str], date_to: Optional[str]) -> Tuple[str, List[str]]:
    """SQL for a date window that also fetches rows whose date is not ISO text; those are few,
    and the caller judges them by their real date after reading them."""
    conds, args = [], []
    if date_from:
        conds.append(f"substr({col},1,10) >= ?")
        args.append(date_from)
    if date_to:
        conds.append(f"substr({col},1,10) <= ?")
        args.append(date_to)
    if not conds:
        return "1=1", []
    iso = f"{col} GLOB '[0-9][0-9][0-9][0-9]-*'"
    return f"(({iso} AND {' AND '.join(conds)}) OR {col} NOT GLOB '[0-9][0-9][0-9][0-9]-*')", args


_PROFILE_STORES = {"HS": "HOANK", "DM": "Hathoro", "WR": "Wrenchly"}


def _store_of_profile(profile: Any) -> str:
    """Which store a placer profile belongs to."""
    p = str(profile or "").strip()
    try:
        from utils.accounts_config import store_for_profile
        return store_for_profile(p, "") or ""
    except Exception:
        return _PROFILE_STORES.get(p.upper().split("_", 1)[0], "")


_ZORO_CODE_RE = re.compile(r"\b([GK]\d{5,})\b")


def _zoro_code(v: Any) -> str:
    m = _ZORO_CODE_RE.search(str(v or "").upper().replace("HATH-", ""))
    return m.group(1) if m else ""


_HISTORY_COLS = "account, asin, amazon_order_id, purchase_date, earnings, shipping_amt, shipping_price, quantity, order_status"


def _listing_history(con: sqlite3.Connection, asins: List[str], account: Optional[str],
                     since: str) -> List[Dict[str, Any]]:
    """The earlier orders of these listings, so a change is judged against what came before the
    window. Narrow on purpose: this is the only data the 'changed' rules need."""
    rows: List[Dict[str, Any]] = []
    for i in range(0, len(asins), 500):
        chunk = asins[i:i + 500]
        q = f"SELECT {_HISTORY_COLS} FROM closing_orders WHERE asin IN ({','.join('?' * len(chunk))})"
        args: List[Any] = list(chunk)
        if account and account not in ("All", "all", ""):
            q += " AND account = ?"
            args.append(account)
        for r in con.execute(q, args):
            if _day(r["purchase_date"]) >= since:
                rows.append(dict(r))
    return rows


def _closing_rows_of(con: sqlite3.Connection, order_ids: List[str]) -> List[Dict[str, Any]]:
    """closing's row for these orders, whatever its date or ASIN. A placer row names one ASIN and
    the whole order's earnings; closing knows it was 'B07…, B0H…' and that it had 2 units."""
    rows: List[Dict[str, Any]] = []
    for i in range(0, len(order_ids), 500):
        chunk = order_ids[i:i + 500]
        q = f"SELECT {_HISTORY_COLS} FROM closing_orders WHERE amazon_order_id IN ({','.join('?' * len(chunk))})"
        rows += [dict(r) for r in con.execute(q, chunk)]
    return rows


def _handling_review(con: sqlite3.Connection, account: Optional[str]) -> List[Dict[str, Any]]:
    """Listings that really have handling days to cut: over the last 30 days they usually take
    2+ business days to the ship-by date, and Zoro is not already tight against Amazon's date.
    Almost every listing is at 1 business day, which is as low as it goes."""
    since = (date.today() - timedelta(days=30)).isoformat()
    q = ("SELECT account, asin, purchase_date, ship_by_date, deliver_by_date, zoro_deliver_by "
         "FROM order_placer_orders WHERE wb_order_id != '' AND substr(placed_at,1,10) >= ?")
    args: List[Any] = [since]
    if account and account not in ("All", "all", ""):
        q += " AND account = ?"
        args.append(account)
    by: Dict[Tuple[str, str], List[Tuple[int, Optional[int]]]] = {}
    for r in con.execute(q, args):
        p, s = _d(r["purchase_date"]), _d(r["ship_by_date"])
        if not (p and s and r["asin"]) or "," in r["asin"] or not 0 <= (s - p).days <= 14:
            continue
        a, z = _d(r["deliver_by_date"]), _d(r["zoro_deliver_by"])
        by.setdefault((r["account"] or "", r["asin"]), []).append((_biz_days(p, s), _biz_days(z, a) if (a and z) else None))

    monday = _monday(date.today()).isoformat()
    out = []
    for (acct, asin), rows in by.items():
        if len(rows) < 5:
            continue
        usual = statistics.median(h for h, _ in rows)
        if usual < 2:
            continue
        slacks = [s for _, s in rows if s is not None]
        slack = statistics.median(slacks) if slacks else None
        if slack is not None and slack < 1:
            continue
        where = f"; Zoro arrives about {slack:g} business day{'s' if slack != 1 else ''} before Amazon's date" if slack is not None else ""
        target = max(usual - (slack if slack is not None else 1), 1)
        out.append({
            "asin": asin, "account": acct, "usual_handling": usual, "orders": len(rows), "date": monday,
            "ref_key": f"{acct}:{asin}:reduce_handling:{monday}",
            "message": f"ASIN {asin} ({acct}): usually {usual:g} business days to ship-by over {len(rows)} orders{where} — handling can come down to {target:g}",
        })
    out.sort(key=lambda x: -x["orders"])
    return out


def _zoro_price_changes(con: sqlite3.Connection, date_from: Optional[str], date_to: Optional[str]) -> List[Dict[str, Any]]:
    """Every real Zoro price change in the window: the Unit Price printed on Zoro's order e-mail
    differs from the one on that code's previous e-mail. A code's first e-mail is its baseline.

    The Unit Price is Zoro's own list price, the same before any coupon or promo, so discounts
    and shipping cannot look like a price change; it matched the cart price on all 18,227 e-mails
    checked on 9 Nov 2026. The code's e-mails are the memory, so re-running finds the same
    change under the same key and it goes out once."""
    if not _has_table(con, "archive_zoro"):
        return []
    last: Dict[str, Tuple[float, str, str]] = {}
    if date_from:
        q = ("SELECT sku, unit_price, MAX(email_timestamp) ts, wb_order_id FROM archive_zoro "
             "WHERE unit_price > 0 AND email_timestamp < ? GROUP BY UPPER(sku)")
        for r in con.execute(q, (date_from,)):
            code = _zoro_code(r["sku"])
            if code:
                last[code] = (round(float(r["unit_price"]), 2), r["ts"] or "", r["wb_order_id"] or "")

    w, a = ["unit_price > 0"], []
    if date_from:
        w.append("email_timestamp >= ?")
        a.append(date_from)
    if date_to:
        w.append("substr(email_timestamp,1,10) <= ?")
        a.append(date_to)
    out: List[Dict[str, Any]] = []
    for r in con.execute(f"SELECT sku, unit_price, email_timestamp ts, wb_order_id FROM archive_zoro WHERE {' AND '.join(w)} ORDER BY email_timestamp, wb_order_id", a):
        code = _zoro_code(r["sku"])
        if not code:
            continue
        price, ts, wb = round(float(r["unit_price"]), 2), r["ts"] or "", r["wb_order_id"] or ""
        prev = last.get(code)
        if prev and abs(price - prev[0]) >= 0.01:
            out.append({"code": code, "old": prev[0], "new": price, "old_ts": prev[1], "old_wb": prev[2], "ts": ts, "wb": wb})
        last[code] = (price, ts, wb)
    if not out:
        return out

    # Which order and listings each change belongs to: the alert goes to the store whose order
    # showed it first, and names every listing that uses the code.
    wbs = sorted({c["wb"] for c in out})
    by_wb: Dict[str, Dict[str, Any]] = {}
    for table, cols in (("closing_orders", "account, asin, amazon_order_id, title"),
                        ("order_placer_orders", "account, asin, amazon_order_id, title")):
        for i in range(0, len(wbs), 500):
            chunk = wbs[i:i + 500]
            for r in con.execute(f"SELECT wb_order_id, {cols} FROM {table} WHERE wb_order_id IN ({','.join('?' * len(chunk))})", chunk):
                by_wb[r["wb_order_id"]] = dict(r)
    codes = sorted({c["code"] for c in out})
    wanted = set(codes)
    listings: Dict[str, Dict[Tuple[str, str], str]] = {}
    since = (date.today() - timedelta(days=45)).isoformat()
    for r in con.execute("SELECT sku, account, asin, MAX(placed_at) p FROM order_placer_orders WHERE substr(placed_at,1,10) >= ? GROUP BY sku, account, asin", (since,)):
        code = _zoro_code(r["sku"])
        if code in wanted and r["asin"]:
            listings.setdefault(code, {})[(r["account"] or "", r["asin"])] = r["p"] or ""
    for c in out:
        o = by_wb.get(c["wb"]) or {}
        ls = sorted(listings.get(c["code"], {}).items(), key=lambda kv: kv[1], reverse=True)
        c["listings"] = [k for k, _ in ls]
        c["account"] = o.get("account") or (ls[0][0][0] if ls else "")
        c["asin"] = o.get("asin") or (ls[0][0][1] if ls else "")
        c["order_id"] = o.get("amazon_order_id") or ""
        c["title"] = o.get("title") or ""
    return out


def load_verified() -> Dict[str, Dict[str, Any]]:
    """ASIN -> {zoro_code, qty_to_place, zoro_multiple, second}."""
    out = {}
    if not os.path.exists(VERIFIED_CSV):
        return out
    try:
        with open(VERIFIED_CSV, newline="", encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                asin = (row.get("ASIN") or "").strip()
                if not asin:
                    continue
                out[asin] = {
                    "zoro_code": (row.get("Zoro Code") or "").strip(),
                    "qty_to_place": _f(row.get("Qty to Place")) or 1,
                    "zoro_multiple": _f(row.get("zoro_multiple")) or 1,
                    "second": (row.get("2nd_part") or "").strip(),
                }
    except Exception:
        pass
    return out


def _resolved_keys(con: sqlite3.Connection) -> Dict[str, Set[str]]:
    s: Dict[str, Set[str]] = {}
    try:
        for t, k in con.execute("SELECT insight_type, ref_key FROM insight_resolutions"):
            s.setdefault(t, set()).add(k)
    except sqlite3.OperationalError:
        pass
    return s



# ── what compute() loads, and for how long the answer is kept ────────────────────────────
import ast as _ast
import os as _os
import threading as _threading
import time as _time

_SRC_LITERALS = None
_NEED_CACHE = {}
_JOIN_KEYS = ("amazon_order_id", "wb_order_id", "account")


def _source_literals():
    """Every string literal in this module — a superset of the column names it can read."""
    global _SRC_LITERALS
    if _SRC_LITERALS is None:
        try:
            tree = _ast.parse(open(__file__, encoding="utf-8").read())
            _SRC_LITERALS = {n.value for n in _ast.walk(tree)
                             if isinstance(n, _ast.Constant) and isinstance(n.value, str)}
        except Exception:
            _SRC_LITERALS = set()
    return _SRC_LITERALS


def needed_cols(con, table):
    """SELECT list of the columns of `table` this module actually reads.

    closing_orders has 122 columns and the default 60-day window is ~32k rows: `SELECT *` built
    ~350 MB of Python dicts per call on the cloud copy — the one request that reliably pushed
    Render's 512 MB instance over its limit. A column is kept when its name appears as a string
    literal anywhere in this file (over-inclusive by design, never under), plus the join keys."""
    live = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    key = (table, tuple(live))
    sel = _NEED_CACHE.get(key)
    if sel is None:
        lits = _source_literals()
        keep = [c for c in live if c in lits or c in _JOIN_KEYS]
        sel = ", ".join(keep) if keep else "*"
        _NEED_CACHE[key] = sel
    return sel


def _has_table(con, name):
    try:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
                           (name,)).fetchone() is not None
    except sqlite3.Error:
        return False


_RESULT_CACHE = {}
_RESULT_LOCK = _threading.Lock()
_RESULT_TTL = 300


def _db_stamp(path):
    try:
        st = _os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


def compute(account: Optional[str] = None, date_from: Optional[str] = None,
            date_to: Optional[str] = None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """_compute(), remembered for five minutes per database copy (the policy analytics uses).
    The cloud copy changes every few minutes; between copies, every visitor gets the same answer."""
    path = db_path or DB
    key = (path, account, date_from, date_to)
    stamp, now = _db_stamp(path), _time.time()
    with _RESULT_LOCK:
        hit = _RESULT_CACHE.get(key)
        if hit and hit[0] == stamp and now - hit[1] < _RESULT_TTL:
            return hit[2]
    val = _compute(account, date_from, date_to, db_path)
    with _RESULT_LOCK:
        if len(_RESULT_CACHE) >= 16:
            _RESULT_CACHE.clear()
        _RESULT_CACHE[key] = (stamp, now, val)
    return val


def _compute(account: Optional[str] = None, date_from: Optional[str] = None,
            date_to: Optional[str] = None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Compute all operational insights across the requested account and date window."""
    con = sqlite3.connect(db_path or DB, timeout=10)
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row

    # Default window: last 60 days
    if not date_from and not date_to:
        date_from = (date.today() - timedelta(days=60)).isoformat()

    def in_window(day: str) -> bool:
        return bool(day) and (not date_from or day >= date_from) and (not date_to or day <= date_to)

    # 1. Fetch closing_orders in window
    cw, ca = ["1=1"], []
    if account and account not in ("All", "all", ""):
        cw.append("account = ?")
        ca.append(account)
    _sql, _args = _date_sql("purchase_date", date_from, date_to)
    cw.append(_sql)
    ca.extend(_args)

    closing_rows = [r for r in (dict(x) for x in con.execute(
        f"SELECT {needed_cols(con, 'closing_orders')} FROM closing_orders WHERE {' AND '.join(cw)}", ca))
        if in_window(_day(r.get("purchase_date")))]

    # 2. Fetch order_placer_orders in window (real-time stream)
    pw, pa = ["1=1"], []
    if account and account not in ("All", "all", ""):
        pw.append("account = ?")
        pa.append(account)
    _sql1, _args1 = _date_sql("purchase_date", date_from, date_to)
    _sql2, _args2 = _date_sql("placed_at", date_from, date_to)
    pw.append(f"({_sql1} OR {_sql2})")
    pa.extend(_args1 + _args2)

    placer_rows = [r for r in (dict(x) for x in con.execute(
        f"SELECT {needed_cols(con, 'order_placer_orders')} FROM order_placer_orders WHERE {' AND '.join(pw)}", pa))
        if in_window(_day(r.get("purchase_date"))) or in_window(_day(r.get("placed_at")))]

    # Merge orders by amazon_order_id
    orders_map: Dict[str, Dict[str, Any]] = {}
    for r in closing_rows:
        oid = r.get("amazon_order_id")
        if oid:
            orders_map[oid] = r

    fresh_wbs = []
    for r in placer_rows:
        oid = r.get("amazon_order_id")
        if not oid:
            continue
        if oid not in orders_map:
            orders_map[oid] = r
            wb = (r.get("wb_order_id") or "").strip()
            if wb:
                fresh_wbs.append(wb)
        else:
            existing = orders_map[oid]
            # Overlay fresh placer real-time placement values
            for fld in ("zoro_deliver_by", "zoro_stock", "alt_used", "main_sku", "placed_at", "zoro_qty", "zoro_list_price"):
                val = r.get(fld)
                if val is not None and val != "":
                    existing[fld] = val

    # Fast chunked lookup in archive_zoro using indexed wb_order_id.
    # The Gmail archive is not shipped to the cloud copy; without it the enrichment is skipped
    # and every other insight still comes back (it used to fail here — after loading everything).
    all_wbs = sorted({(o.get("wb_order_id") or "").strip() for o in orders_map.values()} - {""})
    if all_wbs and _has_table(con, "archive_zoro"):
        # Index the orders by WB once. The previous version scanned every order for every
        # archive row — 1,100 fresh WBs x 32k orders = 35 million comparisons, 11 of the 13
        # seconds /api/insights took on the master DB (21 Sep 2026).
        by_wb: Dict[str, List[Dict[str, Any]]] = {}
        for o in orders_map.values():
            wb = o.get("wb_order_id")
            if wb:
                by_wb.setdefault(wb, []).append(o)
        fresh = set(fresh_wbs)
        for i in range(0, len(all_wbs), 500):
            chunk = all_wbs[i:i + 500]
            q = "SELECT wb_order_id, subtotal, shipping_cost, sku, ship_zip, coupon_savings, unit_price FROM archive_zoro WHERE wb_order_id IN (%s)" % (",".join("?" * len(chunk)))
            for ar in con.execute(q, chunk):
                for o in by_wb.get(ar["wb_order_id"], ()):
                    if ar["unit_price"]:
                        o["zoro_unit_price"] = ar["unit_price"]
                    if ar["wb_order_id"] not in fresh:
                        continue
                    if not o.get("zoro_subtotal") and ar["subtotal"] is not None:
                        o["zoro_subtotal"] = ar["subtotal"]
                    if not o.get("zoro_shipping") and ar["shipping_cost"] is not None:
                        o["zoro_shipping"] = ar["shipping_cost"]
                    if not o.get("zoro_sku") and ar["sku"]:
                        o["zoro_sku"] = ar["sku"]
                    if not o.get("zoro_buyer_zip") and ar["ship_zip"]:
                        o["zoro_buyer_zip"] = ar["ship_zip"]
                    if not o.get("coupon_savings") and ar["coupon_savings"] is not None:
                        o["coupon_savings"] = ar["coupon_savings"]

    # 3. Fetch bot_decisions in window
    bw, ba = ["1=1"], []
    if date_from:
        bw.append("substr(ts,1,10) >= ?")
        ba.append(date_from)
    if date_to:
        bw.append("substr(ts,1,10) <= ?")
        ba.append(date_to)
    bot_rows = [dict(r) for r in con.execute(f"SELECT * FROM bot_decisions WHERE {' AND '.join(bw)}", ba)]
    for b in bot_rows:
        b["store"] = _store_of_profile(b.get("profile"))
    if account and account not in ("All", "all", ""):
        # bot_decisions has no store column: unfiltered, every store's decision went out
        # under whichever store ran first.
        bot_rows = [b for b in bot_rows if b["store"] == account]

    # What the "changed" rules compare against, read while the connection is open.
    zoro_changes = _zoro_price_changes(con, date_from, date_to)
    handling_review = _handling_review(con, account)
    listing_history: List[Dict[str, Any]] = []
    _from = _d(date_from)
    if _from and not date_to and (date.today() - _from).days < SHORT_WINDOW_DAYS:
        listing_history = _listing_history(
            con, sorted({(o.get("asin") or "").strip() for o in orders_map.values()} - {""}), account,
            (_from - timedelta(days=HISTORY_DAYS)).isoformat())
        _known = {h.get("amazon_order_id") for h in listing_history}
        listing_history += _closing_rows_of(con, [oid for oid in orders_map if oid not in _known])

    # Set of fleet placed WBs for unmatched check
    placed_wbs = set()
    try:
        placed_wbs = {w[0] for w in con.execute("SELECT wb_order_id FROM order_placer_orders WHERE wb_order_id!=''")}
    except sqlite3.OperationalError:
        pass

    # Pre-index global ASIN -> {title, sku} metadata
    asin_to_meta: Dict[str, Dict[str, str]] = {}
    try:
        for r in con.execute(
            "SELECT UPPER(asin), title, sku FROM closing_orders WHERE asin IS NOT NULL AND title IS NOT NULL GROUP BY UPPER(asin)"
        ):
            if r[0]:
                asin_to_meta[r[0]] = {"title": str(r[1] or "").strip(), "sku": str(r[2] or "").strip()}
        for r in con.execute(
            "SELECT UPPER(asin), title, sku FROM order_placer_orders WHERE asin IS NOT NULL AND title IS NOT NULL GROUP BY UPPER(asin)"
        ):
            if r[0]:
                entry = asin_to_meta.setdefault(r[0], {})
                if r[1]:
                    entry["title"] = str(r[1]).strip()
                if r[2]:
                    entry["sku"] = str(r[2]).strip()
    except Exception:
        pass

    resolved = _resolved_keys(con)
    verified = load_verified()
    # Listing events (CATEGORY 8) are read here, while the connection is open
    listing_event_rows: List[Dict[str, Any]] = []
    if _has_table(con, "listing_events"):
        try:
            listing_event_rows = [dict(r) for r in con.execute("SELECT * FROM listing_events ORDER BY at DESC LIMIT 2000")]
        except sqlite3.Error:
            listing_event_rows = []
    con.close()

    orders = list(orders_map.values())
    today = date.today().isoformat()

    # Overlay window-specific order titles/skus
    for o in orders:
        a = (o.get("asin") or "").strip().upper()
        if a:
            entry = asin_to_meta.setdefault(a, {"title": "", "sku": ""})
            if not entry.get("title") and o.get("title"):
                entry["title"] = str(o["title"]).strip()
            if not entry.get("sku") and (o.get("sku") or o.get("zoro_sku")):
                entry["sku"] = str(o.get("sku") or o.get("zoro_sku")).strip()

    out: Dict[str, List[Dict[str, Any]]] = {k: [] for k in [
        "earnings_increase", "earnings_decrease", "shipping_change",
        "zoro_price_increase", "zoro_price_decrease", "zoro_ship_rule",
        "handling_time_tune", "handling_review", "oos_alert", "alt_sku_used", "low_margin",
        "zip_mismatch", "undelivered", "refunded", "wrong_qty", "shipped_late", "wb_unmatched",
        # listings (10 Nov 2026) — rows of listing_events, see CATEGORY 8 below
        "listing_off_sale", "listing_back_on_sale", "own_brand_other_seller", "own_brand_rating",
        "own_brand_reviews", "own_brand_category",
    ]}

    def add(kind: str, ref_key: str, oid: str, msg: str, sev: str, extra: Optional[Dict[str, Any]] = None):
        if ref_key in resolved.get(kind, set()):
            return
        item = {
            "ref_key": ref_key,
            "order_id": oid or "",
            "message": msg,
            "severity": sev,
            "date": (extra and extra.get("date")) or today
        }
        if extra:
            item.update(extra)

        # Ensure title and SKU are always attached
        a_key = str(item.get("asin") or "").strip().upper()
        if a_key in asin_to_meta:
            if not item.get("title") and asin_to_meta[a_key].get("title"):
                item["title"] = asin_to_meta[a_key]["title"]
            if not item.get("sku") and asin_to_meta[a_key].get("sku"):
                item["sku"] = asin_to_meta[a_key]["sku"]

        out[kind].append(item)

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY 6: Out of Stock (OOS) / Supplier Depletion & Alt SKU
    # ═══════════════════════════════════════════════════════════════════════════
    # One alert per ASIN per day: an out-of-stock listing used to send one for every order.
    oos_by_day: Dict[tuple, Dict[str, Any]] = {}

    def note_oos(acct: str, asin: str, day: str, sku: str, oid: str, ref: str) -> None:
        if not (asin and day):
            return
        e = oos_by_day.setdefault((acct, asin, day), {"sku": sku, "oids": [], "refs": set()})
        if not e["sku"] and sku:
            e["sku"] = sku
        if ref not in e["refs"]:
            e["refs"].add(ref)
            if oid:
                e["oids"].append(oid)

    for b in bot_rows:
        if b.get("action") == "OOS":
            asin = b.get("asin") or ""
            oid = b.get("amazon_id") or ""
            note_oos(b.get("store") or "", asin, _day(b.get("ts")), b.get("sku") or "", oid, oid or asin)

    alt_by_day: Dict[tuple, Dict[str, Any]] = {}
    for o in orders:
        oid = o.get("amazon_order_id") or ""
        m_id = str(o.get("merchant_order_id") or "")
        comm = str(o.get("comments") or "")
        asin = o.get("asin") or ""
        acct = o.get("account") or ""
        sku = o.get("zoro_sku") or o.get("sku") or ""
        d_str = _order_day(o)
        if "OOS" in m_id.upper() or "OOS" in comm.upper():
            note_oos(acct, asin, d_str, sku, oid, oid or asin)

        # Alt SKU Substituted: the order was placed, on the alternative code.
        if o.get("alt_used") == "Yes" and asin and d_str:
            e = alt_by_day.setdefault((acct, asin, d_str), {
                "main": _zoro_code(o.get("main_sku")) or o.get("main_sku") or "primary",
                # closing's `sku` is the Amazon seller SKU; zoro_sku is the code actually bought
                "alt": _zoro_code(o.get("zoro_sku")) or _zoro_code(o.get("sku")) or o.get("sku") or "alt",
                "oids": []})
            e["oids"].append(oid)

    for (acct, asin, d_str), e in oos_by_day.items():
        n = max(len(e["oids"]), 1)
        oid = e["oids"][0] if e["oids"] else ""
        rk = f"{acct}:{asin}:oos:{d_str}"
        on = f" on {n} order{'s' if n > 1 else ''}" if e["oids"] else ""
        msg = f"ASIN {asin} (Zoro {e['sku']}): Supplier is OUT OF STOCK{on} — deactivate listing on Amazon immediately!"
        add("oos_alert", rk, oid, msg, "high", {"asin": asin, "account": acct, "sku": e["sku"], "orders": n, "date": d_str, "link": "order" if oid else "asin"})

    for (acct, asin, d_str), e in alt_by_day.items():
        n = len(e["oids"])
        rk = f"{acct}:{asin}:alt_sku:{d_str}"
        msg = f"ASIN {asin}: Main Zoro SKU {e['main']} is out of stock, so {n} order{'s' if n > 1 else ''} went through on Alternative SKU {e['alt']}"
        add("alt_sku_used", rk, e["oids"][0], msg, "low", {"asin": asin, "account": acct, "main_sku": e["main"], "alt_sku": e["alt"], "orders": n, "date": d_str, "link": "order"})

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY 8: Listings — off sale / back on sale with Zoro's stock, and own-brand changes
    # ═══════════════════════════════════════════════════════════════════════════
    # One item per row of listing_events (utils/listing_events.py): written by the placer's listing
    # guard when it changes a listing because Zoro ran out or restocked, and by the repricer scrape's
    # listing watch (other sellers, rating, number of ratings, category on HOANK / Hathoro listings).
    # Their WhatsApp messages are sent by the writers at once; this only shows them here.
    for ev in listing_event_rows:
        kind, day = ev.get("kind") or "", str(ev.get("at") or "")[:10]
        if kind not in out or not in_window(day):
            continue
        if account and account not in ("All", "all", "") and ev.get("store") != account:
            continue
        add(kind, ev.get("ref_key") or "", "", ev.get("message") or "", ev.get("severity") or "med", {
            "asin": ev.get("asin") or "", "account": ev.get("store") or "", "sku": ev.get("sku") or "",
            "title": ev.get("title") or "", "date": day, "link": "asin"})

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY 7: Low Margin Split (Placed < 8% vs Unplaced Loss-Kamran)
    # ═══════════════════════════════════════════════════════════════════════════
    seen_loss = set()
    for b in bot_rows:
        if b.get("action") == "low_profit":
            oid = b.get("amazon_id") or ""
            asin = b.get("asin") or ""
            d_str = _day(b.get("ts"))
            rk = f"{oid}:loss_rejected" if oid else f"{asin}:loss_rejected:{d_str}"
            if rk not in seen_loss and (oid or asin):
                seen_loss.add(rk)
                msg = f"ASIN {asin}: REJECTED for low margin on Order {oid} — manual review required before ship deadline!"
                add("low_margin", rk, oid, msg, "high", {"asin": asin, "date": d_str, "link": "order" if oid else "asin"})

    low_by_day: Dict[tuple, Dict[str, Any]] = {}
    for o in orders:
        oid = o.get("amazon_order_id") or ""
        asin = o.get("asin") or ""
        acct = o.get("account") or ""
        d_str = _order_day(o)
        m_id = str(o.get("merchant_order_id") or "")
        comm = str(o.get("comments") or "")
        pct = _f(o.get("profit_percent"))
        prof = _f(o.get("profit_dollars"))
        wb = (o.get("wb_order_id") or "").strip()

        # Sub-B: Loss-Kamran checking (unplaced / held)
        if ("LOSS" in m_id.upper() and "KAMRAN" in m_id.upper()) or ("LOSS" in comm.upper() and "KAMRAN" in comm.upper()):
            rk = f"{oid}:loss_kamran"
            if rk not in seen_loss and oid:
                seen_loss.add(rk)
                msg = f"ASIN {asin} ({acct}): REJECTED for low margin on Order {oid} (marked 'Loss-Kamran checking') — manual review required!"
                add("low_margin", rk, oid, msg, "high", {"asin": asin, "account": acct, "date": d_str, "link": "order"})
        elif wb and pct is not None and pct < 8.0 and asin and d_str:
            # Sub-A: Placed with margin < 8%. One alert per ASIN per day, carrying the worst order.
            e = low_by_day.setdefault((acct, asin, d_str), {"n": 0, "pct": pct, "prof": prof, "oid": oid})
            e["n"] += 1
            if pct < e["pct"]:
                e.update(pct=pct, prof=prof, oid=oid)

    for (acct, asin, d_str), e in low_by_day.items():
        rk = f"{acct}:{asin}:low_margin_placed:{d_str}"
        sev = "high" if (e["prof"] is not None and e["prof"] < 0) or e["pct"] < 0 else "med"
        p_str = f"${e['prof']:.2f}" if e["prof"] is not None else "—"
        many = f" ({e['n']} orders that day, this is the lowest)" if e["n"] > 1 else ""
        msg = f"ASIN {asin} ({acct}): PLACED with low margin {e['pct']:.1f}% (< 8%) on Order {e['oid']} (Profit: {p_str}){many} — review pricing!"
        add("low_margin", rk, e["oid"], msg, sev, {"asin": asin, "account": acct, "profit_percent": e["pct"], "profit_dollars": e["prof"], "orders": e["n"], "date": d_str, "link": "order"})

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY 5: Handling time, in business days
    # ═══════════════════════════════════════════════════════════════════════════
    # "Reduce handling" per order is gone: 82% of orders are already at 1 business day, and the
    # days between Zoro's date and Amazon's are Amazon's transit window, which handling time
    # cannot shorten. The listings that can improve come from _handling_review(), weekly.
    late_by_listing: Dict[tuple, Dict[str, Any]] = {}
    for o in orders:
        zdb, adb, asin = _d(o.get("zoro_deliver_by")), _d(o.get("deliver_by_date")), o.get("asin")
        if not (zdb and adb and asin) or "," in asin or zdb <= adb:
            continue
        late = _biz_days(adb, zdb)
        if late < 2:
            continue        # one business day is the carrier's normal variance
        p, placed = _d(o.get("purchase_date")), _d(o.get("placed_at"))
        if p and placed and _biz_days(p, placed) > 1:
            continue        # we placed it late; that says nothing about the listing
        key = (o.get("account") or "", asin)
        h = late_by_listing.get(key)
        if h is None:
            late_by_listing[key] = {"order": o, "late": late, "zdb": zdb, "adb": adb, "n": 1}
        else:
            h["n"] += 1
            if _order_day(o) > _order_day(h["order"]):
                h.update(order=o, late=late, zdb=zdb, adb=adb)

    for (acct, asin), h in late_by_listing.items():
        o, d_str = h["order"], _order_day(h["order"])
        oid = o.get("amazon_order_id") or ""
        week = _monday(_d(d_str)).isoformat() if d_str else ""
        rk = f"{acct}:{asin}:increase_handling:{week}"
        many = f" ({h['n']} orders)" if h["n"] > 1 else ""
        msg = f"ASIN {asin} ({acct}): Zoro delivers {h['zdb']:%a, %b} {h['zdb'].day}, Amazon promised {h['adb']:%a, %b} {h['adb'].day} — {h['late']} business days late{many}. INCREASE handling time on Amazon"
        add("handling_time_tune", rk, oid, msg, "high", {"asin": asin, "account": acct, "late_business_days": h["late"], "date": d_str, "link": "order"})

    for r in handling_review:
        add("handling_review", r["ref_key"], "", r["message"], "low", {
            "asin": r["asin"], "account": r["account"], "usual_handling": r["usual_handling"],
            "orders": r["orders"], "date": r["date"], "link": "asin"})

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY 4: Zoro Shipping Fee Deviation
    # ═══════════════════════════════════════════════════════════════════════════
    # The $50 free-shipping line is judged on the list price before discounts. That is Zoro's
    # Unit Price × quantity; rebuilding it as subtotal + savings double-counted coupons on rows
    # whose subtotal was already pre-discount, and 70 of 154 alerts checked were false.
    for o in orders:
        oid = o.get("amazon_order_id") or ""
        wb = (o.get("wb_order_id") or "").strip()
        zship = _f(o.get("zoro_shipping"))
        if not wb or zship is None:
            continue
        unit = _f(o.get("zoro_unit_price")) or _f(o.get("zoro_list_price")) or 0.0
        qty = max(_f(o.get("zoro_qty")) or _f(o.get("quantity")) or 1.0, 1.0)
        pre = round(unit * qty, 2)
        zsub = _f(o.get("zoro_subtotal"))
        if zsub is None or zsub <= 0:
            zsub = max((_f(o.get("zoro_order_total")) or 0.0) - (_f(o.get("zoro_tax")) or 0.0) - zship, 0.0)
        if unit <= 0 or zsub <= 0 or zsub > pre + 0.02:
            continue        # no Unit Price, or more than one item on the order: cannot be judged
        exp = 0.0 if pre >= 49.99 else 5.99
        # Only when Zoro charged more than its rule; shipping free just under $50 costs nothing.
        if zship > exp + 0.02:
            rk = f"{oid}:zoro_ship_rule"
            sev = "high" if zship > 10.0 else "med"
            msg = f"Zoro shipping ${zship:.2f} on Order {oid} ≠ standard ${exp:.2f} (list price × qty ${pre:.2f} — carrier change / freight surcharge)"
            add("zoro_ship_rule", rk, oid, msg, sev, {
                "asin": o.get("asin"), "zoro_shipping": zship, "expected_shipping": exp,
                "pre_coupon_subtotal": pre, "date": _order_day(o), "link": "order"
            })

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORIES 1, 2: Per (account, asin) transition detection
    # ═══════════════════════════════════════════════════════════════════════════
    # A listing's orders in real date order, with its earlier orders in front of the window's.
    # closing's row wins: the placer's `quantity` is the Zoro quantity on about 2% of orders (two
    # Amazon units on one Zoro item), which reads as double the earnings per unit.
    by_acct_asin: Dict[tuple, List[Dict[str, Any]]] = {}
    in_history = {h.get("amazon_order_id") for h in listing_history}
    for o in listing_history + [o for o in orders if o.get("amazon_order_id") not in in_history]:
        asin = o.get("asin")
        acct = o.get("account") or "Unknown"
        # 'B0FL…, B0GW…' is one order of two listings, so its per-unit figures describe neither.
        if not asin or "," in asin or o.get("order_status") == "Cancelled" or not _order_day(o):
            continue
        by_acct_asin.setdefault((acct, asin), []).append(o)
    for grp in by_acct_asin.values():
        grp.sort(key=_order_day)

    # 1. Earnings Increase & Decrease (Strictly partitioned by (account, asin))
    for (acct, asin), grp in by_acct_asin.items():
        if len(grp) < 2:
            continue
        grp_sorted = grp

        by_day = {}
        for g in grp_sorted:
            d = _order_day(g)
            earn = _f(g.get("earnings"))
            if earn is None or earn <= 0:
                continue
            ship = _f(g.get("shipping_amt")) or 0.0
            net_prod = earn - (ship * 0.85)
            qty = max(_f(g.get("quantity")) or 1.0, 1.0)
            u_earn = net_prod / qty
            if u_earn > 0:
                by_day.setdefault(d, {"vals": [], "oid": g.get("amazon_order_id")})["vals"].append(u_earn)

        runs = []
        for d in sorted(by_day):
            vals = sorted(by_day[d]["vals"])
            med = vals[len(vals) // 2]
            if runs and abs(runs[-1]["val"] - med) <= 0.02:
                runs[-1]["count"] += len(vals)
            else:
                runs.append({"val": med, "date": d, "oid": by_day[d]["oid"], "count": len(vals)})

        for i in range(1, len(runs)):
            o_val, n_val = runs[i - 1]["val"], runs[i]["val"]

            # Multi-Pack Divider Shield: suppress 2x, 3x, 4x jump on 1 order
            ratio = n_val / o_val if o_val > 0 else 1.0
            is_mult = any(abs(ratio - m) < 0.06 for m in (2.0, 3.0, 4.0)) or any(abs(1.0 / ratio - m) < 0.06 for m in (2.0, 3.0, 4.0))
            if is_mult and runs[i]["count"] == 1:
                continue

            diff = n_val - o_val
            pct_change = abs(diff) / o_val if o_val > 0 else 0.0
            if abs(diff) < 0.50 and pct_change < 0.02:
                continue

            d_str = runs[i]["date"]
            if not in_window(d_str):
                continue
            cnt = runs[i]["count"]
            oid = runs[i]["oid"]
            if diff > 0:
                rk = f"{acct}:{asin}:earn_inc:{d_str}"
                msg = f"ASIN {asin} ({acct}): earnings INCREASED ↑ ${o_val:.2f} → ${n_val:.2f}/unit (+${diff:.2f}, {cnt} order{'s' if cnt > 1 else ''} at new)"
                add("earnings_increase", rk, oid, msg, "med", {
                    "asin": asin, "account": acct, "old": round(o_val, 2),
                    "new": round(n_val, 2), "date": d_str, "link": "asin"
                })
            else:
                rk = f"{acct}:{asin}:earn_dec:{d_str}"
                sev = "high" if pct_change >= 0.10 else "med"
                msg = f"ASIN {asin} ({acct}): earnings DECREASED ↓ ${o_val:.2f} → ${n_val:.2f}/unit (-${abs(diff):.2f}, {cnt} order{'s' if cnt > 1 else ''} at new)"
                add("earnings_decrease", rk, oid, msg, sev, {
                    "asin": asin, "account": acct, "old": round(o_val, 2),
                    "new": round(n_val, 2), "date": d_str, "link": "asin"
                })

    # 2. Shipping: a buyer paid on a listing that ships free, or a paid listing went free
    #    (strictly by (account, asin)). Orders not yet in closing have no shipping figure; a blank
    #    is unknown, not free.
    def ship_of(g: Dict[str, Any]) -> Optional[float]:
        a, b = _f(g.get("shipping_amt")), _f(g.get("shipping_price"))
        return None if a is None and b is None else (a or b or 0.0)

    for (acct, asin), grp in by_acct_asin.items():
        known = [(g, ship_of(g)) for g in grp if ship_of(g) is not None]
        consec_paid = 0
        for idx, (g, ship) in enumerate(known):
            oid = g.get("amazon_order_id") or ""
            d_str = _order_day(g)
            if ship > 0.01:
                prior = [s for _, s in known[max(0, idx - 10):idx]]
                if len(prior) >= 3 and sum(1 for s in prior if s <= 0.01) >= 0.7 * len(prior) and in_window(d_str):
                    rk = f"{acct}:{asin}:ship_paid:{oid}"
                    msg = f"ASIN {asin} ({acct}): Buyer paid ${ship:.2f} for faster shipping on Order {oid} (this listing usually ships free) — make sure it goes out fast"
                    add("shipping_change", rk, oid, msg, "med", {
                        "asin": asin, "account": acct, "shipping_amt": ship, "date": d_str, "link": "order"
                    })
                consec_paid += 1
            else:
                if consec_paid >= 3 and in_window(d_str):
                    rk = f"{acct}:{asin}:ship_revert_free:{oid}"
                    msg = f"ASIN {asin} ({acct}): Order {oid} has FREE shipping, but the previous {consec_paid} orders had buyer-paid shipping — verify shipping template!"
                    add("shipping_change", rk, oid, msg, "high", {
                        "asin": asin, "account": acct, "date": d_str, "link": "order"
                    })
                consec_paid = 0

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY 3: Zoro price, from the Unit Price on Zoro's order e-mails
    # ═══════════════════════════════════════════════════════════════════════════
    for c in zoro_changes:
        if account and account not in ("All", "all", "") and c["account"] != account:
            continue        # one alert per change: it belongs to the store that showed it first
        code, old, new, diff = c["code"], c["old"], c["new"], round(c["new"] - c["old"], 2)
        pct = diff / old * 100.0 if old > 0 else 0.0
        word, kind, sev, arrow, sign = ("INCREASED", "zoro_price_increase", "high", "↑", "+") if diff > 0 else ("DECREASED", "zoro_price_decrease", "med", "↓", "-")
        listings = ", ".join(f"{a} ({s})" for s, a in c["listings"][:4]) if c["listings"] else (c["asin"] or "—")
        if len(c["listings"]) > 4:
            listings += f" +{len(c['listings']) - 4} more"
        # The alert ends at the old price's date (owner, 11 Nov 2026); the listings on the code stay
        # on the item as data, not in the text.
        msg = (f"Zoro SKU {code}: Unit Price {word} {arrow} ${old:.2f} → ${new:.2f}/ea ({sign}${abs(diff):.2f}, {pct:+.1f}%). "
               f"New price first on {c['wb']} ({_short_day(c['ts'])}); old price last on {c['old_wb']} ({_short_day(c['old_ts'])})")
        add(kind, f"zoro_price:{code}:{c['wb']}", c["order_id"], msg, sev, {
            "asin": c["asin"], "account": c["account"], "sku": code, "title": c["title"], "old": old,
            "new": new, "wb": c["wb"], "old_wb": c["old_wb"], "date": _day(c["ts"]), "link": "asin",
            "listings": listings
        })

    # ═══════════════════════════════════════════════════════════════════════════
    # Preserved Core Operational Protections (Zip, Undelivered, Refunded, etc.)
    # ═══════════════════════════════════════════════════════════════════════════
    for o in orders:
        oid = o.get("amazon_order_id") or ""
        asin = o.get("asin") or ""
        d_str = _order_day(o)

        # Zip Mismatch
        az, zz = _digits(o.get("ship_postal_code"))[:5], _digits(o.get("zoro_buyer_zip"))[:5]
        if len(az) == 5 and len(zz) == 5 and az != zz:
            rk = f"{oid}:zip_mismatch"
            msg = f"Ship zip {o.get('ship_postal_code')} != Zoro {o.get('zoro_buyer_zip')}"
            add("zip_mismatch", rk, oid, msg, "high", {
                "asin": asin, "ship_city": o.get("ship_city"), "date": d_str, "link": "order"
            })

        # Undelivered past due
        ds = o.get("delivery_status")
        if (ds and ds not in DELIVERED and o.get("manual_delivered") != "Yes"
                and o.get("order_label") not in ("Refunded", "Cancelled") and o.get("order_status") != "Cancelled"):
            db_ = _day(o.get("deliver_by_date"))
            if db_ and db_ < today:
                rk = f"{oid}:undelivered"
                msg = f"Not delivered (status {ds}), due {db_}"
                add("undelivered", rk, oid, msg, "high", {
                    "asin": asin, "delivery_status": ds, "deliver_by_date": db_, "date": d_str, "link": "order"
                })

        # Refunded
        if o.get("refund_done") == "Yes":
            rk = f"{oid}:refunded"
            deliv = o.get("delivery_status") in DELIVERED
            ref_amt = _f(o.get("refund_amount")) or 0.0
            amt = f"${ref_amt:.2f}" if ref_amt > 0 else "(amount not known yet)"
            msg = f"Refunded {amt} — {'returned by customer' if deliv else 'NOT delivered → claim from Zoro'}"
            add("refunded", rk, oid, msg, "high", {
                "asin": asin, "refund_amount": ref_amt, "delivery_status": ds, "date": d_str, "link": "order"
            })

        # Wrong Qty vs verified
        v = verified.get(asin)
        if v:
            exp_qty = (_f(o.get("quantity")) or 1) * v["qty_to_place"] * v["zoro_multiple"]
            zq = _f(o.get("zoro_qty"))
            if zq and zq > 0 and exp_qty and abs(zq - exp_qty) >= 1:
                rk = f"{oid}:wrong_qty"
                msg = f"Placed Zoro qty {zq:g}, expected {exp_qty:g} (×{v['qty_to_place']:g}×{v['zoro_multiple']:g})"
                add("wrong_qty", rk, oid, msg, "high", {
                    "asin": asin, "zoro_qty": zq, "expected_qty": exp_qty, "date": d_str, "link": "order"
                })

        # Shipped LATE vs Amazon ship-by
        if o.get("shipped_on_time") == "Late":
            mh = _f(o.get("ship_margin_hrs"))
            by = f" by {abs(mh)/24:.1f}d" if mh is not None else ""
            rk = f"{oid}:shipped_late"
            add("shipped_late", rk, oid, f"Shipped LATE vs ship-by{by}", "high", {
                "asin": asin, "ship_margin_hrs": mh, "date": d_str, "link": "order"
            })

        # WB unmatched
        wb = (o.get("wb_order_id") or "").strip()
        if wb and placed_wbs and wb not in placed_wbs and o.get("order_status") != "Cancelled" and o.get("order_label") != "Pending":
            rk = f"{oid}:wb_unmatched"
            add("wb_unmatched", rk, oid, f"WB {wb} not in placed orders — verify/cancel", "med", {
                "asin": asin, "wb": wb, "date": d_str, "link": "order"
            })

    counts = {k: len(v) for k, v in out.items()}
    return {"insights": out, "counts": counts, "total": sum(counts.values())}


if __name__ == "__main__":
    import sys
    acct = sys.argv[1] if len(sys.argv) > 1 else None
    res = compute(account=acct)
    print("Counts:", json.dumps(res["counts"], indent=2))
    print("Total alerts:", res["total"])
    for k, v in res["insights"].items():
        if v:
            print(f"\n{k} ({len(v)}):")
            for a in v[:3]:
                print("   ", a.get("order_id") or a.get("asin"), "|", a["message"])
