#!/usr/bin/env python3
"""
sync_order_placer_logs.py — Background sync: order_log.txt → order_placer_orders table

Reads the rich order_log.txt format and upserts into the unified DB.
Designed to run every 5 minutes via cron or launcher auto-sync.

Does NOT modify engine/orders.py — zero touch on order placement.
"""

from __future__ import annotations

import os
import sys
import re
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from utils.unified_db import upsert_placer_order, get_conn

ORDER_LOG_PATH = os.path.join(_SCRIPT_DIR, "data", "rajar_logs", "order_log.txt")

# Real Amazon order ids look like 123-1234567-1234567. Anything else (TEST rows,
# truncated/partially-written log lines) is rejected so garbage never enters the DB.
AMAZON_ID_RE = re.compile(r"^\d{3}-\d{7}-\d{7}$")


def _parse_money(val):
    if not val or val in ("N/A", "", "None"):
        return None
    try:
        return float(str(val).replace("$", "").replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def _ensure_columns():
    """Add columns the placer log carries that order_placer_orders may lack (idempotent)."""
    with get_conn() as conn:
        have = {r[1] for r in conn.execute("PRAGMA table_info(order_placer_orders)")}
        for col, typ in (("zoro_qty", "INTEGER"), ("zoro_deliver_by", "TEXT"), ("zoro_stock", "INTEGER"),
                         ("alt_used", "TEXT"), ("main_sku", "TEXT")):
            if col not in have:
                conn.execute(f"ALTER TABLE order_placer_orders ADD COLUMN {col} {typ}")


def _account_from_profile(profile: str) -> str:
    p = profile.upper()
    return "Hathoro" if p.startswith("DM_") else "HOANK"


def _norm_date(s):
    """Normalize a date string to YYYY-MM-DD so it sorts/filters consistently with
    closing_orders (the placer logs 'Sun, Jun 21, 2026'). Returns the input unchanged
    if it can't be parsed."""
    if not s:
        return s
    s = str(s).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    for f in ("%a, %b %d, %Y", "%A, %b %d, %Y", "%a, %B %d, %Y", "%A, %B %d, %Y",
              "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, f).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def parse_log_line(line: str) -> dict | None:
    """
    Parse a single order_log.txt line into a dict.
    Format: [timestamp] | Key:Value | Key:Value | ...
    """
    line = line.strip()
    if not line or not line.startswith("["):
        return None

    # Extract timestamp
    ts_match = re.match(r"^\[([^\]]+)\]\s+(.*)", line)
    if not ts_match:
        return None
    ts_str, rest = ts_match.groups()

    # Parse key:value pairs
    entry = {"_timestamp": ts_str}
    for part in rest.split(" | "):
        # strip any leading pipe the timestamp regex left on the first field
        # ('| WB:WB123' → 'WB:WB123'), otherwise the key becomes '| WB' and the
        # WB / merchant id silently never lands in the DB.
        part = part.strip().lstrip("|").strip()
        if ":" in part:
            key, val = part.split(":", 1)
            entry[key.strip()] = val.strip()

    return entry


def entry_to_db_data(entry: dict) -> dict | None:
    """Convert parsed log entry into order_placer_orders row dict."""
    amazon_id = entry.get("Amazon", "")
    if not amazon_id or not AMAZON_ID_RE.match(amazon_id):
        return None   # missing or malformed (TEST row / truncated line) → skip

    wb_id = entry.get("WB", "")
    profile = entry.get("Profile", "")
    _zq = _parse_money(entry.get("ZQty"))   # Zoro units actually ordered (e.g. 6 jugs)
    _zst = _parse_money(entry.get("ZoroStock"))   # Zoro inventoryQuantity at placement (None unless low)

    data = {
        "amazon_order_id": amazon_id,
        "wb_order_id": wb_id,
        "account": _account_from_profile(profile),
        "profile_name": profile,
        "asin": entry.get("ASIN", ""),
        "sku": entry.get("SKU", ""),
        "title": entry.get("Title", ""),
        "quantity": sum(int(n) for n in re.findall(r"\d+", str(entry.get("AQty", 0) or 0))),
        "zoro_qty": int(_zq) if _zq else None,
        "zoro_deliver_by": _norm_date(entry.get("ZoroDeliverBy", "")),
        "zoro_stock": int(_zst) if _zst is not None else None,
        "earnings": _parse_money(entry.get("Earnings", "")),
        "amazon_fees": _parse_money(entry.get("AmazonFees", "")),
        "shipping_amt": _parse_money(entry.get("ShippingAmt", "")),
        "zoro_order_total": _parse_money(entry.get("Cost", "")),
        "zoro_tax": _parse_money(entry.get("Tax", "")),
        "profit_dollars": _parse_money(entry.get("Profit", "")),
        "profit_percent": _parse_money(entry.get("FinalMargin", "")),
        "coupon_used": entry.get("Coupon", ""),
        "coupon_savings": _parse_money(entry.get("Discount", "")),
        "ship_name": entry.get("ShipName", ""),
        "ship_street": entry.get("ShipStreet", ""),
        "ship_city": entry.get("ShipCity", ""),
        "ship_state": entry.get("ShipState", ""),
        "ship_postal_code": entry.get("ShipZIP", ""),
        "ship_phone": entry.get("ShipPhone", ""),
        "purchase_date": _norm_date(entry.get("PurchaseDate", "")),
        "ship_by_date": _norm_date(entry.get("ShipBy", "")),
        "deliver_by_date": _norm_date(entry.get("DeliverBy", "")),
        "tracking_ids": entry.get("TrackingIDs", ""),
        "shipped_email_time": entry.get("ShippedEmailTime", ""),
        # Alternative-SKU placement: sku above is the code actually placed (the alt);
        # main_sku is the OOS main code it substituted. alt_used='Yes' drives the
        # dashboard's ALT filter on the Placed tab.
        "alt_used": "Yes" if entry.get("AltUsed", "").strip().lower() == "yes" else "",
        "main_sku": "" if entry.get("MainSKU", "").strip() in ("", "N/A", "None")
                    else entry.get("MainSKU", "").strip(),
    }

    # Parse placed_at from timestamp
    ts_str = entry.get("_timestamp", "")
    if ts_str:
        try:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            data["placed_at"] = dt.isoformat()
        except ValueError:
            pass

    return data


def sync_all(log_path: str = ORDER_LOG_PATH, verbose: bool = True) -> dict:
    """
    Read entire order_log.txt and sync all entries.
    Returns stats dict.
    """
    if not os.path.exists(log_path):
        if verbose:
            print(f"⚠️  order_log.txt not found: {log_path}")
        return {"total": 0, "inserted": 0, "updated": 0, "errors": 0}

    _ensure_columns()
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    inserted = 0
    updated = 0
    errors = 0

    for line in lines:
        entry = parse_log_line(line)
        if not entry:
            continue
        data = entry_to_db_data(entry)
        if not data:
            errors += 1
            continue
        try:
            # upsert_placer_order returns True on insert, but we can't distinguish easily
            # So we check if row exists first
            from utils.unified_db import get_placer_order
            existing = get_placer_order(data["amazon_order_id"])
            upsert_placer_order(data)
            if existing:
                updated += 1
            else:
                inserted += 1
        except Exception as e:
            errors += 1
            if verbose:
                print(f"  ⚠️  Error syncing {data.get('amazon_order_id')}: {e}")

    stats = {"total": inserted + updated, "inserted": inserted, "updated": updated, "errors": errors}
    if verbose:
        print(f"✅ Sync complete: {inserted} inserted, {updated} updated, {errors} errors")
    return stats


def sync_incremental(log_path: str = ORDER_LOG_PATH, verbose: bool = True) -> dict:
    """
    Only sync orders that don't exist in the DB yet.
    Faster for frequent background syncs.
    """
    if not os.path.exists(log_path):
        if verbose:
            print(f"⚠️  order_log.txt not found: {log_path}")
        return {"total": 0, "inserted": 0, "errors": 0}

    _ensure_columns()
    # Get all existing amazon IDs in one query
    with get_conn() as conn:
        existing_ids = set(
            r[0] for r in conn.execute("SELECT amazon_order_id FROM order_placer_orders").fetchall()
        )

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    inserted = 0
    errors = 0

    for line in lines:
        entry = parse_log_line(line)
        if not entry:
            continue
        amazon_id = entry.get("Amazon", "")
        if not amazon_id or amazon_id in existing_ids:
            continue
        data = entry_to_db_data(entry)
        if not data:
            errors += 1
            continue
        try:
            upsert_placer_order(data)
            inserted += 1
        except Exception as e:
            errors += 1
            if verbose:
                print(f"  ⚠️  Error syncing {amazon_id}: {e}")

    stats = {"total": inserted, "inserted": inserted, "errors": errors}
    if verbose:
        print(f"✅ Incremental sync: {inserted} new orders inserted, {errors} errors")
    return stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sync order_log.txt → order_placer_orders table")
    parser.add_argument("--full", action="store_true", help="Full sync (re-check all orders)")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    args = parser.parse_args()

    if args.full:
        stats = sync_all(verbose=not args.quiet)
    else:
        stats = sync_incremental(verbose=not args.quiet)

    return 0 if stats.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
