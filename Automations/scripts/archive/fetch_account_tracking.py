"""
fetch_account_tracking.py — targeted backfill of ONE inbox's Zoro "has shipped"
(tracking) emails into gmail_archive.db, then run sync_gmail_archive.py to push
them into unified archive_tracking (which gives tracking_ids + shipped_email_time).

Why: the every-10-min all-account refresh stalled some inboxes (abecomllc26 at
2026-05-26, salesmercentile at 2026-05-21), so late-May/June Hathoro shipped
emails were never archived → no shipped_email_time. This re-fetches just the
stalled inbox, date-bounded — far lighter on Gmail rate limits than --force.

Usage:
  ../.venv/bin/python scripts/fetch_account_tracking.py abecomllc26 40
  ../.venv/bin/python scripts/fetch_account_tracking.py salesmercentile 45
  (account defaults to abecomllc26, days defaults to 40)
"""
import os, sys, time

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FA = os.path.join(_AUTO, "gmail_fetch")   # fetch code lives here now (old sibling "Final Automation" was deleted)
sys.path.insert(0, _FA)

acct = sys.argv[1] if len(sys.argv) > 1 else "abecomllc26"
days = int(sys.argv[2]) if len(sys.argv) > 2 else 40

from utils import get_gmail_service
from gmail_archive import sync_tracking

DB = os.path.join(_AUTO, "data", "gmail_archive.db")
print(f"Fetching TRACKING (shipped) emails for {acct} — last {days} days → {DB}")

svc = get_gmail_service(acct)
if not svc:
    print(f"❌ no Gmail service for {acct} (token missing/invalid)")
    sys.exit(1)

for attempt in range(1, 4):
    try:
        n = sync_tracking({acct: svc}, DB, days_back=days, max_total=0)
        print(f"✅ fetched {n} tracking rows from {acct}")
        break
    except Exception as e:
        print(f"⚠️ attempt {attempt}/3 failed: {e}")
        if attempt < 3:
            time.sleep(10)
        else:
            sys.exit(1)

print("\nNext: ../.venv/bin/python scripts/sync_gmail_archive.py   (push into unified archive_tracking)")
