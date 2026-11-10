"""
fetch_grainger_costs.py — capture WW (Grainger) order COST + tracking from the
Grainger emails in abecomllc26, and map them onto the WW orders in closing_orders.

Grainger order-confirmation email (from Grainger@service.grainger.com):
  subject 'Order Confirmation #<orderno> PO #WW<po>'
  body: Subtotal $x / Estimated Tax $y / Estimated Shipping $z  → cost = x+y+z
Shipping-confirmation email: subject 'Shipping Confirmation #<orderno> PO #WW<po>'
  → carrier (FedEx/UPS) + tracking number.

Maps by PO (the WW merchant-order-id) → closing_orders.zoro_order_total/subtotal/
tax/shipping + tracking_ids/carrier (gap-fill, non-destructive). Earnings still come
from the Amazon side (archive_earnings by amazon_order_id) — run normal enrichment
for that. DRY-RUN by default.

Usage:
  ../.venv/bin/python scripts/fetch_grainger_costs.py 60          # preview
  ../.venv/bin/python scripts/fetch_grainger_costs.py 60 --go     # fetch + apply
"""
import os, sys, re, email as _email, sqlite3
from html.parser import HTMLParser

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FA = os.path.join(_AUTO, "gmail_fetch")   # fetch code lives here now (old sibling "Final Automation" was deleted)
sys.path.insert(0, _FA)
DB = os.path.join(_AUTO, "data", "automations_unified.db")
ACCT = "abecomllc26"
DAYS = int([a for a in sys.argv[1:] if a.isdigit()][0]) if any(a.isdigit() for a in sys.argv[1:]) else 60
GO = "--go" in sys.argv


class _T(HTMLParser):
    def __init__(s): super().__init__(); s.out = []
    def handle_data(s, d):
        t = re.sub(r"\s+", " ", d).strip()
        if t: s.out.append(t)


def _text(html):
    p = _T(); p.feed(html or ""); return " | ".join(p.out)


def parse_order(subject, html):
    po = (re.search(r"PO #(\S+)", subject) or [None, None])[1]
    ono = (re.search(r"#(\d{6,})", subject) or [None, None])[1]
    joined = _text(html)
    def money(label):
        m = re.search(label + r"\D{0,15}\$([\d,]+\.\d{2})", joined)
        return float(m.group(1).replace(",", "")) if m else None
    sub_, tax, ship = money("Subtotal"), money("Estimated Tax"), money("Estimated Shipping")
    total = round((sub_ or 0) + (tax or 0) + (ship or 0), 2)
    return dict(po=po, order_no=ono, subtotal=sub_, tax=tax, shipping=ship, total=total)


def parse_ship(subject, html):
    po = (re.search(r"PO #(\S+)", subject) or [None, None])[1]
    joined = _text(html)
    carrier = "FedEx" if "FedEx" in joined else ("UPS" if "UPS" in joined else "")
    tl = re.findall(r"trackingnumber[=/]([0-9]{10,})", html or "", re.I) or \
         re.findall(r"\b(\d{12,22})\b", joined)
    return dict(po=po, carrier=carrier, tracking=(list(dict.fromkeys(tl))[:1] or [""])[0])


def main():
    from utils import get_gmail_service, get_email_body_html, search_gmail, batch_get_messages
    svc = get_gmail_service(ACCT)
    if not svc:
        print(f"❌ no Gmail service for {ACCT}"); sys.exit(1)

    orders, ships = {}, {}
    for kind, sub_q, store, parser in [
        ("order", '"Order Confirmation"', orders, parse_order),
        ("ship",  '"Shipping Confirmation"', ships, parse_ship)]:
        q = f'from:(Grainger@service.grainger.com) subject:({sub_q}) newer_than:{DAYS}d'
        msgs = search_gmail(svc, q, max_results=None) or []
        print(f"[grainger {kind}] {len(msgs)} emails")
        for full in batch_get_messages(svc, [m["id"] for m in msgs], progress_label=f"[grainger {kind}] "):
            subj = ""
            for h in full.get("payload", {}).get("headers", []):
                if h.get("name", "").lower() == "subject": subj = h.get("value", ""); break
            html = get_email_body_html(full.get("payload", {}))
            d = parser(subj, html)
            if d.get("po"): store[d["po"].upper()] = d

    print(f"\nparsed: {len(orders)} order-confirmations, {len(ships)} shipping-confirmations")

    # map onto WW orders in closing_orders (PO == merchant_order_id)
    con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
    ww = con.execute("SELECT amazon_order_id, merchant_order_id, zoro_order_total, tracking_ids "
                     "FROM closing_orders WHERE merchant_order_id LIKE 'WW%'").fetchall()
    upd = 0
    for aid, moid, cur_cost, cur_trk in ww:
        po = (moid or "").upper()
        o = orders.get(po); s = ships.get(po)
        if not o and not s:
            continue
        sets, vals = [], []
        if o and o["total"] and (not cur_cost or cur_cost == 0):
            sets += ["zoro_order_total=?", "zoro_subtotal=?", "zoro_tax=?", "zoro_shipping=?"]
            vals += [o["total"], o["subtotal"], o["tax"], o["shipping"]]
        if s and s["tracking"] and not (cur_trk or "").strip():
            sets += ["tracking_ids=?", "carrier=?"]; vals += [s["tracking"], s["carrier"]]
        if sets and GO:
            vals.append(aid)
            con.execute(f"UPDATE closing_orders SET {', '.join(sets)} WHERE amazon_order_id=?", vals)
            upd += 1
        elif sets:
            upd += 1
            print(f"  {aid} PO={po}: cost={o['total'] if o else '-'} track={s['tracking'] if s else '-'}")
    if GO:
        con.commit()
        print(f"\n✅ mapped Grainger cost/tracking onto {upd} WW orders")
    else:
        print(f"\nDRY-RUN: would update {upd} WW orders. Add --go to apply.")
    con.close()


if __name__ == "__main__":
    main()
