"""Would this LUT fit into the camera? A real fit, nothing stored, nothing written.

Shared by the live check after grading (server) and the per-film slider ranges
(tools/build_grade_limits.py), so the two can never disagree. "writable" means the
fit succeeded and every number the camera would hold stays inside RICOH's own range
(the same rules the write path enforces; for GR IIIx also its 33-knot rebuild).
"""
from . import film_fit, models
from .film_requests import FilmRequestError

CHECK_ID = "user_lut_000000000000"


def check_cube(data, model, contract, envelope):
    """data: .cube bytes. Returns dict(writable, violations, reason, level, de_mean)."""
    try:
        profile, report = film_fit.fit_cube(data, CHECK_ID, contract, envelope, model_id=model.id)
        if models.is_iiix(model):
            from .gr3x_camera_preview import installed_fit_report
            report = installed_fit_report(data, contract, profile, report, envelope)
    except (film_fit.FilmFitError, FilmRequestError, ValueError) as exc:
        return dict(writable=False, violations=[], reason=str(exc), level=None, de_mean=None)
    violations = film_fit.envelope_violations(profile, envelope)
    return dict(writable=not violations, violations=violations,
                reason=None if not violations else "超出了理光相机原厂的范围：" + violations[0],
                level=report.get("level"), de_mean=report.get("de_mean"))
