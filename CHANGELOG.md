
# Changelog

## 1.0.1

- Fix startup failure on older supported Textual releases by removing redundant `text-wrap: wrap` declarations; normal wrapping is Textual default.
- Add a regression test to prevent unsupported stylesheet declarations from returning.

## 1.0.0

- Initial Windows terminal client for the live ShlepaFlash read-only API.
- Keyboard and mouse navigation, animated loading/status UI, white/orange ASCII wordmark.
- Device catalogue and firmware descriptions are loaded from the site.
- Firmware logos are read from the site's public list endpoint and rendered as terminal colour blocks; invalid/missing logos fall back to initials.
- HTTPS downloads with size checks, first-party URL enforcement, file cleanup on cancellation/error, and ESP32/ESP32-S3 image preflight.
- COM port discovery and 5-second refresh; confirmed ESP32 flashing via esptool.
- Flipper Zero package hand-off to official qFlipper; it is not incorrectly sent to esptool.
- PowerShell installer/uninstaller and Windows GitHub Actions tests.
