#!/usr/bin/env python3
"""Hathoro x Zoro Select shipping boxes: extract -> price -> 5 images -> Shopify CSV -> validate.

One test product (no database needed), written to its own folder:
    python generate_shipping_boxes_catalog.py --fixture test_data/G0507745.json \\
        --out-dir output/test_G0507745 --csv-name G0507745_shopify_import.csv --status draft

The same product from the Zoro database (read-only):
    python generate_shipping_boxes_catalog.py --sku G0507745 --out-dir output/test_G0507745 \\
        --csv-name G0507745_shopify_import.csv --status draft

Whole catalog (parallel image workers, resume-safe):
    python generate_shipping_boxes_catalog.py --workers 4 --skip-existing

Every run ends with a validation report (data, pricing, image files, Shopify CSV rules);
the exit code is 1 if it found errors. Paths, pricing rules and Shopify fields live in
catalog_settings.json, image text in content.json and the look in theme.json.
"""

import argparse
import csv
import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from hathoro.data import fetch_rows, parse_listing
from hathoro.design import ROOT, Theme, load_json
from hathoro.images import TEMPLATES, file_name, render_listing
from hathoro.pricing import price_for
from hathoro.shopify import COLUMNS, make_handle, product_rows
from hathoro.validate import Report, validate_csv, validate_images, validate_listing
from hathoro.zoro_images import product_photo

FATAL = ("dimensions not found", "missing pack quantity", "missing price")


def resolve(path):
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def run_paths(settings, out_dir=None, csv_name=None):
    """Where a run writes its CSV, images, upload folder and reports."""
    paths = {k: resolve(v) for k, v in settings["paths"].items()}
    if out_dir:
        out = resolve(out_dir)
        paths.update(images_dir=out / "images", csv=out / paths["csv"].name,
                     exceptions=out / "exceptions.csv")
    if csv_name:
        paths["csv"] = paths["csv"].parent / csv_name
    paths["report"] = paths["csv"].parent / "validation_report.txt"
    paths["upload_dir"] = paths["csv"].parent / "upload_to_shopify_files"
    return paths


_worker = {}


def _init_worker(theme_path, content_path):
    _worker["theme"] = Theme(theme_path)
    _worker["content"] = load_json(content_path)
    _worker["cache"] = {}


