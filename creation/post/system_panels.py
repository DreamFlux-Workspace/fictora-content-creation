"""System panels on a finished take: the status windows a system or game genre is told through.

The server places and draws them; the kit only asks and burns. ``finish`` and
the desk's own reel cut send the takes' filmed lengths to the Drama API
(``POST /v1/spines/{spine_id}/episodes/{episode_id}/system-panels``) and get
back the placed panels and the ready ASS layer, which they burn after the
captions. The kit used to carry its own copy of the placement and drawing
rules (fictora-drama ``system_panels.py`` and ``system_panel_render.py``);
the two drifted, so the copy is gone and the server's rules are the only ones.

* **Genres** (user decision, 6 Oct 2026): only ``system_leveling``,
  ``last_human``, ``isekai``, ``cultivation`` and ``regression_revenge``
  (primary or overlay) ever show a panel. The kit asks the server only for
  those shows, so every other show finishes without the call.
* **The writer decides** each panel on a beat; the server places it (at most
  two in a take, never the same form twice in a row, never two at once) and
  draws it (the form's layout, the frame's shape, the palette's colours).
* An older server without the route gets a plain stop naming the fix
  (:data:`OLDER_SERVER`); nothing is drawn from a local copy.

Server-heavy rule (founder, 15 Oct 2026): placement and drawing are the
server's; the kit keeps only the call and the burn.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

#: The genres a system panel belongs to: the kit asks the server only for these shows.
SYSTEM_PANEL_GENRES = frozenset(
    {"system_leveling", "last_human", "isekai", "cultivation", "regression_revenge"}
)
#: The server's route (fictora-drama ``draw_episode_system_panels``).
ROUTE = "/v1/spines/{spine_id}/episodes/{episode_id}/system-panels"
#: A take whose length is unknown counts as this long when placing later takes' panels.
NOMINAL_TAKE_MS = 15_000
#: The house canvas a reel's layer is drawn for (libass scales it to the video).
CANVAS = (1080, 1920)
#: What an older server (no route) is told; ``{command}`` is ``finish`` or ``reel``.
OLDER_SERVER = "this Drama API cannot draw system panels yet; {command} with --no-panels until it is updated"
#: 404 codes the route answers on purpose; any other 404 means the server predates it.
_KNOWN_NOT_FOUND = frozenset({"spine_not_found", "episode_not_found"})


class SystemPanelsUnavailable(RuntimeError):
    """The server cannot place or draw this episode's panels (an older deploy, or a refusal)."""


class _Api(Protocol):
    """The slice of :class:`creation.harness.session.DramaApiRunSession` used here."""

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]: ...


@dataclass(frozen=True)
class DrawnPanel:
    """One panel in a layer, on the layer's own timeline (milliseconds)."""

    panel_id: str
    start_ms: int
    end_ms: int
    form: str
    frame: str
    position: str
    lines: tuple[str, ...]

    def describe(self) -> str:
        """One line for the run notes."""

        return (
            f"{self.form} ({self.frame}, {self.position}) {self.start_ms / 1000:.2f}-"
            f"{self.end_ms / 1000:.2f} s: {' / '.join(self.lines)}"
        )


@dataclass(frozen=True)
class PanelLayer:
    """The server's answer: the panels that play on the asked timeline and the ASS that draws them."""

    ass: str | None
    panels: tuple[DrawnPanel, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.ass) and bool(self.panels)

    def __len__(self) -> int:
        return len(self.panels)

    def describe(self) -> str:
        """``N panel(s): ...`` for the run notes."""

        return f"{len(self.panels)} panel(s): " + "; ".join(
            p.describe() for p in self.panels
        )


NO_PANELS = PanelLayer(ass=None)


def show_takes_panels(spine: Mapping[str, Any] | None) -> bool:
    """Whether the show's genre or an overlay is a system or game genre (the kit asks only then).

    Parameters
    ----------
    spine
        The spine as JSON.

    Returns
    -------
    bool
        True for a show that may show panels.
    """

    if not isinstance(spine, Mapping):
        return False
    genres = [
        spine.get("microdrama_genre"),
        *(spine.get("microdrama_genre_overlays") or []),
    ]
    return any(g in SYSTEM_PANEL_GENRES for g in genres if isinstance(g, str))


