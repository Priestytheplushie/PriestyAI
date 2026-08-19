from unittest.mock import AsyncMock, patch
import pytest
from app.workflows.resolve_conflicts import handle_resolve_conflicts, get_lang_tag


def test_get_lang_tag():
    assert get_lang_tag("main.py") == "python"
    assert get_lang_tag("app.ts") == "typescript"
    assert get_lang_tag("style.css") == "css"
    assert get_lang_tag("unknown.xyz") == ""


@pytest.mark.asyncio
async def test_handle_resolve_conflicts():
    with patch(
        "app.workflows.resolve_conflicts.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.resolve_conflicts.machine_client"
    ) as mock_machine, patch(
        "app.workflows.resolve_conflicts.llm_client.generate_json",
        new_callable=AsyncMock,
    ) as mock_llm_json, patch(
        "app.workflows.resolve_conflicts.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_pull_request = AsyncMock(
            return_value={
                "head": {
                    "ref": "feature",
                    "sha": "sha_head",
                    "repo": {"full_name": "octo/repo"},
                },
                "base": {"ref": "main", "repo": {"full_name": "octo/repo"}},
                "user": {"login": "dev"},
                "maintainer_can_modify": True,
            }
        )
        app_inst.get_branch_sha = AsyncMock(return_value="sha_base")
        app_inst.get_user_permission = AsyncMock(return_value="admin")
        app_inst.get_pull_request_files = AsyncMock(
            return_value=[{"filename": "app.py"}]
        )
        app_inst.get_repository_tree = AsyncMock(return_value=["app.py"])
        app_inst.get_file_content = AsyncMock(return_value="def main(): pass")
        app_inst.get_file_content_and_sha = AsyncMock(
            return_value={"content": "def main(): print('conflict')"}
        )
        app_inst.create_merge_commit = AsyncMock(return_value="merge_sha_123")

        mock_llm_json.return_value = {"has_automated_checks": False}
        mock_llm_gen.side_effect = [
            "def main(): print('resolved')",
            "Resolved merge conflicts!",
        ]
        mock_machine.create_issue_comment = AsyncMock()

        await handle_resolve_conflicts(
            installation_id=1,
            owner="octo",
            repo="repo",
            pull_number=10,
            requester_login="dev",
            user_prompt="resolve conflicts",
        )

        app_inst.create_merge_commit.assert_called_once()
        mock_machine.create_issue_comment.assert_called_once()
