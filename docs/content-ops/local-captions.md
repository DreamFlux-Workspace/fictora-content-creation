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
| `--caption-style S` | `bold`, `subtle` (`house` is its older name), `plain` (white whole lines) or `none` (nothing burned). Default: the show's style (`caption_style` in the desk's `production.config.json`, else `bold` for a new show and `subtle` for one with finished episodes). Same flag on `finish` and `reel` |
| `--words-json J` | A transcript of the take (`/v1/transcripts` words) to time the lines on, on any show. Default: the newest `takes/take-epNN-t1-*words-vN.json` when captioning the raw take |
| `--no-open` | Do not open the result |

Writes, never overwriting:

| File | Content |
| --- | --- |
| `takes/take-ep01-t1-house-vN.ass` | Caption cues |
| `takes/take-ep01-t1-captioned-vN.mp4` | Raw take with captions burned in (audio copied) |
| `run-notes.md` | One note with the files and, per line, when it is on screen and what timed it: `(words)`, `(speech)`, `(manual)`, or a mix such as `(words start, manual end)` |

## House look

The house style the content team delivers (runbook rule 1): **Arial Bold 64 on a 1080×1920 canvas, yellow, outline 5, one face for every line.** It is the kit's default, so a take never needs a re-burn at house size. Every number is scaled to the take by frame height: 64 on 1920 is **45 on the 1344-high H3 take** (typing 64 into a 1344 take's header makes it 42% too big).

