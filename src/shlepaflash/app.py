
"""ShlepaFlash's interactive Windows terminal UI.

The app intentionally uses the public, read-only site API. It never asks for a
site password/API key and never guesses a download path or chip target.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
from rich.style import Style
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Center, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, LoadingIndicator, ProgressBar, RichLog, Static

from . import __version__
from .api import APP_ROOT, decode_logo_data_url, fetch_catalog_sync
from .core import (
    SITE_ORIGIN,
    FlashPlan,
    ShlepaFlashError,
    get_download_for_device,
    plan_esp_image,
    safe_filename,
)
from .flasher import (
    DOWNLOAD_DIR,
    download_artifact,
    find_qflipper,
    list_serial_ports,
    run_esptool,
    validate_bin_for_device,
)

LOG_DIR = APP_ROOT / "logs"
LOG_FILE = LOG_DIR / "client.log"
TIKTOK_URL = "https://www.tiktok.com/@primenimoo"
TELEGRAM_URL = "https://t.me/primenimooo"


def configure_logging() -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=LOG_FILE,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            encoding="utf-8",
        )
    except OSError:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


def make_wordmark() -> Text:
    """ASCII-style logo with the site's white/orange wordmark palette."""
    result = Text()
    result.append("  ________ _   _ _      ______ _____   _    ______  _       _____  ______\n", style="bold #E8EEF5")
    result.append(" |  _____| | | | |    |  ____|  __ \\ | |  |  ____| | |     / ____||  ____|\n", style="bold #E8EEF5")
    result.append(" | |____  | |_| | |    | |__  | |__) || |  | |__    | |    | (___  | |__\n", style="bold #E8EEF5")
    result.append(" |  ____| |  _  | |    |  __| |  ___/ | |  |  __|   | |     \\___ \\ |  __|\n", style="bold #E8EEF5")
    result.append(" | |____  | | | | |____| |____| |     | |__| |____  | |____ ____) || |____\n", style="bold #E8EEF5")
    result.append(" |______| |_| |_|______|______|_|     |_____|______| |______|_____/ |______|\n", style="bold #E8EEF5")
    result.append("  ──────── SHLEPA", style="bold #F4F6F8")
    result.append("FLASH", style="bold #FF9A3D")
    result.append(" ─────────────────────\n", style="bold #526071")
    result.append("  WINDOWS TERMINAL CLIENT  /  DEVICE FIRMWARE CATALOG\n", style="dim #94A3B8")
    return result


def rgb_hex(value: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % value


def image_to_terminal_text(
    image_bytes: bytes | None,
    width: int = 10,
    pixel_height: int = 10,
) -> Text | None:
    """Render an uploaded logo as true-colour half-blocks in supported terminals."""
    if not image_bytes:
        return None
    try:
        with Image.open(io.BytesIO(image_bytes)) as original:
            original.load()
            source = ImageOps.contain(
                original.convert("RGBA"),
                (width, pixel_height),
                Image.Resampling.LANCZOS,
            )
            canvas = Image.new("RGBA", (width, pixel_height), (10, 14, 20, 255))
            left = (width - source.width) // 2
            top = (pixel_height - source.height) // 2
            canvas.alpha_composite(source, (left, top))
            pixels = canvas.convert("RGB")

        result = Text()
        for y in range(0, pixel_height, 2):
            for x in range(width):
                upper = pixels.getpixel((x, y))
                lower = pixels.getpixel((x, min(y + 1, pixel_height - 1)))
                result.append(
                    "▀",
                    style=Style(color=rgb_hex(upper), bgcolor=rgb_hex(lower)),
                )
            if y + 2 < pixel_height:
                result.append("\n")
        return result
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Could not render firmware logo: %s", exc
        )
        return None


def static_device_logo(device_id: str) -> Text:
    path = (
        Path(__file__).resolve().parents[2]
        / "assets"
        / "devices"
        / f"{device_id}.png"
    )
    try:
        return (
            image_to_terminal_text(path.read_bytes(), width=10, pixel_height=10)
            or Text("  ◆  ", style="bold #FF9A3D")
        )
    except OSError:
        return Text("  ◆  ", style="bold #FF9A3D")


class BrandPanel(Static):
    def __init__(self) -> None:
        super().__init__(make_wordmark(), id="brand-panel", markup=False)


class BackScreen(Screen):
    BINDINGS = [("escape", "go_back", "Назад")]

    def action_go_back(self) -> None:
        self.app.pop_screen()


class ConfirmFlashScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Отмена")]

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static("ПЕРЕД ЗАПИСЬЮ", classes="eyebrow")
            yield Static(
                "Вы уверены, что хотите прошить устройство?",
                classes="confirm-title",
            )
            yield Static(
                "Во время записи не отключайте USB-кабель. BIN уже проверен "
                "по заголовку чипа. Несовместимая прошивка не будет записана.",
                classes="muted",
            )
            with Horizontal(classes="button-row"):
                yield Button(
                    "Начать прошивку", id="confirm-yes", variant="primary"
                )
                yield Button("Отмена", id="confirm-no", variant="default")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-yes":
            self.dismiss(True)
        elif event.button.id == "confirm-no":
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ConfirmCancelScreen(ModalScreen[bool]):
    """Confirmation modal for cancelling an active artifact download."""

    BINDINGS = [("escape", "cancel", "Продолжить загрузку")]

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static("ОТМЕНА ЗАГРУЗКИ", classes="eyebrow")
            yield Static(
                "Точно отменить скачивание?", classes="confirm-title"
            )
            yield Static(
                "Незавершённый временный файл будет удалён. Загруженные ранее "
                "файлы останутся на компьютере.",
                classes="muted",
            )
            with Horizontal(classes="button-row"):
                yield Button(
                    "Отменить загрузку",
                    id="download-cancel-yes",
                    variant="primary",
                )
                yield Button(
                    "Продолжить",
                    id="download-cancel-no",
                    variant="default",
                )

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "download-cancel-yes":
            self.dismiss(True)
        elif event.button.id == "download-cancel-no":
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class HomeScreen(Screen):
    BINDINGS = [("escape", "quit_app", "Выход")]
    FRAMES = ("◜", "◠", "◝", "◞", "◡", "◟")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="root", classes="home-root"):
            yield BrandPanel()
            yield Static("Добро пожаловать в ShlepaFlash", id="welcome")
            yield Horizontal(
                Static("◉", id="connection-indicator"),
                Static("Подключение к каталогу сайта…", id="catalog-status"),
                id="catalog-line",
            )
            yield LoadingIndicator(id="startup-loader")
            yield Static("ВЫБЕРИТЕ ДЕЙСТВИЕ", classes="eyebrow")
            yield ListView(
                ListItem(
                    Horizontal(
                        Static("01", classes="menu-num"),
                        Vertical(
                            Label("Прошивка устройства", classes="menu-title"),
                            Label(
                                "Каталог сайта → устройство USB",
                                classes="menu-subtitle",
                            ),
                        ),
                    ),
                    id="home-flash",
                ),
                ListItem(
                    Horizontal(
                        Static("↗", classes="menu-num"),
                        Vertical(
                            Label("Перейти на сайт", classes="menu-title"),
                            Label("sflasher.bot.nu", classes="menu-subtitle"),
                        ),
                    ),
                    id="home-site",
                ),
                ListItem(
                    Horizontal(
                        Static("♪", classes="menu-num"),
                        Vertical(
                            Label("TikTok", classes="menu-title"),
                            Label("@primenimoo", classes="menu-subtitle"),
                        ),
                    ),
                    id="home-tiktok",
                ),
                ListItem(
                    Horizontal(
                        Static("➤", classes="menu-num"),
                        Vertical(
                            Label("Telegram", classes="menu-title"),
                            Label("@primenimooo", classes="menu-subtitle"),
                        ),
                    ),
                    id="home-telegram",
                ),
                ListItem(
                    Horizontal(
                        Static("×", classes="menu-num"),
                        Vertical(
                            Label("Закрыть ShlepaFlash", classes="menu-title"),
                            Label("Esc или Ctrl+Q", classes="menu-subtitle"),
                        ),
                    ),
                    id="home-exit",
                ),
                id="home-menu",
            )
            yield Static(
                "↑ ↓ — выбор     Enter — открыть     Esc — назад     "
                "Ctrl+R — обновить каталог",
                classes="hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        self._spin = 0
        self.set_interval(0.18, self.animate_loader)
        self.run_worker(self.load_catalog(), name="catalog-load", exclusive=True)
        self.call_after_refresh(
            lambda: self.query_one("#home-menu", ListView).focus()
        )

    def animate_loader(self) -> None:
        self._spin += 1
        status = self.query_one("#catalog-status", Static)
        if self.app.catalog_loading:
            frame = self.FRAMES[self._spin % len(self.FRAMES)]
            status.update(
                f"{frame} Подключение к каталогу сайта"
                + "." * ((self._spin % 3) + 1)
            )
        else:
            self.query_one("#startup-loader", LoadingIndicator).display = False

    async def load_catalog(self) -> None:
        self.app.catalog_loading = True
        try:
            result = await asyncio.to_thread(fetch_catalog_sync)
            self.app.manifest = result["manifest"]
            self.app.firmware_logos = result.get("logos", {})
            self.app.catalog_source = result.get("source", "online")
            self.app.catalog_warning = result.get("warning", "")
            count = sum(
                1
                for item in self.app.manifest.get("firmware", [])
                if item.get("available")
            )
            source_label = (
                "онлайн"
                if self.app.catalog_source == "online"
                else "локальный кэш"
            )
            self.query_one("#connection-indicator", Static).update("●")
            self.query_one("#connection-indicator", Static).styles.color = (
                "#46D29A" if source_label == "онлайн" else "#E8A44B"
            )
            self.query_one("#catalog-status", Static).update(
                f"Каталог загружен · {count} доступных прошивок · {source_label}"
            )
            if self.app.catalog_warning:
                self.app.notify(
                    self.app.catalog_warning,
                    title="Каталог ShlepaFlash",
                    severity="warning",
                    timeout=8,
                )
            logging.info(
                "catalog loaded source=%s firmware=%s logos=%s",
                self.app.catalog_source,
                len(self.app.manifest.get("firmware", [])),
                len(self.app.firmware_logos),
            )
        except Exception as exc:
            logging.exception("Catalog loading failed")
            self.query_one("#connection-indicator", Static).update("●")
            self.query_one("#connection-indicator", Static).styles.color = (
                "#EF5350"
            )
            self.query_one("#catalog-status", Static).update(
                "Каталог не загружен · проверьте подключение"
            )
            self.app.notify(
                str(exc),
                title="Не удалось загрузить каталог",
                severity="error",
                timeout=10,
            )
        finally:
            self.app.catalog_loading = False
            self.query_one("#startup-loader", LoadingIndicator).display = False

    @on(ListView.Selected)
    def selected(self, event: ListView.Selected) -> None:
        identifier = event.item.id or ""
        if identifier == "home-flash":
            if self.app.manifest is None:
                self.app.notify(
                    "Каталог ещё не готов. Нажмите Ctrl+R, чтобы повторить загрузку.",
                    severity="warning",
                )
                return
            self.app.push_screen(DeviceScreen())
        elif identifier == "home-site":
            webbrowser.open(SITE_ORIGIN)
        elif identifier == "home-tiktok":
            webbrowser.open(TIKTOK_URL)
        elif identifier == "home-telegram":
            webbrowser.open(TELEGRAM_URL)
        elif identifier == "home-exit":
            self.app.exit()

    def action_quit_app(self) -> None:
        self.app.exit()


class DeviceScreen(BackScreen):
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="root"):
            yield Static("ШАГ 01  /  УСТРОЙСТВО", classes="eyebrow")
            yield Static("Выберите устройство", classes="screen-title")
            yield Static(
                "Список моделей приходит с сайта. Наличие файла проверяется "
                "отдельно для каждой модели.",
                classes="muted",
            )
            devices = (
                self.app.manifest.get("devices", [])
                if self.app.manifest
                else []
            )
            device_items = []
            for device in devices:
                device_id = str(device.get("id", ""))
                available = sum(
                    1
                    for fw in self.app.manifest.get("firmware", [])
                    if get_download_for_device(fw, device_id)
                )
                details = (
                    f"{device.get('chip', 'Unknown')} · {available} доступных "
                    f"файлов · {device.get('transport', 'USB')}"
                )
                device_items.append(
                    ListItem(
                        Horizontal(
                            Static(
                                static_device_logo(device_id),
                                classes="device-logo",
                            ),
                            Vertical(
                                Label(
                                    str(device.get("name", device_id)),
                                    classes="card-title",
                                ),
                                Label(details, classes="card-subtitle"),
                            ),
                        ),
                        id=f"device-{device_id}",
                    )
                )
            yield ListView(*device_items, id="device-list")
            yield Static(
                "Для ESP32 нужен корректный BIN именно для выбранной модели. "
                "Flipper Zero использует отдельный пакет обновления.",
                classes="hint",
            )
            with Horizontal(classes="button-row"):
                yield Button("Обновить каталог", id="device-refresh")
                yield Button("Назад", id="device-back")
        yield Footer()

    @on(ListView.Selected)
    def selected_device(self, event: ListView.Selected) -> None:
        identifier = event.item.id or ""
        if identifier.startswith("device-"):
            device_id = identifier.removeprefix("device-")
            devices = (
                self.app.manifest.get("devices", [])
                if self.app.manifest
                else []
            )
            device = next(
                (x for x in devices if str(x.get("id")) == device_id), None
            )
            if not device:
                self.app.notify(
                    "Эта модель отсутствует в текущем каталоге.",
                    severity="error",
                )
                return
            self.app.selected_device = device
            self.app.push_screen(FirmwareScreen())

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "device-back":
            self.app.pop_screen()
        elif event.button.id == "device-refresh":
            self.run_worker(
                self.refresh_catalog(), name="catalog-refresh", exclusive=True
            )

    async def refresh_catalog(self) -> None:
        try:
            result = await asyncio.to_thread(fetch_catalog_sync)
            self.app.manifest = result["manifest"]
            self.app.firmware_logos = result.get("logos", {})
            self.app.catalog_source = result.get("source", "online")
            self.app.notify("Каталог обновлён.", severity="information")
            self.app.switch_screen(DeviceScreen())
        except Exception as exc:
            self.app.notify(
                str(exc), title="Обновление не удалось", severity="error"
            )


