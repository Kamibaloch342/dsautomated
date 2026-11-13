"""
offer_edit.py — change ONE field of a listing's offer on Seller Central: the quantity or the handling time.

Used by engine/listing_guard.py when Zoro runs out of a listing's item (owner, 10 Nov 2026):
a DS listing gets quantity 0, an own-brand (PL) listing gets a longer handling time.

The page is Seller Central's "Edit Listing → Offer":
    /interactive/listing/workflow/edit/offer?sku=<seller sku>&asin=<asin>&marketplaceID=ATVPDKIKX0DER
It comes in two shapes, both read from the live page on 10 Nov 2026:
  A. the fields sit on the page itself        (B00TY4AMZQ / KS24G-G010473198);
  B. "Sell In Other Marketplaces" is switched on: the page shows a Source Offer card, and its Edit
     button opens a side panel that holds the same fields plus its own Save
     (B0GV9GHGQD / HOA- G105301740).
Field ids are the same in both:
    quantity       kat-input#fulfillment_availability-0-quantity
    handling time  kat-input#fulfillment_availability-0-lead_time_to_ship_max_days
                   (shape A shows it only with "Filter attributes: All"; empty = the account default)
then the page's own Submit (kat-button#form-submit-button).

What the live runs on 10 Nov 2026 showed (B00TY4AMZQ, handling time default → 7 → 1):
  • every change is validated first: the button reads "Validating..." and a click during that is
    lost. The side bar's Offer box goes from "No edits" to "1 Edit" once the change counts;
  • a save is confirmed by kat-modal[data-testid="submitBottomModal"]: "All done. Thanks for
    submitting your changes. If approved, changes will be reflected within 24 hours." The page
    stays on the same URL;
  • a new value showed on a fresh load after one to three minutes, not at once — so the page is
    not read again to check a save, the confirmation is the check;
  • emptying the handling time and saving is accepted and changes nothing: the old value stays.
    To undo a longer handling time, set the number (HOANK's account default is 1 day);
  • the ASIN shown on the edit page only repeats the URL. The page would open any SKU under any
    ASIN, so the listing is first looked up on Manage All Inventory (find_listing), where the row
    of a SKU links to its real ASIN.

Rules this module keeps:
  • it touches exactly one field, found by its id, on a page whose header names the SKU asked for;
  • the value is typed into the field the way a person would, and read back before anything is saved;
  • a value that is already there (quantity already 0, handling time already that long or longer) is
    left alone and nothing is submitted;
  • it never signs in by itself unless the caller passes the placer's profile (the placers' own
    auto-login); without one a sign-in page is reported, not answered;
  • dry_run types and reads back, then leaves the page without saving.

Supervised use (one listing, through a store's closing Chrome that is already signed in):
    python -m engine.offer_edit --port 9331 --asin B00TY4AMZQ --sku "KS24G-G010473198" --field quantity --value 0
    … --dry-run    type and read back, save nothing     … --no-submit  stop at the Submit button
    … --force      save even a value that is already there or shorter (e.g. handling time back to 1)
    … --trace      print what the page showed at each step
"""
from __future__ import annotations

import time
import urllib.parse
from typing import Any, Dict, List, Optional

URL = ("https://sellercentral.amazon.com/interactive/listing/workflow/edit/offer"
       "?sku={sku}&asin={asin}&marketplaceID=ATVPDKIKX0DER")
FIELDS = {
    "quantity": "fulfillment_availability-0-quantity",
    "handling_days": "fulfillment_availability-0-lead_time_to_ship_max_days",
}

_LIB = r"""
if (!window.__zg) {
  const z = window.__zg = {};
  z.deep = function (sel, root) {
    const out = [];
    (function walk(r) {
      for (const el of r.querySelectorAll('*')) { if (el.matches && el.matches(sel)) out.push(el); if (el.shadowRoot) walk(el.shadowRoot); }
    })(root || document);
    return out;
  };
  z.vis = function (el) { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  z.lab = function (b) { return ((b.getAttribute && b.getAttribute('label')) || b.innerText || b.textContent || '').replace(/\s+/g, ' ').trim(); };
  z.press = function (b) { (b.shadowRoot ? (b.shadowRoot.querySelector('button, a') || b) : b).click(); };
  z.panelOf = function (e) { let n = e; while (n) { if (n.tagName === 'KAT-PANEL') return n; n = n.parentNode || n.host; } return null; };
  z.one = function (sel) { return z.deep(sel).filter(z.vis)[0] || null; };
}
"""

