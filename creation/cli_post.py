"""``fictora-produce`` local post commands: voice, revoice, set-bed, finish."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from creation.post.finish import FINISH_INCOMPLETE, run_finish
from creation.post.sfx import parse_adjustment

POST_COMMANDS = frozenset({"voice", "revoice", "set-bed", "finish"})


def add_post_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the local post subcommands.

    Parameters
    ----------
    sub
        ``fictora-produce`` subparsers.
    """

    voice = sub.add_parser(
        "voice",
        help="Change a character's voice (never regenerate): --audition renders 4-10 candidates on their real "
        "lines ($0.30 a set, rendered on the server); --pick N locks one on the cast card (free).",
    )
    voice.add_argument("--desk", type=Path, required=True)
    voice.add_argument("--cast", required=True, help="cast_id or name.")
    mode = voice.add_mutually_exclusive_group(required=True)
    mode.add_argument("--audition", action="store_true", help="Render a candidate set and list it.")
    mode.add_argument("--pick", type=int, default=None, help="Lock candidate N from the newest set.")
    voice.add_argument("--episode", type=int, default=None, help="Audition on this episode's lines only.")
    voice.add_argument("--count", type=int, default=8, help="Candidates, 4-10.")
    voice.add_argument("--cause", default=None, help="Why a second audition set is paid for (required for one).")

    revoice = sub.add_parser(
        "revoice",
        help="Replace one character's lines on a filmed take with their locked (newly picked) voice: mute the "
        "original, lay the new line in. Writes take-epNN-tK-revoice-vN.mp4. Then run finish --take-file on it.",
    )
    revoice.add_argument("--desk", type=Path, required=True)
    revoice.add_argument("--cast", required=True, help="cast_id or name.")
    revoice.add_argument("--episode", type=int, default=1)
    revoice.add_argument("--take", dest="take_id", default="t1")
    revoice.add_argument("--take-file", type=Path, default=None, help="Default: the newest raw take.")
    revoice.add_argument("--words-json", type=Path, default=None, help="Saved Whisper words; default: transcribe.")
    revoice.add_argument("--voice-db", type=float, default=0.0, help="Gain on the new lines.")

    bed = sub.add_parser("set-bed", help="Pin the show's music bed on the desk (a file you chose).")
    bed.add_argument("--desk", type=Path, required=True)
    bed.add_argument("--path", type=Path, required=True, help="Audio file (licensed or made for the show).")

    fin = sub.add_parser(
        "finish",
        help="Finish an accepted take on this laptop: SFX, music bed, colour match to the board, mix, captions, "
        f"Sokii mark. Exits {FINISH_INCOMPLETE} (NOT DONE) when music, SFX or the mix did not go on.",
    )
    fin.add_argument("--desk", type=Path, required=True)
    fin.add_argument("--episode", type=int, default=1)
    fin.add_argument("--take", dest="take_id", default="t1")
    fin.add_argument("--take-file", type=Path, default=None, help="Default: the newest raw take (or a revoice file).")
    fin.add_argument("--no-colour-match", action="store_true", help="Keep the take's own look.")
    fin.add_argument("--colour-strength", type=float, default=1.0, help="0..1 toward the board.")
    fin.add_argument("--bed-db", type=float, default=-16.5, help="Music bed level in the mix.")
    fin.add_argument("--music", default=None, help="Describe a new bed to make (replaces the pinned one).")
    fin.add_argument("--duck-db", type=float, default=None, help="Duck the bed exactly N dB (1-30) under the voice.")
    fin.add_argument("--sfx-adjust", action="append", default=[], help='"door=-6", "hum=drop", "shot:3=+4".')
    fin.add_argument("--line-start", type=float, action="append", default=None, help="Caption line start, per line.")
    fin.add_argument("--watermark-y", type=int, default=None, help="Mark top offset (never into the top 8%%).")
    fin.add_argument("--json", action="store_true", help="Print the report as JSON on stdout.")


def dispatch_post(args: argparse.Namespace) -> int:
    """Run one local post command.

    Parameters
    ----------
    args
        Parsed namespace whose ``command`` is in :data:`POST_COMMANDS`.

    Returns
    -------
    int
        ``0`` done; ``5`` finish NOT DONE.

    Raises
    ------
    ValueError
        When the command is unknown.
    """

    from creation.post.bed import pin_bed
    from creation.post.voice import run_revoice, run_voice_audition, run_voice_pick

    if args.command == "voice":
        if args.audition:
            run_voice_audition(args.desk, cast=args.cast, episode=args.episode, count=args.count, cause=args.cause)
        else:
            run_voice_pick(args.desk, cast=args.cast, pick=args.pick)
        return 0
    if args.command == "revoice":
        run_revoice(
            args.desk,
            cast=args.cast,
            episode=args.episode,
            take_id=args.take_id,
            take_file=args.take_file,
            words_json=args.words_json,
            voice_db=args.voice_db,
        )
        return 0
    if args.command == "set-bed":
        print(f"pinned show bed: {pin_bed(args.desk.expanduser().resolve(), args.path)}")
        return 0
    if args.command == "finish":
        result = run_finish(
            args.desk,
            episode=args.episode,
            take_id=args.take_id,
            take_file=args.take_file,
            colour=not args.no_colour_match,
            colour_strength=args.colour_strength,
            bed_db=args.bed_db,
            music=args.music,
            duck_db=args.duck_db,
            sfx_adjust=tuple(parse_adjustment(raw) for raw in args.sfx_adjust),
            line_starts=tuple(args.line_start) if args.line_start else None,
            watermark_y=args.watermark_y,
        )
        if args.json:
            print(json.dumps(result.as_json(), indent=2))
        else:
            print(result.final)
        return 0 if result.complete else FINISH_INCOMPLETE
    raise ValueError(f"unknown command {args.command}")
