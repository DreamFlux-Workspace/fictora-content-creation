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
uv run python scripts/content_ops_run.py init-series \
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

`15s` opens one take per episode. `30s` opens `t1` and `t2`. `60s` opens four takes.

## Daily loop

1. `status --desk <desk>` — open `QUEUE.md`. Work the waiting column.
2. Draw or reuse shared plates. `approve --gate plates`.
3. Set lines per take. `approve --gate script --episode N`.
4. Draw boards in parallel across episodes. Measure is automatic on approve. `approve --gate board --episode N --take t1 --path …`
5. `estimate` then `preflight`. Exit 0 is the only green light for the API take call.
6. Film once. `filmed`. Measure the take. `verdict --use` or `verdict --change --cause "…"`.

`preflight` fails when lines exceed three, the board is at or below 25% luma, the estimate is missing, spend would pass 2× the envelope, a later take has no hand-off, or a second film has no cause.

## Commands

| Command | Role |
| --- | --- |
| `init-series` | Open the desk |
| `add-episode` | One more slot |
| `status` | Review queue |
| `set-lines` | Draft lines. Does not approve |
| `approve` | Human yes |
| `estimate` | Price on the table |
| `preflight` | Hard stop before the take |
| `handoff` | Previous last frame |
| `spend` | Ledger |
| `filmed` | Take came back |
| `verdict` | Use it or Change this |

## Quality bar

The floor encodes the runbook rules that cost money when skipped. Taste still needs a human looking at the file. The machine only refuses the expensive call when a known fault is still open.
