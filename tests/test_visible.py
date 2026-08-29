"""Display-column helpers for UTF-8 Ambiguous box drawing."""
from shared.visible import (
    at_display,
    center_display,
    display_width,
    fill_display,
    fit_display,
    hline,
    overlay_display,
    sanitize_cell,
    slice_display,
    strip_ansi,
)


def test_sanitize_cell_drops_bare_esc_keeps_colour():
    """A bare ESC in a cell makes the terminal eat the rest of the row."""
    assert sanitize_cell("\x1b") == ""
    assert sanitize_cell("a\x1bb") == "ab"
    assert sanitize_cell("\x1b[33mok\x1b[0m") == "\x1b[33mok\x1b[0m"
    assert sanitize_cell("tab\there") == "tabhere"


def test_esc_title_cannot_shift_the_frame():
    """Regression: a board titled ESC shifted its row ~22 columns left."""
    inner = 20
    cell = f" {sanitize_cell('\x1b'):<{inner}} "
    row = "│" + cell + "│" + " " * 54 + "│"
    assert "\x1b" not in row
    assert display_width(row) == 79
    # divider stays where every other row puts it: │ + 22-col cell
    assert row.index("│", 1) == 23
    assert row.rindex("│") == 78


def test_strip_ansi_keeps_cp437_arrows_drops_csi():
    assert strip_ansi("a\x1b[33mbc") == "abc"
    arrows = " \x18\x19\x1B\x1A · WASD select "
    assert strip_ansi(arrows) == arrows
    assert display_width(arrows, wide_ambiguous=False) == len(arrows)


def test_fill_and_hline_utf8_are_79_display():
    rule = fill_display("─", 79, wide_ambiguous=True)
    assert display_width(rule, wide_ambiguous=True) == 79
    bot = hline("└", "─", "┘", 79, wide_ambiguous=True)
    assert display_width(bot, wide_ambiguous=True) == 79
    assert bot[0] == "└" and bot[-1] == "┘"


def test_overlay_hint_keeps_79():
    base = fill_display("─", 79, wide_ambiguous=True)
    hint = center_display(" ↑↓←→ · WASD select ", 22, wide_ambiguous=True)
    row = overlay_display(base, 16, hint, 79, wide_ambiguous=True)
    assert display_width(row, wide_ambiguous=True) == 79
    assert "select" in row
    inner = slice_display(row, 16, 16 + display_width(hint, wide_ambiguous=True),
                          wide_ambiguous=True)
    assert "select" in inner


def test_at_display_finds_wide_bar():
    # │ is 2 cols when Ambiguous is wide; occupies 0 and 1.
    s = "│" + " " * 22 + "│"
    assert at_display(s, 0, wide_ambiguous=True) == "│"
    assert at_display(s, 1, wide_ambiguous=True) == "│"
    assert at_display(s, 2, wide_ambiguous=True) == " "
    assert at_display(s, 24, wide_ambiguous=True) == "│"


def test_fit_clips_overflow_dashes():
    s = "─" * 20
    out = fit_display(s, 22, wide_ambiguous=True)
    assert display_width(out, wide_ambiguous=True) == 22
    assert out.count("─") == 11  # 11*2=22


def test_pad_line_does_not_inflate_two_cell_dash_row():
    from core.app import _pad_line

    row = "─" * 40  # 80 display when Ambiguous is wide
    out = _pad_line(row + "\r\n", 80, wide_ambiguous=True)
    body = out.split("\r\n")[0]
    assert display_width(body, wide_ambiguous=True) == 79
    assert "\x1b[K" in body


def test_pad_line_space_fills_short_ascii_row():
    from core.app import _pad_line

    out = _pad_line("hi\r\n", 80, wide_ambiguous=False)
    body = out.split("\r\n")[0]
    assert body.startswith("hi")
    assert "\x1b[K" in body
    assert display_width(body, wide_ambiguous=False) == 79
