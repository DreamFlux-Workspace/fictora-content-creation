"""``hook-line``: list and choose an episode's hook line on the app's opening route (free)."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest

from creation.hook_line import run_hook_line
from creation.post.letterbox import desk_hook_line, title_block

OPTIONS = [
    {"style": "stakes", "text": "Miss three payments, lose everything"},
    {"style": "rule", "text": "The clinic takes back chrome"},
    {"style": "twist", "text": "The arm knows his sister"},
]


class FakeApi:
    """Answers the opening route with scripted statuses; ``spine`` returns the story as stored."""

    def __init__(self, spine: dict[str, Any], answers: list[tuple[int, Any]]) -> None:
        self.stored = spine
        self.answers = list(answers)
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.reads = 0

    def spine(self, spine_id: str) -> dict[str, Any]:
        self.reads += 1
        return copy.deepcopy(self.stored)

    def patch_optional(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        self.sent.append((path, body))
        return self.answers.pop(0)


@pytest.fixture
def hook_desk(post_desk: Path) -> tuple[Path, dict[str, Any]]:
    api = post_desk / "ep01" / "api"
    spine = json.loads((api / "03_spine.json").read_text(encoding="utf-8"))
    spine["title"] = "Three Payments Late"
    spine["episode_summaries"][0]["hook_line_options"] = OPTIONS
    spine["episode_summaries"][0]["hook_line_selected"] = {"kind": "option", "index": 2,
                                                           "text": OPTIONS[2]["text"]}  # fmt: skip
    (api / "03_spine.json").write_text(json.dumps(spine), encoding="utf-8")
    return post_desk, spine


def _ok(text: str | None) -> tuple[int, Any]:
    return 200, {
        "hook_line": text,
        "warnings": [{"code": "hook_line_long", "message": "A long hook line."}],
    }


def test_list_reads_the_saved_spine_and_sends_nothing(
    hook_desk: tuple[Path, dict],
) -> None:
    desk, _ = hook_desk
    out = io.StringIO()
    run_hook_line(desk, episode=1, list_only=True, out=out)
    text = out.getvalue()
    assert " 1. Miss three payments, lose everything [stakes]" in text
    assert " *3. The arm knows his sister [twist]" in text
    assert "nothing was sent" in text


def test_pick_sends_the_option_on_the_opening_route(
    hook_desk: tuple[Path, dict],
) -> None:
    desk, spine = hook_desk
    api = FakeApi(spine, [_ok(OPTIONS[0]["text"])])
    out = io.StringIO()
    run_hook_line(desk, episode=1, pick=1, out=out, api=api)
    path, body = api.sent[0]
    assert path == "/v1/spines/spine_test/episodes/episode_01/opening"
    assert body == {
        "spine_version": spine["spine_version"],
        "hook_line": {"kind": "option", "index": 0},
    }
    assert (
        "Hook line for episode 1: 'Miss three payments, lose everything'"
        in out.getvalue()
    )
    assert "note: A long hook line." in out.getvalue()


def test_a_stale_version_is_read_again_and_sent_once_more_never_twice_more(
    hook_desk: tuple[Path, dict],
) -> None:
    desk, spine = hook_desk
    newer = copy.deepcopy(spine)
    newer["spine_version"] = "sha256:" + "1" * 64
    stale = (409, {"error": {"code": "spine_version_conflict", "message": "stale"}})
    api = FakeApi(newer, [stale, _ok("He came to take her arm.")])
    run_hook_line(
        desk, episode=1, text="He came to take her arm.", out=io.StringIO(), api=api
    )
    assert len(api.sent) == 2 and api.reads >= 2, (
        "the spine was read again before the second send"
    )
    assert api.sent[1][1]["spine_version"] == newer["spine_version"]
    assert api.sent[1][1]["hook_line"] == {
        "kind": "custom",
        "text": "He came to take her arm.",
    }
    api = FakeApi(newer, [stale, stale])
    with pytest.raises(RuntimeError, match="HTTP 409"):
        run_hook_line(desk, episode=1, pick=2, out=io.StringIO(), api=api)
    assert len(api.sent) == 2


@pytest.mark.parametrize("status", [404, 405])
def test_an_older_server_gets_the_pick_recorded_on_the_desk_for_finish_and_join(
    hook_desk: tuple[Path, dict], status: int
) -> None:
    desk, spine = hook_desk
    api = FakeApi(spine, [(status, {"detail": "Not Found"})])
    out = io.StringIO()
    run_hook_line(
        desk,
        episode=1,
        text="He came to take her arm. It came with a secret.",
        out=out,
        api=api,
    )
    assert "the app will still show the server's pick" in out.getvalue()
    assert (
        desk_hook_line(desk, 1)["text"]
        == "He came to take her arm. It came with a secret."
    )
    block, _ = title_block(spine, 1, desk=desk)
    assert block.hook == "He came to take her arm. It came with a secret."
    # A later pick the server stores drops the desk's.
    api = FakeApi(spine, [_ok(OPTIONS[1]["text"])])
    run_hook_line(desk, episode=1, pick=2, out=io.StringIO(), api=api)
    assert desk_hook_line(desk, 1) is None


def test_a_known_refusal_is_said_plainly_and_nothing_is_recorded(
    hook_desk: tuple[Path, dict],
) -> None:
    desk, spine = hook_desk
    with pytest.raises(ValueError, match="no option 4"):
        run_hook_line(
            desk, episode=1, pick=4, out=io.StringIO(), api=FakeApi(spine, [])
        )
    refused = (
        404,
        {"error": {"code": "episode_not_found", "message": "no such episode"}},
    )
    with pytest.raises(RuntimeError, match="episode_not_found"):
        run_hook_line(
            desk, episode=1, off=True, out=io.StringIO(), api=FakeApi(spine, [refused])
        )
    assert desk_hook_line(desk, 1) is None


def test_exactly_one_choice_is_asked_for(hook_desk: tuple[Path, dict]) -> None:
    desk, _ = hook_desk
    with pytest.raises(ValueError, match="exactly one"):
        run_hook_line(desk, episode=1, pick=1, text="x", out=io.StringIO())
