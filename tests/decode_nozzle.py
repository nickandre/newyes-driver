#!/usr/bin/env python3
"""
Decode nozzle data from pcapng captures back to a bitmap.

Tries all permutations of the 3 YMC row-reorder tables to find
which assignment produces a recognizable image.

The nozzle format (confirmed):
  - Column-major: for each col, 14 rows × 3 bytes [M, C, Y]
  - Each byte has 8 bits → 14 rows × 8 bits = 112 vertical pixels per pass
  - Pixel (x, y) encoded as: col=x, row=y%14, bit=y//14, mask=0x80>>(y//14)
  - The SDK permutes rows per channel before sending

Tables from libprintsdk.so:
  A = [9,14,5,10,1,6,11,2,7,12,3,8,13,4]
  B = [12,3,8,13,4,9,14,5,10,1,6,11,2,7]
  C = [1,6,11,2,7,12,3,8,13,4,9,14,5,10]

SDK encoding: output[table[i]-1] = input[i]
  → nozzle_row = table[source_row] - 1
Decoding:     source_row = inv_table[nozzle_row]
"""

import os
import sys
from itertools import permutations

A = [9, 14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4]
B = [12, 3, 8, 13, 4, 9, 14, 5, 10, 1, 6, 11, 2, 7]
C = [1, 6, 11, 2, 7, 12, 3, 8, 13, 4, 9, 14, 5, 10]
A_L1 = [14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4, 9]  # A rotated left by 1

TABLES = {'A': A, 'B': B, 'C': C, 'A_L1': A_L1}


def inv(tbl):
    r = [0] * 14
    for i in range(14):
        r[tbl[i] - 1] = i
    return r


def extract_spd(pcapng_path):
    """Extract all SPD packets from a pcapng file."""
    data = open(pcapng_path, 'rb').read()
    packets = []
    idx = 0
    while True:
        pos = data.find(b'\x1b\x30\x31!SPD ', idx)
        if pos == -1:
            break
        ps = pos + 8
        try:
            chunk = data[ps:ps + 60].decode('ascii', errors='replace')
            parts = chunk.split()
            height = int(parts[0])
            y_pos = int(parts[1])
            width = int(parts[2])
            bg = int(parts[3])
            ns = ps + len(f'{height} {y_pos} {width} {bg} ')
            body_size = 1 + width * bg * 3
            nozzle = data[ns:ns + body_size]
            packets.append({
                'height': height, 'y': y_pos, 'width': width,
                'bg': bg, 'header': nozzle[0], 'data': nozzle[1:]
            })
        except (ValueError, IndexError):
            pass
        idx = pos + 1
    return packets


def decode_pass(pkt, inv_tables):
    """Decode one SPD pass → dict of (source_y, source_x) per channel.

    inv_tables: [inv_M, inv_C, inv_Y] — inverse permutation for each channel.
    Returns: list of 3 sets of (y, x) tuples, one per channel.
    """
    nd = pkt['data']
    width = pkt['width']
    channels = [set(), set(), set()]

    for col in range(width):
        for nrow in range(14):
            for ch in range(3):
                offset = (col * 14 + nrow) * 3 + ch
                if offset >= len(nd):
                    continue
                byte_val = nd[offset]
                if not byte_val:
                    continue
                srow = inv_tables[ch][nrow]
                for bit in range(8):
                    if byte_val & (0x80 >> bit):
                        source_y = bit * 14 + srow
                        channels[ch].add((source_y, col))
    return channels


def render_bitmap(pixels, label=""):
    """Render a set of (y, x) pixels as ASCII art."""
    if not pixels:
        print(f"  {label}: (empty)")
        return
    min_y = min(y for y, x in pixels)
    max_y = max(y for y, x in pixels)
    min_x = min(x for y, x in pixels)
    max_x = max(x for y, x in pixels)
    print(f"  {label}: {max_x - min_x + 1}w × {max_y - min_y + 1}h, "
          f"x=[{min_x},{max_x}] y=[{min_y},{max_y}]")
    for y in range(min_y, max_y + 1):
        line = ""
        for x in range(min_x, max_x + 1):
            line += '#' if (y, x) in pixels else '.'
        # trim trailing dots for readability
        line = line.rstrip('.')
        if line:
            print(f"    {y:3d}| {line}")
        else:
            print(f"    {y:3d}|")


def try_all_assignments(pcap_path):
    """Try all 6 permutations of table→channel and render results."""
    pkts = extract_spd(pcap_path)
    print(f"File: {pcap_path}")
    print(f"SPD packets: {len(pkts)}")
    for i, p in enumerate(pkts):
        print(f"  #{i}: height={p['height']} Y={p['y']} width={p['width']} "
              f"header=0x{p['header']:02x}")
    print()

    table_names = ['A', 'B', 'C']
    for perm in permutations(table_names):
        m_tbl, c_tbl, y_tbl = perm
        inv_tables = [inv(TABLES[m_tbl]), inv(TABLES[c_tbl]), inv(TABLES[y_tbl])]
        label = f"M={m_tbl} C={c_tbl} Y={y_tbl}"

        # Decode all passes and merge
        all_ch = [set(), set(), set()]
        for pkt in pkts:
            channels = decode_pass(pkt, inv_tables)
            for ch in range(3):
                all_ch[ch].update(channels[ch])

        # Merge all channels for "black" view
        merged = all_ch[0] | all_ch[1] | all_ch[2]

        # Score: prefer contiguous Y ranges per channel
        scores = []
        for ch in range(3):
            if all_ch[ch]:
                ys = sorted(set(y for y, x in all_ch[ch]))
                spread = ys[-1] - ys[0] + 1
                density = len(ys) / spread if spread else 0
                scores.append(density)
            else:
                scores.append(0)
        avg_score = sum(scores) / len(scores)

        print(f"--- {label} (density={avg_score:.2f}) ---")
        render_bitmap(merged, "merged")
        print()


def decode_with_assignment(pcap_path, m='B', c='A', y='A_L1'):
    """Decode with a specific table assignment and show per-channel detail."""
    pkts = extract_spd(pcap_path)
    print(f"File: {pcap_path}")
    print(f"SPD packets: {len(pkts)}")
    print(f"Tables: M={m} C={c} Y={y}\n")

    inv_tables = [inv(TABLES[m]), inv(TABLES[c]), inv(TABLES[y])]
    ch_names = ['M(magenta)', 'C(cyan)', 'Y(yellow)']

    all_ch = [set(), set(), set()]
    for pkt in pkts:
        channels = decode_pass(pkt, inv_tables)
        for ch in range(3):
            all_ch[ch].update(channels[ch])

    for ch in range(3):
        render_bitmap(all_ch[ch], ch_names[ch])
    print()

    merged = all_ch[0] | all_ch[1] | all_ch[2]
    render_bitmap(merged, "ALL (black)")


if __name__ == '__main__':
    docs = os.path.join(os.path.dirname(__file__), '..', 'docs')

    if '--all' in sys.argv:
        # Try all 6 permutations
        for pcap in ['1dot.pcapng']:
            try_all_assignments(os.path.join(docs, pcap))
    else:
        # Use best known assignment
        for pcap in ['1dot.pcapng']:
            path = os.path.join(docs, pcap)
            if os.path.exists(path):
                decode_with_assignment(path)
                print()
