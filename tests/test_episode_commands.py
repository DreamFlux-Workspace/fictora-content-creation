"""Episode flow commands against a fake API: arc, brief, author, memory, edit, look notes, redraw, line check."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from conftest import set_phase
from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.ops.state import episode_by_ordinal, load_series
from creation.production_state import load_production
from fake_api import FakeApi, spine_fixture

ARCS = {
    "facts": {"arc_pick": True},
    "recap": "Hana closed the shop on Ren.",
    "arc_options": [
        {
            "arc_id": "arc_1",
            "title": "The rival shop",
            "line": "Ren opens across the street.",
        },
        {
            "arc_id": "arc_2",
            "title": "The old recipe",
            "line": "Hana hunts her grandmother's mochi.",
        },
        {
            "arc_id": "arc_3",
            "title": "The lease",
            "line": "The landlord wants the shop back.",
        },
    ],
}
DIRECTIONS = {
    "next_episode_ordinal": 2,
    "directions": [
        {
            "direction_id": "dir_1",
            "title": "Opening day",
            "line": "Ren's shop opens with a queue.",
        },
        {
            "direction_id": "dir_2",
            "title": "Sabotage",
            "line": "Someone swaps the sugar.",
        },
    ],
}


def test_arc_list_then_pick_keeps_the_arc_and_saves_episode_twos_directions(
    desk: Path, api: FakeApi
) -> None:
    briefs = [ARCS, DIRECTIONS]
    api.routes[("POST", "/v1/spines/sp1/director/brief")] = lambda *_: briefs.pop(0)
    api.routes[("POST", "/v1/spines/sp1/series-arc")] = {
        "spine_id": "sp1",
        "spine_version": "v6",
    }
    out = io.StringIO()

    ec.run_arc_list(desk, episodes=30, out=out)
    ec.run_arc_pick(
        desk,
        option=2,
        line="Hana hunts the recipe, one ingredient an episode.",
        out=out,
    )

    assert api.posted("/v1/spines/sp1/director/brief") == [
        {"spine_version": "v5", "season_target_episode_count": 30},
        {"spine_version": "v6"},
    ]
    assert api.posted("/v1/spines/sp1/series-arc") == [
        {
            "spine_version": "v5",
            "arc": {
                "arc_id": "arc_2",
                "title": "The old recipe",
                "line": "Hana hunts the recipe, one ingredient an episode.",
            },
        }
    ]
    state = load_production(desk)
    assert state.series_arc is not None and state.series_arc["rewritten"] is True
    assert "2. The old recipe" in out.getvalue() and "1. Opening day" in out.getvalue()
    assert (desk / "api" / "brief-ep02-v1.json").is_file()


def test_arc_list_on_a_story_with_no_arc_pick_records_nothing(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", "/v1/spines/sp1/director/brief")] = {
        "facts": {"arc_pick": False},
        "arc_options": [],
    }

    with pytest.raises(ec.CommandStopped, match="offers no series arcs"):
        ec.run_arc_list(desk)
    assert load_production(desk).arc_options == []


def test_the_intended_run_is_a_soft_default_in_range(desk: Path) -> None:
    with pytest.raises(ec.CommandStopped, match="7-240"):
        ec.run_arc_list(desk, episodes=3)


def test_author_sends_the_picked_direction_reads_the_extension_job_and_points_the_desk(
    desk: Path, api: FakeApi
) -> None:
    (desk / "api").mkdir(exist_ok=True)
    (desk / "api" / "brief-ep02-v1.json").write_text(
        json.dumps(DIRECTIONS), encoding="utf-8"
    )
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2",
        "status": "queued",
    }
    api.jobs["job_ext_2"] = {"status": "completed"}
    set_phase(desk, "complete", estimate_usd=1.2, last_delivery_url="https://x")
    out = io.StringIO()

    ec.run_author(
        desk, episode=2, direction=ec.author_direction(desk, 2, pick=2), out=out
    )

    assert api.posted("/v1/spines/sp1/pilot-episodes/2/author") == [
        {
            "spine_version": "v5",
            "direction": {
                "direction_id": "dir_2",
                "title": "Sabotage",
                "line": "Someone swaps the sugar.",
            },
        }
    ]
    assert api.polled == [("job_ext_2", False)]
    state = load_production(desk)
    assert (state.episode_ordinal, state.phase, state.estimate_usd) == (
        2,
        "wait_script",
        None,
    )
    slot = episode_by_ordinal(load_series(desk), 2)
    assert [line.original for line in slot.takes[0].lines] == [
        "We're closed.",
        "Not for me.",
    ]
    assert "ep02 (ep_02) Ep 2" in out.getvalue()


def test_author_with_the_humans_words_sends_no_direction_id(desk: Path) -> None:
    assert ec.author_direction(desk, 3, line="  Ren  apologises ", title="Sorry") == {
        "line": "Ren apologises",
        "title": "Sorry",
    }
    with pytest.raises(ec.CommandStopped, match="Pick one"):
        ec.author_direction(desk, 3, pick=1, line="x")
    with pytest.raises(ec.CommandStopped, match="no saved brief for episode 3"):
        ec.author_direction(desk, 3, pick=1)


def test_an_interrupted_author_picks_up_the_same_job_without_posting_again(
    desk: Path, api: FakeApi
) -> None:
    state = load_production(desk)
    state.pending["author-ep02"] = {"key": "k-author-ep02-a1", "job_id": "job_ext_2"}
    from creation.production_state import save_production

    save_production(desk, state)
    api.jobs["job_ext_2"] = {"status": "completed"}

    ec.run_author(desk, episode=2, out=io.StringIO())

    assert api.posted("/v1/spines/sp1/pilot-episodes/2/author") == []
    assert load_production(desk).attempts["author-ep02"] == 1


def test_a_failed_author_job_names_its_rule_and_leaves_the_desk_where_it_was(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2"
    }
    api.jobs["job_ext_2"] = {
        "status": "failed",
        "error": {
            "code": "authoring_validation_failed",
            "message": "cast_id at beats[2]: unknown cast",
        },
    }
    set_phase(desk, "complete")

    with pytest.raises(
        ec.CommandStopped, match="authoring_validation_failed: cast_id at beats"
    ):
        ec.run_author(desk, episode=2, out=io.StringIO())
    state = load_production(desk)
    assert (state.episode_ordinal, state.phase) == (1, "complete")
    assert "author-ep02" not in state.pending and state.attempts["author-ep02"] == 1


def test_memory_sends_only_contract_fields_and_a_repeat_writes_nothing(
    desk: Path, api: FakeApi
) -> None:
    stored = {"notes": ["one situation per episode"], "threads": [], "server_only": 1}
    api.routes[("GET", "/v1/spines/sp1/memory")] = {"memory": stored}
    api.routes[("PUT", "/v1/spines/sp1/memory")] = {"memory": {}}

    ec.run_memory(desk, note="punchline in the last take", out=io.StringIO())
    ec.run_memory(desk, note="one situation per episode", out=io.StringIO())

    puts = [body for method, _, body, _ in api.calls if method == "PUT"]
    assert puts == [
        {
            "spine_version": "v5",
            "memory": {
                "notes": ["one situation per episode", "punchline in the last take"],
                "threads": [],
            },
        }
    ]


def test_edit_before_the_gate_is_a_patch_of_the_merged_direction(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    api.routes[("PATCH", "/v1/spines/sp1")] = {"spine_version": "v6"}

    ec.run_edit(
        desk,
        episode=1,
        beat="1",
        assignments=[ec.parse_assignment("camera_move=dolly_in")],
        out=io.StringIO(),
    )

    patches = [body for method, _, body, _ in api.calls if method == "PATCH"]
    assert patches[0]["patch"]["beats"][0]["motion_direction"] == {
        "camera_move": "dolly_in",
        "intensity": "low",
    }


def test_edit_after_the_gate_runs_the_cascade_with_paid_items_off(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = {
        "proposal_id": "prop_1",
        "items": [
            {
                "item_id": "i_frames",
                "recipe_id": "frames_rewrite",
                "estimated_tier": "text",
                "selected": True,
            },
            {
                "item_id": "i_board",
                "recipe_id": "regen_board_ep01_set01",
                "estimated_tier": "media",
                "selected": True,
            },
        ],
    }
    api.routes[("POST", "/v1/spines/sp1/cascade/execute")] = {
        "stale_storyboard_sets": [{"episode_ordinal": 1, "set_index": 1}]
    }
    out = io.StringIO()

    ec.run_edit(
        desk,
        episode=1,
        frame="frame_episode_01_02",
        assignments=[("shot_scale", "close up")],
        out=out,
    )

    execute = api.posted("/v1/spines/sp1/cascade/execute")[0]
    assert execute["items"] == [
        {"item_id": "i_frames", "selected": True},
        {"item_id": "i_board", "selected": False},
    ]
    assert "redraw-board --episode 1 --take t1" in out.getvalue()
    assert load_series(desk).spend_usd == 0.0


def test_edit_pins_a_performed_line_and_refuses_it_on_an_english_show(
    desk: Path, api: FakeApi
) -> None:
    with pytest.raises(ec.CommandStopped, match="English"):
        ec.build_patch(
            api.spine_doc, episode=1, line_id="line_episode_01_01", spoken="閉店です。"
        )
    patch, _ = ec.build_patch(
        spine_fixture(spoken_language="ja-JP"),
        episode=1,
        line_id="line_episode_01_01",
        spoken="もう閉店です。",
        subtitle="We're closed.",
    )
    assert patch == {
        "dialogue_lines": [
            {
                "line_id": "line_episode_01_01",
                "spoken_text": "もう閉店です。",
                "subtitle_text": "We're closed.",
            }
        ]
    }
    with pytest.raises(ec.CommandStopped, match="send it with --spoken"):
        ec.build_patch(
            spine_fixture(spoken_language="ja-JP"),
            episode=1,
            line_id="line_episode_01_01",
            subtitle="x",
        )


def test_look_note_add_is_capped_at_five(desk: Path, api: FakeApi) -> None:
    api.spine_doc["look_notes"] = [{"note_id": f"n{i}", "text": "x"} for i in range(5)]
    with pytest.raises(ec.CommandStopped, match="already has 5"):
        ec.run_look_note(desk, add="darker overall", out=io.StringIO())
    api.routes[("DELETE", "/v1/spines/sp1/look-notes/n1")] = {}
    ec.run_look_note(desk, remove="2", out=io.StringIO())
    assert [(m, p) for m, p, _, _ in api.calls if m == "DELETE"] == [
        ("DELETE", "/v1/spines/sp1/look-notes/n1")
    ]


def test_redraw_board_uses_the_regenerate_route_warns_a_reroll_and_reopens_the_gate(
    desk: Path, api: FakeApi
) -> None:
    from creation.spine_view import frames_by_set, frames_digest

    digest = frames_digest(frames_by_set(api.spine_doc, episode=1)[1])
    set_phase(desk, "wait_spend", board_digests={"ep01-t1": digest})
    api.routes[("POST", "/v1/spines/sp1/episodes/1/boards/1/regenerate")] = {
        "job_id": "job_redraw"
    }
    api.jobs["job_redraw"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/1/boards/exposure")] = {
        "boards": [{"set_index": 1, "mean_percent": 30.0}]
    }
    out = io.StringIO()

    ec.run_redraw_board(
        desk,
        episode=1,
        take_id="t1",
        cause="her face was under the caption band",
        out=out,
    )

    assert api.posted("/v1/spines/sp1/boards/enrol") == []
    body = api.posted("/v1/spines/sp1/episodes/1/boards/1/regenerate")[0]
    assert body["episode_count"] == 1 and "notes" not in body
    assert "a re-roll" in out.getvalue()
    assert "back at the board gate" in out.getvalue()
    state = load_production(desk)
    assert state.phase == "wait_board" and state.board_paths["t1"].startswith(
        "ep01/boards/board-ep01-t1-v"
    )
    assert episode_by_ordinal(load_series(desk), 1).spend_usd == pytest.approx(0.30)


def _plate_routes(api: FakeApi) -> None:
    def add_note(method: str, path: str, body: dict | None) -> dict:
        card = next(c for c in api.spine_doc["cast"] if c["cast_id"] == "cast_ren")
        card.setdefault("creator_notes", []).append(
            {"note_id": "n1", "text": (body or {})["text"]}
        )
        return {"spine": api.spine_doc}

    def redraw(method: str, path: str, body: dict | None) -> dict:
        for asset in api.spine_doc["media_assets"]:
            if asset.get("relation_id") == "cast_ren":
                asset["url"] = "https://r2.example/ren-v2.png"
        return {"job_id": "job_plate"}

    api.routes[("POST", "/v1/spines/sp1/cast/cast_ren/notes")] = add_note
    api.routes[("POST", "/v1/spines/sp1/cast/cast_ren/regenerate")] = redraw
    api.jobs["job_plate"] = {"status": "completed"}


def test_redraw_plate_notes_one_character_and_redraws_only_them(
    desk: Path, api: FakeApi
) -> None:
    _plate_routes(api)
    plates = desk / "ep01" / "plates"
    plates.mkdir(parents=True, exist_ok=True)
    from fake_api import png_bytes

    (plates / "plate-ep01-1-v1.png").write_bytes(png_bytes(200))
    (plates / "plate-ep01-2-v1.png").write_bytes(png_bytes(60))
    out = io.StringIO()

    made = ec.run_redraw_plate_with_note(
        desk, cast="Ren", note="  Older, a scar over the left brow ", out=out
    )

    assert api.posted("/v1/spines/sp1/cast/cast_ren/notes") == [
        {"spine_version": "v5", "text": "Older, a scar over the left brow"}
    ]
    [(method, path, body, key)] = [c for c in api.calls if c[1].endswith("/regenerate")]
    assert key and body and "notes" not in body, (
        "a recorded key; the notes ride on the card, not the body"
    )
    assert not [
        c for c in api.calls if "cast_hana" in c[1] or c[1].endswith("/cast/enrol")
    ], "nobody else is drawn"
    assert made.name == "plate-ep01-2-v2.png", (
        "the new plate sits next to the old one as a new version"
    )
    assert (plates / "plate-ep01-2-v1.png").is_file() and (
        plates / "contact-ep01-v1.png"
    ).is_file()
    assert (
        "download",
        {"url": "https://r2.example/ren-v2.png", "path": str(made)},
    ) in api.events
    assert episode_by_ordinal(load_series(desk), 1).spend_usd == pytest.approx(0.30)
    assert "nobody else" in out.getvalue()

    ec.run_redraw_plate_with_note(
        desk,
        cast="cast_ren",
        note="older, a scar over the left brow",
        out=io.StringIO(),
    )
    assert len(api.posted("/v1/spines/sp1/cast/cast_ren/notes")) == 1, (
        "the same note is never stacked twice"
    )


def test_an_interrupted_plate_redraw_picks_up_the_same_job_and_an_unknown_name_stops(
    desk: Path, api: FakeApi
) -> None:
    _plate_routes(api)
    unit = "plate-cast_ren-" + __import__("hashlib").sha256(b"older").hexdigest()[:10]
    state = load_production(desk)
    state.pending[unit] = {"key": "pfx-k", "job_id": "job_plate"}
    from creation.production_state import save_production

    save_production(desk, state)
    ec.run_redraw_plate_with_note(desk, cast="Ren", note="Older", out=io.StringIO())
    assert api.posted("/v1/spines/sp1/cast/cast_ren/regenerate") == [], (
        "picked up, not posted (no second charge)"
    )
    with pytest.raises(ec.CommandStopped, match="the cast is: Hana"):
        ec.run_redraw_plate_with_note(
            desk, cast="Mika", note="taller", out=io.StringIO()
        )


def test_check_lines_names_the_approved_line_the_take_was_not_asked_to_say(
    desk: Path, api: FakeApi
) -> None:
    api_dir = desk / "ep01" / "api"
    (api_dir / "spine.json").write_text(json.dumps(api.spine_doc), encoding="utf-8")
    facts = {
        "lines": [
            {"line_id": "line_episode_01_01", "count": 1},
            {"line_id": "line_episode_01_02", "count": 0},
        ]
    }
    (api_dir / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts), encoding="utf-8"
    )
    out = io.StringIO()

    missing = ec.run_check_lines(desk, episode=1, out=out)

    assert missing == 1
    assert "line 2 ('Not for me.') was not in the take's instructions" in out.getvalue()
    assert "1 of 2 approved lines asked" in out.getvalue()


def test_the_cli_says_why_a_command_stopped(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = produce_main(
        [
            "redraw-board",
            "--desk",
            str(desk),
            "--episode",
            "1",
            "--take",
            "t1",
            "--cause",
            " ",
        ]
    )
    assert code == 2
    assert "--cause is required" in capsys.readouterr().err


def _row_facts(*line_shots: tuple[str, int | None], shots: int = 2) -> dict:
    return {
        "shots": [
            {
                "shot_index": index,
                "start_seconds": (index - 1) * 7.5,
                "end_seconds": index * 7.5,
                "speaks": True,
            }
            for index in range(1, shots + 1)
        ],
        "lines": [
            {
                "line_id": line_id,
                "count": 1,
                "shot_index": shot,
                "start_seconds": None if shot is None else (shot - 1) * 7.5,
                "end_seconds": None if shot is None else shot * 7.5,
            }
            for line_id, shot in line_shots
        ],
    }


def test_check_lines_reports_each_lines_row_and_flags_a_speaker_out_of_frame(
    desk: Path, api: FakeApi
) -> None:
    """Learnings #37: Ren's line plays on row 2, which only draws Hana."""

    api_dir = desk / "ep01" / "api"
    (api_dir / "spine.json").write_text(json.dumps(api.spine_doc), encoding="utf-8")
    facts = _row_facts(("line_episode_01_01", 1), ("line_episode_01_02", 2))
    (api_dir / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts), encoding="utf-8"
    )
    out = io.StringIO()

    missing = ec.run_check_lines(desk, episode=1, out=out)

    text = out.getvalue()
    assert missing == 0
    assert (
        "ep01 t1: line 1 ('We're closed.', Hana): shot 1 (0.0–7.5 s), row 1, Hana in frame"
        in text
    )
    assert (
        "!! ep01 t1: line 2 ('Not for me.', Ren): spoken in shot 2 (7.5–15.0 s), row 2, but Ren is not in"
        in text
    )


