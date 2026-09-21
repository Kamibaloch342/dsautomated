"""
insights_engine.py — Operational Insights & Historical Anomaly Engine for ZUHRA Software.

Analyzes incoming orders and comparisons against historical database records:
  1. Amazon Item Price Changed (+ history: last order date and price)
  2. Zoro Base Price Changed (unit cost excluding shipping)
  3. Zoro Code Changed (alternative SKU used for OOS)
  4. Shipping Price Changed (buyer charged $0 shipping vs customary $18+)
  5. Zoro vs Amazon Delivery Window Mismatch (Zoro arrive > Amazon deliver-by)
  6. Low Zoro Stock / Limited Stock Warning
  7. Clean Net Earnings/Unit Changed (item subtotal only, excluding shipping & tax)
  8. Handling Time Changed (e.g. ship-by window reduced)
"""

import os
import re
import sqlite3
from datetime import datetime, date
from typing import Dict, Any, List, Optional

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_PATH = os.path.join(_SCRIPT_DIR, "data", "automations_unified.db")
_ORDER_LOG = os.path.join(_SCRIPT_DIR, "data", "rajar_logs", "order_log.txt")


def _get_db():
    """Connect to unified SQLite database in query_only mode with timeout."""
    try:
        c = sqlite3.connect(_DB_PATH, timeout=10)
        c.execute("PRAGMA busy_timeout=10000")
        c.execute("PRAGMA query_only=ON")
        c.row_factory = sqlite3.Row
        return c
    except Exception:
        return None


