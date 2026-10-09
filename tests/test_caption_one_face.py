"""One episode never mixes caption fonts (Sweet Racket ep 5 "Ransom", 9 Oct 2026).

The ep 5 master showed the hook line in Arial Bold and a later dialogue line
("Bring the car around.") in a yellow Georgia italic serif. The cause was the
kit's own heard-not-seen style (a line flagged ``off_screen`` was set in
Georgia italic), and on a portrait take a stale ``off_screen`` flag was never
checked against what the take draws (letterbox takes were). The fixes:

- the heard-not-seen style is the house face's own bold italic (one face);
- a portrait take sets a flagged line upright when the take draws its speaker;
- a laptop without Arial Bold burns every line in the bundled Liberation Sans
  (Arial's metric twin, the app's caption face) and says so, instead of
  letting libass pick a different fallback per style;
- ``--caption-scale`` makes captions bigger without a hand re-burn
  (Sweet Racket L-20261006-8, L-20261008-5).
"""

from __future__ import annotations

import copy
import io
import json
import re
from pathlib import Path

import pytest
from conftest import make_take, needs_ffmpeg
from test_post_finish import FACTS, _board, fake_bed, fake_sfx

from creation import captions as cap
from creation.caption_bold import build_bold_ass
from creation.captions import (
    FONTS_DIR,
    SUBSTITUTE_FONT_FILES,
    Cue,
    build_ass,
    burnable_ass,
    check_caption_scale,
    letterbox_band,
    one_face_ass,
)
from creation.post.finish import caption_italic_overrides, run_finish

CUES = [
    Cue(0.0, 1.0, "Someone kidnapped the boss's kitten."),
    Cue(1.0, 2.0, "Bring the car around.", italic=True),
]


def _faces(ass: str) -> set[str]:
    """Every face an ASS file names: its styles' Fontname and any ``\\fn`` tag."""

    styles = {
        line.split(",")[1] for line in ass.splitlines() if line.startswith("Style:")
    }
    tags = set(re.findall(r"\\fn([^\\}]+)", ass))
    return styles | tags


@pytest.mark.parametrize(
    "build",
    [
        lambda: build_ass(CUES, width=768, height=1344, style="house"),
        lambda: build_ass(CUES, width=768, height=1344, style="plain"),
        lambda: build_bold_ass(CUES, width=768, height=1344),
        lambda: build_ass(
            CUES,
            width=1080,
            height=1920,
            style="house",
            band=letterbox_band(1080, 1920),
        ),
    ],
    ids=["subtle", "plain", "bold", "letterbox"],
)
def test_every_caption_style_names_one_face_for_upright_and_heard_lines(build) -> None:
    ass = build()
    assert _faces(ass) == {"Arial"}, ass
    italic = next(
        line for line in ass.splitlines() if line.startswith("Style: Italic,")
    )
    assert italic.split(",")[7:9] == ["-1", "-1"], "bold italic of the house face"


# --- a laptop without Arial: one bundled face, said loudly --------------------------------------


def test_the_bundled_twin_ships_with_the_kit() -> None:
    from PIL import ImageFont

    for name in SUBSTITUTE_FONT_FILES:
        path = FONTS_DIR / name
        assert path.is_file(), path
        family, style = ImageFont.truetype(str(path), 20).getname()
        assert family == "Liberation Sans" and "Bold" in style
    assert (FONTS_DIR / "OFL-LiberationSans.txt").is_file()


def test_without_arial_every_line_is_burned_in_the_bundled_twin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cap, "_substitute_said", [])
    ass = tmp_path / "take-cap-v1.ass"
    text = build_ass(CUES, width=768, height=1344) + (
        "Dialogue: 0,0:00:03.00,0:00:04.00,House,,0,0,0,,{\\fnArial}x\n"
    )
    ass.write_text(text, encoding="utf-8")

    burn, scratch = burnable_ass(ass, house_face=False)

    assert scratch == burn and burn != ass
    assert _faces(burn.read_text(encoding="utf-8")) == {"Liberation Sans"}
    assert ass.read_text(encoding="utf-8") == text, (
        "the desk's .ass keeps the house face"
    )
    assert "WARNING FONT:" in capsys.readouterr().err
    assert (
        one_face_ass("Style: Hook,Arial,62,x\n") == "Style: Hook,Liberation Sans,62,x\n"
    )


def test_with_arial_the_file_is_burned_as_written(tmp_path: Path) -> None:
    ass = tmp_path / "a.ass"
    ass.write_text(build_ass(CUES, width=768, height=1344), encoding="utf-8")
    assert burnable_ass(ass, house_face=True) == (ass, None)


def test_without_arial_or_the_bundled_twin_nothing_is_burned(tmp_path: Path) -> None:
    ass = tmp_path / "a.ass"
    ass.write_text(build_ass(CUES, width=768, height=1344), encoding="utf-8")
    with pytest.raises(RuntimeError, match="nothing was burned"):
        burnable_ass(ass, fonts_dir=tmp_path / "empty", house_face=False)


def test_measuring_without_arial_uses_the_twin_not_a_wider_face() -> None:
    import inspect

    source = inspect.getsource(cap._measuring_font)
    assert "SUBSTITUTE_FONT_FILES" in source and "Poppins" not in source


# --- portrait takes check a stale off_screen flag like letterbox takes ----------------------------

