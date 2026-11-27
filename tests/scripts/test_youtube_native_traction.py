"""The native YouTube traction ranker parses real InnerTube pages, filters by one ordered rule table, and reruns free."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import scripts.youtube_native_traction as ynt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "youtube_native_traction"
AS_OF = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


class _FrozenDatetime(datetime):
    """The fixtures' relative dates ("2 weeks ago") are read against the fetch time, so a fetch happens at AS_OF."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return AS_OF if tz is not None else AS_OF.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _fetch_at_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ynt, "datetime", _FrozenDatetime)


JP, KR = ynt.MARKETS["jp"], ynt.MARKETS["kr"]


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _config(**overrides: Any) -> ynt.Config:
    base = ynt.Config(
        markets=("jp", "kr"),
        seeds={
            "jp": (ynt.Seed("AIドラマ", ("AIドラマ",), "ai_drama"),),
            "kr": (ynt.Seed("AI 드라마", ("AI 드라마",), "ai_drama"),),
        },
        expand=0,
        format_words=False,
        pages=1,
        min_age_days=7,
        max_age_days=28,
        min_duration_s=60,
        min_views=1000,
        exclude_tags=frozenset(),
        workers=1,
        out=Path("unused"),
        as_of=AS_OF,
    )
    return replace(base, **overrides)


def _hit(market: str = "jp", seed: str = "AIドラマ", **overrides: Any) -> ynt.SearchHit:
    base = ynt.SearchHit(
        video_id="vid00000001",
        market=market,
        query=seed,
        seed=seed,
        group="ai_drama",
        search_pass="relevance",
        rank=1,
        title="",
        age_text=None,
        seen_at=AS_OF,
    )
    return replace(base, **overrides)


def _video(
    market: str = "jp",
    seed: str = "AIドラマ",
    age_days: float = 10.0,
    search_pass: str = "relevance",
    **data: Any,
) -> ynt.Video:
    record = ynt.parse_ytdlp(_fixture("ytdlp_jp.json"))
    assert isinstance(record, ynt.VideoData)
    record = replace(record, published_at=AS_OF - timedelta(days=age_days), **data)
    return ynt.build_video(
        ynt.MARKETS[market],
        record,
        [_hit(market, seed, search_pass=search_pass)],
        AS_OF,
    )


def test_jp_search_page_parses_into_cards_and_a_continuation() -> None:
    items, token = ynt.parse_search_page(_fixture("search_jp.json"))

    assert [i.video_id for i in items] == [
        "TLT241SkY2w",
        "JYr8u497SP0",
        "h-Xf5LRmKT4",
        "DoN9-IPXT2I",
        "QzUgjzptecw",
    ]
    assert (
        items[3].title == "【AIドラマ】君の名前を聞いただけで心が揺れる｜冷たい彼 EP.4"
    )
    assert items[3].age_text == "9 日前"
    assert token and token.startswith("Ep")


def test_kr_search_page_keeps_stream_and_live_cards() -> None:
    items, _ = ynt.parse_search_page(_fixture("search_kr.json"))
    by_id = {i.video_id: i for i in items}

    assert by_id["XdZw5TZq1Z8"].age_text == "스트리밍 시간: 23시간 전"
    assert by_id["V475uVBnfa0"].age_text is None
    assert by_id["x3GgnZAHV68"].age_text == "2일 전"


@pytest.mark.parametrize(
    ("market", "text", "seconds"),
    [
        (JP, "9 日前", 9 * 86400),
        (JP, "2 週間前", 14 * 86400),
        (JP, "1 か月前", 30 * 86400),
        (JP, "2 ヶ月前", 60 * 86400),
        (JP, "1 年前", 365 * 86400),
        (JP, "44 分前 に配信済み", 44 * 60),
        (KR, "13일 전", 13 * 86400),
        (KR, "2주 전", 14 * 86400),
        (KR, "8시간 전", 8 * 3600),
        (KR, "스트리밍 시간: 23시간 전", 23 * 3600),
        (KR, "1개월 전", 30 * 86400),
        (JP, None, None),
        (JP, "ライブ配信中", None),
        (KR, "9 日前", None),
    ],
)
def test_card_age_parses_in_the_markets_language(
    market: ynt.Market, text: str | None, seconds: float | None
) -> None:
    assert ynt.parse_age_seconds(market, text) == seconds


