#!/usr/bin/env python3
"""
sync_amazon_report.py — keep Amazon's own order list in the DB (27 Sep 2026).

closing.py ingests only the orders that were placed on Zoro (a WB id in merchant-order-id), so
the orders Amazon took that were never placed — cancelled before placement, out of stock, low
profit, state-restricted, waiting — never reached the DB: 6.5 % of Hathoro's last 30 days. The
dashboard's daily report counts days the way Seller Central does, so it needs Amazon's full list.

Every all-orders report the stack downloads — closing (data/reports/<ACCOUNT>/) and the tracking
uploaders (engine/_dl_<account>/) — is folded into two tables (utils/unified_db.py):

  amazon_report_orders   one row per Amazon order: US/Pacific purchase date and day, order-status,
                         merchant-order-id, lines / units / sales, SKUs / ASINs, ship-to
  amazon_report_files    one row per ingested file: account and the Pacific days it covers

A newer report never loses to an older one (last-updated-date decides), so files can be ingested
in any order; a file already ingested with the same mtime is skipped. Read-only on the files.

Usage:
  ../.venv/bin/python scripts/sync_amazon_report.py                        # every known report file
  ../.venv/bin/python scripts/sync_amazon_report.py --file X.txt --account HOANK
  # backfill a past range through Seller Central (needs the account's SC Chrome port free —
  # no closing / tracker / messaging run on it); one account per call, one month per request:
  ../.venv/bin/python scripts/sync_amazon_report.py --download --account HOANK --from 2026-08-01 --to 2026-08-31
Runs hourly from scripts/auto_sync_10min.py and after the closing ingest in scripts/daily_bulk.py.
"""
import argparse
import csv
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)

from utils.unified_db import ensure_amazon_report_schema  # noqa: E402

DB = os.path.join(_AUTO, "data", "automations_unified.db")
PT = ZoneInfo("America/Los_Angeles")

_COLS = ("amazon_order_id", "account", "purchase_date", "purchase_day", "last_updated", "order_status",
         "merchant_order_id", "items", "units", "sales", "shipping", "item_status", "skus", "asins", "title",
         "ship_city", "ship_state", "ship_postal_code", "is_business", "fulfillment", "sales_channel",
         "report_file", "captured_at")


