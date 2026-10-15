"""
tracking_alert.py — the WhatsApp alert for an order whose tracking the tracker just uploaded
(owner, 15 Oct 2026). Layout, exactly the owner's list:

    *Order:* 113-1156499-4150647
    *Order Received PKT:* Oct 15, 1:59 AM      ← Amazon "Sold, ship now" e-mail
    *Order Placed PKT:* Oct 15, 2:01 AM        ← Zoro "has been received" e-mail
    *Order Shipped PKT:* Oct 15, 4:00 AM       ← Zoro "has shipped" e-mail
    *Time Shipped:* 1h 59m                     ← placed → shipped
    *Tracking IDs (6 × FedEx):*
    522715808643
    …
    *Tracking Message Sent:* Yes / No Permission / …

Times are the e-mails' own timestamps, which the archive stores in PKT (the Mac's clock).
Carrier per tracking id comes from Zoro's ship method; anything that is not FedEx / UPS is an LTL
truck line (SouthEastern, ODFL, Pyle, Saia …) and is named as freight.

Run `python utils/tracking_alert.py <amazon-order-id>` to print the alert for any order (nothing sent).
"""
import os
import re
import sqlite3
from datetime import datetime

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


def _span(a, b):
    if not (a and b) or b < a:
        return "—"
    mins = int((b - a).total_seconds() // 60)
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
    missing = [t for t in tids if uploaded is not None and t not in uploaded]
    if missing:
        lines.append(f"⚠️ Not on Amazon yet: {', '.join(missing)} — add by hand")
    lines.append(f"*Tracking Message Sent:* {message_state}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    aid = sys.argv[1] if len(sys.argv) > 1 else "113-1156499-4150647"
    fx = facts(aid)
    print(format_alert(aid, list(fx["carriers"]), "Yes", fx))
