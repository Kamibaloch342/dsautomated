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
            """SELECT amazon_order_id, asin, sku, title, quantity, earnings, amazon_fees,
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
            """SELECT amazon_order_id, asin, sku, title, quantity, earnings, amazon_fees,
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


def get_historical_zoro_costs(zoro_code: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Retrieve previous unit costs for a Zoro code."""
    if not zoro_code:
        return []
    zoro_code = zoro_code.strip().upper()
    c = _get_db()
    if not c:
        return []
    try:
        rows = c.execute(
            """SELECT amazon_order_id, sku, quantity, zoro_order_total, zoro_qty, placed_at
               FROM order_placer_orders
               WHERE UPPER(sku) = ? AND zoro_order_total > 0
               ORDER BY id DESC LIMIT ?""",
            (zoro_code, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
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
    zoro_code = (order.get("sku") or "").strip().upper()
    current_amz_id = str(order.get("amazon_id") or order.get("amazon_order_id") or "")
    amazon_qty = max(1, int(order.get("amazon_qty") or order.get("order_qty") or order.get("quantity") or 1))
    zoro_qty = max(1, int(order.get("zoro_qty") or order.get("qty") or 1))
    qty = amazon_qty
    title = str(order.get("title") or order.get("item_title") or "").strip()
    sku = str(order.get("seller_sku") or order.get("sku") or zoro_code).strip()

    # Guaranteed fallback lookup for Title & SKU if missing
    if (not title or not sku or sku in ("N/A", "None", "")) and asin:
        try:
            from utils.telegram import _lookup_title_and_sku
            db_t, db_s = _lookup_title_and_sku(asin)
            title = title or db_t
            sku = sku or db_s or zoro_code
        except Exception:
            pass

    title_str = title[:80] if title else "Product Listing"
    sku_str = sku if sku else (zoro_code or "N/A")

    # Current financial numbers
    earnings = float(order.get("earnings") or 0.0)
    shipping_amt = float(order.get("shipping_amt") or 0.0)
    raw_zoro_cost = float(order.get("unit_cost") or order.get("zoro_price") or order.get("cost") or 0.0)
    # If raw_zoro_cost was total order cost, convert to per-unit cost
    if order.get("unit_cost"):
        zoro_unit_cost = float(order.get("unit_cost"))
    elif raw_zoro_cost > 0 and zoro_qty > 1:
        zoro_unit_cost = round(raw_zoro_cost / zoro_qty, 2)
    else:
        zoro_unit_cost = raw_zoro_cost

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
    zoro_deliver_str = order.get("zoro_deliver_by") or order.get("zoro_delivery_date")

    # Fetch history
    history = get_historical_asin_orders(asin, limit=10)
    # Filter out current order if it was already inserted
    prior_orders = [h for h in history if str(h.get("amazon_order_id")) != current_amz_id]
    last_order = prior_orders[0] if prior_orders else None

    # -------------------------------------------------------------
    # 1. Amazon Item Price Changed
    # -------------------------------------------------------------
    if item_unit_price > 0 and last_order:
        last_item_price = float(last_order.get("item_price") or 0.0)
        last_qty = max(1, int(last_order.get("amazon_qty") or last_order.get("quantity") or 1))
        if last_item_price > 0:
            last_item_unit_price = round(last_item_price / last_qty, 2)
        else:
            # If item_price wasn't directly in orders, compute from earnings / qty
            last_earn = float(last_order.get("earnings") or 0.0)
            last_fees = float(last_order.get("amazon_fees") or 0.0)
            last_ship = float(last_order.get("shipping_amt") or 0.0)
            last_item_unit_price = round((last_earn + last_fees - last_ship) / last_qty, 2)

        if last_item_unit_price > 0 and abs(item_unit_price - last_item_unit_price) >= 0.50:
            delta = item_unit_price - last_item_unit_price
            pct = (delta / last_item_unit_price) * 100.0
            last_date_str = last_order.get("purchase_date") or last_order.get("placed_at") or "earlier"
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
    # 2. Zoro Base Price Changed (Excluding Flat Shipping)
    # -------------------------------------------------------------
    if zoro_unit_cost > 0 and zoro_code:
        zoro_history = get_historical_zoro_costs(zoro_code, limit=5)
        prior_zoro = [zh for zh in zoro_history if str(zh.get("amazon_order_id")) != current_amz_id]
        if prior_zoro:
            last_zh = prior_zoro[0]
            last_tot = float(last_zh.get("zoro_order_total") or 0.0)
            last_zq = max(1, int(last_zh.get("zoro_qty") or last_zh.get("quantity") or 1))
            # Remove approximate $5.99 shipping if total > $20
            last_ship_est = 5.99 if last_tot > 20 and last_tot < 55 else 0.0
            last_unit_base = round((last_tot - last_ship_est) / last_zq, 2)

            if last_unit_base > 0 and abs(zoro_unit_cost - last_unit_base) >= 0.50:
                cost_delta = zoro_unit_cost - last_unit_base
                cost_pct = (cost_delta / last_unit_base) * 100.0
                cost_direction = "INCREASED" if cost_delta > 0 else "DECREASED"
                status_badge = "🔴" if cost_delta > 0 else "🟢"
                insights.append({
                    "code": "ZORO_BASE_PRICE_CHANGED",
                    "emoji": "🏷️",
                    "title": "Zoro Base Cost Changed",
                    "severity": "warning" if cost_delta > 0 else "info",
                    "asin": asin,
                    "sku": sku_str,
                    "product_title": title_str,
                    "summary": (
                        f"🏷️ *ZORO BASE COST {cost_direction}* {status_badge}\n\n"
                        f"• Title: *{title_str}*\n"
                        f"• SKU: `{sku_str}`\n"
                        f"• Zoro Code: `{zoro_code}`\n"
                        f"• ASIN: `{asin}`\n\n"
                        f"• New Base Unit: *${zoro_unit_cost:.2f}*\n"
                        f"• Old Base Unit: *${last_unit_base:.2f}*\n"
                        f"• Cost Shift:    *{'+$' if cost_delta >= 0 else '-$'}{abs(cost_delta):.2f}* ({cost_pct:+.1f}%)"
                    ),
                    "data": {
                        "new_base": zoro_unit_cost,
                        "old_base": last_unit_base,
                        "delta": cost_delta,
                        "pct": cost_pct,
                    }
                })

    # -------------------------------------------------------------
    # 3. Zoro Code Changed (Alternative SKU Triggered)
    # -------------------------------------------------------------
    alt_used = order.get("alt_used") or order.get("alt_of") or ("ALT" in str(order.get("notes") or "").upper()) or ("- ALT" in str(order.get("code") or "").upper())
    main_sku = order.get("alt_of") or order.get("main_sku")
    if not main_sku or main_sku == "Primary SKU":
        try:
            from utils.telegram import _lookup_title_and_sku
            _, db_sku = _lookup_title_and_sku(asin)
            if db_sku and db_sku != zoro_code:
                main_sku = db_sku
        except Exception:
            pass
    main_sku = main_sku or "Primary SKU"

    if alt_used:
        order_line = f"• Order: `{current_amz_id}`\n" if current_amz_id else ""
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
                f"• Placed by Alternative: `{zoro_code}` (✅ Available)\n"
                f"• Quantity Placed:       *{qty} unit{'s' if qty > 1 else ''}*"
            ),
            "data": {
                "main_sku": main_sku,
                "alt_sku": zoro_code,
                "qty": qty,
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
    # 5. Zoro vs Amazon Delivery Window Mismatch (Late Risk)
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
