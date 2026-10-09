"""House style is the kit default (L-20260928-4) and plain / none captions (L-20260930-11)."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from creation import captions as cap
from creation import setup_check as sc
from creation.captions import (
    CAPTION_STYLES,
    Cue,
    HouseFont,
    build_ass,
    caption_take,
    cjk_font_name,
    find_house_font,
    house_font_size,
    italic_size,
    resolve_caption_style,
    side_margin,
    text_width,
    wrap_caption,
)
from creation.cli_config import add_production_config_args, config_from_args
from creation.production_config import ProductionConfig, save_production_config


def _style(ass: str, name: str) -> list[str]:
    return ass.split(f"Style: {name},")[1].split("\n")[0].split(",")


# --- size per canvas --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("width", "height", "size", "outline", "margin_x"),
    [
        (1080, 1920, 64, 5, 60),  # the house canvas: Arial Bold 64, outline 5
        (768, 1344, 45, 4, 43),  # the H3 take: the same look scaled by height, never 64
        (720, 1280, 43, 3, 40),
    ],
)
def test_house_header_is_arial_bold_64_on_1920_scaled_to_the_frame(
    width: int, height: int, size: int, outline: int, margin_x: int
) -> None:
    ass = build_ass([], width=width, height=height)
    house = _style(ass, "House")
    assert house[0] == "Arial" and house[1] == str(size)
    assert house[2] == "&H0000E5FF"  # yellow
    assert house[6] == "-1"  # bold
    assert house[15] == str(outline)
    assert house[17:20] == ["2", str(margin_x), str(margin_x)]
    assert f"PlayResX: {width}\nPlayResY: {height}" in ass
    # One face for every line: the italic style draws at the same em as Arial Bold.
    assert _style(ass, "Italic")[1] == str(italic_size(size))


def test_64_is_only_the_size_on_a_1920_canvas() -> None:
    assert house_font_size(1920) == 64
    assert house_font_size(1344) == 45  # 64 typed onto a 1344 take is 42% too big


# --- wrapping -------------------------------------------------------------------------------


def test_a_line_that_fits_stays_one_line() -> None:
    assert wrap_caption("Go now.", 45, 768) == (["Go now."], 45)


def test_a_long_line_wraps_into_two_balanced_lines_at_full_size() -> None:
    text = "I never thought I would see you standing here again"
    lines, size = wrap_caption(text, 45, 768)
    assert size == 45 and len(lines) == 2 and " ".join(lines) == text
    room = 768 - 2 * side_margin(768)
    widths = [text_width(line, 45) for line in lines]
    assert max(widths) <= room
    # Balanced: no other break gives a narrower widest line.
    words = text.split()
    best = min(
        max(text_width(" ".join(words[:i]), 45), text_width(" ".join(words[i:]), 45))
        for i in range(1, len(words))
    )
    assert max(widths) == pytest.approx(best)


def test_only_a_caption_too_long_for_two_lines_is_set_smaller() -> None:
    text = " ".join(["unbelievable"] * 12)
    lines, size = wrap_caption(text, 45, 768)
    assert len(lines) == 2 and size < 45
    ass = build_ass([Cue(0.0, 1.0, text)], width=768, height=1344)
    assert f"{{\\fs{size}}}{lines[0]}\\N{lines[1]}" in ass


def test_a_wrapped_cue_is_broken_by_the_kit_not_libass() -> None:
    text = "I never thought I would see you standing here again"
    lines, _ = wrap_caption(text, 45, 768)
    ass = build_ass([Cue(0.0, 1.0, text)], width=768, height=1344)
    assert "WrapStyle: 2" in ass
    assert ass.rstrip().endswith(f"House,,0,0,0,,{lines[0]}\\N{lines[1]}")


# --- CJK: a CJK face per line ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "platform", "face"),
    [
        ("待って", "darwin", "Hiragino Sans"),
        ("기다려", "darwin", "Apple SD Gothic Neo"),
        ("待って", "linux", "Noto Sans CJK JP"),
        ("기다려", "linux", "Noto Sans CJK KR"),
    ],
)
def test_cjk_face_per_script(text: str, platform: str, face: str) -> None:
    assert cjk_font_name(text, platform=platform) == face


def test_a_cjk_cue_is_set_in_a_cjk_face_and_english_cues_stay_arial() -> None:
    ass = build_ass(
        [Cue(0.0, 1.0, "Wait."), Cue(1.0, 2.0, "待って", italic=True)],
        width=768,
        height=1344,
        platform="darwin",
    )
    assert "House,,0,0,0,,Wait.\n" in ass
    assert "Italic,,0,0,0,,{\\fnHiragino Sans}待って" in ass


def test_a_long_cjk_line_without_spaces_wraps_between_characters() -> None:
    text = "ここでずっと待っているからね、必ず帰ってきてね、約束だよ"
    lines, size = wrap_caption(text, 45, 768)
    assert len(lines) == 2 and "".join(lines) == text and size == 45


# --- plain / none ----------------------------------------------------------------------------


def test_plain_is_white_with_the_same_face_size_and_place() -> None:
    house = build_ass([], width=768, height=1344)
    plain = build_ass([Cue(0.0, 1.0, "Hi")], width=768, height=1344, style="plain")
    looks = _style(plain, "Plain")
    assert looks[2] == looks[3] == "&H00FFFFFF"
    assert looks[:2] == _style(house, "House")[:2]
    assert looks[4:] == _style(house, "House")[4:]
    assert _style(plain, "Italic")[2] == "&H00FFFFFF"
    assert "Plain,,0,0,0,,Hi" in plain


def test_none_is_never_rendered() -> None:
    with pytest.raises(ValueError, match="draws no captions"):
        build_ass([], width=768, height=1344, style="none")


def test_the_flag_wins_then_the_desk_config_then_the_show_default(
    tmp_path: Path,
) -> None:
    assert resolve_caption_style(tmp_path) == ("bold", "")  # a new show: Bold
    save_production_config(tmp_path, ProductionConfig(caption_style="plain"))
    assert resolve_caption_style(tmp_path) == ("plain", "")
    assert resolve_caption_style(tmp_path, "none") == ("none", "")
    with pytest.raises(ValueError, match="bold, subtle, house, plain, none"):
        resolve_caption_style(tmp_path, "karaoke")


def test_a_server_caption_style_on_the_desk_is_captioned_as_the_show_default_with_a_note(
    tmp_path: Path,
) -> None:
    save_production_config(tmp_path, ProductionConfig(caption_style="viral_karaoke"))
    style, note = resolve_caption_style(tmp_path)
    assert style == "bold" and "viral_karaoke" in note


def _config_args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_production_config_args(parser)
    return parser.parse_args(list(argv))


def test_desk_config_takes_plain_and_none_and_none_turns_server_captions_off() -> None:
    from creation.production_config import server_caption_style

    plain = config_from_args(_config_args("--caption-style", "plain"))
    assert plain.caption_style == "plain"
    assert server_caption_style(plain) == "house"
    silent = config_from_args(_config_args("--caption-style", "none"))
    assert silent.caption_style == "none"
    assert server_caption_style(silent) is None
    with pytest.raises(ValueError, match="no captions"):
        config_from_args(_config_args("--caption-style", "none", "--api-captions"))


def test_server_captions_are_on_by_default_and_off_with_the_flag() -> None:
    from creation.production_config import ProductionConfig, server_caption_style

    assert config_from_args(_config_args()).api_captions is True
    off = config_from_args(_config_args("--no-api-captions"))
    assert off.api_captions is False
    assert server_caption_style(off) is None
    assert server_caption_style(ProductionConfig(caption_style="viral_karaoke")) == (
        "viral_karaoke"
    )


def test_bind_keeps_the_saved_server_captions_unless_a_flag_is_given() -> None:
    from creation.cli_config import merge_config_from_args
    from creation.production_config import ProductionConfig

    saved = ProductionConfig(api_captions=False, caption_style="subtle")
    assert merge_config_from_args(saved, _config_args()).api_captions is False
    assert merge_config_from_args(saved, _config_args("--api-captions")).api_captions
    kept = merge_config_from_args(
        ProductionConfig(caption_style="subtle"), _config_args("--no-api-captions")
    )
    assert kept.api_captions is False
    assert kept.caption_style == "subtle"


@pytest.mark.parametrize("command", ["finish", "caption"])
def test_finish_and_caption_take_caption_style(command: str) -> None:
    from creation.cli_produce import main

    with pytest.raises(SystemExit) as stopped:
        main([command, "--desk", "/nowhere", "--caption-style", "loud"])
    assert (
        stopped.value.code == 2
    )  # argparse: not one of bold, subtle, house, plain, none
    assert CAPTION_STYLES == ("bold", "subtle", "house", "plain", "none")


def test_caption_none_burns_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation.cli_produce import main

    assert main(["caption", "--desk", str(tmp_path), "--caption-style", "none"]) == 0
    assert "nothing burned" in capsys.readouterr().out
    assert not (tmp_path / "ep01").exists()


def _tone_take(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=192x336:d=4",
         "-f", "lavfi", "-i", "sine=f=440:d=4",
         "-filter_complex", "[1:a]volume='if(between(t,1,2),1,0)':eval=frame[a]",
         "-map", "0:v", "-map", "[a]", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_plain_captions_an_english_show_with_whole_white_lines(tmp_path: Path) -> None:
    ep = tmp_path / "ep01"
    (ep / "api").mkdir(parents=True)
    (ep / "takes").mkdir()
    spine = {"beats": [{"episode_id": "episode_01",
                        "dialogue_lines": [{"text": "I'm coming for you."}]}]}  # fmt: skip
    (ep / "api" / "spine.json").write_text(json.dumps(spine))
    _tone_take(ep / "takes" / "take-ep01-t1-raw-v1.mp4")
    result = caption_take(tmp_path, style="plain")
    assert result.whole_lines
    assert [cue.text for cue in result.cues] == ["I'm coming for you."]
    ass = result.ass.read_text()
    assert "Style: Plain,Arial," in ass and "&H00FFFFFF" in ass
    with pytest.raises(ValueError, match="burns no captions"):
        caption_take(tmp_path, style="none")


# --- fonts -----------------------------------------------------------------------------------


def test_arial_bold_found_by_file(tmp_path: Path) -> None:
    (tmp_path / "Arial Bold.ttf").write_bytes(b"")
    font = find_house_font(
        platform="darwin", font_dirs=[tmp_path], match=lambda _: None
    )
    assert (
        font.ok and font.path == tmp_path / "Arial Bold.ttf" and font.warning() is None
    )


def test_missing_arial_on_linux_names_the_fallback(tmp_path: Path) -> None:
    font = find_house_font(
        platform="linux",
        font_dirs=[tmp_path],
        match=lambda _: (
            "Liberation Sans",
            "Bold",
            "/usr/share/fonts/LiberationSans-Bold.ttf",
        ),
    )
    assert not font.ok
    warning = font.warning() or ""
    assert "Arial Bold not found" in warning and "LiberationSans-Bold.ttf" in warning
    assert "ttf-mscorefonts-installer" in warning


def test_arial_from_fontconfig_on_linux(tmp_path: Path) -> None:
    font = find_house_font(
        platform="linux",
        font_dirs=[tmp_path],
        match=lambda _: ("Arial", "Bold", "/usr/share/fonts/arialbd.ttf"),
    )
    assert font.ok and font.path == Path("/usr/share/fonts/arialbd.ttf")


def test_render_warns_when_arial_bold_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cap, "find_house_font", lambda: HouseFont(None, "DejaVu Sans (/d.ttf)")
    )
    assert cap.house_font_warning([Cue(0, 1, "Hi")]).startswith(
        "FONT: Arial Bold not found"
    )
    assert "WARNING FONT" in capsys.readouterr().err
    assert cap.house_font_warning([Cue(0, 1, "Hi", italic=True)]) == ""


def test_setup_check_warns_without_arial_bold_and_never_fails() -> None:
    check = sc.check_house_font(lambda: HouseFont(None, "DejaVu Sans (/d.ttf)"))
    assert check.ok and check.warn
    assert check.line().startswith(
        "⚠ Arial Bold (house captions): Arial Bold not found"
    )
    present = sc.check_house_font(lambda: HouseFont(Path("/f/Arial Bold.ttf")))
    assert present.ok and not present.warn


def test_setup_check_lists_the_house_font(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def found() -> HouseFont:
        called.append("arial")
        return HouseFont(Path("/f/Arial Bold.ttf"))

    monkeypatch.setattr(sc, "find_house_font", found)
    assert sc.check_house_font().name == "Arial Bold (house captions)"
    assert called == ["arial"]


def test_arial_bold_found_in_the_windows_fonts_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L-20261001-42: Windows keeps Arial Bold as C:/Windows/Fonts/arialbd.ttf."""

    fonts = tmp_path / "Windows" / "Fonts"
    fonts.mkdir(parents=True)
    (fonts / "arialbd.ttf").write_bytes(b"")
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    monkeypatch.setattr(cap, "FONTS_DIR", tmp_path / "bundled")

    font = find_house_font(platform="win32", match=lambda _: None)

    assert font.ok and font.path == fonts / "arialbd.ttf", font


def test_arial_bold_installed_for_one_windows_user_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fonts = tmp_path / "Local" / "Microsoft" / "Windows" / "Fonts"
    fonts.mkdir(parents=True)
    (fonts / "arialbd.ttf").write_bytes(b"")
    monkeypatch.setenv("WINDIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(cap, "FONTS_DIR", tmp_path / "bundled")

    font = find_house_font(platform="win32", match=lambda _: None)

    assert font.path == fonts / "arialbd.ttf", font


def test_missing_arial_hint_covers_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WINDIR", str(tmp_path / "nowhere"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(cap, "FONTS_DIR", tmp_path / "bundled")

    warning = find_house_font(platform="win32", match=lambda _: None).warning() or ""

    assert "Arial Bold not found" in warning
    assert "Windows:" in warning and "arialbd.ttf" in warning, warning
