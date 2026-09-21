---
name: episode-production
description: >-
  Operates Fictora episode production via fictora-produce against the deployed
  Drama Generation API — desk gates, configurable draft/video/estimate, MiniMax
  H3 lane, recovery. Use for content ops, filming takes, or E2E validation in
  fictora-content-creation.
---

# Episode production

**Repo:** `fictora-content-creation` only. **API:** deployed Drama Generation `/v1/*`. **Tool:** `uv run fictora-produce` (not hand-rolled HTTP except debugging).

Progressive detail: [reference.md](reference.md) · Runbook: [docs/content-ops/runbook.md](../../docs/content-ops/runbook.md) · API map: [docs/content-ops/api-map.md](../../docs/content-ops/api-map.md)

## Configuration (single source of truth)

All tunables live in **three layers** (later wins):

| Layer | Location | Purpose |
| --- | --- | --- |
| **Environment** | repo `.env` | Auth + optional global session |
| **Desk config** | `<desk>/production.config.json` | Draft count, take recipe, estimate fallback, poll budgets |
| **Desk state** | `<desk>/production.json` | Phase machine, spine id, session id, video retry suffix |

Example desk config: [config.example.json](config.example.json)

### Environment (`.env`)

| Variable | Required | Default / notes |
| --- | --- | --- |
| `FICTORA_DRAMA_GENERATION_API_BASE_URL` | yes | Prod URL in `.env.example` |
| `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN` | yes | Never print or commit |
| `FICTORA_DRAMA_GENERATION_SESSION_ID` | no | If unset, `production.json` `session_id` is used |

### `production.config.json` fields

| Field | Default | Use |
| --- | --- | --- |
| `draft_episode_count` | **4** | **Do not use 5** — prod plan bible materializes 4; 5 causes ordinal-5 server errors |
| `clip_duration_seconds` | 15 | 4–15 per API |
| `cut_tempo` | `one_shot` | `punchy`, `slow_burn`, … |
| `caption_style` | `house` | **Local** burn-in recipe name (see [local-captions.md](../../docs/content-ops/local-captions.md)) |
| `api_captions` | **`false`** | When false, poll stops at **raw scene clip** (`17_raw_scene_clips.json`); caption on laptop |
| `locale` | `en-US` | Draft locale |
| `fallback_estimate_usd` | 1.20 | When batch estimate v2 returns no USD or is skipped |
| `poll_*_deadline_seconds` | 1800–7200 | Harness poll caps (see reference) |

Set via CLI on **start** or **bind** (writes `production.config.json`):

```bash
uv run fictora-produce start \
  --series "Series Name" \
  --prompt "<premise and visual lock>" \
  --band 15s \
  --preset-id modern-dark-fantasy \
  --video-lane minimax-h3 \
  --draft-episodes 4 \
  --clip-seconds 15 \
  --cut-tempo one_shot \
  --caption-style house \
  --fallback-estimate-usd 1.20
```

Omit **`--api-captions`** for the default **raw take + local captions** path. Add **`--api-captions`** only when you need Drama server burn-in (slow post-production tail).

**Prod-validated example** (modern-dark-fantasy · minimax-h3 · local house captions): [docs/content-ops/examples/tram-not-tonight-ep1.md](../../docs/content-ops/examples/tram-not-tonight-ep1.md)

Inspect merged config:

```bash
uv run fictora-produce config --desk <desk-path>
```

### `production.json` (orchestrator — do not hand-edit unless recovering)

| Field | Meaning |
| --- | --- |
| `phase` | Next allowed action (see phase table below) |
| `session_id` | `X-Drama-Session-Id` for all `/v1` calls on this desk |
| `spine_id` | Story spine after draft |
| `video_idempotency_suffix` | Set by `cancel-job` / `retry-video` for a fresh take key |

## Operator loop (one API enrol block per agent turn)

### 1. Start desk

```bash
uv run fictora-produce start --series "…" --prompt "…" --band 15s
```

Print desk path and `QUEUE.md`. Say **aligned** or **deviation** before paid work.

### 2. Automated step

```bash
uv run fictora-produce step --desk <desk-path>
```

Stops at human gates. Report `ep01/run-notes.md`, downloaded paths under `plates/`, `boards/`, `takes/`.

