#!/usr/bin/env python3
"""
priority_check.py — keeps Priority_ASIN_Control sorted automatically.

WHY: the sheet decides which profile handles an ASIN's orders (Coupon / Reseller /
Deciding per store). New ASINs arrive every day and the priority coupons change
every week, so sorting it by hand does not scale. The bot now sorts it:

  • the SUPERVISOR (engine/orders.py) decides every unlabelled order on the live
    cart and calls record_verdict() → the ASIN lands in the right column at once;
  • this module's check_one() tests an ASIN without an order (PDP badge → brand →
    priority coupons on a real cart) — used for the 6-hourly scan of new ASINs,
    the dashboard button, and the RE-CHECK QUEUE that is filled whenever a NEW
    priority coupon appears in the Coupons tab (every coupon-dependent ASIN of the
    store is re-tested with the new coupons; only permanent PDP-ineligible ones skip).

The rules (owner's, September 2026):
  PDP says "Zoro's best price: not eligible for any other offers or promotions"
                                        → Reseller, permanent (no coupon test needed)
  a PRIORITY coupon applies (15–20 %)   → Coupon — for Zoro Select only when it saves MORE
                                          than the reseller's 10 % Select discount (owner,
                                          28 Sep 2026: a 10 % code such as ORGANIZE… saves
                                          the same → Reseller; 15 % → Coupon)
  no priority coupon, Zoro Select       → Reseller (already 10 % off; a 10 % regular
                                          coupon would be wasted)
  no priority coupon, unit price ≥ $50  → Coupon  (regular 10 % coupon is always worth it)
  no priority coupon, unit price < $50  → Deciding (per order: quantity may push the cart
                                          over $50, or a thin margin → coupon; else reseller)

Verdicts are written to a Check_* block in the same tab (created on first run) and
the ASIN is MOVED into the suggested column when automation.priority_check.auto_assign
is on (default). Standalone use:

    python engine/priority_check.py HS_COUPON [--all] [--max 40] [--no-move]

(refuses to run while that profile's placer is running — one Zoro cart per account).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)

_COUPON_COL_MIN_DEFAULT = 50.0


def coupon_column_min() -> float:
    """Unit price at which an ASIN belongs in _Coupon rather than _Deciding.

    The same number the supervisor uses for the column (engine/orders.py: SUPERVISE_MIN_CART,
    bot_config routing.supervise_min_cart_for_regular). Read on each call so a change in the
    config does not need a restart of the checker."""
    try:
        with open(os.path.join(AUTO, "bot_config.json"), encoding="utf-8") as fh:
            routing = (json.load(fh) or {}).get("routing") or {}
        return float(routing.get("supervise_min_cart_for_regular", _COUPON_COL_MIN_DEFAULT)
                     or _COUPON_COL_MIN_DEFAULT)
    except Exception:
        return _COUPON_COL_MIN_DEFAULT
if AUTO not in sys.path:
    sys.path.insert(0, AUTO)

from utils.config import GOOGLE_SHEET_ID, GOOGLE_SHEET_PRIORITY_ASIN_TAB  # noqa: E402

TAB = GOOGLE_SHEET_PRIORITY_ASIN_TAB
CHECK_COLS = ["Check_ASIN", "Check_Status", "Check_Coupon", "Check_Cart", "Check_Suggest",
              "Check_At", "Check_Profile", "Check_Note"]
RECHECK_DAYS = 7
CART_THRESHOLD = 50.0
DATA = os.path.join(AUTO, "data")
from utils.jsonstore import mutate_json, read_json, write_json_atomic, locked  # noqa: E402
STATE_PATH = os.path.join(DATA, "priority_check_state.json")
PERMANENT = ("PROMO_INELIGIBLE",)          # never re-queued automatically (PDP badge)


# ── sheet helpers ─────────────────────────────────────────────────────────
def _col_letter(idx: int) -> str:
    s, idx = "", idx + 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def store_prefix(profile_name: str) -> str:
    return "DM" if profile_name.upper().startswith("DM") else "HS"


def suggested_column(profile_name: str, suggest: str) -> str:
    pref = store_prefix(profile_name)
    return {"Coupon": f"{pref}_Coupon", "Reseller": f"{pref}_Reseller",
            "Deciding": f"{pref}_Deciding"}.get(suggest, "")


def read_tab(svc):
    res = svc.spreadsheets().values().get(spreadsheetId=GOOGLE_SHEET_ID,
                                          range=f"'{TAB}'!A1:AZ500").execute()
    rows = res.get("values", []) or []
    headers = [h.strip() for h in (rows[0] if rows else [])]
    return headers, rows


# ── grid size + write serialisation ───────────────────────────────────────
# The tab was created with 6 columns × 101 rows. Any write beyond that grid is a
# 400 from the Sheets API (that is why the first day's Check_* writes all failed),
# so every write first makes sure the grid is big enough. Writes from the two
# coupon profiles are serialised per process; each store only touches its own
# columns, and Check_* rows are upserted one ASIN at a time (never the whole
# block), so concurrent placers cannot overwrite each other's verdicts.
import threading as _threading
_write_lock = _threading.Lock()
_grid = {"sheetId": None, "rows": 0, "cols": 0}


def _sheet_grid(svc, refresh=False):
    if _grid["sheetId"] is None or refresh:
        meta = svc.spreadsheets().get(spreadsheetId=GOOGLE_SHEET_ID, fields="sheets.properties").execute()
        for s in meta.get("sheets", []):
            p = s.get("properties", {})
            if p.get("title") == TAB:
                gp = p.get("gridProperties", {})
                _grid.update(sheetId=p.get("sheetId"), rows=int(gp.get("rowCount", 0)), cols=int(gp.get("columnCount", 0)))
    return _grid


def ensure_grid(wsvc, min_cols: int = 0, min_rows: int = 0) -> None:
    """Grow the tab (append columns/rows) so a write at (min_cols, min_rows) fits."""
    g = _sheet_grid(wsvc)
    reqs = []
    if min_cols > g["cols"]:
        reqs.append({"appendDimension": {"sheetId": g["sheetId"], "dimension": "COLUMNS", "length": min_cols - g["cols"] + 2}})
    if min_rows > g["rows"]:
        reqs.append({"appendDimension": {"sheetId": g["sheetId"], "dimension": "ROWS", "length": min_rows - g["rows"] + 100}})
    if reqs:
        wsvc.spreadsheets().batchUpdate(spreadsheetId=GOOGLE_SHEET_ID, body={"requests": reqs}).execute()
        _sheet_grid(wsvc, refresh=True)


def ensure_check_headers(wsvc, headers: list) -> list:
    """Create the Check_* header block (one blank separator column after the last
    existing header) if it is missing. Returns the updated header list."""
    if "Check_ASIN" in headers:
        return headers
    start = len(headers) + 1
    ensure_grid(wsvc, min_cols=start + len(CHECK_COLS), min_rows=2)
    rng = f"'{TAB}'!{_col_letter(start)}1:{_col_letter(start + len(CHECK_COLS) - 1)}1"
    wsvc.spreadsheets().values().update(spreadsheetId=GOOGLE_SHEET_ID, range=rng, valueInputOption="RAW",
                                        body={"values": [CHECK_COLS]}).execute()
    return headers + [""] + CHECK_COLS


def upsert_check_row(wsvc, headers: list, rows: list, row: dict) -> int:
    """Write ONE ASIN's Check_* row: over its existing row if the ASIN is already
    in the block, else on the first free row of the block. Returns the sheet row."""
    ci = headers.index("Check_ASIN")
    asin = (row.get("Check_ASIN") or "").strip().upper()
    target = None
    last = 1
    for ri, r in enumerate(rows[1:], start=2):
        v = (r[ci] if ci < len(r) else "") or ""
        if v.strip():
            last = ri
            if v.strip().upper() == asin and target is None:
                target = ri
    if target is None:
        target = last + 1
    ensure_grid(wsvc, min_cols=ci + len(CHECK_COLS), min_rows=target)
    rng = f"'{TAB}'!{_col_letter(ci)}{target}:{_col_letter(ci + len(CHECK_COLS) - 1)}{target}"
    wsvc.spreadsheets().values().update(spreadsheetId=GOOGLE_SHEET_ID, range=rng, valueInputOption="RAW",
                                        body={"values": [[str(row.get(c, "") or "") for c in CHECK_COLS]]}).execute()
    return target


def collect_asins(headers, rows, profile_name: str) -> dict:
    """ASIN → {"column": header} for this store's columns (+ any inbox column)."""
    pref = store_prefix(profile_name)
    wanted = [h for h in headers if h.upper().startswith(pref + "_")
              or h.lower() in ("new_asins", "new", "inbox", "to_check")]
    out = {}
    for h in wanted:
        ci = headers.index(h)
        for r in rows[1:]:
            if ci < len(r):
                v = (r[ci] or "").strip().upper()
                if v.startswith("B") and len(v) == 10 and v not in out:
                    out[v] = {"column": h}
    return out


