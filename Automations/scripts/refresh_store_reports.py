#!/usr/bin/env python3
"""
refresh_store_reports.py — keep the owner's report folders current, by themselves.

The owner keeps each store's Amazon reports in one place, the way engine/listings.py (the
launcher's Listings page) reads them:

    1. AB ECOM/   Discounted ALR.txt             Hathoro's All Listings Report
                  Order_Reports/2026-08-August.txt   one All Orders file per month
    2. Hypersale/ Hypersale ALR.txt              HOANK's All Listings Report
                  Order_Reports/…

They were filled by hand and stopped in August (19 / 22 Aug). This keeps them filled:

  ORDERS   (every run, no Chrome)  Every All Orders report the stack downloads — the tracker every
           30 min, closing every night, month backfills — is folded into the month files: one line
           per (amazon-order-id, sku), the newest last-updated-date wins, months are Seller
           Central's (US/Pacific; the owner's own files are cut that way: a 31 Jul 22:00 PT order is
           in the July file though its UTC stamp says August). Each file keeps its own header —
           HOANK's downloads carry an extra is-prime column the owner's files don't — and its CRLF
           line ends. A month is found by the month NAME in its file name, so the owner's
           "2026-05-August.txt" is August, as its contents are. Nothing is renamed or deleted.

  LISTINGS (once a day per store)  The All Listings Report is requested through that store's own
           Seller Central Chrome (HOANK 9331, Hathoro 9333 — never a placer's 9221-9224), holding
           the Chrome's turn (utils/sc_chrome) so a tracker or closing run is never interrupted.
           The previous file is backed up, the new one replaces it, and the ASINs that appeared
           or disappeared since the last one are written to data/store_reports/<store>_alr_diff.json.

Every file is backed up once a day before its first change, to ~/.zuhra/backups/store_reports —
outside the Google Drive mirror. Writes are atomic (temp file + rename).

    python scripts/refresh_store_reports.py --orders              # fold new orders into the months
    python scripts/refresh_store_reports.py --listings HOANK      # download HOANK's ALR now
    python scripts/refresh_store_reports.py --auto                # hourly: orders + each ALR older than 24 h
    python scripts/refresh_store_reports.py --orders --dry-run    # say what would change, write nothing
    python scripts/refresh_store_reports.py --orders --root /tmp/copy   # work on a copy of Dropshipping/
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AUTO)

PT = ZoneInfo("America/Los_Angeles")
DB = os.path.join(AUTO, "data", "automations_unified.db")
STATE_DIR = os.path.join(AUTO, "data", "store_reports")
STATE = os.path.join(STATE_DIR, "state.json")
BACKUPS = os.path.expanduser("~/.zuhra/backups/store_reports")
ALR_EVERY_H = 24
ALR_RETRY_H = 2            # after a failed attempt, wait this long before the next
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
# One line of an Amazon order is identified by its order-item-id. (order, sku) is NOT unique: an
# order can carry the same SKU on two line items, and keying on it collapsed 12 real lines in the
# owner's files on 11 Oct (restored from the backups). sku is only the fallback for a file with no
# order-item-id column.
KEY_COLS = ("amazon-order-id", "order-item-id")
FALLBACK_KEY = ("amazon-order-id", "sku")


def key_cols(cols):
    return KEY_COLS if all(k in cols for k in KEY_COLS) else FALLBACK_KEY


# ───────────────────────────────────────────────────────────────── stores
def stores(root=None):
    """The store folders, from engine/listings.py — the one place that knows them. --root swaps
    the Dropshipping folder for a copy (testing)."""
    from engine.listings import ACCOUNTS, DROPSHIPPING_ROOT
    out = []
    for a in ACCOUNTS:
        alr, rep = a["alr"], a["reports"]
        if root:
            alr = os.path.join(root, os.path.relpath(alr, DROPSHIPPING_ROOT))
            rep = os.path.join(root, os.path.relpath(rep, DROPSHIPPING_ROOT))
        out.append({"store": a["key"], "alr": alr, "reports": rep})
    return out


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, STATE)


def backup(path, store, kind):
    """One copy per file per day, before its first change. Returns the copy's path or None."""
    if not os.path.exists(path):
        return None
    d = os.path.join(BACKUPS, kind, store, datetime.now().strftime("%Y%m%d"))
    dst = os.path.join(d, os.path.basename(path))
    if os.path.exists(dst):
        return dst
    os.makedirs(d, exist_ok=True)
    shutil.copy2(path, dst)
    return dst


