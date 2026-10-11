"""
auto_refunds.py — the dashboard's Auto Refunds tab: every refund Amazon told us about, day by day.

Source: Amazon's "Refund Initiated for Order" e-mails. HOANK's arrive in qurtdis, Hathoro's in
abecomllc26 (config/accounts.json → accounts.<store>.earnings_inbox). They are already captured:
the hourly sync (scripts/auto_sync_10min.py) runs gmail_fetch/build_gmail_archive.py, which parses
them into gmail_archive.db → return_emails (email_type='refund_initiated'), and
scripts/sync_gmail_archive.py copies them into automations_unified.db → archive_returns. This
module only reads that table, so the tab is as live as the archive: a refund issued now shows up
after the next hourly sync. archive_returns ships in the slim DB, so Render shows it too.

Checked 11 Oct 2026 against Gmail itself: every "Refund Initiated" message in both inboxes since
1 Sep was in the archive (11 had been missed — 8–9 Sep on qurtdis among them — and were fetched).

Each refund is joined to the order it belongs to — closing_orders (what we placed: WB, Zoro cost,
profit, delivery, the case we opened) and amazon_report_orders (what Amazon sold) — and to any
return request for the same order, so a refund issued with nothing coming back is visible.

Days are Seller Central's (US/Pacific), like the Daily report. The archive stamps e-mails in this
Mac's local time (Asia/Karachi), so each one is converted.

archive_returns has a unique key (order, timestamp, type, inbox), so it holds no duplicates — unlike
gmail_archive.db's return_emails, which carries 114 duplicate rows from old backfills.
"""
import csv
import io
import os
import sqlite3
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    _PKT = ZoneInfo("Asia/Karachi")
    _PT = ZoneInfo("America/Los_Angeles")
except Exception:                       # no tz database: fall back to fixed offsets
    from datetime import timezone
    _PKT = timezone(timedelta(hours=5))
    _PT = timezone(timedelta(hours=-7))

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
DB = os.environ.get("CLOUD_DB_PATH") if (os.environ.get("CLOUD_MODE") == "1" and os.environ.get("CLOUD_DB_PATH")) \
    else os.path.join(AUTO, "data", "automations_unified.db")

# Who issued the refund, as Amazon words it in the e-mail. Kept as Amazon says it; these are the
# values seen since Sep 2026. Anything else is shown as-is.
RESOLUTIONS = ("Customer Services", "Seller Issued Refund", "Returnless Refund", "A-to-Z Guarantee Claim")


def _ro():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA query_only=ON")
    return c


def _rows(c, sql, args=()):
    cur = c.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _inbox_store():
    """{'qurtdis': 'HOANK', 'abecomllc26': 'Hathoro'} from config/accounts.json (with the
    loader's own fallback, which Render has too)."""
    out = {"qurtdis": "HOANK", "abecomllc26": "Hathoro"}
    try:
        import sys
        if AUTO not in sys.path:
            sys.path.insert(0, AUTO)
        from utils.accounts_config import accounts
        for name, cfg in (accounts() or {}).items():
            inbox = (cfg or {}).get("earnings_inbox")
            if inbox:
                out[str(inbox)] = name
    except Exception:
        pass
    return out


def _day(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _pt(ts):
    """'2026-10-11 08:16:56' (archive local time, Asia/Karachi) → aware US/Pacific datetime."""
    try:
        return datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_PKT).astimezone(_PT)
    except (TypeError, ValueError):
        return None


def _reason(raw, title):
    """Amazon's refund reason, when the e-mail had one. The parser sometimes lands the product
    title in this field ('Simple Green Concentrated All-Purpose Cleaner/Degreaser'); a real reason
    is short ('Order not received', 'Damaged'), so anything long or equal to the title is dropped."""
    r = (raw or "").strip()
    if not r or len(r) > 32:
        return ""
    if title and r.lower() in str(title).lower():
        return ""
    return r


