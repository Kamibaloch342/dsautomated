"""Pre-import checks: Shopify CSV rules, listing data and pricing, generated image files.

Every check adds an ERROR (blocks import) or a WARNING (import works, but look at it).
Shopify rules follow help.shopify.com/en/manual/products/import-export/using-csv.
"""

import csv
import hashlib
import io
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from pathlib import Path

from PIL import Image

from .net import ssl_context
from .pricing import price_for

KNOWN_COLUMNS = {
    "Title", "URL handle", "Description", "Vendor", "Product category", "Product Category",
    "Type", "Tags",
    "Published on online store", "Status", "SKU", "Barcodes", "Option1 name", "Option1 value",
    "Option1 LinkedTo", "Option2 name", "Option2 value", "Option2 LinkedTo", "Option3 name",
    "Option3 value", "Option3 LinkedTo", "Price", "Price / International", "Compare-at price",
    "Compare-at price / International", "Cost per item", "Charge tax", "Inventory tracker",
    "Inventory quantity", "Continue selling when out of stock", "Weight value (grams)",
    "Weight unit for display", "Packed product length", "Packed product width",
    "Packed product height", "Packed product dimension unit", "Requires shipping",
    "Fulfillment service", "Product image URL", "Image position", "Image alt text",
    "Variant image URL", "Gift card", "SEO title", "SEO description",
    "Google Shopping / Google Product Category", "Collection",
}
BOOL_COLUMNS = ["Published on online store", "Charge tax", "Requires shipping", "Gift card"]
ENUMS = {
    "Status": {"active", "draft", "archived"},
    "Continue selling when out of stock": {"deny", "continue"},
    "Inventory tracker": {"shopify", "shipwire", "amazon_marketplace_web", ""},
    "Weight unit for display": {"g", "kg", "lb", "oz", ""},
    "Packed product dimension unit": {"cm", "in", ""},
}
IMAGE_COLUMNS = {"URL handle", "Product image URL", "Image position", "Image alt text"}
# Shopify Standard Product Taxonomy (github.com/Shopify/product-taxonomy), shipping subtree
TAXONOMY = {
    "os-14": "Office Supplies > Shipping Supplies",
    "os-14-1": "Office Supplies > Shipping Supplies > Moving & Shipping Boxes",
    "os-14-2": "Office Supplies > Shipping Supplies > Packing Materials",
    "os-14-3": "Office Supplies > Shipping Supplies > Packing Tape",
}
HANDLE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MONEY_RE = re.compile(r"^\d+(?:\.\d{1,2})?$")
PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_]+\}")
MAX_CSV_BYTES = 15 * 1024 * 1024
URL_WORKERS = 8
VALIDATOR_AGENT = "HathoroValidator/1.0"     # lets the tunnel server tell our checks from Shopify


class Report:
    def __init__(self):
        self.items = []

    def error(self, where, msg):
        self.items.append(("ERROR", where, msg))

    def warn(self, where, msg):
        self.items.append(("WARNING", where, msg))

    def ok(self, where, msg):
        self.items.append(("OK", where, msg))

    @property
    def errors(self):
        return [i for i in self.items if i[0] == "ERROR"]

    @property
    def warnings(self):
        return [i for i in self.items if i[0] == "WARNING"]

    def text(self):
        lines = []
        for level in ("ERROR", "WARNING", "OK"):
            for lv, where, msg in self.items:
                if lv == level:
                    lines.append(f"{lv:8s} {where}: {msg}")
        verdict = "FAILED" if self.errors else "PASSED"
        lines.append("")
        lines.append(f"{verdict}: {len(self.errors)} errors, {len(self.warnings)} warnings, "
                     f"{sum(1 for i in self.items if i[0] == 'OK')} checks ok")
        return "\n".join(lines)


# ----------------------------------------------------------------------------- CSV

