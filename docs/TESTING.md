# Test matrix and release checks

## Automated checks

- Python syntax compilation: `python -m compileall -q src tests`.
- Unit tests: `python -m pytest -q`.
- API schema shape and exact model-to-download matching.
- Reject external download origins, traversal-style filenames, HTML/ZIP-as-BIN, unknown chip IDs and wrong-chip images.
- Validate full image/application-only flash address handling based on the website's `flash-utils.js` rules.
- Logo metadata parsing is bounded and falls back gracefully if missing or malformed.
- TUI module import, white/orange wordmark styles, and raster-logo rendering.
- esptool command order (global reset options before `write-flash`) and invalid COM-port rejection.

## Manual release checks on Windows

1. Open Windows Terminal / PowerShell, run `shlepaflash`, and confirm Unicode/colour display.
2. Confirm online catalog and logos load; then test offline start after a successful online catalog has been cached.
3. Use the website to publish a firmware record with a logo; confirm that logo appears in the terminal list. Test a record without a logo too.
4. Connect one known M5Stick board using a data-capable cable; select the matching model and inspect the COM VID/PID.
5. Test bad/mismatched BIN preflight and ensure no serial write starts.
6. Only on a device you own, test a known-good full BIN and/or app-only image intended for that exact model. Keep power/USB stable.
7. On a Flipper Zero, confirm the package downloads and qFlipper opens; complete installation using qFlipper's Install from file command.
8. Test cancel/back/exit and confirm downloaded files and logs remain on disk.

Do not mark a physical flash test as passed unless the actual device has been connected and the flashed firmware has booted successfully.
