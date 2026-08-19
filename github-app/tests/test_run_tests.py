from unittest.mock import AsyncMock, patch
import pytest
from app.workflows.run_tests import handle_run_tests


@pytest.mark.asyncio
async def test_handle_run_tests_on_demand():
    with patch("app.workflows.run_tests.AppInstallationClient") as mock_app_cls, patch(
        "app.workflows.run_tests.machine_client"
    ) as mock_machine, patch(
        "app.workflows.run_tests.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json, patch(
        "app.workflows.run_tests.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_pull_request = AsyncMock(
            return_value={
                "head": {"sha": "sha123", "repo": {"full_name": "octo/repo"}},
                "base": {"repo": {"full_name": "octo/repo"}},
            }
        )
        app_inst.get_user_permission = AsyncMock(return_value="write")
        app_inst.get_repository_tree = AsyncMock(
            return_value=["pytest.ini", "tests/test_app.py"]
        )
        app_inst.create_check_run = AsyncMock()

        mock_llm_json.return_value = {
            "has_automated_checks": False,
            "docker_image": None,
            "commands": [],
        }
        mock_llm_gen.return_value = "Tests ran successfully."
        mock_machine.create_issue_comment = AsyncMock()

        await handle_run_tests(
            installation_id=1,
            owner="octo",
            repo="repo",
            pull_number=10,
            requester_login="dev",
            user_prompt="run tests",
        )

        app_inst.create_check_run.assert_called_once()
        mock_machine.create_issue_comment.assert_called_once()
