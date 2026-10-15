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
     MSG_LIMIT (default 0 = no cap — owner, 15 Oct 2026; was 1000 from 29 Sep, but with
     review requests silently cancelled the backlog only grew)
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
    process_single_order, process_delay_notice, _get_gmail_services, _delivery_days_ago,
    _is_driver_alive, ACCOUNT_CONFIGS, prefetch_guards,
    DAYS_AFTER_DELIVERY_TO_MESSAGE, DELIVERY_MESSAGE_MAX_DAYS, DAYS_AFTER_DELIVERY_TO_REVIEW,
    REVIEW_MAX_DAYS, DELAY_NOTICE_ENABLED, DELAY_NOTICE_STALLED_DAYS, DELAY_NOTICE_MAX_STALLED_DAYS,
    DELAY_NOTICE_MAX_DAYS, DELAY_NOTICE_FRESH_HOURS,
)
from engine.amazon_scraper import _connect_chrome

ACCOUNT  = os.environ.get("MSG_ACCOUNT", "HOANK").strip()
DRY      = os.environ.get("MSG_DRY", "").strip().lower() in ("1", "true", "yes")
LIMIT    = int(os.environ.get("MSG_LIMIT", "0") or 0)     # 0 = no cap (owner, 15 Oct 2026)
MAX_DAYS = int(os.environ.get("MSG_MAX_DAYS") or REVIEW_MAX_DAYS)
DELAY_LIMIT = int(os.environ.get("MSG_DELAY_LIMIT", "100") or 0)   # delay notices per run
DB       = os.path.join(_AUTO, "data", "automations_unified.db")

_MSG_TERMINAL    = ("Yes", "No Permission")
_REVIEW_TERMINAL = ("Yes", "Already Requested", "Expired")


def _prefilter_from_archive(conn):
    """Answer the guard questions from the Gmail ARCHIVE first (synced hourly), so the live
    per-order searches only run for orders the archive knows nothing about. Same rules the
    messaging pass applies, written to the row: a return/refund email → return_requested /
    refund_done; an inbound buyer message → customer_messaged; one of OUR delivery check-ins
    already archived (message_type='delivery_checkin') → delivered_msg_sent='Yes'. Only
    delivered, non-terminal rows of this account are touched; nothing is ever un-set."""
    live = ("account=? AND delivery_status IN ('Delivered','AllDelivered')"
            " AND COALESCE(order_label,'') NOT IN ('Refunded','Cancelled')")
    n = {}
    try:
        n["return"] = conn.execute(
            f"UPDATE closing_orders SET return_requested='Yes' WHERE {live} AND COALESCE(return_requested,'')!='Yes'"
            " AND amazon_order_id IN (SELECT amazon_order_id FROM archive_returns)", (ACCOUNT,)).rowcount
        n["refund"] = conn.execute(
            f"UPDATE closing_orders SET refund_done='Yes' WHERE {live} AND COALESCE(refund_done,'')!='Yes'"
            " AND amazon_order_id IN (SELECT amazon_order_id FROM archive_returns"
            "   WHERE email_type='refund_initiated' OR refund_done='Yes')", (ACCOUNT,)).rowcount
        n["inquiry"] = conn.execute(
            f"UPDATE closing_orders SET customer_messaged='Yes' WHERE {live} AND COALESCE(customer_messaged,'')!='Yes'"
            " AND amazon_order_id IN (SELECT amazon_order_id FROM archive_messages"
            "   WHERE direction='inbound' OR message_type IN ('buyer_inquiry','buyer_reply'))", (ACCOUNT,)).rowcount
        n["checkin"] = conn.execute(
            f"UPDATE closing_orders SET delivered_msg_sent='Yes' WHERE {live}"
            f" AND COALESCE(delivered_msg_sent,'') NOT IN ({','.join('?' * len(_MSG_TERMINAL))})"
            " AND amazon_order_id IN (SELECT amazon_order_id FROM archive_messages"
            "   WHERE message_type='delivery_checkin')", (ACCOUNT, *_MSG_TERMINAL)).rowcount
        conn.commit()
    except sqlite3.OperationalError as e:
        print(f"[MSG] archive pre-filter skipped: {e}")
    if any(n.values()):
        print(f"[MSG] archive pre-filter: return +{n.get('return', 0)}, refund +{n.get('refund', 0)}, "
              f"buyer wrote +{n.get('inquiry', 0)}, check-in already sent +{n.get('checkin', 0)}")


