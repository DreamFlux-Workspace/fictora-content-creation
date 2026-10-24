"""``hook-line --titles | --title K | --no-title``: the server's letterbox title card, the same one the app shows.

Founder, 24 Oct 2026: one source for kit and app. The kit prints and picks
from ``GET …/title-card`` and keeps no list of its own; "No title" leaves the
white line out of the kit's letterbox file too.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any

from tests import test_hook_line_command as _hook_line_tests

from creation.hook_line import run_hook_line
from creation.post.letterbox import title_block

#: The shared desk fixture of the hook-line tests.
hook_desk = _hook_line_tests.hook_desk

CARD = {
    "due": True,
    "lead": "Your letterbox has room for a title above the picture. Pick one, or swipe for more.",
    "options": [
        {"kind": "suggested", "text": "Noor Owes the Syndicate", "label": "Noor Owes…"},
        {"kind": "series", "text": "Three Payments Late", "label": "Three Paym…"},
        {"kind": "none", "text": None, "label": "No title"},
    ],
    "picked_index": 0,
    "hook_line": "The arm knows his sister",
    "hook_label": "Yellow line · the writer's pick",
    "note": "Free to change until the video is made; after that, a free re-finish.",
}


class CardApi:
    def __init__(self, spine: dict[str, Any], answers: list[tuple[int, Any]]) -> None:
        self.stored = spine
        self.answers = list(answers)
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.gets: list[str] = []

    def spine(self, spine_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.stored)

    def get_optional(self, path: str) -> tuple[int, Any]:
        self.gets.append(path)
        return 200, copy.deepcopy(CARD)

    def patch_optional(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        self.sent.append((path, body))
        return self.answers.pop(0)


def test_titles_prints_the_server_card_word_for_word(
    hook_desk: tuple[Path, dict],
) -> None:
    desk, spine = hook_desk
    api = CardApi(spine, [])
    out = io.StringIO()

    run_hook_line(desk, episode=1, titles=True, out=out, api=api)

    text = out.getvalue()
    assert (
        api.gets == ["/v1/spines/spine_test/episodes/episode_01/title-card"]
        and api.sent == []
    )
    assert CARD["lead"] in text
    assert "1. 'Noor Owes the Syndicate'  <- picked" in text and "3. No title" in text
    assert "The arm knows his sister" in text


def test_title_k_sends_the_preview_and_no_title_sends_off(
    hook_desk: tuple[Path, dict],
) -> None:
    desk, spine = hook_desk
    api = CardApi(
        spine,
        [
            (200, {"title_line": "Three Payments Late", "title_line_off": False}),
            (
                200,
                {
                    "title_line": None,
                    "title_line_off": True,
                    "line": "Done. Episode 1's video is re-finished.",
                },
            ),
        ],
    )
    out = io.StringIO()

    run_hook_line(desk, episode=1, title=2, out=out, api=api)
    run_hook_line(desk, episode=1, title=3, out=out, api=api)

    assert api.sent[0][1]["title_line"] == {
        "kind": "custom",
        "text": "Three Payments Late",
    }
    assert api.sent[1][1]["title_line"] == {"kind": "off"}
    assert "none (no white line)" in out.getvalue() and "re-finished" in out.getvalue()


def test_no_title_on_the_server_leaves_the_white_line_out_of_the_kit_file() -> None:
    spine = {"title": "Three Payments Late", "episode_summaries": [{"ordinal": 1, "episode_id": "episode_01",
             "title_line_off": True, "hook_line_selected": {"kind": "custom", "text": "Pay up"}}]}  # fmt: skip
    block, _why = title_block(spine, 1)
    assert block is not None and block.setup == "" and block.hook == "Pay up"
