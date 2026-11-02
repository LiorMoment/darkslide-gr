"""Bounded Scheme C relocation component; no image builder or device operation.

Local adaptation of Darkslide's own film_scheme_c.py / film_standard_proxy.py.
Uses the already attributed MIT templates in _vendor/gr3_film_hooks.py and
_vendor/gr3_four_curve_hooks.py, and the MIT-derived film_arm_assemble.py.
Upstream: idontknownihil/gr3-film-mods, revision
04d5d5dd6e85d80089c6200bd0f0de5fff2d658f; copyright (c) 2026 idontknownihil.
See app/darkslide/_vendor/GR3_FILM_MODS_LICENSE.txt and source attribution JSON.
Modified by LiorMoment: separate new 4KiB append from original-address windows,
dense code allocation, no old zero arena/landings, strict supplied scalar gate.
Only newly assembled components are returned. No generated ARM is executed.

The reader must supply a held official GR III 2.10 original. This API validates
bounded reads and guards but cannot authenticate a caller's callback. Envelope
identity fields are checked; its scalar values must be from the same original.
Root's separate reader validates original identity and every resulting byte.
"""
import struct
from .film_requests import FilmRequestError, validate_batch
from .film_profiles import validate_profile
from .film_patch_plan import PlanImage, _branch
from .film_four_validation import fit_knots, expand_knots, ORIGINAL_SHA
from .film_writer import _init_tail, SLOT_INFO, HDR_FIXES, hdr_fixes
from .film_standard_proxy import derive as proxy_derive, mask_slots
from .film_arm_assemble import assemble
from .film_scheme_c import (
    _Source, _fill_source, _dirty_source, _getter_source, _post_source,
    _composer_source, MODE_CHANGE_WORDS,
)
from ._vendor.gr3_four_curve_hooks import point_source
from ._vendor.gr3_film_hooks import csp_entry
from .fwaddr import A as fa
from . import fwaddr

LAYOUT = 'standard_sliders_extension_experiment_v1'
ROM_BEGIN = 0x540B55C0
APPEND_SIZE = 4096
ROM_END = ROM_BEGIN + APPEND_SIZE
DATA_BASE = ROM_BEGIN + 0x690
STRIDE = 384
LOOKUP = DATA_BASE + 4 * STRIDE
POST = LOOKUP + 64
USED_END = POST + 96
PRIMARY, STANDARD = 0x55018D94, 0x5501900C
ORIGINAL_RTOS = (0x53000000, ROM_BEGIN)
ORIGINAL_DATA = (0x55000000, 0x5503A080)
EXCLUDED_TABLE = (0x53D36368, 0x53D37884)
CODE_BYTES = 1672
CODE_ROLES = ('fill', 'dirty', 'point', 'getter', 'post', 'composer') + tuple(
    'proxy_dispatch_' + str(i) for i in range(3)
) + tuple('proxy_thunk_' + str(i) for i in range(27))
ENVELOPE_FIELDS = (
    'look_min', 'look_max', 'look_row_abs_max', 'post_min', 'post_max',
    'post_col_abs_max', 'curve_step_min', 'curve_step_max',
    'curve_first_min', 'curve_first_max', 'curve_last_min', 'curve_last_max',
)
ENVELOPE_ID = 'gr3-210-stored-look-and-curve-bounds-v1'


def _envelope(value):
    if type(value) is not dict:
        raise FilmRequestError('需要同一官方原件的数值边界')
    if (type(value.get('schema')) is not int or value['schema'] != 1
            or value.get('model') != 'GR III' or value.get('firmware') != fwaddr.firmware_name('2.10')
            or value.get('model_id') != ENVELOPE_ID
            or value.get('payload_sha256') != fwaddr.payload_sha(ORIGINAL_SHA)):
        raise FilmRequestError('原厂边界身份不匹配')
    if any(type(value.get(k)) is not int for k in ENVELOPE_FIELDS):
        raise FilmRequestError('原厂边界必须为完整整数标量')
    e = {k: value[k] for k in ENVELOPE_FIELDS}
    if (not -32768 <= e['look_min'] <= e['look_max'] <= 32767
            or not 0 < e['look_row_abs_max'] <= 98304
            or not -8191 <= e['post_min'] <= e['post_max'] <= 8191
            or not 0 < e['post_col_abs_max'] <= 8191
            or not -16383 <= e['curve_step_min'] <= e['curve_step_max'] <= 16383
            or not 0 <= e['curve_first_min'] <= e['curve_first_max'] < e['curve_last_min']
            <= e['curve_last_max'] <= 16383):
        raise FilmRequestError('原厂边界标量超出合法范围')
    return e


