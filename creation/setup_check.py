"""``fictora-produce setup-check``: is this laptop ready to make an episode?

Checks what the kit needs and nothing else: the Drama API token is present and
accepted (one cheap authenticated read, ``GET /v1/art-style-presets``), ffmpeg
and ffprobe are installed, ffmpeg has libass (captions) and the filters the
local finish and edits use, Georgia Italic (the heard-not-seen caption face) is
where libass will look for it, and the Python and uv the repo runs on. Prints
one ✓ or ✗ line per item (⚠ for a warning that does not block) and never prints
the token. Spends nothing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

import httpx

from creation.captions import ItalicFont, find_ffmpeg, find_italic_font
from creation.harness.credentials import DEFAULT_DRAMA_API
from creation.harness.env import load_env_file
from creation.harness.session import DramaApiRunSession

REPO_ROOT = Path(__file__).resolve().parents[1]
TOKEN_ENV = "FICTORA_DRAMA_GENERATION_SERVICE_TOKEN"
BASE_URL_ENV = "FICTORA_DRAMA_GENERATION_API_BASE_URL"
TOKEN_PROBE_PATH = "/v1/art-style-presets"
"""An authenticated read: needs a valid bearer token, spends nothing."""
MIN_PYTHON = (3, 12)
REQUIRED_FILTERS: tuple[str, ...] = (
    "tblend",  # soften: hard-cut trace
    "loudnorm",  # mix: loudness
    "sidechaincompress",  # mix: bed ducked under the voice
    "alimiter",  # mix: the one limiter
    "amix",  # mix, sfx, revoice
    "atempo",  # tempo: sound kept in pitch
    "lut3d",  # colour: the board's look
    "silencedetect",  # captions: line starts
    "astats",  # sfx shape check, reading a take
    "signalstats",  # edits: brightness
    "gblur",  # blur: the patch over invented text
    "geq",  # blur --feather: the soft edge outside the box
)
"""ffmpeg filters the local finish and edits call (libass's ``ass`` is checked on its own line)."""


@dataclass(frozen=True)
class Check:
    """One setup line.

    Parameters
    ----------
    name
        What was checked.
    ok
        Passed or not.
    detail
        What was found, or how to fix it.
    warn
        A warning: printed ``⚠``, does not fail setup (``ok`` stays True).
    """

    name: str
    ok: bool
    detail: str
    warn: bool = False

    def line(self) -> str:
        """The printed ``✓ name: detail`` / ``⚠ name: detail`` / ``✗ name: detail`` line."""

        mark = "✗" if not self.ok else "⚠" if self.warn else "✓"
        return f"{mark} {self.name}: {self.detail}"


ApiGet = Callable[[str, str, str], tuple[int, Any]]


def _api_get(base_url: str, token: str, path: str) -> tuple[int, Any]:
    with tempfile.TemporaryDirectory(prefix="setup-check-") as tmp:
        run = DramaApiRunSession(
            base_url=base_url, token=token, out_dir=Path(tmp), client_timeout=30.0
        )
        try:
            return run.get_optional(path)
        finally:
            run.client.close()


def check_python(version: tuple[int, ...] | None = None) -> Check:
    """Python is at least 3.12 (``pyproject.toml``)."""

    found = tuple(version or sys.version_info[:3])
    text = ".".join(str(part) for part in found)
    if found[:2] >= MIN_PYTHON:
        return Check("python", True, text)
    return Check(
        "python", False, f"{text}; the kit needs 3.12 or newer (uv sync installs it)"
    )


def check_uv(which: Callable[[str], str | None] = shutil.which) -> Check:
    """``uv`` is on PATH (every command runs as ``uv run ...``)."""

    path = which("uv")
    if not path:
        return Check(
            "uv",
            False,
            "not on PATH; install: https://docs.astral.sh/uv/getting-started/installation/",
        )
    try:
        version = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("uv", False, f"{path} does not run ({exc})")
    return Check("uv", True, version or path)


def check_tools(which: Callable[[str], str | None] = shutil.which) -> list[Check]:
    """ffmpeg and ffprobe, then libass and the filters on the ffmpeg the kit will use."""

    checks: list[Check] = []
    for tool in ("ffmpeg", "ffprobe"):
        path = which(tool)
        checks.append(
            Check(tool, bool(path), path or "not on PATH; macOS: brew install ffmpeg")
        )
    try:
        ffmpeg, _probe = find_ffmpeg()
    except RuntimeError as exc:
        checks.append(Check("libass (captions)", False, str(exc)))
        ffmpeg = which("ffmpeg") or ""
    else:
        checks.append(
            Check("libass (captions)", True, f"the ass filter is in {ffmpeg}")
        )
    if not ffmpeg:
        checks.append(Check("ffmpeg filters", False, "no ffmpeg to ask"))
        return checks
    try:
        listing = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        checks.append(
            Check("ffmpeg filters", False, f"{ffmpeg} -filters did not run ({exc})")
        )
        return checks
    have = set(re.findall(r"^\s*\S+\s+(\w+)\s", listing, re.MULTILINE))
    missing = [name for name in REQUIRED_FILTERS if name not in have]
    if missing:
        checks.append(
            Check(
                "ffmpeg filters",
                False,
                f"missing {', '.join(missing)}; install a full build (brew install ffmpeg)",
            )
        )
    else:
        checks.append(Check("ffmpeg filters", True, ", ".join(REQUIRED_FILTERS)))
    return checks


def check_italic_font(find: Callable[[], ItalicFont] | None = None) -> Check:
    """Georgia Italic resolves where libass looks for it (warns, never fails).

    Poppins, the house caption face, is bundled; Georgia is a system font.
    Without it, heard-not-seen captions quietly come out in another face, so
    this is a ``⚠`` naming the fallback and how to install Georgia.

    Parameters
    ----------
    find
        Font lookup (default :func:`creation.captions.find_italic_font`); tests pass a fake.
    """

    name = "Georgia Italic (heard-not-seen captions)"
    font = (find or find_italic_font)()
    warning = font.warning()
    if warning is None:
        return Check(name, True, str(font.italic))
    return Check(name, True, warning, warn=True)


def check_token(
    env_file: Path | None = None, api_get: ApiGet = _api_get
) -> list[Check]:
    """The token is set (``.env`` or the environment) and the deployed API accepts it. Never prints it."""

    load_env_file(env_file or REPO_ROOT / ".env")
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        return [
            Check(
                "API token",
                False,
                f"{TOKEN_ENV} is not set; copy .env.example to .env and set it",
            )
        ]
    present = Check("API token", True, f"{TOKEN_ENV} is set (value not printed)")
    base_url = os.environ.get(BASE_URL_ENV, "").strip() or DEFAULT_DRAMA_API
    try:
        status, _body = api_get(base_url, token, TOKEN_PROBE_PATH)
    except (httpx.HTTPError, OSError) as exc:
        return [
            present,
            Check(
                "API token accepted",
                False,
                f"could not reach {base_url} ({type(exc).__name__})",
            ),
        ]
    if status in (401, 403):
        return [
            present,
            Check(
                "API token accepted",
                False,
                f"HTTP {status} from {base_url}: the token is refused",
            ),
        ]
    if not 200 <= status < 300:
        return [
            present,
            Check(
                "API token accepted",
                False,
                f"HTTP {status} from GET {TOKEN_PROBE_PATH} on {base_url}",
            ),
        ]
    return [
        present,
        Check(
            "API token accepted",
            True,
            f"GET {TOKEN_PROBE_PATH} on {base_url} answered {status}",
        ),
    ]


def run_setup_check(
    *,
    out: TextIO | None = None,
    env_file: Path | None = None,
    api_get: ApiGet = _api_get,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    """Print every check and return the exit code.

    Parameters
    ----------
    out
        Text stream (stdout by default).
    env_file
        Dotenv file to read the token from (the repo's ``.env`` by default).
    api_get
        ``(base_url, token, path) -> (status, body)``; tests pass a fake.
    which
        PATH lookup; tests pass a fake.

    Returns
    -------
    int
        ``0`` when no line is ✗ (a ⚠ warning does not fail), ``1`` when any is ✗.
    """

    out = out or sys.stdout
    checks = [
        *check_token(env_file, api_get),
        *check_tools(which),
        check_italic_font(),
        check_python(),
        check_uv(which),
    ]
    for check in checks:
        print(check.line(), file=out)
    failed = [check for check in checks if not check.ok]
    warned = [check for check in checks if check.warn]
    if failed:
        print(f"{len(failed)} to fix before filming.", file=out)
    elif warned:
        print(f"Ready ({len(warned)} warning(s) above).", file=out)
    else:
        print("Ready.", file=out)
    return 1 if failed else 0


__all__ = [
    "Check",
    "REQUIRED_FILTERS",
    "TOKEN_PROBE_PATH",
    "check_italic_font",
    "check_python",
    "check_token",
    "check_tools",
    "check_uv",
    "run_setup_check",
]
