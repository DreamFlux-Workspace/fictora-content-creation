"""Per-story harness flags on the draft, an old story replayed as a new one, and the base/head pair (fake server).

The drama server pins a story's harness flags when the story is drafted (fictora-drama
``docs/runbooks/harness-flags.md``). The founder's checkpoint films one old story twice, flags
off and on, and compares the two blind. Nothing here calls the real server or spends.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import cli_produce, orchestrate, story_start
from creation.harness.stages_gated import draft_brief_hash, draft_request_body
from creation.harness_flags import HarnessFlagsRefused, parse_harness_flags
from creation.opening_action import DRAFT_SCHEMA
from creation.production_config import (
    ProductionConfig,
    load_production_config,
    save_production_config,
)
from creation.production_state import load_production, save_production
from fake_api import FakeApi, openapi_doc

DRAFTS = "/v1/prompt-video-authoring-drafts"
FLAG = "tighter_hooks"
OLD_SPINE = "spine_sokii_old"


def _doc(*, takes: bool) -> dict[str, Any]:
    doc = openapi_doc()
    doc["components"]["schemas"][DRAFT_SCHEMA] = {
        "properties": {"prompt": {}, **({"harness_flags": {}} if takes else {})}
    }
    return doc


def _new_desk(desk: Path, api: FakeApi, flags: dict[str, bool] | None) -> None:
    save_production_config(desk, ProductionConfig(harness_flags=flags))
    api.spine_doc = {
        **api.spine_doc,
        "episode_summaries": api.spine_doc["episode_summaries"][:1],
    }
    api.routes[("POST", DRAFTS)] = {"plan_job_id": "job_plan", "spine_id": "sp1"}
    api.jobs["job_plan"] = {"status": "completed"}
    set_phase(desk, "new", spine_id=None)


# --- the flag and the draft -------------------------------------------------------------------


def test_flags_parse_as_on_off_and_refuse_typos_and_contradictions() -> None:
    assert parse_harness_flags(["b_flag=OFF", "a_flag=on", "a_flag=on"]) == {
        "a_flag": True,
        "b_flag": False,
    }
    assert parse_harness_flags([]) is None
    for bad in (["a_flag"], ["a_flag=yes"], ["A-Flag=on"]):
        with pytest.raises(HarnessFlagsRefused):
            parse_harness_flags(bad)
    with pytest.raises(HarnessFlagsRefused, match="both on and off"):
        parse_harness_flags(["a_flag=on", "a_flag=off"])


def test_a_draft_without_flags_is_byte_for_byte_the_old_body() -> None:
    old = draft_request_body(prompt="p", preset_id="x", preset_version="1")
    assert (
        draft_request_body(
            prompt="p", preset_id="x", preset_version="1", harness_flags=None
        )
        == old
    )
    assert draft_brief_hash(
        draft_request_body(
            prompt="p", preset_id="x", preset_version="1", harness_flags={}
        )
    ) == draft_brief_hash(old), "a changed hash would re-draft every existing desk"
    off = draft_request_body(
        prompt="p", preset_id="x", preset_version="1", harness_flags={FLAG: False}
    )
    on = draft_request_body(
        prompt="p", preset_id="x", preset_version="1", harness_flags={FLAG: True}
    )
    assert off["harness_flags"] == {FLAG: False}
    assert (
        len({draft_brief_hash(old), draft_brief_hash(off), draft_brief_hash(on)}) == 3
    ), "each arm is its own draft under its own key"


def test_step_sends_the_desk_s_flags_on_the_draft_under_a_key_that_carries_them(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _new_desk(desk, api, {FLAG: True})
    api.routes[("GET", "/openapi.json")] = _doc(takes=True)

    orchestrate.run_step(desk)

    ((_, _, body, key),) = [c for c in api.calls if c[:2] == ("POST", DRAFTS)]
    assert body["harness_flags"] == {FLAG: True}
    assert f"-b{draft_brief_hash(body)}-draft" in str(key), (
        "a resumed step rebuilds this body from the desk config and sends the same key"
    )
    assert (
        "Harness flags on this story's draft: tighter_hooks=on"
        in capsys.readouterr().out
    )


def test_a_server_whose_draft_takes_no_flags_stops_before_sending(
    desk: Path, api: FakeApi
) -> None:
    _new_desk(desk, api, {FLAG: True})
    api.routes[("GET", "/openapi.json")] = _doc(takes=False)

    with pytest.raises((SystemExit, RuntimeError), match="does not take harness flags"):
        orchestrate.run_step(desk)

    assert api.posted(DRAFTS) == []


@pytest.mark.parametrize(
    ("status", "error", "why"),
    [
        (
            403,
            {"code": "harness_flags_operator_only", "message": "operator setting"},
            "operator desk",
        ),
        (
            422,
            {
                "code": "invalid_request",
                "message": "unknown harness flags: ['tighter_hook']",
            },
            "does not know",
        ),
    ],
)
def test_a_server_refusal_of_the_flags_stops_with_nothing_drafted(
    desk: Path, api: FakeApi, status: int, error: dict[str, str], why: str
) -> None:
    _new_desk(desk, api, {FLAG: True})
    api.routes[("GET", "/openapi.json")] = _doc(takes=True)
    api.routes[("POST", DRAFTS)] = (status, {"error": error})

    with pytest.raises(
        SystemExit,
        match=f"refused the harness flags.*{why}.*Nothing was drafted or charged",
    ):
        orchestrate.run_step(desk)

    assert load_production(desk).spine_id is None
    assert api.jobs["job_plan"] == {"status": "completed"}, "no plan job was polled"


def test_start_keeps_the_flags_and_prints_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli_produce, "resolve_preset", lambda preset_id: (preset_id, "1")
    )
    code = cli_produce.main(
        [
            "start",
            "--series",
            "Flagged",
            "--prompt",
            "A door.",
            "--parent",
            str(tmp_path),
            "--harness-flag",
            f"{FLAG}=on",
            "--harness-flag",
            "other_change=off",
        ]
    )

    assert code == 0
    (desk,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert load_production_config(desk).harness_flags == {
        "other_change": False,
        FLAG: True,
    }
    assert (
        "Harness flags (sent on this story's draft): other_change=off, tighter_hooks=on"
        in capsys.readouterr().out
    )


# --- replaying an old story ---------------------------------------------------------------------

OLD_STORY = {
    "spine_id": OLD_SPINE,
    "title": "Sokii Night Market",
    "scene_prompt_text": "Mina sells umbrellas at the night market.\nLines:\nMina: Rain again.",
    "scene_prompt_normalized": "Mina sells umbrellas at the night market. Lines: Mina: Rain again.",
    "art_style_preset_id": "anime-soft",
    "art_style_preset_version": "v3",
    "duration_band": "30s",
    "spoken_language": "ja-JP",
    "delivery_format": "letterbox",
    "video_lane": "wan",
}


class _Client:
    def close(self) -> None:
        pass


class _Reader:
    """Answers ``GET /v1/spines/{id}`` for the old desk's session; anything else is a test failure."""

    def __init__(self, spine: dict[str, Any], status: int = 200) -> None:
        self.spine, self.status = spine, status
        self.client = _Client()
        self.calls: list[str] = []

    def get_optional(self, path: str) -> tuple[int, Any]:
        self.calls.append(path)
        assert path == f"/v1/spines/{OLD_SPINE}"
        return self.status, self.spine


