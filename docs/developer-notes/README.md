# DARKSLIDE GR developer notes

[简体中文](../开发者说明/) | English

Back to the [README](../../README.en.md).

## Running the tests

Run the tests: `python3 -m unittest discover -s tests`
Some original-file tests use `DARKSLIDE_OFFICIAL_BIN`. Full discovery also requires `DARKSLIDE_READBACK_ORIGINAL` pointing to the official decoded GR III 2.10 payload; the independent readback suite fails explicitly if it is absent. For IIIx desktop checks, set `DARKSLIDE_DESKTOP_IIIX` to the official 1.60 input and run `python3 -m unittest discover -s tests -p test_iiix_desktop.py`. These suites do not produce complete modified firmware.

## Rebuilding the ARM fragments

Only developers rebuilding the bundled ARM fragments need LLVM clang with armv7a-none-eabi support. The fragments are compiled from the shipped licensed source, not extracted from official firmware.

## Address relocation for GR III 2.20 / GR IIIx 1.70

Feature code is written with 2.10 / 1.60 addresses; for 2.20 / 1.70 projects `darkslide/fwaddr.py` maps them to the new firmware through `darkslide/data/reloc_220.json` (anything outside the table is refused; 2.10 / 1.60 are not mapped). The related developer tools only read, and need your own official files:

- `tools/build_reloc_220.py`: builds the table (addresses and shifts only) from the address map and the four decoded originals.
- `tools/check_relocated_windows.py`: checks that the 2.20 / 1.70 film changes differ from 2.10 / 1.60 only in addresses.
- `tools/capture_v29_reference_220.py`: writes the 2.20 film readback reference after all preconditions pass.
- `tools/build_film_arm_bundle_220.py`: rebuilds the 2.20 / 1.70 ARM fragment bundle at the new addresses (the 2.10 / 1.60 bundle is left alone).

## For AI assistants

Full steps for setting up the 拟合 (fit) component or adding film icons for a user: [AGENTS.md](../../AGENTS.md).

## Desktop app shell

Source and packaging of the desktop app shell: [`desktop/`](../../desktop/README.md) (MIT licence).

### Fifth film position: Vivid

Film positions on GR III 2.10 / GR IIIx 1.60 are 2, 8, 9, 10 and 11. Position 2 is Vivid; Positive and Negative remain factory presets. Selecting Vivid uses the five-position layout. Without Vivid, the original four-position output remains byte-for-byte unchanged. Neither layout grows the decoded payload.

Keep `darkslide/data/film_arm_vivid5_v1.json`, `darkslide/film_precompiled_vivid5_pin.py` and `darkslide/film_vivid_fifth_reference.json` in the distribution, alongside the existing four-position resources. Developers can rebuild and check the new resource with `python3 tools/build_film_arm_bundle.py --vivid-five --check`; omit `--vivid-five` to check the frozen four-position fragments. Its source metadata remains historical, while every instruction fragment, address and combination is still compared. End users need no compiler.

The photo-recipe extension currently rejects Vivid; use generation without payload growth. Offline verification does not establish camera behavior. Camera testing should cover the new Vivid film, the original four film positions, unchanged Positive and Negative, preset switching, restart and in-camera RAW development.
