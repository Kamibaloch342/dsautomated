#!/usr/bin/env python3
"""Upload a run's images to freeimage.host (iili.io links) and write a Shopify CSV that uses them.

Shopify copies product images from public links during the import; freeimage.host keeps them
online without this computer, so no tunnel has to stay open.

    python tools/upload_freeimage.py --run-dir ~/Hathoro_Shipping_Boxes/full_2026-10-28

The API key is read from ~/.zuhra/freeimage_key.txt (or FREEIMAGE_API_KEY); get it from
https://freeimage.host/page/api while logged in. Uploads are recorded in
<run-dir>/freeimage_links.json, so an interrupted run resumes where it stopped.

Writes <run-dir>/<csv name>_freeimage.csv: the run's CSV with every image URL replaced by its
iili.io link, then validated (every link downloaded once).
"""

import argparse
import csv
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hathoro.design import load_json  # noqa: E402
from hathoro.net import ssl_context  # noqa: E402
from hathoro.validate import Report, validate_csv  # noqa: E402

API = "https://freeimage.host/api/1/upload"
KEY_FILE = Path.home() / ".zuhra" / "freeimage_key.txt"


def api_key():
    key = os.environ.get("FREEIMAGE_API_KEY") or (
        KEY_FILE.read_text().strip() if KEY_FILE.exists() else "")
    if not key:
        sys.exit(f"No API key: put it in {KEY_FILE} (or set FREEIMAGE_API_KEY).")
    return key


def upload(path, key, attempts=5):
    """POST one file; returns its direct image URL."""
    last = None
    for i in range(attempts):
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in (("key", key), ("action", "upload"), ("format", "json")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                         f"\r\n\r\n{value}\r\n".encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="source"; '
                     f'filename="{path.name}"\r\nContent-Type: image/jpeg\r\n\r\n'.encode())
        parts.append(path.read_bytes())
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        req = urllib.request.Request(API, data=b"".join(parts), headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "HathoroCatalog/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=120, context=ssl_context()) as resp:
                data = json.loads(resp.read())
            url = (data.get("image") or {}).get("url")
            if data.get("status_code") == 200 and url:
                return url
            last = f"unexpected answer: {str(data)[:200]}"
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode(errors="replace")
            if e.code in (400, 401, 403):
                raise RuntimeError(f"{path.name}: HTTP {e.code} {body}") from e
            last = f"HTTP {e.code} {body}"
        except Exception as e:  # noqa: BLE001 - timeouts, resets
            last = e
        time.sleep(5 * 2 ** i)
    raise RuntimeError(f"{path.name}: not uploaded after {attempts} tries ({last})")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, help="the --out-dir of a catalog run")
    ap.add_argument("--csv-name", default="hathoro_zoro_select_shipping_boxes_master.csv")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, help="upload only this many new files (a trial)")
    args = ap.parse_args()

    run = Path(args.run_dir).expanduser()
    files = sorted((run / "upload_to_shopify_files").glob("*.jpg"))
    links_path = run / "freeimage_links.json"
    links = json.loads(links_path.read_text()) if links_path.exists() else {}
    todo = [f for f in files if f.name not in links][:args.limit]
    print(f"{len(files)} images, {len(links)} already uploaded, {len(todo)} to upload")

    key = api_key()
    lock = threading.Lock()
    failed = []
    t0 = time.time()

    def save():
        tmp = links_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(links, indent=1, sort_keys=True))
        tmp.replace(links_path)

    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(upload, f, key): f for f in todo}
        for n, fut in enumerate(as_completed(futures), start=1):
            f = futures[fut]
            try:
                url = fut.result()
                with lock:
                    links[f.name] = url
                    if n % 25 == 0 or n == len(todo):
                        save()
            except RuntimeError as e:
                failed.append(str(e))
                print(f"  FAILED {e}")
                if "HTTP 40" in str(e):
                    save()
                    sys.exit("The API refused the upload (key or limit); stopping.")
            if n % 50 == 0 or n == len(todo):
                el = time.time() - t0
                print(f"  {n}/{len(todo)} uploaded ({el:.0f}s, ~{el / n * (len(todo) - n) / 60:.0f} min left)",
                      flush=True)
    save()
    missing = [f.name for f in files if f.name not in links]
    if missing:
        print(f"\n{len(missing)} images not uploaded yet; run this again to retry them.")
        return 1

    # CSV with the iili.io links
    src = run / args.csv_name
    out = run / (src.stem + "_freeimage.csv")
    with open(src, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = reader.fieldnames
    for r in rows:
        u = r.get("Product image URL")
        if u:
            name = urllib.parse.unquote(u.rsplit("/", 1)[-1].split("?")[0])
            r["Product image URL"] = links[name]
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(out)

    report = Report()
    validate_csv(out, report, load_json(ROOT / "catalog_settings.json"), check_urls=True)
    (run / "validation_report_freeimage.txt").write_text(report.text() + "\n")
    print(f"\nShopify CSV: {out}")
    print(report.text().splitlines()[-1])
    for _, where, msg in report.errors[:20]:
        print(f"  ERROR {where}: {msg}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
