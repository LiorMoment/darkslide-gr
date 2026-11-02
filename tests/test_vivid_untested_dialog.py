"""A film on Vivid asks for the 未实测 confirmation on every firmware (web/app.js generateNow), run with Node."""
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("vivid_untested_dialog_ui.cjs")


@unittest.skipUnless(shutil.which("node"), "needs Node.js")
class VividUntestedDialog(unittest.TestCase):
    def test_asks_on_every_firmware(self):
        out = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PASS", out.stdout)


if __name__ == "__main__":
    unittest.main()
