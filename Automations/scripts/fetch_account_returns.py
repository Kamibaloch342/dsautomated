#!/usr/bin/env python3
"""
fetch_account_returns.py — targeted backfill of ONE inbox's returns/refunds into
gmail_archive.db (then run sync_gmail_archive.py to push into archive_returns).

Much lighter + more robust than `build_gmail_archive.py --force` (which re-fetches
earnings/zoro/tracking/messages too and timed out). Returns-only, single account,
with retry on transient network timeouts.

Usage:
  ../.venv/bin/python scripts/fetch_account_returns.py abecomllc26 40
  (account defaults to abecomllc26, days defaults to 40)
"""
import os, sys, time

_AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FA = os.path.join(_AUTO, "gmail_fetch")   # fetch code lives here now (old sibling "Final Automation" was deleted)
sys.path.insert(0, _FA)

acct = sys.argv[1] if len(sys.argv) > 1 else "abecomllc26"
days = int(sys.argv[2]) if len(sys.argv) > 2 else 40

from utils import get_gmail_service
from gmail_archive import sync_returns_refunds

DB = os.path.join(_AUTO, "data", "gmail_archive.db")
print(f"Fetching returns/refunds for {acct} — last {days} days → {DB}")

svc = get_gmail_service(acct)
if not svc:
    print(f"❌ no Gmail service for {acct} (token missing/invalid)")
    sys.exit(1)

for attempt in range(1, 4):
    try:
        n = sync_returns_refunds({acct: svc}, DB, days_back=days, max_total=0)
        print(f"✅ fetched {n} return/refund rows from {acct}")
        break
    except Exception as e:
        print(f"⚠️ attempt {attempt}/3 failed: {e}")
        if attempt < 3:
            time.sleep(8)
        else:
            print("❌ gave up — network timing out; try again in a moment.")
            sys.exit(1)
