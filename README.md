# Fictora Content Creation

Public repo for **producing episodes** with Cursor (or Claude Code) against the hosted **Drama Generation API**. No UI required. No `fictora-drama` clone. No system prompts.

| Layer | This repo | Private `fictora-drama` |
| --- | --- | --- |
| HTTP client + stages | `creation.harness` | — |
| Desk folders + gates | `fictora-ops` CLI | — |
| Agent orchestration | `.cursor/skills/episode-production/` | — |
| Prompts, Fal, Restate | — | yes |

## Setup

```bash
cp .env.example .env
# FICTORA_DRAMA_GENERATION_SERVICE_TOKEN from engineering

uv sync
```

Open this folder in **Cursor**. The **episode-production** skill drives the same MiniMax H3 flow as internal content ops: draft → cast → script → boards → estimate → take.

## Commands

```bash
# Series desk under ~/Downloads/documents/
uv run fictora-ops init-series --series "My Show" --band 15s --episodes 4

# Versioned filenames, notes, preflight, gate ledger
uv run fictora-ops next-path --dir ./plates --stem plate-hero --suffix .png
uv run fictora-ops preflight --desk <path> --episode 1 --take t1
```

API work uses `creation.harness.DramaApiRunSession` and `creation.harness.stages_gated` (see skill and [docs/extending.md](docs/extending.md)).

## Docs

- [docs/content-ops/](docs/content-ops/) — runbook, api-map, checklists (copied for operators)
- [docs/drama-api-v1.md](docs/drama-api-v1.md) — integrator view + OpenAPI link
- [AGENTS.md](AGENTS.md) — agent rules in this repo

OpenAPI (prod): https://fictora-drama-generation-prod-drama.up.railway.app/openapi.json

## Tests

```bash
uv sync --group test && uv run pytest tests -q
```

## Publish

```bash
git remote add origin git@github.com:DreamFlux-Workspace/fictora-content-creation.git
git push -u origin main
```
