#!/usr/bin/env python3
"""
HOANK Private-Label Listing Builder
===================================
One-file pipeline: Zoro Select "Shipping Box" rows in combined_reports.db
  -> SKU / Title / Bullets 1-5 / Description
  -> Parent/Child variation families (grouped by inside size)
  -> 2 Amazon images per listing, generated with pure Python (no AI):
       {SKU}_1_main.jpg  branded photo, pure-white 1600x1600, ~88% fill
       {SKU}_2_dims.jpg  photo + dimension arrows + info banner
  -> listings.xlsx (import into Google Sheets: File > Import) + listings.csv

Usage:
    python3 build_hoank_listings.py --limit 10      # quick test run
    python3 build_hoank_listings.py                 # full ~1,249 listings
    python3 build_hoank_listings.py --no-images     # spreadsheet only

Requires: pip install Pillow numpy openpyxl
Outputs to:  Automations/private_label/output/
"""

import argparse
import csv
import os
import re
import sqlite3
import time
import urllib.request
from collections import defaultdict
from fractions import Fraction

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# This Python ships an EMPTY TLS trust store until "Install Certificates.command" is run, so
# urllib — which download_photo uses to pull product photos from Zoro's CDN — fails every time
# with CERTIFICATE_VERIFY_FAILED. Same cause that stranded the chromedriver download and broke
# the 04:00 closing on 29 Sep 2026. certifi is already installed; point urllib at it.
try:
    import certifi as _certifi
    _ca = _certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", _ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _ca)
except Exception:
    pass

# ---------------------------------------------------------------- paths / config
HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "..", "TSV Files", "combined_reports.db")
OUT_DIR = os.path.join(HERE, "output")
IMG_DIR = os.path.join(OUT_DIR, "images")
SRC_DIR = os.path.join(OUT_DIR, "zoro_photos")   # downloaded originals (cache)

BRAND = "HOANK"
SKU_PREFIX = "HOA-Kam"
CANVAS = 2000            # square canvas
MAIN_FILL = 0.90         # product fills ~90% of frame (rule: >= 85%)
DOWNLOAD_DELAY = 0.15    # be polite to Zoro's CDN
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"
BRAND_FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
BADGE_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
ORANGE = (244, 121, 32)   # HOANK "NK" orange (matches AI reference)

QUERY = """
SELECT zoro_no, manufacturer_part_number, upc_code, short_description,
       long_description, sales_price, min_order_qty, uom, zoro_image_name,
       ship_pack_weight
FROM zoro_data
WHERE zoro_brand_name = 'Zoro Select'
  AND availabilityStatus = 'In Stock'
  AND is_dropship = 'NO'
  AND (lead_time LIKE '%ships in 1 day%' OR lead_time LIKE '%ships in 1 days%'
       OR lead_time LIKE '%Ships in 1 day%' OR lead_time LIKE '%Ships in 1 days%')
  AND short_description LIKE '%Shipping Box%'
"""


def font(size, bold=True):
    try:
        return ImageFont.truetype(FONT_PATH, size, index=1 if bold else 0)
    except Exception:
        return ImageFont.load_default()


# ---------------------------------------------------------------- spec parsing
def _num(s):
    """'12', '12.5', '12 5/16', '5/8' -> float"""
    s = s.strip()
    if "/" in s:
        parts = s.split()
        whole = float(parts[0]) if len(parts) > 1 else 0.0
        return whole + float(Fraction(parts[-1]))
    return float(s)


def fmt(v):
    """12.0 -> '12', 12.3125 -> '12.31'"""
    return "%g" % round(v, 2)


def parse_specs(long_desc):
    """Extract structured specs from a Zoro Select long_description."""
    ld = long_desc or ""
    m = re.search(r"Inside Length ([\d\s/\.]+?) in, Inside Width ([\d\s/\.]+?) in,"
                  r" Inside Height ([\d\s/\.]+?) in", ld)
    if not m:
        return None   # e.g. box lids -- skipped
    d = {"L": _num(m.group(1)), "W": _num(m.group(2)), "H": _num(m.group(3))}

    def grab(pattern, default=None):
        mm = re.search(pattern, ld)
        return mm.group(1).strip() if mm else default

    d["ect"] = grab(r"(\d+) ECT")
    d["wall"] = grab(r"Wall Type ([^,]+)", "Single Wall")
    d["color"] = grab(r"Color ([^,]+)", "Kraft").split(";")[0].strip()
    d["flute"] = grab(r"Fluting Profile ([^,]+)")
    d["load"] = grab(r"Maximum Load Capacity ([\d,]+(?:\.\d+)?\s*lb)")
    d["style"] = grab(r"Box Style ([^,]+)", "Standard")
    return d


