---
name: episode-production
description: >-
  Runs Fictora episode production through the hosted Drama Generation API with
  human gates, versioned run folders, and MiniMax H3 lane discipline. Use when
  starting a series, filming a take, content ops, or producing episodes with
  Cursor — in the fictora-content-creation repo only.
---

# Episode production (creation repo)

You are the agent in the Episode Production Runbook. The human is the gate. The folder is the review surface.

**Open this repository:** `fictora-content-creation`. Do **not** clone `fictora-drama`. System prompts and compilers are server-side only.

Read when needed:

- [docs/content-ops/README.md](../../docs/content-ops/README.md)
- [docs/content-ops/runbook.md](../../docs/content-ops/runbook.md)
- [docs/content-ops/api-map.md](../../docs/content-ops/api-map.md)
- [docs/content-ops/checklists.md](../../docs/content-ops/checklists.md)
- [docs/drama-api-v1.md](../../docs/drama-api-v1.md)

## Two rules

1. A human says yes before money moves. Plates, then board, then take. No batched gates.
2. Every paid unit is rendered once. A second render needs a written cause. "Try again" is not a cause.

## Never

- Chain two stages in one turn.
- Approve plates, script, or boards in the same turn you enrol them.
- Use `run_cast_look` or any helper that auto-approves cast.
- Overwrite a file. Use `uv run fictora-ops next-path`.
- Print or commit `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN`.
- Clone or read `fictora-drama` for prompts or `src/`.

## Tools in this repo

| Tool | Role |
| --- | --- |
| `uv run fictora-ops` | Desk folders, `QUEUE.md`, gate ledger, preflight |
| `creation.harness.DramaApiRunSession` | HTTP to prod Drama API, JSON in `<run>/api/` |
| `creation.harness.stages_gated` | Visual-first ep1: draft, cast, boards, estimate, video — **split enrol / approve** |

Credentials: `.env` with `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN`. Load via `creation.harness.credentials.load_drama_api_credentials(repo_root)`.

## First turn

Ask where the series desk should go. Default `~/Downloads/documents/`. Then:

```bash
uv run fictora-ops init-series --series "<name>" --band 15s --episodes <n>
```

Print the desk path and `QUEUE.md`. Wait for the brief.

## API pattern (MiniMax H3 lane)

Default `model_overrides.video=minimax-h3` (H3 Max Turbo I2V on prod). Confirm preset ids with `GET /v1/art-style-presets`.

Visual-first order — **one stage per stop**:

1. `start_draft` → poll plan → spine id  
2. `enrol_cast` → download plates → **stop** → human → `approve_cast`  
3. `approve_script` after line gate  
4. `enrol_boards` → `measure_ep1_board_exposure` → **stop** → human → `approve_ep1_boards`  
5. `estimate_batch` → record with `fictora-ops estimate` → preflight  
6. `enrol_video` with `caption_style=house`, `cut_tempo=one_shot` → download take → delivery  

Example session bootstrap (agent writes a small script or runs inline):

```python
from pathlib import Path
from creation.harness.credentials import load_drama_api_credentials
from creation.harness.session import DramaApiRunSession
from creation.harness import stages_gated as stages

repo = Path(".").resolve()
base, token = load_drama_api_credentials(repo)
api_dir = Path("<desk>/ep01/api")
api_dir.mkdir(parents=True, exist_ok=True)
run = DramaApiRunSession(base_url=base, token=token, out_dir=api_dir)
spine_id, _ = stages.start_draft(run, prompt="...", preset_id="...", preset_version="...")
```

Reuse the same `Idempotency-Key` on retries.

## Spend

Take ≈ $1.20. Plate or board ≈ $0.30. First episode new series envelope ≈ $4.50. Past 2× envelope, stop.

## Aligned or deviation

Say **aligned** or **deviation** before paid work. Deviation block required before scratch tools — see runbook.

Product board brightness: `GET .../boards/exposure`. Local `fictora-ops measure-board` is a helper only.
