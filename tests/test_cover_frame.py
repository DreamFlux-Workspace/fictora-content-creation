"""The cover is the video's first frame: the preview Discord, WhatsApp and the phones show (7 Oct 2026).

Exactly frame 0 is replaced; the length, the frame count and the sound stay as
they were, and a cover attached inside the file is kept.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conftest import needs_ffmpeg
from creation.post.cover_frame import episode_cover, put_cover_on_first_frame
from creation.post.media import probe_video, video_streams

pytestmark = needs_ffmpeg


def _clip(path: Path) -> Path:
    """One second of blue at the house 24 fps with a tone: no frame is red."""

    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=180x320:r=24:d=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip
    return path


def _red(path: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=1080x1920", "-frames:v", "1", str(path)],
        check=True,
    )  # fmt: skip
    return path


def _frame_rgb(video: Path, index: int) -> tuple[int, int, int]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{index}),scale=1:1",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    return raw[0], raw[1], raw[2]


def _frames(video: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return int(json.loads(out)["streams"][0]["nb_read_frames"])


def test_only_the_first_frame_becomes_the_cover(tmp_path: Path) -> None:
    video = _clip(tmp_path / "episode.mp4")
    out = put_cover_on_first_frame(
        video, _red(tmp_path / "cover.jpg"), tmp_path / "out.mp4"
    )

    red, green, blue = _frame_rgb(out, 0)
    assert red > 200 and blue < 60  # the cover
    red, green, blue = _frame_rgb(out, 1)
    assert blue > 200 and red < 60  # the picture again
    before, after = probe_video(video), probe_video(out)
    assert (after.width, after.height, after.fps) == (
        before.width,
        before.height,
        before.fps,
    )
    assert after.duration_seconds == pytest.approx(before.duration_seconds, abs=0.05)
    assert _frames(out) == _frames(video)
    assert after.has_audio


def test_a_cover_attached_inside_the_file_is_kept(tmp_path: Path) -> None:
    video = _clip(tmp_path / "episode.mp4")
    art = tmp_path / "art.jpg"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=green:s=180x320", "-frames:v", "1", str(art)],
        check=True,
    )  # fmt: skip
    with_art = tmp_path / "with-art.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-i", str(art), "-map", "0", "-map", "1", "-c", "copy",
         "-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic", str(with_art)],
        check=True,
    )  # fmt: skip

    out = put_cover_on_first_frame(
        with_art, _red(tmp_path / "cover.jpg"), tmp_path / "out.mp4"
    )

    assert video_streams(out)[1] is not None


def test_it_never_overwrites(tmp_path: Path) -> None:
    video = _clip(tmp_path / "episode.mp4")
    with pytest.raises(FileExistsError):
        put_cover_on_first_frame(video, _red(tmp_path / "cover.jpg"), video)


def test_the_newest_finished_reel_cover_is_the_episodes(tmp_path: Path) -> None:
    folder = tmp_path / "reels" / "ep02"
    folder.mkdir(parents=True)
    for name in (
        "reel-ep02-draft-v4-cover-v1.jpg",
        "reel-ep02-v2-cover-v1.jpg",
        "reel-ep02-v3-cover-v1.jpg",
        "reel-ep02-v3-cover-v2.jpg",
        "reel-ep03-v9-cover-v1.jpg",
    ):
        (folder / name).write_bytes(b"jpg")

    assert episode_cover(tmp_path, 2) == folder / "reel-ep02-v3-cover-v2.jpg"
    assert episode_cover(tmp_path, 5) is None


def test_an_older_desks_flat_reel_cover_is_found(tmp_path: Path) -> None:
    flat = tmp_path / "reels"
    flat.mkdir()
    (flat / "reel-ep01-v1-cover-v1.jpg").write_bytes(b"jpg")

    assert episode_cover(tmp_path, 1) == flat / "reel-ep01-v1-cover-v1.jpg"


def test_a_failed_cover_frame_leaves_the_video_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Older desks never stop on it: the reel or the join is kept as written, with a ⚠ line.
    from creation.post import cover_frame
    from creation.post.media import MediaToolError

    video = _clip(tmp_path / "episode.mp4")
    before = video.read_bytes()

    def fail(*_: object) -> Path:
        raise MediaToolError("ffmpeg failed")

    monkeypatch.setattr(cover_frame, "put_cover_on_first_frame", fail)

    warning = cover_frame.cover_first_frame_in_place(
        video, _red(tmp_path / "cover.jpg")
    )

    assert warning is not None and warning.startswith("⚠ cover frame skipped")
    assert video.read_bytes() == before
