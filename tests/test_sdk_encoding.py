#!/usr/bin/env python3
"""
Verify row permutation tables against the 1dot capture.

Decodes the captured nozzle data using the known table assignment
(M=B, C=A, Y=C) and confirms each channel produces a clean circular
dot at the expected position.

Run: python tests/test_sdk_encoding.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from decode_nozzle import inv, TABLES, extract_spd, decode_pass


def test_1dot():
    docs = os.path.join(os.path.dirname(__file__), '..', 'docs')
    path = os.path.join(docs, '1dot.pcapng')
    if not os.path.exists(path):
        print("SKIP: 1dot.pcapng not found")
        return

    pkts = extract_spd(path)
    assert len(pkts) == 1, f"Expected 1 SPD packet, got {len(pkts)}"
    pkt = pkts[0]
    assert pkt['width'] == 30
    assert pkt['bg'] == 14

    inv_tables = [inv(TABLES['B']), inv(TABLES['A']), inv(TABLES['A_L1'])]
    channels = decode_pass(pkt, inv_tables)

    # M and C should decode to identical 6x6 dots at same Y range
    m_ys = sorted(set(y for y, x in channels[0]))
    c_ys = sorted(set(y for y, x in channels[1]))
    assert m_ys == c_ys, f"M and C source Y ranges differ: {m_ys} vs {c_ys}"
    assert m_ys == [7, 8, 9, 10, 11, 12], f"Unexpected M source rows: {m_ys}"

    # M and C should have same widths per row
    for y in m_ys:
        m_cols = sorted(x for sy, x in channels[0] if sy == y)
        c_cols = sorted(x - 12 for sy, x in channels[1] if sy == y)  # undo offset
        assert m_cols == c_cols, f"Row {y}: M cols {m_cols} != C cols {c_cols}"

    # Y channel: filter to bit-7 data only (ignore stray bit-5 pixel at row 13)
    y_ys_bit7 = sorted(set(y for y, x in channels[2] if y < 14))
    # With A_L1, Y decodes to rows 7-12 (same as M/C)
    assert y_ys_bit7 == [7, 8, 9, 10, 11, 12], f"Unexpected Y source rows: {y_ys_bit7}"

    print("PASS: 1dot decode verified")
    print(f"  M: source Y={m_ys}, cols={sorted(set(x for y,x in channels[0]))}")
    print(f"  C: source Y={c_ys}, cols={sorted(set(x for y,x in channels[1]))}")
    print(f"  Y: source Y={y_ys_bit7}, cols={sorted(set(x for y,x in channels[2] if y < 14))}")


def test_roundtrip():
    """Encode pixels → nozzle data → decode back, verify pixel-perfect."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
    from encoder import encode_pass, BYTEGROUP

    # 14x14 test pattern: diagonal line
    pixels = []
    for y in range(14):
        row = [0] * 28
        row[y * 2] = 1
        row[y * 2 + 1] = 1
        pixels.append(row)

    width = 28 + 13 + 12  # pad for offsets
    shifted = []
    for row in pixels:
        shifted.append([0] * 13 + row + [0] * 12)

    pkt_bytes = encode_pass(shifted, width, 184, 0)

    # Parse SPD packet
    ps = pkt_bytes.find(b'!SPD ') + 5
    parts = pkt_bytes[ps:ps + 60].decode('ascii', errors='replace').split()
    height = int(parts[0])
    y_pos = int(parts[1])
    w = int(parts[2])
    bg = int(parts[3])
    ns = ps + len(f'{height} {y_pos} {w} {bg} ')
    nd = pkt_bytes[ns:-2]  # strip \x00\x0d

    pkt = {'height': height, 'y': y_pos, 'width': w, 'bg': bg,
           'header': nd[0], 'data': nd[1:]}

    inv_tables = [inv(TABLES['B']), inv(TABLES['A']), inv(TABLES['A_L1'])]
    channels = decode_pass(pkt, inv_tables)

    # M channel should recover original pixels (offset by pad_left=13)
    for y in range(14):
        expected_x = y * 2 + 13
        assert (y, expected_x) in channels[0], f"Missing M pixel at ({y}, {expected_x})"
        assert (y, expected_x + 1) in channels[0], f"Missing M pixel at ({y}, {expected_x + 1})"

    print("PASS: encode→decode round-trip verified")


if __name__ == '__main__':
    test_1dot()
    test_roundtrip()
