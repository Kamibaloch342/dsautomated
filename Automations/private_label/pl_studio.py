#!/usr/bin/env python3
"""
pl_studio.py — the secondary listing images, rendered here, from real data.

Why this exists rather than an AI scene generator: the free keyless services burn their own
watermark into the output and rate-limit under load, and a text-to-image model with no reference
invents a product that merely resembles the one you ship — which comes back as an
item-not-as-described return. Neither is acceptable on a listing.

So these are drawn, not generated. Every pixel of product is the real Zoro photograph and every
number is from the Zoro feed, which means they are free, instant, unlimited, offline, and cannot
say anything untrue about the item. For industrial and consumable goods that is also what
converts: a buyer choosing paper bags wants the count, the size and the material, not a mood.

    02_specs.jpg      the photo beside the manufacturer's own specification
    03_pack.jpg       how many you get (multipacks only)
    04_size.jpg       the real dimensions, called out on the product
    05_features.jpg   what the specs mean, four tiles
"""
import os
import re

from PIL import Image, ImageDraw, ImageFilter

import build_hoank_listings as B

CANVAS = B.CANVAS                      # 2000, Amazon wants the longest side >= 1600
INK = (26, 26, 28)
MUTED = (112, 114, 120)
HAIR = (226, 226, 230)
PAPER = (255, 255, 255)
ORANGE = B.ORANGE


# ─────────────────────────────────────────────────────────────── product cutout
def cutout(path):
    """The product with its white ground removed.

    Flood-filled inwards from the four corners rather than thresholded on brightness: the
    background is the region connected to the edges, so a white product — a white binder, a white
    bag — keeps its own white body instead of being erased along with the backdrop.
    """
    src = Image.open(path).convert("RGB")
    w, h = src.size
    work = src.copy()
    for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        ImageDraw.floodfill(work, corner, (255, 0, 255), thresh=42)
    mask = Image.new("L", (w, h), 0)
    wp, mp = work.load(), mask.load()
    for y in range(h):
        for x in range(w):
            mp[x, y] = 0 if wp[x, y] == (255, 0, 255) else 255
    mask = mask.filter(ImageFilter.GaussianBlur(1.0))
    out = src.convert("RGBA")
    out.putalpha(mask)
    box = mask.getbbox()
    return out.crop(box) if box else out


def fit(img, bw, bh):
    s = min(bw / img.width, bh / img.height)
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)


def shadow(canvas, img, x, y, strength=110):
    """A soft contact shadow so the product sits on the page instead of hovering on it."""
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    e = Image.new("L", (img.width, max(8, int(img.height * 0.11))), 0)
    ImageDraw.Draw(e).ellipse([0, 0, e.width, e.height], fill=strength)
    sh.paste(Image.new("RGBA", e.size, (0, 0, 0, 255)),
             (x, y + img.height - e.height // 2), e)
    return Image.alpha_composite(canvas, sh.filter(ImageFilter.GaussianBlur(22)))


# ─────────────────────────────────────────────────────────────── page furniture
def page(title=None, kicker=None):
    im = Image.new("RGBA", (CANVAS, CANVAS), PAPER + (255,))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, CANVAS, 14], fill=ORANGE)
    if kicker:
        f = B.font(40, bold=True)
        d.text((96, 92), kicker.upper(), font=f, fill=ORANGE)
    if title:
        f = B.font(78, bold=True)
        d.text((96, 150), title, font=f, fill=INK)
    return im, d


def wordmark(d, y=None, size=76):
    """The same HOA/NK lockup as the main image, so the set reads as one brand."""
    f = B.font(size, bold=True)
    y = CANVAS - int(size * 1.9) if y is None else y
    a, b = "HOA", "NK"
    wa, wb = d.textlength(a, font=f), d.textlength(b, font=f)
    x = (CANVAS - (wa + wb)) / 2
    d.text((x, y), a, font=f, fill=INK)
    d.text((x + wa, y), b, font=f, fill=ORANGE)


