#!/usr/bin/env python3
"""
test_place_order_click.py — the Place Order click, against a scripted fake checkout page.

Why this exists: the first click used to miss. Measured on 17 Sep 2026 — headed, 6 of 29
orders needed the retry (21%); headless with attributed checkout, 4 of 4 did, and one order
ran out of attempts and was locked FAILED_KEPT. The cause was clicking on a button that was
merely present instead of one that was settled, visible, uncovered and enabled.

These tests drive the real functions in checkout/dom.py with a fake driver and a fake clock,
so they run instantly and cover the cases that actually bit: a page still rendering, an
overlay still up, a button still moving, and — most important for an ordering system — never
clicking a second time when the first click may already have submitted an order.

Run:  python scripts/test_place_order_click.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

import checkout.dom as dom  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


class Clock:
    """time.time()/time.sleep() with no real waiting, so a 25 s timeout costs nothing."""
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += float(s)


class FakeBtn:
    def __init__(self, page):
        self.page = page
        self.text = "Place Order"

    def get_attribute(self, _n):
        return None

    def click(self):
        if self.page.selenium_click_raises:
            raise RuntimeError("element not interactable")
        self.page.register_click("selenium")


class FakePage:
    """A checkout page whose state is a function of the fake clock."""

    def __init__(self, clock, **kw):
        self.clock = clock
        self.t0 = clock.time()
        self.current_url = "https://www.zoro.com/checkout"
        self.body_text = "Order Summary"
        self.clicks = []                      # (how, at)
        self.cdp_events = []
        self.btn = FakeBtn(self)

        self.appears_after = kw.get("appears_after", 0.0)
        self.ready_after = kw.get("ready_after", 0.0)
        self.scrim_until = kw.get("scrim_until", 0.0)
        self.covered_until = kw.get("covered_until", 0.0)
        self.moving_until = kw.get("moving_until", 0.0)
        self.disabled_until = kw.get("disabled_until", 0.0)
        self.cdp_raises = kw.get("cdp_raises", False)
        self.selenium_click_raises = kw.get("selenium_click_raises", False)
        self.dead_clicks = kw.get("dead_clicks", 0)          # clicks the page ignores entirely
        self.submits_after = kw.get("submits_after", 0.3)    # click -> confirmation delay
        self.order_id = kw.get("order_id", "WB12345678")
        self.order_in_url = kw.get("order_in_url", True)
        self.stuck_loading = kw.get("stuck_loading", False)  # spins forever, never confirms
        self.resets_idle = kw.get("resets_idle", False)      # spins, then goes back to idle
        self.loading = False
        self.loading_at = 0.0

    # -- page behaviour ------------------------------------------------------
    @property
    def age(self):
        return self.clock.time() - self.t0

    def register_click(self, how):
        self.clicks.append((how, self.age))
        if len(self.clicks) <= self.dead_clicks:
            return                                    # swallowed: handler not bound yet
        self.loading, self.loading_at = True, self.clock.time()

    def _settle(self):
        """Advance the page's own state machine to the current clock."""
        if not self.loading:
            return
        if self.stuck_loading:
            return
        if self.clock.time() - self.loading_at < self.submits_after:
            return
        if self.resets_idle:
            self.loading = False
            return
        self.loading = False
        if self.order_in_url:
            self.current_url = f"https://www.zoro.com/checkout/confirmation?orderNo={self.order_id}"
        else:
            self.current_url = "https://www.zoro.com/checkout/confirmation"
            self.body_text = f"Thank you! Your order {self.order_id} is confirmed."

    def state(self):
        self._settle()
        a = self.age
        moving = a < self.moving_until
        return {
            "ready": a >= self.ready_after,
            "w": 160, "h": 44,
            "x": 100 + (int(a * 37) % 50 if moving else 0),
            "y": 400 + (int(a * 53) % 50 if moving else 0),
            "cx": 180.0, "cy": 422.0,
            "inView": True,
            "visible": True,
            "enabled": a >= self.disabled_until and not self.loading,
            "loading": self.loading,
            "scrim": a < self.scrim_until,
            "covered": a < self.covered_until,
        }


class FakeDriver:
    def __init__(self, page):
        self.page = page

    @property
    def current_url(self):
        self.page._settle()
        return self.page.current_url

    def find_elements(self, _by, sel):
        self.page._settle()
        if self.page.age < self.page.appears_after:
            return []
        if "place-order" in sel or "main-action-top" in sel:
            return [self.page.btn]
        return []

    def execute_script(self, js, *args):
        self.page._settle()
        if js is dom._PLACE_BTN_STATE_JS:
            el = args[0] if args else None
            if el is None:
                return {"gone": True}
            return self.page.state()
        if "innerText" in js:
            return self.page.body_text
        if "scrollIntoView" in js:
            return None
        if "dispatchEvent" in js:
            self.page.register_click("js")
            return None
        return None

    def execute_cdp_cmd(self, cmd, params):
        self.page._settle()
        if self.page.cdp_raises:
            raise RuntimeError("cdp unavailable")
        if cmd == "Input.dispatchMouseEvent":
            self.page.cdp_events.append(params)
            if params.get("type") == "mouseReleased":
                self.page.register_click("cdp")
        return {}


