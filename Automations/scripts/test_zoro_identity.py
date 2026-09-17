#!/usr/bin/env python3
"""
test_zoro_identity.py — which user agent the placer presents, and to whom.

Zoro approved this reseller account and asked (17 Sep 2026) about unattributed activity, so
the placer identifies itself to them by name. Amazon is a separate relationship and must keep
seeing exactly the browser it has always seen from these profiles — so the identity is chosen
per navigation, at the one choke point every request goes through.

The last group runs a real headless Chrome to prove the override actually reaches the page;
it is skipped automatically if Chrome will not start.

Run:  python scripts/test_zoro_identity.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

from utils import helpers as H  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


class FakeDriver:
    """Records the UA in force at each navigation, the way a server would see it."""

    STOCK = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) HeadlessChrome/153.0.0.0 Safari/537.36")

    def __init__(self):
        self.ua = None
        self.trail = []          # (url, ua in force)
        self.navigations = 0

    def execute_cdp_cmd(self, cmd, params):
        if cmd == "Network.setUserAgentOverride":
            self.ua = params.get("userAgent")
        if cmd == "Browser.getVersion":
            return {"userAgent": self.STOCK}
        return {}

    def get(self, url, *a, **kw):
        self.navigations += 1
        self.trail.append((url, self.ua))
        return "navigated"


def fresh():
    d = FakeDriver()
    H.bind_identity_to_navigation(d)
    return d


def main():
    print("\n── who gets told what ──")
    d = fresh()
    d.get("https://www.zoro.com/checkout")
    check("Zoro is told the reseller account by name",
          d.trail[-1][1] == H.RESELLER_USER_AGENT, str(d.trail[-1][1]))
    check("the reseller UA carries the company name", "RajarEnterpriseLLC" in H.RESELLER_USER_AGENT)
    check("and does not announce a headless browser", "Headless" not in H.RESELLER_USER_AGENT)

    d.get("https://sellercentral.amazon.com/orders-v3/search?q=B01")
    check("Amazon keeps the browser's own UA, unchanged",
          d.trail[-1][1] == FakeDriver.STOCK, str(d.trail[-1][1]))
    check("nothing was scrubbed from what Amazon sees",
          "HeadlessChrome" in (d.trail[-1][1] or ""), str(d.trail[-1][1]))

    d.get("https://zoro.com/cart")
    check("the bare zoro.com domain counts as Zoro", d.trail[-1][1] == H.RESELLER_USER_AGENT)
    d.get("https://www.amazon.com/dp/B01")
    check("switching back and forth keeps working", d.trail[-1][1] == FakeDriver.STOCK)

    print("\n── a lookalike host is not Zoro ──")
    for bad in ("https://zoro.com.example.net/checkout", "https://notzoro.com/",
                "https://evil-zoro.com/", "https://zoro.com.attacker.io/cart"):
        d.get(bad)
        check(f"{bad.split('//')[1].split('/')[0]} does NOT get the reseller UA",
              d.trail[-1][1] == FakeDriver.STOCK, str(d.trail[-1][1]))
    d.get("https://checkout.zoro.com/x")
    check("a real zoro.com subdomain does", d.trail[-1][1] == H.RESELLER_USER_AGENT)

    print("\n── plumbing ──")
    d = fresh()
    check("navigation still reaches the real driver.get", d.get("https://www.zoro.com") == "navigated")
    before = d.get
    H.bind_identity_to_navigation(d)
    check("binding twice does not wrap twice", d.get is before)
    # Not every navigation is a web request. chrome://settings parses with a hostname of
    # "settings", which used to read as "not Zoro" and flip the UA on the way to clearing the
    # cache; about:blank, file:// and data: pages send nothing to anyone either.
    for internal in ("chrome://settings/clearBrowserData", "about:blank",
                     "file:///tmp/x.html", "data:text/html,<p>hi"):
        d.get(internal)
        check(f"{internal.split(':')[0]}: leaves the identity alone",
              d.trail[-1][1] == H.RESELLER_USER_AGENT, str(d.trail[-1][1]))
    d.get("https://sellercentral.amazon.com/orders-v3/search")
    d.get("chrome://settings/clearBrowserData")
    check("and leaves it alone the other way round too, on the Amazon UA",
          d.trail[-1][1] == FakeDriver.STOCK, str(d.trail[-1][1]))

    print("\n── it is configurable ──")
    os.environ["ZUHRA_ZORO_UA"] = "Mozilla/5.0 (Custom/1.0)"
    check("env ZUHRA_ZORO_UA wins", H.zoro_user_agent() == "Mozilla/5.0 (Custom/1.0)")
    os.environ["ZUHRA_ZORO_UA"] = ""
    check("an empty env value means send no override at all", H.zoro_user_agent() == "")
    d = fresh()
    d.get("https://www.zoro.com/checkout")
    check("with the override off, Zoro sees the browser's own UA too",
          d.trail[-1][1] is None, str(d.trail[-1][1]))
    del os.environ["ZUHRA_ZORO_UA"]
    check("with nothing set, the reseller UA is the default",
          H.zoro_user_agent() == H.RESELLER_USER_AGENT)

    print("\n── real Chrome ──")
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        import tempfile
        o = Options()
        o.add_argument("--headless=new")
        o.add_argument("--user-data-dir=" + tempfile.mkdtemp())
        drv = webdriver.Chrome(options=o)
    except Exception as e:
        print(f"  ⏭️  skipped — Chrome would not start ({str(e)[:60]})")
        drv = None
    if drv is not None:
        try:
            page = "file://" + os.path.join(HERE, "..", "data", "_ua_probe.html")
            stock_real = H.browser_user_agent(drv)
            check("a headless Chrome really does announce itself as HeadlessChrome",
                  "HeadlessChrome" in stock_real, stock_real)
            H.apply_zoro_identity(drv)
            drv.get("about:blank")
            seen = drv.execute_script("return navigator.userAgent")
            check("the reseller UA actually reaches the page", seen == H.RESELLER_USER_AGENT, seen)
            check("so the page no longer says HeadlessChrome", "Headless" not in seen, seen)
            H.apply_stock_identity(drv)
            drv.get("about:blank")
            seen = drv.execute_script("return navigator.userAgent")
            check("and the stock UA can be put back verbatim", seen == stock_real, seen)
        finally:
            drv.quit()

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
