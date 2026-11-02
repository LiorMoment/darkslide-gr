"""Target body (ordinary / HDF) for a project: validation and read-only reports.

The firmware package names only its family; ordinary and HDF bodies share it.
The body is therefore the user's explicit declaration, never inferred. This
module only reports: it never writes CollaboMode, the filter type or any
firmware byte, and nothing here approves compatibility or replaces device tests.
Protected windows and the shutdown route table come from hdf_guard.
"""
from . import hdf_body_support, hdf_guard, shared_shutdown

BODIES = ("GR III", "GR III HDF", "GR IIIx", "GR IIIx HDF")
UNSPECIFIED = "机身版本未指定"


class BodyTargetError(ValueError):
    pass


def _need(ok, message):
    if not ok:
        raise BodyTargetError(message)


def validate(value):
    """None (old project, not declared) or one of BODIES; no default either way."""
    _need(value is None or (type(value) is str and value in BODIES),
          "机身版本只能是 GR III、GR III HDF、GR IIIx 或 GR IIIx HDF")
    return value


def of(project):
    return validate(project.get("target_body"))


def label(body):
    return UNSPECIFIED if body is None else body


def family(body):
    return hdf_guard.BODIES[validate(body)][0]


def is_hdf(body):
    return body is not None and hdf_guard.BODIES[validate(body)][3] == 1


def require_family(body, model, firmware):
    """A declared body must belong to the template's family and its pinned firmware."""
    if body is None:
        return None
    versions = {table[family(body)]["version"] for table in (hdf_guard.FAMILIES, hdf_guard.FAMILIES_220)}
    _need(family(body) == model and firmware in versions,
          "工程选的机身 %s 和固件 %s %s 不是同一家族" % (body, model, firmware))
    return body


def _boundary(report):
    for flag in ("compatibility_approved", "device_tested"):
        _need(report.get(flag) is False, "机身核对越过研究边界：" + flag)
    return report


def sparse_report(decoded, body, windows):
    """Protected HDF windows against a sparse plan; no complete image is built."""
    _need(body is not None, "核对前请先选机身版本（GR III 或 GR III HDF）")
    rows = tuple(dict(address=w.address, expected=w.expected, replacement=w.replacement) for w in windows)
    try:
        return _boundary(hdf_guard.inspect_sparse(decoded, validate(body), rows))
    except BodyTargetError:
        raise
    except ValueError as exc:
        raise BodyTargetError("机身保护窗核对失败：%s" % exc) from exc


def build_report(decoded, body, candidate):
    """The same protected windows against a complete decoded build (after its own checks)."""
    _need(body is not None, "机身版本未指定")
    try:
        return _boundary(hdf_guard.inspect(decoded, validate(body), candidate))
    except BodyTargetError:
        raise
    except ValueError as exc:
        raise BodyTargetError("机身保护窗核对失败：%s" % exc) from exc


def shutdown_report(body, model=None):
    """Which factory CollaboMode routes the shutdown image covers. The body's
    current CollaboMode is unknown here, so it is passed as None, never guessed."""
    from . import models
    new = model is not None and models.is_220(model)
    report = hdf_body_support.shutdown_coverage(
        validate(body), tuple(shared_shutdown.layout(model or models.GR3)['request_ids']), None,
        hdf_body_support.MODES_220 if new else hdf_body_support.MODES)
    for flag in ("camera_verified", "compatibility_approved", "body_setting_inferred"):
        _need(report.get(flag) is False, "关机图覆盖报告越过研究边界：" + flag)
    return report


def summary(report):
    """Plain, bounded wording for the UI and the manifest."""
    if report["status"] == "protected_windows_unchanged":
        return "已核对的 HDF 路径未被覆盖（%d 个保护窗）；不代表兼容，机身未实测" % len(report["protected_windows"])
    if report["status"] == "protected_windows_with_verified_shutdown_hook":
        return "Street 请求 3 仅在运行时确认 IIIx HDF 时转入自定义关机图；其余保护窗未改，机身未实测"
    if report["status"] == "review_required":
        return "改动碰到了受保护的 HDF 路径（%s），需要专项复核" % "、".join(report["changed_windows"])
    return "只读取了原件基线"


VARIANTS = {
    "GR III": "GR III", "GR III Street Edition": "GR III",
    "GR III Diary Edition": "GR III", "GR III HDF": "GR III HDF",
    "GR IIIx": "GR IIIx", "GR IIIx Urban Edition": "GR IIIx",
    "GR IIIx HDF": "GR IIIx HDF",
}


def variant_body(variant):
    _need(type(variant) is str and variant in VARIANTS, "请选择有效的机身型号")
    return VARIANTS[variant]
