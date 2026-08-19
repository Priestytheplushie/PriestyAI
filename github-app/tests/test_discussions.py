from unittest.mock import AsyncMock, patch
import pytest
import respx
from httpx import Response
from app.workflows.discussion_handler import (
    handle_discussion_opened,
    handle_discussion_comment,
    get_discussion_comment_node_id,
)


@pytest.mark.asyncio
async def test_handle_discussion_opened():
    payload = {
        "discussion": {
            "node_id": "D_node_123",
            "number": 1,
            "title": "How do I setup caching?",
            "body": "Looking for Redis config @PriestyAI",
            "category": {"name": "Q&A"},
        },
        "sender": {"login": "community_user"},
        "repository": {"owner": {"login": "octo"}, "name": "repo"},
        "installation": {"id": 1},
    }

    with patch(
        "app.workflows.discussion_handler.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.discussion_handler.machine_client"
    ) as mock_machine, patch(
        "app.workflows.discussion_handler.llm_client.generate", new_callable=AsyncMock
    ) as mock_llm_gen:

        app_inst = mock_app_cls.return_value
        app_inst.get_default_branch_sha = AsyncMock(
            return_value={"sha": "abc", "branch": "main"}
        )
        app_inst.get_repository_tree = AsyncMock(return_value=["README.md"])
        app_inst.get_file_content = AsyncMock(return_value="# Documentation")

        mock_llm_gen.return_value = "Here is how to configure Redis in app.config..."
        mock_machine.add_discussion_comment = AsyncMock()

        await handle_discussion_opened(payload)

        mock_machine.add_discussion_comment.assert_called_once_with(
            discussion_id="D_node_123",
            body="Here is how to configure Redis in app.config...",
        )


@pytest.mark.asyncio
async def test_handle_discussion_comment_create_issue_intent():
    payload = {
        "action": "created",
        "comment": {
            "body": "@PriestyAI please create an issue for this cache bug",
            "node_id": "DC_123",
            "parent_id": None,
        },
        "discussion": {"node_id": "D_123", "number": 5, "title": "Cache Problem"},
        "sender": {"login": "community_user"},
        "repository": {"owner": {"login": "octo"}, "name": "repo"},
        "installation": {"id": 1},
    }

    with patch(
        "app.workflows.discussion_handler.AppInstallationClient"
    ) as mock_app_cls, patch(
        "app.workflows.discussion_handler.machine_client"
    ) as mock_machine, patch(
        "app.workflows.discussion_handler.llm_client.generate_json",
        new_callable=AsyncMock,
    ) as mock_llm_json:

        app_inst = mock_app_cls.return_value
        app_inst.get_default_branch_sha = AsyncMock(
            return_value={"sha": "abc", "branch": "main"}
        )
        app_inst.get_repository_tree = AsyncMock(return_value=[])
        app_inst.get_repo_labels = AsyncMock(return_value=["bug"])

        mock_llm_json.side_effect = [
            {"intent": "CREATE_ISSUE"},
            {
                "title": "fix: cache eviction bug",
                "body": "Spun off from discussion",
                "selected_labels": ["bug"],
                "reply_text": "Opened #ISSUE_NUMBER",
            },
        ]
        mock_machine.create_issue = AsyncMock(return_value={"number": 75})
        mock_machine.add_discussion_reply = AsyncMock()
        mock_machine.add_discussion_comment = AsyncMock()

        await handle_discussion_comment(payload)

        mock_machine.create_issue.assert_called_once()
        mock_machine.add_discussion_reply.assert_called_once_with(
            discussion_id="D_123",
            reply_to_id="DC_123",
            body="Opened #75",
        )


@pytest.mark.asyncio
@respx.mock
async def test_get_discussion_comment_node_id():
    graphql_response = {
        "data": {
            "repository": {
                "discussion": {
                    "comments": {
                        "nodes": [
                            {"id": "DC_target_node_id", "databaseId": 5555},
                            {"id": "DC_other", "databaseId": 9999},
                        ]
                    }
                }
            }
        }
    }
    respx.post("https://api.github.com/graphql").mock(
        return_value=Response(200, json=graphql_response)
    )

    node_id = await get_discussion_comment_node_id(
        "octo", "repo", discussion_number=1, database_id=5555
    )
    assert node_id == "DC_target_node_id"
