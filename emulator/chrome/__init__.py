"""Desktop chrome: the look-alike photo, its hotspots and the LCD cutout.

Two layers live here:

* pure data/geometry (``load_keymap``, ``load_screen_rect``, ``hit_test``,
  ``fb_rect``) — importable without pygame, used by the tests;
* :class:`ChromeView` — the pygame surface that draws photo + framebuffer +
  pressed-key outlines, used by ``main.py``.

Coordinates in ``keymap.json`` / ``screen_rect.json`` are **photo pixels**
(4455x2784 — see ``image_size``). Everything else is derived by scaling.
"""

import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import pygame
except ImportError:                                   # pragma: no cover
    pygame = None


HERE = os.path.dirname(os.path.abspath(__file__))
EMU_DIR = os.path.dirname(HERE)
REPO_DIR = os.path.dirname(EMU_DIR)

KEYMAP_PATH = os.path.join(HERE, "keymap.json")
SCREEN_RECT_PATH = os.path.join(HERE, "screen_rect.json")
PHOTO_PATH = os.path.join(REPO_DIR, "docs", "design", "reference",
                          "lookalike-model.jpg")

FB_W, FB_H = 320, 240

# Whole-window translation in *window* pixels, applied to the photo, the LCD
# cutout, the framebuffer, the zoom pane and the hotspots alike — so a click
# still lands on the key you see. Photo-space data (keymap.json,
# screen_rect.json) is untouched. The photo is now cropped to the device with
# an even grass border, so nothing needs nudging.
OFFSET = (0, 0)

# Chrome guides are not device colours and deliberately do not come from
# ui/palette.py — they must stay legible whatever the UI palette becomes.
SCREEN_BORDER = (255, 255, 255)
# the LCD's well and the window behind the photo: the panel off, black whatever
# mode the screen is in (it was `palette.BLACK` until #334 took black out of it)
WELL = (0, 0, 0)
# Pressed-key highlight: a straight-red ring with the cap brightened inside it.
OUTLINE_COLOUR = (255, 0, 0)
HILITE_FILL = (255, 255, 255, 56)         # low-opacity white over the keycap

Point = Tuple[float, float]
Poly = List[Point]


# --- data -----------------------------------------------------------------
def load_keymap(path: str = KEYMAP_PATH) -> Dict[str, Poly]:
    """name -> polygon (list of [x, y] in photo pixels)."""
    with open(path) as f:
        data = json.load(f)
    return {k: [tuple(p) for p in poly]
            for k, poly in data["hotspots"].items()}


def load_shapes(path: str = KEYMAP_PATH) -> Dict[str, str]:
    """name -> "circle"; anything absent is a rounded rect.

    Recorded, not inferred. A *square* keycap's rounded-rect polygon carries
    only corner-arc vertices, so its vertex radii vary by under 8 % — there is
    no geometric test that separates it from a circle, and guessing drew every
    square key as a circle.
    """
    with open(path) as f:
        return dict(json.load(f).get("shapes", {}))


def load_screen_rect(path: str = SCREEN_RECT_PATH) -> Tuple[int, int, int, int]:
    with open(path) as f:
        return tuple(json.load(f)["rect"])


def load_screen_quad(path: str = SCREEN_RECT_PATH) -> Poly:
    """The four measured corners of the navy LCD area, in photo pixels.

    The panel is a hair rotated in the photo, so ``rect`` is the largest
    axis-aligned rectangle inside this quad — that is what the framebuffer
    uses. The quad itself is kept for anyone re-measuring the photo.
    """
    with open(path) as f:
        return [tuple(p) for p in json.load(f)["quad"]]


def load_photo_size(path: str = SCREEN_RECT_PATH) -> Tuple[int, int]:
    with open(path) as f:
        return tuple(json.load(f)["image_size"])


def load_corner_radius(path: str = SCREEN_RECT_PATH) -> float:
    """LCD bezel corner radius in photo px (3.5 mm, same as the keycaps)."""
    with open(path) as f:
        return float(json.load(f).get("corner_radius", 0.0))


