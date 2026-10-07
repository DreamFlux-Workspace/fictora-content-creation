"""The pitch card: the producer approves the idea before any paid drawing (founder, 7 Oct 2026).

About half of four series' spend (~$13.50 of ~$44) went on re-renders after the
producer rejected the idea or the script late: "the romance feels forced",
"the plot is all over the place", blank faces from restrained direction, broken
world rules, the premise device in every episode. The card is checked and
stored free; on desks from 6 Oct 2026 ``step`` draws no plates or boards until
it has the human's yes. The script checks are warnings on every desk.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest

from conftest import SHOWN_PRICES, set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.cli_produce import main
from creation.harness_rules import silent_first_beat_lines, word_age_lines
from creation.pitch_card import (
    PG_RULE,
    PitchRefused,
    approve_pitch,
    approved_pitch,
    current_pitch,
    emotional_beat_lines,
    parse_pitch_markdown,
    pitch_gate_refusal,
    premise_device_lines,
    restraint_beat_lines,
    run_pitch,
    script_pitch_lines,
    store_pitch,
    stored_pitches,
)
from creation.rules_epoch import run_rules_epoch
from fake_api import FakeApi, spine_fixture

CAST = "/v1/spines/sp1/cast/enrol"
BOARDS = "/v1/spines/sp1/boards/enrol"


def card(**changes: Any) -> dict[str, Any]:
    """A complete card (The Gallery Heiress, Tuesday's version that was right first time)."""

    base: dict[str, Any] = {
        "throughline": "Seo-yeon walks in on her fiancé and her stepsister and answers with one cold line.",
        "charged_situation": "The birthday cake in her hands while the pair spring apart under her mother's portrait.",
        "world_rules": ["The gallery belongs to Seo-yeon's late mother."],
        "premise_device": {
            "device": "rain",
            "used": False,
            "why": "the betrayal carries it",
        },
        "emotional_beats": [
            {
                "beat": "Seo-yeon sees the ring on Ye-rin's finger",
                "expression": "tears and rage",
                "delivery": "through_tears",
            },
            {
                "beat": "Ye-rin flashes the ring",
                "expression": "smug grin",
                "delivery": "laughing",
            },
        ],
        "intimacy": "The pair springing apart, lipstick smeared on his mouth, her thumb on her own lip.",
        "shouted_lines": False,
        "hook_line_on_screen": False,
        "hook": "The door swings open on the pair springing apart.",
        "ending": "A man in a black overcoat in the dark doorway.",
        "voices": {"mode": "locked", "characters": {"Seo-yeon": "Rachel"}},
    }
    base.update(changes)
    return base


def _legacy(desk: Path) -> Path:
    run_rules_epoch(desk, set_to="legacy", out=io.StringIO())
    return desk


# --- store and validate ---------------------------------------------------------------------------


def test_a_complete_card_is_stored_versioned_and_printed(desk: Path) -> None:
    out = io.StringIO()
    path = desk / "pitch.json"
    path.write_text(json.dumps(card()), encoding="utf-8")

    assert run_pitch(desk, episode=1, file=path, out=out) == 0

    stored = desk / "ep01" / "pitch-v1.json"
    assert stored.is_file()
    text = out.getvalue()
    assert "Throughline: Seo-yeon walks in" in text and "NOT approved yet" in text
    assert "approve --desk" in text and "--gate pitch --episode 1" in text
    assert (
        "Seo-yeon: Rachel" in text
    )  # the voices on the card, for the operator to confirm


def test_a_card_missing_fields_is_refused_with_a_plain_list_and_nothing_stored(
    desk: Path,
) -> None:
    raw = card()
    for name in ("charged_situation", "hook", "shouted_lines", "emotional_beats"):
        raw.pop(name)

    with pytest.raises(PitchRefused) as refused:
        store_pitch(desk, 1, raw)

    message = str(refused.value)
    assert "Nothing was saved" in message
    for name in ("charged_situation", "hook:", "shouted_lines", "emotional_beats"):
        assert name in message
    assert stored_pitches(desk, 1) == []
    assert not (desk / "ep01" / "pitch-v1.json").exists()


def test_a_beat_without_expression_or_a_delivery_the_server_lacks_is_refused(
    desk: Path,
) -> None:
    raw = card(
        emotional_beats=[
            {"beat": "she sees them", "expression": "", "delivery": "sobbing"}
        ]
    )

    with pytest.raises(PitchRefused) as refused:
        store_pitch(desk, 1, raw)

    assert "expression is missing" in str(refused.value)
    assert "'sobbing' is not one the server takes" in str(refused.value)


def test_a_two_sentence_throughline_is_refused(desk: Path) -> None:
    with pytest.raises(PitchRefused, match="2 sentences: write ONE sentence"):
        store_pitch(desk, 1, card(throughline="She finds them. Then she leaves."))
    with pytest.raises(PitchRefused, match="characters: keep it to one sentence"):
        store_pitch(desk, 1, card(throughline="x" * 230))


def test_a_new_card_is_a_new_version_and_the_old_one_is_kept(desk: Path) -> None:
    first, _, _ = store_pitch(desk, 1, card())
    before = first.path.read_bytes()
    second, _, written = store_pitch(desk, 1, card(hook="A cake tilts in her hands."))

    assert written and second.version == 2 and first.path.read_bytes() == before
    assert current_pitch(desk, 1).version == 2  # type: ignore[union-attr]
    # The same card again writes nothing new.
    again, _, written = store_pitch(desk, 1, card(hook="A cake tilts in her hands."))
    assert not written and again.version == 2


def test_the_markdown_card_reads_every_field(desk: Path) -> None:
    text = """# Pitch
## Throughline
Taeho and Yeon share one umbrella and almost say it.
## Charged situation
One umbrella, two men, pouring rain.
## World rules
- Yeon is a fox who cannot lie.
- Taeho cannot see the tails.
## Premise device
device: rain
used: yes
why: the umbrella scene needs it
## Emotional beats
- hands meet on the handle | expression: flustered blush | delivery: breathless
## Intimacy
Hands meeting on the umbrella handle; the aftermath, never contact.
## Shouted lines
no
## Hook line on screen
no
## Hook
Rain hammering an umbrella, two hands on one handle.
## Ending
Yeon's shadow loses a tail.
## Voices
mode: model
- Taeho: model voice
"""
    raw = parse_pitch_markdown(text)
    path = desk / "pitch.md"
    path.write_text(text, encoding="utf-8")

    assert run_pitch(desk, episode=1, file=path, out=io.StringIO()) == 0
    pitch = current_pitch(desk, 1).pitch  # type: ignore[union-attr]
    assert pitch["world_rules"] == [
        "Yeon is a fox who cannot lie.",
        "Taeho cannot see the tails.",
    ]
    assert pitch["premise_device"] == {
        "device": ["rain"],
        "used": True,
        "why": "the umbrella scene needs it",
    }
    assert pitch["emotional_beats"][0] == {
        "beat": "hands meet on the handle",
        "expression": "flustered blush",
        "delivery": "breathless",
    }
    assert pitch["shouted_lines"] is False and raw["voices"]["characters"] == {
        "Taeho": "model voice"
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("intimacy", "They kiss under the umbrella."),
        ("intimacy", "A long embrace in the rain."),
        ("hook", "Caught kissing under the portrait."),
        ("intimacy", "Touching lips for a second."),
    ],
)
def test_contact_is_refused_with_the_pg_rule(
    desk: Path, field: str, value: str
) -> None:
    with pytest.raises(PitchRefused) as refused:
        store_pitch(desk, 1, card(**{field: value}))

    assert PG_RULE in str(refused.value) and field in str(refused.value)
    assert stored_pitches(desk, 1) == []


