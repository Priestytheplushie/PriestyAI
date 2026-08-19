from unittest.mock import AsyncMock, patch
import pytest
from app.workflows.mention_router import handle_comment_created, spam_guard


@pytest.mark.asyncio
async def test_mention_router_plan_approved():
    payload = {
        "action": "created",
        "installation": {"id": 1},
        "comment": {"body": "@PriestyAI LGTM proceed!"},
        "issue": {"number": 10, "title": "feat: cache"},
        "pull_request": {"url": "https://api.github.com/repos/octo/repo/pulls/10"},
        "repository": {"owner": {"login": "octo"}, "name": "repo"},
        "sender": {"login": "maintainer"},
    }

    with patch(
        "app.workflows.mention_router.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.mention_router.machine_client"
    ) as mock_machine, patch(
        "app.workflows.mention_router.execute_approved_plan", new_callable=AsyncMock
    ) as mock_exec, patch(
        "app.workflows.mention_router.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json, patch(
        "app.workflows.mention_router.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_pull_request = AsyncMock(
            return_value={"draft": True, "body": '<!-- priesty-meta: {"steps": []} -->'}
        )
        app_inst.get_issue_comments = AsyncMock(return_value=[])
        app_inst.get_referenced_context = AsyncMock(return_value="")

        mock_llm_json.return_value = {
            "intent": "PLAN_APPROVED",
            "reason": "LGTM received",
        }
        mock_llm_gen.return_value = "Starting work now!"
        mock_machine.create_issue_comment = AsyncMock()

        await handle_comment_created(payload)

        mock_machine.create_issue_comment.assert_called_once()
        mock_exec.assert_called_once_with(
            installation_id=1, owner="octo", repo="repo", pull_number=10
        )


