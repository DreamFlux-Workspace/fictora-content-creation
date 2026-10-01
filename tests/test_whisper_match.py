"""Matching approved lines to Whisper words: English unchanged, Japanese kana pins against Whisper's kanji."""

from __future__ import annotations

import time

import pytest

from creation.post.whisper import LineWindow, Word, heard_reading, line_windows


def _words(*items: tuple[str, float, float]) -> tuple[Word, ...]:
    return tuple(Word(start, end, text) for text, start, end in items)


def test_english_lines_match_as_before() -> None:
    words = _words(("We're", 0.5, 0.7), ("closed.", 0.7, 1.1), ("um", 1.5, 1.6), ("Not", 2.0, 2.2),
                   ("for", 2.2, 2.3), ("me.", 2.3, 2.6))  # fmt: skip
    assert line_windows(
        words, ("We're closed.", "Not for me.", "Goodbye forever.")
    ) == (
        LineWindow(0, "We're closed.", 0.5, 1.1, 1.0),
        LineWindow(1, "Not for me.", 2.0, 2.6, 1.0),
        LineWindow(2, "Goodbye forever.", None, None, 0.0),
    )


def test_a_kana_pinned_line_is_found_in_whisper_kanji() -> None:
    words = _words(("東京に", 1.0, 1.6), ("行くよ", 1.6, 2.2))
    [window] = line_windows(words, ("とうきょうにいくよ",))
    assert (window.start, window.end) == (1.0, 2.2)


def test_a_kana_pin_with_long_kanji_readings_and_a_particle_is_found() -> None:
    words = _words(
        ("ここで", 0.2, 0.6),
        ("待ってて", 0.6, 1.2),
        ("今夜は", 3.0, 3.5),
        ("駄目", 3.5, 4.0),
    )
    windows = line_windows(words, ("ここでまってて", "こんやはだめ"))
    assert [(w.start, w.end) for w in windows] == [(0.2, 1.2), (3.0, 4.0)]


def test_hiragana_katakana_and_the_long_vowel_mark_compare_equal() -> None:
    words = _words(("ラーメン", 0.5, 1.0), ("食べたい", 1.0, 1.6))
    [window] = line_windows(words, ("らあめんたべたい",))
    assert (window.start, window.end) == (0.5, 1.6)


def test_a_kanji_line_is_found_when_whisper_writes_it_in_kana() -> None:
    words = _words(("こんや", 2.0, 2.4), ("は", 2.4, 2.5), ("ダメ", 2.5, 2.9))
    [window] = line_windows(words, ("今夜はだめ",))
    assert (window.start, window.end, window.ratio) == (2.0, 2.9, 1.0), (
        "starts on こんや, not on は"
    )


def test_a_line_spread_thin_over_other_speech_is_not_heard() -> None:
    words = _words(("そんなことないけど", 0.5, 1.5), ("だね", 1.5, 1.9))
    [window] = line_windows(words, ("そうだね",))
    assert window.start is None


def test_kanji_alone_never_counts_as_hearing_a_kana_line() -> None:
    words = _words(("大丈夫", 0.5, 1.0), ("本当", 1.0, 1.5))
    [window] = line_windows(words, ("ありがとう",))
    assert window.start is None


def test_a_different_japanese_line_is_not_heard() -> None:
    words = _words(("大丈夫です", 0.5, 1.2))
    [window] = line_windows(words, ("ありがとう",))
    assert window.start is None


def test_an_alternate_spelling_counts_as_heard() -> None:
    words = _words(("また", 0.4, 0.7), ("明日", 0.7, 1.1))
    lines = (
        "See you tomorrow.",
    )  # a performed line Whisper did not hear in this spelling
    assert line_windows(words, lines)[0].start is None
    [window] = line_windows(
        words, lines, alternates=(("See you tomorrow.", "またあした"),)
    )
    assert (window.line, window.start, window.end) == ("See you tomorrow.", 0.4, 1.1)


def test_an_unheard_japanese_line_over_a_long_take_stays_fast() -> None:
    words = tuple(
        Word(i * 0.2, i * 0.2 + 0.2, "今日は本当に良い天気") for i in range(40)
    )
    began = time.perf_counter()
    [window] = line_windows(
        words, ("ぜんぜんちがうせりふをここでいうけれどきこえない",)
    )
    assert window.start is None
    assert time.perf_counter() - began < 5.0


