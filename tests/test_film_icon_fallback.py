"""Film icons on presets (web/app.js syncFilmIcon / filmIconReady), run with Node: a stand-in name plate
comes back as the user's icon once it loads; the user's own uploaded icon is never touched."""
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("film_icon_fallback_ui.cjs")


@unittest.skipUnless(shutil.which("node"), "needs Node.js")
class FilmIconFallback(unittest.TestCase):
    def test_stand_in_plates_come_back_own_icons_untouched(self):
        out = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PASS", out.stdout)


if __name__ == "__main__":
    unittest.main()
