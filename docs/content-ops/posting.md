# Posting reels: lanes, accounts, slots and the results sheet

Our reels average about 40–140 views. Part of that is the reel; part of it is how we post. This page is the posting side.

## One lane per account

Each Instagram account posts one lane: one kind of story a stranger can recognise from the grid (for example `mystery`, `power-fantasy`, `romance`). A follower who came for regression revenge should not get a cosy café romance next. Put a new kind of show on the account whose lane it fits, or start a lane on a new account.

Write the lane and account on the show's desk, in `production.config.json`:

```json
{
  "lane": "power-fantasy",
  "account": "@yourhandle",
  "posting_slot": "18:30 IST"
}
```

These are for you only: they are never sent to the server and never go in the caption. `reel` prints them with the post text and writes them in the results sheet.

## Stagger the slots: at least 2 hours apart

Today the four accounts post within 10–20 minutes of each other. Four accounts going live together, every day, looks like one operator running a network, which is exactly what Instagram tries to spot and hold back. Give each account its own `posting_slot`, **at least 2 hours from every other account's slot**, and keep to it every day (for example 12:30, 15:00, 18:30 and 21:00 IST).

An account with no `posting_slot` gets a ⚠ on every `reel`. It never stops the reel.

## What to do in Instagram for each reel

`reel` writes `reels/post-epNN-vN.txt` and prints it at the end of the run:

1. **Caption.** Copy only the part above `----- Not part of the caption: for you, not for Instagram -----`. It says "Part N" and "Follow for part N+1." Strangers don't know what an episode is; a numbered part tells them there's a story to start.
2. **Cover.** Instagram ignores a cover attached inside the MP4. In the editor tap **Edit cover**, then **Add from camera roll**, and pick `reels/reel-epNN-vN-cover-vN.jpg` (the "PART N" and series title still `reel` made for free). Without this the grid shows a random frame.
3. **Sound.** Add a trending sound in the Instagram editor and turn its volume down so the voices stay clear.
4. Post in the account's slot.

## The results sheet: `reels/metrics.csv`

Every rendered reel appends one row to `<desk>/reels/metrics.csv`. The kit fills what it knows: reel and cover file, series, part, account, lane, planned slot, the cold open's role and time, and the hook text. You fill the rest by hand from the reel's insights about 48 hours after posting:

| Column | What to write |
| --- | --- |
| `posted_at` | When it actually went up (`2026-10-06 18:32 IST`) |
| `views` | Plays |
| `hold_3s_pct` | Share of viewers still watching at 3 s, if Instagram shows it |
| `avg_watch_pct` | Average watch time as a share of the reel's length |
| `follows`, `saves`, `shares` | From the insights |
| `notes` | Anything odd: posted late, wrong cover, a trend sound that took off |

This is how we find out which covers, cold opens, lanes and slots work. It is manual for now; keep it filled so the numbers can be read across shows.
