"""Turn a user's .cube into GR III film camera parameters (schema 1) plus a fit report.

The base gamma comes from base_curve_python.base_curve() on the user's own official
firmware at run time; it is Ricoh's data, so only its SHA-256 is stored.
"""
import hashlib
import json
import time

from . import film_luts, models
from ._vendor import gr3_lut_fit as fitlib
from ._vendor.gr3_fit_numeric import q13_matrix
from .film_profiles import validate_profile
from .film_requests import FILM_LAYOUT, FilmRequestError

FITTER_ID = "darkslide-film-fit-v2"

BASE_MODELS = {models.GR3.id: "gr3-210-upstream-neutral-stubbed-v2",
               models.GR3X.id: "gr3x-160-upstream-neutral-stubbed-v2",
               # Same emulated Standard base (identical values, checked 2026-10-26), relocated addresses.
               models.GR3_220.id: "gr3-210-upstream-neutral-stubbed-v2",
               models.GR3X_170.id: "gr3x-160-upstream-neutral-stubbed-v2"}
IIIX_FITTER_ID = "darkslide-film-fit-v2-iiix-knots33"


def contract_model(contract, model_id=None):
    """Bind fitting to the runtime reader's complete firmware identity.

    This checks the handoff, not the physical accuracy of the camera model.
    Only base_curve.base_curve() should supply this contract in production.
    """
    if type(contract) is not dict:
        raise FilmFitError("相机基础模型格式不符")
    found = next((m for m in models.ALL
                  if m.input_sha == contract.get("official_sha256")), None)
    if found is None or (model_id is not None and found.id != model_id):
        raise FilmFitError("拟合目标与官方原件身份不匹配")
    expected = dict(schema=1, model=found.name, firmware=found.firmware,
                    payload_sha256=found.payload_sha, model_id=BASE_MODELS[found.id],
                    output_normalization_divisor=16384, value_range=[0, 16384],
                    location_verified=True, emulated_original_arm_instructions=True,
                    native_effect_verified=False)
    for key, value in expected.items():
        actual = contract.get(key)
        if type(actual) is not type(value) or actual != value:
            raise FilmFitError("相机基础模型身份或范围不符：" + key)
    base = contract.get("values")
    if (type(base) is not list or len(base) != 256 or
            any(type(v) is not int or not 0 <= v <= 16384 for v in base)):
        raise FilmFitError("相机基础曲线格式不符")
    return found


# How close the camera can get, from the mean CIEDE2000 on fresh samples.
LEVELS = ((2.0, "高"), (4.0, "中"))


class FilmFitError(ValueError):
    pass


def level_of(de_mean):
    for limit, name in LEVELS:
        if de_mean < limit:
            return name
    return "低"


def stored_curves(profile):
    """The three curves as the camera will hold them: the four-film layout keeps 33
    knots per curve and expands them to 256 points when used (lossy)."""
    if FILM_LAYOUT != "four_curve_v1":
        return [list(c) for c in profile["curves_u14"]], 0
    if profile.get("model") == models.GR3X.name:  # GR IIIx 1.60 and 1.70
        from .gr3x_inplace_support import expand_knots, fit_knots
    else:
        from .film_four_validation import expand_knots, fit_knots
    out, worst = [], 0
    for curve in profile["curves_u14"]:
        knots, err = fit_knots(list(curve))
        out.append(list(expand_knots(knots)))
        worst = max(worst, err["max_abs_u14"])
    return out, worst


# Factory envelope (user, 2026-10-14): a profile whose numbers go beyond what RICOH's own
# presets use is refused for writing, never clamped. The envelope is read at run time from
# the user's firmware (factory_envelope, fixed by the firmware side); keys it lacks are skipped.
ENVELOPE_LABELS = dict(look="前矩阵", post="后矩阵", curve="曲线陡度")


# Plain wording for the fitting library's own errors (its text stays English).
FIT_REASONS = {
    "post-curve matrix too large": "颜色变化太大（比如完全去色、反相或来回起伏的曲线），相机的颜色矩阵装不下",
    "Q13 repair exceeds signed16": "颜色矩阵的数值太大，相机存不下",
    "finite representable matrix required": "算出的颜色矩阵无效",
    "source matrix must preserve white": "算出的颜色矩阵会改变白色",
    "expected 3 matrix rows": "颜色矩阵格式不对",
}


