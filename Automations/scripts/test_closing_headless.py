#!/usr/bin/env python3
"""
test_closing_headless.py — the closing chain runs with no windows, end to end.

The chain is: report download → ingest → reconcile → Seller Central scrape → FedEx →
delivery messages → review requests. Only the FIRST step LAUNCHES a Chrome; every step after
it ATTACHES to that same browser on port 9331/9333. So one switch at the launch point carries
the whole chain — and that only holds while the later steps keep attaching rather than
launching their own. The last group below locks that invariant in place, because if a future
edit gives one of them its own Chrome, this switch would silently stop covering it.

Run:  python scripts/test_closing_headless.py
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


def src(rel):
    return open(os.path.join(AUTO, rel), encoding="utf-8").read()


def func_src(text, name):
    tree = ast.parse(text)
    lines = text.split("\n")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{name} not found")


def main():
    for k in ("ZUHRA_HEADLESS", "ZUHRA_HEADLESS_TRACKING", "ZUHRA_HEADLESS_CLOSING"):
        os.environ.pop(k, None)
    from utils.helpers import headless_wanted

    # ── 1. the switch itself ─────────────────────────────────────────────────
    print("\n── closing has its own switch ──")
    os.environ["ZUHRA_HEADLESS_CLOSING"] = "1"
    os.environ["ZUHRA_HEADLESS"] = "0"
    os.environ["ZUHRA_HEADLESS_TRACKING"] = "0"
    check("closing headless can be on while placers and trackers are off",
          headless_wanted("closing") is True
          and headless_wanted("orders") is False
          and headless_wanted("tracking") is False)
    os.environ["ZUHRA_HEADLESS_CLOSING"] = "0"
    os.environ["ZUHRA_HEADLESS"] = "1"
    check("and off while the placers are on",
          headless_wanted("closing") is False and headless_wanted("orders") is True)
    os.environ["ZUHRA_HEADLESS_CLOSING"] = "1"
    check("the launcher's process names map to the closing switch",
          headless_wanted("bulk_hoank") is True and headless_wanted("messages_hathoro") is True,
          "bulk_* and messages_* are what the launcher names these runs")
    check("an unknown component still falls back to the placers' setting",
          headless_wanted("something_else") is True)

    # ── 2. ensure_chrome resolves it ─────────────────────────────────────────
    print("\n── the Chrome the chain launches follows the switch ──")
    import closing
    import engine.amazon_scraper as A
    seen = {}

    def fake_launch(port, profile_dir=None, headless=False):
        seen["headless"] = headless
        return False

    real = (closing._launch_chrome, closing._kill_stale_chrome, A._connect_chrome)
    closing._launch_chrome = fake_launch
    closing._kill_stale_chrome = lambda p: None
    A._connect_chrome = lambda p: None
    try:
        os.environ["ZUHRA_HEADLESS_CLOSING"] = "1"
        closing.ensure_chrome(9331, tries=1)
        check("switch ON  → the closing Chrome is launched headless", seen.get("headless") is True)
        os.environ["ZUHRA_HEADLESS_CLOSING"] = "0"
        seen.clear()
        closing.ensure_chrome(9331, tries=1)
        check("switch OFF → it keeps its window", seen.get("headless") is False)
        seen.clear()
        closing.ensure_chrome(9999, tries=1, headless=True)
        check("an explicit headless=True still wins (the FedEx pass forces it)",
              seen.get("headless") is True)
        seen.clear()
        closing.ensure_chrome(9999, tries=1, headless=False)
        check("an explicit headless=False still wins", seen.get("headless") is False)
    finally:
        closing._launch_chrome, closing._kill_stale_chrome, A._connect_chrome = real

    # ── 3. review requests ───────────────────────────────────────────────────
    print("\n── review requests follow closing, not the placers ──")
    pd = src("engine/post_delivery.py")
    check("post_delivery asks setup_driver for the closing switch",
          pd.count('setup_driver(port, profile_dir, component="closing")') == 2,
          "both the first driver and the restart-after-death path")
    hl = src("utils/helpers.py")
    check("setup_driver takes a component and uses it",
          "def setup_driver(debugging_port, profile_folder_name, component=\"orders\")" in hl
          and "headless = headless_wanted(component)" in hl)
    check("the placers' own call is unchanged (still the orders switch)",
          'setup_driver(port, profile_dir, component="closing")' not in src("engine/orders.py"))

    # ── 4. FedEx ─────────────────────────────────────────────────────────────
    print("\n── FedEx ──")
    cl = src("closing.py")
    check("the FedEx tracker defaults to headless already",
          "def _launch_fedex_chrome(headless: bool = True)" in src("engine/fedex_tracker.py"))
    check("the FedEx API driver defaults to headless already",
          "headless: bool = True," in src("engine/fedex_api.py"))
    check("the FedEx re-check step forces headless",
          "ensure_chrome(port, headless=True)" in src("scripts/fedex_recheck.py"))

    # ── 5. the launcher ──────────────────────────────────────────────────────
    print("\n── the launcher ──")
    lp = src("gui/launcher.py")
    check("there is a 🫥 Headless (closing) checkbox", "Headless (closing)" in lp
          and "_cls_headless_var" in lp)
    check("it writes chrome.headless_closing",
          'cfg.setdefault("chrome", {})["headless_closing"] = on' in lp)
    check("toggling it restarts nothing",
          "_stop" not in lp.split("def _toggle_closing_headless")[1].split("\n    def ")[0])
    launch_chrome = func_src(lp, "_launch_chrome_closing")
    check("the 🚀 Chrome button drops --start-maximized when headless",
          "HEADLESS_FLAGS if hl else [\"--start-maximized\"]" in launch_chrome)
    check("headless still sets a window size (SC hides columns at a small viewport)",
          "--window-size=1920,1080" in lp)
    check("closing subprocesses are given ZUHRA_HEADLESS_CLOSING",
          'env["ZUHRA_HEADLESS_CLOSING"] = "1"' in lp
          and 'name.startswith(("bulk_", "messages_"))' in lp)
    check("the placers' env var is untouched", 'env["ZUHRA_HEADLESS"] = "1"' in lp)

    # ── 6. the invariant the whole design rests on ───────────────────────────
    print("\n── the later steps still ATTACH, so they inherit the switch ──")
    for rel, fn in (("scripts/send_delivery_messages.py", "messaging"),
                    ("scripts/scrape_sc_fields.py", "SC scrape")):
        text = src(rel)
        check(f"{fn} never builds its own Chrome with its own options",
              "webdriver.Chrome(" not in text and "_connect_chrome" in text, rel)
    # Messaging is normally run on its own, long after the closing run that used to leave a
    # browser behind — so it may START one. It must do that through ensure_chrome, which
    # honours the switch and uses the same profile, never with options of its own.
    msg = src("scripts/send_delivery_messages.py")
    check("messaging starts a Chrome only through closing.ensure_chrome",
          "closing.ensure_chrome(sc_port)" in msg and msg.count("import closing") == 2,
          "at startup and on the mid-run re-attach")
    check("it still prefers attaching to one that is already up",
          msg.index("_connect_chrome(sc_port)") < msg.index("closing.ensure_chrome(sc_port)"))
    check("_connect_chrome really is attach-only (no launch fallback)",
          "debuggerAddress" in func_src(src("engine/amazon_scraper.py"), "_connect_chrome")
          and "--headless" not in func_src(src("engine/amazon_scraper.py"), "_connect_chrome"))

    # ── 7. a logged-out profile fails fast and says what to do ───────────────
    print("\n── a headless run that hits the login page ──")
    check("it does not sit waiting for a login nobody can perform",
          "this run is HEADLESS" in cl and "_headless_run" in cl)
    check("it names both fixes: the credentials to check, and logging in by hand",
          "credentials and OTP secret" in cl and "turn OFF 'Headless (closing)'" in cl
          and "session persists" in cl)
    check("the driver records whether ITS OWN window exists, not just the current setting",
          "_closing_headless = headless" in cl)
    check("a headed run still waits as before", "sys.stdin.isatty()" in cl
          and "LOGIN_WAIT_MINS" in cl)

    # ── 8. closing logs itself in, the way the tracker already does ──────────
    print("\n── closing logs in by itself now ──")
    check("closing calls the same auto_login the tracker uses",
          "from platforms.auth import auto_login" in cl and "auto_login(driver, ACCOUNT)" in cl)
    check("it re-navigates to the report page after logging in, as the tracker does",
          "Auto-login OK" in cl)
    check("the manual wait is now only the fallback",
          cl.index("auto_login(driver, ACCOUNT)") < cl.index("login_waited = 0"))
    from platforms.auth import resolve_account_key
    check("closing's ACCOUNT names map to credential keys",
          resolve_account_key("HOANK") == "hoank" and resolve_account_key("Hathoro") == "hathoro")

    print("\n── a click that lands on a loading overlay is retried ──")
    check("the date dropdown and Request Download go through the retry",
          cl.count("_click_retry(driver, dropdown") == 2
          and "_click_retry(driver, wait.until" in cl)
    check("it retries only interception, and re-raises when it never clears",
          "ElementClickInterceptedException" in cl and "raise" in func_src(cl, "_click_retry"))
    check("a normal click still happens immediately (no added delay)",
          "el.click()" in func_src(cl, "_click_retry"))

    # ── 9. no more two-minute stall on an empty port ─────────────────────────
    print("\n── an empty port is recognised instantly, not after 2 minutes ──")
    import socket, time as _t
    from utils.proc import port_open
    t0 = _t.time(); empty = port_open(59999); dt_empty = (_t.time() - t0) * 1000
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    t0 = _t.time(); live = port_open(srv.getsockname()[1]); dt_live = (_t.time() - t0) * 1000
    srv.close()
    check("an empty port answers False in milliseconds",
          empty is False and dt_empty < 500, f"{dt_empty:.0f} ms")
    check("a real listener answers True", live is True, f"{dt_live:.0f} ms")
    check("a nonsense port number is False, not an exception",
          port_open(-1) is False and port_open("abc") is False and port_open(None) is False)
    check("closing only attempts the attach when something is listening",
          "port_open(port)" in func_src(cl, "ensure_chrome")
          and "_connect_chrome(port) if occupied else None" in cl)
    check("messaging does the same before its attach",
          "port_open(sc_port)" in src("scripts/send_delivery_messages.py"))
    check("if the check itself fails, the old attach still happens",
          "occupied = True" in func_src(cl, "ensure_chrome"))

    # ── 10. review requests say WHY they failed ──────────────────────────────
    print("\n── a failed review request explains itself ──")
    pdsrc = func_src(src("engine/post_delivery.py"), "request_amazon_review")
    check("a missing Yes button reports the buttons it did find",
          "no 'Yes' button appeared" in pdsrc and "buttons on the page" in pdsrc)
    check("a click with no confirmation reports what the page said",
          "clicked Yes but saw no confirmation" in pdsrc)
    check("other errors name the exception type instead of a bare message",
          "type(e).__name__" in pdsrc)
    check("the eligibility answers are unchanged",
          all(x in pdsrc for x in ("No Permission", "Already Requested", "Expired")))

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
