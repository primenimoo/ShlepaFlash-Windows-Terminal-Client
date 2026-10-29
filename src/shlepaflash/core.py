"""Pure logic shared by the TUI and tests. No hardware or network side effects."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import re

SITE_ORIGIN = "https://sflasher.bot.nu"
HEALTH_PATH = "/api?action=desktop-health"
MANIFEST_PATH = "/api?action=desktop-manifest"
PUBLIC_LIST_PATH = "/api?action=list"

DEVICE_CHIPS = {
    "plus2": "ESP32",
    "plus": "ESP32",
    "s3": "ESP32-S3",
    "tembed": "ESP32-S3",
}
CHIP_NAMES = {
    0: "ESP32",
    2: "ESP32-S2",
    5: "ESP32-C3",
    9: "ESP32-S3",
    12: "ESP32-C6",
    13: "ESP32-H2",
    16: "ESP32-C2",
    18: "ESP32-P4",
}
PARTITION_MAGIC = b"\xAA\x50"
DEFAULT_APP_OFFSET = 0x10000


class ShlepaFlashError(Exception):
    """A user-facing, actionable application error."""


@dataclass(frozen=True)
class FlashPlan:
    model_id: str
    chip_name: str
    address: int
    data_offset: int
    payload: bytes
    app_only: bool
    warnings: tuple[str, ...] = ()


def validate_manifest(manifest: object) -> dict:
    if not isinstance(manifest, dict):
        raise ShlepaFlashError("Сайт вернул неожиданный формат каталога.")
    if manifest.get("product") != "ShlepaFlash":
        raise ShlepaFlashError("Ответ получен не от каталога ShlepaFlash.")
    try:
        manifest_version = int(manifest.get("manifestVersion") or 0)
    except (TypeError, ValueError) as exc:
        raise ShlepaFlashError("Версия API каталога указана некорректно.") from exc
    if manifest_version < 1:
        raise ShlepaFlashError("Версия API каталога не поддерживается.")
    devices = manifest.get("devices")
    firmware = manifest.get("firmware")
    if not isinstance(devices, list) or not isinstance(firmware, list):
        raise ShlepaFlashError("В каталоге отсутствует список устройств или прошивок.")
    device_ids = set()
    for device in devices:
        if not isinstance(device, dict) or not device.get("id") or not device.get("name"):
            raise ShlepaFlashError("В каталоге есть запись устройства без ID или названия.")
        device_ids.add(str(device["id"]))
    for item in firmware:
        if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
            raise ShlepaFlashError("В каталоге есть запись прошивки без ID или названия.")
        downloads = item.get("downloads", [])
        if downloads is not None and not isinstance(downloads, list):
            raise ShlepaFlashError(f"Некорректный список файлов у прошивки {item.get('name')}.")
        for artifact in downloads or []:
            if not isinstance(artifact, dict):
                raise ShlepaFlashError(f"Некорректная ссылка на файл у {item.get('name')}.")
            if artifact.get("deviceId") and str(artifact["deviceId"]) not in device_ids:
                raise ShlepaFlashError(f"Прошивка {item.get('name')} ссылается на неизвестную модель.")
    return manifest


def get_download_for_device(firmware: dict, device_id: str) -> dict | None:
    downloads = firmware.get("downloads")
    if not isinstance(downloads, list):
        return None
    for record in downloads:
        if isinstance(record, dict) and str(record.get("deviceId")) == device_id and record.get("downloadPath"):
            return record
    return None


def resolve_download_url(download_path: str, site_origin: str = SITE_ORIGIN) -> str:
    """Resolve a first-party download path; reject arbitrary origins."""
    if not isinstance(download_path, str) or not download_path.strip():
        raise ShlepaFlashError("У выбранной прошивки отсутствует ссылка на файл.")
    origin = urlparse(site_origin)
    candidate = urlparse(download_path)
    if candidate.scheme or candidate.netloc:
        if candidate.scheme != "https" or candidate.hostname != origin.hostname:
            raise ShlepaFlashError("Каталог содержит ссылку за пределами сайта ShlepaFlash. Скачивание отменено.")
        if candidate.username is not None or candidate.password is not None:
            raise ShlepaFlashError("Ссылка содержит данные авторизации. Скачивание отменено.")
        try:
            port = candidate.port
        except ValueError as exc:
            raise ShlepaFlashError("Каталог содержит некорректный порт в ссылке.") from exc
        if port not in (None, 443):
            raise ShlepaFlashError("Каталог содержит нестандартный порт в ссылке. Скачивание отменено.")
        return download_path
    if not download_path.startswith("/") or download_path.startswith("//"):
        raise ShlepaFlashError("Ссылка на файл должна быть относительным URL сайта.")
    return f"{site_origin.rstrip('/')}{download_path}"


def safe_filename(value: str, default: str = "firmware") -> str:
    value = Path(str(value or "")).name
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    if not value:
        value = default
    return value[:100]


def plan_esp_image(image: bytes, model_id: str) -> FlashPlan:
    """Validate image magic/chip ID and calculate an address using website rules.

    Full images with a partition table are written from their image offset. App-only
    images are written at 0x10000, leaving an existing bootloader/table untouched.
    No serial port is opened by this function.
    """
    if model_id not in DEVICE_CHIPS:
        raise ShlepaFlashError("Выбранная модель не прошивается через esptool. Для Flipper Zero используйте qFlipper.")
    if not isinstance(image, (bytes, bytearray)) or len(image) < 1024:
        raise ShlepaFlashError("BIN слишком маленький или не удалось его прочитать. Прошивка не запускалась.")
    image = bytes(image)
    if image[:1] == b"\xE9":
        offset = 0
    elif len(image) > 0x1000 and image[0x1000:0x1001] == b"\xE9":
        offset = 0x1000
    else:
        first = image[:16].hex(" ")
        if image.startswith(b"PK\x03\x04"):
            detail = "Похоже, скачан ZIP-архив, а не BIN-файл."
        elif image.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            detail = "Вместо BIN получена HTML-страница."
        else:
            detail = f"Не найден заголовок ESP-образа 0xE9 (первые байты: {first})."
        raise ShlepaFlashError(f"{detail} Файл не записывался.")
    if len(image) < offset + 14:
        raise ShlepaFlashError("BIN повреждён: заголовок ESP-образа неполный.")
    chip_id = image[offset + 12] | (image[offset + 13] << 8)
    chip_name = CHIP_NAMES.get(chip_id)
    if not chip_name:
        raise ShlepaFlashError(f"BIN содержит неподдерживаемый chip ID {chip_id}; запись отменена.")
    expected = DEVICE_CHIPS[model_id]
    if chip_name != expected:
        raise ShlepaFlashError(
            f"Несовместимая прошивка: файл собран для {chip_name}, а выбрано устройство {expected}. "
            "Ничего не записано. Выберите правильную модель и BIN."
        )
    has_partition = any(
        len(image) > addr + 1 and image[addr:addr + 2] == PARTITION_MAGIC
        for addr in (0x8000, 0x9000)
    )
    app_only = not has_partition
    address = DEFAULT_APP_OFFSET if app_only else offset
    payload = image[offset:]
    warnings: tuple[str, ...] = ()
    if app_only:
        warnings = (
            "В файле не найдена таблица разделов. Это может быть образ только приложения; "
            "он будет записан по 0x10000 и требует уже установленного совместимого загрузчика.",
        )
    return FlashPlan(model_id, chip_name, address, offset, payload, app_only, warnings)
