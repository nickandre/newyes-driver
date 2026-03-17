#!/usr/bin/env python3
"""
Newyes LD0806 — WiFi Query Tool

NOTE: Only query commands (GPS, GFV, GDS, GDE, GPD, GFC) work over WiFi.
Write/action commands (SDC, DPC, SPD) are accepted but silently ignored by
the WiFi firmware — no ACK, no physical action. Printing over WiFi does not
work. See docs/TRACE_ANALYSIS.md for details.

Protocol reverse-engineered from libprintsdk.so (Android APK).

Packet format:
  \x1b\x30\x31 [~|!] [CMD 3-char] [params...] \x00\x00 \x0d
  - \x1b\x30\x31 = header (ESC '0' '1')
  - ~ = read/query command
  - ! = write/action command
  - CMD = 3-letter command code
  - params = space-padded parameter bytes
  - \x00\x00 = checksum placeholder
  - \x0d = terminator (CR)

Usage:
    python3 newyes_print.py status
    python3 newyes_print.py print test
    python3 newyes_print.py print image.png
"""

import socket
import struct
import sys
import time
import io
import logging
import argparse
from pathlib import Path

PRINTER_IP = "192.168.4.1"
PRINTER_PORT = 9100
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 3
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

logging.basicConfig(level=logging.DEBUG, format=LOG_FORMAT)
log = logging.getLogger("newyes")

# ── Protocol Commands ────────────────────────────────────────────────────────
# Extracted from libprintsdk.so disassembly

HEADER = b'\x1b\x30\x31'
TERMINATOR = b'\x0d'

def build_cmd(mode: bytes, code: str, *int_params) -> bytes:
    """Build a protocol command packet.

    Packet: HEADER + mode + CMD + SPACE + ascii_params + SPACE + \x00\x00 + \x0d
    Parameters are encoded as space-separated ASCII decimal integers.

    mode: b'~' for query, b'!' for action
    code: 3-letter command code (e.g. 'GPS', 'GFV')
    int_params: integer parameters (encoded as ASCII decimal)
    """
    pkt = HEADER + mode + code.encode('ascii') + b'\x20'
    if int_params:
        param_str = ' '.join(str(p) for p in int_params)
        pkt += param_str.encode('ascii') + b'\x20'
    pkt += b'\x00\x00' + TERMINATOR
    return pkt

# Query commands (mode = ~)
CMD_GET_STATE = lambda: build_cmd(b'~', 'GPS')
CMD_GET_FIRMWARE = lambda: build_cmd(b'~', 'GFV')
CMD_GET_SERIAL = lambda: build_cmd(b'~', 'GDS')
CMD_GET_ELECTRIC = lambda: build_cmd(b'~', 'GDE')
CMD_GET_PRINT_IDLE = lambda: build_cmd(b'~', 'GPD')
CMD_GET_SENSOR = lambda: build_cmd(b'~', 'GSS')
CMD_GET_FACTORY = lambda i=4: build_cmd(b'~', 'GFC', i)
CMD_GET_WIFI = lambda t=0: build_cmd(b'~', 'GHC', t)
CMD_GET_PRINT_NUM = lambda t=0: build_cmd(b'~', 'GPN', t)
CMD_GET_MOTOR_INFO = lambda t=0: build_cmd(b'~', 'GMI', t)
CMD_CONNECT_TEST = lambda: build_cmd(b'~', 'GCT')

# Action commands (mode = !)
CMD_PRINT_BUFFER = lambda: build_cmd(b'!', 'PBD')
CMD_PAPER_CONTROL = lambda t: build_cmd(b'!', 'DPC', t)
CMD_CARTRIDGE_CLEAN = lambda t=0, i=1, s=1: build_cmd(b'!', 'DSP', t, i, s)
CMD_CLOSE_SOCKET = lambda: build_cmd(b'!', 'CSC')

