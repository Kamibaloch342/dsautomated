"""
Zoro page-dump extractor
────────────────────────
zoro.com pages embed their full Vuex state (window.__NUXT__/INITIAL_STATE);
`zoro_dump.json` is one of those dumps. The old pipeline only kept
title/price/image — this module extracts EVERYTHING the page knows:

  • attributes            → products.specifications (JSONB)   ← the spec sheet
  • primaryCategoryPaths  → full 4-level category tree
  • price / priceUnit / minRetailQty / packageQty / originalPrice
  • leadTime, isDropShipDirect, isLTL, isHazmat, restrictedStates,
    salesStatus, erpId, description
  • variants              → variant_group + variant_label per member

Usage:
    ./venv_mac/bin/python extract_from_dump.py [dump1.json dump2.json ...]
    (defaults to zoro_dump.json)

Idempotent — re-running just re-applies the same values.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app import app
from database import db
from models import Product, Category, SiteSetting


# ─────────────────────────────────────────────
#  Parsing (pure — no DB)
# ─────────────────────────────────────────────

def _attributes_to_specs(attributes):
    """[{name, value}, ...] → ordered {name: value} dict."""
    specs = {}
    for attr in attributes or []:
        name = (attr.get("name") or "").strip()
        value = attr.get("value")
        if name and value not in (None, ""):
            specs[name] = str(value).strip()
    return specs


def _category_names(entry):
    """primaryCategoryPaths → ["Safety", "PPE", ...] (root → leaf)."""
    names = []
    for level in entry.get("primaryCategoryPaths") or []:
        name = (level.get("2") or "").strip()
        if name:
            names.append(name)
    return names


def parse_product_entry(entry):
    """One productDetailsCache entry → normalized record dict."""
    record = {
        "zoro_code": entry.get("zoroNo"),
        "title": entry.get("title"),
        "brand": entry.get("brand"),
        "mfr_number": entry.get("mfrNo"),
        "upc": entry.get("upc"),
        "description": entry.get("description"),
        "unit_price": entry.get("price"),
        "original_price": entry.get("originalPrice"),
        "uom": entry.get("priceUnit"),
        "min_order_qty": entry.get("minRetailQty") or 1,
        "package_qty": entry.get("packageQty") or 1,
        "erp_id": str(entry.get("erpId") or "") or None,
        "sales_status": entry.get("salesStatus"),
        "is_dropship": bool(entry.get("isDropShipDirect")),
        "is_ltl": bool(entry.get("isLTL")),
        "is_hazmat": bool(entry.get("isHazmat")),
        "restricted_states": entry.get("restrictedStates") or [],
        "lead_time": (
            f"Ships in {entry['leadTime']} day{'s' if entry['leadTime'] != 1 else ''}"
            if entry.get("leadTime") is not None else None
        ),
        "specifications": _attributes_to_specs(entry.get("attributes")),
        "category_names": _category_names(entry),
        "variant_group": None,
        "variant_label": None,
        "variant_members": [],   # [{zoro_code, label, price, ...}]
    }

    # Variant group: zoro links size/pack siblings with a shared label
    variants = entry.get("variants") or {}
    option_names = variants.get("attributes") or []
    group_label = variants.get("label")
    if group_label and variants.get("products"):
        record["variant_group"] = group_label
        option = option_names[0] if option_names else None
        if option and record["specifications"].get(option):
            record["variant_label"] = record["specifications"][option]
        for member in variants["products"]:
            m_attrs = _attributes_to_specs(member.get("attributes"))
            label = m_attrs.get(option) if option else None
            record["variant_members"].append({
                "zoro_code": member.get("zoroNo"),
                "title": member.get("title"),
                "brand": member.get("brand"),
                "mfr_number": member.get("mfrNo"),
                "erp_id": str(member.get("erpId") or "") or None,
                "unit_price": member.get("price"),
                "original_price": member.get("originalPrice"),
                "uom": member.get("priceUnit"),
                "min_order_qty": member.get("minRetailQty") or 1,
                "variant_label": label,
                "option_name": option,
            })
    return record


def parse_dump(dump):
    """Full Vuex dump dict → list of normalized product records."""
    cache = (dump.get("productStore") or {}).get("productDetailsCache") or {}
    return [parse_product_entry(e) for e in cache.values() if e.get("zoroNo")]


def parse_ribbon(dump):
    """messagingRibbon → [{message, cta_text, cta_path}] for the topbar."""
    content = (dump.get("messagingRibbon") or {}).get("content") or []
    messages = []
    for item in content:
        cta = item.get("callToAction") or {}
        messages.append({
            "message": (item.get("message") or "").strip(),
            "cta_text": (cta.get("text") or "").strip(),
            "cta_path": (cta.get("urlPath") or "").strip(),
        })
    return [m for m in messages if m["message"]]


# ─────────────────────────────────────────────
#  DB application
# ─────────────────────────────────────────────

def upsert_category_tree(names):
    """["Safety", "PPE", ...] root→leaf; returns deepest category_id."""
    parent_id = None
    for name in names:
        cat = Category.query.filter_by(
            category_name=name, parent_category_id=parent_id
        ).first()
        if cat is None:
            cat = Category(category_name=name, parent_category_id=parent_id)
            db.session.add(cat)
            db.session.flush()
        parent_id = cat.category_id
    return parent_id


SIMPLE_FIELDS = [
    "brand", "mfr_number", "upc", "description", "original_price",
    "uom", "min_order_qty", "package_qty", "erp_id", "sales_status",
    "is_dropship", "is_ltl", "is_hazmat", "restricted_states", "lead_time",
]


def apply_record(record, update_price=True):
    """Upsert one normalized record. Returns (product, created?)."""
    product = Product.query.filter_by(zoro_code=record["zoro_code"]).first()
    created = False
    if product is None:
        product = Product(zoro_code=record["zoro_code"], title=record["title"] or "")
        db.session.add(product)
        created = True

    if record.get("title"):
        product.title = record["title"]
    for field in SIMPLE_FIELDS:
        value = record.get(field)
        if value not in (None, "", []):
            setattr(product, field, value)
    if update_price and record.get("unit_price") is not None:
        product.unit_price = record["unit_price"]

    # Merge specs — dump attributes win over anything partial
    if record.get("specifications"):
        merged = dict(product.specifications or {})
        merged.update(record["specifications"])
        product.specifications = merged

    if record.get("category_names"):
        product.category_id = upsert_category_tree(record["category_names"])

    if record.get("variant_group"):
        product.variant_group = record["variant_group"]
    if record.get("variant_label"):
        product.variant_label = record["variant_label"]

    return product, created


def propagate_to_variant_members(base, record):
    """Copy the base product's rich data onto variant siblings already in
    the DB (matched by variant_group), adjusting the option attribute."""
    if not base.variant_group:
        return 0
    option = None
    for member in record.get("variant_members", []):
        option = member.get("option_name") or option

    members = Product.query.filter(
        Product.variant_group.in_([base.variant_group, record.get("old_group") or ""]),
        Product.zoro_code != base.zoro_code,
    ).all()
    count = 0
    for m in members:
        specs = dict(base.specifications or {})
        if option and m.variant_label:
            specs[option] = m.variant_label
        # keep any member-specific keys that already existed
        for k, v in (m.specifications or {}).items():
            specs.setdefault(k, v)
        m.specifications = specs
        m.category_id = base.category_id
        m.description = m.description or base.description
        m.uom = base.uom
        m.min_order_qty = base.min_order_qty
        m.package_qty = base.package_qty
        m.lead_time = base.lead_time
        m.is_dropship = base.is_dropship
        m.is_ltl = base.is_ltl
        m.is_hazmat = base.is_hazmat
        m.restricted_states = base.restricted_states
        m.sales_status = base.sales_status
        m.variant_group = base.variant_group
        count += 1
    return count


def run(paths):
    total_specs = 0
    with app.app_context():
        for path in paths:
            with open(path) as f:
                dump = json.load(f)

            ribbon = parse_ribbon(dump)
            if ribbon:
                SiteSetting.set("messaging_ribbon", ribbon)
                print(f"{path}: messaging ribbon extracted ({len(ribbon)} messages)")

            records = parse_dump(dump)
            print(f"{path}: {len(records)} product(s) in dump")
            for record in records:
                # remember the group the seeder may have used before
                existing = Product.query.filter_by(zoro_code=record["zoro_code"]).first()
                record["old_group"] = existing.variant_group if existing else None

                product, created = apply_record(record)
                db.session.flush()
                siblings = propagate_to_variant_members(product, record)
                db.session.commit()
                n = len(record["specifications"])
                total_specs += n
                print(f"  {'＋created' if created else '✓updated'} {product.zoro_code}: "
                      f"{n} specs, cat={ '>'.join(record['category_names']) or '—' }, "
                      f"price={record['unit_price']}/{record['uom']}, "
                      f"min={record['min_order_qty']}, pkg={record['package_qty']}, "
                      f"{siblings} variant sibling(s) enriched")
    print(f"\nDone — {total_specs} spec values extracted.")


if __name__ == "__main__":
    dump_paths = sys.argv[1:] or ["zoro_dump.json"]
    run(dump_paths)
