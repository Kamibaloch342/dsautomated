"""
tag_manual_review.py — flag orders that need MANUAL review (don't auto-trust their
closing/profit) by merchant-order-id pattern, into a `manual_review` column.

Patterns (per Kamran's rules):
  WB#### - D   → placer's duplicate-detect marker (already placed) → 'WB-D (duplicate-detect)'
  C-WB####     → placed then cancelled                            → 'C-WB (cancelled-placed)'
  (WW… Grainger orders have no WB so aren't in closing_orders — they need the
   non-WB ingest step before they can be tagged.)

Non-destructive: only sets manual_review; never changes labels/profit. closing.py's
persist is column-scoped so it won't clobber this. Dry-run by default.

Usage:
  ../.venv/bin/python scripts/tag_manual_review.py Hathoro        # preview
  ../.venv/bin/python scripts/tag_manual_review.py Hathoro --go   # apply
"""
import os, sys, re, sqlite3
ACCOUNT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "Hathoro"
GO = "--go" in sys.argv
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "automations_unified.db")

# first match wins; ordered so -D is checked before generic C-
PATTERNS = [
    ("WB-D (duplicate-detect)", lambda m: bool(re.search(r"WB\d+\s*-\s*D\b", m.upper()))),
    ("C-WB (cancelled-placed)", lambda m: m.upper().startswith("C-") or "C-WB" in m.upper()),
]

con = sqlite3.connect(DB, timeout=60); con.execute("PRAGMA busy_timeout=60000")
cols = {r[1] for r in con.execute("PRAGMA table_info(closing_orders)")}
if "manual_review" not in cols:
    if GO:
        con.execute("ALTER TABLE closing_orders ADD COLUMN manual_review TEXT")
        con.commit()
        print("added column closing_orders.manual_review TEXT")
    else:
        print("(dry-run) would add column closing_orders.manual_review TEXT")

rows = con.execute("SELECT amazon_order_id, merchant_order_id FROM closing_orders WHERE account=?",
                   (ACCOUNT,)).fetchall()
from collections import Counter
tally = Counter(); updates = []
for aid, moid in rows:
    m = moid or ""
    for tag, test in PATTERNS:
        if test(m):
            tally[tag] += 1
            updates.append((tag, aid))
            break

print(f"\naccount={ACCOUNT}  scanned={len(rows)} orders")
for tag, n in tally.most_common():
    print(f"  {tag:26} {n}")
print(f"  total to flag: {len(updates)}")

if GO and "manual_review" in (cols | {"manual_review"}):
    for tag, aid in updates:
        con.execute("UPDATE closing_orders SET manual_review=? WHERE amazon_order_id=?", (tag, aid))
    con.commit()
    print(f"\n✅ tagged {len(updates)} orders with manual_review")
else:
    print("\nDRY-RUN — nothing written. Re-run with --go to apply.")
con.close()
