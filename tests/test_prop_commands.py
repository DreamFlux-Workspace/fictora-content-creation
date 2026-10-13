"""Story objects from the desk: ``props``, ``redraw-prop`` and ``remove-prop`` (L-20261014-1).

The skill told operators to POST ``/props/{id}/redraw`` by hand "then `step`
draws it". ``step`` only draws at ``ready_cast_enrol`` and sent the plates
job's fixed key, so a hand-rewound desk got the finished job back: "Cast
drawn", nothing drawn. ``redraw-prop`` records the note and draws in one go
under a key of its own; ``step`` now changes its key when what is owed changed.
"""

from __future__ import annotations

import copy
import hashlib
import io
from pathlib import Path
from typing import Any

import pytest

from conftest import SHOWN_PRICES, set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation import prop_commands as pc
from creation.cli_produce import main as produce_main
from creation.harness.http_util import ConnectionDropped
from creation.ops.state import load_series
from creation.production_state import load_production
from fake_api import FakeApi, png_bytes

SIGN_ID = "prop_wooden_sign"
REDRAW = f"/v1/spines/sp1/props/{SIGN_ID}/redraw"
ENROL = "/v1/spines/sp1/cast/enrol"
DELETE = f"/v1/spines/sp1/props/{SIGN_ID}"
NOTE = "carved wood, the letters cut in, never painted"
WARNING = (
    "Redrawing adds $0.30 per object to this episode. Objects usually read fine on the board, "
    "so only redraw if the object is wrong for the story."
)


def _sign(**over: Any) -> dict[str, Any]:
    return {
        "prop_id": SIGN_ID,
        "name": "Wooden Sign",
        "object_kind": "sign",
        "scale_clause": "a shop sign",
        "creator_notes": [],
        **over,
    }


def _picture(**over: Any) -> dict[str, Any]:
    return {
        "relation_type": "prop",
        "relation_id": SIGN_ID,
        "kind": "still",
        "url": "https://r2.example/sign-v1.png",
        "stale": False,
        "review_state": "approved",
        **over,
    }


def _digest(note: str = NOTE) -> str:
    return hashlib.sha256(" ".join(note.split()).casefold().encode()).hexdigest()[:10]


