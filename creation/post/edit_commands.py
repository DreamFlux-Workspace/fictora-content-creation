"""``fictora-produce`` take edit commands: trim, freeze, tempo, soften, blur, zoom (and ``deboard``, gone).

Every one is a take edit on the server, and none edits a file on this laptop
(server-heavy rule, 15 Oct 2026; gap 2 of the 5 Nov audit: every final video
is made and saved on the server). ``trim --start/--end`` sets the take's
handles (:mod:`creation.post.take_handles`), ``tempo`` its speed
(:mod:`creation.post.take_tempo`); ``trim --cut``, ``freeze``, ``soften``,
``blur`` and ``zoom`` go to the take's post edit (:mod:`creation.post.take_edit`).
The server stores the edited take, records it on the episode and joins the
episode again with it, and every later finish and join keeps it. Name the take
with ``--episode N --take tK``; times are seconds on the take as filmed (take
facts times).

``deboard`` is gone: the server cuts the storyboard frames off the start of
every take itself, and ``trim --start S`` holds a start it left as filmed.

:class:`RecordCarry` is kept for the trim handles' finish record.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from creation.ops.folder import next_versioned_path
from creation.post.edit import SLOW_TEMPO, ZOOM_FACTOR
from creation.post.finish_record import (
    RECORD_FILES,
    FinishRecord,
    carry_finish_record,
)
from creation.post.take_edit import (
    NOTHING_CHANGED,
    TakeEditRequest,
    TakeEditStop,
    parse_blur_box,
    parse_center,
    parse_cut,
    run_take_edit,
)

EDIT_COMMANDS = frozenset(
    {"deboard", "trim", "freeze", "tempo", "soften", "blur", "zoom"}
)
#: Edits sent whole to the take's post edit on the server.
SERVER_EDITS = frozenset({"freeze", "soften", "blur", "zoom"})
#: Said for ``deboard``, which the server does itself.
DEBOARD_GONE = (
    "deboard is gone: the server cuts the storyboard frames off the start of every take itself, before the take "
    "plays. When it left a possible board frame at the start as filmed (take facts board_frames.unsure) and you "
    "looked and saw the board, set where the take starts on the server: trim --episode N --take tK --start S. "
    "Nothing was changed."
)
#: Edits that leave the sound as it was: the record keeps the same pre-bed take.
PICTURE_ONLY = frozenset({"freeze", "soften"})
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
        ``tempo``, ``freeze`` or ``soften``.
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
        #: What happened to the captions on the last :meth:`write` (``!!`` when they could not be carried).
        self.caption_line: str | None = None
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
        written = carry_finish_record(
            self.desk, self.record, files=self.files, edit=edit
        )
        self.caption_line = self._carry_captions(edit)
        return written

    def _carry_captions(self, edit: dict[str, Any]) -> str | None:
        """Carry the master's ``.ass`` onto the edited master, re-timed as the picture was (canary 7 Oct 2026).

        Later steps read a take's captions beside the record's master;
        without this an edit after finish left them uncaptioned.
        """

        from creation.post.edit_captions import captions_for_record, carry_ass

        assert self.record is not None
        before = self.record.resolve(self.desk, "master")
        after = self.files.get("master")
        if before is None or after is None or after.resolve() == before.resolve():
            return None
        source, notes = captions_for_record(self.desk, self.record)
        if source is None:
            if any(note.startswith("!!") for note in notes):
                return "\n".join(notes)
            return (
                f"- Captions: none beside the master `{before.name}`, so none on `{after.name}` "
                "(the take was finished without burned captions)"
            )
        carried = carry_ass(source, after.with_suffix(".ass"), [edit])
        return "\n".join([*notes, carried.line])

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
            f"- Record: `{record.name}` (what `join` reads; {sound}; the un-marked master too)",
            *([self.caption_line] if self.caption_line else []),
        ]


def _take_args(parser: argparse.ArgumentParser, *, take_file_help: str) -> None:
    parser.add_argument("--desk", type=Path, required=True)
    parser.add_argument(
        "--episode",
        type=int,
        default=None,
        help="Episode ordinal. Default: the episode --take-file names, else 1.",
    )
    parser.add_argument(
        "--take",
        dest="take_id",
        default=None,
        help="Take id (default: the take --take-file names, else t1).",
    )
    parser.add_argument("--take-file", type=Path, default=None, help=take_file_help)


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


def episode_for(args: argparse.Namespace) -> int:
    """The episode an edit writes under: the file name, unless ``--episode`` agrees.

    ``--episode`` used to default to 1, so a trim of ``take-ep03-…`` was written
    into ``ep01/takes``. A second trim then could not find the finish record,
    which still lived on the episode the file names.

    Parameters
    ----------
    args
        Parsed edit namespace.

    Returns
    -------
    int
        Episode ordinal.

    Raises
    ------
    ValueError
        When ``--episode`` and the file name disagree.
    """

    named = None
    if args.take_file is not None:
        match = _TAKE_IN_NAME.match(Path(args.take_file).name)
        named = int(match.group(1)) if match else None
    chosen = getattr(args, "episode", None)
    if chosen is None:
        return named if named is not None else 1
    if named is not None and named != chosen:
        raise ValueError(
            f"--episode {chosen} but --take-file `{Path(args.take_file).name}` is episode {named}; "
            "drop --episode (the file names its episode) or pass the right one"
        )
    return int(chosen)


def add_edit_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the take edit subcommands.

    Parameters
    ----------
    sub
        ``fictora-produce`` subparsers.
    """

    gone = sub.add_parser(
        "deboard",
        help="Gone: the server cuts the storyboard frames off the start of every take itself. To hold a start "
        "it left as filmed, set the take's start with trim --start S.",
    )
    # The old settings are still read, so an old command gets the answer, not a usage error.
    _take_args(gone, take_file_help=argparse.SUPPRESS)
    gone.add_argument("--board", default=None, help=argparse.SUPPRESS)
    gone.add_argument("--max-frames", default=None, help=argparse.SUPPRESS)
    gone.add_argument("--hold-unsure", action="store_true", help=argparse.SUPPRESS)
    server_edit_file = (
        "Not taken: this is a take edit on the server and edits no file. Name the take with --episode N "
        "--take tK; times are on the take as filmed."
    )
    preview_help = "Ask the server what the edit would cost (free); nothing is changed."
    trim = sub.add_parser(
        "trim",
        help="Two kinds, both saved on the server. --start S --end E (or --reset): set where the take starts and "
        "ends when it plays (the server's trim handles, fictora-drama #563; nothing billed). --cut A-B (with "
        "--episode N --take tK): a take edit that cuts A-B seconds out of the middle of the take, free; the server "
        "snaps each edge onto the shot change, moves later lines, sounds and captions up with the picture, "
        "re-joins the episode, and every later finish and join keeps it. Times are on the take as filmed (take "
        "facts times, the same as finish --mute).",
    )
    _take_args(trim, take_file_help=server_edit_file)
    trim.add_argument(
        "--cut", default=None,
        help="Take edit: A-B seconds on the take as filmed, e.g. 10.17-12.15. Added to the take's earlier edits.",
    )  # fmt: skip
    trim.add_argument(
        "--start", type=float, default=None,
        help="Handles: where the take starts playing, seconds on the take as filmed (take-facts times); "
        "alone, the end stays where it is.",
    )  # fmt: skip
    trim.add_argument(
        "--end", type=float, default=None,
        help="Handles: where it stops playing, seconds on the take as filmed (at least 1 s after --start); "
        "alone, the start stays where it is.",
    )  # fmt: skip
    trim.add_argument(
        "--reset", action="store_true",
        help="Handles: back to the automatic handles (just past the board frames the server held).",
    )  # fmt: skip
    trim.add_argument(
        "--preview", action="store_true",
        help="Print what would change (handles) or what the cut would cost (--cut, free); send nothing.",
    )  # fmt: skip

    frz = sub.add_parser(
        "freeze",
        help="A take edit on the server (with --episode N --take tK, free): hold the frame at --at for --hold "
        "seconds over the picture that was there. Same length, sound unchanged, so lines, cues and captions stay "
        "put. The server re-joins the episode and every later finish and join keeps it.",
    )
    _take_args(frz, take_file_help=server_edit_file)
    frz.add_argument(
        "--at", type=float, required=True, help="Seconds on the take as filmed: the frame to hold."
    )  # fmt: skip
    frz.add_argument(
        "--hold", type=float, required=True, help="Seconds to hold it (0.6 is a comic beat)."
    )  # fmt: skip
    frz.add_argument("--preview", action="store_true", help=preview_help)

    tempo = sub.add_parser(
        "tempo",
        help="Change a take's speed (0.9 = 10%% slower), on the server: picture and the whole sound together, "
        "pitch kept (lips stay on their words), and every time the take reports moves with it (take facts, "
        "captions, effects, trim handles, post edits). --from and --to speed only that window (the action).",
    )
    _take_args(tempo, take_file_help=server_edit_file)
    tempo.add_argument(
        "--factor", type=float, default=SLOW_TEMPO, help="0.5-2.0; default 0.9."
    )
    tempo.add_argument(
        "--from",
        dest="start",
        type=float,
        default=None,
        help="Seconds: the sped window starts. Requires --to. Lines inside it speed; put the window on the action.",
    )
    tempo.add_argument(
        "--to",
        dest="end",
        type=float,
        default=None,
        help="Seconds: the sped window ends. Requires --from.",
    )
    tempo.add_argument(
        "--preview",
        action="store_true",
        help="Print where each line goes at the new speed; change nothing.",
    )
    tempo.add_argument(
        "--revert",
        action="store_true",
        help="Put back what the take played before its tempo (the server keeps it).",
    )

    soft = sub.add_parser(
        "soften",
        help="A take edit on the server (with --episode N --take tK, free): at each --cut, hold the frame before "
        "the jump and fade it into the next shot over a third of a second. The server moves each one onto a shot "
        "change within a quarter second. Same length, sound unchanged.",
    )
    _take_args(soft, take_file_help=server_edit_file)
    soft.add_argument(
        "--cut", type=float, action="append", required=True,
        help="Seconds on the take as filmed where the jump is (repeat). The take facts list its shot changes.",
    )  # fmt: skip
    soft.add_argument("--preview", action="store_true", help=preview_help)

    blur = sub.add_parser(
        "blur",
        help="PATCH, a take edit on the server (with --episode N --take tK, free): blur boxes of the picture "
        "(garbled readable text the video invented, e.g. a sign) from --from to --to seconds on the take as "
        "filmed. The server re-joins the episode and every later finish and join keeps it. Report it.",
    )
    _take_args(blur, take_file_help=server_edit_file)
    blur.add_argument(
        "--box", dest="boxes", action="append", required=True, metavar="X,Y,W,H[@WIDTHxHEIGHT]",
        help="The box's top-left corner, width and height as fractions of the frame (0 to 1), or in pixels of a "
        "frame grab with its size after @, like 77,410,307,137@768x1366. At least 2%% of the frame each way, "
        "inside the frame (repeat).",
    )  # fmt: skip
    blur.add_argument(
        "--from", dest="start", type=float, required=True, help="Seconds: blur starts."
    )
    blur.add_argument(
        "--to", dest="end", type=float, required=True, help="Seconds: blur ends."
    )
    blur.add_argument("--preview", action="store_true", help=preview_help)

    zoom = sub.add_parser(
        "zoom",
        help="Punch-in, a take edit on the server (with --episode N --take tK, free): push in on --center from "
        "--from to --to seconds on the take as filmed, easing in and out. Hides a jump cut (punch in on the shot "
        "after it) or crops a stray element out of the edge of the frame, instead of a paid re-film. Same length, "
        "sound unchanged. The server re-joins the episode, captions and mark stay their size.",
    )
    _take_args(zoom, take_file_help=server_edit_file)
    zoom.add_argument(
        "--factor", type=float, default=ZOOM_FACTOR,
        help=f"How much closer, more than 1.0 and at most 2.0 (default {ZOOM_FACTOR:g}).",
    )  # fmt: skip
    zoom.add_argument(
        "--center", default=None, metavar="X,Y[@WIDTHxHEIGHT]",
        help="Where to push in: fractions of the frame (0.5,0.42 is the server's default, where faces sit), or "
        "pixels of a frame grab with its size after @, like 384,560@768x1366.",
    )  # fmt: skip
    zoom.add_argument(
        "--from", dest="start", type=float, required=True,
        help="Seconds: the punch-in starts (put it on the jump cut).",
    )  # fmt: skip
    zoom.add_argument(
        "--to", dest="end", type=float, required=True, help="Seconds: the punch-in ends."
    )  # fmt: skip
    zoom.add_argument("--preview", action="store_true", help=preview_help)


