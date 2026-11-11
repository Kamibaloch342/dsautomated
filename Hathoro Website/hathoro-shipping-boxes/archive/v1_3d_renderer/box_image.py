#!/usr/bin/env python3
"""Render a square product image of an open corrugated shipping box with dimension callouts.

The box is modelled in 3D (walls, flaps and corrugated edges all have real thickness),
projected through a perspective camera and rasterised with a z-buffer, so every arrow and
label is placed from the exact projected box edges. Everything that changes between
listings (sizes, title, tagline, labels, colours, flap angles) comes from config.json,
command-line flags or a CSV for batch runs.

    python box_image.py                          # uses config.json
    python box_image.py --size 12x9x3 --out output/12x9x3.png
    python box_image.py --batch sizes.csv        # one image per CSV row
"""

import argparse
import copy
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
DESIGN = 1500.0  # all pixel sizes in the config are given for a 1500 px canvas


# ----------------------------------------------------------------------------- helpers

def hex_rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def fmt_num(v):
    """12.0 -> '12', 12.5 -> '12.5'."""
    return f"{float(v):g}"


def unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


def text_fields(cfg):
    L, W, H = (cfg["box"][k] for k in ("length", "width", "height"))
    return {"L": fmt_num(L), "W": fmt_num(W), "H": fmt_num(H), "u": cfg["box"]["unit"]}


# ----------------------------------------------------------------------------- geometry

@dataclass
class Face:
    origin: np.ndarray
    e1: np.ndarray          # local axis "a" (0..1); flutes run across this axis
    e2: np.ndarray          # local axis "b" (0..1)
    normal: np.ndarray      # outward unit normal
    role: str               # shading recipe, see shade_face()
    poly: np.ndarray = None  # optional convex polygon (world); defaults to the e1/e2 rectangle

    def corners(self):
        if self.poly is not None:
            return self.poly
        o = self.origin
        return np.array([o, o + self.e1, o + self.e1 + self.e2, o + self.e2])


def rect(origin, e1, e2, normal, role):
    return Face(np.asarray(origin, float), np.asarray(e1, float), np.asarray(e2, float),
                unit(normal), role)


