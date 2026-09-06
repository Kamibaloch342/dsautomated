#!/usr/bin/env python3
"""
refresh_zoro_and_listings.py — the daily growth-side refresh.

One command, three steps, safe to run on a schedule:
  1. auto_download.py  — pull any Zoro TSV that changed on S3 (skips unchanged)
  2. build_zoro_db.py  — rebuild combined_reports.db from the TSVs
  3. engine/listings.py — recompute listing health into automations_unified.db

Step 2 only runs when step 1 actually downloaded something, so a normal day
where Zoro hasn't republished costs one cheap S3 listing call instead of a
20-minute rebuild of 2M rows.

Nothing here edits Amazon. The output is the reviewed action lists.

Usage:
    python scripts/refresh_zoro_and_listings.py            # normal daily run
    python scripts/refresh_zoro_and_listings.py --force-db # rebuild DB regardless
    python scripts/refresh_zoro_and_listings.py --skip-download
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
import subprocess
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # Automations/
TSV_DIR = os.path.join(SCRIPT_DIR, "TSV Files")
META = os.path.join(TSV_DIR, "download_metadata.json")
LOG = os.path.join(SCRIPT_DIR, "data", "zoro_refresh.log")
PY = sys.executable


def _python_with_boto3():
    """Interpreter to run the S3 downloader with.

    The venv doesn't necessarily have boto3 (it's only needed by the feed
    downloader, not by the rest of the stack), while the system python often
    does. Pick whichever can actually import it rather than failing the whole
    refresh over one missing dependency.
    """
    for cand in (sys.executable, "python3", "/usr/bin/python3"):
        try:
            r = subprocess.run([cand, "-c", "import boto3"],
                               capture_output=True, timeout=30)
            if r.returncode == 0:
                return cand
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def log(msg):
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _meta_snapshot():
    try:
        with open(META, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}


def run_step(name, cmd, cwd=None, timeout=7200):
    log(f"▶ {name}")
    try:
        p = subprocess.run(cmd, cwd=cwd, timeout=timeout,
                           capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        log(f"✖ {name}: timed out after {timeout}s")
        return False, ""
    out = (p.stdout or "") + (p.stderr or "")
    tail = "\n".join(l for l in out.strip().splitlines()[-6:] if l.strip())
    if p.returncode != 0:
        log(f"✖ {name}: exit {p.returncode}\n{tail}")
        return False, out
    if tail:
        log(f"  {tail}")
    log(f"✔ {name}")
    return True, out


def main():
    ap = argparse.ArgumentParser(description="Daily Zoro feed + listing refresh")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--force-db", action="store_true",
                    help="Rebuild the catalog DB even if no TSV changed")
    ap.add_argument("--days", type=int, help="Redundancy window override")
    ap.add_argument("--retries", type=int, default=4,
                    help="Download attempts (the S3 feed is briefly empty while "
                         "Zoro republishes it each day)")
    ap.add_argument("--retry-wait", type=int, default=180,
                    help="Base seconds between download attempts (grows per attempt)")
    a = ap.parse_args()

    log("=" * 60)
    log("Zoro + listings refresh starting")

    changed = a.force_db
    if not a.skip_download:
        before = _meta_snapshot()
        dl_py = _python_with_boto3()
        if not dl_py:
            log("✖ No interpreter with boto3 — skipping download "
                "(fix: <venv>/bin/python -m pip install boto3)")
            ok = False
        else:
            # Zoro publishes by DELETING the objects and re-uploading, so the
            # bucket is genuinely empty for a window each day, and a download
            # already in flight can die with NoSuchKey when the object is
            # replaced underneath it. Both are transient — retry with backoff
            # rather than treating the day's refresh as failed.
            ok = False
            for attempt in range(1, a.retries + 1):
                ok, out = run_step(
                    f"Download Zoro TSVs from S3 (attempt {attempt}/{a.retries})",
                    [dl_py, "auto_download.py"], cwd=TSV_DIR)
                if ok:
                    break
                transient = ("empty" in out.lower() or "NoSuchKey" in out
                             or "No files found" in out)
                if attempt < a.retries:
                    wait = a.retry_wait * attempt
                    log(f"  {'feed mid-republish' if transient else 'download error'}"
                        f" — retrying in {wait}s")
                    time.sleep(wait)
        after = _meta_snapshot()
        if after != before:
            newly = [k for k, v in after.items() if before.get(k) != v]
            log(f"  {len(newly)} feed file(s) updated: {', '.join(newly)}")
            changed = True
        elif ok:
            log("  no feed changes on S3")
        else:
            # Unchanged metadata AFTER a failed download is not "nothing to do"
            # — it means the feed on S3 may well be newer than what we hold.
            # Say so loudly; the listing engine's feed-age guard is what stops
            # stale stock data from driving live listing changes.
            log("✖ DOWNLOAD FAILED and no feed file was updated — the catalog is "
                "still whatever was last on disk. Stock actions stay withheld "
                "until this succeeds.")

    if changed:
        ok, _ = run_step("Rebuild Zoro catalog DB",
                         [PY, "build_zoro_db.py", "--force"], cwd=TSV_DIR)
        if not ok:
            log("  catalog rebuild failed — listing stock actions will be withheld")
    else:
        log("↷ Catalog DB rebuild skipped (feed unchanged)")

    cmd = [PY, os.path.join(SCRIPT_DIR, "engine", "listings.py")]
    if a.days:
        cmd += ["--days", str(a.days)]
    run_step("Recompute listing health", cmd, cwd=SCRIPT_DIR)

    log("Refresh complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
