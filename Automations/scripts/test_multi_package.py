#!/usr/bin/env python3
"""test_multi_package.py — Amazon's confirm-shipment form takes ≥1 unit per package (20 Oct 2026).

  1 unit, 4 boxes   → [] (single tracking ID; Amazon's rule, nothing to add by hand)
  2 units, 4 boxes  → 2 packages with the first 2 tracking IDs (it used to give up and upload 1)
  4 units, 2 boxes  → 2 packages, qty split [2, 2]
Run:  python scripts/test_multi_package.py   (fake form, no Chrome)"""
import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with redirect_stdout(io.StringIO()):
    import engine.tracking as T

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"\n      {detail}" if detail and not ok else ""))


class El:
    def __init__(self, form, kind):
        self.form, self.kind, self.value = form, kind, ""

    def click(self): pass
    def clear(self): self.value = ""
    def send_keys(self, v): self.value += v


class Form:
    """One package block to start with; 'Add a Shipment' adds another."""
    def __init__(self):
        self.packages = 1
        self.blocks = {k: [El(self, k)] for k in ("qty", "car", "svc", "tid")}

    def find_elements(self, by, sel):
        key = {T.PKG_QTY_SELECT_SELECTOR: "qty", T.PKG_CARRIER_SELECT_SELECTOR: "car",
               T.PKG_SERVICE_SELECT_SELECTOR: "svc", T.TRACKING_ID_FIELD_SELECTOR: "tid"}.get(sel)
        return list(self.blocks[key]) if key else []

    def find_element(self, by, sel):
        return "add-button"

    def execute_script(self, js, *args):
        for k in self.blocks:
            self.blocks[k].append(El(self, k))


class _Sel:
    def __init__(self, el): self.el = el
    @property
    def first_selected_option(self):
        class O: text = "FedEx" if self.el.kind == "car" else "FedEx Ground"
        return O()


class _Wait:
    def __init__(self, d, t): self.d = d
    def until(self, f): return f(self.d)


T.Select = _Sel
T.WebDriverWait = _Wait
T._select_by_value_or_text = lambda el, v: setattr(el, "value", str(v)) or True
T.time.sleep = lambda s: None

ids = ["542544286335", "542544286346", "542544286368", "542544286379"]
with redirect_stdout(io.StringIO()):
    r1 = T.fill_multi_package_tracking(Form(), ids, 1)
check("1 unit, 4 boxes → no split (one tracking ID; Amazon's rule)", r1 == [], r1)

f = Form()
with redirect_stdout(io.StringIO()) as out:
    r2 = T.fill_multi_package_tracking(f, ids, 2)
check("2 units, 4 boxes → 2 packages with the first 2 IDs", r2 == ids[:2], r2)
check("…each package carries 1 unit and its own ID",
      [e.value for e in f.blocks["qty"]] == ["1", "1"] and [e.value for e in f.blocks["tid"]] == ids[:2],
      ([e.value for e in f.blocks["qty"]], [e.value for e in f.blocks["tid"]]))
check("…and says so", "Amazon takes one package per unit" in out.getvalue(), out.getvalue())

f = Form()
with redirect_stdout(io.StringIO()):
    r3 = T.fill_multi_package_tracking(f, ids[:2], 4)
check("4 units, 2 boxes → qty split [2, 2]", r3 == ids[:2] and [e.value for e in f.blocks["qty"]] == ["2", "2"],
      (r3, [e.value for e in f.blocks["qty"]]))

src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine", "tracking.py"),
           encoding="utf-8").read()
check("the upload record and the alert use exactly the IDs put on the form",
      src.count("list(_multi_ids) if _multi_ok else list(_all_tids[:1])") == 2)
check("a 1-unit order is not reported as 'add these on Seller Central by hand'",
      "if len(_all_tids) > 1 and _units == 1:" in src)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
