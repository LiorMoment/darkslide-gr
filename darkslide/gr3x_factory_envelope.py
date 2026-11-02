"""Read only numeric boundaries from the user's pinned GR IIIx 1.60 original.

No factory arrays are returned or retained in the cache. The optional film
post matrix has no comparable stock Q512 table; its bounds are explicitly
schema limits, not measured factory limits. No emulator or writer is imported.
"""
import copy
import hashlib
from pathlib import Path
import struct
import threading
from . import fwread, models
from .fwaddr import A as fa
from . import fwaddr

MODEL_ID = 'gr3x-160-stored-look-and-curve-bounds-v1'
_INPUT_BYTES = 30391788
_PRIMARY = 0x55018D94
_LIBRARY = 0x55037604
_CACHE = {}
_LOCK = threading.Lock()


class FactoryEnvelopeError(ValueError):
    pass


def factory_envelope_from_payload(payload):
    """Read scalar bounds from the same immutable official planning input."""
    _need(type(payload) is bytes and len(payload) == (fwaddr.current() or models.GR3X).payload_size and
          hashlib.sha256(payload).hexdigest() == (fwaddr.current() or models.GR3X).payload_sha,
          '需要官方 GR IIIx 1.60 原件')
    return _statistics(payload)


def _need(value, message):
    if not value:
        raise FactoryEnvelopeError(message)


def _statistics(payload):
    """Runtime private reader; local arrays never escape this function."""
    source = fwread.Payload(payload, models.layout((fwaddr.current() or models.GR3X)))

    def read(address, size):
        first, last = source.off(address), source.off(address + size - 1)
        _need(last == first + size - 1 and first + size <= len(payload),
              '原厂统计读取范围不连续')
        return payload[first:first + size]

    # Current 2.10 native tones11..21, effect records22..24, negative25.
    # Older records0..10 and custom menu IDs are not current native tone IDs.
    records = struct.unpack('<15I', read(fa(_PRIMARY) + 11 * 4, 15 * 4))
    matrices = []
    for record in records:
        _need(record % 4 == 0 and fa(0x55018D94) <= record <= fa(0x55019828) - 84,
              '原厂预设记录位置不匹配')
        matrix = struct.unpack('<I', read(record + 4, 4))[0]
        _need(matrix % 2 == 0 and fa(0x53000000) <= matrix <= fa(0x540B55C0) - 18,
              '原厂前矩阵位置不匹配')
        matrices.append(struct.unpack('<9h', read(matrix, 18)))
    pointers = struct.unpack('<12I', read(fa(_LIBRARY), 48))
    _need(len(set(pointers)) == 12 and all(a % 4 == 0 and
          fa(0x53EF0000) <= a <= fa(0x53F00000) - 512 for a in pointers),
          '原厂曲线表位置不匹配')
    ordered = sorted(pointers)
    _need(all(b - a >= 512 for a, b in zip(ordered, ordered[1:])),
          '原厂曲线表范围重叠')
    curves = [struct.unpack('<256H', read(a, 512)) for a in pointers]
    _need(all(v <= 16383 for curve in curves for v in curve),
          '原厂曲线不是14位范围')
    steps = [b - a for curve in curves for a, b in zip(curve, curve[1:])]
    return dict(
        look_min=min(v for matrix in matrices for v in matrix),
        look_max=max(v for matrix in matrices for v in matrix),
        look_row_abs_max=max(sum(abs(v) for v in matrix[i:i + 3])
                             for matrix in matrices for i in (0, 3, 6)),
        post_min=-8191, post_max=8191, post_col_abs_max=8191,
        curve_step_max=max(steps), curve_step_min=min(steps),
        curve_first_min=min(curve[0] for curve in curves),
        curve_first_max=max(curve[0] for curve in curves),
        curve_last_min=min(curve[-1] for curve in curves),
        curve_last_max=max(curve[-1] for curve in curves),
        schema=1, model='GR IIIx', firmware=(fwaddr.current() or models.GR3X).firmware, model_id=MODEL_ID,
        payload_sha256=(fwaddr.current() or models.GR3X).payload_sha,
        provenance=dict(
            look=dict(source='factory stored Q13 look matrices',
                      primary_indices=[11, 25], records=15),
            curve=dict(source='factory 12 stored u14 tables',
                       tables=12, samples_per_table=256),
            post=dict(source='film profile schema hard limit',
                      factory_comparable=False,
                      reason='原厂H是输出颜色转换的19位编码矩阵，不是新增的Q512后矩阵；无可比原厂表。后矩阵沿用现有每列绝对值和8191硬上限。')),
        limitations=[
            '边界来自原厂存储参数；不包含各设置下组合矩阵及最终像素的全部范围。',
            '四卷33节点重建曲线的相邻步差、起点和终点均须通过这些边界检查。',
            '数字在边界内不证明代码布局、并发、RAW或实机效果已经验证。'],
        original_arrays_retained=False, firmware_written=False)


def factory_envelope(official_bin_path):
    """Return defensive copies of scalar statistics, cached by original SHA."""
    try:
        path = Path(official_bin_path)
        _need(path.stat().st_size in (_INPUT_BYTES, models.GR3X_170.input_size), '需要官方 GR IIIx 1.60 / 1.70 原件')
        raw = path.read_bytes()
    except (OSError, TypeError) as error:
        raise FactoryEnvelopeError('无法读取用户官方原件') from error
    sha = hashlib.sha256(raw).hexdigest()
    _need(sha in (models.GR3X.input_sha, models.GR3X_170.input_sha), '需要官方 GR IIIx 1.60 / 1.70 原件')
    model = models.from_sha(sha)
    key = (sha, model.payload_sha, MODEL_ID)
    with _LOCK:
        if key in _CACHE:
            return copy.deepcopy(_CACHE[key])
        try:
            with fwaddr.firmware(model):
                result = _statistics(fwread.unpack(raw))
        except (ValueError, struct.error) as error:
            raise FactoryEnvelopeError('原厂数值范围读取失败：' + str(error)) from error
        result['official_sha256'] = sha
        _CACHE[key] = copy.deepcopy(result)
        return copy.deepcopy(result)
