"""``fictora-produce review``: the numbers-only read of one take, as one block for the verdict.

A read, never a gate: it exits 0 whatever it measures (2 only when the command
itself fails, like every other command). The block is also appended to the
episode's ``run-notes.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

REVIEW_COMMANDS = frozenset({"review"})


def add_review_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``review``.

    Parameters
    ----------
    sub
        ``fictora-produce`` subparsers.
    """

    review = sub.add_parser(
        "review",
        help="Free, local, numbers-only read of one take (raw or finished): loudness, hard cuts vs the take "
        "facts, frozen/stacked frames, board frames anywhere, drawn text (subtitles the video burned in; needs tesseract), lines asked and heard, people per shot (take facts; counted "
        "by eye), and on a finished file the "
        "caption safe zones (zone sheet for the face check). Each section ✓/⚠ with its threshold; always exits 0.",
    )
    review.add_argument("--desk", type=Path, required=True)
    review.add_argument("--episode", type=int, default=1)
    review.add_argument("--take", dest="take_id", default="t1")
    review.add_argument(
        "--take-file", "--file", dest="take_file", type=Path, default=None,
        help="Default: the newest finished file for the take, else the newest raw take (the block says which).",
    )  # fmt: skip
    review.add_argument(
        "--board",
        type=Path,
        default=None,
        help="Default: the take's approved board on the desk.",
    )
    review.add_argument(
        "--words-json", type=Path, default=None,
        help="A saved Whisper transcript of the take; default: the newest take-epNN-tK-*words-vN.json on the desk.",
    )  # fmt: skip
    review.add_argument(
        "--transcribe", action="store_true",
        help="When no transcript is saved, ask the server for one from the take's stored URL (a few cents).",
    )  # fmt: skip
    review.add_argument(
        "--json", action="store_true", help="Print the review as JSON on stdout."
    )


def dispatch_review(args: argparse.Namespace, *, stream: TextIO | None = None) -> int:
    """Run ``review`` and print its block (or JSON).

    Parameters
    ----------
    args
        Parsed namespace whose ``command`` is ``review``.
    stream
        Where the report goes (stdout by default).

    Returns
    -------
    int
        ``0``, whatever was measured.
    """

    from creation.ops.notes import append_run_note
    from creation.post.review import review_take

    out = stream or sys.stdout
    desk = args.desk.expanduser().resolve()
    result = review_take(
        desk,
        episode=args.episode,
        take_id=args.take_id,
        take_file=args.take_file,
        board=args.board,
        words_json=args.words_json.expanduser().resolve() if args.words_json else None,
        transcribe=args.transcribe,
    )
    run_dir = desk / f"ep{args.episode:02d}"
    reels = (desk / "reels").resolve()
    if result.take.is_relative_to(reels):
        # A reel never touches an existing desk file: its review goes in a new file beside it.
        from creation.ops.folder import next_versioned_path

        note = next_versioned_path(reels, f"{result.take.stem}-review", ".txt")
        with note.open("x", encoding="utf-8") as handle:
            handle.write(result.block() + "\n")
        print(
            f"(review saved to {note}; run-notes.md is not touched for a reel)",
            file=out,
        )
    elif (run_dir / "run-notes.md").is_file():
        append_run_note(run_dir, result.block())
    if args.json:
        print(json.dumps(result.as_json(), indent=2, ensure_ascii=False), file=out)
    else:
        print(result.block(), file=out)
    return 0
