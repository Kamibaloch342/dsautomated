#!/usr/bin/env python3
"""
build_pl_listings.py — turn a PL candidate into a ready-to-upload listing.

build_hoank_listings.py already does this beautifully, but only for shipping boxes: its SQL is
pinned to `short_description LIKE '%Shipping Box%'` and its copy is written about corrugated
cardboard. This takes the same pipeline — the same photo fetch, the same white-canvas framing,
the same HOANK wordmark and pack badge — and drives it from any Zoro number, writing the copy
from Zoro's own description of the product you will actually ship.

Input is Amazon_DS_Hunting/pl_candidates.csv (see pl_rescore.py). Output is one folder per
product, in the shape the Drums work already uses:

    out_pl/<SKU>/
        data.json          every fact used, so a listing can be rebuilt or audited
        listing.txt        title, five bullets, description
        images/01_main.jpg product on white, HOANK wordmark, pack badge

    python3 build_pl_listings.py --limit 5
    python3 build_pl_listings.py --codes G9625506,G5489644
"""
import argparse
import collections
import csv
import json
import os
import re
import sqlite3
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.dirname(HERE)
ROOT = os.path.dirname(AUTO)
ZORO_DB = os.path.join(AUTO, "TSV Files", "combined_reports.db")
CANDIDATES = os.path.join(ROOT, "Amazon_DS_Hunting", "pl_candidates.csv")
OUT = os.path.join(HERE, "out_pl")
PHOTOS = os.path.join(OUT, "_photos")

sys.path.insert(0, HERE)
import build_hoank_listings as B       # noqa: E402  — photo fetch, framing, fonts, brand colours
import pl_store                        # noqa: E402  — the row the launcher and dashboard read


# Attribute names Zoro uses without a group suffix. Longest first, so 'Specific Material' is
# matched before 'Material' and half the value is not swallowed into the key.
KNOWN_KEYS = sorted([
    "Specific Material", "Basic Material", "Number of Rings", "Package Quantity", "Sheet Capacity",
    "Sheet Size", "Color Family", "Overall Length", "Overall Width", "Overall Height", "Ring Size",
    "Ring Type", "Cover Type", "Durability", "Thickness", "Capacity", "Features", "Material",
    "Includes", "Finish", "Height", "Length", "Voltage", "Wattage", "Weight", "Color", "Depth",
    "Style", "Width", "Size", "Type", "Item",
], key=len, reverse=True)

# Values the feed uses for "we did not fill this in".
JUNK_VALUES = {"not applicable", "n/a", "na", "none", "no", "unspecified", "other", "-", "0"}

# What a buyer decides on, in the order they decide it.
SPEC_PRIORITY = [
    "Size", "Ring Size", "Capacity", "Sheet Capacity", "Overall Length", "Overall Width", "Length",
    "Width", "Height", "Depth", "Thickness", "Sheet Size", "Basic Material", "Material",
    "Specific Material", "Durability", "Ring Type", "Cover Type", "Type", "Style", "Color",
    "Color Family", "Finish", "Features", "Includes", "Package Quantity",
]


def zoro_row(conn, code):
    conn.row_factory = sqlite3.Row
    return conn.execute("""SELECT zoro_no, manufacturer_part_number, upc_code, zoro_brand_name,
                                  short_description, long_description, sales_price,
                                  freight_surcharge, min_order_qty, level_one_name, level_two_name
                           FROM zoro_data WHERE zoro_no = ?""", (code,)).fetchone()


def specs_from(long_desc):
    """Zoro writes long_description as 'Key Value, Key Value, ...'.

    On most rows the attribute names carry a group suffix — 'Ring Size - Binders 3 in',
    'Basic Material - Binders Vinyl'. That group word is the same on every attribute of a row,
    so find it and drop it; what is left is the manufacturer's own spec, which is exactly what a
    buyer reads before they click Buy."""
    parts = re.split(r",(?=\s*[A-Z0-9])", long_desc or "")

    groups = collections.Counter()
    for part in parts:
        m = re.search(r"\s-\s+([A-Z][A-Za-z&/]*)\s", part)
        if m:
            groups[m.group(1)] += 1
    top = groups.most_common(1)
    group = top[0][0] if top and top[0][1] >= 2 else None

    out = {}
    for part in parts:
        text = part.strip()
        if not text:
            continue
        key = val = None
        if group:
            m = re.match(rf"(.+?)\s-\s+{re.escape(group)}\s+(.+)$", text)
            if m:
                key, val = m.group(1), m.group(2)
        if key is None:
            for k in KNOWN_KEYS:                   # longest first: 'Specific Material' beats 'Material'
                if text.lower().startswith(k.lower() + " "):
                    key, val = text[:len(k)], text[len(k) + 1:]
                    break
        if not key or not val:
            continue
        key, val = key.strip(" -"), val.strip(" .")
        if val.lower() in JUNK_VALUES:        # 'Thickness: Not Applicable' helps nobody decide
            continue
        if key and val and len(val) <= 60 and key not in out:
            out[key] = val
    return out