SPINE = {
    "cast": [{"cast_id": "cast_boss", "name": "Boss"}, {"cast_id": "cast_gin", "name": "Gin"}],
    "frames": [{"frame_id": "f1", "storyboard_group_id": "s1", "take_shot": 1, "cast_refs": ["cast_gin"]}],
    "beats": [{"frame_id": "f1", "dialogue_lines": [
        {"line_id": "l1", "cast_id": "cast_boss", "off_screen": True},
        {"line_id": "l2", "cast_id": "cast_gin", "off_screen": True},
    ]}],
}  # fmt: skip


def test_a_flagged_line_whose_speaker_the_take_draws_is_upright_from_the_take_facts() -> (
    None
):
    facts = {"take_facts": {
        "shots": [{"shot_index": 1, "start_seconds": 0.0, "end_seconds": 4.0,
                   "people": {"named": ["cast_boss"]}}],
        "lines": [{"line_id": "l1", "count": 1, "start_seconds": 0.5, "end_seconds": 1.5},
                  {"line_id": "l2", "count": 1, "start_seconds": 2.0, "end_seconds": 3.0}],
    }}  # fmt: skip

    overrides, notes = caption_italic_overrides(SPINE, facts)

    assert overrides == {"l1": False, "l2": False}, notes
    assert all(n.startswith("italics: ") for n in notes)


def test_a_flagged_line_nothing_says_is_drawn_stays_italic() -> None:
    overrides, notes = caption_italic_overrides(
        {
            **SPINE,
            "frames": [{"frame_id": "f1", "storyboard_group_id": "s1", "take_shot": 1}],
        },
        {
            "take_facts": {
                "lines": [
                    {
                        "line_id": "l1",
                        "count": 1,
                        "start_seconds": 0.5,
                        "end_seconds": 1.5,
                    }
                ]
            }
        },
    )
    assert overrides == {}
    assert any("l1: italic" in n for n in notes)


@needs_ffmpeg
def test_portrait_finish_sets_a_stale_off_screen_line_upright(post_desk: Path) -> None:
    api = post_desk / "ep01" / "api"
    spine = json.loads((api / "03_spine.json").read_text(encoding="utf-8"))
    spine["beats"][0]["dialogue_lines"][0]["off_screen"] = True
    speaker = spine["beats"][0]["dialogue_lines"][0]["cast_id"]
    (api / "03_spine.json").write_text(json.dumps(spine), encoding="utf-8")
    facts = copy.deepcopy(FACTS)
    facts["take_facts"]["shots"][0]["people"] = {"count": 1, "named": [speaker]}
    line_id = spine["beats"][0]["dialogue_lines"][0]["line_id"]
    facts["take_facts"]["lines"] = [
        {"line_id": line_id, "count": 1, "start_seconds": 1.0, "end_seconds": 2.0}
    ]
    (api / "take-facts-ep01-t1-v1.json").write_text(json.dumps(facts))
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
              tones=((1.0, 2.0, 440), (3.2, 4.0, 880)))  # fmt: skip
    _board(post_desk)
    out = io.StringIO()

    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
                        cut_meter=lambda _take: (2.5,), stream=out)  # fmt: skip

    log = out.getvalue()
    assert result.complete, log
    assert "upright although flagged off screen" in log, log
    takes = post_desk / "ep01" / "takes"
    ass = next(iter(sorted(takes.glob("take-ep01-t1-cap-v*.ass")))).read_text(
        encoding="utf-8"
    )
    assert not [
        line
        for line in ass.splitlines()
        if line.startswith("Dialogue:") and ",Italic," in line
    ], ass
    assert _faces(ass) <= {"Arial"}


# --- --caption-scale: bigger captions, kit-placed, no hand re-burn ------------------------------


def test_caption_scale_one_is_byte_for_byte_today() -> None:
    for style in ("house", "plain"):
        assert build_ass(
            CUES, width=768, height=1344, style=style, size_scale=1.0
        ) == build_ass(CUES, width=768, height=1344, style=style)
    assert build_bold_ass(
        CUES, width=768, height=1344, size_scale=1.0
    ) == build_bold_ass(CUES, width=768, height=1344)


def test_caption_scale_sets_every_line_bigger_and_still_wraps() -> None:
    def size(ass: str, name: str) -> int:
        line = next(
            row for row in ass.splitlines() if row.startswith(f"Style: {name},")
        )
        return int(line.split(",")[2])

    big = build_ass(CUES, width=768, height=1344, size_scale=1.25)
    assert size(big, "House") == 56 and size(big, "Italic") == 56  # 45 x 1.25
    assert "\\N" in big, "a long line wraps; no sed-typed line breaks"
    assert (
        size(build_bold_ass(CUES, width=768, height=1344, size_scale=1.25), "Bold")
        == 80
    )
    band = build_ass(
        CUES, width=1080, height=1920, band=letterbox_band(1080, 1920), size_scale=1.25
    )
    assert size(band, "House") == round(letterbox_band(1080, 1920).size * 1.25)


def test_caption_scale_outside_its_range_stops_before_anything_is_burned() -> None:
    assert check_caption_scale(1.25) == 1.25
    for bad in (0.2, 2.5):
        with pytest.raises(ValueError, match="nothing was burned"):
            check_caption_scale(bad)


def test_finish_and_caption_take_the_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation import cli_post, cli_produce

    seen: dict[str, object] = {}

    def fake_finish(desk: Path, **kwargs: object) -> object:
        seen["finish"] = kwargs["caption_scale"]
        raise RuntimeError("stop here")

    monkeypatch.setattr(cli_post, "run_finish", fake_finish)
    cli_produce.main(["finish", "--desk", str(tmp_path), "--caption-scale", "1.3"])
    assert seen["finish"] == 1.3
    with pytest.raises(SystemExit):
        cli_produce.main(["caption", "--help"])
    assert "--caption-scale" in capsys.readouterr().out