class FirmwareScreen(BackScreen):
    def __init__(self) -> None:
        super().__init__()
        self.firmware_by_id: dict[str, tuple[dict, dict]] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="root"):
            device = self.app.selected_device or {}
            yield Static("ШАГ 02  /  КАТАЛОГ", classes="eyebrow")
            yield Static(
                f"Прошивки для {device.get('name', 'устройства')}",
                classes="screen-title",
            )
            yield Static(
                "Логотипы берутся из поля «Логотип» в админке ShlepaFlash. "
                "Если логотип не задан, показывается текстовый знак.",
                classes="muted",
            )
            firmware_rows = []
            device_id = str(device.get("id", ""))
            for firmware in (
                self.app.manifest.get("firmware", [])
                if self.app.manifest
                else []
            ):
                if firmware.get("available") is False:
                    continue
                download = get_download_for_device(firmware, device_id)
                if not download:
                    continue
                self.firmware_by_id[str(firmware["id"])] = (firmware, download)
                firmware_rows.append((firmware, download))

            if firmware_rows:
                firmware_items = []
                for index, (firmware, download) in enumerate(firmware_rows):
                    logo = self.app.render_firmware_logo(
                        str(firmware.get("id", "")),
                        str(firmware.get("name", "")),
                    )
                    version = str(firmware.get("version", ""))
                    size = int(download.get("size") or 0)
                    size_label = (
                        f"{size / (1024 * 1024):.1f} MiB"
                        if size
                        else "размер проверится при скачивании"
                    )
                    item_line = (
                        f"v{version} · {str(download.get('format', 'bin')).upper()}"
                        f" · {size_label}"
                    )
                    firmware_items.append(
                        ListItem(
                            Horizontal(
                                Static(logo, classes="firmware-logo"),
                                Vertical(
                                    Label(
                                        str(firmware.get("name", "Прошивка")),
                                        classes="card-title",
                                    ),
                                    Label(item_line, classes="card-subtitle"),
                                    Label(
                                        str(firmware.get("description", ""))[:180],
                                        classes="card-description",
                                    ),
                                ),
                            ),
                            id=f"firmware-{index}",
                        )
                    )
                yield ListView(*firmware_items, id="firmware-list")
            else:
                yield Static(
                    "Для этой модели пока нет доступных файлов прошивок. "
                    "Администратор должен загрузить подходящий файл "
                    "в редакторе прошивок сайта.",
                    classes="empty-state",
                )
            with Horizontal(classes="button-row"):
                yield Button("Обновить каталог", id="firmware-refresh")
                yield Button("Открыть сайт", id="firmware-site")
                yield Button("Назад", id="firmware-back")
        yield Footer()

    @on(ListView.Selected)
    def selected_firmware(self, event: ListView.Selected) -> None:
        identifier = event.item.id or ""
        if not identifier.startswith("firmware-"):
            return
        try:
            index = int(identifier.removeprefix("firmware-"))
            item = list(self.firmware_by_id.values())[index]
        except (ValueError, IndexError):
            self.app.notify(
                "Не удалось определить выбранную прошивку.", severity="error"
            )
            return
        self.app.selected_firmware, self.app.selected_download = item
        self.app.push_screen(FirmwareDetailsScreen())

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "firmware-back":
            self.app.pop_screen()
        elif event.button.id == "firmware-site":
            webbrowser.open(SITE_ORIGIN)
        elif event.button.id == "firmware-refresh":
            self.run_worker(
                self.refresh_catalog(), name="firmware-refresh", exclusive=True
            )

    async def refresh_catalog(self) -> None:
        try:
            result = await asyncio.to_thread(fetch_catalog_sync)
            self.app.manifest = result["manifest"]
            self.app.firmware_logos = result.get("logos", {})
            self.app.catalog_source = result.get("source", "online")
            self.app.notify("Каталог обновлён.", severity="information")
            # Replace only the current firmware screen; preserve the selected device.
            self.app.switch_screen(FirmwareScreen())
        except Exception as exc:
            self.app.notify(
                str(exc), title="Обновление не удалось", severity="error"
            )


