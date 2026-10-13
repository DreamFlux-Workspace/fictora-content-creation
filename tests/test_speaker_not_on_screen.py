"""SPEAKER NOT ON SCREEN: a line heard over a shot that shows other named people and not its speaker.

Founder decision (12 Oct 2026). Don't Look, Hana ep 6 (model voices): Sota's line rushed into shot 1,
which shows only Hana and the Cabin Woman, so the ghost mouthed it. Dead Heat ep 2 (locked voices): the
off-screen announcer's lines were mouthed by Moretti, the only face in the shot. Both caught by eye.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pytest
from conftest import SPINE, make_take, needs_ffmpeg

from creation.crowd_lines import beat_shouts
from creation.post import line_check as lc
from creation.post import speaker_screen as ss
from creation.post.finish import run_finish
from creation.post.line_check import ScriptLine, check_take_lines
from creation.post.whisper import Word
from test_post_finish import fake_bed, fake_sfx

HANA_LINE = "Who left the cabin door open again?"
SOTA_LINE = "I told you never to come back here."
NAMES = {
    "cast_hana": "Hana",
    "cast_sota": "Sota",
    "cast_cabin_woman": "Cabin Woman",
    "cast_moretti": "Moretti",
    "cast_announcer": "Announcer",
}


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


def facts(*shots: tuple[float, float, list[str], int]) -> dict:
    """Take facts with ``shots[].people`` (start, end, named cast ids, unnamed count)."""

    return {
        "take_facts": {
            "shots": [
                {
                    "shot_index": index,
                    "start_seconds": start,
                    "end_seconds": end,
                    "people": {
                        "count": len(named) + unnamed,
                        "named": named,
                        "unnamed": unnamed,
                    },
                }
                for index, (start, end, named, unnamed) in enumerate(shots, start=1)
            ]
        }
    }


#: Don't Look, Hana ep 6, take 1: shot 1 shows Hana and the Cabin Woman, shot 2 shows Sota.
HANA_FACTS = facts(
    (0.0, 7.0, ["cast_hana", "cast_cabin_woman"], 0), (7.0, 10.0, ["cast_sota"], 0)
)


def hana_lines(*, window: bool = False) -> list[ScriptLine]:
    return [
        ScriptLine(1, "l1", HANA_LINE, speaker="Hana", cast_id="cast_hana"),
        ScriptLine(
            2,
            "l2",
            SOTA_LINE,
            speaker="Sota",
            cast_id="cast_sota",
            shot_index=2 if window else None,
            window=(7.0, 10.0) if window else None,
        ),
    ]


def checked(
    lines: list[ScriptLine], heard: list[tuple[str, float, float]], take_facts: dict
) -> lc.LineCheck:
    check = check_take_lines(lines, words(*heard), take_id="t1")
    lc.add_speaker_check(check, take_facts, cast_names=NAMES)
    return check


# --- the check (model voices) -----------------------------------------------------------------------------


def test_hana_ep6_sota_heard_over_hana_and_the_cabin_woman_stops() -> None:
    # Sota's line rushed into shot 1 (4.9-6.9 s): only Hana and the Cabin Woman are on screen.
    check = checked(
        hana_lines(), [(HANA_LINE, 0.5, 3.0), (SOTA_LINE, 4.9, 6.9)], HANA_FACTS
    )
    assert not check.ok
    (fault,) = check.faults
    assert fault.kind == "speaker_off" and fault.line is not None
    assert fault.line.line_id == "l2"
    text = fault.describe()
    assert text.startswith('SPEAKER NOT ON SCREEN: line 2 (Sota) "I told you')
    assert "shot 1 (0.00-7.00s), which shows Hana, Cabin Woman but not Sota" in text
    assert fault.listen == (4.5, 7.3)


def test_hana_ep6_with_a_planned_window_is_also_a_wrong_shot() -> None:
    check = checked(
        hana_lines(window=True),
        [(HANA_LINE, 0.5, 3.0), (SOTA_LINE, 4.9, 6.9)],
        HANA_FACTS,
    )
    assert sorted(f.kind for f in check.faults) == ["speaker_off", "wrong_shot"]


def test_a_line_over_its_own_speaker_passes() -> None:
    check = checked(
        hana_lines(), [(HANA_LINE, 0.5, 3.0), (SOTA_LINE, 7.3, 9.5)], HANA_FACTS
    )
    assert check.ok, [f.describe() for f in check.faults]


def test_a_line_spilling_a_moment_before_its_speakers_cut_passes() -> None:
    # Sota starts 0.7 s before the cut to him and four of his seven words land in shot 1, but only the
    # first two are further than the slack from his shot: a cut a few frames off, not the wrong mouth.
    check = checked(
        hana_lines(), [(HANA_LINE, 0.5, 3.0), (SOTA_LINE, 6.3, 7.6)], HANA_FACTS
    )
    assert check.ok, [f.describe() for f in check.faults]


def test_a_shot_that_names_nobody_is_not_judged() -> None:
    empty = facts((0.0, 7.0, [], 1), (7.0, 10.0, ["cast_sota"], 0))
    check = checked(hana_lines(), [(SOTA_LINE, 4.9, 6.9)], empty)
    assert "speaker_off" not in [f.kind for f in check.faults]


def test_take_facts_without_people_say_the_check_did_not_run() -> None:
    check = checked(
        hana_lines(),
        [(HANA_LINE, 0.5, 3.0), (SOTA_LINE, 4.9, 6.9)],
        {
            "take_facts": {
                "shots": [{"shot_index": 1, "start_seconds": 0.0, "end_seconds": 10.0}]
            }
        },
    )
    assert check.ok and any(lc.SPEAKER_NOT_RUN in note for note in check.notes)


def test_an_off_screen_line_over_a_named_face_warns_with_the_likely_mouther() -> None:
    lines = [
        ScriptLine(
            1, "l1", SOTA_LINE, speaker="Sota", cast_id="cast_sota", off_screen=True
        )
    ]
    one_face = facts((0.0, 10.0, ["cast_hana"], 0))
    check = checked(lines, [(SOTA_LINE, 1.0, 3.0)], one_face)
    assert check.ok
    (warning,) = [n for n in check.notes if "heard off screen" in n]
    assert "Hana may mouth it" in warning and warning.startswith("!! line 1 (Sota)")


def test_an_off_screen_line_over_no_named_face_is_quiet() -> None:
    lines = [
        ScriptLine(
            1, "l1", SOTA_LINE, speaker="Sota", cast_id="cast_sota", off_screen=True
        )
    ]
    check = checked(lines, [(SOTA_LINE, 1.0, 3.0)], facts((0.0, 10.0, [], 0)))
    assert check.ok and not [n for n in check.notes if "mouth" in n]


SHOUT = "Get away from the door right now!"


@pytest.mark.parametrize(
    ("off_screen", "unnamed", "expected"),
    [
        (True, 0, "heard off screen"),
        (True, 2, "heard off screen"),
        (False, 0, "a background shout with nobody unnamed on screen"),
        (False, 2, None),
    ],
)
def test_background_shouts_warn_never_stop(
    off_screen: bool, unnamed: int, expected: str | None
) -> None:
    lines = [
        ScriptLine(1, "c1", SHOUT, speaker="Villager, background", cast_id="crowd:c1",
                   off_screen=off_screen)
    ]  # fmt: skip
    check = checked(
        lines, [(SHOUT, 1.0, 3.0)], facts((0.0, 10.0, ["cast_hana"], unnamed))
    )
    assert check.ok
    found = [n for n in check.notes if "may mouth it" in n]
    if expected is None:
        assert found == []
    else:
        (warning,) = found
        assert expected in warning and "Hana may mouth it" in warning


def test_the_crowd_off_screen_flag_is_read_and_its_absence_tolerated() -> None:
    beat = {
        "crowd_lines": [
            {"line_id": "c1", "text": "Charge!", "speaker": {"label": "soldier"}, "off_screen": True},
            {"line_id": "c2", "text": "Run!", "speaker": {"label": "soldier"}},
        ]
    }  # fmt: skip
    assert [s.off_screen for s in beat_shouts(beat)] == [True, False]
    assert lc.crowd_off_screen_ids({"beats": [beat]}) == {"c1"}
    lines = lc.script_lines_for_take(
        [{"line_id": "c2", "cast_id": "crowd:c2", "text": "Run!"}],
        {"take_facts": {"lines": [{"line_id": "c2", "off_screen": True}]}},
    )
    assert lines[0].off_screen and lines[0].cast_id == "crowd:c2"


# --- before filming -----------------------------------------------------------------------------------------


def _row_spine(*, off_screen: bool, drawn: list[str], speaker: str) -> dict:
    return {
        "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
        "cast": [{"cast_id": k, "name": v} for k, v in NAMES.items()],
        "beats_per_storyboard_set": [1],
        "beats": [
            {
                "episode_id": "episode_01",
                "ordinal": 1,
                "frame_id": "f1",
                "dialogue_lines": [
                    {
                        "line_id": "l1",
                        "cast_id": speaker,
                        "text": SOTA_LINE,
                        "off_screen": off_screen,
                    }
                ],
            }
        ],  # fmt: skip
        "frames": [
            {
                "frame_id": "f1",
                "episode_id": "episode_01",
                "ordinal": 1,
                "board_row": 1,
                "storyboard_group_id": "sb_episode_01_set01",
                "cast_refs": drawn,
            }
        ],
    }


def test_prefilm_warns_on_a_row_without_the_speaker_hana_case() -> None:
    spine = _row_spine(
        off_screen=False, drawn=["cast_hana", "cast_cabin_woman"], speaker="cast_sota"
    )
    (warning,) = ss.film_speaker_warnings(
        spine, episode=1, take_ids=["t1"], all_take_ids=["t1"]
    )
    assert warning.startswith('!! ep01 t1: Sota says "I told you')
    assert "board row 1, which draws Cabin Woman, Hana but not Sota" in warning
    assert "--off-screen" in warning and "warning only" in warning


def test_prefilm_warns_on_an_off_screen_line_over_one_face_dead_heat_case() -> None:
    spine = _row_spine(
        off_screen=True, drawn=["cast_moretti"], speaker="cast_announcer"
    )
    (warning,) = ss.film_speaker_warnings(
        spine, episode=1, take_ids=["t1"], all_take_ids=["t1"]
    )
    assert "Announcer (off screen)" in warning and "draws only Moretti" in warning
    assert "Moretti's mouth" in warning


@pytest.mark.parametrize(
    ("off_screen", "drawn", "speaker"),
    [
        (False, ["cast_sota", "cast_hana"], "cast_sota"),  # the speaker is drawn
        (
            True,
            ["cast_moretti", "cast_hana"],
            "cast_announcer",
        ),  # two faces: no single mouther
        (False, [], "cast_sota"),  # nobody drawn
    ],
)
def test_prefilm_is_quiet_when_the_line_sits_with_its_speaker(
    off_screen: bool, drawn: list[str], speaker: str
) -> None:
    spine = _row_spine(off_screen=off_screen, drawn=drawn, speaker=speaker)
    assert (
        ss.film_speaker_warnings(spine, episode=1, take_ids=["t1"], all_take_ids=["t1"])
        == []
    )


def test_the_film_price_prints_the_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_film_episode import ESTIMATE, VIDEO

    from creation import episode_commands, orchestrate
    from creation.episode_commands import run_film
    from creation.ops.floor import approve_board, init_series_desk
    from creation.production_state import ensure_production, save_production
    from fake_api import FakeApi, png_bytes, spine_fixture

    desk = init_series_desk(tmp_path, "Wrong Mouth", band="30s", episode_count=1)
    state = ensure_production(
        desk, prompt="p", preset_id="modern-romance", preset_version="2"
    )
    state.spine_id, state.episode_ordinal, state.phase = "sp1", 1, "complete"
    save_production(desk, state)
    board = desk / "ep01" / "boards" / "board.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_bytes(png_bytes())
    for take_id in ("t1", "t2"):
        approve_board(desk, episode=1, take_id=take_id, image=board)
    spine = spine_fixture(episodes=1)
    # Ren's line sits on row 1, which draws only Hana.
    spine["beats"][0]["frame_id"] = "frame_episode_01_01"
    fake = FakeApi(desk / "ep01" / "api", spine=spine)
    monkeypatch.setattr(orchestrate, "_open_run", lambda _d, _s: fake)
    monkeypatch.setattr(episode_commands, "_open_run", lambda _d, _s: fake)
    fake.routes[("POST", ESTIMATE)] = {
        "cost_estimate": {"total_usd": "1.20", "takes": 1}
    }

    text = run_film(desk, episode=1, take_id="t1")

    assert (
        '!! ep01 t1: Ren says "Not for me." on board row 1, which draws Hana but not Ren'
        in text
    )
    assert fake.posted(VIDEO) == []


# --- finish -----------------------------------------------------------------------------------------------

FINISH_SPINE = copy.deepcopy(SPINE)
FINISH_SPINE["cast"] = [{"cast_id": k, "name": v} for k, v in NAMES.items()]
FINISH_SPINE["beats"][0]["dialogue_lines"] = [
    {"line_id": "l1", "cast_id": "cast_hana", "text": HANA_LINE},
    {"line_id": "l2", "cast_id": "cast_sota", "text": SOTA_LINE},
]
#: The 5 s test take: shot 1 shows Hana and the Cabin Woman, shot 2 shows Sota.
FINISH_FACTS = facts(
    (0.0, 2.5, ["cast_hana", "cast_cabin_woman"], 0), (2.5, 5.0, ["cast_sota"], 0)
)


#: Hana's line, then Sota's rushed into the end of shot 1.
HANA_RUSHED = [(HANA_LINE, 0.1, 1.1), (SOTA_LINE, 1.2, 2.4)]


def _take(
    desk: Path,
    heard: list[tuple[str, float, float]],
    take_facts: dict,
    spine: dict = FINISH_SPINE,
) -> Path:
    (desk / "ep01" / "api" / "03_spine.json").write_text(json.dumps(spine))
    raw = make_take(
        desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
        tones=tuple((s, e, 440 + 220 * i) for i, (_t, s, e) in enumerate(heard)),
    )
    (desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(take_facts)
    )
    body = {
        "words": [
            {"word": w.text, "start": w.start, "end": w.end} for w in words(*heard)
        ]
    }
    (desk / "ep01" / "takes" / "take-ep01-t1-raw-v1-review-words-v1.json").write_text(
        json.dumps(body)
    )
    return raw


def _finish(desk: Path, **kw: object) -> tuple[object, str]:
    out = io.StringIO()
    result = run_finish(
        desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
        transcriber=lambda *a: (_ for _ in ()).throw(AssertionError("transcript is on the desk")),
        stream=out, **kw,
    )  # fmt: skip
    return result, out.getvalue()


def _locked(lines: list[tuple[str, str, float, float, bool]], shots: dict) -> dict:
    locked = copy.deepcopy(shots)
    locked["take_facts"]["soundtrack"] = {
        "mode": "target_audio",
        "reason": None,
        "lines": [
            {"line_id": i, "cast_id": c, "start_s": s, "end_s": e, "off_screen": off}
            for i, c, s, e, off in lines
        ],
    }
    return locked


@needs_ffmpeg
def test_finish_stops_on_the_hana_case_with_a_clip_and_the_refilm(
    post_desk: Path,
) -> None:
    # Sota's line rushed in at 1.2-2.4 s, inside shot 1 (Hana and the Cabin Woman): the ghost mouths it.
    _take(post_desk, HANA_RUSHED, FINISH_FACTS)
    result, printed = _finish(post_desk)
    assert result.line_faults and not result.complete and result.steps == []
    assert "SPEAKER NOT ON SCREEN: line 2 (Sota)" in result.stopped
    assert "which shows Hana, Cabin Woman but not Sota" in result.stopped
    assert (
        f"fictora-produce film --desk {post_desk} --episode 1 --take t1 --cause"
        in result.stopped
    )
    assert "--accept-line-mismatch t1" in result.stopped
    clip = post_desk / "ep01" / "takes" / "listen" / "ep01-t1-line2-speaker_off.wav"
    assert clip.exists() and str(clip) in result.stopped
    assert "!! STOPPED:" in printed


@needs_ffmpeg
def test_finish_delivers_the_hana_case_with_the_override(post_desk: Path) -> None:
    _take(post_desk, HANA_RUSHED, FINISH_FACTS)
    result, printed = _finish(post_desk, accept_line_mismatch=("t1",))
    assert not result.line_faults and result.steps
    assert "delivered anyway (--accept-line-mismatch t1)" in printed


@needs_ffmpeg
def test_finish_passes_lines_over_their_own_speakers(post_desk: Path) -> None:
    _take(post_desk, [(HANA_LINE, 0.2, 2.0), (SOTA_LINE, 2.8, 4.6)], FINISH_FACTS)
    result, printed = _finish(post_desk)
    assert not result.line_faults and result.steps, printed


@needs_ffmpeg
def test_dead_heat_locked_off_screen_announcer_over_one_face_warns(
    post_desk: Path,
) -> None:
    # Dead Heat ep 2: the announcer is heard off screen over a shot showing only Moretti.
    spine = copy.deepcopy(FINISH_SPINE)
    spine["beats"][0]["dialogue_lines"] = [
        {"line_id": "a1", "cast_id": "cast_announcer", "text": "Ladies and gentlemen, the final round.",
         "off_screen": True},
    ]  # fmt: skip
    take_facts = _locked(
        [("a1", "cast_announcer", 0.5, 3.0, True)],
        facts((0.0, 5.0, ["cast_moretti"], 0)),
    )
    _take(
        post_desk,
        [("Ladies and gentlemen, the final round.", 0.5, 3.0)],
        take_facts,
        spine,
    )
    result, printed = _finish(post_desk)
    assert not result.line_faults and result.steps, printed
    assert (
        "[lines] Checking each line plays over its own speaker (locked voices)"
        in printed
    )
    assert "Moretti may mouth it" in printed and "heard off screen" in printed
    assert "Comparing what the take says" not in printed


@needs_ffmpeg
def test_a_locked_take_with_the_speaker_off_the_shot_stops(post_desk: Path) -> None:
    take_facts = _locked(
        [("l1", "cast_hana", 0.2, 2.0, False), ("l2", "cast_sota", 0.4, 2.2, False)],
        FINISH_FACTS,
    )
    _take(post_desk, [(HANA_LINE, 0.2, 2.0)], take_facts)
    result, _printed = _finish(post_desk)
    assert result.line_faults and result.steps == []
    assert "a line plays over the wrong face" in result.stopped
    assert "locked voices" in result.stopped
    assert 'SPEAKER NOT ON SCREEN: line 2 (Sota) "I told you' in result.stopped
    assert "--accept-line-mismatch t1" in result.stopped
