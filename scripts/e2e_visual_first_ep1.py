#!/usr/bin/env python3
"""End-to-end visual-first episode 1 on prod Drama API (paid stages)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve_preset(run, wanted: str | None) -> tuple[str, str]:
    presets = run.get("/v1/art-style-presets").get("presets") or []
    pid = (wanted or "modern-dark-fantasy").strip()
    matches = [row for row in presets if row.get("preset_id") == pid]
    if not matches:
        raise SystemExit(f"preset not found: {pid!r}")
    preset = max(matches, key=lambda row: tuple(int(x) for x in str(row.get("version") or "0").split(".")))
    return str(preset["preset_id"]), str(preset["version"])


def _video_url(delivery: dict) -> str | None:
    for key in ("video_url", "url"):
        if delivery.get(key):
            return str(delivery[key])
    episodes = delivery.get("episodes") or []
    if episodes and isinstance(episodes[0], dict):
        ep = episodes[0]
        for key in ("video_url", "url"):
            if ep.get(key):
                return str(ep[key])
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset-id", default="modern-dark-fantasy")
    parser.add_argument(
        "--prompt",
        default=(
            "E2E smoke: one elder at a sealed basement door, wet concrete, single lamp. "
            "15s horror beat, one speaking line, no gore."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="API artefacts (default: ./e2e-runs/latest/api)",
    )
    parser.add_argument(
        "--spine-id",
        default=None,
        help="Skip draft; continue on an existing spine (requires same session — use run_meta or --session-id).",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="X-Drama-Session-Id (default: run_meta.json or FICTORA_DRAMA_GENERATION_SESSION_ID).",
    )
    args = parser.parse_args()

    from creation.harness.credentials import load_drama_api_credentials
    from creation.harness.run_context import load_run_meta, resolve_session_for_run, save_run_meta
    from creation.harness.session import DramaApiRunSession
    from creation.harness import stages_gated as stages
    from creation.harness.visual_first_ep1 import approve_ep1_boards, measure_ep1_board_exposure

    base, token = load_drama_api_credentials(REPO_ROOT)
    api_dir = args.out_dir or (REPO_ROOT / "e2e-runs" / "latest" / "api")
    api_dir.mkdir(parents=True, exist_ok=True)
    meta = load_run_meta(api_dir)
    session_id = (args.session_id or meta.get("session_id") or resolve_session_for_run(api_dir)).strip()
    print(f"phase=config api={base} out={api_dir} session_id={session_id}", flush=True)

    run = DramaApiRunSession(base_url=base, token=token, out_dir=api_dir, session_id=session_id)
    preset_id, preset_version = _resolve_preset(run, args.preset_id)
    print(f"phase=preset {preset_id}@{preset_version}", flush=True)

    try:
        if args.spine_id:
            spine_id = args.spine_id.strip()
            if meta.get("session_id") and meta.get("session_id") != session_id:
                print("warn: --session-id differs from run_meta; spine may 404", flush=True)
            print(f"phase=draft_skipped spine_id={spine_id}", flush=True)
        else:
            print("phase=draft", flush=True)
            spine_id, plan = stages.start_draft(
                run,
                prompt=args.prompt,
                preset_id=preset_id,
                preset_version=preset_version,
                band="15s",
                video_lane="minimax-h3",
                episode_count=4,
            )
            print(f"phase=draft_done spine_id={spine_id} plan={plan.get('status')}", flush=True)
            save_run_meta(
                api_dir,
                session_id=run.session_id,
                spine_id=spine_id,
                preset_id=preset_id,
                preset_version=preset_version,
            )

        print("phase=cast_enrol", flush=True)
        stages.enrol_cast(
            run,
            spine_id=spine_id,
            prompt=args.prompt,
            preset_id=preset_id,
            preset_version=preset_version,
            video_lane="minimax-h3",
        )
        print("phase=cast_approve", flush=True)
        stages.approve_cast(run, spine_id=spine_id)

        print("phase=script_approve", flush=True)
        stages.approve_script(run, spine_id=spine_id)

        print("phase=boards_enrol", flush=True)
        stages.enrol_boards(
            run,
            spine_id=spine_id,
            prompt=args.prompt,
            preset_id=preset_id,
            preset_version=preset_version,
            video_lane="minimax-h3",
        )

        print("phase=boards_exposure", flush=True)
        exposure = measure_ep1_board_exposure(run, spine_id=spine_id)
        boards = exposure.get("boards") if isinstance(exposure.get("boards"), list) else []
        accept_dim = any(isinstance(b, dict) and b.get("below_dim_floor") for b in boards)
        print(f"phase=boards_exposure_done accept_dim={accept_dim}", flush=True)

        spine = run.spine(spine_id)
        print("phase=boards_approve", flush=True)
        approve_ep1_boards(run, spine_id=spine_id, spine=spine, accept_dim=accept_dim)

        print("phase=estimate", flush=True)
        estimate = stages.estimate_batch(run, spine_id=spine_id)
        print(f"phase=estimate_done keys={list(estimate.keys())[:6]}", flush=True)

        print("phase=video_enrol", flush=True)
        delivery = stages.enrol_video(
            run,
            spine_id=spine_id,
            prompt=args.prompt,
            preset_id=preset_id,
            preset_version=preset_version,
            caption_style="house",
            video_lane="minimax-h3",
            clip_duration_seconds=15,
            cut_tempo="one_shot",
        )
        url = _video_url(delivery)
        summary = {
            "spine_id": spine_id,
            "preset_id": preset_id,
            "preset_version": preset_version,
            "video_url": url,
            "delivery_keys": list(delivery.keys()),
        }
        (api_dir / "e2e_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"phase=complete video_url={url}", flush=True)
        if not url:
            print("warn: delivery had no video_url; inspect 18_delivery.json", flush=True)
            return 1
        return 0
    finally:
        run.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
