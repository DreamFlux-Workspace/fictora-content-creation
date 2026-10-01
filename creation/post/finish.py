"""Finish one accepted take in one command: deboard, SFX, music bed, colour match, mix, captions, mark.

Hosted post-production is off on the Drama API: the raw take has the model's
sound only. ``fictora-produce finish`` finishes it on this laptop:

0. ``deboard``   - replace the storyboard frames the take opens on (Turbo's
   start image) with the first real frame; measured against the approved
   board, capped at 12; nothing written when there are none. The length,
   frame count and sound are unchanged, so every later step (take-facts cue
   times, caption timing, the voice the mix ducks under) stays on the raw
   take's timeline. ``--no-deboard`` skips it.
1. ``sfx``       - the take's cue plan from ``GET /v1/jobs/{take_job}/take-facts?spine_id=``
   (fetched and saved as ``api/take-facts-epNN-tK-vN.json`` when missing),
   rendered on the server (the audio service) and cached in ``epNN/sfx/``.
   The plan already carries the story's drop and level sound notes: each cue
   is laid at -8 dB plus its ``gain_offset_db``, and a cue in
   ``sfx_dropped_cues`` is never laid. The cues then follow the FILMED cuts
   (the server's mix does the same since fictora-drama #487): each planned
   shot change moves to the nearest hard cut measured on the take (tblend
   trace, within 1 s, the review's detector; measured on the raw take when the
   finished file keeps its picture timeline), and each cue keeps its fraction
   of its shot. ``--sfx-adjust`` moves cues on top. Every planned cue that
   is not laid is named with its reason (render failed, wrong shape, starts
   past the take: ``!! NOT LAID`` on the step and on the sound line; left out
   by ``--sfx-adjust`` or a sound note: listed). None is dropped silently.
2. ``bed``       - the show's music bed, always the harness's (the harness bed
   pinned on the desk, else the spine's pinned bed, else made once on the
   server and pinned on the desk; a file pinned by hand is ignored:
   :mod:`creation.post.bed`), and its level in the mix: ``--bed-db``, else the
   desk's ``series.json`` ``bed_db``, else measured from the bed file so the
   bed lands at about -27 LUFS, about 9 dB under the dialogue
   (:func:`creation.post.bed.bed_level`). The step prints the level and why,
   and a ``!!`` line when it puts the bed outside the band. When the take
   facts say the harness's music is already in the take's soundtrack
   (``soundtrack.music.laid``) no bed is laid: the step says so and the take
   counts as having music. ``--music "…"`` never makes or picks music: it is
   saved as a music change note for the harness (:func:`creation.post.bed.record_music_note`),
   and every finish prints the notes waiting for the next re-run.
3. ``colour``    - match the take to the board the human approved.
4. ``mix``       - bed under the take, ducked under the voice, gain measured
   so the mix lands near -18 LUFS; ``--duck-db N`` for an exact duck depth.
   The bed before and after ducking and the duck key are saved beside the mix
   (``take-epNN-tK-mix-vN-{raw,ducked,key}-bus.wav``): ``review`` measures
   the duck depth from them.
5. ``captions``  - house captions (English), timed on the take before the bed.
   On a show spoken in another language (whole English lines) each line is
   timed on the words a transcript of the take heard for it: the saved
   ``take-epNN-tK-*words-vN.json`` (``review --transcribe``), else one made on
   the server from the take's stored URL (``/v1/transcripts``, a few cents).
   A ``--take-file`` made from the newest raw take only by edits that keep
   its sound timeline (``freeze``, ``soften``, ``blur``, ``deboard``, ``colour``; followed
   through ``epNN/takes/edit-chain.jsonl`` or, on older desks, the run notes:
   :mod:`creation.post.lineage`) uses the raw take's transcript too. A line
   with no match, or a take whose sound was changed (``--voice``/``--mute``,
   or a ``--take-file`` after ``trim``/``tempo``/a sound step, or of unknown
   origin), is timed on speech spans. ``--line-start``/``--line-end`` set either end by
   hand. The report names what timed each line.
6. ``watermark`` - the Sokii mark top left, under the covered top strip.
7. ``thumbnail`` - the episode cover as the MP4 attached picture on the marked
   file (platform cover art). A cover already on the desk for this clip
   (``take-epNN-tK-thumb-vN.jpg``) is re-embedded, free. Drawing a new one
   (``POST …/episodes/{n}/thumbnail`` from the take's stored clip URL) is a
   paid still: only with ``--thumbnail`` after the human's yes; the price is
   printed first and booked as ``thumbnail``. Without it, finish says what a
   cover would cost and spends nothing.

A locked-voice take (take facts ``soundtrack.mode == "target_audio"``: the
server sent the lines in the locked voices to the video model as its audio, and
the take's sound is exactly that dialogue track, digital silence between lines)
finishes by default with the sound it lacks (:mod:`creation.post.soundtrack`):
``ambience`` after the effects (the location's own sound, made once per episode
from the take's sound plan and laid at about -28 dB between the lines, ducked
under each line: :mod:`creation.post.ambience`; skipped when the server already
laid it under the voices, ``soundtrack.ambience.laid``), then ``room-tone`` (room tone
only when no ambience could be made, and a free check that each line is heard
in its window), the bed ducked ``TARGET_AUDIO_DUCK_DB`` exactly in each line window,
the effects snapped to cuts measured up to 2 s from the plan (each measured cut
printed) and ducked under the line windows, captions timed on the line windows
(no transcript). Its voices are never muted or replaced without
``--over-locked-voices``. With no bed or no room tone the chain STOPS before
the mix (``!! STOPPED``, exit 5): a voice-only take is never made deliverable.
Facts without ``soundtrack`` (an older server) and native takes finish as before.

Every step writes a new versioned file (``take-epNN-tK-<step>-vN.mp4``); nothing
is overwritten. A failing step is reported and skipped and the chain carries
on from the last good file. But a take is not done until music, SFX and the
mix are on it: when any is missing, ``finish`` prints ``NOT DONE``, writes a
run note and the CLI exits ``5``. The last line is always
``Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`` (✗ where not).

Every run ends by writing ``take-epNN-tK-finish-vN.json``
(:mod:`creation.post.finish_record`): the file the mix read (the take before
the bed) and the un-marked file the mark went on. ``join`` reads it to lay one
bed across several takes and mark the joined file once.

Hand-placed sound (:mod:`creation.post.hand`), each flag repeatable, times on
the take as filmed (deboard keeps the timeline, so nothing is shifted):

- ``--mute A-B`` and ``--voice PATH@S[@DB]`` run as the ``voice`` step right
  after deboard: stray speech silenced, dry lines laid into the take's own
  audio, so the mix ducks the bed under them and captions are timed on them.
- ``--cue PATH@S[@DB]`` runs as the ``cues`` step right after ``sfx``.

Every hand file is checked before any step runs (missing, silent, outside the
take: an error, nothing written). A requested hand step that then fails makes
the take ``NOT DONE`` like missing music.

Inner voice (a character's own thoughts, ``episode_summaries[].inner_voice`` on
the saved spine, saved with ``inner-voice``) runs as the ``inner-voice`` step
right after the hand voice step, on every take that has a cue:

- **Which take.** Cue times count from the start of the episode as filmed. Take
  ``tN`` starts at the sum of the raw lengths of ``t1`` .. ``tN-1`` (ffprobe on
  each newest raw take); a cue belongs to the take its start falls in
  (:func:`creation.inner_voice.take_cues`). One that runs past the seam stays on
  the take where it starts and is flagged ``!!``.
- **The dry line** is made on the server in the thinker's locked voice
  (``voice-lines``, the route ``voice-line`` uses; same key, same ledger unit).
  A line already on the desk for the same words and voice is reused: a re-run
  never pays twice.
- **Laid** like ``--voice``: into the take's own audio at the cue's start,
  levelled to -18 LUFS, so the bed ducks under it; the SFX and hand cues duck
  under it like speech. Script captions stay timed on the take without it.
- **Captioned** in Georgia italic (heard, not seen) where it plays.

A cue that cannot go on (no locked voice, a refusal from the route, a line that
runs past the take, an earlier take not on the desk, a start after the last
filmed take) is named: the rest of the take still finishes and it is ``NOT
DONE``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TextIO

import httpx

from creation import inner_voice as thoughts
from creation.captions import (
    CaptionLine,
    Span,
    SpineFetcher,
    caption_take,
    captions_whole_lines,
    current_spine,
    is_english,
    resolve_caption_style,
)
from creation.harness.raw_video import fetch_take_facts
from creation.ops.floor import record_spend
from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.post.ambience import (
    AMBIENCE_DUCK_DB,
    AMBIENCE_GAP_DB,
    ambience_brief,
    episode_ambience,
    episode_offset,
    lay_ambience,
)
from creation.post.ambience import Maker as AmbienceMaker
from creation.post.ambience import service_maker as ambience_service_maker
from creation.post.audio_service import AudioService, AudioServiceError, DramaApiAudio
from creation.post.bed import (
    DEFAULT_BED_DB,
    Maker,
    bed_level,
    music_notes,
    record_music_note,
    resolve_bed,
    service_music_maker,
)
from creation.post.colour import colour_match
from creation.post.deboard import deboard as deboard_take
from creation.post.edit import measure_cuts
from creation.post.finish_record import write_finish_record
from creation.post.hand import HandPlan, Placed, check_hand_plan, lay_cues, lay_voice
from creation.post.lineage import CHAIN_FILE, raw_take_behind, record_edit
from creation.post.desk import (
    approved_board,
    latest_raw_take,
    open_api,
    saved_spine,
    spine_id,
    take_job_id,
)
from creation.post.media import MediaToolError, measure_loudness, probe_video
from creation.post.mix import CueLevel, check_duck_db, mix_take, pick_gain
from creation.post.take_facts import save_take_facts, stale_facts_reason
from creation.post.take_timeline import align_take_facts
from creation.post.soundtrack import (
    TARGET_AUDIO_CUT_WINDOW_SECONDS,
    TARGET_AUDIO_DUCK_DB,
    Soundtrack,
    heard_summary,
    lay_room_tone,
    line_windows,
    locked_voice_refusal,
    misplaced_lines,
    soundtrack_from,
    unheard_lines,
)
from creation.post.voice_fx import PRESETS as VOICE_FX_PRESETS
from creation.post.sfx import (
    Adjustment,
    Cuts,
    Renderer,
    duplicate_cue_warnings,
    filmed_shot_windows,
    follow_filmed_cuts,
    lay_sfx,
    SfxPlan,
    apply_adjustments,
    plan_from_take_facts,
    planned_shots,
    saved_take_facts,
    service_renderer,
)
from creation.post.take_text import OcrRunner, TextCheck, desk_text_check
from creation.post.thumbnail import THUMBNAIL_USD, attach_episode_thumbnail_to_finish
from creation.post.watermark import watermark

#: The finish step that lays and captions the episode's inner-voice cues on this take.
INNER_VOICE_STEP = "inner-voice"

#: The finish step that lays room tone under a locked-voice take (soundtrack ``target_audio``).
ROOM_TONE_STEP = "room-tone"

#: The finish step that lays the location's ambience under a locked-voice take (before ``room-tone``).
AMBIENCE_STEP = "ambience"

#: ``finish`` exit code: files were written but music, SFX or the mix did not go on.
FINISH_INCOMPLETE = 5

STEP_ERRORS: tuple[type[BaseException], ...] = (
    MediaToolError,
    FileNotFoundError,
    FileExistsError,
    ValueError,
    RuntimeError,
    OSError,
    KeyError,
    SystemExit,
)

FactsFetcher = Callable[[Path, int, str], Path | None]
Transcriber = Callable[[Path, int, str], Path]
"""``(desk, episode, take_id) -> saved words JSON`` (:func:`creation.post.review.server_transcript`)."""


@dataclass
class StepReport:
    """What one step did: ``ran``, ``skipped`` or ``failed``."""

    step: str
    status: str
    detail: str
    output: Path | None = None
    cost_usd: float = 0.0
    #: Planned pieces the step did not lay, each ``what (why)`` (the sfx step's cues).
    not_laid: tuple[str, ...] = ()


@dataclass
class FinishResult:
    """The finished take and every step's report."""

    source: Path
    final: Path
    steps: list[StepReport] = field(default_factory=list)
    loudness: str = ""
    hand_steps: tuple[str, ...] = ()
    #: Loud lines from the drawn-text check on the raw take (empty when it found none).
    text_warnings: list[str] = field(default_factory=list)
    #: ``Soundtrack: …`` (:meth:`creation.post.soundtrack.Soundtrack.one_line`).
    soundtrack: str = ""
    #: The take's sound is the show's locked voices: room tone is required too.
    locked_voices: bool = False
    #: Why the chain stopped before a deliverable (a locked-voice take with no bed or room tone).
    stopped: str = ""
    #: The harness's music is already in the take's soundtrack (take facts ``soundtrack.music.laid``): no bed.
    music_in_take: bool = False
    #: The server laid the location's ambience under the voices (``soundtrack.ambience.laid``).
    ambience_in_take: bool = False
    #: Music change notes saved on the desk for the harness (they go with the next re-run).
    music_notes: tuple[str, ...] = ()

    def _ran(self, name: str) -> bool:
        return any(step.step == name and step.status == "ran" for step in self.steps)

    @property
    def cost_usd(self) -> float:
        """Metered spend across the chain."""

        return round(sum(step.cost_usd for step in self.steps), 4)

    @property
    def sound_missing(self) -> tuple[str, ...]:
        """What a deliverable take needs and did not get: ``music``, ``SFX``, ``mix``."""

        missing: list[str] = []
        mix = next((step for step in self.steps if step.step == "mix"), None)
        if not self.music_in_take and (
            not self._ran("bed") or (mix is not None and "NO MUSIC BED" in mix.detail)
        ):
            missing.append("music")
        if not self._ran("sfx"):
            missing.append("SFX")
        if not self._ran("mix"):
            missing.append("mix")
        if self.locked_voices and not self._ran(ROOM_TONE_STEP):
            missing.append("room tone")
        missing += [
            name
            for name in self.hand_steps
            if not self._ran(name)
            or (name == INNER_VOICE_STEP and self.inner_voice_not_laid)
        ]
        return tuple(missing)

    @property
    def complete(self) -> bool:
        """True when music, SFX and the mix all went on."""

        return not self.sound_missing

    @property
    def cues_not_laid(self) -> tuple[str, ...]:
        """Planned effects that went wrong and are not on the take, each ``sound (reason)``."""

        return tuple(
            cue for step in self.steps if step.step == "sfx" for cue in step.not_laid
        )

    @property
    def inner_voice_not_laid(self) -> tuple[str, ...]:
        """Inner-voice cues of this take that are not on it, each ``cue_id ... (why)``."""

        return tuple(
            cue
            for step in self.steps
            if step.step == INNER_VOICE_STEP
            for cue in step.not_laid
        )

    def sound_line(self) -> str:
        """``Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`` (✗ for what did not go on)."""

        missing = set(self.sound_missing)
        marks = [
            f"{part} {'✗' if part in missing else '✓'}"
            for part in ("music", "SFX", "mix")
        ]
        if self.cues_not_laid and "SFX" not in missing:
            marks[1] += f" (!! {len(self.cues_not_laid)} planned cue(s) not laid)"
        if self.music_in_take and "music" not in missing:
            marks[0] += " (in the take, from the harness)"
        if self.locked_voices and self.ambience_in_take:
            marks.append("ambience ✓ (laid by the server)")
        elif self.locked_voices and self._ran(AMBIENCE_STEP):
            marks.append("ambience ✓")
        elif self.locked_voices:
            marks.append(f"room tone {'✗' if 'room tone' in missing else '✓'}")
        captions = next((s for s in self.steps if s.step == "captions"), None)
        if captions is not None and captions.detail.startswith(CAPTIONS_OFF):
            marks.append("captions off (--caption-style none)")
        else:
            marks.append(f"captions {'✓' if self._ran('captions') else '✗'}")
        marks += [
            f"{'inner voice' if name == INNER_VOICE_STEP else f'hand {name}'} "
            f"{'✗' if name in missing else '✓'}"
            for name in self.hand_steps
        ]
        return "Sound: " + " · ".join(marks)

    def summary_lines(self) -> list[str]:
        """Final file, loudness, each step, then the sound line (cost stays in run notes only)."""

        lines = [f"Final: {self.final}", f"Loudness: {self.loudness or 'not measured'}"]
        if self.soundtrack:
            lines.append(self.soundtrack)
        if self.stopped:
            lines.append(f"!! STOPPED: {self.stopped}")
        lines += [
            f"- {step.step}: {step.status} — {step.detail}" for step in self.steps
        ]
        lines += self.text_warnings
        if self.music_notes:
            lines.append(
                "Music change notes for the harness (they go with the next re-run; nothing was changed here): "
                + "; ".join(self.music_notes)
            )
        lines.append(self.sound_line())
        return lines

    def as_json(self) -> dict[str, Any]:
        """JSON report (no cost: that stays in the run notes)."""

        return {
            "source": str(self.source),
            "final": str(self.final),
            "loudness": self.loudness,
            "soundtrack": self.soundtrack,
            "stopped": self.stopped or None,
            "complete": self.complete,
            "missing": list(self.sound_missing),
            "cues_not_laid": list(self.cues_not_laid),
            "inner_voice_not_laid": list(self.inner_voice_not_laid),
            "text_warnings": list(self.text_warnings),
            "music_notes": list(self.music_notes),
            "steps": [
                {"step": s.step, "status": s.status, "detail": s.detail,
                 "output": str(s.output) if s.output else None}
                for s in self.steps
            ],
        }  # fmt: skip


