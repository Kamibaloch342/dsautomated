"""Line icons drawn with Pillow primitives. Each fits a square of half-size r around (cx, cy)."""

import math


def _line(d, pts, c, w):
    d.line([tuple(p) for p in pts], fill=c, width=int(w), joint="curve")
    rr = w / 2
    for x, y in (pts[0], pts[-1]):
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=c)


def _arrowhead(d, tip, direction, size, c):
    ang = math.atan2(direction[1], direction[0])
    pts = [tip]
    for s in (1, -1):
        a = ang + math.pi + s * math.radians(32)
        pts.append((tip[0] + size * math.cos(a), tip[1] + size * math.sin(a)))
    d.polygon([pts[1], pts[0], pts[2]], fill=c)


def star(d, cx, cy, r, c):
    pts = []
    for i in range(10):
        rad = r if i % 2 == 0 else r * 0.42
        a = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    d.polygon(pts, fill=c)


def crush(d, cx, cy, r, c, accent, w):
    # box under two pressing arrows
    bx0, by0, bx1, by1 = cx - 0.7 * r, cy - 0.05 * r, cx + 0.7 * r, cy + 0.85 * r
    d.rectangle([bx0, by0, bx1, by1], outline=c, width=int(w))
    _line(d, [(cx - 0.7 * r, cy + 0.35 * r), (cx + 0.7 * r, cy + 0.35 * r)], c, w * 0.6)
    for x in (cx - 0.38 * r, cx + 0.38 * r):
        _line(d, [(x, cy - 0.95 * r), (x, cy - 0.42 * r)], accent, w)
        _arrowhead(d, (x, cy - 0.18 * r), (0, 1), 0.32 * r, accent)


def weight(d, cx, cy, r, c, accent, w):
    # classic trapezoid weight with a ring handle
    ring = 0.26 * r
    d.ellipse([cx - ring, cy - 0.95 * r, cx + ring, cy - 0.95 * r + 2 * ring], outline=c,
              width=int(w * 0.9))
    body = [(cx - 0.42 * r, cy - 0.38 * r), (cx + 0.42 * r, cy - 0.38 * r),
            (cx + 0.88 * r, cy + 0.88 * r), (cx - 0.88 * r, cy + 0.88 * r)]
    d.polygon(body, fill=c)
    bar = 0.07 * r
    d.rectangle([cx - 0.36 * r, cy + 0.22 * r - bar, cx + 0.36 * r, cy + 0.22 * r + bar],
                fill=accent)


def ruler(d, cx, cy, r, c, accent, w):
    x0, x1 = cx - 0.95 * r, cx + 0.95 * r
    y0, y1 = cy + 0.05 * r, cy + 0.55 * r
    d.rounded_rectangle([x0, y0, x1, y1], radius=0.08 * r, outline=c, width=int(w))
    n = 9
    for i in range(1, n):
        x = x0 + (x1 - x0) * i / n
        tick = 0.24 * r if i % 2 == 0 else 0.14 * r
        _line(d, [(x, y0), (x, y0 + tick)], c, w * 0.55)
    # span arrow above
    ya = cy - 0.45 * r
    _line(d, [(x0 + 0.25 * r, ya), (x1 - 0.25 * r, ya)], accent, w * 0.8)
    _arrowhead(d, (x0 + 0.02 * r, ya), (-1, 0), 0.3 * r, accent)
    _arrowhead(d, (x1 - 0.02 * r, ya), (1, 0), 0.3 * r, accent)


def recycle(d, cx, cy, r, c, accent, w):
    R = 0.72 * r
    for k in range(3):
        start = -90 + k * 120 + 18
        end = start + 84
        d.arc([cx - R, cy - R, cx + R, cy + R], start, end, fill=c, width=int(w * 1.15))
        a = math.radians(end + 4)
        tip = (cx + R * math.cos(a + 0.18), cy + R * math.sin(a + 0.18))
        tangent = (-math.sin(a), math.cos(a))
        _arrowhead(d, tip, tangent, 0.42 * r, c)
    d.ellipse([cx - 0.14 * r, cy - 0.14 * r, cx + 0.14 * r, cy + 0.14 * r], fill=accent)


