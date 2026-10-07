"""Text arguments that take either the words themselves or a file holding them.

A prompt, a look description or a lines list can be long. Every flag that
takes one accepts both forms, the same way:

- ``@path`` reads the file (the explicit form; a missing file is an error),
- a value naming a file that exists reads that file,
- anything else is the text itself.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

#: Longer values are never tried as a path (a real premise is not a filename).
_MAX_PATH_CHARS = 1024

HELP_SUFFIX = "Text, or @FILE / an existing file path to read it from."


class TextArgError(ValueError):
    """A text-or-file argument could not be read or parsed."""


def text_or_file(value: str, *, flag: str) -> str:
    """Return the text a flag carries: inline words, ``@file``, or an existing file's contents.

    Parameters
    ----------
    value
        What the operator passed.
    flag
        The flag's name, for the error message (``--lines-json``).

    Returns
    -------
    str
        The text, stripped.

    Raises
    ------
    TextArgError
        ``@file`` names a file that cannot be read.
    """

    if value.startswith("@"):
        path = Path(value[1:]).expanduser()
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise TextArgError(f"{flag} {value}: cannot read {path}: {exc}") from exc
    if value and len(value) <= _MAX_PATH_CHARS and "\n" not in value:
        path = Path(value).expanduser()
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise TextArgError(f"{flag} {value}: cannot read {path}: {exc}") from exc
    return value.strip()


def json_or_file(value: str, *, flag: str) -> Any:
    """Parse a flag's JSON, given inline or as ``@file`` / an existing file path.

    Parameters
    ----------
    value
        What the operator passed.
    flag
        The flag's name, for the error message.

    Returns
    -------
    Any
        The parsed JSON.

    Raises
    ------
    TextArgError
        The file cannot be read, or the text is not JSON (the message says which form was tried).
    """

    text = text_or_file(value, flag=flag)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        inline = text == value.strip() and not value.startswith("@")
        hint = (
            f" (no file named {value!r} exists, so it was read as JSON text; pass the JSON, @FILE or an existing path)"
            if inline and not value.lstrip().startswith(("[", "{"))
            else ""
        )
        raise TextArgError(f"{flag} is not valid JSON{hint}: {exc}") from exc


__all__ = ["HELP_SUFFIX", "TextArgError", "json_or_file", "text_or_file"]


def force_utf8_output(*streams: TextIO | None) -> None:
    """Print UTF-8 whatever the console's code page is (default: stdout and stderr).

    A Windows console is often cp1252 / cp949, and printing a Korean or Japanese
    line there raised ``UnicodeEncodeError`` mid-step (``step`` crashed). A
    stream that cannot be reconfigured (a test's ``StringIO``) is left as it is.

    Parameters
    ----------
    streams
        Streams to switch; default ``sys.stdout`` and ``sys.stderr``.
    """

    for stream in streams or (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # Line by line even when piped, so a run read through a pipe (an agent, a script) keeps
        # the kit's order: the closing Applied / Refused line stays last, never above the change
        # list it closes (the canary of 7 Oct 2026 misread a refused look as applied).
        if not getattr(stream, "line_buffering", True):
            try:
                reconfigure(line_buffering=True)
            except (
                ValueError,
                OSError,
            ) as exc:  # a detached or closed stream keeps its buffering
                print(
                    f"WARNING: could not make output line-buffered: {exc}",
                    file=sys.stderr,
                )
        encoding = str(getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding == "utf8":
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (
            ValueError,
            OSError,
        ) as exc:  # a detached or closed stream keeps its encoding
            print(f"WARNING: could not switch output to UTF-8: {exc}", file=sys.stderr)