@pytest.mark.parametrize(
    ("video_kwargs", "cfg_kwargs", "reason"),
    [
        ({"live_status": "is_live"}, {}, ynt.Reason.LIVE_OR_UPCOMING),
        ({"live_status": "is_upcoming"}, {}, ynt.Reason.LIVE_OR_UPCOMING),
        ({"age_days": 40.0}, {}, ynt.Reason.OUTSIDE_WINDOW),
        ({"duration_s": 59}, {}, ynt.Reason.SHORT_FORM),
        ({"duration_s": None}, {}, ynt.Reason.SHORT_FORM),
        ({"duration_s": 300}, {"min_duration_s": 600}, ynt.Reason.SHORT_FORM),
        ({"width": 1080, "height": 1920}, {}, ynt.Reason.NOT_LANDSCAPE),
        ({"width": 1080, "height": 1080}, {}, ynt.Reason.NOT_LANDSCAPE),
        ({"width": None, "height": None}, {}, ynt.Reason.NOT_LANDSCAPE),
        ({"views": 999}, {}, ynt.Reason.BELOW_MIN_VIEWS),
        ({"views": 5000}, {"min_views": 5001}, ynt.Reason.BELOW_MIN_VIEWS),
        (
            {"title": "【AI短剧】霸道总裁爱上我", "audio_language": "zh"},
            {},
            ynt.Reason.NOT_NATIVE,
        ),
        (
            {
                "title": "猫の日常",
                "description": "",
                "yt_tags": (),
                "search_pass": "views",
            },
            {},
            ynt.Reason.OFF_TOPIC,
        ),
        (
            {"description": "日本語吹き替え AIドラマ"},
            {"exclude_tags": frozenset({"dub_or_import"})},
            ynt.Reason.EXCLUDED_TAG,
        ),
    ],
)
def test_each_exclusion_reason_fires(
    video_kwargs: dict[str, Any], cfg_kwargs: dict[str, Any], reason: ynt.Reason
) -> None:
    assert ynt.judge(_video(**video_kwargs), _config(**cfg_kwargs)).reason is reason


def test_an_hour_long_vertical_video_is_not_long_form_landscape() -> None:
    vertical = _video(duration_s=3600, width=1080, height=1920)

    assert vertical.orientation == "portrait"
    assert ynt.judge(vertical, _config()).reason is ynt.Reason.NOT_LANDSCAPE
    assert ynt.judge(_video(duration_s=60, width=1920, height=1080), _config()).kept


def test_enrich_failure_is_its_own_reason_with_the_error() -> None:
    verdict = ynt.judge(ynt.EnrichFailure("x", "Video unavailable"), _config())

    assert (verdict.reason, verdict.detail) == (
        ynt.Reason.ENRICH_FAILED,
        "Video unavailable",
    )


def test_a_video_failing_two_rules_reports_the_earlier_one() -> None:
    assert (
        ynt.judge(_video(age_days=40.0, live_status="is_live"), _config()).reason
        is ynt.Reason.LIVE_OR_UPCOMING
    )
    assert (
        ynt.judge(_video(age_days=40.0, views=10), _config()).reason
        is ynt.Reason.OUTSIDE_WINDOW
    )
    low = _video(views=10, title="猫", description="", search_pass="views")
    assert ynt.judge(low, _config()).reason is ynt.Reason.BELOW_MIN_VIEWS


@pytest.mark.parametrize(
    ("age_days", "kept"), [(6.9, False), (7.0, True), (28.9, True), (29.0, False)]
)
def test_window_keeps_whole_days_seven_through_twenty_eight(
    age_days: float, kept: bool
) -> None:
    assert ynt.judge(_video(age_days=age_days), _config()).kept is kept


@pytest.mark.parametrize(
    ("title", "language", "native"),
    [
        ("【AIドラマ】冷たい彼", None, True),
        ("運命の再会", "ja", True),
        ("霸道总裁的契约新娘", "zh", False),
        ("霸道总裁的契约新娘", None, False),
    ],
)
def test_native_means_kana_in_the_title_or_japanese_audio(
    title: str, language: str | None, native: bool
) -> None:
    video = _video(title=title, audio_language=language, description="AIドラマ")

    assert video.is_native is native
    assert (ynt.judge(video, _config()).reason is ynt.Reason.NOT_NATIVE) is not native


