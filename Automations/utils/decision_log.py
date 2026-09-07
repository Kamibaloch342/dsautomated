"""
utils/decision_log.py — a structured record of every routing decision the placers make.

WHY: "is the supervisor doing the right thing?" has to be answerable from data, not
by scrolling console output. Every decision is written twice:

  • data/decisions/YYYY-MM-DD.jsonl    one JSON object per line with EVERYTHING —
                                        inputs, every coupon attempt and Zoro's reply,
                                        the verdict, the reason (open it in any editor)
  • table bot_decisions                 the same record flattened for SQL and for the
    (automations_unified.db)            dashboard; it rides to the cloud dashboard in
                                        the slim DB like every other table

Kinds:
  supervisor  coupon profile decided an unlabelled order (place / reseller / wait / retry)
  deciding    reseller profile decided a Deciding-ASIN order (route_to_coupon / place_here)
  pdp         the product page carried the "not eligible for any other offers" badge
  route       a label was written for the sibling profile (MR / Rajar / AB ECOM)
  learn       promo-ineligible memory changed (strike / ineligible / cleared)
  sheet       a verdict was written into Priority_ASIN_Control (moved or not)
  check       the priority checker tested an ASIN (scheduled, on demand or re-check queue)
  outcome     what the checkout finally did with a supervised / deciding order

Never raises: a logging failure must never stop a placement.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
AUTOMATIONS_DIR = os.path.dirname(_HERE)
DIR = os.path.join(AUTOMATIONS_DIR, "data", "decisions")
DB_PATH = os.path.join(AUTOMATIONS_DIR, "data", "automations_unified.db")
TABLE = "bot_decisions"

_DDL = (
    f"CREATE TABLE IF NOT EXISTS {TABLE}("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, host TEXT, profile TEXT, kind TEXT NOT NULL, "
    "amazon_id TEXT, asin TEXT, sku TEXT, action TEXT, reason TEXT, coupon TEXT, tier TEXT, "
    "cart REAL, unit_price REAL, qty INTEGER, earnings REAL, margin REAL, zoro_select INTEGER, "
    "pdp_ineligible INTEGER, verdict_status TEXT, verdict_suggest TEXT, attempts INTEGER, data TEXT)"
)
_IDX = (f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_ts ON {TABLE}(ts)",
        f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_order ON {TABLE}(amazon_id)",
        f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_asin ON {TABLE}(asin)",
        f"CREATE INDEX IF NOT EXISTS ix_{TABLE}_kind ON {TABLE}(kind, ts)")
_FLAT = ("profile", "amazon_id", "asin", "sku", "action", "reason", "coupon", "tier", "cart", "unit_price",
         "qty", "earnings", "margin", "zoro_select", "pdp_ineligible", "verdict_status", "verdict_suggest", "attempts")

_lock = threading.Lock()
_schema_ready = False
_host_cache: str | None = None


def _host() -> str:
    global _host_cache
    if _host_cache is None:
        try:
            from utils.r2_store import machine_id
            _host_cache = machine_id()
        except Exception:
            _host_cache = (socket.gethostname() or "unknown").split(".")[0]
    return _host_cache


def _num(v):
    try:
        return None if v is None or v == "" else float(v)
    except Exception:
        return None


def _flatten(rec: dict) -> dict:
    v = rec.get("verdict") or {}
    out = {
        "profile": rec.get("profile"), "amazon_id": rec.get("amazon_id"), "asin": rec.get("asin"),
        "sku": rec.get("sku"), "action": rec.get("action"), "reason": (rec.get("reason") or "")[:500],
        "coupon": rec.get("coupon"), "tier": rec.get("tier"), "cart": _num(rec.get("cart")),
        "unit_price": _num(rec.get("unit_price")), "qty": None, "earnings": _num(rec.get("earnings")),
        "margin": _num(rec.get("margin")),
        "zoro_select": None if rec.get("zoro_select") is None else int(bool(rec.get("zoro_select"))),
        "pdp_ineligible": None if rec.get("pdp_ineligible") is None else int(bool(rec.get("pdp_ineligible"))),
        "verdict_status": v.get("status") if isinstance(v, dict) else None,
        "verdict_suggest": v.get("suggest") if isinstance(v, dict) else None,
        "attempts": len(rec.get("attempts") or []) if isinstance(rec.get("attempts"), list) else None,
    }
    try:
        out["qty"] = int(float(str(rec.get("qty")).split(",")[0])) if rec.get("qty") not in (None, "") else None
    except Exception:
        out["qty"] = None
    return out


def log(kind: str, **fields) -> dict:
    """Record one decision. Returns the record (handy for tests). Never raises."""
    rec = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "host": _host(), "kind": kind}
    rec.update(fields)
    line = json.dumps(rec, ensure_ascii=False, default=str)
    # 1) daily JSONL — the complete record
    try:
        os.makedirs(DIR, exist_ok=True)
        with _lock, open(os.path.join(DIR, rec["ts"][:10] + ".jsonl"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    # 2) DB row — flattened + the full JSON in `data`
    global _schema_ready
    try:
        c = sqlite3.connect(DB_PATH, timeout=3)
        try:
            c.execute("PRAGMA busy_timeout=3000")
            c.execute("PRAGMA journal_mode=WAL")
            if not _schema_ready:
                c.execute(_DDL)
                for s in _IDX:
                    c.execute(s)
                _schema_ready = True
            flat = _flatten(rec)
            cols = ["ts", "host", "kind"] + list(_FLAT) + ["data"]
            vals = [rec["ts"], rec["host"], kind] + [flat[k] for k in _FLAT] + [line]
            c.execute(f"INSERT INTO {TABLE}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)
            c.commit()
        finally:
            c.close()
    except Exception:
        pass
    return rec


def read(db_path: str | None = None, date: str | None = None, kind: str | None = None, profile: str | None = None,
         asin: str | None = None, amazon_id: str | None = None, action: str | None = None,
         since: str | None = None, limit: int = 500) -> list[dict]:
    """Rows from bot_decisions (newest first) with the full JSON expanded into `data`."""
    p = db_path or DB_PATH
    if not os.path.exists(p):
        return []
    where, args = [], []
    if date:
        where.append("ts LIKE ?"); args.append(f"{date}%")
    if since:
        where.append("ts >= ?"); args.append(since)
    for col, val in (("kind", kind), ("profile", profile), ("asin", asin), ("amazon_id", amazon_id), ("action", action)):
        if val:
            where.append(f"{col} = ?"); args.append(val)
    sql = f"SELECT * FROM {TABLE}" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA busy_timeout=5000")
            rows = []
            for r in c.execute(sql, args):
                d = dict(r)
                try:
                    d["data"] = json.loads(d.get("data") or "{}")
                except Exception:
                    d["data"] = {}
                rows.append(d)
            return rows
        finally:
            c.close()
    except sqlite3.OperationalError:
        return []


def read_jsonl(date: str | None = None) -> list[dict]:
    """All records of one day straight from the JSONL file (no DB needed)."""
    date = date or datetime.now().strftime("%Y-%m-%d")
    p = os.path.join(DIR, f"{date}.jsonl")
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        out.append(json.loads(ln))
                    except Exception:
                        continue
    except FileNotFoundError:
        pass
    return out
