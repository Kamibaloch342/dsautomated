#!/usr/bin/env python3
"""
priority_check.py — verifies every ASIN in Priority_ASIN_Control automatically.

WHY: every week new priority ASINs arrive and the owner had to decide by hand
which column (Coupon / Reseller / Deciding) each one belongs in. This module
checks each ASIN against the LIVE system and writes the verdict back into the
same Google Sheet tab, so the team only reviews — nobody has to test by hand:

  MAPPED?      is the ASIN in Verified_SKU_Dataset (the bot never places unmapped ASINs)
  STOCK/PRICE  live Zoro inventory + unit price of the mapped Zoro code (× qty multiplier)
  COUPON       builds a real Zoro cart with the item and tries the PRIORITY coupons,
               then the regular ones → COUPON_OK:<code> / PROMO_INELIGIBLE / TRANSIENT
  CART         estimated cart at order-qty 1 vs the $50 free-shipping / coupon threshold
  SUGGEST      Coupon   — a coupon applies and cart ≥ $50
               Deciding — a coupon applies but cart < $50 (depends on order qty)
               Reseller — promo-ineligible (no coupon ever applies)
               Unmapped / OOS — needs a human first

Results land in a "Check_*" block in Priority_ASIN_Control (created on first run,
to the right of the existing columns) and learned promo-ineligible codes go to
data/dataset/coupon_intel.json — the same memory the placer's routing uses, so
a PROMO_INELIGIBLE verdict immediately routes that ASIN's orders to the reseller.
With automation.priority_check.auto_assign = true the ASIN is also MOVED into
the suggested column; the default is suggest-only (a human moves it).

Runs (a) inside the coupon placers between cycles — automation.priority_check
every_hours, or whenever data/control/priority_check.request exists (the
dashboard's "Check priority ASINs" button) — using the placer's own Zoro
session, so it never fights a running checkout for the cart; or (b) standalone:

    python engine/priority_check.py HS_COUPON [--all] [--max 40] [--auto-assign]

Standalone refuses to run while that profile's placer is running (one Zoro cart
per account) — the placer lock file makes this automatic.
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
STATE_PATH = os.path.join(AUTO, "data", "priority_check_state.json")


def _col_letter(idx: int) -> str:
    """0-based column index → A1 letters."""
    s = ""
    idx += 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def read_tab(svc):
    res = svc.spreadsheets().values().get(spreadsheetId=GOOGLE_SHEET_ID,
                                          range=f"'{TAB}'!A1:AZ500").execute()
    rows = res.get("values", []) or []
    headers = [h.strip() for h in (rows[0] if rows else [])]
    return headers, rows


def store_prefix(profile_name: str) -> str:
    return "DM" if profile_name.upper().startswith("DM") else "HS"


def collect_asins(headers, rows, profile_name: str) -> dict:
    """ASIN → {"column": header} for this store's columns + any inbox column."""
    pref = store_prefix(profile_name)
    wanted = [h for h in headers if h.upper().startswith(pref + "_")
              or h.lower() in ("new_asins", "new", "inbox", "to_check", "promo_ineligible")]
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
    """Existing Check_* rows keyed by ASIN (so unchanged ASINs keep their verdict)."""
    if "Check_ASIN" not in headers:
        return {}
    idx = {c: headers.index(c) for c in CHECK_COLS if c in headers}
    out = {}
    for r in rows[1:]:
        a_i = idx["Check_ASIN"]
        if a_i < len(r) and (r[a_i] or "").strip():
            out[r[a_i].strip().upper()] = {c: (r[i] if i < len(r) else "") for c, i in idx.items()}
    return out


def write_check_block(wsvc, headers, results: dict) -> None:
    """(Re)write the whole Check_* block: header + one row per ASIN, sorted so the
    newest verdicts are on top. Only the block's columns are touched."""
    if "Check_ASIN" in headers:
        start = headers.index("Check_ASIN")
    else:
        start = len(headers) + 1               # leave one empty column as a separator
    rows = [CHECK_COLS]
    ordered = sorted(results.values(), key=lambda d: d.get("Check_At", ""), reverse=True)
    for d in ordered:
        rows.append([str(d.get(c, "") or "") for c in CHECK_COLS])
    # pad with blanks so stale rows below the new block are cleared
    rows += [[""] * len(CHECK_COLS) for _ in range(max(0, 400 - len(rows)))]
    rng = f"'{TAB}'!{_col_letter(start)}1:{_col_letter(start + len(CHECK_COLS) - 1)}{len(rows)}"
    wsvc.spreadsheets().values().update(spreadsheetId=GOOGLE_SHEET_ID, range=rng,
                                        valueInputOption="RAW", body={"values": rows}).execute()


