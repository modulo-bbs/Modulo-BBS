"""B1 — shared text wrapping (boards-unification §B1).

``wrap`` is pure len() math over decoded str: CP437 glyphs are one char
each once decoded, so no encoding assumptions belong here. Greedy fill,
hard-break of over-long words, explicit newlines preserved as line breaks.
"""
from __future__ import annotations

from shared.textwrap import wrap


def test_simple_greedy_fill():
    assert wrap("the quick brown fox", 10) == ["the quick", "brown fox"]


def test_text_fitting_one_line_untouched():
    assert wrap("hello", 10) == ["hello"]
    assert wrap("exactly-ten", 11) == ["exactly-ten"]


def test_long_word_hard_broken():
    assert wrap("aaaaaaaaaaaaaa", 5) == ["aaaaa", "aaaaa", "aaaa"]


def test_mixed_words_and_long_word():
    # Matches stdlib textwrap semantics: the final hard-break remainder
    # keeps participating in greedy fill ("d end", not an orphan "d").
    assert wrap("short averyveryverylongword end", 10) == [
        "short",
        "averyveryv",
        "erylongwor",
        "d end",
    ]


def test_explicit_newlines_preserved():
    assert wrap("a\n\nb", 10) == ["a", "", "b"]


def test_empty_string_is_one_blank_line():
    assert wrap("", 10) == [""]


def test_collapse_repeated_spaces_but_keep_content():
    # Greedy filler never emits trailing/leading spaces on a line.
    out = wrap("a  lot   of   spaces here", 8)
    for line in out:
        assert line == line.strip()
        assert len(line) <= 8


def test_cp437_single_width_safety():
    text = "█▓▒░ █▓▒░ █▓▒░"
    out = wrap(text, 7)
    assert all(len(line) <= 7 for line in out)
    assert wrap("██████", 3) == ["███", "███"]


def test_zero_and_negative_width_clamps_to_one():
    assert wrap("abc", 0) == ["a", "b", "c"]
    assert wrap("abc", -5) == ["a", "b", "c"]
