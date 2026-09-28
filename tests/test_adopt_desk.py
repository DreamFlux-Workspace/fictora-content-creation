"""``fictora-ops adopt-desk``: a desk made by the retired internal kit opens here, and nothing is lost."""

from __future__ import annotations

import json
import pathlib
from pathlib import Path
from typing import Any

import pytest

from creation.cli_ops import main as ops_main
from creation.harness.raw_video import episode_clips
from creation.ops.adopt import AdoptRefused, adopt_desk
from creation.ops.floor import record_verdict
from creation.ops.state import load_series, save_series
from creation.post.desk import latest_raw_take, take_clip
from creation.production_config import load_production_config
from creation.production_state import load_production

SECRET = "SENTINEL-PROVIDER-SPEC-never-copy-me"
DECOYS = (
    "take-ep01-t1-colour-v1.mp4",
    "take-ep01-t1-mix-v1.mp4",
    "take-ep01-t1-cap-v1.mp4",
    "take-ep01-t1-deboard-v1.mp4",
    "take-ep01-t1-sfx-v1.mp4",
    "take-ep01-t1-mix-v1-raw-bus.wav",
    "take-ep01-api-captioned-v1.mp4",
    "take-ep01-t1-v1.mov",
    "take-ep01-t1-v1-last-frame.mp4",
)
URL_1 = "https://media.example/clip/ep1-second-film.mp4"
URL_2 = "https://media.example/clip/ep2-first-film.mp4"


def _gate(status: str = "pending", path: str | None = None) -> dict[str, Any]:
    return {"status": status, "at_utc": None, "path": path, "note": None, "luma_percent": None}


