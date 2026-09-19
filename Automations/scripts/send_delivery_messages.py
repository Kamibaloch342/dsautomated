"""
send_delivery_messages.py — the DECOUPLED post-delivery messaging pass.

Design: the closing pipeline runs extraction-only (fast: report → enrich →
FedEx → label → persist); THIS script then messages from the DB afterwards, so
buyer messaging never sits inside the pipeline's critical path. Run it any time
— it picks up every delivered order that still needs a delivery check-in and/or
review request, sends what's due, and records the result. All the safety guards
(already-sent Gmail confirm, return/refund skip, customer-inquiry skip, zip
mismatch, 6/12-day windows, never-downgrade-terminal-status) live in
engine/post_delivery.process_single_order and are reused as-is.

Closes the daily_bulk gap where an order delivered via fedex_recheck and
completed by reconcile was never messaged (closing's resume skipped it, and
neither fedex_recheck nor reconcile message anyone).

PREREQ: the account's logged-in headed SC Chrome (HOANK→9331, Hathoro→9333).
Attach-only — if it isn't running, the script stops safely (messages stay
pending for the next run) and NEVER launches a blank not-logged-in Chrome.

Usage:
  MSG_ACCOUNT=HOANK ../.venv/bin/python scripts/send_delivery_messages.py
Env: MSG_ACCOUNT (HOANK) | MSG_DRY=1 (preview only)
     MSG_LIMIT (default 300 per run ≈ one day's delivered volume, so a backlog
     drains gradually instead of blasting thousands of buyer messages at once —
     Amazon reads that as spam; 0 = unlimited, use deliberately)
     MSG_MAX_DAYS (30 — don't message deliveries older than this; Amazon's
     review-request window is ~30 days and a check-in that late reads as spam)
"""
import os
import sys
import sqlite3

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AUTO not in sys.path:
    sys.path.insert(0, _AUTO)
print = __import__("functools").partial(print, flush=True)

from utils.accounts_config import accounts, gmail_tokens, acquire_lock, release_lock
from engine.post_delivery import (
    process_single_order, _get_gmail_services, _delivery_days_ago,
    _is_driver_alive, ACCOUNT_CONFIGS,
    DAYS_AFTER_DELIVERY_TO_MESSAGE, DAYS_AFTER_DELIVERY_TO_REVIEW,
)
from engine.amazon_scraper import _connect_chrome

ACCOUNT  = os.environ.get("MSG_ACCOUNT", "HOANK").strip()
DRY      = os.environ.get("MSG_DRY", "").strip().lower() in ("1", "true", "yes")
LIMIT    = int(os.environ.get("MSG_LIMIT", "300") or 0)   # 0 = unlimited (deliberate only)
MAX_DAYS = int(os.environ.get("MSG_MAX_DAYS") or 30)
DB       = os.path.join(_AUTO, "data", "automations_unified.db")

_MSG_TERMINAL    = ("Yes", "No Permission")
_REVIEW_TERMINAL = ("Yes", "Already Requested", "Expired")


def load_pending():
    """Delivered orders for THIS account still owed a message and/or review,
    delivered 6–MAX_DAYS ago. Refunded/returned orders are excluded up front
    (pd re-checks Gmail anyway — this just avoids wasted lookups)."""
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    ph_m = ",".join("?" * len(_MSG_TERMINAL))
    ph_r = ",".join("?" * len(_REVIEW_TERMINAL))
    rows = conn.execute(
        f"""SELECT * FROM closing_orders
            WHERE account = ?
              AND delivery_status IN ('Delivered', 'AllDelivered')
              AND delivery_date IS NOT NULL AND delivery_date != ''
              AND COALESCE(order_label, '') NOT IN ('Refunded', 'Cancelled')
              AND COALESCE(return_requested, '') != 'Yes'
              AND COALESCE(refund_done, '') != 'Yes'
              AND (COALESCE(delivered_msg_sent, '') NOT IN ({ph_m})
                   OR COALESCE(review_requested, '') NOT IN ({ph_r}))
            ORDER BY delivery_date ASC""",
        [ACCOUNT, *_MSG_TERMINAL, *_REVIEW_TERMINAL]).fetchall()
    conn.close()

    pending = []
    for r in rows:
        o = dict(r)
        days = _delivery_days_ago(str(o.get("delivery_date") or ""))
        if days < DAYS_AFTER_DELIVERY_TO_MESSAGE or days > MAX_DAYS:
            continue
        msg_due = (o.get("delivered_msg_sent") or "") not in _MSG_TERMINAL \
            and days >= DAYS_AFTER_DELIVERY_TO_MESSAGE
        rev_due = (o.get("review_requested") or "") not in _REVIEW_TERMINAL \
            and days >= DAYS_AFTER_DELIVERY_TO_REVIEW
        if msg_due or rev_due:
            pending.append(o)
    return pending