#: The captions step's detail when the caption style is ``none`` (nothing burned, on purpose).
CAPTIONS_OFF = "caption style none"

INCOMPLETE_FIX = (
    "Music: the bed step's error says why the harness bed could not be found or made (the spine is "
    "saved on the desk, the server's audio route answered); the kit never lays music you choose. "
    "SFX: finish needs the take's facts (GET /v1/jobs/{take_job}/take-facts; it fetches them when "
    "a clip record in api/ (17_raw_scene_clips.json or film-*-raw-scene-clips.json) names the take job) and the server's audio endpoints. "
    "Mix, or a hand voice / cues step you asked for: read that step's error above. "
    "Inner voice: read the cue the inner-voice step names (a voice to lock with `voice --audition` / "
    "`--pick`, a refusal to tell engineering about, or a cue to move with `inner-voice`)."
)


def book(
    desk: Path,
    *,
    episode: int,
    usd: float,
    take_id: str | None = None,
    stream: TextIO | None = None,
    unit: str | None = None,
) -> None:
    """Add local post spend to the desk ledger (``unit``: what it bought); a desk without that slot gets a printed note, not a crash."""

    try:
        record_spend(desk, episode=episode, usd=usd, take_id=take_id, unit=unit)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(
            f"note: a spend was not booked on the desk ledger ({exc}); see run-notes.md",
            file=stream or sys.stderr,
        )
        if (desk / f"ep{episode:02d}" / "run-notes.md").is_file():
            append_run_note(
                desk / f"ep{episode:02d}",
                f"Not booked on the ledger: ${usd:.3f} ({exc}); fictora-ops spend",
            )