def test_topic_match_ignores_whitespace_case_width_and_hashtags() -> None:
    def from_views(title: str) -> ynt.Video:
        return _video(
            market="kr",
            seed="AI 드라마",
            title=title,
            description="",
            yt_tags=(),
            search_pass="views",
        )

    assert ynt.judge(from_views("ａｉ드라마 첫사랑 1화"), _config()).kept
    assert ynt.judge(from_views("첫사랑 1화 #AI #드라마"), _config()).kept
    assert ynt.judge(from_views("첫사랑 1화"), _config()).reason is ynt.Reason.OFF_TOPIC


def test_a_normal_order_result_is_on_topic_without_naming_the_term() -> None:
    video = _video(
        title="猫の日常", description="", yt_tags=(), search_pass="relevance"
    )

    assert not ynt.term_match(video, _config())
    assert ynt.judge(video, _config()).kept


def test_dub_markers_tag_the_video_without_dropping_it() -> None:
    video = _video(description="【日本語吹き替え】AIドラマ")

    assert video.labels == ("dub_or_import",)
    assert ynt.judge(video, _config()).kept


@pytest.mark.parametrize(
    ("title", "description", "tags"),
    [
        ("【PR】新作AIドラマ", "", ("paid_promo_hint",)),
        ("新作AIドラマ", "この動画はプロモーションを含みます", ("paid_promo_hint",)),
        ("PRIME会員の恋 AIドラマ", "express delivery", ()),
        ("話題作を5分で解説 AIドラマ", "", ("recap_hint",)),
        ("AIドラマ 第1話", "あらすじ：会社員の主人公が…", ()),
        ("【日本語吹替】AIドラマ ネタバレあり", "", ("dub_or_import", "recap_hint")),
        ("【公式】新作アニメ 本予告", "", ("official_or_trailer_hint",)),
        ("AIアニメの作り方 完全講座", "", ("howto_or_music_hint",)),
        ("OPEN前の喫茶店で起きたこと AIドラマ", "公式サイトはこちら", ()),
    ],
)
def test_tags_are_read_from_text_and_a_latin_term_needs_a_whole_word(
    title: str, description: str, tags: tuple[str, ...]
) -> None:
    assert _video(title=title, description=description, yt_tags=()).labels == tags


def test_korean_tags_ignore_spacing() -> None:
    video = _video(
        market="kr",
        seed="AI 드라마",
        title="AI 드라마 결말 포함 리뷰",
        description="유료 광고 포함",
        yt_tags=(),
    )

    assert video.labels == ("recap_hint", "paid_promo_hint")


def test_format_words_add_one_query_per_seed_and_word_under_the_same_group(
    tmp_path: Path,
) -> None:
    cfg = _config(format_words=True)
    fakes = _Fakes()

    queries = ynt.build_queries(
        JP, cfg.seeds["jp"], cfg, ynt.Cache(tmp_path), fakes.clients(), ynt.Counters()
    )

    assert [(q.text, q.source, q.seed.group) for q in queries] == [
        ("AIドラマ", "seed", "ai_drama"),
        ("AIドラマ 漫画動画", "format_word", "ai_drama"),
        ("AIドラマ ボイスコミック", "format_word", "ai_drama"),
        ("AIドラマ アニメ", "format_word", "ai_drama"),
    ]
    assert [
        q.source
        for q in ynt.build_queries(
            JP,
            cfg.seeds["jp"],
            _config(),
            ynt.Cache(tmp_path),
            fakes.clients(),
            ynt.Counters(),
        )
    ] == ["seed"]


def test_group_rollup_reads_demand_and_supply_per_trope() -> None:
    def video(vid: str, group: str, views: int, channel: str, title: str) -> ynt.Video:
        v = _video(
            age_days=10.0,
            views=views,
            channel_id=channel,
            title=title,
            description="",
            yt_tags=(),
            video_id=vid,
        )
        return replace(v, hits=(_hit(group=group, video_id=vid),))

    kept = ynt.score_cohort(
        [
            video("a", "bride", 4000, "c1", "【AIドラマ】暴君の花嫁 第1話"),
            video("b", "bride", 3000, "c2", "【AIドラマ】身代わり花嫁 第2話"),
            video("c", "bride", 2000, "c2", "【日本語吹替】契約結婚の花嫁"),
            video("d", "bride", 1000, "c3", "花嫁の復讐 要約"),
            video("e", "bride", 500, "c3", "花嫁の涙"),
            video("f", "horror", 9000, "c4", "深夜バイトのルール"),
        ]
    )

    horror, bride, empty = ynt.group_rollup(
        kept, ["bride", "horror", "unused"], list(JP.tags)
    )

    assert (horror.group, horror.uploads, horror.channels) == ("horror", 1, 1)
    assert horror.median_views_per_month == pytest.approx(9000 / 10 * 30)
    assert (bride.uploads, bride.channels) == (5, 3)
    assert bride.median_views_per_month == pytest.approx(2000 / 10 * 30)
    assert bride.top is not None and bride.top.video.data.video_id == "a"
    assert {tag: share for tag, share in bride.tag_share.items() if share} == {
        "dub_or_import": 0.2,
        "recap_hint": 0.2,
    }
    # Top quartile of five is the two best, from two channels; only the bracket word they share repeats.
    assert bride.title_words == (("aiドラマ", 2),)
    assert (empty.uploads, empty.top, empty.median_views_per_month) == (0, None, 0.0)


