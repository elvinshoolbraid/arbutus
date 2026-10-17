"""USB/FILE — `PROJECT · MIDI · USB · SYSTEM` on 1-4 (screen-sketches § FILE).

Stage J1 built **PROJECT** and the subpage frame itself. **SAVE is gone
since D97 (#817)**: the instrument is always saved (`app/project_file.py`),
and SHIFT+ENC3's save-as-copy went with it. **Since D98 (#818)** PROJECT
reads `NEW › · LOAD › · RENAME › · BOUNCE ›` and nothing is armed: none of the
four can lose work any more, so a turn right acts (below). Stage J2 builds the
other three, and its job is as much about saying what this box *cannot* do as
what it can:

* **MIDI** — `IN CH · OUT CH · CLOCK IN · CLOCK OUT`. IN CH and CLOCK IN are
  real: the emulator has had a MIDI input since phase 3, and `CLOCK IN` is the
  source TEMPO's SYNC `midi` has been waiting for since H2. There is **no MIDI
  output**, so OUT CH and CLOCK OUT are named, dim and locked (DDR-053's
  shape) with the reason on screen. Sending a clock out of a 60 fps UI thread
  would be a musical lie, not a feature.
* **USB** — `MODE · CHARGE · PAIR`. Switching MODE to STORAGE **force-saves
  first** (DDR-021), which is the part of the rule that matters and works
  here. A MacBook cannot be a USB device and its charger is not this
  program's, so those are locked and say why.

  **`PAIR` is live since #97, where `net.sock` answers.** It opens and closes
  § 9.5's pairing window and the 4-digit code the phone asks for is drawn at
  the foot of the page — the one thing on it drawn LARGE, because it is read
  across a room and typed into a phone. On a laptop there is no socket, so the
  cell is J2's locked one and the row still says `BLE PAIRING IS THE DEVICE
  OS`.
* **SYSTEM** — the scrolling settings list, ENC1 selecting and ENC2 changing,
  with RESET behind the 1-2-3-4 confirm **twice** (DDR-064). `WIFI` and
  `FORGET PHONES` are live since #97 where `net.sock` answers: `WIFI` draws
  § 9.2's `OFF · JOINING · <SSID> <ip> · FAILED` and `FORGET PHONES` is
  editable, behind **one** round of 1-2-3-4 — the second round is DDR-064's
  and belongs to the one gesture that throws away more than a session, and a
  forgotten phone is re-paired by standing next to the instrument.

Settings live in `<library>/settings.json` (`app/settings.py`), not in the
project: a project is a song and a setting is the device.

**PROJECT acts on the turn** (D98, `control-inventory.md` § Menus). Until
D97 the four were an arm-then-confirm pair (`1 2 3 4 TO CONFIRM`), because
they were the gestures that could throw a session away; with the instrument
always saved none of them can, and the lock stays only where something is
still lost: SYSTEM › RESET (twice, DDR-064), FORGET PHONES and the `.fw` line.
NEW and RENAME open the **name menu** (`app/name_menu.py`); LOAD opens the
two-pane project browser (folder = date), which highlights and does not load
— a browser that loaded a project per detent would re-read eight tape media
on every click. Either commits on the push of the encoder just turned or on
**the red key**, the one cell of the strip drawn red, under the cell that was
turned; any other page key cancels. BOUNCE starts at once.
"""

import os
from typing import Dict, List, Optional

from app import box_trust
from app import keys as K
from app import project_file as PF
from app.menu import MenuPage, StaticSource
from app.name_menu import NameMenu
from app.page import Page
from app.params import Param, StepType, pad_slots
from ui import draw, palette
from ui.font import LARGE, SMALL

PROJECT, MIDI, USB, SYSTEM = 0, 1, 2, 3
SUBPAGES = ("PROJECT", "MIDI", "USB", "SYSTEM")

#: the actions, by encoder (screen-sketches § FILE › PROJECT, D98). ENC3 was
#: SAVE, and SHIFT+ENC3 save-as-copy, until D97 (#817).
ACTIONS = ("NEW", "LOAD", "RENAME", "BOUNCE")
#: D98 marks a turn-to-act cell `▸`. The font has no `▸` and `ui/font.py` is
#: not this page's to edit (a hard rule): `›`, the nearest glyph it draws,
#: until Elfy draws one (run's call, 2026-10-13; `ui-aesthetic.md` § 4).
TURN_MARK = "\u203a"
#: what a turn right does, in the cell's value line
ACT_VALUES = {"NEW": "PICK NAME", "LOAD": "PROJECTS", "RENAME": "PICK NAME",
              "BOUNCE": "TO WAV"}
#: the LOAD browser's red key, under ENC2 that opened it
OPEN = "OPEN"

#: the keys of the confirm, in the order they have to arrive
CONFIRM_KEYS = ("1", "2", "3", "4")
CONFIRM_STRIP = "1 2 3 4 TO CONFIRM"

#: SYSTEM › RESET is the one action that asks twice (DDR-064)
RESET = "RESET"
CONFIRM_AGAIN = "1 2 3 4 AGAIN"

#: MIDI IN channel cell: index 0 is omni, 1..16 are the channels
CHANNEL_LABELS = ["OMNI"] + ["%d" % n for n in range(1, 17)]
OUT_CHANNEL_LABELS = ["%d" % n for n in range(1, 17)]
OFF_ON = ["OFF", "ON"]

#: FILE › USB `MODE (storage / midi)`, plus the off state it starts in
USB_MODES = ["OFF", "STORAGE", "MIDI"]

#: `device-os-spec.md` § 11.1, verbatim: what the page says while the computer
#: holds `/data`. `app.usb_exported` is set by `arbutus_os/main.py` off
#: `usb.sock` (D6, #27) and is **never** true on the emulator, which has no
#: socket — so nothing below changes a pixel off the device.
USB_EXPORTED_LINE = "USB STORAGE — EJECT ON THE COMPUTER, THEN TURN MODE OFF"

#: Which of the four ACTIONS reach the library partition: all of them. `NEW`
#: was left out once as "resets the session in RAM", but since #297 it clears
#: the working media in `/data/.state` and points the tape back there, which
#: while exported is the bare mount point (#617). SYSTEM › RESET is refused
#: for the same reason (`_arm`); RENAME moves the open project's folder.
DISK_ACTIONS = ("NEW", "LOAD", "RENAME", "BOUNCE")

#: what this build honestly cannot do, said once each and shown on the page
NO_MIDI_OUT = "NO MIDI OUT ON THIS HOST"
NO_MIDI_IN = "NO MIDI DEVICE"
NO_GADGET = "NO USB GADGET ON THIS HOST"
NO_CHARGE = "ON \u00b7 NOT SWITCHABLE HERE"

#: FILE › USB's STORAGE and CHARGE rows once `usb.sock` / `power.sock`
#: answer (#524; § 11, § 7.2's `CHG_STAT` row). With no socket the two
#: sentences above stay, § 4.3's rule again. A refusal `arbutus-usb` already
#: knows (`storage_blocked`: `NO /data PARTITION`) is drawn as it comes: § 11.1's
#: refusals are player-facing sentences.
STORAGE_EXPORTED = "EXPORTED TO THE COMPUTER"
STORAGE_READY = "READY"
CHARGING = "CHARGING"
NOT_CHARGING = "NOT CHARGING"
#: `charge_stat: unknown` — a board with no `CHG_STAT` line (the bench CM4)
CHARGE_UNKNOWN = "NO CHARGE LINE"
NO_BLE = "BLE PAIRING IS THE DEVICE OS"
NO_WIFI = "NOT BUILT (DEVICE OS)"

