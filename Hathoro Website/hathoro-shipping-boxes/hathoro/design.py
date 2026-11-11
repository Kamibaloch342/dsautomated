"""Hathoro listing design system: canvas, typography, photos, header, tiles, seal, arrows.

All positions and sizes in the templates are "design px" for a 1500 x 1500 canvas.
`Canvas` converts them to its supersampled drawing surface, so changing `canvas` in
theme.json scales every image without touching the templates. Drawing calls are queued
and run in order in finish(), so a template can measure and lay out text first.
"""

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import icons

ROOT = Path(__file__).resolve().parent.parent
DESIGN = 1500.0


def hex_rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


def load_json(path):
    return json.loads(Path(path).read_text())


def deferred(fn):
    """Queue a drawing call; queued calls run in finish()."""
    def wrapper(self, *args, **kwargs):
        self.ops.append((fn, args, kwargs))
    wrapper.__doc__ = fn.__doc__
    return wrapper


class Theme:
    def __init__(self, path=ROOT / "theme.json"):
        self.data = load_json(path)
        self.colors = self.data["colors"]
        self._fonts = {}
        self._photos = {}

    def __getitem__(self, key):
        return self.data[key]

    def color(self, name):
        return hex_rgb(self.colors.get(name, name))

    def font(self, key, size_px):
        size_px = max(1, int(round(size_px)))
        k = (key, size_px)
        if k not in self._fonts:
            self._fonts[k] = ImageFont.truetype(str(ROOT / self.data["fonts"][key]), size_px)
        return self._fonts[k]

    def photo(self, name):
        """(PIL image, spec) for a template photo listed in theme.json -> photos."""
        spec = self.data["photos"][name]
        if name not in self._photos:
            img = Image.open(ROOT / spec["file"])
            img.load()
            self._photos[name] = img
        return self._photos[name], spec


