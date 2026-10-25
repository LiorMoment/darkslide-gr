"""Capture the 柔光 (soft glow) patch windows from the delivered test firmware.

    python3 tools/capture_soft_glow.py <official fwdc239b.bin> <soft-glow fwdc239b.bin>

Compares the two decoded GR III 2.10 payloads and writes darkslide/soft_glow_windows.json:
every changed run (runs closer than 16 bytes are joined) as [payload offset, original hex,
replacement hex]. The final payload checksum word is left out: packing recomputes it.
Both files are pinned by SHA-256, so only the reviewed v22.6 delivery can be captured.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from darkslide import fwread, models  # noqa: E402

VERSION = "v22.6"
SOURCE_SHA256 = "ece36e0f21d3eff0104015202d7dcfc96709cae303b2a8f2c69052e2dd625931"
SOURCE_PAYLOAD_SHA256 = "97e00581d1b9fb9d58b28c283d9730ae9c477163418e74767877cef170b3e9e6"
JOIN = 16


def sha(data):
    return hashlib.sha256(data).hexdigest()


def runs(before, after):
    out = []
    end = len(before) - 4
    for block in range(0, end, 4096):
        if before[block:block + 4096] == after[block:block + 4096]:
            continue
        for k in range(block, min(end, block + 4096)):
            if before[k] != after[k]:
                if out and k - out[-1][1] <= JOIN:
                    out[-1][1] = k + 1
                else:
                    out.append([k, k + 1])
    return out


def main(official_path, glow_path):
    official, glow = Path(official_path).read_bytes(), Path(glow_path).read_bytes()
    if sha(official) != models.GR3.input_sha or sha(glow) != SOURCE_SHA256:
        raise SystemExit("not the official GR III 2.10 file and the reviewed %s delivery" % VERSION)
    before, after = bytes(fwread.unpack(official)), bytes(fwread.unpack(glow))
    if sha(before) != models.GR3.payload_sha or sha(after) != SOURCE_PAYLOAD_SHA256 or len(before) != len(after):
        raise SystemExit("decoded payload identity differs")
    windows = [[s, before[s:e].hex(), after[s:e].hex()] for s, e in runs(before, after)]
    data = dict(schema=1, feature="soft_glow", version=VERSION, source_bin_sha256=SOURCE_SHA256,
                source_payload_sha256=SOURCE_PAYLOAD_SHA256, official_payload_sha256=models.GR3.payload_sha,
                changed_bytes=sum(len(w[1]) // 2 for w in windows), windows=windows)
    target = ROOT / "darkslide" / "soft_glow_windows.json"
    target.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    print("%d windows, %d bytes -> %s (sha256 %s)" % (len(windows), data["changed_bytes"], target,
                                                     sha(target.read_bytes())))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
