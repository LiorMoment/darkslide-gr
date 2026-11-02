"""Projects opened out of order, 生成 and film icon syncs (web/app.js), run with Node: an overtaken opening
stops, 生成 waits for every icon sync and holds new ones, no uploads into a project opened later."""
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("film_icon_project_ui.cjs")


@unittest.skipUnless(shutil.which("node"), "needs Node.js")
class FilmIconProject(unittest.TestCase):
    def test_openings_generation_and_uploads(self):
        out = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PASS", out.stdout)


if __name__ == "__main__":
    unittest.main()
