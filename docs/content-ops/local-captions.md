# Local captions (house recipe)

Use this when `production.config.json` has **`api_captions: false`** (default). The API returns a **raw scene MP4** (`ep01/api/17_raw_scene_clips.json`, downloaded to `ep01/takes/take-ep01-t1-raw-vN.mp4`). Captions are burned on the operator's laptop. ffmpeg never runs on Railway for this.

## One command

```bash
uv run fictora-produce caption --desk <desk>
```

| Option | Use |
| --- | --- |
| `--episode N` | Episode other than 1 |
| `--take <path>` | A specific raw MP4 (default: newest `take-epNN-t1-raw-v*.mp4`) |
| `--line-start S` | Seconds where a line starts; once per line, in order. Overrides speech detection and word timing |
| `--line-end S` | Seconds where a line's caption goes off; once per line, in order. Exact: no hold, no reading minimum. Works with or without `--line-start` |
| `--words-json J` | A transcript of the take (`/v1/transcripts` words) to time whole English lines on. Default: the newest `takes/take-epNN-t1-*words-vN.json` when captioning the raw take |
| `--no-open` | Do not open the result |

Writes, never overwriting:

| File | Content |
| --- | --- |
| `takes/take-ep01-t1-house-vN.ass` | Caption cues |
| `takes/take-ep01-t1-captioned-vN.mp4` | Raw take with captions burned in (audio copied) |
| `run-notes.md` | One note with the files and, per line, when it is on screen and what timed it: `(words)`, `(speech)`, `(manual)`, or a mix such as `(words start, manual end)` |

## House look

Matches the content team's reference captions (Closing Shift, Sauce Left Over, The Elevator cuts).

| Property | Value |
| --- | --- |
| Text colour | Yellow `#FFE500` |
| Font | Poppins Bold, bundled in `assets/fonts/` (SIL OFL) so every laptop renders the same |
| Size | 50 px on a 1344 px-tall frame (3.7% of height), scaled to the take |
| Edge | Black outline 3, soft 50% black shadow 1, no box |
| Placement | Centred; bottom of text at 62% of frame height (margin 511 px on 1344), block inside 55–70% (social safe zones). Never wraps: a caption too wide for one line is set smaller |
| Reveal | Flicker: words build up to three on screen, then reset; each line resets |
| Timing | On screen only while the line is spoken; last word holds 0.15 s (0.25 s after a transcript's last word), never into the next line. A whole English line (show not spoken in English) stays up at least max(1.2 s, 0.3 s a word), extended forward only, never into the next line |
| Heard, not seen | A line marked `off_screen`, or any line of a cast member the server flags `voice_only`, is set in Georgia italic (ASS style `Italic`, not bold): same drawn size, colour, edge and place. libass sizes a face by its full line height, so the `Italic` Fontsize is scaled (32 against Poppins' 50 on 1344 px) to draw at the same em. Georgia is a system font (macOS ships it), not bundled; `setup-check` warns (⚠) when Georgia Italic is missing, and a render that falls back prints `WARNING FONT: …` and notes it in the run notes |
| Not English | Captions are English only. A line whose caption (`subtitle_text`, else `text`) has kana, CJK, Hangul, any other non-Latin letter, or CJK/fullwidth punctuation is timed (it keeps its speech span, so later lines stay put) but not drawn. `caption` and `finish` print ``NOT ENGLISH: <line id> "…" — add an English subtitle with `edit`/`line --subtitle` `` and write it to `run-notes.md` |

## How timing works

1. **Lines**: episode dialogue from the newest spine snapshot in `ep01/api/` that has beats (`03_spine.json`; approve receipts are skipped).
2. **Speech spans**: `silencedetect=noise=-30dB:d=0.3` on the take. Gaps shorter than 0.12 s are clicks, not speech.
3. **Anchor**: each line starts on the next speech span (silence-end onset) and absorbs following spans only while the pause is under 0.6 s and the line still needs time. Spans after the last line (ambience, a door, the music tail) are ignored.
4. **Words**: spread across the line's span, weighted by word length.

No transcription model is needed for an English show: the words are already fixed at the script gate. If detection picks the wrong sound, watch the raw take, note where each line starts and ends, and re-run with `--line-start` / `--line-end`.

### A show not spoken in English: timed on the transcript's words

Speech spans cannot tell a stammer or a mid-line pause from a line: on Hanakaze Sweets ep02 t1 (Japanese) a stammer `も、` at 9.5 s started line 2 two seconds early, and a pause inside line 1 ended it at 1.63 s of 3.05 s. So when the show's `spoken_language` is not English (whole English lines), each line is timed on the words a Whisper transcript of the take heard for it:

1. **Transcript**: `--words-json`, else the newest saved `takes/take-epNN-tK-*words-vN.json` (`review --transcribe`, `revoice`). `finish` asks the server for one when none is saved (`/v1/transcripts` on the take's stored URL, a few cents, saved as `take-epNN-tK-review-words-vN.json` and reused next time); `caption` never does.
2. **Match**: the same matcher `review` uses for "heard" (`creation/post/whisper.py`, `line_windows`), on what is performed (`spoken_text`, else `text`) and every spelling; a Japanese line is matched on the server's per-word readings.
3. **Start**: the first matched word, skipping at the head a word Whisper stretched past what one word can last (over 1.2 s and 0.25 s a character: `もう` over 9.47–11.33 s) and one short leading word (two characters or fewer, a stammer) cut off from the rest by a pause over 0.6 s.
4. **End**: the last matched word's end (a stretched last word is cut to 1.2 s from its start), plus a 0.25 s hold, never into the next line.
5. **Readable**: each whole line stays up at least max(1.2 s, 0.3 s per English word), extended forward only, never into the next line.
6. **Fallback**: a line the transcript did not match is timed on speech spans (steps 2–3 above). So is every line when there is no transcript (and none can be made), when `--voice`/`--mute` changed the take's speech, or when `finish --take-file` is not the raw take the server transcribed. The report says which method timed each line.

Hanakaze ep02 t1 with its transcript: line 1 `0.03–3.30 s` (was 0.03–1.63), line 2 `11.33–13.50 s` (was 9.47–10.43).

**English shows are unchanged**: word flicker on speech spans, and a transcript is ignored (a test pins it). Flicker spreads each English word across its span by length; timing each word on Whisper's words would be better in principle but has not been checked on real takes, so it is not switched on.

## Requirements

- **ffmpeg + ffprobe with libass** (`ffmpeg -filters` lists `ass`). macOS: `brew install ffmpeg`; use `ffmpeg-full` only if your build lacks `ass`. `fictora-produce start` warns when it is missing.

## Low-level

`scripts/burn_house_captions.sh <raw.mp4> <captions.ass> <out.mp4>` burns an existing ASS with the bundled font. Prefer the command above, which builds the ASS too.

Do not ask the video model for on-screen text; captions are always post.
