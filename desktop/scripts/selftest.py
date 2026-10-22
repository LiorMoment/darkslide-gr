#!/usr/bin/env python3
"""Drive a selftest build of the desktop app through its window, as a user would.

    python3 desktop/scripts/selftest.py <app executable> [--firmware fwdc239b.bin] [--report out.json]

The app must be built with `-tags selftest` (see desktop/README.md). Its data goes to
a fresh temporary folder, never the user's own. Without --firmware it checks what
needs no RICOH file: the page loads, a file reaches the server through the page's
import code, a server file and a blob download through the webview's download path,
links leave for the browser, and the page's 退出 ends the app and its server.

With --firmware it also makes the same project on every platform (RECIPE: an
imported .cube, sliders, a picture icon, a new name and a shutdown picture),
generates the firmware and downloads it through 下载固件. --report writes what was
made (inputs, the page's pictures, the bytes written), as JSON, to compare platforms.

Exit code 0 when every check passed.
"""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

CONTROL_PORT = 9911
CONTROL = "http://127.0.0.1:%d" % CONTROL_PORT
REPO = Path(__file__).resolve().parents[2]
results = []

# The same project on every platform: what a user would set by hand.
RECIPE = {
    # Four imported .cube looks, one on each LUT preset, each renamed.
    "luts": [
        {"slot": 8, "look": "warm", "name": "Warm A"},
        {"slot": 9, "look": "cool", "name": "Cool B"},
        {"slot": 10, "look": "fade", "name": "Fade C"},
        {"slot": 11, "look": "punch", "name": "Punch D"},
    ],
    "grade_slot": 11,                                  # sliders on top of that preset's LUT
    "grade": {"temp": 10, "con": 15, "sat": -10},
    "icon_slot": 2,                                    # a picture as the icon, and a new name
    "name": "Selftest",
    # Picked by hand: left alone, the page guesses it from the system language, which
    # differs between machines and changes the menu text written into the firmware.
    "menu_language": "zh-TW",
}

# Each look maps r, g, b in 0..1 to the output colour.
LOOKS = {
    "warm": lambda r, g, b: (min(1, 0.04 + 0.96 * r ** 0.92), 0.03 + 0.95 * g, 0.02 + 0.9 * b ** 1.08),
    "cool": lambda r, g, b: (0.02 + 0.9 * r ** 1.1, 0.02 + 0.96 * g, min(1, 0.05 + 0.95 * b ** 0.9)),
    "fade": lambda r, g, b: tuple(0.12 + 0.8 * (0.85 * v + 0.15 * (r + g + b) / 3) for v in (r, g, b)),
    "punch": lambda r, g, b: tuple(min(1, max(0, 0.5 + (v - 0.5) * 1.35)) for v in (r, g, b)),
}


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (" — " + str(detail) if detail else ""), flush=True)
    return ok


