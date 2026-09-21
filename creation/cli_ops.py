"""CLI for review folders and series desks (no Fal enrol from folder commands alone)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Sequence

from creation.ops.floor import (
    add_episode,
    approve_board,
    approve_post,
    approve_script,
    approve_series_gate,
    default_desk_parent,
    init_series_desk,
    preflight_take,
    record_estimate,
    record_filmed,
    record_spend,
    record_verdict,
    set_handoff,
    set_take_lines,
    status_rows,
    write_queue,
)
from creation.ops.folder import DEFAULT_RUN_PARENT, init_run_folder, next_versioned_path
from creation.ops.luma import measure_board_luma
from creation.ops.notes import append_run_note
from creation.ops.state import parse_spoken_lines

PREFLIGHT_FAIL = 3


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one content-ops command."""

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    _add_folder_parsers(sub)
    _add_floor_parsers(sub)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return _dispatch(args)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2


def _add_folder_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    init_p = sub.add_parser("init", help="Create one dated run folder (single episode).")
    init_p.add_argument("--series", required=True, help="Series title.")
    init_p.add_argument("--parent", type=Path, default=DEFAULT_RUN_PARENT)
    init_p.add_argument("--date", default=None, help="Folder date YYYY-MM-DD.")

    luma_p = sub.add_parser("measure-board", help="Measure storyboard mean luma (local helper).")
    luma_p.add_argument("image", type=Path)
    luma_p.add_argument("--json", action="store_true")

    note_p = sub.add_parser("note", help="Append a block to run-notes.md.")
    note_p.add_argument("--run-dir", type=Path, required=True)
    note_p.add_argument("--body", required=True)

    ver_p = sub.add_parser("next-path", help="Print the next unused versioned filename.")
    ver_p.add_argument("--dir", type=Path, required=True)
    ver_p.add_argument("--stem", required=True)
    ver_p.add_argument("--suffix", required=True)