def _numeric(profile, envelope):
    look, post = profile['look_matrix_q13'], profile['post_matrix_q512']
    if (any(not envelope['look_min'] <= v <= envelope['look_max']
            for row in look for v in row)
            or any(sum(abs(v) for v in row) > envelope['look_row_abs_max']
                   for row in look)
            or any(not envelope['post_min'] <= v <= envelope['post_max']
                   for row in post for v in row)
            or any(sum(abs(post[i][j]) for i in range(3))
                   > envelope['post_col_abs_max'] for j in range(3))):
        raise FilmRequestError('胶片矩阵超出原厂数值边界')
    packed, errors = [], []
    for curve in profile['curves_u14']:
        knots, error = fit_knots(curve)
        rebuilt = expand_knots(knots)
        if any(not envelope['curve_step_min'] <= b - a <= envelope['curve_step_max']
               for a, b in zip(rebuilt, rebuilt[1:])):
            raise FilmRequestError('胶片重建曲线步差超出原厂数值边界')
        if not (envelope['curve_first_min'] <= rebuilt[0] <= envelope['curve_first_max']
                and envelope['curve_last_min'] <= rebuilt[-1] <= envelope['curve_last_max']):
            raise FilmRequestError('胶片重建曲线起点或终点超出原厂数值边界：起点 %d（原厂 %d～%d），终点 %d（原厂 %d～%d）' % (
                rebuilt[0], envelope['curve_first_min'], envelope['curve_first_max'],
                rebuilt[-1], envelope['curve_last_min'], envelope['curve_last_max']))
        packed.append(knots)
        errors.append(error)
    return tuple(packed), tuple(errors)


def slot_layout(slot):
    if type(slot) is not int or slot not in (8, 9, 10, 11):
        raise FilmRequestError('胶片位置无效')
    base = fa(DATA_BASE) + (slot - 8) * STRIDE
    return dict(slot=slot, record=base, matrix=base + 84,
                knots=(base + 104, base + 172, base + 240),
                lookup_entry=fa(LOOKUP) + (slot - 8) * 16,
                post_entry=fa(POST) + (slot - 8) * 24)


def _bounded_original(callback):
    if not callable(callback):
        raise FilmRequestError('需要只读官方原件')

    def read(address, size):
        if (type(address) is not int or type(size) is not int
                or not 0 < size <= 512 or not 0 <= address < address + size <= 2**32):
            raise FilmRequestError('原件读取参数无效')
        end = address + size
        if not any(a <= address < end <= b for a, b in (fa(ORIGINAL_RTOS), fa(ORIGINAL_DATA))):
            raise FilmRequestError('仅允许原 RTOS 与原数据别名读取')
        a, b = fa(EXCLUDED_TABLE)
        if address < b and a < end:
            raise FilmRequestError('旧仲裁表不属于胶片空间')
        blob = callback(address, size)
        if type(blob) is not bytes or len(blob) != size:
            raise FilmRequestError('原件读取不完整')
        return blob
    return read


def _base_result(mask):
    return dict(layout=LAYOUT, old_windows=(), append_address=fa(ROM_BEGIN),
                origin_payload_sha256=ORIGINAL_SHA, origin_payload_bytes=59718388,
                rtos_old_length=0x10B55C0,
                rtos_new_length=0x10B55C0 + (4096 if mask else 0),
                append_file_offset=0x10C5E30, container_rebuild_required=bool(mask),
                append_bytes=b'', installed_mask=mask, selected_slots=(),
                records=(), code_regions=(), code_bytes=0, code_span=0,
                compression_errors=(), source_regions=(),
                component_only=True, relocation_experiment=True,
                runtime_mapping_verified=False, device_verified=False,
                full_image_created=False, full_image_verified=False,
                write_gate_open=False, storage_liveness_verified=False,
                same_gamma_concurrent_calls_verified=False,
                factory_library_unchanged=True, factory_random_code_unchanged=True,
                old_arbitration_table_unchanged=True, old_landings_unchanged=True,
                expanded_scratch_bytes=0)


