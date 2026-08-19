import pytest
from tools.news.utils import clean_unicode_text, clean_display_name


@pytest.mark.parametrize(
    "raw_input, expected",
    [
        ("“Hello World”—it’s working", '"Hello World"-it\'s working'),
        ("Emoji test 🙂🔥🚀", "Emoji test"),
        ("Clean   spaces   and   lines", "Clean spaces and lines"),
        ("", ""),
        (None, ""),
    ],
)
def test_clean_unicode_text(raw_input, expected):
    assert clean_unicode_text(raw_input) == expected


@pytest.mark.parametrize(
    "raw_name, expected",
    [
        ("[CLAN] Alex", "Alex"),
        ("(DEV) Jordan_99", "Jordan_99"),
        ("{MOD} Sam", "Sam"),
        ("VIP | Taylor", "Taylor"),
        ("PRO • Morgan", "Morgan"),
        ("PRO ツ CoolGamer", "CoolGamer"),
        ("", "User"),
        (None, "User"),
    ],
)
def test_clean_display_name(raw_name, expected):
    assert clean_display_name(raw_name) == expected
