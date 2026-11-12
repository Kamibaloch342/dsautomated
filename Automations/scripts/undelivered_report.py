#!/usr/bin/env python3
"""
undelivered_report.py — the undelivered orders of one or more Zoro accounts, as an Excel file.

Asked for on 12 Nov 2026: "the list of undelivered orders of robstark (1 Oct – 12 Nov) and
abecomllc26 (1 Sep – 12 Nov), from the launcher whenever I want, and it should first check
whether each one is already refunded, cancelled or found".

For every order a Zoro account bought in its date range that the database does not show as
delivered, BEFORE it goes on the list:

  1. FOUND?      Zoro's own order tracking is asked again, now (engine/zoro_tracking.py, the
                 same lookup and the same write-back as scripts/zoro_delivery_check.py — the
                 database is updated exactly as the daily sweep would). Delivered now → "Found".
                 That same answer says which Zoro account really bought the order (the e-mail
                 that unlocks it), whether Zoro CANCELLED it, and its Sales Order number (SO…).
  2. CANCELLED?  Zoro's order status (live, else closing_orders.zoro_order_status) and Amazon's
                 (amazon_report_orders / closing_orders.order_status).
  3. REFUNDED?   Zoro's "Cash Refund #" e-mails in the account's own Gmail (PDF or HTML), matched
                 to the order by the SO number, else by the buyer's Amazon phone code that Zoro
                 prints under the ship-to address. Parsed refunds are cached in
                 data/zoro_cash_refunds_cache.json, so a later run reads only the new e-mails.
                 Amazon's own refund to the buyer (archive_returns 'Refund Initiated') is shown
                 as a column — Zoro still owes the money for an undelivered parcel.

Each order lands in exactly one sheet: <account> undelivered · Already refunded (Zoro) ·
Cancelled · Found (delivered). A Summary sheet counts them.

Usage (from Automations/):
  ../.venv/bin/python scripts/undelivered_report.py                       # ranges saved by the launcher
  ../.venv/bin/python scripts/undelivered_report.py robstark771:2026-10-01:2026-11-12 abecomllc26:2026-09-01
  options: --no-live (no Zoro lookups) · --no-gmail (no refund e-mails) · --no-write (look up, change
           nothing in the database) · --out FILE.xlsx · --open (open the file when done, macOS)

An account is a Gmail token name (robstark771) or its address; the end date may be left out
(= today). The launcher's "📋 Undelivered" button (closing panel) runs this with --open.
"""
import argparse
import base64
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _AUTO)
sys.path.insert(0, os.path.join(_AUTO, "scripts"))
print = __import__("functools").partial(print, flush=True)

from utils.accounts_config import (acquire_lock, release_lock, gmail_addresses,  # noqa: E402
                                   store_for_profile, zoro_forwarding_hubs, zoro_profile_inboxes,
                                   zoro_tracking_cfg)

DB = os.path.join(_AUTO, "data", "automations_unified.db")
PREFS = os.path.join(_AUTO, "data", "undelivered_report.json")
REFUND_CACHE = os.path.join(_AUTO, "data", "zoro_cash_refunds_cache.json")
SO_CACHE = os.path.join(_AUTO, "data", "zoro_sales_orders.json")     # WB → Zoro SO, from the live lookups
OUT_DIR = os.path.join(os.path.dirname(_AUTO), "reports", "undelivered")
LOCK = "undelivered_report"
DELIVERED = ("Delivered", "AllDelivered")
DEFAULT_RANGES = [{"account": "robstark771", "from": "2026-10-01", "to": ""},
                  {"account": "abecomllc26", "from": "2026-09-01", "to": ""}]


# ── small helpers ────────────────────────────────────────────────────────────
_MON = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun",
                                    "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def day_of(v) -> str:
    """The first date in a closing / placer date field → 'YYYY-MM-DD' ('' when none).
    purchase_date comes in four shapes ('2026-11-05 23:50', 'Wed, May 20, 2026, 1:20 PM PDT',
    '04/24/2026', 'Apr 27, 2026 9:05:47 PM PDT'); deliver_by_date can read
    'Thu, Nov 12, 2026| ZoroDeliverBy:2026-11-13' — the part before '|' is Amazon's."""
    s = str(v or "").split("|", 1)[0].strip()
    if not s:
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b", s)
    if m and m.group(1).lower() in _MON:
        return f"{m.group(3)}-{_MON[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return ""


def digits(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def split_ids(s):
    out = []
    for t in str(s or "").replace(";", ",").split(","):
        t = t.strip()
        if t and t not in out:
            out.append(t)
    return out


_ADDR = None


def addr_of(token_or_email) -> str:
    v = str(token_or_email or "").strip()
    if "@" in v:
        return v.lower()
    return (gmail_addresses().get(v) or "").lower()


def token_of(email_or_token) -> str:
    """robstark771@gmail.com → robstark771 (the Gmail token name the stack uses)."""
    global _ADDR
    v = str(email_or_token or "").strip()
    if not v:
        return ""
    if "@" not in v:
        return v
    if _ADDR is None:
        _ADDR = {}
        for tok, a in gmail_addresses().items():
            _ADDR.setdefault(str(a).lower(), tok)
    return _ADDR.get(v.lower(), v.lower().split("@", 1)[0])


def atomic_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, default=str)
    os.replace(tmp, path)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ── ranges ───────────────────────────────────────────────────────────────────