@pytest.fixture
def old_desk(desk: Path) -> Path:
    state = load_production(desk)
    state.spine_id = OLD_SPINE
    state.phase = "complete"
    save_production(desk, state)
    save_production_config(
        desk,
        ProductionConfig(
            caption_style="subtle",
            voice_mode="model",
            lane="mystery",
            rules_epoch="legacy",
        ),
    )
    return desk


def _opener(reader: _Reader, seen: list[str]):
    def open_(session_id: str, _out: Path) -> _Reader:
        seen.append(session_id)
        return reader

    return open_


def test_a_replay_reads_the_old_story_s_inputs_from_its_spine(old_desk: Path) -> None:
    seen: list[str] = []
    reader = _Reader(OLD_STORY)

    for source_arg in (str(old_desk), OLD_SPINE):
        source = story_start.read_replay_source(
            source_arg, parent=old_desk.parent, opener=_opener(reader, seen)
        )
        inputs = source.inputs
        assert source.desk == old_desk.resolve()
        assert inputs.prompt == OLD_STORY["scene_prompt_text"], (
            "the brief with its line breaks"
        )
        assert inputs.preset == ("anime-soft", "v3")
        assert (inputs.band, inputs.video_lane) == ("30s", "wan")
        assert (inputs.config.spoken_language, inputs.config.delivery_format) == (
            "ja-JP",
            "letterbox",
        )
        assert (inputs.config.caption_style, inputs.config.voice_mode) == (
            "subtle",
            "model",
        )
        assert inputs.config.lane is None, "posting fields stay with the old show"
    assert seen == [load_production(old_desk).session_id] * 2


