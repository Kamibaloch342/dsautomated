#!/usr/bin/env python3
"""
build_shipping_box_batch.py — cut an upload batch out of SHIPPING_BOX_HOANK_ALL.xlsm, one UPC per box.

The ALL workbook is the pool of HOANK shipping-box listings not yet on Amazon (every box "GTIN
Exempt", which Amazon refuses for this category — the test upload of 12 Oct 2026 went through
only with a real UPC). Given a list of purchased UPCs, this writes a new .xlsm holding exactly
that many boxes, in sheet order, a variation family always whole (a parent row + all its
children, so no family is split across uploads). For every box it sets:

  Product Id Type / Product Id   UPC / the next unused code (written as text)
  Release Date, Merchant Release Date, Offering Release Date (US)   --release (default 2026-10-01)
  Main Image URL      <sku>_main_zoro.jpg — Zoro's own product photo, nothing added (owner,
                      13 Oct 2026: "keep the same zoro image unbranded, no need to label"). The
                      photo is only enlarged to 1600 px so Amazon's zoom works, and re-framed to
                      88 % fill on pure white when it fills less than 85 % (Amazon G1881), or
                      given a thin white margin when Zoro framed it edge to edge. A Zoro photo
                      carrying a text badge is replaced by Zoro's photo of a box of the same
                      size and colour (has_text_badge / clean_photo).
  Other images 2-8    (parent rows: 1-7) the 7 HOANK brand images as JPEG copies — same pictures, about a sixth of
                      the PNG's size, because Amazon fetches them from this Mac through Tailscale
                      (a 3.3 MB PNG took 39 s from outside). The PNGs stay for the live listings.

Two gaps in the ALL sheet are filled here (found by the 13 Oct pre-upload check; both would make
Amazon refuse or orphan a variation family):
  Set Name (BZ)       a child's variation theme is SET_NAME, but fill_shipping_box_template.py put
                      the varying value in Package Size Name (BO) only — BZ gets the BO text, which
                      is unique inside every family.
  Parent rows         Product Description, Bullet Points and "Are batteries required?" are required
                      on a parent too — the description is written from the box size, the bullets
                      are the ones every child of the family shares word for word, batteries = No.

Everything else in each row is copied unchanged. The sheet is edited inside the zip, so the
template's dropdowns, hidden sheets and macros survive. Boxes already live (the store's ALR, by
SKU and by the SKU without its prefix) and UPCs already on any listing are refused.

    python build_shipping_box_batch.py --upcs upcs.txt --out ~/Downloads/Dropshipping/BATCH.xlsm
"""
import argparse
import csv
import html
import os
import re
import sys
import zipfile

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
IMG_DIR = os.path.join(OUT_DIR, "images")
PHOTOS = os.path.join(OUT_DIR, "zoro_photos")
CONST_DIR = os.path.join(HERE, "constant_images")
DL = os.path.expanduser("~/Downloads/Dropshipping")
SHEET = "xl/worksheets/sheet5.xml"          # the "Template" sheet
FIRST = 8                                   # first data row
BASE = "https://kamrans-macbook-air-1.tailbd8602.ts.net/pl_images/"

COL_LEVEL, COL_PARENT = "D", "E"
COL_ID_TYPE, COL_ID = "J", "K"
DATE_COLS = ("BF", "CJ", "DR")              # Release Date, Merchant Release Date, Offering Release Date (US)
COL_MAIN = "V"
COL_DESC, COL_BULLETS, COL_BATT = "AF", ("AG", "AH", "AI", "AJ", "AK"), "EV"
COL_THEME, COL_PKG_NAME, COL_SET_NAME = "F", "BO", "BZ"
CONST_COLS = ("X", "Y", "Z", "AA", "AB", "AC", "AD")     # other images 2..8
SIDE, FILL = 1600, 0.88            # re-framed photos: the item alone, not its shadow, clears 85 %


def check_digit_ok(u):
    return (len(u) == 12 and u.isdigit()
            and (10 - (3 * sum(map(int, u[0:11:2])) + sum(map(int, u[1:11:2]))) % 10) % 10 == int(u[11]))


def col_idx(c):
    n = 0
    for ch in c:
        n = n * 26 + ord(ch) - 64
    return n


def cells(row):
    """[(col, xml)] of one <row>."""
    return [(c, p) for p, c in re.findall(r'(<c r="([A-Z]+)\d+"[^>]*?(?:/>|>.*?</c>))', row, re.S)]


