# Gates and take-read checklists

Copy the relevant block into chat when you stop. Do not continue until every box is true.

## Before plates are approved

- [ ] `plates/` contains the actual images, not a description
- [ ] Each speaking character has full-length + bust
- [ ] Two characters in one frame are separated by build and colour
- [ ] Human has opened the contact sheet and said yes

## Before the script is approved

- [ ] Three or fewer lines per 15s take
- [ ] Lines are in the language they will be spoken, with the translation beside them
- [ ] Each line has its own cell with no competing business
- [ ] Facts the picture cannot show are in a line
- [ ] Human said yes to the lines

## Before the board is approved

- [ ] Human has looked at the newest `boards/` file
- [ ] Agent reported brightness from `measure-board`
- [ ] Interiors are roughly 28–35% mean luma; below 25% is dim-risk
- [ ] If this take follows another: hand-off frame is in `reference/` and pasted into cell 1, not redrawn
- [ ] Hand-off is a frame with picture, never fade or black
- [ ] Both men (or both figures) appear once per frame unless the brief says otherwise
- [ ] No readable text or digits in frame

## Before the take is enrolled

- [ ] Cast approved and on screen
- [ ] Lines approved, three or fewer
- [ ] Board approved and bright enough
- [ ] Hand-off in place when there is a predecessor
- [ ] `POST /v1/spines/{id}/batches/estimate` number is on the table
- [ ] Agent said **aligned** or posted a **DEVIATION** block
- [ ] Human said yes to the spend

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

## Re-film

Allowed only with a written cause that names what in the direction produced the fault, and what specific words change.

Examples that justified spend:

- Line dropped → the cell carried seven actions; give the line its own cell
- Doors cycled → "stands in the doorway" drew a wall; put figures one step inside
- Glove detached → forbidding it failed twice; change the pose so the contact does not exist

Not a cause: a detail slightly off-model, a minor background object, a beat that reads at 80%.