# --------------------------------------------------------------------------- #
# The server's per-word readings (``words[].reading`` on a Japanese transcript).
# --------------------------------------------------------------------------- #


def _read(*items: tuple[str, str, float, float]) -> tuple[Word, ...]:
    return tuple(Word(start, end, text, reading) for text, reading, start, end in items)


def test_a_kana_pin_heard_as_kanji_only_is_found_on_the_readings() -> None:
    # No kana in what Whisper wrote, so reading shape alone can never hear it.
    assert (
        line_windows(_words(("大丈夫", 0.4, 1.1)), ("だいじょうぶ",))[0].start is None
    )
    [window] = line_windows(
        _read(("大丈夫", "ダイジョウブ", 0.4, 1.1)), ("だいじょうぶ",)
    )
    assert (window.start, window.end, window.ratio) == (0.4, 1.1, 1.0)


def test_a_different_line_with_the_same_kana_skeleton_is_not_heard_on_the_readings() -> (
    None
):
    words = (("僕は", "ボクハ", 0.2, 0.6), ("行かない", "イカナイ", 0.6, 1.2))
    # Reading shape lets 僕 stand for わたし; the reading says ボク.
    assert (
        line_windows(
            _words(*((t, s, e) for t, _r, s, e in words)), ("わたしはいかない",)
        )[0].start
        == 0.2
    )
    assert line_windows(_read(*words), ("わたしはいかない",))[0].start is None


def test_a_kanji_line_and_later_lines_are_found_on_the_readings() -> None:
    words = _read(
        ("東京に", "トウキョウニ", 1.0, 1.6), ("行くよ", "イクヨ", 1.6, 2.2),
        ("らーめん", "らーめん", 3.0, 3.5), ("食べたい", "タベタイ", 3.5, 4.0),
    )  # fmt: skip
    windows = line_windows(words, ("東京に行くよ", "ラーメン、たべたい！"))
    assert [(w.start, w.end, w.ratio) for w in windows] == [
        (1.0, 2.2, 1.0),
        (3.0, 4.0, 1.0),
    ]


def test_a_kanji_only_line_and_english_still_match_on_whisper_text_when_readings_exist() -> (
    None
):
    words = _read(
        ("OK", "オーケー", 0.0, 0.3),
        ("今夜", "コンヤ", 0.5, 0.9),
        ("駄目", "ダメ", 0.9, 1.3),
    )
    windows = line_windows(words, ("OK", "今夜駄目"))
    assert [(w.start, w.end) for w in windows] == [(0.0, 0.3), (0.5, 1.3)]


def test_readings_load_from_the_api_transcript_and_older_transcripts_have_none(
    tmp_path,
) -> None:
    import json

    from creation.post.whisper import load_words

    new = tmp_path / "new.json"
    new.write_text(
        json.dumps(
            {
                "words": [
                    {"word": "笑って", "start": 0.1, "end": 0.6, "reading": "ワラッテ"}
                ]
            }
        )
    )
    old = tmp_path / "old.json"
    old.write_text(
        json.dumps({"words": [{"word": "笑って", "start": 0.1, "end": 0.6}]})
    )
    assert load_words(new) == (Word(0.1, 0.6, "笑って", "ワラッテ"),)
    assert load_words(old) == (Word(0.1, 0.6, "笑って"),)


def test_lines_stay_in_order_across_whisper_text_and_readings() -> None:
    words = _read(
        ("今夜", "コンヤ", 0.0, 0.4),
        ("ね", "ネ", 0.4, 0.5),
        ("今夜", "コンヤ", 2.0, 2.4),
    )
    # 今夜 matches Whisper's text; the kana line after it must not reuse that word's reading.
    windows = line_windows(words, ("今夜", "こんや"))
    assert [(w.start, w.end) for w in windows] == [(0.0, 0.4), (2.0, 2.4)]


# --------------------------------------------------------------------------- #
# Every plausible reading (``words[].readings``): a kanji read more than one way.
# --------------------------------------------------------------------------- #


def _alts(*items: tuple[str, tuple[str, ...], float, float]) -> tuple[Word, ...]:
    return tuple(
        Word(start, end, text, readings[0], readings)
        for text, readings, start, end in items
    )


