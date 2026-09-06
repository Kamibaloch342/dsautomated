#!/usr/bin/env python3
"""repair_mislabeled_wb.py — fix Amazon seller-order-id labels corrupted by the
2026-08-19/20 wrong-page incident, from PLACEMENT-TIME truth only.

Per mislabeled order (same detection as detect_mislabeled_wb.py):
  • has a placement-truth WB           → label := that WB (relinks order↔WB)
  • Pending + NO placement record      → label := ""  (unhides it: the placer can
                                         finally see and PLACE this real order)
  • Shipped + NO placement record      → SKIPPED, listed for manual review
                                         (clearing could tempt a re-buy of an old
                                         fulfilled order — never automated)

All writes go through the guarded update_amazon_seller_id (page-identity check:
re-navigate once, refuse on mismatch — the guard added after this incident).

    1) Launcher → Closing page → 🚀 Launch HOANK (port 9331) / Hathoro (9333),
       make sure Seller Central is logged in for that account.
    2) python3 scripts/repair_mislabeled_wb.py --account HOANK            # DRY RUN
    3) python3 scripts/repair_mislabeled_wb.py --account HOANK --apply    # write

Every action (and read-back result) is appended to data/mislabel_repair_log.txt.
"""
import argparse, os, re, sqlite3, sys, time

AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AUTO)
DB = os.path.join(AUTO, "data", "automations_unified.db")
LOGF = os.path.join(AUTO, "data", "mislabel_repair_log.txt")
WB = re.compile(r"\b(W[BW]\d{6,})\b")
PORTS = {"HOANK": 9331, "Hathoro": 9333}


def log(msg):
    line = f"[{time.strftime('%F %T')}] {msg}"
    print(line, flush=True)
    with open(LOGF, "a") as f:
        f.write(line + "\n")


def placement_truth_owner_map(c):
    owner = {}
    for r in c.execute("SELECT amazon_order_id, wb_order_id FROM placer_claims "
                       "WHERE status='CONFIRMED' AND COALESCE(wb_order_id,'')!=''"):
        for wb in WB.findall(r["wb_order_id"]):
            owner.setdefault(wb, r["amazon_order_id"])
    for r in c.execute("SELECT amazon_order_id, wb_order_id FROM order_placer_orders "
                       "WHERE COALESCE(wb_order_id,'')!=''"):
        for wb in WB.findall(r["wb_order_id"]):
            owner.setdefault(wb, r["amazon_order_id"])
    logp = os.path.join(AUTO, "data", "rajar_logs", "order_log.txt")
    if os.path.exists(logp):
        rx = re.compile(r"WB:(W[BW]\d+).*?Amazon:(\d{3}-\d{7}-\d{7})")
        with open(logp, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                m = rx.search(line)
                if m:
                    owner.setdefault(m.group(1), m.group(2))
    return owner


def build_worklist(account):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c.row_factory = sqlite3.Row
    owner = placement_truth_owner_map(c)
    fix, manual = [], []
    for r in c.execute("SELECT amazon_order_id, merchant_order_id, order_status FROM closing_orders "
                       "WHERE account=? AND COALESCE(merchant_order_id,'')!=''", (account,)):
        foreign = [wb for wb in WB.findall(r["merchant_order_id"])
                   if owner.get(wb) and owner[wb] != r["amazon_order_id"]]
        if not foreign:
            continue
        own = sorted(wb for wb, o in owner.items() if o == r["amazon_order_id"])
        if own:
            fix.append((r["amazon_order_id"], r["merchant_order_id"], " ".join(own)))
        elif (r["order_status"] or "") == "Pending":
            fix.append((r["amazon_order_id"], r["merchant_order_id"], ""))   # clear → placer sees it
        else:
            manual.append((r["amazon_order_id"], r["merchant_order_id"], r["order_status"]))
    c.close()
    return fix, manual


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, choices=list(PORTS))
    ap.add_argument("--apply", action="store_true", help="write to Amazon (default: dry run)")
    a = ap.parse_args()

    fix, manual = build_worklist(a.account)
    log(f"=== repair {a.account}: {len(fix)} to fix, {len(manual)} manual-review "
        f"({'APPLY' if a.apply else 'DRY RUN'}) ===")
    for amz, cur, new in fix:
        log(f"  {amz}: '{cur}' → '{new or '(CLEAR — unplaced order, placer will pick it up)'}'")
    for amz, cur, st in manual:
        log(f"  MANUAL: {amz} [{st}] '{cur}' — shipped with no placement record; not touching")
    if not a.apply:
        log("dry run only — re-run with --apply to write."); return
    if not fix:
        log("nothing to write."); return

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from utils.helpers import ensure_chromedriver
    from platforms.amazon import update_amazon_seller_id

    port = PORTS[a.account]
    opts = ChromeOptions()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
    try:
        driver = webdriver.Chrome(service=ChromeService(ensure_chromedriver()), options=opts)
    except Exception as e:
        log(f"❌ cannot attach to Chrome on port {port} — launch it from the launcher's "
            f"Closing page first ({e})"); sys.exit(1)

    ok = fail = 0
    for amz, cur, new in fix:
        res = update_amazon_seller_id(driver, amz, new)
        if res:
            ok += 1; log(f"  ✅ {amz} → '{new or '(cleared)'}'")
        else:
            fail += 1; log(f"  ❌ {amz} write not verified — fix by hand")
        time.sleep(2)
    log(f"=== done: {ok} fixed, {fail} failed, {len(manual)} left for manual review ===")


if __name__ == "__main__":
    main()
