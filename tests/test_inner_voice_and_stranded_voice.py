"""A character's thoughts (`inner-voice`), the guard on stranding a voice-only character, and the film preflight.

Hana inner voice (2026-09-30): a thought added as `line --new-voice "Hana (inner voice)"`, then that
voice's only line removed, left a cast member with no lines and no look; filming then failed for the
whole request (`422 spine_reuse_invalid: cast.cast_hana-inner-voice.visual_brief is required`).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import orchestrate
from creation.episode_commands import (
    CommandStopped,
    run_film,
    run_inner_voice,
    run_line,
)
from creation.ops.floor import approve_board
from creation.ops.state import episode_by_ordinal, load_series
from creation.production_state import load_production
from creation.stranded_voice import (
    explain_film_refusal,
    stranded_cast,
    voices_left_without_lines,
)
from fake_api import FakeApi, png_bytes, spine_fixture

VOICE = "cast_hana-inner-voice"
VIDEO = "/v1/video-generations"
ESTIMATE = "/v1/spines/sp1/batches/estimate"
INNER = "/v1/spines/sp1/episodes/1/inner-voice"
REFUSAL = (
    "HTTP 422 POST https://drama.example/v1/video-generations: spine_reuse_invalid: "
    f"cast.{VOICE}.visual_brief is required"
)


def _with_voice(spine: dict[str, Any], *, lines: int = 1) -> dict[str, Any]:
    """Episode 1 gets a voice-only character (no look, in no frame) with ``lines`` off-screen lines."""

    spine["cast"].append(
        {
            "cast_id": VOICE,
            "name": "Hana (inner voice)",
            "role": "her thoughts",
            "voice_only": True,
        }
    )
    spine["beats"][0]["dialogue_lines"] += [
        {
            "line_id": f"line_voice_{n}",
            "cast_id": VOICE,
            "text": f"Don't look at him ({n}).",
            "off_screen": True,
        }
        for n in range(1, lines + 1)
    ]
    return spine


def _patch_route(api: FakeApi) -> None:
    def patch(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        removed = set((body or {})["patch"].get("remove_dialogue_line_ids") or [])
        for beat in api.spine_doc["beats"]:
            beat["dialogue_lines"] = [
                ln for ln in beat["dialogue_lines"] if ln["line_id"] not in removed
            ]
        for edit in (body or {})["patch"].get("dialogue_lines") or []:
            for beat in api.spine_doc["beats"]:
                for ln in beat["dialogue_lines"]:
                    if ln["line_id"] == edit["line_id"]:
                        ln.update(edit)
        return {}

    api.routes[("PATCH", "/v1/spines/sp1")] = patch


def _sent(api: FakeApi, method: str) -> list[Any]:
    return [body for m, _p, body, _k in api.calls if m == method]


# --- The guard on line --remove / --speaker -----------------------------------------------------


def test_removing_a_voice_only_characters_only_line_stops_before_anything_is_sent(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = _with_voice(spine_fixture(approved=False))
    _patch_route(api)

    with pytest.raises(CommandStopped) as stopped:
        run_line(desk, episode=1, remove="line_voice_1", out=io.StringIO())

    message = str(stopped.value)
    assert _sent(api, "PATCH") == []
    assert message.startswith("nothing was sent: removing line_voice_1 leaves")
    assert f"Hana (inner voice) ({VOICE}) with no lines" in message
    assert "stay in the cast with no look" in message
    assert "refuse to film" in message
    assert "keep the line" in message
    assert '--add --beat N --speaker "Hana (inner voice)"' in message
    assert "inner-voice --desk" in message
    assert "--strand-voice" in message


def test_strand_voice_lets_the_removal_through_and_says_what_it_costs(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = _with_voice(spine_fixture(approved=False))
    _patch_route(api)
    out = io.StringIO()

    run_line(desk, episode=1, remove="line_voice_1", strand_voice=True, out=out)

    assert _sent(api, "PATCH")[0]["patch"] == {
        "remove_dialogue_line_ids": ["line_voice_1"]
    }
    assert "!! --strand-voice: Hana (inner voice)" in out.getvalue()
    notes = (desk / "ep01" / "run-notes.md").read_text(encoding="utf-8")
    assert "--strand-voice" in notes


def test_a_voice_with_another_line_left_or_given_one_in_the_same_command_is_not_stopped(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = _with_voice(spine_fixture(approved=False), lines=2)
    _patch_route(api)

    run_line(desk, episode=1, remove="line_voice_1", out=io.StringIO())

    assert _sent(api, "PATCH")[0]["patch"] == {
        "remove_dialogue_line_ids": ["line_voice_1"]
    }


def test_the_same_command_adding_the_voice_a_new_line_passes() -> None:
    spine = _with_voice(spine_fixture(approved=False))

    assert voices_left_without_lines(spine, removed=["line_voice_1"]) == [
        (VOICE, "Hana (inner voice)")
    ]
    assert (
        voices_left_without_lines(spine, removed=["line_voice_1"], added=[VOICE]) == []
    )


def test_removing_a_drawn_characters_line_is_never_stopped() -> None:
    spine = _with_voice(spine_fixture(approved=False))
    spine["cast"][0]["visual_brief"] = {"face": "round"}  # Hana is drawn
    spine["episode_summaries"] = spine["episode_summaries"][:1]
    spine["beats"] = spine["beats"][:1]

    assert voices_left_without_lines(spine, removed=["line_episode_01_01"]) == []


def test_giving_the_voices_only_line_to_someone_else_stops_too(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = _with_voice(spine_fixture(approved=False))
    _patch_route(api)

    with pytest.raises(CommandStopped) as stopped:
        run_line(
            desk, episode=1, line="line_voice_1", speaker="Hana", out=io.StringIO()
        )

    assert _sent(api, "PATCH") == []
    assert "giving line_voice_1 to another speaker leaves" in str(stopped.value)

    run_line(
        desk,
        episode=1,
        line="line_voice_1",
        speaker="Hana",
        strand_voice=True,
        out=io.StringIO(),
    )
    assert _sent(api, "PATCH")[0]["patch"]["dialogue_lines"][0]["cast_id"] == (
        "cast_hana"
    )


def test_the_cli_takes_strand_voice(desk: Path, api: FakeApi) -> None:
    from creation.cli_produce import main as produce_main

    api.spine_doc = _with_voice(spine_fixture(approved=False))
    _patch_route(api)

    assert (
        produce_main(
            ["line", "--desk", str(desk), "--episode", "1", "--remove", "line_voice_1"]
        )
        == 2
    )
    assert _sent(api, "PATCH") == []
    assert (
        produce_main(
            [
                "line",
                "--desk",
                str(desk),
                "--episode",
                "1",
                "--remove",
                "line_voice_1",
                "--strand-voice",
            ]  # fmt: skip
        )
        == 0
    )
    assert len(_sent(api, "PATCH")) == 1


# --- The film preflight ------------------------------------------------------------------------


def _stranded() -> dict[str, Any]:
    spine = _with_voice(spine_fixture(), lines=0)
    return spine


def test_stranded_cast_is_no_look_no_lines_and_no_frame() -> None:
    assert stranded_cast(_stranded()) == [(VOICE, "Hana (inner voice)")]
    assert stranded_cast(_with_voice(spine_fixture())) == []  # still has its line
    framed = _stranded()
    framed["frames"][0]["cast_refs"] = [VOICE]
    assert stranded_cast(framed) == []
    looked = _stranded()
    looked["cast"][-1]["visual_brief"] = {"face": "round"}
    assert stranded_cast(looked) == []


def _film_desk(desk: Path, api: FakeApi) -> None:
    board = desk / "ep01" / "boards" / "board.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_bytes(png_bytes())
    approve_board(desk, episode=1, take_id="t1", image=board)
    set_phase(desk, "complete")
    api.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "0.30"}}


def test_film_pricing_names_the_stranded_character_before_any_spend(
    desk: Path, api: FakeApi
) -> None:
    _film_desk(desk, api)
    api.spine_doc = _stranded()

    text = run_film(desk, episode=1, out=io.StringIO())

    assert api.posted(VIDEO) == []
    assert "PREFLIGHT WARNING" not in text, (
        "fictora-drama #517 films without them: information only"
    )
    assert (
        f"info: stranded_voice (ep01): Hana (inner voice) ({VOICE}) is in the cast with no lines"
        in text
    )
    assert (
        "The server films without them and draws no plate (fictora-drama #517)" in text
    )
    assert "Only an older deploy refuses" in text
    assert f"cast.{VOICE}.visual_brief is required" in text
    assert text.index("stranded_voice") < text.index("Film episode 1 alone")


def test_film_pricing_says_nothing_when_nobody_is_stranded(
    desk: Path, api: FakeApi
) -> None:
    _film_desk(desk, api)
    api.spine_doc = _with_voice(spine_fixture())

    text = run_film(desk, episode=1, out=io.StringIO())

    assert "stranded_voice" not in text and "PREFLIGHT" not in text


def test_the_servers_refusal_is_explained_and_nothing_is_charged(
    desk: Path, api: FakeApi
) -> None:
    _film_desk(desk, api)
    api.spine_doc = _stranded()
    out = io.StringIO()
    run_film(desk, episode=1, out=out)
    api.routes[("POST", VIDEO)] = SystemExit(REFUSAL)
    spend_before = episode_by_ordinal(load_series(desk), 1).spend_usd

    with pytest.raises(CommandStopped) as stopped:
        run_film(desk, episode=1, confirm_spend=True, out=out)

    message = str(stopped.value)
    assert "spine_reuse_invalid" in message
    assert "nothing was filmed or charged" in message
    assert "Hana (inner voice)" in message and "a line back" in message
    assert "older than #517" in message
    assert episode_by_ordinal(load_series(desk), 1).spend_usd == spend_before
    assert load_production(desk).pending["film-ep01"]["job_id"] is None
    assert "info: stranded_voice" in out.getvalue()  # said again before sending


def test_step_estimate_warns_before_the_spend_yes(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "ready_estimate")
    api.spine_doc = _stranded()
    api.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "0.30"}}

    result = orchestrate.run_step(desk)

    first = result.message.split("\n")[0]
    assert first.startswith("info: stranded_voice (ep01): Hana (inner voice)")
    assert "PREFLIGHT WARNING" not in result.message


def test_step_film_refusal_is_explained(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "ready_video")
    api.spine_doc = _stranded()
    api.routes[("POST", VIDEO)] = SystemExit(REFUSAL)

    with pytest.raises(SystemExit) as stopped:
        orchestrate.run_step(desk)

    assert "nothing was filmed or charged" in str(stopped.value.code)
    assert load_production(desk).phase == "failed"


def test_other_refusals_are_left_alone() -> None:
    assert explain_film_refusal("HTTP 422 POST x: cast_not_approved: nope") is None


# --- inner-voice -----------------------------------------------------------------------------------


def _inner_route(api: FakeApi) -> None:
    def put(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        summary = api.spine_doc["episode_summaries"][body["episode_ordinal"] - 1]
        summary["inner_voice"] = body["cues"]
        return dict(api.spine_doc)

    api.routes[("PUT", INNER)] = put


def test_a_thought_goes_on_the_character_with_the_whole_list_and_no_cast_place(
    desk: Path, api: FakeApi
) -> None:
    _inner_route(api)
    api.spine_doc["episode_summaries"][0]["inner_voice"] = [
        {
            "cue_id": "iv_ep01_01",
            "start_ms": 500,
            "end_ms": 1500,
            "speaker_cast_id": "cast_ren",
            "line": "She knows.",
        }
    ]
    cast_before = list(api.spine_doc["cast"])
    out = io.StringIO()

    cues = run_inner_voice(
        desk, episode=1, cast="hana", text="Don't look at him.", at=3.2, out=out
    )

    body = _sent(api, "PUT")[0]
    assert body == {
        "spine_version": "v5",
        "episode_ordinal": 1,
        "cues": [
            {
                "cue_id": "iv_ep01_01",
                "start_ms": 500,
                "end_ms": 1500,
                "speaker_cast_id": "cast_ren",
                "line": "She knows.",
            },
            {
                "cue_id": "iv_ep01_02",
                "start_ms": 3200,
                "end_ms": 4800,
                "speaker_cast_id": "cast_hana",
                "line": "Don't look at him.",
            },
        ],
    }
    assert [c["cue_id"] for c in cues] == ["iv_ep01_01", "iv_ep01_02"]
    assert api.spine_doc["cast"] == cast_before
    text = out.getvalue()
    assert "Hana (thinks): Don't look at him." in text
    assert "script approval: unchanged" in text
    assert "Heard in `finish`" in text and "3.2s on the episode" in text
    assert "lays it at the cue and captions it in Georgia italic" in text
    assert "--episode 1 --take tK" in text
    assert "does not lay" not in text
    assert "no --until: 1.60s from the word count" in text
    notes = (desk / "ep01" / "run-notes.md").read_text(encoding="utf-8")
    assert "inner-voice: added iv_ep01_02" in notes
    assert (desk / "ep01" / "api" / "spine.json").is_file()


def test_unknown_cast_and_bad_flags_stop_before_any_call(
    desk: Path, api: FakeApi
) -> None:
    _inner_route(api)
    with pytest.raises(CommandStopped, match="no cast member 'Mira'"):
        run_inner_voice(desk, episode=1, cast="Mira", text="Hm.", at=1.0)
    with pytest.raises(CommandStopped, match="needs --cast NAME"):
        run_inner_voice(desk, episode=1, text="Hm.")
    with pytest.raises(CommandStopped, match="pass one of"):
        run_inner_voice(desk, episode=1, cast="Hana", text="Hm.", at=1, clear=True)
    with pytest.raises(CommandStopped, match="must be after --at"):
        run_inner_voice(desk, episode=1, cast="Hana", text="Hm.", at=2, until=1)
    assert _sent(api, "PUT") == []


def test_remove_by_number_and_list(desk: Path, api: FakeApi) -> None:
    _inner_route(api)
    api.spine_doc["episode_summaries"][0]["inner_voice"] = [
        {
            "cue_id": "iv_ep01_01",
            "start_ms": 500,
            "end_ms": 1500,
            "speaker_cast_id": "cast_hana",
            "line": "One.",
        }
    ]
    listing = io.StringIO()
    run_inner_voice(desk, episode=1, out=listing)
    assert "1. iv_ep01_01  0.50-1.50s  Hana (thinks): One." in listing.getvalue()
    assert _sent(api, "PUT") == []

    run_inner_voice(desk, episode=1, remove="1", out=io.StringIO())

    assert _sent(api, "PUT")[0]["cues"] == []


def test_the_servers_refusal_is_printed_with_plain_words(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("PUT", INNER)] = SystemExit(
        f"HTTP 422 PUT https://drama.example{INNER}: invalid_request: speaker_cast_id unknown"
    )

    with pytest.raises(CommandStopped) as stopped:
        run_inner_voice(desk, episode=1, cast="Hana", text="Hm.", at=1.0)

    assert "speaker_cast_id unknown" in str(stopped.value)
    assert "nothing was saved" in str(stopped.value)


def test_the_cli_takes_inner_voice(desk: Path, api: FakeApi) -> None:
    from creation.cli_produce import main as produce_main

    _inner_route(api)
    code = produce_main(
        [
            "inner-voice",
            "--desk",
            str(desk),
            "--episode",
            "1",
            "--cast",
            "Hana",
            "--text",
            "Breathe.",
            "--at",
            "2",
            "--until",
            "3.5",
        ]  # fmt: skip
    )

    assert code == 0
    assert _sent(api, "PUT")[0]["cues"][0]["end_ms"] == 3500


# --- a Japanese thought under an English caption (Hanakaze ep 7) ------------------------------------


def test_a_thought_can_say_japanese_under_an_english_caption(
    desk: Path, api: FakeApi
) -> None:
    """The cue holds one ``line``: the caption goes to the server, the words said stay on the desk."""

    from creation.inner_voice import load_spoken, spoken_path

    _inner_route(api)
    out = io.StringIO()

    run_inner_voice(
        desk,
        episode=1,
        cast="hana",
        text="Don't look at him.",
        spoken_text="見ないで。",
        at=3.2,
        out=out,
    )

    (body,) = _sent(api, "PUT")
    assert body["cues"] == [
        {"cue_id": "iv_ep01_01", "start_ms": 3200, "end_ms": 4800, "speaker_cast_id": "cast_hana",
         "line": "Don't look at him."}
    ], "the contract cue is unchanged: the caption, no other field"  # fmt: skip
    assert load_spoken(desk, 1) == {
        "iv_ep01_01": {"line": "Don't look at him.", "spoken_text": "見ないで。"}
    }
    text = out.getvalue()
    assert "Hana (thinks): Don't look at him.  [says: 見ないで。]" in text
    assert "NOT ENGLISH" not in text

    listing = io.StringIO()
    run_inner_voice(desk, episode=1, out=listing)
    assert "[says: 見ないで。]" in listing.getvalue()

    run_inner_voice(desk, episode=1, remove="iv_ep01_01", out=io.StringIO())
    assert load_spoken(desk, 1) == {}, "a removed cue's words go with it"
    assert spoken_path(desk, 1).is_file()


def test_spoken_text_needs_a_new_thought_and_a_japanese_caption_is_named(
    desk: Path, api: FakeApi
) -> None:
    _inner_route(api)
    with pytest.raises(CommandStopped, match="--spoken-text goes with a new thought"):
        run_inner_voice(desk, episode=1, spoken_text="見ないで。")
    with pytest.raises(CommandStopped, match="--spoken-text is empty"):
        run_inner_voice(
            desk, episode=1, cast="Hana", text="Hm.", at=1.0, spoken_text="  "
        )
    assert _sent(api, "PUT") == []

    out = io.StringIO()
    run_inner_voice(desk, episode=1, cast="Hana", text="見ないで。", at=1.0, out=out)
    assert (
        "the caption is not English, so finish leaves it uncaptioned (NOT ENGLISH)"
        in out.getvalue()
    )
    assert '--spoken-text "<the words said>"' in out.getvalue()


def test_stale_spoken_words_never_ride_a_changed_caption() -> None:
    from creation.inner_voice import spoken_for

    saved = {"iv_ep01_01": {"line": "Don't look at him.", "spoken_text": "見ないで。"}}

    assert spoken_for(saved, "iv_ep01_01", "Don't  look at him.") == "見ないで。"
    assert spoken_for(saved, "iv_ep01_01", "Look at him.") == ""
    assert spoken_for(saved, "iv_ep01_02", "Don't look at him.") == ""