# ───────────────────────────────────────────────────────────────── tab-separated files
def read_tsv(path):
    """(raw header line, stripped column names, rows as lists, had_bom). Amazon's flat files are
    plain tab-separated with no quoting, so a line is split on tabs and nothing else."""
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig", errors="replace")
    lines = text.split("\r\n") if "\r\n" in text else text.split("\n")
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        return "", [], [], bom
    head = lines[0]
    cols = [c.strip().lstrip("﻿") for c in head.split("\t")]
    rows = [ln.split("\t") for ln in lines[1:]]
    return head, cols, rows, bom


def write_tsv(path, head, rows, bom=False):
    tmp = path + ".tmp"
    body = "\r\n".join([head] + ["\t".join(r) for r in rows]) + "\r\n"
    with open(tmp, "wb") as fh:
        if bom:
            fh.write(b"\xef\xbb\xbf")
        fh.write(body.encode("utf-8"))
    os.replace(tmp, path)


def pacific_month(iso):
    """'2026-08-01T03:12:09+00:00' → (2026, 7): Seller Central's month, not UTC's."""
    try:
        return datetime.fromisoformat(str(iso).strip().replace("Z", "+00:00")).astimezone(PT).timetuple()[:2]
    except (TypeError, ValueError):
        return None


def month_of_name(fname):
    """'2026-05-August.txt' → (2026, 8). The month WORD decides: the owner's HOANK folder has
    April…August files all prefixed 2026-05, and their contents follow the word, not the number.
    'Febuary' (sic) is February."""
    stem = os.path.splitext(fname)[0]
    parts = stem.split("-")
    if len(parts) < 2 or not parts[0].isdigit():
        return None
    year = int(parts[0])
    word = (parts[-1] or "").strip().lower()[:3]
    for i, m in enumerate(MONTHS, 1):
        if m.lower()[:3] == word:
            return year, i
    if len(parts) >= 2 and parts[1].isdigit() and 1 <= int(parts[1]) <= 12:
        return year, int(parts[1])
    return None


# ───────────────────────────────────────────────────────────────── orders → month files
def report_files(store):
    """Every all-orders report the stack has downloaded for this store and still has on disk,
    oldest first, from the registry scripts/sync_amazon_report.py keeps."""
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
        rows = c.execute("SELECT path, mtime FROM amazon_report_files WHERE account = ? ORDER BY mtime",
                         (store,)).fetchall()
        c.close()
    except Exception:
        rows = []
    seen, out = set(), []
    for p, mt in rows:
        if p and p not in seen and os.path.exists(p):
            seen.add(p)
            out.append((p, os.path.getmtime(p)))
    return out


