"""Matching approved lines to Whisper words: English unchanged, Japanese kana pins against Whisper's kanji."""

from __future__ import annotations

import time

from creation.post.whisper import LineWindow, Word, line_windows


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