_STATE = _LIB + r"""
const z = window.__zg, fid = arguments[0];
const f = z.one('#' + CSS.escape(fid));
const skuEl = document.querySelector('[data-testid="page-title-sku"]');
const asinEl = z.deep('#product-details-asin')[0];
const sub = z.deep('#form-submit-button')[0];
const alerts = z.deep('kat-alert, kat-inline-alert, [role="alert"]').filter(z.vis)
    .map(e => [e.getAttribute('variant') || '', e.getAttribute('header') || '', e.getAttribute('description') || '', (e.innerText || '')].join(' ').replace(/\s+/g, ' ').trim().slice(0, 160))
    .filter(Boolean).slice(0, 4);
const modals = z.deep('kat-modal').filter(e => e.getAttribute('visible') === 'true' && z.vis(e))
    .map(e => ({testid: e.getAttribute('data-testid') || '', text: (e.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 160),
                buttons: z.deep('kat-button', e).filter(z.vis).map(z.lab).filter(Boolean).slice(0, 5)}));
return {
  path: location.pathname,
  signin: /\/ap\/(signin|mfa|cvf)/.test(location.pathname),
  sku: skuEl ? skuEl.innerText.replace(/\s+/g, ' ').trim() : '',
  asin: asinEl ? z.lab(asinEl) : '',
  field: f ? {value: String(f.value == null ? '' : f.value), disabled: f.hasAttribute('disabled'), inPanel: !!z.panelOf(f)} : null,
  editCard: !!z.one('kat-button[data-testid="card-edit-action"]'),
  form: !!z.one('#fulfillment_availability-0-quantity'),
  // the side bar's Offer box: "No edits" until the form has registered a change
  edits: (function () { const b = document.querySelector('#hub-item-box-offer'); return b ? (b.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60) : ''; })(),
  toasts: z.deep('kat-toast, kat-flashbar, kat-banner, kat-statusindicator').filter(z.vis).map(e => ((e.getAttribute('label') || e.getAttribute('header') || '') + ' ' + (e.innerText || '')).replace(/\s+/g, ' ').trim().slice(0, 120)).filter(Boolean).slice(0, 3),
  buttons: z.deep('kat-button').filter(z.vis).map(z.lab).filter(Boolean).slice(0, 14),
  title: document.title,
  // after a save: kat-modal[data-testid="submitBottomModal"] — "All done. Thanks for submitting your changes. …"
  saved: (function () { const m = z.deep('kat-modal[data-testid="submitBottomModal"]').filter(e => e.getAttribute('visible') === 'true')[0];
                        return m ? (m.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 110) : ''; })(),
  panelOpen: z.deep('kat-panel').filter(z.vis).length > 0,
  submit: sub ? {vis: z.vis(sub), disabled: sub.hasAttribute('disabled'), label: z.lab(sub)} : null,
  alerts: alerts, modals: modals,
  body: (document.body.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 140)
};
"""


def _state(driver, fid: str) -> Dict[str, Any]:
    return driver.execute_script(_STATE, fid) or {}


def _wait(driver, fid: str, ready, seconds: float, trace: Optional[List] = None, tag: str = "") -> Dict[str, Any]:
    end = time.time() + seconds
    st: Dict[str, Any] = {}
    while time.time() < end:
        try:
            st = _state(driver, fid)
        except Exception:
            st = {}
        if st and ready(st):
            break
        time.sleep(0.5)
    if trace is not None:
        trace.append((tag, {k: st.get(k) for k in ("path", "field", "editCard", "panelOpen", "edits", "submit", "alerts", "modals")}))
    return st