def _add_floor_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    desk_parent = default_desk_parent()
    series_p = sub.add_parser("init-series", help="Create a series desk with parallel episode slots.")
    series_p.add_argument("--series", required=True)
    series_p.add_argument("--band", required=True, choices=("15s", "30s", "60s"))
    series_p.add_argument("--episodes", type=int, required=True)
    series_p.add_argument("--parent", type=Path, default=desk_parent)
    series_p.add_argument("--date", default=None)
    series_p.add_argument(
        "--continuing",
        action="store_true",
        help="This desk reuses an existing cast (continuing envelope).",
    )

    sub.add_parser("add-episode", help="Open one more episode slot.").add_argument("--desk", type=Path, required=True)

    status_p = sub.add_parser("status", help="Print the review queue.")
    status_p.add_argument("--desk", type=Path, required=True)
    status_p.add_argument("--json", action="store_true")

    lines_p = sub.add_parser("set-lines", help="Replace lines on one take. Does not approve.")
    lines_p.add_argument("--desk", type=Path, required=True)
    lines_p.add_argument("--episode", type=int, required=True)
    lines_p.add_argument("--take", required=True)
    lines_p.add_argument("--lines-json", required=True, help="JSON list of {speaker, original, translation}.")

    ap = sub.add_parser("approve", help="Record a human yes on a gate.")
    ap.add_argument("--desk", type=Path, required=True)
    ap.add_argument("--gate", required=True, choices=("look", "plates", "script", "board", "post"))
    ap.add_argument("--episode", type=int, default=None)
    ap.add_argument("--take", default=None)
    ap.add_argument("--path", type=Path, default=None)

    est = sub.add_parser("estimate", help="Record the priced batch before take enrol.")
    est.add_argument("--desk", type=Path, required=True)
    est.add_argument("--episode", type=int, required=True)
    est.add_argument("--take", required=True)
    est.add_argument("--usd", type=float, required=True)

    spend = sub.add_parser("spend", help="Add a paid unit to the ledger.")
    spend.add_argument("--desk", type=Path, required=True)
    spend.add_argument("--episode", type=int, required=True)
    spend.add_argument("--usd", type=float, required=True)
    spend.add_argument("--take", default=None)

    ho = sub.add_parser("handoff", help="Pin the previous last frame on this take.")
    ho.add_argument("--desk", type=Path, required=True)
    ho.add_argument("--episode", type=int, required=True)
    ho.add_argument("--take", required=True)
    ho.add_argument("--path", type=Path, required=True)

    pf = sub.add_parser("preflight", help="Block take enrol unless runbook checks pass.")
    pf.add_argument("--desk", type=Path, required=True)
    pf.add_argument("--episode", type=int, required=True)
    pf.add_argument("--take", required=True)
    pf.add_argument("--json", action="store_true")

    filmed = sub.add_parser("filmed", help="Mark that one take film came back.")
    filmed.add_argument("--desk", type=Path, required=True)
    filmed.add_argument("--episode", type=int, required=True)
    filmed.add_argument("--take", required=True)

    verd = sub.add_parser("verdict", help="Record Use it or Change this.")
    verd.add_argument("--desk", type=Path, required=True)
    verd.add_argument("--episode", type=int, required=True)
    verd.add_argument("--take", required=True)
    verd.add_argument("--use", action="store_true")
    verd.add_argument("--change", action="store_true")
    verd.add_argument("--cause", default=None)


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "init":
        day = date.fromisoformat(args.date) if args.date else date.today()
        print(init_run_folder(args.parent, args.series, day=day))
        return 0
    if args.command == "measure-board":
        report = measure_board_luma(args.image)
        if args.json:
            print(
                json.dumps(
                    {
                        "path": str(report.path),
                        "mean_percent": round(report.mean_percent, 2),
                        "below_dim_floor": report.below_dim_floor,
                        "in_interior_band": report.in_interior_band,
                        "line": report.one_line(),
                    }
                )
            )
        else:
            print(report.one_line())
        return 0
    if args.command == "note":
        print(append_run_note(args.run_dir, args.body))
        return 0
    if args.command == "next-path":
        print(next_versioned_path(args.dir, args.stem, args.suffix))
        return 0
    if args.command == "init-series":
        day = date.fromisoformat(args.date) if args.date else date.today()
        desk = init_series_desk(
            args.parent,
            args.series,
            band=args.band,
            episode_count=args.episodes,
            day=day,
            continuing=args.continuing,
        )
        print(desk)
        return 0
    if args.command == "add-episode":
        episode = add_episode(args.desk)
        print(episode.slug)
        return 0
    if args.command == "status":
        rows = status_rows(args.desk)
        write_queue(args.desk)
        if args.json:
            print(json.dumps(rows))
        else:
            for row in rows:
                print(
                    f"{row['episode']}  waiting={row['waiting']}  "
                    f"preflight={row['preflight']}  {row['spend']} of {row['envelope']}"
                )
        return 0
    if args.command == "set-lines":
        payload = json.loads(args.lines_json)
        if not isinstance(payload, list):
            raise ValueError("lines-json must be a JSON list")
        take = set_take_lines(
            args.desk,
            episode=args.episode,
            take_id=args.take,
            lines=parse_spoken_lines(payload),
        )
        print(f"{args.take} {len(take.lines)} lines (not approved)")
        return 0
    if args.command == "approve":
        return _dispatch_approve(args)
    if args.command == "estimate":
        take = record_estimate(args.desk, episode=args.episode, take_id=args.take, usd=args.usd)
        print(f"{args.take} estimate ${take.estimate_usd:.2f}")
        return 0
    if args.command == "spend":
        series = record_spend(args.desk, episode=args.episode, usd=args.usd, take_id=args.take)
        print(f"series spend ${series.spend_usd:.2f}")
        return 0
    if args.command == "handoff":
        take = set_handoff(args.desk, episode=args.episode, take_id=args.take, image=args.path)
        print(take.handoff_path)
        return 0
    if args.command == "preflight":
        report = preflight_take(args.desk, episode=args.episode, take_id=args.take)
        if args.json:
            print(
                json.dumps(
                    {
                        "passed": report.passed,
                        "line": report.one_line(),
                        "checks": [{"code": c.code, "ok": c.ok, "detail": c.detail} for c in report.checks],
                    }
                )
            )
        else:
            print(report.one_line())
            for check in report.checks:
                mark = "ok" if check.ok else "FAIL"
                print(f"  {mark}  {check.code}: {check.detail}")
        return 0 if report.passed else PREFLIGHT_FAIL
    if args.command == "filmed":
        take = record_filmed(args.desk, episode=args.episode, take_id=args.take)
        print(f"{args.take} filmed_count={take.filmed_count}")
        return 0
    if args.command == "verdict":
        if args.use == args.change:
            raise ValueError("pass exactly one of --use or --change")
        take = record_verdict(
            args.desk,
            episode=args.episode,
            take_id=args.take,
            verdict="use" if args.use else "change",
            cause=args.cause,
        )
        print(f"{args.take} verdict={take.verdict}")
        return 0
    return 1


def _dispatch_approve(args: argparse.Namespace) -> int:
    if args.gate in {"look", "plates"}:
        record = approve_series_gate(
            args.desk,
            args.gate,
            path=str(args.path) if args.path else None,
        )
        print(f"{args.gate} {record.status}")
        return 0
    if args.episode is None:
        raise ValueError(f"--episode is required for {args.gate}")
    if args.gate == "script":
        record = approve_script(args.desk, episode=args.episode)
        print(f"ep{args.episode:02d} script {record.status}")
        return 0
    if args.gate == "post":
        record = approve_post(
            args.desk,
            episode=args.episode,
            path=str(args.path) if args.path else None,
        )
        print(f"ep{args.episode:02d} post {record.status}")
        return 0
    if args.take is None or args.path is None:
        raise ValueError("board approve needs --take and --path")
    record = approve_board(args.desk, episode=args.episode, take_id=args.take, image=args.path)
    print(f"{args.take} board {record.status}  {record.note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