def _parse_date(d_str: Any) -> Optional[date]:
    """Parse various date formats into date object."""
    if not d_str:
        return None
    s = str(d_str).strip()
    # YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # "Fri, Sep 11, 2026" or "Sep 11, 2026"
    for fmt in ("%a, %b %d, %Y", "%b %d, %Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.split("T")[0], fmt).date()
        except ValueError:
            pass
    return None


def last_amazon_unit_price(asin: str, exclude_amazon_id: str = "") -> Optional[Dict[str, Any]]:
    """The previous Amazon selling price per unit for this ASIN, from closing_orders — the
    table filled from Amazon's own order report (item_price = line total, quantity = units the
    buyer bought). order_placer_orders is not a safe source for this: until 21 Sep 2026 the
    placer wrote the ZORO quantity into its `quantity` column, so a 25-pack sold once looked
    like 25 units at $1.48 and the alert reported a +2399% price rise."""
    if not asin:
        return None
    c = _get_db()
    if not c:
        return None
    try:
        r = c.execute(
            """SELECT item_price, quantity, purchase_date FROM closing_orders
               WHERE UPPER(asin) = ? AND amazon_order_id != ?
                 AND CAST(item_price AS REAL) > 0 AND CAST(quantity AS INTEGER) > 0
               ORDER BY purchase_date DESC LIMIT 1""",
            (asin.strip().upper(), str(exclude_amazon_id or ""))).fetchone()
        if not r:
            return None
        return {"unit_price": round(float(r[0]) / int(r[1]), 2), "date": r[2]}
    except Exception:
        return None
    finally:
        try:
            c.close()
        except Exception:
            pass


def get_historical_asin_orders(asin: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieve historical orders for an ASIN from automations_unified.db."""
    if not asin:
        return []
    asin = asin.strip().upper()
    c = _get_db()
    if not c:
        return []
    try:
        # Search both order_placer_orders and orders tables
        rows = c.execute(
            """SELECT amazon_order_id, asin, sku, title, quantity, zoro_qty, earnings, amazon_fees,
                      shipping_amt, zoro_order_total, purchase_date, ship_by_date, deliver_by_date,
                      zoro_deliver_by, zoro_stock, alt_used, main_sku, placed_at
               FROM order_placer_orders
               WHERE UPPER(asin) = ?
               ORDER BY id DESC LIMIT ?""",
            (asin, limit),
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]

        # Fallback to general orders table
        rows = c.execute(
            """SELECT amazon_order_id, asin, sku, title, quantity, zoro_qty, earnings, amazon_fees,
                      shipping_amt, zoro_order_total, purchase_date, ship_by_date, deliver_by_date,
                      item_price, shipping_price
               FROM orders
               WHERE UPPER(asin) = ? AND (wb_order_id IS NOT NULL AND wb_order_id != '')
               ORDER BY id DESC LIMIT ?""",
            (asin, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"  [Insights] DB historical fetch error: {e}")
        return []
    finally:
        try:
            c.close()
        except Exception:
            pass


TRACKER_FILE = os.path.join(_SCRIPT_DIR, "data", "dataset", "price_tracker.json")

# Alert when Zoro's per-unit LIST price moves by at least this much against the last one
# recorded for the same Zoro code. Chosen by the owner on 20 Sep 2026.
LIST_PRICE_ALERT_PCT = float(os.environ.get("ZUHRA_LIST_PRICE_ALERT_PCT", "2.0") or 2.0)
# The old derived-from-totals rule stays available for comparison only.
_LEGACY_BASE_PRICE_ALERT = str(os.environ.get("ZUHRA_LEGACY_BASE_PRICE_ALERT", "")).strip().lower() in ("1", "true", "yes")
_price_tracker_cache = {}


def _load_price_tracker() -> dict:
    global _price_tracker_cache
    if not _price_tracker_cache and os.path.exists(TRACKER_FILE):
        try:
            import json
            with open(TRACKER_FILE, "r", encoding="utf-8") as f:
                _price_tracker_cache = json.load(f)
        except Exception:
            _price_tracker_cache = {}
    return _price_tracker_cache


def _apply_coupon_rate_reversal(subtotal: float, coupon_str: str) -> float:
    """Given a post-coupon merchandise subtotal and coupon code, reconstruct the pre-coupon catalog total."""
    c = str(coupon_str or "").upper()
    if any(k in c for k in ("ZSELECT", "20%", "TWENTY", "20")):
        return subtotal / 0.80
    if any(k in c for k in ("15%", "FIFTEEN", "15")):
        return subtotal / 0.85
    if any(k in c for k in ("10%", "TEN", "SELECT", "10")):
        return subtotal / 0.90
    if any(k in c for k in ("5%", "FIVE")):
        return subtotal / 0.95
    return subtotal


def compute_order_zoro_base_unit(order_dict: Dict[str, Any], zoro_code: str = "", con: Optional[sqlite3.Connection] = None) -> float:
    """
    Compute true pre-coupon, pre-tax, pre-shipping Zoro catalog unit price.
    Normalized against destination sales tax, flat/carrier shipping, and coupon discounts.
    """
    # 1. Direct catalog price passed from order placement (api_price from live cart)
    cat_price = float(order_dict.get("zoro_catalog_price") or order_dict.get("api_price") or 0.0)
    if cat_price > 0:
        return round(cat_price, 2)

    z_code = str(zoro_code or order_dict.get("zoro_code") or order_dict.get("sku") or "").strip().upper()
    zq = max(1, int(order_dict.get("zoro_qty") or order_dict.get("quantity") or order_dict.get("qty") or 1))
    wb = str(order_dict.get("wb_order_id") or order_dict.get("wb_id") or "").strip()
    coupon = str(order_dict.get("coupon_used") or order_dict.get("coupon_code") or order_dict.get("coupon") or "").strip().upper()
    discount = float(order_dict.get("coupon_savings") or order_dict.get("coupon_off") or order_dict.get("discount") or 0.0)

    # 2. Check archive_zoro by wb_order_id if available (has exact pre-tax/pre-shipping subtotal)
    db_conn = con or _get_db()
    close_db = (db_conn is not None and con is None)
    if wb and wb.startswith("WB") and db_conn:
        try:
            r = db_conn.execute(
                "SELECT unit_price, subtotal, shipping_cost, tax, order_total, coupon_savings, qty FROM archive_zoro WHERE wb_order_id = ? LIMIT 1",
                (wb,)
            ).fetchone()
            if r:
                arch_unit = float(r["unit_price"] or 0.0)
                arch_sub = float(r["subtotal"] or 0.0)
                arch_disc = float(r["coupon_savings"] or 0.0)
                arch_qty = max(1, int(r["qty"] or zq or 1))
                if arch_unit > 0:
                    return round(arch_unit, 2)
                if arch_sub > 0:
                    cat_tot = arch_sub + arch_disc
                    if arch_disc <= 0 and coupon and coupon not in ("NONE", "FALSE", "N/A", ""):
                        cat_tot = _apply_coupon_rate_reversal(arch_sub, coupon)
                    return round(cat_tot / arch_qty, 2)
        except Exception:
            pass
        finally:
            if close_db:
                try:
                    db_conn.close()
                except Exception:
                    pass

    # 3. Check direct merchandise subtotal if already present
    subtotal = float(order_dict.get("subtotal") or order_dict.get("zoro_subtotal") or 0.0)
    if subtotal > 0:
        cat_tot = subtotal + discount
        if discount <= 0 and coupon and coupon not in ("NONE", "FALSE", "N/A", ""):
            cat_tot = _apply_coupon_rate_reversal(subtotal, coupon)
        return round(cat_tot / zq, 2)

    # 4. Check price_tracker.json (live catalog price from previous placements/cart checks)
    tot = float(order_dict.get("zoro_order_total") or order_dict.get("cost") or order_dict.get("tc") or order_dict.get("zoro_price") or 0.0)
    tracker = _load_price_tracker()
    if z_code in tracker:
        trk_p = float(tracker[z_code].get("price") or 0.0)
        if trk_p > 0 and tot > 0:
            exp_sub = trk_p * zq
            if coupon and coupon not in ("NONE", "FALSE", "N/A", ""):
                if any(k in coupon for k in ("ZSELECT", "20%", "TWENTY", "20")):
                    exp_sub *= 0.80
                elif any(k in coupon for k in ("10%", "TEN", "SELECT", "10")):
                    exp_sub *= 0.90
            exp_ship = 0.0 if exp_sub >= 49.99 else 5.99
            exp_pre_tax = exp_sub + exp_ship
            # If tot matches expected within destination sales tax tolerance (0% to 15% tax)
            if exp_pre_tax * 0.98 <= tot <= exp_pre_tax * 1.15:
                return round(trk_p, 2)

    # 5. Financial reconstruction from gross checkout cost
    tax = float(order_dict.get("zoro_tax") or order_dict.get("tax") or 0.0)
    shipping = float(order_dict.get("zoro_shipping") or order_dict.get("shipping_cost") or 0.0)
    pre_tax = max(0.0, tot - tax)
    if shipping <= 0:
        if pre_tax < 49.99 and pre_tax > 15.0:
            shipping = 5.99
        else:
            shipping = 0.0
    subtotal = max(0.0, pre_tax - shipping)
    cat_tot = subtotal + discount
    if discount <= 0 and coupon and coupon not in ("NONE", "FALSE", "N/A", ""):
        cat_tot = _apply_coupon_rate_reversal(subtotal, coupon)
    return round(cat_tot / zq, 2)


def get_historical_zoro_costs(zoro_code: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieve previous orders and unit costs for a Zoro code from placer orders and archive_zoro."""
    if not zoro_code:
        return []
    zoro_code = zoro_code.strip().upper()
    alt_prefix_code = ("K" + zoro_code[1:]) if zoro_code.startswith("G") else (("G" + zoro_code[1:]) if zoro_code.startswith("K") else zoro_code)
    c = _get_db()
    if not c:
        return []
    try:
        query = """
            SELECT amazon_order_id, wb_order_id, sku, quantity, zoro_order_total,
                   zoro_tax, coupon_used, coupon_savings, zoro_qty, placed_at
            FROM order_placer_orders
            WHERE (UPPER(sku) = ? OR UPPER(sku) = ?) AND zoro_order_total > 0
            ORDER BY id DESC LIMIT ?
        """
        rows = [dict(r) for r in c.execute(query, (zoro_code, alt_prefix_code, limit)).fetchall()]

        wb_ids = [r.get("wb_order_id") for r in rows if r.get("wb_order_id")]
        if wb_ids:
            placeholders = ",".join("?" * len(wb_ids))
            arch_rows = c.execute(
                f"""SELECT wb_order_id, subtotal, shipping_cost, tax, order_total, coupon_savings, unit_price
                    FROM archive_zoro WHERE wb_order_id IN ({placeholders})""",
                wb_ids
            ).fetchall()
            arch_map = {ar["wb_order_id"]: dict(ar) for ar in arch_rows}
            for r in rows:
                wb = r.get("wb_order_id")
                if wb and wb in arch_map:
                    ar = arch_map[wb]
                    r["subtotal"] = ar.get("subtotal")
                    r["shipping_cost"] = ar.get("shipping_cost")
                    if ar.get("unit_price") and ar["unit_price"] > 0:
                        r["unit_price"] = ar["unit_price"]
                    if ar.get("coupon_savings") and ar["coupon_savings"] > 0:
                        r["coupon_savings"] = ar["coupon_savings"]
                    if ar.get("tax") is not None:
                        r["zoro_tax"] = ar["tax"]

        if len(rows) < limit:
            existing_wbs = {r.get("wb_order_id") for r in rows if r.get("wb_order_id")}
            needed = limit - len(rows)
            arch_query = """
                SELECT '' AS amazon_order_id, wb_order_id, sku, qty AS quantity, order_total AS zoro_order_total,
                       tax AS zoro_tax, coupon_savings, qty AS zoro_qty, email_timestamp AS placed_at,
                       subtotal, shipping_cost, unit_price
                FROM archive_zoro
                WHERE (UPPER(sku) = ? OR UPPER(sku) = ?) AND order_total > 0
                ORDER BY id DESC LIMIT ?
            """
            arch_supp = [dict(r) for r in c.execute(arch_query, (zoro_code, alt_prefix_code, needed * 2)).fetchall()]
            for ar in arch_supp:
                if ar.get("wb_order_id") not in existing_wbs:
                    existing_wbs.add(ar.get("wb_order_id"))
                    rows.append(ar)
                    if len(rows) >= limit:
                        break

        return rows
    except Exception as e:
        print(f"  [Insights] Error fetching historical zoro costs: {e}")
        return []
    finally:
        try:
            c.close()
        except Exception:
            pass


def analyze_order_insights(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Run all 8 operational insight checks on an order.
    Returns list of triggered insight objects.
    """
    insights = []
    asin = (order.get("asin") or "").strip().upper()
    current_amz_id = str(order.get("amazon_id") or order.get("amazon_order_id") or "")
    amazon_qty = max(1, int(order.get("amazon_qty") or order.get("order_qty") or order.get("quantity") or 1))
    zoro_qty = max(1, int(order.get("zoro_qty") or order.get("qty") or 1))
    qty = amazon_qty
    title = str(order.get("title") or order.get("item_title") or "").strip()

    # 1. Resolve Zoro code (e.g. K221882659, G410877336)
    zoro_code = ""
    for k in ("zoro_code", "alt_code", "alt_sku", "code", "zoro_sku"):
        val = str(order.get(k) or "").replace(" - ALT", "").replace("- ALT", "").strip().upper()
        if val and re.match(r"^[GK]\d+$", val):
            zoro_code = val
            break
    if not zoro_code:
        raw_code = str(order.get("code") or "").replace(" - ALT", "").replace("- ALT", "").strip().upper()
        if raw_code and (raw_code.startswith("G") or raw_code.startswith("K")):
            zoro_code = raw_code.split()[0]
    if not zoro_code:
        raw_sku = str(order.get("sku") or "").strip().upper()
        if re.match(r"^[GK]\d+$", raw_sku):
            zoro_code = raw_sku
        else:
            m = re.search(r"[GK]\d+", raw_sku)
            if m:
                zoro_code = m.group(0)
    if not zoro_code:
        zoro_code = str(order.get("sku") or "N/A").strip().upper()

    # 2. Resolve Amazon Seller SKU
    sku = str(order.get("seller_sku") or order.get("sku") or "").strip()

    # Guaranteed fallback lookup for Title & SKU if missing
    if (not title or not sku or sku in ("N/A", "None", "")) and asin:
        try:
            from utils.telegram import _lookup_title_and_sku
            db_t, db_s = _lookup_title_and_sku(asin)
            title = title or db_t
            sku = sku or db_s
        except Exception:
            pass

    title_str = title[:80] if title else "Product Listing"
    sku_str = sku if sku else (zoro_code or "N/A")

    # Current financial numbers
    earnings = float(order.get("earnings") or 0.0)
    shipping_amt = float(order.get("shipping_amt") or 0.0)
    current_base_unit = compute_order_zoro_base_unit(order, zoro_code=zoro_code)
    zoro_unit_cost = current_base_unit

    raw_item_price = float(order.get("item_price") or order.get("unit_price") or 0.0)
    # If item_price was total line revenue, convert to per-unit price using amazon_qty
    if order.get("unit_price"):
        item_unit_price = float(order.get("unit_price"))
    elif raw_item_price > 0 and amazon_qty > 1:
        item_unit_price = round(raw_item_price / amazon_qty, 2)
    else:
        item_unit_price = raw_item_price

    # Dates
    purchase_date_str = order.get("purchase_date")
    ship_by_str = order.get("ship_by_date") or order.get("ship_by")
    deliver_by_str = order.get("deliver_by_date") or order.get("deliver_by")
    zoro_deliver_str = order.get("zoro_deliver_by") or order.get("zoro_delivery_date") or order.get("zoro_deliver_date") or order.get("zoro_delivery") or order.get("zoro_eta")

    # Fetch history
    history = get_historical_asin_orders(asin, limit=10)
    # Filter out current order if it was already inserted
    prior_orders = [h for h in history if str(h.get("amazon_order_id")) != current_amz_id]
    last_order = prior_orders[0] if prior_orders else None

    # -------------------------------------------------------------
    # 1. Amazon Item Price Changed
    # -------------------------------------------------------------
    _amz_prev = last_amazon_unit_price(asin, exclude_amazon_id=current_amz_id) if item_unit_price > 0 else None
    if item_unit_price > 0 and (_amz_prev or last_order):
        if _amz_prev:
            # Amazon's own report: line total / units bought. Authoritative.
            last_item_unit_price = _amz_prev["unit_price"]
            last_date_str = _amz_prev["date"] or "earlier"
        else:
            # No closed order for this ASIN yet — fall back to the placer's record, dividing by
            # the AMAZON quantity only. A row whose quantity equals its Zoro quantity above 1
            # was written before the 21 Sep 2026 fix and would divide by the Zoro count, so it
            # is not a baseline.
            last_item_price = float(last_order.get("item_price") or 0.0)
            last_qty = max(1, int(last_order.get("amazon_qty") or last_order.get("quantity") or 1))
            _zq = int(last_order.get("zoro_qty") or 0)
            if last_item_price > 0:
                last_item_unit_price = round(last_item_price / last_qty, 2)
            elif _zq > 1 and last_qty == _zq:
                last_item_unit_price = 0.0
            else:
                last_earn = float(last_order.get("earnings") or 0.0)
                last_fees = float(last_order.get("amazon_fees") or 0.0)
                last_ship = float(last_order.get("shipping_amt") or 0.0)
                last_item_unit_price = round((last_earn + last_fees - last_ship) / last_qty, 2)
            last_date_str = last_order.get("purchase_date") or last_order.get("placed_at") or "earlier"

        if last_item_unit_price > 0 and abs(item_unit_price - last_item_unit_price) >= 0.50:
            delta = item_unit_price - last_item_unit_price
            pct = (delta / last_item_unit_price) * 100.0
            direction = "INCREASED" if delta > 0 else "DECREASED"
            emoji = "📈" if delta > 0 else "📉"
            status_badge = "🟢" if delta > 0 else "🔴"
            order_line = f"• Order: `{current_amz_id}`\n" if current_amz_id else ""
            insights.append({
                "code": "ITEM_PRICE_CHANGED",
                "emoji": emoji,
                "title": f"Amazon Price {direction}",
                "severity": "info" if delta > 0 else "warning",
                "asin": asin,
                "sku": sku_str,
                "product_title": title_str,
                "summary": (
                    f"{emoji} *AMAZON ITEM PRICE {direction}* {status_badge}\n\n"
                    f"• Title: *{title_str}*\n"
                    f"• SKU: `{sku_str}`\n"
                    f"• ASIN: `{asin}`\n"
                    f"{order_line}\n"
                    f"• Current Price:  *${item_unit_price:.2f}* / unit\n"
                    f"• Previous Price: *${last_item_unit_price:.2f}* / unit ({last_date_str})\n"
                    f"• Price Shift:    *{'+$' if delta >= 0 else '-$'}{abs(delta):.2f}* / unit ({pct:+.1f}%)"
                ),
                "data": {
                    "current_price": item_unit_price,
                    "last_price": last_item_unit_price,
                    "delta": delta,
                    "pct": pct,
                    "last_date": last_date_str,
                }
            })

    # -------------------------------------------------------------
    # 2. Zoro LIST price changed — raw vs raw, threshold 2%
    # -------------------------------------------------------------
    # Compares the per-unit list price Zoro returned at cart-add for THIS order
    # (zoro_catalog_price = api_price) with the last one recorded for the same Zoro code
    # (order_placer_orders.zoro_list_price). No division by quantity, no coupon reversal:
    # both numbers are Zoro's own list price, so MOQ packs and coupons cannot fake a change.
    # First placement of a code seeds the baseline and cannot alert.
    try:
        _cur_list = float(order.get("zoro_catalog_price") or order.get("api_price") or 0.0)
    except (TypeError, ValueError):
        _cur_list = 0.0
    if _cur_list > 0 and zoro_code:
        try:
            from utils.unified_db import last_list_price
            _last_list = last_list_price(zoro_code, exclude_amazon_id=current_amz_id)
        except Exception:
            _last_list = None
        if _last_list and _last_list > 0:
            _d = _cur_list - _last_list
            _pct = (_d / _last_list) * 100.0
            if abs(_pct) >= LIST_PRICE_ALERT_PCT:
                _dir = "UP" if _d > 0 else "DOWN"
                _emoji = "📈" if _d > 0 else "📉"
                insights.append({
                    "code": "ZORO_LIST_PRICE_CHANGED",
                    "severity": "high" if abs(_pct) >= 10 else "medium",
                    "zoro_code": zoro_code,
                    "asin": asin,
                    "sku": order.get("seller_sku") or order.get("sku") or "",
                    "title": order.get("title") or order.get("item_title") or "",
                    "account": order.get("account") or "",
                    "summary": (
                        f"{_emoji} *ZORO LIST PRICE {_dir}* ({_pct:+.1f}%)\n\n"
                        f"• Zoro Code: `{zoro_code}`\n"
                        f"• Now:       *${_cur_list:.2f}* / unit\n"
                        f"• Before:    *${_last_list:.2f}* / unit\n"
                        f"• Change:    *{'+$' if _d >= 0 else '-$'}{abs(_d):.2f}* per unit\n"
                        f"• Order:     {current_amz_id}"
                        + (f"  (Zoro qty {zoro_qty})" if zoro_qty > 1 else "")
                    ),
                    "data": {"current_list_price": round(_cur_list, 2),
                             "last_list_price": round(_last_list, 2),
                             "delta": round(_d, 2), "pct": round(_pct, 2)},
                })

    # -------------------------------------------------------------
    # 2b. (legacy) Zoro Base Price Changed — derived from totals; off by default
    # -------------------------------------------------------------
    # Kept behind ZUHRA_LEGACY_BASE_PRICE_ALERT=1 for comparison only. It reverse-engineers a
    # unit price from an order total (÷ qty, un-coupon by code name) and misfires on MOQ
    # packs and coupon orders — the reason the raw rule above exists.
    if _LEGACY_BASE_PRICE_ALERT and current_base_unit > 0 and zoro_code:
        zoro_history = get_historical_zoro_costs(zoro_code, limit=10)
        current_wb = str(order.get("wb_order_id") or order.get("wb_id") or "")
        prior_zoro = [
            zh for zh in zoro_history
            if str(zh.get("amazon_order_id") or "") != current_amz_id
            and (not current_wb or str(zh.get("wb_order_id") or "") != current_wb)
        ]
        if prior_zoro:
            last_zh = prior_zoro[0]
            last_unit_base = compute_order_zoro_base_unit(last_zh, zoro_code=zoro_code)

            if last_unit_base > 0:
                cost_delta = current_base_unit - last_unit_base
                cost_pct = (cost_delta / last_unit_base) * 100.0
                # Alert only if change is at least 2.0% AND at least $0.50
                if abs(cost_pct) >= 2.0 and abs(cost_delta) >= 0.50:
                    cost_direction = "INCREASED" if cost_delta > 0 else "DECREASED"
                    emoji = "🏷️"
                    status_badge = "🔴" if cost_delta > 0 else "🟢"
                    order_line = f"• Order: `{current_amz_id}`\n" if current_amz_id else ""
                    insights.append({
                        "code": "ZORO_BASE_PRICE_CHANGED",
                        "emoji": emoji,
                        "title": f"Zoro Base Cost {cost_direction.title()}",
                        "severity": "warning" if cost_delta > 0 else "info",
                        "asin": asin,
                        "sku": sku_str,
                        "product_title": title_str,
                        "summary": (
                            f"{emoji} *ZORO BASE COST {cost_direction}* {status_badge}\n\n"
                            f"• Title: *{title_str}*\n"
                            f"• SKU: `{sku_str}`\n"
                            f"• Zoro Code: `{zoro_code}`\n"
                            f"• ASIN: `{asin}`\n"
                            f"{order_line}\n"
                            f"• New Base Unit: *${current_base_unit:.2f}*\n"
                            f"• Old Base Unit: *${last_unit_base:.2f}*\n"
                            f"• Cost Shift:    *{'+$' if cost_delta >= 0 else '-$'}{abs(cost_delta):.2f}* ({cost_pct:+.1f}%)"
                        ),
                        "data": {
                            "new_base": current_base_unit,
                            "old_base": last_unit_base,
                            "delta": cost_delta,
                            "pct": cost_pct,
                        }
                    })

    # -------------------------------------------------------------
    # 3. Zoro Code Changed (Alternative SKU Triggered)
    # -------------------------------------------------------------
    alt_used = order.get("alt_used") or order.get("alt_of") or ("ALT" in str(order.get("notes") or "").upper()) or ("- ALT" in str(order.get("code") or "").upper())
    main_sku = str(order.get("alt_of") or order.get("main_sku") or "").strip()
    if not main_sku or main_sku in ("Primary SKU", "None", "N/A"):
        try:
            from utils.telegram import _lookup_title_and_sku
            _, db_sku = _lookup_title_and_sku(asin)
            if db_sku and db_sku != zoro_code:
                main_sku = db_sku
        except Exception:
            pass
    main_sku = main_sku or "Primary SKU"
    main_sku = main_sku.replace(" - ALT", "").replace("- ALT", "").strip()

    if alt_used:
        order_line = f"• Order: `{current_amz_id}`\n" if current_amz_id else ""
        placed_code = zoro_code
        # Units actually placed on supplier
        placed_units = zoro_qty
        qty_placed_str = f"*{placed_units} unit{'s' if placed_units > 1 else ''}*"
        if amazon_qty != placed_units:
            qty_placed_str += f" ({amazon_qty} order pack)"

        insights.append({
            "code": "ALT_CODE_USED",
            "emoji": "🔄",
            "title": "Alternative SKU Used (Main OOS)",
            "severity": "info",
            "asin": asin,
            "sku": sku_str,
            "product_title": title_str,
            "summary": (
                f"🔄 *ALTERNATIVE SKU USED (Main OOS)*\n\n"
                f"• Title: *{title_str}*\n"
                f"• SKU: `{sku_str}`\n"
                f"• ASIN: `{asin}`\n"
                f"{order_line}\n"
                f"• Primary Zoro Code:     `{main_sku}` (❌ Out of Stock)\n"
                f"• Placed by Alternative: `{placed_code}` (✅ Available)\n"
                f"• Quantity Placed:       {qty_placed_str}"
            ),
            "data": {
                "main_sku": main_sku,
                "alt_sku": placed_code,
                "qty": placed_units,
                "amazon_qty": amazon_qty,
            }
        })

    # -------------------------------------------------------------
    # 4. Shipping Price Changed (Buyer Shipping Charge Mismatch)
    # -------------------------------------------------------------
    # Check if ASIN historically had buyer shipping charged >= $10.00
    if prior_orders:
        hist_shipping = [float(po.get("shipping_amt") or 0.0) for po in prior_orders if float(po.get("shipping_amt") or 0.0) > 0]
        if hist_shipping:
            avg_hist_ship = sum(hist_shipping) / len(hist_shipping)
            if avg_hist_ship >= 10.0 and shipping_amt < 1.0:
                order_line = f"• Order: `{current_amz_id}`\n" if current_amz_id else ""
                insights.append({
                    "code": "SHIPPING_MISMATCH",
                    "emoji": "⚠️",
                    "title": "Shipping Charge Mismatch (Free Shipping Charged)",
                    "severity": "alert",
                    "asin": asin,
                    "sku": sku_str,
                    "product_title": title_str,
                    "summary": (
                        f"⚠️ *SHIPPING CHARGE MISMATCH (Free Shipping Charged)*\n\n"
                        f"• Title: *{title_str}*\n"
                        f"• SKU: `{sku_str}`\n"
                        f"• ASIN: `{asin}`\n"
                        f"{order_line}\n"
                        f"• Customary Shipping:     *${avg_hist_ship:.2f}* (Charged on prior orders)\n"
                        f"• Current Order Shipping: *$0.00* (⚠️ FREE shipping charged to buyer!)\n"
                        f"• Payout Impact:          *-${avg_hist_ship:.2f}*\n"
                        f"• Action Required:        Verify Amazon Shipping Template immediately!"
                    ),
                    "data": {
                        "expected_shipping": avg_hist_ship,
                        "current_shipping": shipping_amt,
                    }
                })

    # -------------------------------------------------------------
    # 4b. Zoro Shipping Fee Anomaly (≠ standard $5.99 / free over $50)
    # -------------------------------------------------------------
    zoro_ship_charged = None
    for k in ("zoro_shipping", "shipping_cost", "zship"):
        if order.get(k) is not None:
            try:
                zoro_ship_charged = float(order.get(k))
                break
            except (ValueError, TypeError):
                pass
    if zoro_ship_charged is not None and zoro_ship_charged > 0:
        pre_coupon_subtotal = current_base_unit * zoro_qty if current_base_unit > 0 else 0.0
        if pre_coupon_subtotal <= 0:
            pre_coupon_subtotal = float(order.get("cost") or order.get("zoro_price") or 0.0)
        expected_ship = 0.0 if pre_coupon_subtotal >= 49.99 else 5.99
        ship_deviation = zoro_ship_charged - expected_ship
        if abs(ship_deviation) > 0.15:
            order_line = f"• Order: `{current_amz_id}`\n" if current_amz_id else ""
            insights.append({
                "code": "ZORO_SHIPPING_ANOMALY",
                "emoji": "🚚",
                "title": "Zoro Shipping Fee Deviation",
                "severity": "high" if zoro_ship_charged > 10.0 else "warning",
                "asin": asin,
                "sku": sku_str,
                "product_title": title_str,
                "summary": (
                    f"🚚 *ZORO SHIPPING FEE DEVIATION* ⚠️\n\n"
                    f"• Title: *{title_str}*\n"
                    f"• SKU: `{sku_str}`\n"
                    f"• Zoro Code: `{zoro_code}`\n"
                    f"• ASIN: `{asin}`\n"
                    f"{order_line}\n"
                    f"• Shipping Charged: *${zoro_ship_charged:.2f}*\n"
                    f"• Standard Expected: *${expected_ship:.2f}* (Pre-coupon Cart: ${pre_coupon_subtotal:.2f})\n"
                    f"• Shift:             *{'+$' if ship_deviation > 0 else '-$'}{abs(ship_deviation):.2f}*\n"
                    f"• Cause:             Carrier change, freight fee, or heavy item surcharge"
                ),
                "data": {
                    "zoro_shipping": zoro_ship_charged,
                    "expected_shipping": expected_ship,
                    "difference": ship_deviation,
                }
            })

    # -------------------------------------------------------------
    # 5. Zoro vs Amazon Delivery Window Mismatch (Late Risk & Buy Box Tuning)
    # -------------------------------------------------------------
    zoro_date = _parse_date(zoro_deliver_str)
    amz_deliver_by = _parse_date(deliver_by_str)
    if zoro_date and amz_deliver_by and zoro_date > amz_deliver_by:
        days_late = (zoro_date - amz_deliver_by).days
        order_line = f"• Order: `{current_amz_id}`\n" if current_amz_id else ""
        insights.append({
            "code": "DELIVERY_MISMATCH_LATE",
            "emoji": "⏰",
            "title": "Delivery Window Mismatch (OTDR Risk)",
            "severity": "alert",
            "asin": asin,
            "sku": sku_str,
            "product_title": title_str,
            "summary": (
                f"⏰ *DELIVERY WINDOW MISMATCH (Late Risk)*\n\n"
                f"• Title: *{title_str}*\n"
                f"• SKU: `{sku_str}`\n"
                f"• ASIN: `{asin}`\n"
                f"{order_line}\n"
                f"• Amazon Promise Date: *{amz_deliver_by.strftime('%a, %b %d')}*\n"
                f"• Zoro Est. Delivery:  *{zoro_date.strftime('%a, %b %d')}* (🚨 *{days_late} day{'s' if days_late > 1 else ''} LATE*)\n"
                f"• Carrier:             FedEx Ground\n"
                f"• Defect Risk:         Potential Late Delivery defect on OTDR"
            ),
            "data": {
                "amazon_deliver_by": str(amz_deliver_by),
                "zoro_deliver_date": str(zoro_date),
                "days_late": days_late,
            }
        })
    elif zoro_date and amz_deliver_by and (amz_deliver_by - zoro_date).days >= 2:
        days_early = (amz_deliver_by - zoro_date).days
        order_line = f"• Order: `{current_amz_id}`\n" if current_amz_id else ""
        insights.append({
            "code": "REDUCE_HANDLING_TIME",
            "emoji": "⚡",
            "title": "Reduce Amazon Handling Time",
            "severity": "info",
            "asin": asin,
            "sku": sku_str,
            "product_title": title_str,
            "summary": (
                f"⚡ *REDUCE HANDLING TIME OPPORTUNITY*\n\n"
                f"• Title: *{title_str}*\n"
                f"• SKU: `{sku_str}`\n"
                f"• ASIN: `{asin}`\n"
                f"{order_line}\n"
                f"• Amazon Promise Date: *{amz_deliver_by.strftime('%a, %b %d')}*\n"
                f"• Zoro Est. Delivery:  *{zoro_date.strftime('%a, %b %d')}* (⚡ *{days_early} days early*)\n"
                f"• Opportunity:         Supplier delivery is fast ({days_early}d ahead)!\n"
                f"• Recommendation:      Reduce Amazon handling time by {days_early} days to improve Buy Box win rate"
            ),
            "data": {
                "amazon_deliver_by": str(amz_deliver_by),
                "zoro_deliver_date": str(zoro_date),
                "days_early": days_early,
            }
        })

    # -------------------------------------------------------------
    # 6. Low Zoro Stock or Limited Stock Warning
    # -------------------------------------------------------------
    zoro_stock_note = str(order.get("zoro_stock") or order.get("notes") or "")
    if any(k in zoro_stock_note.lower() for k in ("limited stock", "limited quantity", "only 1 left", "only 2 left", "only 3 left")):
        insights.append({
            "code": "LOW_ZORO_STOCK",
            "emoji": "📦",
            "title": "Low Zoro Stock Warning",
            "severity": "warning",
            "asin": asin,
            "sku": sku_str,
            "product_title": title_str,
            "summary": (
                f"📦 *LOW SUPPLIER STOCK WARNING*\n\n"
                f"• Title: *{title_str}*\n"
                f"• SKU: `{sku_str}`\n"
                f"• Zoro Code: `{zoro_code}`\n"
                f"• ASIN: `{asin}`\n\n"
                f"• Supplier Inventory: ⚠️ *{zoro_stock_note}*\n"
                f"• Recommendation:     Monitor listing or reduce active inventory to avoid OOS"
            ),
            "data": {
                "zoro_code": zoro_code,
                "note": zoro_stock_note,
            }
        })

    # -------------------------------------------------------------
    # 7. Clean Net Earnings / Unit Changed (Excl Shipping & Tax)
    # -------------------------------------------------------------
    if earnings > 0 and amazon_qty > 0 and last_order:
        current_clean_unit = round((earnings - shipping_amt) / amazon_qty, 2)
        last_earn = float(last_order.get("earnings") or 0.0)
        last_ship = float(last_order.get("shipping_amt") or 0.0)
        last_qty = max(1, int(last_order.get("amazon_qty") or last_order.get("quantity") or 1))
        if last_earn > 0:
            last_clean_unit = round((last_earn - last_ship) / last_qty, 2)
            if last_clean_unit > 0 and abs(current_clean_unit - last_clean_unit) >= 1.00:
                clean_delta = current_clean_unit - last_clean_unit
                clean_pct = (clean_delta / last_clean_unit) * 100.0
                if abs(clean_pct) >= 5.0:
                    clean_direction = "INCREASED" if clean_delta > 0 else "DECREASED"
                    emoji = "💰" if clean_delta > 0 else "📉"
                    status_badge = "🟢" if clean_delta > 0 else "🔴"
                    order_line = f"• Order: `{current_amz_id}`\n" if current_amz_id else ""
                    insights.append({
                        "code": "CLEAN_EARNINGS_SHIFT",
                        "emoji": emoji,
                        "title": "Clean Net Earnings Per Unit Shift",
                        "severity": "info" if clean_delta > 0 else "warning",
                        "asin": asin,
                        "sku": sku_str,
                        "product_title": title_str,
                        "summary": (
                            f"{emoji} *CLEAN UNIT EARNINGS {clean_direction}* {status_badge}\n\n"
                            f"• Title: *{title_str}*\n"
                            f"• SKU: `{sku_str}`\n"
                            f"• ASIN: `{asin}` (Excluding ship & tax)\n"
                            f"{order_line}\n"
                            f"• Current Net Unit:  *${current_clean_unit:.2f}* / unit\n"
                            f"• Previous Net Unit: *${last_clean_unit:.2f}* / unit\n"
                            f"• Net Shift:         *{'+$' if clean_delta >= 0 else '-$'}{abs(clean_delta):.2f}* / unit ({clean_pct:+.1f}%)"
                        ),
                        "data": {
                            "current_clean": current_clean_unit,
                            "last_clean": last_clean_unit,
                            "delta": clean_delta,
                            "pct": clean_pct,
                        }
                    })

    # -------------------------------------------------------------
    # 8. Handling Time Changed
    # -------------------------------------------------------------
    p_date = _parse_date(purchase_date_str)
    s_date = _parse_date(ship_by_str)
    if p_date and s_date:
        handling_days = (s_date - p_date).days
        # Normal handling is 2-4 days. If Amazon reduced handling to <= 1 day:
        if handling_days <= 1:
            order_line = f"• Order: `{current_amz_id}`\n" if current_amz_id else ""
            insights.append({
                "code": "HANDLING_TIME_REDUCED",
                "emoji": "⚡",
                "title": "Amazon Handling Time Reduced",
                "severity": "warning",
                "asin": asin,
                "sku": sku_str,
                "product_title": title_str,
                "summary": (
                    f"⚡ *AMAZON HANDLING TIME REDUCED*\n\n"
                    f"• Title: *{title_str}*\n"
                    f"• SKU: `{sku_str}`\n"
                    f"• ASIN: `{asin}`\n"
                    f"{order_line}\n"
                    f"• Purchase Date: {p_date.strftime('%a, %b %d')}\n"
                    f"• Ship-By Date:  *{s_date.strftime('%a, %b %d')}* (⚡ *{handling_days} day{'s' if handling_days > 1 else ''} handling*)\n"
                    f"• Expected:      2-3 Days standard\n"
                    f"• Action:        Must fulfill promptly to avoid Late Shipment defect!"
                ),
                "data": {
                    "handling_days": handling_days,
                    "ship_by": str(s_date),
                    "purchase": str(p_date),
                }
            })

    return insights