def read_check_block(headers, rows) -> dict:
    if "Check_ASIN" not in headers:
        return {}
    idx = {c: headers.index(c) for c in CHECK_COLS if c in headers}
    out = {}
    a_i = idx["Check_ASIN"]
    for r in rows[1:]:
        if a_i < len(r) and (r[a_i] or "").strip():
            out[r[a_i].strip().upper()] = {c: (r[i] if i < len(r) else "") for c, i in idx.items()}
    return out


def write_check_block(wsvc, headers, results: dict) -> None:
    """(Re)write the whole Check_* block — header + one row per ASIN, newest first.
    Only the block's own columns are touched."""
    start = headers.index("Check_ASIN") if "Check_ASIN" in headers else len(headers) + 1
    rows = [CHECK_COLS]
    for d in sorted(results.values(), key=lambda d: d.get("Check_At", ""), reverse=True):
        rows.append([str(d.get(c, "") or "") for c in CHECK_COLS])
    rows += [[""] * len(CHECK_COLS) for _ in range(max(0, 450 - len(rows)))]
    rng = f"'{TAB}'!{_col_letter(start)}1:{_col_letter(start + len(CHECK_COLS) - 1)}{len(rows)}"
    wsvc.spreadsheets().values().update(spreadsheetId=GOOGLE_SHEET_ID, range=rng,
                                        valueInputOption="RAW", body={"values": rows}).execute()


