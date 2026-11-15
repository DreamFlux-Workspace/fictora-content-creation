"""Fork a desk's story into harness-flag arms, each on a new desk that writes episode 2 next (fake server).

fictora-drama #1727: ``POST /v1/spines/{id}/forks`` copies a story whose look, cast
and episode 1 were approved, with other harness flags. Nothing here calls the real server.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from creation import cli_produce, fork_story, orchestrate
from creation.episode_commands import run_author
from creation.fork_story import (
    FORK_FILENAME,
    FORK_SET_FILENAME,
    ForkRefused,
    arm_roles,
    fork_desk,
    fork_set,
    parse_arms,
)
from creation.harness_flags import HarnessFlagsRefused
from creation.production_config import (
    ProductionConfig,
    load_production_config,
    save_production_config,
)
from creation.production_state import load_production
from creation.shared_spine import shared_spine_refusal
from fake_api import FakeApi, spine_fixture

SOURCE_CONFIG = ProductionConfig(
    clip_duration_seconds=10,
    caption_style="subtle",
    voice_mode="model",
    delivery_format="letterbox",
    letterbox_caption_colour="white",
    spoken_language="ja",
    rules_epoch="2026-10-06",
    harness_flags={"tighter_hooks": False},
)


class _Client:
    def close(self) -> None:
        pass


class ForkServer:
    """Answers the fork route like fictora-drama #1727 and the attach routes like the operator routes."""

    def __init__(self, *, refuse: tuple[int, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, Any, str | None]] = []
        self.refuse = refuse
        self.forks: dict[str, dict[str, Any]] = {}
        self.client = _Client()

    def post_optional(
        self, path: str, body: Any, *, idempotency_key: str | None = None
    ):
        self.calls.append(("POST", path, body, idempotency_key))
        if path.endswith("/forks"):
            if self.refuse:
                return self.refuse
            fork_id = f"spine_fork_{len(self.forks) + 1:032x}"
            fork = self.forks.setdefault(
                idempotency_key or "",
                {
                    "spine_id": fork_id,
                    "source_spine_id": "sp1",
                    "spine_version": "sha256:" + "a" * 64,
                    "harness_flags": sorted(
                        name for name, on in (body["harness_flags"] or {}).items() if on
                    )
                    or None,
                },
            )
            return 201, fork
        return 200, {"owner_kind": "operator", "collaborators": [body["session_id"]]}

    def get(self, path: str):
        self.calls.append(("GET", path, None, None))
        spine = spine_fixture(episodes=1)
        spine.update(spine_id=path.rsplit("/", 1)[-1], rules_epoch="2026-10-06")
        return spine

    def get_optional(self, path: str):
        raise AssertionError(f"unexpected read {path}")

    def delete_optional(self, path: str):
        self.calls.append(("DELETE", path, None, None))
        return 200, {}

    def opener(self, _session_id: str, _out: Path) -> ForkServer:
        return self

    def sent(self, suffix: str) -> list[tuple[Any, str | None]]:
        return [
            (body, key)
            for method, path, body, key in self.calls
            if method == "POST" and path.endswith(suffix)
        ]


@pytest.fixture
def source(desk: Path) -> Path:
    save_production_config(desk, SOURCE_CONFIG)
    return desk


def _fork(
    source: Path,
    server: ForkServer,
    flags: dict[str, bool] | None,
    series: str = "Closing on",
) -> Any:
    out = io.StringIO()
    result = fork_desk(
        source, flags=flags, series=series, opener=server.opener, out=out
    )
    return result, out.getvalue()


def test_a_fork_desk_films_like_the_source_and_writes_episode_two_next(
    source: Path,
) -> None:
    server = ForkServer()

    result, printed = _fork(source, server, {"tighter_hooks": True})

    [(body, key)] = server.sent("/v1/spines/sp1/forks")
    assert body == {"harness_flags": {"tighter_hooks": True}}
    assert key and key.startswith("kit-fork-")
    desk = result.desk
    assert desk.parent == source.parent and desk != source
    state = load_production(desk)
    assert state.spine_id == result.spine_id != "sp1"
    assert (state.phase, state.episode_ordinal) == ("complete", 1)
    assert state.session_id != load_production(source).session_id
    assert state.video_lane == load_production(source).video_lane
    config = load_production_config(desk)
    assert {
        "clip_duration_seconds": config.clip_duration_seconds,
        "caption_style": config.caption_style,
        "voice_mode": config.voice_mode,
        "delivery_format": config.delivery_format,
        "letterbox_caption_colour": config.letterbox_caption_colour,
        "spoken_language": config.spoken_language,
    } == {
        "clip_duration_seconds": 10,
        "caption_style": "subtle",
        "voice_mode": "model",
        "delivery_format": "letterbox",
        "letterbox_caption_colour": "white",
        "spoken_language": "ja",
    }
    # The fork's flags are on its spine; the fork desk never drafts, so it sends none.
    assert config.harness_flags is None
    record = json.loads((desk / "api" / FORK_FILENAME).read_text())
    assert record["source_desk"] == str(source) and record["source_spine_id"] == "sp1"
    assert record["harness_flags_sent"] == {"tighter_hooks": True}
    assert record["harness_flags_on"] == ["tighter_hooks"] and record["created_at"]
    assert f"Idempotency-Key {key}" in printed
    assert f"author --desk {desk} --episode 2" in printed


def test_a_rerun_sends_nothing_and_answers_the_same_desk(source: Path) -> None:
    server = ForkServer()
    first, _ = _fork(source, server, {"tighter_hooks": True})

    again, printed = _fork(source, server, {"tighter_hooks": True})

    assert again.desk == first.desk and not again.made_now
    assert len(server.sent("/forks")) == 1 and "Already forked" in printed


