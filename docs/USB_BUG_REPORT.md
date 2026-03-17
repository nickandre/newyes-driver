# LD0806 USB Printer Class Issues

**Device**: Newyes LD0806, VID `0x2E88` PID `0x6001`, Firmware `VER-1B20250918H1`

## 1. Malformed GET_DEVICE_ID Response

The IEEE 1284 Device ID length field declares **307 bytes** (`0x0133`) but only **51 bytes** are returned. Wireshark flags this as a malformed packet. This violates USB Printer Class spec Section 3.1.1.

## 2. GET_PORT_STATUS Always Returns 0x00

Port status reports all bits zero (not selected, error) regardless of actual printer state. The printer is online and functioning — the status byte is a hardcoded stub.

## 3. Bulk Transfers Fail on macOS

Bulk OUT writes to EP 0x01 return I/O errors on macOS via libusb. The same bytes succeed on Windows via `CreateFileW`. Likely caused by #1 — macOS's USB printer driver enters an error state from the length mismatch.
