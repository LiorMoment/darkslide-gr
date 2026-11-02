"""Rescue gate: offline checks of a few listed rules that, if broken, are known to
stop the official GR III 2.10 package from being re-flashed over this file.

Third, request-agnostic layer after the builder and the composite read-back.
It knows nothing about icons, names or the shutdown image; it only compares
the official input with the output and refuses any file that breaks one of the
rules below. The only user-reachable update route is MENU + power-on with the
official 2.10 package on the SD card; passing these rules does not prove the
camera will accept that package, finish writing it, or boot afterwards:

  * the two version words must stay 02 0A 15 1B (2.10) — a higher version makes
    the updater reject the official package as "older" (code analysis);
  * every boot-chain record (BOOTPARA, DRAMPARA, PARTPARA, M0, BOOT, CPU) and
    every other record outside RTOS / ICONBIN / RES must be byte-identical;
  * inside RTOS, the updater and the boot/update flow must be untouched;
  * the decoded payload must keep its length and record layout (in-place rule).

Imports the independent reader only; never writes anything.
Basis: the author's own analysis of the updater checks, write order and
updater addresses in the official 2.10 package; implemented here without
third-party code.
"""
import struct

from . import fwread, models
import hashlib

VERSION_WORD = b"\x02\x0a\x15\x1b"
VERSION_OFFSETS = (0x38, 0x50)
MAY_CHANGE = ("RTOS", "ICONBIN", "RES")
BOOT_CHAIN = ("BOOTPARA", "DRAMPARA", "PARTPARA", "M0", "BOOT", "CPU")
RTOS_VA, RTOS_BODY = fwread.V210["rtos_va"], fwread.V210["rtos_body"]
# (id, title, VA start, VA end) — RTOS windows that hold the recovery path itself.
PROTECTED = (
    ("rescue_updater_window", "更新器代码未被改动（版本比较、校验、写入调度）", 0x53620000, 0x53640000),
    ("rescue_bootflow_window", "开机与更新流程代码未被改动", 0x53820000, 0x53840000),
)
# GR III 2.20 / GR IIIx 1.70 (rebuilt main system): the same two windows, re-located function by
# function (431 updater and 609 boot/update-flow functions paired in order; the boot/update-flow
# window grew by 0x38). Located by the author's own comparison of the official 2.10 and 2.20 packages.
PROTECTED_220 = (
    ("rescue_updater_window", "更新器代码未被改动（版本比较、校验、写入调度）", 0x536200F0, 0x536400F0),
    ("rescue_bootflow_window", "开机与更新流程代码未被改动", 0x5381FFD8, 0x53840010),
)
# Decoded length -> windows. The two firmware generations differ in length.
WINDOWS_BY_LENGTH = {59718388: PROTECTED, 59798532: PROTECTED_220}


class GateError(ValueError):
    pass


def _need(ok, message):
    if not ok:
        raise GateError(message)


def records(payload):
    """16-byte record headers: name(8) type(2) index(2) length(4); RES is last."""
    out, at = [], 0
    while at + 16 <= len(payload):
        name = payload[at:at + 8].rstrip(b"\0")
        if not name or any(c < 32 or c > 126 for c in name):
            break
        _, _, size = struct.unpack_from("<HHI", payload, at + 8)
        if name == b"RES":
            out.append(("RES", at, len(payload)))
            break
        _need(at + 16 + size <= len(payload), "record beyond payload")
        out.append((name.decode(), at, at + 16 + size))
        at += 16 + size
    _need(len(out) >= 6 and out[-1][0] == "RES", "record table")
    return out


def _first_difference(a, b):
    step = 4096
    for i in range(0, len(a), step):
        if a[i:i + step] != b[i:i + step]:
            for k in range(i, min(len(a), i + step)):
                if a[k] != b[k]:
                    return k
    return None


