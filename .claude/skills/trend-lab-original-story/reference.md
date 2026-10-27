# Trend-lab hosted episode — reference

Detail behind [SKILL.md](SKILL.md). Implementation and pipeline order: sibling repo **fictora-trend-lab** `docs/PIPELINE.md`.

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

Successful completion returns a body whose workflow result includes **`output_path`** like  
`r2://fictora-trend-lab-runs/runs/<run_id>/out/final_v1.mp4`.

## Story JSON (minimal)

| Field | Purpose |
| --- | --- |
| `style_line`, `setting_line` | Look and place |
| `characters[]` | `id`, `description`, `look_hint`, `voice` |
| `frames[]` | Storyboard stills: `cast`, `action` |
| `parts[]` | One Sume take each: `frames`, `duration_sec` (4–30), `shots` with `{spoken lines}` |
| `title_lines[]`, `delivery_format` | Letterbox or portrait finish |
| `video_model`, `resolution` | Default **seedance-2.5**, **480p** on the worker |

Example starters: **fictora-trend-lab** `examples/original_story.example.json`, `examples/e2e_smoke_minimal.json`.

## Pull the final MP4

When the operator has **R2** read credentials (or asks ops), object key:

`runs/<run_id>/out/final_v1.mp4`

See **fictora-trend-lab** `docs/SECRETS.md` for presign/pull patterns. Do not embed secrets in chat.

## CLI shortcut (optional)

If the human runs **fictora-trend-lab** locally with the same env vars, these call the same hosted API:

```bash
uv run trend-lab remake --story-file story.json --film
uv run trend-lab remake-reference --reference-file ref.mp4 --film --scaffold-generate
```

The skill's default path is **hosted HTTP**, not local ingest/analyze, unless the human asked to edit a scaffold on disk before filming.

## Vs episode-production

| | Desk (`fictora-produce`) | Hosted trend-lab (this skill) |
| --- | --- | --- |
| Goal | Series episodes with gates | One finished MP4 per run |
| Input | Spine, brief, gates | `story.json` or reference → scaffold |
| Finish | Local + server paths | Worker agent → drama Modal finish |
