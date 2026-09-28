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
| Draft | `fictora-produce step --desk D` (prints the brief's lines against the script: kept, rewritten, cut, added) | Show the human every line that is not `kept`; fix it with `line` or accept it |
| Plates | `fictora-produce step --desk D` | `approve --desk D --gate plates` after a yes; one character wrong: `redraw-plate --desk D --cast NAME --note "…"` (that character alone, $0.30; show the contact sheet) |
| Script | (lines from the draft) | `approve --desk D --gate script` after a yes |
| Board | `fictora-produce step --desk D` (prints the shot list with who speaks on each row, speaker warnings and safe-zone warnings) | `approve --desk D --gate board`; or `redraw-board --desk D --episode N --take tK --cause "…"` |
| Estimate | `fictora-produce step --desk D` | The human says yes to the number. The line names the lane and its $/s; a `!! SERVER ESTIMATE FAILED` line means the number is the kit's local table: say that to the human before the yes |
| Take | `fictora-produce step --desk D --confirm-spend` | — |
| Read | watch `takes/`, write every fault | Use it, or Change this + cause |
| Re-film one take | `film --desk D --episode N --take tK --cause "…"` (prices it), then the same with `--confirm-spend` | The human says yes to the number; nothing else is filmed or booked |
| Finish | `fictora-produce finish --desk D [--take-file F]`, then `review --desk D --episode N --take tK` (caption boxes vs the covered zones, zone sheet for the face check; warns only) | Watch the final file; the last line must read `Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓` |
| Next episode | `arc --desk D --list` / `--pick K` (episode 2), `brief --desk D --episode N` (3 on), `author --desk D --episode N --direction K` or `--line "…"` | Script yes, then the same Board → Finish loop |

Optional after the draft, before the plates, our own style frame: write the look down with the human, then `look-frame --desk D --description @look.txt` (inline words work too) draws it on the server from the words alone ($0.30; no image goes in, and never a picture found online) into `shared/look/look-frame-vN.png` and prints its `image_url`. Show the frame. Only after the human's yes, `look --desk D --url <that image_url>` pins it; to change it, change the description and draw again (the same description never pays twice). If `look-frame` says the server has no look-frame route, stop and tell engineering; never draw it with a provider key. `look-note --desk D --add "…"` steers the drawings. Edits: `line` for a line (its words, the performed JA/KO line, its speaker, or heard-not-seen; `--add` a line to a beat, `--remove` one, `--new-voice` for someone only heard: the server and the desk change together, and it says what happens to the script approval); `edit` for a beat or frame (reference.md). `fictora-produce status --desk D` any time. After each stage: report the path, append `run-notes.md` (`fictora-ops note`), stop.

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
- **Speaking mouths.** No clench, grit, pressed or closed mouth on a speaking row. The big acting goes before the line and after it; during the words the mouth moves. Lips move on every row that carries a line; check the speaker's face is readable there.
- **Unseen movers.** Something that must never be seen moving (a statue, a doll) is banned by name in `forbidden_elements` ("no statue motion") on every frame that shows it. Then each row that shows it opens with it already in place and keeps it in the same place in both cells. The take compile enforces this, but still watch the take for a slide-in.
- **Expressions** fit the moment and the right face (library in reference.md).
- The hook, the hand-off, the safe zones, no readable text or digits. Report brightness: information only, a dark board is the human's call. To change a board, fix the frame first (`edit --frame … --set …`), then `redraw-board` (it takes no notes).

## Spend (warn, never block)

Takes film on **H3 Max Turbo image-to-video** (768p) by default: **$0.30 per 15 s take through 30 Sep 2026** ($0.02/s, fal promo), **$0.60 from 1 Oct 2026** ($0.04/s); no charge for images; a take under 5 s films and bills 5 s. H3 Max reference-to-video ($1.20 a 15 s take) is an engineering-side switch: quote it only when the estimate or the take facts name `minimax/h3-max/reference-to-video`. The desk prices whatever lane the server names. A plate or board is $0.30.

On Turbo the take is image-to-video from its storyboard board: the whole board is the video's first frame and is the only picture the model gets. Cast plates and voice references are not sent, so the board carries the look and the faces, and the voice in the raw take is the model's own. A raw take can open on a frame or two of the board grid; `finish` removes them first (deboard; `--no-deboard` to skip). Budgets: first episode **$5.50**, continuing 15 s **$2.50**, continuing 30 s **$5.00**. Say "$X of $Y" when an episode crosses its budget; past 2× the human decides. Detail: reference.md.

## Prompt policy

Compiled prompts are proprietary and stay on the server. Never fetch, save, print or quote them. Record the draft, cast, board and video **job ids** in `run-notes.md`; when a take needs its prompt looked at, hand engineering the job id.

## A take is not done until finish ran; hosted post is off

The take step stops at the raw clip: the model's sound only, no music, no effects, no captions. Hosted post-production is switched off on the API, so the finish runs on this laptop. As soon as the human says Use it, run it without being asked:

```bash
uv run fictora-produce finish --desk D [--episode N] [--take tK] [--take-file F] [--duck-db N] [--sfx-adjust "door=-6"] [--line-start S ...] [--no-colour-match] \
  [--mute A-B ...] [--voice FILE@S[@DB] ...] [--cue FILE@S[@DB] ...]
```

It lays the take's sound effects (from `GET /v1/jobs/{take_job}/take-facts`, never the prompt), the show's music bed (pinned once per show; `set-bed --desk D --path F` pins your own file), matches the look to the approved board, mixes at a measured level near −18 LUFS, burns house captions and puts the Sokii mark top left. Every step writes a new `take-epNN-tK-<step>-vN.mp4`. Needs ffmpeg with libass. Generated audio (effects, the bed, audition clips, dry lines, Whisper timings) is made on the server by the Drama API operator audio routes (`sfx-cues`, `audio-bed/render`, `voice-auditions/render`, `voice-lines`, `/v1/transcripts`); no provider key ever goes on this laptop and no local file is uploaded (transcripts read the take's stored URL). Rate limits and in-progress answers are waited out automatically. If a route refuses (`operator_audio_unavailable`, `budget_cap_exceeded`), `finish` still does the look, mix, captions and mark, reports `NOT DONE` and names the refusal: tell engineering, never add a key. Costs go to `run-notes.md` only; never quote them to anyone else.

- The last line reads `Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`. `NOT DONE` and exit code **5** mean music, SFX or the mix is missing: do not hand the file over; fix what it names and run `finish` again.
- A `409 hosted_post_off`, or a `503 restate_unavailable` from post-production, is not an outage: never retry it, run `finish`.
- Captions are always the English line. On a Japanese or Korean show, `finish` / `caption` show each whole English line over its speech (no word flicker); the run note says `whole English lines`.

## Hand sound: stray speech, a new line, a missing cue

All three are mix notes, never a re-film. Times are seconds on the take **as filmed** (the raw take): `deboard` keeps the timeline, so nothing is shifted. Each flag repeats.

- **Stray speech** (a mumble or garbled fake speech between lines, from the read): `finish --mute A-B`. The take's own audio is silent inside A-B (short fades just outside, no click), before the effects go on.
- **A new or changed line:** `voice-line --desk D --cast X --text "..." [--episode N] [--spoken-text "..."]` makes one dry line in X's locked voice on the server ($0.10 per 1,000 characters, Whisper-checked) into `epNN/voices/`. Play it to the human, then `finish --mute A-B --voice FILE@S` (mute the old line, lay the new one at its start). It is levelled to −18 LUFS unless `@DB`; the bed ducks under it like speech. A line that would run past the take is refused.
- **A sound the take's Sound lines missed:** `cue --desk D --episode N --description "a descending comic brass sting" [--seconds 1.5]` makes one cue on the server (~$0.002 a second) into `epNN/sfx/` and prints its RMS per half second and shape check. Then `finish --cue FILE@S[@DB]`: −8 dB under the take unless `@DB`, 10 dB lower while someone speaks, clamped to end 0.15 s before the take does.
- One cue per visible action, on the frame where the action lands. A cue with a bad shape is rendered once more with a changed description, then dropped with a line in the run notes; a missing cue never blocks a take. A quiet cue: raise it in 2–3 dB steps with `@DB` until it reads.
- `finish` checks every hand file before any step: missing, silent, or outside the take is an error and nothing is written. A hand step that then fails prints `NOT DONE` like missing music.

## Local edits: deboard, soften, freeze, trim, tempo

Free, on this laptop (ffmpeg + numpy). Each writes a new `take-epNN-tK-<step>-vN.mp4` and a run note; nothing is overwritten. Details and thresholds: reference.md.

- **Board frames.** A Turbo take opens on a frame or two of the still board. `finish` now removes them first (`deboard`): measured against the approved board, capped at 12, replaced with the first real frame so the length and sound do not move (take-facts cues and captions keep their times). None found: nothing written. `--no-deboard` only when the human wants the board opening kept. `CAP HIT` in the report: look at the head at full rate.
- **Order.** raw → `deboard` → `soften` / `freeze` → `finish --take-file <that file>` → `trim` / `tempo` on the finished file → watch.
- `soften` always after `deboard` (the board-to-motion jump reads as a cut). No `--cut` detects hard cuts itself; say which cuts it found.
- `freeze --at S --hold S` keeps the length: the held frame covers the picture, the sound plays on.
- `trim` only on the finished file (`--take-file` is required). Check the first frame after the cut at full size. A result under 15 s is a DEVIATION; say so.
- `tempo --factor 0.9` only for a show cut slow, and only on the finished file: `finish` lays effects at the filmed times.

```bash
uv run fictora-produce deboard --desk D [--episode N] [--take tK] [--take-file F] [--board B]
uv run fictora-produce soften  --desk D [--take-file F] [--cut S ...]
uv run fictora-produce freeze  --desk D [--take-file F] --at 6.2 --hold 0.6
uv run fictora-produce trim    --desk D --take-file FINAL --cut 10.17-12.15 [--cues-json J]
uv run fictora-produce tempo   --desk D --take-file FINAL [--factor 0.9]
```

## Change a character's voice (never regenerate)

"The voice feels off" is a voice change, not a new story and not a new video. Never re-draft or re-film everything for it.

```bash
uv run fictora-produce voice --desk D --cast NAME --audition [--episode N]   # 4-10 candidates on their real lines, $0.30
uv run fictora-produce voice --desk D --cast NAME --audition --text "…" --voices Rachel,Aria,Sarah   # one line, only those voices in the reel
uv run fictora-produce voice --desk D --cast NAME --pick N                   # after the human picks (N or the voice's name); free
uv run fictora-produce revoice --desk D --cast NAME --episode N --take tK   # each filmed take they speak in
uv run fictora-produce finish --desk D --episode N --take tK --take-file <take-epNN-tK-revoice-vN.mp4>
```

Play the listening reel (`shared/voices/<cast>/audition-vN/reel-vN.m4a`; `reel-vN.txt` says where each numbered candidate starts) to the human; a second audition set needs `--cause`. New wording: the server auditions only lines on the spine, so put the words on the line first (`edit --episode N --line-id ID --text "…"`), then `--audition --text "…"`. A set that already holds the line and the named voices makes a new reel for free. On Turbo (the default) no take carries the locked voice, because voice references are not sent: revoice each filmed take the character speaks in, including takes filmed after the pick. Only on R2V do takes not filmed yet use the new voice as they are. Re-film only a take where the dub does not sit (lips visibly wrong, a shouted line), with a cause and a stated cost; never the other takes, never the story.

An off-screen voice (speaker, phone, radio) played over another character's face is heard as that face speaking: give it a source in post with `voice-fx --file F --range A-B --preset intercom|phone|radio` (local ffmpeg, $0, a new file at the same level), then `finish --take-file` on it.

`revoice` finds a kana-pinned Japanese line in Whisper's kanji: on the per-word readings the server's transcript carries, or by reading shape on an older server. If a line is still reported "not heard", pass `--words-json` or re-film only that take.

## Recovery

- Video stuck ~50% in post: `fictora-produce cancel-job --desk D --job-id job_video_…` and stop. Never `retry-video`, never `--confirm-spend` again.
- Change this on one take (human yes + a written cause): `film --desk D --episode N --take tK --cause "…"` prices that take alone; after the yes, the same with `--confirm-spend` films only it (fresh seed) and books only it. Episodes 1..N−1 and the episode's other takes are never filmed again.
- A whole episode again (rare; human yes + cause): `film --desk D --episode N --cause "…"`, then `--confirm-spend`. `retry-video --new-paid-take` does the same for the desk's current episode, once per desk.
- `film` or `step` says the deployed API does not film one episode alone yet: nothing was sent or charged. Stop and tell engineering; never work around it.
- Interrupted poll: run the same `step` again; the job may still be running.
- More: reference.md.

## Episode 2 on

Episode 1 approved first. Ask the human how many episodes the run should be. For a long run prefer an arc with an engine (a situation that repeats with a new problem) plus a slow question; refuse an arc that closes within a few episodes. Steer each later episode by its direction in the human's words, not by series-wide notes. Never pre-stage scripts for later episodes. Commands: `arc --list [--episodes N]`, `arc --pick K`, `author --episode N --direction K | --line "…"`, then `approve --gate script` and the same loop (reference.md).

## Adopting a desk from the old internal kit

A desk made by the retired internal kit (raw takes named `take-epNN-tK-vN.mp4`, a desk-root `api/18_takes.json`, no `production.json`) does not open here until it is adopted. Run the plan first and read it with the human:

```bash
uv run fictora-ops adopt-desk --desk D --dry-run   # prints every file it would create and every inferred value
uv run fictora-ops adopt-desk --desk D             # only after the human agrees with the plan
```

It only creates files: `series.pre-adopt.json` (a backup), a hard link `take-epNN-tK-raw-vN.mp4` beside each old raw take, `epNN/api/17_raw_scene_clips.json` and `epNN/api/spine.json`, `production.config.json`, and `production.json` with the episode and phase it inferred. Nothing is renamed, moved, deleted or overwritten, and the take list's `provider_spec` is dropped unread. Running it twice changes nothing. An existing, different `production.json` stops it; `--force` replaces it only after a backup. Every `CONFIRM` line in the plan (a gate the old desk never recorded, an unfinished job, a take without a verdict) goes to the human before the next paid step.

## Setup check, one plate again, the ledger

- **First thing on a new laptop:** `uv run fictora-produce setup-check`. One ✓/✗ line each: the API token set and accepted (one free authenticated read), ffmpeg and ffprobe, libass (captions), the filters the finish and edits use, Python 3.12+, uv. Any ✗ exits 1: fix it before the first paid step. It never prints the token.
- **One character's plate again**, at the plates gate only (after `step` drew them, before `approve --gate plates`): `fictora-produce plates --desk D --cast NAME --cause "…"` redraws that one plate ($0.30) and keeps the rest of the cast. The cause is a label (the route takes no notes) and "try again" is refused. Past the plates gate it is refused before anything is sent.
- **Book by hand with a unit:** `fictora-ops spend --desk D --episode N --usd X [--take tK] --unit look-frame` (or `voice-line`, `cue:gaan-sting` …). Every booking also lands in `spend_log` in `series.json` (episode, take, dollars, unit, time); the kit's own bookings name their unit.