def gate_payloads(before, after):
    """Checks on two decoded payloads (official, generated)."""
    checks = []

    def add(cid, title, ok, detail=""):
        checks.append(dict(id=cid, title=title, ok=bool(ok), detail=detail))

    add("rescue_length", "解码长度与原件相同（原位替换，不扩容）", len(before) == len(after),
        "%d → %d" % (len(before), len(after)))
    if len(before) != len(after):
        return checks
    try:
        old, new = records(before), records(after)
    except GateError as exc:
        add("rescue_layout", "记录表布局与原件相同", False, str(exc))
        return checks
    add("rescue_layout", "记录表布局与原件相同", old == new,
        "" if old == new else "记录名或边界不同")
    if old != new:
        return checks
    changed = {}
    for name, start, end in old:
        if before[start:end] != after[start:end]:
            changed[name] = _first_difference(before[start:end], after[start:end]) + start
    boot_bad = [n for n in BOOT_CHAIN if n in changed]
    add("rescue_boot_chain", "引导链记录（BOOTPARA/DRAMPARA/PARTPARA/M0/BOOT/CPU）与原件逐字节相同",
        not boot_bad, "、".join(boot_bad))
    other_bad = [n for n in changed if n not in MAY_CHANGE and n not in BOOT_CHAIN]
    add("rescue_other_records", "RTOS、ICONBIN、RES 以外的记录与原件逐字节相同", not other_bad,
        "、".join(other_bad))
    # Only the 2.20 / 1.70 length switches windows; everything else is checked exactly as before.
    for cid, title, lo, hi in WINDOWS_BY_LENGTH.get(len(before), PROTECTED):
        a, b = lo - RTOS_VA + RTOS_BODY, hi - RTOS_VA + RTOS_BODY
        hit = _first_difference(before[a:b], after[a:b])
        add(cid, title, hit is None, "" if hit is None else "首个差异 VA 0x%08X" % (lo + hit))
    return checks


def gate(official, output, model_id=None):
    """Checks on the raw files. Failures never authorize a download."""
    checks = []

    def add(cid, title, ok, detail=""):
        checks.append(dict(id=cid, title=title, ok=bool(ok), detail=detail))

    try:
        model = models.get(model_id)
        add("rescue_input_identity", "官方原件机型身份", hashlib.sha256(official).hexdigest() == model.input_sha)
        _need(type(official) is bytes and type(output) is bytes and len(output) >= 0x80, "inputs")
        words = [output[o:o + 4] for o in VERSION_OFFSETS]
        add("rescue_version", ("版本字段保持 %s 原值；仅静态核对，同版本回刷未实测" % model.firmware) if models.is_220(model) else
            ("版本字段保持 %s 原值；仅静态核对，IIIx 同版本回刷未实测" % model.firmware) if model == models.GR3X else "版本字段仍是 2.10（02 0A 15 1B）：版本检查通过；旧版曾实测刷回，本次第五槽、画幅与柔光组合尚未验证刷回",
            all(w == model.version for w in words) and
            all(official[o:o + 4] == model.version for o in VERSION_OFFSETS),
            " / ".join(w.hex() for w in words))
        before, after = fwread.unpack(official), fwread.unpack(output)
        checks += gate_payloads(before, after)
    except (GateError, fwread.FirmwareReadError, ValueError, TypeError, struct.error) as exc:
        add("rescue_closed_failure", "救机门禁核对过程出错", False, str(exc))
    return checks


def all_ok(checks):
    return type(checks) is list and bool(checks) and all(c.get("ok") is True for c in checks)


def rescue_note(model):
    """The rescue card wording for this model's official package."""
    if model == models.GR3:
        return dict(RESCUE_NOTE)
    return dict(RESCUE_NOTE, official_file="%s（理光官方 %s %s 原包）" % (model.filename, model.name, model.firmware),
                official_sha256=model.input_sha)


RESCUE_NOTE = dict(
    official_file="fwdc239b.bin（理光官方 GR III 2.10 原包）",
    official_sha256=fwread.V210_INPUT_SHA256,
    how="满电（DB-110 或 K-AC166J）→ 插入只放官方原包的救机卡 → 按住 MENU 键开机 → 确认更新；全程不关机、不开电池仓、不拔卡",
    covers="按住 MENU 开机能见到固件更新画面时可以尝试（包括黑屏或断电后仍能见到的情况）；进入更新画面只说明更新流程前段能运行，官方包是否被接受、写入是否成功、之后能否启动不能事先保证",
    not_covered="按住 MENU 开机见不到固件更新画面（完全开不了机、更新中断电后进不了更新画面）：目前没有已验证的用户恢复通道，建议停手并联系售后",
)
