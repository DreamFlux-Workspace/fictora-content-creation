"""What a take's soundtrack is, from its take facts: the model's own sound, or the show's locked voices.

On the H3 Turbo lane a newer server can render a take's lines in the series'
locked voices and send them to the video model as the take's audio
(``soundtrack.mode == "target_audio"``). The model animates the mouths to that
track, and the take comes back with EXACTLY that dialogue track as its sound:
the locked voices, digital silence between the lines, no ambience, no foley,
no music. So on such a take the after-filming sound is not a fix-up, it is the
whole room:

- the voices are already right: ``revoice``, ``finish --mute`` and
  ``finish --voice`` would only damage them, and need ``--over-locked-voices``;
- ``finish`` lays the location's ambience under the whole take
  (:mod:`creation.post.ambience`), or room tone (:func:`lay_room_tone`) when no
  ambience cue can be made, so the gaps between lines are never digital silence;
- the bed is ducked exactly inside each line's window (:func:`line_windows`);
- the planned effects follow the cuts measured on the filmed take, with a wider
  snap window (:data:`TARGET_AUDIO_CUT_WINDOW_SECONDS`): cuts can land more than
  1 s off the plan (learning L-20261001-10);
- captions are timed on the line windows themselves: no transcript is needed.

Take facts from an older server have no ``soundtrack`` key: :func:`soundtrack_from`
reads them as ``native`` and every command behaves as before.

Server contract (``GET /v1/jobs/{id}/take-facts``)::

    soundtrack: {"mode": "target_audio" | "native", "reason": str | null,
                 "track_url": str | null,
                 "lines": [{"line_id", "cast_id", "start_s", "end_s", "off_screen"}],
                 "native_foley": bool}
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from creation.post.media import measure_rms_windows, probe_video, run_ffmpeg

#: The take's sound is the locked-voice dialogue track the server sent to the video model.
TARGET_AUDIO = "target_audio"
#: The take's sound is the video model's own (voices, ambience, foley).
NATIVE = "native"

#: How far the bed drops inside each line window on a locked-voice take (``--duck-db`` overrides it).
TARGET_AUDIO_DUCK_DB = 12.0
#: How far a planned shot change may move to a measured cut on a locked-voice take (native takes keep 1 s).
TARGET_AUDIO_CUT_WINDOW_SECONDS = 2.0
#: Room tone level (RMS, dBFS) under a locked-voice take: heard as air in the gaps, never over a line.
ROOM_TONE_DB = -52.0
#: Brown noise at this amplitude, band-limited 60 Hz-1.5 kHz, reads about -49.5 dB RMS.
_ROOM_TONE_AMPLITUDE = 0.02
_ROOM_TONE_RAW_DB = -49.5
#: A line window whose loudest 0.1 s reads under this has no voice in it.
LINE_HEARD_DB = -40.0
#: The flag that lets an operator change the voices on a locked-voice take anyway.
OVER_LOCKED_VOICES_FLAG = "--over-locked-voices"


@dataclass(frozen=True)
class SoundLine:
    """One line on the take's dialogue track, in take seconds."""

    line_id: str
    cast_id: str
    start: float
    end: float
    off_screen: bool = False


@dataclass(frozen=True)
class Soundtrack:
    """A take's soundtrack as its take facts describe it.

    Parameters
    ----------
    mode
        :data:`TARGET_AUDIO` or :data:`NATIVE`.
    reason
        Why the server chose the mode (``None`` when it gave none).
    lines
        The dialogue track's lines (empty on a native take).
    native_foley
        Whether the returned sound carries the model's own ambience and foley.
    sent
        False when the facts had no usable ``soundtrack`` (an older server): read as native.
    track_url
        The dialogue track the server sent to the video model.
    """

    mode: str = NATIVE
    reason: str | None = None
    lines: tuple[SoundLine, ...] = ()
    native_foley: bool = True
    sent: bool = False
    track_url: str | None = None

    @property
    def target_audio(self) -> bool:
        """True when the take's sound is the locked-voice dialogue track."""

        return self.mode == TARGET_AUDIO

    def one_line(self) -> str:
        """``Soundtrack: locked voices (...)`` or ``Soundtrack: native (reason: ...)``."""

        if self.target_audio:
            return (
                f"Soundtrack: locked voices, {len(self.lines)} line(s) (no native ambience); "
                "location ambience (room tone if none can be made), bed + effects will be laid"
            )
        if not self.sent:
            return "Soundtrack: native (the server sent no soundtrack: an older server)"
        return f"Soundtrack: native (reason: {self.reason or 'none given'})"


