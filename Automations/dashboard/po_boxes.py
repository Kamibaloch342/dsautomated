"""
po_boxes.py — Attention → PO boxes: the orders the placers refused because they ship to a PO box.

The owner's rule (12 Oct 2026): Zoro ships by UPS/FedEx and neither delivers to a PO box, so such an
order is never placed — it is labelled "BB" on Amazon for the team to handle, and its address is kept
(utils.unified_db.po_box_orders) so this tab can show what kinds of PO-box addresses come in.

Orders labelled "POBOX" or "BB" before the table existed are listed too, without an address (the
placer did not keep one then, and Amazon's reports carry no street).
"""
import os
import re
import sqlite3
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
DB = os.environ.get("CLOUD_DB_PATH") if (os.environ.get("CLOUD_MODE") == "1" and os.environ.get("CLOUD_DB_PATH")) \
    else os.path.join(AUTO, "data", "automations_unified.db")

LABELS = ("BB", "POBOX")
_BOX = re.compile(r"\b(?:P\.?\s*O\.?\s*(?:BOX|BX)\b|POST\s+OFFICE\s+BOX\b|P\.?\s*O\.?\s*B\.?\s*#?\s*\d)[\s#]*\d*",
                  re.IGNORECASE)


# Wider net for the "how is it written" summary only (never for the placer's decision): catches the
# forms Zoro refused that the placer's pattern let through — "P0 Box", "PO Box123", "Box # 12", "Drawer".
_BOX_BROAD = re.compile(r"\b(?:P\W*[O0]\W*)?(?:BOX|BX|DRAWER|POB)\b[\s#]*\d*", re.IGNORECASE)


def _ro():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA query_only=ON")
    c.row_factory = sqlite3.Row
    return c


def _has(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone())


def _mask(text):
    """Digits → '#', except the '0' of a 'P0 Box' typo (it is part of how the box is written)."""
    t = str(text or "")
    return re.sub(r"\d+", lambda m: m.group(0) if (m.group(0) == "0" and m.start() > 0 and t[m.start() - 1] in "pP")
                  else "#", t)


def box_form(street):
    """How the box is written, numbers hidden: 'PO Box 123' → 'PO BOX #', 'P.O. Box 9' → 'P.O. BOX #'."""
    st = str(street or "")
    m = _BOX.search(st) or _BOX_BROAD.search(st)
    if m:
        return re.sub(r"\s+", " ", _mask(m.group(0))).strip().upper()
    # Zoro refused it although our pattern did not see a PO box: show the whole line, digits hidden
    return re.sub(r"\s+", " ", _mask(st)).strip().upper()[:40] or "(no street on record)"


def po_boxes(q):
    acct = ((q.get("account") or [""])[0] or "").strip()
    acct = "" if acct.lower() in ("", "all", "both") else acct
    c = _ro()
    try:
        rows, seen = [], set()
        has_ar = _has(c, "amazon_report_orders")
        if _has(c, "po_box_orders"):
            for r in c.execute("SELECT * FROM po_box_orders" + (" WHERE account=?" if acct else "")
                               + " ORDER BY last_seen DESC", ([acct] if acct else [])):
                d = dict(r)
                seen.add(d["amazon_order_id"])
                st = " ".join(filter(None, [d.get("street"), d.get("street2")]))
                d["form"] = box_form(st) if st.strip() else ""      # no street kept → no form to count
                rows.append(d)
        if has_ar:
            q_old = ("SELECT amazon_order_id, account, purchase_day, merchant_order_id, ship_city, ship_state, "
                     "ship_postal_code FROM amazon_report_orders WHERE UPPER(TRIM(merchant_order_id)) IN (?, ?)")
            args = list(LABELS)
            if acct:
                q_old += " AND account=?"
                args.append(acct)
            try:
                old = c.execute(q_old + " ORDER BY purchase_day DESC", args).fetchall()
            except sqlite3.OperationalError:          # older report table without ship_postal_code
                old = c.execute(q_old.replace(", ship_postal_code", "") + " ORDER BY purchase_day DESC", args).fetchall()
            for r in old:
                d = dict(r)
                if d["amazon_order_id"] in seen:
                    continue
                rows.append({"amazon_order_id": d["amazon_order_id"], "account": d["account"], "profile": "",
                             "street": "", "street2": "", "city": d.get("ship_city") or "",
                             "state": d.get("ship_state") or "", "zip": d.get("ship_postal_code") or "",
                             "detected_by": "label only (before addresses were kept)",
                             "label_written": d.get("merchant_order_id") or "", "first_seen": d.get("purchase_day") or "",
                             "last_seen": d.get("purchase_day") or "", "times_seen": None, "form": ""})
        if has_ar and rows:      # purchase day, status and title from Amazon's own report
            ids = [r["amazon_order_id"] for r in rows]
            info = {}
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                for r in c.execute("SELECT amazon_order_id, purchase_day, order_status, title, merchant_order_id FROM amazon_report_orders "
                                   f"WHERE amazon_order_id IN ({','.join('?' * len(part))})", part):
                    info[r["amazon_order_id"]] = dict(r)
            for r in rows:
                i = info.get(r["amazon_order_id"]) or {}
                r["purchase_day"] = i.get("purchase_day") or (r.get("first_seen") or "")[:10]
                r["order_status"] = i.get("order_status") or ""
                r["title"] = (i.get("title") or "")[:90]
                r["label_now"] = i.get("merchant_order_id") or ""
        kinds = Counter(r["form"] for r in rows if r.get("form"))
        return {"ready": True, "rows": rows, "count": len(rows), "label": LABELS[0],
                "kinds": sorted(kinds.items(), key=lambda x: -x[1]),
                "recorded": sum(1 for r in rows if r.get("street"))}
    finally:
        c.close()
