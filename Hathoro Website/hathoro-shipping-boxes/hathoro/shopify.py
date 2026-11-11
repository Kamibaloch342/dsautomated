"""Shopify product CSV rows in the current product template format (one row per image).

Column names follow help.shopify.com/en/manual/products/import-export/using-csv/csv-columns.
The optional "Collection" column puts the product in a collection; for an automated
collection the product must also meet its conditions (here: Product category).
"""

import re

from .data import fmt_dim
from .design import ROOT

COLUMNS = [
    "Title", "URL handle", "Description", "Vendor", "Product category", "Type", "Tags",
    "Published on online store", "Status", "SKU", "Barcodes", "Option1 name", "Option1 value",
    "Price", "Compare-at price", "Cost per item", "Charge tax", "Inventory tracker",
    "Inventory quantity", "Continue selling when out of stock", "Weight value (grams)",
    "Weight unit for display", "Packed product length", "Packed product width",
    "Packed product height", "Packed product dimension unit", "Requires shipping",
    "Fulfillment service", "Product image URL", "Image position", "Image alt text",
    "Gift card", "SEO title", "SEO description", "Collection",
]

LB_TO_G = 453.592


def listing_fields(li):
    """Template fields for titles, tags and the description (images use li.fields())."""
    f = li.fields()
    strength_items = strength_rows = ""
    if f["rating"]:
        test = "Edge crush test" if li.ect else "Burst strength (Mullen)"
        strength_items += f"<li><strong>{test}:</strong> {f['rating']}</li>\n"
        strength_rows += f"<tr><td>{test}</td><td>{f['rating']}</td></tr>\n"
    if li.max_lbs:
        strength_items += (f"<li><strong>Box maker's limits:</strong> up to {li.max_lbs} lb "
                           f"gross weight, {li.size_limit} in max L + W + H</li>\n")
        strength_rows += f"<tr><td>Max gross weight</td><td>{li.max_lbs} lb</td></tr>\n"
    style_items = style_rows = ""
    if li.score_depths:
        cuts = ", ".join(f'{fmt_dim(d)}"' for d in li.score_depths)
        style_items = (f"<li><strong>Multi-depth:</strong> scored at {cuts} so it cuts down "
                       "to the height you need</li>\n")
        style_rows = f"<tr><td>Cut-down heights</td><td>{cuts}</td></tr>\n"
    elif li.multi_depth:
        style_items = ("<li><strong>Multi-depth:</strong> scored so it cuts down to a lower "
                       "height</li>\n")
    kraft = li.color == "Kraft"
    rating_slug = f"{li.ect}-ect" if li.ect else f"{li.burst}-lb-test" if li.burst else ""
    title_style = ("" if kraft else f"{li.color} ") + ("Multi-Depth " if li.multi_depth else "")
    f.update({
        "LxWxH_upper": f["LxWxH"].upper(),
        "wall_lower": f["wall"].lower(),
        # every attribute that tells two Zoro boxes apart goes into the handle
        "handle_extra": "-".join(x for x in ("" if kraft else li.color,
                                             "multi-depth" if li.multi_depth else "",
                                             f["wall"], rating_slug) if x),
        "title_style": title_style,
        "title_style_lower": title_style.lower(),
        "color_lower": li.color.lower(),
        "color_text": "Kraft (brown)" if kraft else li.color,
        "color_tag": "" if kraft else f"{li.color} Boxes",
        "style_tag": "Multi-Depth Boxes" if li.multi_depth else "",
        "style_items": style_items,
        "style_rows": style_rows,
        "title_suffix": f" - {f['wall']}, {f['rating']}" if f["rating"] else f" - {f['wall']}",
        "rating_seo": f", {f['rating']}" if f["rating"] else "",
        "strength_items": strength_items,
        "strength_rows": strength_rows,
    })
    return f


def make_handle(li, settings):
    """Shopify URL handle: lowercase letters, digits and hyphens only."""
    raw = settings["shopify"]["handle"].format(**listing_fields(li)).lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", raw)).strip("-")


def image_url(settings, handle, file_name):
    base = (settings.get("image_base_url") or "").rstrip("/")
    if not base:
        return ""
    return settings.get("image_url", "{base}/{file}").format(base=base, handle=handle,
                                                              file=file_name)


def _num(v):
    return f"{float(v):g}" if v not in (None, "") else ""


def product_rows(li, price, image_files, settings, status=None):
    """CSV rows for one product. image_files: [(image name, file name)] in gallery order.
    Without an image_base_url the product row is written with no images."""
    sh = settings["shopify"]
    f = listing_fields(li)
    handle = make_handle(li, settings)
    body = (ROOT / sh["body_template"]).read_text().format(**f).strip()
    tags = [t.format(**f) for t in sh["tags"]]
    tags = ", ".join(dict.fromkeys(t for t in tags if t.strip()))
    grams = round(float(li.ship_pack_weight) * LB_TO_G) if li.ship_pack_weight else ""
    tf = lambda v: "true" if v else "false"  # noqa: E731
    has_dims = all(v not in (None, "", 0) for v in
                   (li.ship_pack_length, li.ship_pack_width, li.ship_pack_height))

    product = dict.fromkeys(COLUMNS, "")
    product.update({
        "Title": sh["title"].format(**f),
        "URL handle": handle,
        "Description": body,
        "Vendor": sh["vendor"],
        "Product category": sh["product_category"],
        "Type": sh["type"],
        "Tags": tags,
        "Published on online store": tf(sh["published"]),
        "Status": status or sh["status"],
        "SKU": sh["sku"].format(**f),
        "Barcodes": li.upc_code or "",
        "Option1 name": sh["option1_name"],
        "Option1 value": sh["option1_value"].format(**f),
        "Price": f"{price.price:.2f}",
        "Compare-at price": f"{price.compare_at:.2f}" if price.compare_at else "",
        "Cost per item": f"{price.landed_cost:.2f}",
        "Charge tax": tf(sh["taxable"]),
        "Inventory tracker": sh["inventory_tracker"],
        "Inventory quantity": sh["inventory_qty"],
        "Continue selling when out of stock": sh["inventory_policy"],
        "Weight value (grams)": grams,
        "Weight unit for display": "lb",
        "Packed product length": _num(li.ship_pack_length) if has_dims else "",
        "Packed product width": _num(li.ship_pack_width) if has_dims else "",
        "Packed product height": _num(li.ship_pack_height) if has_dims else "",
        "Packed product dimension unit": "in" if has_dims else "",
        "Requires shipping": tf(sh["requires_shipping"]),
        "Fulfillment service": sh["fulfillment_service"],
        "Gift card": "false",
        "SEO title": sh["seo_title"].format(**f),
        "SEO description": sh["seo_description"].format(**f),
        "Collection": sh.get("collection", ""),
    })

    rows = [product]
    pos = 0
    for name, file_name in image_files:
        url = image_url(settings, handle, file_name)
        if not url:
            continue
        pos += 1
        row = product if pos == 1 else dict.fromkeys(COLUMNS, "")
        row.update({
            "URL handle": handle,
            "Product image URL": url,
            "Image position": pos,
            "Image alt text": sh["image_alt"].get(name, "").format(**f),
        })
        if pos > 1:
            rows.append(row)
    return handle, rows
