"""
fetch_account_earnings.py — targeted backfill of ONE inbox's Amazon "Sold, ship
now:" EARNINGS emails into gmail_archive.db, then run sync_gmail_archive.py to push
into unified archive_earnings (gives total_your_earnings/fees/shipping by amazon id).

Companion to fetch_account_zoro/tracking. sync_earnings takes a SINGLE service +
account_name (unlike the dict-based zoro/tracking syncs).

Usage:
  ../.venv/bin/python scripts/fetch_account_earnings.py abecomllc26 60
"""
import os, sys, time

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FA = os.path.join(_AUTO, "gmail_fetch")   # fetch code lives here now (old sibling "Final Automation" was deleted)
sys.path.insert(0, _FA)

acct = sys.argv[1] if len(sys.argv) > 1 else "abecomllc26"
days = int(sys.argv[2]) if len(sys.argv) > 2 else 60

from utils import get_gmail_service
from gmail_archive import sync_earnings

DB = os.path.join(_AUTO, "data", "gmail_archive.db")
print(f"Fetching EARNINGS ('Sold, ship now') for {acct} — last {days} days → {DB}")

svc = get_gmail_service(acct)
if not svc:
    print(f"❌ no Gmail service for {acct} (token missing/invalid)")
    sys.exit(1)

for attempt in range(1, 4):
    try:
        n = sync_earnings(svc, DB, acct, days_back=days, max_total=0)
        print(f"✅ fetched {n} earnings rows from {acct}")
        break
    except Exception as e:
        print(f"⚠️ attempt {attempt}/3 failed: {e}")
        if attempt < 3:
            time.sleep(10)
        else:
            sys.exit(1)

print("\nNext: ../.venv/bin/python scripts/sync_gmail_archive.py   (push into unified archive_earnings)")
