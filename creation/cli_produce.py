"""CLI: orchestrate episode production on a series desk."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from creation.captions import caption_take, find_ffmpeg
from creation.cli_config import add_production_config_args, config_from_args
from creation.orchestrate import approve_gate, bind_desk, run_step, status_message
from creation.production_config import load_production_config, save_production_config
from creation.recover import cancel_video_job, prepare_video_retry
from creation.ops.floor import init_series_desk
from creation.ops.folder import DEFAULT_RUN_PARENT
from creation.ops.notes import append_run_note


def _warn_if_no_local_ffmpeg() -> None:
    """Warn at desk creation when this machine cannot burn local captions."""

    try:
        find_ffmpeg()
    except RuntimeError as exc:
        print(f"WARNING: {exc}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch fictora-produce commands."""

    parser = argparse.ArgumentParser(description="Orchestrate Drama API production on a content desk.")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Create desk + bind prompt (does not call API yet).")
    start.add_argument("--series", required=True)
    start.add_argument("--prompt", required=True)
    start.add_argument("--band", default="15s", choices=("15s", "30s", "60s"))
    start.add_argument("--episodes", type=int, default=1, help="Desk episode slots (API draft plans 4 for cadence).")
    start.add_argument("--parent", type=Path, default=DEFAULT_RUN_PARENT)
    start.add_argument("--preset-id", default="modern-dark-fantasy")
    start.add_argument("--video-lane", default="minimax-h3")
    add_production_config_args(start)

    bind = sub.add_parser("bind", help="Bind API orchestration to an existing desk.")
    bind.add_argument("--desk", type=Path, required=True)
    bind.add_argument("--prompt", required=True)
    bind.add_argument("--preset-id", default="modern-dark-fantasy")
    bind.add_argument("--video-lane", default="minimax-h3")
    bind.add_argument("--episode", type=int, default=1)
    add_production_config_args(bind)

    cfg_show = sub.add_parser("config", help="Print merged production.config.json for a desk.")
    cfg_show.add_argument("--desk", type=Path, required=True)

    sub.add_parser("status", help="Show production phase.").add_argument("--desk", type=Path, required=True)

    step = sub.add_parser("step", help="Run the next automated API step (one enrol block per call).")
    step.add_argument("--desk", type=Path, required=True)
    step.add_argument(
        "--confirm-spend",
        action="store_true",
        help="After estimate gate, confirm spend and film the take.",
    )

    ap = sub.add_parser("approve", help="Human yes on plates, script, or board.")
    ap.add_argument("--desk", type=Path, required=True)
    ap.add_argument("--gate", required=True, choices=("plates", "script", "board"))
    ap.add_argument("--path", type=Path, default=None)
    ap.add_argument("--accept-dim", action="store_true")

    cancel = sub.add_parser("cancel-job", help="Cancel a stuck video or coordinator job.")
    cancel.add_argument("--desk", type=Path, required=True)
    cancel.add_argument("--job-id", required=True)

    retry = sub.add_parser(
        "retry-video",
        help="One new paid take after a human yes. Refuses a second retry.",
    )
    retry.add_argument("--desk", type=Path, required=True)
    retry.add_argument("--job-id", default=None)
    retry.add_argument(
        "--new-paid-take",
        action="store_true",
        help="Required. Confirms this enrol is a new paid take, not a stuck-job retry.",
    )

    cap = sub.add_parser(
        "caption",
        help="Burn house captions on the raw take locally (ffmpeg on this machine).",
    )
    cap.add_argument("--desk", type=Path, required=True)
    cap.add_argument("--episode", type=int, default=1)
    cap.add_argument("--take", type=Path, default=None, help="Raw MP4; default newest take-epNN-t1-raw-v*.mp4.")
    cap.add_argument(
        "--line-start",
        type=float,
        action="append",
        default=None,
        help="Seconds where a line starts, once per line in order. Overrides speech detection.",
    )
    cap.add_argument("--no-open", action="store_true", help="Do not open the captioned file.")

    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.command == "start":
            desk = init_series_desk(args.parent, args.series, band=args.band, episode_count=args.episodes)
            state = bind_desk(
                desk,
                prompt=args.prompt,
                preset_id=args.preset_id,
                video_lane=args.video_lane,
            )
            save_production_config(desk, config_from_args(args))
            print(desk)
            _warn_if_no_local_ffmpeg()
            print(f"bound session_id={state.session_id} phase={state.phase}")
            print("Next: uv run fictora-produce step --desk", desk)
            return 0
        if args.command == "bind":
            state = bind_desk(
                args.desk,
                prompt=args.prompt,
                preset_id=args.preset_id,
                video_lane=args.video_lane,
                episode_ordinal=args.episode,
            )
            save_production_config(args.desk, config_from_args(args))
            print(f"bound session_id={state.session_id} phase={state.phase}")
            return 0
        if args.command == "config":
            print(json.dumps(asdict(load_production_config(args.desk)), indent=2))
            return 0
        if args.command == "status":
            print(status_message(args.desk))
            return 0
        if args.command == "step":
            result = run_step(args.desk, confirm_spend=args.confirm_spend)
            print(result.message)
            for path in result.paths:
                print(f"  file: {path}")
            return 0
        if args.command == "approve":
            result = approve_gate(
                args.desk,
                gate=args.gate,
                path=args.path,
                accept_dim=True if args.accept_dim else None,
            )
            print(result.message)
            return 0
        if args.command == "caption":
            result = caption_take(
                args.desk,
                episode_ordinal=args.episode,
                take=args.take,
                line_starts=args.line_start,
            )
            ep_dir = args.desk.expanduser().resolve() / f"ep{args.episode:02d}"
            timing = "; ".join(
                f'"{line}" {span.start:.2f}-{span.end:.2f}s' for line, span in zip(result.lines, result.anchors)
            )
            append_run_note(
                ep_dir,
                f"Local house captions: {result.video.name} (cues {result.ass.name}). Lines: {timing}.",
            )
            for line, span in zip(result.lines, result.anchors):
                print(f"  {span.start:6.2f}-{span.end:6.2f}s  {line}")
            print(f"  file: {result.ass}")
            print(f"  file: {result.video}")
            print("Human QC: watch the captioned take. Wrong timing? Re-run with --line-start per line.")
            if not args.no_open and sys.platform == "darwin":
                subprocess.run(["open", str(result.video)], check=False)
            return 0
        if args.command == "cancel-job":
            payload = cancel_video_job(args.desk, args.job_id)
            print(payload)
            print("Cancelled. Do not enrol another take.")
            print("A new take starts another ffmpeg job on Railway.")
            return 0
        if args.command == "retry-video":
            if not args.new_paid_take:
                print(
                    "Refused. retry-video starts a new paid take and another ffmpeg job on Railway.",
                    file=sys.stderr,
                )
                print("Pass --new-paid-take only after a human yes for a new take.", file=sys.stderr)
                return 2
            suffix = prepare_video_retry(args.desk, job_id=args.job_id)
            print(f"phase=ready_video suffix={suffix}")
            print("Next: uv run fictora-produce step --desk … --confirm-spend")
            return 0
    except (RuntimeError, ValueError, FileNotFoundError, FileExistsError, subprocess.CalledProcessError) as exc:
        print(exc, file=sys.stderr)
        return 2
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
