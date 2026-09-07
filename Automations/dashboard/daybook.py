"""Daybook + growth analytics for the ZUHRA dashboard (read-only).

daybook(q)      orders grouped by day (purchase / placed / shipped / delivered date) with
                every lifecycle state per order and per-day totals — the "old dashboard"
                view the owner asked for, rebuilt on v_orders_all + archive_messages.
daybook_csv(q)  the same rows as CSV (date column first, sorted by day).
growth(q)       business analytics for the period vs the previous period: trends, profiles,
                coupons, ASIN leaders/losers, refunds, cancellations, shipping, geography,
                repeat buyers, weekday/hour patterns, Zoro account spend, lead times.

Every function takes the parsed query dict `q` (urllib.parse.parse_qs shape) and honours
account / from / to / profile / zoro_account like the rest of server.py.
"""
import csv
import io
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
DB = os.path.join(AUTO, "data", "automations_unified.db")

DELIVERED = ("Delivered", "AllDelivered")
FINAL_LABELS = ("Completed", "Loss", "Refunded", "Cancelled", "NotPlaced")


# ── helpers ──────────────────────────────────────────────────────────────
def _ro():
    c = sqlite3.connect(DB, timeout=8)
    c.execute("PRAGMA busy_timeout=8000")
    c.execute("PRAGMA query_only=ON")
    c.row_factory = sqlite3.Row
    return c


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _q1(q, k, default=""):
    v = q.get(k, [default])
    return (v[0] if v else default) or default


