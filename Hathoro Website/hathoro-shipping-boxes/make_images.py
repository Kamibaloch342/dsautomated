#!/usr/bin/env python3
"""Make listing images for any size without the database.

    python make_images.py --size 22x18x8 --pack 25 --ect 32 --made-in-usa
    python make_images.py --size 12x9x3 --pack 10 --only main,dimensions
    python make_images.py --size 22x18x8 --pack 25 --set "pack.badge={pack} BOXES" \\
                          --set "dimensions.subline=Perfect for Moving\\nand Storage"

--set changes any text in content.json for this run (section.key=value; use \\n for a new
line). Edit content.json itself to change it permanently.
"""

import argparse

from hathoro.api import make_images
from hathoro.images import TEMPLATES


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", required=True, help="LxWxH in inches, e.g. 22x18x8")
    ap.add_argument("--pack", type=int, required=True, help="boxes per pack")
    ap.add_argument("--ect", type=int, help="edge crush rating, e.g. 32")
    ap.add_argument("--wall", choices=["Single Wall", "Double Wall"])
    ap.add_argument("--made-in-usa", action="store_true")
    ap.add_argument("--photo", help="supplier product photo for image 1")
    ap.add_argument("--only", help="comma list: " + ",".join(n for n, _ in TEMPLATES))
    ap.add_argument("--set", action="append", default=[], metavar="SECTION.KEY=VALUE")
    ap.add_argument("--out", default="output/manual")
    args = ap.parse_args()

    text = {}
    for item in args.set:
        key, _, value = item.partition("=")
        section, _, field = key.partition(".")
        text.setdefault(section, {})[field] = value.replace("\\n", "\n")

    only = set(args.only.split(",")) if args.only else None
    for name, path in make_images(args.size, args.pack, ect=args.ect, wall=args.wall,
                                  made_in_usa=args.made_in_usa, photo=args.photo, text=text,
                                  out_dir=args.out,
                                  only=only):
        print(f"{name:11s} {path}")


if __name__ == "__main__":
    main()
