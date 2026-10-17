#!/usr/bin/env python3
"""Rebuild the chrome reference photo, keymap and screen rect from a raw shot.

    venv/bin/python tools/rebuild_keymap.py IMG_1234.png

Why this exists: a photo of the panel is never square-on, so hotspots traced
from it are perspective-distorted and the LCD is a rotated quad that no
axis-aligned framebuffer can fill. This rectifies the photo first, then measures
everything off the rectified image, where photo pixels are metric.

The method, in order (see docs/design/reference/rectifying-the-photo.md):

1. Find the LCD bezel aperture by subpixel gradient edges, not colour
   thresholds. The recess is shadowed and a colour mask wanders tens of pixels
   along the shaded side; the cream-to-dark step does not.
2. Fit the rectifying transform from those four corners against a known 4:3.
   It comes out affine - the vanishing points are past 600k px, so the shot is
   effectively orthographic - which matters because an affine extrapolates
   safely across the whole frame where a projective fit amplifies corner error.
3. Warp onto a 16:9 canvas sized so the LCD is a whole multiple of 320x240,
   padding the short sides with mirrored grass cross-faded into a 180-flipped
   copy (a mirror is seamless but symmetric; the flip is asymmetric but creases).
4. Take px/mm from the key pitch, not the faceplate: 13 intervals of the note
   row beat two hand-placed edges in side lighting. Cross-check against the
   faceplate width.
5. Detect caps as cream blobs on the dark green plate, name them from the
   previous keymap, then regularise onto a least-squares uniform pitch - which
   also recovers any cap the detector lost.
6. Fit the encoder recess rings by scoring a dark groove against the cream just
   inside and outside it. Raw darkness gets dragged off by cast shadows.
"""
import argparse
import json
import math
import os
import sys
from collections import deque

import numpy as np
import pygame

HERE = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.dirname(HERE)
REPO = os.path.dirname(EMU)
sys.path.insert(0, EMU)

CHROME = os.path.join(EMU, "chrome")
PHOTO_OUT = os.path.join(REPO, "docs", "design", "reference",
                         "lookalike-model.jpg")
CANVAS = (5120, 2880)            # 16:9, and 8 x the 640 px LCD
LCD_W, LCD_H = 640, 480
GRID_MM = 16.0                   # key pitch, design intent
CAP_MM = 15.0
RADIUS_MM = 3.5                  # keycap and bezel corner radius
ENC_MM = 10.0                    # encoder recess ring radius
FEATHER = 300.0                  # px of mirror-to-flip cross-fade in the pad
# The shell's outer edge, corner radius and brown rim on the rectified photo, measured
# by hand 2026-10-02 (#278: 242.8 x 130.7 mm, 11.2 mm corners, a ~2.2 mm rim), not
# fitted here; carried into screen_rect.json for the app's drawing (#425).
SHELL = [1207, 711, 2707, 1457]  # x, y, w, h
SHELL_RADIUS = 125
SHELL_RIM = 24


# --- 1. subpixel edges -----------------------------------------------------
def _subpix(v, sign):
    g = np.diff(v) * sign
    i = int(np.argmax(g))
    if i <= 0 or i >= len(g) - 1:
        return None, 0.0
    y0, y1, y2 = g[i - 1], g[i], g[i + 1]
    d = y0 - 2 * y1 + y2
    return i + 0.5 + (0.0 if d == 0 else 0.5 * (y0 - y2) / d), float(y1)


def fit_edge(lum, axis, ts, lo, hi, sign, name=""):
    """Robust line through the strongest luminance step on each scanline."""
    pts = []
    for t in ts:
        v = lum[t, lo:hi] if axis == "x" else lum[lo:hi, t]
        p, s = _subpix(v, sign)
        if p is not None and s > 6:
            pts.append((t, lo + p))
    p = np.array(pts, float)
    if len(p) < 20:
        raise SystemExit("edge %s: only %d samples - check the search window"
                         % (name, len(p)))
    for _ in range(8):
        m, b = np.polyfit(p[:, 0], p[:, 1], 1)
        r = p[:, 1] - (m * p[:, 0] + b)
        mad = np.median(np.abs(r - np.median(r))) * 1.4826
        keep = np.abs(r) < max(3 * mad, 0.5)
        if keep.all() or keep.sum() < 20:
            break
        p = p[keep]
    print("  %-8s slope %+.5f  pos %9.2f  kept %d/%d" %
          (name, m, b, len(p), len(pts)))
    return m, b


