"""Lines the server laid on a take after filming: never laid twice, never flagged, captioned heard-not-seen.

On a take whose video model voices its own lines (a model-voice show, a take
that fell back to its own sound), the model puts every line it is given on a
visible face (Deep End ep 4: the boy mouthed the PA announcer). The server
(fictora-drama #834) now keeps a *bodiless* line out of what the model is asked
to say (an off-screen PA, radio or intercom shout, a voice-only character, a
narrator never seen), renders it in that voice and lays it on the take after
filming, at its planned time, with the creator's words exactly. The take facts
say which lines it laid::

    laid_after_filming: [{"line_id": str, "speaker": str,
                          "start_seconds": float, "end_seconds": float,
                          "spine_line_id": str}]   # spine_line_id when known

``line_id`` is the spine's crowd line id for a background voice, and
``<scene>-s<shot>-l<position>`` for a cast line (the take's id, the shot, the
line's place among the shot's lines). ``spine_line_id`` (newer servers) is the
story's own line id (``dialogue_lines[]`` or crowd line) and names the script
line outright; without it the kit matches by speaker, shot and time.
``speaker`` is the name the take prints.
Times are on the filmed take. The list is left out on every other take, and an
older server never sends it: then nothing here changes anything.

The kit only reads it (:func:`laid_after_filming`, :func:`laid_line_ids`):

- ``finish --voice`` and ``voice-line`` refuse to lay such a line a second time
  (:func:`second_lays`, :func:`laid_voice_line_refusal`);
- the line check and the speaker check never flag it (``WRONG SHOT``,
  ``SPEAKER NOT ON SCREEN``, an off-screen "may mouth it" warning, extra speech
  in its window): nobody on screen was asked to say it;
- its caption is set in the heard-not-seen italic.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The take facts field (fictora-drama #834).
LAID_FIELD = "laid_after_filming"
#: Seconds of slack when a hand line or a mute is compared with a laid line's window.
WINDOW_SLACK_SECONDS = 0.25
#: A cast line's laid id: ``<scene>-s<shot>-l<position>``.
_CAST_LINE_ID = re.compile(r"-s(\d+)-l(\d+)$")
#: A speaker label's trailing delivery note: ``Narrator (V.O.)``, ``Aya (inner voice)``.
_DRY_LABEL = re.compile(
    r"(?i)\s*\(\s*(?:inner voice|voice[- ]over|narration|v\.\s?o\.?|o\.\s?s\.?|off[- ]screen)\s*\)\s*$"
)


@dataclass(frozen=True)
class LaidLine:
    """One line the server laid on the take after filming (who and when; never its words)."""

    line_id: str
    speaker: str
    start: float
    end: float
    #: The story's own line id, when the server sends it (``spine_line_id``).
    spine_line_id: str = ""

    @property
    def shot_index(self) -> int | None:
        """The shot of a cast line (from its ``-s<shot>-l<n>`` id); ``None`` for a crowd line."""

        found = _CAST_LINE_ID.search(self.line_id)
        return int(found.group(1)) if found else None

    def label(self) -> str:
        """``PA Announcer at 2.00-4.10s``."""

        return f"{self.speaker} at {self.start:.2f}-{self.end:.2f}s"


def _body(facts: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not facts:
        return {}
    body = facts.get("take_facts", facts)
    return body if isinstance(body, Mapping) else {}


def laid_after_filming(facts: Mapping[str, Any] | None) -> tuple[LaidLine, ...]:
    """The take facts' ``laid_after_filming`` rows (empty when absent, as on an older server).

    Parameters
    ----------
    facts
        The take facts (``{"take_facts": {...}}`` or the facts alone), or ``None``.

    Returns
    -------
    tuple[LaidLine, ...]
        Each readable row, in the order the server lists them.
    """

    out: list[LaidLine] = []
    for row in _body(facts).get(LAID_FIELD) or []:
        if not isinstance(row, Mapping):
            continue
        line_id, speaker = (
            str(row.get("line_id") or "").strip(),
            str(row.get("speaker") or "").strip(),
        )
        start, end = row.get("start_seconds"), row.get("end_seconds")
        if (
            not line_id
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or end <= start
        ):
            continue
        story = str(row.get("spine_line_id") or "").strip()
        out.append(LaidLine(line_id, speaker, float(start), float(end), story))
    return tuple(out)


def speaker_key(name: str) -> str:
    """A speaker name to compare on: no trailing delivery note, case-folded, one space."""

    return re.sub(r"\s+", " ", _DRY_LABEL.sub("", name or "")).strip().casefold()


def _overlaps(a: tuple[float, float], b: tuple[float, float], slack: float) -> bool:
    return a[0] <= b[1] + slack and b[0] <= a[1] + slack


def laid_line_ids(
    facts: Mapping[str, Any] | None,
    *,
    cast_names: Mapping[str, str] | None = None,
    line_cast: Mapping[str, str] | None = None,
) -> dict[str, LaidLine]:
    """Which of the take's spine lines the server laid after filming.

    A row with ``spine_line_id`` (newer servers) names its line outright. Without
    it, a background voice's row names its spine line id, and a cast line's row
    names the take's own id, so it is found by its speaker (the cast card of that
    name) among the take facts' ``lines[]``, in the row's shot (else by time),
    first unclaimed first.

    Parameters
    ----------
    facts
        The take facts.
    cast_names
        ``cast_id`` to display name (the spine's cast).
    line_cast
        ``line_id`` to ``cast_id`` for the take's lines, in the order they play:
        fills a ``lines[]`` row without ``cast_id``, and stands in for
        ``lines[]`` when the facts list none.

    Returns
    -------
    dict[str, LaidLine]
        Spine ``line_id`` to the row that laid it; empty when the facts carry no rows.
    """

    laid = laid_after_filming(facts)
    if not laid:
        return {}
    names = cast_names or {}
    owners = dict(line_cast or {})
    rows: list[Mapping[str, Any]] = [
        row
        for row in _body(facts).get("lines") or []
        if isinstance(row, Mapping) and row.get("line_id")
    ]
    if not rows:
        rows = [
            {"line_id": line_id, "cast_id": cast} for line_id, cast in owners.items()
        ]
    known = {str(row["line_id"]) for row in rows} | set(owners)
    found: dict[str, LaidLine] = {}
    # The server's own id first, so the fallback below never takes a line it names.
    for item in laid:
        if item.spine_line_id:
            found.setdefault(item.spine_line_id, item)
    for item in laid:
        if item.spine_line_id:
            continue
        if item.line_id in known:
            found.setdefault(item.line_id, item)
            continue
        key = speaker_key(item.speaker)
        casts = {
            cast_id
            for cast_id, name in names.items()
            if key and (speaker_key(name) == key or speaker_key(cast_id) == key)
        }
        if not casts:
            continue
        candidates = [
            row
            for row in rows
            if str(row["line_id"]) not in found
            and str(row.get("cast_id") or owners.get(str(row["line_id"])) or "")
            in casts
            and (row.get("count") is None or int(row.get("count") or 0) >= 1)
        ]
        shot = item.shot_index
        if shot is not None and any(
            row.get("shot_index") is not None for row in candidates
        ):
            candidates = [row for row in candidates if row.get("shot_index") == shot]
        elif any(
            isinstance(row.get("start_seconds"), (int, float)) for row in candidates
        ):
            candidates = [
                row
                for row in candidates
                if isinstance(row.get("start_seconds"), (int, float))
                and isinstance(row.get("end_seconds"), (int, float))
                and _overlaps(
                    (float(row["start_seconds"]), float(row["end_seconds"])),
                    (item.start, item.end),
                    WINDOW_SLACK_SECONDS,
                )
            ]
        if candidates:
            found[str(candidates[0]["line_id"])] = item
    return found


def laid_windows(facts: Mapping[str, Any] | None) -> tuple[tuple[float, float], ...]:
    """Where each laid line plays on the take (seconds)."""

    return tuple((item.start, item.end) for item in laid_after_filming(facts))


def _covered(window: tuple[float, float], mutes: Sequence[tuple[float, float]]) -> bool:
    return any(
        a <= window[0] + WINDOW_SLACK_SECONDS and b >= window[1] - WINDOW_SLACK_SECONDS
        for a, b in mutes
    )


@dataclass(frozen=True)
class HandVoice:
    """One ``finish --voice`` line as the laid-line guard reads it."""

    path: Path
    start: float
    end: float
    #: The ``voice-line`` sidecar's ``cast_id`` and ``line`` (empty when unknown).
    cast_id: str = ""
    text: str = ""


def second_lays(
    laid: Mapping[str, LaidLine],
    texts: Mapping[str, str],
    voices: Sequence[HandVoice],
    *,
    mutes: Sequence[tuple[float, float]] = (),
    cast_names: Mapping[str, str] | None = None,
    said_as: Any = None,
) -> list[str]:
    """Each ``finish --voice`` line that would lay a line the server already laid (a stop each).

    A hand line is that line again when its words are the laid line's (``said_as``
    decides, with the line check's own threshold), or when its speaker is the
    laid line's speaker and it plays over the laid line's window. A ``--mute``
    over the whole laid window takes the server's line out first: the hand line
    replaces it, never doubles it, and is allowed.

    Parameters
    ----------
    laid
        :func:`laid_line_ids` (spine line id to its row); unresolved rows are not here.
    texts
        Spine line id to its words as performed.
    voices
        The hand lines, with their sidecar's speaker and words.
    mutes
        ``finish --mute`` windows.
    cast_names
        ``cast_id`` to display name.
    said_as
        ``(text, line_id) -> bool``: the hand words are heard as that line.

    Returns
    -------
    list[str]
        One refusal per doubled line; empty when nothing is laid twice.
    """

    names = cast_names or {}
    out: list[str] = []
    for voice in voices:
        speaker = speaker_key(names.get(voice.cast_id, "")) if voice.cast_id else ""
        for line_id, item in laid.items():
            window = (item.start, item.end)
            if _covered(window, mutes):
                continue
            same_words = (
                bool(voice.text)
                and said_as is not None
                and said_as(voice.text, line_id)
            )
            same_voice = (
                bool(speaker)
                and speaker == speaker_key(item.speaker)
                and _overlaps((voice.start, voice.end), window, WINDOW_SLACK_SECONDS)
            )
            if not (same_words or same_voice):
                continue
            words = texts.get(line_id, "")
            quoted = f' "{words}"' if words else ""
            out.append(
                f"--voice {voice.path.name}@{voice.start:.2f}s lays {item.speaker}'s line{quoted} again: the "
                f"server already laid it on this take after filming ({item.label()}; take facts "
                f"{LAID_FIELD}), so it would play twice. Drop this --voice; to replace the server's line "
                f"instead, add --mute {item.start:.2f}-{item.end:.2f} so only yours plays"
            )
            break
    return out


def muted_laid_lines(
    laid: Mapping[str, LaidLine],
    texts: Mapping[str, str],
    mutes: Sequence[tuple[float, float]],
    voices: Sequence[HandVoice] = (),
) -> list[str]:
    """``!!`` warnings for a ``--mute`` that silences a laid line with no hand line in its place."""

    out: list[str] = []
    for line_id, item in laid.items():
        window = (item.start, item.end)
        hit = [m for m in mutes if _overlaps(m, window, 0.0)]
        if not hit or any(
            _overlaps((v.start, v.end), window, WINDOW_SLACK_SECONDS) for v in voices
        ):
            continue
        words = texts.get(line_id, "")
        quoted = f' "{words}"' if words else ""
        a, b = hit[0]
        out.append(
            f"!! --mute {a:.2f}-{b:.2f} silences {item.speaker}'s line{quoted}, which the server laid on "
            f"this take after filming ({item.label()}): it will be missing from the episode. Drop the mute "
            "unless that is what you want"
        )
    return out


@dataclass(frozen=True)
class TakeLaid:
    """A desk take's laid lines, resolved to its script (:func:`take_laid_lines`)."""

    #: Spine line id to the row that laid it.
    laid: dict[str, LaidLine]
    #: Spine line id to its words as performed.
    texts: dict[str, str]
    #: The take's script lines (:func:`creation.post.line_check.script_lines_for_take`).
    lines: tuple[Any, ...] = ()
    language: str = "en"
    #: ``cast_id`` to display name.
    names: dict[str, str] | None = None

    def said_as(self, text: str, line_id: str) -> bool:
        """Whether ``text`` is heard as line ``line_id`` (the line check's own threshold)."""

        from creation.post.line_check import patched_line_ids

        line = [row for row in self.lines if row.line_id == line_id]
        return bool(line) and line_id in patched_line_ids(line, [text], self.language)


def take_laid_lines(
    desk: Path, episode: int, take_id: str, facts: Mapping[str, Any] | None
) -> TakeLaid:
    """The lines the server laid on one desk take, with their words, read from the saved spine.

    Parameters
    ----------
    desk
        Series desk.
    episode, take_id
        Which take.
    facts
        Its take facts.

    Returns
    -------
    TakeLaid
        Empty when the facts carry no ``laid_after_filming`` (an older server) or no spine is saved.
    """

    if not laid_after_filming(facts):
        return TakeLaid({}, {})
    from creation.post.desk import saved_spine
    from creation.post.line_check import script_lines_for_take, shouts_asked_of
    from creation.post.review import take_lines

    found = take_lines(desk, episode, take_id, shouts_asked=shouts_asked_of(facts))
    spine = saved_spine(desk, episode)
    if found is None or spine is None:
        return TakeLaid({}, {})
    raw, language = found
    names = {
        str(card.get("cast_id")): str(card.get("name") or "")
        for card in spine[0].get("cast") or []
        if isinstance(card, Mapping) and card.get("cast_id")
    }
    lines = script_lines_for_take(raw, facts, cast_names=names)
    laid = laid_line_ids(
        facts,
        cast_names=names,
        line_cast={
            str(r.get("line_id")): str(r.get("cast_id") or "")
            for r in raw
            if r.get("line_id")
        },
    )
    texts = {line.line_id: line.performed for line in lines if line.line_id in laid}
    return TakeLaid(laid, texts, tuple(lines), language, names)


def _sidecar(path: Path) -> tuple[str, str]:
    """A ``voice-line`` file's ``cast_id`` and ``line`` from its ``.json`` (empty when unknown)."""

    import json

    meta = path.with_suffix(".json")
    try:
        body = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    if not isinstance(body, Mapping):
        return "", ""
    return str(body.get("cast_id") or ""), str(body.get("line") or "")


def finish_laid_guard(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    facts: Mapping[str, Any] | None,
    mutes: Sequence[tuple[float, float]] = (),
    voices: Sequence[tuple[Any, float]] = (),
) -> tuple[list[str], list[str]]:
    """``finish``'s check of its hand ``--voice`` / ``--mute`` against the lines the server laid.

    Parameters
    ----------
    desk
        Series desk.
    episode, take_id
        Which take.
    facts
        The take facts (on the clip's timeline).
    mutes
        ``--mute`` windows.
    voices
        ``(Placed, seconds)`` per ``--voice`` line (:attr:`creation.post.hand.HandPlan.voices`).

    Returns
    -------
    tuple[list[str], list[str]]
        Refusals (a ``--voice`` laying a laid line again: finish stops before making anything)
        and ``!!`` warnings (a ``--mute`` silencing a laid line). Both empty on an older server.
    """

    if not (mutes or voices) or not laid_after_filming(facts):
        return [], []
    take = take_laid_lines(desk, episode, take_id, facts)
    if not take.laid:
        return [], []
    hand = [
        HandVoice(
            placed.path,
            float(placed.start),
            float(placed.start) + float(seconds),
            *_sidecar(placed.path),
        )
        for placed, seconds in voices
    ]
    refusals = second_lays(
        take.laid,
        take.texts,
        hand,
        mutes=mutes,
        cast_names=take.names,
        said_as=take.said_as,
    )
    return refusals, muted_laid_lines(take.laid, take.texts, mutes, hand)


def episode_take_facts(desk: Path, episode: int) -> dict[str, Path]:
    """Each take's newest saved take facts on the desk (``epNN/api/take-facts-epNN-tK-vN.json``)."""

    newest: dict[str, tuple[int, Path]] = {}
    api = desk / f"ep{episode:02d}" / "api"
    pattern = re.compile(rf"^take-facts-ep{episode:02d}-(t\d+)-v(\d+)\.json$")
    for path in api.glob(f"take-facts-ep{episode:02d}-*.json") if api.is_dir() else ():
        found = pattern.match(path.name)
        if found and int(found.group(2)) >= newest.get(found.group(1), (-1, path))[0]:
            newest[found.group(1)] = (int(found.group(2)), path)
    return {take: path for take, (_v, path) in sorted(newest.items())}


def laid_voice_line_refusal(
    desk: Path, *, episode: int, cast_id: str, name: str, text: str
) -> str:
    """Why ``voice-line`` must not make this line: a take of the episode already has it laid; ``""`` otherwise.

    Read before anything is spent. A line counts as laid when its words are a
    laid line's (any speaker: a PA line voiced by a cast member is still that
    line). Takes whose facts carry no ``laid_after_filming`` (an older server)
    never refuse.
    """

    import json

    for take_id, path in episode_take_facts(desk, episode).items():
        try:
            facts = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(facts, Mapping) or not laid_after_filming(facts):
            continue
        take = take_laid_lines(desk, episode, take_id, facts)
        for line_id, item in take.laid.items():
            if take.said_as(text, line_id):
                return (
                    f"{name}'s line {text!r} is already laid on ep{episode:02d} {take_id} by the server after "
                    f"filming ({item.label()}; take facts {LAID_FIELD}): a hand line would play it twice, so "
                    "nothing was made or spent. finish plays it as it is. To replace the server's line with a new "
                    "one, run voice-line again with --replace-laid, then finish that take with "
                    f"--mute {item.start:.2f}-{item.end:.2f} --voice FILE@{item.start:.2f}"
                )
    return ""


def voice_fx_laid_notes(
    desk: Path, *, episode: int, file: Path, ranges: Sequence[tuple[float, float]]
) -> list[str]:
    """Notes for ``voice-fx`` ranges over a line the server laid: the treatment is optional there.

    The take is read from the file name (``take-epNN-tK-...``); a file named otherwise, or facts
    without ``laid_after_filming``, give no note.
    """

    import json

    found = re.search(rf"take-ep{episode:02d}-(t\d+)", file.name)
    path = episode_take_facts(desk, episode).get(found.group(1)) if found else None
    if path is None:
        return []
    try:
        facts = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[str] = []
    for item in laid_after_filming(facts if isinstance(facts, Mapping) else None):
        if any(_overlaps(span, (item.start, item.end), 0.0) for span in ranges):
            out.append(
                f"note: {item.label()} was laid by the server after filming (take facts {LAID_FIELD}): no face "
                "mouths it, so this treatment only gives the voice a source (optional)"
            )
    return out


__all__ = [
    "LAID_FIELD",
    "WINDOW_SLACK_SECONDS",
    "HandVoice",
    "LaidLine",
    "TakeLaid",
    "episode_take_facts",
    "finish_laid_guard",
    "laid_voice_line_refusal",
    "take_laid_lines",
    "voice_fx_laid_notes",
    "laid_after_filming",
    "laid_line_ids",
    "laid_windows",
    "muted_laid_lines",
    "second_lays",
    "speaker_key",
]
