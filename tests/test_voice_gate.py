"""The voices gate: shown after the script's yes; filming refused until every speaking voice is kept or picked.

Every test here asks for ``voice_gate`` so the real gate runs (it is off by default in conftest).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

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
KEEP = "/v1/spines/sp1/voice-approvals"


@pytest.fixture
def voiced(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """The fake API with the voices the draft locked (Hana = Aria, Ren = Liam); ``voice`` reads it too."""

    hana, ren = api.spine_doc["cast"]
    hana["voice_brief"] = {
        "provider_voice": "Aria",
        "seedance_vocal_signature": "warm low alto, unhurried",
        "reference_audio_url": "https://media.test/hana-ref.mp3",
    }
    ren["voice_brief"] = {
        "provider_voice": "Liam",
        "reference_audio_url": "https://media.test/ren-ref.mp3",
    }
    api.routes[("POST", KEEP)] = lambda _m, _p, body: _server_keep(api, body)
    monkeypatch.setattr(voice_mod, "open_api", lambda _desk, _episode: api)
    return api


def _server_approve(api: FakeApi, cast_id: str, how: str) -> None:
    """What fictora-drama #603 does: record the card's current voice, one record per character."""

    card = next(c for c in api.spine_doc["cast"] if c["cast_id"] == cast_id)
    voice = (card.get("voice_brief") or {}).get("provider_voice") or None
    rest = [
        r for r in api.spine_doc.get("voice_approvals", []) if r["cast_id"] != cast_id
    ]
    api.spine_doc["voice_approvals"] = [
        *rest,
        {
            "cast_id": cast_id,
            "provider_voice": voice,
            "how": how,
            "approved_at": "2026-10-05T12:00:00Z",
        },
    ]
    api.spine_doc["spine_version"] += "+"


def _server_keep(api: FakeApi, body: dict[str, Any] | None) -> Any:
    assert body is not None and 1 <= len(body["cast_ids"]) <= 4
    if body["spine_version"] != api.spine_doc["spine_version"]:
        return 409, {"error": {"code": "spine_version_conflict"}}
    for cast_id in body["cast_ids"]:
        _server_approve(api, cast_id, "kept")
    return {"spine": copy.deepcopy(api.spine_doc)}


def _server_pick(api: FakeApi, cast_id: str, voice: str) -> None:
    def pick(_m: str, _p: str, _body: dict[str, Any] | None) -> dict[str, Any]:
        card = next(c for c in api.spine_doc["cast"] if c["cast_id"] == cast_id)
        card["voice_brief"] = {"provider_voice": voice}
        _server_approve(api, cast_id, "picked")
        return {"spine": copy.deepcopy(api.spine_doc)}

    api.routes[("POST", f"/v1/spines/sp1/cast/{cast_id}/voice-auditions/pick")] = pick


def _ready_to_film(api: FakeApi) -> None:
    _video_routes(api, facts_lines=None, children=("job_take_a",))
    api.routes[("GET", "/v1/jobs/job_take_a/take-facts")] = {
        "take_facts": {"endpoint_id": ""}
    }


def _keep(desk: Path, *flags: str) -> int:
    # A keep comes after the human heard each voice (desks from 6 Oct 2026 refuse it without --heard).
    heard = ["--heard"] if {"--keep", "--keep-all"} & set(flags) else []
    return produce_main(["voice", "--desk", str(desk), *flags, *heard])


# --- One step at a time: the voices come after the script's yes (founder phone test, 22 Oct 2026) ---


def test_the_plates_yes_leads_to_the_script_and_says_nothing_about_voices(
    desk: Path, voiced: FakeApi
) -> None:
    set_phase(desk, "wait_plates")
    voiced.routes[("POST", "/v1/spines/sp1/cast/approve")] = {"ok": True}

    result = orchestrate.approve_gate(desk, gate="plates")

    assert "Human: approve script lines." in result.message
    assert "Voices gate" not in result.message
    assert "--keep-all" not in result.message
    assert load_production(desk).phase == "wait_script"


def test_step_at_the_script_gate_asks_for_the_script_only(
    desk: Path, voiced: FakeApi
) -> None:
    set_phase(desk, "wait_script")

    text = orchestrate.run_step(desk).message

    assert "Waiting on human gate `script`" in text
    assert "Voices gate" not in text


