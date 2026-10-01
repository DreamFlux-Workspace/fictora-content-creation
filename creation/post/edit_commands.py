"""``fictora-produce`` local edit commands: deboard, trim, freeze, tempo, soften, blur.

Each reads one take on the desk (``--take-file``, else the newest raw take),
writes ``epNN/takes/take-epNN-tK-<step>-vN.mp4`` (never overwriting), appends
a run note, records the edit in ``epNN/takes/edit-chain.jsonl``
(:mod:`creation.post.lineage`: ``finish`` reads it to know a ``freeze`` or
``soften`` output still has the raw take's sound timeline) and prints what it
did. Free: ffmpeg and numpy on this laptop.
A file with the episode cover attached (``finish``'s ``-sokii-cover-``
deliverable) keeps the cover through the edit and is written
``take-epNN-tK-<step>-cover-vN.mp4``.

When the file edited is one a finish record names (``trim`` / ``tempo`` on the
finished take, or ``freeze`` / ``soften`` / ``blur`` run on it), the same edit is applied
to the record's other files first: the take before the bed (``pre_bed``) and
the un-marked master (``take-epNN-tK-<step>-prebed-vN.mp4``,
``-master-vN.mp4``, ``-final-vN.mp4``), then to the named file, and a new
finish record names the edited three with the edit chain, so ``join`` still
lays one bed across the edited take and marks once. ``freeze``, ``soften`` and
``blur`` leave the sound alone, so the record keeps the same pre-bed take.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.post.deboard import BOARD_LEAK_MAX_FRAMES, deboard
from creation.post.desk import approved_board, latest_raw_take
from creation.post.edit import (
    BLUR_SIGMA,
    SLOW_TEMPO,
    BlurResult,
    TrimResult,
    blur_boxes,
    change_tempo,
    cut_frames,
    freeze_frame,
    measure_cuts,
    parse_box,
    parse_cut,
    plan_trim,
    shift_json_file,
    soften_seams,
)
from creation.post.finish_record import (
    RECORD_FILES,
    FinishRecord,
    carry_finish_record,
    record_for_file,
)
from creation.post.lineage import record_edit
from creation.post.media import probe_video, video_streams

EDIT_COMMANDS = frozenset({"deboard", "trim", "freeze", "tempo", "soften", "blur"})
#: Edits that carry a finish record onto their output.
CARRIED = frozenset({"trim", "tempo", "freeze", "soften", "blur"})
#: Edits that leave the sound as it was: the record keeps the same pre-bed take.
PICTURE_ONLY = frozenset({"freeze", "soften", "blur"})
#: Name part of each edited record file: ``take-epNN-tK-<step>-<part>-vN.mp4``.
COMPANION = {"pre_bed": "prebed", "master": "master", "final": "final"}

Apply = Callable[[Path, Path], object]


class RecordCarry:
    """The finish record of the file being edited, and the edited copy of each of its files.

    Parameters
    ----------
    desk
        Series desk.
    record
        The record naming the edited file, or ``None`` (nothing is carried).
    source
        The file the operator named.
    step
        ``trim``, ``tempo``, ``freeze``, ``soften`` or ``blur``.
    """

    def __init__(
        self, desk: Path, record: FinishRecord | None, source: Path, step: str
    ) -> None:
        self.desk = desk
        self.record = record
        self.source = source.resolve()
        self.step = step
        self.files: dict[str, Path | None] = {}
        self.problem: str | None = None
        if record is not None:
            self.problem = self._check()

    def _inputs(self) -> dict[str, Path | None]:
        assert self.record is not None
        return {role: self.record.resolve(self.desk, role) for role in RECORD_FILES}

    def _check(self) -> str | None:
        for role, path in self._inputs().items():
            if path is None and role == "pre_bed":
                continue
            if path is None or not path.is_file():
                return f"the {COMPANION[role]} file its finish record names is gone ({path})"
        return None

    @property
    def active(self) -> bool:
        """A record was found and every file it names is on the desk."""

        return self.record is not None and self.problem is None

    def edit_companions(self, apply: Apply) -> None:
        """Apply the edit to every record file except the one the operator named (written first)."""

        if not self.active:
            return
        assert self.record is not None
        takes = self.desk / f"ep{self.record.episode:02d}" / "takes"
        base = f"take-ep{self.record.episode:02d}-{self.record.take_id}"
        done: dict[Path, Path] = {}
        for role, path in self._inputs().items():
            if path is None:
                self.files[role] = None
                continue
            resolved = path.resolve()
            if role == "pre_bed" and self.step in PICTURE_ONLY:
                self.files[role] = resolved
                continue
            if resolved == self.source:
                continue
            if resolved not in done:
                out = next_versioned_path(
                    takes, f"{base}-{self.step}-{COMPANION[role]}", ".mp4"
                )
                apply(resolved, out)
                done[resolved] = out
            self.files[role] = done[resolved]

    def write(self, output: Path, edit: dict[str, Any]) -> Path | None:
        """Write the carried record once the named file is edited into ``output``."""

        if not self.active:
            return None
        assert self.record is not None
        for role, path in self._inputs().items():
            if path is not None and path.resolve() == self.source:
                if role == "pre_bed" and self.step in PICTURE_ONLY:
                    continue
                self.files[role] = output
        return carry_finish_record(self.desk, self.record, files=self.files, edit=edit)

    def lines(self, record: Path | None) -> list[str]:
        """What the operator is told about the record."""

        if self.record is None:
            return []
        if record is None:
            return [
                f"- No finish record for the edited file: {self.problem}. `join` refuses it; run finish "
                "again, then edit the new finished file."
            ]
        sound = (
            "the sound before the bed is unchanged"
            if self.step in PICTURE_ONLY
            else "the take before the bed was edited the same way"
        )
        return [
            f"- Record: `{record.name}` (what `join` reads; {sound}; the un-marked master too)"
        ]


def _take_args(parser: argparse.ArgumentParser, *, take_file_help: str) -> None:
    parser.add_argument("--desk", type=Path, required=True)
    parser.add_argument("--episode", type=int, default=1)
    parser.add_argument(
        "--take",
        dest="take_id",
        default=None,
        help="Take id (default: the take --take-file names, else t1).",
    )
    parser.add_argument("--take-file", type=Path, default=None, help=take_file_help)


def add_edit_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the local edit subcommands.

    Parameters
    ----------
    sub
        ``fictora-produce`` subparsers.
    """

    raw_default = "Default: the newest raw take."
    deb = sub.add_parser(
        "deboard",
        help="Replace the storyboard frames a take opens on with its first real frame (measured against the "
        "board, cap 12; length and sound unchanged). finish runs this first; no board frames = nothing written.",
    )
    _take_args(deb, take_file_help=raw_default)
    deb.add_argument(
        "--board",
        type=Path,
        default=None,
        help="Default: the take's approved board on the desk.",
    )
    deb.add_argument("--max-frames", type=int, default=BOARD_LEAK_MAX_FRAMES)

    trim = sub.add_parser(
        "trim",
        help="Cut A-B seconds out of a FINISHED take on the real shot change (each edge snapped within 0.1 s), "
        "frame-accurate; prints how far later cues, lines and captions move.",
    )
    _take_args(
        trim,
        take_file_help="The finished take to cut (required: finish the raw take first).",
    )
    trim.add_argument(
        "--cut", required=True, help="A-B seconds on the take, e.g. 10.17-12.15."
    )
    trim.add_argument(
        "--cues-json", type=Path, action="append", default=[],
        help="A cues or captions JSON list [{start, end?, ...}] to shift: writes <stem>-trim-vN.json (repeat).",
    )  # fmt: skip

    frz = sub.add_parser(
        "freeze",
        help="Comic freeze-frame: hold the frame at --at for --hold seconds over the picture that was there. "
        "Same length, sound copied, so lines, cues and captions stay put.",
    )
    _take_args(frz, take_file_help=raw_default)
    frz.add_argument(
        "--at",
        type=float,
        required=True,
        help="Seconds into the take: the frame to hold.",
    )
    frz.add_argument(
        "--hold",
        type=float,
        required=True,
        help="Seconds to hold it (0.6 is a comic beat).",
    )

    tempo = sub.add_parser(
        "tempo",
        help="Change the speed of picture and sound together, pitch kept (0.9 = 10%% slower). Run it on the "
        "FINISHED take: finish lays the take's effects at the filmed times.",
    )
    _take_args(
        tempo,
        take_file_help="The take to change (the finished take). Default: the newest raw take.",
    )
    tempo.add_argument(
        "--factor", type=float, default=SLOW_TEMPO, help="0.5-2.0; default 0.9."
    )

    soft = sub.add_parser(
        "soften",
        help="Hold-and-fade 0.33 s at each hard cut (tblend trace), or at each --cut. Same length, sound copied. "
        "Deboard first; then finish --take-file the softened file.",
    )
    _take_args(soft, take_file_help=raw_default)
    soft.add_argument(
        "--cut",
        type=float,
        action="append",
        default=[],
        help="Cut time in seconds (repeat).",
    )

    blur = sub.add_parser(
        "blur",
        help="PATCH: Gaussian-blur boxes of the picture (garbled readable text the video invented, e.g. a sign) "
        "from --from to --to seconds. Same length, sound copied. Run it on the FINISHED take; report it.",
    )
    _take_args(
        blur,
        take_file_help="The take to blur (the finished take). Default: the newest raw take.",
    )
    blur.add_argument(
        "--box", dest="boxes", type=parse_box, action="append", required=True,
        help="x,y,w,h in pixels of the take (top-left corner, width, height); must lie inside the frame (repeat).",
    )  # fmt: skip
    blur.add_argument(
        "--from", dest="start", type=float, required=True, help="Seconds: blur starts."
    )
    blur.add_argument(
        "--to", dest="end", type=float, required=True, help="Seconds: blur ends."
    )
    blur.add_argument(
        "--strength", type=float, default=BLUR_SIGMA,
        help=f"Blur sigma in pixels (default {BLUR_SIGMA:g}).",
    )  # fmt: skip
    blur.add_argument(
        "--feather", type=int, default=0,
        help="Pixels of soft edge OUTSIDE each box (default 0, a hard edge). The box itself stays fully blurred.",
    )  # fmt: skip