class FirmwareDetailsScreen(BackScreen):
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="root"):
            firmware = self.app.selected_firmware or {}
            device = self.app.selected_device or {}
            yield Static("ШАГ 03  /  ПРОШИВКА", classes="eyebrow")
            yield Static(
                self.app.render_firmware_logo(
                    str(firmware.get("id", "")),
                    str(firmware.get("name", "")),
                ),
                id="detail-logo",
            )
            yield Static(
                str(firmware.get("name", "Прошивка")),
                classes="screen-title",
            )
            yield Static(
                f"Версия {firmware.get('version', '—')} · "
                f"Для {device.get('name', 'устройства')}",
                classes="muted",
            )
            with ScrollableContainer(id="description-scroll"):
                yield Static(
                    str(
                        firmware.get("longDescription")
                        or firmware.get("description")
                        or "Описание отсутствует."
                    ),
                    id="firmware-description",
                )
            download = self.app.selected_download or {}
            size = int(download.get("size") or 0)
            size_label = (
                f"Ожидаемый размер: {size:,} байт".replace(",", " ")
                if size
                else "Размер будет определён во время загрузки"
            )
            yield Static(size_label, classes="hint")
            with Horizontal(classes="button-row"):
                yield Button(
                    "Выбрать и скачать", id="details-select", variant="primary"
                )
                yield Button("Открыть сайт", id="details-site")
                yield Button("Назад", id="details-back")
        yield Footer()

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "details-back":
            self.app.pop_screen()
        elif event.button.id == "details-site":
            webbrowser.open(SITE_ORIGIN)
        elif event.button.id == "details-select":
            self.app.push_screen(DownloadScreen())