def test_the_script_yes_shows_each_speaking_voice_keep_all_first(
    desk: Path, voiced: FakeApi
) -> None:
    set_phase(desk, "wait_script")
    voiced.routes[("POST", "/v1/spines/sp1/approve")] = {"spine_version": "v6"}

    result = orchestrate.approve_gate(desk, gate="script")

    text = result.message
    assert (
        "script approved on the API. Next: the voices (below), then fictora-produce step (boards)."
        in text
    )
    assert "Voices gate (before filming)" in text
    assert "Hana (cast_hana): Aria: warm low alto, unhurried [needs a yes]" in text
    assert "sample: https://media.test/hana-ref.mp3" in text
    assert "Ren (cast_ren): Liam [needs a yes]" in text
    # Keep all voices leads (the app's filled button), then hearing them; a keep only once heard.
    keep_all = text.index(
        f"Keep all voices, once the human heard every one: fictora-produce voice --desk {desk} --keep-all --heard"
    )
    assert keep_all < text.index("Or hear the voices with the human first")
    assert f"voice --desk {desk} --cast cast_hana --keep --heard" in text
    # Quoted from Ren's own lines at the server's per-character rate, not a flat $0.30 (L-20261008-25).
    assert f"voice --desk {desk} --cast cast_ren --audition (about $" in text
    assert "$0.30" not in text
    assert load_production(desk).phase == "ready_boards_enrol"


def test_the_script_yes_prints_the_servers_voices_line(
    desk: Path, voiced: FakeApi
) -> None:
    set_phase(desk, "wait_script")
    voiced.routes[("POST", "/v1/spines/sp1/approve")] = {"spine_version": "v6"}
    said = (
        "Script approved. Before I draw the storyboard, hear the voices Hana and Ren will speak in. "
        "Keep them all, or hear them and pick others."
    )
    voiced.routes[("GET", "/v1/spines/sp1/voices-step")] = {
        "due": True,
        "text": said,
        "buttons": [
            {"label": "Keep all voices", "action": "keep_all_voices", "primary": True},
            {"label": "Hear the voices", "action": "hear_voices"},
        ],
    }

    text = orchestrate.approve_gate(desk, gate="script").message

    assert f"Sokii: {said}" in text
    asked = [
        path
        for (verb, path, _b, _k) in voiced.calls
        if verb == "GET" and "/voices-step" in path
    ]
    assert any(
        path.endswith("?episode_ordinal=1&moment=after_script") for path in asked
    )


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
    # Each keep went to the server, which now holds both yeses; the desk mirrors them.
    assert [b["cast_ids"] for b in voiced.posted(KEEP)] == [["cast_hana"], ["cast_ren"]]
    assert {
        (r["cast_id"], r["provider_voice"], r["how"])
        for r in voiced.spine_doc["voice_approvals"]
    } == {("cast_hana", "Aria", "kept"), ("cast_ren", "Liam", "kept")}
    record = json.loads((desk / APPROVALS_PATH).read_text(encoding="utf-8"))
    assert record["grandfathered"] is False
    assert record["voices"]["cast_hana"]["provider_voice"] == "Aria"
    assert record["voices"]["cast_ren"]["how"] == "kept"
    assert record["voices"]["cast_ren"]["on_server"] is True


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
    _server_pick(voiced, "cast_ren", "Bill")

    voice_mod.run_voice_pick(desk, cast="Ren", pick=1)

    # The server's pick records the yes; the desk only mirrors it (and never re-sends it).
    assert {r["cast_id"]: r["how"] for r in voiced.spine_doc["voice_approvals"]} == {
        "cast_ren": "picked"
    }
    record = load_approvals(desk)["voices"]["cast_ren"]
    assert (record["provider_voice"], record["how"], record["on_server"]) == (
        "Bill",
        "picked",
        True,
    )
    assert voiced.posted(KEEP) == []


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
    assert load_approvals(desk)["voices"] == {}
    assert voiced.posted(KEEP) == []
    assert "voice_approvals" not in voiced.spine_doc


