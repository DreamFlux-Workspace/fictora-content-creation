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

An episode is a chain of 15-second takes. A take is one continuous camera move or three to four covered shots, chosen by what the scene is (Shot plan per take, below). Every take opens on the frame the previous take ended on.

The band is season-locked on the spine. Choose 15s or 30s and hold it.

| Duration band | Takes (storyboard sets) | Beats per set | What it suits |
| --- | --- | --- | --- |
| 15s | 1 | 3 | Inner-monologue. One situation, one turn. |
| 30s | 2 | 2, 3 | Two-hander: setup take + payoff take. |
| 60s | 4 | 2, 3, 2, 3 | A full scene with a making/wordless take inside it. |

**Take length defaults to 15 seconds.** `clip_duration_seconds` accepts 4–15. Send 12 for a shorter take. 16 is a rejection. The harness body takes the length you pass. Use `cut_tempo=one_shot` for one unbroken camera move.

Fixed properties of a take:

- A declared shot plan (below). A continuous shot is boarded as the beginning and end of each phase of one camera move; the camera is a little closer in each cell.
- Three spoken lines maximum. A four-line 15s take drops a line, usually the one that carries the story.
- A hand-off frame. Spoken successors paste the prior last frame into cell 1a. A making (wordless) take pastes the next take's opening into its last cell. Prefer ending a spoken take on a wide.
- 9:16. Portrait. 768P on the H3 lane.

### Shot plan per take

Choose the plan from what the scene is. One default for every show is how a romance comes out as three near-identical shots and a horror take as one slow push-in (reviewer notes, 2026-09-25). Set it on the draft (`fictora-produce start --cut-tempo T`), never only on the take, so the board and the take agree.

| Plan | `--cut-tempo` | Use for |
| --- | --- | --- |
| One continuous shot | `one_shot` | Inner monologue, a making or process take, a walk |
| Coverage: 3–4 shots, one per board row | `punchy` | Romance, horror, comedy, any two-hander where a reaction lands |

The H3 row board holds four rows at most, so four shots is the most one take can carry. Never a shot per beat in either plan.

Coverage rules. Check them on the board before the board gate; a board that breaks them is fixed at the frame (`edit --frame … --set …`) and redrawn, never re-filmed:

- No two neighbouring shots at the same size and angle. Across the take: one wide, one face close-up, one insert (hands, an object) or a point of view.
- Every shot names its camera move with direction and size: "tracking backward ahead of her from the door to the counter", "arc a quarter circle from her side of the counter to his", "dolly in onto her face". "Camera holds" only on an insert. A row with a spoken line keeps only `locked`, `dolly_in`, `dolly_out`, `pan_*`, `tilt_*` or `handheld` (lips must stay readable); arcs, orbits, trucks and cranes go on silent rows.
- Write the acting big and physical: what the face and body do ("leans right into the lens, grin enormous, drums her fingers on her cheeks"). Mellow acting renders as a slideshow even with the camera moving.
- The reply gets the listener's angle: a reverse, or across the counter. Never the same two-shot again.
- Horror: never a whole take on one held shot, and never a fixed wide. The scare gets an insert or a point of view; the threat comes toward the camera; the monster's defining behaviour is on screen (SCP-173 moves only when unseen: cut away and back, and it is closer).
- One spoken line per beat, nothing else happening during it (a beat that also carried a drawing and a reaction lost its line).
- End on a wide for the hand-off.
- When the human names a beat's shots, set them on the beat: `edit --desk D --episode N --beat B --shot "size|subject|camera|angle"` (repeat, 1–4; shot 1 is the beat's first row), then `redraw-board` if the board is already drawn. Write the camera move with direction and size there too.
- `one_shot` means no cuts. Each board row still keeps its own camera: one phase of the move per row, not a new shot per row.

Sound and captions are built after the take comes back, never asked of the model:

- One music bed per series, not per episode. Generated beds run about 30 seconds. `finish` loops the bed under the picture; check the music runs to the last second, or the tail loses its music with no error.
- One bed across a join. Takes of one episode are joined under ONE bed, so the music never restarts at a seam.
- Everything that is not the voice is ducked under it, about 8–9 dB, not simply turned down. The mix LUFS says nothing about ducking. `finish` ducks with a sidechain compressor and saves the mix bus files beside the mix, so `review` reports the depth (6–12 dB under the voice); when a line must sit clearly over the bed, pass `finish --duck-db 9` for an exact depth.
- Per-take gain plus one limiter. Never loudnorm on a take.
- The bed sits about 9–11 dB under the dialogue between lines (about −27 dB in the mix). `finish` measures the bed and sets its level for that, unless the desk's `series.json` `bed_db` or `--bed-db` says otherwise; it prints the level and why, and `!!` when the bed would sit near-silent or over the voices. Set `bed_db` once per show when the human wants it louder or quieter; `join` and `reel` read it too.
- `finish` lays the take's own effects from its take facts. Exteriors come back near-silent (−38 LUFS) and get the most from them. A sound the Sound lines missed is a hand cue: [sound-cues.md](sound-cues.md) (one per visible action, shape measured, clamped to its own take).
- Voice-over lines are generated dry and placed on measured beats in post. They are never written into the take as fixture lines. A brief with narrator or voice-over lines is flagged at `start` and before the draft; `brief --desk D --strip-narration` takes them out (before the first `step` it edits the desk's brief; after the draft it replaces the server's stored brief).
- Captions are English only for now. Japanese and Korean captions are deferred. The caption is the line's `subtitle_text`, else its `text`: on a Japanese or Korean show give every line an English subtitle before the script gate (`line --line N --spoken "…" --subtitle "…"`). The caption font (Arial Bold) has no Japanese or Korean glyphs, so a line with no English is left uncaptioned and `finish` / `caption` warn: ``NOT ENGLISH: <line id> "…" — add an English subtitle with `edit`/`line --subtitle` ``. Give it the subtitle and finish again.
- A voice heard, not seen (an `off_screen` line, or a `voice_only` cast member such as an intercom) is captioned in the house face's italic (Arial Bold Italic), same size, colour, edge and place as the house caption: one face per episode.
- Captions: when the caption is the same language as the audio, word-by-word flicker in the safe band. When the caption is a translation, the whole line goes up and holds for exactly as long as that line is spoken. Both are the house style: yellow `#FFE500`, Arial Bold 64 on a 1080×1920 canvas, black outline 5, soft shadow, no box, one face for every line. The kit scales it to the take by height (45 on the 1344-high H3 take), so a kit caption is already house size: no re-burn (a re-burn at 64 on a 1344 take is 1.4x house size). For bigger captions use `finish --caption-scale X`. Text bottom sits at 62% of frame height, inside the social caption band (55–70%); a line too wide for one line wraps onto two balanced lines, and only one too long for two is set smaller. `--caption-style plain` burns white whole lines instead, `none` burns nothing. Flicker builds up to three words, then resets. On screen only while the line is spoken. Times come from the words a saved transcript heard for each line when there is one (any show), else from silence-end onsets; `EXTRA SPEECH` means the take had more speech stretches than lines, so watch the captions. Lines come from the current spine (read from the server), and a line laid with `finish --voice` is captioned in italic where it is laid.
- H3 cell seams are a compiler product. Post detects them with a frame-difference trace and dissolves each seam in place. Do not keep a private `soften.py`.

