import base64
import pytest
import respx
from httpx import Response
from app.github.client import AppInstallationClient, machine_client


@pytest.mark.asyncio
@respx.mock
async def test_app_client_get_user_permission():
    client = AppInstallationClient(1)
    respx.post("https://api.github.com/app/installations/1/access_tokens").mock(
        return_value=Response(201, json={"token": "test_token"})
    )
    respx.get(
        "https://api.github.com/repos/octocat/repo/collaborators/alice/permission"
    ).mock(return_value=Response(200, json={"permission": "admin"}))

    perm = await client.get_user_permission("octocat", "repo", "alice")
    assert perm == "admin"

    owner_perm = await client.get_user_permission("octocat", "repo", "octocat")
    assert owner_perm == "admin"


@pytest.mark.asyncio
@respx.mock
async def test_app_client_file_content_and_sha():
    client = AppInstallationClient(1)
    respx.post("https://api.github.com/app/installations/1/access_tokens").mock(
        return_value=Response(201, json={"token": "test_token"})
    )

    encoded = base64.b64encode(b"print('hello world')").decode("utf-8")
    respx.get("https://api.github.com/repos/octocat/repo/contents/main.py").mock(
        return_value=Response(
            200, json={"content": encoded, "encoding": "base64", "sha": "sha123"}
        )
    )

    result = await client.get_file_content_and_sha("octocat", "repo", "main.py")
    assert result["content"] == "print('hello world')"
    assert result["sha"] == "sha123"

    respx.get("https://api.github.com/repos/octocat/repo/contents/missing.py").mock(
        return_value=Response(404)
    )
    missing = await client.get_file_content_and_sha("octocat", "repo", "missing.py")
    assert missing["content"] is None
    assert missing["sha"] is None


@pytest.mark.asyncio
@respx.mock
async def test_app_client_labels_and_states():
    client = AppInstallationClient(1)
    respx.post("https://api.github.com/app/installations/1/access_tokens").mock(
        return_value=Response(201, json={"token": "test_token"})
    )

    respx.get("https://api.github.com/repos/octocat/repo/labels").mock(
        return_value=Response(200, json=[{"name": "bug"}, {"name": "feature"}])
    )
    labels = await client.get_repo_labels("octocat", "repo")
    assert labels == ["bug", "feature"]

    respx.post("https://api.github.com/repos/octocat/repo/issues/10/labels").mock(
        return_value=Response(200, json=[{"name": "bug"}])
    )
    added = await client.add_labels("octocat", "repo", 10, ["bug"])
    assert len(added) == 1

    respx.patch("https://api.github.com/repos/octocat/repo/issues/10").mock(
        return_value=Response(200, json={"state": "closed"})
    )
    closed = await client.set_issue_state("octocat", "repo", 10, state="closed")
    assert closed["state"] == "closed"


@pytest.mark.asyncio
@respx.mock
async def test_machine_client_operations():
    respx.post("https://api.github.com/repos/octocat/repo/issues").mock(
        return_value=Response(201, json={"number": 88, "title": "New Issue"})
    )
    issue = await machine_client.create_issue("octocat", "repo", "New Issue", "Body")
    assert issue["number"] == 88

    respx.post("https://api.github.com/repos/octocat/repo/pulls").mock(
        return_value=Response(201, json={"number": 99, "title": "New PR"})
    )
    pr = await machine_client.create_pull_request(
        "octocat", "repo", "New PR", "head", "base", "Body"
    )
    assert pr["number"] == 99

    respx.post("https://api.github.com/repos/octocat/repo/issues/99/comments").mock(
        return_value=Response(201, json={"id": 12345, "body": "Hello!"})
    )
    comment = await machine_client.create_issue_comment("octocat", "repo", 99, "Hello!")
    assert comment["id"] == 12345

    respx.patch("https://api.github.com/user/repository_invitations/555").mock(
        return_value=Response(204)
    )
    accepted = await machine_client.accept_invitation(555)
    assert accepted is True
