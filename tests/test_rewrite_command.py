"""`rewrite`: re-write a drafted, unapproved episode from the human's direction (learning #94, Hanakaze ep 7)."""

from __future__ import annotations

import copy
import io
import shutil
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.production_state import load_production, save_production
from fake_api import FakeApi

NOTES = "/v1/spines/sp1/episodes/ep_02/notes"
REWRITE = "/v1/spines/sp1/pilot-episodes/2/rewrite"


def _serve_notes(api: FakeApi) -> None:
    """Notes routes that change the fake's spine like the server: add appends, delete drops, each bumps the version."""

    counter = {"n": 0}

    def bump() -> dict[str, Any]:
        doc = api.spine_doc
        doc["spine_version"] = f"v{int(doc['spine_version'][1:]) + 1}"
        return copy.deepcopy(doc)

    def add(_method: str, _path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        assert body is not None
        assert body["spine_version"] == api.spine_doc["spine_version"]
        counter["n"] += 1
        api.spine_doc["episode_summaries"][1].setdefault("creator_notes", []).append(
            {"note_id": f"note_{counter['n']}", "text": body["text"]}
        )
        return bump()

    def remove(_method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        assert body == {"spine_version": api.spine_doc["spine_version"]}
        note_id = path.rsplit("/", 1)[1]
        summary = api.spine_doc["episode_summaries"][1]
        summary["creator_notes"] = [
            n for n in summary["creator_notes"] if n["note_id"] != note_id
        ]
        return bump()

    api.routes[("POST", NOTES)] = add
    for n in range(1, 5):
        api.routes[("DELETE", f"{NOTES}/note_{n}")] = remove
    api.routes[("DELETE", f"{NOTES}/note_human")] = remove


def _rewritten(api: FakeApi, job: str) -> None:
    def admit(_method: str, _path: str, body: dict[str, Any] | None) -> Any:
        assert body == {"spine_version": api.spine_doc["spine_version"]}
        api.spine_doc["episode_summaries"][1]["title"] = "The sugar"
        api.spine_doc["episode_summaries"][1]["summary"] = "Ren swaps the sugar."
        return {
            "request_id": "r1",
            "spine_id": "sp1",
            "episode_ordinal": 2,
            "rewrite_job_id": job,
            "status": "queued",
        }

    api.routes[("POST", REWRITE)] = admit
    api.jobs[job] = {"status": "completed"}


def test_rewrite_adds_the_note_rewrites_polls_and_prints_like_author(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve_notes(api)
    _rewritten(api, "job_pilot_rewrite_1")
    out = io.StringIO()

    ec.run_rewrite(
        desk, episode=2, line="  Ren  swaps the sugar ", title="Sabotage", out=out
    )

    assert api.posted(NOTES) == [
        {"spine_version": "v5", "text": "Sabotage: Ren swaps the sugar"}
    ]
    assert api.posted(REWRITE) == [{"spine_version": "v6"}]
    key = next(k for m, p, _, k in api.calls if m == "POST" and p == REWRITE)
    assert key and key.endswith("-rewrite-ep02-a1")
    assert api.polled == [("job_pilot_rewrite_1", False)]
    state = load_production(desk)
    assert (state.episode_ordinal, state.phase) == (2, "wait_script")
    assert state.rewrite_notes == {
        "ep_02": {"note_id": "note_1", "text": "Sabotage: Ren swaps the sugar"}
    }
    assert state.pending == {} and state.attempts["rewrite-ep02"] == 1
    printed = out.getvalue()
    assert "ep02 (ep_02) The sugar" in printed
    assert "Ren swaps the sugar." in printed
    assert "We're closed." in printed
    assert (
        "Next: read the lines and shots above; say yes "
        "(`fictora-produce approve --gate script`) or edit them."
        in capsys.readouterr().err
    )
    assert list((desk / "api").glob("rewrite-ep02-terminal*.json"))


def test_a_second_rewrite_replaces_the_kits_last_direction_and_keeps_the_humans(
    desk: Path, api: FakeApi
) -> None:
    _serve_notes(api)
    api.spine_doc["episode_summaries"][1]["creator_notes"] = [
        {"note_id": "note_human", "text": "Keep Hana dry."}
    ]
    _rewritten(api, "job_rw_1")
    ec.run_rewrite(desk, episode=2, line="Ren swaps the sugar.", out=io.StringIO())
    _rewritten(api, "job_rw_2")

    ec.run_rewrite(desk, episode=2, line="Ren confesses instead.", out=io.StringIO())

    deleted = [p for m, p, _, _ in api.calls if m == "DELETE"]
    assert deleted == [f"{NOTES}/note_1"]
    notes = api.spine_doc["episode_summaries"][1]["creator_notes"]
    assert [n["text"] for n in notes] == ["Keep Hana dry.", "Ren confesses instead."]
    second = [k for m, p, _, k in api.calls if m == "POST" and p == REWRITE][1]
    assert second.endswith("-rewrite-ep02-a2")
    assert load_production(desk).rewrite_notes["ep_02"]["note_id"] == "note_2"


def test_an_interrupted_rewrite_picks_up_its_job_and_adds_no_second_note(
    desk: Path, api: FakeApi
) -> None:
    _serve_notes(api)
    state = load_production(desk)
    state.pending["rewrite-ep02"] = {"key": "k-rewrite-ep02-a1", "job_id": "job_rw_1"}
    save_production(desk, state)
    api.jobs["job_rw_1"] = {"status": "completed"}

    ec.run_rewrite(desk, episode=2, line="Ren swaps the sugar.", out=io.StringIO())

    assert api.posted(NOTES) == [] and api.posted(REWRITE) == []
    assert api.polled == [("job_rw_1", False)]


def test_episode_one_is_refused_before_anything_is_sent(
    desk: Path, api: FakeApi
) -> None:
    with pytest.raises(ec.CommandStopped, match="episode 1 is written by the draft"):
        ec.run_rewrite(desk, episode=1, line="Make it rain.", out=io.StringIO())
    assert api.calls == []


def test_an_approved_episode_is_refused_and_points_to_edit(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc["episode_summaries"][1]["authoring_state"] = "approved"

    with pytest.raises(
        ec.CommandStopped, match=r"approved.*`edit --episode 2` / `line`"
    ):
        ec.run_rewrite(desk, episode=2, line="Ren swaps the sugar.", out=io.StringIO())
    assert [c for c in api.calls if c[0] != "GET"] == []


def test_an_episode_not_written_yet_points_to_author(desk: Path, api: FakeApi) -> None:
    api.spine_doc["episode_summaries"][1]["authoring_state"] = "summary_only"

    with pytest.raises(ec.CommandStopped, match="author --episode 2"):
        ec.run_rewrite(desk, episode=2, line="Ren swaps the sugar.", out=io.StringIO())
    assert [c for c in api.calls if c[0] != "GET"] == []


def test_a_line_over_the_author_limit_is_refused_before_sending(
    desk: Path, api: FakeApi
) -> None:
    with pytest.raises(ec.CommandStopped, match="the server takes at most 400"):
        ec.run_rewrite(desk, episode=2, line="x" * 401, out=io.StringIO())
    assert [c for c in api.calls if c[0] != "GET"] == []


def test_rewrite_on_a_shared_story_is_refused_unless_ok(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(desk, desk.parent / "closing-time-copy")
    argv = [
        "rewrite",
        "--desk",
        str(desk),
        "--episode",
        "2",
        "--line",
        "Ren swaps the sugar.",
    ]

    assert produce_main(argv) == 2
    assert "--shared-spine-ok" in capsys.readouterr().err
    assert api.calls == []

    _serve_notes(api)
    _rewritten(api, "job_rw_1")
    assert produce_main([*argv, "--shared-spine-ok"]) == 0
    assert api.posted(REWRITE) == [{"spine_version": "v6"}]


def test_run_unit_reads_the_rewrite_job_id() -> None:
    assert (
        ec.admitted_job_id({"rewrite_job_id": "job_rw", "status": "queued"}) == "job_rw"
    )
