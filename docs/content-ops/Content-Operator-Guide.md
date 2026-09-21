# Fictora episode production — operator guide

**Audience:** Mihir, Tejas, and anyone filming ep1 on prod with Cursor + this repo.  
**Updated:** September 2026 · **Tooling:** `uv run fictora-produce` (not raw API calls).

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

---

## One-time setup (each person, ~10 min)

1. Clone `DreamFlux-Workspace/fictora-content-creation` and run `uv sync`.
2. Copy `.env.example` → `.env` and set **`FICTORA_DRAMA_GENERATION_SERVICE_TOKEN`** (from engineering).
3. Open the **repo root** in Cursor (so the **episode-production** skill loads).
4. Optional check (no spend): `uv run python scripts/smoke_live.py --phase read`
5. For local yellow captions after filming, install **ffmpeg with libass** on macOS:
   `brew install ffmpeg-full` (agent uses `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`).

---

## Start a new series (paste into Cursor)

Use a **new series name** and a **dated slug** (folder name is automatic, e.g. `2026-09-22-tram-not-tonight`).

```
Read `.cursor/skills/episode-production/SKILL.md` and follow it exactly.

Goal: Produce episode 1 on prod Drama API — new series.

Story (ep1):
- Vertical 15s, modern-dark-fantasy preset, minimax-h3.
- [Setting, beat, one speaking line, cast notes, no gore.]

Desk:
- `uv run fictora-produce start` with series "…" and your premise.
- Use `--draft-episodes 4`, `--clip-seconds 15`, `--cut-tempo one_shot`, `--caption-style house`.
- Do NOT pass `--api-captions` (default: raw clip on server, captions on laptop).

Workflow:
- One `fictora-produce step` per turn until I approve gates.
- After boards, estimate → I say yes → `step --confirm-spend`.
- When raw MP4 lands, burn house captions with `scripts/burn_house_captions.sh` and open the captioned take.

Start with `start`, print desk path and phase, wait for my "aligned" before the first paid step.
```

**Prod-validated example** (copy premise flags from here): [examples/tram-not-tonight-ep1.md](examples/tram-not-tonight-ep1.md).

---

## Your gates (human yes before money)

| Order | You open | You say | Agent runs after your yes |
| --- | --- | --- | --- |
| 1 | — | **aligned** | First `step` (draft plan) |
| 2 | `ep01/plates/` | **plates ok** / **next** | `approve --gate plates` then `step` |
| 3 | Script in chat or `ep01/api/03_spine.json` beats | **script ok** / **next** | `approve --gate script` then `step` (boards) |
| 4 | `ep01/boards/` + exposure note in chat | **board ok** / **next** | `approve --gate board --path …` (add **`--accept-dim`** if board is dark night interior) |
| 5 | Estimate in chat (~**$1.20** take) | **yes** | `step --confirm-spend` |
| 6 | `ep01/takes/*captioned*.mp4` | use or **deviation** + cause | Re-board or re-film only with a named cause |

**Silence is not approval.** Each gate needs a fresh yes.

---

## Phase cheat sheet (what “next” means)

| Phase | Meaning |
| --- | --- |
| `new` | Desk ready; waiting for **aligned** → draft |
| `wait_plates` | Review cast stills |
| `wait_script` | Review ep1 lines on spine |
| `wait_board` | Review storyboard; check dim warning (~25% luma floor) |
| `wait_spend` | Confirm **yes** before ~$1.20 take |
| `complete` | Raw MP4 on disk → local captions + QC |

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
  --draft-episodes 4 \
  --clip-seconds 15 \
  --cut-tempo one_shot \
  --caption-style house

# One automated API block per invocation
uv run fictora-produce step --desk <desk>

# Human gates
uv run fictora-produce approve --desk <desk> --gate plates
uv run fictora-produce approve --desk <desk> --gate script
uv run fictora-produce approve --desk <desk> --gate board \
  --path <desk>/ep01/boards/board-ep01-t1-1-v1.png
# If exposure was below dim floor and you accept the dark board:
#   ... --accept-dim

# Film take (after you said yes to estimate)
uv run fictora-produce step --desk <desk> --confirm-spend
```

**Do not use `--draft-episodes 5`** — prod plans four episodes; five causes plan errors.

**Do not use `--api-captions`** unless engineering asks — it slows post and waits on server burn-in.

---

## After the raw take (local house captions)

Default config: **`api_captions: false`**. Filming finishes when the **raw** scene MP4 is downloaded (often within a minute after MiniMax returns). You do **not** wait for server caption burn.

| File | Role |
| --- | --- |
| `ep01/takes/take-ep01-t1-raw-v1.mp4` | Silent of burned captions; use for timing |
| `ep01/api/17_raw_scene_clips.json` | Same clip URL |
| `ep01/takes/take-ep01-t1-captioned-v1.mp4` | **Ship candidate** after local burn |

Agent workflow ([local-captions.md](local-captions.md)):

1. Line text from spine / script gate (e.g. *“Not tonight.”*).
2. `ffmpeg` **silencedetect** on the raw take → anchor captions to **speech span**, not long pauses.
3. Write `take-ep01-t1-house.ass` (yellow `#F5D547`, black outline, no box).
4. Burn:

```bash
cd /path/to/fictora-content-creation
FFMPEG=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg \
  bash scripts/burn_house_captions.sh \
  <desk>/ep01/takes/take-ep01-t1-raw-v1.mp4 \
  <desk>/ep01/takes/take-ep01-t1-house.ass \
  <desk>/ep01/takes/take-ep01-t1-captioned-v1.mp4
open <desk>/ep01/takes/take-ep01-t1-captioned-v1.mp4
```

Scrub to the spoken line; captions should flicker word-by-word in the lower centre band.

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
| Cast plates (batch) | $0.30 |
| Storyboard | $0.30 |
| 15s take | $1.20 |
| **First ep envelope** | **~$4.50** |

Stop and escalate if spend goes past **2×** the envelope without a written reason.

A **second** take needs a **named cause** (brief/board issue), not “try again.”

---

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Empty `plates/` or `boards/` right after step | Pull latest `main`; re-run `step` on same desk if phase allows, or ask agent to download from `ep01/api/06_*cast_terminal*.json` / `09_*boards_terminal*.json`. |
| Network error while polling | Same desk, run `step` again — job may still be running server-side. |
| Stuck at 50% on **video** with **local captions** | Often post-prod you can ignore; with `api_captions: false` the harness should already stop at raw clip. Check for `17_raw_scene_clips.json`. |
| Board “dim” warning (~14–24% luma) | Night interiors are common; approve with **`--accept-dim`** if the mosaic looks right on screen. |
| Plan fails on episode 5 | Set **`draft_episode_count` to 4** only. |
| Token / 503 / billing | Engineering — do not rotate token in chat. |

Video recovery (agent, same desk): `fictora-produce cancel-job` → `retry-video` → `step --confirm-spend` (see episode-production skill).

---

## Rules of the road

- Open every gate file before you say ok.
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