# --- The server holds the yeses (fictora-drama #603) ------------------------------------------------


def _local_yes(desk: Path, **voices: str) -> None:
    """A desk from before this change: yeses only in shared/voices/approvals.json."""

    path = desk / APPROVALS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "grandfathered": False,
                "voices": {
                    cast_id: {
                        "name": cast_id,
                        "provider_voice": voice,
                        "how": "kept",
                        "at": "2026-10-05T09:00:00+00:00",
                    }
                    for cast_id, voice in voices.items()
                },
            }
        ),  # fmt: skip
        encoding="utf-8",
    )


def test_a_yes_on_the_server_unlocks_filming_with_nothing_on_the_desk(
    desk: Path, voiced: FakeApi
) -> None:
    # The creator kept both voices in the app: the spine carries the yeses, the desk has none.
    _server_approve(voiced, "cast_hana", "kept")
    _server_approve(voiced, "cast_ren", "picked")
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)

    orchestrate.run_step(desk, confirm_spend=True)

    assert len(voiced.posted(VIDEO)) == 1
    assert voiced.posted(KEEP) == []
    assert load_approvals(desk)["voices"] == {}


def test_a_server_yes_on_an_older_voice_needs_a_new_one(
    desk: Path, voiced: FakeApi
) -> None:
    _server_approve(voiced, "cast_hana", "kept")
    _server_approve(voiced, "cast_ren", "kept")  # on Liam
    voiced.spine_doc["cast"][1]["voice_brief"] = {"provider_voice": "Bill"}
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)

    with pytest.raises(RuntimeError, match=r"Ren \(Bill\)") as refused:
        orchestrate.run_step(desk, confirm_spend=True)

    assert "Hana (Aria)" not in str(refused.value).splitlines()[1]
    assert voiced.posted(VIDEO) == []


def test_a_desk_only_yes_is_pushed_to_the_server_once(
    desk: Path, voiced: FakeApi
) -> None:
    _local_yes(desk, cast_hana="Aria", cast_ren="Liam")
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)

    orchestrate.run_step(desk, confirm_spend=True)

    assert len(voiced.posted(VIDEO)) == 1
    assert [b["cast_ids"] for b in voiced.posted(KEEP)] == [["cast_hana", "cast_ren"]]
    assert {r["cast_id"] for r in voiced.spine_doc["voice_approvals"]} == {
        "cast_hana",
        "cast_ren",
    }
    local = load_approvals(desk)["voices"]
    assert (
        local["cast_hana"]["on_server"] is True
        and local["cast_ren"]["on_server"] is True
    )

    # Once moved, the server is the truth: a yes it no longer holds is not sent again from the desk.
    voiced.spine_doc["voice_approvals"] = [
        r for r in voiced.spine_doc["voice_approvals"] if r["cast_id"] != "cast_ren"
    ]
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    with pytest.raises(RuntimeError, match=r"Ren \(Liam\)"):
        orchestrate.run_step(desk, confirm_spend=True)
    assert len(voiced.posted(KEEP)) == 1


def test_a_desk_yes_on_an_older_voice_is_not_pushed(
    desk: Path, voiced: FakeApi
) -> None:
    # Ren's yes was given to Bill; the card now locks Liam. The keep route would record Liam: never send it.
    _local_yes(desk, cast_hana="Aria", cast_ren="Bill")
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)

    with pytest.raises(RuntimeError, match=r"Ren \(Liam\)"):
        orchestrate.run_step(desk, confirm_spend=True)

    assert [b["cast_ids"] for b in voiced.posted(KEEP)] == [["cast_hana"]]
    assert [r["cast_id"] for r in voiced.spine_doc["voice_approvals"]] == ["cast_hana"]
    assert voiced.posted(VIDEO) == []


def test_an_older_server_falls_back_to_the_desk_file_and_says_so(
    desk: Path, voiced: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    voiced.routes[("POST", KEEP)] = (404, {"detail": "Not Found"})
    _local_yes(desk, cast_hana="Aria", cast_ren="Liam")
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)

    orchestrate.run_step(desk, confirm_spend=True)

    assert len(voiced.posted(VIDEO)) == 1
    assert "no voice-approvals route yet" in capsys.readouterr().err
    assert load_approvals(desk)["voices"]["cast_ren"].get("on_server") is not True


