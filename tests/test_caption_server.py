"""finish and caption burn the server's caption layout (24 Oct 2026, fictora-drama #1029).

The kit times the words; the server places them (``POST /v1/captions/layout``)
and the file it returns is burned exactly as given. An older server (404) or
no connection falls back to the kit's own layout with a printed NOTE, never an
error. ``--caption-scale`` other than 1 keeps the kit's layout (the server
lays out the house size only).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from creation.caption_bold import bold_cues
from creation.caption_server import (
    CAPTION_LAYOUT_ROUTE,
    caption_chunks,
    server_caption_ass,
    server_override,
)
from creation.captions import (
    Cue,
    build_ass,
    caption_take,
    flicker_cues,
    portrait_caption_ass,
)

#: A burnable file standing in for the server's answer (marked so the test can tell it apart).
SERVER_ASS = (
    "[Script Info]\n; laid out by the server\nScriptType: v4.00+\nPlayResX: 192\nPlayResY: 336\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
    "MarginL, MarginR, MarginV, Encoding\n"
    "Style: bold,Arial,16,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,1,1,2,26,26,101,1\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:01.00,0:00:02.00,bold,,0,0,0,,Laid out by the server\n"
)


def _words(*pairs: tuple[str, float]) -> list[Cue]:
    return [Cue(start, start + 0.25, text) for text, start in pairs]


def test_bold_cues_become_one_chunk_per_bold_line_with_each_word_from_when_it_is_said() -> (
    None
):
    cues = bold_cues(_words(("Not", 1.0), ("for", 1.3), ("sale.", 1.6)), marked={2})

    (chunk,) = caption_chunks(cues, whole_lines=False)

    assert [w["text"] for w in chunk["words"]] == ["Not", "for", "sale."]
    assert [w["start_ms"] for w in chunk["words"]] == [1000, 1300, 1600]
    assert chunk["emphasis_index"] == 2 and chunk["italic"] is False
    assert chunk["end_ms"] == round(cues[-1].end * 1000)


def test_house_flicker_cues_become_one_chunk_per_build_up() -> None:
    cues = flicker_cues(
        _words(("How", 0.9), ("much", 1.2), ("for", 1.5), ("this?", 1.8))
    )

    chunks = caption_chunks(cues, whole_lines=False)

    assert [[w["text"] for w in c["words"]] for c in chunks] == [
        ["How", "much"],
        ["for", "this?"],
    ]
    assert [w["start_ms"] for w in chunks[0]["words"]] == [900, 1200]
    assert [w["start_ms"] for w in chunks[1]["words"]] == [1500, 1800]
    assert "emphasis_index" not in chunks[0]


def test_whole_lines_are_one_chunk_each() -> None:
    cues = [
        Cue(1.0, 2.5, "Wait here for me, okay.", italic=True),
        Cue(3.0, 4.0, "Not for sale."),
    ]

    chunks = caption_chunks(cues, whole_lines=True)

    assert len(chunks) == 2 and chunks[0]["italic"] is True
    assert {w["start_ms"] for w in chunks[0]["words"]} == {1000}


@pytest.mark.real_caption_server
def test_the_server_file_is_used_as_given(capsys: pytest.CaptureFixture[str]) -> None:
    sent: list[tuple[str, dict[str, Any]]] = []

    def server(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        sent.append((path, body))
        return 200, {
            "ass": SERVER_ASS,
            "cues": [],
            "findings": [{"message": "caption in the right rail at 1s"}],
        }

    cues = bold_cues(_words(("Not", 1.0), ("for", 1.3), ("sale.", 1.6)))
    with server_override(server):
        text = portrait_caption_ass(
            cues, width=192, height=336, style="bold", whole_lines=False
        )

    assert text == SERVER_ASS
    ((path, body),) = sent
    assert path == CAPTION_LAYOUT_ROUTE
    assert (body["width"], body["height"], body["style"], body["reveal"]) == (
        192,
        336,
        "bold",
        "word",
    )
    err = capsys.readouterr().err
    assert "WARNING server caption check: caption in the right rail at 1s" in err


@pytest.mark.real_caption_server
@pytest.mark.parametrize(
    ("style", "whole", "reveal"), [("subtle", False, "word"), ("plain", True, "line")]
)
def test_subtle_and_plain_ask_for_the_servers_house_look(
    style: str, whole: bool, reveal: str
) -> None:
    sent: list[dict[str, Any]] = []
    with server_override(
        lambda path, body: (sent.append(body), (200, {"ass": SERVER_ASS}))[1]
    ):
        portrait_caption_ass(
            [Cue(0.0, 1.0, "Wait.")],
            width=192,
            height=336,
            style=style,
            whole_lines=whole,
        )
    assert sent[0]["style"] == ("plain" if style == "plain" else "house")
    assert sent[0]["reveal"] == reveal


@pytest.mark.parametrize("status", [404, 405])
def test_an_older_server_falls_back_to_the_kits_layout_with_a_note(
    status: int, capsys: pytest.CaptureFixture[str]
) -> None:
    cues = [Cue(0.0, 1.0, "Wait.")]
    with server_override(lambda path, body: (status, {"detail": "Not Found"})):
        text = portrait_caption_ass(
            cues, width=192, height=336, style="house", whole_lines=False
        )
    assert text == build_ass(cues, width=192, height=336, style="house")
    assert (
        "NOTE captions: this Drama API has no caption layout route yet"
        in capsys.readouterr().err
    )


@pytest.mark.real_caption_server
def test_no_connection_or_a_refusal_falls_back_too(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def down(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        raise OSError("connection refused")

    with server_override(down):
        assert server_caption_ass(
            [Cue(0.0, 1.0, "Wait.")], width=192, height=336, style="house", whole_lines=False,
            desk=None, episode=1,
        ) == (None, [])  # fmt: skip
    with server_override(
        lambda path, body: (422, {"error": {"code": "caption_layout_invalid"}})
    ):
        assert server_caption_ass(
            [Cue(0.0, 1.0, "Wait.")], width=192, height=336, style="house", whole_lines=False,
            desk=None, episode=1,
        ) == (None, [])  # fmt: skip
    err = capsys.readouterr().err
    assert "could not be reached" in err and "HTTP 422" in err


@pytest.mark.real_caption_server
def test_a_caption_scale_keeps_the_kits_layout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def never(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        raise AssertionError("the server is not asked for a scaled caption")

    cues = [Cue(0.0, 1.0, "Wait.")]
    with server_override(never):
        text = portrait_caption_ass(
            cues,
            width=192,
            height=336,
            style="house",
            whole_lines=False,
            size_scale=1.2,
        )
    assert text == build_ass(cues, width=192, height=336, style="house", size_scale=1.2)
    assert "--caption-scale 1.2" in capsys.readouterr().err


@pytest.mark.real_caption_server
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_caption_take_writes_and_burns_the_servers_file(tmp_path: Path) -> None:
    ep = tmp_path / "ep01"
    (ep / "api").mkdir(parents=True)
    (ep / "takes").mkdir()
    spine = {
        "beats": [
            {
                "episode_id": "episode_01",
                "dialogue_lines": [{"text": "I'm coming for you."}],
            }
        ]
    }
    (ep / "api" / "03_spine.json").write_text(json.dumps(spine))
    take = ep / "takes" / "take-ep01-t1-raw-v1.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=192x336:d=4",
         "-f", "lavfi", "-i", "sine=f=440:d=4",
         "-filter_complex", "[1:a]volume='if(between(t,1,2),1,0)':eval=frame[a]",
         "-map", "0:v", "-map", "[a]", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(take)],
        check=True,
    )  # fmt: skip
    sent: list[dict[str, Any]] = []
    with server_override(
        lambda path, body: (sent.append(body), (200, {"ass": SERVER_ASS}))[1]
    ):
        result = caption_take(tmp_path, style="bold")
    assert result.ass.read_text(encoding="utf-8") == SERVER_ASS
    assert result.video.stat().st_size > 0
    words = [w["text"] for chunk in sent[0]["chunks"] for w in chunk["words"]]
    assert words == ["I'm", "coming", "for", "you."]
