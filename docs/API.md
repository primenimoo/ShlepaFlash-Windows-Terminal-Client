# ShlepaFlash Desktop API integration

Origin: `https://sflasher.bot.nu`

1. `GET /api?action=desktop-health` — health and API version.
2. `GET /api?action=desktop-manifest` — devices, firmware metadata, compatible download records, file format and expected size.
3. `GET /api?action=list` — public firmware items including the data-URL `logo` field already used by the website. This route is used only to display admin-uploaded logos in the terminal UI.
4. `GET /api?action=download&id=<firmware-id>&model=<device-id>` — file. The client validates same-origin HTTPS, HTTP status, consistency between the manifest size and HTTP Content-Length, and the device image header before flashing. ESP32 images are written using esptool; Flipper Zero packages are handed off to official qFlipper and are never sent to esptool.

Known device IDs in the current manifest: `plus2`, `plus`, `s3`, `flipper`, `tembed`. Never choose a firmware by its name alone; match the `downloads[].deviceId` value to the selected ID.

The client performs read-only calls only. It doesn't need an API key and must not gain write/admin endpoints as part of this client.
