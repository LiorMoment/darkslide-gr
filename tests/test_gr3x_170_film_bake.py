"""调色 (film-bake) on GR IIIx 1.70: the frozen source and the fitted result are checked against the project's
own IIIx model, not always against 1.60. 1.60 stays byte-for-byte as before, and a profile or packet of
the other firmware (or of a non-IIIx model) is still refused. Pure tests: built-in film data only."""
import copy
import unittest

from darkslide import film_profiles, gr3x_recipe_lineage as lineage, models, photo_recipe
from darkslide.film_requests import FilmRequestError
from darkslide.store import Store

IIIX = (models.GR3X, models.GR3X_170)


class Packets(unittest.TestCase):
    def test_1_60_default_unchanged(self):
        p = film_profiles.load_profile('gold200', models.GR3X.id)
        packet = photo_recipe.encode(p, 8)
        self.assertEqual(packet, photo_recipe.encode(p, 8, models.GR3X.id))
        self.assertEqual(photo_recipe.decode(packet), photo_recipe.decode(packet, models.GR3X.id))

    def test_each_iiix_firmware_round_trips(self):
        for model in IIIX:
            with self.subTest(model=model.id):
                p = film_profiles.load_profile('portra400', model.id)
                snap = photo_recipe.decode(photo_recipe.encode(p, 9, model.id), model.id)
                self.assertEqual((snap['profile']['model'], snap['profile']['firmware']), (model.name, model.firmware))
                for k in photo_recipe.FIELDS:
                    self.assertEqual(snap['profile'][k], p[k])

    def test_other_firmware_is_refused(self):
        for model, other in ((models.GR3X, models.GR3X_170), (models.GR3X_170, models.GR3X)):
            with self.subTest(model=model.id, other=other.id):
                p = film_profiles.load_profile('gold200', model.id)
                with self.assertRaises(FilmRequestError):          # profile of 1.60 claimed as 1.70 and back
                    photo_recipe.encode(p, 8, other.id)
                packet = photo_recipe.encode(p, 8, model.id)
                with self.assertRaisesRegex(ValueError, '不适用于当前 IIIx 处理链'):
                    photo_recipe.decode(packet, other.id)

    def test_1_70_profile_still_refused_by_default(self):
        p = film_profiles.load_profile('gold200', models.GR3X_170.id)
        with self.assertRaises(FilmRequestError):
            photo_recipe.encode(p, 8)

    def test_non_iiix_model_refused(self):
        p = film_profiles.load_profile('gold200', models.GR3_220.id)
        for model in (models.GR3, models.GR3_220):
            with self.subTest(model=model.id):
                with self.assertRaisesRegex(ValueError, '不适用于当前 IIIx 处理链'):
                    photo_recipe.encode(p, 8, model.id)


class Lineage(unittest.TestCase):
    # capture() reads the project store only for user / photo LUT sources; built-in films and a 调色 with no film
    # under it need no store, so these pure tests pass none.
    def bake(self, film, model, grade):
        return dict(film=film, grade=grade, recipe_source=lineage.capture(None, None, film, model))

    def test_capture_attach_verify_both_firmwares(self):
        for model in IIIX:
            for film, grade in (('gold200', {'exp': 10}), (None, {'exp': -10})):
                with self.subTest(model=model.id, film=film):
                    bake = self.bake(film, model, grade)
                    source = bake['recipe_source']['source']
                    if film:
                        self.assertEqual(source['id'], film)
                        built_in = film_profiles.load_profile(film, model.id)
                        want = photo_recipe.decode(photo_recipe.encode(built_in, 8, model.id), model.id)['numeric_sha256']
                        self.assertEqual(source['numeric_sha256'], want)
                    else:
                        self.assertIsNone(source)
                    fitted = copy.deepcopy(film_profiles.load_profile('ektar100', model.id))
                    fitted['id'] = 'user_lut_0123456789ab'
                    fitted.update(official_sha256=model.input_sha, base_model_id='gr3x-160-upstream-neutral-stubbed-v2')
                    out = lineage.attach(fitted, bake, model)
                    self.assertEqual(out['recipe_derivation']['grade'], grade)
                    lineage.verify(out, bake, model)
                    other = models.GR3X_170 if model == models.GR3X else models.GR3X
                    with self.assertRaises((ValueError, FilmRequestError)):
                        lineage.verify(out, bake, other)

    def test_1_70_built_in_capture_uses_1_70_data(self):
        src = lineage.capture(None, None, 'gold200', models.GR3X_170)['source']
        self.assertEqual(src['id'], 'gold200')
        with self.assertRaises(FilmRequestError):           # the old fixed-1.60 capture on a 1.70 profile path
            photo_recipe.encode(film_profiles.load_profile('gold200', models.GR3X_170.id), 8, models.GR3X.id)

    def test_summary_checks_against_the_project_model(self):
        bake = self.bake(None, models.GR3X_170, {'exp': 5})
        p = copy.deepcopy(film_profiles.load_profile('gold200', models.GR3X_170.id))
        p['id'] = 'user_lut_0123456789ab'
        p.update(official_sha256=models.GR3X_170.input_sha, base_model_id='gr3x-160-upstream-neutral-stubbed-v2')
        p = lineage.attach(p, bake, models.GR3X_170)
        entry = dict(name='x', size=17, sha256='0' * 64, imported_at='t', profile=p, fit={}, bake=bake)
        self.assertTrue(Store._lut_summary('user_lut_0123456789ab', entry, models.GR3X_170)['fitted'])
        old = Store._lut_summary('user_lut_0123456789ab', entry, models.GR3X)   # checked as 1.60: needs a refit
        self.assertFalse(old['fitted'])
        self.assertTrue(old['needs_refit_reason'])


if __name__ == '__main__':
    unittest.main()
