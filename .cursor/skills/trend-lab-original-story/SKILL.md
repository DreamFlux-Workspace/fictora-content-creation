---
name: trend-lab-original-story
description: >-
  Ships short original-story episodes via the hosted fictora-trend-lab worker
  (TrendRemake, story JSON, Sume film, drama transcripts and Modal finish).
  Use when the human wants trend-lab, original story mode, remake --film,
  hosted worker, or a spine-free one-off reel—not fictora-produce desk production.
---

# Trend-lab original story (hosted)

**Repo to run:** sibling **`fictora-trend-lab`** (clone next to this kit). **Not** `fictora-content-creation` CLI. **Not** spine / `fictora-produce` desks unless the human explicitly switches lanes.

**Product split**

| Lane | Where | Input |
| --- | --- | --- |
| **Desk episodic** | This kit + Drama API | Series, spine, gates → [episode-production](../episode-production/SKILL.md) |
| **Original story** | **fictora-trend-lab** on Railway | One **`story.json`** file, agent tools, no Postgres |

Trend-lab talks to **fictora-drama-generation** over HTTP only (`/v1/transcripts`, `/v1/operator/episode-finish`). It never imports `fictora-drama` source.

## When to use this skill

- One-off or experiment episodes from a **story file** (characters, frames, parts, dialogue in `{braces}`).
- **Hybrid:** reference video for **hook/beat timing only**, then **original** cast and script (`story-scaffold` → edit → `remake --film`).
- **Hosted** production: laptop submits, **prod worker** spends (OpenAI draw, Sume, drama finish).
- Validating the trend-lab + drama Modal path after deploy or env changes.

Stay on **episode-production** for ongoing series, pitch cards, plate/board gates, and `fictora-ops` desks.

## Reference structure → original story

In **fictora-trend-lab**:

```bash
uv run trend-lab new-run --label hybrid
export RUN=<run_id>
uv run trend-lab ingest --run-id $RUN --file reference.mp4
uv run trend-lab analyze --run-id $RUN
uv run trend-lab story-scaffold --run-id $RUN   # optional: --generate
# Edit runs/$RUN/story.scaffold.json — replace placeholders, never paste reference dialogue.
uv run trend-lab remake --story-file runs/$RUN/story.scaffold.json --film
```

Reference pixels are **not** traced into the take; beats drive **frame count and duration** only.

## Ship from a laptop (prod worker)

1. **Clone / open** `fictora-trend-lab`, `uv sync --extra agent --extra vlm --extra render`.
2. **`.env`** (gitignored) at minimum for submit + Sume spend:
   - `TREND_LAB_SERVE_URL` — public `https` origin of Railway **`trend-lab`**
   - `TREND_LAB_RUN_TOKEN` — bearer for `POST /runs`
   - `SUME_API_KEY`, `SUME_WEBHOOK_BASE_URL`, `SUME_WEBHOOK_SECRET`, `FAL_KEY`
3. **Story file** — start from `examples/original_story.example.json` or `examples/e2e_smoke_minimal.json`.
4. **Run** (blocks until Restate finishes; ~5 min for a one-part story is normal):

```bash
cd fictora-trend-lab
uv run trend-lab remake --story-file my_story.json --film
```

5. **Success** — JSON includes `output_path` like `r2://fictora-trend-lab-runs/runs/<run_id>/out/final_vN.mp4`. Pull with R2 creds on the worker or locally if `R2_*` is in your `.env`.

Optional flags: `--trim-stray` (extra-word cuts; Modal finish may not apply trim—see reference). **`--mark` is not uploaded** to the worker.

## Prod topology (Railway `prod-drama`)

| Service | Role |
| --- | --- |
| **trend-lab** | Public HTTPS, Sume webhooks, `POST /runs`, story agent |
| **trend-lab-restate** | Private Restate journal |
| **fictora-drama-generation** | Operator audio + Modal **`/v1/operator/episode-finish`** when `FICTORA_DRAMA_MEDIA_FINISH=modal` |

Worker env (not usually on the laptop): `FICTORA_DRAMA_GENERATION_*`, `FICTORA_DRAMA_EPISODE_FINISH=modal`, `OPENAI_API_KEY`, `R2_*`, `TREND_LAB_STORY_MODEL` (default **`gpt-6.1-sol`** in code).

## Agent rules

- Read trend-lab **`docs/PIPELINE.md`** before changing workflow order or CLI semantics; **`AGENTS.md`** in that repo for boundaries.
- Do **not** add `fictora` imports to trend-lab. Cross-repo fixes go in **fictora-drama** for generation routes and **trend-lab** for the HTTP client.
- Never commit `.env`, tokens, or `runs/`. Never print `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN` in chat or tickets.
- **`trend-lab story-finish`** CLI uses local ffmpeg; **hosted `remake --film`** uses drama Modal via the agent **`finish`** tool. Do not claim desk `fictora-produce finish` and trend-lab finish are the same step.

## If something breaks

Quick checks (details: [reference.md](reference.md)):

- Worker: `GET {trend-lab-origin}/hooks/sume/health` → `{"status":"ok"}`
- Drama: `POST …/v1/operator/episode-finish` with `{}` → **422**, not **404**
- **401 OpenAI** on worker during `remake` → rotate **`OPENAI_API_KEY`** on **`trend-lab`** (match a working key from **`fictora-drama-generation`** in the same Railway project)
- **500 Modal auth** on episode finish → **`MODAL_TOKEN_ID`** / **`MODAL_TOKEN_SECRET`** on **`fictora-drama-generation`** (same as **fictora-drama-restate**)

## More detail

- Trend-lab docs index: `fictora-trend-lab/docs/README.md`
- Secrets + prod smoke curls: `fictora-trend-lab/docs/SECRETS.md`
- Operator comparison and troubleshooting: [reference.md](reference.md)
