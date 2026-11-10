#!/usr/bin/env python3
"""dup_check_local.py — deterministic duplicate check from LOCAL records.

Replaces the live-Gmail keyword search as the FIRST line of duplicate defence.
Why (2026-08-20, after the "-D" mislabel flood): the Gmail check searched by
customer NAME, took the first message Gmail ranked, verified only that the zip
appeared somewhere in the body, and grabbed the FIRST "WB…" string in it. With
many receipts matching (common names, repeat buyers, marketing mails) it kept
returning a WRONG WB, which the placer then stamped as "<wrong WB> - D" on the
order — 56 orders were found mislabelled this way, some hidden from placement
entirely.

This module answers the same question from records that cannot be confused:

  tier 0 — order_placer_orders: every order OUR fleet placed, written at the
           moment of placement with the full ship address. ZERO lag — catches a
           duplicate placed seconds ago by another profile (live Gmail could
           not: delivery + index lag, the root of the 2026-06/07 re-buy loop).
  tier 1 — archive_zoro: every Zoro receipt email, synced continuously by the
           launcher (≤10 min behind). Covers manual/external Zoro orders.

Matching is EXACT: normalized street AND 5-digit zip must both match — not
name similarity, not keyword relevance. Ties go to the most recent record and
every distinct WB found is reported, so multiple receipts mean visibility, not
confusion.

The caller (platforms/gmail.py) still falls back to live Gmail when these
records are missing AND the archive is stale, keeping fail-closed semantics.
"""
import json
import os
import re
import sqlite3
import time

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(_AUTO, "data", "automations_unified.db")
FETCH_STATE = os.path.join(_AUTO, "data", "gmail_fetch_state.json")
_WB = re.compile(r"\b(W[BW]\d{6,})\b")


def _norm_street(s):
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", "", (s or "").upper())).strip()


def _zip5(z):
    d = re.sub(r"\D", "", z or "")
    return d[:5]


def _clean_sku(sku):
    s = (sku or "").strip().upper()
    return re.sub(r"^(NEW_|HATH-)", "", s)


def _parse_ts(ts_str):
    if not ts_str:
        return 0.0
    s = str(ts_str).replace("T", " ").split(".")[0].strip()
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def archive_is_fresh(max_minutes=30):
    # 30 min, not 15: the stamp advances every ~10-20 min in practice (fetch cycle
    # + sync time), so a 15-min bar flapped stale and sent 288 needless Gmail
    # fallbacks in one 6-hour window (2026-08-21 audit). The zero-lag placer tier
    # covers every fleet order regardless; this bar only gates EXTERNAL-order
    # visibility, where 30 min matches what live Gmail's own lag used to be.
    """True when the Gmail archive was fetched+synced recently, so a local 'no
    duplicate' can be trusted without a live Gmail confirmation."""
    try:
        st = json.load(open(FETCH_STATE))
        age = time.time() - time.mktime(time.strptime(st["last_fetch"], "%Y-%m-%dT%H:%M:%S"))
        return 0 <= age <= max_minutes * 60
    except Exception:
        return False


_ZORO_CODE = re.compile(r"\bG\d{5,12}\b")


def _code_overlap(a, b):
    """Do these two Zoro-code sets share an item? A seller SKU can carry a cut-off code (G569554-D for
    G5695541), so one being the start of the other counts as the same item."""
    return any(x == y or x.startswith(y) or y.startswith(x) for x in a for y in b)


def different_zoro_codes(a, b):
    """Do these two texts name different items? True only with proof: Zoro codes on both sides, none shared."""
    codes_a, codes_b = (set(_ZORO_CODE.findall(str(t or "").upper())) for t in (a, b))
    return bool(codes_a and codes_b) and not _code_overlap(codes_a, codes_b)


