"""
gmail_gapfill.py — LIVE-GMAIL recovery for whatever the archive doesn't have.

The design (Kamran's): gmail_archive.db is the rate-limit optimization — bulk-
extract once, enrich from the DB. But when a field is still missing after the
archive pass, the closing chain MUST go to live Gmail and get it — the emails
are usually there (wrong/unarchived inbox), the pipeline just never looked.

This script scans closing_orders (ANY label — resume-skipped terminal rows too)
for the fields that live in email and are still empty, and searches live Gmail
tiered: the store's PREFERRED inboxes first (config/accounts.json — HOANK:
qurtdis + salesmercentile1/inhur; Hathoro: abecomllc26 + salesmercentile/
wrenchlyllc), then EVERY inbox. Recovered fields are written non-destructively
and profit/speeds/label are recomputed for the touched rows.

Fields recovered:
  earnings / amazon_fees / shipping_amt / sold_email_time  ← 'Sold, ship now' (by Amazon id)
  zoro_order_total / subtotal / shipping / tax / zoro_email_ts ← Zoro receipt (by WB)
  tracking_ids / carrier / shipped_email_time              ← Zoro 'has shipped' (by WB)

Usage:  GAPFILL_ACCOUNT=HOANK ../.venv/bin/python scripts/gmail_gapfill.py
Env:    GAPFILL_ACCOUNT (HOANK) | GAPFILL_LIMIT (500/run, newest first, 0=all)
        GAPFILL_DRY=1 (report what would be recovered, write nothing)
"""
import os
import sys
import sqlite3

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTO not in sys.path:
    sys.path.insert(0, _AUTO)
print = __import__("functools").partial(print, flush=True)

ACCOUNT = os.environ.get("GAPFILL_ACCOUNT", "HOANK").strip()
LIMIT   = int(os.environ.get("GAPFILL_LIMIT", "500") or 0)
DRY     = os.environ.get("GAPFILL_DRY", "").strip().lower() in ("1", "true", "yes")
DB      = os.path.join(_AUTO, "data", "automations_unified.db")

os.environ.setdefault("CLOSING_ACCOUNT", ACCOUNT)   # closing reads this at import
import closing
from engine.live_gmail_per_order import search_earnings, search_zoro_cost, search_zoro_tracking
from utils.accounts_config import preferred_inboxes, acquire_lock, release_lock

PREF = preferred_inboxes(ACCOUNT)


def _empty(v):
    return v is None or str(v).strip().lower() in ("", "none")


def load_gap_orders(conn):
    """Shipped orders of this account still missing an email-recoverable field.
    Newest first so today's report orders always fit inside the per-run LIMIT."""
    rows = conn.execute(
        """SELECT * FROM closing_orders
           WHERE account = ? AND order_status = 'Shipped'
             AND (   earnings IS NULL OR earnings = ''
                  OR sold_email_time IS NULL OR sold_email_time = ''
                  OR zoro_order_total IS NULL OR zoro_order_total = '' OR zoro_order_total = 0
                  OR tracking_ids IS NULL OR tracking_ids = ''
                  OR shipped_email_time IS NULL OR shipped_email_time = '')
           ORDER BY purchase_date DESC""",
        (ACCOUNT,)).fetchall()
    return [dict(r) for r in rows]


def fill_one(o):
    """Live-Gmail lookups for this order's missing fields. Returns {field: value}
    of NEW values only (never overwrites something already present)."""
    found = {}
    aid = o["amazon_order_id"]
    wbs = closing._extract_wb_ids(o.get("merchant_order_id")) or \
        ([o["wb_order_id"]] if o.get("wb_order_id") else [])

    # 1) 'Sold, ship now' → earnings + sold_email_time (Amazon-id keyed)
    if _empty(o.get("earnings")) or _empty(o.get("sold_email_time")):
        e = search_earnings(aid, preferred=PREF["earnings"])
        for k, v in (e or {}).items():
            if _empty(o.get(k)) and not _empty(v):
                found[k] = v

    # 2) Zoro receipt → cost + zoro_email_ts (WB keyed)
    if not (closing._safe_float(o.get("zoro_order_total")) or 0):
        for w in wbs:
            z = search_zoro_cost(w, preferred=PREF["zoro"])
            if z.get("zoro_order_total"):
                for k, v in z.items():
                    if _empty(o.get(k)) and not _empty(v):
                        found[k] = v
                break

    # 3) Zoro 'has shipped' → tracking + shipped_email_time (WB keyed)
    if _empty(o.get("tracking_ids")) or _empty(o.get("shipped_email_time")):
        for w in wbs:
            t = search_zoro_tracking(w, preferred=PREF["zoro"])
            if t:
                for k, v in t.items():
                    if _empty(o.get(k)) and not _empty(v):
                        found[k] = v
            if not (_empty(o.get("tracking_ids")) and "tracking_ids" not in found) \
                    and not (_empty(o.get("shipped_email_time")) and "shipped_email_time" not in found):
                break
    return found


