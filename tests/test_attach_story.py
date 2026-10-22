"""Attach an existing server story to a new desk, find stories by title, and detach (fake server).

Founder approval, 21 Oct 2026: the kit works on a creator's app story while its
desk is listed on it; the creator stays the owner. Nothing here calls the real server.
"""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from creation.attach_story import (
    AttachRefused,
    attach_story,
    detach_story,
    last_written_episode,
    stories_command,
)

SPINE_ID = "spine_video_hana"
SPINE = {
    "spine_id": SPINE_ID,
    "title": "Don't Look, Hana",
    "logline": "A girl lies to her boyfriend about the ghost in the lift.",
    "duration_band": "30s",
    "spoken_language": "en-US",
    "art_style_preset_id": "anime-soft",
    "art_style_preset_version": "v3",
    "rules_epoch": "2026-10-06",
    "episode_summaries": [
        {"episode_id": "ep01", "ordinal": 1, "authoring_state": "written"},
        {"episode_id": "ep02", "ordinal": 2, "authoring_state": "written"},
        {"episode_id": "ep03", "ordinal": 3, "authoring_state": "summary_only"},
    ],
    "beats": [{"episode_id": "ep02"}],
}


class _Client:
    def close(self) -> None:
        pass


class FakeSession:
    """Records calls and answers like the server's operator routes."""

    def __init__(
        self,
        *,
        post_status: int = 200,
        spine: dict[str, Any] | None = None,
        fail_get: bool = False,
    ):
        self.calls: list[tuple[str, str, Any]] = []
        self.post_status = post_status
        self.spine = spine or SPINE
        self.fail_get = fail_get
        self.client = _Client()

    def post_optional(self, path: str, body: Any):
        self.calls.append(("POST", path, body))
        if self.post_status == 404:
            return 404, {"detail": "Not Found"}
        if self.post_status >= 300:
            return self.post_status, {
                "error": {
                    "code": "operator_only",
                    "message": "Not on a creator session.",
                }
            }
        return 200, {
            "spine_id": SPINE_ID,
            "owner_kind": "app_creator",
            "collaborators": [body["session_id"]],
            "changed": True,
        }

    def get(self, path: str):
        self.calls.append(("GET", path, None))
        if self.fail_get:
            raise RuntimeError("read failed")
        return self.spine

    def get_optional(self, path: str):
        self.calls.append(("GET", path, None))
        if path.endswith("/caption-look"):
            return 200, {"caption_look": "bold", "source": "show"}
        if path.startswith("/v1/operator/spines"):
            return 200, {
                "spines": [
                    {
                        "spine_id": SPINE_ID,
                        "title": SPINE["title"],
                        "owner_kind": "app_creator",
                        "episodes_written": 2,
                        "updated_at": "2026-10-21T08:00:00Z",
                    }
                ]
            }
        return 404, {"detail": "Not Found"}

    def delete_optional(self, path: str):
        self.calls.append(("DELETE", path, None))
        return 200, {
            "spine_id": SPINE_ID,
            "owner_kind": "app_creator",
            "collaborators": [],
            "changed": True,
        }


def _opener(session: FakeSession):
    return lambda _session_id, _out_dir: session


def test_attach_lists_the_desk_then_builds_it_from_the_server(tmp_path: Path) -> None:
    session = FakeSession()

    result = attach_story(
        SPINE_ID,
        parent=tmp_path,
        opener=_opener(session),
        today=date(2026, 10, 21),
        out=io.StringIO(),
    )

    method, path, body = session.calls[0]
    assert (method, path) == ("POST", f"/v1/spines/{SPINE_ID}/collaborators")
    assert body == {"session_id": result.session_id}
    assert result.episode == 2
    production = json.loads((result.desk / "production.json").read_text())
    assert (
        production["spine_id"] == SPINE_ID
        and production["session_id"] == result.session_id
    )
    attach = json.loads((result.desk / "api" / "attach.json").read_text())
    assert attach["owner_kind"] == "app_creator" and attach["detached_at"] is None


def test_a_refused_read_takes_the_listing_off_and_leaves_no_desk(
    tmp_path: Path,
) -> None:
    session = FakeSession(fail_get=True)

    with pytest.raises(RuntimeError):
        attach_story(
            SPINE_ID,
            parent=tmp_path,
            opener=_opener(session),
            today=date(2026, 10, 21),
            out=io.StringIO(),
        )

    assert session.calls[-1][0] == "DELETE"
    assert not any(tmp_path.iterdir())


def test_an_older_server_is_refused_before_anything_changes(tmp_path: Path) -> None:
    session = FakeSession(post_status=404)

    with pytest.raises(AttachRefused, match="server too old"):
        attach_story(
            SPINE_ID,
            parent=tmp_path,
            opener=_opener(session),
            today=date(2026, 10, 21),
            out=io.StringIO(),
        )

    assert [call[0] for call in session.calls] == ["POST"]
    assert not any(tmp_path.iterdir())


def test_a_server_refusal_changes_nothing(tmp_path: Path) -> None:
    session = FakeSession(post_status=403)

    with pytest.raises(AttachRefused, match="did not attach"):
        attach_story(
            SPINE_ID,
            parent=tmp_path,
            opener=_opener(session),
            today=date(2026, 10, 21),
            out=io.StringIO(),
        )

    assert not any(tmp_path.iterdir())


def test_detach_takes_the_desk_off_and_records_it(tmp_path: Path) -> None:
    session = FakeSession()
    result = attach_story(
        SPINE_ID,
        parent=tmp_path,
        opener=_opener(session),
        today=date(2026, 10, 21),
        out=io.StringIO(),
    )

    code = detach_story(result.desk, opener=_opener(session), out=io.StringIO())

    assert code == 0
    assert session.calls[-1] == (
        "DELETE",
        f"/v1/spines/{SPINE_ID}/collaborators/{result.session_id}",
        None,
    )
    assert json.loads((result.desk / "api" / "attach.json").read_text())["detached_at"]


def test_detach_refuses_a_desk_that_was_never_attached(tmp_path: Path) -> None:
    assert detach_story(tmp_path, opener=_opener(FakeSession()), out=io.StringIO()) == 2


def test_stories_prints_the_server_s_rows_without_changing_anything() -> None:
    session = FakeSession()
    out = io.StringIO()

    assert stories_command("Hana", opener=_opener(session), out=out) == 0

    assert SPINE_ID in out.getvalue() and "app creator" in out.getvalue()
    assert all(call[0] == "GET" for call in session.calls)


def test_last_written_episode_skips_summary_only_episodes() -> None:
    assert last_written_episode(SPINE) == 2