def test_intimacy_none_is_fine(desk: Path) -> None:
    stored, _, _ = store_pitch(desk, 1, card(intimacy="none"))
    assert stored.pitch["intimacy"] == "none"


# --- carried from the previous approved pitch -----------------------------------------------------


def test_world_rules_and_the_device_carry_from_the_last_approved_pitch(
    desk: Path,
) -> None:
    store_pitch(desk, 1, card(world_rules=["Sota cannot feel a ghost or touch one."]))
    approve_pitch(desk, 1)
    raw = card(premise_device={"used": False, "why": "the bowl swap carries it"})
    raw.pop("world_rules")

    stored, notes, _ = store_pitch(desk, 2, raw)

    assert stored.pitch["world_rules"] == ["Sota cannot feel a ghost or touch one."]
    assert stored.pitch["premise_device"]["device"] == ["rain"]
    assert any(
        "carried from the previous episode's approved pitch: re-confirm" in n
        for n in notes
    )


def test_world_rules_come_from_the_series_memory_when_no_pitch_has_them(
    desk: Path,
) -> None:
    (desk / "api").mkdir(exist_ok=True)
    (desk / "api" / "memory-v1.json").write_text(
        json.dumps({"memory": {"canon": ["Hana sees ghosts; Sota never does."]}}),
        encoding="utf-8",
    )
    raw = card()
    raw.pop("world_rules")

    stored, notes, _ = store_pitch(desk, 1, raw)

    assert stored.pitch["world_rules"] == ["Hana sees ghosts; Sota never does."]
    assert any("series memory" in note for note in notes)


