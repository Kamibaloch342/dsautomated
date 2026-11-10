#!/usr/bin/env python3
"""
scripts/patch_derived.py — backfill the NEW derived fields onto rows already in
closing_orders, without re-running the slow pipeline. Recomputes purely from data
already stored (+ the Zoro promo from archive_zoro), so it's instant and safe.

Applies: purchase_date (→ Amazon US/Pacific date), normalized ship_by/deliver_by,
PKT timestamps, placement & Zoro-ship speeds, coupon (Promo applied), net profit.

Run:  cd Automations && python3 scripts/patch_derived.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import closing as C                       # reuse the exact same helpers
from engine import closing_signals as CS
from utils.unified_db import get_conn

PATCH = ("purchase_date", "ship_by_date", "deliver_by_date",
         "order_received_pkt", "zoro_placed_pkt", "zoro_shipped_pkt",
         "placement_speed", "placement_speed_hrs", "zoro_ship_speed", "zoro_ship_speed_hrs",
         "coupon_used", "coupon_savings", "net_profit_after_refund",
         "is_business", "zoro_item_desc")


def _report_purchase_map():
    """amazon_order_id → report purchase-date (UTC) from the latest report on disk,
    so existing rows can recover the order TIME (not just the date)."""
    import glob
    files = sorted(glob.glob(os.path.join(C.REPORTS_DIR, "*.txt")), key=os.path.getmtime)
    if not files:
        return {}
    m = {}
    for r in C.parse_report_tsv(files[-1]):
        oid = (r.get("amazon-order-id") or "").strip()
        if oid:
            m[oid] = {
                "purchase": (r.get("purchase-date") or "").strip(),
                "b2b": "Yes" if (r.get("is-business-order") or "").strip().lower() == "true" else "No",
            }
    return m


def _pd(v, report_utc=None):
    """purchase_date in Amazon Pacific date+time. Prefer the report UTC (has the
    precise time); else convert/normalize whatever is already stored."""
    if report_utc:
        d = C.amazon_order_date(report_utc=report_utc)
        if d:
            return d
    if not v:
        return v
    s = str(v)
    if "T" in s or "+00:00" in s or s.endswith("Z"):
        return C.amazon_order_date(report_utc=s)
    return C.norm_date(s)


def main():
    C._ensure_columns()                   # make sure the columns + view exist
    pmap = _report_purchase_map()
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM closing_orders")]
        print(f"patching {len(rows)} rows… (report purchase-times: {len(pmap)})")
        n = 0
        for o in rows:
            rep = pmap.get(o["amazon_order_id"]) or {}
            o["purchase_date"] = _pd(o.get("purchase_date"), rep.get("purchase"))
            if rep.get("b2b"):
                o["is_business"] = rep["b2b"]
            C.derive_fields(o)            # dates norm + PKT ts + speeds
            # coupon + Zoro item description from the Zoro email snippet (archive)
            if o.get("wb_order_id"):
                zr = conn.execute("SELECT raw_body_snippet FROM archive_zoro WHERE wb_order_id=? "
                                  "ORDER BY email_timestamp DESC LIMIT 1", (o["wb_order_id"],)).fetchone()
                if zr:
                    if not o.get("coupon_savings"):
                        cs, lbl = CS.extract_zoro_promo(zr[0])
                        if cs:
                            o["coupon_savings"], o["coupon_used"] = cs, lbl
                    zd = CS.extract_zoro_item(zr[0])
                    if zd:
                        o["zoro_item_desc"] = zd
            np = C.compute_net_profit(o)
            if np is not None:
                o["net_profit_after_refund"] = np
            sets = {k: o.get(k) for k in PATCH if o.get(k) not in (None, "")}
            if sets:
                conn.execute("UPDATE closing_orders SET " + ", ".join(f"{k}=?" for k in sets) +
                             " WHERE amazon_order_id=?", list(sets.values()) + [o["amazon_order_id"]])
                n += 1
        print(f"✓ updated {n} rows")


if __name__ == "__main__":
    main()
