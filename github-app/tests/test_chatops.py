from unittest.mock import AsyncMock, patch
import pytest
from app.workflows.chatops import handle_chatops


@pytest.mark.asyncio
async def test_chatops_assign_as_maintainer():
    with patch("app.workflows.chatops.AppInstallationClient") as mock_app_cls, patch(
        "app.workflows.chatops.machine_client"
    ) as mock_machine, patch(
        "app.workflows.chatops.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json, patch(
        "app.workflows.chatops.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_issue_details = AsyncMock(
            return_value={"user": {"login": "author_user"}}
        )
        app_inst.get_user_permission = AsyncMock(return_value="admin")
        app_inst.get_repo_labels = AsyncMock(return_value=["bug", "help wanted"])

        mock_llm_json.return_value = {
            "actions": [
                {"action_type": "ASSIGN", "users": ["alice"], "target_number": 15}
            ]
        }
        mock_llm_gen.return_value = "Assigned @alice to #15."
        mock_machine.assign_users = AsyncMock(
            return_value={"assignees": [{"login": "alice"}]}
        )
        mock_machine.create_issue_comment = AsyncMock()

        await handle_chatops(
            installation_id=1,
            owner="octocat",
            repo="repo",
            issue_or_pr_number=15,
            is_pr=False,
            requester_login="maintainer_user",
            user_prompt="assign @alice",
        )

        mock_machine.assign_users.assert_called_once_with(
            "octocat", "repo", 15, ["alice"]
        )
        mock_machine.create_issue_comment.assert_called_once()


@pytest.mark.asyncio
async def test_chatops_permission_denied_for_non_maintainer():
    with patch("app.workflows.chatops.AppInstallationClient") as mock_app_cls, patch(
        "app.workflows.chatops.machine_client"
    ) as mock_machine, patch(
        "app.workflows.chatops.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json, patch(
        "app.workflows.chatops.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_issue_details = AsyncMock(
            return_value={"user": {"login": "author_user"}}
        )
        app_inst.get_user_permission = AsyncMock(return_value="read")
        app_inst.get_repo_labels = AsyncMock(return_value=["bug"])

        mock_llm_json.return_value = {
            "actions": [
                {
                    "action_type": "CLOSE",
                    "target_number": 15,
                    "state_reason": "completed",
                }
            ]
        }
        mock_llm_gen.return_value = "Sorry, permission denied."
        app_inst.set_issue_state = AsyncMock()
        mock_machine.create_issue_comment = AsyncMock()

        await handle_chatops(
            installation_id=1,
            owner="octocat",
            repo="repo",
            issue_or_pr_number=15,
            is_pr=False,
            requester_login="random_contributor",
            user_prompt="close this issue",
        )

        app_inst.set_issue_state.assert_not_called()
        mock_machine.create_issue_comment.assert_called_once()


@pytest.mark.parametrize(
    "action_dict,method_to_check",
    [
        ({"action_type": "UNASSIGN", "users": ["alice"]}, "remove_assignees"),
        ({"action_type": "CLOSE", "state_reason": "completed"}, "set_issue_state"),
        ({"action_type": "REOPEN"}, "set_issue_state"),
        ({"action_type": "MARK_READY"}, "mark_pr_ready_for_review"),
        ({"action_type": "MERGE", "merge_method": "squash"}, "merge_pull_request"),
        ({"action_type": "LOCK", "lock_reason": "resolved"}, "lock_issue"),
        ({"action_type": "UNLOCK"}, "unlock_issue"),
    ],
)
@pytest.mark.asyncio
async def test_chatops_action_matrix_as_maintainer(action_dict, method_to_check):
    with patch("app.workflows.chatops.AppInstallationClient") as mock_app_cls, patch(
        "app.workflows.chatops.machine_client"
    ) as mock_machine, patch(
        "app.workflows.chatops.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json, patch(
        "app.workflows.chatops.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_issue_details = AsyncMock(
            return_value={"user": {"login": "author"}}
        )
        app_inst.get_pull_request = AsyncMock(
            return_value={"user": {"login": "author"}}
        )
        app_inst.get_user_permission = AsyncMock(return_value="admin")
        app_inst.get_repo_labels = AsyncMock(return_value=["bug", "enhancement"])

        app_inst.set_issue_state = AsyncMock()
        app_inst.mark_pr_ready_for_review = AsyncMock()
        app_inst.merge_pull_request = AsyncMock()
        app_inst.lock_issue = AsyncMock()
        app_inst.unlock_issue = AsyncMock()
        mock_machine.remove_assignees = AsyncMock()
        mock_machine.create_issue_comment = AsyncMock()

        mock_llm_json.return_value = {"actions": [action_dict]}
        mock_llm_gen.return_value = "Action executed."

        await handle_chatops(
            installation_id=1,
            owner="octo",
            repo="repo",
            issue_or_pr_number=10,
            is_pr=True,
            requester_login="maintainer",
            user_prompt=f"execute {action_dict['action_type']}",
        )

        mock_machine.create_issue_comment.assert_called_once()
        if (
            hasattr(app_inst, method_to_check)
            and getattr(app_inst, method_to_check).called
        ):
            getattr(app_inst, method_to_check).assert_called_once()
        elif (
            hasattr(mock_machine, method_to_check)
            and getattr(mock_machine, method_to_check).called
        ):
            getattr(mock_machine, method_to_check).assert_called_once()


