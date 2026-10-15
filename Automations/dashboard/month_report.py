"""Daily orders report — the page the dashboard opens on (27 Sep 2026).

One month, one row per day, counted the way Seller Central counts: from Amazon's own order
report (amazon_report_orders, filled by scripts/sync_amazon_report.py — every order, placed or
not), joined with what the stack knows about each order: closing's label and money
(closing_orders), orders placed but not closed yet (order_placer_orders), and delivery from
Zoro's tracking (the closing_orders delivery columns scripts/zoro_delivery_check.py writes).

month_report(q)  ?month=YYYY-MM&account=   per-day counts, month totals, report coverage
month_day(q)     ?date=YYYY-MM-DD&account=  every order of one day with label, reason, delivery
month_csv(q)     ?month=YYYY-MM&account=   the whole month as CSV, one row per order
All three also take &profile= (DM_COUPON … or "none" = placed by hand) and &gmail= (the Zoro inbox
the order was bought with): only the orders placed that way are counted (12 Oct 2026).

Every order lands in exactly one bucket of each:
  label     Completed · Loss · Incomplete · Refunded · Cancelled · Not placed (· Manual)
  delivery  delivered · transit · stalled (no movement) · exception · freight · zoro_cancelled ·
            not_shipped · na (cancelled / never placed)
  late      overlay: not delivered and the Amazon deliver-by passed ≥ late_days ago
A day with no ingested Amazon report ("covered": false) shows only what the DB knows — the
orders placed on Zoro — and the page says so.
"""
import csv
import io
import os
import re
import sqlite3
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
DB = os.environ.get("CLOUD_DB_PATH") if (os.environ.get("CLOUD_MODE") == "1" and os.environ.get("CLOUD_DB_PATH")) \
    else os.path.join(AUTO, "data", "automations_unified.db")

DELIVERED = ("Delivered", "AllDelivered")
LABELS = ("Completed", "Loss", "Incomplete", "Refunded", "Cancelled", "Not placed", "Manual")
DSTATES = ("delivered", "transit", "stalled", "exception", "freight", "zoro_cancelled", "not_shipped", "na")
WHY = {   # Incomplete reasons, in the order the page lists them
    "stalled": "No movement", "late": "Late — still in transit", "exception": "Delivery exception",
    "zoro_cancelled": "Zoro cancelled", "freight": "Freight — confirm by hand", "late_ship": "Not shipped, ship-by passed",
    "not_shipped": "Not shipped yet", "transit": "In transit, on time", "partial": "Partly delivered",
    "no_earnings": "Delivered — earnings missing", "no_cost": "Delivered — Zoro cost missing",
    "no_tracking": "Delivered — tracking missing", "stale": "Delivered — label not refreshed",
    "not_closed": "Placed — not in closing yet", "unchecked": "Not checked yet",
}
_CODES = (("C-WB", "Zoro order cancelled"), ("OOS", "Out of stock"), ("SR", "State restricted"),
          ("LOW_PROFIT", "Low profit"), ("LOSS", "Low profit"), ("DISCONTINUED", "Discontinued"),
          ("POBOX", "PO box"), ("PO_BOX", "PO box"), ("BB", "PO box (BB)"), ("UNMAPPED", "ASIN not mapped"), ("DUPLICATE", "Duplicate"),
          ("NET30", "Net-30"), ("UNPLACED", "Unplaced"), ("CANCELLED", "Cancelled"),
          ("DELIVERY TIME HIGH", "Zoro delivery too late for Amazon's date"))
_WB_RX = re.compile(r"WB\d{6,}")
_TZ_RX = re.compile(r"\s+(?:PST|PDT|EST|EDT|CST|CDT|MST|MDT|UTC|GMT)\b.*$")
_TIME_RX = re.compile(r",?\s+(?:at\s+)?\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp][Mm])?$")