def _render(row, out_dir, photo, only, skip_existing):
    li = parse_listing(row)
    render_listing(_worker["theme"], li, _worker["content"], out_dir, only=only,
                   cache=_worker["cache"], skip_existing=skip_existing, product_photo=photo)
    return li.zoro_no


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", "--sample", dest="fixture",
                    help="JSON with zoro_data rows (one row, a list, or the export from "
                         "tools/export_zoro_rows.py); used instead of the DB")
    ap.add_argument("--db", help="override paths.database from catalog_settings.json")
    ap.add_argument("--sku", help="only this zoro_no")
    ap.add_argument("--limit", type=int, help="only the first N qualifying rows")
    ap.add_argument("--out-dir", help="write everything for this run into one folder")
    ap.add_argument("--csv-name", help="file name for the Shopify CSV (default from settings)")
    ap.add_argument("--image-base-url", help="public folder URL where the images are hosted")
    ap.add_argument("--status", choices=["active", "draft", "archived"],
                    help="override the Shopify status (use draft for test imports)")
    ap.add_argument("--settings", default=str(ROOT / "catalog_settings.json"))
    ap.add_argument("--content", default=str(ROOT / "content.json"))
    ap.add_argument("--theme", default=str(ROOT / "theme.json"))
    ap.add_argument("--no-images", action="store_true", help="CSV only (images already made)")
    ap.add_argument("--only", help="comma list of images: " + ",".join(n for n, _ in TEMPLATES))
    ap.add_argument("--workers", type=int, default=1,
                    help=f"parallel image workers (this machine has {os.cpu_count()} CPUs)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="keep images that already exist (resume an interrupted run)")
    ap.add_argument("--check-urls", action="store_true",
                    help="validation also downloads every image URL in the CSV")
    args = ap.parse_args()

    settings = load_json(args.settings)
    content = load_json(args.content)
    theme = Theme(args.theme)
    if args.image_base_url is not None:
        settings["image_base_url"] = args.image_base_url
    paths = run_paths(settings, args.out_dir, args.csv_name)
    report_path, upload_dir = paths["report"], paths["upload_dir"]

    # ------------------------------------------------------------ 1. extract
    if args.fixture:
        data = load_json(resolve(args.fixture))
        if isinstance(data, dict) and "rows" in data:       # tools/export_zoro_rows.py output
            rows = data["rows"]
        else:
            rows = data if isinstance(data, list) else [data]
        for r in rows:                                       # same alias as the DB query
            if not r.get("country_of_origin"):
                col = next((k for k in r if ("country" in k.lower() or "origin" in k.lower())
                            and k != "country_of_origin"), None)
                if col:
                    r["country_of_origin"] = r[col]
        if args.limit:
            rows = rows[:args.limit]
        if args.sku:
            rows = [r for r in rows if r.get("zoro_no") == args.sku]
        print(f"{len(rows)} row(s) from {args.fixture}")
    else:
        db = Path(args.db or paths["database"])
        if not db.exists():
            sys.exit(f"Database not found: {db}\n(use --fixture test_data/G0507745.json to test)")
        rows, origin_col = fetch_rows(db, sku=args.sku, limit=args.limit)
        print(f"{len(rows)} qualifying rows from {db.name}")
        if args.sku and not rows:
            sys.exit(f"{args.sku} is not in the qualifying rows (brand, 1-day lead time, "
                     "in stock, price and pack size filters)")
        if not origin_col:
            print("Note: no country-of-origin column found; the Made in USA image is only used "
                  "for rows whose country_of_origin says USA.")

    images_dir = resolve(paths["images_dir"])
    only = set(args.only.split(",")) if args.only else None
    all_rows, exceptions, jobs, checked = [], [], [], []
    t0 = time.time()

    # ------------------------------------------------------------ 2. parse, price, CSV rows
    priced = {}                      # handle -> (row, li, price): one product per handle
    for row in rows:
        li = parse_listing(row)
        fatal = [p for p in li.problems if p.startswith(FATAL)]
        if fatal:
            exceptions.append({"zoro_no": li.zoro_no, "reason": "; ".join(fatal),
                               "short_description": li.short_description})
            continue
        try:
            price = price_for(li, settings["pricing"])
        except ValueError as e:
            exceptions.append({"zoro_no": li.zoro_no, "reason": str(e),
                               "short_description": li.short_description})
            continue
        handle = make_handle(li, settings)
        # the same box (size, wall, rating, colour, style, pack) from several Zoro SKUs:
        # list it once, bought from the cheapest SKU
        kept = priced.get(handle)
        if kept and (kept[2].landed_cost, kept[1].zoro_no) <= (price.landed_cost, li.zoro_no):
            dropped, winner = li, kept[1]
        else:
            priced[handle] = (row, li, price)
            dropped, winner = (kept[1], li) if kept else (None, None)
        if dropped:
            exceptions.append({"zoro_no": dropped.zoro_no,
                               "reason": f"skipped: same box as {winner.zoro_no}, which costs less",
                               "short_description": dropped.short_description})

    for handle, (row, li, price) in priced.items():
        out_dir = images_dir / handle
        photo = None
        if not args.no_images and (not only or "main" in only):
            try:
                photo = product_photo(li, settings)
            except RuntimeError as e:
                print(f"  {li.zoro_no}: photo download failed ({e})")
            if photo is None:
                exceptions.append({"zoro_no": li.zoro_no, "reason": "warning: no supplier photo",
                                   "short_description": li.short_description})
        jobs.append((row, out_dir, photo))
        files = [(name, file_name(name, li, content)) for name, _ in TEMPLATES]
        handle, product = product_rows(li, price, files, settings, status=args.status)
        all_rows += product
        checked.append((li, price, product[0], photo is not None, out_dir, files))

        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "listing.json").write_text(json.dumps(
            {"listing": li.to_dict(), "pricing": price.to_dict(), "handle": handle,
             "supplier_photo": str(photo) if photo else None},
            indent=2, default=str) + "\n")
        if len(rows) <= 5:
            print_listing(li, price, product[0], out_dir)

    # ------------------------------------------------------------ 3. images
    if jobs and not args.no_images:
        print(f"\nRendering images for {len(jobs)} listing(s) with {args.workers} worker(s)...")
        render_args = (only, args.skip_existing)
        if args.workers <= 1:
            _init_worker(args.theme, args.content)
            for n, (row, out_dir, photo) in enumerate(jobs, start=1):
                _render(row, out_dir, photo, *render_args)
                progress(n, len(jobs), t0)
        else:
            with ProcessPoolExecutor(args.workers, initializer=_init_worker,
                                     initargs=(args.theme, args.content)) as pool:
                futures = [pool.submit(_render, row, out_dir, photo, *render_args)
                           for row, out_dir, photo in jobs]
                for n, fut in enumerate(as_completed(futures), start=1):
                    fut.result()
                    progress(n, len(jobs), t0)

    # one flat folder with every distinct image, ready to upload to Shopify Files / a CDN
    upload_dir.mkdir(parents=True, exist_ok=True)
    for *_, out_dir, files in checked:
        for _, name in files:
            src = out_dir / name
            if src.exists() and not (upload_dir / name).exists():
                shutil.copy2(src, upload_dir / name)

    # ------------------------------------------------------------ 4. CSV
    csv_path = resolve(paths["csv"])
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nShopify CSV: {csv_path} ({len(all_rows)} rows)")
    print(f"Images to upload: {upload_dir} ({len(list(upload_dir.glob('*.jpg')))} files)")
    if not settings.get("image_base_url"):
        print("NOTE: the CSV has NO images. Shopify only imports images from public https URLs:\n"
              "      upload the files above (Shopify admin > Content > Files), then re-run with\n"
              "      --image-base-url <the files folder URL>.")

    if exceptions:
        ex_path = resolve(paths["exceptions"])
        with open(ex_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["zoro_no", "reason", "short_description"],
                               lineterminator="\n")
            w.writeheader()
            w.writerows(exceptions)
        warned = sum(e["reason"].startswith("warning") for e in exceptions)
        print(f"Skipped {len(exceptions) - warned} rows, {warned} warnings, see {ex_path}")

    # ------------------------------------------------------------ 5. validate
    report = Report()
    for li, price, row, photo_found, *_ in checked:
        validate_listing(li, price, row, settings, report, photo_found=photo_found)
    validate_images({li.zoro_no: [(n, d / f) for n, f in files]
                     for li, _, _, _, d, files in checked}, theme, report)
    validate_csv(csv_path, report, settings, check_urls=args.check_urls)
    report_path.write_text(report.text() + "\n")
    print(f"\nValidation ({report_path}):")
    print(report.text())
    print(f"\nDone in {time.time() - t0:.0f}s")
    sys.exit(1 if report.errors else 0)


def progress(n, total, t0):
    if n == total or n % 10 == 0:
        el = time.time() - t0
        print(f"  {n}/{total} listings ({el:.0f}s, ~{el / n * (total - n) / 60:.0f} min left)")


def print_listing(li, price, row, out_dir):
    f = li.fields()
    print(f"\n{row['Title']}")
    print(f"  Size {li.L:g} x {li.W:g} x {li.H:g} in | {li.wall} | {f['rating'] or 'no rating'}"
          f" | pack {li.pack} | made in USA: {li.made_in_usa}")
    print(f"  Base ${li.unit_cost:.2f} x {li.order_qty} = ${price.base_cost:.2f}"
          f"  -> -{price.discount:.2f} discount = ${price.discounted_cost:.2f}"
          f"  + surcharge ${price.surcharge:.2f} + freight ${price.freight:.2f}"
          f" = landed ${price.landed_cost:.2f}")
    print(f"  Price ${price.price:.2f} (raw {price.raw_price:.2f})  compare-at "
          f"{price.compare_at}  profit ${price.profit:.2f} ({price.margin:.2%})")
    print(f"  SKU {row['SKU']}  |  images: {out_dir}")


if __name__ == "__main__":
    main()
