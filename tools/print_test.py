#!/usr/bin/env python3
"""
Yellow table fine-tuning — Table A is close but has 8 stray dots (1 per bit group).
Test A vs A-rotated-left-1 vs A-rotated-right-1.
"""

import ctypes
import ctypes.wintypes as wt
import time
import sys

DEVICE_PATH = r'\\?\USB#VID_2E88&PID_6001#Printer_123456#{28d78fad-5a12-11d1-ae5b-0000f803a8c2}'
HEADER = b'\x1b\x30\x31'

BYTEGROUP = 14
NOZZLE_HEIGHT = BYTEGROUP * 8

TABLE_A     = [9, 14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4]
TABLE_A_L1  = TABLE_A[1:] + TABLE_A[:1]   # [14,5,10,1,6,11,2,7,12,3,8,13,4,9]
TABLE_A_R1  = TABLE_A[-1:] + TABLE_A[:-1]  # [4,9,14,5,10,1,6,11,2,7,12,3,8,13]

FONT = {
    'A': [0x18,0x24,0x42,0x42,0x7E,0x42,0x42,0x00],
    'L': [0x40,0x40,0x40,0x40,0x40,0x42,0x7E,0x00],
    'R': [0x7C,0x42,0x42,0x7C,0x50,0x48,0x46,0x00],
    ' ': [0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00],
}


def build_cmd(mode, cmd, params=""):
    pkt = HEADER + mode.encode() + cmd.encode() + b'\x20'
    if params:
        pkt += params.encode() + b'\x20'
    pkt += b'\x00\x00\x0d'
    return pkt


def encode_yellow_pass(pixels, width, y_pos, y_table, y_offset=0):
    img_h = len(pixels)
    nozzle = bytearray(1 + width * BYTEGROUP * 3)
    nozzle[0] = 0x00
    for local_y in range(NOZZLE_HEIGHT):
        img_y = y_offset + local_y
        if img_y < 0 or img_y >= img_h:
            continue
        r = local_y % BYTEGROUP
        b = local_y // BYTEGROUP
        mask = 0x80 >> b
        nr = y_table[r] - 1
        for x in range(width):
            if x >= len(pixels[img_y]) or not pixels[img_y][x]:
                continue
            idx = 1 + (x * BYTEGROUP + nr) * 3 + 2
            nozzle[idx] |= mask
    params = f"163 {y_pos} {width} {BYTEGROUP}"
    pkt = HEADER + b'!SPD ' + params.encode() + b' '
    pkt += bytes(nozzle) + b'\x00\x00\x0d'
    return pkt


def encode_black_pass(pixels, width, y_pos, y_offset=0):
    """Black text using M=B, C=A tables."""
    ROW_PERM_M = [12, 3, 8, 13, 4, 9, 14, 5, 10, 1, 6, 11, 2, 7]
    ROW_PERM_C = [9, 14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4]
    ROW_PERM_Y = TABLE_A  # best known for Y
    img_h = len(pixels)
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
            if x >= len(pixels[img_y]) or not pixels[img_y][x]:
                continue
            idx0 = 1 + (x * BYTEGROUP + nr_m) * 3 + 0
            nozzle[idx0] |= mask
            x1 = x + 12
            if 0 <= x1 < width:
                idx1 = 1 + (x1 * BYTEGROUP + nr_c) * 3 + 1
                nozzle[idx1] |= mask
            x2 = x - 13
            if 0 <= x2 < width:
                idx2 = 1 + (x2 * BYTEGROUP + nr_y) * 3 + 2
                nozzle[idx2] |= mask
    params = f"163 {y_pos} {width} {BYTEGROUP}"
    pkt = HEADER + b'!SPD ' + params.encode() + b' '
    pkt += bytes(nozzle) + b'\x00\x00\x0d'
    return pkt


