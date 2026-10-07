"""When the server's reel engine can't be used for a run, the reel is cut locally with the same rules (7 Oct 2026).

The canary (desk ``2026-10-07-lost-and-found-canary``): production answered the
upload slot 409 ``operator_upload_unavailable`` (no R2 keys on the server), so
``finish`` made no reel, no cover and no clips, only a ⚠ line. Now a new desk's
reel falls back to the local reel engine for that run (``reels/epNN/`` names,
the cover drawn here and laid on frame 0, ``latest.json`` and ``metrics.csv``,
the post text); a refusal about the content still stops; the clips, which have
no local engine, say they need the server.
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import needs_ffmpeg
from test_reel_via_server import legacy_reel_desk, reel_desk  # noqa: F401  (fixtures)

from creation.post import cover_frame
from creation.post.clips_via_server import auto_clips, episode_clips
from creation.post.reel import auto_reel, run_reel
from reel_fake_server import FakeReelServer

pytestmark = needs_ffmpeg

LINE = "Reel made locally (the server reel engine refused: operator_upload_unavailable); same rules"


def _rgb(path: Path, index: int = 0) -> tuple[int, int, int]:
    """Frame ``index`` of a video (or an image) averaged to one pixel."""

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{index}),scale=1:1",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    return raw[0], raw[1], raw[2]


def _close(a: tuple[int, int, int], b: tuple[int, int, int], within: int = 12) -> bool:
    return all(abs(x - y) <= within for x, y in zip(a, b))


def _latest(desk: Path) -> dict[str, Any]:
    return json.loads(
        (desk / "reels" / "ep01" / "latest.json").read_text(encoding="utf-8")
    )


def _assert_made_locally(desk: Path, result: Any, said: str) -> None:
    folder = desk / "reels" / "ep01"
    assert result is not None and result.video is not None and result.cover is not None
    assert result.video == folder / "reel-ep01-v1.mp4" and result.video.is_file()
    assert result.cover.parent == folder and result.cover.name.startswith(
        "reel-ep01-v1-cover"
    )
    # The cover is the reel's frame 0; frame 1 is the cut itself.
    cover = _rgb(result.cover)
    assert _close(_rgb(result.video, 0), cover)
    assert not _close(_rgb(result.video, 1), cover)
    latest = _latest(desk)
    assert latest["reel"] == "reels/ep01/reel-ep01-v1.mp4"
    assert latest["cover"] == f"reels/ep01/{result.cover.name}"
    assert latest["post"] == "reels/ep01/post-ep01-v1.txt"
    rows = list(csv.DictReader((desk / "reels" / "metrics.csv").open(encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["reel_file"] == "reel-ep01-v1.mp4"
    post = (folder / "post-ep01-v1.txt").read_text(encoding="utf-8")
    assert "To do in Instagram" in post or "Edit cover" in post
    assert "Reel from rendered footage (local, $0, no server call)" in said


def test_a_deploy_without_uploads_gets_the_reel_cut_locally_after_finish(
    reel_desk: Path,  # noqa: F811
    reel_server: FakeReelServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reel_server.upload_refused = "operator_upload_unavailable"
    finals: list[Any] = []
    monkeypatch.setattr(
        cover_frame,
        "cover_one_take_final",
        lambda desk, episode, made, out: finals.append(made),
    )
    out = io.StringIO()

    result = auto_reel(reel_desk, 1, trigger="finish", stream=out)

    said = out.getvalue()
    _assert_made_locally(reel_desk, result, said)
    assert _latest(reel_desk)["made_by"] == "finish"
    assert said.count(LINE) == 1 and said.rstrip().endswith(LINE)
    assert "Reel not made" not in said
    assert reel_server.requests == []
    # The 15 s final's cover step runs on the reel made locally too.
    assert finals == [result]


def test_reel_by_hand_falls_back_and_the_final_gets_the_cover(
    reel_desk: Path,  # noqa: F811
    reel_server: FakeReelServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from creation.cli_produce import main

    reel_server.upload_refused = "operator_upload_unavailable"
    finals: list[Any] = []
    monkeypatch.setattr(
        cover_frame,
        "cover_one_take_final",
        lambda desk, episode, made, out: finals.append(made),
    )

    assert main(["reel", "--desk", str(reel_desk), "--episode", "1"]) == 0

    said = capsys.readouterr().out
    assert LINE in said
    assert (reel_desk / "reels" / "ep01" / "reel-ep01-v1.mp4").is_file()
    assert len(finals) == 1 and finals[0].cover is not None


@pytest.mark.parametrize(
    ("setting", "why"),
    [
        ("unreachable", "the server reel engine did not answer: ConnectError"),
        (
            "no_operator_route",
            "the server reel engine has no operator reel route on this deployment",
        ),
    ],
)
def test_a_silent_server_or_one_without_the_route_also_falls_back(
    reel_desk: Path,  # noqa: F811
    reel_server: FakeReelServer,
    setting: str,
    why: str,
) -> None:
    setattr(reel_server, setting, True)
    out = io.StringIO()

    result = run_reel(reel_desk, episode=1, stream=out)

    assert (
        result.video is not None and result.video.is_file() and result.cover is not None
    )
    assert f"Reel made locally ({why}); same rules" in out.getvalue()
    assert _latest(reel_desk)["reel"] == "reels/ep01/reel-ep01-v1.mp4"


def test_a_refusal_about_the_content_still_stops(
    reel_desk: Path,  # noqa: F811
    reel_server: FakeReelServer,
) -> None:
    from creation.post.reel_server import ReelServerError

    reel_server.refuse = "episode_reel_invalid: plan: segments name t9"
    out = io.StringIO()

    assert auto_reel(reel_desk, 1, trigger="finish", stream=out) is None
    said = out.getvalue()
    assert "⚠ Reel not made" in said and "episode_reel_invalid" in said
    assert "made locally" not in said
    with pytest.raises(ReelServerError, match="episode_reel_invalid"):
        run_reel(reel_desk, episode=1, stream=io.StringIO())
    assert not list((reel_desk / "reels").rglob("*.mp4"))


def test_the_clips_say_they_need_the_server_and_how_to_make_them_later(
    reel_desk: Path,  # noqa: F811
    reel_server: FakeReelServer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from creation.cli_produce import main

    reel_server.upload_refused = "operator_upload_unavailable"
    out = io.StringIO()

    assert auto_clips(reel_desk, 1, trigger="finish", stream=out) is None

    said = out.getvalue()
    assert (
        "Clips not made: the TikTok clips are cut only by the server's reel engine"
        in said
    )
    assert "no local clips engine" in said and "operator_upload_unavailable" in said
    assert f"reel --desk {reel_desk} --episode 1 --clips 3" in said
    assert "the finish is done" in said.lower()
    assert not episode_clips(reel_desk, 1).exists()

    assert (
        main(["reel", "--desk", str(reel_desk), "--episode", "1", "--clips", "3"]) == 1
    )
    assert "no local clips engine" in capsys.readouterr().err
