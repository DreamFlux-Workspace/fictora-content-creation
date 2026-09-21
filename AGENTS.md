# Fictora Content Creation — agent instructions

Partners clone **this repo only**. They orchestrate episode production with the **episode-production** skill and the hosted Drama API. They never need `fictora-drama`.

## Boundary

| Allowed | Forbidden |
| --- | --- |
| `creation.harness.*` HTTP to `/v1/*` | `prompts/`, drama `src/` |
| `uv run fictora-ops …` desk tooling | Fal keys, provider compile strings |
| User premises in API bodies | Scraping or storing system prompts |

## Default workflow

1. `uv run fictora-ops init-series …`
2. One aligned API stage per turn via `DramaApiRunSession` + `stages_gated`
3. Human gate recorded with `fictora-ops approve` and files in `plates/`, `boards/`, `takes/`
4. JSON artefacts under `api/`

Read `.cursor/skills/episode-production/SKILL.md` before production work.

## Quality

When editing Python in `creation/`, use numpydoc on public callables and run:

```bash
uv run pytest tests -q
```
