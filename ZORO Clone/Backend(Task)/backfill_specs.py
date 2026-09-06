"""
Spec backfill for products without a page dump
──────────────────────────────────────────────
The scraper's products/*.json files were saved with EMPTY `attributes`,
so most catalog items have no specification sheet. Until each product is
re-scraped (extract_from_dump.py handles any new page dumps), this script
fills the gap with attributes derived from data we actually have — the
product title, TSV import fields and brand — structured the way zoro.com
presents them.

Existing spec keys are never overwritten (dump-extracted values win).

Usage:  ./venv_mac/bin/python backfill_specs.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app import app
from database import db
from models import Product

# Title-derived attribute sheets for the original catalog (facts present
# in the product title / import data, structured zoro-style).
CURATED = {
    # Milwaukee SHOCKWAVE deep well socket family (option: Socket Size)
    "milwaukee-shockwave-deepwell": {
        "Item": "Deep Well Impact Socket",
        "Series": "SHOCKWAVE Impact Duty",
        "Drive Size": "1/2 in",
        "Socket Style": "6-Point",
        "Socket Depth": "Deep",
        "Impact Rated": "Yes",
        "Material": "Forged Steel",
        "Finish": "Black Phosphate",
    },
    # Southwire SJTW extension cord family (option: Cord Length)
    "southwire-sjtw-cord": {
        "Item": "Extension Cord",
        "Cord Type": "SJTW",
        "Wire Gauge": "12 AWG",
        "Conductors": "12/3",
        "Number of Outlets": "1",
        "Plug Configuration": "NEMA 5-15P",
        "Receptacle Configuration": "NEMA 5-15R",
        "Jacket Material": "PVC",
        "Color": "Blue/Red",
        "Environment": "Outdoor",
    },
    # GE LED lamp family (option: Pack Size)
    "ge-led-cylindrical": {
        "Item": "LED Lamp",
        "Shape": "Cylindrical",
        "Housing Material": "Polycarbonate",
        "Technology": "LED",
    },
    # Amerex ABC extinguisher family (option: Capacity)
    "amerex-abc-extinguisher": {
        "Item": "Fire Extinguisher",
        "Class": "ABC",
        "UL Rating": "4A:80B:C",
        "Rechargeable": "Yes",
        "Discharge Range": "21 ft",
        "Agent Type": "Dry Chemical",
    },
    # Individual products (keyed by zoro_code)
    "G3268772": {
        "Item": "Rebar Cutter",
        "Cutting Capacity": "3/4 in",
        "Overall Length": "12 in",
        "Shank Type": "SDS Plus",
        "Application": "Rebar Cutting",
    },
    "G4619501": {
        "Item": "Drill & Tap Set",
        "Set Type": "All Purpose",
        "Application": "Drilling and Tapping",
    },
    "G7638467": {
        "Item": "Germicidal Bleach",
        "Container Type": "Bottle",
        "Container Size": "1 gal",
        "Formula": "Concentrated",
        "Color": "Clear",
        "Fragrance": "Fragrance-Free",
        "Package Quantity": "6PK",
    },
    "G0251377": {
        "Item": "First Aid Kit",
        "Serves": "25 People",
        "Number of Components": "107",
        "OSHA Compliant": "Yes",
        "Case Material": "Plastic",
    },
    "G9783277": {
        "Item": "Fire Extinguisher",
        "Class": "A",
        "UL Rating": "2A",
        "Capacity": "2.5 gal",
        "Agent Type": "Water",
        "Range": "48 ft",
    },
    "G7586896": {
        "Item": "Shipping Box",
        "Inside Length": "12 in",
        "Inside Width": "12 in",
        "Inside Height": "4 in",
        "Material": "Corrugated Kraft",
        "Wall Type": "Single Wall",
        "Edge Crush Test": "32 ECT",
    },
    "G1202564": {
        "Item": "Tape Measure",
        "Series": "FATMAX Classic",
        "Blade Length": "25 ft",
        "Blade Width": "1-1/4 in",
        "Blade Markings": "Stud Markings",
        "Case Material": "ABS Plastic",
        "Grip Type": "Rubber",
    },
}


def baseline_specs(p):
    """Attributes every product can state from import data."""
    specs = {}
    if p.brand:
        specs["Brand"] = p.brand
    if p.mfr_number:
        specs["Mfr. Model Number"] = p.mfr_number
    if p.upc:
        specs["UPC"] = p.upc
    if p.country_of_origin:
        specs["Country of Origin"] = p.country_of_origin
    if p.uom:
        specs["Unit of Measure"] = p.uom
    if p.min_order_qty and p.min_order_qty > 1:
        specs["Minimum Order Quantity"] = str(p.min_order_qty)
    if p.ship_pack_weight:
        specs["Shipping Weight"] = f"{float(p.ship_pack_weight):g} lb"
    if p.ship_pack_length and p.ship_pack_width and p.ship_pack_height:
        specs["Package Dimensions"] = (
            f"{float(p.ship_pack_length):g} x {float(p.ship_pack_width):g} "
            f"x {float(p.ship_pack_height):g} in"
        )
    return specs


def run():
    updated = 0
    with app.app_context():
        for p in Product.query.all():
            curated = CURATED.get(p.variant_group) or CURATED.get(p.zoro_code) or {}
            merged = {}
            merged.update(curated)
            merged.update(baseline_specs(p))
            # existing values (e.g. dump-extracted, option labels) win
            merged.update(p.specifications or {})
            if merged != (p.specifications or {}):
                p.specifications = merged
                updated += 1
                print(f"  ✓ {p.zoro_code}: {len(merged)} specs "
                      f"({len(curated)} curated + baseline)")
        db.session.commit()
    print(f"\nDone — {updated} product(s) backfilled.")


if __name__ == "__main__":
    run()
