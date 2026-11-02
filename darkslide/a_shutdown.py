from . import package
"""GR III 2.10 A-drive shutdown builder; returns bytes, never writes a file.

The normal type-2 updater skips B-drive RES records. Install the existing
Diary record in A and use its original descriptor for five requested entries.
This changes neither partition parameters nor RES record lengths/count.
Caller must independently decode the JPEG and verify the returned container.
Whether the camera shows the picture is confirmed only by flashing it.
"""
import hashlib
import struct
from . import build as codec
from . import shared_shutdown as shared

OLD_PATH = b'B:\\Resource\\Jpeg\\GB_Diary.jpg'
NEW_PATH = b'A:\\Resource\\Jpeg\\GB_Diary.jpg'
PATH_VA = 0x53B2298C
RESOURCE_HEADER = 59298653
RESOURCE_HEADER_220 = 59378796   # GR III 2.20 / GR IIIx 1.70 (two more RES records before it)

class AShutdownError(ValueError):
    pass

def need(ok, message):
    if not ok:
        raise AShutdownError(message)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def _apply_a_shutdown(decoded, jpeg, model_id=None):
    """Pinned official payload only; checksum intentionally left to builder."""
    from . import models
    need(type(jpeg) is bytes, 'JPEG bytes required; None is not restoration')
    changed, details = shared.apply_shared(decoded, jpeg, model_id)
    L = shared.layout(models.get(model_id))
    header = RESOURCE_HEADER_220 if L is shared.L220 else RESOURCE_HEADER
    path_at = shared.off(L['path_va'], len(OLD_PATH) + 1, L['rtos_end'])
    need(decoded[path_at:path_at + len(OLD_PATH) + 1] == OLD_PATH + b'\0',
         'original RTOS Diary path')
    need(decoded[header:header + 32] == OLD_PATH.ljust(32, b'\0') and
         struct.unpack_from('<I', decoded, header + 32)[0] == shared.CAPACITY and
         header + 36 == L['pixel_offset'], 'original RES path/length')
    need(decoded.find(NEW_PATH) == -1, 'A-drive path collision')
    updated = bytearray(changed)
    updated[path_at] = ord('A')
    updated[header] = ord('A')
    edits = list(details['edits']) + [
        dict(what='rtos_diary_path_drive', offset=path_at, length=1, before='42', after='41'),
        dict(what='res_diary_path_drive', offset=header, length=1, before='42', after='41')]
    need(len(updated) == len(decoded), 'payload expansion')
    details = dict(details, schema='gr3-a-shutdown-offline-v1', edits=edits,
                   shared_path=NEW_PATH.decode('ascii'), output_payload_sha256=sha(updated),
                   resource_header_path_changed=True, partition_parameters_unchanged=True,
                   device_installation_verified=False)
    return bytes(updated), details

def apply_a_shutdown(decoded, jpeg, model_id=None):
    """Normalize validation failures from the pinned shared dependency."""
    try:
        return _apply_a_shutdown(decoded, jpeg, model_id)
    except shared.SharedSlotError as exc:
        raise AShutdownError(str(exc)) from exc

def build_a_shutdown(official, jpeg):
    try:
        need(type(official) is bytes and len(official) == codec.INPUT_SIZE and
             sha(official) == codec.INPUT_SHA, 'Official GR III 2.10 input is required')
        original, frames, end, footer = codec._decode(official)
        need(sha(original) == shared.PIN, 'Decoded input identity differs')
        changed, details = apply_a_shutdown(original, jpeg)
        output, payload, edits, rewritten = package.pack_change(
            official, original, frames, end, footer, changed, details['edits'])
        actual, _, _, _ = codec._decode(output)
        need(actual == payload, 'Output image differs after decoding')
        return dict(output=output, input_sha256=codec.INPUT_SHA, output_sha256=sha(output),
                    payload_sha256=sha(payload), payload_edits=edits, frames_replaced=rewritten,
                    shared_capacity=shared.CAPACITY, request_ids=list(shared.REQUEST_IDS),
                    shared_path=NEW_PATH.decode('ascii'), version_word='02 0A 15 1B',
                    metadata_unchanged=True, checksum_verified=True,
                    builder='Darkslide A-drive shutdown builder',
                    research_memory_only=True, candidate_file_written=False,
                    exportable=False, ui_integrated=False, camera_verified=False,
                    device_installation_verified=False, independent_entropy_decode_required=True)
    except (codec.BuildError, shared.SharedSlotError, package.PackageError) as exc:
        raise AShutdownError(str(exc)) from exc
