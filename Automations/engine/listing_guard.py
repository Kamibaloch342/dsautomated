"""
listing_guard.py — a listing whose Zoro item has run out must not keep selling on Amazon,
and must come back when Zoro has the item again.

10 Nov 2026 (owner): B00TY4AMZQ went out of stock on Zoro while the launcher was running. The placer
stamped every new order "OOS" and Amazon went on taking orders — HOANK had 950 unshipped "OOS" orders
by 10 Nov. The rule from now on, when the placer finds the main Zoro code AND every alternative out:

  • DS listing (somebody else's brand)  → take it off sale: Amazon quantity 0;
  • PL listing (brand HOANK / Hathoro)  → keep it on sale and raise its handling time instead.

PL or DS is read from the brand on the product page, not from a list ("just open the product and
check if HOANK or Hathoro"): first from what the repricer scrape already saw (utils/listing_watch.py),
else by opening the page in the placer's own Chrome, and only when neither answers from the SKU prefix.

How it runs, so that an order is never held up:
  1. note_out()        — called by the placer at the moment it stamps an order OOS. One small row in
                         listing_oos (unified DB). No browser, never raises.
  2. handle_pending()  — called by the placer at the END of a cycle, when its Chrome is idle:
       a. each new row: decide PL / DS, make the change on Seller Central (engine/offer_edit.py),
          say what was done, put the Zoro code on the dashboard's Restock Watch. A handling-time
          change goes to the store's Insights group only (owner, 11 Nov 2026); a listing taken off
          sale, or one the placer could not decide on, to the Orders Alerts group as well;
       b. each listing changed earlier: every restock_check_minutes ask Zoro whether the main code or
          an alternative is buyable again, and if so put the listing back — the quantity it had, or
          the handling time it had (owner, 10 Nov 2026: "Yes, build it").

What the restore will and will not do:
  • it only undoes what the guard itself did. A listing that was already at quantity 0, or whose
    handling time was already long, is announced ("Zoro has it again") and left as it is;
  • it acts only while the field still holds what the guard set (quantity 0 / the raised handling
    time). If a person changed it in between, the listing is left alone and the row is closed;
  • "in stock again" means buyable for restock_depth units of the listing, not for one: a single unit
    back on Zoro's shelf would put the listing on sale for one order and off again;
  • a handling time that was empty before goes back to the store's own default, read from Seller
    Central's shipping settings — emptying the field does nothing on Amazon;
  • a restore that fails is announced once and then left to a person. It is not retried in a loop.

Two placers of one store (COUPON and RESELLER) both call handle_pending(): a row is taken with one
atomic UPDATE (status 'new' → 'working', 'changed' → 'restoring'), so only one of them handles it.

Every change is also one row in listing_events, which is what the dashboard's Insights tab shows.

Settings — bot_config.json → "listing_guard" (all optional):
  enabled               true   false = the placer notes nothing and alerts nothing
  armed                 true   false = alert only: nothing is changed on Seller Central
  pl_handling_days      7      handling time for an own-brand listing whose Zoro item is out (owner, 10 Nov 2026)
  restore               true   false = a restock is not acted on (the Restock Watch alert still comes)
  restock_check_minutes 30     how often a changed listing's Zoro stock is asked
  restock_depth         3      units of the listing that must be buyable before it goes back on sale
  realert_hours         24     a listing still taking OOS orders is looked at again after this long
                               (2 hours when its quantity was set to 0 — then the change may not have
                               stuck). A listing that still holds what the guard set is not announced
                               again; one that could not be changed is.
  max_per_cycle         3      listings changed or restored at the end of one placer cycle (about a minute each)
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
    status        TEXT DEFAULT 'new',  -- new | recheck → working → changed | alerted → restoring → restored | restock_told
    action        TEXT,            -- qty0 | handling
    action_at     TEXT,
    note          TEXT,
    prev_value    TEXT,            -- what the field held before the guard changed it (what a restore puts back)
    changed_to    TEXT,            -- what the guard set ('' = it changed nothing: the value was already there)
    unit_qty      INTEGER DEFAULT 1,   -- Zoro units behind ONE unit of the listing (a 25-pack listing: 25)
    restock_checked_at TEXT,
    restored_at   TEXT,
    PRIMARY KEY (store, asin)
)"""
_LATER_COLUMNS = {"prev_value": "TEXT", "changed_to": "TEXT", "unit_qty": "INTEGER DEFAULT 1",
                  "restock_checked_at": "TEXT", "restored_at": "TEXT"}

