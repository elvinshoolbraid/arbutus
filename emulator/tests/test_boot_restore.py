"""#624: what a boot restores has to be what the last session left.

Two rows of the m-emu-app code review (`docs/logs/findings/2026-10-06-m-emu-app.md`):

- **Row 3.** A boot that restores a page other than SYNTH or DRUM set the UI's
  active instrument from `project.json` and never told the audio process, so
  the keys sounded SYNTH while the UI (SEQ, the octave, the ticker) believed
  DRUM. Notes carry no instrument on the wire; the host's `active` is the
  truth (DDR-034).
- **Row 4.** Recalling a slot that has a record changed the store's `active`
  without marking it dirty, so `<page>.json` kept the old letter; and the
  boot never put the store's letter back on the page. After a reboot the
  screen said one slot and the autosave wrote into another.

Each test reboots: a second `App` with `fresh=False` on the same state
directory and a new audio process, as the device does.
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tests.native_support import app_for, host, state_dir   # noqa: E402

STATE = state_dir("boot-restore")


class BootRestore(unittest.TestCase):
    def setUp(self):
        self.native = host(self)
        self.app, self.audio = app_for(self.native, STATE, fresh=True)
        self.addCleanup(self.audio.stop)

    def reboot(self):
        """Flush, then a new audio process and a new App on the same state."""
        self.app.flush()
        native = host(self)
        app, audio = app_for(native, STATE, fresh=False)
        self.addCleanup(audio.stop)
        return native, app

    def press(self, app, key):
        app.on_key(key, True)
        app.on_key(key, False)

    # -- row 3 --------------------------------------------------------------
    def test_a_boot_on_track_after_drum_plays_drum(self):
        self.app.goto("DRUM")
        self.app.goto("TRACK")
        native, app = self.reboot()
        self.assertEqual(app.page_id, "TRACK")
        self.assertEqual(app.active_instrument_id, "DRUM")
        self.assertEqual(native.refresh()["active"], "drum",
                         "the UI restored DRUM but the host plays SYNTH")

    def test_a_boot_on_synth_after_drum_plays_synth(self):
        """The control (#56): the instrument page itself still wins."""
        self.app.goto("DRUM")
        self.app.goto("SYNTH")
        native, app = self.reboot()
        self.assertEqual(native.refresh()["active"], "synth")

    # -- row 4 --------------------------------------------------------------
    def test_a_recalled_slot_is_the_one_on_disk(self):
        self.app.goto("SYNTH")
        self.press(self.app, "B")              # empty: a copy of A
        self.press(self.app, "A")              # has a record: a recall
        self.app.flush()
        store = self.app.pages["SYNTH"].slot_store
        with open(store.path) as f:
            self.assertEqual(json.load(f)["active"], "A",
                             "the recall of A left B as the slot on disk")
        _, app = self.reboot()
        self.assertEqual(app.pages["SYNTH"].slot_store.active, "A")

    def test_a_boot_shows_the_slot_it_restored(self):
        self.app.goto("SYNTH")
        self.press(self.app, "B")
        _, app = self.reboot()
        page = app.pages["SYNTH"]
        self.assertEqual(page.slot_store.active, "B")
        self.assertEqual(page.state.slot_letter, "B",
                         "the breadcrumb says A over slot B's sound")


if __name__ == "__main__":
    unittest.main()