def gmail_match_is_other_product(wb, current_asin=None, current_sku=None, current_amazon_id=None):
    """Did this WB, found in Gmail by name/address and ZIP, provably buy a DIFFERENT product?

    The Gmail check knows nothing about products, so a buyer's second order for something else was stamped
    "<WB> - D" and never bought: 17 orders from 1 to 2 Nov 2026, while the local check above already allowed
    them. This asks the same question of the WB's own records. True only with proof: the WB is on record for
    another Amazon order, and neither its ASINs nor its Zoro codes overlap this order's. A WB we have no record
    of, a shared item, a missing ASIN, a database error: all False, and the duplicate verdict stands."""
    wbs = _WB.findall(str(wb or ""))
    cur_asins = {a.upper() for a in re.findall(r"\bB0[A-Z0-9]{8}\b", str(current_asin or "").upper())}
    cur_codes = set(_ZORO_CODE.findall(str(current_sku or "").upper()))
    cur_aid = (current_amazon_id or "").strip()
    if len(wbs) != 1 or not (cur_asins or cur_codes):
        return False
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        try:
            placed = [r for r in c.execute(
                "SELECT wb_order_id, amazon_order_id, asin, sku FROM order_placer_orders WHERE wb_order_id LIKE ?",
                (f"%{wbs[0]}%",)) if wbs[0] in _WB.findall(r["wb_order_id"] or "")]
            receipts = [] if placed else [r for r in c.execute(
                "SELECT wb_order_id, sku FROM archive_zoro WHERE wb_order_id LIKE ?",
                (f"%{wbs[0]}%",)) if wbs[0] in _WB.findall(r["wb_order_id"] or "")]
        finally:
            c.close()
    except Exception as e:
        print(f"   [DUP_CHECK] could not look up {wbs[0]} ({e}) — the duplicate verdict stands")
        return False
    if placed:
        if cur_aid and any((r["amazon_order_id"] or "").strip() == cur_aid for r in placed):
            return False                      # this very order: an absolute duplicate
        rec_asins = {a for r in placed for a in re.findall(r"\bB0[A-Z0-9]{8}\b", (r["asin"] or "").upper())}
        rec_codes = {x for r in placed for x in _ZORO_CODE.findall((r["sku"] or "").upper())}
    elif receipts:
        rec_asins, rec_codes = set(), {x for r in receipts for x in _ZORO_CODE.findall((r["sku"] or "").upper())}
    else:
        return False                          # no record of this WB: nothing proves anything
    if not rec_asins and not rec_codes:
        return False
    if cur_asins & rec_asins or (cur_codes and rec_codes and _code_overlap(cur_codes, rec_codes)):
        return False
    return bool((cur_asins and rec_asins) or (cur_codes and rec_codes))