# Set commands (mode = ~)
CMD_SET_PRINT_CONFIG = lambda color=0, direction=0, paper=0, density=3: \
    build_cmd(b'~', 'SDC', color, direction, paper, density)


# ── Connection ──────────────────────────────────────────────────────────────

class PrinterConnection:
    def __init__(self, host=PRINTER_IP, port=PRINTER_PORT):
        self.host = host
        self.port = port
        self.sock = None

    def connect(self):
        log.info(f"Connecting to {self.host}:{self.port}...")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(CONNECT_TIMEOUT)
        self.sock.connect((self.host, self.port))
        log.info("Connected!")

    def close(self):
        if self.sock:
            try:
                self.send(CMD_CLOSE_SOCKET(), "close_socket")
            except:
                pass
            self.sock.close()
            self.sock = None
            log.info("Disconnected")

    def send(self, data: bytes, label=""):
        hex_preview = data[:40].hex()
        ascii_preview = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[:40])
        log.debug(f"TX {label} ({len(data)}b): {hex_preview}{'...' if len(data)>40 else ''}")
        log.debug(f"TX ASCII: {ascii_preview}")
        self.sock.sendall(data)

    def recv(self, size=4096, timeout=READ_TIMEOUT) -> bytes:
        self.sock.settimeout(timeout)
        try:
            data = self.sock.recv(size)
            if data:
                hex_preview = data[:60].hex()
                ascii_preview = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[:60])
                log.debug(f"RX ({len(data)}b): {hex_preview}{'...' if len(data)>60 else ''}")
                log.debug(f"RX ASCII: {ascii_preview}")
            else:
                log.debug("RX: connection closed")
            return data
        except socket.timeout:
            log.debug("RX timeout")
            return b""

    def send_cmd(self, cmd: bytes, label="", wait=0.3) -> bytes:
        """Send command and wait for response."""
        self.send(cmd, label)
        time.sleep(wait)
        return self.recv()

    def query_all_status(self):
        """Run all status queries and log responses."""
        queries = [
            ("ConnectTest", CMD_CONNECT_TEST()),
            ("GetState", CMD_GET_STATE()),
            ("GetFirmware", CMD_GET_FIRMWARE()),
            ("GetSerial", CMD_GET_SERIAL()),
            ("GetElectric", CMD_GET_ELECTRIC()),
            ("GetPrintIdle", CMD_GET_PRINT_IDLE()),
            ("GetSensor", CMD_GET_SENSOR()),
            # Factory configs - different indices return different params
            ("GetFactory(0)", build_cmd(b'~', 'GFC', 0)),
            ("GetFactory(1)", build_cmd(b'~', 'GFC', 1)),
            ("GetFactory(2)", build_cmd(b'~', 'GFC', 2)),
            ("GetFactory(3)", build_cmd(b'~', 'GFC', 3)),
            ("GetFactory(4)", build_cmd(b'~', 'GFC', 4)),
            ("GetFactory(5)", build_cmd(b'~', 'GFC', 5)),
            ("GetFactory(6)", build_cmd(b'~', 'GFC', 6)),
            ("GetFactory(7)", build_cmd(b'~', 'GFC', 7)),
            ("GetFactory(8)", build_cmd(b'~', 'GFC', 8)),
            ("GetFactory(9)", build_cmd(b'~', 'GFC', 9)),
            ("GetFactory(10)", build_cmd(b'~', 'GFC', 10)),
            # Motor info
            ("GetMotor(0)", build_cmd(b'~', 'GMI', 0)),
            ("GetMotor(1)", build_cmd(b'~', 'GMI', 1)),
            # Print number
            ("GetPrintNum(0)", build_cmd(b'~', 'GPN', 0)),
            ("GetPrintNum(1)", build_cmd(b'~', 'GPN', 1)),
        ]

        for name, cmd in queries:
            log.info(f"--- {name} ---")
            resp = self.send_cmd(cmd, name)
            if resp:
                self.parse_response(resp, name)
            time.sleep(0.2)

    def parse_response(self, data: bytes, context: str):
        """Parse a protocol response packet."""
        if len(data) < 4:
            log.info(f"  Short response: {data.hex()}")
            return

        # Response: \x1b\x30 + ~CMD + \x00\x00\x00\x00 + space + payload + space + \x00\x00 + \x0d
        if data[0:2] == b'\x1b\x30':
            mode = chr(data[2])
            cmd = data[3:6].decode('ascii', errors='replace')
            # Find payload between the 4 zero bytes and the trailing \x00\x00\x0d
            payload_start = 10  # after header(2) + mode(1) + cmd(3) + zeros(4)
            payload_end = len(data) - 3  # before \x00\x00\x0d
            payload = data[payload_start:payload_end].decode('ascii', errors='replace').strip()
            log.info(f"  {cmd}: \"{payload}\"")
            return payload
        else:
            log.info(f"  Raw response: {data.hex()}")
            log.info(f"  ASCII: {''.join(chr(b) if 32<=b<127 else '.' for b in data)}")


