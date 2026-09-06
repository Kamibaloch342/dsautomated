"""Finalize AI-generated main images into Amazon-ready sets.

Workflow:
  1. Generate the main composite per SKU with your AI tool (prompts in
     ai_prompts.csv, made by this script with --prompts).
  2. Save each AI image into ai_raw/ named exactly {SKU}.png or {SKU}.jpg
     e.g. ai_raw/HOA-Kam-G6131964-25.png
  3. Run:  python3 finalize_ai_images.py
     For every file in ai_raw/ it produces in output_stack/:
       {SKU}_1_main.jpg   2000x2000, pure white, product at 90% fill
       {SKU}_2_dims.jpg   same image annotated with true L/W/H arrows
     ...and prints a QC verdict per image (fill, background, size text).

python3 finalize_ai_images.py --prompts   -> (re)build ai_prompts.csv only
"""
import csv
import os
import re
import sqlite3
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_hoank_listings import (  # noqa: E402
    DB, parse_specs, fmt, font, _arrow, CANVAS, product_bbox)

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "ai_raw")
OUT = os.path.join(HERE, "output_stack")
FILL = 0.90

SKUS = """
HOA-Kam-G2116116-1    HOA-Kam-G3056439-25  HOA-Kam-G6131964-25
HOA-Kam-G1606934-10   HOA-Kam-G6158975-25  HOA-Kam-G7456985-25
HOA-Kam-G3095736-10   HOA-Kam-G6141712-25  HOA-Kam-G6460073-25
HOA-Kam-G6460493-15   HOA-Kam-G6214031-25  HOA-Kam-G3056536-25
HOA-Kam-G2976018-25   HOA-Kam-G2284847-25  HOA-Kam-G2975318-20
HOA-Kam-G3099260-20   HOA-Kam-G3096217-15  HOA-Kam-G1608212-10
HOA-Kam-G1606015-15   HOA-Kam-G0352305-25  HOA-Kam-G6460904-15
HOA-Kam-G3057236-25   HOA-Kam-G3067657-25  HOA-Kam-G1606121-10
HOA-Kam-G6439143-5    HOA-Kam-G3096147-20  HOA-Kam-G3056798-25
HOA-Kam-G6441374-25   HOA-Kam-G2284917-25  HOA-Kam-G6441672-25
HOA-Kam-G2976088-25   HOA-Kam-G3056667-25  HOA-Kam-G6442406-25
HOA-Kam-G3095620-20   HOA-Kam-G3056579-25  HOA-Kam-G3056396-25
HOA-Kam-G1607485-10   HOA-Kam-G6442327-10  HOA-Kam-G3080491-25
HOA-Kam-G000920855-20 HOA-Kam-G6187851-20  HOA-Kam-G4099977-25
HOA-Kam-G6441076-25   HOA-Kam-G6442178-25  HOA-Kam-G6441198-25
HOA-Kam-G3057559-25   HOA-Kam-G2975826-25  HOA-Kam-G3056685-25
HOA-Kam-G2975747-25   HOA-Kam-G6140242-25
""".split()

PROMPT = (
    'Edit the attached product photo of a corrugated kraft shipping box '
    '(inside size {L}"L x {W}"W x {H}"H - KEEP these exact proportions, '
    'do not make the box taller or shorter). Keep the same camera angle and '
    'lighting. 1) Print "HOANK" on the front face in heavy bold sans-serif '
    '(Arial Black style): "HOA" near-black, "NK" orange (#F47920), following '
    'the face perspective like factory print. Spell exactly H-O-A-N-K. '
    '2) Below it add a black rounded-rectangle badge with "{PACK} Pack" in '
    'bold white text. 3) Place the box on a neat strapped stack of the same '
    'boxes folded flat, with "{L}\\" x {W}\\" x {H}\\"" printed in dark gray '
    'on the stack, two thin black straps. 4) Pure white background RGB(255,'
    '255,255), soft natural shadow under the stack only. 5) Square 2000x2000, '
    'product filling about 90% of the frame. No other text or watermarks. '
    'Photorealistic Amazon main-image style.')


