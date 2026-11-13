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
          say what was done in the store's Insights group — and only there, never in the Orders
          Alerts group (owner, 11 Nov 2026) — and put the Zoro code on the dashboard's Restock Watch;
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
  • a restore that fails is announced once and then left to a person. It is not retried in a loop;
  • an item Zoro shows with "Extended lead time" is not back, whatever the stock API says — the placer
    would not buy it (engine.orders._zoro_extended_lead, asked for one listing unit at the profile's ZIP).

Own-brand (PL) listings — owner, 13 Nov 2026: "we never inactivate the PL listing", "if PL listing is OOS
never activate it", "I do want to have the manual control". DS listings are unchanged ("no issue with
the DS, we can take it off and take back in by the script").
  • own brands are HOANK, Hathoro and Wrenchly (utils/listing_watch.PL_BRANDS). A brand that is not ours
    on a listing whose SKU looks like ours (HOA- / HO- / hath / Wrench-) changes nothing: a person is asked;
  • the quantity of a PL listing is never written;
  • a PL listing at quantity 0 on Amazon is not touched at all — not when Zoro runs out, not at the restock;
  • the handling time is raised to 7 days only from empty or the store's default (HOANK: 1 day). Any
    other value (2, 3, 5 days …) was set by a person and is left as it is;
  • put back only what the guard set, only while the field still shows it (as for DS), and only once
    Zoro has had pl_restock_depth units on every check for pl_restock_confirm_minutes;
  • a PL listing that runs out again within pl_flap_hours of being put back is not put back by the guard
    a second time: the restock is announced with the value to set, and a person sets it.
(10 Nov–13 Nov 2026 before this: the Wrenchly-brand B0HR7QZ18R was set 492 → 0, and B0FHSQ12HJ's
hand-set 2 days went 2 → 7 → 2 → 7 in 7 hours while our own placers emptied Zoro's few units.)

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
  pl_restock_depth      10     own-brand listing: units that must be buyable before its handling time goes back
  pl_restock_confirm_minutes 60  own-brand listing: Zoro must still have them this long after first seen back
  pl_flap_hours         24     own-brand listing out again this soon after a put-back → not put back automatically
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
                  "restock_checked_at": "TEXT", "restored_at": "TEXT",
                  "kept": "TEXT",                       # own-brand listing left as a person set it: off_sale | hand_set
                  "restock_seen_at": "TEXT",            # own-brand: first check that found Zoro stock again
                  "hold_restore": "INTEGER DEFAULT 0"}  # own-brand: ran out again within pl_flap_hours of a put-back

_DEFAULTS = {"enabled": True, "armed": True, "pl_handling_days": 7, "restore": True, "restock_check_minutes": 30,
             "restock_depth": 3, "realert_hours": 24, "max_per_cycle": 3,
             "pl_restock_depth": 10, "pl_restock_confirm_minutes": 60, "pl_flap_hours": 24}
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
def _sku_key(s: Optional[str]) -> str:
    return "".join(str(s or "").split()).upper()


def _write_note(ev: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    realert_hours = cfg["realert_hours"]
    c = _conn()
    try:
        with c:
            row = c.execute("SELECT status, action_at, action, seller_sku, restored_at FROM listing_oos "
                            "WHERE store=? AND asin=?", (ev["store"], ev["asin"])).fetchone()
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
            # The order page's SKU reader stops at a space ("HOA- G006569386" → "HOA-"): never let such a
            # cut SKU replace the full one already on the row.
            sku = ev["seller_sku"]
            old = row["seller_sku"] or ""
            if sku and len(_sku_key(old)) > len(_sku_key(sku)) and _sku_key(old).startswith(_sku_key(sku)):
                sku = old
            fresh_args: tuple = ()
            if st in _CLOSED:
                # A new round for this listing: it starts afresh, so the launcher's watch list shows when it
                # went out THIS time and no note, action or brand verdict left from the last round.
                # An own-brand listing put back less than pl_flap_hours ago is not put back on its own a
                # second time (owner, 13 Nov 2026: B0FHSQ12HJ went 2 → 7 → 2 → 7 days in 7 hours).
                hold = int(st == "restored" and (row["action"] or "") == "handling"
                           and _hours_since(row["restored_at"]) < float(cfg["pl_flap_hours"]))
                again = "new"
                fresh = (", prev_value=NULL, changed_to=NULL, restock_checked_at=NULL, restored_at=NULL, "
                         "first_seen=?, note=NULL, action=NULL, action_at=NULL, kept=NULL, restock_seen_at=NULL, "
                         "hold_restore=?")
                fresh_args = (ev["at"], hold)
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
                      (ev["amazon_id"], ev["at"], sku, ev["title"], ev["zoro_code"], ev["alt_codes"],
                       ev["unit_qty"]) + fresh_args + (int(held or 1), ev["store"], ev["asin"]))
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
            _write_note(ev, cfg)
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


CLASH = "brand and SKU disagree"      # kind_from of a listing left alone because of that


def classify(driver, store: str, asin: str, seller_sku: str) -> Tuple[str, str, str]:
    """('PL' | 'DS' | '', brand, where the answer came from).
    A brand that is not ours on a listing whose SKU looks like ours (HOA- / HO- / hath / Wrench-) gives
    '' — nothing is changed and a person is asked (owner, 13 Nov 2026: an own-brand listing must never
    be taken off sale; a wrong brand read had done it). A brand that is ours always gives PL."""
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
        if lw.brand_is_ours(brand):
            return "PL", brand, src
        if seller_sku and lw.sku_is_pl(seller_sku, store):
            return "", brand, CLASH
        return "DS", brand, src
    if seller_sku:
        return ("PL" if lw.sku_is_pl(seller_sku, store) else "DS"), "", "SKU prefix (brand not readable)"
    return "", "", "no brand and no SKU"


def listing_url(asin: str) -> str:
    return ("https://sellercentral.amazon.com/myinventory/inventory?fulfilledBy=all&page=1&pageSize=25"
            f"&searchField=all&searchTerm={urllib.parse.quote(asin)}&sort=date_created_desc&status=all")


_DEFAULT_DAYS: Dict[str, Tuple[Optional[int], float]] = {}     # store → (default handling days, read at)


def store_default_days(driver, store: str) -> Optional[int]:
    """The store's own default handling time (Seller Central → Shipping settings; HOANK: 1 day), read
    once a day per placer — once an hour while it cannot be read. None when the page does not say."""
    hit = _DEFAULT_DAYS.get(store)
    if hit and time.time() - hit[1] < (24 * 3600 if hit[0] is not None else 3600):
        return hit[0]
    from engine import offer_edit
    v = offer_edit.account_default_handling_days(driver)
    _DEFAULT_DAYS[store] = (v, time.time())
    return v


def change_on_amazon(driver, store: str, row: Dict[str, Any], action: str, profile: str = "") -> Dict[str, Any]:
    """Set the quantity to 0 (action 'qty0') or raise the handling time ('handling') on Seller Central,
    in the placer's own Chrome. Returns {'done', 'changed', 'kept', 'note', 'previous', 'set_to', 'found',
    'sku'}: done is True when there is nothing more to do on Amazon; changed only when the guard itself
    just set the value. See engine/offer_edit.py for how the page is driven and what was tried live.

    An own-brand listing is left as a person set it (owner, 13 Nov 2026) — kept says which:
      'off_sale'  its Amazon quantity is 0: nothing is touched, it is the owner's to turn back on;
      'hand_set'  its handling time is neither empty nor the store's default (say 2 days): not raised."""
    out = {"done": False, "changed": False, "kept": "", "note": "", "previous": "", "set_to": "", "found": "", "sku": ""}
    cfg = settings()
    if not cfg["armed"]:
        out["note"] = "the automatic change is switched off (listing_guard.armed)"
        return out
    if driver is None:
        out["note"] = "no browser to make the change with"
        return out
    from engine import offer_edit
    if action == "qty0":
        field, value, extra = "quantity", 0, {}
    else:
        d = store_default_days(driver, store)
        field, value = "handling_days", int(cfg["pl_handling_days"])
        extra = {"skip_if_off_sale": True, "raise_from": ["", str(d if d is not None else 1)]}
    res = offer_edit.set_offer_field(driver, row["asin"], row.get("seller_sku") or "", field, value,
                                     login_profile=profile or None, **extra)
    kept = str(res.get("kept") or "")
    out.update(done=bool(res.get("ok")) or bool(kept), changed=bool(res.get("changed")), kept=kept,
               note=str(res.get("note") or ""), sku=str(res.get("sku") or ""),
               found=str(res.get("previous") or ""))   # what the field held on the page (for the message)
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


def _tell(store: str, text: str, row: Dict[str, Any], kind: str, short: str,
          to: Tuple[str, ...] = ("insights",)) -> None:
    """One message to the store's Insights group, and one row for the dashboard's Insights tab
    (utils/listing_events.py). Listing changes are an Insights matter: nothing from the guard goes to
    the Orders Alerts group (owner, 11 Nov 2026 — first for handling time, then for quantity 0 too)."""
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


# ── the messages (owner, 11 Nov 2026: plain sentences, first person, no labels) ────────────────
def short_name(title: str, asin: str = "") -> str:
    """The product as a headline can name it: the title's first comma-separated parts, up to about
    55 characters ('Eagle Plastic Drum, 30 Gallon Drum, DOT Compliant'); 'ASIN …' with no title."""
    t = " ".join(str(title or "").split()).split(" | ")[0].strip(" ,.-")
    if not t:
        return f"ASIN {asin}" if asin else "this listing"
    parts, out = [p.strip() for p in t.split(",") if p.strip()], ""
    for p in parts:
        if out and len(out) + 2 + len(p) > 55:
            break
        out = f"{out}, {p}" if out else p
    if len(out) > 70:
        out = out[:70].rsplit(" ", 1)[0]
    return out


def _ids(row: Dict[str, Any]) -> str:
    return f"• ASIN {row['asin']}" + (f" · SKU {row['seller_sku']}" if row.get("seller_sku") else "")


def _days(v: str) -> str:
    return f"{v} day" + ("" if str(v) == "1" else "s")


def _message(row: Dict[str, Any], kind: str, brand: str, src: str, action: str, res: Dict[str, Any],
             watching: bool, restore_on: bool, cfg: Optional[Dict[str, Any]] = None) -> str:
    """The message when the placer finds a listing's Zoro item, and every alternative, out of stock.
    Example (owner's wording, 12 Nov 2026):
        🛑 Zoro is out of stock for Transport Drum, Closed Head, 55 gal., Black.
        • ASIN B01M0FGOOK · SKU Kam-18nov25-G5546344-1
        • Please let me know if there is any alternative
        • One order has been marked out of stock.
        • ✅ I set the Amazon quantity from 499 to 0, so the listing is OOS on Amazon now.
    The restock check and the automatic restore still run when quantity was set to 0; the owner
    asked for the line announcing them ("I'll check Zoro every 30 minutes…") to go."""
    cfg = cfg or settings()
    code = row.get("zoro_code") or "?"
    alts = (row.get("alt_codes") or "").strip()
    done, changed, why = bool(res.get("done")), bool(res.get("changed")), (res.get("note") or "").strip()
    prev, found = str(res.get("previous") or "").strip(), str(res.get("found") or "").strip()
    pl_days = str(res.get("set_to") or cfg["pl_handling_days"])
    url = listing_url(row["asin"])
    n = int(row.get("orders") or 1)
    ours = f"It's our own brand{f' ({brand})' if brand else ''}"

    lines = [f"🛑 *Zoro is out of stock for {short_name(row.get('title'), row['asin'])}.*", _ids(row)]
    lines.append(f"• Zoro item {code} is out, and so is every alternative in the sheet ({alts})." if alts
                 else "• Please let me know if there is any alternative")
    lines.append("• One order has been marked out of stock." if n == 1 else f"• {n} orders have been marked out of stock.")

    # what was done on Amazon
    if action == "qty0":
        if done and changed:
            lines.append(f"• ✅ I set the Amazon quantity from {prev or '?'} to 0, so the listing is OOS on Amazon now.")
        elif done:
            lines.append(f"• ✅ The Amazon quantity was already {found or '0'}, so I left it as is.")
        else:
            lines.append(f"• ⚠️ I couldn't set the Amazon quantity to 0 because {why}. Please set it to 0 by hand: {url}")
    elif action == "handling":
        if res.get("kept") == "off_sale":
            lines.append(f"• ✅ {ours}. Its Amazon quantity is 0, so it isn't on sale, and I left it as it is.")
        elif res.get("kept") == "hand_set":
            lines.append(f"• ✅ {ours}, so it stays on sale. Its handling time is "
                         f"{_days(found) if found.isdigit() else found or '?'}, which was set by hand, so I left it as it is.")
        elif done and changed:
            lines.append(f"• ✅ {ours}, so it stays on sale. I raised the handling time from "
                         + (_days(prev) if prev.isdigit() else "the store's default") + f" to {_days(pl_days)}.")
        elif done:
            lines.append(f"• ✅ {ours}, so it stays on sale. The handling time was already "
                         f"{_days(found or pl_days)}, so I left it as is.")
        else:
            lines.append(f"• ⚠️ {ours}, so it should stay on sale with a longer handling time, but I couldn't change it "
                         f"because {why}. Please raise the handling time to {_days(pl_days)} by hand, and don't set the "
                         f"quantity to 0: {url}")
    elif src == CLASH:
        lines.append(f"• ⚠️ The product page says the brand is {brand}, but the SKU looks like one of ours, so I "
                     f"changed nothing on Amazon. Please check it: {url}")
    else:
        lines.append(f"• ⚠️ I couldn't tell whether this is our own brand, so I changed nothing on Amazon. "
                     f"Please check it: {url}")

    # what happens at the restock
    what = "quantity" if action == "qty0" else "handling time"
    every = f"every {int(cfg['restock_check_minutes'])} minutes"
    if not restore_on:
        lines.append(f"• Automatic restore is switched off: when Zoro has it again, please put the Amazon {what} back by hand.")
    elif done and changed and action == "qty0":
        pass                # owner, 12 Nov 2026: no restock line here (the restore still happens)
    elif done and changed and row.get("hold_restore"):
        lines.append(f"• It ran out again less than a day after I last put it back, so this time I won't put the "
                     f"handling time back myself. I'll let you know when Zoro has it again.")
    elif res.get("kept"):
        lines.append(f"• I'll check Zoro {every} and let you know when it's back in stock.")
    elif done and changed:
        back = (f"the quantity back to {prev}" if action == "qty0" and prev.isdigit()
                else "the quantity back" if action == "qty0"
                else f"the handling time back to {_days(prev)}" if prev.isdigit()
                else "the handling time back to the store's default")
        lines.append(f"• I'll check Zoro {every}. When it's back in stock I'll put {back} and let you know.")
    elif done:
        lines.append(f"• I added it to the list to recheck stock {every}. I'll let you know when Zoro has it back "
                     f"in stock. The Amazon {what} will need to be updated.")
    else:
        lines.append(f"• I'll check Zoro {every} and let you know when it's back in stock.")
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
        f"left as it is on Amazon ({res['note']})" if res.get("kept")
        else f"{'quantity set to 0' if action == 'qty0' else 'handling time raised'} on Amazon ({res['note']})" if res["done"]
        else f"NOT changed on Amazon: {res['note']}"))
    if not quiet:
        _tell(store, _message(row, kind, brand, src, action, res, watching, bool(cfg["restore"]), cfg), row,
              "listing_off_sale", short)
    cols = dict(kind=kind, brand=brand, kind_from=src, action=action, status="changed" if res["done"] else "alerted",
                action_at=_now(), note=res["note"][:300], restock_checked_at=None, restock_seen_at=None,
                kept=res.get("kept") or "")
    if res["changed"]:
        cols.update(prev_value=res["previous"], changed_to=res["set_to"])
    if res.get("sku") and res["sku"] != (row.get("seller_sku") or ""):
        cols["seller_sku"] = res["sku"]           # the full SKU from Manage Inventory (a cut one is completed)
    _update(store, row["asin"], **cols)


