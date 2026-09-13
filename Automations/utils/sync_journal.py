#!/usr/bin/env python3
"""
sync_journal.py — keep two (or more) machines' copies of automations_unified.db in step
through Cloudflare R2, so closing can run on one computer and placers on another and the
dashboard (fed by the MASTER machine) still sees everything.

HOW IT WORKS
  • install(db)   puts AFTER INSERT / AFTER UPDATE triggers on the tables listed in TABLES.
                  Every local write appends one row to `sync_journal` with the NEW row (and
                  the OLD row for updates) as JSON. Nothing is ever deleted through sync.
  • push(db)      uploads pending journal rows as gzip JSON batches to
                  R2  sync/events/<host>/<utc>_<seq0>-<seq1>.json.gz  and deletes them locally.
  • pull(db)      downloads every batch written by OTHER hosts (once — receipts in
                  `sync_applied`) and applies it:
                    INSERT  → row missing here: inserted; row present: only EMPTY columns
                              are filled (never overwrite a value we already have);
                    UPDATE  → only the columns that actually CHANGED on the other machine
                              (NEW ≠ OLD) are written — so closing's tracking columns and the
                              placer's placement columns of the same `orders` row never fight;
                    append-only tables (no natural key) → inserted once, deduped by row hash.
                  Rows are matched by their NATURAL KEY (amazon_order_id, wb_order_id …), never
                  by the autoincrement id, which differs per machine.
  • echo safety   the applier writes inside BEGIN IMMEDIATE, remembers max(seq) first and
                  deletes the journal rows its own writes produced before COMMIT. SQLite
                  serialises writers, so those rows are exactly its own — a change never
                  bounces back to the machine it came from.

WHAT DOES NOT SYNC (by design): files (order_log.txt, run_*.json, console logs, Chrome
profiles, tokens), the placement lease (R2 already arbitrates who may place), and schema —
both machines must run the same code (the applier only writes columns that exist locally
and logs the rest).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

JOURNAL = "sync_journal"
APPLIED = "sync_applied"
ROWS = "sync_rows"
PREFIX = "sync/events/"

# table → natural key columns; None → append-only, deduped by a hash of the row (minus id)
TABLES: dict[str, list[str] | None] = {
    "orders": ["amazon_order_id"],
    "closing_orders": ["amazon_order_id"],
    "order_placer_orders": ["amazon_order_id", "wb_order_id"],
    "placer_claims": ["amazon_order_id"],
    "order_crm": ["amazon_order_id"],
    "amazon_order_details": ["amazon_order_id"],
    "order_returns": ["amazon_order_id", "wb_order_id"],
    "order_feedback": ["amazon_order_id"],
    "walmart_orders": ["po_number"],
    "closing_run_log": ["run_id"],
    "fedex_tracking_cache": ["tracking_id"],
    "archive_tracking": ["wb_order_id", "tracking_ids", "email_timestamp", "gmail_account"],
    "archive_messages": ["amazon_order_id", "email_timestamp", "message_type", "gmail_account"],
    "archive_returns": ["amazon_order_id", "email_timestamp", "email_type", "gmail_account"],
    "archive_earnings": ["amazon_order_id", "gmail_account", "email_timestamp"],
    "archive_zoro": None,
    "enrichment_log": None,
    "order_messages": None,
    "bot_decisions": None,
    "crm_notes": None,
    "crm_history": None,
}
SKIP_COLS = {"id", "rowid"}          # per-machine autoincrement ids never travel
JSON_CHUNK = 60                      # json_object() pairs per call (older SQLite caps args at 127)


# ── helpers ───────────────────────────────────────────────────────────────
def _connect(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(db_path, timeout=60)
    c.execute("PRAGMA busy_timeout=60000")
    return c


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")]


def _json_expr(prefix: str, cols: list[str]) -> str:
    """json_object(...) of all columns, chunked and merged with json_patch."""
    chunks = []
    for i in range(0, len(cols), JSON_CHUNK):
        part = cols[i:i + JSON_CHUNK]
        chunks.append("json_object(" + ", ".join(f"'{c}', {prefix}.\"{c}\"" for c in part) + ")")
    expr = chunks[0]
    for ch in chunks[1:]:
        expr = f"json_patch({expr}, {ch})"
    return expr


def _row_hash(row: dict) -> str:
    items = sorted((k, v) for k, v in row.items() if k not in SKIP_COLS)
    return hashlib.sha1(json.dumps(items, sort_keys=True, default=str).encode()).hexdigest()


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def utcnow_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


# ── install ───────────────────────────────────────────────────────────────
def install(db_path: str) -> dict:
    """Create the journal tables and (re)create the triggers for every synced table that
    exists in this database. Idempotent; re-run after a schema change (triggers enumerate
    columns at creation time)."""
    conn = _connect(db_path)
    try:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {JOURNAL}(seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                     f"table_name TEXT NOT NULL, op TEXT NOT NULL, new_json TEXT, old_json TEXT, ts REAL)")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {APPLIED}(event_key TEXT PRIMARY KEY, applied_at REAL, "
                     f"events INTEGER, errors INTEGER, host TEXT)")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {ROWS}(table_name TEXT NOT NULL, row_hash TEXT NOT NULL, "
                     f"PRIMARY KEY(table_name, row_hash))")
        present = _tables(conn)
        done, skipped = [], []
        for t in TABLES:
            for trg in (f"zs_ai_{t}", f"zs_au_{t}"):
                conn.execute(f"DROP TRIGGER IF EXISTS {trg}")
            if t not in present:
                skipped.append(t)
                continue
            cols = [c for c in _cols(conn, t) if c not in SKIP_COLS]
            if not cols:
                skipped.append(t)
                continue
            new_expr, old_expr = _json_expr("NEW", cols), _json_expr("OLD", cols)
            conn.execute(f"CREATE TRIGGER zs_ai_{t} AFTER INSERT ON \"{t}\" FOR EACH ROW BEGIN "
                         f"INSERT INTO {JOURNAL}(table_name, op, new_json, old_json, ts) "
                         f"VALUES ('{t}', 'I', {new_expr}, NULL, (julianday('now') - 2440587.5) * 86400.0); END")
            conn.execute(f"CREATE TRIGGER zs_au_{t} AFTER UPDATE ON \"{t}\" FOR EACH ROW BEGIN "
                         f"INSERT INTO {JOURNAL}(table_name, op, new_json, old_json, ts) "
                         f"VALUES ('{t}', 'U', {new_expr}, {old_expr}, (julianday('now') - 2440587.5) * 86400.0); END")
            done.append(t)
        conn.commit()
        return {"tables": done, "skipped": skipped}
    finally:
        conn.close()


def uninstall(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        for t in TABLES:
            conn.execute(f"DROP TRIGGER IF EXISTS zs_ai_{t}")
            conn.execute(f"DROP TRIGGER IF EXISTS zs_au_{t}")
        conn.commit()
    finally:
        conn.close()


def pending_count(db_path: str) -> int:
    conn = _connect(db_path)
    try:
        if JOURNAL not in _tables(conn):
            return 0
        return conn.execute(f"SELECT COUNT(*) FROM {JOURNAL}").fetchone()[0]
    except Exception:
        return 0
    finally:
        conn.close()


# ── push ──────────────────────────────────────────────────────────────────
def push(db_path: str, store, host: str, batch: int = 2000, max_batches: int = 20) -> dict:
    """Upload pending journal rows in batches; each batch is deleted locally only after
    R2 confirmed the upload."""
    files = events = 0
    conn = _connect(db_path)
    try:
        if JOURNAL not in _tables(conn):
            return {"files": 0, "events": 0}
        for _ in range(max_batches):
            rows = conn.execute(f"SELECT seq, table_name, op, new_json, old_json, ts FROM {JOURNAL} "
                                f"ORDER BY seq LIMIT ?", (batch,)).fetchall()
            if not rows:
                break
            evs = [{"seq": r[0], "table": r[1], "op": r[2], "new": json.loads(r[3]) if r[3] else None,
                    "old": json.loads(r[4]) if r[4] else None, "ts": r[5]} for r in rows]
            body = gzip.compress(json.dumps({"host": host, "created": time.time(), "events": evs},
                                            default=str).encode("utf-8"))
            key = f"{PREFIX}{host}/{utcnow_key()}_{rows[0][0]:012d}-{rows[-1][0]:012d}.json.gz"
            store.put_bytes(key, body, content_type="application/gzip")
            conn.execute(f"DELETE FROM {JOURNAL} WHERE seq <= ?", (rows[-1][0],))
            conn.commit()
            files += 1
            events += len(evs)
            if len(rows) < batch:
                break
        return {"files": files, "events": events}
    finally:
        conn.close()


# ── pull / apply ──────────────────────────────────────────────────────────
def _key_of(item) -> str:
    if isinstance(item, str):
        return item
    return item.get("key") or item.get("Key") or ""


def pull(db_path: str, store, host: str, max_files: int = 40) -> dict:
    """Apply every batch other hosts uploaded and we have not applied yet."""
    stats = {"files": 0, "events": 0, "errors": 0, "skipped_cols": {}}
    conn = _connect(db_path)
    try:
        if JOURNAL not in _tables(conn):
            install(db_path)
        keys = sorted(k for k in (_key_of(i) for i in store.list(PREFIX, max_keys=1000))
                      if k and not k.startswith(f"{PREFIX}{host}/"))
        if not keys:
            return stats
        applied = {r[0] for r in conn.execute(f"SELECT event_key FROM {APPLIED}")}
        todo = [k for k in keys if k not in applied][:max_files]
        cols_cache: dict[str, list[str]] = {}
        present = _tables(conn)
        for key in todo:
            raw = store.get_bytes(key)
            if raw is None:
                continue
            try:
                doc = json.loads(gzip.decompress(raw).decode("utf-8"))
            except Exception:
                doc = json.loads(raw.decode("utf-8", "replace"))
            evs = doc.get("events") or []
            errs = 0
            conn.execute("BEGIN IMMEDIATE")                   # write lock: nobody else journals now
            seq0 = conn.execute(f"SELECT COALESCE(MAX(seq), 0) FROM {JOURNAL}").fetchone()[0]
            try:
                for ev in evs:
                    try:
                        _apply(conn, ev, cols_cache, present, stats)
                    except Exception as e:
                        errs += 1
                        if errs <= 3:
                            print(f"  ⚠️ sync apply {ev.get('table')}: {str(e)[:120]}")
                conn.execute(f"DELETE FROM {JOURNAL} WHERE seq > ?", (seq0,))   # our own echo rows
                conn.execute(f"INSERT OR REPLACE INTO {APPLIED}(event_key, applied_at, events, errors, host) "
                             f"VALUES (?,?,?,?,?)", (key, time.time(), len(evs), errs, doc.get("host")))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            stats["files"] += 1
            stats["events"] += len(evs)
            stats["errors"] += errs
        return stats
    finally:
        conn.close()


def _apply(conn, ev: dict, cols_cache: dict, present: set, stats: dict) -> None:
    t = ev.get("table")
    new = ev.get("new") or {}
    old = ev.get("old")
    if t not in present or t not in TABLES or not new:
        return
    if t not in cols_cache:
        cols_cache[t] = _cols(conn, t)
    local = cols_cache[t]
    dropped = [k for k in new if k not in local]
    if dropped:
        stats["skipped_cols"].setdefault(t, set()).update(dropped)
    new = {k: v for k, v in new.items() if k in local and k not in SKIP_COLS}
    keycols = TABLES[t]
    if keycols and all(k in new and not _empty(new[k]) for k in keycols):
        where = " AND ".join(f"\"{k}\" = ?" for k in keycols)
        args = [new[k] for k in keycols]
        row = conn.execute(f"SELECT rowid FROM \"{t}\" WHERE {where} ORDER BY rowid DESC LIMIT 1", args).fetchone()
        if row is None:
            _insert(conn, t, new)
            return
        rid = row[0]
        if ev.get("op") == "U" and isinstance(old, dict):
            changed = {k: v for k, v in new.items() if old.get(k) != v}
        else:
            # an INSERT that already exists here (both machines saw the order): fill gaps only
            cur = conn.execute(f"SELECT * FROM \"{t}\" WHERE rowid = ?", (rid,)).fetchone()
            names = [d[0] for d in conn.execute(f"SELECT * FROM \"{t}\" WHERE 0").description]
            curd = dict(zip(names, cur))
            changed = {k: v for k, v in new.items() if _empty(curd.get(k)) and not _empty(v)}
        if changed:
            sets = ", ".join(f"\"{k}\" = ?" for k in changed)
            conn.execute(f"UPDATE \"{t}\" SET {sets} WHERE rowid = ?", [*changed.values(), rid])
        return
    # append-only (or key incomplete): insert once, deduped by content hash
    h = _row_hash(new)
    if conn.execute(f"SELECT 1 FROM {ROWS} WHERE table_name = ? AND row_hash = ?", (t, h)).fetchone():
        return
    if ev.get("op") == "U":
        # an update to a row we cannot address by key: keep the latest version as a new row
        # would duplicate it, so skip — such tables should carry a key in TABLES.
        return
    _insert(conn, t, new)
    conn.execute(f"INSERT OR IGNORE INTO {ROWS}(table_name, row_hash) VALUES (?, ?)", (t, h))


def _insert(conn, t: str, row: dict) -> None:
    cols = list(row.keys())
    conn.execute(f"INSERT INTO \"{t}\" ({', '.join(chr(34) + c + chr(34) for c in cols)}) "
                 f"VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])


# ── housekeeping ──────────────────────────────────────────────────────────
def prune_remote(store, days: int = 14) -> int:
    """Delete event batches older than `days` (their timestamp is in the key)."""
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    old = []
    for item in store.list(PREFIX, max_keys=1000):
        k = _key_of(item)
        try:
            stamp = os.path.basename(k).split("_")[0]
            ts = datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).timestamp()
            if ts < cutoff:
                old.append(k)
        except Exception:
            continue
    for k in old:
        store.delete(k)
    return len(old)


def seed_from_cloud(db_path: str, store, slim_key: str = "db/dashboard.db.gz") -> str:
    """First start on a NEW machine: download the slim DB as the local database. Refuses
    to touch an existing database."""
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        raise RuntimeError(f"{db_path} already exists — not overwriting a database")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    tmp = db_path + ".seed"
    if not store.get_file(slim_key, tmp, gunzip=True):
        raise RuntimeError("slim DB not found in R2")
    os.replace(tmp, db_path)
    return db_path


def summary(db_path: str) -> dict:
    conn = _connect(db_path)
    try:
        t = _tables(conn)
        return {"installed": JOURNAL in t and any(r[0].startswith("zs_ai_") for r in
                                                  conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")),
                "pending": conn.execute(f"SELECT COUNT(*) FROM {JOURNAL}").fetchone()[0] if JOURNAL in t else 0,
                "applied_files": conn.execute(f"SELECT COUNT(*) FROM {APPLIED}").fetchone()[0] if APPLIED in t else 0}
    finally:
        conn.close()
