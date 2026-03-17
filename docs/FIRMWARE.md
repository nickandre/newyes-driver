# Firmware Notes

## Current Firmware

```
Version: VER-1B20250918H1
Date:    September 18, 2025
Model:   1B (likely internal model designator)
```

Queried via `~GFV` command over USB.

## OTA Update Mechanism

The Android app (`iSmart Printer`) has a built-in OTA firmware update system.

### Protocol

- Command: `+UDS` (Update Device Software) via `Protocol_HardwareOTA(type, size, data, output)`
- The `+` mode byte distinguishes OTA from regular commands (`~` query, `!` action)
- `SetCommand.OtaParams(type, path)` — type selects update mode, path is local file path
- `DMD_OTA(type, data, size)` orchestrates the update at the DeviceManager level

### Firmware File

- Filename: `Print_Mini.bin`
- Loaded via Flutter `rootBundle.load()` — intended to be a bundled asset
- **Not present** in the current APK build (may only ship in OTA-specific builds)
- `_processOTA()` reads the .bin, logs `"bin文件大小: "` (file size), splits into chunks, sends via `+UDS`

### Cloud Infrastructure

The app uses **Tencent Cloud Object Storage (COS)** via `tencentcloud_cos_sdk_plugin`:
- AppID: `1258478321`
- API Gateway: `http://service-9jiloqcv-1258478321.sh.apigw.tencentcs.com/release/fileConvert`
- Document conversion: `https://ap-shanghai.cloudmarket-apigw.com/service-gsefnc5p/v2/convert_async`
- Backend callback: `https://www.rootoo.net/callback`
- Third-party API: `https://api.duhuitech.com/q?token=`

Bucket name and region are configured at runtime (not hardcoded). Common patterns probed (`firmware-1258478321.cos.ap-shanghai.myqcloud.com`, etc.) returned 404.

### Windows Driver

The Windows `PrinterTool.exe` / `sPrinter V1.12` has **no OTA capability**. Firmware updates are Android-app-only.

## Firmware Extraction — Not Currently Possible

### What we can't do
- **Read firmware from the device**: No read-back command exists in the protocol. The `+UDS` command is write-only.
- **Find firmware in the APK**: `Print_Mini.bin` is not bundled in the current APK build.
- **Download from Tencent COS**: Bucket credentials are set at runtime, common bucket names returned 404.
- **Extract via USB on macOS**: Bulk transfers don't work (see `USB_BUG_REPORT.md`).

### What could work (future)
1. **Intercept OTA update over USB on Windows**: Trigger a firmware update from the Android app while capturing USB traffic with Wireshark. The `+UDS` packets would contain the firmware binary in chunks. Reconstruct from the capture.
2. **MITM the Android app**: Proxy the Tencent COS download through mitmproxy to capture the .bin file before it's sent to the printer.
3. **Decompile a newer APK**: Future APK versions may bundle `Print_Mini.bin` as an asset. Extract directly from the APK.
4. **JTAG/SWD**: Physical debug port on the printer's MCU (requires opening the case, identifying the chip, and soldering).
5. **Contact Newyes**: Request firmware file directly (`newyes@newyes.com`).

## What We'd Do With the Firmware

1. **Identify the MCU**: Instruction set, flash layout, peripheral map
2. **Fix USB Printer Class bugs**: Correct the malformed `GET_DEVICE_ID` length field, implement proper `GET_PORT_STATUS`
3. **Fix WiFi write commands**: Investigate why SDC/DPC/SPD are silently dropped over WiFi TCP
4. **Add standards compliance**: Proper USB Printer Class + possibly IPP/AirPrint support
5. **Understand the nozzle map**: The firmware's print engine reveals how `!SPD` data maps to physical nozzles — this would let us build the image encoder without the proprietary SDK