def make_text_pixels(text, scale):
    pixels = []
    for y in range(8 * scale):
        row = []
        for c in text:
            f = FONT.get(c, [0] * 8)
            fr = f[y // scale]
            for bit in range(8):
                px = 1 if fr & (0x80 >> bit) else 0
                for _ in range(scale):
                    row.append(px)
        pixels.append(row)
    return pixels


def make_diagonal(width, height, thickness=5):
    pixels = []
    for y in range(height):
        row = [0] * width
        cx = int(y * (width - 1) / max(height - 1, 1))
        for t in range(thickness):
            if 0 <= cx + t < width:
                row[cx + t] = 1
        pixels.append(row)
    return pixels


def generate_job():
    packets = []
    pad = 13

    packets.append(build_cmd('~', 'GDS'))
    packets.append(build_cmd('~', 'SDC', '1 0 0 1'))
    packets.append(build_cmd('!', 'DPC', '0'))

    diag_w = 300
    diag_h = NOZZLE_HEIGHT
    gap = 40
    tables = [
        ("A", TABLE_A),
        ("L", TABLE_A_L1),
        ("R", TABLE_A_R1),
    ]

    total_w = pad + (diag_w + gap) * 3 + pad
    y_pos = 163

    # Print labels in black first
    label_scale = 8
    labels = []
    for y in range(min(8 * label_scale, diag_h)):
        row = [0] * total_w
        for ti, (name, _) in enumerate(tables):
            label_px = make_text_pixels(name + " ", label_scale)
            offset_x = pad + ti * (diag_w + gap)
            if y < len(label_px):
                for x in range(min(len(label_px[y]), diag_w)):
                    if label_px[y][x]:
                        row[offset_x + x] = 1
        labels.append(row)
    # Pad to full height
    while len(labels) < diag_h:
        labels.append([0] * total_w)
    packets.append(encode_black_pass(labels, total_w, y_pos, 0))
    print(f"Pass 1: Labels A/L/R in black")

    # Print each yellow diagonal
    diag = make_diagonal(diag_w, diag_h, thickness=5)
    for ti, (name, tbl) in enumerate(tables):
        full_px = []
        offset_x = pad + ti * (diag_w + gap)
        for y in range(diag_h):
            row = [0] * total_w
            for x in range(diag_w):
                if diag[y][x]:
                    row[offset_x + x] = 1
            full_px.append(row)
        packets.append(encode_yellow_pass(full_px, total_w, y_pos, tbl))
        print(f"Pass {ti+2}: Yellow diagonal table {name}: {tbl[:3]}...{tbl[-3:]}")

    packets.append(build_cmd('!', 'DPC', '1'))
    return packets


def main():
    packets = generate_job()
    print(f"\nTotal: {len(packets)} packets")

    k32 = ctypes.windll.kernel32
    handle = k32.CreateFileW(DEVICE_PATH, 0xC0000000, 3, None, 3, 0, None)
    if handle == -1:
        print(f"Failed to open printer: error {k32.GetLastError()}")
        sys.exit(1)
    print("Printer connected.\n")

    written = wt.DWORD(0)
    buf = ctypes.create_string_buffer(512)
    read_n = wt.DWORD(0)

    for i, pkt in enumerate(packets):
        k32.WriteFile(handle, pkt, len(pkt), ctypes.byref(written), None)
        wait = 2.5 if len(pkt) > 10000 else 0.5
        time.sleep(wait)
        k32.ReadFile(handle, buf, 512, ctypes.byref(read_n), None)
        ack = "ack" if read_n.value > 0 else "no ack"
        print(f"  [{i+1}/{len(packets)}] {len(pkt):>6d} bytes  {ack}")
        k32.SetLastError(0)

    k32.CloseHandle(handle)
    print("\nDone!")
    print("A  = table A as-is:      ", TABLE_A)
    print("L  = table A rotated left:  ", TABLE_A_L1)
    print("R  = table A rotated right: ", TABLE_A_R1)


if __name__ == '__main__':
    main()
