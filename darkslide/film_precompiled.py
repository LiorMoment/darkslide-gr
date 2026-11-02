"""Checked ARM fragments for the released film planners; no compiler fallback.

The bundle contains only code assembled from distributed source, never firmware
arrays. SHA pinning detects accidental/stale resources, not hostile modification
of an application whose Python code can also be replaced.
"""
import hashlib
import json
from pathlib import Path
from .film_precompiled_pin import BUNDLE_SHA256
from .film_precompiled_vivid5_pin import VIVID_BUNDLE_SHA256

BUNDLE = Path(__file__).with_name('data') / 'film_arm_v1.json'
FAMILIES = ('GRIII-2.10-v29', 'GRIIIx-1.60-v29')
VIVID_BUNDLE = Path(__file__).with_name('data') / 'film_arm_vivid5_v1.json'
VIVID_FAMILIES = ('GRIII-2.10-vivid5-v1', 'GRIIIx-1.60-vivid5-v1')
# GR III 2.20 / IIIx 1.70: same sources at relocated addresses, separate file and pin.
BUNDLE_220 = Path(__file__).with_name('data') / 'film_arm_220_v1.json'
FAMILIES_220 = ('GRIII-2.20-v29', 'GRIIIx-1.70-v29')
VIVID_BUNDLE_220 = Path(__file__).with_name('data') / 'film_arm_vivid5_220_v1.json'
VIVID_FAMILIES_220 = ('GRIII-2.20-vivid5-v1', 'GRIIIx-1.70-vivid5-v1')


def source_key(source, base):
    if type(source) is not str or type(base) is not int or not 0 <= base < 2**32 or base % 4:
        raise ValueError('typed aligned A32 source/base required')
    return '%08x:%s' % (base, hashlib.sha256(source.encode('utf-8')).hexdigest())


def assemble_bundled(source, base, family):
    key = source_key(source, base)
    if family in FAMILIES:
        resource, digest = BUNDLE, BUNDLE_SHA256
    elif family in VIVID_FAMILIES:
        resource, digest = VIVID_BUNDLE, VIVID_BUNDLE_SHA256
    elif family in FAMILIES_220:
        from .film_precompiled_pin_220 import BUNDLE_220_SHA256
        resource, digest = BUNDLE_220, BUNDLE_220_SHA256
    elif family in VIVID_FAMILIES_220:
        from .film_precompiled_vivid5_pin_220 import VIVID_BUNDLE_220_SHA256
        resource, digest = VIVID_BUNDLE_220, VIVID_BUNDLE_220_SHA256
    else:
        raise ValueError('unsupported precompiled ARM family')
    # Read afresh: resource replacement/corruption must not hide behind a cache.
    try:
        raw = resource.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('precompiled ARM bundle fingerprint mismatch')
        bundle = json.loads(raw)
        if bundle['schema'] != 1:
            raise ValueError('unsupported precompiled ARM schema')
        row = bundle['families'][family]['entries'][key]
        blob = bytes.fromhex(row['hex'])
        if (not blob or len(blob) % 4 or len(blob) != row['size'] or
                hashlib.sha256(blob).hexdigest() != row['sha256']):
            raise ValueError('precompiled ARM fragment integrity mismatch')
        return blob
    except (OSError, KeyError, TypeError, ImportError, json.JSONDecodeError) as exc:
        raise ValueError('预编译胶片组件缺失或与源码不匹配，请使用完整匹配的程序包') from exc
