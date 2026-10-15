"""``outfit``: one call to the server's outfit plan (The Gallery Heiress, 13-15 Oct 2026).

Seo-yeon was to wear a black evening gown in episodes 5-6. The first ``outfit`` edited the card and
every shot itself and kept the plan in a desk file the server never saw; the server now owns the plan
(``POST``/``GET /v1/spines/{id}/outfits``) and the kit only calls it.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from cast_story import _before_gate, _story, _writes
from creation import episode_commands as ec
from creation import outfit_commands as oc
from creation.cli_produce import main as produce_main
from creation.picture_checks import WRONG_OUTFIT_FIX, picture_check_lines
from fake_api import FakeApi, serve_outfits

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


def _serve(api: FakeApi, spine: dict[str, Any]) -> None:
    _before_gate(api, spine)
    serve_outfits(api)


def _posts(api: FakeApi) -> list[dict[str, Any]]:
    return [
        body
        for method, path, body, _ in api.calls
        if method == "POST" and path.endswith("/outfits") and body
    ]


def _drawn(spine: dict[str, Any]) -> dict[str, Any]:
    """Hana has a drawn sheet (a cast-card plate)."""

    spine.setdefault("media_assets", []).append(
        {
            "asset_id": "plate_hana",
            "relation_type": "cast_card",
            "relation_id": "cast_hana",
            "url": "https://x/p.png",
        }
    )
    return spine


def test_add_is_one_request_then_the_sheet_is_priced_not_drawn(
    desk: Path, api: FakeApi
) -> None:
    _serve(api, _drawn(_story(approved=True)))
    out = io.StringIO()

    oc.run_outfit_add(desk, cast="Hana", label="black gown", look=GOWN, out=out)

    (sent,) = _posts(api)
    assert sent["cast_id"] == "cast_hana"
    assert sent["add"] == {"label": "black gown", "instruction": GOWN}
    assert "wear" not in sent and sent["preview"] is False
    assert sent["spine_version"] == "v5"
    assert _hana(api)["visual_brief"]["wardrobe_variants"] == [
        {"label": "black gown", "instruction": GOWN}
    ]
    text = out.getvalue()
    assert "Applied" in text
    assert "about $0.30. Nothing drawn yet." in text
    assert '--add "black gown" --confirm-spend' in text
    # Nothing but the outfit request was written: no card PATCH, no cascade, no redraw.
    assert _writes(api) == [("POST", "/v1/spines/sp1/outfits")]


def test_confirm_spend_draws_only_after_the_price_was_shown(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(api, _drawn(_with_gown(_story(approved=True))))
    drawn: list[str] = []
    monkeypatch.setattr(
        ec, "run_redraw_plate_with_note", lambda *a, **k: drawn.append(k["cast"])
    )
    out = io.StringIO()

    # A yes with no price shown stops (exit 2), so `a && b` never runs b over a silent no-op
    # (L-20261014-28).
    with pytest.raises(ec.CommandStopped, match="did NOT draw"):
        oc.run_outfit_add(
            desk,
            cast="Hana",
            label="Black Gown",
            look=None,
            confirm_spend=True,
            out=out,
        )

    assert drawn == []
    assert "No price shown for this redraw yet." in out.getvalue()
    oc.run_outfit_add(
        desk,
        cast="Hana",
        label="black gown",
        look=None,
        confirm_spend=True,
        out=io.StringIO(),
    )
    assert drawn == ["cast_hana"]
    assert _posts(api) == []


def test_add_and_wear_go_in_one_request_and_name_the_stale_boards(
    desk: Path, api: FakeApi
) -> None:
    _serve(api, _story(approved=True))
    out = io.StringIO()

    oc.run_outfit_change(
        desk,
        cast="Hana",
        add="black gown",
        look=GOWN,
        wear="black gown",
        episodes="1-3",
        out=out,
    )

    (sent,) = _posts(api)
    assert sent["add"]["label"] == "black gown"
    assert sent["wear"] == {
        "wardrobe_variant": "black gown",
        "episode_ordinals": [1, 2, 3],
    }
    assert _worn(api, "episode_01") == ["black gown", "black gown"]
    assert _worn(api, "ep_02") == ["black gown", "black gown"]
    text = out.getvalue()
    assert 'ep01: 2 shot(s) now "black gown".' in text
    assert (
        'ep03: shots not written yet; the writer will put "black gown" on when they are written.'
        in text
    )
    assert (
        "redraw-board" in text and "--episode 2 --take t1" in text and "$0.30" in text
    )
    assert "Applied" in text
    # The plan lives on the server, not on the desk.
    assert api.spine_doc["outfit_plan"] == [
        {
            "cast_id": "cast_hana",
            "wardrobe_variant": "black gown",
            "episode_ordinals": [1, 2, 3],
        }
    ]
    assert not (desk / "shared" / "outfits.json").exists()


def test_wear_base_sends_null_and_the_same_request_again_changes_nothing(
    desk: Path, api: FakeApi
) -> None:
    spine = _with_gown(_story(approved=False))
    for frame in spine["frames"]:
        for entry in frame["visual_brief"]["subject_blocking"]:
            entry["wardrobe_variant"] = "black gown"
    _serve(api, spine)

    oc.run_outfit_wear(desk, cast="Hana", label="base", episodes="2", out=io.StringIO())

    assert _posts(api)[0]["wear"] == {"wardrobe_variant": None, "episode_ordinals": [2]}
    assert _worn(api, "ep_02") == [None, None]
    assert _worn(api, "episode_01") == ["black gown", "black gown"]
    out = io.StringIO()
    oc.run_outfit_wear(desk, cast="Hana", label="base", episodes="2", out=out)
    assert "Nothing changed" in out.getvalue()


def test_preview_sends_preview_and_stores_nothing(desk: Path, api: FakeApi) -> None:
    _serve(api, _with_gown(_story(approved=True)))
    out = io.StringIO()

    oc.run_outfit_wear(
        desk, cast="Hana", label="black gown", episodes="1", preview_only=True, out=out
    )

    assert _posts(api)[0]["preview"] is True
    assert _worn(api, "episode_01") == [None, None]
    assert '-> "black gown"' in out.getvalue()
    assert "Not applied" in out.getvalue()


@pytest.mark.parametrize(
    ("spine_edit", "call", "said"),
    [
        (lambda s: None, ("wear", "red dress"), '--add "red dress"'),
        (
            lambda s: s["cast"][0]["visual_brief"].update(
                wardrobe_variants=[
                    {"label": f"outfit {n}", "instruction": f"look {n}"}
                    for n in range(1, 5)
                ]
            ),
            ("add", "black gown"),
            "already has 4 outfits",
        ),
        (
            lambda s: s["cast"][0].pop("visual_brief"),
            ("wear", "black gown"),
            "heard only",
        ),
    ],
)
def test_server_refusals_read_as_plain_stops(
    desk: Path, api: FakeApi, spine_edit: Any, call: Any, said: str
) -> None:
    spine = _story(approved=False)
    spine_edit(spine)
    _serve(api, spine)
    kind, label = call

    with pytest.raises(ec.CommandStopped, match=said):
        if kind == "wear":
            oc.run_outfit_wear(
                desk, cast="Hana", label=label, episodes="1", out=io.StringIO()
            )
        else:
            oc.run_outfit_add(
                desk, cast="Hana", label=label, look=GOWN, out=io.StringIO()
            )
    assert _worn(api, "episode_01") == [None, None]


def test_remove_in_use_is_refused_by_the_server(desk: Path, api: FakeApi) -> None:
    spine = _with_gown(_story(approved=False))
    spine["frames"][0]["visual_brief"]["subject_blocking"][0]["wardrobe_variant"] = (
        "black gown"
    )
    _serve(api, spine)

    with pytest.raises(ec.CommandStopped, match="still wears"):
        oc.run_outfit_remove(desk, cast="Hana", label="black gown", out=io.StringIO())
    assert _posts(api) == [
        {
            "spine_version": "v5",
            "cast_id": "cast_hana",
            "remove": "black gown",
            "preview": False,
        }
    ]

    spine["frames"][0]["visual_brief"]["subject_blocking"][0]["wardrobe_variant"] = None
    oc.run_outfit_remove(desk, cast="Hana", label="black gown", out=io.StringIO())
    assert _hana(api)["visual_brief"]["wardrobe_variants"] == []


def test_a_conflict_retries_once_with_the_latest_version(
    desk: Path, api: FakeApi
) -> None:
    _serve(api, _with_gown(_story(approved=True)))
    serve = api.routes[("POST", "/v1/spines/sp1/outfits")]
    seen: list[str] = []

    def bumped(m: str, p: str, body: dict[str, Any] | None) -> Any:
        seen.append((body or {})["spine_version"])
        if len(seen) == 1:
            api.spine_doc["spine_version"] = "v6"  # someone edited the story meanwhile
        return serve(m, p, body)

    api.routes[("POST", "/v1/spines/sp1/outfits")] = bumped

    oc.run_outfit_wear(
        desk, cast="Hana", label="black gown", episodes="1", out=io.StringIO()
    )

    assert seen == ["v5", "v6"]
    assert _worn(api, "episode_01") == ["black gown", "black gown"]


def test_an_older_server_stops_and_edits_nothing(desk: Path, api: FakeApi) -> None:
    _before_gate(api, _with_gown(_story(approved=True)))
    api.routes[("POST", "/v1/spines/sp1/outfits")] = (404, {"detail": "Not Found"})

    with pytest.raises(ec.CommandStopped, match="older than the outfit plan"):
        oc.run_outfit_wear(
            desk, cast="Hana", label="black gown", episodes="1", out=io.StringIO()
        )
    assert _writes(api) == [("POST", "/v1/spines/sp1/outfits")]
    assert _worn(api, "episode_01") == [None, None]


def test_list_reads_the_server_view(desk: Path, api: FakeApi, capsys: Any) -> None:
    spine = _with_gown(_story(approved=False))
    spine["frames"][0]["visual_brief"]["subject_blocking"][0]["wardrobe_variant"] = (
        "black gown"
    )
    spine["outfit_plan"] = [
        {
            "cast_id": "cast_hana",
            "wardrobe_variant": "black gown",
            "episode_ordinals": [1, 3],
        }
    ]
    _serve(api, spine)

    assert produce_main(["outfit", "--desk", str(desk), "--cast", "Hana"]) == 0

    text = capsys.readouterr().out
    assert "base: green apron over a white shirt" in text
    assert f'"black gown": {GOWN}' in text
    assert 'plan: "black gown" in ep 1, 3' in text
    assert "ep01: 1 shot(s) 'black gown', 1 shot(s) base" in text
    assert "ep03: shots not written yet; the writer will dress them as planned" in text
    assert _writes(api) == []


def test_an_old_desk_plan_moves_to_the_server_once(desk: Path, api: FakeApi) -> None:
    _serve(api, _with_gown(_story(approved=True)))
    (desk / "shared").mkdir(exist_ok=True)
    (desk / "shared" / "outfits.json").write_text(
        json.dumps(
            {
                "cast_hana": {
                    "name": "Hana",
                    "episodes": {"1": "black gown", "3": "black gown"},
                },
                "_sheet_priced": ["cast_hana:black gown"],
            }
        ),
        encoding="utf-8",
    )
    out = io.StringIO()

    oc.run_outfit_list(desk, cast="Hana", out=out)

    (sent,) = _posts(api)
    assert sent["wear"] == {
        "wardrobe_variant": "black gown",
        "episode_ordinals": [1, 3],
    }
    assert not (desk / "shared" / "outfits.json").exists()
    assert (desk / "shared" / "outfits.migrated.json").is_file()
    assert "Moving this desk's outfit plan to the server" in out.getvalue()
    oc.run_outfit_list(desk, cast="Hana", out=io.StringIO())
    assert len(_posts(api)) == 1


def test_the_old_desk_plan_stays_when_the_server_is_too_old(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _with_gown(_story(approved=True)))
    api.routes[("POST", "/v1/spines/sp1/outfits")] = (
        405,
        {"detail": "Method Not Allowed"},
    )
    (desk / "shared").mkdir(exist_ok=True)
    (desk / "shared" / "outfits.json").write_text(
        json.dumps({"cast_hana": {"name": "Hana", "episodes": {"1": "black gown"}}}),
        encoding="utf-8",
    )

    with pytest.raises(ec.CommandStopped, match="older than the outfit plan"):
        oc.migrate_old_plan(desk, out=io.StringIO())
    assert (desk / "shared" / "outfits.json").is_file()


def test_remove_with_add_stops_before_sending(
    desk: Path, api: FakeApi, capsys: Any
) -> None:
    _serve(api, _with_gown(_story(approved=False)))

    code = produce_main(
        [
            "outfit",
            "--desk",
            str(desk),
            "--cast",
            "Hana",
            "--remove",
            "black gown",
            "--add",
            "x",
        ]
    )

    assert code != 0
    assert "--remove comes alone" in capsys.readouterr().err
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
