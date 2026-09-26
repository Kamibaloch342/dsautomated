#!/usr/bin/env python3
"""
slim_database.py — shrink data/automations_unified.db (1.6 GB → ~100 MB) SAFELY.

What bloats it (see docs): every archive_* table carries a raw_body_snippet
column (~500 MB of email bodies that ALSO live in data/gmail_archive.db), and
enrichment_log holds 1.76 M rows (+154 MB of indexes) nobody reads after 30 days.

This script, in order:
  0. refuses to run while placers / closing / the dashboard supervisor hold the DB
     open for writing? — no: SQLite handles that; instead it takes a BACKUP first
     (rule 8): data/automations_unified.db.bak.YYYYMMDD_HHMM via the online
     backup API (consistent even with other readers).
  1. verifies gmail_archive.db still has the raw bodies for every archive table
     (so nothing is lost by dropping the copies).
  2. rebuilds each archive_* table WITHOUT raw_body_snippet (SQLite has no cheap
     DROP COLUMN for indexed tables: CREATE new AS SELECT → DROP → RENAME, then
     re-creates the indexes).
  3. purges enrichment_log older than --keep-enrichment-days (30) and audit_log
     older than --keep-audit-days (60).
  4. VACUUMs and prints before/after sizes.

DRY RUN by default — it only reports what it would do. Add --apply to do it.
Stop the launcher (placers, closing) before --apply: VACUUM needs the file to
itself for a few minutes; the dashboard can stay up (it only reads).

The backup goes to --backup-dir (default ~/.zuhra/backups — OUTSIDE the Google-Drive-
mirrored project folder), and the run ends with PRAGMA wal_checkpoint(TRUNCATE) so the
-wal file (4 GB on 26 Sep 2026) is reset as well. Since 26 Sep 2026 the archive sync no
longer copies raw bodies and closing/reconcile read the derived columns, so dropping the
column costs nothing downstream.

    python scripts/slim_database.py               # report
    python scripts/slim_database.py --apply       # do it (backup first, automatic)
    python scripts/slim_database.py --apply --keep-enrichment-days=7 --backup-dir=/Volumes/X
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
DATA = os.path.join(AUTO, "data")
DB = os.path.join(DATA, "automations_unified.db")
ARCHIVE = os.path.join(DATA, "gmail_archive.db")
ARCHIVE_TABLES = ["archive_earnings", "archive_zoro", "archive_tracking", "archive_returns", "archive_messages"]
GMAIL_SOURCE = {"archive_earnings": "earnings_emails", "archive_zoro": "zoro_emails",
                "archive_tracking": "tracking_emails", "archive_returns": "return_emails",
                "archive_messages": "message_emails"}


def mb(path: str) -> float:
    return os.path.getsize(path) / 1048576 if os.path.exists(path) else 0.0


def main() -> None:
    apply = "--apply" in sys.argv
    keep_enr = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--keep-enrichment-days=")), 30))
    keep_aud = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--keep-audit-days=")), 60))
    if not os.path.exists(DB):
        print(f"❌ {DB} not found")
        sys.exit(1)
    print(f"DB: {DB}  ({mb(DB):.0f} MB)   mode: {'APPLY' if apply else 'DRY RUN'}")

    c = sqlite3.connect(DB, timeout=60)
    c.execute("PRAGMA busy_timeout=60000")
    plan = []
    # 0. the derived columns (promo / item / Zoro ETA) must exist for every row that still has
    #    a body — closing and reconcile read them instead of the body since 26 Sep 2026.
    try:
        sys.path.insert(0, AUTO)
        from scripts.sync_gmail_archive import pending_derivations
        pending = pending_derivations(c)
    except Exception as e:
        pending = {"error": str(e)}
    blocked = {t for t, n in pending.items() if isinstance(n, int) and n > 0}
    if blocked:
        print(f"  ⛔ derived columns pending on {', '.join(sorted(blocked))} ({pending}) — run\n"
              f"     python scripts/sync_gmail_archive.py --derive-existing\n"
              f"     first; those tables keep their raw bodies this run.")
    # 1. raw_body_snippet columns + gmail_archive safety check
    src = sqlite3.connect(f"file:{ARCHIVE}?mode=ro", uri=True) if os.path.exists(ARCHIVE) else None
    for t in ARCHIVE_TABLES:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
        if "raw_body_snippet" not in cols:
            print(f"  ✓ {t}: no raw_body_snippet (already slim)")
            continue
        if t in blocked:
            continue
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        raw_mb = (c.execute(f"SELECT COALESCE(SUM(LENGTH(raw_body_snippet)),0) FROM {t}").fetchone()[0] or 0) / 1048576
        ok_src = False
        if src is not None:
            st = GMAIL_SOURCE[t]
            try:
                scols = [r[1] for r in src.execute(f"PRAGMA table_info({st})")]
                sn = src.execute(f"SELECT COUNT(*) FROM {st}").fetchone()[0]
                ok_src = any("body" in x for x in scols) and sn >= n * 0.9
                print(f"  • {t}: {n:,} rows, raw bodies {raw_mb:.0f} MB — gmail_archive.{st} has {sn:,} rows "
                      f"{'✓ originals present' if ok_src else '⚠️ originals NOT verified'}")
            except Exception as e:
                print(f"  • {t}: {n:,} rows, raw bodies {raw_mb:.0f} MB — gmail_archive check failed: {e}")
        else:
            print(f"  • {t}: {n:,} rows, raw bodies {raw_mb:.0f} MB — ⚠️ gmail_archive.db missing")
        plan.append(("drop_col", t, ok_src))
    # 2. log purges
    for tbl, days, col in (("enrichment_log", keep_enr, None), ("audit_log", keep_aud, None)):
        try:
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({tbl})")]
            tcol = next((x for x in ("timestamp", "created_at", "logged_at", "ts", "changed_at") if x in cols), None)
            total = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            old = c.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {tcol} < datetime('now', ?)", (f"-{days} days",)).fetchone()[0] if tcol else 0
            print(f"  • {tbl}: {total:,} rows, {old:,} older than {days} days" + ("" if tcol else " (no timestamp column — skipped)"))
            if tcol:
                plan.append(("purge", tbl, (tcol, days)))
        except Exception as e:
            print(f"  • {tbl}: {e}")
    if not apply:
        print("\nDRY RUN — nothing changed. With --apply the raw bodies are OFFLOADED to "
              "data/archive_bodies.db (kept locally, never uploaded), then the columns are "
              "dropped, old log rows purged and the file VACUUMed. Stop the launcher first.")
        return

    # 0. backup (online backup API → consistent copy), outside the Drive-mirrored folder
    bdir = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--backup-dir=")),
                os.path.expanduser("~/.zuhra/backups"))
    try:
        os.makedirs(bdir, exist_ok=True)
        if not os.access(bdir, os.W_OK):
            raise OSError("not writable")
    except OSError as e:
        print(f"⚠️ backup dir {bdir} unusable ({e}) — falling back to {DATA}")
        bdir = DATA
    bak = os.path.join(bdir, "automations_unified.db.bak." + datetime.now().strftime("%Y%m%d_%H%M"))
    free = shutil.disk_usage(bdir).free / 1048576
    if free < mb(DB) * 2.2:
        print(f"❌ not enough free disk for backup + vacuum ({free:.0f} MB free)")
        sys.exit(1)
    print(f"\n💾 backup → {bak}")
    dst = sqlite3.connect(bak)
    c.backup(dst)
    dst.close()
    print(f"   backup done ({mb(bak):.0f} MB)")

    t0 = time.time()
    # Raw bodies are OFFLOADED, never deleted: gmail_archive.db turned out to hold far
    # fewer rows than the unified archive tables, so every body is copied into
    # data/archive_bodies.db (never synced, never uploaded) before the column goes.
    BODIES = os.path.join(DATA, "archive_bodies.db")
    c.execute("ATTACH DATABASE ? AS bodies", (BODIES,))
    for kind, t, info in plan:
        if kind == "drop_col":
            allcols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
            keys = [k for k in ("id", "amazon_order_id", "wb_order_id", "gmail_message_id", "message_id",
                                "email_timestamp", "gmail_account") if k in allcols]
            sel = ", ".join(["rowid AS src_rowid"] + keys + ["raw_body_snippet"])
            print(f"  📤 {t}: offloading raw bodies → {os.path.basename(BODIES)}.{t}_bodies")
            c.execute(f"DROP TABLE IF EXISTS bodies.{t}_bodies")
            c.execute(f"CREATE TABLE bodies.{t}_bodies AS SELECT {sel} FROM {t}")
            n_src = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            n_dst = c.execute(f"SELECT COUNT(*) FROM bodies.{t}_bodies").fetchone()[0]
            c.commit()
            if n_dst != n_src:
                print(f"  ⛔ {t}: offload count mismatch ({n_dst:,} ≠ {n_src:,}) — keeping the column")
                continue
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})") if r[1] != "raw_body_snippet"]
            create = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
            # strip the column definition from the CREATE statement
            lines = [ln for ln in create.splitlines() if "raw_body_snippet" not in ln]
            new_sql = "\n".join(lines).replace(f"CREATE TABLE {t}", f"CREATE TABLE {t}__new", 1)
            new_sql = new_sql.replace(f"CREATE TABLE IF NOT EXISTS {t}", f"CREATE TABLE {t}__new", 1)
            # fix a dangling comma before the closing paren
            new_sql = new_sql.rstrip().rstrip(")").rstrip().rstrip(",") + "\n)"
            idx = [r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (t,))]
            print(f"  🔧 {t}: rebuilding without raw_body_snippet ({len(idx)} index(es))")
            c.execute("BEGIN")
            c.execute(f"DROP TABLE IF EXISTS {t}__new")
            c.execute(new_sql)
            c.execute(f"INSERT INTO {t}__new ({','.join(cols)}) SELECT {','.join(cols)} FROM {t}")
            c.execute(f"DROP TABLE {t}")
            c.execute(f"ALTER TABLE {t}__new RENAME TO {t}")
            for isql in idx:
                try:
                    c.execute(isql)
                except Exception as e:
                    print(f"     ⚠️ index: {e}")
            c.commit()
        elif kind == "purge":
            tcol, days = info
            cur = c.execute(f"DELETE FROM {t} WHERE {tcol} < datetime('now', ?)", (f"-{days} days",))
            c.commit()
            print(f"  🧹 {t}: deleted {cur.rowcount:,} rows older than {days} days")
    c.execute("DETACH DATABASE bodies")
    print("  🧽 VACUUM (this takes a few minutes)…")
    c.execute("VACUUM")
    try:
        r = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        print(f"  🧹 WAL checkpoint(TRUNCATE): busy={r[0]} pages={r[1]} checkpointed={r[2]}")
    except Exception as e:
        print(f"  ⚠️ wal_checkpoint: {e}")
    c.close()
    wal = mb(DB + "-wal")
    print(f"\n✅ done in {time.time() - t0:.0f}s: {mb(bak):.0f} MB → {mb(DB):.0f} MB (+ WAL {wal:.0f} MB)   "
          f"(backup kept at {bak}; raw bodies in {BODIES} = {mb(BODIES):.0f} MB)")


if __name__ == "__main__":
    main()
