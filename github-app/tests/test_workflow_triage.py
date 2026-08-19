from unittest.mock import AsyncMock, patch
import pytest

from app.workflows.installation import handle_installation_event
from app.workflows.issue_triage import handle_issue_opened
from app.workflows.pr_triage import handle_pr_opened, handle_pr_summarize_request
from app.workflows.create_issue import handle_create_issue
from app.workflows.decompose_epic import handle_decompose_epic


@pytest.mark.asyncio
async def test_handle_installation_event_created():
    payload = {
        "action": "created",
        "installation": {"id": 12345},
        "repositories": [{"full_name": "octocat/Hello-World"}],
        "sender": {"login": "octocat"},
    }

    with patch("app.workflows.installation.AppInstallationClient") as mock_client:
        instance = mock_client.return_value
        instance.get_default_branch_sha = AsyncMock(
            return_value={"branch": "main", "sha": "abc1234"}
        )

        await handle_installation_event("installation", payload)
        instance.get_default_branch_sha.assert_called_once_with(
            "octocat", "Hello-World"
        )


@pytest.mark.asyncio
async def test_handle_issue_opened_triage():
    payload = {
        "action": "opened",
        "installation": {"id": 101},
        "repository": {"owner": {"login": "octocat"}, "name": "repo"},
        "issue": {
            "number": 5,
            "title": "Bug: Crash on startup",
            "body": "Null pointer on boot",
        },
        "sender": {"login": "developer"},
    }

    mock_triage_result = {
        "is_duplicate": False,
        "duplicate_issue_number": None,
        "needs_info": False,
        "followup_message": None,
        "selected_labels": ["bug"],
    }

    with patch(
        "app.workflows.issue_triage.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.issue_triage.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm:

        app_instance = mock_app_cls.return_value
        app_instance.get_repo_labels = AsyncMock(return_value=["bug", "feature"])
        app_instance._get_headers = AsyncMock(return_value={})
        app_instance.add_labels = AsyncMock()
        mock_llm.return_value = mock_triage_result

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json = lambda: []

            await handle_issue_opened(payload)
            app_instance.add_labels.assert_called_once_with(
                "octocat", "repo", 5, ["bug"]
            )


@pytest.mark.asyncio
async def test_handle_pr_opened_triage():
    payload = {
        "action": "opened",
        "installation": {"id": 101},
        "repository": {"owner": {"login": "octocat"}, "name": "repo"},
        "pull_request": {
            "number": 12,
            "title": "feat: add oauth",
            "user": {"login": "developer"},
            "body": "Implements google auth",
        },
        "sender": {"login": "developer"},
    }

    with patch("app.workflows.pr_triage.AppInstallationClient") as mock_app_cls, patch(
        "app.workflows.pr_triage.machine_client.create_issue_comment",
        new_callable=AsyncMock,
    ) as mock_comment, patch(
        "app.workflows.pr_triage.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm:

        app_instance = mock_app_cls.return_value
        app_instance.get_pull_request_files = AsyncMock(
            return_value=[
                {
                    "filename": "auth.py",
                    "status": "added",
                    "patch": "+ def login(): pass",
                }
            ]
        )
        app_instance.get_repo_labels = AsyncMock(return_value=["enhancement"])
        app_instance.add_labels = AsyncMock()
        mock_llm.return_value = {
            "summary": "This PR adds OAuth authentication.",
            "selected_labels": ["enhancement"],
        }

        await handle_pr_opened(payload)

        app_instance.add_labels.assert_called_once_with(
            "octocat", "repo", 12, ["enhancement"]
        )
        mock_comment.assert_called_once()


@pytest.mark.asyncio
async def test_handle_create_issue():
    with patch(
        "app.workflows.create_issue.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.create_issue.machine_client"
    ) as mock_machine, patch(
        "app.workflows.create_issue.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm:

        app_instance = mock_app_cls.return_value
        app_instance.get_repo_labels = AsyncMock(return_value=["enhancement", "bug"])
        app_instance.get_issue_comments = AsyncMock(return_value=[])

        mock_llm.return_value = {
            "title": "feat: add redis cache",
            "body": "Follow up task",
            "selected_labels": ["enhancement"],
            "reply_text": "Created issue #ISSUE_NUMBER",
        }

        mock_machine.create_issue = AsyncMock(return_value={"number": 77})
        mock_machine.create_issue_comment = AsyncMock()

        await handle_create_issue(
            installation_id=1,
            owner="octocat",
            repo="repo",
            current_issue_or_pr_number=10,
            requester_login="developer",
            user_prompt="Spin off an issue for Redis",
        )

        mock_machine.create_issue.assert_called_once()
        mock_machine.create_issue_comment.assert_called_once_with(
            "octocat", "repo", 10, "Created issue #77"
        )


@pytest.mark.asyncio
async def test_handle_decompose_epic():
    with patch(
        "app.workflows.decompose_epic.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.decompose_epic.machine_client"
    ) as mock_machine, patch(
        "app.workflows.decompose_epic.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm:

        app_instance = mock_app_cls.return_value
        app_instance.get_issue_details = AsyncMock(
            return_value={"title": "Epic: Rewrite backend", "body": "Scope"}
        )
        app_instance.get_repo_labels = AsyncMock(return_value=["backend"])
        app_instance.get_issue_comments = AsyncMock(return_value=[])
        app_instance.add_sub_issue = AsyncMock(return_value=True)

        mock_llm.return_value = {
            "intro": "Decomposed into:",
            "sub_issues": [
                {
                    "title": "Phase 1: DB",
                    "body": "Part of #5",
                    "selected_labels": ["backend"],
                },
                {
                    "title": "Phase 2: API",
                    "body": "Part of #5",
                    "selected_labels": ["backend"],
                },
            ],
            "outro": "Ready to start.",
        }

        mock_machine.create_issue = AsyncMock(
            side_effect=[{"number": 21, "id": 1001}, {"number": 22, "id": 1002}]
        )
        mock_machine.remove_assignees = AsyncMock()
        mock_machine.create_issue_comment = AsyncMock()

        await handle_decompose_epic(
            installation_id=1,
            owner="octocat",
            repo="repo",
            parent_issue_number=5,
            requester_login="maintainer",
            user_prompt="split this up",
        )

        assert mock_machine.create_issue.call_count == 2
        assert app_instance.add_sub_issue.call_count == 2
        mock_machine.create_issue_comment.assert_called_once()
