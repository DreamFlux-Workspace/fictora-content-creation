# Fictora episode production — operator guide

**Audience:** Mihir, Tejas, and anyone filming ep1 on prod with Cursor or Claude Code + this repo.  
**Updated:** 28 September 2026 · **Tooling:** `uv run fictora-produce` (not raw API calls).

**PDF:** [Content-Operator-Guide.pdf](Content-Operator-Guide.pdf) (regenerate: `bash scripts/render_operator_guide_pdf.sh`)

This is the day-to-day playbook. Craft rules and API detail live in [runbook.md](runbook.md) and [api-map.md](api-map.md). First-time machine setup: [Content-Producer-Setup-Handout.md](../Content-Producer-Setup-Handout.md).

---

## What you are doing

You produce **9:16 vertical ~15s takes** on Fictora’s hosted Drama API. You work in two places:

| Place | What happens |
| --- | --- |
| **Cursor** (this repo open) | The agent runs one paid API step per turn; you say **aligned**, **yes**, or **deviation**. |
| **Desk folder** (`~/Downloads/documents/YYYY-MM-DD-series-slug/`) | You open **plates**, **boards**, and **takes** and approve before money moves. |

You never need the private `fictora-drama` repo. Never paste the API token into chat.

**Two rules above everything else:**

1. **A human says yes before money moves.** Plates, script, board, the spend yes. One gate per yes. Silence is not approval.
2. **Render once.** A second render of a plate, board or take needs a written cause naming what in the direction went wrong. "Try again" is not a cause.

**Order:** Brief → Draft → Cast plates → Script yes → Board → Estimate → Take → Read → Finish.

---

## One-time setup (each person, ~10 min)

1. Clone `DreamFlux-Workspace/fictora-content-creation` and run `uv sync`.
2. Copy `.env.example` → `.env` and set **`FICTORA_DRAMA_GENERATION_SERVICE_TOKEN`** (from engineering) (nothing else: no provider key goes on this laptop; generated audio is made on the server).
3. Open the **repo root** in Cursor (so the **episode-production** skill loads).
4. Optional check (no spend): `uv run python scripts/smoke_live.py --phase read`
5. For the local finish (sound effects, music, mix, captions, mark), install **ffmpeg with libass** on macOS:
   `brew install ffmpeg` (use `ffmpeg-full` only if `ffmpeg -filters` lists no `ass`). `fictora-produce start` warns when it is missing.

---

## Start a new series (paste into Cursor)

Use a **new series name** and a **dated slug** (folder name is automatic, e.g. `2026-09-22-tram-not-tonight`).

```
Read `.cursor/skills/episode-production/SKILL.md` and follow it exactly.

Goal: Produce episode 1 on prod Drama API — new series.

Story (ep1 only — no series arc yet):
- Vertical 15s, modern-dark-fantasy preset, minimax-h3.
- [Setting, beat, up to three lines and who each is said to, cast with real ages, no gore.]
- Hook: open mid-motion on a face, first line by ~0.5 s, the reveal by ~3 s.

Desk:
- `uv run fictora-produce start` with series "…" and your premise.
- Use `--clip-seconds 15`, `--cut-tempo punchy` (or `one_shot` for a monologue or a walk), `--caption-style house`. Episode 1 is drafted alone.
- Do NOT pass `--api-captions` (default: raw clip on server, captions on laptop).

Workflow:
- One `fictora-produce step` per turn until I approve gates.
- After boards, estimate → I say yes → `step --confirm-spend`.
- When I say Use it, run `uv run fictora-produce finish --desk <desk>`. The last line must read `Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`; NOT DONE (exit 5) is not a deliverable.

Start with `start`, print desk path and phase, wait for my "aligned" before the first paid step.
```

**Prod-validated example** (copy premise flags from here): [examples/tram-not-tonight-ep1.md](examples/tram-not-tonight-ep1.md).

---

## Your gates (human yes before money)

