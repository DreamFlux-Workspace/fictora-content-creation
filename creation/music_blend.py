"""The show's music blend: which music families its score may play (founder decision, 2026-10-05).

The server keeps it on the spine (``music_blend``, fictora-drama
intent-driven scoring): up to three families from ``romance``, ``suspense``,
``mystery``, ``comedy``, ``action``, ``drama``, ``horror``, ``slice_of_life``.
The score follows each beat's music intent (written by the harness's beat
writer) and plays the blend's moods on ambiguous beats, so a romance with a
mystery spine can play suspense under a near-confession.

It never adds a step or a gate. Unset, a show plays its genre and overlays
(or the blend the season writer read from the premise). ``step`` and the
estimate print it on one line. ``music-blend --desk D`` reads it;
``--set romance,suspense`` changes it and ``--default`` goes back to the
genre's. Offer ``--set`` only when the human says the music feels off or asks
for it. Only takes filmed afterwards play a change; filmed takes keep their
music (a music note re-mixes those).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO

import httpx

from creation.harness.http_util import api_error_text
from creation.post.desk import open_api, refresh_spine, spine_body

#: The families a blend may name, in the order the server offers them.
MUSIC_BLEND_FAMILIES: tuple[str, ...] = (
    "romance",
    "suspense",
    "mystery",
    "comedy",
    "action",
    "drama",
    "horror",
    "slice_of_life",
)
#: Most families a blend names.
MAX_MUSIC_BLEND = 3
#: The server's route (GET reads, POST sets).
MUSIC_BLEND_ROUTE = "/v1/spines/{spine_id}/music-blend"
SOURCE_WORDS: Mapping[str, str] = {
    "show": "set for this show",
    "story": "read from the premise by the writer",
    "genre": "the show's genre",
    "older-server": "this Drama API has no music-blend route yet (an older deploy), so the genre decides",
    "unreachable": "the Drama API could not be reached; read from the saved spine",
}


class _Api(Protocol):
    """The slice of :class:`creation.harness.session.DramaApiRunSession` used here."""

    def get_optional(self, path: str) -> tuple[int, Any]: ...

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]: ...

    def spine(self, spine_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ShowMusicBlend:
    """The music families the show's score plays, and why."""

    families: tuple[str, ...]
    #: ``show`` | ``story`` | ``genre`` (the server's), or ``older-server`` / ``unreachable``.
    source: str

    def words(self) -> str:
        """``romance + suspense`` (``the genre's own`` when nothing is known)."""

        return (
            " + ".join(family.replace("_", " ") for family in self.families)
            or "the genre's own"
        )

    def line(self) -> str:
        """``Music blend: romance + suspense (set for this show).``"""

        return f"Music blend: {self.words()} ({SOURCE_WORDS.get(self.source, self.source)})."


def parse_blend(raw: str) -> tuple[str, ...]:
    """Read ``romance,suspense`` (or ``romance + suspense``) into families.

    Raises
    ------
    ValueError
        An unknown family, none at all, or more than :data:`MAX_MUSIC_BLEND`.
    """

    names: list[str] = []
    for part in raw.replace("+", ",").split(","):
        name = part.strip().casefold().replace(" ", "_").replace("-", "_")
        if not name:
            continue
        if name not in MUSIC_BLEND_FAMILIES:
            raise ValueError(
                f"unknown music family {part.strip()!r}; choose from {', '.join(MUSIC_BLEND_FAMILIES)}"
            )
        if name not in names:
            names.append(name)
    if not names:
        raise ValueError(
            f"name one to {MAX_MUSIC_BLEND} families: {', '.join(MUSIC_BLEND_FAMILIES)}"
        )
    if len(names) > MAX_MUSIC_BLEND:
        raise ValueError(f"a music blend names at most {MAX_MUSIC_BLEND} families")
    return tuple(names)


def _spine_id(spine: Mapping[str, Any]) -> str:
    return str(spine_body(spine).get("spine_id") or "")


def _from_spine(spine: Mapping[str, Any], source: str) -> ShowMusicBlend:
    stored = spine_body(spine).get("music_blend")
    if isinstance(stored, list) and stored:
        chosen = bool(spine_body(spine).get("music_blend_from_creator"))
        return ShowMusicBlend(
            tuple(str(name) for name in stored), "show" if chosen else "story"
        )
    return ShowMusicBlend((), source)


