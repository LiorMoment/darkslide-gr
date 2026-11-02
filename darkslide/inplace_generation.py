"""Saved-project wiring for the v29 sparse experiment. No image producer."""
import copy
import hashlib
import json

from . import film_requests, film_inplace_cache, film_inplace_integrity, fwaddr, fwread, models
from . import film_profiles as profiles_module
from .film_slots import mask_slots

LAYOUT = 'inplace_history_cache_exit_v29'
VIVID_LAYOUT = 'inplace_history_cache_exit_vivid5_v1'
VIVID_LAYOUT_220 = 'inplace_history_cache_exit_vivid5_220_v1'


def layout_for(requests, model=None):
    """Select from the validated request, never from producer metadata."""
    model = model or fwaddr.current() or models.GR3
    if model not in (models.GR3, models.GR3_220):
        raise ValueError('GR III film layout requires its exact model')
    if not any(r['slot'] == 2 for r in requests):
        return LAYOUT
    return VIVID_LAYOUT_220 if model == models.GR3_220 else VIVID_LAYOUT


class InplaceGenerationError(ValueError):
    pass


def production_available():
    """User-authorized 1.0 v29 App route; legacy writers remain disabled."""
    return True


def run_closed():
    """The production route stays closed regardless of legacy feature flags."""
    raise InplaceGenerationError('不扩容胶片完整固件生成尚未授权；仅可进行稀疏离线核对')


def _need(value, message):
    if not value:
        raise InplaceGenerationError(message)


def prepare(raw, project, *, film_profiles=None):
    """Freeze a complete saved project and independently check sparse windows.

    Caller supplies the official decoded original, not a previous modified
    image or a delta. Non-film edits are preserved in the snapshot but are
    not planned here. Returned dictionaries are detached copies; there is no
    complete-image output, store call, firmware packing or production approval.
    """
    try:
        _need(type(raw) is bytes, '需要不可变的官方解码原件')
        _need(type(project) is dict and not project.get('unsaved'), '需要完整的已保存工程')
        frozen = copy.deepcopy(project)
        if 'input_sha256' in frozen:
            model = fwaddr.current()
            _need(frozen['input_sha256'] == (fwread.V210_INPUT_SHA256 if model is None else model.input_sha),
                  '工程原件身份不一致')
        changes = frozen.get('changes', {})
        _need(type(changes) is dict, '工程改动必须是完整映射')
        requests = film_requests.from_changes(changes)
        supplied = {} if film_profiles is None else copy.deepcopy(film_profiles)
        _need(type(supplied) is dict, '拟合参数必须是映射')
        selected = {r['film'] for r in requests}
        _need(all(type(k) is str and k in selected and k.startswith('user_lut_')
                  for k in supplied), '自定义参数不能覆盖内置胶片或未选胶片')
        resolved = {}
        for ident in sorted(selected):
            if ident.startswith('user_lut_'):
                _need(ident in supplied, '缺少用户 LUT 拟合参数')
                resolved[ident] = profiles_module.validate_profile(supplied[ident], ident, fwaddr.model_id())
            else:
                resolved[ident] = profiles_module.load_profile(ident, fwaddr.model_id())
        profiles = copy.deepcopy(resolved)
        hashes = {key: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                  allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()
                  for key, value in profiles.items()}
        layout = layout_for(requests)
        _need(film_inplace_cache.LAYOUT == LAYOUT, '原四位置生成布局改变')
        plan = film_inplace_cache.plan_offline(raw, list(copy.deepcopy(requests)), copy.deepcopy(profiles))
        _need(plan.get('layout') == layout, '计划布局不一致')
        _need(type(plan.get('decoded_growth_bytes')) is int and plan['decoded_growth_bytes'] == 0,
              '不允许扩容')
        for flag in ('full_image_created', 'production_integration_allowed', 'storage_approved'):
            _need(plan.get(flag) is False, '研究边界不一致：' + flag)
        windows = copy.deepcopy(plan['windows'])
        checks = film_inplace_integrity.verify_project(raw, windows, copy.deepcopy(changes),
                   copy.deepcopy(profiles), planned_layout=layout, checked_layout=layout)
        _need(checks.get('layout') == layout, '独立读回布局不一致')
        _need(checks.get('selected_slots') == [r['slot'] for r in requests], '读回选择不一致')
        for flag in ('data_verified', 'shared_bytes_match_reference', 'complete_window_coverage_verified'):
            _need(checks.get(flag) is True, '独立核对未通过：' + flag)
        for flag in ('shared_code_verified', 'production_approved'):
            _need(checks.get(flag) is False, '独立核对越过研究批准边界：' + flag)
        return dict(schema=1, project=frozen, changes=copy.deepcopy(changes),
                    requests=copy.deepcopy(requests), current_slots=film_requests.current_slots(changes),
                    mask=mask_slots([r['slot'] for r in requests]), profiles=profiles,
                    profile_sha256=hashes, windows=windows, checks=copy.deepcopy(checks),
                    layout=layout, decoded_growth_bytes=0, development_only=True,
                    production_approved=False, exportable=False, full_image_created=False,
                    write_gate_open=False)
    except InplaceGenerationError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        raise InplaceGenerationError('不扩容胶片稀疏核对失败：%s' % exc) from exc
