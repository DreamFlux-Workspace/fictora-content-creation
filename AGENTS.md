# Fictora Content Creation — agent instructions

Partners clone **this repo only**. They orchestrate episode production with the **episode-production** skill and the hosted Drama API. They never need `fictora-drama`.

## Boundary

| Allowed | Forbidden |
| --- | --- |
| `creation.harness.*` HTTP to `/v1/*` | `prompts/`, drama `src/` |
| `uv run fictora-ops …` desk tooling | Fal keys, provider compile strings |
| User premises in API bodies | Scraping or storing system prompts |

## Fix bugs everywhere they apply

Fictora is one product in three repos:

| Repo | What it is |
| --- | --- |
| `DreamFlux-Workspace/fictora-drama` | The server (Drama API): pipelines, prompts, contracts, finishing |
| `DreamFlux-Workspace/fictora-app` | The creator app (Expo client + BFF) |
| `DreamFlux-Workspace/fictora-content-creation` | The operator kit (`fictora-produce` / `fictora-ops` + the episode-production skill) |

A bug found in one repo is often the same gap in another: a rule, default,
message, validation, or a route or field one side never uses. For every bug fix:

1. **Check the other two repos** for the same gap before calling the fix done.
2. **Fix it in each repo where it applies:** the rule in the server, what creators
   see in the app, what operators run in the kit. One PR per repo; link the
   sibling PRs in each PR body.
3. **Say why when a repo is left alone**, in the PR body (e.g. "app: operator-only
   route", "kit: keeps a per-take verdict on purpose").
4. **Write it down when you can't fix it there** (no access, or it waits on a
   server deploy): server or app gaps in the PR body and the app's `STATUS.md`;
   kit gaps in `docs/content-ops/backlog.md`.
5. **Merge the server first**, then the app and the kit. The app and the kit must
   keep working against a server that doesn't have the change yet (fall back,
   or refuse clearly before sending).

Deliberate differences are fine (operator-only tools, the app's auto-keep vs the
kit's per-take verdict), but they must be written down, never accidental.

From this repo: the Boundary above still holds. A server fix is made in
`fictora-drama` itself, never by adding drama code or prompts here. Partners
who only have this repo record the gap in `docs/content-ops/backlog.md` and
tell the Fictora team.

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
