"""A line that cannot fit its take: the kit prints the server's card word for word and sends the choice back.

Founder decision, 20 Oct 2026 (fictora-drama #962). The server owns every word,
number and state of a line-fit proposal; the kit keeps no rule of its own.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import episode_commands as ec
from creation import line_fit, orchestrate
from creation.cli_produce import main
from creation.harness.http_util import ApiRefused, JobFailed
from creation.production_state import load_production, save_production
from creation.story_setup import run_brief_edit
from fake_api import FakeApi

SHA = "a" * 64
VERSION = "sha256:" + "1" * 64
MESSAGE = "Take 2 holds about 22 spoken words; these come to 29, so the last line would run past the end."


def proposal(pid: str = "lf_1", **changes: Any) -> dict[str, Any]:
    """A ``fits`` proposal shaped like the server's ``DramaLineFitProposal``."""

    base: dict[str, Any] = {
        "schema_version": "fictora.drama-line-fit-proposal.v1",
        "proposal_id": pid,
        "source": "line_edit",
        "status": "open",
        "spine_id": "sp1",
        "spine_version": VERSION,
        "source_sha256": SHA,
        "episode_ordinal": 1,
        "take_number": 2,
        "budget": {"take_words_target": 22, "take_words_max": 28, "line_words_target": 10,
                   "line_words_max": 12, "beats": 3},
        "measured": {"words": 29, "seconds": 13.1},
        "outcome": "fits",
        "message": MESSAGE,
        "original_lines": [
            {"slot": "beat_3", "speaker": "Hana", "text": "I waited for you every single night this winter.",
             "words": 9},
        ],
        "fitted_lines": [
            {"slot": "beat_3", "speaker": "Hana", "text": "I waited every night.", "words": 4, "changed": True,
             "action": "she sets the cup down hard"},
        ],
        "trimmed_lines": [],
        "fits_rules": True,
        "rule_notes": [],
        "question": "Use this version?",
        "choices": [
            {"action": "line_fit_use", "label": "Use this", "text": "Use the fitted lines.", "proposal_id": pid},
            {"action": "line_fit_edit", "label": "Edit",
             "text": "Change the lines yourself, and I'll check they fit.", "proposal_id": pid},
        ],
    }  # fmt: skip
    base.update(changes)
    return base


ROUTE = "/v1/line-fit-proposals"


def _serve(api: FakeApi, *proposals: dict[str, Any]) -> None:
    api.routes[("GET", ROUTE)] = {"proposals": list(proposals)}
    for item in proposals:
        api.routes[("GET", f"{ROUTE}/{item['proposal_id']}")] = item


def _run(desk: Path, **kwargs: Any) -> str:
    out = io.StringIO()
    line_fit.run_line_fit(desk, out=out, **kwargs)
    return out.getvalue()


def test_show_prints_the_servers_card_word_for_word(desk: Path, api: FakeApi) -> None:
    _serve(api, proposal())

    text = _run(desk)

    assert MESSAGE in text
    assert '[beat_3] Hana: "I waited for you every single night this winter."' in text
    assert '[beat_3] Hana: "I waited every night."' in text
    assert "(she sets the cup down hard)" in text
    assert "Use this version?" in text
    assert "Use this: Use the fitted lines." in text
    assert "Edit: Change the lines yourself, and I'll check they fit." in text
    assert (
        f"fictora-produce line-fit --desk {desk} --proposal lf_1 --choose use" in text
    )
    assert ("GET", f"{ROUTE}?spine_id=sp1", None, None) in api.calls
    assert not [call for call in api.calls if call[0] == "POST"]


def test_show_a_needs_two_takes_card_offers_trim(desk: Path, api: FakeApi) -> None:
    item = proposal(
        outcome="needs_two_takes",
        message="This moment plays better across two takes.",
        fitted_lines=[],
        trimmed_lines=[{"slot": "beat_3", "speaker": "Hana", "text": "Every night.", "words": 2, "changed": True}],
        fits_rules=False,
        rule_notes=["The take runs past its 28-word limit."],
        question="Still trim to one take?",
        choices=[
            {"action": "line_fit_trim", "label": "Yes, trim to one take", "text": "Trim the lines so the moment "
             "fits one take.", "proposal_id": "lf_1"},
            {"action": "line_fit_edit", "label": "Edit", "text": "Change the lines yourself.", "proposal_id": "lf_1"},
        ],
    )  # fmt: skip
    _serve(api, item)

    text = _run(desk, proposal_id="lf_1")

    assert "This moment plays better across two takes." in text
    assert "  - The take runs past its 28-word limit." in text
    assert "Still trim to one take?" in text
    assert "--proposal lf_1 --choose trim" in text


