"""voice --audition / --pick and revoice: fake Drama API and Fal, real ffmpeg for the dub."""

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
from creation.post.fal import PENDING_FILENAME
from creation.post.media import measure_rms_windows


class FakeApi:
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


class FakeFal:
    def __init__(self) -> None:
        self.submitted: list[tuple[str, dict[str, Any]]] = []

    def submit(self, endpoint: str, arguments: dict[str, Any]) -> str:
        self.submitted.append((endpoint, arguments))
        return f"req-{len(self.submitted)}"

    def result(self, endpoint: str, request_id: str) -> dict[str, Any]:
        return {"audio": {"url": f"https://fal.test/{request_id}.mp3"}}

    def upload(self, path: Path) -> str:
        return "https://fal.test/upload"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi(copy.deepcopy(SPINE))
    monkeypatch.setattr(voice_mod, "open_api", lambda desk, episode: fake)
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
    desk: Path, api: FakeApi, downloads: list[str]
) -> None:
    fal = FakeFal()
    folder = voice_mod.run_voice_audition(desk, cast="Kenji", count=4, fal=fal, out=io.StringIO())

    path, body = api.posts[0]
    assert path == "/v1/spines/spine_test/cast/cast_kenji/voice-auditions"
    assert body == {"spine_version": SPINE["spine_version"], "lines": ["Wait for me here."], "candidate_count": 4}
    assert [a["voice"] for _, a in fal.submitted] == ["Rachel", "Aria", "Roger", "Sarah"]
    assert all(e == "fal-ai/elevenlabs/tts/eleven-v3" and a["text"] == "Wait for me here." for e, a in fal.submitted)
    listing = json.loads((folder / "auditions.json").read_text())
    assert folder.name == "audition-v1" and len(listing["candidates"]) == 4
    assert listing["candidates"][0]["url"] == "https://fal.test/req-1.mp3"
    assert json.loads((desk / PENDING_FILENAME).read_text()) == {}, "every request collected"

    with pytest.raises(ValueError, match="--cause"):
        voice_mod.run_voice_audition(desk, cast="Kenji", count=4, fal=fal, out=io.StringIO())
    second = voice_mod.run_voice_audition(desk, cast="Kenji", count=4, cause="too old", fal=fal, out=io.StringIO())
    assert second.name == "audition-v2"


@needs_ffmpeg
def test_pick_locks_the_listed_candidate_on_the_cast_card(desk: Path, api: FakeApi, downloads: list[str]) -> None:
    voice_mod.run_voice_audition(desk, cast="cast_kenji", count=4, fal=FakeFal(), out=io.StringIO())
    locked = voice_mod.run_voice_pick(desk, cast="kenji", pick=2, out=io.StringIO())

    path, body = api.posts[-1]
    assert path == "/v1/spines/spine_test/cast/cast_kenji/voice-auditions/pick"
    assert body["provider_voice"] == "Aria" and locked == "Aria"
    assert body["url"] == "https://fal.test/req-2.mp3" and body["seconds"] > 0
    with pytest.raises(ValueError, match="the numbers are 1, 2, 3, 4"):
        voice_mod.run_voice_pick(desk, cast="kenji", pick=9, out=io.StringIO())


@needs_ffmpeg
def test_revoice_mutes_only_that_characters_line_and_lays_the_new_voice_in(
    desk: Path, api: FakeApi, downloads: list[str]
) -> None:
    api.spine_body["cast"][0]["voice_brief"] = {"provider_voice": "Aria"}
    take = make_take(desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", seconds=5.0,
                     tones=((1.0, 2.0, 440), (3.0, 4.0, 880)))  # fmt: skip
    words = desk / "ep01" / "takes" / "words.json"
    words.write_text(json.dumps({"chunks": [
        {"text": "Wait", "timestamp": [1.0, 1.3]}, {"text": "for", "timestamp": [1.3, 1.5]},
        {"text": "me", "timestamp": [1.5, 1.7]}, {"text": "here.", "timestamp": [1.7, 2.0]},
        {"text": "Not", "timestamp": [3.0, 3.4]}, {"text": "tonight.", "timestamp": [3.4, 4.0]},
    ]}))  # fmt: skip
    fal = FakeFal()

    out = voice_mod.run_revoice(desk, cast="Kenji", words_json=words, fal=fal, out=io.StringIO())

    assert out.name == "take-ep01-t1-revoice-v1.mp4" and take.is_file()
    [(endpoint, arguments)] = fal.submitted
    assert endpoint == "fal-ai/elevenlabs/tts/eleven-v3"
    assert arguments["voice"] == "Aria" and arguments["text"] == "Wait for me here."
    levels = np.array(measure_rms_windows(out, window_seconds=0.1))
    assert levels[10:13].max() > -30.0, "the new 0.4 s line starts where the old one did"
    assert levels[15:20].max() < -60.0, "the rest of the original line is muted"
    assert levels[31:39].min() > -30.0, "Aya's line is left as filmed"
    record = json.loads(out.with_suffix(".json").read_text())
    assert record["lines"][0]["muted"] == [pytest.approx(0.92), pytest.approx(2.15)]


@needs_ffmpeg
def test_revoice_refuses_a_character_with_no_locked_voice(desk: Path, api: FakeApi) -> None:
    api.spine_body["cast"][0]["voice_brief"] = None
    make_take(desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")
    with pytest.raises(ValueError, match="no locked voice"):
        voice_mod.run_revoice(desk, cast="Kenji", words_json=Path("unused.json"), fal=FakeFal(), out=io.StringIO())


def test_cli_voice_needs_audition_or_pick(desk: Path) -> None:
    with pytest.raises(SystemExit):
        main(["voice", "--desk", str(desk), "--cast", "Kenji"])
