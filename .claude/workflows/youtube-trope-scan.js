export const meta = {
  name: 'youtube-trope-scan',
  description: 'Scan Japanese and Korean YouTube for original drawn stories and report the tropes we can rebuild, with demand and supply per trope',
  whenToUse: 'A trope scan, trend scan or demand-supply gap check on native JP/KR YouTube. Args: {repo, out, markets?, tropes?, judge_top?, pages?, expand?, min_age_days?, max_age_days?}. See .claude/skills/youtube-trope-scan/SKILL.md.',
  phases: [
    { title: 'Queries', detail: 'native search queries per trope, confirmed by autocomplete', model: 'opus' },
    { title: 'Scan', detail: 'search, filter, rank, cut judging batches', model: 'sonnet' },
    { title: 'Judge', detail: 'one agent per batch fills look, source and trope', model: 'sonnet' },
    { title: 'Existing-work check', detail: 'web search for a published work behind every copyable video', model: 'sonnet' },
    { title: 'Merge', detail: 'validate, write judgments.csv, re-render the report', model: 'sonnet' },
    { title: 'Gap read', detail: 'demand and supply per trope', model: 'opus' },
  ],
}

const a = args || {}
if (!a.repo || !a.out) throw new Error('args.repo and args.out are required absolute paths')
const repo = a.repo
const out = a.out
const markets = a.markets && a.markets.length ? a.markets : ['jp', 'kr']
const tropes = a.tropes || []
const judgeTop = a.judge_top || 50
const pages = a.pages || 1
const expand = a.expand === undefined ? 2 : a.expand
const minAge = a.min_age_days || 7
const maxAge = a.max_age_days || 28

// This machine runs two heavy agents at once at most, so fan-out goes through this instead of pipeline().
const MAX_AT_ONCE = 2
async function inPairs(items, run) {
  const results = []
  for (let i = 0; i < items.length; i += MAX_AT_ONCE) {
    results.push(...(await parallel(items.slice(i, i + MAX_AT_ONCE).map((item) => () => run(item)))))
  }
  return results
}

const skill = `${repo}/.claude/skills/youtube-trope-scan`
const rubric = `${skill}/RUBRIC.md`
const vocabulary = `${repo}/scripts/youtube_trope_vocabulary.json`
const judging = `${out}/judging`
const seedsFile = tropes.length ? `${out}/seeds.json` : `${repo}/scripts/youtube_native_traction_seeds_anime.json`
const scanCommand =
  `cd "${repo}" && uv run python scripts/youtube_native_traction.py --market ${markets.join(' ')} ` +
  `--seeds-file "${seedsFile}" --pages ${pages} --expand ${expand} --workers 5 ` +
  `--min-age-days ${minAge} --max-age-days ${maxAge} ` +
  `--exclude-tag official_or_trailer_hint --exclude-tag howto_or_music_hint --out "${out}"`
const judgingTool = `cd "${repo}" && uv run python scripts/youtube_trope_judging.py`

const GOAL =
  'Goal of the whole run: find the story engines (tropes) that draw views on native Japanese and Korean YouTube ' +
  'from original drawn work, so the team can rebuild them in a world of its own. A trope is free to reuse; ' +
  'a specific existing work is not.'

if (tropes.length) {
  phase('Queries')
  const written = await agent(
    `${GOAL}\n\n` +
      `Write the native-language YouTube search queries for these tropes, for the markets ${markets.join(', ')} ` +
      `(jp = Japanese, kr = Korean):\n${JSON.stringify(tropes, null, 1)}\n\n` +
      `Rules:\n` +
      `- Write each query the way a native viewer types it into YouTube search: short, in native script, no translated English phrasing.\n` +
      `- 6 to 10 queries per trope per market. Mix the trope's own words with the drawn-format words natives use ` +
      `(jp: 漫画動画, 異世界漫画, 異世界アニメ, ボイスコミック, スカッと; kr: 썰툰, 영상툰, 모션 웹툰, 애니, 로판).\n` +
      `- Confirm every query against YouTube's own autocomplete before you keep it. For jp:\n` +
      `  curl -s -G "https://suggestqueries.google.com/complete/search" -H "User-Agent: Mozilla/5.0" --data-urlencode "client=firefox" --data-urlencode "ds=yt" --data-urlencode "hl=ja" --data-urlencode "gl=JP" --data-urlencode "q=<query>"\n` +
      `  (kr: hl=ko, gl=KR). The reply is JSON [query, [suggestions...]]. Keep a query only when it returns at least one suggestion. ` +
      `When it returns none, try a shorter or more common phrasing, or drop it.\n` +
      `- Write the kept queries to ${seedsFile} in exactly the shape of ${repo}/scripts/youtube_native_traction_seeds_anime.json ` +
      `(read that file first): {market: [{group, text}, ...]}, with group set to the trope's name from the list above.\n\n` +
      `Return the path and every kept query with its suggestion count.`,
    {
      label: 'write native queries',
      model: 'opus',
      effort: 'medium',
      schema: {
        type: 'object',
        properties: {
          seeds_file: { type: 'string' },
          queries: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                market: { type: 'string' },
                group: { type: 'string' },
                text: { type: 'string' },
                suggestions: { type: 'integer' },
              },
              required: ['market', 'group', 'text', 'suggestions'],
            },
          },
        },
        required: ['seeds_file', 'queries'],
      },
    },
  )
  if (!written || !written.queries.length) return { ok: false, stage: 'Queries', error: 'no query survived the autocomplete check' }
  log(`${written.queries.length} native queries kept for ${tropes.length} tropes`)
}

phase('Scan')
const scan = await agent(
  `${GOAL}\n\n` +
    `Run the scan, then cut the judging batches. Run both commands exactly as written; do not edit any file.\n\n` +
    `1. Scan (use the Bash tool with timeout 600000):\n   ${scanCommand}\n` +
    `   It prints one JSON funnel line per market, then "fetched this run: ...", then the output paths. ` +
    `Every page and video it fetches is cached under ${out}/raw, so if the command hits the timeout, run the same command again ` +
    `(up to 4 times in total) and it continues from where it stopped. ` +
    `If it exits with code 2, YouTube refused or bot-checked it: stop, do not retry, and return ok=false with the exact message.\n` +
    `2. Batches:\n   cd "${repo}" && uv run --with pillow python scripts/youtube_trope_judging.py batches --out "${out}" --top ${judgeTop}\n` +
    `   It prints JSON with the batches it wrote.\n\n` +
    `Return the kept count per market from the funnel lines, and the batches, to_judge and not_judged values from the second command's JSON, unchanged.`,
  {
    label: 'scan and batch',
    model: 'sonnet',
    effort: 'low',
    schema: {
      type: 'object',
      properties: {
        ok: { type: 'boolean' },
        error: { type: 'string' },
        kept: {
          type: 'array',
          items: {
            type: 'object',
            properties: { market: { type: 'string' }, kept: { type: 'integer' } },
            required: ['market', 'kept'],
          },
        },
        batches: {
          type: 'array',
          items: {
            type: 'object',
            properties: { batch: { type: 'string' }, market: { type: 'string' }, videos: { type: 'integer' } },
            required: ['batch', 'market', 'videos'],
          },
        },
        to_judge: { type: 'integer' },
        not_judged: { type: 'integer' },
      },
      required: ['ok', 'kept', 'batches', 'to_judge', 'not_judged'],
    },
  },
)
if (!scan || !scan.ok) return { ok: false, stage: 'Scan', error: scan ? scan.error : 'the scan agent did not finish' }
if (!scan.batches.length) return { ok: false, stage: 'Scan', error: 'the scan kept no videos, so there is nothing to judge', kept: scan.kept }
log(
  `kept ${scan.kept.map((k) => `${k.market} ${k.kept}`).join(', ')}; judging the top ${scan.to_judge}, ` +
    `leaving ${scan.not_judged} kept videos unjudged`,
)

const JUDGED_SCHEMA = {
  type: 'object',
  properties: {
    batch: { type: 'string' },
    valid: { type: 'boolean' },
    judged: { type: 'integer' },
    copyable_yes: { type: 'integer' },
    proposed_tropes: { type: 'array', items: { type: 'string' } },
    error: { type: 'string' },
  },
  required: ['batch', 'valid', 'judged', 'copyable_yes', 'proposed_tropes'],
}

const judged = (
  await inPairs(scan.batches, (b) =>
    agent(
      `${GOAL}\n\n` +
        `Judge batch ${b.batch} (${b.videos} videos, market ${b.market}).\n\n` +
        `Read, in this order:\n` +
        `- ${rubric} : the rubric. Follow it exactly; its enum values are checked by a script.\n` +
        `- ${vocabulary} : the trope vocabulary, name plus description.\n` +
        `- ${judging}/batch_${b.batch}.json : the videos, each with the number n printed on its thumbnail.\n` +
        `- ${judging}/batch_${b.batch}.jpg : the thumbnail sheet. Open it with the Read tool and look at every numbered thumbnail.\n\n` +
        `For each video, decide the look from its thumbnail, decide based_on from the thumbnail, title, channel name, length and tags together, ` +
        `then check the story against each vocabulary entry in turn before you name a trope.\n\n` +
        `Write ${judging}/batch_${b.batch}.judged.json as {"judgments": [...]}, one object per video, in the batch's order.\n` +
        `Then run:\n   ${judgingTool} validate --out "${out}" --batch ${b.batch}\n` +
        `It lists every problem. Fix the file and run it again until it prints "valid": true. ` +
        `Do not weaken a judgment to make it pass: when the check says a copyable video lacks a drawn look or an original source, the judgment was wrong, so correct the field that is wrong.\n\n` +
        `Return the counts from your final file. valid must be the validator's own last answer.`,
      { label: `judge ${b.batch}`, phase: 'Judge', model: 'sonnet', effort: 'medium', schema: JUDGED_SCHEMA },
    ),
  )
).filter(Boolean)
const invalid = scan.batches.filter((b) => !judged.some((j) => j.batch === b.batch && j.valid))
if (invalid.length) {
  return { ok: false, stage: 'Judge', error: `no valid judgment for batch ${invalid.map((b) => b.batch).join(', ')}`, out }
}
const candidates = judged.reduce((n, j) => n + j.copyable_yes, 0)
log(`judged ${judged.reduce((n, j) => n + j.judged, 0)} videos; ${candidates} look copyable before the existing-work check`)

phase('Existing-work check')
const checkedMarkets = [...new Set(scan.batches.map((b) => b.market))]
const checks = (
  await inPairs(checkedMarkets, (market) =>
    agent(
      `${GOAL}\n\n` +
        `You are the skeptic. A judge marked some ${market} videos as original stories we may copy the trope from. ` +
        `Copying from a video that is really an existing published work is the costly mistake, so try to prove each one is an existing work.\n\n` +
        `1. List the candidates:\n   ${judgingTool} candidates --out "${out}" --market ${market}\n` +
        `   If the list is empty, write {"checks": []} to the file in step 3 and return zeros.\n` +
        `2. Read the section "The existing-work check" in ${rubric}. For each candidate, search the web in the video's own language ` +
        `(load a web search tool with ToolSearch if you do not have one). Search the title's distinctive phrase, with and without words such as ` +
        `漫画, 小説, なろう, カクヨム, コミカライズ (jp) or 웹툰, 웹소설, 카카오페이지, 네이버 시리즈, 원작 (kr). ` +
        `A published manga, light novel, web novel, webtoon or anime with the same title or the same distinctive premise means existing_work "yes". ` +
        `A generic premise shared by many stories is not a match. Other videos by the same channel are not a published work.\n` +
        `   existing_work is "no" only when the searches ran and found nothing. If you could not search, or the results are thin or ambiguous, it is "unclear".\n` +
        `3. Write ${judging}/ip_check_${market}.json as {"checks": [{"video_id", "existing_work", "evidence"}]}, one entry per candidate. ` +
        `evidence is one sentence naming what you searched and what you found, with the work's name and a URL when existing_work is "yes".\n\n` +
        `Return the counts.`,
      {
        label: `existing-work check ${market}`,
        phase: 'Existing-work check',
        model: 'sonnet',
        effort: 'medium',
        schema: {
          type: 'object',
          properties: {
            market: { type: 'string' },
            checked: { type: 'integer' },
            existing_yes: { type: 'integer' },
            existing_no: { type: 'integer' },
            existing_unclear: { type: 'integer' },
          },
          required: ['market', 'checked', 'existing_yes', 'existing_no', 'existing_unclear'],
        },
      },
    ),
  )
).filter(Boolean)
log(checks.map((c) => `${c.market}: ${c.existing_no} cleared, ${c.existing_yes} existing works, ${c.existing_unclear} unclear`).join('; '))

phase('Merge')
const MERGE_SCHEMA = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    error: { type: 'string' },
    judged: { type: 'integer' },
    copyable_yes: { type: 'integer' },
    copyable_no: { type: 'integer' },
    copyable_unclear: { type: 'integer' },
    proposed_tropes: { type: 'array', items: { type: 'string' } },
    unjudged_kept_videos: { type: 'integer' },
    refetched_anything: { type: 'boolean' },
  },
  required: ['ok', 'judged', 'copyable_yes', 'copyable_no', 'copyable_unclear', 'proposed_tropes', 'unjudged_kept_videos'],
}
const mergePrompt =
  `${GOAL}\n\n` +
  `Merge the judgments and re-render the report. Run both commands exactly as written; do not edit any file.\n\n` +
  `1. ${judgingTool} merge --out "${out}"\n` +
  `   On success it prints a JSON summary. If it exits non-zero it names a file and lists what is wrong with it: ` +
  `return ok=false with that whole message as error, and zeros for the counts.\n` +
  `2. Only after step 1 succeeds, re-render the report from the cache:\n   ${scanCommand}\n` +
  `   Its "fetched this run" line should read search=0 suggest=0 enrich=0. Set refetched_anything to whether any of the three is above zero.\n\n` +
  `Return the summary's numbers unchanged.`
let merged = await agent(mergePrompt, { label: 'merge and render', model: 'sonnet', effort: 'low', schema: MERGE_SCHEMA })
if (merged && !merged.ok) {
  log(`merge refused: ${merged.error}`)
  await agent(
    `${GOAL}\n\n` +
      `The merge of this run's judgments was refused with this message:\n\n${merged.error}\n\n` +
      `Repair the file it names, and only that file, under ${judging}. Read ${rubric} first. ` +
      `A judged file (batch_*.judged.json) is repaired by redoing the judgments it reports as missing or invalid, from the batch's .json list and .jpg sheet, ` +
      `then running: ${judgingTool} validate --out "${out}" --batch <id> until it prints "valid": true. ` +
      `An existing-work file (ip_check_*.json) is repaired by doing the missing checks as that rubric section describes ` +
      `(candidates come from: ${judgingTool} candidates --out "${out}" --market <market>), and removing entries for videos that are not candidates. ` +
      `Never mark a video's existing_work "no" without having searched for it.`,
    { label: 'repair refused file', phase: 'Merge', model: 'sonnet', effort: 'medium' },
  )
  merged = await agent(mergePrompt, { label: 'merge and render (after repair)', phase: 'Merge', model: 'sonnet', effort: 'low', schema: MERGE_SCHEMA })
}
if (!merged || !merged.ok) return { ok: false, stage: 'Merge', error: merged ? merged.error : 'the merge agent did not finish', out }
log(`${merged.copyable_yes} copyable after the check, ${merged.copyable_unclear} unclear, ${merged.unjudged_kept_videos} kept videos unjudged`)

phase('Gap read')
const gap = await agent(
  `${GOAL}\n\n` +
    `Write the gap read for the person choosing the next show. They have not seen this run.\n\n` +
    `Read:\n` +
    `- ${out}/report.md : the funnel and the "Copyable tropes" table per market.\n` +
    `- ${out}/videos.csv : every kept video. The copyable ones have copyable = yes; trope, look, based_on, title_meaning and judgment_note are filled for judged rows only.\n` +
    `- ${judging}/proposed_tropes.json : tropes a judge proposed that are not in the vocabulary.\n` +
    `- ${vocabulary} : what each trope name means.\n\n` +
    `Compute every number with a short Python script over videos.csv; do not estimate from reading. ` +
    `Views per month is views / age in days x 30 from one snapshot, so it is a rate, not a total.\n\n` +
    `Write ${out}/gap_read.md with these parts, in this order:\n` +
    `1. The answer in three sentences or fewer: which tropes show the strongest demand from original drawn work in each market.\n` +
    `2. One table per market, one row per copyable trope, sorted by median views per month: videos, distinct channels, median views per month, ` +
    `median length in minutes, median channel subscribers, and the top video as a link with its English title meaning.\n` +
    `3. Demand against supply, per market: where few channels serve a trope that still earns high views per month, say so and name the numbers. ` +
    `Where many channels crowd a trope, say that.\n` +
    `4. What the winning videos share: words that recur in their titles, their lengths, what their thumbnails show (from title_meaning and judgment_note only).\n` +
    `5. What was dropped and why it matters: how many of the judged videos were re-uploads, recaps, fan works, photoreal originals or existing works caught by the check, per market.\n` +
    `6. Proposed tropes, each with its one-sentence description and its videos, for a person to accept into the vocabulary or reject.\n` +
    `7. Limits: ${merged.unjudged_kept_videos} kept videos were not judged; judgments come from thumbnails and titles, not from watching; ` +
    `the region setting is partial; and any trope with fewer than three videos is an example, not a measurement. Mark those rows in the tables.\n\n` +
    `Write plain declarative sentences. No sales language, no advice the numbers do not support, no recommendation to copy any specific video. ` +
    `Every claim carries its count.\n\n` +
    `Return the path, a two-sentence headline, and the top tropes per market.`,
  {
    label: 'write gap read',
    model: 'opus',
    effort: 'medium',
    schema: {
      type: 'object',
      properties: {
        path: { type: 'string' },
        headline: { type: 'string' },
        top_tropes: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              market: { type: 'string' },
              trope: { type: 'string' },
              videos: { type: 'integer' },
              channels: { type: 'integer' },
              median_views_per_month: { type: 'number' },
            },
            required: ['market', 'trope', 'videos', 'channels', 'median_views_per_month'],
          },
        },
      },
      required: ['path', 'headline', 'top_tropes'],
    },
  },
)

return {
  ok: true,
  out,
  kept: scan.kept,
  judged: merged.judged,
  not_judged: merged.unjudged_kept_videos,
  copyable: { yes: merged.copyable_yes, no: merged.copyable_no, unclear: merged.copyable_unclear },
  existing_work_checks: checks,
  proposed_tropes: merged.proposed_tropes,
  gap_read: gap ? gap.path : null,
  headline: gap ? gap.headline : null,
  top_tropes: gap ? gap.top_tropes : [],
}
