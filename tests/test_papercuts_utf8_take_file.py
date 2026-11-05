from __future__ import annotations

import io

import pytest

from creation.cli_text import force_utf8_output


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