def _serve(api: FakeApi, *, draws: bool = True) -> dict[str, int]:
    """A server: the redraw route notes and marks stale; enrol draws only what is owed."""

    api.spine_doc["props"] = [_sign()]
    api.spine_doc["media_assets"].append(_picture())
    count = {"enrol": 0}

    def redraw(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        card = api.spine_doc["props"][0]
        card["creator_notes"].append(
            {"note_id": f"pn{len(card['creator_notes'])}", "text": (body or {})["text"]}
        )
        for asset in api.spine_doc["media_assets"]:
            if asset.get("relation_type") == "prop":
                asset["stale"] = True
        return api.spine_doc

    def enrol(_m: str, _p: str, _body: dict[str, Any] | None) -> dict[str, Any]:
        count["enrol"] += 1
        job = f"job_enrol_{count['enrol']}"
        api.jobs[job] = {"job_id": job, "status": "completed"}
        if draws:
            for asset in api.spine_doc["media_assets"]:
                if asset.get("relation_type") in ("prop", "cast_card") and asset.get(
                    "stale"
                ):
                    asset["stale"] = False
                    asset["review_state"] = "pending"
                    asset["url"] = f"{asset['url']}?v{count['enrol']}"
        return {"job_id": job}

    api.routes[("POST", REDRAW)] = redraw
    api.routes[("POST", ENROL)] = enrol
    return count


def _enrol_keys(api: FakeApi) -> list[str | None]:
    return [k for m, p, _b, k in api.calls if m == "POST" and p == ENROL]


# --- props ----------------------------------------------------------------------------------


def test_props_lists_each_object_with_its_picture_state_and_file(
    desk: Path, api: FakeApi
) -> None:
    _serve(api)
    plates = desk / "ep01" / "plates"
    plates.mkdir(parents=True, exist_ok=True)
    (plates / "object-wooden-sign-ep01-v1.png").write_bytes(png_bytes())
    api.spine_doc["props"].append(_sign(prop_id="prop_ring", name="Ring"))
    out = io.StringIO()

    rows = pc.run_props(desk, out=out)

    assert [(r["name"], r["prop_id"], r["picture"]) for r in rows] == [
        ("Wooden Sign", SIGN_ID, "approved"),
        ("Ring", "prop_ring", "none"),
    ]
    assert rows[0]["file"].endswith("object-wooden-sign-ep01-v1.png")
    assert "nothing sent" in out.getvalue()
    assert [m for m, *_ in api.calls] == ["GET"], "read-only"


@pytest.mark.parametrize(
    ("asset", "state"),
    [
        (None, "none"),
        (_picture(review_state="pending"), "drawn"),
        (_picture(stale=True), "stale-redraw-asked"),
        (_picture(), "approved"),
    ],
)
def test_picture_states(asset: dict[str, Any] | None, state: str) -> None:
    from creation.story_objects import picture_state

    spine = {"props": [_sign()], "media_assets": [asset] if asset else []}
    assert picture_state(spine, SIGN_ID) == state


# --- redraw-prop ----------------------------------------------------------------------------


def test_redraw_prop_notes_draws_under_its_own_key_and_books_one_picture(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "wait_board")
    count = _serve(api)
    plates = desk / "ep01" / "plates"
    plates.mkdir(parents=True, exist_ok=True)
    old = plates / "object-wooden-sign-ep01-v1.png"
    old.write_bytes(png_bytes(200))
    out = io.StringIO()

    path = pc.run_redraw_prop(desk, prop="wooden sign", note=NOTE, out=out)

    text = out.getvalue()
    assert WARNING in text
    assert text.index(WARNING) < text.index("$0.30.")
    assert api.posted(REDRAW) == [{"spine_version": "v5", "text": NOTE}]
    prefix = load_production(desk).idempotency_prefix
    assert _enrol_keys(api) == [f"{prefix}-prop-{SIGN_ID}-{_digest()}-a1"]
    assert count["enrol"] == 1
    assert path is not None and path.name == "object-wooden-sign-ep01-v2.png"
    assert old.read_bytes() == png_bytes(200), "the older picture is never overwritten"
    assert [(e.unit, e.usd) for e in load_series(desk).spend_log] == [
        (f"object-redraw:{SIGN_ID}", 0.30)
    ]
    assert load_production(desk).phase == "wait_board", "the step is never moved"
    assert "approve --desk" in text and "--gate plates --again" in text


def test_redraw_prop_at_the_plates_gate_asks_for_the_one_yes(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "wait_plates")
    _serve(api)
    out = io.StringIO()

    pc.run_redraw_prop(desk, prop=SIGN_ID, note=NOTE, out=out)

    assert "one yes covers the cast and objects" in out.getvalue()
    assert load_production(desk).phase == "wait_plates"


def test_an_unknown_object_lists_the_objects_and_sends_nothing(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(api)

    code = produce_main(
        ["redraw-prop", "--desk", str(desk), "--prop", "umbrella", "--note", NOTE]
    )

    assert code == 2
    assert "Wooden Sign (prop_wooden_sign)" in capsys.readouterr().err
    assert [m for m, *_ in api.calls] == ["GET"]
    assert load_series(desk).spend_log == []


def test_a_note_already_recorded_and_still_owed_is_not_sent_again(
    desk: Path, api: FakeApi
) -> None:
    _serve(api)
    api.spine_doc["props"][0]["creator_notes"] = [{"note_id": "pn0", "text": NOTE}]
    api.spine_doc["media_assets"][-1]["stale"] = True

    path = pc.run_redraw_prop(desk, prop="Wooden Sign", note=NOTE, out=io.StringIO())

    assert path is not None
    assert api.posted(REDRAW) == []
    assert len(api.posted(ENROL)) == 1


def test_a_note_already_drawn_sends_nothing(desk: Path, api: FakeApi) -> None:
    _serve(api)
    api.spine_doc["props"][0]["creator_notes"] = [{"note_id": "pn0", "text": NOTE}]
    out = io.StringIO()

    assert pc.run_redraw_prop(desk, prop="Wooden Sign", note=NOTE, out=out) is None

    assert "Nothing was sent or paid" in out.getvalue()
    assert [m for m, *_ in api.calls] == ["GET"]


def test_more_than_one_picture_owed_is_priced_and_waits_for_the_yes(
    desk: Path, api: FakeApi
) -> None:
    _serve(api)
    for asset in api.spine_doc["media_assets"]:
        if asset.get("relation_id") == "cast_ren":
            asset["stale"] = True
    out = io.StringIO()

    assert pc.run_redraw_prop(desk, prop="Wooden Sign", note=NOTE, out=out) is None

    text = out.getvalue()
    assert WARNING in text
    assert "This draws 2 pictures, $0.60 in all" in text and "Ren" in text
    assert "--confirm-spend" in text
    assert api.posted(REDRAW) == [] and api.posted(ENROL) == []

    path = pc.run_redraw_prop(
        desk, prop="Wooden Sign", note=NOTE, confirm_spend=True, out=io.StringIO()
    )

    assert path is not None
    assert sorted(e.unit for e in load_series(desk).spend_log) == [
        f"object-redraw:{SIGN_ID}",
        "plate-owed:cast_ren",
    ]
    assert (desk / "ep01" / "plates" / "plate-ep01-2-v1.png").is_file()


def test_confirm_spend_without_a_shown_price_sends_nothing(
    desk: Path, api: FakeApi
) -> None:
    _serve(api)
    for asset in api.spine_doc["media_assets"]:
        if asset.get("relation_id") == "cast_ren":
            asset["stale"] = True
    out = io.StringIO()

    pc.run_redraw_prop(desk, prop="Wooden Sign", note=NOTE, confirm_spend=True, out=out)

    assert "this price was not shown before" in out.getvalue()
    assert api.posted(ENROL) == []


def test_fresh_on_a_server_without_it_is_refused_before_anything_is_sent(
    desk: Path, api: FakeApi
) -> None:
    _serve(api)
    api.routes[("GET", "/openapi.json")] = {
        "paths": {
            pc.REDRAW_ROUTE: {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/DramaCastNoteAddRequest"
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "DramaCastNoteAddRequest": {
                    "properties": {"spine_version": {}, "text": {}}
                }
            }
        },
    }

    with pytest.raises(ec.CommandStopped, match="from scratch"):
        pc.run_redraw_prop(
            desk, prop="Wooden Sign", note=NOTE, fresh=True, out=io.StringIO()
        )
    assert api.posted(REDRAW) == [] and api.posted(ENROL) == []


def test_fresh_is_its_own_job(desk: Path, api: FakeApi) -> None:
    _serve(api)

    pc.run_redraw_prop(
        desk, prop="Wooden Sign", note=NOTE, fresh=True, out=io.StringIO()
    )

    assert api.posted(REDRAW)[0]["fresh"] is True
    assert str(_enrol_keys(api)[0]).endswith(f"-prop-{SIGN_ID}-{_digest()}-fresh-a1")


def test_a_job_handed_back_without_a_new_picture_stops_and_books_nothing(
    desk: Path, api: FakeApi
) -> None:
    _serve(api, draws=False)

    with pytest.raises(ec.CommandStopped, match="no new picture"):
        pc.run_redraw_prop(desk, prop="Wooden Sign", note=NOTE, out=io.StringIO())

    assert load_series(desk).spend_log == []


# --- remove-prop ----------------------------------------------------------------------------


def _no_boards(api: FakeApi) -> None:
    api.spine_doc["media_assets"] = [
        a for a in api.spine_doc["media_assets"] if a.get("relation_type") != "episode"
    ]


def test_remove_prop_deletes_with_a_key_and_moves_the_pictures_aside(
    desk: Path, api: FakeApi
) -> None:
    _serve(api)
    _no_boards(api)
    api.routes[("DELETE", DELETE)] = {"spine_id": "sp1"}
    plates = desk / "ep01" / "plates"
    plates.mkdir(parents=True, exist_ok=True)
    (plates / "object-wooden-sign-ep01-v1.png").write_bytes(png_bytes())
    out = io.StringIO()

    moved = pc.run_remove_prop(desk, prop="wooden sign", out=out)

    prefix = load_production(desk).idempotency_prefix
    assert [(m, p, k) for m, p, _b, k in api.calls if m == "DELETE"] == [
        ("DELETE", DELETE, f"{prefix}-prop-remove-{SIGN_ID}")
    ]
    assert [p.name for p in moved] == ["object-wooden-sign-ep01-v1.png"]
    assert moved[0].parent == desk / "objects" / "removed"
    assert not (plates / "object-wooden-sign-ep01-v1.png").exists()
    assert "Free" in out.getvalue()
    assert load_series(desk).spend_log == []


def test_remove_prop_is_refused_on_the_desk_once_the_episode_has_a_board(
    desk: Path, api: FakeApi
) -> None:
    _serve(api)  # the fixture's spine has episode 1's board drawn

    with pytest.raises(ec.CommandStopped, match="already has a drawn board"):
        pc.run_remove_prop(desk, prop="Wooden Sign", out=io.StringIO())
    assert [m for m, *_ in api.calls] == ["GET"]


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        ((409, {"error": {"code": "prop_on_board"}}), "already on a drawn board"),
        ((404, {"error": {"code": "prop_not_found"}}), "no longer on the story"),
        ((404, {"detail": "Not Found"}), "needs the server update"),
        ((405, {"detail": "Method Not Allowed"}), "needs the server update"),
    ],
)
def test_remove_prop_says_the_servers_refusal_plainly(
    desk: Path, api: FakeApi, answer: tuple[int, Any], says: str
) -> None:
    _serve(api)
    _no_boards(api)
    api.routes[("DELETE", DELETE)] = answer
    plates = desk / "ep01" / "plates"
    plates.mkdir(parents=True, exist_ok=True)
    kept = plates / "object-wooden-sign-ep01-v1.png"
    kept.write_bytes(png_bytes())

    with pytest.raises(ec.CommandStopped, match=says):
        pc.run_remove_prop(desk, prop="Wooden Sign", out=io.StringIO())
    assert kept.is_file(), "nothing moved when the server did not remove it"


