"""
Parallel FedEx re-check — resolves the remaining Incomplete-WITH-tracking orders
fast by running N FedEx browsers at once. FedEx tracking is PUBLIC (no login), and
the CDP track call drives one tab per browser (no window-switching → no focus theft).

Reuses closing's EXACT logic: bulk_fedex_check (per-order all-packages-delivered
aggregation + session re-prime) → calculate_profit → determine_label. So an order
is relabelled identically to a normal closing run, just checked in parallel.

Resumable: only touches order_label='Incomplete' rows that have tracking. Delivered
ones flip to Completed/Loss; still-moving ones get an updated delivery_status and
stay Incomplete (retried next run).

Usage:
  FEDEX_ACCOUNT=Hathoro FEDEX_WORKERS=3 ../.venv/bin/python scripts/fedex_recheck.py
  env: FEDEX_ACCOUNT (Hathoro), FEDEX_WORKERS (3), FEDEX_MONTHS (2026-05,2026-06),
       FEDEX_LIMIT (0=all), FEDEX_PORT_BASE (9341)
"""
import os, sys, time, sqlite3, threading

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)
print = __import__("functools").partial(print, flush=True)

from utils.accounts_config import default_months

ACCOUNT   = os.environ.get("FEDEX_ACCOUNT", "Hathoro").strip()
WORKERS   = int(os.environ.get("FEDEX_WORKERS") or 3)
# default = previous+current month, computed — a hardcoded default here silently
# excluded every new month's orders from the re-check.
MONTHS    = [m.strip() for m in os.environ.get("FEDEX_MONTHS", default_months()).split(",") if m.strip()]
LIMIT     = int(os.environ.get("FEDEX_LIMIT") or 0)
PORT_BASE = int(os.environ.get("FEDEX_PORT_BASE") or 9341)
BATCH     = 30
DB        = os.path.join(_AUTO, "data", "automations_unified.db")

os.environ.setdefault("CLOSING_ACCOUNT", ACCOUNT)   # closing reads this at import
import closing
from engine.fedex_api import FedExTracker

_PERSIST = ("delivery_status", "delivery_date", "signed_by", "left_at", "estimated_window",
            "last_scan_location", "last_scan_time", "delivery_note", "delivery_from", "delivery_to")
_counter = {"done": 0, "completed": 0}
_lock = threading.Lock()


def _empty(v):
    return v is None or str(v).strip().lower() in ("", "none")


def load_orders():
    c = sqlite3.connect(DB); c.execute("PRAGMA query_only=ON")
    ph = ",".join("?" * len(MONTHS))
    rows = c.execute(
        f"""SELECT amazon_order_id, tracking_ids, tracking_id_uploaded, earnings, zoro_order_total,
                   zoro_tax, profit_dollars, return_requested, refund_done, delivery_status
            FROM closing_orders
            WHERE account=? AND substr(purchase_date,1,7) IN ({ph}) AND order_label='Incomplete'
            ORDER BY purchase_date DESC""",
        [ACCOUNT, *MONTHS]).fetchall()
    c.close()
    cols = ["amazon_order_id", "tracking_ids", "tracking_id_uploaded", "earnings", "zoro_order_total",
            "zoro_tax", "profit_dollars", "return_requested", "refund_done", "delivery_status"]
    orders = []
    for r in rows:
        o = dict(zip(cols, r))
        # merge SC uploaded tracking so FedEx checks it (same bridge as closing)
        up = str(o.get("tracking_id_uploaded") or "").strip()
        if up:
            have = closing._order_tids(o)
            if up not in have:
                o["tracking_ids"] = ", ".join(have + [up])
        if closing._order_tids(o):          # only ones we can actually check
            orders.append(o)
    if LIMIT:
        orders = orders[:LIMIT]
    return orders


def persist(conn, o):
    sets = ["enriched_from_fedex=1", "order_label=?", "delivery_status=?"]
    vals = [o.get("order_label"), o.get("delivery_status")]
    for k in _PERSIST[1:]:
        if not _empty(o.get(k)):
            sets.append(f"{k}=?"); vals.append(str(o[k]).strip())
    if o.get("profit_dollars") is not None:
        sets.append("profit_dollars=?"); vals.append(o["profit_dollars"])
        sets.append("profit_percent=?"); vals.append(o.get("profit_percent"))
    # persist any merged tracking so it sticks
    if not _empty(o.get("tracking_ids")):
        sets.append("tracking_ids=CASE WHEN tracking_ids IS NULL OR TRIM(CAST(tracking_ids AS TEXT)) IN ('','None') THEN ? ELSE tracking_ids END")
        vals.append(str(o["tracking_ids"]).strip())
    vals.append(o["amazon_order_id"])
    conn.execute(f"UPDATE closing_orders SET {', '.join(sets)} WHERE amazon_order_id=?", vals)


def worker(idx, chunk, total):
    port = PORT_BASE + idx
    fedex = FedExTracker(headless=True)
    driver = None
    if not fedex.has_official_api:
        driver = closing.ensure_chrome(port, headless=True)
        if not driver:
            print(f"[w{idx}] could not start Chrome on {port}"); return
        try:
            driver.get("https://www.fedex.com/wtrk/track/"); time.sleep(3)
        except Exception:
            pass
        fedex.driver = driver
        try:
            fedex.prime_session()
        except Exception as e:
            print(f"[w{idx}] prime_session notice ({e}) — continuing")

    conn = sqlite3.connect(DB, timeout=60); conn.execute("PRAGMA busy_timeout=60000")
    for i in range(0, len(chunk), BATCH):
        batch = chunk[i:i + BATCH]
        try:
            closing.bulk_fedex_check(batch, fedex)          # sets delivery_status etc.
            for o in batch:
                pd, pp = closing.calculate_profit(o)
                if pd is not None:
                    o["profit_dollars"], o["profit_percent"] = pd, pp
                o["order_label"] = closing.determine_label(o)
                persist(conn, o)
            conn.commit()
        except Exception as e:
            print(f"[w{idx}] batch error: {e}")
        with _lock:
            _counter["done"] += len(batch)
            _counter["completed"] += sum(1 for o in batch if o.get("order_label") == "Completed")
            print(f"{_counter['done']}/{total} checked | {_counter['completed']} → Completed")
        time.sleep(2)   # gentle pacing
    conn.close()
    if driver:
        closing.release_chrome(driver)
    fedex.close()


def main():
    print(f"FedEx re-check | {ACCOUNT} | {WORKERS} browsers | months {'+'.join(MONTHS)}")
    orders = load_orders()
    total = len(orders)
    print(f"Incomplete-with-tracking to re-check: {total}")
    if not total:
        print("nothing to do."); return
    chunks = [orders[i::WORKERS] for i in range(WORKERS)]   # round-robin split
    threads = []
    for idx in range(WORKERS):
        if not chunks[idx]:
            continue
        t = threading.Thread(target=worker, args=(idx, chunks[idx], total), daemon=True)
        t.start(); threads.append(t)
        time.sleep(2)   # stagger browser launches
    for t in threads:
        t.join()
    print(f"\nDONE — {_counter['done']} re-checked, {_counter['completed']} flipped to Completed.")


if __name__ == "__main__":
    main()
