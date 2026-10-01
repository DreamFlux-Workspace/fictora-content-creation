"""The server's ``minor_in_intimate_scene`` refusal in the operator's words (fictora-drama #562).

A child is never in a romantic, sexual or intimate scene, not even as a
bystander. The server refuses it at every free step (authoring, spine edits,
board and film admission, the estimate) with ``details.scenes[]``. The kit
names each scene and child and says how to fix it: move the child out of that
shot, or change the scene.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fake_api import FakeApi

from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.harness.http_util import api_error_text, describe_job_error

ENVELOPE = {
    "error": {
        "code": "minor_in_intimate_scene",
        "message": "Episode 1 frame 4 (frames.frame_ep01_04) is intimate ('they kiss') and Mina is under 18 ...",
        "details": {
            "scenes": [
                {"path": "frames.frame_ep01_04", "kind": "frame", "episode_ordinal": 1, "ordinal": 4,
                 "minors": ["Mina"], "phrase": "they kiss"},
                {"path": "beats.beat_ep01_02", "kind": "beat", "episode_ordinal": 1, "ordinal": 2,
                 "minors": ["Mina", "Jun"], "phrase": "make love"},
            ]
        },
    },
    "request_id": "req-1",
}  # fmt: skip


def test_the_refusal_names_each_scene_and_child_and_the_fix() -> None:
    text = api_error_text(ENVELOPE)

    assert text.startswith("minor_in_intimate_scene: ")
    assert (
        "never in a romantic, sexual or intimate scene, not even as a bystander" in text
    )
    assert (
        '- episode 1 frame 4 (frame_ep01_04): Mina is under 18 and on screen; "they kiss" makes it intimate'
        in text
    )
    assert (
        "move Mina out of that shot (`edit --episode 1 --frame frame_ep01_04 --set 'cast_refs=[...]'`"
        in text
    )
    assert (
        "- episode 1 beat 2 (beat_ep01_02): Mina and Jun are under 18 and on screen"
        in text
    )
    assert "move Mina and Jun out of that beat (`edit --episode 1 --beat 2`" in text
    assert "or change the scene so it is not intimate" in text
    assert "Never age Mina up" in text


def test_a_job_that_failed_on_it_gets_the_general_fix() -> None:
    text = describe_job_error(
        {
            "status": "failed",
            "error": {
                "code": "minor_in_intimate_scene",
                "message": "Episode 2 frame 1 ...",
            },
        }
    )

    assert "fix: move the child out of that shot" in text
    assert "change the scene so it is not intimate" in text


def test_an_edit_the_server_refuses_for_it_ends_on_refused_with_the_fix(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_: Any) -> Any:
        # What the session raises: the status line, then api_error_text of the envelope.
        raise SystemExit(
            f"HTTP 422 POST /v1/spines/sp1/cascade/preview: {api_error_text(ENVELOPE)}"
        )

    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = refuse

    code = produce_main(
        [
            "edit",
            "--desk",
            str(desk),
            "--episode",
            "1",
            "--frame",
            "frame_episode_01_02",
            "--set",
            "shot_scale=wide",
        ]
    )

    err = capsys.readouterr().err
    assert code == 2
    assert "move Mina out of that shot" in err
    assert [row for row in err.splitlines() if row.strip()][-1].startswith(
        "Refused: HTTP 422 POST /v1/spines/sp1/cascade/preview: minor_in_intimate_scene"
    )


def test_explain_refusal_adds_the_fix_once() -> None:
    bare = (
        "HTTP 422 PATCH /v1/spines/sp1: minor_in_intimate_scene: Episode 1 frame 4 ... "
        f"(details {json.dumps(ENVELOPE['error']['details'])})"
    )

    explained = ec.explain_refusal(bare, {"cast": []}, episode=1)
    again = ec.explain_refusal(explained, {"cast": []}, episode=1)

    assert "move Mina out of that shot" in explained
    assert again.count("move Mina out of that shot") == 1
