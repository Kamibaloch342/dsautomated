#!/usr/bin/env python3
"""
compact_priority_sheet.py — close the gaps in Priority_ASIN_Control (owner, 20 Oct 2026).

Until 20 Oct every bot move BLANKED the ASIN's old cell and wrote it below the last one, so the
columns filled up with holes (and ZUHRA notes on empty cells). engine/priority_check.move_asin
now deletes the cell and shifts the column up; this script tidies what is already there:

  • backs up the whole tab — values AND cell notes — to ~/.zuhra/backups/ first;
  • in every list column (not Check_*), deletes each empty cell between row 2 and the column's
    last ASIN, bottom-up, shifting the cells below up (notes travel with their cells) — the
    order of the ASINs is kept, nothing is added or removed;
  • reports, without changing them, ASINs that sit twice in a column or in two columns of the
    same store.

    python scripts/compact_priority_sheet.py            # dry run: what would move
    python scripts/compact_priority_sheet.py --apply    # do it (refuses while a COUPON placer runs)
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

from utils.config import GOOGLE_SHEET_ID, GOOGLE_SHEET_PRIORITY_ASIN_TAB as TAB  # noqa: E402

BACKUPS = os.path.expanduser("~/.zuhra/backups")


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from utils.config import GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE
    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_SHEETS_SERVICE_ACCOUNT_FILE, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _coupon_placers_running():
    r = subprocess.run(["pgrep", "-a", "-f", r"engine/orders\.py.*(DM|HS)_COUPON"], capture_output=True, text=True)
    return r.returncode == 0


def main():
    apply = "--apply" in sys.argv
    svc = _service()
    got = svc.spreadsheets().get(spreadsheetId=GOOGLE_SHEET_ID, ranges=[f"'{TAB}'!A1:AZ1000"],
                                 includeGridData=True,
                                 fields="sheets(properties(sheetId,title),data(rowData(values(formattedValue,note))))").execute()
    sheet = next(s for s in got["sheets"] if s["properties"]["title"] == TAB)
    sid = sheet["properties"]["sheetId"]
    grid = []
    for rd in (sheet.get("data") or [{}])[0].get("rowData", []) or []:
        grid.append([{"v": (c.get("formattedValue") or "").strip(), "n": c.get("note") or ""}
                     for c in (rd.get("values") or [])])
    if not grid:
        print("tab is empty — nothing to do")
        return 0
    headers = [c["v"] for c in grid[0]]

    os.makedirs(BACKUPS, exist_ok=True)
    bk = os.path.join(BACKUPS, f"priority_asin_control_{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(bk, "w", encoding="utf-8") as f:
        json.dump({"tab": TAB, "sheetId": sid, "headers": headers, "grid": grid}, f, indent=1)
    print(f"backup (values + notes): {bk}")

    def cell(r, c):
        row = grid[r] if r < len(grid) else []
        return row[c] if c < len(row) else {"v": "", "n": ""}

    reqs, report, seen = [], [], {}
    for ci, h in enumerate(headers):
        if not h or h.startswith("Check_"):
            continue
        vals = [cell(r, ci)["v"] for r in range(1, len(grid))]
        last = max((i for i, v in enumerate(vals) if v), default=-1)
        if last < 0:
            continue
        blanks = [i for i in range(last + 1) if not vals[i]]
        stale_notes = sum(1 for i in blanks if cell(i + 1, ci)["n"])
        asins = [v.upper() for v in vals[:last + 1] if v]
        dups = sorted({a for a in asins if asins.count(a) > 1})
        report.append(f"  {h:16s} {len(asins):4d} ASINs · {len(blanks):3d} empty cell(s) to close"
                      f" ({stale_notes} with an old note)" + (f" · in this column twice: {', '.join(dups)}" if dups else ""))
        for a in asins:
            seen.setdefault((h.split('_')[0].upper(), a), []).append(h)
        for i in sorted(blanks, reverse=True):         # bottom-up: earlier row numbers stay valid
            reqs.append({"deleteRange": {"range": {"sheetId": sid, "startRowIndex": i + 1, "endRowIndex": i + 2,
                                                   "startColumnIndex": ci, "endColumnIndex": ci + 1},
                                         "shiftDimension": "ROWS"}})
    print(f"\n'{TAB}':")
    print("\n".join(report))
    cross = {k: v for k, v in seen.items() if len(set(v)) > 1}
    if cross:
        print("\nin two columns of the same store (left as they are — decide by hand):")
        for (store, a), cols in sorted(cross.items()):
            print(f"  {a}: {', '.join(sorted(set(cols)))}")
    print(f"\n{len(reqs)} empty cell(s) to close")
    if not reqs:
        return 0
    if not apply:
        print("dry run — nothing changed (add --apply)")
        return 0
    if _coupon_placers_running() and "--force" not in sys.argv:
        print("⚠️  a COUPON placer is running and may move an ASIN while the columns shift — stop it first "
              "(or add --force)")
        return 2
    svc.spreadsheets().batchUpdate(spreadsheetId=GOOGLE_SHEET_ID, body={"requests": reqs}).execute()
    print(f"✅ closed {len(reqs)} empty cell(s) in one batch — restore from {bk} if anything looks wrong")
    return 0


if __name__ == "__main__":
    sys.exit(main())