def move_asin(wsvc, headers, rows, asin: str, to_col: str, store: str, profile_name: str = "", note_reason: str = "") -> bool:
    """Put the ASIN in `to_col` exactly once and blank it in every OTHER column of
    this store, including duplicate rows (idempotent: nothing happens if it is
    already only there, once).
    Also writes a Google Sheets Cell Note: 'ZUHRA'
    """
    if to_col not in headers:
        return False
    ti = headers.index(to_col)
    data = []
    target_cell = None
    kept = False
    for h in headers:
        if not h.upper().startswith(store + "_"):
            continue
        ci = headers.index(h)
        for ri, r in enumerate(rows[1:], start=2):
            if ci < len(r) and (r[ci] or "").strip().upper() == asin.upper():
                if h == to_col and not kept:
                    kept = True                      # first occurrence in the target stays
                    target_cell = (ci, ri)
                else:
                    data.append({"range": f"'{TAB}'!{_col_letter(ci)}{ri}", "values": [[""]]})
    if not kept:
        tr = 2
        for ri, r in enumerate(rows[1:], start=2):
            if ti < len(r) and (r[ti] or "").strip():
                tr = ri + 1
        ensure_grid(wsvc, min_cols=ti + 1, min_rows=tr)
        data.append({"range": f"'{TAB}'!{_col_letter(ti)}{tr}", "values": [[asin.upper()]]})
        target_cell = (ti, tr)
    if not data:
        return False
    wsvc.spreadsheets().values().batchUpdate(spreadsheetId=GOOGLE_SHEET_ID,
                                             body={"valueInputOption": "RAW", "data": data}).execute()

    # Attach ZUHRA attribution note to the target cell
    if target_cell is not None and not kept:
        try:
            g = _sheet_grid(wsvc)
            sheet_id = g.get("sheetId")
            if sheet_id is not None:
                ci, ri = target_cell
                note_req = {
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": ri - 1,
                            "endRowIndex": ri,
                            "startColumnIndex": ci,
                            "endColumnIndex": ci + 1
                        },
                        "rows": [{
                            "values": [{
                                "note": ("ZUHRA · " + _now() + (" · " + note_reason[:180] if note_reason else "")
                                         + (" · by " + profile_name if profile_name else ""))
                            }]
                        }],
                        "fields": "note"
                    }
                }
                wsvc.spreadsheets().batchUpdate(
                    spreadsheetId=GOOGLE_SHEET_ID,
                    body={"requests": [note_req]}
                ).execute()
        except Exception:
            pass  # Best effort note stamping

    return True


def _auto_assign(mod) -> bool:
    try:
        return bool(((mod.gui_cfg.get("automation") or {}).get("priority_check") or {}).get("auto_assign", True))
    except Exception:
        return True


def _sheet_notes(mod) -> bool:
    """Write the Check_* provenance block into the sheet? OFF by default — the sheet
    stays the six columns the team knows; the bot's memory lives in
    data/priority_verdicts.json and the decision log / dashboard show the reasons."""
    try:
        return bool(((mod.gui_cfg.get("automation") or {}).get("priority_check") or {}).get("write_sheet_notes", False))
    except Exception:
        return False


# ── local verdict memory (replaces the sheet's Check_* block) ─────────────
VERDICTS_PATH = os.path.join(DATA, "priority_verdicts.json")
_vlock = __import__("threading").Lock()


