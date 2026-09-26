#!/usr/bin/env python3
"""
rotate_logs.py — keep the local log files bounded (26 Sep 2026).

Nothing ever rotated them: data/console_log.txt was 1.24 GB and data/raw_logs.txt 2.06 GB
on 26 Sep 2026, every per-process log under data/logs/ was kept forever, and each closing
run left a 20–40 MB JSONL under Final_Testing_Logs/runs/.

Policy (all sizes/ages overridable by environment variable):
  • console_log.txt / raw_logs.txt (the launcher opens them per write): when larger than
    ROTATE_MB (200) they are RENAMED to <name>.<YYYYmmdd-HHMM>; rotated generations older
    than KEEP_DAYS (15) are deleted.
  • .autosync_output.log / .zoro_watch.log (held open by a running subprocess): when larger
    than ROTATE_SMALL_MB (50) the content is copied to a dated file and the live file is
    truncated in place (append-mode writers continue cleanly).
  • data/logs/*.log, Final_Testing_Logs/runs/*.jsonl and rotated generations older than
    KEEP_DAYS are deleted; engine/_dl_*/*.txt (tracker report downloads) older than 2 days
    and all but the newest 3 reports per account under data/reports/ are deleted.

Run by hand or from auto_sync_10min.py (hourly, cheap when nothing is due):
    python scripts/rotate_logs.py            # do it
    python scripts/rotate_logs.py --dry-run  # only report
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
DATA = os.path.join(AUTO, "data")

ROTATE_MB = float(os.environ.get("ROTATE_MB", "200"))
ROTATE_SMALL_MB = float(os.environ.get("ROTATE_SMALL_MB", "50"))
KEEP_DAYS = int(os.environ.get("ROTATE_KEEP_DAYS", "15"))
DL_KEEP_DAYS = int(os.environ.get("ROTATE_DL_KEEP_DAYS", "2"))
REPORTS_KEEP = int(os.environ.get("ROTATE_REPORTS_KEEP", "3"))

RENAME_FILES = [os.path.join(DATA, "console_log.txt"), os.path.join(DATA, "raw_logs.txt")]
TRUNCATE_FILES = [os.path.join(AUTO, ".autosync_output.log"), os.path.join(AUTO, ".zoro_watch.log")]
AGE_GLOBS = [os.path.join(DATA, "logs", "*.log"),
             os.path.join(AUTO, "Final_Testing_Logs", "runs", "*.jsonl")]


def _mb(p):
    try:
        return os.path.getsize(p) / 1048576
    except OSError:
        return 0.0


def _stamp():
    return datetime.now().strftime("%Y%m%d-%H%M")


def rotate(verbose=True, dry_run=False):
    out = {"rotated": [], "truncated": [], "deleted": 0, "freed_mb": 0.0}

    def say(msg):
        if verbose:
            print(f"[rotate_logs] {msg}", flush=True)

    # 1) rename-rotate the per-write log files
    for p in RENAME_FILES:
        size = _mb(p)
        if size >= ROTATE_MB:
            target = f"{p}.{_stamp()}"
            say(f"rotate {os.path.basename(p)} ({size:.0f} MB) → {os.path.basename(target)}")
            if not dry_run:
                try:
                    os.replace(p, target)
                    out["rotated"].append(target)
                except OSError as e:
                    say(f"  ⚠️ {e}")

    # 2) copy-truncate the files a running process keeps open
    for p in TRUNCATE_FILES:
        size = _mb(p)
        if size >= ROTATE_SMALL_MB:
            target = f"{p}.{_stamp()}"
            say(f"copy+truncate {os.path.basename(p)} ({size:.0f} MB) → {os.path.basename(target)}")
            if not dry_run:
                try:
                    shutil.copyfile(p, target)
                    with open(p, "r+b") as f:
                        f.truncate(0)
                    out["truncated"].append(target)
                except OSError as e:
                    say(f"  ⚠️ {e}")

    # 3) age-based deletion: rotated generations, per-process logs, run JSONLs
    cutoff = time.time() - KEEP_DAYS * 86400
    candidates = []
    for base in RENAME_FILES + TRUNCATE_FILES:
        candidates.extend(glob.glob(base + ".*"))
    for g in AGE_GLOBS:
        candidates.extend(glob.glob(g))
    for p in candidates:
        try:
            if os.path.getmtime(p) < cutoff:
                size = _mb(p)
                say(f"delete {os.path.relpath(p, AUTO)} ({size:.1f} MB, older than {KEEP_DAYS} d)")
                if not dry_run:
                    os.remove(p)
                out["deleted"] += 1
                out["freed_mb"] += size
        except OSError:
            pass

    # 4) tracker report downloads older than DL_KEEP_DAYS
    dl_cutoff = time.time() - DL_KEEP_DAYS * 86400
    for p in glob.glob(os.path.join(AUTO, "engine", "_dl_*", "*.txt")):
        try:
            if os.path.getmtime(p) < dl_cutoff:
                size = _mb(p)
                if not dry_run:
                    os.remove(p)
                out["deleted"] += 1
                out["freed_mb"] += size
        except OSError:
            pass

    # 5) closing report downloads: keep the newest REPORTS_KEEP per account
    for acct_dir in glob.glob(os.path.join(DATA, "reports", "*")):
        if not os.path.isdir(acct_dir):
            continue
        files = sorted(glob.glob(os.path.join(acct_dir, "*.txt")), key=os.path.getmtime, reverse=True)
        for p in files[REPORTS_KEEP:]:
            try:
                size = _mb(p)
                if not dry_run:
                    os.remove(p)
                out["deleted"] += 1
                out["freed_mb"] += size
            except OSError:
                pass

    if out["rotated"] or out["truncated"] or out["deleted"]:
        say(f"done: {len(out['rotated'])} rotated, {len(out['truncated'])} truncated, "
            f"{out['deleted']} deleted, {out['freed_mb']:.0f} MB freed" + (" [dry run]" if dry_run else ""))
    elif verbose:
        say("nothing due")
    return out


if __name__ == "__main__":
    rotate(verbose=True, dry_run="--dry-run" in sys.argv)