def parse_ranges(specs):
    """['robstark771:2026-10-01:2026-11-12', …] (or the launcher's saved ranges) →
    [{'account': token, 'email': address, 'from': 'YYYY-MM-DD', 'to': 'YYYY-MM-DD'}]."""
    today = date.today().isoformat()
    raw = []
    if specs:
        for s in specs:
            parts = [p.strip() for p in str(s).split(":")]
            raw.append({"account": parts[0], "from": parts[1] if len(parts) > 1 else "",
                        "to": parts[2] if len(parts) > 2 else ""})
    else:
        raw = (load_json(PREFS, {}) or {}).get("ranges") or DEFAULT_RANGES
    out = []
    for r in raw:
        tok = token_of(r.get("account"))
        if not tok:
            continue
        frm = day_of(r.get("from")) or "2000-01-01"
        to = day_of(r.get("to")) or today
        email = addr_of(tok) or addr_of(r.get("account"))
        if not email:
            print(f"⚠️  {tok}: no e-mail address known for this account (config/accounts.json → gmail_addresses) "
                  f"— Zoro cannot be asked about its orders")
        out.append({"account": tok, "email": email, "from": frm, "to": to})
    return out


def in_range(rng, day):
    return bool(day) and rng["from"] <= day <= rng["to"]


# ── candidates ───────────────────────────────────────────────────────────────
def attribute(conn, row, profile, hubs):
    """(Zoro account token, how we know) for one order, before asking Zoro:
    the e-mail that unlocked it before > the inbox Zoro's confirmation landed in (not a
    forwarding hub) > the same from archive_zoro > the placer profile's inbox (a guess)."""
    if row.get("zoro_lookup_email"):
        return token_of(row["zoro_lookup_email"]), "Zoro lookup"
    for k in ("zoro_gmail_account", "zoro_account_name"):
        v = str(row.get(k) or "").strip()
        if v and v not in hubs:
            return token_of(v), "Zoro e-mail"
    wb = row.get("wb_order_id")
    if wb:
        for a in conn.execute("SELECT gmail_account, zoro_account_name FROM archive_zoro WHERE wb_order_id=? "
                              "ORDER BY email_timestamp DESC LIMIT 4", (wb,)):
            for v in a:
                v = str(v or "").strip()
                if v and v not in hubs:
                    return token_of(v), "Zoro e-mail"
    p = zoro_profile_inboxes().get(str(profile or "").upper())
    return (p, "placer profile (guess)") if p else ("", "unknown")


def load_candidates(conn, ranges, learned):
    """Every placed order (WB on file) in the union of the ranges that the database does not
    show as delivered — from closing_orders, plus orders the placer bought that closing has
    not ingested yet. Kept when its account is one of ours, or when the account is only a
    guess and the order's placer profile has bought with one of ours lately (Zoro decides)."""
    hubs = set(zoro_forwarding_hubs())
    lo = min(r["from"] for r in ranges)
    hi = max(r["to"] for r in ranges)
    want = {r["account"] for r in ranges}
    want_emails = {r["email"] for r in ranges if r["email"]}
    prof_of = {}
    for aid, prof in conn.execute(
            "SELECT amazon_order_id, profile_name FROM order_placer_orders WHERE id IN "
            "(SELECT MAX(id) FROM order_placer_orders GROUP BY amazon_order_id)"):
        prof_of[aid] = str(prof or "").strip().upper()

    def profile_could_be_ours(profile, store):
        """An order whose account is only a guess is asked about when its profile MOSTLY buys
        with one of the listed accounts (top two of what unlocked its orders lately, or its
        inbox in accounts.json). HS_COUPON bought a few hundred orders with robstark771 in
        Oct–Nov, but ~95 % elsewhere: asking Zoro about all its unproven orders would cost
        thousands of lookups for a handful; closing proves those within a day anyway."""
        key = profile or f"store:{store or ''}"
        cands = set(learned.get(key, [])[:2]) | {addr_of(zoro_profile_inboxes().get(profile, ""))}
        return bool(cands & want_emails)

    rows, seen = [], set()
    # closing_orders: compare the date in Python — purchase_date has several shapes
    for r in conn.execute("SELECT * FROM closing_orders WHERE COALESCE(wb_order_id,'') != '' "
                          "AND COALESCE(delivery_status,'') NOT IN ('Delivered','AllDelivered')"):
        row = dict(r)
        d = day_of(row.get("purchase_date"))
        if not d or not (lo <= d <= hi):
            continue
        prof = prof_of.get(row["amazon_order_id"]) or str(row.get("profile_name") or "").strip().upper()
        acct, how = attribute(conn, row, prof, hubs)
        guess = how in ("placer profile (guess)", "unknown")
        if acct in want or (guess and not row.get("delivery_checked_at") and profile_could_be_ours(prof, row.get("account"))):
            row.update(_src="closing", _day=d, _profile=prof, _acct=acct, _how=how)
            rows.append(row)
        seen.add(row["amazon_order_id"])
    # Orders the placer bought that closing has not ingested yet (closing runs a day behind).
    # Any order closing HAS is decided above — delivered ones must not come back through here.
    in_closing = {a for (a,) in conn.execute("SELECT amazon_order_id FROM closing_orders")}
    for r in conn.execute("SELECT * FROM order_placer_orders WHERE id IN "
                          "(SELECT MAX(id) FROM order_placer_orders WHERE COALESCE(wb_order_id,'') LIKE 'WB%' "
                          " GROUP BY amazon_order_id)"):
        row = dict(r)
        aid = row["amazon_order_id"]
        if aid in seen or aid in in_closing:
            continue
        d = day_of(row.get("purchase_date"))
        if not d or not (lo <= d <= hi):
            continue
        prof = str(row.get("profile_name") or "").strip().upper()
        base = {"order_status": "", "order_label": "", "delivery_status": "",
                "zoro_lookup_email": "", "zoro_gmail_account": "", "zoro_account_name": "",
                "zoro_order_status": "", "delivery_note": "", "carrier": "", "last_scan_time": "",
                "last_scan_location": "", "delivery_checked_at": "", "refund_done": "", "refund_amount": None}
        base.update(row)
        store = store_for_profile(prof) or str(row.get("account") or "")
        base["account"] = store
        acct, how = attribute(conn, base, prof, hubs)
        guess = how in ("placer profile (guess)", "unknown")
        if acct in want or (guess and profile_could_be_ours(prof, store)):
            base.update(_src="placer", _day=d, _profile=prof, _acct=acct, _how=how)
            rows.append(base)
    return rows


