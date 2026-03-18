# Windows Execution Guide

How to run Python scripts on the Windows machine (nandre-x1c) from macOS via SSH, and print to the Newyes LD0806 over USB.

## Windows Machine

| Field | Value |
|-------|-------|
| Hostname | `nandre-x1c` |
| IP | `10.0.0.96` |
| Username | `Admin` |
| Password | `test1234` |
| Python | `C:\Program Files\Python312\python.exe` |
| Printer | "Smart Printer" (SoftwareDevice class, VID `2E88` PID `6001`) |

## SSH Access

Uses `sshpass` (installed via Homebrew on macOS) for non-interactive password auth:

```bash
# One-off command
sshpass -p 'test1234' ssh -o StrictHostKeyChecking=no Admin@10.0.0.96 "command here"

# Interactive session
sshpass -p 'test1234' ssh -o StrictHostKeyChecking=no Admin@10.0.0.96
```

## Transferring Scripts

Use `scp` to copy files, then run via SSH:

```bash
# Copy a script to Windows
sshpass -p 'test1234' scp -o StrictHostKeyChecking=no /path/to/script.py Admin@10.0.0.96:script.py

# Run it
sshpass -p 'test1234' ssh -o StrictHostKeyChecking=no Admin@10.0.0.96 \
    "\"C:\Program Files\Python312\python.exe\" C:\Users\Admin\script.py 2>&1"
```

**Important**: Piping to `python -c -` does NOT work over SSH to Windows. Always write to a file first, then run.

### One-liner pattern (write + copy + run)

```bash
# 1. Write script locally
cat << 'PYEOF' > /tmp/my_script.py
print("hello from windows")
PYEOF

# 2. Copy and run
sshpass -p 'test1234' scp -o StrictHostKeyChecking=no /tmp/my_script.py Admin@10.0.0.96:my_script.py && \
sshpass -p 'test1234' ssh -o StrictHostKeyChecking=no Admin@10.0.0.96 \
    "\"C:\Program Files\Python312\python.exe\" C:\Users\Admin\my_script.py 2>&1"
```

## USB Printer Access

The printer appears as a USB Printer Class device. No pip packages are needed — we use `ctypes` + `CreateFileW` directly.

### Device Path

```
\\?\USB#VID_2E88&PID_6001#Printer_123456#{28d78fad-5a12-11d1-ae5b-0000f803a8c2}
```

The GUID `{28d78fad-5a12-11d1-ae5b-0000f803a8c2}` is the standard USB Printer Class interface GUID. The serial `Printer_123456` may vary — if it doesn't open, enumerate with PowerShell:

```powershell
Get-PnpDevice | Where-Object { $_.FriendlyName -like '*Smart*' -or $_.FriendlyName -like '*Printer*' }
```

### Opening the Device

```python
import ctypes
import ctypes.wintypes as wt
import time

DEVICE_PATH = r'\\?\USB#VID_2E88&PID_6001#Printer_123456#{28d78fad-5a12-11d1-ae5b-0000f803a8c2}'
HEADER = b'\x1b\x30\x31'

k32 = ctypes.windll.kernel32
handle = k32.CreateFileW(DEVICE_PATH, 0xC0000000, 3, None, 3, 0, None)
if handle == -1:
    raise RuntimeError(f"Failed to open printer: error {k32.GetLastError()}")
```

### Sending Commands & Reading Responses

```python
def send_recv(handle, data, wait=0.5):
    k32 = ctypes.windll.kernel32
    written = wt.DWORD(0)
    k32.WriteFile(handle, data, len(data), ctypes.byref(written), None)
    time.sleep(wait)
    buf = ctypes.create_string_buffer(512)
    read_n = wt.DWORD(0)
    k32.ReadFile(handle, buf, 512, ctypes.byref(read_n), None)
    resp = buf.raw[:read_n.value] if read_n.value > 0 else b""
    k32.SetLastError(0)  # IMPORTANT: clear stale errors
    return resp

def build(mode, cmd, params=""):
    pkt = HEADER + mode.encode() + cmd.encode() + b'\x20'
    if params:
        pkt += params.encode() + b'\x20'
    pkt += b'\x00\x00\x0d'
    return pkt
```

## Protocol Reference

### Packet Format

```
Request:  \x1b\x30\x31 <mode> <CMD> \x20 [params \x20] \x00\x00 \x0d
Response: \x1b\x30 <mode> <CMD> \x00\x00\x00\x00 \x20 <payload> \x20 \x00 \x0d
```

