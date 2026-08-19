from unittest.mock import AsyncMock, patch
import pytest
from app.workflows.pr_review import (
    handle_pr_review_requested,
    handle_pr_review_submitted,
)


@pytest.mark.asyncio
async def test_handle_pr_review_requested():
    payload = {
        "requested_reviewer": {"login": "PriestyAI"},
        "installation": {"id": 1},
        "pull_request": {
            "number": 50,
            "title": "feat: login route",
            "body": "Adds /login",
            "head": {"sha": "sha_pr_head"},
            "base": {"ref": "main"},
            "mergeable": True,
        },
        "repository": {"owner": {"login": "octo"}, "name": "repo"},
        "sender": {"login": "developer"},
    }

    with patch("app.workflows.pr_review.AppInstallationClient") as mock_app_cls, patch(
        "app.workflows.pr_review.machine_client"
    ) as mock_machine, patch(
        "app.workflows.pr_review.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json:

        app_inst = mock_app_cls.return_value
        app_inst.get_pull_request_files = AsyncMock(
            return_value=[
                {
                    "filename": "src/app.py",
                    "status": "modified",
                    "patch": "@@ -1,3 +1,4 @@",
                }
            ]
        )
        app_inst.get_repository_tree = AsyncMock(
            return_value=["src/app.py", "CONTRIBUTING.md"]
        )
        app_inst.get_file_content = AsyncMock(return_value="# Guidelines")

        mock_llm_json.side_effect = [
            {"has_automated_checks": False, "docker_image": None, "commands": []},
            {
                "verdict": "APPROVE",
                "summary": "Looks clean! Small optional suggestion.",
                "comments": [
                    {
                        "path": "src/app.py",
                        "line": 15,
                        "body": "Consider using set:\n```suggestion\n  seen = set()\n```",
                    }
                ],
            },
        ]
        mock_machine.post_pull_request_review = AsyncMock()

        await handle_pr_review_requested(payload)

        mock_machine.post_pull_request_review.assert_called_once()
        call_kwargs = mock_machine.post_pull_request_review.call_args.kwargs
        assert call_kwargs["event"] == "APPROVE"
        assert len(call_kwargs["comments"]) == 1


@pytest.mark.asyncio
async def test_handle_pr_review_submitted_approval():
    payload = {
        "action": "submitted",
        "review": {
            "state": "approved",
            "user": {"login": "reviewer_alex"},
            "body": "LGTM!",
        },
        "pull_request": {"number": 50, "title": "feat: login route"},
        "repository": {"owner": {"login": "octo"}, "name": "repo"},
        "installation": {"id": 1},
    }

    with patch("app.workflows.pr_review.AppInstallationClient") as mock_app_cls, patch(
        "app.workflows.pr_review.machine_client"
    ) as mock_machine, patch(
        "app.workflows.pr_review.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_pull_request_commits = AsyncMock(
            return_value=[{"commit": {"message": "feat: initial"}}]
        )
        mock_llm_gen.return_value = "Thanks for the review @reviewer_alex! You can squash and merge whenever ready."
        mock_machine.create_issue_comment = AsyncMock()

        await handle_pr_review_submitted(payload)

        mock_machine.create_issue_comment.assert_called_once()
