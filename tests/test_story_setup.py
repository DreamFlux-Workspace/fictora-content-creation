"""Story setup after the draft: spoken language, the stored brief, and one-person shows.

Learnings 1 Oct 2026: L-20261001-21 / -28 (Kuchisake-onna drafted en-US but
voiced in Japanese), L-20261001-1 (narration left in the stored brief cost a
desk), L-20261001-4 (a one-creature show drew an invented narrator).
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from cast_story import _before_gate, _patches, _story
from conftest import set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.plan_prompt import (
    ensure_plan_prompt,
    narrator_lines,
    narrator_warning,
    server_cast_floor,
    strip_narration,
)
from creation.production_config import load_production_config
from creation.production_state import load_production, save_production
from creation.story_setup import run_brief_edit, run_language
from fake_api import FakeApi

LANGUAGE = "/v1/spines/sp1/spoken-language"
BRIEF = "/v1/spines/sp1/scene-prompt"
DRAFTS = "/v1/prompt-video-authoring-drafts"

NARRATED = """# Sighted

Mika walks the night ward alone.

Narrator (V.O.): Nobody leaves the ward before dawn.

## Lines

| Take | Beat | Speaker | Line |
| --- | --- | --- | --- |
| 1 | 1 | Mika | Is anyone there? |
| 1 | 2 | Narrator | She should not have asked. |

