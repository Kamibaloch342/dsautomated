"""
listings_report.py — every listing of a store with the handling time Amazon really has for it.

Owner, 13 Nov 2026: "whenever I run it, it should download the fresh All Listings Report, then scrape
all this information and show me … I want to see all the listings' handling time."

For each store (HOANK and Wrenchly by default), in its own Seller Central Chrome (accounts.json
report_port: HOANK 9331, Hathoro 9333, Wrenchly 9337) and holding that Chrome's turn (utils.sc_chrome):
  1. sign in if Seller Central asks (platforms.auth.auto_login, as closing does);
  2. read the store's default handling time (Settings → Shipping → General);
  3. request and download a fresh All Listings Report (platforms/download_all_listings_report.py) —
     it has every SKU / ASIN / title / price / quantity / status, but no handling time;
  4. for every listing, ask Seller Central for the offer the Edit Listing page shows
     (GET /abis/ajax/edit-v2?sku=…&asin=…, the page's own request, ~2.5 s, four at a time) and read
     handling time (fulfillment_availability#1.lead_time_to_ship_max_days — absent = the store
     default), quantity, price, brand, fulfilment channel and whether the offer is active.
READ-ONLY: nothing is typed or saved on Seller Central.

Out:  data/listings_report/<store>.json    (the launcher's 🧾 Listings → "Listings report" window)
      reports/listings/Listings_<date>.xlsx (Summary + one sheet per store; --open opens it)

  python scripts/listings_report.py                      HOANK + Wrenchly
  python scripts/listings_report.py --store Hathoro      one store (repeat --store for several)
  python scripts/listings_report.py --no-download        use the last downloaded report (faster test)
  python scripts/listings_report.py --limit 20           first 20 listings only (a quick look)
  python scripts/listings_report.py --excel-only         rebuild the Excel from the saved JSON files
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if AUTO not in sys.path:
    sys.path.insert(0, AUTO)

DATA_DIR = os.path.join(AUTO, "data", "listings_report")
XLSX_DIR = os.path.join(os.path.dirname(AUTO), "reports", "listings")
DB = os.path.join(AUTO, "data", "automations_unified.db")
DEFAULT_STORES = ("HOANK", "Wrenchly")
CONCURRENCY = 4          # edit-v2 requests in flight at once
BATCH = 40               # listings per browser call (progress is printed per batch)

# Runs in the page: fetch edit-v2 for each [asin, sku] and keep only what the report needs
# (each answer is ~700 KB of form schema). 429 / 5xx back off and retry; a sign-in page is told apart.
_FETCH_JS = r"""
const items = arguments[0], conc = arguments[1], done = arguments[arguments.length - 1];
const out = new Array(items.length);
let next = 0;
const sleep = ms => new Promise(s => setTimeout(s, ms));
async function one(i) {
  const [asin, sku] = items[i];
  const u = '/abis/ajax/edit-v2?sku=' + encodeURIComponent(sku) + '&asin=' + encodeURIComponent(asin);
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await fetch(u, {credentials: 'include', headers: {'Accept': 'application/json'}});
      if (/\/ap\/(signin|mfa|cvf)/.test(r.url || '')) { out[i] = {err: 'signin'}; return; }
      if (r.status === 429 || r.status >= 500) { await sleep(3000 * (attempt + 1)); continue; }
      if (r.status !== 200) { out[i] = {err: 'HTTP ' + r.status}; return; }
      const j = await r.json();
      const f = ((j.listingContext || {}).attributePropertiesV3Flat) || {};
      const lists = (((((j.skuContext || {}).merchantOffersByAsinResponse || {}).value || {}).result || {})
                     .merchantOffersByAsinList) || [];
      let me = null;
      for (const l of lists) for (const s of (l.merchantOffersByAsinSkuDataList || [])) if (s.sku === sku) me = s;
      const g = k => (f[k] === undefined || f[k] === null) ? null : String(f[k]);
      out[i] = {h: g('fulfillment_availability#1.lead_time_to_ship_max_days'),
                q: g('fulfillment_availability#1.quantity') ?? (me ? String(me.quantity) : null),
                p: g('purchasable_offer#1.our_price#1.schedule#1.value_with_tax') ?? (me ? String(me.price) : null),
                ch: g('fulfillment_availability#1.fulfillment_channel_code'),
                b: g('brand#1.value'), n: g('item_name#1.value'),
                mq: g('max_order_quantity#1.value'),
                act: me ? !!me.isActive : null, blk: me ? !!me.isBlocked : null};
      return;
    } catch (e) {
      if (attempt === 2) { out[i] = {err: String(e).slice(0, 100)}; return; }
      await sleep(2000);
    }
  }
  if (!out[i]) out[i] = {err: 'Seller Central kept answering busy'};
}
async function worker() { while (next < items.length) { const i = next++; await one(i); } }
Promise.all(Array.from({length: conc}, worker)).then(() => done(out)).catch(e => done({fatal: String(e)}));
"""


# ── small helpers ──────────────────────────────────────────────────────────────────────────────
def _days(n) -> str:
    return f"{n} day" + ("" if str(n) == "1" else "s")


def _num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _int(v) -> Optional[int]:
    f = _num(v)
    return int(f) if f is not None else None


def read_alr(path: str) -> List[Dict[str, str]]:
    """The All Listings Report as dicts (Amazon's flat file: tabs, no quoting, maybe a BOM)."""
    raw = open(path, "rb").read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        return []
    cols = [c.strip().lstrip("﻿") for c in lines[0].split("\t")]
    rows = []
    for ln in lines[1:]:
        parts = ln.split("\t")
        rows.append({c: (parts[i].strip() if i < len(parts) else "") for i, c in enumerate(cols)})
    return rows


def guard_rows(store: str) -> Dict[str, Dict[str, Any]]:
    """The listing guard's open rows for this store (engine/listing_guard.py), by ASIN. Read-only."""
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        try:
            have = {r[1] for r in c.execute("PRAGMA table_info(listing_oos)")}
            if not have:
                return {}
            cols = [x for x in ("asin", "status", "action", "prev_value", "changed_to", "action_at", "kept", "zoro_code")
                    if x in have]
            return {r["asin"]: dict(r) for r in c.execute(
                f"SELECT {', '.join(cols)} FROM listing_oos WHERE store=? AND status IN "
                "('new', 'recheck', 'working', 'changed', 'alerted', 'restoring')", (store,))}
        finally:
            c.close()
    except sqlite3.Error:
        return {}


def guard_note(g: Optional[Dict[str, Any]]) -> str:
    """'Zoro out — handling set 2 → 7 d (13 Nov)' … for an open listing-guard row, '' otherwise."""
    if not g:
        return ""
    when = ""
    try:
        when = datetime.strptime(str(g.get("action_at") or "")[:19], "%Y-%m-%d %H:%M:%S").strftime(" (%d %b)")
    except ValueError:
        pass
    to, prev, kept = str(g.get("changed_to") or ""), str(g.get("prev_value") or ""), str(g.get("kept") or "")
    if g.get("action") == "handling" and to:
        return f"Zoro out — script set handling {prev or 'default'} → {to} d{when}"
    if g.get("action") == "qty0" and to:
        return f"Zoro out — script set quantity {prev or '?'} → 0{when}"
    if kept == "off_sale":
        return f"Zoro out — quantity 0, left alone{when}"
    if kept == "hand_set":
        return f"Zoro out — hand-set handling kept{when}"
    st = str(g.get("status") or "")
    if st == "changed":
        return f"Zoro out — already like this, not changed by the script{when}"
    if st == "alerted":
        return f"Zoro out — not changed by the script (could not, or asked a person){when}"
    return f"Zoro out — waiting for the script{when}"


# ── one store, in its own process (closing reads its account at import) ─────────────────────────
def _attach(port: int):
    """A new WebDriver session on the Chrome at `port`, on a page that exists (one is opened through
    the DevTools endpoint when the Chrome has none left)."""
    import urllib.request
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    base = f"http://127.0.0.1:{port}"
    try:
        pages = [t for t in json.load(urllib.request.urlopen(f"{base}/json/list", timeout=5)) if t.get("type") == "page"]
        if not pages:
            urllib.request.urlopen(urllib.request.Request(f"{base}/json/new?about:blank", method="PUT"), timeout=5)
            time.sleep(1)
    except Exception:
        pass
    o = Options()
    o.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
    w = webdriver.Chrome(options=o)
    try:
        hs = w.window_handles
        if hs:
            w.switch_to.window(hs[-1])
    except Exception:
        pass
    return w


def _signed_in(driver, store: str, url: str) -> bool:
    driver.get(url)
    time.sleep(3)
    if "/ap/" in (driver.current_url or ""):                     # signin / mfa / cvf
        print(f"  🔐 {store}: Seller Central asks to sign in — signing in…", flush=True)
        try:
            from platforms.auth import auto_login
            if not auto_login(driver, store):
                return False
        except Exception as e:
            print(f"  ⚠️ {store}: sign-in failed ({type(e).__name__}: {e})", flush=True)
            return False
        driver.get(url)
        time.sleep(3)
    try:
        from platforms.auth import on_account_switcher, pick_account
        from utils.accounts_config import accounts
        if on_account_switcher(driver):
            a = accounts().get(store, {})
            pick_account(driver, a.get("sc_store_name"), a.get("sc_marketplace"), target_url=url)
    except Exception:
        pass
    cur = (driver.current_url or "").lower()
    return "sellercentral.amazon.com" in cur and "/ap/" not in cur


def run_store(store: str, download: bool = True, limit: int = 0, conc: int = CONCURRENCY) -> int:
    os.environ["CLOSING_ACCOUNT"] = store
    from utils.accounts_config import accounts
    from utils.sc_chrome import sc_turn, SCBusy
    from utils.proc import port_open, close_chrome
    from utils import listing_watch as lw
    os.makedirs(DATA_DIR, exist_ok=True)
    port = int(accounts()[store]["report_port"])
    alr_path = os.path.join(DATA_DIR, f"{store}_all_listings.txt")
    t0 = time.time()
    print(f"\n━━ {store} — Seller Central Chrome {port}", flush=True)
    try:
        with sc_turn(port, f"listings report {store}", max_wait=900, on_timeout="skip"):
            import closing
            from engine import offer_edit
            ours = not port_open(port)
            d = closing.ensure_chrome(port)
            try:
                if not _signed_in(d, store, "https://sellercentral.amazon.com/myinventory/inventory"):
                    print(f"  ❌ {store}: not signed in to Seller Central — nothing read", flush=True)
                    return 2
                default = offer_edit.account_default_handling_days(d)
                print(f"  {store}: default handling time {_days(default) if default else 'not readable'}", flush=True)

                if download:
                    print(f"  {store}: requesting a fresh All Listings Report…", flush=True)
                    from platforms.download_all_listings_report import download_all_listings_report
                    tmp = alr_path + ".download"
                    dl = download_all_listings_report(port=port, output_tsv=tmp, timeout_seconds=300)
                    fresh = read_alr(dl) if dl and os.path.exists(dl) else []
                    if fresh and "seller-sku" in fresh[0] and "asin1" in fresh[0]:
                        os.replace(dl, alr_path)
                        print(f"  ✅ {store}: All Listings Report downloaded", flush=True)
                    else:
                        print(f"  ⚠️ {store}: the report did not download — "
                              + ("using the last one" if os.path.exists(alr_path) else "no earlier one to use"), flush=True)
                if not os.path.exists(alr_path):
                    print(f"  ❌ {store}: no All Listings Report to work from", flush=True)
                    return 3
                alr_at = datetime.fromtimestamp(os.path.getmtime(alr_path)).strftime("%Y-%m-%d %H:%M")
                rows = [r for r in read_alr(alr_path) if r.get("seller-sku") and r.get("asin1")]
                if limit:
                    rows = rows[:limit]
                print(f"  {store}: {len(rows):,} listings in the report ({alr_at}) — reading each offer…", flush=True)

                # The download drove the Chrome through its own session and closed its tab — which can
                # be the very window this session was on (13 Nov 2026: "no such window" after the
                # download, both stores). Read the offers through a new session on a page that exists.
                w = _attach(port) if download else d
                try:
                    w.get("https://sellercentral.amazon.com/myinventory/inventory")
                    time.sleep(3)
                    w.set_script_timeout(600)
                    got: List[Dict[str, Any]] = []
                    for b in range(0, len(rows), BATCH):
                        part = rows[b:b + BATCH]
                        res = w.execute_async_script(_FETCH_JS, [[r["asin1"], r["seller-sku"]] for r in part], conc)
                        if isinstance(res, dict) and res.get("fatal"):
                            raise RuntimeError(res["fatal"])
                        if any((x or {}).get("err") == "signin" for x in res):
                            print(f"  🔐 {store}: signed out mid-way — signing in again", flush=True)
                            if not _signed_in(w, store, "https://sellercentral.amazon.com/myinventory/inventory"):
                                raise RuntimeError("signed out and the sign-in failed")
                            res = w.execute_async_script(_FETCH_JS, [[r["asin1"], r["seller-sku"]] for r in part], conc)
                        got.extend(res)
                        bad = sum(1 for x in got if (x or {}).get("err"))
                        print(f"  {store}: {min(b + BATCH, len(rows)):,}/{len(rows):,} read"
                              + (f" ({bad} could not be read)" if bad else "")
                              + f" · {time.time() - t0:.0f} s", flush=True)
                finally:
                    if w is not d:
                        try:
                            w.service.stop()        # end our session only; quit() would close the tabs
                        except Exception:
                            pass
            finally:
                closing.release_chrome(d)
                if ours:
                    close_chrome(os.path.join(closing.CLOSING_PROFILE_ROOT, f"port_{port}"), port, timeout=10)
    except SCBusy as e:
        print(f"  ❌ {store}: {e}", flush=True)
        return 4

    guard = guard_rows(store)
    listings = []
    for r, x in zip(rows, got):
        x = x or {}
        h = _int(x.get("h"))
        brand = x.get("b") or ""
        listings.append({
            "sku": r["seller-sku"], "asin": r["asin1"],
            "title": (x.get("n") or r.get("item-name") or "").strip(),
            "brand": brand,
            "own": "PL" if lw.is_pl_listing(brand, r["seller-sku"], store) else "DS",
            "status": r.get("status") or "",
            "active": x.get("act"),
            "qty": _int(x.get("q")) if x.get("q") is not None else _int(r.get("quantity")),
            "price": _num(x.get("p")) if x.get("p") is not None else _num(r.get("price")),
            "handling": h,                                     # None = not set on the listing
            "handling_days": h if h is not None else default,  # what Amazon uses
            "channel": "FBA" if (x.get("ch") or r.get("fulfillment-channel") or "").upper().startswith("AMAZON") else "MFN",
            "max_order": _int(x.get("mq")),
            "opened": (r.get("open-date") or "")[:10],
            "guard": guard_note(guard.get(r["asin1"])),
            "error": x.get("err") or "",
        })
    out = {"store": store, "made_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "alr_at": alr_at,
           "default_days": default, "seconds": round(time.time() - t0), "listings": listings}
    tmp = os.path.join(DATA_DIR, f"{store}.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    os.replace(tmp, os.path.join(DATA_DIR, f"{store}.json"))
    print("  " + summary_line(out), flush=True)
    return 0


# ── what the owner reads ───────────────────────────────────────────────────────────────────────
def handling_text(l: Dict[str, Any], default) -> str:
    if l.get("error"):
        return "?"
    if l.get("handling") is None:
        return f"{_days(default)} (default)" if default else "default"
    return _days(l["handling"])


def breakdown(rep: Dict[str, Any]) -> List[tuple]:
    """[(label, count)] of handling times, the default first."""
    from collections import Counter
    c = Counter()
    for l in rep["listings"]:
        if l.get("error"):
            c["could not read"] += 1
        else:
            c[handling_text(l, rep.get("default_days"))] += 1
    return sorted(c.items(), key=lambda kv: (kv[0] == "could not read", "default" not in kv[0],
                                              _int(kv[0].split()[0]) or 0))


def summary_line(rep: Dict[str, Any]) -> str:
    ls = rep["listings"]
    active = sum(1 for l in ls if (l.get("status") or "").lower() == "active")
    return (f"{rep['store']}: {len(ls):,} listings ({active:,} active) · handling time: "
            + " · ".join(f"{k} {v:,}" for k, v in breakdown(rep)))


def load_reports(stores) -> List[Dict[str, Any]]:
    out = []
    for s in stores:
        p = os.path.join(DATA_DIR, f"{s}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out.append(json.load(f))
    return out


def write_excel(reports: List[Dict[str, Any]]) -> Optional[str]:
    if not reports:
        return None
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    os.makedirs(XLSX_DIR, exist_ok=True)
    path = os.path.join(XLSX_DIR, f"Listings_{'_'.join(r['store'] for r in reports)}_{datetime.now():%Y-%m-%d_%H%M}.xlsx")
    wb = Workbook()
    bold, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F")
    amber, grey, red = (PatternFill("solid", fgColor=c) for c in ("FFF2CC", "EDEDED", "F8D7DA"))

    ws = wb.active
    ws.title = "Summary"
    ws.append(["Store", "Listings", "Active", "Own brand (PL)", "Default handling", "Handling time", "Listings",
               "Report from", "Read at"])
    for c in ws[1]:
        c.font, c.fill = bold, head_fill
    for rep in reports:
        ls = rep["listings"]
        first = True
        for label, n in breakdown(rep):
            ws.append([rep["store"] if first else "", len(ls) if first else None,
                       sum(1 for l in ls if (l.get("status") or "").lower() == "active") if first else None,
                       sum(1 for l in ls if l.get("own") == "PL") if first else None,
                       _days(rep["default_days"]) if first and rep.get("default_days") else ("" if not first else "?"),
                       label, n, rep.get("alr_at") if first else "", rep.get("made_at") if first else ""])
            if "default" not in label:
                ws.cell(ws.max_row, 6).fill = amber
            first = False
        ws.append([])
    for i, w in enumerate((12, 10, 9, 15, 16, 22, 10, 18, 18), 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    cols = [("SKU", 30), ("ASIN", 13), ("Product", 60), ("Brand", 16), ("PL/DS", 7), ("Status", 10),
            ("Qty", 7), ("Price", 9), ("Handling time", 17), ("Channel", 8), ("Opened", 11), ("Listing guard", 46)]
    for rep in reports:
        ws = wb.create_sheet(rep["store"][:31])
        ws.append([c for c, _ in cols])
        for c in ws[1]:
            c.font, c.fill = bold, head_fill
        default = rep.get("default_days")
        ls = sorted(rep["listings"], key=lambda l: (l.get("handling") is None, -(l.get("handling") or 0),
                                                    (l.get("status") or "").lower() != "active", l["sku"]))
        for l in ls:
            ws.append([l["sku"], l["asin"], l["title"][:120], l["brand"], l["own"], l["status"],
                       l["qty"], l["price"], handling_text(l, default), l["channel"], l["opened"],
                       l["guard"] or (f"could not read: {l['error']}" if l.get("error") else "")])
            row = ws.max_row
            if l.get("error"):
                ws.cell(row, 9).fill = red
            elif l.get("handling") is not None and l["handling"] != default:
                ws.cell(row, 9).fill = amber
            if (l.get("status") or "").lower() != "active" or l.get("qty") == 0:
                for col in (6, 7):
                    ws.cell(row, col).fill = grey
            ws.cell(row, 8).number_format = "$#,##0.00"
        for i, (_, w) in enumerate(cols, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = ws.dimensions
        for c in ws[1]:
            c.alignment = Alignment(vertical="center")
    tmp = path + ".tmp.xlsx"
    wb.save(tmp)
    os.replace(tmp, path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", action="append", help="HOANK / Hathoro / Wrenchly (repeatable; default HOANK + Wrenchly)")
    ap.add_argument("--no-download", action="store_true", help="use the last downloaded All Listings Report")
    ap.add_argument("--limit", type=int, default=0, help="read only the first N listings of each store")
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY)
    ap.add_argument("--excel-only", action="store_true", help="rebuild the Excel from the saved JSON files")
    ap.add_argument("--open", action="store_true", help="open the Excel file when done (macOS)")
    ap.add_argument("--one", help=argparse.SUPPRESS)                 # child process: one store
    a = ap.parse_args()

    if a.one:
        return run_store(a.one, download=not a.no_download, limit=a.limit, conc=max(1, min(8, a.concurrency)))

    stores = a.store or list(DEFAULT_STORES)
    codes = {}
    if not a.excel_only:
        for s in stores:      # one process per store, one after another (they share the download folder)
            cmd = [sys.executable, "-u", os.path.abspath(__file__), "--one", s, "--concurrency", str(a.concurrency)]
            if a.no_download:
                cmd.append("--no-download")
            if a.limit:
                cmd += ["--limit", str(a.limit)]
            codes[s] = subprocess.call(cmd, cwd=AUTO)
    reports = load_reports([s for s in stores if codes.get(s, 0) == 0])
    print("\n━━ Listings report")
    for rep in reports:
        print("  " + summary_line(rep) + f"  (read {rep['made_at']})")
    for s, c in codes.items():
        if c:
            print(f"  ❌ {s}: not read this time (code {c}) — its last report is kept")
    reps = load_reports(stores)
    for rep in reps:                      # the listing guard's notes as they are now
        g = guard_rows(rep["store"])
        for l in rep["listings"]:
            l["guard"] = guard_note(g.get(l["asin"]))
    path = write_excel(reps)
    if path:
        print(f"  📄 {path}")
        if a.open:
            subprocess.Popen(["open", path])
    return 0 if all(c == 0 for c in codes.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
