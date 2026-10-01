"""Take review says what the server did at a take's ends (fictora-drama #555 / #559).

The server holds board frames on the stored take, keeps the provider's original
when it does (``board_frames.original``) and leaves a run it is not sure of as
filmed (``board_frames.unsure``). Review names all three states; a take with
nothing held and an unsure run is never reported as "held 0"; a take held
before #559 says no original exists. ``review --original`` fetches the
original, checks its sha256 and length, and writes a side-by-side.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from argparse import Namespace
from pathlib import Path

import pytest
from conftest import make_take, needs_ffmpeg

from creation.post.review import review_take
from creation.post.review_command import dispatch_review
from creation.post.take_timeline import fetch_original, server_board_frames

ORIGINAL_URL = "https://media.test/tenants/t/drama/media/clip/abc.mp4"
REASON = "the first frame does not look like the board (likeness 0.12 < 0.6)"


def _record(
    *,
    head: int = 8,
    tail: int = 0,
    original: dict | None = None,
    unsure: list | None = None,
) -> dict:
    record: dict = {"head_frames": head, "tail_frames": tail, "head_s": round(head / 24, 3),
                    "tail_s": round(tail / 24, 3), "frame_rate": 24.0, "timeline_shift_s": 0.0}  # fmt: skip
    if original is not None:
        record["original"] = original
    if unsure is not None:
        record["unsure"] = unsure
    return {"take_facts": {"job_id": "job_take_1", "board_frames": record}}


def _kept(sha: str = "f" * 64, length: int = 1234) -> dict:
    return {"url": ORIGINAL_URL, "content_sha256": sha, "content_length": length}


def test_held_frames_with_the_original_kept_name_the_url_and_sha() -> None:
    held = server_board_frames(_record(head=8, tail=2, original=_kept()))

    assert held is not None
    lines = held.lines()
    assert len(lines) == 1
    assert "server held 8 start / 2 end frame(s)" in lines[0]
    assert "original kept: " + ORIGINAL_URL in lines[0]
    assert "sha256 ffffffffffff…" in lines[0] and "1234 bytes" in lines[0]
    assert "review --original" in lines[0]


def test_nothing_held_with_an_unsure_run_is_never_reported_as_held_zero() -> None:
    held = server_board_frames(
        _record(head=0, unsure=[{"end": "head", "frames": 9, "reason": REASON}])
    )

    assert held is not None
    text = "\n".join(held.lines())
    assert "held 0" not in text and "server held" not in text
    assert "nothing held" not in text
    assert (
        f"possible board frames at the start (9 frame(s)) left as filmed: {REASON}"
        in text
    )


def test_an_unsure_end_is_reported_next_to_the_end_that_was_held() -> None:
    held = server_board_frames(
        _record(
            head=2,
            original=_kept(),
            unsure=[{"end": "tail", "frames": 12, "reason": "cap run"}],
        )
    )

    assert held is not None
    lines = held.lines()
    assert lines[0].startswith("server held 2 start / 0 end frame(s)")
    assert lines[1].startswith(
        "!! possible board frames at the end (12 frame(s)) left as filmed: cap run"
    )


def test_held_frames_with_no_original_say_it_was_stored_before_559() -> None:
    held = server_board_frames(_record(head=10))

    assert held is not None
    assert (
        "held frames but no original stored (before fictora-drama #559)"
        in held.lines()[0]
    )


def _facts_file(desk: Path, payload: dict) -> Path:
    path = desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@needs_ffmpeg
def test_review_shows_the_server_hold_the_original_and_the_unsure_end(
    post_desk: Path,
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")
    _facts_file(
        post_desk,
        _record(
            head=3,
            original=_kept(),
            unsure=[{"end": "tail", "frames": 9, "reason": REASON}],
        ),
    )

    result = review_take(post_desk, episode=1, take_id="t1")

    board = next(s for s in result.sections if s.name == "Board")
    text = "\n".join(board.details)
    assert "server held 3 start / 0 end frame(s)" in text
    assert ORIGINAL_URL in text
    assert "possible board frames at the end (9 frame(s)) left as filmed" in text
    assert board.data["server_held"]["original"]["url"] == ORIGINAL_URL
    assert board.data["server_held"]["unsure"] == [
        {"end": "tail", "frames": 9, "reason": REASON}
    ]


@needs_ffmpeg
def test_the_original_is_fetched_checked_and_compared_with_the_held_take(
    post_desk: Path, tmp_path: Path
) -> None:
    held = make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")
    provider = make_take(tmp_path / "provider.mp4", colour="white")
    data = provider.read_bytes()
    payload = _record(
        head=3, original=_kept(hashlib.sha256(data).hexdigest(), len(data))
    )

    def local(url: str, dest: Path) -> Path:
        assert url == ORIGINAL_URL
        shutil.copyfile(provider, dest)
        return dest

    copy = fetch_original(
        post_desk, episode=1, take_id="t1", payload=payload, held_take=held, fetch=local
    )

    assert copy.path.name == "take-ep01-t1-original-v1.mp4"
    assert copy.path.read_bytes() == data
    assert copy.compare is not None and copy.compare.is_file()


def test_an_original_whose_sha_does_not_match_is_not_kept(
    post_desk: Path, tmp_path: Path
) -> None:
    def wrong(url: str, dest: Path) -> Path:
        dest.write_bytes(b"not the provider's take")
        return dest

    with pytest.raises(ValueError, match="does not match the server's record"):
        fetch_original(post_desk, episode=1, take_id="t1", payload=_record(original=_kept()),
                       held_take=None, fetch=wrong)  # fmt: skip

    assert not list((post_desk / "ep01" / "takes").glob("*original*"))


def test_no_original_before_559_is_said_plainly(post_desk: Path) -> None:
    with pytest.raises(ValueError, match="before fictora-drama #559"):
        fetch_original(
            post_desk, episode=1, take_id="t1", payload=_record(head=4), held_take=None
        )


@needs_ffmpeg
def test_review_original_flag_reports_what_it_fetched(
    post_desk: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")
    _facts_file(post_desk, _record(head=4))
    out = io.StringIO()

    code = dispatch_review(
        Namespace(desk=post_desk, episode=1, take_id="t1", take_file=None, board=None, words_json=None,
                  transcribe=False, json=False, original=True),
        stream=out,
    )  # fmt: skip

    assert code == 0
    assert (
        "original: not fetched: no original to fetch: held frames but no original stored"
        in out.getvalue()
    )