def _seconds(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def soundtrack_from(facts: Mapping[str, Any] | None) -> Soundtrack:
    """Read ``soundtrack`` from take facts; a missing or unreadable one is native (an older server).

    Parameters
    ----------
    facts
        A saved facts file (``{"take_facts": {...}}`` or the facts alone), or ``None``.

    Returns
    -------
    Soundtrack
        The mode and, on a locked-voice take, its line windows (each with ``end > start``), earliest first.
    """

    if not facts:
        return Soundtrack()
    body = facts.get("take_facts", facts)
    raw = body.get("soundtrack") if isinstance(body, Mapping) else None
    if not isinstance(raw, Mapping):
        return Soundtrack()
    mode = str(raw.get("mode") or "")
    if mode not in (TARGET_AUDIO, NATIVE):
        return Soundtrack(
            reason=f"unknown soundtrack mode {mode!r}, read as native", sent=True
        )
    lines: list[SoundLine] = []
    for item in raw.get("lines") or []:
        if not isinstance(item, Mapping):
            continue
        start, end = _seconds(item.get("start_s")), _seconds(item.get("end_s"))
        if start is None or end is None or end <= start or start < 0:
            continue
        lines.append(
            SoundLine(
                str(item.get("line_id") or ""),
                str(item.get("cast_id") or ""),
                round(start, 3),
                round(end, 3),
                bool(item.get("off_screen")),
            )
        )
    reason = raw.get("reason")
    return Soundtrack(
        mode=mode,
        reason=str(reason) if reason else None,
        lines=tuple(sorted(lines, key=lambda line: line.start))
        if mode == TARGET_AUDIO
        else (),
        native_foley=bool(raw.get("native_foley", mode == NATIVE)),
        sent=True,
        track_url=str(raw["track_url"]) if raw.get("track_url") else None,
    )


def saved_soundtrack(desk: Path, episode: int, take_id: str) -> Soundtrack:
    """The soundtrack of the newest saved take facts on the desk (native when none are saved)."""

    from creation.post.sfx import saved_take_facts

    path = saved_take_facts(desk, episode, take_id)
    if path is None:
        return Soundtrack()
    return soundtrack_from(json.loads(path.read_text(encoding="utf-8")))


def line_windows(soundtrack: Soundtrack) -> tuple[tuple[float, float], ...]:
    """``(start, end)`` of every line on the dialogue track, in take seconds."""

    return tuple((line.start, line.end) for line in soundtrack.lines)


def soundtrack_lines(
    facts: Mapping[str, Any] | None, cast_names: Mapping[str, str] | None = None
) -> list[str]:
    """The soundtrack line for a take review, then one ``line_id (who) a-b s`` per line on a locked-voice take.

    Parameters
    ----------
    facts
        A saved facts file, or ``None``.
    cast_names
        ``cast_id`` to display name.

    Returns
    -------
    list[str]
        Printable lines.
    """

    soundtrack = soundtrack_from(facts)
    names = cast_names or {}
    rows = [soundtrack.one_line()]
    for line in soundtrack.lines:
        who = names.get(line.cast_id, line.cast_id) or "?"
        where = ", off screen" if line.off_screen else ""
        rows.append(
            f"  {line.line_id or '?'} ({who}{where}) {line.start:.2f}-{line.end:.2f}s"
        )
    return rows


def locked_voice_refusal(soundtrack: Soundtrack, what: str) -> str:
    """The warning for a voice change asked on a locked-voice take (raise it unless the flag was given)."""

    return (
        f"!! {what} on a take whose sound is the show's locked voices (soundtrack: target_audio): "
        "the lines are already in the voices the cast cards held when it was filmed, and the mouths "
        "were animated to them. Changing them can break lip sync and is not needed unless the human "
        f"picked a new voice after this take was filmed. Pass {OVER_LOCKED_VOICES_FLAG} only then, "
        "or when the human asked for it on this take."
    )


def lay_room_tone(
    take: Path, out: Path, *, level_db: float = ROOM_TONE_DB, seed: int = 204
) -> Path:
    """Lay quiet, band-limited room tone under the whole take (its picture copied), so no gap is digital silence.

    Parameters
    ----------
    take
        The take (its own sound kept as it is).
    out
        New file; must not exist.
    level_db
        Room tone RMS in dBFS.
    seed
        Noise seed (the same take gets the same air on every run).

    Returns
    -------
    Path
        ``out``.

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    ValueError
        When the take has no audio.
    """

    if out.exists():
        raise FileExistsError(f"{out} exists; local post never overwrites")
    info = probe_video(take)
    if not info.has_audio:
        raise ValueError(f"{take.name} has no audio to lay room tone under")
    total = info.duration_seconds
    gain = level_db - _ROOM_TONE_RAW_DB
    graph = (
        f"anoisesrc=color=brown:amplitude={_ROOM_TONE_AMPLITUDE}:seed={seed}:sample_rate=48000:duration={total:.3f},"
        f"highpass=f=60,lowpass=f=1500,volume={gain:+.1f}dB[air];"
        "[0:a]aresample=48000[take];"
        "[take][air]amix=inputs=2:duration=first:normalize=0[a]"
    )
    run_ffmpeg(
        ["-i", str(take), "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)]
    )  # fmt: skip
    return out


Meter = Callable[..., tuple[float, ...]]


def unheard_lines(
    take: Path, soundtrack: Soundtrack, *, measure: Meter = measure_rms_windows
) -> list[str]:
    """Lines whose window on the take holds no voice (loudest 0.1 s under :data:`LINE_HEARD_DB`).

    A free, local check that the dialogue track came back where the facts say it
    is; it reads levels, not words.

    Returns
    -------
    list[str]
        One ``line_id a-b s: no voice in its window (loudest N dB)`` per silent line.
    """

    if not soundtrack.lines:
        return []
    window = 0.1
    levels = measure(take, window_seconds=window)
    silent: list[str] = []
    for line in soundtrack.lines:
        first, last = (
            int(line.start / window),
            max(int(line.start / window), int(line.end / window) - 1),
        )
        inside = levels[first : last + 1]
        loudest = max(inside, default=-120.0)
        if loudest < LINE_HEARD_DB:
            silent.append(
                f"{line.line_id or '?'} {line.start:.2f}-{line.end:.2f}s: no voice in its window "
                f"(loudest {loudest:.0f} dB)"
            )
    return silent


def heard_summary(
    lines: Sequence[SoundLine],
    *,
    silent: Sequence[str],
    problems: Sequence[str],
    words: Path | None,
    skipped: str,
) -> str:
    """One count of the lines heard, never two that disagree.

    A line counts as heard only when its window holds voice (:func:`unheard_lines`) and,
    when a transcript of this take was read, the transcript heard it there
    (:func:`misplaced_lines`). Without a transcript of this take the count says it is
    levels only and why the transcript check was skipped.

    Parameters
    ----------
    lines
        The dialogue track's lines.
    silent, problems
        :func:`unheard_lines` and :func:`misplaced_lines` rows (each starts with the line id).
    words
        The transcript read, or ``None``.
    skipped
        Why no transcript was read (with ``words`` ``None``).

    Returns
    -------
    str
        The summary (the ``!!`` rows are printed by the caller).
    """

    def ids(rows: Sequence[str]) -> set[str]:
        return {row.split(":", 1)[0].split(" ", 1)[0] for row in rows}

    bad = ids(silent) | ids(problems)
    good = sum(1 for line in lines if (line.line_id or "?") not in bad)
    if words is None:
        return (
            f"{good} of {len(lines)} line(s) have voice in their window (levels only); "
            f"transcript check skipped: {skipped}"
        )
    return (
        f"{good} of {len(lines)} line(s) heard in their window "
        f"(voice level, and transcript `{words.name}` of this take)"
    )


def misplaced_lines(
    soundtrack: Soundtrack,
    words_json: Path,
    texts: Mapping[str, str],
    *,
    slack: float = 0.5,
) -> list[str]:
    """Lines a saved transcript of the take heard outside their window (or not at all).

    Parameters
    ----------
    soundtrack
        The take's soundtrack.
    words_json
        A saved transcript of this take (``review --transcribe``); never made here.
    texts
        ``line_id`` to the words heard (``spoken_text`` or ``text``).
    slack
        Seconds a heard line may start outside its window.

    Returns
    -------
    list[str]
        One line per problem; empty when every line with a text is heard in its window.
    """

    from creation.post.whisper import line_windows as heard_windows
    from creation.post.whisper import load_words

    lines = [line for line in soundtrack.lines if texts.get(line.line_id)]
    if not lines:
        return []
    heard = heard_windows(
        load_words(words_json), tuple(texts[line.line_id] for line in lines)
    )
    problems: list[str] = []
    for line, found in zip(lines, heard, strict=True):
        if found.start is None:
            problems.append(f"{line.line_id}: not heard in `{words_json.name}`")
        elif not line.start - slack <= found.start <= line.end:
            problems.append(
                f"{line.line_id}: heard at {found.start:.2f}s, its window is {line.start:.2f}-{line.end:.2f}s"
            )
    return problems


__all__ = [
    "LINE_HEARD_DB",
    "NATIVE",
    "OVER_LOCKED_VOICES_FLAG",
    "ROOM_TONE_DB",
    "TARGET_AUDIO",
    "TARGET_AUDIO_CUT_WINDOW_SECONDS",
    "TARGET_AUDIO_DUCK_DB",
    "SoundLine",
    "Soundtrack",
    "heard_summary",
    "lay_room_tone",
    "line_windows",
    "locked_voice_refusal",
    "misplaced_lines",
    "saved_soundtrack",
    "soundtrack_from",
    "soundtrack_lines",
    "unheard_lines",
]
