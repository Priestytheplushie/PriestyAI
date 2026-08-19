import pytest
from app.core.patcher import apply_search_replace


def test_apply_search_replace_exact_single_block():
    original = "def add(a, b):\n" "    return a - b\n"
    patch = (
        "<<<<<<< SEARCH\n"
        "def add(a, b):\n"
        "    return a - b\n"
        "=======\n"
        "def add(a, b):\n"
        "    return a + b\n"
        ">>>>>>> REPLACE"
    )
    expected = "def add(a, b):\n" "    return a + b\n"
    result = apply_search_replace(original, patch)
    assert result == expected


def test_apply_search_replace_multiple_blocks():
    original = (
        "import os\n"
        "import sys\n\n"
        "def foo():\n"
        "    return 1\n\n"
        "def bar():\n"
        "    return 2\n"
    )
    patch = (
        "<<<<<<< SEARCH\n"
        "def foo():\n"
        "    return 1\n"
        "=======\n"
        "def foo():\n"
        "    return 10\n"
        ">>>>>>> REPLACE\n\n"
        "<<<<<<< SEARCH\n"
        "def bar():\n"
        "    return 2\n"
        "=======\n"
        "def bar():\n"
        "    return 20\n"
        ">>>>>>> REPLACE"
    )
    expected = (
        "import os\n"
        "import sys\n\n"
        "def foo():\n"
        "    return 10\n\n"
        "def bar():\n"
        "    return 20\n"
    )
    result = apply_search_replace(original, patch)
    assert result == expected


def test_apply_search_replace_trailing_whitespace_tolerance():

    original = "def compute():  \n    val = 42   \n    return val\n"
    patch = (
        "<<<<<<< SEARCH\n"
        "def compute():\n"
        "    val = 42\n"
        "=======\n"
        "def compute():\n"
        "    val = 100\n"
        ">>>>>>> REPLACE"
    )
    result = apply_search_replace(original, patch)
    assert "val = 100" in result
    assert "return val" in result


def test_apply_search_replace_no_match_keeps_original():
    original = "def greet():\n    return 'hello'\n"
    patch = (
        "<<<<<<< SEARCH\n"
        "def missing_func():\n"
        "    return False\n"
        "=======\n"
        "def missing_func():\n"
        "    return True\n"
        ">>>>>>> REPLACE"
    )
    result = apply_search_replace(original, patch)
    assert result == original


def test_apply_search_replace_without_markers_returns_patch():

    original = "old text"
    patch = "def new_full_file():\n    pass\n"
    result = apply_search_replace(original, patch)
    assert result == patch
