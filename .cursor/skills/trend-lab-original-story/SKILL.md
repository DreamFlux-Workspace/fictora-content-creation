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

## Run it

The request body is already in [reference.md](reference.md). Send it to the hosted worker. There is no local tool in this lane.

When the human gives a reference MP4, `PUT` the bytes, then `POST /runs` with `from_reference: true`, `film: true`, and `scaffold_generate: true`. When they give a story, `POST /runs` with `story_json` and `film: true`. Do that on the same turn, then poll `GET /runs/{run_id}` until `finished`. Do not ask them to paste `docs/PIPELINE.md`, do not ask them to confirm the cost, and do not wait for a second yes. `film: true` is the spend.

Read [reference.md](reference.md) for the exact JSON, credentials, and how to pull the final.

**Check the pictures first (when the human asks to see plates or storyboards before filming).** Three short runs, each a new run id, each waiting for the human's OK:
1. **Plates:** `film: false`, `cast_only: true`. Show every link in `plates.cast` (one character sheet per character) and stop.
2. **Storyboards** (after plates are approved): `continue_from: <plates run>`, `film: false`. Show every link in `plates.storyboard` and stop.
3. **Film** (after storyboards are approved): `continue_from: <storyboard run>`, `film: true`. Deliver the MP4.
To change a picture at step 1 or 2, edit the story's description, then rerun that step with a new id, `continue_from` the previous run, and `redo_cast` / `redo_frames` naming only what to redraw. Send `story_json` again whenever the story changed. A character who changes clothes for a big scene gets a second character id (for example `kane_tux`); each take allows at most 9 pictures (all character sheets plus that take's frames).

To copy the look of a reference reel or picture, upload up to 3 of its stills to the worker (`PUT /uploads/style` with the run token, body = the image file) and put each returned `style:<id>` in the story's `style_refs` (pick clean frames with faces and a background, no text, no gore). Every character sheet copies all of them, and every frame copies the first. A `style_line` alone cannot copy a look: the worker drew modern glossy anime when asked for 90s cel anime by text only. Show the founder one restyled sheet beside a reference still before redrawing the rest.

## Two rules

1. **One episode, one run.** Submit a single hosted run (`run_id`), wait until `output_path` is set or the workflow fails. Do not start a second run for the same episode unless the human asked to retry with a new id. The one exception is the picture check above: plates, storyboards and film runs chained with `continue_from`.
2. **Original story only.** A reference clip supplies **timing and hook shape**, never dialogue, faces, or on-screen copy from the source. The filmed episode uses **drawn** frames and **new** lines.

## Credentials (operator setup)

The human must configure these where **you** call the worker (bot env or local `.env`). You never print them.

| Variable | Purpose |
| --- | --- |
| `TREND_LAB_SERVE_URL` | Public https origin of **trend-lab** (e.g. prod Railway URL) |
| `TREND_LAB_RUN_TOKEN` | Bearer token for `POST /runs` and `PUT /uploads/reference/{run_id}` |

OpenAI, Sume, drama finish, and R2 live on the **worker**. This checkout only calls it.

## Stage order

```
1 Story (or reference) → 2 Submit hosted run → 3 Wait → 4 Deliver final MP4
```

| Stage | You do | Human |
| --- | --- | --- |
| **Story ready** | **A)** They give `story.json` (characters, frames, parts, `{dialogue}`), or **B)** they attach a **reference MP4** | Nothing else. Original lines are the worker's job |
| **Submit** | **Story:** `POST /runs` with `story_json`, `film: true`. **Reference:** `PUT /uploads/reference/{run_id}` then `POST /runs` with `from_reference: true`, `film: true`, `scaffold_generate: true` | URL + run token are already in env |
| **Wait** | `POST /runs` returns **202**; poll `GET /runs/{run_id}` until `finished` (see [reference.md](reference.md)) | Several minutes for a one-part episode is normal |
| **Deliver** | Ready when poll sets `finished` and **`download_url`**. Download that presigned HTTPS link ([reference.md](reference.md)). Name the episode from `story.json` / `title_lines` if the poll has no title. A reference clip keeps the shot timing; the picture and lines are a new story | Watches the final file |

**Filming:** Sume **Seedance** on the worker defaults to **480p** takes; the **delivered** letterbox file may be upscaled after drama finish.

## Never

- Look for `trend-lab` on PATH or PyPI, or ask the human to paste a body from **fictora-trend-lab**.
- Stop a finished-episode request to confirm spend. This lane films when they ask for the episode.
- Use **episode-production** / `fictora-produce` for this lane unless the human explicitly switches to a desk show.
- Trace or Fal-clone the reference video into the take.
- Copy reference dialogue or on-screen text into the story.
- Print or commit `TREND_LAB_RUN_TOKEN`, drama service tokens, or `.env`.
- Treat **`502` on submit** as proof the run failed without polling. **`POST /runs` returns 202**; poll `GET /runs/{run_id}` before starting another id.
- Reuse a `run_id` after the workflow has ended. A retry is a new id.
- Finish the episode yourself. The worker's finish step writes the MP4. Takes without `output_path` are not the episode.

## After each run

Report **run id**, **episode title**, and **`output_path`**. If there is no final object, say it failed or held and do not hand off the takes. Stop. Do not chain a second episode unless the human asks.
