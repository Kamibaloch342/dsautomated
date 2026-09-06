#!/usr/bin/env python3
"""
build_gmail_archive.py — One-time bulk build of the Gmail archive database.

Usage:
    python3 build_gmail_archive.py --days 150
    python3 build_gmail_archive.py --days 7    # incremental catch-up
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    EARNINGS_GMAIL_ACCOUNT,
    EARNINGS_GMAIL_ACCOUNTS,
    ZORO_ORDER_GMAIL_ACCOUNTS,
    ALL_GMAIL_ACCOUNTS,
    GMAIL_PREFETCH_DAYS,
    GMAIL_ARCHIVE_DB_PATH,
)
from utils import get_gmail_service
from gmail_archive import (
    init_archive,
    sync_earnings,
    sync_zoro,
    sync_returns_refunds,
    sync_tracking,
    sync_messages,
    get_record_count,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
)
logger = logging.getLogger("build_archive")


def _auth_services():
    """Auth all Gmail services and return them."""
    logger.info("[Auth] All accounts for returns/tracking: %s", ALL_GMAIL_ACCOUNTS)
    all_services = {name: get_gmail_service(name) for name in ALL_GMAIL_ACCOUNTS}

    # ALL earnings inboxes (the old singular EARNINGS_GMAIL_ACCOUNT fetched only
    # qurtdis/HOANK — Hathoro's abecomllc26 'Sold, ship now' emails were never
    # archived by the recurring pipeline). Reuse already-authed services.
    logger.info("[Auth] Earnings accounts: %s", EARNINGS_GMAIL_ACCOUNTS or [EARNINGS_GMAIL_ACCOUNT])
    earnings_services = {name: (all_services.get(name) or get_gmail_service(name))
                         for name in (EARNINGS_GMAIL_ACCOUNTS or [EARNINGS_GMAIL_ACCOUNT])}

    logger.info("[Auth] Zoro accounts: %s", ZORO_ORDER_GMAIL_ACCOUNTS)
    zoro_services = {name: (all_services.get(name) or get_gmail_service(name))
                     for name in ZORO_ORDER_GMAIL_ACCOUNTS}

    return earnings_services, zoro_services, all_services


def main():
    parser = argparse.ArgumentParser(description="Build Gmail archive database")
    parser.add_argument(
        "--days",
        type=int,
        default=GMAIL_PREFETCH_DAYS,
        help=f"Days back to fetch (default: {GMAIL_PREFETCH_DAYS})",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(GMAIL_ARCHIVE_DB_PATH),
        help="Archive DB path",
    )
    parser.add_argument(
        "--skip-tracking",
        action="store_true",
        help="Skip tracking sync (slow; run separately if needed)",
    )
    parser.add_argument(
        "--start-from-step",
        type=int,
        default=1,
        help="Start from step N (1-5) to resume an interrupted build",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-fetch even if step already has data in archive",
    )
    args = parser.parse_args()

    db_path = args.db
    days_back = args.days

    logger.info("=" * 60)
    logger.info("Gmail Archive Build — %d days", days_back)
    logger.info("DB: %s", db_path)
    logger.info("=" * 60)

    init_archive(db_path)
    earnings_services, zoro_services, all_services = _auth_services()

    start_step = max(1, min(5, args.start_from_step))

    def _should_skip(step_num: int, table: str) -> bool:
        if step_num < start_step:
            return True
        if args.force:
            return False
        cnt = get_record_count(db_path, table)
        if cnt > 0:
            logger.info("[Build] Step %d/%d: %s already has %d records — skipping (use --force to override)",
                        step_num, 6, table, cnt)
            return True
        return False

    # 1. Earnings
    if not _should_skip(1, "earnings"):
        logger.info("[Build] ══════════════════════════════════════════════════════════")
        logger.info("[Build] Step 1/6: Earnings emails")
        logger.info("[Build] ══════════════════════════════════════════════════════════")
        n = 0
        for _acct, _svc in earnings_services.items():
            n += sync_earnings(_svc, db_path, _acct, days_back=days_back, max_total=0)
        logger.info("[Build] ✓ Earnings complete — %d records (total %d)", n, get_record_count(db_path, "earnings"))

    # 2. Zoro
    if not _should_skip(2, "zoro"):
        logger.info("[Build] ══════════════════════════════════════════════════════════")
        logger.info("[Build] Step 2/6: Zoro emails")
        logger.info("[Build] ══════════════════════════════════════════════════════════")
        n = sync_zoro(zoro_services, db_path, days_back=days_back, max_total=0)
        logger.info("[Build] ✓ Zoro complete — %d records (total %d)", n, get_record_count(db_path, "zoro"))

    # 3. Returns / Refunds
    if not _should_skip(3, "returns"):
        logger.info("[Build] ══════════════════════════════════════════════════════════")
        logger.info("[Build] Step 3/6: Returns/Refunds")
        logger.info("[Build] ══════════════════════════════════════════════════════════")
        n = sync_returns_refunds(all_services, db_path, days_back=days_back, max_total=0)
        logger.info("[Build] ✓ Returns/Refunds complete — %d records (total %d)", n, get_record_count(db_path, "returns"))

    # 4. Messages (all buyer↔seller communication)
    if not _should_skip(4, "messages"):
        logger.info("[Build] ══════════════════════════════════════════════════════════")
        logger.info("[Build] Step 4/5: Messages")
        logger.info("[Build] ══════════════════════════════════════════════════════════")
        n = sync_messages(all_services, db_path, days_back=days_back, max_total=0)
        logger.info("[Build] ✓ Messages complete — %d records", n)

    # 5. Tracking (slow for large days_back)
    if not args.skip_tracking:
        if not _should_skip(5, "tracking"):
            logger.info("[Build] ══════════════════════════════════════════════════════════")
            logger.info("[Build] Step 5/5: Tracking emails (this may take a while)")
            logger.info("[Build] ══════════════════════════════════════════════════════════")
            n = sync_tracking(zoro_services, db_path, days_back=days_back, max_total=0)
            logger.info("[Build] ✓ Tracking complete — %d records (total %d)", n, get_record_count(db_path, "tracking"))
    else:
        logger.info("[Build] Step 5/5: Tracking skipped (--skip-tracking)")

    logger.info("=" * 60)
    logger.info("Archive build complete!")
    logger.info("Totals: earnings=%d, zoro=%d, returns=%d, messages=%d, cancellations=%d, tracking=%d",
                get_record_count(db_path, "earnings"),
                get_record_count(db_path, "zoro"),
                get_record_count(db_path, "returns"),
                get_record_count(db_path, "messages"),
                get_record_count(db_path, "cancellations"),
                get_record_count(db_path, "tracking"))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