- **mode**: `~` (0x7E) = query/set, `!` (0x21) = action
- Response header is `\x1b\x30` (2 bytes, drops the `\x31`)

### Query Commands

```python
send_recv(handle, build('~', 'GPS'))       # State: "0"=busy, "1"=idle
send_recv(handle, build('~', 'GFV'))       # Firmware: "16 VER-1B20250918H1"
send_recv(handle, build('~', 'GDS'))       # Serial: "16 2535080602004176"
send_recv(handle, build('~', 'GDE'))       # Battery: "3 099" (type + percent)
send_recv(handle, build('~', 'GPD'))       # Idle: "1"=idle, "0"=busy
send_recv(handle, build('~', 'GFC', '0'))  # Factory config
```

### Action Commands

```python
send_recv(handle, build('!', 'DPC', '0'), wait=2)  # Feed paper IN
send_recv(handle, build('!', 'DPC', '1'), wait=2)  # Feed paper OUT
# Motor: motorType(0=feeder,1=carriage,2=print) direction stepCount runType
send_recv(handle, build('~', 'DMC', '1 0 50 0'), wait=2)  # Carriage fwd 50
```

## Print Job Sequence

```python
H = b'\x1b\x30\x31'

# 1. Wake up / get serial
send_recv(handle, H + b'~GDS \x00\x00\x0d')

# 2. Set print config (cartridge=color, dir=left, paper=normal, density=normal)
send_recv(handle, H + b'~SDC 1 0 0 1 \x00\x00\x0d')

# 3. Feed paper in
send_recv(handle, H + b'!DPC 0 \x00\x00\x0d', wait=2)

# 4. Send SPD passes (see Nozzle Data Format below)
for pkt in spd_packets:
    written = wt.DWORD(0)
    k32.WriteFile(handle, pkt, len(pkt), ctypes.byref(written), None)
    time.sleep(1.0)  # 1s for large SPD packets
    buf = ctypes.create_string_buffer(512)
    read_n = wt.DWORD(0)
    k32.ReadFile(handle, buf, 512, ctypes.byref(read_n), None)
    k32.SetLastError(0)

# 5. Feed paper out
send_recv(handle, H + b'!DPC 1 \x00\x00\x0d', wait=2)

# 6. Close
k32.CloseHandle(handle)
```

## Nozzle Data Format (SPD)

### Packet Structure

```
\x1b\x30\x31 !SPD <height> <Y> <width> <bytegroup> <header_byte> <nozzle_data> \x00\x00 \x0d
```

| Param | Meaning | Typical Value |
|-------|---------|---------------|
| height | Pass height parameter | 163 |
| Y | Vertical position on page | starts ~158, increments by stride |
| width | Number of horizontal pixel columns | image width + padding |
| bytegroup | Always 14 | 14 |
| header_byte | First byte of nozzle data | 0x00 |

### Data Layout — Column-Major

```
For each column (0 to width-1):
    For each of 14 nozzle rows (0 to 13):
        3 bytes: [ch0/Magenta] [ch1/Cyan] [ch2/Yellow]
```

**Total nozzle data** = 1 header byte + width × 14 × 3 bytes

### Encoding Formula

```
pixel(x, y) → column=x, row=y%14, bit=y//14, bitmask=0x80>>(y//14)
```

- 14 rows × 8 bits = **112 vertical pixel lines per pass**
- Bit 7 (0x80) = top of row's band, bit 0 (0x01) = bottom
- Multi-pass stride: **98 pixels** vertically

### Channel Offsets

For black/grayscale, fire all 3 channels with horizontal offsets:
- **ch0 (Magenta)**: source pixel at column `x`
- **ch1 (Cyan)**: source pixel at column `x - 12` (data placed at column `x + 12`)
- **ch2 (Yellow)**: source pixel at column `x + 13` (data placed at column `x - 13`)

Pad image with 13 columns left + 12 columns right to accommodate offsets.

## Complete Encoder

