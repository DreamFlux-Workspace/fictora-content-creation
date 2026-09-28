"""Whisper word timing (made on the server), and matching approved lines to what was heard.

English and Korean lines are matched word by word / syllable by syllable. A
Japanese line is matched by reading shape: kana are folded (hiragana to
katakana, no long-vowel mark) and a kanji Whisper heard may stand for the kana
of a kana-pinned line (and the reverse). There is no reading dictionary here
(the kit adds no heavy dependency), so a kanji is never actually *read*: see
:func:`_align_japanese` for what that still misses.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from creation.post.audio_service import AudioService

#: Share of a line's words heard in order for the line to count as heard.
LINE_HEARD_RATIO = 0.6

Transcriber = Callable[[Path, Path], Path]
"""``(media, out_json) -> out_json`` with Whisper ``chunks`` saved."""


@dataclass(frozen=True)
class Word:
    """One Whisper word with its time in seconds."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class LineWindow:
    """Where one approved line was heard (``start is None`` when it was not)."""

    index: int
    line: str
    start: float | None
    end: float | None
    ratio: float


def transcribe(
    audio_url: str, out_json: Path, *, audio: AudioService, spine_id: str | None = None, language: str = "en"
) -> Path:
    """Get Whisper word timings for a file already in our storage (the take's stored URL) and save them.

    This kit never uploads local files: a local mix keeps the take's timing, so
    the stored raw take is what gets transcribed.

    Parameters
    ----------
    audio_url
        Durable URL of the take (``17_raw_scene_clips.json``) or of audio the server made.
    out_json
        Where the answer is saved.
    audio
        The generated-audio service.
    spine_id
        Books the call on the story.
    language
        Whisper language (English).

    Returns
    -------
    Path
        ``out_json``.
    """

    key = "whisper-" + hashlib.sha256(f"{audio_url}|{language}".encode()).hexdigest()[:16]
    output = audio.transcribe(audio_url=audio_url, language=language, spine_id=spine_id, key=key)
    out_json.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_json