def test_episode_1_with_no_world_rules_anywhere_is_refused(desk: Path) -> None:
    raw = card()
    raw.pop("world_rules")
    with pytest.raises(PitchRefused, match="world_rules"):
        store_pitch(desk, 1, raw)


def test_the_premise_device_two_episodes_running_is_warned(desk: Path) -> None:
    store_pitch(
        desk, 1, card(premise_device={"device": "rain", "used": True, "why": "premise"})
    )
    approve_pitch(desk, 1)

    _, notes, _ = store_pitch(
        desk, 2, card(premise_device={"device": "rain", "used": True, "why": "again"})
    )
    _, quiet, _ = store_pitch(
        desk,
        3,
        card(
            premise_device={"device": "rain", "used": False, "why": "the hospital call"}
        ),
    )

    assert any("used in the previous episode too" in note for note in notes)
    assert not any("previous episode too" in note for note in quiet)


def test_a_restrained_expression_is_warned_not_refused(desk: Path) -> None:
    beats = [
        {
            "beat": "she sees them",
            "expression": "a cold smile, frozen",
            "delivery": "cold",
        }
    ]
    _, notes, written = store_pitch(desk, 1, card(emotional_beats=beats))
    assert written
    assert any("restrained" in note and "blank face" in note for note in notes)


# --- the yes ---------------------------------------------------------------------------------------


def test_approve_records_the_yes_and_an_edited_card_needs_a_new_one(desk: Path) -> None:
    store_pitch(desk, 1, card())
    assert approved_pitch(desk, 1) is None
    approve_pitch(desk, 1)
    assert approved_pitch(desk, 1).version == 1  # type: ignore[union-attr]

    store_pitch(desk, 1, card(ending="Jun-ho reaches for her."))
    assert approved_pitch(desk, 1) is None
    assert "changed after its yes" not in (
        pitch_gate_refusal(desk, episode=1, stage="the plates") or ""
    )
    approve_pitch(desk, 1)
    assert approved_pitch(desk, 1).version == 2  # type: ignore[union-attr]


def test_a_card_edited_by_hand_after_its_yes_needs_a_new_yes(desk: Path) -> None:
    stored, _, _ = store_pitch(desk, 1, card())
    approve_pitch(desk, 1)
    record = json.loads(stored.path.read_text(encoding="utf-8"))
    record["pitch"]["hook"] = "Something else entirely."
    stored.path.write_text(json.dumps(record), encoding="utf-8")

    assert approved_pitch(desk, 1) is None
    assert "changed after its yes" in (
        pitch_gate_refusal(desk, episode=1, stage="the plates") or ""
    )


def test_approve_with_no_card_says_how_to_store_one(desk: Path) -> None:
    with pytest.raises(RuntimeError, match="has no pitch card yet"):
        approve_pitch(desk, 1)


