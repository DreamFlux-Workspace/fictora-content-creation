# Content creation repository

Episode operators and external integrators use **[fictora-content-creation](https://github.com/DreamFlux-Workspace/fictora-content-creation)**, not this repo.

| Need | Repository |
| --- | --- |
| Run series, approve gates, film takes | `fictora-content-creation` |
| API service, prompts, Fal pipeline, migrations | `fictora-drama` (private) |

Creation repo contains:

- Content Desk (FastAPI BFF + static UI)
- HTTP client for `/v1/*`
- Operator and integrator docs (OpenAPI links only)

It does **not** contain `prompts/`, drama Python packages, or content-ops Cursor skills that reference internal scripts.

After you create the GitHub remote, update clone URLs in `apps/content-desk/README.md` if the org or name differs.