def wrap(d, text, f, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ───────────────────────────────────────────────────────────────── the four pages
def specs_page(photo, data, dest):
    specs = data.get("specs") or {}
    pairs = [(k, v) for k, v in specs.items()][:8]
    if not pairs:
        return None
    im, d = page("Specification", "As supplied")
    prod = fit(photo, 820, 1000)
    px, py = 120, 420
    im = shadow(im, prod, px, py)
    im.paste(prod, (px, py), prod)
    d = ImageDraw.Draw(im)

    kf, vf = B.font(38, bold=True), B.font(38, bold=False)
    rows = [(k, v, wrap(d, str(v), vf, 700)[:2]) for k, v in pairs]
    block = sum(46 + len(ls) * 46 + 40 for _, _, ls in rows)
    x = 1060
    y = max(400, py + (prod.height - block) // 2)
    for k, v, lines in rows:
        d.rectangle([x, y + 12, x + 14, y + 26], fill=ORANGE)
        d.text((x + 36, y), k, font=kf, fill=MUTED)
        for i, line in enumerate(lines):
            d.text((x + 36, y + 46 + i * 46), line, font=vf, fill=INK)
        y += 46 + len(lines) * 46 + 40
        if y > 1620:
            break
    wordmark(d)
    im.convert("RGB").save(dest, "JPEG", quality=92, optimize=True)
    return dest


def pack_page(photo, data, dest):
    pack = int(data.get("pack") or 1)
    if pack <= 1:
        return None
    unit = _unit_word(data.get("title") or "")
    im, d = page(None, None)
    nf = B.font(430, bold=True)
    label = f"{pack:,}"
    w = d.textlength(label, font=nf)
    d.text(((CANVAS - w) / 2, 250), label, font=nf, fill=INK)
    sf = B.font(66, bold=True)
    sub = f"{unit} PER ORDER".upper()
    d.text(((CANVAS - d.textlength(sub, font=sf)) / 2, 720), sub, font=sf, fill=ORANGE)

    prod = fit(photo, 1000, 780)
    px, py = (CANVAS - prod.width) // 2, 880
    im = shadow(im, prod, px, py)
    im.paste(prod, (px, py), prod)
    d = ImageDraw.Draw(im)
    wordmark(d)
    im.convert("RGB").save(dest, "JPEG", quality=92, optimize=True)
    return dest


_DIM_KEYS = ("Height", "Width", "Depth", "Length", "Thickness", "Ring Size", "Sheet Size")


def size_page(photo, data, dest):
    specs = data.get("specs") or {}
    dims = [(k, specs[k]) for k in _DIM_KEYS if k in specs][:4]
    if not dims:
        return None
    im, d = page("Dimensions", "Measured")
    prod = fit(photo, 1000, 950)
    px, py = (CANVAS - prod.width) // 2, 430
    im = shadow(im, prod, px, py)
    im.paste(prod, (px, py), prod)
    d = ImageDraw.Draw(im)

    # callouts down both sides, each on its own leader line to the product edge
    kf, vf = B.font(36, bold=True), B.font(52, bold=True)
    for i, (k, v) in enumerate(dims):
        left = i % 2 == 0
        y = 520 + (i // 2) * 300
        tx = 130 if left else CANVAS - 130
        ex = px - 30 if left else px + prod.width + 30
        d.line([(tx if left else tx - 0, y + 34), (ex, y + 34)], fill=HAIR, width=4)
        d.ellipse([ex - 9, y + 25, ex + 9, y + 43], fill=ORANGE)
        anchor_k = "la" if left else "ra"
        d.text((tx, y - 14), k.upper(), font=kf, fill=MUTED, anchor=anchor_k)
        d.text((tx, y + 40), str(v), font=vf, fill=INK, anchor=anchor_k)
    wordmark(d)
    im.convert("RGB").save(dest, "JPEG", quality=92, optimize=True)
    return dest


def features_page(photo, data, dest):
    """Four tiles, each one a fact from the feed turned into what it means for the buyer.

    Nothing invented: a tile only appears when the spec behind it exists, so the page can be
    three tiles rather than claim a fourth."""
    specs = data.get("specs") or {}
    tiles = []
    for key, head in (("Basic Material", "Material"), ("Specific Material", "Made from"),
                      ("Durability", "Built for"), ("Sheet Capacity", "Holds"),
                      ("Capacity", "Capacity"), ("Color", "Colour"),
                      ("Features", "Feature"), ("Includes", "Includes"),
                      ("Type", "Type"), ("Style", "Style")):
        if key in specs and len(tiles) < 4:
            tiles.append((head, str(specs[key])))
    if int(data.get("pack") or 1) > 1 and len(tiles) < 4:
        tiles.append(("Quantity", f"{int(data['pack']):,} per order"))
    # Some feed rows are nothing but dimensions. Rather than drop the page, fill it from
    # whatever the feed did give — it is all true either way.
    if len(tiles) < 4:
        used = {h.lower() for h, _ in tiles}
        for k, v in specs.items():
            if len(tiles) >= 4:
                break
            if k.lower() not in used and str(v).strip():
                tiles.append((k, str(v)))
                used.add(k.lower())
    if len(tiles) < 2:
        return None

    im, d = page("At a glance", "What you get")
    prod = fit(photo, 620, 620)
    px, py = (CANVAS - prod.width) // 2, 330
    im = shadow(im, prod, px, py)
    im.paste(prod, (px, py), prod)
    d = ImageDraw.Draw(im)

    hf, bf = B.font(34, bold=True), B.font(46, bold=True)
    # tile height follows the tallest tile's content, so a one-line value does not sit in a
    # box built for three
    deep = max(len(wrap(d, b, bf, 830 - 80)[:3]) for _, b in tiles[:4])
    th = 124 + deep * 58
    x0, y0, tw, gap = 120, CANVAS - 260 - (th * 2 + 60), 830, 60
    for i, (head, body) in enumerate(tiles[:4]):
        cx = x0 + (i % 2) * (tw + gap)
        cy = y0 + (i // 2) * (th + gap)
        d.rounded_rectangle([cx, cy, cx + tw, cy + th], radius=22, outline=HAIR, width=3)
        d.rectangle([cx, cy + 26, cx + 8, cy + 78], fill=ORANGE)
        d.text((cx + 40, cy + 32), head.upper(), font=hf, fill=MUTED)
        for j, line in enumerate(wrap(d, body, bf, tw - 80)[:3]):
            d.text((cx + 40, cy + 96 + j * 58), line, font=bf, fill=INK)
    wordmark(d, y=CANVAS - 120, size=62)
    im.convert("RGB").save(dest, "JPEG", quality=92, optimize=True)
    return dest


def _unit_word(title):
    t = title.lower()
    for word, plural in (("bag", "BAGS"), ("cover", "COVERS"), ("binder", "BINDERS"),
                         ("folder", "FOLDERS"), ("glove", "GLOVES"), ("tarp", "TARPS"),
                         ("box", "BOXES"), ("roll", "ROLLS"), ("sheet", "SHEETS")):
        if word in t:
            return plural
    return "PIECES"


PAGES = [("02_specs", specs_page), ("03_pack", pack_page),
         ("04_size", size_page), ("05_features", features_page)]


def render(folder, data, limit=4):
    """Returns the files written. A page that has no data to stand on is skipped, not faked."""
    # The un-branded download, not 01_main.jpg — the main image already carries the wordmark,
    # and re-using it would stamp HOANK twice on every page.
    photos = os.path.join(os.path.dirname(folder), "_photos")
    src = os.path.join(photos, f"{data.get('zoro_no', '')}.jpg")
    if not os.path.exists(src):
        src = os.path.join(folder, "images", "01_main.jpg")
    if not os.path.exists(src):
        return []
    photo = cutout(src)
    made = []
    for name, fn in PAGES[: max(1, min(limit, len(PAGES)))]:
        dest = os.path.join(folder, "images", f"{name}.jpg")
        try:
            if fn(photo, data, dest):
                made.append(dest)
        except Exception as e:
            print(f"    {name}: {type(e).__name__}: {e}")
    return made
