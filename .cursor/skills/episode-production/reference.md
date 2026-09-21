# Episode production — API reference for operators

## Deployed API contract

| Item | Value |
| --- | --- |
| Base URL | `FICTORA_DRAMA_GENERATION_API_BASE_URL` (see `.env.example`) |
| Auth | `Authorization: Bearer`, `X-Drama-Session-Id` on every `/v1` call |
| Mutations | `Idempotency-Key` required (cancel, draft, enrol, video) |
| OpenAPI | `{base}/openapi.json`, `{base}/docs` |

Visual-first sequence (aligned):

1. `POST /v1/prompt-video-authoring-drafts` → poll `GET /v1/jobs/{plan_job_id}`
2. `POST /v1/spines/{id}/cast/enrol` → poll video-generation job route → `POST .../cast/approve`
3. `POST /v1/spines/{id}/approve` (script)
4. `POST /v1/spines/{id}/boards/enrol` → `GET .../episodes/1/boards/exposure` → `POST .../boards/approve`
5. `POST /v1/spines/{id}/batches/estimate` — body: `episode_ids` = **ep1 + ep2** only; spine must have **≥2** summaries (cadence plans **4** on draft)
6. `POST /v1/video-generations` → poll → `GET .../delivery`

Preset list: `GET /v1/art-style-presets` (pin latest version on bind).

## Job status vs Restate logs

| Observation | Meaning |
| --- | --- |
| API `running` 50% for a long time | Video post-prod inspection; may still complete |
| Restate `DramaPostProductionInspectionError` (ffmpeg) | Worker failed inspection; job may eventually `failed` or complete on retry |
| Restate `DramaPersistenceConflictError` (budget terminal) | Replay after batch terminal; often non-fatal if client poll shows `completed` |
| Plan `failed` + `authoring_stalled` + `retryable: true` | Soft stall; safe to re-draft (harness retries) |
| Plan 500 `season bible has no episode summary for ordinal 5` | **Set `draft_episode_count` to 4**, not 5 |

**Trust `GET /v1/video-generations/{id}` or `GET /v1/jobs/{id}`** for operator decisions, not only Restate stderr.

## Error catalog (client actions)

| Code / symptom | Client action |
| --- | --- |
| `plan_media_spine_version_stale` | Harness re-enrols cast/boards (automatic) |
| `authoring_stalled` (retryable) | Harness re-submits draft (automatic) |
| `invalid_episode_selection` (estimate) | Harness saves skip artefact; uses `fallback_estimate_usd` |
| `idempotency_key_required` on cancel | Use `fictora-produce cancel-job` (fixed Idempotency-Key) |
| `spine_not_found` | Session/spine mismatch; new desk |
| Video stuck | `cancel-job` → `retry-video` or `step --confirm-spend` |
| Poll CLI dies mid-job (`ReadError`, body from connection) | Job may still run; re-run `step` (poll retries transport) or resume poll on same `job_id` from `16_video_enrol.json` |

## Artefacts on desk

| Path | Content |
| --- | --- |
| `ep01/api/*.json` | Numbered API request/response snapshots |
| `ep01/api/run.log` | JSONL poll log |
| `ep01/run_meta.json` | Session + spine binding for resume |
| `production.json` | Phase machine |
| `production.config.json` | Tunables |
| `ep01/plates/`, `boards/`, `takes/` | Downloaded media |

## Debugging (deviation — declare before use)

Hand-roll only when `fictora-produce` cannot surface the failure:

```python
from pathlib import Path
from creation.harness.credentials import load_drama_api_credentials
from creation.harness.session import DramaApiRunSession

repo = Path(".").resolve()
base, token = load_drama_api_credentials(repo)
run = DramaApiRunSession(base_url=base, token=token, out_dir=Path("debug/api"), session_id="<from production.json>")
# GET spine, GET job — no prompt edits
```

Do not scrape prompts or clone `fictora-drama`.

## Poll deadlines (`production.config.json`)

| Field | Default (s) | Stage |
| --- | --- | --- |
| `poll_plan_deadline_seconds` | 1800 | Draft plan |
| `poll_cast_deadline_seconds` | 3600 | Cast enrol |
| `poll_boards_deadline_seconds` | 7200 | Boards |
| `poll_video_deadline_seconds` | 7200 | Take |

Plan jobs can exceed 15 minutes under load; increase plan deadline before declaring failure.
