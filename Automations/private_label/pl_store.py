#!/usr/bin/env python3
"""
pl_store.py — the private-label pipeline's one table in the unified DB.

pl_rescore.py scores candidates and build_pl_listings.py turns them into listings; both need the
same rows, and so does the dashboard. A CSV on one machine cannot do that — the dashboard is
served from Render off the slim DB the cloud agent exports, so anything the operator must see has
to live in automations_unified.db. This is that table and nothing else.

The split of ownership matters and is enforced by the SQL below: re-scoring owns the economics
(cost, price, demand) and rewrites them every run; building owns the build columns (sku,
built_at, image_ok) and re-scoring must never clear them, or every run would forget which
products already have listings sitting on disk.
"""
import os
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
DB = os.path.join(AUTO, "data", "automations_unified.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pl_candidates (
    zoro_no         TEXT PRIMARY KEY,
    asin            TEXT,
    monthly_sold    INTEGER,
    amazon_brand    TEXT,
    amazon_title    TEXT,
    zoro_title      TEXT,
    mpn             TEXT,
    pack            INTEGER,
    zoro_price      REAL,
    landed_cost     REAL,
    list_price      REAL,
    market_price    REAL,
    price_vs_market REAL,
    referral_pct    REAL,
    match_method    TEXT,
    hunter_verdict  TEXT,
    scored_at       TEXT,
    sku             TEXT,
    listing_title   TEXT,
    image_ok        INTEGER,
    built_at        TEXT,
    status          TEXT DEFAULT 'candidate'
);
CREATE INDEX IF NOT EXISTS ix_pl_status ON pl_candidates(status);
CREATE INDEX IF NOT EXISTS ix_pl_sold   ON pl_candidates(monthly_sold DESC);
"""

_SCORE_COLS = ["zoro_no", "asin", "monthly_sold", "amazon_brand", "amazon_title", "zoro_title",
               "mpn", "pack", "zoro_price", "landed_cost", "list_price", "market_price",
               "price_vs_market", "referral_pct", "match_method", "hunter_verdict",
               "scored_at"]


def connect(path=DB, timeout=30):
    """The placers hold this DB open in WAL mode, so wait for the lock rather than failing."""
    conn = sqlite3.connect(path, timeout=timeout)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # columns added after the table first shipped
    have = {r[1] for r in conn.execute("PRAGMA table_info(pl_candidates)")}
    for col, decl in (("referral_pct", "REAL"),):
        if col not in have:
            conn.execute(f"ALTER TABLE pl_candidates ADD COLUMN {col} {decl}")
    return conn


def save_candidates(rows, path=DB):
    """Replace the scored economics, keep whatever has already been built.

    A row that stops qualifying is marked 'dropped' rather than deleted: a listing already built
    from it is live on Amazon, and silently losing the row would hide that.
    """
    now = datetime.now().isoformat(timespec="seconds")
    conn = connect(path)
    try:
        with conn:
            seen = set()
            for r in rows:
                seen.add(r["zoro_no"])
                vals = [r["zoro_no"], r.get("asin"), r.get("monthly_sold") or 0,
                        r.get("brand") or "", r.get("title") or "", r.get("zoro_title") or "",
                        r.get("mpn") or "", int(r.get("zoro_pack") or 1),
                        _f(r.get("zoro_price")), _f(r.get("landed_cost")),
                        _f(r.get("price_for_margin")), _f(r.get("market_price")),
                        _f(r.get("price_vs_market")), _f(r.get("referral_pct")),
                        r.get("match") or "",
                        r.get("hunter_verdict") or "", now]
                cols = ",".join(_SCORE_COLS)
                marks = ",".join("?" * len(_SCORE_COLS))
                sets = ",".join(f"{c}=excluded.{c}" for c in _SCORE_COLS if c != "zoro_no")
                conn.execute(
                    f"INSERT INTO pl_candidates ({cols}) VALUES ({marks}) "
                    f"ON CONFLICT(zoro_no) DO UPDATE SET {sets}, "
                    # only a row with no listing on disk goes back to being a plain candidate
                    f"status=CASE WHEN built_at IS NULL THEN 'candidate' ELSE status END",
                    vals)
            if seen:
                marks = ",".join("?" * len(seen))
                conn.execute(
                    f"UPDATE pl_candidates SET status='dropped' "
                    f"WHERE zoro_no NOT IN ({marks}) AND status IN ('candidate','dropped')",
                    list(seen))
        return len(rows)
    finally:
        conn.close()


def mark_built(zoro_no, sku, title, image_ok, path=DB):
    conn = connect(path)
    try:
        with conn:
            conn.execute("UPDATE pl_candidates SET sku=?, listing_title=?, image_ok=?, "
                         "built_at=?, status='built' WHERE zoro_no=?",
                         (sku, title, 1 if image_ok else 0,
                          datetime.now().isoformat(timespec="seconds"), zoro_no))
    finally:
        conn.close()


def _f(v):
    try:
        return round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
