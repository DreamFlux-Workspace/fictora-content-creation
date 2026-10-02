"""`line --spoken` on a show the server holds as English (L-20261001-21, L-20261001-28)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from cast_story import (
    _before_gate,
    _patches,
    _story,
    _writes,
)
from creation import episode_commands as ec
from fake_api import FakeApi


def test_spoken_with_a_language_override_on_an_english_show_is_recorded_on_the_desk_not_sent(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _story(approved=False))

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_line(
            desk,
            episode=1,
            line="1",
            spoken="もう閉店です。",
            subtitle="We're closed.",
            language="ja",
            out=io.StringIO(),
        )

    message = str(stopped.value)
    assert "recorded on the desk" in message
    assert "en-US" in message
    assert "fictora-produce language --desk" in message and "--spoken ja" in message
    assert _writes(api) == []
    record = json.loads(
        (desk / "shared" / "spoken-language.json").read_text(encoding="utf-8")
    )
    assert record["declared"] == "ja-JP"
    assert record["server"] == "en-US"
    assert record["pinned_lines"][0] == {
        "episode": 1,
        "line_id": "line_episode_01_01",
        "spoken": "もう閉店です。",
        "subtitle": "We're closed.",
        "at_utc": record["pinned_lines"][0]["at_utc"],
    }


def test_a_performed_line_that_is_not_english_counts_as_the_override(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _story(approved=False))

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_line(
            desk, episode=1, line="1", spoken="もう閉店です。", subtitle="We're closed.", out=io.StringIO()
        )  # fmt: skip

    assert "recorded on the desk" in str(stopped.value)
    assert _writes(api) == []


def test_english_spoken_on_an_english_show_without_override_names_the_language_flag(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _story(approved=False))

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_line(desk, episode=1, line="1", spoken="Closed.", out=io.StringIO())

    assert "--language ja" in str(stopped.value)
    assert not (desk / "shared" / "spoken-language.json").exists()


def test_a_japanese_show_still_sends_the_pinned_line(desk: Path, api: FakeApi) -> None:
    _before_gate(api, _story(approved=False))
    api.spine_doc["spoken_language"] = "ja-JP"

    ec.run_line(
        desk, episode=1, line="1", spoken="もう閉店です。", subtitle="We're closed.", language="ja",
        out=io.StringIO(),
    )  # fmt: skip

    assert _patches(api)[0]["dialogue_lines"][0]["spoken_text"] == "もう閉店です。"
    assert not (desk / "shared" / "spoken-language.json").exists()