class DownloadScreen(Screen):
    BINDINGS = [("escape", "cancel_download", "Назад")]

    def __init__(self) -> None:
        super().__init__()
        self.download_worker = None
        self.download_complete = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="root"):
            yield Static("ШАГ 04  /  ЗАГРУЗКА", classes="eyebrow")
            yield Static(
                "Загружаю выбранный файл с сайта", classes="screen-title"
            )
            firmware = self.app.selected_firmware or {}
            device = self.app.selected_device or {}
            yield Static(
                f"{firmware.get('name', 'Прошивка')} · "
                f"{device.get('name', 'устройство')}",
                classes="muted",
            )
            yield ProgressBar(total=100, show_eta=False, id="download-progress")
            yield Static("Подключение к HTTPS API…", id="download-status")
            yield RichLog(highlight=True, markup=False, id="download-log")
            yield Button("Отмена", id="download-cancel")
        yield Footer()

    def on_mount(self) -> None:
        self.download_complete = False
        self.download_worker = self.run_worker(
            self.download(), name="firmware-download", exclusive=True
        )

    async def download(self) -> None:
        try:
            firmware = self.app.selected_firmware
            device = self.app.selected_device
            record = self.app.selected_download
            if not firmware or not device or not record:
                raise ShlepaFlashError(
                    "Перед скачиванием выберите устройство и прошивку."
                )
            def progress(received: int, total: int | None) -> None:
                if total and total > 0:
                    pct = min(100, int(received / total * 100))
                    self.query_one(
                        "#download-progress", ProgressBar
                    ).update(progress=pct)
                    self.query_one("#download-status", Static).update(
                        f"Загружено {received:,} / {total:,} байт ({pct}%)".replace(
                            ",", " "
                        )
                    )
                else:
                    self.query_one("#download-status", Static).update(
                        f"Загружено {received:,} байт".replace(",", " ")
                    )

            path = await download_artifact(firmware, device, record, progress)
            self.app.downloaded_path = path
            self.query_one("#download-progress", ProgressBar).update(
                progress=100
            )
            self.query_one("#download-log", RichLog).write(
                f"Файл сохранён: {path}"
            )
            self.query_one("#download-status", Static).update(
                "Загрузка завершена. Проверяю формат файла…"
            )
            if str(device.get("id")) == "flipper":
                self.app.pop_screen()
                self.app.push_screen(PackageScreen(kind="flipper"))
                return
            if path.suffix.lower() != ".bin":
                self.app.pop_screen()
                self.app.push_screen(PackageScreen(kind="package"))
                return
            plan = validate_bin_for_device(path, str(device.get("id", "")))
            self.app.flash_plan = plan
            self.app.pop_screen()
            self.app.push_screen(PortScreen())
        except asyncio.CancelledError:
            logging.info("Firmware download cancelled by user")
            raise
        except Exception as exc:
            logging.exception("Artifact download or preflight failed")
            self.query_one("#download-status", Static).update(
                "Загрузка или проверка не удалась. Устройство не прошивалось."
            )
            self.query_one("#download-log", RichLog).write(f"ОШИБКА: {exc}")
            self.query_one("#download-cancel", Button).label = "Назад"
        finally:
            self.download_complete = True

    def confirm_cancel(self) -> None:
        if self.download_complete:
            self.app.pop_screen()
            return

        def on_confirmation(confirmed: bool | None) -> None:
            if confirmed:
                if self.download_worker is not None:
                    self.download_worker.cancel()
                self.app.pop_screen()

        self.app.push_screen(ConfirmCancelScreen(), on_confirmation)

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "download-cancel":
            self.confirm_cancel()

    def action_cancel_download(self) -> None:
        self.confirm_cancel()


