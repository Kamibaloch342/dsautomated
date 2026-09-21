"""Query helpers shared by the dashboard modules (server.py, daybook.py).

union_query — a fast "top N by date" over v_orders_all.

v_orders_all is `closing_orders UNION ALL <placer-only rows>`. SQLite cannot push an
ORDER BY ... LIMIT through a compound view: it copies every row of both halves into a temp
b-tree and sorts (75k rows x 70 columns for a 3,000-row page — 0.6 s on a Mac, many seconds
on Render's free CPU, per request). Run the same query as two limited halves instead, each
of which walks its own date index and stops after N rows, then merge the ≤ 2N survivors.
Results are identical to the plain view query; if the view cannot be split the plain query
runs unchanged.
"""
import os
import re
import sqlite3
import threading

_HALVES = {}
_HALVES_LOCK = threading.Lock()
_UNION_RE = re.compile(r"\bUNION\s+ALL\b", re.I)


def _db_file(c):
    try:
        for r in c.execute("PRAGMA database_list"):
            if r[1] == "main":
                return r[2] or ""
    except sqlite3.Error:
        pass
    return ""


def view_halves(c, view):
    """(half1_sql, half2_sql) of a two-part UNION ALL view, or None. Cached per DB copy."""
    path = _db_file(c)
    try:
        st = os.stat(path)
        key = (path, view, st.st_mtime_ns, st.st_size)
    except OSError:
        key = (path, view, 0, 0)
    with _HALVES_LOCK:
        if key in _HALVES:
            return _HALVES[key]
    halves = None
    try:
        row = c.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name=?", (view,)).fetchone()
        sql = row[0] if row else ""
        body = sql[sql.upper().find("SELECT"):] if sql else ""
        parts = _UNION_RE.split(body)
        if len(parts) == 2 and parts[0].strip().upper().startswith("SELECT") \
                and parts[1].strip().upper().startswith("SELECT"):
            halves = (parts[0].strip(), parts[1].strip())
    except sqlite3.Error:
        halves = None
    with _HALVES_LOCK:
        if len(_HALVES) > 8:
            _HALVES.clear()
        _HALVES[key] = halves
    return halves


def union_query(c, view, sel, where, args, order_col, limit, desc=True):
    """Rows of `SELECT {sel} FROM {view} WHERE {where} ORDER BY {order_col} [DESC] LIMIT {limit}`.

    order_col and limit are trusted (callers whitelist them); where/args are the usual
    parameterised pair. `sel` is a column list or '*'."""
    limit = int(limit)
    direction = "DESC" if desc else "ASC"
    plain = f"SELECT {sel} FROM {view} WHERE {where} ORDER BY {order_col} {direction} LIMIT {limit}"
    halves = view_halves(c, view)
    if not halves:
        return c.execute(plain, args).fetchall()
    sel_out = sel
    if sel != "*":
        names = [s.strip() for s in sel.split(",")]
        if order_col not in names:                 # the compound ORDER BY needs its column
            sel_out = sel + ", " + order_col
    h1, h2 = halves
    sql = (f"SELECT {sel_out} FROM (SELECT * FROM ({h1}) WHERE {where} "
           f"ORDER BY {order_col} {direction} LIMIT {limit}) "
           f"UNION ALL "
           f"SELECT {sel_out} FROM (SELECT * FROM ({h2}) WHERE {where} "
           f"ORDER BY {order_col} {direction} LIMIT {limit}) "
           f"ORDER BY {order_col} {direction} LIMIT {limit}")
    try:
        return c.execute(sql, list(args) + list(args)).fetchall()
    except sqlite3.OperationalError:
        return c.execute(plain, args).fetchall()
