# 自带 Python 的第三方许可 / Licences of the bundled Python

The desktop app ships a Python from [python-build-standalone](https://github.com/astral-sh/python-build-standalone) (release 20261013, CPython 3.13.16, the build `scripts/fetch_python.py` pins). That Python has these libraries built in (OpenSSL, bzip2, xz/liblzma, libffi, mpdecimal, SQLite, zlib, expat, …); their licences ask for their notices to travel with it.

The texts here are the `python/licenses/` folder of the same release's "full" archives, copied unchanged:

| Folder | From |
|---|---|
| `macos/` | `cpython-3.13.16+20261013-aarch64-apple-darwin-pgo+lto-full.tar.zst` (sha256 3497822d…4362; the x86_64 macOS archive has the same texts) |
| `windows/` | `cpython-3.13.16+20261013-x86_64-pc-windows-msvc-pgo-full.tar.zst` (sha256 784db2d7…4b46) |

Python's own licence is `LICENSE.cpython.txt`. When `fetch_python.py` moves to another release, replace these folders with that release's.
