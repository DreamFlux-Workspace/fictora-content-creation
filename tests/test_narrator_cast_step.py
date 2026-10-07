"""A narrator-named character is asked about once: heard only, or drawn (founder decision 5).

The kit never decides from the name. It asks "NAME — heard only, never seen?
[y/N]" when such a character first appears, saves the answer on the desk,
and puts a heard-only one on the server's voice-only route (every line off
screen, visual brief cleared). With nobody to ask it stops before anything
is drawn and names both flags. An off-screen person who is not named like a
narrator is never asked and never turned into one.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from conftest import SHOWN_PRICES, set_phase
from creation import episode_commands as ec
from creation import narrator_cast as nc
from creation import orchestrate
from creation.cli_produce import main as produce_main
from creation.production_state import load_production
from fake_api import FakeApi, spine_fixture


def _narrated(
    api: FakeApi, *, name: str = "Narrator", lines: bool = True, approved: bool = False
) -> None:
    """Make Ren a narrator-named character with a visual brief; his lines on screen."""

    spine = spine_fixture(approved=approved)
    for card in spine["cast"]:
        if card["cast_id"] == "cast_ren":
            card["name"] = name
            card["visual_brief"] = {"anchors": ["grey coat"]}
    if not lines:
        for beat in spine["beats"]:
            beat["dialogue_lines"] = [
                ln for ln in beat["dialogue_lines"] if ln["cast_id"] != "cast_ren"
            ]
    api.spine_doc = spine
    api.routes[("PATCH", "/v1/spines/sp1")] = {}


def _patches(api: FakeApi) -> list[dict[str, Any]]:
    return [
        body["patch"] for method, _, body, _ in api.calls if method == "PATCH" and body
    ]


def _answers(desk: Path) -> dict[str, Any]:
    path = desk / nc.ANSWERS_FILE
    return json.loads(path.read_text())["characters"] if path.is_file() else {}


# --- the name ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Narrator",
        "The Narrator",
        "Storyteller",
        "Voice-over",
        "Voiceover",
        "VO",
        "V.O.",
        "Hana (inner voice)",
        "Narration",
    ],
)
def test_narrator_names_are_recognised(name: str) -> None:
    assert nc.named_like_narrator(name)


@pytest.mark.parametrize(
    "name",
    [
        "Hana",
        "Genzō Hanakaze",
        "Speaker voice",
        "Intercom",
        "Vo Thi Mai",
        "Novo",
        "Voss",
    ],
)
def test_ordinary_names_are_not(name: str) -> None:
    assert not nc.named_like_narrator(name)


# --- the question -----------------------------------------------------------------------------


def test_yes_puts_them_on_the_voice_only_route_and_is_kept(
    desk: Path, api: FakeApi
) -> None:
    _narrated(api)
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    given = nc.settle_narrators(
        desk, api, api.spine_doc, ask=ask, rerun="step", out=io.StringIO()
    )

    assert asked == ["Narrator — heard only, never seen? [y/N] "]
    assert [(a.cast_id, a.heard_only) for a in given] == [("cast_ren", True)]
    (patch,) = _patches(api)
    assert {entry["line_id"] for entry in patch["dialogue_lines"]} == {
        "line_episode_01_02",
        "line_ep_02_02",
    }
    assert all(entry["off_screen"] is True for entry in patch["dialogue_lines"])
    assert patch["cast"] == [
        {"cast_id": "cast_ren", "visual_brief": None, "heard_only": True}
    ]
    assert _answers(desk)["cast_ren"]["heard_only"] is True

    # Asked once: the next run asks nothing and sends nothing.
    api.calls.clear()
    nc.settle_narrators(
        desk,
        api,
        api.spine_doc,
        ask=lambda _p: pytest.fail("asked twice"),
        rerun="step",
    )
    assert _patches(api) == []


@pytest.mark.parametrize("reply", ["n", "", "no"])
def test_no_keeps_an_ordinary_drawn_character_and_is_kept(
    desk: Path, api: FakeApi, reply: str
) -> None:
    _narrated(api)

    nc.settle_narrators(
        desk, api, api.spine_doc, ask=lambda _p: reply, rerun="step", out=io.StringIO()
    )

    assert _patches(api) == []
    assert _answers(desk)["cast_ren"]["heard_only"] is False
    assert nc.open_questions(api.spine_doc, nc.load_answers(desk)) == []


def test_nobody_to_ask_stops_without_guessing(desk: Path, api: FakeApi) -> None:
    _narrated(api)

    with pytest.raises(nc.NarratorQuestionOpen) as stopped:
        nc.settle_narrators(
            desk, api, api.spine_doc, ask=None, rerun="fictora-produce step --desk D"
        )

    text = str(stopped.value)
    assert "Narrator" in text and "will not guess" in text
    assert "fictora-produce step --desk D --narrator-heard-only 'Narrator'" in text
    assert "fictora-produce step --desk D --narrator-on-screen 'Narrator'" in text
    assert _patches(api) == [] and _answers(desk) == {}


def test_flags_answer_without_asking(desk: Path, api: FakeApi) -> None:
    _narrated(api)

    nc.settle_narrators(
        desk,
        api,
        api.spine_doc,
        heard_only=["narrator"],
        ask=None,
        rerun="step",
        out=io.StringIO(),
    )

    assert _answers(desk)["cast_ren"]["heard_only"] is True and len(_patches(api)) == 1


def test_a_flag_for_nobody_or_both_ways_is_refused(desk: Path, api: FakeApi) -> None:
    _narrated(api)
    with pytest.raises(ValueError, match="not in the cast"):
        nc.settle_narrators(
            desk, api, api.spine_doc, heard_only=["Storyteller"], ask=None, rerun="step"
        )
    with pytest.raises(ValueError, match="both"):
        nc.settle_narrators(
            desk,
            api,
            api.spine_doc,
            heard_only=["Narrator"],
            on_screen=["cast_ren"],
            ask=None,
            rerun="step",
        )
    assert _patches(api) == []


def test_heard_only_with_no_line_yet_is_saved_on_the_card(
    desk: Path, api: FakeApi
) -> None:
    # The server keeps the answer (heard_only) before any line, so no plate is
    # drawn for them.
    _narrated(api, lines=False)
    out = io.StringIO()

    nc.settle_narrators(
        desk, api, api.spine_doc, ask=lambda _p: "y", rerun="step", out=out
    )

    (patch,) = _patches(api)
    assert "dialogue_lines" not in patch
    assert patch["cast"] == [
        {"cast_id": "cast_ren", "visual_brief": None, "heard_only": True}
    ]
    assert _answers(desk)["cast_ren"]["heard_only"] is True


def test_an_older_server_without_heard_only_gets_the_lines_and_a_warning(
    desk: Path, api: FakeApi
) -> None:
    _narrated(api)

    def refuse_heard_only(_m: str, _p: str, body: dict[str, Any] | None) -> Any:
        if any("heard_only" in c for c in (body or {})["patch"].get("cast") or []):
            raise SystemExit(
                "HTTP 400 PATCH: invalid_patch: patch.cast.0.heard_only: Extra inputs are not permitted"
            )
        return {}

    api.routes[("PATCH", "/v1/spines/sp1")] = refuse_heard_only
    out = io.StringIO()

    nc.settle_narrators(
        desk, api, api.spine_doc, ask=lambda _p: "y", rerun="step", out=out
    )

    first, second = _patches(api)
    assert first["cast"][0]["heard_only"] is True
    assert "heard_only" not in second["cast"][0]
    assert {e["line_id"] for e in second["dialogue_lines"]} == {
        "line_episode_01_02",
        "line_ep_02_02",
    }
    assert _answers(desk)["cast_ren"]["heard_only"] is True


def test_an_older_server_and_no_line_yet_warns(desk: Path, api: FakeApi) -> None:
    _narrated(api, lines=False)
    api.routes[("PATCH", "/v1/spines/sp1")] = SystemExit(
        "HTTP 400 PATCH: invalid_patch: patch.cast.0.heard_only: Extra inputs are not permitted"
    )
    out = io.StringIO()

    nc.settle_narrators(
        desk, api, api.spine_doc, ask=lambda _p: "y", rerun="step", out=out
    )

    assert "no line yet" in out.getvalue()
    assert _answers(desk)["cast_ren"]["heard_only"] is True


def test_a_card_the_server_says_is_heard_only_is_not_asked(
    desk: Path, api: FakeApi
) -> None:
    _narrated(api)
    for card in api.spine_doc["cast"]:
        if card["cast_id"] == "cast_ren":
            card["heard_only"] = True

    assert nc.open_questions(api.spine_doc, {}) == []


def test_after_the_script_gate_heard_only_names_the_line_edits(
    desk: Path, api: FakeApi
) -> None:
    _narrated(api, approved=True)
    api.routes[("PATCH", "/v1/spines/sp1")] = SystemExit(
        "HTTP 409 PATCH: cascade_required: approved"
    )

    with pytest.raises(
        nc.NarratorQuestionOpen, match=r"line --line line_episode_01_02 --off-screen"
    ):
        nc.settle_narrators(desk, api, api.spine_doc, ask=lambda _p: "y", rerun="step")
    assert _answers(desk) == {}


def test_an_off_screen_person_is_never_asked_or_made_a_narrator(
    desk: Path, api: FakeApi
) -> None:
    # Ren is heard through the ceiling (every line off screen) but is not
    # named like a narrator; a server-voice-only "Narrator" is already
    # answered by the server's state.
    spine = spine_fixture(approved=False)
    for beat in spine["beats"]:
        for line in beat["dialogue_lines"]:
            if line["cast_id"] == "cast_ren":
                line["off_screen"] = True
    spine["cast"].append({"cast_id": "cast_vo", "name": "Narrator", "voice_only": True})
    api.spine_doc = spine

    given = nc.settle_narrators(
        desk, api, spine, ask=lambda _p: pytest.fail("asked"), rerun="step"
    )

    assert given == [] and _patches(api) == [] and _answers(desk) == {}
    assert spine["cast"][1]["name"] == "Ren"


# --- where it is asked ------------------------------------------------------------------------


def test_step_will_not_draw_plates_until_the_narrator_is_answered(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _narrated(api)
    set_phase(desk, "ready_cast_enrol", drawing_estimates=SHOWN_PRICES)
    api.routes[("POST", "/v1/spines/sp1/cast/enrol")] = {"job_id": "job_cast"}
    api.jobs["job_cast"] = {"status": "completed"}

    assert produce_main(["step", "--desk", str(desk), "--confirm-spend"]) == 2

    err = capsys.readouterr().err
    assert (
        "--narrator-heard-only 'Narrator'" in err
        and "--narrator-on-screen 'Narrator'" in err
    )
    assert not api.posted("/v1/spines/sp1/cast/enrol")
    assert load_production(desk).phase == "ready_cast_enrol"

    assert (
        produce_main(
            [
                "step",
                "--desk",
                str(desk),
                "--confirm-spend",
                "--narrator-heard-only",
                "Narrator",
            ]
        )
        == 0
    )

    assert len(_patches(api)) == 1
    assert api.posted("/v1/spines/sp1/cast/enrol")
    assert load_production(desk).phase == "wait_plates"


def test_step_draws_a_narrator_answered_on_screen(desk: Path, api: FakeApi) -> None:
    _narrated(api)
    set_phase(desk, "ready_cast_enrol", drawing_estimates=SHOWN_PRICES)
    api.routes[("POST", "/v1/spines/sp1/cast/enrol")] = {"job_id": "job_cast"}
    api.jobs["job_cast"] = {"status": "completed"}

    assert (
        produce_main(
            [
                "step",
                "--desk",
                str(desk),
                "--confirm-spend",
                "--narrator-on-screen",
                "Narrator",
            ]
        )
        == 0
    )

    assert _patches(api) == []
    assert api.posted("/v1/spines/sp1/cast/enrol")
    assert _answers(desk)["cast_ren"]["heard_only"] is False


def test_the_draft_asks_when_someone_can_answer(desk: Path, api: FakeApi) -> None:
    _narrated(api)
    api.routes[("POST", "/v1/prompt-video-authoring-drafts")] = {
        "plan_job_id": "job_plan",
        "spine_id": "sp1",
    }
    api.jobs["job_plan"] = {"status": "completed"}
    set_phase(desk, "new", spine_id=None)

    result = orchestrate.run_step(desk, ask=lambda _p: "n")

    assert result.phase == "ready_cast_enrol"
    assert _answers(desk)["cast_ren"]["heard_only"] is False


def test_the_draft_says_the_next_step_will_ask_when_nobody_can(
    desk: Path, api: FakeApi
) -> None:
    _narrated(api)
    api.routes[("POST", "/v1/prompt-video-authoring-drafts")] = {
        "plan_job_id": "job_plan",
        "spine_id": "sp1",
    }
    api.jobs["job_plan"] = {"status": "completed"}
    set_phase(desk, "new", spine_id=None)

    result = orchestrate.run_step(desk)

    assert result.phase == "ready_cast_enrol"
    assert "--narrator-heard-only 'Narrator'" in result.message
    assert _answers(desk) == {}


def test_author_stops_on_a_new_narrator_after_saving_the_episode(
    desk: Path, api: FakeApi
) -> None:
    _narrated(api)
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2"
    }
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}

    with pytest.raises(nc.NarratorQuestionOpen, match="fictora-produce step --desk"):
        ec.run_author(desk, episode=2, out=io.StringIO())
    assert (desk / "api" / "spine.json").is_file()

    ec.run_author(desk, episode=2, narrator_on_screen=["Narrator"], out=io.StringIO())
    assert _answers(desk)["cast_ren"]["heard_only"] is False


def _new_voice_argv(desk: Path, *extra: str) -> list[str]:
    return [
        "line", "--desk", str(desk), "--episode", "1", "--add", "--beat", "1", "--text", "Once upon a time.",
        "--new-voice", "Storyteller", "--role", "The tale's teller", "--voice-description", "warm, low", *extra,
    ]  # fmt: skip


def _server_adds_voices(api: FakeApi) -> None:
    api.spine_doc = spine_fixture(approved=False)
    api.spine_doc["beats"][0]["dialogue_lines"] = []
    api.spine_doc["beats"][0]["motion_direction"]["subject_cast_id"] = "cast_hana"

    def patch(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        for card in (body or {}).get("patch", {}).get("add_voice_only_cast") or []:
            api.spine_doc["cast"].append(
                {"cast_id": card["cast_id"], "name": card["name"], "voice_only": True}
            )
        return {}

    api.routes[("PATCH", "/v1/spines/sp1")] = patch


def test_new_voice_named_like_a_narrator_is_asked_first(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _server_adds_voices(api)

    assert produce_main(_new_voice_argv(desk)) == 2
    err = capsys.readouterr().err
    assert "--narrator-heard-only 'Storyteller'" in err
    assert _patches(api) == []

    assert (
        produce_main(_new_voice_argv(desk, "--narrator-on-screen", "Storyteller")) == 2
    )
    assert "never seen" in capsys.readouterr().err and _patches(api) == []

    assert (
        produce_main(_new_voice_argv(desk, "--narrator-heard-only", "Storyteller")) == 0
    )
    (patch,) = _patches(api)
    assert patch["add_voice_only_cast"][0]["name"] == "Storyteller"
    assert next(iter(_answers(desk).values()))["heard_only"] is True


def test_a_new_voice_not_named_like_a_narrator_is_not_asked(
    desk: Path, api: FakeApi
) -> None:
    _server_adds_voices(api)
    argv = [
        "line", "--desk", str(desk), "--episode", "1", "--add", "--beat", "1", "--text", "Stay still.",
        "--new-voice", "Intercom", "--role", "Facility intercom", "--voice-description", "tinny",
    ]  # fmt: skip

    assert produce_main(argv) == 0
    assert _answers(desk) == {}