class Canvas:
    """Supersampled drawing surface. All public methods take design px."""

    def __init__(self, theme):
        self.theme = theme
        self.C = int(theme["canvas"])
        self.SS = int(theme["supersample"])
        self.k = self.C / DESIGN
        self.f = self.k * self.SS                     # design px -> surface px
        N = self.C * self.SS
        self.img = Image.new("RGB", (N, N), theme.color("background"))
        self.draw = ImageDraw.Draw(self.img)
        self.logo_box = None
        self.ops = []

    # ---- photos ----------------------------------------------------------------------
    @deferred
    def photo(self, img, box, blend="multiply", radius=0, shadow=0.0):
        """Place `img` stretched to box = (x0, y0, x1, y1) design px (may bleed off canvas).

        blend: "multiply" for photos on a white background (keeps their own shadows),
               "alpha" for cut-outs with transparency, "card" for a rounded photo panel.
        shadow: opacity of a soft ground shadow under an "alpha" cut-out.
        """
        x0, y0, x1, y1 = (int(round(v * self.f)) for v in box)
        w, h = x1 - x0, y1 - y0
        im = img.resize((w, h), Image.LANCZOS)
        if blend == "multiply":
            region = self.img.crop((x0, y0, x1, y1))
            self.img.paste(ImageChops.multiply(region, im.convert("RGB")), (x0, y0))
        elif blend == "alpha":
            alpha = im.getchannel("A")
            if shadow:
                sh = alpha.filter(ImageFilter.GaussianBlur(14 * self.f))
                sh = sh.point(lambda v: int(v * shadow))
                off = int(12 * self.f)
                self.img.paste((0, 0, 0), (x0 + off // 2, y0 + off), sh)
            self.img.paste(im.convert("RGB"), (x0, y0), alpha)
        else:  # card
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius * self.f,
                                                   fill=255)
            self.img.paste(im.convert("RGB"), (x0, y0), mask)

    # ---- text ------------------------------------------------------------------------
    def measure(self, style, text, scale=1.0):
        """(width, height) in design px of the ink box."""
        st = self.theme["text"][style]
        f = self.theme.font(st["font"], st["size"] * scale * self.f)
        tr = st.get("tracking", 0) * self.f
        x0, y0, x1, y1 = f.getbbox(text)
        w = x1 - x0 + tr * max(len(text) - 1, 0)
        return w / self.f, (y1 - y0) / self.f

    def fit_scale(self, style, text, max_w):
        scale = 1.0
        while self.measure(style, text, scale)[0] > max_w and scale > 0.3:
            scale *= 0.96
        return scale

    def text(self, xy, text, style, anchor="lt", scale=1.0, color=None):
        """Draw text with its ink box anchored at xy (design px). anchor: l/c/r + t/m/b,
        or l/c/r + s to sit on the baseline. Returns the ink size (design px) right away;
        drawing is queued."""
        self.ops.append((Canvas._draw_text, (xy, text, style, anchor, scale, color), {}))
        return self.measure(style, text, scale)

    def _draw_text(self, xy, text, style, anchor, scale, color):
        st = self.theme["text"][style]
        f = self.theme.font(st["font"], st["size"] * scale * self.f)
        fill = self.theme.color(color or st["color"])
        tr = st.get("tracking", 0) * self.f
        w, h = self.measure(style, text, scale)
        x, y = xy
        x -= {"l": 0, "c": w / 2, "r": w}[anchor[0]]
        if anchor[1] == "s":
            x0 = f.getbbox(text, anchor="ls")[0]
            self.draw.text((x * self.f - x0, y * self.f), text, font=f, fill=fill, anchor="ls")
            return
        y -= {"t": 0, "m": h / 2, "b": h}[anchor[1]]
        x0, y0 = f.getbbox(text)[:2]
        px, py = x * self.f - x0, y * self.f - y0
        if tr:
            for ch in text:
                self.draw.text((px, py), ch, font=f, fill=fill)
                px += f.getlength(ch) + tr
        else:
            self.draw.text((px, py), text, font=f, fill=fill)

    def wrap(self, style, text, max_w, scale=1.0):
        words, lines, cur = text.split(), [], ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if self.measure(style, trial, scale)[0] <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    @deferred
    def rounded(self, box, radius, fill, outline=None, width=0):
        x0, y0, x1, y1 = (v * self.f for v in box)
        self.draw.rounded_rectangle([x0, y0, x1, y1], radius=radius * self.f,
                                    fill=self.theme.color(fill) if fill else None,
                                    outline=self.theme.color(outline) if outline else None,
                                    width=int(width * self.f))

    # ---- brand blocks ----------------------------------------------------------------
    def logo(self):
        """Logo top-right (pasted at final resolution in finish()) + subtitle under it."""
        th, lay = self.theme, self.theme["layout"]
        path = th.data.get("logo")
        if not path:
            return
        logo = Image.open(ROOT / path)
        w = lay["logo_width"]
        h = w * logo.height / logo.width
        x1 = DESIGN - lay["margin_x"]
        y0 = lay["logo_top"]
        self.logo_box = (x1 - w, y0, x1, y0 + h)
        sub = th.data.get("logo_subtitle")
        if sub:
            self.text((x1 - 6, y0 + h + 10), sub, "logo_subtitle", anchor="rt")

    def header(self, eyebrow, badge, subline, top_max_w=None, sub_max_w=None):
        """Eyebrow / badge / subline block, top-left. Returns its bottom y (design px)."""
        lay = self.theme["layout"]
        x = lay["margin_x"]
        y = lay["margin_top"]
        top_max_w = top_max_w or DESIGN - 2 * lay["margin_x"] - lay["logo_width"] - 50
        if eyebrow:
            sc = self.fit_scale("eyebrow", eyebrow, top_max_w)
            _, h = self.text((x, y), eyebrow, "eyebrow", scale=sc)
            y += h + lay["eyebrow_gap"]
        if badge:
            st = self.theme["text"]["badge"]
            pad = st["pad_x"]
            sc = self.fit_scale("badge", badge, top_max_w - 2 * pad)
            w, _ = self.measure("badge", badge, sc)
            bh = st["height"]
            self.rounded((x, y, x + w + 2 * pad, y + bh), st["radius"], st["fill"])
            self.text((x + pad + w / 2, y + bh / 2), badge, "badge", anchor="cm", scale=sc)
            y += bh + lay["badge_gap"]
        if subline:
            lines = subline.split("\n")
            max_w = sub_max_w or DESIGN - 2 * lay["margin_x"]
            sc = min(self.fit_scale("subline", ln, max_w) for ln in lines)
            st = self.theme["text"]["subline"]
            lh = st["size"] * sc * st["line_height"]
            for i, ln in enumerate(lines):
                self.text((x, y + i * lh), ln, "subline", scale=sc)
            y += lh * len(lines) - st["size"] * sc * (st["line_height"] - 1)
        return y

    def tile(self, box, item):
        """Feature tile: icon disc + bold value + muted label, vertically centred in box."""
        lay = self.theme["layout"]
        x0, y0, x1, y1 = box
        self.rounded(box, lay["tile_radius"], "tile")
        ic = lay["tile_icon"]
        pad = 24
        cy = (y0 + y1) / 2
        self.icon(item["icon"], x0 + pad + ic / 2, cy, ic)
        text_x = x0 + pad + ic + 20
        text_w = x1 - pad + 6 - text_x
        sc = self.fit_scale("tile_value", item["value"], text_w)
        label = item.get("label", "")
        lsc = 1.0
        if self.measure("tile_label", label)[0] <= text_w:
            lines = [label]
        elif self.measure("tile_label", label, 0.88)[0] <= text_w:
            lines, lsc = [label], 0.88
        else:
            lines = self.wrap("tile_label", label, text_w)
        lst = self.theme["text"]["tile_label"]
        lh = lst["size"] * lsc * 1.3
        cap_v = self.measure("tile_value", "H", sc)[1]
        cap_l = self.measure("tile_label", "H", lsc)[1]
        gap = 16
        total = cap_v + gap + cap_l + lh * (len(lines) - 1)
        base = cy - total / 2 + cap_v
        self.text((text_x, base), item["value"], "tile_value", anchor="ls", scale=sc)
        for j, ln in enumerate(lines):
            self.text((text_x, base + gap + cap_l + j * lh), ln, "tile_label", anchor="ls",
                      scale=lsc)

    def tiles(self, items, y0, y1, x0=None, x1=None):
        """Row of feature tiles between y0 and y1."""
        lay = self.theme["layout"]
        x0 = lay["margin_x"] if x0 is None else x0
        x1 = DESIGN - lay["margin_x"] if x1 is None else x1
        n, gap = len(items), lay["tile_gap"]
        tw = (x1 - x0 - gap * (n - 1)) / n
        for i, it in enumerate(items):
            tx = x0 + i * (tw + gap)
            self.tile((tx, y0, tx + tw, y1), it)

    def tile_column(self, items, x0, x1, y0, y1):
        """Column of feature tiles between y0 and y1."""
        n, gap = len(items), self.theme["layout"]["tile_gap"]
        th = (y1 - y0 - gap * (n - 1)) / n
        for i, it in enumerate(items):
            ty = y0 + i * (th + gap)
            self.tile((x0, ty, x1, ty + th), it)

    @deferred
    def icon(self, name, cx, cy, size):
        """Navy disc with a white line icon."""
        r = size / 2 * self.f
        X, Y = cx * self.f, cy * self.f
        self.draw.ellipse([X - r, Y - r, X + r, Y + r], fill=self.theme.color("navy"))
        icons.draw(self.draw, name, X, Y, r * 0.62, self.theme.color("on_dark"),
                   self.theme.color("orange"))

    @deferred
    def seal(self, cx, cy, R, top, main, sub, bottom, main_scale=1.0):
        """Round certification-style seal in brand navy."""
        f = self.f
        X, Y, Rr = cx * f, cy * f, R * f
        navy, white, orange = (self.theme.color(c) for c in ("navy", "on_dark", "orange"))
        d = self.draw
        d.ellipse([X - Rr, Y - Rr, X + Rr, Y + Rr], fill=navy)
        for rr, w in ((0.94, 0.012), (0.70, 0.010)):
            d.ellipse([X - Rr * rr, Y - Rr * rr, X + Rr * rr, Y + Rr * rr], outline=white,
                      width=max(1, int(Rr * w)))
        band = 0.82 * Rr
        fnt = self.theme.font("extrabold", R * 0.125 * f)
        if top:
            self._arc_text(top, fnt, X, Y, band, white, upper=True, tracking=R * 0.02 * f)
        if bottom:
            self._arc_text(bottom, fnt, X, Y, band, white, upper=False, tracking=R * 0.02 * f)
        for side in (-1, 1):
            icons.star(d, X + side * band, Y, R * 0.06 * f, orange)
        big = self.theme.font("black", R * 0.46 * main_scale * f)
        x0, y0, x1, y1 = big.getbbox(main)
        while (x1 - x0) > 1.2 * Rr and big.size > 8:
            big = ImageFont.truetype(big.path, int(big.size * 0.94))
            x0, y0, x1, y1 = big.getbbox(main)
        d.text((X - (x0 + x1) / 2, Y - (y0 + y1) / 2 - R * 0.04 * f), main, font=big, fill=white)
        if sub:
            small = self.theme.font("bold", R * 0.105 * f)
            x0, y0, x1, y1 = small.getbbox(sub)
            d.text((X - (x0 + x1) / 2, Y + R * 0.30 * f - (y0 + y1) / 2), sub, font=small,
                   fill=white)
            bw = R * 0.34 * f
            d.rounded_rectangle([X - bw / 2, Y - R * 0.40 * f, X + bw / 2, Y - R * 0.36 * f],
                                radius=R * 0.02 * f, fill=orange)

    def _arc_text(self, text, font, cx, cy, radius, color, upper, tracking=0.0):
        widths = [font.getlength(ch) for ch in text]
        total = sum(widths) + tracking * (len(text) - 1)
        pos = -total / 2
        for ch, w in zip(text, widths):
            ang = (pos + w / 2) / radius
            size = int(font.size * 2)
            tile = Image.new("L", (size, size), 0)
            ImageDraw.Draw(tile).text((size / 2, size / 2), ch, font=font, fill=255, anchor="mm")
            if upper:
                px, py = cx + radius * math.sin(ang), cy - radius * math.cos(ang)
                tile = tile.rotate(-math.degrees(ang), resample=Image.BICUBIC)
            else:
                px, py = cx + radius * math.sin(ang), cy + radius * math.cos(ang)
                tile = tile.rotate(math.degrees(ang), resample=Image.BICUBIC)
            self.img.paste(color, (int(px - size / 2), int(py - size / 2)), tile)
            pos += w + tracking

    # ---- arrows ----------------------------------------------------------------------
    @deferred
    def dim_arrow(self, p0, p1):
        """Dashed dimension arrow with open chevron heads (design px)."""
        a = self.theme["arrows"]
        f = self.f
        p0, p1 = np.asarray(p0, float) * f, np.asarray(p1, float) * f
        width, dash, gap = a["width"] * f, a["dash"] * f, a["gap"] * f
        head, ang = a["head"] * f, math.radians(a["head_angle"])
        color = self.theme.color(a.get("color", "navy"))
        v = p1 - p0
        length = np.linalg.norm(v)
        if length < 1:
            return
        d = v / length
        r = width / 2

        def seg(q0, q1):
            self.draw.line([tuple(q0), tuple(q1)], fill=color, width=int(round(width)))
            for q in (q0, q1):
                self.draw.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=color)

        period = dash + gap
        n = max(1, int((length + gap) // period))
        start = (length - (n * period - gap)) / 2
        for i in range(n):
            s0 = start + i * period
            seg(p0 + d * s0, p0 + d * min(s0 + dash, length))
        for tip, back in ((p0, d), (p1, -d)):
            for sgn in (1, -1):
                c, s_ = math.cos(sgn * ang), math.sin(sgn * ang)
                arm = np.array([back[0] * c - back[1] * s_, back[0] * s_ + back[1] * c])
                seg(tip, tip + arm * head)

    # ---- output ----------------------------------------------------------------------
    def finish(self):
        for fn, args, kwargs in self.ops:
            fn(self, *args, **kwargs)
        self.ops = []
        out = self.img.resize((self.C, self.C), Image.LANCZOS)
        if self.logo_box:
            logo = Image.open(ROOT / self.theme.data["logo"]).convert("RGBA")
            x0, y0, x1, y1 = (v * self.k for v in self.logo_box)
            logo = logo.resize((int(round(x1 - x0)), int(round(y1 - y0))), Image.LANCZOS)
            out.paste(logo, (int(round(x0)), int(round(y0))), logo)
        return out


# ----------------------------------------------------------------------------- layout math

def support(n, w, h):
    """Distance from a w x h box centre to its edge along unit direction n."""
    return 0.5 * (abs(n[0]) * w + abs(n[1]) * h)


def edge_callout(p0, p1, label_wh, offset, pad, trim, away_from=None, toward=None):
    """Arrow parallel to the edge p0 -> p1 (design px), offset outwards, plus its label box."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = unit(p1 - p0)
    n = np.array([-d[1], d[0]])
    if toward is not None:
        if np.dot(n, toward) < 0:
            n = -n
    elif away_from is not None and np.dot(n, 0.5 * (p0 + p1) - away_from) < 0:
        n = -n
    a0, a1 = p0 + n * offset + d * trim, p1 + n * offset - d * trim
    w, h = label_wh
    c = 0.5 * (a0 + a1) + n * (pad + support(n, w, h))
    return {"p0": a0, "p1": a1, "center": c, "w": w, "h": h}


def callout_boxes(callouts):
    out = []
    for c in callouts:
        out.append((min(c["p0"][0], c["p1"][0]), min(c["p0"][1], c["p1"][1]),
                    max(c["p0"][0], c["p1"][0]), max(c["p0"][1], c["p1"][1])))
        out.append((c["center"][0] - c["w"] / 2, c["center"][1] - c["h"] / 2,
                    c["center"][0] + c["w"] / 2, c["center"][1] + c["h"] / 2))
    return out


def fit_photo(size, region, extras=None, align="center"):
    """Largest scale s (design px per photo px) and offset o so the photo plus any extra
    boxes from extras(s, o) fit inside region = (x0, y0, x1, y1).
    align: "center", or "bottom-right" to push the photo into that corner."""
    w, h = size
    rx0, ry0, rx1, ry1 = region

    def bounds(s):
        xs, ys = [0, w * s], [0, h * s]
        if extras:
            for bx0, by0, bx1, by1 in extras(s, np.zeros(2)):
                xs += [bx0, bx1]
                ys += [by0, by1]
        return min(xs), min(ys), max(xs), max(ys)

    lo, hi = 1e-4, 1e3
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        bx0, by0, bx1, by1 = bounds(mid)
        if bx1 - bx0 <= rx1 - rx0 and by1 - by0 <= ry1 - ry0:
            lo = mid
        else:
            hi = mid
    s = lo
    bx0, by0, bx1, by1 = bounds(s)
    if align == "bottom-right":
        o = np.array([rx1 - bx1, ry1 - by1])
    else:
        o = np.array([(rx0 + rx1) / 2 - (bx0 + bx1) / 2, (ry0 + ry1) / 2 - (by0 + by1) / 2])
    return s, o


def trim_white(img, threshold=246, margin=4):
    """Crop away a near-white border (for supplier photos)."""
    rgb = np.asarray(img.convert("RGB"))
    mask = rgb.min(axis=2) < threshold
    if not mask.any():
        return img
    ys, xs = np.nonzero(mask)
    x0, y0 = max(xs.min() - margin, 0), max(ys.min() - margin, 0)
    x1, y1 = min(xs.max() + margin + 1, img.width), min(ys.max() + margin + 1, img.height)
    return img.crop((x0, y0, x1, y1))
