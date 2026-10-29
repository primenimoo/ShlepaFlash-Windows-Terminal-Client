from shlepaflash.api import logos_from_public_list, decode_logo_data_url


def test_firmware_logo_from_admin_public_list_is_reused():
    logo = "data:image/png;base64,iVBORw0KGgo="
    result = logos_from_public_list({"items": [{"id": "fw-123", "logo": logo}, {"id": "fw-456", "logo": "https://outside.invalid/img.png"}]})
    assert result == {"fw-123": logo}
    assert decode_logo_data_url(logo) is not None


def test_bad_logo_is_ignored_not_fatal():
    assert logos_from_public_list({"items": [{"id": "x", "logo": "data:image/png;base64," + ("A" * 500_000)}]}) == {}
    assert decode_logo_data_url("https://outside.invalid/logo.png") is None