def show_music_blend(run: _Api, spine: Mapping[str, Any]) -> ShowMusicBlend:
    """Ask the server which music families the show plays (free).

    Returns
    -------
    ShowMusicBlend
        The server's answer. On an older server (no route: a bare 404) or no
        connection, the spine's stored ``music_blend`` when it has one, else
        the genre's own.

    Raises
    ------
    RuntimeError
        Any other refusal.
    """

    sid = _spine_id(spine)
    try:
        status, answer = run.get_optional(MUSIC_BLEND_ROUTE.format(spine_id=sid))
    except httpx.TransportError:
        return _from_spine(spine, "unreachable")
    if 200 <= status < 300 and isinstance(answer, Mapping):
        families = answer.get("music_blend")
        if isinstance(families, list):
            return ShowMusicBlend(
                tuple(str(name) for name in families),
                str(answer.get("source") or "genre"),
            )
    error = answer.get("error") if isinstance(answer, Mapping) else None
    if status in (404, 405) and not (isinstance(error, Mapping) and error.get("code")):
        return _from_spine(spine, "older-server")
    raise RuntimeError(
        f"the server could not say the show's music blend (HTTP {status}): {api_error_text(answer)}"
    )


def set_music_blend(
    run: _Api, spine: Mapping[str, Any], families: Sequence[str] | None
) -> dict[str, Any]:
    """Store ``families`` as the show's music blend, or go back to the genre's with ``None`` (free).

    A stale spine version is read again and sent once more.

    Raises
    ------
    RuntimeError
        The server is older than the route, or refused the change.
    """

    body = spine_body(spine)
    sid = _spine_id(body)
    path = MUSIC_BLEND_ROUTE.format(spine_id=sid)
    wanted = list(families) if families else None
    for attempt in (1, 2):
        status, answer = run.post_optional(
            path, {"spine_version": body.get("spine_version"), "music_blend": wanted}
        )
        if 200 <= status < 300 and isinstance(answer, Mapping):
            return dict(answer)
        error = answer.get("error") if isinstance(answer, Mapping) else None
        code = str(error.get("code") or "") if isinstance(error, Mapping) else ""
        if status == 409 and code == "spine_version_conflict" and attempt == 1:
            body = spine_body(run.spine(sid))
            continue
        if status in (404, 405) and not code:
            raise RuntimeError(
                "This Drama API has no music-blend route yet (an older deploy): the show's music follows its "
                "genre. Nothing was changed."
            )
        raise RuntimeError(
            f"the server refused the music blend (HTTP {status}): {api_error_text(answer)}"
        )
    raise AssertionError("unreachable")


def music_blend_line(run: _Api, spine: Mapping[str, Any]) -> str:
    """The one line ``step`` and the estimate print. Never stops the caller."""

    try:
        return show_music_blend(run, spine).line()
    except RuntimeError as exc:
        return f"Music blend: not known ({exc})."


def run_music_blend(
    desk: Path,
    *,
    set_to: str | None = None,
    default: bool = False,
    out: TextIO | None = None,
) -> ShowMusicBlend:
    """``music-blend --desk D [--set romance,suspense | --default]``: read or change the blend (free).

    Parameters
    ----------
    desk
        Series desk bound to a story.
    set_to
        ``romance,suspense`` to store it for the show; ``None`` to read it.
    default
        Drop the show's own blend: back to its genre (or the writer's).
    out
        Text stream.

    Returns
    -------
    ShowMusicBlend
        The show's blend after the call.
    """

    from creation.production_state import load_production

    if set_to is not None and default:
        raise ValueError("--set and --default go alone")
    families = parse_blend(set_to) if set_to is not None else None
    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    episode = load_production(desk).episode_ordinal
    run = open_api(desk, episode)
    try:
        spine = refresh_spine(run, desk, episode)
        if families is not None or default:
            answer = set_music_blend(run, spine, families)
            if answer.get("changed"):
                print("Music blend changed for this show.", file=out)
            else:
                print("The show already plays that blend: nothing changed.", file=out)
            if answer.get("notice"):
                print(str(answer["notice"]), file=out)
            spine = refresh_spine(run, desk, episode)
        blend = show_music_blend(run, spine)
    finally:
        run.client.close()
    print(blend.line(), file=out)
    print(
        "Each beat's own music intent (written by the harness) still leads; the blend decides the "
        "ambiguous beats. Filmed takes keep their music.",
        file=out,
    )
    print(
        f"Change it: fictora-produce music-blend --desk {desk} --set romance,suspense "
        f"(families: {', '.join(MUSIC_BLEND_FAMILIES)}; up to {MAX_MUSIC_BLEND}), or --default.",
        file=out,
    )
    return blend


__all__ = [
    "MAX_MUSIC_BLEND",
    "MUSIC_BLEND_FAMILIES",
    "MUSIC_BLEND_ROUTE",
    "ShowMusicBlend",
    "music_blend_line",
    "parse_blend",
    "run_music_blend",
    "set_music_blend",
    "show_music_blend",
]
