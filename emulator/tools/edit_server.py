#!/usr/bin/env python3
"""Live palette + font editor for the running emulator.

Started by `main.py --edit`, on a daemon thread. The pygame window is the
preview: it re-renders every frame, and every draw site reads `palette.X` and
`font._GLYPHS` at call time, so an edit lands on the next frame — on whatever
page you have navigated to. This server only serves the controls.

Thread note: CPython rebinds a module attribute atomically and glyph arrays
are replaced wholesale, so the worst a race can do is render one frame with a
mix of old and new. At 60 fps that is invisible.

    venv/bin/python main.py --width 1800 --edit
    venv/bin/python tools/edit_server.py       # standalone, no preview
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # emulator/
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from ui import draw, font, palette                             # noqa: E402
import tools.edit_writeback as writeback                       # noqa: E402

DEFAULT_PORT = 8765
PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "edit_page.html")


def _state():
    return {
        "groups": [{"title": t, "names": list(n)} for t, n in palette.GROUPS],
        "colors": {k: list(v) for k, v in palette.snapshot().items()},
        "paletteDirty": list(palette.dirty()),
        # #334: two tables; the rows show the mode on screen, Save writes both
        "mode": palette.mode(),
        "modes": list(palette.MODES),
        "paletteDirtyAll": len(palette.dirty_entries()),
        "fontDirty": [[s, c] for s, c in font.dirty()],
        "sizes": [font.SMALL, font.LARGE],
        "cell": {s: list(font.CELL[s]) for s in (font.SMALL, font.LARGE)},
        "ink": {s: list(font.INK[s]) for s in (font.SMALL, font.LARGE)},
        "charset": {s: font.charset(s) for s in (font.SMALL, font.LARGE)},
        "default": {s: font.METRICS[s][2] for s in (font.SMALL, font.LARGE)},
        "tabular": {s: font._TABULAR_SRC[s] for s in (font.SMALL, font.LARGE)},
        "tabularChars": sorted(font.TABULAR),
        "spacingDirty": font.spacing_dirty(),
    }


def _glyphs(size):
    """Grid, effective advance and ink width per character."""
    return {ch: {"grid": font.get_grid(ch, size),
                 "adv": font.advance(ch, size),
                 "ink": font.ink_width(ch, size)}
            for ch in font.charset(size)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):                       # keep the console clean
        pass

    def _send(self, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _fail(self, code, msg):
        raw = json.dumps({"error": msg}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(PAGE_PATH) as f:
                return self._send(f.read(), "text/html")
        if path == "/api/state":
            return self._send(_state())
        if path == "/api/glyphs":
            size = self.path.split("size=")[-1] if "size=" in self.path else font.SMALL
            if size not in (font.SMALL, font.LARGE):
                return self._fail(400, f"bad size {size!r}")
            return self._send(_glyphs(size))
        return self._fail(404, path)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as exc:
            return self._fail(400, f"bad json: {exc}")
        path = self.path.split("?")[0]
        try:
            if path == "/api/color":
                palette.set_color(body["name"], body["rgb"])
            elif path == "/api/glyph":
                font.set_grid(body["ch"], body["size"], body["grid"])
            elif path == "/api/mode":
                # which table the swatches edit, and what the preview draws.
                # Not the instrument's setting: the phone (or V) owns that.
                palette.set_mode(body["mode"])
                return self._send(_state())
            elif path == "/api/revert-color":
                palette.revert(body["name"])
            elif path == "/api/revert-glyph":
                font.revert(body["ch"], body["size"])
            elif path == "/api/advance":
                font.set_advance(body["ch"], body["size"], body.get("adv"))
            elif path == "/api/tabular":
                font.set_tabular(body["size"], body.get("adv"))
            elif path == "/api/autofit":
                size = body["size"]
                moved = font.autofit(size, int(body.get("tracking", 1)),
                                     only=body.get("only"))
                return self._send({"moved": moved, "glyphs": _glyphs(size),
                                   **_state()})
            elif path == "/api/revert-spacing":
                font.revert_spacing()
                return self._send({"glyphs": _glyphs(body["size"]), **_state()})
            elif path == "/api/save":
                done = writeback.write_all()
                return self._send({"saved": {"palette": list(done["palette"]),
                                             "font": [list(e) for e in done["font"]],
                                             "spacing": done["spacing"]},
                                   **_state()})
            else:
                return self._fail(404, path)
        except (KeyError, ValueError, LookupError) as exc:
            return self._fail(400, f"{type(exc).__name__}: {exc}")
        finally:
            # C5b (#98): `draw.text` caches one ink mask per (string, size),
            # built from `font.glyph`/`font.advance`. This server is the only
            # thing in the build that mutates either, so it is the only thing
            # that has to drop them — unconditionally rather than per branch,
            # because a POST here is human-paced and a branch added later
            # would otherwise leave the preview showing the old glyph.
            draw.invalidate_text_cache()
        return self._send({"paletteDirty": list(palette.dirty()),
                           "paletteDirtyAll": len(palette.dirty_entries()),
                           "fontDirty": [[s, c] for s, c in font.dirty()],
                           "spacingDirty": font.spacing_dirty(),
                           "tabular": {s: font._TABULAR_SRC[s]
                                       for s in (font.SMALL, font.LARGE)}})


def serve(port=DEFAULT_PORT, background=True):
    """Start the editor. Returns (server, url)."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    if background:
        threading.Thread(target=httpd.serve_forever, daemon=True,
                         name="edit-server").start()
    return httpd, url


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    httpd, url = serve(port, background=False)
    print(f"editor: {url}  (no preview — run `main.py --edit` for the live window)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
