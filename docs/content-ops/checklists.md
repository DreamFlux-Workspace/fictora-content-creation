# Gates and take-read checklists

Copy the relevant block into chat when you stop. Do not continue until every box is true.

## Before the draft is sent

- [ ] A new desk, never an existing one overwritten: if the date and slug already exist, the human chose (continue it, or a new name)
- [ ] `fictora-produce start` carries `--cut-tempo` chosen from the scene (shot plan) and the nearest `--preset-id`; a look frame to pin, or look notes planned for the preset's world
- [ ] No arc asked for and no later episode outlined (episode 1 alone)
- [ ] Hook: frame 0 mid-motion on a face, first line by ~0.5 s, the reveal by ~3 s
- [ ] Real ages written; nobody under 18 in any romance; voice-only characters marked in the cast table
- [ ] Each line said TO someone; a silent comic beat costs a line in 15 s, and the human chose
- [ ] Source material: licence and credit named (SCP is CC BY-SA 3.0, credit the author); no unlicensed look copied
- [ ] Japanese/Korean: register fits who speaks to whom; a dialect has a native speaker's yes, or standard language

## After the draft, before the look

- [ ] Every brief line compared with the script (the draft `step` prints kept / rewritten / cut / added); each line that is not `kept` shown to the human beside the script line
- [ ] A line marked "keep exactly" is unchanged, or fixed with `line` before the script gate
- [ ] Japanese/Korean: each performed line pinned, and each line has an English subtitle (the caption)

## Before the look is approved

- [ ] Our own frame, drawn with `look-frame --description` from a written description (never a third-party image), shown to the human
- [ ] The human was told, in these words: takes film on Turbo, which redraws a look far from anime (pastel, watercolour, painterly, cartoon) in H3's own anime finish; `finish` colour-matches colour and light, not the drawing style
- [ ] Only after the yes: `look --url <image_url>` pinned it

## Before plates are approved

- [ ] `plates/` contains the actual images, not a description
- [ ] Each speaking character has full-length + bust
- [ ] Two characters in one frame are separated by build and colour
- [ ] Ages are written as real ages; nobody under 18 is in a romance
- [ ] A voice-only (off-screen) cast member got no plate (every line off screen, in no frame)
- [ ] The preset's world did not leak in (settings, props; expected only in plates drawn before a look frame was pinned); look notes set if it did
- [ ] One wrong character: `redraw-plate --cast NAME --note "…"`, the note describing shapes, not judgements
- [ ] A plate refused by image moderation (more often with a child) reported to the human with its job id, not retried blind
- [ ] Human has opened the contact sheet and said yes

## Before the script is approved

- [ ] Three or fewer lines per 15s take
- [ ] Lines are in the language they will be spoken, with the translation beside them
- [ ] Each line has its own cell with no competing business
- [ ] Facts the picture cannot show are in a line
- [ ] A silent comic beat was paid for with a line, and the human chose it
- [ ] Every line is said TO someone in the room, or to an off-screen voice with a source
- [ ] Every brief line the writers changed or dropped is shown beside the spine line; a "keep exactly" line that was dropped, moved to another speaker or translated is fixed
- [ ] Japanese / Korean lines sound native for who speaks to whom; a dialect has a native speaker's yes
- [ ] First beat carries the hook: mid-motion on a face, a line by ~0.5 s, the reveal by ~3 s
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
- [ ] Both men (or both figures) appear once per frame unless the brief says otherwise
- [ ] No readable text or digits in frame
- [ ] Frame 0 is mid-motion on a face, not a wide or a still
- [ ] Visible cause: every action row follows a visible face reacting to its cause, or shares its frame
- [ ] Each row opens on the emotion the row before it ended on
- [ ] A move the beat states is drawn nearer in the next row that shows it; a horror threat has its own insert or POV row
- [ ] Each spoken line sits on a row with its speaker in frame, medium or closer; no off-screen speaker drawn
- [ ] No clench, grit, pressed or closed mouth on a speaking row; big acting before and after the line
- [ ] Expressions fit the moment (not the genre) and sit on the right face
- [ ] No face, eyes, mouth or key prop in the top 8%, bottom 20%, or right 12% of the lower two thirds of any cell

## Before episode 2's idea

- [ ] Episode 1 approved (script, board, a finished take)
- [ ] The human named the intended run; `--episodes N` passed on `arc --list` and `arc --pick`
- [ ] `arc --list` printed three arcs from the server (not arcs you wrote); each can carry N episodes; an arc that closes within a few episodes refused
- [ ] The three were pasted to the human; the human picked one or rewrote it; `arc --pick K` kept it (it can change until episode 2 is written, then `409 series_arc_not_open`)
- [ ] Episode 2's idea is a direction the brief printed (`author --direction K`) or the human's own words (`author --line`), never series memory
- [ ] The printed script carries the human's idea
- [ ] `approve --gate script` for episode 2 alone (the desk points at it after `author`)

## Before the take is enrolled

- [ ] Cast approved and on screen
- [ ] Lines approved, three or fewer
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
- [ ] Every fault written down, including faults you will not fix

Then watch the file in `takes/`. A take that will be re-filmed gets no `finish`, no cues and no voice lines.

## Before the take is handed over

- [ ] Local finish ran (`fictora-produce finish`) and ended `Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`; the raw take is never the deliverable
- [ ] `review` run again on the finished file: loudness in band, safe zones clear, zone sheet looked at for faces
- [ ] Ducking under the voice about 8–9 dB (by ear: `finish` does not measure it; `--duck-db 9` when a line must sit clearly over the bed)
- [ ] No loudnorm anywhere; per-take gain and one limiter
- [ ] Stray speech muted (`--mute`); every hand cue answers a visible action, inside its own take, and audible
- [ ] Captions are English, in the 55–70% band, on screen only while the line is spoken
- [ ] Any `trim` / `tempo` done after `finish`, on the finished file; first frame after a cut checked; new length stated (shorter than the band is fine)
- [ ] Join: one bed across it, 24 fps, seam steps under 5 dB
- [ ] Un-marked master kept (`…-cap-vN.mp4`); the marked `…-sokii-vN.mp4` is the delivered copy
- [ ] Job ids are in `run-notes.md`; no compiled prompt was fetched or saved

## Re-film

Allowed only with a written cause that names what in the direction produced the fault, and what specific words change.

Examples that justified spend:

- Line dropped → the cell carried seven actions; give the line its own cell
- Doors cycled → "stands in the doorway" drew a wall; put figures one step inside
- Glove detached → forbidding it failed twice; change the pose so the contact does not exist

Not a cause: a detail slightly off-model, a minor background object, a beat that reads at 80%.
