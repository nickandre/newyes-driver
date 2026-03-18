# newyes-driver

Reverse-engineered driver for the Newyes LD0806 portable inkjet printer.

## Project Overview

This is a hardware reverse-engineering project. The printer has no public documentation — everything here was figured out from USB captures, APK disassembly, and physical print tests.

**Current state**: The nozzle encoding is fully solved. The encoder can produce correct nozzle data from bitmap images. USB printing works via a Windows machine accessed over SSH. WiFi printing does not work (firmware silently ignores write commands over WiFi).

## Architecture

- **No dependencies** beyond Python stdlib. No pip packages needed.
- **Windows-only for printing** — the printer's USB Printer Class implementation is non-compliant, only works with Windows `CreateFileW`. macOS USB is broken.
- **Remote execution model** — development happens on macOS, scripts are copied to a Windows machine (`nandre-x1c`, 10.0.0.96) via `sshpass`/`scp` and run over SSH. See `docs/WINDOWS_EXECUTION.md`.

## Key Technical Details

### Nozzle Encoding (docs/NOZZLE_PATTERN.md)

The printer uses a CMY inkjet cartridge with 14 nozzle rows × 8 bits = 112 vertical pixels per pass. Data is column-major: for each column, 14 rows × 3 bytes (M, C, Y).

**Critical**: Each channel has a different row permutation table. Without the correct permutation, text prints garbled (solid shapes look fine because permutation doesn't affect uniform data). Tables were extracted from `libprintsdk.so` in the Android APK.

```
ch0/Magenta → [12, 3, 8, 13, 4, 9, 14, 5, 10, 1, 6, 11, 2, 7]
ch1/Cyan    → [9, 14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4]
ch2/Yellow  → [14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4, 9]  (YMC_A rotated left 1)
```

Channel horizontal offsets: C is +12 columns right, Y is -13 columns left (relative to M). Pad images with 13 cols left + 12 cols right.

Paper width: ~2146 pixels. Full color printing works (RGB→CMY + Floyd-Steinberg dithering).

### Protocol

All commands use: `\x1b\x30\x31 [mode] [CMD] [params] \x00\x00 \x0d`

Print sequence: `~GDS` → `~SDC 1 0 0 1` → `!DPC 0` → `!SPD ...` × N → `!DPC 1`

### Running on the Windows machine

```bash
# Copy and run a script
sshpass -p 'test1234' scp -o StrictHostKeyChecking=no <script.py> Admin@10.0.0.96:<script.py>
sshpass -p 'test1234' ssh -o StrictHostKeyChecking=no Admin@10.0.0.96 \
    "\"C:\Program Files\Python312\python.exe\" C:\Users\Admin\<script.py> 2>&1"
```

Scripts sent to Windows must be **self-contained** (no imports from other project files) since only the single file is copied.

## Files

### Tools (tools/)
- `encoder.py` — Image-to-nozzle encoder with row permutation. This is the core encoding implementation.
- `print_image.py` — Full color image printing (RGB→CMY, dither, multi-pass). Runs on macOS, generates packet binary for Windows sender.
- `print_test.py` — Hardware verification test script (Hello World + colored diagonals).
- `windows_usb_demo.py` — Windows USB printer driver (status, paper feed, motor control, print replay)
- `wifi_query.py` — WiFi TCP query tool (queries only, printing doesn't work over WiFi)
- `captured_packets.py` — Captured USB print job packets for replay

### Tests (tests/)
- `decode_nozzle.py` — Nozzle data decoder. Extracts SPD from pcapng files and renders as ASCII bitmap. Useful for verifying captures.
- `test_sdk_encoding.py` — Verification tests: decodes 1dot capture, round-trip encode/decode.
- `*.bin` — Extracted nozzle data from hello_world captures at various scales.

### Docs (docs/)
- `NOZZLE_PATTERN.md` — Complete nozzle encoding specification (the most important doc)
- `PROTOCOL_FLOW.md` — Wire protocol and command reference
- `WINDOWS_EXECUTION.md` — Remote Windows execution guide with protocol reference
- `TRACE_ANALYSIS.md` — USB capture analysis and WiFi failure investigation
- `USB_BUG_REPORT.md` — Firmware USB Printer Class compliance issues
- `FIRMWARE.md` — Firmware version and OTA update mechanism
- `*.pcapng` — USB/WiFi packet captures (1dot, 2dots, hello-world at various scales, pdf-printout)

## Printing an Image

```bash
# 1. Process image on macOS (generates .bin packet file + Windows sender)
source .venv/bin/activate
python tools/print_image.py image.jpg

# 2. Copy to Windows and print
sshpass -p 'test1234' scp -o StrictHostKeyChecking=no image_print.bin image_send.py Admin@10.0.0.96:
sshpass -p 'test1234' ssh -o StrictHostKeyChecking=no Admin@10.0.0.96 \
    "\"C:\Program Files\Python312\python.exe\" C:\Users\Admin\image_send.py C:\Users\Admin\image_print.bin 2>&1"
```

## Testing

```bash
python tests/test_sdk_encoding.py    # Verify permutation tables against captures
python tests/decode_nozzle.py        # Decode 1dot.pcapng with known tables
python tests/decode_nozzle.py --all  # Try all 6 table permutations
```
