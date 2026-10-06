"""Reels made by themselves after every finish and every edit that writes a new deliverable (6 Oct 2026).

Per-episode folder ``reels/epNN/``, a draft name while takes are missing, a skip when nothing
changed, ``latest.json``, a hand-edited plan reused while its takes are unchanged, and one
results-sheet row per episode.
"""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import needs_ffmpeg
from test_reel import reel_desk  # noqa: F401  (fixture)

from creation.post.reel import auto_reel, reel_paths


def _rows(desk: Path) -> list[dict[str, str]]:
    return list(csv.DictReader((desk / "reels" / "metrics.csv").open(encoding="utf-8")))


def _latest(desk: Path, episode: int = 1) -> dict[str, Any]:
    return json.loads(
        (desk / "reels" / f"ep{episode:02d}" / "latest.json").read_text(
            encoding="utf-8"
        )
    )


def test_reel_paths_go_in_the_episode_folder_and_count_on_from_flat_reels(
    tmp_path: Path,
) -> None:
    flat = tmp_path / "reels"
    flat.mkdir()
    (flat / "reel-ep01-v2.mp4").write_bytes(b"")  # an older desk's flat reel
    paths = reel_paths(tmp_path, 1)
    assert paths["video"] == tmp_path / "reels" / "ep01" / "reel-ep01-v3.mp4"
    assert paths["post"].name == "post-ep01-v3.txt"
    draft = reel_paths(tmp_path, 1, draft=True)
    assert draft["video"].name == "reel-ep01-draft-v3.mp4"
    assert draft["plan"].name == "reel-plan-ep01-draft-v3.json"