def check_address_local(street, zip_code, days=7, current_asin=None, current_sku=None,
                        current_amazon_id=None, repeat_threshold_hours=72.0, strict=False):
    """Exact-match duplicate lookup with smart repeat-customer & multi-item filtering.
    Returns
        {"wb": <most recent WB>, "source": ..., "when": ..., "all_wbs": [...]}
    or None when no record matches or when matches are safe repeat/multi-item orders. Never raises,
    unless strict=True: then a lookup error raises, for a caller to whom "could not look" and
    "nothing there" are different answers."""
    st, z5 = _norm_street(street), _zip5(zip_code)
    if not st or not z5:
        return None

    cur_asin = (current_asin or "").strip().upper()
    cur_sku = _clean_sku(current_sku)
    cur_aid = (current_amazon_id or "").strip()

    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        win = f"-{int(days)} day"
        matches = []
        for r in c.execute(
                "SELECT wb_order_id, amazon_order_id, asin, sku, ship_street, ship_postal_code AS zip, placed_at AS ts "
                "FROM order_placer_orders WHERE placed_at >= datetime('now', ?) "
                "AND COALESCE(wb_order_id,'')!=''", (win,)):
            if _norm_street(r["ship_street"]) == st and _zip5(r["zip"]) == z5:
                for wb in _WB.findall(r["wb_order_id"]):
                    matches.append({
                        "ts": r["ts"] or "",
                        "wb": wb,
                        "source": "placer-record",
                        "amazon_id": (r["amazon_order_id"] or "").strip(),
                        "asin": (r["asin"] or "").strip().upper(),
                        "sku": _clean_sku(r["sku"])
                    })
        for r in c.execute(
                "SELECT wb_order_id, sku, ship_street, ship_zip AS zip, email_timestamp AS ts "
                "FROM archive_zoro WHERE email_timestamp >= datetime('now', ?) "
                "AND COALESCE(wb_order_id,'')!=''", (win,)):
            if _norm_street(r["ship_street"]) == st and _zip5(r["zip"]) == z5:
                for wb in _WB.findall(r["wb_order_id"]):
                    matches.append({
                        "ts": r["ts"] or "",
                        "wb": wb,
                        "source": "zoro-receipt",
                        "amazon_id": "",
                        "asin": "",
                        "sku": _clean_sku(r["sku"])
                    })
        c.close()
    except Exception as e:
        if strict:
            raise
        print(f"   [DUP_LOCAL] lookup error ({e}) — treating as no local answer")
        return None

    if not matches:
        return None

    matches.sort(key=lambda m: m["ts"], reverse=True)  # newest first

    # 1. HARD GATE: Same Amazon Order ID is an absolute duplicate (never re-buy)
    if cur_aid:
        for m in matches:
            if m.get("amazon_id") and m["amazon_id"] == cur_aid:
                print(f"   🛑 [DUP_LOCAL] FATAL DUPLICATE: Amazon Order ID {cur_aid} was already placed as {m['wb']} at {m['ts']}")
                return {"wb": m["wb"], "source": m["source"], "when": m["ts"], "all_wbs": [m["wb"]], "reason": "same_amazon_order"}

    # 2. Check each match for repeat customer or multi-item purchases
    now = time.time()
    dup_candidates = []
    for m in matches:
        pts = _parse_ts(m["ts"])
        hours_ago = ((now - pts) / 3600.0) if pts > 0 else 0.0

        # Different product check:
        different_product = False
        if cur_asin and m.get("asin"):
            if cur_asin != m["asin"]:
                different_product = True
        elif cur_sku and m.get("sku"):
            # Compare the ITEMS, not the SKU text. In the Rush/Ultra batch check current_sku is
            # Amazon's seller SKU ("HO- G6723787") while a Zoro receipt carries the bare code
            # ("G6723787"). As text those never match, so every receipt at the same address read
            # "DIFFERENT product" and an unlocked order was bought a second time
            # (114-6240669-0408261: WB8146166479 + WB2913319477, 2 Nov 2026). Different only with
            # proof: Zoro codes on both sides and none shared. (Walmart: current_sku lists every
            # Zoro code the row can mean — same test.)
            if _ZORO_CODE.search(cur_sku) or _ZORO_CODE.search(str(m["sku"]).upper()):
                different_product = different_zoro_codes(cur_sku, m["sku"])
            else:
                different_product = cur_sku != m["sku"]   # no Zoro code on either side: the text is all there is

        if different_product:
            print(f"   ℹ️ [DUP_LOCAL] address match is for a DIFFERENT product "
                  f"(current: {cur_asin or cur_sku} vs previous: {m.get('asin') or m.get('sku')} on {m['wb']}) — not a duplicate")
            continue

        # Repeat customer check (> repeat_threshold_hours, e.g. 72h / 3 days):
        if hours_ago > repeat_threshold_hours:
            print(f"   ℹ️ [DUP_LOCAL] repeat customer: {m['wb']} was placed {hours_ago:.1f}h ago "
                  f"(> {repeat_threshold_hours:.0f}h) — legitimate repeat order, not an accidental duplicate")
            continue

        # Potential accidental duplicate (< 72h, same ASIN/SKU or unknown ASIN/SKU)
        dup_candidates.append(m)

    if not dup_candidates:
        print(f"   ✅ [DUP_LOCAL] address matched {len(matches)} previous order(s), but all are safe "
              f"(different products or placed > {repeat_threshold_hours:.0f}h ago) — ALLOWING PLACEMENT")
        return None

    top = dup_candidates[0]
    top_pts = _parse_ts(top["ts"])
    top_hours = ((now - top_pts) / 3600.0) if top_pts > 0 else 0.0
    print(f"   🔁 [DUP_LOCAL] DUPLICATE DETECTED: same product ordered {top_hours:.1f}h ago (WB: {top['wb']})")
    all_wbs = []
    for m in dup_candidates:
        if m["wb"] not in all_wbs:
            all_wbs.append(m["wb"])
    return {"wb": top["wb"], "source": top["source"], "when": top["ts"], "all_wbs": all_wbs}


# ── "That WB is on this buyer's OTHER order" (owner, 4 Nov 2026) ─────────────────────────────
# The check above matches by address, so a buyer's real repeat purchase matched the WB of their
# earlier order and was stamped "<WB> - D" and never bought (112-7422969-0994666, 3 Nov 2026, matched
# WB5917587551 of 112-9339236-7538642). Seller Central lists a buyer's orders with their Seller order
# IDs; a WB that sits on another of them does not prove THIS order was bought. These two functions
# only read text and records, so they can be checked without a browser.