def move_asin(wsvc, headers, rows, asin: str, from_col: str, to_col: str) -> bool:
    """auto_assign: blank the ASIN in from_col and append it to to_col."""
    if to_col not in headers or from_col not in headers or from_col == to_col:
        return False
    fi, ti = headers.index(from_col), headers.index(to_col)
    data = []
    for ri, r in enumerate(rows[1:], start=2):
        if fi < len(r) and (r[fi] or "").strip().upper() == asin:
            data.append({"range": f"'{TAB}'!{_col_letter(fi)}{ri}", "values": [[""]]})
    # first empty row in the target column
    tr = 2
    for ri, r in enumerate(rows[1:], start=2):
        if ti < len(r) and (r[ti] or "").strip():
            tr = ri + 1
    data.append({"range": f"'{TAB}'!{_col_letter(ti)}{tr}", "values": [[asin]]})
    wsvc.spreadsheets().values().batchUpdate(spreadsheetId=GOOGLE_SHEET_ID,
                                             body={"valueInputOption": "RAW", "data": data}).execute()
    return True


def suggested_column(profile_name: str, suggest: str) -> str:
    pref = store_prefix(profile_name)
    return {"Coupon": f"{pref}_Coupon", "Reseller": f"{pref}_Reseller", "Deciding": f"{pref}_Deciding"}.get(suggest, "")


def check_one(driver, asin: str, sku_map: dict, priority_codes: list, regular_codes: list) -> dict:
    """Test ONE ASIN on the live Zoro session. Returns a Check_* row dict."""
    from platforms.zoro import check_inventory, clear_cart_and_add, clear_cart_selenium
    from checkout.true_api import _get_cart_id, _apply_coupon_api, _is_transient_coupon_error

    row = {"Check_ASIN": asin, "Check_At": _now(), "Check_Coupon": "", "Check_Cart": "",
           "Check_Note": ""}
    m = sku_map.get(asin) or {}
    code = (m.get("zoro_code") or "").strip()
    if not code:
        row.update(Check_Status="UNMAPPED", Check_Suggest="Map it first",
                   Check_Note="not in Verified_SKU_Dataset")
        return row
    try:
        qty = max(1, int(float(m.get("qty_multiplier") or 1) * int(m.get("zoro_multiple") or 1)))
    except Exception:
        qty = 1
    cartable, status = check_inventory(driver, code, qty)
    if not cartable:
        if status in ("API_ERROR", "NO_DATA"):
            row.update(Check_Status="TRANSIENT", Check_Suggest="Re-check later",
                       Check_Note=f"{code}: inventory API {status}")
        else:
            row.update(Check_Status="OOS", Check_Suggest="Wait / alt code", Check_Note=f"{code}: {status}")
        return row
    ok, price = clear_cart_and_add(driver, code, qty)
    if not ok:
        row.update(Check_Status="TRANSIENT", Check_Suggest="Re-check later", Check_Note=f"{code}: cart add failed")
        return row
    cart = round(float(price or 0) * qty, 2)
    row["Check_Cart"] = f"${cart:.2f} ({code} x{qty} @ ${float(price or 0):.2f})"
    cart_id = _get_cart_id(driver)
    tried = transient = 0
    worked = tier = None
    if cart_id:
        for tier_name, codes in (("priority", priority_codes), ("regular", regular_codes)):
            for c in codes:
                tried += 1
                okc, res = _apply_coupon_api(driver, cart_id, c)
                if okc and (res or 0) > 0:
                    worked, tier = c, tier_name
                    break
                if not okc and _is_transient_coupon_error(res):
                    transient += 1
            if worked:
                break
    try:
        clear_cart_selenium(driver)
    except Exception:
        pass
    if worked:
        row["Check_Coupon"] = f"{worked} ({tier})"
        if tier == "priority":
            row.update(Check_Status="COUPON_OK", Check_Suggest="Coupon" if cart >= CART_THRESHOLD else "Deciding",
                       Check_Note="" if cart >= CART_THRESHOLD else f"cart < ${CART_THRESHOLD:.0f} at qty 1 — coupon profile only when the order is big enough")
        else:
            row.update(Check_Status="REGULAR_ONLY", Check_Suggest="Deciding",
                       Check_Note="priority coupons rejected; a regular coupon works (needs cart ≥ $50)")
    elif tried and transient == tried:
        row.update(Check_Status="TRANSIENT", Check_Suggest="Re-check later", Check_Note="coupon API busy")
    elif tried:
        row.update(Check_Status="PROMO_INELIGIBLE", Check_Suggest="Reseller",
                   Check_Note=f"all {tried} coupon(s) rejected for {code}")
    else:
        row.update(Check_Status="NO_COUPONS", Check_Suggest="Deciding", Check_Note="no coupons loaded to test")
    return row


