"""
tracking_alert.py — the WhatsApp alert for an order whose tracking the tracker just uploaded
(owner, 15 Oct 2026). Layout, exactly the owner's list:

    *Order:* 113-1156499-4150647
    *Order Received PKT:* Oct 15, 1:59 AM      ← Amazon "Sold, ship now" e-mail
    *Order Placed PKT:* Oct 15, 2:01 AM        ← Zoro "has been received" e-mail
    *Order Shipped PKT:* Oct 15, 4:00 AM       ← Zoro "has shipped" e-mail
    *Time Shipped:* 1h 59m                     ← placed → shipped, Zoro's shipping hours only
    *Tracking IDs (6 × FedEx):*
    522715808643
    …
    *Tracking Message Sent:* Yes / No Permission / …

Times are the e-mails' own timestamps, which the archive stores in PKT (the Mac's clock).

Time Shipped counts only Zoro's shipping hours (owner, 11 Nov 2026: "zoro starts shipping from 6pm
till 3pm" PKT; he chose 3 PM over a 12 PM close): a shipping day opens at 8 AM US Eastern and closes
at 5 AM Eastern the next morning (_SHIP_CLOSE), Monday to Friday — Friday's runs to Saturday 5 AM —
without the six US holidays on which Zoro ships almost nothing. Worked in America/New_York, so in
PKT it is 6 PM to 3 PM next day in winter and 5 PM to 2 PM while the US is on daylight time.
The gap (3 PM–6 PM PKT in winter), weekends and holidays never count; a time inside them counts
from the next opening. Times are cut to the minute first, like the clock lines the alert shows.
  Why: Zoro's first shipped e-mail per WB (archive_tracking) arrives from about 10 AM to 1–2 AM
Eastern, with a thin tail to 5 AM. Of those since 1 Nov 2026, 99.3 % fall inside 8 AM–5 AM Eastern
(94.5 % inside 8 AM–2 AM; Sep–Oct: 98.6 % and 93.0 %), against 40.8 % inside 8 AM–5 PM.
Saturday-dated e-mails are 2.7 % of all, about two-thirds of them 0–2 AM (Friday's night) and most
of the rest before 6 AM; Sunday 0.4 %.
Examples, winter weekday: placed 12:00 PM PKT (2 AM Eastern, inside the window), shipped 7:00 PM
PKT (9 AM Eastern) → 4h 00m (12–3 PM and 6–7 PM PKT; the owner's own example, which he accepted
at 4 hours when he chose 3 PM); placed 3:00 PM PKT, shipped 7:00 PM PKT → 1h 00m.

Carrier per tracking id comes from Zoro's ship method; anything that is not FedEx / UPS is an LTL
truck line (SouthEastern, ODFL, Pyle, Saia …) and is named as freight.

Run `python utils/tracking_alert.py <amazon-order-id>` to print the alert for any order (nothing sent).
"""
import os
import re
import sqlite3
from datetime import date, datetime, time, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:          # no tz database (bare Windows Python): _et_utc applies the US rule itself
    _ET = None

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(_AUTO, "data", "automations_unified.db")
_CLOCK = re.compile(r"\d{1,2}:\d{2}")
_WB = re.compile(r"WB\d{6,}")


def _ts(v):
    """'2026-10-15 01:59:39' / '2026-10-15T02:00:31' → datetime; None without a clock time."""
    s = str(v or "").strip()
    if not _CLOCK.search(s):
        return None
    try:
        return datetime.fromisoformat(s.replace("T", " ")[:19])
    except ValueError:
        return None


def _show(dt):
    return f"{dt:%b} {dt.day}, {dt:%I:%M %p}".replace(", 0", ", ") if dt else "—"


_PKT = timezone(timedelta(hours=5))            # the archive's clock; Pakistan has kept no DST since 2009
_SHIP_OPEN = time(8, 0)     # a Zoro shipping day opens at 8 AM US Eastern (6 PM PKT in winter, 5 PM in summer)
# ... and closes at this US Eastern time the NEXT morning: 5 AM = 3 PM PKT in winter, 2 PM in
# summer (owner, 11 Nov 2026: "3 PM is good time"). time(2, 0) would be the 12 PM PKT close.
# _ship_window never lets it run past the next opening, so hours are never counted twice.
_SHIP_CLOSE = time(5, 0)


