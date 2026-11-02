"""A picture the browser shows but cannot decode to pixels says 「图片打不开」 (web/pixels.js), run with Node."""
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("pixels_decode_error_ui.cjs")


@unittest.skipUnless(shutil.which("node"), "needs Node.js")
class PixelsDecodeError(unittest.TestCase):
    def test_undecodable_picture_says_it_cannot_be_opened(self):
        out = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PASS", out.stdout)


if __name__ == "__main__":
    unittest.main()
