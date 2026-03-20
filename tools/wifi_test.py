#!/usr/bin/env python3
"""
Newyes LD0806 — WiFi print test.

Mirrors the exact iOS app connection pattern:
- Fresh TCP connection per action
- GPD → GDS before any write command
- Prints "Hello World!" over WiFi

Logs everything to wifi_test.log.

Usage:
    python3 tools/wifi_test.py
"""

import socket
import time
from datetime import datetime

HOST = "192.168.4.1"
PORT_QUERY = 9100   # read-only queries (GPD, GDS, GFV, GDE, GPS, GFC)
PORT_WRITE = 9200   # write/action commands (SDC, SFC, DPC, SPD, DSP)
LOG_FILE = "wifi_test.log"

HEADER = b'\x1b\x30\x31'
BYTEGROUP = 14
BITS_PER_BYTE = 8
NOZZLE_HEIGHT = BYTEGROUP * BITS_PER_BYTE  # 112
PASS_STRIDE = 98
CH1_OFFSET = 12
CH2_OFFSET = -13

ROW_PERM_M = [12, 3, 8, 13, 4, 9, 14, 5, 10, 1, 6, 11, 2, 7]
ROW_PERM_C = [9, 14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4]
ROW_PERM_Y = [14, 5, 10, 1, 6, 11, 2, 7, 12, 3, 8, 13, 4, 9]

results = []


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] {msg}"
    print(line)
    results.append(line)


def build_cmd(mode, cmd, params=""):
    pkt = HEADER + mode.encode() + cmd.encode() + b'\x20'
    if params:
        pkt += params.encode() + b'\x20'
    pkt += b'\x00\x00\x0d'
    return pkt


def hex_dump(data, max_bytes=80):
    return data[:max_bytes].hex(' ')


def parse_response(data):
    if not data or len(data) < 10 or data[:2] != b'\x1b\x30':
        return None
    cmd = data[2:6].decode('ascii', errors='replace')
    payload = data[10:-3].decode('ascii', errors='replace').strip()
    return cmd, payload