@pytest.mark.asyncio
async def test_mention_router_start_issue():
    payload = {
        "action": "created",
        "installation": {"id": 1},
        "comment": {"body": "@PriestyAI work on this"},
        "issue": {"number": 20, "title": "fix: connection pool"},
        "repository": {"owner": {"login": "octo"}, "name": "repo"},
        "sender": {"login": "maintainer"},
    }

    with patch(
        "app.workflows.mention_router.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.mention_router.handle_issue_assigned", new_callable=AsyncMock
    ) as mock_assign, patch(
        "app.workflows.mention_router.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json:

        app_inst = mock_app_cls.return_value
        app_inst.get_issue_comments = AsyncMock(return_value=[])
        app_inst.get_referenced_context = AsyncMock(return_value="")

        mock_llm_json.return_value = {
            "intent": "START_ISSUE",
            "reason": "Direct command",
        }

        await handle_comment_created(payload)
        mock_assign.assert_called_once()


@pytest.mark.parametrize(
    "intent,is_pr,target_function_called",
    [
        ("RESOLVE_CONFLICTS", True, "handle_resolve_conflicts"),
        ("RUN_TESTS", True, "handle_run_tests"),
        ("CREATE_ISSUE", False, "handle_create_issue"),
        ("CREATE_SUB_ISSUES", False, "handle_decompose_epic"),
    ],
)
@pytest.mark.asyncio
async def test_mention_router_intent_dispatch(intent, is_pr, target_function_called):
    payload = {
        "action": "created",
        "installation": {"id": 1},
        "comment": {"body": f"@PriestyAI {intent.lower()}"},
        "issue": {"number": 10, "title": "Test Item"},
        "repository": {"owner": {"login": "octo"}, "name": "repo"},
        "sender": {"login": "dev"},
    }
    if is_pr:
        payload["pull_request"] = {
            "url": "https://api.github.com/repos/octo/repo/pulls/10"
        }

    with patch(
        "app.workflows.mention_router.AppInstallationClient"
    ) as mock_app_cls, patch(
        f"app.workflows.mention_router.{target_function_called}", new_callable=AsyncMock
    ) as mock_target, patch(
        "app.workflows.mention_router.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json:

        app_inst = mock_app_cls.return_value
        app_inst.get_pull_request = AsyncMock(return_value={"draft": False, "body": ""})
        app_inst.get_issue_comments = AsyncMock(return_value=[])
        app_inst.get_referenced_context = AsyncMock(return_value="")

        mock_llm_json.return_value = {"intent": intent, "reason": "Testing intent"}

        await handle_comment_created(payload)
        mock_target.assert_called_once()


@pytest.mark.asyncio
async def test_mention_router_rate_limited_sender_ignored():
    spam_guard._cooldowns["blocked_user"] = 9999999999.0
    payload = {
        "action": "created",
        "comment": {"body": "@PriestyAI ping"},
        "sender": {"login": "blocked_user"},
    }
    with patch("app.workflows.mention_router.AppInstallationClient") as mock_app:
        await handle_comment_created(payload)
        mock_app.assert_not_called()


@pytest.mark.asyncio
async def test_mention_router_resolve_conflicts_on_issue_posts_warning():
    payload = {
        "action": "created",
        "installation": {"id": 1},
        "comment": {"body": "@PriestyAI resolve conflicts"},
        "issue": {"number": 10, "title": "Test Issue"},
        "repository": {"owner": {"login": "octo"}, "name": "repo"},
        "sender": {"login": "dev"},
    }

    with patch(
        "app.workflows.mention_router.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.mention_router.machine_client"
    ) as mock_machine, patch(
        "app.workflows.mention_router.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json:

        app_inst = mock_app_cls.return_value
        app_inst.get_issue_comments = AsyncMock(return_value=[])
        app_inst.get_referenced_context = AsyncMock(return_value="")

        mock_llm_json.return_value = {"intent": "RESOLVE_CONFLICTS"}
        mock_machine.create_issue_comment = AsyncMock()

        await handle_comment_created(payload)

        mock_machine.create_issue_comment.assert_called_once_with(
            owner="octo",
            repo="repo",
            issue_number=10,
            body="Merge conflicts can only be resolved on Pull Requests, not Issues.",
        )


@pytest.mark.asyncio
async def test_mention_router_run_tests_on_issue_posts_warning():
    payload = {
        "action": "created",
        "installation": {"id": 1},
        "comment": {"body": "@PriestyAI run tests"},
        "issue": {"number": 10, "title": "Test Issue"},
        "repository": {"owner": {"login": "octo"}, "name": "repo"},
        "sender": {"login": "dev"},
    }

    with patch(
        "app.workflows.mention_router.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.mention_router.machine_client"
    ) as mock_machine, patch(
        "app.workflows.mention_router.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json:

        app_inst = mock_app_cls.return_value
        app_inst.get_issue_comments = AsyncMock(return_value=[])
        app_inst.get_referenced_context = AsyncMock(return_value="")

        mock_llm_json.return_value = {"intent": "RUN_TESTS"}
        mock_machine.create_issue_comment = AsyncMock()

        await handle_comment_created(payload)

        mock_machine.create_issue_comment.assert_called_once_with(
            owner="octo",
            repo="repo",
            issue_number=10,
            body="Test execution can only be run on Pull Request branches.",
        )


@pytest.mark.asyncio
async def test_mention_router_summarize_intent():
    payload = {
        "action": "created",
        "installation": {"id": 1},
        "comment": {"body": "@PriestyAI summarize this"},
        "issue": {"number": 10, "title": "Task Title", "body": "Task details"},
        "repository": {"owner": {"login": "octo"}, "name": "repo"},
        "sender": {"login": "dev"},
    }

    with patch(
        "app.workflows.mention_router.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.mention_router.machine_client"
    ) as mock_machine, patch(
        "app.workflows.mention_router.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json:

        app_inst = mock_app_cls.return_value
        app_inst.get_issue_comments = AsyncMock(return_value=[])
        app_inst.get_referenced_context = AsyncMock(return_value="")

        mock_llm_json.return_value = {"intent": "SUMMARIZE"}
        mock_machine.create_issue_comment = AsyncMock()

        await handle_comment_created(payload)

        mock_machine.create_issue_comment.assert_called_once()


@pytest.mark.asyncio
async def test_mention_router_none_intent():
    payload = {
        "action": "created",
        "installation": {"id": 1},
        "comment": {"body": "@PriestyAI asdfghjk"},
        "issue": {"number": 10, "title": "Task"},
        "repository": {"owner": {"login": "octo"}, "name": "repo"},
        "sender": {"login": "test_spammer"},
    }

    with patch(
        "app.workflows.mention_router.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.mention_router.spam_guard.record_none_strike"
    ) as mock_strike, patch(
        "app.workflows.mention_router.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json:

        app_inst = mock_app_cls.return_value
        app_inst.get_issue_comments = AsyncMock(return_value=[])
        app_inst.get_referenced_context = AsyncMock(return_value="")

        mock_llm_json.return_value = {"intent": "NONE"}

        await handle_comment_created(payload)
        mock_strike.assert_called_once_with("test_spammer")


@pytest.mark.asyncio
async def test_mention_router_general_qa_with_code_lookup():
    payload = {
        "action": "created",
        "installation": {"id": 1},
        "comment": {"body": "@PriestyAI where is the token timeout set?"},
        "issue": {"number": 10, "title": "Question"},
        "repository": {"owner": {"login": "octo"}, "name": "repo"},
        "sender": {"login": "dev"},
    }

    with patch(
        "app.workflows.mention_router.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.mention_router.machine_client"
    ) as mock_machine, patch(
        "app.workflows.mention_router.llm_client.generate_json", new_callable=AsyncMock
    ) as mock_llm_json, patch(
        "app.workflows.mention_router.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_default_branch_sha = AsyncMock(
            return_value={"branch": "main", "sha": "sha123"}
        )
        app_inst.get_repository_tree = AsyncMock(return_value=["src/config.py"])
        app_inst.get_file_content = AsyncMock(return_value="TOKEN_TIMEOUT = 300")
        app_inst.get_issue_comments = AsyncMock(return_value=[])
        app_inst.get_referenced_context = AsyncMock(return_value="")

        mock_llm_json.side_effect = [
            {"intent": "GENERAL_QA"},
            {"selected_files": ["src/config.py"]},
        ]
        mock_llm_gen.return_value = (
            "The token timeout is set in `src/config.py` on line 1."
        )
        mock_machine.create_issue_comment = AsyncMock()

        await handle_comment_created(payload)

        mock_machine.create_issue_comment.assert_called_once_with(
            owner="octo",
            repo="repo",
            issue_number=10,
            body="The token timeout is set in `src/config.py` on line 1.",
        )
