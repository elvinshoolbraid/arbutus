"""The UI's side of an instrument: which engine, which item, ENV, the octave.

This used to be the audio graph — engine → LFO → effect chain, rendered per
block in numpy. All of that is the audio process's now (`audio/`), and what is
left is the state the SYNTH, DRUM and SEQ pages read and write:

  * the engine proxy and where it came from in the library (the header line),
  * the octave the `‹ ›` keys move, and the map of which note each key
    actually sounded, so a release after an octave change still lands,
  * the ENV page's ADSR and MODE, forwarded to the engine (which forwards it
    to the host's `env`; `PolyHost` applies it **per voice** —
  `emulator/notes/as-built.md` § Polyphony has the bug report that rule comes from).

Nothing here touches audio.
"""

from typing import Dict, Optional

ENV_MODES = ("poly", "mono", "legato", "unison")


class AmpEnv:
    """The ENV page's ADSR values, forwarded to the engine on every write.

    Kept as an attribute bag so `setattr(inst.env, "attack", v)` (pages/synth)
    still works; every write pushes the whole set down.
    """

    _FIELDS = ("attack", "decay", "sustain", "release")

    def __init__(self, instrument, attack: float, decay: float,
                 sustain: float, release: float):
        object.__setattr__(self, "_inst", instrument)
        self.attack = attack
        self.decay = decay
        self.sustain = sustain
        self.release = release

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name in self._FIELDS:
            self._inst.push_amp_env()

    def as_tuple(self):
        return (self.attack, self.decay, self.sustain, self.release)


class Instrument:
    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self.engine = None             # a NativeEngine, once one is loaded
        self.engine_folder = None      # LibraryFolder
        self.item = ""                 # patch / sample / kit name
        self.env = AmpEnv(self, attack=0.012, decay=0.2, sustain=0.8,
                          release=0.34)
        self.env_mode = 0              # poly / mono / legato / unison
        # the rest of the ENV SHIFT row (DDR-052). GLIDE is portamento in
        # mono/legato *and* the bend ramp, DETUNE is the unison spread
        # outermost to outermost, BEND is the bend range. Units and defaults
        # are the host's (`audio/host/PROTOCOL.md` § env): s, cents, semitones.
        self.env_glide = 0.0
        self.env_detune = 12.0
        self.env_bend_range = 2.0
        self.octave = 0
        # the note the engine last heard. DRUM's per-pad pages edit the
        # last-hit key (§15.3), and it is the *transposed* note that names the
        # pad, because that is the one the engine received.
        self.last_note: Optional[int] = None
        # key as played -> note actually handed to the engine. Keyed by the
        # played note, not the transposed one, so a note released after the
        # octave moved still releases the voice it started (it used to leave
        # the voice sounding, a drone under everything played afterwards).
        self._held: Dict[int, int] = {}

    # -- engine swapping --------------------------------------------------
    def set_engine(self, engine, folder=None, item=""):
        if self.engine is not None:
            self.engine.all_notes_off()
        self.engine = engine
        self.engine_folder = folder
        self.item = item
        self._held.clear()
        self.push_amp_env()
        self.push_voice_policy()

    def push_amp_env(self):
        """Hand the ENV page's ADSR to the engine, which applies it per voice."""
        if self.engine is not None:
            self.engine.set_amp_adsr(*self.env.as_tuple())

    def push_voice_policy(self):
        """The ENV SHIFT row's three (DDR-052). Same trip as the ADSR: they are
        fields of the host's `env` block, not engine parameters."""
        setter = getattr(self.engine, "set_voice_policy", None)
        if setter is not None:
            setter(self.env_glide, self.env_detune, self.env_bend_range)

    def set_env_mode(self, idx: int, name: str = ""):
        """ENV MODE: poly / mono / legato / unison, implemented by `PolyHost`."""
        self.env_mode = int(idx)
        setter = getattr(self.engine, "set_env_mode", None)
        if setter is not None and name:
            setter(name)

    def set_bend(self, value: float):
        """SHIFT+‹ › held on SYNTH/DRUM. The host ramps to `value` over GLIDE
        and back on release, so the UI sends the edges, never a slide."""
        setter = getattr(self.engine, "bend", None)
        if setter is not None:
            setter(value)

    @property
    def engine_name(self) -> str:
        if self.engine_folder is not None:
            return self.engine_folder.name
        return self.engine.get_name() if self.engine else "—"

    @property
    def item_name(self) -> str:
        return self.item or "—"

    # -- notes ------------------------------------------------------------
    def note_on(self, midi_note: int, velocity: float = 1.0):
        note = midi_note + 12 * self.octave
        old = self._held.get(midi_note)
        if old is not None and old != note and self.engine:
            self.engine.note_off(old)          # same key, new transposition
        if self.engine:
            self.engine.note_on(note, velocity)
        self._held[midi_note] = note
        self.last_note = note

    def note_off(self, midi_note: int):
        note = self._held.pop(midi_note, midi_note + 12 * self.octave)
        if self.engine:
            self.engine.note_off(note)

    def all_notes_off(self):
        if self.engine:
            self.engine.all_notes_off()
        self._held.clear()
        # panic lets go of every held control, and a bend is one: without this
        # an alt-tab mid-bend leaves the instrument a tone sharp with no key
        # left to release it
        self.set_bend(0.0)
