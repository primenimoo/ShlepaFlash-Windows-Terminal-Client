# ShlepaFlash Desktop for Windows

Russian-language interactive terminal application for the public ShlepaFlash firmware catalogue. The client downloads firmware from `https://sflasher.bot.nu`, validates ESP32/ESP32-S3 image headers before writing, detects COM ports, and invokes Espressif's `esptool` for compatible `.bin` files. Flipper Zero update packages are handed off to official qFlipper rather than being treated as ESP images.

See **[README_RU.md](README_RU.md)** for setup, workflow, API details, testing, limitations and the one-line Windows installer command.

The app also reads the public `GET /api?action=list` metadata to render firmware logos stored in the website admin panel. If a logo isn't available, a text fallback is used.