def size_class(d):
    cu_ft = d["L"] * d["W"] * d["H"] / 1728.0
    if cu_ft < 1.6:
        return "SMALL"
    if cu_ft < 3.2:
        return "MEDIUM"
    if cu_ft < 5.0:
        return "LARGE"
    return "EXTRA LARGE"


# ---------------------------------------------------------------- listing copy
def build_sku(zoro_no, moq):
    return f"{SKU_PREFIX}-{zoro_no}-{moq}"


def build_title(d, moq):
    return (f'{BRAND} Shipping Boxes {fmt(d["L"])}"L x {fmt(d["W"])}"W x '
            f'{fmt(d["H"])}"H, {moq} Pack, Corrugated Boxes for Moving, '
            f'Shipping, Mailing, Packing, Storage, {d["color"]} Cardboard')


def build_bullets(d, moq):
    color = d["color"].lower()
    wall = (d["wall"] or "single wall").lower()
    # DURABLE bullet with graceful fallbacks for missing ECT / flute / load
    parts = [f"Built with {wall} corrugated cardboard"]
    if d["flute"]:
        parts.append(d["flute"])
    if d["ect"]:
        parts.append(f'{d["ect"]} ECT rating')
    durable = ", ".join(parts)
    if d["load"]:
        durable += (f", these {color} boxes support a maximum load capacity "
                    f'of {d["load"]}.')
    else:
        durable += f", these {color} boxes hold up to demanding shipping use."
    return [
        f"PACK OF {moq} BOXES: {BRAND} shipping boxes come in a set of {moq}, "
        f"providing ample supply for all your moving, shipping, and storage needs.",
        f'DIMENSIONS: Each box measures {fmt(d["L"])}"L x {fmt(d["W"])}"W x '
        f'{fmt(d["H"])}"H inside, making them ideal for packing clothes, books, '
        f"and various household items.",
        f"DURABLE CONSTRUCTION: {durable}",
        f"QUICK ASSEMBLY: These {BRAND} boxes assemble in seconds with a simple "
        f"3-step process - unfold, shape, tape, and ship instantly with no tools "
        f"required.",
        f"100% RECYCLABLE: Made from corrugated cardboard with plastic-free "
        f"packaging and 100% post-consumer recyclability, keeping waste to a "
        f"minimum.",
    ]


def build_description(d, moq):
    strength = f'{d["ect"]} ECT-rated ' if d["ect"] else ""
    load = (f' Each box supports a maximum load of {d["load"]},'
            if d["load"] else "")
    return (
        f'{BRAND} {fmt(d["L"])}" x {fmt(d["W"])}" x {fmt(d["H"])}" corrugated '
        f"shipping boxes, {moq} per pack. Made from {strength}"
        f'{(d["wall"] or "single wall").lower()} {d["color"].lower()} corrugated '
        f"cardboard, these boxes are built for moving, shipping, mailing, packing "
        f"and storage.{load} and folds flat for easy storage before use. "
        f"Assembly takes seconds: unfold, shape, tape and ship - no tools "
        f"required. 100% recyclable and made with post-consumer recycled "
        f"content, so your packaging stays as responsible as it is reliable. "
        f"Stock up once and cover every shipment, move or seasonal rush with a "
        f"box that holds its shape and protects what matters."
    )


# ---------------------------------------------------------------- image helpers
def download_photo(url, dest):
    """Zoro CDN blocks default agents and its /full/ variant is only 450px.
    Rewrite to /large/ (600px, the biggest served) and fetch with browser UA."""
    if os.path.exists(dest) and os.path.getsize(dest) > 5000:
        return True
    url = url.replace("/product/full/", "/product/large/")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "image/jpeg,image/*;q=0.8",
        "Referer": "https://www.zoro.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        if len(data) < 5000:
            return False
        with open(dest, "wb") as f:
            f.write(data)
        time.sleep(DOWNLOAD_DELAY)
        return True
    except Exception as e:
        print(f"    ! download failed {url}: {e}")
        return False