def build_box(L, W, H, t, flap_angles, flap_depth, bend_steps=10):
    """Faces of an open RSC box. x = length, y = up, z = width (towards the camera)."""
    X0, X1, Z0, Z1 = -L / 2, L / 2, -W / 2, W / 2
    up = np.array([0.0, 1.0, 0.0])
    faces = []

    # walls: long walls (front/back) run the full length, short walls sit between them
    faces += [
        rect((X0, 0, Z1), (L, 0, 0), (0, H, 0), (0, 0, 1), "wall_outer"),
        rect((X0, 0, Z0), (L, 0, 0), (0, H, 0), (0, 0, -1), "wall_outer"),
        rect((X0, 0, Z0 + t), (0, 0, W - 2 * t), (0, H, 0), (-1, 0, 0), "wall_outer"),
        rect((X1, 0, Z0 + t), (0, 0, W - 2 * t), (0, H, 0), (1, 0, 0), "wall_outer"),
        # corner strips (ends of the long walls)
        rect((X0, 0, Z1 - t), (0, 0, t), (0, H, 0), (-1, 0, 0), "wall_outer"),
        rect((X1, 0, Z1 - t), (0, 0, t), (0, H, 0), (1, 0, 0), "wall_outer"),
        rect((X0, 0, Z0), (0, 0, t), (0, H, 0), (-1, 0, 0), "wall_outer"),
        rect((X1, 0, Z0), (0, 0, t), (0, H, 0), (1, 0, 0), "wall_outer"),
        # inside
        rect((X0 + t, t, Z1 - t), (L - 2 * t, 0, 0), (0, H - t, 0), (0, 0, -1), "wall_inner"),
        rect((X0 + t, t, Z0 + t), (L - 2 * t, 0, 0), (0, H - t, 0), (0, 0, 1), "wall_inner"),
        rect((X0 + t, t, Z0 + t), (0, 0, W - 2 * t), (0, H - t, 0), (1, 0, 0), "wall_inner"),
        rect((X1 - t, t, Z0 + t), (0, 0, W - 2 * t), (0, H - t, 0), (-1, 0, 0), "wall_inner"),
        rect((X0 + t, t, Z0 + t), (L - 2 * t, 0, 0), (0, 0, W - 2 * t), (0, 1, 0), "floor"),
    ]

    flaps = {
        # name: hinge point (outer top edge), hinge direction, hinge length, outward wall normal
        "front": ((X0, H, Z1), (1, 0, 0), L, (0, 0, 1)),
        "back": ((X0, H, Z0), (1, 0, 0), L, (0, 0, -1)),
        "left": ((X0, H, Z0 + t), (0, 0, 1), W - 2 * t, (-1, 0, 0)),
        "right": ((X1, H, Z0 + t), (0, 0, 1), W - 2 * t, (1, 0, 0)),
    }
    flap_quads = []
    for name, (h, a, length, nw) in flaps.items():
        h, a, nw = (np.asarray(v, float) for v in (h, a, nw))
        th = math.radians(flap_angles[name])
        u = up * math.cos(th) + nw * math.sin(th)        # hinge -> free edge
        w = -nw * math.cos(th) + up * math.sin(th)       # outer liner -> inner liner
        F, e_len = flap_depth, a * length
        faces += [
            Face(h, e_len, u * F, -w, "flap_outer"),
            Face(h + w * t, e_len, u * F, w, "flap_inner"),
            Face(h + u * F, e_len, w * t, u, "flute"),                 # free edge
            Face(h, u * F, w * t, -a, "flute"),                       # side edges
            Face(h + e_len, u * F, w * t, a, "flute"),
        ]
        flap_quads.append(np.array([h, h + e_len, h + e_len + u * F, h + u * F]))

        # rounded crease between the wall's inner liner and the flap's inner liner
        arc = [h + t * (-nw * math.cos(al) + up * math.sin(al))
               for al in np.linspace(0.0, th, bend_steps + 1)]
        for k in range(bend_steps):
            mid = (k + 0.5) / bend_steps * th
            n = -nw * math.cos(mid) + up * math.sin(mid)
            faces.append(Face(arc[k], e_len, arc[k + 1] - arc[k], unit(n), "bend"))
        if th > 1e-3:
            cap = np.array([h] + arc)
            faces.append(Face(h, e_len, arc[-1] - h, -a, "cap", poly=cap))
            faces.append(Face(h + e_len, e_len, arc[-1] - h, a, "cap", poly=cap + e_len))

    base = np.array([(X0, 0, Z0), (X1, 0, Z0), (X1, 0, Z1), (X0, 0, Z1)], float)
    return faces, flap_quads, base


class Camera:
    def __init__(self, yaw_deg, elev_deg, perspective, target, radius):
        yaw, elev = math.radians(yaw_deg), math.radians(elev_deg)
        to_cam = np.array([-math.sin(yaw) * math.cos(elev), math.sin(elev),
                           math.cos(yaw) * math.cos(elev)])
        self.D = radius / max(perspective, 1e-4)
        self.pos = np.asarray(target, float) + to_cam * self.D
        self.f = -to_cam
        self.r = unit(np.cross(self.f, [0.0, 1.0, 0.0]))
        self.u = np.cross(self.r, self.f)
        self.R = np.stack([self.r, self.u, self.f])   # world -> camera rows

    def to_cam(self, p):
        return (np.asarray(p, float) - self.pos) @ self.R.T

    def project(self, p):
        c = self.to_cam(p)
        c = np.atleast_2d(c)
        return np.stack([self.D * c[:, 0] / c[:, 2], -self.D * c[:, 1] / c[:, 2]], axis=1)

    def facing(self, face):
        return np.dot(self.pos - face.origin, face.normal) > 1e-9


