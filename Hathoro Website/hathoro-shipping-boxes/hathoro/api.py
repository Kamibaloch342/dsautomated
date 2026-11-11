"""One-call helpers for making listing images from Python."""

import copy

from .data import parse_listing
from .design import ROOT, Theme, load_json
from .images import render_listing


def merge(base, override):
    """Deep-merge `override` into a copy of `base` (dicts merge, everything else replaces)."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def make_images(size, pack, ect=None, wall=None, made_in_usa=False, photo=None, text=None,
                theme=None, out_dir="output/manual", only=None, sku="MANUAL"):
    """Render the image set for a box size without a database row.

    size: "22x18x8" or (22, 18, 8). photo: supplier photo for image 1 (path).
    text: overrides for content.json, e.g.
    {"pack": {"badge": "{pack} BOXES"}, "dimensions": {"subline": "Perfect for Moving"}}.
    theme: overrides for theme.json, e.g. {"colors": {"orange": "#E85D04"}}.
    """
    if isinstance(size, str):
        size = [float(x) for x in size.lower().replace("×", "x").split("x")]
    L, W, H = size
    desc = f"Shipping Box, {L:g} in x {W:g} in x {H:g} in"
    if wall:
        desc += f", {wall}"
    if ect:
        desc += f", {ect} ECT"
    row = {"zoro_no": sku, "short_description": desc, "sales_price": 1.0,
           "min_order_qty": pack, "country_of_origin": "USA" if made_in_usa else None}
    li = parse_listing(row)
    content = merge(load_json(ROOT / "content.json"), text)
    th = Theme()
    th.data = merge(th.data, theme)
    th.colors = th.data["colors"]
    out = ROOT / out_dir if not str(out_dir).startswith("/") else out_dir
    return render_listing(th, li, content, out, only=only, product_photo=photo)
