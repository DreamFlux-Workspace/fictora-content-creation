"""Where each locked-voice line's voice really starts, so its caption never goes up before it (L-20261005-6).

A locked-voice take (``soundtrack.mode == "target_audio"``) plays the dialogue
track the server made, and its take facts say where each line sits on that
track (``soundtrack.lines``). Those windows are where the server *placed* each
line, not where its voice is: Petty Crimes (L-20261005-6) captions timed on them
came out about 0.45 s late, and on Three Payments Late (2026-10-06) every voice
starts 0.03-0.12 s after its window does, so its captions went up a few frames
before the voice.

``finish`` on a desk created on or after 6 Oct 2026 now times a locked-voice
take on a transcript of the take when it has one (made once on the server,
``/v1/transcripts``, cached), and on the windows only where the transcript did
not hear a line. Either way each caption's start is moved to where its voice is
measured to start on the take's own sound (:func:`voice_starts`): earlier when
the voice runs ahead of its window, later when it starts inside it. A caption
never goes up before its voice.

Legacy desks (created before 6 Oct 2026, :mod:`creation.rules_epoch`) keep the
planned windows exactly as before.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from creation.captions import Span

#: Level windows for the onset measure (seconds).
ONSET_WINDOW_SECONDS = 0.05
#: How far before its window a line's voice is looked for (the track can run ahead of its facts).
ONSET_EARLY_SECONDS = 0.6
#: Voice is louder than the window's loudest minus this ...
ONSET_BELOW_PEAK_DB = 20.0
#: ... and than the quiet before the line (:func:`_quiet_level`) plus this.
ONSET_OVER_GAP_DB = 6.0
#: Consecutive loud windows that make an onset (0.1 s: a click or a short effect is not a voice).
ONSET_MIN_WINDOWS = 2
#: How far back from its window the quiet before a line is read (twice the early reach).
ONSET_QUIET_SECONDS = 2 * ONSET_EARLY_SECONDS
#: The quiet before a line must be at least this long to read its level from.
ONSET_GAP_SECONDS = 0.15
#: A caption moved for its voice keeps at least this long on screen.
MIN_CAPTION_SECONDS = 0.3

Meter = Callable[..., Sequence[float]]


@dataclass(frozen=True)
class VoiceStart:
    """One line's planned window and where its voice was measured to start (``None``: not measured)."""

    line_id: str
    window: Span
    onset: float | None
    how: str

    @property
    def delta(self) -> float | None:
        """Seconds the voice starts after its window does (negative: ahead of it)."""

        return None if self.onset is None else round(self.onset - self.window.start, 3)

    def text(self) -> str:
        if self.onset is None:
            return (
                f"{self.line_id} kept its window {self.window.start:.2f}s ({self.how})"
            )
        return (
            f"{self.line_id} voice at {self.onset:.2f}s, its window {self.window.start:.2f}s "
            f"({self.delta:+.2f}s)"
        )


def _quiet_level(levels: Sequence[float], first: int, last: int) -> float | None:
    """The quiet before a line: the lower quartile of its stretch (digital silence counts as quiet).

    The stretch reaches :data:`ONSET_QUIET_SECONDS` back from the window (never
    into the previous line), so it holds the gap before the voice even when the
    voice runs up to :data:`ONSET_EARLY_SECONDS` ahead of its window.
    """

    stretch = list(levels[max(0, first) : max(0, last)])
    if len(stretch) * ONSET_WINDOW_SECONDS < ONSET_GAP_SECONDS:
        return None
    return (
        float(statistics.quantiles(stretch, n=4)[0]) if len(stretch) > 1 else stretch[0]
    )


