import time
import pytest
import jwt
import respx
from httpx import Response
from app.core.auth import GitHubAuthManager
from app.config import settings


def test_generate_jwt():
    auth_mgr = GitHubAuthManager()
    token = auth_mgr.generate_jwt()

    assert isinstance(token, str)
    assert len(token) > 20

    decoded = jwt.decode(token, options={"verify_signature": False})
    assert decoded["iss"] == "123456"
    assert "exp" in decoded
    assert "iat" in decoded


@pytest.mark.asyncio
@respx.mock
async def test_get_installation_token_with_caching():
    auth_mgr = GitHubAuthManager()

    route = respx.post(
        "https://api.github.com/app/installations/999/access_tokens"
    ).mock(
        return_value=Response(201, json={"token": "ghs_mock_installation_token_abc"})
    )

    token1 = await auth_mgr.get_installation_token(999)
    assert token1 == "ghs_mock_installation_token_abc"
    assert route.call_count == 1

    token2 = await auth_mgr.get_installation_token(999)
    assert token2 == "ghs_mock_installation_token_abc"
    assert route.call_count == 1
