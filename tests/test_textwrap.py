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


# -- wrap_rows: offset-tracking layout for the overlay notepad editor --------


def test_wrap_rows_partitions_text_contiguously():
    from shared.textwrap import wrap_rows

    text = "hello world, this is a somewhat longer line of text"
    rows = wrap_rows(text, 12)
    assert all(ln <= 12 for _, ln in rows)
    assert rows[0][0] == 0
    # Rows advance strictly; any gap between consecutive rows must be made
    # up solely of dropped spaces (within a line) or the newline itself.
    prev_end = 0
    rebuilt = []
    for st, ln in rows:
        assert st >= prev_end
        gap = text[prev_end:st]
        assert all(ch in (" ", "\n") for ch in gap)
        rebuilt.append(gap + text[st:st + ln])
        prev_end = st + ln
    assert "".join(rebuilt) == text
    tail_gap = text[prev_end:]
    assert all(ch in (" ", "\n") for ch in tail_gap)


def test_wrap_rows_trailing_newline_yields_blank_last_row():
    from shared.textwrap import wrap_rows

    assert wrap_rows("", 10) == [(0, 0)]
    assert wrap_rows("ab\n", 10) == [(0, 2), (3, 0)]
    assert wrap_rows("ab\ncd", 10) == [(0, 2), (3, 2)]


def test_wrap_rows_hard_breaks_long_words_and_drops_boundary_spaces():
    from shared.textwrap import wrap_rows

    text = "abcdefg"
    rows = wrap_rows(text, 4)
    assert [text[st:st + ln] for st, ln in rows] == ["abcd", "efg"]

    # The row may keep a leading space when several surround the boundary;
    # only the boundary-crossing space itself is dropped from the layout.
    text = "a  b"
    rows = wrap_rows(text, 3)
    assert [text[st:st + ln].strip() for st, ln in rows] == ["a", "b"]
    assert rows[1][0] == 3


def test_caret_semantics_over_layout():
    """The editor's caret rules: offset lands inside the first row that can
    hold it (end-of-row preferred over next-row start)."""
    from shared.textwrap import wrap_rows

    def cell(rows, off):
        for i, (st, ln) in enumerate(rows):
            if off <= st + ln:
                return i, off - st
        st, ln = rows[-1]
        return len(rows) - 1, min(off - st, ln)

    rows = wrap_rows("abcdefg", 4)      # ["abcd", "efg"]
    assert cell(rows, 0) == (0, 0)
    assert cell(rows, 4) == (0, 4)      # boundary shows at end of row 0
    assert cell(rows, 5) == (1, 1)
    assert cell(rows, 7) == (1, 3)
    assert cell(wrap_rows("ab\n", 10), 3) == (1, 0)   # on the blank last row
