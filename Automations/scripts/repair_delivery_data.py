#!/usr/bin/env python3
"""
repair_delivery_data.py — undo two known bad writes to closing_orders. Reversible.

A. FedEx statuses written by the old tracking code (19 Sep 2026).
   Before commit 3389e11 the scraper stored whatever word it found on FedEx's page, and could
   not tell "FedEx refused us" from "not delivered yet". That left ~1,200 rows holding raw
   FedEx wording ("On the way", "Delivery updated", "Shipment exception") and ~4,750 holding
   "Unknown", which is not a status at all. Today's normalize_status() maps the wording to the
   canonical set; anything it cannot place — and "Unknown"/"Blocked" — becomes blank, so the
   order is simply looked up again instead of carrying a fact nobody established.
   enriched_from_fedex is cleared on every row touched, for the same reason.

B. The rows scripts/test_pipeline_orders.py wrote on 19 Sep 2026 when it was run by mistake.
   It drove a live Seller Central session over six HOANK orders and wrote what the then-broken
   FedEx tier returned. Compared against data/automations_unified.db.backup_repair (8 Sep):
     112-5802763-4772250  AllDelivered -> "Blocked"   (restore)
     114-0632174-2905805  Cancelled    -> "Delivered" (restore)
     113-1804611-6381827  no row       -> created     (delete; the order is Unshipped)
     114-6547048-4310631  AllDelivered -> "Delivered" (left alone — same meaning)
   It also ran twice, so its order_returns / order_feedback inserts are duplicated; only rows
   identical in every column but the id are removed.

Every change is written to data/repairs/undo_<stamp>.json first, so it can be put back.

Usage:
    python scripts/repair_delivery_data.py            # dry run — shows the plan, writes nothing
    python scripts/repair_delivery_data.py --apply    # make the changes
    python scripts/repair_delivery_data.py --undo data/repairs/undo_<stamp>.json
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

DB = os.path.join(AUTO, "data", "automations_unified.db")
REPAIR_DIR = os.path.join(AUTO, "data", "repairs")

CANONICAL = ("Delivered", "AllDelivered", "PartialDelivered", "InTransit",
             "OutForDelivery", "LabelCreated", "DeliveryException", "Cancelled")
NOT_A_STATUS = ("Unknown", "Blocked")

# B: what the 8 Sep backup holds for the rows the pipeline test overwrote
RESTORE = {"112-5802763-4772250": "AllDelivered", "114-0632174-2905805": "Cancelled"}
CREATED_BY_TEST = "113-1804611-6381827"
TEST_ORDER_IDS = ("112-8246579-0124267", "112-5802763-4772250", "114-6547048-4310631",
                  "113-1804611-6381827", "114-0632174-2905805", "111-1105746-6925831")


def _conn(read_only):
    if read_only:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    else:
        c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=60000")
    return c


def plan_statuses(c):
    """[(amazon_order_id, current, replacement)] for every non-canonical status."""
    from engine.fedex_api import normalize_status
    rows = c.execute(
        "SELECT amazon_order_id, delivery_status FROM closing_orders "
        "WHERE COALESCE(delivery_status,'')!='' AND delivery_status NOT IN (%s)"
        % ",".join("?" * len(CANONICAL)), CANONICAL).fetchall()
    out = []
    for r in rows:
        cur = r["delivery_status"]
        new = "" if cur in NOT_A_STATUS else normalize_status(cur)
        if new == "Unknown":
            new = ""                      # wording today's code still cannot place
        if new != cur:
            out.append((r["amazon_order_id"], cur, new))
    return out


def plan_duplicates(c, table):
    """ids of rows identical to an earlier one in every column but the id, for the test orders."""
    cols = [d[1] for d in c.execute(f"PRAGMA table_info({table})")]
    keys = [x for x in cols if x != "id"]
    if not keys:
        return []
    ph = ",".join("?" * len(TEST_ORDER_IDS))
    drop = []
    for d in c.execute(
            f"SELECT MIN(id) keep, GROUP_CONCAT(id) ids FROM {table} "
            f"WHERE amazon_order_id IN ({ph}) GROUP BY {', '.join(keys)} HAVING COUNT(*)>1",
            TEST_ORDER_IDS):
        drop += [int(x) for x in d["ids"].split(",") if int(x) != d["keep"]]
    return sorted(drop)


def report(c):
    statuses = plan_statuses(c)
    print(f"A. FedEx statuses — {len(statuses)} row(s) to rewrite")
    for (was, new), n in Counter((s[1], s[2]) for s in statuses).most_common():
        shown = repr(new) if new else "(blank — look it up again)"
        print(f"     {n:6d}  {was!r:28s} -> {shown}")

    print("\nB. the accidental pipeline-test writes")
    for oid, was in RESTORE.items():
        r = c.execute("SELECT delivery_status FROM closing_orders WHERE amazon_order_id=?", (oid,)).fetchone()
        print(f"     {oid}: {(r['delivery_status'] if r else None)!r} -> {was!r}")
    r = c.execute("SELECT order_status FROM closing_orders WHERE amazon_order_id=?", (CREATED_BY_TEST,)).fetchone()
    print(f"     {CREATED_BY_TEST}: " + (f"row exists (order_status {r['order_status']!r}) -> delete"
                                         if r else "no row — nothing to delete"))
    dupes = {t: plan_duplicates(c, t) for t in ("order_returns", "order_feedback")}
    for t, ids in dupes.items():
        print(f"     {t}: {len(ids)} exact duplicate row(s) -> delete")
    return statuses, dupes


def apply(c, statuses, dupes):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(REPAIR_DIR, exist_ok=True)
    undo = {"created": stamp, "status_was": [], "deleted": {}}

    undo["status_was"] = [{"id": a, "was": was} for a, was, _ in statuses]
    for oid in RESTORE:
        r = c.execute("SELECT delivery_status FROM closing_orders WHERE amazon_order_id=?", (oid,)).fetchone()
        if r:
            undo["status_was"].append({"id": oid, "was": r["delivery_status"]})
    row = c.execute("SELECT * FROM closing_orders WHERE amazon_order_id=?", (CREATED_BY_TEST,)).fetchone()
    if row:
        undo["deleted"]["closing_orders"] = [dict(row)]
    for t, ids in dupes.items():
        if ids:
            undo["deleted"][t] = [dict(r) for r in c.execute(
                f"SELECT * FROM {t} WHERE id IN ({','.join('?' * len(ids))})", ids)]

    path = os.path.join(REPAIR_DIR, f"undo_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(undo, f, indent=1, default=str)
    print(f"  undo written: {os.path.relpath(path, AUTO)}")

    with c:
        c.executemany("UPDATE closing_orders SET delivery_status=?, enriched_from_fedex=0 "
                      "WHERE amazon_order_id=?", [(new, a) for a, _, new in statuses])
        for oid, was in RESTORE.items():
            c.execute("UPDATE closing_orders SET delivery_status=? WHERE amazon_order_id=?", (was, oid))
        if row:
            c.execute("DELETE FROM closing_orders WHERE amazon_order_id=?", (CREATED_BY_TEST,))
        for t, ids in dupes.items():
            if ids:
                c.execute(f"DELETE FROM {t} WHERE id IN ({','.join('?' * len(ids))})", ids)
    print(f"  applied: {len(statuses)} status rewrites, {len(RESTORE)} restores, "
          f"{1 if row else 0} row deleted, {sum(len(v) for v in dupes.values())} duplicates removed")
    return path


def undo(path):
    data = json.load(open(path, encoding="utf-8"))
    c = _conn(read_only=False)
    with c:
        for row in data.get("status_was", []):
            c.execute("UPDATE closing_orders SET delivery_status=? WHERE amazon_order_id=?",
                      (row["was"], row["id"]))
        for table, rows in (data.get("deleted") or {}).items():
            for r in rows:
                cols = list(r.keys())
                c.execute(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) "
                          f"VALUES ({','.join('?' * len(cols))})", [r[k] for k in cols])
    print(f"  reverted {len(data.get('status_was', []))} status value(s) and "
          f"{sum(len(v) for v in (data.get('deleted') or {}).values())} deleted row(s)")
    c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    ap.add_argument("--undo", metavar="FILE", help="put back what an earlier --apply changed")
    a = ap.parse_args()

    if a.undo:
        return undo(a.undo)

    c = _conn(read_only=not a.apply)
    statuses, dupes = report(c)
    if not a.apply:
        print("\n  dry run — nothing written. Re-run with --apply to make these changes.")
        c.close()
        return
    print()
    apply(c, statuses, dupes)
    print("\n  shipped orders by status now:")
    for r in c.execute("SELECT COALESCE(NULLIF(delivery_status,''),'(blank)') s, COUNT(*) n "
                       "FROM closing_orders WHERE order_status='Shipped' GROUP BY 1 ORDER BY n DESC LIMIT 8"):
        print(f"     {r['s']:20s} {r['n']:7d}")
    c.close()


if __name__ == "__main__":
    main()
