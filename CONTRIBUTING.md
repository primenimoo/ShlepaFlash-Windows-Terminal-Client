# Contributing

- Keep firmware compatibility tied to manifest `downloads[].deviceId`, not to names guessed from descriptions.
- Add or update unit tests when changing image validation, API parsing, and download handling.
- Never invoke external commands with `shell=True`; pass arguments as an argv list.
- Never automatically erase all device flash. Any future destructive feature must have a separate explicit confirmation.
- Keep the public client read-only and free of site/admin secrets.
- Run `python -m compileall -q src tests` and `python -m pytest -q` before submitting changes.
