#!/usr/bin/env python3
"""
test_message_box.py — the two waits that made every buyer message fail on 18 Sep.

Both bugs were the same shape: a WebDriverWait that could not actually wait.

  1. The contact-reason radio. clickRadio returns {success:false} when it finds no radio,
     and `wait.until(lambda d: script(...) or {})` treated that dict as a hit because a
     non-empty dict is truthy. The run printed "✔ Radio button selected: [?]" having
     selected nothing, on the first poll, with no retry.

  2. The message box. `document.querySelector('kat-textarea').shadowRoot...` dereferences
     null when the box has not rendered yet, and wait.until only swallows
     NoSuchElementException — a JavascriptException escapes the wait immediately. So it
     never retried either: "Cannot read properties of null (reading 'shadowRoot')".

Together: no reason selected, so no box, so a hard crash on all 1315 HOANK orders.
These tests drive the real predicates against fake pages that render late.

Run:  python scripts/test_message_box.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


def func_src(text, name):
    tree = ast.parse(text)
    lines = text.split("\n")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{name} not found")


class FakePage:
    """A messaging page where the radio and the box appear after N polls."""

    def __init__(self, radio_after=0, box_after=0, radio_ever=True, box_ever=True):
        self.polls_radio = 0
        self.polls_box = 0
        self.radio_after, self.box_after = radio_after, box_after
        self.radio_ever, self.box_ever = radio_ever, box_ever
        self.radio_selected = False

    def click_radio(self):
        self.polls_radio += 1
        if self.radio_ever and self.polls_radio > self.radio_after:
            self.radio_selected = True
            return {"success": True, "chosen": "order_details"}
        return {"success": False}

    def find_box(self):
        """The real page only renders the box once a reason is selected."""
        self.polls_box += 1
        if not self.radio_selected:
            return None
        if self.box_ever and self.polls_box > self.box_after:
            return "<textarea>"
        return None


def until(pred, page, max_polls=20):
    """Stand-in for WebDriverWait.until: poll until truthy, else TimeoutException."""
    from selenium.common.exceptions import TimeoutException
    for _ in range(max_polls):
        v = pred(page)
        if v:
            return v
    raise TimeoutException()


def main():
    src = open(os.path.join(AUTO, "engine", "tracking.py"), encoding="utf-8").read()
    fn = func_src(src, "_click_radio_and_type")

    # ── the predicates, as the real code now defines them ────────────────────
    def radio_pred(page):
        r = page.click_radio()
        return r if isinstance(r, dict) and r.get("success") else False

    def box_pred(page):
        return page.find_box()

    print("\n── the contact-reason radio ──")
    p = FakePage(radio_after=0)
    check("a radio that is there straight away is selected", until(radio_pred, p).get("chosen") == "order_details")
    p = FakePage(radio_after=5)
    r = until(radio_pred, p)
    check("a radio that renders late is waited for, not given up on",
          r.get("success") and p.polls_radio == 6, f"{p.polls_radio} polls")
    p = FakePage(radio_ever=False)
    try:
        until(radio_pred, p)
        check("no radio at all raises rather than reporting a phantom success", False)
    except Exception as e:
        check("no radio at all raises rather than reporting a phantom success",
              type(e).__name__ == "TimeoutException")
    check("the wait now polls on success itself, not on any dict it gets back",
          'r.get("success")' in fn and "wait.until(_pick_radio)" in fn
          and "wait.until(lambda d: d.execute_script(js_select_option" not in fn)
    check("the ✔ is only printed once a selection actually succeeded",
          fn.index("wait.until(_pick_radio)") < fn.index("✔  Radio button selected: [{"))
    check("a missing reason now says so and skips the order",
          "no contact reason available" in fn and "Skipping" in fn)

    print("\n── the message box ──")
    p = FakePage(radio_after=0, box_after=0)
    until(radio_pred, p)
    check("a box that is ready is found", until(box_pred, p) == "<textarea>")
    p = FakePage(radio_after=0, box_after=4)
    until(radio_pred, p)
    check("a box that renders a beat after the radio is waited for",
          until(box_pred, p) == "<textarea>" and p.polls_box == 5, f"{p.polls_box} polls")
    p = FakePage(radio_after=0, box_ever=False)
    until(radio_pred, p)
    try:
        until(box_pred, p)
        check("a box that never appears times out instead of crashing", False)
    except Exception as e:
        check("a box that never appears times out instead of crashing",
              type(e).__name__ == "TimeoutException")

    print("\n── the JS itself never dereferences null ──")
    check("the lookup returns null when the host is missing",
          "if(!t||!t.shadowRoot)return null;" in fn)
    check("the old unguarded one-liner is gone",
          "document.querySelector('kat-textarea').shadowRoot.querySelector" not in fn)
    check("a timeout there is reported as 'box never appeared', not as a typing error",
          "the message box never appeared" in fn)

    print("\n── the safety net still stands ──")
    check("an empty box is still never sent", "the message box is STILL empty" in fn
          and "NOT clicking Send" in fn)
    check("text that does not match what was intended is still never sent",
          "holds different text than intended" in fn)
    check("the three insertion tiers are untouched",
          "CDP insertText" in fn and "execCommand" in fn and "native setter" in fn)

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