def _take(take_id: str, *, board: str = "pending", filmed: int = 0, lines: int = 1) -> dict[str, Any]:
    return {
        "take_id": take_id,
        "lines": [{"speaker": "Hana", "original": f"Line {n}.", "translation": ""} for n in range(lines)],
        "board": _gate(board),
        "estimate_usd": 0.3 if filmed else None,
        "filmed_count": filmed,
        "verdict": "pending",
        "change_cause": None,
        "handoff_path": None,
        "spend_usd": 0.3 * filmed,
        "overrides": [],
        "legacy_take_note": {"kept": True},
    }


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def internal_desk(root: Path, *, ep2_filmed: bool = True) -> Path:
    """A desk laid out the way the internal kit left it (synthetic values)."""

    desk = root / "2026-09-26-closing-time"
    series = {
        "schema_version": "fictora.content-ops.series.v1",
        "title": "Closing Time",
        "slug": "closing-time",
        "band": "15s",
        "day": "2026-09-26",
        "continuing": False,
        "look": _gate("approved", "shared/look/look-frame-v1.png"),
        "plates": _gate("approved", "shared/plates/plates-contact-v1.png"),
        "spend_usd": 3.0,
        "episodes": [
            {
                "ordinal": 1,
                "slug": "ep01",
                "script": _gate("pending"),  # the old kit never recorded it, but the take was filmed
                "post": _gate(),
                "takes": [_take("t1", board="approved", filmed=2)],
                "spend_usd": 2.4,
                "legacy_episode_note": "kept",
            },
            {
                "ordinal": 2,
                "slug": "ep02",
                "script": _gate("approved"),
                "post": _gate(),
                "takes": [_take("t1", board="approved" if ep2_filmed else "pending", filmed=1 if ep2_filmed else 0)],
                "spend_usd": 0.6,
            },
        ],
        "bed_path": "shared/beds/show-bed-v1.mp3",
        "bed_db": -16.5,
        "spine_id": "spine_test_1",
        "api_session_id": "create-flow-test",
        "arc_options": [{"arc_id": "arc_1", "title": "The Queue", "line": "Every night a bigger scheme."}],
        "series_arc": {
            "arc": {"arc_id": "arc_1", "title": "The Queue", "line": "Every night a bigger scheme."},
            "option": 1,
            "rewritten": False,
            "spine_version": "sha256:abc",
            "at_utc": "2026-09-26T17:50:38+00:00",
        },
        "api": {
            "idempotency_prefix": "closing-time-abc123",
            "spine_version": "sha256:abc",
            "draft_job_id": "job_video_plan_1",
            "preset_id": "slice-of-life",
            "preset_version": None,
            "cut_tempo": "punchy",
            "video_lane": "minimax-h3",
            "spoken_language": "ja-JP",
            "pending": {} if ep2_filmed else {"boards-ep02": {"key": "closing-time-abc123-boards-ep02-a1", "job_id": None}},
            "attempts": {"draft": 1, "take-ep01-t1": 2},
            "renders": {"draft": 1},
            "board_frames": {"boards-ep01-t1": "0123456789abcdef"},
        },
        "spend_log": [{"at_utc": "2026-09-26T18:19:05+00:00", "episode": 1, "usd": 0.3, "unit": "board", "take_id": "t1"}],
    }
    _write(desk / "series.json", series)
    spine = {
        "spine_id": "spine_test_1",
        "spine_version": "sha256:abc",
        "spoken_language": "ja-JP",
        "cut_tempo": "punchy",
        "video_lane": "minimax-h3",
        "art_style_preset_id": "slice-of-life",
        "art_style_preset_version": "1.2.0",
        "episode_summaries": [
            {"episode_id": "episode_01", "ordinal": 1, "authoring_state": "approved"},
            {"episode_id": "ep_02", "ordinal": 2, "authoring_state": "approved"},
        ],
        "beats": [
            {"beat_id": "b1", "episode_id": "episode_01", "dialogue_lines": [{"line_id": "l1", "cast_id": "cast_hana", "text": "Closed."}]},
            {"beat_id": "b2", "episode_id": "ep_02", "dialogue_lines": [{"line_id": "l2", "cast_id": "cast_hana", "text": "Again?"}]},
        ],
    }
    _write(desk / "api" / "spine.json", spine)
    _write(
        desk / "api" / "draft-request.json",
        {"prompt": "A sweet shop at closing time.\nMore brief.", "art_style_preset_id": "slice-of-life", "cut_tempo": "punchy",
         "spoken_language": "ja-JP", "locale": "en-US"},
    )
    rows = [
        {"episode_id": "episode_01", "take_index": 1, "scene_id": "sb_ep1", "take_job_id": "job_video_scene_ep1",
         "video_url": URL_1, "duration_ms": 15104, "path": "/x/.incoming/take-episode_01-t1.mp4", "provider_spec": SECRET},
    ]
    if ep2_filmed:
        rows.append({"episode_id": "ep_02", "take_index": 1, "scene_id": "sb_ep2", "take_job_id": "job_video_scene_ep2",
                     "video_url": URL_2, "duration_ms": 15104, "path": "/x/.incoming/take-ep_02-t1.mp4", "provider_spec": SECRET})
    _write(desk / "api" / "18_takes.json", rows)
    for folder in ("api", "ep01/api", "ep02/api"):  # the old kit left these; nothing may open them
        _write(desk / folder / f"provider-spec-{folder.split('/')[0]}-t1-v1.json", {"spec": SECRET})
        _write(desk / folder / f"18_provider_spec-{folder.split('/')[0]}.json", {"spec": SECRET})
    _write(
        desk / "ep01" / "api" / "video-terminal-ep01-t1-v2.json",
        {"job_id": "job_video_coord_ep1", "status": "completed", "takes": [],
         "output": {"scenes": [{"episode_id": "episode_01", "video_url": URL_1}]}},
    )
    takes = desk / "ep01" / "takes"
    takes.mkdir(parents=True)
    (takes / "take-ep01-t1-v1.mp4").write_bytes(b"first film")
    (takes / "take-ep01-t1-v2.mp4").write_bytes(b"second film")
    for name in DECOYS:
        (takes / name).write_bytes(b"post step")
    (desk / "ep01" / "boards").mkdir(parents=True)
    (desk / "ep01" / "boards" / "board-ep01-t1-v3.png").write_bytes(b"png")
    (desk / "ep02" / "api").mkdir(parents=True, exist_ok=True)
    (desk / "ep02" / "takes").mkdir(parents=True)
    if ep2_filmed:
        (desk / "ep02" / "takes" / "take-ep02-t1-v1.mp4").write_bytes(b"ep2 film")
    return desk