def run(**kw):
    """One order attempt against a page described by kw. Returns (result, page)."""
    clock = Clock()
    page = FakePage(clock, **kw)
    real_time = dom.time
    dom.time = clock
    try:
        return dom._click_place_order(FakeDriver(page)), page
    finally:
        dom.time = real_time


def main():
    print("\n── the click lands first time ──")
    wb, p = run()
    check("happy path returns the WB id", wb == "WB12345678", str(wb))
    check("it clicked exactly once", len(p.clicks) == 1, str(p.clicks))
    check("the click went through CDP, not element.click()", p.clicks[0][0] == "cdp", str(p.clicks))
    check("CDP sent a real press and release at the button's centre",
          [e["type"] for e in p.cdp_events] == ["mouseMoved", "mousePressed", "mouseReleased"]
          and all(e["x"] == 180.0 and e["y"] == 422.0 for e in p.cdp_events), str(p.cdp_events))

    print("\n── it waits for the page instead of clicking early ──")
    for label, kw, until in (
            ("the document to finish loading", {"ready_after": 3.0}, 3.0),
            ("the button to exist", {"appears_after": 4.0}, 4.0),
            ("the loading overlay to clear", {"scrim_until": 3.0}, 3.0),
            ("whatever is drawn over it to go away", {"covered_until": 3.0}, 3.0),
            ("the button to stop moving", {"moving_until": 5.0}, 5.0),
            ("the button to become enabled", {"disabled_until": 3.0}, 3.0)):
        wb, p = run(**kw)
        check(f"waits for {label}",
              wb == "WB12345678" and len(p.clicks) == 1 and p.clicks[0][1] >= until,
              f"clicked at {p.clicks[0][1] if p.clicks else None}, needed >= {until}")

    print("\n── delivery fallbacks ──")
    wb, p = run(cdp_raises=True)
    check("no CDP: falls back to Selenium's click and still succeeds",
          wb == "WB12345678" and p.clicks[0][0] == "selenium", str(p.clicks))
    wb, p = run(cdp_raises=True, selenium_click_raises=True)
    check("no CDP and an uninteractable element: falls back to a dispatched MouseEvent",
          wb == "WB12345678" and p.clicks[0][0] == "js", str(p.clicks))

    print("\n── a click that does nothing is retried ──")
    wb, p = run(dead_clicks=1)
    check("a swallowed click is detected and retried", wb == "WB12345678" and len(p.clicks) == 2, str(p.clicks))
    wb, p = run(dead_clicks=2)
    check("two swallowed clicks still get there on the third", wb == "WB12345678" and len(p.clicks) == 3, str(p.clicks))
    wb, p = run(dead_clicks=99)
    check("a button that never responds gives up after 3 clicks, no order id",
          wb is None and len(p.clicks) == 3, str((wb, len(p.clicks))))

    print("\n── never place the same order twice ──")
    wb, p = run(stuck_loading=True)
    check("a submission still in flight is NOT clicked again",
          wb is None and len(p.clicks) == 1, f"clicked {len(p.clicks)}x")
    wb, p = run(resets_idle=True)
    check("a submission the page abandoned (button back to idle) is retried",
          len(p.clicks) == 3 and wb is None, str((wb, len(p.clicks))))

    print("\n── confirmation ──")
    wb, p = run(order_in_url=False)
    check("reads the WB id from the page when the URL has no orderNo", wb == "WB12345678", str(wb))
    wb, p = run(appears_after=10_000)
    check("a button that never appears fails with no click at all",
          wb is None and not p.clicks, str((wb, p.clicks)))

    print("\n── a button that never settles is still clicked (no regression) ──")
    wb, p = run(covered_until=10_000)
    check("permanently covered: clicks anyway through the element, not CDP coordinates",
          wb == "WB12345678" and p.clicks and p.clicks[0][0] in ("selenium", "js")
          and not p.cdp_events, str((wb, p.clicks, len(p.cdp_events))))
    wb, p = run(moving_until=10_000)
    check("a button that never stops moving is still clicked",
          wb == "WB12345678" and len(p.clicks) == 1, str((wb, p.clicks)))
    wb, p = run(disabled_until=10_000)
    check("a button that stays disabled is NOT clicked", wb is None and not p.clicks, str((wb, p.clicks)))

    print("\n── failure reasons are specific ──")
    reasons = {
        "page still loading": {"ready": False},
        "button disabled": {"ready": True, "visible": True, "enabled": False},
        "still spinning": {"ready": True, "visible": True, "enabled": True, "loading": True},
        "an overlay is covering the page": {"ready": True, "visible": True, "enabled": True, "scrim": True},
        "something is drawn over the button": {"ready": True, "visible": True, "enabled": True, "covered": True},
        "button left the page": {"gone": True},
        "button not found": None,
    }
    for want, state in reasons.items():
        check(f"says '{want}' when that is what it was waiting on",
              dom._why_not_clickable(state) == want, dom._why_not_clickable(state))

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
