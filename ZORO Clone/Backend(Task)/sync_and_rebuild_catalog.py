#!/usr/bin/env python3
"""
Master Zoro Catalog Sync & Enrichment Pipeline for EKUIPT
──────────────────────────────────────────────────────────
Automates the full 24-hour catalog lifecycle:
  1. Amazon S3 Check & Sync: Compares timestamps with S3 bucket (zoro-preferred-seller-096)
     and downloads fresh TSV feeds.
  2. TSV Database Compilation: Converts raw TSVs into combined_reports.db.
  3. High-Speed Enrichment Extractor: Scrapes rich categories, specs, galleries,
     and variant families into catalog_enrichment.db.
  4. Search Index Compilation: Builds catalog_index.db with SQLite FTS5.
  5. Deployment Packager: Compresses and splits catalog for Docker / GitHub deployment.

Usage:
  python sync_and_rebuild_catalog.py           # Full sync & rebuild
  python sync_and_rebuild_catalog.py --skip-s3 # Skip S3 download if TSVs are already present
  python sync_and_rebuild_catalog.py --extract-only --concurrency 50
"""

import os
import sys
import time
import subprocess
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
TSV_DIR = os.path.abspath(os.path.join(REPO_ROOT, "..", "Automations", "TSV Files"))
PYTHON_BIN = sys.executable

def log(step, msg):
    print(f"\n[{time.strftime('%H:%M:%S')}] 🚀 [STEP {step}] {msg}", flush=True)

def run_command(cmd, cwd=BASE_DIR):
    print(f"  ▶ Executing: {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    res = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd)
    if res.returncode != 0:
        print(f"❌ Command failed with exit code {res.returncode}")
        return False
    return True

def main():
    parser = argparse.ArgumentParser(description="Sync, enrich, and build Zoro Catalog for EKUIPT.")
    parser.add_argument("--skip-s3", action="store_true", help="Skip checking/downloading from Amazon S3.")
    parser.add_argument("--skip-extract", action="store_true", help="Skip live product web extraction.")
    parser.add_argument("--concurrency", type=int, default=40, help="Async scraping concurrency (default: 40).")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of items to enrich.")
    args = parser.parse_args()

    t_start = time.time()
    print("=" * 70)
    print(" EKUIPT — FULL ZORO CATALOG AUTOMATED SYNC & ENRICHMENT PIPELINE")
    print("=" * 70)

    # 1. Amazon S3 Check & Sync
    if not args.skip_s3:
        log("1/5", "Checking Amazon S3 bucket (zoro-preferred-seller-096)...")
        auto_download_py = os.path.join(TSV_DIR, "auto_download.py")
        if os.path.exists(auto_download_py):
            tsv_venv_py = os.path.join(TSV_DIR, ".venv", "bin", "python")
            py_exec = tsv_venv_py if os.path.exists(tsv_venv_py) else PYTHON_BIN
            success = run_command([py_exec, auto_download_py], cwd=TSV_DIR)
            if not success:
                print("⚠️ S3 sync encountered an issue, proceeding with existing local TSVs...")
        else:
            print(f"⚠️ S3 auto_download.py not found at {auto_download_py}")
    else:
        log("1/5", "Skipping Amazon S3 sync (--skip-s3 requested).")

    # 2. Build TSV SQLite Database
    log("2/5", "Compiling TSV datafeeds into combined_reports.db...")
    build_db_py = os.path.join(TSV_DIR, "build_zoro_db.py")
    if os.path.exists(build_db_py):
        tsv_venv_py = os.path.join(TSV_DIR, ".venv", "bin", "python")
        py_exec = tsv_venv_py if os.path.exists(tsv_venv_py) else PYTHON_BIN
        run_command([py_exec, build_db_py], cwd=TSV_DIR)
    else:
        print(f"⚠️ build_zoro_db.py not found at {build_db_py}")

    # 3. Extract Rich Catalog (Pinia Categories, Multi-Images, Specifications, Variants)
    if not args.skip_extract:
        log("3/5", f"Extracting rich product details & images (Concurrency: {args.concurrency})...")
        extract_py = os.path.join(BASE_DIR, "extract_rich_catalog.py")
        cmd = [PYTHON_BIN, extract_py, "--concurrency", str(args.concurrency), "--fast-ship-only"]
        if args.limit:
            cmd.extend(["--limit", str(args.limit)])
        run_command(cmd, cwd=BASE_DIR)
    else:
        log("3/5", "Skipping rich catalog web extraction (--skip-extract requested).")

    # 4. Build SQLite FTS5 Search Index
    log("4/5", "Building catalog_index.db with FTS5 search & image verification...")
    build_index_py = os.path.join(BASE_DIR, "build_search_index.py")
    run_command([PYTHON_BIN, build_index_py], cwd=BASE_DIR)

    # 5. Compress and Split for Production Deployment
    log("5/5", "Packaging catalog_enrichment.db for production deployment...")
    enrich_db = os.path.join(BASE_DIR, "catalog_enrichment.db")
    if os.path.exists(enrich_db):
        run_command(
            f"gzip -c -9 '{enrich_db}' | split -b 45M - '{enrich_db}.gz.part_'",
            cwd=BASE_DIR
        )
        print("  ✓ Split archives created for GitHub deployment.")

    total_time = time.time() - t_start
    print("\n" + "=" * 70)
    print(f"🎉 Pipeline Complete in {total_time/60:.1f} minutes!")
    print("=" * 70)

if __name__ == "__main__":
    main()
