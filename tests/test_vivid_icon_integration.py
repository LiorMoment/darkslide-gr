"""The Vivid fifth film position together with film icons on GR III 2.20 / GR IIIx 1.70 (saved projects,
version isolation, layouts), and the page's icon syncs on that position (run with Node). Never builds firmware."""
import copy
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from darkslide import film_requests, inplace_generation, iiix_prepare, models
from darkslide.store import Store, StoreError

SCRIPT = Path(__file__).with_name("film_icon_vivid_ui.cjs")
FIVE = {"2": "portra160", "8": "gold200", "9": "portra400", "10": "ektar100", "11": "5219"}


def icon_png():
    """A 40x40 icon as the page uploads it: opaque in the visible 38x36 window, clear outside."""
    rows = []
    for y in range(40):
        row = bytearray([0])
        for x in range(40):
            row += bytes((200, 40, 40, 255)) if 1 <= x <= 38 and 2 <= y <= 37 else bytes((0, 0, 0, 0))
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 40, 40, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


class SavedVividIcons(unittest.TestCase):
    def project(self, folder, model):
        store, template = Store(folder), models.template_for(model)
        with patch.object(store, "has_input", return_value=True):
            pid = store.create_project("vivid icons", model.input_sha, template)["id"]
        return store, template, pid

    def test_vivid_keeps_film_icon_stand_in_mark_and_own_icon_after_reopening(self):
        for model in models.NEW_220:
            with self.subTest(model=model.id), tempfile.TemporaryDirectory() as folder:
                store, template, pid = self.project(folder, model)
                asset = store.put_asset(pid, icon_png(), expect_icon=True)["asset"]
                film_icon = dict(asset=asset, style="image", film="portra160")
                for icon in (film_icon, dict(film_icon, fallback=True), dict(asset=asset, style="image")):
                    changes = {"2": dict(film="portra160", icon=icon)}
                    store.put_draft(pid, changes, template)
                    store.save(pid, template)
                    self.assertEqual(Store(folder).get_project(pid)["changes"], changes)
                # the stand-in mark needs a film mark and the value true, on Vivid as on the others
                for bad in (dict(asset=asset, style="image", fallback=True), dict(film_icon, fallback=False)):
                    with self.assertRaises(StoreError):
                        store.put_draft(pid, {"2": dict(film="portra160", icon=bad)}, template)

    def test_all_five_positions_with_icons_save_on_their_own_firmware_only(self):
        for model in models.NEW_220:
            with self.subTest(model=model.id), tempfile.TemporaryDirectory() as folder:
                store, template, pid = self.project(folder, model)
                asset = store.put_asset(pid, icon_png(), expect_icon=True)["asset"]
                changes = {key: dict(film=film, icon=dict(asset=asset, style="image", film=film)) for key, film in FIVE.items()}
                store.put_draft(pid, changes, template)
                store.save(pid, template)
                reopened = Store(folder).get_project(pid)
                self.assertEqual(reopened["changes"], changes)
                self.assertEqual(models.for_project(reopened), model)
                self.assertEqual(store.builds(), [])

    def test_a_user_lut_or_icon_from_the_other_versions_project_is_refused(self):
        cube = b"LUT_3D_SIZE 2\n" + b"".join(("%d %d %d\n" % (r, g, b)).encode() for b in (0, 1) for g in (0, 1) for r in (0, 1))
        with tempfile.TemporaryDirectory() as folder:
            store, t220, p220 = self.project(folder, models.GR3_220)
            t170 = models.template_for(models.GR3X_170)
            with patch.object(store, "has_input", return_value=True):
                p170 = store.create_project("iiix", models.GR3X_170.input_sha, t170)["id"]
            lut = store.put_film_lut(p220, cube, "own.cube")["id"]
            asset = store.put_asset(p220, icon_png(), expect_icon=True)["asset"]
            store.put_draft(p220, {"2": dict(film=lut, icon=dict(asset=asset, style="image"))}, t220)
            # a draft may hold it for the moment (drafts are checked loosely); saving, which 生成 starts with, refuses
            store.put_draft(p170, {"2": dict(film=lut)}, t170)
            with self.assertRaises(StoreError):
                store.save(p170, t170)
            with self.assertRaises(StoreError):
                store.validate_changes(p170, {"2": dict(film=lut)}, t170, strict=True)
            store.put_draft(p170, {}, t170)
            # icons are looked up in the project's own folder: refused at once
            with self.assertRaises(StoreError):
                store.put_draft(p170, {"2": dict(icon=dict(asset=asset, style="image"))}, t170)
            self.assertEqual(store._lut_model(p220), models.GR3_220)
            self.assertEqual(store._lut_model(p170), models.GR3X_170)

    def test_icon_marks_do_not_change_the_firmware_layout(self):
        # the layout comes from the films alone: an icon, its film mark or a stand-in mark changes nothing
        for model in models.NEW_220:
            module = iiix_prepare if models.is_iiix(model) else inplace_generation
            plain = {key: dict(film=film) for key, film in FIVE.items()}
            marked = copy.deepcopy(plain)
            for key, film in FIVE.items():
                marked[key]["icon"] = dict(asset="a" * 64, style="image", film=film, fallback=True)
            with self.subTest(model=model.id):
                self.assertEqual(module.layout_for(film_requests.from_changes(marked), model),
                                 module.layout_for(film_requests.from_changes(plain), model))
                self.assertEqual(list(film_requests.from_changes(marked)), list(film_requests.from_changes(plain)))


@unittest.skipUnless(shutil.which("node"), "needs Node.js")
class PageVividIcons(unittest.TestCase):
    def test_icon_syncs_on_the_vivid_position(self):
        out = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PASS", out.stdout)


if __name__ == "__main__":
    unittest.main()