def run_check(driver, profile_name: str, sku_map=None, coupon_codes=None, cache=None, mod=None,
              only_new: bool = True, max_asins: int = 25, auto_assign=None) -> list:
    """Check this store's Priority-ASIN-Control ASINs and write verdicts to the sheet.
    `mod` is engine.orders (loaders + promo learning); loaded lazily when None."""
    if mod is None:
        from engine import orders as mod  # noqa: F811 — standalone mode
    svc = mod._get_sheets_service()
    wsvc = mod._get_sheets_write_service()
    headers, rows = read_tab(svc)
    if not headers:
        print("  ⚠️ priority check: sheet tab empty/unreadable")
        return []
    if sku_map is None:
        sku_map = mod.load_sku_map() or {}
    if coupon_codes is None:
        coupon_codes = mod.load_coupon_codes()
    pri, reg = ([], [])
    if isinstance(coupon_codes, tuple) and len(coupon_codes) == 2:
        pri = []
        for c in coupon_codes[0] or []:
            cd = str(c.get("code") or "").strip()
            if cd and cd not in pri:
                pri.append(cd)
        reg = [str(c).strip() for c in (coupon_codes[1] or []) if str(c).strip() and str(c).strip() not in pri][:6]
    if auto_assign is None:
        auto_assign = bool(((mod.gui_cfg.get("automation") or {}).get("priority_check") or {}).get("auto_assign"))

    existing = read_check_block(headers, rows)
    targets = collect_asins(headers, rows, profile_name)
    cutoff = (datetime.now() - timedelta(days=RECHECK_DAYS)).strftime("%Y-%m-%d %H:%M")
    todo = []
    for asin, meta in targets.items():
        prev = existing.get(asin)
        if only_new and prev and prev.get("Check_At", "") >= cutoff and prev.get("Check_Status") not in ("TRANSIENT", ""):
            continue
        todo.append((asin, meta["column"]))
    todo = todo[:max_asins]
    print(f"\n🧪 PRIORITY CHECK ({profile_name}): {len(targets)} ASIN(s) in sheet, "
          f"{len(todo)} to test now ({len(pri)} priority + {len(reg)} regular coupons)")
    if not todo:
        return []
    results = []
    for i, (asin, col) in enumerate(todo, 1):
        t0 = time.time()
        try:
            row = check_one(driver, asin, sku_map, pri, reg)
        except Exception as e:
            row = {"Check_ASIN": asin, "Check_At": _now(), "Check_Status": "ERROR", "Check_Suggest": "Re-check later",
                   "Check_Coupon": "", "Check_Cart": "", "Check_Note": str(e)[:120]}
        row["Check_Profile"] = profile_name
        row["Check_Note"] = "; ".join(filter(None, [f"in {col}", row.get("Check_Note", "")]))
        existing[asin] = row
        results.append(row)
        print(f"  [{i}/{len(todo)}] {asin} ({col}) → {row['Check_Status']} / {row.get('Check_Suggest', '')} "
              f"{row.get('Check_Coupon', '')} {row.get('Check_Cart', '')}  ({time.time() - t0:.0f}s)")
        # feed the placer's promo-ineligible memory
        code = ((sku_map.get(asin) or {}).get("zoro_code") or "").strip()
        try:
            if row["Check_Status"] == "PROMO_INELIGIBLE":
                mod._learn_promo_outcome(code, asin, False, cache=cache, definitive=True)
            elif row["Check_Status"] in ("COUPON_OK", "REGULAR_ONLY"):
                mod._learn_promo_outcome(code, asin, True, cache=cache)
        except Exception as e:
            print(f"    ⚠️ learn: {e}")
        if auto_assign:
            target_col = suggested_column(profile_name, row.get("Check_Suggest", ""))
            if target_col and target_col != col and col.upper().startswith(store_prefix(profile_name) + "_"):
                try:
                    if move_asin(wsvc, headers, rows, asin, col, target_col):
                        print(f"    ↪ moved {asin}: {col} → {target_col}")
                        headers, rows = read_tab(svc)
                except Exception as e:
                    print(f"    ⚠️ move failed: {e}")
    try:
        write_check_block(wsvc, headers, existing)
        print(f"  ✅ verdicts written to '{TAB}' Check_* columns")
    except Exception as e:
        print(f"  ⚠️ could not write verdicts to the sheet: {e}")
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"last_run": _now(), "profile": profile_name, "checked": len(results),
                       "results": results}, f, indent=1)
    except Exception:
        pass
    return results


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not argv or argv[0].upper() not in ("DM_COUPON", "HS_COUPON"):
        print("usage: python engine/priority_check.py DM_COUPON|HS_COUPON [--all] [--max N] [--auto-assign]")
        sys.exit(2)
    only_new = "--all" not in sys.argv
    mx = 25
    if "--max" in sys.argv:
        try:
            mx = int(sys.argv[sys.argv.index("--max") + 1])
        except Exception:
            pass
    # engine.orders selects its profile from argv (positional) and takes the
    # per-profile lock at import — if the placer is running this exits, which is
    # exactly the "one Zoro cart per account" guard we want.
    from engine import orders as mod
    driver = mod.connect_chrome()
    if driver is None:
        print("❌ could not attach to the profile's Chrome")
        sys.exit(1)
    cache = mod._load_coupon_intel()
    run_check(driver, mod.PROFILE_NAME, cache=cache, mod=mod, only_new=only_new, max_asins=mx,
              auto_assign=("--auto-assign" in sys.argv) or None)


if __name__ == "__main__":
    main()
