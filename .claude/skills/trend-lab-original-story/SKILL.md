---
name: trend-lab-original-story
description: >-
  Produces one finished short episode on the hosted fictora-trend-lab worker
  (story or reference structure → draw → Sume film → drama finish). Use for
  trend-lab hosted episodes only—not fictora-produce desks or Fal reel clones.
---

# Trend-lab episode production (hosted)

You are the agent. The human supplies a **story** or a **reference clip**. The **hosted worker** produces one **finished episode MP4**.

**Orchestration:** this skill in **fictora-content-creation**. **Execution:** authenticated HTTP to Railway **trend-lab** only. **Not** `fictora-produce`, desks, spine, or pitch gates — that is [episode-production](../episode-production/SKILL.md).

Read when you need detail, not on the first turn:

- Hosted API, story JSON, credentials, pull the final: [reference.md](reference.md)

## Two rules

1. **One episode, one run.** Submit a single hosted run (`run_id`), wait until `output_path` is set or the workflow fails. Do not start a second run for the same episode unless the human asked to retry with a new id.
2. **Original story only.** A reference clip supplies **timing and hook shape**, never dialogue, faces, or on-screen copy from the source. The filmed episode uses **drawn** frames and **new** lines.

## Credentials (operator setup)

The human must configure these where **you** call the worker (bot env or local `.env`). You never print them.

| Variable | Purpose |
| --- | --- |
| `TREND_LAB_SERVE_URL` | Public https origin of **trend-lab** (e.g. prod Railway URL) |
| `TREND_LAB_RUN_TOKEN` | Bearer token for `POST /runs` and `PUT /uploads/reference/{run_id}` |

OpenAI, Sume, drama finish, and R2 live on the **worker** — not on the operator laptop unless they run trend-lab CLI locally.

## Stage order

```
1 Story (or reference) → 2 Submit hosted run → 3 Wait → 4 Deliver final MP4
```

| Stage | You do | Human |
| --- | --- | --- |
| **Story ready** | **A)** They give `story.json` (characters, frames, parts, `{dialogue}`), or **B)** they attach a **reference MP4** for structure-only scaffold | Confirms cast, setting, and lines are **original** (edit scaffold if needed before film) |
| **Submit** | **Story:** `POST /runs` with `story_json`, `film: true`. **Reference:** `PUT /uploads/reference/{run_id}` then `POST /runs` with `from_reference: true`, `film: true` (optional `scaffold_generate`, `reference_caption`) | Has configured URL + run token |
| **Wait** | Poll status or wait on the submit response until phase **done** or a terminal error | Several minutes for a one-part episode is normal |
| **Deliver** | Report `run_id` and `output_path` (`r2://…/runs/<id>/out/final_vN.mp4`); pull or presign for the human to watch ([reference.md](reference.md)) | Watches the final file; sound and captions should match a normal episode finish |

**Filming:** Sume **Seedance** on the worker defaults to **480p** takes; the **delivered** letterbox file may be upscaled after drama finish.

## Never

- Use **episode-production** / `fictora-produce` for this lane unless the human explicitly switches to a desk show.
- Trace or Fal-clone the reference video into the take.
- Copy reference dialogue or on-screen text into the story.
- Print or commit `TREND_LAB_RUN_TOKEN`, drama service tokens, or `.env`.
- Treat **`502` on submit** as proof the run failed — check logs/R2 for that `run_id` before reusing the same id.
- Claim **`trend-lab story-finish`** (local ffmpeg CLI) is the same step as the hosted agent **finish** (drama Modal on the worker).

## After each run

Report **run id**, **output path**, and whether the episode **finished** or **held** (e.g. `film: false`). Stop. Do not chain a second episode unless the human asks.
