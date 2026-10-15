"""The impact step asks the server which frames to punch in (L-20261014-7).

Every fight take printed ``[impact] skipped: no big impact on this take``: the
kit decided with its own copy of the word lists, and a fight's strikes ("Ren
elbows the guard") name none of them (the server also planned no cue at all
for a fight take; fixed in fictora-drama). Now the server decides
(``POST /v1/jobs/{take_job}/impact-plan``, the hosted episode's rules) on what
the kit measured, the editor's ``--impact-at`` hits go to it, and the kit only
measures and draws. Offline, the kit's copy plans it and honours the server's
``impact`` call in the take facts.
"""

from __future__ import annotations

import copy
import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from conftest import needs_ffmpeg

from creation.cli_post import _impact_hits
from creation.post import finish as finish_module
from creation.post.finish import api_impact_planner, run_finish
from creation.post.impact_post import (
    ImpactHit,
    ImpactPlanUnavailable,
    impact_plan_request,
    impact_style,
    parse_impact_at,
    plan_from_server_answer,
)
from creation.post.sfx import plan_from_take_facts
from test_post_finish import FACTS, fake_bed
from test_sfx_motion import _desk
from conftest import make_tone

#: A fight take's facts from the fixed server: the strike is a cue the server called a hit.
FIGHT_FACTS = copy.deepcopy(FACTS)
FIGHT_FACTS["take_facts"]["sfx_cues"] = [
    {
        "shot_index": 2,
        "sound": "Ren elbows the guard",
        "kind": "event",
        "start_seconds": 3.0,
        "duration_seconds": 1.2,
        "source": "impact",
        "impact": "hit",
    }
]


def _fight_desk(root: Path, *, box_at: float | None = 3.5) -> Path:
    desk = _desk(root, date(2026, 10, 14), box_at=box_at)
    (desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FIGHT_FACTS)
    )
    return desk


def _finish(desk: Path, **kw: Any):  # type: ignore[no-untyped-def]
    out = io.StringIO()
    kw.setdefault("cut_meter", lambda _take: ())
    result = run_finish(desk, sfx_render=lambda cue, target: make_tone(target, seconds=cue.seconds, freq=300),
                        bed_maker=fake_bed, facts_fetcher=lambda *a: None, stream=out, **kw)  # fmt: skip
    return result, out.getvalue()


def _impact(result):  # type: ignore[no-untyped-def]
    return next(s for s in result.steps if s.step == "impact")


# --- what the kit sends and reads ------------------------------------------------------------------


def test_impact_at_reads_seconds_and_an_optional_blast() -> None:
    assert parse_impact_at("2.4, 5.10:blast,7:HIT") == (
        ImpactHit(2.4),
        ImpactHit(5.1, "blast"),
        ImpactHit(7.0, "hit"),
    )
    with pytest.raises(ValueError, match="SECONDS or SECONDS:blast"):
        parse_impact_at("2.4s")


def test_the_request_carries_the_measurements_and_the_answer_is_the_server_s_plan() -> (
    None
):
    body = impact_plan_request(
        ((0.04, 0.6), (0.08, 9.0)),
        windows={2: (2.6, 5.0), 1: (0.0, 2.6)},
        cuts=(2.6,),
        speech=None,
        hits=(ImpactHit(3.2, "blast"),),
        take_seconds=5.0,
    )
    assert body["motion"] == [[0.04, 0.6], [0.08, 9.0]]
    assert [s["shot_index"] for s in body["shots"]] == [1, 2]
    assert body["speech"] is None and body["hits"] == [{"at_s": 3.2, "style": "blast"}]

    plan = plan_from_server_answer(
        {
            "moments": [
                {
                    "time_s": 3.5,
                    "until_s": 4.3,
                    "style": "hit",
                    "label": "Ren elbows the guard",
                    "source": "planned",
                }
            ],
            "skipped": ["Ren kicks (mid-line at 1.10s)"],
        }  # fmt: skip
    )
    assert [m.one_line() for m in plan.moments] == [
        "Ren elbows the guard: punch-in + shake at 3.50s"
    ]
    assert plan.skipped == ("Ren kicks (mid-line at 1.10s)",)
    with pytest.raises(ImpactPlanUnavailable):
        plan_from_server_answer({"error": "nope"})


