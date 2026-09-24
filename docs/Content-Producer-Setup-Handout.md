# Fictora episode production — setup handout for content producers

**Repository:** https://github.com/DreamFlux-Workspace/fictora-content-creation  
**Audience:** Content producers (e.g. Mihir, Tejas) using Cursor with the episode-production skill  
**Updated:** September 2026

**Day-to-day operations:** [Content-Operator-Guide.pdf](content-ops/Content-Operator-Guide.pdf) · [Markdown source](content-ops/Content-Operator-Guide.md) (gates, commands, local captions).

---

## 1. What this is

You produce short **9:16 vertical episodes** (~15 seconds) using:

- This **GitHub repo** (tools + agent instructions only)
- **Cursor** (AI agent runs production steps)
- The hosted **Drama Generation API** (drawing, boards, filming, post on Fictora servers)

You do **not** need the private `fictora-drama` repository. Your review surface is a **desk folder** on your Mac (`~/Downloads/documents/…`) with `plates/`, `boards/`, and `takes/`.

You **approve** faces, script lines, and storyboards **before** the ~$1.20 take is filmed.

---

## 2. What you need from DreamFlux / engineering

| Item | Notes |
| --- | --- |
| GitHub access | Clone `DreamFlux-Workspace/fictora-content-creation` |
| API service token | See Section 3 — required in `.env` |
| Cursor (recommended) | Open the cloned repo as the workspace |
| macOS | Tested workflow; Linux likely works with `uv` |

**Security:** Never paste the API token into Cursor chat, Slack, or email. Never commit the `.env` file.

---

## 3. Environment variables (required setup)

Create a file named `.env` in the repo root (copy from `.env.example`).

### Required

| Variable | Description |
| --- | --- |
| `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN` | Bearer token for all `/v1/` API calls. **Required.** Ask engineering if you do not have it. |

### Optional

| Variable | If unset |
| --- | --- |
| `FICTORA_DRAMA_GENERATION_API_BASE_URL` | Defaults to production: `https://fictora-drama-generation-prod-drama.up.railway.app` |
| `FICTORA_DRAMA_GENERATION_SESSION_ID` | Each desk stores its own session in `production.json` after you run `start` — you usually leave this commented out |

### Minimal `.env` example

```env
FICTORA_DRAMA_GENERATION_API_BASE_URL=https://fictora-drama-generation-prod-drama.up.railway.app
FICTORA_DRAMA_GENERATION_SERVICE_TOKEN=paste-your-token-here
```

### Desk files (not environment variables)

After you start a series, the agent uses files on your desk:

| File | Purpose |
| --- | --- |
| `<desk>/production.json` | Current phase, spine id, session id |
| `<desk>/production.config.json` | Clip length (15s), captions (`house`), draft episode count (**4**) |

---

## 4. One-time machine setup (~10 minutes)

1. **Install uv** (Python tool runner): https://docs.astral.sh/uv/

2. **Clone the repo:**

   ```bash
   git clone https://github.com/DreamFlux-Workspace/fictora-content-creation.git
   cd fictora-content-creation
   ```

3. **Configure credentials:**

   ```bash
   cp .env.example .env
   ```

   Open `.env` in a text editor and set `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN`.

4. **Install project dependencies:**

   ```bash
   uv sync
   ```

5. **Open the repo folder in Cursor.** A new chat fast-forwards `main`. You do not run `git pull`. The included **episode-production** skill tells the agent how to run production safely (human gates, one paid step per turn).

6. **Verify read-only API access (optional):**

   ```bash
   uv run python scripts/smoke_live.py --phase read
   ```

   This checks presets and desk tooling without spending on LLM/video.

---

## 5. Production flow (your role vs the agent)

```
start → [YOU: aligned] → draft → cast plates → [YOU: plates ok] → script → [YOU: script ok]
    → storyboard → [YOU: board ok] → estimate → [YOU: yes] → raw take
    → local house captions → [YOU: review captioned MP4]
```

