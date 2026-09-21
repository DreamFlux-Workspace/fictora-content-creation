# Local captions (Drama app / house recipe)

Use this when `production.config.json` has **`api_captions: false`** (default). The API returns a **raw scene MP4** in `ep01/api/17_raw_scene_clips.json` and `ep01/takes/take-ep01-t1-raw*.mp4`. Captions match prod **`caption_style: house`** without waiting on API post-production.

## House look (same as Drama app)

| Property | Value |
| --- | --- |
| Text colour | Yellow `#F5D547` |
| Outline | Black edge, no background box |
| Placement | Centre band — bottom of text ~**62%** of frame height; size ~**1.6%** of frame height (whole-line preset) |
| Same language as audio | Word-level flicker in the safe band |
| Translation | Whole line holds for the spoken interval |
| Timing | **Silence-end onsets** on the take audio, not raw Whisper word starts across pauses |

Authoritative prose: [runbook.md](runbook.md) (Sound and captions) and [api-map.md](api-map.md).

## Operator steps

1. **Lines** — From `ep01/api/03_spine.json` (or `scripts/` export): episode 1 dialogue bound to the take.
2. **Raw file** — `take-ep01-t1-raw.mp4` or the URL in `17_raw_scene_clips.json` → `clips[0].url`.
3. **Transcribe / align** — Measure the take; align cue times to **silence boundaries** per runbook. If the automatic transcript stretches one syllable across seconds, anchor on a later word in the line.
4. **ASS** — Generate house flicker ASS with the same compiler as prod (from a checkout of `fictora-drama`):

   ```bash
   cd ../fictora-drama && uv run python scripts/…  # or inline compile_ass per timed word cues
   ```

   Timings: run `ffmpeg -af silencedetect=noise=-30dB:d=0.3` on the raw take; anchor the line on the **first speech span** after opening silence (silence-end → next silence-start).

5. **Burn** — repo helper (uses **ffmpeg-full** on macOS when plain `ffmpeg` lacks libass):

   ```bash
   FFMPEG=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg \
     bash scripts/burn_house_captions.sh \
     ep01/takes/take-ep01-t1-raw-v1.mp4 \
     ep01/takes/take-ep01-t1-house.ass \
     ep01/takes/take-ep01-t1-captioned-v1.mp4
   ```

6. **Gate** — Human read on the captioned file; record in `run-notes.md` before series post approve.

Prod burns the same recipe server-side when `api_captions: true` (`--api-captions` on `fictora-produce start`). Content ops default is **local** for speed and control.

## Tools

- **ffmpeg** with libass (Homebrew ffmpeg often needs an explicit libass build).
- Optional: Whisper or your existing desk transcript script — always **re-anchor** timings to silence-end rules before burn.

Do not ask the video model for on-screen text; captions are always post.
