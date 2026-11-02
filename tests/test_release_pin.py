"""The App's own entry must rebuild the package the user tested on a camera on 2026-11-02, byte for byte.
Needs the official GR III 2.10 file (DARKSLIDE_OFFICIAL_BIN) and DARKSLIDE_SLOW_TESTS=1; the build is only held in memory."""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from darkslide import generate, menu_order, template
from darkslide.store import Store

TESTED = 'c472567d4078c8eea9a53affaa8832895024f53890bb84a3135df1ca53b9971e'
FILMS = {2: 'portra160', 8: 'gold200', 9: 'portra400', 10: 'ektar100', 11: '5219'}
STILL = [8, 9, 10, 11, 1, 2, 3, 4, 5, 6, 12, 7]


@unittest.skipUnless(os.environ.get('DARKSLIDE_SLOW_TESTS') and os.environ.get('DARKSLIDE_OFFICIAL_BIN'), 'needs the official file and DARKSLIDE_SLOW_TESTS')
class CameraTestedBuild(unittest.TestCase):
    def test_rebuilds_the_tested_package(self):
        official = Path(os.environ['DARKSLIDE_OFFICIAL_BIN']).read_bytes()
        t = template.load()
        order = {'schema': 1, 'still_slots': STILL, 'video_slots': menu_order.video_for(STILL)}
        with tempfile.TemporaryDirectory() as tmp:
            s = Store(tmp)
            pid = s.create_project('pin', s.put_input(official), t, target_body='GR III')['id']
            s.put_draft(pid, {str(k): {'film': v} for k, v in FILMS.items()}, t, menu_order=order,
                        camera_features={'wide_ratio': True, 'soft_glow': True})
            output, manifest = generate.run_inplace(s, s.save(pid, t), t, official)
        self.assertEqual(hashlib.sha256(output).hexdigest(), TESTED)
        self.assertTrue(all(c['ok'] is True for c in manifest['checks']))


if __name__ == '__main__':
    unittest.main()