| Property | Value |
| --- | --- |
| Text colour | Yellow `#FFE500` |
| Font | Arial Bold. A system font (macOS ships it; it cannot be bundled): `setup-check` warns (⚠) when it is missing, and a render that falls back prints `WARNING FONT: …` and notes it in the run notes |
| Size | 64 on a 1920 px-tall frame (3.3% of height), scaled to the take: 45 on 1344, 43 on 1280 |
| Edge | Black outline 5 (4 on 1344), soft 50% black shadow 2 (1 on 1344), no box |
| Placement | Centred, side margins 60 px on a 1080-wide frame (43 on 768); bottom of text at 62% of frame height (margin 511 px on 1344, 730 on 1920), block inside 55–70% (social safe zones), two lines included |
| Long lines | A caption too wide for one line wraps onto **two balanced lines** (the break that makes the two lines closest in width; the kit breaks it, `WrapStyle: 2`, so libass adds no breaks of its own). Only a caption that does not fit on two lines is set smaller |
| CJK | Arial has no Japanese, Chinese or Korean glyphs, so a cue with them is set in a CJK face for that line only (`\fn`: Hiragino Sans, or Apple SD Gothic Neo for Hangul, on macOS; Noto Sans CJK JP / KR elsewhere). Captions stay English (Not English, below), so this only reaches a hand-made cue |
| Reveal | Flicker: words build up to three on screen, then reset; each line resets |
| Timing | On screen only while the line is spoken; last word holds 0.15 s (0.25 s after a transcript's last word), never into the next line. A whole English line (show not spoken in English) stays up at least max(1.2 s, 0.3 s a word), extended forward only, never into the next line |
| Heard, not seen | A line marked `off_screen`, or any line of a cast member the server flags `voice_only`, is set in Georgia italic (ASS style `Italic`, not bold): same drawn size, colour, edge and place. libass sizes a face by its full line height, so the `Italic` Fontsize is scaled (46 against Arial Bold's 45 on 1344 px) to draw at the same em. Georgia is a system font (macOS ships it), not bundled; `setup-check` warns (⚠) when Georgia Italic is missing, and a render that falls back prints `WARNING FONT: …` and notes it in the run notes |
| Not English | Captions are English only. A line whose caption (`subtitle_text`, else `text`) has kana, CJK, Hangul, any other non-Latin letter, or CJK/fullwidth punctuation is timed (it keeps its speech span, so later lines stay put) but not drawn. `caption` and `finish` print ``NOT ENGLISH: <line id> "…" — add an English subtitle with `edit`/`line --subtitle` `` and write it to `run-notes.md` |

## Caption styles

| Style | What is burned |
| --- | --- |
| `bold` (a new show's default) | One short line at a time (two or three words, split above ~12 characters, never across a clause mark or a breath), Arial Bold 92 on 1920 (1.44x house; 64 on 1344), white with one word in house yellow, outline 7, shadow 3, bottom at 76%. Each word shows when it is said: the chunk is laid out whole and its unsaid words are transparent. The yellow word is the line's spine `emphasis_word` when set, else the chunk's last content word (an ALL-CAPS shout first; a one-word chunk only when it carries meaning). Heard-not-seen lines in Georgia italic at the same size and place. Whole lines (white, one yellow word, up to two lines) on a show spoken in another language. A letterbox show keeps its own band (`creation/caption_bold.py`) |
| `subtle` / `house` | The house look above: yellow; word flicker on a show spoken in English, whole lines on a show spoken in another language. A show with finished episodes keeps it |
| `plain` | White whole-line captions on any show: same face, size, edge, wrapping and safe band; heard-not-seen lines still in Georgia italic (white) |
| `none` | No captions. `finish` still mixes and marks the take, which is complete; its sound line reads `captions off (--caption-style none)` |

Set it per run with `--caption-style` on `finish`, `caption` or `reel`, or for the show with `fictora-produce caption-style --desk D --set bold|subtle|plain|none` (or `start --caption-style …`; `caption_style` in `production.config.json`). Unset, it is chosen once at the look approval: a new show gets a free preview still (`shared/look/caption-preview-vN.png`, Bold left, Subtle right, on the approved look frame with the first dialogue line) and `bold` is saved; a continuing show is saved `subtle` without asking (`creation/caption_preview.py`). It is the same setting as before: `caption_style` was already the desk's local caption recipe, and the server only sees it with `--api-captions`, so `--api-captions` with `plain` or `none` is refused (the server does not burn those). A desk whose `caption_style` is a server recipe name (e.g. `viral_karaoke`) is captioned as an unset show is (Bold new, Subtle continuing) locally, with a note.

## How timing works

1. **Lines**: the take's dialogue from the **current spine**: `finish` and `caption` read it from the server (a free `GET`, saved on the desk as `spine.json`), so a line deleted or changed before filming is never captioned from an old desk copy (L-20261001-8). Only when the server cannot be read is the newest snapshot in `epNN/api/` used, and the report says `!! captions from the desk's copy …`.
2. **Speech spans**: `silencedetect=noise=-30dB:d=0.3` on the take. Gaps shorter than 0.12 s are clicks, not speech.
3. **Anchor**: each line starts on the next speech span (silence-end onset) and absorbs following spans only while the pause is under 0.6 s and the line still needs time. Spans after the last line are not captioned, and the report and run notes say **`EXTRA SPEECH: N speech stretch(es) for M line(s); not captioned: a-b s …`** (also on stderr). Lines take spans strictly in order, so one stray stretch of speech before a line (a mumble, a line the take made up) puts every later caption on the wrong words; leftover spans are the only sign of it without a transcript (L-20260930-6: "You must be Hana." over Hana). It can also be a door or the music tail: watch the captions. Fix: a transcript (`review --transcribe`, then finish again: captions follow the words), `finish --mute A-B` for stray speech, or `--line-start` / `--line-end`.
4. **Words**: with a transcript, each word shows when it is said (below); without one they are spread across the line's span, weighted by word length (an estimate).

No transcription model is needed for an English show: the words are already fixed at the script gate. If detection picks the wrong sound, watch the raw take, note where each line starts and ends, and re-run with `--line-start` / `--line-end`.

### A show not spoken in English: timed on the transcript's words

Speech spans cannot tell a stammer or a mid-line pause from a line: on Hanakaze Sweets ep02 t1 (Japanese) a stammer `も、` at 9.5 s started line 2 two seconds early, and a pause inside line 1 ended it at 1.63 s of 3.05 s. So when the show's `spoken_language` is not English (whole English lines), each line is timed on the words a Whisper transcript of the take heard for it:

1. **Transcript**: `--words-json`, else the newest saved `takes/take-epNN-tK-*words-vN.json` (`review --transcribe`, `revoice`; `review` itself reads only a transcript of the exact take it checks: see reference.md). `finish` asks the server for one when none is saved (`/v1/transcripts` on the take's stored URL, a few cents, saved as `take-epNN-tK-review-words-vN.json` and reused next time); `caption` never does.
2. **Match**: the same matcher `review` uses for "heard" (`creation/post/whisper.py`, `line_windows`), on what is performed (`spoken_text`, else `text`) and every spelling; a Japanese line is matched on the server's per-word readings (a word with several plausible readings, `readings`, is compared on the one a line has: 美味 heard for うまい as ウマ, 今日 for こんにちは as コンニチ).
3. **Start**: the first matched word. A word at the head that Whisper stretched past what one word can last (over 1.2 s and 0.25 s a character) starts where its speech starts: the last speech span of the take (the same silencedetect as speech spans) that begins inside the word after a pause over 0.6 s (ep 4 `ちょっと!` heard 9.37–12.45 s starts at 11.34 s; ep 6 `悪` 8.81–12.57 s at 12.42 s). Only with no such onset is it skipped (`もう` over 9.47–11.33 s with no speech in it would be). Also skipped: one short leading word (two characters or fewer, a stammer) cut off from the rest by a pause over 0.6 s.
4. **End**: the last matched word's end (a stretched last word is cut to 1.2 s from its start), plus a 0.25 s hold, never into the next line.
5. **Readable**: each whole line stays up at least max(1.2 s, 0.3 s per English word), extended forward only, never into the next line.
6. **Fallback**: a line the transcript did not match is timed on speech spans (steps 2–3 above). So is every line when there is no transcript (and none can be made), when `--voice`/`--mute` changed the take's speech, or when `finish --take-file` is not the raw take the server transcribed. The report says which method timed each line.

Hanakaze ep02 t1 with its transcript: line 1 `0.03–3.30 s` (was 0.03–1.63), line 2 `11.33–13.50 s` (was 9.47–10.43).

**English shows use a transcript too** when one is saved (`--words-json`, else the newest `take-epNN-tK-*words-vN.json`; `review --transcribe` makes one): each line is placed on the words heard for it, the same matcher as above, so extra speech on the take no longer moves it. `finish` never asks the server for a transcript of an English take: with none saved it times on speech spans and warns `EXTRA SPEECH` when stretches are left over. A take with hand `--voice` / `--mute` uses the saved transcript with the hand lines added and the mutes taken out (`take-epNN-tK-cap-timing-vN.json`). English still flickers, and each word now shows **when it is said** (6 Oct 2026): its own transcript time, checked against the take's speech spans, because Whisper stamps a word after a pause from the end of the word before it (SCP-173 Blink ep 1: `Please,` heard 6.29 s, said 7.83 s; `open` heard 8.19 s, said 8.83 s). A word that starts mostly in silence starts where the speech inside it starts (at least 0.1 s later); a stretched word starts at its onset after a pause, else where it is stamped when that is speech. No word shows before its sound. Spreading the words over the line by length put that episode's `door!` 0.8 s after it was said. Where the line as written and what was said differ, **what was said is shown** (the line's own spelling stays where the words agree, `NOT—` for a heard `not!`, and for a name or a 4+ letter word one letter off); the report and run notes say `WORDING: captioned as said, not as written: …`. A line timed on speech spans or hand times is still spread (an estimate). A locked-voice take (`soundtrack: target_audio`) is still timed on its exact line windows, never a transcript.

### Lines laid by hand

A line laid with `finish --voice FILE@S` is captioned where it is laid, in the heard-not-seen style (Georgia italic), method `(laid)`, with the words saved beside it by `voice-line` (its `.json`). Its window is left out of the take's speech stretches, so the script lines stay on their own speech. A `--voice` line whose words are one of the take's script lines (a replacement read: `--mute` the old line, `--voice` the new one) is that line: captioned once, as the script line. A `--voice` file with no saved words is named: `!! --voice FILE not captioned: no words saved with it`. Inner-voice cues are captioned the same way (they always were).

## Requirements

- **Arial Bold** (house face) and **Georgia Italic** (heard-not-seen face) as system fonts; `setup-check` warns when either is missing.
- **ffmpeg + ffprobe with libass** (`ffmpeg -filters` lists `ass`). macOS: `brew install ffmpeg`; use `ffmpeg-full` only if your build lacks `ass`. `fictora-produce start` warns when it is missing.

## Low-level

`scripts/burn_house_captions.sh <raw.mp4> <captions.ass> <out.mp4>` burns an existing ASS (the bundled fonts dir is passed to libass; Arial and Georgia come from the system). Prefer the command above, which builds the ASS too.

Do not ask the video model for on-screen text; captions are always post.
