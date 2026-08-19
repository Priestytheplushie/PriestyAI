import base64
import pytest
import respx
from httpx import Response
from app.github.client import AppInstallationClient, machine_client


@pytest.mark.asyncio
@respx.mock
async def test_app_client_user_permission_and_invite():
    client = AppInstallationClient(1)
    respx.post("https://api.github.com/app/installations/1/access_tokens").mock(
        return_value=Response(201, json={"token": "test_token"})
    )

    respx.get(
        "https://api.github.com/repos/octocat/repo/collaborators/alice/permission"
    ).mock(return_value=Response(200, json={"permission": "admin"}))
    perm = await client.get_user_permission("octocat", "repo", "alice")
    assert perm == "admin"

    assert await client.get_user_permission("octocat", "repo", "octocat") == "admin"

    respx.put("https://api.github.com/repos/octocat/repo/collaborators/alice").mock(
        return_value=Response(201, json={"id": 123, "invitee": {"login": "alice"}})
    )
    invite = await client.invite_collaborator("octocat", "repo", "alice")
    assert invite["id"] == 123


@pytest.mark.asyncio
@respx.mock
async def test_app_client_graphql_linked_branches_and_ready():
    client = AppInstallationClient(1)
    respx.post("https://api.github.com/app/installations/1/access_tokens").mock(
        return_value=Response(201, json={"token": "test_token"})
    )

    respx.post("https://api.github.com/graphql").mock(
        return_value=Response(
            200,
            json={
                "data": {
                    "repository": {
                        "issue": {
                            "linkedBranches": {
                                "nodes": [{"ref": {"name": "feature/branch-linked"}}]
                            }
                        }
                    }
                }
            },
        )
    )
    branch = await client.get_issue_linked_branch("octocat", "repo", 10)
    assert branch == "feature/branch-linked"

    query_pr_resp = {
        "data": {
            "repository": {"pullRequest": {"id": "PR_node_id_123", "isDraft": True}}
        }
    }
    mutation_resp = {
        "data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}
    }
    respx.post("https://api.github.com/graphql").mock(
        side_effect=[
            Response(200, json=query_pr_resp),
            Response(200, json=mutation_resp),
        ]
    )
    ready = await client.mark_pr_ready_for_review("octocat", "repo", 10)
    assert ready is True


@pytest.mark.asyncio
@respx.mock
async def test_app_client_git_trees_and_commits():
    client = AppInstallationClient(1)
    respx.post("https://api.github.com/app/installations/1/access_tokens").mock(
        return_value=Response(201, json={"token": "test_token"})
    )

    respx.get(
        "https://api.github.com/repos/octocat/repo/git/trees/sha_tree?recursive=1"
    ).mock(
        return_value=Response(
            200,
            json={
                "tree": [
                    {"path": "main.py", "type": "blob"},
                    {"path": "docs", "type": "tree"},
                    {"path": "README.md", "type": "blob"},
                ]
            },
        )
    )
    tree = await client.get_repository_tree("octocat", "repo", "sha_tree")
    assert tree == ["main.py", "README.md"]

    respx.get("https://api.github.com/repos/octocat/repo/git/commits/base_sha").mock(
        return_value=Response(200, json={"tree": {"sha": "base_tree_sha"}})
    )
    respx.post("https://api.github.com/repos/octocat/repo/git/commits").mock(
        return_value=Response(201, json={"sha": "empty_commit_sha"})
    )
    respx.post("https://api.github.com/repos/octocat/repo/git/refs").mock(
        return_value=Response(201, json={"ref": "refs/heads/new-branch"})
    )
    new_sha = await client.create_branch_with_empty_commit(
        owner="octocat",
        repo="repo",
        branch_name="new-branch",
        base_sha="base_sha",
        message="chore: init branch",
    )
    assert new_sha == "empty_commit_sha"


@pytest.mark.asyncio
@respx.mock
async def test_app_client_update_file_and_review_comments():
    client = AppInstallationClient(1)
    respx.post("https://api.github.com/app/installations/1/access_tokens").mock(
        return_value=Response(201, json={"token": "test_token"})
    )

    respx.put("https://api.github.com/repos/octocat/repo/contents/main.py").mock(
        return_value=Response(
            200, json={"content": {"name": "main.py"}, "commit": {"sha": "sha_updated"}}
        )
    )
    res = await client.update_file(
        owner="octocat",
        repo="repo",
        path="main.py",
        content="print('updated')",
        message="fix: update file",
        sha="sha_old",
        branch="main",
    )
    assert res["commit"]["sha"] == "sha_updated"

    respx.get("https://api.github.com/repos/octocat/repo/pulls/10/comments").mock(
        return_value=Response(200, json=[{"id": 1, "body": "Inline note"}])
    )
    review_comments = await client.get_pull_request_review_comments(
        "octocat", "repo", 10
    )
    assert len(review_comments) == 1

    respx.get("https://api.github.com/repos/octocat/repo/issues/10/comments").mock(
        return_value=Response(200, json=[{"id": 2, "body": "Issue comment"}])
    )
    issue_comments = await client.get_issue_comments("octocat", "repo", 10)
    assert len(issue_comments) == 1


@pytest.mark.asyncio
@respx.mock
async def test_machine_client_threads_and_discussions():

    get_threads_resp = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "id": "thread_node_123",
                                "isResolved": False,
                                "comments": {"nodes": [{"databaseId": 88888}]},
                            }
                        ]
                    }
                }
            }
        }
    }
    resolve_mutation_resp = {
        "data": {
            "resolveReviewThread": {
                "thread": {"id": "thread_node_123", "isResolved": True}
            }
        }
    }
    respx.post("https://api.github.com/graphql").mock(
        side_effect=[
            Response(200, json=get_threads_resp),
            Response(200, json=resolve_mutation_resp),
        ]
    )

    resolved = await machine_client.resolve_review_thread(
        owner="octocat", repo="repo", pull_number=10, comment_db_id=88888
    )
    assert resolved is True

    reply_resp = {
        "data": {
            "addDiscussionComment": {
                "comment": {"id": "DC_reply_node", "url": "https://..."}
            }
        }
    }
    respx.post("https://api.github.com/graphql").mock(
        return_value=Response(200, json=reply_resp)
    )
    reply_id = await machine_client.add_discussion_reply(
        discussion_id="D_123", reply_to_id="DC_parent", body="Reply content"
    )
    assert reply_id == "DC_reply_node"
