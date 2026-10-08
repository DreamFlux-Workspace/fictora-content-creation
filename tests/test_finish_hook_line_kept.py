"""``finish --hook-line`` is kept on the desk so ``join`` lays the same line (L-20261006-5)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from creation.post.letterbox import (
    HOOK_LINE_FILE,
    desk_hook_line,
    keep_finish_hook_line,
    title_block,
)

SPINE = {
    "title": "Three Payments Late",
    "episode_summaries": [
        {
            "ordinal": 1,
            "episode_id": "episode_01",
            "hook_line_options": [{"style": "stakes", "text": "The spine's line"}],
            "hook_line_selected": {
                "kind": "option",
                "index": 0,
                "text": "The spine's line",
            },
        }
    ],
}


def test_kept_line_is_what_join_lays(tmp_path: Path) -> None:
    kept = keep_finish_hook_line(
        tmp_path, 1, hook_line="  My   own line ", no_hook_line=False
    )
    assert kept == (tmp_path / HOOK_LINE_FILE).resolve()
    assert desk_hook_line(tmp_path, 1)["text"] == "My own line"
    block, _ = title_block(SPINE, 1, desk=tmp_path)
    assert block is not None and block.hook == "My own line"


def test_no_hook_line_is_kept_as_off(tmp_path: Path) -> None:
    keep_finish_hook_line(tmp_path, 1, hook_line=None, no_hook_line=True)
    assert desk_hook_line(tmp_path, 1)["kind"] == "off"


def test_nothing_kept_without_a_flag(tmp_path: Path) -> None:
    assert (
        keep_finish_hook_line(tmp_path, 1, hook_line="  ", no_hook_line=False) is None
    )
    assert not (tmp_path / HOOK_LINE_FILE).exists()
    block, _ = title_block(SPINE, 1, desk=tmp_path)
    assert block is not None and block.hook == "The spine's line"


def _fake_finish(monkeypatch: pytest.MonkeyPatch, complete: bool = True) -> None:
    from creation import cli_post

    monkeypatch.setattr(
        cli_post,
        "run_finish",
        lambda *_a, **_k: SimpleNamespace(
            complete=complete, final="out/take-ep02-final.mp4"
        ),
    )
    monkeypatch.setattr("creation.post.reel.auto_reel", lambda *a, **k: None)
    monkeypatch.setattr(
        "creation.post.clips_via_server.auto_clips", lambda *a, **k: None
    )


def test_finish_cli_keeps_the_hook_line_for_the_finished_episode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from creation.cli_produce import main

    _fake_finish(monkeypatch)
    assert main(["finish", "--desk", str(tmp_path), "--hook-line", "Finish line"]) == 0
    saved = json.loads((tmp_path / HOOK_LINE_FILE).read_text(encoding="utf-8"))
    assert saved["episodes"]["2"]["text"] == "Finish line"
    assert saved["episodes"]["2"]["why"] == "finish --hook-line"


def test_finish_cli_without_the_flag_keeps_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from creation.cli_produce import main

    _fake_finish(monkeypatch)
    assert main(["finish", "--desk", str(tmp_path)]) == 0
    assert not (tmp_path / HOOK_LINE_FILE).exists()


def test_an_incomplete_finish_keeps_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from creation.cli_produce import main

    _fake_finish(monkeypatch, complete=False)
    main(["finish", "--desk", str(tmp_path), "--hook-line", "Finish line"])
    assert not (tmp_path / HOOK_LINE_FILE).exists()
