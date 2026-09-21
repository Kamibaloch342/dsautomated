#!/usr/bin/env python3
"""
test_cart_clear.py — a placer must never stand still because Zoro refuses a cart removal.

21 Sep 2026, DM_RESELLER: Zoro answered DELETE /v1/cart/<id>/items/G121868920 with HTTP 500, so
the item stayed in the cart, so _clear_cart_api fell through to clear_cart_selenium — which was

    while True:
        btns = driver.find_elements(...)
        if not btns: break
        click(btns[0]); sleep(2)

The Remove button goes to the same failing backend, so the button never disappeared. The placer
clicked it for thirteen minutes, printed nothing after the first click, placed no orders, and
left its claim to be reaped as "orphaned mid-placement". It had already done the same earlier
that evening.

Run:  python scripts/test_cart_clear.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


class FakeDriver:
    """A Zoro cart page. `removes_work` False = the backend refuses every removal."""

    def __init__(self, items, removes_work=True):
        self.items = items
        self.removes_work = removes_work
        self.clicks = 0
        self.gets = []

    def get(self, url):
        self.gets.append(url)

    def find_elements(self, by, sel):
        return [f"btn{i}" for i in range(self.items)]

    def execute_script(self, script, *args):
        self.clicks += 1
        if self.removes_work and self.items:
            self.items -= 1


def main():
    import platforms.zoro as Z
    Z.time.sleep = lambda s: None          # the real waits are what made this slow, not the logic

    print("\n── the normal case still works ──")
    d = FakeDriver(3)
    t = time.time()
    ok = Z.clear_cart_selenium(d)
    check("three items are removed and it reports success", ok is True and d.items == 0, f"items={d.items}")
    check("one click per item, no more", d.clicks == 3, f"{d.clicks} clicks")
    check("it went to the cart page", d.gets == ["https://www.zoro.com/cart"], str(d.gets))
    check("an already-empty cart returns at once", Z.clear_cart_selenium(FakeDriver(0)) is True)

    print("\n── Zoro refusing the removal (the 21 Sep hang) ──")
    d = FakeDriver(1, removes_work=False)
    t = time.time()
    ok = Z.clear_cart_selenium(d)
    took = time.time() - t
    check("it gives up instead of looping for ever", ok is False, "returned True")
    check("it stops after a few clicks, not hundreds", d.clicks <= 5, f"{d.clicks} clicks")
    check("and it returns promptly", took < 5, f"{took:.1f}s")

    print("\n── a slow-but-working cart is not mistaken for a stuck one ──")
    class SlowDriver(FakeDriver):
        """The button takes one extra poll to disappear — progress, just not instantly."""
        def execute_script(self, script, *args):
            self.clicks += 1
            if self.clicks % 2 == 0 and self.items:
                self.items -= 1
    d = SlowDriver(2)
    check("two items that each need two clicks still clear", Z.clear_cart_selenium(d) is True, f"items={d.items}")

    print("\n── the deadline is real ──")
    class NeverEmpties(FakeDriver):
        """Count goes up and down without ever reaching zero — never three stalls in a row."""
        def __init__(self):
            super().__init__(2, removes_work=False)
            self.flip = 0
        def find_elements(self, by, sel):
            self.flip += 1
            self.items = 2 if self.flip % 2 else 1
            return [f"btn{i}" for i in range(self.items)]
    real_sleep, d = Z.time.sleep, NeverEmpties()
    Z.time.sleep = lambda s: None
    t = time.time()
    ok = Z.clear_cart_selenium(d, timeout=0.5)
    took = time.time() - t
    Z.time.sleep = real_sleep
    check("a cart that never empties still ends, on the deadline", ok is False and took < 3, f"{took:.1f}s, ok={ok}")

    print("\n── the caller abandons the order rather than spending ──")
    ta = open(os.path.join(AUTO, "checkout", "true_api.py"), encoding="utf-8").read()
    check("a failed clear ends the checkout as FAILED, before payment",
          'if not _clear_cart_api(driver):\n            return _format_checkout_result("FAILED"' in ta)
    zs = open(os.path.join(AUTO, "platforms", "zoro.py"), encoding="utf-8").read()
    check("no unbounded loop is left in the cart clear",
          "while True:" not in zs.split("def clear_cart_selenium")[1].split("\ndef ")[0])

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