# ── Image Preparation ───────────────────────────────────────────────────────

def prepare_image(image_path: str, max_width: int = 384):
    from PIL import Image
    img = Image.open(image_path)
    log.info(f"Loaded: {img.size[0]}x{img.size[1]} mode={img.mode}")

    if img.size[0] > max_width:
        ratio = max_width / img.size[0]
        new_h = int(img.size[1] * ratio)
        img = img.resize((max_width, new_h), Image.LANCZOS)
        log.info(f"Resized to {img.size[0]}x{img.size[1]}")

    if img.mode != '1':
        img = img.convert('L')
        img = img.point(lambda x: 0 if x < 128 else 255, '1')

    width, height = img.size
    row_bytes = (width + 7) // 8
    raw = img.tobytes()
    # Invert: PIL 1=white, printer 1=ink
    raw = bytes(b ^ 0xFF for b in raw)
    log.info(f"Bitmap: {width}x{height}, {row_bytes} bytes/row, {len(raw)} total")
    return width, height, raw, row_bytes


def make_test_pattern(width=384, height=100):
    row_bytes = (width + 7) // 8
    data = bytearray(row_bytes * height)
    for y in range(height):
        for xb in range(row_bytes):
            x = xb * 8
            val = 0
            if y < 2 or y >= height - 2:
                val = 0xFF
            elif x < 8:
                val = 0x80
            elif x + 8 >= width:
                val = 0x01
            elif y % 20 == 0:
                val = 0xFF
            elif abs(x - y) < 2:
                val = 0xFF
            data[y * row_bytes + xb] = val
    log.info(f"Test pattern: {width}x{height}, {len(data)} bytes")
    return width, height, bytes(data), row_bytes


# ── Commands ────────────────────────────────────────────────────────────────

def cmd_status(args):
    conn = PrinterConnection(args.host, args.port)
    conn.connect()
    try:
        conn.query_all_status()
    finally:
        conn.close()


