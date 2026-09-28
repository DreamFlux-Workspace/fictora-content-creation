"""Matching approved lines to Whisper words: English unchanged, Japanese kana pins against Whisper's kanji."""

from __future__ import annotations

import time

from creation.post.whisper import LineWindow, Word, line_windows


def _words(*items: tuple[str, float, float]) -> tuple[Word, ...]:
    return tuple(Word(start, end, text) for text, start, end in items)


def test_english_lines_match_as_before() -> None:
    words = _words(("We're", 0.5, 0.7), ("closed.", 0.7, 1.1), ("um", 1.5, 1.6), ("Not", 2.0, 2.2),
                   ("for", 2.2, 2.3), ("me.", 2.3, 2.6))  # fmt: skip
    assert line_windows(words, ("We're closed.", "Not for me.", "Goodbye forever.")) == (
        LineWindow(0, "We're closed.", 0.5, 1.1, 1.0),
        LineWindow(1, "Not for me.", 2.0, 2.6, 1.0),
        LineWindow(2, "Goodbye forever.", None, None, 0.0),
    )


def test_a_kana_pinned_line_is_found_in_whisper_kanji() -> None:
    words = _words(("東京に", 1.0, 1.6), ("行くよ", 1.6, 2.2))
    [window] = line_windows(words, ("とうきょうにいくよ",))
    assert (window.start, window.end) == (1.0, 2.2)


def test_a_kana_pin_with_long_kanji_readings_and_a_particle_is_found() -> None:
    words = _words(("ここで", 0.2, 0.6), ("待ってて", 0.6, 1.2), ("今夜は", 3.0, 3.5), ("駄目", 3.5, 4.0))
    windows = line_windows(words, ("ここでまってて", "こんやはだめ"))
    assert [(w.start, w.end) for w in windows] == [(0.2, 1.2), (3.0, 4.0)]


def test_hiragana_katakana_and_the_long_vowel_mark_compare_equal() -> None:
    words = _words(("ラーメン", 0.5, 1.0), ("食べたい", 1.0, 1.6))
    [window] = line_windows(words, ("らあめんたべたい",))
    assert (window.start, window.end) == (0.5, 1.6)


def test_a_kanji_line_is_found_when_whisper_writes_it_in_kana() -> None:
    words = _words(("こんや", 2.0, 2.4), ("は", 2.4, 2.5), ("ダメ", 2.5, 2.9))
    [window] = line_windows(words, ("今夜はだめ",))
    assert (window.start, window.end, window.ratio) == (2.0, 2.9, 1.0), "starts on こんや, not on は"


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
    lines = ("See you tomorrow.",)  # a performed line Whisper did not hear in this spelling
    assert line_windows(words, lines)[0].start is None
    [window] = line_windows(words, lines, alternates=(("See you tomorrow.", "またあした"),))
    assert (window.line, window.start, window.end) == ("See you tomorrow.", 0.4, 1.1)


def test_an_unheard_japanese_line_over_a_long_take_stays_fast() -> None:
    words = tuple(Word(i * 0.2, i * 0.2 + 0.2, "今日は本当に良い天気") for i in range(40))
    began = time.perf_counter()
    [window] = line_windows(words, ("ぜんぜんちがうせりふをここでいうけれどきこえない",))
    assert window.start is None
    assert time.perf_counter() - began < 5.0