# ----------------------------------------------------------------------------- rasteriser

class Raster:
    """Z-buffered rasteriser over a supersampled canvas. proj -> px: px = s * proj + o."""

    def __init__(self, size, cam, s, o, cfg):
        self.N = size
        self.cam, self.s, self.o = cam, s, np.asarray(o, float)
        self.cfg = cfg
        self.z = np.full((size, size), np.inf)
        self.rgb = np.zeros((size, size, 3), np.float32)
        self.cover = np.zeros((size, size), bool)
        light = unit(cfg["lighting"]["direction"])
        self.light = light

    def draw(self, face, thickness):
        cam = self.cam
        P = cam.project(face.corners()) * self.s + self.o
        x0, y0 = np.floor(P.min(0)).astype(int)
        x1, y1 = np.ceil(P.max(0)).astype(int) + 1
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, self.N), min(y1, self.N)
        if x1 <= x0 or y1 <= y0:
            return
        X, Y = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)

        # inside test against a convex polygon (orientation-independent)
        area = 0.0
        for i in range(len(P)):
            (ax, ay), (bx, by) = P[i], P[(i + 1) % len(P)]
            area += ax * by - bx * ay
        sign = 1.0 if area > 0 else -1.0
        inside = np.ones(X.shape, bool)
        for i in range(len(P)):
            (ax, ay), (bx, by) = P[i], P[(i + 1) % len(P)]
            inside &= sign * ((bx - ax) * (Y - ay) - (by - ay) * (X - ax)) >= -1e-9
        if not inside.any():
            return

        # exact ray / plane intersection for every covered pixel
        sx = (X[inside] - self.o[0]) / self.s
        sy = (Y[inside] - self.o[1]) / self.s
        d = np.stack([sx / cam.D, -sy / cam.D, np.ones_like(sx)], axis=1)
        n_c = cam.R @ face.normal
        o_c = cam.to_cam(face.origin)
        tpar = np.dot(n_c, o_c) / (d @ n_c)
        pc = d * tpar[:, None]                    # camera-space hit points
        depth = pc[:, 2]

        ys, xs = np.nonzero(inside)
        ys, xs = ys + y0, xs + x0
        win = depth < self.z[ys, xs] - 1e-9
        if not win.any():
            return
        ys, xs, pc, depth = ys[win], xs[win], pc[win], depth[win]

        # local face coordinates a, b in [0, 1]
        rel = pc - o_c
        e1c, e2c = cam.R @ face.e1, cam.R @ face.e2
        a = rel @ e1c / np.dot(e1c, e1c)
        b = rel @ e2c / np.dot(e2c, e2c)

        self.z[ys, xs] = depth
        self.rgb[ys, xs] = shade_face(face, a, b, thickness, self.light, self.cfg)
        self.cover[ys, xs] = True


def shade_face(face, a, b, t, light, cfg):
    col = cfg["colors"]
    kraft = np.array(hex_rgb(col["kraft"]), np.float32)
    lc = cfg["lighting"]
    lam = np.clip(lc["ambient"] + lc["diffuse"] * float(np.dot(face.normal, light)),
                  lc["min"], lc["max"])
    len1, len2 = np.linalg.norm(face.e1), np.linalg.norm(face.e2)
    role = face.role

    if role in ("flute", "cap"):
        return edge_pattern(face, a, b, t, lam, cfg)

    if role == "wall_outer":
        g = 0.955 + 0.045 * b
    elif role == "flap_outer":
        g = 0.95 + 0.06 * b
    elif role == "flap_inner":
        g = 0.93 + 0.07 * b
    elif role == "wall_inner":
        corner = np.minimum(a, 1 - a) * len1
        g = (0.76 + 0.20 * np.clip(b, 0, 1) ** 0.8) * (1 - 0.14 * np.exp(-corner / 0.5))
    elif role == "floor":
        edge = np.minimum.reduce([a * len1, (1 - a) * len1, b * len2, (1 - b) * len2])
        g = 0.70 + 0.16 * (1 - np.exp(-edge / 1.2))
    else:  # bend
        g = np.full_like(a, 0.97)

    # faint vertical flute ribs + overall shade
    pitch = cfg["material"]["rib_pitch"]
    ribs = 1 + cfg["material"]["rib_strength"] * np.sin(2 * np.pi * a * len1 / pitch)
    f = (lam * g * ribs).astype(np.float32)
    return kraft[None, :] * f[:, None]