**Never** two `step` calls in one turn unless the human asked to catch up.

### 3. Human gates

```bash
uv run fictora-produce approve --desk <desk> --gate plates
uv run fictora-produce approve --desk <desk> --gate script
uv run fictora-produce approve --desk <desk> --gate board --path <boards/file-reviewed.png>
```

Add `--accept-dim` when exposure API flagged dim and the human accepted.

### 4. Spend → take

```bash
uv run fictora-produce step --desk <desk> --confirm-spend
```

With **`api_captions: false`** (default), the step finishes when **scene child jobs** complete — typically soon after MiniMax returns — and writes:

- `ep01/api/17_raw_scene_clips.json` — `clips[].url` (raw MP4, no burn-in)
- `ep01/takes/take-ep01-t1-raw*.mp4` — downloaded primary clip

Do **not** wait for parent job 100% or `18_delivery.json` unless `api_captions: true`.

### 5. Local captions (house / Drama app style)

When phase is **`complete`** and `api_captions` is false, **do not stop at the raw MP4** — finish the desk take in the same session unless the human asked to pause.

After the raw take lands, follow **[docs/content-ops/local-captions.md](../../docs/content-ops/local-captions.md)**:

1. Dialogue from spine / script gate.
2. Transcribe the take; align cues to **silence-end onsets** (runbook).
3. Burn **house** ASS with ffmpeg **libass** — yellow `#F5D547`, black edge, no box, ~62% frame height.
4. Save captioned take under `takes/` (e.g. `take-ep01-t1-captioned-v1.mp4`); note in `run-notes.md`.
5. **Open** the captioned file for human QC (macOS: `open <path>`).

```bash
FFMPEG=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg \
  bash scripts/burn_house_captions.sh \
  ep01/takes/take-ep01-t1-raw-v1.mp4 \
  ep01/takes/take-ep01-t1-house.ass \
  ep01/takes/take-ep01-t1-captioned-v1.mp4
```

Status:

```bash
uv run fictora-produce status --desk <desk>
```

## Phase machine

| Phase | Agent |
| --- | --- |
| `new` | `step` → draft |
| `ready_cast_enrol` | `step` → cast enrol + plate downloads |
| `wait_plates` | Human → `approve --gate plates` |
| `wait_script` | Human → `approve --gate script` |
| `ready_boards_enrol` | `step` → boards + exposure |
| `wait_board` | Human → `approve --gate board --path …` |
| `ready_estimate` | `step` → batch estimate (or fallback USD) |
| `wait_spend` | Human yes → `step --confirm-spend` |
| `ready_video` | (internal) → video enrol |
| `complete` | Raw clip or API delivery on disk; local caption if `api_captions: false` |
| `failed` | Read `last_error`, fix config or recover (reference) |

Harness auto-retries: `plan_media_spine_version_stale` (cast/boards), retryable `authoring_stalled` (plan).

## Recovery (deployed API)

| Situation | Action |
| --- | --- |
| Video stuck `running` ~50%, Restate ffmpeg errors | Poll job; `cancel-job` then `step --confirm-spend` with new suffix |
| Plan `authoring_stalled` retryable | Re-run `step` from `new` or let harness plan retry |
| Estimate 400 cadence | Harness skips + `fallback_estimate_usd`; take still enrols |
| Wrong session / spine 404 | New desk + new `start`; do not reuse old spine id |

```bash
uv run fictora-produce cancel-job --desk <desk> --job-id job_video_…
uv run fictora-produce retry-video --desk <desk> --job-id job_video_…
uv run fictora-produce step --desk <desk> --confirm-spend
```

## Rules

1. Human yes before money moves (plates, board, spend).
2. One paid enrol per agent turn.
3. Never clone or read `fictora-drama`.
4. Never overwrite desk media; use versioned filenames via ops helpers.

## Engineering smoke (not production gates)

```bash
uv run python scripts/smoke_live.py --phase read
uv run python scripts/e2e_visual_first_ep1.py --out-dir e2e-runs/manual/api
```

Production path: **fictora-produce + human gates only**.

## Spend (H3 lane)

Take ~$1.20 · plate/board ~$0.30 · first-ep envelope ~$4.50. Stop past 2× envelope.
