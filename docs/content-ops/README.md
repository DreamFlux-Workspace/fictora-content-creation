# Content ops — produce a Fictora episode

You are in the **fictora-content-creation** repository. Agents use `.cursor/skills/episode-production/SKILL.md` and `uv run fictora-ops`. The hosted Drama API runs on the server; prompts are not in this repo.

Desk folders default under `~/Downloads/documents/`. API JSON goes in each episode's `api/` folder.

---

This page is the **Cursor + folder desk** kit. You do not type raw HTTP. You open files and say yes.

**Operators (Mihir, Tejas):** [Content-Operator-Guide.pdf](Content-Operator-Guide.pdf) · [Content-Operator-Guide.md](Content-Operator-Guide.md) — setup pointer, Cursor prompts, gates, `fictora-produce` commands, local captions.

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
- Use the `fictora-produce` / `fictora-ops` commands for every **Aligned** stage, never hand-written HTTP. See [api-map.md](api-map.md).
- Say **DEVIATION** before any Gap or Partial stage, in four lines, before doing it.
- Never overwrite a file. New version, new `-vN` suffix.
- Never call `scripts/drama_create_flow_smoke.py` for a real production. That script approves plates, script, and boards without you.

## Start a series

Say this to the agent:

> Open a series floor for \<series name\>. Band is 15s. Episode 1 only. Folder under `~/Downloads/documents/`.

The agent asks where the desk should go, then runs:

```bash
uv run fictora-ops init-series \
  --series "<series name>" \
  --band 15s \
  --episodes 1
```

It prints the desk path. Open `QUEUE.md`. That is the review surface. Episode folders sit side by side. Shared plates live in `shared/plates/`.

A single-folder `init` still exists for one off-desk experiment. Real content work uses the floor. See [floor.md](floor.md).

Then give the brief: premise, cast (with real ages), set, the hook, the lines, and whether plates already exist. No series arc yet: the arc is chosen at episode 2.

## Coming from the internal kit

The internal kit in fictora-drama (`scripts/content_ops_run.py`) is retired. This kit makes the same episodes from this repo against the same deployed API; nothing here imports fictora-drama.

**Desks it made.** A desk made by the internal kit (raw takes named `take-epNN-tK-vN.mp4`, a desk-root `api/18_takes.json`, no `production.json`) opens here once it is adopted. Read the plan with the human first; it only ever creates files (a backup of `series.json`, hard links to the raw takes, the files this kit reads) and never renames, moves or overwrites anything:

```bash
uv run fictora-ops adopt-desk --desk D --dry-run   # the plan: every file it would create, every inferred value
uv run fictora-ops adopt-desk --desk D             # after the human agrees
```

Every `CONFIRM` line in the plan (a gate the old desk never recorded, an unfinished job, a take without a verdict) goes to the human before the next paid step. Details: the skill, "Adopting a desk from the old internal kit".

**Commands.** `P` is `uv run fictora-produce`, `O` is `uv run fictora-ops`. `--help` on any command is the truth for its flags.

| Internal kit (`content_ops_run.py …`) | This kit |
| --- | --- |
| `init-series --series S --band B --episodes N` | `O init-series` (same flags), or `P start --series S --prompt @brief.md --band B --preset-id ID --cut-tempo T` (opens the desk and binds the premise) |
| `draft --brief FILE --preset ID --tempo T [--language ja]` | `P start … --prompt @FILE --preset-id ID --cut-tempo T [--language ja]`, then `P step --desk D` |
| `look-frame --prompt DESC.txt` | `P look-frame --desk D --description @DESC.txt` |
| `look --image FRAME.png` | `P approve --desk D --gate look` (pins the newest look frame and records the yes; `--url` for a frame the human picked) |
| `look-note --add/--remove` | `P look-note --desk D --add/--remove` (same) |
| `plates` | `P step --desk D` (draws the cast at that phase) |
| `plates --cause "…"` (the whole cast again) | `P redraw-plate --desk D --cast NAME --note "…"` (one character, corrected) |
| `board --episode N` | `P step --desk D` (draws the current episode's boards) |
| `board --episode N --take tK --cause "…"` | edits first (`P edit --frame/--beat`, `P look-note`), then `P redraw-board --desk D --episode N --take tK --cause "…" [--note "…"]` (stops unpaid when nothing changed; `--reroll` for a random bad draw) |
| `film --episode N --take tK` (first film) | `P step --desk D` (estimate), then `P step --desk D --confirm-spend` |
| `verdict --change --cause "…"`, then `film` | `P film --desk D --episode N --take tK --cause "…"` (prices), then the same with `--confirm-spend` |
| `approve --gate look\|plates\|script\|board` | `P approve --desk D --gate look\|plates\|script\|board` (look: pins the frame and records the yes; the others send the API approval); `O approve` records a desk-only yes (post; look without a pin) |
| `set-lines`; a line PATCH (was a deviation) | `O set-lines` (desk only); `P line` (server and desk together, also `--add`, `--remove`, `--new-voice`) |
| `edit`, `spine --refresh`, `arc`, `author`, `memory`, `voice`, `revoice`, `voice-line` | `P` with the same name |
| `review --take … --whisper` | `P review --desk D --episode N --take tK [--transcribe]`; `P check-lines` for the line check alone |
| `sfx`, `mix`, `captions`, `colour-match`, `watermark` | `P finish` runs them all; `--sfx-adjust`, `--duck-db`, `--bed-db`, `--no-colour-match`, `--colour-strength`, `--watermark-y` change one step; `P caption` burns captions alone on a raw take |
| `finish … --voice/--mute/--cue` | `P finish … --voice FILE@S[@DB] --mute A-B --cue FILE@S[@DB]` (same idea) |
| `cue --name N --text "…" --seconds S` | `P cue --desk D --episode N --description "…" [--seconds S]` |
| `set-bed --spine-json … / --library` | `P set-bed --desk D --path FILE`, or let `finish` make and pin the show's bed |
| `deboard`, `soften`, `freeze`, `trim`, `tempo` | `P` with the same name, on `--desk D [--take-file F]` |
| `join --part t1 --part t2 …` | `P join --desk D --episode N` (or `--episodes 1 2 3`, or `--take-file F …`); needs a finish record per take, so re-run `finish` on a take finished before the join landed |
| `preflight`, `status`, `spend`, `estimate`, `handoff`, `filmed`, `verdict`, `next-path` | `O` with the same name |
| `sync-repo`, `setup-check` | `P setup-check` (token, ffmpeg + libass, filters, Georgia Italic warning, Python, uv) |

## Spend

Lane today is H3 Max Turbo image-to-video (the server's default; H3 Max reference-to-video is an engineering-side switch). A 15s take is **$0.30** through 30 Sep 2026 ($0.02/s, fal promo) and **$0.60** from 1 Oct ($0.04/s). The board is the only picture the take gets: no cast plates, no voice references. A plate or board is **$0.30**.

| Episode | Budget |
| --- | --- |
| Continuing 15s, existing cast | $2.50 |
| Continuing 30s | $5.00 |
| Continuing 60s | $8.00 |
| First episode of a new series | $5.50 |

Budgets warn, they never block. Past twice the budget, the agent says so and you decide.

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
| [sound-cues.md](sound-cues.md) | The agent. Placing a hand sound cue. |
| [templates/](templates/) | Copied into each run folder. |
| [floor.md](floor.md) | Parallel series desk and preflight. |