def test_on_an_older_server_a_keep_stays_on_the_desk_and_still_gates(
    desk: Path, voiced: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    voiced.routes[("POST", KEEP)] = (404, {"detail": "Not Found"})
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)

    assert _keep(desk, "--cast", "Hana", "--keep") == 0
    out = capsys.readouterr().out
    assert "on this desk only" in out and "no voice-approvals route yet" in out

    with pytest.raises(RuntimeError, match=r"Ren \(Liam\)") as refused:
        orchestrate.run_step(desk, confirm_spend=True)
    assert "no voice-approvals route yet" in str(refused.value)
    assert voiced.posted(VIDEO) == []

    assert _keep(desk, "--cast", "Ren", "--keep") == 0
    orchestrate.run_step(desk, confirm_spend=True)
    assert len(voiced.posted(VIDEO)) == 1


def test_a_server_refusal_that_is_not_an_older_deploy_is_not_hidden(
    desk: Path, voiced: FakeApi
) -> None:
    voiced.routes[("POST", KEEP)] = (404, {"error": {"code": "cast_not_found"}})
    set_phase(desk, "wait_spend", estimate_usd=1.2)

    with pytest.raises(RuntimeError, match="cast_not_found"):
        voice_mod.run_voice_gate(desk, cast="Hana", keep=True, heard=True)

    assert load_approvals(desk)["voices"] == {}


def test_a_desk_approved_in_the_app_is_not_grandfathered_by_its_own_takes(
    desk: Path, voiced: FakeApi
) -> None:
    _server_approve(voiced, "cast_hana", "kept")
    _server_approve(voiced, "cast_ren", "kept")
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)
    orchestrate.run_step(
        desk, confirm_spend=True
    )  # the gate read the desk before its first take
    record_filmed(desk, episode=1, take_id="t1")
    voiced.spine_doc["cast"][1]["voice_brief"] = {"provider_voice": "Bill"}
    set_phase(desk, "wait_spend", estimate_usd=1.2)

    with pytest.raises(RuntimeError, match=r"Ren \(Bill\)"):
        orchestrate.run_step(desk, confirm_spend=True)
    assert load_approvals(desk)["grandfathered"] is False


def test_a_keep_that_meets_a_newer_spine_reads_it_and_tries_once_more(
    desk: Path, voiced: FakeApi
) -> None:
    calls: list[str] = []

    def keep(_m: str, _p: str, body: dict[str, Any] | None) -> Any:
        calls.append(str((body or {}).get("spine_version")))
        if len(calls) == 1:
            voiced.spine_doc["spine_version"] = (
                "v6"  # someone saved the spine in between
            )
        return _server_keep(voiced, body)

    voiced.routes[("POST", KEEP)] = keep

    assert voice_mod.run_voice_gate(desk, keep=True, heard=True) == [
        "cast_hana",
        "cast_ren",
    ]

    assert calls == ["v5", "v6"]
    assert {r["cast_id"] for r in voiced.spine_doc["voice_approvals"]} == {
        "cast_hana",
        "cast_ren",
    }


def test_seven_voices_are_kept_four_then_three_and_every_one_is_kept(
    desk: Path, voiced: FakeApi
) -> None:
    # A series holds up to 50 characters (Noodle24, 8 Oct): the keep route still takes four per call.
    from creation.voice_gate import KEEP_BATCH, keep_on_server

    for n in range(3, 8):
        voiced.spine_doc["cast"].append(
            {
                "cast_id": f"cast_guest_{n}",
                "name": f"Guest {n}",
                "voice_brief": {"provider_voice": f"Voice{n}"},
            }
        )
    wanted = [c["cast_id"] for c in voiced.spine_doc["cast"]]
    assert len(wanted) == 7 and KEEP_BATCH == 4

    body, note = keep_on_server(voiced, desk, copy.deepcopy(voiced.spine_doc), wanted)

    assert note is None and body is not None
    assert [b["cast_ids"] for b in voiced.posted(KEEP)] == [wanted[:4], wanted[4:]]
    # Each call carries the story version the call before it left (no conflict, no retry).
    assert {r["cast_id"] for r in voiced.spine_doc["voice_approvals"]} == set(wanted)


