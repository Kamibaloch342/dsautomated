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
                         ("alt_used", "TEXT"), ("main_sku", "TEXT"),
                         # raw Zoro price facts (see utils.unified_db.PLACER_PRICE_COLUMNS)
                         ("zoro_list_price", "REAL"), ("zoro_subtotal", "REAL"), ("zoro_shipping", "REAL")):
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
        # Recorded raw from the audit line: ListPrice is Zoro's per-unit list price at cart-add,
        # Subtotal/Shipping are what the order summary stated. N/A parses to NULL, not 0.
        "zoro_list_price": _parse_money(entry.get("ListPrice", "")),
        "zoro_subtotal": _parse_money(entry.get("Subtotal", "")),
        "zoro_shipping": _parse_money(entry.get("Shipping", "")),
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


_INCR_STATE = os.path.join(_SCRIPT_DIR, "data", "placer_log_sync_state.json")
_FULL_STATE = os.path.join(_SCRIPT_DIR, "data", "placer_full_sync_state.json")


def _log_signature(log_path):
    try:
        st = os.stat(log_path)
        return {"size": st.st_size, "mtime": round(st.st_mtime, 3)}
    except OSError:
        return None


def sync_smart(log_path: str = ORDER_LOG_PATH, verbose: bool = True, force_full: bool = False,
               full_every_hours: float = 24.0) -> dict:
    """The sync the closing chain should call. Incremental normally; the full 60k-line
    reconciliation (sync_all — log values win over stored ones) once every
    `full_every_hours`, or on demand. Before 26 Sep 2026 every closing run and every
    daily_bulk step ran sync_all, per account — four full rewrites of the placer table per
    bulk, ~240k row updates for a handful of new lines."""
    import json, datetime as _dt
    last = None
    try:
        last = _dt.datetime.fromisoformat(json.load(open(_FULL_STATE))["last_full"])
    except Exception:
        pass
    due = force_full or last is None or (_dt.datetime.now() - last) >= _dt.timedelta(hours=full_every_hours)
    if due:
        stats = sync_all(log_path, verbose=verbose)
        stats["mode"] = "full"
        try:
            with open(_FULL_STATE, "w") as f:
                json.dump({"last_full": _dt.datetime.now().isoformat(timespec="seconds")}, f)
        except Exception:
            pass
        return stats
    stats = sync_incremental(log_path, verbose=verbose)
    stats["mode"] = "incremental"
    return stats


def sync_incremental(log_path: str = ORDER_LOG_PATH, verbose: bool = True) -> dict:
    """Insert orders the DB does not have, and fill blanks on the ones it does.

    This used to skip every amazon_order_id already present. The placer writes its own row the
    moment an order is placed, carrying only what checkout knows — so that row already existed
    when this sync first saw the line, and the fields only the log has (profile, title, margin,
    buyer name and street, phone, alt code, and the tracking that arrives later) were never
    filled. They landed only at the next closing, which runs sync_all. On 21 Sep 2026 that left
    359 of the last 400 orders with no profile and no title.

    Blanks only: a value already stored is never overwritten here (sync_all still reconciles
    everything at closing), so this is safe to run every few minutes and settles to no writes
    at all once the rows are complete."""
    if not os.path.exists(log_path):
        if verbose:
            print(f"⚠️  order_log.txt not found: {log_path}")
        return {"total": 0, "inserted": 0, "enriched": 0, "errors": 0}

    # The launcher calls this every 60 s. When the log has not changed since the last pass
    # there is nothing to learn from it — skip the 56 MB parse and the 57k-row SELECT.
    import json
    sig = _log_signature(log_path)
    if log_path == ORDER_LOG_PATH and sig:
        try:
            prev = json.load(open(_INCR_STATE))
            if prev.get("sig") == sig:
                if verbose:
                    print("✅ Incremental sync: order_log.txt unchanged — nothing to do")
                return {"total": 0, "inserted": 0, "enriched": 0, "errors": 0, "skipped_unchanged": True}
        except Exception:
            pass

    _ensure_columns()
    # Everything already stored, read once: an untouched row then costs a dict lookup, no query.
    with get_conn() as conn:
        stored_cols = [d[0] for d in conn.execute(
            "SELECT * FROM order_placer_orders LIMIT 0").description]
        have = {r["amazon_order_id"]: r for r in conn.execute(
            "SELECT * FROM order_placer_orders")}

    def _blank(v):
        return v is None or (isinstance(v, str) and not v.strip())

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    inserted = enriched = errors = skipped = 0

    for line in lines:
        entry = parse_log_line(line)
        if not entry:
            continue
        amazon_id = entry.get("Amazon", "")
        if not amazon_id:
            continue
        data = entry_to_db_data(entry)
        if not data:
            # Walmart placements share this log ("Amazon:WALMART:PO…") and belong in
            # walmart_orders, not here — skipped, not an error.
            if amazon_id.upper().startswith("WALMART"):
                skipped += 1
            else:
                errors += 1
            continue
        row = have.get(amazon_id)
        if row is not None:
            # only what this line can fill and the row is still missing
            fill = {k: v for k, v in data.items()
                    if k in stored_cols and not _blank(v) and _blank(row[k])}
            if not fill:
                continue
            data = dict(fill, amazon_order_id=amazon_id)
        try:
            upsert_placer_order(data)
            if row is None:
                inserted += 1
                have[amazon_id] = None      # a later line for the same order enriches instead
            else:
                enriched += 1
        except Exception as e:
            errors += 1
            if verbose:
                print(f"  ⚠️  Error syncing {amazon_id}: {e}")

    stats = {"total": inserted + enriched, "inserted": inserted,
             "enriched": enriched, "errors": errors, "skipped_non_amazon": skipped}
    if log_path == ORDER_LOG_PATH and sig and not errors:
        try:
            with open(_INCR_STATE, "w") as f:
                json.dump({"sig": sig}, f)
        except Exception:
            pass
    if verbose:
        print(f"✅ Incremental sync: {inserted} new, {enriched} enriched, {errors} errors"
              + (f", {skipped} Walmart rows skipped" if skipped else ""))
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
