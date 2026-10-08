"""Show voices: locked or the video model's own, one choice per show (founder decision, 2026-10-05).

A continuing show filmed before option C changed voice actor mid-season when its
next episode filmed in locked voices. The server now keeps the choice on the
show (``voice_mode``); the kit offers it at ``start``, reads and sets it with
``voice-mode``, prints it on ``step``, the estimate and ``film``, and holds
filming for the Voices gate only on a ``locked`` show.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest

from conftest import needs_ffmpeg, set_phase
from creation import orchestrate
from creation.cli_config import add_production_config_args, config_from_args
from creation.cli_produce import main as produce_main
from creation.episode_commands import CommandStopped, run_film
from creation.post import voice as voice_mod
from creation.production_config import load_production_config, save_production_config
from creation.voice_gate import film_refusal, gate_text
from creation import voice_mode as voice_mode_mod
from creation.voice_mode import (
    MODEL_GATE_SKIPPED,
    send_desk_choice,
    set_show_voices,
    show_voices,
)
from fake_api import FakeApi
from test_voice_gate import VIDEO, _board_yes, _ready_to_film

pytestmark = pytest.mark.usefixtures("voice_gate")

ROUTE = "/v1/spines/sp1/voice-mode"
NOTICE = (
    "Earlier episodes keep their voices; new takes will use the video model's own voices, "
    "from each character's voice description."
)


def _server(
    api: FakeApi, *, inferred: str | None = "model", filmed: int = 6
) -> FakeApi:
    """The voice-mode routes as fictora-drama answers them: stored, else inferred from filmed takes, else locked."""

    hana, ren = api.spine_doc["cast"]
    hana["voice_brief"] = {"provider_voice": "Aria"}
    ren["voice_brief"] = {"provider_voice": "Liam"}

    def read(_m: str, _p: str, _body: Any) -> dict[str, Any]:
        stored = api.spine_doc.get("voice_mode")
        if stored:
            mode, source = stored, "show"
        elif filmed and inferred:
            mode, source = inferred, "inferred"
        else:
            mode, source = "locked", "default"
        return {
            "spine_id": "sp1",
            "spine_version": api.spine_doc["spine_version"],
            "voice_mode": mode,
            "source": source,
            "filmed_takes": filmed,
            "model_voice_takes": filmed if inferred == "model" else 0,
        }

    def write(_m: str, _p: str, body: dict[str, Any] | None) -> Any:
        assert body is not None
        if body["spine_version"] != api.spine_doc["spine_version"]:
            return 409, {"error": {"code": "spine_version_conflict"}}
        before = read("GET", ROUTE, None)
        api.spine_doc["voice_mode"] = body["voice_mode"]
        api.spine_doc["spine_version"] += "+"
        changed = before["voice_mode"] != body["voice_mode"]
        return 200, {
            "spine_id": "sp1",
            "spine_version": api.spine_doc["spine_version"],
            "previous_voice_mode": before["voice_mode"],
            "previous_source": before["source"],
            "voice_mode": body["voice_mode"],
            "changed": changed,
            "applied": True,
            "filmed_takes": filmed,
            "notice": NOTICE
            if changed and filmed and body["voice_mode"] == "model"
            else None,
        }

    api.routes[("GET", ROUTE)] = read
    api.routes[("POST", ROUTE)] = write
    return api


@pytest.fixture
def model_show(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """Beach Court After Dark: earlier episodes filmed with the model's own voices, no choice stored yet."""

    monkeypatch.setattr(voice_mod, "open_api", lambda _desk, _episode: api)
    monkeypatch.setattr(voice_mode_mod, "open_api", lambda _desk, _episode: api)
    return _server(api, inferred="model")


