"""The kit prints the lighter cards' lines from the server (lighter cards v3, 25 Oct 2026), as the app shows them."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from creation.card_lines import card_lines_path, idea_text, read_card_lines, shot_line
from creation.pitch_card import card_text, run_pitch

_SPINE = "spine_1"
_LINES: dict[str, Any] = {
    "show_hook": "A repo man comes to take a cook's arm, and finds his missing sister in it.",
    "story_question": "Will Dez take the arm, or the truth?",
    "place_name": "Night market stall",
    "from_writer": True,
    "pending": False,
    "episodes": [
        {
            "episode_id": "ep01",
            "episode_ordinal": 1,
            "episode_short": [
                "Dez grabs Noor's chrome arm in the middle of dinner rush.",
                "Its scan shows the face of the sister he lost.",
            ],
            "episode_cast_ids": ["cast_01", "cast_02", "cast_03"],
            "seen_only_cast_ids": ["cast_03"],
            "from_writer": True,
        }
    ],
    "cast": [
        {
            "cast_id": "cast_01",
            "role_line": "the cook who won't give up her arm",
            "want_short": "Wants to keep her arm, or her stall shuts.",
            "from_writer": True,
        },
        {
            "cast_id": "cast_02",
            "role_line": "the debt collector who hesitates",
            "want_short": "Wants to finish the job.",
            "from_writer": True,
        },
        {
            "cast_id": "cast_03",
            "role_line": "only seen on the scan",
            "want_short": "",
            "from_writer": True,
        },
    ],
}
_SPINE_BODY = {
    "title": "Three Payments Late",
    "cast": [
        {"cast_id": "cast_01", "name": "Noor Haddad", "short_name": "Noor"},
        {"cast_id": "cast_02", "name": "Dez", "short_name": "Dez"},
        {"cast_id": "cast_03", "name": "His sister"},
    ],
}
_CARD: dict[str, Any] = {
    "episode_ordinal": 1,
    "status": "draft",
    "draft": 1,
    "pitch_digest": "sha256:" + "a" * 64,
    "approved": False,
    "takes": [
        {
            "take": 1,
            "shots": [
                {
                    "shot": 1,
                    "camera": "Close-up",
                    "scene": "Dez grabs Noor's wrist mid-toss, the oil spits across the pass.",
                    "line": {"speaker": "Dez", "text": "Three missed payments."},
                    "opens": True,
                    "shot_label": "Close on Noor's wrist",
                    "shot_camera": "still",
                    "action_short": "Dez grabs Noor's wrist mid-toss.",
                }
            ],
        }
    ],
}


class _Api:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def get_optional(self, path: str) -> tuple[int, Any]:
        self.paths.append(path)
        if path == card_lines_path(_SPINE, 1):
            return 200, {"card_lines": _LINES}
        if path == f"/v1/spines/{_SPINE}":
            return 200, _SPINE_BODY
        return 200, dict(_CARD)

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]:
        raise AssertionError("no post")


def test_the_idea_card_prints_the_servers_lines_in_the_apps_order() -> None:
    text = idea_text(_LINES, _SPINE_BODY, 1)

    lines = text.splitlines()
    assert lines[0] == "Idea card: Three Payments Late"
    assert (
        lines[1].endswith("finds his missing sister in it.")
        and lines[2] == "  The question: Will Dez take the arm, or the truth?"
    )
    assert "    Noor: the cook who won't give up her arm" in lines
    assert "      Wants to keep her arm, or her stall shuts." in lines
    assert "    His sister: only seen on the scan (only seen, no lines)" in lines
    assert lines[-1] == "  Where: Night market stall"
    assert "—" not in text


def test_the_script_prints_one_action_line_the_spoken_line_and_the_shot_line() -> None:
    text = card_text(_CARD)

    assert "    1. [opens] Dez grabs Noor's wrist mid-toss." in text
    assert '       Dez: "Three missed payments."' in text
    assert "       [shot] Close on Noor's wrist · still" in text
    assert (
        shot_line({"shot_label": "Wide on the wok", "shot_camera": "pan"})
        == "Wide on the wok · pan"
    )
    assert shot_line({"camera": "Wide"}) is None


def test_pitch_reads_the_card_lines_first_so_the_card_shows_the_writers_labels(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import creation.post.desk as desk_module

    monkeypatch.setattr(desk_module, "spine_id", lambda desk: _SPINE)
    api = _Api()
    out = io.StringIO()

    run_pitch(tmp_path, episode=1, out=out, api=api)

    assert api.paths.index(card_lines_path(_SPINE, 1)) < api.paths.index(
        f"/v1/spines/{_SPINE}/episodes/1/pitch"
    )
    assert "The question: Will Dez take the arm, or the truth?" in out.getvalue()


def test_an_older_server_prints_no_idea_block() -> None:
    class _Old:
        def get_optional(self, path: str) -> tuple[int, Any]:
            return 404, {"error": {"code": "not_found"}}

    assert read_card_lines(_Old(), _SPINE, 1) is None