_C_COLS = ("amazon_order_id", "account", "purchase_date", "order_status", "order_label", "merchant_order_id",
           "wb_order_id", "sku", "asin", "title", "quantity", "item_price", "earnings", "zoro_order_total",
           "profit_dollars", "delivery_status", "delivery_date", "deliver_by_date", "ship_by_date", "tracking_ids",
           "carrier", "shipped_email_time", "delivery_note", "last_scan_time", "last_scan_location", "stalled_days",
           "last_movement_at", "zoro_order_status", "refund_done", "refund_amount", "return_requested",
           "delivered_msg_sent", "review_requested", "delay_notice_sent", "customer_messaged", "ship_city",
           "ship_state", "delivery_checked_at", "profile_name", "zoro_gmail_account", "flag_color")
_P_COLS = ("amazon_order_id", "account", "purchase_date", "placed_at", "wb_order_id", "sku", "asin", "title",
           "quantity", "earnings", "zoro_order_total", "profit_dollars", "tracking_ids", "shipped_email_time",
           "deliver_by_date", "ship_by_date", "ship_city", "ship_state", "profile_name")


def _ro():
    # Plain tuples: dict(sqlite3.Row) cost 0.33 s per 15k rows, zip() 0.07 s (measured 28 Sep 2026).
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA query_only=ON")
    return c


