# Example: Tram Not Tonight (ep1, prod-validated)

Desk slug pattern: `2026-09-22-tram-not-tonight` (`start --series "Tram Not Tonight"`).

## Start command

```bash
uv run fictora-produce start \
  --series "Tram Not Tonight" \
  --prompt "Vertical 15s modern dark fantasy. Empty late-night tram car: rain streaking the windows, fluorescent lights flickering, wet reflections on plastic seats. Episode 1 beat: tired night-shift nurse Mara (on screen, alone in the car) notices a folded paper left on the seat beside her; she looks at it but does NOT open or read it. One quiet spoken line from Mara: \"Not tonight.\" Season bible: at least two named characters — Mara (nurse, on screen) and a second character (e.g. tram driver or unseen passenger) who can stay off-screen or voice-only in ep1. No gore, no jump scares; moody restraint." \
  --band 15s \
  --preset-id modern-dark-fantasy \
  --video-lane minimax-h3 \
  --draft-episodes 4 \
  --clip-seconds 15 \
  --cut-tempo one_shot \
  --caption-style house \
  --fallback-estimate-usd 1.20
```

Do **not** pass `--api-captions`; burn house captions locally after the raw take lands.

## Outcomes (2026-09-22 prod run)

- Spine title **The Folded Fare**; cast **Mara Vale** + **Elias Rook** (driver, ep2-forward on screen).
- Ep1 line on spine: **Not tonight.**
- Board exposure ~14% mean (below 25% dim floor) — use `approve --gate board --accept-dim` when intentional night interior.
- Raw take ~15s vertical; local caption via `uv run fictora-produce caption --desk <desk>` (silence-end ASS cues).

## Agent completion checklist

After `step --confirm-spend` with `api_captions: false`:

1. Confirm `ep01/takes/take-ep01-t1-raw-v1.mp4` and `17_raw_scene_clips.json`.
2. `uv run fictora-produce caption --desk <desk>` — builds the house ASS, burns the captioned take, logs paths in `ep01/run-notes.md`, opens it.
3. Human QC on the captioned take.
