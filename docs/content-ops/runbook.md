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

1. A human says yes before money moves. Plates, script, board, the spend yes, in that order. Each one shown. Each one approved. No batching of gates. Silence is not consent; a yes from last time does not carry forward.
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
- Captions are English only for now. Japanese and Korean captions are deferred.
- Captions: when the caption is the same language as the audio, word-by-word flicker in the safe band. When the caption is a translation, the whole line goes up and holds for exactly as long as that line is spoken. Both are yellow `#FFE500`, Poppins Bold, black edge, soft shadow, no box. Text bottom sits at 70% of frame height, inside the social caption band (55–70%); size is 50 px on a 1344 px frame (3.7% of height). Flicker builds up to three words, then resets. On screen only while the line is spoken. Times come from silence-end onsets, not Whisper word starts across a pause.
- H3 cell seams are a compiler product. Post detects them with a frame-difference trace and dissolves each seam in place. Do not keep a private `soften.py`.

## The nine stages

This is the order `fictora-produce` runs them in.

```
1 Brief → 2 Draft → 3 Cast plates → 4 Script → 5 Board → 6 Estimate → 7 Take → 8 Read the take → 9 Finish
                                                                        ↓ fault with a named cause
                                                                      5 Board
```

1. **Brief.** One line of premise, the cast, the set, the hook, the lines. If the story needs a fact the picture cannot show, that fact must be spoken in a line. Decide now. Episode 1 is made on its own: no series arc yet (see Series arc).
2. **Draft.** The first `step` writes the story on the API. Compare its lines with the brief before the script gate. The draft writes episode 1 alone (`outline_mode=arc_at_episode_two`, `episode_count: 1`); later episodes are written with `author --episode N`.
3. **Cast plates.** One full-length figure plus a bust per character, plus object plates for any prop that must stay consistent, plus a location sheet for a set that recurs. Gate: show the plates, get a yes.
4. **Script.** Beats and lines per take, three lines maximum. Gate: the lines, in the original language, with the translation, and every line the writers changed from the brief.
5. **Board.** The storyboard mosaic. Measure mean luma and report it with the board; a dark board is the human's call, not a fault. Gate: the board image itself, checked against the board checks below.
6. **Estimate.** Price the batch before enrolling it. The line always names the lane and its $/s. If it starts with `!! SERVER ESTIMATE FAILED`, the server gave no dollars and the kit priced from its own table: tell the human before they say yes.
7. **Take.** The only expensive call. Everything above exists to make this call succeed once.
8. **Read the take.** Measure first: transcript, cut count, loudness, exposure. Then compare against the board. Write down every fault, including the ones you will not fix.
9. **Finish.** Local, on the laptop: `fictora-produce finish` (sound effects from the take facts, the show's music bed, colour match to the board, mix near −18 LUFS, captions, the mark), then the join. A take is not done until the finish ran. A raw take is never a deliverable.

The gates at 3, 4, and 5 are not optional and are not batchable. A wrong face at the plate stage costs $0.30. The same face after the take costs a re-board and a re-take ($0.60 through 30 Sep 2026: $0.30 board + $0.30 Turbo take; $0.90 from 1 Oct). On Turbo the board is the only picture the take gets, so a wrong face on the board is a wrong face in the take.

## How the loop runs

The agent stops after every step and waits. It does not chain two stages. It does not decide a step was good enough.

Confirm after each step, before the next one. Draw the plates → stop → report what was made and where it is → wait for a yes.

The folder is the review surface. The report says the path, not just the verdict.

Never overwrite silently. A new version is a new file: `board-ep01-t1-v2.png`. Rejected versions stay on disk.

Ask for the merged cut whenever an episode has a second take. Joining costs nothing. One music bed across the whole thing. Soften every seam. Assert 24 fps. Check loudness across each seam — a 5 dB step is audible. The merged file is a new file. Individual takes stay on disk.

## Spend

Lane check: takes on the `minimax-h3` lane film on **H3 Max Turbo image-to-video**, `minimax/h3-max-turbo/image-to-video`, at 768p. That is the server's default; H3 Max reference-to-video (`minimax/h3-max/reference-to-video`) is an engineering-side switch on the deploy, not something the desk picks. The estimate's `cost_estimate.video_endpoint_id` and each take's facts say which one filmed; price and quote that one.

On Turbo the take is image-to-video from the take's whole storyboard board: the board is the first frame and the only picture the model sees. Cast plates and voice references are not sent, so the board carries the faces and the look, and the raw take speaks in the model's own voice. A raw take can open on a frame or two of the board grid; `fictora-produce finish` replaces them first (`deboard`, measured against the approved board, length and sound unchanged).

| Unit | Cost | Notes |
| --- | --- | --- |
| Cast or object plate | $0.30 | Per image. A character needs two (full + bust). |
| Storyboard | $0.30 | Per board, first draw or redraw. |
| Take (15 s, H3 Max Turbo I2V, 768p), the default | $0.30 through 30 Sep 2026; $0.60 from 1 Oct 2026 | $0.02/s (fal promo), then $0.04/s. A 12 s take is $0.24 / $0.48. No image charge. A take under 5 s films and bills 5 s. |
| Take (15 s, H3 Max R2V, 768p), engineering switch only | $1.20 | $0.08/s. Plus $0.02048 per reference image past four (the board plus every cast plate, at most nine). Quote only when the estimate or take facts name it. |
| Voice audition set | $0.30 | Candidates on the real lines. Once per character; a second set needs a written cause. |
| Voice line | $0.10 per 1,000 characters | |
| Music bed | ~$0.10 | Once per series. |
| SFX cue | $0.002 per rendered second | Only for takes that come back quiet. |

Budgets are **warnings, never a hard stop**: first 15 s episode of a new series **$5.50**; continuing 15 s **$2.50**; continuing 30 s **$5.00**. A first 15 s episode filmed once is about $1.90 on Turbo (plates, a board, a take, cues; $2.20 from 1 Oct, $2.80 on R2V), so the budget covers redraws and re-films. Say "$X of $Y" when an episode crosses its budget, not at the end. Past twice the budget, say so and the human decides whether to go on.

"Change this" is billed. A retry must reuse the same idempotency key. Two jobs for one take means that key was missing. Report it.

## Craft rules that cost money when broken

### Series arc and episode 2 on

- Episode 1 is made on its own. Do not outline four or five episodes up front, and do not ask for an arc at the start.
- The arc is chosen at episode 2, after episode 1 is approved. Ask the human for the intended run (how many episodes) first; the arcs are written to carry that many.
- Check each arc can carry the run. For a long run prefer an engine (a situation that repeats with a new problem) plus a slow question. Refuse an arc that closes within a few episodes (a 25–30 episode run was once offered a one-week mystery).
- Each later episode is steered by its **direction**: the human picks one of the offered directions or says their own, and it reaches the writer in their words. Confirm the idea is in the printed script. Series-wide memory is for standing rules ("keep every episode punchy"), never for one episode's idea.
- Each episode's script is approved on its own. Never pre-stage scripts for later episodes.
- Commands: `fictora-produce arc --desk D --list [--episodes N]`, `arc --pick K`, `brief --desk D --episode N` (3 on), `author --desk D --episode N --direction K | --line "…"`, then `approve --gate script` and the usual loop. The run length is a soft default; the season continues past it.

### The hook: the first three seconds

- Frame 0 is mid-motion on a face: a hand already moving, a head already turning. It is the cover frame. Never an establishing wide, never a still.
- The first line lands by about 0.5 s.
- The premise's reveal (the thing the episode is about) is on screen or said by about 3 s.
- Check it in the brief, the script and the board, before the board gate. A board that opens on a wide or a pause is redrawn: fix the frame (`edit --frame … --set …`), then `redraw-board` (the route takes no notes). A weak open is never fixed by re-filming.

### Social safe zones

Every episode goes to TikTok, Instagram Reels and YouTube Shorts with one layout. On a 9:16 frame the platforms cover:

| Zone | Where | What covers it |
| --- | --- | --- |
| Top strip | top 8% of the height | tabs, search, camera |
| Bottom band | bottom 20% of the height | post caption, username, music |
| Right rail | right 12% of the width, lower two thirds of the height | like, comment, share |

- Faces, eyes, mouths and key props never sit in a covered zone. Bodies, hands, floor and set may run through them. Composition is otherwise free: faces are not forced to the centre (it looked ugly).
- Captions sit in the band 55–70% of the height.
- The Sokii mark sits top left, just under the top strip: `23:121` on 768×1344 (x = 3% of the width, y = 9% of the height), 0.6 opacity. Never top right.
- There is no face detector. The boards `step` warns when a frame's written placement puts a face or prop in a zone (text only); look at every cell anyway. A face or key prop in a zone: fix the frame's placement with `edit --frame`, then `redraw-board`.
- On the finished take, `fictora-produce review --desk D --episode N --take tK` measures the caption box on sampled frames against these zones and the 55–70% band, and writes a zone sheet (`<take>-zones-vN.png`, zones shaded red) for the face check. It warns and never blocks; faces are still the human's look.

### Expression library

The writers pick an anime expression (`reaction_kind`) for every emotional moment, on any cell. The creator does not pick it; the agent checks it at the board gate. The kinds and the marks each must show are in the skill's [reference.md](../../.claude/skills/episode-production/reference.md). What was learned paying for it:

- Smiling rage keeps the eyes OPEN with tiny trembling pupils and the too-wide grin: the smile must stay. Drawn with blank white eyes it played as a scowl.
- Gloom draws tatesen lines on a VISIBLE face. A hidden face or the back of a head loses it.
- Pick by the moment, not the genre. Every kind may play in any genre. The symbolic kinds (veins, sweat drops, gloom lines, chibi squash, smiling rage) suit a light, awkward, petty or comic moment, even inside a serious show; real dread, grief, danger or tenderness gets the plainer kinds.
- Whose face: on a cell with two or more people the writers name the face that wears the kind. A listener's expression on a speaking cell plays whole and silent on the listener. Check it on the board.

### Board checks

Check before the board gate:

- The rows follow the declared shot plan.
- **Visible cause.** Every action row is preceded by a visible face reacting to its cause, or shares the frame with it. "Jealousy → breaks the sign" did not read when the face was hidden and the insert showed only hands.
- **Each row opens on the emotion the row before it ended on.** A calm smile right after the sign broke read wrong.
- **The picture shows the rule.** A change the viewer must see as a jump goes in its own row. H3 blends the cells of one row into one move.
- **Speakers.** The speaker is in the frame of the row their line plays on, at a medium shot or closer, or the line plays over someone else's face. No off-screen speaker is drawn: not as a sleeve, a hand, a cane or a shadow. The board shot list prints the line and speaker on each row and warns when the speaker is not drawn there.
- **Speaking mouths.** No clench, grit, pressed or closed mouth on a speaking row. Big physical acting goes before the line and after it; during the words the mouth moves.
- The hook, the hand-off, the safe zones, no readable text or digits, brightness reported.

### Characters and age

- Characters may be any age. Write the real age ("10, primary-school kid", "sixteen") and the plate draws that age.
- The one hard line: never romantic, sexual, suggestive or fan-service framing of any character under 18, and never a romance arc for them (no love interest, crush, dating, or someone else's romance aimed at them). The server rejects a breach as `minor_in_romance_arc`. PG staging (no kissing, embracing or face contact) applies to everyone. Asked for a minor in a romance, say no and offer the character at 18+ or the relationship as non-romantic.

### Off-screen voices

- Declare an off-screen voice in the brief's cast table as voice only. It is never drawn. The plate stage still draws a plate for it today: say so before the spend.
- An off-screen voice that plays over another character's face is heard as that face speaking. Give it a source in frame (a wall grille, a phone in a hand) or a source treatment in post (band-limited intercom).

### Language

- A Japanese or Korean line must sound like a native speaker in that situation. The situation picks the set phrase (staff to customer: 申し訳ございません / 정말 죄송합니다, not ごめんなさい). No English quip carried word for word. No notice-board noun stack in speech (「逆襲中止！」 "counterattack cancelled!") unless the character really is announcing.
- A dialect (Kansai-ben, Busan satoori) needs a native speaker's yes before the script gate. With no one to check it, write the standard language.
- Pin the exact performed line so localization never rewrites it. Compare every draft line with the brief and show the human any line that changed.

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

- Three lines maximum per fifteen seconds. A 15 s take is three beats, so a silent comic beat (a pause, a stare, a freeze) costs a line. Say so at the brief.
- Ask who each line is said TO. A line said to nobody is exposition; give it a listener in the room or an off-screen voice.
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

### Hosted post is off

Hosted post-production is switched off on the service. Never call `POST /v1/video-generations/{id}/post-production-runs` and never pass `--api-captions`. An answer of `409 hosted_post_off`, or the older `503 restate_unavailable` from a post-production call, means finish the take locally with `fictora-produce finish --desk D`. It does not mean the service is down; do not retry it and do not report an outage. `finish` ends with `Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`; `NOT DONE` (exit 5) is not a deliverable.

### Change a character's voice (never regenerate the story)

A voice that "feels off" is a voice change, not a new story and not a new video. The voice is a lock on the cast card. Audition candidates on the character's real lines (`fictora-produce voice --desk D --cast NAME --audition`), or on new wording in the voices the human names (`--text "…" --voices A,B`: sent to the server as they are; an unknown voice or wording over 300 characters is refused by name before anything is spent), the human picks (`voice --pick N` or the voice's name), re-voice the filmed takes where that character speaks (`revoice --desk D --cast NAME --episode N --take tK`), then finish again (`finish --take-file <revoice file>`). On Turbo (the default) no take carries the locked voice, because voice references are not sent, so revoice takes filmed after the pick too; only on R2V do takes not filmed yet use the new voice as they are. Only when the dub does not sit (lips visibly wrong, a shouted line), re-film the takes where that character speaks, with a cause and a stated cost. Never the other takes, never the story.

### Prompt policy

- The compiled video prompt and the compiled image prompt behind every plate and board are proprietary and stay on the server. The shot description on the spine is yours to read.
- Never fetch, save, print or quote `GET /v1/jobs/{id}/provider-spec`. Operator tokens get `403 provider_spec_internal_only`.
- Record every job id (draft, cast, board, video) in `run-notes.md`. A take whose prompt must be looked at goes to engineering as its job id. Learnings name job ids, never prompt text.

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

Always warn for: a finished episode longer than its 15, 30, or 60s band; editing product code or settings (never acceptable — propose it to engineering).

Do not warn for: a finished cut shorter than its 15 / 30 / 60 s band after trimming (e.g. 14.2 s; takes still film at the normal clip length); `clip_duration_seconds` in 4–15; `cut_tempo=one_shot`; `caption_style=house`; product hand-off paste; 2×N row boards when every frame sets `board_row`; `PUT .../inner-voice`; voice auditions and pick; `POST .../audio-bed`; product sidechain ducking; `arc`, `brief`, `author`, `memory`, `edit`, `look-frame`, `look`, `look-note`, `redraw-board`, `check-lines`. Those are aligned.

Money warnings:

- "This re-render will cost $0.60 and here is the cause I've identified." No cause, no re-render.
- "This episode is now at $X of a $Y budget." Said when it crosses, not at the end.
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

- The local finish ran on every accepted take. A raw take is never a deliverable.
- Every spoken line is heard, exactly as approved.
- The film reads as one continuous move per take. Forced cuts are softened.
- Each take opens on the frame the previous one ended on.
- Music runs to the last second, ducked under every line.
- Loudness is consistent across takes.
- Captions follow the transcript-vs-translation recipe. House style. On screen only while the line is spoken.
- The last frame is saved as the hand-off for the next episode.
- `run-notes.md` carries the ledger, the faults accepted, and the causes of anything re-filmed.
- Spend is inside the budget, or the overage is explained in one line.

A series is finished when there is also a joined cut of every episode, one music bed, a write-up that names what the product could not do, and the learnings folded back.

The measure of whether this process is working is not whether the films are good. It is whether a good film cost one take.
