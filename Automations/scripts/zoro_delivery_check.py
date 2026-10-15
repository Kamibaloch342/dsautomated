#!/usr/bin/env python3
"""
zoro_delivery_check.py — confirm deliveries from Zoro's own order tracking (27 Sep 2026).

Replaces the FedEx page tiers (Akamai-blocked since 19 Sep) as the delivery source. For every
shipped, undelivered order it asks Zoro's guest lookup (engine/zoro_tracking.py) with the WB
number and the e-mail of the Zoro account that placed it, then Zoro's shipment-tracking for
each parcel, and writes back:

  closing_orders      delivery_status (never backwards; Delivered only when EVERY parcel is),
                      delivery_date, last_scan_time / last_scan_location / delivery_note,
                      left_at / signed_by, estimated_window, last_movement_at, stalled_days,
                      zoro_order_status, delivery_source='zoro', delivery_checked_at,
                      gap-fills Zoro knows and closing lacked: tracking_ids, coupon_used
                      (promo code), zoro_order_total / subtotal / tax / shipping,
                      zoro_buyer_zip, zoro_sku / zoro_qty, zoro_deliver_by (Zoro's promise),
                      then profit + order_label through closing.py's own functions
  zoro_package_tracking   one row per parcel with its scan history

The dashboard's Attention → Delivery lists come straight from these columns: LATE =
deliver-by passed by ≥ late_days and undelivered; STALLED = no carrier movement for
≥ stalled_days and undelivered. The delay notice (engine/post_delivery.py) reads the same.

Usage:
  ../.venv/bin/python scripts/zoro_delivery_check.py                       # both accounts
  ZORO_TRACK_ACCOUNT=HOANK ../.venv/bin/python scripts/zoro_delivery_check.py
  ../.venv/bin/python scripts/zoro_delivery_check.py --probe WB4184658338 salesmercentile@gmail.com
Env:  ZORO_TRACK_ACCOUNT (ALL) · ZORO_TRACK_DAYS (60) · ZORO_TRACK_LIMIT (0 = all) ·
      ZORO_TRACK_RECHECK_HOURS (20) · ZORO_TRACK_DELAY (0.4 s between calls, all workers) ·
      ZORO_TRACK_WORKERS (4; the browser transport always runs 1) ·
      ZORO_TRACK_TRANSPORT (auto|http|browser) · ZORO_TRACK_DRY=1 (fetch + parse, write nothing) ·
      ZORO_TRACK_ORDERS=WB…,WB… (only these, whatever their state) ·
      ZORO_TRACK_FULL=1 (always re-read the order, not just its known parcels) · ZORO_TRACK_DUMP_DIR
Exit codes: 0 ok · 2 Zoro refused the transport (nothing was marked) · 3 already running
"""
import json
import os
import sqlite3
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)
print = __import__("functools").partial(print, flush=True)

from utils.accounts_config import (accounts, acquire_lock, release_lock, gmail_addresses,
                                   zoro_profile_inboxes, zoro_tracking_cfg)
from utils.unified_db import (ensure_zoro_tracking_schema, upsert_package_tracking,
                              start_closing_run, update_closing_run, finish_closing_run)
from engine.zoro_tracking import ZoroTracker, ZoroRefused, fmt_ts
from engine.fedex_api import STATUS_RANK

DB = os.path.join(_AUTO, "data", "automations_unified.db")
CFG = zoro_tracking_cfg()
ACCOUNT = os.environ.get("ZORO_TRACK_ACCOUNT", "ALL").strip() or "ALL"
DAYS = int(os.environ.get("ZORO_TRACK_DAYS") or CFG.get("days", 60))
LIMIT = int(os.environ.get("ZORO_TRACK_LIMIT") or 0)
RECHECK_H = float(os.environ.get("ZORO_TRACK_RECHECK_HOURS") or CFG.get("recheck_hours", 20))
DELAY = float(os.environ.get("ZORO_TRACK_DELAY") or CFG.get("delay_s", 0.4))
WORKERS = max(1, int(os.environ.get("ZORO_TRACK_WORKERS") or CFG.get("workers", 4)))
TRANSPORT = (os.environ.get("ZORO_TRACK_TRANSPORT") or CFG.get("transport", "auto")).strip().lower()
PORT = int(os.environ.get("ZORO_TRACK_PORT") or CFG.get("port", 9225))
DRY = os.environ.get("ZORO_TRACK_DRY", "").strip().lower() in ("1", "true", "yes")
FULL = os.environ.get("ZORO_TRACK_FULL", "").strip().lower() in ("1", "true", "yes")
ONLY = [x.strip().upper() for x in os.environ.get("ZORO_TRACK_ORDERS", "").replace("\n", ",").split(",") if x.strip()]
DUMP_DIR = os.environ.get("ZORO_TRACK_DUMP_DIR", "").strip()
DELIVERED = ("Delivered", "AllDelivered")
MAX_EMAIL_TRIES = 4          # primary candidates first, then the busiest Zoro inboxes


