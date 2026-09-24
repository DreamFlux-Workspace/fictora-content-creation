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
| `--line-start S` | Seconds where a line starts; once per line, in order. Overrides speech detection |
| `--no-open` | Do not open the result |

Writes, never overwriting:

| File | Content |
| --- | --- |
| `takes/take-ep01-t1-house-vN.ass` | Caption cues |
| `takes/take-ep01-t1-captioned-vN.mp4` | Raw take with captions burned in (audio copied) |
| `run-notes.md` | One note with the files and each line's time span |

## House look

Matches the content team's reference captions (Closing Shift, Sauce Left Over, The Elevator cuts).

| Property | Value |
| --- | --- |
| Text colour | Yellow `#FFE500` |
| Font | Poppins Bold, bundled in `assets/fonts/` (SIL OFL) so every laptop renders the same |
| Size | 50 px on a 1344 px-tall frame (3.7% of height), scaled to the take |
| Edge | Black outline 3, soft 50% black shadow 1, no box |
| Placement | Centred; bottom of text at 70% of frame height (margin 403 px on 1344) |
| Reveal | Flicker: words build up to three on screen, then reset; each line resets |
| Timing | On screen only while the line is spoken; last word holds 0.15 s, never into the next line |

## How timing works

1. **Lines**: episode dialogue from the newest spine snapshot in `ep01/api/` that has beats (`03_spine.json`; approve receipts are skipped).
2. **Speech spans**: `silencedetect=noise=-30dB:d=0.3` on the take. Gaps shorter than 0.12 s are clicks, not speech.
3. **Anchor**: each line starts on the next speech span (silence-end onset) and absorbs following spans only while the pause is under 0.6 s and the line still needs time. Spans after the last line (ambience, a door, the music tail) are ignored.
4. **Words**: spread across the line's span, weighted by word length.

No transcription model is needed: the words are already fixed at the script gate. If detection picks the wrong sound, watch the raw take, note where each line starts, and re-run with `--line-start`.

## Requirements

- **ffmpeg + ffprobe with libass** (`ffmpeg -filters` lists `ass`). macOS: `brew install ffmpeg`; use `ffmpeg-full` only if your build lacks `ass`. `fictora-produce start` warns when it is missing.

## Low-level

`scripts/burn_house_captions.sh <raw.mp4> <captions.ass> <out.mp4>` burns an existing ASS with the bundled font. Prefer the command above, which builds the ASS too.

Do not ask the video model for on-screen text; captions are always post.