def value(row, col, ss):
    for c, p in cells(row):
        if c != col:
            continue
        v = re.search(r"<v>(.*?)</v>", p, re.S)
        if 't="s"' in p.split(">", 1)[0] and v:
            return ss[int(v.group(1))]
        t = re.search(r"<t[^>]*>(.*?)</t>", p, re.S)
        return html.unescape(t.group(1)) if t else (v.group(1) if v else None)
    return None


def set_cells(row, rn, new):
    """Replace / add inline-string cells {col: text}, keeping each cell's style."""
    parts = cells(row)
    style = {c: (re.search(r' s="(\d+)"', p.split(">", 1)[0]) or [None, None])[1] for c, p in parts}
    keep = [(c, p) for c, p in parts if c not in new]
    for c, text in new.items():
        st = f' s="{style[c]}"' if style.get(c) else ""
        keep.append((c, f'<c r="{c}{rn}"{st} t="inlineStr"><is><t>{html.escape(text)}</t></is></c>'))
    keep.sort(key=lambda x: col_idx(x[0]))
    return re.match(r"<row [^>]*>", row).group(0) + "".join(p for _, p in keep) + "</row>"


def renumber(row, new):
    row = re.sub(r'<row r="\d+"', f'<row r="{new}"', row, count=1)
    return re.sub(r'(<c r="[A-Z]+)\d+"', rf'\g<1>{new}"', row)


