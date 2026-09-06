#!/usr/bin/env python3
"""One-off backfill for SPLIT orders (1 Amazon order = 2-4 Zoro/WB orders).

closing.py historically kept only the FIRST WB and looked up tracking for that WB
only, so sibling legs' FedEx tracking — though already captured in archive_tracking —
never made it into closing_orders.tracking_ids. This recovers them by unioning every
WB leg's tracking (same logic as closing._union_leg_tracking), so the dashboard shows
all legs and the next FedEx pass checks every package.

SAFE: only UNIONS tracking (never removes); does NOT touch delivery_status (that needs
a real FedEx re-check across the new legs — run the closing FedEx pass for that).
Idempotent: re-running adds nothing once legs are unioned.
"""
import os, re, sqlite3, time

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "automations_unified.db")


def _wb_ids(m):
    seen, out = set(), []
    for w in re.findall(r"WB\d+", str(m or "").upper()):
        if w not in seen:
            seen.add(w); out.append(w)
    return out


def _split(s):
    return [t.strip() for t in str(s or "").replace(";", ",").split(",") if t.strip()]


def main(apply=True):
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT amazon_order_id, merchant_order_id, wb_order_id, tracking_ids, carrier, delivery_status "
        "FROM closing_orders WHERE merchant_order_id LIKE '%WB%WB%'").fetchall()
    changed = 0
    for r in rows:
        wbs = _wb_ids(r["merchant_order_id"])
        if len(wbs) <= 1:
            continue
        have, seen = [], set()
        for t in _split(r["tracking_ids"]):
            if t not in seen:
                seen.add(t); have.append(t)
        before = len(have)
        carrier = r["carrier"]
        for wb in wbs:
            for tr in con.execute(
                    "SELECT tracking_ids, carrier FROM archive_tracking "
                    "WHERE wb_order_id=? AND tracking_ids IS NOT NULL AND tracking_ids!='' "
                    "ORDER BY email_timestamp DESC", (wb,)):
                for t in _split(tr[0]):
                    if t not in seen:
                        seen.add(t); have.append(t)
                if (not carrier) and tr[1]:
                    carrier = tr[1]
        added = len(have) - before
        if added:
            changed += 1
            new_tids = ", ".join(have)
            print(f"  {r['amazon_order_id']}: {before}->{len(have)} tracking (+{added})  [{len(wbs)} WB legs, status={r['delivery_status']}]")
            if apply:
                for attempt in range(8):
                    try:
                        con.execute("UPDATE closing_orders SET tracking_ids=?, carrier=COALESCE(NULLIF(carrier,''),?) "
                                    "WHERE amazon_order_id=?", (new_tids, carrier, r["amazon_order_id"]))
                        con.commit(); break
                    except sqlite3.OperationalError as e:
                        if "locked" in str(e) and attempt < 7:
                            time.sleep(3); continue
                        raise
    con.close()
    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}: {changed}/{len(rows)} split orders gained tracking legs.")
    print("NOTE: delivery_status NOT recomputed — run the closing FedEx pass to re-verify all legs.")
    return changed


if __name__ == "__main__":
    import sys
    main(apply="--dry" not in sys.argv)