def product_bbox(im, thresh=245):
    a = np.asarray(im.convert("RGB"))
    nz = np.argwhere((a < thresh).any(axis=2))
    if not len(nz):
        return 0, 0, im.width, im.height
    y0, x0 = nz.min(0)
    y1, x1 = nz.max(0)
    return int(x0), int(y0), int(x1) + 1, int(y1) + 1


def place_on_canvas(photo, fill, canvas_px, y_shift=0):
    """Crop product, upscale (Lanczos), center on pure-white square canvas."""
    x0, y0, x1, y1 = product_bbox(photo)
    prod = photo.convert("RGB").crop((x0, y0, x1, y1))
    s = fill * canvas_px / max(prod.size)
    prod = prod.resize((round(prod.width * s), round(prod.height * s)),
                       Image.LANCZOS)
    canvas = Image.new("RGB", (canvas_px, canvas_px), (255, 255, 255))
    px = (canvas_px - prod.width) // 2
    py = (canvas_px - prod.height) // 2 + y_shift
    canvas.paste(prod, (px, py))
    return canvas, (px, py, prod.width, prod.height)


def _perspective_coeffs(src_size, quad):
    src = [(0, 0), (src_size[0], 0), src_size, (0, src_size[1])]
    M, B = [], []
    for (x, y), (u, v) in zip(quad, src):
        M.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        M.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        B.extend([u, v])
    return np.linalg.solve(np.array(M, float), np.array(B, float))


def warp_layer(layer, quad, size, fill_white):
    coeffs = _perspective_coeffs((layer.width, layer.height), quad)
    fill = (255, 255, 255, 255) if fill_white else (0, 0, 0, 0)
    return layer.transform(size, Image.PERSPECTIVE, coeffs,
                           resample=Image.BICUBIC, fillcolor=fill)


def branding_layers(pack_qty):
    """Two layers, same geometry: ink (multiply-blended, keeps cardboard
    texture) and overlay (white badge text, alpha-composited on top).
    Rendered in Arial Black / Arial Bold to match the brand reference and
    tightly cropped so the caller can scale it WITHOUT distortion."""
    w, h = 1200, 760
    ink = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    di, do = ImageDraw.Draw(ink), ImageDraw.Draw(overlay)
    f = ImageFont.truetype(BRAND_FONT, 230)
    hoa_w = di.textlength("HOA", font=f)
    total = hoa_w + di.textlength("NK", font=f)
    x = (w - total) / 2
    di.text((x, 20), "HOA", font=f, fill=(20, 18, 16))
    di.text((x + hoa_w, 20), "NK", font=f, fill=ORANGE)
    bf = ImageFont.truetype(BADGE_FONT, 150)
    label = f"{pack_qty} Pack"
    bw = di.textlength(label, font=bf) + 120
    bx = (w - bw) / 2
    di.rounded_rectangle([bx, 420, bx + bw, 660], radius=45, fill=(15, 13, 12))
    do.text((w / 2, 540), label, font=bf, fill=(255, 255, 255, 245),
            anchor="mm")
    bbox = ink.getchannel("R").point(lambda v: 255 - v).getbbox()  # crop tight
    pad = 14
    box = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
           min(w, bbox[2] + pad), min(h, bbox[3] + pad))
    return ink.crop(box), overlay.crop(box)