def box(d, cx, cy, r, c, accent, w):
    # isometric carton with tape
    t = (cx, cy - 0.85 * r)
    l, rt = (cx - 0.8 * r, cy - 0.42 * r), (cx + 0.8 * r, cy - 0.42 * r)
    m = (cx, cy + 0.02 * r)
    bl, br, b = (cx - 0.8 * r, cy + 0.48 * r), (cx + 0.8 * r, cy + 0.48 * r), (cx, cy + 0.92 * r)
    _line(d, [t, rt, br, b, bl, l, t], c, w)
    _line(d, [l, m, rt], c, w)
    _line(d, [m, b], c, w)
    _line(d, [((t[0] + l[0]) / 2 + 0.05 * r, (t[1] + l[1]) / 2 - 0.02 * r),
              ((rt[0] + m[0]) / 2 - 0.05 * r, (rt[1] + m[1]) / 2 + 0.02 * r)], accent, w * 0.9)


def flat(d, cx, cy, r, c, accent, w):
    # three flat sheets
    for i, col in enumerate((c, c, accent)):
        y = cy + 0.55 * r - i * 0.42 * r
        pts = [(cx - 0.95 * r, y), (cx + 0.35 * r, y), (cx + 0.95 * r, y - 0.32 * r),
               (cx - 0.35 * r, y - 0.32 * r)]
        d.polygon(pts, outline=col, width=int(w * 0.8))


def flag(d, cx, cy, r, c, accent, w):
    x0, y0, x1, y1 = cx - 0.95 * r, cy - 0.62 * r, cx + 0.95 * r, cy + 0.62 * r
    d.rounded_rectangle([x0, y0, x1, y1], radius=0.08 * r, outline=c, width=int(w * 0.8))
    for i in range(1, 6):
        y = y0 + (y1 - y0) * i / 6
        xs = x0 + (0.85 * r if y < cy else 0)
        _line(d, [(xs + 0.06 * r, y), (x1 - 0.06 * r, y)], c, w * 0.5)
    cxn, cyn = x0 + 0.42 * r, y0 + 0.30 * r
    d.rectangle([x0 + 0.05 * r, y0 + 0.05 * r, x0 + 0.82 * r, cy], fill=accent)
    star(d, cxn, cyn + 0.03 * r, 0.26 * r, c)


def shield(d, cx, cy, r, c, accent, w):
    pts = [(cx, cy - 0.95 * r), (cx + 0.78 * r, cy - 0.62 * r), (cx + 0.70 * r, cy + 0.15 * r),
           (cx, cy + 0.95 * r), (cx - 0.70 * r, cy + 0.15 * r), (cx - 0.78 * r, cy - 0.62 * r)]
    _line(d, pts + [pts[0]], c, w)
    _line(d, [(cx - 0.36 * r, cy + 0.0 * r), (cx - 0.08 * r, cy + 0.30 * r),
              (cx + 0.40 * r, cy - 0.30 * r)], accent, w * 1.1)


def truck(d, cx, cy, r, c, accent, w):
    d.rounded_rectangle([cx - 0.95 * r, cy - 0.55 * r, cx + 0.25 * r, cy + 0.38 * r],
                        radius=0.06 * r, outline=c, width=int(w))
    _line(d, [(cx + 0.25 * r, cy - 0.25 * r), (cx + 0.65 * r, cy - 0.25 * r),
              (cx + 0.95 * r, cy + 0.05 * r), (cx + 0.95 * r, cy + 0.38 * r),
              (cx + 0.25 * r, cy + 0.38 * r)], c, w)
    for x in (cx - 0.55 * r, cx + 0.55 * r):
        d.ellipse([x - 0.2 * r, cy + 0.38 * r, x + 0.2 * r, cy + 0.78 * r], fill=accent)
    for i, y in enumerate((cy - 0.25 * r, cy + 0.02 * r)):
        _line(d, [(cx - 1.35 * r + i * 0.15 * r, y), (cx - 1.08 * r, y)], c, w * 0.6)


def check(d, cx, cy, r, c, accent, w):
    _line(d, [(cx - 0.6 * r, cy), (cx - 0.15 * r, cy + 0.45 * r), (cx + 0.65 * r, cy - 0.45 * r)],
          c, w * 1.3)


ICONS = {
    "crush": crush, "weight": weight, "ruler": ruler, "recycle": recycle, "box": box,
    "flat": flat, "flag": flag, "shield": shield, "truck": truck, "check": check,
}


def draw(d, name, cx, cy, r, color, accent):
    ICONS[name](d, cx, cy, r, color, accent, max(2.0, r * 0.13))