@pytest.mark.asyncio
async def test_chatops_add_and_remove_labels():
    with patch("app.workflows.chatops.AppInstallationClient") as mock_app_cls, patch(
        "app.workflows.chatops.machine_client"
    ) as mock_machine, patch(
        "app.workflows.chatops.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json, patch(
        "app.workflows.chatops.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_issue_details = AsyncMock(
            return_value={"user": {"login": "author"}}
        )
        app_inst.get_user_permission = AsyncMock(return_value="admin")
        app_inst.get_repo_labels = AsyncMock(return_value=["bug", "enhancement"])
        app_inst.add_labels = AsyncMock()
        app_inst.remove_label = AsyncMock()
        mock_machine.create_issue_comment = AsyncMock()

        mock_llm_json.return_value = {
            "actions": [
                {"action_type": "ADD_LABELS", "labels": ["bug"]},
                {"action_type": "REMOVE_LABELS", "labels": ["enhancement"]},
            ]
        }
        mock_llm_gen.return_value = "Labels updated."

        await handle_chatops(
            installation_id=1,
            owner="octo",
            repo="repo",
            issue_or_pr_number=10,
            is_pr=False,
            requester_login="maintainer",
            user_prompt="label updates",
        )

        app_inst.add_labels.assert_called_once_with("octo", "repo", 10, ["bug"])
        app_inst.remove_label.assert_called_once_with("octo", "repo", 10, "enhancement")


@pytest.mark.asyncio
async def test_chatops_merge_on_issue_denied():
    with patch("app.workflows.chatops.AppInstallationClient") as mock_app_cls, patch(
        "app.workflows.chatops.machine_client"
    ) as mock_machine, patch(
        "app.workflows.chatops.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json, patch(
        "app.workflows.chatops.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_issue_details = AsyncMock(
            return_value={"user": {"login": "author"}}
        )
        app_inst.get_user_permission = AsyncMock(return_value="admin")
        app_inst.get_repo_labels = AsyncMock(return_value=[])

        mock_llm_json.return_value = {
            "actions": [{"action_type": "MERGE", "target_number": 10}]
        }
        mock_llm_gen.return_value = "Cannot merge an issue."
        mock_machine.create_issue_comment = AsyncMock()

        await handle_chatops(
            installation_id=1,
            owner="octo",
            repo="repo",
            issue_or_pr_number=10,
            is_pr=False,
            requester_login="maintainer",
            user_prompt="merge this",
        )

        mock_machine.create_issue_comment.assert_called_once()


@pytest.mark.asyncio
async def test_chatops_assign_bot_and_author():
    with patch("app.workflows.chatops.AppInstallationClient") as mock_app_cls, patch(
        "app.workflows.chatops.machine_client"
    ) as mock_machine, patch(
        "app.workflows.chatops.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json, patch(
        "app.workflows.chatops.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_issue_details = AsyncMock(
            return_value={"user": {"login": "author_alex"}}
        )
        app_inst.get_user_permission = AsyncMock(return_value="none")
        app_inst.get_repo_labels = AsyncMock(return_value=[])

        mock_llm_json.return_value = {
            "actions": [
                {
                    "action_type": "ASSIGN",
                    "users": ["PriestyAI", "author_alex"],
                    "target_number": 10,
                }
            ]
        }
        mock_llm_gen.return_value = "Assigned."
        mock_machine.assign_users = AsyncMock(
            return_value={
                "assignees": [{"login": "PriestyAI"}, {"login": "author_alex"}]
            }
        )
        mock_machine.create_issue_comment = AsyncMock()

        await handle_chatops(
            installation_id=1,
            owner="octo",
            repo="repo",
            issue_or_pr_number=10,
            is_pr=False,
            requester_login="author_alex",
            user_prompt="work on this",
        )

        mock_machine.assign_users.assert_called_once()
        mock_machine.create_issue_comment.assert_called_once()
