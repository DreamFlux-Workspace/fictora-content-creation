# Series floor — parallel episodes, one quality bar

The content function produces many episodes. Quality does not come from filming faster. It comes from clearing cheap gates in parallel, then filming each take once.

The floor is the desk that makes that possible.

## What is parallel

Allowed:

- Many episode folders on one series desk, all waiting at once.
- One review queue. A human clears `look`, then `plates`, then several scripts, then several boards.
- Shared plates, look, voices, and the series bed. Episode 2 does not redraw the cast.
- API enrol of plates or boards for different episodes after the shared plates are approved.

Not allowed:

- Batching human gates. Four boards in one "looks fine" is how The Elevator episode 1 spent six takes.
- Auto-approve. `drama_create_flow_smoke.py` is not a floor command.
- A take enrol when `preflight` exits 3.

## Open a desk

```bash
uv run fictora-ops init-series \
  --series "One More Round" \
  --band 15s \
  --episodes 4
```

This creates:

```
~/Downloads/documents/YYYY-MM-DD-one-more-round/
  series.json
  QUEUE.md
  shared/look  shared/plates  shared/reference  shared/voices  shared/beds
  ep01/  ep02/  ep03/  ep04/
```

`15s` opens one take per episode. `30s` opens `t1` and `t2`. `60s` opens four takes. `--continuing` marks a desk that reuses an existing cast (the continuing envelope).

A desk that already exists for that date and slug is refused, never overwritten. Do not delete it or pick another parent folder to get round it: ask the human whether to continue it or name the series differently.

## Daily loop

1. `status --desk <desk>` — open `QUEUE.md`. Work the waiting column.
2. Draw or reuse shared plates. `approve --gate plates`.
3. Set lines per take. `approve --gate script --episode N`.
4. Draw boards in parallel across episodes. Measure is automatic on approve. `approve --gate board --episode N --take t1 --path …`
5. `estimate` then `preflight`. Exit 0 is the only green light for the API take call.
6. Film once. `filmed`. Measure the take. `verdict --use` or `verdict --change --cause "…"`.

`preflight` blocks (exit 3) only while a human gate is open. It warns (exit 4) when lines exceed three, the board is at or below 25% luma, the estimate is missing, spend would pass 2× the envelope, a later take has no hand-off, or a second film has no cause. Paste the banner to the human; only their explicit "film anyway" in this turn lets you run `preflight … --proceed-anyway ep0N-tN` (logged, next film only).

## Commands

| Command | Role |
| --- | --- |
| `init-series` | Open the desk |
| `add-episode` | One more slot |
| `status` | Review queue |
| `set-lines` | Draft lines on the desk only (the server keeps its own; `fictora-produce line` changes both). `--lines-json` takes the JSON, `@FILE` or a file path. Reopens the script gate |
| `approve` | Human yes |
| `estimate` | Price on the table |
| `preflight` | Hard stop before the take |
| `handoff` | Previous last frame |
| `spend` | Ledger |
| `filmed` | Take came back |
| `verdict` | Use it or Change this |

## Envelopes

Warnings, never a stop (`creation/prices.py`): first 15 s episode of a new series $5.50; continuing 15 s $2.50; continuing 30 s $5.00; continuing 60 s $8.00. Preflight warns (exit 4) past twice the envelope; say "$X of $Y" when an episode crosses it.

## Quality bar

The floor encodes the runbook rules that cost money when skipped. Taste still needs a human looking at the file. The machine only refuses the expensive call when a known fault is still open.
