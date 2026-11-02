"""Classify an imported firmware file."""
import hashlib

from . import models


def digest_of(blob):
    """Hex SHA-256 of a byte string (the content address used for inputs, assets and builds)."""
    return hashlib.sha256(blob).hexdigest()


sha256 = digest_of  # older name; tests/test_grain.py still imports it

# SHA-256 of the first 128 bytes of each official file. Builds keep that header
# unchanged (version and container fields), so a file that has it but is not the
# official original is a modified copy of that firmware, for example an earlier
# DARKSLIDE GR build. Only the digest is kept here, no firmware content.
MODIFIED_HEADERS = {
    "11d1c6843c64cc9326e5ea23dd0b2f4342a9da00e79a528e8eb3e9a2cced362f": ("GR III", "2.10", "gr3_v210.zip"),
    "1e42465ba4a4fa33d0dc94d8519cfb8f424b90ee9161a06eeda43f6cae140a90": ("GR IIIx", "1.60", "gr3x_v160.zip"),
    "431eb4f71362ee8f2f40788950ced2421112edcd82563a3af40449a96dc82511": ("GR III", "2.20", "gr3_v220.zip"),
    "919b915f5d7e34277cc88fb97af2a61720526a6a50b7fc8a86ad3d4b9c85f944": ("GR IIIx", "1.70", "gr3x_v170.zip"),
}


def identify(data, filename, template, builds):
    """Return dict(kind=official|generated|rejected, sha256, reason, ...).

    builds: list of past build records (each with output_sha256 and project_id).
    """
    digest = digest_of(data)
    base = dict(sha256=digest, size=len(data), filename=filename)
    for item in template.get("official_inputs", []):
        if digest == item["sha256"]:
            return dict(base, kind="official", model=template["model"], firmware=template["firmware"],
                        reason="官方 %s %s 原件" % (template["model"], template["firmware"]))
    for build in builds:
        if build.get("output_sha256") == digest:
            return dict(base, kind="generated", project_id=build.get("project_id"),
                        reason="这是 DARKSLIDE GR 生成的文件，请打开对应工程继续编辑")
    header = MODIFIED_HEADERS.get(digest_of(data[:128]))
    if header:
        model, firmware, archive = header
        reason = ("这是改过的 %s %s 固件（比如 DARKSLIDE GR 之前生成的修改版），不是官方原件。请导入官方原件：可以点上面的「下载 %s %s」，把 %s 直接拖进来。"  # noqa: E501 one string, so it translates whole
                  % (model, firmware, model, firmware, archive))
        return dict(base, kind="rejected", reason=reason)
    # An unknown file is checked against the default template only, so name the look-alike from every supported
    # original and list all supported versions, not just the template's one.
    official = template.get("official_inputs", [{}])[0]
    alike = [(official.get("size"), official.get("filename"), template["model"], template["firmware"])]
    alike += [(m.input_size, m.filename, m.name, m.firmware) for m in models.ALL]
    match = next((a for a in alike if len(data) == a[0] and filename == a[1]), None)
    if match:
        reason = "文件名和大小像 %s %s，但内容不是官方原件（可能被改过）" % match[2:]
    elif filename.lower().startswith("fwdc") and filename.lower().endswith(".bin"):
        reason = "看起来是理光固件，但不是支持的官方原件。支持：%s" % supported()
    else:
        reason = "不是支持的官方固件。支持：%s" % supported()
    return dict(base, kind="rejected", reason=reason)


def supported():
    """Supported originals, e.g. 'GR III 2.10 / 2.20 · GR IIIx 1.60 / 1.70' (names stay untranslated)."""
    versions = {}
    for m in models.ALL:
        versions.setdefault(m.name, []).append(m.firmware)
    return " · ".join("%s %s" % (name, " / ".join(sorted(fw))) for name, fw in versions.items())
