"""voice --audition / --pick and revoice: fake Drama API and audio service, real ffmpeg for the dub."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import SPINE, make_take, make_tone, needs_ffmpeg

from creation.cli_produce import main
from creation.post import voice as voice_mod
from creation.post.media import measure_rms_windows


class FakePostApi:
    """Answers like the deployed API for the routes these commands call."""

    def __init__(self, spine: dict[str, Any]) -> None:
        self.spine_body = spine
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.saved: list[str] = []
        self.client = self

    def close(self) -> None:
        pass

    def spine(self, spine_id: str) -> dict[str, Any]:
        return {"spine": copy.deepcopy(self.spine_body)}

    def save(self, name: str, payload: Any) -> None:
        self.saved.append(name)

    def post(self, path: str, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        self.posts.append((path, body))
        if path.endswith("/voice-auditions"):
            text = " ".join(body["lines"])
            voices = ["Rachel", "Aria", "Roger", "Sarah"][: body["candidate_count"]]
            return {"cast_id": "cast_kenji", "candidates": [{"provider_voice": v, "text": text} for v in voices]}
        if path.endswith("/voice-auditions/pick"):
            for card in self.spine_body["cast"]:
                if card["cast_id"] == "cast_kenji":
                    card["voice_brief"] = {"provider_voice": body["provider_voice"]}
            return {"spine": self.spine_body}
        raise AssertionError(f"unexpected POST {path}")


class FakeAudio:
    """The generated-audio service: records every request, answers with a URL."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _answer(self, kind: str, kwargs: dict[str, Any]) -> str:
        self.calls.append((kind, kwargs))
        return f"https://audio.test/{kind}-{len(self.calls)}.mp3"

    def audition_sample(self, **kwargs: Any) -> str:
        return self._answer("audition", kwargs)

    def voice_line(self, **kwargs: Any) -> str:
        return self._answer("line", kwargs)


@pytest.fixture
def post_api(monkeypatch: pytest.MonkeyPatch) -> FakePostApi:
    fake = FakePostApi(copy.deepcopy(SPINE))
    monkeypatch.setattr(voice_mod, "open_api", lambda post_desk, episode: fake)
    return fake


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    fetched: list[str] = []

    def fake_download(url: str, dest: Path) -> Path:
        fetched.append(url)
        return make_tone(dest, seconds=0.4, freq=660)

    monkeypatch.setattr(voice_mod, "download", fake_download)
    return fetched


@needs_ffmpeg
def test_audition_renders_each_candidate_on_the_real_lines_and_a_second_set_needs_a_cause(
    post_desk: Path, post_api: FakePostApi, downloads: list[str]
) -> None:
    audio = FakeAudio()
    folder = voice_mod.run_voice_audition(post_desk, cast="Kenji", count=4, audio=audio, out=io.StringIO())

    path, body = post_api.posts[0]
    assert path == "/v1/spines/spine_test/cast/cast_kenji/voice-auditions"
    assert body == {"spine_version": SPINE["spine_version"], "lines": ["Wait for me here."], "candidate_count": 4}
    assert [a["voice"] for _, a in audio.calls] == ["Rachel", "Aria", "Roger", "Sarah"]
    assert all(kind == "audition" and a["text"] == "Wait for me here." for kind, a in audio.calls)
    assert len({a["key"] for _, a in audio.calls}) == 4, "one idempotency key per candidate"
    listing = json.loads((folder / "auditions.json").read_text())
    assert folder.name == "audition-v1" and len(listing["candidates"]) == 4
    assert listing["candidates"][0]["url"] == "https://audio.test/audition-1.mp3"

    with pytest.raises(ValueError, match="--cause"):
        voice_mod.run_voice_audition(post_desk, cast="Kenji", count=4, audio=audio, out=io.StringIO())
    second = voice_mod.run_voice_audition(
        post_desk, cast="Kenji", count=4, cause="too old", audio=audio, out=io.StringIO()
    )
    assert second.name == "audition-v2"


@needs_ffmpeg
def test_pick_locks_the_listed_candidate_on_the_cast_card(
    post_desk: Path, post_api: FakePostApi, downloads: list[str]
) -> None:
    voice_mod.run_voice_audition(post_desk, cast="cast_kenji", count=4, audio=FakeAudio(), out=io.StringIO())
    locked = voice_mod.run_voice_pick(post_desk, cast="kenji", pick=2, out=io.StringIO())

    path, body = post_api.posts[-1]
    assert path == "/v1/spines/spine_test/cast/cast_kenji/voice-auditions/pick"
    assert body["provider_voice"] == "Aria" and locked == "Aria"
    assert body["url"] == "https://audio.test/audition-2.mp3" and body["seconds"] > 0
    with pytest.raises(ValueError, match="the numbers are 1, 2, 3, 4"):
        voice_mod.run_voice_pick(post_desk, cast="kenji", pick=9, out=io.StringIO())


@needs_ffmpeg
def test_revoice_mutes_only_that_characters_line_and_lays_the_new_voice_in(
    post_desk: Path, post_api: FakePostApi, downloads: list[str]
) -> None:
    post_api.spine_body["cast"][0]["voice_brief"] = {"provider_voice": "Aria"}
    take = make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", seconds=5.0,
                     tones=((1.0, 2.0, 440), (3.0, 4.0, 880)))  # fmt: skip
    words = post_desk / "ep01" / "takes" / "words.json"
    words.write_text(json.dumps({"chunks": [
        {"text": "Wait", "timestamp": [1.0, 1.3]}, {"text": "for", "timestamp": [1.3, 1.5]},
        {"text": "me", "timestamp": [1.5, 1.7]}, {"text": "here.", "timestamp": [1.7, 2.0]},
        {"text": "Not", "timestamp": [3.0, 3.4]}, {"text": "tonight.", "timestamp": [3.4, 4.0]},
    ]}))  # fmt: skip
    audio = FakeAudio()

    out = voice_mod.run_revoice(post_desk, cast="Kenji", words_json=words, audio=audio, out=io.StringIO())

    assert out.name == "take-ep01-t1-revoice-v1.mp4" and take.is_file()
    [(endpoint, arguments)] = audio.calls
    assert endpoint == "line"
    assert arguments["voice"] == "Aria" and arguments["text"] == "Wait for me here."
    levels = np.array(measure_rms_windows(out, window_seconds=0.1))
    assert levels[10:13].max() > -30.0, "the new 0.4 s line starts where the old one did"
    assert levels[15:20].max() < -60.0, "the rest of the original line is muted"
    assert levels[31:39].min() > -30.0, "Aya's line is left as filmed"
    record = json.loads(out.with_suffix(".json").read_text())
    assert record["lines"][0]["muted"] == [pytest.approx(0.92), pytest.approx(2.15)]


@needs_ffmpeg
def test_revoice_refuses_a_character_with_no_locked_voice(post_desk: Path, post_api: FakePostApi) -> None:
    post_api.spine_body["cast"][0]["voice_brief"] = None
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")
    with pytest.raises(ValueError, match="no locked voice"):
        voice_mod.run_revoice(
            post_desk, cast="Kenji", words_json=Path("unused.json"), audio=FakeAudio(), out=io.StringIO()
        )


def test_cli_voice_needs_audition_or_pick(post_desk: Path) -> None:
    with pytest.raises(SystemExit):
        main(["voice", "--desk", str(post_desk), "--cast", "Kenji"])
