"""
Zoro Product Scraper
────────────────────
Scrapes product pages from zoro.com, extracts:
  - LD+JSON structured data (title, brand, price, etc.)
  - Specification tables from the page HTML
  - Category breadcrumb tree from BreadcrumbList schema
  - All product images

Saves to:
  1. PostgreSQL database (primary) with specs as JSONB
  2. JSON files in products/ directory (backup)
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import sys

# ── Add Backend(Task) to path for imports ──
sys.path.insert(0, os.path.dirname(__file__))

from app import app
from database import db
from models import Product, ProductImage, Category


# ── URLs to scrape ──

urls = [
    "https://www.zoro.com/milwaukee-tool-1-12-in-shockwave-impact-duty-12-in-drive-deep-well-6-point-impact-socket-49-66-6238/i/G501259447/",
    "https://www.zoro.com/bosch-rebar-cutter-34-in-12-in-oal-sds-plus-rc2124/i/G3268772/",
    "https://www.zoro.com/pure-bright-germicidal-bleach-bottle-1-gal-concentrated-formula-clear-fragrance-free-6pk-kik-bleach6/i/G7638467/",
    "https://www.zoro.com/azusa-safety-cut-resistant-work-gloves-cut-level-a4-dipped-polyurethane-smooth-m-1-pr-az593/i/G7804513/",
    "https://www.zoro.com/ge-lighting-led-lamp-cylindrical-polycarbonate-pk2-led15ls2850/i/G6637946/",
    "https://www.zoro.com/irwin-all-purpose-drill-tap-set-80187/i/G4619501/",
    "https://www.zoro.com/southwire-sjtw-extension-cord-12-awg-outdoor-123-1-outlets-5-15r-bluered-pvc-5-15p-50-ft-2548sw0064/i/G1395266/",
    "https://www.zoro.com/amerex-fire-extinguisher-class-abc-ul-rating-4a80bc-rechargeable-10-lb-capacity-21-ft-range-b456/i/G3933237/",
    "https://www.zoro.com/first-aid-only-first-aid-kit-serves-25-people-107-components-osha-compliant-plastic-case-223/i/G0251377/",
    "https://www.zoro.com/stanley-25-ft-fatmax-classic-tape-measure-1-14-in-blade-stud-markings-abs-plastic-case-rubber-grip-33-725/i/G1202564/",
]

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def extract_ld_json(soup):
    """Extract the Product LD+JSON structured data from the page."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and data.get("@type") == "Product":
                return data
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def extract_breadcrumbs(soup):
    """
    Extract the category breadcrumb trail from the page HTML.

    Zoro uses a BreadcrumbList schema inside a <nav> with <ol> containing
    <li> items with itemprop="name" spans.

    Returns a list like ["Lighting", "Light Bulbs", "LED Light Bulbs"]
    (skips "Home" at the start and the product title at the end).
    """
    breadcrumbs = []

    # Strategy 1: Parse schema.org BreadcrumbList markup
    nav = soup.find("nav", attrs={"aria-label": "breadcrumb"})
    if nav:
        items = nav.find_all("li", attrs={"itemprop": "itemListElement"})
        for item in items:
            name_el = item.find("span", attrs={"itemprop": "name"})
            if name_el:
                name = name_el.get_text(strip=True)
                if name:
                    breadcrumbs.append(name)

    # Strategy 2: Fallback — look for any BreadcrumbList ol
    if not breadcrumbs:
        ol = soup.find(
            "ol",
            attrs={"itemtype": lambda v: v and "BreadcrumbList" in str(v)}
        )
        if ol:
            for li in ol.find_all("li"):
                name_el = li.find("span", attrs={"itemprop": "name"})
                if name_el:
                    name = name_el.get_text(strip=True)
                    if name:
                        breadcrumbs.append(name)

    if not breadcrumbs:
        return []

    # Remove "Home" (first item) and the product title (last item)
    # The last breadcrumb is typically the product name, not a category
    if breadcrumbs and breadcrumbs[0].lower() == "home":
        breadcrumbs = breadcrumbs[1:]
    if len(breadcrumbs) > 1:
        # Remove the last item (product title)
        breadcrumbs = breadcrumbs[:-1]

    return breadcrumbs


def extract_specifications(soup):
    """
    Extract the specification table from the Zoro product page.
    Returns a dict like {"Voltage": "120V", "Wattage": "15W"}.
    """
    specs = {}

    # Zoro uses a spec table with rows of label/value pairs
    spec_tables = soup.find_all("table")
    for table in spec_tables:
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True)
                value = cells[1].get_text(strip=True)
                if key and value:
                    specs[key] = value

    # Also try definition-list style specs (<dl><dt>...<dd>...)
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            key = dt.get_text(strip=True)
            value = dd.get_text(strip=True)
            if key and value:
                specs[key] = value

    return specs


def extract_all_images(product_json):
    """Extract all image URLs from the LD+JSON data."""
    images = []
    raw_images = product_json.get("image", [])

    if isinstance(raw_images, list):
        for img in raw_images:
            if isinstance(img, dict):
                url = img.get("contentUrl") or img.get("url")
                if url:
                    images.append(url)
            elif isinstance(img, str):
                images.append(img)
    elif isinstance(raw_images, str):
        images.append(raw_images)

    return images


def clean_availability(raw):
    """Convert schema.org availability URL to readable string."""
    if not raw:
        return "Unknown"
    # "http://schema.org/InStock" → "In Stock"
    status = raw.rsplit("/", 1)[-1]
    # CamelCase → spaced: "InStock" → "In Stock"
    result = ""
    for char in status:
        if char.isupper() and result:
            result += " "
        result += char
    return result