def _scp_spine() -> dict:
    """SCP-173 take 1: the denial (line 3) plays on row 4, the statue's POV."""

    def frame(ordinal: int, row: int, cast: list[str]) -> dict:
        return {
            "frame_id": f"f{ordinal}",
            "episode_id": "episode_01",
            "ordinal": ordinal,
            "board_row": row,
            "storyboard_group_id": "asset_plan_board_sp_episode_01_set01",
            "cast_refs": cast,
            "visual_brief": {
                "subject_blocking": [{"cast_id": cast_id} for cast_id in cast]
            },
        }

    statue, d9341, voice = "cast_the-statue", "cast_d-9341", "cast_speaker-voice"
    return {
        "cast": [
            {"cast_id": statue, "name": "The Statue"},
            {"cast_id": d9341, "name": "D-9341"},
            {"cast_id": voice, "name": "Speaker Voice"},
        ],
        "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
        "beats_per_storyboard_set": [3],
        "beats": [
            {
                "episode_id": "episode_01",
                "ordinal": 1,
                "dialogue_lines": [
                    {
                        "line_id": "l1",
                        "cast_id": voice,
                        "text": "Do not break eye contact.",
                        "off_screen": True,
                    }
                ],
            },
            {
                "episode_id": "episode_01",
                "ordinal": 2,
                "dialogue_lines": [
                    {
                        "line_id": "l2",
                        "cast_id": d9341,
                        "text": "Please— open the door!",
                    }
                ],
            },
            {
                "episode_id": "episode_01",
                "ordinal": 3,
                "dialogue_lines": [
                    {
                        "line_id": "l3",
                        "cast_id": d9341,
                        "text": "No no no— I'm not blinking",
                    }
                ],
            },
        ],
        "frames": [
            frame(1, 1, [statue]),
            frame(2, 1, [statue]),
            frame(3, 2, [d9341, statue]),
            frame(4, 2, [d9341, statue]),
            frame(5, 3, [d9341]),
            frame(6, 3, [d9341]),
            frame(7, 4, [statue]),
            frame(8, 4, [statue]),
        ],
    }


