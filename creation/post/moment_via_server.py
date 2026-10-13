"""``moment``: several finished episodes joined into one TikTok part by the server's engine ($0).

Founder, 13 Oct 2026: TikTok clips were removed; a moment is the TikTok format instead. PART N
of a show: the episodes play whole and in order, cut as one story (the server's
``reel_moment``: dead air cut at scene edges, never a line, never inside a shot), with a title
bar, a hook line under it, one bed under the whole part and an end card ("Part N+1 →").

The kit sends each episode's pre-caption takes and caption cues, every take naming its
episode, to the reel route in mode ``moment`` and saves what comes back under
``reels/moments/``. ``--auto`` groups a show's finished episodes into parts of about a minute.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from creation.post.media import probe_video
from creation.post.reel import (
    REELS_DIR,
    TakeSource,
    _patched,
    _posting,
    take_sources,
    write_new,
)
from creation.post.reel_cover import METRICS_FILE, cover_path, record_metrics_row
from creation.post.reel_plan import OPERATOR_DIVIDER, post_operator_notes
from creation.post.reel_server import ReelServer, ReelServerUnreachable, reel_job_id
from creation.post.reel_via_server import (
    letterbox_finals,
    operator_body,
    reel_bed,
    take_cue_emphasis,
    take_timing,
)

MOMENTS_DIR = "moments"
#: The recipe column a moment's metrics row carries (apart from the episode reels of the same part number).
MOMENT_RECIPE = "moment"
#: ``--auto``: episodes join a part until their takes run this long (the cut lands near a minute).
PART_TAKE_SECONDS = 75.0
#: The server's limit on episodes in one moment.
MAX_EPISODES = 6


@dataclass
class MomentResult:
    """What one moment run wrote."""

    part: int
    episodes: list[int]
    video: Path | None = None
    cover: Path | None = None
    plan_path: Path | None = None
    post: Path | None = None
    seconds: float = 0.0
    lines: list[str] = field(default_factory=list)


def parse_episodes(text: str) -> list[int]:
    """``1-3`` or ``1,2,3`` as episode ordinals, ascending, no repeats.

    Raises
    ------
    ValueError
        Not a range or a comma list of positive numbers, or more than :data:`MAX_EPISODES`.
    """

    text = text.strip()
    found = re.fullmatch(r"(\d+)\s*-\s*(\d+)", text)
    if found:
        lo, hi = int(found.group(1)), int(found.group(2))
        numbers = list(range(lo, hi + 1))
    else:
        numbers = [int(x) for x in text.split(",") if x.strip().isdigit()]
        if len(numbers) != len([x for x in text.split(",") if x.strip()]):
            raise ValueError(
                f"--episodes {text!r}: a range (1-3) or a comma list (1,2,3)"
            )
    numbers = sorted(set(numbers))
    if not numbers or numbers[0] < 1:
        raise ValueError(f"--episodes {text!r}: episodes are numbered from 1")
    if len(numbers) > MAX_EPISODES:
        raise ValueError(
            f"--episodes {text!r}: a moment joins at most {MAX_EPISODES} episodes"
        )
    return numbers


def group_parts(lengths: Sequence[tuple[int, float]]) -> list[list[int]]:
    """``--auto``: consecutive episodes grouped into parts of about :data:`PART_TAKE_SECONDS` of takes.

    A short leftover at the end joins the part before it (when that stays within
    :data:`MAX_EPISODES`), so the last part is not a lone short episode.
    """

    parts: list[list[int]] = []
    current: list[int] = []
    run = 0.0
    for episode, seconds in lengths:
        current.append(episode)
        run += seconds
        if run >= PART_TAKE_SECONDS or len(current) == MAX_EPISODES:
            parts.append(current)
            current, run = [], 0.0
    if current:
        if (
            parts
            and run < PART_TAKE_SECONDS / 2
            and len(parts[-1]) + len(current) <= MAX_EPISODES
        ):
            parts[-1] += current
        else:
            parts.append(current)
    return parts


def moment_paths(desk: Path, part: int) -> dict[str, Path]:
    """The next free ``vN`` for a part's files under ``reels/moments/`` (never an existing file)."""

    folder = desk / REELS_DIR / MOMENTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"moment-part{part:02d}"
    used = {0} | {
        int(m.group(1))
        for p in folder.iterdir()
        if (
            m := re.match(
                rf"^(?:{stem}|moment-plan-part{part:02d}|moment-post-part{part:02d})-v(\d+)",
                p.name,
            )
        )
    }
    n = max(used) + 1
    return {
        "video": folder / f"{stem}-v{n}.mp4",
        "plan": folder / f"moment-plan-part{part:02d}-v{n}.json",
        "post": folder / f"moment-post-part{part:02d}-v{n}.txt",
    }


