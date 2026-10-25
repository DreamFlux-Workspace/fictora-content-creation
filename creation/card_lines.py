"""The lighter cards' short lines come from the server (lighter cards v3, founder, 25 Oct 2026).

The app's idea, script and cast cards show short lines only, and the server
writes every one of them (fictora-drama ``card_lines``): the show's hook (why
you would watch), the story's question, each episode's two short lines and
its people (seen-only ones too), each person's role line and "wants" line, a
place name, and a short label per shot ("Close on Noor's wrist · still"). The
kit prints the same lines for the operator, so kit and app match: one source,
nothing kept on the desk.

- ``GET /v1/spines/{id}/card-lines?episode=N`` writes what the story still
  owes for the episode (one small text call on the server, a few seconds at
  most, free) and answers the lines. An older server answers 404; the kit then
  prints nothing extra.
- Names come from the spine's cast cards (``short_name`` when the server sends it).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

#: The script card's camera words, after a shot's label.
CAMERA_WORDS = {
    "still": "still",
    "push_in": "push in",
    "pull_back": "pull back",
    "pan": "pan",
    "tilt": "tilt",
    "handheld": "handheld",
}


class _Api(Protocol):
    """The slice of :class:`creation.harness.session.DramaApiRunSession` used here."""

    def get_optional(self, path: str) -> tuple[int, Any]: ...


def card_lines_path(spine_id: str, episode: int) -> str:
    """The card-lines route for one episode.

    Parameters
    ----------
    spine_id
        The desk's story.
    episode
        Episode ordinal.

    Returns
    -------
    str
        ``/v1/spines/{spine_id}/card-lines?episode={episode}``.
    """

    return f"/v1/spines/{spine_id}/card-lines?episode={episode}"


def read_card_lines(run: _Api, spine_id: str, episode: int) -> dict[str, Any] | None:
    """Read (and have the server write, when owed) the cards' short lines for one episode.

    Parameters
    ----------
    run
        The API session.
    spine_id
        The desk's story.
    episode
        Episode ordinal.

    Returns
    -------
    dict[str, Any] | None
        The server's ``card_lines``, or ``None`` on an older server or a refused read.
    """

    status, answer = run.get_optional(card_lines_path(spine_id, episode))
    if status != 200 or not isinstance(answer, Mapping):
        return None
    lines = answer.get("card_lines")
    return dict(lines) if isinstance(lines, Mapping) else None


def _names(spine: Mapping[str, Any] | None) -> dict[str, str]:
    cast = spine.get("cast") if isinstance(spine, Mapping) else None
    out: dict[str, str] = {}
    for card in cast if isinstance(cast, Sequence) else []:
        if isinstance(card, Mapping) and card.get("cast_id"):
            out[str(card["cast_id"])] = str(
                card.get("short_name") or card.get("name") or card["cast_id"]
            )
    return out


def idea_text(
    lines: Mapping[str, Any], spine: Mapping[str, Any] | None, episode: int
) -> str:
    """The idea card and the episode's people as the app shows them, in the server's words.

    Parameters
    ----------
    lines
        The server's ``card_lines``.
    spine
        The spine (for the title and the people's names), or ``None``.
    episode
        Episode ordinal.

    Returns
    -------
    str
        Printable block: title, hook, the question, the episode's two lines,
        who is in it ("Name: role", then what they want), and where.
    """

    names = _names(spine)
    title = str(spine.get("title") or "") if isinstance(spine, Mapping) else ""
    out = [f"Idea card{': ' + title if title else ''}"]
    out.append(f"  Hook: {lines.get('show_hook')}")
    out.append(f"  The question: {lines.get('story_question')}")
    entry = next(
        (
            item
            for item in lines.get("episodes") or []
            if isinstance(item, Mapping) and item.get("episode_ordinal") == episode
        ),
        None,
    )
    cast = {
        str(item.get("cast_id")): item
        for item in lines.get("cast") or []
        if isinstance(item, Mapping)
    }
    if entry is not None:
        out.append(f"  Episode {episode}:")
        out.extend(f"    {line}" for line in entry.get("episode_short") or [])
        people = [str(cast_id) for cast_id in entry.get("episode_cast_ids") or []]
        seen_only = {str(cast_id) for cast_id in entry.get("seen_only_cast_ids") or []}
        if people:
            out.append("  Who's in it:")
        for cast_id in people:
            item = cast.get(cast_id, {})
            seen = " (only seen, no lines)" if cast_id in seen_only else ""
            out.append(
                f"    {names.get(cast_id, cast_id)}: {item.get('role_line', '')}{seen}"
            )
            if item.get("want_short"):
                out.append(f"      {item['want_short']}")
    if lines.get("place_name"):
        out.append(f"  Where: {lines['place_name']}")
    if lines.get("pending"):
        out.append(
            "  (Some lines are still the story's own words; the server writes them on the next read.)"
        )
    return "\n".join(out)


def shot_line(shot: Mapping[str, Any]) -> str | None:
    """A shot's light line as the app's "See shots" shows it ("Close on Noor's wrist · still").

    Parameters
    ----------
    shot
        One pitch-card shot.

    Returns
    -------
    str | None
        The label and the camera word, or ``None`` on an older server.
    """

    label = shot.get("shot_label")
    if not label:
        return None
    camera = CAMERA_WORDS.get(str(shot.get("shot_camera") or ""), "")
    return f"{label} · {camera}" if camera else str(label)


__all__ = [
    "CAMERA_WORDS",
    "card_lines_path",
    "idea_text",
    "read_card_lines",
    "shot_line",
]
