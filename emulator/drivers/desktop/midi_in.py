"""MIDI input: chromatic keys + 4 relative encoders (emulator-spec §3).

Config lives in ``emulator/midi.toml``. Everything here degrades gracefully:
no ``mido``, no matching device, or a broken config never stops the emulator.
"""

import os
import sys
from typing import Dict, NamedTuple, Optional, Tuple

if sys.version_info >= (3, 11):
    import tomllib
else:                                                 # pragma: no cover
    tomllib = None

try:
    import mido
except ImportError:                                   # pragma: no cover
    mido = None

from app.keys import NUM_NOTES, note_key
from app.settings import shape_velocity

EMU_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(EMU_DIR, "midi.toml")

#: relative-encoder encodings understood by :func:`decode_relative`
ENCODINGS = ("twos-complement", "offset-64", "signmag")

DEFAULTS = {
    "device": "",
    "channel": -1,
    "note_offset": 53,        # MIDI note that lands on chromatic key 0 (F)
    "velocity": True,
    "cc": {"enc1": 16, "enc2": 17, "enc3": 18, "enc4": 19,
           "encoding": "twos-complement"},
}


def decode_relative(value: int, encoding: str) -> int:
    """CC data byte -> signed encoder delta.

    * ``twos-complement`` — 1..63 = +1..+63, 65..127 = -63..-1 (64 = 0)
    * ``offset-64``       — 64 is centre, so delta = value - 64
    * ``signmag``         — bit 6 is the sign, bits 0..5 the magnitude
    """
    v = int(value) & 0x7F
    if encoding == "offset-64":
        return v - 64
    if encoding == "signmag":
        mag = v & 0x3F
        return mag if v & 0x40 else -mag
    if encoding == "twos-complement":
        return v - 128 if v >= 64 else v
    raise ValueError(f"unknown relative encoding: {encoding!r}")


def load_config(path: str = CONFIG_PATH) -> dict:
    """Parse midi.toml over the defaults. Missing/broken file = defaults."""
    cfg = {k: (dict(v) if isinstance(v, dict) else v)
           for k, v in DEFAULTS.items()}
    if tomllib is None or not os.path.exists(path):
        return cfg
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except Exception as exc:                          # pragma: no cover
        print(f"midi.toml: {exc} — using defaults")
        return cfg
    for k, v in raw.items():
        if k == "cc" and isinstance(v, dict):
            cfg["cc"].update(v)
        else:
            cfg[k] = v
    enc = cfg["cc"].get("encoding", "twos-complement")
    if enc not in ENCODINGS:
        print(f"midi.toml: unknown encoding {enc!r} — using twos-complement")
        cfg["cc"]["encoding"] = "twos-complement"
    return cfg


def cc_to_encoder(cfg: dict) -> Dict[int, int]:
    """{cc number: encoder index 0..3} from the config."""
    return {int(cfg["cc"][f"enc{i + 1}"]): i for i in range(4)
            if f"enc{i + 1}" in cfg["cc"]}


class MidiSnapshot(NamedTuple):
    """Everything turning a MIDI message into a note reads, read once.

    #128: on the device the rtmidi reader thread sounds a note itself rather
    than waiting for the 60 Hz drain (`arbutus_os/midi.py`, `device-os-spec.md`
    § 8.2), and it may not read `Settings` while the tick is writing it. This
    is the whole of what that decode needs — two settings a player can move
    (`IN CH`, `MIDI VEL CURVE`) and two `midi.toml` constants — published by
    the tick once a frame, immutable, so the reader thread sees last frame's
    or this frame's and never half of either.
    """

    in_ch: int = -1                 # -1 = omni
    note_offset: int = 53           # MIDI note that lands on chromatic key 0
    use_velocity: bool = True
    vel_curve: str = "LINEAR"


def decode_note(msg, snap: MidiSnapshot) -> Optional[Tuple[str, bool, float]]:
    """One message -> `(key name, pressed, velocity)`, or None if it is not a
    note for us.

    Pure: no `self`, no `Settings`, nothing but the message and the snapshot,
    which is what lets the reader thread call it. `handle()` below calls it
    too — on the main thread with a snapshot taken on the spot — so the two
    paths cannot decode the same message differently.

    None covers everything that is not a note (CC, bend, clock, transport) and
    a note on a channel `IN CH` is filtering out.
    """
    kind = getattr(msg, "type", "")
    if kind not in ("note_on", "note_off"):
        return None
    if snap.in_ch >= 0 and getattr(msg, "channel", 0) != snap.in_ch:
        return None
    # A note outside the 24 physical keys still plays: `note_key` names a
    # chromatic index and `App.on_key` adds it to NOTE_BASE_MIDI, negative
    # index and all. That is the behaviour a controller with 49 keys has had
    # since phase 3 and it is not changed here.
    key = note_key(int(msg.note) - snap.note_offset)
    if kind == "note_on" and getattr(msg, "velocity", 0):
        raw = (msg.velocity / 127.0) if snap.use_velocity else 1.0
        return key, True, shape_velocity(raw, snap.vel_curve)
    return key, False, 0.0


