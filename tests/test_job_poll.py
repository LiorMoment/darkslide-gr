"""The page's job polling (web/app.js apiJob/pollJob), run with Node against a scripted server."""
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("job_poll_ui.cjs")


@unittest.skipUnless(shutil.which("node"), "needs Node.js")
class JobPolling(unittest.TestCase):
    def test_page_polls_a_job_and_throws_the_plain_error(self):
        out = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("PASS", out.stdout)


if __name__ == "__main__":
    unittest.main()
