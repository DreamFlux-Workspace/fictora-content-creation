"""Bold captions (new portrait default) and Subtle (today's house flicker), chosen once per show (6 Oct 2026)."""

from __future__ import annotations

import argparse
import io
import json
import shutil
from pathlib import Path

import pytest

from creation.caption_bold import (
    BOLD_MAX_CHARS,
    BOLD_MAX_WORDS,
    bold_font_size,
    bold_from_flicker,
    bold_margin_v,
    build_bold_ass,
    marked_index,
    pick_emphasis,
    spine_emphasis,
    word_key,
)
from creation.caption_dashes import DASH_IN_CAPTION
from creation.captions import (
    Cue,
    LetterboxBand,
    Span,
    build_ass,
    build_line_cues,
    episode_caption_lines,
    italic_size,
    resolve_caption_style,
    side_margin,
    text_width,
)
from creation.production_config import (
    ProductionConfig,
    load_production_config,
    save_production_config,
)

YELLOW = r"{\c&H0000E5FF&}"
HIDDEN = r"{\alpha&HFF&}"


def _style(ass: str, name: str) -> list[str]:
    return ass.split(f"Style: {name},")[1].split("\n")[0].split(",")


def _events(ass: str) -> list[list[str]]:
    return [
        line.split(":", 1)[1].split(",", 9)
        for line in ass.splitlines()
        if line.startswith("Dialogue:")
    ]


def _bold(lines: list[str], spans: list[Span], **kw: object) -> list[Cue]:
    groups = build_line_cues(lines, spans, chunking="bold", **kw)  # type: ignore[arg-type]
    return [c for g in groups for c in g]


# --- geometry -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("width", "height", "size", "outline", "margin_v"),
    [
        (1080, 1920, 92, 7, 461),  # 64 x 1.44 on the house canvas, bottom at 76%
        (768, 1344, 64, 5, 323),  # the H3 take: the comparison reel's numbers
        (720, 1280, 61, 5, 307),
    ],
)
def test_bold_is_arial_bold_92_on_1920_white_bottom_at_76_percent_scaled_to_the_frame(
    width: int, height: int, size: int, outline: int, margin_v: int
) -> None:
    ass = build_ass(
        [Cue(0, 1, "Go", chunk="Go", shown=1)], width=width, height=height, style="bold"
    )
    bold = _style(ass, "Bold")
    assert bold[0] == "Arial" and bold[1] == str(size) == str(bold_font_size(height))
    assert bold[2] == bold[3] == "&H00FFFFFF"  # white; the one yellow word is a tag
    assert bold[6] == "-1"
    assert bold[15] == str(outline)
    assert bold[17:21] == [
        "2",
        str(side_margin(width)),
        str(side_margin(width)),
        str(margin_v),
    ]
    assert bold_margin_v(height) == margin_v
    assert round(height - margin_v) / height == pytest.approx(0.76, abs=0.002)


def test_bold_is_about_1_44_times_subtle() -> None:
    house = int(_style(build_ass([], width=1080, height=1920), "House")[1])
    assert bold_font_size(1920) / house == pytest.approx(1.44, abs=0.01)


def test_inner_voice_keeps_georgia_italic_at_bold_size_and_place() -> None:
    ass = build_bold_ass(
        [Cue(0, 1, "Run", italic=True, chunk="Run", shown=1)], width=768, height=1344
    )
    italic = _style(ass, "Italic")
    assert italic[0] == "Georgia" and italic[1] == str(italic_size(64))
    assert italic[7] == "-1" and italic[2] == "&H00FFFFFF"
    assert italic[15:] == _style(ass, "Bold")[15:]
    assert _events(ass)[0][3] == "Italic"


# --- one short line at a time ------------------------------------------------------------------


LINES = [
    "No matter what happens... do not break eye contact.",
    "Please— open the door!",
    "I never thought I would see you standing here again tonight",
]


