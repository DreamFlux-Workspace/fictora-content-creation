"""``fictora-produce setup-check``: token present and accepted, ffmpeg with libass and the kit's filters. No network."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Any

import pytest

from creation import setup_check as sc
from creation.captions import HouseFont, ItalicFont

TOKEN = "tok-secret-value-123"
ALL_FILTERS = "\n".join(
    f" TSC {name:<18}V->V       x"
    for name in (*sc.REQUIRED_FILTERS, "ass", "subtitles")
)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(sc.TOKEN_ENV, raising=False)
    monkeypatch.delenv(sc.BASE_URL_ENV, raising=False)
    path = tmp_path / ".env"
    path.write_text(
        f"{sc.TOKEN_ENV}={TOKEN}\n{sc.BASE_URL_ENV}=https://drama.example\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """ffmpeg, ffprobe and uv found; ffmpeg lists every filter the kit needs."""

    listing = {"filters": ALL_FILTERS}
    monkeypatch.setattr(sc, "find_ffmpeg", lambda: ("/x/ffmpeg", "/x/ffprobe"))
    monkeypatch.setattr(sc, "find_italic_font", lambda: ITALIC_PRESENT)
    monkeypatch.setattr(sc, "find_house_font", lambda: ARIAL_PRESENT)

    def run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        out = listing["filters"] if "-filters" in cmd else "uv 0.6.12"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(sc.subprocess, "run", run)
    return listing


ARIAL_PRESENT = HouseFont(Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"))
ITALIC_PRESENT = ItalicFont(
    Path("/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
)


def which(name: str) -> str | None:
    return f"/x/{name}"


def api_ok(calls: list[tuple[str, str, str]]):
    def get(base: str, token: str, path: str) -> tuple[int, Any]:
        calls.append((base, token, path))
        return 200, {"presets": []}

    return get


def test_everything_present_and_accepted_prints_ticks_and_exits_0(
    env: Path, tools: dict[str, str]
) -> None:
    calls: list[tuple[str, str, str]] = []
    out = io.StringIO()

    code = sc.run_setup_check(out=out, env_file=env, api_get=api_ok(calls), which=which)

    printed = out.getvalue()
    assert code == 0, printed
    assert calls == [
        ("https://drama.example", TOKEN, "/v1/art-style-presets")
    ]  # one authenticated read
    assert (
        "✗" not in printed
        and printed.count("✓") == 11
        and printed.rstrip().endswith("Ready.")
    )
    assert "✓ API token accepted" in printed and "✓ libass (captions)" in printed
    assert "✓ tesseract (drawn text)" in printed
    assert (
        "✓ Arial Bold Italic (heard-not-seen captions): "
        "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf" in printed
    )
    assert TOKEN not in printed


def test_a_refused_token_is_a_cross_and_exit_1(
    env: Path, tools: dict[str, str]
) -> None:
    out = io.StringIO()
    code = sc.run_setup_check(
        out=out, env_file=env, api_get=lambda *_: (401, {"error": "nope"}), which=which
    )
    assert code == 1
    assert (
        "✗ API token accepted: HTTP 401" in out.getvalue()
        and "refused" in out.getvalue()
    )
    assert TOKEN not in out.getvalue()


def test_a_missing_token_makes_no_request(
    tmp_path: Path, tools: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(sc.TOKEN_ENV, raising=False)
    calls: list[tuple[str, str, str]] = []
    out = io.StringIO()
    assert (
        sc.run_setup_check(
            out=out, env_file=tmp_path / "none.env", api_get=api_ok(calls), which=which
        )
        == 1
    )
    assert calls == [] and "✗ API token:" in out.getvalue()


def test_an_unreachable_api_is_a_cross(env: Path, tools: dict[str, str]) -> None:
    import httpx

    def down(*_: Any) -> tuple[int, Any]:
        raise httpx.ConnectError("no route")

    out = io.StringIO()
    assert sc.run_setup_check(out=out, env_file=env, api_get=down, which=which) == 1
    assert (
        "✗ API token accepted: could not reach https://drama.example (ConnectError)"
        in out.getvalue()
    )


def test_ffmpeg_without_libass_or_a_filter_is_a_cross(
    env: Path, tools: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_libass() -> tuple[str, str]:
        raise RuntimeError(
            "ffmpeg with libass (the ass/subtitles filter) and ffprobe are required"
        )

    monkeypatch.setattr(sc, "find_ffmpeg", no_libass)
    tools["filters"] = "\n".join(
        line for line in ALL_FILTERS.splitlines() if " tblend " not in line
    )
    out = io.StringIO()

    code = sc.run_setup_check(out=out, env_file=env, api_get=api_ok([]), which=which)

    printed = out.getvalue()
    assert code == 1
    assert "✗ libass (captions): ffmpeg with libass" in printed
    assert "✗ ffmpeg filters: missing tblend" in printed


def test_no_ffmpeg_on_path_is_a_cross(env: Path, tools: dict[str, str]) -> None:
    out = io.StringIO()
    code = sc.run_setup_check(
        out=out,
        env_file=env,
        api_get=api_ok([]),
        which=lambda name: None if name.startswith("ff") else f"/x/{name}",
    )
    assert (
        code == 1
        and "✗ ffmpeg: not on PATH" in out.getvalue()
        and "✗ ffprobe: not on PATH" in out.getvalue()
    )


def test_an_old_python_is_a_cross() -> None:
    assert not sc.check_python((3, 11, 9)).ok
    assert sc.check_python((3, 12, 0)).ok


def test_missing_arial_italic_is_a_warning_naming_the_bundled_twin_not_a_failure(
    env: Path, tools: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sc,
        "find_italic_font",
        lambda: ItalicFont(
            None, None, "DejaVu Sans (/usr/share/fonts/dejavu/DejaVuSans.ttf)"
        ),
    )
    out = io.StringIO()

    code = sc.run_setup_check(out=out, env_file=env, api_get=api_ok([]), which=which)

    printed = out.getvalue()
    assert code == 0, printed  # a warning never blocks setup
    assert "✗" not in printed
    line = next(row for row in printed.splitlines() if "Arial Bold Italic (" in row)
    assert line.startswith(
        "⚠ Arial Bold Italic (heard-not-seen captions): Arial Bold Italic not found"
    )
    assert (
        "bundled Liberation Sans Bold Italic" in line
    )  # one face, never a silent fallback
    assert "ttf-mscorefonts-installer" in line and "Restore Standard Fonts" in line
    assert printed.rstrip().endswith("Ready (1 warning(s) above).")


def test_missing_tesseract_is_a_warning_not_a_failure(
    env: Path, tools: dict[str, str]
) -> None:
    out = io.StringIO()

    code = sc.run_setup_check(
        out=out,
        env_file=env,
        api_get=api_ok([]),
        which=lambda name: None if name == "tesseract" else which(name),
    )

    printed = out.getvalue()
    assert code == 0, printed
    assert (
        "⚠ tesseract (drawn text):" in printed and "brew install tesseract" in printed
    )
    assert printed.rstrip().endswith("Ready (1 warning(s) above).")


def test_arial_bold_without_the_italic_is_a_warning() -> None:
    check = sc.check_italic_font(
        lambda: ItalicFont(None, Path("/fonts/Arial Bold.ttf"))
    )
    assert check.ok and check.warn
    assert (
        check.line().startswith("⚠ ") and "only /fonts/Arial Bold.ttf" in check.line()
    )
