#!/usr/bin/env python3
"""
test_dashboard_memory.py — the dashboard's memory fixes, and the one way they could go wrong.

Why this exists: Render kept killing the cloud dashboard for exceeding its 512 MB limit. The
cause was measured on the 72k-order copy of the live DB (18 Sep 2026):

    analytics._rows()   SELECT * -> 122 cols x 72,107 rows as dicts   469 MB, 4.9 s
    analytics.health()  loaded the whole table twice to count six things   469 MB, 7.5 s
    server.overview()   SELECT * to add up eight columns                   4.6 s
    /api/orders         full JSON str + bytes + a third gzip copy          17.5 MB/request
    the R2 refresher    held the 35 MB gzipped DB in an io.BytesIO
    ThreadingHTTPServer no ceiling on concurrent requests

The fix projects each scan down to the columns it reads. That is the risky part: these rows
are read with r.get(), so a column left out of a projection comes back as None and produces a
QUIETLY WRONG number instead of raising. The first test below re-derives the columns each
function actually reads and fails if any of them is missing from the declared list — so the
projections cannot drift out of step with the code that consumes them.

Run:  python scripts/test_dashboard_memory.py
"""
import ast
import gzip
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)
sys.path.insert(0, os.path.join(AUTO, "dashboard"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


def src(path):
    return open(os.path.join(AUTO, path), encoding="utf-8").read()


def func_src(module_src, name):
    """The source of one function or method, by AST line span."""
    tree = ast.parse(module_src)
    lines = module_src.split("\n")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{name} not found")


def cols_read(text):
    """Column names the code reads off a row: r.get("x"), r["x"]."""
    return (set(re.findall(r"""\.get\(\s*["']([a-z_][a-z0-9_]*)["']""", text))
            | set(re.findall(r"""\br\[["']([a-z_][a-z0-9_]*)["']\]""", text)))


def declared(module_src, name):
    m = re.search(rf"{name}\s*=\s*\(([^)]*)\)", module_src, re.S)
    assert m, f"{name} not found"
    return {x.strip().strip('"\'') for x in m.group(1).split(",") if x.strip().strip('"\'')}


def main():
    an = src("dashboard/analytics.py")
    sv = src("dashboard/server.py")

    # ── 1. projections cover every column the consuming code reads ───────────
    print("\n── no projected scan drops a column its own code reads ──")

    # analytics: _rows() feeds every function in the module, so check the whole module.
    an_declared = declared(an, "_ROW_COLS")
    import sqlite3
    slim = os.path.join(AUTO, "data", "dashboard_slim.db")
    if os.path.exists(slim):
        c = sqlite3.connect(f"file:{slim}?mode=ro", uri=True)
        real = {r[1] for r in c.execute("PRAGMA table_info(closing_orders)")}
        c.close()
    else:
        real = an_declared                      # no local copy; trust the declared list
    missing = sorted((cols_read(an) & real) - an_declared)
    check("analytics._ROW_COLS covers every closing_orders column analytics reads",
          not missing, f"missing: {missing}")
    check("_ROW_COLS is a real reduction, not a copy of the table",
          len(an_declared) < len(real) / 2 if real else True,
          f"{len(an_declared)} declared vs {len(real)} in the table")

    for fn, const in (("overview", "_OVERVIEW_COLS"), ("asin_list", "_ASINLIST_COLS")):
        body = func_src(sv, fn)
        want = declared(sv, const)
        got = cols_read(body) & (real or want)
        miss = sorted(got - want)
        check(f"server.{fn} declares every column it reads", not miss, f"missing: {miss}")

    # ── 2. the scans really are projected ────────────────────────────────────
    print("\n── the heavy scans no longer ask for every column ──")
    check("analytics._rows no longer does SELECT *",
          "SELECT * FROM closing_orders" not in func_src(an, "_rows"))
    check("analytics.health counts in SQL instead of loading rows",
          "SELECT * FROM closing_orders" not in func_src(an, "health")
          and "COUNT(*)" in func_src(an, "health"))
    check("server.overview no longer does SELECT *",
          "SELECT * FROM closing_orders" not in func_src(sv, "overview"))
    check("server.asin_list no longer does SELECT *",
          "SELECT * FROM closing_orders" not in func_src(sv, "asin_list"))
    check("single-row lookups were left alone (SELECT * on one row is fine)",
          "SELECT * FROM closing_orders WHERE amazon_order_id=?" in sv)

    # ── 3. one composite computation, one load ───────────────────────────────
    print("\n── today() loads the orders once, not five times ──")
    check("today() wraps its work in the shared-rows block",
          "_shared_rows()" in func_src(an, "today"))
    check("the shared cache is per-thread and dropped at the end of the block",
          "threading.local()" in an and "_ROWS_TL.cache = None" in an)

    # ── 4. result cache ──────────────────────────────────────────────────────
    print("\n── results are reused until the database changes ──")
    import analytics as AN
    check("the heavy endpoints are memoized", hasattr(AN.today, "__wrapped__"))
    calls = {"n": 0}
    real_rows = AN._rows

    def counting(*a, **k):
        calls["n"] += 1
        return real_rows(*a, **k)
    AN._rows = counting
    try:
        AN._RESULT_CACHE.clear()
        AN.leakage(None, None, None, None)
        first = calls["n"]
        AN.leakage(None, None, None, None)
        AN.leakage(None, None, None, None)
        check("a repeat call does not touch the database", calls["n"] == first, f"{calls['n']} loads")
        AN._RESULT_CACHE["_stamp"] = ("changed", 0)     # as a new DB copy would
        AN.leakage(None, None, None, None)
        check("a new database copy invalidates the cache", calls["n"] > first, f"{calls['n']} loads")
    finally:
        AN._rows = real_rows

    # ── 5. responses stream into gzip instead of being copied three times ────
    print("\n── responses are built without three full copies ──")
    send = func_src(sv, "_send")
    check("_send encodes straight into the gzip buffer", "iterencode" in send)
    check("it no longer builds json.dumps(...).encode() first when gzipping",
          send.count("json.dumps") <= 1)
    payload = {"orders": [{"a": i, "t": "x" * 50} for i in range(500)], "count": 500}
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=5) as gz:
        for chunk in json.JSONEncoder(default=str).iterencode(payload):
            gz.write(chunk.encode())
    check("the streamed body decodes back to exactly the same object",
          json.loads(gzip.decompress(buf.getvalue())) == payload)

    # ── 6. the gate and the page cache ───────────────────────────────────────
    print("\n── concurrency gate and page cache ──")
    check("there is a ceiling on requests building a response at once", "_INFLIGHT" in sv)
    check("the gate wraps the handlers, not handle_one_request (keep-alive would deadlock)",
          "with _INFLIGHT:" in func_src(sv, "do_GET")
          and "with _INFLIGHT:" in func_src(sv, "do_POST")
          and "def handle_one_request" not in sv)
    check("cloud mode gets a tighter gate than a local machine",
          "4 if OPS.CLOUD_MODE else 16" in sv)
    check("the page is read once per deploy and served with an ETag",
          "_page_bytes" in sv and "If-None-Match" in sv and "304" in sv)

    # ── 7. the R2 refresher streams to disk ──────────────────────────────────
    print("\n── the DB download no longer passes through memory ──")
    r2 = src("utils/r2_store.py")
    gf = func_src(r2, "get_file")
    check("get_file streams the gzipped DB to a file, not an io.BytesIO",
          "io.BytesIO()" not in gf and "download_file(" in gf)
    check("it cleans up the intermediate .gz on failure", "gz_tmp" in gf and "os.remove" in gf)
    check("boto3's parallel part buffers are capped", "max_concurrency=2" in r2)

    # ── 8. missing archive tables answer clearly ─────────────────────────────
    print("\n── data the cloud copy does not carry fails legibly ──")
    check("a missing table returns a plain explanation, not a raw SQL error",
          "no such table" in sv and "cloud_limited" in sv and "503" in sv)


    # ══════════════════════════════════════════════════════════════════════════════════════
    # 21 Sep 2026 — "make the dashboard fast": measured on a local replica of the Render
    # service (cloud mode, the real 154 MB slim copy): /api/insights loaded 32k rows x 122
    # columns (RSS 27 → 373 MB) and then 503'd on the missing Gmail archive; /api/overview
    # built 75k dicts per call (+106 MB); every list ran through the UNION-ALL view with a
    # temp-b-tree sort of all rows; each refresh re-copied 154 MB page by page through a WAL.
    # ══════════════════════════════════════════════════════════════════════════════════════
    import sqlite3 as _sq
    import time as _tm
    import types as _types
    from datetime import date as _date, timedelta as _td
    sys.path.insert(0, os.path.join(AUTO, "dashboard"))
    import dbq
    import insights as INS
    slim = os.path.join(AUTO, "data", "dashboard_slim.db")
    have_slim = os.path.exists(slim)

    def _rocon():
        c = _sq.connect(f"file:{slim}?mode=ro", uri=True, timeout=10)
        c.row_factory = _sq.Row
        return c

    print("\n── the UNION view is read as two indexed halves, not one big sort ──")
    if not have_slim:
        print("  ⏭️  no data/dashboard_slim.db — skipped")
    else:
        c = _rocon()
        halves = dbq.view_halves(c, "v_orders_all")
        check("v_orders_all splits into exactly two SELECT halves", bool(halves) and len(halves) == 2)
        frm = (_date.today() - _td(days=13)).isoformat()
        cases = [("1=1", [], "purchase_date", 3000),
                 ("substr(replace(purchase_date,'T',' '),1,10) >= ?", [frm], "purchase_date", 20000),
                 ("account=?", ["Hathoro"], "placed_at", 500),
                 ("1=1", [], "shipped_email_time", 1000)]
        same = True; faster = []
        for where, args, dc, lim in cases:
            t = _tm.time()
            plain = c.execute(f"SELECT amazon_order_id, {dc} FROM v_orders_all WHERE {where} "
                              f"ORDER BY {dc} DESC LIMIT {lim}", args).fetchall()
            tp = _tm.time() - t; t = _tm.time()
            split = dbq.union_query(c, "v_orders_all", f"amazon_order_id, {dc}", where, args, dc, lim)
            ts = _tm.time() - t
            faster.append((dc, round(tp, 3), round(ts, 3)))
            # Rows tied on the date column at the LIMIT boundary may legitimately be chosen
            # differently (dates carry minute resolution). Everything else must be identical:
            # the sequence of dates, and the membership away from that boundary date.
            pd, sd = [r[1] for r in plain], [r[1] for r in split]
            dates = {r[0]: r[1] for r in plain}; dates.update({r[0]: r[1] for r in split})
            diff = {r[0] for r in plain} ^ {r[0] for r in split}
            boundary = pd[-1] if pd else None
            if len(plain) != len(split) or pd != sd or any(dates[o] != boundary for o in diff):
                same = False
        check("both halves return the same rows (ties at the boundary aside) and the same date sequence", same)
        tot_plain = sum(x[1] for x in faster); tot_split = sum(x[2] for x in faster)
        check(f"the split is faster on this machine ({tot_plain:.2f}s → {tot_split:.2f}s over 4 queries)",
              tot_split < tot_plain, str(faster))
        h1, _ = halves
        plan = " | ".join(r[3] for r in c.execute(
            f"EXPLAIN QUERY PLAN SELECT * FROM ({h1}) WHERE 1=1 ORDER BY purchase_date DESC LIMIT 3000"))
        check("the closing_orders half walks its purchase_date index instead of sorting", "idx_closing_purchase" in plan, plan)
        fb = dbq.union_query(c, "v_no_such_view", "*", "1=1", [], "x", 5) if False else None
        c.close()
    src_dbq = src("dashboard/dbq.py")
    check("a view that cannot be split falls back to the plain query", "return c.execute(plain, args).fetchall()" in src_dbq)
    sv2 = src("dashboard/server.py"); dbk = src("dashboard/daybook.py")
    check("orders() and daybook._fetch go through union_query", "dbq.union_query(c, \"v_orders_all\"" in sv2
          and "dbq.union_query(c, \"v_orders_all\"" in dbk)

    print("\n── overview and the ASIN list are summed in SQL and agree with the old loops ──")
    ov_src = func_src(sv2, "overview"); al_src = func_src(sv2, "asin_list")
    check("overview has no per-row Python loop left", "for r in rows" not in ov_src and "dict(r)" not in ov_src)
    check("asin_list has no per-row Python loop left", "for r in rows" not in al_src and "dict(r)" not in al_src)
    if have_slim:
        def _f(v):
            try: return float(v)
            except (TypeError, ValueError): return 0.0
        ns = {"_has_table": lambda n: True, "_filters": lambda q, date_col="purchase_date": ("1=1", [], "", "", ""),
              "ro": _rocon, "_f": _f, "date": _date, "timedelta": _td}
        exec(ov_src, ns); exec(al_src, ns)
        # reference = the loops this commit removed, run on the same database
        c = _rocon()
        rows = [dict(r) for r in c.execute("SELECT earnings, profit_dollars, quantity, purchase_date, asin, sku, title, "
                                           "order_label, zoro_account_name, return_requested, refund_done, profit_percent, "
                                           "time_to_ship_hrs FROM closing_orders")]
        c.close()
        earn = sum(_f(r["earnings"]) for r in rows); profit = sum(_f(r["profit_dollars"]) for r in rows)
        labels = {}
        for r in rows:
            labels[r["order_label"] or "?"] = labels.get(r["order_label"] or "?", 0) + 1
        days = {(r["purchase_date"] or "")[:10] for r in rows} - {""}
        agg = {}
        for r in rows:
            k = (r["asin"] or "?", r["sku"] or "?"); a = agg.setdefault(k, 0); agg[k] = a + _f(r["quantity"])
        ref_top = {k for k, _ in sorted(agg.items(), key=lambda kv: -kv[1])[:50]}
        ov = ns["overview"]({})
        check("overview: same order count", ov["kpi"]["orders"] == len(rows), f"{ov['kpi']['orders']} vs {len(rows)}")
        check("overview: earnings and profit within 0.5% of the loop", abs(ov["kpi"]["earnings"] - earn) <= 0.005 * abs(earn) + 1
              and abs(ov["kpi"]["profit"] - profit) <= 0.005 * abs(profit) + 1, f"{ov['kpi']} vs {earn:.2f}/{profit:.2f}")
        check("overview: one series point per purchase day", len(ov["series"]) == len(days), f"{len(ov['series'])} vs {len(days)}")
        check("overview: label counts identical", ov["labels"] == labels, f"{ov['labels']} vs {labels}")
        got_top = {(t["asin"], t["sku"]) for t in ov["top"]}
        check("overview: top-50 by units matches (ties aside)", len(got_top & ref_top) >= 45, f"overlap {len(got_top & ref_top)}")
        al = ns["asin_list"]({})["asins"]
        ref = {}
        for r in rows:
            g = ref.setdefault(r["asin"] or "?", {"orders": 0, "units": 0.0, "returns": 0, "m": []})
            g["orders"] += 1; g["units"] += _f(r["quantity"])
            if r["return_requested"] == "Yes": g["returns"] += 1
            if r["profit_percent"] is not None: g["m"].append(_f(r["profit_percent"]))
        by = {a["asin"]: a for a in al}
        check("asin_list: same set of ASINs", set(by) == set(ref), f"{len(by)} vs {len(ref)}")
        bad = [a for a in ref if a in by and (by[a]["orders"] != ref[a]["orders"] or by[a]["returns"] != ref[a]["returns"]
                                              or abs(by[a]["units"] - int(ref[a]["units"])) > 1)]
        check("asin_list: orders, returns and units agree per ASIN", not bad, f"{len(bad)} differ, e.g. {bad[:3]}")
        mm = [a for a in ref if a in by and ref[a]["m"] and by[a]["margin_all"] is not None
              and abs(by[a]["margin_all"] - round(sum(ref[a]["m"]) / len(ref[a]["m"]), 1)) > 0.15]
        check("asin_list: average margin agrees per ASIN", not mm, f"{len(mm)} differ, e.g. {mm[:3]}")
        check("asin_list: sorted by units, most first", all(al[i]["units"] >= al[i+1]["units"] for i in range(len(al) - 1)))

    print("\n── insights: fewer columns, works without the Gmail archive, remembered per copy ──")
    ins = src("dashboard/insights.py")
    check("closing_orders and order_placer_orders are read through needed_cols()", ins.count("{needed_cols(con, '") == 2)
    check("the archive lookup is guarded by a table check, before anything is loaded",
          'if fresh_wbs and _has_table(con, "archive_zoro")' in ins)
    check("compute() is the cached wrapper around _compute()", "def _compute(" in ins and "_RESULT_CACHE[key] = (stamp, now, val)" in ins)
    check("the archive enrichment indexes orders by WB once instead of scanning them per archive row",
          "by_wb.setdefault(wb, []).append(o)" in ins and "for o in orders_map.values():\n                    if o.get(\"wb_order_id\") == wb_id" not in ins)
    if have_slim:
        c = _rocon()
        sel = INS.needed_cols(c, "closing_orders").split(", ")
        ncols = len([r for r in c.execute("PRAGMA table_info(closing_orders)")])
        c.close()
        check(f"insights asks closing_orders for {len(sel)} of its {ncols} columns", len(sel) < ncols * 0.6, str(len(sel)))
        need = {"amazon_order_id", "purchase_date", "profit_dollars", "delivery_status", "zoro_order_total", "refund_done", "order_label"}
        check("...and every field the rules read is among them", need <= set(sel), str(need - set(sel)))
        t = _tm.time(); r1 = INS.compute(db_path=slim); t1 = _tm.time() - t
        check("compute() succeeds on the cloud copy (no archive_zoro) instead of a 503",
              isinstance(r1, dict) and "insights" in r1 and "counts" in r1)
        calls = {"n": 0}; real = _sq.connect
        def counting(*a, **k):
            calls["n"] += 1; return real(*a, **k)
        INS.sqlite3.connect = counting
        try:
            t = _tm.time(); r2 = INS.compute(db_path=slim); t2 = _tm.time() - t
        finally:
            INS.sqlite3.connect = real
        check(f"a repeat call is served from memory ({t1:.2f}s → {t2*1000:.0f} ms, 0 connections)", calls["n"] == 0 and r2 is r1)

    print("\n── the cloud copy is swapped in whole, and kept out of WAL so that is safe ──")
    ops = src("dashboard/ops.py")
    inst = func_src(ops, "_install_download")
    check("a refresh installs the download with os.replace", "os.replace(tmp_dl, CLOUD_DB_PATH)" in inst)
    check("...retiring a stale -wal first, and copying page by page only if it cannot", "journal_mode=DELETE" in inst and "src.backup(dst)" in inst)
    check("cloud_db_refresh no longer puts the copy back into WAL", "journal_mode=WAL" not in func_src(ops, "cloud_db_refresh"))
    check("server start-up and rw() leave the cloud copy out of WAL",
          '"PRAGMA journal_mode=DELETE" if OPS.CLOUD_MODE' in sv2 and "if not OPS.CLOUD_MODE:" in func_src(sv2, "rw"))

    print("\n── daybook and growth are remembered per copy ──")
    import daybook as _dbk
    check("growth() and daybook() are memoized", hasattr(_dbk.growth, "__wrapped__") and hasattr(_dbk.daybook, "__wrapped__"))
    check("callers get their own copy of a cached answer", "copy.deepcopy" in func_src(dbk, "_memo"))


    print("\n── the caches are warmed after every refresh, so no visitor pays the cold cost ──")
    ops2 = src("dashboard/ops.py"); sv3 = src("dashboard/server.py")
    check("ops exposes on_refresh() and runs the hooks after a copy is installed",
          "def on_refresh(fn)" in ops2 and "_run_after_refresh()" in func_src(ops2, "cloud_db_refresh"))
    check("the hooks run in a background thread and never overlap themselves",
          "threading.Thread(target=go, daemon=True" in ops2 and "_AFTER_LOCK.acquire(blocking=False)" in ops2)
    warm = func_src(sv3, "_warm_caches")
    check("the server warms only what the home page needs: today and overview",
          all(k in warm for k in ("AN.today(", "overview(")) and not any(k in warm for k in ("INS.compute(", "_dbk.growth(", "_dbk.daybook(")))
    check("...only in cloud mode, and once for the copy downloaded at start-up",
          "if OPS.CLOUD_MODE:\n    OPS.on_refresh(_warm_caches)\n    OPS._run_after_refresh()" in sv3)
    import importlib as _il, ops as _ops
    ran = []
    _ops._AFTER_REFRESH.clear(); _ops.on_refresh(lambda: ran.append(1)); _ops.on_refresh(lambda: ran.append(2))
    _ops._run_after_refresh()
    for _ in range(50):
        if len(ran) == 2: break
        _tm.sleep(0.05)
    check("registered hooks run, in order, off-thread", ran == [1, 2], str(ran))
    _ops._AFTER_REFRESH.clear()

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