# ── live check (Zoro's own order tracking) ───────────────────────────────────
def live_check(conn, rows, want_emails, write=True, delay=None, workers=None):
    """Ask Zoro about every candidate now. Fills row['_live'] = {status, zoro_status, so, email,
    last_scan_time, last_scan_location, note, carrier, tracking, undelivered} and, for orders
    closing has, writes the answer back through the delivery sweep's own persist()."""
    import zoro_delivery_check as Z
    from engine.zoro_tracking import ZoroTracker, ZoroRefused

    cfg = zoro_tracking_cfg()
    delay = float(delay if delay is not None else os.environ.get("UNDELIVERED_DELAY") or cfg.get("delay_s", 0.4))
    workers = max(1, int(workers or os.environ.get("UNDELIVERED_WORKERS") or cfg.get("workers", 4)))
    transport = str(cfg.get("transport", "auto")).strip().lower()
    if transport == "browser":
        workers = 1
    Z._live_columns(conn)
    tr = ZoroTracker(transport=transport, port=int(cfg.get("port", 9225)), delay_s=delay)
    stats = {"asked": 0, "answered": 0, "not_found": 0, "errors": 0, "written": 0, "label_changes": 0}
    t0 = time.time()

    def prepared():
        for row in rows:
            primary, cands, pinned, key = Z.email_candidates(conn, row)
            # the account this list is about goes right after the order's own proof
            if row.get("_acct"):
                e = addr_of(row["_acct"])
                if e in want_emails and e in cands:
                    cands = cands[:pinned] + [e] + [c for c in cands[pinned:] if c != e]
            yield row, cands, pinned, key, Z.email_parcels(conn, row["wb_order_id"])

    def fetch(item):
        row, cands, pinned, key, mail_ids = item
        own_ids = split_ids(row.get("tracking_ids"))
        result, tried = None, []
        for email in Z.ranked(cands, pinned, key):
            tried.append(email)
            result = tr.track_order(row["wb_order_id"], email, fallback_tracking=own_ids, email_tracking=mail_ids)
            if result["outcome"] == "ok":
                Z.note_win(key, email)
                return result, email, tried
            if result["outcome"] != "not_found":
                break
        return result, "", tried

    n = len(rows)
    print(f"[live] asking Zoro about {n} order(s) ({tr.mode} transport, {delay}s pace, {workers} worker(s)"
          f"{', database is updated' if write else ', database NOT updated'})")
    try:
        for i, (item, (result, used, tried)) in enumerate(Z._pipeline(prepared(), fetch, workers), 1):
            row = item[0]
            stats["asked"] += 1
            if not result or result.get("outcome") != "ok":
                oc = (result or {}).get("outcome") or "no e-mail to ask with"
                stats["not_found" if oc == "not_found" else "errors"] += 1
                row["_live"] = {"error": "not found under " + ", ".join(tried) if oc == "not_found" else str(oc)}
            else:
                stats["answered"] += 1
                agg, order = result["aggregate"], result.get("order") or {}
                pk = result.get("packages") or []
                row["_live"] = {
                    "status": agg.get("delivery_status") or "",
                    "zoro_status": order.get("order_status") or "",
                    "so": order.get("sales_order") or "",
                    "email": used,
                    "zoro_total": num((order.get("totals") or {}).get("total")),
                    "last_scan_time": agg.get("last_scan_time") or "",
                    "last_scan_location": agg.get("last_scan_location") or "",
                    "delivery_date": agg.get("delivery_date") or "",
                    "note": agg.get("delivery_note") or "",
                    "carrier": ", ".join(sorted({p.get("carrier_code") or p.get("carrier") or "" for p in pk} - {""})),
                    "tracking": [p["tracking_number"] for p in pk if p.get("tracking_number")],
                    "undelivered": [p["tracking_number"] for p in pk
                                    if p.get("tracking_number") and not p.get("delivered_at")],
                    "line_items": order.get("line_items") or [],
                }
                if used:
                    row["_acct"], row["_how"] = token_of(used), "Zoro lookup (now)"
                if write and row["_src"] == "closing":
                    try:
                        now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        label = Z.persist(conn, row, result, now_s, used)
                        conn.commit()
                        stats["written"] += 1
                        if label:
                            stats["label_changes"] += 1
                    except sqlite3.Error as e:
                        print(f"  ⚠️ {row['wb_order_id']}: could not write the answer ({e}) — the list still uses it")
            if i % 50 == 0 or i == n:
                el = time.time() - t0
                print(f"  {i}/{n} · answered {stats['answered']} · not found {stats['not_found']} · "
                      f"errors {stats['errors']} · ETA {(n - i) * el / max(i, 1) / 60:.0f} min")
    except ZoroRefused as e:
        print(f"[live] 🚫 Zoro refused the lookups ({e}) — the rest of the list uses the database as it was")
        stats["refused"] = True
    finally:
        tr.close()
        # keep the SO numbers: a later run without the live check still matches refunds by SO
        try:
            sos = load_json(SO_CACHE, {}) or {}
            for row in rows:
                so = (row.get("_live") or {}).get("so")
                if so:
                    sos[row["wb_order_id"]] = so
            atomic_json(SO_CACHE, sos)
        except Exception as e:
            print(f"[live] ⚠️ could not save the SO numbers ({e})")
    stats["seconds"] = round(time.time() - t0)
    print(f"[live] done in {stats['seconds']} s — {stats}")
    return stats


