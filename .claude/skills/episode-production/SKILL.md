---
name: episode-production
description: >-
  Makes Fictora episodes from the terminal with fictora-produce and fictora-ops
  against the deployed Drama Generation API: human gates before money, render
  once, the hook, social safe zones, board checks, spend and recovery. Use for
  content ops, starting a series, drawing plates or boards, filming a take,
  local captions, or E2E validation in fictora-content-creation.
---

# Episode production

You are the agent. The human is the gate. The desk folder is the review surface.

**Repo:** `fictora-content-creation` only. **API:** deployed Drama Generation `/v1/*`. **Tools:** `uv run fictora-produce` (the stages) and `uv run fictora-ops` (desk, ledger, notes). `--help` on any command is the truth for its flags.

Read when you need the detail, not all on the first turn:

- Craft, spend, recovery, API contract: [reference.md](reference.md)
- Full rules and why: [runbook.md](../../../docs/content-ops/runbook.md)
- Gate stop cards: [checklists.md](../../../docs/content-ops/checklists.md)
- Human guide: [Content-Operator-Guide.md](../../../docs/content-ops/Content-Operator-Guide.md)

## Two rules

1. **A human says yes before money moves.** Plates, script, board, the spend yes. One gate per yes. No batched gates. Silence is not consent; a yes from last time does not carry forward.
2. **Render once.** Every paid unit is rendered once. A second render needs a written cause naming what in the direction produced the fault. "Try again" is not a cause.

## Never

- Chain two stages in one turn, or run two `step` calls in one turn unless the human asked to catch up.
- Approve plates, script or a board in the turn you enrolled them.
- Overwrite a desk file. New version, new `-vN` name (`fictora-ops next-path`).
- Write your own HTTP around the API. Where no command exists it is a DEVIATION (reference.md) and a backlog line.
- Fetch, save, print or quote a compiled prompt (`GET /v1/jobs/{id}/provider-spec`). Record job ids instead (Prompt policy).
- Clone or read `fictora-drama`. Edit product code or settings to get a different look.
- Print or commit `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN`.
- Retry a video job stuck in post. `cancel-job` it.
- Call hosted post-production, or pass `--api-captions`. Hosted post is off.
- Regenerate the story or re-film every take to change a voice.
- Hand over a raw take. A take is not done until the local finish ran.

## Stage order

```
1 Brief → 2 Draft → 3 Plates → 4 Script yes → 5 Board → 6 Estimate → 7 Take → 8 Read → 9 Finish
```

| Stage | Command | Gate |
| --- | --- | --- |
| Desk | `fictora-produce start --series "…" --prompt "…" --band 15s --preset-id ID --video-lane minimax-h3 --clip-seconds 15 --cut-tempo T` | Say **aligned** before the first paid step |
| Draft | `fictora-produce step --desk D` | Compare brief lines with the spine |
| Plates | `fictora-produce step --desk D` | `approve --desk D --gate plates` after a yes |
| Script | (lines from the draft) | `approve --desk D --gate script` after a yes |
| Board | `fictora-produce step --desk D` | `approve --desk D --gate board --path <boards/…png> [--accept-dim]` |
| Estimate | `fictora-produce step --desk D` | The human says yes to the number |
| Take | `fictora-produce step --desk D --confirm-spend` | — |
| Read | watch `takes/`, write every fault | Use it, or Change this + cause |
| Finish | `fictora-produce finish --desk D [--take-file F]` | Watch the final file; the last line must read `Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓` |

`fictora-produce status --desk D` any time. After each stage: report the path, append `run-notes.md` (`fictora-ops note`), stop.

## Brief

Write it with the human (`docs/content-ops/templates/brief.md`) and check before the draft:

- **Premise:** a visible physical event with a small turn. Beats are what hands, bodies and objects do, in order.
- **Episode 1 alone.** Do not ask for a series arc; do not outline four or five episodes. The arc is chosen at episode 2.
- **Hook** (below) is in the first beat.
- **Lines:** three at most per 15 s take, one per beat. A 15 s take is three beats, so a silent comic beat (a pause, a stare, a freeze) costs a line: say so and let the human choose. Ask who each line is said TO. Mark lines that must survive word for word.
- **Off-screen voices:** declare as voice only; never drawn, not even a sleeve or a shadow. Over another character's face it reads as that face speaking: give it a source (a wall grille, a phone in hand) or a post treatment.
- **Age:** any age; write the real age. Nobody under 18 in romance, ever (reference.md).
- **Language:** JA/KO lines must sound native in that situation; pin performed lines; a dialect needs a native speaker's yes. Captions are English only for now.

## Hook: the first three seconds

- Frame 0 is mid-motion on a face (a hand already moving, a head already turning). It is the cover. Never an establishing wide, never a still.
- The first line lands by about 0.5 s.
- The reveal (what the episode is about) is on screen or said by about 3 s.