def _f(v):
    try:
        return round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _default_range(today):
    """From the 1st of LAST month to today — in October that is 1 Sep → today, the owner's ask."""
    first_this = today.replace(day=1)
    first_prev = (first_this - timedelta(days=1)).replace(day=1)
    return first_prev, today


def auto_refunds(q):
    """Refunds between `from` and `to` (Pacific days), newest first, with the order they belong to.
    Filters: store=HOANK|Hathoro, resolution=<Amazon's wording>, q=<order id / ASIN / SKU / title /
    customer / WB>, returned=yes|no."""
    g = lambda k, d="": ((q.get(k) or [d])[0] or d).strip()
    today = datetime.now(_PT).date()
    d0, d1 = _default_range(today)
    d_from = _day(g("from")) or d0
    d_to = _day(g("to")) or d1
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    store_f, res_f, text, ret_f = g("store"), g("resolution"), g("q").lower(), g("returned").lower()

    inbox_store = _inbox_store()
    c = _ro()
    try:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='archive_returns'").fetchone():
            # On Render the table only exists once the cloud agent on the main Mac ships it in the
            # slim DB (cloud_agent.SLIM_TABLES) — an agent started before 11 Oct 2026 does not.
            # Locally it is missing only if the archive sync has never run.
            if os.environ.get("CLOUD_MODE") == "1":
                why = ("This online copy has no refunds yet. The cloud agent on the main Mac uploads them "
                       "once it is restarted with the 11 Oct update — then they appear here within a few minutes.")
            else:
                why = "archive_returns is missing — run scripts/sync_gmail_archive.py"
            return {"ready": False, "error": why, "rows": [], "days": [], "totals": {}}

        # A Pacific day spans 12–13 h of the next Karachi day, so read one day either side and
        # keep exactly the Pacific range below.
        lo = (d_from - timedelta(days=1)).isoformat()
        hi = (d_to + timedelta(days=2)).isoformat()
        refunds = _rows(c, """
            SELECT amazon_order_id, gmail_account, email_timestamp, refund_amount, item_price_refund,
                   outbound_shipping_refund, refund_reason, refund_resolution, customer_name, asin, sku
            FROM archive_returns
            WHERE email_type = 'refund_initiated' AND email_timestamp >= ? AND email_timestamp < ?""", (lo, hi))

        kept = []
        for r in refunds:
            when = _pt(r["email_timestamp"])
            if when is None or not (d_from <= when.date() <= d_to):
                continue
            r["_when"] = when
            kept.append(r)

        ids = sorted({r["amazon_order_id"] for r in kept if r.get("amazon_order_id")})
        closing, report, returns, all_refunds = {}, {}, {}, {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            ph = ",".join("?" * len(chunk))
            for o in _rows(c, f"""
                    SELECT amazon_order_id, account, title, asin, sku, quantity, item_price, earnings,
                           purchase_date, wb_order_id, zoro_order_total, zoro_order_status, profit_dollars,
                           net_profit_after_refund, order_label, delivery_status, delivery_date,
                           case_status, case_type, zoro_case_number, zoro_refund_amount, flag_color,
                           ship_city, ship_state
                    FROM closing_orders WHERE amazon_order_id IN ({ph})""", chunk):
                closing.setdefault(o["amazon_order_id"], o)
            for o in _rows(c, f"""
                    SELECT amazon_order_id, account, title, asins, skus, units, sales, purchase_day, order_status
                    FROM amazon_report_orders WHERE amazon_order_id IN ({ph})""", chunk):
                report.setdefault(o["amazon_order_id"], o)
            for o in _rows(c, f"""
                    SELECT amazon_order_id, MIN(email_timestamp) AS at, MAX(return_reason) AS reason,
                           MAX(customer_comment) AS comment
                    FROM archive_returns WHERE email_type = 'return_auth' AND amazon_order_id IN ({ph})
                    GROUP BY amazon_order_id""", chunk):
                returns[o["amazon_order_id"]] = o
            # every refund ever issued on these orders, not just the ones in range — a second
            # partial refund on an order is worth seeing next to the first
            for o in _rows(c, f"""
                    SELECT amazon_order_id, COUNT(*) AS n, SUM(refund_amount) AS total
                    FROM archive_returns WHERE email_type = 'refund_initiated' AND amazon_order_id IN ({ph})
                    GROUP BY amazon_order_id""", chunk):
                all_refunds[o["amazon_order_id"]] = o
        last = c.execute("SELECT MAX(email_timestamp), MAX(fetched_at) FROM archive_returns "
                         "WHERE email_type = 'refund_initiated'").fetchone()
    finally:
        c.close()

    rows = []
    for r in kept:
        oid = r.get("amazon_order_id") or ""
        co, ro, rt, ar = closing.get(oid) or {}, report.get(oid) or {}, returns.get(oid), all_refunds.get(oid) or {}
        store = co.get("account") or ro.get("account") or inbox_store.get(r.get("gmail_account") or "", "")
        title = co.get("title") or ro.get("title") or ""
        res = (r.get("refund_resolution") or "").strip()
        row = {
            "order_id": oid,
            "store": store,
            "inbox": r.get("gmail_account") or "",
            "refunded_at": r["_when"].strftime("%Y-%m-%d %H:%M"),
            "day": r["_when"].date().isoformat(),
            "amount": _f(r.get("refund_amount")),
            "item_refund": _f(r.get("item_price_refund")),
            "shipping_refund": _f(r.get("outbound_shipping_refund")),
            "resolution": res,
            "reason": _reason(r.get("refund_reason"), title),
            "customer": (r.get("customer_name") or "").strip(),
            "title": title,
            "asin": co.get("asin") or r.get("asin") or ro.get("asins") or "",
            "sku": co.get("sku") or r.get("sku") or ro.get("skus") or "",
            "qty": co.get("quantity") or ro.get("units"),
            "sale": _f(co.get("item_price")) if co.get("item_price") not in (None, "") else _f(ro.get("sales")),
            "earnings": _f(co.get("earnings")),
            "purchase_date": (co.get("purchase_date") or ro.get("purchase_day") or "")[:10],
            "wb": co.get("wb_order_id") or "",
            "zoro_cost": _f(co.get("zoro_order_total")),
            "zoro_status": co.get("zoro_order_status") or "",
            "profit": _f(co.get("profit_dollars")),
            # closing's formula (closing.compute_net_profit): gross − every customer refund on the
            # order + what Zoro credited back — computed HERE from the refund e-mails, because the
            # closing row only absorbs a refund at the next nightly run and was a day stale on the
            # newest ones (112-8896393-9790656 read +$42.90 on a $293.98 refund).
            "net_after_refund": (round((_f(co.get("profit_dollars")) or 0.0) - (_f(ar.get("total")) or 0.0)
                                       + (_f(co.get("zoro_refund_amount")) or 0.0), 2)
                                 if co.get("profit_dollars") not in (None, "") else None),
            "zoro_recovered": _f(co.get("zoro_refund_amount")),
            "label": co.get("order_label") or "",
            "delivery": co.get("delivery_status") or "",
            "delivered_on": (co.get("delivery_date") or "")[:10],
            "returned": bool(rt),
            "return_requested_at": ((_pt(rt["at"]) or datetime.min).strftime("%Y-%m-%d") if rt else ""),
            "return_reason": (rt or {}).get("reason") or "",
            "return_comment": (rt or {}).get("comment") or "",
            "refunds_on_order": int(ar.get("n") or 1),
            "refunded_on_order": _f(ar.get("total")),
            "case_status": co.get("case_status") or "",
            "case_type": co.get("case_type") or "",
            "zoro_case": co.get("zoro_case_number") or "",
            "flag": co.get("flag_color") or "",
            "ship_to": ", ".join(x for x in (co.get("ship_city"), co.get("ship_state")) if x),
        }
        if store_f and row["store"] != store_f:
            continue
        if res_f and row["resolution"] != res_f:
            continue
        if ret_f in ("yes", "no") and row["returned"] != (ret_f == "yes"):
            continue
        if text and not any(text in str(row.get(k) or "").lower()
                            for k in ("order_id", "asin", "sku", "title", "customer", "wb", "reason",
                                      "return_reason", "resolution")):
            continue
        rows.append(row)

    rows.sort(key=lambda x: x["refunded_at"], reverse=True)

    # day-by-day, newest first; every day in range appears, even a day with none
    by_day = {}
    for x in rows:
        d = by_day.setdefault(x["day"], {"count": 0, "amount": 0.0, "HOANK": [0, 0.0], "Hathoro": [0, 0.0]})
        d["count"] += 1
        d["amount"] += x["amount"] or 0.0
        if x["store"] in ("HOANK", "Hathoro"):
            d[x["store"]][0] += 1
            d[x["store"]][1] += x["amount"] or 0.0
    days, d = [], d_to
    while d >= d_from:
        k = d.isoformat()
        v = by_day.get(k) or {"count": 0, "amount": 0.0, "HOANK": [0, 0.0], "Hathoro": [0, 0.0]}
        days.append({"day": k, "count": v["count"], "amount": round(v["amount"], 2),
                     "hoank": v["HOANK"][0], "hoank_amount": round(v["HOANK"][1], 2),
                     "hathoro": v["Hathoro"][0], "hathoro_amount": round(v["Hathoro"][1], 2)})
        d -= timedelta(days=1)

    def tally(key):
        out = {}
        for x in rows:
            k = x[key] or "—"
            t = out.setdefault(k, {"count": 0, "amount": 0.0})
            t["count"] += 1
            t["amount"] = round(t["amount"] + (x["amount"] or 0.0), 2)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["count"]))

    placed = [x for x in rows if x["wb"]]
    totals = {
        "count": len(rows),
        "orders": len({x["order_id"] for x in rows}),
        "amount": round(sum(x["amount"] or 0.0 for x in rows), 2),
        "no_return": sum(1 for x in rows if not x["returned"]),
        "no_return_amount": round(sum(x["amount"] or 0.0 for x in rows if not x["returned"]), 2),
        "placed": len(placed),
        "zoro_cost_placed": round(sum(x["zoro_cost"] or 0.0 for x in placed), 2),
        # per ORDER, not per refund row: an order refunded twice carries both refunds already
        "net_after_refund": round(sum(v for v in {x["order_id"]: x["net_after_refund"] for x in rows
                                                  if x["net_after_refund"] is not None}.values()), 2),
        "multi_refund_orders": len({x["order_id"] for x in rows if x["refunds_on_order"] > 1}),
        "by_store": tally("store"),
        "by_resolution": tally("resolution"),
    }
    return {"ready": True, "from": d_from.isoformat(), "to": d_to.isoformat(), "today": today.isoformat(),
            "rows": rows, "days": days, "totals": totals,
            "resolutions": list(RESOLUTIONS),
            "last_refund": (_pt(last[0]).strftime("%Y-%m-%d %H:%M") if last and last[0] and _pt(last[0]) else None),
            "last_sync": (str(last[1])[:16].replace("T", " ") if last and last[1] else None)}


CSV_COLS = ["refunded_at", "day", "store", "order_id", "amount", "item_refund", "shipping_refund", "resolution",
            "reason", "returned", "return_requested_at", "return_reason", "customer", "title", "asin", "sku", "qty",
            "sale", "earnings", "purchase_date", "wb", "zoro_cost", "zoro_status", "profit", "net_after_refund",
            "zoro_recovered",
            "label", "delivery", "delivered_on", "refunds_on_order", "refunded_on_order", "case_status",
            "zoro_case", "ship_to", "inbox"]


def auto_refunds_csv(q):
    d = auto_refunds(q)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS)
    for r in d.get("rows") or []:
        w.writerow([("Yes" if r[k] else "No") if k == "returned" else ("" if r.get(k) is None else r.get(k))
                    for k in CSV_COLS])
    return buf.getvalue(), f"auto_refunds_{d.get('from', '')}_to_{d.get('to', '')}.csv"
