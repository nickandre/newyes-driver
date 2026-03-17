# newyes-driver

Reverse-engineered driver and protocol documentation for the **Newyes LD0806** portable inkjet printer.

## Hardware

| Property | Value |
|----------|-------|
| Model | Newyes LD0806 |
| USB VID:PID | `0x2E88:0x6001` |
| Firmware | `VER-1B20250918H1` |
| WiFi SSID | `LD0806-XXXX` (last 4 digits of serial) |
| WiFi Password | `12345678` |
| WiFi IP | `192.168.4.1` |
| WiFi Port | `9100` (TCP) |
| Print Resolution | 300 DPI (600 DPI mode in SDK) |
| Cartridge | Black (K) or Color (YMC) |

## Status

### Working (Windows USB)
- All query commands (state, firmware, serial, battery, sensor, factory config)
- Paper feed (in/out)
- Motor control (feeder, carriage, print motor)
- Full print job replay from captured packets

### Partially Working (WiFi TCP)
- Query commands only (GPS, GFV, GDS, GDE, GPD, GFC)
- Write/action commands silently ignored (no ACK, no physical action)
- TCP connection stalls after ~180KB of print data
- **Printing does not work over WiFi** — see [docs/TRACE_ANALYSIS.md](docs/TRACE_ANALYSIS.md)

### Not Working (macOS USB)
- Bulk transfers fail with ERRNO 5 (I/O Error) due to firmware non-compliance
- See [docs/USB_BUG_REPORT.md](docs/USB_BUG_REPORT.md)

## Protocol

All transports (USB, WiFi TCP, Bluetooth) use the same packet format:

```
Request:  \x1b\x30\x31 [mode] [CMD] \x20 [params] \x20 \x00\x00 \x0d
Response: \x1b\x30      [mode] [CMD] \x00\x00\x00\x00 \x20 [payload] \x20 \x00\x00 \x0d
```

- Mode `~` = query/set, `!` = action
- CMD = 3 ASCII chars (e.g. `GPS`, `SDC`, `SPD`)
- Params = space-separated ASCII decimal integers

### Commands

| CMD | Mode | Function | Params |
|-----|------|----------|--------|
| `GPS` | `~` | Get printer state | — |
| `GFV` | `~` | Get firmware version | — |
| `GDS` | `~` | Get serial number | — |
| `GDE` | `~` | Get battery level | — |
| `GPD` | `~` | Get print idle status | — |
| `GSS` | `~` | Get sensor reading | — |
| `GFC` | `~` | Get factory config | `<index>` |
| `SDC` | `~` | Set print config | `<cartridgeType> <direction> <paperType> <density>` |
| `DPC` | `!` | Paper feed | `0`=in, `1`=out |
| `DMC` | `~` | Motor control | `<motorType> <direction> <steps> <runType>` |
| `SPD` | `!` | Send print data | `<height> <y> <width> <byteGroup>` + nozzle data |
| `DSP` | `!` | Cartridge clean | `<type> <ignite> <squeegee>` |
| `CSC` | `!` | Close connection | — |

### Print Sequence

From USB trace of successful print:
```
~GDS                          → handshake (get serial)
~SDC 1 0 0 1                  → set config (YMC, left, normal, normal)
!DPC 0                        → paper feed in
!SPD 714 <Y> 1073 14 [data]   → print pass (×17, Y stride 98)
!DPC 1                        → paper feed out
```

See [docs/PROTOCOL_FLOW.md](docs/PROTOCOL_FLOW.md) for full protocol documentation.

## Setup (Windows USB)

```powershell
# 1. Install Python
Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile python-installer.exe
.\python-installer.exe /quiet InstallAllUsers=1 PrependPath=1

# 2. Clone this repo
git clone https://github.com/nickandre/newyes-driver.git
cd newyes-driver

# 3. Connect printer via USB, power on

# 4. Run
python tools\windows_usb_demo.py status
```

No additional drivers or libraries needed — uses Windows `CreateFileW` on the USB Printer Class device interface directly.

**Device path**: The script expects `\\?\USB#VID_2E88&PID_6001#Printer_123456#{28d78fad-5a12-11d1-ae5b-0000f803a8c2}`. If your printer has a different serial number, update `DEVICE_PATH` in the script.

## Setup (WiFi)

```bash
# 1. Connect to printer WiFi: LD0806-XXXX, password: 12345678
# 2. pip install pillow (optional, for image prep)
python tools/wifi_query.py status
```

## Tools

### `tools/windows_usb_demo.py`
Windows-only. Communicates with the printer over USB via `CreateFileW`. Supports status queries, paper feed, motor control, and print replay.

```
python windows_usb_demo.py status    # Query all status info
python windows_usb_demo.py paper     # Feed paper in/out
python windows_usb_demo.py motor     # Test motor control
python windows_usb_demo.py print     # Replay captured print job
```

Requires `captured_packets.py` in the same directory for the `print` command.

### `tools/wifi_query.py`
Cross-platform. Queries printer status over WiFi TCP. Connect to the printer's WiFi AP first (`LD0806-XXXX`, password `12345678`).

```
python wifi_query.py status      # Query status
python wifi_query.py discover    # Scan for printer
```

**Note**: Only query commands work over WiFi. Printing over WiFi is not yet functional.

### `tools/captured_packets.py`
The 20 USB packets from a successful Windows print job, extracted from the Wireshark capture. Used by the other tools for print replay.

## Docs

- [docs/PROTOCOL_FLOW.md](docs/PROTOCOL_FLOW.md) — Wire protocol specification
- [docs/TRACE_ANALYSIS.md](docs/TRACE_ANALYSIS.md) — USB capture analysis and WiFi investigation
- [docs/USB_BUG_REPORT.md](docs/USB_BUG_REPORT.md) — Firmware USB Printer Class non-compliance

## Reverse Engineering Sources

- **Windows driver**: `sPrinter V1.12` (NSIS installer containing `PrinterTool.exe` with embedded `PrintSDKDll.dll`)
- **Android app**: `iSmart Printer` (Flutter APK with native `libprintsdk.so`)
- **Decompiled Java**: `com.example.printsdk` and `com.example.ytprint_plugin` via jadx
- **Dart AOT**: Decompiled via [blutter](https://github.com/worawit/blutter) — revealed `SocketManager` WiFi flow
- **USB capture**: Wireshark + USBPcap on Windows

## Known Firmware Bugs

See [docs/USB_BUG_REPORT.md](docs/USB_BUG_REPORT.md):
1. **Malformed GET_DEVICE_ID**: Reports 307-byte length, sends 51 bytes
2. **GET_PORT_STATUS stub**: Always returns `0x00` regardless of state
3. **macOS USB broken**: Bulk transfers fail due to above issues
