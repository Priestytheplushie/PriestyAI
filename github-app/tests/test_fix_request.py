from unittest.mock import AsyncMock, patch
import pytest
from app.workflows.fix_request import handle_fix_request


@pytest.mark.asyncio
async def test_handle_fix_request_in_scope_authorized():
    with patch(
        "app.workflows.fix_request.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.fix_request.machine_client"
    ) as mock_machine, patch(
        "app.workflows.fix_request.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json, patch(
        "app.workflows.fix_request.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_pull_request = AsyncMock(
            return_value={
                "head": {
                    "ref": "feature/auth",
                    "sha": "sha_head",
                    "repo": {"full_name": "octo/repo"},
                },
                "base": {"ref": "main", "repo": {"full_name": "octo/repo"}},
                "user": {"login": "alex"},
                "title": "feat: auth",
                "maintainer_can_modify": True,
            }
        )
        app_inst.get_user_permission = AsyncMock(return_value="write")
        app_inst.get_pull_request_files = AsyncMock(
            return_value=[{"filename": "app.py"}]
        )
        app_inst.get_repository_tree = AsyncMock(
            return_value=["app.py", "CONTRIBUTING.md"]
        )
        app_inst.get_file_content = AsyncMock(return_value="# Contributing")
        app_inst.get_pull_request_review_comments = AsyncMock(return_value=[])
        app_inst.get_referenced_context = AsyncMock(return_value="")
        app_inst.get_file_content_and_sha = AsyncMock(
            return_value={"content": "def run(): pass", "sha": "123"}
        )
        app_inst.get_branch_sha = AsyncMock(return_value="sha_head")
        app_inst.create_commit_on_branch = AsyncMock(return_value="new_sha_commit")

        mock_machine.get_pull_request_reviews = AsyncMock(return_value=[])
        mock_machine.request_reviewers = AsyncMock()
        mock_machine.create_issue_comment = AsyncMock()

        mock_llm_json.side_effect = [
            {
                "is_out_of_scope": False,
                "commit_message": "fix: update run function",
                "steps": [
                    {
                        "step_number": 1,
                        "target_file": "app.py",
                        "action": "MODIFY",
                        "action_summary": "add return",
                    }
                ],
            },
            {"has_automated_checks": False},
        ]
        mock_llm_gen.side_effect = [
            "def run(): return True",
            "I've pushed the requested fixes to the branch!",
        ]

        await handle_fix_request(
            installation_id=1,
            owner="octo",
            repo="repo",
            pull_number=10,
            requester_login="alex",
            user_prompt="fix the return value",
        )

        app_inst.create_commit_on_branch.assert_called_once()
        mock_machine.create_issue_comment.assert_called_once()


@pytest.mark.asyncio
async def test_handle_fix_request_out_of_scope():
    with patch(
        "app.workflows.fix_request.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.fix_request.machine_client"
    ) as mock_machine, patch(
        "app.workflows.fix_request.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json:

        app_inst = mock_app_cls.return_value
        app_inst.get_pull_request = AsyncMock(
            return_value={
                "head": {
                    "ref": "feature/auth",
                    "sha": "sha_head",
                    "repo": {"full_name": "octo/repo"},
                },
                "base": {"ref": "main", "repo": {"full_name": "octo/repo"}},
                "user": {"login": "alex"},
            }
        )
        app_inst.get_user_permission = AsyncMock(return_value="write")
        app_inst.get_pull_request_files = AsyncMock(return_value=[])
        app_inst.get_repository_tree = AsyncMock(return_value=[])
        app_inst.get_file_content = AsyncMock(return_value="")
        app_inst.get_pull_request_review_comments = AsyncMock(return_value=[])
        app_inst.get_referenced_context = AsyncMock(return_value="")
        mock_machine.get_pull_request_reviews = AsyncMock(return_value=[])
        mock_machine.create_issue_comment = AsyncMock()

        mock_llm_json.return_value = {
            "is_out_of_scope": True,
            "scope_explanation": "This is too large for this PR. Please create a new issue.",
        }

        await handle_fix_request(
            installation_id=1,
            owner="octo",
            repo="repo",
            pull_number=10,
            requester_login="alex",
            user_prompt="rewrite entire database layer",
        )

        mock_machine.create_issue_comment.assert_called_once_with(
            owner="octo",
            repo="repo",
            issue_number=10,
            body="This is too large for this PR. Please create a new issue.",
        )
