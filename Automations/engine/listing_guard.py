"""
listing_guard.py — a listing whose Zoro item has run out must not keep selling on Amazon.

10 Nov 2026 (owner): B00TY4AMZQ went out of stock on Zoro while the launcher was running. The placer
stamped every new order "OOS" and Amazon went on taking orders — HOANK had 950 unshipped "OOS" orders
by 10 Nov. The rule from now on, when the placer finds the main Zoro code AND every alternative out:

  • DS listing (somebody else's brand)  → take it off sale: Amazon quantity 0;
  • PL listing (brand HOANK / Hathoro)  → keep it on sale and raise its handling time instead.

PL or DS is read from the brand on the product page, not from a list ("just open the product and
check if HOANK or Hathoro"): first from what the repricer scrape already saw (utils/listing_watch.py),
else by opening the page in the placer's own Chrome, and only when neither answers from the SKU prefix.

How it runs, in two steps so that an order is never held up:
  1. note_out()        — called by the placer at the moment it stamps an order OOS. One small row in
                         listing_oos (unified DB). No browser, never raises.
  2. handle_pending()  — called by the placer at the END of a cycle, when its Chrome is idle. For each
                         new row: decide PL / DS, make the change on Seller Central
                         (change_on_amazon → engine/offer_edit.py), tell the Orders Alerts group what
                         was done, and put the Zoro code on the dashboard's Restock Watch so the team
                         hears when it is back.

Nothing puts a listing back when Zoro restocks: the Restock Watch alert is the cue, and the value
the listing had before (listing_oos.prev_value, also in the alert) is what to restore by hand.
A handling time is only ever raised here, never lowered; a quantity is only ever set to 0.

Settings — bot_config.json → "listing_guard" (all optional):
  enabled            true   false = the placer notes nothing and alerts nothing
  armed              true   false = alert only: nothing is changed on Seller Central
  pl_handling_days   7      handling time for an own-brand listing whose Zoro item is out (owner, 10 Nov 2026)
  realert_hours      24     a listing still taking OOS orders is announced again after this long
                            (2 hours when it was changed on Amazon — then something did not stick)
  max_per_cycle      3      listings handled at the end of one placer cycle (each takes about a minute)
"""
from __future__ import annotations

import os
import sqlite3
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(_AUTO, "data", "automations_unified.db")

_DDL = """
CREATE TABLE IF NOT EXISTS listing_oos (
    store         TEXT NOT NULL,
    asin          TEXT NOT NULL,
    seller_sku    TEXT,
    title         TEXT,
    zoro_code     TEXT,            -- the main Zoro code that is out
    alt_codes     TEXT,            -- the alternatives that were tried and are out too ("" = none listed)
    kind          TEXT,            -- PL | DS | '' (could not tell)
    brand         TEXT,
    kind_from     TEXT,            -- where the brand came from: repricer scrape / product page / SKU prefix
    orders        INTEGER DEFAULT 0,   -- orders stamped OOS for this listing since first_seen
    last_order    TEXT,
    first_seen    TEXT,
    last_seen     TEXT,
    status        TEXT DEFAULT 'new',  -- new → alerted | changed | failed
    action        TEXT,            -- qty0 | handling
    action_at     TEXT,
    note          TEXT,
    prev_value    TEXT,            -- what the field held before the change (to put back after the restock)
    PRIMARY KEY (store, asin)
)"""

_DEFAULTS = {"enabled": True, "armed": True, "pl_handling_days": 7, "realert_hours": 24, "max_per_cycle": 3}
REALERT_AFTER_CHANGE_HOURS = 2      # orders still arriving this long after the change: it did not stick
_MISSED: List[Dict[str, Any]] = []        # notes the database refused (busy) — written at the next chance


def settings() -> Dict[str, Any]:
    cfg = dict(_DEFAULTS)
    try:
        from utils.config import load_gui_config
        raw = load_gui_config().get("listing_guard") or {}
        for k, d in _DEFAULTS.items():
            if raw.get(k) in (None, ""):
                continue
            cfg[k] = (raw[k] if isinstance(raw[k], bool) else str(raw[k]).strip().lower() in ("1", "true", "yes", "on")) \
                if isinstance(d, bool) else int(float(raw[k]))
    except Exception:
        pass
    return cfg


