"""柔光 on every firmware does not touch the settings constructor on the MENU update path: the saved setting is read
from the Normal and Playback start-up branches. Checks the window files only, no firmware needed."""
import json
import unittest
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "darkslide" / "soft_glow_windows.json"


def off(va):
    return va - 0x53000000 + 0x10870


FILES = (("soft_glow_windows.json", (0x5324B718, 0x48), (0x53229174, 0x24), (0x53240730, 0x5324074C), ((0x532292A0, "d07400eb"), (0x5322926C, "dd7400eb"))),
         ("soft_glow_windows_220.json", (0x5324B7F0, 0x48), (0x5322924C, 0x24), (0x53240808, 0x53240824), ((0x53229378, "d07400eb"), (0x53229344, "dd7400eb"))))


class UpdatePath(unittest.TestCase):
    def test_constructor_and_update_branch_stay_original_and_read_sites_are_written(self):
        for name, constructor, update, template, sites in FILES:
            windows = json.loads((DATA.parent / name).read_text(encoding="utf-8"))["windows"]
            written = lambda va, size=4: [w for w in windows if w[0] < off(va) + size and off(va) < w[0] + len(w[2]) // 2]
            self.assertEqual(written(*constructor), [], name)
            self.assertEqual(written(*update), [], name)
            for va in template:
                self.assertEqual(written(va), [], name)
            for va, original in sites:
                at, old, new = written(va)[0]
                start = (off(va) - at) * 2
                # A run holds only the bytes that changed; the high byte of a BL may stay outside it.
                self.assertEqual(old[start:start + 8], original[:len(old[start:start + 8])], name)
                self.assertNotEqual(new[start:start + 8], old[start:start + 8], name)


if __name__ == "__main__":
    unittest.main()