A board that opens on a wide or a pause is redrawn with that as the shape note, before any take. A weak open is never fixed by re-filming.

## Social safe zones (TikTok, Reels, Shorts)

On 9:16 the platforms cover the **top 8%**, the **bottom 20%**, and the **right 12% of the lower two thirds**.

- Faces, eyes, mouths and key props never sit there. Everything else is free; do not centre faces by default.
- Captions sit in the band **55–70%** of the height.
- The Sokii mark goes **top left**, just under the top strip (23:121 on 768×1344, 0.6 opacity). Never top right.

## Board gate

Open the newest `boards/` file and check (full list: [checklists.md](../../../docs/content-ops/checklists.md)):

- **Visible cause:** every action row follows a visible face reacting to its cause, or shares the frame with it.
- **Each row opens on the emotion the row before it ended on.**
- **Speakers:** a spoken line sits on a row where its speaker is in frame, medium or closer. No off-screen speaker is drawn.
- **Speaking mouths:** no clench, grit, pressed or closed mouth on a speaking row. Big acting goes before and after the line; during the words the mouth moves.
- **Expressions** fit the moment and the right face (library in reference.md).
- The hook, the hand-off, the safe zones, no readable text or digits. Report brightness; a dark board is the human's call (`--accept-dim`).

## Spend (warn, never block)

Takes film on H3 Max R2V: **$1.20 per 15 s take** ($0.08/s). A plate or board is $0.30. Budgets: first episode **$5.50**, continuing 15 s **$2.50**, continuing 30 s **$5.00**. Say "$X of $Y" when an episode crosses its budget; past 2× the human decides. Detail: reference.md.

## Prompt policy

Compiled prompts are proprietary and stay on the server. Never fetch, save, print or quote them. Record the draft, cast, board and video **job ids** in `run-notes.md`; when a take needs its prompt looked at, hand engineering the job id.

## A take is not done until finish ran; hosted post is off

The take step stops at the raw clip: the model's sound only, no music, no effects, no captions. Hosted post-production is switched off on the API, so the finish runs on this laptop. As soon as the human says Use it, run it without being asked:

```bash
uv run fictora-produce finish --desk D [--episode N] [--take tK] [--take-file F] [--duck-db N] [--sfx-adjust "door=-6"] [--line-start S ...] [--no-colour-match]
```

It lays the take's sound effects (from `GET /v1/jobs/{take_job}/take-facts`, never the prompt), the show's music bed (pinned once per show; `set-bed --desk D --path F` pins your own file), matches the look to the approved board, mixes at a measured level near −18 LUFS, burns house captions and puts the Sokii mark top left. Every step writes a new `take-epNN-tK-<step>-vN.mp4`. Needs `FAL_KEY` in `.env` and ffmpeg with libass.

- The last line reads `Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`. `NOT DONE` and exit code **5** mean music, SFX or the mix is missing: do not hand the file over; fix what it names and run `finish` again.
- A `409 hosted_post_off`, or a `503 restate_unavailable` from post-production, is not an outage: never retry it, run `finish`.

## Change a character's voice (never regenerate)

"The voice feels off" is a voice change, not a new story and not a new video. Never re-draft or re-film everything for it.

```bash
uv run fictora-produce voice --desk D --cast NAME --audition [--episode N]   # 4-10 candidates on their real lines, $0.30
uv run fictora-produce voice --desk D --cast NAME --pick N                   # after the human picks; free
uv run fictora-produce revoice --desk D --cast NAME --episode N --take tK   # each filmed take they speak in
uv run fictora-produce finish --desk D --episode N --take tK --take-file <take-epNN-tK-revoice-vN.mp4>
```

Play the candidates (`shared/voices/<cast>/audition-vN/`) to the human; a second audition set needs `--cause`. Takes not filmed yet use the new voice as they are. Re-film only a take where the dub does not sit (lips visibly wrong, a shouted line), with a cause and a stated cost; never the other takes, never the story.

## Recovery

- Video stuck ~50% in post: `fictora-produce cancel-job --desk D --job-id job_video_…` and stop. Never `retry-video`, never `--confirm-spend` again.
- A second paid take, after a human yes and a written cause, once per desk: `retry-video --desk D --new-paid-take`, then `step --confirm-spend`.
- Interrupted poll: run the same `step` again; the job may still be running.
- More: reference.md.

## Episode 2 on

Episode 1 approved first. Ask the human how many episodes the run should be. For a long run prefer an arc with an engine (a situation that repeats with a new problem) plus a slow question; refuse an arc that closes within a few episodes. Steer each later episode by its direction in the human's words, not by series-wide notes. Never pre-stage scripts for later episodes. Commands: reference.md.