def test_a_replay_falls_back_to_the_local_brief(old_desk: Path) -> None:
    bare = {k: v for k, v in OLD_STORY.items() if not k.startswith("scene_prompt")}
    source = story_start.read_replay_source(
        str(old_desk), parent=old_desk.parent, opener=_opener(_Reader(bare), [])
    )
    assert source.inputs.prompt == "A shop at closing time."
    assert source.prompt_from == "the old desk's local brief"


def test_a_story_no_desk_holds_is_refused_with_the_attach_command(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        story_start.ReplayRefused, match="start --spine-id spine_elsewhere"
    ):
        story_start.read_replay_source(
            "spine_elsewhere", parent=tmp_path, opener=_opener(_Reader({}), [])
        )


def test_start_from_story_makes_a_new_undrafted_desk_with_the_old_inputs_and_the_flags(
    old_desk: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(story_start, "open_session", _opener(_Reader(OLD_STORY), []))
    parent = tmp_path / "replays"

    code = cli_produce.main(
        [
            "start",
            "--from-story",
            str(old_desk),
            "--series",
            "Sokii replay",
            "--parent",
            str(parent),
            "--harness-flag",
            f"{FLAG}=on",
        ]
    )

    assert code == 0, capsys.readouterr().err
    (desk,) = list(parent.iterdir())
    state, config = load_production(desk), load_production_config(desk)
    assert state.spine_id is None and state.phase == "new", (
        "a NEW story, drafted by the next step"
    )
    assert state.session_id != load_production(old_desk).session_id
    assert (state.preset_id, state.preset_version, state.band, state.video_lane) == (
        "anime-soft",
        "v3",
        "30s",
        "wan",
    )
    assert config.harness_flags == {FLAG: True}
    assert config.rules_epoch != "legacy", "a new story runs the current rules"
    out = capsys.readouterr().out
    assert f"Replaying story {OLD_SPINE}" in out and "tighter_hooks=on" in out


def test_start_from_story_refuses_inputs_that_would_change_the_replay(
    old_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_produce.main(
        ["start", "--from-story", str(old_desk), "--preset-id", "x", "--band", "60s"]
    )

    assert code == 2
    assert "leave out --preset-id, --band" in capsys.readouterr().err


# --- the pair ------------------------------------------------------------------------------------


def test_the_pair_makes_a_base_desk_with_flags_off_and_a_head_desk_with_them_on(
    old_desk: Path, tmp_path: Path
) -> None:
    source = story_start.read_replay_source(
        str(old_desk), parent=old_desk.parent, opener=_opener(_Reader(OLD_STORY), [])
    )
    parent = tmp_path / "pairs"

    base, head = story_start.start_harness_pair(
        source,
        parent=parent,
        series="Sokii",
        flag_names=[FLAG, FLAG],
        out=io.StringIO(),
    )

    assert load_production_config(base).harness_flags == {FLAG: False}
    assert load_production_config(head).harness_flags == {FLAG: True}
    assert load_production(base).prompt == load_production(head).prompt
    record = json.loads(
        (base / "api" / story_start.PAIR_FILENAME).read_text(encoding="utf-8")
    )
    assert record == json.loads(
        (head / "api" / story_start.PAIR_FILENAME).read_text(encoding="utf-8")
    )
    assert record["source_spine_id"] == OLD_SPINE
    assert record["harness_flag"] == FLAG
    assert (record["base"]["desk"], record["head"]["desk"]) == (str(base), str(head))
    assert list(parent.iterdir()) and not [
        p for p in parent.iterdir() if p not in (base, head)
    ]
