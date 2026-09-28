# Episode production — reference

Detail behind [SKILL.md](SKILL.md). The rules and their history: [runbook.md](../../../docs/content-ops/runbook.md).

## Configuration

Three layers, later wins:

| Layer | Location | Purpose |
| --- | --- | --- |
| Environment | repo `.env` | `FICTORA_DRAMA_GENERATION_API_BASE_URL`, `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN` (never print), optional `FICTORA_DRAMA_GENERATION_SESSION_ID` |
| Desk config | `<desk>/production.config.json` | Written by `start` / `bind`; read with `fictora-produce config --desk D` |
| Desk state | `<desk>/production.json` | Phase, `session_id`, `spine_id`, `video_idempotency_suffix`. Do not hand-edit unless recovering |

| `production.config.json` field | Default | Use |
| --- | --- | --- |
| `draft_episode_count` | 1 | Ignored. The draft writes episode 1 alone (`outline_mode=arc_at_episode_two`); later episodes are written with `author --episode N` |
| `clip_duration_seconds` | 15 | 4–15. 16 is rejected |
| `cut_tempo` | unset (server default `punchy`) | Pick per scene (Shot plan, below). Set it at `start` (`--cut-tempo`): it goes on the draft, so board and take agree |
| `caption_style` | `house` | Local burn-in recipe ([local-captions.md](../../../docs/content-ops/local-captions.md)) |
| `api_captions` | `false` | Keep false. The take step stops at the raw clip; captions run on the laptop |
| `locale` | `en-US` | Draft locale |
| `spoken_language` | unset (English) | `--language ja` / `ko` at `start`: the cast speaks it; captions stay English |
| `fallback_estimate_usd` | 1.20 | Used only when the batch estimate sends no dollars and the price table has no rate for the lane |
| `poll_*_deadline_seconds` | 1800–7200 | Poll caps (below) |

## Phase machine (`fictora-produce`)

One machine per desk, pointed at one episode (`episode_ordinal` in `production.json`; `status` prints it). `author --episode N` points it at episode N with its lines at the script gate. `epNN` below is that episode.

| Phase | Next move |
| --- | --- |
| `new` | `step` → draft |
| `ready_cast_enrol` | `step` → cast plates + downloads to `ep01/plates/` (episode 1 only; books $0.30 a plate) |
| `wait_plates` | Human yes → `approve --gate plates` |
| `wait_script` | Human yes on the lines (printed by the draft or `author`; `epNN/api/spine.json`) → `approve --gate script` (episode 1: the whole spine; episode 2 on: `pilot-episodes/{n}/approve`) |
| `ready_boards_enrol` | `step` → boards to `epNN/boards/board-epNN-tK-vN.png`, brightness (information only), the shot list row by row, safe-zone warnings; books $0.30 a board |
| `wait_board` | Human yes → `approve --gate board` (the boards `step` drew; `--path` only for a board it did not draw). Or `redraw-board` |
| `ready_estimate` | `step` → this episode's estimate, and only this episode (earlier episodes are not filmed again): the server's dated dollars, else the price table; says "$X of a $Y envelope" (warn only) |
| `wait_spend` | Human yes → `step --confirm-spend` |
| `ready_video` | (internal) take enrol |
| `complete` | Every take of the episode raw on disk (`epNN/takes/take-epNN-tK-raw-vN.mp4`) with its take facts (`epNN/api/take-facts-epNN-tK-vN.json`); spend booked from the facts; video and take job ids in `run-notes.md` → `finish` per take, then the next episode |
| `failed` | Read `last_error` in `production.json`; see Recovery |

The harness auto-retries `plan_media_spine_version_stale` (cast, boards) and retryable `authoring_stalled` (draft). Any other failed job, and any refused request, stops with the server's own code, message and rule details (for example `authoring_validation_failed at frames[3]: …`, or a take refused because it would drop an approved line): read it to the human; do not re-run blind.

## Aligned or deviation

Before any paid step, say one word: **aligned** or **deviation**. If deviation, post before doing it:

