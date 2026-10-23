# Posting episodes: lanes, accounts and slots

This page is the posting side: which account a show goes on, and when. Reels, TikTok parts ("moments") and clips were retired by the founder on 23 Oct 2026: the finished episode itself is what goes on Instagram and TikTok.

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

These are for you only: they are never sent to the server and never go in the caption.

## Stagger the slots: at least 2 hours apart

Four accounts going live together, every day, looks like one operator running a network, which is exactly what Instagram tries to spot and hold back. Give each account its own `posting_slot`, **at least 2 hours from every other account's slot**, and keep to it every day (for example 12:30, 15:00, 18:30 and 21:00 IST).

## What to do in Instagram or TikTok for each episode

1. **Video.** Post the finished episode (the `-sokii` file from `finish`, or the joined episode from `join`).
2. **Sound.** The video carries no music (voices, effects and room sound only). Add a trending sound in the Instagram or TikTok editor that fits the account (eerie for horror, soft for BL and food, playful-spooky for horror-comedy) and turn its volume down so the voices stay clear. If an episode truly needs its own score (a jumpscare sting), ask the Fictora team for its "with score" version and choose which one to post.
3. Post in the account's slot.
