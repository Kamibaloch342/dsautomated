"""The five Hathoro listing images.

1 = the supplier's own product photo, centred. 2-5 = fixed real template photos from
theme.json -> photos; only their text (content.json) changes per listing.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image

from .design import DESIGN, Canvas, callout_boxes, edge_callout, fit_photo, trim_white


def fill(template, fields):
    """Recursively apply str.format(**fields) to every string in a content block."""
    if isinstance(template, str):
        return template.format(**fields)
    if isinstance(template, list):
        return [fill(x, fields) for x in template]
    if isinstance(template, dict):
        return {k: fill(v, fields) for k, v in template.items() if not k.startswith("_")}
    return template


def cover(img, w, h, focus=(0.5, 0.5)):
    """Crop img to aspect w:h around a focus point (fractions of width/height)."""
    target = w / h
    if img.width / img.height > target:
        cw, ch = round(img.height * target), img.height
    else:
        cw, ch = img.width, round(img.width / target)
    x0 = min(max(round(focus[0] * img.width - cw / 2), 0), img.width - cw)
    y0 = min(max(round(focus[1] * img.height - ch / 2), 0), img.height - ch)
    return img.crop((x0, y0, x0 + cw, y0 + ch))


def _box(o, s, size):
    return (o[0], o[1], o[0] + size[0] * s, o[1] + size[1] * s)


def _content_top(c, header_bottom):
    return header_bottom + c.theme["layout"]["content_gap"]


def _tiles_band(c):
    lay = c.theme["layout"]
    y1 = DESIGN - lay["margin_bottom"]
    return y1 - lay["tiles_height"], y1


def has_white_background(img):
    a = np.asarray(img.convert("RGB"))
    border = np.concatenate([a[:4].reshape(-1, 3), a[-4:].reshape(-1, 3),
                             a[:, :4].reshape(-1, 3), a[:, -4:].reshape(-1, 3)])
    return np.median(border.min(axis=1)) >= 240


# ----------------------------------------------------------------------------- 1. main

def main_image(theme, li, content, product_photo=None):
    c = Canvas(theme)
    txt = fill(content["main"], li.fields())
    lay = theme["layout"]
    c.logo()

    if product_photo is None:
        img, _ = theme.photo("open_box")            # fallback: Hathoro template photo
    else:
        img = Image.open(product_photo).convert("RGB")
        if has_white_background(img):
            img = trim_white(img)
    pad = lay["main_padding"]
    top = c.logo_box[3] + 50 if c.logo_box else pad
    bottom = DESIGN - top                           # symmetric, so the product is centred
    if txt.get("show_info"):
        bottom = DESIGN - lay["margin_bottom"] - 190
    s, o = fit_photo(img.size, (pad, top, DESIGN - pad, bottom))
    c.photo(img, _box(o, s, img.size), "multiply")

    if txt.get("show_info"):
        x, yb = lay["margin_x"], DESIGN - lay["margin_bottom"]
        pill_w = 0
        if txt.get("pack"):
            pw, _ = c.measure("pill", txt["pack"])
            pill_w, pill_h = pw + 2 * 44, 112
            px1 = DESIGN - lay["margin_x"]
            c.rounded((px1 - pill_w, yb - pill_h, px1, yb), pill_h / 2, "orange")
            c.text((px1 - pill_w / 2, yb - pill_h / 2), txt["pack"], "pill", anchor="cm")
        max_w = DESIGN - 2 * lay["margin_x"] - pill_w - 40
        if txt.get("spec"):
            sc = c.fit_scale("main_spec", txt["spec"], max_w)
            _, sh = c.text((x, yb), txt["spec"], "main_spec", anchor="lb", scale=sc)
            yb -= sh + 22
        if txt.get("size"):
            sc = c.fit_scale("main_size", txt["size"], max_w)
            c.text((x, yb), txt["size"], "main_size", anchor="lb", scale=sc)
    return c.finish()


# ----------------------------------------------------------------------------- 2. dimensions

def _dimension_callouts(c, anchors, s, o, txt):
    a = c.theme["arrows"]

    def P(p):
        return np.asarray(p, float) * s + o

    centre = P(anchors["centre"])
    args = (a["offset"], a["label_pad"], a["trim"])
    out = []
    for key in ("width", "length"):
        p0, p1 = (P(p) for p in anchors[key])
        wh = c.measure("callout", txt[key])
        out.append((txt[key], edge_callout(p0, p1, wh, *args, away_from=centre)))
    # height: on the front face next to the front corner, like the reference image
    p0, p1 = (P(p) for p in anchors["height"])
    l0, l1 = (P(p) for p in anchors["length"])
    wh = c.measure("callout", txt["height"])
    out.append((txt["height"], edge_callout(p0, p1, wh, *args, toward=l1 - l0)))
    return out


def dimensions_image(theme, li, content):
    c = Canvas(theme)
    txt = fill(content["dimensions"], li.fields())
    lay = theme["layout"]
    c.logo()
    hb = c.header(txt["eyebrow"], txt["badge"], txt["subline"])
    photo, spec = theme.photo("open_box")
    anchors = spec["anchors"]
    region = (lay["margin_x"], _content_top(c, hb), DESIGN - lay["margin_x"],
              DESIGN - lay["margin_bottom"])

    def extras(s, o):
        return callout_boxes([co for _, co in _dimension_callouts(c, anchors, s, o, txt)])

    s, o = fit_photo(photo.size, region, extras)
    c.photo(photo, _box(o, s, photo.size), spec["blend"])
    for label, co in _dimension_callouts(c, anchors, s, o, txt):
        c.dim_arrow(co["p0"], co["p1"])
        c.text(tuple(co["center"]), label, "callout", anchor="cm")
    return c.finish()


# ----------------------------------------------------------------------------- 3. corrugated

def corrugated_image(theme, li, content):
    c = Canvas(theme)
    txt = fill(content[content_key("corrugated", li)], li.fields())
    lay = theme["layout"]
    c.logo()
    hb = c.header(txt["eyebrow"], txt["badge"], txt["subline"])
    top, bottom = _content_top(c, hb), DESIGN - lay["margin_bottom"]

    # left: real close-up photo panel showing the corrugated edges
    photo, spec = theme.photo("closeup")
    card_w = 560
    x0 = lay["margin_x"]
    crop = cover(photo, card_w, bottom - top, focus=spec.get("focus", (0.5, 0.45)))
    c.photo(crop, (x0, top, x0 + card_w, bottom), "card", radius=lay["tile_radius"])

    # right: seal + spec tiles
    col0, col1 = x0 + card_w + 40, DESIGN - lay["margin_x"]
    R = 150
    c.seal((col0 + col1) / 2, top + R, R, txt["seal_top"], str(txt["seal_main"]),
           txt["seal_sub"], txt["seal_bottom"])
    c.tile_column(txt["tiles"], col0, col1, top + 2 * R + 40, bottom)
    return c.finish()


# ----------------------------------------------------------------------------- 4. pack

def pack_image(theme, li, content):
    c = Canvas(theme)
    txt = fill(content["pack"], li.fields())
    lay = theme["layout"]
    c.logo()
    hb = c.header(txt["eyebrow"], txt["badge"], txt["subline"])
    ty0, ty1 = _tiles_band(c)
    photo, spec = theme.photo("flat_stack")
    right = DESIGN if spec.get("bleed_right") else DESIGN - lay["margin_x"]
    region = (lay["margin_x"] + 60, _content_top(c, hb), right, ty0 - 40)
    s, o = fit_photo(photo.size, region, align="bottom-right")
    c.photo(photo, _box(o, s, photo.size), spec["blend"], shadow=0.22)
    c.tiles(txt["tiles"], ty0, ty1)
    return c.finish()


# ----------------------------------------------------------------------------- 5. origin

def origin_image(theme, li, content):
    c = Canvas(theme)
    txt = fill(content[content_key("origin", li)], li.fields())
    lay = theme["layout"]
    c.logo()
    hb = c.header(txt["eyebrow"], txt["badge"], txt["subline"])
    ty0, ty1 = _tiles_band(c)
    top = _content_top(c, hb)
    photo, spec = theme.photo("open_box")
    R = 190
    region = (lay["margin_x"] + 2 * R - 30, top, DESIGN - lay["margin_x"], ty0 - 40)
    s, o = fit_photo(photo.size, region)
    c.photo(photo, _box(o, s, photo.size), spec["blend"])
    c.seal(lay["margin_x"] + R, (top + ty0 - 40) / 2 + 40, R, txt["seal_top"],
           txt["seal_main"], txt["seal_sub"], txt["seal_bottom"])
    c.tiles(txt["tiles"], ty0, ty1)
    return c.finish()


# ----------------------------------------------------------------------------- all

TEMPLATES = [
    ("main", main_image),
    ("dimensions", dimensions_image),
    ("corrugated", corrugated_image),
    ("pack", pack_image),
    ("origin", origin_image),
]


def content_key(name, li):
    """Which content.json block an image uses (fallback blocks avoid unverified claims)."""
    if name == "corrugated" and not li.has_rating:
        return "corrugated_generic"
    if name == "origin" and not li.made_in_usa:
        return "origin_not_usa"
    return name


def file_name(name, li, content):
    return fill(content[content_key(name, li)]["file"], li.fields())


def render_listing(theme, li, content, out_dir, only=None, cache=None, skip_existing=False,
                   product_photo=None):
    """Render the image set for one listing into out_dir. Returns [(name, path)].

    Images 2-5 use fixed photos, so they depend only on their filled-in text; with a
    `cache` dict, an image already rendered in this batch with the same text is reused.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name, fn in TEMPLATES:
        if only and name not in only:
            continue
        path = out_dir / file_name(name, li, content)
        if skip_existing and path.exists():
            results.append((name, path))
            continue
        if name == "main":
            img = fn(theme, li, content, product_photo)
        else:
            ck = content_key(name, li)
            key = (ck, json.dumps(fill(content[ck], li.fields()), sort_keys=True))
            img = cache.get(key) if cache is not None else None
            if img is None:
                img = fn(theme, li, content)
                if cache is not None:
                    cache[key] = img
        img.save(path, "JPEG", quality=theme["jpeg_quality"], dpi=(theme["dpi"],) * 2,
                 optimize=True, subsampling=0)
        results.append((name, path))
    return results