def make_moment(
    desk: Path,
    *,
    episodes: Sequence[int],
    part: int,
    next_text: str | None = None,
    hook_line: str | None = None,
    caption_style: str | None = None,
    stream: TextIO | None = None,
    server: ReelServer | None = None,
) -> MomentResult:
    """Send the episodes' takes to the server as one moment and save it under ``reels/moments/``.

    Parameters
    ----------
    desk
        Series desk (made on or after 6 Oct 2026: the server's engine).
    episodes
        The episodes to join, in order (at most :data:`MAX_EPISODES`).
    part
        PART N on its title bar, cover and post text.
    next_text
        The end card's words; ``None`` is the server's ``Part N+1 →``.
    hook_line
        The hook line under the title bar; ``None`` is the last episode's hook question.
    caption_style
        As ``reel``; default the show's.
    stream
        Progress output (stdout by default).
    server
        The server client (tests pass a stand-in); default the last episode's.

    Returns
    -------
    MomentResult
        The files written.

    Raises
    ------
    ValueError
        No saved spine, an episode with no finished take, a letterbox show, or takes of different sizes.
    ReelServerError
        The server refused or failed the moment.
    """

    from creation.captions import episode_caption_lines, resolve_caption_style
    from creation.post.desk import saved_spine
    from creation.spine_view import episode_id_for

    out = stream or sys.stdout
    desk = desk.expanduser().resolve()
    episodes = sorted(set(episodes))
    last = episodes[-1]
    found = saved_spine(desk, last)
    if found is None:
        raise ValueError(
            f"no saved spine with beats in ep{last:02d}/api/: the moment plans from its beats"
        )
    spine = found[0]
    style, style_note = resolve_caption_style(desk, caption_style)
    with tempfile.TemporaryDirectory(prefix="fictora-moment-") as tmp:
        scratch = Path(tmp)
        per_episode: list[tuple[int, list[TakeSource], list[Any]]] = []
        for ep in episodes:
            ep_spine = (saved_spine(desk, ep) or (spine,))[0]
            srcs = take_sources(desk, ep, spine=ep_spine, scratch=scratch, stream=out)
            if not srcs:
                raise ValueError(
                    f"episode {ep} has no finished take: finish it before its moment"
                )
            if letterbox_finals(desk, ep_spine, srcs, []) is not None:
                raise ValueError(
                    "a letterbox show's moment is not made yet: its takes sit in bands"
                )
            lines = episode_caption_lines(ep_spine, ep) if style == "bold" else []
            per_episode.append((ep, srcs, lines))
        everything = [s for _, srcs, _ in per_episode for s in srcs]
        if (
            len(
                {
                    (i.width, i.height)
                    for i in (probe_video(s.source) for s in everything)
                }
            )
            != 1
        ):
            raise ValueError("the takes differ in size; a moment needs one frame size")
        bed = reel_bed(desk, everything)
        client = server or ReelServer(desk, last)
        try:
            job_id = reel_job_id(desk, last, [s.take_id for s in per_episode[-1][1]])
            takes: list[dict[str, Any]] = []
            for ep, srcs, lines in per_episode:
                for s in srcs:
                    url, sha = client.upload(
                        _patched(s, scratch, list(s.patches)), kind="video"
                    )
                    shots, events = take_timing(desk, ep, s)
                    takes.append(
                        {
                            "take_id": f"t{len(takes) + 1}",
                            "episode": ep,
                            "video_url": url,
                            "sha256": sha,
                            "cues": [
                                {
                                    "start_s": round(c.start, 3),
                                    "end_s": round(c.end, 3),
                                    "text": c.text,
                                    "italic": c.italic,
                                    **(
                                        {"emphasis_word": w}
                                        if w and style == "bold"
                                        else {}
                                    ),
                                }  # fmt: skip
                                for c, w in take_cue_emphasis(s, lines)
                                if c.end > c.start and c.text.strip()
                            ],
                            "shots": shots,
                            "sound_events_s": events,
                        }
                    )
            bed_upload = (
                client.upload(bed.bed, kind="audio") if bed.bed is not None else None
            )
            request = operator_body(
                takes=takes, bed=bed, bed_upload=bed_upload, still_upload=None, caption_style=style, pov=False,
                seconds=15.0, ending=None, hook_line=hook_line, no_hook_line=False, hook_line_position=None,
                no_cover=False, cover_frame=None, plan=None, part=part,
            )  # fmt: skip
            request.pop("seconds", None)
            request["mode"] = "moment"
            request["moment"] = {
                "part": part,
                **({"next_text": next_text} if next_text else {}),
            }
            print(
                f"Moment PART {part} (episodes {', '.join(map(str, episodes))}; {len(takes)} takes; "
                "$0, the server's engine):",
                file=out,
                flush=True,
            )
            try:
                answer = client.make(job_id, episode_id_for(spine, last), request)
            except ReelServerUnreachable:
                if not client.reused:
                    raise
                client.forget_uploads()
                client.reused = False
                answer = client.make(job_id, episode_id_for(spine, last), request)
            paths = moment_paths(desk, part)
            plan = dict(answer.get("plan") or {})
            plan["episodes"] = episodes
            write_new(
                paths["plan"], json.dumps(plan, indent=2, ensure_ascii=False) + "\n"
            )
            client.download(str(answer["reel_video_url"]), paths["video"])
            cover = (
                client.download(str(answer["cover_url"]), cover_path(paths["video"]))
                if answer.get("cover_url")
                else None
            )
        finally:
            if server is None:
                client.close()
    posting, posting_notes = _posting(desk)
    todo = post_operator_notes(cover=cover.name if cover else None, **posting)
    caption = str(answer.get("caption_text") or "").rstrip("\n") + "\n"
    write_new(paths["post"], caption + OPERATOR_DIVIDER + "\n" + "\n".join(todo) + "\n")
    record_metrics_row(
        desk / REELS_DIR / METRICS_FILE,
        {
            "reel_file": f"{MOMENTS_DIR}/{paths['video'].name}", "cover_file": cover.name if cover else "",
            "series": str(spine.get("title") or desk.name), "part": part, "account": posting["account"],
            "lane": posting["lane"], "planned_post_slot": posting["posting_slot"], "recipe": MOMENT_RECIPE,
            "hook_text": hook_line or "",
        },
    )  # fmt: skip
    result = MomentResult(
        part=part, episodes=episodes, video=paths["video"], cover=cover, plan_path=paths["plan"], post=paths["post"],
        seconds=int(answer.get("duration_ms") or 0) / 1000.0,
        lines=[bed.note, *[str(x) for x in answer.get("report") or []], *([style_note] if style_note else [])],
    )  # fmt: skip
    for line in [*result.lines, *[f"⚠ {n}" for n in posting_notes]]:
        print(f"- {line}", file=out)
    print(f"moment: {result.video} ({result.seconds:.1f} s)", file=out)
    return result


