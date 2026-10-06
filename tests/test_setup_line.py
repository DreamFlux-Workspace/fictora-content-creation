"""``hook-line --setup-line``: a letterbox episode's own setup line (the white title line), 6 Oct 2026.

fictora-drama #628 added ``episode_summaries[].title_line`` (at most 60
characters), set on the opening route the hook line uses:
``PATCH .../episodes/{episode_id}/opening`` with ``title_line: {kind: custom,
text} | {kind: default}``. ``finish``, ``join`` and ``reel`` put it on the
letterbox title block's white line, before the series title. An older server
(the route refuses the field) gets the line kept on the desk
(``shared/setup-line.json``) for finish and join.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any

import pytest
from test_hook_line_command import OPTIONS, FakeApi, hook_desk  # noqa: F401

from creation.hook_line import run_hook_line
from creation.post.letterbox import desk_setup_line, title_block

SETUP = "POV: your roommate texted at 3 a.m."


def _ok(title_line: str | None) -> tuple[int, Any]:
    return 200, {
        "hook_line": OPTIONS[2]["text"],
        "title_line": title_line,
        "warnings": [],
    }


def test_the_setup_line_is_sent_on_the_opening_route(
    hook_desk: tuple[Path, dict],  # noqa: F811
) -> None:
    desk, spine = hook_desk
    api = FakeApi(spine, [_ok(SETUP)])
    out = io.StringIO()

    run_hook_line(desk, episode=1, setup_line=f"  {SETUP} ", out=out, api=api)

    path, body = api.sent[0]
    assert path == "/v1/spines/spine_test/episodes/episode_01/opening"
    assert body == {
        "spine_version": spine["spine_version"],
        "title_line": {"kind": "custom", "text": SETUP},
    }
    assert f"Setup line for episode 1: {SETUP!r}" in out.getvalue()


def test_a_hook_choice_and_a_setup_line_go_as_two_sends(
    hook_desk: tuple[Path, dict],  # noqa: F811
) -> None:
    desk, spine = hook_desk
    api = FakeApi(spine, [(200, {"hook_line": None, "warnings": []}), _ok(SETUP)])

    run_hook_line(
        desk, episode=1, off=True, setup_line=SETUP, out=io.StringIO(), api=api
    )

    assert [body.get("hook_line") for _, body in api.sent] == [{"kind": "off"}, None]
    assert api.sent[1][1]["title_line"] == {"kind": "custom", "text": SETUP}


def test_default_clears_it(hook_desk: tuple[Path, dict]) -> None:  # noqa: F811
    desk, spine = hook_desk
    api = FakeApi(spine, [_ok(None)])
    out = io.StringIO()

    run_hook_line(desk, episode=1, default_setup_line=True, out=out, api=api)

    assert api.sent[0][1]["title_line"] == {"kind": "default"}
    assert "the series title" in out.getvalue()


def test_a_setup_line_over_60_characters_is_refused_before_sending(
    hook_desk: tuple[Path, dict],  # noqa: F811
) -> None:
    desk, spine = hook_desk
    api = FakeApi(spine, [])
    with pytest.raises(ValueError, match="at most 60"):
        run_hook_line(desk, episode=1, setup_line="x" * 61, out=io.StringIO(), api=api)
    with pytest.raises(ValueError, match="not both"):
        run_hook_line(
            desk,
            episode=1,
            setup_line=SETUP,
            default_setup_line=True,
            out=io.StringIO(),
            api=api,
        )
    assert api.sent == []


@pytest.mark.parametrize(
    "answer",
    [
        (
            422,
            {
                "detail": [
                    {
                        "loc": ["body", "title_line"],
                        "msg": "Extra inputs are not permitted",
                    }
                ]
            },
        ),
        (404, {"detail": "Not Found"}),
        (
            200,
            {"hook_line": "x", "warnings": []},
        ),  # took the call, but said nothing of a setup line
    ],
)
def test_an_older_server_gets_the_setup_line_kept_on_the_desk_for_finish_and_join(
    hook_desk: tuple[Path, dict],  # noqa: F811
    answer: tuple[int, Any],
) -> None:
    desk, spine = hook_desk
    out = io.StringIO()

    run_hook_line(
        desk, episode=1, setup_line=SETUP, out=out, api=FakeApi(spine, [answer])
    )

    assert "the app will still show" in out.getvalue()
    assert desk_setup_line(desk, 1) == {"kind": "custom", "text": SETUP}
    block, _ = title_block(spine, 1, desk=desk)
    assert block is not None and block.setup == SETUP
    # A later setup line the server stores drops the desk's.
    run_hook_line(
        desk,
        episode=1,
        setup_line=SETUP,
        out=io.StringIO(),
        api=FakeApi(spine, [_ok(SETUP)]),
    )
    assert desk_setup_line(desk, 1) is None


def test_finish_and_join_prefer_the_episodes_setup_line_over_the_series_title(
    hook_desk: tuple[Path, dict],  # noqa: F811
) -> None:
    desk, spine = hook_desk
    block, _ = title_block(spine, 1, desk=desk)
    assert block is not None and block.setup == "Three Payments Late"

    mine = copy.deepcopy(spine)
    mine["episode_summaries"][0]["title_line"] = SETUP
    block, _ = title_block(mine, 1, desk=desk)
    assert (
        block is not None
        and block.setup == SETUP
        and block.setup_source == "the episode's setup line"
    )
