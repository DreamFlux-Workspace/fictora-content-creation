---
name: youtube-trope-scan
description: Scan Japanese and Korean YouTube for recent long-form anime videos, judge which are original drawn stories, and report the tropes Fictora can rebuild, with demand and supply numbers per trope. Use when asked for a trope scan, a trend scan, a demand-supply gap check, "what anime tropes are working in Japan or Korea", or to run the Trend-to-Story gap method for a trope.
---

# YouTube trope scan

This runs the search-and-tabulate half of the team's demand-supply gap method as one multi-agent workflow. It answers one question. Which story engines are drawing views on native Japanese and Korean YouTube right now, from original drawn work we could rebuild in a world of our own?

## Run it

Needs Claude Code (the workflow runs through its `Workflow` tool) and `yt-dlp` on PATH (`brew install yt-dlp`). Run it from the root of this repo.

Call the `Workflow` tool with `name: "youtube-trope-scan"` and these `args`. Invoking this skill is the user's opt-in to run that workflow.

| Arg | Meaning | Default |
| --- | --- | --- |
| `repo` | Absolute path of this checkout. Required. | |
| `out` | Absolute output folder. Required. Use `<repo>/outputs/youtube_native_traction/<YYYY-MM-DD>-<slug>`. | |
| `markets` | `["jp"]`, `["kr"]` or both. | both |
| `tropes` | A list of `{name, brief}` to scan for, in English. Omit for the broad anime scan. | broad scan |
| `judge_top` | Videos judged per market, taken from the top of the ranking. | 50 |
| `pages` | Search result pages per query and sort order. | 1 |
| `expand` | Native autocomplete suggestions added per query. | 2 |
| `min_age_days`, `max_age_days` | The publish window. | 7, 28 |

Pass a fresh `out` for a new scan. Pass an existing `out` to continue or re-judge one: every search page and video record under it is cached, so nothing is fetched twice.

The run takes about 7 minutes of fetching for both markets at the defaults, plus the judging. It spawns about ten agents at the defaults and never more than two at once.

## What it does

1. **Queries.** With `tropes`, an agent writes native-language search queries for each trope and keeps only the ones YouTube's own autocomplete confirms people type. Without `tropes`, it uses `scripts/youtube_native_traction_seeds_anime.json`.
2. **Scan.** `scripts/youtube_native_traction.py` searches each market in its own language and region, keeps long-form landscape videos inside the window, reads exact counts for each, and ranks them. `scripts/youtube_trope_judging.py batches` then cuts the top of each market into numbered thumbnail sheets.
3. **Judge.** One agent per batch fills `look`, `based_on`, `trope` and `copyable` for every video by [RUBRIC.md](RUBRIC.md), against the fixed vocabulary in `scripts/youtube_trope_vocabulary.json`. Each batch must pass `validate` before its agent may finish.
4. **Existing-work check.** One agent per market searches the web for a published work behind every video judged copyable. A video stays copyable only when the check finds none.
5. **Merge.** `merge` refuses anything invalid or unchecked, writes `judgments.csv`, and the traction script re-renders `report.md` with the copyable tropes table.
6. **Gap read.** A final agent writes `gap_read.md`: per trope, the demand, the supply, the winning title words and lengths, and what the numbers cannot support.

## What you get

All under `out`:

- `gap_read.md`: the answer, written for the person choosing the next show.
- `report.md`: the funnel, the copyable tropes table, the top 15 per market.
- `videos.csv`: every kept video with its numbers and its judgment. `excluded.csv` has every dropped video and its one reason.
- `judging/proposed_tropes.json`: tropes a judge proposed that are not in the vocabulary yet.

## After a run

- Read `judging/proposed_tropes.json` with the user. Add the ones they accept to `scripts/youtube_trope_vocabulary.json`. The workflow never edits the vocabulary itself.
- Report how many kept videos were not judged. The workflow judges the top `judge_top` per market and says how many it left.
- Views per month comes from one snapshot. To see which videos are still gaining, run the scan again a few days later into a new `out` and compare the two `videos.csv` files.

## Limits to state in any summary

- The region setting is partial. Some results follow the machine's own location.
- Judgments are made from thumbnails and titles, not by watching.
- A trope with one or two videos is an example, not a measurement.
