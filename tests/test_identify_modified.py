"""A modified copy of an official firmware (for example an earlier DARKSLIDE GR build)
is named as such, for the right camera, with a pointer to the official download."""

import hashlib
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from darkslide import identify as I, template

GR3 = Path(os.environ.get("DARKSLIDE_OFFICIAL_BIN", "/nonexistent/fwdc239b.bin"))
GR3X = Path(os.environ.get("DARKSLIDE_IIIX_BIN", "/nonexistent/fwdc243b.bin"))


class Rules(unittest.TestCase):
    def setUp(self):
        self.t = template.load()

    def test_known_header_names_the_camera(self):
        header = b"H" * 128
        table = {hashlib.sha256(header).hexdigest(): ("GR IIIx", "1.60", "gr3x_v160.zip")}
        with patch.object(I, "MODIFIED_HEADERS", table):
            r = I.identify(header + b"changed body", "fwdc243b.bin", self.t, [])
        self.assertEqual(r["kind"], "rejected")
        self.assertIn("改过的 GR IIIx 1.60 固件", r["reason"])
        self.assertIn("gr3x_v160.zip", r["reason"])

    def test_unknown_header_keeps_the_old_wording(self):
        r = I.identify(b"x" * 300, "fwdc239b.bin", self.t, [])
        self.assertEqual(r["kind"], "rejected")
        self.assertNotIn("改过的", r["reason"])

    def test_unknown_file_lists_every_supported_version(self):
        for name in ("fwdc243b.bin", "IMG_0001.JPG"):
            r = I.identify(b"x" * 300, name, self.t, [])
            self.assertEqual(r["kind"], "rejected")
            self.assertIn("GR III 2.10 / 2.20 · GR IIIx 1.60 / 1.70", r["reason"])

    def test_look_alike_names_the_matching_version_not_the_default(self):
        from darkslide import models
        for m in models.ALL:
            r = I.identify(b"x" * m.input_size, m.filename, self.t, [])
            self.assertIn("像 %s " % m.name, r["reason"])
            self.assertIn("可能被改过", r["reason"])

    def test_own_build_still_opens_its_project(self):
        data = b"H" * 128 + b"build"
        r = I.identify(data, "fwdc239b.bin", self.t, [dict(output_sha256=I.digest_of(data), project_id="p_000000000000")])
        self.assertEqual(r["kind"], "generated")


class RealFiles(unittest.TestCase):
    def check(self, path, model, firmware, archive):
        if not path.exists():
            self.skipTest("official firmware not available on this machine")
        raw = path.read_bytes()
        t = template.load()
        modified = raw[:4096] + bytes([raw[4096] ^ 0xFF]) + raw[4097:]
        r = I.identify(modified, path.name, t, [])
        self.assertEqual(r["kind"], "rejected")
        self.assertIn("改过的 %s %s 固件" % (model, firmware), r["reason"])
        self.assertIn(archive, r["reason"])

    def test_gr3(self):
        self.check(GR3, "GR III", "2.10", "gr3_v210.zip")
        if GR3.exists():
            self.assertEqual(I.identify(GR3.read_bytes(), "fwdc239b.bin", template.load(), [])["kind"], "official")

    def test_gr3x(self):
        self.check(GR3X, "GR IIIx", "1.60", "gr3x_v160.zip")


if __name__ == "__main__":
    unittest.main()