@needs_ffmpeg
def test_auto_reel_writes_the_episode_folder_latest_json_and_one_metrics_row(
    reel_desk: Path,  # noqa: F811
) -> None:
    out = io.StringIO()
    result = auto_reel(reel_desk, 1, trigger="finish", stream=out)

    assert result is not None and result.video is not None and result.cover is not None
    folder = reel_desk / "reels" / "ep01"
    assert result.video.parent == folder and result.cover.parent == folder
    assert result.post is not None and result.post.parent == folder
    latest = _latest(reel_desk)
    assert latest["reel"] == "reels/ep01/reel-ep01-v1.mp4"
    assert latest["cover"] == f"reels/ep01/{result.cover.name}"
    assert latest["post"] == "reels/ep01/post-ep01-v1.txt"
    assert latest["draft"] is False and latest["made_by"] == "finish"
    assert len(_rows(reel_desk)) == 1

    # Nothing changed: the next finish says so in one line and cuts nothing.
    again = io.StringIO()
    assert auto_reel(reel_desk, 1, trigger="finish", stream=again) is None
    assert "unchanged" in again.getvalue()
    assert len(again.getvalue().strip().splitlines()) == 1
    assert sorted(p.name for p in folder.glob("reel-ep01-v*.mp4")) == [
        "reel-ep01-v1.mp4"
    ]

    # The finished take changes: a new reel, still one row for the episode, the old reel superseded.
    pre_bed = reel_desk / "ep01" / "takes" / "take-ep01-t1-colour-v1.mp4"
    stat = pre_bed.stat()
    os.utime(pre_bed, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    third = auto_reel(reel_desk, 1, trigger="trim", stream=io.StringIO())
    assert (
        third is not None
        and third.video is not None
        and third.video.name == "reel-ep01-v2.mp4"
    )
    rows = _rows(reel_desk)
    assert len(rows) == 1
    assert rows[0]["reel_file"] == "reel-ep01-v2.mp4"
    assert "reel-ep01-v1.mp4" in rows[0]["superseded"]
    assert _latest(reel_desk)["made_by"] == "trim"


@needs_ffmpeg
def test_a_posted_row_is_kept_and_the_recut_gets_its_own_row(reel_desk: Path) -> None:  # noqa: F811
    auto_reel(reel_desk, 1, trigger="finish", stream=io.StringIO())
    sheet = reel_desk / "reels" / "metrics.csv"
    rows = _rows(reel_desk)
    rows[0]["posted_at"], rows[0]["views"] = "2026-10-06 18:30 IST", "120"
    with sheet.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    pre_bed = reel_desk / "ep01" / "takes" / "take-ep01-t1-colour-v1.mp4"
    stat = pre_bed.stat()
    os.utime(pre_bed, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    auto_reel(reel_desk, 1, trigger="finish", stream=io.StringIO())
    auto_reel(reel_desk, 1, trigger="reel", stream=io.StringIO(), force=True)

    rows = _rows(reel_desk)
    assert [r["views"] for r in rows] == ["120", ""]
    assert rows[0]["reel_file"] == "reel-ep01-v1.mp4"
    assert (
        rows[1]["reel_file"] == "reel-ep01-v3.mp4"
        and "reel-ep01-v2.mp4" in rows[1]["superseded"]
    )


@needs_ffmpeg
def test_an_episode_with_takes_still_to_finish_is_a_draft(reel_desk: Path) -> None:  # noqa: F811
    spine_file = reel_desk / "ep01" / "api" / "spine.json"
    spine = json.loads(spine_file.read_text(encoding="utf-8"))
    spine["frames"] += [
        {
            "frame_id": f"g2f{i}",
            "episode_id": "episode_01",
            "board_row": i,
            "storyboard_group_id": "g2",
        }
        for i in range(1, 3)
    ]
    spine_file.write_text(json.dumps(spine), encoding="utf-8")
    out = io.StringIO()

    result = auto_reel(reel_desk, 1, trigger="finish", stream=out)

    assert result is not None and result.video is not None
    assert result.video.name == "reel-ep01-draft-v1.mp4"
    assert (
        "episode incomplete — re-cut when the last take is finished" in out.getvalue()
    )
    assert _latest(reel_desk)["draft"] is True


@needs_ffmpeg
def test_a_hand_edited_plan_is_reused_while_its_takes_are_unchanged(
    reel_desk: Path,  # noqa: F811
) -> None:
    first = auto_reel(reel_desk, 1, trigger="finish", stream=io.StringIO())
    assert first is not None
    body = json.loads(first.plan_path.read_text(encoding="utf-8"))
    body["segments"] = body["segments"][1:]  # the human drops the flash-forward
    body["notes"].append("hand edit: no flash-forward")
    first.plan_path.write_text(json.dumps(body), encoding="utf-8")
    pre_bed = reel_desk / "ep01" / "takes" / "take-ep01-t1-colour-v1.mp4"
    stat = pre_bed.stat()
    os.utime(pre_bed, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    out = io.StringIO()
    second = auto_reel(reel_desk, 1, trigger="finish", stream=out)

    assert second is not None
    assert [s["role"] for s in second.segments] == [s["role"] for s in body["segments"]]
    assert f"reusing the hand-edited plan `{first.plan_path.name}`" in out.getvalue()
    made = json.loads(second.plan_path.read_text(encoding="utf-8"))
    assert made["edited_from"] == first.plan_path.name


@needs_ffmpeg
def test_a_hand_edited_plan_for_other_takes_is_not_reused_and_says_why(
    reel_desk: Path,  # noqa: F811
) -> None:
    first = auto_reel(reel_desk, 1, trigger="finish", stream=io.StringIO())
    assert first is not None
    body = json.loads(first.plan_path.read_text(encoding="utf-8"))
    body["notes"].append("hand edit")
    body["takes"]["t1"]["source"] = "ep01/takes/take-ep01-t1-colour-v0.mp4"
    first.plan_path.write_text(json.dumps(body), encoding="utf-8")
    pre_bed = reel_desk / "ep01" / "takes" / "take-ep01-t1-colour-v1.mp4"
    stat = pre_bed.stat()
    os.utime(pre_bed, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    out = io.StringIO()
    second = auto_reel(reel_desk, 1, trigger="finish", stream=out)

    assert second is not None
    text = out.getvalue()
    assert (
        "fresh plan" in text and "t1" in text and "take-ep01-t1-colour-v0.mp4" in text
    )


def test_auto_reel_without_a_spine_is_one_line(tmp_path: Path) -> None:
    out = io.StringIO()
    assert auto_reel(tmp_path, 1, trigger="finish", stream=out) is None
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("Reel: not made")


# --- the CLI: on by default, --no-reel skips -----------------------------------------------------


def test_finish_runs_the_reel_unless_no_reel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from creation import cli_post
    from creation.cli_produce import main

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cli_post, "run_finish",
        lambda *_a, **_k: SimpleNamespace(complete=True, final="f", episode=1),
    )  # fmt: skip
    monkeypatch.setattr(
        "creation.post.reel.auto_reel", lambda desk, episode, **k: calls.append(k)
    )

    assert main(["finish", "--desk", str(tmp_path)]) == 0
    assert [c["trigger"] for c in calls] == ["finish"]
    assert main(["finish", "--desk", str(tmp_path), "--no-reel"]) == 0
    assert len(calls) == 1


def test_an_incomplete_finish_makes_no_reel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from creation import cli_post
    from creation.cli_produce import main

    calls: list[Any] = []
    monkeypatch.setattr(
        cli_post, "run_finish",
        lambda *_a, **_k: SimpleNamespace(complete=False, final="f", episode=1),
    )  # fmt: skip
    monkeypatch.setattr("creation.post.reel.auto_reel", lambda *a, **k: calls.append(k))
    main(["finish", "--desk", str(tmp_path)])
    assert calls == []


@pytest.mark.parametrize("command", ["trim", "freeze", "tempo", "soften", "blur"])
def test_the_edit_commands_take_no_reel(command: str) -> None:
    import argparse

    from creation.post.edit_commands import add_edit_parsers

    parser = argparse.ArgumentParser()
    add_edit_parsers(parser.add_subparsers(dest="command"))
    needs = {"freeze": ["--at", "1", "--hold", "0.4"], "tempo": ["--factor", "0.9"],
             "blur": ["--box", "1,1,4,4", "--from", "0", "--to", "1"]}  # fmt: skip
    args = parser.parse_args(
        [command, "--desk", "d", *needs.get(command, []), "--no-reel"]
    )
    assert args.no_reel is True


def test_an_edit_that_writes_a_record_makes_a_reel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from creation.post import edit_commands

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "creation.post.reel.auto_reel",
        lambda desk, episode, **k: calls.append({"episode": episode, **k}),
    )
    args = SimpleNamespace(command="tempo", no_reel=False, episode=2)
    edit_commands.reel_after_edit(args, tmp_path, tmp_path / "rec.json", io.StringIO())
    edit_commands.reel_after_edit(
        args, tmp_path, None, io.StringIO()
    )  # nothing new: no reel
    args.no_reel = True
    edit_commands.reel_after_edit(args, tmp_path, tmp_path / "rec.json", io.StringIO())
    assert calls == [{"episode": 2, "trigger": "tempo", "stream": calls[0]["stream"]}]