def _empty(v):
    return v is None or str(v).strip().lower() in ("", "none")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _split_ids(s):
    out, seen = [], set()
    for t in str(s or "").replace(";", ",").split(","):
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ── which e-mail unlocks the order ───────────────────────────────────────────
_BUSY_INBOXES = None


def busy_inboxes(conn):
    """Zoro inboxes by how many confirmations they received lately — the fallback order when
    an order's own account is unknown (only accounts that actually buy on Zoro are tried)."""
    global _BUSY_INBOXES
    if _BUSY_INBOXES is None:
        addr = gmail_addresses()
        out = []
        try:
            for r in conn.execute("SELECT gmail_account, COUNT(*) FROM archive_zoro "
                                  "WHERE email_timestamp >= date('now','-120 day') GROUP BY 1 ORDER BY 2 DESC"):
                e = addr.get(str(r[0] or "").strip())
                if e and e not in out:
                    out.append(e)
        except sqlite3.Error:
            pass
        for tok in zoro_profile_inboxes().values():
            e = addr.get(tok)
            if e and e not in out:
                out.append(e)
        _BUSY_INBOXES = out
    return _BUSY_INBOXES


def email_candidates(conn, row):
    """(primary, all): the Zoro account e-mail for this order — the inbox that received Zoro's
    confirmation (closing's zoro_gmail_account / archive_zoro.gmail_account), the order's Zoro
    account name, the placer profile's inbox — then the busiest Zoro inboxes as a last resort."""
    addr = gmail_addresses()
    cands = []

    def add(token):
        e = addr.get(str(token or "").strip())
        if e and e not in cands:
            cands.append(e)
    add(row.get("zoro_gmail_account")); add(row.get("zoro_account_name"))
    if not cands:
        for r in conn.execute("SELECT gmail_account, zoro_account_name FROM archive_zoro WHERE wb_order_id=? "
                              "ORDER BY email_timestamp DESC LIMIT 2", (row["wb_order_id"],)):
            add(r[0]); add(r[1])
    if not cands:
        p = conn.execute("SELECT profile_name FROM order_placer_orders WHERE amazon_order_id=?",
                         (row["amazon_order_id"],)).fetchone()
        if p and p[0]:
            add(zoro_profile_inboxes().get(str(p[0]).strip().upper()))
    primary = list(cands)
    for e in busy_inboxes(conn):
        if e not in cands:
            cands.append(e)
    return primary, cands[:max(len(primary), MAX_EMAIL_TRIES)]


# ── candidates ───────────────────────────────────────────────────────────────
_COLS = ("amazon_order_id, wb_order_id, merchant_order_id, account, order_status, order_label, "
         "delivery_status, delivery_date, deliver_by_date, shipped_email_time, purchase_date, tracking_ids, "
         "zoro_account_name, zoro_gmail_account, zoro_deliver_by, earnings, zoro_order_total, zoro_tax, "
         "zoro_subtotal, zoro_shipping, zoro_buyer_zip, coupon_used, coupon_savings, zoro_sku, zoro_qty, "
         "profit_dollars, return_requested, refund_done, last_movement_at, zoro_order_status, delivery_checked_at, "
         "delivery_note, carrier")
_LIVE_COLS = set()


def _live_columns(conn):
    _LIVE_COLS.update(r[1] for r in conn.execute("PRAGMA table_info(closing_orders)"))
    return _LIVE_COLS