def _rows(c, sql, args=()):
    cur = c.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _today():
    """Amazon's today. Days in this report are Seller Central's (US/Pacific); this Mac runs on
    Pakistan time and Render on UTC, so date.today() would open a day Amazon has not started."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).date()
    except Exception:
        return (datetime.utcnow() - timedelta(hours=7)).date()


def _q1(q, k, default=""):
    v = q.get(k, [default])
    return (v[0] if v else default) or default


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _day(v):
    """'YYYY-MM-DD' from any date format the DB holds ('2026-09-07 18:11', 'Sat, Sep 26, 2026',
    'Sat, May 23, 2026 PDT', 'Wednesday, 6/03/2026 3:07 pm', '8/25/2026 10:45 am', and the
    placer's deliver-by 'Thu, Sep 24, 2026| ZoroDeliverBy:2026-09-22' — Amazon's date first)."""
    s = str(v or "").strip()
    if not s:
        return ""
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return _day_slow(s)


@lru_cache(maxsize=8192)
def _day_slow(s):
    s = s.split("|", 1)[0].strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    s = _TIME_RX.sub("", _TZ_RX.sub("", s)).strip()
    for fmt in ("%a, %b %d, %Y", "%b %d, %Y", "%A, %m/%d/%Y", "%a, %m/%d/%Y", "%m/%d/%Y", "%A, %B %d, %Y",
                "%B %d, %Y", "%a %b %d %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _has_table(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)).fetchone())


def _cols(c, table, wanted):
    have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    return [x for x in wanted if x in have]


def _by_ids(c, table, cols, ids, extra=""):
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in _rows(c, f"SELECT {', '.join(cols)} FROM {table} WHERE amazon_order_id IN "
                          f"({','.join('?' * len(part))}){extra}", part):
            out[r["amazon_order_id"]] = r
    return out


def _thresholds():
    try:
        from utils.accounts_config import zoro_tracking_cfg
        cfg = zoro_tracking_cfg()
        return int(cfg.get("late_days", 2)), int(cfg.get("stalled_days", 3))
    except Exception:
        return 2, 3


@lru_cache(maxsize=1)
def _inbox_map():
    """{placer profile: Zoro inbox it buys with} — for orders placed but not closed yet, whose own
    row has no inbox. Closing's zoro_gmail_account wins wherever it exists: DM_COUPON bought with
    salesmercentile in September and robstark771 since."""
    try:
        from utils.accounts_config import zoro_profile_inboxes
        return dict(zoro_profile_inboxes())
    except Exception:
        return {}


def _code_reason(mid):
    u = str(mid or "").strip().upper()
    if not u:
        return ""
    for code, text in _CODES:
        if u.startswith(code):
            return text
    return str(mid).strip()[:40]


# ── one order ─────────────────────────────────────────────────────────────
def _order(a, c, p, today, late_days, stalled_min):
    """Merge the Amazon report row (a), the closing row (c) and the placer row (p) of one order."""
    a, c, p = a or {}, c or {}, p or {}
    src = c or p
    oid = a.get("amazon_order_id") or src.get("amazon_order_id")
    amz = a.get("order_status") or c.get("order_status") or ""
    mid = a.get("merchant_order_id") or c.get("merchant_order_id") or ""
    m = _WB_RX.search(str(c.get("wb_order_id") or p.get("wb_order_id") or mid).upper())
    wb = m.group(0) if m else ""
    placed = bool(wb) or bool(c) or bool(p)
    ds = c.get("delivery_status") or ""
    delivered = ds in DELIVERED or c.get("manual_delivered") == "Yes"
    note = str(c.get("delivery_note") or "")
    tracking = str(c.get("tracking_ids") or p.get("tracking_ids") or "")
    shipped = bool(tracking.strip()) or bool(c.get("shipped_email_time") or p.get("shipped_email_time")) or amz == "Shipped"
    try:
        stalled = int(c.get("stalled_days")) if c.get("stalled_days") not in (None, "") else None
    except (TypeError, ValueError):
        stalled = None

    # label ── closing's, corrected by Amazon's fresher status; the rest for orders closing lacks
    label = c.get("order_label") or ""
    if label == "NotPlaced":
        label = "Not placed"
    if amz == "Cancelled" and not delivered and label not in ("Refunded", "Completed", "Loss", "Manual"):
        label = "Cancelled"
    if not label:
        label = "Cancelled" if amz == "Cancelled" else ("Incomplete" if placed else "Not placed")
    if label not in LABELS:
        label = "Incomplete"

    # delivery state
    if label in ("Cancelled", "Not placed") and not delivered:
        dstate = "na"
    elif delivered:
        dstate = "delivered"
    elif str(c.get("zoro_order_status") or "").lower() == "cancelled":
        dstate = "zoro_cancelled"
    elif note.startswith("Freight carrier"):
        dstate = "freight"
    elif ds == "DeliveryException":
        dstate = "exception"
    elif shipped and stalled is not None and stalled >= stalled_min:
        dstate = "stalled"
    elif shipped:
        dstate = "transit"
    else:
        dstate = "not_shipped"
    dby = _day(c.get("deliver_by_date") or p.get("deliver_by_date"))
    late = 0
    if dby and dstate not in ("na", "delivered", "zoro_cancelled"):
        try:
            late = (today - date.fromisoformat(dby)).days
        except ValueError:
            late = 0
        late = late if late >= late_days else 0

    # why ── one plain sentence per order, and a key for the Incomplete breakdown
    earn, cost = _f(c.get("earnings") or p.get("earnings")), _f(c.get("zoro_order_total") or p.get("zoro_order_total"))
    profit = c.get("profit_dollars") if c.get("profit_dollars") not in (None, "") else p.get("profit_dollars")
    delivered_day = _day(c.get("delivery_date"))
    wkey, why = "", ""
    if label == "Completed":
        why = f"Delivered {delivered_day}" if delivered_day else "Delivered"
    elif label == "Loss":
        why = f"Delivered at a loss (${_f(profit):,.2f})"
    elif label == "Refunded":
        why = "Refunded" + (f" ${_f(c.get('refund_amount')):,.2f}" if _f(c.get("refund_amount")) else "")
    elif label == "Cancelled":
        why = _code_reason(mid) or "Cancelled on Amazon"
        if why == "Cancelled":
            why = "Cancelled on Amazon"
    elif label == "Not placed":
        why = _code_reason(mid) or ("Waiting to be placed" if amz in ("Pending", "Unshipped") else "Not placed on Zoro")
    elif label == "Manual":
        why = "Handled by hand"
    else:
        sby = _day(c.get("ship_by_date") or p.get("ship_by_date"))
        if not c:
            wkey = "not_closed"
        elif dstate == "zoro_cancelled":
            wkey = "zoro_cancelled"
        elif dstate == "not_shipped":
            wkey = "late_ship" if (sby and sby < today.isoformat()) else "not_shipped"
        elif dstate in ("freight", "exception", "stalled"):
            wkey = dstate
        elif dstate == "transit":
            wkey = "late" if late else ("partial" if ds == "PartialDelivered" else
                                        ("transit" if c.get("delivery_checked_at") or ds else "unchecked"))
        elif dstate == "delivered":
            wkey = ("no_earnings" if not earn else "no_cost" if not cost
                    else "no_tracking" if not tracking.strip() else "stale")
        else:
            wkey = "unchecked"
        why = WHY.get(wkey, "")
        if wkey == "stalled" and stalled is not None:
            why = f"No movement for {stalled} days"
        elif wkey == "late":
            why = f"Late {late} days — still in transit"
        elif wkey == "exception" and note:
            why = "Exception: " + note[:80]
        elif wkey == "freight" and note:
            why = note.replace("Freight carrier — not trackable via Zoro: ", "Freight: ")[:80]
        elif wkey == "transit" and dby:
            why = f"In transit, due {dby}"
        elif wkey == "not_closed" and p.get("placed_at"):
            why = f"Placed {str(p.get('placed_at'))[:16]} — not in closing yet"
    pdate = a.get("purchase_date") or str(src.get("purchase_date") or "")
    prof = str(c.get("profile_name") or p.get("profile_name") or "").strip().upper() if placed else ""
    gmail = str(c.get("zoro_gmail_account") or "").strip() if placed else ""
    if placed and not gmail and prof:
        gmail = _inbox_map().get(prof, "")
    return {
        "id": oid, "acct": a.get("account") or src.get("account") or "",
        "prof": prof, "gmail": gmail,
        "day": a.get("purchase_day") or _day(src.get("purchase_date")),
        "t": pdate[11:16] if len(pdate) >= 16 and pdate[4] == "-" else "",
        "amz": amz or ("—" if not a else ""), "mid": mid, "wb": wb, "placed": placed,
        "sku": a.get("skus") or c.get("sku") or p.get("sku") or "",
        "asin": a.get("asins") or c.get("asin") or p.get("asin") or "",
        "title": (a.get("title") or c.get("title") or p.get("title") or "")[:90],
        "units": int(_f(a.get("units")) or _f(c.get("quantity")) or _f(p.get("quantity")) or 0),
        "sales": round(_f(a.get("sales")) if a else _f(c.get("item_price")), 2),
        "label": label, "why": why, "wkey": wkey, "dstate": dstate, "late": late,
        "stalled": stalled if dstate == "stalled" else None,
        "deliver_by": dby, "delivered": delivered_day, "dstatus": ds,
        "tracking": tracking[:60], "carrier": c.get("carrier") or "",
        "last_scan": " · ".join(x for x in (str(c.get("last_scan_time") or "")[:16], str(c.get("last_scan_location") or "")) if x),
        "earn": round(earn, 2), "cost": round(cost, 2),
        "profit": round(_f(profit), 2) if profit not in (None, "") else None,
        "city": a.get("ship_city") or c.get("ship_city") or p.get("ship_city") or "",
        "state": a.get("ship_state") or c.get("ship_state") or p.get("ship_state") or "",
        "checkin": c.get("delivered_msg_sent") == "Yes",
        "review": c.get("review_requested") in ("Yes", "Already Requested"),
        "delay": c.get("delay_notice_sent") == "Yes",
        "in_amazon": bool(a), "in_system": bool(c or p), "in_closing": bool(c),
        "flag": c.get("flag_color") or "", "tags": "", "urgent": False,     # CRM overlay in _collect
    }


# ── collect a date range ──────────────────────────────────────────────────
def _coverage(c):
    """{account: [(from, to, file, ingested_at)]} of the Amazon reports ingested so far."""
    out = {}
    if not _has_table(c, "amazon_report_files"):
        return out
    for acct, f, t, name, at in c.execute("SELECT account, span_from, span_to, report_file, ingested_at "
                                          "FROM amazon_report_files WHERE COALESCE(span_from,'')!='' "
                                          "ORDER BY span_to DESC"):
        out.setdefault(acct, []).append((f, t, name, at))
    return out


def _covered(cov, day, accts):
    hits = [any(f <= day <= t for f, t, _, _ in cov.get(a, [])) for a in accts]
    return True if all(hits) else ("partial" if any(hits) else False)


def _accounts(acct):
    try:
        from utils.accounts_config import accounts
        allx = list(accounts())
    except Exception:
        allx = ["HOANK", "Hathoro"]
    return allx if (not acct or acct.lower() in ("all", "both")) else [acct]


def _collect(c, d0, d1, acct):
    """Every order whose (Amazon) purchase day is in [d0, d1], merged across the three sources."""
    today = _today()
    late_days, stalled_min = _thresholds()
    af = " AND account=?" if acct else ""
    aargs = [acct] if acct else []
    hi = (date.fromisoformat(d1) + timedelta(days=1)).isoformat()
    # 1. Amazon's own list for the range (the count Seller Central shows)
    A = {}
    if _has_table(c, "amazon_report_orders"):
        A = {r["amazon_order_id"]: r for r in _rows(
            c, f"SELECT * FROM amazon_report_orders WHERE purchase_day BETWEEN ? AND ?{af}", [d0, d1] + aargs)}
    # 2. closing rows dated into the range, plus the closing rows of Amazon's orders dated elsewhere
    ccols = _cols(c, "closing_orders", _C_COLS)
    C = {r["amazon_order_id"]: r for r in _rows(
        c, f"SELECT {', '.join(ccols)} FROM closing_orders WHERE purchase_date >= ? AND purchase_date < ?{af}",
        [d0, hi] + aargs)}
    C.update(_by_ids(c, "closing_orders", ccols, [i for i in A if i not in C]))
    # 3. placed on Zoro but not in closing yet (the anti-join keeps closed orders out of the fetch)
    P = {}
    if _has_table(c, "order_placer_orders"):
        pcols = _cols(c, "order_placer_orders", _P_COLS)
        not_closed = " AND amazon_order_id NOT IN (SELECT amazon_order_id FROM closing_orders)"
        plo = (date.fromisoformat(d0) - timedelta(days=3)).isoformat()
        phi = (date.fromisoformat(d1) + timedelta(days=6)).isoformat()
        P = {r["amazon_order_id"]: r for r in _rows(
            c, f"SELECT {', '.join(pcols)} FROM order_placer_orders WHERE placed_at >= ? AND placed_at < ?{af}{not_closed}",
            [plo, phi] + aargs)}
        P.update(_by_ids(c, "order_placer_orders", pcols, [i for i in A if i not in C and i not in P]))
        # a closed order whose closing row has no profile: the placer's own row knows it
        blank = [i for i, r in C.items() if not (r.get("profile_name") or "").strip()]
        if blank and "profile_name" in pcols:
            for i, r in _by_ids(c, "order_placer_orders", ["amazon_order_id", "profile_name"], blank).items():
                if r.get("profile_name"):
                    C[i]["profile_name"] = r["profile_name"]
    try:   # the CRM row: hand-marked deliveries count as delivered, as everywhere else on the
        # dashboard; flag / tags / urgent ride along so a row shows what the drawer set on it
        cc = _cols(c, "order_crm", ("amazon_order_id", "manual_delivered", "flag_color", "tags", "is_urgent")) \
            if _has_table(c, "order_crm") else []
        conds = [w for k, w in (("manual_delivered", "manual_delivered='Yes'"),
                                ("flag_color", "COALESCE(flag_color,'')!=''"),
                                ("tags", "COALESCE(tags,'')!=''"), ("is_urgent", "is_urgent='Yes'")) if k in cc]
        crm = {r["amazon_order_id"]: r for r in _rows(
            c, f"SELECT {', '.join(cc)} FROM order_crm WHERE {' OR '.join(conds)}")} \
            if conds and "amazon_order_id" in cc else {}
    except sqlite3.Error:
        crm = {}
    for i, r in crm.items():
        if r.get("manual_delivered") == "Yes" and i in C:
            C[i]["manual_delivered"] = "Yes"
    out = [_order(a, C.get(i), P.get(i), today, late_days, stalled_min) for i, a in A.items()]
    # 4. orders the DB dates into the range that Amazon's report does not list for it: on a day
    #    without a report they are all there is; otherwise Amazon's day decides (edges, time zones)
    extra = {}
    for i, row in list(C.items()) + list(P.items()):
        if i in A or i in extra:
            continue
        day = _day(row.get("purchase_date"))
        if d0 <= day <= d1 and (not acct or row.get("account") == acct):
            extra[i] = day
    if extra and _has_table(c, "amazon_report_orders"):
        ids = list(extra)
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            for oid, pday in c.execute(f"SELECT amazon_order_id, purchase_day FROM amazon_report_orders "
                                       f"WHERE amazon_order_id IN ({','.join('?' * len(part))})", part):
                if pday and not (d0 <= pday <= d1):
                    extra.pop(oid, None)
    for i, day in extra.items():
        o = _order(None, C.get(i), None if i in C else P.get(i), today, late_days, stalled_min)
        o["day"] = day
        out.append(o)
    for o in out:
        r = crm.get(o["id"])
        if r:
            o["flag"] = r.get("flag_color") or o["flag"]
            o["tags"] = r.get("tags") or ""
            o["urgent"] = r.get("is_urgent") == "Yes"
    return out


def _who(q):
    """(profile, gmail) filters from the query: '' = any; profile 'none' = placed with no profile (by hand)."""
    prof = _q1(q, "profile").strip().upper()
    gmail = _q1(q, "gmail").strip()
    return ("" if prof in ("", "ALL") else prof), ("" if gmail.lower() in ("", "all") else gmail)


def _keep(o, prof, gmail):
    if prof and (o["prof"] or ("NONE" if o["placed"] else "")) != prof:
        return False
    return not gmail or o["gmail"] == gmail


def _options(orders, prof, gmail):
    """Dropdown choices with order counts. Each list is counted under the OTHER filter, so picking
    an inbox narrows the profiles to the ones that bought with it, and the other way round."""
    profs, mails = Counter(), Counter()
    for o in orders:
        if not o["placed"]:
            continue
        if _keep(o, "", gmail):
            profs[o["prof"] or "NONE"] += 1
        if _keep(o, prof, "") and o["gmail"]:
            mails[o["gmail"]] += 1
    return sorted(profs.items()), sorted(mails.items(), key=lambda x: (-x[1], x[0]))


def _agg(orders):
    t = {"orders": 0, "units": 0, "sales": 0.0, "placed": 0, "late": 0, "earn": 0.0, "cost": 0.0, "profit": 0.0,
         "in_amazon": 0, "in_system": 0, "missing_in_system": 0, "not_in_amazon": 0,
         "checkin": 0, "review": 0, "delay": 0}
    amz, lab, dst, why = Counter(), Counter(), Counter(), Counter()
    for o in orders:
        t["orders"] += 1
        t["units"] += o["units"]
        t["sales"] += o["sales"]
        t["placed"] += 1 if o["placed"] else 0
        t["late"] += 1 if o["late"] else 0
        t["earn"] += o["earn"]
        t["cost"] += o["cost"]
        t["profit"] += o["profit"] or 0
        t["in_amazon"] += 1 if o["in_amazon"] else 0
        t["in_system"] += 1 if o["in_system"] else 0
        t["missing_in_system"] += 1 if (o["in_amazon"] and o["wb"] and not o["in_system"]) else 0
        t["not_in_amazon"] += 1 if (o["in_system"] and not o["in_amazon"]) else 0
        t["checkin"] += 1 if o["checkin"] else 0
        t["review"] += 1 if o["review"] else 0
        t["delay"] += 1 if o["delay"] else 0
        amz[o["amz"] or "—"] += 1
        lab[o["label"]] += 1
        dst[o["dstate"]] += 1
        if o["label"] == "Incomplete" and o["wkey"]:
            why[o["wkey"]] += 1
    for k in ("sales", "earn", "cost", "profit"):
        t[k] = round(t[k], 2)
    t["amz"], t["label"], t["del"], t["why"] = dict(amz), dict(lab), dict(dst), dict(why)
    return t


# ── cache ─────────────────────────────────────────────────────────────────
# A month is ~16k orders across three tables: 2–4 s cold on this machine, more on Render. The
# underlying rows only change when closing / the Zoro sweep / the report sync run, so a result is
# served for up to _STALE seconds and refreshed in the background once it is older than _FRESH:
# the page opens at once after the first load, and is never more than a few minutes behind.
_CACHE, _CACHE_LOCK, _FRESH, _STALE = {}, threading.Lock(), 60.0, 1800.0
_REFRESHING = set()


def _cached(key, fn, force=False):
    now = time.time()
    with _CACHE_LOCK:
        hit = None if force else _CACHE.get(key)
    if hit and now - hit[0] < _FRESH:
        return hit[1]
    if hit and now - hit[0] < _STALE:
        with _CACHE_LOCK:
            start = key not in _REFRESHING
            _REFRESHING.add(key)
        if start:
            def refresh():
                try:
                    val = fn()
                    with _CACHE_LOCK:
                        _CACHE[key] = (time.time(), val)
                except Exception:
                    pass
                finally:
                    with _CACHE_LOCK:
                        _REFRESHING.discard(key)
            threading.Thread(target=refresh, daemon=True, name="month-report-refresh").start()
        return hit[1]
    val = fn()
    with _CACHE_LOCK:
        if len(_CACHE) > 64:
            for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:32]:
                _CACHE.pop(k, None)
        _CACHE[key] = (time.time(), val)
    return val


LAST30 = "last30"   # the rolling window the daily all-orders report covers — the page's default


def _month_key(q):
    m = (_q1(q, "month") or LAST30).strip().lower()
    return LAST30 if m in (LAST30, "30", "last30days") else m


def _month_bounds(month):
    if month == LAST30:
        today = _today()
        return today - timedelta(days=29), today
    try:
        y, m = (int(x) for x in str(month).split("-")[:2])
        first = date(y, m, 1)
    except (ValueError, TypeError):
        first = _today().replace(day=1)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt - timedelta(days=1)


def _months(c):
    ms = set()
    if _has_table(c, "amazon_report_orders"):
        ms |= {r[0] for r in c.execute("SELECT DISTINCT substr(purchase_day,1,7) FROM amazon_report_orders "
                                       "WHERE purchase_day LIKE '2%'")}
    ms |= {r[0] for r in c.execute("SELECT DISTINCT substr(purchase_date,1,7) FROM closing_orders "
                                   "WHERE purchase_date LIKE '2___-__%'")}
    ms.add(_today().strftime("%Y-%m"))
    return sorted((m for m in ms if m and len(m) == 7), reverse=True)[:24]


# ── endpoints ─────────────────────────────────────────────────────────────
def month_report(q):
    acct = _q1(q, "account")
    acct = "" if acct.lower() in ("", "all", "both") else acct
    prof, gmail = _who(q)
    mkey = _month_key(q)
    first, last = _month_bounds(mkey)

    def build():
        c = _ro()
        try:
            today = _today()
            end = min(last, today) if first <= today else last
            orders = _collect(c, first.isoformat(), end.isoformat(), acct) if first <= today else []
            profiles, gmails = _options(orders, prof, gmail)
            if prof or gmail:
                orders = [o for o in orders if _keep(o, prof, gmail)]
            cov = _coverage(c)
            accts = _accounts(acct)
            by_day = {}
            for o in orders:
                by_day.setdefault(o["day"], []).append(o)
            days = []
            d = first
            while d <= end:
                k = d.isoformat()
                row = _agg(by_day.get(k, []))
                row.update(date=k, dow=d.strftime("%a"), covered=_covered(cov, k, accts))
                days.append(row)
                d += timedelta(days=1)
            tot = _agg(orders)
            late_days, stalled_min = _thresholds()
            covinfo = {a: ({"from": min(x[0] for x in cov[a]), "to": max(x[1] for x in cov[a]),
                            "last_ingested": max(x[3] or "" for x in cov[a]), "files": len(cov[a])}
                           if cov.get(a) else None) for a in accts}
            try:   # the last completed Zoro sweep (a MAX over closing_orders is a 1.6 s full scan);
                # closing_run_log stores UTC, so it goes out as ISO with a Z for the browser
                last_check = (c.execute("SELECT COALESCE(completed_at, started_at) AS t FROM closing_run_log "
                                        "WHERE run_id LIKE 'zoro-%' AND status='completed' "
                                        "ORDER BY t DESC LIMIT 1").fetchone() or [""])[0] or ""
                last_check = (str(last_check).replace(" ", "T")[:19] + "Z") if last_check else ""
            except sqlite3.Error:
                last_check = ""
            return {"month": mkey if mkey == LAST30 else first.strftime("%Y-%m"),
                    "account": acct or "All", "profile": prof, "gmail": gmail,
                    "profiles": profiles, "gmails": gmails, "from": first.isoformat(),
                    "to": end.isoformat(), "today": today.isoformat(), "months": _months(c),
                    "totals": tot, "days": days, "coverage": covinfo,
                    # today is still being written; a report downloaded this morning covers it only in part
                    "uncovered": [x["date"] for x in days if x["covered"] is not True and x["date"] != today.isoformat()],
                    "late_days": late_days, "stalled_days": stalled_min, "why_labels": WHY,
                    "delivery_checked": last_check, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        finally:
            c.close()
    return _cached(("m", DB, mkey, first.isoformat(), acct, prof, gmail), build, force=_q1(q, "fresh") == "1")


def month_day(q):
    acct = _q1(q, "account")
    acct = "" if acct.lower() in ("", "all", "both") else acct
    prof, gmail = _who(q)
    day = _day(_q1(q, "date")) or _today().isoformat()

    def build():
        c = _ro()
        try:
            orders = _collect(c, day, day, acct)
        finally:
            c.close()
        if prof or gmail:
            orders = [o for o in orders if _keep(o, prof, gmail)]
        orders.sort(key=lambda o: (o["t"] or "99:99", o["id"]))
        return {"date": day, "account": acct or "All", "profile": prof, "gmail": gmail,
                "orders": orders, "totals": _agg(orders)}
    return _cached(("d", DB, day, acct, prof, gmail), build, force=_q1(q, "fresh") == "1")


CSV_COLS = (("date", "day"), ("time_pt", "t"), ("account", "acct"), ("profile", "prof"), ("zoro_gmail", "gmail"),
            ("amazon_order_id", "id"),
            ("amazon_status", "amz"), ("merchant_order_id", "mid"), ("wb", "wb"), ("label", "label"),
            ("reason", "why"), ("delivery", "dstate"), ("late_days", "late"), ("no_movement_days", "stalled"),
            ("deliver_by", "deliver_by"), ("delivered", "delivered"), ("tracking", "tracking"),
            ("last_scan", "last_scan"), ("sku", "sku"), ("asin", "asin"), ("title", "title"), ("units", "units"),
            ("sales", "sales"), ("earnings", "earn"), ("zoro_cost", "cost"), ("profit", "profit"),
            ("city", "city"), ("state", "state"), ("checkin_sent", "checkin"), ("review_sent", "review"),
            ("delay_notice_sent", "delay"), ("in_amazon_report", "in_amazon"), ("in_closing", "in_closing"))
_DSTATE_TEXT = {"delivered": "Delivered", "transit": "In transit", "stalled": "No movement", "exception": "Exception",
                "freight": "Freight", "zoro_cancelled": "Zoro cancelled", "not_shipped": "Not shipped", "na": ""}


def month_csv(q):
    acct = _q1(q, "account")
    acct = "" if acct.lower() in ("", "all", "both") else acct
    prof, gmail = _who(q)
    mkey = _month_key(q)
    first, last = _month_bounds(mkey)
    end = min(last, _today())
    c = _ro()
    try:
        orders = _collect(c, first.isoformat(), end.isoformat(), acct) if first <= end else []
    finally:
        c.close()
    if prof or gmail:
        orders = [o for o in orders if _keep(o, prof, gmail)]
    orders.sort(key=lambda o: (o["day"], o["t"] or "99:99", o["id"]))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([h for h, _ in CSV_COLS])
    for o in orders:
        row = []
        for _, k in CSV_COLS:
            v = o.get(k)
            if k == "dstate":
                v = _DSTATE_TEXT.get(v, v)
            if isinstance(v, bool):
                v = "yes" if v else ""
            row.append("" if v is None else v)
        w.writerow(row)
    tag = f"{first.isoformat()}_to_{end.isoformat()}" if mkey == LAST30 else first.strftime('%Y-%m')
    who = "".join(f"_{x.lower()}" for x in (prof, gmail) if x)
    return buf.getvalue(), f"orders_{tag}_{(acct or 'all').lower()}{who}.csv"
