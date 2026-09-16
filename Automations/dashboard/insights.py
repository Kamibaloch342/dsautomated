#!/usr/bin/env python3
"""Business insights engine for the CRM dashboard.

Generates real-time, high-accuracy insights across 8 core categories:
  1. Amazon Earnings/Unit (Split: Increase ↑ vs Decrease ↓) partitioned by (account, asin)
  2. Amazon Buyer Shipping Status Changed (Free → Paid, Paid → Free)
  3. Zoro Base Price (Split: Increase ↑ vs Decrease ↓) reconstructed pre-coupon
  4. Zoro Shipping Fee Deviation (Actual ≠ expected $0/free or $5.99)
  5. Handling-Time Tuner (Slack between Amazon promise and Zoro ETA)
  6. Out of Stock (OOS) / Supplier Depletion & Alt SKU Substitution
  7. Low Margin Split (Placed < 8% vs Unplaced 'Loss-Kamran checking')
  8. Low Zoro Stock (< 20 units remaining)
Plus critical operational protections (Zip mismatch, Undelivered past due,
Refunded, Wrong quantity multiplier, Shipped late, WB unmatched).

Data sources: order_placer_orders, bot_decisions, archive_zoro, closing_orders.
Safe, query_only read against automations_unified.db (WAL compatible).
"""

import os
import re
import csv
import sqlite3
import json
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional, Set

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # Development_Unit
AUTO = os.path.join(ROOT, "Automations")
DB = os.environ.get("CLOUD_DB_PATH") if (os.environ.get("CLOUD_MODE") == "1" and os.environ.get("CLOUD_DB_PATH")) else os.path.join(AUTO, "data", "automations_unified.db")
VERIFIED_CSV = os.path.join(ROOT, "DS_Closing", "Hypersale_Closing", "verified_skus_cache.csv")

DELIVERED = ("Delivered", "AllDelivered")


