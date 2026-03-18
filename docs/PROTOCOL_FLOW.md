# Newyes LD0806 Protocol Flow

## Transport

- WiFi AP mode: printer creates network `LD0806-XXXX`
- IP: `192.168.4.1`, Port: `9100`
- Plain TCP socket, no TLS
- Printer is a raw data sink — no handshake on connect

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

### Queries that return data:
```
~GPS  → "0" or "1"                    (0=busy, 1=idle)
~GFV  → "16 VER-1B20250918H1"        (length + firmware string)
~GDS  → "16 2535080602004176"         (length + serial number)
~GDE  → "3 099"                       (type + battery percent)
~GPD  → "1"                           (1=idle, 0=busy)
~GFC 0 → "1 4"                       (factory config: value type)
```

### Commands accepted (no response = normal for writes):
```
~SDC 0 0 0 1     (SetPrintConfig: cartridge=K, dir=left, paper=normal, density=normal)
!DPC 0            (PaperControl: in)
!DPC 1            (PaperControl: out)
!SPD 0 N W X Y   (SendPrintData: dir=0, dataSize=N, sendWidth=W, x=X, y=Y + raw data)
!PBD              (PrintBufferData: followed by raw data)
!DSP 0 1 1        (CartridgeClean: type=0, ignite=1, squeegee=1)
!CSC              (CloseSocketConnect)
```

### Commands with no response (possibly unsupported by LD0806):
```
~GCT              (ConnectTest)
~GSS              (GetSensorStatus)
~GMI 0/1          (GetMotorInfo)
~GPN 0/1          (GetPrintNumber)
```

## Normal Print Flow (from decompiled app)

### Step 1: Connect
```python
sock = socket.connect("192.168.4.1", 9100)
```
No handshake. Just open TCP.

### Step 2: Heartbeat (background)
The app sends `~GPS` periodically to keep the connection alive and monitor state.

### Step 3: Set Print Config
```
App → SDK: SetCommand(type=0, {cartridgeType=0, direction=0, paperType=0, density=1})
SDK → App: byte[] containing ~SDC packet
App → Printer: send byte[] over TCP
```
Wire: `\x1b\x30\x31~SDC 0 0 0 1 \x00\x00\x0d`

### Step 4: Process Image & Send Data
```
App → SDK: SDKParam_Set({cartridgeType=0, imageColor=1, passType=0, direction=0, coordType=0})
App → SDK: CreatePrintDataGenerator(imagePath)
LOOP:
  App → SDK: GetPrintData()
  SDK → App: byte[] containing !SPD packet with nozzle-mapped data
  App → Printer: send byte[] over TCP
  App → SDK: GetReturnCode()
  if returnCode == 176: done
```

Each `GetPrintData()` call returns one "pass" of print data — a horizontal strip
processed through the nozzle map. The byte array is a complete `!SPD` packet.

### Step 5: Close
```
App → Printer: !CSC
sock.close()
```

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

1. **WiFi printing** — USB works, WiFi write commands are silently ignored (see TRACE_ANALYSIS.md)
2. **End-to-end validation** — print encoded text on hardware to confirm visual correctness
