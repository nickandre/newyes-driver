# Nozzle Encoding — Fully Solved

The complete encoding pipeline for the Newyes LD0806 CMY print head.

## Physical Print Head

The LD0806 uses a CMY (Cyan, Magenta, Yellow) inkjet cartridge. No black — black is C+M+Y combined.

Three color banks are physically offset from each other:
- **Horizontally**: ch1(C) is +12 columns right, ch2(Y) is -13 columns left relative to ch0(M)
- **Vertically**: arranged in a V pattern — Cyan top-left, Yellow top-right, Magenta bottom-center

## SPD Packet Format

```
\x1b\x30\x31 !SPD <height> <Y> <width> 14 <header_byte> <nozzle_data> \x00\x00 \x0d
```

| Field | Meaning |
|-------|---------|
| height | Pass height parameter (varies: 163, 541, 714 seen in captures) |
| Y | Vertical page position (increments by stride between passes) |
| width | Number of horizontal pixel columns |
| 14 | Always 14 (bytegroup / nozzle rows) |
| header_byte | 0x00 (first byte of nozzle data body) |

## Nozzle Data Layout — Column-Major

```
For each horizontal column (0 to width-1):
    For each of 14 nozzle rows (0 to 13):
        3 bytes: [ch0/Magenta] [ch1/Cyan] [ch2/Yellow]
```

**Total nozzle data** = 1 header byte + width × 14 × 3 bytes

## Encoding Formula

Source pixel at `(x, y)`:
```
source_row  = y % 14
bit_index   = y // 14
bitmask     = 0x80 >> bit_index
```

Then apply the **per-channel row permutation** and **column offset**:
```
ch0/M: nozzle_col = x,      nozzle_row = ROW_PERM_M[source_row] - 1
ch1/C: nozzle_col = x + 12, nozzle_row = ROW_PERM_C[source_row] - 1
ch2/Y: nozzle_col = x - 13, nozzle_row = ROW_PERM_Y[source_row] - 1
```

Write into nozzle data:
```
nozzle[(nozzle_col * 14 + nozzle_row) * 3 + channel] |= bitmask
```

## Row Permutation Tables — SOLVED

Extracted from `libprintsdk.so` in the Android APK (`iSmart Printer`) at binary offset `0xa4ba4`. Verified against `1dot.pcapng`, `2dots.pcapng`, and `hello-world-x6.pcapng` captures using `tests/decode_nozzle.py`.

The SDK function `p803SDK_ReroupData14` applies: `output[table[i] - 1] = input[i]`

| Channel | Byte | Table (1-based) | Origin |
|---------|------|-----------------|--------|
| Magenta | 0 | `[12, 3, 8, 13, 4, 9, 14, 5, 10, 1, 6, 11, 2, 7]` | YMC_B |
| Cyan | 1 | `[9, 14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4]` | YMC_A |
| Yellow | 2 | `[14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4, 9]` | YMC_A rotated left by 1 |

**How verified**: Hardware print tests on the LD0806. M and C tables were confirmed first (readable "Hello World!" text, smooth magenta and cyan diagonal lines). The yellow table required fine-tuning — raw YMC_A produced 8 stray dots (one per bit group, consistent with source_row 0 being off by 1), and rotating left by 1 position eliminated all artifacts. The final tables produce perfect diagonal lines in all three colors.

### How the tables relate

The three raw tables in the binary at `0xa4ba4` (A), `0xa4bb2` (B), `0xa4bc0` (C) are rotations of the same underlying pattern. Duplicates at `0xa4bce-0xa4bea`.

The disassembled `GetRegroupRect_YMC` function assigns channels by `(byte_index+1)%3`, giving M=C, C=B, Y=A. However, **hardware testing shows different assignments**: M=B, C=A, and Y=A-rotated-left-1. The discrepancy likely means the disassembled function is not the final code path used for the p803/LD0806, or there is additional processing we didn't capture. The hardware-verified tables are definitive.

There are also two K (black cartridge) tables at `0xa4b6c` / `0xa4b7a` for the separate black-only cartridge model, unused by the LD0806's CMY cartridge.

## Multi-Pass Tiling

- **Pass height**: 14 rows × 8 bits = 112 vertical pixels per pass
- **Pass stride**: 98 pixels between passes (overlapping by 14)
- At typical font sizes, one pass covers ~2 lines of text

## Print Job Sequence

```
~GDS                    → handshake (get serial)
~SDC 1 0 0 1            → config (YMC cartridge, left-to-right, normal paper, normal density)
!DPC 0                  → feed paper in
!SPD ... [nozzle_data]  → print pass (repeat for each pass, Y increments by stride)
!DPC 1                  → feed paper out
```

## Paper Width

The printable width is approximately **2146 pixels** (~7.15" at 300 DPI). The SDK's pcapng captures use content-sized widths (30-1073), not full paper width.

## Source Files

- `tools/encoder.py` — image-to-nozzle encoder with permutation tables
- `tools/print_image.py` — full color image printing (RGB→CMY, Floyd-Steinberg dither, multi-pass)
- `tests/decode_nozzle.py` — nozzle-to-bitmap decoder for verifying captures
- `tests/test_sdk_encoding.py` — verification against 1dot capture

## Exploration History

The row permutation was the last piece of the puzzle. The journey:

1. **Column-major layout** confirmed via solid-box and staircase print tests
2. **Channel byte order** (M=0, C=1, Y=2) confirmed via single-channel prints
3. **Channel offsets** (C=+12, Y=-13) measured from captured dot positions
4. **Encoding formula** `y%14 / y//14` confirmed via 100% round-trip with captured SDK data
5. **Row permutation** was the remaining unknown — text printed garbled while solid shapes worked fine
6. **Candidate permutation** `[0,9,5,1,10,6,4,13,8,3,12,7,2,11]` from dot analysis printed "hetto" instead of "hello" — confirming row groups but not exact ordering
7. **Extracted tables from Android APK** — disassembled `libprintsdk.so` (x86_64) to find 3 lookup tables and the `ReroupData14` function
8. **Verified M=B, C=A via hardware prints** — brute-force tested all 6 table-to-channel permutations; readable text and smooth diagonals confirmed M and C tables
9. **Yellow table fine-tuned on hardware** — raw table A had 8 stray dots (one per bit group); rotating left by 1 position eliminated all artifacts, producing perfect yellow diagonals
10. **Full color image printed** — CMY parrots photo with Floyd-Steinberg dithering printed successfully at full paper width (2146 pixels)
