#!/usr/bin/env python3
"""
Newyes LD0806 — Unified Printer Interface

Supports WiFi TCP transport (USB is Windows-only, see windows_usb_demo.py).
Protocol is identical over both transports.

Usage:
    python tools/printer.py status
    python tools/printer.py clean
    python tools/printer.py sleep [1|2|5|10|30|60|120|240]
    python tools/printer.py paper [in|out]
    python tools/printer.py print <image_path>
"""

import socket
import sys
import time
import logging
import argparse

log = logging.getLogger("newyes")

# ── Protocol ────────────────────────────────────────────────────────────────

HEADER = b'\x1b\x30\x31'
TERMINATOR = b'\x0d'

# Sleep time values for ~SFC/~GFC command (index 4)
SLEEP_VALUES = {
    1: 1, 2: 2, 3: 5, 4: 10, 5: 30, 6: 60, 7: 120, 8: 240,
}
SLEEP_MINUTES_TO_VALUE = {v: k for k, v in SLEEP_VALUES.items()}


def build_cmd(mode, cmd, params=""):
    """Build a protocol command packet.

    mode: '~' for query/set, '!' for action
    cmd: 3-letter command code
    params: space-separated parameter string
    """
    pkt = HEADER + mode.encode() + cmd.encode() + b'\x20'
    if params:
        pkt += params.encode() + b'\x20'
    pkt += b'\x00\x00' + TERMINATOR
    return pkt


def parse_response(data):
    """Parse a protocol response, return (cmd, payload) or None."""
    if not data or len(data) < 10 or data[:2] != b'\x1b\x30':
        return None
    cmd = data[2:6].decode('ascii', errors='replace')
    # Payload sits between the header+zeros and trailing \x00\x00\x0d
    payload = data[10:-3].decode('ascii', errors='replace').strip()
    return cmd, payload


# ── Transport ───────────────────────────────────────────────────────────────