# ── Zoro "Cash Refund" e-mails ───────────────────────────────────────────────
def _find_att(p, ext):
    if (p.get("filename") or "").lower().endswith(ext):
        return (p.get("body") or {}).get("attachmentId")
    for sp in p.get("parts", []) or []:
        r = _find_att(sp, ext)
        if r:
            return r
    return None


def parse_refund_text(txt, subject=""):
    """Text of a Zoro Cash Refund (PDF or the HTML body) → the facts we match on."""
    t = re.sub(r"[ \t]+", " ", txt or "")
    so = re.search(r"\b(SO\d{6,10})\b", t)
    ref = (re.search(r"Cash Refund\s*#?\s*(\d{5,9})", t) or re.search(r"Refund\s*#\s*(\d{5,9})", t)
           or re.search(r"#\s*(\d{5,9})", subject or ""))
    dt = re.search(r"(?:Cash Refund Date|Date)\s*(\d{1,2}/\d{1,2}/\d{4})", t)
    tot = re.search(r"\bTotal\s*\$\s*([\d,]+\.\d{2})", t) or re.search(r"\bTotal\s+([\d,]+\.\d{2})\s*$", t, re.M)
    frm = re.search(r"Created From\s*((?:Return Authorization|Cash/CC Sale)\s*#\s*\d+)", t)
    wbs = sorted(set(re.findall(r"\b(WB\d{8,12})\b", t)))
    # The buyer's Amazon phone code is printed under the ship-to street: 13–16 digits, unique per order
    phones = sorted(set(x for x in re.findall(r"(?<![\d*])(\d{13,16})(?!\d)", t)))
    d = ""
    if dt:
        mm, dd, yy = dt.group(1).split("/")
        d = f"{yy}-{int(mm):02d}-{int(dd):02d}"
    return {"refund_no": ref.group(1) if ref else "", "so": so.group(1) if so else "", "date": d,
            "total": float(tot.group(1).replace(",", "")) if tot else None,
            "created_from": frm.group(1) if frm else "", "wbs": wbs, "phones": phones}


