"""日期印记 (date07): how the feature is requested and what its window file is. No original or changed payload here."""
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from darkslide import camera_features, models, wide_ratio, wide_ratio_verify  # noqa: E402

DATA = Path(wide_ratio_verify.__file__).with_name("date_stamp_windows.json")
BOTH = dict(soft_glow=True, wide_ratio=True)


class Requests(unittest.TestCase):
    def test_needs_glow_and_wide(self):
        for value in (dict(date_stamp=True), dict(date_stamp=True, soft_glow=True), dict(date_stamp=True, wide_ratio=True)):
            with self.assertRaises(camera_features.FeatureError):
                camera_features.clean(value)
        self.assertTrue(camera_features.clean(dict(BOTH, date_stamp=True))["date_stamp"])

    def test_edit_carries_date_only_when_on(self):
        self.assertEqual(camera_features.edits(BOTH), [wide_ratio.edit(glow=True)])
        self.assertNotIn("date", camera_features.edits(BOTH)[0])
        self.assertIs(camera_features.edits(dict(BOTH, date_stamp=True))[0]["date"], True)

    def test_validate_refuses_date_without_glow(self):
        good = wide_ratio.edit(glow=True, date=True)
        wide_ratio.validate_request(good)
        for bad in (dict(good, glow=False), dict(good, date=False), dict(good, date=1)):
            with self.assertRaises(Exception):
                wide_ratio.validate_request(bad)

    def test_capabilities(self):
        # 1.6.1 does not offer the date stamp on any model (user, 2026-11-02).
        for model in (models.GR3, models.GR3X, *models.NEW_220):
            caps = models.capabilities(model)
            self.assertFalse(caps["date_stamp"])
            with self.assertRaises(camera_features.FeatureError):
                camera_features.clean(dict(BOTH, date_stamp=True), caps)
        for model in models.NEW_220:
            self.assertFalse(models.capabilities(model)["wide_ratio"])


class Windows(unittest.TestCase):
    def test_file_pinned_and_both_readers_agree(self):
        self.assertEqual(hashlib.sha256(DATA.read_bytes()).hexdigest(), wide_ratio_verify.DATE_DATA_SHA256)
        rows = wide_ratio._windows(True, True)
        self.assertEqual(rows, wide_ratio_verify.windows(True, True))

    def test_rows_ordered_and_same_length(self):
        cursor = 0
        for at, old, new in json.loads(DATA.read_text())["windows"]:
            self.assertGreaterEqual(at, cursor)
            self.assertEqual(len(old), len(new))
            cursor = at + len(old) // 2

    def test_date_without_glow_refused(self):
        with self.assertRaises(wide_ratio_verify.WideRatioError):
            wide_ratio_verify.windows(False, True)

    def test_mode_11_windows_outside_the_date_set_keep_their_bytes(self):
        date = sorted(wide_ratio._windows(True, True))
        changed = {}
        for at, _old, new in date:
            changed.update({at + i: b for i, b in enumerate(new)})
        moved = 0
        for at, _old, new in wide_ratio._windows(True):
            for i, b in enumerate(new):
                # Every mode-11 byte is either kept or overwritten by a date row: none falls out of the set.
                self.assertIn(at + i, changed)
                moved += changed[at + i] != b
        self.assertLess(moved, 12000)


if __name__ == "__main__":
    unittest.main()
