"""Capture the GR III 2.20 v29 range table and reference (also used by GR IIIx 1.70).

    python3 tools/capture_v29_reference_220.py <2.10 payload> <2.20 payload> [--write]

The 2.20 readback (film_inplace_complete, film_inplace_integrity) needs its own
film_inplace_ranges_220.json and film_inplace_reference_220.json. They are taken from
the 2.20 writer only after four checks, any failure stops the run:

1. tools/check_relocated_windows.py film-gr3: every 2.20 window is the 2.10 window,
   relocated (original and replacement bytes), for all 15 slot masks.
2. The writer with the shipped 2.20 ARM bundle gives exactly the windows of the
   checker's direct assembly.
3. The 2.20 ranges equal the pinned 2.10 ranges with every address relocated.
4. No written byte lies in a 2.20 HDF protected window.
5. Per mask, the split into per-slot data bytes and shared bytes has the 2.10 counts.

Without --write nothing is changed; with the files in place it re-checks them.
"""
import hashlib
import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import check_relocated_windows as checker  # noqa: E402
from darkslide import (film_inplace_cache as cache, film_inplace_integrity as integrity,  # noqa: E402
                       film_profiles, fwaddr, fwread, hdf_guard, models)
from darkslide.film_arm_assemble import assemble  # noqa: E402

FILMS = checker.FILMS


def overlay_of(windows):
    return {w.address + i: b for w in windows for i, b in enumerate(w.replacement)}


