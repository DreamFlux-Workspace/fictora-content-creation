"""``fictora-produce`` local edit commands: deboard, trim, freeze, tempo, soften.

Each reads one take on the desk (``--take-file``, else the newest raw take),
writes ``epNN/takes/take-epNN-tK-<step>-vN.mp4`` (never overwriting), appends
a run note and prints what it did. Free: ffmpeg and numpy on this laptop.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.post.deboard import BOARD_LEAK_MAX_FRAMES, deboard
from creation.post.desk import approved_board, latest_raw_take
from creation.post.edit import (
    SLOW_TEMPO,
    change_tempo,
    freeze_frame,
    measure_cuts,
    parse_cut,
    shift_json_file,
    soften_seams,
    trim_take,
)

EDIT_COMMANDS = frozenset({"deboard", "trim", "freeze", "tempo", "soften"})


def _take_args(parser: argparse.ArgumentParser, *, take_file_help: str) -> None:
    parser.add_argument("--desk", type=Path, required=True)
    parser.add_argument("--episode", type=int, default=1)
    parser.add_argument("--take", dest="take_id", default="t1")
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
    deb.add_argument("--board", type=Path, default=None, help="Default: the take's approved board on the desk.")
    deb.add_argument("--max-frames", type=int, default=BOARD_LEAK_MAX_FRAMES)

    trim = sub.add_parser(
        "trim",
        help="Cut A-B seconds out of a FINISHED take on the real shot change (each edge snapped within 0.1 s), "
        "frame-accurate; prints how far later cues, lines and captions move.",
    )
    _take_args(trim, take_file_help="The finished take to cut (required: finish the raw take first).")
    trim.add_argument("--cut", required=True, help="A-B seconds on the take, e.g. 10.17-12.15.")
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
    frz.add_argument("--at", type=float, required=True, help="Seconds into the take: the frame to hold.")
    frz.add_argument("--hold", type=float, required=True, help="Seconds to hold it (0.6 is a comic beat).")

    tempo = sub.add_parser(
        "tempo",
        help="Change the speed of picture and sound together, pitch kept (0.9 = 10%% slower). Run it on the "
        "FINISHED take: finish lays the take's effects at the filmed times.",
    )
    _take_args(tempo, take_file_help="The take to change (the finished take). Default: the newest raw take.")
    tempo.add_argument("--factor", type=float, default=SLOW_TEMPO, help="0.5-2.0; default 0.9.")

    soft = sub.add_parser(
        "soften",
        help="Hold-and-fade 0.33 s at each hard cut (tblend trace), or at each --cut. Same length, sound copied. "
        "Deboard first; then finish --take-file the softened file.",
    )
    _take_args(soft, take_file_help=raw_default)
    soft.add_argument("--cut", type=float, action="append", default=[], help="Cut time in seconds (repeat).")


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
    run_dir = desk / f"ep{args.episode:02d}"
    takes = run_dir / "takes"
    base = f"take-ep{args.episode:02d}-{args.take_id}"
    if args.command == "trim" and args.take_file is None:
        raise ValueError("trim needs --take-file: finish the raw take first, then trim the finished file")
    source = _source(args, desk)

    def target(step: str) -> Path:
        takes.mkdir(parents=True, exist_ok=True)
        return next_versioned_path(takes, f"{base}-{step}", ".mp4")

    lines: list[str]
    if args.command == "deboard":
        board = args.board.expanduser().resolve() if args.board else approved_board(desk, args.episode, args.take_id)
        if board is None:
            raise FileNotFoundError(f"no board for {args.take_id} on the desk; pass --board")
        result = deboard(source, board, target("deboard"), max_frames=args.max_frames)
        lines = [f"Deboard `{source.name}` against `{board.name}`: {result.one_line()}"]
    elif args.command == "trim":
        trimmed = trim_take(source, parse_cut(args.cut), target("trim"))
        lines = [
            f"Trim `{source.name}` -> `{trimmed.output.name}`",
            f"- cut starts: {trimmed.start.one_line()}",
            f"- cut ends:   {trimmed.end.one_line()}",
            f"- {trimmed.shift_line()}",
        ]
        for path in args.cues_json:
            shifted, notes = shift_json_file(path, trimmed.start.seconds, trimmed.end.seconds)
            lines.append(f"- shifted {path.name} -> `{shifted.name}`")
            lines += [f"  - {note}" for note in notes]
        lines.append("Next: watch the first frame after the cut at full size.")
    elif args.command == "freeze":
        frozen = freeze_frame(source, target("freeze"), at=args.at, hold=args.hold)
        lines = [f"`{source.name}`: {frozen.one_line()}"]
    elif args.command == "tempo":
        slowed = change_tempo(source, target("tempo"), factor=args.factor)
        lines = [
            f"Tempo {args.factor:g}x `{source.name}` -> `{slowed.name}`: every time on the old file is now "
            f"time / {args.factor:g}."
        ]
    elif args.command == "soften":
        cuts = tuple(args.cut) or measure_cuts(source)
        if not cuts:
            lines = [f"No hard cuts in `{source.name}` (tblend trace); nothing written"]
        else:
            softened = soften_seams(source, target("soften"), cuts)
            found = "given" if args.cut else "found"
            lines = [
                f"Softened {len(cuts)} cut(s) {found} at {', '.join(f'{c:.2f}s' for c in cuts)} in `{source.name}` "
                f"-> `{softened.name}` (hold-and-fade 0.33 s; length and sound unchanged)"
            ]
    else:
        raise ValueError(f"unknown command {args.command}")
    if (run_dir / "run-notes.md").is_file():
        append_run_note(run_dir, "\n".join(lines))
    for line in lines:
        print(line, file=out)
    return 0