#: Hanakaze ep 5 take 1 (``take-ep05-t1-review-words-v1.json``), with the readings the server now sends.
HANAKAZE_EP05 = _alts(
    ("食べ", ("タベ",), 0.17, 0.57), ("な", ("ナ",), 0.57, 0.75), ("さい", ("サイ",), 0.75, 1.11),
    ("よ", ("ヨ",), 1.11, 1.35), ("ー!", ("",), 1.35, 2.19), ("うん", ("ウン",), 5.57, 6.51),
    ("…", ("",), 6.51, 6.69), ("美味", ("ビミ", "ウマ"), 7.89, 8.17), ("い", ("イ",), 8.17, 8.49),
)  # fmt: skip


def test_hanakaze_ep05_umai_heard_as_bimi_plus_i_is_found() -> None:
    lines = ("食べなさいよ！", "……うまい。")
    # The words' own readings alone: 美味 + い read ビミ + イ and the line is missing.
    own = tuple(Word(w.start, w.end, w.text, w.reading) for w in HANAKAZE_EP05)
    assert line_windows(own, lines)[1].start is None
    first, second = line_windows(HANAKAZE_EP05, lines)
    assert (first.start, first.end) == (0.17, 1.35)
    assert (second.start, second.end, second.ratio, second.by) == (
        7.89,
        8.49,
        1.0,
        "sound",
    )


@pytest.mark.parametrize(
    ("heard", "line"),
    [
        ((("今日", ("キョウ", "コンニチ"), 0.2, 0.6), ("は", ("ハ",), 0.6, 0.8)), "こんにちは"),
        ((("上手", ("ジョウズ", "ウワテ", "カミテ"), 0.2, 0.6), ("だ", ("ダ",), 0.6, 0.7), ("ね", ("ネ",), 0.7, 0.8)),
         "うわてだね"),
        ((("明日", ("アス", "アシタ"), 0.2, 0.6), ("ね", ("ネ",), 0.6, 0.8)), "あしたね"),
    ],
)  # fmt: skip
def test_a_kanji_with_another_reading_meets_the_kana_line(
    heard: tuple[tuple[str, tuple[str, ...], float, float], ...], line: str
) -> None:
    words = _alts(*heard)
    own = tuple(Word(w.start, w.end, w.text, w.reading) for w in words)
    assert line_windows(own, (line,))[0].start is None
    [window] = line_windows(words, (line,))
    assert (window.start, window.end, window.by) == (0.2, 0.8, "sound")


def test_the_own_reading_is_kept_when_a_line_has_it() -> None:
    today = _alts(("今日", ("キョウ", "コンニチ"), 0.0, 0.4))[0]
    assert heard_reading(today, ("キョウハ",)) == "キョウ"
    assert heard_reading(today, ("サヨウナラ",)) == "キョウ"
    assert heard_reading(today, ("コンニチハ",)) == "コンニチ"
    # A different line is still not heard just because the word has many readings.
    assert (
        line_windows(
            _alts(("今日", ("キョウ", "コンニチ"), 0.0, 0.4)), ("さようなら",)
        )[0].start
        is None
    )


def test_readings_load_from_the_api_transcript(tmp_path) -> None:
    import json

    from creation.post.whisper import load_words

    path = tmp_path / "words.json"
    path.write_text(json.dumps({"words": [
        {"word": "美味", "start": 7.89, "end": 8.17, "reading": "ビミ", "readings": ["ビミ", "ウマ"]},
    ]}))  # fmt: skip
    assert load_words(path) == (Word(7.89, 8.17, "美味", "ビミ", ("ビミ", "ウマ")),)


def test_a_misspelt_name_still_hears_the_line() -> None:
    """L-20261001-16: "Hello, Wren." transcribed "Hello, Ren." is heard, not MISSING."""

    words = _words(("Hello,", 0.2, 0.5), ("Ren.", 0.5, 0.9))
    [window] = line_windows(words, ("Hello, Wren.",))
    assert (window.start, window.end) == (0.2, 0.9)


@pytest.mark.parametrize(
    ("line", "heard"),
    [("Thanks, Jon.", "John"), ("Go, Kat.", "Cat"), ("Hi, Maya.", "Mya")],
)
def test_a_name_heard_as_a_sound_alike_counts(line: str, heard: str) -> None:
    words = _words((line.split()[0], 0.0, 0.3), (heard, 0.3, 0.6))
    [window] = line_windows(words, (line,))
    assert window.start == 0.0, window


def test_a_lower_case_word_still_needs_its_own_spelling() -> None:
    """Sound-alike is for names: "cat" is not "bat"."""

    words = _words(("the", 0.0, 0.2), ("bat", 0.2, 0.5))
    [window] = line_windows(words, ("the cat",))
    assert window.start is None
