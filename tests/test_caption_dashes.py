"""No em or en dash in any burned caption (user rule, 6 Oct 2026).

The script and the voice keep their dashes; what is drawn on the picture does
not: a cut-off ends on an ellipsis, a dash between words is a comma, a hyphen
inside a word stays.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import needs_ffmpeg
from test_reel import reel_desk  # noqa: F401  (fixture)

from creation.caption_dashes import DASH_IN_CAPTION, ELLIPSIS, caption_text
from creation.captions import (
    Cue,
    LetterboxBand,
    Span,
    build_ass,
    build_line_cues,
    caption_take,
)
from creation.post.hook_overlay import HookOverlay, overlay_ass
from creation.post.letterbox import TitleBlock, title_ass
from creation.post.reel import parse_ass_cues, run_reel
from creation.post.reel_cover import cover_ass, cover_layout

#: Lines as scripts write them: every kind of dash, spaced and not.
FIXTURE_LINES = (
    "Please—",
    "no no—",
    "I'm NOT—",
    "D-9341 — do not move.",
    "Wait–what did you say?",
    "I said -- stop",
    "It's a well-known trick - trust me —",
    '"Don\'t—" she said.',
    "—and then the lights went out",
    "Hold on — hold on — I can explain—",
)


@pytest.mark.parametrize(
    ("given", "drawn"),
    [
        ("Please—", "Please…"),
        ("no no—", "no no…"),
        ("I'm NOT—", "I'm NOT…"),
        ("Please —", "Please…"),
        ('"Don\'t—"', '"Don\'t…"'),
        ("Stop–", "Stop…"),
        ("Stop--", "Stop…"),
    ],
)
def test_a_dash_ending_a_cut_off_line_is_an_ellipsis(given: str, drawn: str) -> None:
    assert caption_text(given) == drawn
    assert drawn.endswith(("…", '…"'))
    assert ELLIPSIS == "…"


@pytest.mark.parametrize(
    ("given", "drawn"),
    [
        ("D-9341 — do not", "D-9341, do not"),
        ("D-9341—do not", "D-9341, do not"),
        ("wait – what", "wait, what"),
        ("I said -- stop", "I said, stop"),
        ("trust me - now", "trust me, now"),
        ("yes, — no", "yes, no"),
        ('"Go—" she said', '"Go," she said'),
        ("one—\ntwo", "one,\ntwo"),
        ("Hold on\n— wait", "Hold on,\nwait"),
        ("Hold on — hold on — wait", "Hold on, hold on, wait"),
    ],
)
def test_a_dash_between_words_is_a_comma(given: str, drawn: str) -> None:
    assert caption_text(given) == drawn


@pytest.mark.parametrize(
    "text",
    ["D-9341", "a well-known face", "b-but I", "X-ray at 9-5", "Wait, what?", "", "…"],
)
def test_hyphens_inside_words_and_plain_text_are_untouched(text: str) -> None:
    assert caption_text(text) == text


def test_an_opening_dash_is_dropped_and_the_rule_is_idempotent() -> None:
    assert caption_text("—and then") == "and then"
    for line in FIXTURE_LINES:
        once = caption_text(line)
        assert caption_text(once) == once
        assert not DASH_IN_CAPTION.search(once), (line, once)


def test_word_flicker_shows_the_cut_off_with_an_ellipsis() -> None:
    (cues,) = build_line_cues(["Please— no no—"], [Span(0.0, 2.0)])
    assert [c.text for c in cues] == ["Please…", "Please, no", "Please, no no…"]
    # Timing is the dashed line's: the same words at the same times.
    (dashed,) = build_line_cues(["Please— no no—"], [Span(0.0, 2.0)])
    (plain,) = build_line_cues(["Please… no no…"], [Span(0.0, 2.0)])
    assert [(c.start, c.end) for c in dashed] == [(c.start, c.end) for c in plain]


@pytest.mark.parametrize("chunking", ["three", "phrase"])
def test_every_built_cue_is_dash_free(chunking: str) -> None:
    spans = [Span(i * 3.0, i * 3.0 + 2.5) for i in range(len(FIXTURE_LINES))]
    groups = build_line_cues(
        list(FIXTURE_LINES), spans, chunking=chunking, italic=[True, False] * 5
    )
    texts = [c.text for g in groups for c in g]
    assert texts and not any(DASH_IN_CAPTION.search(t) for t in texts)
    whole = build_line_cues(list(FIXTURE_LINES), spans, whole_lines=True)
    assert not any(DASH_IN_CAPTION.search(c.text) for g in whole for c in g)


def _no_dash_in_events(ass: str) -> None:
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert events
    for line in events:
        assert "—" not in line and "–" not in line, line


def test_rendered_ass_for_fixture_lines_has_no_em_or_en_dash() -> None:
    cues = [Cue(i, i + 0.9, line) for i, line in enumerate(FIXTURE_LINES)]
    _no_dash_in_events(build_ass(cues, width=1080, height=1920))
    band = LetterboxBand(130, 950, 1380, 1300, 1560, 48, 36)
    _no_dash_in_events(build_ass(cues, width=1080, height=1920, band=band))
    assert "Please…" in build_ass(cues, width=1080, height=1920)


def test_hook_line_overlay_and_title_bar_have_no_dash() -> None:
    hook = HookOverlay("hook", "She lied — again—", "top", 0.0, 3.0)
    ass = overlay_ass(hook, width=1080, height=1920)
    _no_dash_in_events(ass)
    assert "She lied, again…" in ass
    bar = HookOverlay(
        "title_bar", "Part 2 — the door", "top", 0.0, 3.0, title="Show – One"
    )
    _no_dash_in_events(overlay_ass(bar, width=1080, height=1920))


def test_letterbox_title_band_has_no_dash() -> None:
    ass, _ = title_ass(TitleBlock("POV: your roommate — texted", '"Don\'t come home—"'))
    _no_dash_in_events(ass)
    assert "POV: your roommate, texted" in ass


def test_reel_cover_title_has_no_dash() -> None:
    ass = cover_ass(
        cover_layout(series="Night Shift — Ward 9", part=2, width=1080, height=1920)
    )
    _no_dash_in_events(ass)
    assert "Night Shift, Ward 9" in ass.replace("\\N", " ")


def _tone_take(path: Path) -> None:
    # 4 s clip: silence, a 1 s tone standing in for the line, silence.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=192x336:d=4",
         "-f", "lavfi", "-i", "sine=f=440:d=4",
         "-filter_complex", "[1:a]volume='if(between(t,1,2),1,0)':eval=frame[a]",
         "-map", "0:v", "-map", "[a]", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_finish_captions_burn_without_a_dash(tmp_path: Path) -> None:
    ep = tmp_path / "ep01"
    (ep / "api").mkdir(parents=True)
    (ep / "takes").mkdir()
    spine = {
        "beats": [{"episode_id": "episode_01", "dialogue_lines": [{"text": "D-9341 — please—"}]}],
    }  # fmt: skip
    (ep / "api" / "03_spine.json").write_text(json.dumps(spine), encoding="utf-8")
    _tone_take(ep / "takes" / "take-ep01-t1-raw-v1.mp4")
    result = caption_take(tmp_path)
    texts = [c.text for c in result.cues]
    assert texts[-1] == "D-9341, please…"
    _no_dash_in_events(result.ass.read_text(encoding="utf-8"))


@needs_ffmpeg
def test_reel_captions_burn_without_a_dash(reel_desk: Path) -> None:  # noqa: F811
    cap = reel_desk / "ep01" / "takes" / "take-ep01-t1-cap-v1.ass"
    cap.write_text(
        "[Events]\n"
        "Dialogue: 0,0:00:00.30,0:00:01.20,House,,0,0,0,,Open the door—\n"
        "Dialogue: 0,0:00:03.10,0:00:04.00,House,,0,0,0,,Who — is there\n"
        "Dialogue: 0,0:00:04.60,0:00:05.30,House,,0,0,0,,It was me–\n",
        encoding="utf-8",
    )
    result = run_reel(reel_desk, episode=1, seconds=6.0)
    assert result.ass is not None
    ass = result.ass.read_text(encoding="utf-8")
    _no_dash_in_events(ass)
    assert [c.text for c in parse_ass_cues(ass)][-1] == "It was me…"