def test_every_chunk_is_one_short_line_of_two_or_three_words() -> None:
    cues = _bold(LINES, [Span(0, 5), Span(6, 8), Span(9, 14)])
    chunks = list(dict.fromkeys(c.chunk for c in cues))
    for chunk in chunks:
        words = chunk.split()
        assert 1 <= len(words) <= BOLD_MAX_WORDS
        if len(words) > 1:
            assert len(" ".join(word_key(w) for w in words)) <= BOLD_MAX_CHARS
    assert chunks[:4] == [
        "No matter",
        "what happens...",
        "do not break",
        "eye contact.",
    ]
    assert "open the door!" in chunks
    # A clause mark ends a chunk; one word is never left alone when it can join.
    assert sum(len(c.split()) == 1 for c in chunks[5:]) == 0
    ass = build_ass(cues, width=768, height=1344, style="bold")
    assert "\\N" not in ass  # never two lines
    for event in _events(ass):
        assert event[9].count("\\fs") <= 1


def test_a_chunk_too_wide_is_set_smaller_never_wrapped() -> None:
    word = "Pneumonoultramicroscopicsilicovolcanoconiosis"
    ass = build_bold_ass([Cue(0, 1, word, chunk=word, shown=1)], width=768, height=1344)
    text = _events(ass)[0][9]
    assert "\\N" not in text and "\\fs" in text
    size = int(text.split("\\fs")[1].split("}")[0].split("\\")[0])
    assert text_width(word, size) <= 768 - 2 * side_margin(768)


def test_each_word_appears_when_it_is_said_and_the_chunk_never_moves() -> None:
    said = [Cue(1.00, 1.30, "Open"), Cue(1.42, 1.60, "the"), Cue(1.95, 2.40, "door!")]
    cues = build_line_cues(
        ["Open the door!"], [Span(1.0, 2.4)], chunking="bold", word_cues=[said]
    )[0]
    assert [c.start for c in cues] == [w.start for w in said]
    for cue, word in zip(cues, said):
        assert (
            0 <= word.start - cue.start <= 0.08
        )  # never more than 80 ms early, never late
    assert {c.chunk for c in cues} == {"Open the door!"}
    events = [
        e[9] for e in _events(build_ass(cues, width=768, height=1344, style="bold"))
    ]
    assert (
        events[0] == f"Open {HIDDEN}the door!"
    )  # laid out whole, unsaid words transparent
    assert events[1] == f"Open the {HIDDEN}door!"
    assert events[2] == f"Open the {YELLOW}door!{{\\c&H00FFFFFF&}}"


# --- the yellow word ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chunk", "index"),
    [
        (["open", "the", "door!"], 2),
        (["do", "not", "break"], 2),
        (["I'm", "NOT…"], 1),
        (["Please…"], 0),  # one content word: yellow
        (["I'm"], -1),  # one function word: all white
        (["of", "the"], -1),
        (["STOP", "right", "there"], 0),  # shouted wins
    ],
)
def test_the_heuristic_picks_the_main_word_and_skips_function_words(
    chunk: list[str], index: int
) -> None:
    assert pick_emphasis(chunk) == index


def test_the_writer_marked_emphasis_on_the_spine_wins_in_its_chunk() -> None:
    spine = {
        "beats": [
            {
                "episode_id": "episode_01",
                "dialogue_lines": [
                    {"text": "Open the door now.", "emphasis_word": "now"},
                    {"text": "Open the door now."},
                ],
            }
        ]
    }
    lines = episode_caption_lines(spine, 1)
    assert [line.emphasis for line in lines] == ["now", None]
    groups = build_line_cues(
        [line.text for line in lines], [Span(0, 2), Span(3, 5)], chunking="bold",
        emphasis=[line.emphasis for line in lines],
    )  # fmt: skip
    marked, heuristic = groups[0][-1], groups[1][-1]
    assert marked.chunk.split()[marked.emphasis] == "now."
    assert (
        heuristic.chunk.split()[heuristic.emphasis] == "now."
    )  # also the last content word
    # A word that is not the last content word: the spine moves the yellow.
    cues = build_line_cues(
        ["Open the door!"], [Span(0, 2)], chunking="bold", emphasis=["Open"]
    )[0]
    assert cues[-1].emphasis == 0
    assert marked_index(["Open", "the", "door!"], 2) == 2
    assert spine_emphasis({"emphasis": "door"}) == "door"
    assert spine_emphasis({}) is None