def test_the_take_facts_carry_the_server_s_impact_call_and_it_wins() -> None:
    (cue,) = plan_from_take_facts(FIGHT_FACTS).cues
    assert cue.impact == "hit"
    assert impact_style(cue.sound, cue.kind) is None, (
        "the kit's word lists alone miss a fight's strike"
    )
    assert impact_style(cue.sound, cue.kind, cue.impact) == "hit"
    assert plan_from_take_facts(FACTS).cues[0].impact is None, (
        "an older server's facts carry no call"
    )


# --- in finish -------------------------------------------------------------------------------------


@needs_ffmpeg
def test_finish_draws_the_server_s_plan_and_sends_it_the_editor_s_hits(
    tmp_path: Path,
) -> None:
    desk = _fight_desk(tmp_path)
    sent: list[dict[str, Any]] = []

    def planner(body: dict[str, Any]) -> Any:
        sent.append(body)
        return plan_from_server_answer(
            {
                "moments": [
                    {
                        "time_s": 3.5,
                        "until_s": 4.3,
                        "style": "hit",
                        "label": "Ren elbows the guard",
                        "source": "planned",
                    }
                ],
                "skipped": [],
            }  # fmt: skip
        )

    result, _ = _finish(
        desk, impact_planner=lambda *a: planner, impact_at=(ImpactHit(1.2),)
    )

    impact = _impact(result)
    assert impact.status == "ran", impact.detail
    assert (
        impact.detail.startswith("server: ")
        and "Ren elbows the guard: punch-in + shake at 3.50s" in impact.detail
    )
    assert (
        impact.output is not None and impact.output.name == "take-ep01-t1-impact-v1.mp4"
    )
    (body,) = sent
    assert body["motion"], "the kit measured its copy of the take"
    assert body["hits"] == [{"at_s": 1.2, "style": "hit"}]
    assert body["take_seconds"] and body["take_seconds"] > 4


@needs_ffmpeg
def test_offline_a_fight_take_still_punches_in_on_the_server_s_call(
    tmp_path: Path,
) -> None:
    """The symptom: ``skipped: no big impact on this take`` on a fight take whose strike is a cue."""

    desk = _fight_desk(tmp_path)

    result, log = _finish(desk, impact_planner=lambda *a: None)

    impact = _impact(result)
    assert impact.status == "ran", impact.detail
    assert "no big impact on this take" not in impact.detail
    assert "Ren elbows the guard: punch-in + shake at 3.50s" in impact.detail
    assert "the kit's copy plans it" in log


@needs_ffmpeg
def test_an_unreachable_server_says_the_editor_s_hits_were_not_applied(
    tmp_path: Path,
) -> None:
    desk = _fight_desk(tmp_path)

    def down(_body: dict[str, Any]) -> Any:
        raise ImpactPlanUnavailable("HTTP 503")

    result, log = _finish(
        desk, impact_planner=lambda *a: down, impact_at=(ImpactHit(1.2),)
    )

    assert "!! --impact-at needs the server's plan (HTTP 503): not applied" in log
    assert _impact(result).status == "ran", "the kit's copy still plans the strike"


# --- review fixes --------------------------------------------------------------------------------------


def _answer(*moments: tuple[float, str]) -> Any:
    return plan_from_server_answer(
        {
            "moments": [
                {
                    "time_s": t,
                    "until_s": t + 0.8,
                    "style": "hit",
                    "label": label,
                    "source": "planned",
                }
                for t, label in moments
            ],
            "skipped": [],
        }  # fmt: skip
    )


