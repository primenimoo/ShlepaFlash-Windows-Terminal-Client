import pytest
from io import BytesIO

from PIL import Image

from shlepaflash.app import image_to_terminal_text, make_wordmark, render_ascii_percent, render_ascii_progress


def test_wordmark_uses_the_supplied_block_logo_and_brand_colors():
    mark = make_wordmark()
    lines = mark.plain.splitlines()
    assert len(lines) == 5
    assert all("█" in line for line in lines)
    assert lines[0].startswith("███████ ██   ██")
    styles = [str(span.style).lower() for span in mark.spans]
    assert any("#f4f6f8" in style for style in styles)
    assert any("#ff9a3d" in style for style in styles)


@pytest.mark.parametrize("value", [0, 7, 87, 100])
def test_ascii_percent_renders_all_rows_and_orange_art(value):
    rendered = render_ascii_percent(value)
    assert len(rendered.plain.splitlines()) == 5
    assert "\\n" not in rendered.plain
    assert rendered.plain.strip()
    assert all(span.style.color is not None for span in rendered.spans)
    assert all("#ff9a3d" in str(span.style).lower() for span in rendered.spans)


def test_ascii_progress_handles_limits_and_clamps_values():
    assert render_ascii_progress(0).plain == "[" + "-" * 28 + "]"
    assert render_ascii_progress(100).plain == "[" + "=" * 28 + "]"
    assert render_ascii_progress(140).plain == render_ascii_progress(100).plain


def test_raster_logo_renders_to_terminal_blocks():
    image = Image.new("RGB", (8, 8), (255, 128, 0))
    stream = BytesIO()
    image.save(stream, format="PNG")
    result = image_to_terminal_text(stream.getvalue(), width=6, pixel_height=6)
    assert result is not None
    assert "▀" in result.plain


def test_bad_logo_does_not_crash_renderer():
    assert image_to_terminal_text(b"not a real image", width=6, pixel_height=6) is None


def test_screens_use_callback_modals_instead_of_blocking_waits():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "src" / "shlepaflash" / "app.py").read_text(encoding="utf-8")
    assert "push_screen_wait(" not in source
    assert "ConfirmCancelScreen(), on_confirmation" in source
    assert "ConfirmFlashScreen(), on_confirmation" in source


def test_catalog_refresh_does_not_discard_selected_device_screen():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "src" / "shlepaflash" / "app.py").read_text(encoding="utf-8")
    assert "self.app.switch_screen(FirmwareScreen())" in source
    assert "self.app.pop_screen()\n            self.app.pop_screen()\n            self.app.push_screen(DeviceScreen())" not in source
    assert "self.call_after_refresh(self._pop_to_home_step)" in source


def test_textual_stylesheet_does_not_use_incompatible_text_wrap_declarations():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "src" / "shlepaflash" / "app.py").read_text(encoding="utf-8")
    css = source.split('    CSS = """', 1)[1].split('    """', 1)[0]
    assert "text-wrap:" not in css
    assert "#flash-log" not in css
    assert "#download-log" not in css
    assert "RichLog" not in source


def test_textual_app_starts_and_mounts_compact_home(monkeypatch):
    import asyncio

    from shlepaflash.app import HomeScreen, ShlepaFlashApp

    monkeypatch.setattr(
        "shlepaflash.app.fetch_catalog_sync",
        lambda: {
            "manifest": {"devices": [], "firmware": []},
            "logos": {},
            "source": "online",
            "warning": "",
        },
    )

    async def exercise():
        app = ShlepaFlashApp()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause(0.1)
            assert isinstance(app.screen, HomeScreen)
            assert app.screen.query_one("#home-menu") is not None
            assert app.screen.query_one("#brand-panel") is not None

    asyncio.run(exercise())