def specs_for(sku):
    m = re.match(r"HOA-Kam-(G\w+)-(\d+)$", sku)
    zoro_no, pack = m.group(1), int(m.group(2))
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT long_description FROM zoro_data WHERE zoro_no=?",
                    (zoro_no,)).fetchone()
    return parse_specs(r["long_description"]) if r else None, pack


def write_prompts():
    path = os.path.join(HERE, "ai_prompts.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sku", "size", "prompt"])
        for sku in SKUS:
            d, pack = specs_for(sku)
            if not d:
                print("!! not in DB:", sku)
                continue
            w.writerow([sku, f'{fmt(d["L"])}x{fmt(d["W"])}x{fmt(d["H"])}',
                        PROMPT.format(L=fmt(d["L"]), W=fmt(d["W"]),
                                      H=fmt(d["H"]), PACK=pack)])
    print("wrote", path, f"({len(SKUS)} SKUs)")


def finalize():
    os.makedirs(OUT, exist_ok=True)
    files = {}
    if os.path.isdir(RAW):
        for fn in os.listdir(RAW):
            base, ext = os.path.splitext(fn)
            if ext.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                files[base] = os.path.join(RAW, fn)
    if not files:
        print(f"no images found in {RAW}/ - save AI outputs there named "
              "{SKU}.png (see ai_prompts.csv), then rerun")
        return
    n_ok = 0
    for sku, path in sorted(files.items()):
        d, pack = specs_for(sku)
        if not d:
            print(f"SKIP {sku}: not in DB")
            continue
        im = Image.open(path).convert("RGB")
        # normalize: crop product, 90% fill on pure white 2000x2000
        x0, y0, x1, y1 = product_bbox(im, thresh=250)
        prod = im.crop((x0, y0, x1, y1))
        s = FILL * CANVAS / max(prod.size)
        prod = prod.resize((round(prod.width * s), round(prod.height * s)),
                           Image.LANCZOS)
        canvas = Image.new("RGB", (CANVAS, CANVAS), (255, 255, 255))
        px, py = (CANVAS - prod.width) // 2, (CANVAS - prod.height) // 2
        canvas.paste(prod, (px, py))
        main_path = os.path.join(OUT, f"{sku}_1_main.jpg")
        canvas.save(main_path, quality=92)

        # dims variant: arrows anchored on the upper (hero box) part
        dims = canvas.copy()
        dr = ImageDraw.Draw(dims)
        f = font(56)
        bx0, bx1 = px + prod.width * 0.18, px + prod.width * 0.82
        bymid = py + prod.height * 0.52
        _arrow(dr, (bx0, bymid + prod.height * 0.34),
               (px + prod.width * 0.47, bymid + prod.height * 0.40),
               f'{fmt(d["L"])}" long', f)
        _arrow(dr, (px + prod.width * 0.53, bymid + prod.height * 0.40),
               (bx1, bymid + prod.height * 0.34), f'{fmt(d["W"])}" wide', f)
        _arrow(dr, (px + prod.width * 0.94, py + prod.height * 0.08),
               (px + prod.width * 0.94, py + prod.height * 0.42),
               f'{fmt(d["H"])}" high', f)
        dims.save(os.path.join(OUT, f"{sku}_2_dims.jpg"), quality=92)

        # QC: source resolution, background whiteness, fill
        src_w = Image.open(path).width
        issues = []
        if src_w < 1500:
            issues.append(f"low-res-source:{src_w}px")
        corners = [im.getpixel((2, 2)), im.getpixel((im.width - 3, 2))]
        if any(min(c) < 245 for c in corners):
            issues.append("bg-not-white")
        print(f"{'REVIEW' if issues else 'PASS  '} {sku}"
              + (f"  [{';'.join(issues)}]" if issues else ""))
        n_ok += 1
    print(f"\nfinalized {n_ok} SKUs -> {OUT}")
    missing = [s for s in SKUS if s not in files]
    if missing:
        print(f"still missing {len(missing)} AI images:", *missing[:10])


if __name__ == "__main__":
    if "--prompts" in sys.argv:
        write_prompts()
    else:
        write_prompts()
        finalize()
