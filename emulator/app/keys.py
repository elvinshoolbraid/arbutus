"""Every physical control on the Arbutus-1 (control-inventory.md).

52 keys + 4 encoders. Key enum members are stable string ids so scripts,
keymap.json and MIDI config can name them.
"""

from enum import Enum


class Key(str, Enum):
    # --- page keys (9) ---
    SYNTH = "SYNTH"
    DRUM = "DRUM"
    TRACK = "TRACK"
    MIXER = "MIXER"
    SEQ = "SEQ"
    INPUT = "INPUT"
    HELP = "HELP"
    FILE = "FILE"          # USB/FILE
    TEMPO = "TEMPO"        # also a left-block key; one physical key

    # --- subpage / track keys ---
    K1 = "1"
    K2 = "2"
    K3 = "3"
    K4 = "4"

    # --- arrows ---
    LEFT = "LEFT"          # ‹
    RIGHT = "RIGHT"        # ›

    # --- slot / tape keys (green + brown legends on the same 6 keys) ---
    A = "A"                # A / IN
    B = "B"                # B / OUT
    C = "C"                # C / LOOP
    X = "X"                # X / REV
    Y = "Y"                # Y / SPEED
    Z = "Z"                # Z / FILTER

    # --- left block ---
    SLICE = "SLICE"        # SLICE / TAPE
    PLAY = "PLAY"
    STOP = "STOP"
    REC = "REC"
    LIFT = "LIFT"
    DROP = "DROP"
    SHIFT = "SHIFT"


PAGE_KEYS = (Key.SYNTH, Key.DRUM, Key.TRACK, Key.MIXER, Key.SEQ,
             Key.INPUT, Key.HELP, Key.FILE, Key.TEMPO)

NUMBER_KEYS = (Key.K1, Key.K2, Key.K3, Key.K4)
SLOT_KEYS = (Key.A, Key.B, Key.C, Key.X, Key.Y, Key.Z)
TAPE_KEY_NAMES = {Key.A: "IN", Key.B: "OUT", Key.C: "LOOP",
                  Key.X: "REV", Key.Y: "SPEED", Key.Z: "FILTER"}

# --- chromatic keys: 24, F..E, two octaves ---
NUM_NOTES = 24
NOTE_BASE_MIDI = 53  # F3


def note_key(n: int) -> str:
    """NOTE_0 .. NOTE_23 — the chromatic keys as event ids."""
    return f"NOTE_{n}"


NOTE_KEYS = tuple(note_key(n) for n in range(NUM_NOTES))


def is_note(key) -> bool:
    return isinstance(key, str) and key.startswith("NOTE_")


def note_index(key: str) -> int:
    return int(key.split("_", 1)[1])


class Encoder(int, Enum):
    ENC1 = 0
    ENC2 = 1
    ENC3 = 2
    ENC4 = 3


ENCODERS = (Encoder.ENC1, Encoder.ENC2, Encoder.ENC3, Encoder.ENC4)

#: The four encoder pushes (#818, D98): matrix keys `COL11 x ROW0-3` on P3
#: (io-map § Key matrix), named as `arbutus_os/drivers/matrix_p3.json` names
#: them, so the device's key reader and the emulator send the same id. Not in
#: `Key`: they have no hotspot of their own (a click on an encoder is its push,
#: `main.py`), and `ALL_KEYS` is the 52 switches.
ENC_PUSH_KEYS = tuple("ENC%d_PUSH" % (n + 1) for n in range(4))


def push_index(key) -> int:
    """`ENC2_PUSH` -> 1; -1 for any other key."""
    key = str(getattr(key, "value", key))
    return ENC_PUSH_KEYS.index(key) if key in ENC_PUSH_KEYS else -1

ALL_KEYS = tuple(k.value for k in Key) + NOTE_KEYS
