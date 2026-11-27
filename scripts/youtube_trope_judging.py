"""Batch, validate and merge the judgment pass that follows ``youtube_native_traction.py``.

Whether a video is a drawn-look original with a trope we can rebuild cannot be read from metadata; someone has to
look. This script is the contract around that looking, so a person or an agent can do it and the result is checked
the same way:

``batches``     cut the top kept videos of each market into numbered thumbnail sheets plus a list per batch
``validate``    check one judged batch against the rubric's enums, the trope vocabulary and the batch's own ids
``candidates``  list a market's videos judged copyable, for the existing-work check
``merge``       apply the existing-work checks and write ``<out>/judgments.csv`` for the traction report

Files live under ``<out>/judging/``::

    batch_jp_01.json         the batch: its videos, numbered as on the sheet
    batch_jp_01.jpg          the thumbnail sheet
    batch_jp_01.judged.json  {"judgments": [{video_id, title_meaning, look, based_on, trope, trope_description?,
                             copyable, note?}]}, written by whoever judged the batch
    ip_check_jp.json         {"checks": [{video_id, existing_work, evidence}]}, written by the existing-work check

Run::

    uv run --with pillow python scripts/youtube_trope_judging.py batches --out <out> --top 50
    uv run python scripts/youtube_trope_judging.py validate --out <out> --batch jp_01
    uv run python scripts/youtube_trope_judging.py candidates --out <out> --market jp
    uv run python scripts/youtube_trope_judging.py merge --out <out>

The rubric the judgments follow is ``.claude/skills/youtube-trope-scan/RUBRIC.md``.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VOCABULARY_FILE = Path(__file__).with_name("youtube_trope_vocabulary.json")
JUDGING_DIR = "judging"
JUDGMENTS_COLUMNS = [
    "video_id",
    "title_meaning",
    "look",
    "based_on",
    "trope",
    "copyable",
    "judgment_note",
]
NEW_TROPE_PREFIX = "new: "

LOOKS = (
    "anime",
    "manga",
    "story-toon",
    "3d-or-ai-comic",
    "photoreal",
    "live-action",
    "other",
)
DRAWN_LOOKS = frozenset({"anime", "manga", "story-toon"})
BASED_ON = (
    "original",
    "reupload",
    "recap",
    "fan-work",
    "official",
    "paid-promo",
    "import",
    "not-a-story",
    "unclear",
)
COPYABLE = ("yes", "no", "unclear")
EXISTING_WORK = ("yes", "no", "unclear")

SHEET_COLUMNS = 5
THUMB_W, THUMB_H = 320, 180


@dataclass(frozen=True)
class Batch:
    batch_id: str
    market: str
    videos: tuple[dict[str, str], ...]


def judging_dir(out: Path) -> Path:
    return out / JUDGING_DIR


def load_vocabulary(path: Path = VOCABULARY_FILE) -> dict[str, str]:
    """Trope name to description. The fixed list every video is checked against."""
    return {
        entry["name"]: entry["description"]
        for entry in json.loads(path.read_text(encoding="utf-8"))
    }


def video_id_of(row: Mapping[str, str]) -> str:
    return row["url"].rsplit("v=", 1)[-1]


def plan_batches(rows: Sequence[Mapping[str, str]], top: int, size: int) -> list[Batch]:
    """The first ``top`` rows of each market (videos.csv is already ranked), in batches of ``size`` per market."""
    batches = []
    for market in dict.fromkeys(row["market"] for row in rows):
        picked = [row for row in rows if row["market"] == market][:top]
        for start in range(0, len(picked), size):
            videos = tuple(
                {
                    "n": str(n),
                    "video_id": video_id_of(row),
                    "url": row["url"],
                    "title": row["title"],
                    "channel": row["channel"],
                    "subscribers": row["subscribers"],
                    "duration_s": row["duration_s"],
                    "views_per_month": row["views_per_month"],
                    "tags": row["tags"],
                }
                for n, row in enumerate(picked[start : start + size], 1)
            )
            batches.append(Batch(f"{market}_{start // size + 1:02d}", market, videos))
    return batches


def read_videos(out: Path) -> list[dict[str, str]]:
    path = out / "videos.csv"
    if not path.exists():
        raise SystemExit(
            f"{path} is missing. Run scripts/youtube_native_traction.py into this --out first."
        )
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"{path} is missing.") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from None


def load_batches(out: Path) -> list[Batch]:
    paths = sorted(
        p
        for p in judging_dir(out).glob("batch_*.json")
        if not p.name.endswith(".judged.json")
    )
    if not paths:
        raise SystemExit(
            f"No batches under {judging_dir(out)}. Run the `batches` command first."
        )
    return [
        Batch(raw["batch"], raw["market"], tuple(raw["videos"]))
        for raw in map(_read_json, paths)
    ]


def check_judgments(
    batch: Batch, judged: Any, vocabulary: Mapping[str, str]
) -> list[str]:
    """Every way ``judged`` breaks the contract for ``batch``; empty when it is valid."""
    rows = judged.get("judgments") if isinstance(judged, dict) else None
    if not isinstance(rows, list):
        return ['the file must be an object with a "judgments" list']
    errors = []
    expected = [v["video_id"] for v in batch.videos]
    seen = [str(row.get("video_id")) for row in rows if isinstance(row, dict)]
    errors += [f"{vid}: not judged" for vid in expected if vid not in seen]
    errors += [
        f"{vid}: not in this batch"
        for vid in dict.fromkeys(seen)
        if vid not in expected
    ]
    errors += [
        f"{vid}: judged {seen.count(vid)} times"
        for vid in dict.fromkeys(seen)
        if seen.count(vid) > 1
    ]
    for row in rows:
        if not isinstance(row, dict):
            errors.append("a judgment is not an object")
            continue
        vid = str(row.get("video_id"))
        look, based_on, copyable = (
            row.get("look"),
            row.get("based_on"),
            row.get("copyable"),
        )
        trope = str(row.get("trope") or "")
        if look not in LOOKS:
            errors.append(f"{vid}: look {look!r} is not one of {', '.join(LOOKS)}")
        if based_on not in BASED_ON:
            errors.append(
                f"{vid}: based_on {based_on!r} is not one of {', '.join(BASED_ON)}"
            )
        if copyable not in COPYABLE:
            errors.append(
                f"{vid}: copyable {copyable!r} is not one of {', '.join(COPYABLE)}"
            )
        if not str(row.get("title_meaning") or "").strip():
            errors.append(f"{vid}: title_meaning is empty")
        if copyable == "yes":
            if look not in DRAWN_LOOKS:
                errors.append(
                    f"{vid}: copyable yes needs a drawn look ({', '.join(sorted(DRAWN_LOOKS))}), not {look}"
                )
            if based_on != "original":
                errors.append(
                    f"{vid}: copyable yes needs based_on original, not {based_on}"
                )
            if not trope:
                errors.append(f"{vid}: copyable yes needs a trope")
        if trope.startswith(NEW_TROPE_PREFIX):
            if not trope[len(NEW_TROPE_PREFIX) :].strip():
                errors.append(
                    f"{vid}: a new trope needs a name after {NEW_TROPE_PREFIX!r}"
                )
            if not str(row.get("trope_description") or "").strip():
                errors.append(f"{vid}: a new trope needs a trope_description")
        elif trope and trope not in vocabulary:
            errors.append(
                f"{vid}: trope {trope!r} is not in the vocabulary; use an exact name, "
                f"or propose one as {NEW_TROPE_PREFIX!r}<name> with a trope_description"
            )
    return errors


def load_judged(
    out: Path, batch: Batch, vocabulary: Mapping[str, str]
) -> list[dict[str, Any]]:
    path = judging_dir(out) / f"batch_{batch.batch_id}.judged.json"
    judged = _read_json(path)
    errors = check_judgments(batch, judged, vocabulary)
    if errors:
        raise SystemExit(f"{path} is invalid:\n" + "\n".join(f"  {e}" for e in errors))
    return [dict(row) for row in judged["judgments"]]


def check_ip_checks(candidates: Sequence[str], raw: Any) -> list[str]:
    rows = raw.get("checks") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return ['the file must be an object with a "checks" list']
    errors = []
    seen = [str(row.get("video_id")) for row in rows if isinstance(row, dict)]
    errors += [
        f"{vid}: no existing-work check" for vid in candidates if vid not in seen
    ]
    errors += [
        f"{vid}: not a copyable candidate of this market"
        for vid in dict.fromkeys(seen)
        if vid not in candidates
    ]
    for row in rows:
        if not isinstance(row, dict):
            errors.append("a check is not an object")
        elif row.get("existing_work") not in EXISTING_WORK:
            errors.append(
                f"{row.get('video_id')}: existing_work {row.get('existing_work')!r} is not one of "
                f"{', '.join(EXISTING_WORK)}"
            )
        elif not str(row.get("evidence") or "").strip():
            errors.append(f"{row.get('video_id')}: evidence is empty")
    return errors


def apply_ip_check(
    judgment: Mapping[str, Any], check: Mapping[str, Any] | None
) -> dict[str, str]:
    """One judgments.csv row. A copyable video stays copyable only when the existing-work check says ``no``."""
    row = {
        "video_id": str(judgment["video_id"]),
        "title_meaning": str(judgment.get("title_meaning") or ""),
        "look": str(judgment["look"]),
        "based_on": str(judgment["based_on"]),
        "trope": str(judgment.get("trope") or ""),
        "copyable": str(judgment["copyable"]),
        "judgment_note": str(judgment.get("note") or ""),
    }
    if row["copyable"] != "yes" or check is None:
        return row
    verdict = check["existing_work"]
    note = f"existing-work check: {verdict}. {check['evidence']}".strip()
    row["judgment_note"] = (
        f"{row['judgment_note']} | {note}" if row["judgment_note"] else note
    )
    if verdict == "yes":
        row.update(copyable="no", based_on="recap")
    elif verdict == "unclear":
        row["copyable"] = "unclear"
    return row


def merge(
    out: Path, vocabulary: Mapping[str, str], skip_ip_check: bool
) -> dict[str, Any]:
    """Validate every judged batch and existing-work check, then write judgments.csv and the proposed tropes."""
    batches = load_batches(out)
    rows: list[dict[str, str]] = []
    proposed: dict[str, dict[str, Any]] = {}
    for market in dict.fromkeys(b.market for b in batches):
        judged = [
            j
            for b in batches
            if b.market == market
            for j in load_judged(out, b, vocabulary)
        ]
        candidates = [str(j["video_id"]) for j in judged if j["copyable"] == "yes"]
        checks: dict[str, Mapping[str, Any]] = {}
        if not skip_ip_check and candidates:
            path = judging_dir(out) / f"ip_check_{market}.json"
            raw = _read_json(path)
            errors = check_ip_checks(candidates, raw)
            if errors:
                raise SystemExit(
                    f"{path} is invalid:\n" + "\n".join(f"  {e}" for e in errors)
                )
            checks = {str(c["video_id"]): c for c in raw["checks"]}
        for judgment in judged:
            row = apply_ip_check(judgment, checks.get(str(judgment["video_id"])))
            rows.append(row)
            if row["trope"].startswith(NEW_TROPE_PREFIX) and row["copyable"] == "yes":
                entry = proposed.setdefault(
                    row["trope"][len(NEW_TROPE_PREFIX) :].strip(),
                    {
                        "description": str(judgment.get("trope_description") or ""),
                        "video_ids": [],
                    },
                )
                entry["video_ids"].append(row["video_id"])
    with (out / "judgments.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=JUDGMENTS_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    (judging_dir(out) / "proposed_tropes.json").write_text(
        json.dumps(proposed, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return {
        "judged": len(rows),
        "copyable": {v: sum(1 for r in rows if r["copyable"] == v) for v in COPYABLE},
        "existing_work_check": "skipped" if skip_ip_check else "applied",
        "proposed_tropes": sorted(proposed),
        "unjudged_kept_videos": len(read_videos(out)) - len(rows),
    }


def write_batches(out: Path, top: int, size: int) -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise SystemExit(
            "The `batches` command needs Pillow. Run it as `uv run --with pillow python ...`."
        ) from None
    rows = read_videos(out)
    batches = plan_batches(rows, top, size)
    target = judging_dir(out)
    thumbs = target / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    listed = []
    for batch in batches:
        sheet_rows = (len(batch.videos) + SHEET_COLUMNS - 1) // SHEET_COLUMNS
        sheet = Image.new(
            "RGB", (SHEET_COLUMNS * THUMB_W, sheet_rows * THUMB_H), "black"
        )
        draw = ImageDraw.Draw(sheet)
        for i, video in enumerate(batch.videos):
            thumb = thumbs / f"{video['video_id']}.jpg"
            if not thumb.exists():
                urllib.request.urlretrieve(
                    f"https://i.ytimg.com/vi/{video['video_id']}/mqdefault.jpg", thumb
                )
            x, y = (i % SHEET_COLUMNS) * THUMB_W, (i // SHEET_COLUMNS) * THUMB_H
            sheet.paste(
                Image.open(thumb).convert("RGB").resize((THUMB_W, THUMB_H)), (x, y)
            )
            draw.rectangle((x, y, x + 46, y + 22), fill="yellow")
            draw.text((x + 4, y + 4), video["n"], fill="black")
        sheet_path = target / f"batch_{batch.batch_id}.jpg"
        sheet.save(sheet_path, quality=80)
        list_path = target / f"batch_{batch.batch_id}.json"
        payload = {
            "batch": batch.batch_id,
            "market": batch.market,
            "sheet": sheet_path.name,
            "videos": batch.videos,
        }
        list_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        listed.append(
            {
                "batch": batch.batch_id,
                "market": batch.market,
                "videos": len(batch.videos),
            }
        )
    planned = sum(b["videos"] for b in listed)
    return {
        "batches": listed,
        "to_judge": planned,
        "kept_videos": len(rows),
        "not_judged": len(rows) - planned,
    }


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("batches", "validate", "candidates", "merge"):
        cmd = sub.add_parser(name)
        cmd.add_argument(
            "--out", type=Path, required=True, help="the traction run's output folder"
        )
        if name == "batches":
            cmd.add_argument(
                "--top",
                type=int,
                default=50,
                help="videos per market, taken from the top of videos.csv",
            )
            cmd.add_argument("--size", type=int, default=25, help="videos per batch")
        if name == "validate":
            cmd.add_argument(
                "--batch", required=True, help="batch id, for example jp_01"
            )
        if name == "candidates":
            cmd.add_argument("--market", required=True)
        if name == "merge":
            cmd.add_argument(
                "--skip-ip-check",
                action="store_true",
                help="merge without the existing-work check",
            )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "batches":
        result = write_batches(args.out, args.top, args.size)
    else:
        vocabulary = load_vocabulary()
        batches = load_batches(args.out)
        if args.command == "validate":
            batch = next((b for b in batches if b.batch_id == args.batch), None)
            if batch is None:
                raise SystemExit(
                    f"No batch {args.batch!r}. Batches: {', '.join(b.batch_id for b in batches)}"
                )
            judged = load_judged(args.out, batch, vocabulary)
            result = {"batch": batch.batch_id, "valid": True, "judged": len(judged)}
        elif args.command == "candidates":
            by_id = {
                v["video_id"]: v
                for b in batches
                if b.market == args.market
                for v in b.videos
            }
            judged = [
                j
                for b in batches
                if b.market == args.market
                for j in load_judged(args.out, b, vocabulary)
            ]
            result = {
                "market": args.market,
                "candidates": [
                    {
                        **by_id[str(j["video_id"])],
                        "title_meaning": j.get("title_meaning"),
                        "trope": j.get("trope"),
                    }
                    for j in judged
                    if j["copyable"] == "yes"
                ],
            }
        else:
            result = merge(args.out, vocabulary, args.skip_ip_check)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
