# Gates and take-read checklists

Copy the relevant block into chat when you stop. Do not continue until every box is true.

## Before the draft is sent

- [ ] A new desk, never an existing one overwritten: if the date and slug already exist, the human chose (continue it, or a new name)
- [ ] `fictora-produce start` carries `--cut-tempo` chosen from the scene (shot plan) and the nearest `--preset-id`; a look frame to pin, or look notes planned for the preset's world
- [ ] No arc asked for and no later episode outlined (episode 1 alone)
- [ ] Hook: frame 0 already moving (the strongest image on the server's spectacle-first genres, a face on every other genre), first line by ~0.5 s (or a silent open the picture carries), what the episode is about clear by ~3 s
- [ ] Real ages written; nobody under 18 in any romance; voice-only characters marked in the cast table
- [ ] Source material: licence and credit named (SCP is CC BY-SA 3.0, credit the author); no unlicensed look copied
- [ ] Japanese/Korean: register fits who speaks to whom; a dialect has a native speaker's yes, or standard language
- [ ] Japanese/Korean-voiced: `start --language ja|ko` (it cannot be changed later)
- [ ] A creature inspired by a reference is written as a new original design; no stock character names ("Mara Voss")
- [ ] No music asked for: the videos carry none (a trending sound is added in Instagram or TikTok at posting)

## After the draft, before the look

- [ ] Every brief line compared with the script (the draft `step` prints kept / rewritten / cut / added); each line that is not `kept` shown to the human beside the script line
- [ ] A line marked "keep exactly" is unchanged, or fixed with `line` before the script gate
- [ ] Japanese/Korean: each performed line pinned, and each line has an English subtitle (the caption)

## Before the look is approved

- [ ] Our own frame, drawn with `look-frame --description` from a written description (never a third-party image), shown to the human
- [ ] The look-frame words stay stylised (anime/manhwa; no photoreal, live-action or real-film look) and name no real person
- [ ] The human was told, in these words: takes film on Turbo, which redraws a look far from anime (pastel, watercolour, painterly, cartoon) in H3's own anime finish; `finish` colour-matches colour and light, not the drawing style
- [ ] Only after the yes: `fictora-produce approve --desk D --gate look` recorded it and pinned the frame (the newest look frame unless `--path`/`--url` named another). `step`, `redraw-plate` and `redraw-board` refuse until then, and again for a look frame drawn after the yes

## Before plates are approved

- [ ] `plates/` contains the actual images, not a description
- [ ] Each speaking character has full-length + bust
- [ ] Two characters in one frame are separated by build and colour; every named character has a clearly different outfit and silhouette
- [ ] Jackets and uniforms: plain back, no lettering or logos
- [ ] A child in a coat or dress has leggings or shorts under it
- [ ] Ages are written as real ages; nobody under 18 is in a romance
- [ ] A voice-only (off-screen) cast member got no plate (every line off screen, in no frame)
- [ ] The preset's world did not leak in (settings, props; expected only in plates drawn before a look frame was pinned); look notes set if it did
- [ ] One wrong character: `redraw-plate --cast NAME --note "…"`, the note describing shapes, not judgements
- [ ] A plate refused by image moderation (more often with a child) reported to the human with its job id, not retried blind
- [ ] Human has opened the contact sheet and said yes

## Voices (after the plates, before filming)

- [ ] `voice-mode --desk D` read; on a show whose earlier episodes used the video model's own voices, the human was asked "Keep the same voices as earlier episodes (recommended) or switch to locked voices?" and the answer stored (`--set model` / `--set locked`). On `model` the rest of this list is skipped.

- [ ] Every speaking character's locked voice was shown to the human (`voice --desk D --list`), with an audition offered (`voice --cast NAME --audition`, about $0.02-0.05 a character, priced on its lines; only after their yes)
- [ ] Each voice kept (`voice --cast NAME --keep` / `--keep-all`) or picked (`--pick N`) by the human; never kept for them

## Before the script is approved

- [ ] Three or fewer lines per 15s take
- [ ] Lines are in the language they will be spoken, with the translation beside them
- [ ] Each line has its own cell with no competing business
- [ ] Every key fact is shown in the picture, or said when the picture cannot show it
- [ ] Every brief line the writers changed or dropped is shown beside the spine line; a "keep exactly" line that was dropped, moved to another speaker or translated is fixed
- [ ] Japanese / Korean lines sound native for who speaks to whom; a dialect has a native speaker's yes
- [ ] First beat carries the hook: already moving (a face, or the strongest image on a spectacle-first genre), a line by ~0.5 s (or a silent open the picture carries), what the episode is about clear by ~3 s
- [ ] Authoring notes (`note: … words (10 recommended)`) shown to the human; they are nudges, the creator decides, nothing was blocked
- [ ] Human said yes to the lines

## Before the board is approved

- [ ] Human has looked at the newest `boards/` file and the shot list `step` printed
- [ ] The rows match the take's declared shot plan (at most four rows, four shots)
- [ ] Coverage takes: no two neighbouring rows at the same size and angle; a wide, a face close-up, and an insert or point of view; every row names its camera move with direction and size ("camera holds" only on an insert) and big physical acting
- [ ] Horror: no whole take on one held shot or a fixed wide; the scare has an insert or point of view; the threat comes toward the camera
- [ ] A jump the viewer must see is split across rows, never two cells of one row
- [ ] An exit names the destination with the character's back to the portal
- [ ] Agent reported brightness from `measure-board`
- [ ] Interiors are roughly 28–35% mean luma; below 25% is dim-risk
- [ ] If this take follows another: hand-off frame is in `reference/` and pasted into cell 1, not redrawn
- [ ] Hand-off is a frame with picture, never fade or black
- [ ] A redraw followed an edit (frame, beat or look note) the human saw before and after; `--same-shots` only for a random bad draw with the right frames
- [ ] Both men (or both figures) appear once per frame unless the brief says otherwise
- [ ] No readable text or digits in frame
- [ ] Frame 0 is already moving (the strongest image on a spectacle-first genre, else a face), never an empty room, a slow drift or a still
- [ ] Visible cause (eye check; the server's writer stages it): every action row follows a visible face reacting to its cause, or shares its frame; a death or a break shows who or what causes it
- [ ] A child: camera at or above their eye level; a fall or landing feet-first or seated, legs together
- [ ] Every frame on the board shares one location string; no line moved between beats
- [ ] A repurposed episode: title and summary updated before the redraw
- [ ] Each row opens on the emotion the row before it ended on
- [ ] A move the beat states is drawn nearer in the next row that shows it; a horror threat has its own insert or POV row
- [ ] Each spoken line sits on a row with its speaker in frame, medium or closer; no off-screen speaker drawn
- [ ] No `!! ... names X but lists Y` in the shot list: each cell lists the character its words describe (the kit also checks this before the boards are drawn and before a redraw, and stops the paid draw)
- [ ] No `!! row N: NAME "…" does not say which hand …` in the shot list without a look at that row: every hand on someone is attached to its own visible body, coming from the toucher's side of the frame (the kit warns before the boards and before a redraw; never a stop)
- [ ] No `!! ... hides the mouth` in the shot list: nothing over or at a speaker's mouth on a speaking row
- [ ] No `!! N insert cells in a row`: at most one insert per take with a line, never two in a row
- [ ] No `!! rows A→B: ... expression jumps` / `mood bounces` in the shot list: a face keeps its mood from row to row unless the beat puts a cause on screen (fix with `edit --frame N`, free, before the take)
- [ ] No clench, grit, pressed or closed mouth on a speaking row; big acting before and after the line
- [ ] Expressions fit the moment (not the genre) and sit on the right face
- [ ] No face, eyes, mouth or key prop in the top 8%, bottom 20%, or right 12% of the lower two thirds of any cell

## Before episode 2's idea

- [ ] Episode 1 approved (script, board, a finished take)
- [ ] The human named the intended run; `--episodes N` passed on `arc --list` and `arc --pick`
- [ ] `arc --list` printed three arcs from the server (not arcs you wrote); each can carry N episodes; an arc that closes within a few episodes refused
- [ ] The three were pasted to the human; the human picked one or rewrote it; `arc --pick K` kept it (it can change until episode 2 is written, then `409 series_arc_not_open`)
- [ ] The previous episode's last ~5 s as filmed and delivered was watched; the idea follows from that, not from its script
- [ ] Episode 2's idea is a direction the brief printed (`author --direction K`) or the human's own words (`author --line`), never series memory
- [ ] The printed script carries the human's idea
- [ ] The printed script was read closely before the yes: an authored episode cannot be authored again (`invalid_extension_ordinal`); before the yes, `rewrite --episode N --line "…"` re-writes a draft (free); after it, changes go through `edit` / `line`
- [ ] `approve --gate script` for episode 2 alone (the desk points at it after `author`)

## Before the take is enrolled

- [ ] Cast approved and on screen
- [ ] Every speaking voice kept or picked (filming refuses until it is)
- [ ] Lines approved
- [ ] Board approved and bright enough
- [ ] Hand-off in place when there is a predecessor
- [ ] `POST /v1/spines/{id}/batches/estimate` number is on the table
- [ ] Agent said **aligned** or posted a **DEVIATION** block
- [ ] Human said yes to the spend
- [ ] Spend so far is said as "$X of $Y" (first ep $5.50, continuing 15 s $2.50, continuing 30 s $5.00, continuing 60 s $8.00; warn, never block)

## After the take, before you judge it

Run `fictora-produce review --desk D --episode N --take tK` on the raw take and paste its block (thresholds: the skill's reference.md, "Review: the numbers"). It gives these numbers:

- [ ] Duration
- [ ] Transcript of spoken lines with timings
- [ ] Line check: every approved line was in the take's instructions (`check-lines`; a miss goes to engineering with the take job id)
- [ ] Cut count from consecutive-frame compare (not scene detection), against the declared shot plan
- [ ] Frozen or stacked double frames looked at full size; board frames only at the head
- [ ] Loudness (dialogue take about −15 to −20 LUFS; below −30 needs cues)
- [ ] Brightness vs the board
- [ ] Beat-by-beat read against the board
- [ ] A child in the take (not just the board): camera at or above eye level, falls feet-first or seated with legs together, legs covered under coats and dresses; checked before captioning
- [ ] The end of the take watched for invented text (lettering on backs, signs)
- [ ] Every fault written down, including faults you will not fix

Then watch the file in `takes/`. A take that will be re-filmed gets no `finish`, no cues and no voice lines.

## Before the take is handed over

- [ ] Local finish ran (`fictora-produce finish`) and ended `Sound: no music ✓ · SFX ✓ · mix ✓ · captions ✓`; the raw take is never the deliverable
- [ ] `review` run again on the finished file: loudness in band, safe zones clear, zone sheet looked at for faces
- [ ] No music on the delivered file (no `music !!` on the sound line; a take that has it is named to the human for a re-film)
- [ ] No loudnorm anywhere; per-take gain and one limiter
- [ ] Wrong take dialogue (stray or garbled speech, a wrong line): take audio dropped and rebuilt (voices, effects, ambience), no muted ranges; every hand cue answers a visible action, inside its own take, and audible
- [ ] Captions are English, where the show's caption style puts them (Bold bottom at 76%, Subtle in the 55–70% band), on screen only while the line is spoken; no `NOT ENGLISH` in the finish summary
- [ ] Off-screen and voice-only lines are in italic (the same face as the rest); every on-screen line is upright; one caption face in the whole episode
- [ ] Any `trim` / `tempo` done after `finish`, on the finished file, with the producer's yes; what was cut shown; first frame after a cut checked; new length stated (shorter than the band is fine). `deboard` holds board frames and never cuts
- [ ] No post overlay covers the key reveal (test frames of the key shots looked at)
- [ ] Previews, rough cuts and "just combine" files carry the house captions too
- [ ] Join: no music under it, 24 fps, seam steps under 5 dB
- [ ] At posting: a trending sound added inside Instagram or TikTok that fits the show; captions on; the "with score" version only when the episode truly needs it and was asked for
- [ ] Un-marked master kept (`…-cap-vN.mp4`); the marked `…-sokii-vN.mp4` is the delivered copy
- [ ] Job ids are in `run-notes.md`; no compiled prompt was fetched or saved
- [ ] The delivered file copied (not moved) into `<Series> - finished episodes`; the desk folder not moved or renamed

## Re-film

Salvage first (Gallery Heiress review, 12 Oct 2026). Before any re-film:

- [ ] The free edits were offered to the producer: `trim --cut` a short mistake (on screen about 0.5 s or less is always a cut), the trim handles for an end, `tempo` on one shot, `zoom` (punch-in) for a jump cut or a stray element at the edge, `join --take-file` in the right order for takes out of order
- [ ] Re-film only when none hides the fault (a smear or melt on a face, the wrong person throughout, a spoken line wrong on a model-voice take), or the producer saw the free edit and turned it down
- [ ] A decent take that is still re-filmed is on the reuse list (`keep-take --note "…"`)

Allowed only with a written cause that names what in the direction produced the fault, and what specific words change.

Examples that justified spend:

- Line dropped → the cell carried seven actions; give the line its own cell
- Doors cycled → "stands in the doorway" drew a wall; put figures one step inside
- Glove detached → forbidding it failed twice; change the pose so the contact does not exist

Not a cause: a detail slightly off-model, a minor background object, a beat that reads at 80%.