# --- First-line samples made on the server, side by side (founder phone test, 24 Oct 2026) ---------

CARD_SAMPLES = "/v1/spines/sp1/voices-card/samples"


def _card(hana: dict[str, Any], ren: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "fictora.drama-voices-card.v1",
        "rows": [{"cast_id": "cast_hana", **hana}, {"cast_id": "cast_ren", **ren}],
    }


def _no_reference_samples(api: FakeApi) -> None:
    for card in api.spine_doc["cast"]:
        card["voice_brief"].pop("reference_audio_url", None)


def test_the_script_yes_prints_the_servers_first_line_samples_and_one_still_being_made(
    desk: Path, voiced: FakeApi
) -> None:
    """The kit asked for no sample before: with none on the card, every voice needed a paid audition."""

    _no_reference_samples(voiced)
    set_phase(desk, "wait_script")
    voiced.routes[("POST", "/v1/spines/sp1/approve")] = {"spine_version": "v6"}
    voiced.routes[("POST", CARD_SAMPLES)] = _card(
        {
            "sample_state": "preparing",
            "sample_note": "Getting the voice ready…",
        },
        {
            "sample_state": "ready",
            "sample_url": "https://media.test/ren-first-line.mp3",
        },
    )

    text = orchestrate.approve_gate(desk, gate="script").message

    assert "sample: https://media.test/ren-first-line.mp3" in text
    assert "Ren: play the sample above" in text
    assert "first line: getting the voice ready" in text
    assert "Hana: no free sample, so audition first" in text
    asked = [
        path
        for (verb, path, _b, _k) in voiced.calls
        if verb == "POST" and path.startswith(CARD_SAMPLES)
    ]
    assert asked == [f"{CARD_SAMPLES}?episode_ordinal=1"]


def test_a_voice_heard_from_its_server_sample_can_be_kept(
    desk: Path, voiced: FakeApi
) -> None:
    _no_reference_samples(voiced)
    voiced.routes[("POST", CARD_SAMPLES)] = _card(
        {
            "sample_state": "ready",
            "sample_url": "https://media.test/hana-first-line.mp3",
        },
        {
            "sample_state": "ready",
            "sample_url": "https://media.test/ren-first-line.mp3",
        },
    )

    assert _keep(desk, "--keep-all") == 0

    assert voiced.posted(KEEP)


def test_an_older_server_without_the_card_leaves_the_gate_as_it_was(
    desk: Path, voiced: FakeApi
) -> None:
    set_phase(desk, "wait_script")
    voiced.routes[("POST", "/v1/spines/sp1/approve")] = {"spine_version": "v6"}

    text = orchestrate.approve_gate(desk, gate="script").message

    assert "sample: https://media.test/hana-ref.mp3" in text
    assert "getting the voice ready" not in text


# --- The server's voices hold (fictora-drama #1131; Don't Look, Hana ep 12, 27 Oct 2026) -----------

HOLD_SENTENCE = (
    "Filming is waiting: Emily and Rebecca need your yes on their voices. "
    "Keep them, or hear them and pick others. Filming starts once you do."
)


def _hold_body() -> dict[str, Any]:
    return {
        "error": {
            "code": "voices_not_approved",
            "message": HOLD_SENTENCE,
            "details": {
                "nothing_sent": True,
                "pending_cast_ids": ["cast_emily", "cast_rebecca"],
                "names": ["Emily", "Rebecca"],
                "episode_ordinals": [1],
            },
        },
        "request_id": "req-1",
    }


def _server_holds_the_film(api: FakeApi) -> None:
    """``POST /v1/video-generations`` answers 409 voices_not_approved, as the deployed API does."""

    from creation.harness.http_util import ApiRefused, http_error_message

    class _Response:
        status_code = 409
        request = None

    body = _hold_body()
    api.routes[("POST", VIDEO)] = lambda _m, _p, _b: ApiRefused(
        http_error_message(_Response(), body),  # type: ignore[arg-type]
        status=409,
        body=body,
    )


