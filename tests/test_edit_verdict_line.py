"""Every `edit` / `line` ends on `Applied` or `Refused: <reason>` (L-20261001-25).

The kit prints "old -> new" before it sends; a refusal used to come last as a
`Stopped:` paragraph and was read past. The last line now says what happened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from fake_api import FakeApi, spine_fixture


def _refuse(api: FakeApi, text: str) -> None:
    def refuse(*_: Any) -> Any:
        raise SystemExit(text)

    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = refuse


def _last(text: str) -> str:
    return [row for row in text.splitlines() if row.strip()][-1]


def _edit_frame(desk: Path, *sets: str) -> int:
    args = [
        "edit",
        "--desk",
        str(desk),
        "--episode",
        "1",
        "--frame",
        "frame_episode_01_02",
    ]
    for value in sets:
        args += ["--set", value]
    return produce_main(args)


def test_a_server_refusal_ends_on_refused_after_the_printed_change(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _refuse(api, "HTTP 400: invalid_patch: The cascade preview edit is invalid.")

    code = _edit_frame(desk, "shot_scale=wide")

    out, err = capsys.readouterr()
    assert code == 2, "a refusal still exits 2"
    assert "->" in out, "the change was printed before sending"
    assert _last(err).startswith("Refused: HTTP 400: invalid_patch")
    assert "Applied" not in out + err


def test_a_batched_refusal_names_each_change_and_says_none_were_made(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _refuse(api, "HTTP 400: invalid_patch: The cascade preview edit is invalid.")

    code = _edit_frame(desk, "shot_scale=wide", "camera_angle=low")

    err = capsys.readouterr().err
    assert code == 2
    assert "  Refused: visual_brief.shot_scale" in err
    assert "  Refused: visual_brief.camera_angle" in err
    assert _last(err).startswith("Refused: ") and _last(err).endswith(
        "(2 changes, none made)"
    )


def test_a_refusal_before_sending_ends_on_refused(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _edit_frame(desk, "subject_blocking.3.pose=x")

    assert code == 2
    assert _last(capsys.readouterr().err).startswith("Refused: ")


def test_an_edit_the_server_took_ends_on_applied(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    api.routes[("PATCH", "/v1/spines/sp1")] = {"spine_version": "v6"}

    code = produce_main(
        [
            "line",
            "--desk",
            str(desk),
            "--episode",
            "1",
            "--line",
            "1",
            "--text",
            "We're shut.",
        ]
    )

    out, err = capsys.readouterr()
    assert code == 0
    assert _last(out) == "Applied"
    assert "Refused" not in out + err


def test_a_batched_edit_the_server_took_names_each_change(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    api.routes[("PATCH", "/v1/spines/sp1")] = {"spine_version": "v6"}

    code = _edit_frame(desk, "shot_scale=wide", "camera_angle=low")

    out = capsys.readouterr().out
    assert code == 0
    assert "  Applied: visual_brief.shot_scale" in out
    assert "  Applied: visual_brief.camera_angle" in out
    assert _last(out) == "Applied: all 2 changes"


def test_listing_the_lines_is_not_an_edit_and_gets_no_verdict(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    code = produce_main(["line", "--desk", str(desk), "--episode", "1"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Applied" not in out and "Refused" not in out


def test_the_verdict_rows() -> None:
    assert ec.change_items(
        [
            "  intent: a  ->  b",
            "  shot_plan:",
            "    was 1: wide",
            "    now 1: close",
            "  !! a warning, not a change",
        ]
    ) == ["intent", "shot_plan"]
    assert ec.edit_verdict(["intent"]) == ["Applied"]
    assert ec.edit_verdict(["intent"], refused="HTTP 409: stale\nfix: x") == [
        "Refused: HTTP 409: stale"
    ]
    assert ec.edit_verdict(["intent", "shot_plan"], not_kept=["shot_plan"]) == [
        "  Applied: intent",
        "  Refused: shot_plan",
        "Refused: the server answered but did not keep shot_plan (likely an older deploy); 1 of 2 applied",
    ]
    assert ec.edit_verdict(["intent"], preview=True)[-1].startswith(
        "Not applied: --preview"
    )
