# Trend-lab original story — reference

## Story JSON (minimal shape)

| Field | Purpose |
| --- | --- |
| `style_line`, `setting_line` | Look and place for every draw/film step |
| `characters[]` | `id`, `description`, `look_hint`, `voice` |
| `frames[]` | Storyboard stills: `cast`, `action` |
| `parts[]` | One Sume take each: `frames`, `duration_sec` (4–30), `shots` with `{spoken lines}` |
| `title_lines[]`, `delivery_format` | Letterbox or portrait finish |
| `captions[]` | Optional; empty → timed after film |

Rejections are schema errors in `story-init`; fix the file before resubmitting.

## Hosted run lifecycle

```text
remake --film  →  POST /runs (bearer)  →  Restate TrendRemake
  →  agent: read_story → draw → film (Sume webhook) → time_captions → finish
  →  finish: upload takes → POST /v1/operator/episode-finish (Modal) → R2 final
```

CLI **waits** on the HTTP response until the workflow completes or fails.

## Environment matrix

| Variable | Laptop submit | Railway trend-lab |
| --- | --- | --- |
| `TREND_LAB_SERVE_URL` | Required | — |
| `TREND_LAB_RUN_TOKEN` | Required | Required (same bearer) |
| `SUME_*`, `FAL_KEY` | Required for `--film` | Required |
| `FICTORA_DRAMA_GENERATION_*` | Optional (worker has prod) | Required for drama captions/finish |
| `FICTORA_DRAMA_EPISODE_FINISH` | — | `modal` for hosted Modal finish |
| `OPENAI_API_KEY` | Only if running steps locally | Required for agent |
| `MODAL_TOKEN_*` | — | On **fictora-drama-generation**, not only Restate |
| `TREND_LAB_STORY_MODEL` | Optional override | e.g. `gpt-6.1-sol` |

## Pull and open the final MP4

When `R2_*` is in local `.env`:

```bash
cd fictora-trend-lab
mkdir -p "runs/<run_id>/out"
uv run python -c "
from pathlib import Path
from trend_lab.r2_store import _client, r2_config
cfg = r2_config()
key = 'runs/<run_id>/out/final_v1.mp4'
dest = Path('runs/<run_id>/out/final_v1.mp4')
_client(cfg).download_file(cfg.bucket, key, str(dest))
print(dest.resolve())
"
open "runs/<run_id>/out/final_v1.mp4"
```

## Smoke after deploy

From `fictora-trend-lab/docs/SECRETS.md` (no secrets in commands):

```bash
export TREND_LAB_ORIGIN="https://trend-lab-prod-drama.up.railway.app"
export DRAMA_ORIGIN="https://fictora-drama-generation-prod-drama.up.railway.app"
curl -fsS "$TREND_LAB_ORIGIN/hooks/sume/health"
curl -sS -o /dev/null -w "%{http_code}\n" -X POST "$DRAMA_ORIGIN/v1/operator/episode-finish" \
  -H "Content-Type: application/json" -d '{}'
```

Expect **422** on episode-finish (validation), not **404**.

## Finish-only verify (no Sume)

Useful when debugging drama Modal without filming:

1. `story-init` + copy a short MP4 to `runs/<id>/out/part_01_v1.mp4`
2. Invoke agent finish path (same as workflow `finish` tool), not CLI `story-finish` alone.

See `fictora-trend-lab/docs/SECRETS.md` § Production smoke.

## Vs episode-production

| | Desk (`fictora-produce`) | Trend-lab (`remake --film`) |
| --- | --- | --- |
| Identity | Spine, series, gates | Single story JSON |
| Human gates | Required per stage | Agent-driven; human submits once |
| Finish | Local `finish` + reel desk | Drama Modal on worker |
| This repo's role | Primary | Orchestration docs + this skill only |
