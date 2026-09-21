# Episode production — Content Desk operator guide

**Audience:** Content operators (no codebase required)  
**App:** Fictora Content Creation (`content-desk`)

## What you do

You are the gate. The desk calls the hosted API. You approve plates, script, boards, and spend before the take films.

1. Human yes before paid work moves forward.
2. One render per paid unit unless direction gives a written cause for a redo.

## Setup

1. Clone **fictora-content-creation** (not fictora-drama).
2. Copy `.env.example` to `.env`.
3. Set `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN` from engineering. Never paste it into chat.
4. Run:

```bash
uv sync
uv run content-desk
```

5. Open `http://127.0.0.1:5199`.

## Flow in the UI

| Step | Action |
| --- | --- |
| New series | Title, premise, band, preset, lane → **Start draft** |
| Plates | **Enrol plates** → review images → **Approve plates** |
| Script | **Approve script** |
| Board | **Enrol board** → **Measure exposure** → **Approve board** |
| Cost | **Estimate** → confirm spend |
| Take | **Enrol take** → watch delivery |

Desk files: `~/Downloads/content-desk-data` by default.

## Spend (H3 lane, reference)

| Item | Approx |
| --- | --- |
| 15s take | $1.20 |
| Plate or board | $0.30 |
| First episode new series | ~$4.50 envelope |

Stop and escalate if spend exceeds twice the envelope.

## What you do not need

- The fictora-drama repository
- Cursor skills or `scripts/` from the service repo
- Fal or provider keys

Questions: tejassingh.inbox@gmail.com, vikram@dreamflux.ai.
