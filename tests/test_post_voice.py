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
from creation.ops.state import load_series
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
            return {
                "cast_id": "cast_kenji",
                "candidates": [{"provider_voice": v, "text": text} for v in voices],
            }
        if path.endswith("/voice-auditions/pick"):
            for card in self.spine_body["cast"]:
                if card["cast_id"] == "cast_kenji":
                    card["voice_brief"] = {"provider_voice": body["provider_voice"]}
            return {"spine": self.spine_body}
        raise AssertionError(f"unexpected POST {path}")


class FakeAudio:
    """The server's operator audio routes: records every request, answers with stored URLs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def render_auditions(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("auditions", kwargs))
        voices = ["Rachel", "Aria", "Roger", "Sarah"][: kwargs["count"]]
        return {
            "candidates": [
                {"voice_id": v, "text": " ".join(kwargs["lines"]), "audio_url": f"https://media.test/aud-{i}.mp3",
                 "seconds": 1.5}
                for i, v in enumerate(voices, start=1)
            ],
            "cost_usd": 0.02,
        }  # fmt: skip

    def voice_line(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("line", kwargs))
        return {"audio_url": "https://media.test/line.mp3", "seconds": 0.4, "provider_voice": "Aria",
                "reading": {"checked": True, "read_right": True, "match": 1.0}, "cost_usd": 0.002}  # fmt: skip

    words: list[dict[str, Any]] | None = None

    def transcribe(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("transcribe", kwargs))
        if self.words is not None:
            return {"text": "", "words": self.words}
        return {"text": "", "words": [
            {"word": "Wait", "start": 1.0, "end": 1.3}, {"word": "for", "start": 1.3, "end": 1.5},
            {"word": "me", "start": 1.5, "end": 1.7}, {"word": "here.", "start": 1.7, "end": 2.0},
            {"word": "Not", "start": 3.0, "end": 3.4}, {"word": "tonight.", "start": 3.4, "end": 4.0},
        ]}  # fmt: skip


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
    folder = voice_mod.run_voice_audition(
        post_desk, cast="Kenji", count=4, audio=audio, out=io.StringIO()
    )

    [(kind, asked)] = audio.calls
    assert kind == "auditions" and post_api.posts == [], (
        "rendered by the server's render route, nothing local"
    )
    assert {k: v for k, v in asked.items() if k != "key"} == {
        "spine_id": "spine_test", "cast_id": "cast_kenji", "spine_version": SPINE["spine_version"],
        "lines": ["Wait for me here."], "count": 4, "text": None, "voices": None,
    }  # fmt: skip
    assert asked["key"] == voice_mod._unit(
        "audition-kenji",
        {"lines": ["Wait for me here."], "count": 4, "set": "audition-v1"},
    ), (
        "a plain audition keeps the request key it had, so an interrupted run still replays"
    )
    listing = json.loads((folder / "auditions.json").read_text())
    assert folder.name == "audition-v1" and [c["provider_voice"] for c in listing["candidates"]] == [
        "Rachel", "Aria", "Roger", "Sarah"]  # fmt: skip
    assert listing["candidates"][0]["url"] == "https://media.test/aud-1.mp3"
    assert downloads[0] == "https://media.test/aud-1.mp3"

    with pytest.raises(ValueError, match="--cause"):
        voice_mod.run_voice_audition(
            post_desk, cast="Kenji", count=4, audio=audio, out=io.StringIO()
        )
    second = voice_mod.run_voice_audition(
        post_desk,
        cast="Kenji",
        count=4,
        cause="too old",
        audio=audio,
        out=io.StringIO(),
    )
    assert second.name == "audition-v2"
    assert [entry.unit for entry in load_series(post_desk).spend_log] == [
        "voice-audition:kenji"
    ] * 2
    assert audio.calls[-1][1]["key"] != asked["key"], (
        "a paid second set is a new request, not a replay"
    )


@needs_ffmpeg
def test_pick_locks_the_listed_candidate_on_the_cast_card(
    post_desk: Path, post_api: FakePostApi, downloads: list[str]
) -> None:
    voice_mod.run_voice_audition(
        post_desk, cast="cast_kenji", count=4, audio=FakeAudio(), out=io.StringIO()
    )
    locked = voice_mod.run_voice_pick(
        post_desk, cast="kenji", pick=2, out=io.StringIO()
    )

    path, body = post_api.posts[-1]
    assert path == "/v1/spines/spine_test/cast/cast_kenji/voice-auditions/pick"
    assert body["provider_voice"] == "Aria" and locked == "Aria"
    assert body["url"] == "https://media.test/aud-2.mp3" and body["seconds"] == 1.5
    with pytest.raises(
        ValueError, match="the set is 1 Rachel, 2 Aria, 3 Roger, 4 Sarah"
    ):
        voice_mod.run_voice_pick(post_desk, cast="kenji", pick=9, out=io.StringIO())


@needs_ffmpeg
def test_revoice_mutes_only_that_characters_line_and_lays_the_new_voice_in(
    post_desk: Path, post_api: FakePostApi, downloads: list[str]
) -> None:
    post_api.spine_body["cast"][0]["voice_brief"] = {"provider_voice": "Aria"}
    take = make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", seconds=5.0,
                     tones=((1.0, 2.0, 440), (3.0, 4.0, 880)))  # fmt: skip
    stored = "https://media.test/tenants/t/drama/take-ep01-t1.mp4"
    (post_desk / "ep01" / "api" / "17_raw_scene_clips.json").write_text(json.dumps(
        {"clips": [{"job_id": "job_scene_1", "url": stored, "episode_id": "episode_01", "set_index": 1}]}))  # fmt: skip
    audio = FakeAudio()

    out = voice_mod.run_revoice(post_desk, cast="Kenji", audio=audio, out=io.StringIO())

    assert out.name == "take-ep01-t1-revoice-v1.mp4" and take.is_file()
    (kind, heard), (endpoint, arguments) = audio.calls
    assert kind == "transcribe" and heard["audio_url"] == stored, (
        "the take's stored URL, never an upload"
    )
    assert endpoint == "line"
    assert (arguments["cast_id"], arguments["text"]) == (
        "cast_kenji",
        "Wait for me here.",
    )
    levels = np.array(measure_rms_windows(out, window_seconds=0.1))
    assert levels[10:13].max() > -30.0, (
        "the new 0.4 s line starts where the old one did"
    )
    assert levels[15:20].max() < -60.0, "the rest of the original line is muted"
    assert levels[31:39].min() > -30.0, "Aya's line is left as filmed"
    record = json.loads(out.with_suffix(".json").read_text())
    assert record["lines"][0]["muted"] == [pytest.approx(0.92), pytest.approx(2.15)]
    assert [
        (entry.unit, entry.take_id) for entry in load_series(post_desk).spend_log
    ] == [("revoice:kenji", "t1")]


@needs_ffmpeg
def test_revoice_refuses_a_character_with_no_locked_voice(
    post_desk: Path, post_api: FakePostApi
) -> None:
    post_api.spine_body["cast"][0]["voice_brief"] = None
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")
    with pytest.raises(ValueError, match="no locked voice"):
        voice_mod.run_revoice(
            post_desk,
            cast="Kenji",
            words_json=Path("unused.json"),
            audio=FakeAudio(),
            out=io.StringIO(),
        )


def test_cli_voice_needs_audition_or_pick(post_desk: Path) -> None:
    with pytest.raises(SystemExit):
        main(["voice", "--desk", str(post_desk), "--cast", "Kenji"])


@needs_ffmpeg
def test_revoice_without_a_stored_take_url_refuses_to_upload(
    post_desk: Path, post_api: FakePostApi
) -> None:
    post_api.spine_body["cast"][0]["voice_brief"] = {"provider_voice": "Aria"}
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")
    audio = FakeAudio()
    with pytest.raises(ValueError, match="never uploads"):
        voice_mod.run_revoice(post_desk, cast="Kenji", audio=audio, out=io.StringIO())
    assert audio.calls == []


JA_WORDS = [
    {"word": "ここで", "start": 1.0, "end": 1.4}, {"word": "待ってて", "start": 1.4, "end": 2.0},
    {"word": "今夜は", "start": 3.0, "end": 3.5}, {"word": "だめ", "start": 3.5, "end": 4.0},
]  # fmt: skip


@needs_ffmpeg
def test_revoice_on_a_japanese_show_voices_and_finds_the_performed_line_not_the_subtitle(
    post_desk: Path, post_api: FakePostApi, downloads: list[str]
) -> None:
    spine = post_api.spine_body
    spine["spoken_language"] = "ja-JP"
    spine["cast"][0]["voice_brief"] = {"provider_voice": "Aria"}
    kenji, aya = spine["beats"][0]["dialogue_lines"]
    kenji.update(spoken_text="ここで待ってて", subtitle_text="Wait for me here.")
    aya.update(spoken_text="今夜はだめ", subtitle_text="Not tonight.")
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", seconds=5.0,
              tones=((1.0, 2.0, 440), (3.0, 4.0, 880)))  # fmt: skip
    (post_desk / "ep01" / "api" / "17_raw_scene_clips.json").write_text(json.dumps(
        {"clips": [{"job_id": "j1", "url": "https://media.test/tenants/t/drama/t1.mp4", "episode_id": "episode_01", "set_index": 1}]}))  # fmt: skip
    audio = FakeAudio()
    audio.words = JA_WORDS

    out = voice_mod.run_revoice(post_desk, cast="Kenji", audio=audio, out=io.StringIO())

    (_, heard), (_, line) = audio.calls
    assert heard["language"] == "ja", "the take is transcribed in the show's language"
    assert line["language"] == "ja"
    assert line["text"] == "ここで待ってて", (
        "the performed line, not the English subtitle"
    )
    assert line["spoken_text"] == "ここで待ってて", (
        "so the server's kana re-check applies"
    )
    record = json.loads(out.with_suffix(".json").read_text())
    assert record["lines"][0]["original_window"] == [1.0, 2.0], (
        "found by the Japanese words"
    )
    assert record["not_heard"] == []


def test_the_voice_line_route_gets_the_performed_line_language_and_spoken_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from creation.harness.session import DramaApiRunSession
    from creation.post import desk as desk_mod
    from creation.post.audio_service import DramaApiAudio

    bodies: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"audio_url": "u"})

    def open_api(desk: Path, episode: int) -> DramaApiRunSession:
        run = DramaApiRunSession(
            base_url="https://drama.test", token="t", out_dir=tmp_path, session_id="s"
        )
        run.client = httpx.Client(transport=httpx.MockTransport(answer))
        return run

    monkeypatch.setattr(desk_mod, "open_api", open_api)
    DramaApiAudio(tmp_path).voice_line(
        spine_id="sp",
        cast_id="c",
        text="ここで待ってて",
        language="ja",
        key="k",
        spoken_text="ここで待ってて",
    )
    assert bodies == [
        {"text": "ここで待ってて", "language": "ja", "spoken_text": "ここで待ってて"}
    ]


class ServerAudio(FakeAudio):
    """Answers like the render route after Drama #468: ``voices`` replaces the default slate.

    One candidate per named voice in that order; a retired premade comes back as
    its catalog stand-in (``Bella`` -> ``Sarah``); an unknown name is the server's
    named 422, raised the way ``DramaApiAudio`` raises it.
    """

    DEFAULT_SLATE = (
        "Rachel",
        "Aria",
        "Roger",
        "Sarah",
        "Laura",
        "Charlie",
        "George",
        "Callum",
    )
    STAND_INS = {"bella": "Sarah"}

    def render_auditions(self, **kwargs: Any) -> dict[str, Any]:
        from creation.post.audio_service import AudioServiceError

        self.calls.append(("auditions", kwargs))
        catalog = {v.casefold(): v for v in (*self.DEFAULT_SLATE, "Alice", "Bill")}
        named = kwargs.get("voices")
        if named:
            unknown = [
                v
                for v in named
                if v.casefold() not in catalog and v.casefold() not in self.STAND_INS
            ]
            if unknown:
                raise AudioServiceError(
                    f"HTTP 422 voice-auditions/render: voice_audition_unknown_voice: {unknown[0]!r} is not an "
                    "Eleven v3 voice; the catalog is Rachel, Aria, ..."
                )
            voices = [
                self.STAND_INS.get(v.casefold()) or catalog[v.casefold()] for v in named
            ]
        else:
            voices = list(self.DEFAULT_SLATE[: kwargs["count"]])
        said = kwargs.get("text") or kwargs["lines"][0]
        return {
            "candidates": [
                {
                    "voice_id": v,
                    "text": said,
                    "audio_url": f"https://media.test/aud-{i}.mp3",
                    "seconds": 0.4,
                }
                for i, v in enumerate(voices, start=1)
            ],
            "cost_usd": 0.02,
        }


@needs_ffmpeg
def test_new_wording_and_named_voices_go_straight_to_the_server_and_all_land_in_one_reel(
    post_desk: Path, post_api: FakePostApi, downloads: list[str]
) -> None:
    audio = ServerAudio()
    out = io.StringIO()
    folder = voice_mod.run_voice_audition(
        post_desk, cast="Kenji", text="  No matter what happens...  do not break eye contact. ",
        voices="Laura, bella", audio=audio, out=out,
    )  # fmt: skip

    [(_, asked)] = audio.calls
    assert asked["text"] == "No matter what happens... do not break eye contact.", (
        "wording not on the spine is sent"
    )
    assert asked["voices"] == ["Laura", "bella"], (
        "the names as typed, in order; the server resolves them"
    )
    assert asked["lines"] == []
    listing = json.loads((folder / "auditions.json").read_text())
    assert listing["text"] == asked["text"] and listing["voices"] == ["Laura", "bella"]
    [reel] = listing["reels"]
    assert [(i["number"], i["voice"]) for i in reel["index"]] == [
        (1, "Laura"),
        (2, "Sarah"),
    ], "every clip the server read goes in the reel, a retired name under its stand-in"
    assert reel["index"][1]["start"] == pytest.approx(
        0.4 + voice_mod.REEL_GAP_SECONDS, abs=0.05
    )
    reel_file = folder / reel["file"]
    assert reel_file.name == "reel-v1.m4a" and reel_file.with_suffix(".txt").is_file()
    from creation.post.media import media_duration

    assert media_duration(reel_file) == pytest.approx(
        2 * (0.4 + voice_mod.REEL_GAP_SECONDS), abs=0.15
    )
    assert " 1. Laura" in out.getvalue() and " 2. Sarah" in out.getvalue()

    again = voice_mod.run_voice_audition(
        post_desk, cast="Kenji", text="no matter what happens... do not break eye contact.", voices=["laura"],
        audio=audio, out=io.StringIO(),
    )  # fmt: skip
    assert again == folder and len(audio.calls) == 1, (
        "a set that holds the wording and voice: new reel, nothing paid"
    )
    assert json.loads((folder / "auditions.json").read_text())["reels"][1][
        "voices"
    ] == ["Laura"]
    assert (folder / "reel-v2.m4a").is_file() and (folder / "reel-v1.m4a").is_file()


@needs_ffmpeg
def test_named_voices_on_the_real_lines_replace_the_default_slate(
    post_desk: Path, post_api: FakePostApi, downloads: list[str]
) -> None:
    audio = ServerAudio()
    folder = voice_mod.run_voice_audition(
        post_desk, cast="Kenji", voices="Callum", audio=audio, out=io.StringIO()
    )

    [(_, asked)] = audio.calls
    assert (
        asked["lines"] == ["Wait for me here."]
        and asked["text"] is None
        and asked["voices"] == ["Callum"]
    )
    listing = json.loads((folder / "auditions.json").read_text())
    assert [c["provider_voice"] for c in listing["candidates"]] == ["Callum"], (
        "one voice asked, one voice paid"
    )


def test_the_servers_named_refusal_is_printed_as_it_is_and_nothing_is_booked(
    post_desk: Path,
    post_api: FakePostApi,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import httpx

    from creation.harness.session import DramaApiRunSession
    from creation.post import desk as desk_mod

    bodies: list[dict[str, Any]] = []
    refusal = {"error": {"code": "voice_audition_unknown_voice",
                         "message": "'Zorblax' is not an Eleven v3 voice; the catalog is Rachel, Aria, Sarah"}}  # fmt: skip

    def answer(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(422, json=refusal)

    def open_api(desk: Path, episode: int) -> DramaApiRunSession:
        run = DramaApiRunSession(
            base_url="https://drama.test", token="t", out_dir=post_desk, session_id="s"
        )
        run.client = httpx.Client(transport=httpx.MockTransport(answer))
        return run

    monkeypatch.setattr(desk_mod, "open_api", open_api)
    code = main(["voice", "--desk", str(post_desk), "--cast", "Kenji", "--audition", "--text", "Do not blink.",
                 "--voices", "Zorblax,Sarah"])  # fmt: skip

    err = capsys.readouterr().err
    assert code == 2
    assert (
        "422" in err
        and "voice_audition_unknown_voice: 'Zorblax' is not an Eleven v3 voice" in err
    )
    assert "catalog is Rachel, Aria, Sarah" in err and "nothing was spent" in err
    [body] = bodies
    assert body == {
        "spine_version": SPINE["spine_version"],
        "text": "Do not blink.",
        "voices": ["Zorblax", "Sarah"],
    }
    assert not list(post_desk.glob("shared/voices/*/audition-v*/auditions.json")), (
        "no set recorded, nothing booked"
    )


def test_voices_are_split_as_typed_and_left_to_the_server() -> None:
    assert voice_mod.parse_voices("Aria, aria ,,Sarah") == ["Aria", "aria", "Sarah"]
    assert len(voice_mod.parse_voices(",".join(f"v{i}" for i in range(11)))) == 11, (
        "the server owns the cap"
    )
    assert voice_mod.parse_voices(None) == []


@needs_ffmpeg
def test_pick_by_voice_name(
    post_desk: Path, post_api: FakePostApi, downloads: list[str]
) -> None:
    voice_mod.run_voice_audition(
        post_desk,
        cast="Kenji",
        text="Do not blink.",
        voices="Rachel,Laura",
        audio=ServerAudio(),
        out=io.StringIO(),
    )
    assert (
        voice_mod.run_voice_pick(
            post_desk, cast="Kenji", pick="laura", out=io.StringIO()
        )
        == "Laura"
    )
    path, body = post_api.posts[-1]
    assert (
        path.endswith("/voice-auditions/pick")
        and body["url"] == "https://media.test/aud-2.mp3"
    )


# --- voice-fx ----------------------------------------------------------------------------------


def _samples(path: Path, tmp: Path) -> np.ndarray:
    import wave

    from creation.post.media import extract_wav

    wav = extract_wav(path, tmp / f"{path.stem}.wav", rate=16000)
    with wave.open(str(wav)) as handle:
        return np.frombuffer(
            handle.readframes(handle.getnframes()), dtype=np.int16
        ).astype(float)


@needs_ffmpeg
def test_voice_fx_treats_only_the_range_keeps_its_level_and_writes_a_new_file(
    tmp_path: Path,
) -> None:
    from creation.post.voice_fx import apply_voice_fx, parse_range

    from creation.post.media import run_ffmpeg

    loud = make_take(
        tmp_path / "loud.mp4", seconds=5.0, tones=((0.5, 4.5, 150), (0.5, 4.5, 1000))
    )
    take = tmp_path / "take-ep01-t1-raw-v1.mp4"
    # A hot voice (about -8.5 dB RMS): the band-pass and compressor alone would drop the range ~10 dB.
    run_ffmpeg(
        ["-i", str(loud), "-c:v", "copy", "-af", "volume=6", "-c:a", "aac", str(take)]
    )
    before = take.read_bytes()
    start, end = parse_range("1-3")

    made = apply_voice_fx(take, start=start, end=end, preset="intercom")

    assert (
        made.name == "take-ep01-t1-raw-v1-intercom-v1.mp4"
        and take.read_bytes() == before
    )
    dry, wet = _samples(take, tmp_path), _samples(made, tmp_path)
    rate = 16000
    inside = slice(int(1.5 * rate), int(2.5 * rate))
    outside = slice(int(3.5 * rate), int(4.3 * rate))

    def corr(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.corrcoef(a, b)[0, 1])

    def low_to_band_db(a: np.ndarray) -> float:
        spectrum = np.abs(np.fft.rfft(a))  # 1 s of audio: bin n is n Hz
        return 20 * np.log10(spectrum[145:156].max() / spectrum[995:1006].max())

    def db(a: np.ndarray) -> float:
        return 20 * np.log10(np.sqrt(np.mean(a**2)) + 1e-9)

    assert corr(dry[outside], wet[outside]) > 0.99, (
        "outside the range the take is as filmed"
    )
    assert low_to_band_db(wet[inside]) < low_to_band_db(dry[inside]) - 20, (
        "inside, the band-pass takes the lows out"
    )
    assert abs(db(wet[inside]) - db(dry[inside])) < 1.5, (
        "the treated range keeps the level it had"
    )
    assert apply_voice_fx(take, start=1, end=3, preset="intercom").name.endswith(
        "-intercom-v2.mp4"
    )


def test_voice_fx_refuses_a_bad_range_or_preset(tmp_path: Path) -> None:
    from creation.post.voice_fx import apply_voice_fx, parse_range

    for raw in ("3-1", "abc", "2-2"):
        with pytest.raises(ValueError, match="START-END"):
            parse_range(raw)
    with pytest.raises(ValueError, match="intercom, phone, radio"):
        apply_voice_fx(tmp_path / "x.mp4", start=0, end=1, preset="robot")


@needs_ffmpeg
def test_cli_voice_fx_writes_a_new_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    take = make_take(tmp_path / "take.mp4", seconds=3.0)
    assert (
        main(["voice-fx", "--file", str(take), "--range", "0.5-2", "--preset", "phone"])
        == 0
    )
    assert (tmp_path / "take-phone-v1.mp4").is_file()
    assert (
        main(["voice-fx", "--file", str(take), "--range", "2-1", "--preset", "radio"])
        == 2
    )


@needs_ffmpeg
def test_voice_fx_treats_every_repeated_range_into_one_file(tmp_path: Path) -> None:
    take = make_take(
        tmp_path / "take-ep01-t1-revoice-v1.mp4",
        seconds=5.0,
        tones=((0.1, 4.9, 150), (0.1, 4.9, 1000)),
    )

    assert (
        main(
            [
                "voice-fx",
                "--file",
                str(take),
                "--range",
                "0.3-1.7",
                "--range",
                "3.1-4.5",
                "--preset",
                "intercom",
            ]
        )  # fmt: skip
        == 0
    )

    made = tmp_path / "take-ep01-t1-revoice-v1-intercom-v1.mp4"
    assert made.is_file()
    assert not list(tmp_path.glob("*-intercom-v1-intercom-*")), "one pass, not a chain"
    dry, wet = _samples(take, tmp_path), _samples(made, tmp_path)
    rate = 16000

    def lows_db(a: np.ndarray) -> float:
        spectrum = np.abs(np.fft.rfft(a))  # 1 s of audio: bin n is n Hz
        return 20 * np.log10(spectrum[145:156].max() + 1e-9)

    def second(at: float) -> slice:
        return slice(int(at * rate), int(at * rate) + rate)

    for at in (0.5, 3.3):
        assert lows_db(wet[second(at)]) < lows_db(dry[second(at)]) - 15, (
            f"the range around {at}s is treated"
        )
    assert float(np.corrcoef(dry[second(1.9)], wet[second(1.9)])[0, 1]) > 0.99, (
        "between the ranges the take is as filmed"
    )


def test_voice_fx_refuses_ranges_that_overlap(tmp_path: Path) -> None:
    from creation.post.voice_fx import apply_voice_fx

    with pytest.raises(ValueError, match="do not overlap"):
        apply_voice_fx(
            make_take(tmp_path / "take.mp4", seconds=3.0),
            ranges=[(0.5, 2.0), (1.5, 2.5)],
            preset="phone",
        )