#: FILE's three device-OS cells, once `net.sock` answers (D3/D4's UI half,
#: #97). All four sentences above stay exactly as they are on a laptop: with
#: no socket `network` is `None` and every branch below is unreachable, which
#: is § 4.3's "a cell unlocks exactly when the socket behind it answers".
#: A device whose `arbutus-net` is up but whose adapter is not (every CM4 on
#: the bench, E7) says this rather than advertising nothing in silence.
NO_ADAPTER = "NO BLUETOOTH ON THIS DEVICE"
#: `pair_state` from `arbutus_os/net/ble.py`, word for word. The page cannot
#: import it — `emulator/` never imports `arbutus_os/` — so the seam is a test
#: (`arbutus_os/tests/test_net_pump.py`), the same arrangement `FW_CONFIRM`
#: has. Only this one state needs naming: the rest of the cell reads the two
#: booleans `pairing` and `paired`.
PAIR_CODE_OK = "code_ok"
#: what the PAIR row says at each step of § 9.5's flow
PAIR_OFF = "OFF"
PAIR_PAIRED = "PAIRED"
PAIR_OPENING = "OPENING"
PAIR_ACCEPTED = "CODE ACCEPTED"
#: and the line at the foot of FILE › USB while the window is open. The code
#: is the one thing on this page a player reads from across a room and types
#: into a phone, so it is the only thing on it drawn LARGE.
PAIR_LINE = "PAIR CODE %s"
#: § 9.2's four states, as the WIFI row draws them. `up` is not in the table
#: because it is the SSID and the address, which are not a fixed string.
WIFI_LABELS = {"off": "OFF", "joining": "JOINING", "failed": "FAILED"}
#: FILE › SYSTEM › FORGET PHONES — the row, the armed action, and what it says
FORGET = "FORGET PHONES"
FORGET_ARMED = "FORGET PHONES: EVERY PAIRED APP STOPS WORKING"
FORGET_SENT = "FORGETTING PHONES"
FORGET_PAIRED = "ONE PHONE PAIRED — 1 2 3 4 UNPAIRS IT"
FORGET_NONE = "NO PHONE PAIRED"
#: armed with the door shut — `arbutus_os/main.py` never wired `net_command`
NO_NET_SOCKET = "NO ARBUTUS-NET ON THIS HOST"
NO_BACKLIGHT = "STORED — NO BACKLIGHT HERE"
#: what the same row says on the instrument, where there *is* a backlight:
#: `arbutus_os/drivers/uart_input.py` turns the percentage into the ATmega's
#: `L n` (`device-os-spec.md` § 6.2). The device sets `app.backlight`; nothing
#: else in the UI knows or cares which host it is on.
BACKLIGHT_NOTE = "SCREEN BRIGHTNESS"
NO_IMU = "KEY VEL HIDDEN: NO IMU FITTED"
#: and what the row itself says when a sensor *is* fitted. The setting exists
#: in the spec (screen-sketches § FILE › SYSTEM, DDR-048: FIXED or a velocity
#: inferred from the case jolt at note-on) and the inference does not exist in
#: the host, so the row is named, dim and locked with the reason — the same
#: thing `WIFI`, `CHARGE` and `FORGET PHONES` do, rather than an option that
#: can be set to MOTION and change nothing (#117).
KEY_VEL_UNBUILT = "MOTION VELOCITY NOT BUILT"
#: `POWER-OFF`'s note where `power.sock` says `halt_only` (#446, #659): a
#: header Pi on P3 (and the bench boards) has nothing that restarts it after a
#: power-off, so the device refuses the idle one and the row reads `OFF`,
#: dim and locked, rather than offering minutes it ignores. The setting
#: itself is left as stored: it is the library's, and a board that can come
#: back honours it again.
POWER_OFF_NOTE = "IDLE TIMEOUT, HONOURED BY THE DEVICE"
HALT_ONLY_NOTE = "NO IDLE OFF: NOTHING HERE RESTARTS IT"
#: what the `.fw` line says with no `fw.sock` behind it — every emulator run,
#: and a device whose `arbutus-fw` is not up (`device-os-spec.md` § 4.3: a cell
#: unlocks exactly when the socket behind it answers).
FW_LINE = ".FW / .ARBOX: DEVICE OS APPLIES IT"

#: FILE › USB's ENC4, the `.fw` line's action (D5b, #103). One cell, because at
#: most one of the three is ever outstanding, and each is behind J1's 1-2-3-4:
#: `device-os-spec.md` § 10.4 asks for it on RESTART and § 10.1 gives the
#: unsigned confirmation "the DDR-064 shape".
FW_UPDATE = "UPDATE"
#: a `.fw` is unpacked and the pointer is flipped — `reboot` on `fw.sock`,
#: because the UI is unprivileged and cannot reboot the machine itself
FW_RESTART = "RESTART"
#: a file in the inbox is unsigned, or signed by a key this device does not
#: know (DDR-073) — `apply {file, confirm: true}`
FW_INSTALL = "INSTALL"
#: a rollback happened and § 10.5 says its line stands "until dismissed"
FW_DISMISS = "DISMISS"
FW_ACTIONS = (FW_RESTART, FW_INSTALL, FW_DISMISS)
#: `arbutus_os/fw/installer.py`'s `CONFIRM`, word for word. It is repeated and
#: not imported because `emulator/` never imports `arbutus_os/` — the UI is the
#: same code on a laptop, where there is no device package at all — and
#: `arbutus_os/tests/test_fw_pump.py` asserts the two strings still agree.
FW_CONFIRM = "UNSIGNED UPDATE — INSTALL ANYWAY?"
#: `fw.sock` is answering and there is nothing to do
FW_NONE = "NO UPDATE WAITING"
#: armed with the door shut — `arbutus_os/main.py` never wired `fw_command`
FW_NO_SOCKET = "NO ARBUTUS-FW ON THIS HOST"

#: SYSTEM's rows, keyed rather than indexed, because the list is not a fixed
#: length: `KEY VEL` is there only with a motion sensor fitted — "hidden
#: without an IMU" (screen-sketches § FILE › SYSTEM, device-os-spec § 6.5) —
#: and the host now says whether there is one (#116).
SYS_VEL = "vel"
SYS_KEY_VEL = "key_vel"
SYS_POWER = "power"
SYS_DATE = "date"
SYS_TAPE = "tape"
SYS_BRIGHT = "bright"
SYS_WIFI = "wifi"
SYS_PHONES = "phones"
SYS_RESET = "reset"

BRIGHT_MIN, BRIGHT_MAX, BRIGHT_STEP = 10, 100, 10

#: FILE › SYSTEM's tape read-out (C1 step 7, `tape-streaming-design.md` § 3.7:
#: "`state` carries `tape.pool` … for the FILE › SYSTEM page and the bench").
#: **Not in the sketch's list** — § FILE › SYSTEM names eight rows and this is a
#: ninth — and it is here rather than on a new page because it is one line of
#: read-out about the instrument, which is what this list already is (`DATE /
#: TIME` and `WIFI` are read-outs too). A row, not a meter: the number that
#: matters to a player is `HARD DRIVE SLOW`, and that is a flag in the strip.
TAPE_ROW = "TAPE BUFFER"
TAPE_ALL_RAM_VALUE = "ALL IN RAM"
TAPE_ALL_RAM = "THE WHOLE TAPE IS HELD IN MEMORY"
TAPE_OK = "NOTHING HAS BEEN READ LATE"
#: How many blocks played silence because the card was behind — the count the
#: `HARD DRIVE SLOW` flag is raised on, kept where it can be read afterwards.
TAPE_LATE = "%d BLOCKS PLAYED SILENCE"
TAPE_WRITING = "%d RANGES WAITING FOR THE CARD"


class _ProjectSource(StaticSource):
    """The project list, and where each row lives on disk.

    A project is a **folder** (`projects/<date>/<name>/` + a WAV per used
    track), so DDR-055's LIFT has to move a directory — the same shape a drum
    kit already is (`item_kind: "folder"`). `shutil.move` does not care which
    it is, so all this has to supply is the path.
    """

    def __init__(self, table, root: str):
        super().__init__(table)
        self.root = root

    def _date(self, folder_idx: int) -> Optional[str]:
        folders = self.folders()
        if not folders:
            return None
        return folders[max(0, min(len(folders) - 1, folder_idx))]

    def item_path(self, folder_idx: int, item_idx: int) -> Optional[str]:
        date = self._date(folder_idx)
        if date is None:
            return None
        items = self.table.get(date) or []
        if not 0 <= item_idx < len(items):
            return None
        return os.path.join(self.root, date, items[item_idx])

    def folder_path(self, folder_idx: int) -> Optional[str]:
        date = self._date(folder_idx)
        return None if date is None else os.path.join(self.root, date)


class _ProjectBrowser(MenuPage):
    """The two-pane list LOAD puts up (folder = date, the names on the right).

    D98's menu: it highlights and waits; the push of the encoder just turned
    (ENC2's, which opened it, until another is turned) or the red `OPEN` key
    loads; any other page key closes it. LIFT and DROP are still the file
    manager's (DDR-055), as on every browser.
    """

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        #: the encoder a push must come from to load: ENC2 opened the list
        self.last_turned = 1

    def strip_labels(self):
        return [None, OPEN, None, None]

    def red_strip_cell(self):
        return 1

    def tickers(self):
        return self.parent.tickers()

    def on_encoder(self, idx: int, delta: int, shift: bool) -> bool:
        used = super().on_encoder(idx, delta, shift)
        if used:
            self.last_turned = idx
        return used

    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        """A push loads or cancels; a file op on this list changes what LOAD
        would load — re-read it."""
        if K.push_index(key) >= 0:
            if pressed:
                if K.push_index(key) == self.last_turned:
                    self.parent.open_picked()
                else:
                    self.parent.close_menus()
            return True
        used = super().on_key(key, pressed, shift)
        if used:
            self.parent.refresh_listing()
        elif pressed:
            self.parent.close_menus()     # any other page key cancels (D98)
            return True
        return used


#: #920: while the audio process renders a bounce. Three dots: the font
#: draws no `…` (`app/file_jobs.py`'s note).
BOUNCING_LINE = "BOUNCING..."


