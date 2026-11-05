"""`finish --take-file` a freeze/soften of the raw take: captions still time on the raw take's transcript.

Hanakaze ep 2-3: `finish --take-file take-ep02-t1-freeze-v5.mp4` refused the transcript ("not the raw take the
server transcribes") although a freeze keeps the sound timeline. The edit chain (``edit-chain.jsonl``, or the run
notes on an older desk) now says how the file was made.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

from conftest import make_tone, needs_ffmpeg

from creation.post.finish import run_finish
from creation.post.lineage import raw_take_behind, record_edit
from test_captions_word_timing import _japanese_desk, _japanese_words


def _finish(desk: Path, take_file: Path, asked: list[str]) -> tuple[object, str]:
    def transcriber(d: Path, episode: int, take_id: str) -> Path:
        asked.append(take_id)
        return _japanese_words(
            d / "ep01" / "takes" / "take-ep01-t1-review-words-v1.json"
        )

    out = io.StringIO()
    result = run_finish(
        desk,
        take_file=take_file,
        sfx_render=lambda cue, target: make_tone(target, seconds=cue.seconds, freq=300),
        facts_fetcher=lambda *a: None,
        transcriber=transcriber,
        stream=out,
    )
    return result, out.getvalue()


def _captions(result: object) -> str:
    return next(s for s in result.steps if s.step == "captions").detail  # type: ignore[attr-defined]


@needs_ffmpeg
def test_a_file_of_unknown_origin_is_still_refused(post_desk: Path) -> None:
    _japanese_desk(post_desk)
    takes = post_desk / "ep01" / "takes"
    stranger = takes / "take-ep01-t1-handmade-v1.mp4"
    shutil.copy(takes / "take-ep01-t1-raw-v1.mp4", stranger)
    asked: list[str] = []

    result, out = _finish(post_desk, stranger, asked)

    # No record of which take it is: nothing is transcribed, and the line check says it did not run.
    assert asked == []
    assert "!! line check NOT RUN: no record of which raw take" in out, out
    assert "is not the raw take the server transcribes (no record of how" in _captions(
        result
    )


def test_an_older_desk_is_read_from_its_run_notes(tmp_path: Path) -> None:
    takes = tmp_path / "ep02" / "takes"
    takes.mkdir(parents=True)
    (tmp_path / "ep02" / "run-notes.md").write_text(
        "`take-ep02-t1-raw-v1.mp4`: Freeze at 3.42s held 0.60s -> `take-ep02-t1-freeze-v1.mp4` "
        "(duration 15.10s, audio timeline unchanged)\n"
        "Softened 2 cut(s) found at 3.88s, 7.33s in `take-ep02-t1-freeze-v1.mp4` -> `take-ep02-t1-soften-v1.mp4` "
        "(hold-and-fade 0.33 s; length and sound unchanged)\n",
        encoding="utf-8",
    )

    lineage = raw_take_behind(tmp_path, takes / "take-ep02-t1-soften-v1.mp4")

    assert lineage.raw == (takes / "take-ep02-t1-raw-v1.mp4").resolve()
    assert lineage.keeps_timeline
    assert lineage.chain_text() == "take-ep02-t1-raw-v1.mp4 -> freeze -> soften"


def test_one_timeline_breaking_edit_anywhere_in_the_chain_is_named(
    tmp_path: Path,
) -> None:
    takes = tmp_path / "ep01" / "takes"
    takes.mkdir(parents=True)
    raw = takes / "take-ep01-t1-raw-v1.mp4"
    trimmed = takes / "take-ep01-t1-trim-v1.mp4"
    frozen = takes / "take-ep01-t1-freeze-v1.mp4"
    record_edit(tmp_path, op="trim", source=raw, output=trimmed)
    record_edit(tmp_path, op="freeze", source=trimmed, output=frozen)

    lineage = raw_take_behind(tmp_path, frozen)

    assert lineage.raw == raw.resolve() and not lineage.keeps_timeline
    assert (
        "`take-ep01-t1-trim-v1.mp4` came from `take-ep01-t1-raw-v1.mp4` by trim"
        in lineage.reason
    )
