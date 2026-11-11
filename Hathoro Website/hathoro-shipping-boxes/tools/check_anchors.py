#!/usr/bin/env python3
"""Draw the open-box anchor points from theme.json on the photo, to check them after a swap.

    python tools/check_anchors.py      ->  output/anchors_open_box.png
"""

import sys
from pathlib import Path

from PIL import ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hathoro.design import ROOT, Theme  # noqa: E402


def main():
    img, spec = Theme().photo("open_box")
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)
    colors = {"width": (0, 120, 255), "length": (230, 0, 0), "height": (0, 170, 0)}
    for name, col in colors.items():
        p0, p1 = spec["anchors"][name]
        d.line([tuple(p0), tuple(p1)], fill=col, width=4)
        for x, y in (p0, p1):
            d.ellipse([x - 7, y - 7, x + 7, y + 7], outline=col, width=3)
        d.text((p0[0] + 10, p0[1] + 10), name, fill=col)
    cx, cy = spec["anchors"]["centre"]
    d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=(255, 0, 255))
    out = ROOT / "output" / "anchors_open_box.png"
    out.parent.mkdir(exist_ok=True)
    img.save(out)
    print(out)


if __name__ == "__main__":
    main()
