from shlepaflash.core import (
    ShlepaFlashError, get_download_for_device, plan_esp_image,
    resolve_download_url, safe_filename, validate_manifest,
)
import pytest


def esp_image(chip_id: int, *, offset: int = 0, partition_table: bool = False, size: int = 0x9000 + 16) -> bytes:
    data = bytearray(max(size, offset + 1024))
    data[offset] = 0xE9
    data[offset + 12] = chip_id & 0xFF
    data[offset + 13] = (chip_id >> 8) & 0xFF
    if partition_table:
        data[0x8000:0x8002] = b"\xAA\x50"
    return bytes(data)


def test_manifest_schema_accepts_site_contract():
    payload = {
        "product": "ShlepaFlash", "manifestVersion": 1,
        "devices": [{"id": "plus2", "name": "M5StickC Plus2"}],
        "firmware": [{"id": "demo", "name": "Demo", "downloads": [{"deviceId": "plus2", "downloadPath": "/api?action=download&id=demo&model=plus2"}]}],
    }
    assert validate_manifest(payload) is payload


def test_manifest_rejects_unknown_device_reference():
    payload = {
        "product": "ShlepaFlash", "manifestVersion": 1,
        "devices": [{"id": "plus2", "name": "M5StickC Plus2"}],
        "firmware": [{"id": "demo", "name": "Demo", "downloads": [{"deviceId": "unknown", "downloadPath": "/api?action=download"}]}],
    }
    with pytest.raises(ShlepaFlashError):
        validate_manifest(payload)


def test_app_only_esp32_plans_to_factory_app_address():
    plan = plan_esp_image(esp_image(0, size=2048), "plus2")
    assert plan.chip_name == "ESP32"
    assert plan.app_only is True
    assert plan.address == 0x10000
    assert plan.payload[0] == 0xE9


def test_full_image_keeps_bootloader_and_partitioned_layout():
    plan = plan_esp_image(esp_image(0, partition_table=True), "plus")
    assert plan.app_only is False
    assert plan.address == 0
    assert plan.data_offset == 0


def test_image_with_bootloader_at_1000_uses_expected_offset():
    plan = plan_esp_image(esp_image(9, offset=0x1000, partition_table=True, size=0xA000), "s3")
    assert plan.chip_name == "ESP32-S3"
    assert plan.data_offset == 0x1000
    assert plan.address == 0x1000
    assert plan.payload[0] == 0xE9


def test_wrong_chip_is_rejected_before_serial_access():
    with pytest.raises(ShlepaFlashError, match="Несовместимая прошивка"):
        plan_esp_image(esp_image(9, size=2048), "plus2")


@pytest.mark.parametrize("bad", [b"", b"PK\x03\x04" + b"x" * 2000, b"<!doctype html>" + b" " * 2000, b"x" * 2048])
def test_non_bin_or_bad_image_is_rejected(bad):
    with pytest.raises(ShlepaFlashError):
        plan_esp_image(bad, "plus2")


def test_external_download_origin_is_rejected():
    with pytest.raises(ShlepaFlashError):
        resolve_download_url("https://evil.example/payload.bin")


def test_first_party_relative_download_is_resolved():
    assert resolve_download_url("/api?action=download&id=x&model=plus2") == "https://sflasher.bot.nu/api?action=download&id=x&model=plus2"


def test_download_selects_exact_model_not_first_file():
    firmware = {"downloads": [
        {"deviceId": "plus2", "downloadPath": "/one"},
        {"deviceId": "s3", "downloadPath": "/two"},
    ]}
    assert get_download_for_device(firmware, "s3")["downloadPath"] == "/two"
    assert get_download_for_device(firmware, "flipper") is None


def test_filename_is_safe_and_bounded():
    result = safe_filename("../../bad name:\x00.bin")
    assert "/" not in result and "\\" not in result and ".." not in result
    assert len(safe_filename("a" * 300)) <= 100


def test_manifest_rejects_non_numeric_version():
    payload = {"product": "ShlepaFlash", "manifestVersion": "v1", "devices": [], "firmware": []}
    with pytest.raises(ShlepaFlashError, match="некорректно"):
        validate_manifest(payload)


def test_external_download_credentials_and_invalid_port_are_rejected():
    with pytest.raises(ShlepaFlashError, match="авторизации"):
        resolve_download_url("https://user:pass@sflasher.bot.nu/file.bin")
    with pytest.raises(ShlepaFlashError, match="некорректный порт"):
        resolve_download_url("https://sflasher.bot.nu:invalid/file.bin")