def _vline(m, b):
    return np.array([1.0, -m, -b])


def _hline(m, b):
    return np.array([-m, 1.0, -b])


def _cross(l1, l2):
    p = np.cross(l1, l2)
    return (p[0] / p[2], p[1] / p[2])


def find_lcd(lum, box):
    """Aperture corners, given a rough (x0, y0, x1, y1) box around the LCD."""
    x0, y0, x1, y1 = box
    rows = range(int(y0 + 0.1 * (y1 - y0)), int(y1 - 0.1 * (y1 - y0)), 2)
    cols = range(int(x0 + 0.1 * (x1 - x0)), int(x1 - 0.1 * (x1 - x0)), 2)
    pad = int(0.09 * (x1 - x0))
    L = fit_edge(lum, "x", rows, int(x0 - pad), int(x0 + pad), -1, "lcd L")
    R = fit_edge(lum, "x", rows, int(x1 - pad), int(x1 + pad), +1, "lcd R")
    T = fit_edge(lum, "y", cols, int(y0 - pad), int(y0 + pad), -1, "lcd T")
    B = fit_edge(lum, "y", cols, int(y1 - pad), int(y1 + pad), +1, "lcd B")
    lv, rv = _vline(*L), _vline(*R)
    th, bh = _hline(*T), _hline(*B)
    return [_cross(lv, th), _cross(rv, th), _cross(rv, bh), _cross(lv, bh)]


def device_bbox(rgb):
    """Rough outer bounds of the synth, used only to centre the crop."""
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    h, w = R.shape
    dev = (R > 140) & (R > B + 25) & (R >= G - 10)
    m = np.zeros_like(dev)
    m[int(h * 0.2):int(h * 0.82), int(w * 0.1):int(w * 0.92)] = True
    dev &= m
    xs = np.nonzero(dev.sum(0) > h * 0.20)[0]
    ys = np.nonzero(dev.sum(1) > w * 0.35)[0]
    return xs.min(), ys.min(), xs.max(), ys.max()


# --- 2/3. rectify ----------------------------------------------------------
def rectify(img, quad, centre):
    """Affine mapping the LCD quad to an axis-aligned LCD_W x LCD_H, centred."""
    src = np.array(quad, float)
    dst = np.array([(0, 0), (LCD_W, 0), (LCD_W, LCD_H), (0, LCD_H)], float)
    P = np.hstack([src, np.ones((4, 1))])
    M, *_ = np.linalg.lstsq(P, dst, rcond=None)
    res = np.abs(np.hstack([src, np.ones((4, 1))]) @ M - dst).max()
    print("  affine residual on the four corners: %.2f px" % res)
    if res > 3.0:
        print("  WARNING: residual is large - the shot may be too oblique for "
              "an affine fit, or an edge window caught the wrong feature")
    M = M.copy()
    M[2] += np.array(CANVAS, float) / 2 - (np.append(centre, 1.0) @ M)
    return np.vstack([M.T, [0, 0, 1]])


def warp(img, A):
    Ainv = np.linalg.inv(A)
    H, W, _ = img.shape
    out = np.zeros((CANVAS[1], CANVAS[0], 3), np.uint8)

    def bilinear(sx, sy):
        x0 = np.clip(np.floor(sx), 0, W - 2).astype(np.int32)
        fx = (sx - x0)[..., None]
        y0 = np.clip(np.floor(sy), 0, H - 2).astype(np.int32)
        fy = (sy - y0)[..., None]
        return ((img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx) * (1 - fy)
                + (img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx) * fy)

    for ya in range(0, CANVAS[1], 180):
        yb = min(ya + 180, CANVAS[1])
        ys, xs = np.mgrid[ya:yb, 0:CANVAS[0]]
        acc = np.zeros((yb - ya, CANVAS[0], 3), np.float32)
        for dy in (-0.25, 0.25):                  # 2x2 supersample
            for dx in (-0.25, 0.25):
                u, v = xs + dx, ys + dy
                sx = Ainv[0, 0] * u + Ainv[0, 1] * v + Ainv[0, 2]
                sy = Ainv[1, 0] * u + Ainv[1, 1] * v + Ainv[1, 2]
                over = np.maximum(-sx, sx - (W - 1))
                mx = np.where(sx < 0, -sx, np.where(sx > W - 1,
                                                    2 * (W - 1) - sx, sx))
                my = np.where(sy < 0, -sy, np.where(sy > H - 1,
                                                    2 * (H - 1) - sy, sy))
                a = bilinear(mx, my)
                pad = over > 0
                if pad.any():
                    t = np.clip(over / FEATHER, 0, 1)[..., None]
                    b = bilinear(mx, np.clip(H - 1 - my, 0, H - 1))
                    a = np.where(pad[..., None], a * (1 - t) + b * t, a)
                acc += a
        out[ya:yb] = np.clip(acc / 4, 0, 255).astype(np.uint8)
    t = np.abs(np.arange(CANVAS[0]) - CANVAS[0] / 2) / (CANVAS[0] / 2)
    g = 1.0 - 0.35 * np.clip((t - 0.55) / 0.45, 0, 1) ** 2
    return np.clip(out.astype(np.float32) * g[None, :, None], 0, 255).astype(np.uint8)