def snapshot(desk: Path) -> dict[str, tuple[int, int, int]]:
    """Every file with its inode, size and mtime."""

    return {
        str(p.relative_to(desk)): (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(desk.rglob("*"))
        if p.is_file()
    }


def forbid_provider_spec_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """From here on, opening a provider-spec file fails the test."""

    real_open = pathlib.Path.open

    def guarded(self: Path, *args: Any, **kwargs: Any) -> Any:
        if "provider-spec" in self.name or "provider_spec" in self.name:
            raise AssertionError(f"opened {self}")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", guarded)


def test_raw_takes_are_hardlinked_under_this_kits_name_and_nothing_else_matches(tmp_path: Path) -> None:
    desk = internal_desk(tmp_path)
    before = snapshot(desk)
    adopt_desk(desk)
    takes = desk / "ep01" / "takes"
    for version in (1, 2):
        original = takes / f"take-ep01-t1-v{version}.mp4"
        raw = takes / f"take-ep01-t1-raw-v{version}.mp4"
        assert raw.samefile(original)
    assert (desk / "ep02" / "takes" / "take-ep02-t1-raw-v1.mp4").samefile(desk / "ep02" / "takes" / "take-ep02-t1-v1.mp4")
    raws = sorted(p.name for p in takes.glob("*raw-v*.mp4"))
    assert raws == ["take-ep01-t1-raw-v1.mp4", "take-ep01-t1-raw-v2.mp4"]
    after = snapshot(desk)
    for name, (inode, size, mtime) in before.items():  # nothing renamed, moved, rewritten
        assert name in after and after[name][0] == inode and after[name][1:] == (size, mtime)


def test_an_existing_different_raw_file_is_left_alone(tmp_path: Path) -> None:
    desk = internal_desk(tmp_path)
    squatter = desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4"
    squatter.write_bytes(b"something else")
    plan, _ = adopt_desk(desk)
    assert squatter.read_bytes() == b"something else"
    assert any("take-ep01-t1-raw-v1.mp4" in item for item in plan.left_alone)


def test_the_take_list_reads_through_this_kits_readers(tmp_path: Path) -> None:
    desk = internal_desk(tmp_path)
    adopt_desk(desk)
    assert latest_raw_take(desk, 1, "t1").name == "take-ep01-t1-raw-v2.mp4"
    assert take_clip(desk, 1, "t1") == {
        "job_id": "job_video_scene_ep1", "url": URL_1, "relation_id": None, "set_index": 1, "episode_id": "episode_01",
    }
    raw_1 = json.loads((desk / "ep01" / "api" / "17_raw_scene_clips.json").read_text())
    assert raw_1["coordinator_job_id"] == "job_video_coord_ep1"
    raw_2 = json.loads((desk / "ep02" / "api" / "17_raw_scene_clips.json").read_text())
    assert [c["job_id"] for c in episode_clips(raw_2, episode_id="ep_02")] == ["job_video_scene_ep2"]
    assert take_clip(desk, 2, "t1")["url"] == URL_2  # ep_02 found via the spine copied into ep02/api


def test_production_json_loads_and_carries_the_old_desk(tmp_path: Path) -> None:
    desk = internal_desk(tmp_path)
    adopt_desk(desk)
    state = load_production(desk)
    assert (state.session_id, state.spine_id, state.preset_id, state.preset_version) == (
        "create-flow-test", "spine_test_1", "slice-of-life", "1.2.0",
    )
    assert (state.episode_ordinal, state.phase, state.band) == (2, "complete", "15s")
    assert state.idempotency_prefix == "closing-time-abc123"
    assert state.attempts == {"draft": 1, "take-ep01-t1": 2}
    assert state.series_arc == {
        "arc_id": "arc_1", "title": "The Queue", "line": "Every night a bigger scheme.",
        "option": 1, "rewritten": False, "spine_version": "sha256:abc",
    }
    assert state.prompt.startswith("A sweet shop at closing time.")
    config = load_production_config(desk)
    assert (config.cut_tempo, config.spoken_language, config.locale) == ("punchy", "ja-JP", "en-US")


def test_phase_waits_on_boards_for_a_scripted_unboarded_episode(tmp_path: Path) -> None:
    desk = internal_desk(tmp_path, ep2_filmed=False)
    plan, _ = adopt_desk(desk)
    state = load_production(desk)
    assert (state.episode_ordinal, state.phase) == (2, "ready_boards_enrol")
    assert any("boards-ep02" in line for line in plan.confirm)


def test_a_filmed_episode_with_a_pending_script_record_is_complete_and_asks(tmp_path: Path) -> None:
    desk = internal_desk(tmp_path, ep2_filmed=False)
    series = json.loads((desk / "series.json").read_text())
    series["episodes"] = series["episodes"][:1]
    _write(desk / "series.json", series)
    plan, _ = adopt_desk(desk)
    assert (load_production(desk).episode_ordinal, load_production(desk).phase) == (1, "complete")
    assert any("script is still pending" in line and "filmed" in line for line in plan.confirm)


def test_unknown_series_fields_survive_load_and_save(tmp_path: Path) -> None:
    desk = internal_desk(tmp_path)
    original = json.loads((desk / "series.json").read_text())
    save_series(desk, load_series(desk))
    assert json.loads((desk / "series.json").read_text()) == original
    record_verdict(desk, episode=1, take_id="t1", verdict="use")
    saved = json.loads((desk / "series.json").read_text())
    assert saved["episodes"][0]["takes"][0]["verdict"] == "use"
    for key in ("spend_log", "bed_db", "api", "series_arc", "api_session_id", "spine_id", "arc_options"):
        assert saved[key] == original[key]
    assert saved["episodes"][0]["legacy_episode_note"] == "kept"
    assert saved["episodes"][0]["takes"][0]["legacy_take_note"] == {"kept": True}


def test_running_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    desk = internal_desk(tmp_path)
    first, made = adopt_desk(desk)
    assert made and (desk / "series.pre-adopt.json").is_file()
    between = snapshot(desk)
    second, again = adopt_desk(desk)
    assert again == [] and second.actions == []
    assert snapshot(desk) == between


def test_dry_run_prints_every_file_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    desk = internal_desk(tmp_path)
    before = snapshot(desk)
    assert ops_main(["adopt-desk", "--desk", str(desk), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert snapshot(desk) == before
    for name in ("series.pre-adopt.json", "take-ep01-t1-raw-v1.mp4", "take-ep01-t1-raw-v2.mp4", "ep01/api/17_raw_scene_clips.json",
                 "ep02/api/17_raw_scene_clips.json", "ep02/api/spine.json", "production.json", "production.config.json"):
        assert name in out
    assert "phase" in out and "complete" in out and "CONFIRM" in out


def test_refuses_to_overwrite_production_json_and_force_backs_it_up(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    desk = internal_desk(tmp_path)
    (desk / "production.json").write_text('{"session_id": "mine", "prompt": "p", "preset_id": "x", "preset_version": "1"}\n')
    before = snapshot(desk)
    with pytest.raises(AdoptRefused):
        adopt_desk(desk)
    assert ops_main(["adopt-desk", "--desk", str(desk)]) == 2
    assert snapshot(desk) == before
    capsys.readouterr()
    assert ops_main(["adopt-desk", "--desk", str(desk), "--force"]) == 0
    assert json.loads((desk / "production.pre-adopt-v1.json").read_text())["session_id"] == "mine"
    assert load_production(desk).session_id == "create-flow-test"


def test_provider_spec_never_read_copied_or_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    desk = internal_desk(tmp_path)
    forbid_provider_spec_reads(monkeypatch)
    assert ops_main(["adopt-desk", "--desk", str(desk)]) == 0
    assert SECRET not in capsys.readouterr().out
    for path in desk.rglob("*.json"):
        if path.name in {"18_takes.json", "series.pre-adopt.json"} or "provider" in path.name:
            continue
        text = path.read_text()
        assert SECRET not in text and "provider_spec" not in text, path
