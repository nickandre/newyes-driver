# newyes-driver

Reverse-engineered driver for the Newyes LD0806 portable inkjet printer.

## Project Overview

This is a hardware reverse-engineering project. The printer has no public documentation — everything here was figured out from USB captures, APK disassembly, and physical print tests.

**Current state**: Nozzle encoding fully solved. USB printing works via Windows. **WiFi printing works** (solved 2026-03-20). Key discovery: the printer uses **two TCP ports** — 9100 for queries, 9200 for writes. The protocol bytes are identical to USB on both ports.

## Architecture

- **No dependencies** beyond Python stdlib. No pip packages needed (Pillow optional for image printing).
- **WiFi printing** — `tools/printer.py` is the unified interface. Printer WiFi AP at `192.168.4.1`, port 9100 (queries) and 9200 (writes).
- **USB printing (Windows-only)** — the printer's USB Printer Class is non-compliant, only works with Windows `CreateFileW`. Scripts sent to Windows must be self-contained.
- **Remote execution model** — development happens on macOS, Windows scripts are copied to `nandre-x1c` (10.0.0.96) via `sshpass`/`scp`. See `docs/WINDOWS_EXECUTION.md`.

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
- `printer.py` — **Unified printer interface** (WiFi TCP). Status, sleep config, clean, paper feed, print. Transport-agnostic design.
- `encoder.py` — Image-to-nozzle encoder with row permutation. This is the core encoding implementation.
- `print_image.py` — Full color image printing (RGB→CMY, dither, multi-pass). Generates packet binary for Windows sender.
- `print_test.py` — Hardware verification test script (Hello World + colored diagonals).
- `windows_usb_demo.py` — Windows USB printer driver (status, paper feed, motor control, print replay). Self-contained.
- `wifi_query.py` — Legacy WiFi query tool (superseded by printer.py)
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
- `ios_wifi_print.pcapng` — iOS app WiFi print capture (complete print job with ACKs)
- `ios_wifi_commands.pcapng` — iOS app WiFi commands capture (sleep config change, cartridge clean)
- `*.pcapng` — USB/WiFi packet captures (1dot, 2dots, hello-world at various scales, pdf-printout)

## Printing

### WiFi (macOS — recommended)
```bash
# Connect to printer WiFi network first, then:
python tools/printer.py status                # Query printer status
python tools/printer.py print image.jpg       # Print an image
python tools/printer.py sleep 30              # Set sleep timeout
python tools/printer.py clean                 # Clean print head
python tools/printer.py paper                 # Paper feed in/out cycle
```

### USB (Windows)
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
python tools/wifi_test.py                    # WiFi print test (Hello World)
python tests/test_sdk_encoding.py            # Verify permutation tables against captures
python tests/decode_nozzle.py                # Decode 1dot.pcapng with known tables
python tests/decode_nozzle.py --all          # Try all 6 table permutations
```