def _digits(z: Any) -> str:
    return re.sub(r"\D", "", str(z or ""))


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _d(s: Any) -> Optional[date]:
    if not s:
        return None
    s = str(s).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    for fmt in ("%a, %b %d, %Y", "%b %d, %Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.split("T")[0], fmt).date()
        except ValueError:
            pass
    return None


def load_verified() -> Dict[str, Dict[str, Any]]:
    """ASIN -> {zoro_code, qty_to_place, zoro_multiple, second}."""
    out = {}
    if not os.path.exists(VERIFIED_CSV):
        return out
    try:
        with open(VERIFIED_CSV, newline="", encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                asin = (row.get("ASIN") or "").strip()
                if not asin:
                    continue
                out[asin] = {
                    "zoro_code": (row.get("Zoro Code") or "").strip(),
                    "qty_to_place": _f(row.get("Qty to Place")) or 1,
                    "zoro_multiple": _f(row.get("zoro_multiple")) or 1,
                    "second": (row.get("2nd_part") or "").strip(),
                }
    except Exception:
        pass
    return out


def _resolved_keys(con: sqlite3.Connection) -> Dict[str, Set[str]]:
    s: Dict[str, Set[str]] = {}
    try:
        for t, k in con.execute("SELECT insight_type, ref_key FROM insight_resolutions"):
            s.setdefault(t, set()).add(k)
    except sqlite3.OperationalError:
        pass
    return s


def compute(account: Optional[str] = None, date_from: Optional[str] = None,
            date_to: Optional[str] = None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Compute all operational insights across the requested account and date window."""
    con = sqlite3.connect(db_path or DB, timeout=10)
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row

    # Default window: last 60 days
    if not date_from and not date_to:
        date_from = (date.today() - timedelta(days=60)).isoformat()

    # 1. Fetch closing_orders in window
    cw, ca = ["1=1"], []
    if account and account not in ("All", "all", ""):
        cw.append("account = ?")
        ca.append(account)
    if date_from:
        cw.append("substr(purchase_date,1,10) >= ?")
        ca.append(date_from)
    if date_to:
        cw.append("substr(purchase_date,1,10) <= ?")
        ca.append(date_to)

    closing_rows = [dict(r) for r in con.execute(f"SELECT * FROM closing_orders WHERE {' AND '.join(cw)}", ca)]

    # 2. Fetch order_placer_orders in window (real-time stream)
    pw, pa = ["1=1"], []
    if account and account not in ("All", "all", ""):
        pw.append("account = ?")
        pa.append(account)
    if date_from:
        pw.append("(substr(purchase_date,1,10) >= ? OR substr(placed_at,1,10) >= ?)")
        pa.extend([date_from, date_from])
    if date_to:
        pw.append("(substr(purchase_date,1,10) <= ? OR substr(placed_at,1,10) <= ?)")
        pa.extend([date_to, date_to])

    placer_rows = [dict(r) for r in con.execute(f"SELECT * FROM order_placer_orders WHERE {' AND '.join(pw)}", pa)]

    # Merge orders by amazon_order_id
    orders_map: Dict[str, Dict[str, Any]] = {}
    for r in closing_rows:
        oid = r.get("amazon_order_id")
        if oid:
            orders_map[oid] = r

    fresh_wbs = []
    for r in placer_rows:
        oid = r.get("amazon_order_id")
        if not oid:
            continue
        if oid not in orders_map:
            orders_map[oid] = r
            wb = (r.get("wb_order_id") or "").strip()
            if wb:
                fresh_wbs.append(wb)
        else:
            existing = orders_map[oid]
            # Overlay fresh placer real-time placement values
            for fld in ("zoro_deliver_by", "zoro_stock", "alt_used", "main_sku", "placed_at", "zoro_qty"):
                val = r.get(fld)
                if val is not None and val != "":
                    existing[fld] = val

    # Fast chunked lookup in archive_zoro using indexed wb_order_id for fresh orders
    if fresh_wbs:
        for i in range(0, len(fresh_wbs), 500):
            chunk = fresh_wbs[i:i + 500]
            q = "SELECT wb_order_id, subtotal, shipping_cost, sku, ship_zip, coupon_savings FROM archive_zoro WHERE wb_order_id IN (%s)" % (",".join("?" * len(chunk)))
            for ar in con.execute(q, chunk):
                wb_id = ar["wb_order_id"]
                for o in orders_map.values():
                    if o.get("wb_order_id") == wb_id:
                        if not o.get("zoro_subtotal") and ar["subtotal"] is not None:
                            o["zoro_subtotal"] = ar["subtotal"]
                        if not o.get("zoro_shipping") and ar["shipping_cost"] is not None:
                            o["zoro_shipping"] = ar["shipping_cost"]
                        if not o.get("zoro_sku") and ar["sku"]:
                            o["zoro_sku"] = ar["sku"]
                        if not o.get("zoro_buyer_zip") and ar["ship_zip"]:
                            o["zoro_buyer_zip"] = ar["ship_zip"]
                        if not o.get("coupon_savings") and ar["coupon_savings"] is not None:
                            o["coupon_savings"] = ar["coupon_savings"]

    # 3. Fetch bot_decisions in window
    bw, ba = ["1=1"], []
    if date_from:
        bw.append("substr(ts,1,10) >= ?")
        ba.append(date_from)
    if date_to:
        bw.append("substr(ts,1,10) <= ?")
        ba.append(date_to)
    bot_rows = [dict(r) for r in con.execute(f"SELECT * FROM bot_decisions WHERE {' AND '.join(bw)}", ba)]

    # Set of fleet placed WBs for unmatched check
    placed_wbs = set()
    try:
        placed_wbs = {w[0] for w in con.execute("SELECT wb_order_id FROM order_placer_orders WHERE wb_order_id!=''")}
    except sqlite3.OperationalError:
        pass

    resolved = _resolved_keys(con)
    verified = load_verified()
    con.close()

    orders = list(orders_map.values())
    today = date.today().isoformat()

    out: Dict[str, List[Dict[str, Any]]] = {k: [] for k in [
        "earnings_increase", "earnings_decrease", "shipping_change",
        "zoro_price_increase", "zoro_price_decrease", "zoro_ship_rule",
        "handling_time_tune", "oos_alert", "low_margin", "low_zoro_stock",
        "zip_mismatch", "undelivered", "refunded", "wrong_qty", "shipped_late", "wb_unmatched"
    ]}

    def add(kind: str, ref_key: str, oid: str, msg: str, sev: str, extra: Optional[Dict[str, Any]] = None):
        if ref_key in resolved.get(kind, set()):
            return
        item = {
            "ref_key": ref_key,
            "order_id": oid or "",
            "message": msg,
            "severity": sev,
            "date": (extra and extra.get("date")) or today
        }
        if extra:
            item.update(extra)
        out[kind].append(item)

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY 6: Out of Stock (OOS) / Supplier Depletion & Alt SKU
    # ═══════════════════════════════════════════════════════════════════════════
    seen_oos = set()
    for b in bot_rows:
        if b.get("action") == "OOS":
            asin = b.get("asin")
            sku = b.get("sku") or ""
            d_str = (b.get("ts") or "")[:10]
            oid = b.get("amazon_id") or ""
            rk = f"{oid}:oos" if oid else f"{asin}:oos:{d_str}"
            if rk not in seen_oos and asin:
                seen_oos.add(rk)
                msg = f"ASIN {asin} (Zoro {sku}): Supplier is OUT OF STOCK{' on Order '+oid if oid else ''} — deactivate listing on Amazon immediately!"
                add("oos_alert", rk, oid, msg, "high", {"asin": asin, "sku": sku, "date": d_str, "link": "order" if oid else "asin"})

    for o in orders:
        oid = o.get("amazon_order_id") or ""
        m_id = str(o.get("merchant_order_id") or "")
        comm = str(o.get("comments") or "")
        asin = o.get("asin") or ""
        sku = o.get("zoro_sku") or o.get("sku") or ""
        d_str = str(o.get("purchase_date") or o.get("placed_at") or "")[:10]
        if "OOS" in m_id.upper() or "OOS" in comm.upper():
            rk = f"{oid}:oos"
            if rk not in seen_oos and oid:
                seen_oos.add(rk)
                msg = f"ASIN {asin} (Zoro {sku}): Supplier is OUT OF STOCK on Order {oid} — deactivate listing on Amazon immediately!"
                add("oos_alert", rk, oid, msg, "high", {"asin": asin, "sku": sku, "date": d_str, "link": "order"})

        # Alt SKU Substituted
        if o.get("alt_used") == "Yes":
            rk = f"{oid}:alt_sku"
            main_s = o.get("main_sku") or "primary"
            sub_s = o.get("sku") or o.get("zoro_sku") or "alt"
            msg = f"ASIN {asin}: Main Zoro SKU {main_s} is OOS — placed Alternative SKU {sub_s} on Order {oid}"
            add("oos_alert", rk, oid, msg, "med", {"asin": asin, "main_sku": main_s, "alt_sku": sub_s, "date": d_str, "link": "order"})

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY 7: Low Margin Split (Placed < 8% vs Unplaced Loss-Kamran)
    # ═══════════════════════════════════════════════════════════════════════════
    seen_loss = set()
    for b in bot_rows:
        if b.get("action") == "low_profit":
            oid = b.get("amazon_id") or ""
            asin = b.get("asin") or ""
            d_str = (b.get("ts") or "")[:10]
            rk = f"{oid}:loss_rejected" if oid else f"{asin}:loss_rejected:{d_str}"
            if rk not in seen_loss and (oid or asin):
                seen_loss.add(rk)
                msg = f"ASIN {asin}: REJECTED for low margin on Order {oid} — manual review required before ship deadline!"
                add("low_margin", rk, oid, msg, "high", {"asin": asin, "date": d_str, "link": "order" if oid else "asin"})

    for o in orders:
        oid = o.get("amazon_order_id") or ""
        asin = o.get("asin") or ""
        acct = o.get("account") or ""
        d_str = str(o.get("purchase_date") or o.get("placed_at") or "")[:10]
        m_id = str(o.get("merchant_order_id") or "")
        comm = str(o.get("comments") or "")
        pct = _f(o.get("profit_percent"))
        prof = _f(o.get("profit_dollars"))
        wb = (o.get("wb_order_id") or "").strip()

        # Sub-B: Loss-Kamran checking (unplaced / held)
        if ("LOSS" in m_id.upper() and "KAMRAN" in m_id.upper()) or ("LOSS" in comm.upper() and "KAMRAN" in comm.upper()):
            rk = f"{oid}:loss_kamran"
            if rk not in seen_loss and oid:
                seen_loss.add(rk)
                msg = f"ASIN {asin} ({acct}): REJECTED for low margin on Order {oid} (marked 'Loss-Kamran checking') — manual review required!"
                add("low_margin", rk, oid, msg, "high", {"asin": asin, "account": acct, "date": d_str, "link": "order"})
        elif wb and pct is not None and pct < 8.0:
            # Sub-A: Placed with margin < 8%
            rk = f"{oid}:low_margin_placed"
            is_neg = (prof is not None and prof < 0) or pct < 0
            sev = "high" if is_neg else "med"
            p_str = f"${prof:.2f}" if prof is not None else "—"
            msg = f"ASIN {asin} ({acct}): PLACED with low margin {pct:.1f}% (< 8%) on Order {oid} (Profit: {p_str}) — review pricing!"
            add("low_margin", rk, oid, msg, sev, {"asin": asin, "account": acct, "profit_percent": pct, "profit_dollars": prof, "date": d_str, "link": "order"})

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY 8: Low Zoro Stock (< 20)
    # ═══════════════════════════════════════════════════════════════════════════
    seen_stock = set()
    for o in orders:
        zs = _f(o.get("zoro_stock"))
        asin = o.get("asin")
        if zs is not None and zs < 20 and asin:
            d_str = str(o.get("purchase_date") or o.get("placed_at") or "")[:10]
            rk = f"{asin}:low_stock:{d_str}"
            if rk not in seen_stock:
                seen_stock.add(rk)
                sku = o.get("zoro_sku") or o.get("sku") or ""
                sev = "high" if zs < 5 else "med"
                msg = f"ASIN {asin} (Zoro {sku}): Supplier has LIMITED STOCK — only {int(zs)} unit(s) remaining!"
                add("low_zoro_stock", rk, o.get("amazon_order_id"), msg, sev, {"asin": asin, "sku": sku, "zoro_stock": int(zs), "date": d_str, "link": "asin"})

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY 5: Handling-Time Tuner (Slack between Promise and ETA)
    # ═══════════════════════════════════════════════════════════════════════════
    # Deduplicate per (account, asin) to provide high-leverage listing actions
    handled_slack: Dict[tuple, Dict[str, Any]] = {}
    for o in orders:
        zdb = _d(o.get("zoro_deliver_by"))
        adb = _d(o.get("deliver_by_date"))
        if zdb and adb:
            slack = (adb - zdb).days
            asin = o.get("asin")
            acct = o.get("account") or ""
            if not asin:
                continue
            key = (acct, asin)
            if key not in handled_slack:
                handled_slack[key] = {"min_slack": slack, "max_slack": slack, "order": o, "count": 1}
            else:
                h = handled_slack[key]
                h["count"] += 1
                if slack < h["min_slack"]:
                    h["min_slack"] = slack
                if slack > h["max_slack"]:
                    h["max_slack"] = slack
                # Keep latest order
                if str(o.get("purchase_date") or "") > str(h["order"].get("purchase_date") or ""):
                    h["order"] = o

    for (acct, asin), h in handled_slack.items():
        o = h["order"]
        oid = o.get("amazon_order_id") or ""
        zdb = _d(o.get("zoro_deliver_by"))
        adb = _d(o.get("deliver_by_date"))
        d_str = str(o.get("purchase_date") or o.get("placed_at") or "")[:10]
        if h["min_slack"] < 0:
            # Rule B: Zoro ETA is after Amazon promise -> INCREASE handling time
            late_days = abs(h["min_slack"])
            rk = f"{acct}:{asin}:increase_handling:{d_str}"
            msg = f"ASIN {asin} ({acct}): Zoro ETA {zdb} is AFTER Amazon promise {adb} ({late_days}d LATE) — INCREASE handling time on Amazon immediately!"
            add("handling_time_tune", rk, oid, msg, "high", {"asin": asin, "account": acct, "slack": h["min_slack"], "date": d_str, "link": "order"})
        elif h["max_slack"] >= 2:
            # Rule A: Zoro ETA is 2+ days earlier -> REDUCE handling time to win Buy Box
            rk = f"{acct}:{asin}:reduce_handling:{d_str}"
            msg = f"ASIN {asin} ({acct}): Zoro ETA {zdb} is {h['max_slack']}d ahead of Amazon promise {adb} — REDUCE handling time on Amazon to win Buy Box!"
            add("handling_time_tune", rk, oid, msg, "med", {"asin": asin, "account": acct, "slack": h["max_slack"], "date": d_str, "link": "order"})

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY 4: Zoro Shipping Fee Deviation
    # ═══════════════════════════════════════════════════════════════════════════
    for o in orders:
        oid = o.get("amazon_order_id") or ""
        wb = (o.get("wb_order_id") or "").strip()
        if not wb:
            continue
        zsub = _f(o.get("zoro_subtotal"))
        if zsub is None or zsub <= 0:
            tot = _f(o.get("zoro_order_total")) or 0.0
            tax = _f(o.get("zoro_tax")) or 0.0
            zsub = max(tot - tax, 0.0)
        cs = _f(o.get("coupon_savings")) or 0.0
        pre = zsub + cs
        zship = _f(o.get("zoro_shipping"))
        if zsub > 0 and zship is not None:
            exp = 0.0 if pre >= 49.99 else 5.99
            ok = abs(zship - exp) <= 0.02 or (exp == 5.99 and abs(zship - 5.00) <= 0.02)
            if not ok:
                rk = f"{oid}:zoro_ship_rule"
                sev = "high" if zship > 10.0 else "med"
                msg = f"Zoro shipping ${zship:.2f} on Order {oid} ≠ standard ${exp:.2f} (pre-coupon subtotal ${pre:.2f} — carrier change / freight surcharge)"
                d_str = str(o.get("purchase_date") or o.get("placed_at") or "")[:10]
                add("zoro_ship_rule", rk, oid, msg, sev, {
                    "asin": o.get("asin"), "zoro_shipping": zship, "expected_shipping": exp,
                    "pre_coupon_subtotal": round(pre, 2), "date": d_str, "link": "order"
                })

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORIES 1, 2, 3: Per (account, asin) and (asin) Transition Detection
    # ═══════════════════════════════════════════════════════════════════════════
    by_acct_asin: Dict[tuple, List[Dict[str, Any]]] = {}
    by_asin: Dict[str, List[Dict[str, Any]]] = {}
    for o in orders:
        asin = o.get("asin")
        acct = o.get("account") or "Unknown"
        if not asin or o.get("order_status") == "Cancelled":
            continue
        by_acct_asin.setdefault((acct, asin), []).append(o)
        by_asin.setdefault(asin, []).append(o)

    # 1. Earnings Increase & Decrease (Strictly partitioned by (account, asin))
    for (acct, asin), grp in by_acct_asin.items():
        if len(grp) < 2:
            continue
        grp_sorted = sorted(grp, key=lambda x: str(x.get("purchase_date") or x.get("placed_at") or ""))

        by_day = {}
        for g in grp_sorted:
            d = str(g.get("purchase_date") or g.get("placed_at") or "")[:10]
            if not d:
                continue
            earn = _f(g.get("earnings"))
            if earn is None or earn <= 0:
                continue
            ship = _f(g.get("shipping_amt")) or 0.0
            net_prod = earn - (ship * 0.85)
            qty = max(_f(g.get("quantity")) or 1.0, 1.0)
            u_earn = net_prod / qty
            if u_earn > 0:
                by_day.setdefault(d, {"vals": [], "oid": g.get("amazon_order_id")})["vals"].append(u_earn)

        runs = []
        for d in sorted(by_day):
            vals = sorted(by_day[d]["vals"])
            med = vals[len(vals) // 2]
            if runs and abs(runs[-1]["val"] - med) <= 0.02:
                runs[-1]["count"] += len(vals)
            else:
                runs.append({"val": med, "date": d, "oid": by_day[d]["oid"], "count": len(vals)})

        seen_vals = {round(runs[0]["val"], 2)} if runs else set()
        for i in range(1, len(runs)):
            o_val, n_val = runs[i - 1]["val"], runs[i]["val"]
            if round(n_val, 2) in seen_vals:
                continue
            seen_vals.add(round(n_val, 2))

            # Multi-Pack Divider Shield: suppress 2x, 3x, 4x jump on 1 order
            ratio = n_val / o_val if o_val > 0 else 1.0
            is_mult = any(abs(ratio - m) < 0.06 for m in (2.0, 3.0, 4.0)) or any(abs(1.0 / ratio - m) < 0.06 for m in (2.0, 3.0, 4.0))
            if is_mult and runs[i]["count"] == 1:
                continue

            diff = n_val - o_val
            pct_change = abs(diff) / o_val if o_val > 0 else 0.0
            if abs(diff) < 0.50 and pct_change < 0.02:
                continue

            d_str = runs[i]["date"]
            cnt = runs[i]["count"]
            oid = runs[i]["oid"]
            if diff > 0:
                rk = f"{acct}:{asin}:earn_inc:{d_str}"
                msg = f"ASIN {asin} ({acct}): earnings INCREASED ↑ ${o_val:.2f} → ${n_val:.2f}/unit (+${diff:.2f}, {cnt} order{'s' if cnt > 1 else ''} at new)"
                add("earnings_increase", rk, oid, msg, "med", {
                    "asin": asin, "account": acct, "old": round(o_val, 2),
                    "new": round(n_val, 2), "date": d_str, "link": "asin"
                })
            else:
                rk = f"{acct}:{asin}:earn_dec:{d_str}"
                sev = "high" if pct_change >= 0.10 else "med"
                msg = f"ASIN {asin} ({acct}): earnings DECREASED ↓ ${o_val:.2f} → ${n_val:.2f}/unit (-${abs(diff):.2f}, {cnt} order{'s' if cnt > 1 else ''} at new)"
                add("earnings_decrease", rk, oid, msg, sev, {
                    "asin": asin, "account": acct, "old": round(o_val, 2),
                    "new": round(n_val, 2), "date": d_str, "link": "asin"
                })

    # 2. Shipping Change (Free -> Paid vs Paid -> Free, strictly by (account, asin))
    for (acct, asin), grp in by_acct_asin.items():
        if len(grp) < 2:
            continue
        grp_sorted = sorted(grp, key=lambda x: str(x.get("purchase_date") or x.get("placed_at") or ""))
        consec_paid = 0
        prev_was_free = False
        for idx, g in enumerate(grp_sorted):
            ship = _f(g.get("shipping_amt")) or _f(g.get("shipping_price")) or 0.0
            oid = g.get("amazon_order_id") or ""
            d_str = str(g.get("purchase_date") or g.get("placed_at") or "")[:10]
            if ship > 0.01:
                # Case A: Free -> Paid
                if prev_was_free and idx > 0:
                    rk = f"{acct}:{asin}:ship_paid:{oid}"
                    msg = f"ASIN {asin} ({acct}): Buyer paid ${ship:.2f} shipping on Order {oid} (was Free shipping — check urgent delivery)"
                    add("shipping_change", rk, oid, msg, "med", {
                        "asin": asin, "account": acct, "shipping_amt": ship, "date": d_str, "link": "order"
                    })
                consec_paid += 1
                prev_was_free = False
            else:
                # Case C: Paid >= 3 orders -> Free
                if consec_paid >= 3:
                    rk = f"{acct}:{asin}:ship_revert_free:{oid}"
                    msg = f"ASIN {asin} ({acct}): Order {oid} has FREE shipping, but previous {consec_paid} orders had buyer-paid shipping — verify shipping template!"
                    add("shipping_change", rk, oid, msg, "high", {
                        "asin": asin, "account": acct, "date": d_str, "link": "order"
                    })
                consec_paid = 0
                prev_was_free = True

    # 3. Zoro Base Price Increase & Decrease (Reconstructed pre-coupon)
    for asin, grp in by_asin.items():
        if len(grp) < 2:
            continue
        grp_sorted = sorted(grp, key=lambda x: str(x.get("purchase_date") or x.get("placed_at") or ""))
        by_day = {}
        for g in grp_sorted:
            d = str(g.get("purchase_date") or g.get("placed_at") or "")[:10]
            if not d:
                continue
            zsub = _f(g.get("zoro_subtotal"))
            if zsub is None or zsub <= 0:
                tot = _f(g.get("zoro_order_total")) or 0.0
                tax = _f(g.get("zoro_tax")) or 0.0
                zsub = max(tot - tax, 0.0)
            if zsub <= 0:
                continue
            cs = _f(g.get("coupon_savings")) or 0.0
            pre = zsub + cs
            zq = max(_f(g.get("zoro_qty")) or _f(g.get("quantity")) or 1.0, 1.0)
            base_u = pre / zq
            sku_val = g.get("zoro_sku") or g.get("sku") or ""
            by_day.setdefault(d, {"vals": [], "oid": g.get("amazon_order_id"), "sku": sku_val})["vals"].append(base_u)

        runs = []
        for d in sorted(by_day):
            vals = sorted(by_day[d]["vals"])
            med = vals[len(vals) // 2]
            if runs and abs(runs[-1]["val"] - med) <= 0.02:
                runs[-1]["count"] += len(vals)
            else:
                runs.append({"val": med, "date": d, "oid": by_day[d]["oid"], "sku": by_day[d]["sku"], "count": len(vals)})

        seen_vals = {round(runs[0]["val"], 2)} if runs else set()
        for i in range(1, len(runs)):
            o_val, n_val = runs[i - 1]["val"], runs[i]["val"]
            if round(n_val, 2) in seen_vals:
                continue
            seen_vals.add(round(n_val, 2))
            diff = n_val - o_val
            pct_change = abs(diff) / o_val if o_val > 0 else 0.0
            if pct_change < 0.02 or abs(diff) < 0.10:
                continue
            d_str = runs[i]["date"]
            cnt = runs[i]["count"]
            oid = runs[i]["oid"]
            sku = runs[i]["sku"] or ""
            if diff > 0:
                rk = f"{asin}:zoro_price_inc:{d_str}"
                msg = f"Zoro SKU {sku} (ASIN {asin}): Zoro catalog price INCREASED ↑ ${o_val:.2f} → ${n_val:.2f}/unit (+${diff:.2f}, {cnt} order{'s' if cnt > 1 else ''} at new)"
                add("zoro_price_increase", rk, oid, msg, "high", {
                    "asin": asin, "sku": sku, "old": round(o_val, 2),
                    "new": round(n_val, 2), "date": d_str, "link": "asin"
                })
            else:
                rk = f"{asin}:zoro_price_dec:{d_str}"
                msg = f"Zoro SKU {sku} (ASIN {asin}): Zoro catalog price DECREASED ↓ ${o_val:.2f} → ${n_val:.2f}/unit (-${abs(diff):.2f}, {cnt} order{'s' if cnt > 1 else ''} at new)"
                add("zoro_price_decrease", rk, oid, msg, "med", {
                    "asin": asin, "sku": sku, "old": round(o_val, 2),
                    "new": round(n_val, 2), "date": d_str, "link": "asin"
                })

    # ═══════════════════════════════════════════════════════════════════════════
    # Preserved Core Operational Protections (Zip, Undelivered, Refunded, etc.)
    # ═══════════════════════════════════════════════════════════════════════════
    for o in orders:
        oid = o.get("amazon_order_id") or ""
        asin = o.get("asin") or ""
        d_str = str(o.get("purchase_date") or o.get("placed_at") or "")[:10]

        # Zip Mismatch
        az, zz = _digits(o.get("ship_postal_code"))[:5], _digits(o.get("zoro_buyer_zip"))[:5]
        if len(az) == 5 and len(zz) == 5 and az != zz:
            rk = f"{oid}:zip_mismatch"
            msg = f"Ship zip {o.get('ship_postal_code')} != Zoro {o.get('zoro_buyer_zip')}"
            add("zip_mismatch", rk, oid, msg, "high", {
                "asin": asin, "ship_city": o.get("ship_city"), "date": d_str, "link": "order"
            })

        # Undelivered past due
        ds = o.get("delivery_status")
        if ds and ds not in DELIVERED and o.get("manual_delivered") != "Yes":
            db_ = str(o.get("deliver_by_date") or "")[:10]
            if db_ and db_ < today:
                rk = f"{oid}:undelivered"
                msg = f"Not delivered (status {ds}), due {db_}"
                add("undelivered", rk, oid, msg, "high", {
                    "asin": asin, "delivery_status": ds, "deliver_by_date": db_, "date": d_str, "link": "order"
                })

        # Refunded
        if o.get("refund_done") == "Yes":
            rk = f"{oid}:refunded"
            deliv = o.get("delivery_status") in DELIVERED
            ref_amt = _f(o.get("refund_amount")) or 0.0
            msg = f"Refunded ${ref_amt:.2f} — {'returned by customer' if deliv else 'NOT delivered → claim from Zoro'}"
            add("refunded", rk, oid, msg, "high", {
                "asin": asin, "refund_amount": ref_amt, "delivery_status": ds, "date": d_str, "link": "order"
            })

        # Wrong Qty vs verified
        v = verified.get(asin)
        if v:
            exp_qty = (_f(o.get("quantity")) or 1) * v["qty_to_place"] * v["zoro_multiple"]
            zq = _f(o.get("zoro_qty"))
            if zq and zq > 0 and exp_qty and abs(zq - exp_qty) >= 1:
                rk = f"{oid}:wrong_qty"
                msg = f"Placed Zoro qty {zq:g}, expected {exp_qty:g} (×{v['qty_to_place']:g}×{v['zoro_multiple']:g})"
                add("wrong_qty", rk, oid, msg, "high", {
                    "asin": asin, "zoro_qty": zq, "expected_qty": exp_qty, "date": d_str, "link": "order"
                })

        # Shipped LATE vs Amazon ship-by
        if o.get("shipped_on_time") == "Late":
            mh = _f(o.get("ship_margin_hrs"))
            by = f" by {abs(mh)/24:.1f}d" if mh is not None else ""
            rk = f"{oid}:shipped_late"
            add("shipped_late", rk, oid, f"Shipped LATE vs ship-by{by}", "high", {
                "asin": asin, "ship_margin_hrs": mh, "date": d_str, "link": "order"
            })

        # WB unmatched
        wb = (o.get("wb_order_id") or "").strip()
        if wb and placed_wbs and wb not in placed_wbs and o.get("order_status") != "Cancelled" and o.get("order_label") != "Pending":
            rk = f"{oid}:wb_unmatched"
            add("wb_unmatched", rk, oid, f"WB {wb} not in placed orders — verify/cancel", "med", {
                "asin": asin, "wb": wb, "date": d_str, "link": "order"
            })

    counts = {k: len(v) for k, v in out.items()}
    return {"insights": out, "counts": counts, "total": sum(counts.values())}


if __name__ == "__main__":
    import sys
    acct = sys.argv[1] if len(sys.argv) > 1 else None
    res = compute(account=acct)
    print("Counts:", json.dumps(res["counts"], indent=2))
    print("Total alerts:", res["total"])
    for k, v in res["insights"].items():
        if v:
            print(f"\n{k} ({len(v)}):")
            for a in v[:3]:
                print("   ", a.get("order_id") or a.get("asin"), "|", a["message"])