There is no narrator on screen."""


# --- Narrator lines in a brief ------------------------------------------------------------------


def test_narrator_lines_are_found_by_label_and_table_row_not_by_prose() -> None:
    assert narrator_lines(NARRATED) == [
        "Narrator (V.O.): Nobody leaves the ward before dawn.",
        "| 1 | 2 | Narrator | She should not have asked. |",
    ]
    assert narrator_lines("KENJI (V.O.): I never went back.") == [
        "KENJI (V.O.): I never went back."
    ]
    assert narrator_lines("Kenji: Is anyone there?\nThe story has no narration.") == []


def test_strip_narration_keeps_everything_else_as_written() -> None:
    stripped = strip_narration(NARRATED)

    assert narrator_lines(stripped) == []
    assert "| 1 | 1 | Mika | Is anyone there? |" in stripped
    assert "There is no narrator on screen." in stripped
    assert "\n\n\n" not in stripped
    # The kit's own cast directive talks about narrators but is not a narrator line.
    for floor in (1, 2):
        directed = ensure_plan_prompt(NARRATED, cast_floor=floor)
        assert strip_narration(directed).endswith(directed.rsplit("\n\n", 1)[1])


def test_the_warning_names_the_lines_and_the_command() -> None:
    warning = narrator_warning(NARRATED, desk="/desks/sighted")

    assert warning is not None
    assert "2 narrator / voice-over line(s)" in warning
    assert "brief --desk /desks/sighted --strip-narration" in warning
    assert "finish --voice" in warning
    assert narrator_warning("Mika walks the ward.") is None


def test_step_warns_before_the_draft(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    state = load_production(desk)
    state.prompt = NARRATED
    save_production(desk, state)
    set_phase(desk, "new", spine_id=None)
    api.routes[("POST", DRAFTS)] = {"plan_job_id": "job_plan", "spine_id": "sp1"}
    api.jobs["job_plan"] = {"status": "completed"}

    orchestrate.run_step(desk)

    assert "narrator / voice-over line(s)" in capsys.readouterr().err
    # A warning, never a stop: the draft was sent.
    assert api.posted(DRAFTS)


# --- One-person shows ---------------------------------------------------------------------------


def test_a_one_person_server_gets_the_one_person_directive_in_place_of_the_two_member_one() -> (
    None
):
    two = ensure_plan_prompt("A lighthouse keeper alone in the fog.")

    one = ensure_plan_prompt(two, cast_floor=1)

    assert "[fictora:season-bible-cast-min=2]" not in one
    assert one.count("[fictora:season-bible-cast-min=1]") == 1
    assert "one person is a whole cast" in one
    assert "never invent a narrator" in one.casefold()
    assert "at least two" not in one
    assert ensure_plan_prompt(one, cast_floor=1) == one
    # Back on an older server, the two-member directive comes back once.
    assert ensure_plan_prompt(one).count("[fictora:season-bible-cast-min=2]") == 1


class _Capabilities:
    def __init__(self, status: int, body: Any) -> None:
        self.answer = (status, body)

    def get_optional(self, path: str) -> tuple[int, Any]:
        assert path == "/v1/capabilities"
        return self.answer


def test_the_cast_floor_is_read_from_the_server_and_two_when_it_does_not_say() -> None:
    assert (
        server_cast_floor(_Capabilities(200, {"reaction_kinds": [], "cast_floor": 1}))
        == 1
    )
    assert server_cast_floor(_Capabilities(200, {"reaction_kinds": []})) == 2
    assert server_cast_floor(_Capabilities(404, {"detail": "Not Found"})) == 2


def test_the_draft_asks_a_one_person_server_for_exactly_the_people_the_story_needs(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "new", spine_id=None)
    api.routes[("GET", "/v1/capabilities")] = {"reaction_kinds": [], "cast_floor": 1}
    api.routes[("POST", DRAFTS)] = {"plan_job_id": "job_plan", "spine_id": "sp1"}
    api.jobs["job_plan"] = {"status": "completed"}

    orchestrate.run_step(desk)

    sent = api.posted(DRAFTS)[0]
    assert sent is not None
    assert "[fictora:season-bible-cast-min=1]" in sent["prompt"]
    assert "[fictora:season-bible-cast-min=2]" not in sent["prompt"]


def test_the_draft_keeps_two_on_an_older_server(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "new", spine_id=None)
    api.routes[("POST", DRAFTS)] = {"plan_job_id": "job_plan", "spine_id": "sp1"}
    api.jobs["job_plan"] = {"status": "completed"}

    orchestrate.run_step(desk)

    sent = api.posted(DRAFTS)[0]
    assert sent is not None
    assert "[fictora:season-bible-cast-min=2]" in sent["prompt"]


# --- language -----------------------------------------------------------------------------------

EFFECT = {
    "performed_lines_cleared": [],
    "lines_to_localize": 4,
    "when_localized": "next_take",
    "speech_levels_cleared": 0,
    "speech_level_examples_cleared": 0,
    "voice_references_cleared": ["cast_hana"],
    "filmed_in_other_language": [],
}


def _language_route(api: FakeApi, *, refuse: tuple[int, Any] | None = None) -> None:
    def answer(_method: str, _path: str, body: dict[str, Any] | None) -> Any:
        assert body is not None
        if refuse is not None:
            return refuse
        if not body.get("preview"):
            api.spine_doc["spoken_language"] = body["spoken_language"]
            api.spine_doc["spine_version"] = "v6"
        return {
            "spine_id": "sp1",
            "spine_version": api.spine_doc["spine_version"],
            "previous_spoken_language": "en-US",
            "spoken_language": body["spoken_language"],
            "changed": True,
            "applied": not body.get("preview"),
            "effect": EFFECT,
        }

    api.routes[("POST", LANGUAGE)] = answer


def _language_bodies(api: FakeApi) -> list[dict[str, Any] | None]:
    return api.posted(LANGUAGE)


def test_language_shows_the_change_then_applies_it(desk: Path, api: FakeApi) -> None:
    _language_route(api)
    record = desk / "shared" / "spoken-language.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(
        json.dumps(
            {
                "declared": "ja-JP",
                "server": "en-US",
                "pinned_lines": [
                    {
                        "episode": 1,
                        "line_id": "line_episode_01_01",
                        "spoken": "わたし、きれい？",
                        "subtitle": "Am I pretty?",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = io.StringIO()

    assert run_language(desk, spoken="ja", out=out)

    bodies = _language_bodies(api)
    assert [
        body["preview"] if body and "preview" in body else False for body in bodies
    ] == [True, False]
    assert all(
        body and body["spoken_language"] == "ja-JP" and body["spine_version"] == "v5"
        for body in bodies
    )
    text = out.getvalue()
    assert "language: en-US -> ja-JP" in text
    assert (
        "lines localized again into the new language: 4, before the next take" in text
    )
    assert (
        "seed voice clips made again in the new language with the next take (same voice): Hana"
        in text
    )
    # The line recorded while the server held the show as English, ready to send.
    assert (
        '--line line_episode_01_01 --spoken "わたし、きれい？" --subtitle "Am I pretty?" --language ja'
        in text
    )
    assert text.rstrip().splitlines()[-1] == "Applied"
    assert load_production_config(desk).spoken_language == "ja"
    assert json.loads(record.read_text(encoding="utf-8"))["server"] == "ja-JP"
    assert (
        json.loads((desk / "api" / "spine.json").read_text(encoding="utf-8"))[
            "spoken_language"
        ]
        == "ja-JP"
    )


def test_language_preview_sends_only_the_preview(desk: Path, api: FakeApi) -> None:
    _language_route(api)
    out = io.StringIO()

    assert not run_language(desk, spoken="ko", preview=True, out=out)

    assert [body and body.get("preview") for body in _language_bodies(api)] == [True]
    assert out.getvalue().rstrip().splitlines()[-1].startswith("Not applied: --preview")
    assert api.spine_doc["spoken_language"] == "en-US"


def test_takes_filmed_in_another_language_refuse_with_the_flag_to_pass(
    desk: Path, api: FakeApi
) -> None:
    filmed = {**EFFECT, "filmed_in_other_language": ["job_video_1"]}
    _language_route(
        api,
        refuse=(
            409,
            {
                "error": {
                    "code": "spoken_language_change_filmed",
                    "message": "1 video generation(s) of this show were filmed in another language.",
                    "details": {"effect": filmed},
                }
            },
        ),
    )
    out = io.StringIO()

    assert not run_language(desk, spoken="ja", out=out)

    text = out.getvalue()
    assert "!! filmed in another language: job_video_1" in text
    last = text.rstrip().splitlines()[-1]
    assert last.startswith("Refused: spoken_language_change_filmed")
    assert "--confirm-filmed" in last
    assert len(_language_bodies(api)) == 1


def test_confirm_filmed_is_sent(desk: Path, api: FakeApi) -> None:
    _language_route(api)

    run_language(desk, spoken="ja", confirm_filmed=True, out=io.StringIO())

    assert all(
        body and body["confirm_filmed"] is True for body in _language_bodies(api)
    )


def test_a_server_without_the_route_is_refused_clearly(
    desk: Path, api: FakeApi
) -> None:
    _language_route(api, refuse=(404, {"detail": "Not Found"}))
    out = io.StringIO()

    assert not run_language(desk, spoken="ja", out=out)

    last = out.getvalue().rstrip().splitlines()[-1]
    assert last.startswith("Refused: this server has no spoken-language route yet")
    assert load_production_config(desk).spoken_language is None


def test_the_language_the_show_already_has_sends_nothing(
    desk: Path, api: FakeApi
) -> None:
    _language_route(api)
    out = io.StringIO()

    assert run_language(desk, spoken="en", out=out)

    assert _language_bodies(api) == []
    assert (
        out.getvalue()
        .rstrip()
        .splitlines()[-1]
        .startswith("Not applied: the show is already performed in en-US")
    )


def test_before_the_draft_the_language_is_the_desks(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "new", spine_id=None)
    out = io.StringIO()

    assert run_language(desk, spoken="ko", out=out)

    assert load_production_config(desk).spoken_language == "ko"
    assert api.calls == []
    assert (
        out.getvalue().rstrip().splitlines()[-1]
        == "Applied (on the desk; no story yet: the draft sends it)"
    )


def test_after_the_change_line_spoken_is_sent(desk: Path, api: FakeApi) -> None:
    """Once the server holds the show as Japanese, ``line --spoken`` sends the pin (it was recorded before)."""

    _before_gate(api, _story(approved=False))
    _language_route(api)
    with pytest.raises(ec.CommandStopped):
        ec.run_line(
            desk,
            episode=1,
            line="1",
            spoken="もう閉店です。",
            language="ja",
            out=io.StringIO(),
        )
    assert _patches(api) == []

    run_language(desk, spoken="ja", out=io.StringIO())
    ec.run_line(
        desk,
        episode=1,
        line="1",
        spoken="もう閉店です。",
        language="ja",
        out=io.StringIO(),
    )

    assert _patches(api)[0]["dialogue_lines"][0]["spoken_text"] == "もう閉店です。"


# --- brief --------------------------------------------------------------------------------------


def _with_brief(desk: Path, api: FakeApi, text: str) -> None:
    state = load_production(desk)
    state.prompt = text
    save_production(desk, state)
    api.spine_doc["scene_prompt_normalized"] = " ".join(text.split())


def _brief_route(api: FakeApi, *, refuse: tuple[int, Any] | None = None) -> None:
    def answer(_method: str, _path: str, body: dict[str, Any] | None) -> Any:
        assert body is not None
        if refuse is not None:
            return refuse
        normalized = " ".join(body["scene_prompt"].split())
        if not body.get("preview"):
            api.spine_doc["scene_prompt_normalized"] = normalized
            api.spine_doc["spine_version"] = "v6"
        return {
            "spine_id": "sp1",
            "spine_version": api.spine_doc["spine_version"],
            "scene_prompt_normalized": normalized,
            "scene_prompt_sha256": "a" * 64,
            "previous_scene_prompt_sha256": "b" * 64,
            "changed": True,
            "applied": not body.get("preview"),
            "locked_line_count": 0,
        }

    api.routes[("PUT", BRIEF)] = answer


def _brief_bodies(api: FakeApi) -> list[dict[str, Any] | None]:
    return [
        body for method, path, body, _ in api.calls if (method, path) == ("PUT", BRIEF)
    ]


def test_strip_narration_replaces_the_stored_brief_and_the_desks(
    desk: Path, api: FakeApi
) -> None:
    _with_brief(desk, api, NARRATED)
    _brief_route(api)
    out = io.StringIO()

    assert run_brief_edit(desk, strip=True, out=out)

    bodies = _brief_bodies(api)
    assert [body and body.get("preview", False) for body in bodies] == [True, False]
    sent = bodies[-1]
    assert sent is not None and narrator_lines(sent["scene_prompt"]) == []
    assert sent["spine_version"] == "v5"
    text = out.getvalue()
    assert "  -Narrator (V.O.): Nobody leaves the ward before dawn." in text
    assert text.rstrip().splitlines()[-1] == "Applied"
    assert load_production(desk).prompt == sent["scene_prompt"]


def test_edit_sends_the_file_and_warns_when_narration_is_still_in_it(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _with_brief(desk, api, "Mika walks the night ward alone.")
    _brief_route(api)
    new = tmp_path / "brief.md"
    new.write_text(
        "Mika walks the night ward alone.\nVO: The ward remembers.\n", encoding="utf-8"
    )
    out = io.StringIO()

    assert run_brief_edit(desk, edit=f"@{new}", out=out)

    assert (
        _brief_bodies(api)[-1]["scene_prompt"]
        == new.read_text(encoding="utf-8").strip()
    )
    assert (
        "!! the new brief still has 1 narrator / voice-over line(s)" in out.getvalue()
    )


def test_a_brief_the_server_refuses_is_refused_and_the_desk_keeps_its_brief(
    desk: Path, api: FakeApi
) -> None:
    _with_brief(desk, api, NARRATED)
    _brief_route(
        api,
        refuse=(
            422,
            {
                "error": {
                    "code": "locked_lines_out_of_bounds",
                    "message": "Your brief locks lines word for word…",
                }
            },
        ),
    )
    out = io.StringIO()

    assert not run_brief_edit(desk, strip=True, out=out)

    assert (
        out.getvalue()
        .rstrip()
        .splitlines()[-1]
        .startswith("Refused: locked_lines_out_of_bounds")
    )
    assert len(_brief_bodies(api)) == 1
    assert load_production(desk).prompt == NARRATED


def test_brief_preview_sends_only_the_preview(desk: Path, api: FakeApi) -> None:
    _with_brief(desk, api, NARRATED)
    _brief_route(api)
    out = io.StringIO()

    assert not run_brief_edit(desk, strip=True, preview=True, out=out)

    assert [body and body.get("preview") for body in _brief_bodies(api)] == [True]
    assert out.getvalue().rstrip().splitlines()[-1].startswith("Not applied: --preview")
    assert load_production(desk).prompt == NARRATED


def test_a_server_without_the_brief_route_is_refused_clearly(
    desk: Path, api: FakeApi
) -> None:
    _with_brief(desk, api, NARRATED)
    _brief_route(api, refuse=(405, {"detail": "Method Not Allowed"}))
    out = io.StringIO()

    assert not run_brief_edit(desk, strip=True, out=out)

    assert (
        "Refused: this server has no brief (scene-prompt) route yet" in out.getvalue()
    )


def test_strip_with_nothing_to_strip_sends_nothing(desk: Path, api: FakeApi) -> None:
    _with_brief(desk, api, "Mika walks the night ward alone.")
    _brief_route(api)
    out = io.StringIO()

    assert run_brief_edit(desk, strip=True, out=out)

    assert _brief_bodies(api) == []
    assert "Not applied: it has no narrator lines" in out.getvalue()


def test_strip_refuses_when_the_desk_and_the_server_disagree(
    desk: Path, api: FakeApi
) -> None:
    _with_brief(desk, api, NARRATED)
    api.spine_doc["scene_prompt_normalized"] = "A different brief someone stored."

    with pytest.raises(ec.CommandStopped) as stopped:
        run_brief_edit(desk, strip=True, out=io.StringIO())

    assert "--edit @FILE" in str(stopped.value)


def test_before_the_draft_the_brief_is_the_desks(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "new", spine_id=None)
    state = load_production(desk)
    state.prompt = NARRATED
    save_production(desk, state)
    out = io.StringIO()

    assert run_brief_edit(desk, strip=True, out=out)

    assert api.calls == []
    assert narrator_lines(load_production(desk).prompt) == []
    assert "(on the desk; no story yet: the draft sends it)" in out.getvalue()


def test_the_cli_brief_needs_an_episode_or_an_edit(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation.cli_produce import main

    assert main(["brief", "--desk", str(desk)]) == 2
    assert "--edit @FILE / --strip-narration" in capsys.readouterr().err


def test_the_cli_ends_a_stopped_brief_edit_on_a_verdict(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation.cli_produce import main

    assert main(["brief", "--desk", str(desk), "--edit", "@/no/such/brief.md"]) == 2
    assert (
        capsys.readouterr()
        .err.rstrip()
        .splitlines()[-1]
        .startswith("Refused: --edit @/no/such/brief.md")
    )


def test_the_cli_ends_a_bad_language_on_a_verdict(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation.cli_produce import main

    assert main(["language", "--desk", str(desk), "--spoken", "fr"]) == 2
    assert (
        capsys.readouterr()
        .err.rstrip()
        .splitlines()[-1]
        .startswith("Refused: --language takes ja, ko or en")
    )