```
DEVIATION — this is not the product flow
What I'm about to do: <the thing>
Why the API can't:     <the endpoint or field that refuses it>
Product gap:           <one line for docs/content-ops/backlog.md>
Extra cost:            <$ and minutes>
```

Always a deviation: a length other than 15 / 30 / 60 s; any route with no command here (a single-plate redraw); hand-rolled HTTP. Editing product code or settings is never acceptable. Aligned, with commands (below): episode 2 on (`arc`, `brief`, `author`), line and frame edits (`edit`), board redraws (`redraw-board`; the regenerate route takes no notes, so the change goes in first with `edit --frame` or `look-note`).

## Script gate

- Compare every brief line with the spine. The writers may cut or rewrite a line. Show the human each changed or vanished line beside the spine line. A "keep exactly" line that changed is a fault: fix it before the gate.
- Show each line in the language it will be spoken, with the translation beside it.
- **Japanese / Korean:** the register follows who speaks to whom (staff to customer is 申し訳ございません / 정말 죄송합니다, not ごめんなさい). No English quip carried word for word. No notice-board noun stack in speech (「逆襲中止！」 "counterattack cancelled!") unless the character is really announcing. The server repairs that one form once; everything else is your check. Pin the exact performed line (`spoken_text`) so localization does not rewrite it. A dialect (Kansai-ben, Busan satoori) needs a native speaker's yes before the script gate; if nobody can check it, write the standard language.
- **Captions** are English only for now (house style). Japanese / Korean captions are deferred.

## Characters and age

Characters may be any age: children, teenagers, adults, the elderly. Write the real age ("10, primary-school kid", "sixteen", "late twenties") and the plate draws it.