# --- 4/5. measure the grid -------------------------------------------------
def components(mask, min_px=100, step=4):
    d = mask[::step, ::step]
    h, w = d.shape
    seen = np.zeros_like(d, bool)
    out = []
    for y in range(h):
        for x in range(w):
            if d[y, x] and not seen[y, x]:
                q = deque([(y, x)])
                seen[y, x] = True
                pts = []
                while q:
                    cy, cx = q.popleft()
                    pts.append((cy, cx))
                    for ny, nx in ((cy - 1, cx), (cy + 1, cx),
                                   (cy, cx - 1), (cy, cx + 1)):
                        if 0 <= ny < h and 0 <= nx < w and d[ny, nx] \
                                and not seen[ny, nx]:
                            seen[ny, nx] = True
                            q.append((ny, nx))
                if len(pts) >= min_px:
                    p = np.array(pts) * step
                    out.append((p[:, 1].min(), p[:, 0].min(),
                                p[:, 1].max(), p[:, 0].max()))
    return out


def row_key(c):
    """Caps in one row share a top and a bottom; 18 px of slack absorbs noise."""
    return (round(c[1] / 18), round(c[3] / 18))


def split_runs(xs):
    """Indices grouped into runs, breaking where a gap exceeds 1.6x the median.

    A single top/bottom band can hold two separate blocks (the REC/LIFT pair and
    the icon row sit at the same height either side of the LCD), and they must
    not be fitted as one grid.
    """
    if len(xs) < 3:
        return [list(range(len(xs)))]
    gaps = np.diff(xs)
    med = np.median(gaps)
    runs, cur = [], [0]
    for i, g in enumerate(gaps):
        if g > 1.6 * med:
            runs.append(cur)
            cur = [i + 1]
        else:
            cur.append(i + 1)
    runs.append(cur)
    return runs


def key_pitch(comps):
    """Pitch from the longest evenly spaced row of caps.

    Measured, not assumed: the note row gives 13 intervals, which beats any
    two-edge measurement of the faceplate under this side lighting.
    """
    best = None
    rows = {}
    for c in comps:
        rows.setdefault(row_key(c), []).append(c)
    for members in rows.values():
        xs = np.sort(np.array([(c[0] + c[2]) / 2 for c in members], float))
        for run in split_runs(xs):
            if len(run) < 5:
                continue
            sx = xs[run]
            d = np.diff(sx)
            if np.std(d) > 0.06 * np.median(d):
                continue
            if best is None or len(sx) > best[0]:
                i = np.arange(len(sx))
                m, _ = np.polyfit(i, sx, 1)
                best = (len(sx), m)
    if best is None:
        raise SystemExit("no evenly spaced row found - cap detection failed")
    print("  key pitch %.2f px over %d intervals" % (best[1], best[0] - 1))
    return best[1]


def fit_rings(lum, seeds, pxmm):
    """Encoder recess centres: a dark groove against cream inside and outside."""
    th = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    ct, st = np.cos(th), np.sin(th)

    def score(cx, cy, r):
        tot = 0
        for rr, w in ((r, -2.0), (r - 14, 1.0), (r + 14, 1.0)):
            xs = np.clip((cx + rr * ct).astype(int), 0, lum.shape[1] - 1)
            ys = np.clip((cy + rr * st).astype(int), 0, lum.shape[0] - 1)
            tot = tot + w * lum[ys, xs]
        return float(np.median(tot))

    out = []
    for cx0, cy0 in seeds:
        best = None
        for dx in np.arange(-24, 24.1, 0.5):
            for dy in np.arange(-24, 24.1, 0.5):
                for r in np.arange(ENC_MM * pxmm - 10, ENC_MM * pxmm + 10, 1.0):
                    sc = score(cx0 + dx, cy0 + dy, r)
                    if best is None or sc > best[0]:
                        best = (sc, cx0 + dx, cy0 + dy, r)
        out.append(best)
        print("  ring at %.1f %.1f  r %.1f px (%.2f mm)  contrast %.0f"
              % (best[1], best[2], best[3], best[3] / pxmm, best[0]))
    xs = np.array([b[1] for b in out])
    ys = np.array([b[2] for b in out])
    i = np.arange(len(out))
    px, py = np.polyfit(i, xs, 1), np.polyfit(i, ys, 1)
    print("  encoder pitch %.2f px (%.2f mm), line residual %.1f px"
          % (px[0], px[0] / pxmm, np.abs(xs - np.polyval(px, i)).max()))
    return list(zip(np.polyval(px, i), np.polyval(py, i)))