_ORDER_ID_LINE = re.compile(r"^\d{3}-\d{7}-\d{7}$")
_SELLER_ID_LINE = re.compile(r"^Seller order ID:\s*(.*)$", re.I)
_PLAIN_LABEL = re.compile(r"^W[BW]\d{6,}(?:\s+W[BW]\d{6,})*(?:\s+\+\d+)?$")
_WB_ONLY = re.compile(r"^W[BW]\d{6,}$")
_ROW_CANCELLED = re.compile(r"\bcancell?ed\b|buyer cancellation|cancellation reason|requested cancel"
                            r"|requested that this order be cancel", re.I)
_ROW_LIVE = re.compile(r"\b(?:un)?shipped\b", re.I)


def parse_order_rows(page_text):
    """The rows of a Manage Orders page, from its innerText: one order id per line opens a row, the
    same way scrape_order_ids reads it. Returns [{"amazon_id", "label", "cancelled", "live"}]."""
    rows, by_id, cur, label_next = [], {}, None, False
    for raw in (page_text or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if _ORDER_ID_LINE.match(line):
            label_next = False
            cur = by_id.get(line)
            if cur is None:
                cur = by_id[line] = {"amazon_id": line, "label": "", "cancelled": False, "live": False}
                rows.append(cur)
            continue
        if cur is None:
            continue
        if label_next:
            label_next = False
            cur["label"] = re.sub(r"\s+", " ", line)
            continue
        m = _SELLER_ID_LINE.match(line)
        if m:
            value = re.sub(r"\s+", " ", m.group(1)).strip()
            if value:
                cur["label"] = value
            else:
                label_next = True
            continue
        if _ROW_CANCELLED.search(line):
            cur["cancelled"] = True
        if _ROW_LIVE.search(line):
            cur["live"] = True
    return rows


def placer_wbs_for_orders(amazon_ids):
    """{amazon_id: {WB, ...}} from order_placer_orders, written the moment an order is placed, so it
    knows a WB whose write-back to Amazon is still journaled. Raises on a database error."""
    ids = [a for a in dict.fromkeys(amazon_ids or []) if a]
    out = {}
    if not ids:
        return out
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    try:
        marks = ",".join("?" * len(ids))
        for aid, wb in c.execute(f"SELECT amazon_order_id, wb_order_id FROM order_placer_orders "
                                 f"WHERE amazon_order_id IN ({marks})", ids):
            found = _WB.findall(wb or "")
            if found:
                out.setdefault(aid, set()).update(found)
    finally:
        c.close()
    return out


def wbs_on_other_orders(matched_wbs, rows, current_amazon_id, recorded=None):
    """Is EVERY WB the duplicate check matched attached to another live order on this buyer's list?

    Returns (proved, why). One unaccounted WB and the duplicate stands, because that one may be this
    order's own purchase: bought as WB2 with the write-back lost, while WB1 sits on the buyer's other
    order. Attached means the row's label is nothing but WB ids, or the placer's record has that WB
    placed for that row. A "<WB> - D" label is a verdict, not a purchase: on its own it proves nothing,
    since a sibling can wear the WB of the very order being checked. A cancelled order proves nothing
    either: its WB may still be shipping to this buyer, so buying again is a person's call."""
    wbs = list(dict.fromkeys(matched_wbs or []))
    if not wbs:
        return False, "no WB id to check"
    for w in wbs:
        if not _WB_ONLY.match(str(w)):
            return False, f"a match with no clean WB id ({w})"
    recorded = recorded or {}
    by_id = {r["amazon_id"]: r for r in rows or []}
    me = by_id.get(current_amazon_id)
    if me is None:
        return False, "this order is not on the list, so it can't be trusted as this buyer's list"
    if len(by_id) < 2:
        return False, "the list shows no other order"
    own = set(_WB.findall(me.get("label") or "")) | set(recorded.get(current_amazon_id) or ())
    if own:
        return False, f"this very order already carries {sorted(own)[0]}"
    where = {}
    for w in wbs:
        for aid, r in by_id.items():
            if aid == current_amazon_id or r.get("cancelled") or not r.get("live"):
                continue
            label = (r.get("label") or "").strip()
            plain = set(_WB.findall(label)) if _PLAIN_LABEL.match(label) else set()
            if w in plain or w in (recorded.get(aid) or ()):
                where[w] = aid
                break
        else:
            return False, f"{w} is on none of this buyer's other live orders"
    return True, ", ".join(f"{w} is on {a}" for w, a in where.items())