def load_words(path: Path) -> tuple[Word, ...]:
    """Word times from a saved transcript: the API's ``words`` (``{word, start, end}``) or Whisper ``chunks``.

    Raises
    ------
    ValueError
        When the file has no ``chunks`` list.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("words"), list):
        return tuple(
            Word(start=float(w.get("start") or 0.0), end=float(w.get("end") or w.get("start") or 0.0),
                 text=str(w.get("word") or "").strip())
            for w in payload["words"]
        )  # fmt: skip
    chunks = payload.get("chunks") if isinstance(payload, dict) else None
    if not isinstance(chunks, list):
        raise ValueError(f"{path} has no Whisper words or chunks")
    words: list[Word] = []
    for chunk in chunks:
        stamp = chunk.get("timestamp") or [None, None]
        start = float(stamp[0]) if stamp[0] is not None else (words[-1].end if words else 0.0)
        end = float(stamp[1]) if stamp[1] is not None else start
        words.append(Word(start=start, end=end, text=str(chunk.get("text") or "").strip()))
    return tuple(words)


#: Japanese / Chinese / Korean script: matched by character (Whisper and the script split words differently).
_CJK = re.compile(r"[\u3005\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
#: Kanji (and the repeat mark 々): one of these is heard where the script may have its reading in kana.
_HAN = re.compile(r"[\u3005\u3400-\u9fff]")
#: Katakana after folding (hiragana is folded to katakana, so this is all kana).
_KANA = re.compile(r"[\u30a1-\u30fa]")
#: Most kana one kanji can stand for when matching a kana line to Whisper's kanji (東=とう, 京=きょう).
MAX_KANA_PER_KANJI = 4
#: Share of a Japanese line's characters that must be heard as the same kana (not stood in for by a
#: kanji); at least one always. Kanji alone never count as hearing a line.
KANA_LITERAL_RATIO = 0.15


def _fold_kana(char: str) -> str:
    """Hiragana to katakana, so ここ and ココ compare equal (Whisper writes either)."""

    return chr(ord(char) + 0x60) if "\u3041" <= char <= "\u3096" else char


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"\w+", unicodedata.normalize("NFKC", text).lower()):
        if _CJK.search(token):
            # One token per character, kana folded to katakana, the long-vowel mark dropped
            # (Whisper and a kana pin disagree on it: ラーメン / らあめん / らめん).
            tokens.extend(_fold_kana(char) for char in token if char != "\u30fc")
        else:
            tokens.append(token)
    return tokens


def _same(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))


def _match(flat: list[tuple[str, int]], target: list[str], cursor: int) -> tuple[int, int, float] | None:
    if not target:
        return None
    if any(_KANA.fullmatch(token) for token in target):
        return _match_japanese(flat, target, cursor)
    for start in range(cursor, len(flat)):
        if not any(_same(flat[start][0], token) for token in target[:2]):
            continue
        pos, matched, last = start, 0, start
        for token in target:
            probe = pos
            while probe < len(flat) and probe - pos <= 2 and not _same(flat[probe][0], token):
                probe += 1
            if probe < len(flat) and probe - pos <= 2:
                matched += 1
                last = probe
                pos = probe + 1
        ratio = matched / len(target)
        if ratio >= LINE_HEARD_RATIO:
            return start, last, ratio
    return None


def _align_japanese(heard: list[str], target: list[str]) -> tuple[float, int, int] | None:
    """Best alignment of a Japanese line against heard characters starting at ``heard[0]``.

    Kana match kana. Without a reading dictionary a kanji cannot be read, so a
    heard kanji may stand for 1-4 of the line's kana (a kana-pinned line against
    Whisper's kanji), and a kanji in the line may stand for 1-4 heard kana. Only
    exact kana count as *literal*; a heard kana left unmatched inside the window
    costs one.

    Returns
    -------
    tuple[float, int, int] | None
        ``(covered, literal, last_heard_index)`` for the whole line, or ``None``.
    """

    n, m = len(target), len(heard)
    # best[i][j]: (covered, literal, last) after using target[:i] and heard[:j].
    best: list[list[tuple[float, int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = (0.0, 0, -1)

    def offer(i: int, j: int, value: tuple[float, int, int]) -> None:
        current = best[i][j]
        if current is None or value[:2] > current[:2]:
            best[i][j] = value

    for i in range(n + 1):
        for j in range(m + 1):
            state = best[i][j]
            if state is None:
                continue
            covered, literal, last = state
            if j < m and last >= 0:  # a heard character not in the line (never before the first match)
                offer(i, j + 1, (covered - (1 if _KANA.fullmatch(heard[j]) else 0), literal, last))
            if i < n:
                offer(i + 1, j, state)  # a character of the line not heard
            if i < n and j < m:
                if heard[j] == target[i]:
                    offer(i + 1, j + 1, (covered + 1, literal + 1, j))
                if _HAN.fullmatch(heard[j]):
                    for k in range(1, MAX_KANA_PER_KANJI + 1):
                        if i + k > n or not _KANA.fullmatch(target[i + k - 1]):
                            break
                        offer(i + k, j + 1, (covered + k, literal, j))
                if _HAN.fullmatch(target[i]):
                    for k in range(1, MAX_KANA_PER_KANJI + 1):
                        if j + k > m or not _KANA.fullmatch(heard[j + k - 1]):
                            break
                        offer(i + 1, j + k, (covered + 1, literal, j + k - 1))
    finals = [state for state in best[n] if state is not None and state[2] >= 0]
    return max(finals, key=lambda state: state[:2]) if finals else None


def _match_japanese(flat: list[tuple[str, int]], target: list[str], cursor: int) -> tuple[int, int, float] | None:
    """:func:`_match` for a line with kana: compared by reading shape, not character for character."""

    n = len(target)
    need_literal = max(1, math.ceil(n * KANA_LITERAL_RATIO))
    window = 2 * n + MAX_KANA_PER_KANJI
    for start in range(cursor, len(flat)):
        first = flat[start][0]
        kanji_opens_line = _HAN.fullmatch(target[0]) is not None and _KANA.fullmatch(first) is not None
        if first not in target[:2] and not _HAN.fullmatch(first) and not kanji_opens_line:
            continue
        aligned = _align_japanese([token for token, _ in flat[start : start + window]], target)
        if aligned is None:
            continue
        covered, literal, last = aligned
        ratio = covered / n
        if ratio >= LINE_HEARD_RATIO and literal >= need_literal:
            return start, start + last, ratio
    return None


def line_windows(
    words: tuple[Word, ...],
    lines: tuple[str, ...],
    *,
    alternates: tuple[tuple[str, ...], ...] | None = None,
) -> tuple[LineWindow, ...]:
    """Match approved lines, in order, to Whisper words.

    Parameters
    ----------
    words
        Whisper words.
    lines
        Approved lines in spoken order.
    alternates
        Other spellings of each line (same order as ``lines``), such as its
        ``text`` and ``spoken_text``: a line counts as heard when any spelling
        is. The earliest place any spelling is heard wins.

    Returns
    -------
    tuple[LineWindow, ...]
        One window per line; ``start is None`` when the line was not heard.
    """

    flat = [(token, index) for index, word in enumerate(words) for token in _tokens(word.text)]
    cursor = 0
    found: list[LineWindow] = []
    for index, line in enumerate(lines):
        spellings = [line, *(alternates[index] if alternates and index < len(alternates) else ())]
        matches = [m for m in (_match(flat, _tokens(s), cursor) for s in dict.fromkeys(spellings) if s) if m]
        if not matches:
            found.append(LineWindow(index, line, None, None, 0.0))
            continue
        first, last, ratio = min(matches, key=lambda m: (m[0], -m[2]))
        ids = sorted({flat[pos][1] for pos in range(first, last + 1)})
        found.append(LineWindow(index, line, words[ids[0]].start, words[ids[-1]].end, round(ratio, 2)))
        cursor = last + 1
    return tuple(found)
