
from io import BytesIO
from pathlib import Path

from PIL import Image

from shlepaflash.app import image_to_terminal_text, make_wordmark


def test_wordmark_has_brand_and_both_brand_colors():
    mark = make_wordmark()
    assert "SHLEPA" in mark.plain
    assert "FLASH" in mark.plain

    styles = [str(span.style).lower() for span in mark.spans]
    assert any("#f4f6f8" in style for style in styles)
    assert any("#ff9a3d" in style for style in styles)


def test_raster_logo_renders_to_terminal_blocks():
    image = Image.new("RGB", (8, 8), (255, 128, 0))
    stream = BytesIO()
    image.save(stream, format="PNG")
    result = image_to_terminal_text(stream.getvalue(), width=6, pixel_height=6)

    assert result is not None
    assert "▀" in result.plain


def test_bad_logo_does_not_crash_renderer():
    assert image_to_terminal_text(
        b"not a real image",
        width=6,
        pixel_height=6,
    ) is None


def test_screens_use_callback_modals_instead_of_blocking_waits():
    source = (
        Path(__file__).parents[1] / "src" / "shlepaflash" / "app.py"
    ).read_text(encoding="utf-8")

    assert "push_screen_wait(" not in source
    assert "ConfirmCancelScreen(), on_confirmation" in source
    assert "ConfirmFlashScreen(), on_confirmation" in source


def test_catalog_refresh_does_not_discard_selected_device_screen():
    source = (
        Path(__file__).parents[1] / "src" / "shlepaflash" / "app.py"
    ).read_text(encoding="utf-8")

    assert "self.app.switch_screen(FirmwareScreen())" in source
    assert (
        "self.app.pop_screen()\n"
        "            self.app.pop_screen()\n"
        "            self.app.push_screen(DeviceScreen())"
    ) not in source

    assert "self.call_after_refresh(self._pop_to_home_step)" in source


def test_textual_stylesheet_does_not_use_incompatible_text_wrap_declarations():
    source = (
        Path(__file__).parents[1] / "src" / "shlepaflash" / "app.py"
    ).read_text(encoding="utf-8")
    css = source.split('    CSS = """', 1)[1].split('    """', 1)[0]

    assert "text-wrap:" not in css