def test_a_rerun_after_the_desk_failed_resends_the_same_key(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = ForkServer()

    def fail(*_a: Any, **_k: Any) -> Any:
        raise fork_story.AttachRefused("disk full")

    monkeypatch.setattr(fork_story, "attach_story", fail)
    with pytest.raises(ForkRefused, match="Run the same command again"):
        _fork(source, server, {"tighter_hooks": True})
    monkeypatch.undo()
    save_production_config(source, SOURCE_CONFIG)

    result, _ = _fork(source, server, {"tighter_hooks": True})

    keys = [key for _, key in server.sent("/forks")]
    assert len(keys) == 2 and keys[0] == keys[1]
    assert load_production(result.desk).spine_id == result.spine_id


def test_the_same_flags_on_another_series_is_another_fork(source: Path) -> None:
    server = ForkServer()

    off, _ = _fork(source, server, {"tighter_hooks": False}, series="off")
    off2, _ = _fork(source, server, {"tighter_hooks": False}, series="off2")

    assert off.spine_id != off2.spine_id
    assert len({key for _, key in server.sent("/forks")}) == 2


@pytest.mark.parametrize(
    ("status", "code", "said"),
    [
        (403, "operator_only", "only an operator desk session"),
        (422, "validation_error", "does not know one of the flag names"),
        (404, "spine_not_found", "does not show this story"),
        (409, "spine_fork_source_busy", "has a run going"),
        (409, "spine_fork_past_episode_one", "past episode 1"),
        (409, "spine_fork_legacy_rules", "legacy"),
        (409, "idempotency_conflict", "another body"),
    ],
)
def test_a_refusal_stops_before_any_desk(
    source: Path, status: int, code: str, said: str
) -> None:
    server = ForkServer(refuse=(status, {"error": {"code": code, "message": "no"}}))
    before = sorted(source.parent.iterdir())

    with pytest.raises(ForkRefused) as refused:
        _fork(source, server, {"tighter_hooks": True})

    assert said in str(refused.value) and "Nothing was made or charged" in str(
        refused.value
    )
    assert sorted(source.parent.iterdir()) == before
    assert server.sent("/collaborators") == []


def test_an_older_server_is_named(source: Path) -> None:
    server = ForkServer(refuse=(404, {"detail": "Not Found"}))

    with pytest.raises(ForkRefused, match="cannot fork a story"):
        _fork(source, server, None)


def test_a_fork_desk_writes_episode_two_then_steps_to_its_boards(
    source: Path, api: FakeApi
) -> None:
    result, _ = _fork(source, ForkServer(), {"tighter_hooks": True})
    fork = result.desk
    assert shared_spine_refusal("author", fork) is None
    assert shared_spine_refusal("step", fork) is None
    assert "--episode 2" in orchestrate.run_step(fork).message

    api.spine_doc = {**spine_fixture(episodes=2), "spine_id": result.spine_id}
    api.routes[("GET", f"/v1/spines/{result.spine_id}")] = lambda *_: api.spine_doc
    api.routes[("POST", f"/v1/spines/{result.spine_id}/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2"
    }
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    run_author(fork, episode=2, keep_voices=True)

    state = load_production(fork)
    assert (state.episode_ordinal, state.phase) == (2, "wait_script")
    api.routes[("POST", f"/v1/spines/{result.spine_id}/pilot-episodes/2/approve")] = (
        api.spine_doc
    )
    stepped = orchestrate.approve_gate(fork, gate="script")
    assert stepped.phase == "ready_boards_enrol"


def test_fork_set_records_each_arm_s_blind_eval_role(source: Path) -> None:
    server = ForkServer()
    arms = parse_arms(["off:all-off", "on:all-on", "off2:all-off"], ["tighter_hooks"])

    record = fork_set(
        source, arms=arms, series="Closing", opener=server.opener, out=io.StringIO()
    )

    assert [(row["arm"], row["role"]) for row in record["arms"]] == [
        ("off", "base"),
        ("on", "head"),
        ("off2", "base_repeat"),
    ]
    assert record["harness_flag"] == "tighter_hooks"
    assert len({row["spine_id"] for row in record["arms"]}) == 3
    for row in record["arms"]:
        assert (
            json.loads((Path(row["desk"]) / "api" / FORK_SET_FILENAME).read_text())
            == record
        )
        assert Path(row["desk"]).name.endswith(f"closing-{row['arm']}")


def test_arms_parse_explicit_specs_and_refuse_bad_ones() -> None:
    arms = parse_arms(["mix:a=on,b=off", "on:all-on"], ["a", "b"])
    assert arms[0].flags == {"a": True, "b": False}
    assert arm_roles(arms) == {"mix": None, "on": "head"}
    with pytest.raises(HarnessFlagsRefused, match="needs at least one --flag"):
        parse_arms(["off:all-off"], [])
    with pytest.raises(HarnessFlagsRefused, match="given twice"):
        parse_arms(["x:all-on", "x:all-off"], ["a"])
    with pytest.raises(HarnessFlagsRefused, match="NAME:all-off"):
        parse_arms(["off"], ["a"])


def test_the_fork_command_runs_on_a_source_that_shares_its_story(
    source: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = ForkServer()
    monkeypatch.setattr(fork_story, "open_session", server.opener)
    sibling = source.parent / "copy-desk"
    sibling.mkdir()
    (sibling / "production.json").write_text((source / "production.json").read_text())

    code = cli_produce.main(
        [
            "fork",
            "--desk",
            str(source),
            "--harness-flag",
            "tighter_hooks=on",
            "--series",
            "Hooks on",
        ]
    )

    assert code == 0, capsys.readouterr().err
    assert server.sent("/v1/spines/sp1/forks")[0][0] == {
        "harness_flags": {"tighter_hooks": True}
    }