# --- step: the plates key never replays a finished job (the regression) ----------------------


def _plates_step(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "ready_cast_enrol", drawing_estimates=dict(SHOWN_PRICES))


def test_a_first_plates_step_keeps_its_key(desk: Path, api: FakeApi) -> None:
    count = _serve(api)
    api.spine_doc["props"] = [_sign()]
    plates = [
        a
        for a in api.spine_doc["media_assets"]
        if a.get("relation_type") == "cast_card"
    ]
    api.spine_doc["media_assets"] = [
        a
        for a in api.spine_doc["media_assets"]
        if a.get("relation_type") not in ("cast_card", "prop")
    ]

    def enrol(_m: str, _p: str, _body: dict[str, Any] | None) -> dict[str, Any]:
        count["enrol"] += 1
        api.spine_doc["media_assets"].extend(copy.deepcopy(plates))
        api.spine_doc["media_assets"].append(_picture(review_state="pending"))
        api.jobs["job_first"] = {"status": "completed"}
        return {"job_id": "job_first"}

    api.routes[("POST", ENROL)] = enrol
    _plates_step(desk, api)

    result = orchestrate.run_step(desk, confirm_spend=True)

    prefix = load_production(desk).idempotency_prefix
    assert _enrol_keys(api) == [f"{prefix}-step-ep1-cast-enrol"]
    assert "Cast drawn" in result.message