def test_cohort_score_is_mean_percentile_and_falls_back_to_views_per_day() -> None:
    videos = [
        _video(age_days=10.0, views=100, subscribers=33),  # vpd 10, vps ~3.03
        _video(age_days=10.0, views=200, subscribers=200),  # vpd 20, vps 1
        _video(age_days=10.0, views=300, subscribers=None),  # vpd 30, no vps
    ]
    scored = {round(s.views_per_day): s for s in ynt.score_cohort(videos)}

    # vpd percentiles 1/6, 3/6, 5/6; vps percentiles among the two that have it 3/4, 1/4.
    assert scored[10].traction_score == pytest.approx(100 * (1 / 6 + 3 / 4) / 2)
    assert scored[20].traction_score == pytest.approx(100 * (3 / 6 + 1 / 4) / 2)
    assert scored[30].traction_score == pytest.approx(100 * 5 / 6)
    assert scored[30].score_basis == "views_per_day_only"
    assert scored[10].score_basis == "views_per_day+views_per_sub"
    assert [round(s.views_per_day) for s in ynt.score_cohort(videos)] == [30, 10, 20]


def test_spearman_sees_a_perfect_inverse_order() -> None:
    assert ynt.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert ynt.spearman([1, 2], [2, 1]) is None


class _Fakes:
    def __init__(self) -> None:
        self.enrich_calls: list[list[str]] = []
        self.search_calls = 0
        base = _fixture("ytdlp_jp.json")
        self.records: dict[str, Mapping[str, Any]] = {
            "DoN9-IPXT2I": base,
            "JYr8u497SP0": {
                **base,
                "id": "JYr8u497SP0",
                "title": "もうAIと実写の区別がつかない？リアルすぎるAIドラマ",
                "description": "",
                "timestamp": int((AS_OF - timedelta(days=14)).timestamp()),
                "view_count": 62756,
                "channel_follower_count": None,
                "duration": 829,
                "width": 1080,
                "height": 1920,
            },
        }

    def search(self, market: ynt.Market, body: Mapping[str, Any]) -> Any:
        self.search_calls += 1
        if "continuation" in body:
            return {}
        return _fixture("search_jp.json")

    def suggest(self, market: ynt.Market, query: str) -> Any:
        return [query, [query, f"{query} 日本語", f"{query} 最新"], [], {}]

    def enrich(
        self, ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, Any] | ynt.EnrichFailure]:
        self.enrich_calls.append(list(ids))
        return {
            i: self.records.get(i) or ynt.EnrichFailure(i, "Video unavailable")
            for i in ids
        }

    def clients(self) -> ynt.Clients:
        return ynt.Clients(search=self.search, suggest=self.suggest, enrich=self.enrich)