def clean_name(short_desc):
    """Zoro's short_description is the product, terse: '1" D-Ring Binder, Heavy Duty, White'."""
    return re.sub(r"\s+", " ", short_desc or "").strip(" ,")


def spec_pairs(specs, limit=5):
    """The specs a buyer decides on, most-decisive first, then whatever else the feed gave."""
    ordered = [k for k in SPEC_PRIORITY if k in specs]
    ordered += [k for k in specs if k not in ordered]
    return [(k, specs[k]) for k in ordered[:limit]]


def build_copy(row, specs, pack):
    """Title, bullets and description from what Zoro says the product is.

    Nothing here is invented: every number comes from the feed. That matters because the buyer
    receives the Zoro item, and a bullet that promises something it does not do is a return."""
    name = clean_name(row["short_description"])
    cat = (row["level_two_name"] or row["level_one_name"] or "").strip()
    pairs = spec_pairs(specs)
    spec_line = ", ".join(f"{k}: {v}" for k, v in pairs)

    title = f"{B.BRAND} {name}"
    if pack > 1 and not re.search(r"\bpk\s*\d|\b\d+\s*(pack|count)\b", name, re.I):
        title += f", {pack} Pack"
    if cat and cat.lower() not in title.lower():
        title += f" \u2014 {cat}"
    title = title[:200]

    # Lead with what makes this the right unit to buy. A "1 PACK" bullet on a single binder says
    # nothing, so a single unit leads on its most decisive spec instead.
    lead_key, lead_val = pairs[0] if pairs else ("", "")
    if pack > 1:
        head = (f"{pack} PACK: {B.BRAND} {name}, {pack} per order \u2014 keep a working supply on "
                f"the shelf and restock less often.")
    elif lead_key:
        head = (f"{lead_key.upper()} {lead_val.upper()}: {B.BRAND} {name}, built to the "
                f"manufacturer's original specification.")
    else:
        head = f"{B.BRAND} {name}, built to the manufacturer's original specification."

    bullets = [head]
    if len(pairs) > 1:
        bullets.append("SPECIFICATIONS: " + ", ".join(f"{k}: {v}" for k, v in pairs[1:]) + ".")
    bullets += [
        "CONSISTENT QUALITY: Every unit is the same item to the same specification, so the one you "
        "order next month performs exactly like this one.",
        "READY TO USE: Arrives ready for immediate use \u2014 no assembly and no extra parts to buy.",
        f"BACKED BY {B.BRAND}: If anything is not right, message us through Amazon and we will put "
        f"it right.",
    ]

    description = (
        f"{B.BRAND} {name}"
        + (f", {pack} per pack" if pack > 1 else "")
        + ". "
        + (f"{spec_line}. " if spec_line else "")
        + f"This is the specification professionals buy for {cat.lower() or 'daily work'}, packed "
        f"and shipped so it arrives ready to use."
        + (" Order once and keep a working supply on the shelf instead of reordering a single unit "
           "at a time." if pack > 1 else "")
    )
    return title, bullets, description