class WiFiTransport:
    """TCP transport to printer WiFi AP (192.168.4.1).

    The printer uses two ports:
      - 9100: read-only queries (GPD, GDS, GFV, GDE, GPS, GFC, GSS)
      - 9200: write/action commands (SDC, SFC, DPC, SPD, DSP)
    """

    PORT_QUERY = 9100
    PORT_WRITE = 9200

    def __init__(self, host="192.168.4.1"):
        self.host = host
        self.query_sock = None
        self.write_sock = None

    def connect(self):
        log.info(f"Connecting to {self.host} (query:{self.PORT_QUERY}, write:{self.PORT_WRITE})...")
        self.query_sock = self._open(self.PORT_QUERY)
        self.write_sock = self._open(self.PORT_WRITE)
        log.info("Connected")

    def _open(self, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((self.host, port))
        return sock

    def close(self):
        for sock in (self.query_sock, self.write_sock):
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                sock.close()
        self.query_sock = None
        self.write_sock = None

    def _is_write_cmd(self, data):
        """Determine if a command should go to the write port."""
        if len(data) < 7:
            return False
        # Commands after the header (\x1b\x30\x31): mode byte + 3-char command
        mode = data[3:4]
        cmd = data[4:7]
        # Action commands (! mode) always go to write port
        if mode == b'!':
            return True
        # Set commands (~ mode but S** prefix) go to write port
        if mode == b'~' and cmd.startswith(b'S'):
            return True
        return False

    def send_recv(self, data, timeout=3):
        """Send data to the appropriate port and receive response."""
        sock = self.write_sock if self._is_write_cmd(data) else self.query_sock
        sock.sendall(data)
        sock.settimeout(timeout)
        try:
            return sock.recv(4096)
        except socket.timeout:
            return b""


# ── Printer ─────────────────────────────────────────────────────────────────

class Printer:
    """High-level printer interface, transport-agnostic."""

    def __init__(self, transport):
        self.transport = transport

    def connect(self):
        self.transport.connect()

    def close(self):
        self.transport.close()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def command(self, mode, cmd, params="", timeout=3):
        """Send a command and return parsed (cmd, payload) or None."""
        pkt = build_cmd(mode, cmd, params)
        resp = self.transport.send_recv(pkt, timeout=timeout)
        return parse_response(resp)

    # ── Queries ──────────────────────────────────────────────────────────

    def get_status(self):
        """GPS — printer state. 0=idle, 1=printing, etc."""
        r = self.command('~', 'GPS')
        return r[1] if r else None

    def get_serial(self):
        """GDS — device serial number."""
        r = self.command('~', 'GDS')
        return r[1] if r else None

    def get_firmware(self):
        """GFV — firmware version string."""
        r = self.command('~', 'GFV')
        return r[1] if r else None

    def get_battery(self):
        """GDE — returns (model, battery_percent)."""
        r = self.command('~', 'GDE')
        if r and r[1]:
            parts = r[1].split()
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
        return None

    def get_idle(self):
        """GPD — printer present/idle. 1=connected."""
        r = self.command('~', 'GPD')
        return r[1] if r else None

    def get_sensor(self):
        """GSS — sensor state."""
        r = self.command('~', 'GSS')
        return r[1] if r else None

    def get_sleep_time(self):
        """GFC 4 — get sleep timeout. Returns minutes."""
        r = self.command('~', 'GFC', '4')
        if r and r[1]:
            parts = r[1].split()
            if len(parts) == 2:
                val = int(parts[1])
                return SLEEP_VALUES.get(val, val)
        return None

    # ── Settings ─────────────────────────────────────────────────────────

    def set_sleep_time(self, minutes):
        """SFC 4 <val> — set sleep timeout in minutes.

        Valid: 1, 2, 5, 10, 30, 60, 120, 240
        """
        val = SLEEP_MINUTES_TO_VALUE.get(minutes)
        if val is None:
            raise ValueError(
                f"Invalid sleep time {minutes}. "
                f"Valid: {sorted(SLEEP_MINUTES_TO_VALUE.keys())}")
        r = self.command('~', 'SFC', f'4 {val}')
        return r is not None

    def set_print_config(self, color=1, direction=0, paper=0, density=1):
        """SDC — set print config before printing.

        color: 0=BW, 1=YMC
        direction: 0=forward
        paper: 0=normal
        density: 1=normal
        """
        r = self.command('~', 'SDC', f'{color} {direction} {paper} {density}')
        return r is not None

    # ── Actions ──────────────────────────────────────────────────────────

    def paper_feed_in(self):
        """DPC 0 — feed paper in."""
        r = self.command('!', 'DPC', '0', timeout=10)
        return r is not None

    def paper_feed_out(self):
        """DPC 1 — feed paper out."""
        r = self.command('!', 'DPC', '1', timeout=10)
        return r is not None

    def clean_cartridge(self, mode=0, intensity=2000, channels=3):
        """DSP — clean print head.

        mode: 0=standard
        intensity: 2000=default from iOS app
        channels: 3=all (CMY)
        """
        r = self.command('!', 'DSP', f'{mode} {intensity} {channels}', timeout=10)
        return r is not None

    def send_spd(self, spd_packet, timeout=10):
        """Send a raw SPD packet and wait for ACK."""
        resp = self.transport.send_recv(spd_packet, timeout=timeout)
        return parse_response(resp) is not None

    def print_packets(self, spd_packets, color=1, density=1):
        """Execute a full print job from pre-encoded SPD packets.

        Follows the iOS app protocol:
        GPD → GDS → SDC → DPC 0 → SPD × N → DPC 1
        """
        log.info("Checking printer...")
        idle = self.get_idle()
        if idle != '1':
            log.error(f"Printer not ready (GPD={idle})")
            return False

        serial = self.get_serial()
        log.info(f"Printer serial: {serial}")

        log.info("Setting print config...")
        if not self.set_print_config(color=color, density=density):
            log.error("SDC failed")
            return False

        log.info("Feeding paper in...")
        if not self.paper_feed_in():
            log.error("DPC 0 failed")
            return False

        log.info(f"Sending {len(spd_packets)} passes...")
        for i, pkt in enumerate(spd_packets):
            log.info(f"  Pass {i+1}/{len(spd_packets)} ({len(pkt)} bytes)")
            if not self.send_spd(pkt):
                log.error(f"SPD pass {i+1} failed")
                return False

        log.info("Feeding paper out...")
        if not self.paper_feed_out():
            log.error("DPC 1 failed")
            return False

        log.info("Print complete!")
        return True


# ── CLI ─────────────────────────────────────────────────────────────────────

def cmd_status(printer, args):
    print(f"  Status:    {printer.get_status()}")
    print(f"  Idle:      {printer.get_idle()}")
    serial = printer.get_serial()
    print(f"  Serial:    {serial}")
    print(f"  Firmware:  {printer.get_firmware()}")
    bat = printer.get_battery()
    if bat:
        print(f"  Battery:   {bat[1]}%")
    print(f"  Sensor:    {printer.get_sensor()}")
    print(f"  Sleep:     {printer.get_sleep_time()} min")


def cmd_sleep(printer, args):
    if args.minutes is None:
        current = printer.get_sleep_time()
        print(f"  Current sleep time: {current} min")
        print(f"  Valid values: 1, 2, 5, 10, 30, 60, 120, 240")
    else:
        printer.set_sleep_time(args.minutes)
        print(f"  Sleep time set to {args.minutes} min")


def cmd_clean(printer, args):
    print("  Cleaning cartridge...")
    printer.clean_cartridge()
    print("  Done")


def cmd_paper(printer, args):
    if args.direction == 'in':
        print("  Feeding paper in...")
        printer.paper_feed_in()
    elif args.direction == 'out':
        print("  Feeding paper out...")
        printer.paper_feed_out()
    else:
        print("  Feeding paper in...")
        printer.paper_feed_in()
        time.sleep(1)
        print("  Feeding paper out...")
        printer.paper_feed_out()
    print("  Done")


def cmd_print(printer, args):
    from encoder import encode_image, wrap_print_job
    from PIL import Image
    import os

    image_path = args.image
    print(f"  Loading: {image_path}")

    # Import the full color pipeline from print_image
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from print_image import process_image, generate_packets, build_cmd as pi_build_cmd

    c_pix, m_pix, y_pix, width, height = process_image(image_path)
    print(f"  Encoding {width}x{height}...")
    packets = generate_packets(c_pix, m_pix, y_pix, width, height)

    # Separate SPD packets from control commands (generate_packets includes GDS/SDC/DPC)
    spd_packets = [p for p in packets if b'!SPD' in p[:20]]
    print(f"  {len(spd_packets)} passes")

    printer.print_packets(spd_packets)


def main():
    parser = argparse.ArgumentParser(description="Newyes LD0806 Printer")
    parser.add_argument("--host", default="192.168.4.1")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Query printer status")

    p = sub.add_parser("sleep", help="Get/set sleep timeout")
    p.add_argument("minutes", type=int, nargs="?",
                   choices=[1, 2, 5, 10, 30, 60, 120, 240])

    sub.add_parser("clean", help="Clean print head")

    p = sub.add_parser("paper", help="Feed paper in/out")
    p.add_argument("direction", nargs="?", choices=["in", "out"],
                   help="Direction (omit for in+out cycle)")

    p = sub.add_parser("print", help="Print an image")
    p.add_argument("image", help="Image file path")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")

    transport = WiFiTransport(args.host)
    with Printer(transport) as p:
        {"status": cmd_status,
         "sleep": cmd_sleep,
         "clean": cmd_clean,
         "paper": cmd_paper,
         "print": cmd_print,
         }[args.command](p, args)


if __name__ == "__main__":
    main()
