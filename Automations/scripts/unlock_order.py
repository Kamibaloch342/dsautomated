#!/usr/bin/env python3
"""
unlock_order.py — manually release an Amazon order that the placement lock is holding.

Use this ONLY after you've checked the Zoro account and confirmed the order is safe to
(re)place — i.e. it was NOT actually charged / has no WB, so it's not a duplicate. The lock
holds orders in FAILED_KEPT (unknown failure, money may have been spent) or CONFIRMED (already
placed) so they can't be auto-rebought; this releases that hold so the placer can try again.

Usage:
    ../.venv/bin/python scripts/unlock_order.py 114-5669294-8888259 [more ids...]
    ../.venv/bin/python scripts/unlock_order.py --status 114-5669294-8888259   # just show state

IMPORTANT: a RUNNING placer keeps the lock in memory. After unlocking, RESTART the placer
(or at least that profile) so it picks up the change — otherwise the running process will keep
blocking the order until it restarts.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import placement_lock as plock  # noqa: E402


def _show(aid):
    import sqlite3
    r = sqlite3.connect(plock.DB_PATH).execute(
        "SELECT amazon_order_id,status,wb_order_id,profile,claimed_at,reconciled_at,note "
        "FROM placer_claims WHERE amazon_order_id=?", (aid,)).fetchone()
    print(f"  {aid}: {r if r else '(not in placer_claims — not locked)'}")


def main():
    args = [a for a in sys.argv[1:] if a]
    if not args:
        print(__doc__)
        return
    status_only = "--status" in args
    ids = [a for a in args if not a.startswith("--")]
    if not ids:
        print("No order id given."); return

    if status_only:
        print("Current lock state:")
        for aid in ids:
            _show(aid)
        return

    for aid in ids:
        print(f"\nBefore:")
        _show(aid)
        plock.override(aid, note="manually unlocked via unlock_order.py (verified safe to re-place)")
        print(f"After:")
        _show(aid)
    print("\n✅ Released (status → RELEASED). The placer can now retry these on its next cycle.")
    print("⚠️  If the placer is RUNNING, restart it (or that profile) so it drops the in-memory hold.")


if __name__ == "__main__":
    main()