def plan(read, edits, profiles, envelope):
    """Return guarded old windows and one separate owned append, never apply them.

    `edits` is the full current installed selection, not an incremental command.
    Callers replacing one film must retain the other three edits/profiles. Slot
    addresses and code widths are fixed for every nonzero selection mask.
    Empty selection permits read=None and returns no append/windows. Invalid
    requests/profiles dictionary/envelope still reject before this empty return.
    """
    requests = validate_batch(edits)
    if type(profiles) is not dict:
        raise FilmRequestError('需要胶片拟合参数')
    limits = _envelope(envelope)
    if read is not None and not callable(read):
        raise FilmRequestError('需要只读官方原件')
    resolved, compressed, errors = {}, {}, {}
    for e in requests:
        if e['film'] not in profiles:
            raise FilmRequestError('所选胶片缺少参数')
        p = validate_profile(profiles[e['film']], e['film'])
        resolved[e['slot']] = p
        compressed[e['slot']], errors[e['slot']] = _numeric(p, limits)
    mask = mask_slots([e['slot'] for e in requests])
    result = _base_result(mask)
    if not mask:
        return result
    source = _Source(_bounded_original(read))
    read = source.read
    for at, word in fa(MODE_CHANGE_WORDS):
        if read(at, 4) != struct.pack('<I', word):
            raise FilmRequestError('原厂模式切换缓存合同不匹配')
    stock, tail = read(fa(STANDARD), 84), _init_tail(read)
    if read(fa(0x536DD464), 4) != struct.pack('<I', 0xE89DAFF8):
        raise FilmRequestError('原厂fill返回不匹配')

    append = bytearray(APPEND_SIZE)
    append_spans, code = [], []
    cursor = fa(ROM_BEGIN)

    def append_write(address, blob):
        if type(address) is not int or type(blob) is not bytes or not blob or len(blob) > 512:
            raise FilmRequestError('新增区写入必须为有界不可变字节')
        end = address + len(blob)
        if not fa(ROM_BEGIN) <= address < end <= fa(USED_END):
            raise FilmRequestError('新增区写入越界')
        if any(address < b and a < end for a, b in append_spans):
            raise FilmRequestError('新增区写入重叠')
        append_spans.append((address, end))
        append[address - fa(ROM_BEGIN):end - fa(ROM_BEGIN)] = blob

    def allocate(role, src):
        nonlocal cursor
        if (type(role) is not str or len(code) >= len(CODE_ROLES)
                or role != CODE_ROLES[len(code)]):
            raise FilmRequestError('新增区代码顺序不匹配')
        at = cursor
        blob = assemble(src, at)
        if not blob or len(blob) % 4 or at + len(blob) > fa(DATA_BASE):
            raise FilmRequestError('胶片代码超出新增只读区')
        append_write(at, blob)
        code.append(dict(role=role, address=at, size=len(blob)))
        cursor += len(blob)
        return at

    for role, src in (('fill', _fill_source(mask)), ('dirty', _dirty_source(mask)),
                      ('point', point_source()), ('getter', _getter_source(fa(LOOKUP))),
                      ('post', _post_source(fa(POST)))):
        allocate(role, src)
    point = next(r['address'] for r in code if r['role'] == 'point')
    allocate('composer', _composer_source(fa(LOOKUP), point))
    addresses = {r['role']: r['address'] for r in code}
    guards = [
        (fa(0x536DD450), 0x159632D0, _branch(fa(0x536DD450), addresses['fill'])),
        (fa(0x536DD024), 0xE5C4100E, _branch(fa(0x536DD024), addresses['dirty'])),
        (fa(0x536E4774), _branch(fa(0x536E4774), fa(0x536E4720), link=True),
         _branch(fa(0x536E4774), addresses['post'], link=True)),
        (fa(0x536E87C4), _branch(fa(0x536E87C4), fa(0x536EEDF0), link=True),
         _branch(fa(0x536E87C4), addresses['getter'], link=True)),
        (fa(0x536E87C8), 0xE51B3044, _branch(fa(0x536E87C8), addresses['composer'])),
    ]
    if mask & 4:
        guards.extend(hdr_fixes())
    proxy_words, proxy_codes, proxy_meta = proxy_derive(
        read, [e['slot'] for e in requests], allocate)
    if proxy_codes:
        raise FilmRequestError('未授权改变原位置的额外代码')
    guards.extend(proxy_words)
    if tuple(r['role'] for r in code) != CODE_ROLES or cursor - fa(ROM_BEGIN) != CODE_BYTES:
        raise FilmRequestError('新增区代码尺寸合同不匹配')
    allowed = [(a, a + 4) for a, _, _ in guards]
    aliases = [i for e in requests for i in SLOT_INFO[e['slot']][1]]
    allowed.extend((fa(PRIMARY) + 4 * i, fa(PRIMARY) + 4 * i + 4) for i in aliases)
    # Every original write is a word and separate from the new owned buffer.
    for a, b in allowed:
        if not any(x <= a < b <= y for x, y in (fa(ORIGINAL_RTOS), fa(ORIGINAL_DATA))):
            raise FilmRequestError('旧补丁写入越界')
        x, y = fa(EXCLUDED_TABLE)
        if a < y and x < b:
            raise FilmRequestError('旧仲裁表禁止写入')
    image = PlanImage(read, tuple(allowed), max_bytes=8192)
    for at, old, new in guards:
        image.guarded_word(at, old, new)
    records, compression_errors = [], []
    for e in requests:
        r, p = slot_layout(e['slot']), resolved[e['slot']]
        clone = bytearray(stock)
        clone[4:8] = struct.pack('<I', r['matrix'])
        clone[68:84] = tail
        append_write(r['record'], bytes(clone))
        append_write(r['matrix'], struct.pack('<9h', *(v for row in p['look_matrix_q13'] for v in row)))
        for at, knots in zip(r['knots'], compressed[e['slot']]):
            append_write(at, struct.pack('<33H', *knots))
        append_write(r['lookup_entry'], struct.pack('<4I', r['record'], *r['knots']))
        append_write(r['post_entry'], csp_entry(r['record'], p['post_matrix_q512']))
        for i in SLOT_INFO[e['slot']][1]:
            image.write(fa(PRIMARY) + 4 * i, struct.pack('<I', r['record']))
        records.append(dict(r, film=e['film'], aliases=SLOT_INFO[e['slot']][1]))
        compression_errors.append(dict(slot=e['slot'], channels=errors[e['slot']]))
    old_windows = image.seal()
    source.stable()
    result.update(old_windows=old_windows, append_bytes=bytes(append),
                  selected_slots=tuple(e['slot'] for e in requests), records=tuple(records),
                  compression_errors=tuple(compression_errors), code_regions=tuple(code),
                  code_bytes=CODE_BYTES, code_span=cursor - fa(ROM_BEGIN),
                  data_base=fa(DATA_BASE), lookup_address=fa(LOOKUP), post_address=fa(POST),
                  used_end=fa(USED_END), append_unused_bytes=fa(ROM_END) - fa(USED_END),
                  proxy=proxy_meta, numeric_gate=limits,
                  source_regions=tuple((a, n) for a, n in source.regions))
    return result