def _loaded(s: Dict[str, Any]) -> bool:
    """The page is up: a sign-in page, or the offer form with its Submit button at rest. While the
    button reads "Validating..." the form is still being redrawn, and a field found now is gone a
    moment later."""
    if s.get("signin"):
        return True
    sub = s.get("submit") or {}
    return bool((s.get("field") or s.get("editCard") or s.get("form")) and sub.get("vis")
                and "validating" not in str(sub.get("label") or "").lower())


def _ready_to_submit(s: Dict[str, Any]) -> bool:
    sub = s.get("submit") or {}
    label = str(sub.get("label") or "").lower()
    edits = str(s.get("edits") or "").lower()
    return bool(sub.get("vis") and not sub.get("disabled") and "submit" in label and "validating" not in label
                and edits and "no edits" not in edits)


def _press(driver, selector: str, within_panel: bool = False) -> bool:
    return bool(driver.execute_script(_LIB + r"""
        const z = window.__zg;
        let els = z.deep(arguments[0]).filter(z.vis);
        if (arguments[1]) els = els.filter(e => z.panelOf(e));
        if (!els.length || els[0].hasAttribute('disabled')) return false;
        z.press(els[0]); return true;""", selector, within_panel))


def _press_when_enabled(driver, selector: str, within_panel: bool = False, seconds: float = 15.0) -> bool:
    """Press it as soon as it can be pressed: a button is disabled while the form validates what was typed."""
    end = time.time() + seconds
    while time.time() < end:
        if _press(driver, selector, within_panel):
            return True
        time.sleep(0.5)
    return False


def _show_all_attributes(driver, in_panel: bool) -> str:
    """The view filter above the fields ("Filter attributes"): Recommended → All. A view setting only."""
    return driver.execute_script(_LIB + r"""
        const z = window.__zg;
        let fs = z.deep('kat-dropdown').filter(z.vis).filter(e => /filter/i.test((e.getAttribute('name') || '') + (e.getAttribute('data-testid') || '')));
        fs = fs.filter(e => !!z.panelOf(e) === arguments[0]);
        const f = fs[0]; if (!f) return 'no filter';
        if (f.value === 'All') return 'All';
        if (!(f.options || []).some(o => o.value === 'All')) return 'no All option';
        f.value = 'All';
        f.dispatchEvent(new CustomEvent('change', {bubbles: true, composed: true, detail: {value: 'All'}}));
        return 'set';""", in_panel) or ""


def _type(driver, fid: str, text: str) -> str:
    """Type `text` into the field as a person would (real key events into the input inside the Katal
    component), leave the field, and return what the component then holds."""
    from selenium.webdriver.common.keys import Keys
    from selenium.common.exceptions import StaleElementReferenceException
    for attempt in range(3):
        try:
            host = driver.execute_script(_LIB + "return window.__zg.one('#' + CSS.escape(arguments[0]));", fid)
            if host is None:
                return ""
            inner = driver.execute_script("return arguments[0].shadowRoot ? arguments[0].shadowRoot.querySelector('input') : null;", host)
            if inner is None:
                return ""
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'}); arguments[0].focus(); arguments[0].select();", inner)
            time.sleep(0.2)
            inner.send_keys(text)
            time.sleep(0.2)
            inner.send_keys(Keys.TAB)
            time.sleep(0.5)
            # read it from the page afresh: the component may have been redrawn by the change
            return str(driver.execute_script(_LIB + "const f = window.__zg.one('#' + CSS.escape(arguments[0])); "
                                             "return f ? String(f.value == null ? '' : f.value) : '';", fid))
        except StaleElementReferenceException:
            time.sleep(1.0)          # the form redrew itself under us — find the field again
    return ""


def _norm(s: str) -> str:
    return "".join(str(s or "").split()).upper()


INVENTORY_URL = ("https://sellercentral.amazon.com/myinventory/inventory?fulfilledBy=all&page=1&pageSize=25"
                 "&searchField=all&searchTerm={term}&sort=date_created_desc&status=all")

