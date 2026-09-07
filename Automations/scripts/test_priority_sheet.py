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

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL)); sys.exit(1)


if __name__ == "__main__":
    main()