def _error_code(answer: Any) -> str:
    error = answer.get("error") if isinstance(answer, Mapping) else None
    return str(error.get("code") or "") if isinstance(error, Mapping) else ""


def _error_text(answer: Any) -> str:
    from creation.harness.http_util import api_error_text

    return api_error_text(answer)


def layer_from_answer(answer: Mapping[str, Any]) -> PanelLayer:
    """Read the route's answer into a :class:`PanelLayer`.

    Parameters
    ----------
    answer
        ``DramaSystemPanelsResponse`` as JSON.

    Returns
    -------
    PanelLayer
        The drawn panels (with their form, look and lines) and the ASS.
    """

    placed = {
        str(p.get("panel_id")): p
        for p in answer.get("panels") or []
        if isinstance(p, Mapping)
    }
    drawn: list[DrawnPanel] = []
    for item in answer.get("drawn") or []:
        if not isinstance(item, Mapping):
            continue
        panel = placed.get(str(item.get("panel_id")), {})
        style = panel.get("style") if isinstance(panel.get("style"), Mapping) else {}
        drawn.append(
            DrawnPanel(
                panel_id=str(item.get("panel_id")),
                start_ms=int(item.get("start_ms") or 0),
                end_ms=int(item.get("end_ms") or 0),
                form=str(panel.get("form") or ""),
                frame=str(style.get("frame") or ""),
                position=str(panel.get("position") or "upper"),
                lines=tuple(str(line) for line in panel.get("lines") or []),
            )
        )
    ass = answer.get("ass")
    return PanelLayer(ass=str(ass) if ass else None, panels=tuple(drawn))


def request_layer(
    desk: Path,
    episode: int,
    episode_id: str,
    body: dict[str, Any],
    *,
    command: str = "finish",
    api: _Api | None = None,
) -> PanelLayer:
    """Ask the server for an episode's panel layer (free; nothing is stored).

    Parameters
    ----------
    desk
        Series desk bound to a spine.
    episode
        Episode ordinal (its ``api/`` folder).
    episode_id
        The episode's id on the spine.
    body
        ``DramaSystemPanelsRequest`` as JSON.
    command
        The command the fix names (``finish`` or ``reel``).
    api
        An open session (tests); the desk's own by default.

    Returns
    -------
    PanelLayer
        What the server placed and drew.

    Raises
    ------
    SystemPanelsUnavailable
        An older server without the route (:data:`OLDER_SERVER`), or any refusal.
    """

    from creation.post.desk import open_api, spine_id

    session = open_api(desk, episode) if api is None else None
    run: _Api = api if api is not None else session  # type: ignore[assignment]
    try:
        status, answer = run.post_optional(
            ROUTE.format(spine_id=spine_id(desk), episode_id=episode_id), body
        )
    finally:
        if session is not None:
            session.client.close()
    if 200 <= status < 300 and isinstance(answer, Mapping):
        return layer_from_answer(answer)
    if status == 405 or (status == 404 and _error_code(answer) not in _KNOWN_NOT_FOUND):
        raise SystemPanelsUnavailable(OLDER_SERVER.format(command=command))
    raise SystemPanelsUnavailable(
        f"the server refused the system panels (HTTP {status}): {_error_text(answer)}; "
        f"{command} with --no-panels to go on without them"
    )


def take_ms(lengths_s: Sequence[float | None]) -> list[int]:
    """Each take's length in milliseconds; an unknown one counts as :data:`NOMINAL_TAKE_MS`.

    Parameters
    ----------
    lengths_s
        Lengths in seconds, ``t1`` first (``None``: no raw file on the desk).

    Returns
    -------
    list[int]
        Milliseconds.
    """

    return [round(s * 1000) if s else NOMINAL_TAKE_MS for s in lengths_s]


