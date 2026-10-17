#!/usr/bin/env python3
"""ArbutusOS desktop emulator — phase 3.

The window is the look-alike photo (`docs/design/reference/lookalike-model.jpg`)
with the 320x240 framebuffer blitted into the LCD cutout at an integer scale.
Keys are clickable hotspots (`chrome/keymap.json`); the physical keyboard map
from phase 1 still works; MIDI drives the chromatic keys and the 4 encoders.

Run:
    python3 main.py                       # photo chrome, ~1400 px wide
    python3 main.py --scale 0.55          # explicit photo scale
    python3 main.py --zoom-only           # phase-1 plain 3x window
    python3 main.py --fresh               # wipe emulator/state/ first
    python3 main.py --midi "Keystation"
    python3 main.py --exit-after 1 --headless --screenshot out.png

The audio is always `audio/build/host/arbutus-audio` (stage N1): there is no
in-process graph any more. `--offline` runs it with no device, which is what
the tests and `tools/verify.py` use.

As the mock device's UI (the iPhone companion app sees this window's screen):

    python3 main.py --attach ~/.arbutus/mock/run/audio.sock \
        --share ~/.arbutus/mock/run \
        --state-dir ~/.arbutus/mock/data/.state --library ~/.arbutus/mock/data

`emulator/run.sh --phone` starts the mock and runs exactly this. Each flag is
independent and, left off, changes nothing (the private host, `emulator/state/`,
`audio/library/`):

    --attach SOCKET    use the host already on SOCKET; never spawn or stop one.
                       The host's clock is its own: whether to pull `render`
                       is read from its `state`, and `--offline` is ignored
    --share DIR        publish each frame for `GET /screen.png` through
                       `arbutus_os/screenshare.py` (the device UI's own hand-over)
    --state-dir DIR    the working state (the device's `/data/.state`)
    --library DIR      the library root (the device's `/data`): `projects/`,
                       `settings.json`, the browsers
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

# Keep SDL away from the audio device entirely; sounddevice owns it.
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from app.app import App, SAMPLE_RATE
from app import project_file
from app.keys import ENC_PUSH_KEYS, Key, note_key

try:
    import pygame
except ImportError:                                   # pragma: no cover
    pygame = None


# --- keyboard map (documented in CLAUDE.md) --------------------------------
def build_keymap():
    """pygame key -> Key / NOTE_n / (encoder, delta). Unchanged since phase 1."""
    if pygame is None:                                # pragma: no cover
        return {}, {}, {}
    k = pygame.key
    cmd = {
        pygame.K_F1: Key.SYNTH, pygame.K_F2: Key.DRUM, pygame.K_F3: Key.TRACK,
        pygame.K_F4: Key.MIXER, pygame.K_F5: Key.SEQ, pygame.K_F6: Key.INPUT,
        pygame.K_F7: Key.HELP, pygame.K_F8: Key.FILE, pygame.K_F9: Key.TEMPO,
        pygame.K_1: Key.K1, pygame.K_2: Key.K2, pygame.K_3: Key.K3,
        pygame.K_4: Key.K4,
        pygame.K_5: Key.A, pygame.K_6: Key.B, pygame.K_7: Key.C,
        pygame.K_8: Key.X, pygame.K_9: Key.Y, pygame.K_0: Key.Z,
        pygame.K_LEFT: Key.LEFT, pygame.K_RIGHT: Key.RIGHT,
        pygame.K_SPACE: Key.PLAY, pygame.K_BACKSPACE: Key.STOP,
        pygame.K_RETURN: Key.REC, pygame.K_TAB: Key.SLICE,
        pygame.K_MINUS: Key.LIFT, pygame.K_EQUALS: Key.DROP,
        pygame.K_LSHIFT: Key.SHIFT, pygame.K_RSHIFT: Key.SHIFT,
    }
    # Piano-aligned on the top two letter rows (internal input mode, no MIDI):
    # home row = white keys from F, the row above = the black key that sits
    # between its two neighbours, like a real keyboard. F..A = 17 chromatic
    # keys (one octave + a fourth). ← → shift octaves for the rest.
    #   w e   r   y u   o p
    #  a s d f g h j k l ;
    #  F G A B C D E F G A
    whites = "asdfghjkl;"
    white_idx = [0, 2, 4, 6, 7, 9, 11, 12, 14, 16]
    blacks = {"w": 1, "e": 3, "r": 5, "y": 8, "u": 10, "o": 13, "p": 15}
    notes = {}
    for ch, n in list(zip(whites, white_idx)) + list(blacks.items()):
        notes[k.key_code(ch)] = note_key(n)
    encs = {
        pygame.K_LEFTBRACKET: (1, -1), pygame.K_RIGHTBRACKET: (1, +1),
        pygame.K_COMMA: (3, -1), pygame.K_PERIOD: (3, +1),
    }
    return cmd, notes, encs


ENC_DRAG_PX = 6        # vertical mouse travel per encoder step while dragging


def drag_steps(y0: int, y: int) -> int:
    """Encoder steps for a drag from y0 to y: up = +, ENC_DRAG_PX per step.

    int() of the true quotient truncates toward zero, so the first step
    either way takes the full ENC_DRAG_PX. Floor division (``//``) rounded
    down: a 1 px downward jitter of a plain click sent -1 (#1123).
    """
    return int((y0 - y) / ENC_DRAG_PX)


_build_keymap = build_keymap          # phase-1 name, kept for compatibility


ENCODER_NAMES = ("ENC1", "ENC2", "ENC3", "ENC4")


class EncoderPushes:
    """The desktop's two ways to push ENC1-4 (#818), out of the event loop so
    a test can drive them (#1180): Ctrl (or Cmd) + 1-4, and a click on an
    encoder that turned nothing.

    A push is held like a key, so its release is the same digit's, whatever
    the modifier does meanwhile: `held` is the digits down as pushes."""

    def __init__(self, app):
        self.app = app
        self.keys = (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4)
        self.mods = pygame.KMOD_CTRL | pygame.KMOD_META
        self.held = set()

    def key(self, key, down: bool, mod: int) -> bool:
        """A KEYDOWN/KEYUP; True when it was a push (the loop stops there)."""
        if key not in self.keys or (key not in self.held
                                    and not (down and mod & self.mods)):
            return False
        (self.held.add if down else self.held.discard)(key)
        self.app.on_key(ENC_PUSH_KEYS[self.keys.index(key)], down)
        return True

    def click(self, idx: int):
        """A click on encoder `idx` that turned nothing: press and release."""
        push = ENC_PUSH_KEYS[idx]
        self.app.on_key(push, True)
        self.app.on_key(push, False)


def _parse_args(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="wipe emulator/state/")
    ap.add_argument("--scale", type=float, default=None,
                    help="photo scale (chrome) or integer window scale "
                         "with --zoom-only")
    ap.add_argument("--width", type=int, default=0,
                    help="max chrome window width in px (default: the display "
                         "width, which gives a 1x LCD filling the screen)")
    ap.add_argument("--zoom", action="store_true",
                    help="start with the zoomed framebuffer pane showing")
    ap.add_argument("--zoom-only", action="store_true",
                    help="phase-1 plain framebuffer window, no photo")
    ap.add_argument("--midi", default="", help="MIDI input name substring")
    ap.add_argument("--no-midi", action="store_true")
    ap.add_argument("--no-audio", action="store_true",
                    help="no audio device: the process runs --offline")
    ap.add_argument("--audio", choices=("native",), default="native",
                    help=argparse.SUPPRESS)      # kept as a no-op alias
    ap.add_argument("--offline", action="store_true",
                    help="no device; `render` is the clock (tests)")
    ap.add_argument("--no-mic", action="store_true",
                    help="disable the INPUT page's mic capture")
    ap.add_argument("--mic-device", default=None,
                    help="sounddevice input device index or name substring")
    ap.add_argument("--headless", action="store_true",
                    help="SDL dummy video driver, no audio device")
    ap.add_argument("--frames", type=int, default=0,
                    help="render N frames then exit cleanly")
    ap.add_argument("--exit-after", type=float, default=0.0,
                    help="run for N seconds then exit cleanly")
    ap.add_argument("--offset", type=int, nargs=2, metavar=("X", "Y"),
                    default=None,
                    help="translate the whole chrome by X Y window px "
                         "(default: chrome.OFFSET)")
    ap.add_argument("--edit", action="store_true",
                    help="live palette/font editor in the browser (aesthetic pass)")
    ap.add_argument("--edit-port", type=int, default=8765,
                    help="port for --edit (default 8765)")
    ap.add_argument("--screenshot", default="",
                    help="write the final window to this PNG on exit")
    ap.add_argument("--attach", default="", metavar="SOCKET",
                    help="use the arbutus-audio already listening on SOCKET "
                         "instead of spawning one (the mock device's host)")
    ap.add_argument("--share", default="", metavar="DIR",
                    help="publish frames for GET /screen.png into DIR (the "
                         "mock's run/ folder)")
    ap.add_argument("--state-dir", default="", metavar="DIR",
                    help="working state (default emulator/state/)")
    ap.add_argument("--library", default="", metavar="DIR",
                    help="library root: projects/, settings.json, browsers "
                         "(default audio/library/)")
    ap.add_argument("--appearance", choices=("light", "dark"), default=None,
                    help="start the screen in this mode, for this run only "
                         "(otherwise the mode it last had; V toggles, #334)")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)

    if args.headless:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

    if pygame is None:                                # pragma: no cover
        print("ERROR: pygame not installed")
        return 1

    # --- the audio process -------------------------------------------------
    # It is spawned before the App because the library, the engines, the rack
    # and the tape all come out of it. A missing binary is fatal and says so:
    # there is nothing to fall back to any more (stage N1).
    from app.native_audio import BOXES, NativeAudio, NativeUnavailable

    # An attached host's is read back from it below (#629).
    offline = args.offline or args.headless or args.no_audio
    try:
        if args.attach:
            native = NativeAudio(attach=True, socket_path=args.attach,
                                 sample_rate=SAMPLE_RATE,
                                 client="arbutus-emulator")
        else:
            # --library is the host's too: its browsers come from there (#1075)
            native = NativeAudio(offline=offline, sample_rate=SAMPLE_RATE,
                                 libraries=([BOXES, args.library]
                                            if args.library else None))
    except NativeUnavailable as exc:
        print("ERROR: %s" % exc)
        return 1
    if args.attach:
        # Someone else's host: whether its clock needs pulling is its to say,
        # and its `state` says it (#629). A flag here could only get it wrong:
        # an offline host left unpulled freezes the tape, and a live one
        # refuses every `render`.
        offline = bool(native.state.get("offline", args.offline))
        native.offline = offline
    print("audio: %s (%s%s)" % (native.firmware,
                                "attached, " if args.attach else "",
                                "offline" if offline else "live"))

    app_kw = {}
    if args.state_dir:
        os.makedirs(args.state_dir, exist_ok=True)
        app_kw["state_dir"] = args.state_dir
    if args.library:
        from app.native_library import NativeLibrary
        app_kw["library"] = NativeLibrary(native, root=args.library)
    app = App(fresh=args.fresh, native=native, **app_kw)
    project_file.ensure_open(app)        # D97: a project is always open
    if args.appearance:
        app.set_appearance(args.appearance, persist=False)

    share = None
    if args.share:
        # `arbutus_os` is the repo's package, a level above this folder.
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if repo not in sys.path:
            sys.path.insert(0, repo)
        from arbutus_os.screenshare import Publisher
        share = Publisher(args.share)

    # --- live look editor -------------------------------------------------
    # Serves the controls only; this window is the preview. Every draw site
    # reads palette/font at call time, so an edit lands on the next frame.
    if args.edit:
        from tools.edit_server import serve
        try:
            _, url = serve(args.edit_port)
            print(f"look editor: {url}")
            if not args.headless:
                import webbrowser
                webbrowser.open(url)
        except OSError as exc:
            print(f"look editor not started: {exc}")

    # --- audio -----------------------------------------------------------
    # The audio process owns the device, so the driver here has nothing to do
    # (live) or pulls `render` as the clock (offline).
    from drivers.native_backend import NativeLiveAudio, NativeOfflineAudio

    audio = NativeOfflineAudio(native) if offline else NativeLiveAudio(native)
    audio.initialize(SAMPLE_RATE, 2, None)
    audio.start()
    headless_audio = offline           # the offline clock needs pulling

    # --- mic -------------------------------------------------------------
    # Only a factory: the device is opened when the INPUT page is first
    # visited, so macOS asks for microphone permission then and not at boot.
    if not (headless_audio or args.no_mic):
        from drivers.desktop.sounddevice_input import SounddeviceInput

        def _mic():
            dev = args.mic_device
            if dev is not None and dev.isdigit():
                dev = int(dev)
            return SounddeviceInput(device=dev)

        app.mic_driver_factory = _mic

    # --- MIDI ------------------------------------------------------------
    midi = None
    if not args.no_midi:
        from drivers.desktop.midi_in import MidiIn
        midi = MidiIn(app, device_override=args.midi)
        # FILE › MIDI draws the port it is listening to, and there is exactly
        # one (the port is opened once at boot; hot-plug is out of scope)
        app.midi_in = midi

    # Video subsystem only. pygame.init() also brings up SDL audio, which on
    # macOS deadlocks against the CoreAudio stream sounddevice already owns
    # (symptom: bouncing dock icon, window never appears).
    pygame.display.init()
    from chrome.window import Window
    clock = pygame.time.Clock()
    fb = np.zeros((240, 320, 3), dtype=np.uint8)

    chrome = None
    win = Window("Arbutus — ArbutusOS", (640, 480), hidden=True)
    if args.zoom_only:
        scale = max(1, int(args.scale or 3))
        zoom_scale = scale + 1
        zoomed = False
        win.resize_px((320 * scale * win.ratio, 240 * scale * win.ratio))
        screen = win.surface
        surface = pygame.Surface((320, 240))
    else:
        from chrome import ChromeView
        # The dummy video driver reports a fictional 1024x768, so only a real
        # display gets to cap the window. Sizes here are points; chrome works
        # in device pixels, so the cap is scaled up by the window's ratio.
        info = pygame.display.Info()
        avail = (None if args.headless
                 else (info.current_w or 2560, info.current_h or 1440))
        cap = (None if avail is None
               else (int(avail[0] * win.ratio), int(avail[1] * win.ratio)))
        chrome = ChromeView(max_width=args.width or (avail or (2560,))[0],
                            photo_scale=args.scale,
                            offset=args.offset, max_size=cap,
                            device=win.ratio)
        chrome.zoomed = args.zoom
        win.resize_px(chrome.size)
        screen = win.surface
        chrome.optimize(screen)
    win.show()

    cmd_map, note_map, enc_map = build_keymap()
    pushes = EncoderPushes(app)
    mouse_key = None                     # hotspot currently held by the mouse
    enc_drag = None                      # (encoder idx, start y, steps sent)
    running = True
    frames = 0
    t_end = time.monotonic() + args.exit_after if args.exit_after > 0 else None

    def press(name, down):
        if name in ENCODER_NAMES:
            return
        app.on_key(name, down)
        if chrome is not None:
            chrome.press(name) if down else chrome.release(name)

    def panic(announce=True):
        """All notes off + forget every held control. `\\`, and on focus loss.

        An unfocused window never delivers the KEYUP, so alt-tabbing mid-note
        used to leave the note sounding with no key to release it.
        """
        nonlocal mouse_key, enc_drag
        app.panic()
        mouse_key = None
        enc_drag = None
        if chrome is not None:
            chrome.clear()
        if announce:
            print("panic: all notes off")

    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type in (pygame.KEYDOWN, pygame.KEYUP):
                down = ev.type == pygame.KEYDOWN
                if ev.key == pygame.K_ESCAPE and down:
                    running = False
                elif ev.key in (pygame.K_x, pygame.K_BACKSLASH) and down:
                    panic()          # `x` is not a key on the device
                elif ev.key == pygame.K_m and down and chrome is not None:
                    # M magnifies the whole chrome, not just the LCD: one scale
                    # drives photo, hotspots, outlines and framebuffer together
                    if chrome.set_fb_multiple(2 if chrome.fb_multiple == 1 else 1):
                        win.resize_px(chrome.size)
                        screen = win.surface
                        chrome.optimize(screen)
                        print("chrome: LCD at %dx (%dx%d), window %dx%d"
                              % (chrome.fb_multiple, 320 * chrome.fb_multiple,
                                 240 * chrome.fb_multiple, *chrome.size))
                elif ev.key == pygame.K_v and down:
                    # V: light ↔ dark, as the phone would (#334); not a key
                    # on the device, and remembered like the phone's is
                    app.set_appearance("dark" if app.appearance == "light"
                                       else "light")
                    print("screen: %s" % app.appearance)
                elif ev.key == pygame.K_z and down:
                    if chrome is not None:
                        chrome.toggle_zoom()
                    else:
                        zoomed = not zoomed
                        s = zoom_scale if zoomed else scale
                        win.resize_px((320 * s * win.ratio, 240 * s * win.ratio))
                        screen = win.surface
                elif pushes.key(ev.key, down, ev.mod):
                    pass                     # it was ENCn's push (#818)
                elif ev.key in cmd_map:
                    press(cmd_map[ev.key].value, down)
                elif ev.key in note_map:
                    press(note_map[ev.key], down)
                elif ev.key in enc_map and down:
                    idx, delta = enc_map[ev.key]
                    app.on_encoder(idx, delta)
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                if chrome is not None:
                    name = chrome.hit(win.to_px(ev.pos))
                    if name in ENCODER_NAMES:
                        # click-and-drag: up = +, down = −, ENC_DRAG_PX per step
                        enc_drag = (ENCODER_NAMES.index(name), ev.pos[1], 0)
                        chrome.press(name)
                    elif name:
                        mouse_key = name
                        press(name, True)
            elif ev.type == pygame.MOUSEMOTION and enc_drag is not None:
                idx, y0, sent = enc_drag
                steps = drag_steps(y0, ev.pos[1])
                if steps != sent:
                    app.on_encoder(idx, int(steps - sent))
                    enc_drag = (idx, y0, steps)
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                if enc_drag is not None:
                    chrome.release(ENCODER_NAMES[enc_drag[0]])
                    if enc_drag[2] == 0:
                        # a click that turned nothing is the encoder's push
                        pushes.click(enc_drag[0])
                    enc_drag = None
                if mouse_key is not None:
                    press(mouse_key, False)
                    mouse_key = None
            elif ev.type == pygame.WINDOWFOCUSLOST:
                panic(announce=False)
            elif ev.type == pygame.MOUSEWHEEL:
                if ev.y:
                    idx = 0
                    if chrome is not None:
                        name = chrome.hit(win.to_px(pygame.mouse.get_pos()))
                        if name in ENCODER_NAMES:
                            idx = ENCODER_NAMES.index(name)
                            chrome.press(name)
                            chrome.release(name)
                    app.on_encoder(idx, int(np.sign(ev.y)))

        if midi is not None:
            midi.poll()
        app.tick()
        if headless_audio:
            audio.pull(1)                 # keep the clock moving with no device

        if share is not None:
            share.poll()                  # before drawing: the request this frame answers
        fb[:] = 0
        app.render(fb)
        if share is not None:
            share.pump(fb)
        app.feed.frame_drawn(app, fb)     # #853: `frame` for a watching phone
        if chrome is not None:
            chrome.draw(screen, fb)
        else:
            pygame.surfarray.blit_array(surface, np.transpose(fb, (1, 0, 2)))
            pygame.transform.scale(surface, screen.get_size(), screen)
        win.flip()
        clock.tick(60)

        frames += 1
        if args.frames and frames >= args.frames:
            running = False
        if t_end is not None and time.monotonic() >= t_end:
            running = False

    if args.screenshot:
        path = os.path.abspath(args.screenshot)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pygame.image.save(screen, path)
        print(f"screenshot: {path}")

    # closing the window is this instrument's power-off (§ 7.4, D97)
    project_file.power_off(app)
    app.flush()
    if midi is not None:
        midi.close()
    app.close_mic()
    native.close()
    try:
        audio.stop()
        audio.cleanup()
    except Exception as exc:                          # pragma: no cover
        print(f"audio shutdown: {exc}")
    pygame.display.quit()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
