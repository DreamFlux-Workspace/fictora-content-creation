"""CLI: orchestrate episode production on a series desk."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from creation.cli_config import add_production_config_args, config_from_args
from creation.orchestrate import approve_gate, bind_desk, run_step, status_message
from creation.production_config import load_production_config, save_production_config
from creation.recover import cancel_video_job, prepare_video_retry
from creation.ops.floor import init_series_desk
from creation.ops.folder import DEFAULT_RUN_PARENT


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

    retry = sub.add_parser("retry-video", help="After cancel/fail: fresh idempotency + ready_video phase.")
    retry.add_argument("--desk", type=Path, required=True)
    retry.add_argument("--job-id", default=None)

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
        if args.command == "cancel-job":
            payload = cancel_video_job(args.desk, args.job_id)
            suffix = prepare_video_retry(args.desk, job_id=args.job_id)
            print(payload)
            print(f"video_idempotency_suffix={suffix}")
            print("Next: uv run fictora-produce step --desk … --confirm-spend")
            return 0
        if args.command == "retry-video":
            suffix = prepare_video_retry(args.desk, job_id=args.job_id)
            print(f"phase=ready_video suffix={suffix}")
            print("Next: uv run fictora-produce step --desk … --confirm-spend")
            return 0
    except (RuntimeError, ValueError, FileNotFoundError, FileExistsError) as exc:
        print(exc, file=sys.stderr)
        return 2
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