#: Name parts of a take whose voice was treated after filming (``revoice``, ``voice-fx``).
TREATED_VOICE_MARKS = ("-revoice-", *(f"-{name}-v" for name in VOICE_FX_PRESETS))


def treated_voice(take: Path) -> bool:
    """True for a ``revoice`` or ``voice-fx`` output (or a file made from one)."""

    return any(mark in take.name for mark in TREATED_VOICE_MARKS)


def newest_versioned(directory: Path, stem: str) -> Path | None:
    """The highest ``stem-vN.json`` in ``directory``, or ``None``."""

    found: list[tuple[int, Path]] = []
    for path in directory.glob(f"{stem}-v*.json"):
        tail = path.stem.rsplit("-v", 1)[-1]
        if tail.isdigit():
            found.append((int(tail), path))
    return max(found)[1] if found else None


def voice_line_text(path: Path) -> str:
    """The words of a ``voice-line`` file (its ``.json``'s ``line``), or ``""`` when unknown."""

    meta = path.with_suffix(".json")
    if not meta.is_file():
        return ""
    return str(json.loads(meta.read_text(encoding="utf-8")).get("line") or "")


def with_hand_lines(words_json: Path, hand: HandPlan, out: Path) -> Path:
    """Write a copy of a transcript with the hand ``--voice`` lines in and the ``--mute`` windows out.

    Parameters
    ----------
    words_json
        The take's transcript (``{"words": [...]}`` or Whisper ``chunks``).
    hand
        The checked hand plan: each voice line's words (from the ``voice-line``
        file's ``.json``: its ``line``) are spread over where it is laid.
    out
        New file (``take-epNN-tK-cap-timing-vN.json``).

    Returns
    -------
    Path
        ``out``.
    """

    from creation.post.whisper import load_words

    muted = [*hand.mutes, *hand.voice_windows]
    kept = [
        {"word": w.text, "start": w.start, "end": w.end}
        for w in load_words(words_json)
        if not any(a <= (w.start + w.end) / 2 <= b for a, b in muted)
    ]
    for line, seconds in hand.voices:
        parts = voice_line_text(line.path).split()
        if not parts:
            continue
        step = seconds / len(parts)
        kept += [
            {
                "word": part,
                "start": line.start + i * step,
                "end": line.start + (i + 1) * step,
            }
            for i, part in enumerate(parts)
        ]
    kept.sort(key=lambda w: w["start"])
    out.write_text(json.dumps({"words": kept}, indent=2) + "\n", encoding="utf-8")
    return out


def cue_description(path: Path) -> str:
    """What a hand cue sounds like: its ``cue`` file's saved description, else its file name."""

    meta = path.with_suffix(".json")
    if meta.is_file():
        described = json.loads(meta.read_text(encoding="utf-8")).get("description")
        if described:
            return str(described)
    return path.stem.rsplit("-v", 1)[0].removeprefix("cue-").replace("-", " ")


def api_facts_fetcher(desk: Path, episode: int, take_id: str) -> Path | None:
    """Fetch and save the take's facts from the API, when the desk knows the take's job id."""

    job = take_job_id(desk, episode, take_id)
    if job is None:
        return None
    run = open_api(desk, episode)
    try:
        facts = fetch_take_facts(run, job, spine_id=spine_id(desk))
    finally:
        run.client.close()
    if facts is None:
        return None
    found = saved_spine(desk, episode)
    return save_take_facts(
        desk,
        episode=episode,
        take_id=take_id,
        facts=facts,
        spine=found[0] if found else None,
    )


def take_lengths(
    desk: Path, episode: int, take_id: str, *, source: Path
) -> tuple[list[float | None], bool]:
    """The raw length of takes ``t1`` .. this one, and whether this is the last take filmed.

    Each length is ffprobe on the take's newest raw file (the episode as filmed,
    the timeline inner-voice cues count on); ``None`` for an earlier take with no
    raw file on the desk. This take falls back to ``source`` when its raw file is
    gone.

    Returns
    -------
    tuple[list[float | None], bool]
        Lengths in take order, and True when no later take has a raw file.
    """

    number = thoughts.take_number(take_id)
    lengths: list[float | None] = []
    for index in range(1, number + 1):
        try:
            raw = latest_raw_take(desk, episode, f"t{index}")
        except FileNotFoundError:
            lengths.append(
                probe_video(source).duration_seconds if index == number else None
            )
            continue
        lengths.append(probe_video(raw).duration_seconds)
    try:
        latest_raw_take(desk, episode, f"t{number + 1}")
    except FileNotFoundError:
        return lengths, True
    return lengths, False


def take_inner_voice(
    spine: dict[str, Any] | None,
    desk: Path,
    *,
    episode: int,
    take_id: str,
    source: Path,
) -> thoughts.TakeCuePlan:
    """The episode's inner-voice cues that fall in this take (:func:`creation.inner_voice.take_cues`).

    Each carries the words its voice says when they were kept apart from the
    caption (``inner-voice --spoken-text``, :func:`creation.inner_voice.spoken_for`).
    """

    cues = thoughts.episode_cues(spine, episode=episode) if spine else []
    if not cues:
        return thoughts.TakeCuePlan()
    lengths, last = take_lengths(desk, episode, take_id, source=source)
    plan = thoughts.take_cues(
        cues, take=thoughts.take_number(take_id), lengths=lengths, last_filmed=last
    )
    spoken = thoughts.load_spoken(desk, episode)
    if not spoken:
        return plan
    return replace(
        plan,
        cues=tuple(
            replace(cue, spoken_text=thoughts.spoken_for(spoken, cue.cue_id, cue.line))
            for cue in plan.cues
        ),
    )