def load_candidates(conn):
    accts = list(accounts()) if ACCOUNT.upper() in ("ALL", "BOTH") else [ACCOUNT]
    ph = ",".join("?" * len(accts))
    if ONLY:
        where = (f"account IN ({ph}) AND COALESCE(wb_order_id,'') != '' AND "
                 f"(wb_order_id IN ({','.join('?' * len(ONLY))}) OR amazon_order_id IN ({','.join('?' * len(ONLY))})) ")
        args = [*accts, *ONLY, *ONLY]
    else:
        # Refunded orders are checked too (13 Oct 2026): a shipped order refunded on Amazon still
        # needs its delivery — delivered means the buyer has the item AND the refund (ask for the
        # return / file a claim), not delivered means Zoro owes the refund. WB4834992254 was
        # delivered 16 Sep yet showed "in transit" for a month. The label cannot move: every
        # Refunded row carries return_requested = refund_done = 'Yes', which closing.determine_label
        # keeps Refunded; messaging and the Late/Stalled alerts already skip Refunded orders.
        where = (f"account IN ({ph}) AND COALESCE(wb_order_id,'') != '' "
                 f"AND COALESCE(delivery_status,'') NOT IN ('Delivered','AllDelivered') "
                 f"AND COALESCE(order_label,'') != 'Cancelled' "
                 f"AND COALESCE(order_status,'') != 'Cancelled' "
                 f"AND (order_status='Shipped' OR COALESCE(shipped_email_time,'') != '') "
                 f"AND substr(purchase_date,1,10) >= date('now', ?) "
                 f"AND (delivery_checked_at IS NULL OR delivery_checked_at = '' "
                 f"     OR delivery_checked_at < datetime('now','localtime', ?)) ")
        args = [*accts, f"-{DAYS} day", f"-{RECHECK_H} hours"]
    rows = [dict(r) for r in conn.execute(
        f"SELECT {_COLS} FROM closing_orders WHERE {where} "
        f"ORDER BY CASE WHEN COALESCE(deliver_by_date,'')!='' AND date(deliver_by_date) < date('now') THEN 0 ELSE 1 END, "
        f"purchase_date DESC", args)]
    if LIMIT:
        rows = rows[:LIMIT]
    return rows


def known_parcels(conn, wb):
    return [{"tracking_number": r[0], "carrier_code": r[1] or ""} for r in conn.execute(
        "SELECT tracking_number, carrier FROM zoro_package_tracking WHERE wb_order_id=?", (wb,))]