def zoro_main(src, dst, white=False):
    """Zoro's photo as Amazon's MAIN image: nothing added, 1600 px, pure white, >= 85 % fill."""
    im = Image.open(src).convert("RGB")
    bb = im.convert("L").point(lambda p: 0 if p > 247 else 255).getbbox() or (0, 0) + im.size
    fill = max(bb[2] - bb[0], bb[3] - bb[1]) / max(im.size)
    if fill >= 0.85:                 # the photo as Zoro framed it, enlarged
        if not white:                # its faint off-white margin → pure 255 (only what touches the
            im = _clear_edge_white(im)   # edge); a white box is left as Zoro shot it — its faces are
                                         # nearly background-white and would lose their outline
        part, scale = im, SIDE / max(im.size)
        if min(bb[0], bb[1], im.width - bb[2], im.height - bb[3]) <= 3:
            scale *= 0.96            # Zoro framed it edge to edge: a thin white margin keeps the
                                     # whole item inside the frame (40 of the first 405)
    else:                            # too much white margin: the same photo, cropped to the item
        # A loosely framed photo often has a grey floor shadow round the item; cropped and put on
        # white it shows as an off-white panel (G4285565, 13 Oct). The shadow is faded out first.
        im = im if white else _clear_edge_white(_fade_shadow(im))
        bb = im.convert("L").point(lambda p: 0 if p > 247 else 255).getbbox() or (0, 0) + im.size
        part = im.crop(bb)
        scale = SIDE * FILL / max(part.size)
    part = part.resize((max(1, round(part.width * scale)), max(1, round(part.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGB", (SIDE, SIDE), (255, 255, 255))
    canvas.paste(part, ((SIDE - part.width) // 2, (SIDE - part.height) // 2))
    canvas.save(dst, "JPEG", quality=92, optimize=True, dpi=(72, 72))
    return fill


def _white_item(im):
    """True for a white box: most of what is not background is light (its faces sit at 236-247)."""
    lo = np.asarray(im).min(axis=2)
    return (lo < 236).sum() < 0.6 * (lo <= 247).sum()


def _fade_shadow(im, lo_from=200, lo_to=245, chroma=18):
    """Fade a grey floor shadow into white — gradually, so no cut-out edge is left (a hard threshold
    left a stair-stepped outline on G4285565). Only grey pixels move: a kraft or brown face is far
    too coloured to qualify, and a white item (whose faces are themselves light grey) is left
    alone entirely."""
    if _white_item(im):
        return im                    # its own faces would fade too
    a = np.asarray(im).astype(float)
    lo, hi = a.min(axis=2), a.max(axis=2)
    t = np.clip((lo - lo_from) / (lo_to - lo_from), 0, 1) * ((hi - lo) <= chroma)
    a = a * (1 - t[..., None]) + 255 * t[..., None]
    return Image.fromarray(a.round().astype(np.uint8))


def _clear_edge_white(im, floor=248):
    """Near-white (>= floor) reachable from the photo's edge becomes pure 255; nothing else moves."""
    a = np.asarray(im).copy()
    # .copy(): Image.fromarray hands back a READ-ONLY image, and ImageDraw.floodfill on it fails
    # silently (it swallows the ValueError) — the first version of this cleared nothing.
    mask = Image.fromarray(np.where(a.min(axis=2) >= floor, 255, 0).astype(np.uint8)).copy()
    w, h = mask.size
    for xy in [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)] + \
              [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)]:
        if mask.getpixel(xy) == 255:
            ImageDraw.floodfill(mask, xy, 128, thresh=0)
    a[np.asarray(mask) == 128] = 255
    return Image.fromarray(a)


_BO = re.compile(r"(.+?) - (\w+ Wall) - (\w+) - (\d+) Pack(?: \((Style \d+)\))?$")


def _join(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def parent_text(kid_rows, ss):
    """Description, bullets and battery answer for a variation parent, from its children only.
    The description names only what really varies inside the family (pack count, board grade,
    colour, style) — read from each child's Package Size Name, e.g. '32 ECT - Single Wall - Kraft
    - 25 Pack'. If one does not parse, the sentence makes no claim about the options at all."""
    first = value(kid_rows[0], COL_DESC, ss) or ""
    m = re.match(r"HOANK (.+?) corrugated shipping boxes", first)
    size = m.group(1) if m else ""
    parts = [_BO.match((value(k, COL_PKG_NAME, ss) or "").strip()) for k in kid_rows]
    desc = ""
    if size and all(parts):
        packs = sorted({int(p[4]) for p in parts})
        grades = sorted({f"{p[1]} {p[2].lower()}" for p in parts})
        colours = sorted({p[3].lower() for p in parts})
        styles = sorted({p[5] or "Style 1" for p in parts})
        opts = []
        if len(packs) > 1:
            opts.append("pack sizes of " + _join([str(n) for n in packs]))
        if len(grades) > 1:
            opts.append("board grades (" + ", ".join(grades) + ")")
        if len(colours) > 1:
            opts.append(_join(colours) + " colours")
        if not opts and len(styles) > 1:
            opts.append(f"{len(styles)} styles")
        head = f"HOANK {size} corrugated shipping boxes" + (f", {packs[0]} per pack" if len(packs) == 1 else "")
        desc = (head + (", available in " + _join(opts) if opts else "")
                + ". Built for moving, shipping, mailing, packing and storage.")
    elif size:
        desc = (f"HOANK {size} corrugated shipping boxes for moving, shipping, mailing, packing and "
                f"storage; each option lists its pack count, board grade and colour.")
    shared = []
    for c in COL_BULLETS:
        b = value(kid_rows[0], c, ss)
        if b and all(value(k, c, ss) == b for k in kid_rows[1:]):
            shared.append(b)
    out = {COL_BATT: "No"}
    if desc:
        out[COL_DESC] = desc
    out.update(dict(zip(COL_BULLETS, shared)))
    return out


def has_text_badge(path):
    """Zoro stamps some photos with a red marketing badge ('WITHSTANDS HIGH HUMIDITY'). Text on a
    MAIN image breaks Amazon's rule, and it is not on the box the buyer gets."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    return ((a[..., 0] > 180) & (a[..., 1] < 90) & (a[..., 2] < 110)).mean() > 0.003


def clean_photo(sku, by_sku, siblings):
    """The Zoro photo for *sku*, or — when it carries a text badge — the photo of a box of the same
    size and colour (same wall preferred): first its own family, then any listing of that size."""
    info = by_sku.get(sku) or {}
    name = lambda r: os.path.basename(((r or {}).get("zoro_image_url") or "").split("?")[0])
    own = name(info)
    path = os.path.join(PHOTOS, own) if own else ""
    if not own or not os.path.exists(path):
        sys.exit(f"{sku}: Zoro photo missing ({info.get('zoro_image_url') or 'no URL in listings.csv'})")
    if not has_text_badge(path):
        return path, ""
    pool = [by_sku[s] for s in siblings if s in by_sku] + \
           [r for r in by_sku.values() if r.get("relationship") != "PARENT" and r.get("size") == info.get("size")]
    cands = [r for r in pool if r.get("color") == info.get("color") and name(r) and name(r) != own]
    cands.sort(key=lambda r: r.get("wall") != info.get("wall"))          # same wall first, stable
    for r in cands:
        alt = os.path.join(PHOTOS, name(r))
        if os.path.exists(alt) and not has_text_badge(alt):
            return alt, f"Zoro photo {own} has a text badge — same-size {info.get('color')} box photo used"
    sys.exit(f"{sku}: Zoro photo {own} has a text badge and no same-size, same-colour photo exists — decide by hand")


def brand_jpegs():
    names = []
    for i in range(2, 9):
        png = os.path.join(CONST_DIR, f"hoank_const_0{i}.png")
        jpg = png[:-4] + ".jpg"
        if not os.path.exists(jpg) or os.path.getmtime(jpg) < os.path.getmtime(png):
            Image.open(png).convert("RGB").save(jpg, "JPEG", quality=90, optimize=True, progressive=True, dpi=(72, 72))
        names.append(os.path.basename(jpg))
    return names


def live_listings(paths):
    """(seller SKUs, SKU tails without the HOA-/HO- prefix, every text) of the given listing reports."""
    skus, tails, text = set(), set(), ""
    for p in paths:
        if not os.path.exists(p):
            sys.exit(f"listing report not found: {p}")
        raw = open(p, encoding="utf-8-sig", errors="replace").read()
        text += raw
        for r in csv.DictReader(raw.splitlines(), delimiter="\t"):
            s = (r.get("seller-sku") or "").strip()
            if s:
                skus.add(s)
                tails.add(re.sub(r"^[A-Z]+-Kam-", "", s))
    return skus, tails, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(DL, "SHIPPING_BOX_HOANK_ALL.xlsm"))
    ap.add_argument("--upcs", required=True, help="text file, one UPC per line")
    ap.add_argument("--out", required=True)
    ap.add_argument("--map", help="CSV of sku → UPC written beside --out by default")
    ap.add_argument("--release", default="2026-10-01")
    ap.add_argument("--store-alr", default=os.path.join(DL, "2. Hypersale", "Hypersale ALR.txt"),
                    help="HOANK's listing report: boxes already live there are left out")
    ap.add_argument("--listings", nargs="*", default=[os.path.join(DL, "1. AB ECOM", "Discounted ALR.txt")],
                    help="other stores' listing reports: a UPC on any of them is refused too")
    a = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.release):
        sys.exit("--release must be YYYY-MM-DD")

    upcs = [u.strip() for u in open(a.upcs) if u.strip()]
    bad = [u for u in upcs if not check_digit_ok(u)]
    dup = {u for u in upcs if upcs.count(u) > 1}
    live_skus, live_tails, live_text = live_listings([a.store_alr])
    live_text += live_listings(a.listings)[2]
    used = [u for u in upcs if u in live_text]
    if bad or dup or used:
        sys.exit(f"refusing: bad check digit {bad}, duplicated {sorted(dup)}, already on a listing {used}")

    by_sku = {r["sku"]: r for r in csv.DictReader(open(os.path.join(OUT_DIR, "listings.csv"), encoding="utf-8"))}
    consts = brand_jpegs()

    zin = zipfile.ZipFile(a.src)
    ss = [html.unescape(re.sub(r"<[^>]+>", "", m)) for m in
          re.findall(r"<si>(.*?)</si>", zin.read("xl/sharedStrings.xml").decode("utf-8"), re.S)]
    s = zin.read(SHEET).decode("utf-8")
    a0, b0 = s.index("<sheetData>") + len("<sheetData>"), s.index("</sheetData>")
    rows = re.findall(r'(<row r="(\d+)"[^>]*>.*?</row>)', s[a0:b0], re.S)
    head = [r for r, n in rows if int(n) < FIRST]

    # groups in sheet order: a parent and the children under it, or one standalone box
    groups, skipped_live = [], []
    for row, n in rows:
        if int(n) < FIRST:
            continue
        sku = (value(row, "A", ss) or "").strip()
        if not sku:
            continue
        level = value(row, COL_LEVEL, ss)
        if sku in live_skus or re.sub(r"^[A-Z]+-Kam-", "", sku) in live_tails:
            skipped_live.append(sku)
            continue
        if level == "Parent":
            groups.append({"parent": (sku, row), "kids": []})
        elif level == "Child":
            if not groups or not groups[-1]["parent"] or groups[-1]["parent"][0] != value(row, COL_PARENT, ss):
                sys.exit(f"child {sku} is not directly under its parent — the sheet order changed")
            groups[-1]["kids"].append((sku, row))
        else:
            groups.append({"parent": None, "kids": [(sku, row)]})
    groups = [g for g in groups if g["kids"]]            # a parent whose every child is live goes too

    want, chosen, n_boxes = len(upcs), [], 0
    for g in groups:
        if n_boxes + len(g["kids"]) <= want:
            chosen.append(g)
            n_boxes += len(g["kids"])
        if n_boxes == want:
            break
    if n_boxes != want:
        sys.exit(f"only {n_boxes} boxes fit for {want} UPCs")

    out_rows, mapping, it, rn = list(head), [], iter(upcs), FIRST
    fills, notes = [], []
    for g in chosen:
        if g["parent"]:
            # a parent row carries the brand images only: same pictures, the light JPEG copies
            prow = g["parent"][1]
            prn = int(re.search(r'<row r="(\d+)"', prow).group(1))
            swap = {}
            for c, _ in cells(prow):
                v = value(prow, c, ss) or ""
                m = re.fullmatch(r"(https://\S+/)(hoank_const_0[2-8])\.png", v)
                if m:
                    swap[c] = m.group(1) + m.group(2) + ".jpg"
            fill = {c: v for c, v in parent_text([r for _, r in g["kids"]], ss).items()
                    if not (value(prow, c, ss) or "").strip()}
            fill.update(swap)
            out_rows.append(renumber(set_cells(prow, prn, fill) if fill else prow, rn))
            rn += 1
        sibs = [k for k, _ in g["kids"]]
        for sku, row in g["kids"]:
            photo, note = clean_photo(sku, by_sku, sibs)
            if note:
                notes.append((sku, note))
            main = f"{sku}_main_zoro.jpg"
            white = str((by_sku.get(sku) or {}).get("color") or "").strip().lower() == "white"
            fills.append(zoro_main(photo, os.path.join(IMG_DIR, main), white=white))
            upc = next(it)
            new = {COL_ID_TYPE: "UPC", COL_ID: upc, COL_MAIN: BASE + main}
            new.update({c: a.release for c in DATE_COLS})
            new.update({c: BASE + f for c, f in zip(CONST_COLS, consts)})
            if (g["parent"] and "SET_NAME" in (value(row, COL_THEME, ss) or "").upper()
                    and not (value(row, COL_SET_NAME, ss) or "").strip()):
                new[COL_SET_NAME] = value(row, COL_PKG_NAME, ss) or ""
            out_rows.append(renumber(set_cells(row, int(re.search(r'<row r="(\d+)"', row).group(1)), new), rn))
            mapping.append({"row": rn, "sku": sku, "parent": g["parent"][0] if g["parent"] else "",
                            "upc": upc, "main_image": main, "zoro_photo": os.path.basename(photo),
                            "photo_note": note})
            rn += 1

    last = rn - 1
    s2 = s[:a0] + "".join(out_rows) + s[b0:]
    s2 = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:IN{last}"/>', s2, count=1)
    s2 = re.sub(r"<hyperlinks>.*?</hyperlinks>", "", s2, flags=re.S)
    tmp = a.out + ".tmp"
    with zipfile.ZipFile(tmp, "w") as zout:
        for item in zin.infolist():
            data = s2.encode("utf-8") if item.filename == SHEET else zin.read(item.filename)
            zout.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED)
    os.replace(tmp, a.out)
    mp = a.map or os.path.splitext(a.out)[0] + "_UPC_map.csv"
    with open(mp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(mapping[0]))
        w.writeheader()
        w.writerows(mapping)
    fam = sum(1 for g in chosen if g["parent"])
    print(f"{os.path.basename(a.out)}: {n_boxes} boxes ({fam} families with {sum(len(g['kids']) for g in chosen if g['parent'])} "
          f"variations, {sum(1 for g in chosen if not g['parent'])} single listings) + {fam} parent rows = rows {FIRST}..{last}")
    print(f"UPCs {upcs[0]} … {upcs[-1]}; release date {a.release}; already live, left out: {skipped_live}")
    print(f"main images: {len(fills)} Zoro photos, {sum(1 for f in fills if f < 0.85)} re-framed (fill < 85%); map → {mp}")
    for sku, note in notes:
        print(f"  photo swapped — {sku}: {note}")


if __name__ == "__main__":
    main()
