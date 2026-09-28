"""Whisper word timing (made on the server), and matching approved lines to what was heard (English)."""

from __future__ import annotations

import hashlib
import json
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
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"\w+", unicodedata.normalize("NFKC", text).lower()):
        if _CJK.search(token):
            tokens.extend(token)
        else:
            tokens.append(token)
    return tokens


def _same(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))


def _match(flat: list[tuple[str, int]], target: list[str], cursor: int) -> tuple[int, int, float] | None:
    if not target:
        return None
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


def line_windows(words: tuple[Word, ...], lines: tuple[str, ...]) -> tuple[LineWindow, ...]:
    """Match approved lines, in order, to Whisper words.

    Parameters
    ----------
    words
        Whisper words.
    lines
        Approved lines in spoken order.

    Returns
    -------
    tuple[LineWindow, ...]
        One window per line; ``start is None`` when the line was not heard.
    """

    flat = [(token, index) for index, word in enumerate(words) for token in _tokens(word.text)]
    cursor = 0
    found: list[LineWindow] = []
    for index, line in enumerate(lines):
        match = _match(flat, _tokens(line), cursor)
        if match is None:
            found.append(LineWindow(index, line, None, None, 0.0))
            continue
        first, last, ratio = match
        ids = sorted({flat[pos][1] for pos in range(first, last + 1)})
        found.append(LineWindow(index, line, words[ids[0]].start, words[ids[-1]].end, round(ratio, 2)))
        cursor = last + 1
    return tuple(found)
