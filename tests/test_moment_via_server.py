"""``moment`` (13 Oct 2026): finished episodes joined into one TikTok part by the server; clips are gone."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from conftest import needs_ffmpeg
from reel_fake_server import FakeReelServer
from test_reel import reel_desk as legacy_reel_desk  # noqa: F401  (fixture)
from test_reel_via_server import reel_desk  # noqa: F401  (fixture)

from creation.post.moment_via_server import (
    MAX_EPISODES,
    MOMENT_RECIPE,
    PART_TAKE_SECONDS,
    group_parts,
    make_moment,
    moment_offer,
    moment_paths,
    offer_moment_after_finish,
    parse_episodes,
)


def test_episode_lists() -> None:
    assert parse_episodes("1-3") == [1, 2, 3]
    assert parse_episodes("3, 1,2") == [1, 2, 3]
    for bad in ("", "0-2", "a-b", "1-9", "1,x"):
        with pytest.raises(ValueError):
            parse_episodes(bad)
    assert MAX_EPISODES == 6


def test_auto_groups_episodes_into_parts_of_about_a_minute() -> None:
    # Six 30 s episodes: parts of three (90 s of takes, about a minute after the cut).
    assert group_parts([(i, 30.0) for i in range(1, 7)]) == [[1, 2, 3], [4, 5, 6]]
    # A short last episode joins the part before it rather than standing alone.
    assert group_parts([(1, 40.0), (2, 40.0), (3, 15.0)]) == [[1, 2, 3]]
    # Long episodes (over the target) are a part each.
    assert group_parts([(1, PART_TAKE_SECONDS + 5), (2, PART_TAKE_SECONDS + 5)]) == [
        [1],
        [2],
    ]


def test_moment_files_never_overwrite(tmp_path: Path) -> None:
    first = moment_paths(tmp_path, 2)
    first["video"].write_bytes(b"x")
    assert moment_paths(tmp_path, 2)["video"].name == "moment-part02-v2.mp4"


@needs_ffmpeg
def test_a_moment_sends_every_take_with_its_episode_and_keeps_its_own_files_and_row(
    reel_desk: Path,  # noqa: F811
) -> None:
    server = FakeReelServer()
    out = io.StringIO()

    made = make_moment(
        reel_desk,
        episodes=[1],
        part=3,
        next_text="Follow for Part 4",
        stream=out,
        server=server,
    )

    body = server.requests[-1]
    assert body["mode"] == "moment" and body["moment"] == {
        "part": 3,
        "next_text": "Follow for Part 4",
    }
    assert "seconds" not in body and "plan" not in body
    assert all(
        t["episode"] == 1 and t["take_id"].startswith("t")
        for t in body["operator"]["takes"]
    )
    assert body["operator"]["part"] == 3
    folder = reel_desk / "reels" / "moments"
    assert made.video == folder / "moment-part03-v1.mp4" and made.video.is_file()
    assert made.post is not None and made.post.is_file()
    assert json.loads(made.plan_path.read_text(encoding="utf-8"))["episodes"] == [1]
    rows = list(
        csv.DictReader((reel_desk / "reels" / "metrics.csv").open(encoding="utf-8"))
    )
    assert [r["recipe"] for r in rows] == [MOMENT_RECIPE] and rows[0]["part"] == "3"
    # The episode's own reel books are untouched.
    assert not (reel_desk / "reels" / "ep01" / "latest.json").exists()


def test_the_kit_offers_a_moment_after_episode_3_or_4_and_never_makes_one(
    tmp_path: Path,
) -> None:
    lengths = [(1, 25.0), (2, 25.0), (3, 25.0), (4, 25.0)]
    # Founder, 13 Oct 2026: ask only at episode 3 or 4.
    for episode in (1, 2, 5):
        assert moment_offer(tmp_path, episode, lengths=lengths) is None
    offer = moment_offer(tmp_path, 3, lengths=lengths)
    assert offer is not None and offer.parts == [[1, 2, 3]]
    assert offer.commands == [
        f'moment --desk {tmp_path.resolve()} --episodes 1-3 --part 1 --next "Follow for Part 2"'
    ]
    # Episodes 1 to 4, a short leftover joins the part before it.
    assert moment_offer(tmp_path, 4, lengths=lengths).parts == [[1, 2, 3, 4]]  # type: ignore[union-attr]
    # An unfinished earlier episode: the run starts after the gap.
    assert moment_offer(
        tmp_path, 4, lengths=[(2, 30.0), (3, 30.0), (4, 30.0)]
    ).parts == [[2, 3, 4]]  # type: ignore[union-attr]
    # Not when the just-finished episode or its neighbour is missing.
    assert moment_offer(tmp_path, 3, lengths=[(1, 30.0), (2, 30.0)]) is None
    assert moment_offer(tmp_path, 4, lengths=[(1, 30.0), (2, 30.0), (4, 30.0)]) is None
    # A show that already has a moment is not asked again.
    folder = tmp_path / "reels" / "moments"
    folder.mkdir(parents=True)
    (folder / "moment-part01-v1.mp4").write_bytes(b"x")
    assert moment_offer(tmp_path, 4, lengths=lengths) is None


def test_the_offer_prints_a_question_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import creation.post.moment_via_server as mvs

    monkeypatch.setattr(
        mvs,
        "finished_episode_lengths",
        lambda desk, stream=None: [(1, 30.0), (2, 30.0), (3, 30.0)],
    )
    monkeypatch.setattr(mvs, "_letterbox", lambda desk, episode: False)
    out = io.StringIO()
    assert offer_moment_after_finish(tmp_path, 3, stream=out) is not None
    text = out.getvalue()
    assert (
        "MOMENT OFFER" in text
        and "Part 1 = episodes 1–3" in text
        and "on yes: moment" in text
    )
    assert not (tmp_path / "reels" / "moments").exists()


@needs_ffmpeg
def test_a_moment_that_ran_out_of_time_keeps_its_uploads_and_is_never_sent_again(
    reel_desk: Path,  # noqa: F811
) -> None:
    """As the reel (#266): a timed-out cut is not a reason to forget the uploads and send it all again."""

    from creation.post.reel_server import ReelServerTimedOut, deadline_message

    class _OutOfTime(FakeReelServer):
        def __init__(self) -> None:
            super().__init__()
            self.reused = True  # the takes came from uploads.json
            self.makes = 0
            self.forgot = 0

        def make(
            self, job_id: str, episode_id: str, body: dict[str, object]
        ) -> dict[str, object]:
            self.makes += 1
            raise ReelServerTimedOut(deadline_message("the server cut the reel", 600.0))

        def forget_uploads(self) -> None:
            self.forgot += 1

    server = _OutOfTime()
    with pytest.raises(ReelServerTimedOut):
        make_moment(
            reel_desk, episodes=[1], part=3, stream=io.StringIO(), server=server
        )

    assert server.makes == 1, "the cut is sent once"
    assert server.forgot == 0, "uploads.json is kept"
