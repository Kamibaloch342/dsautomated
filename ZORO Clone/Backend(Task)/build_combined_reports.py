#!/usr/bin/env python3
"""
Fast, Zero-Dependency Zoro TSV to SQLite Catalog Compiler
─────────────────────────────────────────────────────────
Compiles raw zoro_datafeed_*.tsv files into combined_reports.db (table: zoro_data).
Peak memory bounded, uses chunked multi-row transactions with SQLite PRAGMAs.
"""

import os
import sys
import csv
import glob
import time
import sqlite3

TSV_DIR = "/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/Automations/TSV Files"
DB_PATH = os.path.join(TSV_DIR, "combined_reports.db")
TMP_PATH = DB_PATH + ".building"
BATCH_SIZE = 25000

PRICE_COLS = {"sales_price", "unit_price", "freight_surcharge"}

def parse_val(col_name, val):
    if not val:
        return None
    val = val.strip()
    if col_name in PRICE_COLS:
        try:
            return float(val.replace("$", "").replace(",", ""))
        except (ValueError, TypeError):
            return None
    return val

def build_combined_db():
    tsv_files = sorted([
        os.path.join(TSV_DIR, f)
        for f in os.listdir(TSV_DIR)
        if f.startswith("zoro_datafeed_") and f.endswith(".tsv")
    ])

    if not tsv_files:
        print(f"❌ No zoro_datafeed_*.tsv files found in {TSV_DIR}")
        return False

    print(f"🔧 Compiling {len(tsv_files)} Zoro TSV datafeeds into {DB_PATH}...")
    for f in tsv_files:
        print(f"  • {os.path.basename(f)} ({os.path.getsize(f) / (1024*1024):.2f} MB)")

    if os.path.exists(TMP_PATH):
        try:
            os.remove(TMP_PATH)
        except OSError:
            pass

    # Read header from file 0
    with open(tsv_files[0], "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        headers = [h.strip() for h in next(reader)]

    if "zoro_no" not in headers:
        print("❌ Error: 'zoro_no' column not found in header.")
        return False

    col_defs = []
    for h in headers:
        if h in PRICE_COLS:
            col_defs.append(f"[{h}] REAL")
        else:
            col_defs.append(f"[{h}] TEXT")

    create_sql = f"CREATE TABLE zoro_data (\n  " + ",\n  ".join(col_defs) + "\n);"
    placeholders = ",".join(["?"] * len(headers))
    insert_sql = f"INSERT INTO zoro_data VALUES ({placeholders})"

    t0 = time.time()
    total_rows = 0

    con = sqlite3.connect(TMP_PATH)
    con.executescript("""
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA cache_size = -300000;
        PRAGMA temp_store = MEMORY;
    """)

    con.execute(create_sql)

    for idx, tsv_file in enumerate(tsv_files):
        f_t0 = time.time()
        file_rows = 0
        batch = []
        with open(tsv_file, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f, delimiter="\t")
            if idx == 0:
                next(reader) # skip header for file 0

            for row in reader:
                if not row or not row[0].strip():
                    continue
                # Pad or trim row to match header length
                if len(row) < len(headers):
                    row.extend([""] * (len(headers) - len(row)))
                elif len(row) > len(headers):
                    row = row[:len(headers)]

                # Clean and parse values
                parsed = [parse_val(headers[i], row[i]) for i in range(len(headers))]
                # Uppercase zoro_no
                if parsed[0]:
                    parsed[0] = str(parsed[0]).upper()

                batch.append(parsed)
                file_rows += 1
                total_rows += 1

                if len(batch) >= BATCH_SIZE:
                    con.executemany(insert_sql, batch)
                    con.commit()
                    batch = []
                    print(f"  ⏳ {os.path.basename(tsv_file)}: {file_rows:,} rows (Total: {total_rows:,})", end="\r", flush=True)

            if batch:
                con.executemany(insert_sql, batch)
                con.commit()
                batch = []

        print(f"\n  ✓ {os.path.basename(tsv_file)}: {file_rows:,} rows in {time.time()-f_t0:.1f}s")

    print("  🔧 Deduplicating & creating index on zoro_no...")
    con.execute("""
        DELETE FROM zoro_data
        WHERE rowid NOT IN (SELECT MAX(rowid) FROM zoro_data GROUP BY zoro_no)
    """)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_zoro_no ON zoro_data(zoro_no)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_brand ON zoro_data(zoro_brand_name)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_avail ON zoro_data(availabilityStatus)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_lead ON zoro_data(lead_time)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_dropship ON zoro_data(is_dropship)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_blind ON zoro_data(is_blind_ship)")
    con.execute("PRAGMA journal_mode = WAL")
    con.commit()

    unique_cnt = con.execute("SELECT COUNT(*) FROM zoro_data").fetchone()[0]
    con.close()

    # Atomic swap
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass
    os.replace(TMP_PATH, DB_PATH)

    total_time = time.time() - t0
    print("=" * 65)
    print(f"🎉 SUCCESS: Built combined_reports.db")
    print(f"  • Total Unique Products: {unique_cnt:,}")
    print(f"  • Database File Size:    {os.path.getsize(DB_PATH)/(1024*1024):.2f} MB")
    print(f"  • Total Compilation Time:{total_time:.1f}s")
    print("=" * 65)
    return True

if __name__ == "__main__":
    build_combined_db()
