"""An episode's TikTok clips: 2-3 short clips cut by the server's reel engine ($0), new desks only.

Clip mode of the reel route (``fictora-drama`` ``docs/reels/reel-rules.md``,
"Clips"): the same engine, rules and uploads as the desk's reel
(:mod:`creation.post.reel_via_server`), asked for ``mode: clips``. Each clip
is a straight 10-24 s window of the episode that opens in motion and ends on a
peak, never overlapping the others (3 for an episode of 30 s or more, else 2),
with its own cover ("clip k" under PART N) and post text.

After a complete ``finish`` (and an edit that wrote a new deliverable), right
after the reel, the clips are made by themselves (``--no-clips`` skips them);
``fictora-produce reel --clips N`` makes them again by hand. A desk made before
6 Oct 2026 (``rules_epoch``) gets no clips at all. A clip that fails never
fails the finish: the line says so and how to make them later.

Files, in ``reels/epNN/clips/`` (one ``vN`` for the whole set, never reused)::

    clip-epNN-k-vN.mp4         the clip
    clip-epNN-k-vN-cover.jpg   its cover (TikTok's cover picker)
    post-clip-epNN-k-vN.txt    the caption, then the operator's notes under the divider
    latest.json                the current set and what it was cut from

and one row per clip in ``reels/metrics.csv`` (``kind`` ``clip``, ``clip`` k).
Clips go on TikTok clip accounts, not Instagram (Instagram demotes accounts
that repost the same footage); the notes say so.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from creation.post.reel import (
    AUTO_REEL_FAILED,
    REELS_DIR,
    _posting,
    episode_reels,
    expected_takes,
    sources_fingerprint,
    take_sources,
    write_new,
)
from creation.post.reel_cover import METRICS_FILE, record_metrics_row
from creation.post.reel_plan import OPERATOR_DIVIDER
from creation.post.reel_server import (
    ReelEngineUnavailable,
    ReelServer,
    ReelServerError,
    ReelServerUnreachable,
    fallback_reason,
)

#: The episode's clip folder under its reel folder, and its record of the current set.
CLIPS_DIR = "clips"
CLIPS_LATEST_FILE = "latest.json"
#: At most this many clips per episode; each this long.
MAX_CLIPS = 3
CLIP_SECONDS = (10.0, 24.0)
#: Where clips are posted, and why not Instagram (written in every clip's notes).
TIKTOK_NOTE = "Post on a TikTok clip account, not Instagram: Instagram demotes accounts that repost the same footage."


@dataclass(frozen=True)
class ClipsAsk:
    """What to ask the server for: how many clips (1-3) and how long (10-24 s); ``None`` is the server's default."""

    count: int | None = None
    seconds: float | None = None

    def __post_init__(self) -> None:
        if self.count is not None and not 1 <= self.count <= MAX_CLIPS:
            raise ValueError(f"--clips {self.count}: 1-{MAX_CLIPS}")
        if (
            self.seconds is not None
            and not CLIP_SECONDS[0] <= self.seconds <= CLIP_SECONDS[1]
        ):
            raise ValueError(
                f"--clip-seconds {self.seconds:g}: a clip is {CLIP_SECONDS[0]:g}-{CLIP_SECONDS[1]:g} s"
            )

    def request_fields(self) -> dict[str, Any]:
        """The reel request's clip fields (``mode: clips`` and any count or length asked for)."""

        body: dict[str, Any] = {"mode": "clips"}
        if self.count is not None:
            body["clip_count"] = self.count
        if self.seconds is not None:
            body["clip_seconds"] = self.seconds
        return body


@dataclass
class ClipFile:
    """One saved clip: its files and its window of the episode."""

    index: int
    video: Path
    cover: Path | None
    post: Path
    start_ms: int
    end_ms: int
    why: str
    duration_ms: int = 0