def _d10(v):
    """'YYYY-MM-DD' from any of the date formats in the DB ('2026-09-07', '2026-09-07T18:11:20',
    '2026-04-20 00:22', 'Sun, Sep 6, 2026')."""
    s = str(v or "").strip()
    if not s:
        return ""
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    for fmt in ("%a, %b %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%m/%d/%Y %I:%M %p",
                "%A, %m/%d/%Y %I:%M %p", "%A, %m/%d/%Y", "%a, %m/%d/%Y %I:%M %p"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s[:10]


def _dt(v):
    s = str(v or "").strip().replace("T", " ")
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S.%f",
                "%A, %m/%d/%Y %I:%M %p", "%a, %b %d, %Y", "%m/%d/%Y %I:%M %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _hours(a, b):
    da, db_ = _dt(a), _dt(b)
    if not da or not db_:
        return None
    return round((db_ - da).total_seconds() / 3600.0, 1)


def _period(q, default_days=14):
    """(from, to) as YYYY-MM-DD; defaults to the last `default_days` days."""
    frm, to = _d10(_q1(q, "from")), _d10(_q1(q, "to"))
    if not to:
        to = date.today().isoformat()
    if not frm:
        frm = (date.fromisoformat(to) - timedelta(days=default_days - 1)).isoformat()
    return frm, to


DATECOLS = {"purchase": "purchase_date", "placed": "placed_at", "shipped": "shipped_email_time",
            "delivered": "delivery_date"}


def _where(q, datecol, frm, to):
    where, args = ["1=1"], []
    acct = _q1(q, "account")
    if acct and acct.lower() != "all":
        where.append("account=?"); args.append(acct)
    prof = _q1(q, "profile")
    if prof and prof.lower() != "all":
        where.append("profile_name=?"); args.append(prof)
    za = _q1(q, "zoro_account")
    if za and za.lower() != "all":
        where.append("zoro_account_name=?"); args.append(za)
    label = _q1(q, "label")
    if label and label.lower() != "all":
        where.append("order_label=?"); args.append(label)
    where.append(f"substr(replace({datecol},'T',' '),1,10) >= ?"); args.append(frm)
    where.append(f"substr(replace({datecol},'T',' '),1,10) <= ?"); args.append(to)
    return " AND ".join(where), args


def _messages(c, order_ids):
    """Per-order buyer communication from the Gmail archive:
    tracking_sent · delivery_checkin · oos_notice · cancellation_ack · buyer wrote (inquiry/reply)."""
    out = defaultdict(dict)
    if not order_ids:
        return out
    ids = list(order_ids)
    try:
        for i in range(0, len(ids), 800):
            chunk = ids[i:i + 800]
            ph = ",".join("?" * len(chunk))
            for r in c.execute(
                    f"SELECT amazon_order_id, message_type, MIN(email_timestamp) first_at, COUNT(*) n "
                    f"FROM archive_messages WHERE amazon_order_id IN ({ph}) GROUP BY 1, 2", chunk):
                out[r[0]][r[1]] = {"at": r[2], "n": r[3]}
    except sqlite3.OperationalError:
        pass
    return out


def _returns(c, order_ids):
    """Per-order refund / return facts from the Gmail archive (dated, unlike closing_orders)."""
    out = defaultdict(dict)
    if not order_ids:
        return out
    ids = list(order_ids)
    try:
        for i in range(0, len(ids), 800):
            chunk = ids[i:i + 800]
            ph = ",".join("?" * len(chunk))
            for r in c.execute(
                    f"SELECT amazon_order_id, email_type, MIN(COALESCE(NULLIF(refund_date,''), NULLIF(return_request_date,''), "
                    f"substr(email_timestamp,1,10))) at, MAX(COALESCE(refund_amount,0)) amount, MAX(COALESCE(return_reason,'')) reason "
                    f"FROM archive_returns WHERE amazon_order_id IN ({ph}) GROUP BY 1, 2", chunk):
                out[r[0]][r[1]] = {"at": r[2], "amount": r[3], "reason": r[4]}
    except sqlite3.OperationalError:
        pass
    return out


def _states(o, msgs, rets=None):
    """Derived lifecycle flags for one order (all booleans/strings, cheap to render)."""
    lab = o.get("order_label") or ""
    st = o.get("order_status") or ""
    ds = o.get("delivery_status") or ""
    wb = (o.get("wb_order_id") or o.get("merchant_order_id") or "")
    placed = bool(str(wb).upper().startswith("WB"))
    cancelled = (lab == "Cancelled" or st == "Cancelled")
    shipped = bool((o.get("shipped_email_time") or "").strip()) or st == "Shipped"
    delivered = ds in DELIVERED or (o.get("manual_delivered") == "Yes")
    tracking = bool((o.get("tracking_ids") or "").strip())
    rr = (rets or {}).get(o.get("amazon_order_id"), {})
    refunded = o.get("refund_done") == "Yes" or lab == "Refunded" or bool(rr.get("refund_initiated"))
    returned = o.get("return_requested") == "Yes" or bool(rr.get("return_auth"))
    today = date.today().isoformat()
    dby = _d10(o.get("deliver_by_date"))
    sby = _d10(o.get("ship_by_date"))
    late_ship = (not shipped and not cancelled and sby and sby < today)
    late_deliv = (not delivered and not cancelled and dby and dby < today) or (o.get("on_time") == "Late")
    pending = (lab == "Pending") or (not placed and not cancelled and lab not in FINAL_LABELS)
    m = msgs.get(o.get("amazon_order_id"), {})
    return {
        "placed": placed, "cancelled": cancelled, "shipped": shipped, "delivered": delivered,
        "tracking": tracking, "refunded": refunded, "returned": returned, "pending": pending,
        "late_ship": bool(late_ship), "late_delivery": bool(late_deliv),
        "loss": lab == "Loss" or (_f(o.get("profit_dollars")) < 0 and lab in ("Completed", "Loss")),
        "incomplete": lab == "Incomplete",
        "tracking_msg_sent": bool(m.get("tracking_sent")) or (o.get("customer_messaged") == "Yes"),
        "tracking_msg_at": (m.get("tracking_sent") or {}).get("at"),
        "delivery_checkin": bool(m.get("delivery_checkin")) or (o.get("delivered_msg_sent") == "Yes"),
        "delivery_checkin_at": (m.get("delivery_checkin") or {}).get("at"),
        "oos_notice": bool(m.get("oos_notice")), "cancel_ack": bool(m.get("cancellation_ack")),
        "buyer_wrote": bool(m.get("buyer_inquiry") or m.get("buyer_reply")),
        "buyer_msgs": (m.get("buyer_inquiry") or {}).get("n", 0) + (m.get("buyer_reply") or {}).get("n", 0),
        "has_case": bool(o.get("case_status") or o.get("flag_color") or o.get("tags") or o.get("comments")),
        "refund_at": (rr.get("refund_initiated") or {}).get("at") or _d10(o.get("refund_date")),
        "return_at": (rr.get("return_auth") or {}).get("at"),
        "refund_amount": _f((rr.get("refund_initiated") or {}).get("amount")) or (_f(o.get("refund_amount")) if refunded else 0.0),
        "return_reason": (rr.get("return_auth") or {}).get("reason") or o.get("return_reason") or o.get("refund_reason") or "",
    }


ORDER_COLS = ["amazon_order_id", "account", "profile_name", "order_label", "order_status", "asin", "sku", "title",
              "quantity", "is_business", "earnings", "amazon_fees", "zoro_order_total", "zoro_subtotal", "zoro_shipping",
              "zoro_tax", "coupon_used", "coupon_savings", "profit_dollars", "profit_percent",
              "net_profit_after_refund", "purchase_date", "placed_at", "ship_by_date", "deliver_by_date",
              "shipped_email_time", "delivery_status", "delivery_date", "on_time", "shipped_on_time", "tracking_ids",
              "carrier", "wb_order_id", "merchant_order_id", "zoro_account_name", "ship_name", "ship_city",
              "ship_state", "ship_postal_code", "refund_done", "refund_amount", "refund_date", "refund_reason",
              "return_requested", "return_reason", "customer_messaged", "delivered_msg_sent", "flag_color", "tags",
              "case_status", "case_type", "comments", "reminder_at", "placement_speed_hrs", "time_to_ship_hrs",
              "zoro_ship_speed_hrs", "manual_delivered", "coupon_used"]


def _live_cols(c, table):
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _crm_overlay(c, rows):
    try:
        crm = {x["amazon_order_id"]: dict(x) for x in c.execute("SELECT * FROM order_crm")}
    except sqlite3.OperationalError:
        return
    keys = ("flag_color", "tags", "case_status", "case_type", "comments", "reminder_at", "manual_delivered")
    for o in rows:
        ov = crm.get(o.get("amazon_order_id"))
        if ov:
            for k in keys:
                if ov.get(k) not in (None, ""):
                    o[k] = ov[k]


def _fetch(q, datecol_key, frm, to, extra_where="", extra_args=()):
    datecol = DATECOLS.get(datecol_key, "purchase_date")
    where, args = _where(q, datecol, frm, to)
    if extra_where:
        where += " AND " + extra_where
        args += list(extra_args)
    c = _ro()
    have = _live_cols(c, "v_orders_all")
    cols = [x for x in dict.fromkeys(ORDER_COLS) if x in have]
    rows = [dict(r) for r in c.execute(
        f"SELECT {', '.join(cols)} FROM v_orders_all WHERE {where} ORDER BY {datecol} DESC LIMIT 20000", args)]
    _crm_overlay(c, rows)
    ids = {r["amazon_order_id"] for r in rows}
    msgs = _messages(c, ids)
    rets = _returns(c, ids)
    c.close()
    return rows, msgs, rets, datecol


def _money(rows):
    earn = sum(_f(r.get("earnings")) for r in rows)
    cost = sum(_f(r.get("zoro_order_total")) for r in rows)
    profit = sum(_f(r.get("profit_dollars")) for r in rows)
    coup = sum(_f(r.get("coupon_savings")) for r in rows)
    refunds = sum((r.get("_states") or {}).get("refund_amount", 0) if r.get("_states") else (_f(r.get("refund_amount")) if r.get("refund_done") == "Yes" else 0) for r in rows)
    units = sum(_f(r.get("quantity")) or 1 for r in rows)
    return {"earnings": round(earn, 2), "cost": round(cost, 2), "profit": round(profit, 2),
            "margin_pct": round(profit / cost * 100, 1) if cost else None,
            "coupon_saved": round(coup, 2), "refunds": round(refunds, 2), "units": int(units),
            "avg_profit": round(profit / len(rows), 2) if rows else None}


def _counts(states):
    keys = ("placed", "pending", "shipped", "delivered", "tracking", "late_ship", "late_delivery", "cancelled",
            "refunded", "returned", "loss", "incomplete", "tracking_msg_sent", "delivery_checkin", "buyer_wrote",
            "oos_notice", "has_case")
    out = {k: 0 for k in keys}
    for s in states:
        for k in keys:
            if s.get(k):
                out[k] += 1
    out["orders"] = len(states)
    return out


def freshness():
    """How current each data source is — shown on the page so 'not shipped' is read
    as 'closing has not enriched this yet' when that is the case."""
    c = _ro()
    out = {}
    for key, sql in (("last_placed", "SELECT MAX(placed_at) FROM order_placer_orders"),
                     ("last_closing_enrichment", "SELECT MAX(last_enrichment_run) FROM closing_orders"),
                     ("last_closing_purchase", "SELECT MAX(substr(purchase_date,1,10)) FROM closing_orders"),
                     ("last_buyer_message", "SELECT MAX(email_timestamp) FROM archive_messages"),
                     ("last_return_email", "SELECT MAX(email_timestamp) FROM archive_returns"),
                     ("last_tracking_email", "SELECT MAX(email_timestamp) FROM archive_tracking")):
        try:
            out[key] = c.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            out[key] = None
    c.close()
    # last_enrichment_run is a run id like 'Hathoro-20260830-053730' → keep the timestamp
    import re as _re
    m = _re.search(r"(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})", str(out.get("last_closing_enrichment") or ""))
    if m:
        out["last_closing_enrichment"] = f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}"
    return out