def test_a_rewound_plates_step_never_replays_the_finished_job(
    desk: Path, api: FakeApi
) -> None:
    """L-20261014-1: plates drawn, an object redraw asked by hand, desk rewound to the plates step."""

    _serve(api)
    api.spine_doc["media_assets"][-1]["stale"] = True  # the hand-POSTed redraw
    _plates_step(desk, api)

    orchestrate.run_step(desk, confirm_spend=True)

    prefix = load_production(desk).idempotency_prefix
    (key,) = _enrol_keys(api)
    assert key != f"{prefix}-step-ep1-cast-enrol", "the old, finished job's key"
    assert str(key).startswith(f"{prefix}-step-ep1-cast-enrol-o")


def test_a_second_plates_job_of_the_episode_gets_a_new_key(
    desk: Path, api: FakeApi
) -> None:
    _serve(api)
    api.spine_doc["media_assets"][-1]["stale"] = True
    _plates_step(desk, api)
    orchestrate.run_step(desk, confirm_spend=True)
    api.spine_doc["media_assets"][-1]["stale"] = True
    _plates_step(desk, api)

    orchestrate.run_step(desk, confirm_spend=True)

    first, second = _enrol_keys(api)
    assert first != second


def test_an_in_flight_plates_job_is_picked_up_under_the_same_key(
    desk: Path, api: FakeApi
) -> None:
    _serve(api)
    api.spine_doc["media_assets"][-1]["stale"] = True
    api.routes[("POST", ENROL)] = ConnectionDropped("connection dropped")
    _plates_step(desk, api)
    with pytest.raises(SystemExit):
        orchestrate.run_step(desk, confirm_spend=True)
    # Part of the job landed before the re-run: what is owed now differs.
    for asset in api.spine_doc["media_assets"]:
        if asset.get("relation_id") == "cast_ren":
            asset["stale"] = True
    api.routes[("POST", ENROL)] = {"job_id": "job_again"}
    api.jobs["job_again"] = {"status": "completed"}

    orchestrate.run_step(desk, confirm_spend=True)

    first, second = _enrol_keys(api)
    assert first == second
