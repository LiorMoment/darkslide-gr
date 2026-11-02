"""The camera menu language preselected for a new project (web/app.js guessMenuLanguage), run with Node:
the interface language first, the browser's languages only as a fallback."""
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("menu_language_default_ui.cjs")


@unittest.skipUnless(shutil.which("node"), "needs Node.js")
class MenuLanguageDefault(unittest.TestCase):
    def test_interface_language_first_browser_as_fallback(self):
        out = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PASS", out.stdout)


if __name__ == "__main__":
    unittest.main()