def run_js(code, timeout=180):
    """Run JavaScript in the page; an async function's value is awaited."""
    req = urllib.request.Request(CONTROL + "/" + "ev" + "al", data=code.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.load(resp)
    if out.get("error"):
        raise RuntimeError(out["error"])
    value = out.get("value")
    return json.loads(value) if isinstance(value, str) and value[:1] in "[{\"" else value


def downloads():
    with urllib.request.urlopen(CONTROL + "/downloads", timeout=5) as resp:
        return json.load(resp)


def wait_download(url_part, before, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for row in downloads()[before:]:
            if url_part in row["URL"]:
                return row
        time.sleep(0.25)
    return None


def file_url(path):
    return CONTROL + "/file?path=" + urllib.parse.quote(str(path))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def wait_page(proc, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            return None
        try:
            state = run_js('JSON.stringify({href: location.href, ready: document.readyState, drop: !!document.getElementById("drop")})', timeout=5)
            if state["href"].startswith("http://127.0.0.1:") and state["ready"] == "complete" and state["drop"]:
                return state["href"].rstrip("/")
        except Exception:  # the control channel or the page is not up yet
            pass
        time.sleep(0.25)
    return None


def fresh_page(proc):
    """Reload the page. Chromium (WebView2) lets a page start one download without a
    click of the user's; later ones wait for a permission nobody grants here. A person's
    click resets that, the scripted clicks of this test do not, so each download check
    starts from its own page load."""
    run_js("(() => { location.reload(); return 'reloading'; })()")
    time.sleep(1)
    return wait_page(proc)


def make_fixtures(work):
    """The recipe's files, made byte for byte the same on every platform."""
    n = 17
    cubes = {}
    for look, fn in LOOKS.items():
        rows = []
        for b in range(n):
            for g in range(n):
                for r in range(n):
                    rows.append("%.6f %.6f %.6f" % fn(r / (n - 1), g / (n - 1), b / (n - 1)))
        cube = work / ("selftest-%s.cube" % look)
        cube.write_bytes(('TITLE "Selftest %s"\nLUT_3D_SIZE %d\n' % (look.title(), n) + "\n".join(rows) + "\n").encode("ascii"))
        cubes[look] = cube
    # One picture, a JPEG photo as most are, for both the icon and the shutdown picture:
    # what differs between platforms is then the page's handling, not the input.
    picture = REPO / "tests" / "fixtures" / "shutdown_gradient.jpg"
    return cubes, picture, picture


HELPERS = """
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (fn, ms = 60000) => { for (let t = 0; t < ms; t += 200) { if (fn()) return true; await wait(200); } return false; };
  // A file chosen in an <input type=file>, as the picker leaves it; the page's own change handler runs.
  const choose = async (id, url, name, type) => {
    const blob = await (await fetch(url)).blob();
    const dt = new DataTransfer(); dt.items.add(new File([blob], name, { type }));
    const input = document.getElementById(id); input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const pickSlot = async (no) => { document.querySelectorAll(".slot")[listedSlots().findIndex((s) => s.no === no)].click(); await wait(800); };
  const toasts = () => [...document.querySelectorAll(".toast")].map((t) => t.innerText).join(" / ");
  const rename = async (text) => {
    const name = document.getElementById("nameInput");
    name.value = text; name.dispatchEvent(new Event("input", { bubbles: true })); name.dispatchEvent(new Event("change", { bubbles: true }));
    await wait(300);
  };
"""


def generate_and_download(origin, firmware, work, report, luts=None, settle=0):
    """RECIPE through the page, then 生成 and 下载固件. `luts`, four (path, name) pairs, replace
    the made-up looks, e.g. the user's own Fuji .cube files."""
    cubes, icon, shutdown = make_fixtures(work)
    if luts:
        RECIPE["luts"] = [dict(slot=lut["slot"], look=Path(path).stem, name=name) for lut, (path, name) in zip(RECIPE["luts"], luts)]
        cubes = {Path(path).stem: Path(path) for path, _ in luts}
    report["inputs"] = dict({"firmware": sha256(firmware), "icon": sha256(icon), "shutdown": sha256(shutdown), "recipe": RECIPE},
                            **{"cube " + look: sha256(path) for look, path in cubes.items()})
    # The picture's pixels as this webview decodes them, at full size: with the page's
    # default decoding, without colour management, and through an <img> as loadImage does.
    # Equal everywhere means a deterministic resize alone makes the page's output equal.
    report["decoded"] = run_js("""(async () => {
      const blob = await (await fetch(%s)).blob();
      const hash = async (source) => {
        const c = document.createElement("canvas"); c.width = source.width; c.height = source.height;
        const g = c.getContext("2d"); g.drawImage(source, 0, 0);
        const px = g.getImageData(0, 0, c.width, c.height).data;
        const d = new Uint8Array(await crypto.subtle.digest("SHA-256", px));
        return [...d].map((b) => b.toString(16).padStart(2, "0")).join("");
      };
      const img = new Image(); img.src = URL.createObjectURL(blob); await img.decode();
      return JSON.stringify({
        "bitmap default": await hash(await createImageBitmap(blob)),
        "bitmap no colour management": await hash(await createImageBitmap(blob, { colorSpaceConversion: "none", premultiplyAlpha: "none" })),
        "img element": await hash(img),
      });
    })()""" % json.dumps(file_url(icon)))
    out = run_js("""(async () => { %s
      const blob = await (await fetch(%s)).blob();
      await importFile(new File([blob], %s));
      document.querySelector("#projectPick .btn.primary").click();
      await until(() => state.project && document.getElementById("btnGenerate").offsetParent);
      return JSON.stringify({imported: document.getElementById("importResult").innerText});
    })()""" % (HELPERS, json.dumps(file_url(firmware)), json.dumps(Path(firmware).name)))
    if not check("import the official firmware", out["imported"].startswith("✓"), out["imported"]):
        return

    # The fitting component, as the page installs it once the user agrees.
    out = run_js("""(async () => {
      let st = await (await fetch("/api/fitting-setup")).json();
      if (st.state === "missing") st = await (await fetch("/api/fitting-setup", { method: "POST" })).json();
      return JSON.stringify(st);
    })()""", timeout=600)
    report["fitting"] = out
    if not check("install the fitting component", out.get("state") not in ("missing", "failed", "unsupported"),
                 "%s %s" % (out.get("state"), out.get("note", ""))):
        return

    r = RECIPE
    out = run_js("""(async () => { %(helpers)s
      const errors = [];
      const lang = document.getElementById("menuLangSelect");
      lang.value = %(lang)s; lang.dispatchEvent(new Event("change", { bubbles: true }));
      if (!await until(() => state.project.menu_language === %(lang)s)) errors.push("menu language not set");
      for (const lut of %(luts)s) {   // 导入 .cube on each LUT preset, then a name of our own
        await pickSlot(lut.slot);
        const before = (state.changes[lut.slot] || {}).film;
        await choose("filmLutInput", lut.url, lut.file, "text/plain");
        if (!await until(() => { const f = (state.changes[lut.slot] || {}).film; return f && f !== before; })) errors.push("lut " + lut.look + " not applied: " + toasts());
        await rename(lut.name);
        if ((state.changes[lut.slot] || {}).name !== lut.name) errors.push("preset " + lut.slot + " not renamed");
      }
      await pickSlot(%(grade)d);
      setEdit(%(grade)d, "grade", %(values)s);
      await pickSlot(%(icon)d);
      await choose("imageInput", %(png)s, "picture.jpg", "image/jpeg");
      await until(() => document.getElementById("cropDialog").open);
      await wait(500);
      document.getElementById("cropApply").click();
      if (!await until(() => ((state.changes[%(icon)d] || {}).icon || {}).asset)) errors.push("icon not applied: " + toasts());
      await rename(%(name)s);
      document.getElementById("featShutdown").click(); await wait(500);
      await choose("sdInput", %(jpg)s, "shutdown.jpg", "image/jpeg");
      if (!await until(() => document.getElementById("sdDialog").open)) errors.push("shutdown dialog did not open: " + toasts());
      await wait(500);
      if (!await until(() => !document.getElementById("sdApply").disabled, 120000)) errors.push("shutdown not fitted: " + document.getElementById("sdStat").textContent);
      document.getElementById("sdApply").click();
      if (!await until(() => state.changes.shutdown && !document.getElementById("sdDialog").open)) errors.push("shutdown not applied: " + toasts());
      document.getElementById("featIC").click(); await wait(1500);
      return JSON.stringify({ errors, changes: state.changes, shutdown: state.shutdown || null });
    })()""" % dict(helpers=HELPERS, grade=r["grade_slot"], icon=r["icon_slot"], values=json.dumps(r["grade"]),
                   luts=json.dumps([dict(lut, url=file_url(cubes[lut["look"]]), file=cubes[lut["look"]].name) for lut in r["luts"]]),
                   png=json.dumps(file_url(icon)), jpg=json.dumps(file_url(shutdown)), name=json.dumps(r["name"]),
                   lang=json.dumps(r["menu_language"])), timeout=600)
    report["changes"] = out["changes"]
    if not check("set up the recipe (4 LUTs renamed, sliders, icon, name, shutdown picture)", not out["errors"], "; ".join(out["errors"])):
        return
    if settle:
        # What the server started on its own after the imports finishes before 生成 asks for fits.
        print("    waiting %d s before 生成" % settle, flush=True)
        time.sleep(settle)
    report["settle_seconds"] = settle

    run_js("""(async () => {
      document.getElementById("btnGenerate").click();
      await new Promise((r) => setTimeout(r, 1500));
      const d = document.getElementById("preflightDialog");
      if (d.open) [...d.querySelectorAll("button")].find((b) => /继续|繼續|Continue/i.test(b.innerText)).click();
      return "started";
    })()""")
    # Fitting four LUTs takes minutes, many on a slow machine: ask once a minute, print what
    # the page says, give up after an hour.
    started, last = time.monotonic(), ""
    while True:
        out = run_js("""(() => {
          const open = [...document.querySelectorAll("dialog[open]")];
          if (open.length) return JSON.stringify({done: true, ok: false, title: "dialog", text: open.map((x) => x.id + ": " + x.innerText.slice(0, 300)).join(" | ")});
          // showPending hides 查看明细 and showResult shows it, whether it worked or not.
          const ok = !document.getElementById("doneDownloads").hidden, ended = !document.getElementById("doneMore").hidden;
          return JSON.stringify({done: ok || ended, ok, title: document.getElementById("doneTitle").textContent,
            text: document.getElementById("doneText").textContent + " " + (ended && !ok ? document.getElementById("doneChecks").innerText.slice(0, 1500) : ""),
            href: document.getElementById("dlFirmware").getAttribute("href")});
        })()""")
        if out["done"] or time.monotonic() - started > 3600:
            break
        if out["text"] != last:
            print("    %3.0f min  %s %s" % ((time.monotonic() - started) / 60, out["title"], out["text"][:100]), flush=True)
            last = out["text"]
        time.sleep(15)
    report["generate_seconds"] = round(time.monotonic() - started)
    if not check("generate a firmware", out["ok"], out["title"] + " " + out["text"][:200]):
        report["generate_error"] = out
        return
    manifest = json.load(urllib.request.urlopen(origin + out["href"].replace("/firmware", "/manifest")))
    report["manifest"] = manifest
    report["firmware_sha256"] = manifest["output_sha256"]
    report["regions"] = regions(urllib.request.urlopen(origin + out["href"], timeout=120).read(), manifest)
    before = len(downloads())
    run_js('(() => { document.getElementById("dlFirmware").click(); return "clicked"; })()')
    row = wait_download(out["href"], before)
    ok = row and not row["Err"] and sha256(row["Path"]) == manifest["output_sha256"]
    detail = row and (row["Err"] or row["Path"])
    if not ok:
        link = run_js('(() => { const a = document.getElementById("dlFirmware"); return JSON.stringify({href: a.getAttribute("href"), download: a.download, shown: !!a.offsetParent}); })()')
        detail = "events %s; folder %s; link %s" % (json.dumps(downloads()[before:], ensure_ascii=False),
                                                    sorted(os.listdir(work / "downloads")), link)
    check("download the firmware through 下载固件", ok, detail)


def regions(firmware, manifest):
    """SHA-256 of the bytes the generator wrote, by what they are (film, icon, names, the
    shutdown picture...), read back with the project's own fwread: where two platforms'
    firmware differ, this says which part does."""
    sys.path.insert(0, str(REPO))
    from darkslide import fwread
    payload = fwread.unpack(firmware)
    groups = {"payload (must match payload_sha256_after)": hashlib.sha256(payload)}
    for change in sorted(manifest["payload_changes"], key=lambda c: c["offset"]):
        h = groups.setdefault(change.get("what") or "film: " + str(change.get("kind")), hashlib.sha256())
        h.update(payload[change["offset"]:change["offset"] + change["length"]])
    return {what: h.hexdigest() for what, h in groups.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], fromfile_prefix_chars="@")
    ap.add_argument("app", help="the app's executable (Darkslide GR.exe, or Contents/MacOS/Darkslide GR)")
    ap.add_argument("--firmware", help="an official fwdc239b.bin for the generate-and-download checks")
    ap.add_argument("--report", help="write what was made, as JSON, to compare platforms")
    ap.add_argument("--lut", action="append", metavar="PATH=NAME",
                    help="a .cube for the next LUT preset and its new name; four of them replace the made-up looks")
    ap.add_argument("--settle", type=int, default=0, metavar="SECONDS",
                    help="wait this long between setting up the recipe and 生成")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="darkslide-selftest-"))
    data, dl_dir = work / "data", work / "downloads"
    data.mkdir()
    dl_dir.mkdir()
    env = dict(os.environ, DARKSLIDE_DATA=str(data), DARKSLIDE_SELFTEST_PORT=str(CONTROL_PORT),
               DARKSLIDE_SELFTEST_DOWNLOADS=str(dl_dir))
    report = {"platform": sys.platform, "machine": platform.machine(), "app": args.app}
    started = time.monotonic()
    proc = subprocess.Popen([args.app], env=env)
    try:
        origin = wait_page(proc)
        if not check("the page loads in the window", origin, "%.1fs" % (time.monotonic() - started)):
            return 1
        print("page at " + origin)

        # A file reaches the server through the page's own import code (what a drop or
        # the file picker hands it). Not a firmware: the server must say so.
        fake = work / "not-firmware.bin"
        fake.write_bytes(b"darkslide selftest " * 64)
        out = run_js("""(async () => {
          const blob = await (await fetch(%s)).blob();
          await importFile(new File([blob], "not-firmware.bin"));
          return document.getElementById("importResult").innerText;
        })()""" % json.dumps(file_url(fake)))
        check("a file goes through the page's import to the server", out.startswith("✕"), out)

        # A server file on a link with download, as 下载固件 is.
        films = json.load(urllib.request.urlopen(origin + "/api/film-catalog"))["films"]
        path = "/api/film-preview/%s.cube" % urllib.parse.quote(films[0]["id"])
        expected = hashlib.sha256(urllib.request.urlopen(origin + path).read()).hexdigest()
        before = len(downloads())
        run_js('(() => { const a = document.createElement("a"); a.href = %s; a.download = "selftest-film.cube"; document.body.append(a); a.click(); a.remove(); return "clicked"; })()' % json.dumps(path))
        row = wait_download(path, before)
        check("download a server file from a link", row and not row["Err"] and sha256(row["Path"]) == expected,
              row and (row["Err"] or row["Path"]))

        # A blob with a Chinese name, as 导出 .cube and the recipe card make (revoked after 1 s, as
        # they do); then the same with an ASCII name and without revoking, to tell causes apart.
        text = "TITLE \"自测\"\nLUT_3D_SIZE 2\n"
        for label, name, revoke in (("a Chinese name, revoked after 1 s like the page", "自测 测试.cube", True),
                                    ("an ASCII name, revoked after 1 s", "selftest-blob.cube", True),
                                    ("an ASCII name, never revoked", "selftest-kept.cube", False)):
            fresh_page(proc)
            before = len(downloads())
            run_js("""(() => { const a = document.createElement("a");
              a.href = URL.createObjectURL(new Blob([%s], {type: "text/plain"})); a.download = %s;
              a.click(); if (%s) setTimeout(() => URL.revokeObjectURL(a.href), 1000); return "clicked"; })()"""
                   % (json.dumps(text), json.dumps(name), "true" if revoke else "false"))
            row = wait_download("blob:", before, timeout=30)
            ok = row and not row["Err"] and Path(row["Path"]).name == name and Path(row["Path"]).read_text(encoding="utf-8") == text
            detail = row and (row["Err"] or row["Path"])
            if not ok:
                detail = "events %s; folder %s" % (json.dumps(downloads()[before:], ensure_ascii=False), sorted(os.listdir(dl_dir)))
            check("download a blob with " + label, ok, detail)

        out = run_js('(() => { localStorage.setItem("darkslide.selftest", "記得"); return localStorage.getItem("darkslide.selftest"); })()')
        check("localStorage works", out == "記得", out)

        # A link to another site is kept out of the window (selftest builds don't open the browser).
        run_js('(() => { const a = document.createElement("a"); a.href = "https://example.com/"; document.body.append(a); a.click(); a.remove(); return "clicked"; })()')
        time.sleep(1.5)
        out = run_js("location.href")
        check("external links leave the window alone", out.rstrip("/") == origin, out)

        if args.firmware:
            luts = [lut.rsplit("=", 1) for lut in args.lut or []]
            if luts and len(luts) != len(RECIPE["luts"]):
                sys.exit("give %d --lut, one per LUT preset" % len(RECIPE["luts"]))
            fresh_page(proc)  # 下载固件 is then this page's first download
            generate_and_download(origin, args.firmware, work, report, luts, args.settle)

        # The page's 退出 ends the server, and with it the app.
        server = urllib.parse.urlparse(origin).port
        run_js('(() => { document.getElementById("btnQuit").click(); return "clicked"; })()')
        try:
            code = proc.wait(timeout=20)
            check("退出 quits the app", True, "exit code %d" % code)
        except subprocess.TimeoutExpired:
            check("退出 quits the app", False, "still running after 20s")
        time.sleep(1)
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/" % server, timeout=2)
            check("the server stopped with the app", False, "still answering")
        except OSError:
            check("the server stopped with the app", True)
    finally:
        if proc.poll() is None:
            proc.kill()
        report["results"] = results
        if args.report:
            Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    failed = [name for name, ok in results if not ok]
    print("\n%d passed, %d failed" % (len(results) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