def face_quad(canvas, px, py, pw, ph):
    """Estimate the box's front face by looking at the actual pixels, so the
    branding never spills off the product or across the top-face edge.

    1. Detect a 3/4-angle shot by comparing brightness of the left vs right
       half of the product (the shaded side face is clearly darker).
    2. Constrain each corner of the quad to the product's real pixel span on
       that row (with margins), so tall tubes, squat boxes, open boxes and
       stacks all keep the logo on cardboard."""
    a = np.asarray(canvas.convert("L"), float)
    mask = a < 245

    def row_span(y, lo_frac, hi_frac):
        y = int(min(max(y, 0), a.shape[0] - 1))
        xs = np.where(mask[y])[0]
        if len(xs) < 20:                       # empty row: fall back to bbox
            x0, x1 = px, px + pw
        else:
            x0, x1 = xs.min(), xs.max()
        return x0 + lo_frac * (x1 - x0), x0 + hi_frac * (x1 - x0)

    squat = ph < pw * 0.75
    fy_top, fy_bot = (0.48, 0.92) if squat else (0.40, 0.90)

    # look for the front vertical corner edge inside the branding band:
    # a strong, tall column of horizontal gradient near the middle of the box
    band = a[int(py + fy_top * ph):int(py + fy_bot * ph), px:px + pw]
    if band.shape[0] > 10 and band.shape[1] > 40:
        grad = np.abs(np.diff(band, axis=1)).mean(axis=0)
        k = 7
        grad = np.convolve(grad, np.ones(k) / k, mode="same")
        s, e = int(0.28 * pw), int(0.72 * pw)
        edge_rel = s + int(np.argmax(grad[s:e]))
        edge_strength = grad[edge_rel]
        edge_x = px + edge_rel
    else:
        edge_strength, edge_x = 0, px + pw // 2

    yt, yb = py + fy_top * ph, py + fy_bot * ph

    # measure the usable width at the MIDDLE of the face band, where a box in
    # 3/4 view is widest (top/bottom rows taper into the corner V and would
    # crush the logo); off-cardboard corners are caught by the shrink loop in
    # gen_main_image afterwards
    m0, m1 = row_span(py + 0.50 * ph, 0.0, 1.0)
    n0, n1 = row_span(py + 0.62 * ph, 0.0, 1.0)
    x0, x1 = max(m0, n0), min(m1, n1)
    span = x1 - x0

    tilt_sign = 1
    if edge_strength > 3.0 and x0 + 0.25 * span < edge_x < x1 - 0.25 * span:
        # 3/4 view with the corner edge inside the span: score each side by
        # width AND brightness, so we don't brand the face in shadow
        yy0, yy1 = int(yt), int(yb)
        lmean = float(a[yy0:yy1, int(x0):int(edge_x)].mean())
        rmean = float(a[yy0:yy1, int(edge_x):int(x1)].mean())
        lscore = (edge_x - x0) * (lmean / 255) ** 1.5
        rscore = (x1 - edge_x) * (rmean / 255) ** 1.5
        if rscore >= lscore:
            xl, xr = edge_x + 0.07 * span, x1 - 0.09 * span
        else:
            xl, xr = x0 + 0.09 * span, edge_x - 0.07 * span
            tilt_sign = -1
    else:
        # frontal shot (or edge near the border): center on the face
        xl, xr = x0 + 0.26 * span, x1 - 0.26 * span
        tilt_sign = 0

    return xl, xr, yt, yb, tilt_sign


def brand_quad(layer, xl, xr, yt, yb, tilt_sign):
    """Fit the branding layer inside the face area at its OWN aspect ratio
    (never stretched): shrink to fit the band height if needed, center it,
    and apply only a mild printed-on-face vertical tilt."""
    aspect = layer.height / layer.width
    w = xr - xl
    h = w * aspect
    band_h = (yb - yt) * 0.92
    if h > band_h:                 # too tall for the face: scale down
        h = band_h
        w = h / aspect
    cx = (xl + xr) / 2
    cy = yt + (yb - yt) * 0.48
    xl2, xr2 = cx - w / 2, cx + w / 2
    yt2, yb2 = cy - h / 2, cy + h / 2
    tilt = tilt_sign * 0.035 * h
    return [(xl2, yt2 + tilt), (xr2, yt2 - tilt),
            (xr2, yb2 - tilt), (xl2, yb2)]