def test_the_cli_stores_prints_refuses_and_approves(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = desk / "bad.json"
    bad.write_text(json.dumps({"throughline": "One line."}), encoding="utf-8")
    good = desk / "good.json"
    good.write_text(json.dumps(card()), encoding="utf-8")

    assert (
        main(["pitch", "--desk", str(desk), "--episode", "1", "--file", str(bad)]) == 2
    )
    assert "Nothing was saved" in capsys.readouterr().err
    assert main(["pitch", "--desk", str(desk), "--file", str(good)]) == 0
    assert main(["approve", "--desk", str(desk), "--gate", "pitch"]) == 0
    assert "Pitch approved: episode 1, v1" in capsys.readouterr().out
    assert main(["pitch", "--desk", str(desk), "--episode", "1"]) == 0
    assert "v1, approved" in capsys.readouterr().out
    assert (
        main(["approve", "--desk", str(desk), "--gate", "plates", "--episode", "1"])
        == 2
    )


# --- the gate before plates and boards (new desks) -------------------------------------------------


@pytest.mark.usefixtures("pitch_gate")
@pytest.mark.parametrize("phase,route,stage", [
    ("ready_cast_enrol", CAST, "the plates"),
    ("ready_boards_enrol", BOARDS, "the boards"),
])  # fmt: skip
def test_step_draws_nothing_without_an_approved_pitch_on_a_new_desk(
    desk: Path, api: FakeApi, phase: str, route: str, stage: str
) -> None:
    set_phase(desk, phase, drawing_estimates=SHOWN_PRICES)

    with pytest.raises(RuntimeError) as stop:
        orchestrate.run_step(desk, confirm_spend=True)

    message = str(stop.value)
    assert message.startswith(f"Stopped before {stage}. Nothing was sent.")
    assert "fictora-produce pitch --desk" in message and "approve --desk" in message
    assert "--gate pitch --episode 1" in message
    assert not api.posted(route)
    assert api.calls == [] or all(call[0] == "GET" for call in api.calls)


@pytest.mark.usefixtures("pitch_gate")
def test_step_goes_on_to_the_price_once_the_pitch_has_its_yes(
    desk: Path, api: FakeApi
) -> None:
    store_pitch(desk, 1, card())
    approve_pitch(desk, 1)
    set_phase(desk, "ready_cast_enrol")

    result = orchestrate.run_step(desk)

    assert "about $" in result.message
    assert not api.posted(CAST)


@pytest.mark.usefixtures("pitch_gate")
def test_an_edited_pitch_stops_step_again(desk: Path, api: FakeApi) -> None:
    store_pitch(desk, 1, card())
    approve_pitch(desk, 1)
    store_pitch(desk, 1, card(hook="A new hook."))
    set_phase(desk, "ready_boards_enrol", drawing_estimates=SHOWN_PRICES)

    with pytest.raises(RuntimeError, match="pitch v2 has no yes yet"):
        orchestrate.run_step(desk, confirm_spend=True)
    assert not api.posted(BOARDS)


@pytest.mark.usefixtures("pitch_gate")
def test_a_legacy_desk_is_never_held_for_a_pitch(desk: Path, api: FakeApi) -> None:
    _legacy(desk)
    assert pitch_gate_refusal(desk, episode=1, stage="the plates") is None
    set_phase(desk, "ready_cast_enrol")
    api.routes[("POST", CAST)] = {"job_id": "job_cast"}
    api.jobs["job_cast"] = {"status": "completed"}

    result = orchestrate.run_step(desk)

    assert "Cast drawn" in result.message
    assert api.posted(CAST)


# --- the script checks (warnings, every desk) -------------------------------------------------------


def _spine(**beat_changes: Any) -> dict[str, Any]:
    spine = spine_fixture(episodes=3)
    for beat in spine["beats"]:
        beat.update(copy.deepcopy(beat_changes))
    return spine


def test_a_take_whose_first_beat_is_silent_is_warned() -> None:
    spine = spine_fixture(episodes=1)
    first = spine["beats"][0]
    second = copy.deepcopy(first)
    second.update(beat_id="beat_2", ordinal=2)
    first["dialogue_lines"] = []
    spine["beats"].append(second)

    lines = silent_first_beat_lines(spine, episode=1, take_count=1)

    assert lines and "take t1: its first beat (beat 1) has no line" in lines[0]
    assert (
        silent_first_beat_lines(spine_fixture(episodes=1), episode=1, take_count=1)
        == []
    )


@pytest.mark.parametrize(
    "words",
    ["twenty-six, adult woman", "sixteen", "in her late twenties", "thirty years old"],
)
def test_an_age_in_words_on_a_cast_card_is_warned(words: str) -> None:
    spine = spine_fixture(episodes=1)
    spine["cast"][0]["visual_brief"] = {"age": words}

    lines = word_age_lines(spine)

    assert (
        lines
        and "Hana: the age is written in words" in lines[0]
        and "digits" in lines[0]
    )


def test_an_age_in_digits_is_fine() -> None:
    spine = spine_fixture(episodes=1)
    spine["cast"][0]["visual_brief"] = {
        "age": "26, adult woman",
        "description": "twenty minutes late",
    }
    assert word_age_lines(spine) == []


def test_restraint_words_in_a_beat_are_warned() -> None:
    spine = _spine(motion_intent="Seo-yeon freezes, a cold smile")
    lines = restraint_beat_lines(spine, episode=1)
    assert lines and "freezes" in lines[0] and "cold smile" in lines[0]
    assert restraint_beat_lines(spine_fixture(), episode=1) == []


def test_an_emotional_beat_without_expression_or_delivery_on_the_story_is_warned() -> (
    None
):
    pitch = card(
        emotional_beats=[
            {
                "beat": "Hana wipes the counter in tears",
                "expression": "tears",
                "delivery": "through_tears",
            }
        ]
    )
    spine = spine_fixture(episodes=1)

    lines = emotional_beat_lines(spine, pitch, episode=1)

    assert lines and "no expression" in lines[0] and "no delivery" in lines[0]
    assert "--set delivery=through_tears" in lines[0]

    spine["beats"][0]["reaction_kind"] = "crying"
    spine["beats"][0]["motion_direction"]["delivery"] = "through_tears"
    assert emotional_beat_lines(spine, pitch, episode=1) == []


def test_the_premise_device_in_an_episode_whose_pitch_leaves_it_out_is_warned() -> None:
    pitch = card(
        premise_device={"device": "rain", "used": False, "why": "not this time"}
    )
    spine = _spine(
        motion_intent="Rain streams down the window as Hana wipes the counter"
    )

    lines = premise_device_lines(spine, pitch, episode=1)

    assert lines and "is in this episode's lines or beats" in lines[0]
    assert premise_device_lines(spine_fixture(), pitch, episode=1) == []


def test_the_premise_device_in_each_of_the_last_three_episodes_is_warned() -> None:
    pitch = card(premise_device={"device": "rain", "used": True, "why": "premise"})
    spine = _spine(motion_intent="Rain on the shutters")

    lines = premise_device_lines(spine, pitch, episode=3)

    assert any("each of the last 3 episodes (1–3)" in line for line in lines)
    spine["beats"][1]["motion_intent"] = "A dry night"
    assert premise_device_lines(spine, pitch, episode=3) == []


def test_the_script_gate_prints_the_pitch_and_its_world_rules(desk: Path) -> None:
    store_pitch(desk, 1, card())
    approve_pitch(desk, 1)

    text = orchestrate.script_gate_text(desk, spine_fixture(), episode=1)

    assert "Pitch card, episode 1 (v1, approved)" in text
    assert "World rules — check every line and beat against these:" in text
    assert "The gallery belongs to Seo-yeon's late mother." in text
    assert (
        "emotional beat" in text
    )  # the fixture's beats carry no expression or delivery


def test_no_pitch_prints_nothing_extra(desk: Path) -> None:
    assert script_pitch_lines(desk, spine_fixture(), episode=1) == []
    assert "Pitch card" not in orchestrate.script_gate_text(
        desk, spine_fixture(), episode=1
    )


def test_a_legacy_desk_gets_the_same_script_gate_without_a_pitch(desk: Path) -> None:
    before = orchestrate.script_gate_text(desk, spine_fixture(), episode=1)
    _legacy(desk)
    assert orchestrate.script_gate_text(desk, spine_fixture(), episode=1) == before


# --- every paid command waits for the newest pitch's yes (new desks) -----------------------------


def _paid_posts(api: FakeApi) -> list[str]:
    return [path for method, path, _, _ in api.calls if method != "GET"]


@pytest.mark.usefixtures("pitch_gate")
@pytest.mark.parametrize(
    "command,call",
    [
        ("redraw-board", lambda desk: ec.run_redraw_board(desk, episode=1, take_id="t1", cause="the lamp")),
        ("redraw-plate", lambda desk: ec.run_redraw_plate_with_note(desk, cast="Hana", note="narrow face")),
        ("film --confirm-spend", lambda desk: ec.run_film(desk, episode=1, confirm_spend=True)),
    ],
)  # fmt: skip
def test_paid_commands_send_nothing_while_the_newest_pitch_lacks_its_yes(
    desk: Path, api: FakeApi, command: str, call: Any
) -> None:
    store_pitch(desk, 1, card())
    approve_pitch(desk, 1)
    # Rewritten mid-episode, after the boards: the new card has no yes yet.
    store_pitch(desk, 1, card(ending="Jun-ho reaches for her."))

    with pytest.raises(RuntimeError) as stop:
        call(desk)

    message = str(stop.value)
    assert message.startswith(f"Stopped before `{command}`. Nothing was sent.")
    assert (
        "pitch v2 has no yes yet" in message and "--gate pitch --episode 1" in message
    )
    assert f"`{command}` again" in message
    assert _paid_posts(api) == []


@pytest.mark.usefixtures("pitch_gate")
def test_step_does_not_film_while_the_newest_pitch_lacks_its_yes(
    desk: Path, api: FakeApi
) -> None:
    store_pitch(desk, 1, card())
    set_phase(desk, "wait_spend")

    with pytest.raises(RuntimeError, match="Stopped before filming. Nothing was sent."):
        orchestrate.run_step(desk, confirm_spend=True)
    assert _paid_posts(api) == []


@pytest.mark.usefixtures("pitch_gate")
def test_pricing_a_film_is_never_held_for_the_pitch(desk: Path, api: FakeApi) -> None:
    store_pitch(desk, 1, card())
    try:
        ec.run_film(desk, episode=1, confirm_spend=False, out=io.StringIO())
    except Exception as exc:  # noqa: BLE001 - any other stop is fine; the pitch one is not
        assert "pitch" not in str(exc)


@pytest.mark.usefixtures("pitch_gate")
def test_a_legacy_desk_runs_paid_commands_without_a_pitch(desk: Path) -> None:
    _legacy(desk)
    for command in ("redraw-board", "redraw-plate", "film --confirm-spend"):
        ec._hold_for_pitch(desk, command, episode=1)  # nothing raised


@pytest.mark.usefixtures("pitch_gate")
def test_redraws_go_on_once_the_newest_pitch_has_its_yes(desk: Path) -> None:
    store_pitch(desk, 1, card())
    approve_pitch(desk, 1)
    ec._hold_for_pitch(desk, "redraw-board", episode=1)
    ec._hold_for_pitch(desk, "redraw-plate")


# --- the premise device in every language --------------------------------------------------------


def test_the_device_takes_a_list_of_words_and_a_single_string_still_works(
    desk: Path,
) -> None:
    listed, _, _ = store_pitch(
        desk,
        1,
        card(
            premise_device={"device": ["rain", "비", "雨"], "used": False, "why": "x"}
        ),
    )
    assert listed.pitch["premise_device"]["device"] == ["rain", "비", "雨"]
    single, _, _ = store_pitch(
        desk, 2, card(premise_device={"device": "rain", "used": False, "why": "x"})
    )
    assert single.pitch["premise_device"]["device"] == ["rain"]
    assert (
        parse_pitch_markdown(
            "## Premise device\ndevice: rain, 비, 雨\nused: no\nwhy: x\n"
        )["premise_device"]["device"]
        == "rain, 비, 雨"
    )
    split, _, _ = store_pitch(
        desk,
        3,
        card(premise_device={"device": "rain, 비, 雨", "used": False, "why": "x"}),
    )
    assert split.pitch["premise_device"]["device"] == ["rain", "비", "雨"]


def _korean_spine(spoken: str) -> dict[str, Any]:
    spine = spine_fixture(episodes=1, spoken_language="ko-KR")
    line = spine["beats"][0]["dialogue_lines"][0]
    line["text"] = "It's closing time."
    line["subtitle_text"] = "It's closing time."
    line["spoken_text"] = spoken
    return spine


def test_a_device_used_only_in_a_korean_spoken_line_is_caught() -> None:
    pitch = card(
        premise_device={"device": ["rain", "비"], "used": False, "why": "not this time"}
    )
    spine = _korean_spine("비가 그치면 문 닫아요.")

    lines = premise_device_lines(spine, pitch, episode=1)

    assert (
        lines
        and "rain / 비" in lines[0]
        and "is in this episode's lines or beats" in lines[0]
    )
    assert (
        premise_device_lines(_korean_spine("이제 문 닫아요."), pitch, episode=1) == []
    )


def test_a_device_in_a_japanese_spoken_line_in_three_episodes_running_is_caught() -> (
    None
):
    pitch = card(
        premise_device={"device": ["rain", "雨"], "used": True, "why": "premise"}
    )
    spine = spine_fixture(episodes=3, spoken_language="ja-JP")
    for beat in spine["beats"]:
        beat["dialogue_lines"][0]["spoken_text"] = "雨がやんだら閉めます。"

    lines = premise_device_lines(spine, pitch, episode=3)

    assert any("each of the last 3 episodes" in line for line in lines)
