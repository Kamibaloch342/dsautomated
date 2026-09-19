#!/usr/bin/env python3
"""
test_tracking_dedupe.py — a buyer must never be sent the same tracking number twice.

Found on 19 Sep from order 111-1549917-2638602 (Hathoro, 4 boxes): closing_orders.tracking_ids
held every number twice —

    512626394999, 512626395002, 512626395013, 512626395024,
    512626394999, 512626395002, 512626395013, 512626395024

so the delivery message listed eight numbers and told the buyer their order had shipped in
"8 separate packages". 360 orders were in that state; 24 had already been messaged.

The source data was clean: archive_tracking held 4, the tracker logged 4, the enrichment log
recorded 4. Each writer dedupes its own input — but a merge of two already-clean sets does
not, and that merge is not audited. So the guard goes in the two places that cannot be
bypassed: the single upsert every closing row passes through, and the message builder itself.

Run:  python scripts/test_tracking_dedupe.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
sys.path.insert(0, AUTO)

PASS, FAIL = [], []

REAL = ("512626394999, 512626395002, 512626395013, 512626395024, "
        "512626394999, 512626395002, 512626395013, 512626395024")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


def main():
    # ── the message a buyer actually receives ────────────────────────────────
    print("\n── the delivery message ──")
    from engine.post_delivery import _build_tracking_lines

    lines, note = _build_tracking_lines(REAL)
    n = len(re.findall(r"^\s*\d+\)", lines, re.M))
    check("the real doubled order lists 4 numbers, not 8", n == 4, f"listed {n}")
    check("each number appears exactly once",
          all(lines.count(t) == 1 for t in
              ("512626394999", "512626395002", "512626395013", "512626395024")))
    check("the buyer is told 4 packages, not 8", "4 separate packages" in note, note[:70])

    lines, note = _build_tracking_lines("1Z999")
    check("a single tracking id gets no multi-package note", note == "" and "1) 1Z999" in lines)
    lines, note = _build_tracking_lines("1Z999, 1Z999, 1Z999")
    check("the same id three times is still ONE package", note == "" and lines.count("1Z999") == 1)
    lines, note = _build_tracking_lines("1Z111, 1Z222")
    check("two genuinely different ids still say 2 packages", "2 separate packages" in note)
    lines, note = _build_tracking_lines("")
    check("no tracking at all falls back to 'see order details'", "see order details" in lines)
    lines, note = _build_tracking_lines(None)
    check("None is handled like empty", "see order details" in lines)
    lines, _ = _build_tracking_lines(" 1Z111 ,, 1Z222 , ")
    check("stray spaces and empty entries are ignored",
          lines.count(")") == 2 and "1Z111" in lines and "1Z222" in lines)
    lines, _ = _build_tracking_lines("1Z333, 1Z111, 1Z222, 1Z111")
    check("first-seen order is preserved",
          lines.index("1Z333") < lines.index("1Z111") < lines.index("1Z222"))

    # ── the door every closing row goes through ──────────────────────────────
    print("\n── the upsert cannot store a duplicate ──")
    import sqlite3
    import utils.unified_db as U

    db = os.path.join(os.environ.get("TMPDIR", "/tmp"), "dedupe_test.db")
    if os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE closing_orders (amazon_order_id TEXT PRIMARY KEY, "
                "tracking_ids TEXT, account TEXT, updated_at TEXT)")
    con.commit()

    def stored(oid):
        return con.execute("SELECT tracking_ids FROM closing_orders WHERE amazon_order_id=?",
                           (oid,)).fetchone()[0]

    U.upsert_closing_order({"amazon_order_id": "A", "tracking_ids": REAL, "account": "Hathoro"},
                           conn=con)
    got = stored("A")
    check("the doubled value is stored deduplicated",
          got == "512626394999, 512626395002, 512626395013, 512626395024", got)

    U.upsert_closing_order({"amazon_order_id": "B", "tracking_ids": "1Z111, 1Z222"}, conn=con)
    check("a clean multi-box value is stored untouched", stored("B") == "1Z111, 1Z222")

    U.upsert_closing_order({"amazon_order_id": "C", "tracking_ids": "1Z999"}, conn=con)
    check("a single id is untouched", stored("C") == "1Z999")

    U.upsert_closing_order({"amazon_order_id": "D", "tracking_ids": ""}, conn=con)
    check("an empty value stays empty", stored("D") == "")

    U.upsert_closing_order({"amazon_order_id": "E", "account": "HOANK"}, conn=con)
    check("a row that does not mention tracking_ids still writes",
          con.execute("SELECT account FROM closing_orders WHERE amazon_order_id='E'")
             .fetchone()[0] == "HOANK")

    before = {"amazon_order_id": "F", "tracking_ids": "1Z1, 1Z1"}
    U.upsert_closing_order(before, conn=con)
    check("the caller's own dict is not mutated behind its back",
          before["tracking_ids"] == "1Z1, 1Z1", str(before))
    check("...while what was stored is deduplicated", stored("F") == "1Z1")

    con.close()
    os.remove(db)

    print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