def official_origin():
    """Return fixed reader identity declarations, not callback authentication."""
    return dict(schema=1, model='GR III', firmware='2.10',
                official_sha256='d6a3c9c288d784080e7b283222ecdd8752adfeed6faa36dab44f73115354bfd4',
                payload_sha256=ORIGINAL_SHA, payload_bytes=59718388,
                rtos_body=0x10870, rtos_va=fa(0x53000000),
                rtos_old_length=0x10B55C0, append_file_offset=0x10C5E30)


def _same_snapshot(actual, expected):
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return actual.keys() == expected.keys() and all(
            _same_snapshot(actual[k], v) for k, v in expected.items())
    if type(expected) in (tuple, list):
        return len(actual) == len(expected) and all(
            _same_snapshot(a, b) for a, b in zip(actual, expected))
    return actual == expected


def plan_official_payload(raw, edits, profiles=None, *, envelope=None, origin=None):
    """Authenticate the original payload and derive its own scalar boundaries.

    Optional snapshots must match the derived values with exact types. They
    cannot select a different firmware, layout, envelope or source identity.
    Only a local plan is returned; no original bytes are applied or packaged.
    """
    import hashlib
    from .factory_envelope import factory_envelope_from_payload
    from .film_patch_plan import from_official_payload
    from .film_profiles import load_profile
    if (type(raw) is not bytes or len(raw) != 59718388
            or hashlib.sha256(raw).hexdigest() != ORIGINAL_SHA):
        raise FilmRequestError('新增区只接受官方GR III2.10原解码数据')
    requests = validate_batch(edits)
    derived_envelope = factory_envelope_from_payload(raw)
    derived_origin = official_origin()
    if envelope is not None and not _same_snapshot(envelope, derived_envelope):
        raise FilmRequestError('边界快照与原件重新读取结果不符')
    if origin is not None and not _same_snapshot(origin, derived_origin):
        raise FilmRequestError('来源快照与固定官方身份不符')
    if profiles is None:
        profiles = {e['film']: load_profile(e['film']) for e in requests}
    result = plan(from_official_payload(raw, ()).reader, list(requests),
                  profiles, derived_envelope)
    result['origin'] = derived_origin
    return result