class PortScreen(BackScreen):
    def __init__(self) -> None:
        super().__init__()
        self.ports: list[dict[str, str]] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="root"):
            yield Static("ШАГ 05  /  USB", classes="eyebrow")
            yield Static("Подключите устройство", classes="screen-title")
            device = self.app.selected_device or {}
            plan = self.app.flash_plan
            yield Static(
                f"Модель: {device.get('name','—')} · "
                f"Чип: {plan.chip_name if plan else 'не проверен'} · "
                "BIN уже проверен до открытия порта.",
                classes="muted",
            )
            yield Static(
                "Порт обновляется по запросу. Подключите устройство надёжным "
                "USB-кабелем с передачей данных.",
                classes="hint",
            )
            with Horizontal(id="port-row"):
                yield Static("COM-порт", classes="field-label")
                yield Static("Обнаружение…", id="port-summary")
            yield ListView(id="port-list")
            with Horizontal(classes="button-row"):
                yield Button("Обновить порты", id="port-refresh")
                yield Button(
                    "Прошить", id="port-flash", variant="primary", disabled=True
                )
                yield Button("Назад", id="port-back")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(
            self.refresh_ports(), name="serial-port-scan", exclusive=True
        )
        self.set_interval(5, self.schedule_port_refresh)

    def schedule_port_refresh(self) -> None:
        self.run_worker(
            self.refresh_ports(), name="serial-port-scan", exclusive=True
        )

    async def refresh_ports(self) -> None:
        previously = getattr(self.app, "selected_port", None)
        self.ports = list_serial_ports()
        view = self.query_one("#port-list", ListView)
        await view.clear()
        if not self.ports:
            await view.append(
                ListItem(
                    Static(
                        "Устройство не найдено · подключите USB и нажмите "
                        "«Обновить порты»."
                    ),
                    id="port-none",
                    disabled=True,
                )
            )
            self.query_one("#port-summary", Static).update(
                "COM-порты не найдены"
            )
            self.query_one("#port-flash", Button).disabled = True
            self.app.selected_port = None
            return

        items = []
        for port in self.ports:
            label = f"{port['device']}   {port['description']}"
            if port["hwid"]:
                label += f"\n{port['hwid']}"
            items.append(
                ListItem(
                    Static(label),
                    id="port-" + port["device"].replace("\\", "_"),
                )
            )
        await view.extend(items)
        self.query_one("#port-summary", Static).update(
            f"Найдено портов: {len(self.ports)}"
        )
        if previously and any(p["device"] == previously for p in self.ports):
            self.app.selected_port = previously
            self.query_one("#port-flash", Button).disabled = False
        else:
            self.app.selected_port = None
            self.query_one("#port-flash", Button).disabled = True

    @on(ListView.Selected)
    def selected_port(self, event: ListView.Selected) -> None:
        identifier = event.item.id or ""
        if identifier.startswith("port-COM"):
            selected = identifier.removeprefix("port-")
            if any(p["device"] == selected for p in self.ports):
                self.app.selected_port = selected
                self.query_one("#port-summary", Static).update(
                    "Выбран порт: " + selected
                )
                self.query_one("#port-flash", Button).disabled = False

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "port-back":
            self.app.pop_screen()
        elif event.button.id == "port-refresh":
            self.run_worker(
                self.refresh_ports(), name="serial-port-refresh", exclusive=True
            )
        elif event.button.id == "port-flash":
            if (
                not self.app.selected_port
                or not self.app.flash_plan
                or not self.app.downloaded_path
            ):
                self.app.notify(
                    "Сначала выберите COM-порт и проверьте BIN.",
                    severity="warning",
                )
                return

            def on_confirmation(confirmed: bool | None) -> None:
                if confirmed:
                    self.app.push_screen(FlashingScreen())

            self.app.push_screen(ConfirmFlashScreen(), on_confirmation)


class FlashingScreen(Screen):
    BINDINGS = [("ctrl+c", "ignore_abort", "")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="root"):
            yield Static("SHLEPAFLASH  /  FLASHING", classes="eyebrow")
            yield Static("Прошивка устройства…", classes="screen-title")
            device = self.app.selected_device or {}
            yield Static(
                f"{device.get('name','Устройство')} · "
                f"{self.app.selected_port or 'COM?'}",
                classes="muted",
            )
            yield ProgressBar(total=100, show_eta=False, id="flash-progress")
            yield Static("Подготовка прошивальщика…", id="flash-status")
            yield RichLog(highlight=True, markup=False, id="flash-log")
            yield Static(
                "Не отключайте USB-кабель во время записи.", classes="hint"
            )
            yield Button("Завершить", id="flash-finish", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self.flash(), name="esp-flash", exclusive=True)

    async def flash(self) -> None:
        try:
            plan: FlashPlan | None = self.app.flash_plan
            port: str | None = self.app.selected_port
            image: Path | None = self.app.downloaded_path
            if not plan or not port or not image:
                raise ShlepaFlashError(
                    "Сеанс прошивки неполный. Вернитесь к выбору устройства и порта."
                )
            log = self.query_one("#flash-log", RichLog)
            for warning in plan.warnings:
                log.write("ПРЕДУПРЕЖДЕНИЕ: " + warning)
            self.query_one("#flash-status", Static).update(
                "Запускаю esptool. Может потребоваться режим загрузчика на устройстве…"
            )

            def out(line: str) -> None:
                log.write(line)
                logging.info("esptool: %s", line)

            def progress(pct: int) -> None:
                self.query_one("#flash-progress", ProgressBar).update(
                    progress=pct
                )
                self.query_one("#flash-status", Static).update(
                    f"Запись во Flash-память · {pct}%"
                )

            code, output = await run_esptool(
                plan, port, image, on_output=out, on_progress=progress
            )
            if code != 0:
                self.query_one("#flash-status", Static).update(
                    "Прошивка не завершена. Не отключайте плату, если запись ещё продолжается."
                )
                log.write("ОШИБКА: esptool завершился с кодом " + str(code))
                tail = "\n".join(output.splitlines()[-30:])
                self.query_one("#flash-log", RichLog).write(tail[-4000:])
            else:
                self.query_one("#flash-progress", ProgressBar).update(
                    progress=100
                )
                self.query_one("#flash-status", Static).update(
                    "✓  Прошивка успешно записана. Проверьте перезапуск устройства."
                )
                log.write("✓  ShlepaFlash: операция завершена успешно.")
                log.write(
                    "Если плата не перезапустилась автоматически, нажмите Reset "
                    "или переподключите USB."
                )
                logging.info(
                    "flash success device=%s port=%s file=%s",
                    plan.model_id,
                    port,
                    image.name,
                )
        except Exception as exc:
            logging.exception("Firmware flash failed")
            self.query_one("#flash-status", Static).update(
                "Ошибка прошивки · проверьте журнал ниже"
            )
            self.query_one("#flash-log", RichLog).write("ОШИБКА: " + str(exc))
            self.app.notify(
                str(exc),
                title="Прошивка не завершена",
                severity="error",
                timeout=10,
            )
        finally:
            self.query_one("#flash-finish", Button).disabled = False

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "flash-finish":
            self.app.return_home()

    def action_ignore_abort(self) -> None:
        self.app.notify(
            "Не прерывайте запись без необходимости. Оставьте устройство "
            "подключённым до завершения.",
            severity="warning",
        )