def test_choose_use_posts_the_choice_and_saves_the_story(
    desk: Path, api: FakeApi
) -> None:
    _serve(api, proposal())
    spine = {**api.spine_doc, "spine_version": VERSION}
    api.routes[("POST", f"{ROUTE}/lf_1/choose")] = (
        200,
        {"proposal_id": "lf_1", "status": "applied", "spine": spine},
    )

    text = _run(desk, proposal_id="lf_1", choose="use")

    assert api.posted(f"{ROUTE}/lf_1/choose") == [
        {"choice": "use", "source_sha256": SHA, "spine_version": VERSION}
    ]
    assert "Server: applied (lf_1)." in text
    assert (desk / "api" / "spine.json").is_file()


def test_choose_trim_sends_trim(desk: Path, api: FakeApi) -> None:
    _serve(api, proposal(outcome="needs_two_takes"))
    api.routes[("POST", f"{ROUTE}/lf_1/choose")] = (
        200,
        {"proposal_id": "lf_1", "status": "applied"},
    )

    _run(desk, choose="trim")

    assert api.posted(f"{ROUTE}/lf_1/choose")[0]["choice"] == "trim"


def test_choose_edit_sends_the_creators_lines_and_shows_the_next_card(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _serve(api, proposal())
    nxt = proposal("lf_2", message="Still 25 words; take 2 holds about 22.")
    api.routes[("POST", f"{ROUTE}/lf_1/choose")] = (
        200,
        {"proposal_id": "lf_1", "status": "needs_choice", "next_proposal": nxt},
    )
    lines = tmp_path / "lines.txt"
    lines.write_text("beat_3: I waited for you all winter.\n", encoding="utf-8")

    text = _run(desk, proposal_id="lf_1", choose="edit", edit=f"@{lines}")

    sent = api.posted(f"{ROUTE}/lf_1/choose")[0]
    assert sent["choice"] == "edit"
    assert sent["lines"] == [{"slot": "beat_3", "text": "I waited for you all winter."}]
    assert "Server: needs_choice" in text
    assert "Still 25 words; take 2 holds about 22." in text
    assert "--proposal lf_2 --choose use" in text


def test_edit_without_choose_edit_is_refused_and_nothing_is_sent(
    desk: Path, api: FakeApi
) -> None:
    with pytest.raises(line_fit.LineFitStopped):
        _run(desk, proposal_id="lf_1", edit="beat_3: x")
    assert api.calls == []


def test_redrafting_follows_the_episode_job_and_puts_the_episode_on_the_desk(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(
        api, proposal(source="episode_author", episode_ordinal=2, spine_version=None)
    )
    api.routes[("POST", f"{ROUTE}/lf_1/choose")] = (
        200,
        {"proposal_id": "lf_1", "status": "redrafting", "job_id": "job_ep2"},
    )
    api.jobs["job_ep2"] = {"job_id": "job_ep2", "status": "completed"}
    shown: list[dict[str, Any]] = []
    monkeypatch.setattr(ec, "_show_authored", lambda *a, **k: shown.append(k))

    text = _run(desk, choose="use")

    assert "Server: redrafting (job job_ep2)." in text
    assert api.polled == [("job_ep2", False)]
    assert shown and shown[0]["episode"] == 2 and shown[0]["label"] == "line-fit"


def test_redrafting_a_first_draft_points_the_desk_at_the_new_job(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "new", spine_id=None)
    state = load_production(desk)
    unit = orchestrate.draft_unit(1)
    state.pending[unit] = {"key_prefix": "p-bX", "brief_hash": "X", "key": "p-bX-draft", "attempt": 0,
                           "plan_job_id": "job_plan_1", "spine_id": "sp1",
                           "paused": line_fit.LINE_NEEDS_CREATOR_CHOICE}  # fmt: skip
    save_production(desk, state)
    _serve(
        api,
        proposal(
            source="plan_author", spine_id=None, spine_version=None, job_id="job_plan_1"
        ),
    )
    api.routes[("POST", f"{ROUTE}/lf_1/choose")] = (
        200,
        {"proposal_id": "lf_1", "status": "redrafting", "job_id": "job_plan_2"},
    )
    api.jobs["job_plan_2"] = {
        "job_id": "job_plan_2",
        "status": "completed",
        "spine_id": "sp1",
    }

    text = _run(desk, choose="use")

    assert ("GET", f"{ROUTE}?job_id=job_plan_1", None, None) in api.calls
    record = load_production(desk).pending[unit]
    assert record["plan_job_id"] == "job_plan_2" and "paused" not in record
    assert f"fictora-produce step --desk {desk}" in text


def test_the_cli_runs_line_fit(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(api, proposal())

    assert main(["line-fit", "--desk", str(desk)]) == 0
    assert MESSAGE in capsys.readouterr().out


def test_a_server_without_the_route_is_said_plainly(desk: Path, api: FakeApi) -> None:
    api.routes[("GET", ROUTE)] = lambda *_: (_ for _ in ()).throw(SystemExit("x"))
    api.get_optional = lambda path: (404, {"detail": "Not Found"})  # type: ignore[method-assign]

    with pytest.raises(line_fit.LineFitStopped, match="no line-fit route"):
        _run(desk)


# --- where proposals arrive: a draft, an episode job, a line edit, a brief -------------------------


def _refusal() -> dict[str, Any]:
    return {"error": {"code": "line_needs_creator_choice", "message": MESSAGE,
                      "details": {"fit_proposals": [proposal()]}}}  # fmt: skip


def test_a_draft_that_waits_on_the_creator_prints_the_servers_card(
    desk: Path, api: FakeApi
) -> None:
    """The step pause reads details.fit_proposals; it no longer regex-parses the refusal text."""

    api.spine_doc = {
        **api.spine_doc,
        "episode_summaries": api.spine_doc["episode_summaries"][:1],
    }
    api.routes[("POST", "/v1/prompt-video-authoring-drafts")] = {
        "plan_job_id": "job_plan_1",
        "spine_id": "sp1",
    }
    api.jobs["job_plan_1"] = {
        "status": "failed",
        "error": {
            "code": "authoring_validation_failed",
            "message": "locked_lines_out_of_bounds at scene_prompt: old words; line_needs_creator_choice",
            "details": {
                "cause": "needs_creator_choice",
                "fit_proposals": [proposal(source="plan_author")],
            },
        },
    }
    set_phase(desk, "new", spine_id=None)

    with pytest.raises(RuntimeError) as paused:
        orchestrate.run_step(desk)

    text = str(paused.value)
    assert text.startswith("PAUSED for the creator")
    assert MESSAGE in text and '"I waited every night."' in text
    assert "Show it again: fictora-produce line-fit --desk " in text
    assert "old words" not in text
    record = load_production(desk).pending[orchestrate.draft_unit(1)]
    assert record["paused"] == line_fit.LINE_NEEDS_CREATOR_CHOICE


def test_an_old_servers_locked_lines_pause_still_works() -> None:
    message = "plan failed authoring_validation_failed: locked_lines_out_of_bounds at scene_prompt: Line 2 is 22 words."

    text = orchestrate.locked_lines_pause(message, desk="D")

    assert text is not None and "Line 2 is 22 words." in text
    assert orchestrate.locked_lines_pause_code(message) == "locked_lines_out_of_bounds"


def test_a_line_fit_code_without_its_proposals_points_at_the_command() -> None:
    message = "plan failed x: locked_lines_out_of_bounds at scene_prompt: y; line_needs_creator_choice"

    text = orchestrate.locked_lines_pause(message, desk="D")

    assert orchestrate.locked_lines_pause_code(message) == "line_needs_creator_choice"
    assert text is not None and "fictora-produce line-fit --desk D" in text


def test_a_job_failure_keeps_its_proposals() -> None:
    exc = JobFailed(
        "plan failed",
        job={"status": "failed", "error": {"details": {"fit_proposals": [proposal()]}}},
    )

    assert [p["proposal_id"] for p in line_fit.proposals_from_exit(exc)] == ["lf_1"]
    assert (
        line_fit.failure_cause(
            {"error": {"details": {"cause": "needs_creator_choice"}}}
        )
        == "needs_creator_choice"
    )


def test_an_episode_job_that_waits_shows_the_card(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_a"
    }
    api.jobs["job_a"] = {"status": "failed", "error": {"code": "authoring_validation_failed", "message": "m",
                         "details": {"cause": "needs_creator_choice",
                                     "fit_proposals": [proposal(source="episode_author", episode_ordinal=2)]}}}  # fmt: skip

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_unit(desk, api, unit="author-ep02", path="/v1/spines/sp1/pilot-episodes/2/author", body={},
                    video_route=False, deadline_seconds=60)  # type: ignore[arg-type]  # fmt: skip

    assert MESSAGE in str(stopped.value)
    assert "--proposal lf_1 --choose use" in str(stopped.value)


def test_a_line_edit_answered_409_shows_the_card_and_changes_nothing(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = {
        **api.spine_doc,
        "approval_state": "draft",
        "spine_version": VERSION,
    }
    api.routes[("PATCH", "/v1/spines/sp1")] = ApiRefused("HTTP 409: line_needs_creator_choice", status=409,
                                                        body=_refusal())  # fmt: skip

    with pytest.raises(ec.LineFitPause) as stopped:
        ec.run_line(desk, episode=1, line="1", text="I waited for you every single night this winter.",
                    out=io.StringIO())  # fmt: skip

    assert MESSAGE in str(stopped.value)
    assert "--proposal lf_1 --choose use" in str(stopped.value)


def test_a_brief_edit_answered_409_shows_the_card(desk: Path, api: FakeApi) -> None:
    api.spine_doc = {
        **api.spine_doc,
        "spine_version": VERSION,
        "scene_prompt": "Old brief.",
    }
    api.routes[("PUT", "/v1/spines/sp1/scene-prompt")] = (409, _refusal())
    out = io.StringIO()

    with pytest.raises(ec.CommandStopped) as stopped:
        run_brief_edit(desk, edit="A new brief with a long locked line.", out=out)

    assert MESSAGE in str(stopped.value)
    assert "Not applied" in out.getvalue()