def connect(port):
    """Open a fresh TCP connection to the printer."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect((HOST, port))
    log(f"Connected to {HOST}:{port}")
    return sock


def close(sock):
    """Cleanly close a connection."""
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    sock.close()


def send_recv(sock, pkt, label, timeout=5):
    log(f"TX {label} ({len(pkt)}b): {hex_dump(pkt)}")
    t0 = time.time()
    try:
        sock.sendall(pkt)
    except Exception as e:
        log(f"TX {label} SEND ERROR: {e}")
        return None

    sock.settimeout(timeout)
    try:
        resp = sock.recv(4096)
        elapsed = (time.time() - t0) * 1000
        log(f"RX {label} ({elapsed:.0f}ms, {len(resp)}b): {hex_dump(resp)}")
        parsed = parse_response(resp)
        if parsed:
            log(f"   PARSED: cmd={parsed[0]} payload=\"{parsed[1]}\"")
        return resp
    except socket.timeout:
        elapsed = (time.time() - t0) * 1000
        log(f"RX {label} TIMEOUT after {elapsed:.0f}ms")
        return None
    except Exception as e:
        log(f"RX {label} ERROR: {e}")
        return None


def handshake(sock):
    """GPD → GDS handshake (iOS app does this before every action)."""
    send_recv(sock, build_cmd("~", "GPD", ""), "GPD")
    time.sleep(0.1)
    send_recv(sock, build_cmd("~", "GDS", ""), "GDS")
    time.sleep(0.1)


# ── Encoder (self-contained) ───────────────────────────────────────────────

def encode_pass(pixels, width, pass_y_global, image_y_offset=0):
    img_h = len(pixels)
    nozzle = bytearray(1 + width * BYTEGROUP * 3)
    nozzle[0] = 0x00

    for local_y in range(NOZZLE_HEIGHT):
        img_y = image_y_offset + local_y
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
            x1 = x + CH1_OFFSET
            if 0 <= x1 < width:
                idx1 = 1 + (x1 * BYTEGROUP + nr_c) * 3 + 1
                nozzle[idx1] |= mask
            x2 = x + CH2_OFFSET
            if 0 <= x2 < width:
                idx2 = 1 + (x2 * BYTEGROUP + nr_y) * 3 + 2
                nozzle[idx2] |= mask

    height_param = 337
    params = f"{height_param} {pass_y_global} {width} {BYTEGROUP}"
    pkt = HEADER + b'!SPD ' + params.encode() + b' '
    pkt += bytes(nozzle) + b'\x00\x00\x0d'
    return pkt


def encode_hello_world():
    FONT = {
        'H': [0x42, 0x42, 0x42, 0x7E, 0x42, 0x42, 0x42, 0x00],
        'e': [0x00, 0x00, 0x3C, 0x42, 0x7E, 0x40, 0x3C, 0x00],
        'l': [0x30, 0x10, 0x10, 0x10, 0x10, 0x10, 0x38, 0x00],
        'o': [0x00, 0x00, 0x3C, 0x42, 0x42, 0x42, 0x3C, 0x00],
        ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        'W': [0x42, 0x42, 0x42, 0x42, 0x5A, 0x66, 0x42, 0x00],
        'r': [0x00, 0x00, 0x5C, 0x62, 0x40, 0x40, 0x40, 0x00],
        'd': [0x02, 0x02, 0x3E, 0x42, 0x42, 0x42, 0x3E, 0x00],
        '!': [0x10, 0x10, 0x10, 0x10, 0x10, 0x00, 0x10, 0x00],
    }
    text = "Hello World!"
    scale = 4
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

    img_h = len(pixels)
    img_w = len(pixels[0])
    pad_left = max(0, -CH2_OFFSET)
    pad_right = max(0, CH1_OFFSET)
    total_w = img_w + pad_left + pad_right

    shifted = []
    for row in pixels:
        new_row = [0] * pad_left + list(row) + [0] * pad_right
        shifted.append(new_row[:total_w])

    packets = []
    y_offset = 0
    page_y = 492
    while y_offset < img_h:
        pkt = encode_pass(shifted, total_w, page_y + y_offset, y_offset)
        packets.append(pkt)
        y_offset += PASS_STRIDE

    return text, img_w, img_h, total_w, packets


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    log(f"Newyes LD0806 WiFi Test — {datetime.now().isoformat()}")
    log(f"Target: {HOST} (query:{PORT_QUERY}, write:{PORT_WRITE})")

    # ── Connection 1: Queries on port 9100 ──
    log("")
    log("=" * 60)
    log("CONNECTION 1: Status queries (port 9100)")
    log("=" * 60)
    try:
        s = connect(PORT_QUERY)
    except Exception as e:
        log(f"CONNECT FAILED: {e}")
        save_log()
        return

    try:
        handshake(s)
        for mode, cmd, params, label in [
            ("~", "GFV", "", "Firmware"),
            ("~", "GDE", "", "Battery"),
            ("~", "GPD", "", "PrinterIdle"),
            ("~", "GFC", "4", "SleepConfig"),
            ("~", "GPS", "", "Status"),
        ]:
            send_recv(s, build_cmd(mode, cmd, params), label)
            time.sleep(0.1)
    finally:
        close(s)
        log("Connection 1 closed")

    time.sleep(0.5)

    # ── Connection 2: Set sleep on port 9200 (write port) ──
    log("")
    log("=" * 60)
    log("CONNECTION 2: Set sleep to 30 min (port 9200)")
    log("=" * 60)
    try:
        s = connect(PORT_WRITE)
    except Exception as e:
        log(f"CONNECT FAILED: {e}")
        save_log()
        return

    try:
        handshake(s)
        resp = send_recv(s, build_cmd("~", "SFC", "4 5"), "SetSleep(30min)")
        if resp:
            log("SFC ACK received!")
        else:
            log("SFC: no ACK")
    finally:
        close(s)
        log("Connection 2 closed")

    time.sleep(0.5)

    # Verify sleep on query port
    try:
        s = connect(PORT_QUERY)
        send_recv(s, build_cmd("~", "GFC", "4"), "GetSleep(verify)")
        close(s)
    except Exception as e:
        log(f"Verify failed: {e}")

    time.sleep(0.5)

    # ── Connection 3: Print Hello World on port 9200 ──
    log("")
    log("=" * 60)
    log("CONNECTION 3: Print Hello World! (port 9200)")
    log("=" * 60)

    text, img_w, img_h, total_w, spd_packets = encode_hello_world()
    log(f"Image: '{text}' — {img_w}x{img_h} px, padded to {total_w} cols")
    log(f"SPD passes: {len(spd_packets)}")
    for i, pkt in enumerate(spd_packets):
        log(f"  Pass {i}: {len(pkt)} bytes")

    try:
        s = connect(PORT_WRITE)
    except Exception as e:
        log(f"CONNECT FAILED: {e}")
        save_log()
        return

    try:
        handshake(s)

        log("--- Print sequence ---")
        resp = send_recv(s, build_cmd("~", "SDC", "1 0 0 1"), "SDC", timeout=5)
        if not resp:
            log("ERROR: No ACK for SDC — aborting")
            return
        time.sleep(0.2)

        resp = send_recv(s, build_cmd("!", "DPC", "0"), "DPC_in", timeout=10)
        if not resp:
            log("ERROR: No ACK for DPC 0 — aborting")
            return
        time.sleep(0.5)

        for i, pkt in enumerate(spd_packets):
            resp = send_recv(s, pkt, f"SPD_{i+1}/{len(spd_packets)}", timeout=10)
            if not resp:
                log(f"ERROR: No ACK for SPD pass {i+1} — aborting")
                return
            time.sleep(0.3)

        resp = send_recv(s, build_cmd("!", "DPC", "1"), "DPC_out", timeout=10)
        if not resp:
            log("ERROR: No ACK for DPC 1")
            return

        log("")
        log("*** PRINT COMPLETE! ***")
    finally:
        close(s)
        log("Connection 3 closed")

    log("")
    log("=" * 60)
    log("ALL TESTS COMPLETE")
    log("=" * 60)
    save_log()


def save_log():
    with open(LOG_FILE, 'w') as f:
        f.write('\n'.join(results) + '\n')
    print(f"\nLog saved to {LOG_FILE}")


if __name__ == "__main__":
    main()
