"""``fictora-produce`` take edit commands: trim, freeze, tempo, soften, blur, zoom (and ``deboard``, gone)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from creation.post.take_edit import (
    NOTHING_CHANGED,
    TakeEditRequest,
    TakeEditStop,
    build_request,
    parse_blur_box,
    parse_center,
    parse_cut,
    parse_voice_fx,
    run_take_edit,
)

EDIT_COMMANDS = frozenset(
    {"deboard", "trim", "freeze", "tempo", "soften", "blur", "zoom"}
)
DEBOARD_GONE = (
    "deboard is gone: the server cuts the storyboard frames off the start of every take itself, before the take "
    "plays. When it left a possible board frame at the start as filmed (take facts board_frames.unsure) and you "
    "looked and saw the board, set where the take starts on the server: trim --episode N --take tK --start S. "
    "Nothing was changed."
)
#: The slow cut some shows use.
SLOW_TEMPO = 0.9
#: Default punch-in: the picture 15% closer (a crop of 1/1.15 of each side), enough to read as a new angle.
ZOOM_FACTOR = 1.15


def _take_args(parser: argparse.ArgumentParser, *, take_file_help: str) -> None:
    parser.add_argument("--desk", type=Path, required=True)
    parser.add_argument(
        "--episode",
        type=int,
        default=None,
        help="Episode ordinal.",
    )
    parser.add_argument(
        "--take",
        dest="take_id",
        default=None,
        help="Take id, like t2.",
    )
    parser.add_argument("--take-file", type=Path, default=None, help=take_file_help)


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


def _trim_cut(args: argparse.Namespace) -> TakeEditRequest:
    if args.start is not None or args.end is not None or args.reset:
        raise TakeEditStop(
            "trim --cut cuts a stretch out of the take; --start/--end/--reset move where it starts and "
            f"ends. Run them as two commands. {NOTHING_CHANGED}"
        )
    return TakeEditRequest(trims=[parse_cut(args.cut)])


def _zoom(args: argparse.Namespace) -> TakeEditRequest:
    zoom: dict[str, Any] = {
        "start_seconds": args.start,
        "end_seconds": args.end,
        "zoom": args.factor,
    }
    if args.center is not None:
        zoom["center_x"], zoom["center_y"] = parse_center(args.center)
    return TakeEditRequest(zooms=[zoom])


#: Each take edit command's settings as the server's take edit fields (format only; the server checks the rest).
POST_EDIT_REQUESTS: dict[str, Callable[[argparse.Namespace], TakeEditRequest]] = {
    "trim": _trim_cut,
    "blur": lambda args: TakeEditRequest(
        blurs=[parse_blur_box(raw, start=args.start, end=args.end) for raw in args.boxes]
    ),
    "freeze": lambda args: TakeEditRequest(
        freeze_frames=[{"at_seconds": args.at, "hold_seconds": args.hold}]
    ),
    "soften": lambda args: TakeEditRequest(
        soften=[{"at_seconds": cut, "snap_to_cut": True} for cut in args.cut]
    ),
    "zoom": _zoom,
    "voice-fx": lambda args: TakeEditRequest(
        voice_fx=[parse_voice_fx(raw, preset=args.preset) for raw in args.span]
    ),
    "revoice": lambda args: build_request(
        desk=args.desk.expanduser().resolve(), episode=args.episode, voices=[args.cast]
    ),
}  # fmt: skip


def _take_stop(args: argparse.Namespace, what: str) -> str | None:
    """Why the command cannot name one take on the server (an old file flag, or a missing one), or ``None``."""

    if getattr(args, "take_file", None) is not None:
        flag = "--file" if args.command == "voice-fx" else "--take-file"
        return (
            f"{what} is a take edit on the server now and edits no file: drop {flag} and name the take "
            "with --episode N --take tK. Times are seconds on the take as filmed (take facts times, the same "
            f"as finish --mute), not on a finished file. {NOTHING_CHANGED}"
        )
    missing = [
        flag
        for flag, value in (
            ("--desk D", args.desk),
            ("--episode N", args.episode),
            ("--take tK", args.take_id),
        )
        if value is None
    ]
    if missing:
        return f"{what} edits one take on the server: add {' '.join(missing)}. {NOTHING_CHANGED}"
    return None


def send_server_edit(args: argparse.Namespace, out: TextIO) -> int:
    """Send one take edit to the server and print its answer; no file on this laptop is edited.

    Parameters
    ----------
    args
        Parsed namespace whose ``command`` is in :data:`POST_EDIT_REQUESTS`, with ``desk``, ``episode``,
        ``take_id``, ``preview`` and the command's settings.
    out
        Where the server's answer is printed.

    Returns
    -------
    int
        ``0`` when the server took (or priced) the edit; ``2`` when it was refused.
    """

    what = args.command if args.command != "trim" else "trim --cut"
    try:
        stop = _take_stop(args, what)
        if stop is not None:
            raise TakeEditStop(stop)
        lines = run_take_edit(
            args.desk,
            episode=args.episode,
            take_id=args.take_id,
            request=POST_EDIT_REQUESTS[args.command](args),
            preview=bool(getattr(args, "preview", False)),
            yes=bool(getattr(args, "yes", False)),
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
        ``0``; ``2`` when the take is not named on the server, the server refused the edit, or for
        ``deboard`` (gone).

    Raises
    ------
    ValueError
        When the command is unknown, or ``trim`` names neither handles nor a cut.
    """

    out = stream or sys.stdout
    if args.command == "deboard":
        print(DEBOARD_GONE, file=sys.stderr)
        return 2
    if args.command in POST_EDIT_REQUESTS and (
        args.command != "trim" or args.cut is not None
    ):
        return send_server_edit(args, out)
    stop = _take_stop(args, args.command)
    if stop is not None:
        print(f"Stopped: {stop}", file=sys.stderr)
        return 2
    desk = args.desk.expanduser().resolve()
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
