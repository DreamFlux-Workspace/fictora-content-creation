"""Finish one accepted take in one command: deboard, SFX, music bed, colour match, mix, captions, mark.

Hosted post-production is off on the Drama API: the raw take has the model's
sound only. ``fictora-produce finish`` finishes it on this laptop:

0. ``deboard``   - replace the storyboard frames the take opens on (Turbo's
   start image) with the first real frame; measured against the approved
   board, capped at 12; nothing written when there are none. The length,
   frame count and sound are unchanged, so every later step (take-facts cue
   times, caption timing, the voice the mix ducks under) stays on the raw
   take's timeline. ``--no-deboard`` skips it.
1. ``sfx``       - the take's cue plan from ``GET /v1/jobs/{take_job}/take-facts``
   (fetched and saved as ``api/take-facts-epNN-tK-vN.json`` when missing),
   rendered on the server (the audio service) and cached in ``epNN/sfx/``.
2. ``bed``       - the show's music bed (desk pin, else the spine's pinned bed,
   else made once on the server and pinned on the desk).
3. ``colour``    - match the take to the board the human approved.
4. ``mix``       - bed under the take, ducked under the voice, gain measured
   so the mix lands near -18 LUFS; ``--duck-db N`` for an exact duck depth.
5. ``captions``  - house captions (English), timed on the take before the bed.
6. ``watermark`` - the Sokii mark top left, under the covered top strip.

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
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from creation.captions import caption_take
from creation.harness.raw_video import fetch_take_facts
from creation.ops.floor import record_spend
from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.post.audio_service import DramaApiAudio
from creation.post.bed import DEFAULT_BED_DB, Maker, resolve_bed, service_music_maker
from creation.post.colour import colour_match
from creation.post.deboard import deboard as deboard_take
from creation.post.finish_record import write_finish_record
from creation.post.hand import HandPlan, Placed, check_hand_plan, lay_cues, lay_voice
from creation.post.desk import (
    approved_board,
    latest_raw_take,
    open_api,
    saved_spine,
    spine_id,
    take_job_id,
)
from creation.post.media import MediaToolError, measure_loudness, probe_video
from creation.post.mix import CueLevel, check_duck_db, mix_take
from creation.post.take_facts import level_notes, save_take_facts, stale_facts_reason
from creation.post.sfx import (
    Adjustment,
    Renderer,
    lay_sfx,
    plan_from_take_facts,
    saved_take_facts,
    service_renderer,
)
from creation.post.watermark import watermark

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


@dataclass
class StepReport:
    """What one step did: ``ran``, ``skipped`` or ``failed``."""

    step: str
    status: str
    detail: str
    output: Path | None = None
    cost_usd: float = 0.0


@dataclass
class FinishResult:
    """The finished take and every step's report."""

    source: Path
    final: Path
    steps: list[StepReport] = field(default_factory=list)
    loudness: str = ""
    hand_steps: tuple[str, ...] = ()

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
        if not self._ran("bed") or (mix is not None and "NO MUSIC BED" in mix.detail):
            missing.append("music")
        if not self._ran("sfx"):
            missing.append("SFX")
        if not self._ran("mix"):
            missing.append("mix")
        missing += [name for name in self.hand_steps if not self._ran(name)]
        return tuple(missing)

    @property
    def complete(self) -> bool:
        """True when music, SFX and the mix all went on."""

        return not self.sound_missing

    def sound_line(self) -> str:
        """``Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`` (✗ for what did not go on)."""

        missing = set(self.sound_missing)
        marks = [
            f"{part} {'✗' if part in missing else '✓'}"
            for part in ("music", "SFX", "mix")
        ]
        marks.append(f"captions {'✓' if self._ran('captions') else '✗'}")
        marks += [
            f"hand {name} {'✗' if name in missing else '✓'}" for name in self.hand_steps
        ]
        return "Sound: " + " · ".join(marks)

    def summary_lines(self) -> list[str]:
        """Final file, loudness, each step, then the sound line (cost stays in run notes only)."""

        lines = [f"Final: {self.final}", f"Loudness: {self.loudness or 'not measured'}"]
        lines += [
            f"- {step.step}: {step.status} — {step.detail}" for step in self.steps
        ]
        lines.append(self.sound_line())
        return lines

    def as_json(self) -> dict[str, Any]:
        """JSON report (no cost: that stays in the run notes)."""

        return {
            "source": str(self.source),
            "final": str(self.final),
            "loudness": self.loudness,
            "complete": self.complete,
            "missing": list(self.sound_missing),
            "steps": [
                {"step": s.step, "status": s.status, "detail": s.detail,
                 "output": str(s.output) if s.output else None}
                for s in self.steps
            ],
        }  # fmt: skip


