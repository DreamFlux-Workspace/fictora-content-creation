# Fictora Content Creation — agent instructions

This repository is the **public creation layer** for Fictora episodic drama. Partners and operators extend this tree. They do **not** need the private `fictora-drama` service repository.

## Boundary

| In this repo | Never in this repo |
| --- | --- |
| HTTP client (`content_desk/drama_client.py`) | `prompts/`, prompt templates, compiler strings |
| BFF routes and operator UI | Fal keys, Restate workers, Postgres schema |
| Operator docs and OpenAPI links | Imports from `fictora.drama_generation` |

All model and provider behavior lives **server-side** on the hosted Drama Generation API. User-supplied **premises** in the UI are product input, not system prompts.

## Extend safely

- Add UI steps, desk fields, or BFF routes that call **documented** `/v1/*` endpoints only.
- Use `Idempotency-Key` on enrol and create routes.
- Keep secrets in environment variables. Never commit tokens.
- Contract source of truth: production OpenAPI at  
  `https://fictora-drama-generation-prod-drama.up.railway.app/openapi.json`

## Commands

```bash
uv sync --group test
uv run content-desk
uv run pytest tests -q
```

## Python quality

Public modules in `content_desk/` use numpydoc on exported callables when you change them. Run `ruff check` and `ruff format` before you finish.