| Order | You open | You say | Agent runs after your yes |
| --- | --- | --- | --- |
| 1 | — | **aligned** | First `step` (draft plan) |
| 2 | `ep01/plates/` | **plates ok** / **next** | `approve --gate plates` then `step`; one character wrong: `redraw-plate --cast NAME --note "…"` |
| 3 | Script in chat or `ep01/api/03_spine.json` beats | **script ok** / **next** | `approve --gate script` then `step` (boards) |
| 4 | `ep01/boards/` + exposure note in chat | **board ok** / **next** | `approve --gate board` (brightness is information only; to change it: `edit --frame`, then `redraw-board`) |
| 5 | Estimate in chat (~**$0.30** take; $0.60 from 1 Oct) | **yes** | `step --confirm-spend` |
| 6 | The raw take, then `ep01/takes/take-ep01-t1-sokii-vN.mp4` | **Use it**, or **Change this** + cause | `finish` on Use it; re-board or re-film only with a named cause |

**Silence is not approval.** Each gate needs a fresh yes.

---

## What to check at each gate

**Script.** Three lines at most per 15 s take. A silent comic beat (a pause, a stare) costs a line. Every line is said to someone. Any line the writers changed from your brief is shown to you beside the new one. Japanese or Korean lines sound like a native speaker in that situation; a dialect needs a native speaker's yes. Captions are English for now.

**Board.** Frame 0 is mid-motion on a face. Every action follows a visible face reacting to its cause. Each row opens on the emotion the last row ended on. Each speaker is in frame, medium or closer, on the row where they speak; no off-screen speaker is drawn. No clenched or closed mouth while someone speaks. Expressions fit the moment and sit on the right face.

**Social safe zones (TikTok, Reels, Shorts).** No face, eyes, mouth or key prop in the top 8%, the bottom 20%, or the right 12% of the lower two thirds. Faces do not have to be centred. Captions sit between 55% and 70% of the height. The Sokii mark goes top left.

**Age.** Characters can be any age. Nobody under 18 is ever in a romance.

---

## Phase cheat sheet (what “next” means)

| Phase | Meaning |
| --- | --- |
| `new` | Desk ready; waiting for **aligned** → draft |
| `wait_plates` | Review cast stills |
| `wait_script` | Review ep1 lines on spine |
| `wait_board` | Review storyboard; check dim warning (~25% luma floor) |
| `wait_spend` | Confirm **yes** before the take (~$0.30; $0.60 from 1 Oct) |
| `complete` | Raw MP4 on disk → `finish` + QC |

Check anytime:

```bash
uv run fictora-produce status --desk ~/Downloads/documents/<your-desk-folder>
```

---

## Commands (you or the agent)

Replace `<desk>` with the full path printed at `start`.

```bash
# Create desk (no API spend yet)
uv run fictora-produce start \
  --series "Series Title" \
  --prompt "Premise…" \
  --band 15s \
  --preset-id modern-dark-fantasy \
  --video-lane minimax-h3 \
  --clip-seconds 15 \
  --cut-tempo punchy \
  --caption-style house

# One automated API block per invocation
uv run fictora-produce step --desk <desk>

# Human gates
uv run fictora-produce approve --desk <desk> --gate plates
uv run fictora-produce approve --desk <desk> --gate script
uv run fictora-produce approve --desk <desk> --gate board

# Film take (after you said yes to estimate)
uv run fictora-produce step --desk <desk> --confirm-spend
```

**Episode 2 on:** `arc --desk <desk> --list --episodes N`, `arc --pick K`, then `author --desk <desk> --episode 2 --direction K` (or `--line "…"`), and the same gates.

**Do not use `--api-captions`** unless engineering asks — it slows post and waits on server burn-in.

---

## After the raw take (local finish)

Default config: **`api_captions: false`**. Filming finishes when the **raw** scene MP4 is downloaded (often within a minute after MiniMax returns). Hosted post is off, so the raw take has the model's sound only: no music, no effects, no captions.

One command, after you say Use it:

```bash
uv run fictora-produce finish --desk <desk>
```

| File | Role |
| --- | --- |
| `ep01/takes/take-ep01-t1-raw-v1.mp4` | As filmed; never the deliverable |
| `ep01/takes/take-ep01-t1-sfx-v1.mp4` | Sound effects laid from the take facts |
| `ep01/takes/take-ep01-t1-colour-v1.mp4` | Look matched to your approved board |
| `ep01/takes/take-ep01-t1-mix-v1.mp4` | Music bed under the voice, near −18 LUFS |
| `ep01/takes/take-ep01-t1-cap-v1.mp4` | House captions |
| `ep01/takes/take-ep01-t1-sokii-v1.mp4` | **Ship candidate**: Sokii mark top left |

