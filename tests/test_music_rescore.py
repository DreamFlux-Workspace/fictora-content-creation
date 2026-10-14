"""``rescore-music``: the harness re-scores made episodes with today's music rules; the kit rebuilds its file.

- Without ``--yes`` only a plan (``dry_run``) goes out: which takes change, which are skipped and why, the cost.
- ``--yes`` applies (``confirm``) under a key saved on the desk, then rebuilds each changed episode's file on
  this laptop: the new take saved as the next raw version, finish run again on it, join run for the episode,
  which writes the next readable ``<slug>_epNN_draft_vN`` file. Old files are never touched.
- ``--all`` runs the whole show; episodes are rebuilt in episode order.
- ``--revert`` calls the harness's revert and rebuilds the same way.
- A take the kit cannot finish again by itself keeps the episode's old file (said, exit 5).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fake_api import FakeApi

from creation.cli_produce import main
from creation.ops.folder import next_versioned_path
from creation.ops.state import load_series
from creation.post import music_rescore
from creation.post.episode_name import episode_files, next_episode_path
from creation.post.finish_record import write_finish_record

ROUTE = "/v1/spines/sp1/music-rescore"
OLD_FILE = "closing-time_ep{ep:02d}_draft_v1_2026-10-01.mp4"


def _row(ep: int, take: int, action: str = "rescore", **extra: Any) -> dict[str, Any]:
    return {
        "video_job_id": f"job_video_{ep}", "take_index": take, "take_job_id": f"job_take_e{ep}t{take}",
        "episode_id": f"episode_{ep:02d}", "episode_ordinal": ep, "take": take,
        "action": action, "reason": extra.pop("reason", None), "changed": False,
        "previous_music": {"laid": True, "version": 1, "file": "romance/tension_a.wav"},
        "music": {"laid": True, "version": 1, "file": "romance/tension_b.wav"}, **extra,
    }  # fmt: skip


def _answer(body: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    applied = bool(body.get("confirm"))
    revert = bool(body.get("revert"))
    takes = []
    for row in rows:
        row = dict(row)
        if revert and row["action"] == "rescore":
            row["action"] = "revert"
        if applied and row["action"] in ("rescore", "revert"):
            row.update(
                changed=True,
                video_url=f"https://media.test/{row['take_job_id']}-{'old' if revert else 'new'}.mp4",
            )
        takes.append(row)
    cues = 0 if revert else sum(1 for t in takes if t["action"] == "rescore")
    return {
        "spine_id": "sp1", "spine_version": "1.0.0", "kind": "revert" if revert else "rescore",
        "status": "applied" if applied else "planned", "message": "done" if applied else "planned",
        "rescore_id": "rs1" if applied else None, "episode_ordinal": body.get("episode_ordinal"),
        "takes": takes, "episodes": [],
        "cost": {"new_cue_count": cues, "new_cues_usd": 0.2 * cues, "spend_allowed": True,
                 "spent_usd": 0.2 * cues if applied else 0.0},
        "needs_confirmation": not applied, "job_id": "rs1" if applied else None,
    }  # fmt: skip


def _route(api: FakeApi, rows: list[dict[str, Any]]) -> None:
    api.routes[("POST", ROUTE)] = lambda _m, _p, body: _answer(body, rows)
    for row in rows:
        api.routes[("GET", f"/v1/jobs/{row['take_job_id']}/take-facts")] = {
            "take_facts": {"soundtrack": {"music": {"laid": True}}}
        }


def _posts(api: FakeApi) -> list[tuple[dict[str, Any], str | None]]:
    return [
        (body or {}, key)
        for method, path, body, key in api.calls
        if method == "POST" and path == ROUTE
    ]


def _made_episode(
    desk: Path, ep: int, takes: tuple[int, ...] = (1,), *, joined: bool = True
) -> None:
    """An episode the kit already finished and joined: clip record, raw takes, finish records, the file."""

    folder = desk / f"ep{ep:02d}"
    (folder / "api").mkdir(parents=True, exist_ok=True)
    (folder / "takes").mkdir(parents=True, exist_ok=True)
    clips = [
        {"job_id": f"job_take_e{ep}t{t}", "url": f"https://media.test/e{ep}t{t}.mp4", "set_index": t,
         "episode_id": f"episode_{ep:02d}"}
        for t in takes
    ]  # fmt: skip
    (folder / "api" / "17_raw_scene_clips.json").write_text(
        json.dumps({"clips": clips}), encoding="utf-8"
    )
    for t in takes:
        raw = folder / "takes" / f"take-ep{ep:02d}-t{t}-raw-v1.mp4"
        raw.write_bytes(b"old raw")
        final = folder / "takes" / f"take-ep{ep:02d}-t{t}-sokii-v1.mp4"
        final.write_bytes(b"old final")
        write_finish_record(
            desk, episode=ep, take_id=f"t{t}", complete=True, pre_bed=raw, master=final, final=final,
            bed=None, bed_db=0.0, duck_db=None, music_in_take=True,
        )  # fmt: skip
    if joined:
        (folder / "takes" / OLD_FILE.format(ep=ep)).write_bytes(b"old episode")


@pytest.fixture
def kit(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Fake the laptop work (download, finish, join); record what ran."""

    calls: dict[str, list[Any]] = {"download": [], "finish": [], "join": []}

    def download(url: str, directory: Path, stem: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = next_versioned_path(directory, stem, ".mp4")
        path.write_bytes(url.encode())
        calls["download"].append((url, path.name))
        return path

    def finish(
        desk: Path, episode: int, take_id: str, take_file: Path, colour: Any, out: Any
    ) -> tuple[bool, str]:
        calls["finish"].append((episode, take_id, take_file.name))
        return True, ""

    def join(desk: Path, episode: int, out: Any) -> tuple[Path | None, str]:
        folder = desk / f"ep{episode:02d}" / "takes"
        path = next_episode_path(folder, desk, episode, "draft")
        path.write_bytes(b"new episode")
        calls["join"].append((episode, path.name))
        return path, ""

    monkeypatch.setattr(music_rescore, "_download", download)
    monkeypatch.setattr(music_rescore, "_finish", finish)
    monkeypatch.setattr(music_rescore, "_join", join)
    return calls


def _rescore(desk: Path, *extra: str) -> int:
    return main(["rescore-music", "--desk", str(desk), *extra])


def test_without_yes_only_the_plan_goes_out(
    desk: Path,
    api: FakeApi,
    kit: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _made_episode(desk, 1, (1, 2))
    _route(
        api,
        [
            _row(1, 1),
            _row(1, 2, "skip", reason="its music was made by the video model"),
        ],
    )

    assert _rescore(desk, "--episode", "1") == 0

    assert [body for body, _ in _posts(api)] == [
        {"dry_run": True, "episode_ordinal": 1}
    ]
    text = capsys.readouterr().out
    assert "ep01 t1: gets the music today's rules give" in text
    assert "tension_a.wav -> music v1, tension_b.wav" in text
    assert "ep01 t2: skipped: its music was made by the video model" in text
    assert "Cost: $0.20 for 1 new cue version(s)" in text
    assert "Not applied: nothing was changed or spent. Applying costs $0.20" in text
    assert "rescore-music --desk" in text and "--episode 1 --yes" in text
    assert kit == {"download": [], "finish": [], "join": []}
    assert load_series(desk).spend_usd == 0


def test_yes_applies_then_rebuilds_a_new_episode_file_and_keeps_the_old(
    desk: Path,
    api: FakeApi,
    kit: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _made_episode(desk, 1, (1, 2))
    _route(
        api, [_row(1, 1), _row(1, 2, "skip", reason="no stored voices-and-room track")]
    )

    assert _rescore(desk, "--episode", "1", "--yes") == 0

    posts = _posts(api)
    assert [body for body, _ in posts] == [
        {"dry_run": True, "episode_ordinal": 1},
        {"confirm": True, "episode_ordinal": 1},
    ]
    [entry] = music_rescore.rescore_entries(desk)
    assert (
        posts[1][1] == entry["idempotency_key"]
        and entry["id"] in entry["idempotency_key"]
    )
    assert entry["status"] == "applied" and entry["rescore_id"] == "rs1"
    # Only the changed take: saved as the next raw version, finished on it, the episode joined.
    assert kit["download"] == [
        ("https://media.test/job_take_e1t1-new.mp4", "take-ep01-t1-raw-v2.mp4")
    ]
    assert kit["finish"] == [(1, "t1", "take-ep01-t1-raw-v2.mp4")]
    takes = desk / "ep01" / "takes"
    names = [p.name for p in episode_files(takes, 1)]
    assert names[0] == OLD_FILE.format(ep=1) and len(names) == 2
    assert names[1].startswith("closing-time_ep01_draft_v2_")
    assert (takes / OLD_FILE.format(ep=1)).read_bytes() == b"old episode"
    assert (takes / "take-ep01-t1-raw-v1.mp4").read_bytes() == b"old raw"
    text = capsys.readouterr().out
    assert "ep01 t2: skipped: no stored voices-and-room track" in text
    assert "ep01: rebuilt" in text and f"new episode file: {names[1]}" in text
    assert "--revert --rescore-id rs1" in text
    assert load_series(desk).spend_usd == pytest.approx(0.20)


def test_whole_show_goes_in_episode_order(
    desk: Path, api: FakeApi, kit: dict[str, list[Any]]
) -> None:
    _made_episode(desk, 1)
    _made_episode(desk, 2)
    # The harness answers in any order; the kit rebuilds episode 1 first.
    _route(api, [_row(2, 1), _row(1, 1)])

    assert _rescore(desk, "--all", "--yes") == 0

    assert [body for body, _ in _posts(api)] == [{"dry_run": True}, {"confirm": True}]
    assert [ep for ep, *_ in kit["finish"]] == [1, 2]
    assert [ep for ep, _ in kit["join"]] == [1, 2]


def test_revert_calls_the_harness_and_rebuilds_the_same_way(
    desk: Path,
    api: FakeApi,
    kit: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _made_episode(desk, 1)
    _route(api, [_row(1, 1)])

    assert (
        _rescore(desk, "--episode", "1", "--revert", "--rescore-id", "rs1", "--yes")
        == 0
    )

    assert [body for body, _ in _posts(api)] == [
        {"dry_run": True, "episode_ordinal": 1, "revert": True, "rescore_id": "rs1"},
        {"confirm": True, "episode_ordinal": 1, "revert": True, "rescore_id": "rs1"},
    ]
    assert kit["download"] == [
        ("https://media.test/job_take_e1t1-old.mp4", "take-ep01-t1-raw-v2.mp4")
    ]
    assert len(kit["join"]) == 1
    text = capsys.readouterr().out
    assert "the music it played before the re-score is put back, free" in text
    assert "Cost: free" in text


def test_a_take_finished_with_hand_sound_keeps_the_old_file(
    desk: Path,
    api: FakeApi,
    kit: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _made_episode(desk, 1)
    (desk / "ep01" / "takes" / "take-ep01-t1-voice-v1.mp4").write_bytes(b"hand voice")
    _route(api, [_row(1, 1)])

    assert _rescore(desk, "--episode", "1", "--yes") == music_rescore.REBUILD_NOT_DONE

    assert kit["finish"] == [] and kit["join"] == []
    # The new take is still saved, so the next finish by hand reads the new music.
    assert [name for _, name in kit["download"]] == ["take-ep01-t1-raw-v2.mp4"]
    assert [p.name for p in episode_files(desk / "ep01" / "takes", 1)] == [
        OLD_FILE.format(ep=1)
    ]
    text = capsys.readouterr().out
    assert "ep01: !! NOT rebuilt, the old file stays" in text
    assert "hand-placed sound or words (voice)" in text


def test_a_failed_refinish_keeps_the_old_file(
    desk: Path, api: FakeApi, kit: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _made_episode(desk, 1)
    _route(api, [_row(1, 1)])
    monkeypatch.setattr(
        music_rescore, "_finish", lambda *a: (False, "finish NOT DONE (no SFX)")
    )

    assert _rescore(desk, "--episode", "1", "--yes") == music_rescore.REBUILD_NOT_DONE

    assert kit["join"] == []
    assert [p.name for p in episode_files(desk / "ep01" / "takes", 1)] == [
        OLD_FILE.format(ep=1)
    ]


def test_an_episode_never_joined_here_has_nothing_to_rebuild(
    desk: Path,
    api: FakeApi,
    kit: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _made_episode(desk, 1, joined=False)
    _route(api, [_row(1, 1)])

    assert _rescore(desk, "--episode", "1", "--yes") == 0

    assert kit["finish"] == [] and kit["join"] == []
    assert "ep01: nothing to rebuild" in capsys.readouterr().out


def test_a_dropped_apply_is_sent_again_under_the_same_key(
    desk: Path, api: FakeApi, kit: dict[str, list[Any]]
) -> None:
    _made_episode(desk, 1)
    rows = [_row(1, 1)]
    _route(api, rows)

    def dropped(_m: str, _p: str, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("confirm"):
            raise SystemExit("HTTP 503 upstream")
        return _answer(body, rows)

    api.routes[("POST", ROUTE)] = dropped
    assert _rescore(desk, "--episode", "1", "--yes") == music_rescore.NOT_APPLIED
    [entry] = music_rescore.rescore_entries(desk)
    assert entry["status"] == "sending"

    _route(api, rows)
    assert _rescore(desk, "--episode", "1", "--yes") == 0

    keys = [key for body, key in _posts(api) if body.get("confirm")]
    assert len(keys) == 2 and keys[0] == keys[1]
    assert [e["status"] for e in music_rescore.rescore_entries(desk)] == ["applied"]


def test_flags_are_checked_before_anything_is_sent(desk: Path, api: FakeApi) -> None:
    assert _rescore(desk, "--episode", "1", "--rescore-id", "rs1") == 2
    assert _posts(api) == []