_ROWS = _LIB + r"""
const z = window.__zg, rows = [];
for (const a of z.deep('a[href^="/skucentral?mSku="]')) {
  const sku = (a.innerText || '').trim();
  const row = document.getElementById(sku) || a.closest('div[id]');
  if (!row || !sku) continue;
  const dp = z.deep('a[href*="amazon.com/dp/"]', row)[0];
  const m = dp ? /\/dp\/([A-Z0-9]{10})/.exec(dp.getAttribute('href') || '') : null;
  // two boxes per row: the price box carries a currency label ("USD"), the quantity box does not
  const qg = z.deep('kat-input-group', row).find(g => !g.querySelector('kat-label'));
  const qi = qg ? qg.querySelector('kat-input') : null;
  const st = z.deep('kat-label', row)[0];
  rows.push({sku: sku, asin: m ? m[1] : '', qty: qi ? String(qi.value == null ? '' : qi.value) : '', status: st ? z.lab(st) : ''});
}
return {path: location.pathname, signin: /\/ap\/(signin|mfa|cvf)/.test(location.pathname), rows: rows,
        body: (document.body.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 140)};
"""


def _inventory_rows(driver, term: str, login_profile: Optional[str] = None) -> Dict[str, Any]:
    """Search Manage All Inventory for `term` (a SKU or an ASIN): {'rows': [...], 'signin': bool, …}."""
    url = INVENTORY_URL.format(term=urllib.parse.quote(term))
    st: Dict[str, Any] = {}
    for attempt in (1, 2):
        driver.get(url)
        end = time.time() + 30
        while time.time() < end:
            time.sleep(1)
            try:
                st = driver.execute_script(_ROWS) or {}
            except Exception:
                st = {}
            if st.get("signin") or st.get("rows"):
                break
        if st.get("signin") and login_profile and attempt == 1:
            from platforms.auth import auto_login
            auto_login(driver, login_profile)
            continue
        break
    return st


def find_listing(driver, asin: str, sku: str = "", login_profile: Optional[str] = None) -> Dict[str, Any]:
    """Look the listing up on Manage All Inventory. Returns {'ok', 'sku', 'asin', 'qty', 'status', 'note'}.

    This is what ties a seller SKU to its ASIN: each inventory row is the element whose id is the SKU
    and it links to amazon.com/dp/<ASIN>. With a SKU, that row must show this ASIN. Without one, the
    ASIN must have exactly one row — two SKUs on one ASIN is a choice for a person."""
    out = {"ok": False, "sku": "", "asin": "", "qty": "", "status": "", "note": ""}
    asin, sku = str(asin or "").strip().upper(), str(sku or "").strip()
    st = _inventory_rows(driver, sku or asin, login_profile)
    if st.get("signin"):
        out["note"] = "Seller Central is asking to sign in"
        return out
    rows = st.get("rows") or []
    hits = [r for r in rows if r.get("asin") == asin and (not sku or _norm(r.get("sku")) == _norm(sku))]
    if not hits and sku:
        # The order page's SKU reader stops at a space, so "HOA- G006569386" can arrive as "HOA-"
        # (13 Nov 2026). Look the ASIN up instead and take its one listing whose SKU begins so.
        by_asin = _inventory_rows(driver, asin, login_profile)
        if not by_asin.get("signin"):
            starts = [r for r in (by_asin.get("rows") or [])
                      if r.get("asin") == asin and _norm(r.get("sku")).startswith(_norm(sku))]
            hits = starts                         # two or more: a person's call (below)
    if not hits:
        seen = ", ".join(f"{r.get('sku')} → {r.get('asin')}" for r in rows[:3])
        out["note"] = (f"SKU {sku} is not a listing of {asin} in Manage Inventory" if sku else f"{asin} is not in Manage Inventory") \
            + (f" (found: {seen})" if seen else "")
        return out
    if len(hits) > 1:
        out["note"] = f"{asin} has {len(hits)} listings ({', '.join(r['sku'] for r in hits[:4])}) — which one is a person's call"
        return out
    out.update(ok=True, **hits[0])
    return out


