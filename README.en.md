<p align="center"><img src="docs/images/logo.png" width="128" alt="DARKSLIDE GR"></p>

# DARKSLIDE GR

[简体中文](README.md) | English

Give your RICOH GR III / GR IIIx film colours, your own LUTs, your own preset icons and names, a reordered menu and soft glow. It all happens on your own computer and produces one firmware file you can flash to the camera.

![DARKSLIDE GR editor](docs/images/editor.png)

> [!WARNING]
> Flashing modified firmware is risky: your camera may fail to boot or be permanently bricked, and you may lose your warranty. This project is not affiliated with RICOH. Read "Risks" below before you decide to flash.

## Download and install

### Desktop app (recommended)

Download the installer for your computer from [Releases](https://github.com/LiorMoment/darkslide-gr/releases/latest) and double-click to install. No Python, no terminal.

| Your computer | Download |
|---|---|
| Mac with Apple chip (M1, M2, M3, M4…) | `mac-apple-silicon.dmg` |
| Mac with Intel chip | `mac-intel.dmg` |
| Windows 10 / 11 (64-bit) | `windows-setup.exe` |

Not sure which Mac you have? Apple menu → About This Mac: "Chip Apple M…" means Apple chip, "Processor Intel…" means Intel.

**The system stops it the first time**, because the installers are not signed by Apple or Microsoft:

- **Mac**: close the warning, open System Settings › Privacy & Security and click "Open Anyway" near the bottom.
- **Windows**: when "Windows protected your PC" appears, click "More info › Run anyway". The Windows app has only been tested automatically on GitHub's machines; Windows 10 needs WebView2 (Windows 11 has it).

The desktop app is made by [@Ady0402](https://github.com/Ady0402).

### Script version

If you already have Python 3.9 or newer, you can run the source code directly: download it, then run `sh start.sh` on a Mac or double-click `start.cmd` on Windows. Your browser opens the page.

Both versions do the same and share one data folder, so you can use both. Everything runs on your computer; nothing is uploaded.

## What it does

- **Film colours**: put one roll of film on each of four presets (Bleach Bypass, Retro, HDR Tone, Cross Processing). 16 built-in films, fine-tuned with sliders.
- **Your own LUT**: put a .cube you use in Lightroom, DaVinci Resolve or a phone app (or a Lightroom .xmp) into the camera, so the shutter gives you that colour. What the camera stores is an approximation, and the page shows how close it is; the first time needs a component of about 12 MB.
- **Icons and preset names**: give 12 presets your own icon (upload a picture or type a few letters) and name, shown in the camera menu.
- **Shutdown screen**: use your own picture.
- **Negative Film grain**: turn on the grain option of Negative Film; set its level in the camera.
- **Menu order**: drag to set the order of the Image Control menu; the movie and RAW development lists follow.
- **Soft glow**: the camera gets a "Soft glow" setting (off, weak, medium, strong) that makes bright areas bloom as if through a diffusion filter, shared by all presets.
- **Dark and light**: switch between match system, dark and light at the top right (from 1.6).
- **5 interface languages**: 简体中文, 繁體中文, English, 日本語, 한국어.

You can change all of these together and build once. Everything else stays factory; the firmware version stays the same and the firmware does not grow.

How each feature works and what to watch out for: [user guide](docs/user-guide/).

## Supported cameras and testing

| Camera | Official firmware needed | Tested |
|---|---|---|
| GR III | 2.10 | ✅ Tested by the author on one camera: icons, preset names, shutdown screen, Negative Film grain, flashing back to official firmware; four films, menu order and soft glow were also flashed together, item-by-item record pending |
| GR III HDF | 2.10 | ✅ Films, preset names and Traditional Chinese menus tested by a user; menu order and soft glow not tested |
| GR III Street, Diary | 2.10 | ⚠️ Adapted, not camera-tested |
| GR IIIx | 1.60 | ✅ Flashed and used by a user; menu order and soft glow not tested |
| GR IIIx Urban, HDF | 1.60 | ⚠️ Adapted, not camera-tested |
| GR IV | — | 🚧 Being adapted, cannot build yet |

- "Tested" means one camera; the samples are few, and your icons, names and film choices may not have been tried.
- Whenever you use something not camera-tested, you confirm the risk before every build.
- Item-by-item testing notes: [user guide](docs/user-guide/).

## Risks

- After flashing, your camera may fail to boot or work properly, or be **permanently bricked**; you may also lose warranty and official support. The author accepts no liability for any loss.
- The checks before building run offline on your computer and cannot replace testing on a real camera.
- DARKSLIDE GR only produces a firmware file: it **does not talk to your camera or touch your memory card**. Whether and how to flash is entirely up to you.
- Before flashing, prepare a rescue card that holds only the official firmware (the app has a rescue guide).
- This project is not affiliated with or endorsed by RICOH.

If it breaks, you get to keep both pieces. If you cannot accept your camera becoming a very elegant brick, do not flash it.

## Official firmware you need

This repository contains **no** RICOH firmware and offers none for download. Download the official firmware from RICOH's website; it is checked when you import it.

| Camera | File name | Size | SHA-256 |
|---|---|---|---|
| GR III series · 2.10 | `fwdc239b.bin` | 30,391,780 bytes | `d6a3c9c288d784080e7b283222ecdd8752adfeed6faa36dab44f73115354bfd4` |
| GR IIIx series · 1.60 | `fwdc243b.bin` | 30,391,788 bytes | `708e79f3b7b96a7e1581b4acf4c412233050a1832718ef7f06c8397db1b431c7` |

Check it yourself: `shasum -a 256 fwdc239b.bin` (Windows: `certutil -hashfile fwdc239b.bin SHA256`). The same file is used by regular and HDF cameras, so pick the right model when you create a project.

## How it keeps you safe

- Only the official files in the table above are accepted; anything else is refused.
- Every build starts again from the official file, then a separate, independent piece of code unpacks and checks it. **Only if every check passes** is it offered for download, with a manifest of what changed.
- The version number, the boot records and the firmware-update area are not changed by a single byte, so you can still flash the official file back afterwards. Flashing back was tested on one GR III, but this is not a guarantee: a camera that cannot boot at all is not covered.

## Screens

| Choose camera, import firmware | Build and verify |
|---|---|
| ![Choose camera](docs/images/start.png) | ![Build result](docs/images/done.png) |

Dark theme:

![Dark theme](docs/images/editor-dark.png)

## Reporting issues

Use the "Bug report" form under [Issues](../../issues). Include camera model, firmware version, file SHA-256 and steps to reproduce. **Please never upload firmware files.** The SHA-256 is enough.

## License

[GPL-2.0-only](LICENSE). The author asks that it be used only for personal learning, sharing and research, and not for commercial purposes such as selling it or charging for flashing (this request is not an additional license condition).

More: [author statement](AUTHOR_STATEMENT.md) · [third-party notices](THIRD_PARTY_NOTICES.md) (in Chinese) · [changelog](CHANGELOG.md) · [developer notes](docs/developer-notes/)
