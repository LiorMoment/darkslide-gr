"""Overlapping film icon syncs (web/app.js), run with Node: newest wins, a late failure never puts the
stand-in plate back, a load reported mid-sync is not missed, other projects and own icons are untouched."""
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("film_icon_race_ui.cjs")


@unittest.skipUnless(shutil.which("node"), "needs Node.js")
class FilmIconRace(unittest.TestCase):
    def test_overlapping_syncs(self):
        out = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PASS", out.stdout)


if __name__ == "__main__":
    unittest.main()
