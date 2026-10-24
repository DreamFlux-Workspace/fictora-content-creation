"""A thought's place on each take and its black and white, from the server (24 Oct 2026).

Server-heavy (founder, 15 Oct 2026): the kit holds no rule for where a thought
plays or how it looks. ``finish`` and ``join`` send each take's real length
(ffprobe on the desk's newest raw take), its render job and, for the grey,
the shot cuts measured on it, to the server's free thoughts route
(``POST /v1/spines/{spine_id}/episodes/{ordinal}/thoughts``, fictora-drama
``thought_windows``). The server answers with every thought on every take,
placed the way its own join places them, and the exact ffmpeg filter it greys
them with. The kit only measures its files and applies what comes back.

An older server with no such route (HTTP 404), no credentials or no network:
no grey, and a printed note; never an error.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from creation.post.desk import latest_raw_take, open_api, spine_id, take_job_id
from creation.post.media import probe_video, run_ffmpeg

#: The server's thoughts route (fictora-drama ``thought_windows``; drama PR "thoughts route").
THOUGHTS_PATH = "/v1/spines/{spine_id}/episodes/{ordinal}/thoughts"

#: One POST of the route's body; returns ``(HTTP status, parsed JSON)``.
ThoughtsPost = Callable[[dict[str, Any]], tuple[int, Any]]
#: Builds the poster for a desk's episode (tests pass a stand-in for the server).
ThoughtsFetcher = Callable[[Path, int], ThoughtsPost]


class ThoughtsUnavailable(RuntimeError):
    """The server did not place the thoughts; the message is the plain note to print."""


@dataclass(frozen=True)
class ThoughtWindow:
    """One thought heard on one take, as the server placed it (seconds on the take)."""

    source: str
    id: str
    speaker_cast_id: str
    text: str
    start: float
    end: float
    grey: bool
    spoken_text: str = ""
    acting_direction: str = ""
    episode_start: float | None = None
    episode_end: float | None = None
    runs_past_take_end: bool = False


@dataclass(frozen=True)
class TakeThoughts:
    """One take's thoughts, the spans the server greys and its filter for them."""

    take: int
    seconds: float
    windows: tuple[ThoughtWindow, ...] = ()
    grey_spans: tuple[tuple[float, float], ...] = ()
    grey_filter: str | None = None


@dataclass(frozen=True)
class EpisodeThoughts:
    """The server's answer for the takes sent."""

    takes: tuple[TakeThoughts, ...]
    #: ``(cue_id, episode_start_s, reason)`` of each cue no take sent holds.
    unplaced: tuple[tuple[str, float, str], ...] = ()
    grey_on: bool = False

    def take(self, number: int) -> TakeThoughts | None:
        """Take ``number``'s entry (``t1`` is 1), or ``None`` when it was not sent."""

        return next((item for item in self.takes if item.take == number), None)


def episode_has_thought_lines(spine: Mapping[str, Any], episode: int) -> bool:
    """Whether the server lists a thought line in this episode (``thought_line_ids`` on the spine).

    Parameters
    ----------
    spine
        The saved spine.
    episode
        Episode ordinal.

    Returns
    -------
    bool
        ``True`` when one of the episode's lines is a thought.
    """

    from creation.spine_view import episode_id_for

    ids = set(spine.get("thought_line_ids") or ())
    if not ids:
        return False
    episode_id = episode_id_for(spine, episode)
    return any(
        line.get("line_id") in ids
        for beat in spine.get("beats") or []
        if isinstance(beat, Mapping) and beat.get("episode_id") == episode_id
        for line in beat.get("dialogue_lines") or []
        if isinstance(line, Mapping)
    )


def episode_has_thoughts(spine: Mapping[str, Any] | None, episode: int) -> bool:
    """Whether the episode has a thought for the server to place: an inner-voice cue or a thought line.

    Parameters
    ----------
    spine
        The saved spine (``None``: none on the desk).
    episode
        Episode ordinal.

    Returns
    -------
    bool
        ``False`` asks the server nothing.
    """

    from creation.inner_voice import episode_cues

    if not spine:
        return False
    return bool(episode_cues(spine, episode=episode)) or episode_has_thought_lines(
        spine, episode
    )


def api_thoughts_fetcher(desk: Path, episode: int) -> ThoughtsPost:
    """POST to the server's thoughts route for this desk's episode.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.

    Returns
    -------
    ThoughtsPost
        Posts one body and returns the status and answer.
    """

    def post(body: dict[str, Any]) -> tuple[int, Any]:
        run = open_api(desk, episode)
        try:
            path = THOUGHTS_PATH.format(
                spine_id=quote(spine_id(desk), safe=""), ordinal=episode
            )
            return run.post_optional(path, body)
        finally:
            run.client.close()

    return post


def desk_take_lengths(
    desk: Path, episode: int, upto: int, *, source: Path | None = None
) -> list[float | None]:
    """The real length of takes ``t1`` .. ``t{upto}`` (ffprobe on each newest raw take).

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    upto
        Last take number.
    source
        The last take's file when its raw file is gone.

    Returns
    -------
    list[float | None]
        Seconds per take; ``None`` for a take with no raw file on the desk.
    """

    lengths: list[float | None] = []
    for index in range(1, upto + 1):
        try:
            raw = latest_raw_take(desk, episode, f"t{index}")
        except FileNotFoundError:
            lengths.append(
                probe_video(source).duration_seconds
                if index == upto and source is not None
                else None
            )
            continue
        lengths.append(probe_video(raw).duration_seconds)
    return lengths


