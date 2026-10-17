"""
Multi-tab Seller-Central field scraper — the EXCEPTION path since 26 Sep 2026.

It used to queue every order missing deliver_by_date OR tracking_id_uploaded (3,258 HOANK +
1,443 Hathoro pages ≈ 3 hours on 26 Sep 2026), although the placer already captures the
dates at placement (100 % coverage) and the tracker now records what it uploaded
(order_placer_orders.tracking_id_uploaded + action_log). The queue is therefore: Shipped
orders with NO upload record anywhere, never scraped or scraped more than SCRAPE_RETRY_DAYS
ago, newest first, at most SCRAPE_LIMIT per run. Feedback is still read off the page while
we are there (it exists on ~0.04 % of orders — not worth a pass of its own).

Since 17 Oct 2026 the queue also takes Shipped orders with NO deliver_by_date, of ANY month
(the placer only captures it since 7 Sep 2026 and nothing else re-reads it, so ~9,000 older
orders stayed empty in the coverage report): after the upload-record gaps, newest first, at
most SCRAPE_DELIVER_BY_LIMIT per run (default 200; 0 = off). A page that loaded but showed no
deliver-by is re-read at most every 30 days, never every run.

WHY THIS DESIGN
---------------
Headless does NOT work for SC: the session lives only inside a live, logged-in,
HEADED Chrome process — a headless/restarted launch is bounced to /ap/signin
(proven 2026-06-25). So this tool CONNECTS (debuggerAddress) to a Chrome you
launched and logged into, and never restarts it.

Parallelism is done with MULTIPLE TABS inside that ONE logged-in Chrome (tabs
share the session) — so 1 login per account gives ~N-way concurrency. The N page
loads overlap in the browser while the single driver only does fast switch+poll.

SAFETY (the logged-out / captcha detector)
------------------------------------------
Every page is classified. If ANY tab is bounced to signin or shows a captcha, the
WHOLE run STOPS immediately with a loud banner — it never writes empty rows over
20k orders. It's resumable: scraped orders are marked enriched_from_scrape=1, so
after you re-login you just run it again and it continues where it stopped.

USAGE
-----
1. Launch a headed Chrome for the account and LOG IN to Seller Central, e.g.:
     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
        --remote-debugging-port=9331 \
        --user-data-dir="$HOME/.closing_chrome_profiles/port_9331" \
        --no-first-run --no-default-browser-check \
        --disable-blink-features=AutomationControlled \
        https://sellercentral.amazon.com/orders-v3
   (HOANK -> port 9331 profile, Hathoro -> port 9333 profile.)
2. Run this tool against that account:
     SCRAPE_ACCOUNT=HOANK ../.venv/bin/python scripts/scrape_sc_fields.py
   Env overrides: SCRAPE_ACCOUNT, SCRAPE_PORT, SCRAPE_TABS (default 4),
   SCRAPE_LIMIT (0=all), SCRAPE_MONTHS ('2026-05,2026-06' or 'all'), SCRAPE_DRYRUN=1,
   SCRAPE_DELIVER_BY_LIMIT (deliver-by backfill per run, default 200, 0 = off).
"""
import os, sys, time, sqlite3

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _DIR)
print = __import__("functools").partial(print, flush=True)

# ── config ──────────────────────────────────────────────────────────────────
ACCOUNT  = os.environ.get("SCRAPE_ACCOUNT", "HOANK").strip()
PORTS    = {"HOANK": 9331, "Hathoro": 9333}
PORT     = int(os.environ.get("SCRAPE_PORT") or PORTS.get(ACCOUNT, 9331))
TABS     = int(os.environ.get("SCRAPE_TABS") or 4)
LIMIT    = int(os.environ.get("SCRAPE_LIMIT") or 400)      # per run; 0 = unbounded
RETRY_DAYS = int(os.environ.get("SCRAPE_RETRY_DAYS") or 7)  # re-read a page that showed no shipment yet
# default = previous+current month, computed — a hardcoded default here silently
# excluded every new month's orders from the scrape.
from utils.accounts_config import default_months
MONTHS   = [m.strip() for m in os.environ.get("SCRAPE_MONTHS", default_months()).split(",") if m.strip()]
ALL_MONTHS = [m.lower() for m in MONTHS] == ["all"]
DELIVER_BY_LIMIT = int(os.environ.get("SCRAPE_DELIVER_BY_LIMIT") or 200)
DRYRUN   = os.environ.get("SCRAPE_DRYRUN", "0").strip() in ("1", "true", "yes", "on")
PAGE_WAIT = float(os.environ.get("SCRAPE_PAGE_WAIT") or 14)   # max seconds per round
RECYCLE_EVERY = int(os.environ.get("SCRAPE_RECYCLE_EVERY") or 150)  # flush tab memory every N orders
MULTI_TAB = TABS > 1   # single-tab mode never switches windows → Chrome won't steal focus on macOS
DB_PATH  = os.path.join(_DIR, "data", "automations_unified.db")
URL      = "https://sellercentral.amazon.com/orders-v3/order/{}"

