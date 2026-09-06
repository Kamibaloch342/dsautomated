"""
Seed Script — Populate PostgreSQL from existing JSON files
──────────────────────────────────────────────────────────
Reads the scraped JSON files in products/ and inserts
them into the database so you can use the API immediately
without needing to re-scrape.

Now also handles category_path to build the category tree.

Usage:
    python seed_from_json.py
"""

import os
import json
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app import app
from database import db
from models import Product, ProductImage, Category


def clean_availability(raw):
    """Convert schema.org availability URL to readable string."""
    if not raw:
        return "Unknown"
    if raw.startswith("http"):
        status = raw.rsplit("/", 1)[-1]
        result = ""
        for char in status:
            if char.isupper() and result:
                result += " "
            result += char
        return result
    return raw


def upsert_category_tree(category_names):
    """
    Given a list like ["Safety", "Gloves & Hand Protection", "Cut-Resistant Gloves"],
    create/find each category in the tree and return the deepest category's ID.
    """
    if not category_names:
        return None

    parent_id = None

    for name in category_names:
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
            db.session.flush()
            parent_id = new_cat.category_id
            print(f"    📁 Created category: {name}")

    return parent_id


def seed():
    products_dir = os.path.join(os.path.dirname(__file__), "products")

    if not os.path.isdir(products_dir):
        print("❌ No products/ directory found. Run scraper.py first.")
        return

    json_files = [
        f for f in os.listdir(products_dir) if f.endswith(".json")
    ]

    if not json_files:
        print("❌ No JSON files found in products/")
        return

    print(f"📦 Found {len(json_files)} product files\n")

    inserted = 0
    updated = 0
    errors = 0

    with app.app_context():
        # Ensure tables exist
        db.create_all()

        for filename in sorted(json_files):
            filepath = os.path.join(products_dir, filename)

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)

                zoro_code = data.get("zoro_code", "")
                if not zoro_code:
                    print(f"  ⚠️  Skipping {filename}: no zoro_code")
                    continue

                # Parse price
                price_raw = data.get("price") or data.get("unit_price", 0)
                try:
                    price = float(price_raw)
                except (ValueError, TypeError):
                    price = 0.0

                # Get specs (might be "attributes" or "specifications")
                specs = (
                    data.get("specifications")
                    or data.get("attributes")
                    or {}
                )

                # Clean availability
                availability = clean_availability(
                    data.get("availability", "")
                )

                # ── Handle category_path ──
                category_path = data.get("category_path", [])
                category_id = upsert_category_tree(category_path)

                # Check for existing
                existing = Product.query.filter_by(
                    zoro_code=zoro_code
                ).first()

                if existing:
                    existing.title = data.get("title", "")
                    existing.brand = data.get("brand", "")
                    existing.unit_price = price
                    existing.availability = availability
                    existing.mfr_number = data.get("mfr_number", "")
                    existing.upc = data.get("upc", "")
                    existing.description = data.get("description", "")
                    existing.image_url = data.get("image_url", "")
                    existing.product_url = data.get("product_url", "")
                    existing.specifications = specs
                    existing.category_id = category_id
                    product_obj = existing
                    updated += 1
                    print(f"  🔄 Updated: {zoro_code} — {data.get('title', '')[:50]}")
                else:
                    product_obj = Product(
                        zoro_code=zoro_code,
                        title=data.get("title", ""),
                        brand=data.get("brand", ""),
                        unit_price=price,
                        availability=availability,
                        mfr_number=data.get("mfr_number", ""),
                        upc=data.get("upc", ""),
                        description=data.get("description", ""),
                        image_url=data.get("image_url", ""),
                        product_url=data.get("product_url", ""),
                        specifications=specs,
                        category_id=category_id,
                    )
                    db.session.add(product_obj)
                    inserted += 1
                    print(f"  ✅ Inserted: {zoro_code} — {data.get('title', '')[:50]}")

                db.session.flush()

                # Add images if present
                images = data.get("images", [])
                if not images and data.get("image_url"):
                    images = [data["image_url"]]

                if images:
                    ProductImage.query.filter_by(
                        product_id=product_obj.product_id
                    ).delete()
                    for img_url in images:
                        db.session.add(ProductImage(
                            product_id=product_obj.product_id,
                            image_url=img_url,
                        ))

            except Exception as e:
                print(f"  ❌ Error with {filename}: {e}")
                errors += 1
                db.session.rollback()
                continue

        db.session.commit()

    print(f"\n{'='*50}")
    print(f"✅ Inserted: {inserted}  |  🔄 Updated: {updated}  |  ❌ Errors: {errors}")
    print(f"{'='*50}")


if __name__ == "__main__":
    seed()
