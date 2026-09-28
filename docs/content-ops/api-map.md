# API map — aligned vs gap

The agent uses this table before every paid step. Anything marked **Aligned** must go through the Drama Generation API. Gaps are the only legitimate territory for a scratch tool.

**Run every route through a command, never by hand.** The routes below are reference: what `fictora-produce` and `fictora-ops` send, so you can read a refusal and name a gap. Never write your own HTTP around the API (no `DramaApiRunSession`, `httpx` or `curl` script, no importing `creation.harness` into a scratch file). The commands keep the session, record each `Idempotency-Key` before the call and reuse it on a retry, so an interrupted command run again never pays twice. A route with no command is a DEVIATION (the skill's reference.md) and a line in [backlog.md](backlog.md). The one exception is a declared, read-only debugging `GET` (reference.md, "Debugging").

Do not call `scripts/drama_create_flow_smoke.py` on a content production. That walk enrols and approves without a human gate.

| Route family | Command |
| --- | --- |
| Draft, cast, boards, estimate, take, poll | `fictora-produce step` (one block per call; `--confirm-spend` for the take) |
| Approvals | `fictora-produce approve --desk D --gate plates\|script\|board` |
| Arc, brief, episode 2 on, memory | `arc`, `brief`, `author`, `memory` |
| Line and spine edits | `line`, `edit`, `spine --refresh` |
| Look | `look-frame`, `look`, `look-note` |
| One board or one plate again | `redraw-board`, `redraw-plate` |
| One take or one episode again | `film` |
| Voices and paid audio | `voice`, `revoice`, `voice-line`, `cue`, `finish` (effects, bed, transcripts) |
| Cancel a stuck job | `cancel-job` |
| Line check (take facts) | `check-lines`, `review` |

Credentials: `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN` and optional `FICTORA_DRAMA_GENERATION_API_BASE_URL`. Never print the token.

## Stage status

| Stage | Status | Product path |
| --- | --- | --- |
| Series + script | Aligned | `POST /v1/prompt-video-authoring-drafts` → poll `GET /v1/jobs/{id}` → `GET /v1/spines/{id}` |
| Steer a draft episode | Aligned | `POST /v1/spines/{id}/episodes/{n}/steers` |
| Edit lines directly | Aligned | `PATCH /v1/spines/{id}` (`fictora-produce line`: words, performed line, speaker, off-screen) |
| Add or drop a line, add an off-screen voice | Aligned | `PATCH /v1/spines/{id}` `add_dialogue_lines[]`, `remove_dialogue_line_ids[]`, `add_voice_only_cast[]` (the cascade after the script gate); refusals are named 400s. Kit: `fictora-produce line --add --beat N --speaker NAME --text "..."`, `--remove ID`, `--new-voice NAME --role ... --voice-description ...` |
| Approve script | Aligned | `POST /v1/spines/{id}/approve` — only after the human says yes to the lines |
| Our own style frame | Aligned | `POST /v1/spines/{id}/look-frame` `{description, size?}` → `{image_url, width, height, cached, cost_usd}`: drawn on the server from the written description alone (text only; a link is `422 look_frame_text_only`), $0.30, cached per (story, description, size). Never pins. Kit: `fictora-produce look-frame --desk D --description TEXT|@FILE`. An older server answers 404 on the route. |
| Look / style | Aligned | `POST /v1/spines/{id}/look-register` pins `look_register_url`. First-draw stills (cast, boards, look plates) put that crop as Image 1. Cast-edit / look-plate-edit keep identity as Image 1. |
| Cast plates | Aligned | `POST /v1/spines/{id}/cast/enrol` → poll → download plates → **stop** → `POST /v1/spines/{id}/cast/approve` |
| Location + prop plates | Aligned | `POST /v1/spines/{id}/look-plates/draw` with `plate=object` (`prop_id`) or `plate=location` (`location_id`). Location uses the set-sheet template (no people). Props use the object-plate template. |
| Boards | Aligned | `POST /v1/spines/{id}/boards/enrol` → poll → download → **stop** → `POST /v1/spines/{id}/episodes/{n}/boards/approve`. Authoring accepts a 2×N row board when every frame sets `board_row` (2/4/6/8 cells). Default remains 3×3 when `board_row` is omitted. |
| Board exposure check | Aligned | `GET /v1/spines/{id}/episodes/{n}/boards/exposure` measures Rec. 709 mean luma. `POST .../boards/approve` returns `422 boards_dim` when a board is ≤25% mean luma unless `accept_dim=true`. Interior target band 28–35%. `fictora-ops measure-board` is a local helper, not the product path. |
| Cost check | Aligned — mandatory | `POST /v1/spines/{id}/batches/estimate` before enrol |
| Take | Aligned | `POST /v1/video-generations` with `authoring_mode=reuse`, `clip_duration_seconds` 4–15 (default 15), `aspect_ratio=9:16` |
| Poll / cancel | Aligned | `GET /v1/video-generations/{id}`; `POST /v1/jobs/{id}/cancel` |
| Per-take verdict | Aligned | Operator says Use it or Change this |
| Delivery | Not used | `GET /v1/video-generations/{id}/delivery`. Hosted post is off, so there is nothing to deliver: the take stops at the raw clip and `fictora-produce finish` makes the deliverable on the laptop. |
| One unbroken shot | Aligned | `cut_tempo=one_shot`. One camera move. Every cell is one shot. The camera is a little closer in each cell. |
| A beat's own shots | Aligned (needs fictora-drama #464) | `beats[].shot_plan`: 1–4 shots `{size, subject, camera?, angle?}` in words; `PATCH /v1/spines/{id}` before the script gate, the cascade after it (marks the take's frames; its next board redraw follows the plan); `null` clears it. Shot 1 is the beat's first board row; shots past the beat's rows are clamped and logged, never refused. Kit: `edit --beat N --shot "size\|subject\|camera\|angle"` (repeatable), `--shot-plan JSON\|@FILE`, `--clear-shot-plan`. An older server answers `422`. |
| Take length other than 15s | Aligned | `clip_duration_seconds` accepts 4–15. Default remains 15. 16 is still a rejection. |
| Inner-voice / narration track | Aligned | `PUT /v1/spines/{id}/episodes/{n}/inner-voice` replaces dry cues. Mixed in post. Take compile refuses inner-voice / voice-over / narration / V.O. as spoken fixture lines. |
| Voice auditions and picks | Aligned | `POST /v1/spines/{id}/cast/{cast_id}/voice-auditions` compiles 4–10 Eleven v3 candidates on the character's real spoken lines, or on `text` (new wording, ≤300 characters) in `voices` (1–10 named catalog voices instead of the default slate); `.../render` takes the same body (`voice --audition --text --voices` passes both through; named `422 voice_audition_*` on a broken rule). `POST .../voice-auditions/pick` locks `DramaCastVoiceBrief`. |
| Music + SFX | Aligned | `POST /v1/spines/{id}/audio-bed` pins `series_audio_bed_url`. Post uses that file instead of generating a new show bed. Product library beds still mix unless `FICTORA_DRAMA_AUDIO_BED=off`. |
| Ducking under voice | Aligned | Product ffmpeg mix uses `sidechaincompress` against the processed take stem, not a static −12 dB envelope. |
| Captions | Aligned | `caption_style=house`: yellow `#FFE500`, Poppins Bold, black edge, soft shadow, no box, text bottom at 62% of frame height (block in 55–70%). English flickers; a translation holds the whole line. |
| Hand-off frame between takes | Aligned | Every successor pastes the prior last frame into board cell 1a. A missing clip or paste fault raises and stops the take. |
| Making-take forward lock | Aligned | An opening wordless making take's last cell must match the next take's opening (`making_take_needs_forward_lock`). Later silent sets are not making takes. Seam-reuse skips a making predecessor. `apply_forward_lock` pastes the next opening into the last cell. |
| Intra-take H3 cell seams | Aligned | Post measures cuts with `scale=16:28` frame-diff (YAVG ≥ 25), then hold-and-fade 1/3 s. Scene detect is not the gate. |
| Bed covers the cut | Aligned | Mix loops the bed (`-stream_loop -1`) and `assert_audio_covers_duration` refuses a short mix. Joined-cut floor is 95% of probed take sum. |
| Speech vs authored lines | Aligned | `speech_verify` diffs heard windows against bound lines and mutes unscripted spans. Caption starts snap to `silencedetect` onsets, or to an explicit override list. |
| Line-only speaking cell | Aligned | Authoring lint `speaking_cell_overloaded` when a speaking cell also carries several physical actions. |
| Canary take length | Aligned | Fixture / episode `duration_seconds` (4–15) is passed to `compile_take_for_family`. Do not hardcode 15 in `canary_compile`. |

