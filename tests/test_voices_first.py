"""Voices are decided first, by the human, on desks from 6 Oct 2026 (never left to a default).

Don't Look, Hana (about 5 h, three voices): the show began on the video model's
own voices, so Hana's thoughts, spoken in a kept voice, could never match the
voice she speaks in. Noodle24 ep 2 was filmed in other voices than ep 1 ($1.20).

- Plates and boards wait until every speaking voice has the human's yes (the
  film gate, asked before anything is drawn).
- A show on the video model's own voices is refused plates and boards, and
  ``voice-mode --set model`` and new thoughts are refused, while anyone thinks
  aloud or narrates.
- ``author`` writes episode 2 on only after ``--keep-voices``.

Desks created before 6 Oct 2026 keep their old behaviour (rules epoch).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from conftest import SHOWN_PRICES, set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation import voice_mode as voice_mode_mod
from creation.episode_commands import CommandStopped, voices_unconfirmed_stop
from creation.post import voice as voice_mod
from creation.rules_epoch import run_rules_epoch
from creation.voice_gate import thought_and_narration_names
from fake_api import FakeApi
from test_voice_mode import _server

pytestmark = pytest.mark.usefixtures("voice_gate")

CAST = "/v1/spines/sp1/cast/enrol"
BOARDS = "/v1/spines/sp1/boards/enrol"


def _legacy(desk: Path) -> Path:
    run_rules_epoch(desk, set_to="legacy", out=io.StringIO())
    return desk


def _show(api: FakeApi, monkeypatch: pytest.MonkeyPatch, *, mode: str) -> FakeApi:
    monkeypatch.setattr(voice_mod, "open_api", lambda _desk, _episode: api)
    monkeypatch.setattr(voice_mode_mod, "open_api", lambda _desk, _episode: api)
    _server(api, inferred=None, filmed=0)
    api.spine_doc["voice_mode"] = mode
    api.routes[("POST", CAST)] = {"job_id": "job_cast"}
    api.jobs["job_cast"] = {"status": "completed"}
    return api


def _hana_thinks(api: FakeApi) -> None:
    summary = api.spine_doc["episode_summaries"][0]
    summary["inner_voice"] = [
        {"cue_id": "iv1", "speaker_cast_id": "cast_hana", "line": "Don't look."}
    ]


# --- who is heard in a voice made after filming ----------------------------------------------------


def test_thoughts_name_their_character(api: FakeApi) -> None:
    assert thought_and_narration_names(api.spine_doc) == []
    _hana_thinks(api)
    assert thought_and_narration_names(api.spine_doc) == ["Hana"]


# --- plates and boards wait for the voices ---------------------------------------------------------


def test_a_locked_show_draws_no_plates_before_every_voice_has_a_yes(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _show(api, monkeypatch, mode="locked")
    set_phase(desk, "ready_cast_enrol", drawing_estimates=SHOWN_PRICES)

    with pytest.raises(
        RuntimeError, match="Stopped before the plates. Nothing was sent."
    ) as stop:
        orchestrate.run_step(desk, confirm_spend=True)

    assert "Hana" in str(stop.value) and "--keep" in str(stop.value)
    assert not api.posted(CAST)


def test_a_model_show_with_thoughts_draws_no_boards(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _show(api, monkeypatch, mode="model")
    _hana_thinks(api)
    set_phase(desk, "ready_boards_enrol", drawing_estimates=SHOWN_PRICES)

    with pytest.raises(RuntimeError, match="Stopped before the boards") as stop:
        orchestrate.run_step(desk, confirm_spend=True)

    assert "thoughts or narration (Hana)" in str(stop.value)
    assert "voice-mode --desk" in str(stop.value) and "--set locked" in str(stop.value)
    assert not api.posted(BOARDS)


def test_a_model_show_with_nobody_thinking_aloud_goes_on_to_the_price(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _show(api, monkeypatch, mode="model")
    set_phase(desk, "ready_cast_enrol")

    result = orchestrate.run_step(desk)

    assert "about $" in result.message
    assert not api.posted(CAST)


def test_a_legacy_desk_draws_plates_without_asking_for_voices(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _legacy(desk)
    _show(api, monkeypatch, mode="locked")
    set_phase(desk, "ready_cast_enrol")

    result = orchestrate.run_step(desk)

    assert result.phase == "wait_plates"
    assert api.posted(CAST)


# --- the video model's own voices are refused on a show with thoughts -----------------------------


def test_voice_mode_model_is_refused_while_someone_thinks_aloud(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _show(api, monkeypatch, mode="locked")
    _hana_thinks(api)

    with pytest.raises(
        RuntimeError, match="Not changed: the show stays on locked voices"
    ):
        voice_mode_mod.run_voice_mode(desk, set_to="model", out=io.StringIO())

    assert api.spine_doc["voice_mode"] == "locked"


def test_a_thought_is_refused_on_a_model_show(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _show(api, monkeypatch, mode="model")

    with pytest.raises(CommandStopped, match="Not added, nothing sent"):
        ec.run_inner_voice(
            desk, episode=1, cast="Hana", text="Don't look.", at=2.0, out=io.StringIO()
        )


# --- author waits for the human's yes on the voices -------------------------------------------------


def test_author_stops_until_the_voices_are_kept(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _show(api, monkeypatch, mode="locked")

    stop = voices_unconfirmed_stop(
        desk, api, api.spine_doc, episode=2, keep_voices=False
    )

    assert stop is not None and stop.startswith(
        "Voices first, nothing written: Voices for the next take: locked"
    )
    assert "--keep-voices" in stop
    assert (
        voices_unconfirmed_stop(desk, api, api.spine_doc, episode=2, keep_voices=True)
        is None
    )


def test_author_never_stops_a_legacy_desk(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _legacy(desk)
    _show(api, monkeypatch, mode="locked")

    assert (
        voices_unconfirmed_stop(desk, api, api.spine_doc, episode=2, keep_voices=False)
        is None
    )