class PackageScreen(BackScreen):
    def __init__(self, kind: str = "package") -> None:
        super().__init__()
        self.kind = kind

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="root"):
            path = self.app.downloaded_path
            device = self.app.selected_device or {}
            title = (
                "Пакет для Flipper Zero"
                if self.kind == "flipper"
                else "Пакет прошивки скачан"
            )
            yield Static("SHLEPAFLASH  /  PACKAGE", classes="eyebrow")
            yield Static(title, classes="screen-title")
            yield Static(f"Устройство: {device.get('name','—')}", classes="muted")
            yield Static(
                f"Файл: {path}" if path else "Файл не найден",
                id="package-path",
            )
            if self.kind == "flipper":
                yield Static(
                    "Flipper Zero использует свой протокол обновления. "
                    "ShlepaFlash скачал пакет и не передаёт его через esptool. "
                    "Откройте qFlipper, выберите Install from file / "
                    "Установить из файла и укажите скачанный пакет. "
                    "Не отключайте устройство во время обновления.",
                    classes="package-instructions",
                )
            else:
                yield Static(
                    "Сайт опубликовал не сырой BIN. ShlepaFlash не будет "
                    "передавать ZIP/TGZ/DFU в esptool автоматически. "
                    "Используйте инструкцию именно для этого формата и модели.",
                    classes="package-instructions",
                )
            with Horizontal(classes="button-row"):
                yield Button(
                    "Открыть qFlipper",
                    id="package-qflipper",
                    variant="primary",
                    disabled=self.kind != "flipper",
                )
                yield Button(
                    "Показать файл", id="package-file", disabled=path is None
                )
                yield Button(
                    "Открыть страницу загрузки qFlipper",
                    id="package-qflipper-web",
                    disabled=self.kind != "flipper",
                )
                yield Button("Завершить", id="package-finish")
        yield Footer()

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        path = self.app.downloaded_path
        if event.button.id == "package-qflipper":
            executable = find_qflipper()
            if executable:
                try:
                    os.startfile(str(executable))  # type: ignore[attr-defined]
                except Exception as exc:
                    self.app.notify(
                        f"Не удалось запустить qFlipper: {exc}",
                        severity="error",
                    )
            else:
                webbrowser.open("https://flipperzero.one/update")
                self.app.notify(
                    "qFlipper не найден. Откройте официальный сайт, установите "
                    "qFlipper, затем выберите Install from file.",
                    severity="warning",
                    timeout=10,
                )
        elif event.button.id == "package-file" and path:
            try:
                if os.name == "nt":
                    subprocess.Popen(["explorer.exe", "/select,", str(path)])
                else:
                    webbrowser.open(path.parent.as_uri())
            except Exception as exc:
                self.app.notify(
                    f"Не удалось открыть папку загрузок: {exc}",
                    severity="error",
                )
        elif event.button.id == "package-qflipper-web":
            webbrowser.open("https://flipperzero.one/update")
        elif event.button.id == "package-finish":
            self.app.return_home()