# ── persist ──────────────────────────────────────────────────────────────────
def persist(conn, row, result, now_s):
    """Write one order's result. Returns the new label ('' when the label did not change)."""
    import closing as C
    agg, pkgs, order = result["aggregate"], result["packages"], result.get("order")
    aid, wb = row["amazon_order_id"], row["wb_order_id"]
    sets, vals = ["delivery_source='zoro'", "delivery_checked_at=?"], [now_s]
    o = dict(row)
    zoro_cancelled = False
    if order:
        if not _empty(order.get("order_status")):
            sets.append("zoro_order_status=?"); vals.append(str(order["order_status"])[:80])
            zoro_cancelled = str(order["order_status"]).strip().lower() == "cancelled"
        if zoro_cancelled and not pkgs:
            # Zoro cancelled the supplier order while Amazon still shows it shipped: the buyer
            # will not receive anything. Loud note, own dashboard bucket, no stall arithmetic.
            sets.append("delivery_note=?"); vals.append("Zoro order CANCELLED at Zoro — the buyer will not receive it; refund or re-place")
            sets.append("stalled_days=NULL"); sets.append("last_movement_at=NULL")
        t = order.get("totals") or {}
        # facts Zoro states about its own order that closing may have missed
        gap = {}
        if _empty(row.get("coupon_used")) and t.get("promo_code"):
            gap["coupon_used"] = str(t["promo_code"])[:60]
        if _empty(row.get("coupon_savings")) and _num(t.get("discount")):
            gap["coupon_savings"] = _num(t.get("discount"))
        if not (_num(row.get("zoro_order_total")) or 0) and _num(t.get("total")):
            gap["zoro_order_total"] = _num(t.get("total"))
        if _empty(row.get("zoro_subtotal")) and _num(t.get("subtotal")) is not None:
            gap["zoro_subtotal"] = _num(t.get("subtotal"))
        if _empty(row.get("zoro_tax")) and _num(t.get("tax")) is not None:
            gap["zoro_tax"] = _num(t.get("tax"))
        if _empty(row.get("zoro_shipping")) and _num(t.get("shipping")) is not None:
            gap["zoro_shipping"] = (_num(t.get("shipping")) or 0) + (_num(t.get("freight")) or 0)
        zipc = (order.get("ship_to") or {}).get("zip") or ""
        if _empty(row.get("zoro_buyer_zip")) and zipc:
            gap["zoro_buyer_zip"] = str(zipc)[:12]
        if _empty(row.get("zoro_sku")) and order.get("line_items"):
            gap["zoro_sku"] = ", ".join(li["zoro_no"] for li in order["line_items"] if li.get("zoro_no"))[:200]
        if not (_num(row.get("zoro_qty")) or 0) and order.get("line_items"):
            q = sum(int(_num(li.get("qty")) or 0) for li in order["line_items"])
            if q:
                gap["zoro_qty"] = q
        if _empty(row.get("zoro_deliver_by")) and order.get("expected_delivery"):
            gap["zoro_deliver_by"] = order["expected_delivery"][:10]
        for k, v in gap.items():
            sets.append(f"{k}=?"); vals.append(v)
            o[k] = v
    prev = str(row.get("delivery_status") or "").strip()
    new = agg["delivery_status"]
    is_downgrade_exception = (new == "DeliveryException" and ("return" in str(agg.get("delivery_note") or "").lower() or "damaged" in str(agg.get("delivery_note") or "").lower()))
    if new and new not in ("Unknown", "Blocked"):
        # Never move an order backwards unless the new answer is a delivery or an RTS/DeliveryException.
        if new in DELIVERED or not prev or STATUS_RANK.get(new, 0) >= STATUS_RANK.get(prev, 0) or is_downgrade_exception:
            o["delivery_status"] = new
            sets.append("delivery_status=?"); vals.append(new)
            sets.append("enriched_from_fedex=1")
            for col in ("delivery_date", "last_scan_time", "last_scan_location", "delivery_note",
                        "estimated_window", "left_at", "signed_by"):
                v = agg.get(col) or ""
                if v:
                    sets.append(f"{col}=?"); vals.append(str(v)[:300])
                    o[col] = v
                elif is_downgrade_exception and col in ("delivery_date", "left_at", "signed_by"):
                    sets.append(f"{col}=NULL")
                    o[col] = None
    if pkgs and agg.get("untracked") == len(pkgs):
        # Freight: the order stays unconfirmed, but say why (dashboard bucket, no alerts), and
        # clear any stall a FedEx-era status left behind — there are no scans to stall on.
        if _empty(row.get("delivery_note")) or "not trackable" in str(row.get("delivery_note") or ""):
            sets.append("delivery_note=?"); vals.append(agg["delivery_note"][:300])
        if "carrier" in _LIVE_COLS and _empty(row.get("carrier")):
            sets.append("carrier=?"); vals.append(", ".join(sorted({p["carrier_code"] for p in pkgs if p["carrier_code"]}))[:80])
        sets.append("stalled_days=NULL"); sets.append("last_movement_at=NULL")
    if agg.get("last_movement_at"):
        sets.append("last_movement_at=?"); vals.append(agg["last_movement_at"])
    if agg.get("stalled_days") is not None:
        sets.append("stalled_days=?"); vals.append(int(agg["stalled_days"]))
    elif o.get("delivery_status") in DELIVERED:
        sets.append("stalled_days=0")
    # tracking numbers Zoro knows that closing never captured (the ~20 % FedEx could not resolve)
    zoro_ids = [p["tracking_number"] for p in pkgs if p.get("tracking_number")]
    have = _split_ids(row.get("tracking_ids"))
    merged = have + [t for t in zoro_ids if t not in have]
    if merged != have:
        sets.append("tracking_ids=?"); vals.append(", ".join(merged))
        o["tracking_ids"] = ", ".join(merged)
    # label + profit through closing's own rules
    pd, pp = C.calculate_profit(o)
    if pd is not None:
        o["profit_dollars"], o["profit_percent"] = pd, pp
        sets.append("profit_dollars=?"); vals.append(pd)
        sets.append("profit_percent=?"); vals.append(pp)
    label = C.determine_label(o)
    if row.get("order_label") == "Refunded" and label != "Refunded":
        label = ""      # a refunded order keeps its label — the sweep only adds where the parcel went
    allow_downgrade = is_downgrade_exception and row.get("order_label") in ("Completed", "Loss")
    if label and label != row.get("order_label") and (
            allow_downgrade or not (
                label == "Incomplete" and row.get("order_label") in ("Completed", "Loss", "Refunded", "Cancelled"))):
        sets.append("order_label=?"); vals.append(label)
    else:
        label = ""
    vals.append(aid)
    conn.execute(f"UPDATE closing_orders SET {', '.join(sets)} WHERE amazon_order_id=?", vals)
    if pkgs:
        upsert_package_tracking([{
            "wb_order_id": wb, "tracking_number": p["tracking_number"], "amazon_order_id": aid,
            "carrier": p.get("carrier_code") or p.get("carrier") or "", "status": p["status"],
            "delivered_at": fmt_ts(p["delivered_at"], seconds=True), "shipped_at": fmt_ts(p["shipped_at"], seconds=True),
            "last_event": (p["last_event"] or "")[:200], "last_event_at": fmt_ts(p["last_event_at"], seconds=True),
            "last_exception": (p["last_exception"] or p.get("error") or "")[:200],
            "last_location": (p["last_location"] or "")[:120], "last_movement_at": fmt_ts(p["last_movement_at"], seconds=True),
            "estimated_delivery": p["estimated_delivery"], "delayed": 1 if p["delayed"] else 0,
            "n_events": p["n_events"], "events_json": json.dumps(p["events"], default=str)[:20000],
            "url": (p["url"] or "")[:300], "checked_at": now_s} for p in pkgs], conn=conn)
    return label