@pytest.fixture
def new_show(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    monkeypatch.setattr(voice_mod, "open_api", lambda _desk, _episode: api)
    monkeypatch.setattr(voice_mode_mod, "open_api", lambda _desk, _episode: api)
    return _server(api, inferred=None, filmed=0)


# --- start --voice-mode ------------------------------------------------------------------------------


def test_start_takes_a_voice_mode_and_keeps_it_on_the_desk(tmp_path: Path) -> None:
    import argparse

    parser = argparse.ArgumentParser()
    add_production_config_args(parser)

    assert config_from_args(parser.parse_args([])).voice_mode is None
    assert (
        config_from_args(parser.parse_args(["--voice-mode", "model"])).voice_mode
        == "model"
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["--voice-mode", "native"])


def test_the_desk_choice_is_sent_once_the_story_exists_and_never_over_a_stored_one(
    desk: Path, new_show: FakeApi
) -> None:
    config = load_production_config(desk)
    config.voice_mode = "model"
    save_production_config(desk, config)
    err = io.StringIO()

    send_desk_choice(desk, new_show, new_show.spine("sp1"), out=err)
    send_desk_choice(desk, new_show, new_show.spine("sp1"), out=err)

    assert [body["voice_mode"] for body in new_show.posted(ROUTE)] == ["model"]
    assert new_show.spine_doc["voice_mode"] == "model"
    assert (
        "Voice mode `model` (from start --voice-mode) is now set for this show."
        in err.getvalue()
    )
    # A choice made since (in the app) is never overwritten by the desk.
    new_show.spine_doc["voice_mode"] = "locked"
    send_desk_choice(desk, new_show, new_show.spine("sp1"), out=err)
    assert len(new_show.posted(ROUTE)) == 1


def test_no_desk_choice_sends_nothing(desk: Path, new_show: FakeApi) -> None:
    send_desk_choice(desk, new_show, new_show.spine("sp1"))

    assert new_show.posted(ROUTE) == []


# --- reading the server ------------------------------------------------------------------------------


def test_the_server_answer_and_why(model_show: FakeApi) -> None:
    voices = show_voices(model_show, model_show.spine_doc)

    assert (
        voices.mode,
        voices.source,
        voices.filmed_takes,
        voices.model_voice_takes,
    ) == ("model", "inferred", 6, 6)
    assert voices.line() == (
        "Voices for the next take: the video model's own voices, from each character's voice description "
        "on the cast card (the voices this show's earlier episodes were filmed with)."
    )


def test_an_older_server_reads_as_locked_and_says_so(api: FakeApi) -> None:
    voices = show_voices(api, api.spine_doc)

    assert (voices.mode, voices.source) == ("locked", "older-server")
    assert "older deploy" in voices.line()
    with pytest.raises(RuntimeError, match="no voice-mode route yet"):
        api.routes[("POST", ROUTE)] = lambda *_: (404, {"detail": "Not Found"})
        set_show_voices(api, api.spine_doc, "model")


def test_a_stale_version_is_read_again_once(model_show: FakeApi) -> None:
    stale = copy.deepcopy(model_show.spine_doc)
    model_show.spine_doc["spine_version"] += "+newer"

    answer = set_show_voices(model_show, stale, "model")

    assert answer["applied"] is True
    assert [b["spine_version"] for b in model_show.posted(ROUTE)] == [
        stale["spine_version"],
        stale["spine_version"] + "+newer",
    ]


# --- voice-mode --desk D [--set] ---------------------------------------------------------------------


def test_voice_mode_prints_the_shows_voices_and_how_to_change_them(
    desk: Path, model_show: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert produce_main(["voice-mode", "--desk", str(desk)]) == 0

    out = capsys.readouterr().out
    assert "Voices for the next take: the video model's own voices" in out
    assert (
        "Filmed so far: 6 take(s), 6 of them with the video model's own voices." in out
    )
    assert "Voices gate: skipped" in out
    assert f"fictora-produce voice-mode --desk {desk} --set locked" in out
    assert model_show.posted(ROUTE) == []


def test_keeping_the_earlier_voices_stores_model_and_prints_no_gate(
    desk: Path, model_show: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert produce_main(["voice-mode", "--desk", str(desk), "--set", "model"]) == 0

    out = capsys.readouterr().out
    assert model_show.spine_doc["voice_mode"] == "model"
    assert "now stored for this show (new takes already used it)" in out
    assert "(set for this show)" in out
    assert load_production_config(desk).voice_mode == "model"


def test_switching_to_locked_prints_the_servers_notice_and_the_gate(
    desk: Path, model_show: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    model_show.routes[("POST", ROUTE)] = _with_notice(
        model_show.routes[("POST", ROUTE)]
    )

    assert produce_main(["voice-mode", "--desk", str(desk), "--set", "locked"]) == 0

    out = capsys.readouterr().out
    assert "Voice mode set to `locked` for this show." in out
    assert "Earlier episodes keep their voices; new takes will use locked voices" in out
    assert (
        f"Voices gate: applies (hear and keep every speaking voice before filming: fictora-produce voice --desk {desk} --list)"
        in out
    )


def _with_notice(write: Any) -> Any:
    def wrapped(method: str, path: str, body: Any) -> Any:
        status, answer = write(method, path, body)
        if answer.get("changed") and body["voice_mode"] == "locked":
            answer["notice"] = (
                "Earlier episodes keep their voices; new takes will use locked voices "
                "(each character's voice on their cast card)."
            )
        return status, answer

    return wrapped


# --- the Voices gate only on a locked show -----------------------------------------------------------


def test_on_a_model_show_the_gate_is_skipped_and_filming_is_not_held(
    desk: Path, model_show: FakeApi
) -> None:
    spine = model_show.spine("sp1")

    text = gate_text(desk, spine, episode=1, run=model_show)

    assert "Voices for the next take: the video model's own voices" in text
    assert MODEL_GATE_SKIPPED in text
    assert "needs a yes" not in text
    assert film_refusal(desk, spine, episode=1, run=model_show) is None


def test_on_a_locked_show_the_gate_still_holds_filming(
    desk: Path, model_show: FakeApi
) -> None:
    model_show.spine_doc["voice_mode"] = "locked"
    spine = model_show.spine("sp1")

    text = gate_text(desk, spine, episode=1, run=model_show)
    refused = film_refusal(desk, spine, episode=1, run=model_show)

    assert (
        text.startswith("Voices for the next take: locked voices")
        and "needs a yes" in text
    )
    assert refused is not None and refused.startswith(
        "Stopped before filming. Nothing was sent."
    )


def test_a_new_show_is_locked_and_gated(desk: Path, new_show: FakeApi) -> None:
    refused = film_refusal(desk, new_show.spine("sp1"), episode=1, run=new_show)

    assert refused is not None and "Hana (Aria), Ren (Liam)" in refused


def test_voice_list_on_a_model_show_says_the_gate_is_skipped(
    desk: Path, model_show: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert produce_main(["voice", "--desk", str(desk), "--list"]) == 0

    out = capsys.readouterr().out
    assert MODEL_GATE_SKIPPED in out and "needs a yes" not in out


def test_step_films_a_model_show_with_no_voice_yes(
    desk: Path, model_show: FakeApi
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(model_show)

    orchestrate.run_step(desk, confirm_spend=True)

    assert len(model_show.posted(VIDEO)) == 1
    assert "voice_approvals" not in model_show.spine_doc


# --- step, the estimate and film say which voices ----------------------------------------------------


def test_the_estimate_says_which_voices_the_take_will_use(
    desk: Path, model_show: FakeApi
) -> None:
    set_phase(desk, "ready_estimate")
    model_show.routes[("POST", "/v1/spines/sp1/batches/estimate")] = {
        "cost_estimate": {"total_usd": "1.20", "priced_on": "2026-10-05", "takes": 1}
    }

    text = orchestrate.run_step(desk).message

    assert "Voices for the next take: the video model's own voices" in text
    assert "No Voices gate: there is no locked voice to hear." in text
    assert "VOICES NOT APPROVED" not in text


def test_film_prices_and_films_in_the_shows_voices(
    desk: Path, model_show: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _board_yes(desk)
    set_phase(desk, "complete")
    model_show.routes[("POST", "/v1/spines/sp1/batches/estimate")] = {
        "cost_estimate": {"total_usd": "1.20", "priced_on": "2026-10-05", "takes": 1}
    }
    _ready_to_film(model_show)

    priced = run_film(
        desk, episode=1, cause="re-film in the voices of earlier episodes"
    )
    assert "Voices for the next take: the video model's own voices" in priced

    run_film(
        desk,
        episode=1,
        cause="re-film in the voices of earlier episodes",
        confirm_spend=True,
    )
    assert len(model_show.posted(VIDEO)) == 1
    assert (
        "Voices for the next take: the video model's own voices"
        in capsys.readouterr().out
    )


def test_every_kit_film_says_finish_lays_the_theme_on_model_and_locked_shows(
    desk: Path, model_show: FakeApi
) -> None:
    """The kit lays the show's theme in finish: it always says so (the server decides per show).

    A model-voice take is filmed with no music in it; a locked show's takes
    carry the harness's music unless the show's music lock is ``finish``
    (fictora-drama show music lock; L-20261005-2).
    """

    _board_yes(desk)
    set_phase(desk, "complete", film_estimates={"ep01": 1.2})
    _ready_to_film(model_show)

    run_film(desk, episode=1, confirm_spend=True)
    assert model_show.posted(VIDEO)[-1]["music_by_finish"] is True

    model_show.spine_doc["voice_mode"] = "locked"
    model_show.spine_doc["voice_approvals"] = [
        {
            "cast_id": c,
            "provider_voice": v,
            "how": "kept",
            "approved_at": "2026-10-05T12:00:00Z",
        }
        for c, v in (("cast_hana", "Aria"), ("cast_ren", "Liam"))
    ]
    set_phase(desk, "complete", film_estimates={"ep01": 1.2})
    run_film(desk, episode=1, cause="locked re-film", confirm_spend=True)
    assert model_show.posted(VIDEO)[-1]["music_by_finish"] is True


def test_step_films_a_model_show_with_music_by_finish(
    desk: Path, model_show: FakeApi
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(model_show)

    orchestrate.run_step(desk, confirm_spend=True)

    assert model_show.posted(VIDEO)[-1]["music_by_finish"] is True


def test_film_on_a_locked_show_is_still_held_for_the_voices(
    desk: Path, model_show: FakeApi
) -> None:
    model_show.spine_doc["voice_mode"] = "locked"
    _board_yes(desk)
    set_phase(desk, "complete", film_estimates={"ep01": 1.2})
    _ready_to_film(model_show)

    with pytest.raises(CommandStopped, match="--keep-all"):
        run_film(desk, episode=1, confirm_spend=True)
    assert model_show.posted(VIDEO) == []


# --- finish lays the show's music on a model-voice take ------------------------------------------------


@needs_ffmpeg
def test_a_model_voice_take_gets_the_shows_bed_as_before(post_desk: Path) -> None:
    """A take filmed in voice mode ``model`` has no soundtrack block (a native run) and no baked music."""

    from test_harness_music import _desk_take, _made_bed
    from test_post_finish import fake_sfx
    from test_target_audio_soundtrack import facts_with

    from creation.post.finish import run_finish

    facts = facts_with(None)
    facts["take_facts"]["model_music"] = False
    _desk_take(post_desk, facts)
    calls: list[str | None] = []

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), bed_maker=_made_bed(calls),
        facts_fetcher=lambda *a: None, thumbnail=False, stream=io.StringIO(),
    )  # fmt: skip

    assert result.complete
    assert next(s for s in result.steps if s.step == "bed").status == "ran"
    assert not result.music_in_take
    assert json.loads(json.dumps(calls)) == [None]
