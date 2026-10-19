#!/usr/bin/env python3
"""
test_priority_sheet.py — Priority_ASIN_Control upkeep and the reseller's waits (owner, 20 Oct 2026).

  1. A move never leaves a hole: the old cell is deleted (the column shifts up, notes with it) and
     the ASIN goes into the first empty cell of the target column, with its ZUHRA note.
  2. Coupon vs Deciding is decided on ONE AMAZON UNIT (Zoro price × pack), not the Zoro unit.
  3. "Zoro did not answer" is not a verdict: the ASIN stays queued (back of the queue), its last
     real verdict is kept, the sweep pauses after 3 in a row, and after 6 tries it is UNTESTABLE.
  4. The reseller holds discovery while the store's queue is not empty or the Coupons tab has
     priority codes its coupon placer has not swept; it never buys an ASIN untested with them.

Run:  python scripts/test_priority_sheet.py     (no network, no Chrome, nothing written to the sheet)
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"\n      {detail}" if detail and not ok else ""))


import engine.priority_check as PC  # noqa: E402

TMP = tempfile.mkdtemp(prefix="pc_test_")
PC.DATA = TMP
PC.VERDICTS_PATH = os.path.join(TMP, "priority_verdicts.json")
os.makedirs(os.path.join(TMP, "dataset"), exist_ok=True)


# ── a fake Google Sheet that really applies deleteRange / updateCells ─────────────────
class FakeSheet:
    def __init__(self, columns):                    # {"DM_Reseller": ["B0…", "", …], …}
        self.headers = list(columns)
        n = max(len(v) for v in columns.values())
        self.grid = [[(columns[h][r] if r < len(columns[h]) else "") for h in self.headers] for r in range(n)]
        self.notes = {}
        self.requests = []

    def rows(self):
        return [list(self.headers)] + [list(r) for r in self.grid]

    def col(self, h):
        ci = self.headers.index(h)
        vals = [r[ci] for r in self.grid]
        while vals and not vals[-1]:
            vals.pop()
        return vals

    # the googleapiclient call chain the code uses
    def spreadsheets(self):
        return self

    def batchUpdate(self, spreadsheetId=None, body=None):
        for rq in (body or {}).get("requests", []):
            self.requests.append(rq)
            if "deleteRange" in rq:
                r = rq["deleteRange"]["range"]
                ci, ri = r["startColumnIndex"], r["startRowIndex"] - 1      # grid row (header excluded)
                for k in range(ri, len(self.grid) - 1):
                    self.grid[k][ci] = self.grid[k + 1][ci]
                    self.notes[(k, ci)] = self.notes.pop((k + 1, ci), None)
                self.grid[-1][ci] = ""
                self.notes.pop((len(self.grid) - 1, ci), None)
            elif "updateCells" in rq:
                r = rq["updateCells"]["range"]
                ci, ri = r["startColumnIndex"], r["startRowIndex"] - 1
                while ri >= len(self.grid):
                    self.grid.append([""] * len(self.headers))
                v = rq["updateCells"]["rows"][0]["values"][0]
                self.grid[ri][ci] = v["userEnteredValue"]["stringValue"]
                self.notes[(ri, ci)] = v.get("note")
        return self

    def execute(self):
        return {}


PC._grid.update(sheetId=7, rows=500, cols=20)          # no grid growth needed in the fake


def fresh(columns):
    return FakeSheet(columns)


print("\n1. moves keep the columns compact")
sh = fresh({"DM_Reseller": ["A1", "A2", "B0TARGET01", "A4"], "DM_Coupon": ["C1", "", "C3"],
            "DM_Deciding": ["D1"], "HS_Reseller": ["B0TARGET01"]})
PC.move_asin(sh, sh.headers, sh.rows(), "B0TARGET01", "DM_Coupon", "DM", "DM_COUPON", "priority works")
check("removed from DM_Reseller and the cells below shifted up", sh.col("DM_Reseller") == ["A1", "A2", "A4"], sh.col("DM_Reseller"))
check("written into the first empty cell of DM_Coupon (the old gap), not below the last",
      sh.col("DM_Coupon") == ["C1", "B0TARGET01", "C3"], sh.col("DM_Coupon"))
check("the target cell carries the ZUHRA note", str(sh.notes.get((1, 1)) or "").startswith("ZUHRA"), sh.notes)
check("the other store's column is untouched", sh.col("HS_Reseller") == ["B0TARGET01"])
check("one atomic batchUpdate with a deleteRange(shift ROWS) and the write",
      [list(r)[0] for r in sh.requests] == ["deleteRange", "updateCells"]
      and sh.requests[0]["deleteRange"]["shiftDimension"] == "ROWS", sh.requests)

sh = fresh({"DM_Reseller": ["X", "B0DUP00001", "Y", "B0DUP00001", "Z"], "DM_Coupon": ["C1"], "DM_Deciding": []})
PC.move_asin(sh, sh.headers, sh.rows(), "B0DUP00001", "DM_Coupon", "DM")
check("duplicates in another column are all deleted, bottom-up", sh.col("DM_Reseller") == ["X", "Y", "Z"], sh.col("DM_Reseller"))
check("appended to a column without gaps", sh.col("DM_Coupon") == ["C1", "B0DUP00001"], sh.col("DM_Coupon"))

sh = fresh({"DM_Coupon": ["B0KEEP0001", "M", "B0KEEP0001", "N"], "DM_Reseller": []})
PC.move_asin(sh, sh.headers, sh.rows(), "B0KEEP0001", "DM_Coupon", "DM")
check("a duplicate in the target column is removed, the first one stays", sh.col("DM_Coupon") == ["B0KEEP0001", "M", "N"], sh.col("DM_Coupon"))
sh = fresh({"DM_Coupon": ["B0ONLY0001"], "DM_Reseller": ["R"]})
check("already only in the target → nothing written",
      PC.move_asin(sh, sh.headers, sh.rows(), "B0ONLY0001", "DM_Coupon", "DM") is False and not sh.requests)


print("\n2. Coupon vs Deciding on one Amazon unit")
import types  # noqa: E402

fake_zoro = types.ModuleType("platforms.zoro")
state = {"price": 16.0, "cartable": (True, "IN_STOCK")}
fake_zoro.check_inventory = lambda d, code, qty: state["cartable"]
fake_zoro.clear_cart_and_add = lambda d, code, qty: (True, state["price"])
fake_zoro.clear_cart_selenium = lambda d: True
fake_zoro.check_pdp_promo = lambda d, code: False
fake_zoro.get_product_brand = lambda d, code: "Acme"
fake_zoro.ensure_on_site = lambda d: None
fake_zoro.ensure_token = lambda d: state.get("token", True)
fake_api = types.ModuleType("checkout.true_api")
fake_api._get_cart_id = lambda d: "cart1"
fake_api._apply_coupon_api = lambda d, cid, c: (False, {"cartErrors": [{"detail": {"error": "not valid for the item(s)"}}]})
fake_api.classify_promo_error = lambda res: ("item", None)
fake_api._clear_cart_api = lambda d: True
sys.modules["platforms.zoro"] = fake_zoro
sys.modules["checkout.true_api"] = fake_api


class Drv:
    current_url = "https://www.zoro.com/"


sku = {"B0PACK0004": {"zoro_code": "G1", "zoro_multiple": 4, "qty_multiplier": 1, "brand": "Acme"},
       "B0SINGLE01": {"zoro_code": "G2", "zoro_multiple": 1, "qty_multiplier": 1, "brand": "Acme"}}
r = PC.check_one(Drv(), "B0PACK0004", sku, ["ZSELECTJ1026"])
check("$16 × 4-pack = $64 for one Amazon unit → Coupon (was Deciding)",
      r.get("Check_Suggest") == "Coupon" and "$64.00 for one Amazon unit" in r.get("Check_Note", ""), r)
r = PC.check_one(Drv(), "B0SINGLE01", sku, ["ZSELECTJ1026"])
check("$16 single → Deciding", r.get("Check_Suggest") == "Deciding", r)
state["token"] = False
r = PC.check_one(Drv(), "B0SINGLE01", sku, ["ZSELECTJ1026"])
check("logged out of Zoro → TRANSIENT 'not tested', no cart touched",
      r.get("Check_Status") == "TRANSIENT" and "not tested" in r.get("Check_Note", ""), r)
state["token"] = True


print("\n3. 'Zoro did not answer' stays queued and keeps the last real verdict")
qp = os.path.join(TMP, "priority_recheck_DM.json")
with open(qp, "w") as f:
    json.dump({"reason": "test", "asins": [["B0T1", "DM_Reseller"], ["B0T2", "DM_Reseller"], ["B0T3", "DM_Reseller"],
                                           ["B0T4", "DM_Reseller"]], "mode": "sweep_added"}, f)
PC.save_verdict("B0T1", {"Check_Status": "SELECT", "Check_Suggest": "Reseller", "Check_At": "2026-10-12 10:00",
                         "Check_Profile": "DM_COUPON"}, "DM_Reseller", "human")
calls = {"n": 0}


def transient_check(driver, asin, sku_map, pri, cache=None, mod=None):
    calls["n"] += 1
    return {"Check_ASIN": asin, "Check_At": PC._now(), "Check_Status": "TRANSIENT", "Check_Suggest": "Re-check later",
            "Check_Coupon": "", "Check_Cart": "", "Check_Note": "inventory API API_ERROR"}


real_check, real_ready = PC.check_one, PC.zoro_session_ready
PC.check_one = transient_check
PC.zoro_session_ready = lambda d: True
PC.read_tab = lambda svc: (["DM_Reseller"], [["DM_Reseller"]])
batch = [("B0T1", "DM_Reseller"), ("B0T2", "DM_Reseller"), ("B0T3", "DM_Reseller"), ("B0T4", "DM_Reseller")]
res = PC._run_batch(Drv(), "DM_COUPON", batch, sku, ["ZSELECTJ1026"], {}, None, None, None, [], [], True,
                    mode="sweep_added", on_done=lambda a: PC._queue_remove(qp, a),
                    on_retry=lambda a: PC._queue_retry(qp, a, "DM_COUPON"))
q = json.load(open(qp))
check("sweep paused after 3 'no answer' in a row (4th not tried)", calls["n"] == 3, calls)
check("nothing dropped from the queue — tried ones went to the back with a try counted",
      [x[0] for x in q["asins"]] == ["B0T4", "B0T1", "B0T2", "B0T3"] and q["asins"][1][2] == 1, q["asins"])
check("the last real verdict (SELECT) was kept, not overwritten by TRANSIENT",
      PC.load_verdicts()["B0T1"]["status"] == "SELECT", PC.load_verdicts().get("B0T1"))
for _ in range(PC.QUEUE_MAX_TRIES - 1):
    PC._queue_retry(qp, "B0T2", "DM_COUPON")
q = json.load(open(qp))
check(f"after {PC.QUEUE_MAX_TRIES} tries the ASIN leaves the queue as UNTESTABLE (column kept)",
      "B0T2" not in [x[0] for x in q["asins"]] and PC.load_verdicts()["B0T2"]["status"] == "UNTESTABLE", q)
PC.check_one, PC.zoro_session_ready = real_check, real_ready
os.remove(qp)


print("\n4. the reseller waits")
setp = os.path.join(TMP, "dataset", "priority_coupon_set_DM_COUPON.json")
with open(setp, "w") as f:
    json.dump({"codes": ["FALL93A26", "JANITORIAL71026"], "at": "2026-10-12 10:05"}, f)
new = ["MATERIAL81026", "SUPPLIES11726", "ZSELECTJ1026"]
r = PC.sweep_hold_reason("DM", new)
check("new codes in the tab that DM_COUPON has not swept → hold (coupon placer stopped / labels-only)",
      bool(r) and "ZSELECTJ1026" in r and "DM_COUPON" in r, r)
with open(setp, "w") as f:
    json.dump({"codes": sorted(new), "at": "2026-10-19 10:42"}, f)
check("swept set = tab, empty queue → no hold", PC.sweep_hold_reason("DM", new) is None)
PC.enqueue_asins("DM_COUPON", ["B0Q1", "B0Q2"], "test")
r = PC.sweep_hold_reason("DM", new)
check("queue not empty → hold, however old the sweep flag is", bool(r) and "2 ASIN(s)" in r, r)
check("no priority code in the tab at all → never hold (nothing to test with; Sunday)",
      PC.sweep_hold_reason("DM", []) is None)
os.remove(os.path.join(TMP, "priority_recheck_DM.json"))

v = {"B0OLD00001": {"status": "SELECT", "at": "2026-10-12 11:00", "profile": "DM_COUPON"},
     "B0FRESH001": {"status": "NO_PRIORITY", "at": "2026-10-19 11:30", "profile": "DM_COUPON"},
     "B0TRANS001": {"status": "TRANSIENT", "at": "2026-10-19 10:43", "profile": "DM_COUPON"},
     "B0BADGE001": {"status": "PROMO_INELIGIBLE", "at": "2026-09-01 10:00", "profile": "DM_COUPON"},
     "B0HSONLY01": {"status": "TRANSIENT", "at": "2026-10-19 19:05", "profile": "HS_COUPON"}}
check("tested before this week's coupons → wait", bool(PC.reseller_must_wait("B0OLD00001", "DM", v, new)))
check("tested after them → place", PC.reseller_must_wait("B0FRESH001", "DM", v, new) is None)
check("last test got no answer → wait", "no answer" in (PC.reseller_must_wait("B0TRANS001", "DM", v, new) or ""))
check("PDP 'not eligible' badge → place (no coupon can apply)", PC.reseller_must_wait("B0BADGE001", "DM", v, new) is None)
check("the other store's verdict does not hold this store", PC.reseller_must_wait("B0HSONLY01", "DM", v, new) is None)
check("never tested at all → as before (place)", PC.reseller_must_wait("B0NEVER001", "DM", v, new) is None)
check("no priority code in the tab → nothing to wait for", PC.reseller_must_wait("B0OLD00001", "DM", v, []) is None)

src = open(os.path.join(AUTO, "engine", "orders.py"), encoding="utf-8").read()
check("the supervisor gets the price of ONE AMAZON UNIT (× qty_multiplier × zoro_multiple)",
      '* float(primary_item.get("qty_multiplier") or 1)' in src and '* int(primary_item.get("zoro_multiple") or 1)' in src)
check("the reseller's late check queues untested ASINs instead of buying them",
      "_pc_late.reseller_must_wait(_pasin, _STORE" in src and '_pc_late.enqueue_asins(f"{_STORE}_COUPON"' in src)
check("_sweep_active asks sweep_hold_reason with the Coupons tab's priority codes",
      "_pc.sweep_hold_reason(_STORE, _sheet_priority_codes())" in src)

print("\n5. two profiles on one Zoro account never both place")
for _m in ("platforms.zoro", "checkout.true_api"):
    sys.modules.pop(_m, None)
import checkout.true_api as TA  # noqa: E402
TA._LIVE_ACCOUNTS = os.path.join(TMP, "zoro_accounts_live.json")
acct = {"id": "cust-A"}
TA._get_cart = lambda d: {"cartId": "c", "customerId": acct["id"]}
check("first profile on the account → no clash", TA._shared_account_clash(None, "DM_COUPON") is None)
check("second profile on the SAME account → clash names the other", TA._shared_account_clash(None, "HS_COUPON") == "DM_COUPON")
acct["id"] = "cust-B"
check("after it logs in to its own account → no clash", TA._shared_account_clash(None, "HS_COUPON") is None)
acct["id"] = "cust-A"
check("the first profile again, alone on its account → no clash", TA._shared_account_clash(None, "DM_COUPON") is None)
ta_src = open(os.path.join(AUTO, "checkout", "true_api.py"), encoding="utf-8").read()
check("the check runs before the cart is cleared and returns SHARED_ACCOUNT",
      ta_src.index("_shared_account_clash(driver, active_profile)") < ta_src.index('print(f"\\n[3] _clear_cart_api()")')
      and '_format_checkout_result("SHARED_ACCOUNT"' in ta_src)
check("the placer releases a SHARED_ACCOUNT order (nothing bought, retried)",
      '"CART_BUSY", "SHARED_ACCOUNT", "NO_COUPONS_SKIP"' in src)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