def gen_main_image(photo, pack_qty, out_path):
    """Returns a QC dict for this image (see quality_check)."""
    canvas, (px, py, pw, ph) = place_on_canvas(photo, MAIN_FILL, CANVAS)
    xl, xr, yt, yb, tilt_sign = face_quad(canvas, px, py, pw, ph)
    ink, overlay = branding_layers(pack_qty)
    # place at the widest usable size, shrinking toward the center only if
    # part of the branding would land off the cardboard
    lm = np.asarray(canvas.convert("L")) < 245
    cx = (xl + xr) / 2
    for shrink in (1.0, 0.9, 0.8, 0.7, 0.6):
        w2 = (xr - xl) * shrink / 2
        quad = brand_quad(ink, cx - w2, cx + w2, yt, yb, tilt_sign)
        qx = [int(p[0]) for p in quad]
        qy = [int(p[1]) for p in quad]
        region = lm[max(0, min(qy)):max(qy), max(0, min(qx)):max(qx)]
        if region.size and region.mean() > 0.985:
            break
    qc = quality_check(canvas, quad, px, py, pw, ph)
    ink_w = warp_layer(ink, quad, canvas.size, fill_white=True)
    ink_w = ink_w.convert("RGB").filter(ImageFilter.GaussianBlur(0.6))
    out = Image.fromarray((np.asarray(canvas, np.uint16) *
                           np.asarray(ink_w, np.uint16) // 255).astype(np.uint8))
    over_w = warp_layer(overlay, quad, canvas.size, fill_white=False)
    out = Image.alpha_composite(out.convert("RGBA"), over_w).convert("RGB")
    out.save(out_path, quality=92)
    return qc


def quality_check(canvas, quad, px, py, pw, ph):
    """Automatic QC before branding is applied:
    - fill: does the product cover ~MAIN_FILL of the canvas?
    - on_box: is the branding area 100% on product pixels (not background)?
    - clean_area: is the branding area free of existing print/graphics?
    - logo_size: is the logo a sensible size relative to the product?"""
    a = np.asarray(canvas.convert("L"), float)
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    qx0, qx1 = int(max(0, min(xs))), int(min(CANVAS, max(xs)))
    qy0, qy1 = int(max(0, min(ys))), int(min(CANVAS, max(ys)))
    region = a[qy0:qy1, qx0:qx1]
    if region.size == 0:
        return {"qc_pass": False, "qc_issues": "empty-brand-area"}
    on_box = float((region < 245).mean())          # non-white = on product
    # existing print = strong local contrast (ink vs board) in the brand area;
    # a plain face in shadow is uniformly dark and must NOT trigger this
    ink_px = float((region < 70).mean())
    dark = ink_px if (ink_px > 0.02 or region.std() > 32) else 0.0
    fill = max(pw, ph) / CANVAS
    logo_frac = (qx1 - qx0) / max(pw, 1)
    issues = []
    if fill < MAIN_FILL - 0.03:
        issues.append(f"low-fill:{fill:.2f}")
    if on_box < 0.985:
        issues.append(f"brand-off-box:{on_box:.2f}")
    if dark > 0.05:
        issues.append(f"existing-print-under-brand:{dark:.2f}")
    if not 0.18 <= logo_frac <= 0.75:
        issues.append(f"logo-size:{logo_frac:.2f}")
    return {"qc_pass": not issues, "qc_issues": ";".join(issues)}


def _arrow(d, p1, p2, label, f, color=(30, 26, 24)):
    d.line([p1, p2], fill=color, width=8)
    import math
    for tip, other in ((p1, p2), (p2, p1)):
        ang = math.atan2(other[1] - tip[1], other[0] - tip[0])
        for da in (0.42, -0.42):
            d.line([tip, (tip[0] + 34 * math.cos(ang + da),
                          tip[1] + 34 * math.sin(ang + da))],
                   fill=color, width=8)
    mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    tb = d.textbbox((0, 0), label, font=f)
    pad = 14
    d.rounded_rectangle([mid[0] - (tb[2] - tb[0]) / 2 - pad,
                         mid[1] - (tb[3] - tb[1]) / 2 - pad,
                         mid[0] + (tb[2] - tb[0]) / 2 + pad,
                         mid[1] + (tb[3] - tb[1]) / 2 + pad],
                        radius=10, fill=(255, 255, 255), outline=color, width=3)
    d.text(mid, label, font=f, fill=color, anchor="mm")


def gen_dims_image(photo, d, out_path):
    """Secondary image: photo + L/W/H arrows + bottom info banner."""
    canvas, (px, py, pw, ph) = place_on_canvas(photo, 0.62, CANVAS,
                                               y_shift=-CANVAS // 9)
    dr = ImageDraw.Draw(canvas)
    f = font(52)
    bx1, by1 = px + pw, py + ph
    _arrow(dr, (px + 0.02 * pw, by1 + 60), (px + 0.45 * pw, by1 + 110),
           f'{fmt(d["L"])}" long', f)
    _arrow(dr, (px + 0.55 * pw, by1 + 110), (bx1 - 0.02 * pw, by1 + 60),
           f'{fmt(d["W"])}" wide', f)
    _arrow(dr, (bx1 + 70, py + 0.15 * ph), (bx1 + 70, py + 0.85 * ph),
           f'{fmt(d["H"])}" high', f)
    # bottom banner
    b_top = CANVAS - 340
    dr.rounded_rectangle([60, b_top, CANVAS - 60, CANVAS - 60], radius=24,
                         fill=(255, 255, 255), outline=(225, 220, 212), width=3)
    dr.text((CANVAS / 2, b_top + 75), f"{size_class(d)} SHIPPING BOXES",
            font=font(78), fill=(30, 26, 24), anchor="mm")
    dr.text((CANVAS / 2, b_top + 158),
            f'{fmt(d["L"])}x{fmt(d["W"])}x{fmt(d["H"])} Size',
            font=font(58, bold=False), fill=(60, 55, 50), anchor="mm")
    dr.text((CANVAS / 2, b_top + 228),
            "Holds clothes, books, and household items",
            font=font(46, bold=False), fill=(90, 84, 78), anchor="mm")
    canvas.save(out_path, quality=92)


# ---------------------------------------------------------------- main pipeline
def run(limit=None, images=True, outdir=None):
    global OUT_DIR, IMG_DIR
    if outdir:
        OUT_DIR = os.path.abspath(outdir)
        IMG_DIR = os.path.join(OUT_DIR, "images")
    os.makedirs(IMG_DIR, exist_ok=True)
    os.makedirs(SRC_DIR, exist_ok=True)   # shared photo cache stays put

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(QUERY).fetchall()
    print(f"query matched {len(rows)} rows")

    # parse + group into size families
    listings = []
    for r in rows:
        d = parse_specs(r["long_description"])
        if d is None:
            print(f"  skipping {r['zoro_no']} (no inside dimensions - lid?)")
            continue
        listings.append((r, d))
    families = defaultdict(list)
    for r, d in listings:
        families[(d["L"], d["W"], d["H"])].append((r, d))

    if limit:
        # keep whole families together so parent/child stays coherent in tests
        picked, out = 0, []
        for key in sorted(families, key=lambda k: -len(families[k])):
            out.append(key)
            picked += len(families[key])
            if picked >= limit:
                break
        families = {k: families[k] for k in out}
        print(f"--limit {limit}: testing {picked} listings "
              f"in {len(families)} families")

    sheet_rows = []
    n_done = 0
    for (L, W, H), members in families.items():
        size_str = f'{fmt(L)}x{fmt(W)}x{fmt(H)}'
        is_family = len(members) > 1
        parent_sku = f"{SKU_PREFIX}-P-{size_str}" if is_family else ""

        if is_family:   # parent row: no price, no images, no zoro code
            pd = members[0][1]
            sheet_rows.append({
                "relationship": "PARENT",
                "sku": parent_sku, "parent_sku": "", "zoro_code": "",
                "variation_name": "",
                "title": (f'{BRAND} Shipping Boxes {fmt(L)}"L x {fmt(W)}"W x '
                          f'{fmt(H)}"H, Corrugated Boxes for Moving, Shipping, '
                          f'Mailing, Packing, Storage'),
                "size": size_str, "pack_qty": "", "ect": "", "wall": "",
                "color": "", "bullet1": "", "bullet2": "", "bullet3": "",
                "bullet4": "", "bullet5": "", "description": "",
                "zoro_price_each": "", "pack_cost": "", "upc": "",
                "main_image": "", "dims_image": "", "zoro_image_url": "",
                "qc_pass": "", "qc_issues": "",
            })

        for r, d in members:
            moq = int(re.sub(r"\D", "", r["min_order_qty"] or "1") or 1)
            sku = build_sku(r["zoro_no"], moq)
            bullets = build_bullets(d, moq)
            variation = ""
            if is_family:
                bits = [f'{d["ect"]} ECT' if d["ect"] else "Standard",
                        d["wall"] or "", d["color"], f"{moq} Pack"]
                variation = " - ".join(b for b in bits if b)

            main_path = os.path.join(IMG_DIR, f"{sku}_1_main.jpg")
            dims_path = os.path.join(IMG_DIR, f"{sku}_2_dims.jpg")
            img_ok = False
            qc = {"qc_pass": "", "qc_issues": "no-image"}
            if images and (r["zoro_image_name"] or "").startswith("http"):
                src = os.path.join(
                    SRC_DIR, os.path.basename(r["zoro_image_name"]))
                if download_photo(r["zoro_image_name"], src):
                    try:
                        photo = Image.open(src)
                        qc = gen_main_image(photo, moq, main_path)
                        gen_dims_image(photo, d, dims_path)
                        img_ok = True
                    except Exception as e:
                        print(f"    ! image gen failed {sku}: {e}")
                        qc = {"qc_pass": False, "qc_issues": f"error:{e}"}

            price = float(r["sales_price"] or 0)
            sheet_rows.append({
                "relationship": "CHILD" if is_family else "STANDALONE",
                "sku": sku, "parent_sku": parent_sku,
                "zoro_code": r["zoro_no"], "variation_name": variation,
                "title": build_title(d, moq), "size": size_str,
                "pack_qty": moq, "ect": d["ect"] or "",
                "wall": d["wall"] or "", "color": d["color"],
                "bullet1": bullets[0], "bullet2": bullets[1],
                "bullet3": bullets[2], "bullet4": bullets[3],
                "bullet5": bullets[4],
                "description": build_description(d, moq),
                "zoro_price_each": price,
                "pack_cost": round(price * moq, 2),
                "upc": (r["upc_code"] or "").strip(),
                "main_image": main_path if img_ok else "",
                "dims_image": dims_path if img_ok else "",
                "zoro_image_url": r["zoro_image_name"] or "",
                "qc_pass": ("PASS" if qc["qc_pass"] else "REVIEW")
                           if img_ok else "",
                "qc_issues": qc["qc_issues"],
            })
            n_done += 1
            if n_done % 50 == 0:
                print(f"  ...{n_done} listings processed")

    write_outputs(sheet_rows)
    n_review = sum(1 for x in sheet_rows if x.get("qc_pass") == "REVIEW")
    print(f"\nQC: {sum(1 for x in sheet_rows if x.get('qc_pass')=='PASS')} pass, "
          f"{n_review} flagged for manual review (see qc_pass/qc_issues columns)")
    print(f"done: {n_done} listings "
          f"({sum(1 for x in sheet_rows if x['relationship']=='PARENT')} parents, "
          f"{sum(1 for x in sheet_rows if x['relationship']=='CHILD')} children, "
          f"{sum(1 for x in sheet_rows if x['relationship']=='STANDALONE')} standalone)")
    print(f"spreadsheet: {os.path.join(OUT_DIR, 'listings.xlsx')}")
    print("import into Google Sheets via File > Import > Upload")


COLUMNS = ["relationship", "sku", "parent_sku", "zoro_code", "variation_name",
           "title", "size", "pack_qty", "ect", "wall", "color",
           "bullet1", "bullet2", "bullet3", "bullet4", "bullet5",
           "description", "zoro_price_each", "pack_cost", "upc",
           "main_image", "dims_image", "zoro_image_url",
           "qc_pass", "qc_issues"]


def write_outputs(sheet_rows):
    csv_path = os.path.join(OUT_DIR, "listings.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(sheet_rows)
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font as XFont, PatternFill
        wb = Workbook()
        ws = wb.active
        ws.title = "HOANK Listings"
        ws.append([c.upper() for c in COLUMNS])
        for c in ws[1]:
            c.font = XFont(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F3864")
        parent_fill = PatternFill("solid", fgColor="DDEBF7")
        for row in sheet_rows:
            ws.append([row[c] for c in COLUMNS])
            if row["relationship"] == "PARENT":
                for c in ws[ws.max_row]:
                    c.fill = parent_fill
        widths = {"A": 12, "B": 24, "C": 22, "D": 12, "E": 34, "F": 60,
                  "G": 12, "Q": 60}
        for col, wd in widths.items():
            ws.column_dimensions[col].width = wd
        for col in "LMNOP":
            ws.column_dimensions[col].width = 45
        ws.freeze_panes = "A2"
        wb.save(os.path.join(OUT_DIR, "listings.xlsx"))
    except ImportError:
        print("openpyxl not installed - wrote CSV only "
              "(pip install openpyxl for the .xlsx)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="HOANK listing builder")
    ap.add_argument("--limit", type=int, default=None,
                    help="test run: process roughly this many listings")
    ap.add_argument("--no-images", action="store_true",
                    help="skip photo download + image generation")
    ap.add_argument("--outdir", default=None,
                    help="write spreadsheet + images to this folder "
                         "instead of ./output")
    args = ap.parse_args()
    run(limit=args.limit, images=not args.no_images, outdir=args.outdir)
