"""Kit commands never read the terminal's stdin (L-20261006-17).

ffmpeg reads stdin for its keyboard commands, so a kit command run inside a
``while read`` loop ate the loop's next lines. Every child process the kit
starts gets no stdin (``stdin=subprocess.DEVNULL``; not ``-nostdin``, which
would change every recorded ffmpeg command line the golden tests freeze).
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALLS = {"run", "Popen", "check_output", "call", "check_call"}


def _unguarded_calls() -> list[str]:
    found = []
    for path in sorted((ROOT / "creation").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.func.attr in CALLS
                and not any(k.arg in ("stdin", "input") for k in node.keywords)
            ):
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def test_every_child_process_gets_no_stdin() -> None:
    assert _unguarded_calls() == [], (
        "pass stdin=subprocess.DEVNULL to every subprocess call"
    )


def test_run_ffmpeg_gives_ffmpeg_no_stdin(monkeypatch) -> None:
    from creation.post import media

    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(media, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", fake_run)
    media.run_ffmpeg(["-i", "x", "y"])
    assert seen["argv"][:3] == ["ffmpeg", "-v", "error"]
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL
