# Placing sound cues

`fictora-produce finish` lays the take's own sound effects from its take facts (`GET /v1/jobs/{take_job}/take-facts`, never the prompt). Those cues come from each shot's Sound lines, the impacts the story states (a beat's end state, a row's chain or sound; fictora-drama #462) and the sounds a sound note adds to the take. Take facts saved before an impact was planned or a note was added lack it until `take-facts --refresh` (below). Use this page when you place a cue by hand: a cue neither planned, a cue for a wordless making take, or a sting no note can place.

Make it on the server, then lay it in the finish:

```bash
uv run fictora-produce cue --desk D --episode N --description "a descending comic brass sting" [--seconds 1.5]
uv run fictora-produce finish --desk D --episode N --take tK --cue epNN/sfx/cue-a-descending-comic-brass-v1.mp3@4.2[@-6]
```

`cue` costs about $0.002 a second (0.5 s at least), saves `epNN/sfx/cue-<words>-vN.mp3` with a sidecar, and prints the cue's RMS per half second and its shape check. The same description and length answer the same file for free: to try again, change the words. Book it with `fictora-ops spend --desk D --episode N --usd X --unit cue:<name>` when the command did not.

The take's own effects are levelled or dropped with `finish --sfx-adjust` (`"door=-6"`, `"hum=drop"`, `"shot:3=+4"`), never re-rendered.

A sound the human asks for on one take goes on the story as a sound note, then into the take's facts:

```bash
uv run fictora-produce sound-note --desk D --episode N --take tK [--shot S | --row R] "add a dry stone crack at the end"
uv run fictora-produce take-facts --desk D --episode N --take tK --refresh   # new version; prints the cues it adds
uv run fictora-produce finish --desk D --episode N --take tK
```

Both are free. A take filmed before the note keeps its saved facts until `--refresh`; `finish` prints `!!` when its facts are older than the notes. `sound-note` with no take is a drop or level note ("no purring", "louder rain"): the server's mix reads it, `finish` does not, so pass the same change with `--sfx-adjust`. `sound-note --desk D` lists the notes; `--remove N` drops one.

Every rule here was paid for in a production.

## One cue per visible action

- A cue answers something the viewer can see: a pour, a knife on a board, a door, a cup set down. No picture, no cue.
- One cue per action. Two cues on one action read as two actions; a looped cue under a single gesture reads as the gesture repeating (Dumplings ep 3: "multiple purrs").
- Place it on the frame where the action lands, not where it starts. Find that frame on the contact sheet, then check it at full rate.
- Room tone and weather are the bed, one layer per location, not a cue per shot.
- Times are seconds on the take **as filmed** (the raw take). `deboard` keeps the timeline, so never subtract the board frames.

## Measure the cue's shape before you place it

A generated cue is not always what its name says. A "groan" came back as a click; a fridge hum carried a purr band.

- Read its RMS per half second (`cue` prints it). The shape check is a guess from the words: a short click-and-squelch was once called "sustained". Trust the numbers, not the label.
- An event (a door, a can) must hit in its first half second and stay under about 3 seconds.
- A sustained sound (rain, hum) must not die after its first half second.
- A generated ambience often peaks at its end. Place a cue by where its energy is, not where the file starts; if the strong part is late, describe a shorter sound and make it again.
- A cue that is wrong once is rendered once more (new words), then dropped with a line in the run notes. A missing cue never blocks the take.

## Clamp every cue to its own take

- A cue that outlives its take becomes a sound in the next location with no cause on screen. `finish --cue` clamps every hand cue to end 0.15 s before the take does, and refuses a cue that starts outside the take or has under 0.25 s of room.
- In a joined episode, a cue belongs to the take it is placed on. Place cues on the take (`finish` on that take), before the join, never on the joined file.
- `finish` checks every `--cue` file before any step: a missing or silent file (no half second above −50 dB RMS) stops it with nothing written. The laid cue layer is measured too: a cue that comes out silent there fails the step and the take reads `NOT DONE` (`· hand cues ✗`).

## Anime comedy cues

Names that worked for Hanakaze Sweets: a "gaan" sting (a descending brass or piano hit for gloom), a vein pop, a rumble or "gogogo" (menace building), a sign clack, cane thuds, a wood crack, a boing. One per visible mark or action, placed where the mark appears.

## Level

- Cues sit under the take: the default is −8 dB (`--cue FILE@S`); `@DB` sets it (−40 to +30). Move in 2–3 dB steps. A level note is a mix note, never a re-render.
- A generated cue can come back quiet (a "neck crack" at −31 dB RMS peaked 10 dB under the bed at +8 and needed +20). `finish` fails only on a silent cue, and prints `!! cue … peaks N dB under the music bed` for a take-facts cue buried under the bed. Listen for each hand cue in the finished file and raise it until it reads.
- Hand cues sit 10 dB lower while someone speaks (speaking shots from the take facts, and hand lines). Do not hand-duck cues around lines.
- No loudness normalisation on a take. The mix gives it one gain and one limiter.

## Record it

Write one line per hand cue in `run-notes.md` (`fictora-ops note`) saying which action it answers and where it sits (`cue-gaan-sting-v1.mp3 @ 4.2 s, −6 dB: her face falls after the sign cracks`).
