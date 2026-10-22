#!/usr/bin/env python3
"""Check that the desktop app ships every part of the repository the program reads.

    python3 desktop/scripts/check_resources.py [built app's resources folder ...]

The app bundles what mygo.config.ts lists under `resources`. When the program starts
reading another top-level folder or file (as darkslide/ reads film_curves/ and
templates/), the app would lack it and fail at run time. This finds every top-level
name of the repository that darkslide/*.py or launcher.py spells as a string, and
fails when one of them is not listed. Given built resources folders (an app's
Contents/Resources, or the folder next to Darkslide GR.exe), it also checks each
listed entry is there. Exit code 0 when nothing is missing.
"""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
REPO = DESKTOP.parent
# Top-level names the program spells without reading them from the app's folder.
NOT_READ = {
    "desktop": "this app itself",
    "README.md": "photo_recipe.py reads film_curves/README.md, which ships with film_curves",
}


def listed():
    """Base names of the config's resources, as the CLI evaluates it."""
    out = subprocess.run(["node", "--input-type=module", "-e",
                          "const c = (await import(process.argv[1])).default;"
                          "console.log(JSON.stringify(c.resources || []));",
                          (DESKTOP / "mygo.config.ts").as_uri()],
                         check=True, capture_output=True, text=True, cwd=DESKTOP)
    return {Path(p).name for p in json.loads(out.stdout)}


def spelled(top):
    """Top-level names that appear as string literals in the program."""
    found = {}
    sources = sorted((REPO / "darkslide").rglob("*.py")) + [REPO / "launcher.py"]
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for part in re.split(r"[/\\]", node.value):
                    if part in top:
                        found.setdefault(part, path.relative_to(REPO).as_posix())
    return found


def main():
    top = {p.name for p in REPO.iterdir() if not p.name.startswith(".")}
    have = listed()
    need = {"launcher.py": "launcher.py", "darkslide": "launcher.py"}
    need.update(spelled(top))
    missing = sorted(n for n in need if n not in have and n not in NOT_READ)
    for name in missing:
        print("missing from mygo.config.ts resources: %s (read by %s)" % (name, need[name]))
    for folder in map(Path, sys.argv[1:]):
        for name in sorted(have):
            if not (folder / name).exists():
                missing.append(name)
                print("missing from %s: %s" % (folder, name))
    if not missing:
        print("resources complete: %s" % ", ".join(sorted(have)))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
