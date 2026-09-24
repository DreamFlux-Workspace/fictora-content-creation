# Episode Production Runbook

Making Fictora episodes through the Drama Generation API, from a terminal, with Cursor or Claude Code.

2026-09-19 · @Someone. In-repo copy for content ops. The PDF remains the issued original.

This is the order of operations, the gate before each payment, and the list of things that cost money when done in the wrong order. It is also the context an agent reads when it calls the API. Limits here are meant to be exact.

## Who this is for, and what aligned means

The three productions behind every rule:

| Series | Episodes | Length | Takes/ep | What it proves |
| --- | --- | --- | --- | --- |
| One More Round | 4 | 15s | 1 | Cheapest shape. One unbroken shot, inner monologue, no dialogue rendering. |
| Same Floor (The Elevator) | 3 | 30s | 2 | Two-hander dialogue in Korean, rendered in the take. Episode 1 is the cautionary tale: six renders of one take, all design churn after the money was spent. |
| Sauce Left Over | 2 | ~45s | 3 | Wordless making takes interspersed with dialogue. Cheapest per second. ~45s was assembled by hand. Deviation, not a template. |

**Aligned** means every stage the product can do is done with the product's API call, in the product's order, passing the product's gates. The API is the default path. Where the API cannot do a stage, you may build a scratch harness, but the deviation is declared before it is built.

Two rules sit above everything else:

1. A human says yes before money moves. Plates, board, take, in that order. Each one shown. Each one approved. No batching of gates.
2. Every paid unit is rendered once. A second render needs a written root cause naming what in the direction caused the fault. "Try again" is not a cause. In nine cases out of ten the fault was the brief, not the model.

## What an episode is

An episode is a chain of 15-second takes. Each take is one unbroken camera move. Every take opens on the frame the previous take ended on.

The band is season-locked on the spine. Choose 15s or 30s and hold it.

| Duration band | Takes (storyboard sets) | Beats per set | What it suits |
| --- | --- | --- | --- |
| 15s | 1 | 3 | Inner-monologue. One situation, one turn. |
| 30s | 2 | 2, 3 | Two-hander: setup take + payoff take. |
| 60s | 4 | 2, 3, 2, 3 | A full scene with a making/wordless take inside it. |

**Take length defaults to 15 seconds.** `clip_duration_seconds` accepts 4–15. Send 12 for a shorter take. 16 is a rejection. The harness body takes the length you pass. Use `cut_tempo=one_shot` for one unbroken camera move.

Fixed properties of a take:

- One unbroken shot, boarded as the beginning and end of each phase of one camera move. The camera is a little closer in each cell.
- Three spoken lines maximum. A four-line 15s take drops a line, usually the one that carries the story.
- A hand-off frame. Spoken successors paste the prior last frame into cell 1a. A making (wordless) take pastes the next take's opening into its last cell. Prefer ending a spoken take on a wide.
- 9:16. Portrait. 768P on the H3 lane.

Sound and captions are built after the take comes back, never asked of the model:

- One music bed per series, not per episode. Generated beds run about 30 seconds. Loop them and assert the mix covers the probed cut, or the tail loses its music with no error.
- Cues are made only when the take returns quiet. Exteriors come back near-silent (−38 LUFS). Interiors with dialogue come back usable.
- Voice-over lines are generated dry and placed on measured beats in post. They are never written into the take as fixture lines.
- Captions: when the caption is the same language as the audio, word-by-word flicker in the safe band. When the caption is a translation, the whole line goes up and holds for exactly as long as that line is spoken. Both are yellow `#FFE500`, Poppins Bold, black edge, soft shadow, no box. Text bottom sits at 70% of frame height; size is 50 px on a 1344 px frame (3.7% of height). Flicker builds up to three words, then resets. On screen only while the line is spoken. Times come from silence-end onsets, not Whisper word starts across a pause.
- H3 cell seams are a compiler product. Post detects them with a frame-difference trace and dissolves each seam in place. Do not keep a private `soften.py`.

## The nine stages

```
1 Brief → 2 Look → 3 Cast plates → 4 Script → 5 Board → 6 Estimate → 7 Take → 8 Read the take → 9 Post
                                                                              ↓ fault with a named cause
                                                                            5 Board
```