def run_finish(
    desk: Path,
    *,
    episode: int = 1,
    take_id: str = "t1",
    take_file: Path | None = None,
    deboard: bool = True,
    colour: bool = True,
    colour_strength: float = 1.0,
    bed_db: float | None = None,
    music: str | None = None,
    duck_db: float | None = None,
    sfx_adjust: tuple[Adjustment, ...] = (),
    line_starts: tuple[float, ...] | None = None,
    line_ends: tuple[float, ...] | None = None,
    watermark_y: int | None = None,
    mutes: tuple[tuple[float, float], ...] = (),
    voices: tuple[Placed, ...] = (),
    cues: tuple[Placed, ...] = (),
    sfx_render: Renderer | None = None,
    bed_maker: Maker | None = None,
    facts_fetcher: FactsFetcher = api_facts_fetcher,
    transcriber: Transcriber | None = None,
    cut_meter: Cuts | None = None,
    ambience_maker: AmbienceMaker | None = None,
    thumbnail: bool = True,
    draw_thumbnail: bool = False,
    voice_audio: AudioService | None = None,
    text_ocr: OcrRunner | None = None,
    over_locked_voices: bool = False,
    caption_style: str | None = None,
    spine_fetcher: SpineFetcher | None = None,
    stream: TextIO | None = None,
) -> FinishResult:
    """Run the whole local post chain on one accepted take.

    A take whose facts say its sound is the show's locked voices
    (``soundtrack.mode == "target_audio"``, :mod:`creation.post.soundtrack`)
    finishes the same way with four differences: the location's ambience is laid
    under the whole take (``ambience``, after the effects; one cue per episode,
    cached on the desk; room tone in ``room-tone`` only when no cue can be made),
    the bed ducks exactly
    ``TARGET_AUDIO_DUCK_DB`` inside each line window, the effects follow cuts
    measured up to 2 s off the plan (every measured cut is printed), and the
    captions are timed on the line windows. With no bed or no room tone the
    chain STOPS before the mix: a voice-only take is never made deliverable.
    The voices are never muted or replaced on such a take without
    ``over_locked_voices``. Facts with no ``soundtrack`` (an older server)
    finish exactly as before.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        ``t1``.
    take_file
        Take to finish (e.g. a ``revoice`` output); default the newest raw take.
    deboard
        Replace the board frames at the head first (``--no-deboard`` turns it off).
    colour
        Match the take to its approved board.
    colour_strength
        0..1.
    bed_db
        ``--bed-db``: the bed's level in the mix. ``None`` reads the desk's
        ``series.json`` ``bed_db``, else measures the bed and lands it about 9 dB
        under the dialogue (:func:`creation.post.bed.bed_level`).
    music
        ``--music``: a music change note for the harness ("calmer"). Saved on
        the desk (:func:`creation.post.bed.record_music_note`); no music is
        made or picked from it.
    duck_db
        Exact duck depth under the voice (1-30 dB); ``None`` uses the compressor.
    sfx_adjust
        Per-take SFX level changes.
    line_starts
        Manual caption line starts.
    line_ends
        Manual caption line ends (``--line-end``), one per line in order.
    watermark_y
        Mark top offset override (never into the top 8%).
    mutes
        ``--mute`` windows: stray speech silenced in the take's own audio (take seconds as filmed).
    voices
        ``--voice`` dry lines laid into the take's own audio (take seconds as filmed).
    cues
        ``--cue`` hand cues laid after the SFX step (take seconds as filmed).
    sfx_render, bed_maker, facts_fetcher, transcriber, cut_meter, voice_audio, ambience_maker
        Injected for tests (``bed_maker`` makes the harness bed on the server;
        ``transcriber`` makes a transcript of the take on the server;
        ``ambience_maker`` makes a locked-voice take's location ambience on the server;
        ``cut_meter`` measures the take's hard cuts, :func:`creation.post.edit.measure_cuts`;
        ``voice_audio`` makes the inner-voice dry lines, the Drama API by default;
        ``text_ocr`` reads frames for the drawn-text check, the ``tesseract`` command by default).
    thumbnail
        Put the episode cover on the deliverable (``--no-thumbnail`` turns it
        off). A cover already on the desk for this clip is re-embedded, free.
    draw_thumbnail
        ``--thumbnail``: when no cover is on the desk, draw one on the server
        (:data:`creation.post.thumbnail.THUMBNAIL_USD`, printed first). Off by
        default: finish never spends on a cover without the opt-in.
    over_locked_voices
        ``--over-locked-voices``: allow ``--mute``, ``--voice`` or a revoice /
        voice-fx file on a take whose sound is the locked voices (warned, not refused).
    caption_style
        ``--caption-style``: ``house`` (yellow flicker), ``plain`` (white whole
        lines) or ``none`` (no captions; the take is still complete). ``None``
        reads the desk's ``production.config.json`` (default ``house``).
    spine_fetcher
        Reads the current spine for the captions (default: the server, saved on
        the desk; :func:`creation.captions.current_spine`). The desk's copy is
        used, with a ``!!`` note, only when it cannot be read.
    stream
        Progress output (stderr by default).

    Returns
    -------
    FinishResult
        The final file and every step's report.

    Raises
    ------
    FileNotFoundError
        When there is no take to finish.
    ValueError
        When ``duck_db`` is out of range, or a mute, voice or cue cannot go on the
        take (outside it, silent), or the voices of a locked-voice take would be
        changed without ``over_locked_voices``; checked before any step runs.
    """

    out = stream or sys.stderr
    desk = desk.expanduser().resolve()
    check_duck_db(duck_db)
    if music is not None:
        # The operator says what should change; the harness picks the music on the next re-run.
        record_music_note(
            desk, music, episode=episode, take_id=take_id, via="finish --music"
        )
        print(
            f"[music] saved as a change note for the harness, nothing made here: {music}",
            file=out,
            flush=True,
        )
    style, style_note = resolve_caption_style(desk, caption_style)
    run_dir = desk / f"ep{episode:02d}"
    source = (
        take_file.expanduser().resolve()
        if take_file
        else latest_raw_take(desk, episode, take_id)
    )
    if not source.is_file():
        raise FileNotFoundError(f"take not found: {source}")
    hand = HandPlan()
    if mutes or voices or cues:
        # Take seconds as filmed: deboard replaces the board frames without cutting, so no shift is applied.
        hand = check_hand_plan(
            probe_video(source).duration_seconds, mutes=mutes, voices=voices, cues=cues
        )
    takes = run_dir / "takes"
    base = f"take-ep{episode:02d}-{take_id}"
    # The take facts, read (or fetched) once: they say whose voices the take's sound is.
    facts_state: dict[str, Any] = {
        "path": saved_take_facts(desk, episode, take_id),
        "error": None,
    }
    if facts_state["path"] is None:
        try:
            facts_state["path"] = facts_fetcher(desk, episode, take_id)
        except STEP_ERRORS as exc:
            facts_state["error"] = exc  # the sfx step reports it, as before
    timeline_note = ""
    if facts_state["path"] is not None:
        # The facts' times must be the clip's: the server holds board frames (no shift), but a take
        # stored while it cut them (fictora-drama #543) is measured against its track and re-timed.
        try:
            checked = align_take_facts(
                desk,
                episode=episode,
                take_id=take_id,
                take=source,
                facts_path=facts_state["path"],
            )
        except STEP_ERRORS as exc:
            timeline_note = f"!! take timeline not checked: {exc}"
        else:
            facts_state["path"] = checked.facts
            timeline_note = checked.note
    soundtrack = (
        soundtrack_from(json.loads(facts_state["path"].read_text(encoding="utf-8")))
        if facts_state["path"] is not None
        else Soundtrack()
    )
    locked = soundtrack.target_audio
    if locked:
        changes = [
            what
            for what, asked in (
                ("finish --mute", bool(mutes)),
                ("finish --voice", bool(voices)),
                (
                    f"finishing the revoice / voice-fx file `{source.name}`",
                    treated_voice(source),
                ),
            )
            if asked
        ]
        for what in changes:
            if not over_locked_voices:
                raise ValueError(locked_voice_refusal(soundtrack, what))
            print(
                locked_voice_refusal(soundtrack, what) + " (given: carrying on)",
                file=out,
            )
    found_spine = saved_spine(desk, episode)
    spine = found_spine[0] if found_spine else None
    board = approved_board(desk, episode, take_id)
    audio = DramaApiAudio(desk, episode=episode)
    sfx_render = sfx_render or service_renderer(audio, spine_id(desk))
    bed_maker = bed_maker or service_music_maker(audio)
    thought_plan = take_inner_voice(
        spine, desk, episode=episode, take_id=take_id, source=source
    )
    hand_steps = (
        (("voice",) if hand.mutes or hand.voices else ())
        + ((INNER_VOICE_STEP,) if thought_plan.any else ())
        + (("cues",) if hand.cues else ())
    )
    result = FinishResult(
        source=source,
        final=source,
        hand_steps=hand_steps,
        soundtrack=soundtrack.one_line() if facts_state["path"] is not None else "",
        locked_voices=locked,
        music_in_take=soundtrack.music_laid,
        ambience_in_take=locked and soundtrack.ambience_laid,
        music_notes=tuple(music_notes(desk, episode=episode, take_id=take_id)),
    )
    # A locked-voice take ducks exactly inside its line windows unless --duck-db says otherwise.
    mix_duck_db = duck_db if duck_db is not None or not locked else TARGET_AUDIO_DUCK_DB
    current = source
    bed_state: dict[str, Any] = {
        "path": None,
        "cues": (),
        "speech": None,
        # The bed's level in the mix (:func:`creation.post.bed.bed_level`), resolved once the bed is known.
        "db": bed_db if bed_db is not None else DEFAULT_BED_DB,
        "db_source": "flag" if bed_db is not None else "default",
    }
    voice_state: dict[str, Path | None] = {"path": None}
    # The inner-voice step's output (what the mix ducks under) and each laid thought: (cue, placed, seconds).
    thought_state: dict[str, Any] = {"path": None, "laid": []}
    # For the finish record `join` reads: what the mix read, and what the mark went on.
    record_state: dict[str, Path | None] = {"pre_bed": None, "master": None}
    print(
        f"Finishing {source.name}: board frames, sound effects, music, look, mix, captions, mark (2-4 minutes)",
        file=out,
    )
    if facts_state["path"] is not None:
        print(soundtrack.one_line(), file=out)
    if timeline_note:
        print(timeline_note, file=out)
        append_run_note(run_dir, f"Finish · {timeline_note}")
    append_run_note(
        run_dir,
        f"Finish chain on `{source.name}` (board: `{board.name if board else 'none'}`)"
        + (f"; {soundtrack.one_line()}" if locked else ""),
    )
    print("[text] Looking for words the video drew into the take", file=out, flush=True)
    try:
        text_check: TextCheck | None = desk_text_check(
            desk, episode, take_id, source, ocr=text_ocr
        )
        print(f"[text] {text_check.summary()}", file=out, flush=True)
    except STEP_ERRORS as exc:
        text_check = None
        result.text_warnings = [
            f"!! text check failed ({type(exc).__name__}: {str(exc)[:200]}): drawn subtitles were NOT "
            "looked for; watch the take for drawn text"
        ]
        print(f"[text] {result.text_warnings[0]}", file=out, flush=True)

    def step(name: str, doing: str, work: Callable[[Path], StepReport]) -> None:
        nonlocal current
        print(f"[{name}] {doing}", file=out, flush=True)
        try:
            report = work(current)
        except STEP_ERRORS as exc:
            report = StepReport(
                name, "failed", f"{type(exc).__name__}: {exc}".strip()[:300]
            )
            print(
                f"[{name}] Stopped: {report.detail}. Skipped; carrying on from the last good file",
                file=out,
            )
            append_run_note(
                run_dir, f"Finish · {name}: FAILED, skipped — {report.detail}"
            )
            result.steps.append(report)
            return
        print(f"[{name}] {report.status}: {report.detail}", file=out, flush=True)
        if report.output is not None:
            try:
                record_edit(desk, op=name, source=current, output=report.output)
            except OSError as exc:
                print(
                    f"[{name}] could not record the step in {CHAIN_FILE} ({exc}); a later finish of "
                    f"`{report.output.name}` will not know how it was made",
                    file=out,
                )
            current = report.output
        result.steps.append(report)

    def do_deboard(take: Path) -> StepReport:
        if not deboard:
            return StepReport("deboard", "skipped", "--no-deboard")
        if board is None:
            return StepReport(
                "deboard", "skipped", "no approved board on the desk to measure against"
            )
        trimmed = deboard_take(
            take, board, next_versioned_path(takes, f"{base}-deboard", ".mp4")
        )
        append_run_note(
            run_dir, f"Finish · deboard against `{board.name}`: {trimmed.one_line()}"
        )
        if trimmed.output is None:
            return StepReport(
                "deboard",
                "ran",
                f"no board frames ({trimmed.leak.one_line()}); nothing written",
            )
        return StepReport("deboard", "ran", trimmed.one_line(), trimmed.output)

    def do_voice(take: Path) -> StepReport:
        voiced = lay_voice(
            take, next_versioned_path(takes, f"{base}-voice", ".mp4"), hand
        )
        voice_state["path"] = voiced
        parts = [f"muted {a:.2f}-{b:.2f}s" for a, b in hand.mutes]
        parts += [
            f"voice {line.one_line()} ({seconds:.2f}s)" for line, seconds in hand.voices
        ]
        append_run_note(run_dir, f"Hand voice -> `{voiced.name}`: " + "; ".join(parts))
        return StepReport("voice", "ran", "; ".join(parts), voiced)

    def thought_windows() -> list[tuple[float, float]]:
        return [
            (placed.start, placed.start + seconds)
            for _cue, placed, seconds in thought_state["laid"]
        ]

    def laid_voice_windows() -> list[tuple[float, float]]:
        """Every dry line laid on the take (``--voice`` lines, inner-voice cues): ducked like a line."""

        return [
            *(hand.voice_windows if voice_state["path"] is not None else ()),
            *thought_windows(),
        ]

    def do_inner_voice(take: Path) -> StepReport:
        from creation.post.handmade import make_voice_line

        cards = {
            str(card.get("cast_id")): dict(card)
            for card in (spine or {}).get("cast") or []
            if isinstance(card, dict) and card.get("cast_id")
        }
        service = voice_audio or audio
        take_seconds = probe_video(take).duration_seconds
        not_laid = list(thought_plan.problems)
        flags = [f"!! {cue.seam}" for cue in thought_plan.cues if cue.seam]
        placed: list[tuple[thoughts.TakeCue, Placed]] = []
        paid = 0.0
        reused = 0
        for cue in thought_plan.cues:
            card = cards.get(cue.speaker_cast_id)
            who = str((card or {}).get("name") or cue.speaker_cast_id)
            if card is None:
                not_laid.append(
                    f"{cue.cue_id} ({who}): not in the cast on the saved spine"
                )
                continue
            if not cue.spoken_text and not is_english(cue.line):
                # Voiced as written, but its caption will be left off (NOT ENGLISH): say how to split them.
                flags.append(
                    f"!! {cue.cue_id}: the thought is not English, so it plays uncaptioned; add it again with "
                    '`inner-voice --text "<English caption>" --spoken-text "<the words said>"`'
                )
            try:
                made = make_voice_line(
                    desk, spine=spine or {}, card=card, text=cue.line, episode=episode,
                    spoken_text=cue.spoken_text or None, audio=service, out=out, take_id=take_id,
                    reuse=True, extra={"cue_id": cue.cue_id, "inner_voice": True},
                )  # fmt: skip
            except (
                ValueError,
                KeyError,
                AudioServiceError,
                httpx.HTTPError,
                OSError,
            ) as exc:
                not_laid.append(f"{cue.cue_id} ({who}): {exc}"[:300])
                continue
            paid += made.cost_usd
            reused += made.reused
            if made.reading.get("checked") and not made.reading.get("read_right"):
                flags.append(
                    f"!! {cue.cue_id}: the dry line may be misread, listen to `{made.path.name}`"
                )
            placed.append((cue, Placed(made.path, cue.start)))
        checked: list[tuple[thoughts.TakeCue, Placed, float]] = []
        for cue, line in placed:
            try:
                ((_line, seconds),) = check_hand_plan(
                    take_seconds, voices=(line,)
                ).voices
            except (ValueError, FileNotFoundError, MediaToolError) as exc:
                not_laid.append(f"{cue.cue_id}: {exc}"[:300])
                continue
            checked.append((cue, line, seconds))
        parts = [
            f"{cue.cue_id} {line.path.name} @{line.start:.2f}s ({seconds:.2f}s)"
            + (
                f", says {cue.spoken_text!r} under the caption {cue.line!r}"
                if cue.spoken_text
                else ""
            )
            for cue, line, seconds in checked
        ]
        tail = "".join(
            [
                *(f"; {flag}" for flag in flags),
                *(f"; !! NOT LAID {item}" for item in not_laid),
            ]
        )
        where = (
            f"t{thoughts.take_number(take_id)} starts at {thought_plan.offset:.2f}s on the episode"
            if thought_plan.offset is not None
            else "where this take starts is unknown"
        )
        made_note = f"; {reused} dry line(s) reused from the desk" if reused else ""
        if not checked:
            append_run_note(
                run_dir, f"Finish · inner voice ({where}): nothing laid{tail}"
            )
            return StepReport(
                INNER_VOICE_STEP, "ran", f"nothing laid ({where}){tail}", None, paid,
                not_laid=tuple(not_laid),
            )  # fmt: skip
        laid = lay_voice(
            take,
            next_versioned_path(takes, f"{base}-inner-voice", ".mp4"),
            HandPlan(voices=tuple((line, seconds) for _cue, line, seconds in checked)),
        )
        thought_state["path"] = laid
        thought_state["laid"] = checked
        append_run_note(
            run_dir,
            f"Inner voice ({where}) -> `{laid.name}`: {'; '.join(parts)}{made_note}{tail}, ${paid:.3f}",
        )
        return StepReport(
            INNER_VOICE_STEP, "ran", f"{'; '.join(parts)} ({where}){made_note}{tail}", laid, paid,
            not_laid=tuple(not_laid),
        )  # fmt: skip

    def do_cues(take: Path) -> StepReport:
        speech = list(hand.voice_windows) + thought_windows()
        facts = saved_take_facts(desk, episode, take_id)
        if bed_state["speech"] is not None:
            speech += bed_state[
                "speech"
            ]  # the sfx step's speaking windows, on the filmed shots
        elif facts is not None:
            speech += plan_from_take_facts(
                json.loads(facts.read_text(encoding="utf-8"))
            ).speech
        laid = lay_cues(
            take,
            next_versioned_path(takes, f"{base}-cues", ".mp4"),
            hand,
            speech=tuple(speech),
        )
        parts = [f"{cue.one_line()} for {seconds:.2f}s" for cue, seconds in hand.cues]
        clashes = duplicate_cue_warnings(
            tuple((cue_description(cue.path), cue.start) for cue, _ in hand.cues),
            tuple((c.sound, c.start) for c in bed_state["cues"]),
        )
        for warning in clashes:
            print(f"[cues] {warning}", file=out, flush=True)
        append_run_note(
            run_dir,
            f"Hand cues -> `{laid.name}`: "
            + "; ".join(parts)
            + "".join(f"\n- {w}" for w in clashes),
        )
        return StepReport("cues", "ran", "; ".join([*parts, *clashes]), laid)

    def on_filmed_cuts(
        plan: SfxPlan, payload: dict[str, Any], take: Path
    ) -> tuple[SfxPlan, str]:
        """Move the planned cues onto the shots as filmed (fictora-drama #487's placement, done here)."""

        shots = planned_shots(payload)
        if len(shots) < 2 and not locked:
            return plan, "cues as planned (the take facts plan one shot)"
        # Cuts are measured on the raw take when the finished file keeps its picture timeline: a freeze
        # hold ends in a jump that would read as a cut, and soften fades the real ones.
        lineage = raw_take_behind(desk, source)
        measured = (
            lineage.raw if lineage.raw is not None and lineage.keeps_timeline else take
        )
        try:
            cuts = (cut_meter or measure_cuts)(measured)
            duration = probe_video(take).duration_seconds
        except (RuntimeError, OSError, MediaToolError) as exc:
            return plan, f"cues on the planned shots (cuts not measured: {exc})"[:300]
        if locked:
            # A locked-voice take: cuts land up to ~1 s+ off the plan (L-20261001-10), so the snap is wider
            # and every measured cut is printed.
            filmed = filmed_shot_windows(
                shots, cuts, duration=duration,
                window=TARGET_AUDIO_CUT_WINDOW_SECONDS, show_measured=True,
            )  # fmt: skip
        else:
            filmed = filmed_shot_windows(shots, cuts, duration=duration)
        return follow_filmed_cuts(plan, filmed), (
            f"cues follow the filmed cuts measured on `{measured.name}`: {filmed.one_line()}"
        )

    def do_sfx(take: Path) -> StepReport:
        if facts_state["error"] is not None:
            raise facts_state["error"]
        facts = facts_state["path"]
        if facts is None:
            return StepReport(
                "sfx",
                "skipped",
                f"no take facts: no clip record in api/ (17_raw_scene_clips.json, film-*-raw-scene-clips.json) "
                f"names a job for {take_id} and no "
                f"api/take-facts-ep{episode:02d}-{take_id}-vN.json is saved",
            )
        payload = json.loads(facts.read_text(encoding="utf-8"))
        stale = stale_facts_reason(payload, spine, episode=episode, take_id=take_id)
        if stale:
            warning = (
                f"!! the saved take facts `{facts.name}` are older than the story's sound notes: {stale}. "
                f"This finish lays the old plan. Run `fictora-produce take-facts --desk {desk} --episode {episode} "
                f"--take {take_id} --refresh`, then finish again"
            )
            print(f"[sfx] {warning}", file=out, flush=True)
            append_run_note(run_dir, f"Finish · sfx: {warning}")
        plan, filmed_note = on_filmed_cuts(plan_from_take_facts(payload), payload, take)
        if locked:
            # The dialogue track is the take's sound: its line windows are exact and do not move with the cuts.
            plan = replace(plan, speech=line_windows(soundtrack))
            filmed_note += f"; effects duck under the {len(plan.speech)} line window(s) of the dialogue track"
        if laid_voice_windows():
            # The effects duck under a laid thought or --voice line like under any line.
            plan = replace(plan, speech=(*plan.speech, *laid_voice_windows()))
        bed_state["speech"] = plan.speech
        append_run_note(run_dir, f"Finish · sfx: {filmed_note}")
        dropped = (
            f"; dropped by sound notes: {', '.join(plan.dropped)}"
            if plan.dropped
            else ""
        )
        if not plan.cues:
            return StepReport(
                "sfx",
                "ran",
                f"the take facts plan no effect (every shot speaks, or a sound note dropped every cue); "
                f"`{facts.name}`{dropped}"
                + (f"; facts older than the sound notes ({stale})" if stale else ""),
            )
        if not apply_adjustments(plan.cues, sfx_adjust):
            # The operator dropped every planned cue: zero effects is the choice, the step is done.
            left = "; ".join(
                f"{cue.sound} (dropped by --sfx-adjust)" for cue in plan.cues
            )
            append_run_note(run_dir, f"SFX: none laid; left out: {left}")
            return StepReport(
                "sfx",
                "ran",
                f"0 cue(s): every planned effect was dropped by --sfx-adjust; "
                f"left out on purpose: {left}{dropped}"
                + (f"; facts older than the sound notes ({stale})" if stale else "")
                + f"; {filmed_note}",
            )
        sfx = lay_sfx(
            take,
            plan,
            cache_dir=run_dir / "sfx",
            output=next_versioned_path(takes, f"{base}-sfx", ".mp4"),
            adjustments=sfx_adjust,
            render=sfx_render,
        )
        if sfx.cost_usd:
            book(
                desk,
                episode=episode,
                usd=sfx.cost_usd,
                take_id=take_id,
                stream=out,
                unit="sfx",
            )
        bed_state["cues"] = tuple(
            CueLevel(c.sound, c.start, c.seconds, peak, c.gain_db)
            for c, peak in zip(sfx.mixed, sfx.peaks_db)
        )
        cues = ", ".join(
            f"{c.sound} @{c.start:.2f}s {c.gain_db:+.0f} dB"
            + (f" (note {', '.join(c.note_ids)})" if c.note_ids else "")
            for c in sfx.mixed
        )
        note = f"SFX -> `{sfx.output.name}`: {cues}; rendered {sfx.rendered}, ${sfx.cost_usd:.3f}"
        note += "".join(f"\n- skipped: {s}" for s in sfx.skipped)
        note += "".join(f"\n- dropped by a sound note: {d}" for d in plan.dropped)
        note += "".join(f"\n- left out: {d}" for d in sfx.dropped)
        append_run_note(run_dir, note)
        older = (
            f"; laid from facts older than the sound notes ({stale})" if stale else ""
        )
        # Every planned cue that is not on the take is named with its reason, never dropped silently.
        planned = len(sfx.mixed) + len(sfx.skipped) + len(sfx.dropped)
        not_laid = (
            f"; !! NOT LAID {len(sfx.skipped)} of {planned} planned: {'; '.join(sfx.skipped)}"
            if sfx.skipped
            else ""
        )
        left_out = (
            f"; left out on purpose: {'; '.join(sfx.dropped)}" if sfx.dropped else ""
        )
        return StepReport(
            "sfx",
            "ran",
            f"{len(sfx.mixed)} cue(s): {cues}{not_laid}{left_out}{dropped}{older}; {filmed_note}",
            sfx.output,
            sfx.cost_usd,
            not_laid=sfx.skipped,
        )

    def spine_line_texts() -> dict[str, str]:
        """``line_id`` to the words heard (``spoken_text``, else ``text``) for this episode's lines."""

        from creation.spine_view import episode_id_for

        if spine is None:
            return {}
        episode_id = episode_id_for(spine, episode)
        return {
            str(line.get("line_id")): str(
                line.get("spoken_text") or line.get("text") or ""
            )
            for beat in spine.get("beats") or []
            if isinstance(beat, dict) and beat.get("episode_id") == episode_id
            for line in beat.get("dialogue_lines") or []
            if isinstance(line, dict) and line.get("line_id")
        }

    def do_ambience(take: Path) -> StepReport:
        """The location's ambience under the whole locked-voice take: one cue per episode, cached on the desk."""

        if soundtrack.ambience_laid:
            return StepReport(
                AMBIENCE_STEP,
                "skipped",
                "the server laid the location's ambience under the voices (take facts ambience.laid): "
                "no kit ambience and no room tone",
            )
        payload = json.loads(facts_state["path"].read_text(encoding="utf-8"))
        brief = ambience_brief(payload, spine, episode=episode)
        if brief is None:
            return StepReport(
                AMBIENCE_STEP,
                "skipped",
                "no ambience cue: the spine names no location for this take's frames and the take facts "
                "plan no sustained sound; room tone is laid instead",
            )
        lengths, last = take_lengths(desk, episode, take_id, source=source)
        offset = episode_offset(lengths)
        seam = ""
        if offset is None:
            offset = 0.0
            seam = "; !! an earlier take's raw file is not on the desk: laid from the ambience's start, so the seam into this take may change ambience"
        # The cue covers the whole episode as filmed so far (later takes too), up to the route's 22 s.
        known = sum(length for length in lengths if length is not None)
        later = len(lengths) + 1
        while True:
            try:
                known += probe_video(
                    latest_raw_take(desk, episode, f"t{later}")
                ).duration_seconds
            except FileNotFoundError:
                break
            later += 1
        maker = ambience_maker or ambience_service_maker(audio, spine_id(desk))
        try:
            found, note = episode_ambience(
                desk, episode, brief, episode_seconds=known, make=maker
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"the ambience cue could not be fetched: {exc}") from exc
        if found.cost_usd:
            book(
                desk,
                episode=episode,
                usd=found.cost_usd,
                take_id=take_id,
                stream=out,
                unit="ambience",
            )
        if note:
            offset, seam = 0.0, f"; !! {note}"
        # The mix measures the take WITH its ambience (a steady floor lowers the gated loudness, so the
        # mix then raises the take): lay, read the gain the mix will pick, and re-lay until the gaps land
        # at AMBIENCE_GAP_DB in the mix (Hanakaze live check: one pass landed 4 dB loud).
        target = next_versioned_path(takes, f"{base}-ambience", ".mp4")
        take_gain = pick_gain(measure_loudness(take))
        for _attempt in range(3):
            laid = lay_ambience(
                take,
                found.path,
                target,
                windows=sorted([*line_windows(soundtrack), *laid_voice_windows()]),
                level_db=AMBIENCE_GAP_DB - take_gain,
                offset=offset,
                fade_in=offset == 0.0,
                fade_out=last,
            )
            mix_gain = pick_gain(measure_loudness(laid.output))
            if abs(laid.laid_db + mix_gain - AMBIENCE_GAP_DB) <= 1.0:
                break
            take_gain = mix_gain
            laid.output.unlink()  # this run's own file, re-laid at the corrected level
        else:
            laid = lay_ambience(
                take, found.path, target,
                windows=sorted([*line_windows(soundtrack), *laid_voice_windows()]),
                level_db=AMBIENCE_GAP_DB - take_gain, offset=offset,
                fade_in=offset == 0.0, fade_out=last,
            )  # fmt: skip
            mix_gain = pick_gain(measure_loudness(laid.output))
        take_gain = mix_gain
        where = "made now" if found.made else "on the desk, free"
        detail = (
            f'ambience: "{found.description}", {AMBIENCE_GAP_DB:.0f} dB between the lines in the mix '
            f"({laid.laid_db:+.1f} dB before the mix's {take_gain:+.1f} dB take gain), ducked "
            f"{AMBIENCE_DUCK_DB:.0f} dB under {len(soundtrack.lines)} line window(s)"
            + (
                f" and {len(laid_voice_windows())} laid voice window(s)"
                if laid_voice_windows()
                else ""
            )
            + "; "
            f"cue `{found.path.name}` ({where}), from {offset:.2f}s of the episode's ambience{seam}"
        )
        append_run_note(
            run_dir,
            f"Finish · ambience -> `{laid.output.name}`: {detail}"
            + (f"; ${found.cost_usd:.3f}" if found.cost_usd else ""),
        )
        return StepReport(AMBIENCE_STEP, "ran", detail, laid.output, found.cost_usd)

    def do_room_tone(take: Path) -> StepReport:
        """Room tone under the whole locked-voice take, then a free check that each line is heard in its window."""

        from creation.post.review import take_words

        if soundtrack.ambience_laid:
            toned, air = (
                None,
                "no room tone: the server's location ambience fills the gaps",
            )
        elif result._ran(AMBIENCE_STEP):
            # The location's ambience fills the gaps: room tone is only the fallback.
            toned, air = None, "no room tone: the location ambience fills the gaps"
        else:
            toned, air = (
                lay_room_tone(
                    take, next_versioned_path(takes, f"{base}-room-tone", ".mp4")
                ),
                "room tone under the whole take (fallback: no location ambience, see the ambience step)",
            )
        silent = unheard_lines(source, soundtrack)
        # Only a transcript of this very take: one of the take it replaced reads other words.
        words, skipped = take_words(desk, episode, take_id, source)
        if words is not None and treated_voice(source):
            words, skipped = None, "the voices were treated after filming"
        problems = (
            misplaced_lines(soundtrack, words, spine_line_texts(), take=source)
            if words is not None
            else []
        )
        heard = heard_summary(
            soundtrack.lines,
            silent=silent,
            problems=problems,
            words=words,
            skipped=skipped,
        )
        detail = f"{air}; {heard}" + "".join(f"; !! {c}" for c in (*silent, *problems))
        append_run_note(
            run_dir,
            f"Finish · room tone -> `{toned.name if toned else 'nothing laid'}`: {detail}",
        )
        return StepReport(ROOM_TONE_STEP, "ran", detail, toned)

    def do_bed(_take: Path) -> StepReport:
        if soundtrack.music_laid:
            # The harness's music is already in the take: a bed under it would double the music.
            append_run_note(
                run_dir,
                "Bed: none, the harness's music is in the take's soundtrack (music.laid)",
            )
            return StepReport(
                "bed",
                "skipped",
                "the harness's music is already in the take's soundtrack (take facts music.laid)",
            )
        bed = resolve_bed(desk, spine=spine, maker=bed_maker)
        bed_state["path"] = bed.path
        if bed.cost_usd:
            book(desk, episode=episode, usd=bed.cost_usd, stream=out, unit="bed")
        level = bed_level(desk, bed.path, flag=bed_db)
        bed_state["db"], bed_state["db_source"] = level.db, level.source
        if level.warning:
            print(f"[bed] {level.warning}", file=out, flush=True)
        append_run_note(run_dir, f"Bed: {bed.one_line()} at {level.one_line()}")
        return StepReport(
            "bed", "ran", f"{bed.one_line()} at {level.one_line()}", None, bed.cost_usd
        )

    def do_colour(take: Path) -> StepReport:
        if not colour:
            return StepReport("colour", "skipped", "--no-colour-match")
        if board is None:
            return StepReport(
                "colour", "skipped", "no approved board on the desk to match"
            )
        matched = colour_match(
            take,
            board,
            next_versioned_path(takes, f"{base}-colour", ".mp4"),
            strength=colour_strength,
        )
        append_run_note(
            run_dir,
            f"Colour match to `{board.name}` -> `{matched.output.name}`: {matched.one_line()}",
        )
        return StepReport("colour", "ran", matched.one_line(), matched.output)

    def do_mix(take: Path) -> StepReport:
        record_state["pre_bed"] = take
        mixed = mix_take(
            take,
            next_versioned_path(takes, f"{base}-mix", ".mp4"),
            bed=bed_state["path"],
            bed_db=bed_state["db"],
            # Nothing to duck without a bed (the harness's music is in the take).
            duck_db=mix_duck_db if bed_state["path"] is not None else None,
            music_in_take=soundtrack.music_laid,
            voice_source=thought_state["path"] or voice_state["path"] or source,
            cues=bed_state["cues"],
            buses=True,
            duck_windows=(
                sorted([*line_windows(soundtrack), *laid_voice_windows()])
                if locked
                else None
            ),
        )
        buses = (
            f" (buses for review: {', '.join(f'`{b.name}`' for b in mixed.buses)})"
            if mixed.buses
            else ""
        )
        append_run_note(
            run_dir, f"Mix -> `{mixed.output.name}`: {mixed.one_line()}{buses}"
        )
        return StepReport("mix", "ran", mixed.one_line(), mixed.output)

    def caption_words() -> tuple[Path | None, str]:
        """The transcript to time the lines on, and a note saying which (or why none).

        Any show is timed on a saved transcript of the raw take when one is on
        the desk (L-20260930-6: speech spans put every English line after a
        stray stretch of speech on the wrong words). A show not spoken in
        English asks the server for one when none is saved; an English take
        never does (it is timed on speech spans, with an ``EXTRA SPEECH``
        warning when stretches are left over).

        A revoiced or voice-fx take is timed on the transcript its revoice
        read (``take-epNN-tK-revoice-words-vN.json``, the raw take's words: the
        new lines are laid where the old ones were), with hand ``--voice``
        lines added and ``--mute`` windows taken out, on any show: the treated
        speech moves speech spans off the lines.
        """

        from creation.post.review import saved_words, server_transcript

        if spine is None:
            return None, ""
        if locked and not treated_voice(source):
            return (
                None,
                "timed on the take facts' line windows (the locked-voice dialogue track; no transcript)",
            )
        if treated_voice(source):
            words = newest_versioned(takes, f"{base}-revoice-words") or saved_words(
                desk, episode, take_id
            )
            if words is None:
                return None, (
                    f"no transcript timing: `{source.name}` is a revoice / voice-fx file and no "
                    f"`{base}-revoice-words-vN.json` is saved (timed on speech spans)"
                )
            if hand.voices or hand.mutes:
                merged = with_hand_lines(
                    words,
                    hand,
                    next_versioned_path(takes, f"{base}-cap-timing", ".json"),
                )
                return (
                    merged,
                    f"transcript `{words.name}` with the hand lines (`{merged.name}`)",
                )
            return words, f"transcript `{words.name}`"
        english = not captions_whole_lines(spine)
        if voice_state["path"] is not None and not english:
            return (
                None,
                "no transcript timing: the hand voice step changed the take's speech",
            )
        try:
            raw = latest_raw_take(desk, episode, take_id)
        except FileNotFoundError:
            raw = None
        if raw is None:
            return None, "no transcript timing: no raw take on the desk"
        via = ""
        if raw.resolve() != source:
            # A freeze/soften/deboard/colour of the raw take keeps its sound timeline: its transcript still holds.
            lineage = raw_take_behind(desk, source)
            if lineage.raw is None or lineage.raw.resolve() != raw.resolve():
                why = lineage.reason or (
                    f"it was made from `{lineage.raw.name}`, not the newest raw take `{raw.name}`"
                    if lineage.raw is not None
                    else "its raw take is unknown"
                )
                return None, (
                    f"no transcript timing: `{source.name}` is not the raw take the server transcribes ({why})"
                )
            if not lineage.keeps_timeline:
                return None, f"no transcript timing: {lineage.reason}"
            via = f" (raw take's words; `{source.name}` keeps its sound timeline: {lineage.chain_text()})"
        saved = saved_words(desk, episode, take_id)
        if saved is not None and english and (hand.voices or hand.mutes):
            # The hand lines are laid where the transcript cannot have heard them: add them, take the mutes out.
            merged = with_hand_lines(
                saved, hand, next_versioned_path(takes, f"{base}-cap-timing", ".json")
            )
            return (
                merged,
                f"transcript `{saved.name}` with the hand lines (`{merged.name}`){via}",
            )
        if saved is not None:
            return saved, f"transcript `{saved.name}`{via}"
        if english:
            # An English take is never sent for a transcript: without a saved one it is timed on speech.
            return None, ""
        try:
            made = (transcriber or server_transcript)(desk, episode, take_id)
        except (ValueError, RuntimeError, OSError, KeyError, httpx.HTTPError) as exc:
            return None, f"no transcript ({type(exc).__name__}: {exc})"[:300]
        return made, f"transcript made on the server: `{made.name}`{via}"

    def laid_voice_lines() -> tuple[list[tuple[CaptionLine, Span]], list[str]]:
        """Each ``--voice`` line with its words and where it plays, and a ``!!`` note per one with no words."""

        laid: list[tuple[CaptionLine, Span]] = []
        notes: list[str] = []
        for line, seconds in hand.voices:
            words = voice_line_text(line.path)
            if not words:
                notes.append(
                    f"!! --voice {line.path.name} not captioned: no words saved with it "
                    f"(`{line.path.with_suffix('.json').name}`); make it with `voice-line`, which saves them"
                )
                continue
            laid.append(
                (
                    CaptionLine(f"voice {line.path.name}", words, True, words),
                    Span(line.start, line.start + seconds),
                )
            )
        return laid, notes

    def do_captions(take: Path) -> StepReport:
        if style == "none":
            append_run_note(
                run_dir, f"Finish · captions: {CAPTIONS_OFF}, nothing burned"
            )
            return StepReport(
                "captions", "skipped", f"{CAPTIONS_OFF}: no captions burned"
            )
        caption_spine, spine_note = current_spine(desk, episode, fetch=spine_fetcher)
        words_json, words_note = caption_words()
        if words_note:
            append_run_note(run_dir, f"Finish · captions: {words_note}")
        laid, laid_notes = laid_voice_lines() if voice_state["path"] else ([], [])
        try:
            captioned = caption_take(
                desk,
                episode_ordinal=episode,
                take=take,
                line_starts=list(line_starts) if line_starts else None,
                line_ends=list(line_ends) if line_ends else None,
                words_json=words_json,
                timing_source=voice_state["path"] or source,
                stem=f"{base}-cap",
                spine=caption_spine,
                laid_lines=laid,
                fixed_lines=[
                    (
                        CaptionLine(
                            cue.cue_id, cue.line, True, cue.spoken_text or cue.line
                        ),
                        Span(line.start, line.start + seconds),
                    )
                    for cue, line, seconds in thought_state["laid"]
                ],
                # Only this take's beats' lines: t2 is never captioned with t1's.
                take_index=thoughts.take_number(take_id),
                line_spans=(
                    {
                        line.line_id: Span(line.start, line.end)
                        for line in soundtrack.lines
                    }
                    if locked and not treated_voice(source)
                    else None
                ),
                style=style,
            )
        except ValueError as exc:
            if "no dialogue lines" in str(exc):
                return StepReport(
                    "captions",
                    "skipped",
                    "no dialogue lines in the spine (wordless take)",
                )
            raise
        timing = "; ".join(captioned.timing_lines())
        treatment = "whole English lines" if captioned.whole_lines else "word flicker"
        if style != "house":
            treatment = f"{style}, {treatment}"
        if words_note:
            treatment += f", {words_note}"
        # A line that is not English is left uncaptioned, and an italic line may miss
        # Georgia Italic on this laptop; the summary line says which.
        warnings = "".join(
            f" · {w}"
            for w in (
                *captioned.not_english,
                captioned.font_warning,
                captioned.take_lines_warning,
                style_note,
                captioned.timing_warning,
                spine_note,
                *laid_notes,
            )
            if w
        )
        append_run_note(
            run_dir,
            f"Captions ({treatment}) -> `{captioned.video.name}` (cues `{captioned.ass.name}`): {timing}{warnings}",
        )
        return StepReport(
            "captions",
            "ran",
            f"{len(captioned.lines)} line(s), {treatment}: {timing}{warnings}",
            captioned.video,
        )

    def do_watermark(take: Path) -> StepReport:
        marked = watermark(
            take, next_versioned_path(takes, f"{base}-sokii", ".mp4"), y=watermark_y
        )
        record_state["master"] = take
        append_run_note(
            run_dir, f"Watermarked -> `{marked.name}` (un-marked master `{take.name}`)"
        )
        return StepReport(
            "watermark",
            "ran",
            "Sokii mark top left, under the covered top strip",
            marked,
        )

    def do_thumbnail(take: Path) -> StepReport:
        if not thumbnail:
            return StepReport("thumbnail", "skipped", "--no-thumbnail")
        try:
            final, answer = attach_episode_thumbnail_to_finish(
                desk,
                episode=episode,
                take_id=take_id,
                marked_video=take,
                takes_dir=takes,
                base_stem=base,
                draw=draw_thumbnail,
                stream=out,
            )
        except (MediaToolError, SystemExit, httpx.HTTPError, OSError) as exc:
            detail = (
                str(exc.code)
                if isinstance(exc, SystemExit)
                else f"{type(exc).__name__}: {exc}"
            )
            return StepReport("thumbnail", "failed", detail[:300])
        if answer is None:
            return StepReport(
                "thumbnail",
                "skipped",
                "no stored take URL or the server has no episode thumbnail route yet",
            )
        if answer.get("needs_opt_in"):
            ask = (
                f"no cover on the desk yet. Drawing one on the server costs ${THUMBNAIL_USD:.2f}; "
                f"after the human's yes, finish again with --thumbnail"
            )
            append_run_note(run_dir, f"Finish · thumbnail: skipped, {ask}")
            return StepReport("thumbnail", "skipped", ask)
        if answer.get("reused"):
            append_run_note(
                run_dir,
                f"Episode cover `{answer['reused']}` re-embedded on `{final.name}`, $0",
            )
            return StepReport(
                "thumbnail",
                "ran",
                f"attached the saved cover `{answer['reused']}` (free); deliver `{final.name}`",
                final,
            )
        cost = float(answer.get("cost_usd") or 0.0)
        if cost:
            book(
                desk,
                episode=episode,
                usd=cost,
                take_id=take_id,
                stream=out,
                unit="thumbnail",
            )
        cached = " (cached, free)" if answer.get("cached") else ""
        append_run_note(
            run_dir,
            f"Episode thumbnail embedded on `{final.name}`{cached}, ${cost:.2f}",
        )
        return StepReport(
            "thumbnail",
            "ran",
            f"attached cover from server draw{cached}; deliver `{final.name}`",
            final,
            cost,
        )

    step("deboard", "Replacing the board frames at the head of the take", do_deboard)
    if "voice" in hand_steps:
        step("voice", "Muting stray speech and laying the hand voice lines", do_voice)
    if INNER_VOICE_STEP in hand_steps:
        step(
            INNER_VOICE_STEP,
            "Making and laying the episode's inner-voice lines on this take",
            do_inner_voice,
        )
    step("sfx", "Laying the take's sound effects", do_sfx)
    if "cues" in hand_steps:
        step("cues", "Laying the hand cues", do_cues)
    if locked:
        step(
            AMBIENCE_STEP,
            "Laying the location's ambience under the locked voices",
            do_ambience,
        )
        if not result._ran(AMBIENCE_STEP) and not soundtrack.ambience_laid:
            print(
                f"[{AMBIENCE_STEP}] !! no location ambience on this take: room tone is laid instead "
                "(the take will sound drier than a native take)",
                file=out,
                flush=True,
            )
        step(ROOM_TONE_STEP, "Laying room tone under the locked voices", do_room_tone)
    step("bed", "Finding the show's music bed (the harness's)", do_bed)
    has_music = soundtrack.music_laid or result._ran("bed")
    if locked and not (has_music and result._ran(ROOM_TONE_STEP)):
        lacking = " and ".join(
            name
            for name, present in (
                ("music bed", has_music),
                ("room tone", result._ran(ROOM_TONE_STEP)),
            )
            if not present
        )
        result.stopped = (
            f"this take's sound is only the locked voices (digital silence between lines) and it has no "
            f"{lacking}. Nothing deliverable was made: no mix, captions or mark. Fix the step named above "
            "(the harness bed: the spine saved on the desk and the server's audio route answering) and finish again"
        )
        print(f"!! STOPPED: {result.stopped}", file=out, flush=True)
        append_run_note(run_dir, f"Finish · STOPPED: {result.stopped}")
    else:
        step("colour", "Matching the look to the approved board", do_colour)
        step("mix", "Mixing the bed under the voice at a measured level", do_mix)
        step(
            "captions",
            "Skipping captions (--caption-style none)"
            if style == "none"
            else f"Burning {style} captions",
            do_captions,
        )
        step("watermark", "Putting the Sokii mark on", do_watermark)
    if result.complete:
        step(
            "thumbnail",
            "Putting the episode cover on the deliverable",
            do_thumbnail,
        )

    result.final = current
    if text_check is not None:
        result.text_warnings = text_check.warning_lines(
            desk=desk, episode=episode, take_id=take_id, final=current
        )
    try:
        result.loudness = f"{measure_loudness(current):.1f} LUFS"
    except MediaToolError as exc:
        result.loudness = f"not measured ({exc})"
    summary = result.summary_lines()
    record = write_finish_record(
        desk,
        episode=episode,
        take_id=take_id,
        complete=result.complete,
        pre_bed=record_state["pre_bed"] if result._ran("mix") else None,
        music_in_take=soundtrack.music_laid,
        master=record_state["master"] or current,
        final=current,
        bed=bed_state["path"],
        bed_db=bed_state["db"],
        bed_db_source=bed_state["db_source"],
        duck_db=mix_duck_db,
        hand_voices=[
            {"file": line.path.name, "start": line.start, "seconds": round(seconds, 3),
             "line": voice_line_text(line.path)}
            for line, seconds in hand.voices
        ] if result._ran("voice") else [],
        inner_voice=[
            {"cue_id": cue.cue_id, "file": line.path.name, "start": line.start, "seconds": round(seconds, 3),
             "episode_start": cue.episode_start, "speaker_cast_id": cue.speaker_cast_id, "line": cue.line,
             **({"spoken_text": cue.spoken_text} if cue.spoken_text else {})}
            for cue, line, seconds in thought_state["laid"]
        ],
    )  # fmt: skip
    summary.insert(1, f"Record: {record.name} (what `join` reads)")
    append_run_note(
        run_dir,
        "Finish summary\n"
        + "\n".join(summary)
        + f"\nCost (operator only): ${result.cost_usd:.3f} of generated audio",
    )
    for line in summary:
        print(line, file=out)
    if not result.complete:
        missing = ", ".join(result.sound_missing)
        append_run_note(
            run_dir,
            f"Finish NOT DONE: no {missing}. Do not deliver `{result.final.name}`.",
        )
        print(
            f"Stopped: NOT DONE: this take has no {missing}. Do not deliver {result.final.name}.",
            file=out,
        )
        print(f"Fix: {INCOMPLETE_FIX}", file=out)
        print("Next: fix what is missing and run finish again.", file=out)
        return result
    print("Next: watch it and say Use it or Change this.", file=out)
    return result
