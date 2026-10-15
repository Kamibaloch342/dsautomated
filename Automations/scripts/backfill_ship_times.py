#!/usr/bin/env python3
"""
backfill_ship_times.py — give every shipped order its real Zoro ship time, then its speeds.

15 Oct 2026: the daily coverage report showed zoro_ship_speed missing on ~10 % of shipped orders
(3,400 a month since August). 7,090 of them hold a ship DATE where the ship TIME belongs —
shipped_email_time = 'Tue, Sep 1' — because the tracker records Zoro's ship date, the placer row
passes it to closing, and closing only fills EMPTY fields, so the exact "Has Shipped" e-mail time in
archive_tracking never replaced it. Without a clock time no speed can be computed.

For each shipped order whose ship time has no clock time (or is empty): take the earliest Zoro
shipping e-mail time for its WB (archive_tracking.email_timestamp) and, when missing, the Zoro
order e-mail time (archive_zoro.email_timestamp), then recompute zoro_shipped_pkt / zoro_placed_pkt /
zoro_ship_speed / placement_speed through closing.derive_fields — closing's own rules. Only empty
or date-only values are touched; a real value is never overwritten.

    python scripts/backfill_ship_times.py            # dry run: counts + 5 examples
    python scripts/backfill_ship_times.py --apply    # write
"""
import argparse
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)
DB = os.path.join(AUTO, "data", "automations_unified.db")

_CLOCK = re.compile(r"\d{1,2}:\d{2}")
_WB = re.compile(r"WB\d{6,}")
COLS = ("shipped_email_time", "zoro_email_ts", "sold_email_time", "zoro_shipped_pkt", "zoro_placed_pkt",
        "order_received_pkt", "zoro_ship_speed", "zoro_ship_speed_hrs", "placement_speed", "placement_speed_hrs")


def _blank(v):
    return v is None or str(v).strip() in ("", "None")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--since", default="2026-01-01", help="purchase_date from (YYYY-MM-DD)")
    a = ap.parse_args()
    import closing as C

    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    rows = conn.execute(
        f"SELECT amazon_order_id, wb_order_id, {', '.join(COLS)} FROM closing_orders "
        "WHERE order_status='Shipped' AND purchase_date >= ? "
        "AND (COALESCE(zoro_ship_speed,'')='' OR COALESCE(placement_speed,'')='' "
        "     OR COALESCE(shipped_email_time,'') NOT GLOB '*[0-9]:[0-9][0-9]*')", (a.since,)).fetchall()
    ship_ts, zoro_ts = {}, {}
    for wb, ts in conn.execute("SELECT wb_order_id, MIN(email_timestamp) FROM archive_tracking "
                               "WHERE COALESCE(email_timestamp,'')!='' GROUP BY wb_order_id"):
        ship_ts[wb] = ts
    for wb, ts in conn.execute("SELECT wb_order_id, MIN(email_timestamp) FROM archive_zoro "
                               "WHERE COALESCE(email_timestamp,'')!='' GROUP BY wb_order_id"):
        zoro_ts[wb] = ts

    updates, examples = [], []
    for r in rows:
        o = dict(r)
        wbs = _WB.findall(str(o.get("wb_order_id") or ""))
        if not wbs:
            continue
        before = dict(o)
        st = str(o.get("shipped_email_time") or "")
        if not _CLOCK.search(st):
            t = min((ship_ts[w] for w in wbs if w in ship_ts), default=None)
            if t:
                o["shipped_email_time"] = t
        if not _CLOCK.search(str(o.get("zoro_email_ts") or "")):
            t = min((zoro_ts[w] for w in wbs if w in zoro_ts), default=None)
            if t:
                o["zoro_email_ts"] = t
        C.derive_fields(o)
        sets = {}
        for k in COLS:
            new, old = o.get(k), before.get(k)
            if _blank(new) or new == old:
                continue
            if k in ("shipped_email_time", "zoro_email_ts"):
                if _CLOCK.search(str(old or "")):
                    continue                         # a real time is never overwritten
            elif not _blank(old):
                continue                             # derived fields: fill only when empty
            sets[k] = new
        if sets:
            updates.append((o["amazon_order_id"], sets))
            if len(examples) < 5:
                examples.append((o["amazon_order_id"], {k: (before.get(k), v) for k, v in sets.items()
                                                        if k in ("shipped_email_time", "zoro_ship_speed")}))
    still = sum(1 for r in rows) - len(updates)
    print(f"shipped orders needing a ship time or speed: {len(rows)} | fixable from the e-mails: {len(updates)} "
          f"| no e-mail to take it from: {still}")
    for aid, ch in examples:
        print(f"  {aid}: " + "; ".join(f"{k} {o!r} → {n!r}" for k, (o, n) in ch.items()))
    if not a.apply:
        print("dry run — nothing written (add --apply)")
        return 0
    with conn:
        for aid, sets in updates:
            conn.execute(f"UPDATE closing_orders SET {', '.join(f'{k}=?' for k in sets)} WHERE amazon_order_id=?",
                         [*sets.values(), aid])
    print(f"written: {len(updates)} order(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
