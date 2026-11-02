"""IIIx current-install numeric preview; archived RAW snapshots stay untouched.

This validates scalar numeric limits, not firmware hooks or camera behaviour.
The generator and its independent readback remain the authority for outputs.
"""
import copy
import hashlib
from . import film_fit, film_luts, film_profiles, models, photo_recipe
from .gr3x_inplace_support import _numeric, expand_knots

LAYOUT = 'gr3x160-knots33-preview-v1'


def numeric_sha(profile):
    return hashlib.sha256(photo_recipe.CORE.pack(*(v for field in photo_recipe.FIELDS
        for row in profile[field] for v in row))).hexdigest()


def _iiix(firmware):
    """GR IIIx 1.70 data (built-in films relabelled, fits on 1.70 projects) or 1.60."""
    return models.GR3X_170 if firmware == models.GR3X_170.firmware else models.GR3X


def installed_profile(profile, limits):
    original = film_profiles.validate_profile(profile, profile['id'], _iiix(profile.get('firmware')).id)
    knots, errors = _numeric(original, limits)
    applied = copy.deepcopy(original)
    applied['curves_u14'] = [list(expand_knots(c)) for c in knots]
    return applied, dict(layout=LAYOUT, points=33, numeric_installable=True,
        source_numeric_sha256=numeric_sha(original), applied_numeric_sha256=numeric_sha(applied),
        compression_errors=list(errors), camera_verified=False,
        scope='current-install numeric constraints only')


def readiness(profile, limits):
    try:
        _, result = installed_profile(profile, limits)
        return result
    except ValueError as error:
        return dict(layout=LAYOUT, numeric_installable=False, reason=str(error),
                    camera_verified=False, scope='current-install numeric constraints only')


def preview(base, profile, limits):
    applied, info = installed_profile(profile, limits)
    cube = film_fit.camera_cube(base, applied)
    return (('# IIIx current installation: 33-node reconstruction; approximate, not camera verified.\n'
             '# Applied numeric SHA256: ' + info['applied_numeric_sha256'] + '\n').encode() + cube)


def installed_fit_report(data, contract, profile, original_report, limits):
    """Score reconstructed values; keep the uncompressed fit score as provenance."""
    film_fit.contract_model(contract, _iiix(contract.get('firmware')).id)
    applied, info = installed_profile(profile, limits)
    _, size, rows = film_luts.parse_cube(data)
    fit = film_fit.fitlib
    scores = fit.evaluate(contract['values'],
        [[v / 8192 for v in row] for row in applied['look_matrix_q13']],
        applied['curves_u14'],
        [[v / 512 for v in row] for row in applied['post_matrix_q512']],
        fit.target_of(fit.Cube(size, rows)))
    result = copy.deepcopy(original_report)
    result.update({k: round(scores[k], 2) for k in ('de_mean','de_p95','de_max','grey_de_mean')})
    result.update(level=film_fit.level_of(scores['de_mean']), samples=scores['samples'],
        current_install=info, source_fit=copy.deepcopy(original_report),
        note='按最终 33 节点重建曲线计算的相机模型色差；不是实拍色差。')
    return result
