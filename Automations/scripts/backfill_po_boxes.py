#!/usr/bin/env python3
"""
backfill_po_boxes.py — put the PO-box orders the placers refused BEFORE 12 Oct 2026 into po_box_orders.

Until 12 Oct the address scrape refused a PO-box order without labelling it, so the order was
re-scraped every cycle (one 2,240 times) and nothing kept its address. The per-run logs
(data/rajar_logs/run_*.csv, Notes = PO_BOX) still list every such order, with how often it came
back. This copies them in — no street (it was never logged), city/state from Amazon's own report —
so Attention → PO boxes shows the history, not only what the placers meet from now on.

Additive only: INSERT OR IGNORE, a row the placer recorded live is never touched.

    python scripts/backfill_po_boxes.py            # dry run: what would be added
    python scripts/backfill_po_boxes.py --apply    # write
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

from utils.unified_db import DB_PATH, PO_BOX_SCHEMA, get_conn  # noqa: E402


def collect(log_dir):
    hits = {}
    for f in sorted(glob.glob(os.path.join(log_dir, "run_*.csv"))):
        m = re.search(r"run_(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})", os.path.basename(f))
        day = m.group(1) if m else ""
        prof = ""
        try:
            prof = json.load(open(f[:-4] + ".json", encoding="utf-8")).get("profile", "") or ""
        except Exception:
            pass
        try:
            rows = list(csv.DictReader(open(f, encoding="utf-8", errors="replace")))
        except Exception:
            continue
        for r in rows:
            notes = (r.get("Notes") or "").upper()
            if "PO_BOX" not in notes and "PO BOX" not in notes:
                continue
            oid = (r.get("Amazon_ID") or "").strip()
            if not re.fullmatch(r"\d{3}-\d{7}-\d{7}", oid):
                continue
            ts = f"{day} {(r.get('Timestamp') or '').strip()}".strip()
            h = hits.setdefault(oid, {"first": ts, "last": ts, "n": 0, "profiles": set()})
            h["n"] += 1
            h["first"] = min(h["first"], ts)
            h["last"] = max(h["last"], ts)
            if prof:
                h["profiles"].add(prof)
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--logs", default=os.path.join(AUTO, "data", "rajar_logs"))
    a = ap.parse_args()
    hits = collect(a.logs)
    print(f"PO-box orders in the run logs: {len(hits)}")
    with get_conn(a.db) as conn:
        info = {}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='amazon_report_orders'").fetchone():
            for oid in hits:
                r = conn.execute("SELECT account, ship_city, ship_state FROM amazon_report_orders WHERE amazon_order_id=?",
                                 (oid,)).fetchone()
                if r:
                    info[oid] = dict(r)
        have = set()
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='po_box_orders'").fetchone():
            have = {r[0] for r in conn.execute("SELECT amazon_order_id FROM po_box_orders")}
        new = [o for o in hits if o not in have]
        print(f"already in po_box_orders: {len(hits) - len(new)} · to add: {len(new)}")
        for oid in sorted(new, key=lambda o: hits[o]["first"]):
            h, i = hits[oid], info.get(oid, {})
            prof = ",".join(sorted(h["profiles"]))
            acct = i.get("account") or ("Hathoro" if prof.startswith("DM_") else ("HOANK" if prof else ""))
            print(f"  {oid}  {acct:7} {prof:32} {h['first'][:16]} → {h['last'][:16]}  ×{h['n']}")
        if not a.apply:
            print("\ndry run — nothing written (add --apply)")
            return
        conn.executescript(PO_BOX_SCHEMA)
        for oid in new:
            h, i = hits[oid], info.get(oid, {})
            prof = ",".join(sorted(h["profiles"]))
            acct = i.get("account") or ("Hathoro" if prof.startswith("DM_") else ("HOANK" if prof else ""))
            conn.execute("""INSERT OR IGNORE INTO po_box_orders (amazon_order_id, account, profile, street, street2, city,
                                state, zip, detected_by, label_written, first_seen, last_seen, times_seen)
                            VALUES (?, ?, ?, '', '', ?, ?, '', 'amazon_address (run log, before 12 Oct)', '', ?, ?, ?)""",
                         (oid, acct, prof, i.get("ship_city") or "", i.get("ship_state") or "", h["first"], h["last"], h["n"]))
        print(f"\nadded {len(new)} row(s)")


if __name__ == "__main__":
    main()
