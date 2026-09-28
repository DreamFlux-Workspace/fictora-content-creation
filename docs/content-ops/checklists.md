# Gates and take-read checklists

Copy the relevant block into chat when you stop. Do not continue until every box is true.

## Before plates are approved

- [ ] `plates/` contains the actual images, not a description
- [ ] Each speaking character has full-length + bust
- [ ] Two characters in one frame are separated by build and colour
- [ ] Ages are written as real ages; nobody under 18 is in a romance
- [ ] A voice-only (off-screen) cast member was flagged before the plate spend
- [ ] Human has opened the contact sheet and said yes

## Before the script is approved

- [ ] Three or fewer lines per 15s take
- [ ] Lines are in the language they will be spoken, with the translation beside them
- [ ] Each line has its own cell with no competing business
- [ ] Facts the picture cannot show are in a line
- [ ] A silent comic beat was paid for with a line, and the human chose it
- [ ] Every line is said TO someone in the room, or to an off-screen voice with a source
- [ ] Every brief line the writers changed or dropped is shown beside the spine line
- [ ] Japanese / Korean lines sound native for who speaks to whom; a dialect has a native speaker's yes
- [ ] First beat carries the hook: mid-motion on a face, a line by ~0.5 s, the reveal by ~3 s
- [ ] Human said yes to the lines

## Before the board is approved

- [ ] Human has looked at the newest `boards/` file
- [ ] Agent reported brightness from `measure-board`
- [ ] Interiors are roughly 28–35% mean luma; below 25% is dim-risk
- [ ] If this take follows another: hand-off frame is in `reference/` and pasted into cell 1, not redrawn
- [ ] Hand-off is a frame with picture, never fade or black
- [ ] Both men (or both figures) appear once per frame unless the brief says otherwise
- [ ] No readable text or digits in frame
- [ ] Frame 0 is mid-motion on a face, not a wide or a still
- [ ] Visible cause: every action row follows a visible face reacting to its cause, or shares its frame
- [ ] Each row opens on the emotion the row before it ended on
- [ ] Each spoken line sits on a row with its speaker in frame, medium or closer; no off-screen speaker drawn
- [ ] No clench, grit, pressed or closed mouth on a speaking row; big acting before and after the line
- [ ] Expressions fit the moment (not the genre) and sit on the right face
- [ ] No face, eyes, mouth or key prop in the top 8%, bottom 20%, or right 12% of the lower two thirds of any cell

## Before the take is enrolled

- [ ] Cast approved and on screen
- [ ] Lines approved, three or fewer
- [ ] Board approved and bright enough
- [ ] Hand-off in place when there is a predecessor
- [ ] `POST /v1/spines/{id}/batches/estimate` number is on the table
- [ ] Agent said **aligned** or posted a **DEVIATION** block
- [ ] Human said yes to the spend
- [ ] Spend so far is said as "$X of $Y" (first ep $5.50, continuing 15 s $2.50, continuing 30 s $5.00; warn, never block)

## After the take, before you judge it

Ask for these numbers first:

- [ ] Duration
- [ ] Transcript of spoken lines with timings
- [ ] Cut count from consecutive-frame compare (not scene detection)
- [ ] Loudness (dialogue take about −15 to −20 LUFS; below −30 needs cues)
- [ ] Brightness vs the board
- [ ] Beat-by-beat read against the board
- [ ] Every fault written down, including faults you will not fix

Then watch the file in `takes/`.

## Before the take is handed over

- [ ] Local finish ran (`fictora-produce caption`); the raw take is never the deliverable
- [ ] Captions are English, in the 55–70% band, on screen only while the line is spoken
- [ ] Job ids are in `run-notes.md`; no compiled prompt was fetched or saved

## Re-film

Allowed only with a written cause that names what in the direction produced the fault, and what specific words change.

Examples that justified spend:

- Line dropped → the cell carried seven actions; give the line its own cell
- Doors cycled → "stands in the doorway" drew a wall; put figures one step inside
- Glove detached → forbidding it failed twice; change the pose so the contact does not exist

Not a cause: a detail slightly off-model, a minor background object, a beat that reads at 80%.