def _edit_request(args: argparse.Namespace) -> TakeEditRequest:
    """The command's settings as the server's take edit fields (format only; the server checks the rest)."""

    if args.command == "trim":
        if args.start is not None or args.end is not None or args.reset:
            raise TakeEditStop(
                "trim --cut cuts a stretch out of the take; --start/--end/--reset move where it starts and "
                f"ends. Run them as two commands. {NOTHING_CHANGED}"
            )
        return TakeEditRequest(trims=[parse_cut(args.cut)])
    if args.command == "blur":
        return TakeEditRequest(
            blurs=[parse_blur_box(raw, start=args.start, end=args.end) for raw in args.boxes]
        )  # fmt: skip
    if args.command == "freeze":
        return TakeEditRequest(
            freeze_frames=[{"at_seconds": args.at, "hold_seconds": args.hold}]
        )
    if args.command == "soften":
        return TakeEditRequest(
            soften=[{"at_seconds": cut, "snap_to_cut": True} for cut in args.cut]
        )
    zoom: dict[str, Any] = {
        "start_seconds": args.start,
        "end_seconds": args.end,
        "zoom": args.factor,
    }
    if args.center is not None:
        zoom["center_x"], zoom["center_y"] = parse_center(args.center)
    return TakeEditRequest(zooms=[zoom])


