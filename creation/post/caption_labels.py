"""Caption labels live on the server (decision D2, 21 Oct 2026).

A caption label is a caption with no spoken line under it: a word the take
never says clearly ("Blinking!"), shown as text only at a window on one take
as filmed. ``finish --caption-label "TEXT@A-B"`` used to burn the words
straight from the command line, so no rule ever read them. Now:

* ``finish --caption-label ...`` sends the take's labels to
  ``PUT /v1/spines/{spine_id}/episodes/{episode_id}/caption-labels``; the
  server draws each (no em or en dash), checks it with the line rules (ten
  words, never said to the viewer; its warnings print word for word) and keeps
  them on the episode (``episode_summaries[].caption_labels``).
* Every ``finish`` of the take burns the labels the server holds, so a
  re-finish keeps them; ``--no-caption-labels`` clears them on the server.
* An older server (no route) stops the finish before anything is built; the
  kit keeps no labels of its own.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

LABELS_ROUTE = "/v1/spines/{spine_id}/episodes/{episode_id}/caption-labels"
OLDER_SERVER = (
    "this Drama API cannot keep caption labels yet (an older deploy). The kit no longer burns them from the "
    "command line: update the server, then finish again"
)

Label = tuple[str, float, float]


def stored_take_labels(
    spine: Mapping[str, Any] | None, episode: int, take: int
) -> tuple[Label, ...]:
    """The take's caption labels as the server holds them on the spine (no call).

    Parameters
    ----------
    spine
        The saved spine, or ``None``.
    episode
        Episode ordinal.
    take
        One-based take.

    Returns
    -------
    tuple[Label, ...]
        ``(text, start, end)`` per label, in time order.
    """

    if not isinstance(spine, Mapping):
        return ()
    from creation.post.desk import spine_body
    from creation.spine_view import episode_summary

    summary = episode_summary(spine_body(spine), episode)
    out = [
        (str(item.get("text") or ""), float(item["start_s"]), float(item["end_s"]))
        for item in summary.get("caption_labels") or []
        if isinstance(item, Mapping) and item.get("take") == take and item.get("text")
    ]
    return tuple(sorted(out, key=lambda label: (label[1], label[2])))


def save_take_labels(
    desk: Path,
    episode: int,
    take: int,
    labels: Sequence[Label],
    *,
    out: TextIO | None = None,
    api: Any = None,
) -> tuple[tuple[Label, ...], dict[str, Any]]:
    """Send one take's caption labels to the server; print its warnings; answer what it keeps.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take
        One-based take.
    labels
        ``(text, start, end)`` per label; empty clears the take's labels.
    out
        Text stream.
    api
        Open API session (tests); the desk's own otherwise.

    Returns
    -------
    tuple[tuple[Label, ...], dict[str, Any]]
        The labels as the server keeps them, and the spine read again after.

    Raises
    ------
    RuntimeError
        An older server, or the server refused the labels.
    """

    from creation.harness.http_util import api_error_text
    from creation.post.desk import open_api, refresh_spine, spine_id
    from creation.spine_view import episode_id_for

    stream: TextIO = out if out is not None else sys.stdout
    session = open_api(desk, episode) if api is None else None
    run = api if api is not None else session
    try:
        body = refresh_spine(run, desk, episode)
        path = LABELS_ROUTE.format(
            spine_id=spine_id(desk), episode_id=episode_id_for(body, episode)
        )
        request = {
            "take": take,
            "labels": [
                {"text": text, "start_s": start, "end_s": end}
                for text, start, end in labels
            ],
        }
        answer: Any = None
        for attempt in (1, 2):
            status, answer = run.put_optional(
                path, {"spine_version": body.get("spine_version"), **request}
            )
            code = (
                (answer.get("error") or {}).get("code")
                if isinstance(answer, Mapping)
                else None
            )
            if status == 409 and code == "spine_version_conflict" and attempt == 1:
                body = refresh_spine(run, desk, episode)
                continue
            if status in (404, 405) and code not in (
                "spine_not_found",
                "episode_not_found",
            ):
                raise RuntimeError(OLDER_SERVER)
            if not 200 <= status < 300 or not isinstance(answer, Mapping):
                raise RuntimeError(
                    f"the server refused the caption labels (HTTP {status}): {api_error_text(answer)}"
                )
            break
        body = refresh_spine(run, desk, episode)
    finally:
        if session is not None:
            session.client.close()
    for warning in answer.get("warnings") or []:
        message = warning.get("message") if isinstance(warning, Mapping) else warning
        print(f"[caption-label] note: {message}", file=stream)
    kept = tuple(
        (str(item["text"]), float(item["start_s"]), float(item["end_s"]))
        for item in answer.get("labels") or []
    )
    shown = (
        ", ".join(f"{text!r}@{start:g}-{end:g}" for text, start, end in kept) or "none"
    )
    print(f"[caption-label] t{take} on the server: {shown}", file=stream)
    return kept, body


def finish_caption_labels(
    desk: Path,
    episode: int,
    take: int,
    given: Sequence[Label],
    spine: Mapping[str, Any] | None,
    *,
    clear: bool = False,
    out: TextIO | None = None,
) -> tuple[tuple[Label, ...], Mapping[str, Any] | None]:
    """The labels ``finish`` burns on a take: the server's, after any it was given are saved.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take
        One-based take.
    given
        ``--caption-label`` values (sent to the server first when any).
    spine
        The saved spine.
    clear
        ``--no-caption-labels``: clear the take's labels on the server.
    out
        Text stream.

    Returns
    -------
    tuple[tuple[Label, ...], Mapping[str, Any] | None]
        The labels to burn, and the spine (read again when anything was sent).
    """

    if clear and given:
        raise ValueError("--caption-label or --no-caption-labels, not both")
    if given or clear:
        kept, body = save_take_labels(
            desk, episode, take, () if clear else given, out=out
        )
        return kept, body
    return stored_take_labels(spine, episode, take), spine


__all__ = [
    "LABELS_ROUTE",
    "OLDER_SERVER",
    "finish_caption_labels",
    "save_take_labels",
    "stored_take_labels",
]
