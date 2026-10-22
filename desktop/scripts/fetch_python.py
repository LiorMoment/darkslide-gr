#!/usr/bin/env python3
"""下載並解壓縮固定版本的 python-build-standalone 執行環境到 MyGo 平台資源目錄。

用法：
    python3 desktop/scripts/fetch_python.py [--platform darwin-arm64 ...] [--cache DIR]

僅使用標準函式庫（Python 3.9+）。下載檔必須同時通過大小與 sha256 驗證才會解壓縮。
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import quote

RELEASE = "20261013"
BASE_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/" + RELEASE
MARKER_NAME = "DARKSLIDE_RUNTIME.json"

DESKTOP_DIR = Path(__file__).resolve().parent.parent

# 平台 -> (asset, size, sha256)
ASSETS = {
    "darwin-arm64": (
        "cpython-3.13.16+20261013-aarch64-apple-darwin-install_only_stripped.tar.gz",
        25268458,
        "16a5332fb0dcb77f163fc8f8cbccd95be1644cc53e68e5d2a79f50afd6680a0e",
    ),
    "darwin-amd64": (
        "cpython-3.13.16+20261013-x86_64-apple-darwin-install_only_stripped.tar.gz",
        25001913,
        "f507ae04eb7747cd97cb484e8c5d83936cbfc52d9b08a1f35c00b47c707b1ae1",
    ),
    "windows-amd64": (
        "cpython-3.13.16+20261013-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
        21987516,
        "7aba3985d4cee1c7cacb963ca6aa08a5d3e695d7f9609d9fabc56f22ddcf5a06",
    ),
}

# 相對於解壓後 python/ 的可移除項目（不存在則略過），依平台分開。支援 glob，比對區分大小寫：
# 在不分大小寫的檔案系統上，Unix 的 lib/thread* 會誤配 Windows 的 Lib/threading.py。
PRUNE = {
    "darwin": [
        "lib/python3.*/test",
        "lib/python3.*/idlelib",
        "lib/python3.*/tkinter",
        "lib/python3.*/turtledemo",
        "lib/python3.*/ensurepip",
        "lib/python3.*/lib2to3",
        "lib/tcl*", "lib/tk*", "lib/itcl*", "lib/thread*", "lib/libtcl*",
        "include",
        "share",
    ],
    "windows": [
        "Lib/test", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo",
        "Lib/ensurepip", "Lib/lib2to3",
        "tcl",
        "include",
    ],
}


def die(msg: str):
    print("錯誤：" + msg, file=sys.stderr)
    sys.exit(1)


def fmt(n: int) -> str:
    return "%.1f MiB" % (n / 1048576)


def tree_size(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path: Path, size: int, sha: str) -> Optional[str]:
    """回傳錯誤訊息；通過則回傳 None。"""
    actual = path.stat().st_size
    if actual != size:
        return "大小不符（預期 %d，實際 %d）" % (size, actual)
    digest = sha256_of(path)
    if digest != sha:
        return "sha256 不符（預期 %s，實際 %s）" % (sha, digest)
    return None


def ensure_download(plat: str, cache: Path) -> Path:
    asset, size, sha = ASSETS[plat]
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / asset
    if dest.exists():
        err = verify(dest, size, sha)
        if err is None:
            print("[%s] 快取命中：%s" % (plat, dest))
            return dest
        print("[%s] 快取檔無效（%s），刪除並重新下載" % (plat, err))
        dest.unlink()
    url = BASE_URL + "/" + quote(asset, safe="")
    print("[%s] 下載 %s" % (plat, url))
    part = dest.with_name(dest.name + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "darkslide-fetch-python"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
            got, last = 0, 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                got += len(chunk)
                if got - last >= 5 << 20:
                    last = got
                    print("    %s / %s" % (fmt(got), fmt(size)), flush=True)
    except Exception as e:  # noqa: BLE001
        if part.exists():
            part.unlink()
        die("[%s] 下載失敗：%s" % (plat, e))
    err = verify(part, size, sha)
    if err:
        part.unlink()
        die("[%s] 驗證失敗，已刪除下載檔：%s" % (plat, err))
    part.replace(dest)
    return dest


def _inside(base: PurePosixPath, target: PurePosixPath) -> bool:
    return target == base or base in target.parents


def check_member(m: tarfile.TarInfo) -> None:
    """拒絕絕對路徑、`..`、指向樹外的連結與特殊檔案（所有 Python 版本皆執行）。"""
    p = PurePosixPath(m.name)
    if p.is_absolute() or ".." in p.parts or (":" in m.name and "\\" in m.name):
        die("tar 成員路徑不安全：%s" % m.name)
    if not p.parts or p.parts[0] != "python":
        die("tar 成員不在頂層 python/ 之下：%s" % m.name)
    if m.isdev() or m.isfifo():
        die("tar 含不允許的特殊檔案：%s" % m.name)
    if m.issym() or m.islnk():
        link = PurePosixPath(m.linkname)
        if link.is_absolute():
            die("連結使用絕對路徑：%s -> %s" % (m.name, m.linkname))
        if m.issym():
            resolved = PurePosixPath(os.path.normpath(str(p.parent / link)))
        else:  # hardlink 的 linkname 相對於 archive 根
            resolved = PurePosixPath(os.path.normpath(str(link)))
        if resolved.is_absolute() or ".." in resolved.parts or not _inside(PurePosixPath("python"), resolved):
            die("連結指向樹外：%s -> %s" % (m.name, m.linkname))


def safe_extract(archive: Path, dest: Path) -> None:
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        for m in members:
            check_member(m)
        if hasattr(tarfile, "data_filter"):
            tf.extractall(dest, members=members, filter="data")
        else:
            tf.extractall(dest, members=members)


def prune(root: Path, plat: str) -> None:
    for pat in PRUNE[plat.split("-")[0]]:
        for p in root.glob(pat):
            if not fnmatch.fnmatchcase(p.relative_to(root).as_posix(), pat):
                continue
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists() or p.is_symlink():
                p.unlink()
    # The app bundle is read-only (and runs with -B), so the standard library is
    # compiled here, once: bytecode of one 3.13 serves every platform's 3.13.
    if sys.version_info[:2] == (3, 13):
        import compileall
        for lib in [p for p in (root / "lib").glob("python3.*")] + [root / "Lib"]:
            if lib.is_dir():
                compileall.compile_dir(lib, quiet=1, workers=0)
    else:
        print("    （本機不是 Python 3.13，略過預編譯，啟動時會較慢）")


def read_marker(target: Path):
    try:
        return json.loads((target / MARKER_NAME).read_text())
    except (OSError, ValueError):
        return None


def install(plat: str, cache: Path) -> None:
    asset, size, sha = ASSETS[plat]
    target = DESKTOP_DIR / "resources" / plat / "python"
    marker = read_marker(target)
    if marker and marker.get("asset") == asset and marker.get("sha256") == sha:
        print("[%s] 已是最新（%s），略過" % (plat, asset))
        return
    archive = ensure_download(plat, cache)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".extract-", dir=parent))
    backup = None
    try:
        print("[%s] 解壓縮（已驗證 size+sha256）" % plat)
        safe_extract(archive, tmp)
        new = tmp / "python"
        if not new.is_dir():
            die("[%s] 壓縮檔缺少頂層 python/ 目錄" % plat)
        before = tree_size(new)
        prune(new, plat)
        lib = new / "Lib" if plat.startswith("windows") else next(new.glob("lib/python3.*"))
        for module in ("threading.py", "socket.py", "http/server.py", "json/__init__.py", "sqlite3/__init__.py"):
            if not (lib / module).is_file():
                die("[%s] 精簡後缺少必要模組 %s" % (plat, module))
        after = tree_size(new)
        print("[%s] 精簡前 %s -> 精簡後 %s" % (plat, fmt(before), fmt(after)))
        (new / MARKER_NAME).write_text(
            json.dumps({"asset": asset, "sha256": sha, "size": size, "release": RELEASE}, indent=2) + "\n"
        )
        if os.path.lexists(target):
            backup = parent / (".old-" + tmp.name)
            os.replace(target, backup)
        os.replace(new, target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
    print("[%s] 完成：%s" % (plat, target))


def main() -> None:
    ap = argparse.ArgumentParser(description="下載並安裝 python-build-standalone 執行環境")
    ap.add_argument("--platform", action="append", choices=sorted(ASSETS),
                    help="目標平台，可重複指定（預設全部）")
    ap.add_argument("--cache", type=Path, default=DESKTOP_DIR / ".cache" / "python",
                    help="下載快取目錄（預設 desktop/.cache/python）")
    args = ap.parse_args()
    for plat in args.platform or sorted(ASSETS):
        install(plat, args.cache.resolve())


if __name__ == "__main__":
    main()