def _run(out: Path, fakes: _Fakes) -> int:
    argv = [
        "--market",
        "jp",
        "--seed",
        "AIドラマ",
        "--expand",
        "1",
        "--pages",
        "2",
        "--out",
        str(out),
    ]
    return ynt.main([*argv, "--as-of", AS_OF.isoformat()], clients=fakes.clients())


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_full_run_accounts_for_every_video(tmp_path: Path) -> None:
    fakes = _Fakes()

    assert _run(tmp_path, fakes) == 0

    funnel = json.loads((tmp_path / "funnel.json").read_text())["markets"]["jp"]
    assert ynt.funnel_balances(funnel)
    assert funnel["queries"] == {"seed": 1, "format_word": 0, "autocomplete": 1}
    assert funnel["prefilter_dropped"] == {
        "prefilter_too_young": 2,
        "prefilter_too_old": 0,
    }
    enriched_ids = sorted(i for call in fakes.enrich_calls for i in call)
    assert "TLT241SkY2w" not in enriched_ids and "QzUgjzptecw" not in enriched_ids

    kept = _rows(tmp_path / "videos.csv")
    assert [(r["url"][-11:], r["orientation"], r["rank"]) for r in kept] == [
        ("DoN9-IPXT2I", "landscape", "1")
    ]
    assert funnel["kept"] == 1
    assert all(7 <= float(r["age_days"]) < 29 for r in kept)

    excluded = _rows(tmp_path / "excluded.csv")
    reasons = {r["url"][-11:]: (r["reason"], r["detail"]) for r in excluded}
    assert reasons["h-Xf5LRmKT4"] == ("enrich_failed", "Video unavailable")
    assert reasons["TLT241SkY2w"] == ("prefilter_too_young", "1 時間前")
    assert reasons["JYr8u497SP0"][0] == "not_landscape"
    assert (
        next(r for r in excluded if r["url"].endswith("JYr8u497SP0"))["orientation"]
        == "portrait"
    )
    assert len(excluded) + len(kept) == funnel["unique_videos"]
    assert all(
        r["look"] == r["based_on"] == r["trope"] == "" and r["groups"] == "AIドラマ"
        for r in kept
    )
    assert "### Copyable tropes" not in (tmp_path / "report.md").read_text()
    assert float(kept[0]["views_per_month"]) == pytest.approx(
        float(kept[0]["views_per_day"]) * 30, rel=0.01
    )
    report = (tmp_path / "report.md").read_text()
    assert "region pin is partial" in report
    assert "### Demand and supply by trope" in report
    assert "Long-form landscape only" in report


def test_a_judgments_file_fills_the_columns_and_lists_only_copyable_tropes(
    tmp_path: Path,
) -> None:
    _run(tmp_path, _Fakes())
    (tmp_path / "judgments.csv").write_text(
        "video_id,title_meaning,look,based_on,trope,copyable\n"
        "DoN9-IPXT2I,Cold boyfriend ep 4,anime,original,cold lead thaws for one person,yes\n"
        "JYr8u497SP0,AI drama explainer,photoreal,original,,no\n",
        encoding="utf-8",
    )

    _run(tmp_path, _Fakes())

    row = _rows(tmp_path / "videos.csv")[0]
    assert (row["look"], row["based_on"], row["trope"], row["copyable"]) == (
        "anime",
        "original",
        "cold lead thaws for one person",
        "yes",
    )
    report = (tmp_path / "report.md").read_text()
    assert "Judged 1 of 1 kept videos; 1 are drawn-look originals" in report
    assert "| cold lead thaws for one person | 1 | 1 |" in report


def test_only_copyable_videos_count_toward_a_trope() -> None:
    a, b, c = (
        replace(_video(views=v, video_id=i), hits=(_hit(video_id=i),))
        for i, v in (("a", 3000), ("b", 2000), ("c", 1000))
    )
    kept = ynt.score_cohort([a, b, c])
    judgments = {
        "a": {"trope": "exiled healer", "copyable": "yes"},
        "b": {"trope": "exiled healer", "copyable": "no"},
        "c": {"trope": "", "copyable": "yes"},
    }

    (stat,) = ynt.trope_rollup(kept, judgments, list(JP.tags))

    assert (stat.group, stat.uploads) == ("exiled healer", 1)
    assert stat.top is not None and stat.top.video.data.video_id == "a"


def test_rerun_into_the_same_out_fetches_nothing_and_writes_the_same_table(
    tmp_path: Path,
) -> None:
    first = _Fakes()
    _run(tmp_path, first)
    before = (tmp_path / "videos.csv").read_bytes()

    second = _Fakes()
    assert (
        ynt.main(
            [
                "--market",
                "jp",
                "--seed",
                "AIドラマ",
                "--expand",
                "1",
                "--pages",
                "2",
                "--out",
                str(tmp_path),
            ],
            clients=second.clients(),
        )
        == 0
    )

    assert first.enrich_calls and second.enrich_calls == []
    assert second.search_calls == 0
    assert (tmp_path / "videos.csv").read_bytes() == before
    assert json.loads((tmp_path / "funnel.json").read_text())["fetched_this_run"] == {
        "search": 0,
        "suggest": 0,
        "enrich": 0,
    }
