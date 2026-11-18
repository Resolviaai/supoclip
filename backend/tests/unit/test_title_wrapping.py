"""Burned-in titles stay inside the frame, in every script."""

from src.ai import sanitize_hook_title
from src.media.captions import _title_lines, _title_width, build_hook_title_ass, build_point_label_ass
from src.media.common import HOOK_TITLE_TOP_MARGIN_FRAC

CHINESE = "\u539f\u6765\u6211\u4eec\u575a\u6301\u7684\u77ac\u95f4\u771f\u7684\u80fd\u591f\u6210\u4e3a\u522b\u4eba\u5b9e\u4e60\u7684\u52c7\u6c14"


def _font_px(style_line):
    return int(style_line.split(",")[2])


def test_english_titles_wrap_as_before():
    assert _title_width("Heaven") == 6
    assert _title_lines("Heaven", 20) == ["Heaven"]
    assert _title_lines("A really very long compound word", 12) == ["A really very", "long compound word"]


def test_chinese_title_breaks_into_two_balanced_lines():
    first, second = _title_lines(CHINESE, 26)
    assert first + second == CHINESE
    assert abs(len(first) - len(second)) <= 1


def test_chinese_hook_title_fits_the_frame():
    style, events = build_hook_title_ass(CHINESE, {}, 1080, 1920, 6, "Arial", 62)
    assert "\\N" in events[0]
    # Each line is 11-12 characters at about one em each, inside the frame's width.
    assert _font_px(style) * 12 <= 1080


def test_short_chinese_title_stays_on_one_line():
    _, events = build_hook_title_ass(CHINESE[:6], {}, 1080, 1920, 6, "Arial", 62)
    assert "\\N" not in events[0]


def test_chinese_point_label_wraps():
    _, events = build_point_label_ass(
        [{"text": CHINESE, "start": 5.0}], {}, 1080, 1920, 20.0, "Arial", 62, hook_end=4.0
    )
    assert "\\N" in events[0]


def test_phrasing_aware_line_breaks_keep_phrases_intact():
    assert _title_lines("The Best AI Tools I Use Nobody Knows About", 24) == [
        "The Best AI Tools I Use",
        "Nobody Knows About",
    ]
    assert _title_lines("Uber Eats For Sunlight", 15) == [
        "Uber Eats",
        "For Sunlight",
    ]
    assert _title_lines("How I Saved $2,700 In My Paid Ad Spend", 24) == [
        "How I Saved $2,700",
        "In My Paid Ad Spend",
    ]


def test_sanitize_hook_title_enforces_clean_punctuation_and_specificity():
    assert sanitize_hook_title('"Why Sleep Matters!"') == "Why Sleep Matters!"
    assert (
        sanitize_hook_title("Best AI Tools I Use, But Nobody Knows")
        == "Best AI Tools I Use Nobody Knows"
    )
    assert (
        sanitize_hook_title("How I Saved $2,700 In 22 Days.")
        == "How I Saved $2,700 In 22 Days"
    )
    assert (
        sanitize_hook_title('Best AI Tools "Secret" I Use')
        == 'Best AI Tools "Secret" I Use'
    )
    assert (
        sanitize_hook_title("The AI System (Zero Budget)")
        == "The AI System (Zero Budget)"
    )
    assert sanitize_hook_title("Part 1: The bank calls") == "Part 1: The bank calls"


def test_build_hook_title_ass_highlights_quoted_power_words_and_upper_third_margin():
    template = {"font_color": "#FFFFFF", "emphasis_color": "#FFE000", "stroke_color": "#000000", "stroke_width": 3}
    style, events = build_hook_title_ass(
        'Best AI Tools "Secret" In 22 Days', template, 1080, 1920, 10.0, "Arial", 62
    )
    margin_v = int(style.split(",")[21])
    assert margin_v == int(1920 * HOOK_TITLE_TOP_MARGIN_FRAC)
    assert "{\\c&H0000E0FF&}\"Secret\"" in events[0]
    assert "{\\c&H0000E0FF&}22" in events[0]