def send_server_edit(
    args: argparse.Namespace,
    request: Callable[[argparse.Namespace], TakeEditRequest],
    out: TextIO,
) -> int:
    """Send one take edit to the server and print its answer; no file on this laptop is edited.

    The server adds it to the take's current edit, re-joins the episode with it,
    and keeps it in every later finish and join (kit #316 finishes from the
    server's copy, so an edit made only on this laptop was lost).

    Parameters
    ----------
    args
        Parsed namespace with ``desk``, ``episode``, ``take_id``, ``preview`` and the command's settings.
    request
        Turns the settings into the server's fields.
    out
        Where the server's answer is printed.

    Returns
    -------
    int
        ``0`` when the server took (or priced) the edit; ``2`` when it was refused.
    """

    what = args.command if args.command != "trim" else "trim --cut"
    try:
        if getattr(args, "take_file", None) is not None:
            raise TakeEditStop(
                f"{what} is a take edit on the server now and edits no file: drop --take-file and name the take "
                "with --episode N --take tK. Times are seconds on the take as filmed (take facts times, the same "
                f"as finish --mute), not on a finished file. {NOTHING_CHANGED}"
            )
        if args.take_id is None or args.episode is None:
            raise TakeEditStop(
                f"{what} edits one take on the server: add --episode N --take tK. {NOTHING_CHANGED}"
            )
        lines = run_take_edit(
            args.desk,
            episode=args.episode,
            take_id=args.take_id,
            request=request(args),
            preview=bool(getattr(args, "preview", False)),
        )
    except TakeEditStop as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 2
    if args.command == "blur" and not args.preview:
        lines.append(
            "PATCH, not a fix: this hides text the video invented; report it (learnings / the PR) so the source "
            "stops making it.",
        )
    for line in lines:
        print(line, file=out)
    return 0


