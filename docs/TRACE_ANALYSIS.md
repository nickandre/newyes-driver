# USB Trace Analysis + WiFi Investigation

## USB Trace — Successful Print

### Capture Details
- **File**: `usb-printer.pcapng` (713KB)
- **Source**: Windows PC with sPrinter V1.12, USB connection
- **Device**: USB address 4, VID 0x2E88

### Complete Sequence (20 packets)

```
Frame 43  OUT  ~GDS                           → Get serial (handshake)
Frame 46  IN   ~GDS "16 2535080602004176"     ← Response

Frame 47  OUT  ~SDC 1 0 0 1                   → Set config (YMC, left, normal, normal)
Frame 50  IN   ~SDC ""                        ← ACK

Frame 51  OUT  !DPC 0                         → Paper feed in
Frame 54  IN   !DPC ""                        ← ACK

Frames 55-118: 17 × !SPD packets              → Print data passes
  Each: !SPD 714 <Y> 1073 14 [45067 bytes]    ← ACK after each

Frame 119 OUT  !DPC 1                         → Paper feed out
```

### SPD Parameters
```
!SPD 714 <Y_offset> 1073 14
     │      │        │    └─ ByteGroup: 14
     │      │        └───── Width: 1073 columns
     │      └───────────── Y position (stride 98 between passes)
     └──────────────────── Height: 714

Y positions: 1102 1200 1298 1396 1494 1592 1690 1788 1886 1984 2082 2180 2278 2376 2474 2572 (17 passes)
```

### Key Finding: USB ACKs Everything
Every command (GDS, SDC, DPC, SPD) gets an ACK response over USB.
The response is a protocol packet with empty payload.

## WiFi TCP — Failed Attempts

### What Works
- TCP connection to `192.168.4.1:9100` — reliable
- `~GDS` (get serial) — **gets response**
- All other query commands (GPS, GFV, GDE, GPD) — **get responses**

### What Fails
- `~SDC` (set config) — **no ACK** over WiFi
- `!DPC` (paper control) — **no ACK** over WiFi
- `!SPD` (print data) — **no ACK** over WiFi
- Connection dies after ~270KB of data (~6 SPD packets)
- Printer never prints anything

### Connection Death Pattern
Consistent across all approaches:
- Packet 1-6 send OK (packets 1-3 are small commands, 4-6 are SPDs)
- Packet 7 (or 8) times out — `sendall()` blocks, `send()` blocks, `select()` writable check fails
- After timeout, connection is dead (heartbeat GPS fails)
- Total data sent before death: ~270KB

### Approaches Tried
1. **Single connection, sequential sends** — dies at packet 7
2. **Single connection, chunked sends (512B/20ms)** — dies at packet 7
3. **Single connection, SO_SNDBUF=2048** — blocks on first SPD
4. **Fresh connection per SPD** — all sends succeed but nothing prints
5. **All data concatenated, stream send** — stalls at ~100KB
6. **Single connection with heartbeat between SPDs** — dies at packet 7
7. **Single connection with response draining** — dies at packet 7

## Root Cause Analysis

### Theory: WiFi Firmware is Different from USB Firmware

Evidence:
1. **No ACKs over WiFi**: USB firmware ACKs every packet. WiFi firmware ACKs nothing except query commands.
2. **Connection limit**: WiFi connection dies after ~270KB regardless of pacing.
3. **No physical action**: Motor/paper/cartridge commands are accepted silently but produce no movement.

### Theory: WiFi May Use Different Data Format

From the SDK disassembly:
- `Protocol_SendPrintData` (normal) uses header `\x1b\x30\x31`
- `Protocol_SendPrintData_Blu` (Bluetooth) uses header `\x1b\x30\x30`
- WiFi might use yet another variant, or the Bluetooth variant

The `commType` enum: TCP=0, USB=1, Serial=2, Bluetooth=3.
The SDC we captured uses `\x1b\x30\x31` (the `31` = '1' might mean USB mode).
WiFi/TCP might need `\x1b\x30\x30` (the `30` = '0' for TCP mode).

### Theory: Missing WiFi-Specific Init

The Android app has:
- `_preConnectCheck` — verifies WiFi SSID matches printer
- `_onConnect` → `_connectWithRetry` — connects with retry logic
- `_startHeartbeat` — periodic GPS queries
- `_getInfoCommand` / `_processQueryInfo` — queries device info
- `_setupPrintParameters` — configures SDK based on device info

We're skipping all of this. The printer may require a specific handshake over WiFi.

### Theory: SDK Waits for ACKs

The `DM_printImage` function calls `sendAndReceive()` for every packet and checks `isCommRes()` after each. Over USB, ACKs come back. Over WiFi, they don't. The SDK might detect WiFi mode and skip the ACK check, or the app might handle the ACK differently.

## Critical Next Step: Capture Android WiFi Traffic

The **only way to resolve this** is to capture what the actual Android app sends over WiFi TCP. The USB trace tells us the data format, but the WiFi transport behavior may be fundamentally different.

Options:
1. **PCAPdroid** on Android phone (no root) — capture WiFi traffic to printer
2. **TCP proxy** on Mac — relay and log all traffic between phone and printer
3. **Android emulator + proxy** — more complex but no phone needed

## What the USB Trace Taught Us

Even though WiFi printing doesn't work yet, the USB trace gave us:
1. **Exact command sequence**: GDS → SDC → DPC → SPD×17 → DPC
2. **Correct SDC params**: `1 0 0 1` (cartridgeType=1/YMC, direction=0, paper=0, density=1)
3. **SPD format**: `!SPD <height> <Y> <width> <ByteGroup>` + nozzle data
4. **ByteGroup = 14** (confirmed)
5. **Pass stride = 98**, **17 passes** for the captured image
6. **Data is nozzle-mapped** — 14 rows × 1073 columns × 3 bytes per value
7. **The printer ACKs over USB** — WiFi's lack of ACKs is anomalous
