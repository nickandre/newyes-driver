#!/usr/bin/env python3
"""
Print a color image on the Newyes LD0806.

Processes image on macOS (requires Pillow), writes packet data to a file,
then a minimal sender script is generated for Windows.

Usage:
    source .venv/bin/activate
    python tools/print_image.py parrots-large.jpg
"""

import sys
import os
import struct

BYTEGROUP = 14
BITS_PER_BYTE = 8
NOZZLE_HEIGHT = BYTEGROUP * BITS_PER_BYTE  # 112
PASS_STRIDE = 98
CH1_OFFSET = 12
CH2_OFFSET = -13
PAPER_WIDTH = 2146  # pixels (~4" at 600 DPI; 1073 printed at half paper width)

ROW_PERM_M = [12, 3, 8, 13, 4, 9, 14, 5, 10, 1, 6, 11, 2, 7]
ROW_PERM_C = [9, 14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4]
ROW_PERM_Y = [14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4, 9]

HEADER = b'\x1b\x30\x31'


def build_cmd(mode, cmd, params=""):
    pkt = HEADER + mode.encode() + cmd.encode() + b'\x20'
    if params:
        pkt += params.encode() + b'\x20'
    pkt += b'\x00\x00\x0d'
    return pkt


def encode_cmy_pass(c_pixels, m_pixels, y_pixels, width, y_pos, y_offset=0):
    """Encode one SPD pass from separate C, M, Y 1-bit bitmaps."""
    img_h = len(m_pixels)
    nozzle = bytearray(1 + width * BYTEGROUP * 3)
    nozzle[0] = 0x00

    for local_y in range(NOZZLE_HEIGHT):
        img_y = y_offset + local_y
        if img_y < 0 or img_y >= img_h:
            continue

        r = local_y % BYTEGROUP
        b = local_y // BYTEGROUP
        mask = 0x80 >> b
        nr_m = ROW_PERM_M[r] - 1
        nr_c = ROW_PERM_C[r] - 1
        nr_y = ROW_PERM_Y[r] - 1

        for x in range(width):
            # Magenta channel at column x
            if x < len(m_pixels[img_y]) and m_pixels[img_y][x]:
                idx = 1 + (x * BYTEGROUP + nr_m) * 3 + 0
                nozzle[idx] |= mask

            # Cyan channel at column x + CH1_OFFSET
            x1 = x + CH1_OFFSET
            if 0 <= x1 < width and x < len(c_pixels[img_y]) and c_pixels[img_y][x]:
                idx = 1 + (x1 * BYTEGROUP + nr_c) * 3 + 1
                nozzle[idx] |= mask

            # Yellow channel at column x + CH2_OFFSET
            x2 = x + CH2_OFFSET
            if 0 <= x2 < width and x < len(y_pixels[img_y]) and y_pixels[img_y][x]:
                idx = 1 + (x2 * BYTEGROUP + nr_y) * 3 + 2
                nozzle[idx] |= mask

    params = f"163 {y_pos} {width} {BYTEGROUP}"
    pkt = HEADER + b'!SPD ' + params.encode() + b' '
    pkt += bytes(nozzle) + b'\x00\x00\x0d'
    return pkt


def floyd_steinberg_dither(channel_data, width, height):
    """Floyd-Steinberg dithering on a single channel (0-255) -> 1-bit."""
    # Work with floats
    buf = [[float(channel_data[y][x]) for x in range(width)] for y in range(height)]
    out = [[0] * width for _ in range(height)]

    for y in range(height):
        for x in range(width):
            old = buf[y][x]
            new = 255.0 if old > 127 else 0.0
            out[y][x] = 1 if new == 255.0 else 0  # CMY: 255 = full ink, fire nozzle
            err = old - new
            if x + 1 < width:
                buf[y][x + 1] += err * 7 / 16
            if y + 1 < height:
                if x - 1 >= 0:
                    buf[y + 1][x - 1] += err * 3 / 16
                buf[y + 1][x] += err * 5 / 16
                if x + 1 < width:
                    buf[y + 1][x + 1] += err * 1 / 16

    return out


def process_image(image_path):
    """Load image, scale to paper width, convert RGB->CMY, dither."""
    from PIL import Image

    img = Image.open(image_path).convert('RGB')
    orig_w, orig_h = img.size

    # Scale to paper width
    scale = PAPER_WIDTH / orig_w
    new_h = int(orig_h * scale)
    img = img.resize((PAPER_WIDTH, new_h), Image.LANCZOS)
    print(f"Scaled: {orig_w}x{orig_h} -> {PAPER_WIDTH}x{new_h}")

    # Extract RGB channels and convert to CMY
    pixels = img.load()
    c_raw = [[255 - pixels[x, y][0] for x in range(PAPER_WIDTH)] for y in range(new_h)]
    m_raw = [[255 - pixels[x, y][1] for x in range(PAPER_WIDTH)] for y in range(new_h)]
    y_raw = [[255 - pixels[x, y][2] for x in range(PAPER_WIDTH)] for y in range(new_h)]

    print("Dithering C...", end=" ", flush=True)
    c_dith = floyd_steinberg_dither(c_raw, PAPER_WIDTH, new_h)
    print("M...", end=" ", flush=True)
    m_dith = floyd_steinberg_dither(m_raw, PAPER_WIDTH, new_h)
    print("Y...", end=" ", flush=True)
    y_dith = floyd_steinberg_dither(y_raw, PAPER_WIDTH, new_h)
    print("done.")

    return c_dith, m_dith, y_dith, PAPER_WIDTH, new_h


