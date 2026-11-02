"""Accept the official download as-is: Ricoh ships GR III 2.10 as gr3_v210.zip with
fwdc239b.bin inside. unwrap() takes the .bin out in memory so the user does not have
to unzip first; anything that is not a zip passes through unchanged."""

import io
import zipfile
from pathlib import PurePosixPath

FIRMWARE_NAME = "fwdc239b.bin"
MAX_MEMBER = 64 * 1024 * 1024  # same ceiling as an uploaded .bin (server MAX_BODY)
ZIP_MAGIC = b"PK\x03\x04"


class ZipError(ValueError):
    """The upload is a zip, but not one Darkslide can take the firmware from."""


def is_zip(data):
    return data[:4] == ZIP_MAGIC


def unwrap(data, filename):
    """(firmware bytes, filename) from an upload. A zip must hold exactly one
    fwdc239b.bin (any folder, any letter case); its bytes and name are returned.
    Other data is returned unchanged. Raises ZipError with a user-facing reason."""
    if not is_zip(data):
        return data, filename
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [m for m in zf.infolist()
                       if not m.is_dir() and PurePosixPath(m.filename).name.lower() in (FIRMWARE_NAME, "fwdc243b.bin")]
            if not members:
                raise ZipError("压缩包里没有已支持的官方固件，请导入 GR III 2.10 / 2.20 或 GR IIIx 1.60 / 1.70 官方包")
            if len(members) > 1:
                raise ZipError("压缩包里有不止一个固件候选，请解压后只导入所需官方 BIN")
            member = members[0]
            if member.file_size > MAX_MEMBER:
                raise ZipError("压缩包里的 fwdc239b.bin 太大，不是 GR III 固件")
            with zf.open(member) as fh:
                body = fh.read(MAX_MEMBER + 1)
            if len(body) > MAX_MEMBER:
                raise ZipError("压缩包里的 fwdc239b.bin 太大，不是 GR III 固件")
            return body, PurePosixPath(member.filename).name
    except ZipError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, OSError, EOFError):
        raise ZipError("压缩包打不开（可能没下载完或已损坏），请重新下载，或者解压后拖入 fwdc239b.bin")