def finished_episode_lengths(
    desk: Path, stream: TextIO | None = None
) -> list[tuple[int, float]]:
    """Each finished episode of the desk (every expected take finished) and its takes' total length."""

    from creation.post.desk import saved_spine
    from creation.post.reel import expected_takes

    out = stream or sys.stdout
    found: list[tuple[int, float]] = []
    for folder in sorted(desk.glob("ep[0-9][0-9]")):
        ep = int(folder.name[2:])
        saved = saved_spine(desk, ep)
        if saved is None:
            continue
        with tempfile.TemporaryDirectory(prefix="fictora-moment-check-") as tmp:
            try:
                srcs = take_sources(
                    desk, ep, spine=saved[0], scratch=Path(tmp), stream=out
                )
            except (ValueError, OSError):
                continue
            expected = expected_takes(desk, saved[0], ep)
            if not srcs or (expected is not None and len(srcs) < expected):
                continue
            found.append((ep, sum(probe_video(s.source).duration_s for s in srcs)))
    return found


def run_moments(
    desk: Path,
    *,
    episodes: Sequence[int] | None,
    part: int | None,
    next_text: str | None = None,
    hook_line: str | None = None,
    caption_style: str | None = None,
    stream: TextIO | None = None,
    server: ReelServer | None = None,
) -> list[MomentResult]:
    """``moment``: one part (``--episodes`` and ``--part``), or every part of the show (``--auto``).

    ``--auto`` groups the finished episodes (:func:`group_parts`) and makes PART 1, 2, ... in
    order; each card says ``Part N+1 →`` but the last says ``Follow for Part N+1``.
    """

    out = stream or sys.stdout
    desk = desk.expanduser().resolve()
    if episodes is not None:
        return [
            make_moment(
                desk,
                episodes=episodes,
                part=part or 1,
                next_text=next_text,
                hook_line=hook_line,
                caption_style=caption_style,
                stream=out,
                server=server,
            )  # fmt: skip
        ]
    parts = group_parts(finished_episode_lengths(desk, out))
    if not parts:
        raise ValueError("no finished episode on the desk to make a moment from")
    print(
        "Moments: "
        + "; ".join(
            f"PART {i} = episodes {', '.join(map(str, p))}"
            for i, p in enumerate(parts, 1)
        ),
        file=out,
    )
    made: list[MomentResult] = []
    for index, group in enumerate(parts, start=1):
        # The last part has no Part N+1 yet: its card asks for a follow instead.
        card = (
            next_text
            if index < len(parts)
            else (next_text or f"Follow for Part {index + 1}")
        )
        made.append(
            make_moment(
                desk,
                episodes=group,
                part=index,
                next_text=card,
                hook_line=hook_line,
                caption_style=caption_style,
                stream=out,
                server=server,
            )  # fmt: skip
        )
    return made


__all__ = [
    "MAX_EPISODES",
    "MOMENTS_DIR",
    "MOMENT_RECIPE",
    "MomentResult",
    "PART_TAKE_SECONDS",
    "group_parts",
    "make_moment",
    "moment_paths",
    "parse_episodes",
    "run_moments",
]
