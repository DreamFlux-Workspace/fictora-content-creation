"""`look-options`: the art style is picked before the plates, from looks drawn side by side (GRAFT, 15 Oct 2026).

The server offers a new story two or three looks at one picture each and holds the plates until
one is picked; the kit asks, prices, draws, saves and picks through it, and keeps nothing itself.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from creation import orchestrate
from creation.cli_produce import main as produce_main
from creation.look_options_commands import look_options_refusal, run_look_options
from creation.ops.state import load_series
from creation.production_state import load_production
from conftest import SHOWN_PRICES, set_phase
from fake_api import FakeApi

BASE = "/v1/spines/sp1/look-options"
LABELS = ["The style it has now", "80s hand-painted cel anime", "Shounen action anime"]


def _options(*, drawn: bool) -> list[dict[str, Any]]:
    return [
        {
            "option_id": f"look_{n}",
            "label": label,
            **(
                {"image_url": f"https://cdn.example/look-option/{n}.png"}
                if drawn
                else {}
            ),
        }
        for n, label in enumerate(LABELS, start=1)
    ]


def _server(api: FakeApi, *, offered: bool = True) -> dict[str, Any]:
    """The look-options routes, answering like the server; returns its state."""

    state: dict[str, Any] = {
        "offered": offered,
        "options": [],
        "picked": None,
        "bodies": [],
    }

    def view(*_: Any) -> dict[str, Any]:
        return {
            "offered": state["offered"] and state["picked"] is None,
            "reason": "Pick the art style before the characters are drawn."
            if state["offered"]
            else "A continuing show keeps the look its episodes went out with.",
            "options": state["options"],
            "picked_option_id": state["picked"],
            "cost_usd": 0.0,
        }

    def post(_m: str, _p: str, body: dict[str, Any] | None) -> Any:
        state["bodies"].append(body or {})
        if (body or {}).get("preview"):
            return 200, {**view(), "options": _options(drawn=False), "cost_usd": 0.9}
        state["options"] = _options(drawn=True)
        return 200, {**view(), "cost_usd": 0.9, "cached": False}

    def pick(_m: str, path: str, body: dict[str, Any] | None) -> Any:
        option_id = path.split("/look-options/", 1)[1].removesuffix("/pick")
        state["picked"] = option_id
        state["offered"] = False
        api.spine_doc["look_register_url"] = next(
            o["image_url"] for o in state["options"] if o["option_id"] == option_id
        )
        api.spine_doc["look_option_picked_id"] = option_id
        return 200, dict(api.spine_doc)

    api.routes[("GET", BASE)] = view
    api.routes[("POST", BASE)] = post
    for n in (1, 2, 3):
        api.routes[("POST", f"{BASE}/look_{n}/pick")] = pick
    return state


def test_without_flags_it_prints_the_offer_and_price_and_draws_nothing(
    desk: Path, api: FakeApi
) -> None:
    server = _server(api)
    out = io.StringIO()

    run_look_options(desk, out=out)

    assert server["bodies"] == [{"count": 3, "descriptions": [], "preview": True}]
    text = out.getvalue()
    assert "about $0.90" in text and "Nothing drawn yet" in text
    for label in LABELS:
        assert label in text
    assert "--confirm-spend" in text
    assert not list((desk / "shared").rglob("look-option-*"))
    assert load_series(desk).spend_log == []


def test_confirm_spend_draws_saves_each_look_and_books_what_the_server_booked(
    desk: Path, api: FakeApi
) -> None:
    server = _server(api)
    out = io.StringIO()

    run_look_options(desk, confirm_spend=True, out=out)

    assert server["bodies"] == [{"count": 3, "descriptions": []}]
    (_, _, _, key) = next(
        call for call in api.calls if call[0] == "POST" and call[1] == BASE
    )
    assert key and "look-options" in key
    saved = sorted(p.name for p in (desk / "shared" / "look").iterdir())
    assert saved == [
        "look-option-1-v1.png",
        "look-option-2-v1.png",
        "look-option-3-v1.png",
    ]
    assert [round(entry.usd, 2) for entry in load_series(desk).spend_log] == [0.9]
    assert "--pick N" in out.getvalue()


def test_the_humans_own_looks_are_sent_as_written(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    server = _server(api)
    look = tmp_path / "cel.txt"
    look.write_text("80s hand-painted cel, like Last Call.", encoding="utf-8")

    run_look_options(
        desk,
        confirm_spend=True,
        descriptions=[f"@{look}", "clean shounen inks"],
        out=io.StringIO(),
    )

    assert server["bodies"][0]["descriptions"] == [
        "80s hand-painted cel, like Last Call.",
        "clean shounen inks",
    ]


def test_pick_pins_the_look_on_the_server_and_records_the_look_yes(
    desk: Path, api: FakeApi
) -> None:
    _server(api)
    run_look_options(desk, confirm_spend=True, out=io.StringIO())
    out = io.StringIO()

    run_look_options(desk, pick=2, out=out)

    assert api.posted(f"{BASE}/look_2/pick")
    look = load_series(desk).look
    assert look.status == "approved" and look.path == "shared/look/look-option-2-v1.png"
    assert "Look picked: 80s hand-painted cel anime" in out.getvalue()


def test_a_pick_that_names_no_drawn_look_sends_nothing(
    desk: Path, api: FakeApi
) -> None:
    from creation.episode_commands import CommandStopped

    _server(api)
    with pytest.raises(CommandStopped) as stopped:
        run_look_options(desk, pick=1, out=io.StringIO())
    assert "names no drawn look" in str(stopped.value)
    assert not [call for call in api.calls if call[0] == "POST"]


def test_step_stops_before_episode_1_plates_while_the_server_offers_a_pick(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _server(api)
    set_phase(desk, "ready_cast_enrol", drawing_estimates=SHOWN_PRICES)

    assert produce_main(["step", "--desk", str(desk)]) == 2

    err = capsys.readouterr().err
    assert (
        "pick the art style before the plates" in err and "look-options --desk" in err
    )
    assert not [call for call in api.calls if call[0] == "POST"]
    assert load_production(desk).phase == "ready_cast_enrol"


def test_step_stops_while_the_drawn_looks_wait_for_a_pick(
    desk: Path, api: FakeApi
) -> None:
    _server(api)
    run_look_options(desk, confirm_spend=True, out=io.StringIO())
    run = orchestrate._open_run(desk, load_production(desk))

    refusal = look_options_refusal(desk, run, "sp1")

    assert refusal and "none is picked" in refusal


def test_step_draws_plates_once_a_look_is_picked_or_the_human_keeps_the_preset(
    desk: Path, api: FakeApi
) -> None:
    server = _server(api)
    set_phase(desk, "ready_cast_enrol", drawing_estimates=SHOWN_PRICES)
    api.routes[("POST", "/v1/spines/sp1/cast/enrol")] = {"job_id": "job_cast"}
    api.jobs["job_cast"] = {"status": "completed"}

    kept = orchestrate.run_step(desk, confirm_spend=True, keep_preset_look=True)
    assert kept.phase == "wait_plates"

    set_phase(desk, "ready_cast_enrol", drawing_estimates=SHOWN_PRICES)
    run_look_options(desk, confirm_spend=True, out=io.StringIO())
    run_look_options(desk, pick=1, out=io.StringIO())
    assert server["picked"] == "look_1"
    picked = orchestrate.run_step(desk, confirm_spend=True)
    assert picked.phase == "wait_plates"


def test_a_continuing_show_or_an_older_server_is_never_held(
    desk: Path, api: FakeApi
) -> None:
    run = orchestrate._open_run(desk, load_production(desk))
    assert look_options_refusal(desk, run, "sp1") is None  # no route: an older server
    _server(api, offered=False)
    assert look_options_refusal(desk, run, "sp1") is None
