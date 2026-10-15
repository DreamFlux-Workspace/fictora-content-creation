"""``cue`` relies on the server to word a monster cue, and stops safely on a server that cannot (fictora-drama #826).

The kit no longer refuses "monster" itself (GRAFT, 14 Oct 2026). The ``cue``
command (a hand cue, and only it) says ``server_words_creature_cues: true``;
finish's Sound-line effects and the ambience never do, so a continuing show's
effects keep their words. A server older than #826 refuses that
field with a 422 before anything is made, and the kit says the server is too
old instead of sending a cue that could come back as a dog or a person.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from creation.harness.session import DramaApiRunSession
from creation.post import audio_service
from creation.post.audio_service import (
    CREATURE_CUE_FLAG,
    CREATURE_CUE_SERVER_TOO_OLD,
    AudioServiceError,
)

_OK = {"audio_url": "https://media.test/cue.mp3", "kind": "event", "rms_windows": [-20.0],
       "window_seconds": 0.5, "shape_problem": None, "cached": False, "cost_usd": 0.004,
       "creature_guard": True}  # fmt: skip


def _audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> audio_service.DramaApiAudio:
    from creation.post import desk as desk_mod

    def open_api(desk: Path, episode: int) -> DramaApiRunSession:
        run = DramaApiRunSession(
            base_url="https://drama.test", token="t", out_dir=tmp_path, session_id="s"
        )
        run.client = httpx.Client(transport=httpx.MockTransport(handler))
        return run

    monkeypatch.setattr(desk_mod, "open_api", open_api)
    return audio_service.DramaApiAudio(tmp_path)


def test_the_cue_command_asks_the_server_to_word_creature_cues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_OK)

    audio = _audio(tmp_path, monkeypatch, handler)
    answer = audio.sfx_cue(
        spine_id="sp",
        sound="a monster as the door opens",
        seconds=2.0,
        key="k",
        server_words_creature_cues=True,
    )

    assert answer["creature_guard"] is True
    assert bodies == [
        {
            "sound": "a monster as the door opens",
            "seconds": 2.0,
            CREATURE_CUE_FLAG: True,
        }
    ]


def test_a_server_without_the_guard_stops_the_cue_with_nothing_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            422,
            json={"error": {"code": "invalid_request", "message": "Extra inputs are not permitted",
                            "details": {"errors": [{"loc": ["body", CREATURE_CUE_FLAG]}]}}},
        )  # fmt: skip

    audio = _audio(tmp_path, monkeypatch, handler)
    with pytest.raises(AudioServiceError) as stopped:
        audio.sfx_cue(
            spine_id="sp",
            sound="a monster",
            seconds=2.0,
            key="k",
            server_words_creature_cues=True,
        )

    assert str(stopped.value) == CREATURE_CUE_SERVER_TOO_OLD
    assert "nothing was spent" in str(stopped.value)
    assert len(calls) == 1, "never re-sent without the flag"


def test_another_refusal_keeps_the_servers_own_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"error": {"code": "invalid_request", "message": "seconds too long"}},
        )

    audio = _audio(tmp_path, monkeypatch, handler)
    with pytest.raises(AudioServiceError, match="seconds too long"):
        audio.sfx_cue(spine_id="sp", sound="a door", seconds=2.0, key="k")


def test_finish_effects_and_ambience_never_send_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Continuing shows: finish's Sound-line effects and the ambience keep their words and work on any server."""

    from creation.post import ambience, sfx

    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_OK)

    audio = _audio(tmp_path, monkeypatch, handler)
    monkeypatch.setattr(sfx, "download", lambda url, dest: dest)
    monkeypatch.setattr(ambience, "download", lambda url, dest: dest)

    cue = sfx.SfxCue(
        shot_index=0,
        sound="the creature screeches",
        kind="event",
        start=1.0,
        seconds=1.5,
    )
    sfx.service_renderer(audio, "sp")(cue, tmp_path / "e.mp3")
    ambience.service_maker(audio, "sp")(
        "a monster lair, dripping water", 4.0, tmp_path / "a.mp3"
    )

    assert len(bodies) == 2
    assert all(CREATURE_CUE_FLAG not in body for body in bodies)


def test_the_cue_command_passes_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation.post import handmade

    sent: list[dict[str, object]] = []

    class _Audio:
        def sfx_cue(self, **kwargs: object) -> dict[str, object]:
            sent.append(kwargs)
            raise AudioServiceError(CREATURE_CUE_SERVER_TOO_OLD)

    monkeypatch.setattr(handmade, "spine_id", lambda desk: "sp")
    with pytest.raises(AudioServiceError):
        handmade.run_cue(
            tmp_path, episode=1, description="a monster", seconds=1.0, audio=_Audio()
        )
    assert sent and sent[0]["server_words_creature_cues"] is True
