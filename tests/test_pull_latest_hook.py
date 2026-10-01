"""The session-start pull hook (L-20261001-22, L-20261001-29): honest, and independent of python and pwd."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".cursor" / "hooks" / "pull-latest.sh"

needs_bash_git = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash and git",
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _checkout(tmp_path: Path, *, branch: str = "main") -> Path:
    """A clone of a tiny origin with the hook copied in, on ``branch``."""

    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@example.com")
    _git(origin, "config", "user.name", "t")
    (origin / "a.txt").write_text("one\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", 'first "quoted" subject')
    repo = tmp_path / "repo"
    _git(tmp_path, "clone", "-q", str(origin), str(repo))
    hooks = repo / ".cursor" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy(HOOK, hooks / HOOK.name)
    if branch != "main":
        _git(repo, "checkout", "-qb", branch)
    return repo


def _fake_bin(tmp_path: Path, broken: tuple[str, ...]) -> Path:
    """A PATH dir where each name in ``broken`` behaves like the Windows Store stub (prints, exits 9009)."""

    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    for name in broken:
        stub = fake / name
        stub.write_text(
            "#!/bin/sh\necho 'Python was not found; run without arguments to install from the Microsoft Store' >&2\nexit 9009\n"
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return fake


def _run(
    repo: Path, *, cwd: Path, broken: tuple[str, ...] = (), env_dir: str | None = None
) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"CLAUDE_PROJECT_DIR", "CURSOR_PROJECT_DIR"}
    }
    if broken:
        env["PATH"] = f"{_fake_bin(cwd.parent, broken)}{os.pathsep}{env['PATH']}"
    if env_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = env_dir
    done = subprocess.run(
        ["bash", str(repo / ".cursor" / "hooks" / HOOK.name)],
        input=json.dumps({"hook_event_name": "SessionStart"}),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(done.stdout)


def _message(payload: dict) -> str:
    return payload["hookSpecificOutput"]["additionalContext"]


@needs_bash_git
def test_hook_finds_its_checkout_from_its_own_location(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    message = _message(_run(repo, cwd=elsewhere))

    assert message.startswith("This checkout already matches origin/main"), message


@needs_bash_git
def test_hook_ignores_a_project_dir_that_is_not_a_checkout(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    message = _message(_run(repo, cwd=elsewhere, env_dir=str(elsewhere)))

    assert "already matches origin/main" in message, message


@needs_bash_git
def test_hook_answers_when_python3_is_the_windows_store_stub(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)

    payload = _run(repo, cwd=repo, broken=("python3", "python", "py"))

    message = _message(payload)
    assert 'first "quoted" subject' in message, message


@needs_bash_git
def test_a_skip_says_skipped_never_pull_finished(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, branch="feature")

    message = _message(_run(repo, cwd=repo, broken=("python3", "python", "py")))

    assert message.startswith("Pull skipped: checkout is on feature"), message
    assert "Pull finished" not in message


@needs_bash_git
def test_cursor_gets_its_own_shape_and_a_quote_survives(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, branch='say-"hi"')
    done = subprocess.run(
        ["bash", str(repo / ".cursor" / "hooks" / HOOK.name)],
        input="{}",
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert json.loads(done.stdout) == {
        "additional_context": 'Pull skipped: checkout is on say-"hi", not main. That branch was left as-is.'
    }
