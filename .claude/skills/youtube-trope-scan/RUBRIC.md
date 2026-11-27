# Judging rubric

The goal is a list of tropes Fictora can rebuild in a world of its own. A trope is free to reuse. A specific work is not: its names, its magic system, its key scenes and its plot order stay off-limits even with a twist. So every judgment answers three questions about one video. Is it drawn? Is it an original story? What is the engine underneath it?

Judge from the thumbnail sheet, the title, the channel, the length and the tags. You are not watching the video. When those do not settle a field, use the `unclear` value. A wrong `yes` costs more than an `unclear`, because a `yes` can send a team to copy an existing work.

## Fields

Write one object per video, in the batch's order.

| Field | Values |
| --- | --- |
| `video_id` | Copied from the batch file. |
| `title_meaning` | One plain English line saying what the title says. Not a literal translation, not a review. |
| `look` | `anime`, `manga`, `story-toon`, `3d-or-ai-comic`, `photoreal`, `live-action`, `other` |
| `based_on` | `original`, `reupload`, `recap`, `fan-work`, `official`, `paid-promo`, `import`, `not-a-story`, `unclear` |
| `trope` | An exact name from `scripts/youtube_trope_vocabulary.json`, or `new: <name>`, or empty. |
| `trope_description` | Required with `new: <name>`. One sentence in the style of the vocabulary's descriptions. |
| `copyable` | `yes`, `no`, `unclear` |
| `note` | Optional. The one fact that decided a close call. |

## Look

- `anime`: colour illustration or animation in an anime style, including AI-illustrated story videos.
- `manga`: black-and-white comic panels.
- `story-toon`: simple flat cartoon or webtoon drawing, the Korean 썰툰 and 영상툰 style, and Japanese スカッと漫画 with the same flat look.
- `3d-or-ai-comic`: rendered 3D or glossy semi-real AI comic art, common in dubbed Chinese imports.
- `photoreal`: AI or CGI that reads as live action.
- `live-action`: filmed people.

`anime`, `manga` and `story-toon` are the drawn looks.

## Based on

- `original`: a story the channel made. Typical signs: a small channel, a designed colour thumbnail with the hook written on it, a length between a few minutes and about two hours, a title that tells the whole premise.
- `reupload`: someone else's published manga or TV anime uploaded as a compilation. Typical signs: black-and-white panels, a chapter or episode range in the title (`1~52`, `第1話〜第24話`), several hours long, a channel whose name is in an unrelated language or about an unrelated subject.
- `recap`: a summary or review of an existing work. Titles carry words such as `애니리뷰`, `결말포함`, `解説`, `ネタバレ`.
- `fan-work`: a new piece built on an existing work, such as an AI live-action remake or a "descendants of" story.
- `official`: a studio or publisher's own upload.
- `paid-promo`: an advertisement for an app, a platform or a title.
- `import`: a dubbed or subtitled foreign production whose source you cannot name.
- `not-a-story`: an explainer, a ranking, a tutorial, a commentary, a music video.
- `unclear`: it could be original or an existing work and nothing on the card settles it.

A retelling of a named radio or TV show's listener stories is `recap`, not `original`. An anonymous anecdote channel that writes its own scripts is `original`.

## Trope

Name the engine, not the setting. "Exiled healer" and "exiled mechanic" are the same trope. Check the video against each vocabulary entry in turn and pick the one whose description fits. Read the descriptions, not only the names.

Propose `new: <name>` only when no entry fits and you can state the engine in one sentence that would also describe a different story. Leave `trope` empty when the video is not a story or you cannot tell what happens in it.

## Copyable

`yes` needs all three: a drawn look, `based_on` equal to `original`, and a trope. Use `unclear` when it would be `yes` except that `based_on` is `unclear`. Everything else is `no`. A photoreal original is `no` here: it is a real signal, but this pass is for drawn work.

## The existing-work check

Every `yes` is checked once more before it counts, by a search for a published manga, light novel, web novel, webtoon or anime with the same title or the same distinctive premise. The check writes `yes`, `no` or `unclear` with the evidence it found. Only `no` leaves the video copyable. Default to `unclear` when the search is thin.