def _fmt_time(frames: int, sample_rate: int) -> str:
    s = max(0.0, float(frames) / max(1, sample_rate))
    return "%d:%02d" % (int(s) // 60, int(s) % 60)


def _fmt_bytes(n: int) -> str:
    for unit, div in (("G", 1 << 30), ("M", 1 << 20), ("K", 1 << 10)):
        if n >= div:
            return "%.1f%s" % (n / float(div), unit)
    return "%dB" % n


class FilePage(Page):
    key_id = "FILE"
    title = "USB/FILE"
    category = "utility"
    subpage_names = SUBPAGES

    def __init__(self, app):
        super().__init__(app)
        self.state.subpage = PROJECT
        #: SHIFT slot 7 — "MASTER FX (on/off, for BOUNCE)"
        self.master_fx = False
        #: the armed action (RESET, FORGET PHONES, the `.fw` line) and how many of
        #: 1-2-3-4 have landed
        self._armed: Optional[str] = None
        self._confirm = 0
        #: how many more 1-2-3-4 runs this action wants. RESET asks twice
        #: (DDR-064); everything else asks once.
        self._rounds = 1
        self._browser: Optional[MenuPage] = None
        #: NEW's or RENAME's name menu while it is up (#818)
        self._names: Optional[NameMenu] = None
        self._listing: Dict[str, List[str]] = {}
        #: the last WAV BOUNCE wrote — the path handed to the library
        self.bounce_path = ""
        #: SYSTEM's list cursor (ENC1) — the page keeps its place (DDR-028)
        self.system_row = SYS_VEL
        #: the project the last USB mode change flushed (DDR-021, D97)
        self.usb_saved = ""
        #: what `pair {on}` last asked for, until `net.sock` agrees. `None`
        #: means nothing is in flight. The cell shows this rather than the
        #: mirror while it is set — `UsbPump.sent`'s shape, one page up — so a
        #: detent lights the cell at once instead of on the service's answer,
        #: and a second detent in the same direction does not mint a second
        #: code under the player's fingers (`begin()` makes a fresh one every
        #: call).
        self._pair_sent: Optional[bool] = None
        self._build_params()

    def on_enter(self):
        """Arriving never finds a gun cocked from the last visit."""
        self.disarm()

    # -- params -------------------------------------------------------------
    def _build_params(self):
        # display only: `on_encoder` acts on the turn before a Param sees it
        self._actions: List[Optional[Param]] = [
            Param("%s %s" % (name, TURN_MARK), 0, 0, 0, StepType.DISCRETE,
                  choices=[ACT_VALUES[name]])
            for name in ACTIONS]
        self.p_master_fx = Param("MASTER FX", 0, 1, 0, StepType.DISCRETE,
                                 choices=["OFF", "ON"])
        self.p_master_fx.on_change = lambda v: setattr(self, "master_fx",
                                                       bool(int(v)))

        # --- MIDI (stage J2) ---------------------------------------------
        self.p_in_ch = Param("IN CH", 0, len(CHANNEL_LABELS) - 1, 0,
                             StepType.DISCRETE, choices=list(CHANNEL_LABELS))
        self.p_in_ch.on_change = self._set_in_channel
        # there is no MIDI output on this host, so the cell is named and dead
        # rather than a number that goes nowhere (DDR-053's shape)
        self.p_out_ch = Param("OUT CH", 0, len(OUT_CHANNEL_LABELS) - 1, 0,
                              StepType.DISCRETE,
                              choices=list(OUT_CHANNEL_LABELS))
        self.p_out_ch.locked = self.p_out_ch.dimmed = True
        self.p_clock_in = Param("CLOCK IN", 0, 1, 0, StepType.DISCRETE,
                                choices=list(OFF_ON))
        self.p_clock_in.on_change = self._set_clock_in
        self.p_clock_out = Param("CLOCK OUT", 0, 1, 0, StepType.DISCRETE,
                                 choices=list(OFF_ON))
        self.p_clock_out.locked = self.p_clock_out.dimmed = True

        # --- USB (stage J2) ----------------------------------------------
        self.p_usb_mode = Param("MODE", 0, len(USB_MODES) - 1, 0,
                                StepType.DISCRETE, choices=list(USB_MODES))
        self.p_usb_mode.on_change = self._set_usb_mode
        self.p_charge = Param("CHARGE", 0, 1, 1, StepType.DISCRETE,
                              choices=list(OFF_ON))
        self.p_charge.locked = self.p_charge.dimmed = True
        self.p_pair = Param("PAIR", 0, 1, 0, StepType.DISCRETE,
                            choices=list(OFF_ON))
        # locked until `net.sock` answers, and recomputed every frame in
        # `_usb_slots` — `locked` is what makes `turn()` a no-op, so on a
        # laptop `on_change` below is unreachable
        self.p_pair.locked = self.p_pair.dimmed = True
        self.p_pair.on_change = self._set_pair
        # The `.fw` line's one control (#103). No `choices`, because the cell
        # has to name *which* of the three it would run, and `choices` wins
        # over `formatter` in `Param.format_value`.
        self.p_fw = Param(FW_UPDATE, 0, 1, 0, StepType.DISCRETE)
        self.p_fw.on_change = self._fw_cell_turned

        # --- SYSTEM (stage J2) -------------------------------------------
        # Two knobs for a list of any length, which is the sketch's own
        # design: "a scrolling list (ENC1 selects, ENC2 changes)".
        # the range is rebuilt every frame in `_system_slots`: the list is
        # not a fixed length any more (#116)
        self.p_select = Param("SELECT", 0, 6, 0, StepType.DISCRETE)
        self.p_value = Param("VALUE", 0, 1, 0, StepType.DISCRETE)

    # -- the settings document's two doors on this page --------------------
    def _set_in_channel(self, value: float):
        """Cell index 0 is omni, 1..16 are channels 1..16 on the wire 0..15."""
        idx = int(value)
        self.app.settings.set("midi", "in_ch", -1 if idx == 0 else idx - 1)

    def _set_clock_in(self, value: float):
        on = bool(int(value))
        self.app.settings.set("midi", "clock_in", on)
        self.app.midi_clock.set_enabled(on)

    def _set_pair(self, value: float):
        """ENC3 on FILE › USB: open or close § 9.5's pairing window.

        There is no setting behind this and nothing to persist: the radio's
        state is the service's, the cell mirrors it, and `pair {on}` is the
        whole of what the UI does. Unlike the four ACTIONS there is no
        1-2-3-4 in front of it — opening a three-minute window that a second
        turn closes loses nothing, and a player standing at the instrument
        with a phone in their hand is the only one who can turn it.
        """
        on = value >= 0.5
        if on == (self._pair_sent if self._pair_sent is not None
                  else bool((self.network or {}).get("pairing"))):
            return
        send = getattr(self.app, "net_command", None)
        if send is None:
            self.app.flash(NO_NET_SOCKET, "NO NET")
            return
        self._pair_sent = on
        send({"type": "pair", "on": on})
        self.app.flash("PAIRING OPEN" if on else "PAIRING CLOSED", "PAIR")

    def _set_usb_mode(self, value: float):
        """DDR-021: entering USB mode saves first. No prompt.

        Since D97 (#817) the instrument is always saved, so the save is a
        flush: the transport stops and the open project's document, the clip
        list and the slots go to the card now rather than on the autosave's
        clock, before the computer is handed `/data`.
        """
        mode = USB_MODES[int(value)]
        previous = str(self.app.settings.get("usb", "mode"))
        self.app.settings.set("usb", "mode", mode)
        if mode == "STORAGE" and previous != "STORAGE":
            PF.halt(self.app)
            self.app.flush()
            folder = PF.open_folder(self.app)
            # #1123: a card that refused the flush (full, read-only) said
            # SAVED here; `not_saved` is the refusal's own record (#954)
            if folder is None or PF.not_saved(self.app):
                # the takes are on the card already; only the document is not
                self.app.flash("NOT SAVED BEFORE USB", "NOT SAVED")
                return
            self.usb_saved = os.path.basename(folder)
            self.app.flash("SAVED %s BEFORE USB" % self.usb_saved,
                           self.usb_saved)

    def slots(self) -> List[Optional[Param]]:
        if self.state.subpage == MIDI:
            return self._midi_slots()
        if self.state.subpage == USB:
            return self._usb_slots()
        if self.state.subpage == SYSTEM:
            return self._system_slots()
        exported = self.usb_exported
        for name, p in zip(ACTIONS, self._actions):
            # § 11.1: "LOAD/SAVE/BOUNCE and the browsers' file ops are locked
            # while exported, with the reason on the page". The library is
            # unmounted — a write would land in an empty mount point.
            p.locked = p.dimmed = exported and name in DISK_ACTIONS
        self.p_master_fx.value = 1.0 if self.master_fx else 0.0
        return pad_slots(list(self._actions)
                         + [None, None, None, self.p_master_fx])

    def _midi_slots(self) -> List[Optional[Param]]:
        """The settings document is the value; the cells are read back from it
        every frame, the way every host-owned cell in this instrument is."""
        st = self.app.settings
        ch = int(st.get("midi", "in_ch"))
        self.p_in_ch.value = float(0 if ch < 0 else ch + 1)
        self.p_out_ch.value = float(max(0, int(st.get("midi", "out_ch")) - 1))
        self.p_clock_in.value = 1.0 if st.clock_in else 0.0
        self.p_clock_out.value = 1.0 if st.get("midi", "clock_out") else 0.0
        return pad_slots([self.p_in_ch, self.p_out_ch, self.p_clock_in,
                          self.p_clock_out])

    def _usb_slots(self) -> List[Optional[Param]]:
        st = self.app.settings
        mode = str(st.get("usb", "mode"))
        self.p_usb_mode.value = float(USB_MODES.index(mode)
                                      if mode in USB_MODES else 0)
        # #524: where `power.sock` answers the cell is the charger's state
        # (`CHG_STAT`); it stays locked, the charger is not switchable
        pw = self.power
        self.p_charge.value = 1.0 if (st.get("usb", "charge") if pw is None
                                      else pw.get("charging")) else 0.0
        net = self.network
        pairing = bool((net or {}).get("pairing"))
        if self._pair_sent is not None and self._pair_sent == pairing:
            self._pair_sent = None              # the service caught up
        self.p_pair.value = 1.0 if (self._pair_sent
                                    if self._pair_sent is not None
                                    else pairing) else 0.0
        # § 4.3, and then § 9.5: a device whose `arbutus-net` is up but whose
        # adapter is not can no more pair than a laptop can, so `ble: false`
        # locks the cell too and the row says which of the two it is.
        self.p_pair.locked = self.p_pair.dimmed = (net is None
                                                   or not net.get("ble"))
        # § 4.3's rule for the `.fw` line, plus § 11.1's: the inbox *is* the
        # library root, so while the computer holds it there is nothing to
        # install and nothing safe to reboot out from under.
        action = self._fw_action()
        self.p_fw.value = 1.0 if self._armed in FW_ACTIONS else 0.0
        self.p_fw.locked = self.p_fw.dimmed = (action is None
                                               or self.usb_exported)
        self.p_fw.formatter = (lambda v, a=action:
                               "ARM" if v >= 0.5 else (a or "—"))
        return pad_slots([self.p_usb_mode, self.p_charge, self.p_pair,
                          self.p_fw])

    def _system_slots(self) -> List[Optional[Param]]:
        rows = self._system_rows()
        n = self._row_index(rows)
        self.p_select.max_val = float(len(rows) - 1)
        self.p_select.value = float(n)
        self.p_select.formatter = lambda v, r=rows: r[int(v)][1]
        row = rows[n]
        self.p_value.formatter = lambda v, t=row[2]: t
        self.p_value.locked = self.p_value.dimmed = not row[3]
        return pad_slots([self.p_select, self.p_value, None, None])

    def _row_index(self, rows) -> int:
        """Where the cursor is in a list whose length is not fixed.

        A key that is not in the list -- the sensor went away while the cursor
        sat on `KEY VEL` -- lands on the first row rather than nowhere.
        """
        for i, r in enumerate(rows):
            if r[0] == self.system_row:
                return i
        self.system_row = rows[0][0]
        return 0

    @property
    def motion_sensor(self) -> bool:
        """Is a motion sensor fitted and answering (device-os-spec § 6.5)?

        The host says so on `hello` and in every state document
        (`PROTOCOL.md` § Handshake, #116). False on a laptop, on the IO board,
        on a P3 with nothing fitted, and on one whose sensor has stopped
        answering -- which § 6.5 makes one case deliberately.
        """
        return bool(getattr(self.app.native, "motion_sensor", False))

    @property
    def network(self) -> Optional[dict]:
        """`net.sock`'s status document, or `None` where there is no socket.

        `arbutus_os/pumps.py`'s `NetPump` writes it once a frame off
        `DeviceServices`, with § 9.5's pairing push merged over the poll; on
        the emulator nothing ever writes it, so every cell that reads it stays
        exactly as stage J2 drew it. Same shape as `firmware` above, and for
        the same reason — the flag belongs to the device runtime and a laptop
        has no `arbutus-net` to set it from.
        """
        doc = getattr(self.app, "network", None)
        return doc if isinstance(doc, dict) else None

    # -- FILE's three device-OS cells (D3/D4's UI half, #97) ----------------
    def _pair_row(self):
        """The PAIR row's `(value, colour)` — § 9.5's flow, one line of it."""
        net = self.network
        if net is None:
            return NO_BLE, palette.DIM
        if not net.get("ble"):
            return NO_ADAPTER, palette.DIM
        if net.get("pairing"):
            if str(net.get("pair_state") or "") == PAIR_CODE_OK:
                # the phone typed the code right; the three writes are still
                # in flight, and repeating the code now would invite a retype
                return PAIR_ACCEPTED, palette.GREEN
            return str(net.get("code") or PAIR_OPENING), palette.GREEN
        if net.get("paired"):
            return PAIR_PAIRED, palette.INK
        return PAIR_OFF, palette.DIM

    def _pair_code(self) -> str:
        """The four digits while the window is open, or `""`.

        Read separately from `_pair_row` because it is the one thing on this
        page drawn LARGE, and because it must vanish the instant the window
        does — a stale code on the glass is a code a player types into a phone
        that will refuse it.
        """
        net = self.network or {}
        if not net.get("pairing") or not net.get("ble"):
            return ""
        if str(net.get("pair_state") or "") == PAIR_CODE_OK:
            return ""
        return str(net.get("code") or "")

    def _wifi_row(self) -> str:
        """§ 9.2's `OFF · JOINING · <SSID> <ip> · FAILED`, off `status`."""
        net = self.network
        if net is None:
            return NO_WIFI
        state = str(net.get("wifi") or "off")
        if state == "up":
            ssid = str(net.get("ssid") or "").strip()
            ip = str(net.get("ip") or "").strip()
            return " ".join(x for x in (ssid or "UP", ip) if x)
        return WIFI_LABELS.get(state, state.upper())

    def _forget_phones(self):
        """The confirm landed on FILE › SYSTEM › FORGET PHONES (§ 9.5).

        One message and nothing else: deleting the token, cancelling the
        advertisement, forgetting every saved network and restarting
        `arbutus-audio` so the old token stops working are all the service's
        (§ 9.4.1). Whether that last one happened comes back on the reply and
        `NetPump` is what says so.
        """
        send = getattr(self.app, "net_command", None)
        if send is None:
            self.app.flash(NO_NET_SOCKET, "NO NET")
            return
        send({"type": "forget_phones"})
        self.app.flash(FORGET_SENT, "FORGET")

    # -- SYSTEM's list ------------------------------------------------------
    def _system_rows(self):
        """`(key, label, value, editable, note)` per row, rebuilt every frame.

        Nothing is cached because three of them are not settings at all —
        the clock is the platform's and WIFI and FORGET PHONES are the device
        OS's, which is not built (`companion-app-spec.md`). They are shown so
        the list is the list the sketch asks for and so the instrument says
        what it does not have, rather than quietly being shorter.

        The length is not fixed: `KEY VEL` is in the list only when the host
        reports a motion sensor (§ 6.5, #116). Keyed and not indexed for that
        reason — an index would mean a different row on a P3 than on a laptop.
        """
        st = self.app.settings
        power = int(st.get("system", "power_off"))
        fitted = self.motion_sensor
        net = self.network
        rows = [
            (SYS_VEL, "MIDI VEL CURVE", str(st.get("system", "vel_curve")),
             True, "" if fitted else NO_IMU),
        ]
        if fitted:
            rows.append((SYS_KEY_VEL, "KEY VEL", "FIXED", False,
                         KEY_VEL_UNBUILT))
        if (self.power or {}).get("halt_only"):
            power_row = (SYS_POWER, "POWER-OFF", "OFF", False, HALT_ONLY_NOTE)
        else:
            power_row = (SYS_POWER, "POWER-OFF",
                         "OFF" if power == 0 else "%d MIN" % power,
                         True, POWER_OFF_NOTE)
        rows += [
            power_row,
            (SYS_DATE, "DATE / TIME", st.now_string(), False, "HOST CLOCK"),
            (SYS_TAPE, TAPE_ROW) + self._tape_cells(),
            (SYS_BRIGHT, "BRIGHTNESS",
             "%d%%" % int(st.get("system", "brightness")), True,
             BACKLIGHT_NOTE if getattr(self.app, "backlight", None)
             else NO_BACKLIGHT),
            (SYS_WIFI, "WIFI", self._wifi_row(), False, ""),
            (SYS_PHONES, FORGET,
             "TURN RIGHT" if net is not None else "\u2014",
             net is not None,
             (FORGET_PAIRED if net.get("paired") else FORGET_NONE)
             if net is not None else NO_BLE),
            (SYS_RESET, RESET, "TURN RIGHT", True, "1 2 3 4, THEN AGAIN"),
        ]
        return rows

    @property
    def tape_pool(self) -> Optional[dict]:
        """`state.tape.pool` — the RAM window over the tape (C1 step 7).

        Always present on a host that has it, with `ranges` 0 where the tape
        is the whole-track RAM tape it has always been — which is every host
        until the medium moves onto storage. `None` is an older host, and
        reads the same as that.
        """
        doc = getattr(self.app.native, "state", None) or {}
        pool = (doc.get("tape") or {}).get("pool")
        return pool if isinstance(pool, dict) else None

    def _tape_cells(self):
        """`(value, editable, note)` for the TAPE BUFFER row."""
        pool = self.tape_pool or {}
        ranges = int(pool.get("ranges") or 0)
        late = int(pool.get("late_total") or 0)
        queued = int(pool.get("queued") or 0)
        if not ranges:
            return (TAPE_ALL_RAM_VALUE, False, TAPE_ALL_RAM)
        note = TAPE_LATE % late if late else TAPE_OK
        if queued:
            note = TAPE_WRITING % queued
        return ("%d/%d" % (int(pool.get("resident") or 0), ranges), False, note)

    def _system_change(self, delta: int):
        """ENC2 on the selected row. RESET arms instead of changing."""
        st = self.app.settings
        row = self.system_row
        if row == SYS_VEL:
            from app.settings import VEL_CURVES
            cur = str(st.get("system", "vel_curve"))
            i = VEL_CURVES.index(cur) if cur in VEL_CURVES else 0
            st.set("system", "vel_curve",
                   VEL_CURVES[max(0, min(len(VEL_CURVES) - 1, i + delta))])
        elif row == SYS_POWER:
            from app.settings import POWER_OFF_MINUTES
            cur = int(st.get("system", "power_off"))
            i = (POWER_OFF_MINUTES.index(cur)
                 if cur in POWER_OFF_MINUTES else 0)
            st.set("system", "power_off",
                   POWER_OFF_MINUTES[max(0, min(len(POWER_OFF_MINUTES) - 1,
                                                i + delta))])
        elif row == SYS_BRIGHT:
            v = int(st.get("system", "brightness")) + delta * BRIGHT_STEP
            st.set("system", "brightness",
                   max(BRIGHT_MIN, min(BRIGHT_MAX, v)))
        elif row == SYS_PHONES:
            self._arm(FORGET if delta > 0 else None)
        elif row == SYS_RESET:
            self._arm(RESET if delta > 0 else None)

    @property
    def usb_exported(self) -> bool:
        """Is `/data` in the computer's hands right now (§ 11.1)?

        `getattr` and not an attribute on `App`: the flag belongs to the
        device runtime, the emulator has no `usb.sock` to set it from, and a
        page reaching for something the App may not have is how every other
        device-only fact reaches this page. Since #617 it is the App's
        `library_held`, from the STORAGE request's park, not the service's
        later `exported`.
        """
        return bool(getattr(self.app, "library_held",
                            getattr(self.app, "usb_exported", False)))

    @property
    def usb_status(self) -> Optional[dict]:
        """`usb.sock`'s status document, or `None` where there is no socket.

        `arbutus_os/pumps.py`'s `UsbPump` writes it once a frame; on the
        emulator nothing does, so STORAGE says J2's sentence (#524).
        """
        doc = getattr(self.app, "usb_status", None)
        return doc if isinstance(doc, dict) else None

    @property
    def power(self) -> Optional[dict]:
        """`power.sock`'s status document, or `None` where there is no socket.

        `PowerPump` writes it; on the emulator nothing does, so CHARGE says
        J2's sentence (#524). The laptop's own battery stays HELP's alone
        (`app/battery.py`): a laptop's charger is not the instrument's port.
        """
        doc = getattr(self.app, "power", None)
        return doc if isinstance(doc, dict) else None

    def _storage_row(self):
        """STORAGE's `(value, colour)`: § 11's state, or J2's sentence."""
        usb = self.usb_status
        if usb is None:
            return NO_GADGET, palette.DIM
        if usb.get("exported"):
            return STORAGE_EXPORTED, palette.GREEN
        why = usb.get("storage_blocked")
        if why:
            return str(why), palette.DIM
        return STORAGE_READY, palette.INK

    def _charge_row(self):
        """CHARGE's `(value, colour)`: `CHG_STAT` (§ 7.2), or J2's sentence."""
        pw = self.power
        if pw is None:
            return NO_CHARGE, palette.DIM
        if pw.get("charge_stat") == "unknown":
            return CHARGE_UNKNOWN, palette.DIM
        if pw.get("charging"):
            return CHARGING, palette.GREEN
        return NOT_CHARGING, palette.INK

    # -- the `.fw` line (D5b, #103) -----------------------------------------
    @property
    def firmware(self) -> Optional[dict]:
        """`fw.sock`'s status document, or `None` where there is no socket.

        `arbutus_os/pumps.py`'s `FwPump` writes it once a frame off
        `DeviceServices`; on the emulator nothing ever writes it, which is
        § 4.3's "a cell unlocks exactly when the socket behind it answers"
        read the only honest way round — `None` is locked, not empty.
        """
        doc = getattr(self.app, "firmware", None)
        return doc if isinstance(doc, dict) else None

    def _fw_waiting(self) -> Optional[dict]:
        """The inbox entry that needs the player to say yes (§ 10.1, DDR-073).

        `arbutus-fw` refuses to apply an untrusted file at boot — there is no
        screen at § 2.1 step 4 — so it sits in the inbox with `needs_confirm`
        until this page asks. Newest first, as `inbox()` sorts it.
        """
        for entry in (self.firmware or {}).get("inbox") or []:
            if isinstance(entry, dict) and entry.get("needs_confirm"):
                return entry
        return None

    def _fw_action(self) -> Optional[str]:
        """Which of the three ENC4 would run, or `None` for "nothing to do".

        **`staged`, not `pending`** (#111). A staged release is written into
        the other slot and waiting for a restart; a `pending` one is the
        release this instrument is *running*, on trial until it has drawn a
        frame. Offering `RESTART` for the second would be offering to apply
        what is already applied.

        **RESTART wins over INSTALL.** Installing a second file replaces the
        staged release — `_stage_app` unpacks into the other slot, which while
        something is staged is that same slot — so the one the player has
        already been told is ready is offered first. (Before #111 the reason
        was worse: the other slot was the *rollback*, and a second install
        threw away the only way back.)
        """
        doc = self.firmware
        if doc is None:
            return None
        if doc.get("staged"):
            return FW_RESTART
        if self._fw_waiting() is not None:
            return FW_INSTALL
        if doc.get("boot_result"):
            return FW_DISMISS
        return None

    def _fw_message(self) -> str:
        """The sentence under the rows — § 10's four, or J2's placeholder.

        `status.message` is already `boot_result` or `last_result`'s, in that
        order, so the failed-update line outlives whatever happened after it
        (§ 10.7 item 5). The `needs_confirm` fallback is for the one gap that
        leaves: a service restarted after its boot pass has an inbox and an
        empty `last_result`.
        """
        doc = self.firmware
        if doc is None:
            return FW_LINE
        message = str(doc.get("message") or "")
        if message:
            return message
        if self._fw_waiting() is not None:
            return FW_CONFIRM
        return FW_NONE

    def _fw_colour(self):
        """Red is a refusal or a rollback, green is a release ready to run."""
        doc = self.firmware
        if doc is None:
            return palette.SUBTLE
        if doc.get("boot_result"):
            return palette.RED
        if str((doc.get("last_result") or {}).get("result")) == "rejected":
            return palette.RED
        if doc.get("staged"):
            return palette.GREEN
        if self._fw_waiting() is not None:
            return palette.ACCENT_BROWN
        return palette.SUBTLE

    def _fw_cell_turned(self, value: float):
        """ENC4 on FILE › USB: turning right arms whatever is outstanding."""
        self._arm(self._fw_action() if value >= 0.5 else None)

    def _run_firmware(self, action: str):
        """The confirm landed. One message on `fw.sock`, and nothing else.

        Every one of the three is privileged — unpacking into a slot, rebooting
        the machine, writing the state document — so the UI does none of them:
        `fw_command` is the door `arbutus_os/main.py` opens (§ 4.3).
        """
        send = getattr(self.app, "fw_command", None)
        if send is None:
            self.app.flash(FW_NO_SOCKET, "NO FW")
            return
        if action == FW_RESTART:
            send({"type": "reboot"})
            self.app.flash("RESTARTING", "RESTART")
        elif action == FW_INSTALL:
            entry = self._fw_waiting()
            if entry is None:
                self.app.flash(FW_NONE, "NO UPDATE")
                return
            name = str(entry.get("file", ""))
            send({"type": "apply", "file": name, "confirm": True})
            self.app.flash("INSTALLING %s" % name, "INSTALL")
        else:
            send({"type": "dismiss"})
            self.app.flash("DISMISSED", "OK")

    # -- the confirm --------------------------------------------------------
    def _arm(self, action: Optional[str]):
        """Turning one of the four arms it; turning it back puts it down."""
        if self.usb_exported and (action in DISK_ACTIONS or action == RESET
                                  or action in FW_ACTIONS):
            # Arming is also reachable from the strip keys (`on_key`), not only
            # from a locked cell, so the refusal lives here and not only in
            # `slots()`. The firmware actions are in it for § 11.1's reason and
            # not J1's: the inbox *is* the library root, so while the computer
            # holds it there is no file to install, and a reboot would pull the
            # volume out from under whatever is copying onto it.
            self.app.flash(USB_EXPORTED_LINE, "USB BUSY")
            return
        self._armed = action
        self._confirm = 0
        # DDR-064: "SYSTEM › RESET asks 1-2-3-4 and then again." It is the one
        # gesture in the instrument that throws away more than a session, so
        # it is the one that costs eight key presses instead of four.
        self._rounds = 2 if action == RESET else 1
        self.close_menus()

    def disarm(self):
        self._armed = None
        self._confirm = 0
        self._rounds = 1
        self.close_menus()

    def on_panic(self):
        self.disarm()

    def cancel_confirm(self):
        """A key `App.on_key` keeps from the page (transport, notes) puts an
        armed run down too: "any other key in any position cancels"
        (control-inventory § The 1-2-3-4 confirm; #1123). Only when armed:
        `disarm` also closes LOAD's browser, which a note must not."""
        if self._armed is not None:
            self.disarm()

    def _confirm_press(self, key: str) -> bool:
        """One of 1-4 while something is armed. True once the run completes."""
        if key != CONFIRM_KEYS[self._confirm]:
            self.disarm()
            self.app.flash("CANCELLED", "CANCEL")
            return False
        self._confirm += 1
        if self._confirm < len(CONFIRM_KEYS):
            return False
        if self._rounds > 1:
            # one run down, one to go — the strip keeps saying CONFIRM and the
            # ticker says AGAIN, so the second pass is visibly the same gesture
            self._rounds -= 1
            self._confirm = 0
            self.app.flash("%s — %s" % (self._armed, CONFIRM_AGAIN), "AGAIN")
            return False
        action, self._armed, self._confirm = self._armed, None, 0
        self._rounds = 1
        self._run(action)
        return True

    def _run(self, action: str):
        app = self.app
        if action in FW_ACTIONS:
            self._run_firmware(action)
            return
        if action == FORGET:
            self._forget_phones()
        elif action == RESET:
            # DDR-064's reset: the instrument back to boot *and* the device
            # back to its defaults. It deliberately does **not** empty the
            # library — a settings menu that deletes a player's samples is a
            # different and much worse decision, and nothing in the spec asks
            # for one.
            if PF.new(app) is None and PF.not_saved(app):
                self._not_saved()
                return
            app.settings.reset()
            app.midi_clock.set_enabled(app.settings.clock_in)
            self.usb_saved = ""
            self.system_row = SYS_VEL
            app.flash("RESET TO DEFAULTS", "RESET")
        elif action == "BOUNCE":
            # D98 (#920): the render is a job in the audio process. BOUNCE
            # starts it and returns; `bounce_done` hears how it ended.
            if app.bounce_job is not None:
                app.flash(BOUNCING_LINE, "BOUNCING")
                return
            _, length = PF.bounce_span(app)
            if length <= 0:
                app.flash("NOTHING TO BOUNCE", "NO TAPE")
                return
            reply = PF.bounce(app, self.master_fx, job=True)
            if reply is None:
                app.flash("BOUNCE REFUSED", "REFUSED")
                return
            if app.bounce_job is not None:
                app.flash(BOUNCING_LINE, "BOUNCING")

    def bounce_done(self, m: dict):
        """The host's `bounce` push (#920): the file, or why there is none."""
        app = self.app
        if m.get("path"):
            self.bounce_path = str(m["path"])
            app.flash("BOUNCED %s" % os.path.basename(self.bounce_path),
                      "BOUNCED")
        elif m.get("error"):
            app.flash("BOUNCE FAILED: %s" % m["error"], "BOUNCE FAILED")

    # -- PROJECT: a turn right acts (D98, #818) -----------------------------
    def _act(self, action: str):
        if self.usb_exported and action in DISK_ACTIONS:
            self.app.flash(USB_EXPORTED_LINE, "USB BUSY")
            return
        self.disarm()
        if action != "BOUNCE":
            self.app.unflash()
        if action == "NEW":
            self.open_names(opener=ACTIONS.index("NEW"), on_pick=self._new)
        elif action == "LOAD":
            self._open_browser()
        elif action == "RENAME":
            current = (self.app.open_project or "").partition("/")[2]
            if not current or PF.open_folder(self.app) is None:
                self.app.flash("NO PROJECT OPEN", "NO PROJECT")
                return
            self.open_names(opener=ACTIONS.index("RENAME"),
                            on_pick=self._rename, current=current)
        elif action == "BOUNCE":
            self._run("BOUNCE")

    def open_names(self, opener: int, on_pick, current: Optional[str] = None,
                   crumb: Optional[List[str]] = None):
        """The name menu over this page (`app/name_menu.py`)."""
        self._names = NameMenu(self.app, self, "project",
                               PF.taken_names(self.app), on_pick, opener,
                               current=current,
                               crumb=crumb or [self.title,
                                               ACTIONS[opener]])
        self.app.open_menu(self._names)

    def name_after_offload(self):
        """The open project went to the phone (#821): the instrument is
        already in a new, auto-named one (`App.release_project`), and the
        name menu asks for its name (screen-sketches § FILE › PROJECT)."""
        if PF.open_folder(self.app) is None:
            return
        self.app.goto(self.key_id)
        self.state.subpage = PROJECT
        self.open_names(opener=ACTIONS.index("NEW"), on_pick=self._rename,
                        crumb=[self.title, "NEW"])

    def _not_saved(self):
        """#954: the open project's last document was refused, so NEW, LOAD
        and RESET leave it open rather than lose what the card does not hold."""
        current = (self.app.open_project or "").partition("/")[2]
        self.app.flash("NOT SAVED, %s STAYS OPEN" % current, "NOT SAVED")

    def _new(self, name: str):
        if PF.new(self.app, name) is None:
            if PF.not_saved(self.app):
                self._not_saved()
                return
            self.app.flash("NEW PROJECT, NOT NAMED %s" % name, "NEW")
            return
        self.app.flash("NEW PROJECT %s" % name, name)

    def _rename(self, name: str):
        current = (self.app.open_project or "").partition("/")[2]
        if name == current:
            return                          # `(current)` picked: nothing to do
        if PF.rename(self.app, name) is None:
            self.app.flash("CANNOT RENAME TO %s" % name, "NOT RENAMED")
            return
        self.app.flash("RENAMED %s" % name, name)

    def open_picked(self):
        """LOAD's commit: the push, or the red key."""
        app = self.app
        picked = self._picked()
        self.close_menus()
        if picked is None:
            app.flash("NO PROJECTS", "NONE")
            return
        date, name = picked
        path = PF.project_path(app, date, name)
        # a project naming an unsigned box asks first (#1161); CANCEL keeps
        # the open project, so nothing of the new one is half-applied
        if not box_trust.ask_ids(app, project_ids(app, PF.read(path)),
                                 lambda: self._open(path, name)):
            self._open(path, name)

    def _open(self, path: str, name: str):
        app = self.app
        if PF.load(app, path):
            app.flash("LOADED %s" % name, name)
        elif PF.not_saved(app):
            self._not_saved()
        elif PF.read(path) is None:
            app.flash("NOT A PROJECT", "BAD FILE")
        else:
            # its tape would not open (#411): the session stays as it was
            app.flash("CANNOT OPEN %s" % name, "NOT LOADED")

    def close_menus(self):
        """Put down the browser or the name menu, whichever is up."""
        names, self._names = self._names, None
        if names is not None and self.app.menu is names:
            self.app.close_menu()
        self._close_browser()

    def menu_closed(self, menu):
        """A name menu closed itself (its push, or a key it cancels on)."""
        if menu is self._names:
            self._names = None

    # -- the project browser ------------------------------------------------
    def _open_browser(self):
        self._listing = PF.list_projects(self.app)
        table = {d: list(n) for d, n in self._listing.items()} or {"—": ["—"]}
        source = _ProjectSource(table, PF.projects_root(self.app))
        self._browser = _ProjectBrowser(self.app, self, source,
                                        lambda f, i: None,
                                        [self.title, "LOAD"])
        self.app.open_menu(self._browser)

    def refresh_listing(self):
        """After a LIFT or DROP on the project list, re-read the tree."""
        self._listing = PF.list_projects(self.app)
        if self._browser is not None:
            self._browser.source.table = {d: list(n)
                                          for d, n in self._listing.items()} \
                or {"—": ["—"]}
            self._browser.source._keys = list(self._browser.source.table.keys())

    def _close_browser(self):
        if self._browser is not None and self.app.menu is self._browser:
            self.app.close_menu()
        self._browser = None

    def _picked(self):
        """`(date, name)` of the highlighted row, or None when there are none."""
        if self._browser is None or not self._listing:
            return None
        dates = list(self._listing.keys())
        d = dates[max(0, min(len(dates) - 1, self._browser.state.folder_idx))]
        names = self._listing[d]
        if not names:
            return None
        return d, names[max(0, min(len(names) - 1,
                                   self._browser.state.item_idx))]

    def on_menu_number(self, key: str):
        """1-4 with a menu of this page's up: the red key commits, the other
        three cancel (D98). `App.on_key` has already closed the menu.

        A cancel is only a cancel: the key does not also switch the subpage,
        which would land a player who pressed `4` to back out of NEW on
        SYSTEM (run's call, 2026-10-13).
        """
        key = str(getattr(key, "value", key))
        names, browser = self._names, self._browser
        if names is not None:
            self._names = None
            if key == str(names.red_key + 1):
                names.commit()
            return
        if browser is not None:
            if key == str(browser.red_strip_cell() + 1):
                self.open_picked()
            else:
                self.close_menus()
            return
        self.on_key(key, True, False)

    # -- Page contract ------------------------------------------------------
    def strip_labels(self):
        # Four identical cells on purpose: all four have to be pressed, and
        # the highlight (`active_strip_cell`) walks 1 → 4 as they land, so the
        # strip shows how far through the run you are. The sentence itself is
        # the dynamic strip's, where DDR-031 puts a sentence.
        if self._armed is not None:
            return ["CONFIRM"] * 4
        return list(SUBPAGES)

    def active_strip_cell(self):
        if self._armed is not None:
            return self._confirm if self._confirm < len(CONFIRM_KEYS) else None
        return self.state.subpage

    def tickers(self):
        from app.app import TICKER_OTHER
        if self.usb_exported:
            # § 11.1: the instrument keeps playing, so this is a line on the
            # page and not a modal — it is the only thing FILE has to say
            # while the computer holds the library.
            return [(TICKER_OTHER, USB_EXPORTED_LINE, "USB STORAGE")]
        if self._armed is None:
            return []
        strip = CONFIRM_AGAIN if self._asked_once else CONFIRM_STRIP
        return [(TICKER_OTHER, "%s — %s" % (strip, self._armed),
                 "%s?" % self._armed)]

    @property
    def _asked_once(self) -> bool:
        """True once the first 1-2-3-4 of a two-round confirm has landed."""
        return self._armed == RESET and self._rounds == 1

    def on_key(self, key, pressed: bool, shift: bool) -> bool:
        key = str(getattr(key, "value", key))
        if not pressed:
            return False
        if K.push_index(key) >= 0:
            return True                 # nothing to commit here: not a cancel
        if key in CONFIRM_KEYS:
            if self._armed is not None:
                self._confirm_press(key)
                return True
            self.state.subpage = CONFIRM_KEYS.index(key)
            return True
        # any other key on this page is an escape hatch, deliberately: the
        # four actions are the only ones that can lose work
        self.disarm()
        return False

    def on_encoder(self, idx: int, delta: int, shift: bool) -> bool:
        if self.state.subpage == SYSTEM:
            return self._system_encoder(idx, delta, shift)
        if self.state.subpage != PROJECT:
            return super().on_encoder(idx, delta, shift)
        # D98: a turn right acts; a turn left does nothing (it never did
        # anything but put the gun down, and there is no gun)
        if not shift and 0 <= idx < len(ACTIONS):
            if delta > 0:
                self._act(ACTIONS[idx])
            return True
        return super().on_encoder(idx, delta, shift)

    def _system_encoder(self, idx: int, delta: int, shift: bool) -> bool:
        """ENC1 selects, ENC2 changes (screen-sketches § FILE › SYSTEM)."""
        if shift or delta == 0:
            return False
        rows = self._system_rows()
        n = self._row_index(rows)
        if idx == 0:
            # moving the cursor puts down anything armed on the row you left:
            # a gun pointed at RESET must not follow you up the list
            if self._armed is not None:
                self.disarm()
            self.system_row = rows[max(0, min(len(rows) - 1, n + delta))][0]
            return True
        if idx == 1:
            if not rows[n][3]:
                return True             # a read-out row: named, and inert
            self._system_change(delta)
            return True
        return False

    #: per subpage, because the four encoders mean four different things on
    #: each of them — which is exactly the question DDR-025 exists to answer
    HELP = {
        PROJECT: {
            "ENC1": "ENC1 NEW: turn right, pick a name, push (or the red "
                    "key) to make it",
            "ENC2": "ENC2 LOAD: turn right for the project list, push (or "
                    "the red key) loads",
            "ENC3": "ENC3 RENAME: turn right, pick a name, push (or the red "
                    "key)",
            "ENC4": "ENC4 BOUNCE: turn right for an offline WAV of the whole "
                    "tape (SHIFT: MASTER FX in the bounce or not)",
        },
        MIDI: {
            "ENC1": "ENC1 IN CH: which MIDI channel the keys listen on "
                    "(OMNI = all)",
            "ENC2": "ENC2 OUT CH: no MIDI output on this host — the cell is "
                    "dead on purpose",
            "ENC3": "ENC3 CLOCK IN: follow an incoming MIDI clock. "
                    "TEMPO SYNC = MIDI then follows it",
            "ENC4": "ENC4 CLOCK OUT: nothing to send it on here",
        },
        USB: {
            "ENC1": "ENC1 MODE: STORAGE writes the project to the card "
                    "first (DDR-021). No USB gadget on this host",
            "ENC2": "ENC2 CHARGE: the charger is the device's, not this "
                    "laptop's",
            "ENC3": "ENC3 PAIR: open the pairing window — the 4-digit code "
                    "shows here, type it into the app",
            "ENC4": "ENC4 UPDATE: a .fw or .arbox waiting in the library "
                    "root — turn right to arm, then 1 2 3 4",
        },
        SYSTEM: {
            "ENC1": "ENC1 SELECT: move down the settings list",
            "ENC2": "ENC2 VALUE: change the selected setting. On RESET it "
                    "arms 1 2 3 4, twice",
            "ENC3": "ENC3: nothing — SYSTEM is two knobs and a list",
            "ENC4": "ENC4: nothing — SYSTEM is two knobs and a list",
        },
    }

    def help_for(self, target: str) -> str:
        key = str(target)
        if key == "FILE":
            return "USB/FILE: 1-4 = PROJECT / MIDI / USB / SYSTEM"
        return self.HELP.get(self.state.subpage, {}).get(key, "")

    # -- render -------------------------------------------------------------
    def render(self, fb, rect, app):
        if self.state.subpage == MIDI:
            self._render_midi(fb, rect)
            return
        if self.state.subpage == USB:
            self._render_usb(fb, rect)
            return
        if self.state.subpage == SYSTEM:
            self._render_system(fb, rect)
            return
        x, y, w, h = rect
        date, _, name = (app.open_project or "").partition("/")
        draw.text(fb, x + 6, y + 6, name or "NO PROJECT", palette.INK, LARGE)
        if date:
            draw.text(fb, x + 6, y + 22, date, palette.SUBTLE, SMALL)

        used = sum(int(c.length) for track in app.tape.tracks for c in track)
        rows = [("TAPE USED", _fmt_time(used, app.sample_rate)),
                ("FREE", self._free()),
                ("MASTER FX", "ON" if self.master_fx else "OFF")]
        for i, (label, value) in enumerate(rows):
            ry = y + 40 + i * 12
            draw.text(fb, x + 6, ry, label, palette.SUBTLE, SMALL)
            draw.text(fb, x + w - 6 - draw.text_width(value, SMALL), ry, value,
                      palette.INK, SMALL)

        if self._armed is not None:
            draw.text_centered(fb, x + w // 2, y + h - 14, self._armed_line(),
                               palette.RED, SMALL)
        elif app.bounce_job is not None:
            draw.text_centered(fb, x + w // 2, y + h - 14, BOUNCING_LINE,
                               palette.INK, SMALL)
        elif self.bounce_path:
            draw.text_centered(fb, x + w // 2, y + h - 14,
                               draw.clip_text(os.path.basename(self.bounce_path),
                                              w - 12, SMALL),
                               palette.GREEN, SMALL)

    def _armed_line(self) -> str:
        """What is about to happen — not a repeat of the strip's sentence.

        The strip already says `1 2 3 4 TO CONFIRM`; the thing it cannot say
        is the consequence, which is the whole reason there is a confirm.
        """
        app = self.app
        if self._armed in FW_ACTIONS:
            return self._fw_armed_line()
        if self._armed == FORGET:
            return FORGET_ARMED
        if self._armed == RESET:
            return "RESET ERASES EVERYTHING — AGAIN" if self._asked_once \
                else "RESET: THE SONG AND THE SETTINGS"
        _, length = PF.bounce_span(app)
        return "BOUNCE %s · MASTER FX %s" % (
            _fmt_time(length, app.sample_rate), "ON" if self.master_fx else "OFF")

    def _fw_armed_line(self) -> str:
        """What ENC4's confirm is about to do — the version, not the verb.

        `staged_version` first (#111): the staged release is the one a restart
        lands in, and since the pointer no longer moves at staging time
        `status.version` is honestly the running one. `last_result.version`
        stays behind it for the instant between an `apply` reply and the next
        `status` poll, when only the reply knows what was just written.
        """
        doc = self.firmware or {}
        if self._armed == FW_RESTART:
            ver = str(doc.get("staged_version")
                      or (doc.get("last_result") or {}).get("version")
                      or doc.get("version") or "")
            return ("RESTART INTO %s" % ver) if ver else "RESTART TO APPLY"
        if self._armed == FW_INSTALL:
            #: `trust` rather than the word "UNSIGNED": `manifest.py` has two
            #: values today and the trust table (§ 10.6) has five rows, so the
            #: file says what it is instead of this line guessing.
            entry = self._fw_waiting() or {}
            ver = str(entry.get("version") or "")
            name = str(entry.get("file") or "THE FILE")
            trust = str(entry.get("trust") or "unsigned").upper()
            return "INSTALL %s%s — %s" % (name, " %s" % ver if ver else "",
                                          trust)
        return str((doc.get("boot_result") or {}).get("message")
                   or "CLEAR THE UPDATE MESSAGE")

    def _free(self) -> str:
        try:
            st = os.statvfs(self.app.library.root)
            return _fmt_bytes(st.f_bavail * st.f_frsize)
        except (OSError, AttributeError):
            return "—"

    # -- the J2 subpages ----------------------------------------------------
    def _rows(self, fb, rect, rows, top=6):
        """`(label, value, colour)` down the main area — MIDI and USB's shape."""
        x, y, w, _ = rect
        for i, (label, value, colour) in enumerate(rows):
            ry = y + top + i * 12
            draw.text(fb, x + 6, ry, label, palette.SUBTLE, SMALL)
            text = draw.clip_text(value, w - 12 - draw.text_width(label, SMALL),
                                  SMALL)
            draw.text(fb, x + w - 6 - draw.text_width(text, SMALL), ry, text,
                      colour, SMALL)

    def _render_midi(self, fb, rect):
        app = self.app
        port = getattr(getattr(app, "midi_in", None), "port_name", None)
        clock = app.midi_clock
        st = app.settings
        ch = int(st.get("midi", "in_ch"))

        if clock.enabled and clock.alive():
            clock_txt, clock_col = "%.1f BPM" % clock.bpm, palette.GREEN
        elif clock.enabled:
            clock_txt, clock_col = "NO CLOCK", palette.ACCENT_BROWN
        else:
            clock_txt, clock_col = "OFF", palette.DIM
        self._rows(fb, rect, [
            ("IN", port or NO_MIDI_IN,
             palette.INK if port else palette.DIM),
            ("CHANNEL", CHANNEL_LABELS[0 if ch < 0 else ch + 1],
             palette.INK),
            ("CLOCK IN", clock_txt, clock_col),
            ("OUT", NO_MIDI_OUT, palette.DIM),
        ])
        x, y, w, h = rect
        # the sentence that makes the two dead cells make sense, and the one
        # that says what turning CLOCK IN on is *for*
        draw.text_centered(fb, x + w // 2, y + h - 14,
                           draw.clip_text("CLOCK IN FEEDS TEMPO SYNC MIDI",
                                          w - 12, SMALL),
                           palette.SUBTLE, SMALL)

    def _render_usb(self, fb, rect):
        st = self.app.settings
        mode = str(st.get("usb", "mode"))
        rows = [("MODE", mode,
                 palette.INK if mode != "OFF" else palette.DIM),
                ("SAVED", self.usb_saved or "\u2014",
                 palette.GREEN if self.usb_saved else palette.DIM),
                ("STORAGE",) + self._storage_row(),
                ("CHARGE",) + self._charge_row()]
        pair_value, pair_colour = self._pair_row()
        rows.append(("PAIR", pair_value, pair_colour))
        rows += self._fw_rows()
        self._rows(fb, rect, rows)
        x, y, w, h = rect
        code = self._pair_code()
        if code:
            # § 9.5 step 3: "the screen shows a 4-digit code and the user
            # types it into the app". It takes the foot of the page from the
            # `.fw` sentence while it is up, and it is the one thing on this
            # page drawn LARGE. The `.fw` line is persistent and will still be
            # there afterwards; a pairing window is three minutes long and is
            # the only thing the player is doing while it is open.
            draw.text_centered(fb, x + w // 2, y + h - 14,
                               PAIR_LINE % code, palette.GREEN, LARGE)
            return
        draw.text_centered(fb, x + w // 2, y + h - 14,
                           draw.clip_text(self._fw_message(), w - 12, SMALL),
                           self._fw_colour(), SMALL)

    def _fw_rows(self):
        """The two rows the `.fw` line is, or none at all off the device.

        Two and not one because the question a player asks here has two halves
        — what is running, and what is waiting — and § 10.3's whole design is
        that the answer to the first is always two versions, the slot that
        booted and the one that is the way back.
        """
        doc = self.firmware
        if doc is None:
            return []
        slot = str(doc.get("slot", "?")).upper()
        other = str(doc.get("other_slot", "?")).upper()
        running = "%s %s" % (slot, str(doc.get("version") or "\u2014"))
        spare = "%s %s" % (other, str(doc.get("other_slot_version") or "\u2014"))
        inbox = doc.get("inbox") or []
        if doc.get("staged"):
            #: #111: the staged slot is the one this row is about. A `pending`
            #: slot is already running, so it belongs in `running` above and
            #: says nothing here.
            waiting, colour = "RESTART TO APPLY", palette.GREEN
        elif inbox:
            waiting = str((inbox[0] or {}).get("file") or "\u2014")
            colour = palette.ACCENT_BROWN
        else:
            waiting, colour = "\u2014", palette.DIM
        return [("SLOTS", "%s \u00b7 %s" % (running, spare), palette.INK),
                (FW_UPDATE, waiting, colour)]

    def _render_system(self, fb, rect):
        """The scrolling list. ENC1 is the cursor, ENC2 the value."""
        x, y, w, h = rect
        rows = self._system_rows()
        cursor = self._row_index(rows)
        visible = 7
        top = max(0, min(max(0, len(rows) - visible),
                         cursor - visible // 2))
        for i in range(visible):
            n = top + i
            if n >= len(rows):
                break
            _key, label, value, editable, _note = rows[n]
            ry = y + 4 + i * 11
            active = (n == cursor)
            if active:
                draw.fill_rect(fb, x + 4, ry - 1, w - 8, 10, palette.INVERT_BG)
            fg = palette.INVERT_FG if active else (
                palette.INK if editable else palette.DIM)
            draw.text(fb, x + 8, ry, label, fg, SMALL)
            vw = draw.text_width(value, SMALL)
            draw.text(fb, x + w - 8 - vw, ry, value, fg, SMALL)

        note = rows[cursor][4] if rows else ""
        if self._armed in (RESET, FORGET):
            note, colour = self._armed_line(), palette.RED
        elif note == NO_IMU:
            # the one place the missing IMU has to be said (DDR-048); with a
            # sensor fitted the row itself is there instead and this is ""
            colour = palette.DIM
        else:
            colour = palette.SUBTLE
        if note:
            draw.text_centered(fb, x + w // 2, y + h - 12,
                               draw.clip_text(note, w - 12, SMALL), colour,
                               SMALL)

    # -- persistence --------------------------------------------------------
    def snapshot(self) -> dict:
        # the *settings* are not in here: they are the device's and live in
        # `<library>/settings.json`, not in a project or in `state/`. What a
        # snapshot carries is where you were standing.
        # the project's name is `app.open_project`'s, not the page's (D97)
        return {"subpage": self.state.subpage, "master_fx": self.master_fx,
                "system_row": self.system_row}

    def restore(self, data: dict):
        self.state.subpage = int(data.get("subpage", PROJECT))
        self.master_fx = bool(data.get("master_fx", False))
        # the cursor is a key since #116; an older snapshot carries an index
        # into the seven-row list this page used to have.
        row = data.get("system_row", SYS_VEL)
        if isinstance(row, int):
            legacy = (SYS_VEL, SYS_POWER, SYS_DATE, SYS_BRIGHT, SYS_WIFI,
                      SYS_PHONES, SYS_RESET)
            row = legacy[max(0, min(len(legacy) - 1, row))]
        self.system_row = str(row)
        self.disarm()


def project_ids(app, doc) -> List[str]:
    """Every box id a project document loads (#1161): each slot page's six
    records, and the master rack."""
    if not isinstance(doc, dict):
        return []
    ids: List[str] = []
    for pid, block in (doc.get("slots") or {}).items():
        page = app.pages.get(pid)
        if page is None or not isinstance(block, dict):
            continue
        for record in (block.get("slots") or {}).values():
            ids += box_trust.record_ids(app, page, record)
    ids += box_trust.doc_ids((doc.get("master") or {}).get("fx"))
    return ids