_TAKE_IN_NAME = re.compile(r"^take-ep(\d+)-(t\d+)(?:-|\.|$)")


def take_id_for(args: argparse.Namespace) -> str:
    """The take an edit writes under: ``--take``, else the take ``--take-file`` names, else ``t1``.

    A trimmed take 2 used to be written as ``t1`` because ``--take`` defaulted to
    t1 whatever file was passed.

    Raises
    ------
    ValueError
        When ``--take`` and the file name disagree.
    """

    named = None
    if args.take_file is not None:
        match = _TAKE_IN_NAME.match(Path(args.take_file).name)
        named = match.group(2) if match else None
    if args.take_id is None:
        return named or "t1"
    if named is not None and named != args.take_id:
        raise ValueError(
            f"--take {args.take_id} but --take-file `{Path(args.take_file).name}` is {named}; "
            "drop --take (the file names its take) or pass the right file"
        )
    return str(args.take_id)


def _source(args: argparse.Namespace, desk: Path) -> Path:
    if args.take_file is not None:
        path = args.take_file.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"take not found: {path}")
        return path
    return latest_raw_take(desk, args.episode, args.take_id)


def dispatch_edit(args: argparse.Namespace, *, stream: TextIO | None = None) -> int:
    """Run one local edit command.

    Parameters
    ----------
    args
        Parsed namespace whose ``command`` is in :data:`EDIT_COMMANDS`.
    stream
        Where the report goes (stdout by default).

    Returns
    -------
    int
        ``0``.

    Raises
    ------
    ValueError
        When the command is unknown, or a tool refuses its input.
    FileNotFoundError
        When the take or the board is missing.
    """

    out = stream or sys.stdout
    desk = args.desk.expanduser().resolve()
    args.take_id = take_id_for(args)
    run_dir = desk / f"ep{args.episode:02d}"
    takes = run_dir / "takes"
    base = f"take-ep{args.episode:02d}-{args.take_id}"
    if args.command == "trim" and args.take_file is None:
        raise ValueError(
            "trim needs --take-file: finish the raw take first, then trim the finished file"
        )
    source = _source(args, desk)
    # A finished file with the episode cover attached keeps it through the edit, and says so in its name.
    covered = "-cover" if video_streams(source)[1] is not None else ""

    def target(step: str) -> Path:
        takes.mkdir(parents=True, exist_ok=True)
        return next_versioned_path(takes, f"{base}-{step}{covered}", ".mp4")

    carry = RecordCarry(
        desk,
        record_for_file(desk, source) if args.command in CARRIED else None,
        source,
        args.command,
    )
    lines: list[str]
    if args.command == "deboard":
        board = (
            args.board.expanduser().resolve()
            if args.board
            else approved_board(desk, args.episode, args.take_id)
        )
        if board is None:
            raise FileNotFoundError(
                f"no board for {args.take_id} on the desk; pass --board"
            )
        result = deboard(source, board, target("deboard"), max_frames=args.max_frames)
        if result.output is not None:
            record_edit(desk, op="deboard", source=source, output=result.output)
        lines = [f"Deboard `{source.name}` against `{board.name}`: {result.one_line()}"]
    elif args.command == "trim":
        first, after, fps, before = plan_trim(source, parse_cut(args.cut))

        def cut_same(take: Path, out: Path) -> Path:
            rate = probe_video(take).fps or fps
            return cut_frames(
                take,
                out,
                begin=round(first.frame / fps * rate),
                stop=round(after.frame / fps * rate),
                fps=rate,
            )

        carry.edit_companions(cut_same)
        trimmed = TrimResult(
            source=source,
            output=cut_frames(
                source, target("trim"), begin=first.frame, stop=after.frame, fps=fps
            ),
            start=first,
            end=after,
            fps=fps,
            duration_before=before,
        )
        record_edit(
            desk, op="trim", source=source, output=trimmed.output,
            cut=[first.seconds, after.seconds],
        )  # fmt: skip
        record = carry.write(
            trimmed.output,
            {"op": "trim", "cut": [first.seconds, after.seconds],
             "frames": [first.frame, after.frame], "fps": fps},
        )  # fmt: skip
        lines = [
            f"Trim `{source.name}` -> `{trimmed.output.name}`",
            f"- cut starts: {trimmed.start.one_line()}",
            f"- cut ends:   {trimmed.end.one_line()}",
            f"- {trimmed.shift_line()}",
            *carry.lines(record),
        ]
        for path in args.cues_json:
            shifted, notes = shift_json_file(
                path, trimmed.start.seconds, trimmed.end.seconds
            )
            lines.append(f"- shifted {path.name} -> `{shifted.name}`")
            lines += [f"  - {note}" for note in notes]
        lines.append("Next: watch the first frame after the cut at full size.")
    elif args.command == "freeze":

        def freeze_same(take: Path, out: Path) -> object:
            return freeze_frame(take, out, at=args.at, hold=args.hold)

        carry.edit_companions(freeze_same)
        frozen = freeze_frame(source, target("freeze"), at=args.at, hold=args.hold)
        record_edit(
            desk, op="freeze", source=source, output=frozen.output,
            at=frozen.at_seconds, hold=frozen.hold_seconds,
        )  # fmt: skip
        record = carry.write(
            frozen.output,
            {"op": "freeze", "at": frozen.at_seconds, "hold": frozen.hold_seconds},
        )
        lines = [f"`{source.name}`: {frozen.one_line()}", *carry.lines(record)]
    elif args.command == "tempo":

        def tempo_same(take: Path, out: Path) -> Path:
            return change_tempo(take, out, factor=args.factor)

        carry.edit_companions(tempo_same)
        slowed = change_tempo(source, target("tempo"), factor=args.factor)
        record_edit(desk, op="tempo", source=source, output=slowed, factor=args.factor)
        record = carry.write(slowed, {"op": "tempo", "factor": args.factor})
        lines = [
            f"Tempo {args.factor:g}x `{source.name}` -> `{slowed.name}`: every time on the old file is now "
            f"time / {args.factor:g}.",
            *carry.lines(record),
        ]
    elif args.command == "soften":
        cuts = tuple(args.cut) or measure_cuts(source)
        if not cuts:
            lines = [f"No hard cuts in `{source.name}` (tblend trace); nothing written"]
        else:

            def soften_same(take: Path, out: Path) -> Path:
                return soften_seams(take, out, cuts)

            carry.edit_companions(soften_same)
            softened = soften_seams(source, target("soften"), cuts)
            record_edit(
                desk, op="soften", source=source, output=softened, cuts=list(cuts)
            )
            record = carry.write(softened, {"op": "soften", "cuts": list(cuts)})
            found = "given" if args.cut else "found"
            lines = [
                f"Softened {len(cuts)} cut(s) {found} at {', '.join(f'{c:.2f}s' for c in cuts)} in `{source.name}` "
                f"-> `{softened.name}` (hold-and-fade 0.33 s; length and sound unchanged)",
                *carry.lines(record),
            ]
    elif args.command == "blur":
        boxes = tuple(args.boxes)

        def blur_same(take: Path, out: Path) -> BlurResult:
            return blur_boxes(take, out, boxes, start=args.start, end=args.end,
                              strength=args.strength, feather=args.feather)  # fmt: skip

        carry.edit_companions(blur_same)
        blurred = blur_same(source, target("blur"))
        detail: dict[str, Any] = {
            "boxes": [box.as_list() for box in blurred.boxes],
            "from": blurred.start_seconds,
            "to": blurred.end_seconds,
            "strength": blurred.strength,
            "feather": blurred.feather,
        }
        record_edit(desk, op="blur", source=source, output=blurred.output, **detail)
        record = carry.write(blurred.output, {"op": "blur", **detail})
        lines = [
            f"Blur `{source.name}`: {blurred.one_line()}",
            "- PATCH, not a fix: this hides text the video invented; report it (learnings / the PR) so the "
            "source stops making it.",
            *carry.lines(record),
        ]
    else:
        raise ValueError(f"unknown command {args.command}")
    if (run_dir / "run-notes.md").is_file():
        append_run_note(run_dir, "\n".join(lines))
    for line in lines:
        print(line, file=out)
    return 0
