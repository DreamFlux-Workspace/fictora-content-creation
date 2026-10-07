"""Canary, 7 Oct 2026: `gender: woman` was refused by the server and the refusal printed above the
change list in a piped run, so a refused look was read as applied and two plates were redrawn from
unchanged cards ($0.60)."""

from __future__ import annotations

import io
import subprocess
import sys

import pytest

from creation.cast_commands import parse_look
from creation.cli_text import force_utf8_output
from creation.episode_commands import CommandStopped


@pytest.mark.parametrize(
    "word, server",
    [("woman", "female"), ("Woman", "female"), ("girl", "female"), ("female", "female"),
     ("man", "male"), ("boy", "male"), ("male", "male")],
)  # fmt: skip
def test_everyday_gender_words_become_the_servers_values(
    word: str, server: str
) -> None:
    _, brief = parse_look(f"age: 28\ngender: {word}\nhair: jaw-length bob")
    assert brief["gender_presentation"] == server


def test_a_gender_the_server_cannot_take_stops_before_anything_is_sent() -> None:
    with pytest.raises(
        CommandStopped, match="the server takes female or male.*Nothing was sent"
    ):
        parse_look("gender: wizard")


def test_a_json_look_is_read_the_same_way() -> None:
    _, brief = parse_look('{"gender_presentation": "woman", "age_band": "28"}')
    assert brief["gender_presentation"] == "female"


def test_piped_output_keeps_its_order() -> None:
    # stdout to a pipe is block-buffered by default; stderr is not, so a refusal on stderr jumped
    # above the change list on stdout. After force_utf8_output both go out line by line.
    code = (
        "import sys; from creation.cli_text import force_utf8_output; force_utf8_output(); "
        "print('look for Mina:'); print('  hair: bob'); print('Refused: x', file=sys.stderr); "
        "print('done')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=False, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, check=True,
    ).stdout.splitlines()  # fmt: skip
    assert out == ["look for Mina:", "  hair: bob", "Refused: x", "done"]


def test_a_string_stream_is_left_alone() -> None:
    stream = io.StringIO()
    force_utf8_output(stream)
    print("ok", file=stream)
    assert stream.getvalue() == "ok\n"
