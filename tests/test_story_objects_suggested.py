"""A story object the server only suggests is never drawn, priced or paid by the kit (Long Shadow, GRAFT).

Action-series review, 14-15 Oct 2026: each approval that wrote an episode let
the server add up to three story objects, and the next plates step drew each
one ($0.30) without anyone asking; someone had to run ``props`` after every
approval to catch them. The server now marks those cards ``proposed`` and owes
them no picture. The kit reads that mark (it keeps no list of its own): the
objects are not owed, do not route the episode through plates, ``author`` and
``props`` say they wait for a yes, and ``keep-prop`` is that yes (free).
"""

from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation import orchestrate
from creation import prop_commands as pc
from creation.production_state import load_production
from creation.story_objects import (
    new_objects,
    new_objects_notice,
    objects_owing_approval,
    objects_to_draw,
    owed_pictures,
    picture_state,
    suggested_objects,
)
from fake_api import FakeApi

SIGN_ID = "prop_wooden_sign"
KEEP = f"/v1/spines/sp1/props/{SIGN_ID}/keep"
SUGGESTED = {
    "prop_id": SIGN_ID,
    "name": "Wooden Sign",
    "object_kind": "sign",
    "scale_clause": "a shop sign",
    "intro_episode_ordinal": 2,
    "proposed": True,
}
KEPT = {key: value for key, value in SUGGESTED.items() if key != "proposed"}


def test_a_suggested_object_is_never_owed_a_picture() -> None:
    spine: dict[str, Any] = {"props": [SUGGESTED], "media_assets": []}
    assert objects_to_draw(spine, episode=2) == []
    assert objects_owing_approval(spine, episode=2) == []
    assert owed_pictures(spine)[1] == []
    assert picture_state(spine, SIGN_ID) == "suggested"
    assert suggested_objects(spine) == [(SIGN_ID, "Wooden Sign")]
    # Kept (the server drops the mark): owed, as before.
    kept = {"props": [KEPT], "media_assets": []}
    assert objects_to_draw(kept, episode=2) == [(SIGN_ID, "Wooden Sign")]
    assert picture_state(kept, SIGN_ID) == "none"


def test_author_says_a_suggestion_costs_nothing_until_kept() -> None:
    (line,) = new_objects_notice(
        new_objects({"props": []}, {"props": [SUGGESTED]}),
        suggested={SIGN_ID},
        desk="desks/x",
    )
    assert line.startswith("story object suggested: Wooden Sign (prop_wooden_sign).")
    assert "Nothing is drawn or paid unless the human says yes" in line
    assert 'fictora-produce keep-prop --desk desks/x --prop "Wooden Sign"' in line
    assert "remove-prop" in line


def test_the_script_yes_never_routes_a_suggestion_through_plates(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2"
    }
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    ec.run_author(desk, episode=2, out=io.StringIO())
    api.spine_doc = {**api.spine_doc, "props": [SUGGESTED]}
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/approve")] = {
        "spine_version": "v6"
    }

    scripted = orchestrate.approve_gate(desk, gate="script")

    assert "Wooden Sign" not in scripted.message
    assert load_production(desk).phase != "ready_cast_enrol"


def test_author_prints_the_suggestion(desk: Path, api: FakeApi) -> None:
    before = copy.deepcopy(api.spine_doc)

    def author(_m: str, _p: str, _body: dict[str, Any] | None) -> dict[str, Any]:
        api.spine_doc = {**copy.deepcopy(before), "props": [SUGGESTED]}
        return {"extension_job_id": "job_ext_2"}

    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = author
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    out = io.StringIO()

    ec.run_author(desk, episode=2, out=out)

    assert "story object suggested: Wooden Sign" in out.getvalue()
    assert "story object added" not in out.getvalue()


def test_props_marks_a_suggestion_and_says_how_to_keep_it(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc["props"] = [SUGGESTED]
    out = io.StringIO()

    rows = pc.run_props(desk, out=out)

    assert [(r["name"], r["picture"]) for r in rows] == [("Wooden Sign", "suggested")]
    assert "keep-prop" in out.getvalue()
    assert [m for m, *_ in api.calls] == ["GET"], "read-only"


def test_keep_prop_says_yes_on_the_server_with_a_key_and_draws_nothing(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc["props"] = [dict(SUGGESTED)]

    def keep(_m: str, _p: str, _body: dict[str, Any] | None) -> dict[str, Any]:
        api.spine_doc["props"] = [dict(KEPT)]
        return {**api.spine_doc, "draw_cost_usd": 0.3}

    api.routes[("POST", KEEP)] = keep
    out = io.StringIO()

    pc.run_keep_prop(desk, prop="wooden sign", out=out)

    prefix = load_production(desk).idempotency_prefix
    assert [k for m, p, _b, k in api.calls if m == "POST"] == [
        f"{prefix}-prop-keep-{SIGN_ID}"
    ]
    assert all(p != "/v1/spines/sp1/cast/enrol" for _m, p, *_ in api.calls), (
        "keeping draws nothing"
    )
    assert "is kept. Free; nothing was drawn" in out.getvalue()
    assert "$0.30" in out.getvalue()


def test_keep_prop_on_a_kept_object_sends_nothing(desk: Path, api: FakeApi) -> None:
    api.spine_doc["props"] = [dict(KEPT)]
    out = io.StringIO()

    pc.run_keep_prop(desk, prop="Wooden Sign", out=out)

    assert [m for m, *_ in api.calls] == ["GET"]
    assert "already kept" in out.getvalue()


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        ((404, {"error": {"code": "prop_not_found"}}), "no longer on the story"),
        ((404, {"detail": "Not Found"}), "needs the server update"),
    ],
)
def test_keep_prop_says_the_servers_refusal_plainly(
    desk: Path, api: FakeApi, answer: tuple[int, Any], says: str
) -> None:
    api.spine_doc["props"] = [dict(SUGGESTED)]
    api.routes[("POST", KEEP)] = answer

    with pytest.raises(ec.CommandStopped, match=says):
        pc.run_keep_prop(desk, prop="Wooden Sign", out=io.StringIO())


def test_a_suggestion_can_be_removed_after_boards_are_drawn(
    desk: Path, api: FakeApi
) -> None:
    # The server allows the "no" on a suggestion (it was never drawn into a board); the kit must too.
    api.spine_doc["props"] = [dict(SUGGESTED)]
    boards = desk / "ep01" / "boards"
    boards.mkdir(parents=True, exist_ok=True)
    (boards / "board-01.png").write_bytes(b"png")
    delete = f"/v1/spines/sp1/props/{SIGN_ID}"

    def remove(_m: str, _p: str, _body: dict[str, Any] | None) -> dict[str, Any]:
        api.spine_doc["props"] = []
        return api.spine_doc

    api.routes[("DELETE", delete)] = remove
    out = io.StringIO()

    pc.run_remove_prop(desk, prop="Wooden Sign", out=out)

    assert [(m, p) for m, p, *_ in api.calls if m == "DELETE"] == [("DELETE", delete)]
    assert "is off the story" in out.getvalue()


def test_a_kept_object_is_still_held_once_boards_are_drawn(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc["props"] = [{**KEPT, "intro_episode_ordinal": 1}]
    boards = desk / "ep01" / "boards"
    boards.mkdir(parents=True, exist_ok=True)
    (boards / "board-01.png").write_bytes(b"png")

    with pytest.raises(ec.CommandStopped, match="already has a drawn board"):
        pc.run_remove_prop(desk, prop="Wooden Sign", out=io.StringIO())
    assert all(m != "DELETE" for m, *_ in api.calls)