def validate_csv(path, report, settings=None, check_urls=False):
    path = Path(path)
    raw = path.read_bytes()
    where = path.name
    if len(raw) > MAX_CSV_BYTES:
        report.error(where, f"file is {len(raw) / 1e6:.1f} MB; Shopify imports up to 15 MB, "
                            "split it into several files")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        report.error(where, f"not UTF-8: {e}")
        return []
    if "\r\n" in text:
        report.warn(where, "CRLF line endings; Shopify asks for LF")
    rows = list(csv.DictReader(io.StringIO(text)))
    headers = rows[0].keys() if rows else csv.DictReader(io.StringIO(text)).fieldnames or []
    unknown = [h for h in headers if h not in KNOWN_COLUMNS
               and not h.startswith(("product.metafields.", "Included / "))]
    if unknown:
        report.error(where, f"unknown columns (break the import): {unknown}")
    if "Title" not in headers or "URL handle" not in headers:
        report.error(where, "Title and URL handle columns are required")
        return rows
    if "Barcode" in headers and "Barcodes" in headers:
        report.error(where, "file has both Barcode and Barcodes columns")
    if not rows:
        report.error(where, "no product rows")
        return rows
    report.ok(where, f"{len(rows)} rows, {len(headers)} known columns, UTF-8")

    to_check = {}
    products = defaultdict(list)
    for i, row in enumerate(rows, start=2):
        products[row["URL handle"]].append((i, row))
    skus = defaultdict(list)
    expected_collection = (settings or {}).get("shopify", {}).get("collection")

    for handle, prow in products.items():
        w = f"{handle or '(no handle)'}"
        first_line, first = prow[0]
        titled = [i for i, r in prow if r.get("Title")]
        if len(titled) > 1:
            report.error(w, f"URL handle used by {len(titled)} products (lines {titled}): Shopify "
                            "would merge them into one product")
        if not HANDLE_RE.match(handle or ""):
            report.error(w, f"URL handle '{handle}' must be lowercase letters, digits, hyphens")
        if len(handle) > 255:
            report.error(w, "URL handle longer than 255 characters")
        if not first.get("Title"):
            report.error(w, f"line {first_line}: first row of a product needs a Title")
        elif len(first["Title"]) > 255:
            report.error(w, "Title longer than 255 characters")

        for col in BOOL_COLUMNS:
            v = first.get(col, "")
            if v and v not in ("true", "false"):
                report.error(w, f"{col} must be true/false, got '{v}'")
        for col, allowed in ENUMS.items():
            v = first.get(col, "")
            if col in first and v not in allowed:
                report.error(w, f"{col} must be one of {sorted(a for a in allowed if a)}, got '{v}'")
        if first.get("Status") == "draft":
            report.warn(w, "Status is draft: the product is hidden from the storefront until "
                           "you set it to active")

        price = first.get("Price", "")
        if not MONEY_RE.match(price) or float(price) <= 0:
            report.error(w, f"Price '{price}' must be a positive amount like 23.99")
        cap = first.get("Compare-at price", "")
        if cap:
            if not MONEY_RE.match(cap):
                report.error(w, f"Compare-at price '{cap}' is not an amount")
            elif MONEY_RE.match(price) and float(cap) <= float(price):
                report.error(w, "Compare-at price must be higher than Price")
        cost = first.get("Cost per item", "")
        if cost:
            if not MONEY_RE.match(cost):
                report.error(w, f"Cost per item '{cost}' is not an amount")
            elif MONEY_RE.match(price) and float(cost) >= float(price):
                report.error(w, f"Cost per item {cost} is not below Price {price}")
        grams = first.get("Weight value (grams)", "")
        if grams == "":
            report.warn(w, "no weight: shipping rates at checkout will treat it as 0 lb")
        elif not grams.isdigit():
            report.error(w, f"Weight value (grams) '{grams}' must be a whole number")
        qty = first.get("Inventory quantity", "")
        if qty and not qty.lstrip("-").isdigit():
            report.error(w, f"Inventory quantity '{qty}' must be a whole number")

        if first.get("SKU") and not (first.get("Option1 name") and first.get("Option1 value")):
            report.error(w, "SKU/price/weight need Option1 name and Option1 value")
        if first.get("SKU"):
            skus[first["SKU"]].append(handle)

        cat = first.get("Product category", first.get("Product Category", ""))
        if not cat:
            report.warn(w, "no Product category: automated collections based on category "
                           "will not pick it up")
        elif cat not in TAXONOMY.values() and cat.split("/")[-1] not in TAXONOMY:
            report.warn(w, f"Product category '{cat}' not in the bundled shipping-supplies "
                           "list; check it against Shopify's taxonomy")
        coll = first.get("Collection", "")
        if expected_collection and coll != expected_collection:
            report.error(w, f"Collection is '{coll}', expected '{expected_collection}'")
        elif not coll:
            report.warn(w, "no Collection column value")

        if len(first.get("SEO title", "")) > 70:
            report.warn(w, f"SEO title is {len(first['SEO title'])} characters (>70 is cut "
                           "off in search results)")
        if len(first.get("SEO description", "")) > 320:
            report.warn(w, "SEO description longer than 320 characters")
        for tag in (t.strip() for t in first.get("Tags", "").split(",")):
            if len(tag) > 255:
                report.error(w, f"tag longer than 255 characters: {tag[:40]}...")

        for line, row in prow:
            for col, v in row.items():
                if v and PLACEHOLDER_RE.search(v):
                    report.error(w, f"line {line}: unfilled template placeholder in {col}: "
                                    f"{PLACEHOLDER_RE.search(v).group()}")
        for line, row in prow[1:]:
            extra = [c for c, v in row.items() if v and c not in IMAGE_COLUMNS]
            if extra:
                report.error(w, f"line {line}: image rows may only fill {sorted(IMAGE_COLUMNS)}, "
                                f"found {extra}")

        positions = []
        for line, row in prow:
            url = row.get("Product image URL", "")
            if not url:
                continue
            if not url.startswith("https://"):
                report.error(w, f"line {line}: image URL must be public https: {url}")
            name = url.rsplit("/", 1)[-1].split("?")[0]
            if re.search(r"_(thumb|small|medium)\b", name):
                report.error(w, f"line {line}: image file name '{name}' contains _thumb/_small/"
                                "_medium, which Shopify rejects")
            alt = row.get("Image alt text", "")
            if len(alt) > 512:
                report.error(w, f"line {line}: image alt text longer than 512 characters")
            pos = row.get("Image position", "")
            if not pos.isdigit():
                report.error(w, f"line {line}: Image position '{pos}' must be 1, 2, 3...")
            else:
                positions.append(int(pos))
            if check_urls:
                to_check.setdefault(url, w)        # shared images are checked once
        if positions and positions != list(range(1, len(positions) + 1)):
            report.error(w, f"image positions {positions} must run 1..{len(positions)}")
        if not positions:
            report.warn(w, "no images in the CSV (set image_base_url once images are hosted, "
                           "or add them by hand in Shopify)")
        else:
            report.ok(w, f"{len(positions)} images, positions 1-{len(positions)}")
        report.ok(w, f"product row: price {price}, compare-at {cap or '-'}, cost {cost or '-'}, "
                     f"{grams or '?'} g, category '{cat}', collection '{coll}'")

    for sku, handles in skus.items():
        if len(handles) > 1:
            report.error("SKU", f"{sku} used by several products: {handles}")
    if to_check:
        with ThreadPoolExecutor(URL_WORKERS) as pool:
            results = pool.map(check_url, to_check)
            for (url, where), (ok, msg) in zip(to_check.items(), results):
                (report.ok if ok else report.error)(where, msg)
    return rows