# ── step 2b: Zoro has it again → put the listing back ──────────────────────────────────────────
def _sheet_codes(asin: str) -> Tuple[str, List[str]]:
    """(main Zoro code, [alternatives]) the Google Sheet holds for this ASIN NOW — through the placer's
    own load_sku_map() (cached 5 min). The placer runs engine/orders.py as __main__, so it is looked
    up there rather than imported again. ('', []) when the sheet cannot be read."""
    import sys
    for name in ("__main__", "engine.orders"):
        fn = getattr(sys.modules.get(name), "load_sku_map", None)
        if callable(fn):
            try:
                m = fn().get(asin) or {}
                return str(m.get("zoro_code") or "").strip(), [str(a).strip() for a in (m.get("alt_skus") or []) if str(a).strip()]
            except Exception:
                return "", []
    return "", []


def _back_in_stock(driver, row: Dict[str, Any], depth: int) -> Optional[str]:
    """The Zoro code (main or an alternative) that is buyable again for `depth` units of the listing,
    '' when none is, None when Zoro did not answer for one of them (no decision this time).
    Asked: the codes the guard saw out, then whatever the sheet says NOW — owner, 12 Nov 2026: "I put
    an alternative SKU of Zoro [in the sheet], the script should detect that we do have the
    alternative and change the handling time back". row['_new_alt'] names a code the sheet added
    after the guard fired, for the message."""
    from platforms.zoro import check_inventory
    qty = max(1, int(row.get("unit_qty") or 1)) * max(1, int(depth))
    known = [str(row.get("zoro_code") or "").strip()] + [a.strip() for a in str(row.get("alt_codes") or "").split(",")]
    known = [c for c in known if c]
    s_main, s_alts = _sheet_codes(row["asin"])
    added = [c for c in [s_main] + s_alts if c and c not in known]
    row["_sheet_added"] = added
    codes = []
    for c in known + added:
        if c not in codes:
            codes.append(c)
    # The placer treats Zoro's "Extended lead time" as out of stock (13 Nov 2026) while the stock API still
    # says in stock — asked here too, or the listing would go back on sale for orders the placer refuses.
    lead = _placer_fn("_zoro_extended_lead")
    zip_fn = _placer_fn("_restock_zip")
    one = max(1, int(row.get("unit_qty") or 1))
    for code in codes:
        cartable, status = check_inventory(driver, code, qty)
        if status in _UNKNOWN:
            return None
        if cartable:
            if lead and zip_fn:
                try:
                    why = lead(driver, code, one, {"zip_code": zip_fn()})
                except Exception:
                    why = ""
                if why:
                    print(f"  ⏳ listing guard: {row['asin']} — {code} is in stock but not back yet: {why}")
                    continue
            return code
    return ""


