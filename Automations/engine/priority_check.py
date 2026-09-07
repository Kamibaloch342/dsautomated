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
  a PRIORITY coupon applies (15–20 %)   → Coupon — even for Zoro Select, it beats 10 %
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
if AUTO not in sys.path:
    sys.path.insert(0, AUTO)

from utils.config import GOOGLE_SHEET_ID, GOOGLE_SHEET_PRIORITY_ASIN_TAB  # noqa: E402

TAB = GOOGLE_SHEET_PRIORITY_ASIN_TAB
CHECK_COLS = ["Check_ASIN", "Check_Status", "Check_Coupon", "Check_Cart", "Check_Suggest",
              "Check_At", "Check_Profile", "Check_Note"]
RECHECK_DAYS = 7
CART_THRESHOLD = 50.0
DATA = os.path.join(AUTO, "data")
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


def move_asin(wsvc, headers, rows, asin: str, to_col: str, store: str) -> bool:
    """Put the ASIN in `to_col` and blank it in every OTHER column of this store
    (idempotent: nothing happens if it is already only there)."""
    if to_col not in headers:
        return False
    ti = headers.index(to_col)
    data = []
    already = False
    for h in headers:
        if not h.upper().startswith(store + "_"):
            continue
        ci = headers.index(h)
        for ri, r in enumerate(rows[1:], start=2):
            if ci < len(r) and (r[ci] or "").strip().upper() == asin:
                if h == to_col:
                    already = True
                else:
                    data.append({"range": f"'{TAB}'!{_col_letter(ci)}{ri}", "values": [[""]]})
    if not already:
        tr = 2
        for ri, r in enumerate(rows[1:], start=2):
            if ti < len(r) and (r[ti] or "").strip():
                tr = ri + 1
        data.append({"range": f"'{TAB}'!{_col_letter(ti)}{tr}", "values": [[asin]]})
    if not data:
        return False
    wsvc.spreadsheets().values().batchUpdate(spreadsheetId=GOOGLE_SHEET_ID,
                                             body={"valueInputOption": "RAW", "data": data}).execute()
    return True


def _auto_assign(mod) -> bool:
    try:
        return bool(((mod.gui_cfg.get("automation") or {}).get("priority_check") or {}).get("auto_assign", True))
    except Exception:
        return True


def record_verdict(profile_name: str, asin: str, status: str, coupon: str, cart: str, suggest: str,
                   note: str = "", mod=None, move=None) -> dict:
    """Write ONE verdict (used by the supervisor after a real order): Check_* row +
    move the ASIN into the suggested column. Safe to call from a thread."""
    if mod is None:
        from engine import orders as mod  # noqa: F811
    svc, wsvc = mod._get_sheets_service(), mod._get_sheets_write_service()
    headers, rows = read_tab(svc)
    if not headers:
        return {"ok": False, "error": "sheet empty"}
    block = read_check_block(headers, rows)
    block[asin] = {"Check_ASIN": asin, "Check_Status": status, "Check_Coupon": coupon or "",
                   "Check_Cart": cart or "", "Check_Suggest": suggest, "Check_At": _now(),
                   "Check_Profile": profile_name, "Check_Note": note or ""}
    moved = False
    if (move if move is not None else _auto_assign(mod)):
        col = suggested_column(profile_name, suggest)
        if col:
            try:
                moved = move_asin(wsvc, headers, rows, asin, col, store_prefix(profile_name))
            except Exception as e:
                print(f"    ⚠️ move {asin} → {col}: {e}")
    write_check_block(wsvc, headers, block)
    print(f"  📋 sheet: {asin} → {status} / {suggest}" + (" (moved)" if moved else ""))
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
    min_order = None
    if cart_id:
        for c in priority_codes:
            tried += 1
            okc, res = _apply_coupon_api(driver, cart_id, c)
            if okc and (res or 0) > 0:
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
        row.update(Check_Status="SELECT", Check_Suggest="Reseller",
                   Check_Note="Zoro Select, no priority coupon" + (f" (one applies from ${min_order:.0f})" if min_order else ""))
    elif unit >= CART_THRESHOLD:
        row.update(Check_Status="NO_PRIORITY", Check_Suggest="Coupon",
                   Check_Note=f"no priority coupon; unit ≥ ${CART_THRESHOLD:.0f} → regular 10 %")
    else:
        row.update(Check_Status="NO_PRIORITY", Check_Suggest="Deciding",
                   Check_Note=f"no priority coupon; unit < ${CART_THRESHOLD:.0f} → per-order (qty / margin)"
                              + (f"; a priority coupon applies from ${min_order:.0f}" if min_order else ""))
    if not tried:
        row["Check_Note"] = "no priority coupons loaded; " + row["Check_Note"]
    return row


