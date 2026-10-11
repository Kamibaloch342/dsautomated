"""
sales_drops.py — Attention → Sales drops: good sellers whose orders fell, 48 h against the 48 h before.

The owner's rule (11 Oct 2026): only listings that sell well are worth the time — an ASIN qualifies
when it had at least `min_orders` (10) orders in the EARLIER window — and it is flagged when the
latest window fell by more than `drop_pct` (25 %). "10 orders on 1–2 Oct, 6 on 3–4 Oct" is −40 %.

Source: amazon_report_orders — Amazon's own order list, the table the Daily report counts from,
refreshed from every all-orders report the tracker (every 30 min) and closing download. Days are
Seller Central's (US/Pacific). An order counts once per ASIN in it; cancelled orders are not demand
(engine/listings.py's rule too).

The windows end on the last day the data COVERS IN FULL, not on yesterday: a report downloaded at
15:39 PT on 10 Oct holds only part of 10 Oct, and comparing that half day against two whole days
would flag every good seller. Each store's own latest report sets its cut-off.
"""
import os
import sqlite3
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    _PT = ZoneInfo("America/Los_Angeles")
except Exception:
    from datetime import timezone
    _PT = timezone(timedelta(hours=-7))

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
DB = os.environ.get("CLOUD_DB_PATH") if (os.environ.get("CLOUD_MODE") == "1" and os.environ.get("CLOUD_DB_PATH")) \
    else os.path.join(AUTO, "data", "automations_unified.db")

STORES = ("HOANK", "Hathoro")
TREND_DAYS = 14


def _ro():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA query_only=ON")
    return c


def _num(q, k, d, lo, hi):
    try:
        v = float(((q.get(k) or [d])[0]))
    except (TypeError, ValueError):
        v = d
    return max(lo, min(hi, v))


def complete_through(c, store):
    """The last Pacific day this store's data holds in full: the day before its newest report was
    downloaded. Falls back to the last day with orders, minus one, when the file record is gone."""
    row = c.execute("SELECT MAX(mtime) FROM amazon_report_files WHERE account = ?", (store,)).fetchone()
    if row and row[0]:
        return datetime.fromtimestamp(float(row[0]), _PT).date() - timedelta(days=1)
    row = c.execute("SELECT MAX(purchase_day) FROM amazon_report_orders WHERE account = ?", (store,)).fetchone()
    if row and row[0]:
        return date.fromisoformat(row[0]) - timedelta(days=1)
    return None


def sales_drops(q):
    g = lambda k, d="": ((q.get(k) or [d])[0] or d).strip()
    days = int(_num(q, "days", 2, 1, 14))
    min_orders = int(_num(q, "min_orders", 10, 1, 10000))
    drop_pct = _num(q, "drop_pct", 25, 1, 100)
    acct = g("account")
    stores = [acct] if acct in STORES else list(STORES)

    c = _ro()
    try:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='amazon_report_orders'").fetchone():
            return {"ready": False, "error": "amazon_report_orders is missing", "rows": []}
        rows, windows = [], {}
        for store in stores:
            end = complete_through(c, store)
            if not end:
                continue
            a0 = end - timedelta(days=days - 1)              # latest window  [a0 .. end]
            b1 = a0 - timedelta(days=1)
            b0 = b1 - timedelta(days=days - 1)               # earlier window [b0 .. b1]
            t0 = end - timedelta(days=TREND_DAYS - 1)
            lo = min(b0, t0).isoformat()
            windows[store] = {"latest": [a0.isoformat(), end.isoformat()],
                              "earlier": [b0.isoformat(), b1.isoformat()]}
            per = {}
            for oid, day, asins, units, sales, title, skus in c.execute(
                    """SELECT amazon_order_id, purchase_day, asins, units, sales, title, skus
                       FROM amazon_report_orders
                       WHERE account = ? AND purchase_day BETWEEN ? AND ?
                         AND COALESCE(order_status, '') != 'Cancelled'""", (store, lo, end.isoformat())):
                alist = [a.strip().upper() for a in str(asins or "").replace(";", ",").split(",") if a.strip()]
                if not alist:
                    continue
                share = 1.0 / len(alist)                     # a 2-ASIN order's sales split evenly
                for a in alist:
                    p = per.setdefault(a, {"A": set(), "B": set(), "uA": 0.0, "uB": 0.0, "sA": 0.0, "sB": 0.0,
                                           "daily": {}, "title": "", "skus": set()})
                    p["daily"][day] = p["daily"].get(day, 0) + 1
                    if title and (len(alist) == 1 or not p["title"]):
                        p["title"] = title
                    if skus and len(alist) == 1:
                        p["skus"].add(str(skus).strip())
                    d = date.fromisoformat(day)
                    if a0 <= d <= end:
                        p["A"].add(oid); p["uA"] += (units or 0) * share; p["sA"] += (sales or 0) * share
                    elif b0 <= d <= b1:
                        p["B"].add(oid); p["uB"] += (units or 0) * share; p["sB"] += (sales or 0) * share
            for a, p in per.items():
                nb, na = len(p["B"]), len(p["A"])
                if nb < min_orders:
                    continue
                change = (na - nb) / nb * 100.0
                pa = (p["sA"] / p["uA"]) if p["uA"] else None
                pb = (p["sB"] / p["uB"]) if p["uB"] else None
                trend = []
                d = end - timedelta(days=TREND_DAYS - 1)
                while d <= end:
                    trend.append(p["daily"].get(d.isoformat(), 0))
                    d += timedelta(days=1)
                rows.append({
                    "store": store, "asin": a, "title": p["title"], "sku": ", ".join(sorted(p["skus"]))[:120],
                    "earlier": nb, "latest": na, "change_pct": round(change, 1), "lost": nb - na,
                    "alert": change < -drop_pct,
                    "units_earlier": round(p["uB"], 1), "units_latest": round(p["uA"], 1),
                    "sales_earlier": round(p["sB"], 2), "sales_latest": round(p["sA"], 2),
                    "price_earlier": round(pb, 2) if pb else None, "price_latest": round(pa, 2) if pa else None,
                    # our own price moved up — the first thing to rule out when orders fall
                    "price_up": bool(pa and pb and pa > pb * 1.03),
                    "trend": trend, "trend_end": end.isoformat(),
                })
    finally:
        c.close()

    rows.sort(key=lambda r: (not r["alert"], -r["lost"] if r["alert"] else r["change_pct"]))
    alerts = [r for r in rows if r["alert"]]
    return {"ready": True, "rows": rows, "alerts": len(alerts), "watched": len(rows),
            "orders_lost": sum(r["lost"] for r in alerts),
            "rule": {"days": days, "min_orders": min_orders, "drop_pct": drop_pct},
            "windows": windows, "stores": stores}
