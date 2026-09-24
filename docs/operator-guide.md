# Operator guide (Cursor + this repo)

You do not clone `fictora-drama`. You clone **fictora-content-creation** and open it in Cursor. A new chat fast-forwards `main`. You do not run `git pull`.

1. Copy `.env.example` → `.env` and set `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN`.
2. Tell the agent: *Open a series floor for &lt;name&gt;. Band 15s. Four episodes.*
3. The agent runs `uv run fictora-ops init-series …` and stops at each gate.
4. You open files under `~/Downloads/documents/…` and say yes or no.

Full rules: [docs/content-ops/runbook.md](content-ops/runbook.md).

Spend (H3 lane): take ~$1.20, plate/board ~$0.30. See runbook envelopes.

Contacts: tejassingh.inbox@gmail.com, vikram@dreamflux.ai.
