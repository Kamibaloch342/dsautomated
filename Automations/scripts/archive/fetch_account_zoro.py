"""
fetch_account_zoro.py — targeted backfill of ONE inbox's Zoro "has been received"
(order COST/receipt) emails into gmail_archive.db, then run sync_gmail_archive.py
to push into unified archive_zoro (which gives zoro_order_total/subtotal/tax/coupon).

Companion to fetch_account_tracking.py. The same inboxes that stalled for tracking
(abecomllc26 ~5/26, salesmercentile ~5/13, wrenchlyllc ~6/6) also stalled for the
cost receipts → split orders can't sum all legs → profit overstated. This recovers
them, date-bounded + single account (light on Gmail rate limits).

Usage:
  ../.venv/bin/python scripts/fetch_account_zoro.py abecomllc26 40
  ../.venv/bin/python scripts/fetch_account_zoro.py salesmercentile 50
  ../.venv/bin/python scripts/fetch_account_zoro.py wrenchlyllc 35
"""
import os, sys, time

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FA = os.path.join(_AUTO, "gmail_fetch")   # fetch code lives here now (old sibling "Final Automation" was deleted)
sys.path.insert(0, _FA)

acct = sys.argv[1] if len(sys.argv) > 1 else "abecomllc26"
days = int(sys.argv[2]) if len(sys.argv) > 2 else 40

from utils import get_gmail_service
from gmail_archive import sync_zoro

DB = os.path.join(_AUTO, "data", "gmail_archive.db")
print(f"Fetching ZORO cost-receipt emails for {acct} — last {days} days → {DB}")

svc = get_gmail_service(acct)
if not svc:
    print(f"❌ no Gmail service for {acct} (token missing/invalid)")
    sys.exit(1)

for attempt in range(1, 4):
    try:
        n = sync_zoro({acct: svc}, DB, days_back=days, max_total=0)
        print(f"✅ fetched {n} zoro receipt rows from {acct}")
        break
    except Exception as e:
        print(f"⚠️ attempt {attempt}/3 failed: {e}")
        if attempt < 3:
            time.sleep(10)
        else:
            sys.exit(1)

print("\nNext: ../.venv/bin/python scripts/sync_gmail_archive.py   (push into unified archive_zoro)")