# ── DAYBOOK ──────────────────────────────────────────────────────────────
def daybook(q):
    """Orders grouped by day. q: account, from, to, profile, zoro_account, label,
    datecol=purchase|placed|shipped|delivered (default purchase), days (default 14 when no from)."""
    try:
        default_days = max(1, min(120, int(_q1(q, "days", "14"))))
    except ValueError:
        default_days = 14
    frm, to = _period(q, default_days)
    dk = _q1(q, "datecol", "purchase")
    rows, msgs, rets, datecol = _fetch(q, dk, frm, to)
    by_day = defaultdict(list)
    for o in rows:
        o["_states"] = _states(o, msgs, rets)
        key = _d10(o.get(datecol)) or "unknown"
        by_day[key].append(o)
    days = []
    for d in sorted(by_day, reverse=True):
        os_ = by_day[d]
        sts = [o["_states"] for o in os_]
        orders = []
        for o in os_:
            s = o.pop("_states")
            orders.append({
                "id": o.get("amazon_order_id"), "account": o.get("account"), "profile": o.get("profile_name"),
                "label": o.get("order_label"), "status": o.get("order_status"), "asin": o.get("asin"),
                "sku": o.get("sku"), "title": (o.get("title") or "")[:90], "qty": o.get("quantity"),
                "business": o.get("is_business") == "Yes",
                "wb": o.get("wb_order_id") or "", "wb_all": o.get("merchant_order_id") or "",
                "earnings": _f(o.get("earnings")), "cost": _f(o.get("zoro_order_total")),
                "profit": _f(o.get("profit_dollars")), "margin": o.get("profit_percent"),
                "coupon": o.get("coupon_used") or "", "coupon_saved": _f(o.get("coupon_savings")),
                "purchase_date": _d10(o.get("purchase_date")), "placed_at": o.get("placed_at") or "",
                "ship_by": _d10(o.get("ship_by_date")), "deliver_by": _d10(o.get("deliver_by_date")),
                "shipped_at": o.get("shipped_email_time") or "", "delivered_at": o.get("delivery_date") or "",
                "delivery_status": o.get("delivery_status") or "", "on_time": o.get("on_time") or "",
                "shipped_on_time": o.get("shipped_on_time") or "",
                "tracking": (o.get("tracking_ids") or "")[:40], "carrier": o.get("carrier") or "",
                "zoro_account": o.get("zoro_account_name") or "",
                "state": o.get("ship_state") or "", "city": o.get("ship_city") or "",
                "refund_amount": s["refund_amount"], "refund_at": s["refund_at"] or "", "return_at": s["return_at"] or "",
                "return_reason": s["return_reason"],
                "flag": o.get("flag_color") or "", "tags": o.get("tags") or "",
                "case_status": o.get("case_status") or "", "case_type": o.get("case_type") or "",
                "comments": (o.get("comments") or "")[:160], "reminder_at": o.get("reminder_at") or "",
                "placement_hrs": o.get("placement_speed_hrs"), "ship_hrs": o.get("time_to_ship_hrs"),
                "s": s,
            })
        days.append({"date": d, "counts": _counts(sts), "money": _money(os_), "orders": orders})
    all_states = [o["s"] for d in days for o in d["orders"]]
    return {"from": frm, "to": to, "datecol": dk, "days": days,
            "totals": {"counts": _counts(all_states), "money": _money(rows)}, "count": len(rows),
            "freshness": freshness()}


