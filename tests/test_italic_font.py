"""The heard-not-seen caption face: the house face slanted (Arial Bold Italic), its lookup and warning.

Since 9 Oct 2026 (Sweet Racket ep 5 "Ransom": a yellow Georgia italic serif
line among Arial Bold captions) a voice heard, not seen is set in the house
face's own bold italic, so one episode never mixes caption fonts.
"""

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


def test_the_heard_not_seen_face_is_the_house_face() -> None:
    """The one switch: the italic face IS the house face, bold, at the same em."""

    assert cap.ITALIC_FONT_NAME == cap.FONT_NAME == "Arial"
    assert cap.ITALIC_IS_HOUSE_FACE
    assert cap.ITALIC_BOLD_FLAG == -1
    assert cap.italic_size(45) == 45 and cap.italic_size(64) == 64
    assert "Georgia Italic.ttf" not in cap.ITALIC_FONT_FILES
    assert "Arial Bold Italic.ttf" in cap.ITALIC_FONT_FILES


def test_macos_finds_arial_bold_italic_in_the_system_font_folder(
    tmp_path: Path,
) -> None:
    supplemental = tmp_path / "Supplemental"
    italic = _font(supplemental, "Arial Bold Italic.ttf")
    regular = _font(supplemental, "Arial Bold.ttf")

    font = find_italic_font(
        platform="darwin", font_dirs=[tmp_path / "bundled", supplemental], match=_no_fc
    )

    assert font == ItalicFont(italic, regular) and font.ok
    assert font.warning() is None


def test_macos_without_arial_says_the_bundled_twin_is_used(tmp_path: Path) -> None:
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
    assert warning.startswith("Arial Bold Italic not found;"), warning
    assert (
        "bundled Liberation Sans Bold Italic, like every other caption line" in warning
    )
    assert "Restore Standard Fonts" in warning


def test_macos_does_not_trust_fontconfig_for_the_italic(tmp_path: Path) -> None:
    # CoreText, not fontconfig, draws the caption on macOS: a fontconfig hit is not proof.
    font = find_italic_font(
        platform="darwin",
        font_dirs=[tmp_path],
        match=lambda _p: ("Arial", "Bold Italic", "/opt/fc/Arial Bold Italic.ttf"),
    )
    assert not font.ok


def test_linux_resolves_through_fontconfig(tmp_path: Path) -> None:
    patterns: list[str] = []

    def fc(pattern: str) -> tuple[str, str, str]:
        patterns.append(pattern)
        return (
            "Arial",
            "Bold Italic",
            "/usr/share/fonts/truetype/msttcorefonts/arialbi.ttf",
        )

    font = find_italic_font(platform="linux", font_dirs=[tmp_path], match=fc)

    assert patterns == ["Arial:bold:italic"]
    assert font.ok
    assert font.italic == Path("/usr/share/fonts/truetype/msttcorefonts/arialbi.ttf")


def test_linux_without_arial_says_the_bundled_twin_is_used(tmp_path: Path) -> None:
    font = find_italic_font(
        platform="linux",
        font_dirs=[tmp_path],
        match=lambda _p: (
            "DejaVu Sans",
            "Bold Oblique",
            "/usr/share/fonts/DejaVuSans-BoldOblique.ttf",
        ),
    )

    assert not font.ok
    warning = font.warning() or ""
    assert "Liberation Sans Bold Italic" in warning
    assert "ttf-mscorefonts-installer" in warning


def test_linux_arial_bold_only_is_a_synthetic_slant_warning(tmp_path: Path) -> None:
    font = find_italic_font(
        platform="linux",
        font_dirs=[tmp_path],
        match=lambda _p: ("Arial", "Bold", "/fonts/arialbd.ttf"),
    )
    assert not font.ok
    assert "only /fonts/arialbd.ttf" in (font.warning() or "")


def test_an_arial_bold_italic_in_the_bundled_fonts_dir_wins(tmp_path: Path) -> None:
    italic = _font(tmp_path, "Arial Bold Italic.ttf")
    font = find_italic_font(platform="linux", font_dirs=[tmp_path], match=_no_fc)
    assert font.italic == italic


def test_render_warns_on_stderr_when_an_italic_cue_falls_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cap, "find_italic_font", lambda: ItalicFont(None, None, "X (/a/X.ttf)")
    )

    warning = italic_font_warning(
        [Cue(0, 1, "Go"), Cue(1, 2, "Behind you.", italic=True)]
    )

    assert warning.startswith("FONT: Arial Bold Italic not found"), warning
    assert f"WARNING {warning}" in capsys.readouterr().err


def test_render_is_quiet_with_the_italic_or_with_no_italic_cue(
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
        cap, "find_italic_font", lambda: ItalicFont(Path("/f/Arial Bold Italic.ttf"))
    )
    assert italic_font_warning([Cue(0, 1, "Behind", italic=True)]) == ""
    assert capsys.readouterr().err == ""
