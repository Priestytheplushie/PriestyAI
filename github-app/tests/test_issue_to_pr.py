from unittest.mock import AsyncMock, patch
import pytest

from app.workflows.issue_to_pr import handle_issue_assigned, execute_approved_plan


@pytest.mark.asyncio
async def test_handle_issue_assigned_standard_plan():
    payload = {
        "assignee": {"login": "PriestyAI"},
        "installation": {"id": 1},
        "issue": {
            "number": 42,
            "title": "feat: add user authentication",
            "body": "Implement JWT login",
        },
        "repository": {"owner": {"login": "octocat"}, "name": "repo"},
        "sender": {"login": "maintainer"},
    }

    with patch(
        "app.workflows.issue_to_pr.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.issue_to_pr.machine_client"
    ) as mock_machine, patch(
        "app.workflows.issue_to_pr.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json:

        app_inst = mock_app_cls.return_value
        app_inst.get_sub_issues = AsyncMock(return_value=[])
        app_inst.get_default_branch_sha = AsyncMock(
            return_value={"branch": "main", "sha": "base_sha_123"}
        )
        app_inst.get_repository_tree = AsyncMock(
            return_value=["README.md", "src/app.py", "package.json", "src/auth.py"]
        )
        app_inst.get_file_content = AsyncMock(return_value="print('app')")
        app_inst.get_issue_comments = AsyncMock(return_value=[])
        app_inst.get_referenced_context = AsyncMock(return_value="")
        app_inst.get_issue_linked_branch = AsyncMock(return_value=None)
        app_inst.get_branch_sha = AsyncMock(return_value="base_sha_123")
        app_inst.create_branch_with_empty_commit = AsyncMock(
            return_value="new_commit_sha"
        )

        mock_llm_json.side_effect = [
            {"selected_files": ["src/app.py"]},
            {
                "is_too_broad": False,
                "branch_name": "feature/user-auth",
                "pr_title": "feat(auth): implement JWT login",
                "pr_intro": "Plan to implement JWT auth",
                "pr_call_to_action": "Let me know if this looks good!",
                "issue_comment": "Opened draft PR #PR_NUMBER",
                "steps": [
                    {
                        "step_number": 1,
                        "title": "Create auth controller",
                        "difficulty": "standard",
                        "files": [
                            {
                                "path": "src/auth.py",
                                "action": "CREATE",
                                "summary": "JWT helper",
                            }
                        ],
                    }
                ],
            },
        ]

        mock_machine.create_pull_request = AsyncMock(return_value={"number": 101})
        mock_machine.assign_users = AsyncMock()
        mock_machine.create_issue_comment = AsyncMock()

        await handle_issue_assigned(payload)

        mock_machine.create_pull_request.assert_called_once()
        mock_machine.assign_users.assert_called_once()
        mock_machine.create_issue_comment.assert_called_once_with(
            "octocat", "repo", 42, "Opened draft PR #101"
        )


@pytest.mark.asyncio
async def test_execute_approved_plan():
    pr_body = (
        "### Plan\n\n- [ ] **1. Create auth controller** (`src/auth.py`)\n\n"
        '<!-- priesty-meta: {"issue_number": 42, "maintainer": "maintainer", '
        '"branch": "feature/user-auth", "base_sha": "base_sha_123", '
        '"steps": [{"step_number": 1, "title": "Create auth controller", "files": [{"path": "src/auth.py", "action": "CREATE", "summary": "JWT helper"}]}]} -->'
    )

    with patch(
        "app.workflows.issue_to_pr.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.issue_to_pr.machine_client"
    ) as mock_machine, patch(
        "app.workflows.issue_to_pr.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen, patch(
        "app.workflows.issue_to_pr.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json:

        app_inst = mock_app_cls.return_value
        app_inst.get_pull_request = AsyncMock(return_value={"body": pr_body})
        app_inst.get_file_content = AsyncMock(return_value="")
        app_inst.get_repository_tree = AsyncMock(return_value=["src/app.py"])
        app_inst.get_file_content_and_sha = AsyncMock(
            return_value={"content": None, "sha": None}
        )
        app_inst.create_commit_on_branch = AsyncMock(return_value="commit_sha_2")
        app_inst.update_pull_request = AsyncMock()
        app_inst.mark_pr_ready_for_review = AsyncMock(return_value=True)
        app_inst.get_branch_sha = AsyncMock(return_value="commit_sha_2")
        app_inst.create_check_run = AsyncMock()

        mock_llm_json.return_value = {"has_automated_checks": False}
        mock_llm_gen.side_effect = [
            "def auth(): return True",
            "All tasks complete and ready for review!",
        ]
        mock_machine.remove_assignees = AsyncMock()
        mock_machine.assign_users = AsyncMock()
        mock_machine.request_reviewers = AsyncMock()
        mock_machine.create_issue_comment = AsyncMock()

        await execute_approved_plan(
            installation_id=1,
            owner="octocat",
            repo="repo",
            pull_number=101,
        )

        app_inst.create_commit_on_branch.assert_called_once()
        app_inst.mark_pr_ready_for_review.assert_called_once_with(
            "octocat", "repo", 101
        )
        app_inst.create_check_run.assert_called_once()
        mock_machine.create_issue_comment.assert_called_once()