CSV_COLS = [("date", None), ("amazon_order_id", "id"), ("account", "account"), ("profile", "profile"),
            ("wb", "wb"), ("all_wb", "wb_all"), ("asin", "asin"), ("sku", "sku"), ("title", "title"), ("qty", "qty"),
            ("business", "business"), ("label", "label"), ("amazon_status", "status"),
            ("placed", "s.placed"), ("cancelled", "s.cancelled"), ("shipped", "s.shipped"), ("delivered", "s.delivered"),
            ("pending", "s.pending"), ("late_ship", "s.late_ship"), ("late_delivery", "s.late_delivery"),
            ("tracking_msg_sent", "s.tracking_msg_sent"), ("tracking_msg_at", "s.tracking_msg_at"),
            ("delivery_checkin_sent", "s.delivery_checkin"), ("buyer_wrote", "s.buyer_wrote"),
            ("oos_notice_sent", "s.oos_notice"), ("cancel_ack_sent", "s.cancel_ack"),
            ("refunded", "s.refunded"), ("refund_amount", "refund_amount"), ("return_requested", "s.returned"),
            ("return_reason", "return_reason"), ("purchase_date", "purchase_date"), ("placed_at", "placed_at"),
            ("ship_by", "ship_by"), ("shipped_at", "shipped_at"), ("deliver_by", "deliver_by"),
            ("delivered_at", "delivered_at"), ("delivery_status", "delivery_status"), ("on_time", "on_time"),
            ("shipped_on_time", "shipped_on_time"), ("tracking", "tracking"), ("carrier", "carrier"),
            ("earnings", "earnings"), ("zoro_cost", "cost"), ("coupon", "coupon"), ("coupon_saved", "coupon_saved"),
            ("profit", "profit"), ("margin_pct", "margin"), ("zoro_account", "zoro_account"),
            ("ship_state", "state"), ("ship_city", "city"), ("flag", "flag"), ("tags", "tags"),
            ("case_status", "case_status"), ("case_type", "case_type"), ("comments", "comments")]