# --- no dashes, Subtle unchanged, letterbox unchanged ----------------------------------------


def test_bold_draws_no_em_or_en_dash() -> None:
    cues = _bold(
        ["Please— open the door—", "D-9341 — do not move."], [Span(0, 3), Span(4, 7)]
    )
    ass = build_ass(cues, width=768, height=1344, style="bold")
    body = "\n".join(e[9] for e in _events(ass))
    assert not DASH_IN_CAPTION.search(body)
    assert "D-9341" in body  # a hyphen inside a word stays
    assert "Please…" in body


#: Today's house ASS for this fixture, written by origin/main before Bold (8faeaaa).
SUBTLE_GOLDEN = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 768\nPlayResY: 1344\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
    "MarginL, MarginR, MarginV, Encoding\n"
    "Style: House,Arial,45,&H0000E5FF,&H0000E5FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,43,43,511,1\n"
    "Style: Italic,Georgia,46,&H0000E5FF,&H0000E5FF,&H00000000,&H80000000,0,-1,0,0,100,100,0,0,1,4,1,2,43,43,511,1\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:00.50,0:00:00.87,House,,0,0,0,,Open\n"
    "Dialogue: 0,0:00:00.87,0:00:01.17,House,,0,0,0,,Open the\n"
    "Dialogue: 0,0:00:01.17,0:00:01.54,House,,0,0,0,,Open the door\n"
    "Dialogue: 0,0:00:01.54,0:00:01.91,House,,0,0,0,,now,\n"
    "Dialogue: 0,0:00:01.91,0:00:02.65,House,,0,0,0,,now, please.\n"
    "Dialogue: 0,0:00:03.00,0:00:03.20,Italic,,0,0,0,,I\n"
    "Dialogue: 0,0:00:03.20,0:00:03.70,Italic,,0,0,0,,I hear\n"
    "Dialogue: 0,0:00:03.70,0:00:04.35,Italic,,0,0,0,,I hear you.\n"
)


@pytest.mark.parametrize("style", ["subtle", "house"])
def test_subtle_is_todays_house_style_byte_for_byte(style: str) -> None:
    groups = build_line_cues(
        ["Open the door now, please.", "I hear you."], [Span(0.5, 2.5), Span(3.0, 4.2)],
        italic=[False, True],
    )  # fmt: skip
    cues = [c for g in groups for c in g]
    assert build_ass(cues, width=768, height=1344, style=style) == SUBTLE_GOLDEN


def test_letterbox_keeps_its_own_layout_whatever_the_style() -> None:
    band = LetterboxBand(80, 950, 1417, 1380, 1560, 62, 48)
    cues = [Cue(0.0, 1.0, "Open the door")]
    house = build_ass(cues, width=1080, height=1920, style="house", band=band)
    assert build_ass(cues, width=1080, height=1920, style="bold", band=band) == house


# --- reels: Bold read back and rebuilt ---------------------------------------------------------


def test_a_bold_file_read_back_by_the_reel_rebuilds_the_same_captions() -> None:
    from creation.post.reel import parse_ass_cues

    cues = _bold(
        ["Open the door now.", "Please wait."],
        [Span(0, 2), Span(2.5, 4)],
        emphasis=["Open", None],
    )
    ass = build_ass(cues, width=768, height=1344, style="bold")
    read = parse_ass_cues(ass)
    assert all(HIDDEN not in c.text for c in read)
    assert [c.text for c in read] == [c.text for c in cues]  # what shows, word by word
    again = build_ass(read, width=768, height=1344, style="bold")
    assert _events(again) == _events(ass)


def test_house_flicker_cues_become_bold_for_a_reel() -> None:
    flicker = build_line_cues(["Do not break eye contact."], [Span(0, 2)])[0]
    bold = bold_from_flicker(flicker)
    assert [c.start for c in bold] == [c.start for c in flicker]
    assert list(dict.fromkeys(c.chunk for c in bold)) == [
        "Do not break",
        "eye contact.",
    ]


