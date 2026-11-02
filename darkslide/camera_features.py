"""Whole-camera features a project turns on: 柔光 (soft glow) and the wide aspect ratios (16:9, 65:24).

They belong to the camera, not to a preset (user, 2026-10-24): one switch each, stored with the project as
{"soft_glow": true, "wide_ratio": true}; a feature that is off is simply absent. The page shows them for
GR III and GR IIIx alike. Whether a switch may be turned on is the model's capability ("soft_glow",
"wide_ratio" in models.capabilities). 柔光 is written into the firmware (soft_glow, v22.28) for GR III 2.10
and GR IIIx 1.60; the wide ratios (wide_ratio, wide16) for both too, on non-HDF bodies. With both on,
the wide ratio patch carries 柔光 too, because they share hooks. 日期印记 (date_stamp, date07) is built on that
combined patch, so it needs both on; the camera has its own menu switch for the date, off by default.
1.6.1 does not offer the date stamp: its capability is off on every model and it stays in UNVERIFIED.
"""

NAMES = ("soft_glow", "wide_ratio", "date_stamp")
# Features the firmware generation writes.
WRITTEN = ("soft_glow", "wide_ratio", "date_stamp")
# Features whose update/restore evidence is not yet in. While a feature is listed
# here, full candidate generation is refused even though it is in WRITTEN. This is
# a guard, not a fix: it blocks the two generation entries that call refusal()
# (generate._run, gr3x_generation._run); it does NOT cover any caller that reaches
# a write interface without going through refusal().
UNVERIFIED = ("date_stamp",)
LABELS = dict(soft_glow="柔光", wide_ratio="16:9 和 65:24 宽幅", date_stamp="日期印记")


class FeatureError(ValueError):
    pass


def clean(value, capabilities=None):
    """The features that are on, as a dict of True values ({} = none). None counts as none. Anything
    else that is not exactly {name: bool} is refused; with capabilities, so is turning on a feature
    the model cannot take yet."""
    if value is None:
        return {}
    if type(value) is not dict:
        raise FeatureError("相机功能开关格式错误")
    out = {}
    for name, on in value.items():
        if name not in NAMES:
            raise FeatureError("不认识的相机功能：%s" % name)
        if type(on) is not bool:
            raise FeatureError("%s 的开关必须是真或假" % LABELS[name])
        if on:
            if capabilities is not None and not capabilities.get(name):
                raise FeatureError("%s 还不能装进这台相机（研发中）" % LABELS[name])
            out[name] = True
    if out.get("date_stamp") and not (out.get("soft_glow") and out.get("wide_ratio")):
        raise FeatureError("日期印记要和柔光、画幅一起装")
    return out


def refusal(features):
    """Why a project with these features on cannot be generated yet, or None when every one that is on
    can be written AND is not pending update/restore evidence. Switch toggling and saving are not
    affected; see clean()."""
    unverified = [LABELS[n] for n in NAMES if (features or {}).get(n) and n in UNVERIFIED]
    if unverified:
        return "%s的更新/恢复证据还没齐，这次不能生成；请先把开关关掉" % "、".join(unverified)
    on = [LABELS[n] for n in NAMES if (features or {}).get(n) and n not in WRITTEN]
    if not on:
        return None
    return "%s还没有接进固件生成，这次不能生成；请先把开关关掉" % "、".join(on)


def edits(features):
    """Generation edits for the features that are on and written."""
    from . import soft_glow, wide_ratio
    features = features or {}
    if features.get("wide_ratio"):
        return [wide_ratio.edit(glow=bool(features.get("soft_glow")), date=bool(features.get("date_stamp")))]
    return [soft_glow.edit()] if features.get("soft_glow") else []