def load_verdicts() -> dict:
    """asin -> {status, suggest, coupon, cart, at, profile, note, column}"""
    try:
        with open(VERDICTS_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def clear_verdicts(keep_permanent: bool = True) -> int:
    """Clear local verdict cache when Google Sheet is wiped or on coupon reset.
    Ensures Google Sheets remains the sole source of truth and local cache never
    resurrects or locks out fresh testing. Keeps PDP-ineligible if keep_permanent=True."""
    with _vlock, mutate_json(VERDICTS_PATH, {}) as v:
        count = len(v)
        if keep_permanent:
            kept = {k: val for k, val in v.items() if (val.get("status") == "PROMO_INELIGIBLE" and "best price" in (val.get("note") or "").lower())}
            v.clear()
            v.update(kept)
            return count - len(kept)
        else:
            v.clear()
            return count


def save_verdict(asin: str, row: dict, column: str = "", owner: str | None = None) -> None:
    """owner: "bot" when the bot put the ASIN in `column`, "human" when a person did.
    None keeps the previous owner (or "human" for an ASIN the bot never placed).
    Cross-process safe: HS_COUPON and DM_COUPON share this file."""
    with _vlock, mutate_json(VERDICTS_PATH, {}) as v:
        prev = v.get(asin.upper()) or {}
        if owner is None:                       # keep what we knew; an ASIN the bot never placed is a human's
            owner = prev.get("owner") or "human"
        v[asin.upper()] = {"status": row.get("Check_Status"), "suggest": row.get("Check_Suggest"),
                           "coupon": row.get("Check_Coupon", ""), "cart": row.get("Check_Cart", ""),
                           "at": row.get("Check_At") or _now(), "profile": row.get("Check_Profile", ""),
                           "note": row.get("Check_Note", ""), "column": column, "owner": owner}


def may_move(status: str, current_col: str, bot_owned: bool, mode: str = "new") -> bool:
    """Move policy for an ASIN that is ALREADY in a column.
    mode: "new" (scheduled check of new ASINs) · "live" (supervisor after a real order)
          · "recheck" (a new priority coupon arrived) · "all" (dashboard Re-check all)
          · "reset" (dashboard Reset & rebuild).
      • a priority coupon works (COUPON_OK)          → yes, promote to Coupon (the owner's rule)
      • the product page says ineligible             → yes, Reseller (coupons can never apply)
      • the bot placed it there itself (bot_owned)   → yes, it may re-sort its own placement
      • reset                                        → yes, everything is rebuilt from scratch
      • recheck / all and it sits in _Coupon          → yes, coupon-dependent ASINs are re-sorted
                                                       when the coupon set changes
      • a human placed it in _Reseller / _Deciding   → NO — record the verdict, leave the column
    An ASIN in no column (current_col empty) is always placed."""
    if not current_col:
        return True
    if status in ("COUPON_OK", "PROMO_INELIGIBLE"):
        return True
    if bot_owned or mode == "reset":
        return True
    if mode in ("live", "recheck", "all", "sweep_added", "sweep_removed") and current_col.upper().endswith("_COUPON"):
        return True
    return False


def _bot_owned(prev: dict, current_col: str) -> bool:
    return bool(current_col) and prev.get("owner") == "bot" and (prev.get("column") or "").upper() == current_col.upper()


def is_permanent(asin: str, sku_map: dict | None = None, verdicts: dict | None = None) -> bool:
    """PDP-badge ineligible ASINs never need a re-test."""
    v = (verdicts if verdicts is not None else load_verdicts()).get(asin.upper()) or {}
    if v.get("status") == "PROMO_INELIGIBLE" and "best price" in (v.get("note") or "").lower():
        return True
    try:
        from platforms.zoro import pdp_promo_ineligible
        code = ((sku_map or {}).get(asin.upper()) or {}).get("zoro_code")
        return bool(code and pdp_promo_ineligible(code))
    except Exception:
        return False


def record_verdict(profile_name: str, asin: str, status: str, coupon: str, cart: str, suggest: str,
                   note: str = "", mod=None, move=None) -> dict:
    """Write ONE verdict (used by the supervisor after a real order): Check_* row +
    move the ASIN into the suggested column. Safe to call from a thread."""
    if mod is None:
        from engine import orders as mod  # noqa: F811
    with _write_lock:
        svc, wsvc = mod._get_sheets_service(), mod._get_sheets_write_service()
        headers, rows = read_tab(svc)
        if not headers:
            return {"ok": False, "error": "sheet empty"}
        row = {"Check_ASIN": asin, "Check_Status": status, "Check_Coupon": coupon or "",
               "Check_Cart": cart or "", "Check_Suggest": suggest, "Check_At": _now(),
               "Check_Profile": profile_name, "Check_Note": note or ""}
        moved = False
        col = suggested_column(profile_name, suggest)
        cur = (collect_asins(headers, rows, profile_name).get(asin.upper()) or {}).get("column", "")
        prev = load_verdicts().get(asin.upper()) or {}
        blocked = bool(col) and bool(cur) and col != cur and not may_move(status, cur, _bot_owned(prev, cur), "live")
        if blocked:
            print(f"    · {asin} stays in {cur} (human placement; no priority coupon) — verdict recorded only")
            col = cur
        if (move if move is not None else _auto_assign(mod)) and col and col != cur:
            try:
                reason = f"{status} / {suggest}: {note}" if note else f"{status} / {suggest}"
                moved = move_asin(wsvc, headers, rows, asin, col, store_prefix(profile_name),
                                  profile_name=profile_name, note_reason=reason)
            except Exception as e:
                print(f"    ⚠️ move {asin} → {col}: {e}")
        owner = "bot" if moved else ("human" if blocked else None)
        save_verdict(asin, row, col if (moved or col == cur) else cur, owner)   # the bot's own memory
        if _sheet_notes(mod):                       # optional: mirror it into the sheet
            had_block = "Check_ASIN" in headers
            headers = ensure_check_headers(wsvc, headers)
            if moved or not had_block:
                headers, rows = read_tab(svc)
            upsert_check_row(wsvc, headers, rows, row)
    print(f"  📋 sheet: {asin} → {status} / {suggest}" + (f" (moved to {col})" if moved else ""))
    return {"ok": True, "moved": moved}


# ── one ASIN ──────────────────────────────────────────────────────────────
def check_one(driver, asin: str, sku_map: dict, priority_codes: list, cache=None, mod=None) -> dict:
    """Test ONE ASIN on the live Zoro session (no order involved)."""
    from platforms.zoro import check_inventory, clear_cart_and_add, clear_cart_selenium, check_pdp_promo, get_product_brand
    from checkout.true_api import _get_cart_id, _apply_coupon_api, classify_promo_error

    row = {"Check_ASIN": asin, "Check_At": _now(), "Check_Coupon": "", "Check_Cart": "", "Check_Note": ""}
    m = sku_map.get(asin) or {}
    code = (m.get("zoro_code") or "").strip()
    if not code:
        row.update(Check_Status="UNMAPPED", Check_Suggest="Map it first", Check_Note="not in Verified_SKU_Dataset")
        return row
    zoro_session_ready(driver)                      # never call Zoro's API from chrome://new-tab-page
    try:
        qty = max(1, int(float(m.get("qty_multiplier") or 1) * int(m.get("zoro_multiple") or 1)))
    except Exception:
        qty = 1
    # 0) PDP badge — permanent, no coupon test needed
    badge = check_pdp_promo(driver, code)
    if badge:
        if mod is not None:
            try:
                mod._learn_promo_outcome(code, asin, False, cache=cache, definitive=True, source="pdp")
            except Exception:
                pass
        row.update(Check_Status="PROMO_INELIGIBLE", Check_Suggest="Reseller",
                   Check_Note=f"{code}: Zoro's best price — not eligible for any offers or promotions")
        return row
    # 1) brand (sheet first, PDP microdata second)
    brand = (m.get("brand") or "").strip()
    if not brand:
        try:
            brand = get_product_brand(driver, code) or ""
        except Exception:
            brand = ""
    is_select = "zoro select" in brand.lower()
    # 2) stock + price via a real cart
    cartable, status = check_inventory(driver, code, qty)
    if not cartable:
        if status in ("API_ERROR", "NO_DATA"):
            row.update(Check_Status="TRANSIENT", Check_Suggest="Re-check later", Check_Note=f"{code}: inventory API {status}")
        else:
            row.update(Check_Status="OOS", Check_Suggest="Wait / alt code", Check_Note=f"{code}: {status}")
        return row
    ok, price = clear_cart_and_add(driver, code, qty)
    if not ok:
        row.update(Check_Status="TRANSIENT", Check_Suggest="Re-check later", Check_Note=f"{code}: cart add failed")
        return row
    unit = float(price or 0)
    cart = round(unit * qty, 2)
    row["Check_Cart"] = f"${cart:.2f} ({code} x{qty} @ ${unit:.2f})" + (" · Zoro Select" if is_select else "")
    # 3) priority coupons on the live cart (regular 10 % coupons are never tested here —
    #    they always exist; whether they are worth it is a price/quantity question)
    cart_id = _get_cart_id(driver)
    tried = transient = item_rejects = 0
    worked = None
    matched = None          # (code, discount): applies to a Zoro Select item but saves only the Select 10 %
    min_order = None
    known_min = (cache or {}).get("coupon_min_order") or {}
    if cart_id:
        from utils.config import coupon_beats_select
        for c in priority_codes:
            km = known_min.get(c)
            if km and float(km) > cart + 0.005:      # known minimum above this cart: no API call needed
                tried += 1
                min_order = min(min_order or float(km), float(km))
                continue
            tried += 1
            okc, res = _apply_coupon_api(driver, cart_id, c)
            if okc and (res or 0) > 0:
                if is_select and not coupon_beats_select(res, cart):
                    # Owner, 28 Sep 2026: equal money (a 10 % code vs the reseller's 10 % Select
                    # discount) → Reseller; keep looking for a code that saves more.
                    matched = matched or (c, float(res))
                    continue
                worked = c
                break
            kind, extra = classify_promo_error(res)
            if kind in ("token", "transient"):
                transient += 1
            elif kind == "item":
                item_rejects += 1
            elif kind == "min_order" and extra:
                min_order = min(min_order or extra, extra)
    try:
        from checkout.true_api import _clear_cart_api
        if not _clear_cart_api(driver):
            clear_cart_selenium(driver)
    except Exception:
        try:
            clear_cart_selenium(driver)
        except Exception:
            pass
    sel_note = "Zoro Select" if is_select else f"unit ${unit:.2f}"
    if worked:
        row["Check_Coupon"] = f"{worked} (priority)"
        row.update(Check_Status="COUPON_OK", Check_Suggest="Coupon", Check_Note=f"priority {worked} works · {sel_note}")
        if mod is not None:
            try:
                mod._learn_promo_outcome(code, asin, True, cache=cache)
            except Exception:
                pass
    elif tried and transient == tried:
        row.update(Check_Status="TRANSIENT", Check_Suggest="Re-check later", Check_Note="coupon API busy / session")
    elif is_select:
        if matched:
            row["Check_Coupon"] = f"{matched[0]} (= Select 10 %)"
            row.update(Check_Status="SELECT", Check_Suggest="Reseller",
                       Check_Note=f"Zoro Select; priority {matched[0]} saves ${matched[1]:.2f}, the same as the "
                                  f"reseller's 10 % Select discount → Reseller")
            if mod is not None:
                try:
                    mod._learn_promo_outcome(code, asin, True, cache=cache)   # coupons DO apply to it
                except Exception:
                    pass
        else:
            row.update(Check_Status="SELECT", Check_Suggest="Reseller",
                       Check_Note="Zoro Select, no priority coupon" + (f" (one applies from ${min_order:.0f})" if min_order else ""))
    else:
        # No priority coupon, not Zoro Select: the unit price decides the column, exactly as the
        # supervisor decides it after a real order (orders.py: col_by_price) and as the labelling
        # flow has said since 7 Sep 2026 — "_Coupon if unit is $50 or more, otherwise _Deciding".
        #
        # Between 14 and 29 Sep this branch answered Deciding whatever the price, on the argument
        # that the reseller hands a ≥$50 cart over per order anyway. It does — but only after
        # reserving the order, building the cart and writing MR, so every single order of a $316
        # or $89 item took a pointless trip through the reseller first, and the sweep kept pulling
        # those ASINs back out of _Coupon. 31 ASINs were sitting in _Deciding at a unit price
        # above the threshold when this was found.
        #
        # Below the threshold the answer is still Deciding: whether such an order needs a coupon
        # depends on the quantity and on the reseller's margin, and only the order knows those.
        _min = coupon_column_min()
        _big = unit >= _min
        row.update(Check_Status="NO_PRIORITY", Check_Suggest="Coupon" if _big else "Deciding",
                   Check_Note=(f"no priority coupon; unit ${unit:.2f} ≥ ${_min:.0f} → regular coupon every time"
                               if _big else
                               f"no priority coupon; unit ${unit:.2f} < ${_min:.0f} → per-order (qty / regular coupon / margin)")
                              + (f"; a priority coupon applies from ${min_order:.0f}" if min_order else ""))
    if not tried:
        row["Check_Note"] = "no priority coupons loaded; " + row["Check_Note"]
    return row


def zoro_session_ready(driver) -> bool:
    """Make sure the driver is on zoro.com with a live Okta token before any inventory / cart /
    promo API call. On a cold start the managed Chrome sits on chrome://new-tab-page and every
    API call answers 'no_token' (15 TRANSIENT verdicts in a row on 10 Sep)."""
    try:
        from platforms.zoro import ensure_on_site, ensure_token
        if "zoro.com" not in (driver.current_url or ""):
            ensure_on_site(driver)
        ensure_token(driver)
        return True
    except Exception as e:
        print(f"  ⚠️  Zoro session not ready: {e}")
        return False


# ── batches ───────────────────────────────────────────────────────────────
def _priority_codes(mod, coupon_codes, cache=None):
    """Priority codes to test, HIGHEST percentage first (20 % before 15 %), fixed-amount
    codes last. A fixed-amount code's sheet minimum (e.g. '10:50') seeds the known
    cart-minimum memory so the checker never wastes an attempt below it."""
    if coupon_codes is None:
        coupon_codes = mod.load_coupon_codes()
    seen, items = set(), []
    if isinstance(coupon_codes, tuple) and len(coupon_codes) == 2:
        for c in coupon_codes[0] or []:
            c = c or {}
            cd = str(c.get("code") or "").strip()
            if not cd:
                continue
            if cache is not None and c.get("min_order"):
                try:
                    mins = cache.setdefault("coupon_min_order", {})
                    mins[cd] = max(float(c["min_order"]), float(mins.get(cd) or 0))
                except Exception:
                    pass
            if cd in seen:
                continue
            seen.add(cd)
            pct = c.get("pct")
            items.append((0 if pct is not None else 1, -float(pct or 0), len(items), cd))
    items.sort()
    return [cd for _, _, _, cd in items]


def _state_path(profile_name: str) -> str:
    return os.path.join(DATA, f"priority_check_state_{profile_name}.json")


def _run_batch(driver, profile_name, asins_with_cols, sku_map, pri, cache, mod, svc, wsvc, headers, rows, move,
               mode: str = "new", on_done=None, current_cols=None):
    """Test each ASIN and apply the move policy. Reliability rules:
      • one ASIN's failure never aborts the rest (per-ASIN try/except, on_done always called);
      • the column an ASIN is judged against is its column NOW (current_cols), not the
        one recorded when it was queued days ago;
      • rows are re-read under the write lock right before a move, so a cell filled by
        the supervisor thread meanwhile is never overwritten;
      • with NO priority coupons loaded nothing is moved — a bad coupon-sheet read must
        never demote a store's Coupon column."""
    notes = _sheet_notes(mod)
    if notes:
        with _write_lock:
            headers = ensure_check_headers(wsvc, headers)
        headers, rows = read_tab(svc)
    if move and not pri:
        print("  ⚠️  no priority coupons loaded — verdicts are recorded but NOTHING is moved this run")
        move = False
    if not zoro_session_ready(driver):
        print("  ⏸️  Zoro session unavailable — nothing tested this run (ASINs stay queued)")
        return []
    results = []
    for i, (asin, col) in enumerate(asins_with_cols, 1):
        t0 = time.time()
        if i % 25 == 0:
            zoro_session_ready(driver)                # token refresh on long sweeps
        col = (current_cols or {}).get(asin, col) if current_cols is not None else col
        try:
            try:
                row = check_one(driver, asin, sku_map, pri, cache=cache, mod=mod)
            except Exception as e:
                row = {"Check_ASIN": asin, "Check_At": _now(), "Check_Status": "ERROR", "Check_Suggest": "Re-check later",
                       "Check_Coupon": "", "Check_Cart": "", "Check_Note": str(e)[:120]}
            row["Check_Profile"] = profile_name
            row["Check_Note"] = "; ".join(filter(None, [f"was in {col}" if col else "new", row.get("Check_Note", "")]))
            results.append(row)
            try:
                from utils import decision_log as _dl
                _dl.log("check", profile=profile_name, asin=asin, sku=((sku_map.get(asin) or {}).get("zoro_code") or ""),
                        action=row.get("Check_Status"), coupon=row.get("Check_Coupon", ""), reason=row.get("Check_Note", ""),
                        verdict={"status": row.get("Check_Status"), "suggest": row.get("Check_Suggest"), "note": row.get("Check_Note")},
                        cart_text=row.get("Check_Cart", ""), was_in=col, move=move, mode=mode, priority_codes=pri)
            except Exception:
                pass
            print(f"  [{i}/{len(asins_with_cols)}] {asin} ({col or 'new'}) → {row['Check_Status']} / "
                  f"{row.get('Check_Suggest', '')} {row.get('Check_Coupon', '')} {row.get('Check_Cart', '')}  ({time.time() - t0:.0f}s)")
            target = suggested_column(profile_name, row.get("Check_Suggest", ""))
            prev = load_verdicts().get(asin) or {}
            blocked = bool(move and target and col and target != col
                           and not may_move(row.get("Check_Status", ""), col, _bot_owned(prev, col), mode))
            if blocked:
                print(f"    · {asin} stays in {col} (human placement; no priority coupon) — verdict recorded only")
                target = col
            moved = False
            with _write_lock:
                if move and target and target != col:
                    try:
                        headers, rows = read_tab(svc)              # fresh: never trust a snapshot for a write
                        reason = f"{row.get('Check_Status', '')} / {row.get('Check_Suggest', '')}: {row.get('Check_Note', '')}"
                        moved = move_asin(wsvc, headers, rows, asin, target, store_prefix(profile_name),
                                          profile_name=profile_name, note_reason=reason)
                        if moved:
                            row["Check_Moved"] = f"{col or '—'} → {target}"
                            print(f"    ↪ {asin}: {col or '—'} → {target}")
                            headers, rows = read_tab(svc)
                    except Exception as e:
                        print(f"    ⚠️ move failed: {e}")
                try:
                    save_verdict(asin, row, target if (moved or not move or target == col) else col,
                                 "bot" if moved else ("human" if blocked else None))
                except Exception as e:
                    print(f"    ⚠️ verdict memory not written for {asin}: {e}")
                if notes:
                    try:
                        upsert_check_row(wsvc, headers, rows, row)
                        headers, rows = read_tab(svc)
                    except Exception as e:
                        print(f"    ⚠️ could not write the verdict row for {asin}: {e}")
        except Exception as e:
            print(f"    ⚠️ {asin}: {e} — skipped this time (it stays queued for the next run if it was queued)")
        finally:
            if on_done is not None:
                try:
                    on_done(asin)
                except Exception as e:
                    print(f"    ⚠️ queue bookkeeping failed for {asin}: {e}")
    print(f"  ✅ {len(results)} verdict(s) recorded" + (f" (+ Check_* columns in '{TAB}')" if notes else " in data/priority_verdicts.json"))
    try:
        write_json_atomic(_state_path(profile_name),
                          {"last_run": _now(), "profile": profile_name, "checked": len(results), "mode": mode,
                           "results": results}, indent=1)
    except Exception:
        pass
    return results


def run_check(driver, profile_name: str, sku_map=None, coupon_codes=None, cache=None, mod=None,
              only_new: bool = True, max_asins: int = 25, auto_assign=None) -> list:
    """Check this store's ASINs (new/stale ones, or all with only_new=False)."""
    if mod is None:
        from engine import orders as mod  # noqa: F811
    svc, wsvc = mod._get_sheets_service(), mod._get_sheets_write_service()
    headers, rows = read_tab(svc)
    if not headers:
        print("  ⚠️ priority check: sheet tab empty/unreadable")
        return []
    sku_map = sku_map if sku_map is not None else (mod.load_sku_map() or {})
    pri = _priority_codes(mod, coupon_codes, cache)
    move = _auto_assign(mod) if auto_assign is None else bool(auto_assign)
    existing = load_verdicts()                      # local memory, not the sheet
    targets = collect_asins(headers, rows, profile_name)
    cutoff = (datetime.now() - timedelta(days=RECHECK_DAYS)).strftime("%Y-%m-%d %H:%M")
    sheet_checks = read_check_block(headers, rows)
    if "Check_ASIN" in headers and not sheet_checks and existing:
        # Operator cleared the Check_* block in Google Sheets -> prune local non-permanent cache
        clear_verdicts(keep_permanent=True)
        existing = load_verdicts()
    todo = []
    for asin, meta in targets.items():
        prev = existing.get(asin)
        # If the sheet maintains a Check_* block and this ASIN is missing from it, the sheet was wiped/cleared for this ASIN:
        if "Check_ASIN" in headers and asin not in sheet_checks and not is_permanent(asin, sku_map, existing):
            pass
        elif only_new and prev and (prev.get("at") or "") >= cutoff and prev.get("status") not in (
                "TRANSIENT", "ERROR", "OOS", "UNMAPPED", "", None):
            # If a priority coupon is active and this ASIN was not tested under it, do NOT skip
            if pri and prev.get("coupon") not in pri and not is_permanent(asin, sku_map, existing):
                pass
            else:
                continue
        todo.append((asin, meta["column"]))
    # never-checked first, then the oldest verdicts — so a hand-added ASIN in the last
    # column is not starved behind a pile of transient re-tries
    todo.sort(key=lambda t: ((existing.get(t[0]) or {}).get("at") or ""))
    todo = todo[:max_asins]
    print(f"\n🧪 PRIORITY CHECK ({profile_name}): {len(targets)} ASIN(s) in sheet, {len(todo)} to test now, "
          f"{len(pri)} priority coupon(s), move={'on' if move else 'off'}")
    if not todo:
        return []
    return _run_batch(driver, profile_name, todo, sku_map, pri, cache, mod, svc, wsvc, headers, rows, move,
                      mode="new" if only_new else "all")


# ── re-check queue (new weekly coupon / reset) ────────────────────────────
def _queue_path(profile_name: str) -> str:
    return os.path.join(DATA, f"priority_recheck_{store_prefix(profile_name)}.json")


def queue_size(profile_name: str) -> int:
    """Pending ASINs; -1 when the file exists but cannot be parsed (run_queue rebuilds it)."""
    p = _queue_path(profile_name)
    if not os.path.exists(p):
        return 0
    q = read_json(p, None)                    # a corrupt file is moved aside → None
    if not isinstance(q, dict):
        return -1
    return len(q.get("asins") or [])


def enqueue_recheck(profile_name: str, reason: str, mod=None, include_ineligible: bool = False,
                    columns=None, mode: str | None = None) -> int:
    """Queue EVERY ASIN of this store for a re-test with the current priority coupons
    (a coupon change means every earlier verdict is stale, so the list is always the
    full store — resuming a half-done pass would miss the new coupon). Permanent
    PDP-ineligible ASINs are skipped unless include_ineligible (reset & rebuild), and
    a pending reset is never downgraded by a later coupon-change queue."""
    if mod is None:
        from engine import orders as mod  # noqa: F811
    headers, rows = read_tab(mod._get_sheets_service())
    targets = collect_asins(headers, rows, profile_name)
    try:
        sku_map = mod.load_sku_map() or {}
    except Exception:
        sku_map = {}
    p = _queue_path(profile_name)
    os.makedirs(DATA, exist_ok=True)
    with locked(p):
        prev_q = read_json(p, None)
        prev_reset = isinstance(prev_q, dict) and prev_q.get("mode") == "reset"
        include = include_ineligible or prev_reset
        verdicts = load_verdicts()
        wanted = tuple(c.upper() for c in (columns or ()))     # e.g. ("_RESELLER", "_DECIDING") or ("_COUPON",)
        asins = []
        for asin, meta in targets.items():
            if wanted and not any(str(meta["column"]).upper().endswith(w) for w in wanted):
                continue
            if not include and is_permanent(asin, sku_map, verdicts):
                continue
            asins.append([asin, meta["column"]])
        if isinstance(prev_q, dict) and prev_q.get("asins"):
            reason = f"{reason} (replaces pending: {prev_q.get('reason', '')})"
            # a wider pending queue must not shrink to a narrower one: keep its ASINs too
            if wanted:
                have = {a[0] for a in asins}
                for x in prev_q["asins"]:
                    a = x[0] if isinstance(x, list) else x
                    if a not in have:
                        asins.append([a, x[1] if isinstance(x, list) and len(x) > 1 else ""])
        write_json_atomic(p, {"reason": reason, "queued_at": _now(), "profile": profile_name, "asins": asins,
                              "mode": "reset" if include else (mode or "recheck"), "columns": list(columns or [])}, indent=1)
    return len(asins)


def enqueue_asins(profile_name: str, asins, reason: str, mode: str = "recheck") -> int:
    """Queue SPECIFIC ASINs for a re-test (no Chrome, no sheet read — the coupon placer drains
    the queue with its own session between cycles, judging each ASIN's column as it is then).
    Merged into a pending queue under its lock: nothing already queued is dropped and a pending
    reset is not downgraded. Used after a rule change to re-sort only the ASINs it affects
    (28 Sep 2026: Zoro Select ASINs promoted to Coupon by a 10 % priority code)."""
    wanted = [str(a).strip().upper() for a in (asins or []) if str(a).strip()]
    if not wanted:
        return 0
    p = _queue_path(profile_name)
    os.makedirs(DATA, exist_ok=True)
    with locked(p):
        q = read_json(p, None)
        if not isinstance(q, dict) or not q.get("asins"):
            q = {"reason": reason, "queued_at": _now(), "profile": profile_name, "asins": [],
                 "mode": mode, "columns": []}
        else:
            q["reason"] = f"{q.get('reason', '')} + {reason}"
        have = {(x[0] if isinstance(x, list) else x) for x in q["asins"]}
        added = 0
        for a in wanted:
            if a not in have:
                q["asins"].append([a, ""])
                have.add(a)
                added += 1
        write_json_atomic(p, q, indent=1)
    return added


def _queue_remove(p: str, asin: str) -> None:
    """Drop ONE finished ASIN from the queue file (atomic, under the lock) so a crash
    mid-batch loses at most the ASIN being tested, never the whole batch."""
    with locked(p):
        q = read_json(p, None)
        if not isinstance(q, dict):
            return
        q["asins"] = [x for x in (q.get("asins") or []) if (x[0] if isinstance(x, list) else x) != asin]
        write_json_atomic(p, q, indent=1)


def run_queue(driver, profile_name: str, sku_map=None, coupon_codes=None, cache=None, mod=None, max_asins: int = 15) -> list:
    """Drain up to max_asins from the re-check queue (called between placer cycles)."""
    if mod is None:
        from engine import orders as mod  # noqa: F811
    p = _queue_path(profile_name)
    if not os.path.exists(p):
        return []
    q = read_json(p, None, quarantine=True)          # a corrupt queue is kept aside, then rebuilt
    if not isinstance(q, dict):
        print(f"  ⚠️  re-check queue for {profile_name} was unreadable — rebuilding it (full store)")
        try:
            enqueue_recheck(profile_name, "queue file was unreadable — rebuilt", mod=mod)
        except Exception as e:
            print(f"  ⚠️  could not rebuild the queue: {e}")
            return []
        q = read_json(p, None) or {}
    pending = q.get("asins") or []
    if not pending:
        try:
            os.remove(p)
        except OSError:
            pass
        return []
    batch = pending[:max_asins]
    svc, wsvc = mod._get_sheets_service(), mod._get_sheets_write_service()
    headers, rows = read_tab(svc)
    current_cols = {a: m["column"] for a, m in collect_asins(headers, rows, profile_name).items()}
    sku_map = sku_map if sku_map is not None else (mod.load_sku_map() or {})
    pri = _priority_codes(mod, coupon_codes, cache)
    print(f"\n🔁 PRIORITY RE-CHECK ({profile_name}): {len(batch)} of {len(pending)} queued — {q.get('reason', '')}")
    if not pri:
        print("  ⏸️  no priority coupons loaded right now — the queue waits (nothing is tested or moved)")
        return []
    results = _run_batch(driver, profile_name, [tuple(x) for x in batch], sku_map, pri, cache, mod, svc, wsvc,
                         headers, rows, _auto_assign(mod), mode=q.get("mode") or "recheck",
                         on_done=lambda a: _queue_remove(p, a), current_cols=current_cols)
    left = queue_size(profile_name)
    if left == 0:
        print(f"  ✅ re-check queue drained ({q.get('reason', '')})")
        try:
            os.remove(p)
        except OSError:
            pass
    return results


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not argv or argv[0].upper() not in ("DM_COUPON", "HS_COUPON"):
        print("usage: python engine/priority_check.py DM_COUPON|HS_COUPON [--all] [--max N] [--no-move]\n"
              "       python engine/priority_check.py DM_COUPON|HS_COUPON --queue ASIN[,ASIN…] [--reason TEXT]\n"
              "         (queue only: no Chrome — the running coupon placer re-tests them between cycles)")
        sys.exit(2)
    if "--queue" in sys.argv:
        try:
            raw = sys.argv[sys.argv.index("--queue") + 1]
        except IndexError:
            raw = ""
        why = sys.argv[sys.argv.index("--reason") + 1] if "--reason" in sys.argv[:-1] else "targeted re-check"
        n = enqueue_asins(argv[0].upper(), raw.replace(" ", ",").split(","), why)
        print(f"queued {n} ASIN(s) for {argv[0].upper()} — {queue_size(argv[0].upper())} waiting in "
              f"{_queue_path(argv[0].upper())}")
        return
    only_new = "--all" not in sys.argv
    mx = 25
    if "--max" in sys.argv:
        try:
            mx = int(sys.argv[sys.argv.index("--max") + 1])
        except Exception:
            pass
    # engine.orders selects its profile from argv and takes the per-profile lock in
    # main() only, so importing it here is safe; connect_chrome attaches to the profile.
    from engine import orders as mod
    driver = mod.connect_chrome()
    if driver is None:
        print("❌ could not attach to the profile's Chrome")
        sys.exit(1)
    cache = mod._load_coupon_intel()
    run_check(driver, mod.PROFILE_NAME, cache=cache, mod=mod, only_new=only_new, max_asins=mx,
              auto_assign=(False if "--no-move" in sys.argv else None))


if __name__ == "__main__":
    main()