def merge_orders(store_cfg, files, dry_run=False, verbose=True):
    """Fold the given report files into the store's month files. Returns a summary per month."""
    store, folder = store_cfg["store"], store_cfg["reports"]
    if not os.path.isdir(folder):
        print(f"  {store}: no Order_Reports folder at {folder} — skipped")
        return {}

    # newest line per (order, sku) across the incoming reports, by last-updated-date
    incoming = {}                               # (y, m) -> {key: (updated, {col: val})}
    for path, _mt in files:
        head, cols, rows, _bom = read_tsv(path)
        if not cols or "amazon-order-id" not in cols or "purchase-date" not in cols:
            continue
        ix = {c: i for i, c in enumerate(cols)}
        kc = key_cols(cols)
        for r in rows:
            if len(r) < len(cols):
                r = r + [""] * (len(cols) - len(r))
            rec = {c: r[i] for c, i in ix.items()}
            key = tuple(rec.get(k, "") for k in kc)
            if not key[0]:
                continue
            ym = pacific_month(rec.get("purchase-date"))
            if not ym:
                continue
            upd = rec.get("last-updated-date") or ""
            bucket = incoming.setdefault(ym, {})
            old = bucket.get(key)
            if old is None or upd >= old[0]:
                bucket[key] = (upd, rec)

    # the store's month files, by the month their NAME says
    existing = {}
    for f in sorted(os.listdir(folder)):
        if f.endswith(".txt") and not f.startswith("."):
            ym = month_of_name(f)
            if ym and ym not in existing:
                existing[ym] = os.path.join(folder, f)
    template = existing[max(existing)] if existing else None

    summary = {}
    for ym in sorted(incoming):
        new_rows = incoming[ym]
        path = existing.get(ym) or os.path.join(folder, f"{ym[0]}-{ym[1]:02d}-{MONTHS[ym[1] - 1]}.txt")
        created = ym not in existing
        if created:
            if template:
                head, cols, _r, bom = read_tsv(template)
            else:                                # no month file at all yet: the report's own header
                head, cols, _r, bom = read_tsv(files[-1][0])
            rows = []
        else:
            head, cols, rows, bom = read_tsv(path)
        ix = {c: i for i, c in enumerate(cols)}
        fk = key_cols(cols)
        by_key, order = {}, []
        for r in rows:
            if len(r) < len(cols):
                r = r + [""] * (len(cols) - len(r))
            key = tuple(r[ix[k]] if k in ix else "" for k in fk)
            if key not in by_key:
                order.append(key)
            by_key[key] = r
        added = updated = 0
        for _rkey, (upd, rec) in new_rows.items():
            key = tuple(rec.get(k, "") for k in fk)
            line = [rec.get(c, "") for c in cols]
            cur = by_key.get(key)
            if cur is None:
                by_key[key] = line
                order.append(key)
                added += 1
                continue
            cur_upd = cur[ix["last-updated-date"]] if "last-updated-date" in ix else ""
            if upd < cur_upd:                    # never let an older report undo a newer line
                continue
            # Field by field, and a blank never wins. Amazon strips the ship-to address from orders
            # older than about 30 days, so a later report of a July order arrives with ship-city,
            # -state, -postal-code and -country empty; taking that line whole erased the owner's
            # addresses (90 of 105 July "updates" were exactly that). A status that moved —
            # Pending → Cancelled — still comes through.
            merged = [n if (n or "").strip() else o for o, n in zip(cur, line)]
            if merged != cur:
                by_key[key] = merged
                updated += 1
        if not added and not updated:
            continue
        pd_ix = ix.get("purchase-date")
        out = sorted((by_key[k] for k in order),
                     key=lambda r: ((r[pd_ix] if pd_ix is not None else ""), r[ix.get("amazon-order-id", 0)]))
        label = f"{ym[0]}-{ym[1]:02d}"
        summary[label] = {"file": os.path.basename(path), "created": created, "added": added,
                          "updated": updated, "lines": len(out)}
        if verbose:
            print(f"  {store} {label}: {'new file ' if created else ''}{os.path.basename(path)} "
                  f"+{added:,} new, {updated:,} updated → {len(out):,} lines")
        if not dry_run:
            if not created:
                backup(path, store, "orders")
            write_tsv(path, head, out, bom)
    return summary