## The nine stages

This is the order `fictora-produce` runs them in.

```
1 Brief → 2 Draft → 3 Cast plates → 3b Voices → 4 Script → 5 Board → 6 Estimate → 7 Take → 8 Read the take → 9 Finish
                                                                                    ↓ fault with a named cause
                                                                                  5 Board
```

1. **Brief.** One line of premise, the cast, the set, the hook, the lines. If the story needs a fact the picture cannot show, that fact must be spoken in a line. Decide now. Episode 1 is made on its own: no series arc yet (see Series arc). Source material: name its licence and credit (SCP is CC BY-SA 3.0: credit the article's author); never copy the look of an image that is not under that licence. A creature inspired by a reference (a film monster, an SCP, a game boss) is written as a new original design: its own shape, skin and colouring, described in our words, never the reference's.
2. **Draft.** The first `step` writes the story on the API. Compare its lines with the brief before the script gate. The draft writes episode 1 alone (`outline_mode=arc_at_episode_two`, `episode_count: 1`); later episodes are written with `author --episode N`.
3. **Cast plates.** One full-length figure plus a bust per character, plus object plates for any prop that must stay consistent, plus a location sheet for a set that recurs. Gate: show the plates, get a yes.
   **3b. Voices.** The draft locked a voice per character and every take speaks in it, so the human hears it before filming. The plates yes prints each speaking character's voice; offer an audition (`voice --cast NAME --audition`, about $0.02-0.05 a character, after a yes), then the human keeps it (`voice --cast NAME --keep`, or `--keep-all`) or picks another (`--pick N`). Filming is refused until every speaking voice in the episode has that yes. The yeses live on the server (`spine.voice_approvals`), shared with the creator app; a desk's old `shared/voices/approvals.json` yeses move there on their own, and the desk file is read only when the server is older or unreachable (the kit prints a note). A desk that filmed before this gate is grandfathered. The gate applies only to a show whose voice mode is `locked`: a show whose earlier episodes were filmed with the video model's own voices keeps them (`voice mode model`, no gate) unless the human switches it (`voice-mode --desk D [--set locked|model]`; ask at the start of every new episode on such a show: "Keep the same voices as earlier episodes (recommended) or switch to locked voices?").
4. **Script.** Beats and lines per take, three lines maximum. Gate: the lines, in the original language, with the translation, and every line the writers changed from the brief.
5. **Board.** The storyboard mosaic. Measure mean luma and report it with the board; a dark board is the human's call, not a fault. Gate: the board image itself, checked against the board checks below.
6. **Estimate.** Price the batch before enrolling it. The line always names the lane and its $/s. If it starts with `!! SERVER ESTIMATE FAILED`, the server gave no dollars and the kit priced from its own table; `!! SERVER ESTIMATE DOES NOT ADD UP` means the server's own parts (rate x seconds, video + stills) disagree with its total and the higher is shown. Tell the human either before they say yes. The server prices on `priced_on`, fal's billing day (San Francisco time), and the kit trusts it: on a rate-change day the morning in India is still the old rate.
7. **Take.** The only expensive call. Everything above exists to make this call succeed once.
8. **Read the take.** Measure first: transcript, cut count, loudness, exposure. Then compare against the board. Write down every fault, including the ones you will not fix.
9. **Finish.** Local, on the laptop: `fictora-produce finish` (sound effects from the take facts, the show's music bed, colour match to the board, mix near −18 LUFS, captions, the mark; on a locked-voice take also the location's ambience, one cue per episode at about -28 dB between the lines and continuous across the takes' seam (room tone only when no cue can be made), the bed and the ambience ducked in each line window and under every laid voice line or thought, and captions from the line windows), then the join. A take is not done until the finish ran. A raw take is never a deliverable. That holds for everything the human is shown, not only the delivery: a preview, a rough cut or a "just combine these" file is made from finished takes and carries the house captions.

The gates at 3, 4, and 5 are not optional and are not batchable. A wrong face at the plate stage costs $0.30. The same face after the take costs a re-board and a re-take ($0.60 through 30 Sep 2026: $0.30 board + $0.30 Turbo take; $0.90 from 1 Oct). On Turbo the board is the only picture the take gets, so a wrong face on the board is a wrong face in the take.

## How the loop runs

Run the kit from bash (or zsh), never PowerShell: PowerShell breaks the quoting in `--set '…'`, `--note "…"` and JSON arguments, and the server gets something else.

The agent stops after every step and waits. It does not chain two stages. It does not decide a step was good enough.

Confirm after each step, before the next one. Draw the plates → stop → report what was made and where it is → wait for a yes.

The folder is the review surface. The report says the path, not just the verdict.

Never overwrite silently. A new version is a new file: `board-ep01-t1-v2.png`. Rejected versions stay on disk.

When a step fails, the desk says `failed` and names the stage. Fix the cause, then `fictora-produce retry-step --desk D --cause "…"`: it backs up `production.json`, puts that stage back with a fresh key, says whether re-running it is paid (boards $0.30 each), and sends nothing. The next `step` re-runs it. Never hand-edit `production.json`.

Slow is not stuck. While a job runs the kit prints its status every poll; when the job's `updated_at` and progress have not moved for 10 minutes it prints a `WARNING: The server has not updated this job for N min (last update HH:MM UTC)` line, and again every 10 minutes (a film's warning starts `video job job_video_…:` and also watches the take jobs and clips; a film stops at once when `/v1/video-generations/{id}` says failed, even while `/v1/jobs` still says `running 0%`). Check `fictora-produce status --desk D`, tell the human, and with their yes stop it with `fictora-produce cancel-job --desk D --job-id <id>`. The kit never cancels or retries on its own. When `cancel-job` says the cancel was requested but the job has not stopped yet, steps already started on the server may keep running for a while (a server fix is in progress): it reads the job once more after 15 s and says where it stands. Do not cancel again and never enrol another take while it runs.

After a `cancel-job`, run the stopped command again: a redraw, `author` or `rewrite` whose saved job was cancelled or failed starts a fresh job under a new key and says so (a redraw re-sends its note). A job still running, or one that completed, is picked up and never sent twice.

Ask for the merged cut whenever an episode has a second take. Joining costs nothing. One music bed across the whole thing. Soften every seam. Assert 24 fps. Check loudness across each seam — a 5 dB step is audible. The merged file is a new file. Individual takes stay on disk.

## Spend

Lane check: takes on the `minimax-h3` lane film on **H3 Max Turbo image-to-video**, `minimax/h3-max-turbo/image-to-video`, at 768p. That is the server's default; H3 Max reference-to-video (`minimax/h3-max/reference-to-video`) is an engineering-side switch on the deploy, not something the desk picks. The estimate's `cost_estimate.video_endpoint_id` and each take's facts say which one filmed; price and quote that one.

On Turbo the take is image-to-video from the take's whole storyboard board: the board is the first frame and the only picture the model sees. Cast plates and voice references are not sent, so the board carries the faces and the look, and the raw take speaks in the model's own voice. Once the server sends the locked voices as the take's audio (take facts `soundtrack: target_audio`, printed as `Soundtrack: locked voices`), the raw take speaks in the locked voices instead, with digital silence between lines and no ambience or effects: `finish` then lays the location's ambience (made once per episode from the spine's frame location and the take facts' sustained sounds, about -28 dB between the lines, ducked under each line, cached in `epNN/ambience/`; room tone only as the fallback, with a note), the bed ducked in each line window, the effects on the measured cuts and captions from the line windows by default, and stops (exit 5, nothing deliverable) when it has no bed. Never revoice such a take. A raw take can open on a frame or two of the board grid; `fictora-produce finish` replaces them first (`deboard`, measured against the approved board, length and sound unchanged).

| Unit | Cost | Notes |
| --- | --- | --- |
| Cast or object plate | $0.30 | Per image. A character needs two (full + bust). |
| Storyboard | $0.30 | Per board, first draw or redraw. |
| Take (15 s, H3 Max Turbo I2V, 768p), the default | $0.30 through 30 Sep 2026; $0.60 from 1 Oct 2026 | $0.02/s (fal promo), then $0.04/s from 2026-10-01T07:00Z (fal's day; 12:30 IST). A 12 s take is $0.24 / $0.48. No image charge. A take under 5 s films and bills 5 s. |
| Take (15 s, H3 Max R2V, 768p), engineering switch only | $1.20 | $0.08/s. Plus $0.02048 per reference image past four (per take: the board plus a plate for each named person in that take, at most nine). Quote only when the estimate or take facts name it. |
| Voice audition set | about $0.02-0.05 (Eleven v3 per character of the lines × voices; the kit prints the quote) | Candidates on the real lines. Once per character; a second set needs a written cause. |
| Voice line | $0.10 per 1,000 characters | |
| Music bed | ~$0.10 | Once per series. |
| SFX cue | $0.002 per rendered second | Only for takes that come back quiet. |

Budgets are **warnings, never a hard stop**: first 15 s episode of a new series **$5.50**; continuing 15 s **$2.50**; continuing 30 s **$5.00**; continuing 60 s **$8.00** (`creation/prices.py`). A first 15 s episode filmed once is about $1.90 on Turbo (plates, a board, a take, cues; $2.20 from 1 Oct, $2.80 on R2V), so the budget covers redraws and re-films. Say "$X of $Y" when an episode crosses its budget, not at the end. Past twice the budget, say so and the human decides whether to go on.

"Change this" is billed. A retry must reuse the same idempotency key. Two jobs for one take means that key was missing. Report it.

## Craft rules that cost money when broken

### Series arc and episode 2 on

- Episode 1 is made on its own. Do not outline four or five episodes up front, and do not ask for an arc at the start.
- The arc is chosen at episode 2, after episode 1 is approved. Ask the human for the intended run (how many episodes) first; the arcs are written to carry that many.
- Check each arc can carry the run. For a long run prefer an engine (a situation that repeats with a new problem) plus a slow question. Refuse an arc that closes within a few episodes (a 25–30 episode run was once offered a one-week mystery).
- Each later episode is steered by its **direction**: the human picks one of the offered directions or says their own, and it reaches the writer in their words. Confirm the idea is in the printed script. Series-wide memory is for standing rules ("keep every episode punchy"), never for one episode's idea.
- Each episode's script is approved on its own. Never pre-stage scripts for later episodes.
- Write episode N+1 from the last ~5 s actually filmed and delivered (watch the delivered file), not from episode N's script: a take that drifted from its script hands off what was filmed.
- An authored episode cannot be authored again: a second `author` for it is refused (`409 prior_episode_not_approved` / `invalid_extension_ordinal`). Before the script yes, re-write it with `rewrite --desk D --episode N --line "…" [--title T]` (episode 2 on, free): the line becomes an episode note that replaces the last `rewrite` direction, the server re-writes the episode from its notes, and the script prints as after `author`. Read the printed script closely before the script yes; after that, change it only with `edit` and `line`.
- Commands: `fictora-produce arc --desk D --list [--episodes N]`, `arc --pick K`, `brief --desk D --episode N` (3 on), `author --desk D --episode N --direction K | --line "…"`, then `approve --gate script` and the usual loop. The run length is a soft default; the season continues past it.
- The kept arc can be changed (`arc --pick` again) until episode 2 is written. After that the route answers `409 series_arc_not_open`: the arc is fixed, steer later episodes by their direction instead.
- Episodes are found by ordinal, never by a typed id. The server names every new episode `episode_NN`; a story made before 2026-09-28 keeps `ep_02` and on for its later episodes. Every kit command resolves both, and the server's spine routes accept either form.

### The hook: the first three seconds

- Frame 0 is mid-motion on a face: a hand already moving, a head already turning. It is the cover frame. Never an establishing wide, never a still.
- The first line lands by about 0.5 s.
- A silent opening is allowed when the picture carries the hook (power shown, an emotional close-up, an impossible image). Otherwise the first line is 10 words or fewer, with no backstory.
- The premise's reveal (the thing the episode is about) is on screen or said by about 3 s, after the commitment. When it must land by 3 s, keep the first line to about 2 s (a 4-second first line put episode 2's reveal at 4 s).
- Lines 10 words or fewer; a take about 22 spoken words or fewer. The last beat lands one new fact, never a fade (a quiet genre may close softly). Never a line to the viewer, except in a POV episode: a character speaking to the camera as if it were the viewer (a brief that opens "POV: …") is the story and stays; a call to action or a question about the show never does. At most one spectacle beat per take; none is fine.
- The word counts come back from the server as authoring notes (fictora-drama #538): `note: beat 3 line runs 14 words (10 recommended) — …`, printed after the draft, `author`, `line` / `edit` and a cascade preview, and added to `run-notes.md`. They never block and the kit never shortens a line: tell the human, the creator decides. Pacing comes back the same way (fictora-drama, 9 Oct 2026; Hana L-20260930-9, Gallery L-20261008-23): `pace: Take 2, beat 3: a silent moment with no movement plays for about 3.5 seconds. …` when a beat with no line holds still past about 1.5 s, and `pace: Take 2: about 3.5 seconds pass between the line on beat 2 and the next one on beat 4. …` when the next line comes more than about a second after the last one ends (one of each a take at most, estimated from the plan, free). Read them to the producer before boards: give the moment an action, bring the line in sooner, or keep it.
- Check it in the brief, the script and the board, before the board gate. A board that opens on a wide or a pause is redrawn: say it with `redraw-board --take tK --note "opens on a close-up of …"`, or fix the frame (`edit --frame … --set …`) and then `redraw-board`. A weak open is never fixed by re-filming.

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
- There is no face detector. The boards `step` lists a frame whose written placement could put a face or prop in a zone (`look at row R cell C: …`, never `!!`: written words are not a finding). Look at that cell and at every other cell. On the finished take `review` measures the caption box (sampled frames and every cue of the burned `.ass`) and gives each finding its times. A face or key prop in a zone: fix the frame's placement with `edit --frame`, then `redraw-board`.
- On the finished take, `fictora-produce review --desk D --episode N --take tK` measures the caption box on sampled frames against these zones and the 55–70% band, and writes a zone sheet (`<take>-zones-vN.png`, zones shaded red) for the face check. It warns and never blocks; faces are still the human's look.

### Expression library

The writers pick an anime expression (`reaction_kind`) for every emotional moment, on any cell. The creator can ask for one on a beat (`fictora-produce edit --beat N --expression KIND`, `none` clears; `expressions` lists the kinds the deploy offers); the beat's anchor row then wears it. The agent checks it at the board gate. A speaker's own way of playing the line is the beat's `delivery`: `edit --beat N --set delivery=whispered` (laughing, excited, whispered, shouted, through_tears, deadpan, trailing_off, breathless, cold, or `null`), checked against the deploy's `/openapi.json` before anything is sent. The kinds and the marks each must show are in the skill's [reference.md](../../.claude/skills/episode-production/reference.md). What was learned paying for it:

- Smiling rage keeps the eyes OPEN with tiny trembling pupils and the too-wide grin: the smile must stay. Drawn with blank white eyes it played as a scowl.
- Gloom draws tatesen lines on a VISIBLE face. A hidden face or the back of a head loses it.
- Pick by the moment, not the genre. Every kind may play in any genre. The symbolic kinds (veins, sweat drops, gloom lines, chibi squash, smiling rage) suit a light, awkward, petty or comic moment, even inside a serious show; real dread, grief, danger or tenderness gets the plainer kinds.
- Whose face: on a cell with two or more people the writers name the face that wears the kind. A listener's expression on a speaking cell plays whole and silent on the listener. Check it on the board.

### Board checks

Check before the board gate:

- The rows follow the declared shot plan.
- **Visible cause.** Every action row is preceded by a visible face reacting to its cause, or shares the frame with it. "Jealousy → breaks the sign" did not read when the face was hidden and the insert showed only hands. When a direction ends on something dying or breaking, write who or what causes it ("the falling beam crushes the lamp", "the cold kills the bird"); left unsaid, the board pins it on the nearest hand.
- **Each row opens on the emotion the row before it ended on.** A calm smile right after the sign broke read wrong.
- **The picture shows the rule.** A change the viewer must see as a jump goes in its own row. H3 blends the cells of one row into one move. A move the beat states (closer, at arm's length) is drawn nearer in the next row that shows it; on a `punchy` horror take the threat gets its own insert or point-of-view row.
- **Speakers.** The speaker is in the frame of the row their line plays on, at a medium shot or closer, or the line plays over someone else's face. No off-screen speaker is drawn: not as a sleeve, a hand, a cane or a shadow. The board shot list prints the line and speaker on each row and warns when the speaker is not drawn there.
- **Speaking mouths.** No clench, grit, pressed or closed mouth on a speaking row. Big physical acting goes before the line and after it; during the words the mouth moves.
- **One person, twice (Turbo).** On the default lane the video model gets one picture, the board, and draws from every panel of it. The same person drawn in several panels (kneeling at the end of row 3, standing at the start of row 4) can come out as two of them in one shot, and look-alike extras read as the same face. The shot list prints `!!` when one character is in the cells either side of a cut in plainly different postures (standing, kneeling, sitting, lying, crouching, as the frame's pose says them; other poses are not guessed at). Warning only: watch that cut in the take.
- The hook, the hand-off, the safe zones, no readable text or digits, brightness reported.

To change a board, say what is wrong: `redraw-board --desk D --episode N --take tK --note "medium two-shot walking down the hallway, waist-up, no map"`. The note is at most 500 characters; a longer one is refused on the desk with its length, and nothing is sent or booked. The note goes through the director (the app's path) and becomes edits to the take's beats; the kit prints them per row, then redraws, and the server re-authors the take's frames from them. A note that changes no shot stops before paying. When the server takes the note itself it may draw nothing (the note needs detail, changed nothing, failed or could not be saved): the kit prints `!!` with the reason and "nothing was charged", books nothing and keeps the old board. For `note_needs_detail` it prints the server's question: ask the human, then `redraw-board --take tK --note "<answer>"`. A note that changed a beat's shot plan is said, and the desk's spine copy is saved again. Or change what it is drawn from yourself (`edit --frame N --set …`, `edit --beat N …`, `look-note`), then `redraw-board … --cause "…"`. The cause is a label only. With nothing changed since the board was drawn (frame briefs, the take's beats, look notes, plates) `redraw-board` stops unpaid with `!!`; `--same-shots` only for a random bad draw with the right frames. A board showing another episode's content is a server bug: report it with the job id. The full procedure is "Fixing a board" in the skill. To change who is in a shot, edit the frame's staging: `edit --frame N --set 'subject_blocking=[…]'` with one entry per person (`cast_id`, `frame_position`, `pose`, `gaze`, `interaction`); the kit sends the whole brief and `cast_refs` to match, then `redraw-board`. To only take someone out, `edit --frame N --set 'cast_refs=["Hana"]'` (the server keeps the others' staging, fictora-drama #498). The server names a refused cast change: `frame_cast_needs_staging` (someone added without an entry), `frame_cast_mismatch`, `off_screen_cast_on_frame` (only heard in this take), `voice_only_cast_on_screen`. On a deploy older than #498 a `cast_refs`-only removal is refused (`subject blocking must match cast_refs`): edit `subject_blocking` instead.

Two server rules no error names yet: every frame on one board shares one location string, and a line cannot move from one beat to another. Write the brief and the edits to fit both.

Repurposing an episode (a new idea on an episode already authored): update its title and summary first, then redraw (the kit has no command for them yet: ask engineering; backlog). A redraw re-authors the frames from the beats and the summary, so an old summary pulls the old story back into the board.

### Characters and age

- Characters may be any age. Write the real age ("10, primary-school kid", "sixteen") and the plate draws that age. Some presets still describe adult proportions, so a child may be drawn older; Meadow Hour (`slice-of-life`) is the natural fit for kids.
- A plate or board with a child may be refused by image moderation more often. Report the refusal to the human with the job id; do not retry blind.
- The one hard line: never romantic, sexual, suggestive or fan-service framing of any character under 18, and never a romance arc for them (no love interest, crush, dating, or someone else's romance aimed at them). The server rejects a breach as `minor_in_romance_arc`. A child is also never in a romantic, sexual or intimate scene, not even as a bystander (producer rule, 1 Oct 2026): the server refuses a beat or frame with someone under 18 on screen and intimate staging as `minor_in_intimate_scene` (authoring, spine edits, boards, estimate, film; nothing paid), and the kit names the scene, the child and the fix: move the child out of that shot, or change the scene. Never age the child up to keep it. PG staging (no kissing, embracing or face contact) applies to everyone. Asked for a minor in a romance, say no and offer the character at 18+ or the relationship as non-romantic.
- Children on camera (P0; a 6-year-old's fall was filmed legs-up): the camera is at or above the child's eye level, never below. Falls and landings are feet-first or seated with legs together. Leggings or shorts under coats and dresses, written into the plate and the frames. Check the take itself for all three, not just the board, before captioning: a take that breaks one is not captioned or shown; stop and tell the human.
- An unclothed creature's plate (Sighted, L-20261001-3: refused as sexual): on new shows the server adds plain, non-sexual body wording to the plate by default ("sexless and smooth like a store mannequin", no genitals, nipples or other sexual anatomical detail, nothing suggestive), for any non-human cast member whose card says it wears no clothing. The art style is never renamed. Do not write that wording into the look yourself. A continuing show (created before 6 Oct 2026) keeps its old plate prompt: if one of its creature plates is refused, report the job id to the human and engineering; do not retry blind.
- **First-person (POV) shows (new shows).** A brief that opens "POV: …" is the viewer's eyes: nobody in the cast holds the camera unless the brief names whose eyes it is ("POV: Mira's eyes", "through Mira's eyes"), and the speaker is drawn in front of the camera. On any show the server never makes a creature, an antagonist or the only cast member the camera holder unless the brief says so (NOCLIP, L-20261006-2: a monster was the camera, every POV shot drew its claws). A POV character seen only as hands (the brief names them as the POV, their card says "POV" / "first-person" / "only their hands are seen", or every frame stages them as hands) gets a hands-only plate: hands, forearms and what is worn at the wrists, no face, no figure, never the other character's look (Not Home, L-20261006-12). Their POV frames ban their face, back, shoulder and hair, and an over-the-shoulder view of them; the board picture check prints `!! picture check: tK board: row N: the shot is NAME's own POV, but …` when their body is drawn anyway (L-20261006-18): look at that row before approving. Continuing shows keep the old rule.

### Off-screen voices

- Declare an off-screen voice in the brief's cast table as voice only. It is never drawn. With every line off screen and no frame showing them, the server flags the card `voice_only` and draws no plate; `plates/` and the plate count leave them out. If a plate for them turns up, a frame puts them on screen: fix the frame or accept the plate before the spend.
- An off-screen voice that plays over another character's face is heard as that face speaking. Give it a source in frame (a wall grille, a phone in a hand) or a source treatment in post (band-limited intercom).

### Background shouts

- Background people (a soldier, kids, a vendor) may shout one short line: at most 6 words, at most 2 per beat, in a generic crowd voice. They are never characters: no card, no plate, no voice to approve, never counted as speakers or toward the three lines a take holds. The script gate prints them beside the beat's line as `[background] Soldier: "Charge!"`. Change one with `line --shout sN --text "..."`, drop one with `line --remove-shout sN` (`line` lists the numbers). Who shouts cannot be changed: someone who really talks is a character, so drop the shout and `line --add` the words to someone in the cast.

### Language

- A Japanese or Korean line must sound like a native speaker in that situation. The situation picks the set phrase (staff to customer: 申し訳ございません / 정말 죄송합니다, not ごめんなさい). No English quip carried word for word. No notice-board noun stack in speech (「逆襲中止！」 "counterattack cancelled!") unless the character really is announcing.
- A dialect (Kansai-ben, Busan satoori) needs a native speaker's yes before the script gate. With no one to check it, write the standard language.
- A Japanese- or Korean-voiced show starts with `start --language ja` (or `ko`), so `spoken_language` is right from the draft. A show drafted in the wrong language is changed with `language --desk D --spoken ja` (read what it sets aside before the yes; takes filmed in another language keep their audio).
- Pin the exact performed line so localization never rewrites it. Compare every draft line with the brief and show the human any line that changed.

### Look and medium

- Only an image sets the medium. A reference frame as the first image decides ink or photography.
- `--preset-id` is required at `start` even when the look will be our own frame. `uv run fictora-produce presets` lists the published ids. `start` checks the id before it makes the desk; if an older kit left a desk that was made but never bound, `start` prints the exact `bind --desk …` command that finishes it. Pick the nearest: the horror preset (Dead Light) is a draft and cannot be picked yet, so horror takes Cold Gate (`modern-dark-fantasy`).
- A pinned look frame wins over the preset: the preset keeps only its shot composition, and its world, palette, finish and reference images leave every drawing. Pin it before the plates; anything drawn earlier still carries the preset's world (meadows, baskets, a white cat). Without a look frame, steer that world with look notes (`look-note --desk D --add "indoors, an arcade; no meadows"`, at most five) before the plates are drawn.
- Say what you want. Never name what you don't. "No mirrors" puts mirrors in the frame.
- Every unnamed extra (a new crew, guards) gets a written look of their own in the brief or the redraw note: a different face, hair and build, their own stencil or name. Left unwritten, the board draws one shared face (episode 2 drew the dead lead's face and stencil on the new crew) and the video may show the same person twice. Describe extras by look in a note, not by designation ("D-7", "guard 2"): naming them only works once the server's unnamed-figures field ships.
- How many people: a series holds up to 50 named characters; unnamed background people never count. One shot shows at most 4 people. One take has at most 8 named people, and at most 6 of them speak on H3 Turbo (the default) or 3 on H3 Max and Seedance; the server's writer splits takes to stay inside these. When a guest's arc ends (they die, move away, are done), mark them as gone: `fictora-produce cast-exit --desk D --cast NAME --after-episode N`. The next episodes' writers then see one line for them instead of their whole card. `--clear` brings them back. Free.
- Give every named character a clearly different outfit and silhouette, so no two read alike at a glance. Avoid stock names the writers reach for ("Mara Voss"); pick a name of your own.
- Extreme close-ups of eyes or skin drift photo-real on a drawn look unless the row's note names the show's style ("flat anime, clean ink lines, no photographic texture").
- Redraw notes (`redraw-plate --note`, `look-note`, a frame edit) describe shapes, never judgements: "short, round and fat, two clumsy pleats", not "crooked".
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
- After an exit, name the destination and put their back to the portal. "Two steps toward the desk, back to the mirror" is an exit. "Back to the camera" faces the glass they just left.

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
- No digits and no readable text anywhere. Jackets and uniforms say "plain back, no lettering or logos". Watch the end of every take for invented text: it shows up most in the last seconds.
- A sign the story needs (its words in the take facts' `story_signs`) that the video garbled: `finish` names it and the shot; with the human's yes, `finish --sign-overlay` draws the exact words over it instead of a blur.

### Dialogue

- Three lines maximum per fifteen seconds. A 15 s take is three beats, so a silent comic beat (a pause, a stare, a freeze) costs a line. Say so at the brief.
- Ask who each line is said TO. A line said to nobody is exposition; give it a listener in the room or an off-screen voice.
- To lock lines word for word, write "Keep these lines exactly as written." in the brief's Lines section, with each line's beat and take filled. The server puts back a locked line the writers trimmed or reworded; a dropped, moved or translated one is still caught at the script gate. A locked line that cannot fit pauses the draft for the creator (nothing drafted): edit the brief together, `bind --prompt` the edited brief (or `brief --edit` it) and `step`. The edited brief is always a new draft; `step` on the unchanged brief only shows the pause again and pays nothing. **Script contract (fictora-drama, 9 Oct 2026):** the brief's Lines (or Script / Dialogue) section is the approved script even without the lock sentence: after every first draft the server puts back its trimmed, reworded, moved or dropped lines and their speakers (a dropped minor speaker's short call-out comes back as a background shout), on every show and language. The lock sentence still adds the pause for a line that cannot fit. Whatever the draft still says differently prints after `step`/`author`/`rewrite` as `SCRIPT: …` with the `line` command that puts it back: ask the human, never fix it silently.
- A line needs its own cell with no competing business.
- Never write a character speaking with a full mouth.
- Direct volume as clear and audible. "Barely audible" renders at −50 dB.
- A line missing, said wrong, repeated, invented, or on the wrong shot on a take filmed in the video model's own voices: `finish` stops before making anything and prints the re-film command (founder decision, 8 Oct 2026; L-20260924-10). Re-film that take; do not patch an on-screen line with a laid voice line (it breaks lip sync). Only a line heard off screen may be laid by hand, as the human's choice. `finish --accept-line-mismatch tK` delivers it anyway (logged).
- A gap between spoken lines can render as garbled fake speech. Do not re-film for that and do not mute the range: a muted range leaves a hole in the background (the take's own room sound drops out). Drop the take's audio and rebuild it: the voices as dry lines, the effects, an ambience cue and the bed (skill, "Hand sound"). A locked-voice take needs none of this: its audio is only the locked lines, and `finish` lays the ambience and bed.
- A character's first speaking episode needs its own voice audition on their own real lines.

### Light and sound

- Measure the board's brightness before you look at it as a creative choice.
- A scene lit only by a red emergency lamp renders at about half the readable brightness. Plan to lift it afterwards.
- Never use a broadcast loudness pass on action. Per-take gain and one limiter.
- Music and effects are compressed under the voice, not simply turned down.
- Check that a generated sound effect actually contains a sound.
- A generated music bed is about 30 seconds. Loop it for anything longer.
- The music is the harness's: never pin a file or write a music brief. Say what should change ("calmer", "quieter under the lines") with `fictora-produce music-note --desk D [--episode N] [--take tK] "calmer"`. Never name a composer or a studio in a note.
- `music-note` sends the note to the harness as a dry run first and prints the plan: what kind of change (level only is free; new music is one new bed, about $0.20), which takes re-mix free and which can only change by filming again, with their price. Read it, then run the same command with `--yes` to apply. Nothing is changed or spent without `--yes`.
- Takes whose music the video model made change only by filming again. That needs `--confirm-refilm` as well as `--yes`, after you have seen the total. Without it those takes keep their old music.
- Undo a change with `music-note --desk D --revert N` (then `--yes`). Notes saved with `finish --music` or `--save-only` go with `music-note --desk D --send-saved` (plan first, `--yes` applies).
- When the human says the music feels off in kind (too safe, wrong genre, never tense), offer the show's music blend: `fictora-produce music-blend --desk D` reads it, `--set romance,suspense` changes it (free; up to three families), `--default` goes back to the genre's. Only takes filmed afterwards play it; never ask about it at show start and never hold filming for it. A desk created before 6 Oct 2026 keeps its music: no blend line, and a change is refused.
- An existing show (desk created before 6 Oct 2026) keeps one source of music all season (founder decision, 2026-10-08); a newer show is scored fresh every episode and has no lock. `music-lock --desk D` says whether `finish` lays the show's bed or the harness's music is in the take, and why. A new take with the harness's music baked in on a show whose bed is laid in finish is re-mixed on its music-free stem under the show's bed, no re-film. Only a take whose music the video model made (no stem) is NOT DONE: re-film it (a `finish` show's film runs send `music_by_finish`), or change the lock on purpose with the human's yes: `music-lock --desk D --set in-take --reason "…"`.
- A note that was applied is never sent again; sending it again after a dropped connection is the same request. `blocked` means the harness has provider spend off: nothing changed, send it later.
- After applying, wait for `the delivered cut changed`, then re-download the episode. The take files on the laptop still have the old music.

### Hosted post is off

Hosted post-production is switched off on the service. Never call `POST /v1/video-generations/{id}/post-production-runs`. New desks default to **`api_captions: true`**: the server finishes each episode (concat + house captions) after filming; prod runs that step on the Modal media worker (~30–60 s). The desk still downloads the **raw** take; **`fictora-produce finish`** on the laptop remains mandatory for music, SFX, mix, the show's Bold/Subtle captions, and the mark. Use **`--no-api-captions`** at `start` or `bind` only when the human wants raw-only enrol. An answer of `409 hosted_post_off`, or the older `503 restate_unavailable` from a post-production call, means finish the take locally with `fictora-produce finish --desk D`. It does not mean the service is down; do not retry it and do not report an outage. `finish` ends with `Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓`; `NOT DONE` (exit 5) is not a deliverable.

### Change a character's voice (never regenerate the story)

A voice that "feels off" is a voice change, not a new story and not a new video. The voice is a lock on the cast card. Audition candidates on the character's real lines (`fictora-produce voice --desk D --cast NAME --audition`), or on new wording in the voices the human names (`--text "…" --voices A,B`: sent to the server as they are; an unknown voice or wording over 300 characters is refused by name before anything is spent), the human picks (`voice --pick N` or the voice's name), re-voice the filmed takes where that character speaks (`revoice --desk D --cast NAME --episode N --take tK`), then finish again (`finish --take-file <revoice file>`). On Turbo (the default) a take whose facts say `Soundtrack: native` (or say nothing: an older server) does not carry the locked voice, because voice references are not sent, so revoice those takes filmed after the pick too; only on R2V, or on a Turbo take filmed with `Soundtrack: locked voices` after the pick, do takes use the new voice as they are. A locked-voice take filmed before the pick is the one locked-voice case that needs `revoice --over-locked-voices`; the kit refuses a revoice, `--mute` or `--voice` on a locked-voice take without it. Only when the dub does not sit (lips visibly wrong, a shouted line), re-film the takes where that character speaks, with a cause and a stated cost. Never the other takes, never the story.

A one-word line the transcript missed ("Blinking!") is revoiced on its planned window from the take facts, and `revoice` says it did: listen before using it. A short line that reads better than it sounds can be a caption only: `finish --caption-label "Blinking!@A-B"`.

### Prompt policy

- The compiled video prompt and the compiled image prompt behind every plate and board are proprietary and stay on the server. The shot description on the spine is yours to read.
- Never fetch, save, print or quote `GET /v1/jobs/{id}/provider-spec`. Operator tokens get `403 provider_spec_internal_only`.
- Record every job id (draft, cast, board, video) in `run-notes.md`. A take whose prompt must be looked at goes to engineering as its job id. Learnings name job ids, never prompt text.

### Choosing sound

You pick the sound. You do not re-roll it. Voices are auditioned then locked. Music is chosen from two or three beds, then reused as the series bed. Effects are generated per take that came back quiet, then levelled. Almost every sound note is a mix note. Say the level, not the verdict.

## Reading a take

Measure first, then watch. `fictora-produce review --desk D --episode N --take tK` measures it on the laptop for free (loudness, cuts against the take facts, frozen and stacked frames, board frames, every approved line, and safe zones on a finished file) and appends one block to `run-notes.md`. Paste that block into the verdict. Its thresholds and how to read each ⚠ are in the skill's [reference.md](../../.claude/skills/episode-production/reference.md) ("Review: the numbers"); do not restate them from memory. A ⚠ is something to look at, never by itself a reason to re-film.

| Check | What good looks like | If it's wrong |
| --- | --- | --- |
| Transcript | Every line heard, exactly as written, with timing | A missing line is the one real re-film. A stray mumble or wrong words: drop the take audio and rebuild it (Dialogue), never mute a range. |
| Cut count | Matches the declared shot plan (0 for `one_shot`, one per planned shot change for coverage) | Forced cuts at line boundaries are expected. Soften in post. |
| Loudness | About −15 to −20 for a dialogue take | Below −30 is effectively silent. Build cues. |
| Brightness | Roughly in the band the board was in | Dim sections get lifted afterwards. |
| Beat-by-beat vs board | Every beat present, in order | Missing beats are a direction fault. Write the cause before spending again. |
| People per shot | The head count the take facts give per shot (`review`'s People section; newer servers only) | On Turbo the model films from the board picture, so a person drawn in two panels can appear twice at once, most often at a cut where they change pose. Re-film with the cause "same person rendered twice at shot N". |

The automatic scene detector does not see this model's cuts. Compare consecutive frames. The automatic transcript sometimes stretches a syllable across several seconds. That is a mis-assignment. Anchor the caption on a later word.

A take that will be re-filmed (Change this, with a cause) is not finished: do not run `finish`, cues or a voice line on it. Spend and time go to the new take.

Faults accepted without a re-film (calibration): a door frame in the opening second; a torso slightly more toned than the plate; a bag drawn twice for one second; an emergency lamp staying lit; small stickers on a car window; an elbow tap that did not read; a character walking with his back to camera for three seconds. Write them in the notes. Leave them.

## Finishing order

- The only automatic edits to footage are the board frames: `deboard` holds the first real frame over the storyboard frames a take opens on (never cuts, and never where the server left them as filmed: then it asks), and `finish` cuts at the server's automatic trim handles, just past the board frames the server held (fictora-drama #563; the server's own join cuts there too). Any other trim or cut of footage, including `trim --start/--end` handles, needs the producer's yes first, and the reply shows what was cut (the times and the frames either side).
- `finish` the raw take first: its effects, hand cues and hand lines are placed at the times as filmed. Then `trim` or `tempo` the finished file, never the raw take. After a trim, check the first frame after the cut at full size (a 0.04 s miss once flashed the removed shot) and say the new length; shorter than the band is fine.
- An overlay added in post (a title, a sticker, a blur, a hand caption) never covers the key reveal. Render test frames of the key shots with the overlay on and look at them before the full render.
- Keep the un-marked master. `finish` writes every step to its own file; the captioned file before the mark (`…-cap-vN.mp4`) is the master, the marked file (`…-sokii-vN.mp4`) is what goes out. Never delete the master to save space.
- The join (`fictora-produce join --desk D --episode N`, or `--episodes 1 2 3` for a series cut): one bed across every take, 24 fps, and the level step at each seam under 5 dB (a 5 dB step is audible; over it the join says `NOT DONE` and exits 5). In a series cut the episode seams are levelled first (the last and first 4 s of the two episodes move toward each other, at most 10 dB together). Place cues on each take before the join, never on the joined file. Never join the marked `-sokii` files by hand: that stitches two beds and two marks.
- The reel cut (`fictora-produce reel --desk D --episode N`, after the episode is accepted): a separate IG / TikTok edit cut from the footage already rendered. Local, $0, no new video and no server route. It opens on the strongest frame as a short flash-forward and ends on the new fact; it trims the calm setup and keeps one pivot; captions are the accepted ones re-timed through the cut; no line to the viewer and no end-card question (the call to action goes in the suggested post text). It writes only under `reels/` and never overwrites. Detail: reference.md, Reel cut.
- `join` reads the record `finish` leaves beside each take. A take finished before the kit had `join` has no record: run `finish` on it again first. A take cut with `trim` or `tempo` after `finish` carries a new record (the same cut on the take before the bed and the master), so it joins at its new length; an edit that prints `No finish record` is refused by `join`: finish again, then edit. A re-captioned copy of a finished take joins with `--take-file COPY --from-record <its -cap master>`: same size, frames and sound are checked, and the stand-in is written in the run notes (L-20261001-9).

## Scratch tools

Never copy a desk folder to experiment: the copy keeps the same `spine_id`, one story on the server, so an edit, redraw or film on it changes the real desk's story. The kit stops any story-changing or paid command on a desk whose `spine_id` another desk beside it (or under the default desks folder) also names, and lists that desk; `--shared-spine-ok` lets it through when sharing is meant. Reads and local post are never stopped.


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

If a desk already exists for that date and slug, `start` and `init-series` refuse it. Never delete, rename or overwrite it, and never pick another parent folder to get round it: ask the human whether to continue that desk (`fictora-produce status --desk D`) or name the series differently. Never move or rename a desk folder after it is made either.

Every delivered episode is also copied (never moved) into the series' `<Series> - finished episodes` folder, so the human has one place for what went out.

```
run-notes.md
reference/     source crops and every hand-off frame
plates/        cast, prop, set + contact sheets
boards/        every version kept
fixtures/      beats, lines, camera
voices/        auditions, pick, generated lines
sfx/  beds/
takes/         raw, mix, captioned final, joined episode
reels/         social reel cuts, their plans and post text (`reel`)
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