## Visual-first episode 1 order

After draft completes, the product order is:

1. Cast enrol → poll → **human plate gate** → cast approve
2. Script approve (`POST /v1/spines/{id}/approve`) after the line gate
3. Boards enrol → poll → `GET .../episodes/{n}/boards/exposure` → **human board gate** → boards approve (`accept_dim=true` only when the human accepts a dim board)
4. Estimate
5. Video enrol → poll → raw takes + take facts (then `finish` locally)

`fictora-produce step` runs these calls with the gates split and stops at each one. Do not approve in the same turn you enrol.

`clip_duration_seconds` defaults to 15. Send 4–15 for a shorter or full take. 16 is a rejection, not a longer film. Use `cut_tempo=one_shot` for an unbroken move. Use `caption_style=house` for the runbook caption recipe.

Lane pin: `model_overrides.video=minimax-h3`. The server picks the endpoint behind it: `minimax/h3-max-turbo/image-to-video` at 768P by default (the take's storyboard board is the only image sent; no cast plates, no voice references), or `minimax/h3-max/reference-to-video` when engineering switches the deploy to R2V. The pin never decides it. Read the live endpoint from the estimate (`cost_estimate.video_endpoint_id`, `usd_per_second`) or a take's facts (`endpoint_id`), or `GET /v1/video-lanes` (`endpoint_ids`), before trusting a string from memory.

## Download into the run folder

After each media job completes, write files into the review folders with versioned names:

```bash
uv run fictora-ops next-path --dir <run>/plates --stem "plate-coach-full" --suffix .png
```

JSON from the API goes in `<run>/api/`. Humans review `plates/`, `boards/`, and `takes/`, not the JSON.
