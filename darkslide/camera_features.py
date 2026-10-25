"""Whole-camera features a project turns on: 柔光 (soft glow) and the 65:24 wide aspect ratio.

They belong to the camera, not to a preset (user, 2026-10-24): one switch each, stored with the project as
{"soft_glow": true, "wide_ratio": true}; a feature that is off is simply absent. The page shows them for
GR III and GR IIIx alike. Whether a switch may be turned on is the model's capability ("soft_glow",
"wide_ratio" in models.capabilities). 柔光 is written into the firmware (soft_glow, v22.6) for GR III 2.10
and GR IIIx 1.60; 65:24 is still being built, so it stays off and generating refuses a project that has
it on instead of leaving it out.
"""

NAMES = ("soft_glow", "wide_ratio")
# Features the firmware generation writes (2026-10-25: 柔光 v22.6 ships, 65:24 waits).
WRITTEN = ("soft_glow",)
LABELS = dict(soft_glow="柔光", wide_ratio="65:24 宽幅")


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
    return out


def refusal(features):
    """Why a project with these features on cannot be generated yet, or None when every one that is on
    can be written."""
    on = [LABELS[n] for n in NAMES if (features or {}).get(n) and n not in WRITTEN]
    if not on:
        return None
    return "%s还没有接进固件生成，这次不能生成；请先把开关关掉" % "、".join(on)


def edits(features):
    """Generation edits for the features that are on and written."""
    from . import soft_glow
    return [soft_glow.edit()] if (features or {}).get("soft_glow") else []