def generate_packets(c_pix, m_pix, y_pix, width, height):
    """Generate all SPD packets for the image."""
    pad_left = max(0, -CH2_OFFSET)  # 13
    pad_right = max(0, CH1_OFFSET)  # 12
    total_w = width + pad_left + pad_right

    # Pad all channels
    c_pad = [[0] * pad_left + row + [0] * pad_right for row in c_pix]
    m_pad = [[0] * pad_left + row + [0] * pad_right for row in m_pix]
    y_pad = [[0] * pad_left + row + [0] * pad_right for row in y_pix]

    packets = []
    packets.append(build_cmd('~', 'GDS'))
    packets.append(build_cmd('~', 'SDC', '1 0 0 1'))
    packets.append(build_cmd('!', 'DPC', '0'))

    y_offset = 0
    page_y = 158
    pass_num = 0
    while y_offset < height:
        pkt = encode_cmy_pass(c_pad, m_pad, y_pad, total_w,
                              page_y + y_offset, y_offset)
        packets.append(pkt)
        pass_num += 1
        print(f"  Pass {pass_num}: Y={page_y + y_offset}, "
              f"rows {y_offset}-{min(y_offset + NOZZLE_HEIGHT, height) - 1}, "
              f"{len(pkt)} bytes")
        y_offset += PASS_STRIDE

    packets.append(build_cmd('!', 'DPC', '1'))
    return packets


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <image_path>")
        sys.exit(1)

    image_path = sys.argv[1]
    print(f"Processing: {image_path}")

    c_pix, m_pix, y_pix, width, height = process_image(image_path)
    print(f"\nEncoding {width}x{height} image...")
    packets = generate_packets(c_pix, m_pix, y_pix, width, height)
    print(f"\nTotal: {len(packets)} packets, "
          f"{sum(len(p) for p in packets)} bytes")

    # Write packets to binary file
    out_path = os.path.splitext(image_path)[0] + '_print.bin'
    with open(out_path, 'wb') as f:
        # Write packet count, then length-prefixed packets
        f.write(struct.pack('<I', len(packets)))
        for pkt in packets:
            f.write(struct.pack('<I', len(pkt)))
            f.write(pkt)
    print(f"Wrote: {out_path}")

    # Generate Windows sender script
    sender_path = os.path.splitext(image_path)[0] + '_send.py'
    with open(sender_path, 'w') as f:
        f.write('''#!/usr/bin/env python3
"""Send pre-encoded print data to printer."""
import ctypes, ctypes.wintypes as wt, time, sys, struct

DEVICE_PATH = r'\\\\?\\USB#VID_2E88&PID_6001#Printer_123456#{28d78fad-5a12-11d1-ae5b-0000f803a8c2}'

bin_path = sys.argv[1] if len(sys.argv) > 1 else r'parrots-large_print.bin'
data = open(bin_path, 'rb').read()
off = 0
n_pkts = struct.unpack_from('<I', data, off)[0]; off += 4
packets = []
for _ in range(n_pkts):
    plen = struct.unpack_from('<I', data, off)[0]; off += 4
    packets.append(data[off:off+plen]); off += plen
print(f"Loaded {n_pkts} packets from {bin_path}")

k32 = ctypes.windll.kernel32
handle = k32.CreateFileW(DEVICE_PATH, 0xC0000000, 3, None, 3, 0, None)
if handle == -1:
    print(f"Failed: error {k32.GetLastError()}"); sys.exit(1)
print("Printer connected.")
written = wt.DWORD(0)
buf = ctypes.create_string_buffer(512)
read_n = wt.DWORD(0)
for i, pkt in enumerate(packets):
    k32.WriteFile(handle, pkt, len(pkt), ctypes.byref(written), None)
    wait = 2.0 if len(pkt) > 10000 else 0.5
    time.sleep(wait)
    k32.ReadFile(handle, buf, 512, ctypes.byref(read_n), None)
    ack = "ack" if read_n.value > 0 else "no ack"
    print(f"  [{i+1}/{n_pkts}] {len(pkt):>6d} bytes  {ack}")
    k32.SetLastError(0)
k32.CloseHandle(handle)
print("Done!")
''')
    print(f"Wrote: {sender_path}")
    print(f"\nTo print:")
    print(f"  sshpass -p 'test1234' scp -o StrictHostKeyChecking=no "
          f"{out_path} {sender_path} Admin@10.0.0.96:")
    print(f"  sshpass -p 'test1234' ssh -o StrictHostKeyChecking=no Admin@10.0.0.96 "
          f"'\"C:\\Program Files\\Python312\\python.exe\" "
          f"C:\\Users\\Admin\\{os.path.basename(sender_path)} "
          f"C:\\Users\\Admin\\{os.path.basename(out_path)} 2>&1'")


if __name__ == '__main__':
    main()
