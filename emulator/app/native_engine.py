"""Engines that live in the audio process, wearing the face the pages speak.

`Instrument` and the SYNTH/DRUM pages know an engine by a handful of methods —
`get_parameters`, `set_parameter`, `note_on/off`, `set_amp_adsr`, `process`.
`NativeEngine` implements exactly those and turns each one into a protocol
message, so **no page needed a native branch to play a note**.

The 8 slots are built from the descriptor in the host's `state` document
(`engine.json`'s params snapshot, §3.8) and from nothing else: id, name, type,
curve, min, max, def, step, unit, labels. `parameter-framework.md` says the UI
knows nothing about synthesis, and here it literally cannot — the engine is in
another process.
"""

import os
from typing import Callable, List, Optional

import numpy as np

from .params import Param, StepType

#: continuous slots with no useful `step` (and every log slot) get one
#: hundred detents across the whole sweep. parameter-framework.md asks a
#: LOGARITHMIC control to "feel even"; an even *normalised* step is that.
DETENTS = 100


# ---------------------------------------------------------------------------
# descriptor -> UI slot
# ---------------------------------------------------------------------------

def denorm(desc: dict, n: float) -> float:
    lo, hi = float(desc.get("min", 0.0)), float(desc.get("max", 1.0))
    n = min(1.0, max(0.0, float(n)))
    if desc.get("curve") == "log" and lo > 0.0 and hi > 0.0:
        return lo * (hi / lo) ** n
    return lo + n * (hi - lo)


def norm(desc: dict, v: float) -> float:
    lo, hi = float(desc.get("min", 0.0)), float(desc.get("max", 1.0))
    if hi == lo:
        return 0.0
    if desc.get("curve") == "log" and lo > 0.0 and hi > 0.0 and v > 0.0:
        import math
        return min(1.0, max(0.0, math.log(v / lo) / math.log(hi / lo)))
    return min(1.0, max(0.0, (v - lo) / (hi - lo)))


def _fmt(desc: dict) -> Callable[[float], str]:
    """Print a normalised slot in the engine's own units."""
    unit = desc.get("unit") or ""

    def fmt(n: float) -> str:
        v = denorm(desc, n)
        if abs(v) >= 1000.0:
            s = "%.1fk" % (v / 1000.0)
        elif abs(v) >= 100.0:
            s = "%.0f" % v
        else:
            s = "%.2f" % v
        return s + unit

    return fmt


def _labelled(base: Callable[[float], str], display: Callable[[], dict],
              pid: str) -> Callable[[float], str]:
    """Prefer the engine's own readout (ABI 2 `format_param`, §15.11).

    `display` is keyed by parameter id and holds the string the box wants
    drawn for the value **currently in `params`** — `1/8`, `C3`, `2.20 s`.
    It is read fresh at draw time rather than baked in, because the host
    recomputes it for every `state`; both per-pad and ordinary writes refetch
    state synchronously, so the label is right by the next frame.
    """
    def fmt(v: float) -> str:
        s = (display() or {}).get(pid)
        return s if s else base(v)
    return fmt


def slot_param(desc: dict, value: float,
               send: Optional[Callable[[float], None]],
               display: Optional[Callable[[], dict]] = None) -> Param:
    """One `arb_param_desc` as a UI slot (§3.5).

    ENUM and INTEGER live in *unit* space — one entry or one unit per detent,
    a value label and no bar. CONTINUOUS lives in **normalised** space, so the
    position bar is right for a log curve and the value that goes back down
    the socket needs no conversion.
    """
    name = (desc.get("name") or desc.get("id") or "?").upper()
    kind = desc.get("type", "continuous")
    labels = desc.get("labels") or None
    pid = desc.get("id") or ""

    if kind in ("enum", "integer"):
        lo, hi = float(desc.get("min", 0.0)), float(desc.get("max", 1.0))
        unit = desc.get("unit") or ""
        p = Param(name, lo, hi, denorm(desc, value), StepType.DISCRETE,
                  step_size=float(desc.get("step") or 1.0),
                  unit=unit, display_format="{:.0f}",
                  choices=list(labels) if labels else None)
        if display is not None and not labels:
            # an INTEGER the box names itself — the sampler's ROOT, which is a
            # MIDI number in the descriptor and `C3` on the screen
            p.formatter = _labelled(lambda v: "%.0f%s" % (v, unit), display, pid)
        if send is not None:
            p.on_change = lambda v, d=desc: send(norm(d, v))
        return p

    span = float(desc.get("max", 1.0)) - float(desc.get("min", 0.0))
    step = float(desc.get("step") or 0.0)
    nstep = (step / span) if (span and step and desc.get("curve") != "log") \
        else 1.0 / DETENTS
    p = Param(name, 0.0, 1.0, float(value), StepType.LINEAR,
              step_size=max(1e-4, nstep))
    base = _fmt(desc)
    p.formatter = base if display is None else _labelled(base, display, pid)
    if send is not None:
        p.on_change = send
    return p