# --- 6. shapes -------------------------------------------------------------
def rounded(x0, y0, x1, y1, r, seg=5):
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
                       (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for s in range(seg + 1):
            t = math.radians(a0 + 90 * s / seg)
            pts.append([round(cx + r * math.cos(t), 1),
                        round(cy + r * math.sin(t), 1)])
    return pts


def circle(cx, cy, r, seg=24):
    return [[round(cx + r * math.cos(2 * math.pi * i / seg), 1),
             round(cy + r * math.sin(2 * math.pi * i / seg), 1)]
            for i in range(seg)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("photo", help="the raw panel shot")
    ap.add_argument("--lcd-box", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                    help="rough bounds of the navy LCD in the raw shot; "
                         "found automatically if omitted")
    ap.add_argument("--dry-run", action="store_true",
                    help="measure and report, write nothing")
    args = ap.parse_args()

    pygame.init()
    rgb = pygame.surfarray.array3d(
        pygame.image.load(args.photo)).transpose(1, 0, 2).astype(float)
    lum = rgb.mean(2)
    print("raw photo %dx%d" % (rgb.shape[1], rgb.shape[0]))

    box = args.lcd_box
    if box is None:
        R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        navy = (B > R + 25) & (B > G + 15) & (B > 45) & (B < 220)
        cs = components(navy, min_px=400)
        box = max(cs, key=lambda c: (c[2] - c[0]) * (c[3] - c[1]))
        print("LCD found at", box)

    print("1. LCD aperture edges")
    quad = find_lcd(lum, box)
    w = (math.dist(quad[0], quad[1]) + math.dist(quad[3], quad[2])) / 2
    h = (math.dist(quad[0], quad[3]) + math.dist(quad[1], quad[2])) / 2
    print("  aperture %.1f x %.1f px, aspect %.4f (want 1.3333)" % (w, h, w / h))

    print("2. rectifying affine")
    dx0, dy0, dx1, dy1 = device_bbox(rgb)
    A = rectify(rgb, quad, np.array([(dx0 + dx1) / 2.0, (dy0 + dy1) / 2.0]))

    print("3. warping to %dx%d" % CANVAS)
    out = warp(rgb, A)
    rect_lum = out.mean(2).astype(float)
    R, G, B = out[..., 0].astype(float), out[..., 1].astype(float), out[..., 2].astype(float)

    q = lambda p: (A @ np.array([p[0], p[1], 1.0]))[:2]
    lcd = np.array([q(p) for p in quad])
    LX, LY = lcd[:, 0].min(), lcd[:, 1].min()
    print("  LCD lands at %.1f %.1f" % (LX, LY))

    print("4. scale")
    cream = (rect_lum > 132) & (R > B + 6) & (R > 140)
    keep = np.zeros_like(cream)
    keep[700:2180, 1150:3960] = True
    cream &= keep
    cream[int(LY) - 15:int(LY) + LCD_H + 15, int(LX) - 20:int(LX) + LCD_W + 20] = False
    blobs = components(cream)
    pitch = key_pitch(blobs)
    pxmm = pitch / GRID_MM
    print("  px/mm %.4f  (cap %.0f px, corner r %.1f px, encoder r %.1f px)"
          % (pxmm, CAP_MM * pxmm, RADIUS_MM * pxmm, ENC_MM * pxmm))

    print("5. caps")
    km_path = os.path.join(CHROME, "keymap.json")
    with open(km_path) as f:
        prev = json.load(f)["hotspots"]
    ref = {k: (sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v))
           for k, v in prev.items()}
    cap = CAP_MM * pxmm
    caps = [c for c in blobs
            if 0.55 * cap < c[2] - c[0] < 2.2 * cap
            and 0.55 * cap < c[3] - c[1] < 2.8 * cap]
    print("  %d cap-shaped blobs" % len(caps))

    # name every blob by nearest previous hotspot, keeping the best blob per name
    want = [n for n in ref if not n.startswith("ENC")]
    claim = {}
    for c in caps:
        cx, cy = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
        n = min(want, key=lambda k: math.dist((cx, cy), ref[k]))
        d = math.dist((cx, cy), ref[n])
        if n not in claim or d < claim[n][0]:
            claim[n] = (d, c)
    missing = [n for n in want if n not in claim]
    print("  named %d/%d caps%s" % (len(claim), len(want),
          (", grid will fill " + ",".join(missing)) if missing else ""))

    # group into rows by shared top/bottom, then into runs by x
    rows = {}
    for n, (_, c) in claim.items():
        rows.setdefault(row_key(c), []).append((n, c))
    rects = {}
    for members in rows.values():
        members.sort(key=lambda m: m[1][0])
        ty = int(np.median([m[1][1] for m in members]))
        by = int(np.median([m[1][3] for m in members]))
        w = int(np.median([m[1][2] - m[1][0] for m in members]))
        cx = np.array([(m[1][0] + m[1][2]) / 2 for m in members], float)
        for run in split_runs(cx):
            sub = [members[i] for i in run]
            sx = cx[run]
            d = np.diff(sx)
            uniform = len(sx) > 2 and np.std(d) < 0.06 * np.median(d)
            if len(sx) == 2:
                uniform, d = True, np.array([sx[1] - sx[0]])
            if not uniform:
                # piano spacing (the black keys): keep measured x, only the
                # cap widths are regularised, by shape
                for (n, c), x in zip(sub, sx):
                    ww = c[2] - c[0]
                    rects[n] = (x - ww / 2, ty, x + ww / 2, by)
                continue
            idx = np.round((sx - sx[0]) / np.median(d)).astype(int)
            m, b = np.polyfit(idx, sx, 1)
            for n, i in zip([x[0] for x in sub], idx):
                rects[n] = (m * i + b - w / 2, ty, m * i + b + w / 2, by)
            # the same grid places any cap the detector lost
            for n in list(missing):
                if abs(ref[n][1] - (ty + by) / 2) > 0.6 * (by - ty):
                    continue
                i = int(round((ref[n][0] - b) / m))
                if i in idx or not (min(idx) - 2 <= i <= max(idx) + 2):
                    continue
                rects[n] = (m * i + b - w / 2, ty, m * i + b + w / 2, by)
                missing.remove(n)
                print("  grid filled %s at index %d" % (n, i))

    if missing:
        raise SystemExit("could not place: %s" % ", ".join(missing))

    print("6. encoder rings")
    eref = sorted((ref[k] for k in ref if k.startswith("ENC")), key=lambda p: p[0])
    cen = fit_rings(rect_lum, eref, pxmm)

    hot = {n: rounded(*rects[n], RADIUS_MM * pxmm) for n in rects}
    for i, (ex, ey) in enumerate(cen):
        hot["ENC%d" % (i + 1)] = circle(ex, ey, ENC_MM * pxmm)
    print("  %d hotspots" % len(hot))

    if args.dry_run:
        return
    with open(km_path, "w") as f:
        json.dump({"hotspots": hot,
                   # recorded, not inferred: a square cap's rounded-rect
                   # polygon is not geometrically distinguishable from a circle
                   "shapes": {n: "circle" for n in hot if n.startswith("ENC")}},
                  f, indent=1)
    print("wrote chrome/keymap.json")
    pygame.image.save(pygame.surfarray.make_surface(out.transpose(1, 0, 2)),
                      PHOTO_OUT, os.path.basename(PHOTO_OUT))
    print("wrote", PHOTO_OUT)
    with open(os.path.join(CHROME, "screen_rect.json"), "w") as f:
        json.dump({"image": os.path.relpath(PHOTO_OUT, REPO),
                   "image_size": list(CANVAS),
                   "rect": [round(LX, 1), round(LY, 1), LCD_W, LCD_H],
                   "corner_radius": round(RADIUS_MM * pxmm, 1),
                   "px_per_mm": round(pxmm, 4),
                   "shell": SHELL, "shell_radius": SHELL_RADIUS,
                   "shell_rim": SHELL_RIM}, f, indent=1)
    print("wrote chrome/screen_rect.json")


if __name__ == "__main__":
    main()