# ── batches ───────────────────────────────────────────────────────────────
def _priority_codes(mod, coupon_codes):
    if coupon_codes is None:
        coupon_codes = mod.load_coupon_codes()
    pri = []
    if isinstance(coupon_codes, tuple) and len(coupon_codes) == 2:
        for c in coupon_codes[0] or []:
            cd = str((c or {}).get("code") or "").strip()
            if cd and cd not in pri:
                pri.append(cd)
    return pri


def _run_batch(driver, profile_name, asins_with_cols, sku_map, pri, cache, mod, svc, wsvc, headers, rows, move):
    existing = read_check_block(headers, rows)
    results = []
    for i, (asin, col) in enumerate(asins_with_cols, 1):
        t0 = time.time()
        try:
            row = check_one(driver, asin, sku_map, pri, cache=cache, mod=mod)
        except Exception as e:
            row = {"Check_ASIN": asin, "Check_At": _now(), "Check_Status": "ERROR", "Check_Suggest": "Re-check later",
                   "Check_Coupon": "", "Check_Cart": "", "Check_Note": str(e)[:120]}
        row["Check_Profile"] = profile_name
        row["Check_Note"] = "; ".join(filter(None, [f"was in {col}" if col else "new", row.get("Check_Note", "")]))
        existing[asin] = row
        results.append(row)
        try:
            from utils import decision_log as _dl
            _dl.log("check", profile=profile_name, asin=asin, sku=((sku_map.get(asin) or {}).get("zoro_code") or ""),
                    action=row.get("Check_Status"), coupon=row.get("Check_Coupon", ""), reason=row.get("Check_Note", ""),
                    verdict={"status": row.get("Check_Status"), "suggest": row.get("Check_Suggest"), "note": row.get("Check_Note")},
                    cart_text=row.get("Check_Cart", ""), was_in=col, move=move, priority_codes=pri)
        except Exception:
            pass
        print(f"  [{i}/{len(asins_with_cols)}] {asin} ({col or 'new'}) → {row['Check_Status']} / "
              f"{row.get('Check_Suggest', '')} {row.get('Check_Coupon', '')} {row.get('Check_Cart', '')}  ({time.time() - t0:.0f}s)")
        if move:
            target = suggested_column(profile_name, row.get("Check_Suggest", ""))
            if target:
                try:
                    if move_asin(wsvc, headers, rows, asin, target, store_prefix(profile_name)):
                        print(f"    ↪ {asin}: {col or '—'} → {target}")
                        headers, rows = read_tab(svc)
                except Exception as e:
                    print(f"    ⚠️ move failed: {e}")
    try:
        write_check_block(wsvc, headers, existing)
        print(f"  ✅ verdicts written to '{TAB}' Check_* columns")
    except Exception as e:
        print(f"  ⚠️ could not write verdicts: {e}")
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"last_run": _now(), "profile": profile_name, "checked": len(results), "results": results}, f, indent=1)
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
    pri = _priority_codes(mod, coupon_codes)
    move = _auto_assign(mod) if auto_assign is None else bool(auto_assign)
    existing = read_check_block(headers, rows)
    targets = collect_asins(headers, rows, profile_name)
    cutoff = (datetime.now() - timedelta(days=RECHECK_DAYS)).strftime("%Y-%m-%d %H:%M")
    todo = []
    for asin, meta in targets.items():
        prev = existing.get(asin)
        if only_new and prev and prev.get("Check_At", "") >= cutoff and prev.get("Check_Status") not in ("TRANSIENT", "ERROR", ""):
            continue
        todo.append((asin, meta["column"]))
    todo = todo[:max_asins]
    print(f"\n🧪 PRIORITY CHECK ({profile_name}): {len(targets)} ASIN(s) in sheet, {len(todo)} to test now, "
          f"{len(pri)} priority coupon(s), move={'on' if move else 'off'}")
    if not todo:
        return []
    return _run_batch(driver, profile_name, todo, sku_map, pri, cache, mod, svc, wsvc, headers, rows, move)