class ShlepaFlashApp(App[None]):
    TITLE = "ShlepaFlash"
    SUB_TITLE = "Terminal firmware client"
    CSS = """
    Screen { background: #080B10; color: #E8EEF5; }
    Header { background: #101721; color: #E8EEF5; }
    Footer { background: #101721; color: #AAB7C5; }
    #root { width: 100%; height: 1fr; padding: 1 3; }
    #brand-panel { height: auto; min-height: 8; margin-bottom: 1; padding: 1 2; background: #0E141D; border: round #F09A45; color: #E8EEF5; }
    #welcome { margin: 0 0 1 1; color: #E8EEF5; text-style: bold; }
    #catalog-line { height: 1; margin: 0 0 1 1; }
    #connection-indicator { width: 3; color: #F0A04A; }
    #catalog-status { color: #9DAAB8; }
    #startup-loader { height: 1; margin: 0 1 1 1; color: #FF9A3D; }
    .eyebrow { height: auto; margin: 1 0; color: #FF9A3D; text-style: bold; }
    .screen-title { height: auto; margin-bottom: 1; color: #F4F6F8; text-style: bold; }
    .muted { height: auto; margin-bottom: 1; color: #93A0B0; }
    .hint { height: auto; margin: 1 0; color: #AAB7C5; }
    .empty-state { height: auto; margin: 2 0; padding: 1 2; border: round #39485B; color: #F6C07B; }
    ListView { height: 1fr; min-height: 6; margin: 1 0; padding: 0 1; background: #0B1017; border: round #273444; }
    ListItem { height: auto; min-height: 3; padding: 1; background: #0D131C; border-bottom: solid #1B2632; }
    ListItem:hover { background: #192431; }
    ListItem.-highlight { background: #1D2A38; border-left: thick #FF9A3D; }
    .menu-num { width: 6; color: #FF9A3D; text-style: bold; content-align: center middle; }
    .menu-title { color: #E8EEF5; text-style: bold; }
    .menu-subtitle { color: #8795A5; }
    .device-logo { width: 14; height: auto; padding: 0 1; content-align: center middle; }
    .firmware-logo { width: 14; height: auto; padding: 0 1; content-align: center middle; }
    .card-title { color: #F4F6F8; text-style: bold; }
    .card-subtitle { color: #FFB56B; }
    .card-description { color: #A5B1BE; }
    #detail-logo { height: auto; margin-bottom: 1; }
    #description-scroll { height: 1fr; min-height: 5; padding: 1 2; border: round #263444; background: #0D131C; }
    #firmware-description { height: auto; color: #D8E0E9; }
    .button-row { height: auto; min-height: 3; margin: 1 0; align: left middle; }
    Button { margin-right: 1; background: #1A2532; color: #E8EEF5; border: round #415168; }
    Button:hover { background: #2B394A; color: #FFFFFF; border: round #FF9A3D; }
    Button.-primary { background: #F08B2E; color: #11151B; text-style: bold; border: round #FFB56B; }
    Button.-primary:hover { background: #FFB56B; color: #11151B; }
    #port-row { height: auto; margin: 1 0; }
    .field-label { width: 14; color: #FFB56B; text-style: bold; }
    #port-summary { color: #C3CDD8; }
    #download-progress, #flash-progress { margin: 1 0; color: #FF9A3D; }
    #download-status, #flash-status { height: auto; margin: 1 0; color: #C7D1DC; }
    #download-log, #flash-log { height: 1fr; min-height: 5; border: round #273444; background: #080B10; color: #C7D1DC; }
    .package-instructions { height: auto; margin: 1 0; padding: 1 2; border: round #5B462E; background: #17120D; color: #F3D6B2; }
    #confirm-box { width: 70%; max-width: 72; height: auto; max-height: 80%; padding: 2 3; border: round #FF9A3D; background: #101721; align: center middle; }
    .confirm-title { height: auto; margin-bottom: 1; color: #F4F6F8; text-style: bold; text-align: center; }
    #confirm-box .muted { text-align: center; }
    #confirm-box .button-row { align: center middle; }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Выход"),
        ("ctrl+r", "refresh_catalog", "Обновить каталог"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.manifest: dict | None = None
        self.firmware_logos: dict[str, str] = {}
        self.catalog_source = "unknown"
        self.catalog_warning = ""
        self.catalog_loading = False
        self.selected_device: dict | None = None
        self.selected_firmware: dict | None = None
        self.selected_download: dict | None = None
        self.selected_port: str | None = None
        self.downloaded_path: Path | None = None
        self.flash_plan: FlashPlan | None = None

    def on_mount(self) -> None:
        configure_logging()
        self.push_screen(HomeScreen())

    def return_home(self) -> None:
        """Pop transient screens one at a time, returning to the original HomeScreen."""
        self.selected_device = None
        self.selected_firmware = None
        self.selected_download = None
        self.selected_port = None
        self.downloaded_path = None
        self.flash_plan = None
        self._home_pops_remaining = 16
        self.call_after_refresh(self._pop_to_home_step)

    def _pop_to_home_step(self) -> None:
        if isinstance(self.screen, HomeScreen):
            return
        if self._home_pops_remaining <= 0:
            self.switch_screen(HomeScreen())
            return
        self._home_pops_remaining -= 1
        self.pop_screen()
        self.call_after_refresh(self._pop_to_home_step)

    def action_refresh_catalog(self) -> None:
        current = self.screen
        if isinstance(current, HomeScreen):
            current.run_worker(
                current.load_catalog(),
                name="manual-catalog-refresh",
                exclusive=True,
            )
        else:
            self.notify(
                "Вернитесь на главный экран, чтобы обновить каталог.",
                severity="information",
            )

    def render_firmware_logo(self, firmware_id: str, firmware_name: str) -> Text:
        logo_data = self.firmware_logos.get(firmware_id, "")
        blob = decode_logo_data_url(logo_data)
        rendered = image_to_terminal_text(blob, width=12, pixel_height=12)
        if rendered:
            return rendered
        initials = re.sub(r"[^A-Za-zА-Яа-я0-9]", "", firmware_name)[:3].upper() or "SF"
        return Text(
            "\n".join(["  ┌──────┐", f"  │{initials:^6}│", "  └──────┘"]),
            style="bold #FF9A3D",
        )


def main() -> None:
    configure_logging()
    try:
        app = ShlepaFlashApp()
        app.run(mouse=True)
    except KeyboardInterrupt:
        return
    except Exception as exc:
        logging.exception("Fatal application error")
        print(f"ShlepaFlash не удалось запустить: {exc}", file=sys.stderr)
        print(f"Журнал: {LOG_FILE}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