# --- geometry -------------------------------------------------------------
def point_in_poly(pt: Point, poly: Sequence[Point]) -> bool:
    """Even-odd ray cast. Convex or not, doesn't matter."""
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            # x of the edge at height y
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def poly_bbox(poly: Sequence[Point]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def polys_overlap(a: Sequence[Point], b: Sequence[Point]) -> bool:
    """True if the two (axis-aligned-ish) polygons share any area.

    Cheap and sufficient here: bbox reject, then vertex-in-other tests plus a
    centre test. Hotspots are convex blobs, so this is exact enough.
    """
    ax0, ay0, ax1, ay1 = poly_bbox(a)
    bx0, by0, bx1, by1 = poly_bbox(b)
    if ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0:
        return False
    for p in a:
        if point_in_poly(p, b):
            return True
    for p in b:
        if point_in_poly(p, a):
            return True
    ca = (sum(p[0] for p in a) / len(a), sum(p[1] for p in a) / len(a))
    cb = (sum(p[0] for p in b) / len(b), sum(p[1] for p in b) / len(b))
    return point_in_poly(ca, b) or point_in_poly(cb, a)


def hit_test(pt: Point, keymap: Dict[str, Poly]) -> Optional[str]:
    """Hotspot name under a point in photo coords, or None."""
    for name, poly in keymap.items():
        if point_in_poly(pt, poly):
            return name
    return None


def fb_rect(screen_rect: Sequence[float],
            fb_w: int = FB_W, fb_h: int = FB_H) -> Tuple[int, int, int, int]:
    """Largest **integer** framebuffer scale that fits ``screen_rect``, centred.

    Returns (x, y, w, h) in the same coordinate space as ``screen_rect``.
    The scale never drops below 1 — at small window sizes the 320x240 frame is
    simply a little larger than the photo's LCD (see emulator/CLAUDE.md).
    """
    x, y, w, h = screen_rect
    scale = max(1, min(int(w // fb_w), int(h // fb_h)))
    fw, fh = fb_w * scale, fb_h * scale
    return (int(round(x + (w - fw) / 2)), int(round(y + (h - fh) / 2)), fw, fh)


def fb_scale(screen_rect: Sequence[float],
             fb_w: int = FB_W, fb_h: int = FB_H) -> int:
    return max(1, min(int(screen_rect[2] // fb_w), int(screen_rect[3] // fb_h)))


def fit_scale(photo_rect: Sequence[float], photo_w: int, max_width: int,
              fb_w: int = FB_W) -> float:
    """Photo scale that lands the LCD on an exact multiple of 320x240.

    Any other scale leaves the framebuffer letterboxed inside the navy area
    (or spilling over it). Picks the largest such scale whose window is still
    <= ``max_width``, falling back to 1x when even that is too wide.
    """
    k = int(max_width / photo_w * photo_rect[2] // fb_w)
    return fb_w * max(1, k) / photo_rect[2]


# --- the pygame view ------------------------------------------------------
class ChromeView:
    """Photo backdrop + framebuffer + pressed-key outlines.

    ``photo_scale`` maps photo pixels to window pixels. If the photo has an
    alpha channel (Elvin's future PNG with a hole cut for the LCD) the
    framebuffer is drawn *under* it; otherwise it is drawn *over* the LCD rect.

    Every measurement here is in **device** pixels. ``device`` is how many of
    those go to one of the display's points (2.0 on Retina, see
    ``chrome/window.py``), and it multiplies the scale rather than changing any
    of the layout arithmetic: at ``device=2`` a 1x LCD is 640 device px wide,
    which is the same size on the glass as 320 px on a 1x display but drawn at
    the panel's real resolution.
    """

    OUTLINE_FRAMES = 2          # keep an outline for >= 1 frame after release
    OUTLINE_W = 4               # press-outline stroke, window px
    OUTLINE_SS = 4              # supersampling for the outline's antialiasing

    def __init__(self, photo_path: str = PHOTO_PATH, max_width: int = 1400,
                 photo_scale: Optional[float] = None,
                 keymap_path: str = KEYMAP_PATH,
                 screen_rect_path: str = SCREEN_RECT_PATH,
                 offset: Optional[Tuple[int, int]] = None,
                 max_size: Optional[Tuple[int, int]] = None,
                 device: float = 1.0):
        if pygame is None:                            # pragma: no cover
            raise RuntimeError("pygame required for ChromeView")
        self.device = max(1.0, float(device))
        self._t = max(1, int(round(self.OUTLINE_W * self.device)))
        self.offset = tuple(OFFSET if offset is None else offset)
        self.keymap = load_keymap(keymap_path)
        self.shapes = load_shapes(keymap_path)
        self.photo_rect = load_screen_rect(screen_rect_path)
        self.corner_radius = load_corner_radius(screen_rect_path)

        self._raw = pygame.image.load(photo_path)
        self.has_alpha = (self._raw.get_alpha() is not None
                          or self._raw.get_flags() & pygame.SRCALPHA)
        pw, ph = self._raw.get_size()
        self.photo_size = (pw, ph)
        self._cap = max_size
        self._rescale(self.device * (float(photo_scale) if photo_scale
                      else fit_scale(self.photo_rect, pw, max_width)))

        self.fb_surface = pygame.Surface((FB_W, FB_H))
        self._held = {}                  # name -> frames remaining
        self._outline_cache = {}         # (w, h, radius, circle) -> ring surface
        self._mask_cache = {}            # (w, h, radius) -> LCD corner mask
        self._bg = None                  # composited photo, window-sized
        self._dirty = []                 # rects to restore from _bg next frame

        # zoom pane over the photo's right half
        self.zoomed = False

    def optimize(self, target=None):
        """Convert the photo to the display format. No-op with no target.

        ``target`` is the window surface: with the high-DPI ``pygame.Window``
        there is no display surface for the no-argument ``convert()`` to find,
        and an unconverted 24-bit photo costs several ms a frame to blit.
        """
        args = (target,) if target is not None else ()
        try:
            self.photo = (self.photo.convert_alpha(*args) if self.has_alpha
                          else self.photo.convert(*args))
            self._target = target
        except pygame.error:
            pass                      # no video mode yet; caller re-runs later

    def _rescale(self, scale: float):
        """Re-derive every window-space measurement from a new photo scale."""
        pw, ph = self.photo_size
        self.scale = scale
        self.photo_px = (max(1, int(round(pw * scale))),
                         max(1, int(round(ph * scale))))
        self.photo = (self._raw if self.photo_px == (pw, ph)
                      else pygame.transform.smoothscale(self._raw, self.photo_px))
        self.optimize(getattr(self, "_target", None))

        # The window never exceeds the display: a scale that makes the photo
        # bigger than the screen simply crops it, centred on the synth, rather
        # than opening a window you cannot see the middle of.
        cap = self._cap or self.photo_px
        d = int(self.device) if float(self.device).is_integer() else 1
        self.size = tuple(max(d, (min(self.photo_px[i], cap[i]) // d) * d)
                          for i in (0, 1))
        # A cropped window is centred on the photo — the whole synth in frame,
        # which is the point of the chrome. From 2x up it is centred on the
        # *LCD* instead: at that scale the photo's centre is halfway down the
        # keybed with the screen off past the window edge.
        on_lcd = round(scale * self.photo_rect[2] / (FB_W * self.device)) > 1
        self.origin = tuple(
            (self.size[i] - self.photo_px[i]) // 2
            if not on_lcd or self.photo_px[i] <= self.size[i] else
            int(max(self.size[i] - self.photo_px[i],
                    min(0, round(self.size[i] / 2
                                 - (self.photo_rect[i] + self.photo_rect[i + 2] / 2)
                                 * scale))))
            for i in (0, 1))

        self.screen_rect = tuple(v * scale for v in self.photo_rect)
        self.fb_rect = fb_rect(self.screen_rect)
        self.fb_scale = fb_scale(self.screen_rect)
        self.fb_radius = self.corner_radius * scale
        self.zoom_rect = self._zoom_rect()
        self._outline_cache = {}
        self._bg = None

    @property
    def fb_multiple(self) -> int:
        """How many window *points* the LCD gives each framebuffer pixel."""
        return max(1, int(round(self.screen_rect[2] / (FB_W * self.device))))

    def set_fb_multiple(self, k: int) -> bool:
        """Scale the whole chrome so the LCD is k x 320x240. False if unchanged.

        Everything rides on one number, so 2x is exactly 1x doubled — photo,
        hotspots, outlines and framebuffer together — and the window crops to
        the display rather than growing past it.
        """
        scale = self.device * FB_W * max(1, int(k)) / self.photo_rect[2]
        if abs(scale - self.scale) < 1e-9:
            return False
        self._rescale(scale)
        return True

    # -- geometry ---------------------------------------------------------
    def _zoom_rect(self):
        """Integer 3x (or the largest that fits) pane over the right half."""
        w, h = self.size
        avail_w, avail_h = w // 2 - 16, h - 32
        s = max(1, min(3, avail_w // FB_W, avail_h // FB_H))
        fw, fh = FB_W * s, FB_H * s
        return (w - fw - (w // 2 - fw) // 2, (h - fh) // 2, fw, fh)

    @property
    def photo_at(self) -> Tuple[int, int]:
        """Where the photo's top-left sits in the window (may be negative)."""
        return (self.origin[0] + self.offset[0], self.origin[1] + self.offset[1])

    def shift(self, rect) -> Tuple[int, int, int, int]:
        """A photo-space-derived rect moved into window space."""
        ox, oy = self.photo_at
        return (int(rect[0]) + ox, int(rect[1]) + oy, int(rect[2]), int(rect[3]))

    def window_to_photo(self, pos) -> Point:
        """Undo the placement before scaling, so hotspots follow what is drawn."""
        ox, oy = self.photo_at
        return ((pos[0] - ox) / self.scale, (pos[1] - oy) / self.scale)

    def hit(self, pos) -> Optional[str]:
        """Hotspot under a *window* position."""
        return hit_test(self.window_to_photo(pos), self.keymap)

    # -- press feedback ---------------------------------------------------
    def press(self, name: str):
        self._held[name] = -1            # -1 = held down

    def release(self, name: str):
        if name in self._held:
            self._held[name] = self.OUTLINE_FRAMES

    def clear(self):
        """Drop every highlight at once (panic, focus loss)."""
        self._held.clear()

    def toggle_zoom(self):
        self.zoomed = not self.zoomed

    # -- render -----------------------------------------------------------
    def _background(self, dest):
        """Photo + black LCD well, composited once at window size.

        At Retina resolution the photo is 5120x2880 and blitting it every frame
        costs ~12 ms. It never changes between rescales, so it is composited
        once and only the rects that actually move — the LCD, the zoom pane,
        the key highlights — are repainted from it each frame.
        """
        if self._bg is None or self._bg.get_size() != dest.get_size():
            bg = pygame.Surface(dest.get_size())
            bg.fill(WELL)
            bg.blit(self.photo, self.photo_at)
            sx, sy, sw, sh = self.shift(self.screen_rect)
            pygame.draw.rect(bg, WELL, pygame.Rect(sx, sy, sw, sh),
                             border_radius=int(round(self.fb_radius)))
            self._bg = bg
            self._dirty = None            # force a full repaint
        return self._bg

    def draw(self, dest, fb: np.ndarray):
        pygame.surfarray.blit_array(self.fb_surface, np.transpose(fb, (1, 0, 2)))
        px, py = self.photo_at
        fb_at = self.shift(self.fb_rect)
        r = self.fb_radius

        if self.has_alpha:
            # framebuffer *under* the photo: no static composite to restore from
            dest.fill(WELL)
            self._blit_fb(dest, fb_at, r)
            dest.blit(self.photo, (px, py))
            self._dirty = []              # repainted whole: nothing to restore
        else:
            bg = self._background(dest)
            if self._dirty is None:
                dest.blit(bg, (0, 0))
            else:
                for rect in self._dirty:
                    dest.blit(bg, rect.topleft, rect)
            self._dirty = []
            self._blit_fb(dest, fb_at, r)

        if self.zoomed:
            # zoom_rect is already window space (it is sized from self.size):
            # only the hand nudge applies, never the crop's negative origin
            zx, zy, zw, zh = self.zoom_rect
            zoom_at = (zx + self.offset[0], zy + self.offset[1], zw, zh)
            zx, zy, zw, zh = zoom_at
            self._mark(pygame.Rect(zx - 5, zy - 5, zw + 10, zh + 10))
            dest.fill(WELL, pygame.Rect(zx - 4, zy - 4, zw + 8, zh + 8))
            self._blit_fb(dest, zoom_at)
            self._draw_screen_border(dest, zoom_at)

        self._draw_outlines(dest)

    def _draw_screen_border(self, dest, rect, radius=0):
        """1 px ring *outside* the active area — it must not cover a pixel of
        the 320x240 frame, so the rect is grown by one before being stroked.

        Only the zoom pane uses this now: it floats over the photo and needs an
        edge. The LCD itself sits in a real bezel and wants no drawn ring."""
        x, y, w, h = rect
        pygame.draw.rect(dest, SCREEN_BORDER,
                         pygame.Rect(x - 1, y - 1, w + 2, h + 2), 1,
                         border_radius=int(round(radius)) + 1 if radius else 0)

    def _mark(self, rect):
        """Remember a rect to restore from the background on the next frame."""
        if self._dirty is not None:
            self._dirty.append(pygame.Rect(rect).clip(
                pygame.Rect(0, 0, *self.size)))

    def _corner_mask(self, w, h, radius):
        key = (w, h, round(radius, 1))
        hit = self._mask_cache.get(key)
        if hit is None:
            hit = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.rect(hit, (255, 255, 255, 255), pygame.Rect(0, 0, w, h),
                             border_radius=int(round(radius)))
            self._mask_cache[key] = hit
        return hit

    def _blit_fb(self, dest, rect, radius=0):
        x, y, w, h = rect
        # never smoothscale — pixels stay pixels
        scaled = pygame.transform.scale(self.fb_surface, (w, h))
        if radius >= 1:
            # the physical bezel has a 3.5 mm radius; punch the same shape out
            # of the frame so the photo's cream shows through at the corners
            scaled = scaled.convert_alpha()
            scaled.blit(self._corner_mask(w, h, radius), (0, 0),
                        special_flags=pygame.BLEND_RGBA_MULT)
        dest.blit(scaled, (x, y))

    def _outline_surface(self, w, h, radius, circle):
        """A stroked, antialiased ring that hugs the cap, over a soft fill.

        pygame has no antialiased thick primitives, so it is drawn oversized and
        smoothscaled down. The stroke sits *outside* the hotspot: the inner edge
        has the cap's own radius and the outer edge that plus the stroke width,
        which is what keeps the width even round the corners — stroking the
        polygon directly does not. Inside it, the cap itself is washed with
        ``HILITE_FILL`` so a held key reads as lit, not just outlined.
        """
        key = (w, h, round(radius, 1), circle)
        hit = self._outline_cache.get(key)
        if hit is not None:
            return hit
        t, ss = self._t, self.OUTLINE_SS
        big = pygame.Surface(((w + 2 * t) * ss, (h + 2 * t) * ss), pygame.SRCALPHA)
        if circle:
            centre = (big.get_width() / 2, big.get_height() / 2)
            pygame.draw.circle(big, HILITE_FILL, centre, min(w, h) / 2 * ss)
            pygame.draw.circle(big, OUTLINE_COLOUR, centre,
                               (min(w, h) / 2 + t) * ss, t * ss)
        else:
            pygame.draw.rect(big, HILITE_FILL,
                             pygame.Rect(t * ss, t * ss, w * ss, h * ss), 0,
                             border_radius=int(round(radius * ss)))
            pygame.draw.rect(big, OUTLINE_COLOUR,
                             pygame.Rect(0, 0, *big.get_size()), t * ss,
                             border_radius=int(round((radius + t) * ss)))
        small = pygame.transform.smoothscale(big, (w + 2 * t, h + 2 * t))
        self._outline_cache[key] = small
        return small

    def _draw_outlines(self, dest):
        ox, oy = self.photo_at          # not self.offset: the photo is also
                                        # centred, and cropped when the window
                                        # is capped to the display
        radius = self.corner_radius * self.scale
        for name in list(self._held):
            poly = self.keymap.get(name)
            if not poly:
                continue
            x0, y0, x1, y1 = poly_bbox(poly)
            w = int(round((x1 - x0) * self.scale))
            h = int(round((y1 - y0) * self.scale))
            if w < 1 or h < 1:
                continue
            ring = self._outline_surface(
                w, h, radius, self.shapes.get(name) == "circle")
            at = (int(round(x0 * self.scale)) + ox - self._t,
                  int(round(y0 * self.scale)) + oy - self._t)
            self._mark(pygame.Rect(at, ring.get_size()))
            dest.blit(ring, at)
            n = self._held[name]
            if n > 0:
                self._held[name] = n - 1
            elif n == 0:
                del self._held[name]
