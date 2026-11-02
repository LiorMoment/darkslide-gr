"""Self-contained IIIx film-core snapshots; no camera file hooks are installed.

The numeric ABI stores the actual 768 curve values and both 3x3 matrices.
Stock per-shot controls (WB, exposure, grain, etc.) remain in the photo's own
metadata. They are NOT inferred from a build or claimed by this snapshot.
"""
import base64
import copy
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path

from . import models
from .film_profiles import validate_profile
from .film_requests import validate_request

MAGIC = b'DSFILM\0\0'
HEADER = struct.Struct('<8sHHHHII')
CORE = struct.Struct('<9h768H9h')
MAX_META = 65536
MAX_PACKET = HEADER.size + CORE.size + MAX_META + 4
ABI = 'gr3x-160-t3-q13-q512-v1'
RESTORED_ID = re.compile(r'user_lut_photo_[0-9a-f]{32}\Z')
FIELDS = ('look_matrix_q13', 'curves_u14', 'post_matrix_q512')


def _need(ok, message):
    if not ok:
        raise ValueError(message)


def _unique(pairs):
    out = {}
    for key, value in pairs:
        _need(key not in out, '配方 JSON 有重复字段')
        out[key] = value
    return out


def _reject(value):
    raise ValueError('配方 JSON 含非有限数值')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _model(model_id):
    """The IIIx firmware a packet is for: 1.60 unless the caller names its project's model (1.70).
    Only GR IIIx: the packet ABI is the IIIx film core."""
    model = models.get(models.GR3X.id if model_id is None else model_id)
    _need(models.is_iiix(model), '配方不适用于当前 IIIx 处理链')
    return model


def _profile(profile, slot, model_id=None):
    _need(type(profile) is dict, '配方必须是对象')
    validate_request(dict(kind='film', slot=slot, film=profile.get('id')))
    return validate_profile(profile, profile['id'], _model(model_id).id)


def encode(profile, slot, model_id=None):
    """Freeze numeric data AND original profile/provenance, not a catalog key.
    model_id: the project's IIIx model (default 1.60); the profile must be for exactly that model."""
    p = _profile(profile, slot, model_id)
    origin = p.get('provenance', {})
    if (isinstance(origin, dict) and origin.get('project') in
            ('idontknownihil/gr3-film-mods', 'andreavolpato/spektrafilm')
            and origin.get('license') == 'CC-BY-SA-4.0' and 'data_notices' not in p):
        folder = Path(__file__).resolve().parent.parent / 'film_curves'
        # Carry the existing attribution, unmodified license and change log
        # inside the packet so a separated photo/profile keeps these records.
        p['data_notices'] = {name: (folder/name).read_text(encoding='utf-8')
                             for name in ('README.md','SPEKTRAFILM_LICENSE.txt','CHANGELOG.txt')}
    core = CORE.pack(*(v for field in FIELDS for row in p[field] for v in row))
    meta = _json(dict(abi=ABI, official_sha256=_model(model_id).input_sha,
                      scope='darkslide-film-core',
                      profile={k: v for k, v in p.items() if k not in FIELDS}))
    _need(len(meta) <= MAX_META, '配方说明超过容量')
    body = HEADER.pack(MAGIC, 1, 1, slot, 0, CORE.size, len(meta)) + core + meta
    return body + struct.pack('<I', zlib.crc32(body))


def decode(packet, model_id=None):
    """Reject unknown ABI, corruption, ambiguous JSON and unsafe numeric data.
    model_id: the IIIx model the packet must have been made for (default 1.60)."""
    _need(type(packet) is bytes and HEADER.size + CORE.size + 4 <= len(packet) <= MAX_PACKET,
          '配方长度错误')
    magic, version, abi, slot, flags, ncore, nmeta = HEADER.unpack_from(packet)
    _need((magic, version, abi, flags, ncore) == (MAGIC, 1, 1, 0, CORE.size),
          '不支持的配方版本或处理链')
    _need(slot in (8, 9, 10, 11) and nmeta <= MAX_META and
          HEADER.size + ncore + nmeta + 4 == len(packet), '配方槽位或边界错误')
    _need(zlib.crc32(packet[:-4]) == struct.unpack_from('<I', packet, len(packet)-4)[0],
          '配方校验不通过')
    try:
        meta = json.loads(packet[HEADER.size+ncore:-4].decode('utf-8'),
                          object_pairs_hook=_unique, parse_constant=_reject)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('配方说明无法读取') from error
    _need(type(meta) is dict and set(meta) == {'abi', 'official_sha256', 'scope', 'profile'},
          '配方说明字段不匹配')
    _need(meta['abi'] == ABI and meta['official_sha256'] == _model(model_id).input_sha and
          meta['scope'] == 'darkslide-film-core', '配方不适用于当前 IIIx 处理链')
    p = meta['profile']
    _need(type(p) is dict and not any(k in p for k in FIELDS), '配方数值重复')
    values = CORE.unpack_from(packet, HEADER.size)
    p = dict(p, look_matrix_q13=[list(values[i:i+3]) for i in range(0, 9, 3)],
             curves_u14=[list(values[i:i+256]) for i in range(9, 777, 256)],
             post_matrix_q512=[list(values[i:i+3]) for i in range(777, 786, 3)])
    p = _profile(p, slot, model_id)
    return dict(slot=slot, profile=p, abi=ABI, scope='darkslide-film-core',
                packet_sha256=hashlib.sha256(packet).hexdigest(),
                numeric_sha256=hashlib.sha256(packet[HEADER.size:HEADER.size+CORE.size]).hexdigest())


def restored_profile(packet):
    """Numeric handoff accepted by the existing IIIx writer, without refitting.

The user_lut_ prefix is the existing numeric-import namespace, not a claim
that this profile was fitted from a new LUT. Preserve the archived provenance.
"""
    snapshot = decode(packet)
    p = copy.deepcopy(snapshot['profile'])
    original_id = p['id']
    p.update(id='user_lut_photo_' + snapshot['packet_sha256'][:32],
             official_sha256=models.GR3X.input_sha,
             base_model_id='gr3x-160-upstream-neutral-stubbed-v2',
             photo_snapshot=dict(original_id=original_id, original_slot=snapshot['slot'],
                                 packet_sha256=snapshot['packet_sha256'],
                                 numeric_sha256=snapshot['numeric_sha256'],
                                 refitted=False, per_shot_controls_included=False))
    return _profile(p, snapshot['slot'])


