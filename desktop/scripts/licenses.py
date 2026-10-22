#!/usr/bin/env python3
"""Write desktop/DESKTOP_LICENSES.md: the desktop shell's own licence, and the full licence
text of everything built into it or bundled with it.

    python3 desktop/scripts/licenses.py [--check]

The Go modules come from `go list -deps` for macOS and Windows; their texts are read from the
module cache, Go's own from GOROOT. --check fails when the file is out of date (CI runs it).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
OUT = DESKTOP / "DESKTOP_LICENSES.md"

HEADER = """# DARKSLIDE GR desktop app: licences

The desktop app is two programs side by side:

- **DARKSLIDE GR** itself (`launcher.py`, `darkslide/`, `web/`, `templates/`, `film_curves/`,
  `native/`), unchanged, under **GPL-2.0-only**: see `LICENSE` and `THIRD_PARTY_NOTICES.md`
  next to this file. Its source: https://github.com/LiorMoment/darkslide-gr
- **The desktop shell** (`desktop/` in https://github.com/LiorMoment/darkslide-gr), which
  starts DARKSLIDE GR's own local server and shows its page in
  a window. The shell is a separate program and talks to DARKSLIDE GR only over local HTTP. It is
  under the **MIT** licence below, and is built with the Go modules listed after it.

The bundled Python (CPython 3.13 from python-build-standalone) carries its licence in the app:
`python/lib/python3.13/LICENSE.txt` on macOS, `python/LICENSE.txt` on Windows.

## The desktop shell

MIT License

Copyright (c) 2026 Ady0402 and DARKSLIDE GR desktop contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def modules():
    found = {}
    for goos in ("darwin", "windows"):
        out = subprocess.run(["go", "list", "-deps", "-json", "."], cwd=DESKTOP, capture_output=True, text=True,
                             check=True, env=dict(os.environ, GOOS=goos)).stdout
        dec, i = json.JSONDecoder(), 0
        while i < len(out):
            obj, i = dec.raw_decode(out, i)
            while i < len(out) and out[i].isspace():
                i += 1
            m = obj.get("Module")
            if m and not m.get("Main"):
                found.setdefault(m["Path"], m)
    return [found[k] for k in sorted(found)]


def licence_files(folder):
    return [f for f in sorted(os.listdir(folder)) if f.lower().startswith(("licen", "copying", "notice"))]


def render():
    parts = [HEADER, "## Built into the shell\n"]
    goroot = Path(subprocess.run(["go", "env", "GOROOT"], capture_output=True, text=True, check=True).stdout.strip())
    # Go's own texts are in GOROOT, or one folder up in Homebrew's layout. No version for Go,
    # so the file does not change with the Go patch release a machine builds with.
    go_files = [next(d / f for d in (goroot, goroot.parent) if (d / f).exists())
                for f in ("LICENSE", "PATENTS") if any((d / f).exists() for d in (goroot, goroot.parent))]
    entries = [("Go standard library and runtime", "", go_files)]
    entries += [(m["Path"], m["Version"], [Path(m["Dir"], f) for f in licence_files(m["Dir"])]) for m in modules()]
    for name, ver, files in entries:
        if not files:
            raise SystemExit("no licence file for %s" % name)
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace").rstrip()
            parts.append("### %s (%s)\n\n```\n%s\n```\n" % (" ".join(filter(None, (name, ver))), path.name, text))
    return "\n".join(parts)


def main():
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("DESKTOP_LICENSES.md is out of date: run python3 desktop/scripts/licenses.py")
            return 1
        print("DESKTOP_LICENSES.md is up to date")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