def check_url(url, attempts=5):
    """Download one image URL; (ok, message). Retries timeouts and 5xx answers, which a
    tunnel or CDN gives now and then, before calling the link broken."""
    last = None
    for i in range(attempts):
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": VALIDATOR_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=20, context=ssl_context()) as resp:
                resp.read()
                ctype = resp.headers.get("Content-Type", "")
                if resp.status == 200 and ctype.startswith("image/"):
                    return True, f"reachable: {url.rsplit('/', 1)[-1]}"
                return False, f"{url} answered {resp.status} {ctype}"
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return False, f"image URL not reachable ({e}): {url}"
            last = e
        except Exception as e:  # noqa: BLE001 - timeouts, resets
            last = e
        if i + 1 < attempts:
            time.sleep(5 * 2 ** i)               # 5, 10, 20, 40 s: rides out tunnel blips
    return False, f"image URL not reachable after {attempts} tries ({last}): {url}"


# ----------------------------------------------------------------------------- listing data

def validate_listing(li, price, row, settings, report, photo_found=True):
    w = li.zoro_no
    rules = settings["pricing"]
    if not (li.L and li.W and li.H):
        report.error(w, "dimensions missing")
    else:
        report.ok(w, f"inside size {li.L:g} x {li.W:g} x {li.H:g} in")
    if li.pack <= 0:
        report.error(w, "pack quantity missing")
    lead = (li.lead_time or "").lower()
    if "1 day" not in lead:
        report.error(w, f"lead time '{li.lead_time}' is not 1 day")
    if li.availabilityStatus not in (None, "", "In Stock"):
        report.error(w, f"availability '{li.availabilityStatus}' is not In Stock")

    expected = price_for(li, rules)
    if row and row.get("Price") != f"{expected.price:.2f}":
        report.error(w, f"CSV price {row.get('Price')} != recomputed {expected.price:.2f}")
    if expected.margin + 1e-9 < rules["target_margin"]:
        report.error(w, f"margin {expected.margin:.2%} below {rules['target_margin']:.0%}")
    else:
        surcharge = f" + ${expected.surcharge:.2f} freight surcharge" if expected.surcharge else ""
        report.ok(w, f"pricing: ${li.unit_cost:.2f} x {li.order_qty} = ${expected.base_cost:.2f}"
                     f"{surcharge}, "
                     f"landed ${expected.landed_cost:.2f}, price ${expected.price:.2f}, "
                     f"profit ${expected.profit:.2f} ({expected.margin:.2%})")
    if li.has_rating:
        report.ok(w, f"strength claim backed by data: {li.fields()['rating']}, "
                     f"{li.max_lbs} lb max")
    else:
        report.warn(w, "no ECT/burst rating in the data: image 3 uses generic text")
    if li.made_in_usa:
        report.ok(w, f"Made in USA image backed by country of origin '{li.country_of_origin}'")
    else:
        report.warn(w, f"country of origin '{li.country_of_origin or 'unknown'}': image 5 "
                       "uses the non-origin version")
    if not li.ship_pack_weight:
        report.warn(w, "ship_pack_weight missing")
    if not photo_found:
        report.warn(w, "supplier photo not found: image 1 uses the Hathoro template photo")
    for p in li.problems:
        if not p.startswith(("ECT / burst", "no supplier photo")):
            report.warn(w, p)