def slots_from(desc_params: List[dict], values: dict,
               send: Optional[Callable[[int, float], None]],
               display: Optional[Callable[[], dict]] = None) -> List[Param]:
    """The ≤8 slots of one loaded engine, in descriptor order."""
    out = []
    for i, d in enumerate(desc_params[:8]):
        v = values.get(d.get("id"), norm(d, float(d.get("def", 0.0))))

        def make(idx):
            return None if send is None else (lambda n, k=idx: send(k, float(n)))

        out.append(slot_param(d, float(v), make(i), display))
    return out


# ---------------------------------------------------------------------------
# the engine proxy
# ---------------------------------------------------------------------------

class NativeEngine:
    """An engine in the audio process, seen through `ISynthEngine`'s hole.

    It renders nothing here: `process()` returns silence because the sound is
    already coming out of the host's device. Everything else is a message.
    """

    def __init__(self, native, inst_id: str, engine_id: str, name: str = "",
                 slot: str = "engine", sample_rate: int = 48000):
        self.native = native
        self.inst_id = inst_id
        self.engine_id = engine_id
        self.slot = slot
        self.sample_rate = sample_rate
        self._name = name or engine_id.split("/")[-1].upper()
        self._adsr = (0.012, 0.2, 0.8, 0.34)
        self._mode = "poly"
        #: the ENV SHIFT row (DDR-052), cached for the same reason the ADSR is:
        #: every `env` message carries the whole block
        self._policy = (0.0, 12.0, 2.0)          # glide s, detune ct, bend st

    # -- identity ---------------------------------------------------------
    def get_name(self) -> str:
        return self._name

    def get_category(self) -> str:
        return "synth"

    # -- the host's view of this slot -------------------------------------
    @property
    def record(self) -> dict:
        return self.native.slot(self.inst_id, self.slot)

    @property
    def desc_params(self) -> List[dict]:
        return (self.record.get("desc") or {}).get("params") or []

    @property
    def honours_env(self) -> bool:
        return bool((self.record.get("desc") or {}).get("honours_env", True))

    @property
    def display(self) -> dict:
        """What `format_param` wants drawn, per parameter id (§15.11)."""
        return self.record.get("display") or {}

    # -- parameters -------------------------------------------------------
    def ui_slots(self) -> List[Param]:
        """`pages/synth.py` calls this instead of adapting `get_parameters()`."""
        return slots_from(self.desc_params, self.record.get("params") or {},
                          self.set_parameter, lambda: self.display)

    def get_parameters(self):
        return self.ui_slots()

    def set_parameter(self, idx: int, normalised: float):
        self.native.set_param(self.inst_id, self.slot, int(idx),
                              float(normalised))

    # -- per-pad parameters (§15.3) ---------------------------------------
    @property
    def kit(self) -> dict:
        """The §7.3 pad map as the host reports it — every pad `{file, params}`
        with every `per_note` param filled in. That document *is* the save."""
        return self.native.instrument(self.inst_id).get("kit") or {}

    def pad_record(self, pad: int) -> dict:
        rec = (self.kit.get("pads") or {}).get(str(int(pad)))
        if isinstance(rec, str):
            return {"file": rec, "params": {}}     # the v1 shape still reads
        return rec or {}

    def pad_file(self, pad: int) -> str:
        return self.pad_record(pad).get("file") or ""

    def set_note_parameter(self, pad: int, idx: int, normalised: float):
        self.native.set_note_param(self.inst_id, int(pad), int(idx),
                                   float(normalised))

    def copy_pad(self, src: int, dst: int) -> dict:
        return self.native.copy_pad(self.inst_id, int(src), int(dst))

    def set_pad_file(self, pad: int, path: str) -> bool:
        """Put a WAV on one pad (DDR-030's drop-on-DRUM).

        §15.3 has `set_note_param` and `copy_pad` and no per-pad *file*
        message, because the kit document **is** the save: the edit is that
        document with one `file` changed, sent back as `load_kit`. Absolute
        paths survive the host's `dir` resolution, so the rest of the kit keeps
        reading the files it was authored with.
        """
        kit = self.kit
        if not kit:
            return False
        doc = dict(kit)
        pads = dict(doc.get("pads") or {})
        rec = self.pad_record(pad)
        pads[str(int(pad))] = {"file": os.path.abspath(path),
                               "params": dict(rec.get("params") or {})}
        doc["pads"] = pads
        return self.native.load_kit(self.inst_id, doc).get("type") != "error"

    def pad_slots(self, pad: int) -> List[Param]:
        """One pad's 8 slots.

        A `per_note` parameter reads from the kit document and writes with
        `set_note_param`; anything else on the same engine is an ordinary
        slot. `arbutus/drum` flags all eight, but the split is the
        descriptor's to make, not this page's (§15.3).
        """
        values = self.record.get("params") or {}
        pad_values = self.pad_record(pad).get("params") or {}
        out = []
        for i, d in enumerate(self.desc_params[:8]):
            pid = d.get("id")
            default = norm(d, float(d.get("def", 0.0)))
            if d.get("per_note"):
                v = pad_values.get(pid, default)
                send = (lambda n, k=i, p=int(pad):
                        self.set_note_parameter(p, k, float(n)))
            else:
                v = values.get(pid, default)
                send = lambda n, k=i: self.set_parameter(k, float(n))
            out.append(slot_param(d, float(v), send, lambda: self.display))
        return out

    # -- waveforms (§15.6) ------------------------------------------------
    def pad_waveform(self, pad: int, bins: int = 128) -> Optional[dict]:
        return self.native.waveform(bins=bins, instrument=self.inst_id,
                                    pad=int(pad))

    def asset_waveform(self, bins: int = 128) -> Optional[dict]:
        return self.native.waveform(bins=bins, instrument=self.inst_id,
                                    slot=self.slot)

    # -- notes ------------------------------------------------------------
    def note_on(self, note: int, velocity: float = 1.0):
        self.native.note(note, True, velocity)

    def note_off(self, note: int):
        self.native.note(note, False, 0.0)

    def all_notes_off(self):
        self.native.panic()

    # -- the ENV page -----------------------------------------------------
    def set_amp_adsr(self, attack, decay, sustain, release):
        self._adsr = (attack, decay, sustain, release)
        self._push_env()

    def set_env_mode(self, mode: str):
        self._mode = mode
        self._push_env()

    def set_voice_policy(self, glide, detune, bend_range):
        """ENV SHIFT row: GLIDE · DETUNE · BEND (DDR-052)."""
        self._policy = (glide, detune, bend_range)
        self._push_env()

    def _push_env(self):
        glide, detune, bend_range = self._policy
        self.native.env(self.inst_id, *self._adsr, self._mode,
                        glide=glide, detune=detune, bend_range=bend_range)

    def bend(self, value: float):
        """One message per edge; the host owns the ramp (§15.10)."""
        self.native.bend(self.inst_id, value)

    # -- audio ------------------------------------------------------------
    def process(self, num_samples: int) -> np.ndarray:
        return np.zeros(int(num_samples), dtype=np.float32)

    # -- the sampler's waveform -------------------------------------------
    # The samples are in the host's address space and stay there: a page asks
    # for `waveform` and draws the peaks that come back (§15.6). Nothing in
    # `emulator/` opens an audio file.
    @property
    def has_asset(self) -> bool:
        return bool(self.record.get("assets"))

    def _unit(self, pid: str, default: float) -> float:
        for d in self.desc_params:
            if d.get("id") == pid:
                return float((self.record.get("params") or {}).get(
                    pid, norm(d, float(d.get("def", default)))))
        return default

    @property
    def start(self) -> float:
        return self._unit("start", 0.0)

    @property
    def end(self) -> float:
        return self._unit("end", 1.0)

    @property
    def loop_in(self) -> float:
        return self._unit("loop_in", 1.0)

    @property
    def loop_out(self) -> float:
        return self._unit("loop_out", 1.0)

    @property
    def has_loop(self) -> bool:
        """LOOP-IN = LOOP-OUT is no loop (§15.11); default 1/1, so a sampler
        that was never looped draws no loop markers at all."""
        return abs(self.loop_out - self.loop_in) > 1e-6

    def markers(self) -> List[tuple]:
        """(position 0..1, is_loop) for the sampler's four, in draw order."""
        out = [(self.start, False), (self.end, False)]
        if self.has_loop:
            out += [(self.loop_in, True), (self.loop_out, True)]
        return out