The hard line: never romantic, sexual, suggestive or fan-service framing of anyone under 18. Their age and body are never sexualised, and they are never in a romance arc (no love interest, crush, dating, or someone else's romance aimed at them). A romance lead is 18+. The server rejects a breach as `minor_in_romance_arc`. PG staging (no kissing, embracing or face contact) applies to everyone. Asked for a minor in a romance: say no and offer the character at 18+ or the relationship as non-romantic.

## Off-screen voices

Declare them in the brief's cast table as voice only (an intercom, a phone, a grandfather in the back room). They are never drawn: not a sleeve, a hand, a cane or a shadow. An off-screen voice over another character's face is heard as that face speaking; give it a source in frame or a source treatment in post. The plate stage still draws a plate for a voice-only cast member today: say so before the plate spend.

## Shot plan per take

Pick the plan from the scene, not one default for every show.

| Plan | `--cut-tempo` | Use for |
| --- | --- | --- |
| One continuous shot | `one_shot` | Inner monologue, a making or process take, a walk |
| Coverage, 3–4 shots, one per board row | `punchy` | Romance, horror, comedy, any two-hander where a reaction lands |

Coverage: a wide, a face close-up, and an insert or point of view; no two neighbouring rows at the same size and angle; the reply on the listener's angle. A row with a spoken line keeps only `locked`, `dolly_in`, `dolly_out`, `pan_*`, `tilt_*` or `handheld` (lips stay readable); arcs, orbits, trucks and cranes go on silent rows. One spoken line per beat, nothing else happening during it. End on a wide for the hand-off.

## Expression library

The writers pick an anime expression (`reaction_kind`) for every emotional moment; the creator does not pick it. You check it at the board gate.

| Kind | Marks that must be drawn |
| --- | --- |
| `laugh`, `big_laugh` | open laugh; big: huge open mouth, tears at the eye corners |
| `happy`, `sparkle_delight` | closed crescent eyes (^^), sparkles, blush |
| `slow_surprise`, `dramatic_gasp`, `double_take` | eyes widen late; gasp; look, look away, snap back wide-eyed |
| `stunned_blank`, `shock`, `freeze` | blank white eyes or shrunken pupils, jaw drop, white-out, body locked |
| `sweat_drop`, `embarrassed`, `blush_look_away` | sweat drop, blush stripes, steam, eyes away |
| `anger_flare`, `comic_anger`, `eye_twitch`, `pause_then_outburst` | cross vein, sharp teeth, steam, red aura; the twitch; a held beat then the burst |
| `smiling_rage`, `smile_goes_cold` | eyes OPEN, tiny trembling pupils, eyelid twitch, shadow over the upper face, vein, too-wide clenched grin. The smile stays: never blank white eyes, never a scowl |
| `gloom` | vertical tatesen lines, colour drained, a dark cloud, on a VISIBLE face |
| `deadpan` | dot eyes, flat-line mouth |
| `deflated` | chibi squash, soul drifting out |
| `comic_tears`, `tear_up` | waterfall tears; welling eyes, wobbling mouth |
| `flinch` | a small recoil |

- **Pick by the moment, not the genre.** Any kind may play in any genre. The symbolic kinds (`happy` through `deflated`) fit a light, awkward, petty or comic moment, even in a serious show (a sweat drop at an awkward pause in horror). Real dread, grief, danger or tenderness gets the plainer kinds.
- **Whose face.** On a cell with two or more people, the writers name the face that wears the kind. A listener's expression on a speaking cell plays whole and silent on the listener. Wrong or missing face, or a wrong mark: fix the frame (`edit --frame … --set …`), then `redraw-board`.
- On a speaking cell the expression plays before the first word and after the last; during the words the mouth moves.

## Social safe zones

| Zone | Where | Covered by |
| --- | --- | --- |
| Top strip | top 8% of the height | tabs, search, camera |
| Bottom band | bottom 20% of the height | post caption, username, music |
| Right rail | right 12% of the width, lower two thirds | like, comment, share |

- Faces, eyes, mouths and key props never sit in a zone. Bodies, hands, floor and set may run through. Off-centre and two-shots are fine; do not centre faces by default.
- There is no face detector: look at every cell of the board. The boards `step` warns (`!!`) when a frame's written placement puts a face or prop in a zone; it reads text only. A face or key prop in a zone: fix the frame's placement (`edit --frame … --set …`), then `redraw-board`.
- Captions: the block stays in 55–70% of the height. `finish` and `caption` put the text bottom at 62% and never wrap (a too-wide caption is set smaller).
- The Sokii mark: top left, just under the top strip, `23:121` on 768×1344 (x = 3% of width, y = 9% of height), 0.6 opacity. Never top right.

## Board checks, in full

- The rows follow the declared shot plan.
- **Visible cause.** Every action row is preceded by a visible face reacting to its cause, or shares the frame with it.
- **Opening emotion.** Each row opens on the emotion the row before ended on. A calm smile right after the sign breaks reads wrong.
- **The picture shows the rule.** A change the viewer must see as a jump (the statue is closer) goes in its own row: H3 blends the cells of one row into one move.
- **Speakers.** A spoken line sits on a row where its speaker is in frame, at medium or closer. No off-screen speaker is drawn.
- **Speaking mouths.** No clench, grit, pressed or closed mouth on a speaking row. Big acting before and after the line.
- The hook (frame 0 mid-motion on a face), the hand-off frame (a frame with picture, never a fade or black), no readable text or digits, safe zones.
- Brightness is information only, never a block: report it; a dark board is the human's call. (`--accept-dim` is accepted and ignored.)

## Spend

| Unit | Cost |
| --- | --- |
| Take, 15 s, H3 Max R2V (`minimax/h3-max/reference-to-video`, 768p) | $1.20 ($0.08/s; a 12 s take is $0.96), plus $0.02048 per reference image past four (board + every cast plate, at most nine) |
| Cast plate, object plate, board | $0.30 each (a character needs two plates: full + bust) |
| Voice audition set | $0.30, once per character; a second set needs a cause |
| Voice line | $0.10 per 1,000 characters |

H3 Max Turbo I2V applies only when engineering pins it for a take. Never quote it by default.

Budgets are warnings, never a hard stop: first episode of a new series **$5.50**, continuing 15 s **$2.50**, continuing 30 s **$5.00**. A first 15 s episode filmed once is about $2.80, so the budget covers one redraw and one re-film. Say "$X of $Y" when the episode crosses its budget, not at the end. Past 2×, say so and let the human decide. `step` books plates, boards and takes itself (a take from its take facts: lane, seconds, reference images); `redraw-board` books its board. Book anything else by hand with `fictora-ops spend`. `fictora-ops preflight` never blocks on budget: a gate open exits 3; warnings exit 4 until the human says film anyway and you run `--proceed-anyway ep01-t1` (logged, next film only).

"Change this" is billed. A retry reuses the same idempotency key; two jobs for one take is a bug: report it.

## Prompt policy

- The compiled video prompt and the compiled image prompt behind every plate and board are proprietary and stay on the server. Never fetch, save, print or quote `GET /v1/jobs/{id}/provider-spec` (operator tokens get `403 provider_spec_internal_only`).
- The shot description (beats, `motion_intent`, the visual brief on the spine) is yours to read.
- Write the draft, cast, board and video job ids into `run-notes.md`. A take whose prompt must be looked at goes to engineering as its job id. Learnings name job ids, never prompt text.

## Reading a take

Measure, then watch: every approved line heard, exactly; cut count (compare consecutive frames); loudness (dialogue about −15 to −20 LUFS); brightness vs the board; every beat present in order. Write every fault, including the ones you will not fix. Then ask **Use it** or **Change this**.

- A missing spoken line is the one real re-film. Name the cause first.
- A line the take was never asked to say is a server fault: `fictora-produce check-lines --desk D --episode N [--take tK]` names it from the take facts (never the prompt). Report it with the take job id; do not re-film blind.
- Stray mumble between lines is a mute in the mix, not a re-film.

## Finish

A raw take is never a deliverable. With `api_captions: false`:

```bash
uv run fictora-produce finish --desk D [--episode N] [--take tK] [--take-file F] [--duck-db N] \
  [--sfx-adjust "door=-6" | "hum=drop" | "shot:3=+4"] [--bed-db -16.5] [--music "..."] \
  [--line-start S ...] [--no-colour-match] [--colour-strength 0..1] [--watermark-y Y] [--json]
```

| Step | What it does | Writes |
| --- | --- | --- |
| sfx | Cue plan from `GET /v1/jobs/{take_job}/take-facts?spine_id=` (saved as `api/take-facts-epNN-tK-vN.json`; the take job is read from `api/17_raw_scene_clips.json`). Each cue renders on the server from its Sound label (~$0.002/s), is shape-checked, cached in `epNN/sfx/`, laid at −8 dB and ducked 10 dB under speaking shots | `take-epNN-tK-sfx-vN.mp4` |
| bed | The desk's pinned bed; else the spine's `series_audio_bed_url`; else one made once on the server (~$0.06) from the genre (or `--music "…"`), levelled here to −20 LUFS, pinned in `shared/beds/` | — |
| colour | One Lab curve for the whole take, fitted to the approved board (gutters left out) | `…-colour-vN.mp4` + `.cube` |
| mix | Bed looped under the take at `--bed-db`, sidechain-ducked under the voice (`--duck-db N` = exactly N dB, 1–30), take gain measured to land near −18 LUFS (band −20 to −15), one limiter | `…-mix-vN.mp4` |
| captions | House captions timed on the take before the bed | `…-cap-vN.mp4` + `.ass` |
| watermark | Sokii mark top left (x 3%, y 9%), never in the top 8% | `…-sokii-vN.mp4` |

A step that fails is reported and skipped; the chain carries on from the last good file. The summary ends with `Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`. When music, SFX or the mix did not go on it prints `NOT DONE`, writes a run note and exits **5**: the file is not a deliverable. A second `finish` writes the next versions and reuses the cached cues and the pinned bed ($0).

`caption --desk D [--line-start S ...]` still burns captions alone on a raw take (timing detail: [local-captions.md](../../../docs/content-ops/local-captions.md)). Needs ffmpeg + ffprobe with libass. Generated audio (effects, the bed, audition clips, dry lines, Whisper timings) is made on the server by the Drama API operator audio routes (`sfx-cues`, `audio-bed/render`, `voice-auditions/render`, `voice-lines`, `/v1/transcripts`); no provider key ever goes on this laptop and no local file is uploaded (transcripts read the take's stored URL). Rate limits and in-progress answers are waited out automatically. If a route refuses (`operator_audio_unavailable`, `budget_cap_exceeded`), `finish` still does the look, mix, captions and mark, reports `NOT DONE` and names the refusal: tell engineering, never add a key. Costs go to `run-notes.md` only; never quote them to anyone else.

**Hosted post is off.** Never call `POST /v1/video-generations/{id}/post-production-runs` and never pass `--api-captions`. `409 hosted_post_off`, or the older `503 restate_unavailable` from post-production, means finish locally. It is not an outage; do not retry it.

## Change a character's voice

The voice is a lock on the cast card, not part of the story or the picture. Never re-draft the story or re-film every take to change it.

| Command | What it does | Spends |
| --- | --- | --- |
| `voice --desk D --cast NAME --audition [--episode N] [--count 8] [--cause "…"]` | `POST /v1/spines/{id}/cast/{cast_id}/voice-auditions/render` on up to 3 of the character's real lines renders every candidate on the server; each clip is saved to `shared/voices/<cast>/audition-vN/NN-<voice>.mp3` with `auditions.json`. A second set needs `--cause`. Every render carries an idempotency key, so a re-run never pays twice | $0.30 a set |
| `voice --desk D --cast NAME --pick N` | `POST …/voice-auditions/pick` locks candidate N on the cast card and saves the spine again | nothing |
| `revoice --desk D --cast NAME --episode N --take tK [--take-file F] [--words-json W] [--voice-db 0]` | Has the server render each of that character's lines dry in the locked voice and time the take's words (Whisper), then on this laptop mutes the original there (0.08 s before to 0.15 s after), lays the new line in at the same start. Picture copied, other characters as filmed. Writes `take-epNN-tK-revoice-vN.mp4` + `.json` | $0.10 per 1,000 characters + Whisper pennies |

Then `finish --desk D --episode N --take tK --take-file <revoice file>`. Takes not filmed yet use the new voice as they are. Re-film only a take where the dub does not sit (lips visibly wrong, a shouted line), with a cause and a stated cost; never the other takes.

## Episode 2 on

Episode 1 is made on its own; the arc is chosen at episode 2, after episode 1 is approved. Ask the human how many episodes the run should be, then:

- Offer arcs sized to that run. For a long run prefer an engine (a situation that repeats with a new problem) plus a slow question; refuse an arc that closes within a few episodes.
- Each later episode is steered by its **direction**: the human picks one or says their own, and it goes to the writer in their words. Confirm the idea is in the printed script. Series-wide notes are for standing rules ("keep every episode punchy"), never for this episode's idea.
- Approve each episode's script on its own. Never pre-stage scripts for later episodes.

```bash
uv run fictora-produce arc --desk D --list [--episodes N]          # the three arcs, sized to the run (episode 2 only)
uv run fictora-produce arc --desk D --pick K [--title T] [--line L] [--episodes N]   # keeps it; prints episode 2's directions
uv run fictora-produce brief --desk D --episode N [--episodes N]   # directions for episode 3 on
uv run fictora-produce author --desk D --episode N (--direction K | --line "the human's words" [--title T])
uv run fictora-produce approve --desk D --gate script              # then step (boards), approve board, step (estimate), step --confirm-spend
uv run fictora-produce memory --desk D (--note TEXT | --thread TEXT)   # standing rules only
```

`author` opens the desk slot, prints the script, and points the phase machine at the new episode. The season target is a soft default: an episode past it is written as "the season continues"; 240 is the only stop. The boards `step` sends `episode_count: N` and the server draws only the episodes up to N that still lack boards, so episode N is drawn and booked alone. The take films with `episode_count: N, episode_ordinal: N`: episode N alone (episodes 1..N must be approved; none of them is filmed or booked again), and its first take opens on the previous episode's stored last frame when the location matches (nothing to paste by hand).

## Film one episode, or one take of it

```bash
uv run fictora-produce film --desk D --episode N --take tK --cause "what in the direction made the fault"   # prices take K alone; spends nothing
uv run fictora-produce film --desk D --episode N --take tK --cause "…" --confirm-spend                     # after the human's yes: films only take K
uv run fictora-produce film --desk D --episode N [--cause "…"] [--confirm-spend]                          # the whole episode N, alone
```

- Without `--confirm-spend` it prices exactly what will be filmed (`batches/estimate` with `reroll_take_index` for one take) against the envelope and stops. `--confirm-spend` refuses until that number was shown.
- It sends `POST /v1/video-generations` with `episode_count: N, episode_ordinal: N`, plus `reroll_take_index: K, seed_attempt: <films so far + 1>` for one take. The new take lands as the next `take-epNN-tK-raw-vN.mp4` with its take facts; only it is booked. The other takes stay as filmed.
- A take (or episode) already filmed needs `--cause` naming what in the direction produced the fault; "try again" is refused. The cause is recorded on the take as Change this and in `run-notes.md`.
- Only from a board with a human yes on the desk. An interrupted `film` run again picks up its job and never pays twice.
- An older deploy without `episode_ordinal` is refused before anything is sent (episode 1 films alone either way). Say so to the human and tell engineering.


## Recovery

| Situation | Action |
| --- | --- |
| Video stuck `running` ~50%, post still going | `fictora-produce cancel-job --desk D --job-id job_video_…` only. Never `retry-video`, never `--confirm-spend` again (a second enrol starts another ffmpeg on Railway) |
| Poll CLI dies mid-job (`ReadError`) | Job may still run: re-run `step` on the same desk |
| Draft `authoring_stalled`, retryable | Harness retries; else re-run `step` from `new` |
| Draft or `author` failed `authoring_validation_failed` | The message names the rule and where. Fix the brief or the direction; do not re-run the same thing |
| Draft 500 cast `too_short` | Premise asked for a solo cast; pull latest `main`, re-`step` |
| Estimate 400 `invalid_episode_selection` | The step saves the skip and prices from the table |
| Empty `plates/` or `boards/` after `step` | Downloads fall back to the enrol terminal JSON (`ep01/api/06_*cast_terminal*.json`, `09_*boards_terminal*.json`); pull `main`, re-run `step` |
| `spine_not_found` / wrong session | New desk + new `start`; never reuse an old spine id |
| Change this on one take (human yes + cause) | `film --desk D --episode N --take tK --cause "…"`, then the same with `--confirm-spend`: films and books only take K |
| A second paid take of the whole current episode (human yes + cause, once per desk) | `retry-video --desk D --new-paid-take`, then `step --confirm-spend` (enrols a new job with a fresh seed; never resumes the old one) |
| "The deployed Drama API does not film one episode alone yet" | Nothing was sent or charged. Stop; tell engineering (the deploy needs fictora-drama #436) |
| `author`, `redraw-board` or `film` interrupted | Run the same command again: it reuses the recorded key or polls the recorded job, never pays twice |

Trust `GET /v1/video-generations/{id}` or `GET /v1/jobs/{id}` over log lines.

## API contract

| Item | Value |
| --- | --- |
| Base URL | `FICTORA_DRAMA_GENERATION_API_BASE_URL` (see `.env.example`) |
| Auth | `Authorization: Bearer`, `X-Drama-Session-Id` on every `/v1` call |
| Mutations | `Idempotency-Key` required |
| OpenAPI | `{base}/openapi.json`, `{base}/docs` |

Sequence `fictora-produce` runs: `POST /v1/prompt-video-authoring-drafts` (`episode_count: 1`, `outline_mode: arc_at_episode_two`, `cut_tempo`) → cast enrol → cast approve → spine approve → boards enrol + exposure → boards approve → `batches/estimate` (this episode) → `POST /v1/video-generations` → raw takes from the take jobs + `GET /v1/jobs/{take}/take-facts?spine_id=`. Episode 2 on: `director/brief` → `series-arc` → `pilot-episodes/{n}/author` → `pilot-episodes/{n}/approve` → boards (`episode_count: N`) → estimate → take (`episode_count: N, episode_ordinal: N`). One take: `batches/estimate` with `reroll_take_index: K`, then the take with `reroll_take_index: K, seed_attempt`. `/delivery` is never needed (hosted post is off). Presets: `GET /v1/art-style-presets`.

## Commands for edits and checks

```bash
uv run fictora-produce edit --desk D --episode N (--beat ID|N | --frame ID|N | --line-id ID) [--intent TEXT] [--set KEY=VALUE ...] [--text T] [--spoken S] [--subtitle S] [--select-regen] [--preview]
uv run fictora-produce look-frame --desk D --description FILE [--size 1088x1936]   # draw our own style frame on the server from the written look; $0.30; never pins
uv run fictora-produce look --desk D --url https://…          # pin one style frame (the look-frame's image_url, after the human's yes); spends nothing
uv run fictora-produce look-note --desk D (--add TEXT | --remove ID|N)   # at most five; the next drawing uses them
uv run fictora-produce spine --desk D --refresh
uv run fictora-produce redraw-board --desk D --episode N --take tK --cause "why"   # $0.30; back to the board gate
uv run fictora-produce check-lines --desk D --episode N [--take tK]                # exit 5 when an approved line was not asked for
```

- `edit` before the script gate is a plain patch. After it, the server asks for a cascade: `edit` prints every item, runs the free ones, and leaves the paid ones (`tier media`) off unless `--select-regen`; it names each board that no longer matches (redraw it with `redraw-board`). `--preview` stops after the list.
- `edit --line-id … --spoken "…" [--subtitle "…"]` pins the exact performed line on a Japanese or Korean show before the script gate; an English show refuses it. A new English `--text` on such a show is re-localized when the script is approved.
- `look-frame` sends only the words in FILE (1–4000 characters) to `POST /v1/spines/{id}/look-frame`; the server draws on the product's still model (text only: it refuses a link in the description) and answers our stored PNG. The desk saves `shared/look/look-frame-vN.png`, books the still, and prints the `image_url` for `look --url`. It never pins. The same description and size are cached on the server (re-running is free). An older server answers 404 on the route: the command stops with that message and books nothing; tell engineering.
- `redraw-board` warns when the frame briefs have not changed since the last drawing (a re-roll). A beat edit made after the board was drawn is carried into the redraw by the server. The cause is a label on the desk only.

## Desk artefacts

| Path | Content |
| --- | --- |
| `production.json`, `production.config.json` | Phase machine, tunables |
| `ep01/api/*.json`, `ep01/api/run.log` | Numbered API snapshots, JSONL poll log (support only) |
| `ep01/plates/`, `epNN/boards/`, `epNN/takes/` | Review surface |
| `api/spine.json`, `epNN/api/spine.json` | The story as last read; `api/brief-epNN-vK.json` briefs and arcs |
| `epNN/api/take-facts-epNN-tK-vN.json` | Take facts: lane, seconds, reference images, shots, which approved line ids were asked for. Never the prompt |
| `ep01/run-notes.md` | Ledger, job ids, faults, causes |

## Poll deadlines

| Field | Default (s) |
| --- | --- |
| `poll_plan_deadline_seconds` | 1800 |
| `poll_cast_deadline_seconds` | 3600 |
| `poll_boards_deadline_seconds` | 7200 |
| `poll_video_deadline_seconds` | 7200 |

Draft jobs can exceed 15 minutes under load; raise the deadline before calling it failed.

## Debugging (a declared deviation)

Only when `fictora-produce` cannot surface a failure: read-only `GET` of the spine or a job via `creation.harness.session.DramaApiRunSession` with the desk's `session_id`. No mutations, no prompt fetches. Engineering smoke (not production): `uv run python scripts/smoke_live.py --phase read`.