def edge_pattern(face, a, b, t, lam, cfg):
    """Cut edge of single-wall corrugated board: two liners with a sine-wave medium."""
    col = cfg["colors"]
    liner = np.array(hex_rgb(col["edge_liner"]), np.float32)
    medium = np.array(hex_rgb(col["edge_medium"]), np.float32)
    cavity = np.array(hex_rgb(col["edge_cavity"]), np.float32)
    if face.role == "cap":
        return np.broadcast_to(cavity * lam, (len(a), 3)).copy()

    s = a * np.linalg.norm(face.e1)            # along the cut, in inches
    y = b * t                                    # across the board, in inches
    lt = 0.13 * t                                # liner thickness
    lam_w = cfg["material"]["flute_pitch"] * t   # flute wavelength
    amp = 0.5 * t - lt - 0.04 * t
    k = 2 * np.pi / lam_w
    yc = 0.5 * t + amp * np.sin(k * s)
    slope = amp * k * np.cos(k * s)
    half = 0.055 * t * np.sqrt(1 + slope ** 2)
    is_liner = (y < lt) | (y > t - lt)
    is_medium = np.abs(y - yc) < half

    out = np.empty((len(a), 3), np.float32)
    out[:] = cavity
    out[is_medium] = medium
    out[is_liner] = liner
    return out * lam


# ----------------------------------------------------------------------------- drawing

def load_font(path, size):
    return ImageFont.truetype(str(HERE / path), max(1, int(round(size))))


def text_size(font, text):
    x0, y0, x1, y1 = font.getbbox(text)
    return x1 - x0, y1 - y0, x0, y0


def draw_text_centered(draw, center, text, font, fill):
    w, h, ox, oy = text_size(font, text)
    draw.text((center[0] - w / 2 - ox, center[1] - h / 2 - oy), text, font=font, fill=fill)


def draw_tracked(draw, xy, text, font, fill, tracking):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking


def dashed_arrow(draw, p0, p1, color, width, dash, gap, head, head_angle):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    v = p1 - p0
    length = np.linalg.norm(v)
    if length < 1:
        return
    d = v / length
    r = width / 2

    def seg(a, b):
        draw.line([tuple(a), tuple(b)], fill=color, width=int(round(width)))
        for q in (a, b):
            draw.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=color)

    if length >= 2.5 * head:
        # dashes, centred so both ends finish on a dash
        period = dash + gap
        n = max(1, int((length + gap) // period))
        used = n * period - gap
        start = (length - used) / 2
        for i in range(n):
            s0 = start + i * period
            seg(p0 + d * s0, p0 + d * min(s0 + dash, length))
        heads = ((p0, d), (p1, -d))
    else:
        # too short for inside arrowheads: drafting style, arrows pointing in from outside
        ext = 1.8 * head
        seg(p0, p1)
        seg(p0 - d * ext, p0)
        seg(p1, p1 + d * ext)
        heads = ((p0, -d), (p1, d))

    ang = math.radians(head_angle)
    for tip, back in heads:
        for sgn in (1, -1):
            c, s_ = math.cos(sgn * ang), math.sin(sgn * ang)
            arm = np.array([back[0] * c - back[1] * s_, back[0] * s_ + back[1] * c])
            seg(tip, tip + arm * head)


def support(n, w, h):
    """Distance from a w*h box centre to its edge along unit direction n."""
    return 0.5 * (abs(n[0]) * w + abs(n[1]) * h)


# ----------------------------------------------------------------------------- layout

def dimension_callouts(cam, L, W, H, s, o, cfg, fonts):
    """Arrow segments and label boxes (final-canvas px) for a given projection scale/offset."""
    lay = cfg["layout"]
    k = cfg["canvas"] / DESIGN
    gap, pad, trim = lay["arrow_gap"] * k, lay["label_pad"] * k, lay["arrow_trim"] * k
    X0, X1, Z0, Z1 = -L / 2, L / 2, -W / 2, W / 2

    def px(p):
        return cam.project(p)[0] * s + o

    centre = px((0, H / 2, 0))
    fields = text_fields(cfg)
    out = []

    def add(name, edge, text, normal=None):
        p0, p1 = px(edge[0]), px(edge[1])
        d = unit(p1 - p0)
        n = np.array([-d[1], d[0]])
        if normal is not None:
            if np.dot(n, normal) < 0:
                n = -n
        elif np.dot(n, 0.5 * (p0 + p1) - centre) < 0:
            n = -n
        a0, a1 = p0 + n * gap + d * trim, p1 + n * gap - d * trim
        w, h, _, _ = text_size(fonts["label"], text)
        c = 0.5 * (a0 + a1) + n * (pad + support(n, w, h))
        out.append({"name": name, "p0": a0, "p1": a1, "text": text, "center": c, "w": w, "h": h})

    labels = cfg["labels"]
    add("width", [(X0, 0, Z0), (X0, 0, Z1)], labels["width"].format(**fields))
    add("length", [(X0, 0, Z1), (X1, 0, Z1)], labels["length"].format(**fields))

    htext = labels["height"].format(**fields)
    style = lay["height_arrow"]
    if style == "corner":
        # inside the front face, next to the nearest corner (like the reference image)
        into_front = px((X0 + 1, 0, Z1)) - px((X0, 0, Z1))
        add("height", [(X0, 0, Z1), (X0, H, Z1)], htext, normal=into_front)
        top, bot = out[-1]["p0"], out[-1]["p1"]
        if np.linalg.norm(top - bot) < out[-1]["h"] * 1.6:   # box too flat: move outside
            out.pop()
            style = "right"
    if style == "right":
        add("height", [(X1, 0, Z1), (X1, H, Z1)], htext)
    return out


def fit_layout(cam, faces, L, W, H, cfg, fonts, region):
    pts = np.concatenate([cam.project(f.corners()) for f in faces])
    rx0, ry0, rx1, ry1 = region

    def bounds(s):
        q = pts * s
        o = np.zeros(2)
        call = dimension_callouts(cam, L, W, H, s, o, cfg, fonts)
        xs = [q[:, 0].min(), q[:, 0].max()]
        ys = [q[:, 1].min(), q[:, 1].max()]
        for c in call:
            xs += [c["p0"][0], c["p1"][0], c["center"][0] - c["w"] / 2, c["center"][0] + c["w"] / 2]
            ys += [c["p0"][1], c["p1"][1], c["center"][1] - c["h"] / 2, c["center"][1] + c["h"] / 2]
        return min(xs), min(ys), max(xs), max(ys)

    lo, hi = 1e-3, 1e5
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        bx0, by0, bx1, by1 = bounds(mid)
        if bx1 - bx0 <= rx1 - rx0 and by1 - by0 <= ry1 - ry0:
            lo = mid
        else:
            hi = mid
    s = lo
    bx0, by0, bx1, by1 = bounds(s)
    o = np.array([(rx0 + rx1) / 2 - (bx0 + bx1) / 2, (ry0 + ry1) / 2 - (by0 + by1) / 2])
    return s, o


# ----------------------------------------------------------------------------- main render

def render(cfg, blank=False):
    """Return (image, layout). blank=True draws only the box; layout still holds every
    text/arrow position (final-canvas px) so other tools can draw on the blank image."""
    C = int(cfg["canvas"])
    SS = int(cfg["supersample"])
    k = C / DESIGN
    lay, col = cfg["layout"], cfg["colors"]
    navy = hex_rgb(col["navy"])

    shape = cfg.get("shape") or cfg["box"]
    L, W, H = (float(shape[n]) for n in ("length", "width", "height"))
    t = float(cfg["material"]["thickness"])
    t = min(max(t, 0.006 * max(L, W, H)), 0.025 * max(L, W, H))
    flap_depth = cfg["flaps"].get("depth") or W / 2

    faces, flap_quads, base = build_box(L, W, H, t, cfg["flaps"]["angles"], flap_depth)
    cam_cfg = cfg["camera"]
    radius = 0.5 * math.sqrt(L * L + W * W + (H + flap_depth) ** 2)
    cam = Camera(cam_cfg["yaw"], cam_cfg["elevation"], cam_cfg["perspective"],
                 (0, H / 2, 0), radius)

    fonts = {
        "label": load_font(cfg["fonts"]["label"], lay["label_size"] * k),
        "title": load_font(cfg["fonts"]["title"], lay["title_size"] * k),
        "badge": load_font(cfg["fonts"]["badge"], lay["badge_size"] * k),
        "tagline": load_font(cfg["fonts"]["tagline"], lay["tagline_size"] * k),
    }
    fields = text_fields(cfg)

    # ---------------------------------------------------------------- header (final px)
    mx = lay["margin_x"] * k
    header_bottom = lay["margin_top"] * k
    header_items = []
    logo_img = None
    if cfg.get("logo"):
        logo_img = Image.open(HERE / cfg["logo"]).convert("RGBA")
        lw = lay["logo_width"] * k
        logo_img = logo_img.resize((int(lw), int(lw * logo_img.height / logo_img.width)),
                                   Image.LANCZOS)
    logo_left = C - mx - (logo_img.width if logo_img else 0) - 30 * k

    y = lay["margin_top"] * k
    title = cfg["title"].format(**fields)
    if title:
        f = fonts["title"]
        _, h, _, oy = text_size(f, title)
        header_items.append(("title", (mx, y - oy), title))
        y += h + lay["title_gap"] * k
    badge = cfg["badge"].format(**fields)
    if badge:
        f = fonts["badge"]
        bh = lay["badge_height"] * k
        padx = lay["badge_pad_x"] * k
        avail = logo_left - mx - 2 * padx
        while text_size(f, badge)[0] > avail and f.size > 10:
            f = ImageFont.truetype(f.path, f.size - 2)
        fonts["badge"] = f
        bw = text_size(f, badge)[0] + 2 * padx
        header_items.append(("badge", (mx, y, mx + bw, y + bh), badge))
        y += bh + lay["badge_gap"] * k
    tagline = cfg["tagline"].format(**fields)
    if tagline:
        f = fonts["tagline"]
        lines = tagline.split("\n")
        while max(f.getlength(ln) for ln in lines) > C - 2 * mx and f.size > 10:
            f = ImageFont.truetype(f.path, f.size - 2)
        fonts["tagline"] = f
        lh = f.size * lay["tagline_line_height"]
        header_items.append(("tagline", (mx, y), lines, lh))
        y += lh * len(lines)
    header_bottom = max(header_bottom, y)

    region = (mx, header_bottom + lay["box_top_gap"] * k, C - mx, C - lay["margin_bottom"] * k)
    s, o = fit_layout(cam, faces, L, W, H, cfg, fonts, region)
    callouts = dimension_callouts(cam, L, W, H, s, o, cfg, fonts)

    # ---------------------------------------------------------------- box (supersampled)
    N = C * SS
    ras = Raster(N, cam, s * SS, o * SS, cfg)
    for f in faces:
        if cam.facing(f):
            ras.draw(f, t)

    rng = np.random.default_rng(7)
    noise = rng.standard_normal((N // 2, N // 2)).astype(np.float32)
    noise_img = Image.fromarray(((noise * 40) + 128).clip(0, 255).astype(np.uint8))
    noise_img = noise_img.filter(ImageFilter.GaussianBlur(0.8)).resize((N, N), Image.BILINEAR)
    noise = (np.asarray(noise_img, np.float32) - 128) / 40
    ras.rgb *= (1 + cfg["material"]["grain"] * noise)[..., None]

    # ---------------------------------------------------------------- ground shadow
    bg = np.array(hex_rgb(cfg["background"]), np.float32)
    sh_res = N // 4
    sh = Image.new("L", (sh_res, sh_res), 0)
    sd = ImageDraw.Draw(sh)
    ls = unit(cfg["lighting"]["shadow_direction"])

    def ground(p):
        p = np.asarray(p, float)
        return p - ls[None, :] * (p[:, 1:2] / ls[1])

    def shadow_poly(poly, value):
        q = cam.project(poly) * (s * SS / 4) + o * (SS / 4)
        sd.polygon([tuple(v) for v in q], fill=value)

    sh_soft_val = int(255 * cfg["shadow"]["soft_opacity"])
    shadow_poly(ground(base), sh_soft_val)
    for q in flap_quads:
        shadow_poly(ground(q), sh_soft_val)
    soft = sh.filter(ImageFilter.GaussianBlur(cfg["shadow"]["soft_blur"] * k * SS / 4))
    sh2 = Image.new("L", (sh_res, sh_res), 0)
    sd = ImageDraw.Draw(sh2)
    grow = cfg["shadow"]["contact_grow"]
    contact = base + np.array([[-grow, 0, -grow], [grow, 0, -grow], [grow, 0, grow], [-grow, 0, grow]])
    shadow_poly(contact, int(255 * cfg["shadow"]["contact_opacity"]))
    contact_img = sh2.filter(ImageFilter.GaussianBlur(cfg["shadow"]["contact_blur"] * k * SS / 4))
    shadow = np.maximum(np.asarray(soft.resize((N, N), Image.BILINEAR), np.float32),
                        np.asarray(contact_img.resize((N, N), Image.BILINEAR), np.float32)) / 255
    canvas = bg[None, None, :] * (1 - shadow[..., None] * 0.55)
    canvas[ras.cover] = ras.rgb[ras.cover]
    img = Image.fromarray(canvas.clip(0, 255).astype(np.uint8), "RGB")
    del ras, canvas, shadow, noise

    logo_xy = (int(C - mx - logo_img.width), int(lay["logo_top"] * k)) if logo_img else None
    layout = {
        "canvas": C,
        "box": {"length": L, "width": W, "height": H},
        "callouts": [{"name": c["name"], "text": c["text"],
                      "arrow": [c["p0"].round(1).tolist(), c["p1"].round(1).tolist()],
                      "label_center": c["center"].round(1).tolist(),
                      "label_size": [c["w"], c["h"]]} for c in callouts],
        "header": [{"kind": it[0], "text": it[2],
                    ("rect" if it[0] == "badge" else "top_left"): [round(v, 1) for v in it[1]]}
                   for it in header_items],
        "logo": {"rect": [*logo_xy, logo_xy[0] + logo_img.width, logo_xy[1] + logo_img.height]}
        if logo_img else None,
    }
    if blank:
        return img.resize((C, C), Image.LANCZOS), layout

    # ---------------------------------------------------------------- vector overlay (supersampled)
    draw = ImageDraw.Draw(img)
    for c in callouts:
        dashed_arrow(draw, c["p0"] * SS, c["p1"] * SS, navy, lay["arrow_width"] * k * SS,
                     lay["dash"] * k * SS, lay["dash_gap"] * k * SS,
                     lay["arrow_head"] * k * SS, lay["arrow_head_angle"])
    label_font = load_font(cfg["fonts"]["label"], lay["label_size"] * k * SS)
    for c in callouts:
        draw_text_centered(draw, c["center"] * SS, c["text"], label_font, navy)

    for item in header_items:
        kind = item[0]
        if kind == "title":
            f = load_font(cfg["fonts"]["title"], lay["title_size"] * k * SS)
            x, y0 = item[1]
            draw_tracked(draw, (x * SS, y0 * SS), item[2], f, hex_rgb(col["title"]),
                         lay["title_tracking"] * k * SS)
        elif kind == "badge":
            x0, y0, x1, y1 = (v * SS for v in item[1])
            draw.rounded_rectangle([x0, y0, x1, y1], radius=lay["badge_radius"] * k * SS,
                                   fill=hex_rgb(col["badge"]))
            f = ImageFont.truetype(fonts["badge"].path, fonts["badge"].size * SS)
            draw_text_centered(draw, ((x0 + x1) / 2, (y0 + y1) / 2), item[2], f,
                               hex_rgb(col["badge_text"]))
        elif kind == "tagline":
            f = ImageFont.truetype(fonts["tagline"].path, fonts["tagline"].size * SS)
            x, y0 = item[1]
            for i, line in enumerate(item[2]):
                draw.text((x * SS, (y0 + i * item[3]) * SS), line, font=f,
                          fill=hex_rgb(col["tagline"]))

    img = img.resize((C, C), Image.LANCZOS)
    if logo_img is not None:
        img.paste(logo_img, logo_xy, logo_img)
    return img, layout


# ----------------------------------------------------------------------------- CLI

def parse_size(text):
    parts = text.lower().replace("×", "x").split("x")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("size must look like 12x9x3")
    return [float(p) for p in parts]


def apply_row(cfg, row):
    cfg = copy.deepcopy(cfg)
    for key in ("length", "width", "height"):
        if row.get(key):
            cfg["box"][key] = float(row[key])
    for key in ("title", "tagline", "badge"):
        if row.get(key):
            cfg[key] = row[key].replace("\\n", "\n")
    for key in ("length", "width", "height"):
        if row.get(f"{key}_label"):
            cfg["labels"][key] = row[f"{key}_label"]
    return cfg


def default_name(cfg):
    b = cfg["box"]
    return f"box_{fmt_num(b['length'])}x{fmt_num(b['width'])}x{fmt_num(b['height'])}.png"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--size", type=parse_size, help="LxWxH, e.g. 12x9x3")
    ap.add_argument("--title")
    ap.add_argument("--tagline", help="use \\n for a line break")
    ap.add_argument("--badge", help="badge text; default is built from the size")
    ap.add_argument("--out", help="output PNG path")
    ap.add_argument("--batch", help="CSV with columns length,width,height[,title,tagline,badge,filename]")
    ap.add_argument("--blank", action="store_true", help="draw only the box (no text, arrows or logo)")
    ap.add_argument("--layout", action="store_true",
                    help="also write <image>.json with every arrow/label/header position in px")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    if args.size:
        cfg["box"]["length"], cfg["box"]["width"], cfg["box"]["height"] = args.size
    for key in ("title", "tagline", "badge"):
        val = getattr(args, key)
        if val is not None:
            cfg[key] = val.replace("\\n", "\n")

    def save(c, path):
        img, layout = render(c, blank=args.blank)
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        print("saved", path)
        if args.layout:
            path.with_suffix(".json").write_text(json.dumps(layout, indent=2) + "\n")

    out_dir = HERE / "output"
    if args.batch:
        with open(args.batch, newline="") as fh:
            for row in csv.DictReader(fh):
                c = apply_row(cfg, row)
                save(c, out_dir / (row.get("filename") or default_name(c)))
        return
    save(cfg, Path(args.out) if args.out else out_dir / default_name(cfg))


if __name__ == "__main__":
    main()
