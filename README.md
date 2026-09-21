# Fictora Content Creation

Public creation layer for Fictora episodic drama: operator UI, BFF, and HTTP client for the **hosted Drama Generation API**.

Partners clone **this** repository to build workflows and tools. They do **not** clone the private service repo (`fictora-drama`). System prompts and provider compilers stay on the server.

## Quick start

```bash
cp .env.example .env
# Set FICTORA_DRAMA_GENERATION_SERVICE_TOKEN

uv sync
uv run content-desk
```

Open [http://127.0.0.1:5199](http://127.0.0.1:5199).

## Docs

| Doc | Who |
| --- | --- |
| [docs/operator-guide.md](docs/operator-guide.md) | Content operators |
| [docs/drama-api-v1.md](docs/drama-api-v1.md) | Integrators |
| [docs/extending.md](docs/extending.md) | Engineers extending the desk |
| [AGENTS.md](AGENTS.md) | Cursor / Claude Code in this repo |
| [SECURITY.md](SECURITY.md) | Token and prompt boundary |

Live contract: [OpenAPI (prod)](https://fictora-drama-generation-prod-drama.up.railway.app/openapi.json).

## Docker

```bash
docker build -t fictora-content-creation .
docker run --rm -p 5199:5199 \
  -e FICTORA_DRAMA_GENERATION_SERVICE_TOKEN=... \
  -v content-desk-data:/data \
  -e CONTENT_DESK_DATA_DIR=/data \
  fictora-content-creation
```

## Tests

```bash
uv sync --group test
uv run pytest tests -q
```

## Relationship to fictora-drama

| Repository | Role |
| --- | --- |
| **fictora-content-creation** (this) | UI, BFF, operator docs — safe to share |
| **fictora-drama** (private) | API service, Restate, Postgres, **prompts**, compilers |

Service changes ship from `fictora-drama`. Creation-layer changes ship from this repo.

## Publish a new remote

This tree is meant to be its own Git repository:

```bash
git init -b main
git add -A && git commit -m "Initial content creation layer"
gh repo create DreamFlux-Workspace/fictora-content-creation --private --source=. --push
```

Adjust org/name to your GitHub layout.
