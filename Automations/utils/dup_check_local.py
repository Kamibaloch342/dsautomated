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


def check_address_local(street, zip_code, days=7):
    """Exact-match duplicate lookup. Returns
        {"wb": <most recent WB>, "source": ..., "when": ..., "all_wbs": [...]}
    or None when no record matches. Never raises."""
    st, z5 = _norm_street(street), _zip5(zip_code)
    if not st or not z5:
        return None
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        win = f"-{int(days)} day"
        matches = []
        for r in c.execute(
                "SELECT wb_order_id, ship_street, ship_postal_code AS zip, placed_at AS ts "
                "FROM order_placer_orders WHERE placed_at >= datetime('now', ?) "
                "AND COALESCE(wb_order_id,'')!=''", (win,)):
            if _norm_street(r["ship_street"]) == st and _zip5(r["zip"]) == z5:
                for wb in _WB.findall(r["wb_order_id"]):
                    matches.append((r["ts"] or "", wb, "placer-record"))
        for r in c.execute(
                "SELECT wb_order_id, ship_street, ship_zip AS zip, email_timestamp AS ts "
                "FROM archive_zoro WHERE email_timestamp >= datetime('now', ?) "
                "AND COALESCE(wb_order_id,'')!=''", (win,)):
            if _norm_street(r["ship_street"]) == st and _zip5(r["zip"]) == z5:
                for wb in _WB.findall(r["wb_order_id"]):
                    matches.append((r["ts"] or "", wb, "zoro-receipt"))
        c.close()
    except Exception as e:
        print(f"   [DUP_LOCAL] lookup error ({e}) — treating as no local answer")
        return None
    if not matches:
        return None
    matches.sort(reverse=True)                     # newest first
    ts, wb, src = matches[0]
    all_wbs = []
    for _, w, _s in matches:
        if w not in all_wbs:
            all_wbs.append(w)
    return {"wb": wb, "source": src, "when": ts, "all_wbs": all_wbs}