def run_orders(root=None, dry_run=False, all_files=False):
    st = load_state()
    total = {}
    for sc in stores(root):
        store = sc["store"]
        merged = st.setdefault("orders", {}).setdefault(store, {})
        files = report_files(store)
        todo = files if all_files else [(p, mt) for p, mt in files if merged.get(p) != mt]
        if not todo:
            print(f"  {store}: no new report since the last merge")
            continue
        print(f"  {store}: merging {len(todo)} report file(s)")
        total[store] = merge_orders(sc, todo, dry_run=dry_run)
        if not dry_run and not root:
            for p, mt in todo:
                merged[p] = mt
    if not dry_run and not root:
        st["orders_at"] = datetime.now().isoformat(timespec="seconds")
        save_state(st)
    return total


# ───────────────────────────────────────────────────────────────── All Listings Report
def alr_asins(path):
    """{asin: status} from an All Listings Report."""
    if not path or not os.path.exists(path):
        return {}
    _h, cols, rows, _b = read_tsv(path)
    ix = {c: i for i, c in enumerate(cols)}
    a_i, s_i = ix.get("asin1"), ix.get("status")
    out = {}
    if a_i is None:
        return out
    for r in rows:
        if len(r) > a_i and r[a_i].strip():
            out[r[a_i].strip().upper()] = (r[s_i].strip() if s_i is not None and len(r) > s_i else "")
    return out


def new_sellers(store, days=30):
    """ASINs whose FIRST order (Amazon's own report) fell in the last `days` — listings that have
    just started selling, i.e. the ones a fixed list would never have picked up."""
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
        since = (datetime.now(PT).date() - timedelta(days=days)).isoformat()
        firsts = {}
        for asins, day in c.execute("SELECT asins, purchase_day FROM amazon_report_orders "
                                    "WHERE account = ? AND asins IS NOT NULL", (store,)):
            for a in str(asins).replace(";", ",").split(","):
                a = a.strip().upper()
                if a and (a not in firsts or day < firsts[a]):
                    firsts[a] = day
        c.close()
        return sorted(a for a, d in firsts.items() if d and d >= since)
    except Exception:
        return []


def download_listings(store, root=None):
    """Request + download this store's All Listings Report through its own Seller Central Chrome,
    holding the Chrome's turn. Runs in its own process (closing reads its account at import)."""
    from utils.accounts_config import accounts
    from utils.sc_chrome import sc_turn, SCBusy
    sc = next(s for s in stores(root) if s["store"] == store)
    port = int(accounts()[store]["report_port"])
    os.environ["CLOSING_ACCOUNT"] = store
    tmp = os.path.join(STATE_DIR, f"{store}_alr_download.tmp")
    os.makedirs(STATE_DIR, exist_ok=True)
    if os.path.exists(tmp):
        os.remove(tmp)
    try:
        with sc_turn(port, f"all-listings report {store}", max_wait=600, on_timeout="skip"):
            import closing
            from platforms.download_all_listings_report import download_all_listings_report
            from utils.proc import port_open, close_chrome
            ours = not port_open(port)           # nothing on the port → the Chrome will be ours
            d = closing.ensure_chrome(port)
            try:
                path = download_all_listings_report(port=port, output_tsv=tmp, timeout_seconds=300)
            finally:
                closing.release_chrome(d)
                # release_chrome() only ends the WebDriver session; a Chrome launched here and
                # attached by port keeps running (two were left up on 11 Oct). Close it, and only
                # it — the same as scripts/sync_amazon_report.download does.
                if ours:
                    close_chrome(os.path.join(closing.CLOSING_PROFILE_ROOT, f"port_{port}"), port, timeout=10)
    except SCBusy as e:
        print(f"  {store}: {e} — the listings report waits for the next run")
        return {"ok": False, "why": "busy"}
    if not path or not os.path.exists(path):
        return {"ok": False, "why": "no file"}

    _h, cols, rows, _b = read_tsv(path)
    if "asin1" not in cols or "seller-sku" not in cols or len(rows) < 1:
        os.remove(path)
        print(f"  {store}: the downloaded file is not an All Listings Report — kept the old one")
        return {"ok": False, "why": "not an ALR"}

    before, after = alr_asins(sc["alr"]), alr_asins(path)
    same = os.path.exists(sc["alr"]) and open(sc["alr"], "rb").read() == open(path, "rb").read()
    backup(sc["alr"], store, "alr")
    os.replace(path, sc["alr"])
    added = sorted(set(after) - set(before)) if before else []
    removed = sorted(set(before) - set(after)) if before else []
    diff = {
        "store": store, "at": datetime.now().isoformat(timespec="seconds"),
        "listings": len(after), "active": sum(1 for s in after.values() if s.lower() == "active"),
        "unchanged_file": same, "new_listings": added, "removed_listings": removed,
        "new_sellers_30d": new_sellers(store),
    }
    json.dump(diff, open(os.path.join(STATE_DIR, f"{store}_alr_diff.json"), "w", encoding="utf-8"), indent=1)
    print(f"  {store}: All Listings Report saved — {diff['listings']:,} listings ({diff['active']:,} active)"
          + ("; identical to the last one (Amazon may have served the previous report)" if same else "")
          + f"; {len(added)} new / {len(removed)} gone since the last report; "
            f"{len(diff['new_sellers_30d'])} ASINs had their first order in the last 30 days")
    return {"ok": True, **{k: (len(v) if isinstance(v, list) else v) for k, v in diff.items()}}