# ── re-check queue (new weekly coupon / reset) ────────────────────────────
def _queue_path(profile_name: str) -> str:
    return os.path.join(DATA, f"priority_recheck_{store_prefix(profile_name)}.json")


def queue_size(profile_name: str) -> int:
    try:
        with open(_queue_path(profile_name), encoding="utf-8") as f:
            return len((json.load(f) or {}).get("asins") or [])
    except Exception:
        return 0


def enqueue_recheck(profile_name: str, reason: str, mod=None, include_ineligible: bool = False) -> int:
    """Queue every coupon-dependent ASIN of this store for a re-test. Permanent
    PDP-ineligible ASINs are skipped unless include_ineligible (reset & rebuild)."""
    if mod is None:
        from engine import orders as mod  # noqa: F811
    headers, rows = read_tab(mod._get_sheets_service())
    block = read_check_block(headers, rows)
    targets = collect_asins(headers, rows, profile_name)
    asins = []
    for asin, meta in targets.items():
        prev = block.get(asin) or {}
        if not include_ineligible and prev.get("Check_Status") in PERMANENT and "PDP" in (prev.get("Check_Note") or "").upper():
            continue
        asins.append([asin, meta["column"]])
    os.makedirs(DATA, exist_ok=True)
    with open(_queue_path(profile_name), "w", encoding="utf-8") as f:
        json.dump({"reason": reason, "queued_at": _now(), "profile": profile_name, "asins": asins}, f, indent=1)
    return len(asins)


def run_queue(driver, profile_name: str, sku_map=None, coupon_codes=None, cache=None, mod=None, max_asins: int = 15) -> list:
    """Drain up to max_asins from the re-check queue (called between placer cycles)."""
    if mod is None:
        from engine import orders as mod  # noqa: F811
    p = _queue_path(profile_name)
    try:
        with open(p, encoding="utf-8") as f:
            q = json.load(f) or {}
    except Exception:
        return []
    pending = q.get("asins") or []
    if not pending:
        try:
            os.remove(p)
        except OSError:
            pass
        return []
    batch, rest = pending[:max_asins], pending[max_asins:]
    svc, wsvc = mod._get_sheets_service(), mod._get_sheets_write_service()
    headers, rows = read_tab(svc)
    sku_map = sku_map if sku_map is not None else (mod.load_sku_map() or {})
    pri = _priority_codes(mod, coupon_codes)
    print(f"\n🔁 PRIORITY RE-CHECK ({profile_name}): {len(batch)} of {len(pending)} queued — {q.get('reason', '')}")
    results = _run_batch(driver, profile_name, [tuple(x) for x in batch], sku_map, pri, cache, mod, svc, wsvc,
                         headers, rows, _auto_assign(mod))
    q["asins"] = rest
    with open(p, "w", encoding="utf-8") as f:
        json.dump(q, f, indent=1)
    if not rest:
        print(f"  ✅ re-check queue drained ({q.get('reason', '')})")
        try:
            os.remove(p)
        except OSError:
            pass
    return results


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not argv or argv[0].upper() not in ("DM_COUPON", "HS_COUPON"):
        print("usage: python engine/priority_check.py DM_COUPON|HS_COUPON [--all] [--max N] [--no-move]")
        sys.exit(2)
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
