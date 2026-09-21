# Fictora episode production — setup handout for content producers

**Repository:** https://github.com/DreamFlux-Workspace/fictora-content-creation  
**Audience:** Content producers using Cursor with the episode-production skill  
**Updated:** September 2026

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

5. **Open the repo folder in Cursor.** The included **episode-production** skill tells the agent how to run production safely (human gates, one paid step per turn).

6. **Verify read-only API access (optional):**

   ```bash
   uv run python scripts/smoke_live.py --phase read
   ```

   This checks presets and desk tooling without spending on LLM/video.

---

## 5. Production flow (your role vs the agent)

```
Brief + draft → Cast plates → [YOU: plates ok] → Script → [YOU: script ok]
    → Storyboard → [YOU: board ok] → Price estimate → [YOU: yes to spend]
    → Film take (long wait) → Review video in takes/
```

| Step | You open | You say |
| --- | --- | --- |
| Plates | `ep01/plates/` | “plates ok” or what is wrong |
| Script | lines in chat / `scripts/` | “script ok” or revised lines |
| Board | `ep01/boards/` | “board ok” (or accept dim board if agent warns) |
| Cost | number in chat | “yes” to spend, or stop |
| Take | `ep01/takes/` | use it, or ask for a fix with a clear cause |

**Silence is not approval.** Each episode needs fresh yeses at each gate.

### Starting a new series (paste to Cursor)

> Start episode production for “[Series Name]”. Band 15s, MiniMax H3, visual-first ep1. Premise: [your prompt]. Put the desk under `~/Downloads/documents/`.

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

- Subtitles are **not** generated by the video model. They are **burned in during post-production** (default style: `house` — yellow text, black edge).
- Mid-job previews often have **no captions** yet. Use the file in `takes/` after the job reaches **completed**.
- Captions may only appear for **a few seconds** around the spoken line—scrub to that beat when reviewing.

---

## 9. When something goes wrong

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| “Error reading body from connection” | Network blip while polling | Run `step` again on the **same desk**; job often still running server-side |
| Stuck at **50%** 20–30+ min | Post-production (mix, captions) | Wait; ask agent to check API job status |
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

- `README.md` — technical overview
- `docs/Episode-Production-Partner-Guide.md` — expanded partner guide
- `docs/content-ops/runbook.md` — full production craft runbook
- `.cursor/skills/episode-production/SKILL.md` — agent instructions

---

## 12. Support

- **Token, outages, billing:** DreamFlux / Fictora engineering contact  
- **Day-to-day production:** Cursor + this repo + human gates in Section 5