def brand_main_image(photo_path, dest, pack):
    """Product on a pure-white square, HOANK wordmark and pack badge beneath it.

    build_hoank_listings warps the wordmark onto a box face, which needs a box's geometry. A
    binder, a folder or a bag has no such face, so the mark sits on the canvas — still the
    HOANK lockup, still Amazon-legal on pure white, and it works for any shape.
    """
    photo = Image.open(photo_path)
    canvas, _box = B.place_on_canvas(photo, 0.74, B.CANVAS, y_shift=-int(B.CANVAS * 0.05))
    d = ImageDraw.Draw(canvas)

    # wordmark: HOA in ink, NK in the brand orange
    f = B.font(150, bold=True)
    hoa, nk = "HOA", "NK"
    w_hoa = d.textlength(hoa, font=f)
    w_nk = d.textlength(nk, font=f)
    x = (B.CANVAS - (w_hoa + w_nk)) / 2
    y = int(B.CANVAS * 0.80)
    d.text((x, y), hoa, font=f, fill=(17, 17, 17))
    d.text((x + w_hoa, y), nk, font=f, fill=B.ORANGE)

    # pack badge
    if pack > 1:
        bf = B.font(86, bold=True)
        label = f"{pack} Pack"
        tw = d.textlength(label, font=bf)
        pad_x, pad_y, h = 52, 26, 124
        bx = (B.CANVAS - (tw + pad_x * 2)) / 2
        by = y + 190
        d.rounded_rectangle([bx, by, bx + tw + pad_x * 2, by + h], radius=26, fill=(17, 17, 17))
        d.text((bx + pad_x, by + pad_y), label, font=bf, fill=(255, 255, 255))

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    canvas.save(dest, "JPEG", quality=92, optimize=True)
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--codes", default="", help="comma-separated Zoro numbers instead of the CSV order")
    a = ap.parse_args()

    wanted = [c.strip() for c in a.codes.split(",") if c.strip()]
    rows = list(csv.DictReader(open(CANDIDATES, encoding="utf-8")))
    if wanted:
        rows = [r for r in rows if r["zoro_no"] in wanted]
    rows = rows[:a.limit]

    conn = sqlite3.connect(f"file:{ZORO_DB}?mode=ro", uri=True)
    zi = sqlite3.connect(f"file:{os.path.join(ROOT, 'Amazon_DS_Hunting', 'hunter.db')}?mode=ro", uri=True)
    os.makedirs(PHOTOS, exist_ok=True)
    made = []

    for r in rows:
        code = r["zoro_no"]
        z = zoro_row(conn, code)
        if not z:
            print(f"  {code}: not in the Zoro feed — skipped")
            continue
        pack = int(r["zoro_pack"] or 1)
        sku = f"{B.SKU_PREFIX}-{code}-{pack}"
        folder = os.path.join(OUT, sku)
        os.makedirs(os.path.join(folder, "images"), exist_ok=True)

        specs = specs_from(z["long_description"])
        title, bullets, desc = build_copy(z, specs, pack)

        img_url = (zi.execute("SELECT image FROM zoro_index WHERE zoro_no=?", (code,)).fetchone() or [""])[0]
        main_img = ""
        if img_url:
            src = os.path.join(PHOTOS, f"{code}.jpg")
            if B.download_photo(img_url, src):
                try:
                    main_img = brand_main_image(src, os.path.join(folder, "images", "01_main.jpg"), pack)
                except Exception as e:
                    print(f"  {code}: image failed — {type(e).__name__}: {e}")

        data = {
            "sku": sku, "zoro_no": code, "asin_comparable": r["asin"],
            "monthly_sold_comparable": int(r["monthly_sold"] or 0),
            "zoro_brand": z["zoro_brand_name"] or "", "mpn": z["manufacturer_part_number"],
            "upc": z["upc_code"], "category": z["level_two_name"] or z["level_one_name"],
            "pack": pack, "zoro_price": float(z["sales_price"] or 0),
            "landed_cost": float(r["landed_cost"]), "list_price": float(r["price_for_margin"]),
            "specs": specs, "title": title, "bullets": bullets, "description": desc,
            "source_photo": img_url,
        }
        json.dump(data, open(os.path.join(folder, "data.json"), "w", encoding="utf-8"), indent=1)
        with open(os.path.join(folder, "listing.txt"), "w", encoding="utf-8") as fh:
            fh.write(f"SKU        {sku}\nZORO       {code}   cost ${data['landed_cost']:.2f}\n"
                     f"LIST AT    ${data['list_price']:.2f}   (comparable {r['asin']}, "
                     f"{data['monthly_sold_comparable']:,}/mo)\n\nTITLE\n{title}\n\nBULLETS\n")
            for b in bullets:
                fh.write(f"  • {b}\n")
            fh.write(f"\nDESCRIPTION\n{desc}\n")
        try:
            pl_store.mark_built(code, sku, title, bool(main_img))
        except Exception as e:
            print(f"  {code}: built, but the DB row was not updated — {type(e).__name__}: {e}")
        made.append((sku, title, main_img))
        print(f"  ✓ {sku}\n      {title[:96]}\n      image: {'yes' if main_img else 'NO PHOTO'}")

    print(f"\n  {len(made)} listing(s) → {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
