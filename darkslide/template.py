"""Slot templates: which presets exist and which edits are open.

Opening more slots means shipping a new template file, not changing code.
"""
import json

from .paths import TEMPLATE_ROOT

STAGES = ("closed", "later", "test", "open")
EDITABLE = ("test", "open")
# grain: "factory" = the camera menu already has it, "menu" = this tool can
# open the hidden item, "closed" = not possible with the current research.
GRAIN_STAGES = ("closed", "factory", "menu")
# Template id -> detail sidecar (other ids keep the GR III 2.10 sidecar, as before).
DETAIL_FILES = {"gr3x-1.60-reviewed-1": "gr3x_v160.detail.json", "gr3-2.20-reviewed-1": "gr3_v220.detail.json",
                "gr3x-1.70-reviewed-1": "gr3x_v170.detail.json"}


class TemplateError(ValueError):
    pass


def load(name="gr3_v210.json"):
    value = json.loads((TEMPLATE_ROOT / name).read_text(encoding="utf-8"))
    validate(value)
    return value


def validate(value):
    def need(cond, msg):
        if not cond:
            raise TemplateError(msg)

    need(value.get("schema") == 1, "unknown template schema")
    slots = value.get("slots")
    need(isinstance(slots, list) and slots, "template has no slots")
    seen = set()
    for slot in slots:
        need(isinstance(slot.get("no"), int) and slot["no"] not in seen, "slot numbers must be unique ints")
        seen.add(slot["no"])
        need(slot.get("icon") in STAGES and slot.get("rename") in STAGES, "slot %s: bad stage" % slot["no"])
        need(isinstance(slot.get("factory_en"), str) and slot["factory_en"], "slot %s: factory_en missing" % slot["no"])
    for item in value.get("official_inputs", []):
        need(len(item.get("sha256", "")) == 64, "official input sha256 malformed")


def slot(template, no):
    for item in template["slots"]:
        if item["no"] == no:
            return item
    raise KeyError(no)


def can_edit(template, no, what):
    return slot(template, no)[what] in EDITABLE


def load_detail(template, name=None):
    """Detail-menu stages live in a sidecar file (<template>.detail.json) so the
    main template keeps exactly the fields the pinned generation core accepts."""
    name = name or template["id"]
    path = TEMPLATE_ROOT / (name if name.endswith(".json") else DETAIL_FILES.get(template["id"], "gr3_v210.detail.json"))
    if not path.exists():
        return dict(schema=1, template_id=template["id"], grain={}, grain_note={})
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != 1 or value.get("template_id") != template["id"]:
        raise TemplateError("detail sidecar does not match template")
    grain = value.get("grain") or {}
    for key, stage in grain.items():
        if stage not in GRAIN_STAGES or not any(s["no"] == int(key) for s in template["slots"]):
            raise TemplateError("detail sidecar: bad grain entry %s" % key)
    value.setdefault("grain_note", {})
    params = value.setdefault("factory_params", {})
    for key, entry in (params.get("slots") or {}).items():
        items = entry.get("items") if isinstance(entry, dict) else None
        if not any(s["no"] == int(key) for s in template["slots"]) or not isinstance(items, list) or not items:
            raise TemplateError("detail sidecar: bad factory_params entry %s" % key)
        for item in items:
            ok = isinstance(item, list) and len(item) == 2 and isinstance(item[0], str) and (
                item[1] is None or isinstance(item[1], str)
                or (type(item[1]) is int and -4 <= item[1] <= 4))
            if not ok:
                raise TemplateError("detail sidecar: bad factory_params item in %s" % key)
    return value


def grain_stage(template, no):
    slot(template, no)
    return load_detail(template)["grain"].get(str(no), "closed")