def _nth_weekday(year, month, weekday, n):
    """The n-th `weekday` (Mon=0) of the month; n=-1 is the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _zoro_ships(d):
    """Is US Eastern date `d` a Zoro shipping day, i.e. does a window open on it at 8 AM? Monday to
    Friday (Saturday-dated shipped e-mails are ~2.7 %, about two-thirds of them 0–2 AM inside
    Friday's window; Sunday 0.4 %), except New Year's Day, Memorial Day, 4 July, Labor Day,
    Thanksgiving and Christmas, when its shipped e-mails fall to a small share of a normal day
    (Labor Day 2026: 13 against ~500 the Friday before; Memorial Day 2026: 22 against ~900 the day
    after). A holiday on a weekend is not moved to the Friday or Monday (4 July 2026, a Saturday:
    Zoro still shipped 160 on Friday 3 July). The window of the day before a holiday still runs to
    _SHIP_CLOSE on the holiday."""
    if d.weekday() >= 5:
        return False
    y = d.year
    return d not in (date(y, 1, 1), _nth_weekday(y, 5, 0, -1), date(y, 7, 4),
                     _nth_weekday(y, 9, 0, 1), _nth_weekday(y, 11, 3, 4), date(y, 12, 25))


def _et_utc(d, t):
    """US Eastern wall time `t` on date `d` → aware UTC datetime."""
    if _ET is not None:
        return datetime.combine(d, t, tzinfo=_ET).astimezone(timezone.utc)
    # No tz database: US daylight time runs from 2 AM on the second Sunday of March to 2 AM on the
    # first Sunday of November. Read like zoneinfo's fold=0: 2–3 AM on the spring day (a time that
    # does not exist) as EST, 1–2 AM on the autumn day (which happens twice) as EDT.
    local = datetime.combine(d, t)
    edt = (datetime.combine(_nth_weekday(d.year, 3, 6, 2), time(3, 0)) <= local
           < datetime.combine(_nth_weekday(d.year, 11, 6, 1), time(2, 0)))
    return (local + timedelta(hours=4 if edt else 5)).replace(tzinfo=timezone.utc)


def _ship_window(d):
    """Zoro's shipping hours of US Eastern shipping day `d` — 8 AM on d to _SHIP_CLOSE on d+1 — as
    UTC datetimes. d+1 is Tuesday to Saturday, never the Sunday of a US clock change. The close is
    held at or before the next day's opening, so a close set too late can never count hours twice."""
    nxt = d + timedelta(days=1)
    return _et_utc(d, _SHIP_OPEN), min(_et_utc(nxt, _SHIP_CLOSE), _et_utc(nxt, _SHIP_OPEN))


def _utc(dt):
    """A naive archive time is PKT."""
    return (dt.replace(tzinfo=_PKT) if dt.tzinfo is None else dt).astimezone(timezone.utc)


def _span(a, b):
    """Time Shipped: placed → shipped counting only Zoro's shipping hours (see the module doc).
    Placed 3:00 PM and shipped 7:00 PM PKT on a winter weekday → 1h 00m; placed 12:00 PM → 4h 00m."""
    if not (a and b):
        return "—"
    # cut to the minute like the clock lines the alert shows (PKT and UTC differ by whole hours)
    a = _utc(a).replace(second=0, microsecond=0)
    b = _utc(b).replace(second=0, microsecond=0)
    if b < a:
        return "—"
    # Eastern is UTC−4 or −5, so these bracket the Eastern dates of a and b; the walk starts one day
    # earlier because a window runs past midnight into the next date. Days that miss [a, b] add 0.
    secs = 0.0
    d, last = (a - timedelta(hours=5)).date() - timedelta(days=1), (b - timedelta(hours=4)).date()
    while d <= last:
        if _zoro_ships(d):
            lo, hi = _ship_window(d)
            lo, hi = max(lo, a), min(hi, b)
            if hi > lo:
                secs += (hi - lo).total_seconds()
        d += timedelta(days=1)
    mins = int(secs // 60)
    return f"{mins // 60}h {mins % 60:02d}m"


def carrier_label(carrier, method, tid=""):
    """Zoro's ship method → the carrier name the owner reads. FedEx and UPS by name; every other
    method seen since June 2026 is an LTL truck line → '<line> (Freight)'."""
    m, c = str(method or "").strip(), str(carrier or "").strip()
    u = f"{m} {c}".upper()
    if ("FREIGHT" in u or "FRT" in u or u.startswith("FXFE")) and ("FED" in u or "FDX" in u or "FXFE" in u):
        return "FedEx Freight"
    if u.startswith("UPS") or " UPS" in u or str(tid).upper().startswith("1Z"):
        return "UPS"
    if any(k in u for k in ("FED EX", "FEDEX", "FDX", "FDEG")):
        return "FedEx"
    if m:
        name = m.title() if m.isupper() else m
        return name if "FREIGHT" in m.upper() else f"{name} (Freight)"
    if re.fullmatch(r"\d{12,15}", str(tid)):
        return "FedEx"
    return "Carrier unknown"


def facts(amazon_id, wb_ids=None, conn=None):
    """Received / placed / shipped times and {tracking id: carrier} for one order, from the e-mail
    archive first, then closing's row, then the placer's row (read-only)."""
    own = conn is None
    conn = conn or sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    try:
        def one(sql, args):
            try:
                r = conn.execute(sql, args).fetchone()
                return r if r else ()
            except sqlite3.Error:
                return ()
        cl = one("SELECT wb_order_id, sold_email_time, zoro_email_ts, shipped_email_time "
                 "FROM closing_orders WHERE amazon_order_id=?", (amazon_id,))
        pl = one("SELECT wb_order_id, placed_at, shipped_email_time FROM order_placer_orders "
                 "WHERE amazon_order_id=? ORDER BY placed_at DESC LIMIT 1", (amazon_id,))
        wbs = list(dict.fromkeys(_WB.findall(" ".join(str(x) for x in (wb_ids or []))) +
                                 _WB.findall(str(cl[0] if cl else "")) + _WB.findall(str(pl[0] if pl else ""))))
        ph = ",".join("?" * len(wbs)) or "''"

        received = _ts((one("SELECT MIN(email_timestamp) FROM archive_earnings WHERE amazon_order_id=?",
                            (amazon_id,)) or [None])[0]) or _ts(cl[1] if cl else None)
        placed = (_ts((one(f"SELECT MIN(email_timestamp) FROM archive_zoro WHERE wb_order_id IN ({ph})",
                           wbs) or [None])[0])
                  or _ts(cl[2] if cl else None) or _ts(pl[1] if pl else None))
        shipped, ids, method_label = None, {}, ""
        try:
            rows = conn.execute(f"SELECT tracking_ids, carrier, delivery_method, email_timestamp FROM archive_tracking "
                                f"WHERE wb_order_id IN ({ph}) ORDER BY email_timestamp", wbs).fetchall()
        except sqlite3.Error:
            rows = []
        for tids, carrier, method, ts in rows:
            t = _ts(ts)
            if t and (shipped is None or t < shipped):
                shipped = t
            if method or carrier:   # freight e-mails often carry the line but no number we could read
                method_label = carrier_label(carrier, method)
            for tid in str(tids or "").split(","):
                if tid.strip():
                    ids.setdefault(tid.strip(), carrier_label(carrier, method, tid.strip()))
        shipped = shipped or _ts(pl[2] if pl else None) or _ts(cl[3] if cl else None)
        return {"received": received, "placed": placed, "shipped": shipped, "carriers": ids,
                "method_carrier": method_label}
    finally:
        if own:
            conn.close()


def format_alert(amazon_id, tracking_ids, message_state, f, uploaded=None):
    """The caption. `tracking_ids`: every id Zoro sent; `uploaded`: the ids Amazon actually got."""
    tids = [t for t in dict.fromkeys(tracking_ids or []) if t] or list(f.get("carriers") or {})
    car = f.get("carriers") or {}
    fallback = f.get("method_carrier") or ""
    labels = [car.get(t) or (fallback if fallback and fallback != "Carrier unknown" else carrier_label("", "", t))
              for t in tids]
    kinds = list(dict.fromkeys(labels))
    head = ", ".join(f"{labels.count(k)} × {k}" for k in kinds) or "none"
    lines = [f"*Order:* {amazon_id}",
             f"*Order Received PKT:* {_show(f.get('received'))}",
             f"*Order Placed PKT:* {_show(f.get('placed'))}",
             f"*Order Shipped PKT:* {_show(f.get('shipped'))}",
             f"*Time Shipped:* {_span(f.get('placed'), f.get('shipped'))}",
             f"*Tracking IDs ({head}):*"]
    lines += [t + (f" ({lab})" if len(kinds) > 1 else "") for t, lab in zip(tids, labels)]
    # No remarks about what the bot did or did not do — the owner's boss reads these groups
    # (owner, 16 Oct 2026). Ids Amazon did not take are reported in the tracker's own log only.
    lines.append(f"*Tracking Message Sent:* {message_state}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    aid = sys.argv[1] if len(sys.argv) > 1 else "113-1156499-4150647"
    fx = facts(aid)
    print(format_alert(aid, list(fx["carriers"]), "Yes", fx))
