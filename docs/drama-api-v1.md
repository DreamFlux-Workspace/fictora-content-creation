# Drama Generation API v1 (integrator view)

This document is for **creation-layer** clients. Field-level detail lives in OpenAPI.

| Resource | URL |
| --- | --- |
| Base URL (prod) | `https://fictora-drama-generation-prod-drama.up.railway.app` |
| OpenAPI | `/openapi.json` |
| Swagger UI | `/docs` |

## Authentication

Every `/v1` call requires:

| Header | Purpose |
| --- | --- |
| `Authorization: Bearer <service-token>` | Tenant identity |
| `X-Drama-Session-Id: <session-id>` | Client session binding |

Mutations that enqueue work also need `Idempotency-Key`.

Environment variables used by Content Desk:

```bash
export FICTORA_DRAMA_GENERATION_API_BASE_URL="https://fictora-drama-generation-prod-drama.up.railway.app"
export FICTORA_DRAMA_GENERATION_SERVICE_TOKEN="<from engineering>"
```

Never commit the token.

## Rules for creation repos

1. Call the HTTP API only. Do not embed or scrape system prompts.
2. Do not call Fal directly.
3. Treat OpenAPI as the contract when this doc and the server disagree.

## Visual-first episode flow

Content Desk implements:

- Draft: `POST /v1/prompt-video-authoring-drafts`
- Cast: `POST /v1/spines/{spine_id}/cast/enrol`, `POST .../cast/approve`
- Script: `POST /v1/spines/{spine_id}/approve`
- Boards: `POST /v1/spines/{spine_id}/boards/enrol`, `GET /v1/spines/{spine_id}/episodes/1/boards/exposure`, `POST .../boards/approve`
- Estimate: `POST /v1/spines/{spine_id}/batches/estimate`
- Video: `POST /v1/video-generations`, `GET /v1/video-generations/{job_id}/delivery`

Art-style presets: `GET /v1/art-style-presets` (ids and versions only; preset **content** is server-side).

For full endpoint lists, pilot authoring, batches, and webhooks, use OpenAPI on the deployment you target.