1. **Brief.** One line of premise, the cast, the set, the arc. If the story needs a fact the picture cannot show, that fact must be spoken in a line. Decide now.
2. **Look.** Text-free crops of reference frames. An image sets the medium. Words do not.
3. **Cast plates.** One full-length figure plus a bust per character, plus object plates for any prop that must stay consistent, plus a location sheet for a set that recurs. Gate: show the plates, get a yes.
4. **Script.** Beats and lines per take, three lines maximum. Gate: the lines, in the original language, with the translation.
5. **Board.** The storyboard mosaic. Measure mean luma before showing it. Interiors below about 25% will render dim. Gate: the board image itself.
6. **Estimate.** Price the batch before enrolling it.
7. **Take.** The only expensive call. Everything above exists to make this call succeed once.
8. **Read the take.** Measure first: transcript, cut count, loudness, exposure. Then compare against the board. Write down every fault, including the ones you will not fix.
9. **Post.** Voice on measured beats, cues only where silent, mix with music ducked under voice, captions, join, loudness check.

The gates at 3, 4, and 5 are not optional and are not batchable. A wrong face at the plate stage costs $0.30. The same face after the take costs $1.50 and a re-board.

## How the loop runs

The agent stops after every step and waits. It does not chain two stages. It does not decide a step was good enough.

Confirm after each step, before the next one. Draw the plates → stop → report what was made and where it is → wait for a yes.

The folder is the review surface. The report says the path, not just the verdict.

Never overwrite silently. A new version is a new file: `board-ep01-t1-v2.png`. Rejected versions stay on disk.

Ask for the merged cut whenever an episode has a second take. Joining costs nothing. One music bed across the whole thing. Soften every seam. Assert 24 fps. Check loudness across each seam — a 5 dB step is audible. The merged file is a new file. Individual takes stay on disk.

## Spend

Lane check: every take uses H3 Max Turbo I2V, `minimax/h3-max-turbo/image-to-video`. The start frame is the board mosaic. Cast plates and voice clips are not sent on this lane.

| Unit | Cost | Notes |
| --- | --- | --- |
| Cast or object plate | $0.30 | Per image. A character needs two (full + bust). |
| Storyboard | $0.30 | Per board. |
| Take (15s, H3 Max) | $1.20 | The only large unit. |
| Voice audition set | $0.30 | 8–10 candidates on the real lines. Once per character, ever. |
| Voice lines for a take | ~$0.10 | Three lines. |
| Music bed | ~$0.10 | Once per series. |
| SFX cue set | $0.40–$0.70 | Only for takes that come back silent. |

Envelopes: continuing 15s $2.50. Continuing 30s $4.00. First episode of a new series $4.50. Past twice the envelope, stop and escalate.

"Change this" is billed. A retry must reuse the same idempotency key. Two jobs for one take means that key was missing. Report it.

## Craft rules that cost money when broken

### Look and medium

- Only an image sets the medium. A reference frame as the first image decides ink or photography.
- Say what you want. Never name what you don't. "No mirrors" puts mirrors in the frame.
- Faces come from crops of references, never from the reference wholesale, and never from a real person's likeness.
- Describe the artwork once. The look is settled for the series. A near-copy of the reference is a fault. Look drift between episodes is a fault.
- Consistency is inherited from plates and accepted frames, not from repeating the style paragraph.

### Boarding a continuous shot

- No shot per beat. A fight or a conversation is three or four continuous moves with the reactions inside them.
- Board one continuous move as a row: beginning of the phase on the left, end on the right. Two moments per row, never more.
- Ask once: "no cut anywhere; the camera is a little closer in each cell."
- A wordless, locked-camera take for a two-hander always reads as a slideshow. Every take carries lines; every shot carries a camera move.
- A take that opens mid-action with nothing to draw will render the storyboard. Give it something to open on.
- A making or montage take is boarded backwards: it must end on the next take's first frame.

### Continuity

- Paste, don't redraw. The previous take's real last frame goes into the next board's first cell.
- Hand off on a wide.
- The hand-off is the last frame with picture — never a fade, never black.
- When an object must match across takes, crop its reference from the take you accepted.

### Things that drift

- Objects drift unless anchored. Pin a story object with its own cropped reference and state how many exist.
- A coloured detail named in a second place becomes a second object.
- Duplicates are compositional, never numeric. "Only one person" does not help.
- When a prop keeps detaching, change the pose so the contact does not exist. Forbidding it in words fails twice.
- No digits and no readable text anywhere.

### Dialogue

- Three lines maximum per fifteen seconds.
- A line needs its own cell with no competing business.
- Never write a character speaking with a full mouth.
- Direct volume as clear and audible. "Barely audible" renders at −50 dB.
- A gap between spoken lines can render as garbled fake speech. Mute that window in the mix. Do not re-film.
- A character's first speaking episode needs its own voice audition on their own real lines.

