import sys
from pathlib import Path
import pytest
from rich.text import Text

from cli import (
    colorize_log,
    find_python_executable,
    get_process_stats,
    matches_filter_level,
)


@pytest.mark.parametrize(
    "line",
    [
        "20:41:15 [INFO] Server started on port 8000",
        "20:41:15 [WARN] Rate limit approaching",
        "20:41:15 [ERROR] Unhandled KeyError in triage worker",
        "Any random string",
    ],
)
def test_matches_filter_level_all(line):

    assert matches_filter_level(line, "ALL") is True


@pytest.mark.parametrize(
    "line,expected",
    [
        ("20:41:15 [ERROR] Failed to clone repo", True),
        ("Traceback (most recent call last):", True),
        ("CRITICAL: Container failed with status=500", True),
        ("Application crash reported", True),
        ("20:41:15 [INFO] Normal application heartbeat", False),
        ("20:41:15 [WARN] 429 cooldown active", False),
    ],
)
def test_matches_filter_level_error(line, expected):
    assert matches_filter_level(line, "ERROR") is expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("20:41:15 [ERROR] Failed to clone repo", True),
        ("20:41:15 [WARN] Rate limit reached", True),
        ("20:41:15 [WARNING] Request rejected due to cooldown", True),
        ("HTTP 429 Too Many Requests", True),
        ("20:41:15 [INFO] Successfully synced shard 0", False),
        ("20:41:15 [DEBUG] Reading cache file", False),
    ],
)
def test_matches_filter_level_warn_plus(line, expected):
    assert matches_filter_level(line, "WARN+") is expected


def test_colorize_log_returns_rich_text():
    line = "20:41:15 [INFO] POST /webhook 200 OK"
    result = colorize_log(line, service="github")
    assert isinstance(result, Text)
    assert result.plain == line

    assert len(result.spans) > 0


def test_colorize_log_github_tokens():
    line = "20:41:15 [priesty.issue_to_pr] #42 opened on branch feature/cache-ttl [python:3.11] PASSED"
    result = colorize_log(line, service="github")
    assert isinstance(result, Text)
    assert len(result.spans) >= 4


def test_colorize_log_discord_tokens():
    line = "20:41:16 [chat] [Server News] Video rendering complete with Edge-TTS"
    result = colorize_log(line, service="discord")
    assert isinstance(result, Text)
    assert len(result.spans) >= 2


def test_colorize_log_with_search_highlight():
    line = "20:41:15 [INFO] POST /webhook 200 OK"
    result = colorize_log(line, service="github", highlight_query="webhook")
    assert isinstance(result, Text)
    assert len(result.spans) > 0


def test_get_process_stats_none_pid():
    cpu, mem = get_process_stats(None)
    assert cpu == 0.0
    assert mem == 0.0


def test_find_python_executable():

    executable = find_python_executable(Path(__file__).parent)
    assert isinstance(executable, str)
    assert len(executable) > 0
