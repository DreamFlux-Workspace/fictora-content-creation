"""The judgment pass is cut into batches, each judged batch is held to the rubric, and only checked originals merge."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
import scripts.youtube_native_traction as ynt
import scripts.youtube_trope_judging as judging

VOCABULARY = {
    "exiled healer": "thrown out, then proven right",
    "kind stew": "kindness repaid",
}


def _row(market: str, video_id: str, title: str = "t") -> dict[str, str]:
    return {
        "market": market,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": title,
        "channel": "c",
        "subscribers": "100",
        "duration_s": "600",
        "views_per_month": "1000",
        "tags": "",
    }


def _judgment(video_id: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "video_id": video_id,
        "title_meaning": "meaning",
        "look": "anime",
        "based_on": "original",
        "trope": "exiled healer",
        "copyable": "yes",
    }
    return {**base, **overrides}


def _write_run(
    out: Path, rows: list[dict[str, str]], size: int = 2
) -> list[judging.Batch]:
    target = out / "judging"
    target.mkdir(parents=True)
    with (out / "videos.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    batches = judging.plan_batches(rows, top=50, size=size)
    for b in batches:
        payload = {
            "batch": b.batch_id,
            "market": b.market,
            "sheet": "x.jpg",
            "videos": b.videos,
        }
        (target / f"batch_{b.batch_id}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    return batches


def _write_judged(out: Path, batch_id: str, judgments: list[dict[str, Any]]) -> None:
    path = out / "judging" / f"batch_{batch_id}.judged.json"
    path.write_text(json.dumps({"judgments": judgments}), encoding="utf-8")


def _write_checks(out: Path, market: str, checks: list[dict[str, str]]) -> None:
    (out / "judging" / f"ip_check_{market}.json").write_text(
        json.dumps({"checks": checks}), encoding="utf-8"
    )


def test_batches_take_the_top_of_each_market_and_number_from_one() -> None:
    rows = [_row("jp", f"j{i}") for i in range(5)] + [
        _row("kr", f"k{i}") for i in range(3)
    ]

    batches = judging.plan_batches(rows, top=3, size=2)

    assert [(b.batch_id, [v["video_id"] for v in b.videos]) for b in batches] == [
        ("jp_01", ["j0", "j1"]),
        ("jp_02", ["j2"]),
        ("kr_01", ["k0", "k1"]),
        ("kr_02", ["k2"]),
    ]
    assert [v["n"] for v in batches[0].videos] == ["1", "2"]


@pytest.mark.parametrize(
    ("judgments", "error"),
    [
        ([_judgment("a")], "b: not judged"),
        ([_judgment("a"), _judgment("b"), _judgment("zz")], "zz: not in this batch"),
        ([_judgment("a"), _judgment("a"), _judgment("b")], "a: judged 2 times"),
        (
            [_judgment("a", look="cartoon"), _judgment("b")],
            "a: look 'cartoon' is not one of",
        ),
        (
            [_judgment("a", based_on="stolen"), _judgment("b")],
            "a: based_on 'stolen' is not one of",
        ),
        (
            [_judgment("a", copyable="maybe"), _judgment("b")],
            "a: copyable 'maybe' is not one of",
        ),
        (
            [_judgment("a", title_meaning=" "), _judgment("b")],
            "a: title_meaning is empty",
        ),
        (
            [_judgment("a", look="photoreal"), _judgment("b")],
            "a: copyable yes needs a drawn look",
        ),
        (
            [_judgment("a", based_on="recap"), _judgment("b")],
            "a: copyable yes needs based_on original",
        ),
        ([_judgment("a", trope=""), _judgment("b")], "a: copyable yes needs a trope"),
        (
            [_judgment("a", trope="exiled healr"), _judgment("b")],
            "a: trope 'exiled healr' is not in the vocabulary",
        ),
        (
            [_judgment("a", trope="new: cursed inn"), _judgment("b")],
            "a: a new trope needs a trope_description",
        ),
    ],
)
def test_a_judged_batch_that_breaks_the_rubric_is_refused(
    judgments: list[dict[str, Any]], error: str
) -> None:
    batch = judging.plan_batches([_row("jp", "a"), _row("jp", "b")], top=50, size=25)[0]

    errors = judging.check_judgments(batch, {"judgments": judgments}, VOCABULARY)

    assert any(e.startswith(error) for e in errors), errors


def test_a_valid_batch_passes_including_a_described_new_trope_and_a_non_story() -> None:
    batch = judging.plan_batches([_row("jp", "a"), _row("jp", "b")], top=50, size=25)[0]
    judgments = [
        _judgment(
            "a",
            trope="new: cursed inn",
            trope_description="an inn that charges in years of life",
        ),
        _judgment("b", look="photoreal", based_on="fan-work", trope="", copyable="no"),
    ]

    assert judging.check_judgments(batch, {"judgments": judgments}, VOCABULARY) == []


def test_merge_keeps_a_video_copyable_only_when_the_existing_work_check_clears_it(
    tmp_path: Path,
) -> None:
    _write_run(
        tmp_path, [_row("jp", v) for v in ("clear", "taken", "murky", "recap")], size=4
    )
    _write_judged(
        tmp_path,
        "jp_01",
        [
            _judgment("clear"),
            _judgment("taken"),
            _judgment(
                "murky",
                trope="new: cursed inn",
                trope_description="an inn that charges in years",
            ),
            _judgment("recap", based_on="recap", trope="", copyable="no"),
        ],
    )
    _write_checks(
        tmp_path,
        "jp",
        [
            {
                "video_id": "clear",
                "existing_work": "no",
                "evidence": "no manga or novel by this title",
            },
            {
                "video_id": "taken",
                "existing_work": "yes",
                "evidence": "serialized on Kakuyomu since 2024",
            },
            {
                "video_id": "murky",
                "existing_work": "unclear",
                "evidence": "a similar title exists",
            },
        ],
    )

    summary = judging.merge(tmp_path, VOCABULARY, skip_ip_check=False)

    with (tmp_path / "judgments.csv").open(encoding="utf-8") as fh:
        rows = {r["video_id"]: r for r in csv.DictReader(fh)}
    assert (rows["clear"]["copyable"], rows["clear"]["based_on"]) == ("yes", "original")
    assert (rows["taken"]["copyable"], rows["taken"]["based_on"]) == ("no", "recap")
    assert "serialized on Kakuyomu" in rows["taken"]["judgment_note"]
    assert rows["murky"]["copyable"] == "unclear"
    assert rows["recap"]["copyable"] == "no"
    assert summary["copyable"] == {"yes": 1, "no": 2, "unclear": 1}
    assert summary["proposed_tropes"] == []
    assert summary["unjudged_kept_videos"] == 0


def test_merge_refuses_when_a_copyable_video_has_no_existing_work_check(
    tmp_path: Path,
) -> None:
    _write_run(tmp_path, [_row("jp", "a"), _row("jp", "b")])
    _write_judged(tmp_path, "jp_01", [_judgment("a"), _judgment("b")])
    _write_checks(
        tmp_path,
        "jp",
        [{"video_id": "a", "existing_work": "no", "evidence": "nothing found"}],
    )

    with pytest.raises(SystemExit, match="b: no existing-work check"):
        judging.merge(tmp_path, VOCABULARY, skip_ip_check=False)

    assert not (tmp_path / "judgments.csv").exists()


def test_merge_refuses_an_invalid_judged_batch_and_names_the_file(
    tmp_path: Path,
) -> None:
    _write_run(tmp_path, [_row("jp", "a"), _row("jp", "b")])
    _write_judged(tmp_path, "jp_01", [_judgment("a")])

    with pytest.raises(
        SystemExit, match=r"batch_jp_01\.judged\.json is invalid:\n  b: not judged"
    ):
        judging.merge(tmp_path, VOCABULARY, skip_ip_check=True)


def test_a_cleared_new_trope_is_reported_as_proposed_with_its_videos(
    tmp_path: Path,
) -> None:
    _write_run(tmp_path, [_row("kr", "a"), _row("kr", "b")])
    new = {
        "trope": "new: cursed inn",
        "trope_description": "an inn that charges in years of life",
    }
    _write_judged(tmp_path, "kr_01", [_judgment("a", **new), _judgment("b", **new)])

    summary = judging.merge(tmp_path, VOCABULARY, skip_ip_check=True)

    proposed = json.loads(
        (tmp_path / "judging" / "proposed_tropes.json").read_text(encoding="utf-8")
    )
    assert summary["proposed_tropes"] == ["cursed inn"]
    assert proposed == {
        "cursed inn": {
            "description": "an inn that charges in years of life",
            "video_ids": ["a", "b"],
        }
    }


def test_the_merged_file_has_exactly_the_columns_the_traction_report_reads() -> None:
    assert judging.JUDGMENTS_COLUMNS == ["video_id", *ynt.JUDGMENT_COLUMNS]
    assert judging.JUDGING_DIR and ynt.JUDGMENTS_FILE == "judgments.csv"


def test_the_shipped_vocabulary_loads_with_a_description_for_every_trope() -> None:
    vocabulary = judging.load_vocabulary()

    assert len(vocabulary) >= 10
    assert all(
        name == name.strip() and description for name, description in vocabulary.items()
    )
