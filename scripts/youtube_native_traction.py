"""Rank recent YouTube videos the way a native Japanese or Korean viewer finds them, with the evidence for every row.

Searches YouTube in each market's language (InnerTube search pinned to the market's ``gl``/``hl``, seed terms plus
the native autocomplete suggestions for each seed), keeps videos published ``--min-age-days`` to ``--max-age-days``
ago, reads exact counts for each with ``yt-dlp``, filters with one ordered rule table, and scores the kept videos
within each market. No API key.

Only long-form landscape videos are kept: Shorts and vertical videos are excluded by the video's own width, height
and duration, whatever its length (many hour-long dubbed dramas are vertical).

This is the search-and-tabulate step of the team's demand-supply gap method: seeds are grouped by trope, each trope
is searched in normal order and sorted by views, ``--format-words`` adds the market's format words to every seed, and
the report reads demand (views per month) against supply (uploads, distinct channels) per trope. The ``look``,
``based_on``, ``trope`` and ``copyable`` columns are filled from ``<out>/judgments.csv`` once someone has looked at
the videos (empty until then), and the report then lists the copyable tropes; the ``recap_hint`` and
``paid_promo_hint`` tags are hints read from text, not verdicts.

The region pin is partial. InnerTube localizes results to ``gl``/``hl``, but some results still follow the caller's
IP, so a run from outside the market sees a blend of local and caller-region videos.

Every raw search, autocomplete and video response is cached under ``<out>/raw/``, and the first run's as-of time is
kept in ``<out>/run.json``. A rerun into the same ``--out`` fetches nothing it already has and writes the same tables.
Delete a file under ``raw/`` to refetch it (a cached ``enrich_failed`` video included).

Run::

    uv run python scripts/youtube_native_traction.py
    uv run python scripts/youtube_native_traction.py --market jp --seed 'AIドラマ' --pages 1 --expand 2 --out /tmp/yt

Writes ``videos.csv`` (kept, ranked), ``excluded.csv`` (every dropped video and its one reason), ``funnel.json`` and
``report.md`` to ``--out`` (default ``outputs/youtube_native_traction/<YYYY-MM-DD>/``). Needs ``yt-dlp`` on PATH.
"""

from __future__ import annotations

import argparse
import csv
import functools
import hashlib
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEEDS_FILE = Path(__file__).with_name("youtube_native_traction_seeds.json")
DAY_S = 86400.0
MINUTE_S = 60.0
HOUR_S = 3600.0
WEEK_S = 7 * DAY_S
MONTH_S = 30 * DAY_S
YEAR_S = 365 * DAY_S
ENRICH_BATCH = 8
SEARCH_GAP_S = 0.7
INNERTUBE_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
INNERTUBE_CLIENT_VERSION = "2.20261120.01.00"
SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
USER_AGENT = "Mozilla/5.0"
DUB_OR_IMPORT = "dub_or_import"
RECAP_HINT = "recap_hint"
PAID_PROMO_HINT = "paid_promo_hint"
OFFICIAL_OR_TRAILER_HINT = "official_or_trailer_hint"
HOWTO_OR_MUSIC_HINT = "howto_or_music_hint"
MONTH_DAYS = 30
TITLE_WORDS_SHOWN = 8


@dataclass(frozen=True)
class TagRule:
    """Terms that tag a video. A tag is a hint read from text, never a verdict."""

    terms: tuple[str, ...]
    title_only: bool = False


@dataclass(frozen=True)
class Market:
    key: str
    gl: str
    hl: str
    language_prefix: str
    native_script: re.Pattern[str]
    age_units: Mapping[str, float]
    format_words: tuple[str, ...]
    tags: Mapping[str, TagRule]


MARKETS: dict[str, Market] = {
    "jp": Market(
        key="jp",
        gl="JP",
        hl="ja",
        language_prefix="ja",
        # Kana only: kanji alone also matches Chinese titles.
        native_script=re.compile(r"[\u3040-\u30ff]"),
        age_units={
            "秒": 1.0,
            "分": MINUTE_S,
            "時間": HOUR_S,
            "日": DAY_S,
            "週間": WEEK_S,
            "か月": MONTH_S,
            "ヶ月": MONTH_S,
            "カ月": MONTH_S,
            "年": YEAR_S,
        },
        format_words=("漫画動画", "ボイスコミック", "アニメ"),
        tags={
            DUB_OR_IMPORT: TagRule(
                ("吹き替え", "吹替", "中国ドラマ", "中国ショートドラマ")
            ),
            # Title only: an original's own description often carries an あらすじ (synopsis) section.
            RECAP_HINT: TagRule(
                ("解説", "あらすじ", "ネタバレ", "要約"), title_only=True
            ),
            PAID_PROMO_HINT: TagRule(
                ("プロモーションを含みます", "PR", "タイアップ", "スポンサー")
            ),
            OFFICIAL_OR_TRAILER_HINT: TagRule(
                ("公式", "予告", "ティザー", "PV", "trailer", "teaser", "劇場公開"),
                title_only=True,
            ),
            HOWTO_OR_MUSIC_HINT: TagRule(
                (
                    "作り方",
                    "使い方",
                    "チュートリアル",
                    "講座",
                    "MV",
                    "歌ってみた",
                    "主題歌",
                    "OP",
                    "ED",
                ),
                title_only=True,
            ),
        },
    ),
    "kr": Market(
        key="kr",
        gl="KR",
        hl="ko",
        language_prefix="ko",
        native_script=re.compile(r"[\uac00-\ud7a3]"),
        age_units={
            "초": 1.0,
            "분": MINUTE_S,
            "시간": HOUR_S,
            "일": DAY_S,
            "주": WEEK_S,
            "개월": MONTH_S,
            "년": YEAR_S,
        },
        format_words=("애니", "웹툰", "만화", "모션 웹툰"),
        tags={
            DUB_OR_IMPORT: TagRule(("더빙", "중국드라마", "중국숏드라마", "중드")),
            # Title only: an original's own description often carries a 줄거리 (plot) section.
            RECAP_HINT: TagRule(
                ("리뷰", "요약", "결말포함", "줄거리"), title_only=True
            ),
            PAID_PROMO_HINT: TagRule(("유료광고", "광고포함", "협찬")),
            OFFICIAL_OR_TRAILER_HINT: TagRule(
                ("공식", "예고", "티저", "하이라이트", "PV", "trailer", "teaser"),
                title_only=True,
            ),
            HOWTO_OR_MUSIC_HINT: TagRule(
                (
                    "만들기",
                    "만드는법",
                    "만드는방법",
                    "튜토리얼",
                    "강의",
                    "MV",
                    "뮤비",
                    "OST",
                ),
                title_only=True,
            ),
        },
    ),
}


@dataclass(frozen=True)
class SearchPass:
    sp: str
    # YouTube's own relevance order. The views-sorted pass matches the query loosely, so its results are not.
    normal_order: bool


SEARCH_PASSES: dict[str, SearchPass] = {
    "relevance": SearchPass("EgQIBBAB", normal_order=True),
    "views": SearchPass("CAMSBAgEEAE=", normal_order=False),
}

Orientation = Literal["landscape", "portrait", "square", "unknown"]
QuerySource = Literal["seed", "format_word", "autocomplete"]
QUERY_SOURCES: tuple[QuerySource, ...] = ("seed", "format_word", "autocomplete")


@dataclass(frozen=True)
class Seed:
    text: str
    match: tuple[str, ...]
    group: str


@dataclass(frozen=True)
class Query:
    text: str
    seed: Seed
    market: str
    source: QuerySource


@dataclass(frozen=True)
class SearchHit:
    video_id: str
    market: str
    query: str
    seed: str
    group: str
    search_pass: str
    rank: int
    title: str
    age_text: str | None
    seen_at: datetime


@dataclass(frozen=True)
class VideoData:
    """The market-independent subset of one ``yt-dlp -j`` record."""

    video_id: str
    title: str
    description: str
    channel: str
    channel_id: str
    channel_verified: bool | None
    subscribers: int | None
    published_at: datetime
    duration_s: int | None
    width: int | None
    height: int | None
    views: int
    likes: int | None
    comments: int | None
    audio_language: str | None
    yt_tags: tuple[str, ...]
    live_status: str | None


@dataclass(frozen=True)
class EnrichFailure:
    video_id: str
    error: str


@dataclass(frozen=True)
class Video:
    market: str
    data: VideoData
    hits: tuple[SearchHit, ...]
    age_days: float
    title_native: bool
    labels: tuple[str, ...]

    @property
    def url(self) -> str:
        return video_url(self.data.video_id)

    @property
    def orientation(self) -> Orientation:
        w, h = self.data.width, self.data.height
        if not w or not h:
            return "unknown"
        return "landscape" if w > h else "portrait" if w < h else "square"

    @property
    def in_normal_order(self) -> bool:
        return any(SEARCH_PASSES[h.search_pass].normal_order for h in self.hits)

    @property
    def is_native(self) -> bool:
        language = (self.data.audio_language or "").lower()
        return self.title_native or language.startswith(
            MARKETS[self.market].language_prefix
        )


class Reason(StrEnum):
    ENRICH_FAILED = "enrich_failed"
    LIVE_OR_UPCOMING = "live_or_upcoming"
    OUTSIDE_WINDOW = "outside_window"
    SHORT_FORM = "short_form"
    NOT_LANDSCAPE = "not_landscape"
    BELOW_MIN_VIEWS = "below_min_views"
    NOT_NATIVE = "not_native"
    OFF_TOPIC = "off_topic"
    EXCLUDED_TAG = "excluded_tag"


class PrefilterDrop(StrEnum):
    TOO_YOUNG = "prefilter_too_young"
    TOO_OLD = "prefilter_too_old"


@dataclass(frozen=True)
class Verdict:
    reason: Reason | None
    detail: str = ""

    @property
    def kept(self) -> bool:
        return self.reason is None


@dataclass(frozen=True)
class Config:
    markets: tuple[str, ...]
    seeds: Mapping[str, tuple[Seed, ...]]
    expand: int
    format_words: bool
    pages: int
    min_age_days: int
    max_age_days: int
    min_duration_s: int
    min_views: int
    exclude_tags: frozenset[str]
    workers: int
    out: Path
    as_of: datetime


@dataclass(frozen=True)
class ScoredVideo:
    video: Video
    views_per_day: float
    views_per_sub: float | None
    like_rate: float | None
    comment_rate: float | None
    traction_score: float
    score_basis: str

    @property
    def views_per_month(self) -> float:
        return self.views_per_day * MONTH_DAYS

    @property
    def query_hits(self) -> int:
        return len({h.query for h in self.video.hits})

    @property
    def best_rank(self) -> int:
        return min(h.rank for h in self.video.hits)


@dataclass(frozen=True)
class Excluded:
    market: str
    video_id: str
    reason: str
    detail: str
    hits: tuple[SearchHit, ...]
    video: Video | None


SearchFetch = Callable[[Market, Mapping[str, Any]], Any]
SuggestFetch = Callable[[Market, str], Any]
EnrichFetch = Callable[[Sequence[str]], Mapping[str, Mapping[str, Any] | EnrichFailure]]


@dataclass(frozen=True)
class Clients:
    search: SearchFetch
    suggest: SuggestFetch
    enrich: EnrichFetch


class BotCheckError(RuntimeError):
    pass


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", fold(text))


_LATIN_WORD = re.compile(r"[a-z0-9]+")


def has_term(term: str, folded: str) -> bool:
    """Whether ``term`` occurs in ``folded`` text. A Latin term such as ``PR`` matches only as a whole word."""
    t = fold(term)
    if _LATIN_WORD.fullmatch(t):
        return (
            re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", folded) is not None
        )
    return normalize(t) in normalize(folded)


def tag_video(market: Market, data: VideoData) -> tuple[str, ...]:
    title = fold(data.title)
    everything = fold(" ".join((data.title, data.description, *data.yt_tags)))
    return tuple(
        tag
        for tag, rule in market.tags.items()
        if any(
            has_term(term, title if rule.title_only else everything)
            for term in rule.terms
        )
    )


@functools.cache
def _age_pattern(market_key: str) -> re.Pattern[str]:
    units = sorted(MARKETS[market_key].age_units, key=len, reverse=True)
    return re.compile(r"(\d+)\s*(" + "|".join(map(re.escape, units)) + ")")


def parse_age_seconds(market: Market, text: str | None) -> float | None:
    """Lower bound of a localized relative age such as ``2 週間前`` or ``스트리밍 시간: 23시간 전``."""
    if not text:
        return None
    m = _age_pattern(market.key).search(text)
    return int(m.group(1)) * market.age_units[m.group(2)] if m else None


def _walk(node: Any, keys: frozenset[str]) -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys:
                yield k, v
            else:
                yield from _walk(v, keys)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, keys)


@dataclass(frozen=True)
class PageItem:
    video_id: str
    title: str
    age_text: str | None


def parse_search_page(raw: Any) -> tuple[list[PageItem], str | None]:
    items: list[PageItem] = []
    token: str | None = None
    for key, node in _walk(
        raw, frozenset({"videoRenderer", "continuationItemRenderer"})
    ):
        if key == "videoRenderer":
            title = "".join(
                r.get("text", "") for r in node.get("title", {}).get("runs", [])
            )
            age = node.get("publishedTimeText", {}).get("simpleText")
            items.append(PageItem(node["videoId"], title, age))
        elif token is None:
            token = (
                node.get("continuationEndpoint", {})
                .get("continuationCommand", {})
                .get("token")
            )
    return items, token


def parse_suggestions(raw: Any) -> list[str]:
    return (
        [s for s in raw[1] if isinstance(s, str)]
        if isinstance(raw, list) and len(raw) > 1
        else []
    )


def _int_or_none(value: Any) -> int | None:
    return (
        int(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else None
    )


YTDLP_KEYS = (
    "id",
    "title",
    "description",
    "channel",
    "channel_id",
    "channel_is_verified",
    "channel_follower_count",
    "timestamp",
    "release_timestamp",
    "upload_date",
    "duration",
    "width",
    "height",
    "view_count",
    "like_count",
    "comment_count",
    "language",
    "tags",
    "live_status",
    "was_live",
)


def ytdlp_subset(record: Mapping[str, Any]) -> dict[str, Any]:
    return {k: record.get(k) for k in YTDLP_KEYS}


def parse_ytdlp(record: Mapping[str, Any]) -> VideoData | EnrichFailure:
    video_id = str(record["id"])
    ts = _int_or_none(record.get("timestamp")) or _int_or_none(
        record.get("release_timestamp")
    )
    if ts is not None:
        published = datetime.fromtimestamp(ts, UTC)
    elif isinstance(record.get("upload_date"), str):
        published = datetime.strptime(record["upload_date"], "%Y%m%d").replace(
            tzinfo=UTC
        )
    else:
        return EnrichFailure(video_id, "yt-dlp record has no publish time")
    return VideoData(
        video_id=video_id,
        title=record.get("title") or "",
        description=record.get("description") or "",
        channel=record.get("channel") or "",
        channel_id=record.get("channel_id") or "",
        channel_verified=verified
        if isinstance(verified := record.get("channel_is_verified"), bool)
        else None,
        subscribers=_int_or_none(record.get("channel_follower_count")),
        published_at=published,
        duration_s=_int_or_none(record.get("duration")),
        width=_int_or_none(record.get("width")),
        height=_int_or_none(record.get("height")),
        views=_int_or_none(record.get("view_count")) or 0,
        likes=_int_or_none(record.get("like_count")),
        comments=_int_or_none(record.get("comment_count")),
        audio_language=record.get("language") or None,
        yt_tags=tuple(record.get("tags") or ()),
        live_status=record.get("live_status"),
    )


def build_video(
    market: Market, data: VideoData, hits: Sequence[SearchHit], as_of: datetime
) -> Video:
    return Video(
        market=market.key,
        data=data,
        hits=tuple(hits),
        age_days=(as_of - data.published_at).total_seconds() / DAY_S,
        title_native=bool(market.native_script.search(data.title)),
        labels=tag_video(market, data),
    )


def _topic_key(text: str) -> str:
    return normalize(text).replace("#", "")


def term_match(video: Video, cfg: Config) -> bool:
    """Whether the video's own text names a match term of a seed that surfaced it. ``#AI #ドラマ`` reads as one term."""
    seeds = {s.text: s for s in cfg.seeds.get(video.market, ())}
    terms = {t for h in video.hits if h.seed in seeds for t in seeds[h.seed].match}
    haystack = _topic_key(
        " ".join((video.data.title, video.data.description, *video.data.yt_tags))
    )
    return any(_topic_key(t) in haystack for t in terms)


def on_topic(video: Video, cfg: Config) -> bool:
    """Relevant when YouTube ranked it in normal order for a query, or its own text names the topic.

    A literal term match alone dropped real demand on the first live run (an ``AI Drama`` titled in English, an
    ``#AI #ショートドラマ`` Short), while results that only the views-sorted pass returns were about half unrelated.
    """
    return video.in_normal_order or term_match(video, cfg)


RULES: tuple[tuple[Reason, Callable[[Video, Config], bool]], ...] = (
    (
        Reason.LIVE_OR_UPCOMING,
        lambda v, c: v.data.live_status in {"is_live", "is_upcoming"},
    ),
    (
        Reason.OUTSIDE_WINDOW,
        lambda v, c: not c.min_age_days <= math.floor(v.age_days) <= c.max_age_days,
    ),
    (
        Reason.SHORT_FORM,
        lambda v, c: v.data.duration_s is None or v.data.duration_s < c.min_duration_s,
    ),
    # Unknown dimensions fail too: a video is kept only when it is known to be landscape.
    (Reason.NOT_LANDSCAPE, lambda v, c: v.orientation != "landscape"),
    (Reason.BELOW_MIN_VIEWS, lambda v, c: v.data.views < c.min_views),
    (Reason.NOT_NATIVE, lambda v, c: not v.is_native),
    (Reason.OFF_TOPIC, lambda v, c: not on_topic(v, c)),
    (Reason.EXCLUDED_TAG, lambda v, c: bool(set(v.labels) & c.exclude_tags)),
)


def judge(subject: Video | EnrichFailure, cfg: Config) -> Verdict:
    """First failing rule wins. ``enrich_failed`` comes first and is decided by type: there is no Video to test."""
    if isinstance(subject, EnrichFailure):
        return Verdict(Reason.ENRICH_FAILED, subject.error)
    for reason, fails in RULES:
        if fails(subject, cfg):
            return Verdict(reason)
    return Verdict(None)


def prefilter(
    market: Market, hits: Sequence[SearchHit], cfg: Config
) -> PrefilterDrop | None:
    """Drop only what is certainly outside the window, judged from the search card's rounded relative age.

    A card age is a lower bound rounded down by one unit, so the young cut keeps a two-day margin (with the default
    window it drops 5 days or less) and the old cut drops only ages past ``max + 1`` days (months and years).
    """
    for hit in hits:
        seconds = parse_age_seconds(market, hit.age_text)
        if seconds is None:
            continue
        days = (seconds + (cfg.as_of - hit.seen_at).total_seconds()) / DAY_S
        if days <= cfg.min_age_days - 2:
            return PrefilterDrop.TOO_YOUNG
        if days > cfg.max_age_days + 1:
            return PrefilterDrop.TOO_OLD
        return None
    return None


def percentile_ranks(values: Sequence[float]) -> list[float]:
    """Mid-rank percentile in [0, 1]: share of values below plus half the share equal (itself included)."""
    n = len(values)
    return [
        (sum(v < x for v in values) + 0.5 * sum(v == x for v in values)) / n
        for x in values
    ]


def _rate(part: int | None, whole: int) -> float | None:
    return part / whole if part is not None and whole > 0 else None


def score_cohort(videos: Sequence[Video]) -> list[ScoredVideo]:
    vpd = [v.data.views / max(v.age_days, 1e-9) for v in videos]
    vps = [
        v.data.views / v.data.subscribers if v.data.subscribers else None
        for v in videos
    ]
    vpd_pct = percentile_ranks(vpd)
    present = [i for i, x in enumerate(vps) if x is not None]
    vps_pct = dict(
        zip(
            present,
            percentile_ranks([vps[i] for i in present if vps[i] is not None]),
            strict=True,
        )
    )
    scored = []
    for i, v in enumerate(videos):
        if i in vps_pct:
            score, basis = (
                100 * (vpd_pct[i] + vps_pct[i]) / 2,
                "views_per_day+views_per_sub",
            )
        else:
            score, basis = 100 * vpd_pct[i], "views_per_day_only"
        scored.append(
            ScoredVideo(
                video=v,
                views_per_day=vpd[i],
                views_per_sub=vps[i],
                like_rate=_rate(v.data.likes, v.data.views),
                comment_rate=_rate(v.data.comments, v.data.views),
                traction_score=score,
                score_basis=basis,
            )
        )
    return sorted(
        scored,
        key=lambda s: (-s.traction_score, -s.views_per_day, s.video.data.video_id),
    )


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 3:
        return None
    rx, ry = _average_ranks(xs), _average_ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (sx * sy) if sx and sy else None


class Cache:
    """Raw responses on disk, written by temp-file rename so a crash never leaves a half file."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, kind: str, name: str) -> Path:
        return self.root / kind / f"{name}.json"

    def get(self, kind: str, name: str) -> Any | None:
        p = self.path(kind, name)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put(self, kind: str, name: str, value: Any) -> None:
        p = self.path(kind, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(p)


def _key(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()[:12]


@dataclass
class Counters:
    search_fetched: int = 0
    suggest_fetched: int = 0
    enrich_fetched: int = 0


def build_queries(
    market: Market,
    seeds: Sequence[Seed],
    cfg: Config,
    cache: Cache,
    clients: Clients,
    n: Counters,
) -> list[Query]:
    queries: list[Query] = []
    seen: set[str] = set()
    candidates = [Query(s.text, s, market.key, "seed") for s in seeds]
    if cfg.format_words:
        candidates += [
            Query(f"{s.text} {w}", s, market.key, "format_word")
            for s in seeds
            for w in market.format_words
        ]
    for query in candidates:
        if normalize(query.text) not in seen:
            seen.add(normalize(query.text))
            queries.append(query)
    for seed in seeds if cfg.expand > 0 else ():
        name = f"{market.key}__{_key(seed.text)}"
        envelope = cache.get("suggest", name)
        if envelope is None:
            envelope = {
                "query": seed.text,
                "market": market.key,
                "response": clients.suggest(market, seed.text),
            }
            cache.put("suggest", name, envelope)
            n.suggest_fetched += 1
        added = 0
        for suggestion in parse_suggestions(envelope["response"]):
            if added >= cfg.expand:
                break
            if normalize(suggestion) in seen:
                continue
            seen.add(normalize(suggestion))
            queries.append(Query(suggestion, seed, market.key, "autocomplete"))
            added += 1
    return queries


def _client_context(market: Market) -> dict[str, Any]:
    return {
        "client": {
            "clientName": "WEB",
            "clientVersion": INNERTUBE_CLIENT_VERSION,
            "hl": market.hl,
            "gl": market.gl,
        }
    }


def search_query(
    market: Market,
    query: Query,
    cfg: Config,
    cache: Cache,
    clients: Clients,
    n: Counters,
) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for pass_name, search_pass in SEARCH_PASSES.items():
        token: str | None = None
        rank = 0
        for page in range(cfg.pages):
            if page and token is None:
                break
            name = f"{market.key}__{pass_name}__{_key(query.text)}__p{page + 1}"
            envelope = cache.get("search", name)
            if envelope is None:
                body: dict[str, Any] = {"context": _client_context(market)}
                body.update(
                    {"continuation": token}
                    if token
                    else {"query": query.text, "params": search_pass.sp}
                )
                fetched_at = datetime.now(UTC)
                envelope = {
                    "fetched_at": fetched_at.isoformat(),
                    "query": query.text,
                    "pass": pass_name,
                    "page": page + 1,
                    "request": body,
                    "response": clients.search(market, body),
                }
                cache.put("search", name, envelope)
                n.search_fetched += 1
            items, token = parse_search_page(envelope["response"])
            seen_at = datetime.fromisoformat(envelope["fetched_at"])
            for item in items:
                rank += 1
                hits.append(
                    SearchHit(
                        video_id=item.video_id,
                        market=market.key,
                        query=query.text,
                        seed=query.seed.text,
                        group=query.seed.group,
                        search_pass=pass_name,
                        rank=rank,
                        title=item.title,
                        age_text=item.age_text,
                        seen_at=seen_at,
                    )
                )
    return hits


def enrich_all(
    ids: Sequence[str], cfg: Config, cache: Cache, clients: Clients, n: Counters
) -> dict[str, VideoData | EnrichFailure]:
    def load(video_id: str, cached: Mapping[str, Any]) -> VideoData | EnrichFailure:
        if "error" in cached:
            return EnrichFailure(video_id, cached["error"])
        return parse_ytdlp(cached)

    out: dict[str, VideoData | EnrichFailure] = {}
    missing = []
    for video_id in ids:
        cached = cache.get("videos", video_id)
        if cached is None:
            missing.append(video_id)
        else:
            out[video_id] = load(video_id, cached)
    batches = [
        missing[i : i + ENRICH_BATCH] for i in range(0, len(missing), ENRICH_BATCH)
    ]
    with ThreadPoolExecutor(max_workers=max(cfg.workers, 1)) as pool:
        futures = {pool.submit(clients.enrich, batch): batch for batch in batches}
        for future in as_completed(futures):
            results = future.result()
            for video_id in futures[future]:
                n.enrich_fetched += 1
                result = results.get(video_id) or EnrichFailure(
                    video_id, "yt-dlp returned nothing for this id"
                )
                record = (
                    {"id": video_id, "error": result.error}
                    if isinstance(result, EnrichFailure)
                    else result
                )
                cache.put("videos", video_id, record)
                out[video_id] = load(video_id, record)
    return out


@dataclass(frozen=True)
class MarketResult:
    market: Market
    queries: tuple[Query, ...]
    hits: tuple[SearchHit, ...]
    kept: tuple[ScoredVideo, ...]
    excluded: tuple[Excluded, ...]
    funnel: dict[str, Any]


def _group_hits(hits: Sequence[SearchHit]) -> dict[str, list[SearchHit]]:
    grouped: dict[str, list[SearchHit]] = defaultdict(list)
    for h in hits:
        grouped[h.video_id].append(h)
    return grouped


def run(cfg: Config, clients: Clients) -> tuple[list[MarketResult], Counters]:
    cache = Cache(cfg.out / "raw")
    n = Counters()
    staged = []
    for key in cfg.markets:
        market = MARKETS[key]
        queries = build_queries(market, cfg.seeds[key], cfg, cache, clients, n)
        hits = [
            h for q in queries for h in search_query(market, q, cfg, cache, clients, n)
        ]
        grouped = _group_hits(hits)
        drops = {vid: prefilter(market, hs, cfg) for vid, hs in grouped.items()}
        staged.append((market, queries, hits, grouped, drops))

    to_enrich = sorted(
        {vid for *_, grouped, drops in staged for vid in grouped if drops[vid] is None}
    )
    enriched = enrich_all(to_enrich, cfg, cache, clients, n)

    results = []
    for market, queries, hits, grouped, drops in staged:
        excluded: list[Excluded] = []
        kept: list[Video] = []
        for vid in sorted(grouped):
            hs = tuple(grouped[vid])
            drop = drops[vid]
            if drop is not None:
                excluded.append(
                    Excluded(
                        market.key, vid, drop.value, hs[0].age_text or "", hs, None
                    )
                )
                continue
            outcome = enriched[vid]
            subject = (
                build_video(market, outcome, hs, cfg.as_of)
                if isinstance(outcome, VideoData)
                else outcome
            )
            verdict = judge(subject, cfg)
            video = subject if isinstance(subject, Video) else None
            if verdict.reason is None and video is not None:
                kept.append(video)
            else:
                excluded.append(
                    Excluded(
                        market.key, vid, str(verdict.reason), verdict.detail, hs, video
                    )
                )
        scored = score_cohort(kept)
        funnel = build_funnel(market, queries, hits, grouped, drops, excluded, scored)
        results.append(
            MarketResult(
                market,
                tuple(queries),
                tuple(hits),
                tuple(scored),
                tuple(excluded),
                funnel,
            )
        )
    return results, n


def build_funnel(
    market: Market,
    queries: Sequence[Query],
    hits: Sequence[SearchHit],
    grouped: Mapping[str, Sequence[SearchHit]],
    drops: Mapping[str, PrefilterDrop | None],
    excluded: Sequence[Excluded],
    scored: Sequence[ScoredVideo],
) -> dict[str, Any]:
    reasons = Counter(e.reason for e in excluded)
    source = Counter(q.source for q in queries)
    return {
        "queries": {s: source[s] for s in QUERY_SOURCES},
        "search_hits": len(hits),
        "unique_videos": len(grouped),
        "prefilter_dropped": {d.value: reasons[d.value] for d in PrefilterDrop},
        "enriched": sum(1 for d in drops.values() if d is None),
        "excluded": {r.value: reasons[r.value] for r in Reason},
        "kept": len(scored),
        "kept_tagged": {
            tag: sum(1 for s in scored if tag in s.video.labels) for tag in market.tags
        },
    }


def funnel_balances(f: Mapping[str, Any]) -> bool:
    return (
        f["unique_videos"] == sum(f["prefilter_dropped"].values()) + f["enriched"]
        and f["enriched"] == sum(f["excluded"].values()) + f["kept"]
    )


def _fmt(x: float | None, digits: int = 4) -> str:
    return "" if x is None else f"{x:.{digits}f}"


def _join(values: Sequence[str]) -> str:
    return " | ".join(sorted(set(values)))


def _video_fields(video: Video | None, cfg: Config) -> dict[str, Any]:
    if video is None:
        return {}
    d = video.data
    return {
        "term_match": term_match(video, cfg),
        "title": d.title,
        "channel": d.channel,
        "channel_id": d.channel_id,
        "channel_verified": "" if d.channel_verified is None else d.channel_verified,
        "subscribers": "" if d.subscribers is None else d.subscribers,
        "published": d.published_at.strftime("%Y-%m-%d"),
        "age_days": _fmt(video.age_days, 2),
        "duration_s": "" if d.duration_s is None else d.duration_s,
        "width": d.width or "",
        "height": d.height or "",
        "orientation": video.orientation,
        "views": d.views,
        "likes": "" if d.likes is None else d.likes,
        "comments": "" if d.comments is None else d.comments,
        "title_native": video.title_native,
        "audio_language": d.audio_language or "",
        "tags": _join(video.labels),
    }


JUDGMENT_COLUMNS = [
    "title_meaning",
    "look",
    "based_on",
    "trope",
    "copyable",
    "judgment_note",
]
JUDGMENTS_FILE = "judgments.csv"
Judgments = Mapping[str, Mapping[str, str]]


def load_judgments(out: Path) -> dict[str, dict[str, str]]:
    """The judgment pass's answers from ``<out>/judgments.csv``, keyed by video id; empty until that pass has run.

    The file has a ``video_id`` column plus the judgment columns. ``copyable`` is ``yes`` for a drawn-look original
    whose trope can be rebuilt in a world of our own. Whoever looked at the videos writes it; this script only reads.
    """
    path = out / JUDGMENTS_FILE
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {
            row["video_id"]: {c: (row.get(c) or "").strip() for c in JUDGMENT_COLUMNS}
            for row in csv.DictReader(fh)
        }


VIDEO_COLUMNS = [
    "market", "rank", "traction_score", "score_basis", "views_per_month", "views_per_day",
    "views_per_sub", "like_rate", "comment_rate", "query_hits", "best_rank", "url", "title", *JUDGMENT_COLUMNS,
    "channel", "channel_id", "channel_verified", "subscribers", "published", "age_days", "duration_s", "width", "height", "orientation", "views",
    "likes", "comments", "title_native", "audio_language", "tags", "term_match", "surfaced_by", "groups", "seeds", "queries", "thumbnail_url",
]  # fmt: skip
EXCLUDED_COLUMNS = [
    "market", "reason", "detail", "url", "title", "channel", "channel_id", "subscribers", "published", "age_days",
    "duration_s", "width", "height", "orientation", "views", "likes", "comments", "title_native", "audio_language", "tags", "term_match",
    "surfaced_by", "age_text", "seeds", "queries",
]  # fmt: skip


def _write_csv(
    path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def video_rows(
    results: Sequence[MarketResult], cfg: Config, judgments: Judgments
) -> list[dict[str, Any]]:
    rows = []
    for r in results:
        for rank, s in enumerate(r.kept, 1):
            rows.append(
                {
                    "market": r.market.key,
                    "rank": rank,
                    "traction_score": _fmt(s.traction_score, 1),
                    "score_basis": s.score_basis,
                    "views_per_month": _fmt(s.views_per_month, 0),
                    "views_per_day": _fmt(s.views_per_day, 1),
                    "views_per_sub": _fmt(s.views_per_sub, 3),
                    "like_rate": _fmt(s.like_rate),
                    "comment_rate": _fmt(s.comment_rate),
                    "query_hits": s.query_hits,
                    "best_rank": s.best_rank,
                    "url": s.video.url,
                    **_video_fields(s.video, cfg),
                    **{
                        c: judgments.get(s.video.data.video_id, {}).get(c, "")
                        for c in JUDGMENT_COLUMNS
                    },
                    "surfaced_by": _join([h.search_pass for h in s.video.hits]),
                    "groups": _join([h.group for h in s.video.hits]),
                    "seeds": _join([h.seed for h in s.video.hits]),
                    "queries": _join([h.query for h in s.video.hits]),
                    "thumbnail_url": f"https://i.ytimg.com/vi/{s.video.data.video_id}/hqdefault.jpg",
                }
            )
    return rows


@dataclass(frozen=True)
class GroupStat:
    """One trope's demand (views per month) and supply (uploads, channels) inside the window."""

    group: str
    uploads: int
    channels: int
    median_views_per_month: float
    top: ScoredVideo | None
    tag_share: Mapping[str, float]
    title_words: tuple[tuple[str, int], ...]


_TITLE_SPLIT = re.compile(r"[\s【】「」『』|｜#]+")


def title_words(videos: Sequence[ScoredVideo]) -> list[tuple[str, int]]:
    """Title words used by two or more channels among ``videos``, with the channel count, most shared first.

    Counted per channel, not per title, so one channel's series cannot fill the list with its own name.
    """
    channels: dict[str, set[str]] = defaultdict(set)
    for s in videos:
        d = s.video.data
        for word in _TITLE_SPLIT.split(fold(d.title)):
            if len(word) > 1:
                channels[word].add(d.channel_id or d.channel)
    repeated = [(w, len(cs)) for w, cs in channels.items() if len(cs) >= 2]
    return sorted(repeated, key=lambda wc: (-wc[1], wc[0]))[:TITLE_WORDS_SHOWN]


def group_rollup(
    kept: Sequence[ScoredVideo], groups: Sequence[str], tags: Sequence[str]
) -> list[GroupStat]:
    """Per search group, over one market's ``kept`` videos, sorted by median views per month."""
    return _rollup(
        {
            g: [s for s in kept if any(h.group == g for h in s.video.hits)]
            for g in groups
        },
        tags,
    )


def trope_rollup(
    kept: Sequence[ScoredVideo], judgments: Judgments, tags: Sequence[str]
) -> list[GroupStat]:
    """Per judged trope, over the kept videos the judgment pass marked copyable."""
    by_trope: dict[str, list[ScoredVideo]] = defaultdict(list)
    for s in kept:
        judged = judgments.get(s.video.data.video_id, {})
        if judged.get("copyable") == "yes" and judged.get("trope"):
            by_trope[judged["trope"]].append(s)
    return _rollup(by_trope, tags)


def _rollup(
    members: Mapping[str, Sequence[ScoredVideo]], tags: Sequence[str]
) -> list[GroupStat]:
    stats = []
    for group, unsorted in members.items():
        vs = sorted(unsorted, key=lambda s: (-s.views_per_month, s.video.data.video_id))
        winners = vs[: math.ceil(len(vs) / 4)]
        stats.append(
            GroupStat(
                group=group,
                uploads=len(vs),
                channels=len(
                    {s.video.data.channel_id or s.video.data.channel for s in vs}
                ),
                median_views_per_month=statistics.median(s.views_per_month for s in vs)
                if vs
                else 0.0,
                top=vs[0] if vs else None,
                tag_share={
                    t: sum(t in s.video.labels for s in vs) / len(vs) if vs else 0.0
                    for t in tags
                },
                title_words=tuple(title_words(winners)),
            )
        )
    return sorted(stats, key=lambda g: (-g.median_views_per_month, -g.uploads, g.group))


def excluded_rows(results: Sequence[MarketResult], cfg: Config) -> list[dict[str, Any]]:
    order = {r.value: i for i, r in enumerate([*PrefilterDrop, *Reason])}
    rows = []
    for r in results:
        for e in sorted(r.excluded, key=lambda e: (order[e.reason], e.video_id)):
            rows.append(
                {
                    "market": e.market,
                    "reason": e.reason,
                    "detail": e.detail,
                    "url": video_url(e.video_id),
                    "title": e.hits[0].title,
                    **_video_fields(e.video, cfg),
                    "surfaced_by": _join([h.search_pass for h in e.hits]),
                    "age_text": next((h.age_text for h in e.hits if h.age_text), ""),
                    "seeds": _join([h.seed for h in e.hits]),
                    "queries": _join([h.query for h in e.hits]),
                }
            )
    return rows


def _length(seconds: int | None) -> str:
    if seconds is None:
        return ""
    hours, rest = divmod(seconds, 3600)
    return (
        f"{hours}:{rest // 60:02d}:{rest % 60:02d}"
        if hours
        else f"{rest // 60}:{rest % 60:02d}"
    )


def _md(text: str) -> str:
    return (
        text.replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("\n", " ")
    )


def _num(x: float | None, digits: int = 0) -> str:
    return "" if x is None else f"{x:,.{digits}f}"


def render_report(
    results: Sequence[MarketResult], cfg: Config, judgments: Judgments
) -> str:
    lines = [
        "# YouTube native traction",
        "",
        f"As of {cfg.as_of.isoformat()}. Window: published {cfg.min_age_days} to {cfg.max_age_days} days ago "
        f"(whole days). Long-form landscape only: at least {cfg.min_duration_s} s long and wider than tall. Minimum views: {cfg.min_views:,}. Search pages per query and pass: {cfg.pages}. "
        f"Autocomplete expansion: {cfg.expand} per seed. Format-word queries: {'on' if cfg.format_words else 'off'}.",
        "",
        "Views per month is views / age in days x 30. Every video here is under a month old, so it is a rate, "
        "not a count the video has reached.",
        "",
        f"`{RECAP_HINT}` and `{PAID_PROMO_HINT}` are hints read from titles and descriptions, and `{DUB_OR_IMPORT}` "
        "is read the same way. None of them is a verdict; `look` and `based_on` in videos.csv are empty until "
        "someone has looked at the video.",
        "",
        "The region pin is partial. Searches send the market's `gl`/`hl`, and YouTube localizes most results to it, "
        "but some results still follow the caller's IP. Treat a run from outside the market as a blend.",
        "",
        "In the trope tables, a video counts under every trope whose queries surfaced it. Winning titles are the "
        "top quarter by views/month, and a title word is listed with the number of winning channels that use it "
        "(two or more).",
        "",
        "A video is on topic when YouTube returned it in normal order for a query, or its own title, description "
        "or tags name the topic. A video that only the views-sorted search returned and that never names the topic "
        "is excluded as `off_topic`. `term_match` and `surfaced_by` in both CSVs let you tighten or loosen this.",
        "",
        "`traction_score` is 100 x the mean of a video's within-market percentile ranks of views/month and "
        "views/subscriber (views/month alone when subscribers are hidden; see `score_basis` in videos.csv).",
    ]
    for r in results:
        m, f = r.market, r.funnel
        lines += [
            "",
            f"## {m.key.upper()} (gl={m.gl}, hl={m.hl})",
            "",
            "### Funnel",
            "",
            "| step | count |",
            "|---|---|",
        ]
        lines += [
            f"| queries: {source} | {count} |" for source, count in f["queries"].items()
        ]
        lines += [
            f"| search hits | {f['search_hits']} |",
            f"| unique videos | {f['unique_videos']} |",
        ]
        lines += [f"| dropped: {k} | {v} |" for k, v in f["prefilter_dropped"].items()]
        lines += [f"| enriched | {f['enriched']} |"]
        lines += [f"| excluded: {k} | {v} |" for k, v in f["excluded"].items()]
        lines += [f"| kept | {f['kept']} |"]
        lines += [
            f"| kept and tagged {tag} | {count} |"
            for tag, count in f["kept_tagged"].items()
        ]
        groups = list(dict.fromkeys(seed.group for seed in cfg.seeds[m.key]))
        lines += ["", "### Demand and supply by trope", ""]
        if r.kept:
            lines += [
                "| trope | uploads | channels | median views/month | top video | its views/month | dub or import | "
                "recap hint | title words shared by winning channels |",
                "|---|---|---|---|---|---|---|---|---|",
            ]
            for g in group_rollup(r.kept, groups, list(m.tags)):
                top = (
                    f"[{_md(g.top.video.data.title[:40])}]({g.top.video.url})"
                    if g.top
                    else ""
                )
                words = ", ".join(f"{_md(w)} ({c})" for w, c in g.title_words)
                lines.append(
                    f"| {_md(g.group)} | {g.uploads} | {g.channels} | {_num(g.median_views_per_month)} | {top} | "
                    f"{_num(g.top.views_per_month) if g.top else ''} | {g.tag_share[DUB_OR_IMPORT]:.0%} | "
                    f"{g.tag_share[RECAP_HINT]:.0%} | {words} |"
                )
        else:
            lines.append("None kept.")
        lines += ["", "### Top 15", ""]
        if r.kept:
            lines += [
                "| # | title | channel | subs | age (d) | length | views | views/month | views/sub | like rate | tags "
                "| score |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|",
            ]
            for i, s in enumerate(r.kept[:15], 1):
                d = s.video.data
                lines.append(
                    f"| {i} | [{_md(d.title[:70])}]({s.video.url}) | {_md(d.channel)} | {_num(d.subscribers)} | "
                    f"{math.floor(s.video.age_days)} | {_length(d.duration_s)} | {d.views:,} | {_num(s.views_per_month)} | "
                    f"{_num(s.views_per_sub, 2)} | {_fmt(s.like_rate)} | {_join(s.video.labels)} | "
                    f"{s.traction_score:.1f} |"
                )
        else:
            lines.append("None kept.")
        if judgments:
            judged = [s for s in r.kept if s.video.data.video_id in judgments]
            tropes = trope_rollup(r.kept, judgments, list(m.tags))
            lines += [
                "",
                "### Copyable tropes",
                "",
                f"Judged {len(judged)} of {len(r.kept)} kept videos; {sum(g.uploads for g in tropes)} are drawn-look "
                "originals with a trope that can be rebuilt in a world of our own. `look`, `based_on` and `trope` "
                f"come from `{JUDGMENTS_FILE}`, written by whoever looked at the videos.",
                "",
            ]
            if tropes:
                lines += [
                    "| trope | uploads | channels | median views/month | top video | its views/month |",
                    "|---|---|---|---|---|---|",
                ]
                for g in tropes:
                    top = (
                        f"[{_md(g.top.video.data.title[:40])}]({g.top.video.url})"
                        if g.top
                        else ""
                    )
                    lines.append(
                        f"| {_md(g.group)} | {g.uploads} | {g.channels} | {_num(g.median_views_per_month)} | {top} | "
                        f"{_num(g.top.views_per_month) if g.top else ''} |"
                    )
        lines += ["", "### Repeat channels", ""]
        by_channel: dict[str, list[ScoredVideo]] = defaultdict(list)
        for s in r.kept:
            by_channel[s.video.data.channel_id or s.video.data.channel].append(s)
        repeats = sorted(
            (vs for vs in by_channel.values() if len(vs) >= 2),
            key=lambda vs: (
                -len(vs),
                -statistics.median(s.views_per_month for s in vs),
            ),
        )
        if repeats:
            lines += [
                "| channel | subs | kept videos | median views/month |",
                "|---|---|---|---|",
            ]
            for vs in repeats:
                med = statistics.median(s.views_per_month for s in vs)
                d = vs[0].video.data
                lines.append(
                    f"| {_md(d.channel)} | {_num(d.subscribers)} | {len(vs)} | {med:,.0f} |"
                )
        else:
            lines.append("No channel has two or more kept videos.")
        lines += ["", "### How far to trust these numbers", ""]
        rho = spearman(
            [s.video.age_days for s in r.kept], [s.views_per_day for s in r.kept]
        )
        note = (
            " Fewer than 10 videos, so percentiles move in coarse steps."
            if len(r.kept) < 10
            else ""
        )
        rho_text = (
            "not computable (fewer than 3 videos or no spread)"
            if rho is None
            else f"{rho:+.2f}"
        )
        lines.append(
            f"n = {len(r.kept)}.{note} Spearman rho between age_days and views/month: {rho_text}. "
            "Views front-load, so views/month favours younger videos; a strongly negative rho means the score "
            "leans on recency in this market."
        )
    return "\n".join(lines) + "\n"


def write_outputs(
    results: Sequence[MarketResult], cfg: Config, n: Counters
) -> dict[str, Path]:
    cfg.out.mkdir(parents=True, exist_ok=True)
    paths = {
        name: cfg.out / name
        for name in ("videos.csv", "excluded.csv", "funnel.json", "report.md")
    }
    judgments = load_judgments(cfg.out)
    _write_csv(paths["videos.csv"], VIDEO_COLUMNS, video_rows(results, cfg, judgments))
    _write_csv(paths["excluded.csv"], EXCLUDED_COLUMNS, excluded_rows(results, cfg))
    funnel = {
        "as_of": cfg.as_of.isoformat(),
        "fetched_this_run": {
            "search": n.search_fetched,
            "suggest": n.suggest_fetched,
            "enrich": n.enrich_fetched,
        },
        "markets": {r.market.key: r.funnel for r in results},
    }
    paths["funnel.json"].write_text(
        json.dumps(funnel, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    paths["report.md"].write_text(
        render_report(results, cfg, judgments), encoding="utf-8"
    )
    return paths


class _Gap:
    def __init__(self, seconds: float) -> None:
        self.seconds, self.last, self.lock = seconds, 0.0, threading.Lock()

    def wait(self) -> None:
        with self.lock:
            delay = self.last + self.seconds - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.last = time.monotonic()


_search_gap = _Gap(SEARCH_GAP_S)


def _post_json(url: str, body: Mapping[str, Any]) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def innertube_search(market: Market, body: Mapping[str, Any]) -> Any:
    _search_gap.wait()
    return _post_json(INNERTUBE_URL, body)


def youtube_suggest(market: Market, query: str) -> Any:
    params = urllib.parse.urlencode(
        {"client": "firefox", "ds": "yt", "hl": market.hl, "gl": market.gl, "q": query}
    )
    req = urllib.request.Request(
        f"{SUGGEST_URL}?{params}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset))


_YTDLP_ERROR = re.compile(r"ERROR: \[youtube\] ([\w-]{11}): (.*)")


def make_ytdlp_enrich(binary: str) -> EnrichFetch:
    def enrich(ids: Sequence[str]) -> dict[str, Mapping[str, Any] | EnrichFailure]:
        cmd = [
            binary,
            "--no-update",
            "--skip-download",
            "--ignore-errors",
            "--no-warnings",
            "-j",
        ]
        proc = subprocess.run(
            [*cmd, *map(video_url, ids)],
            capture_output=True,
            text=True,
            timeout=120 + 30 * len(ids),
            check=False,
        )
        if "confirm you" in proc.stderr and "bot" in proc.stderr:
            raise BotCheckError(proc.stderr.strip()[-800:])
        out: dict[str, Mapping[str, Any] | EnrichFailure] = {}
        for line in proc.stdout.splitlines():
            if line.startswith("{"):
                record = json.loads(line)
                out[record["id"]] = ytdlp_subset(record)
        for m in _YTDLP_ERROR.finditer(proc.stderr):
            out.setdefault(m.group(1), EnrichFailure(m.group(1), m.group(2).strip()))
        tail = proc.stderr.strip().splitlines()[-1:] or [f"exit {proc.returncode}"]
        return {
            vid: out.get(vid) or EnrichFailure(vid, f"no record from yt-dlp: {tail[0]}")
            for vid in ids
        }

    return enrich


def load_seeds(
    path: Path,
    markets: Sequence[str],
    override: Sequence[str],
    group: str | None = None,
) -> dict[str, tuple[Seed, ...]]:
    """Seeds per market. A seed's ``group`` is its trope; it defaults to the seed's own text."""
    if override:
        return {m: tuple(Seed(t, (t,), group or t) for t in override) for m in markets}
    raw = json.loads(path.read_text(encoding="utf-8"))
    seeds = {}
    for m in markets:
        entries = raw.get(m)
        if not entries:
            raise SystemExit(f"{path} has no seeds for market {m!r}")
        seeds[m] = tuple(
            Seed(
                e["text"],
                tuple(e.get("match") or [e["text"]]),
                e.get("group") or e["text"],
            )
            for e in entries
        )
    return seeds


def resolve_as_of(out: Path, explicit: str | None) -> datetime:
    """An explicit ``--as-of`` wins; otherwise reuse the time stored by the first run into ``out``, so reruns agree."""
    run_file = out / "run.json"
    if explicit:
        as_of = datetime.fromisoformat(explicit)
        as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    elif run_file.exists():
        return datetime.fromisoformat(json.loads(run_file.read_text())["as_of"])
    else:
        as_of = datetime.now(UTC).replace(microsecond=0)
    out.mkdir(parents=True, exist_ok=True)
    run_file.write_text(json.dumps({"as_of": as_of.isoformat()}) + "\n")
    return as_of


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--market", nargs="+", choices=sorted(MARKETS), default=sorted(MARKETS)
    )
    p.add_argument(
        "--seed",
        action="append",
        default=[],
        help="replaces the seeds file for an ad hoc run",
    )
    p.add_argument(
        "--group",
        help="trope name for the --seed values (default: each seed is its own group)",
    )
    p.add_argument("--seeds-file", type=Path, default=DEFAULT_SEEDS_FILE)
    p.add_argument(
        "--expand",
        type=int,
        default=5,
        help="autocomplete suggestions added per seed; 0 disables",
    )
    p.add_argument(
        "--format-words",
        action="store_true",
        help="also search each seed with the market's format words",
    )
    p.add_argument("--pages", type=int, default=2)
    p.add_argument("--min-age-days", type=int, default=7)
    p.add_argument("--max-age-days", type=int, default=28)
    p.add_argument(
        "--min-duration-s",
        type=int,
        default=60,
        help="shorter videos are excluded as short_form",
    )
    p.add_argument("--min-views", type=int, default=1000)
    p.add_argument(
        "--exclude-tag",
        action="append",
        default=[],
        help=f"for example {DUB_OR_IMPORT}",
    )
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--out", type=Path)
    p.add_argument(
        "--as-of",
        help="ISO time ages are measured against (default: now, or the time stored in --out)",
    )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None, clients: Clients | None = None) -> int:
    args = parse_args(argv)
    if clients is None:
        binary = shutil.which("yt-dlp")
        if binary is None:
            raise SystemExit(
                "yt-dlp is not on PATH. Install it (for example `uv tool install yt-dlp`) and rerun."
            )
        clients = Clients(
            search=innertube_search,
            suggest=youtube_suggest,
            enrich=make_ytdlp_enrich(binary),
        )
    out = args.out or REPO_ROOT / "outputs" / "youtube_native_traction" / datetime.now(
        UTC
    ).strftime("%Y-%m-%d")
    cfg = Config(
        markets=tuple(args.market),
        seeds=load_seeds(args.seeds_file, args.market, args.seed, args.group),
        expand=args.expand,
        format_words=args.format_words,
        pages=args.pages,
        min_age_days=args.min_age_days,
        max_age_days=args.max_age_days,
        min_duration_s=args.min_duration_s,
        min_views=args.min_views,
        exclude_tags=frozenset(args.exclude_tag),
        workers=args.workers,
        out=out,
        as_of=resolve_as_of(out, args.as_of),
    )
    try:
        results, counters = run(cfg, clients)
    except urllib.error.HTTPError as exc:
        print(
            f"YouTube refused a request: HTTP {exc.code} {exc.reason}. Cached pages are kept; rerun later."
        )
        return 2
    except BotCheckError as exc:
        print(
            f"yt-dlp hit YouTube's bot check; stopping. Cached videos are kept. yt-dlp said:\n{exc}"
        )
        return 2
    paths = write_outputs(results, cfg, counters)
    for r in results:
        print(f"{r.market.key}: {json.dumps(r.funnel, ensure_ascii=False)}")
    print(
        f"fetched this run: search={counters.search_fetched} suggest={counters.suggest_fetched} "
        f"enrich={counters.enrich_fetched}"
    )
    for path in paths.values():
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
