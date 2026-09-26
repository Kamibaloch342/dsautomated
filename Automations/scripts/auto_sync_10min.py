#!/usr/bin/env python3
"""Every-10-min background refresh (run by the launcher).

  1) gmail_archive.db  → unified archive_* (continuous refund/return capture)
  2) order_log.txt     → order_placer_orders (new script placements)
  3) order_placer_orders → closing_orders   (Pending pre-fill for the dashboard)

Each step is isolated so one failure can't abort the others. sync_gmail_archive
calls sys.exit on a missing source DB, so it's caught here (running in-process is
fine because this whole file is launched as its own subprocess by the launcher).
"""
import sys, os, subprocess, json, datetime
AUTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(AUTO)
# Gmail fetcher moved from the old "Final Automation" folder into
# Automations/gmail_fetch (daily_bulk.py made the same migration; this path
# was left behind and made every hourly gmail_fetch step fail after the
# machine migration — no new shipping/refund emails were being captured).
FA = os.path.join(AUTO, "gmail_fetch")
PYBIN = os.path.join(ROOT, ".venv", "bin", "python")
sys.path.insert(0, AUTO)
# Same freshness stamp daily_bulk.py reads: a successful fetch+sync here means the
# closing chain can SKIP its own full Gmail re-fetch (FRESH_MINS window).
FETCH_STATE = os.path.join(AUTO, "data", "gmail_fetch_state.json")


def step(name, fn):
    try:
        fn()
        print(f"[auto-sync] {name}: ok", flush=True)
        return True
    except SystemExit as e:
        print(f"[auto-sync] {name}: skipped (exit {e.code})", flush=True)
    except Exception as e:
        print(f"[auto-sync] {name}: error {e}", flush=True)
    return False


def main():
    # 0) Gmail → gmail_archive.db: fetch the last few days so NEW refund/return
    #    emails get captured continuously. Bounded + --force (incremental refresh).
    #    Serialized against daily_bulk via the shared gmail_fetch lock — if a
    #    closing-run fetch holds it, skip this cycle instead of double-fetching.
    from utils.accounts_config import acquire_lock, release_lock
    def fetch():
        got, holder = acquire_lock("gmail_fetch")
        if not got:
            raise SystemExit(f"fetch lock held by pid {holder}")
        try:
            py = PYBIN if os.path.exists(PYBIN) else sys.executable
            r = subprocess.run([py, "build_gmail_archive.py", "--days", "2", "--force"],
                               cwd=FA, capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout or "")[-200:])
        finally:
            release_lock("gmail_fetch")
    ok_fetch = step("gmail_fetch", fetch)
    def g():
        import scripts.sync_gmail_archive as s
        s.main()
    ok_sync = step("gmail_archive", g)
    if ok_fetch and ok_sync:
        try:
            with open(FETCH_STATE, "w") as f:
                json.dump({"last_fetch": datetime.datetime.now().isoformat(timespec="seconds")}, f)
        except Exception as e:
            print(f"[auto-sync] ⚠️ could not write fetch state: {e}", flush=True)

    # 1.5) Two-way Google-Sheets placement sync: upload our new placements, PULL placements
    #      made on other machines into the local log (+duplicate lock), trim sheet to 5 days.
    #      Runs BEFORE placer_log so pulled rows land in order_placer_orders this same pass.
    def sh():
        from utils.sheet_sync import sync_bidirectional, is_order_log_sync_enabled
        if is_order_log_sync_enabled():
            sync_bidirectional(days=5)
        else:
            print("[auto-sync] sheet_log: skipped (Order_Log sync disabled)", flush=True)
    step("sheet_log", sh)

    def p():
        from scripts.sync_order_placer_logs import sync_incremental
        sync_incremental(verbose=False)
    step("placer_log", p)

    def c():
        from scripts.sync_placer_to_closing import sync
        sync(verbose=False)
    step("placer_to_closing", c)

    def ins():
        from scripts.send_insights_alerts import dispatch_account_insights
        dispatch_account_insights(account="HOANK", days=0, limit=10)
        dispatch_account_insights(account="Hathoro", days=0, limit=10)
    step("whatsapp_insights", ins)

    # 3) keep the log files bounded (rename/copy-truncate when large, delete after 15 days)
    def rot():
        from scripts.rotate_logs import rotate
        r = rotate(verbose=False)
        if r["rotated"] or r["truncated"] or r["deleted"]:
            print(f"[auto-sync] rotate_logs: {len(r['rotated'])} rotated, {len(r['truncated'])} truncated, "
                  f"{r['deleted']} deleted, {r['freed_mb']:.0f} MB freed", flush=True)
    step("rotate_logs", rot)


if __name__ == "__main__":
    main()
