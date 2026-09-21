# Fictora Content Creation

Partner repo for **episode production** with Cursor or Claude Code against the hosted **Drama Generation API** (`/v1/*`). You get a desk folder (plates, boards, takes), human gates, and a CLI that calls the API—no UI, no `fictora-drama` clone, no system prompts in this tree.

**Sharing with a producer?** PDF handout: [Content-Producer-Setup-Handout.pdf](docs/Content-Producer-Setup-Handout.pdf) (source: [Content-Producer-Setup-Handout.md](docs/Content-Producer-Setup-Handout.md)). Longer guide: [Episode-Production-Partner-Guide.md](docs/Episode-Production-Partner-Guide.md).

| Layer | This repo | Private `fictora-drama` |
| --- | --- | --- |
| HTTP harness + gated stages | `creation.harness` | — |
| Desk layout + gate ledger | `fictora-ops` | — |
| Phase orchestration | `fictora-produce` | — |
| Agent runbook | `.cursor/skills/episode-production/` | — |
| Prompts, Fal, Restate workers | — | yes |

## Requirements

- Python **3.12+**
- [uv](https://docs.astral.sh/uv/)
- Service token for the deployed Drama API (from engineering)

## Quick start

```bash
git clone git@github.com:DreamFlux-Workspace/fictora-content-creation.git
cd fictora-content-creation

cp .env.example .env
# Set FICTORA_DRAMA_GENERATION_SERVICE_TOKEN in .env (never commit it)

uv sync
```

Open the repo in **Cursor**. For production work, the agent should read `.cursor/skills/episode-production/SKILL.md` (Claude Code: `.claude/skills/episode-production/SKILL.md`).

### Start a series desk

```bash
uv run fictora-produce start \
  --series "My Show" \
  --prompt "Premise, cast, set, visual lock, one 15s horror beat…" \
  --band 15s \
  --preset-id modern-dark-fantasy \
  --video-lane minimax-h3 \
  --draft-episodes 4 \
  --clip-seconds 15 \
  --cut-tempo one_shot \
  --caption-style house
```

This creates a folder under `~/Downloads/documents/…`, writes `production.json` + `production.config.json`, and sets phase `new`. The printed path is your **desk**.

### Operator loop

One **paid API enrol block** per agent turn unless you explicitly catch up.

```bash
# Automated step (draft, cast, boards, estimate, or video poll)
uv run fictora-produce step --desk ~/Downloads/documents/my-show-…

# Human gates (after reviewing plates/, script, boards/)
uv run fictora-produce approve --desk … --gate plates
uv run fictora-produce approve --desk … --gate script
uv run fictora-produce approve --desk … --gate board
uv run fictora-produce approve --desk … --gate board --accept-dim   # when exposure is below dim floor

# After estimate is shown in chat
uv run fictora-produce step --desk … --confirm-spend

uv run fictora-produce status --desk …
uv run fictora-produce config --desk …
```

**Visual-first episode 1 order:** draft → cast enrol → **plate gate** → script gate → boards enrol → **board gate** → estimate → spend confirm → take (video) → delivery in `ep01/takes/`.

Trust **`GET /v1/video-generations/{id}`** for job status during long post-prod (often ~50% for many minutes). Poll transport blips are retried automatically; if the CLI exits, re-run `step` on the same desk—it resumes the job in `16_video_enrol.json` without re-enrolling.

### Recovery

```bash
# Stuck or failed video job
uv run fictora-produce cancel-job --desk … --job-id job_video_…
uv run fictora-produce step --desk … --confirm-spend
```

## Configuration

| File | Role |
| --- | --- |
| `.env` | API base URL, service token, optional global session id |
| `<desk>/production.config.json` | Draft count (**use 4**, not 5), clip length, captions, estimate fallback, poll deadlines |
| `<desk>/production.json` | Phase machine, spine id, session id, video retry suffix |

Example desk config: `.cursor/skills/episode-production/config.example.json`.

Important defaults:

- **`draft_episode_count: 4`** — five planned episodes breaks estimate/plan on prod.
- **`caption_style: house`** — burn-in captions are applied in post-production, not by the video model.
- **`fallback_estimate_usd: 1.20`** — used when batch estimate is skipped.

## Desk utilities (`fictora-ops`)

Lower-level desk tooling (init layout, versioned paths, preflight, gate ledger):

```bash
uv run fictora-ops init-series --series "My Show" --band 15s --episodes 4
uv run fictora-ops next-path --dir ./plates --stem plate-hero --suffix .png
uv run fictora-ops preflight --desk <path> --episode 1 --take t1
uv run fictora-ops status --desk <path>
```

Use **`fictora-produce`** for the aligned API pipeline; use **`fictora-ops`** for filenames, notes, and review queue.

## Documentation

- [docs/content-ops/](docs/content-ops/) — runbook, API map, checklists
- [docs/drama-api-v1.md](docs/drama-api-v1.md) — integrator overview
- [docs/extending.md](docs/extending.md) — harness extension notes
- [AGENTS.md](AGENTS.md) — rules for coding agents in this repo

OpenAPI (prod): https://fictora-drama-generation-prod-drama.up.railway.app/openapi.json

## Tests and smoke

```bash
uv sync --group test
uv run pytest tests -q
```

Optional live checks (uses token from `.env`):

```bash
uv run python scripts/smoke_live.py --phase read
uv run python scripts/smoke_live.py --phase draft   # plan job — LLM spend
```

E2E visual-first script (long-running, prod API):

```bash
uv run python scripts/e2e_visual_first_ep1.py --help
```

## Project layout

```
creation/
  harness/          DramaApiRunSession, stages_gated, HTTP poll helpers
  orchestrate.py    fictora-produce phase machine
  cli_produce.py    fictora-produce entrypoint
  cli_ops.py        fictora-ops entrypoint
  production_*.py   Desk config + state
  ops/              Desk floor, luma, gates
  recover.py        Cancel job + video retry suffix
tests/              Unit tests (no live API)
scripts/            smoke_live, e2e helpers
docs/content-ops/   Operator runbook (human + agent)
```

## Contributing

- Do not commit `.env` or tokens.
- When changing Python under `creation/`, use numpydoc on public callables and run `uv run pytest tests -q`.
- Partners orchestrate with the **episode-production** skill; do not hand-roll `/v1` except debugging.

## License

See repository defaults for DreamFlux / Fictora partner use.
