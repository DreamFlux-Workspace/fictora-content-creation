"""``outfit``: a character's outfits on the card and on the shots (The Gallery Heiress, 13-15 Oct 2026).

Seo-yeon was to wear a black evening gown in episodes 5-6. A look note and the gown written into
every shot were ignored; the server's own outfit fields (``wardrobe_variants`` on the card,
``wardrobe_variant`` per shot) had no kit command, so it was hand-built JSON, one edit per shot.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from cast_story import _after_gate, _before_gate, _patches, _story, _writes
from creation import episode_commands as ec
from creation import outfit_commands as oc
from creation.cli_produce import main as produce_main
from creation.picture_checks import WRONG_OUTFIT_FIX, picture_check_lines
from fake_api import FakeApi

GOWN = "floor-length black silk evening gown, off-shoulder"


def _hana(api: FakeApi) -> dict[str, Any]:
    return next(c for c in api.spine_doc["cast"] if c["cast_id"] == "cast_hana")


def _with_gown(spine: dict[str, Any]) -> dict[str, Any]:
    hana = next(c for c in spine["cast"] if c["cast_id"] == "cast_hana")
    hana["visual_brief"]["wardrobe_variants"] = [
        {"label": "black gown", "instruction": GOWN}
    ]
    return spine


def _worn(api: FakeApi, episode_id: str) -> list[Any]:
    return [
        entry.get("wardrobe_variant")
        for frame in api.spine_doc["frames"]
        if frame["episode_id"] == episode_id
        for entry in frame["visual_brief"]["subject_blocking"]
        if entry["cast_id"] == "cast_hana"
    ]


def test_add_sends_the_whole_brief_with_the_outfit_then_prices_the_sheet(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _story(approved=False))
    out = io.StringIO()

    oc.run_outfit_add(desk, cast="Hana", label="black gown", look=GOWN, out=out)

    (patch,) = _patches(api)
    sent = patch["cast"][0]
    assert sent["cast_id"] == "cast_hana"
    # The server replaces the brief whole: every anchor goes with the new outfit.
    assert sent["visual_brief"]["wardrobe_anchors"] == [
        "green apron over a white shirt"
    ]
    assert sent["visual_brief"]["wardrobe_variants"] == [
        {"label": "black gown", "instruction": GOWN}
    ]
    assert _hana(api)["visual_brief"]["wardrobe_variants"][0]["label"] == "black gown"
    text = out.getvalue()
    assert "Applied" in text
    assert "about $0.30. Nothing drawn yet." in text
    assert '--add "black gown" --confirm-spend' in text
    assert not [p for m, p in _writes(api) if "/regenerate" in p]


def test_confirm_spend_without_a_shown_price_draws_nothing(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _before_gate(api, _with_gown(_story(approved=False)))
    drawn: list[str] = []
    monkeypatch.setattr(
        ec, "run_redraw_plate_with_note", lambda *a, **k: drawn.append(k["cast"])
    )
    out = io.StringIO()

    oc.run_outfit_add(
        desk, cast="Hana", label="Black Gown", look=None, confirm_spend=True, out=out
    )

    assert drawn == []
    assert "No price shown for this redraw yet." in out.getvalue()
    # The price is now shown, so the next confirm draws, once.
    oc.run_outfit_add(
        desk,
        cast="Hana",
        label="black gown",
        look=None,
        confirm_spend=True,
        out=io.StringIO(),
    )
    assert drawn == ["cast_hana"]
    assert _patches(api) == []


def test_the_same_outfit_again_sends_nothing_and_new_words_reword_it(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _with_gown(_story(approved=False)))
    out = io.StringIO()

    oc.run_outfit_add(desk, cast="Hana", label="BLACK GOWN", look=GOWN, out=out)
    assert _patches(api) == []
    assert 'already has "black gown" with these words' in out.getvalue()

    oc.run_outfit_add(
        desk,
        cast="Hana",
        label="black gown",
        look="short black cocktail dress",
        out=io.StringIO(),
    )
    (patch,) = _patches(api)
    assert patch["cast"][0]["visual_brief"]["wardrobe_variants"] == [
        {"label": "black gown", "instruction": "short black cocktail dress"}
    ]


def test_a_fifth_outfit_and_base_as_a_name_stop_before_sending(
    desk: Path, api: FakeApi
) -> None:
    spine = _story(approved=False)
    spine["cast"][0]["visual_brief"]["wardrobe_variants"] = [
        {"label": f"outfit {n}", "instruction": f"look {n}"} for n in range(1, 5)
    ]
    _before_gate(api, spine)

    with pytest.raises(ec.CommandStopped, match="already has 4 outfits"):
        oc.run_outfit_add(
            desk, cast="Hana", label="black gown", look=GOWN, out=io.StringIO()
        )
    with pytest.raises(ec.CommandStopped, match="card's own outfit"):
        oc.run_outfit_add(desk, cast="Hana", label="Base", look=GOWN, out=io.StringIO())
    assert _writes(api) == []


def test_add_preview_after_the_gate_executes_nothing(desk: Path, api: FakeApi) -> None:
    edits = _after_gate(api, _story(approved=True))
    out = io.StringIO()

    oc.run_outfit_add(
        desk, cast="Hana", label="black gown", look=GOWN, preview_only=True, out=out
    )

    assert edits and edits[0]["target_type"] == "cast_card"
    assert not [p for m, p in _writes(api) if p.endswith("/cascade/execute")]
    assert "Not applied" in out.getvalue()
    assert "wardrobe_variants" not in _hana(api)["visual_brief"]


def test_wear_tags_every_shot_of_two_episodes_and_names_the_stale_boards(
    desk: Path, api: FakeApi
) -> None:
    edits = _after_gate(api, _with_gown(_story(approved=True)))
    out = io.StringIO()

    oc.run_outfit_wear(desk, cast="Hana", label="black gown", episodes="1-2", out=out)

    # One episode edit each, every shot of hers in it.
    assert [e["target_id"] for e in edits] == ["episode_01", "ep_02"]
    assert all(e["target_type"] == "episode" for e in edits)
    assert _worn(api, "episode_01") == ["black gown", "black gown"]
    assert _worn(api, "ep_02") == ["black gown", "black gown"]
    text = out.getvalue()
    assert 'ep01: 2 shot(s) now "black gown".' in text
    assert "--episode 2 --take t1" in text
    assert "redraw-board" in text and "$0.30" in text
    assert oc.load_plan(desk)["cast_hana"]["episodes"] == {
        "1": "black gown",
        "2": "black gown",
    }
    assert oc.outfit_plan_lines(desk, api.spine_doc, episode=1) == []

    # Again: every shot is already right, nothing is sent.
    before = len(edits)
    oc.run_outfit_wear(
        desk, cast="Hana", label="black gown", episodes="1-2", out=io.StringIO()
    )
    assert len(edits) == before


def test_wear_before_the_gate_patches_and_base_clears_the_tag(
    desk: Path, api: FakeApi
) -> None:
    spine = _with_gown(_story(approved=False))
    for frame in spine["frames"]:
        for entry in frame["visual_brief"]["subject_blocking"]:
            entry["wardrobe_variant"] = "black gown"
    _before_gate(api, spine)

    oc.run_outfit_wear(desk, cast="Hana", label="base", episodes="2", out=io.StringIO())

    (patch,) = _patches(api)
    assert {f["frame_id"] for f in patch["frames"]} == {
        "frame_ep_02_01",
        "frame_ep_02_02",
    }
    assert _worn(api, "ep_02") == [None, None]
    assert _worn(api, "episode_01") == ["black gown", "black gown"]


def test_wear_preview_before_the_gate_prints_and_sends_nothing(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _with_gown(_story(approved=False)))
    out = io.StringIO()

    oc.run_outfit_wear(
        desk, cast="Hana", label="black gown", episodes="1", preview_only=True, out=out
    )

    assert _writes(api) == []
    assert '-> "black gown"' in out.getvalue()
    assert oc.load_plan(desk) == {}


def test_an_unknown_outfit_stops_and_names_add(desk: Path, api: FakeApi) -> None:
    _before_gate(api, _story(approved=False))

    with pytest.raises(ec.CommandStopped, match='--add "red dress"'):
        oc.run_outfit_wear(
            desk, cast="Hana", label="red dress", episodes="1", out=io.StringIO()
        )
    assert _writes(api) == []


def test_an_episode_not_written_yet_keeps_the_plan_and_the_board_price_warns(
    desk: Path, api: FakeApi
) -> None:
    _after_gate(api, _with_gown(_story(approved=True)))
    out = io.StringIO()

    oc.run_outfit_wear(desk, cast="Hana", label="black gown", episodes="2-3", out=out)

    assert "ep03: shots not written yet" in out.getvalue()
    assert oc.load_plan(desk)["cast_hana"]["episodes"]["3"] == "black gown"
    # Episode 3's shots arrive in her base outfit: the free check names them and the fix.
    for frame in [f for f in api.spine_doc["frames"] if f["episode_id"] == "ep_02"]:
        api.spine_doc["frames"].append(
            {
                **json.loads(json.dumps(frame)),
                "frame_id": frame["frame_id"] + "_3",
                "episode_id": "ep_03",
            }
        )
    api.spine_doc["episode_summaries"].append(
        {"episode_id": "ep_03", "ordinal": 3, "title": "Ep 3"}
    )
    for frame in api.spine_doc["frames"]:
        if frame["episode_id"] == "ep_03":
            frame["visual_brief"]["subject_blocking"][0]["wardrobe_variant"] = None

    (line,) = oc.outfit_plan_lines(desk, api.spine_doc, episode=3)
    assert line.startswith(
        '!! outfit: ep03: Hana should wear "black gown" (the plan) but 2 shot(s) do not'
    )
    assert '--wear "black gown" --episodes 3' in line


def test_remove_is_refused_while_a_shot_wears_it(desk: Path, api: FakeApi) -> None:
    spine = _with_gown(_story(approved=False))
    spine["frames"][0]["visual_brief"]["subject_blocking"][0]["wardrobe_variant"] = (
        "black gown"
    )
    _before_gate(api, spine)

    with pytest.raises(ec.CommandStopped, match="still wears"):
        oc.run_outfit_remove(desk, cast="Hana", label="black gown", out=io.StringIO())
    assert _writes(api) == []

    spine["frames"][0]["visual_brief"]["subject_blocking"][0]["wardrobe_variant"] = None
    oc.run_outfit_remove(desk, cast="Hana", label="black gown", out=io.StringIO())
    assert _hana(api)["visual_brief"]["wardrobe_variants"] == []


def test_list_shows_outfits_and_what_each_episode_wears(
    desk: Path, api: FakeApi, capsys: Any
) -> None:
    spine = _with_gown(_story(approved=False))
    spine["frames"][0]["visual_brief"]["subject_blocking"][0]["wardrobe_variant"] = (
        "black gown"
    )
    _before_gate(api, spine)

    assert produce_main(["outfit", "--desk", str(desk), "--cast", "Hana"]) == 0

    text = capsys.readouterr().out
    assert "base: green apron over a white shirt" in text
    assert f'"black gown": {GOWN}' in text
    assert "ep01: 1 shot(s) 'black gown', 1 shot(s) base" in text
    assert _writes(api) == []


@pytest.mark.parametrize(
    ("raw", "numbers"),
    [("5", [5]), ("5-6", [5, 6]), ("5,7", [5, 7]), (" 2-3, 6 ", [2, 3, 6])],
)
def test_episodes_parse(raw: str, numbers: list[int]) -> None:
    assert oc.parse_episodes(raw) == numbers


@pytest.mark.parametrize("raw", ["", "five", "6-5", "0", "5-"])
def test_bad_episodes_stop(raw: str) -> None:
    with pytest.raises(ec.CommandStopped):
        oc.parse_episodes(raw)


def test_a_wrong_outfit_finding_prints_readably_with_the_fix() -> None:
    lines = picture_check_lines(
        [
            {
                "kind": "wrong_outfit",
                "message": 'panel 3: Seo-yeon wears an ivory blouse, not "black gown"',
            }
        ],
        what="t1 board",
    )

    assert (
        lines[0]
        == '!! picture check: t1 board: panel 3: Seo-yeon wears an ivory blouse, not "black gown"'
    )
    assert lines[1] == WRONG_OUTFIT_FIX


def test_the_board_price_warns_about_shots_off_the_outfit_plan(
    desk: Path, api: FakeApi
) -> None:
    from conftest import set_phase

    from creation import orchestrate

    api.spine_doc = _with_gown(_story(approved=True))
    api.routes[("POST", "/v1/spines/sp1/boards/enrol")] = {"job_id": "job_boards"}
    api.jobs["job_boards"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/1/boards/exposure")] = {"boards": []}
    (desk / "shared").mkdir(exist_ok=True)
    (desk / "shared" / "outfits.json").write_text(
        json.dumps({"cast_hana": {"name": "Hana", "episodes": {"1": "black gown"}}}),
        encoding="utf-8",
    )
    set_phase(desk, "ready_boards_enrol")

    result = orchestrate.run_step(desk)

    assert "storyboard(s), one a take" in result.message
    assert (
        '!! outfit: ep01: Hana should wear "black gown" (the plan) but 2 shot(s) do not'
        in result.message
    )
    assert not api.posted("/v1/spines/sp1/boards/enrol")