def cmd_print(args):
    conn = PrinterConnection(args.host, args.port)

    if args.image == 'test':
        width, height, raw_data, row_bytes = make_test_pattern(args.width)
    else:
        path = Path(args.image)
        if not path.exists():
            log.error(f"File not found: {args.image}")
            return
        width, height, raw_data, row_bytes = prepare_image(args.image, args.width)

    conn.connect()
    try:
        # Step 1: Get state
        log.info("=== Get State ===")
        resp = conn.send_cmd(CMD_GET_STATE(), "GetState")
        if resp:
            conn.parse_response(resp, "GetState")

        # Step 2: Set print config (color=0/BW, direction=0/fwd, paper=0, density=3)
        log.info("=== Set Print Config ===")
        conn.send_cmd(CMD_SET_PRINT_CONFIG(0, 0, 0, 3), "SetPrintConfig", wait=0.5)

        # Step 3: Paper control - init paper feed (type=0)
        log.info("=== Paper Control ===")
        conn.send_cmd(CMD_PAPER_CONTROL(0), "PaperControl", wait=0.5)

        # Step 4: Send print data using !SPD command
        # Protocol_SendPrintData builds: \x1b\x30\x31\x21 SPD \x20 + params + data
        # 5 snprintf("%d") calls = 5 int params as ASCII, then memcpy of raw data
        log.info(f"=== Send Print Data ({len(raw_data)} bytes, {width}x{height}) ===")

        # Build SPD packet: header + !SPD + space + 5 ASCII params + space + raw data + checksum + CR
        pkt = HEADER + b'!SPD\x20'
        # Params from function signature: (int direction, int dataSize, int sendW, int x, int y, data, out)
        params = f"0 {len(raw_data)} {row_bytes} 0 0"
        pkt += params.encode('ascii') + b'\x20'
        pkt += raw_data
        pkt += b'\x00\x00' + TERMINATOR

        conn.send(pkt, "SPD")
        time.sleep(3)
        resp = conn.recv(timeout=5)
        if resp:
            conn.parse_response(resp, "SPD_response")
        else:
            log.info("No response to SPD (may be normal)")

        # Step 5: Try PBD (PrintBufferData) + raw data as alternative approach
        log.info("=== Try PBD + raw data ===")
        conn.send(CMD_PRINT_BUFFER(), "PBD_header")
        time.sleep(0.2)
        conn.send(raw_data, "PBD_data")
        time.sleep(3)
        resp = conn.recv(timeout=5)
        if resp:
            conn.parse_response(resp, "PBD_response")

        log.info("Print job sent. Check printer.")
    finally:
        conn.close()


def cmd_discover(args):
    host = args.host
    ports = [9100, 12345, 8080, 80, 443, 8899, 6000, 4000, 5000,
             3000, 19000, 18000, 1234, 5555, 8888, 9999]

    log.info(f"Scanning {host}...")
    for port in ports:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            if s.connect_ex((host, port)) == 0:
                log.info(f"  Port {port}: OPEN")
            s.close()
        except:
            pass


def cmd_clean(args):
    conn = PrinterConnection(args.host, args.port)
    conn.connect()
    try:
        resp = conn.send_cmd(CMD_CARTRIDGE_CLEAN(0, 1, 1), "Clean")
        if resp:
            conn.parse_response(resp, "Clean")
    finally:
        conn.close()


def cmd_raw(args):
    """Send a raw command string to the printer."""
    conn = PrinterConnection(args.host, args.port)
    conn.connect()
    try:
        # Parse hex string or use as ASCII
        if args.data.startswith('0x') or all(c in '0123456789abcdefABCDEF ' for c in args.data):
            data = bytes.fromhex(args.data.replace('0x', '').replace(' ', ''))
        else:
            data = args.data.encode('ascii')

        resp = conn.send_cmd(data, "raw", wait=1)
        if resp:
            conn.parse_response(resp, "raw")
    finally:
        conn.close()


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Newyes LD0806 Smart Printer Tool")
    parser.add_argument("--host", default=PRINTER_IP)
    parser.add_argument("--port", type=int, default=PRINTER_PORT)

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("discover", help="Scan for printer on network")
    sub.add_parser("status", help="Query printer status")
    sub.add_parser("clean", help="Clean print head")

    p = sub.add_parser("print", help="Print an image or test pattern")
    p.add_argument("image", help="Image file or 'test'")
    p.add_argument("--width", type=int, default=384, help="Max width in pixels")

    p = sub.add_parser("raw", help="Send raw hex/ascii command")
    p.add_argument("data", help="Hex bytes or ASCII string")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {'discover': cmd_discover,
     'status': cmd_status,
     'print': cmd_print,
     'clean': cmd_clean,
     'raw': cmd_raw,
    }[args.command](args)


if __name__ == "__main__":
    main()
