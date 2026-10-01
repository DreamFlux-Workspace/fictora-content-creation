"""Small papercuts: UTF-8 output on any console, and an edit names the take its --take-file is."""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import pytest

from creation.cli_text import force_utf8_output
from creation.post.edit_commands import take_id_for


def test_korean_prints_on_a_cp1252_console_after_the_switch() -> None:
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252")
    with pytest.raises(UnicodeEncodeError):
        print("안녕하세요", file=console)
    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")

    force_utf8_output(console)
    print("안녕하세요", file=console)
    console.flush()

    assert console.buffer.getvalue().decode("utf-8") == "안녕하세요\n"  # type: ignore[attr-defined]


def test_a_stream_that_cannot_switch_is_left_alone() -> None:
    force_utf8_output(io.StringIO())


def _args(take_id: str | None, take_file: str | None) -> argparse.Namespace:
    return argparse.Namespace(
        take_id=take_id, take_file=Path(take_file) if take_file else None
    )


def test_the_take_comes_from_the_take_file_when_take_is_not_given() -> None:
    assert take_id_for(_args(None, "/d/ep01/takes/take-ep01-t2-sokii-v1.mp4")) == "t2"
    assert take_id_for(_args(None, "/d/ep01/takes/take-ep01-t2.mp4")) == "t2"
    assert take_id_for(_args(None, "/elsewhere/clip.mp4")) == "t1"
    assert take_id_for(_args(None, None)) == "t1"
    assert take_id_for(_args("t2", "/d/take-ep01-t2-cap-v1.mp4")) == "t2"
    with pytest.raises(ValueError, match="--take t1 but --take-file"):
        take_id_for(_args("t1", "/d/take-ep01-t2-cap-v1.mp4"))
