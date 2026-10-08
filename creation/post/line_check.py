"""Did a model-voice take say its script? The "missing or wrong line" stop before ``finish`` delivers.

Founder decision (8 Oct 2026, learning L-20260924-10, seen 6x; Not Home 6 Oct
played a line over the wrong character): a show filmed in the video model's
own voices (voice mode ``model``, or any take whose take facts say
``soundtrack.mode`` is not ``target_audio``) keeps those voices, but the video
model writes the words itself and sometimes drops, changes, repeats or invents
a line, or says it on another shot. After filming, the take's transcript is
compared with its scripted lines; on a fault ``finish`` STOPS before anything
is made and names the take, the line, what was heard, and the re-film command.
Re-filming is the remedy: laying a separate voice line over the take breaks
lip sync. Only a line heard off screen (no lips) may be patched instead, as an
explicit, warned option.

Locked-voice takes (``target_audio``) are not checked here: their words are
rendered and checked before filming and cannot drift.

How a line is matched (:func:`check_take_lines`)
--------------------------------------------------
Both the script and the transcript are normalised first (:func:`normalise`):
Unicode NFKC, lower case, apostrophes dropped (``don't`` = ``dont``), and
numbers written one way (English digits as words, ``3`` = ``three``;
Japanese digits as kanji numerals, ``3`` = ``三``; Korean digits as Sino-Korean
syllables, ``3`` = ``삼``). Punctuation never counts. Then each line is found
in order with the kit's word matcher (:func:`creation.post.whisper.line_windows`:
word by word in English, character by character in Japanese and Korean, a
Japanese line on the server's kana readings, cast names by sound). A line's
**score** is the share of its words (characters for ja/ko) heard in order.

- score >= :data:`LINE_OK_RATIO` (0.7): heard. Small wording differences pass
  (a 10-word line may lose or change 3 words; "right here" for "here").
- matched but under 0.7, or matched with many words the script does not have
  inside it: **WRONG WORDS**.
- not matched: **WRONG WORDS** when there is unscripted speech where the line
  should be (what was heard is printed), else **MISSING**.
- a line under :data:`SHORT_LINE_WORDS` words (:data:`SHORT_LINE_CHARS`
  characters in Japanese / Korean) is never a stop on the transcript alone
  (transcripts miss "Hey.", and read うまい as 美味い): it prints ``CHECK BY EAR``.
- unscripted speech left over that matches a line already heard: **REPEATED**.
- other unscripted speech of :data:`EXTRA_MIN_WORDS` words (or
  :data:`EXTRA_MIN_CHARS` Japanese/Korean characters) or more, fillers ("um",
  "oh") and known transcript noise ("thanks for watching") left out:
  **EXTRA SPEECH**. A ``finish --mute`` window over it clears it.
- an on-screen line with most of its heard words more than :data:`SHOT_SLACK_SECONDS`
  outside the shot the take facts put it in (``lines[].start_seconds`` /
  ``end_seconds``): **WRONG SHOT** (it plays over someone else's face).

Cost: the transcript is the server's Whisper route (``POST /v1/transcripts``),
booked at $0 by the server and cached per take media, so a repeated finish of
the same take reads the saved file on the desk and asks nothing.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx

from creation.post.whisper import Word, _tokens, line_windows, word_count

#: Share of a line's words heard in order for it to count as said (small wording differences pass).
LINE_OK_RATIO = 0.7
#: Lines under this many words are never a stop on the transcript alone (``CHECK BY EAR``).
SHORT_LINE_WORDS = 3
#: The same for a Japanese / Chinese / Korean line, counted in characters ("うまい" is three).
SHORT_LINE_CHARS = 5
#: Unscripted words in a row that count as invented speech (English and other spaced languages).
EXTRA_MIN_WORDS = 3
#: Unscripted characters in a row that count as invented speech (Japanese, Chinese, Korean).
EXTRA_MIN_CHARS = 5
#: Seconds a line's middle may sit outside its planned shot before it is on the wrong shot.
SHOT_SLACK_SECONDS = 0.75
#: Words inside a matched line the script does not have, beyond which the line is wrong words:
#: at least this many, and at least half the line.
INSERTED_MIN_WORDS = 3

#: The finish flag that delivers a faulted take anyway (logged in the run notes).
ACCEPT_FLAG = "--accept-line-mismatch"

#: Sounds that are not words: never invented speech on their own.
FILLERS = frozenset(
    "um umm uh uhh uhm hmm hm mm mmm mhm ah ahh aah oh ooh huh er erm eh ha haha hah heh whoa wow ow ugh "
    "あ ア え エ う ウ ん ン お オ ー は ハ ふ フ 음 어 아 오 으 응 흠 헉 하".split()
)
#: Whisper's well-known inventions over silence or music: transcript noise, not speech.
TRANSCRIPT_NOISE = (
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "subscribe",
    "bye",
    "ご視聴ありがとうございました",
    "ありがとうございました",
    "시청해주셔서 감사합니다",
    "감사합니다",
)

#: Shows whose transcript must carry Japanese / Chinese / Korean script to be read.
CJK_LANGUAGES = frozenset({"ja", "zh", "ko"})
_CJK = re.compile(r"[々぀-ヿ㐀-鿿가-힯]")
_APOSTROPHES = re.compile(r"['’‘`]")

_EN_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen"
).split()
_EN_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_JA_DIGITS = "〇一二三四五六七八九"
_KO_DIGITS = ("영", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구")


def _english_number(n: int) -> str:
    if n < 20:
        return _EN_ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _EN_TENS[tens] + (f" {_EN_ONES[ones]}" if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return f"{_EN_ONES[hundreds]} hundred" + (
            f" {_english_number(rest)}" if rest else ""
        )
    if n < 1_000_000:
        thousands, rest = divmod(n, 1000)
        return f"{_english_number(thousands)} thousand" + (
            f" {_english_number(rest)}" if rest else ""
        )
    return " ".join(_EN_ONES[int(d)] for d in str(n))


def _cjk_number(
    n: int, digits: Sequence[str], units: tuple[str, str, str], ten_thousand: str
) -> str:
    """``n`` in kanji / Sino-Korean numerals (一 is dropped before 十百千, as both languages say them)."""

    if n == 0:
        return digits[0]
    if n >= 100_000_000:
        return "".join(digits[int(d)] for d in str(n))
    high, low = divmod(n, 10_000)
    out = ""
    if high:
        out += (
            ""
            if high == 1 and digits[1] == "일"
            else _cjk_number(high, digits, units, ten_thousand)
        ) + ten_thousand
    for value, unit in ((1000, units[2]), (100, units[1]), (10, units[0])):
        count, low = divmod(low, value)
        if count:
            out += ("" if count == 1 else digits[count]) + unit
    if low:
        out += digits[low]
    return out


def _numbers(text: str, language: str) -> str:
    def spell(match: re.Match[str]) -> str:
        n = int(match.group(0).replace(",", ""))
        if language == "ja":
            return _cjk_number(n, _JA_DIGITS, ("十", "百", "千"), "万")
        if language == "ko":
            return _cjk_number(n, _KO_DIGITS, ("십", "백", "천"), "만")
        return f" {_english_number(n)} "

    return re.sub(r"\d{1,3}(?:,\d{3})+|\d+", spell, text)


def normalise(text: str, language: str = "en") -> str:
    """One spelling of what is said: NFKC, lower case, no apostrophes, numbers written one way.

    Parameters
    ----------
    text
        A script line or a transcript word.
    language
        The show's two-letter language (``en``, ``ja``, ``ko``, ...).

    Returns
    -------
    str
        The text to match on (punctuation is dropped later, by the matcher's tokens).
    """

    text = unicodedata.normalize("NFKC", text).lower()
    text = _APOSTROPHES.sub("", text)
    return re.sub(r"\s+", " ", _numbers(text, language)).strip()


@dataclass(frozen=True)
class ScriptLine:
    """One scripted line of the take, as the check reads it."""

    number: int
    line_id: str
    #: What should be heard (``spoken_text``, else ``text``).
    performed: str
    #: Every spelling it may be heard as (``text``, ``spoken_text``).
    spellings: tuple[str, ...] = ()
    speaker: str = ""
    off_screen: bool = False
    #: The shot the take facts put it in, and that shot's window (take seconds).
    shot_index: int | None = None
    window: tuple[float, float] | None = None


@dataclass(frozen=True)
class LineFault:
    """One thing the take got wrong about its script."""

    #: ``missing`` | ``wrong`` | ``repeated`` | ``extra`` | ``wrong_shot``.
    kind: str
    line: ScriptLine | None
    #: What the transcript heard there (empty for a missing line).
    heard: str = ""
    at: tuple[float, float] | None = None
    detail: str = ""

    LABELS = {
        "missing": "MISSING LINE",
        "wrong": "WRONG WORDS",
        "repeated": "REPEATED LINE",
        "extra": "EXTRA SPEECH",
        "wrong_shot": "WRONG SHOT",
    }

    def describe(self) -> str:
        """``MISSING LINE: line 2 (Aya) "Not tonight." was not heard`` and the like."""

        label = self.LABELS[self.kind]
        when = f" at {self.at[0]:.2f}-{self.at[1]:.2f}s" if self.at else ""
        if self.line is not None:
            who = f" ({self.line.speaker})" if self.line.speaker else ""
            what = f'line {self.line.number}{who} "{self.line.performed}"'
        else:
            what = ""
        if self.kind == "missing":
            return f"{label}: {what} was not heard anywhere in the take" + (
                f" ({self.detail})" if self.detail else ""
            )
        if self.kind == "wrong":
            return f'{label}: {what} was heard as "{self.heard}"{when}' + (
                f" ({self.detail})" if self.detail else ""
            )
        if self.kind == "repeated":
            return f'{label}: {what} was said again{when} ("{self.heard}")'
        if self.kind == "extra":
            return f'{label}: the take says words that are not in the script{when}: "{self.heard}"'
        return f"{label}: {what} was heard{when}, {self.detail}"


@dataclass
class LineCheck:
    """What the check found on one take."""

    take_id: str
    lines: tuple[ScriptLine, ...] = ()
    faults: list[LineFault] = field(default_factory=list)
    #: Notes that are never a stop (a short line to check by ear, a patched off-screen line).
    notes: list[str] = field(default_factory=list)
    #: Why the lines could not be compared (a transcript in another language); empty when they were.
    unread: str = ""
    #: Each line heard: ``(line, start, end, score)``.
    heard: list[tuple[ScriptLine, float, float, float]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing would stop ``finish``."""

        return not self.faults and not self.unread

    def summary(self) -> str:
        """``line check: 3 of 3 scripted line(s) heard as written`` (or the fault count)."""

        if self.unread:
            return f"line check: NOT CHECKED ({self.unread})"
        said = len(self.heard)
        base = f"line check: {said} of {len(self.lines)} scripted line(s) heard as written, on their shot"
        if self.faults:
            base += f"; {len(self.faults)} fault(s)"
        return base


def _short(line: str) -> bool:
    """Too short to stop on the transcript alone: under 3 words, or 5 characters in ja/zh/ko."""

    size = word_count(line)
    return size < (SHORT_LINE_CHARS if _CJK.search(line) else SHORT_LINE_WORDS)


def _flat(words: Sequence[Word]) -> list[tuple[str, int]]:
    return [
        (token, index)
        for index, word in enumerate(words)
        for token in _tokens(word.text)
    ]


def _text_of(words: Sequence[Word], indices: Sequence[int]) -> str:
    joined = []
    for index in indices:
        text = words[index].text
        if (
            joined
            and _CJK.search(text[:1] or "")
            and _CJK.search(joined[-1][-1:] or "")
        ):
            joined[-1] += text
        else:
            joined.append(text)
    return " ".join(joined).strip()


def _is_noise(text: str, language: str) -> bool:
    key = " ".join(_tokens(normalise(text, language)))
    return any(
        key == " ".join(_tokens(normalise(noise, language)))
        for noise in TRANSCRIPT_NOISE
    )


def _runs(indices: Sequence[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for index in indices:
        if runs and index == runs[-1][-1] + 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    return runs


def _speech_size(words: Sequence[Word], run: Sequence[int]) -> int:
    """Spoken units in ``run``: words, or characters for Japanese / Korean; fillers left out."""

    size = 0
    for index in run:
        for token in _tokens(words[index].text):
            if token in FILLERS:
                continue
            size += 1
    return size


def _is_extra(words: Sequence[Word], run: Sequence[int], language: str) -> bool:
    size = _speech_size(words, run)
    cjk = any(_CJK.search(words[index].text) for index in run)
    if size < (EXTRA_MIN_CHARS if cjk else EXTRA_MIN_WORDS):
        return False
    return not _is_noise(_text_of(words, run), language)


def check_take_lines(
    lines: Sequence[ScriptLine],
    words: Sequence[Word],
    *,
    take_id: str,
    language: str = "en",
    muted: Sequence[tuple[float, float]] = (),
    patched: Sequence[str] = (),
) -> LineCheck:
    """Compare a model-voice take's transcript with its scripted lines.

    Parameters
    ----------
    lines
        The take's scripted lines, in order (with their planned shot windows when known).
    words
        The transcript of the take as filmed (Whisper words with times).
    take_id
        ``t1`` (for the messages).
    language
        The show's two-letter language: numbers are normalised its way.
    muted
        ``finish --mute`` windows: speech inside one is taken out, never invented speech.
    patched
        Line ids a ``finish --voice`` line lays in by hand. Only an off-screen line may be patched
        (no lips to miss); its missing or wrong words become a warned note.

    Returns
    -------
    LineCheck
        The faults (each a stop) and the notes (never a stop).
    """

    check = LineCheck(take_id=take_id, lines=tuple(lines))
    if (
        language in CJK_LANGUAGES
        and words
        and not any(_CJK.search(word.text) for word in words)
    ):
        check.unread = (
            f"the transcript has no {language} words (it was made in another language), so the lines "
            "were not compared; make one in the show's language: review --transcribe"
        )
        return check
    norm_words = tuple(
        replace(word, text=normalise(word.text, language)) for word in words
    )
    kept = [
        index
        for index, word in enumerate(norm_words)
        if not any(
            start - 0.05 <= (word.start + word.end) / 2 <= end + 0.05
            for start, end in muted
        )
    ]
    heard_words = tuple(norm_words[index] for index in kept)
    if not lines:
        windows: tuple[Any, ...] = ()
    else:
        windows = line_windows(
            heard_words,
            tuple(normalise(line.performed, language) for line in lines),
            alternates=tuple(
                tuple(normalise(s, language) for s in line.spellings) for line in lines
            ),
        )
    claimed: set[int] = set()
    spans: dict[str, tuple[int, ...]] = {}
    tokens_of = [len(_tokens(word.text)) for word in heard_words]
    placed: list[tuple[ScriptLine, tuple[int, ...] | None, float]] = []
    for line, window in zip(lines, windows, strict=True):
        if window.start is None:
            placed.append((line, None, 0.0))
            continue
        span = tuple(range(window.words[0], window.words[-1] + 1))
        claimed.update(span)
        placed.append((line, span, window.ratio))

    def gap_for(position: int) -> list[int]:
        """Unclaimed words between the line before and the line after ``position`` (where it should be)."""

        before = max((s[-1] for _l, s, _r in placed[:position] if s), default=-1)
        after = min(
            (s[0] for _l, s, _r in placed[position + 1 :] if s),
            default=len(heard_words),
        )
        return [i for i in range(before + 1, after) if i not in claimed]

    def fault(kind: str, line: ScriptLine | None, **kw: Any) -> None:
        if (
            line is not None
            and line.line_id in patched
            and kind in ("missing", "wrong")
        ):
            if line.off_screen:
                check.notes.append(
                    f'!! line {line.number} "{line.performed}" is laid by hand (finish --voice), allowed only '
                    "because it is heard off screen: listen that it sits in the scene"
                )
                return
            check.notes.append(
                f"!! line {line.number} is laid by hand (finish --voice) but it is spoken ON screen: a laid "
                "line breaks lip sync, so it does not clear the fault (re-film the take)"
            )
        check.faults.append(LineFault(kind, line, **kw))

    for position, (line, span, ratio) in enumerate(placed):
        short = _short(line.performed)
        if span is None:
            gap = gap_for(position)
            said = [i for i in gap if _speech_size(heard_words, [i])]
            if short:
                check.notes.append(
                    f'CHECK BY EAR: line {line.number} "{line.performed}" is too short to judge on the transcript '
                    "alone and it was not found; listen before deciding (never a re-film on this alone)"
                )
                continue
            if said and _speech_size(heard_words, said) >= max(
                2, round(0.4 * word_count(line.performed))
            ):
                at = (heard_words[said[0]].start, heard_words[said[-1]].end)
                claimed.update(said)
                fault(
                    "wrong", line, heard=_text_of(words, [kept[i] for i in said]), at=at
                )
            else:
                fault("missing", line)
            continue
        at = (heard_words[span[0]].start, heard_words[span[-1]].end)
        size = sum(tokens_of[i] for i in span)
        target = len(_tokens(normalise(line.performed, language)))
        # Kanji and kana count differently: no inserted-word count for a Japanese / Chinese line.
        inserted = (
            0 if _CJK.search(line.performed) else max(0, size - round(ratio * target))
        )
        if not short and (
            ratio < LINE_OK_RATIO or inserted >= max(INSERTED_MIN_WORDS, target / 2)
        ):
            why = (
                f"{ratio:.0%} of its words heard, {LINE_OK_RATIO:.0%} needed"
                if ratio < LINE_OK_RATIO
                else f"{inserted} words the script does not have inside the line"
            )
            # The unscripted words around it, where the line should be, are what was said instead.
            said = sorted({*span, *(i for i in gap_for(position) if i not in claimed)})
            claimed.update(said)
            at = (heard_words[said[0]].start, heard_words[said[-1]].end)
            fault(
                "wrong",
                line,
                heard=_text_of(words, [kept[i] for i in said]),
                at=at,
                detail=why,
            )
            continue
        check.heard.append((line, at[0], at[1], ratio))
        spans[line.line_id] = span

    # Wrong shot: an on-screen line heard well outside the shot the take facts put it in.
    for line, start, end, _ratio in list(check.heard):
        if line.window is None or line.off_screen:
            continue
        low, high = line.window
        # Each heard word by its middle: Whisper stretches a line's first word back over the silence
        # before it (Hanakaze ep 6: one kanji "heard" from 8.81 s to 12.57 s), so one word never decides.
        middles = [
            (heard_words[i].start + heard_words[i].end) / 2 for i in spans[line.line_id]
        ]
        inside = sum(
            1
            for m in middles
            if low - SHOT_SLACK_SECONDS <= m <= high + SHOT_SLACK_SECONDS
        )
        if inside * 2 < len(middles):
            shot = f"shot {line.shot_index} " if line.shot_index is not None else ""
            check.heard.remove((line, start, end, _ratio))
            fault(
                "wrong_shot",
                line,
                at=(start, end),
                detail=f"but the take puts it in {shot}({low:.2f}-{high:.2f}s): it plays over another shot",
            )

    # What is left over: a line said again, or speech nobody wrote.
    left = [i for i in range(len(heard_words)) if i not in claimed]
    for run in _runs(left):
        if not _speech_size(heard_words, run):
            continue
        sub = tuple(heard_words[i] for i in run)
        again = None
        for line in lines:
            if _short(line.performed):
                continue
            found = line_windows(
                sub,
                (normalise(line.performed, language),),
                alternates=(tuple(normalise(s, language) for s in line.spellings),),
            )[0]
            if found.start is not None and found.ratio >= LINE_OK_RATIO:
                again = (line, found)
                break
        text = _text_of(words, [kept[i] for i in run])
        at = (heard_words[run[0]].start, heard_words[run[-1]].end)
        if again is not None:
            fault("repeated", again[0], heard=text, at=at)
        elif _is_extra(heard_words, run, language):
            fault("extra", None, heard=text, at=at)
    check.faults.sort(
        key=lambda f: f.at[0] if f.at else (f.line.number if f.line else 0)
    )
    return check


def script_lines_for_take(
    lines: Sequence[Mapping[str, str]],
    facts: Mapping[str, Any] | None,
    *,
    cast_names: Mapping[str, str] | None = None,
    off_screen_ids: Sequence[str] = (),
) -> list[ScriptLine]:
    """The take's approved lines (:func:`creation.post.review.take_lines`) with their planned shot windows.

    Parameters
    ----------
    lines
        ``{line_id, cast_id, text, spoken_text, performed}`` per line, in order.
    facts
        The saved take facts (``{"take_facts": {...}}`` or the facts alone); their ``lines[]`` give each
        line's shot and that shot's window. ``None``: no wrong-shot check.
    cast_names
        ``cast_id`` to name, for the messages.
    off_screen_ids
        Lines heard off screen (no wrong-shot check; the only lines a hand voice line may patch).

    Returns
    -------
    list[ScriptLine]
        One per line, numbered from 1.
    """

    body = (facts or {}).get("take_facts", facts or {})
    placed = {
        str(row.get("line_id")): row
        for row in (body.get("lines") or [])
        if isinstance(row, Mapping) and row.get("line_id")
    }
    names = cast_names or {}
    out: list[ScriptLine] = []
    for number, line in enumerate(lines, start=1):
        row = placed.get(str(line.get("line_id")), {})
        start, end = row.get("start_seconds"), row.get("end_seconds")
        window = (
            (float(start), float(end))
            if isinstance(start, (int, float))
            and isinstance(end, (int, float))
            and end > start
            else None
        )
        performed = str(
            line.get("performed") or line.get("spoken_text") or line.get("text") or ""
        )
        spellings = tuple(
            s
            for s in (str(line.get("text") or ""), str(line.get("spoken_text") or ""))
            if s and s != performed
        )
        cast_id = str(line.get("cast_id") or "")
        out.append(
            ScriptLine(
                number=number,
                line_id=str(line.get("line_id") or ""),
                performed=performed,
                spellings=spellings,
                speaker=names.get(cast_id, ""),
                off_screen=str(line.get("line_id")) in set(off_screen_ids),
                shot_index=int(row["shot_index"])
                if row.get("shot_index") is not None
                else None,
                window=window,
            )
        )
    return out


def patched_line_ids(
    lines: Sequence[ScriptLine], texts: Sequence[str], language: str = "en"
) -> list[str]:
    """Line ids a ``finish --voice`` line says (its saved words heard as the line, same threshold)."""

    found: list[str] = []
    for text in texts:
        if not text:
            continue
        words = tuple(
            Word(float(i), float(i) + 0.5, token)
            for i, token in enumerate(normalise(text, language).split())
        )
        for line in lines:
            window = line_windows(
                words,
                (normalise(line.performed, language),),
                alternates=(tuple(normalise(s, language) for s in line.spellings),),
            )[0]
            if window.start is not None and window.ratio >= LINE_OK_RATIO:
                found.append(line.line_id)
    return found


def desk_line_check(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    take: Path,
    facts: Mapping[str, Any] | None,
    transcriber: Callable[..., Path] | None = None,
    muted: Sequence[tuple[float, float]] = (),
    voice_texts: Sequence[str] = (),
) -> tuple[LineCheck | None, str]:
    """Run the line check on a desk's model-voice take: its transcript (made once, $0) against its script.

    Parameters
    ----------
    desk
        Series desk.
    episode, take_id
        Which take.
    take
        The file being finished (its raw take's transcript is the one read).
    facts
        The take facts (planned shot of each line), or ``None``.
    transcriber
        ``(desk, episode, take_id) -> words JSON``: the server's Whisper transcript of the stored take
        (:func:`creation.post.review.server_transcript`, the default), asked only when none of this
        take is saved on the desk.
    muted
        ``finish --mute`` windows.
    voice_texts
        The words of each ``finish --voice`` line.

    Returns
    -------
    tuple[LineCheck | None, str]
        The check, and which transcript it read; ``None`` and why when it could not run.
    """

    from creation.post.audio_service import AudioServiceError
    from creation.post.desk import saved_spine
    from creation.post.review import server_transcript, take_lines, take_words
    from creation.post.whisper import load_words
    from creation.spine_view import heard_line_ids

    found = take_lines(desk, episode, take_id)
    spine = saved_spine(desk, episode)
    if found is None or spine is None:
        return None, "no spine snapshot on the desk (run `spine --refresh`)"
    raw_lines, language = found
    if not raw_lines:
        return LineCheck(take_id=take_id), "a wordless take: no scripted lines"
    names = {
        str(card.get("cast_id")): str(card.get("name") or "")
        for card in spine[0].get("cast") or []
        if isinstance(card, Mapping)
    }
    lines = script_lines_for_take(
        raw_lines,
        facts,
        cast_names=names,
        off_screen_ids=sorted(heard_line_ids(spine[0])),
    )
    words_path, why = take_words(desk, episode, take_id, take)
    made = False
    if words_path is None:
        try:
            words_path = (transcriber or server_transcript)(desk, episode, take_id)
        except (
            ValueError,
            RuntimeError,
            OSError,
            KeyError,
            httpx.HTTPError,
            AudioServiceError,
            SystemExit,
        ) as exc:
            # No stored URL, no server, no token (SystemExit from the credentials): said, never a crash.
            return (
                None,
                f"no transcript of this take ({why}) and none could be made: {type(exc).__name__}: {exc}"[
                    :400
                ],
            )
        made = True
    try:
        words = load_words(words_path)
    except (OSError, ValueError) as exc:
        return None, f"the transcript `{words_path.name}` could not be read: {exc}"[
            :300
        ]
    check = check_take_lines(
        lines,
        words,
        take_id=take_id,
        language=language,
        muted=muted,
        patched=patched_line_ids(lines, voice_texts, language),
    )
    source = f"transcript `{words_path.name}`" + (
        " (made on the server now, $0, cached per take)" if made else ""
    )
    return check, source


def accepted(take_id: str, accept: Sequence[str]) -> bool:
    """Whether ``--accept-line-mismatch`` names this take (``t2``, or ``all``)."""

    return any(value.strip().lower() in (take_id.lower(), "all") for value in accept)


def stop_message(check: LineCheck, *, desk: str, episode: int, take_id: str) -> str:
    """The plain stop: the take, each fault, what was heard, and the re-film command."""

    rows = [
        f"ep{episode:02d} {take_id}: the take does not say its script. The video model writes the words "
        "itself on this show (model voices), and this take got them wrong:"
    ]
    rows += [f"  - {fault.describe()}" for fault in check.faults]
    cause = "; ".join(fault.describe() for fault in check.faults)[:180].replace(
        '"', "'"
    )
    rows.append(
        "Re-film this take (prices it first; nothing is spent before the human's yes to the number, "
        f"about $0.40 a take): fictora-produce film --desk {desk} --episode {episode} --take {take_id} "
        f'--cause "{cause}"  then the same with --confirm-spend.'
    )
    rows.append(
        "Do not patch a spoken line with a laid voice line: it breaks lip sync. Only a line heard off screen "
        "(no lips) may be laid with voice-line + finish --voice instead, and that is warned."
    )
    rows.append(
        f"To deliver it as it is anyway (logged in the run notes): finish again with {ACCEPT_FLAG} {take_id}."
    )
    return "\n".join(rows)


__all__ = [
    "ACCEPT_FLAG",
    "EXTRA_MIN_CHARS",
    "EXTRA_MIN_WORDS",
    "LINE_OK_RATIO",
    "SHORT_LINE_CHARS",
    "SHORT_LINE_WORDS",
    "SHOT_SLACK_SECONDS",
    "LineCheck",
    "LineFault",
    "ScriptLine",
    "accepted",
    "check_take_lines",
    "desk_line_check",
    "patched_line_ids",
    "normalise",
    "script_lines_for_take",
    "stop_message",
]
