#!/usr/bin/env bash
# repair_closing_gaps.sh — fill the missing fields the closing coverage report lists.
#
#   bash Automations/scripts/repair_closing_gaps.sh          # steps 1-3 (Gmail + DB, no Chrome)
#   bash Automations/scripts/repair_closing_gaps.sh --fedex   # also step 4 (delivery_status, needs Chrome)
#
# ROOT CAUSES this repairs (diagnosed 2026-08-22 against the live DB):
#   * Hathoro's earnings inbox abecomllc26 was never fetched by the recurring
#     pipeline (build_gmail_archive only fetched EARNINGS_GMAIL_ACCOUNTS[0]).
#     Proof: Jul16-Aug11 archive density abecomllc26=173 vs qurtdis=7450.
#     The code is fixed; this backfills the historical gap it left behind
#     -> earnings, sold_email_time, profit_dollars, placement_speed.
#   * Inboxes wrenchlyllc and salesmercentile1 stalled ~2026-07-30 in the
#     zoro/tracking archives -> shipped_email_time, tracking_ids, zoro_ship_speed.
#   * closing.py only enriches inside its 30-day report window, so older orders
#     never picked the data back up. reconcile_and_reenrich has no window.
#
# CONCURRENCY (important):
#   Steps 1-2 are safe any time - Gmail API + append-only archive writes.
#   Step 3 is NOT: reconcile_and_reenrich runs its whole per-row pass inside ONE
#   implicit transaction, holding the single SQLite WAL writer lock for 1-3 min.
#   The placers use busy_timeout=60000, so a placement landing in that window
#   FAILS TO RECORD - data/unified_db_write_failures.jsonl already holds 53
#   "database is locked" entries (orders bought but not written down).
#   -> this script refuses step 3 while placers are running (override: --force-busy)
#      and takes a DB snapshot first.
set -uo pipefail

AUTO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$AUTO/.." && pwd)"
PY="$ROOT/.venv/bin/python"
EARN_DAYS="${EARN_DAYS:-130}"     # covers back to ~2026-04-14 (oldest gap 2026-04-21)
STALL_DAYS="${STALL_DAYS:-40}"    # covers the 2026-07-29 stall of the two inboxes
FEDEX_MONTHS_DEFAULT="2026-04,2026-05,2026-06,2026-07,2026-08"

cd "$AUTO" || exit 1
say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
run() { echo "\$ $*"; "$@"; local rc=$?; [ $rc -ne 0 ] && echo "   (exit $rc - continuing)"; return 0; }

say "STEP 1/4  Targeted Gmail backfills (no Chrome needed)"
run "$PY" -u scripts/fetch_account_earnings.py abecomllc26 "$EARN_DAYS"
for acct in wrenchlyllc salesmercentile1; do
  run "$PY" -u scripts/fetch_account_zoro.py     "$acct" "$STALL_DAYS"
  run "$PY" -u scripts/fetch_account_tracking.py "$acct" "$STALL_DAYS"
done

say "STEP 2/4  Push gmail_archive.db -> unified archive_* tables"
run "$PY" -u scripts/sync_gmail_archive.py

say "STEP 3/4  Re-enrich + recompute derived fields for both accounts"
# Gap-fills from the archives (never overwrites a present value) and recomputes
# placement_speed / zoro_ship_speed / profit_dollars / labels via closing.py itself.
BUSY="$(pgrep -fl 'engine/orders.py' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$BUSY" != "0" ] && [ "${1:-}" != "--force-busy" ] && [ "${2:-}" != "--force-busy" ]; then
  echo "⚠️  $BUSY order placer(s) are RUNNING."
  echo "    Step 3 holds the SQLite writer lock for 1-3 minutes; a placement that"
  echo "    lands inside that window is bought but NOT recorded (53 such losses"
  echo "    are already in data/unified_db_write_failures.jsonl)."
  echo "    -> Stop the placers, then re-run this script (steps 1-2 are already done"
  echo "       and are idempotent), or re-run with --force-busy to override."
else
  SNAP="$AUTO/data/automations_unified.db.bak_$(date +%Y%m%d_%H%M)"
  echo "\$ snapshot -> $SNAP"
  "$PY" - "$SNAP" <<'PYEOF' || echo "   (snapshot failed - continuing)"
import sqlite3, sys
src = sqlite3.connect("/Users/kamranbaloch/Downloads/Dropshipping/Development_Unit/Automations/data/automations_unified.db")
dst = sqlite3.connect(sys.argv[1])
src.backup(dst); dst.close(); src.close()
print("   snapshot ok")
PYEOF
  run "$PY" -u scripts/reconcile_and_reenrich.py HOANK  --go
  run "$PY" -u scripts/reconcile_and_reenrich.py Hathoro --go
fi

if [ "${1:-}" = "--fedex" ]; then
  say "STEP 4/4  FedEx delivery_status backfill (launches its own Chrome)"
  for acct in HOANK Hathoro; do
    echo "\$ FEDEX_ACCOUNT=$acct FEDEX_MONTHS=${FEDEX_MONTHS:-$FEDEX_MONTHS_DEFAULT} $PY -u scripts/fedex_recheck.py"
    FEDEX_ACCOUNT="$acct" \
    FEDEX_MONTHS="${FEDEX_MONTHS:-$FEDEX_MONTHS_DEFAULT}" \
    FEDEX_PORT_BASE="$([ "$acct" = "HOANK" ] && echo 9341 || echo 9351)" \
      "$PY" -u scripts/fedex_recheck.py || echo "   (fedex $acct exited non-zero - continuing)"
  done
else
  say "STEP 4/4  SKIPPED (delivery_status) - re-run with --fedex when you want it"
fi

say "REMAINING GAPS"
"$PY" - <<'PYEOF'
import sqlite3, os
AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if '__file__' in dir() else None
DB = "/Users/kamranbaloch/Downloads/Dropshipping/Development_Unit/Automations/data/automations_unified.db"
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
fields = ["earnings","shipped_email_time","wb_order_id","tracking_ids",
          "delivery_status","profit_dollars","placement_speed","zoro_ship_speed"]
print(f"{'field':<22}{'HOANK':>10}{'Hathoro':>10}")
for f in fields:
    row = []
    for acct in ("HOANK","Hathoro"):
        n = db.execute(f"""SELECT COUNT(*) FROM closing_orders
                           WHERE account=? AND order_status='Shipped'
                             AND ({f} IS NULL OR {f}='')""", (acct,)).fetchone()[0]
        row.append(n)
    print(f"{f:<22}{row[0]:>10}{row[1]:>10}")
PYEOF
echo
echo "Done. Compare the counts above with the numbers in data/missing_report_*.txt."
