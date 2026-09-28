# Fictora Content Creation

Partner repo for **episode production** with Cursor or Claude Code against the hosted **Drama Generation API** (`/v1/*`). You get a desk folder (plates, boards, takes), human gates, and a CLI that calls the API—no UI, no `fictora-drama` clone, no system prompts in this tree.

**Sharing with a producer?** Ops PDF: [Content-Operator-Guide.pdf](docs/content-ops/Content-Operator-Guide.pdf) (source: [Content-Operator-Guide.md](docs/content-ops/Content-Operator-Guide.md)). Setup PDF: [Content-Producer-Setup-Handout.pdf](docs/Content-Producer-Setup-Handout.pdf). Longer guide: [Episode-Production-Partner-Guide.md](docs/Episode-Production-Partner-Guide.md).

| Layer | This repo | Private `fictora-drama` |
| --- | --- | --- |
| HTTP harness + gated stages | `creation.harness` | — |
| Desk layout + gate ledger | `fictora-ops` | — |
| Phase orchestration | `fictora-produce` | — |
| Agent runbook | `.cursor/skills/episode-production/` | — |
| Local finish (mix, colour, captions, mark, revoice lay-in; generated audio requested from the API) | `creation/post` (`fictora-produce finish`, `voice`, `revoice`) | — |
| Prompts, server Fal keys, Restate workers | — | yes |

## Requirements

- Python **3.12+**
- [uv](https://docs.astral.sh/uv/)
- Service token for the deployed Drama API (from engineering)
- ffmpeg with libass, for the local finish (no provider key: generated audio is made on the server)

## Quick start

```bash
git clone git@github.com:DreamFlux-Workspace/fictora-content-creation.git
cd fictora-content-creation

cp .env.example .env
# Set FICTORA_DRAMA_GENERATION_SERVICE_TOKEN in .env (never commit it)

uv sync
```

Open the repo in **Cursor**. A new chat fast-forwards `main`. You do not run `git pull`. For production work, the agent should read `.cursor/skills/episode-production/SKILL.md` (Claude Code: `.claude/skills/episode-production/SKILL.md`).

### Start a series desk

```bash
uv run fictora-produce start \
  --series "My Show" \
  --prompt "Premise, cast, set, visual lock, one 15s horror beat…" \
  --band 15s \
  --preset-id modern-dark-fantasy \
  --video-lane minimax-h3 \
  --clip-seconds 15 \
  --cut-tempo punchy \
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

# Episode 2 on (after episode 1's script is approved)
uv run fictora-produce arc --desk … --list --episodes 30     # then: arc --desk … --pick 2
uv run fictora-produce author --desk … --episode 2 --direction 1   # or --line "the human's words"

# Edits and redraws (see the skill's reference.md)
uv run fictora-produce edit --desk … --episode 1 --frame 3 --set shot_scale="close up"
uv run fictora-produce redraw-board --desk … --episode 1 --take t1 --cause "face under the caption band"
uv run fictora-produce check-lines --desk … --episode 1

# After estimate is shown in chat (films the desk's current episode alone)
uv run fictora-produce step --desk … --confirm-spend

# Re-film one take of episode N (Change this + a written cause); prices first, films with --confirm-spend
uv run fictora-produce film --desk … --episode 2 --take t2 --cause "what in the direction made the fault"
uv run fictora-produce film --desk … --episode 2 --take t2 --cause "…" --confirm-spend

uv run fictora-produce status --desk …
uv run fictora-produce config --desk …
```

**Visual-first episode 1 order:** draft → cast enrol → **plate gate** → script gate → boards enrol → **board gate** → estimate → spend confirm → take (video) → delivery in `ep01/takes/`.

Trust **`GET /v1/video-generations/{id}`** for job status during long post-prod (often ~50% for many minutes). Poll transport blips are retried automatically; if the CLI exits, re-run `step` on the same desk—it resumes the job in `16_video_enrol.json` without re-enrolling.

### Finish a take (hosted post is off)

The raw take has the model's sound only. After the human says Use it:

```bash
uv run fictora-produce finish --desk ~/Downloads/documents/my-show-…
```

Sound effects (from the take facts), the show's music bed, colour match to the approved board, a mix near −18 LUFS, captions, the Sokii mark. Each step writes a new `take-epNN-tK-<step>-vN.mp4`. The last line must read `Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`; `NOT DONE` exits 5.

### Change a character's voice (never regenerate)

```bash
uv run fictora-produce voice --desk … --cast NAME --audition
uv run fictora-produce voice --desk … --cast NAME --pick N
uv run fictora-produce revoice --desk … --cast NAME --episode 1 --take t1
uv run fictora-produce finish --desk … --take-file <take-ep01-t1-revoice-vN.mp4>
```

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

- **Episode 1 is drafted alone** (`outline_mode=arc_at_episode_two`); `draft_episode_count` is ignored. Later episodes: `arc`, then `author --episode N`.
- **`caption_style: house`** — burn-in captions are applied in post-production, not by the video model.
- **`fallback_estimate_usd`** (unset) — per-take dollars used only when the lane the server names has no verified price in `creation/prices.py`; unset prices it at the H3 Max Turbo rate. Takes are priced from the server's estimate first, then from the dated table on the endpoint the server says it films on (H3 Max Turbo I2V by default).

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
  cli_post.py       finish, voice, revoice, set-bed
  post/             Local finish: sfx, bed, colour, mix, watermark, voice, Whisper; audio_service = the one generated-audio interface
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
