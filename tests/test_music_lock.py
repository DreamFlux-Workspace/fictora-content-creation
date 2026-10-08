"""A show's music stays the same across its episodes (founder decision, 2026-10-08).

- L-20261005-2 (Seedlings): early episodes finished with the show's bed; later
  takes came back with the harness's music baked in and ``finish`` skipped the
  bed, so the show's music changed mid-season.
- L-20261005-22 (Beach Court): the same, with the show's pinned theme.

The desk keeps where the show's music comes from (``series.json``
``music_lock``): read once from the first finished episode, else written by
the show's first finish. A take never finished before that comes back with
music on a ``finish`` show is NOT DONE (never silently bed-less); a take
finished before keeps its music; ``music-lock --set ... --reason`` changes it
on purpose and logs it.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import make_take, needs_ffmpeg
from test_harness_music import _made_bed, _never
from test_post_finish import TWO_LINES, fake_sfx
from test_target_audio_soundtrack import NATIVE, facts_with

from creation.music_lock import (
    MUSIC_LOCK_LOG,
    DeskMusicLock,
    desk_music_lock,
    infer_desk_music_lock,
    run_music_lock,
    server_kind,
    set_server_music_lock,
    store_desk_music_lock,
    stored_desk_music_lock,
    take_music_refusal,
)
from creation.ops.state import load_series
from creation.post.finish import run_finish
from creation.post.finish_record import write_finish_record


def _record(
    desk: Path,
    episode: int,
    take_id: str,
    *,
    bed: str | None,
    music_in_take: bool = False,
) -> None:
    takes = desk / f"ep{episode:02d}" / "takes"
    takes.mkdir(parents=True, exist_ok=True)
    final = takes / f"take-ep{episode:02d}-{take_id}-final-v1.mp4"
    write_finish_record(
        desk, episode=episode, take_id=take_id, complete=True, pre_bed=final, master=final, final=final,
        bed=desk / bed if bed else None, bed_db=-6.0, duck_db=None, music_in_take=music_in_take,
    )  # fmt: skip


def _take(desk: Path, take_id: str, facts: dict) -> Path:
    raw = make_take(
        desk / "ep01" / "takes" / f"take-ep01-{take_id}-raw-v1.mp4", tones=TWO_LINES
    )
    (desk / "ep01" / "api" / f"take-facts-ep01-{take_id}-v1.json").write_text(
        json.dumps(facts)
    )
    return raw


MUSIC_IN_TRACK = facts_with({**NATIVE, "music": {"laid": True}})
SHOW_BED = "shared/beds/show-bed-v1.mp3"


# --- the lock, read once ------------------------------------------------------------------------


def test_the_first_finished_episode_decides_and_the_read_is_stored_once(
    post_desk: Path,
) -> None:
    assert infer_desk_music_lock(post_desk) is None
    # Seedlings: ep 1 finished with the show's bed, ep 2 came back with the harness's music.
    _record(post_desk, 1, "t1", bed=SHOW_BED)
    _record(post_desk, 1, "t2", bed=SHOW_BED)
    _record(post_desk, 2, "t1", bed=None, music_in_take=True)

    lock = desk_music_lock(post_desk, out=io.StringIO())

    assert lock == DeskMusicLock(
        kind="finish", origin="inferred", episode=1, bed=SHOW_BED
    )
    assert stored_desk_music_lock(post_desk) == lock
    assert load_series(post_desk).extra["music_lock"]["kind"] == "finish"
    logged = [
        json.loads(line)
        for line in (post_desk / MUSIC_LOCK_LOG).read_text().splitlines()
    ]
    assert [entry["origin"] for entry in logged] == ["inferred"]
    # Stored: a later read does not read the records again.
    _record(post_desk, 1, "t3", bed=None, music_in_take=True)
    _record(post_desk, 1, "t4", bed=None, music_in_take=True)
    _record(post_desk, 1, "t5", bed=None, music_in_take=True)
    assert desk_music_lock(post_desk, out=io.StringIO()) == lock


def test_a_show_whose_first_episode_carried_the_harness_music_is_in_take(
    post_desk: Path,
) -> None:
    _record(post_desk, 1, "t1", bed=None, music_in_take=True)
    assert infer_desk_music_lock(post_desk) == DeskMusicLock(
        kind="in_take", origin="inferred", episode=1
    )


def test_the_refusal_is_only_for_a_new_take_with_music_on_a_finish_show(
    tmp_path: Path,
) -> None:
    finish = DeskMusicLock(kind="finish", origin="inferred", episode=1, bed=SHOW_BED)
    in_take = DeskMusicLock(kind="in_take", origin="inferred", episode=1)
    why = "take facts music.laid"

    refusal = take_music_refusal(
        finish, music_in_take=True, music_why=why, finished_before=False, desk=tmp_path
    )
    assert (
        refusal is not None
        and refusal.startswith("!! NOT DONE")
        and "show-bed-v1.mp3" in refusal
    )
    assert "L-20261005-2" in refusal and "music-lock" in refusal
    for lock, music, before in (
        (finish, False, False),
        (finish, True, True),
        (in_take, True, False),
        (None, True, False),
    ):
        assert (
            take_music_refusal(
                lock,
                music_in_take=music,
                music_why=why,
                finished_before=before,
                desk=tmp_path,
            )
            is None
        )


# --- through finish ------------------------------------------------------------------------------


@needs_ffmpeg
def test_episode_n_plus_1_keeps_the_shows_bed_after_the_server_starts_baking_music(
    post_desk: Path,
) -> None:
    # Ep 1, take 1: the take carries no music; the show's first finish lays the bed and keeps the lock.
    _take(post_desk, "t1", facts_with(NATIVE))
    out = io.StringIO()
    first = run_finish(
        post_desk, sfx_render=fake_sfx([]), bed_maker=_made_bed([]), facts_fetcher=lambda *a: None,
        thumbnail=False, stream=out,
    )  # fmt: skip
    assert first.complete, out.getvalue()
    lock = stored_desk_music_lock(post_desk)
    assert lock is not None and (lock.kind, lock.origin, lock.episode) == (
        "finish",
        "first_finish",
        1,
    )
    assert lock.bed is not None and Path(lock.bed).name.startswith("show-bed-v")

    # A server update bakes the harness's music into the next take: finish never silently drops the bed.
    _take(post_desk, "t2", MUSIC_IN_TRACK)
    with pytest.raises(
        ValueError, match="NOT DONE: this show's music is laid in finish"
    ):
        run_finish(
            post_desk, take_id="t2", sfx_render=fake_sfx([]), bed_maker=_never, facts_fetcher=lambda *a: None,
            thumbnail=False, stream=io.StringIO(),
        )  # fmt: skip


@needs_ffmpeg
def test_a_take_finished_before_keeps_the_music_it_was_finished_with(
    post_desk: Path,
) -> None:
    _record(post_desk, 1, "t1", bed=None, music_in_take=True)
    store_desk_music_lock(
        post_desk,
        DeskMusicLock(kind="finish", origin="inferred", episode=1),
        out=io.StringIO(),
    )
    _take(post_desk, "t1", MUSIC_IN_TRACK)

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), bed_maker=_never, facts_fetcher=lambda *a: None,
        thumbnail=False, stream=io.StringIO(),
    )  # fmt: skip

    assert result.complete and result.music_in_take


@needs_ffmpeg
def test_an_explicit_change_lets_the_harness_music_through_and_is_logged(
    post_desk: Path,
) -> None:
    store_desk_music_lock(
        post_desk,
        DeskMusicLock(kind="finish", origin="inferred", episode=1),
        out=io.StringIO(),
    )
    changed = DeskMusicLock(
        kind="in_take",
        origin="changed",
        episode=2,
        reason="the founder chose scored music",
    )
    store_desk_music_lock(post_desk, changed, out=io.StringIO())
    _take(post_desk, "t2", MUSIC_IN_TRACK)

    result = run_finish(
        post_desk, take_id="t2", sfx_render=fake_sfx([]), bed_maker=_never, facts_fetcher=lambda *a: None,
        thumbnail=False, stream=io.StringIO(),
    )  # fmt: skip

    assert result.complete and result.music_in_take
    logged = [
        json.loads(line)
        for line in (post_desk / MUSIC_LOCK_LOG).read_text().splitlines()
    ]
    assert (
        logged[-1]["reason"] == "the founder chose scored music"
        and logged[-1]["origin"] == "changed"
    )
    assert "changed on purpose: the founder chose scored music" in changed.line()


def test_a_change_needs_a_reason_and_a_known_kind(post_desk: Path) -> None:
    with pytest.raises(ValueError, match="needs a reason"):
        run_music_lock(post_desk, set_to="in-take", reason=" ")
    with pytest.raises(ValueError, match="finish or in-take"):
        run_music_lock(post_desk, set_to="scored", reason="x")
    assert stored_desk_music_lock(post_desk) is None


class _Run:
    def __init__(self, answers: list[tuple[int, Any]]) -> None:
        self.answers = answers
        self.posted: list[tuple[str, dict[str, Any]]] = []

    def get_optional(self, path: str) -> tuple[int, Any]:
        return 404, None

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ):  # noqa: ANN201
        self.posted.append((path, body))
        return self.answers.pop(0)

    def spine(self, spine_id: str) -> dict[str, Any]:
        return {"spine_id": spine_id, "spine_version": "sha256:" + "1" * 64}


def test_the_server_lock_is_sent_with_its_reason_and_an_older_server_is_said() -> None:
    assert (
        server_kind("finish", locked_voices=True),
        server_kind("in_take", locked_voices=True),
    ) == ("finish", "track")
    assert server_kind("in_take", locked_voices=False) == "model"
    stale = (409, {"error": {"code": "spine_version_conflict"}})
    run = _Run(
        [stale, (200, {"applied": True, "notice": "Earlier episodes keep their music"})]
    )
    spine = {"spine_id": "s1", "spine_version": "sha256:" + "0" * 64}

    answer = set_server_music_lock(run, spine, "finish", reason="keep the theme")

    assert answer["applied"] is True
    assert [body["spine_version"][-1] for _path, body in run.posted] == ["0", "1"]
    assert run.posted[0] == (
        "/v1/spines/s1/music-lock",
        {**run.posted[0][1], "kind": "finish", "reason": "keep the theme"},
    )
    with pytest.raises(RuntimeError, match="older deploy"):
        set_server_music_lock(_Run([(404, None)]), spine, "finish", reason="x")