class MidiIn:
    """Opens the first input whose name contains ``device`` and pumps events.

    Hot-plug is out of scope: the port is opened once at boot.

    ``open_port=False`` builds the decoder without a port of its own, for a
    transport that opens its own (``arbutus_os/midi.py``, the device's reader
    thread — every MIDI-class input, hot-plugged, on the ALSA sequencer).
    """

    def __init__(self, app, config: Optional[dict] = None,
                 device_override: str = "", config_path: str = CONFIG_PATH,
                 open_port: bool = True):
        self.app = app
        self.cfg = config if config is not None else load_config(config_path)
        if device_override:
            self.cfg["device"] = device_override
        self.cc_map = cc_to_encoder(self.cfg)
        self.encoding = self.cfg["cc"].get("encoding", "twos-complement")
        self.channel = int(self.cfg.get("channel", -1))
        self.note_offset = int(self.cfg.get("note_offset", 53))
        self.use_velocity = bool(self.cfg.get("velocity", True))
        self.port = None
        self.port_name = None
        self._seed_settings()
        if open_port:
            self._open()

    # -- FILE › MIDI owns the channel now (stage J2) -----------------------
    def _settings(self):
        return getattr(self.app, "settings", None)

    def _seed_settings(self):
        """`midi.toml`'s `channel` is the boot default, not the live value.

        FILE › MIDI's `IN CH` is the control a player turns, and it is stored
        in `<library>/settings.json`. The toml key stays what it always was —
        what a fresh install starts on — so it is copied across exactly once,
        when there is no settings document yet.
        """
        st = self._settings()
        if st is None or os.path.exists(st.path) or self.channel == -1:
            return
        st.set("midi", "in_ch", self.channel)

    @property
    def in_channel(self) -> int:
        """-1 = omni. The page's value if there is one, else the toml's."""
        st = self._settings()
        return self.channel if st is None else st.midi_channel

    def snapshot(self) -> MidiSnapshot:
        """What `decode_note` needs, read off the live state. Main thread.

        The device's transport publishes one of these per frame for its reader
        thread (#128); `handle()` takes one per message, which is the same
        thing when the caller already is the main thread.
        """
        st = self._settings()
        return MidiSnapshot(
            in_ch=self.in_channel,
            note_offset=self.note_offset,
            use_velocity=self.use_velocity,
            vel_curve="LINEAR" if st is None else st.vel_curve)

    def note_of(self, msg, snap: Optional[MidiSnapshot] = None):
        """`decode_note` against this decoder's own state. See it for the shape."""
        return decode_note(msg, self.snapshot() if snap is None else snap)

    # -- device -----------------------------------------------------------
    def _open(self):
        if mido is None:
            print("MIDI: mido not installed — continuing without MIDI")
            return
        try:
            names = mido.get_input_names()
        except Exception as exc:                      # pragma: no cover
            print(f"MIDI: no backend ({exc}) — continuing without MIDI")
            return
        want = str(self.cfg.get("device", "")).lower()
        match = next((n for n in names if want in n.lower()), None)
        if match is None:
            print(f"MIDI: no input matching {want!r} — continuing without MIDI")
            return
        try:
            self.port = mido.open_input(match)
            self.port_name = match
            print(f"MIDI in: {match}")
        except Exception as exc:                      # pragma: no cover
            print(f"MIDI: cannot open {match!r} ({exc})")
            self.port = None

    def close(self):
        if self.port is not None:
            try:
                self.port.close()
            except Exception:                         # pragma: no cover
                pass
            self.port = None

    # -- events -----------------------------------------------------------
    def _wanted_channel(self, msg) -> bool:
        ch = self.in_channel
        return ch < 0 or getattr(msg, "channel", 0) == ch

    def handle(self, msg, now: Optional[float] = None):
        """Route one mido message into the app. Public so tests can drive it.

        ``now`` is when the message *arrived*, when the caller knows and that
        is not when it is being dispatched. Only the clock cares, and it cares
        a lot: a transport that queues (the device's reader thread) would
        otherwise hand the 24-ppqn meter the frame boundary instead of the
        wire, and intervals that are all whole frames are not a tempo. ``None`` means "now", which is what polling in the tick means.
        """
        # System realtime carries no channel, so the clock is read before the
        # channel filter — a master's clock is not on a channel at all.
        clock = getattr(self.app, "midi_clock", None)
        if clock is not None:
            if msg.type == "clock":
                clock.pulse(now)
                return
            if msg.type in ("start", "continue", "stop"):
                clock.transport(msg.type, now)
                return
        if not self._wanted_channel(msg):
            return
        note = decode_note(msg, self.snapshot())
        if note is not None:
            key, pressed, vel = note
            # #853: the live stream tells a MIDI keyboard's note from the
            # instrument's own key (`ui_feed.UiFeed.key`)
            feed = getattr(self.app, "feed", None)
            if feed is not None:
                feed.midi = int(msg.note)
            try:
                if pressed:
                    self.app.on_key(key, True, vel)
                else:
                    self.app.on_key(key, False)
            finally:
                if feed is not None:
                    feed.midi = None
        elif msg.type == "control_change" and msg.control in self.cc_map:
            delta = decode_relative(msg.value, self.encoding)
            if delta:
                self.app.on_encoder(self.cc_map[msg.control], int(delta))
        elif msg.type == "pitchwheel":
            # DDR-052: bend comes from MIDI or from SHIFT+‹ ›. The wire is
            # -8192..8191; the host ramps the position over GLIDE and scales
            # it by BEND, so the active instrument is sent -1..1 and no more.
            value = max(-1.0, min(1.0, float(getattr(msg, "pitch", 0)) / 8192.0))
            self.app.instrument.set_bend(value)

    def poll(self):
        if self.port is None:
            return
        for msg in self.port.iter_pending():
            self.handle(msg)


def in_key_range(index: int) -> bool:
    """True if a chromatic index lands on one of the 24 physical keys."""
    return 0 <= index < NUM_NOTES
