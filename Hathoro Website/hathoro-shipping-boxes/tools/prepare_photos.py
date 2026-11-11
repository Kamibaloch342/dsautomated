#!/usr/bin/env python3
"""One-time prep of the fixed template photos in assets/photos/ from assets/photos/source/.

    python tools/prepare_photos.py

- open_box.png      Hathoro open box, cropped (logo removed), background levelled to pure white
- flat_stack.png    stack of flattened boxes, purple background keyed out to transparency
- closeup.jpg       open box close-up (right half of the Made in USA image)

When you get better (higher resolution) photos, put them in assets/photos/source/, adjust
the crop boxes below if needed, run this again, then update the anchor points for the
open box in theme.json -> photos (check them with tools/check_anchors.py).
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "photos" / "source"
OUT = ROOT / "assets" / "photos"


def level_white(rgb, start=236):
    """Push the near-white studio background to pure white without touching the product."""
    a = np.asarray(rgb, np.float32)
    lo = a.min(axis=2, keepdims=True)
    t = np.clip((lo - start) / (250 - start), 0, 1)       # 0 = product, 1 = background
    out = a * (1 - t) + 255 * t
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))


def key_purple(rgb):
    """Alpha from how much bluer than red a pixel is (kraft is red/orange, backdrop is blue)."""
    a = np.asarray(rgb, np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    d = b - r
    alpha = np.clip((70 - d) / 95, 0, 1)
    alpha = np.asarray(Image.fromarray((alpha * 255).astype(np.uint8))
                       .filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.7)),
                       np.float32) / 255
    # remove the blue spill on the edges
    b2 = np.minimum(b, g * 0.92 + 4)
    out = np.dstack([r, g, b2, alpha * 255]).clip(0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    box = Image.open(SRC / "hathoro_open_box.webp").convert("RGB")
    level_white(box.crop((30, 360, 1240, 1092))).save(OUT / "open_box.png")

    stack = Image.open(SRC / "flat_pack_stack.webp").convert("RGB")
    key_purple(stack.crop((328, 372, 1024, 1000))).save(OUT / "flat_stack.png")

    close = Image.open(SRC / "made_in_usa_closeup.webp").convert("RGB")
    close.crop((590, 0, 1024, 1024)).save(OUT / "closeup.jpg", quality=95)
    print("photos written to", OUT)


if __name__ == "__main__":
    main()