CAPTCHA_MARKERS = ("enter the characters", "type the characters you see", "robot check",
                   "solve this puzzle", "automated access", "are you a human")

import datetime as _dt
_TZ_TOKENS = (" PDT", " PST", " EDT", " EST", " CDT", " CST", " MDT", " MST", " UTC", " GMT")
def norm_date(s):
    """'Sat, Jun 27, 2026 PDT' -> '2026-06-27' (so on_time/dashboards can parse)."""
    if not s:
        return s
    t = str(s).strip()
    for tz in _TZ_TOKENS:
        if t.endswith(tz):
            t = t[: -len(tz)].strip()
    for fmt in ("%a, %b %d, %Y", "%b %d, %Y", "%A, %B %d, %Y", "%B %d, %Y",
                "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return _dt.datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return str(s).strip()
# columns we gap-fill (never overwrite an existing good value)
TEXT_COLS = ["deliver_by_date", "ship_by_date", "tracking_id_uploaded",
             "tracking_carrier_uploaded", "tracking_upload_date",
             "ship_service_used", "ship_from",
             "feedback_text", "feedback_date"]
# columns this tool may need to create (idempotent)
NEW_COLS = {"ship_service_used": "TEXT", "ship_from": "TEXT"}


def banner(msg):
    line = "═" * 64
    print(f"\n{line}\n  {msg}\n{line}\n")


# ── detector ────────────────────────────────────────────────────────────────
def classify(driver, expect_aid=None):
    """LOGGED_OUT / CAPTCHA / WRONG_PAGE / SKELETON / OK — the safety gate.

    expect_aid is the order id this tab is SUPPOSED to be showing. If the tab's
    URL doesn't contain it, we return WRONG_PAGE and refuse to read it — this is
    what guarantees one order's page can never be saved under another's id.
    """
    url = (driver.current_url or "").lower()
    if "/ap/signin" in url or "/ap/" in url or "signin" in url:
        return "LOGGED_OUT", ""
    try:
        body = driver.find_element("tag name", "body").text
    except Exception:
        body = ""
    bl = body.lower()
    if any(s in bl for s in CAPTCHA_MARKERS):
        return "CAPTCHA", body
    if expect_aid and expect_aid.lower() not in url:
        return "WRONG_PAGE", body          # tab not on the expected order → never read it
    if len(body) < 500 or ("new seller central" in bl and "order details" not in bl):
        return "SKELETON", body
    return "OK", body


# ── extraction (reuses the proven AmazonScraper + closing_signals extractors) ──
def _val_after(lines, label, skip=("image", "more details", "")):
    """In the SC shipment block, labels and values sit on consecutive lines
    ('Shipping service' \\n 'FedEx Ground'). Return the first real value line
    after an EXACT label line (the colon variants like 'Shipping service: Standard'
    are order-level and won't exact-match, so we get the carrier service)."""
    lab = label.strip().lower()
    for i, l in enumerate(lines):
        if l.strip().lower() == lab:
            for j in range(i + 1, min(i + 4, len(lines))):
                v = lines[j].strip()
                if v.lower() not in skip:
                    return v
    return ""


def parse_loaded(scraper, driver, body):
    r = {}
    lines = body.splitlines()
    r["deliver_by_date"] = norm_date(scraper._extract_date(body, "Deliver by"))
    r["ship_by_date"]    = norm_date(scraper._extract_date(body, "Ship by"))
    tu = scraper._extract_tracking_uploaded(body)
    r["tracking_id_uploaded"]      = tu.get("tracking_id_uploaded", "")
    r["tracking_carrier_uploaded"] = tu.get("tracking_carrier_uploaded", "")
    r["tracking_upload_date"]      = norm_date(tu.get("tracking_upload_date", ""))  # = actual ship date
    # shipment-block fields (carrier service + origin warehouse)
    r["ship_service_used"] = _val_after(lines, "Shipping service")
    r["ship_from"]         = _val_after(lines, "Ship from")
    fb = scraper._extract_feedback(body)
    r["feedback_rating"] = fb.get("rating")
    r["feedback_text"]   = fb.get("text", "")
    r["feedback_date"]   = fb.get("date", "")
    try:
        from engine.closing_signals import extract_feedback as _ef
        f2 = _ef(driver.page_source)
        if f2.get("feedback_rating") is not None:
            r["feedback_rating"] = f2["feedback_rating"]
        if f2.get("feedback_text"):
            r["feedback_text"] = f2["feedback_text"]
        if f2.get("feedback_date"):
            r["feedback_date"] = f2["feedback_date"]
    except Exception:
        pass
    return r


def _has(v):
    return v not in (None, "") and str(v).strip().lower() not in ("", "none")


def persist(conn, aid, r):
    """Non-destructive gap-fill: only writes a column that is currently empty.
    enriched_from_scrape=1 is stamped only when the page actually showed a confirmed shipment;
    a page read too early (Amazon's report says Shipped before the order page does) keeps
    scraped_at only, so the RETRY_DAYS rule re-reads it instead of trapping it — 6,061 Shipped
    orders were stuck that way on 26 Sep 2026."""
    got_upload = _has(r.get("tracking_id_uploaded"))
    sets, vals = (["enriched_from_scrape=1", "scraped_at=?"] if got_upload else ["scraped_at=?"]), \
        [time.strftime("%Y-%m-%d %H:%M:%S")]
    for col in TEXT_COLS:
        v = r.get(col)
        if _has(v):
            sets.append(f"{col}=CASE WHEN {col} IS NULL OR TRIM(CAST({col} AS TEXT)) IN ('','None') THEN ? ELSE {col} END")
            vals.append(str(v).strip())
    if r.get("feedback_rating") is not None:
        sets.append("feedback_rating=CASE WHEN feedback_rating IS NULL OR feedback_rating=0 THEN ? ELSE feedback_rating END")
        vals.append(int(r["feedback_rating"]))
    vals.append(aid)
    conn.execute(f"UPDATE closing_orders SET {', '.join(sets)} WHERE amazon_order_id=?", vals)


class BrowserGone(Exception):
    """Raised when the Chrome window/process is gone and can't be driven."""


def open_tabs(driver, n):
    """Return n usable tab handles, opening more if some have been closed.
    Raises BrowserGone if the browser itself is unreachable."""
    from selenium.common.exceptions import WebDriverException
    try:
        handles = driver.window_handles
        while len(handles) < n:
            driver.switch_to.new_window("tab")
            handles = driver.window_handles
        return handles[:n]
    except WebDriverException:
        raise BrowserGone


def recycle_tabs(driver, n):
    """Close worker tabs and open n fresh ones — flushes the SC SPA's accumulated
    memory so a long run doesn't crash the renderer. Raises BrowserGone if dead."""
    from selenium.common.exceptions import WebDriverException
    try:
        if not MULTI_TAB:
            driver.get("about:blank")      # single tab: just flush it, no window switching
            return driver.window_handles[:1]
        handles = driver.window_handles
        driver.switch_to.window(handles[0])
        for h in handles[1:]:
            driver.switch_to.window(h)
            driver.close()
        driver.switch_to.window(driver.window_handles[0])
        driver.get("about:blank")
        return open_tabs(driver, n)
    except WebDriverException:
        raise BrowserGone


def _months_sql():
    """(SQL condition, args) for the scrape months; SCRAPE_MONTHS=all → every month."""
    if ALL_MONTHS:
        return "1=1", []
    return f"substr(purchase_date,1,7) IN ({','.join('?' * len(MONTHS))})", list(MONTHS)


def count_total(conn):
    msql, margs = _months_sql()
    return conn.execute(
        f"SELECT COUNT(*) FROM closing_orders WHERE account=? AND {msql}",
        [ACCOUNT, *margs]).fetchone()[0]


def build_queue(conn):
    """Shipped orders of the scrape months with no upload record in closing_orders, the placer
    table or the tracker's action_log, not scraped in the last RETRY_DAYS, newest first —
    then Shipped orders of any month with no deliver_by_date (see the module docstring)."""
    msql, margs = _months_sql()
    q = (f"SELECT amazon_order_id FROM closing_orders "
         f"WHERE account=? AND {msql} "
         f"AND order_status='Shipped' "
         f"AND COALESCE(enriched_from_scrape,0)=0 "
         f"AND ( tracking_id_uploaded IS NULL OR TRIM(CAST(tracking_id_uploaded AS TEXT)) IN ('','None') ) "
         f"AND ( scraped_at IS NULL OR scraped_at='' OR scraped_at < datetime('now', 'localtime', ?) ) "
         f"AND amazon_order_id NOT IN (SELECT amazon_order_id FROM order_placer_orders "
         f"      WHERE tracking_id_uploaded IS NOT NULL AND TRIM(CAST(tracking_id_uploaded AS TEXT)) NOT IN ('','None')) "
         f"AND amazon_order_id NOT IN (SELECT amazon_order_id FROM action_log "
         f"      WHERE action_type='tracking_upload' AND status IN ('success','already_shipped')) "
         f"ORDER BY purchase_date DESC")
    try:
        rows = [r[0] for r in conn.execute(q, [ACCOUNT, *margs, f"-{RETRY_DAYS} days"]).fetchall()]
    except sqlite3.OperationalError as e:
        # an older schema without the placer upload columns: fall back to the closing row only
        print(f"[queue] fallback ({e})")
        q2 = (f"SELECT amazon_order_id FROM closing_orders WHERE account=? AND {msql} "
              f"AND order_status='Shipped' AND COALESCE(enriched_from_scrape,0)=0 "
              f"AND ( tracking_id_uploaded IS NULL OR TRIM(CAST(tracking_id_uploaded AS TEXT)) IN ('','None') ) "
              f"ORDER BY purchase_date DESC")
        rows = [r[0] for r in conn.execute(q2, [ACCOUNT, *margs]).fetchall()]
    if LIMIT:
        rows = rows[:LIMIT]
    if DELIVER_BY_LIMIT:
        have = set(rows)
        dq = ("SELECT amazon_order_id FROM closing_orders "
              "WHERE account=? AND order_status='Shipped' "
              "AND ( deliver_by_date IS NULL OR TRIM(CAST(deliver_by_date AS TEXT)) IN ('','None') ) "
              "AND ( scraped_at IS NULL OR scraped_at='' OR scraped_at < datetime('now', 'localtime', '-30 days') ) "
              "ORDER BY purchase_date DESC")
        extra = [r[0] for r in conn.execute(dq, [ACCOUNT]).fetchall() if r[0] not in have]
        rows += extra[:DELIVER_BY_LIMIT]
        if extra:
            print(f"[queue] + {min(len(extra), DELIVER_BY_LIMIT)} of {len(extra)} orders with no deliver-by date")
    return rows


def main():
    from selenium.common.exceptions import WebDriverException
    from engine.amazon_scraper import _connect_chrome, AmazonScraper

    banner(f"SC field scraper | {ACCOUNT} | port {PORT} | {TABS} tabs{'  [DRY-RUN]' if DRYRUN else ''}")
    driver = _connect_chrome(PORT)
    if driver is None:
        banner(f"❌ No Chrome on port {PORT}. Launch it + LOG IN to Seller Central first, then re-run.")
        sys.exit(2)
    scraper = AmazonScraper(driver=driver)

    # create any new columns this tool needs (idempotent)
    wconn = sqlite3.connect(DB_PATH, timeout=60)
    wconn.execute("PRAGMA busy_timeout=60000")
    existing = {r[1] for r in wconn.execute("PRAGMA table_info(closing_orders)")}
    for col, typ in NEW_COLS.items():
        if col not in existing:
            wconn.execute(f"ALTER TABLE closing_orders ADD COLUMN {col} {typ}")
    wconn.commit(); wconn.close()

    rconn = sqlite3.connect(DB_PATH, timeout=60)
    rconn.execute("PRAGMA query_only=ON")
    total_db = count_total(rconn)
    queue = build_queue(rconn)
    rconn.close()
    total = len(queue)

    # ── startup summary ──
    print(f"Total orders in DB ({ACCOUNT}, {'all months' if ALL_MONTHS else '+'.join(MONTHS)}) : {total_db}")
    print(f"Shipped orders with no upload record        : {total}" + (f"  (capped at {LIMIT} this run)" if LIMIT and total >= LIMIT else ""))
    if not total:
        print("Nothing to scrape — every shipped order has an upload record. ✅"); return
    print("Starting...\n")

    # early auth probe (fail fast before opening tabs)
    driver.get(URL.format(queue[0])); time.sleep(4)
    st, _ = classify(driver)
    if st in ("LOGGED_OUT", "CAPTCHA"):
        banner(f"🔒 {st} — Chrome on port {PORT} is NOT logged in. Log in, then re-run (resumable).")
        sys.exit(3)

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")

    done = ok = skipped = 0
    since_recycle = 0
    consecutive_fail = 0
    t_start = time.time()
    base = 0
    try:
        while base < total:
            batch = queue[base:base + TABS]
            try:
                handles = open_tabs(driver, len(batch))            # revalidates/reopens tabs each round
                # 1) fire navigations (non-blocking) so loads overlap
                for h, aid in zip(handles, batch):
                    if MULTI_TAB:
                        driver.switch_to.window(h)
                    driver.execute_script("window.location.href = arguments[0];", URL.format(aid))
                # 2) poll until each tab reaches its order url / signin / timeout
                pending = set(range(len(batch)))
                deadline = time.time() + PAGE_WAIT
                while pending and time.time() < deadline:
                    for i in list(pending):
                        if MULTI_TAB:
                            driver.switch_to.window(handles[i])
                        u = (driver.current_url or "").lower()
                        if batch[i].lower() in u or "signin" in u or "/ap/" in u:
                            try:
                                if driver.execute_script("return document.readyState") == "complete":
                                    pending.discard(i)
                            except Exception:
                                pass
                    if pending:
                        time.sleep(0.4)
                # 3) classify + extract
                updates, abort = [], None
                for i, aid in enumerate(batch):
                    if MULTI_TAB:
                        driver.switch_to.window(handles[i])
                    st, body = classify(driver, aid)
                    if st in ("SKELETON", "WRONG_PAGE"):       # foreground + retry once (Chrome throttles bg tabs)
                        time.sleep(3)
                        st, body = classify(driver, aid)
                    if st in ("LOGGED_OUT", "CAPTCHA"):
                        abort = st; break
                    done += 1
                    if st != "OK":
                        skipped += 1
                        print(f"{done}/{total}  {aid} - skipped (will retry)")
                        continue
                    r = parse_loaded(scraper, driver, body)
                    if any(_has(r.get(k)) for k in ("deliver_by_date", "tracking_id_uploaded")):
                        ok += 1
                    updates.append((aid, r))
                    print(f"{done}/{total}  {aid} - scraped successfully")
                # 4) persist this round
                if updates and not DRYRUN:
                    for a, rr in updates:
                        persist(conn, a, rr)
                    conn.commit()
                if abort:
                    banner(f"🔒 {abort} after {done} orders — session dropped. Progress SAVED. "
                           f"Log in again on port {PORT} and re-run the SAME command to resume.")
                    return
                consecutive_fail = 0
                base += TABS
                since_recycle += len(batch)
                if since_recycle >= RECYCLE_EVERY:               # flush SPA memory → avoids renderer crashes
                    recycle_tabs(driver, TABS)
                    since_recycle = 0
                    print(f"... {done}/{total} done | {ok} with data | "
                          f"{done/max(time.time()-t_start,1):.2f}/s | "
                          f"ETA ~{(total-done)/max(done/max(time.time()-t_start,1),0.01)/60:.0f} min")
            except BrowserGone:
                if not DRYRUN:
                    try: conn.commit()
                    except Exception: pass
                banner(f"💥 Chrome on port {PORT} closed/crashed after {done} orders. Progress SAVED. "
                       f"Relaunch the logged-in window and re-run the SAME command — it resumes.")
                return
            except WebDriverException as e:
                consecutive_fail += 1
                print(f"[warn] browser hiccup near order {base} ({type(e).__name__}) — skipping this batch, will retry next run")
                base += TABS
                if consecutive_fail >= 6:
                    banner(f"💥 Too many browser errors after {done} orders. Progress SAVED. Relaunch + re-run (resumable).")
                    return
    finally:
        conn.close()
    banner(f"DONE {ACCOUNT}: {done}/{total} processed | {ok} got data | {skipped} skipped (retry next run).")


def main_in_turn():
    """main() while holding this store's Seller Central Chrome (utils/sc_chrome.py) — the
    tracking uploader drives the same Chrome all day (28 Sep 2026)."""
    from utils.sc_chrome import sc_turn
    with sc_turn(PORT, f"SC scrape {ACCOUNT}", max_wait=1200):
        main()


if __name__ == "__main__":
    main_in_turn()
