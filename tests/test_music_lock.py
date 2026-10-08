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
from test_voice_mode import model_show  # noqa: F401 - fixture
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
from creation.rules_epoch import run_rules_epoch
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


@pytest.fixture
def old_desk(post_desk: Path) -> Path:
    """A desk created before 6 Oct 2026: the music lock applies (existing shows only)."""

    run_rules_epoch(post_desk, set_to="legacy", out=io.StringIO())
    return post_desk


MUSIC_IN_TRACK = facts_with({**NATIVE, "music": {"laid": True}})
STEM_URL = "https://media.example.com/tenants/t/drama/tracks/stem.wav"
MUSIC_AND_STEM = facts_with({**NATIVE, "music": {"laid": True, "stem_url": STEM_URL}})
SHOW_BED = "shared/beds/show-bed-v1.mp3"


# --- the lock, read once ------------------------------------------------------------------------


def test_the_first_finished_episode_decides_and_the_read_is_stored_once(
    old_desk: Path,
) -> None:
    assert infer_desk_music_lock(old_desk) is None
    # Seedlings: ep 1 finished with the show's bed, ep 2 came back with the harness's music.
    _record(old_desk, 1, "t1", bed=SHOW_BED)
    _record(old_desk, 1, "t2", bed=SHOW_BED)
    _record(old_desk, 2, "t1", bed=None, music_in_take=True)

    lock = desk_music_lock(old_desk, out=io.StringIO())

    assert lock == DeskMusicLock(
        kind="finish", origin="inferred", episode=1, bed=SHOW_BED
    )
    assert stored_desk_music_lock(old_desk) == lock
    assert load_series(old_desk).extra["music_lock"]["kind"] == "finish"
    logged = [
        json.loads(line)
        for line in (old_desk / MUSIC_LOCK_LOG).read_text().splitlines()
    ]
    assert [entry["origin"] for entry in logged] == ["inferred"]
    # Stored: a later read does not read the records again.
    _record(old_desk, 1, "t3", bed=None, music_in_take=True)
    _record(old_desk, 1, "t4", bed=None, music_in_take=True)
    _record(old_desk, 1, "t5", bed=None, music_in_take=True)
    assert desk_music_lock(old_desk, out=io.StringIO()) == lock