def zoro_cash_refunds(accounts, since):
    """{account: [refund dicts]} for each Gmail token, read through the cache."""
    sys.path.insert(0, os.path.join(_AUTO, "gmail_fetch"))
    from gmail_fetch.utils import get_gmail_service, search_gmail, get_email_body_html, clean_html
    cache = load_json(REFUND_CACHE, {}) or {}
    out, changed = {}, False
    after = since.replace("-", "/")
    for acct in accounts:
        svc = get_gmail_service(acct)
        if svc is None:
            print(f"[refunds] ⚠️ {acct}: no working Gmail token — its Zoro refunds were NOT checked")
            out[acct] = None
            continue
        try:
            msgs = search_gmail(svc, f'from:zoro.com subject:"Cash Refund" after:{after}') or []
        except Exception as e:
            print(f"[refunds] ⚠️ {acct}: Gmail search failed ({e}) — its Zoro refunds were NOT checked")
            out[acct] = None
            continue
        new = [m["id"] for m in msgs if f"{acct}:{m['id']}" not in cache]
        print(f"[refunds] {acct}: {len(msgs)} Cash Refund e-mail(s) since {since}, {len(new)} not read before")
        for i, mid in enumerate(new, 1):
            try:
                full = svc.users().messages().get(userId="me", id=mid).execute()
                payload = full.get("payload", {})
                subj = next((h["value"] for h in payload.get("headers", []) if h["name"].lower() == "subject"), "")
                txt = ""
                att_id = _find_att(payload, ".pdf")
                if att_id:
                    import pdfplumber
                    att = svc.users().messages().attachments().get(userId="me", messageId=mid, id=att_id).execute()
                    with pdfplumber.open(io.BytesIO(base64.urlsafe_b64decode(att["data"]))) as pdf:
                        txt = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
                if not txt.strip():
                    html_id = _find_att(payload, ".html")
                    if html_id:
                        att = svc.users().messages().attachments().get(userId="me", messageId=mid, id=html_id).execute()
                        txt = clean_html(base64.urlsafe_b64decode(att["data"]).decode("utf-8", errors="ignore"))
                    else:
                        txt = clean_html(get_email_body_html(payload))
                rec = parse_refund_text(txt, subj)
                rec.update(account=acct, msg_id=mid, subject=subj,
                           received=datetime.fromtimestamp(int(full.get("internalDate", 0)) / 1000).strftime("%Y-%m-%d %H:%M"))
                cache[f"{acct}:{mid}"] = rec
                changed = True
            except Exception as e:
                print(f"  ⚠️ {acct} message {mid}: {e}")
            if i % 25 == 0 or i == len(new):
                print(f"  read {i}/{len(new)}")
        ids = {m["id"] for m in msgs}
        out[acct] = [v for k, v in cache.items() if k.startswith(f"{acct}:") and v.get("msg_id") in ids]
    if changed:
        atomic_json(REFUND_CACHE, cache)
    return out


def match_refunds(rows, refunds_by_acct):
    """row['_zoro_refunds'] = refunds that belong to the order: same SO, same WB in the text, or the
    order's Amazon ship-to phone code in the refund's ship-to block. Refunds of every listed
    account are tried (an order can turn out to be another of our accounts')."""
    by_so, by_wb, by_phone = {}, {}, {}
    for lst in refunds_by_acct.values():
        for rf in lst or []:
            if rf.get("so"):
                by_so.setdefault(rf["so"], []).append(rf)
            for w in rf.get("wbs") or []:
                by_wb.setdefault(w, []).append(rf)
            for p in rf.get("phones") or []:
                by_phone.setdefault(p, []).append(rf)
    so_cache = load_json(SO_CACHE, {}) or {}
    for row in rows:
        found, how = {}, set()
        so = (row.get("_live") or {}).get("so") or so_cache.get(row["wb_order_id"]) or ""
        row["_so"] = so
        for rf in by_so.get(so, []) if so else []:
            found[rf["msg_id"]] = rf; how.add("SO")
        for rf in by_wb.get(row["wb_order_id"], []):
            found[rf["msg_id"]] = rf; how.add("WB")
        ph = digits(row.get("ship_phone"))
        if len(ph) >= 13:
            for rf in by_phone.get(ph, []):
                if rf["msg_id"] not in found:
                    found[rf["msg_id"]] = rf; how.add("buyer phone code")
        row["_zoro_refunds"] = sorted(found.values(), key=lambda r: r.get("date") or "")
        row["_zoro_refund_how"] = " + ".join(sorted(how))


