"""Composite main images in the 'HOANK box on a stack of flats' style.

For each SKU: cut the real box out of its Zoro photo (keeps true geometry),
brand it (HOANK + pack badge, reusing the pipeline), paste it onto the clean
stack template with a soft shadow, print the true size on the stack's front
band, and save a 2000x2000 main image + a dimensions variant with arrows.

Usage:
    python3 make_stack_composites.py SKU [SKU ...]
    python3 make_stack_composites.py --file skus.txt
Outputs to output_stack/.
"""
import json
import os
import re
import sqlite3
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_hoank_listings import (  # noqa: E402
    DB, SRC_DIR, BRAND_FONT, download_photo, parse_specs, fmt, font,
    branding_layers, face_quad, brand_quad, warp_layer, product_bbox,
    _arrow, CANVAS)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output_stack")
TEMPLATE = os.path.join(HERE, "stack_template_clean.png")
META = json.load(open(os.path.join(HERE, "stack_template_meta.json")))
FILL = 0.90
INK = (66, 52, 38)          # printed size text tone (dark warm brown)


def cutout_box(photo):
    """Return RGBA cutout of the product: background made transparent via
    border flood fill, so white tape/labels inside the box stay opaque."""
    im = photo.convert("RGB")
    marker = (255, 0, 255)
    work = im.copy()
    seedable = ImageDraw.floodfill
    for seed in [(0, 0), (im.width - 1, 0), (0, im.height - 1),
                 (im.width - 1, im.height - 1),
                 (im.width // 2, 0), (0, im.height // 2),
                 (im.width - 1, im.height // 2)]:
        try:
            seedable(work, seed, marker, thresh=24)
        except Exception:
            pass
    a = np.asarray(work)
    bg = (a[:, :, 0] == 255) & (a[:, :, 1] == 0) & (a[:, :, 2] == 255)
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    alpha = np.asarray(Image.fromarray(alpha).filter(
        ImageFilter.GaussianBlur(1.0)))
    out = np.dstack([np.asarray(im), alpha])
    x0, y0, x1, y1 = product_bbox(im)
    return Image.fromarray(out).crop((x0, y0, x1, y1))


def brand_cutout(cut, pack_qty):
    """Apply HOANK branding onto the RGBA cutout (reuses pipeline logic)."""
    rgb = Image.new("RGB", cut.size, (255, 255, 255))
    rgb.paste(cut.convert("RGB"), (0, 0), cut)
    xl, xr, yt, yb, ts = face_quad(rgb, 0, 0, cut.width, cut.height)
    ink, overlay = branding_layers(pack_qty)
    quad = brand_quad(ink, xl, xr, yt, yb, ts)
    ink_w = warp_layer(ink, quad, cut.size, fill_white=True)
    ink_w = ink_w.convert("RGB").filter(ImageFilter.GaussianBlur(0.5))
    merged = Image.fromarray(
        (np.asarray(rgb, np.uint16) * np.asarray(ink_w, np.uint16)
         // 255).astype(np.uint8))
    over_w = warp_layer(overlay, quad, cut.size, fill_white=False)
    merged = Image.alpha_composite(merged.convert("RGBA"), over_w)
    merged.putalpha(cut.getchannel("A"))
    return merged


def front_y(x):
    L, F, R = META["L"], META["F"], META["R"]
    if x <= F[0]:
        m = (F[1] - L[1]) / (F[0] - L[0])
        return L[1] + m * (x - L[0])
    m = (R[1] - F[1]) / (R[0] - F[0])
    return F[1] + m * (x - F[0])


def compose(sku, zoro_no, pack_qty, d):
    tpl = Image.open(TEMPLATE).convert("RGB")
    W = tpl.width
    con = sqlite3.connect(DB)
    url = con.execute("SELECT zoro_image_name FROM zoro_data WHERE zoro_no=?",
                      (zoro_no,)).fetchone()[0]
    src = os.path.join(SRC_DIR, os.path.basename(url))
    if not download_photo(url, src):
        raise RuntimeError("photo download failed")
    cut = brand_cutout(cutout_box(Image.open(src)), pack_qty)

    # scale: box width ~56% of canvas, capped so the composite fits
    bw = int(0.56 * W)
    bh = int(cut.height * bw / cut.width)
    if bh > 0.62 * W:
        bh = int(0.62 * W)
        bw = int(cut.width * bh / cut.height)
    cut = cut.resize((bw, bh), Image.LANCZOS)

    # anchor: bottom-center of the box sits a bit behind the front edge
    cx = int(0.52 * W)
    base_y = int(front_y(cx)) - 26
    bx, by = cx - bw // 2, base_y - bh

    scene = tpl.copy()
    # soft contact shadow on the face
    sh = Image.new("L", (W, W), 0)
    ImageDraw.Draw(sh).ellipse([bx + 20, base_y - 36, bx + bw - 20,
                                base_y + 26], fill=90)
    sh = sh.filter(ImageFilter.GaussianBlur(22))
    dark = Image.fromarray(
        (np.asarray(scene, np.float32)
         * (1 - np.asarray(sh, np.float32)[:, :, None] / 255 * 0.45)
         ).clip(0, 255).astype(np.uint8))
    scene = Image.composite(dark, scene, sh)
    scene.paste(cut, (bx, by), cut)

    # print true size on the front band, left of the box
    label = f'{fmt(d["L"])}"  x  {fmt(d["W"])}"  x  {fmt(d["H"])}"'
    tf = ImageFont.truetype(BRAND_FONT, 54)
    tw = int(tf.getlength(label)) + 40
    layer = Image.new("RGBA", (tw, 90), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((20, 8), label, font=tf, fill=INK + (235,))
    L, F = META["L"], META["F"]
    ang = -np.degrees(np.arctan2(F[1] - L[1], F[0] - L[0]))
    layer = layer.rotate(ang, expand=True, resample=Image.BICUBIC)
    tx = int(0.16 * W)
    ty = int(front_y(tx + layer.width * 0.5)) + 8
    scene.paste(layer, (tx, ty - layer.height // 2), layer)

    # place on white at 90% fill
    a = np.asarray(scene)
    nz = np.argwhere((a < 245).any(axis=2))
    y0, x0 = nz.min(0)
    y1, x1 = nz.max(0)
    prod = scene.crop((x0, y0, x1 + 1, y1 + 1))
    s = FILL * CANVAS / max(prod.size)
    prod = prod.resize((round(prod.width * s), round(prod.height * s)),
                       Image.LANCZOS)
    canvas = Image.new("RGB", (CANVAS, CANVAS), (255, 255, 255))
    px, py = (CANVAS - prod.width) // 2, (CANVAS - prod.height) // 2
    canvas.paste(prod, (px, py))
    canvas.save(os.path.join(OUT, f"{sku}_1_main.jpg"), quality=92)

    # dims variant: arrows anchored on the hero box within the composite
    dims = canvas.copy()
    dr = ImageDraw.Draw(dims)
    fx = lambda v: px + (v * s)  # noqa: E731
    fy = lambda v: py + (v * s)  # noqa: E731
    gx0, gy0 = fx(bx - x0), fy(by - y0)
    gw, gh = bw * s, bh * s
    f = font(52)
    _arrow(dr, (gx0 - 10, gy0 + gh * 0.98 + 46),
           (gx0 + gw * 0.44, gy0 + gh * 1.02 + 46), f'{fmt(d["L"])}" long', f)
    _arrow(dr, (gx0 + gw * 0.56, gy0 + gh * 1.02 + 46),
           (gx0 + gw + 10, gy0 + gh * 0.98 + 46), f'{fmt(d["W"])}" wide', f)
    _arrow(dr, (gx0 + gw + 64, gy0 + gh * 0.12),
           (gx0 + gw + 64, gy0 + gh * 0.88), f'{fmt(d["H"])}" high', f)
    dims.save(os.path.join(OUT, f"{sku}_2_dims.jpg"), quality=92)
    return True


def main(skus):
    os.makedirs(OUT, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    done, failed = 0, []
    for sku in skus:
        m = re.match(r"HOA-Kam-(G\w+)-(\d+)$", sku.strip())
        if not m:
            failed.append((sku, "bad SKU format"))
            continue
        zoro_no, pack = m.group(1), int(m.group(2))
        r = con.execute("SELECT long_description FROM zoro_data WHERE "
                        "zoro_no=?", (zoro_no,)).fetchone()
        if not r:
            failed.append((sku, "not in DB"))
            continue
        d = parse_specs(r["long_description"])
        if not d:
            failed.append((sku, "no dims in DB"))
            continue
        try:
            compose(sku, zoro_no, pack, d)
            done += 1
            print(f"ok {sku}  ({fmt(d['L'])}x{fmt(d['W'])}x{fmt(d['H'])})")
        except Exception as e:
            failed.append((sku, str(e)))
    print(f"\ndone: {done} ok, {len(failed)} failed")
    for s, why in failed:
        print(f"  FAILED {s}: {why}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--file":
        skus = [l.strip() for l in open(args[1]) if l.strip()]
    else:
        skus = args
    main(skus)
