"""``fictora-produce review``: the numbers-only read of one take, as one block for the verdict.

A read, never a gate: it exits 0 whatever it measures (2 only when the command
itself fails, like every other command). The block is also appended to the
episode's ``run-notes.md``.

After the block it prints the free edits to try before any re-film (salvage
first, :mod:`creation.post.salvage`). ``keep-take`` puts a decent rejected take
on the desk's reuse list (``shared/reuse-takes.jsonl``); ``keep-take --list``
prints it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

import httpx

REVIEW_COMMANDS = frozenset({"review", "keep-take"})


def add_review_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``review`` and ``keep-take``.

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
    review.add_argument(
        "--original", action="store_true",
        help="Also download the provider's original the server kept when it held board frames "
        "(take facts board_frames.original, fictora-drama #559), check its sha256 and length, and write a "
        "side-by-side of the held ends (takes/take-epNN-tK-original-compare-vN.mp4). Free.",
    )  # fmt: skip

    keep = sub.add_parser(
        "keep-take",
        help="Put a decent rejected take on the desk's reuse list (shared/reuse-takes.jsonl), with a note on "
        "what is good in it and where it could go later in the series. --list prints the list. Free, local.",
    )
    keep.add_argument("--desk", type=Path, required=True)
    keep.add_argument("--episode", type=int, default=1)
    keep.add_argument("--take", dest="take_id", default="t1")
    keep.add_argument(
        "--take-file", type=Path, default=None,
        help="The file kept. Default: the take's newest raw file.",
    )  # fmt: skip
    keep.add_argument(
        "--note", default=None, help="What is good in it and where it could go."
    )
    keep.add_argument(
        "--list", action="store_true", help="Print the reuse list; add nothing."
    )


def dispatch_keep_take(
    args: argparse.Namespace, *, stream: TextIO | None = None
) -> int:
    """Run ``keep-take``: add a take to the desk's reuse list, or print the list.

    Parameters
    ----------
    args
        Parsed namespace whose ``command`` is ``keep-take``.
    stream
        Where the report goes (stdout by default).

    Returns
    -------
    int
        ``0``.

    Raises
    ------
    ValueError
        When neither ``--note`` nor ``--list`` is given.
    """

    from creation.ops.notes import append_run_note
    from creation.post.salvage import keep_for_reuse, reuse_lines

    out = stream or sys.stdout
    desk = args.desk.expanduser().resolve()
    if args.list:
        for line in reuse_lines(desk):
            print(line, file=out)
        return 0
    entry = keep_for_reuse(desk, episode=args.episode, take_id=args.take_id, note=args.note or "",
                           take_file=args.take_file)  # fmt: skip
    file_text = f" `{entry['file']}`" if entry.get("file") else ""
    line = (
        f"Kept for reuse: ep{args.episode:02d} {args.take_id}{file_text}: {entry['note']} "
        "(shared/reuse-takes.jsonl; `keep-take --list` shows the list)"
    )
    run_dir = desk / f"ep{args.episode:02d}"
    if (run_dir / "run-notes.md").is_file():
        append_run_note(run_dir, line)
    print(line, file=out)
    return 0


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

    if getattr(args, "command", "review") == "keep-take":
        return dispatch_keep_take(args, stream=stream)

    from creation.ops.notes import append_run_note
    from creation.post.review import review_take
    from creation.post.salvage import free_fix_lines

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

        note = next_versioned_path(
            result.take.parent, f"{result.take.stem}-review", ".txt"
        )
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
        for line in free_fix_lines(
            desk, args.episode, args.take_id, take_file=result.take, indent=""
        ):
            print(line, file=out)
    if getattr(args, "original", False):
        from creation.post.desk import latest_raw_take
        from creation.post.sfx import saved_take_facts
        from creation.post.take_timeline import fetch_original

        facts_path = saved_take_facts(desk, args.episode, args.take_id)
        try:
            raw = latest_raw_take(desk, args.episode, args.take_id)
        except FileNotFoundError:
            raw = None
        try:
            copy = fetch_original(
                desk,
                episode=args.episode,
                take_id=args.take_id,
                payload=json.loads(facts_path.read_text(encoding="utf-8"))
                if facts_path
                else None,
                held_take=raw,
            )
        except (ValueError, OSError, httpx.HTTPError) as exc:
            print(f"original: not fetched: {exc}", file=out)
        else:
            for line in copy.lines():
                print(line, file=out)
            if (run_dir / "run-notes.md").is_file():
                append_run_note(run_dir, "Review · " + "; ".join(copy.lines()))
    return 0