def main():
    cfgs = accounts()
    if ACCOUNT not in cfgs:
        print(f"[MSG] ❌ Unknown account '{ACCOUNT}' (use {' or '.join(cfgs)})")
        return 2
    pd_cfg = ACCOUNT_CONFIGS[cfgs[ACCOUNT]["pd_key"]]
    sc_port = cfgs[ACCOUNT]["sc_port"]

    got, holder = acquire_lock(f"messages_{ACCOUNT}")
    if not got:
        print(f"[MSG] ❌ Another messaging run for {ACCOUNT} is active (pid {holder}) — aborting.")
        return 3
    try:
        return _run(pd_cfg, sc_port)
    finally:
        release_lock(f"messages_{ACCOUNT}")


def _run(pd_cfg, sc_port):
    pending = load_pending()
    print(f"[MSG] {ACCOUNT}: {len(pending)} delivered order(s) owed a message/review "
          f"(window {DAYS_AFTER_DELIVERY_TO_MESSAGE}–{MAX_DAYS} days, dry={DRY})")
    if not pending:
        print("[MSG] ✅ Nothing to send.")
        return 0
    if LIMIT:
        pending = pending[:LIMIT]

    driver = None
    if not DRY:
        # Attach to the closing chain's Chrome if it is still up, otherwise start one exactly
        # as closing does. ensure_chrome honours the closing headless switch and uses the same
        # ~/.closing_chrome_profiles profile, so messaging inherits the logged-in session and
        # the same windowless mode instead of demanding a browser someone left open. Messaging
        # is usually run on its own, long after the closing run that used to leave one behind.
        from utils.proc import port_open
        driver = _connect_chrome(sc_port) if port_open(sc_port) else None
        if not driver:
            try:
                import closing
                print(f"[MSG] No Chrome on port {sc_port} — starting one the way closing does...")
                driver = closing.ensure_chrome(sc_port)
            except Exception as e:
                print(f"[MSG] ⚠️ Could not start Chrome on {sc_port}: {type(e).__name__}: {e}")
                driver = None
        if not driver:
            print(f"[MSG] ⚠️ No logged-in Chrome on port {sc_port} — messages stay pending. "
                  f"Run ▶ Run Closing for {ACCOUNT} first, or press 🚀 Chrome, and re-run.")
            return 2

    gmail_services = _get_gmail_services(gmail_tokens())
    if not gmail_services:
        # Without Gmail we can't run the return/refund + already-sent guards —
        # sending blind risks messaging a buyer who has an open return. Refuse.
        print("[MSG] ❌ No Gmail services could authenticate — refusing to message "
              "without the return/refund guards. Fix the tokens and re-run.")
        return 2

    sent = reviewed = skipped = failed = 0
    for o in pending:
        if driver is not None and not _is_driver_alive(driver):
            driver = _connect_chrome(sc_port)
            if not driver:
                try:                       # same recovery as at startup
                    import closing
                    driver = closing.ensure_chrome(sc_port)
                except Exception:
                    driver = None
            if not driver:
                print("[MSG] ⚠️ Chrome died mid-run and would not re-attach — stopping; "
                      "the rest stay pending.")
                break
        try:
            res = process_single_order(driver, o, pd_cfg, gmail_services,
                                       closing_table=True, dry_run=DRY)
            if res.get("delivered_msg_sent") == "Yes":
                sent += 1
            elif res.get("customer_messaged") == "Yes" or res.get("return_requested") == "Yes" \
                    or res.get("refund_done") == "Yes":
                skipped += 1
            if res.get("review_requested") in ("Yes", "Already Requested"):
                reviewed += 1
        except Exception as e:
            failed += 1
            print(f"[MSG]   {o.get('amazon_order_id')}: failed — {e}")

    print(f"[MSG] ✅ DONE — {sent} delivery messages, {reviewed} review requests, "
          f"{skipped} guarded-skip, {failed} errors (of {len(pending)} pending)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
