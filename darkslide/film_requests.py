"""Shared UI request contract; the experimental writer stays disabled."""
import os
import re
from .film_slots import SUPPORTED_KEYS, SUPPORTED_SLOTS

FILM_WRITER_AVAILABLE=False
# The legacy four-film layout remains only for its original consumers.
# "legacy_arena_v1" (any subset, old private arena) is kept only for its own tests.
FILM_LAYOUT="four_curve_v1"
_ID=re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")

class FilmRequestError(ValueError):pass

def experimental():
    return False

def available():
    return False

def status():
    from .film_profiles import ready_profile_ids
    from .inplace_generation import production_available
    enabled = production_available() is True
    return {"available": enabled, "supported_slots": list(SUPPORTED_SLOTS),
            "model": "GR III", "firmware": "2.10", "profile_schema": 1,
            "ready_profile_ids": ready_profile_ids(), "layout": "inplace_history_cache_exit_v29",
            "supported_layouts": ["inplace_history_cache_exit_v29", "inplace_history_cache_exit_vivid5_v1"],
            "needs_all_four": False, "experimental": False,
            "reason": ("GR III 不扩容胶片生成已开放；其他机型按各自入口和确认流程处理" if enabled else "可保存选择并做不扩容只读核对；胶片完整输出尚未开放")}

def needs_closed_output(changes):
    return any(type(edit) is dict and ('film' in edit or
               (key in SUPPORTED_KEYS and bool(edit.get('grade'))))
               for key, edit in changes.items())


def validate_request(edit):
    if type(edit) is not dict or set(edit)!={"kind","slot","film"} or edit["kind"]!="film":
        raise FilmRequestError("胶片改动必须包含 kind、slot、film")
    if type(edit["slot"]) is not int or edit["slot"] not in SUPPORTED_SLOTS:
        raise FilmRequestError("胶片只支持鲜艳、银流冲洗、怀旧、HDR 色调和交叉冲洗")
    if type(edit["film"]) is not str or not _ID.fullmatch(edit["film"]):
        raise FilmRequestError("无效的胶片编号")
    return dict(edit)

def validate_batch(edits):
    if type(edits) is not list or len(edits)>len(SUPPORTED_SLOTS):
        raise FilmRequestError("一次最多选择五个胶片位置")
    clean=[validate_request(edit) for edit in edits]
    if len({edit["slot"] for edit in clean})!=len(clean):
        raise FilmRequestError("同一位置只能选择一卷胶片")
    return tuple(sorted(clean,key=lambda edit:edit["slot"]))

def from_changes(changes):
    if type(changes) is not dict:raise FilmRequestError("无效的工程改动")
    edits=[]
    for key,change in changes.items():
        if key=="shutdown":continue
        if type(change) is dict and "film" in change:
            if type(key) is not str or key not in SUPPORTED_KEYS:
                raise FilmRequestError("工程中的胶片位置无效")
            edits.append({"kind":"film","slot":int(key),"film":change["film"]})
    return validate_batch(edits)


def current_slots(changes):
    selected = {e['slot']: e['film'] for e in from_changes(changes)}
    return tuple({'slot': slot, 'film': selected.get(slot)} for slot in SUPPORTED_SLOTS)
