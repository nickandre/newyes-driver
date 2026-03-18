"""
Newyes LD0806 — Pure Python Image-to-Nozzle Encoder

Nozzle data format:
  Column-major: for each horizontal pixel column, 14 nozzle rows × 3 bytes
  Pixel (x, y) maps to: column=x, row=y%14, bit=y//14, bitmask=0x80>>(y//14)
  Three bytes per (column, row): [ch0/M, ch1/C, ch2/Y]
  ch0 = source pixel at x
  ch1 = source pixel at x-12 (bank offset +12)
  ch2 = source pixel at x+13 (bank offset -13)

Row permutation (from libprintsdk.so):
  The SDK permutes the 14 nozzle rows per channel before sending.
  Encoding: nozzle_row = table[source_row] - 1
  Tables extracted from libprintsdk.so @ 0xa4ba4:
    ch0/M → YMC_B = [12,3,8,13,4,9,14,5,10,1,6,11,2,7]
    ch1/C → YMC_A = [9,14,5,10,1,6,11,2,7,12,3,8,13,4]
    ch2/Y → YMC_A rotated left 1 = [14,5,10,1,6,11,2,7,12,3,8,13,4,9]

Multi-pass: stride=98, 112 nozzle lines per pass (14 rows × 8 bits)
"""

BYTEGROUP = 14
BITS_PER_BYTE = 8
NOZZLE_HEIGHT = BYTEGROUP * BITS_PER_BYTE  # 112
PASS_STRIDE = 98
CH1_OFFSET = 12
CH2_OFFSET = -13

# Row permutation tables (1-based, from libprintsdk.so)
# Usage: nozzle_row = ROW_PERM_x[source_row % 14] - 1
ROW_PERM_M = [12, 3, 8, 13, 4, 9, 14, 5, 10, 1, 6, 11, 2, 7]  # YMC_B
ROW_PERM_C = [9, 14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4]  # YMC_A
ROW_PERM_Y = [14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4, 9]  # YMC_A rotated left by 1


def encode_pass(pixels, width, pass_y_global, image_y_offset=0):
    """Encode one SPD pass from a pixel bitmap.
    
    pixels: list of rows, each row is list of 0/1 values
    width: number of horizontal pixel columns
    pass_y_global: Y position on page for this pass
    image_y_offset: which image row corresponds to nozzle position 0
    
    Returns: SPD packet bytes
    """
    img_h = len(pixels)
    
    # Build nozzle data: header + width * 14 * 3 bytes
    nozzle = bytearray(1 + width * BYTEGROUP * 3)
    nozzle[0] = 0x00  # header byte
    
    for local_y in range(NOZZLE_HEIGHT):
        img_y = image_y_offset + local_y
        if img_y < 0 or img_y >= img_h:
            continue
        
        r = local_y % BYTEGROUP
        b = local_y // BYTEGROUP
        mask = 0x80 >> b

        # Apply per-channel row permutation
        nr_m = ROW_PERM_M[r] - 1
        nr_c = ROW_PERM_C[r] - 1
        nr_y = ROW_PERM_Y[r] - 1

        for x in range(width):
            if x >= len(pixels[img_y]) or not pixels[img_y][x]:
                continue

            # ch0/M: at column x
            idx0 = 1 + (x * BYTEGROUP + nr_m) * 3 + 0
            nozzle[idx0] |= mask

            # ch1/C: at column x + CH1_OFFSET
            x1 = x + CH1_OFFSET
            if 0 <= x1 < width:
                idx1 = 1 + (x1 * BYTEGROUP + nr_c) * 3 + 1
                nozzle[idx1] |= mask

            # ch2/Y: at column x + CH2_OFFSET
            x2 = x + CH2_OFFSET
            if 0 <= x2 < width:
                idx2 = 1 + (x2 * BYTEGROUP + nr_y) * 3 + 2
                nozzle[idx2] |= mask
    
    # Build SPD packet
    height_param = 163  # from captures
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
    
    # Pad width to account for channel offsets
    pad_left = max(0, -CH2_OFFSET)  # ch2 needs room on the left
    pad_right = max(0, CH1_OFFSET)  # ch1 needs room on the right
    total_w = img_w + pad_left + pad_right
    
    # Shift pixels right by pad_left
    shifted = []
    for row in pixels:
        new_row = [0] * pad_left + list(row) + [0] * pad_right
        shifted.append(new_row[:total_w])
    
    # Generate passes
    packets = []
    y_offset = 0
    while y_offset < img_h:
        pass_y = page_y + y_offset  # could use stride-based but keep simple
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


if __name__ == "__main__":
    # Test with simple text
    FONT = {
        'H': [0x42,0x42,0x42,0x7E,0x42,0x42,0x42,0x00],
        'e': [0x00,0x00,0x3C,0x42,0x7E,0x40,0x3C,0x00],
        'l': [0x30,0x10,0x10,0x10,0x10,0x10,0x38,0x00],
        'o': [0x00,0x00,0x3C,0x42,0x42,0x42,0x3C,0x00],
        ' ': [0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00],
        'W': [0x42,0x42,0x42,0x42,0x5A,0x66,0x42,0x00],
        'r': [0x00,0x00,0x5C,0x62,0x40,0x40,0x40,0x00],
        'd': [0x02,0x02,0x3E,0x42,0x42,0x42,0x3E,0x00],
        '!': [0x10,0x10,0x10,0x10,0x10,0x00,0x10,0x00],
    }
    
    text = "Hello World!"
    scale = 4
    pixels = []
    for y in range(8 * scale):
        row = []
        for c in text:
            f = FONT.get(c, [0]*8)
            fr = f[y // scale]
            for bit in range(8):
                px = 1 if fr & (0x80 >> bit) else 0
                for _ in range(scale): row.append(px)
        pixels.append(row)
    
    packets = encode_image(pixels)
    job = wrap_print_job(packets)
    
    print(f"Text: '{text}' ({len(pixels[0])}x{len(pixels)} px)")
    print(f"SPD passes: {len(packets)}")
    for i, p in enumerate(packets):
        print(f"  Pass {i}: {len(p)} bytes")
    print(f"Total commands: {len(job)}")
