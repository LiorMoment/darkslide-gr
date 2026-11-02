"""16:9 / 65:24 wide ratios (B183k): request shape, the pinned window file, the independent readback, and, with the real
official file (DARKSLIDE_OFFICIAL_BIN), every route alone and with 柔光, a film and the menu order, plus the HDF refusal.
In memory only."""
import hashlib
import json
import os
import unittest
from pathlib import Path

import tempfile

from darkslide import soft_glow_verify, camera_features, edit_core, fwread, generate, gr3x_generation, menu_order, models, rescue_gate, template as tpl, wide_ratio, wide_ratio_verify
from darkslide.store import Store

OFFICIAL = Path(os.environ.get("DARKSLIDE_OFFICIAL_BIN", "/nonexistent/fwdc239b.bin"))
IIIX = Path(os.environ.get("DARKSLIDE_IIIX_BIN", "/nonexistent/fwdc243b.bin"))
REVERSED = list(menu_order.FACTORY_STILL[::-1])
ORDER = dict(schema=1, still_slots=REVERSED, video_slots=menu_order.video_for(REVERSED))


class Shape(unittest.TestCase):
    def test_level_reader_is_the_hand_written_guard_word_read(self):
        # These words exist only in the window file. A core rebuilt from core.c would read owner+0xB28 again.
        at = 0x53E4CC54 - 0x53000000 + 0x10870
        want = bytes.fromhex("d00e03e3060545e3000090e550a8e1e70b0000ea")
        for mode, rows in json.loads(wide_ratio.DATA.read_text(encoding="utf-8"))["modes"].items():
            held = [bytes.fromhex(new)[at - start:at - start + 20] for start, _, new in rows if start <= at < start + len(new) // 2]
            self.assertEqual(held, [want], mode)

    def test_display_plan_is_not_overwritten(self):
        # The wide ratio's display code starts where the glow platform ends; 0x53E5EDEC points at it.
        at, ptr = 0x53E02F1C - 0x53000000 + 0x10870, 0x53E5EDEC - 0x53000000 + 0x10870
        for mode, rows in json.loads(wide_ratio.DATA.read_text(encoding="utf-8"))["modes"].items():
            held = [bytes.fromhex(new)[at - start:] for start, _, new in rows if start <= at < start + len(new) // 2]
            self.assertEqual(len(held), 1, mode)
            self.assertEqual(len(held[0]), 375, mode)  # its last byte equals the original, so the row ends one short of 376
            self.assertEqual(hashlib.sha256(held[0]).hexdigest(), "064a240a109dbb5098a247279a14e444f9c4bb65d8b5c98fc46049142e2c3487", mode)
            pointer = [bytes.fromhex(new)[ptr - start:ptr - start + 4] for start, _, new in rows if start <= ptr < start + len(new) // 2]
            self.assertEqual(pointer, [bytes.fromhex("1d2fe053")], mode)

    def test_failed_settings_branch_keeps_its_landing(self):
        # 0x53E4CC50 branches here when the settings object is null: the level must be a fixed 0, not what sl held.
        at = 0x53E4CC94 - 0x53000000 + 0x10870
        for mode, rows in json.loads(wide_ratio.DATA.read_text(encoding="utf-8"))["modes"].items():
            held = [bytes.fromhex(new)[at - start:at - start + 4] for start, _, new in rows if start <= at < start + len(new) // 2]
            self.assertEqual(held, [bytes.fromhex("00a0a0e3")], mode)

    def test_request(self):
        wide_ratio.validate_request(wide_ratio.edit())
        wide_ratio.validate_request(wide_ratio.edit(glow=True))
        for bad in (dict(kind="wide_ratio"), dict(kind="wide_ratio", version="b183i", glow=False),
                    dict(kind="wide_ratio", version="wide16", glow=1), dict(kind="wide_ratio", version="wide16", glow=False, x=1)):
            with self.assertRaises(edit_core.BuildError, msg=repr(bad)):
                wide_ratio.validate_request(bad)

    def test_switches_make_one_edit(self):
        self.assertEqual(camera_features.edits(dict(wide_ratio=True)), [wide_ratio.edit()])
        self.assertEqual(camera_features.edits(dict(wide_ratio=True, soft_glow=True)), [wide_ratio.edit(glow=True)])
        self.assertIsNone(camera_features.refusal(dict(wide_ratio=True, soft_glow=True)))

    def test_both_series_take_it(self):
        for model in (models.GR3, models.GR3X):
            self.assertIs(models.capabilities(model)["wide_ratio"], True)

    def test_window_file_is_pinned_and_ordered(self):
        self.assertEqual(len(wide_ratio_verify.windows(False)), 149)
        self.assertEqual(len(wide_ratio_verify.windows(True)), 220)
        for glow in (False, True):
            self.assertTrue(all(len(o) == len(n) for _, o, n in wide_ratio_verify.windows(glow)))


@unittest.skipUnless(OFFICIAL.is_file(), "needs DARKSLIDE_OFFICIAL_BIN")
class Real(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.official = OFFICIAL.read_bytes()
        cls.before = fwread.unpack(cls.official)
        cls.before = cls.before[0] if isinstance(cls.before, tuple) else cls.before

    def test_apply_matches_readback_and_rejects_changed_candidate(self):
        for glow in (False, True):
            after, changes = wide_ratio.apply(self.before, glow=glow)
            checked = wide_ratio_verify.verify(self.before, after, glow)
            self.assertEqual(checked["windows"], len(changes))
            bad = bytearray(after)
            at = checked["spans"][0][0]
            bad[at] ^= 1
            with self.assertRaises(wide_ratio_verify.WideRatioError):
                wide_ratio_verify.verify(self.before, bytes(bad), glow)

    def built(self, features, body="GR III", changes=None, order=None):
        t = tpl.load()
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            pid = store.create_project("画幅", store.put_input(self.official), t, target_body=body)["id"]
            store.put_draft(pid, changes or {}, t, menu_order=order, camera_features=features)
            project = store.save(pid, t)
            return generate.run(store, project, t, self.official)

    def test_saved_project_routes(self):
        names = {"2": {"name": "Wide"}}
        for features, changes, order, glow in ((dict(wide_ratio=True), None, None, False),
                                               (dict(wide_ratio=True, soft_glow=True), None, None, True),
                                               (dict(wide_ratio=True, soft_glow=True), names, ORDER, True)):
            output, manifest = self.built(features, changes=changes, order=order)
            self.assertTrue(all(c["ok"] is True for c in manifest["checks"]))
            ids = [c["id"] for c in manifest["checks"]]
            self.assertIn("wide_ratio_windows", ids)
            self.assertNotIn("soft_glow_windows", ids)
            self.assertIn("wide_ratio", manifest["features"])
            self.assertIn("按住 MENU 开机", manifest["verification"])
            self.assertIn("画幅", [row["preset"] for row in manifest["summary"]])
            after = fwread.unpack(output)
            after = bytes(after[0] if isinstance(after, tuple) else after)
            for at, _, new in wide_ratio_verify.windows(glow):
                self.assertEqual(after[at:at + len(new)], new)
            self.assertTrue(rescue_gate.all_ok(rescue_gate.gate(self.official, output)))

    def test_app_route_builds(self):
        # Five films, menu order, 画幅 + 柔光. Not a package a user tested.
        t = tpl.load()
        still = [8, 9, 10, 11, 1, 2, 3, 4, 5, 6, 12, 7]
        order = dict(schema=1, still_slots=still, video_slots=menu_order.video_for(still))
        changes = {"2": {"film": "portra160"}, "8": {"film": "gold200"}, "9": {"film": "portra400"}, "10": {"film": "ektar100"},
                   "11": {"film": "5219"}}
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            pid = store.create_project("画幅", store.put_input(self.official), t, target_body="GR III")["id"]
            store.put_draft(pid, changes, t, menu_order=order, camera_features=dict(wide_ratio=True, soft_glow=True))
            output, _ = generate.run_inplace(store, store.save(pid, t), t, self.official)
        self.assertTrue(output)

    def test_hdf_body_is_refused(self):
        with self.assertRaisesRegex(generate.GenerateError, "还不支持 HDF 机身"):
            self.built(dict(wide_ratio=True), body="GR III HDF")

    def test_refuses_other_payload(self):
        with self.assertRaises(edit_core.BuildError):
            wide_ratio.apply(self.before[:-1] + b"\0")

    def test_same_glow_and_read_off_the_update_path(self):
        off = lambda va: va - 0x53000000 + 0x10870
        for glow in (False, True):
            rows = wide_ratio_verify.windows(glow)
            written = lambda va: [r for r in rows if r[0] <= off(va) < r[0] + len(r[2])]
            self.assertEqual(written(0x5324B758), [], "the constructor stays original")
            self.assertEqual(written(0x53240730), [], "the allocation size stays original")
            self.assertEqual(written(0x5324074C), [], "the default-template call stays original")
            self.assertEqual(bool(written(0x532292A0)), glow)
            self.assertEqual(bool(written(0x5322926C)), glow)
        # The glow module is the one the 柔光 switch writes alone, except where wide chains to it (capture shim).
        solo = {at + i: new[i] for at, _, new in soft_glow_verify.windows() for i in range(len(new))}
        wide = {at + i: new[i] for at, _, new in wide_ratio_verify.windows(True) for i in range(len(new))}
        base = off(0x53E01B68)
        differ = [hex(0x53E01B68 + i) for i in range(5044) if solo.get(base + i) != wide.get(base + i)]
        self.assertEqual(differ, ["0x53e0253c", "0x53e0253d", "0x53e0253e", "0x53e02540"])
        self.assertNotIn("实测", generate.CHECK_TITLES["wide_ratio_windows"].split("不代表")[0])


@unittest.skipUnless(IIIX.is_file(), "needs DARKSLIDE_IIIX_BIN")
class RealIIIx(unittest.TestCase):
    """IIIx: same windows, no camera has run them (handbook exception 3)."""

    @classmethod
    def setUpClass(cls):
        cls.official, cls.t = IIIX.read_bytes(), tpl.load("gr3x_v160.json")

    def test_saved_project_route(self):
        t = self.t
        for features, glow in ((dict(wide_ratio=True), False), (dict(wide_ratio=True, soft_glow=True), True)):
            with tempfile.TemporaryDirectory() as tmp:
                store = Store(tmp)
                pid = store.create_project("画幅", store.put_input(self.official), t, target_body="GR IIIx")["id"]
                store.put_draft(pid, {}, t, camera_features=features)
                output, manifest = gr3x_generation.run(store, store.save(pid, t), t, self.official)
            self.assertTrue(all(c["ok"] is True for c in manifest["checks"]))
            self.assertIn("wide_ratio_windows", [c["id"] for c in manifest["checks"]])
            self.assertIn("GR IIIx 上已适配、未实测", manifest["verification"])
            after = fwread.unpack(output)
            after = bytes(after[0] if isinstance(after, tuple) else after)
            for at, _, new in wide_ratio_verify.windows(glow):
                self.assertEqual(after[at:at + len(new)], new)

    def test_with_a_film_and_the_menu_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            pid = store.create_project("画幅", store.put_input(self.official), self.t, target_body="GR IIIx")["id"]
            store.put_draft(pid, {"8": {"film": "portra400"}}, self.t, menu_order=ORDER, camera_features=dict(wide_ratio=True, soft_glow=True))
            output, manifest = gr3x_generation.run(store, store.save(pid, self.t), self.t, self.official)
        self.assertTrue(all(c["ok"] is True for c in manifest["checks"]))
        self.assertIn("wide_ratio_windows", [c["id"] for c in manifest["checks"]])
        after = fwread.unpack(output)
        after = bytes(after[0] if isinstance(after, tuple) else after)
        for at, _, new in wide_ratio_verify.windows(True):
            self.assertEqual(after[at:at + len(new)], new)

    def test_hdf_body_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            pid = store.create_project("画幅", store.put_input(self.official), self.t, target_body="GR IIIx HDF")["id"]
            store.put_draft(pid, {}, self.t, camera_features=dict(wide_ratio=True))
            with self.assertRaisesRegex(generate.GenerateError, "还不支持 HDF 机身"):
                gr3x_generation.run(store, store.save(pid, self.t), self.t, self.official)

    def test_open(self):
        self.assertIs(models.capabilities(models.GR3X)["wide_ratio"], True)


if __name__ == "__main__":
    unittest.main()
