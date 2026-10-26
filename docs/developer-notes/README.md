# DARKSLIDE GR developer notes

[简体中文](../开发者说明/) | English

Back to the [README](../../README.en.md).

## Running the tests

Run the tests: `python3 -m unittest discover -s tests`
Some original-file tests use `DARKSLIDE_OFFICIAL_BIN`. Full discovery also requires `DARKSLIDE_READBACK_ORIGINAL` pointing to the official decoded GR III 2.10 payload; the independent readback suite fails explicitly if it is absent. For IIIx desktop checks, set `DARKSLIDE_DESKTOP_IIIX` to the official 1.60 input and run `python3 -m unittest discover -s tests -p test_iiix_desktop.py`. These suites do not produce complete modified firmware.

## Rebuilding the ARM fragments

Only developers rebuilding the bundled ARM fragments need LLVM clang with armv7a-none-eabi support. The fragments are compiled from the shipped licensed source, not extracted from official firmware.

## For AI assistants

Full steps for setting up the 拟合 (fit) component or adding film icons for a user: [AGENTS.md](../../AGENTS.md).

## Desktop app shell

Source and packaging of the desktop app shell: [`desktop/`](../../desktop/README.md) (MIT licence).