def run_auto(root=None):
    """Hourly: orders always; each store's ALR once it is older than ALR_EVERY_H. The ALR runs in
    a child process per store, one after another (they share Chrome's temp download folder)."""
    print("── orders → month files")
    run_orders(root)
    st = load_state()
    alr = st.setdefault("alr", {})
    py = sys.executable
    for sc in stores(root):
        store = sc["store"]
        age_h = (time.time() - os.path.getmtime(sc["alr"])) / 3600 if os.path.exists(sc["alr"]) else 1e9
        tried_h = (time.time() - float(alr.get(store, {}).get("tried", 0))) / 3600
        if age_h < ALR_EVERY_H:
            print(f"── {store}: All Listings Report is {age_h:.1f} h old — next after {ALR_EVERY_H} h")
            continue
        if tried_h < ALR_RETRY_H:
            print(f"── {store}: last attempt {tried_h:.1f} h ago failed — retrying after {ALR_RETRY_H} h")
            continue
        print(f"── {store}: All Listings Report is {age_h:.0f} h old — downloading")
        alr[store] = {"tried": time.time()}
        save_state(st)
        cmd = [py, os.path.abspath(__file__), "--listings", store] + (["--root", root] if root else [])
        try:
            r = subprocess.run(cmd, cwd=AUTO, timeout=1500, capture_output=True, text=True)
            tail = [ln for ln in (r.stdout or "").splitlines() if ln.startswith(f"  {store}:")]
            print(tail[-1] if tail else f"  {store}: exit {r.returncode} {(r.stderr or '')[-200:]}")
        except subprocess.TimeoutExpired:
            print(f"  {store}: All Listings Report timed out — retrying after {ALR_RETRY_H} h")


def main():
    ap = argparse.ArgumentParser(description="Keep the owner's ALR and month order files current.")
    ap.add_argument("--orders", action="store_true", help="fold new all-orders reports into the month files")
    ap.add_argument("--all-files", action="store_true", help="with --orders: re-merge every report on disk")
    ap.add_argument("--listings", metavar="STORE", help="download that store's All Listings Report now")
    ap.add_argument("--auto", action="store_true", help="hourly mode: orders + any ALR older than 24 h")
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--root", help="a copy of the Dropshipping folder to work on instead (testing)")
    a = ap.parse_args()
    if a.listings:
        r = download_listings(a.listings, a.root)
        sys.exit(0 if r.get("ok") else (3 if r.get("why") == "busy" else 1))
    if a.auto:
        run_auto(a.root)
    elif a.orders:
        run_orders(a.root, dry_run=a.dry_run, all_files=a.all_files)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