# ----------------------------------------------------------------------------- images

def validate_images(listing_files, theme, report):
    """listing_files: {zoro_no: [(name, path)]}. Checks each file and that a file name shared
    between listings always holds the same picture."""
    size = int(theme["canvas"])
    seen = {}
    for sku, files in listing_files.items():
        for name, path in files:
            path = Path(path)
            w = f"{sku} {path.name}"
            if not path.exists():
                report.error(w, "image file missing")
                continue
            if re.search(r"_(thumb|small|medium)\b", path.stem):
                report.error(w, "file name contains _thumb/_small/_medium")
            try:
                with Image.open(path) as im:
                    fmt, dims, mode = im.format, im.size, im.mode
            except Exception as e:  # noqa: BLE001
                report.error(w, f"not a readable image: {e}")
                continue
            mb = path.stat().st_size / 1e6
            if fmt != "JPEG" or dims != (size, size) or mode != "RGB":
                report.error(w, f"expected {size}x{size} RGB JPEG, got {dims} {mode} {fmt}")
            elif mb > 20:
                report.error(w, f"{mb:.1f} MB; Shopify's limit is 20 MB")
            else:
                report.ok(w, f"{dims[0]}x{dims[1]} JPEG, {mb:.2f} MB")
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            if path.name in seen and seen[path.name][0] != digest:
                report.error(w, f"same file name as {seen[path.name][1]} but a different "
                                "picture; with one shared image folder one would overwrite "
                                "the other")
            seen.setdefault(path.name, (digest, sku))
