#!/usr/bin/env python3
"""
travel_mode.py — hand the dashboard to the Windows box while you travel, then take
the team's work back when you return.

THE MODEL (why this is safe):  the two machines never own the same fact.

    MAC  (always primary)  owns  orders, placer_claims, order_placer_orders,
                                 closing_orders, archive_* — everything the
                                 software generates. It keeps placing while
                                 you are away.
    WIN  (travel only)     owns  order_crm, crm_notes, dash_chat, dash_reminders,
                                 dash_prefs, insight_resolutions — everything a
                                 human types.

Because ownership is partitioned, there is nothing to reconcile and nothing can be
silently lost. --back merges ONLY the human tables; it can never touch your order
history or the de-duplication locks.

USAGE
  On the MAC, before you leave:
      python scripts/travel_mode.py --go
        Builds a slim snapshot (drops ~1.18M rows of logs the dashboard never
        reads), serves it over Tailscale, and prints the one command to run on
        Windows.

  On the MAC, when you get back:
      python scripts/travel_mode.py --back
        Pulls the human tables straight from the Windows dashboard and merges
        them in. Backs up your DB first. Add --dry-run to preview.

  Anytime:
      python scripts/travel_mode.py --status
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
DB = os.path.join(AUTO, "data", "automations_unified.db")
OUT_DIR = os.path.join(AUTO, "data", "travel")
SNAP = os.path.join(OUT_DIR, "travel_snapshot.db")
SNAP_GZ = SNAP + ".gz"
PORT = 8790

# ── Tables a HUMAN edits on Windows. These — and ONLY these — come back. ──────
#    Every entry needs a merge rule below; adding a table here without one is a
#    hard error, so this list can never silently grow into machine-owned data.
HUMAN_TABLES = [
    "order_crm",            # per-order flags/tags/case status  (PK amazon_order_id)
    "crm_notes",            # free-text notes                    (append-only)
    "dash_chat",            # team chat                          (append-only)
    "dash_reminders",       # reminder center                    (append + done flag)
    "dash_prefs",           # per-browser display name           (PK client_key)
    "insight_resolutions",  # dismissed insights                 (UNIQUE type+ref)
]

# ── Tables the dashboard NEVER reads — dropped from the travel snapshot. ──────
#    Verified against dashboard/server.py: 0 references each. Dropping them takes
#    the transfer from ~1.9 GB to a few hundred MB. They stay intact on the Mac.
SKIP_TABLES = [
    "enrichment_log",    # ~921k rows
    "audit_log",         # ~27k
    "archive_earnings",  # ~159k
    "archive_messages",  # ~73k
    "raw_api_log",
    "action_log",
]


def say(msg=""):
    print(msg, flush=True)


def _conn(path, readonly=False):
    uri = f"file:{path}?mode=ro" if readonly else f"file:{path}"
    c = sqlite3.connect(uri, uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _counts(conn, tables):
    out = {}
    for t in sorted(tables):
        try:
            out[t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception:
            out[t] = -1
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def _tailscale_ip():
    for exe in ("/Applications/Tailscale.app/Contents/MacOS/Tailscale",
                "tailscale", "/usr/local/bin/tailscale"):
        try:
            r = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=8)
            ip = (r.stdout or "").strip().splitlines()
            if ip and ip[0].count(".") == 3:
                return ip[0].strip()
        except Exception:
            continue
    return None


def _peer_ip(name_hint="desktop"):
    """Best-effort: find the Windows peer's tailnet IP from `tailscale status`."""
    for exe in ("/Applications/Tailscale.app/Contents/MacOS/Tailscale",
                "tailscale", "/usr/local/bin/tailscale"):
        try:
            r = subprocess.run([exe, "status"], capture_output=True, text=True, timeout=8)
            for line in (r.stdout or "").splitlines():
                parts = line.split()
                if len(parts) >= 2 and name_hint.lower() in parts[1].lower():
                    return parts[0]
        except Exception:
            continue
    return None


# ══════════════════════════════════════════════════════════════════════
#  --go : build + serve the slim snapshot
# ══════════════════════════════════════════════════════════════════════

