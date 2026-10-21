"""The pitch card is the server's (decision A, 21 Oct 2026): the kit reads, approves and changes it there."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from creation import pitch_card
from creation.harness.http_util import CLIENT_CAPABILITIES_HEADER, api_headers
from creation.pitch_card import (
    PitchError,
    card_text,
    carry_desk_pitch_yes,
    desk_pitch_yes,
    run_approve_pitch,
    run_pitch,
)

_DIGEST = "sha256:" + "a" * 64
_SPINE = "spine_1"


def _card(**update: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "schema_version": "fictora.drama-pitch-card.v1",
        "spine_id": _SPINE,
        "episode_id": "ep01",
        "episode_ordinal": 1,
        "status": "draft",
        "draft": 1,
        "pitch_digest": _DIGEST,
        "approved": False,
        "already_made": False,
        "held": True,
        "takes": [
            {
                "take": 1,
                "shots": [
                    {
                        "shot": 1,
                        "camera": "Close-up · low angle",
                        "camera_move": "Push in",
                        "scene": "Maya slams the ticket rail.",
                        "line": {"speaker": "Maya", "text": "Hands on the pass!"},
                        "expression": "Maya's jaw sets",
                        "opens": True,
                    },
                    {
                        "shot": 2,
                        "camera": "Wide",
                        "scene": "The kitchen freezes.",
                        "ends_on": True,
                    },
                ],
            }
        ],
        "world_rules": ["Nobody leaves the line mid-service."],
    }
    card.update(update)
    return card


class _FakeApi:
    """The server's pitch routes, in memory."""

    def __init__(self, card: dict[str, Any]) -> None:
        self.card = card
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def get_optional(self, path: str) -> tuple[int, Any]:
        assert path == f"/v1/spines/{_SPINE}/episodes/1/pitch"
        return 200, dict(self.card)

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]:
        self.posts.append((path, body))
        if path.endswith("/approve"):
            if body["pitch_digest"] != self.card["pitch_digest"]:
                return 409, {
                    "error": {
                        "code": "pitch_changed",
                        "message": "This card changed since you saw it.",
                    }
                }
            self.card = {
                **self.card,
                "approved": True,
                "status": "approved",
                "held": False,
            }
            return 200, dict(self.card)
        return 202, {"status": "change_requested", "from_draft": 1, "next_draft": 2}