def set_offer_field(driver, asin: str, sku: str, field: str, value: int, login_profile: Optional[str] = None,
                    dry_run: bool = False, trace: Optional[List] = None, force: bool = False,
                    submit: bool = True, lower_ok: bool = False, only_if: Optional[str] = None,
                    skip_if_off_sale: bool = False, raise_from: Optional[List[str]] = None) -> Dict[str, Any]:
    """Set the quantity or the handling time of one offer. Returns
    {'ok': bool, 'changed': bool, 'skipped': bool, 'kept': str, 'previous': str, 'sku': str, 'note': str}.
    ok=True/changed=False means the value was already there (or a dry run). force=True types and
    submits even then (a supervised check, or putting a longer handling time back). submit=False goes
    through everything up to the Submit button and stops there — nothing is saved.
    lower_ok=True lets a handling time go DOWN (putting a listing back after a restock); without it
    a handling time that is already as long or longer is left alone. only_if="7": change the field
    only while it still holds that value — otherwise a person has changed it since, and the result
    is ok=False, skipped=True with nothing typed.
    The two below leave a listing as a person set it (owner, 13 Nov 2026, own-brand listings) —
    ok=False, skipped=True, nothing typed, and 'kept' says why:
      skip_if_off_sale=True  — Manage Inventory shows quantity 0: kept='off_sale';
      raise_from=["", "1"]   — a handling time is only raised from one of these values (empty = the
                               store default); any other shorter value was set by hand: kept='hand_set'.
    'sku' is the listing's full SKU as Manage Inventory shows it."""
    out = {"ok": False, "changed": False, "skipped": False, "kept": "", "previous": "", "sku": "", "note": ""}
    fid = FIELDS.get(field)
    if not fid:
        out["note"] = f"unknown field {field!r}"
        return out
    want = str(int(value))
    asin, sku = str(asin or "").strip().upper(), str(sku or "").strip()
    if not asin:
        out["note"] = "the listing's ASIN is needed"
        return out

    try:
        found = find_listing(driver, asin, sku, login_profile)
        if trace is not None:
            trace.append(("inventory", found))
        if not found["ok"]:
            out["note"] = found["note"]
            return out
        sku = found["sku"]
        out["sku"] = sku
        if skip_if_off_sale and found["qty"].strip() == "0":
            out.update(skipped=True, kept="off_sale", previous="",
                       note="the Amazon quantity is 0 (not on sale) — left alone")
            return out
        if field == "quantity" and only_if is not None and found["qty"].strip() != str(only_if):
            out.update(skipped=True, previous=found["qty"],
                       note=f"the quantity is {found['qty'] or 'empty'}, not {only_if} — changed by hand since, left alone")
            return out
        if field == "quantity" and found["qty"].isdigit() and int(found["qty"]) == int(want) and not force:
            out.update(ok=True, previous=found["qty"], note=f"quantity was already {want}")
            return out
        url = URL.format(sku=urllib.parse.quote_plus(sku), asin=urllib.parse.quote(asin))
        driver.get(url)
        st = _wait(driver, fid, _loaded, 40, trace, "loaded")
        if st.get("signin"):
            if not login_profile:
                out["note"] = "Seller Central is asking to sign in"
                return out
            from platforms.auth import auto_login
            auto_login(driver, login_profile)
            driver.get(url)
            st = _wait(driver, fid, _loaded, 40, trace, "after sign-in")
            if st.get("signin"):
                out["note"] = "Seller Central is still asking to sign in"
                return out
        if not st.get("field") and not st.get("editCard") and not st.get("form"):
            out["note"] = f"the offer page did not load ({(st.get('body') or 'empty page')[:80]})"
            return out

        # A "what's new" notice sits over the page now and then; its button only closes it.
        if _press(driver, 'kat-button[data-testid="feature-launch-got-it"]'):
            time.sleep(1)

        # The page must be the one for THIS SKU before anything is typed. (Its ASIN line only repeats
        # the URL — the inventory look-up above is what tied this SKU to the ASIN.)
        if _norm(sku) not in _norm(st.get("sku")):
            out["note"] = f"the page shows another listing ({st.get('sku') or '?'} / {st.get('asin') or '?'})"
            return out

        if not st.get("field") and st.get("editCard"):                    # shape B: open the Source Offer panel
            if not _press(driver, 'kat-button[data-testid="card-edit-action"]'):
                out["note"] = "the Source Offer card's Edit button could not be pressed"
                return out
            st = _wait(driver, fid, lambda s: s.get("field") or (field != "quantity" and s.get("panelOpen")), 15, trace, "panel")
        if not st.get("field"):                                           # handling time hides under "Recommended"
            _show_all_attributes(driver, in_panel=bool(st.get("panelOpen")))
            st = _wait(driver, fid, lambda s: s.get("field"), 12, trace, "all attributes")
        f = st.get("field")
        if not f:
            out["note"] = f"the {field.replace('_', ' ')} field is not on the page"
            return out
        if f.get("disabled"):
            out["note"] = f"the {field.replace('_', ' ')} field is locked on this listing"
            return out

        prev = str(f.get("value") or "").strip()
        out["previous"] = prev
        if only_if is not None and prev != str(only_if):
            out.update(skipped=True, note=f"the {'handling time' if field == 'handling_days' else field} is "
                                          f"{prev or 'empty'}, not {only_if} — "
                                          f"changed by hand since, left alone")
            return out
        if field == "quantity" and prev.isdigit() and int(prev) == int(want) and not force:
            out.update(ok=True, note=f"quantity was already {want}")
            return out
        if field == "handling_days" and prev.isdigit() and not force and (
                int(prev) == int(want) or (int(prev) > int(want) and not lower_ok)):
            out.update(ok=True, note=f"handling time was already {prev} days")
            return out
        if field == "handling_days" and raise_from is not None and not force \
                and prev not in {str(v).strip() for v in raise_from}:
            out.update(skipped=True, kept="hand_set",
                       note=f"the handling time is {prev or 'empty'} days, set by hand — left alone")
            return out

        got = _type(driver, fid, want)
        if got.strip() != want:
            out["note"] = f"typed {want} but the field shows {got!r} — nothing saved"
            return out
        if dry_run:
            out.update(ok=True, note=f"dry run: {prev or 'default'} → {want} typed and read back, not saved")
            return out

        if f.get("inPanel"):                                              # shape B: the panel's own Save first
            if not _press_when_enabled(driver, 'kat-button[data-testid="kat-button-save"]', within_panel=True):
                out["note"] = "the panel's Save button could not be pressed — nothing saved"
                return out
            st = _wait(driver, fid, lambda s: not s.get("panelOpen"), 12, trace, "panel saved")
            if st.get("panelOpen"):
                out["note"] = "the panel did not close after Save" + (f": {st['alerts'][0]}" if st.get("alerts") else "") + " — nothing submitted"
                return out

        # The form validates every change: the button reads "Validating..." and a click then is lost.
        # Go on only when the side bar counts the edit ("Offer 1 Edit") and the button is at rest
        # again ("Submit Edits"). 10 Nov 2026: a click sent right after typing saved nothing.
        st = _wait(driver, fid, _ready_to_submit, 25, trace, "edit registered")
        if not _ready_to_submit(st):
            out["note"] = ("the form did not register the change" if "no edits" in str(st.get("edits") or "").lower()
                           else "the form never finished validating") + \
                (f": {st['alerts'][0]}" if st.get("alerts") else "") + " — nothing saved"
            return out
        if not submit:
            out.update(ok=True, note=f"ready to submit ({st.get('edits')}), stopped before Submit — nothing saved")
            return out
        if not _press(driver, "#form-submit-button"):
            out["note"] = "the Submit button could not be pressed — nothing saved"
            return out
        done, why = _await_submitted(driver, fid, trace)
        if not done:
            out["note"] = f"submitted, but Seller Central did not confirm it ({why})"
            return out
        out.update(ok=True, changed=True,
                   note=(f"quantity {prev or '?'} → {want}" if field == "quantity"
                         else f"handling time {prev or 'default'} → {want} days"))
        return out
    except Exception as e:
        out["note"] = f"{type(e).__name__}: {str(e).splitlines()[0][:140] if str(e) else ''}"
        return out
    finally:
        if dry_run or not submit:         # leave the page so the typed value cannot be saved by accident
            try:
                driver.get("https://sellercentral.amazon.com/myinventory/inventory")
            except Exception:
                pass


