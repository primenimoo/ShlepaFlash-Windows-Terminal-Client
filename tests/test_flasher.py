import asyncio
from pathlib import Path

import httpx
import pytest

from shlepaflash import flasher
from shlepaflash.core import FlashPlan, ShlepaFlashError, plan_esp_image
from shlepaflash.flasher import build_esptool_command


def test_esptool_global_reset_options_are_before_write_flash(tmp_path: Path):
    plan = FlashPlan(
        model_id="plus2", chip_name="ESP32", address=0x10000, data_offset=0,
        payload=b"\xE9" + b"\x00" * 2047, app_only=True,
    )
    command = build_esptool_command(plan, "COM7", tmp_path / "firmware.bin")
    write_at = command.index("write-flash")
    assert command[command.index("--chip") + 1] == "esp32"
    assert command[command.index("--before") + 1] == "default-reset"
    assert command[command.index("--after") + 1] == "hard-reset"
    assert command.index("--before") < write_at
    assert command.index("--after") < write_at
    assert command[write_at + 1:write_at + 7] == ["--flash-mode", "dio", "--flash-freq", "40m", "--flash-size", "detect"]
    assert command[-2:] == ["0x10000", str(tmp_path / "firmware.bin")]


def test_esptool_rejects_invalid_com_port(tmp_path: Path):
    plan = FlashPlan("plus2", "ESP32", 0x10000, 0, b"\xE9" * 1024, True)
    with pytest.raises(ShlepaFlashError, match="COM-порт"):
        build_esptool_command(plan, "not-a-port", tmp_path / "firmware.bin")


def test_download_rejects_manifest_size_mismatch(tmp_path: Path, monkeypatch):
    body = b"x" * 512
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(200, content=body, request=request)
        )
        return real_client(*args, **kwargs)

    monkeypatch.setattr(flasher.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(flasher, "DOWNLOAD_DIR", tmp_path)
    with pytest.raises(ShlepaFlashError, match="не совпадает с HTTP"):
        asyncio.run(flasher.download_artifact(
            {"id": "fw", "name": "Demo", "version": "1"},
            {"id": "plus2"},
            {"downloadPath": "/api?action=download&id=fw&model=plus2", "format": "bin", "size": 513},
        ))
    assert not list(tmp_path.glob("*.part"))
    assert not list(tmp_path.glob("*.bin"))


def test_full_image_payload_is_trimmed_before_esptool_write(tmp_path: Path, monkeypatch):
    # Image header starts at 0x1000; partition table is in the full image at 0x8000.
    image = bytearray(0xA000)
    image[0x1000] = 0xE9
    image[0x1000 + 12] = 9  # ESP32-S3
    image[0x1000 + 13] = 0
    image[0x8000:0x8002] = b"\xAA\x50"
    image_path = tmp_path / "firmware.bin"
    image_path.write_bytes(image)
    plan = plan_esp_image(bytes(image), "s3")
    captured = {}

    class FakeReader:
        async def read(self, count):
            return b""

    class FakeProcess:
        stdout = FakeReader()
        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = list(command)
        flash_payload_path = Path(command[-1])
        captured["prepared_path"] = flash_payload_path
        captured["prepared_payload"] = flash_payload_path.read_bytes()
        return FakeProcess()

    monkeypatch.setattr(flasher.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    code, output = asyncio.run(flasher.run_esptool(plan, "COM7", image_path))
    assert code == 0
    assert captured["prepared_payload"] == plan.payload
    assert captured["prepared_payload"][0] == 0xE9
    command = captured["command"]
    assert command[command.index("--chip") + 1] == "esp32s3"
    assert command[command.index("write-flash") + 7] == "0x1000"
    assert not captured["prepared_path"].exists(), "temporary prepared image should be cleaned up"
