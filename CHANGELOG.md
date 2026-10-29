# Changelog

## 1.0.2 — Compact centered terminal UI
- Replaced the old logo with the supplied 5-row block wordmark; SHLEPA is white and FLASH is orange.
- Simplified the home menu to plain text items with hover/selection highlighting.
- Centered the main content, device/firmware lists, USB selection, and confirmation dialogs.
- Removed visible esptool/download log panes; diagnostic output remains in `logs/client.log`.
- Added shared ASCII-art percentage and progress-track renderers for download and flashing screens.
- Added renderer tests and removed the nested firmware-description scroll area.


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
