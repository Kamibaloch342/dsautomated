#!/usr/bin/env python3
"""Export the qualifying Zoro Select shipping-box rows from combined_reports.db to one file.

    python3 export_zoro_rows.py                         # default database path
    python3 export_zoro_rows.py "/path/to/combined_reports.db"

Writes ~/Downloads/zoro_shipping_boxes_rows.json (all columns of every qualifying row).
The database is opened read-only. The filters are the same as the catalog pipeline's.
"""

import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

DB = Path("/Users/kamibaloch342/Downloads/Dropshipping/Development_Unit/Automations/"
          "TSV Files/combined_reports.db")
OUT = Path.home() / "Downloads" / "zoro_shipping_boxes_rows.json"

# keep in sync with hathoro/data.py QUERY (tests/test_pipeline.py checks this)
WHERE = """
    (zoro_brand_name IS NULL OR zoro_brand_name = '' OR zoro_brand_name = 'Zoro Select')
    AND (short_description LIKE '%Shipping Box%'
         OR short_description LIKE '%Corrugated Box%'
         OR short_description LIKE '%Multi-Depth Box%')
    AND short_description NOT LIKE '%Lid%'
    AND short_description NOT LIKE '%Pad%'
    AND short_description NOT LIKE '%Roll%'
    AND short_description NOT LIKE '%Tape%'
    AND lead_time LIKE '%1 day%'
    AND (availabilityStatus = 'In Stock' OR availabilityStatus IS NULL)
    AND sales_price > 0
    AND min_order_qty > 0
"""


def main():
    db = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DB
    if not db.exists():
        sys.exit(f"Database not found: {db}\n"
                 'Run it with the path:  python3 export_zoro_rows.py "/path/to/combined_reports.db"')
    con = sqlite3.connect(f"file:{quote(str(db))}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    columns = [r[1] for r in con.execute("PRAGMA table_info(zoro_data)")]
    rows = [dict(r) for r in con.execute(f"SELECT * FROM zoro_data WHERE {WHERE} ORDER BY zoro_no")]
    con.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": str(db), "columns": columns, "rows": rows},
                              default=str, ensure_ascii=False))
    print(f"{len(rows)} rows, {len(columns)} columns -> {OUT} "
          f"({OUT.stat().st_size / 1e6:.1f} MB). Upload this file to Claude.")


if __name__ == "__main__":
    main()