def _placer_fn(name: str):
    """A function of the running placer (engine/orders.py runs as __main__), or None outside a placer."""
    import sys
    for mod in ("__main__", "engine.orders"):
        fn = getattr(sys.modules.get(mod), name, None)
        if callable(fn):
            return fn
    return None


def _brand_now_ours(brand: Optional[str]) -> bool:
    try:
        from utils.listing_watch import brand_is_ours
        return brand_is_ours(brand)
    except Exception:
        return False


def _restore(driver, store: str, profile: str, cfg: Dict[str, Any], row: Dict[str, Any], code: str) -> None:
    from engine import offer_edit
    asin, sku, action = row["asin"], row.get("seller_sku") or "", row.get("action") or ""
    set_to, prev = str(row.get("changed_to") or "").strip(), str(row.get("prev_value") or "").strip()
    what = "quantity" if action == "qty0" else "handling time"
    main = str(row.get("zoro_code") or "").strip()
    url = listing_url(asin)
    status, note = "restock_told", ""
    added = code in (row.get("_sheet_added") or [])
    lines = [f"✅ *Zoro has {short_name(row.get('title'), asin)} back in stock.*", _ids(row),
             f"• The sheet now has the alternative {code}, and Zoro has it in stock." if added
             else f"• Zoro item {code} can be bought again." if code == main or not main
             else f"• Zoro has the alternative {code} in stock again (the main item {main} is still out)."]

    kept = str(row.get("kept") or "")
    if not set_to:
        # The guard changed nothing here (the value was already there, a person's value was kept, or the
        # change failed): tell, don't touch.
        note = f"Zoro {code} is in stock again; the placer had not changed this listing, so it is left as it is"
        if action == "handling" and kept == "off_sale":
            lines.append(f"• Its Amazon quantity was 0 when I looked, so I left it alone. It's yours to put back on "
                         f"sale: {url}")
        elif action == "handling" and kept == "hand_set":
            lines.append("• I hadn't changed its handling time (it was set by hand), so I left it as it is.")
        else:
            lines.append(f"• I hadn't changed this listing, so I left it alone. Please update the Amazon {what} "
                         f"by hand: {url}")
    elif action == "qty0" and _brand_now_ours(row.get("brand")):
        # Set to 0 before its brand was known as ours (Wrenchly, 13 Nov 2026: B0HR7QZ18R 492 → 0). The
        # quantity of an own-brand listing is never written by the guard — a person puts it back.
        note = f"Zoro {code} is in stock again; own-brand listing ({row.get('brand')}), quantity left for a person"
        lines.append(f"• This is our own brand ({row.get('brand')}). I had set its quantity from "
                     f"{prev or '?'} to 0 by mistake, so please put it back by hand: {url}")
    elif action == "handling" and row.get("hold_restore"):
        # Own-brand listing that ran out again soon after the last put-back: Zoro's stock is too thin to
        # trust, so a person decides (owner, 13 Nov 2026).
        before = _days(prev) if prev.isdigit() else "the store's default"
        note = (f"Zoro {code} is in stock again; it had run out again within {int(cfg['pl_flap_hours'])} h of the "
                f"last put-back, so the handling time is left at {set_to} days for a person")
        lines.append(f"• It ran out again less than a day after I last put it back, so I left the handling time at "
                     f"{_days(set_to)}. Set it back to {before} when you're happy with Zoro's stock: {url}")
    else:
        target: Optional[int] = None
        if action == "qty0":
            target = int(prev) if prev.isdigit() and int(prev) > 0 else None
        elif prev.isdigit():
            target = int(prev)
        else:                                   # it was empty before = the store's own default
            target = store_default_days(driver, store)
        if target is None:
            note = f"Zoro {code} is in stock again; the {what} it had before is not known"
            lines.append(f"• ⚠️ I don't know what the {what} was before, so please set it by hand: {url}")
        else:
            res = offer_edit.set_offer_field(driver, asin, sku, "quantity" if action == "qty0" else "handling_days",
                                             target, login_profile=profile or None, lower_ok=True, only_if=set_to,
                                             skip_if_off_sale=(action == "handling"))
            was = set_to if action == "qty0" else _days(set_to)
            now = str(target) if action == "qty0" else _days(str(target))
            if res.get("ok"):
                status = "restored"
                note = f"Zoro {code} is in stock again — {res.get('note')}"
                lines.append(f"• ✅ I set the Amazon quantity back from {was} to {now}, so the listing is on sale again."
                             if action == "qty0" else f"• ✅ I set the handling time back from {was} to {now}.")
            elif res.get("kept") == "off_sale":
                note = f"Zoro {code} is in stock again; the Amazon quantity is 0, so the listing was left alone"
                lines.append(f"• Its Amazon quantity is 0, so I didn't touch it. The handling time is still {was}; "
                             f"please set it back to {now} when you put it on sale again: {url}")
            elif res.get("skipped"):
                note = f"Zoro {code} is in stock again; {res.get('note')}"
                lines.append(f"• Someone changed the {what} after I did (it is {res.get('previous') or 'empty'} now, "
                             f"not {was}), so I left it alone.")
            else:
                note = f"Zoro {code} is in stock again, but the {what} could not be put back: {res.get('note')}"
                lines.append(f"• ⚠️ I couldn't put the {what} back to {now} because {res.get('note')}. "
                             f"Please do it by hand: {url}")
    print(f"  ✅ LISTING RESTOCKED AT ZORO: {asin} ({sku or '?'}) — {note}")
    _watch_restock(row, remove=True)
    _tell(store, "\n".join(lines), row, "listing_back_on_sale", note)
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
        # An own-brand listing needs more stock, and needs it to last: Zoro must have pl_restock_depth units
        # on every check for pl_restock_confirm_minutes (owner, 13 Nov 2026 — with 3 units it was put back,
        # our own placers bought them within the hour, and it went out again). A DS listing as before.
        pl = (row.get("action") or "") == "handling"
        try:
            code = _back_in_stock(driver, row, int(cfg["pl_restock_depth"] if pl else cfg["restock_depth"]))
        except Exception as e:
            print(f"  ⚠️  listing guard: stock question failed for {row['asin']} ({type(e).__name__})")
            continue
        if code is None:                          # Zoro did not answer: no decision this time
            continue
        if not code:                              # still out
            if pl and row.get("restock_seen_at"):
                _update(store, row["asin"], restock_seen_at=None)
                print(f"  ⏳ listing guard: {row['asin']} — Zoro is out again, the hour starts over next time")
            continue
        if pl:
            wait = max(0, int(cfg["pl_restock_confirm_minutes"]))
            seen = row.get("restock_seen_at")
            if not seen and wait:
                _update(store, row["asin"], restock_seen_at=_now())
                print(f"  ⏳ listing guard: {row['asin']} — Zoro has {code} again; put back if it is still there "
                      f"in {wait} min")
                continue
            if seen and _hours_since(seen) * 60 < wait:
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


def _anything_due(store: str, cfg: Dict[str, Any]) -> bool:
    """One read-only look: does this store have a row to handle (new / recheck / a claim to tidy) or a
    restock check that is due? A placer with no orders calls handle_pending every cycle (11 Nov 2026),
    so the usual answer — nothing — must not take the database's write lock. Unsure → True."""
    every = max(5, int(cfg["restock_check_minutes"]))
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        try:
            return c.execute(
                "SELECT 1 FROM listing_oos WHERE store=? AND (status IN ('new', 'recheck', 'working', 'restoring') "
                "OR (? AND status IN ('changed', 'alerted') AND COALESCE(zoro_code, '') != '' AND action_at <= ? "
                "AND (restock_checked_at IS NULL OR restock_checked_at <= ?))) LIMIT 1",
                (store, 1 if cfg["restore"] else 0, _ago(every), _ago(every))).fetchone() is not None
        finally:
            c.close()
    except sqlite3.OperationalError as e:
        return "no such table" not in str(e)            # no table yet: nothing to do; anything else: look properly
    except sqlite3.Error:
        return True


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
                _write_note(ev, cfg)
            except sqlite3.Error:
                _MISSED.insert(0, ev)
                break
        if not _anything_due(store, cfg):
            return driver
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
