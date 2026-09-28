"""Finish one accepted take in one command: SFX, music bed, colour match, mix, captions, mark.

Hosted post-production is off on the Drama API: the raw take has the model's
sound only. ``fictora-produce finish`` finishes it on this laptop:

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
from creation.post.desk import (
    approved_board,
    latest_raw_take,
    open_api,
    saved_spine,
    spine_id,
    take_job_id,
)
from creation.post.media import MediaToolError, measure_loudness
from creation.post.mix import check_duck_db, mix_take
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
        return tuple(missing)

    @property
    def complete(self) -> bool:
        """True when music, SFX and the mix all went on."""

        return not self.sound_missing

    def sound_line(self) -> str:
        """``Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`` (✗ for what did not go on)."""

        missing = set(self.sound_missing)
        marks = [f"{part} {'✗' if part in missing else '✓'}" for part in ("music", "SFX", "mix")]
        marks.append(f"captions {'✓' if self._ran('captions') else '✗'}")
        return "Sound: " + " · ".join(marks)

    def summary_lines(self) -> list[str]:
        """Final file, loudness, each step, then the sound line (cost stays in run notes only)."""

        lines = [f"Final: {self.final}", f"Loudness: {self.loudness or 'not measured'}"]
        lines += [f"- {step.step}: {step.status} — {step.detail}" for step in self.steps]
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
    "Mix: read the mix step's error above."
)


def book(desk: Path, *, episode: int, usd: float, take_id: str | None = None, stream: TextIO | None = None) -> None:
    """Add local post spend to the desk ledger; a desk without that slot gets a printed note, not a crash."""

    try:
        record_spend(desk, episode=episode, usd=usd, take_id=take_id)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"note: a spend was not booked on the desk ledger ({exc}); see run-notes.md", file=stream or sys.stderr)
        if (desk / f"ep{episode:02d}" / "run-notes.md").is_file():
            append_run_note(
                desk / f"ep{episode:02d}", f"Not booked on the ledger: ${usd:.3f} ({exc}); fictora-ops spend"
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
    path = next_versioned_path(desk / f"ep{episode:02d}" / "api", f"take-facts-ep{episode:02d}-{take_id}", ".json")
    path.write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")
    return path


def run_finish(
    desk: Path,
    *,
    episode: int = 1,
    take_id: str = "t1",
    take_file: Path | None = None,
    colour: bool = True,
    colour_strength: float = 1.0,
    bed_db: float = DEFAULT_BED_DB,
    music: str | None = None,
    duck_db: float | None = None,
    sfx_adjust: tuple[Adjustment, ...] = (),
    line_starts: tuple[float, ...] | None = None,
    watermark_y: int | None = None,
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
        When ``duck_db`` is out of range (checked before any step runs).
    """

    out = stream or sys.stderr
    desk = desk.expanduser().resolve()
    check_duck_db(duck_db)
    run_dir = desk / f"ep{episode:02d}"
    source = take_file.expanduser().resolve() if take_file else latest_raw_take(desk, episode, take_id)
    if not source.is_file():
        raise FileNotFoundError(f"take not found: {source}")
    takes = run_dir / "takes"
    base = f"take-ep{episode:02d}-{take_id}"
    found_spine = saved_spine(desk, episode)
    spine = found_spine[0] if found_spine else None
    board = approved_board(desk, episode, take_id)
    audio = DramaApiAudio(desk, episode=episode)
    sfx_render = sfx_render or service_renderer(audio, spine_id(desk))
    bed_maker = bed_maker or service_music_maker(audio)
    result = FinishResult(source=source, final=source)
    current = source
    bed_state: dict[str, Any] = {"path": None}
    print(f"Finishing {source.name}: sound effects, music, look, mix, captions, mark (2-4 minutes)", file=out)
    append_run_note(run_dir, f"Finish chain on `{source.name}` (board: `{board.name if board else 'none'}`)")

    def step(name: str, doing: str, work: Callable[[Path], StepReport]) -> None:
        nonlocal current
        print(f"[{name}] {doing}", file=out, flush=True)
        try:
            report = work(current)
        except STEP_ERRORS as exc:
            report = StepReport(name, "failed", f"{type(exc).__name__}: {exc}".strip()[:300])
            print(f"[{name}] Stopped: {report.detail}. Skipped; carrying on from the last good file", file=out)
            append_run_note(run_dir, f"Finish · {name}: FAILED, skipped — {report.detail}")
            result.steps.append(report)
            return
        print(f"[{name}] {report.status}: {report.detail}", file=out, flush=True)
        if report.output is not None:
            current = report.output
        result.steps.append(report)

    def do_sfx(take: Path) -> StepReport:
        facts = saved_take_facts(desk, episode, take_id) or facts_fetcher(desk, episode, take_id)
        if facts is None:
            return StepReport(
                "sfx",
                "skipped",
                f"no take facts: api/17_raw_scene_clips.json names no job for {take_id} and no "
                f"api/take-facts-ep{episode:02d}-{take_id}-vN.json is saved",
            )
        plan = plan_from_take_facts(json.loads(facts.read_text(encoding="utf-8")))
        if not plan.cues:
            return StepReport("sfx", "ran", f"the take facts plan no effect (every shot speaks); `{facts.name}`")
        sfx = lay_sfx(
            take,
            plan,
            cache_dir=run_dir / "sfx",
            output=next_versioned_path(takes, f"{base}-sfx", ".mp4"),
            adjustments=sfx_adjust,
            render=sfx_render,
        )
        if sfx.cost_usd:
            book(desk, episode=episode, usd=sfx.cost_usd, take_id=take_id, stream=out)
        cues = ", ".join(f"{c.sound} @{c.start:.2f}s {c.gain_db:+.0f} dB" for c in sfx.mixed)
        note = f"SFX -> `{sfx.output.name}`: {cues}; rendered {sfx.rendered}, ${sfx.cost_usd:.3f}"
        note += "".join(f"\n- skipped: {s}" for s in sfx.skipped)
        append_run_note(run_dir, note)
        return StepReport("sfx", "ran", f"{len(sfx.mixed)} cue(s): {cues}", sfx.output, sfx.cost_usd)

    def do_bed(_take: Path) -> StepReport:
        bed = resolve_bed(desk, spine=spine, music=music, maker=bed_maker)
        bed_state["path"] = bed.path
        if bed.cost_usd:
            book(desk, episode=episode, usd=bed.cost_usd, stream=out)
        append_run_note(run_dir, f"Bed: {bed.one_line()} at {bed_db:+.1f} dB")
        return StepReport("bed", "ran", f"{bed.one_line()} at {bed_db:+.1f} dB", None, bed.cost_usd)

    def do_colour(take: Path) -> StepReport:
        if not colour:
            return StepReport("colour", "skipped", "--no-colour-match")
        if board is None:
            return StepReport("colour", "skipped", "no approved board on the desk to match")
        matched = colour_match(
            take, board, next_versioned_path(takes, f"{base}-colour", ".mp4"), strength=colour_strength
        )
        append_run_note(run_dir, f"Colour match to `{board.name}` -> `{matched.output.name}`: {matched.one_line()}")
        return StepReport("colour", "ran", matched.one_line(), matched.output)

    def do_mix(take: Path) -> StepReport:
        mixed = mix_take(
            take,
            next_versioned_path(takes, f"{base}-mix", ".mp4"),
            bed=bed_state["path"],
            bed_db=bed_db,
            duck_db=duck_db,
            voice_source=source,
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
                timing_source=source,
                stem=f"{base}-cap",
            )
        except ValueError as exc:
            if "no dialogue lines" in str(exc):
                return StepReport("captions", "skipped", "no dialogue lines in the spine (wordless take)")
            raise
        timing = "; ".join(
            f"{a.start:.2f}-{a.end:.2f}s {line!r}" for line, a in zip(captioned.lines, captioned.anchors)
        )
        append_run_note(run_dir, f"Captions -> `{captioned.video.name}` (cues `{captioned.ass.name}`): {timing}")
        return StepReport("captions", "ran", f"{len(captioned.lines)} line(s): {timing}", captioned.video)

    def do_watermark(take: Path) -> StepReport:
        marked = watermark(take, next_versioned_path(takes, f"{base}-sokii", ".mp4"), y=watermark_y)
        append_run_note(run_dir, f"Watermarked -> `{marked.name}` (un-marked master `{take.name}`)")
        return StepReport("watermark", "ran", "Sokii mark top left, under the covered top strip", marked)

    step("sfx", "Laying the take's sound effects", do_sfx)
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
    append_run_note(
        run_dir,
        "Finish summary\n" + "\n".join(summary) + f"\nCost (operator only): ${result.cost_usd:.3f} of generated audio",
    )
    for line in summary:
        print(line, file=out)
    if not result.complete:
        missing = ", ".join(result.sound_missing)
        append_run_note(run_dir, f"Finish NOT DONE: no {missing}. Do not deliver `{result.final.name}`.")
        print(f"Stopped: NOT DONE: this take has no {missing}. Do not deliver {result.final.name}.", file=out)
        print(f"Fix: {INCOMPLETE_FIX}", file=out)
        print("Next: fix what is missing and run finish again.", file=out)
        return result
    print("Next: watch it and say Use it or Change this.", file=out)
    return result