# ── probe (one order, print everything) ──────────────────────────────────────
def probe(order_no, email):
    tr = ZoroTracker(transport=TRANSPORT, port=PORT, delay_s=DELAY,
                     dump_dir=DUMP_DIR or os.path.join(_AUTO, "data", "dataset", "zoro_tracking_samples"))
    try:
        res = tr.track_order(order_no, email)
        print(f"{order_no} ({email}) → {res['outcome']} via {tr.mode}, {tr.calls} call(s)")
        if res["outcome"] != "ok":
            print("  payload:", json.dumps(res.get("error"), indent=1, default=str)[:1500])
            return 1
        o = res["order"] or {}
        print(f"  Zoro status={o.get('order_status')!r} SO={o.get('sales_order')} placed={o.get('created_at')} "
              f"method={o.get('shipping_method')!r} promo={o.get('totals', {}).get('promo_code')!r} "
              f"total={o.get('totals', {}).get('total')} expected_delivery={o.get('expected_delivery')}")
        print(f"  ship-to: {o.get('ship_to')}")
        print(f"  items: {[(li['zoro_no'], li['qty'], li['expected_delivery']) for li in o.get('line_items', [])]}")
        for p in res["packages"]:
            print(f"  📦 {p['tracking_number']} [{p['carrier_code']}/{p['carrier']}] {p['status']}  "
                  f"delivered={fmt_ts(p['delivered_at'])} shipped={fmt_ts(p['shipped_at'])} "
                  f"last={p['last_event']!r} @ {fmt_ts(p['last_event_at'])} {p['last_location']} "
                  f"movement={fmt_ts(p['last_movement_at'])} left_at={p['left_at']!r} err={p.get('error')!r}")
            for e in p["events"][:5]:
                print(f"       {e['at']}  {e['desc']}  {e['location']}  {e['exception']}")
        print("  aggregate:", res["aggregate"])
        if res.get("failures"):
            print("  ⚠️ package lookups that failed:", res["failures"])
        print(f"  raw payloads saved under {tr.dump_dir}")
        return 0
    finally:
        tr.close()


