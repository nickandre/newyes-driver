# Nozzle Pattern — What We Know and Don't Know

## Print Head Physical Layout

The LD0806 uses a CMY (Cyan, Magenta, Yellow) inkjet cartridge. There is no black (K) cartridge — black is produced by combining C+M+Y.

The print head has **three separate color banks** (C, M, Y) that are **physically offset** from each other:
- Horizontally: ~1mm between banks (visible as color fringe on edges of solid black)
- Vertically: arranged in a V pattern — Cyan top-left, Yellow top-right, Magenta bottom-center

## SPD Data Format — Confirmed

```
Packet: \x1b\x30\x31 !SPD <height> <Y> <width> <bytegroup> <header_byte> <nozzle_data> \x00\x00 \x0d
```

| Field | Meaning | Confirmed By |
|-------|---------|-------------|
| height | Pass height param (136 in real capture, 714 in earlier) | USB trace |
| Y | Vertical page position | USB trace |
| width | Number of horizontal pixel columns | USB trace + box test |
| bytegroup | Always 14 | USB trace |
| header_byte | 0x00 or 0x02 | USB trace |

## Data Layout — Confirmed: Column-Major

```
For each horizontal column (0 to width-1):
    For each of 14 nozzle rows (0 to 13):
        3 bytes: [byte_0] [byte_1] [byte_2]
```

**Total data** = 1 header byte + width × 14 × 3 bytes

Confirmed by:
- `width * 14 * 3 = body_size` matches exactly in USB traces
- Solid box (all 0xFF) prints correctly as a rectangle
- Staircase test (different length per row) shows rows correspond to horizontal positions correctly

## What We've Confirmed

### Row ordering is sequential
The staircase test (each row = different horizontal length bar) showed rows 0-13 map to vertical positions top-to-bottom in order. **No row interleaving.**

### Each byte has 8 bits = 8 vertical sub-positions
- All 0xFF = solid band (confirmed by box test)
- Bit 7 (MSB, 0x80) fires near the top of the row's band
- Bit 0 (LSB, 0x01) fires near the bottom
- The staircase within a row (bits 7→0 at different lengths) showed sequential top-to-bottom ordering

### Columns map left-to-right
Half-length test confirmed column 0 = left side of page.

## What's Wrong / Unknown

### Channel byte order
We assumed `[C, M, Y]` but experiments show:
- Byte 0 is **NOT** cyan and **NOT** magenta — results are inconsistent
- Putting data in byte 1 expecting magenta produced cyan output
- Putting data in byte 0 expecting cyan also produced different results

**The 3-byte channel mapping is unresolved.** It may not be a simple fixed C/M/Y order.

### Bit-to-nozzle mapping within a byte
While the general direction is correct (bit 7 = top, bit 0 = bottom), text printing produces garbled output. Specifically:
- "Hello World!" at 8x scale produced text with correct overall shape but "some rows interspersed lower than they should be" — the L characters had jagged bases
- Individual bit tests show the 8 bits are roughly evenly spaced but NOT contiguous — there are gaps between nozzle positions

### Only ~7 of 14 rows visible in single-bit test
When setting only bit 7 (0x80) for each row individually with magenta-only data, only ~7 lines appeared (not 14). This suggests:
- Rows may be **paired** — two data rows share one physical nozzle group
- Or the 14 rows map to alternating even/odd physical banks
- Or some rows control different aspects (e.g., forward/reverse pass)

### The DFixGroup regroup function
The SDK has `p803SDK_DFixGroup` and `p803SDK_DDateGroup` functions that permute the data before sending. The `ReroupData14` function is a simple lookup table permutation:
```c
for (int i = 0; i < count; i++) {
    output[lookup[i] - 1] = input[i];
}
```
We have not been able to extract the actual lookup table values.

## What Prints Correctly

| Test | Result | Implications |
|------|--------|-------------|
| All 0xFF, width=1073, 14 rows | Solid black rectangle with CMY edge fringe | Basic format is correct |
| All 0xFF, width=300 (left) + 0x00 width=300 (right) | Clean box on left, white on right | Column ordering correct |
| Staircase: each row different length, all 0xFF | Tapered shape, rows in order | Row ordering is sequential |
| Bit ladder: each bit different length in row 0 | Sequential top-to-bottom | Bit ordering is MSB=top |
| Captured USB replay from real SDK | Full image prints perfectly | The protocol is correct |
| Text with y%14/y//14 interleave | Almost correct but rows slightly shuffled | Close but not exact interleave |

## Next Steps

1. **Extract the DFixGroup lookup table** — either by disassembly or by printing 14 individually identifiable patterns (need unique identification, not just bit 7)
2. **Determine exact channel byte order** — print data in only byte 0, only byte 1, only byte 2 separately with 0xFF to identify which color each position controls
3. **Compare our output with real SDK output** — for a known input image, capture what the SDK produces and compare byte-by-byte with our encoder's output
4. **Consider using the SDK DLL** — if the interleave pattern is too complex to reverse engineer, loading the DLL with its Qt/OpenCV dependencies on Windows would bypass the problem entirely

## Physical Nozzle Geometry (Estimated)

- 14 nozzle rows × 8 bits = 112 vertical sub-positions per pass
- At 300 DPI: 112 / 300 = 0.37 inches per pass
- Y stride between passes: ~92-98 units
- Three color banks (C, M, Y) physically offset in V pattern
- Total nozzle count: 14 × 8 × 3 colors = 336 individual nozzles