@dataclass
class ClipsResult:
    """What a clip request saved (empty ``clips`` when the server made none)."""

    episode: int
    version: int
    clips: list[ClipFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    series: str = ""
    sources: dict[str, Any] = field(default_factory=dict)
    draft: bool = False
    server: dict[str, Any] = field(default_factory=dict)
    metrics: Path | None = None

    def summary(self) -> str:
        """One line: how many clips, how long, where."""

        if not self.clips:
            return f"Clips ep{self.episode:02d}: none made"
        lengths = ", ".join(
            f"{(c.end_ms - c.start_ms) / 1000:.1f} s" for c in self.clips
        )
        return (
            f"Clips ep{self.episode:02d} v{self.version}: {len(self.clips)} ({lengths}) in "
            f"{self.clips[0].video.parent} ($0)"
        )


def episode_clips(desk: Path, episode: int) -> Path:
    """The episode's clip folder, ``<desk>/reels/epNN/clips/``."""

    return episode_reels(desk, episode) / CLIPS_DIR


def next_clip_version(desk: Path, episode: int) -> int:
    """The next free ``vN`` for an episode's clip set (one N for every clip of the set, never reused)."""

    folder = episode_clips(desk, episode)
    pattern = re.compile(rf"^(?:post-)?clip-ep{episode:02d}-\d+-v(\d+)")
    used = [
        int(m.group(1))
        for p in (folder.iterdir() if folder.is_dir() else ())
        if (m := pattern.match(p.name))
    ]
    return max(used, default=0) + 1


def clip_paths(desk: Path, episode: int, index: int, version: int) -> dict[str, Path]:
    """Clip ``index``'s three files in set ``version`` (the folder is made when missing)."""

    folder = episode_clips(desk, episode)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"clip-ep{episode:02d}-{index}-v{version}"
    return {
        "video": folder / f"{stem}.mp4",
        "cover": folder / f"{stem}-cover.jpg",
        "post": folder / f"post-{stem}.txt",
    }


def clip_operator_notes(
    *,
    cover: str | None,
    account: str | None = None,
    lane: str | None = None,
    posting_slot: str | None = None,
) -> list[str]:
    """What the operator does in TikTok for one clip (never part of the caption).

    Parameters
    ----------
    cover
        The clip's cover file name (``None``: none was made).
    account, lane, posting_slot
        The desk's posting fields, when set.

    Returns
    -------
    list[str]
        Plain lines: the account line first when any posting field is set, then where to post and the cover.
    """

    lines: list[str] = []
    posting = [
        f"Account: {account}" if account else "",
        f"Lane: {lane}" if lane else "",
        f"Post at: {posting_slot}" if posting_slot else "",
    ]
    if any(posting):
        lines.append(" · ".join(p for p in posting if p))
    lines.append(TIKTOK_NOTE)
    lines.append("To do in TikTok:")
    if cover:
        lines.append(f"- Cover: tap Edit cover, then Upload, and pick {cover}.")
    else:
        lines.append("- Cover: no cover image was made; pick a frame with Edit cover.")
    lines.append("- Sound: keep the clip's own sound; the voices carry it.")
    return lines


def save_clips(
    desk: Path,
    episode: int,
    answer: Mapping[str, Any],
    client: Any,
    *,
    series: str,
    warnings: Sequence[str] = (),
    stream: TextIO | None = None,
) -> ClipsResult:
    """Download every clip and its cover the server made and write its post text. Writes only new files.

    Parameters
    ----------
    desk, episode
        The desk and episode.
    answer
        The reel route's answer in clip mode (``kind: clips``, ``clips`` listed).
    client
        The server client (downloads).
    series
        The series title (books).
    warnings
        The desk's own warnings about the inputs, joined with the server's.
    stream
        Progress output (stdout by default).

    Returns
    -------
    ClipsResult
        The saved clips, in episode order.

    Raises
    ------
    ReelServerError
        The server answered with no clips (an older server that ignores ``mode``).
    """

    out = stream or sys.stdout
    clips = [c for c in answer.get("clips") or [] if isinstance(c, Mapping)]
    if answer.get("kind") != "clips" or not clips:
        raise ReelServerError(
            "the server made no clips (it does not have clip mode yet); make them later"
        )
    version = next_clip_version(desk, episode)
    posting, _notes = _posting(desk)
    result = ClipsResult(
        episode=episode,
        version=version,
        series=series,
        warnings=list(dict.fromkeys([*warnings, *(answer.get("warnings") or [])])),
        server=dict(answer),
    )
    for item in sorted(clips, key=lambda c: int(c.get("index") or 0)):
        index = int(item.get("index") or len(result.clips) + 1)
        paths = clip_paths(desk, episode, index, version)
        client.download(str(item["video_url"]), paths["video"])
        cover: Path | None = None
        if item.get("cover_url"):
            cover = client.download(str(item["cover_url"]), paths["cover"])
        notes = clip_operator_notes(cover=cover.name if cover else None, **posting)
        caption = str(item.get("caption_text") or "").rstrip("\n") + "\n"
        write_new(
            paths["post"], caption + OPERATOR_DIVIDER + "\n" + "\n".join(notes) + "\n"
        )
        result.clips.append(
            ClipFile(
                index=index,
                video=paths["video"],
                cover=cover,
                post=paths["post"],
                start_ms=int(item.get("start_ms") or 0),
                end_ms=int(item.get("end_ms") or 0),
                why=str(item.get("why") or ""),
                duration_ms=int(item.get("duration_ms") or 0),
            )
        )
    for warning in result.warnings:
        print(f"⚠ {warning}", file=out)
    return result


def record_clips(
    desk: Path,
    episode: int,
    result: ClipsResult,
    *,
    made_by: str,
    stream: TextIO | None = None,
) -> ClipsResult:
    """Keep the books on a clip set: ``clips/latest.json`` and one ``metrics.csv`` row per clip (``kind`` clip)."""

    out = stream or sys.stdout
    if not result.clips:
        return result
    from creation.post.reel_cover import series_part

    posting, _ = _posting(desk)
    part = series_part(desk, episode).number
    folder = episode_clips(desk, episode)
    latest = {
        "episode": episode,
        "version": result.version,
        "clips": [
            {
                "index": c.index,
                "video": c.video.relative_to(desk).as_posix(),
                "cover": c.cover.relative_to(desk).as_posix() if c.cover else None,
                "post": c.post.relative_to(desk).as_posix(),
                "start_ms": c.start_ms,
                "end_ms": c.end_ms,
                "why": c.why,
            }
            for c in result.clips
        ],
        "draft": result.draft,
        "made_by": made_by,
        "made_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": result.sources,
    }
    scratch = folder / (CLIPS_LATEST_FILE + ".tmp")
    scratch.write_text(
        json.dumps(latest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    scratch.replace(folder / CLIPS_LATEST_FILE)
    for clip in result.clips:
        result.metrics = record_metrics_row(
            desk / REELS_DIR / METRICS_FILE,
            {
                "reel_file": clip.video.name, "cover_file": clip.cover.name if clip.cover else "",
                "series": result.series, "part": part, "kind": "clip", "clip": clip.index,
                "account": posting["account"], "lane": posting["lane"],
                "planned_post_slot": posting["posting_slot"],
            },
        )  # fmt: skip
    for clip in result.clips:
        print(f"clip {clip.index}: {clip.video}  ({clip.why})", file=out)
        if clip.cover is not None:
            print(f"  cover: {clip.cover}", file=out)
        print(f"  post: {clip.post}", file=out)
    print(f"metrics: {result.metrics} (one row per clip, kind clip)", file=out)
    print(TIKTOK_NOTE, file=out)
    print(result.summary(), file=out)
    return result


def read_clips_latest(desk: Path, episode: int) -> dict[str, Any]:
    """The episode's ``clips/latest.json`` (empty when there is none or it cannot be read)."""

    path = episode_clips(desk, episode) / CLIPS_LATEST_FILE
    try:
        body = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except json.JSONDecodeError as exc:
        print(
            f"⚠ {path} could not be read ({exc}); it is written again with these clips",
            file=sys.stderr,
        )
        return {}
    return body if isinstance(body, dict) else {}


def clips_unreachable_message(desk: Path, episode: int) -> str:
    """What to do when the server did not answer: make the clips later."""

    return (
        "Clips not made: the server didn't answer. Re-run "
        f"`fictora-produce reel --desk {desk} --episode {episode} --clips 3` later."
    )


def clips_need_server_message(desk: Path, episode: int, exc: ReelServerError) -> str:
    """The clips can't be made now: only the server cuts them (no local clips engine); how to make them later."""

    return (
        f"Clips not made: the TikTok clips are cut only by the server's reel engine (no local clips engine), "
        f"and it can't be used now ({fallback_reason(exc)}). Make them once it takes uploads: "
        f"`fictora-produce reel --desk {desk} --episode {episode} --clips 3`."
    )


def run_clips(
    desk: Path,
    *,
    episode: int,
    count: int | None = None,
    seconds: float | None = None,
    stream: TextIO | None = None,
    made_by: str = "reel",
    server: ReelServer | None = None,
    take_files: Sequence[str] = (),
    sources: Sequence[str] = (),
    captions: Sequence[str] = (),
    caption_style: str | None = None,
) -> ClipsResult:
    """Make the episode's clips on the server and keep the books. Writes only under ``<desk>/reels/``.

    Parameters
    ----------
    desk
        Series desk (made on or after 6 Oct 2026).
    episode
        Episode ordinal.
    count, seconds
        ``--clips N`` (1-3) and ``--clip-seconds S`` (10-24); ``None`` is the server's default.
    stream
        Progress output (stdout by default).
    made_by
        ``reel`` (by hand), ``finish`` or an edit's name, for ``clips/latest.json``.
    server
        The server client (tests pass a stand-in); default the desk's.
    take_files, sources, captions, caption_style
        ``reel``'s ``--take-file`` / ``--source`` / ``--captions`` / ``--caption-style``: the clips are cut
        from the same takes and captions the reel would be (canary 7 Oct 2026: ``--clips`` ignored them).

    Returns
    -------
    ClipsResult
        The saved clips.

    Raises
    ------
    ValueError
        A desk made before 6 Oct 2026 (no clips), a count or length out of range, or no finished take.
    ReelServerUnreachable, ReelServerError
        As the reel.
    """

    from creation.post.reel_via_server import legacy_desk, make_reel

    ask = ClipsAsk(count, seconds)
    desk = desk.expanduser().resolve()
    if legacy_desk(desk):
        raise ValueError(
            "clips are made for desks created on or after 6 Oct 2026; this desk is older (no clips)"
        )
    with tempfile.TemporaryDirectory(prefix="fictora-clips-") as tmp:
        made = make_reel(
            desk, episode=episode, seconds=15.0, plan_only=False, plan_file=None, take_files=tuple(take_files),
            sources=tuple(sources), captions=tuple(captions), caption_style=caption_style, ending=None,
            stream=stream, scratch=Path(tmp), server=server, clips=ask,
        )  # fmt: skip
    assert isinstance(made, ClipsResult)
    return record_clips(desk, episode, made, made_by=made_by, stream=stream)


def auto_clips(
    desk: Path,
    episode: int,
    *,
    trigger: str,
    stream: TextIO | None = None,
    force: bool = False,
    server: ReelServer | None = None,
) -> ClipsResult | None:
    """The clips made by themselves after a complete ``finish`` or an edit that wrote a new deliverable.

    Nothing for a desk made before 6 Oct 2026 (silently: its finish is frozen),
    nothing while takes are still to finish (clips of half an episode), nothing
    when the finished takes are the ones the current set was cut from. A
    failure is printed (the finish is done), never raised.

    Returns
    -------
    ClipsResult | None
        The clips, or ``None`` when none were made.
    """

    from creation.post.desk import saved_spine
    from creation.post.reel_via_server import legacy_desk

    out = stream or sys.stdout
    desk = desk.expanduser().resolve()
    if legacy_desk(desk):
        return None
    found = saved_spine(desk, episode)
    if found is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="fictora-clips-check-") as tmp:
            srcs = take_sources(
                desk, episode, spine=found[0], scratch=Path(tmp), stream=out
            )
            fingerprint = sources_fingerprint(desk, srcs)
        expected = expected_takes(desk, found[0], episode)
        if expected is not None and len(srcs) < expected:
            print(
                f"Clips: not yet ({len(srcs)} of {expected} take(s) finished); they are cut when the episode is",
                file=out,
            )
            return None
        latest = read_clips_latest(desk, episode)
        if not force and latest.get("sources") == fingerprint and latest.get("clips"):
            print(
                f"Clips: unchanged since v{latest.get('version')} (the same finished takes); not cut again",
                file=out,
            )
            return None
        print(
            f"Clips for TikTok (after {trigger}; $0, the server's reel engine; --no-clips skips them):",
            file=out,
        )
        return run_clips(
            desk, episode=episode, stream=out, made_by=trigger, server=server
        )
    except ReelServerUnreachable as exc:
        print(
            f"⚠ {clips_unreachable_message(desk, episode)} ({exc}; the {trigger} is done)",
            file=out,
        )
        return None
    except ReelEngineUnavailable as exc:
        print(
            f"⚠ {clips_need_server_message(desk, episode, exc)} The {trigger} is done.",
            file=out,
        )
        return None
    except AUTO_REEL_FAILED as exc:
        print(
            f"⚠ Clips not made ({type(exc).__name__}: {exc}); the {trigger} is done. "
            f"Try again with `reel --desk D --episode {episode} --clips 3`",
            file=out,
        )
        return None


__all__ = [
    "CLIPS_DIR",
    "TIKTOK_NOTE",
    "ClipFile",
    "ClipsAsk",
    "ClipsResult",
    "auto_clips",
    "clip_operator_notes",
    "clip_paths",
    "clips_need_server_message",
    "clips_unreachable_message",
    "episode_clips",
    "next_clip_version",
    "read_clips_latest",
    "record_clips",
    "run_clips",
    "save_clips",
]