def _desk(tmp_path: Path, *, approved: bool, edited_after: bool = False) -> Path:
    desk = tmp_path / "desk"
    folder = desk / "ep01"
    folder.mkdir(parents=True)
    (desk / "production.json").write_text(json.dumps({"spine_id": _SPINE}))
    pitch = {"throughline": "Maya proves she can run the pass."}
    (folder / "pitch-v1.json").write_text(json.dumps({"pitch": pitch}))
    digest = hashlib.sha256(
        json.dumps(
            pitch, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    if approved:
        (folder / "pitch-approvals.json").write_text(
            json.dumps({"approvals": [{"version": 1, "digest": digest}]})
        )
    if edited_after:
        (folder / "pitch-v1.json").write_text(
            json.dumps({"pitch": {"throughline": "Edited by hand."}})
        )
    return desk


@pytest.fixture
def _desk_spine(monkeypatch: pytest.MonkeyPatch) -> None:
    import creation.post.desk as desk_module

    monkeypatch.setattr(desk_module, "spine_id", lambda desk: _SPINE)


def test_every_kit_request_announces_it_shows_the_pitch_card() -> None:
    """So the server holds paid drawing and filming until the card's yes, as for the app."""

    assert api_headers("t", "s")[CLIENT_CAPABILITIES_HEADER] == "pitch-card-1"


def test_the_card_prints_shots_lines_faces_and_world_rules() -> None:
    text = card_text(_card())

    assert "Pitch card, episode 1 (draft 1, NOT approved yet)" in text
    assert (
        "1. Close-up · low angle · Push in [opens]: Maya slams the ticket rail." in text
    )
    assert 'Maya: "Hands on the pass!"' in text and "(Maya's jaw sets)" in text
    assert "2. Wide [ends on]" in text
    assert "- Nobody leaves the line mid-service." in text
    # The writer decides shouts and voices: nothing on the card asks about them.
    assert "shout" not in text.lower() and "voice" not in text.lower()


def test_a_card_before_the_script_is_not_ready() -> None:
    assert "Not ready: the episode's script is not written yet." in card_text(
        _card(status="not_ready")
    )


@pytest.mark.usefixtures("_desk_spine")
def test_approve_sends_the_yes_on_the_draft_read_from_the_server(
    tmp_path: Path,
) -> None:
    api = _FakeApi(_card())
    out = io.StringIO()

    card = run_approve_pitch(tmp_path, episode=1, out=out, api=api)

    assert card["approved"] is True
    assert api.posts == [
        (
            f"/v1/spines/{_SPINE}/episodes/1/pitch/approve",
            {"pitch_digest": _DIGEST, "draft": 1, "via": "tap"},
        )
    ]
    assert "Pitch approved on the server: episode 1, draft 1." in out.getvalue()
    # Nothing is written on the desk.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.usefixtures("_desk_spine")
def test_an_approved_card_is_not_approved_twice(tmp_path: Path) -> None:
    api = _FakeApi(_card(approved=True, status="approved"))

    run_approve_pitch(tmp_path, episode=1, out=io.StringIO(), api=api)

    assert api.posts == []


@pytest.mark.usefixtures("_desk_spine")
def test_a_card_before_the_script_cannot_be_approved(tmp_path: Path) -> None:
    with pytest.raises(PitchError, match="script is not written yet"):
        run_approve_pitch(
            tmp_path,
            episode=1,
            out=io.StringIO(),
            api=_FakeApi(_card(status="not_ready")),
        )


@pytest.mark.usefixtures("_desk_spine")
def test_pitch_prints_the_card_and_records_a_change(tmp_path: Path) -> None:
    api = _FakeApi(_card())
    out = io.StringIO()

    assert (
        run_pitch(tmp_path, episode=1, change="End on Dev, not Maya.", out=out, api=api)
        == 0
    )

    assert api.posts == [
        (
            f"/v1/spines/{_SPINE}/episodes/1/pitch/changes",
            {"pitch_digest": _DIGEST, "message": "End on Dev, not Maya."},
        )
    ]
    text = out.getvalue()
    assert "Change recorded on episode 1's card (draft 1)" in text
    assert "approve --desk" in text and "--gate pitch --episode 1" in text


def test_a_desk_yes_is_read_only_while_the_desk_card_is_unchanged(
    tmp_path: Path,
) -> None:
    assert desk_pitch_yes(_desk(tmp_path / "a", approved=True), 1) is True
    assert desk_pitch_yes(_desk(tmp_path / "b", approved=False), 1) is False
    assert (
        desk_pitch_yes(_desk(tmp_path / "c", approved=True, edited_after=True), 1)
        is False
    )


def test_a_desk_yes_on_the_episode_in_progress_is_carried_once(tmp_path: Path) -> None:
    desk = _desk(tmp_path, approved=True)
    api = _FakeApi(_card())
    err = io.StringIO()

    assert carry_desk_pitch_yes(desk, api, _SPINE, 1, out=err) is True
    assert "Carried this desk's earlier pitch yes for episode 1" in err.getvalue()
    # Approved on the server now: nothing more to carry.
    assert carry_desk_pitch_yes(desk, api, _SPINE, 1, out=err) is False
    assert len(api.posts) == 1
    # The desk files are left as they were (never written).
    assert sorted(path.name for path in (desk / "ep01").iterdir()) == [
        "pitch-approvals.json",
        "pitch-v1.json",
    ]


@pytest.mark.parametrize(
    "update",
    [
        {
            "draft": 2
        },  # the card moved on after the desk yes: the human approves the new draft
        {"already_made": True},  # on its boards: never held anyway
        {"status": "not_ready"},
        {"approved": True, "status": "approved"},
    ],
)
def test_a_desk_yes_is_never_carried_onto_a_card_that_moved_on(
    tmp_path: Path, update: dict[str, Any]
) -> None:
    api = _FakeApi(_card(**update))

    assert (
        carry_desk_pitch_yes(
            _desk(tmp_path, approved=True), api, _SPINE, 1, out=io.StringIO()
        )
        is False
    )
    assert api.posts == []


def test_no_desk_yes_carries_nothing(tmp_path: Path) -> None:
    api = _FakeApi(_card())

    assert (
        carry_desk_pitch_yes(
            _desk(tmp_path, approved=False), api, _SPINE, 1, out=io.StringIO()
        )
        is False
    )
    assert api.posts == []


def test_the_server_hold_tells_the_operator_what_to_run() -> None:
    import httpx

    from creation.harness.session import ApiRefused, DramaApiRunSession

    request = httpx.Request("POST", "https://drama.test/v1/video-generations")
    response = httpx.Response(
        409,
        request=request,
        json={
            "error": {
                "code": "pitch_not_approved",
                "message": "Approve episode 2's card first. Nothing was sent.",
            }
        },
    )
    session = DramaApiRunSession.__new__(DramaApiRunSession)

    with pytest.raises(ApiRefused) as caught:
        session._ok(response)

    assert "Approve episode 2's card first. Nothing was sent." in str(caught.value)
    assert "approve --desk D --gate pitch" in str(caught.value)


def test_the_old_desk_files_are_not_a_source_of_truth_any_more() -> None:
    """No desk card store, desk approvals, field checks or shot mirror are left in the kit."""

    for name in (
        "store_pitch",
        "approve_pitch",
        "validate_pitch",
        "pitch_gate_refusal",
        "script_pitch_lines",
    ):
        assert not hasattr(pitch_card, name), name
    with pytest.raises(ImportError):
        __import__("creation.pitch_shots")