def _facts_desk(
    root: Path, facts: dict[str, Any], *, box_at: float | None = 3.5
) -> Path:
    desk = _desk(root, date(2026, 10, 14), box_at=box_at)
    (desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(json.dumps(facts))
    return desk


def _never(*_a: Any) -> Any:
    raise AssertionError("the server was asked for a take that cannot need it")


@needs_ffmpeg
def test_the_kit_sends_no_speech_list_unless_it_has_real_windows(
    tmp_path: Path,
) -> None:
    """Stale facts (no speaking shot known) must not tell the server "nothing is spoken"."""

    quiet = copy.deepcopy(FIGHT_FACTS)
    for shot in quiet["take_facts"]["shots"]:
        shot["speaks"] = False
    sent: list[dict[str, Any]] = []
    desk = _facts_desk(tmp_path, quiet)

    _finish(desk, impact_planner=lambda *a: lambda body: sent.append(body) or _answer())

    (body,) = sent
    assert body["speech"] is None


@needs_ffmpeg
def test_a_punch_in_with_no_hit_sound_under_it_says_to_refresh_the_facts(
    tmp_path: Path,
) -> None:
    desk = _fight_desk(tmp_path)

    result, log = _finish(
        desk, impact_planner=lambda *a: lambda body: _answer((1.2, "Ren kicks"))
    )

    assert _impact(result).status == "ran"
    assert "!! Ren kicks at 1.20s is punched in but no hit sound is laid there" in log
    assert "take-facts --desk" in log and "--refresh" in log


@needs_ffmpeg
def test_stale_fight_facts_with_no_shot_skip_with_the_refresh_command(
    tmp_path: Path,
) -> None:
    stale = copy.deepcopy(FACTS)
    stale["take_facts"]["shots"] = []
    stale["take_facts"]["sfx_cues"] = []
    desk = _facts_desk(tmp_path, stale)

    result, log = _finish(desk, impact_planner=lambda *a: _never)

    impact = _impact(result)
    assert impact.status == "skipped" and "--refresh" in impact.detail
    assert "!! the saved take facts plan no shot" in log


@needs_ffmpeg
def test_a_take_with_no_fight_no_impact_and_no_hit_is_never_measured_or_sent(
    tmp_path: Path,
) -> None:
    drama = copy.deepcopy(FACTS)
    drama["take_facts"]["fight"] = False
    drama["take_facts"]["sfx_cues"][0]["sound"] = "a soft knock"
    desk = _facts_desk(tmp_path, drama)

    result, _ = _finish(desk, impact_planner=lambda *a: _never)

    assert (_impact(result).status, _impact(result).detail) == (
        "skipped",
        "no big impact on this take",
    )


@needs_ffmpeg
def test_more_hits_than_the_server_takes_sends_the_first_eight_and_says_so(
    tmp_path: Path,
) -> None:
    desk = _fight_desk(tmp_path)
    sent: list[dict[str, Any]] = []
    hits = tuple(ImpactHit(0.3 + 0.4 * i) for i in range(10))

    _, log = _finish(
        desk,
        impact_planner=lambda *a: lambda body: sent.append(body) or _answer(),
        impact_at=hits,
    )

    assert len(sent[0]["hits"]) == 8
    assert "!! 10 --impact-at hits: the server takes at most 8" in log
    with pytest.raises(SystemExit, match="at most 8"):
        _impact_hits([hits[:5], hits[5:]])


def test_a_malformed_answer_is_no_plan_not_a_crash() -> None:
    for moment in ({"until_s": 1.0, "style": "hit"}, {"time_s": "x", "until_s": 1.0, "style": "hit"},
                   {"time_s": 1.0, "until_s": 1.5, "style": "flash"}):  # fmt: skip
        with pytest.raises(ImpactPlanUnavailable):
            plan_from_server_answer({"moments": [moment]})


def test_a_desk_whose_spine_cannot_be_read_falls_back_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(finish_module, "take_job_id", lambda *a: "job_1")

    def broken(_desk: Path) -> str:
        raise ValueError("production.json is not JSON")

    monkeypatch.setattr(finish_module, "spine_id", broken)
    planner = api_impact_planner(tmp_path, 1, "t1")
    assert planner is not None
    with pytest.raises(ImpactPlanUnavailable, match="could not be opened"):
        planner({})


def test_kit_tests_never_reach_the_real_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """The autouse fixture: even a desk that names a take job never builds a real planner in tests."""

    monkeypatch.setattr(finish_module, "take_job_id", lambda *a: "job_1")
    assert api_impact_planner(Path("/nowhere"), 1, "t1") is not None, (
        "the real factory would call out"
    )
    assert finish_module.api_impact_planner(Path("/nowhere"), 1, "t1") is None


@needs_ffmpeg
def test_a_take_whose_cuts_were_not_measured_is_not_drawn(tmp_path: Path) -> None:
    """Moments are filmed-shot times: without measured cuts they could land off the action."""

    desk = _fight_desk(tmp_path)

    def no_cuts(_take: Path) -> tuple[float, ...]:
        raise RuntimeError("cut detector failed")

    result, _ = _finish(desk, impact_planner=lambda *a: _never, cut_meter=no_cuts)

    assert (_impact(result).status, _impact(result).detail) == (
        "skipped",
        "the take's cuts were not measured",
    )