def voice_starts(
    take: Path,
    lines: Sequence[Any],
    *,
    measure: Meter | None = None,
) -> list[VoiceStart]:
    """Where each locked-voice line's voice starts on the take, measured on its own sound.

    For each line (its window ``start``-``end`` on the dialogue track) the
    first 0.1 s from :data:`ONSET_EARLY_SECONDS` before the window (never
    before the previous line's window ends) to the window's middle that is
    louder than both the window's loudest minus :data:`ONSET_BELOW_PEAK_DB`
    and the quiet before the line plus :data:`ONSET_OVER_GAP_DB`. The track
    can carry the server's ambience and music under the voices, so the
    threshold is read against the line's own surroundings, never a fixed level.

    Parameters
    ----------
    take
        The take as filmed (its sound is the dialogue track).
    lines
        The soundtrack's lines (``line_id``, ``start``, ``end``), earliest first.
    measure
        Level meter (``creation.post.media.measure_rms_windows``); tests pass one.

    Returns
    -------
    list[VoiceStart]
        One per line, in order.
    """

    from creation.post.media import measure_rms_windows

    w = ONSET_WINDOW_SECONDS
    levels = list((measure or measure_rms_windows)(take, window_seconds=w))
    found: list[VoiceStart] = []
    for index, line in enumerate(lines):
        window = Span(float(line.start), float(line.end))
        line_id = str(getattr(line, "line_id", "") or f"line {index + 1}")
        before = float(lines[index - 1].end) if index else 0.0
        search_from = max(before, window.start - ONSET_EARLY_SECONDS, 0.0)
        first, middle = (
            int(round(search_from / w)),
            int(round((window.start + window.duration / 2) / w)),
        )
        inside = levels[int(round(window.start / w)) : int(round(window.end / w))]
        if not inside or middle <= first:
            found.append(VoiceStart(line_id, window, None, "not measured"))
            continue
        quiet_from = max(before, window.start - ONSET_QUIET_SECONDS, 0.0)
        quiet = _quiet_level(
            levels, int(round(quiet_from / w)), int(round(window.start / w))
        )
        threshold = max(inside) - ONSET_BELOW_PEAK_DB
        if quiet is not None:
            threshold = max(threshold, quiet + ONSET_OVER_GAP_DB)
        onset = None
        for i in range(first, min(middle, len(levels))):
            run = levels[i : i + ONSET_MIN_WINDOWS]
            if len(run) == ONSET_MIN_WINDOWS and all(lv > threshold for lv in run):
                onset = round(i * w, 3)
                break
        if onset is None:
            found.append(VoiceStart(line_id, window, None, "no voice measured near it"))
            continue
        found.append(
            VoiceStart(line_id, window, onset, "measured on the dialogue track")
        )
    return found


def moved_spans(
    spans: Mapping[str, Span], starts: Sequence[VoiceStart]
) -> dict[str, Span]:
    """``spans`` (the windows ``caption_take`` times the lines on) moved onto each line's measured voice.

    A span keeps its own end (a letterbox line already ends with its voice)
    unless the voice ran ahead of its window, when the end moves earlier by as
    much; it never ends sooner than :data:`MIN_CAPTION_SECONDS` after it starts.
    """

    by_id = {s.line_id: s for s in starts}
    out: dict[str, Span] = {}
    for line_id, span in spans.items():
        start = by_id.get(line_id)
        if start is None or start.onset is None:
            out[line_id] = span
            continue
        shift = min(0.0, start.onset - start.window.start)
        out[line_id] = Span(
            start.onset,
            round(max(span.end + shift, start.onset + MIN_CAPTION_SECONDS), 3),
        )
    return out


def voice_floors(starts: Sequence[VoiceStart]) -> dict[str, float]:
    """``line_id`` to where its voice starts: no caption of that line goes up before it."""

    return {s.line_id: s.onset for s in starts if s.onset is not None}


def summary(starts: Sequence[VoiceStart]) -> str:
    """One line for the run notes: how far the voices sit from their windows."""

    measured = [s.delta for s in starts if s.delta is not None]
    if not measured:
        return "voice starts not measured (captions keep the take facts' line windows)"
    return (
        f"captions start with the voice (measured on the take; voice vs window "
        f"{min(measured):+.2f}..{max(measured):+.2f}s): "
        + "; ".join(s.text() for s in starts)
    )


__all__ = [
    "MIN_CAPTION_SECONDS",
    "ONSET_EARLY_SECONDS",
    "VoiceStart",
    "moved_spans",
    "summary",
    "voice_floors",
    "voice_starts",
]
