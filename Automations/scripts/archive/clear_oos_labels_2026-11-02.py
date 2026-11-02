#!/usr/bin/env python3
"""clear_oos_labels_2026-11-02.py — one-off: take back the OOS labels the placers stamped on Zoro
code G5695541 on 2 Nov 2026, so those orders return to the queue (owner's decision).

Also used on 3 Nov for the 13 orders wrongly stamped "<WB> - D" by the product-blind Gmail check
(--plan data/dash_d_clear_plan_2026-11-03.json --result ...): those plan rows name the exact label to take
back, and each is proven again just before the write. They must not be cleared while a placer still runs the
old check, or its next cycle stamps them again: --wait-for-new-code holds the run until none does.

G5695541 ran out at 20:51 and the old code stamped each of its orders OOS, a label nothing ever takes
back. Ultra mode now holds such orders instead, so these are the only ones left to repair. The plan
(data/oos_clear_plan_2026-11-02.json) holds each order's label from before the stamp, taken from
amazon_report_orders: 165 go back to blank, 15 to 'Rajar'.

Runs on the store's Seller Central Chrome, not a placer's. It holds that Chrome's sc_turn for the whole run and
never goes ahead without it: a tracker drives the same tab, and on 2 Nov this script, set to "proceed" after a
2-minute wait, navigated the tab under a running tracker. A busy Chrome now means waiting, then stopping.

Guards:
  * Only a label reading exactly what the plan expects (OOS, unless the row says otherwise) is ever changed.
    A WB id or anything a person typed is left alone.
  * Writes go through the guarded update_amazon_seller_id (page-identity check, read-back).
  * Cancelled orders and pages with no Edit button are skipped and listed.
  * Stops on a login page or after 5 failures in a row: this script never signs in.
  * Every result is appended to data/oos_clear_result_2026-11-02.csv, and a rerun skips what is done.

    1) Launcher → Closing page → 🚀 Launch HOANK (port 9331); Seller Central must be logged in.
    2) python scripts/archive/clear_oos_labels_2026-11-02.py                  # DRY RUN, writes nothing
    3) python scripts/archive/clear_oos_labels_2026-11-02.py --apply --limit 1 --reload-verify 1
    4) python scripts/archive/clear_oos_labels_2026-11-02.py --apply
    5) python scripts/archive/clear_oos_labels_2026-11-02.py --verify         # reads every order back
"""
import argparse, csv, json, os, re, sqlite3, subprocess, sys, time

AUTO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, AUTO)
print = __import__("functools").partial(print, flush=True)

PLAN = os.path.join(AUTO, "data", "oos_clear_plan_2026-11-02.json")
RESULT = os.path.join(AUTO, "data", "oos_clear_result_2026-11-02.csv")
MAX_FAILS_IN_A_ROW = 5
TURN_WAIT_S = 2700

# Same read as update_amazon_seller_id's own read-back, plus the two things that decide what may be done.
READ_JS = r"""
var body = ((document.body && document.body.innerText) || '').replace(/\s+/g, ' ');
var el = document.querySelector('[data-test-id="your-seller-order-id"], [data-test-id="merchant-order-id"]');
var t = el ? (el.innerText || el.textContent || el.value || '') : '';
t = t.replace(/\s+/g, ' ').trim();
if (!t || t === '#' || t === 'Edit') {
    var m = body.match(/(?:Your seller order ID|Merchant order ID)\s*:?\s*(.*?)(?:\s+Edit\b|\s+Go back|\s+Print packing|$)/i);
    t = m ? m[1].trim() : '';
}
var edit = !!document.querySelector(arguments[0]);
return {label: t.replace(/^#/, '').trim(), edit: edit,
        cancelled: !edit && /order cancelled|this order has been cancel+ed/i.test(body),
        ready: document.readyState};
"""


def _norm(label):
    return re.sub(r"\s+", " ", label or "").strip().upper()