def _says_the_wait(text: str, desk: Path) -> None:
    lines = text.splitlines()
    assert lines[0] == HOLD_SENTENCE
    assert "Nothing was filmed or charged." in lines
    assert lines.index("  - Emily") < lines.index("  - Rebecca")
    assert f"fictora-produce voice --desk {desk} --keep-all --heard" in text
    assert f'fictora-produce voice --desk {desk} --cast "Emily" --audition' in text
    assert "Then film again." in lines
    assert "Traceback" not in text and "details {" not in text


def test_a_film_the_server_holds_for_voices_says_the_wait_names_who_and_what_to_do(
    desk: Path, voiced: FakeApi
) -> None:
    _board_yes(desk)
    set_phase(desk, "complete", film_estimates={"ep01": 1.2})
    _ready_to_film(voiced)
    assert (
        _keep(desk, "--keep-all") == 0
    )  # the kit's own list passes; the server still holds
    _server_holds_the_film(voiced)

    with pytest.raises(CommandStopped) as stopped:
        run_film(desk, episode=1, confirm_spend=True)

    _says_the_wait(str(stopped.value), desk)
    assert (
        len(voiced.posted(VIDEO)) == 1
    )  # asked once; refused before anything was reserved


def test_the_film_hold_exits_non_zero_from_the_cli_with_no_stack_trace(
    desk: Path, voiced: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _board_yes(desk)
    set_phase(desk, "complete", film_estimates={"ep01": 1.2})
    _ready_to_film(voiced)
    assert _keep(desk, "--keep-all") == 0
    _server_holds_the_film(voiced)
    capsys.readouterr()

    code = produce_main(
        ["film", "--desk", str(desk), "--episode", "1", "--confirm-spend"]
    )

    assert code != 0
    _says_the_wait(capsys.readouterr().err.split("Stopped: ", 1)[-1].strip(), desk)


def test_a_step_the_server_holds_for_voices_goes_back_to_the_spend_yes(
    desk: Path, voiced: FakeApi
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ready_to_film(voiced)
    assert _keep(desk, "--keep-all") == 0
    _server_holds_the_film(voiced)

    with pytest.raises(RuntimeError) as stopped:
        orchestrate.run_step(desk, confirm_spend=True)

    _says_the_wait(str(stopped.value), desk)
    state = load_production(desk)
    assert (
        state.phase == "wait_spend"
    )  # not failed: nothing was sent, film again after the yes
    assert state.video_enrolled_suffix is None


def test_a_failed_job_with_the_hold_reads_the_same_without_a_desk() -> None:
    from creation.harness.http_util import api_error_text

    text = api_error_text(_hold_body())

    assert text.splitlines()[0] == HOLD_SENTENCE
    assert "fictora-produce voice --desk <desk> --keep-all --heard" in text
    assert text.endswith("(server code: voices_not_approved)")


def test_keep_all_keeps_everyone_the_server_holds_for_an_inner_voice_only_character_too(
    desk: Path, voiced: FakeApi
) -> None:
    # Mio is heard only in thought (an inner-voice cue, no spoken line): the
    # line speakers alone missed her, and the server still held the film.
    voiced.spine_doc["cast"].append(
        {
            "cast_id": "cast_mio",
            "name": "Mio",
            "voice_brief": {
                "provider_voice": "Sora",
                "reference_audio_url": "https://media.test/mio-ref.mp3",
            },
        }
    )
    voiced.routes[("GET", "/v1/spines/sp1/voices-step")] = {
        "due": True,
        "pending_cast_ids": ["cast_ren", "cast_mio"],
        "text": "Filming is waiting: Ren and Mio need your yes on their voices.",
    }

    kept = voice_mod.run_voice_gate(desk, keep=True, heard=True)

    sent = [cast_id for body in voiced.posted(KEEP) for cast_id in body["cast_ids"]]  # type: ignore[index]
    assert {"cast_ren", "cast_mio"} <= set(sent)
    assert "cast_mio" in kept
    asked = [
        p for (verb, p, _b, _k) in voiced.calls if verb == "GET" and "/voices-step" in p
    ]
    assert any(p.endswith("moment=before_filming") for p in asked)