# ── main sweep ───────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--probe":
        return probe(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    lock = f"zoro_delivery_{ACCOUNT}"
    got, holder = acquire_lock(lock)
    if not got:
        print(f"[ZORO-TRACK] ❌ another delivery check for {ACCOUNT} is running (pid {holder}) — aborting")
        return 3
    try:
        return _run()
    finally:
        release_lock(lock)


def _pipeline(items, fn, workers):
    """Run fn over items on `workers` threads, yielding (item, result) IN ORDER with a bounded
    number in flight, so the main thread can persist while the others fetch."""
    with ThreadPoolExecutor(max_workers=workers) as ex:
        pending = deque()
        for item in items:
            pending.append((item, ex.submit(fn, item)))
            if len(pending) >= workers * 3:
                it, fut = pending.popleft()
                yield it, fut.result()
        while pending:
            it, fut = pending.popleft()
            yield it, fut.result()


def _run():
    run_id = f"zoro-{ACCOUNT}-{datetime.now():%Y%m%d-%H%M%S}"
    t0 = time.time()
    added = ensure_zoro_tracking_schema()
    if added:
        print(f"[ZORO-TRACK] schema: added closing_orders.{', '.join(added)}")
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    _live_columns(conn)
    rows = load_candidates(conn)
    workers = 1 if TRANSPORT == "browser" else WORKERS
    print(f"[ZORO-TRACK] {ACCOUNT}: {len(rows)} shipped, undelivered order(s) to check "
          f"(last {DAYS} d, rechecked after {RECHECK_H:g} h, {TRANSPORT} transport, {DELAY}s pace, "
          f"{workers} worker(s){', DRY' if DRY else ''}{', only ' + ','.join(ONLY) if ONLY else ''})")
    if not rows:
        print("[ZORO-TRACK] ✅ nothing to check")
        conn.close()
        return 0
    try:
        start_closing_run(run_id, datetime.now().date().isoformat(), datetime.now().date().isoformat(), [ACCOUNT])
    except Exception:
        pass
    tr = ZoroTracker(transport=TRANSPORT, port=PORT, delay_s=DELAY, dump_dir=DUMP_DIR)
    stats = {"checked": 0, "delivered": 0, "moving": 0, "exception": 0, "not_found": 0, "no_parcels": 0,
             "errors": 0, "tracking_filled": 0, "cost_filled": 0, "labels": 0, "fallback_email": 0, "parcel_only": 0}
    blocked = False

    def prepared():
        # DB reads stay on the main thread: each work item carries everything the fetch needs.
        for row in rows:
            parcels = []
            if not FULL and str(row.get("zoro_order_status") or "").strip().lower() == "complete":
                parcels = known_parcels(conn, row["wb_order_id"])   # fully shipped: no new parcels can appear
            primary, cands = ([], []) if parcels else email_candidates(conn, row)
            yield row, parcels, primary, cands

    def fetch(item):
        row, parcels, primary, cands = item
        if parcels:
            return tr.track_order(row["wb_order_id"], "", known_parcels=parcels), "", True
        result = None
        own_ids = _split_ids(row.get("tracking_ids"))     # from Zoro's shipping e-mail, via closing
        for email in cands:
            result = tr.track_order(row["wb_order_id"], email, fallback_tracking=own_ids)
            if result["outcome"] == "ok":
                return result, email, False
            if result["outcome"] != "not_found":
                break
        if result is None:
            result = {"outcome": "invalid", "error": {"error": "no e-mail candidates"}, "order": None,
                      "packages": [], "aggregate": {"delivery_status": ""}}
        return result, "", False

    try:
        for i, (item, (result, used, parcel_only)) in enumerate(_pipeline(prepared(), fetch, workers), 1):
            row, parcels, primary, cands = item
            now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if result["outcome"] != "ok":
                oc = result["outcome"]
                stats["not_found" if oc == "not_found" else "errors"] += 1
                if oc in ("not_found", "invalid") and not DRY:
                    # Say why on the row (dashboard "untracked" bucket) without touching a real note.
                    why = (f"Zoro: order not found under {', '.join(cands)}" if oc == "not_found"
                           else f"Zoro lookup skipped: {str(result.get('error', {}).get('error', ''))[:80]}")
                    conn.execute("UPDATE closing_orders SET delivery_checked_at=?, "
                                 "delivery_note=CASE WHEN COALESCE(delivery_note,'')='' OR delivery_note LIKE 'Zoro%' "
                                 "THEN ? ELSE delivery_note END WHERE amazon_order_id=?",
                                 (now_s, why[:300], row["amazon_order_id"]))
                if oc not in ("not_found", "invalid"):
                    print(f"  {row['wb_order_id']}: {oc} {str(result.get('error'))[:120]}")
                elif DRY:
                    print(f"  [DRY] {row['wb_order_id']}: {oc} after {', '.join(cands) or 'no e-mails'}")
            else:
                agg = result["aggregate"]
                stats["checked"] += 1
                if parcel_only:
                    stats["parcel_only"] += 1
                elif used and used not in primary:
                    stats["fallback_email"] += 1
                if not result["packages"]:
                    stats["no_parcels"] += 1
                if agg["delivery_status"] in DELIVERED:
                    stats["delivered"] += 1
                elif agg["delivery_status"] == "DeliveryException":
                    stats["exception"] += 1
                elif agg["delivery_status"]:
                    stats["moving"] += 1
                if DRY:
                    print(f"  [DRY] {row['wb_order_id']} ({used or 'parcels known'}) → {agg['delivery_status'] or '—'} "
                          f"pkgs={len(result['packages'])} delivered={agg['delivered']} last={agg['last_scan_time']} "
                          f"{agg['last_scan_location']} stalled={agg['stalled_days']} "
                          f"promo={(result.get('order') or {}).get('totals', {}).get('promo_code', '')!r} "
                          f"zoro_status={(result.get('order') or {}).get('order_status', '')!r}")
                else:
                    had_tracking = bool(_split_ids(row.get("tracking_ids")))
                    had_cost = bool(_num(row.get("zoro_order_total")) or 0)
                    label = persist(conn, row, result, now_s)
                    if not had_tracking and result["packages"]:
                        stats["tracking_filled"] += 1
                    if not had_cost and result.get("order") and _num((result["order"].get("totals") or {}).get("total")):
                        stats["cost_filled"] += 1
                    if label:
                        stats["labels"] += 1
            if i % 25 == 0:
                conn.commit()
            if i % 100 == 0 or i == len(rows):
                el = time.time() - t0
                print(f"  {i}/{len(rows)} · delivered {stats['delivered']} · moving {stats['moving']} · exceptions "
                      f"{stats['exception']} · not found {stats['not_found']} · {el / max(i, 1):.2f}s/order · "
                      f"ETA {(len(rows) - i) * el / max(i, 1) / 60:.0f} min")
    except ZoroRefused as e:
        blocked = True
        print(f"[ZORO-TRACK] 🚫 {e} — stopping; nothing was marked for the remaining orders")
    finally:
        conn.commit()
        conn.close()
        tr.close()
    secs = round(time.time() - t0, 1)
    print(f"[ZORO-TRACK] ✅ DONE in {secs}s — checked {stats['checked']} (parcel-only {stats['parcel_only']}), "
          f"delivered {stats['delivered']}, moving {stats['moving']}, exceptions {stats['exception']}, "
          f"not found {stats['not_found']}, no parcels {stats['no_parcels']}, errors {stats['errors']}, "
          f"tracking filled {stats['tracking_filled']}, cost filled {stats['cost_filled']}, labels changed {stats['labels']}"
          f"{', fallback e-mail used ' + str(stats['fallback_email']) if stats['fallback_email'] else ''}"
          f" · {tr.calls} calls via {tr.mode}")
    try:
        update_closing_run(run_id, {"orders_processed": len(rows), "orders_enriched": stats["checked"],
                                    "fedex_hits": stats["delivered"], "errors": stats["errors"],
                                    "error_details": json.dumps({"kind": "zoro_delivery", "stats": stats,
                                                                 "seconds": secs, "transport": tr.mode,
                                                                 "blocked": blocked, "dry": DRY})})
        finish_closing_run(run_id, "failed" if blocked else "completed")
    except Exception:
        pass
    return 2 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
