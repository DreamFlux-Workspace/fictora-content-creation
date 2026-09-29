# Somewhere Between Us — Ep01 production bug log (2026-09-29)

Content-ops · Fictora Drama Generation API · series-v6.

Two server-side bugs on the same episode: board rendering, then filming blocked. Both logged in [backlog.md](../backlog.md). No scratch HTTP workarounds.

## Summary

| # | Area | Status |
| --- | --- | --- |
| 1 | Storyboard redraw | Accepted board v3 as-is; stopped redraw spend |
| 2 | Voice pick → filming | Blocked until engineering fixes cast field |

---

## Bug 1 — storyboard redraw

Take 2, row 1 (beat 4) should open on Hyunwoo and Jiwon walking together down the hallway — a new composition, not take 1’s map lean. The frame brief on the server was confirmed correct before each redraw:

- “no map visible in the walking row”
- shot scale medium two-shot, waist-up
- story moment: “Bumps Jiwon’s shoulder lightly while both continue forward in full stride.”

### Three renders, one scene (row 1, cell 1)

| Version | Action | Result |
| --- | --- | --- |
| v1 | Default draw | Map in hand, close ear-level lean — `job_boards_3768075049e94c939c85265080d73c39` |
| v2 | 1st redraw (cause, no beat edit yet) | Same scene — `job_board_regen_4ba005d7d83e4da292a932206d96440e` |
| v3 | 2nd redraw after beat rewrite (“no map, full stride, waist-up”) | Same scene — `job_board_regen_964e56fd6b5a47b898733b651a3176f2` |

### Cache ruled out (pixel diff on row 1, cell 1)

| Pair | Mean pixel difference | Reading |
| --- | --- | --- |
| v1 → v2 | 33.5 / 255 (13.1%) | Genuine new render |
| v1 → v3 | 33.3 / 255 (13.0%) | Genuine new render |
| v2 → v3 | 39.7 / 255 (15.6%) | Genuine new render |

### Likely cause and decision

Cast reference plates include a scenario panel with the map lean pose; the image model may over-anchor on that panel for consistency, overriding the row brief.

**Decision:** Stop redraw spend. Accept take 2 board v3 (story-ok, less visually distinct from take 1 open than intended). Product fix tracked in backlog.

---

## Bug 2 — voice pick wipes required cast field

Both voices locked via `POST /v1/spines/{id}/cast/{cast_id}/voice-auditions/pick` — Kang Jiwon → Liam, Lee Hyunwoo → Roger. Picks returned success.

Filming refused before spend:

```text
POST /v1/video-generations
422 spine_reuse_invalid: cast.cast_kang-jiwon.seedance_vocal_signature is required;
  cast.cast_lee-hyunwoo.seedance_vocal_signature is required
```

The aligned pick route clears `seedance_vocal_signature` instead of preserving or deriving it. The kit has no aligned route to restore the field.

### Prior hit

| Production | Cast repicked | Date |
| --- | --- | --- |
| Somewhere Between Us (series-v6) | Kang Jiwon → Liam, Lee Hyunwoo → Roger | 2026-09-29 |
| Somewhere Between Us (series-v5) | George, Charlie | 2026-09-28 |

**Decision:** Filming stays blocked. Runbook: no raw patch workaround. Engineering: `req_c811db7f097047da912fb7b7a7e94677`.