def daybook_csv(q):
    d = daybook(q)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c for c, _ in CSV_COLS])
    for day in d["days"]:
        for o in day["orders"]:
            row = []
            for name, key in CSV_COLS:
                if key is None:
                    row.append(day["date"]); continue
                if key.startswith("s."):
                    v = o["s"].get(key[2:])
                else:
                    v = o.get(key)
                if isinstance(v, bool):
                    v = "yes" if v else ""
                row.append("" if v is None else v)
            w.writerow(row)
    return buf.getvalue(), f"daybook_{d['from']}_to_{d['to']}.csv"


# ── GROWTH ANALYTICS ─────────────────────────────────────────────────────
def _agg(rows_states):
    rows = [r for r, _ in rows_states]
    m = _money(rows)
    cnt = _counts([s for _, s in rows_states])
    n = len(rows) or 1
    m.update({"orders": len(rows),
              "cancel_rate": round(cnt["cancelled"] / n * 100, 1),
              "refund_rate": round(cnt["refunded"] / n * 100, 1),
              "return_rate": round(cnt["returned"] / n * 100, 1),
              "late_rate": round(cnt["late_delivery"] / n * 100, 1),
              "loss_orders": cnt["loss"], "cancelled": cnt["cancelled"], "refunded": cnt["refunded"],
              "returned": cnt["returned"], "late": cnt["late_delivery"]})
    return m