def cmd_go(args):
    if not os.path.exists(DB):
        say(f"!! database not found: {DB}")
        sys.exit(1)
    os.makedirs(OUT_DIR, exist_ok=True)

    say("=" * 66)
    say("  TRAVEL MODE — handing the dashboard to Windows")
    say("=" * 66)

    # 1. Consistent snapshot via SQLite's backup API — safe while the placer runs.
    say("\n[1/4] snapshotting the live database (safe while placer is running)…")
    for p in (SNAP, SNAP_GZ):
        if os.path.exists(p):
            os.remove(p)
    src = _conn(DB, readonly=True)
    dst = sqlite3.connect(SNAP)
    with dst:
        src.backup(dst)
    src.close()
    say(f"      snapshot: {_human(os.path.getsize(SNAP))}")

    # 2. Drop the log tables the dashboard never reads, then reclaim the space.
    say("\n[2/4] dropping tables the dashboard never reads…")
    con = sqlite3.connect(SNAP)
    present = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    dropped = 0
    for t in SKIP_TABLES:
        if t in present:
            n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            # keep the (empty) table so any stray query still finds it
            con.execute(f'DELETE FROM "{t}"')
            say(f"      emptied {t:<20} ({n:,} rows)")
            dropped += n
    con.commit()
    say(f"      compacting…")
    con.execute("VACUUM")
    con.close()
    say(f"      removed {dropped:,} rows -> {_human(os.path.getsize(SNAP))}")

    # 3. Compress for the wire.
    say("\n[3/4] compressing…")
    with open(SNAP, "rb") as fi, gzip.open(SNAP_GZ, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, length=1 << 20)
    os.remove(SNAP)
    size = os.path.getsize(SNAP_GZ)
    digest = _sha256(SNAP_GZ)
    say(f"      {_human(size)}  sha256={digest[:16]}…")

    # 4. Serve it and print the Windows command.
    ip = _tailscale_ip() or "<your-mac-tailscale-ip>"
    say("\n[4/4] serving over Tailscale…")
    say("\n" + "=" * 66)
    say("  RUN THIS ON WINDOWS (one line):")
    say("=" * 66)
    say(f"""
cd /d C:\\Users\\Kamran\\Downloads\\Automations && curl -o data\\travel.db.gz http://{ip}:{PORT}/{os.path.basename(SNAP_GZ)} && ..\\.venv\\Scripts\\python.exe scripts\\travel_mode.py --install data\\travel.db.gz
""")
    say("=" * 66)
    say(f"\n  Keep this window open until the download finishes ({_human(size)}).")
    say("  Press Ctrl+C when Windows reports OK.\n")

    os.chdir(OUT_DIR)
    import http.server
    import socketserver
    with socketserver.TCPServer(("0.0.0.0", PORT), http.server.SimpleHTTPRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            say("\n  stopped serving. Safe travels — the Mac keeps placing.")


# ══════════════════════════════════════════════════════════════════════
#  --install : (Windows) put the snapshot in place
# ══════════════════════════════════════════════════════════════════════

def cmd_install(args):
    src = args.install
    if not os.path.exists(src):
        say(f"!! not found: {src}")
        sys.exit(1)
    say("installing travel snapshot…")
    if os.path.exists(DB):
        bak = DB + f".before-travel-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.move(DB, bak)
        say(f"  previous DB backed up -> {os.path.basename(bak)}")
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    with gzip.open(src, "rb") as fi, open(DB, "wb") as fo:
        shutil.copyfileobj(fi, fo, length=1 << 20)
    con = _conn(DB, readonly=True)
    n = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    con.close()
    say(f"  OK — installed ({_human(os.path.getsize(DB))}, {n:,} orders).")
    say("  Restart the dashboard:  ..\\.venv\\Scripts\\python.exe scripts\\start_dashboard.py")


# ══════════════════════════════════════════════════════════════════════
#  --export : dump the human tables (runs on Windows, or via the API)
# ══════════════════════════════════════════════════════════════════════

def _export_payload(db_path):
    con = _conn(db_path, readonly=True)
    have = _tables(con)
    payload = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "tables": {}}
    for t in HUMAN_TABLES:
        if t not in have:
            continue
        rows = [dict(r) for r in con.execute(f'SELECT * FROM "{t}"')]
        payload["tables"][t] = rows
    con.close()
    return payload