def load_pending():
    """Delivered orders for THIS account still owed a message and/or review,
    delivered 6–MAX_DAYS ago. Refunded/returned orders and orders whose buyer wrote to us
    are excluded up front (pd re-checks Gmail anyway — this just avoids wasted lookups)."""
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    _prefilter_from_archive(conn)
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
              AND COALESCE(customer_messaged, '') != 'Yes'
              AND (COALESCE(delivered_msg_sent, '') NOT IN ({ph_m})
                   OR COALESCE(review_requested, '') NOT IN ({ph_r}))
            ORDER BY delivery_date ASC""",
        [ACCOUNT, *_MSG_TERMINAL, *_REVIEW_TERMINAL]).fetchall()
    conn.close()

    pending = []
    for r in rows:
        o = dict(r)
        days = _delivery_days_ago(str(o.get("delivery_date") or ""))
        if days < 0 or days > MAX_DAYS:
            continue
        # check-in: from the delivery day (default 0) up to DELIVERY_MESSAGE_MAX_DAYS (7);
        # review: from DAYS_AFTER_DELIVERY_TO_REVIEW (6) up to MAX_DAYS (30).
        msg_due = (o.get("delivered_msg_sent") or "") not in _MSG_TERMINAL \
            and DAYS_AFTER_DELIVERY_TO_MESSAGE <= days <= DELIVERY_MESSAGE_MAX_DAYS
        rev_due = (o.get("review_requested") or "") not in _REVIEW_TERMINAL \
            and days >= DAYS_AFTER_DELIVERY_TO_REVIEW
        if msg_due or rev_due:
            o["_days"], o["_msg_due"], o["_rev_due"] = days, msg_due, rev_due
            pending.append(o)
    # delivery_date is a weekday-first string, so the SQL order is alphabetical — order here.
    # Two queues, taken one-for-one: check-ins (freshest delivery first — "as soon as it is
    # delivered") and review requests (oldest first, so the ones about to leave Amazon's 30-day
    # window go before they are lost). A run that ends on MSG_LIMIT or its timeout used to spend
    # everything on one kind: the 27 Sep chain sent 440 check-ins and not one review request
    # while ~12,000 reviews were due. An order owed both is in the check-in queue (one visit).
    # Oldest delivery first (owner, 15 Oct 2026): an order nearest the end of Amazon's 30-day
    # review window goes before it is lost; an order owed both is one visit.
    return sorted(pending, key=lambda o: -o["_days"])


def load_delayed():
    """Shipped, undelivered orders of THIS account whose carrier scans stopped between
    DELAY_NOTICE_STALLED_DAYS and DELAY_NOTICE_MAX_STALLED_DAYS days ago per the LAST Zoro
    delivery check (fresh within DELAY_NOTICE_FRESH_HOURS — a stale answer is not a reason to
    alarm a buyer), not yet noticed, not cancelled/refunded/returned, buyer silent, purchased
    within DELAY_NOTICE_MAX_DAYS. Older stalls stay on the dashboard for a person; freight
    orders never qualify (no delivery_status)."""
    if not DELAY_NOTICE_ENABLED:
        return []
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(closing_orders)")}
        if "stalled_days" not in cols:
            return []
        rows = conn.execute(
            f"""SELECT * FROM closing_orders
                WHERE account = ?
                  AND COALESCE(wb_order_id, '') != ''
                  AND COALESCE(delivery_status, '') NOT IN ('', 'Delivered', 'AllDelivered')
                  AND COALESCE(delivery_note, '') NOT LIKE 'Freight carrier%'
                  AND (order_status = 'Shipped' OR COALESCE(shipped_email_time, '') != '')
                  AND COALESCE(order_status, '') != 'Cancelled'
                  AND COALESCE(order_label, '') NOT IN ('Refunded', 'Cancelled')
                  AND COALESCE(return_requested, '') != 'Yes'
                  AND COALESCE(refund_done, '') != 'Yes'
                  AND COALESCE(customer_messaged, '') != 'Yes'
                  AND COALESCE(delay_notice_sent, '') NOT IN ({','.join('?' * len(_MSG_TERMINAL))})
                  AND COALESCE(stalled_days, 0) BETWEEN ? AND ?
                  AND COALESCE(delivery_checked_at, '') >= datetime('now', 'localtime', ?)
                  AND substr(purchase_date, 1, 10) >= date('now', ?)
                ORDER BY stalled_days ASC, purchase_date DESC""",
            [ACCOUNT, *_MSG_TERMINAL, DELAY_NOTICE_STALLED_DAYS, DELAY_NOTICE_MAX_STALLED_DAYS,
             f"-{DELAY_NOTICE_FRESH_HOURS} hours", f"-{DELAY_NOTICE_MAX_DAYS} day"]).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _alert_sent(account, before, res):
    """One WhatsApp line per order where a check-in or a review request ACTUALLY went through on
    this visit (owner, 15 Oct 2026). Errors, 'No Permission', 'Already Requested' and 'Expired'
    are never posted. Only accounts with a '<store>_messages' group get the feed."""
    new_msg = res.get("delivered_msg_sent") == "Yes" and before.get("delivered_msg_sent") != "Yes"
    new_rev = res.get("review_requested") == "Yes" and before.get("review_requested") != "Yes"
    if not (new_msg or new_rev) or DRY:
        return
    try:
        from utils.whatsapp import get_whatsapp_config, DEFAULT_GROUPS, send_whatsapp_message
        key = f"{str(account).strip().lower()}_messages"
        to = (get_whatsapp_config().get("groups") or {}).get(key) or DEFAULT_GROUPS.get(key)
        if not to:
            return
        o = dict(before, **{k: v for k, v in res.items() if v not in (None, "")})
        d = lambda k: str(o.get(k) or "").split("|")[0].strip() or "—"
        yn = lambda v: "Yes" if str(v or "").strip().lower() == "yes" else "No"
        # Amazon refuses some buyers ('No Permission' — opted out of messages): say so, not "No"
        msg_state = lambda v: "No Permission" if str(v or "").strip().lower() == "no permission" else yn(v)
        what = " + ".join(x for x, f in (("Delivery message", new_msg), ("Review request", new_rev)) if f)
        text = (f"✅ *{account} — {what} sent*\n"
                f"Order: {d('amazon_order_id')}\n"
                f"ASIN: {d('asin')} | SKU: {d('sku')} | Qty: {d('quantity')}\n"
                f"Purchase date: {d('purchase_date')}\n"
                f"Deliver by: {d('deliver_by_date')}\n"
                f"Delivered: {d('delivery_date')}\n"
                f"Return requested: {yn(o.get('return_requested'))}\n"
                f"Refund issued: {yn(o.get('refund_done'))}\n"
                f"Delivery message sent: {msg_state(o.get('delivered_msg_sent'))}\n"
                f"Review request: {yn(o.get('review_requested'))}")
        send_whatsapp_message(text, to=to)
    except Exception as e:
        print(f"[MSG]   WhatsApp alert skipped: {e}")


def _turn(sc_port, driver):
    """One buyer message = one turn on this store's Seller Central Chrome (utils/sc_chrome.py):
    the tracking uploader drives the same Chrome all day, and per-order turns let its uploads run
    between messages instead of waiting out a whole messaging run (late shipments). A dry run has
    no Chrome and takes no turn."""
    from contextlib import nullcontext
    if driver is None:
        return nullcontext()
    from utils.sc_chrome import sc_turn
    return sc_turn(sc_port, f"messaging {ACCOUNT}", max_wait=900)


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
    delayed = load_delayed()
    print(f"[MSG] {ACCOUNT}: {len(pending)} delivered order(s) owed a message/review "
          f"(check-in {DAYS_AFTER_DELIVERY_TO_MESSAGE}–{DELIVERY_MESSAGE_MAX_DAYS} d, review "
          f"{DAYS_AFTER_DELIVERY_TO_REVIEW}–{MAX_DAYS} d after delivery) and {len(delayed)} stalled "
          f"shipment(s) owed a delay notice ({DELAY_NOTICE_STALLED_DAYS}+ days without movement"
          f"{'' if DELAY_NOTICE_ENABLED else ', notices OFF'}), dry={DRY}")
    if not pending and not delayed:
        print("[MSG] ✅ Nothing to send.")
        return 0
    if LIMIT:
        pending = pending[:LIMIT]
    if DELAY_LIMIT:
        delayed = delayed[:DELAY_LIMIT]

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
            print(f"[MSG] ⚠️ No Chrome on port {sc_port} — messages stay pending. "
                  f"Run ▶ Run Closing for {ACCOUNT} first, or press 🚀 Chrome, and re-run.")
            return 2
        # Having a browser is not having a session. A freshly launched headless Chrome lands
        # on sign-in (Amazon refuses the profile cookie once the UA says HeadlessChrome), and
        # every order then reports "no contact reason available" because the contact page is
        # really the login page. Clear login and the account switcher before touching order 1.
        from platforms.auth import ensure_sc_session
        with _turn(sc_port, driver):
            _signed_in = ensure_sc_session(driver, ACCOUNT,
                                           target_url="https://sellercentral.amazon.com/home",
                                           label="MSG")
        if not _signed_in:
            print(f"[MSG] ⚠️ Could not get a signed-in Seller Central for {ACCOUNT} — "
                  f"messages stay pending, nothing was sent.")
            return 2

    gmail_services = _get_gmail_services(gmail_tokens())
    if not gmail_services:
        # Without Gmail we can't run the return/refund + already-sent guards —
        # sending blind risks messaging a buyer who has an open return. Refuse.
        print("[MSG] ❌ No Gmail services could authenticate — refusing to message "
              "without the return/refund guards. Fix the tokens and re-run.")
        return 2

    # One batched Gmail pass answers the guard questions for the whole run (a few OR-queries
    # per inbox instead of ~25 searches per order); orders with mail still get the exact
    # per-order checks inside process_single_order.
    guards = None
    try:
        guards = prefetch_guards(gmail_services, [o["amazon_order_id"] for o in pending + delayed])
    except Exception as e:
        print(f"[MSG] guard prefetch failed ({e}) — per-order Gmail checks will run")

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
            before = dict(o)
            with _turn(sc_port, driver):
                res = process_single_order(driver, o, pd_cfg, gmail_services,
                                           closing_table=True, dry_run=DRY, guards=guards)
            _alert_sent(ACCOUNT, before, res)
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

    # ── Delay notices: one per stalled shipment, same Chrome, same guards ──
    delayed_sent = delayed_skipped = delayed_failed = 0
    for o in delayed:
        if driver is not None and not _is_driver_alive(driver):
            print("[MSG] ⚠️ Chrome died before the delay notices — they stay pending.")
            break
        try:
            with _turn(sc_port, driver):
                res = process_delay_notice(driver, o, pd_cfg, gmail_services,
                                           closing_table=True, dry_run=DRY, guards=guards)
            if res.get("delay_notice_sent") == "Yes":
                delayed_sent += 1
            elif res.get("customer_messaged") == "Yes" or res.get("return_requested") == "Yes" \
                    or res.get("refund_done") == "Yes" or res.get("delay_notice_sent") == "No":
                delayed_skipped += 1
        except Exception as e:
            delayed_failed += 1
            print(f"[MSG]   {o.get('amazon_order_id')}: delay notice failed — {e}")

    print(f"[MSG] ✅ DONE — {sent} delivery messages, {reviewed} review requests, "
          f"{skipped} guarded-skip, {failed} errors (of {len(pending)} pending); "
          f"delay notices: {delayed_sent} sent, {delayed_skipped} guarded-skip, {delayed_failed} errors "
          f"(of {len(delayed)} stalled)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
