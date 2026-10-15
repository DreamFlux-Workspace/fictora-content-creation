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

from creation.post.finish import run_finish
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
    (desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(json.dumps(FIGHT_FACTS))
    return desk


def _finish(desk: Path, **kw: Any):  # type: ignore[no-untyped-def]
    out = io.StringIO()
    result = run_finish(desk, sfx_render=lambda cue, target: make_tone(target, seconds=cue.seconds, freq=300),
                        bed_maker=fake_bed, facts_fetcher=lambda *a: None, cut_meter=lambda _take: (),
                        stream=out, **kw)  # fmt: skip
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


def test_the_request_carries_the_measurements_and_the_answer_is_the_server_s_plan() -> None:
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
        {"moments": [{"time_s": 3.5, "until_s": 4.3, "style": "hit", "label": "Ren elbows the guard",
                      "source": "planned"}], "skipped": ["Ren kicks (mid-line at 1.10s)"]}  # fmt: skip
    )
    assert [m.one_line() for m in plan.moments] == ["Ren elbows the guard: punch-in + shake at 3.50s"]
    assert plan.skipped == ("Ren kicks (mid-line at 1.10s)",)
    with pytest.raises(ImpactPlanUnavailable):
        plan_from_server_answer({"error": "nope"})


def test_the_take_facts_carry_the_server_s_impact_call_and_it_wins() -> None:
    (cue,) = plan_from_take_facts(FIGHT_FACTS).cues
    assert cue.impact == "hit"
    assert impact_style(cue.sound, cue.kind) is None, "the kit's word lists alone miss a fight's strike"
    assert impact_style(cue.sound, cue.kind, cue.impact) == "hit"
    assert plan_from_take_facts(FACTS).cues[0].impact is None, "an older server's facts carry no call"


# --- in finish -------------------------------------------------------------------------------------


@needs_ffmpeg
def test_finish_draws_the_server_s_plan_and_sends_it_the_editor_s_hits(tmp_path: Path) -> None:
    desk = _fight_desk(tmp_path)
    sent: list[dict[str, Any]] = []

    def planner(body: dict[str, Any]) -> Any:
        sent.append(body)
        return plan_from_server_answer(
            {"moments": [{"time_s": 3.5, "until_s": 4.3, "style": "hit", "label": "Ren elbows the guard",
                          "source": "planned"}], "skipped": []}  # fmt: skip
        )

    result, _ = _finish(desk, impact_planner=lambda *a: planner, impact_at=(ImpactHit(1.2),))

    impact = _impact(result)
    assert impact.status == "ran", impact.detail
    assert impact.detail.startswith("server: ") and "Ren elbows the guard: punch-in + shake at 3.50s" in impact.detail
    assert impact.output is not None and impact.output.name == "take-ep01-t1-impact-v1.mp4"
    (body,) = sent
    assert body["motion"], "the kit measured its copy of the take"
    assert body["hits"] == [{"at_s": 1.2, "style": "hit"}]
    assert body["take_seconds"] and body["take_seconds"] > 4


@needs_ffmpeg
def test_offline_a_fight_take_still_punches_in_on_the_server_s_call(tmp_path: Path) -> None:
    """The symptom: ``skipped: no big impact on this take`` on a fight take whose strike is a cue."""

    desk = _fight_desk(tmp_path)

    result, log = _finish(desk, impact_planner=lambda *a: None)

    impact = _impact(result)
    assert impact.status == "ran", impact.detail
    assert "no big impact on this take" not in impact.detail
    assert "Ren elbows the guard: punch-in + shake at 3.50s" in impact.detail
    assert "the kit's copy plans it" in log


@needs_ffmpeg
def test_an_unreachable_server_says_the_editor_s_hits_were_not_applied(tmp_path: Path) -> None:
    desk = _fight_desk(tmp_path)

    def down(_body: dict[str, Any]) -> Any:
        raise ImpactPlanUnavailable("HTTP 503")

    result, log = _finish(desk, impact_planner=lambda *a: down, impact_at=(ImpactHit(1.2),))

    assert "!! --impact-at needs the server's plan (HTTP 503): not applied" in log
    assert _impact(result).status == "ran", "the kit's copy still plans the strike"
