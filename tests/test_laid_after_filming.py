"""Lines the server laid after filming (fictora-drama #834, take facts ``laid_after_filming``).

On a model-voice take the server keeps a bodiless line (an off-screen PA, a voice-only character) out
of what the video model says and lays it after filming (Deep End ep 4: the boy mouthed the PA). The kit
must not lay it again, must not flag it, and captions it heard-not-seen. Facts without the field (an
older server) behave exactly as before.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import SPINE, make_take, make_tone, needs_ffmpeg

from creation.post import line_check as lc
from creation.post.hand import HandPlan, Placed
from creation.post.line_check import ScriptLine, check_take_lines
from creation.post.whisper import Word

KENJI = "Wait for me here."
PA = "The last train has left the station."
SHOUT = "Platform two is now closed to all passengers."
NAMES = {"cast_kenji": "Kenji", "cast_aya": "Aya", "cast_pa": "Station PA"}

#: The cast line the server laid (its id is the take's: scene, shot 2, line 0) and the crowd line (spine id).
LAID = [
    {
        "line_id": "scene_01_t1-s2-l0",
        "speaker": "Station PA",
        "start_seconds": 2.6,
        "end_seconds": 4.4,
    },
    {"line_id": "c1", "speaker": "Announcer", "start_seconds": 0.2, "end_seconds": 1.4},
]


def words(*spans: tuple[str, float, float]) -> list[Word]:
    out: list[Word] = []
    for text, start, end in spans:
        parts = text.split()
        step = (end - start) / len(parts)
        out += [
            Word(start + i * step, start + (i + 1) * step, p)
            for i, p in enumerate(parts)
        ]
    return out


def take_facts(*, laid: bool) -> dict[str, Any]:
    """Two shots: shot 1 shows Kenji, shot 2 shows Kenji too (the PA is never drawn)."""

    body: dict[str, Any] = {
        "shots": [
            {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 2.5,
             "people": {"count": 1, "named": ["cast_kenji"], "unnamed": 0}},
            {"shot_index": 2, "start_seconds": 2.5, "end_seconds": 5.0,
             "people": {"count": 1, "named": ["cast_kenji"], "unnamed": 0}},
        ],
        "lines": [
            {"line_id": "c1", "count": 1, "shot_index": 1, "start_seconds": 0.0, "end_seconds": 2.5,
             "cast_id": "crowd:c1", "off_screen": True},
            {"line_id": "l1", "count": 1, "shot_index": 1, "start_seconds": 0.0, "end_seconds": 2.5,
             "cast_id": "cast_kenji"},
            {"line_id": "l3", "count": 1, "shot_index": 2, "start_seconds": 2.5, "end_seconds": 5.0,
             "cast_id": "cast_pa"},
        ],
    }  # fmt: skip
    if laid:
        body["laid_after_filming"] = copy.deepcopy(LAID)
    return {"take_facts": body}


RAW_LINES = [
    {"line_id": "c1", "cast_id": "crowd:c1", "speaker": "Announcer, background", "text": SHOUT,
     "performed": SHOUT, "off_screen": "true"},
    {"line_id": "l1", "cast_id": "cast_kenji", "text": KENJI, "performed": KENJI},
    # Not flagged off screen on the spine: a voice-only character's line (the server lays it, no body).
    {"line_id": "l3", "cast_id": "cast_pa", "text": PA, "performed": PA},
]  # fmt: skip


def lines_for(facts: dict[str, Any]) -> list[ScriptLine]:
    return lc.script_lines_for_take(RAW_LINES, facts, cast_names=NAMES)


def checked(
    facts: dict[str, Any], heard: list[tuple[str, float, float]]
) -> lc.LineCheck:
    from creation.post.laid_lines import laid_windows

    check = check_take_lines(
        lines_for(facts), words(*heard), take_id="t1", laid_windows=laid_windows(facts)
    )
    lc.add_speaker_check(check, facts, cast_names=NAMES)
    return check


#: The PA's line heard in shot 1 (over Kenji, outside its planned shot 2), the shout over Kenji too.
HEARD = [(SHOUT, 0.2, 1.4), (KENJI, 1.5, 2.4), (PA, 0.3, 2.2)]


# --- reading the field ---------------------------------------------------------------------------------------


def test_the_field_is_read_and_its_absence_changes_nothing() -> None:
    from creation.post.laid_lines import laid_after_filming, laid_line_ids

    rows = laid_after_filming(take_facts(laid=True))
    assert [(r.line_id, r.speaker, r.start, r.end) for r in rows] == [
        ("scene_01_t1-s2-l0", "Station PA", 2.6, 4.4),
        ("c1", "Announcer", 0.2, 1.4),
    ]
    assert rows[0].shot_index == 2 and rows[1].shot_index is None
    assert laid_after_filming(take_facts(laid=False)) == ()
    assert laid_after_filming(None) == ()
    # The cast line is found by its speaker in its shot; the crowd line by its spine id.
    found = laid_line_ids(take_facts(laid=True), cast_names=NAMES)
    assert sorted(found) == ["c1", "l3"]
    assert laid_line_ids(take_facts(laid=False), cast_names=NAMES) == {}


def test_a_speaker_label_with_a_delivery_note_still_names_the_cast_line() -> None:
    from creation.post.laid_lines import laid_line_ids

    facts = take_facts(laid=True)
    facts["take_facts"]["laid_after_filming"][0]["speaker"] = "station pa (V.O.)"
    assert "l3" in laid_line_ids(facts, cast_names=NAMES)


def test_script_lines_mark_laid_lines_heard_off_screen() -> None:
    laid = {line.line_id: line for line in lines_for(take_facts(laid=True))}
    assert laid["l3"].laid and laid["l3"].off_screen
    assert laid["c1"].laid and not laid["l1"].laid
    before = {line.line_id: line for line in lines_for(take_facts(laid=False))}
    assert not before["l3"].laid and not before["l3"].off_screen


# --- (b) never flagged ----------------------------------------------------------------------------------------


def test_older_facts_still_flag_the_voice_only_line_as_today() -> None:
    check = checked(take_facts(laid=False), HEARD)
    kinds = sorted(f.kind for f in check.faults)
    assert kinds == ["speaker_off", "wrong_shot"], [f.describe() for f in check.faults]
    assert any("Kenji may mouth it" in n for n in check.notes)


def test_a_laid_line_is_never_a_wrong_shot_a_wrong_speaker_or_a_mouth_warning() -> None:
    check = checked(take_facts(laid=True), HEARD)
    assert check.ok, [f.describe() for f in check.faults]
    assert not [n for n in check.notes if "mouth" in n], check.notes


def test_words_in_a_laid_window_are_never_invented_speech() -> None:
    facts = take_facts(laid=True)
    # The laid PA line as the transcript garbled it: no script line claims these words.
    heard = [
        (SHOUT, 0.2, 1.4),
        (KENJI, 1.5, 2.4),
        (PA, 2.6, 4.4),
        ("final call for the night", 4.45, 4.9),
    ]
    laid_check = checked(facts, heard)
    assert "extra" not in [f.kind for f in laid_check.faults]
    assert any("laid after filming" in n for n in laid_check.notes)
    old = checked(take_facts(laid=False), heard)
    assert "extra" in [f.kind for f in old.faults], (
        "older facts: invented speech, as today"
    )


def test_a_laid_shout_is_part_of_what_the_take_was_asked() -> None:
    facts = take_facts(laid=True)
    facts["take_facts"]["lines"] = [
        r for r in facts["take_facts"]["lines"] if r["line_id"] != "c1"
    ]
    assert "c1" in lc.shouts_asked_of(facts)
    assert "c1" not in lc.shouts_asked_of(
        take_facts(laid=False)
        | {"take_facts": facts["take_facts"] | {"laid_after_filming": []}}
    )


# --- (c) captioned heard-not-seen ---------------------------------------------------------------------------


def _caption_spine() -> dict[str, Any]:
    spine = copy.deepcopy(SPINE)
    spine["cast"].append(
        {
            "cast_id": "cast_pa",
            "name": "Station PA",
            "voice_brief": {"provider_voice": "Bill"},
        }
    )
    spine["beats"][0]["dialogue_lines"].append(
        {"line_id": "l3", "cast_id": "cast_pa", "text": PA}
    )
    return spine


def test_a_laid_line_is_captioned_in_italic() -> None:
    from creation.post.finish import caption_italic_overrides

    overrides, notes = caption_italic_overrides(_caption_spine(), take_facts(laid=True))
    assert overrides.get("l3") is True and overrides.get("c1") is True
    assert any("l3: italic (laid by the server after filming" in n for n in notes)
    before, _ = caption_italic_overrides(_caption_spine(), take_facts(laid=False))
    assert "l3" not in before


# --- (a) never laid twice -----------------------------------------------------------------------------------


def _desk(post_desk: Path, *, laid: bool) -> None:
    spine = _caption_spine()
    spine["beats"][0]["crowd_lines"] = [
        {
            "line_id": "c1",
            "text": SHOUT,
            "speaker": {"label": "announcer"},
            "off_screen": True,
        }
    ]
    (post_desk / "ep01" / "api" / "03_spine.json").write_text(json.dumps(spine))
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(take_facts(laid=laid))
    )


def _voice(post_desk: Path, name: str, *, cast_id: str, line: str) -> Path:
    path = post_desk / "ep01" / "voices" / f"{name}.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    path.with_suffix(".json").write_text(json.dumps({"line": line, "cast_id": cast_id}))
    return path


def _guard(post_desk: Path, laid: bool, hand: HandPlan) -> tuple[list[str], list[str]]:
    from creation.post.laid_lines import finish_laid_guard

    facts = json.loads(
        (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").read_text()
    )
    return finish_laid_guard(
        post_desk,
        episode=1,
        take_id="t1",
        facts=facts,
        mutes=hand.mutes,
        voices=hand.voices,
    )


def test_finish_voice_refuses_a_second_lay_of_a_laid_line(post_desk: Path) -> None:
    _desk(post_desk, laid=True)
    pa = _voice(post_desk, "voice-ep01-pa-v1", cast_id="cast_pa", line=PA)
    refusals, _ = _guard(post_desk, True, HandPlan(voices=((Placed(pa, 2.6), 1.8),)))
    (refusal,) = refusals
    assert "server already laid it on this take after filming" in refusal
    assert f'"{PA}"' in refusal and "--mute 2.60-4.40" in refusal
    # The PA's words voiced by someone else are still that line.
    kenji = _voice(post_desk, "voice-ep01-kenji-v1", cast_id="cast_kenji", line=SHOUT)
    assert _guard(post_desk, True, HandPlan(voices=((Placed(kenji, 3.0), 1.0),)))[0]


def test_a_mute_over_the_laid_line_makes_the_hand_line_a_replacement(
    post_desk: Path,
) -> None:
    _desk(post_desk, laid=True)
    pa = _voice(post_desk, "voice-ep01-pa-v1", cast_id="cast_pa", line=PA)
    hand = HandPlan(mutes=((2.5, 4.5),), voices=((Placed(pa, 2.6), 1.8),))
    assert _guard(post_desk, True, hand) == ([], [])


def test_a_mute_alone_over_a_laid_line_warns(post_desk: Path) -> None:
    _desk(post_desk, laid=True)
    _refusals, warnings = _guard(post_desk, True, HandPlan(mutes=((2.0, 3.0),)))
    (warning,) = warnings
    assert (
        warning.startswith("!! --mute 2.00-3.00 silences Station PA's line")
        and PA in warning
    )


def test_an_unrelated_hand_line_and_an_older_server_pass(post_desk: Path) -> None:
    _desk(post_desk, laid=True)
    kenji = _voice(post_desk, "voice-ep01-kenji-v1", cast_id="cast_kenji", line=KENJI)
    assert _guard(post_desk, True, HandPlan(voices=((Placed(kenji, 1.5), 0.9),))) == (
        [],
        [],
    )
    _desk(post_desk, laid=False)
    pa = _voice(post_desk, "voice-ep01-pa-v1", cast_id="cast_pa", line=PA)
    assert _guard(
        post_desk,
        False,
        HandPlan(mutes=((2.0, 3.0),), voices=((Placed(pa, 2.6), 1.8),)),
    ) == ([], [])


class _Api:
    def __init__(self, spine: dict[str, Any]) -> None:
        self.spine_body, self.client = spine, self

    def close(self) -> None:
        pass

    def spine(self, spine_id: str) -> dict[str, Any]:
        return {"spine": copy.deepcopy(self.spine_body)}

    def save(self, name: str, payload: Any) -> None:
        pass


class _NoAudio:
    def voice_line(self, **kw: Any) -> dict[str, Any]:
        raise AssertionError("nothing is spent on a line the server already laid")


def test_voice_line_refuses_a_laid_line_before_spending(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation.post import handmade

    _desk(post_desk, laid=True)
    monkeypatch.setattr(
        handmade, "open_api", lambda desk, episode: _Api(_caption_spine())
    )
    with pytest.raises(
        ValueError, match="already laid on ep01 t1 by the server after filming"
    ):
        handmade.run_voice_line(
            post_desk, cast="Station PA", text=PA, audio=_NoAudio(), out=io.StringIO()
        )


def test_voice_line_is_untouched_on_an_older_server(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation.post import handmade
    from creation.post.laid_lines import laid_voice_line_refusal

    _desk(post_desk, laid=False)
    assert (
        laid_voice_line_refusal(
            post_desk, episode=1, cast_id="cast_pa", name="Station PA", text=PA
        )
        == ""
    )
    _desk(post_desk, laid=True)
    assert (
        laid_voice_line_refusal(
            post_desk, episode=1, cast_id="cast_kenji", name="Kenji", text=KENJI
        )
        == ""
    )
    monkeypatch.setattr(
        handmade, "open_api", lambda desk, episode: _Api(_caption_spine())
    )
    with pytest.raises(AssertionError, match="nothing is spent"):
        # --replace-laid goes on to the server (the fake refuses to be called, proving it was reached).
        handmade.run_voice_line(
            post_desk,
            cast="Station PA",
            text=PA,
            audio=_NoAudio(),
            out=io.StringIO(),
            replace_laid=True,
        )


# --- the take's timeline ------------------------------------------------------------------------------------


def test_a_cut_head_moves_the_laid_lines_with_the_rest() -> None:
    from creation.post.take_timeline import shift_take_facts

    moved = shift_take_facts(take_facts(laid=True), 0.5)
    assert [
        (r["start_seconds"], r["end_seconds"])
        for r in moved["take_facts"]["laid_after_filming"]
    ] == [
        (2.1, 3.9),
        (0.0, 0.9),
    ]
    assert (
        "laid_after_filming"
        not in shift_take_facts(take_facts(laid=False), 0.5)["take_facts"]
    )


def test_voice_fx_notes_a_range_over_a_laid_line(post_desk: Path) -> None:
    from creation.post.laid_lines import voice_fx_laid_notes

    _desk(post_desk, laid=True)
    take = post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4"
    (note,) = voice_fx_laid_notes(post_desk, episode=1, file=take, ranges=[(2.5, 4.5)])
    assert "Station PA at 2.60-4.40s was laid by the server after filming" in note
    assert (
        voice_fx_laid_notes(post_desk, episode=1, file=take, ranges=[(4.6, 4.9)]) == []
    )


@needs_ffmpeg
def test_finish_stops_before_making_anything_on_a_second_lay(post_desk: Path) -> None:
    from creation.post.finish import run_finish
    from test_post_finish import fake_bed, fake_sfx

    _desk(post_desk, laid=True)
    make_take(
        post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
        tones=((0.2, 1.4, 440), (2.6, 4.4, 660)),
    )
    pa = make_tone(
        post_desk / "ep01" / "voices" / "voice-ep01-pa-v1.mp3",
        seconds=1.5,
        freq=550,
        volume=0.3,
    )
    pa.with_suffix(".json").write_text(json.dumps({"line": PA, "cast_id": "cast_pa"}))
    with pytest.raises(
        ValueError, match="server already laid it on this take after filming"
    ):
        run_finish(
            post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
            voices=(Placed(pa, 2.6),), stream=io.StringIO(),
        )  # fmt: skip
    assert not list((post_desk / "ep01" / "takes").glob("take-ep01-t1-voice-*.mp4"))
