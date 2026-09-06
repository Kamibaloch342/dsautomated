"""One-time prep: turn Sample.png (AI composite with box + size text) into a
clean, reusable stack template plus geometry metadata for compositing.

Approach: the stack's fluted layer edges have strong vertical texture energy;
the smooth top face (and the old hero box) do not. Detect the boundary where
flutes begin per column -> that's the top face's front edge. Fit lines, build
the top-face parallelogram, white-out everything above it (removes the old
box), repaint the face with sampled kraft tones (removes old text + shadow),
and redraw the straps.

Outputs: stack_template_clean.png (2000x2000) + stack_template_meta.json
"""
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SRC = "Sample.png"
OUT_IMG = "stack_template_clean.png"
OUT_META = "stack_template_meta.json"
W = 2000


def main():
    im = Image.open(SRC).convert("RGB").resize((W, W), Image.LANCZOS)
    a = np.asarray(im).astype(np.float32)
    lum = a.mean(axis=2)

    # ---- detect flute-texture boundary per column --------------------------
    # vertical gradient energy, box-filtered down each column
    gy = np.abs(np.diff(lum, axis=0))
    k = 25
    kern = np.ones((k, 1)) / k
    from numpy.lib.stride_tricks import sliding_window_view
    sw = sliding_window_view(gy, k, axis=0)          # (H-k, W, k)
    energy = sw.mean(axis=2)                          # rolling mean down cols

    nonwhite = lum < 245
    Y0, Y1 = 950, 1700          # front edge of the top face lives in this band
    boundary = {}
    for x in range(0, W, 4):
        col = energy[:, x]
        for y in range(Y0, min(Y1, len(col) - 320)):
            # flutes are RELENTLESS horizontal bands: demand continuously high
            # energy over a DEEP window, so printed text (followed by a smooth
            # strip of face) can never fake it
            w = col[y:y + 260]
            if col[y] > 9.0 and np.percentile(w, 25) > 5.0 \
               and w.mean() > 7.0 and nonwhite[y, x]:
                boundary[x] = y
                break
    xs = np.array(sorted(boundary))
    ys = np.array([boundary[x] for x in xs], dtype=float)
    print(f"boundary points: {len(xs)}, x range {xs.min()}-{xs.max()}")

    # median smoothing to kill stragglers
    ys_s = ys.copy()
    for i in range(len(ys)):
        ys_s[i] = np.median(ys[max(0, i - 4):i + 5])

    def fit_line(xa, ya):
        m = np.polyfit(xa, ya, 1)
        for _ in range(3):                     # outlier rejection passes
            res = np.abs(np.polyval(m, xa) - ya)
            keep = res < max(20, np.percentile(res, 80))
            m = np.polyfit(xa[keep], ya[keep], 1)
        return m

    fi = int(np.argmax(ys_s))
    Fx0 = int(np.clip(xs[fi], 0.30 * W, 0.70 * W))
    ml = fit_line(xs[xs <= Fx0], ys_s[xs <= Fx0])
    mr = fit_line(xs[xs >= Fx0], ys_s[xs >= Fx0])
    Fx = int((mr[1] - ml[1]) / (ml[0] - mr[0]))
    Fy = int(np.polyval(ml, Fx))
    Lx, Rx = int(xs[0]), int(xs[-1])
    Ly = int(np.polyval(ml, Lx))
    Ry = int(np.polyval(mr, Rx))
    Bx, By = Lx + Rx - Fx, Ly + Ry - Fy
    print(f"L({Lx},{Ly}) F({Fx},{Fy}) R({Rx},{Ry}) B({Bx},{By})")

    # ---- sample kraft tone near the front edge (clean strip) --------------
    samples = []
    for x, y in boundary.items():
        yy = max(0, y - 14)
        if nonwhite[yy, x]:
            samples.append(a[yy, x])
    front_tone = np.median(np.array(samples), axis=0)
    back_tone = front_tone * 0.90
    print("front tone", front_tone.astype(int))

    # ---- straps: fixed positions measured from the template photo --------
    straps = [(398, 446), (1440, 1498)]
    print("straps at:", straps)

    # ---- 1. white-out everything above the back edges (kills old box) -----
    d = ImageDraw.Draw(im)
    d.polygon([(0, 0), (W, 0), (W, Ry), (Rx, Ry), (Bx, By), (Lx, Ly),
               (0, Ly)], fill=(255, 255, 255))

    # ---- 2. repaint the top face --------------------------------------
    a = np.asarray(im).astype(np.float32)
    face = Image.new("L", (W, W), 0)
    ImageDraw.Draw(face).polygon([(Lx, Ly), (Bx, By), (Rx, Ry), (Fx, Fy)],
                                 fill=255)
    fm = np.asarray(face) > 0
    yy, xx = np.nonzero(fm)
    t = (yy - By) / max(1, Fy - By)               # 0 = back, 1 = front
    rng = np.random.default_rng(7)
    tone = back_tone + (front_tone - back_tone) * t[:, None]
    a[yy, xx] = (tone + rng.normal(0, 2.0, (len(yy), 1))).clip(0, 255)
    # low-frequency mottling so the repaint doesn't look synthetic
    noise = rng.normal(0, 14, (W // 16, W // 16)).astype(np.float32)
    noise = np.asarray(Image.fromarray(noise, mode="F").resize((W, W),
                       Image.BICUBIC)).astype(np.float32)
    a[yy, xx] = (a[yy, xx] + noise[yy, xx, None] * 0.35).clip(0, 255)
    # subtle front-edge seam
    seam = Image.new("L", (W, W), 0)
    ds = ImageDraw.Draw(seam)
    ds.line([(Lx, Ly), (Fx, Fy)], fill=255, width=5)
    ds.line([(Fx, Fy), (Rx, Ry)], fill=255, width=5)
    sm = np.asarray(seam) > 0
    a[sm] *= 0.85

    # ---- 3. remove the printed size text: ink darker than local kraft -----
    def front_y(x):
        return np.polyval(ml if x <= Fx else mr, x)
    tx0, tx1 = int(0.14 * W), int(0.68 * W)
    for x in range(tx0, tx1):
        fy = int(front_y(x)) - 12
        band = a[fy:fy + 200, x]
        if band.size == 0:
            continue
        bl = band.mean(axis=1)
        med = np.median(bl)
        ink = bl < med - 22
        if ink.any():
            good = np.nonzero(~ink)[0]
            if len(good) > 4:
                for ch in range(3):
                    band[:, ch] = np.where(
                        ink, np.interp(np.arange(len(bl)), good,
                                       band[good, ch]), band[:, ch])
            a[fy:fy + 200, x] = band

    # ---- 4. redraw straps across the repainted face (slight lean) --------
    stimg = Image.new("L", (W, W), 0)
    dst = ImageDraw.Draw(stimg)
    for x0, x1 in straps:
        yb0, yb1 = front_y(x0) + 6, front_y(x1) + 6
        dst.polygon([(x0, yb0), (x1, yb1),
                     (x1 + 14, yb1 - 500), (x0 + 14, yb0 - 500)], fill=255)
    stm = (np.asarray(stimg) > 0) & fm
    a[stm] = (np.array([40.0, 36.0, 34.0])
              + rng.normal(0, 3, (int(stm.sum()), 1))).clip(0, 255)

    out = Image.fromarray(a.clip(0, 255).astype(np.uint8))
    blur = out.filter(ImageFilter.GaussianBlur(1.0))
    mask = Image.fromarray((fm * 255).astype(np.uint8)) \
        .filter(ImageFilter.GaussianBlur(2))
    out = Image.composite(blur, out, mask)
    out.save(OUT_IMG)
    meta = dict(L=[Lx, Ly], F=[Fx, Fy], R=[Rx, Ry], B=[Bx, By],
                straps=straps, size=W)
    with open(OUT_META, "w") as fh:
        json.dump(meta, fh)
    print("saved", OUT_IMG, "and", OUT_META)


if __name__ == "__main__":
    main()
