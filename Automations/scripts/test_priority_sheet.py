#!/usr/bin/env python3
"""
test_priority_sheet.py — the Priority_ASIN_Control writer against a FAKE Sheets
service (no network). The first live day failed on exactly this layer: the tab
had 6 columns × 101 rows and every Check_* write beyond the grid returned 400.

Covers: column letters · header block creation (grid grows first) · per-row
upsert (existing row reused, new row appended, grid grows for rows) · move_asin
(dedupes duplicates in the target column, blanks other columns, appends once,
grows rows when the column is full).

Run:  python scripts/test_priority_sheet.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)
from engine import priority_check as pc  # noqa: E402

TAB = pc.TAB
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


class Exec:
    def __init__(self, v): self.v = v
    def execute(self): return self.v


class FakeSheets:
    """Mimics googleapiclient's chained calls: spreadsheets().get / .batchUpdate /
    .values().get / .update / .batchUpdate — records every write."""
    def __init__(self, data, cols=6, rows=101):
        self.data, self.cols, self.rows, self.calls = data, cols, rows, []

    def spreadsheets(self): return self
    def values(self): return self

    def get(self, **kw):
        if "fields" in kw:
            return Exec({"sheets": [{"properties": {"title": TAB, "sheetId": 7,
                                                    "gridProperties": {"rowCount": self.rows, "columnCount": self.cols}}}]})
        return Exec({"values": self.data})

    def update(self, **kw):
        self.calls.append(("update", kw["range"].split("!")[1], kw["body"]["values"]))
        return Exec({})

    def batchUpdate(self, **kw):
        body = kw["body"]
        if "requests" in body:                       # grid growth
            for r in body["requests"]:
                ad = r["appendDimension"]
                if ad["dimension"] == "COLUMNS": self.cols += ad["length"]
                else: self.rows += ad["length"]
            self.calls.append(("grow", self.cols, self.rows))
        else:                                        # value writes
            self.calls.append(("values", [(d["range"].split("!")[1], d["values"][0][0]) for d in body["data"]]))
        return Exec({})


def fresh(fake):
    pc._grid.update(sheetId=None, rows=0, cols=0)
    return fake


def main():
    check("col letters A / Z / AA / AH", (pc._col_letter(0), pc._col_letter(25), pc._col_letter(26), pc._col_letter(33)) == ("A", "Z", "AA", "AH"))

    # ── header block creation on the real 6-column tab ───────────────────
    headers6 = ["DM_Reseller", "DM_Coupon", "HS_Reseller", "HS_Coupon", "DM_Deciding", "HS_Deciding"]
    f = fresh(FakeSheets([headers6] + [["B0A", "B0B", "", "", "", ""]]))
    h2 = pc.ensure_check_headers(f, list(headers6))
    grow = [c for c in f.calls if c[0] == "grow"]
    upd = [c for c in f.calls if c[0] == "update"]
    check("headers: grid grown to fit the block BEFORE writing", grow and f.cols >= 15 and f.calls.index(grow[0]) < f.calls.index(upd[0]))
    check("headers: written at H1:O1 (one blank separator after column F)", upd and upd[0][1] == "H1:O1" and upd[0][2] == [pc.CHECK_COLS])
    check("headers: returned list has Check_ASIN at index 7", h2.index("Check_ASIN") == 7)

    # ── per-row upsert ──────────────────────────────────────────────────
    hdr = headers6 + [""] + pc.CHECK_COLS
    rows = [hdr,
            ["B0A", "", "", "", "", "", "", "B0X", "COUPON_OK"],
            ["", "", "", "", "", "", "", "B0Y", "SELECT"]]
    f = fresh(FakeSheets(rows, cols=15, rows=101))
    r = pc.upsert_check_row(f, hdr, rows, {"Check_ASIN": "B0NEW", "Check_Status": "NO_PRIORITY", "Check_Suggest": "Deciding"})
    check("upsert: new ASIN goes to the first free block row (row 4, H4:O4)", r == 4 and f.calls[-1][1] == "H4:O4" and f.calls[-1][2][0][0] == "B0NEW")
    r = pc.upsert_check_row(f, hdr, rows, {"Check_ASIN": "b0y", "Check_Status": "COUPON_OK", "Check_Suggest": "Coupon"})
    check("upsert: existing ASIN overwrites its own row (row 3)", r == 3 and f.calls[-1][1] == "H3:O3")
    big = [hdr] + [["", "", "", "", "", "", "", f"B0{i:07d}", "x"] for i in range(100)]
    f = fresh(FakeSheets(big, cols=15, rows=101))
    r = pc.upsert_check_row(f, hdr, big, {"Check_ASIN": "B0LAST", "Check_Status": "SELECT"})
    check("upsert: block full (row 102 needed) → rows grown first", r == 102 and any(c[0] == "grow" and c[2] >= 102 for c in f.calls))

    # ── move_asin ───────────────────────────────────────────────────────
    rows = [headers6,
            ["B0DUP", "B0MOVE", "", "", "", ""],
            ["B0DUP", "B0KEEP", "", "", "", ""],
            ["", "", "", "", "", ""]]
    f = fresh(FakeSheets(rows, cols=6, rows=101))
    ok = pc.move_asin(f, headers6, rows, "B0DUP", "DM_Reseller", "DM")
    w = dict(f.calls[-1][1]) if f.calls and f.calls[-1][0] == "values" else {}
    check("move: duplicate in the target column → the second copy is blanked, nothing appended", ok and w == {"A3": ""})
    f = fresh(FakeSheets(rows, cols=6, rows=101))
    ok = pc.move_asin(f, headers6, rows, "B0MOVE", "DM_Reseller", "DM")
    w = dict(f.calls[-1][1])
    check("move: ASIN in another column of the store → blanked there and appended to the target (A4)", ok and w == {"B2": "", "A4": "B0MOVE"})
    f = fresh(FakeSheets(rows, cols=6, rows=101))
    ok = pc.move_asin(f, headers6, rows, "B0KEEP", "DM_Coupon", "DM")
    check("move: already only in the target → no write at all", ok is False and not f.calls)
    full = [headers6] + [[f"B0{i:07d}", "", "", "", "", ""] for i in range(100)]
    f = fresh(FakeSheets(full, cols=6, rows=101))
    ok = pc.move_asin(f, headers6, full, "B0FRESH", "DM_Reseller", "DM")
    check("move: column full (row 102) → rows grown before the append", ok and any(c[0] == "grow" for c in f.calls) and dict(f.calls[-1][1]) == {"A102": "B0FRESH"})

    # ── move policy: human placements are respected ──────────────────────
    mm = pc.may_move
    check("policy: ASIN in no column → always placed", mm("NO_PRIORITY", "", False) and mm("SELECT", "", False, "live"))
    check("policy: human-placed Reseller/Deciding, no priority coupon → NOT moved (new / live / recheck / all)",
          not any(mm(st, col, False, mode) for st in ("NO_PRIORITY", "REGULAR_OK", "SELECT")
                  for col in ("HS_Reseller", "HS_Deciding") for mode in ("new", "live", "recheck", "all")))
    check("policy: priority coupon works → promoted even from a human placement", mm("COUPON_OK", "HS_Reseller", False, "live"))
    check("policy: product-page ineligible → Reseller even from a human placement", mm("PROMO_INELIGIBLE", "HS_Coupon", False, "new"))
    check("policy: bot-owned placement may be re-sorted", mm("NO_PRIORITY", "HS_Coupon", True, "new"))
    check("policy: coupon-dependent (_Coupon) ASIN re-sorted on recheck / all, not on the scheduled new-ASIN check",
          mm("SELECT", "HS_Coupon", False, "recheck") and mm("SELECT", "DM_Coupon", False, "all") and not mm("SELECT", "HS_Coupon", False, "new"))
    check("policy: reset rebuilds everything", mm("SELECT", "HS_Deciding", False, "reset") and mm("NO_PRIORITY", "HS_Reseller", False, "reset"))
    check("owner: bot_owned only when the bot put it in THIS column",
          pc._bot_owned({"owner": "bot", "column": "HS_Coupon"}, "HS_Coupon") and not pc._bot_owned({"owner": "bot", "column": "HS_Coupon"}, "HS_Reseller")
          and not pc._bot_owned({"owner": "human", "column": "HS_Coupon"}, "HS_Coupon") and not pc._bot_owned({}, "HS_Coupon"))

    # ── _run_batch end-to-end with a fake driver, fake sheet and a temp memory ─
    import tempfile, types
    tmp = tempfile.mkdtemp()
    pc.DATA = tmp                                       # every file the checker writes goes to the temp dir
    pc.VERDICTS_PATH = os.path.join(tmp, "verdicts.json")
    pc.STATE_PATH = os.path.join(tmp, "state.json")
    import utils.decision_log as _dl
    _dl.log = lambda *a, **k: None                      # never touch the live decision log from a test
    verdicts = {"B0HUMAN": ("SELECT", "Reseller"), "B0NEWONE": ("NO_PRIORITY", "Deciding"),
                "B0PROMO": ("COUPON_OK", "Coupon"), "B0BOT": ("SELECT", "Reseller"), "B0CDEP": ("SELECT", "Reseller")}
    pc.check_one = lambda driver, asin, sku_map, pri, cache=None, mod=None: {
        "Check_ASIN": asin, "Check_At": pc._now(), "Check_Status": verdicts[asin][0], "Check_Suggest": verdicts[asin][1],
        "Check_Coupon": "", "Check_Cart": "$10", "Check_Note": ""}
    mod = types.SimpleNamespace(gui_cfg={"automation": {"priority_check": {"auto_assign": True, "write_sheet_notes": False}}})
    headers6 = ["DM_Reseller", "DM_Coupon", "HS_Reseller", "HS_Coupon", "DM_Deciding", "HS_Deciding"]
    def batch(rows, todo, mode, seed=None):
        try: os.remove(pc.VERDICTS_PATH)
        except OSError: pass
        for a, (row, col, owner) in (seed or {}).items(): pc.save_verdict(a, row, col, owner)
        f = fresh(FakeSheets([list(r) for r in rows], cols=6, rows=101))
        pc._run_batch(None, "HS_COUPON", todo, {}, ["FACILITY8M26"], None, mod, f, f, [list(r) for r in rows][0], [list(r) for r in rows], True, mode=mode)
        writes = {}
        for c in f.calls:
            if c[0] == "values": writes.update(dict(c[1]))
        return writes, pc.load_verdicts()
    base = [headers6, ["", "", "", "B0HUMAN", "", ""], ["", "", "", "B0CDEP", "", ""], ["", "", "B0PROMO", "", "", ""], ["", "", "", "", "", ""]]
    w, v = batch(base, [("B0HUMAN", "HS_Coupon")], "new")
    check("batch/new: human-placed HS_Coupon ASIN with SELECT verdict → no sheet write, verdict remembered as human/HS_Coupon",
          not w and v["B0HUMAN"]["column"] == "HS_Coupon" and v["B0HUMAN"]["owner"] == "human", f"writes={w} v={v.get('B0HUMAN')}")
    w, v = batch(base, [("B0CDEP", "HS_Coupon")], "recheck")
    check("batch/recheck: coupon-dependent ASIN with SELECT verdict → moved to HS_Reseller, owner bot",
          w.get("D3") == "" and "B0CDEP" in w.values() and v["B0CDEP"]["column"] == "HS_Reseller" and v["B0CDEP"]["owner"] == "bot", f"writes={w}")
    w, v = batch(base, [("B0PROMO", "HS_Reseller")], "new")
    check("batch/new: priority coupon works on a human-placed HS_Reseller ASIN → promoted to HS_Coupon",
          w.get("C4") == "" and "B0PROMO" in w.values() and v["B0PROMO"]["column"] == "HS_Coupon", f"writes={w}")
    w, v = batch(base, [("B0NEWONE", "")], "new")
    check("batch/new: ASIN in no column → placed in HS_Deciding, owner bot",
          "B0NEWONE" in w.values() and v["B0NEWONE"]["column"] == "HS_Deciding" and v["B0NEWONE"]["owner"] == "bot", f"writes={w}")
    w, v = batch(base, [("B0HUMAN", "HS_Coupon")], "new",
                 seed={"B0HUMAN": ({"Check_Status": "COUPON_OK", "Check_Suggest": "Coupon"}, "HS_Coupon", "bot")})
    check("batch/new: the bot's OWN earlier placement may be re-sorted (HS_Coupon → HS_Reseller)",
          w.get("D2") == "" and "B0HUMAN" in w.values() and v["B0HUMAN"]["column"] == "HS_Reseller", f"writes={w}")
    w, v = batch(base, [("B0HUMAN", "HS_Coupon")], "new",
                 seed={"B0HUMAN": ({"Check_Status": "COUPON_OK", "Check_Suggest": "Coupon"}, "HS_Reseller", "bot")})
    check("batch/new: bot placed it in ANOTHER column earlier, a human moved it since → treated as human, not moved",
          not w and v["B0HUMAN"]["owner"] == "human", f"writes={w} v={v.get('B0HUMAN')}")

    # ── no priority coupons loaded → nothing moves ───────────────────────
    w, v = batch(base, [("B0CDEP", "HS_Coupon")], "recheck")            # sanity: with coupons it moves
    check("batch: with priority coupons a recheck moves the Coupon-column ASIN", "B0CDEP" in w.values())
    try: os.remove(pc.VERDICTS_PATH)
    except OSError: pass
    f = fresh(FakeSheets([list(r) for r in base], cols=6, rows=101))
    pc._run_batch(None, "HS_COUPON", [("B0CDEP", "HS_Coupon")], {}, [], None, mod, f, f, [list(r) for r in base][0],
                  [list(r) for r in base], True, mode="recheck")
    w = {}
    for c in f.calls:
        if c[0] == "values": w.update(dict(c[1]))
    check("batch: with NO priority coupons nothing is moved (verdict only)", not w and "B0CDEP" in pc.load_verdicts(), f"writes={w}")

    # ── re-check queue: crash-safe, per-ASIN bookkeeping, isolation ──────
    class Mod:
        gui_cfg = {"automation": {"priority_check": {"auto_assign": True, "write_sheet_notes": False}}}
        def __init__(self, sheet): self.sheet = sheet
        def _get_sheets_service(self): return self.sheet
        def _get_sheets_write_service(self): return self.sheet
        def load_sku_map(self): return {}
        def load_coupon_codes(self): return ([{"code": "FACILITY8M26", "pct": 15}], [])
    qrows = [headers6] + [["", "", f"B0Q{i:07d}", "", "", ""] for i in range(1, 5)]      # 4 ASINs in HS_Reseller (10-char IDs)
    sheet = fresh(FakeSheets([list(r) for r in qrows], cols=6, rows=101))
    m = Mod(sheet)
    try: os.remove(pc.VERDICTS_PATH)
    except OSError: pass
    n = pc.enqueue_recheck("HS_COUPON", "test coupon change", mod=m)
    qp = pc._queue_path("HS_COUPON")
    check("queue: enqueue lists every ASIN of the store", n == 4 and pc.queue_size("HS_COUPON") == 4)
    seen = []
    def flaky_check(driver, asin, sku_map, pri, cache=None, mod=None):
        seen.append(asin)
        if asin == "B0Q0000002":
            raise RuntimeError("boom")
        return {"Check_ASIN": asin, "Check_At": pc._now(), "Check_Status": "COUPON_OK", "Check_Suggest": "Coupon",
                "Check_Coupon": "FACILITY8M26 (priority)", "Check_Cart": "$10", "Check_Note": ""}
    pc.check_one = flaky_check
    res = pc.run_queue(None, "HS_COUPON", sku_map={}, coupon_codes=([{"code": "FACILITY8M26", "pct": 15}], []), mod=m, max_asins=3)
    check("queue: one failing ASIN does not abort the batch (3 tested, 3 results incl. ERROR row)",
          seen == ["B0Q0000001", "B0Q0000002", "B0Q0000003"] and len(res) == 3 and any(r["Check_Status"] == "ERROR" for r in res), f"seen={seen} n={len(res)}")
    check("queue: finished ASINs are removed one by one (1 left after a batch of 3)", pc.queue_size("HS_COUPON") == 1)
    v = pc.load_verdicts()
    check("queue: working priority coupon promoted B0Q…1/B0Q…3 to HS_Coupon, owner bot",
          v.get("B0Q0000001", {}).get("column") == "HS_Coupon" and v.get("B0Q0000003", {}).get("owner") == "bot", str({k: v[k].get("column") for k in v}))
    # a pending RESET is not downgraded by a later coupon-change queue
    pc.enqueue_recheck("HS_COUPON", "reset", mod=m, include_ineligible=True)
    pc.enqueue_recheck("HS_COUPON", "new coupon", mod=m)
    import json as _json
    check("queue: a pending reset keeps mode=reset when a coupon change re-queues", _json.load(open(qp))["mode"] == "reset")
    # corrupt file → size -1, run_queue rebuilds it instead of silently doing nothing
    with open(qp, "w") as fh: fh.write("{not json")
    check("queue: unreadable file reports -1 (not 0)", pc.queue_size("HS_COUPON") == -1)
    pc.check_one = lambda driver, asin, sku_map, pri, cache=None, mod=None: {
        "Check_ASIN": asin, "Check_At": pc._now(), "Check_Status": "SELECT", "Check_Suggest": "Reseller",
        "Check_Coupon": "", "Check_Cart": "$10", "Check_Note": ""}
    res = pc.run_queue(None, "HS_COUPON", sku_map={}, coupon_codes=([{"code": "X", "pct": 15}], []), mod=m, max_asins=50)
    check("queue: unreadable file is rebuilt from the full store and drained", len(res) == 4 and pc.queue_size("HS_COUPON") == 0, f"n={len(res)}")
    check("queue: with no priority coupons the queue waits untouched",
          (pc.enqueue_recheck("HS_COUPON", "again", mod=m) == 4
           and pc.run_queue(None, "HS_COUPON", sku_map={}, coupon_codes=([], []), mod=m) == [] and pc.queue_size("HS_COUPON") == 4))
    # preloaded verdicts for is_permanent
    check("is_permanent: uses the preloaded verdict dict",
          pc.is_permanent("B0PERM", {}, {"B0PERM": {"status": "PROMO_INELIGIBLE", "note": "Zoro's best price badge (PDP)"}})
          and not pc.is_permanent("B0PERM", {}, {}))

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL)); sys.exit(1)


if __name__ == "__main__":
    main()