_DEFAULTS = {"enabled": True, "armed": True, "pl_handling_days": 7, "restore": True, "restock_check_minutes": 30,
             "restock_depth": 3, "realert_hours": 24, "max_per_cycle": 3}
REALERT_AFTER_CHANGE_HOURS = 2      # quantity set to 0 and orders still arriving this long after: look again
STALE_CLAIM_MINUTES = 20            # a row left 'working' / 'restoring' by a placer that died goes back
RESTOCK_CHECKS_PER_CYCLE = 10
_CLOSED = ("restored", "restock_told")   # done with: the next "out of stock" starts the listing afresh
_UNKNOWN = ("API_ERROR", "NO_DATA")
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
    have = {r[1] for r in c.execute("PRAGMA table_info(listing_oos)")}
    for col, typ in _LATER_COLUMNS.items():
        if col not in have:
            c.execute(f"ALTER TABLE listing_oos ADD COLUMN {col} {typ}")
    return c


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ago(minutes: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - minutes * 60))


def _hours_since(ts: Optional[str]) -> float:
    try:
        return (time.time() - time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S"))) / 3600.0
    except Exception:
        return 1e9


def _update(store: str, asin: str, **cols) -> None:
    c = _conn()
    try:
        with c:
            c.execute(f"UPDATE listing_oos SET {', '.join(k + '=?' for k in cols)} WHERE store=? AND asin=?",
                      list(cols.values()) + [store, asin])
    finally:
        c.close()


def _claim(store: str, asin: str, from_status: str, to_status: str) -> bool:
    """Take a row for this placer: one UPDATE that only one process can win."""
    c = _conn()
    try:
        with c:
            return c.execute("UPDATE listing_oos SET status=?, action_at=? WHERE store=? AND asin=? AND status=?",
                             (to_status, _now(), store, asin, from_status)).rowcount == 1
    finally:
        c.close()


# ── step 1: the placer says "this listing is out" ──────────────────────────────────────────────
def _write_note(ev: Dict[str, Any], realert_hours: int) -> None:
    c = _conn()
    try:
        with c:
            row = c.execute("SELECT status, action_at, action FROM listing_oos WHERE store=? AND asin=?",
                            (ev["store"], ev["asin"])).fetchone()
            held = ev.get("held_now")                 # Ultra mode: orders held right now, not one more order
            if row is None:
                c.execute("INSERT INTO listing_oos (store, asin, seller_sku, title, zoro_code, alt_codes, orders, "
                          "last_order, first_seen, last_seen, unit_qty, status) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'new')",
                          (ev["store"], ev["asin"], ev["seller_sku"], ev["title"], ev["zoro_code"], ev["alt_codes"],
                           int(held or 1), ev["amazon_id"], ev["at"], ev["at"], ev["unit_qty"]))
                return
            # Known listing: count the order. It is dealt with again
            #   • at once when it had been put back (or closed) and Zoro is out once more;
            #   • after 2 hours when its quantity was set to 0 and it still takes orders — looked at
            #     again ('recheck'), because then the change may not have stuck;
            #   • after realert_hours when its handling time was raised: an own-brand listing stays on
            #     sale on purpose, so orders go on arriving and say nothing (11 Nov 2026: two listings
            #     were announced again every 2–3 hours, 6 repeats in one night, each to two groups);
            #   • after realert_hours when it was only announced.
            # A 'recheck' sends a message only if something had to be changed again (_handle_new).
            st = row["status"]
            if st in _CLOSED:
                again, fresh = "new", ", prev_value=NULL, changed_to=NULL, restock_checked_at=NULL, restored_at=NULL"
            else:
                wait_h = (REALERT_AFTER_CHANGE_HOURS if st == "changed" and (row["action"] or "") != "handling"
                          else realert_hours)
                due = st in ("changed", "alerted") and _hours_since(row["action_at"]) >= wait_h
                again, fresh = ({"changed": "recheck", "alerted": "new"}[st] if due else ""), ""
            c.execute("UPDATE listing_oos SET last_order = COALESCE(NULLIF(?, ''), last_order), last_seen=?, "
                      "seller_sku = COALESCE(NULLIF(?, ''), seller_sku), title = COALESCE(NULLIF(?, ''), title), "
                      "zoro_code=?, alt_codes=?, unit_qty=?" + (f", status='{again}'" if again else "") + fresh
                      + ", orders = " + ("?" if st in _CLOSED else "MAX(orders, ?)" if held else "orders + ?")
                      + " WHERE store=? AND asin=?",
                      (ev["amazon_id"], ev["at"], ev["seller_sku"], ev["title"], ev["zoro_code"], ev["alt_codes"],
                       ev["unit_qty"], int(held or 1), ev["store"], ev["asin"]))
    finally:
        c.close()


def note_out(store: str, asin: str, seller_sku: str = "", zoro_code: str = "", alt_codes=None,
             amazon_id: str = "", title: str = "", held_now: Optional[int] = None, unit_qty: int = 1) -> bool:
    """The placer found this listing's Zoro code and every alternative out of stock. Never raises.
    held_now (Ultra mode, where the same held orders are met every cycle): how many orders wait now.
    unit_qty: Zoro units one unit of the listing needs — the restock check asks for a multiple of it."""
    try:
        cfg = settings()
        asin = str(asin or "").strip().upper()
        if not cfg["enabled"] or not asin or "," in asin:        # a multi-item order names several ASINs
            return False
        try:
            unit = max(1, int(unit_qty or 1))
        except (TypeError, ValueError):
            unit = 1
        ev = {"store": store, "asin": asin, "seller_sku": str(seller_sku or "").strip(),
              "title": str(title or "").strip()[:160], "zoro_code": str(zoro_code or "").strip(),
              "alt_codes": ", ".join(str(a).strip() for a in (alt_codes or []) if str(a).strip()),
              "amazon_id": str(amazon_id or "").strip(), "at": _now(), "held_now": held_now, "unit_qty": unit}
        try:
            _write_note(ev, cfg["realert_hours"])
        except sqlite3.Error as e:
            _MISSED.append(ev)                                    # database busy: keep it for the cycle end
            print(f"  ⚠️  listing guard: note kept in memory ({type(e).__name__})")
        return True
    except Exception as e:
        print(f"  ⚠️  listing guard: {type(e).__name__}: {e}")
        return False


# ── step 2a: PL or DS, change it on Amazon, tell the team ──────────────────────────────────────
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


def change_on_amazon(driver, store: str, row: Dict[str, Any], action: str, profile: str = "") -> Dict[str, Any]:
    """Set the quantity to 0 (action 'qty0') or raise the handling time ('handling') on Seller Central,
    in the placer's own Chrome. Returns {'done', 'changed', 'note', 'previous', 'set_to'}: done is True
    when Amazon now holds the wanted value; changed only when the guard itself just set it. See
    engine/offer_edit.py for how the page is driven and what was tried on the live page."""
    out = {"done": False, "changed": False, "note": "", "previous": "", "set_to": ""}
    cfg = settings()
    if not cfg["armed"]:
        out["note"] = "the automatic change is switched off (listing_guard.armed)"
        return out
    if driver is None:
        out["note"] = "no browser to make the change with"
        return out
    from engine import offer_edit
    field, value = ("quantity", 0) if action == "qty0" else ("handling_days", int(cfg["pl_handling_days"]))
    res = offer_edit.set_offer_field(driver, row["asin"], row.get("seller_sku") or "", field, value,
                                     login_profile=profile or None)
    out.update(done=bool(res.get("ok")), changed=bool(res.get("changed")), note=str(res.get("note") or ""))
    if out["changed"]:          # only a real change is something to undo later
        out.update(previous=str(res.get("previous") or ""), set_to=str(value))
    return out


def _watch_restock(row: Dict[str, Any], remove: bool = False) -> bool:
    """Put the Zoro code on the dashboard's Restock Watch (zoro_watch), or take our entry off again."""
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
                if remove:
                    c.execute("DELETE FROM zoro_watch WHERE zoro_code=? AND label LIKE 'out of stock —%'", (code,))
                else:
                    c.execute("INSERT OR IGNORE INTO zoro_watch(zoro_code, label) VALUES (?, ?)",
                              (code, f"out of stock — {row['store']} {row['asin']} {row.get('seller_sku') or ''}".strip()))
        finally:
            c.close()
        return True
    except Exception:
        return False


def _groups_for(action: str) -> Tuple[str, ...]:
    """Which WhatsApp groups a listing's messages go to. A handling-time change (own-brand listing)
    is an Insights matter only (owner, 11 Nov 2026); a listing taken off sale, or one the placer could
    not decide on, also goes to the store's Orders Alerts group."""
    return ("insights",) if action == "handling" else ("orders", "insights")


def _tell(store: str, text: str, row: Dict[str, Any], kind: str, short: str,
          to: Tuple[str, ...] = ("orders", "insights")) -> None:
    """One message to each of the store's groups named in *to*, and one row for the dashboard's
    Insights tab (utils/listing_events.py)."""
    try:
        from utils.whatsapp import get_whatsapp_group, send_whatsapp_message
        groups = []
        for which in to:
            g = get_whatsapp_group(store, kind=which)
            if g and g not in groups:
                groups.append(g)
        for g in groups:
            send_whatsapp_message(text, to=g)
    except Exception as e:
        print(f"  ⚠️  listing guard alert not sent: {type(e).__name__}: {e}")
    try:
        from utils.listing_events import record
        record(store, row["asin"], kind, short, sku=row.get("seller_sku") or "", title=row.get("title") or "", db_path=DB)
    except Exception:
        pass


def _head(row: Dict[str, Any]) -> List[str]:
    lines = []
    if (row.get("title") or "").strip():
        lines.append(f"• {row['title'].strip()[:80]}")
    lines.append(f"• ASIN `{row['asin']}`" + (f" · SKU `{row['seller_sku']}`" if row.get("seller_sku") else ""))
    return lines


def _message(row: Dict[str, Any], kind: str, brand: str, src: str, action: str, res: Dict[str, Any],
             watching: bool, restore_on: bool) -> str:
    alts = (row.get("alt_codes") or "").strip()
    want = {"qty0": "set the Amazon quantity to 0", "handling": "raise the handling time (do NOT set it to 0 — own brand)"}
    done, why = res.get("done"), res.get("note") or ""
    head = ("taken off sale" if action == "qty0" else "handling time raised") if done else "still LIVE on Amazon"
    lines = [f"🛑 *Zoro is out of stock — {row['store']} listing {head}*"] + _head(row)
    lines.append(f"• Zoro `{row.get('zoro_code') or '?'}` is out"
                 + (f"; alternatives also out: {alts}" if alts else "; no alternative in the sheet"))
    lines.append("• Listing: " + ({"PL": "own brand (PL)", "DS": "DS"}.get(kind, "could not tell PL from DS"))
                 + (f" — brand “{brand}”" if brand else "") + (f" ({src})" if src else ""))
    lines.append(f"• Orders stamped OOS so far: {row.get('orders') or 1}")
    if done:
        lines.append(f"• ✅ On Amazon: {why}")
        if not res.get("changed"):
            lines.append("• The placer changed nothing here, so at the restock you are told and nothing is put back")
        elif restore_on:
            lines.append("• When Zoro has it again the placer puts it back by itself"
                         + (f" (to {res['previous']})" if res.get("previous") else ""))
        else:
            lines.append("• When Zoro has it again: put it back by hand (automatic restore is switched off)")
    elif action:
        lines.append(f"• ⚠️ NOT changed on Amazon — {why}")
        lines.append(f"• To do by hand: {want[action]}")
        lines.append(f"  {listing_url(row['asin'])}")
    else:
        lines.append(f"• ⚠️ Check it by hand: {listing_url(row['asin'])}")
    if watching:
        lines.append("• Restock Watch: added")
    return "\n".join(lines)


def _handle_new(driver, store: str, profile: str, cfg: Dict[str, Any], row: Dict[str, Any],
                recheck: bool = False) -> None:
    """recheck: the listing was changed earlier and orders went on arriving, so Seller Central is
    looked at again. If it still holds what was set, nothing is said: only the launcher pane shows it."""
    kind, brand, src = classify(driver, store, row["asin"], row.get("seller_sku") or "")
    action = {"PL": "handling", "DS": "qty0"}.get(kind, "")
    res: Dict[str, Any] = {"done": False, "changed": False, "note": "PL or DS could not be decided", "previous": "", "set_to": ""}
    if action:
        try:
            res = change_on_amazon(driver, store, row, action, profile)
        except Exception as e:
            res["note"] = f"{type(e).__name__}: {str(e)[:120]}"
    watching = _watch_restock(row)
    quiet = recheck and res["done"] and not res["changed"]      # still as the guard left it: no news
    print(f"  🛑 LISTING OUT AT ZORO{' (checked again, nothing to do)' if quiet else ''}: "
          f"{row['asin']} ({row.get('seller_sku') or '?'}) — "
          f"{kind or '?'}{' / ' + brand if brand else ''} — "
          + (f"on Amazon: {res['note']}" if res["done"] else f"NOT changed on Amazon ({res['note']})"))
    short = (f"Zoro {row.get('zoro_code') or '?'} is out of stock — " + (
        f"{'quantity set to 0' if action == 'qty0' else 'handling time raised'} on Amazon ({res['note']})" if res["done"]
        else f"NOT changed on Amazon: {res['note']}"))
    if not quiet:
        _tell(store, _message(row, kind, brand, src, action, res, watching, bool(cfg["restore"])), row,
              "listing_off_sale", short, to=_groups_for(action))
    cols = dict(kind=kind, brand=brand, kind_from=src, action=action, status="changed" if res["done"] else "alerted",
                action_at=_now(), note=res["note"][:300], restock_checked_at=None)
    if res["changed"]:
        cols.update(prev_value=res["previous"], changed_to=res["set_to"])
    _update(store, row["asin"], **cols)


# ── step 2b: Zoro has it again → put the listing back ──────────────────────────────────────────
def _back_in_stock(driver, row: Dict[str, Any], depth: int) -> Optional[str]:
    """The Zoro code (main or an alternative) that is buyable again for `depth` units of the listing,
    '' when none is, None when Zoro did not answer for one of them (no decision this time)."""
    from platforms.zoro import check_inventory
    qty = max(1, int(row.get("unit_qty") or 1)) * max(1, int(depth))
    codes = [str(row.get("zoro_code") or "").strip()] + [a.strip() for a in str(row.get("alt_codes") or "").split(",")]
    for code in [c for c in codes if c]:
        cartable, status = check_inventory(driver, code, qty)
        if status in _UNKNOWN:
            return None
        if cartable:
            return code
    return ""


def _restore(driver, store: str, profile: str, cfg: Dict[str, Any], row: Dict[str, Any], code: str) -> None:
    from engine import offer_edit
    asin, sku, action = row["asin"], row.get("seller_sku") or "", row.get("action") or ""
    set_to, prev = str(row.get("changed_to") or "").strip(), str(row.get("prev_value") or "").strip()
    what = "quantity" if action == "qty0" else "handling time"
    lines = [f"✅ *Zoro has it again — {store} listing"]
    status, note = "restock_told", ""

    if not set_to:
        # The guard changed nothing here (the value was already there, or the change failed): tell, don't touch.
        lines[0] += " to put back by hand*"
        note = f"Zoro {code} is in stock again; the placer had not changed this listing, so it is left as it is"
        lines += _head(row) + [f"• Zoro `{code}` is in stock again",
                               "• The placer had not changed this listing, so nothing was put back — check it by hand",
                               f"  {listing_url(asin)}"]
    else:
        target: Optional[int] = None
        if action == "qty0":
            target = int(prev) if prev.isdigit() and int(prev) > 0 else None
        elif prev.isdigit():
            target = int(prev)
        else:                                   # it was empty before = the store's own default
            target = offer_edit.account_default_handling_days(driver)
        if target is None:
            lines[0] += " to put back by hand*"
            note = f"Zoro {code} is in stock again; the {what} it had before is not known"
            lines += _head(row) + [f"• Zoro `{code}` is in stock again",
                                   f"• ⚠️ The {what} it had before is not known — set it by hand", f"  {listing_url(asin)}"]
        else:
            res = offer_edit.set_offer_field(driver, asin, sku, "quantity" if action == "qty0" else "handling_days",
                                             target, login_profile=profile or None, lower_ok=True, only_if=set_to)
            if res.get("ok"):
                status = "restored"
                lines[0] += " back on sale*" if action == "qty0" else " back to its normal handling time*"
                note = f"Zoro {code} is in stock again — {res.get('note')}"
                lines += _head(row) + [f"• Zoro `{code}` is in stock again", f"• ✅ On Amazon: {res.get('note')}"]
            elif res.get("skipped"):
                lines[0] += " left as it is*"
                note = f"Zoro {code} is in stock again; {res.get('note')}"
                lines += _head(row) + [f"• Zoro `{code}` is in stock again", f"• Not touched: {res.get('note')}"]
            else:
                lines[0] += " NOT put back*"
                note = f"Zoro {code} is in stock again, but the {what} could not be put back: {res.get('note')}"
                lines += _head(row) + [f"• Zoro `{code}` is in stock again",
                                       f"• ⚠️ Could not put the {what} back to {target}: {res.get('note')}",
                                       f"• To do by hand: {listing_url(asin)}"]
    print(f"  ✅ LISTING RESTOCKED AT ZORO: {asin} ({sku or '?'}) — {note}")
    _watch_restock(row, remove=True)
    _tell(store, "\n".join(lines), row, "listing_back_on_sale", note, to=_groups_for(action))
    _update(store, asin, status=status, restored_at=_now(), note=note[:300])


def _restock_pass(driver, store: str, profile: str, cfg: Dict[str, Any], room: int) -> None:
    if not cfg["restore"] or driver is None:
        return
    every = max(5, int(cfg["restock_check_minutes"]))
    c = _conn()
    try:
        due = [dict(r) for r in c.execute(
            "SELECT * FROM listing_oos WHERE store=? AND status IN ('changed', 'alerted') AND COALESCE(zoro_code, '') != '' "
            "AND action_at <= ? AND (restock_checked_at IS NULL OR restock_checked_at <= ?) "
            "ORDER BY COALESCE(restock_checked_at, '') LIMIT ?", (store, _ago(every), _ago(every), RESTOCK_CHECKS_PER_CYCLE))]
    finally:
        c.close()
    if not due:
        return
    try:
        from platforms.zoro import ensure_on_site, ensure_token
        ensure_on_site(driver)
        ensure_token(driver)
    except Exception as e:
        print(f"  ⚠️  listing guard: restock check skipped ({type(e).__name__})")
        return
    for row in due:
        # stamp the check first: the other placer of this store then leaves the row alone
        c = _conn()
        try:
            with c:
                mine = c.execute("UPDATE listing_oos SET restock_checked_at=? WHERE store=? AND asin=? AND status=? "
                                 "AND (restock_checked_at IS NULL OR restock_checked_at <= ?)",
                                 (_now(), store, row["asin"], row["status"], _ago(every))).rowcount == 1
        finally:
            c.close()
        if not mine:
            continue
        try:
            code = _back_in_stock(driver, row, int(cfg["restock_depth"]))
        except Exception as e:
            print(f"  ⚠️  listing guard: stock question failed for {row['asin']} ({type(e).__name__})")
            continue
        if not code:                              # still out, or Zoro did not answer
            continue
        if room <= 0:                             # this cycle's time is used up: ask again next cycle
            _update(store, row["asin"], restock_checked_at=None)
            continue
        if not _claim(store, row["asin"], row["status"], "restoring"):
            continue
        room -= 1
        try:
            _restore(driver, store, profile, cfg, row, code)
        except Exception as e:
            print(f"  ⚠️  listing guard: restore failed for {row['asin']} ({type(e).__name__}: {e})")
            _update(store, row["asin"], status=row["status"], note=f"restore error: {type(e).__name__}"[:300])


def handle_pending(driver, store: str, profile: str = ""):
    """End of a placer cycle: deal with this store's listings — the ones noted out of stock since the
    last time, and the ones Zoro has restocked. Returns the driver."""
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
            with c:                                      # rows a placer took and never finished (it was stopped)
                c.execute("UPDATE listing_oos SET status='new' WHERE status='working' AND action_at <= ?", (_ago(STALE_CLAIM_MINUTES),))
                c.execute("UPDATE listing_oos SET status='changed' WHERE status='restoring' AND action_at <= ?", (_ago(STALE_CLAIM_MINUTES),))
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM listing_oos WHERE store=? AND status IN ('new', 'recheck') ORDER BY first_seen LIMIT ?",
                (store, int(cfg["max_per_cycle"])))]
        finally:
            c.close()
        room = int(cfg["max_per_cycle"])
        for row in rows:
            if not _claim(store, row["asin"], row["status"], "working"):
                continue                                 # the store's other placer has it
            room -= 1
            try:
                _handle_new(driver, store, profile, cfg, row, recheck=row["status"] == "recheck")
            except Exception as e:
                print(f"  ⚠️  listing guard: {row['asin']}: {type(e).__name__}: {e}")
                _update(store, row["asin"], status="alerted", note=f"error: {type(e).__name__}: {str(e)[:200]}")
        _restock_pass(driver, store, profile, cfg, room)
    except Exception as e:                               # never in the way of the next cycle
        print(f"  ⚠️  listing guard: {type(e).__name__}: {e}")
    return driver


if __name__ == "__main__":      # python -m engine.listing_guard  → the list, newest first (read-only)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(listing_oos)")}
        pick = [c for c in ("store", "asin", "seller_sku", "kind", "brand", "orders", "status", "action", "prev_value",
                            "changed_to", "first_seen", "action_at", "restock_checked_at", "restored_at", "note") if c in cols]
        if not pick:
            raise sqlite3.OperationalError("no table")
        print(" | ".join(pick))
        for r in con.execute(f"SELECT {', '.join(pick)} FROM listing_oos ORDER BY last_seen DESC LIMIT 50"):
            print(" | ".join(str(x if x is not None else "") for x in r))
    except sqlite3.OperationalError:
        print("listing_oos is empty: no listing has been noted yet")
    finally:
        con.close()