def request_body(
    desk: Path,
    episode: int,
    lengths: Sequence[float],
    *,
    cuts: Mapping[int, Sequence[float]] | None = None,
) -> dict[str, Any]:
    """The route's body: each take's length, its job when the desk knows it, and any cuts measured.

    Parameters
    ----------
    desk, episode
        The desk and episode.
    lengths
        Seconds of takes 1 .. N, all known.
    cuts
        Measured cuts by take number.

    Returns
    -------
    dict[str, Any]
        ``DramaEpisodeThoughtsRequest``.
    """

    takes: list[dict[str, Any]] = []
    for number, seconds in enumerate(lengths, start=1):
        item: dict[str, Any] = {"take_seconds": round(float(seconds), 3)}
        try:
            job = take_job_id(desk, episode, f"t{number}")
        except (OSError, ValueError, KeyError):
            job = None
        if job:
            item["job_id"] = job
        if cuts and cuts.get(number):
            item["cuts_s"] = [round(float(cut), 3) for cut in cuts[number]]
        takes.append(item)
    return {"takes": takes}


def parse_answer(answer: Mapping[str, Any]) -> EpisodeThoughts:
    """Read the server's ``DramaEpisodeThoughtsResponse``.

    Parameters
    ----------
    answer
        The parsed JSON.

    Returns
    -------
    EpisodeThoughts
        The takes, as sent back.
    """

    takes = []
    for take in answer.get("takes") or []:
        windows = tuple(
            ThoughtWindow(
                source=str(window.get("source") or ""),
                id=str(window.get("id") or ""),
                speaker_cast_id=str(window.get("speaker_cast_id") or ""),
                text=" ".join(str(window.get("text") or "").split()),
                start=float(window["start_s"]),
                end=float(window["end_s"]),
                grey=bool(window.get("grey")),
                spoken_text=" ".join(str(window.get("spoken_text") or "").split()),
                acting_direction=str(window.get("acting_direction") or ""),
                episode_start=window.get("episode_start_s"),
                episode_end=window.get("episode_end_s"),
                runs_past_take_end=bool(window.get("runs_past_take_end")),
            )
            for window in take.get("windows") or []
        )
        takes.append(
            TakeThoughts(
                take=int(take["take"]),
                seconds=float(take["take_seconds"]),
                windows=windows,
                grey_spans=tuple(
                    (float(a), float(b)) for a, b in take.get("grey_spans") or []
                ),
                grey_filter=take.get("grey_filter") or None,
            )
        )
    unplaced = tuple(
        (
            str(item.get("cue_id")),
            float(item.get("episode_start_s") or 0.0),
            str(item.get("reason") or ""),
        )
        for item in answer.get("unplaced") or []
    )
    return EpisodeThoughts(tuple(takes), unplaced, bool(answer.get("grey_on")))


def fetch_thoughts(post: ThoughtsPost, body: dict[str, Any]) -> EpisodeThoughts:
    """Ask the server where each thought plays.

    Parameters
    ----------
    post
        :func:`api_thoughts_fetcher` (or a stand-in).
    body
        :func:`request_body`.

    Returns
    -------
    EpisodeThoughts
        The server's answer.

    Raises
    ------
    ThoughtsUnavailable
        With the plain note to print: an older server (404), a refusal, no API.
    """

    try:
        status, answer = post(body)
    except (
        Exception
    ) as exc:  # no credentials, no network: the take goes on without the grey
        raise ThoughtsUnavailable(
            f"the server could not be asked ({str(exc)[:160]})"
        ) from exc
    if status == 404:
        raise ThoughtsUnavailable(
            "the server has no thoughts route yet (an older deploy), so the thought is not placed or greyed; "
            "finish again once the server is updated"
        )
    if not 200 <= status < 300 or not isinstance(answer, Mapping):
        raise ThoughtsUnavailable(
            f"the server did not place the thoughts (HTTP {status})"
        )
    try:
        return parse_answer(answer)
    except (KeyError, TypeError, ValueError) as exc:
        raise ThoughtsUnavailable(
            f"the server's answer could not be read ({exc})"
        ) from exc


def grey_take(take: Path, grey_filter: str, output: Path) -> Path:
    """Play the picture in black and white with the server's filter; the sound is copied untouched.

    Parameters
    ----------
    take
        The take on its filmed timeline (captions are burned later, so they stay in colour).
    grey_filter
        The server's ``grey_filter`` for this take, applied exactly as sent.
    output
        A new file.

    Returns
    -------
    Path
        ``output``.
    """

    run_ffmpeg(
        [
            "-threads", "2", "-i", str(take), "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", grey_filter,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", str(output),
        ]
    )  # fmt: skip
    return output


__all__ = [
    "THOUGHTS_PATH",
    "EpisodeThoughts",
    "TakeThoughts",
    "ThoughtWindow",
    "ThoughtsFetcher",
    "ThoughtsPost",
    "ThoughtsUnavailable",
    "api_thoughts_fetcher",
    "desk_take_lengths",
    "episode_has_thought_lines",
    "episode_has_thoughts",
    "fetch_thoughts",
    "grey_take",
    "parse_answer",
    "request_body",
]