def _conn(timeout: float = 10.0) -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=timeout)
    c.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    c.row_factory = sqlite3.Row
    c.execute(_DDL)
    if "prev_value" not in {r[1] for r in c.execute("PRAGMA table_info(listing_oos)")}:
        c.execute("ALTER TABLE listing_oos ADD COLUMN prev_value TEXT")
    return c


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _hours_since(ts: Optional[str]) -> float:
    try:
        return (time.time() - time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S"))) / 3600.0
    except Exception:
        return 1e9


# ── step 1: the placer says "this listing is out" ──────────────────────────────────────────────
def _write_note(ev: Dict[str, Any], realert_hours: int) -> None:
    c = _conn()
    try:
        with c:
            row = c.execute("SELECT status, action_at FROM listing_oos WHERE store=? AND asin=?",
                            (ev["store"], ev["asin"])).fetchone()
            held = ev.get("held_now")                 # Ultra mode: orders held right now, not one more order
            if row is None:
                c.execute("INSERT INTO listing_oos (store, asin, seller_sku, title, zoro_code, alt_codes, orders, "
                          "last_order, first_seen, last_seen, status) VALUES (?,?,?,?,?,?,?,?,?,?, 'new')",
                          (ev["store"], ev["asin"], ev["seller_sku"], ev["title"], ev["zoro_code"], ev["alt_codes"],
                           int(held or 1), ev["amazon_id"], ev["at"], ev["at"]))
                return
            # Known listing: count the order. Announce it again only after realert_hours — a listing
            # that still takes orders a day later has not been dealt with.
            wait_h = REALERT_AFTER_CHANGE_HOURS if row["status"] == "changed" else realert_hours
            again = row["status"] != "new" and _hours_since(row["action_at"]) >= wait_h
            c.execute("UPDATE listing_oos SET orders = " + ("MAX(orders, ?)" if held else "orders + ?")
                      + ", last_order = COALESCE(NULLIF(?, ''), last_order), last_seen=?, "
                      "seller_sku = COALESCE(NULLIF(?, ''), seller_sku), title = COALESCE(NULLIF(?, ''), title), "
                      "zoro_code=?, alt_codes=?" + (", status='new'" if again else "") + " WHERE store=? AND asin=?",
                      (int(held or 1), ev["amazon_id"], ev["at"], ev["seller_sku"], ev["title"], ev["zoro_code"],
                       ev["alt_codes"], ev["store"], ev["asin"]))
    finally:
        c.close()


def note_out(store: str, asin: str, seller_sku: str = "", zoro_code: str = "", alt_codes=None,
             amazon_id: str = "", title: str = "", held_now: Optional[int] = None) -> bool:
    """The placer found this listing's Zoro code and every alternative out of stock. Never raises.
    held_now (Ultra mode, where the same held orders are met every cycle): how many orders wait now."""
    try:
        cfg = settings()
        asin = str(asin or "").strip().upper()
        if not cfg["enabled"] or not asin or "," in asin:        # a multi-item order names several ASINs
            return False
        ev = {"store": store, "asin": asin, "seller_sku": str(seller_sku or "").strip(),
              "title": str(title or "").strip()[:160], "zoro_code": str(zoro_code or "").strip(),
              "alt_codes": ", ".join(str(a).strip() for a in (alt_codes or []) if str(a).strip()),
              "amazon_id": str(amazon_id or "").strip(), "at": _now(), "held_now": held_now}
        try:
            _write_note(ev, cfg["realert_hours"])
        except sqlite3.Error as e:
            _MISSED.append(ev)                                    # database busy: keep it for the cycle end
            print(f"  ⚠️  listing guard: note kept in memory ({type(e).__name__})")
        return True
    except Exception as e:
        print(f"  ⚠️  listing guard: {type(e).__name__}: {e}")
        return False


# ── step 2: PL or DS, tell the team, watch for the restock ─────────────────────────────────────
def _brand_from_page(driver, asin: str) -> Optional[str]:
    """Open the product page in the placer's Chrome and read its brand line. None if it cannot be read
    (Amazon's "continue shopping" check, a page that did not load) — the caller then asks the SKU."""
    try:
        driver.get(f"https://www.amazon.com/dp/{asin}")
        end = time.time() + 8
        while time.time() < end:
            txt = driver.execute_script(
                "var e = document.querySelector('#bylineInfo'); return e ? (e.innerText || e.textContent || '') : '';") or ""
            if txt.strip():
                import re
                t = " ".join(txt.split())
                m = re.search(r"Visit the (.+?) Store\b", t, re.I) or re.search(r"Brand\s*:\s*(.+)", t, re.I)
                return (m.group(1) if m else t).strip()[:80]
            time.sleep(0.5)
    except Exception:
        pass
    return None


def classify(driver, store: str, asin: str, seller_sku: str) -> Tuple[str, str, str]:
    """('PL' | 'DS' | '', brand, where the answer came from)."""
    from utils import listing_watch as lw
    brand, src = None, ""
    try:
        seen = (lw._load().get(store) or {}).get(asin) or {}
        if seen.get("brand"):
            brand, src = str(seen["brand"]), "repricer scrape"
    except Exception:
        pass
    if not brand and driver is not None:
        brand = _brand_from_page(driver, asin)
        src = "product page" if brand else ""
    if brand:
        return ("PL" if lw.brand_is_ours(brand) else "DS"), brand, src
    if seller_sku:
        return ("PL" if lw.sku_is_pl(seller_sku, store) else "DS"), "", "SKU prefix (brand not readable)"
    return "", "", "no brand and no SKU"


def listing_url(asin: str) -> str:
    return ("https://sellercentral.amazon.com/myinventory/inventory?fulfilledBy=all&page=1&pageSize=25"
            f"&searchField=all&searchTerm={urllib.parse.quote(asin)}&sort=date_created_desc&status=all")


def change_on_amazon(driver, store: str, row: Dict[str, Any], action: str, profile: str = "") -> Tuple[bool, str, str]:
    """Set the quantity to 0 (action 'qty0') or raise the handling time ('handling') on Seller Central,
    in the placer's own Chrome. Returns (done, what happened, previous value). done is True when Amazon
    now holds the wanted value — changed just now, or already there. See engine/offer_edit.py for how
    the page is driven and what was tried on the live page on 10 Nov 2026."""
    cfg = settings()
    if not cfg["armed"]:
        return False, "the automatic change is switched off (listing_guard.armed)", ""
    if driver is None:
        return False, "no browser to make the change with", ""
    from engine import offer_edit
    if action == "qty0":
        res = offer_edit.set_offer_field(driver, row["asin"], row.get("seller_sku") or "", "quantity", 0,
                                         login_profile=profile or None)
    else:
        res = offer_edit.set_offer_field(driver, row["asin"], row.get("seller_sku") or "", "handling_days",
                                         int(cfg["pl_handling_days"]), login_profile=profile or None)
    # the previous value is kept only when something was really changed — "it was already 0" must not
    # overwrite the number the listing had before an earlier change
    return bool(res.get("ok")), str(res.get("note") or ""), (str(res.get("previous") or "") if res.get("changed") else "")


def _watch_restock(row: Dict[str, Any]) -> bool:
    """Put the Zoro code on the dashboard's Restock Watch (zoro_watch): its hourly check raises an
    Insights alert when the item is back in stock, which is when the listing can go back on sale."""
    code = str(row.get("zoro_code") or "").strip().upper()
    if not code:
        return False
    try:
        c = _conn()
        try:
            with c:
                c.execute("CREATE TABLE IF NOT EXISTS zoro_watch(zoro_code TEXT PRIMARY KEY, label TEXT, "
                          "added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_availability TEXT, last_lead_days INTEGER, "
                          "last_lead_text TEXT, last_checked TIMESTAMP, alert TEXT, alert_at TIMESTAMP)")
                c.execute("INSERT OR IGNORE INTO zoro_watch(zoro_code, label) VALUES (?, ?)",
                          (code, f"out of stock — {row['store']} {row['asin']} {row.get('seller_sku') or ''}".strip()))
        finally:
            c.close()
        return True
    except Exception:
        return False


def _message(row: Dict[str, Any], kind: str, brand: str, src: str, action: str, changed: bool, why: str,
             watching: bool) -> str:
    title = (row.get("title") or "").strip()
    alts = (row.get("alt_codes") or "").strip()
    want = {"qty0": "set the Amazon quantity to 0", "handling": "raise the handling time (do NOT set it to 0 — own brand)"}
    if changed:
        head = "taken off sale" if action == "qty0" else "handling time raised"
    else:
        head = "still LIVE on Amazon"
    lines = [f"🛑 *Zoro is out of stock — {row['store']} listing {head}*"]
    if title:
        lines.append(f"• {title[:80]}")
    lines.append(f"• ASIN `{row['asin']}`" + (f" · SKU `{row['seller_sku']}`" if row.get("seller_sku") else ""))
    lines.append(f"• Zoro `{row.get('zoro_code') or '?'}` is out"
                 + (f"; alternatives also out: {alts}" if alts else "; no alternative in the sheet"))
    lines.append("• Listing: " + ({"PL": "own brand (PL)", "DS": "DS"}.get(kind, "could not tell PL from DS"))
                 + (f" — brand “{brand}”" if brand else "") + (f" ({src})" if src else ""))
    lines.append(f"• Orders stamped OOS so far: {row.get('orders') or 1}")
    if changed:
        lines.append(f"• ✅ On Amazon: {why}")
        lines.append("• When Zoro has it again: put the " + ("quantity" if action == "qty0" else "handling time")
                     + " back by hand — nothing does that automatically")
    elif action:
        lines.append(f"• ⚠️ NOT changed on Amazon — {why}")
        lines.append(f"• To do by hand: {want[action]}")
        lines.append(f"  {listing_url(row['asin'])}")
    else:
        lines.append(f"• ⚠️ Check it by hand: {listing_url(row['asin'])}")
    if watching:
        lines.append("• Restock Watch: added — the dashboard says when Zoro has it again")
    return "\n".join(lines)


def handle_pending(driver, store: str, profile: str = ""):
    """End of a placer cycle: deal with this store's listings noted since the last time. Returns the driver."""
    try:
        cfg = settings()
        if not cfg["enabled"]:
            return driver
        while _MISSED:                                   # notes the database was too busy to take
            ev = _MISSED.pop(0)
            try:
                _write_note(ev, cfg["realert_hours"])
            except sqlite3.Error:
                _MISSED.insert(0, ev)
                break
        c = _conn()
        try:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM listing_oos WHERE store=? AND status='new' ORDER BY first_seen LIMIT ?",
                (store, int(cfg["max_per_cycle"])))]
        finally:
            c.close()
        for row in rows:
            kind, brand, src = classify(driver, store, row["asin"], row.get("seller_sku") or "")
            action = {"PL": "handling", "DS": "qty0"}.get(kind, "")
            changed, why, prev = False, "PL or DS could not be decided", ""
            if action:
                try:
                    changed, why, prev = change_on_amazon(driver, store, row, action, profile)
                except Exception as e:
                    changed, why, prev = False, f"{type(e).__name__}: {str(e)[:120]}", ""
            watching = _watch_restock(row)
            text = _message(row, kind, brand, src, action, changed, why, watching)
            print(f"  🛑 LISTING OUT AT ZORO: {row['asin']} ({row.get('seller_sku') or '?'}) — "
                  f"{kind or '?'}{' / ' + brand if brand else ''} — "
                  + (f"changed on Amazon: {why}" if changed else f"NOT changed on Amazon ({why})"))
            try:
                from utils.whatsapp import get_whatsapp_group, send_whatsapp_message
                send_whatsapp_message(text, to=get_whatsapp_group(store, kind="orders"))
            except Exception as e:
                print(f"  ⚠️  listing guard alert not sent: {type(e).__name__}: {e}")
            c = _conn()
            try:
                with c:
                    c.execute("UPDATE listing_oos SET kind=?, brand=?, kind_from=?, action=?, status=?, action_at=?, "
                              "note=?, prev_value = COALESCE(NULLIF(?, ''), prev_value) WHERE store=? AND asin=?",
                              (kind, brand, src, action, "changed" if changed else "alerted", _now(), why[:300],
                               prev, store, row["asin"]))
            finally:
                c.close()
    except Exception as e:                               # never in the way of the next cycle
        print(f"  ⚠️  listing guard: {type(e).__name__}: {e}")
    return driver


if __name__ == "__main__":      # python -m engine.listing_guard  → the list, newest first (read-only)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        for r in con.execute("SELECT store, asin, seller_sku, kind, brand, orders, status, action, prev_value, first_seen, last_seen, note "
                             "FROM listing_oos ORDER BY last_seen DESC LIMIT 50"):
            print(" | ".join(str(x if x is not None else "") for x in r))
    except sqlite3.OperationalError:
        print("listing_oos is empty: no listing has been noted yet")
    finally:
        con.close()