def test_scp_denial_over_the_statue_is_flagged_and_the_off_screen_order_is_not() -> (
    None
):
    facts = _row_facts(("l1", 1), ("l2", 3), ("l3", 4), shots=4)

    lines, flagged = ec.line_row_lines(
        _scp_spine(), facts, episode=1, take_index=1, take_count=1, label="ep01 t1"
    )

    assert flagged == 1
    assert (
        "line 1 ('Do not break eye contact.', Speaker Voice, off screen): shot 1 (0.0–7.5 s), row 1 (heard, not seen)"
        in lines[0]
    )
    assert lines[1].endswith("row 3, D-9341 in frame")
    assert lines[2].startswith("  !! ep01 t1: line 3")
    assert "row 4, but D-9341 is not in that row's frames" in lines[2]


def test_a_speaker_in_one_frame_of_the_row_is_reported_not_flagged() -> None:
    spine = _scp_spine()
    spine["frames"][6]["cast_refs"] = ["cast_d-9341", "cast_the-statue"]
    facts = _row_facts(("l1", 1), ("l2", 3), ("l3", 4), shots=4)

    lines, flagged = ec.line_row_lines(
        spine, facts, episode=1, take_index=1, take_count=1, label="ep01 t1"
    )

    assert flagged == 0
    assert "D-9341 in 1 of 2 frames of the row" in lines[2]


def test_rows_are_not_guessed_when_the_take_merged_them() -> None:
    facts = _row_facts(("l1", 1), ("l2", 2), ("l3", 3), shots=3)

    lines, flagged = ec.line_row_lines(
        _scp_spine(), facts, episode=1, take_index=1, take_count=1, label="ep01 t1"
    )

    assert flagged == 0
    assert all("board rows not matched (3 shots, 4 rows)" in line for line in lines)
