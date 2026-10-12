"""Is the speaker on screen when their line plays? The ``SPEAKER NOT ON SCREEN`` stop and its warnings.

Founder decision (12 Oct 2026): a line must never land in the wrong mouth.
Dead Heat ep 2 (locked voices) had the off-screen announcer's lines mouthed by
Moretti, the only face in the shot under them; Don't Look, Hana ep 6 (model
voices) rushed Sota's line into shot 1, which shows only Hana and the Cabin
Woman, so the ghost mouthed it. The video model animates whichever face is on
screen when a voice plays, so a line heard over a shot that shows other named
people and not its speaker is played by one of them. Both were caught by eye:
the old finish check (``WRONG SHOT``) compared timing only, never who is in
the shot.

After filming (:func:`speaker_faults`, both voice modes)
---------------------------------------------------------
Who is on screen comes from the take facts (``shots[].people.named``, cast ids
per shot; :func:`creation.post.take_facts.shot_people`). When the line plays
comes from what was heard: the transcript's words on a model-voice take (each
word by its middle, as the wrong-shot check reads them), the dialogue track's
line window on a locked-voice take (``soundtrack.lines[]``).

- An on-screen line with at least :data:`SPEAKER_SHARE` of it heard over shots
  that show other named people and not its speaker: **SPEAKER NOT ON SCREEN**,
  a stop like the other line faults (listen clip, re-film command,
  ``--accept-line-mismatch tK``). A moment within :data:`CUT_SLACK_SECONDS` of
  a shot that does show the speaker never counts (a cut a few frames off).
- An off-screen line (a character's, or a background shout the server marks
  ``off_screen``) over a shot that shows named people: a warning naming who may
  mouth it, never a stop. So is a background shout with no flag over a shot
  that shows only named people (nobody unnamed to shout it).
- Shots without ``people`` (an older server) are not judged; when no shot has
  them the check says it did not run.

Before filming (:func:`film_speaker_warnings`)
----------------------------------------------
Nothing the server returns before a film says who each filmed shot will show
(the estimate carries no take facts). So the warning reads the desk's own
spine: a beat's lines are spoken on the board row of the frame it anchors
(``beats[].frame_id``), and the row's cells say who is drawn there
(:func:`creation.spine_view.frame_cast`). It warns (never stops) when an
on-screen line sits on a row that draws other named people and not its
speaker, or an off-screen line sits on a row that draws exactly one face.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from creation.post.line_check import LineFault, ScriptLine, listen_window
from creation.post.take_facts import shot_people

#: Share of a line heard over shots without its speaker (and with other named faces) that stops finish.
SPEAKER_SHARE = 0.5
#: Seconds either side of a shot that shows the speaker in which a moment never counts against the line.
CUT_SLACK_SECONDS = 0.4
#: Seconds between the moments read along a locked-voice line's window.
WINDOW_STEP_SECONDS = 0.05
#: ``cast_id`` prefix of a background shout (never a cast member).
CROWD_PREFIX = "crowd:"


@dataclass(frozen=True)
class ShotFaces:
    """One shot of the take and the named people the take facts put in it."""

    index: int
    start: float
    end: float
    named: tuple[str, ...]
    unnamed: int = 0

    def label(self) -> str:
        """``shot 1 (0.00-5.00s)``."""

        return f"shot {self.index} ({self.start:.2f}-{self.end:.2f}s)"


@dataclass(frozen=True)
class HeardLine:
    """One line as it plays in the take: the moments it is heard, and its span."""

    line: ScriptLine
    #: Take seconds it is heard at (word middles, or steps along the track window).
    moments: tuple[float, ...]
    at: tuple[float, float]


def shot_faces(facts: Mapping[str, Any] | None) -> list[ShotFaces]:
    """The take's shots that carry a usable ``people`` and a time window, in time order.

    Parameters
    ----------
    facts
        The take facts (``{"take_facts": {...}}`` or the facts alone), or ``None``.

    Returns
    -------
    list[ShotFaces]
        Empty when no shot has ``people`` (an older server): nothing is judged then.
    """

    if not facts:
        return []
    body = facts.get("take_facts", facts)
    out: list[ShotFaces] = []
    for position, shot in enumerate(body.get("shots") or [], start=1):
        if not isinstance(shot, Mapping):
            continue
        people = shot_people(shot)
        start, end = shot.get("start_seconds"), shot.get("end_seconds")
        if (
            people is None
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or end <= start
        ):
            continue
        out.append(
            ShotFaces(
                index=int(shot.get("shot_index") or position),
                start=float(start),
                end=float(end),
                named=tuple(people["named"]),
                unnamed=int(people["unnamed"]),
            )
        )
    return sorted(out, key=lambda shot: shot.start)


def window_moments(
    start: float, end: float, *, step: float = WINDOW_STEP_SECONDS
) -> tuple[float, ...]:
    """Moments along a line window (its middle alone when the window is shorter than ``step``)."""

    if end <= start:
        return (start,)
    count = max(1, int((end - start) / step))
    return tuple(start + (i + 0.5) * (end - start) / count for i in range(count))


def _shot_at(shots: Sequence[ShotFaces], moment: float) -> ShotFaces | None:
    for position, shot in enumerate(shots):
        last = position == len(shots) - 1
        if shot.start <= moment < shot.end or (last and moment == shot.end):
            return shot
    return None


def _names(cast_ids: Sequence[str], names: Mapping[str, str]) -> str:
    return ", ".join(names.get(cast_id, cast_id) for cast_id in cast_ids)


def _who(line: ScriptLine, names: Mapping[str, str]) -> str:
    return line.speaker or names.get(line.cast_id, line.cast_id) or "the speaker"


def _dominant(shots: Sequence[ShotFaces], moments: Sequence[float]) -> ShotFaces | None:
    counts = Counter(
        shot.index
        for shot in (_shot_at(shots, moment) for moment in moments)
        if shot is not None
    )
    if not counts:
        return None
    index = counts.most_common(1)[0][0]
    return next(shot for shot in shots if shot.index == index)


def speaker_faults(
    heard: Sequence[HeardLine],
    shots: Sequence[ShotFaces],
    *,
    cast_names: Mapping[str, str] | None = None,
) -> tuple[list[LineFault], list[str]]:
    """Each line heard over a shot that shows other named people and not its speaker.

    Parameters
    ----------
    heard
        The take's lines as heard (:class:`HeardLine`): model-voice words, or locked-voice windows.
    shots
        :func:`shot_faces` of the take facts.
    cast_names
        ``cast_id`` to display name, for the messages.

    Returns
    -------
    tuple[list[LineFault], list[str]]
        ``speaker_off`` faults (each a stop) with their listen windows, and ``!!`` warnings (never a
        stop) for off-screen lines and unflagged background shouts over named faces.
    """

    names = cast_names or {}
    faults: list[LineFault] = []
    warnings: list[str] = []
    if not shots:
        return faults, warnings
    for item in heard:
        line = item.line
        if not item.moments:
            continue
        crowd = line.cast_id.startswith(CROWD_PREFIX)
        who = _who(line, names)
        quoted = f'line {line.number} ({who}) "{line.performed}"'
        when = f"{item.at[0]:.2f}-{item.at[1]:.2f}s"
        if line.off_screen or crowd:
            shot = _dominant(shots, item.moments)
            if shot is None:
                continue
            others = [c for c in shot.named if c != line.cast_id]
            if not others:
                continue
            if not line.off_screen and shot.unnamed:
                continue  # a shout with someone unnamed on screen to give it
            mouther = (
                f"{_names(others, names)} may mouth it"
                if len(others) == 1
                else f"one of {_names(others, names)} may mouth it"
            )
            heard_how = (
                "heard off screen"
                if line.off_screen
                else "a background shout with nobody unnamed on screen"
            )
            warnings.append(
                f"!! {quoted} is {heard_how} at {when}, over {shot.label()}, which shows "
                f"{_names(shot.named, names)}: {mouther}. Watch that shot's mouths before delivering (warning "
                "only; re-film or cut away if a face mouths it)"
            )
            continue
        if not line.cast_id:
            continue
        own = [shot for shot in shots if line.cast_id in shot.named]
        wrong: list[ShotFaces] = []
        for moment in item.moments:
            shot = _shot_at(shots, moment)
            if shot is None or line.cast_id in shot.named or not shot.named:
                continue
            if any(
                s.start - CUT_SLACK_SECONDS <= moment <= s.end + CUT_SLACK_SECONDS
                for s in own
            ):
                continue
            wrong.append(shot)
        if not wrong or len(wrong) < SPEAKER_SHARE * len(item.moments):
            continue
        index = Counter(shot.index for shot in wrong).most_common(1)[0][0]
        shot = next(s for s in wrong if s.index == index)
        found = LineFault(
            "speaker_off",
            line,
            at=item.at,
            detail=(
                f"over {shot.label()}, which shows {_names(shot.named, names)} but not {who}: "
                f"{'that face' if len(shot.named) == 1 else 'one of them'} mouths the line"
            ),
        )
        faults.append(replace(found, listen=listen_window(found)))
    return faults, warnings


def model_heard_lines(
    heard: Sequence[tuple[ScriptLine, float, float]],
    middles: Mapping[str, Sequence[float]],
) -> list[HeardLine]:
    """A model-voice take's heard lines with each word's middle (:attr:`LineCheck.middles`)."""

    return [
        HeardLine(
            line,
            tuple(middles.get(line.line_id) or window_moments(start, end)),
            (start, end),
        )
        for line, start, end in heard
    ]


# --- before filming -----------------------------------------------------------------------------------------


def _row(frame: Mapping[str, Any]) -> int:
    raw = frame.get("board_row")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return int(frame.get("ordinal") or 0)


def take_row_warnings(
    spine: Mapping[str, Any],
    *,
    episode: int,
    take_index: int,
    take_count: int,
    label: str,
    heard_ids: Collection[str] = (),
) -> list[str]:
    """Warnings for one take: a line on a board row without its speaker, or off screen over one face.

    Parameters
    ----------
    spine
        The spine as it is now.
    episode, take_index, take_count
        Which take (``take_index`` from 1).
    label
        ``ep02 t1``, for the messages.
    heard_ids
        Line ids heard off screen (:func:`creation.spine_view.heard_line_ids`).

    Returns
    -------
    list[str]
        ``!!`` lines (warning only); empty when every line sits with its speaker.
    """

    from creation.crowd_lines import beat_shouts
    from creation.spine_view import beats_by_take, frame_cast, frames_by_set

    frames = frames_by_set(spine, episode=episode).get(take_index, [])
    grouped = beats_by_take(spine, episode=episode, take_count=max(1, take_count))
    beats = grouped[take_index - 1] if 1 <= take_index <= len(grouped) else []
    row_of = {
        str(frame.get("frame_id")): _row(frame)
        for frame in frames
        if frame.get("frame_id")
    }
    drawn: dict[int, set[str]] = {}
    for frame in frames:
        drawn.setdefault(_row(frame), set()).update(frame_cast(frame)[0])
    names = {
        str(card.get("cast_id")): str(card.get("name") or card.get("cast_id"))
        for card in spine.get("cast") or []
        if isinstance(card, Mapping) and card.get("cast_id")
    }
    out: list[str] = []
    for beat in sorted(beats, key=lambda b: int(b.get("ordinal") or 0)):
        row = row_of.get(str(beat.get("frame_id") or ""))
        if row is None:
            continue
        faces = sorted(drawn.get(row, set()))
        rows: list[tuple[str, str, str, bool]] = [
            (
                str(raw.get("line_id")),
                str(raw.get("cast_id") or ""),
                str(raw.get("spoken_text") or raw.get("text") or "").strip(),
                raw.get("off_screen") is True or str(raw.get("line_id")) in heard_ids,
            )
            for raw in beat.get("dialogue_lines") or []
            if isinstance(raw, Mapping) and raw.get("line_id")
        ]
        flagged = {
            str(raw.get("line_id"))
            for raw in beat.get("crowd_lines") or []
            if isinstance(raw, Mapping) and raw.get("off_screen") is True
        }
        rows += [
            (s.line_id, f"{CROWD_PREFIX}{s.line_id}", s.performed, True)
            for s in beat_shouts(beat)
            if s.line_id in flagged
        ]
        for line_id, cast_id, text, off in rows:
            if not text:
                continue
            crowd = cast_id.startswith(CROWD_PREFIX)
            who = "a background shout" if crowd else names.get(cast_id, cast_id or "?")
            head = f'!! {label}: {who}{" (off screen)" if off else ""} says "{text}" on board row {row}'
            if off:
                others = [c for c in faces if c != cast_id]
                if len(others) == 1:
                    out.append(
                        f"{head}, which draws only {names.get(others[0], others[0])}: the take may put the line "
                        f"in {names.get(others[0], others[0])}'s mouth (Dead Heat ep 2). Give the row a cutaway "
                        "or a second face, or move the line to a row without a face, then redraw (warning only)"
                    )
                continue
            if not cast_id or cast_id in faces or not faces:
                continue
            mouth = (
                "one of their mouths"
                if len(faces) > 1
                else f"{_names(faces, names)}'s mouth"
            )
            out.append(
                f"{head}, which draws {_names(faces, names)} but not {who}: the take may put the line in "
                f"{mouth} (Don't Look, Hana ep 6). Draw {who} in that row, mark the line off screen (`line --line {line_id} --off-screen`), "
                "or give it to someone drawn there, then redraw (warning only)"
            )
    return out


def film_speaker_warnings(
    spine: Mapping[str, Any],
    *,
    episode: int,
    take_ids: Sequence[str],
    all_take_ids: Sequence[str],
) -> list[str]:
    """The film preflight's speaker warnings for the takes about to be filmed (never a stop).

    Parameters
    ----------
    spine
        The spine as it is now.
    episode
        Episode ordinal.
    take_ids
        The takes this film sends (``t1`` ...).
    all_take_ids
        Every take of the episode, in order (their position is the board set).

    Returns
    -------
    list[str]
        ``!!`` lines; empty when nothing is out of place or the board rows carry no frames yet.
    """

    from creation.spine_view import heard_line_ids

    heard = heard_line_ids(spine)
    out: list[str] = []
    for take_id in take_ids:
        if take_id not in all_take_ids:
            continue
        out += take_row_warnings(
            spine,
            episode=episode,
            take_index=list(all_take_ids).index(take_id) + 1,
            take_count=len(all_take_ids),
            label=f"ep{episode:02d} {take_id}",
            heard_ids=heard,
        )
    return out


__all__ = [
    "CROWD_PREFIX",
    "CUT_SLACK_SECONDS",
    "SPEAKER_SHARE",
    "WINDOW_STEP_SECONDS",
    "HeardLine",
    "ShotFaces",
    "film_speaker_warnings",
    "model_heard_lines",
    "shot_faces",
    "speaker_faults",
    "take_row_warnings",
    "window_moments",
]