# --- the style per show ----------------------------------------------------------------------


def _finished(desk: Path) -> None:
    takes = desk / "ep01" / "takes"
    takes.mkdir(parents=True)
    (takes / "take-ep01-t1-finish-v1.json").write_text("{}")


def test_a_new_show_is_bold_and_a_continuing_show_keeps_subtle(tmp_path: Path) -> None:
    new, old = tmp_path / "new", tmp_path / "old"
    new.mkdir()
    _finished(old)
    assert resolve_caption_style(new) == ("bold", "")
    assert resolve_caption_style(old) == ("house", "")
    # An older desk saved "house" when that was the default: it stays Subtle.
    save_production_config(new, ProductionConfig(caption_style="house"))
    assert resolve_caption_style(new) == ("house", "")


def test_the_flag_overrides_the_show(tmp_path: Path) -> None:
    save_production_config(tmp_path, ProductionConfig(caption_style="bold"))
    assert resolve_caption_style(tmp_path, "subtle") == ("house", "")
    assert resolve_caption_style(tmp_path, "plain") == ("plain", "")
    save_production_config(tmp_path, ProductionConfig(caption_style="subtle"))
    assert resolve_caption_style(tmp_path) == ("house", "")
    assert resolve_caption_style(tmp_path, "bold") == ("bold", "")


@pytest.mark.parametrize("command", ["finish", "reel"])
def test_finish_and_reel_take_bold_and_subtle(command: str) -> None:
    from creation.cli_post import add_post_parsers

    parser = argparse.ArgumentParser()
    add_post_parsers(parser.add_subparsers(dest="command"))
    for style in ("bold", "subtle", "house"):
        args = parser.parse_args(
            [command, "--desk", "/d", "--episode", "1", "--caption-style", style]
        )
        assert args.caption_style == style


def test_caption_takes_subtle_and_none_burns_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation.cli_produce import main

    assert main(["caption", "--desk", str(tmp_path), "--caption-style", "none"]) == 0
    with pytest.raises(SystemExit):
        main(["caption", "--desk", str(tmp_path), "--caption-style", "loud"])
    assert "invalid choice" in capsys.readouterr().err


def test_start_leaves_the_style_unchosen_and_server_captions_keep_house() -> None:
    from creation.cli_config import add_production_config_args, config_from_args

    parser = argparse.ArgumentParser()
    add_production_config_args(parser)
    assert config_from_args(parser.parse_args([])).caption_style is None
    assert (
        config_from_args(parser.parse_args(["--api-captions"])).caption_style == "house"
    )
    with pytest.raises(ValueError, match="local caption style"):
        config_from_args(
            parser.parse_args(["--caption-style", "bold", "--api-captions"])
        )


# --- asked once at the look approval -------------------------------------------------------------


def _desk_with_frame(desk: Path) -> None:
    from PIL import Image

    look = desk / "shared" / "look"
    look.mkdir(parents=True)
    Image.new("RGB", (192, 336), (40, 40, 60)).save(look / "look-frame-v1.png")
    api = desk / "ep01" / "api"
    api.mkdir(parents=True)
    spine = {
        "beats": [
            {
                "episode_id": "episode_01",
                "dialogue_lines": [{"text": "Do not break eye contact."}],
            }
        ]
    }
    (api / "spine.json").write_text(json.dumps(spine))


@pytest.fixture
def drawn(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, str, Path]]:
    from creation import caption_preview

    calls: list[tuple[Path, str, Path]] = []

    def fake(image: Path, line: str, out: Path, **_: object) -> Path:
        calls.append((image, line, out))
        out.write_bytes(b"png")
        return out

    monkeypatch.setattr(caption_preview, "render_caption_preview", fake)
    return calls