def test_a_show_whose_first_episode_carried_the_harness_music_is_in_take(
    old_desk: Path,
) -> None:
    _record(old_desk, 1, "t1", bed=None, music_in_take=True)
    assert infer_desk_music_lock(old_desk) == DeskMusicLock(
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
def test_episode_n_plus_1_with_music_and_no_stem_needs_a_refilm_and_says_so_plainly(
    old_desk: Path,
) -> None:
    # Ep 1, take 1: the take carries no music; the show's first finish lays the bed and keeps the lock.
    _take(old_desk, "t1", facts_with(NATIVE))
    out = io.StringIO()
    first = run_finish(
        old_desk, sfx_render=fake_sfx([]), bed_maker=_made_bed([]), facts_fetcher=lambda *a: None,
        thumbnail=False, stream=out,
    )  # fmt: skip
    assert first.complete, out.getvalue()
    lock = stored_desk_music_lock(old_desk)
    assert lock is not None and (lock.kind, lock.origin, lock.episode) == (
        "finish",
        "first_finish",
        1,
    )
    assert lock.bed is not None and Path(lock.bed).name.startswith("show-bed-v")

    # A server update bakes the harness's music into the next take: finish never silently drops the bed.
    _take(old_desk, "t2", MUSIC_IN_TRACK)
    with pytest.raises(
        ValueError, match="NOT DONE: this show's music is laid in finish"
    ):
        run_finish(
            old_desk, take_id="t2", sfx_render=fake_sfx([]), bed_maker=_never, facts_fetcher=lambda *a: None,
            thumbnail=False, stream=io.StringIO(),
        )  # fmt: skip


@needs_ffmpeg
def test_a_take_finished_before_keeps_the_music_it_was_finished_with(
    old_desk: Path,
) -> None:
    _record(old_desk, 1, "t1", bed=None, music_in_take=True)
    store_desk_music_lock(
        old_desk,
        DeskMusicLock(kind="finish", origin="inferred", episode=1),
        out=io.StringIO(),
    )
    _take(old_desk, "t1", MUSIC_IN_TRACK)

    result = run_finish(
        old_desk, sfx_render=fake_sfx([]), bed_maker=_never, facts_fetcher=lambda *a: None,
        thumbnail=False, stream=io.StringIO(),
    )  # fmt: skip

    assert result.complete and result.music_in_take


@needs_ffmpeg
def test_an_explicit_change_lets_the_harness_music_through_and_is_logged(
    old_desk: Path,
) -> None:
    store_desk_music_lock(
        old_desk,
        DeskMusicLock(kind="finish", origin="inferred", episode=1),
        out=io.StringIO(),
    )
    changed = DeskMusicLock(
        kind="in_take",
        origin="changed",
        episode=2,
        reason="the founder chose scored music",
    )
    store_desk_music_lock(old_desk, changed, out=io.StringIO())
    _take(old_desk, "t2", MUSIC_IN_TRACK)

    result = run_finish(
        old_desk, take_id="t2", sfx_render=fake_sfx([]), bed_maker=_never, facts_fetcher=lambda *a: None,
        thumbnail=False, stream=io.StringIO(),
    )  # fmt: skip

    assert result.complete and result.music_in_take
    logged = [
        json.loads(line)
        for line in (old_desk / MUSIC_LOCK_LOG).read_text().splitlines()
    ]
    assert (
        logged[-1]["reason"] == "the founder chose scored music"
        and logged[-1]["origin"] == "changed"
    )
    assert "changed on purpose: the founder chose scored music" in changed.line()


def test_a_change_needs_a_reason_and_a_known_kind(old_desk: Path) -> None:
    with pytest.raises(ValueError, match="needs a reason"):
        run_music_lock(old_desk, set_to="in-take", reason=" ")
    with pytest.raises(ValueError, match="finish or in-take"):
        run_music_lock(old_desk, set_to="scored", reason="x")
    assert stored_desk_music_lock(old_desk) is None


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


# --- founder decision after review (8 Oct): existing shows only; baked-in music is fixed by mixing ----------


def _stem_fetch(calls: list[str]):
    from conftest import make_tone

    def fetch(url: str, dest: Path) -> Path:
        calls.append(url)
        return make_tone(dest, seconds=15.0, freq=300, volume=0.3)

    return fetch


@needs_ffmpeg
def test_baked_in_music_with_a_stem_is_remixed_under_the_shows_bed_not_refilmed(
    old_desk: Path,
) -> None:
    from creation.post.finish_record import latest_finish_record

    store_desk_music_lock(
        old_desk,
        DeskMusicLock(kind="finish", origin="inferred", episode=1),
        out=io.StringIO(),
    )
    _take(old_desk, "t2", MUSIC_AND_STEM)
    calls: list[str] = []
    out = io.StringIO()

    result = run_finish(
        old_desk, take_id="t2", sfx_render=fake_sfx([]), bed_maker=_made_bed([]), facts_fetcher=lambda *a: None,
        stem_fetcher=_stem_fetch(calls), thumbnail=False, stream=out,
    )  # fmt: skip

    assert result.complete, out.getvalue()
    assert calls == [STEM_URL]
    assert result.source.name.startswith("take-ep01-t2-stem-v")
    assert not result.music_in_take
    assert next(s for s in result.steps if s.step == "bed").status == "ran"
    record = latest_finish_record(old_desk, 1, "t2")
    assert record is not None and record.bed is not None and not record.music_in_take
    assert "swapped for the take's music-free stem" in out.getvalue()


def test_the_stem_remix_applies_only_to_a_new_take_with_music_on_a_finish_show() -> (
    None
):
    from creation.music_lock import stem_remix_applies

    finish = DeskMusicLock(kind="finish", origin="inferred")
    assert stem_remix_applies(
        finish, music_in_take=True, finished_before=False, stem_url=STEM_URL
    )
    for lock, music, before, stem in (
        (finish, True, False, None),
        (finish, True, True, STEM_URL),
        (finish, False, False, STEM_URL),
        (DeskMusicLock(kind="in_take", origin="inferred"), True, False, STEM_URL),
        (None, True, False, STEM_URL),
    ):
        assert not stem_remix_applies(
            lock, music_in_take=music, finished_before=before, stem_url=stem
        )
    # With a stem there is nothing to refuse.
    assert (
        take_music_refusal(
            finish,
            music_in_take=True,
            music_why="x",
            finished_before=False,
            desk=Path("."),
            stem_url=STEM_URL,
        )
        is None
    )


@needs_ffmpeg
def test_a_new_show_has_no_lock_and_finishes_exactly_as_before(post_desk: Path) -> None:
    from creation.post.finish_record import latest_finish_record

    # Created since 6 Oct: earlier episodes finished with a bed, a new take with the harness's music.
    _record(post_desk, 1, "t1", bed=SHOW_BED)
    _take(post_desk, "t2", MUSIC_AND_STEM)

    result = run_finish(
        post_desk, take_id="t2", sfx_render=fake_sfx([]), bed_maker=_never, facts_fetcher=lambda *a: None,
        stem_fetcher=_never, thumbnail=False, stream=io.StringIO(),
    )  # fmt: skip

    assert result.complete and result.music_in_take
    assert desk_music_lock(post_desk) is None
    assert "music_lock" not in load_series(post_desk).extra
    assert not (post_desk / MUSIC_LOCK_LOG).exists()
    record = latest_finish_record(post_desk, 1, "t2")
    assert record is not None and record.music_in_take and record.bed is None
    with pytest.raises(ValueError, match="scores every episode fresh"):
        run_music_lock(post_desk, set_to="finish", reason="keep it")


class _LockRun(_Run):
    def __init__(self, kind: str | None) -> None:
        super().__init__([])
        self.kind = kind

    def get_optional(self, path: str) -> tuple[int, Any]:
        if self.kind is None:
            return 404, None
        return 200, {"music_lock": {"kind": self.kind}, "source": "inferred"}


def test_music_by_finish_is_sent_only_for_an_existing_show_locked_to_finish(
    post_desk: Path, tmp_path: Path
) -> None:
    from creation.music_lock import music_by_finish_wanted

    spine = {"spine_id": "s1"}
    # New show: never, whatever the server or a desk file says.
    assert not music_by_finish_wanted(post_desk, _LockRun("finish"), spine)
    assert not music_by_finish_wanted(None, _LockRun("finish"), spine)
    run_rules_epoch(post_desk, set_to="legacy", out=io.StringIO())
    # Existing show with no desk lock yet: the server's lock decides.
    assert music_by_finish_wanted(post_desk, _LockRun("finish"), spine)
    assert not music_by_finish_wanted(post_desk, _LockRun("track"), spine)
    assert not music_by_finish_wanted(post_desk, _LockRun(None), spine)
    # The desk's lock wins once kept.
    store_desk_music_lock(
        post_desk, DeskMusicLock(kind="in_take", origin="inferred"), out=io.StringIO()
    )
    assert not music_by_finish_wanted(post_desk, _LockRun("finish"), spine)
    store_desk_music_lock(
        post_desk,
        DeskMusicLock(kind="finish", origin="changed", reason="x"),
        out=io.StringIO(),
    )
    assert music_by_finish_wanted(post_desk, _LockRun(None), spine)


def test_a_locked_existing_show_whose_music_is_laid_in_finish_films_with_music_by_finish(
    desk: Path,
    model_show: Any,  # noqa: F811 - the imported fixture
) -> None:
    from conftest import set_phase
    from test_voice_gate import VIDEO, _board_yes, _ready_to_film

    from creation.episode_commands import run_film

    model_show.spine_doc["voice_mode"] = "locked"
    model_show.spine_doc["voice_approvals"] = [
        {
            "cast_id": c,
            "provider_voice": v,
            "how": "kept",
            "approved_at": "2026-10-05T12:00:00Z",
        }
        for c, v in (("cast_hana", "Aria"), ("cast_ren", "Liam"))
    ]
    _board_yes(desk)
    _ready_to_film(model_show)
    set_phase(desk, "complete", film_estimates={"ep01": 1.2})
    run_film(desk, episode=1, confirm_spend=True)
    # A new show on locked voices: the body is exactly main's.
    assert "music_by_finish" not in model_show.posted(VIDEO)[-1]

    run_rules_epoch(desk, set_to="legacy", out=io.StringIO())
    store_desk_music_lock(
        desk,
        DeskMusicLock(kind="finish", origin="inferred", episode=1),
        out=io.StringIO(),
    )
    set_phase(desk, "complete", film_estimates={"ep01": 1.2})
    run_film(desk, episode=1, cause="seedlings ep 4", confirm_spend=True)
    assert model_show.posted(VIDEO)[-1]["music_by_finish"] is True
