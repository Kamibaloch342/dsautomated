#!/usr/bin/env python3
"""Put hand-collected image links into a run's Shopify CSV.

    python tools/fill_image_links.py --run-dir ~/Hathoro_Shipping_Boxes/full_2026-10-28 --start
        writes image_links_to_fill.csv (one row per distinct image file, empty "link" column)
        and <csv>_files.csv (the Shopify CSV with the bare file name in "Product image URL")

    python tools/fill_image_links.py --run-dir ~/Hathoro_Shipping_Boxes/full_2026-10-28
        reads the filled-in image_links_to_fill.csv and writes <csv>_ready.csv with the links,
        then validates it (every link is downloaded once)
"""

import argparse
import csv
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hathoro.design import load_json  # noqa: E402
from hathoro.validate import Report, validate_csv  # noqa: E402


def file_of(url):
    return urllib.parse.unquote(url.rsplit("/", 1)[-1].split("?")[0])


def write_csv(path, fields, rows):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--csv-name", default="hathoro_zoro_select_shipping_boxes_master.csv")
    ap.add_argument("--start", action="store_true", help="write the files to fill in")
    args = ap.parse_args()

    run = Path(args.run_dir).expanduser()
    src = run / args.csv_name
    with open(src, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows, fields = list(reader), reader.fieldnames
    links_path = run / "image_links_to_fill.csv"

    if args.start:
        names, products = {}, {}
        for r in rows:
            if r.get("Product image URL"):
                name = file_of(r["Product image URL"])
                names.setdefault(name, r["URL handle"])
                products[name] = products.get(name, 0) + 1
                r["Product image URL"] = name
        write_csv(run / (src.stem + "_files.csv"), fields, rows)
        write_csv(links_path, ["file", "link", "used_by_products", "example_product"],
                  [{"file": n, "link": "", "used_by_products": products[n],
                    "example_product": h} for n, h in sorted(names.items())])
        print(f"{len(names)} distinct images -> {links_path}")
        print(f"CSV with file names -> {run / (src.stem + '_files.csv')}")
        return 0

    with open(links_path, newline="", encoding="utf-8") as fh:
        links = {r["file"].strip(): r["link"].strip() for r in csv.DictReader(fh)}
    missing = sorted(n for n, u in links.items() if not u)
    if missing:
        print(f"{len(missing)} files still have no link, e.g. {missing[:3]}")
        return 1
    for r in rows:
        if r.get("Product image URL"):
            r["Product image URL"] = links[file_of(r["Product image URL"])]
    out = run / (src.stem + "_ready.csv")
    write_csv(out, fields, rows)
    report = Report()
    validate_csv(out, report, load_json(ROOT / "catalog_settings.json"), check_urls=True)
    (run / "validation_report_ready.txt").write_text(report.text() + "\n")
    print(f"Shopify CSV: {out}\n{report.text().splitlines()[-1]}")
    for _, where, msg in report.errors[:20]:
        print(f"  ERROR {where}: {msg}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