def dispatch_edit(args: argparse.Namespace, *, stream: TextIO | None = None) -> int:
    """Run one take edit command; every edit is made and saved on the server.

    Parameters
    ----------
    args
        Parsed namespace whose ``command`` is in :data:`EDIT_COMMANDS`.
    stream
        Where the report goes (stdout by default).

    Returns
    -------
    int
        ``0``; ``2`` when the server refused the edit, or for ``deboard`` (gone).

    Raises
    ------
    ValueError
        When the command is unknown, or ``trim`` names neither handles nor a cut.
    """

    out = stream or sys.stdout
    if args.command == "deboard":
        print(DEBOARD_GONE, file=sys.stderr)
        return 2
    if args.command in SERVER_EDITS or (
        args.command == "trim" and args.cut is not None
    ):
        return send_server_edit(args, _edit_request, out)
    desk = args.desk.expanduser().resolve()
    if args.take_file is not None:
        raise ValueError(
            f"{args.command} is set on the server and edits no file: drop --take-file and name the take with "
            "--episode N --take tK"
        )
    args.take_id = take_id_for(args)
    args.episode = episode_for(args)
    if args.command == "trim":
        if args.start is None and args.end is None and not args.reset:
            raise ValueError(
                "trim needs --start S and/or --end E (or --reset) for the take's handles, or --cut A-B to cut a "
                "stretch out of the take"
            )
        from creation.post.take_handles import run_take_trim

        return run_take_trim(
            desk,
            episode=args.episode,
            take_id=args.take_id,
            start_s=args.start,
            end_s=args.end,
            reset=args.reset,
            preview=args.preview,
            out=out,
        )
    if args.command == "tempo":
        # A take's tempo is set on the server: picture and sound change speed together and every
        # time the take reports moves with it, so captions land on time (L-20261014-20).
        from creation.post.take_tempo import run_take_tempo

        return run_take_tempo(
            desk,
            episode=args.episode,
            take_id=args.take_id,
            factor=args.factor,
            start_s=args.start,
            end_s=args.end,
            preview=bool(args.preview),
            revert=bool(args.revert),
            out=out,
        )
    raise ValueError(f"unknown command {args.command}")