```python
BYTEGROUP = 14
BITS_PER_BYTE = 8
NOZZLE_HEIGHT = BYTEGROUP * BITS_PER_BYTE  # 112
PASS_STRIDE = 98
CH1_OFFSET = 12
CH2_OFFSET = -13


def encode_pass(pixels, width, pass_y_global, image_y_offset=0):
    """Encode one SPD pass from a pixel bitmap.

    pixels: list of rows, each row is list of 0/1 values
    width: number of horizontal pixel columns
    pass_y_global: Y position on page for this pass
    image_y_offset: which image row corresponds to nozzle position 0

    Returns: SPD packet bytes
    """
    img_h = len(pixels)
    nozzle = bytearray(1 + width * BYTEGROUP * 3)
    nozzle[0] = 0x00  # header byte

    for local_y in range(NOZZLE_HEIGHT):
        img_y = image_y_offset + local_y
        if img_y < 0 or img_y >= img_h:
            continue
        r = local_y % BYTEGROUP
        b = local_y // BYTEGROUP
        mask = 0x80 >> b

        for x in range(width):
            if x >= len(pixels[img_y]) or not pixels[img_y][x]:
                continue
            # ch0 (Magenta) at column x
            idx0 = 1 + (x * BYTEGROUP + r) * 3 + 0
            nozzle[idx0] |= mask
            # ch1 (Cyan) at column x + 12
            x1 = x + CH1_OFFSET
            if 0 <= x1 < width:
                idx1 = 1 + (x1 * BYTEGROUP + r) * 3 + 1
                nozzle[idx1] |= mask
            # ch2 (Yellow) at column x - 13
            x2 = x + CH2_OFFSET
            if 0 <= x2 < width:
                idx2 = 1 + (x2 * BYTEGROUP + r) * 3 + 2
                nozzle[idx2] |= mask

    height_param = 163
    params = f"{height_param} {pass_y_global} {width} {BYTEGROUP}"
    pkt = b'\x1b\x30\x31!SPD ' + params.encode() + b' '
    pkt += bytes(nozzle) + b'\x00\x00\x0d'
    return pkt


def encode_image(pixels, page_y=158):
    """Encode a full image into multiple SPD passes.

    pixels: list of rows, each row is list of 0/1 values
    page_y: starting Y position on page

    Returns: list of SPD packet bytes
    """
    img_h = len(pixels)
    img_w = max(len(row) for row in pixels) if pixels else 0
    pad_left = max(0, -CH2_OFFSET)   # 13
    pad_right = max(0, CH1_OFFSET)   # 12
    total_w = img_w + pad_left + pad_right
    shifted = []
    for row in pixels:
        new_row = [0] * pad_left + list(row) + [0] * pad_right
        shifted.append(new_row[:total_w])
    packets = []
    y_offset = 0
    while y_offset < img_h:
        pass_y = page_y + y_offset
        pkt = encode_pass(shifted, total_w, pass_y, y_offset)
        packets.append(pkt)
        y_offset += PASS_STRIDE
    return packets


def wrap_print_job(spd_packets):
    """Wrap SPD packets with init/cleanup commands."""
    H = b'\x1b\x30\x31'
    commands = [
        H + b'~GDS \x00\x00\x0d',
        H + b'~SDC 1 0 0 1 \x00\x00\x0d',
        H + b'!DPC 0 \x00\x00\x0d',
    ]
    commands.extend(spd_packets)
    commands.append(H + b'!DPC 1 \x00\x00\x0d')
    return commands
```

## Row Permutation — SOLVED

The SDK permutes the 14 nozzle rows per channel before sending. Tables extracted from `libprintsdk.so`:

| Channel | Byte | Table (1-based) |
|---------|------|-----------------|
| Magenta | 0 | `[12, 3, 8, 13, 4, 9, 14, 5, 10, 1, 6, 11, 2, 7]` |
| Cyan | 1 | `[9, 14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4]` |
| Yellow | 2 | `[14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4, 9]` |

Applied as: `nozzle_row = table[source_row % 14] - 1`

See [NOZZLE_PATTERN.md](NOZZLE_PATTERN.md) for full details and verification.

## Key Gotchas

1. **Wait between sends** — 1.0s for large SPD packets, 0.3s for small commands. Too fast = data loss.
2. **Call `SetLastError(0)`** after each ReadFile — Windows carries stale error codes.
3. **Paper must be loaded** — `!DPC 0` feeds paper in; print won't work without it.
4. **Width padding** — add 13 columns left + 12 columns right for channel offsets.
5. **No WiFi writes** — WiFi queries work but write commands silently fail. USB is the only working transport.
6. **Python path** — must use full path `"C:\Program Files\Python312\python.exe"` (the `python` alias in PATH points to the Microsoft Store stub).
7. **SSH quoting** — double-quote the full python path, use `2>&1` to capture stderr.