def test_a_new_show_gets_one_free_preview_and_bold_saved(
    tmp_path: Path, drawn: list[tuple[Path, str, Path]]
) -> None:
    from creation.caption_preview import SWITCH_PROMPT, caption_style_at_look

    _desk_with_frame(tmp_path)
    out = io.StringIO()
    preview = caption_style_at_look(tmp_path, out=out)
    assert preview is not None and preview.name == "caption-preview-v1.png"
    assert drawn == [
        (
            tmp_path / "shared/look/look-frame-v1.png",
            "Do not break eye contact.",
            preview,
        )
    ]
    assert SWITCH_PROMPT in out.getvalue()
    assert load_production_config(tmp_path).caption_style == "bold"
    # Asked once: a second approval (a redrawn look frame) draws nothing and keeps the choice.
    assert caption_style_at_look(tmp_path, out=io.StringIO()) is None
    assert len(drawn) == 1


def test_a_continuing_show_is_not_asked_and_keeps_subtle(
    tmp_path: Path, drawn: list[tuple[Path, str, Path]]
) -> None:
    from creation.caption_preview import caption_style_at_look

    _desk_with_frame(tmp_path)
    _finished(tmp_path)
    out = io.StringIO()
    assert caption_style_at_look(tmp_path, out=out) is None
    assert drawn == []
    assert load_production_config(tmp_path).caption_style == "subtle"
    assert "keeps Subtle" in out.getvalue()


def test_a_show_that_already_chose_is_never_asked(
    tmp_path: Path, drawn: list[tuple[Path, str, Path]]
) -> None:
    from creation.caption_preview import caption_style_at_look, run_caption_style

    _desk_with_frame(tmp_path)
    run_caption_style(tmp_path, set_to="subtle", out=io.StringIO())
    assert caption_style_at_look(tmp_path, out=io.StringIO()) is None
    assert drawn == [] and load_production_config(tmp_path).caption_style == "subtle"
    assert resolve_caption_style(tmp_path) == ("house", "")


def test_no_frame_previews_on_the_first_plate(tmp_path: Path) -> None:
    from PIL import Image

    from creation.caption_preview import preview_image

    plates = tmp_path / "shared" / "plates"
    plates.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(plates / "plate-a-v1.png")
    assert preview_image(tmp_path) == plates / "plate-a-v1.png"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_the_preview_still_is_both_styles_side_by_side(tmp_path: Path) -> None:
    from PIL import Image

    from creation.caption_preview import render_caption_preview

    _desk_with_frame(tmp_path)
    try:
        out = render_caption_preview(
            tmp_path / "shared/look/look-frame-v1.png",
            "Do not break eye contact.",
            tmp_path / "p.png",
        )
    except RuntimeError as exc:  # an ffmpeg without libass
        pytest.skip(str(exc))
    with Image.open(out) as still:
        assert still.size == (384, 336)
    with pytest.raises(FileExistsError):
        render_caption_preview(tmp_path / "shared/look/look-frame-v1.png", "x", out)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_caption_take_burns_bold_on_a_new_show(tmp_path: Path) -> None:
    import subprocess

    from creation.captions import caption_take

    ep = tmp_path / "ep01"
    (ep / "api").mkdir(parents=True)
    (ep / "takes").mkdir()
    spine = {
        "beats": [
            {
                "episode_id": "episode_01",
                "dialogue_lines": [{"text": "Open the door now!"}],
            }
        ]
    }
    (ep / "api" / "spine.json").write_text(json.dumps(spine))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=192x336:d=4",
         "-f", "lavfi", "-i", "sine=f=440:d=4",
         "-filter_complex", "[1:a]volume='if(between(t,1,2.5),1,0)':eval=frame[a]",
         "-map", "0:v", "-map", "[a]", "-shortest", "-c:v", "libx264", "-c:a", "aac",
         str(ep / "takes" / "take-ep01-t1-raw-v1.mp4")],
        check=True,
    )  # fmt: skip
    style, _ = resolve_caption_style(tmp_path)
    result = caption_take(tmp_path, style=style)
    ass = result.ass.read_text()
    assert style == "bold" and "Style: Bold,Arial," in ass and YELLOW in ass
    assert result.video.is_file()
