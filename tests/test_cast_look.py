"""`cast --look` and `line --new-voice --look` (L-20261001-23, L-20261001-103)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from cast_story import (
    SAM_BRIEF,
    _after_gate,
    _before_gate,
    _look_file,
    _patches,
    _refusal,
    _story,
    _with_sam,
    _writes,
)
from creation import cast_commands as cc
from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from fake_api import FakeApi


def test_cast_look_before_the_gate_patches_the_cast_card_in_the_cast_brief_shape(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _with_sam(_story(approved=False), in_frame=True))
    out = io.StringIO()

    cc.run_cast_look(desk, name="Sam", look=_look_file(tmp_path), out=out)

    assert _patches(api) == [
        {
            "cast": [
                {
                    "cast_id": "cast_sam",
                    "visual_description": "A night-shift station guard who has seen too much.",
                    "visual_brief": SAM_BRIEF,
                }
            ]
        }
    ]
    text = out.getvalue()
    assert "visual_brief.face_anchors" in text  # the change is shown before it is sent
    assert f'redraw-plate --desk {desk.resolve()} --cast "Sam"' in text
    saved = json.loads((desk / "api" / "spine.json").read_text(encoding="utf-8"))
    assert saved["cast"][2]["visual_brief"]["hair_anchors"] == ["short grey crew cut"]


def test_cast_look_after_the_gate_sends_the_cast_card_cascade(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    edits = _after_gate(api, _with_sam(_story(approved=True)))

    cc.run_cast_look(
        desk, name="cast_sam", look=_look_file(tmp_path), out=io.StringIO()
    )

    assert edits == [
        {
            "scope": "field",
            "target_type": "cast_card",
            "target_id": "cast_sam",
            "patch": {
                "cast_id": "cast_sam",
                "visual_description": "A night-shift station guard who has seen too much.",
                "visual_brief": SAM_BRIEF,
            },
        }
    ]
    assert ("POST", "/v1/spines/sp1/cascade/execute") in _writes(api)
    assert not _patches(api)


def test_cast_look_preview_shows_the_change_and_sends_nothing(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _with_sam(_story(approved=False)))
    out = io.StringIO()

    cc.run_cast_look(
        desk, name="Sam", look=_look_file(tmp_path), preview_only=True, out=out
    )

    assert _writes(api) == []
    assert "visual_brief.hair_anchors" in out.getvalue()
    assert "preview only: nothing was sent" in out.getvalue()


def test_cast_look_missing_anchors_stops_before_anything_is_sent(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _with_sam(_story(approved=False)))

    with pytest.raises(ec.CommandStopped) as stopped:
        cc.run_cast_look(desk, name="Sam", look="a tired old guard", out=io.StringIO())

    assert "face" in str(stopped.value) and "wardrobe" in str(stopped.value)
    assert _writes(api) == []


def test_line_new_voice_with_a_look_adds_the_voice_then_gives_it_the_look(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _story(approved=False))
    out = io.StringIO()

    ec.run_line(
        desk,
        episode=1,
        add=True,
        beat="2",
        text="Last train's gone.",
        new_voice="Sam",
        role="station guard",
        voice_description="gravelly older man",
        provider_voice="Bill",
        look=_look_file(tmp_path),
        out=out,
    )

    patches = _patches(api)
    assert [sorted(p) for p in patches] == [
        ["add_dialogue_lines", "add_voice_only_cast"],
        ["cast"],
    ]
    assert patches[1]["cast"][0]["cast_id"] == "cast_sam"
    assert patches[1]["cast"][0]["visual_brief"] == SAM_BRIEF
    assert "Sam is heard only for now: no plate is drawn" in out.getvalue()


def test_cast_look_ends_on_the_verdict_line(
    desk: Path, api: FakeApi, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _before_gate(api, _with_sam(_story(approved=False), in_frame=True))

    assert (
        produce_main(
            [
                "cast",
                "--desk",
                str(desk),
                "--name",
                "Sam",
                "--look",
                _look_file(tmp_path),
            ]
        )
        == 0
    )
    last = [row for row in capsys.readouterr().out.splitlines() if row.strip()][-1]
    assert last.startswith("Applied: all ") and last.endswith(" changes")


def test_a_refused_cast_look_ends_on_refused(
    desk: Path, api: FakeApi, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _before_gate(api, _with_sam(_story(approved=False)))

    def refuse(*_: Any) -> Any:
        raise _refusal("invalid_patch", "The story spine patch is invalid.", {})

    api.routes[("PATCH", "/v1/spines/sp1")] = refuse

    assert (
        produce_main(
            [
                "cast",
                "--desk",
                str(desk),
                "--name",
                "Sam",
                "--look",
                _look_file(tmp_path),
            ]
        )
        == 2
    )
    last = [row for row in capsys.readouterr().err.splitlines() if row.strip()][-1]
    assert last.startswith("Refused: ")


def test_line_new_voice_with_a_look_ends_on_one_verdict_line(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _story(approved=False))
    out = io.StringIO()

    ec.run_line(
        desk, episode=1, add=True, beat="2", text="Last train's gone.", new_voice="Sam", role="station guard",
        voice_description="gravelly older man", provider_voice="Bill", look=_look_file(tmp_path), out=out,
    )  # fmt: skip

    rows = [row for row in out.getvalue().splitlines() if row.strip()]
    assert rows[-1] == "Applied: all 3 changes"  # the voice, its line, the look
    assert sum(row.startswith("Applied") for row in rows) == 1


# --- a partial look changes only its fields (canary 7 Oct, Mina) ---------------------------------------


def _mina(description: str, age_band: str) -> dict[str, Any]:
    return {
        "cast_id": "cast_mina",
        "name": "Mina",
        "visual_description": description,
        "visual_brief": {**SAM_BRIEF, "age_band": age_band},
    }


def test_a_look_with_only_an_age_keeps_the_description_and_every_other_field() -> None:
    mina = _mina(
        "A 28-year-old East Asian woman with an angular adult face, shoulder-length dark hair.",
        "twenty-eight, adult",
    )

    patch, _changed = cc.look_patch({"cast": [mina]}, mina, "age: 28")

    assert patch["visual_description"] == mina["visual_description"]
    assert patch["visual_brief"] == {**mina["visual_brief"], "age_band": "28"}


def test_a_new_age_moves_the_age_the_description_states() -> None:
    mina = _mina("A 27-year-old East Asian woman with shoulder-length dark hair.", "27")

    patch, _changed = cc.look_patch({"cast": [mina]}, mina, "age: 28")

    assert patch["visual_description"] == (
        "A 28-year-old East Asian woman with shoulder-length dark hair."
    )
