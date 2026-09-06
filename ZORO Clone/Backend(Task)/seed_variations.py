"""
Seed variation families
───────────────────────
Creates size / length / pack variations for existing catalog products so
the "Variations & price comparison" features have real data. Idempotent:
a family is skipped if its variant_group already has more than one member.

Run:  ./venv_mac/bin/python seed_variations.py
"""

import random
import sys

from app import app
from database import db
from models import Product

# base zoro_code → (group name, label for base, [(label, price, title_transform), ...])
FAMILIES = {
    # Milwaukee SHOCKWAVE deep well sockets — drive sizes
    "G501259447": {
        "group": "milwaukee-shockwave-deepwell",
        "base_label": '1-1/2 in',
        "option_name": "Socket Size",
        "variants": [
            ('7/16 in',  13.49),
            ('9/16 in',  15.79),
            ('3/4 in',   18.99),
            ('1 in',     21.49),
            ('1-1/8 in', 27.99),
        ],
        "title_swap": ("1-1/2 in.", "{label}."),
    },
    # Southwire SJTW extension cord — lengths
    "G1395266": {
        "group": "southwire-sjtw-cord",
        "base_label": "25 ft",
        "option_name": "Cord Length",
        "variants": [
            ("50 ft",  92.49),
            ("100 ft", 148.99),
        ],
        "title_swap": None,  # append label instead
    },
    # Azusa cut-resistant gloves — sizes
    "G7804513": {
        "group": "azusa-a4-gloves",
        "base_label": "M",
        "option_name": "Glove Size",
        "variants": [
            ("S",   9.75),
            ("L",   9.99),
            ("XL",  10.49),
            ("2XL", 11.25),
        ],
        "title_swap": None,
    },
    # GE LED lamps — pack sizes
    "G6637946": {
        "group": "ge-led-cylindrical",
        "base_label": "PK2",
        "option_name": "Pack Size",
        "variants": [
            ("PK4", 19.79),
            ("PK6", 28.49),
        ],
        "title_swap": ("PK2", "{label}"),
    },
    # Amerex ABC fire extinguishers — capacities
    "G3933237": {
        "group": "amerex-abc-extinguisher",
        "base_label": "5 lb",
        "option_name": "Capacity",
        "variants": [
            ("2.5 lb", 64.99),
            ("10 lb",  156.49),
        ],
        "title_swap": None,
    },
}


def _unique_code(existing):
    while True:
        code = "G" + "".join(random.choices("0123456789", k=7))
        if code not in existing and not Product.query.filter_by(zoro_code=code).first():
            existing.add(code)
            return code


def seed():
    created, updated = 0, 0
    new_codes = set()

    for base_code, fam in FAMILIES.items():
        base = Product.query.filter_by(zoro_code=base_code).first()
        if base is None:
            print(f"  ⚠ base {base_code} not in catalog — skipped")
            continue

        existing = Product.query.filter_by(variant_group=fam["group"]).count()
        if existing > 1:
            print(f"  ✓ {fam['group']} already seeded ({existing} members) — skipped")
            continue

        # Tag the base product
        base.variant_group = fam["group"]
        base.variant_label = fam["base_label"]
        specs = dict(base.specifications or {})
        specs[fam["option_name"]] = fam["base_label"]
        base.specifications = specs
        updated += 1

        for label, price in fam["variants"]:
            if fam["title_swap"]:
                old, new = fam["title_swap"]
                title = base.title.replace(old, new.format(label=label))
            else:
                title = f"{base.title}, {label}"

            specs = dict(base.specifications or {})
            specs[fam["option_name"]] = label

            variant = Product(
                zoro_code=_unique_code(new_codes),
                title=title,
                brand=base.brand,
                unit_price=price,
                availability=base.availability or "In Stock",
                category_id=base.category_id,
                mfr_number=(base.mfr_number or "") + "-" + label.replace(" ", "").replace("/", ""),
                upc=None,
                country_of_origin=base.country_of_origin,
                image_url=base.image_url,
                product_url=base.product_url,
                description=base.description,
                lead_time=base.lead_time,
                is_dropship=base.is_dropship,
                uom=base.uom,
                freight_surcharge=base.freight_surcharge,
                min_order_qty=base.min_order_qty,
                is_ltl=base.is_ltl,
                ship_pack_height=base.ship_pack_height,
                ship_pack_length=base.ship_pack_length,
                ship_pack_width=base.ship_pack_width,
                ship_pack_weight=base.ship_pack_weight,
                is_blind_ship=base.is_blind_ship,
                specifications=specs,
                variant_group=fam["group"],
                variant_label=label,
            )
            db.session.add(variant)
            created += 1
            print(f"  + {variant.zoro_code}  {label:<8} ${price:<8} {title[:60]}")

        db.session.commit()

    print(f"\nDone — {created} variant products created, {updated} base products tagged.")


if __name__ == "__main__":
    with app.app_context():
        seed()
