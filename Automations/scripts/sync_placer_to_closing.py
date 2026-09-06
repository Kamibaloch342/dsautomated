#!/usr/bin/env python3
"""Bridge order-script placements into closing_orders — GAP-FILL ONLY.

The placement script logs every order → order_log.txt → order_placer_orders
(via sync_order_placer_logs).

IMPORTANT (2026 redesign): placed-but-not-yet-closed orders are surfaced through
the read-only VIEW `v_orders_all` (closing_orders UNION placer-rows-not-closed),
NOT by physically inserting 'Pending' rows here. The old pre-insert was redundant
(closing.py's placer_lookup() already pulls placer data at processing time) and
polluted the closing_orders source-of-truth table (NULL purchase_date rows, a TEST
row, split-order legs collapsed to one WB). So this script NO LONGER inserts.

It only GAP-FILLS address fields (name/street/phone/profile) onto orders that are
ALREADY in closing_orders — never clobbering closing's richer enrichment. Safe to
run repeatedly / every 10 min from the launcher.
"""
import os, sys, sqlite3, time, re
from datetime import datetime

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "automations_unified.db")

# placer column -> closing column (direct copies)
MAP = {
    "amazon_order_id": "amazon_order_id", "account": "account", "asin": "asin", "sku": "sku",
    "title": "title", "quantity": "quantity", "earnings": "earnings", "amazon_fees": "amazon_fees",
    "shipping_amt": "shipping_amt", "zoro_order_total": "zoro_order_total", "zoro_tax": "zoro_tax",
    "profit_dollars": "profit_dollars", "profit_percent": "profit_percent",
    "coupon_used": "coupon_used", "coupon_savings": "coupon_savings",
    "ship_name": "ship_name", "ship_street": "ship_street", "ship_city": "ship_city",
    "ship_state": "ship_state", "ship_postal_code": "ship_postal_code", "ship_country": "ship_country",
    "ship_phone": "ship_phone", "ship_by_date": "ship_by_date", "deliver_by_date": "deliver_by_date",
    "profile_name": "profile_name", "wb_order_id": "wb_order_id",
    "tracking_ids": "tracking_ids", "shipped_email_time": "shipped_email_time",
}
FILL_ON_EXISTING = ("ship_name", "ship_street", "ship_phone", "profile_name")


def norm_date(s):
    if not s:
        return s
    s = str(s).strip()
    for f in ("%a, %b %d, %Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, f).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else s


def sync(verbose=True):
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.row_factory = sqlite3.Row
    live = {r[1] for r in con.execute("PRAGMA table_info(closing_orders)")}
    existing = {r[0] for r in con.execute("SELECT amazon_order_id FROM closing_orders")}
    placer = [dict(r) for r in con.execute("SELECT * FROM order_placer_orders")]
    ins = filled = 0

    def _do():
        nonlocal ins, filled
        for p in placer:
            aid = p.get("amazon_order_id")
            if not aid:
                continue
            data = {}
            for pc, cc in MAP.items():
                if cc in live and p.get(pc) not in (None, ""):
                    data[cc] = p[pc]
            if p.get("wb_order_id") and "merchant_order_id" in live:
                data["merchant_order_id"] = p["wb_order_id"]
            if p.get("purchase_date"):
                data["purchase_date"] = norm_date(p["purchase_date"])
            if aid not in existing:
                # VIEW model: do NOT pre-insert. Placed-but-unclosed orders are
                # surfaced via the v_orders_all VIEW; closing.py will create the
                # real row (and pull this placer data via placer_lookup) when it
                # processes the order from the report.
                continue
            fill = {k: data[k] for k in FILL_ON_EXISTING if k in data}
            if fill:
                sets = ", ".join(f"{k}=COALESCE(NULLIF({k},''),?)" for k in fill)
                con.execute(f"UPDATE closing_orders SET {sets} WHERE amazon_order_id=?",
                            [*fill.values(), aid])
                filled += 1
        con.commit()

    for i in range(6):
        try:
            _do(); break
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and i < 5:
                time.sleep(1 + i); continue
            raise
    con.close()
    if verbose:
        print(f"placer→closing: gap-filled {filled} existing closing rows "
              f"(VIEW model — no pre-insert)")
    return {"inserted": ins, "filled": filled}


if __name__ == "__main__":
    sync(verbose=True)