The last line reads `Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`. **NOT DONE** (exit 5) names what is missing; that file is not a deliverable. Wrong caption timing? Re-run with `--line-start <seconds>` and/or `--line-end <seconds>` once per line. Bed too loud under a line? `--duck-db 12`.

A character's voice feels off? Never regenerate: `voice --audition`, you pick, `voice --pick N`, `revoice` the takes they speak in, then `finish --take-file <revoice file>` (skill: "Change a character's voice").

---

## Desk layout

```
2026-09-22-my-series/
  production.json          ← phase, spine_id, session (agent)
  production.config.json   ← 15s, one_shot, house, api_captions false
  QUEUE.md                 ← what episode is waiting on
  ep01/
    plates/                ← gate 1
    boards/                ← gate 2
    takes/                 ← raw + captioned video
    api/                   ← JSON log (support / debugging)
    run-notes.md           ← ledger (agent updates)
    brief.md
```

---

## Spend (H3 lane, approximate)

| Unit | ~USD |
| --- | --- |
| Cast plate (each; full + bust per character) | $0.30 |
| Storyboard | $0.30 |
| 15s take (H3 Max Turbo I2V, the default: $0.02/s through 30 Sep 2026, $0.04/s from 1 Oct) | $0.30 / $0.60 |
| 15s take on H3 Max R2V (engineering switch only, $0.08/s) | $1.20 |

| Budget | USD |
| --- | --- |
| First episode of a new series | **$5.50** |
| Continuing 15s | $2.50 |
| Continuing 30s | $5.00 |

Budgets warn, they never block. The agent says "$X of $Y" when an episode crosses its budget; past **2×** you decide whether to go on.

A **second** take needs a **named cause** (brief/board issue), not “try again.”

---

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Empty `plates/` or `boards/` right after step | Pull latest `main`; re-run `step` on same desk if phase allows, or ask agent to download from `ep01/api/06_*cast_terminal*.json` / `09_*boards_terminal*.json`. |
| Network error while polling | Same desk, run `step` again — job may still be running server-side. |
| Stuck at 50% on **video** with **local captions** | Often post-prod you can ignore; with `api_captions: false` the harness should already stop at raw clip. Check for `17_raw_scene_clips.json`. |
| Dark board (~14–24% luma) | Information only. Approve it if the mosaic looks right on screen. |
| Draft or author failed | The message names the rule that failed; fix the brief or the direction, do not re-run blind. |
| Token / 503 / billing | Engineering — do not rotate token in chat. |

Video stuck in post: `fictora-produce cancel-job` and stop. Do not `retry-video`. A second enrol starts another ffmpeg job on Railway.

---

## Rules of the road

- Open every gate file before you say ok.
- A take is not done until the local finish ran. Never ship the raw MP4.
- Hosted post is off. A `hosted_post_off` (or `503 restate_unavailable`) answer from post-production means finish locally; it is not an outage.
- Compiled prompts stay on the server. The agent records job ids in `run-notes.md`; if a take needs its prompt looked at, send engineering the job id.
- A voice that feels off is a voice change, not a new story or a new video. Never regenerate everything for it.
- Episode 2 on: episode 1 approved first, then decide how many episodes the run is. For a long run pick an arc with a repeating engine plus a slow question. Each episode is steered by its own direction in your words.
- One **paid** enrol per agent turn unless you explicitly ask to catch up.
- Never commit `.env` or share the service token.
- Never approve plates, script, and board in one message without looking.

---

## References

| Doc | Use |
| --- | --- |
| [examples/tram-not-tonight-ep1.md](examples/tram-not-tonight-ep1.md) | Copy-paste start command + prod outcomes |
| [Content-Producer-Setup-Handout.md](../Content-Producer-Setup-Handout.md) | Setup + PDF source |
| [local-captions.md](local-captions.md) | Caption timing detail |
| [runbook.md](runbook.md) | Craft, sound, caption recipe |
| `.cursor/skills/episode-production/SKILL.md` | Agent contract |

**Support:** Token, outages, billing → DreamFlux engineering. Day-to-day gates → this guide + Cursor agent.