Default: **no `--api-captions`** — filming stops at the **raw** clip; yellow subtitles are burned on your Mac (see [Content-Operator-Guide.md](content-ops/Content-Operator-Guide.md)).

| Step | You open | You say |
| --- | --- | --- |
| Plates | `ep01/plates/` | “plates ok” or what is wrong |
| Script | lines in chat / `scripts/` | “script ok” or revised lines |
| Board | `ep01/boards/` | “board ok” (or accept dim board if agent warns) |
| Cost | number in chat | “yes” to spend, or stop |
| Take | `ep01/takes/` | use it, or ask for a fix with a clear cause |

**Silence is not approval.** Each episode needs fresh yeses at each gate.

### Starting a new series (paste to Cursor)

Use the full template in [Content-Operator-Guide.md](content-ops/Content-Operator-Guide.md) (includes `--draft-episodes 4`, `--cut-tempo one_shot`, local captions). Short form:

> Read episode-production SKILL.md. `fictora-produce start` for “[Series Name]”, 15s, modern-dark-fantasy, minimax-h3, premise: [your prompt]. One step per turn; wait for my aligned / yes at gates.

---

## 6. Desk folder layout

```
my-series-2026-09-22/
  production.json
  production.config.json
  ep01/
    brief.md
    plates/          ← approve faces
    scripts/
    boards/          ← approve storyboard
    takes/           ← finished video
    api/             ← JSON logs (debugging)
    run-notes.md
```

---

## 7. Useful commands (agent usually runs these)

| Action | Command |
| --- | --- |
| Start desk | `uv run fictora-produce start --series "…" --prompt "…" --band 15s` |
| Next API step | `uv run fictora-produce step --desk <desk-path>` |
| Approve gate | `uv run fictora-produce approve --desk <path> --gate plates` (or `script`, `board`) |
| Approve dim board | add `--accept-dim` to board approve |
| Confirm spend + film | `uv run fictora-produce step --desk <path> --confirm-spend` |
| Status | `uv run fictora-produce status --desk <path>` |

---

## 8. Captions and audio

- Subtitles are **not** in the model output. Default path: **local burn** after the raw take (`api_captions: false`).
- Review **`take-ep01-t1-captioned-v*.mp4`**, not only the raw file. Agent runs `scripts/burn_house_captions.sh` (needs ffmpeg-full + libass).
- Captions appear only around the spoken line—scrub to that beat. Style: yellow `#FFE500`, Poppins Bold, black edge, no box, text bottom at 70% of frame height ([local-captions.md](content-ops/local-captions.md)).

---

## 9. When something goes wrong

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| “Error reading body from connection” | Network blip while polling | Run `step` again on the **same desk**; job often still running server-side |
| Stuck at **50%** on video poll | Server post-prod tail | With local captions, harness should finish at **raw clip** (`17_raw_scene_clips.json`); ask agent to check job / re-step |
| **503 restate_unavailable** | Server orchestration down | Retry later; contact engineering |
| No file in `takes/` | Poll stopped early or delivery not fetched | Re-run `step` on same desk (resume without double-charging if enrol already saved) |

---

## 10. Rules of the road

- Do **not** clone `fictora-drama`.
- Do **not** approve plates, script, and board without opening the files.
- Do **not** share your token or commit `.env`.
- Do **not** film twice in parallel on the same desk without engineering guidance.

---

## 11. More documentation (in the repo)

- [docs/content-ops/Content-Operator-Guide.md](content-ops/Content-Operator-Guide.md) — **primary ops doc** (Mihir / Tejas)
- `README.md` — technical overview
- `docs/Episode-Production-Partner-Guide.md` — expanded partner guide
- `docs/content-ops/runbook.md` — full production craft runbook
- `docs/content-ops/examples/tram-not-tonight-ep1.md` — prod-validated ep1 example
- `.cursor/skills/episode-production/SKILL.md` — agent instructions

---

## 12. Support

- **Token, outages, billing:** DreamFlux / Fictora engineering contact  
- **Day-to-day production:** Cursor + this repo + human gates in Section 5