def _pt(s):
    """Report timestamps are ISO with an offset (UTC in practice) → US/Pacific datetime."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(PT)


def _num(v):
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def parse_report(path):
    """{amazon_order_id: order dict} from one all-orders report, or None when the file is not one."""
    orders = {}
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        rd = csv.DictReader(f, delimiter="\t")
        heads = [(h or "").strip() for h in (rd.fieldnames or [])]
        if "amazon-order-id" not in heads or "purchase-date" not in heads:
            return None
        for raw in rd:
            r = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k is not None}
            oid = r.get("amazon-order-id", "")
            if not oid:
                continue
            o = orders.get(oid)
            if o is None:
                pdt, lu = _pt(r.get("purchase-date")), _pt(r.get("last-updated-date"))
                o = orders[oid] = {
                    "amazon_order_id": oid,
                    "purchase_date": pdt.strftime("%Y-%m-%d %H:%M:%S") if pdt else "",
                    "purchase_day": pdt.strftime("%Y-%m-%d") if pdt else "",
                    "last_updated": lu.strftime("%Y-%m-%d %H:%M:%S") if lu else "",
                    "order_status": r.get("order-status", ""),
                    "merchant_order_id": r.get("merchant-order-id", ""),
                    "items": 0, "units": 0, "sales": 0.0, "shipping": 0.0,
                    "_item_status": [], "_skus": [], "_asins": [],
                    "title": (r.get("product-name") or "")[:200],
                    "ship_city": r.get("ship-city", ""), "ship_state": r.get("ship-state", ""),
                    "ship_postal_code": r.get("ship-postal-code", ""),
                    "is_business": "Yes" if r.get("is-business-order", "").lower() == "true" else "No",
                    "fulfillment": r.get("fulfillment-channel", ""), "sales_channel": r.get("sales-channel", ""),
                }
            o["items"] += 1
            o["units"] += int(_num(r.get("quantity")))
            o["sales"] += _num(r.get("item-price"))
            o["shipping"] += _num(r.get("shipping-price"))
            for key, col in (("_item_status", "item-status"), ("_skus", "sku"), ("_asins", "asin")):
                v = r.get(col, "")
                if v and v not in o[key]:
                    o[key].append(v)
    for o in orders.values():
        o["item_status"] = ", ".join(o.pop("_item_status"))[:120]
        o["skus"] = ", ".join(o.pop("_skus"))[:300]
        o["asins"] = ", ".join(o.pop("_asins"))[:300]
        o["sales"], o["shipping"] = round(o["sales"], 2), round(o["shipping"], 2)
    return orders


def _account_for(path, orders, conn):
    """The account a report belongs to: from its folder, else by looking its orders up."""
    from utils.accounts_config import accounts
    low = path.replace("\\", "/").lower()
    for acct in accounts():
        a = acct.lower()
        if f"/reports/{a}/" in low or f"/_dl_{a}/" in low:
            return acct
    ids = list(orders)[:400]
    votes = Counter()
    for table in ("closing_orders", "order_placer_orders"):
        for i in range(0, len(ids), 200):
            part = ids[i:i + 200]
            try:
                for (acct,) in conn.execute(f"SELECT account FROM {table} WHERE amazon_order_id IN "
                                            f"({','.join('?' * len(part))})", part):
                    if acct:
                        votes[acct] += 1
            except sqlite3.Error:
                pass
    return votes.most_common(1)[0][0] if votes else ""


def known_files():
    """Every all-orders report file the stack keeps on disk (newest first)."""
    from utils.accounts_config import accounts
    dirs = [os.path.join(_AUTO, "data", "reports")]
    for acct in accounts():
        dirs.append(os.path.join(_AUTO, "data", "reports", acct))
        dirs.append(os.path.join(_AUTO, "engine", f"_dl_{acct.lower()}"))
    out = []
    for d in dirs:
        try:
            out += [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".txt")]
        except OSError:
            pass
    return sorted(set(out), key=os.path.getmtime, reverse=True)


def ingest_file(conn, path, account="", verbose=True, record_span=True):
    """Fold one report into the tables. Returns (orders, account) or (0, reason) when skipped.
    record_span=False keeps the orders but claims no coverage (a backfill whose range Amazon did
    not honour must not mark its days as matching Amazon)."""
    name = os.path.basename(path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return 0, "missing"
    row = conn.execute("SELECT mtime FROM amazon_report_files WHERE report_file=?", (name,)).fetchone()
    if row and abs(float(row[0] or 0) - mtime) < 1:
        return 0, "already ingested"
    orders = parse_report(path)
    if orders is None:
        return 0, "not an all-orders report"
    if not orders:
        return 0, "empty report"
    acct = account or _account_for(path, orders, conn)
    if not acct:
        return 0, "account unknown"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    upd = ", ".join(f"{c}=excluded.{c}" for c in _COLS if c != "amazon_order_id")
    sql = (f"INSERT INTO amazon_report_orders ({', '.join(_COLS)}) VALUES ({', '.join('?' * len(_COLS))}) "
           f"ON CONFLICT(amazon_order_id) DO UPDATE SET {upd} "
           f"WHERE COALESCE(excluded.last_updated,'') >= COALESCE(amazon_report_orders.last_updated,'')")
    vals = []
    for o in orders.values():
        o.update(account=acct, report_file=name, captured_at=now)
        vals.append([o.get(c) for c in _COLS])
    conn.executemany(sql, vals)
    days = sorted(o["purchase_day"] for o in orders.values() if o["purchase_day"])
    n_rows = sum(o["items"] for o in orders.values())
    conn.execute("INSERT OR REPLACE INTO amazon_report_files (report_file, account, path, mtime, rows, orders, "
                 "span_from, span_to, ingested_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (name, acct, path, mtime, n_rows, len(orders), days[0] if days and record_span else "",
                  days[-1] if days and record_span else "", now))
    if verbose:
        st = Counter(o["order_status"] for o in orders.values())
        print(f"[AMZ-REPORT] {acct}: {name} → {len(orders):,} orders ({n_rows:,} lines), "
              f"{days[0] if days else '?'} → {days[-1] if days else '?'} · "
              + ", ".join(f"{k} {v:,}" for k, v in st.most_common()), flush=True)
    return len(orders), acct


def sync(files=None, account="", verbose=True, record_span=True):
    """Ingest the given files (default: every known report file). Returns a summary dict."""
    ensure_amazon_report_schema()
    conn = sqlite3.connect(DB, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    out = {"ingested": 0, "orders": 0, "skipped": 0}
    try:
        for path in (files or known_files()):
            n, info = ingest_file(conn, path, account=account, verbose=verbose, record_span=record_span)
            if n:
                out["ingested"] += 1
                out["orders"] += n
                conn.commit()
            else:
                out["skipped"] += 1
                if verbose and info not in ("already ingested",):
                    print(f"[AMZ-REPORT] skipped {os.path.basename(path)}: {info}", flush=True)
        conn.commit()
    finally:
        conn.close()
    if verbose:
        print(f"[AMZ-REPORT] ✅ {out['ingested']} file(s) ingested ({out['orders']:,} orders), "
              f"{out['skipped']} unchanged/skipped", flush=True)
    return out


def download(account, start, end):
    """Backfill: ask Seller Central for the all-orders report of an exact date range (closing's
    own downloader, same Chrome profile, same auto-login) and ingest it. The file lands in
    data/reports/backfill/<ACCOUNT>/ so closing's latest 30-day report is left alone. Returns the
    sync summary plus the Pacific days the file really covers — Amazon's picker has ignored dates
    before, so the span is checked, not assumed. One account per process (closing reads its
    account at import)."""
    from utils.accounts_config import accounts
    from utils.proc import port_open, close_chrome
    os.environ["CLOSING_ACCOUNT"] = account
    import closing
    port = int(accounts()[account]["report_port"])
    # one folder per range: closing's downloader empties its folder before every download, and a
    # shared backfill folder lost each month's file to the next month's request
    dest = os.path.join(_AUTO, "data", "reports", "backfill", account, f"{start}_{end}")
    os.makedirs(dest, exist_ok=True)
    closing.REPORTS_DIR = dest
    ours = not port_open(port)                  # nothing on the port → the Chrome will be ours
    try:
        path = closing.download_report(port, start_date=start, end_date=end)
    finally:
        if ours:   # only the Chrome this call launched (closing profile + this debugging port)
            close_chrome(os.path.join(closing.CLOSING_PROFILE_ROOT, f"port_{port}"), port, timeout=10)
    if not path:
        print(f"[AMZ-REPORT] ❌ {account}: no report for {start} → {end}", flush=True)
        return None
    orders = parse_report(path) or {}
    days = sorted(o["purchase_day"] for o in orders.values() if o["purchase_day"])
    span = (days[0], days[-1]) if days else ("", "")
    honored = bool(days) and span[0] <= start and span[1] >= min(end, (datetime.now(PT).date()).isoformat())
    print(f"[AMZ-REPORT] {account}: asked {start} → {end}, file covers {span[0] or '?'} → {span[1] or '?'} "
          f"({len(orders):,} orders){'' if honored else ' — ⚠️ NOT the requested range'}", flush=True)
    out = sync(files=[path], account=account, record_span=honored)
    out.update(span=span, honored=honored, path=path)
    return out


def main():
    ap = argparse.ArgumentParser(description="Fold Amazon all-orders reports into amazon_report_orders.")
    ap.add_argument("--file", action="append", help="a report file (repeatable); default: every known file")
    ap.add_argument("--account", default="", help="account of --file / --download")
    ap.add_argument("--download", action="store_true",
                    help="backfill: request the report for --from/--to from Seller Central, then ingest it")
    ap.add_argument("--from", dest="start", default="", help="YYYY-MM-DD (with --download)")
    ap.add_argument("--to", dest="end", default="", help="YYYY-MM-DD (with --download)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.download:
        if not (a.account and a.start and a.end):
            ap.error("--download needs --account, --from and --to")
        r = download(a.account, a.start, a.end)
        return 0 if (r and r.get("honored")) else 2
    sync(files=a.file, account=a.account, verbose=not a.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
