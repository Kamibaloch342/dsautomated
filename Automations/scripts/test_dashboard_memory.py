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

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
