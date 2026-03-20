# Newyes LD0806 Protocol Flow

## Transport

- WiFi AP mode: printer creates network `LD0806-XXXX`
- IP: `192.168.4.1`
- **Two TCP ports:**
  - **Port 9100** — read-only queries (GPD, GDS, GFV, GDE, GPS, GFC, GSS)
  - **Port 9200** — write/action commands (SDC, SFC, DPC, SPD, DSP)
- Plain TCP, no TLS
- Write commands sent to port 9100 are silently ignored (no ACK, no error)
- Both ports use the same packet format

## Packet Format

### Request (Host → Printer)
```
┌──────┬──────┬──────┬──────┬──────────┬──────┬──────┬──────┬──────┐
│ 0x1B │ 0x30 │ 0x31 │ mode │ CMD(3ch) │ 0x20 │params│ 0x00 │ 0x0D │
│      │  '0' │  '1' │      │          │  ' ' │ ...  │ 0x00 │  CR  │
└──────┴──────┴──────┴──────┴──────────┴──────┴──────┴──────┴──────┘
  ESC    '0'    '1'   ~!+    3 ASCII    space  ASCII   chk    term
                             letters           ints
```

- **mode**: `~` (0x7E) query/set, `!` (0x21) action, `+` (0x2B) OTA
- **params**: Space-separated ASCII decimal integers, trailing space
- **checksum**: 2 bytes, currently always `\x00\x00`

### Response (Printer → Host)
```
┌──────┬──────┬──────┬──────────┬────────────┬──────┬─────────┬──────┬──────┬──────┐
│ 0x1B │ 0x30 │ mode │ CMD(3ch) │ 0x00000000 │ 0x20 │ payload │ 0x20 │ 0x00 │ 0x0D │
│      │  '0' │      │          │  4 zeros   │  ' ' │  ASCII  │  ' ' │ 0x00 │  CR  │
└──────┴──────┴──────┴──────────┴────────────┴──────┴─────────┴──────┴──────┴──────┘
  ESC    '0'   ~!+    3 ASCII    padding      space  values    space  chk    term
```

Note: Response header is `\x1B\x30` (2 bytes), NOT `\x1B\x30\x31` (drops the `\x31`).

## Confirmed Commands (verified on real hardware)

### Queries (return data over both USB and WiFi):
```
~GPS    → "0"                           (PrinterState: 0=idle, 1=printing)
~GFV    → "16 VER-1B20250918H1"        (Firmware: length + version string)
~GDS    → "16 2535080602004176"         (Serial: length + serial number)
~GDE    → "3 099"                       (DeviceElectric: model + battery%)
~GPD    → "1"                           (PrinterIdle: 1=connected/idle)
~GFC 4  → "1 5"                        (FactoryConfig: sleep time index)
~GSS    → sensor state                  (SensorStatus)
```

### Settings (query-mode ~ but write to device):
```
~SDC 1 0 0 1     (SetPrintConfig: cartridge=1/YMC, dir=0, paper=0, density=1)
~SFC 4 <val>     (SetFactoryConfig: sleep time, val=1..8, see table below)
```

### Actions:
```
!DPC 0            (PaperControl: feed in)
!DPC 1            (PaperControl: feed out)
!SPD H Y W 14     (SendPrintData: height, Y-pos, width, bytegroup=14 + nozzle data)
!DSP 0 2000 3     (CartridgeClean: mode=0, intensity=2000, channels=3/CMY)
!CSC              (CloseSocketConnect)
```

### Sleep Time Values (~GFC 4 / ~SFC 4):
```
Value   Minutes
  1       1
  2       2
  3       5
  4      10
  5      30
  6      60
  7     120
  8     240
```

### Not confirmed on LD0806:
```
~GCT              (ConnectTest)
~GMI 0/1          (GetMotorInfo)
~GPN 0/1          (GetPrintNumber)
```

## Print Flow (captured from iOS app over WiFi, 2026-03-20)

The protocol is identical over USB and WiFi TCP. The printer ACKs every command
on both transports.

### Sequence
```
~GPD              → "1"              (check printer present)
~GDS              → serial           (identify device)
~SDC 1 0 0 1      → ACK             (set config: YMC, forward, normal, density=1)
!DPC 0             → ACK             (paper feed in)
!SPD 337 492 297 14 [nozzle data] → ACK   (print pass 1)
!SPD 337 590 297 14 [nozzle data] → ACK   (print pass 2, Y += 98)
!DPC 1             → ACK             (paper feed out)
```

### Key observations
- App **waits for ACK** before sending next command
- Multiple TCP connections used: queries on one, print on another
- SPD Y positions increment by stride=98 between passes
- SPD params: `<height> <Y_position> <width> <bytegroup=14>`

## Nozzle Encoding — SOLVED

The printer expects nozzle-mapped data, not raw bitmaps. The encoding is fully reverse-engineered:

1. **Column-major layout**: for each column, 14 rows × 3 bytes (M, C, Y)
2. **Per-channel row permutation**: rows are reordered by lookup tables extracted from `libprintsdk.so`
3. **Per-channel column offsets**: C at +12, Y at -13 relative to M (compensating physical nozzle bank positions)

### SPD Packet Data Layout
```
!SPD <height> <Y> <width> 14 <header_byte> <nozzle_data> \x00\x00 \x0d
```

- `height`: pass height parameter (varies by print context)
- `Y`: vertical page position
- `width`: horizontal pixel columns
- `14`: always 14 (bytegroup)
- `nozzle_data`: 1 header byte + width × 14 × 3 bytes, column-major with row permutation applied

See [NOZZLE_PATTERN.md](NOZZLE_PATTERN.md) for the complete encoding specification and `tools/encoder.py` for the implementation.

## Remaining Work

1. **Full image printing over WiFi** — Hello World text verified, need to test color images
2. **Rendering fixes** — text gets clipped at top of pass; need to adjust page_y or add padding
