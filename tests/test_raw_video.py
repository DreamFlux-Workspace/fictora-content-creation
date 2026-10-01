"""Tests for raw scene clip URL extraction."""

from __future__ import annotations

from creation.harness.raw_video import clip_url_from_job_payload


def test_clip_url_from_job_payload_reads_video_url() -> None:
    payload = {
        "result": {
            "video": {"url": "https://example.com/clip.mp4"},
        }
    }
    assert clip_url_from_job_payload(payload) == "https://example.com/clip.mp4"


def test_clip_url_from_job_payload_reads_fictora_media() -> None:
    payload = {
        "result": {
            "_fictora_media": {"public_url": "https://example.com/media.mp4"},
        }
    }
    assert clip_url_from_job_payload(payload) == "https://example.com/media.mp4"


# --- a take counts only when its video job is complete (L-20260925-1) -------------------------------

import pytest  # noqa: E402

from creation.harness import raw_video  # noqa: E402


class _Run:
    """Answers ``GET /v1/jobs/{id}`` from a list of parent answers (one per poll) and fixed children."""

    def __init__(self, parents: list[dict], children: dict[str, dict]) -> None:
        self.parents = parents
        self.children = children
        self.saved: dict[str, dict] = {}
        self.polls = 0

    def get(self, path: str) -> dict:
        job = path.rsplit("/", 1)[-1]
        if job == "job_video_1":
            answer = self.parents[min(self.polls, len(self.parents) - 1)]
            self.polls += 1
            return answer
        return self.children[job]

    def get_optional(self, _path: str) -> tuple[int, dict]:
        return 404, {"error": {"code": "not_found"}}

    def save(self, name: str, payload: dict) -> None:
        self.saved[name] = payload

    def emit(self, *_a, **_k) -> None:
        return None


def _child(index: int) -> dict:
    return {
        "status": "completed",
        "episode_ids": ["ep_01"],
        "relation": {"id": f"scene_ep_01_set0{index}"},
        "result": {"video": {"url": f"https://r2.example/t{index}.mp4"}},
    }


HALF = {"status": "running", "progress": 50, "depends_on": ["job_t1"]}
BOTH_RUNNING = {"status": "running", "progress": 75, "depends_on": ["job_t1", "job_t2"]}
DONE = {"status": "completed", "progress": 100, "depends_on": ["job_t1", "job_t2"]}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raw_video.time, "sleep", lambda _s: None)


def test_a_half_done_job_with_its_first_clip_listed_is_not_a_finished_take() -> None:
    run = _Run(
        [HALF, HALF, BOTH_RUNNING, DONE], {"job_t1": _child(1), "job_t2": _child(2)}
    )

    raw = raw_video.wait_for_raw_scene_clips(
        run, "job_video_1", interval_seconds=0, expected_clips=2
    )

    assert [clip["set_index"] for clip in raw["clips"]] == [1, 2]
    assert raw["coordinator_status"] == "completed"
    assert run.polls == 4, "it waited for the job to complete, not just the listed clip"


def test_a_job_that_never_completes_is_not_done_at_the_deadline() -> None:
    run = _Run([HALF], {"job_t1": _child(1)})

    with pytest.raises(SystemExit, match="NOT done"):
        raw_video.wait_for_raw_scene_clips(
            run, "job_video_1", deadline_seconds=0.05, interval_seconds=0
        )
    assert run.saved == {}, "no clip record is written for half a take"


def test_a_completed_job_with_fewer_clips_than_asked_stops_loud() -> None:
    short = {"status": "completed", "progress": 100, "depends_on": ["job_t1"]}
    run = _Run([short], {"job_t1": _child(1)})

    with pytest.raises(SystemExit, match="asked for 2: the take is short"):
        raw_video.wait_for_raw_scene_clips(
            run, "job_video_1", interval_seconds=0, expected_clips=2
        )
    assert run.saved == {}


# --- the film poll reads /v1/video-generations too, and says when the job is stuck ----------------------


