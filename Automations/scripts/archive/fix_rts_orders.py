#!/usr/bin/env python3
"""
fix_rts_orders.py — Correct orders that were returned to shipper (RTS) / damaged in transit.

Before 4 Oct 2026, packages returned to shipper had their final FedEx warehouse drop-off
(e.g. Louisville, KY) parsed as 'Delivered', causing closing to falsely mark them as
'Completed' with positive profit, even though the buyer never received the item.

This script:
1. Identifies packages in zoro_package_tracking with Return to Shipper / Damaged in transit scans.
2. Re-parses package status to 'DeliveryException' and clears false delivery dates.
3. Updates closing_orders (and orders):
   - Sets delivery_status = 'DeliveryException' (or PartialDelivered if other pkgs delivered)
   - Clears delivery_date, left_at, signed_by
   - Sets order_label = 'Incomplete' (protecting orders already marked 'Refunded' or 'Cancelled')
4. Records every change to data/repairs/rts_undo_<timestamp>.json for rollback safety.

Usage:
    python scripts/fix_rts_orders.py --order 114-5148087-5500245          # dry run on single order
    python scripts/fix_rts_orders.py --order 114-5148087-5500245 --apply  # apply to single order
    python scripts/fix_rts_orders.py                                      # dry run on all RTS orders
    python scripts/fix_rts_orders.py --apply                              # apply to all RTS orders
    python scripts/fix_rts_orders.py --undo data/repairs/rts_undo_....json
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

from engine.zoro_tracking import _RTS_RX, parse_package, aggregate, fmt_ts
import closing as C

DB_PATH = os.path.join(AUTO, "data", "automations_unified.db")
REPAIR_DIR = os.path.join(AUTO, "data", "repairs")


def get_conn(read_only=True):
    if read_only:
        return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=60)
    return sqlite3.connect(DB_PATH, timeout=60)


def find_rts_orders(conn, target_order=None):
    """Find orders with RTS scans in zoro_package_tracking."""
    if target_order:
        sql = "SELECT wb_order_id, tracking_number, amazon_order_id, carrier, status, delivered_at, last_event, last_exception, events_json FROM zoro_package_tracking WHERE amazon_order_id=?"
        rows = conn.execute(sql, (target_order,)).fetchall()
    else:
        sql = "SELECT wb_order_id, tracking_number, amazon_order_id, carrier, status, delivered_at, last_event, last_exception, events_json FROM zoro_package_tracking"
        rows = conn.execute(sql).fetchall()

    by_order = {}
    for r in rows:
        aid = r[2]
        if not aid:
            continue
        if aid not in by_order:
            by_order[aid] = []
        by_order[aid].append({
            "wb_order_id": r[0],
            "tracking_number": r[1],
            "amazon_order_id": r[2],
            "carrier": r[3],
            "status": r[4],
            "delivered_at": r[5],
            "last_event": r[6],
            "last_exception": r[7],
            "events_json": r[8],
        })

    # Filter to orders where at least one parcel has an RTS event
    rts_orders = {}
    for aid, pkgs in by_order.items():
        order_has_rts = False
        for p in pkgs:
            events = json.loads(p["events_json"]) if p["events_json"] else []
            for e in events:
                desc = (e.get("desc") or "") + " " + (e.get("exception") or "")
                if _RTS_RX.search(desc):
                    order_has_rts = True
                    break
            if order_has_rts:
                break
        if order_has_rts:
            rts_orders[aid] = pkgs

    return rts_orders


def reparse_packages_for_order(pkgs):
    """Reconstruct package payloads and re-parse with updated engine.zoro_tracking."""
    reparsed = []
    for p in pkgs:
        events = json.loads(p["events_json"]) if p["events_json"] else []
        t = {
            "trackingNumber": p["tracking_number"],
            "carrier": p["carrier"],
            "scanEventsDetails": [
                {
                    "date": e.get("at"),
                    "eventDescription": e.get("desc"),
                    "exceptionDescription": e.get("exception"),
                    "scanLocation": {"city": e.get("location", "")}
                } for e in events
            ],
            # If original had delivered timestamp
            "stepTimestamps": [{"stepName": "delivered", "timestamp": p["delivered_at"]}] if p["delivered_at"] else []
        }
        res = parse_package(t, carrier_code=p["carrier"])
        reparsed.append(res)
    return reparsed


def compute_order_corrections(conn, aid, pkgs):
    """Compute required DB updates for an RTS order."""
    reparsed = reparse_packages_for_order(pkgs)
    agg = aggregate(reparsed)

    co_row = conn.execute(
        "SELECT amazon_order_id, wb_order_id, delivery_status, delivery_date, delivery_note, left_at, signed_by, order_label, earnings, zoro_order_total, profit_dollars, tracking_ids, order_status, return_requested, refund_done FROM closing_orders WHERE amazon_order_id=?",
        (aid,)
    ).fetchone()

    if not co_row:
        return None

    co_dict = {
        "amazon_order_id": co_row[0],
        "wb_order_id": co_row[1],
        "delivery_status": co_row[2],
        "delivery_date": co_row[3],
        "delivery_note": co_row[4],
        "left_at": co_row[5],
        "signed_by": co_row[6],
        "order_label": co_row[7],
        "earnings": co_row[8],
        "zoro_order_total": co_row[9],
        "profit_dollars": co_row[10],
        "tracking_ids": co_row[11],
        "order_status": co_row[12],
        "return_requested": co_row[13],
        "refund_done": co_row[14],
    }

    # Compare closing_orders
    new_delivery_status = agg["delivery_status"]
    new_delivery_date = agg["delivery_date"] or None
    new_delivery_note = agg["delivery_note"] or None

    simulated = dict(co_dict)
    simulated["delivery_status"] = new_delivery_status
    simulated["delivery_date"] = new_delivery_date
    simulated["delivery_note"] = new_delivery_note

    computed_label = C.determine_label(simulated)
    # Never downgrade an order already confirmed Refunded or Cancelled on Amazon
    if co_dict["order_label"] in ("Refunded", "Cancelled"):
        new_label = co_dict["order_label"]
    else:
        new_label = computed_label

    # Check if closing_orders needs update
    co_changes = {}
    if co_dict["delivery_status"] != new_delivery_status:
        co_changes["delivery_status"] = (co_dict["delivery_status"], new_delivery_status)
    if co_dict["delivery_date"] != new_delivery_date:
        co_changes["delivery_date"] = (co_dict["delivery_date"], new_delivery_date)
    if co_dict["delivery_note"] != new_delivery_note:
        co_changes["delivery_note"] = (co_dict["delivery_note"], new_delivery_note)
    if co_dict["left_at"] and new_delivery_status == "DeliveryException":
        co_changes["left_at"] = (co_dict["left_at"], None)
    if co_dict["signed_by"] and new_delivery_status == "DeliveryException":
        co_changes["signed_by"] = (co_dict["signed_by"], None)
    if co_dict["order_label"] != new_label:
        co_changes["order_label"] = (co_dict["order_label"], new_label)

    # Check pkg changes in zoro_package_tracking
    pkg_changes = []
    for orig, rep in zip(pkgs, reparsed):
        tid = orig["tracking_number"]
        p_ch = {}
        if orig["status"] != rep["status"]:
            p_ch["status"] = (orig["status"], rep["status"])
        orig_deliv = orig["delivered_at"] or None
        rep_deliv = fmt_ts(rep["delivered_at"], seconds=True) or None
        if orig_deliv != rep_deliv:
            p_ch["delivered_at"] = (orig_deliv, rep_deliv)
        orig_exc = (orig["last_exception"] or "").strip()
        rep_exc = (rep["last_exception"] or "").strip()
        if orig_exc != rep_exc:
            p_ch["last_exception"] = (orig_exc, rep_exc)
        if p_ch:
            pkg_changes.append({"tracking_number": tid, "changes": p_ch})

    return {
        "amazon_order_id": aid,
        "co_current": co_dict,
        "co_changes": co_changes,
        "pkg_changes": pkg_changes,
    }


def main():
    parser = argparse.ArgumentParser(description="Fix RTS orders incorrectly marked as Delivered/Completed")
    parser.add_argument("--order", help="Single Amazon Order ID to inspect/fix")
    parser.add_argument("--apply", action="store_true", help="Apply updates to database")
    parser.add_argument("--undo", help="Path to undo JSON file")
    args = parser.parse_args()

    if args.undo:
        if not os.path.exists(args.undo):
            print(f"Undo file {args.undo} not found.")
            return 1
        with open(args.undo, "r") as f:
            undo_data = json.load(f)
        conn = get_conn(read_only=False)
        with conn:
            for item in undo_data.get("closing_orders", []):
                aid = item["amazon_order_id"]
                sets = [f"{col}=?" for col in item["prior"].keys()]
                vals = list(item["prior"].values()) + [aid]
                conn.execute(f"UPDATE closing_orders SET {', '.join(sets)} WHERE amazon_order_id=?", vals)
                if conn.execute("SELECT 1 FROM orders WHERE amazon_order_id=?", (aid,)).fetchone():
                    conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE amazon_order_id=?", vals)

            for item in undo_data.get("package_tracking", []):
                tid = item["tracking_number"]
                sets = [f"{col}=?" for col in item["prior"].keys()]
                vals = list(item["prior"].values()) + [tid]
                conn.execute(f"UPDATE zoro_package_tracking SET {', '.join(sets)} WHERE tracking_number=?", vals)
        print(f"✅ Successfully rolled back {len(undo_data.get('closing_orders', []))} orders from {args.undo}")
        return 0

    ro_conn = get_conn(read_only=True)
    rts_orders = find_rts_orders(ro_conn, target_order=args.order)
    print(f"Found {len(rts_orders)} RTS orders to evaluate.")

    all_corrections = []
    for aid, pkgs in rts_orders.items():
        corr = compute_order_corrections(ro_conn, aid, pkgs)
        if corr and (corr["co_changes"] or corr["pkg_changes"]):
            all_corrections.append(corr)

    print(f"Orders requiring database updates: {len(all_corrections)}")

    label_downgrades = sum(1 for c in all_corrections if "order_label" in c["co_changes"])
    status_corrections = sum(1 for c in all_corrections if "delivery_status" in c["co_changes"])
    print(f"  - Delivery status corrections: {status_corrections}")
    print(f"  - Label updates to Incomplete: {label_downgrades}")

    if args.order and all_corrections:
        c = all_corrections[0]
        print(f"\nDetails for order {c['amazon_order_id']}:")
        print("  closing_orders changes:")
        for col, (old, new) in c["co_changes"].items():
            print(f"    {col}: {old!r} → {new!r}")
        print("  package_tracking changes:")
        for p in c["pkg_changes"]:
            print(f"    tracking {p['tracking_number']}:")
            for col, (old, new) in p["changes"].items():
                print(f"      {col}: {old!r} → {new!r}")

    if not args.apply:
        print("\n[DRY RUN] No changes made to database. Pass --apply to execute.")
        return 0

    # Apply changes
    os.makedirs(REPAIR_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    undo_file = os.path.join(REPAIR_DIR, f"rts_undo_{timestamp}.json")

    undo_payload = {"closing_orders": [], "package_tracking": []}

    rw_conn = get_conn(read_only=False)
    with rw_conn:
        for c in all_corrections:
            aid = c["amazon_order_id"]
            if c["co_changes"]:
                prior_vals = {}
                sets, vals = [], []
                for col, (old, new) in c["co_changes"].items():
                    prior_vals[col] = old
                    if new is None:
                        sets.append(f"{col}=NULL")
                    else:
                        sets.append(f"{col}=?")
                        vals.append(new)
                undo_payload["closing_orders"].append({
                    "amazon_order_id": aid,
                    "prior": prior_vals
                })
                vals.append(aid)
                rw_conn.execute(f"UPDATE closing_orders SET {', '.join(sets)} WHERE amazon_order_id=?", vals)
                # Also sync orders table if order exists there
                rw_conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE amazon_order_id=?", vals)

            for p in c["pkg_changes"]:
                tid = p["tracking_number"]
                prior_p = {}
                p_sets, p_vals = [], []
                for col, (old, new) in p["changes"].items():
                    prior_p[col] = old
                    if new is None:
                        p_sets.append(f"{col}=NULL")
                    else:
                        p_sets.append(f"{col}=?")
                        p_vals.append(new)
                undo_payload["package_tracking"].append({
                    "tracking_number": tid,
                    "prior": prior_p
                })
                p_vals.append(tid)
                rw_conn.execute(f"UPDATE zoro_package_tracking SET {', '.join(p_sets)} WHERE tracking_number=?", p_vals)

    with open(undo_file, "w") as f:
        json.dump(undo_payload, f, indent=2, default=str)

    print(f"\n✅ Successfully applied updates to {len(all_corrections)} orders.")
    print(f"📁 Undo payload written to: {undo_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
