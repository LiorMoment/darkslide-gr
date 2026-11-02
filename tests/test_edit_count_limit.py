"""Changing everything at once is a legal request of 31 edits. Most of these run real requests against both the
generator and the verifier and stop at the official file; the slow one builds all 31 in memory and saves nothing."""
import hashlib
import os
import unittest
from pathlib import Path

from darkslide import composite_verify, compositor, film_requests, fwread, menu_order, template, wide_ratio

LAYOUT = 'inplace_history_cache_exit_vivid5_v1'
FILMS = {'2': 'portra160', '8': 'gold200', '9': 'portra400', '10': 'ektar100', '11': '5219'}
STILL = [2, 8, 9, 10, 11, 1, 7, 12, 3, 4, 5, 6]


def icons(n):
    return [dict(kind='icon', slot=(i % 12) + 1, icon_id=i + 1, pixels=b'') for i in range(n)]


def names(n):
    return [dict(kind='name', slot=(i % 11) + 2, string_id=i + 1, text='x') for i in range(n)]


def films():
    return list(film_requests.from_changes({k: {'film': v} for k, v in FILMS.items()}))


def grain():
    return dict(kind='grain_menu', slot=12, mode=22)


def order():
    return menu_order.edit_for({'schema': 1, 'still_slots': STILL, 'video_slots': menu_order.video_for(STILL)})


def everything():
    """The real 31: 12 icons, 11 names (Standard takes none), 1 grain, 5 films, the menu order and the wide ratio."""
    slots = template.load()['slots'][:12]
    real_icons = [dict(kind='icon', slot=s['no'], icon_id=s['icon_id'], pixels=bytes(6400)) for s in slots]
    real_names = [dict(kind='name', slot=s['no'], string_id=s['string_id'], text='Abc', menu_language='zh-CN') for s in slots[1:]]
    return real_icons + real_names + [grain()] + films() + [order(), wide_ratio.edit(glow=True)]


class EditCount(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = template.load()

    def generator_error(self, edits):
        with self.assertRaises(Exception) as caught:
            compositor.build_inplace_composite(b'', edits, self.template)
        return str(caught.exception)

    def verifier_failures(self, edits):
        checks = composite_verify.verify(b'', b'', edits, self.template, film_layout=LAYOUT)
        checks = checks[0] if isinstance(checks, tuple) else checks
        return ' | '.join(str(c.get('detail')) for c in checks if not c.get('ok'))

    def test_shape(self):
        self.assertEqual(len(everything()), 31)

    def test_everything_reaches_the_official_file_check(self):
        # Only the checks before the official file. The full run below covers the rest.
        self.assertEqual(self.generator_error(everything()), 'fixed official input required')
        self.assertEqual(self.verifier_failures(everything()), 'immutable official input')

    def test_one_more_is_refused_on_both_sides(self):
        edits = everything() + icons(1)
        self.assertIn('too many edits', self.generator_error(edits))
        self.assertIn('too many edits', self.verifier_failures(edits))

    def test_the_total_limit_itself_is_checked_on_both_sides(self):
        # 29 counted edits plus three uncounted ones: only the total of 31 stops this request.
        edits = everything() + [dict(kind='soft_glow', version='v22.28')]
        self.assertEqual(len(edits), 32)
        self.assertIn('too many edits', self.generator_error(edits))
        self.assertIn('too many edits', self.verifier_failures(edits))

    def test_thirty_counted_edits_are_refused_on_both_sides(self):
        edits = icons(13) + names(12) + films()
        self.assertEqual(len(edits), 30)
        self.assertIn('too many edits', self.generator_error(edits))
        self.assertIn('too many edits', self.verifier_failures(edits))

    def test_twenty_five_regular_edits_are_refused_on_both_sides(self):
        edits = icons(13) + names(12)
        self.assertIn('too many regular edits', self.generator_error(edits))
        self.assertIn('too many regular edits', self.verifier_failures(edits))

    def test_duplicates_and_conflicts_are_refused_by_the_generator(self):
        glow = dict(kind='soft_glow', version='v22.28')
        for edits, message in (([order(), order()], 'duplicate menu order'),
                               ([wide_ratio.edit(glow=True), glow], 'wide ratio carries soft glow'),
                               (films()[:4] + films()[:1], '同一位置')):
            with self.subTest(message=message):
                self.assertIn(message, self.generator_error(edits))

    def test_wide_and_glow_together_are_refused_by_the_verifier(self):
        edits = [wide_ratio.edit(glow=True), dict(kind='soft_glow', version='v22.28')]
        self.assertIn('wide ratio request', self.verifier_failures(edits))

    @unittest.skipUnless(os.environ.get('DARKSLIDE_OFFICIAL_BIN'), 'needs the official file')
    def test_duplicates_are_refused_by_the_verifier(self):
        # These checks come after the official file, so they need the real one. Nothing is generated.
        official = Path(os.environ['DARKSLIDE_OFFICIAL_BIN']).read_bytes()
        for edits, layout, message in (([order(), order()], 'legacy_arena_v1', 'duplicate menu order'),
                                       (films()[:4] + films()[:1], LAYOUT, 'film edit')):
            with self.subTest(message=message):
                result = dict(output=official, output_sha256=hashlib.sha256(official).hexdigest())
                checks = composite_verify.verify(official, result, edits, self.template, film_layout=layout)
                checks = checks[0] if isinstance(checks, tuple) else checks
                self.assertIn(message, ' | '.join(str(c.get('detail')) for c in checks if not c.get('ok')))

    @unittest.skipUnless(os.environ.get('DARKSLIDE_SLOW_TESTS') and os.environ.get('DARKSLIDE_OFFICIAL_BIN'), 'needs the official file and DARKSLIDE_SLOW_TESTS')
    def test_all_31_build_and_pass_the_whole_verifier(self):
        # The build stays in memory and is never saved.
        official = Path(os.environ['DARKSLIDE_OFFICIAL_BIN']).read_bytes()
        unpacked = fwread.unpack(official)
        payload = fwread.Payload(unpacked[0] if isinstance(unpacked, tuple) else unpacked)
        edits = everything()
        for edit in edits:
            if edit['kind'] == 'icon':  # the factory icon with its colours changed, so the transparent edge stays legal
                native = bytearray(payload.icon_pixels(edit['icon_id'])[1])
                native[0::4] = bytes(255 - v for v in native[0::4])
                edit['pixels'] = bytes(native)
        result = compositor.build_inplace_composite(official, edits, self.template)
        checks = composite_verify.verify(official, result, edits, self.template, film_layout=LAYOUT)
        checks = checks[0] if isinstance(checks, tuple) else checks
        self.assertEqual([c['id'] for c in checks if not c.get('ok')], [])
        self.assertTrue({'input', 'output_sha', 'payload_sha'} <= {c['id'] for c in checks})
        # The same verifier must notice one edit that was asked for and not made.
        missing = composite_verify.verify(official, result, edits[:-3] + edits[-2:], self.template, film_layout=LAYOUT)
        missing = missing[0] if isinstance(missing, tuple) else missing
        self.assertTrue([c for c in missing if not c.get('ok')])

    def test_empty_is_not_called_too_many(self):
        self.assertIn('no requested changes', self.generator_error([]))


if __name__ == '__main__':
    unittest.main()