def account_default_handling_days(driver) -> Optional[int]:
    """The store's own default handling time — what a listing with an EMPTY handling time gets — read
    from Seller Central → Shipping settings → General ("Default handling time 1 day"). None when the
    page does not say. Needed to put back a listing whose handling time was empty before: emptying
    the field again changes nothing on Amazon, so the number itself has to be set."""
    import re
    try:
        driver.get("https://sellercentral.amazon.com/fbm/general-setting")
        end = time.time() + 20
        while time.time() < end:
            time.sleep(1)
            txt = driver.execute_script("return (document.body.innerText || '').replace(/\\s+/g, ' ');") or ""
            m = re.search(r"Default handling time\s+(\d+)\s+day", txt, re.I)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None


def _await_submitted(driver, fid: str, trace: Optional[List] = None):
    """After Submit: (True, its words) once Seller Central shows "All done. Thanks for submitting your
    changes. If approved, changes will be reflected within 24 hours." — else (False, what it showed)."""
    t0 = time.time()
    end = t0 + 40
    last: Dict[str, Any] = {}
    while time.time() < end:
        time.sleep(1.5)
        try:
            last = _state(driver, fid)
        except Exception:
            continue
        if trace is not None:
            snap = {k: last.get(k) for k in ("path", "edits", "submit", "saved", "alerts", "toasts", "modals")}
            if not trace or trace[-1][1] != snap:          # only what changed since the last look
                trace.append((f"after submit +{time.time() - t0:.0f}s", snap))
        if last.get("saved"):
            return True, last["saved"]          # "All done. Thanks for submitting your changes. …"
        if last.get("alerts"):
            return False, last["alerts"][0][:120]    # the form refused it and says why
    return False, (last.get("alerts") or [last.get("body") or "no change on the page"])[0][:120]


