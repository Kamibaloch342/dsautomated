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

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL)); sys.exit(1)


if __name__ == "__main__":
    main()
