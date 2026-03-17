#!/usr/bin/env python3
"""
Newyes LD0806 — Windows USB Demo

Demonstrates all confirmed working operations over USB on Windows:
- Status queries (GPS, GFV, GDS, GDE, GPD, GSS)
- Paper feed (DPC in/out)
- Motor control (DMC)
- Full print replay from captured packets

Requirements:
    - Windows with Python 3.12+
    - Printer connected via USB and powered on
    - No additional drivers needed (uses CreateFileW on USB printer class interface)

Usage:
    python windows_usb_demo.py status     # Query all status info
    python windows_usb_demo.py paper      # Feed paper in then out
    python windows_usb_demo.py motor      # Test motor control
    python windows_usb_demo.py print      # Replay captured print job
"""

import ctypes
import ctypes.wintypes as wt
import time
import sys
import os

DEVICE_PATH = r'\\?\USB#VID_2E88&PID_6001#Printer_123456#{28d78fad-5a12-11d1-ae5b-0000f803a8c2}'
HEADER = b'\x1b\x30\x31'


def build(mode, cmd, params=""):
    pkt = HEADER + mode.encode() + cmd.encode() + b'\x20'
    if params:
        pkt += params.encode() + b'\x20'
    pkt += b'\x00\x00\x0d'
    return pkt


class USBPrinter:
    def __init__(self):
        self.k32 = ctypes.windll.kernel32
        self.handle = None

    def open(self):
        self.handle = self.k32.CreateFileW(
            DEVICE_PATH, 0xC0000000, 3, None, 3, 0, None)
        if self.handle == -1:
            err = self.k32.GetLastError()
            print(f"Failed to open printer: error {err}")
            print("Is the printer connected via USB and powered on?")
            sys.exit(1)
        print("Printer connected.\n")

    def close(self):
        if self.handle and self.handle != -1:
            self.k32.CloseHandle(self.handle)

    def send_recv(self, data, label="", wait=0.5):
        written = wt.DWORD(0)
        self.k32.WriteFile(self.handle, data, len(data), ctypes.byref(written), None)
        time.sleep(wait)
        buf = ctypes.create_string_buffer(512)
        read_n = wt.DWORD(0)
        self.k32.ReadFile(self.handle, buf, 512, ctypes.byref(read_n), None)
        resp = buf.raw[:read_n.value] if read_n.value > 0 else b""
        if resp and len(resp) > 13:
            payload = resp[10:-3].decode('ascii', errors='replace').strip()
        elif resp:
            payload = "(ack)"
        else:
            payload = "(no response)"
        print(f"  {label}: {payload}")
        self.k32.SetLastError(0)
        return resp


def cmd_status(printer):
    """Query all status information."""
    print("=== Status ===")
    printer.send_recv(build('~', 'GPS'), "State")
    printer.send_recv(build('~', 'GFV'), "Firmware")
    printer.send_recv(build('~', 'GDS'), "Serial")
    printer.send_recv(build('~', 'GDE'), "Battery")
    printer.send_recv(build('~', 'GPD'), "Idle")
    printer.send_recv(build('~', 'GSS'), "Sensor")
    printer.send_recv(build('~', 'GFC', '0'), "Factory Config")


def cmd_paper(printer):
    """Feed paper in then out."""
    print("=== Paper Feed ===")
    printer.send_recv(build('~', 'GSS'), "Sensor before")
    print("\n  Feeding paper IN...")
    printer.send_recv(build('!', 'DPC', '0'), "DPC in", wait=2)
    printer.send_recv(build('~', 'GSS'), "Sensor after in")
    time.sleep(1)
    print("\n  Feeding paper OUT...")
    printer.send_recv(build('!', 'DPC', '1'), "DPC out", wait=2)
    printer.send_recv(build('~', 'GSS'), "Sensor after out")


def cmd_motor(printer):
    """Test motor control. motorType: 0=feeder, 1=carriage, 2=print."""
    print("=== Motor Control ===")
    print("  (motorType, direction, stepCount, runType)\n")
    tests = [
        ("Feeder fwd 50", "0 0 50 0"),
        ("Feeder rev 50", "0 1 50 0"),
        ("Carriage fwd 50", "1 0 50 0"),
        ("Carriage rev 50", "1 1 50 0"),
        ("Print motor fwd 50", "2 0 50 0"),
        ("Print motor rev 50", "2 1 50 0"),
    ]
    for label, params in tests:
        printer.send_recv(build('~', 'DMC', params), label, wait=2)


def cmd_print(printer):
    """Replay captured print job."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, script_dir)
        from captured_packets import PACKETS
    except ImportError:
        print("captured_packets.py not found in same directory")
        sys.exit(1)

    print(f"=== Print ({len(PACKETS)} packets) ===\n")
    written = wt.DWORD(0)
    buf = ctypes.create_string_buffer(512)
    read_n = wt.DWORD(0)
    k32 = printer.k32

    for i, pkt in enumerate(PACKETS):
        if pkt[:3] == HEADER:
            lbl = chr(pkt[3]) + pkt[4:7].decode('ascii', errors='replace')
        else:
            lbl = "RAW"

        k32.WriteFile(printer.handle, pkt, len(pkt), ctypes.byref(written), None)
        wait = 1.0 if len(pkt) > 1000 else 0.3
        time.sleep(wait)
        k32.ReadFile(printer.handle, buf, 512, ctypes.byref(read_n), None)
        ack = "ack" if read_n.value > 0 else "no ack"
        print(f"  [{i+1:2d}/{len(PACKETS)}] {lbl} ({len(pkt):>6d}b) {ack}")
        k32.SetLastError(0)

    print("\nDone!")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    printer = USBPrinter()
    printer.open()

    try:
        {"status": cmd_status,
         "paper": cmd_paper,
         "motor": cmd_motor,
         "print": cmd_print,
        }.get(cmd, cmd_status)(printer)
    finally:
        printer.close()


if __name__ == "__main__":
    main()