def ranges_of(windows):
    return sorted((dict(address=w.address, length=len(w.replacement)) for w in windows), key=lambda r: r['address'])


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    old_raw, new_raw = Path(argv[0]).read_bytes(), Path(argv[1]).read_bytes()
    if hashlib.sha256(new_raw).hexdigest() != models.GR3_220.payload_sha:
        raise SystemExit('second file is not the official GR III 2.20 payload')
    write = '--write' in argv[2:]
    # 1. relocation equivalence
    for key, raw, L in (('old', old_raw, fwread.V210), ('new', new_raw, fwread.V220)):
        payload = fwread.Payload(raw, L, pinned=False)
        checker.READERS[key] = (lambda q: lambda va, n: q.p[q.off(va):q.off(va) + n])(payload)
    old, direct = checker.plans(cache, old_raw, models.GR3), checker.plans(cache, new_raw, models.GR3_220)
    problems, warnings = [], []
    for mask in range(1, 16):
        a = sorted(old[mask], key=lambda w: w.address); b = {w.address: w for w in direct[mask]}
        if len(a) != len(b):
            problems.append('mask %d window count' % mask); continue
        for w in a:
            v = b.get(checker.relocate(w.address, models.GR3_220))
            if v is None:
                problems.append('mask %d: unpaired %#x' % (mask, w.address)); continue
            for part in ('expected', 'replacement'):
                problems += checker.compare(w.address, getattr(w, part), v.address, getattr(v, part),
                                            models.GR3_220, 'mask %d %#x %s' % (mask, w.address, part), warnings)
    if problems or warnings:
        raise SystemExit('relocation equivalence failed: %s' % (problems + warnings)[:10])
    print('1. 15 masks: 2.20 windows are the relocated 2.10 windows')
    pinned_ranges = json.loads((ROOT / 'darkslide/film_inplace_ranges.json').read_text(encoding='utf-8'))
    protected = [(a, b) for _, a, b in hdf_guard.WINDOWS_220]
    old_ref = json.loads(integrity.REFERENCE.read_text(encoding='utf-8'))
    masks, ranges = {}, {}
    with fwaddr.firmware(models.GR3_220):
        for mask in range(16):
            slots = [8 + i for i in range(4) if mask >> i & 1]
            dynamic = integrity.data_addresses(slots)
            if not slots:
                masks['0'], ranges['0'] = integrity.fingerprint({}, dynamic), []
                continue
            edits = [dict(kind='film', slot=s, film=FILMS[s]) for s in slots]
            profiles = {FILMS[s]: film_profiles.load_profile(FILMS[s], models.GR3_220.id) for s in slots}
            shipped = cache.plan_offline(new_raw, edits, profiles)['windows']   # precompiled 2.20 bundle
            # 2. bundle == direct assembly
            if sorted(shipped, key=lambda w: w.address) != sorted(direct[mask], key=lambda w: w.address):
                raise SystemExit('mask %d: shipped bundle differs from direct assembly' % mask)
            # 3. ranges == relocated pinned 2.10 ranges
            moved = sorted((dict(address=fwaddr.A(r['address']), length=r['length'])
                            for r in pinned_ranges['masks'][str(mask)]), key=lambda r: r['address'])
            if moved != ranges_of(shipped):
                raise SystemExit('mask %d: ranges are not the relocated 2.10 ranges' % mask)
            # 4. HDF protected windows untouched
            overlay = overlay_of(shipped)
            if any(a <= at < b for at in overlay for a, b in protected):
                raise SystemExit('mask %d: a film byte lies in a 2.20 HDF protected window' % mask)
            masks[str(mask)] = integrity.fingerprint(overlay, dynamic)
            # 5. same split into per-slot data and shared bytes as the pinned 2.10 reference
            pinned = old_ref['masks'][str(mask)]
            if (masks[str(mask)]['shared_bytes'], masks[str(mask)]['total_bytes']) != (pinned['shared_bytes'], pinned['total_bytes']):
                raise SystemExit('mask %d: data/shared byte counts differ from 2.10' % mask)
            ranges[str(mask)] = ranges_of(shipped)
            print('mask %2d: %4d bytes, %3d windows' % (mask, len(overlay), len(shipped)))
    print('2-5. bundle, relocated ranges, HDF windows and data/shared split checked for 15 masks')
    if masks['0'] != old_ref['masks']['0']:
        raise SystemExit('empty-mask fingerprint differs')
    ref = dict(schema=1, layout=cache.LAYOUT, original_sha256=models.GR3_220.payload_sha,
               candidate_source_sha256=integrity.CANDIDATE_SHA256,
               provenance=('GR III 2.20 v29 reference, captured by tools/capture_v29_reference_220.py after the '
                           'relocation equivalence with 2.10 (all 15 masks), the shipped-bundle/direct-assembly '
                           'match, relocated 2.10 ranges and the 2.20 HDF windows. Also used by GR IIIx 1.70. '
                           'Equality detects drift, not independent ARM semantic proof. film_inplace_cache.py sha256 '
                           + hashlib.sha256((ROOT / 'darkslide/film_inplace_cache.py').read_bytes()).hexdigest()),
               masks=masks)
    range_doc = dict(pinned_ranges, masks=ranges)
    ref_body = (json.dumps(ref, indent=1) + '\n').encode('utf-8')
    range_body = (json.dumps(range_doc, separators=(',', ':')) + '\n').encode('utf-8')
    ref_path, range_path = integrity.REFERENCE_220, ROOT / 'darkslide/film_inplace_ranges_220.json'
    if not write:
        same = (ref_path.exists() and json.loads(ref_path.read_text(encoding='utf-8'))['masks'] == masks and
                range_path.exists() and range_path.read_bytes() == range_body)
        print('pinned 2.20 files %s the writer' % ('match' if same else 'do NOT match (run with --write)'))
        raise SystemExit(0 if same else 1)
    ref_path.write_bytes(ref_body); range_path.write_bytes(range_body)
    src = (ROOT / 'darkslide/film_inplace_integrity.py').read_text(encoding='utf-8')
    src = src.replace("REFERENCE_220_SHA256 = '%s'" % integrity.REFERENCE_220_SHA256,
                      "REFERENCE_220_SHA256 = '%s'" % hashlib.sha256(ref_body).hexdigest())
    (ROOT / 'darkslide/film_inplace_integrity.py').write_text(src, encoding='utf-8')
    print('wrote 2.20 ranges, reference and its pinned digest')


if __name__ == '__main__':
    main(sys.argv[1:])