def still_false_duplicate(p):
    """A "- D" row, checked again just before the write: the WB is still proven another product, and this order
    has not been bought or claimed since. (ok, why)"""
    from utils import dup_check_local as L
    if not L.gmail_match_is_other_product(p["marked_duplicate_of"], p.get("asin"), p.get("zoro_code"), p["amazon_id"]):
        return False, "no longer proven another product"
    try:
        c = sqlite3.connect(f"file:{L.DB}?mode=ro", uri=True, timeout=5)
        try:
            own = c.execute("SELECT wb_order_id FROM order_placer_orders WHERE amazon_order_id=? "
                            "AND COALESCE(wb_order_id,'')!='' LIMIT 1", (p["amazon_id"],)).fetchone()
            claim = c.execute("SELECT status FROM placer_claims WHERE amazon_order_id=?", (p["amazon_id"],)).fetchone()
        finally:
            c.close()
    except Exception as e:
        return False, f"could not check ({e})"
    if own:
        return False, f"already bought as {own[0]}"
    if claim and claim[0] != "RELEASED":
        return False, f"placement claim {claim[0]}"
    return True, ""


def placers_on_old_code(profiles, files):
    """The listed placers still running code older than these files. One not running is no risk: it loads the
    new code when it starts."""
    newest = max(os.path.getmtime(os.path.join(AUTO, f)) for f in files)
    out = subprocess.run(["ps", "-Ao", "etime=,command="], capture_output=True, text=True).stdout
    old = []
    for line in out.splitlines():
        m = re.match(r"\s*(\S+)\s.*engine/orders\.py (\w+)", line)
        if m and m.group(2) in profiles:
            parts = [int(x) for x in re.split("[-:]", m.group(1))]      # macOS has etime, not etimes
            age = sum(v * u for v, u in zip(parts[::-1], (1, 60, 3600, 86400)))
            if time.time() - age <= newest:
                old.append(m.group(2))
    return old


def read_page(driver, selector, wait_s=12):
    """The order page's label, once its Edit button is there or the page says it is cancelled."""
    end = time.time() + wait_s
    info = {"label": "", "edit": False, "cancelled": False, "ready": ""}
    while time.time() < end:
        info = driver.execute_script(READ_JS, selector)
        if info["ready"] == "complete" and (info["edit"] or info["cancelled"]):
            return info
        time.sleep(0.4)
    return info


def done_ids():
    """Orders a previous run finished with: restored, or found not to need it."""
    if not os.path.exists(RESULT):
        return set()
    with open(RESULT, newline="", encoding="utf-8") as f:
        return {r["amazon_id"] for r in csv.DictReader(f)
                if r["result"] in ("restored", "already_restored", "label_differs", "no_longer_proven", "cancelled")}


def record(amazon_id, before, restore, result, note=""):
    new = not os.path.exists(RESULT)
    with open(RESULT, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "amazon_id", "label_found", "restore_to", "result", "note"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), amazon_id, before, restore, result, note])


def attach(port):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from utils.helpers import ensure_chromedriver, harden_driver
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
    try:
        return harden_driver(webdriver.Chrome(service=Service(ensure_chromedriver()), options=opts))
    except Exception as e:
        print(f"❌ cannot attach to Chrome on port {port} — launch it from the launcher's Closing page first ({e})")
        sys.exit(1)


