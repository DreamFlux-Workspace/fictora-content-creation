"""The voices gate: shown after the plates yes; filming refused until every speaking voice is kept or picked.

Every test here asks for ``voice_gate`` so the real gate runs (it is off by default in conftest).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import set_phase
from creation import orchestrate
from creation.cli_produce import main as produce_main
from creation.episode_commands import CommandStopped, run_film
from creation.ops.floor import approve_board, record_filmed
from creation.post import voice as voice_mod
from creation.production_state import load_production
from creation.voice_gate import APPROVALS_PATH, load_approvals
from fake_api import FakeApi, png_bytes
from test_episode_flow_step import _video_routes

pytestmark = pytest.mark.usefixtures("voice_gate")

VIDEO = "/v1/video-generations"


@pytest.fixture
def voiced(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """The fake API with the voices the draft locked (Hana = Aria, Ren = Liam); ``voice`` reads it too."""

    hana, ren = api.spine_doc["cast"]
    hana["voice_brief"] = {
        "provider_voice": "Aria",
        "seedance_vocal_signature": "warm low alto, unhurried",
        "reference_audio_url": "https://media.test/hana-ref.mp3",
    }
    ren["voice_brief"] = {"provider_voice": "Liam"}
    monkeypatch.setattr(voice_mod, "open_api", lambda _desk, _episode: api)
    return api


def _ready_to_film(api: FakeApi) -> None:
    _video_routes(api, facts_lines=None, children=("job_take_a",))
    api.routes[("GET", "/v1/jobs/job_take_a/take-facts")] = {
        "take_facts": {"endpoint_id": ""}
    }


def _keep(desk: Path, *flags: str) -> int:
    return produce_main(["voice", "--desk", str(desk), *flags])


# --- The gate shows after the plates ---------------------------------------------------------------


def test_the_plates_yes_shows_each_speaking_voice_and_what_to_run(
    desk: Path, voiced: FakeApi
) -> None:
    set_phase(desk, "wait_plates")
    voiced.routes[("POST", "/v1/spines/sp1/cast/approve")] = {"ok": True}

    result = orchestrate.approve_gate(desk, gate="plates")

    text = result.message
    assert "Voices gate (before filming)" in text
    assert "Hana (cast_hana): Aria: warm low alto, unhurried [needs a yes]" in text
    assert "sample: https://media.test/hana-ref.mp3" in text
    assert "Ren (cast_ren): Liam [needs a yes]" in text
    assert f"voice --desk {desk} --cast cast_hana --keep" in text
    assert f"voice --desk {desk} --cast cast_ren --audition" in text
    assert "$0.30, only after the human's yes" in text
    assert f"voice --desk {desk} --keep-all" in text
    assert load_production(desk).phase == "wait_script"


def test_step_at_the_script_gate_repeats_the_voices(
    desk: Path, voiced: FakeApi
) -> None:
    set_phase(desk, "wait_script")

    text = orchestrate.run_step(desk).message

    assert "Waiting on human gate `script`" in text
    assert "Ren (cast_ren): Liam [needs a yes]" in text


# --- Filming is refused until the voices have a yes -----------------------------------------------


def test_confirm_spend_is_refused_until_every_voice_has_a_yes(
    desk: Path, voiced: FakeApi
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)

    with pytest.raises(RuntimeError) as refused:
        orchestrate.run_step(desk, confirm_spend=True)

    text = str(refused.value)
    assert text.startswith("Stopped before filming. Nothing was sent.")
    assert "Hana (Aria), Ren (Liam)" in text
    assert f"fictora-produce voice --desk {desk} --cast cast_hana --keep" in text
    assert f"fictora-produce voice --desk {desk} --keep-all" in text
    assert voiced.posted(VIDEO) == []
    assert load_production(desk).phase == "wait_spend"


def test_keeping_one_voice_is_not_enough(desk: Path, voiced: FakeApi) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)
    assert _keep(desk, "--cast", "Hana", "--keep") == 0

    with pytest.raises(RuntimeError, match=r"Ren \(Liam\)") as refused:
        orchestrate.run_step(desk, confirm_spend=True)

    assert "Hana (Aria)" not in str(refused.value).splitlines()[1]
    assert voiced.posted(VIDEO) == []


def test_keeping_each_voice_unlocks_filming(desk: Path, voiced: FakeApi) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)

    assert _keep(desk, "--cast", "Hana", "--keep") == 0
    assert _keep(desk, "--cast", "cast_ren", "--keep") == 0
    orchestrate.run_step(desk, confirm_spend=True)

    assert len(voiced.posted(VIDEO)) == 1
    record = json.loads((desk / APPROVALS_PATH).read_text(encoding="utf-8"))
    assert record["grandfathered"] is False
    assert record["voices"]["cast_hana"]["provider_voice"] == "Aria"
    assert record["voices"]["cast_ren"]["how"] == "kept"


def test_a_desk_already_at_ready_video_is_refused_too(
    desk: Path, voiced: FakeApi
) -> None:
    # retry-video puts the desk at ready_video: the take is enrolled by the next step.
    set_phase(desk, "ready_video", video_idempotency_suffix="-retry-abc")
    _ready_to_film(voiced)

    with pytest.raises(RuntimeError, match="--keep-all"):
        orchestrate.run_step(desk)

    assert voiced.posted(VIDEO) == []


def test_the_refusal_says_the_same_thing_from_the_cli(
    desk: Path, voiced: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)

    code = produce_main(["step", "--desk", str(desk), "--confirm-spend"])

    assert code == 2
    assert "--cast cast_ren --keep" in capsys.readouterr().err
    assert voiced.posted(VIDEO) == []


def test_a_voice_changed_after_its_yes_needs_a_new_one(
    desk: Path, voiced: FakeApi
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)
    assert _keep(desk, "--keep-all") == 0
    voiced.spine_doc["cast"][1]["voice_brief"] = {"provider_voice": "Bill"}

    with pytest.raises(RuntimeError, match=r"Ren \(Bill\)"):
        orchestrate.run_step(desk, confirm_spend=True)

    assert voiced.posted(VIDEO) == []


def test_a_pick_is_the_humans_yes(desk: Path, voiced: FakeApi) -> None:
    folder = desk / "shared" / "voices" / "ren" / "audition-v1"
    folder.mkdir(parents=True)
    (folder / "auditions.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "number": 1,
                        "provider_voice": "Bill",
                        "url": "https://media.test/1.mp3",
                        "seconds": 1.2,
                    },
                ]
            }
        ),  # fmt: skip
        encoding="utf-8",
    )
    voiced.routes[("POST", "/v1/spines/sp1/cast/cast_ren/voice-auditions/pick")] = {
        "ok": True
    }

    voice_mod.run_voice_pick(desk, cast="Ren", pick=1)

    record = load_approvals(desk)["voices"]["cast_ren"]
    assert (record["provider_voice"], record["how"]) == ("Bill", "picked")


def test_the_estimate_warns_before_the_spend_yes(desk: Path, voiced: FakeApi) -> None:
    set_phase(desk, "ready_estimate")
    voiced.routes[("POST", "/v1/spines/sp1/batches/estimate")] = {
        "cost_estimate": {"total_usd": "1.20", "priced_on": "2026-10-05", "takes": 1}
    }

    text = orchestrate.run_step(desk).message

    assert "!! VOICES NOT APPROVED: Hana, Ren." in text
    assert f"voice --desk {desk} --list" in text


# --- film --confirm-spend ---------------------------------------------------------------------------


def _board_yes(desk: Path) -> None:
    board = desk / "ep01" / "boards" / "board.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_bytes(png_bytes())
    approve_board(desk, episode=1, take_id="t1", image=board)


def test_film_is_refused_until_the_voices_have_a_yes_then_films(
    desk: Path, voiced: FakeApi
) -> None:
    _board_yes(desk)
    set_phase(desk, "complete", film_estimates={"ep01": 1.2})
    _ready_to_film(voiced)

    with pytest.raises(CommandStopped, match="--keep-all"):
        run_film(desk, episode=1, confirm_spend=True)
    assert voiced.posted(VIDEO) == []

    assert _keep(desk, "--keep-all") == 0
    run_film(desk, episode=1, confirm_spend=True)

    assert len(voiced.posted(VIDEO)) == 1


# --- Grandfathered desks ----------------------------------------------------------------------------


def test_a_desk_that_filmed_before_the_gate_still_films(
    desk: Path, voiced: FakeApi
) -> None:
    record_filmed(desk, episode=1, take_id="t1")
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)

    orchestrate.run_step(desk, confirm_spend=True)

    assert len(voiced.posted(VIDEO)) == 1
    record = load_approvals(desk)
    assert (
        record["grandfathered"] is True and "before the voices gate" in record["note"]
    )


def test_a_grandfathered_desk_says_so_and_stays_grandfathered(
    desk: Path, voiced: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = desk / "ep01" / "takes"
    takes.mkdir(parents=True, exist_ok=True)
    (takes / "take-ep01-t1-raw-v1.mp4").write_bytes(b"mp4")

    assert _keep(desk, "--cast", "Hana", "--keep") == 0  # Ren never kept

    out = capsys.readouterr().out
    assert "this desk filmed before the voices gate" in out
    assert load_approvals(desk)["grandfathered"] is True
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)
    orchestrate.run_step(desk, confirm_spend=True)
    assert len(voiced.posted(VIDEO)) == 1


def test_a_desk_gated_from_the_start_is_not_grandfathered_by_its_own_takes(
    desk: Path, voiced: FakeApi
) -> None:
    assert _keep(desk, "--keep-all") == 0
    record_filmed(desk, episode=1, take_id="t1")
    voiced.spine_doc["cast"][1]["voice_brief"] = {"provider_voice": "Bill"}
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)

    with pytest.raises(RuntimeError, match=r"Ren \(Bill\)"):
        orchestrate.run_step(desk, confirm_spend=True)


# --- The CLI ------------------------------------------------------------------------------------------


def test_keep_needs_a_cast_and_keep_all_refuses_one(
    desk: Path, voiced: FakeApi
) -> None:
    assert _keep(desk, "--keep") == 2
    assert _keep(desk, "--cast", "Hana", "--keep-all") == 2
    assert not (desk / APPROVALS_PATH).exists()


def test_list_prints_the_gate_and_records_nothing(
    desk: Path, voiced: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _keep(desk, "--list") == 0

    out = capsys.readouterr().out
    assert "Hana (cast_hana): Aria" in out and "--keep-all" in out
    assert not (desk / APPROVALS_PATH).exists()
