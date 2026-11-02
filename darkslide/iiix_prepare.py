"""Bound a saved IIIx project to the existing sparse planner and independent reader."""
import copy
from . import models, film_requests, film_profiles, fwaddr
from . import gr3x_inplace as planner, gr3x_inplace_integrity as reader

VIVID_LAYOUT = 'gr3x160_inplace_cache_exit_vivid5_v1'
VIVID_LAYOUT_170 = 'gr3x170_inplace_cache_exit_vivid5_v1'


def layout_for(requests, model=None):
    model = model or fwaddr.current() or models.GR3X
    if not models.is_iiix(model):
        raise ValueError('GR IIIx film layout requires its exact model')
    if not any(r['slot'] == 2 for r in requests):
        return planner.LAYOUT
    return VIVID_LAYOUT_170 if model == models.GR3X_170 else VIVID_LAYOUT


def prepare(raw, project, supplied=None):
    """GR IIIx 1.60, or 1.70 when the caller runs it inside fwaddr.firmware(model)."""
    model = models.for_project(project)
    if not models.is_iiix(model) or project.get('unsaved'):
        raise ValueError('需要已保存的 IIIx 官方工程')
    frozen = copy.deepcopy(project)
    requests = film_requests.from_changes(frozen.get('changes', {}))
    supplied = supplied or {}
    required = {r['film'] for r in requests if r['film'].startswith('user_lut_')}
    if set(supplied) != required:raise ValueError('IIIx 用户配方来源不完整或包含额外参数')
    profiles = {r['film']: film_profiles.validate_profile(supplied[r['film']],r['film'],model.id)
                if r['film'] in required else film_profiles.load_profile(r['film'],model.id) for r in requests}
    layout = layout_for(requests, model)
    plan = planner.plan_offline(raw, list(copy.deepcopy(requests)), copy.deepcopy(profiles))
    if (plan.get('layout') != layout or type(plan.get('decoded_growth_bytes')) is not int
            or plan['decoded_growth_bytes'] != 0 or any(plan.get(k) is not False for k in
                ('full_image_created', 'production_integration_allowed', 'storage_approved'))):
        raise ValueError('IIIx 稀疏计划超出核对范围')
    windows = copy.deepcopy(plan['windows'])
    checks = reader.verify_project(raw, windows, frozen.get('changes', {}), profiles,
                                  planned_layout=layout, checked_layout=layout)
    if (any(checks.get(k) is not True for k in ('data_verified', 'shared_bytes_match_reference',
            'complete_window_coverage_verified')) or any(checks.get(k) is not False for k in
            ('production_approved', 'shared_code_verified'))):
        raise ValueError('IIIx 独立读回未通过')
    if checks.get('layout') != layout or checks.get('selected_slots') != [r['slot'] for r in requests]:
        raise ValueError('IIIx 独立读回布局或选择不一致')
    return dict(layout=layout, windows=windows, checks=checks,
                current_slots=film_requests.current_slots(frozen.get('changes', {})))