INCOMPLETE_FIX = (
    "Music: pin a bed (`fictora-produce set-bed --desk D --path <file>`, or let finish make one on the server). "
    "SFX: finish needs the take's facts (GET /v1/jobs/{take_job}/take-facts; it fetches them when "
    "api/17_raw_scene_clips.json names the take job) and the server's audio endpoints. "
    "Mix, or a hand voice / cues step you asked for: read that step's error above."
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


def run_finish(
    desk: Path,
    *,
    episode: int = 1,
    take_id: str = "t1",
    take_file: Path | None = None,
    deboard: bool = True,
    colour: bool = True,
    colour_strength: float = 1.0,
    bed_db: float = DEFAULT_BED_DB,
    music: str | None = None,
    duck_db: float | None = None,
    sfx_adjust: tuple[Adjustment, ...] = (),
    line_starts: tuple[float, ...] | None = None,
    watermark_y: int | None = None,
    mutes: tuple[tuple[float, float], ...] = (),
    voices: tuple[Placed, ...] = (),
    cues: tuple[Placed, ...] = (),
    sfx_render: Renderer | None = None,
    bed_maker: Maker | None = None,
    facts_fetcher: FactsFetcher = api_facts_fetcher,
    stream: TextIO | None = None,
) -> FinishResult:
    """Run the whole local post chain on one accepted take.

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
        Bed level in the mix.
    music
        Description for a bed that has to be made (forces a new bed).
    duck_db
        Exact duck depth under the voice (1-30 dB); ``None`` uses the compressor.
    sfx_adjust
        Per-take SFX level changes.
    line_starts
        Manual caption line starts.
    watermark_y
        Mark top offset override (never into the top 8%).
    mutes
        ``--mute`` windows: stray speech silenced in the take's own audio (take seconds as filmed).
    voices
        ``--voice`` dry lines laid into the take's own audio (take seconds as filmed).
    cues
        ``--cue`` hand cues laid after the SFX step (take seconds as filmed).
    sfx_render, bed_maker, facts_fetcher
        Injected for tests.
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
        take (outside it, silent); checked before any step runs.
    """

    out = stream or sys.stderr
    desk = desk.expanduser().resolve()
    check_duck_db(duck_db)
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
    found_spine = saved_spine(desk, episode)
    spine = found_spine[0] if found_spine else None
    board = approved_board(desk, episode, take_id)
    audio = DramaApiAudio(desk, episode=episode)
    sfx_render = sfx_render or service_renderer(audio, spine_id(desk))
    bed_maker = bed_maker or service_music_maker(audio)
    hand_steps = (("voice",) if hand.mutes or hand.voices else ()) + (
        ("cues",) if hand.cues else ()
    )
    result = FinishResult(source=source, final=source, hand_steps=hand_steps)
    current = source
    bed_state: dict[str, Any] = {"path": None, "cues": ()}
    voice_state: dict[str, Path | None] = {"path": None}
    # For the finish record `join` reads: what the mix read, and what the mark went on.
    record_state: dict[str, Path | None] = {"pre_bed": None, "master": None}
    print(
        f"Finishing {source.name}: board frames, sound effects, music, look, mix, captions, mark (2-4 minutes)",
        file=out,
    )
    append_run_note(
        run_dir,
        f"Finish chain on `{source.name}` (board: `{board.name if board else 'none'}`)",
    )

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

    def do_cues(take: Path) -> StepReport:
        speech = list(hand.voice_windows)
        facts = saved_take_facts(desk, episode, take_id)
        if facts is not None:
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
        append_run_note(run_dir, f"Hand cues -> `{laid.name}`: " + "; ".join(parts))
        return StepReport("cues", "ran", "; ".join(parts), laid)

    def do_sfx(take: Path) -> StepReport:
        facts = saved_take_facts(desk, episode, take_id) or facts_fetcher(
            desk, episode, take_id
        )
        if facts is None:
            return StepReport(
                "sfx",
                "skipped",
                f"no take facts: api/17_raw_scene_clips.json names no job for {take_id} and no "
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
        levels = level_notes(spine)
        if levels and not sfx_adjust:
            print(
                f"[sfx] note: the story's drop/level sound notes ({'; '.join(levels)}) are applied by the server's "
                'mix, not by finish; for the same change here pass --sfx-adjust ("hum=drop", "rain=+4")',
                file=out,
                flush=True,
            )
        plan = plan_from_take_facts(payload)
        if not plan.cues:
            return StepReport(
                "sfx",
                "ran",
                f"the take facts plan no effect (every shot speaks); `{facts.name}`"
                + (f"; facts older than the sound notes ({stale})" if stale else ""),
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
            f"{c.sound} @{c.start:.2f}s {c.gain_db:+.0f} dB" for c in sfx.mixed
        )
        note = f"SFX -> `{sfx.output.name}`: {cues}; rendered {sfx.rendered}, ${sfx.cost_usd:.3f}"
        note += "".join(f"\n- skipped: {s}" for s in sfx.skipped)
        append_run_note(run_dir, note)
        older = (
            f"; laid from facts older than the sound notes ({stale})" if stale else ""
        )
        return StepReport(
            "sfx",
            "ran",
            f"{len(sfx.mixed)} cue(s): {cues}{older}",
            sfx.output,
            sfx.cost_usd,
        )

    def do_bed(_take: Path) -> StepReport:
        bed = resolve_bed(desk, spine=spine, music=music, maker=bed_maker)
        bed_state["path"] = bed.path
        if bed.cost_usd:
            book(desk, episode=episode, usd=bed.cost_usd, stream=out, unit="bed")
        append_run_note(run_dir, f"Bed: {bed.one_line()} at {bed_db:+.1f} dB")
        return StepReport(
            "bed", "ran", f"{bed.one_line()} at {bed_db:+.1f} dB", None, bed.cost_usd
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
            bed_db=bed_db,
            duck_db=duck_db,
            voice_source=voice_state["path"] or source,
            cues=bed_state["cues"],
        )
        append_run_note(run_dir, f"Mix -> `{mixed.output.name}`: {mixed.one_line()}")
        return StepReport("mix", "ran", mixed.one_line(), mixed.output)

    def do_captions(take: Path) -> StepReport:
        try:
            captioned = caption_take(
                desk,
                episode_ordinal=episode,
                take=take,
                line_starts=list(line_starts) if line_starts else None,
                timing_source=voice_state["path"] or source,
                stem=f"{base}-cap",
            )
        except ValueError as exc:
            if "no dialogue lines" in str(exc):
                return StepReport(
                    "captions",
                    "skipped",
                    "no dialogue lines in the spine (wordless take)",
                )
            raise
        timing = "; ".join(
            f"{a.start:.2f}-{a.end:.2f}s {line!r}"
            for line, a in zip(captioned.lines, captioned.anchors)
        )
        treatment = "whole English lines" if captioned.whole_lines else "word flicker"
        # A line that is not English is left uncaptioned; the summary line says which.
        warnings = "".join(f" · {w}" for w in captioned.not_english)
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

    step("deboard", "Replacing the board frames at the head of the take", do_deboard)
    if "voice" in hand_steps:
        step("voice", "Muting stray speech and laying the hand voice lines", do_voice)
    step("sfx", "Laying the take's sound effects", do_sfx)
    if "cues" in hand_steps:
        step("cues", "Laying the hand cues", do_cues)
    step("bed", "Finding the show's music bed", do_bed)
    step("colour", "Matching the look to the approved board", do_colour)
    step("mix", "Mixing the bed under the voice at a measured level", do_mix)
    step("captions", "Burning house captions", do_captions)
    step("watermark", "Putting the Sokii mark on", do_watermark)

    result.final = current
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
        master=record_state["master"] or current,
        final=current,
        bed=bed_state["path"],
        bed_db=bed_db,
        duck_db=duck_db,
    )
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