if __name__ == "__main__":
    import argparse, json, os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap = argparse.ArgumentParser(description="Set one offer field of one listing on Seller Central")
    ap.add_argument("--port", type=int, required=True, help="a store's signed-in Seller Central Chrome (9331 HOANK, 9333 Hathoro, 9337 Wrenchly)")
    ap.add_argument("--asin", required=True)
    ap.add_argument("--sku", default="", help="seller SKU; without it the ASIN must have exactly one listing")
    ap.add_argument("--field", choices=sorted(FIELDS), required=True)
    ap.add_argument("--value", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true", help="type and read back, save nothing")
    ap.add_argument("--no-submit", action="store_true", help="everything up to the Submit button, save nothing")
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--force", action="store_true", help="type and submit even when the value is already there")
    ap.add_argument("--lower-ok", action="store_true", help="let a handling time go down")
    ap.add_argument("--only-if", default=None, help="change the field only while it still holds this value")
    a = ap.parse_args()
    import closing
    from utils.sc_chrome import sc_turn
    tr: List = []
    with sc_turn(a.port, f"offer edit {a.asin}", max_wait=120, on_timeout="skip"):
        d = closing.ensure_chrome(a.port, headless=False)
        try:
            res = set_offer_field(d, a.asin, a.sku, a.field, a.value, dry_run=a.dry_run, trace=tr if a.trace else None,
                                  force=a.force, submit=not a.no_submit, lower_ok=a.lower_ok, only_if=a.only_if)
        finally:
            try:
                d.service.stop()          # never quit(): that would close the signed-in tabs
            except Exception:
                pass
    for tag, st in tr:
        print(f"[{tag}] {json.dumps(st)[:600]}")
    print("RESULT:", json.dumps(res))
