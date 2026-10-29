"""Download, port discovery and controlled ESP32 flashing helpers."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import httpx
from serial.tools import list_ports

from .core import (
    SITE_ORIGIN,
    FlashPlan,
    ShlepaFlashError,
    get_download_for_device,
    plan_esp_image,
    resolve_download_url,
    safe_filename,
)

APP_ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ShlepaFlash"
DOWNLOAD_DIR = APP_ROOT / "downloads"
USER_AGENT = "ShlepaFlash-Desktop/1.0.2 (+https://sflasher.bot.nu)"


def list_serial_ports() -> list[dict[str, str]]:
    results = []
    for port in list_ports.comports():
        description = (port.description or "Последовательный порт").strip()
        hwid = (port.hwid or "").strip()
        results.append({"device": port.device, "description": description, "hwid": hwid})
    return sorted(results, key=lambda x: (x["device"].casefold(), x["description"].casefold()))


def artifact_for_device(firmware: dict, device_id: str) -> dict:
    download = get_download_for_device(firmware, device_id)
    if not download:
        raise ShlepaFlashError("Для этой прошивки нет файла, опубликованного для выбранной модели.")
    return download


def artifact_extension(download: dict) -> str:
    fmt = str(download.get("format") or "bin").lower().lstrip(".")
    allowed = {"bin", "tgz", "gz", "zip", "dfu", "fuf", "tar", "tar.gz", "uf2"}
    if fmt not in allowed:
        raise ShlepaFlashError(f"Формат {fmt!r} не поддерживается клиентом. Файл не будет запускаться автоматически.")
    return ".tar.gz" if fmt == "tar.gz" else "." + fmt


async def download_artifact(firmware: dict, device: dict, download: dict, progress: Callable[[int, int | None], None] | None = None) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    url = resolve_download_url(str(download.get("downloadPath") or ""), SITE_ORIGIN)
    parsed = urlparse(url)
    if parsed.hostname != urlparse(SITE_ORIGIN).hostname or parsed.scheme != "https":
        raise ShlepaFlashError("Скачивание разрешено только с HTTPS-домена ShlepaFlash.")
    ext = artifact_extension(download)
    stem = safe_filename(f"{firmware.get('name','firmware')}-{firmware.get('version','latest')}-{device.get('id','device')}")
    destination = DOWNLOAD_DIR / (stem + ext)
    temp = destination.with_suffix(destination.suffix + ".part")
    try:
        manifest_size = int(download.get("size") or 0) or None
    except (TypeError, ValueError):
        manifest_size = None
    received = 0
    total: int | None = manifest_size
    header_size_value: int | None = None
    timeout = httpx.Timeout(60.0, connect=15.0, read=60.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                final_url = urlparse(str(response.url))
                if final_url.scheme != "https" or final_url.hostname != parsed.hostname:
                    raise ShlepaFlashError("Сервер перенаправил скачивание на другой домен. Файл не сохранён.")
                header_size = response.headers.get("content-length")
                if header_size and header_size.isdigit():
                    header_size_value = int(header_size)
                    total = header_size_value
                    if manifest_size and manifest_size != header_size_value:
                        raise ShlepaFlashError(
                            f"Размер в каталоге ({manifest_size} байт) не совпадает с HTTP ({header_size_value} байт). "
                            "Файл не будет использоваться; проверьте публикацию прошивки в админке."
                        )
                with temp.open("wb") as output:
                    async for chunk in response.aiter_bytes(128 * 1024):
                        if not chunk:
                            continue
                        received += len(chunk)
                        if received > 1024 * 1024 * 1024:
                            raise ShlepaFlashError("Файл превысил лимит 1 ГБ. Скачивание прекращено.")
                        output.write(chunk)
                        if progress:
                            progress(received, total)
        if received < 256:
            raise ShlepaFlashError("Сайт вернул пустой или слишком маленький файл. Прошивка не запускалась.")
        if header_size_value and received != header_size_value:
            raise ShlepaFlashError(f"Размер HTTP-файла не совпал: ожидалось {header_size_value} байт, получено {received}. Файл удалён.")
        if manifest_size and received != manifest_size:
            raise ShlepaFlashError(f"Размер не совпал с каталогом: ожидалось {manifest_size} байт, получено {received}. Файл удалён.")
        temp.replace(destination)
        return destination
    except asyncio.CancelledError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def validate_bin_for_device(path: Path, device_id: str) -> FlashPlan:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ShlepaFlashError(f"Не удалось прочитать скачанный файл: {exc}") from exc
    return plan_esp_image(data, device_id)


def find_qflipper() -> Path | None:
    if os.name != "nt":
        return None
    candidates = []
    for env_name in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if base:
            candidates.extend([
                Path(base) / "Programs" / "qFlipper" / "qFlipper.exe",
                Path(base) / "qFlipper" / "qFlipper.exe",
                Path(base) / "Flipper Devices" / "qFlipper" / "qFlipper.exe",
            ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which("qFlipper") or shutil.which("qflipper")
    return Path(found) if found else None


def build_esptool_command(plan: FlashPlan, port: str, image_path: Path) -> list[str]:
    """Build a shell-free esptool command. Global reset options precede write-flash."""
    if not port or not re.fullmatch(r"COM\d+", port, flags=re.IGNORECASE):
        raise ShlepaFlashError("Выберите действительный COM-порт перед прошивкой.")
    chip_arguments = {"ESP32": "esp32", "ESP32-S3": "esp32s3"}
    chip_arg = chip_arguments.get(plan.chip_name)
    if not chip_arg:
        raise ShlepaFlashError(f"Прошивальщик не поддерживает чип {plan.chip_name!r}.")
    return [
        sys.executable, "-m", "esptool",
        "--chip", chip_arg,
        "--port", port,
        "--baud", "460800",
        "--before", "default-reset",
        "--after", "hard-reset",
        "write-flash",
        "--flash-mode", "dio",
        "--flash-freq", "40m",
        "--flash-size", "detect",
        hex(plan.address),
        str(image_path),
    ]


async def run_esptool(
    plan: FlashPlan,
    port: str,
    image_path: Path,
    on_output: Callable[[str], None] | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> tuple[int, str]:
    """Run official esptool via the installed Python environment; never uses shell=True."""
    if not image_path.is_file() or image_path.stat().st_size < 1024:
        raise ShlepaFlashError("BIN-файл отсутствует или слишком мал; прошивка не начиналась.")
    try:
        source = image_path.read_bytes()
        current_plan = plan_esp_image(source, plan.model_id)
    except OSError as exc:
        raise ShlepaFlashError(f"Не удалось проверить BIN перед записью: {exc}") from exc
    if (current_plan.chip_name, current_plan.address, current_plan.data_offset, current_plan.payload) != (plan.chip_name, plan.address, plan.data_offset, plan.payload):
        raise ShlepaFlashError("BIN изменился после проверки. Повторно скачайте и проверьте файл; запись не начиналась.")

    # WebFlasher rules trim a 0x1000 prefix before writing at that same offset.
    # Passing the original file with address 0x1000 would shift the actual image twice.
    prepared_path: Path | None = None
    flash_path = image_path
    if plan.data_offset > 0:
        fd, generated = tempfile.mkstemp(prefix="shlepaflash-prepared-", suffix=".bin", dir=str(image_path.parent))
        os.close(fd)
        prepared_path = Path(generated)
        prepared_path.write_bytes(plan.payload)
        flash_path = prepared_path
    command = build_esptool_command(plan, port, flash_path)
    if on_output:
        on_output("Запускаю esptool без shell-команд…")
        on_output("Порт: " + port + " · Чип: " + plan.chip_name + " · Адрес: " + hex(plan.address))
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(image_path.parent),
        )
        output = bytearray()
        buffer = ""
        while True:
            chunk = await proc.stdout.read(1024) if proc.stdout else b""
            if not chunk:
                break
            output.extend(chunk)
            buffer += chunk.decode("utf-8", errors="replace")
            # esptool updates progress with both CR and LF depending on platform/version.
            while "\n" in buffer or "\r" in buffer:
                indices = [i for i in (buffer.find("\n"), buffer.find("\r")) if i >= 0]
                at = min(indices)
                line, buffer = buffer[:at].strip(), buffer[at + 1:]
                if line and on_output:
                    on_output(line[-400:])
                match = re.search(r"\(\s*(\d{1,3})\s*%\)|\b(\d{1,3})%", line)
                if match and on_progress:
                    number = int(next(g for g in match.groups() if g is not None))
                    on_progress(max(0, min(100, number)))
        if buffer.strip() and on_output:
            on_output(buffer.strip()[-400:])
        code = await proc.wait()
        final_output = output.decode("utf-8", errors="replace")
        if code == 0 and on_progress:
            on_progress(100)
        return code, final_output
    finally:
        if prepared_path:
            try:
                prepared_path.unlink(missing_ok=True)
            except OSError:
                pass
