# Content ops — produce a Fictora episode

You are in the **fictora-content-creation** repository. Agents use `.cursor/skills/episode-production/SKILL.md` and `uv run fictora-ops`. The hosted Drama API runs on the server; prompts are not in this repo.

Desk folders default under `~/Downloads/documents/`. API JSON goes in each episode's `api/` folder.

---

This page is the **Cursor + folder desk** kit. You do not type raw HTTP. You open files and say yes.

The process that made One More Round, Same Floor, and Sauce Left Over is the [Episode Production Runbook](runbook.md). This page is how you run that process without becoming an engineer.

## What you need

1. Cursor or Claude Code, opened on **fictora-content-creation** (not fictora-drama).
2. A drama API service token from engineering. Put it in the repo `.env` as `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN`. Never paste the token into chat.
3. A place for the run folder. Default: `~/Downloads/documents/`. Keep every series beside the last one.

You do not edit `src/`. You do not change product settings to get a different look.

## What you do

You are the gate. The agent draws, films, and mixes. You look at the file and answer.

| Stage | You open | You say |
| --- | --- | --- |
| Brief | `brief.md` | yes, or rewrite the line |
| Plates | `plates/` | yes, or what is wrong with the face |
| Script | `brief.md` lines | yes, with original + translation |
| Board | `boards/` newest version | yes, after the agent reports brightness |
| Cost | the number in chat | yes to spend, or stop |
| Take | `takes/` raw file | Use it, or Change this plus a cause |
| Mix | the mixed file, then the captioned file | mix notes, not a re-film |

Silence is not consent. A yes from last episode does not carry forward.

## What the agent does

The agent follows [`.cursor/skills/episode-production/SKILL.md`](../../.cursor/skills/episode-production/SKILL.md) (Claude Code: `.claude/skills/episode-production/`).

It must:

- Stop after every stage. Report the path. Wait.
- Use the Drama Generation API via `creation.harness` for every **Aligned** stage. See [api-map.md](api-map.md).
- Say **DEVIATION** before any Gap or Partial stage, in four lines, before doing it.
- Never overwrite a file. New version, new `-vN` suffix.
- Never call `scripts/drama_create_flow_smoke.py` for a real production. That script approves plates, script, and boards without you.

## Start a series

Say this to the agent:

> Open a series floor for \<series name\>. Band is 15s. Four episodes. Folder under `~/Downloads/documents/`.

The agent asks where the desk should go, then runs:

```bash
uv run fictora-ops init-series \
  --series "<series name>" \
  --band 15s \
  --episodes 4
```

It prints the desk path. Open `QUEUE.md`. That is the review surface. Episode folders sit side by side. Shared plates live in `shared/plates/`.

A single-folder `init` still exists for one off-desk experiment. Real content work uses the floor. See [floor.md](floor.md).

Then give the brief: premise, cast, set, arc, and whether plates already exist.

## Spend

Lane today is H3 Max. Price a 15s take at **$1.20**. A plate or board is **$0.30**.

| Episode | Envelope |
| --- | --- |
| Continuing 15s, existing cast | $2.50 |
| Continuing 30s | $4.00 |
| First episode of a new series | $4.50 |

If spend passes twice the envelope, stop and escalate.

A second render of the same plate, board, or take needs a written cause in the direction. "Try again" is not a cause. "Change this" buys a fresh render at full price.

## After the film

An episode is done when every approved line is heard, each take reads as one move, the last frame is saved as the hand-off, captions match the recipe in the runbook, and `run-notes.md` has the ledger.

A series is done when there is a joined cut, a write-up from [templates/series-writeup.md](templates/series-writeup.md), and learnings sorted into:

1. a rule for the runbook
2. a change to the scripts
3. a product gap in [backlog.md](backlog.md)

Send the write-up to tejassingh.inbox@gmail.com and vikram@dreamflux.ai.

## Files in this kit

| File | Who reads it |
| --- | --- |
| [runbook.md](runbook.md) | You and the agent. Source of truth. |
| [api-map.md](api-map.md) | The agent. Aligned vs gap. |
| [checklists.md](checklists.md) | Both. Gates and take read. |
| [backlog.md](backlog.md) | Engineering. Gaps the productions keep hitting. |
| [templates/](templates/) | Copied into each run folder. |
| [floor.md](floor.md) | Parallel series desk and preflight. |