def _delta(cur, prev):
    out = {}
    for k, v in cur.items():
        if isinstance(v, (int, float)) and v is not None and isinstance(prev.get(k), (int, float)):
            p = prev.get(k)
            out[k] = {"now": v, "prev": p, "diff": round(v - p, 2),
                      "pct": round((v - p) / abs(p) * 100, 1) if p else None}
    return out


def _bucket_series(rows_states, keyfn):
    by = defaultdict(list)
    for r, s in rows_states:
        k = keyfn(r)
        if k:
            by[k].append((r, s))
    return [dict(key=k, **_agg(by[k])) for k in sorted(by)]


def growth(q):
    """Period analytics with a like-for-like comparison to the previous period."""
    frm, to = _period(q, 30)
    span = (date.fromisoformat(to) - date.fromisoformat(frm)).days + 1
    pfrm = (date.fromisoformat(frm) - timedelta(days=span)).isoformat()
    pto = (date.fromisoformat(frm) - timedelta(days=1)).isoformat()
    rows, msgs, rets, _ = _fetch(q, "purchase", frm, to)
    prow, pmsgs, prets, _ = _fetch(q, "purchase", pfrm, pto)
    cur = [(r, _states(r, msgs, rets)) for r in rows]
    prev = [(r, _states(r, pmsgs, prets)) for r in prow]
    summary = _agg(cur); psummary = _agg(prev)

    # ── time series ──
    daily = _bucket_series(cur, lambda r: _d10(r.get("purchase_date")))
    weekly = _bucket_series(cur + prev, lambda r: (_dt(_d10(r.get("purchase_date")) or "") or datetime(2000, 1, 1)).strftime("%G-W%V"))
    weekday = defaultdict(list)
    hours = defaultdict(int)
    for r, s in cur:
        d = _dt(_d10(r.get("purchase_date")))
        if d:
            weekday[d.strftime("%a")].append((r, s))
        pd_ = _dt(r.get("placed_at"))
        if pd_:
            hours[pd_.hour] += 1
    wd_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekdays = [dict(key=w, **_agg(weekday.get(w, []))) for w in wd_order]
    hour_hist = [{"hour": h, "placed": hours.get(h, 0)} for h in range(24)]

    # ── breakdowns ──
    def by_field(field, label=None, top=None, minimum=1):
        by = defaultdict(list)
        for r, s in cur:
            by[(r.get(field) or "—")].append((r, s))
        out = [dict(key=k, **_agg(v)) for k, v in by.items() if len(v) >= minimum]
        out.sort(key=lambda x: -x["orders"])
        return out[:top] if top else out
    profiles = by_field("profile_name")
    accounts = by_field("account")
    zoro_accounts = by_field("zoro_account_name")
    states = by_field("ship_state", top=20)
    carriers = by_field("carrier")

    # ── coupons ──
    cby = defaultdict(list)
    for r, s in cur:
        code = (r.get("coupon_used") or "").strip()
        if code and code.upper() not in ("NONE", "N/A"):
            cby[code].append((r, s))
    coupons = []
    for code, v in cby.items():
        rr = [r for r, _ in v]
        sav = sum(_f(r.get("coupon_savings")) for r in rr)
        subs = [(_f(r.get("zoro_subtotal")) or 0) + (_f(r.get("coupon_savings")) or 0) for r in rr]
        pct = round(sav / sum(subs) * 100, 1) if sum(subs) else None
        dates = sorted(_d10(r.get("purchase_date")) for r in rr)
        coupons.append({"code": code, "uses": len(rr), "saved": round(sav, 2), "avg_saved": round(sav / len(rr), 2),
                        "avg_pct": pct, "first": dates[0], "last": dates[-1],
                        "kind": "priority (multi-use)" if len(rr) >= 3 else "regular (single-use)",
                        "profit": round(sum(_f(r.get("profit_dollars")) for r in rr), 2)})
    coupons.sort(key=lambda x: -x["saved"])
    coupon_orders = sum(c["uses"] for c in coupons)
    coupon_total = {"orders_with_coupon": coupon_orders, "saved": round(sum(c["saved"] for c in coupons), 2),
                    "share_pct": round(coupon_orders / (len(cur) or 1) * 100, 1),
                    "priority_saved": round(sum(c["saved"] for c in coupons if c["kind"].startswith("priority")), 2),
                    "regular_saved": round(sum(c["saved"] for c in coupons if c["kind"].startswith("regular")), 2)}

    # ── ASINs ──
    aby = defaultdict(list)
    pby = defaultdict(list)
    for r, s in cur:
        aby[r.get("asin") or "—"].append((r, s))
    for r, s in prev:
        pby[r.get("asin") or "—"].append((r, s))
    asins = []
    for a, v in aby.items():
        g = _agg(v)
        g.update({"asin": a, "sku": v[0][0].get("sku"), "title": (v[0][0].get("title") or "")[:70],
                  "prev_orders": len(pby.get(a, [])), "new": a not in pby})
        asins.append(g)
    top_profit = sorted(asins, key=lambda x: -x["profit"])[:25]
    losers = sorted([x for x in asins if x["profit"] < 0 or x["loss_orders"]], key=lambda x: x["profit"])[:25]
    top_volume = sorted(asins, key=lambda x: -x["orders"])[:25]
    trouble = sorted([x for x in asins if x["orders"] >= 3 and (x["cancel_rate"] >= 20 or x["refund_rate"] >= 15 or x["late_rate"] >= 25)],
                     key=lambda x: -(x["cancel_rate"] + x["refund_rate"] + x["late_rate"]))[:25]
    new_asins = sorted([x for x in asins if x["new"]], key=lambda x: -x["orders"])[:25]
    growing = sorted([x for x in asins if x["prev_orders"] and x["orders"] >= x["prev_orders"] * 1.5 and x["orders"] >= 3],
                     key=lambda x: -(x["orders"] - x["prev_orders"]))[:15]
    fading = sorted([x for x in asins if x["prev_orders"] >= 3 and x["orders"] <= x["prev_orders"] * 0.5],
                    key=lambda x: -(x["prev_orders"] - x["orders"]))[:15]

    # ── refunds / returns / cancellations ──
    reasons = defaultdict(lambda: {"n": 0, "amount": 0.0})
    for r, s in cur:
        if s["refunded"] or s["returned"]:
            k = (s.get("return_reason") or "unspecified").strip()
            reasons[k]["n"] += 1
            reasons[k]["amount"] += s.get("refund_amount") or 0
    reason_rows = sorted([dict(reason=k, **v) for k, v in reasons.items()], key=lambda x: -x["n"])[:15]
    refund_asins = sorted([x for x in asins if x["refunded"] or x["returned"]], key=lambda x: -(x["refunded"] + x["returned"]))[:15]
    cancel_asins = sorted([x for x in asins if x["cancelled"]], key=lambda x: -x["cancelled"])[:15]
    net_after_refund = round(sum(_f(r.get("net_profit_after_refund")) if r.get("refund_done") == "Yes" else _f(r.get("profit_dollars")) for r, _ in cur), 2)

    # ── shipping & lead times ──
    def avg(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 1) if xs else None
    ship = {
        "on_time_pct": round(sum(1 for r, _ in cur if r.get("on_time") == "On Time") / max(1, sum(1 for r, _ in cur if r.get("on_time") in ("On Time", "Late"))) * 100, 1),
        "shipped_early": sum(1 for r, _ in cur if r.get("shipped_on_time") == "Early"),
        "shipped_on_time": sum(1 for r, _ in cur if r.get("shipped_on_time") == "On Time"),
        "shipped_late": sum(1 for r, _ in cur if r.get("shipped_on_time") == "Late"),
        "avg_placement_hrs": avg([_f(r.get("placement_speed_hrs")) if r.get("placement_speed_hrs") not in (None, "") else None for r, _ in cur]),
        "avg_time_to_ship_hrs": avg([_f(r.get("time_to_ship_hrs")) if r.get("time_to_ship_hrs") not in (None, "") else None for r, _ in cur]),
        "avg_zoro_ship_hrs": avg([_f(r.get("zoro_ship_speed_hrs")) if r.get("zoro_ship_speed_hrs") not in (None, "") else None for r, _ in cur]),
        "avg_purchase_to_delivered_days": avg([(_hours(_d10(r.get("purchase_date")), _d10(r.get("delivery_date"))) or 0) / 24 if r.get("delivery_date") else None for r, _ in cur]),
        "delivered_pct": round(sum(1 for _, s in cur if s["delivered"]) / (len(cur) or 1) * 100, 1),
        "tracking_msg_pct": round(sum(1 for _, s in cur if s["tracking_msg_sent"]) / max(1, sum(1 for _, s in cur if s["shipped"])) * 100, 1),
        "carriers": carriers,
        "weekly": [{"key": w["key"], "orders": w["orders"], "late": w["late"], "late_rate": w["late_rate"]} for w in weekly],
    }

    # ── buyers & geography ──
    bby = defaultdict(list)
    for r, s in cur + prev:
        k = ((r.get("ship_name") or "").strip().lower(), (r.get("ship_postal_code") or "")[:5])
        if k[0]:
            bby[k].append((r, s))
    repeat = [dict(name=k[0].title(), zip=k[1], **_agg(v)) for k, v in bby.items() if len(v) >= 2]
    repeat.sort(key=lambda x: -x["orders"])
    business = sum(1 for r, _ in cur if r.get("is_business") == "Yes")

    # ── margin distribution ──
    buckets = [("<0%", -1e9, 0), ("0–5%", 0, 5), ("5–10%", 5, 10), ("10–20%", 10, 20), ("20–35%", 20, 35), ("35%+", 35, 1e9)]
    dist = []
    for name, lo, hi in buckets:
        v = [(r, s) for r, s in cur if r.get("profit_percent") not in (None, "") and lo <= _f(r.get("profit_percent")) < hi]
        dist.append({"bucket": name, "orders": len(v), "profit": round(sum(_f(r.get("profit_dollars")) for r, _ in v), 2)})

    return {
        "from": frm, "to": to, "prev_from": pfrm, "prev_to": pto, "days": span,
        "summary": summary, "prev_summary": psummary, "compare": _delta(summary, psummary),
        "daily": daily, "weekly": weekly, "weekdays": weekdays, "hours": hour_hist,
        "profiles": profiles, "accounts": accounts, "zoro_accounts": zoro_accounts,
        "coupons": coupons[:60], "coupon_total": coupon_total,
        "asins": {"top_profit": top_profit, "losers": losers, "top_volume": top_volume, "trouble": trouble,
                  "new": new_asins, "growing": growing, "fading": fading, "count": len(asins),
                  "prev_count": len(pby)},
        "refunds": {"reasons": reason_rows, "asins": refund_asins, "net_after_refund": net_after_refund,
                    "cancel_asins": cancel_asins},
        "shipping": ship,
        "buyers": {"repeat": repeat[:40], "repeat_count": len(repeat), "business_orders": business,
                   "business_pct": round(business / (len(cur) or 1) * 100, 1)},
        "states": states, "margin_dist": dist, "freshness": freshness(),
    }


if __name__ == "__main__":
    import json, sys
    fn = sys.argv[1] if len(sys.argv) > 1 else "daybook"
    q = {"days": ["3"]} if fn == "daybook" else {}
    out = {"daybook": daybook, "growth": growth}[fn](q)
    print(json.dumps(out, default=str)[:4000])
