# Trend-lab hosted episode — reference

Detail behind [SKILL.md](SKILL.md). Send these requests to the hosted worker. That is the whole run.

## Credentials

| Variable | Where | Purpose |
| --- | --- | --- |
| `TREND_LAB_SERVE_URL` | Operator / bot | Worker origin (https) |
| `TREND_LAB_RUN_TOKEN` | Operator / bot | Bearer on upload and run start |

Worker-only (Railway **trend-lab**, not each user): `OPENAI_API_KEY`, `SUME_*`, `FICTORA_DRAMA_GENERATION_*`, `R2_*`, etc.

## Hosted API (produce one episode)

**From a story file**

```http
POST {TREND_LAB_SERVE_URL}/runs
Authorization: Bearer {TREND_LAB_RUN_TOKEN}
Content-Type: application/json

{
  "run_id": "<12-char id>",
  "story_json": "<StoryFile JSON string>",
  "film": true
}
```

The worker answers **202** immediately with `{ "run_id", "status": "accepted", "poll": "/runs/<id>" }`. It does not hold the HTTP connection until filming finishes. Poll until `finished` is true:

```http
GET {TREND_LAB_SERVE_URL}/runs/{run_id}
Authorization: Bearer {TREND_LAB_RUN_TOKEN}
```

When `output_path` is set, pull that object. When `failed` is true, read `error`. A **502** on the initial POST usually means the edge timed out while the worker was still starting the run; poll anyway before starting a new id.

**From a reference clip (structure → scaffold → same agent path)**

```http
PUT {TREND_LAB_SERVE_URL}/uploads/reference/{run_id}
Authorization: Bearer {TREND_LAB_RUN_TOKEN}
Content-Type: application/octet-stream

<reference MP4 bytes>
```

```http
POST {TREND_LAB_SERVE_URL}/runs
Authorization: Bearer {TREND_LAB_RUN_TOKEN}
Content-Type: application/json

{
  "run_id": "<same id>",
  "from_reference": true,
  "film": true,
  "scaffold_generate": true,
  "reference_caption": "optional hook hint"
}
```

Optional: `"reference_url": "https://…"` instead of PUT when the worker has **yt-dlp** (no local upload).

Poll until **`finished`** is true. Success includes **`output_path`** and a presigned **`download_url`** (HTTPS GET, ~24h):

`r2://fictora-trend-lab-runs/runs/<run_id>/out/final_vN.mp4`

Deliver the episode by downloading **`download_url`**. Callers do not need R2 credentials. If `download_url` is missing but `output_path` is set, R2 may be misconfigured on the worker.

No `output_path` after `finished` means the run held or failed (`failed` / `error`). Do not pull takes in its place. A `run_id` whose workflow has already ended cannot be submitted again; use a new id.

## Story JSON (minimal)

| Field | Purpose |
| --- | --- |
| `style_line`, `setting_line` | Look and place |
| `characters[]` | `id`, `description`, `look_hint`, `voice` |
| `frames[]` | Storyboard stills: `cast`, `action` |
| `parts[]` | One Sume take each: `frames`, `duration_sec` (4–30), `shots` with `{spoken lines}` |
| `title_lines[]`, `delivery_format` | Letterbox or portrait finish |
| `video_model`, `resolution` | Default **seedance-2.5**, **480p** on the worker |

## Pull the final MP4

Use **`download_url`** from the last poll. Do not ask for R2 keys or a manual link unless `download_url` is absent after a successful finish.

## Vs episode-production

| | Desk (`fictora-produce`) | Hosted trend-lab (this skill) |
| --- | --- | --- |
| Goal | Series episodes with gates | One finished MP4 per run |
| Input | Spine, brief, gates | `story.json` or reference → scaffold |
| Finish | Local + server paths | Worker agent → drama Modal finish |
