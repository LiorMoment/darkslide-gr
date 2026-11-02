"""Server messages behind a status mark ("✕ " + reason) translate whole (web/i18n.js), run with Node."""
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("i18n_status_mark_ui.cjs")


@unittest.skipUnless(shutil.which("node"), "needs Node.js")
class StatusMarkTranslation(unittest.TestCase):
    def test_marked_message_translates_whole(self):
        out = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PASS", out.stdout)


if __name__ == "__main__":
    unittest.main()
