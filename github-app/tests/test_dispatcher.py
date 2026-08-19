from unittest.mock import AsyncMock, patch
import pytest
from app.main import dispatch_webhook_event


@pytest.mark.asyncio
async def test_dispatch_installation_event():
    with patch(
        "app.main.handle_installation_event", new_callable=AsyncMock
    ) as mock_handler:
        payload = {"action": "created", "installation": {"id": 123}}
        await dispatch_webhook_event("installation", {}, payload)
        mock_handler.assert_called_once_with("installation", payload)


@pytest.mark.asyncio
async def test_dispatch_issue_opened():
    with patch("app.main.handle_issue_opened", new_callable=AsyncMock) as mock_handler:
        payload = {
            "action": "opened",
            "issue": {"number": 42},
            "repository": {"owner": {"login": "octocat"}, "name": "repo"},
        }
        await dispatch_webhook_event("issues", {}, payload)
        mock_handler.assert_called_once_with(payload)


@pytest.mark.asyncio
async def test_dispatch_pr_opened():
    with patch("app.main.handle_pr_opened", new_callable=AsyncMock) as mock_handler:
        payload = {
            "action": "opened",
            "pull_request": {"number": 10},
            "repository": {"owner": {"login": "octocat"}, "name": "repo"},
        }
        await dispatch_webhook_event("pull_request", {}, payload)
        mock_handler.assert_called_once_with(payload)


@pytest.mark.asyncio
async def test_dispatch_comment_created():
    with patch(
        "app.main.handle_comment_created", new_callable=AsyncMock
    ) as mock_handler:
        payload = {
            "action": "created",
            "comment": {"body": "hello"},
            "issue": {"number": 10},
            "repository": {"owner": {"login": "octocat"}, "name": "repo"},
        }
        await dispatch_webhook_event("issue_comment", {}, payload)
        mock_handler.assert_called_once_with(payload)
