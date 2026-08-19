import pytest
from app.workflows.issue_to_pr import (
    clean_branch_slug,
    sanitize_branch_name,
    strip_markdown_fences,
)


@pytest.mark.parametrize(
    "issue_title,expected_slug",
    [
        ("feat: Add Redis Cache Backend", "add-redis-cache-backend"),
        (
            "fix(api): #123 Broken token refresh handler!",
            "broken-token-refresh-handler",
        ),
        ("docs: update README with setup guide", "update-readme-with-setup-guide"),
        ("bug: critical crash on startup", "critical-crash-on-startup"),
        ("Simple Title", "simple-title"),
    ],
)
def test_clean_branch_slug(issue_title, expected_slug):
    result = clean_branch_slug(issue_title)
    assert result == expected_slug


@pytest.mark.parametrize(
    "candidate,issue_title,expected_branch",
    [
        (
            None,
            "Fix connection timeout in DB pool",
            "fix/connection-timeout-in-db-pool",
        ),
        (None, "Add dark mode toggle", "feature/add-dark-mode-toggle"),
        (None, "Docs: Update contributing guide", "docs/update-contributing-guide"),
        (None, "Refactor middleware pipeline", "refactor/middleware-pipeline"),
        ("feat/user-auth", "Irrelevant Title", "feature/user-auth"),
        ("doc/api-reference", "Irrelevant Title", "docs/api-reference"),
        ("custom-branch-name", "Add User API", "feature/custom-branch-name"),
    ],
)
def test_sanitize_branch_name(candidate, issue_title, expected_branch):
    result = sanitize_branch_name(candidate, issue_title)
    assert result == expected_branch


def test_strip_markdown_fences_with_language():
    code_block = "```python\ndef hello():\n    return 'world'\n```"
    expected = "def hello():\n    return 'world'\n"
    assert strip_markdown_fences(code_block) == expected


def test_strip_markdown_fences_without_language():
    code_block = "```\nsome raw text\nline 2\n```"
    expected = "some raw text\nline 2\n"
    assert strip_markdown_fences(code_block) == expected


def test_strip_markdown_fences_already_plain_text():
    plain_text = "const x = 10;\nconsole.log(x);\n"
    assert strip_markdown_fences(plain_text) == plain_text
