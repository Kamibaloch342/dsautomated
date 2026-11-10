#!/usr/bin/env python3
"""
dump_confirm_shipment.py — READ-ONLY capture of Amazon's confirm-shipment page.

Why this exists: the multi-package controls on that page (one tracking input per
package, the "add package" control, the per-item quantity selects) are rendered
by a SPA after login, so they can't be read from a static fetch or from a browser
that isn't authenticated. This reuses the TRACKER's own Chrome profile — the one
already signed in to Seller Central — and dumps the fully-rendered DOM.

It NEVER clicks Confirm. It only navigates, optionally reveals the shipment form,
and writes the structure to data/ for building exact selectors.

Usage:
    cd /Users/kamranbaloch342/Downloads/Dropshipping/Development_Unit/Automations
    ../.venv/bin/python scripts/dump_confirm_shipment.py 114-1668349-3077818
    ../.venv/bin/python scripts/dump_confirm_shipment.py 114-1668349-3077818 --account hoank

Close any Chrome already running on that profile first (Chrome allows one
process per user-data-dir).
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from engine.tracking import (
    AMAZON_ACCOUNTS, resolve_user_data_dir, create_chrome_driver,
    INITIAL_CONFIRM_SHIPMENT_BUTTON_SELECTOR,
)

OUT_DIR = os.path.join(SCRIPT_DIR, "data")

# Everything worth knowing about a control, for writing selectors later.
PROBE_JS = """
function attrs(el) {
    var o = {tag: el.tagName.toLowerCase()};
    ['data-test-id','id','name','type','placeholder','aria-label','value',
     'role','class'].forEach(function(a){
        var v = el.getAttribute(a);
        if (v) o[a] = String(v).slice(0, 140);
    });
    var t = (el.innerText || el.textContent || '').trim();
    if (t) o.text = t.slice(0, 90);
    var r = el.getBoundingClientRect();
    o.visible = !!(r.width > 0 && r.height > 0);
    return o;
}
var out = {url: location.href, inputs: [], selects: [], buttons: [],
           testIds: [], packageHints: []};
document.querySelectorAll('input, kat-input').forEach(function(e){ out.inputs.push(attrs(e)); });
document.querySelectorAll('select, kat-dropdown').forEach(function(e){ out.selects.push(attrs(e)); });
document.querySelectorAll('button, kat-button, a[role=button], span[data-test-id]')
    .forEach(function(e){ out.buttons.push(attrs(e)); });
document.querySelectorAll('[data-test-id]').forEach(function(e){
    out.testIds.push(e.getAttribute('data-test-id'));
});
out.testIds = Array.from(new Set(out.testIds)).sort();
// Anything whose text hints at the multi-package flow.
var rx = /package|split|add another|multiple|shipment \\d/i;
document.querySelectorAll('button, kat-button, a, span, div, label').forEach(function(e){
    var t = (e.innerText || '').trim();
    if (t && t.length < 80 && rx.test(t)) out.packageHints.push(attrs(e));
});
return out;
"""


def main():
    ap = argparse.ArgumentParser(description="Read-only confirm-shipment DOM dump")
    ap.add_argument("order_id", help="Amazon order id, e.g. 114-1668349-3077818")
    ap.add_argument("--account", default="hathoro",
                    help="Which tracker Chrome profile to reuse (default: hathoro)")
    ap.add_argument("--keep-open", action="store_true",
                    help="Leave the browser open after dumping")
    args = ap.parse_args()

    acct = next((a for a in AMAZON_ACCOUNTS if a["key"] == args.account), None)
    if not acct:
        keys = ", ".join(a["key"] for a in AMAZON_ACCOUNTS)
        sys.exit(f"unknown account '{args.account}' — choose one of: {keys}")

    profile_path = resolve_user_data_dir(acct)
    print(f"👤 Chrome profile: {profile_path}")

    options = Options()
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"user-data-dir={profile_path}")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")

    driver = create_chrome_driver(options)
    try:
        url = (f"https://sellercentral.amazon.com/orders-v3/order/"
               f"{args.order_id}/confirm-shipment")
        print(f"🌐 {url}")
        driver.get(url)

        # The form sometimes needs the "Confirm shipment" button clicked to mount.
        # That only REVEALS the form — it does not submit anything.
        try:
            WebDriverWait(driver, 8).until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, INITIAL_CONFIRM_SHIPMENT_BUTTON_SELECTOR))).click()
            print("   (clicked 'Confirm shipment' to reveal the form — nothing submitted)")
        except Exception:
            pass

        time.sleep(5)   # let the SPA finish rendering

        if "signin" in driver.current_url or "/ap/" in driver.current_url:
            sys.exit("🛑 This Chrome profile is not signed in to Seller Central. "
                     "Sign in once in that profile, then re-run.")

        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        html_path = os.path.join(OUT_DIR, "confirm_shipment_structure.html")
        json_path = os.path.join(OUT_DIR, "confirm_shipment_structure.json")

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(f"<!-- {args.order_id} | {stamp} | {driver.current_url} -->\n")
            f.write(driver.page_source)

        probe = driver.execute_script(PROBE_JS) or {}
        probe["order_id"] = args.order_id
        probe["captured_at"] = stamp
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(probe, f, indent=2)

        print(f"\n✅ wrote {html_path}")
        print(f"✅ wrote {json_path}")
        print(f"\n   inputs={len(probe.get('inputs', []))}  "
              f"selects={len(probe.get('selects', []))}  "
              f"buttons={len(probe.get('buttons', []))}  "
              f"test-ids={len(probe.get('testIds', []))}  "
              f"package-hints={len(probe.get('packageHints', []))}")

        hints = probe.get("packageHints") or []
        if hints:
            print("\n   multi-package candidates:")
            for h in hints[:12]:
                print(f"     • <{h.get('tag')}> "
                      f"test-id={h.get('data-test-id', '—')} "
                      f"text={h.get('text', '')[:60]!r}")
        else:
            print("\n   ⚠️  no obvious package/split controls found — the order may be "
                  "qty 1, or already confirmed (Amazon only offers a package split "
                  "when quantity > 1 and the order is still unshipped).")

        if args.keep_open:
            input("\nPress ENTER to close the browser… ")
    finally:
        if not args.keep_open:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
