"""Georgia Italic lookup for heard-not-seen captions, and the render-time warning when it is missing."""

from __future__ import annotations

from pathlib import Path

import pytest

from creation import captions as cap
from creation.captions import Cue, ItalicFont, find_italic_font, italic_font_warning


def _no_fc(_pattern: str) -> tuple[str, str, str] | None:
    return None


def _font(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"\0")
    return path


def test_macos_finds_georgia_italic_in_the_system_font_folder(tmp_path: Path) -> None:
    supplemental = tmp_path / "Supplemental"
    italic = _font(supplemental, "Georgia Italic.ttf")
    regular = _font(supplemental, "Georgia.ttf")

    font = find_italic_font(
        platform="darwin", font_dirs=[tmp_path / "bundled", supplemental], match=_no_fc
    )

    assert font == ItalicFont(italic, regular) and font.ok
    assert font.warning() is None


def test_macos_without_georgia_warns_and_names_the_fallback(tmp_path: Path) -> None:
    font = find_italic_font(
        platform="darwin",
        font_dirs=[tmp_path],
        match=lambda _p: (
            "Helvetica",
            "Oblique",
            "/System/Library/Fonts/Helvetica.ttc",
        ),
    )

    assert not font.ok
    warning = font.warning() or ""
    assert warning.startswith(
        "Georgia not found; heard-not-seen captions will fall back to"
    )
    assert "Helvetica (/System/Library/Fonts/Helvetica.ttc)" in warning
    assert "Restore Standard Fonts" in warning


def test_macos_does_not_trust_fontconfig_for_georgia(tmp_path: Path) -> None:
    # CoreText, not fontconfig, draws the caption on macOS: a fontconfig hit is not proof.
    font = find_italic_font(
        platform="darwin",
        font_dirs=[tmp_path],
        match=lambda _p: ("Georgia", "Italic", "/opt/fc/Georgia Italic.ttf"),
    )
    assert not font.ok


def test_linux_resolves_through_fontconfig(tmp_path: Path) -> None:
    patterns: list[str] = []

    def fc(pattern: str) -> tuple[str, str, str]:
        patterns.append(pattern)
        return (
            "Georgia",
            "Italic",
            "/usr/share/fonts/truetype/msttcorefonts/georgiai.ttf",
        )

    font = find_italic_font(platform="linux", font_dirs=[tmp_path], match=fc)

    assert patterns == ["Georgia:italic"]
    assert font.ok
    assert font.italic == Path("/usr/share/fonts/truetype/msttcorefonts/georgiai.ttf")


def test_linux_without_georgia_names_the_fontconfig_substitute(tmp_path: Path) -> None:
    font = find_italic_font(
        platform="linux",
        font_dirs=[tmp_path],
        match=lambda _p: (
            "DejaVu Sans",
            "Oblique",
            "/usr/share/fonts/DejaVuSans-Oblique.ttf",
        ),
    )

    assert not font.ok
    warning = font.warning() or ""
    assert "DejaVu Sans (/usr/share/fonts/DejaVuSans-Oblique.ttf)" in warning
    assert "ttf-mscorefonts-installer" in warning


def test_linux_georgia_regular_only_is_a_synthetic_slant_warning(
    tmp_path: Path,
) -> None:
    font = find_italic_font(
        platform="linux",
        font_dirs=[tmp_path],
        match=lambda _p: ("Georgia", "Regular", "/fonts/georgia.ttf"),
    )
    assert not font.ok
    assert "only /fonts/georgia.ttf" in (font.warning() or "")


def test_no_fontconfig_and_no_file_still_warns(tmp_path: Path) -> None:
    font = find_italic_font(platform="linux", font_dirs=[tmp_path], match=_no_fc)
    assert "whatever face libass falls back to" in (font.warning() or "")


def test_a_georgia_italic_in_the_bundled_fonts_dir_wins(tmp_path: Path) -> None:
    italic = _font(tmp_path, "Georgia Italic.ttf")
    font = find_italic_font(platform="linux", font_dirs=[tmp_path], match=_no_fc)
    assert font.italic == italic


def test_render_warns_on_stderr_when_an_italic_cue_falls_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cap, "find_italic_font", lambda: ItalicFont(None, None, "Arial (/a/Arial.ttf)")
    )

    warning = italic_font_warning(
        [Cue(0, 1, "Go"), Cue(1, 2, "Behind you.", italic=True)]
    )

    assert (
        warning.startswith("FONT: Georgia not found")
        and "Arial (/a/Arial.ttf)" in warning
    )
    assert f"WARNING {warning}" in capsys.readouterr().err


def test_render_is_quiet_with_georgia_or_with_no_italic_cue(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    looked: list[bool] = []

    def missing() -> ItalicFont:
        looked.append(True)
        return ItalicFont(None)

    monkeypatch.setattr(cap, "find_italic_font", missing)
    assert italic_font_warning([Cue(0, 1, "Go")]) == ""
    assert looked == []  # no italic cue: no lookup at all

    monkeypatch.setattr(
        cap, "find_italic_font", lambda: ItalicFont(Path("/f/Georgia Italic.ttf"))
    )
    assert italic_font_warning([Cue(0, 1, "Behind", italic=True)]) == ""
    assert capsys.readouterr().err == ""