def envelope_violations(profile, env):
    """Plain-language list of where the profile leaves the factory envelope ([] = inside)."""
    if not env:
        return []
    out = []

    def outside(label, value, lo, hi):
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            out.append("%s %s 超出原厂范围（%s～%s）" % (label, value, "—" if lo is None else lo, "—" if hi is None else hi))

    look, post = profile["look_matrix_q13"], profile["post_matrix_q512"]
    for v in (v for row in look for v in row):
        outside("前矩阵系数", v, env.get("look_min"), env.get("look_max"))
    for row in look:
        outside("前矩阵行绝对值和", sum(abs(v) for v in row), None, env.get("look_row_abs_max"))
    for v in (v for row in post for v in row):
        outside("后矩阵系数", v, env.get("post_min"), env.get("post_max"))
    for c in range(3):
        outside("后矩阵列绝对值和", sum(abs(post[r][c]) for r in range(3)), None, env.get("post_col_abs_max"))
    for curve in stored_curves(profile)[0]:
        steps = [b - a for a, b in zip(curve, curve[1:])]
        outside("曲线最大陡度", max(steps), None, env.get("curve_step_max"))
        outside("曲线最小陡度", min(steps), env.get("curve_step_min"), None)
        outside("曲线起点", curve[0], env.get("curve_first_min"), env.get("curve_first_max"))
        outside("曲线终点", curve[-1], env.get("curve_last_min"), env.get("curve_last_max"))
    seen, unique = set(), []
    for item in out:
        key = item.split(" ")[0]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def camera_cube(base, profile, size=17, *, exact=False):
    """What the fitted parameters would give, as a .cube on sRGB-encoded input."""
    M = [[v / 8192 for v in row] for row in profile["look_matrix_q13"]]
    C = [[v / 512 for v in row] for row in profile["post_matrix_q512"]]
    curves = profile["curves_u14"] if exact or profile.get("model") == models.GR3X.name else stored_curves(profile)[0]
    n1 = size - 1
    lines = ['TITLE "camera model %s"' % profile["id"],
             "# Darkslide camera-model preview (approximate, base gamma from the user's own firmware).",
             "LUT_3D_SIZE %d" % size]
    for b in range(size):
        for g in range(size):
            for r in range(size):
                lin = [fitlib.srgb_decode(v / n1) for v in (r, g, b)]
                out = fitlib.model(base, M, curves, lin, C)
                lines.append("%.5f %.5f %.5f" % tuple(out))
    return ("\n".join(lines) + "\n").encode()


def fit_cube(data, ident, contract, envelope=None, *, model_id=None):
    """data: .cube bytes; contract: result of base_curve_python.base_curve(). Returns (profile, report)."""
    try:
        _, size, rows = film_luts.parse_cube(data)
    except film_luts.FilmLutError as exc:
        raise FilmFitError(str(exc)) from None
    model = models.get(model_id)
    if models.is_iiix(model):contract_model(contract, model.id)
    fitter = IIIX_FITTER_ID if models.is_iiix(model) else FITTER_ID
    base = contract["values"]
    if len(base) != 256 or contract.get("output_normalization_divisor") != 16384:
        raise FilmFitError("相机基础曲线格式不符")
    started = time.monotonic()
    target = fitlib.target_of(fitlib.Cube(size, rows))
    bounds = {k: envelope[k] for k in ("look_min", "look_max", "look_row_abs_max") if envelope and k in envelope} or None
    M, curves, C = fitlib.fit(base, target, bounds=bounds)
    # Perceptual refit (lab_fit): an RGB-only fit leaves colours a little too weak.
    M, curves, C = fitlib.refine_perceptual(base, target, M, curves, C, bounds=bounds)
    try:
        look = q13_matrix(M)
        post = fitlib.quantize_post_q512(C)
    except ValueError as exc:
        reason = FIT_REASONS.get(str(exc), str(exc))
        raise FilmFitError("这个 LUT 超出相机能表达的范围：%s" % reason) from None
    profile = dict(
        schema=1, id=ident, model=model.name, firmware=model.firmware,
        look_matrix_q13=look, curves_u14=curves, post_matrix_q512=post,
        provenance=dict(
            kind="user_lut", fitter=fitter, source_sha256=hashlib.sha256(data).hexdigest(),
            target_input_encoding="srgb", target_ev=0.0, mid_index=fitlib.MID_INDEX,
            base_model_id=contract["model_id"], official_sha256=contract["official_sha256"],
            base_values_sha256=hashlib.sha256(json.dumps(base).encode()).hexdigest(),
            absolute_exposure_calibrated=False,
            algorithm="port of gr3-film-mods cube_fit/cca1_fit (MIT, idontknownihil), modified by LiorMoment"),
        native_effect_verified=False)
    try:
        if models.is_iiix(model):
            profile.update(official_sha256=model.input_sha, base_model_id=contract["model_id"])
        profile = validate_profile(profile, ident, model.id)
    except FilmRequestError as exc:
        raise FilmFitError(str(exc)) from None
    # Curve steps inside the factory range, measured on the curves the camera will hold.
    if envelope:
        smax, smin = envelope.get("curve_step_max"), envelope.get("curve_step_min")
        for margin in (0, 4, 12, 24, 48):
            # only steepness can be limited here; a start/end outside the range is reported, never moved
            bad = [v for v in envelope_violations(profile, envelope) if v.startswith("曲线最")]
            if not bad:
                break
            profile["curves_u14"] = [fitlib.limit_steps(c, None if smax is None else smax - margin,
                                                        None if smin is None else smin + margin)
                                     for c in profile["curves_u14"]]
            curves = profile["curves_u14"]
    qM = [[v / 8192 for v in row] for row in look]
    qC = [[v / 512 for v in row] for row in post]
    kept, knots_u14 = stored_curves(profile)
    scores = fitlib.evaluate(base, qM, kept, qC, target)
    report = dict(
        level=level_of(scores["de_mean"]),
        de_mean=round(scores["de_mean"], 2), de_p95=round(scores["de_p95"], 2),
        de_max=round(scores["de_max"], 2), grey_de_mean=round(scores["grey_de_mean"], 2),
        samples=scores["samples"], seconds=round(time.monotonic() - started, 1),
        layout=FILM_LAYOUT, storage_max_u14=knots_u14,
        base_model_id=contract["model_id"], fitter=fitter,
        note="色差按 CIEDE2000、在相机模型上计算，已含相机里 33 节点存储曲线的误差；中灰 7.8 的曝光假设未实拍校准，实际效果以写入后的实拍为准。")
    return profile, report