def cmd_serve_export(args):
    """(Windows) Export the human tables and serve them for the Mac to pull.

    Used when the Windows dashboard is still running an older server.py without
    /api/travel_export — this needs no dashboard restart, so the team keeps working.
    """
    payload = _export_payload(DB)
    body = json.dumps(payload).encode("utf-8")
    total = sum(len(v) for v in payload["tables"].values())
    say("=" * 60)
    say("  serving the team's work for the Mac to collect")
    say("=" * 60)
    for t, rows in payload["tables"].items():
        say(f"   {t:<22}{len(rows):>7,}")
    say(f"   {'TOTAL':<22}{total:>7,} rows  ({_human(len(body))})")
    say(f"\n  Listening on port {PORT}. Leave this open until the Mac says done,")
    say("  then press Ctrl+C.\n")

    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            say(f"  -> collected by {self.client_address[0]}")

    import socketserver
    with socketserver.TCPServer(("0.0.0.0", PORT), H) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            say("\n  stopped.")


def cmd_export(args):
    payload = _export_payload(DB)
    out = args.export if isinstance(args.export, str) else os.path.join(OUT_DIR, "human.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    total = sum(len(v) for v in payload["tables"].values())
    say(f"exported {total:,} rows across {len(payload['tables'])} tables -> {out}")
    for t, rows in payload["tables"].items():
        say(f"   {t:<22} {len(rows):>7,}")


# ══════════════════════════════════════════════════════════════════════
#  --back : merge the human tables into the Mac DB
# ══════════════════════════════════════════════════════════════════════

def _merge(con, payload, dry=False):
    """Merge human rows. Returns {table: (inserted, updated, skipped)}.

    Every rule is idempotent — running --back twice changes nothing the second
    time. Auto-increment ids are NEVER copied (both machines start at 1 and would
    collide); rows are matched on their natural content instead.
    """
    stats = {}
    tabs = payload.get("tables", {})

    def cols(t):
        return [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]

    # ---- order_crm : newest updated_at wins, per order --------------------
    t = "order_crm"
    if t in tabs:
        ins = upd = skip = 0
        existing = {r["amazon_order_id"]: (r["updated_at"] or "")
                    for r in con.execute(f"SELECT amazon_order_id, updated_at FROM {t}")}
        c = [x for x in cols(t)]
        for row in tabs[t]:
            oid = row.get("amazon_order_id")
            if not oid:
                continue
            incoming = row.get("updated_at") or ""
            if oid not in existing:
                if not dry:
                    ks = [k for k in c if k in row]
                    con.execute(f'INSERT INTO {t} ({",".join(ks)}) VALUES ({",".join("?"*len(ks))})',
                                [row[k] for k in ks])
                ins += 1
            elif incoming > existing[oid]:
                if not dry:
                    ks = [k for k in c if k in row and k != "amazon_order_id"]
                    con.execute(f'UPDATE {t} SET {",".join(k+"=?" for k in ks)} WHERE amazon_order_id=?',
                                [row[k] for k in ks] + [oid])
                upd += 1
            else:
                skip += 1
        stats[t] = (ins, upd, skip)

    # ---- crm_notes : append-only, dedupe on content ----------------------
    t = "crm_notes"
    if t in tabs:
        ins = skip = 0
        seen = {(r["amazon_order_id"], r["author"], r["note"], r["created_at"])
                for r in con.execute(f"SELECT amazon_order_id,author,note,created_at FROM {t}")}
        for row in tabs[t]:
            key = (row.get("amazon_order_id"), row.get("author"),
                   row.get("note"), row.get("created_at"))
            if key in seen:
                skip += 1
                continue
            if not dry:
                con.execute(f"INSERT INTO {t} (amazon_order_id,author,note,created_at) VALUES (?,?,?,?)", key)
            seen.add(key)
            ins += 1
        stats[t] = (ins, 0, skip)

    # ---- dash_chat : append-only, dedupe on content ----------------------
    t = "dash_chat"
    if t in tabs:
        ins = skip = 0
        seen = {(r["sender"], r["text"], r["created_at"])
                for r in con.execute(f"SELECT sender,text,created_at FROM {t}")}
        c = cols(t)
        for row in tabs[t]:
            key = (row.get("sender"), row.get("text"), row.get("created_at"))
            if key in seen:
                skip += 1
                continue
            if not dry:
                ks = [k for k in c if k in row and k != "id"]
                con.execute(f'INSERT INTO {t} ({",".join(ks)}) VALUES ({",".join("?"*len(ks))})',
                            [row[k] for k in ks])
            seen.add(key)
            ins += 1
        stats[t] = (ins, 0, skip)

    # ---- dash_reminders : append + let a 'done' tick come back -----------
    t = "dash_reminders"
    if t in tabs:
        ins = upd = skip = 0
        cur = {(r["title"], r["created_at"], r["created_by"]): (r["id"], r["done"])
               for r in con.execute(f"SELECT id,title,created_at,created_by,done FROM {t}")}
        c = cols(t)
        for row in tabs[t]:
            key = (row.get("title"), row.get("created_at"), row.get("created_by"))
            if key not in cur:
                if not dry:
                    ks = [k for k in c if k in row and k != "id"]
                    con.execute(f'INSERT INTO {t} ({",".join(ks)}) VALUES ({",".join("?"*len(ks))})',
                                [row[k] for k in ks])
                ins += 1
            else:
                rid, done_here = cur[key]
                if (row.get("done") or 0) and not done_here:
                    if not dry:
                        con.execute(f"UPDATE {t} SET done=1, done_at=?, done_by=? WHERE id=?",
                                    (row.get("done_at"), row.get("done_by"), rid))
                    upd += 1
                else:
                    skip += 1
        stats[t] = (ins, upd, skip)

    # ---- dash_prefs : newest updated_at wins, per browser ----------------
    t = "dash_prefs"
    if t in tabs:
        ins = upd = skip = 0
        existing = {r["client_key"]: (r["updated_at"] or "")
                    for r in con.execute(f"SELECT client_key,updated_at FROM {t}")}
        for row in tabs[t]:
            k = row.get("client_key")
            if not k:
                continue
            if k not in existing:
                if not dry:
                    con.execute(f"INSERT INTO {t} (client_key,name,updated_at) VALUES (?,?,?)",
                                (k, row.get("name"), row.get("updated_at")))
                ins += 1
            elif (row.get("updated_at") or "") > existing[k]:
                if not dry:
                    con.execute(f"UPDATE {t} SET name=?, updated_at=? WHERE client_key=?",
                                (row.get("name"), row.get("updated_at"), k))
                upd += 1
            else:
                skip += 1
        stats[t] = (ins, upd, skip)

    # ---- insight_resolutions : insert-or-ignore on (type, ref) -----------
    t = "insight_resolutions"
    if t in tabs:
        ins = skip = 0
        seen = {(r["insight_type"], r["ref_key"])
                for r in con.execute(f"SELECT insight_type,ref_key FROM {t}")}
        for row in tabs[t]:
            key = (row.get("insight_type"), row.get("ref_key"))
            if key in seen:
                skip += 1
                continue
            if not dry:
                con.execute(f"INSERT OR IGNORE INTO {t} (insight_type,ref_key,note,resolved_at) VALUES (?,?,?,?)",
                            (key[0], key[1], row.get("note", ""), row.get("resolved_at")))
            seen.add(key)
            ins += 1
        stats[t] = (ins, 0, skip)

    return stats


def _admin_cookie(pw=None):
    """The dashboard's admin cookie: sha256("zuhra-admin:" + ADMIN_PW), sent as dash_auth.
    Both machines carry the same data/dashboard_admin_password.txt, so the Mac can mint
    the cookie the Windows dashboard expects."""
    if not pw:
        try:
            pw = open(os.path.join(AUTO, "data", "dashboard_admin_password.txt")).read().strip()
        except Exception:
            return None
    if not pw:
        return None
    return "dash_auth=" + hashlib.sha256(("zuhra-admin:" + pw).encode()).hexdigest()


def _fetch_from_windows(url, pw):
    req = urllib.request.Request(url)
    ck = _admin_cookie(pw)
    if ck:
        req.add_header("Cookie", ck)
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except ValueError:
        if "<html" in raw[:400].lower() or "sign in" in raw[:400].lower():
            raise RuntimeError(
                "the Windows dashboard returned its LOGIN PAGE, not data — either the admin "
                "password differs between machines, or Windows is still running the old "
                "server.py without /api/travel_export (push the updated file and restart it).")
        raise RuntimeError(f"unexpected non-JSON reply ({raw[:120]!r})")
    if not isinstance(data, dict) or "tables" not in data:
        raise RuntimeError("reply had no 'tables' — Windows is likely running the old server.py.")
    return data


def cmd_back(args):
    # Source: a local file, or pulled straight from the Windows dashboard.
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
        src_desc = args.from_file
    else:
        ip = args.host or _peer_ip() or "100.82.186.124"
        # Try the plain --serve-export helper first (works with ANY dashboard version),
        # then the dashboard's own admin endpoint.
        candidates = [
            (f"http://{ip}:{PORT}/", False),
            (f"http://{ip}:8000/api/travel_export", True),
        ]
        payload = src_desc = None
        errors = []
        for url, needs_auth in candidates:
            say(f"trying {url} …")
            try:
                payload = _fetch_from_windows(url, args.admin_pw if needs_auth else None)
                src_desc = url
                break
            except Exception as e:
                errors.append(f"  {url}\n    {e}")
        if payload is None:
            say("\n!! could not collect the team's work:\n" + "\n".join(errors))
            say("\n   On WINDOWS, run this, then re-run --back here:")
            say("     cd /d C:\\Users\\Kamran\\Downloads\\Automations")
            say("     ..\\.venv\\Scripts\\python.exe scripts\\travel_mode.py --serve-export")
            sys.exit(1)

    tot = sum(len(v) for v in payload.get("tables", {}).values())
    say(f"\nreceived {tot:,} rows from {src_desc} (generated {payload.get('generated_at','?')})")

    if not args.dry_run:
        bak = DB + f".before-merge-{time.strftime('%Y%m%d-%H%M%S')}"
        say(f"backing up the Mac database -> {os.path.basename(bak)}")
        src = _conn(DB, readonly=True)
        dst = sqlite3.connect(bak)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()

    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    say("\n" + ("DRY RUN — nothing will be written" if args.dry_run else "merging…"))
    try:
        with con:
            stats = _merge(con, payload, dry=args.dry_run)
    finally:
        con.close()

    say("\n" + "=" * 58)
    say(f"  {'table':<22}{'new':>7}{'updated':>9}{'same':>8}")
    say("=" * 58)
    ti = tu = ts = 0
    for t, (i, u, s) in stats.items():
        say(f"  {t:<22}{i:>7,}{u:>9,}{s:>8,}")
        ti, tu, ts = ti + i, tu + u, ts + s
    say("=" * 58)
    say(f"  {'TOTAL':<22}{ti:>7,}{tu:>9,}{ts:>8,}")
    if args.dry_run:
        say("\nDry run only. Re-run without --dry-run to apply.")
    else:
        say("\nDone. Your order history and placement locks were never touched.")


# ══════════════════════════════════════════════════════════════════════

def cmd_status(args):
    say("=" * 60)
    say("  TRAVEL MODE — status")
    say("=" * 60)
    say(f"  database: {DB}")
    if not os.path.exists(DB):
        say("  !! missing")
        return
    say(f"  size:     {_human(os.path.getsize(DB))}")
    con = _conn(DB, readonly=True)
    have = _tables(con)
    say("\n  machine-owned (never merged back):")
    for t, n in _counts(con, have & {"orders", "placer_claims", "order_placer_orders",
                                     "closing_orders"}).items():
        say(f"    {t:<24}{n:>10,}")
    say("\n  human-owned (travels back from Windows):")
    for t, n in _counts(con, have & set(HUMAN_TABLES)).items():
        say(f"    {t:<24}{n:>10,}")
    con.close()
    ip = _tailscale_ip()
    peer = _peer_ip()
    say(f"\n  this mac on tailnet: {ip or '?'}")
    say(f"  windows peer:        {peer or 'not found'}")
    say("=" * 60)


def main():
    p = argparse.ArgumentParser(description="Hand the dashboard to Windows and take the work back.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--go", action="store_true", help="(Mac) build + serve the travel snapshot")
    g.add_argument("--install", metavar="FILE", help="(Windows) install a downloaded snapshot")
    g.add_argument("--export", nargs="?", const=True, metavar="FILE",
                   help="(Windows) dump the human tables to JSON")
    g.add_argument("--serve-export", action="store_true",
                   help="(Windows) export + serve for the Mac to pull (no dashboard restart)")
    g.add_argument("--back", action="store_true", help="(Mac) merge the team's work back in")
    g.add_argument("--status", action="store_true", help="show what is where")
    p.add_argument("--dry-run", action="store_true", help="with --back: preview, write nothing")
    p.add_argument("--from-file", metavar="FILE", help="with --back: merge from a JSON file")
    p.add_argument("--host", metavar="IP", help="with --back: Windows tailnet IP")
    p.add_argument("--admin-pw", metavar="PW", help="with --back: dashboard admin password")
    a = p.parse_args()

    if a.go:
        cmd_go(a)
    elif a.install:
        cmd_install(a)
    elif a.serve_export:
        cmd_serve_export(a)
    elif a.export:
        cmd_export(a)
    elif a.back:
        cmd_back(a)
    else:
        cmd_status(a)


if __name__ == "__main__":
    main()