def run(driver, plan, port, apply=False, verify=False, limit=0, reload_verify=0):
    """Returns a Counter of results. Separate from main() so it can be exercised on a test Chrome."""
    from collections import Counter
    import platforms.amazon as A
    from platforms.auth import is_login_page
    from utils.config import AMAZON_EDIT_BUTTON_SELECTOR
    from utils.helpers import safe_get

    skip = set() if verify else done_ids()
    todo = [p for p in plan if p["amazon_id"] not in skip]
    if limit:
        todo = todo[:limit]
    mode = "VERIFY" if verify else ("APPLY" if apply else "DRY RUN")
    print(f"=== {mode}: {len(todo)} order(s) to look at ({len(plan) - len(todo)} done or beyond --limit) ===")
    out, fails, n = Counter(), 0, 0
    for p in todo:
        aid, restore = p["amazon_id"], p.get("restore") or ""
        n += 1
        before = ""
        try:
            safe_get(driver, f"{A.AMAZON_BASE_ORDER_URL}{aid}")
            if is_login_page(driver):
                print("🔐 Seller Central wants a login — stopping. Log in on this Chrome and run again; "
                      "finished orders are skipped.")
                out["stopped_login"] += 1
                return out
            if aid not in (driver.current_url or ""):
                result, before, note = "wrong_page", "", (driver.current_url or "")[:80]
            else:
                info = read_page(driver, AMAZON_EDIT_BUTTON_SELECTOR)
                before, note = info["label"], ""
                if info["cancelled"]:
                    result = "cancelled"
                elif not info["edit"]:
                    result, note = "no_edit_button", "left alone"
                elif verify:
                    result = "verified" if before == restore else "verify_mismatch"
                elif before == restore and _norm(before) != _norm(p.get("expect") or "OOS"):
                    result = "already_restored"
                elif _norm(before) != _norm(p.get("expect") or "OOS"):
                    result, note = "label_differs", "left alone"
                elif p.get("marked_duplicate_of") and not still_false_duplicate(p)[0]:
                    result, note = "no_longer_proven", still_false_duplicate(p)[1] + " — left alone"
                elif not apply:
                    result = "would_restore"
                elif not A.update_amazon_seller_id(driver, aid, restore, None, skip_navigate=True):
                    result, note = "write_failed", "still OOS — fix by hand or run again"
                else:
                    result = "restored"
                    if n <= reload_verify:
                        safe_get(driver, f"{A.AMAZON_BASE_ORDER_URL}{aid}")
                        seen = read_page(driver, AMAZON_EDIT_BUTTON_SELECTOR)["label"]
                        note = f"after reload the page reads '{seen}'"
                        if seen != restore:
                            result = "verify_mismatch"
        except Exception as e:
            result, note = "error", f"{type(e).__name__}: {str(e)[:120]}"
        record(aid, before, restore, result, note)
        out[result] += 1
        print(f"  {n}/{len(todo)} {aid}: found '{before}' → {result}"
              + (f" '{restore}'" if result == "restored" else "") + (f" ({note})" if note else ""))
        fails = fails + 1 if result in ("write_failed", "error", "wrong_page", "verify_mismatch") and not verify else 0
        if fails >= MAX_FAILS_IN_A_ROW:
            print(f"🛑 {fails} failures in a row — stopping. Finished orders are skipped on the next run.")
            out["stopped_failures"] += 1
            return out
        if apply:
            time.sleep(1)
    return out


def main():
    global RESULT
    from utils.sc_chrome import holder
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write; without this nothing is changed")
    ap.add_argument("--verify", action="store_true", help="read every order back, write nothing")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reload-verify", type=int, default=0, help="reopen the first N orders and read them back")
    ap.add_argument("--port", type=int, default=9331)
    ap.add_argument("--plan", default=PLAN)
    ap.add_argument("--result", default=RESULT)
    ap.add_argument("--turn-wait-min", type=float, default=TURN_WAIT_S / 60)
    ap.add_argument("--wait-for-new-code", default="", help="comma-separated profiles that must not be on old code")
    a = ap.parse_args()
    RESULT = a.result
    with open(a.plan, encoding="utf-8") as f:
        plan = json.load(f)
    if a.wait_for_new_code and a.apply:
        profiles = [x.strip() for x in a.wait_for_new_code.split(",") if x.strip()]
        files = ["platforms/gmail.py", "utils/dup_check_local.py"]
        said, end = None, time.time() + 12 * 3600
        while True:
            old = placers_on_old_code(profiles, files)
            if not old:
                break
            if old != said:
                print(f"Waiting: {', '.join(old)} still on the old duplicate check — nothing is written until they restart")
                said = old
            if time.time() > end:
                print("🛑 Gave up after 12 h: placers never restarted. Nothing written.")
                sys.exit(2)
            time.sleep(20)
        print("All listed placers are on the new code.")
    from utils.sc_chrome import sc_turn, SCBusy
    print(f"Waiting for this Chrome's turn (holder now: {holder(a.port) or 'nobody'})...")
    try:
        with sc_turn(a.port, "oos-clear", max_wait=a.turn_wait_min * 60, on_timeout="skip"):
            # Only now: the wait can outlast the Chrome. On 2 Nov the tracker restarted during it and
            # closed this Chrome, and a connection made before the wait was dead when the turn came.
            driver = attach(a.port)
            out = run(driver, plan, a.port, apply=a.apply and not a.verify, verify=a.verify, limit=a.limit,
                      reload_verify=a.reload_verify)
    except SCBusy as e:
        print(f"🛑 {e} — nothing written. Run again later; finished orders are skipped.")
        sys.exit(2)
    print(f"=== done: {dict(out)} ===")
    print(f"    details: {RESULT}")
    try:
        driver.service.process.kill()          # this Chrome stays open: other work shares it
    except Exception:
        pass


if __name__ == "__main__":
    main()