### Light and sound

- Measure the board's brightness before you look at it as a creative choice.
- A scene lit only by a red emergency lamp renders at about half the readable brightness. Plan to lift it afterwards.
- Never use a broadcast loudness pass on action. Per-take gain and one limiter.
- Music and effects are compressed under the voice, not simply turned down.
- Check that a generated sound effect actually contains a sound.
- A generated music bed is about 30 seconds. Loop it for anything longer.

### Choosing sound

You pick the sound. You do not re-roll it. Voices are auditioned then locked. Music is chosen from two or three beds, then reused as the series bed. Effects are generated per take that came back quiet, then levelled. Almost every sound note is a mix note. Say the level, not the verdict.

## Reading a take

Measure first, then watch.

| Check | What good looks like | If it's wrong |
| --- | --- | --- |
| Transcript | Every line heard, exactly as written, with timing | A missing line is the one real re-film. A stray mumble is muted in the mix. |
| Cut count | Zero, for a one-shot take | Forced cuts at line boundaries are expected. Soften in post. |
| Loudness | About −15 to −20 for a dialogue take | Below −30 is effectively silent. Build cues. |
| Brightness | Roughly in the band the board was in | Dim sections get lifted afterwards. |
| Beat-by-beat vs board | Every beat present, in order | Missing beats are a direction fault. Write the cause before spending again. |

The automatic scene detector does not see this model's cuts. Compare consecutive frames. The automatic transcript sometimes stretches a syllable across several seconds. That is a mis-assignment. Anchor the caption on a later word.

Faults accepted without a re-film (calibration): a door frame in the opening second; a torso slightly more toned than the plate; a bag drawn twice for one second; an emergency lamp staying lit; small stickers on a car window; an elbow tap that did not read; a character walking with his back to camera for three seconds. Write them in the notes. Leave them.

## Scratch tools

Allowed: anything in a Gap or Partial row of the API table, and post after the take is delivered.

Not allowed: re-implementing an Aligned row; changing settings inside the product's own code; quietly producing a different outcome than the product.

Every scratch tool owes: a line in the run notes, a one-line backlog entry, and the tool left in the run folder.

## Deviation warning

Before any step that costs money, say one word: **aligned** or **deviation**. If deviation, say this before doing it:

```
DEVIATION — this is not the product flow
What I'm about to do: <the thing>
Why the API can't:     <the endpoint or field that refuses it>
Product gap:           <one line for the backlog>
Extra cost:            <$ and minutes>
```

Always warn for: assembling a length other than 15, 30, or 60s; editing product code or settings (never acceptable — propose it to engineering).

Do not warn for: `clip_duration_seconds` in 4–15; `cut_tempo=one_shot`; `caption_style=house`; product hand-off paste; 2×N row boards when every frame sets `board_row`; `PUT .../inner-voice`; voice auditions and pick; `POST .../audio-bed`; product sidechain ducking. Those are aligned.

Money warnings:

- "This re-render will cost $1.20 and here is the cause I've identified." No cause, no re-render.
- "This episode is now at $X of a $Y envelope." Said when it crosses, not at the end.
- "I could not verify this; here is what I could not check."

## Folder layout

Ask where the folder should go before anything is made. Default: `~/Downloads/documents/YYYY-MM-DD-<series>/`.

```
run-notes.md
reference/     source crops and every hand-off frame
plates/        cast, prop, set + contact sheets
boards/        every version kept
fixtures/      beats, lines, camera
voices/        auditions, pick, generated lines
sfx/  beds/
takes/         raw, mix, captioned final, joined episode
scripts/       tools used this run
artifact/      series write-up
api/           JSON from the drama API (not the review surface)
```

Naming: `board-ep01-t2-v3.png` is episode 1, take 2, version 3.

## Done

An episode is finished when:

- Every spoken line is heard, exactly as approved.
- The film reads as one continuous move per take. Forced cuts are softened.
- Each take opens on the frame the previous one ended on.
- Music runs to the last second, ducked under every line.
- Loudness is consistent across takes.
- Captions follow the transcript-vs-translation recipe. House style. On screen only while the line is spoken.
- The last frame is saved as the hand-off for the next episode.
- `run-notes.md` carries the ledger, the faults accepted, and the causes of anything re-filmed.
- Spend is inside the envelope, or the overage is explained in one line.

A series is finished when there is also a joined cut of every episode, one music bed, a write-up that names what the product could not do, and the learnings folded back.

The measure of whether this process is working is not whether the films are good. It is whether a good film cost one take.
