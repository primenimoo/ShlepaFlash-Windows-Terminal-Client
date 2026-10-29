
"""Read-only client for the public ShlepaFlash website API."""
from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
from typing import Any

import httpx

from .core import (
    SITE_ORIGIN,
    HEALTH_PATH,
    MANIFEST_PATH,
    PUBLIC_LIST_PATH,
    ShlepaFlashError,
    validate_manifest,
)

APP_ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ShlepaFlash"
CACHE_FILE = APP_ROOT / "cache" / "catalog.json"
USER_AGENT = "ShlepaFlash-Desktop/1.0.1 (+https://sflasher.bot.nu)"


def _get_json(client: httpx.Client, url: str) -> dict[str, Any]:
    response = client.get(url)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise ShlepaFlashError(f"Сайт вернул не JSON: {url}") from exc
    if not isinstance(data, dict):
        raise ShlepaFlashError(f"Сайт вернул неожиданный тип данных: {url}")
    return data


def logos_from_public_list(payload: object) -> dict[str, str]:
    """Extract the same data-URL logos that admins added to the website."""
    result: dict[str, str] = {}
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return result
    for item in payload["items"]:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        logo = item.get("logo")
        if isinstance(logo, str) and logo.startswith("data:image/") and len(logo) <= 400_000:
            result[str(item["id"])] = logo
    return result


def _write_cache(
    manifest: dict,
    logos: dict[str, str],
    health: dict | None,
    warning: str = "",
) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = CACHE_FILE.with_suffix(".tmp")
        temp.write_text(
            json.dumps(
                {
                    "manifest": manifest,
                    "logos": logos,
                    "health": health,
                    "warning": warning,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temp.replace(CACHE_FILE)
    except OSError:
        pass


def load_cached_catalog() -> dict | None:
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        manifest = validate_manifest(data.get("manifest"))
        logos = (
            data.get("logos", {})
            if isinstance(data.get("logos", {}), dict)
            else {}
        )
        return {
            "manifest": manifest,
            "logos": logos,
            "health": data.get("health"),
            "source": "cache",
            "warning": (
                "Нет соединения с сайтом. Показан последний сохранённый каталог; "
                "скачивание требует интернета."
            ),
        }
    except (OSError, ValueError, TypeError, ShlepaFlashError):
        return None


def fetch_catalog_sync() -> dict:
    """Load live manifest and firmware logos; fall back to last good cache."""
    health: dict | None = None
    logos: dict[str, str] = {}
    warning = ""
    timeout = httpx.Timeout(15.0, connect=8.0)
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        ) as client:
            try:
                health = _get_json(client, SITE_ORIGIN + HEALTH_PATH)
                if health.get("ok") is not True:
                    warning = (
                        "API доступен, но health-проверка вернула предупреждение."
                    )
            except (httpx.HTTPError, ShlepaFlashError) as exc:
                warning = f"Health API недоступен: {exc}"

            manifest = validate_manifest(
                _get_json(client, SITE_ORIGIN + MANIFEST_PATH)
            )

            try:
                list_payload = _get_json(client, SITE_ORIGIN + PUBLIC_LIST_PATH)
                logos = logos_from_public_list(list_payload)
            except (httpx.HTTPError, ShlepaFlashError) as exc:
                warning = (
                    (warning + " · " if warning else "")
                    + f"Логотипы прошивок не загрузились: {exc}"
                )

            result = {
                "manifest": manifest,
                "logos": logos,
                "health": health,
                "source": "online",
                "warning": warning,
            }
            _write_cache(manifest, logos, health, warning)
            return result
    except (httpx.HTTPError, ShlepaFlashError) as exc:
        cached = load_cached_catalog()
        if cached:
            cached["warning"] = (
                cached.get("warning", "") + f" Причина: {exc}"
            ).strip()
            return cached
        if isinstance(exc, ShlepaFlashError):
            raise
        raise ShlepaFlashError(
            f"Не удалось подключиться к каталогу {SITE_ORIGIN}. "
            f"Проверь интернет и повтори загрузку.\n{exc}"
        ) from exc


def decode_logo_data_url(value: str) -> bytes | None:
    """Decode a bounded base64 image data URL without accessing arbitrary URLs."""
    if (
        not isinstance(value, str)
        or not value.startswith("data:image/")
        or len(value) > 400_000
    ):
        return None
    try:
        header, encoded = value.split(",", 1)
        if ";base64" not in header.lower():
            return None
        blob = base64.b64decode(encoded, validate=True)
        if not blob or len(blob) > 300_000:
            return None
        return blob
    except (ValueError, binascii.Error):
        return None
