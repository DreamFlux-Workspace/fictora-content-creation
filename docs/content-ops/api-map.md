# API map — aligned vs gap

The agent uses this table before every paid step. Anything marked **Aligned** must go through the Drama Generation API. Gaps are the only legitimate territory for a scratch tool.

**Run every route through a command, never by hand.** The routes below are reference: what `fictora-produce` and `fictora-ops` send, so you can read a refusal and name a gap. Never write your own HTTP around the API (no `DramaApiRunSession`, `httpx` or `curl` script, no importing `creation.harness` into a scratch file). The commands keep the session, record each `Idempotency-Key` before the call and reuse it on a retry, so an interrupted command run again never pays twice. A route with no command is a DEVIATION (the skill's reference.md) and a line in [backlog.md](backlog.md). The one exception is a declared, read-only debugging `GET` (reference.md, "Debugging").

Do not call `scripts/drama_create_flow_smoke.py` on a content production. That walk enrols and approves without a human gate.

| Route family | Command |
| --- | --- |
| Draft, cast, boards, estimate, take, poll | `fictora-produce step` (one block per call; `--confirm-spend` for the take) |
| Approvals | `fictora-produce approve --desk D --gate look\|plates\|script\|board` |
| Arc, brief, episode 2 on, memory | `arc`, `brief`, `author`, `memory` |
| A show's spoken language, its stored brief | `language`, `brief --edit` / `--strip-narration` |
| Line and spine edits | `line`, `edit`, `spine --refresh` |
| A character's thoughts | `inner-voice` |
| Expression library, a beat's expression | `expressions`, `edit --beat N --expression KIND\|none` |
| Look | `look-frame`, `look`, `look-note` |
| Sound notes, a take's facts again | `sound-note`, `take-facts --refresh` |
| One board or one plate again | `redraw-board`, `redraw-plate` |
| One take or one episode again | `film` |
| Voices and paid audio | `voice`, `revoice`, `voice-line`, `cue`, `finish` (effects, bed, transcripts) |
| Cancel a stuck job | `cancel-job` |
| Line check (take facts) | `check-lines`, `review`, `take-facts` |

Credentials: `FICTORA_DRAMA_GENERATION_SERVICE_TOKEN` and optional `FICTORA_DRAMA_GENERATION_API_BASE_URL`. Never print the token.

## Stage status

| Stage | Status | Product path |
| --- | --- | --- |
| Series + script | Aligned | `POST /v1/prompt-video-authoring-drafts` → poll `GET /v1/jobs/{id}` → `GET /v1/spines/{id}` |
| Steer a draft episode | Aligned | `POST /v1/spines/{id}/episodes/{n}/steers` |
| Edit lines directly | Aligned | `PATCH /v1/spines/{id}` (`fictora-produce line`: words, performed line, speaker, off-screen) |
| Add or drop a line, add an off-screen voice | Aligned | `PATCH /v1/spines/{id}` `add_dialogue_lines[]`, `remove_dialogue_line_ids[]`, `add_voice_only_cast[]` (the cascade after the script gate); refusals are named 400s (`beat_already_has_line`, `line_speaker_not_motion_subject`, `voice_only_cast_needs_a_line`, `voice_only_cast_on_screen`, `cast_id_taken`, `cast_limit_reached`, `line_id_taken`, `invalid_patch`, `cascade_edit_out_of_scope`). A voice-only character has no plate and no visual brief until a frame shows them (fictora-drama #469, superseding #453 and #466). Kit: `fictora-produce line --add --beat N --speaker NAME --text "..."`, `--remove ID`, `--new-voice NAME --role ... --voice-description ...` (someone heard and never seen, never a character's thoughts: those are `inner-voice`). A removal or speaker change that would leave a voice-only character with no lines stops before sending unless `--strand-voice`. Since fictora-drama #517 the server films without such a character and draws no plate (the kit's plate count matches; `film` / `step` print an `info: stranded_voice` line); a deploy older than #517 refuses the film (`422 spine_reuse_invalid … visual_brief is required`, nothing charged) |
| Approve script | Aligned | `POST /v1/spines/{id}/approve` — only after the human says yes to the lines |
| Our own style frame | Aligned | `POST /v1/spines/{id}/look-frame` `{description, size?}` → `{image_url, width, height, cached, cost_usd}`: drawn on the server from the written description alone (text only; a link is `422 look_frame_text_only`), $0.30, cached per (story, description, size). Never pins. Kit: `fictora-produce look-frame --desk D --description TEXT|@FILE`. An older server answers 404 on the route. |
| Look / style | Aligned | `POST /v1/spines/{id}/look-register` pins `look_register_url`. First-draw stills (cast, boards, look plates) put that crop as Image 1. Cast-edit / look-plate-edit keep identity as Image 1. A pinned look wins over the preset: the preset keeps only its shot composition; its world, palette, finish and reference images are dropped (fictora-drama #461 via #471). The `horror` (Dead Light) preset is a draft and cannot be picked yet (#455 via #471). |
| Cast plates | Aligned | `POST /v1/spines/{id}/cast/enrol` → poll → download plates → **stop** → `POST /v1/spines/{id}/cast/approve`. A later step refused `422 cast_not_approved` for unchanged approved plates: `approve --gate plates --again` sends `cast/approve` again on the current `spine_version` (fresh key, $0), then `retry-step` |
| Location + prop plates | Aligned | `POST /v1/spines/{id}/look-plates/draw` with `plate=object` (`prop_id`) or `plate=location` (`location_id`). Location uses the set-sheet template (no people). Props use the object-plate template. |
| Boards | Aligned | `POST /v1/spines/{id}/boards/enrol` → poll → download → **stop** → `POST /v1/spines/{id}/episodes/{n}/boards/approve`. Authoring accepts a 2×N row board when every frame sets `board_row` (2/4/6/8 cells). Default remains 3×3 when `board_row` is omitted. |
| Board exposure check | Aligned | `GET /v1/spines/{id}/episodes/{n}/boards/exposure` measures Rec. 709 mean luma. `POST .../boards/approve` returns `422 boards_dim` when a board is ≤25% mean luma unless `accept_dim=true`. Interior target band 28–35%. `fictora-ops measure-board` is a local helper, not the product path. |
| Cost check | Aligned — mandatory | `POST /v1/spines/{id}/batches/estimate` before enrol |
| Take | Aligned | `POST /v1/video-generations` with `authoring_mode=reuse`, `clip_duration_seconds` 4–15 (default 15), `aspect_ratio=9:16` |
| Poll / cancel | Aligned | `GET /v1/video-generations/{id}`; `POST /v1/jobs/{id}/cancel` |
| Per-take verdict | Aligned | Operator says Use it or Change this |
| Delivery | Not used | `GET /v1/video-generations/{id}/delivery`. Hosted post is off, so there is nothing to deliver: the take stops at the raw clip and `fictora-produce finish` makes the deliverable on the laptop. |
| One unbroken shot | Aligned | `cut_tempo=one_shot`. One camera move. Every cell is one shot. The camera is a little closer in each cell. |
| A beat's own shots | Aligned (fictora-drama #464, merged via #471) | `beats[].shot_plan`: 1–4 shots `{size, subject, camera?, angle?}` in words; `PATCH /v1/spines/{id}` before the script gate, the cascade after it (marks the take's frames; its next board redraw follows the plan); `null` clears it. Shot 1 is the beat's first board row; shots past the beat's rows are clamped and logged, never refused. Kit: `edit --beat N --shot "size\|subject\|camera\|angle"` (repeatable), `--shot-plan JSON\|@FILE`, `--clear-shot-plan`. An older server answers `422`. |
| Expression library | Aligned once fictora-drama #482 is live | `GET /v1/capabilities` → `{schema_version: "fictora.drama-capabilities.v1", reaction_kinds: [{kind, label, comedy}]}` in library order. Kit: `fictora-produce expressions --desk D [--episode N]` (spends nothing). |
| A beat's expression | Aligned once fictora-drama #482 is live | `beats[].reaction_kind` on `PATCH /v1/spines/{id}` (one kind from `/v1/capabilities`; `null` clears; an invalid kind is `400`); the cascade after the script gate (marks the take's board). The server gives it to the beat's anchor frame (its row on a row board), so it reaches the board prompt ("Expression (…)") and the take prompt. Kit: `edit --beat N --expression KIND\|none`, checked against `/v1/capabilities` first; an older deploy (no `reaction_kind` on `DramaBeatPatch` in `/openapi.json`, or no `/v1/capabilities`) is refused before anything is sent. Any kind on any beat, whatever the genre: the scene decides (decided 2026-09-29). Open: the request does not name whose face on a two-person row. |
| Episode ids | Aligned | New episodes are `episode_NN`; a story made before 2026-09-28 keeps `ep_02` on for later episodes. The spine routes take either form; the kit looks episodes up by ordinal (fictora-drama #456). |
| Authoring warnings | Aligned once fictora-drama #538 deploys | Word-count nudges (`DramaAuthoringWarning`, `fictora.drama-authoring-warning.v1`: `line_long` >10 words, `take_words_over_target` >22 spoken words a take, `first_line_long` >10). Read from every spine response's `authoring_warnings` (never stored), a `plan_author` / `pilot_episode_extend` job's `result.authoring_warnings`, and cascade preview / execute `authoring_warnings` (only what the edit introduces; the messages are also in `warnings`). The kit prints them after the draft, `author`, `line` / `edit` (introduced ones only) and the cascade, and notes them; never a stop. Absent on an older server: nothing printed. The 4-word first-line and 28-word take errors (`first_line_invalid`, `storyboard_set_dialogue_budget_exceeded`) are gone; a silent opening is valid. |
| Take length other than 15s | Aligned | `clip_duration_seconds` accepts 4–15. Default remains 15. 16 is still a rejection. |
| Spoken language after creation | Aligned once fictora-drama #582 deploys (`language`) | `POST /v1/spines/{id}/spoken-language` `{spine_version, spoken_language, preview, confirm_filmed}` → `{previous_spoken_language, spoken_language, changed, applied, effect}`. Clears every performed line, subtitle and gloss (re-localized at script approval or before the next take), seed voice clips (`voice_references_cleared`) and speech-level examples; refused `409 spoken_language_change_busy` (an episode being written, a take filming) and `409 spoken_language_change_filmed` (takes filmed in another language) without `confirm_filmed`. Same language: `changed: false`. Before #582: 404, refused by the kit |
| Stored brief | Aligned once fictora-drama #582 deploys (`brief --edit` / `--strip-narration`) | `PUT /v1/spines/{id}/scene-prompt` `{spine_version, scene_prompt, preview}` → `{scene_prompt_normalized, scene_prompt_sha256, previous_scene_prompt_sha256, changed, applied, locked_line_count}`; refused `422 scene_prompt_empty` / `locked_lines_out_of_bounds` as the draft refuses. Takes film with the stored brief, so the next take carries it. Before #582: 404/405, refused by the kit |
| Cast floor | Aligned once fictora-drama #582 deploys | `GET /v1/capabilities` `cast_floor` (1: one person is a whole cast). Absent on an older server: the kit's draft directive asks for two |
| Inner-voice / narration track | Aligned | `PUT /v1/spines/{id}/episodes/{n}/inner-voice` `{spine_version, episode_ordinal, cues: [{cue_id, start_ms, end_ms, speaker_cast_id, line}]}` replaces the episode's dry cues (answer: the spine; cues on `episode_summaries[].inner_voice`). Never compiled into the take prompt. Take compile refuses inner-voice / voice-over / narration / V.O. as spoken fixture lines. Kit: `fictora-produce inner-voice --desk D --episode N --cast NAME --text "..." [--spoken-text "..."] --at S [--until S]` (`--text` is the cue's `line`, the caption; the contract has no spoken-text field, so `--spoken-text`, the words said, stays on the desk in `epNN/inner-voice-spoken.json`), `--remove N`, `--clear`, no flags to list (a character's thoughts on that character, no cast place). Hosted post is off: `finish` lays each cue on the take it starts in (take N starts at the sum of the earlier raw takes' lengths), its dry line made on `POST /v1/spines/{id}/cast/{cast_id}/voice-lines` (the `voice-line` route and key; reused on a re-run), captioned in Georgia italic. |
| Voice auditions and picks | Aligned | `POST /v1/spines/{id}/cast/{cast_id}/voice-auditions` compiles 4–10 Eleven v3 candidates on the character's real spoken lines, or on `text` (new wording, ≤300 characters) in `voices` (1–10 named catalog voices instead of the default slate); `.../render` takes the same body (`voice --audition --text --voices` passes both through; named `422 voice_audition_*` on a broken rule). `POST .../voice-auditions/pick` locks `DramaCastVoiceBrief`. |
| Show voices (voice mode) | Aligned | `GET /v1/spines/{id}/voice-mode` answers `{voice_mode: locked\|model, source: show\|inferred\|default, filmed_takes, model_voice_takes}`; `POST` the same path with `{spine_version, voice_mode}` stores it (`notice` when filmed voices change). `batches/estimate` and `POST /v1/video-generations` answer `voice_mode` too. Kit: `voice-mode --desk D [--set locked\|model]`; `start --voice-mode` is sent once the story exists, only while the server stores none; `step`, the estimate and `film` print `Voices for the next take: …`; the Voices gate runs only on `locked`. An older server (bare 404) reads as locked. |
| Hook line (episode opening) | Aligned | `PATCH /v1/spines/{id}/episodes/{episode_id}/opening` with `{spine_version, hook_line: {kind: option, index} \| {kind: custom, text} \| {kind: off}}` (the app's opening picker uses the same route; answer: `hook_line`, `hook_line_selected`, `warnings`, never a refusal for craft). Kit: `hook-line --desk D --episode N --list` (saved spine, no call) / `--pick K` / `--text "..."` / `--off`; 409 `spine_version_conflict` is read again and sent once more. An older server (bare 404 or 405), or a 422 on custom text: the pick is kept in `shared/hook-line.json`, used by `finish` / `join`, and the app still shows the server's pick. |
| Sound notes | Aligned (`sound-note`; `take-facts --refresh` for a filmed take) | `POST /v1/spines/{id}/sound-notes` `{spine_version, text}` ("less sound effects", "no purring"), `DELETE .../sound-notes/{note_id}`; at most five; saving spends nothing. Drop and level notes apply to every take's SFX cue plan. With fictora-drama #467, a note can add one sound to one take, in its words or the optional `episode_id`, `take`, `shot`, `row` fields; an add note naming no take is `422 sound_note_needs_take`. A note reaches only take facts fetched after it: `take-facts --refresh` fetches a filmed take's facts again (new version) and `finish` warns when its facts are older than the notes. With fictora-drama #475, facts read with `spine_id` carry the drop and level notes: `sfx_cues[].gain_offset_db` + `note_ids`, `sfx_dropped_cues[].dropped_by_note_id`; `finish` mixes at −8 dB + the offset and never lays a dropped cue, and never re-applies a note. |
| Impact SFX in the cue plan | Aligned once fictora-drama #462 is live | The take facts' `sfx_cues` also carry every impact the story's action states (a beat's `end_state` or `sound_cue`, a row's `chain`, `ends` or `sound`): one short event cue per impact on the shot that prints it, not laid twice when the Sound line names it, speaking shots included. Take facts saved before it lack them until `take-facts --refresh`. |
| Music + SFX | Aligned | `POST /v1/spines/{id}/audio-bed` pins `series_audio_bed_url`. Post uses that file instead of generating a new show bed. Product library beds still mix unless `FICTORA_DRAMA_AUDIO_BED=off`. |
| Ducking under voice | Aligned | Product ffmpeg mix uses `sidechaincompress` against the processed take stem, not a static −12 dB envelope. |
| Captions | Aligned | `caption_style=house`: yellow `#FFE500`, Poppins Bold, black edge, soft shadow, no box, text bottom at 62% of frame height (block in 55–70%). English flickers; a translation holds the whole line. Locally (`finish` / `caption`): an off-screen or voice-only line is set in Georgia italic; a line with no English is left uncaptioned with a `NOT ENGLISH` warning. |
| Episode thumbnail | Aligned | `POST /v1/spines/{id}/episodes/{n}/thumbnail` `{video_url}` (tenant-owned take clip from `17_raw_scene_clips.json`) → `{image_url, width, height, cached, cost_usd}`. **`fictora-produce finish --thumbnail`** calls it after the Sokii mark (the $0.30 price printed first, booked as `thumbnail`), saves `take-epNN-tK-thumb-vN.jpg`, and delivers `take-epNN-tK-sokii-cover-vN.mp4` with that image as the MP4 attached picture. Without `--thumbnail` finish never calls it: a cover already saved for the same clip is embedded again locally ($0), else the step is skipped with the price. Skips when there is no stored clip URL. `--no-thumbnail` skips the step. An older deploy without the route skips with a note (OpenAPI probe). |
| Reel cut (social edit) | Local only, no route | `fictora-produce reel` cuts a reel from the desk's rendered footage on this laptop ($0, ffmpeg): no new video, no server call, nothing uploaded. The plan reads the saved spine's beats and the saved take facts (shots, people, sound events); the strongest-frame score is the kit's own (the server sends no per-shot strength). |
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