def upsert_category_tree(category_names):
    """
    Given a list like ["Safety", "Gloves & Hand Protection", "Cut-Resistant Gloves"],
    create/find each category in the tree and return the deepest category's ID.

    Creates parent → child relationships automatically.
    """
    if not category_names:
        return None

    parent_id = None

    for name in category_names:
        # Check if this category already exists under this parent
        existing = Category.query.filter_by(
            category_name=name,
            parent_category_id=parent_id,
        ).first()

        if existing:
            parent_id = existing.category_id
        else:
            new_cat = Category(
                category_name=name,
                parent_category_id=parent_id,
            )
            db.session.add(new_cat)
            db.session.flush()  # Get the ID
            parent_id = new_cat.category_id
            print(f"    📁 Created category: {name} (parent={new_cat.parent_category_id})")

    return parent_id


def scrape_and_save():
    """Main scrape loop: fetch pages, extract data, save to DB + JSON."""

    os.makedirs("products", exist_ok=True)

    saved = 0
    failed = 0

    with app.app_context():
        for url in urls:
            try:
                print(f"\n📥 Scraping: {url}")
                response = requests.get(url, headers=headers, timeout=30)

                if response.status_code != 200:
                    print(f"  ❌ HTTP {response.status_code}")
                    failed += 1
                    continue

                soup = BeautifulSoup(response.text, "lxml")

                # 1. Extract LD+JSON structured data
                product_json = extract_ld_json(soup)
                if product_json is None:
                    print(f"  ❌ No Product LD+JSON found")
                    failed += 1
                    continue

                zoro_code = product_json.get("sku", "")
                if not zoro_code:
                    print(f"  ❌ No SKU/zoro_code found")
                    failed += 1
                    continue

                # 2. Extract specifications from HTML tables
                specs = extract_specifications(soup)

                # 3. Extract category breadcrumbs
                category_path = extract_breadcrumbs(soup)
                print(f"  🏷️  Categories: {' / '.join(category_path) if category_path else 'None found'}")

                # 4. Create/find the category tree and get the deepest ID
                category_id = upsert_category_tree(category_path)

                # 5. Extract all images
                images = extract_all_images(product_json)
                primary_image = images[0] if images else ""

                # 6. Build product data
                price_str = product_json.get("offers", {}).get("price", "0")
                try:
                    price = float(price_str)
                except (ValueError, TypeError):
                    price = 0.0

                product_data = {
                    "zoro_code": zoro_code,
                    "title": product_json.get("name", ""),
                    "brand": product_json.get("brand", {}).get("name", ""),
                    "price": price_str,
                    "unit_price": price,
                    "availability": clean_availability(
                        product_json.get("offers", {}).get("availability")
                    ),
                    "mfr_number": product_json.get("mpn", ""),
                    "upc": product_json.get("gtin12", ""),
                    "description": product_json.get("description", ""),
                    "image_url": primary_image,
                    "product_url": product_json.get(
                        "offers", {}
                    ).get("url", url),
                    "specifications": specs,
                    "images": images,
                    "category_path": category_path,
                }

                # 7. Save to JSON file (backup)
                filename = f'products/{zoro_code}.json'
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(product_data, f, indent=4, ensure_ascii=False)

                # 8. Upsert into PostgreSQL
                existing = Product.query.filter_by(
                    zoro_code=zoro_code
                ).first()

                if existing:
                    # Update existing product
                    existing.title = product_data["title"]
                    existing.brand = product_data["brand"]
                    existing.unit_price = price
                    existing.availability = product_data["availability"]
                    existing.mfr_number = product_data["mfr_number"]
                    existing.upc = product_data["upc"]
                    existing.description = product_data["description"]
                    existing.image_url = primary_image
                    existing.product_url = product_data["product_url"]
                    existing.specifications = specs
                    existing.category_id = category_id
                    product_obj = existing
                    print(f"  🔄 Updated: {zoro_code}")
                else:
                    # Insert new product
                    product_obj = Product(
                        zoro_code=zoro_code,
                        title=product_data["title"],
                        brand=product_data["brand"],
                        unit_price=price,
                        availability=product_data["availability"],
                        mfr_number=product_data["mfr_number"],
                        upc=product_data["upc"],
                        description=product_data["description"],
                        image_url=primary_image,
                        product_url=product_data["product_url"],
                        specifications=specs,
                        category_id=category_id,
                    )
                    db.session.add(product_obj)
                    print(f"  ✅ Inserted: {zoro_code}")

                db.session.flush()  # Get product_id

                # 9. Upsert images
                if images:
                    # Remove old images for this product
                    ProductImage.query.filter_by(
                        product_id=product_obj.product_id
                    ).delete()

                    for img_url in images:
                        db.session.add(ProductImage(
                            product_id=product_obj.product_id,
                            image_url=img_url,
                        ))

                db.session.commit()
                saved += 1

                print(f"  📋 Specs: {len(specs)} attributes")
                print(f"  🖼️  Images: {len(images)}")

            except Exception as e:
                db.session.rollback()
                print(f"  ❌ Error: {e}")
                failed += 1
                continue

    print(f"\n{'='*50}")
    print(f"✅ Saved: {saved}  |  ❌ Failed: {failed}")
    print(f"{'='*50}")


if __name__ == "__main__":
    scrape_and_save()