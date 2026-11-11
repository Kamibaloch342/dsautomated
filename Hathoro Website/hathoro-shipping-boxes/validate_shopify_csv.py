#!/usr/bin/env python3
"""Check a Shopify product CSV before importing it.

    python validate_shopify_csv.py output/hathoro_zoro_select_shipping_boxes_master.csv
    python validate_shopify_csv.py my.csv --check-urls      # also download every image URL

Exit code 1 if there are errors. The pipeline runs these checks (plus data, pricing and
image-file checks) automatically; this script is for checking any CSV on its own.
"""

import argparse
import sys

from hathoro.design import ROOT, load_json
from hathoro.validate import Report, validate_csv


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--settings", default=str(ROOT / "catalog_settings.json"))
    ap.add_argument("--check-urls", action="store_true", help="fetch every image URL")
    args = ap.parse_args()
    report = Report()
    validate_csv(args.csv, report, load_json(args.settings), check_urls=args.check_urls)
    print(report.text())
    sys.exit(1 if report.errors else 0)


if __name__ == "__main__":
    main()
