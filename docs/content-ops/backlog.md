# Content-ops product backlog

Gaps the Episode Production Runbook named. Each production appends one line when it touches a gap. Do not close a line here by writing a scratch tool. Close it in the product, then mark the row Aligned in [api-map.md](api-map.md).

Closed in product (2026-09-21): `cut_tempo=one_shot`; `clip_duration_seconds` 4–15; `caption_style=house`; successor hand-off paste that fails loud; look-register Image 1 (`POST .../look-register`); location/prop look plates (`POST .../look-plates/draw`); 2×N `board_row` authoring; board exposure gate (`GET .../boards/exposure`, `422 boards_dim`); inner-voice (`PUT .../inner-voice`); voice auditions and pick; series audio-bed pin (`POST .../audio-bed`); sidechain ducking. Come Again (2026-09-18 → product 2026-09-21): frame-diff intra-take cuts + soften; canary `duration_seconds` 4–15; bed coverage assert; making-take forward lock; line-per-cell lint; speech-verify mute/diff; caption onset snap. See [api-map.md](api-map.md) and [research/2026-09-18-come-again-production.md](../research/2026-09-18-come-again-production.md).

Closed 2026-09-28: adding or removing a line and adding an off-screen voice after the draft (fictora-drama #466, `fictora-produce line --add/--remove/--new-voice`; SCP-173 Blink learnings #12/#15).

Closed 2026-09-28 by founder decision: a finished cut shorter than its 15 / 30 / 60 s band (e.g. 14.2 s after trimming a bad half-second) is allowed and is not a deviation. Takes still film at the normal clip length (`clip_duration_seconds` 4–15); a finished episode longer than its band is still a deviation.

| Stage | Gap | Why it costs money or time | Source |
| --- | --- | --- | --- |
| Take | A Turbo take redraws the board in H3's own anime finish: staging, framing, outfits and colour mood carry; drawing style does not (pastel, watercolour, cartoon boards come back anime). `finish` colour-match closes colour and light only | A creator who picks a painterly look sees a different-looking video than the board they approved; said at the look gate | Reviewer note (Paramjeet), measured 2026-09-25; carried from the internal backlog |
| Look | A custom look still needs a preset, whose world leaks into plates; no horror preset | Look notes are the only lever | Hanakaze #7, #19; SCP-173 #4 |
| Post | The automatic effects read only the take's Sound lines, not impacts written elsewhere in a beat | Hand cues on every horror or comedy take | SCP-173 #44, #45 |
| Captions | Japanese / Korean captions and a CJK caption font are deferred (English only) | A Japan-market cut needs captions made by hand | Hanakaze #40, #41 |
| Board | The safe-zone check reads placement text only; there is no measured face box | A face drawn into a covered zone with clean placement words is caught only by eye | Safe zones, 2026-09-28 |

When you add a row, name the production that hit it.