def take_layer(
    desk: Path,
    episode: int,
    episode_id: str,
    take: int,
    lengths_s: Sequence[float | None],
    *,
    width: int,
    height: int,
    picture: tuple[int, int, int, int] | None = None,
    take_count: int = 0,
    api: _Api | None = None,
) -> PanelLayer:
    """One take's panels on the take's own timeline, drawn by the server.

    Parameters
    ----------
    desk, episode, episode_id
        The episode.
    take
        The take's number (``t3`` is 3).
    lengths_s
        Lengths of ``t1`` .. this take in seconds.
    width, height
        The video the layer is burned on.
    picture
        The picture's box on a letterbox canvas (``x, y, w, h``); ``None`` when it fills the frame.
    take_count
        How many takes the episode has (the spine's ``beats_per_storyboard_set``):
        the server reads a beat's take from that pattern only when it gets every
        take's length, so takes after this one are sent at the nominal 15 s (they
        never move this take's panels).
    api
        An open session (tests).

    Returns
    -------
    PanelLayer
        Empty when no panel plays in this take.
    """

    durations = take_ms(lengths_s)[:take]
    if len(durations) < take:
        return NO_PANELS
    durations += [NOMINAL_TAKE_MS] * max(0, take_count - len(durations))
    body: dict[str, Any] = {
        "take_ms": durations,
        "width": width,
        "height": height,
        "take": take,
    }
    if picture is not None:
        body["picture"] = list(picture)
    return request_layer(desk, episode, episode_id, body, api=api)


def reel_layer(
    desk: Path,
    episode: int,
    episode_id: str,
    takes: Sequence[tuple[str, float]],
    segments: Sequence[Any],
    *,
    fps: float = 24.0,
    skip_takes: Sequence[str] = (),
    picture: tuple[int, int, int, int] | None = None,
    api: _Api | None = None,
) -> PanelLayer:
    """The episode's panels moved onto a desk-cut reel through its cut, drawn by the server.

    Parameters
    ----------
    desk, episode, episode_id
        The episode.
    takes
        ``(take_id, duration_s)`` of the reel's takes (``t1``, ``t2`` ...).
    segments
        The reel's segments in order (``.take``, ``.start``, ``.end`` in seconds).
    fps
        The reel's frame rate.
    skip_takes
        Takes cut from a file whose text is already burned in (it carries its panels).
    picture
        The picture's box on a letterbox reel.
    api
        An open session (tests).

    Returns
    -------
    PanelLayer
        On the reel's timeline (the house canvas; libass scales it to the reel).
    """

    numbered = {
        take_id: int(take_id[1:])
        for take_id, _ in takes
        if take_id[:1] == "t" and take_id[1:].isdigit()
    }
    if not numbered:
        return NO_PANELS
    lengths = {numbered[t]: d for t, d in takes if t in numbered}
    durations = take_ms([lengths.get(n) for n in range(1, max(numbered.values()) + 1)])
    cut = [
        {
            "take": seg.take,
            "start_s": max(0.0, float(seg.start)),
            "end_s": max(0.0, float(seg.end)),
        }
        for seg in segments
        if seg.take in numbered
    ]
    if not cut:
        return NO_PANELS
    body: dict[str, Any] = {
        "take_ms": durations,
        "width": CANVAS[0],
        "height": CANVAS[1],
        "reel": {"segments": cut, "fps": fps, "skip_takes": list(skip_takes)},
    }
    if picture is not None:
        body["picture"] = list(picture)
    return request_layer(desk, episode, episode_id, body, command="reel", api=api)


def burn_layer(layer: PanelLayer, video: Path, ass_path: Path, out: Path) -> Path:
    """Write the server's ASS layer to ``ass_path`` and burn it onto ``video`` as ``out``.

    Parameters
    ----------
    layer
        The server's layer (not empty).
    video, ass_path, out
        The take or reel, the ASS to write, the burned file.

    Returns
    -------
    Path
        ``out``.

    Raises
    ------
    ValueError
        An empty layer.
    """

    from creation.captions import burn_ass, find_ffmpeg

    if not layer.ass:
        raise ValueError("no system panel to burn")
    ass_path.write_text(layer.ass, encoding="utf-8")
    burn_ass(find_ffmpeg()[0], video, ass_path, out)
    return out


__all__ = [
    "CANVAS",
    "NO_PANELS",
    "NOMINAL_TAKE_MS",
    "OLDER_SERVER",
    "ROUTE",
    "SYSTEM_PANEL_GENRES",
    "DrawnPanel",
    "PanelLayer",
    "SystemPanelsUnavailable",
    "burn_layer",
    "layer_from_answer",
    "reel_layer",
    "request_layer",
    "show_takes_panels",
    "take_layer",
    "take_ms",
]
