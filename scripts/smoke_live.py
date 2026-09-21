#!/usr/bin/env python3
"""Live smoke for fictora-content-creation before push.

Phase ``read`` hits read-only API routes. Phase ``draft`` enrols one
prompt-video draft and polls the plan job (paid LLM work on prod).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve_preset(client, wanted: str | None) -> tuple[str, str]:
    presets = client.get("/v1/art-style-presets").get("presets") or []
    pid = (wanted or "modern-romance-3").strip()
    matches = [row for row in presets if row.get("preset_id") == pid]
    if not matches:
        matches = [row for row in presets if row.get("preset_id") == "modern-romance-3"]
    if not matches:
        raise SystemExit("no published art-style preset")
    preset = max(matches, key=lambda row: tuple(int(x) for x in str(row.get("version") or "0").split(".")))
    return str(preset["preset_id"]), str(preset["version"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("read", "draft"),
        default="read",
        help="read = presets only; draft = full plan job (costs money).",
    )
    parser.add_argument("--preset-id", default="modern-dark-fantasy")
    parser.add_argument(
        "--prompt",
        default="Old Man Breach smoke: one locked interior, one elder at a door, 15s horror beat.",
    )
    args = parser.parse_args()

    from creation.cli_ops import main as ops_main
    from creation.harness.credentials import load_drama_api_credentials
    from creation.harness.session import DramaApiRunSession
    from creation.harness import stages_gated as stages

    base, token = load_drama_api_credentials(REPO_ROOT)
    print(f"ok api_base={base}")

    with tempfile.TemporaryDirectory(prefix="creation-smoke-") as tmp:
        desk = Path(tmp) / "desk"
        code = ops_main(
            [
                "init-series",
                "--series",
                "Creation Smoke",
                "--band",
                "15s",
                "--episodes",
                "1",
                "--parent",
                str(desk),
            ]
        )
        if code != 0:
            raise SystemExit(f"fictora-ops init-series failed: {code}")
        series_dirs = list(desk.glob("*"))
        if not series_dirs:
            raise SystemExit("init-series produced no desk folder")
        desk_path = series_dirs[0]
        api_dir = desk_path / "ep01" / "api"
        api_dir.mkdir(parents=True, exist_ok=True)
        print(f"ok desk={desk_path}")

        run = DramaApiRunSession(base_url=base, token=token, out_dir=api_dir)
        try:
            preset_id, preset_version = _resolve_preset(run, args.preset_id)
            print(f"ok preset={preset_id}@{preset_version}")

            if args.phase == "read":
                print("ok phase=read (no draft enrol)")
                return 0

            spine_id, plan = stages.start_draft(
                run,
                prompt=args.prompt,
                preset_id=preset_id,
                preset_version=preset_version,
                band="15s",
                video_lane="minimax-h3",
                episode_count=4,
            )
            print(f"ok spine_id={spine_id} plan_status={plan.get('status')}")
            spine = run.spine(spine_id)
            summaries = spine.get("episode_summaries") or []
            print(f"ok episodes={len(summaries)} title={spine.get('title')!r}")
        finally:
            run.client.close()

    print("ok smoke complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