# ── Amazon facts ─────────────────────────────────────────────────────────────
def amazon_facts(conn, rows):
    ids = [r["amazon_order_id"] for r in rows]
    refunds, status = {}, {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        ph = ",".join("?" * len(chunk))
        for aid, amt, ts in conn.execute(
                f"SELECT amazon_order_id, refund_amount, email_timestamp FROM archive_returns "
                f"WHERE email_type='refund_initiated' AND amazon_order_id IN ({ph}) ORDER BY email_timestamp", chunk):
            refunds.setdefault(aid, []).append((num(amt), str(ts or "")[:10]))
        for aid, st in conn.execute(
                f"SELECT amazon_order_id, order_status FROM amazon_report_orders WHERE amazon_order_id IN ({ph}) "
                f"ORDER BY COALESCE(last_updated, captured_at)", chunk):
            status[aid] = st or ""
    for row in rows:
        rf = refunds.get(row["amazon_order_id"]) or []
        if not rf and str(row.get("refund_done") or "").lower() == "yes":
            rf = [(num(row.get("refund_amount")), day_of(row.get("refund_date")))]
        row["_amz_refund"] = rf
        row["_amz_status"] = status.get(row["amazon_order_id"]) or str(row.get("order_status") or "")


# ── buckets ──────────────────────────────────────────────────────────────────
def classify(row):
    live = row.get("_live") or {}
    status = live.get("status") or str(row.get("delivery_status") or "")
    zst = (live.get("zoro_status") or str(row.get("zoro_order_status") or "")).strip()
    row["_status"], row["_zoro_status"] = status, zst
    if status in DELIVERED:
        return "found"
    if zst.lower() == "cancelled":
        row["_cancelled_by"] = "Zoro"
        return "cancelled"
    if row.get("_zoro_refunds"):
        return "refunded"
    if row.get("_amz_status", "").lower() == "cancelled" or str(row.get("order_label") or "") == "Cancelled":
        row["_cancelled_by"] = "Amazon (Zoro order " + (zst or "status unknown") + ")"
        return "cancelled"
    return "undelivered"


# ── Excel ────────────────────────────────────────────────────────────────────
COLS = [
    ("Zoro account", 14), ("Account known from", 18), ("Store", 9), ("Profile", 12),
    ("Amazon order", 21), ("WB order", 14), ("Zoro SO", 12), ("Purchase date", 16),
    ("Amazon deliver-by", 12), ("Days late", 9), ("Delivery status", 16), ("Last scan", 17),
    ("Last scan location", 24), ("Tracking note", 36), ("Carrier", 10), ("Undelivered tracking", 28),
    ("All tracking", 28), ("Zoro status", 11), ("Zoro total $", 11), ("Amazon order status", 12),
    ("Amazon refunded buyer", 22), ("Zoro refund", 34), ("Refund covers", 18), ("Closing label", 11), ("Buyer", 20),
    ("Ship to", 24), ("SKU", 20), ("Title", 50), ("Checked with Zoro", 17),
]


def row_values(row, today):
    live = row.get("_live") or {}
    dby = day_of(row.get("deliver_by_date"))
    late = ""
    if dby and row.get("_bucket") == "undelivered":
        try:
            late = (date.fromisoformat(today) - date.fromisoformat(dby)).days
        except ValueError:
            late = ""
        if isinstance(late, int) and late <= 0:
            late = ""                       # not due yet
    tracking = live.get("tracking") or split_ids(row.get("tracking_ids"))
    undeliv = live.get("undelivered") if live.get("tracking") else tracking
    amz = row.get("_amz_refund") or []
    amz_txt = "; ".join(f"${a:,.2f} {d}".strip() if a is not None else f"yes {d}".strip() for a, d in amz)
    zr = row.get("_zoro_refunds") or []
    zr_txt = "; ".join(f"#{r.get('refund_no')} {r.get('date')} ${(r.get('total') or 0):,.2f}" for r in zr)
    zoro_total = live.get("zoro_total") if live.get("zoro_total") is not None else num(row.get("zoro_order_total"))
    covers = ""
    if zr_txt:
        zr_txt += f" (by {row.get('_zoro_refund_how')})"
        got = sum(r.get("total") or 0 for r in zr)
        if not zoro_total:
            covers = f"${got:,.2f} (order total unknown)"
        elif got >= zoro_total - 0.05:
            covers = "whole order"
        elif got >= zoro_total - 7.00:      # Zoro keeps the $5.99 shipping (+ its tax) on a refund
            covers = "whole order less shipping"
        else:
            covers = f"part (${got:,.2f} of ${zoro_total:,.2f})"
    if live.get("error"):
        checked = "no — " + live["error"][:60]
    elif live:
        checked = "yes, " + datetime.now().strftime("%d %b %H:%M")
    else:
        checked = "not asked"
    ship_to = ", ".join(x for x in (str(row.get("ship_city") or "").strip(), str(row.get("ship_state") or "").strip(),
                                    str(row.get("ship_postal_code") or "").strip()) if x)
    note = live.get("note") or row.get("delivery_note") or ""
    status = row.get("_status") or ""
    if not status or status == "Untracked":
        if not tracking:
            status = "not shipped / no tracking"
        elif "freight" in note.lower() or "not trackable" in note.lower() or status == "Untracked":
            status = "Freight (not trackable)"
        else:
            status = "no status yet"
    return [
        row.get("_acct") or "", row.get("_how") or "", row.get("account") or "", row.get("_profile") or "",
        row["amazon_order_id"], row["wb_order_id"], live.get("so") or row.get("_so") or "",
        str(row.get("purchase_date") or "")[:16],
        dby, late, status,
        live.get("last_scan_time") or row.get("last_scan_time") or "",
        live.get("last_scan_location") or row.get("last_scan_location") or "",
        note[:300], live.get("carrier") or row.get("carrier") or "",
        ", ".join(undeliv or []), ", ".join(tracking or []), row.get("_zoro_status") or "", zoro_total,
        row.get("_amz_status") or "", amz_txt, zr_txt, covers,
        row.get("order_label") or ("not in closing yet" if row["_src"] == "placer" else ""),
        row.get("ship_name") or "", ship_to, row.get("sku") or "", (row.get("title") or "")[:200], checked,
    ]


def write_excel(path, ranges, rows, stats, refunds_ok):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    today = date.today().isoformat()
    wb = openpyxl.Workbook()
    head_fill = PatternFill("solid", fgColor="1F3A5F")
    head_font = Font(bold=True, color="FFFFFF")
    late_fill = PatternFill("solid", fgColor="FDE2E1")

    def sheet(title, items, extra=None, note=""):
        ws = wb.create_sheet(title[:31])
        start = 1
        if note:
            ws.cell(row=1, column=1, value=note).font = Font(italic=True, color="555555")
            start = 3
        cols = list(COLS) + ([extra] if extra else [])
        for c, (h, w) in enumerate(cols, 1):
            cell = ws.cell(row=start, column=c, value=h)
            cell.fill, cell.font = head_fill, head_font
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            ws.column_dimensions[get_column_letter(c)].width = w
        for i, row in enumerate(items, start + 1):
            vals = row_values(row, today)
            if extra:
                vals.append(row.get("_extra") or "")
            for c, v in enumerate(vals, 1):
                ws.cell(row=i, column=c, value=v)
            late = vals[9]
            if isinstance(late, int) and late > 0:
                for c in range(1, len(vals) + 1):
                    ws.cell(row=i, column=c).fill = late_fill
        ws.freeze_panes = ws.cell(row=start + 1, column=6)
        if items:
            ws.auto_filter.ref = f"A{start}:{get_column_letter(len(cols))}{start + len(items)}"
        return ws

    by = {}
    for r in rows:
        by.setdefault(r["_bucket"], []).append(r)
    key = lambda r: (str(r.get("purchase_date") or ""), r["amazon_order_id"])  # noqa: E731

    # Summary
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Undelivered orders — " + " + ".join(r["account"] for r in ranges)
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Made {datetime.now():%d %b %Y %H:%M}. " + "  ·  ".join(
        f"{r['account']}: purchases {r['from']} → {r['to']}" for r in ranges)
    line = 4
    hdr = ["Zoro account", "Still undelivered", "…of which past Amazon deliver-by", "Already refunded by Zoro",
           "Cancelled", "Found (delivered now)", "Amazon already refunded the buyer (still undelivered)"]
    for c, h in enumerate(hdr, 1):
        cell = ws.cell(row=line, column=c, value=h)
        cell.fill, cell.font = head_fill, head_font
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(c)].width = 22 if c > 1 else 16
    ws.row_dimensions[line].height = 45
    for rng in ranges:
        line += 1
        mine = [r for r in rows if r.get("_acct") == rng["account"]]
        und = [r for r in mine if r["_bucket"] == "undelivered"]
        late = [r for r in und if (lambda d: d and d < today)(day_of(r.get("deliver_by_date")))]
        vals = [rng["account"], len(und), len(late), sum(r["_bucket"] == "refunded" for r in mine),
                sum(r["_bucket"] == "cancelled" for r in mine), sum(r["_bucket"] == "found" for r in mine),
                sum(bool(r.get("_amz_refund")) for r in und)]
        for c, v in enumerate(vals, 1):
            ws.cell(row=line, column=c, value=v)
    line += 2
    notes = [
        "How each order was checked before it was listed:",
        "• Found — Zoro's own order tracking was asked again for every order (the same lookup as the daily delivery "
        "check, which also updated the database). Delivered now → sheet 'Found (delivered)'.",
        "• Cancelled — Zoro's order status (asked live) or Amazon's order status.",
        "• Already refunded — Zoro 'Cash Refund' e-mails in the account's Gmail, matched by Zoro Sales Order number "
        "or the buyer's Amazon phone code. Amazon's refund to the buyer is a column only: Zoro still owes the parcel.",
        "• 'Account known from' says how the Zoro account is known; 'placer profile (guess)' means Zoro could not "
        "confirm it.",
        "• Pink rows are past Amazon's deliver-by date.",
    ]
    if stats.get("live") is None:
        notes.append("⚠️ Zoro was NOT asked this time (--no-live): statuses are the database's.")
    elif stats["live"].get("refused"):
        notes.append("⚠️ Zoro refused part of the lookups: some statuses are the database's (column 'Checked with Zoro').")
    else:
        lv = stats["live"]
        notes.append(f"Zoro answered {lv['answered']} of {lv['asked']} lookups ({lv['not_found']} not found under any "
                     f"of our e-mails, {lv['errors']} errors) in {lv.get('seconds', 0) // 60} min.")
    for acct, ok in refunds_ok.items():
        if ok is None:
            notes.append(f"⚠️ {acct}: Zoro refund e-mails were NOT read (no Gmail access) — check refunds by hand.")
        elif ok is False:
            notes.append(f"⚠️ {acct}: Zoro refund e-mails were not read this time (--no-gmail).")
        else:
            notes.append(f"{acct}: {ok} Zoro Cash Refund e-mail(s) read.")
    for t in notes:
        ws.cell(row=line, column=1, value=t)
        line += 1

    for rng in ranges:
        items = sorted([r for r in rows if r["_bucket"] == "undelivered" and r.get("_acct") == rng["account"]], key=key)
        sheet(f"{rng['account']} undelivered", items,
              note=f"{rng['account']} — purchases {rng['from']} → {rng['to']}, not delivered, not refunded by Zoro, "
                   f"not cancelled. {len(items)} order(s).")
    sheet("Already refunded (Zoro)", sorted(by.get("refunded", []), key=key),
          note="Not delivered, but Zoro has already refunded (Cash Refund e-mail matched to the order).")
    for r in by.get("cancelled", []):
        r["_extra"] = r.get("_cancelled_by") or ""
    sheet("Cancelled", sorted(by.get("cancelled", []), key=key), extra=("Cancelled by", 26),
          note="Cancelled at Zoro or on Amazon. 'Amazon (Zoro order Complete)' = Amazon cancelled but Zoro still shipped it.")
    for r in by.get("found", []):
        r["_extra"] = (r.get("_live") or {}).get("delivery_date") or r.get("delivery_date") or ""
    sheet("Found (delivered)", sorted(by.get("found", []), key=key), extra=("Delivered", 20),
          note="The database still had these as undelivered; Zoro's tracking now says delivered.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.xlsx"
    wb.save(tmp)
    os.replace(tmp, path)


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ranges", nargs="*", help="account:from[:to] — e.g. robstark771:2026-10-01:2026-11-12")
    ap.add_argument("--no-live", action="store_true", help="do not ask Zoro (statuses as the database has them)")
    ap.add_argument("--no-gmail", action="store_true", help="do not read Zoro Cash Refund e-mails")
    ap.add_argument("--no-write", action="store_true", help="ask Zoro but change nothing in the database")
    ap.add_argument("--out", default="", help="the .xlsx to write")
    ap.add_argument("--open", action="store_true", help="open the file when done (macOS)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N orders (a quick try)")
    a = ap.parse_args()

    ranges = parse_ranges(a.ranges)
    if not ranges:
        print("❌ no account to report on")
        return 1
    got, holder = acquire_lock(LOCK)
    if not got:
        print(f"❌ an undelivered report is already being made (pid {holder})")
        return 3
    try:
        for r in ranges:
            print(f"[report] {r['account']} ({r['email'] or 'no e-mail'}): purchases {r['from']} → {r['to']}")
        conn = sqlite3.connect(DB, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=60000")
        if a.no_write:
            conn.execute("PRAGMA query_only=ON")
        import zoro_delivery_check as Z
        learned = Z.learned_emails(conn)
        rows = load_candidates(conn, ranges, learned)
        if a.limit:
            rows = sorted(rows, key=lambda r: r["_day"])[:a.limit]
        print(f"[report] {len(rows)} order(s) not delivered in the database "
              f"({sum(r['_src'] == 'placer' for r in rows)} not in closing yet)")
        want_emails = {r["email"] for r in ranges if r["email"]}
        stats = {"live": None}
        if not a.no_live and rows:
            stats["live"] = live_check(conn, rows, want_emails, write=not a.no_write)
        # keep what is really ours, in its own date range
        rng_of = {r["account"]: r for r in ranges}
        rows = [r for r in rows if r.get("_acct") in rng_of and in_range(rng_of[r["_acct"]], r["_day"])]
        refunds_ok = {}
        if a.no_gmail:
            refunds_ok = {r["account"]: False for r in ranges}
            refunds = {}
        else:
            refunds = zoro_cash_refunds([r["account"] for r in ranges], min(r["from"] for r in ranges))
            refunds_ok = {k: (len(v) if v is not None else None) for k, v in refunds.items()}
        match_refunds(rows, refunds)
        amazon_facts(conn, rows)
        conn.close()
        for r in rows:
            r["_bucket"] = classify(r)
        out = a.out or os.path.join(OUT_DIR, f"undelivered_{'_'.join(r['account'] for r in ranges)}_"
                                             f"{datetime.now():%Y-%m-%d_%H%M}.xlsx")
        write_excel(out, ranges, rows, stats, refunds_ok)
        print("")
        for rng in ranges:
            mine = [r for r in rows if r.get("_acct") == rng["account"]]
            c = {b: sum(r["_bucket"] == b for r in mine) for b in ("undelivered", "refunded", "cancelled", "found")}
            print(f"[report] {rng['account']}: {c['undelivered']} still undelivered · {c['refunded']} already refunded "
                  f"by Zoro · {c['cancelled']} cancelled · {c['found']} found delivered")
        print(f"[report] ✅ saved {out}")
        if a.open and sys.platform == "darwin":
            subprocess.Popen(["open", out])
        return 0
    finally:
        release_lock(LOCK)


if __name__ == "__main__":
    sys.exit(main())