class _RunWithGeneration(_Run):
    """Also answers ``GET /v1/video-generations/{id}`` (``None``: the route 404s, an older server)."""

    def __init__(self, parents, children, generation=None) -> None:
        super().__init__(parents, children)
        self.generation = generation
        self.generation_reads = 0

    def get_optional(self, path: str) -> tuple[int, dict]:
        assert path == "/v1/video-generations/job_video_1", path
        self.generation_reads += 1
        if self.generation is None:
            return 404, {"error": {"code": "not_found"}}
        return 200, self.generation


STUCK_AT_ZERO = {
    "status": "running",
    "progress": 0,
    "updated_at": "2026-10-01T09:00:00Z",
}
GENERATION_FAILED = {
    "job_id": "job_video_1",
    "status": "failed",
    "progress": 0,
    "error": {"code": "provider_rejected", "message": "the provider refused the take"},
}


def test_a_failed_video_generation_stops_the_poll_while_the_job_still_says_running() -> (
    None
):
    run = _RunWithGeneration([STUCK_AT_ZERO], {}, generation=GENERATION_FAILED)

    with pytest.raises(raw_video.VideoJobFailed) as stopped:
        raw_video.wait_for_raw_scene_clips(
            run, "job_video_1", deadline_seconds=1, interval_seconds=0
        )

    text = str(stopped.value.code)
    assert "provider_rejected" in text and "the provider refused the take" in text
    assert "/v1/video-generations/job_video_1" in text
    assert run.polls == 1, "stopped on the first poll, not after the deadline"
    assert run.saved["17_video_terminal.json"]["status"] == "failed"


def test_a_cancelled_video_generation_stops_the_poll_too() -> None:
    run = _RunWithGeneration(
        [STUCK_AT_ZERO], {}, generation={"job": {"status": "cancelled"}}
    )

    with pytest.raises(raw_video.VideoJobFailed, match="cancelled"):
        raw_video.wait_for_raw_scene_clips(
            run, "job_video_1", deadline_seconds=1, interval_seconds=0
        )


def test_a_video_generation_route_that_does_not_answer_is_not_a_failure() -> None:
    run = _RunWithGeneration(
        [HALF, DONE], {"job_t1": _child(1), "job_t2": _child(2)}, generation=None
    )

    raw = raw_video.wait_for_raw_scene_clips(
        run, "job_video_1", interval_seconds=0, expected_clips=2
    )

    assert raw["coordinator_status"] == "completed"
    assert run.generation_reads == 2


class _Clock:
    """``time.monotonic`` that moves ``step`` seconds per sleep."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def monotonic(self) -> float:
        return self.now

    def sleep(self, _seconds: float) -> None:
        self.now += self.step


def test_a_film_whose_progress_never_moves_gets_a_stuck_warning_with_its_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock(step=300.0)
    monkeypatch.setattr(raw_video.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(raw_video.time, "sleep", clock.sleep)
    run = _RunWithGeneration([STUCK_AT_ZERO], {}, generation={"status": "running"})
    warnings: list[str] = []

    with pytest.raises(SystemExit, match="NOT done"):
        raw_video.wait_for_raw_scene_clips(
            run,
            "job_video_1",
            deadline_seconds=1300,
            interval_seconds=0,
            warn=warnings.append,
        )

    assert len(warnings) == 2, warnings  # at 10 and 20 minutes without a change
    first = warnings[0]
    assert "job_video_1" in first and "10 min" in first and "09:00 UTC" in first
    assert "cancel-job" in first and "--confirm-spend again" in first


def test_a_film_that_keeps_moving_is_never_called_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock(step=300.0)
    monkeypatch.setattr(raw_video.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(raw_video.time, "sleep", clock.sleep)
    moving = [{"status": "running", "progress": p} for p in range(0, 100, 10)]
    run = _RunWithGeneration(moving, {}, generation={"status": "running"})
    warnings: list[str] = []

    with pytest.raises(SystemExit, match="NOT done"):
        raw_video.wait_for_raw_scene_clips(
            run,
            "job_video_1",
            deadline_seconds=2900,
            interval_seconds=0,
            warn=warnings.append,
        )

    assert warnings == []
