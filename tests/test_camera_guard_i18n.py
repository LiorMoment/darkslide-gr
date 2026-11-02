"""Check the real UI translates temporary guard messages; no firmware or device."""
import shutil, subprocess, unittest
from pathlib import Path

@unittest.skipUnless(shutil.which("node"), "needs Node.js")
class GuardTranslation(unittest.TestCase):
 def test_guard_messages(self):
  r=subprocess.run(["node",str(Path(__file__).with_name("camera_guard_i18n_ui.cjs"))],capture_output=True,text=True,timeout=30)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)

if __name__ == "__main__": unittest.main()