def recompute(o):
    """Profit, speeds and label from the freshly-filled fields (same helpers as
    the closing pipeline, so a gap-filled row is indistinguishable from a
    normally-closed one)."""
    derived = {}
    p, pct = closing.calculate_profit(o)
    if p is not None:
        o["profit_dollars"] = derived["profit_dollars"] = p
        o["profit_percent"] = derived["profit_percent"] = pct
    net = closing.compute_net_profit(o)
    if net is not None:
        o["net_profit_after_refund"] = derived["net_profit_after_refund"] = net
    closing.derive_fields(o)
    for k in ("order_received_pkt", "zoro_placed_pkt", "zoro_shipped_pkt",
              "placement_speed", "placement_speed_hrs",
              "zoro_ship_speed", "zoro_ship_speed_hrs"):
        if not _empty(o.get(k)):
            derived[k] = o[k]
    o.update(closing.compute_timeliness(o))
    o.update(closing.compute_ship_timeliness(o))
    for k in ("on_time", "time_to_ship_hrs", "shipped_on_time", "ship_margin_hrs"):
        if not _empty(o.get(k)):
            derived[k] = o[k]
    label = closing.determine_label(o)
    if label and label != "Incomplete":        # never downgrade a terminal label to Incomplete
        derived["order_label"] = label
    elif not o.get("order_label"):
        derived["order_label"] = label
    return derived


def main():
    got, holder = acquire_lock(f"gapfill_{ACCOUNT}")
    if not got:
        print(f"[GAPFILL] ❌ Another gap-fill for {ACCOUNT} is running (pid {holder}) — aborting.")
        return 3
    try:
        return _run()
    finally:
        release_lock(f"gapfill_{ACCOUNT}")


def _run():
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    orders = load_gap_orders(conn)
    total_gaps = len(orders)
    if LIMIT:
        orders = orders[:LIMIT]
    print(f"[GAPFILL] {ACCOUNT}: {total_gaps} Shipped orders with email-recoverable gaps"
          + (f" → processing newest {len(orders)} (GAPFILL_LIMIT={LIMIT})" if LIMIT and total_gaps > LIMIT else "")
          + (" [DRY]" if DRY else ""))
    if not orders:
        print("[GAPFILL] ✅ Nothing to recover.")
        conn.close()
        return 0

    stats = {}
    touched = still = 0
    for i, o in enumerate(orders, 1):
        try:
            found = fill_one(o)
        except Exception as e:
            print(f"[GAPFILL]   {o['amazon_order_id']}: search error — {e}")
            continue
        if not found:
            still += 1
            continue
        o.update(found)
        updates = dict(found)
        updates.update(recompute(o))
        for k in found:
            stats[k] = stats.get(k, 0) + 1
        touched += 1
        if DRY:
            print(f"[GAPFILL]   [DRY] {o['amazon_order_id']}: would fill {sorted(found)}")
            continue
        sets = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE closing_orders SET {sets}, updated_at=datetime('now') WHERE amazon_order_id=?",
            [*updates.values(), o["amazon_order_id"]])
        if i % 25 == 0:
            conn.commit()
            print(f"[GAPFILL]   {i}/{len(orders)} scanned, {touched} recovered so far…")
    conn.commit()
    conn.close()

    print(f"[GAPFILL] ✅ DONE — {touched} orders recovered from live Gmail, "
          f"{still} genuinely not found (of {len(orders)} scanned)")
    for k in sorted(stats):
        print(f"[GAPFILL]     {k:22} +{stats[k]}")
    if LIMIT and total_gaps > LIMIT:
        print(f"[GAPFILL]   ({total_gaps - LIMIT} older gap orders remain — next runs drain them)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
