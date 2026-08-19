import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app, extract_resource_key, verify_signature, dispatch_webhook_event


def generate_test_signature(payload_bytes: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), msg=payload_bytes, digestmod=hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def test_verify_signature_valid():
    secret = "my_webhook_secret_123"
    body = b'{"action":"opened","number":42}'
    valid_sig = generate_test_signature(body, secret)
    assert verify_signature(body, secret, valid_sig) is True


def test_verify_signature_invalid():
    secret = "my_webhook_secret_123"
    body = b'{"action":"opened","number":42}'
    assert verify_signature(body, secret, "sha256=invalid_hash_value") is False
    assert verify_signature(body, secret, None) is False


def test_extract_resource_key_issue():
    payload = {
        "repository": {"owner": {"login": "octocat"}, "name": "Spoon-Knife"},
        "issue": {"number": 99},
    }
    assert extract_resource_key("issues", payload) == "octocat/Spoon-Knife#99"


@pytest.mark.asyncio
async def test_health_check_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_webhook_valid_signature_accepted(monkeypatch):
    secret = "test_webhook_secret_key"
    monkeypatch.setattr("app.config.settings.GITHUB_WEBHOOK_SECRET", secret)

    payload_dict = {
        "action": "opened",
        "issue": {"number": 10},
        "repository": {"owner": {"login": "octocat"}, "name": "test-repo"},
    }
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    sig = generate_test_signature(payload_bytes, secret)

    headers = {
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": sig,
        "Content-Type": "application/json",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook", content=payload_bytes, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_dispatch_webhook_event_routes():
    with patch(
        "app.main.handle_installation_event", new_callable=AsyncMock
    ) as mock_inst, patch(
        "app.main.handle_issue_opened", new_callable=AsyncMock
    ) as mock_issue:

        await dispatch_webhook_event(
            "installation", {}, {"action": "created", "installation": {"id": 1}}
        )
        mock_inst.assert_called_once()

        await dispatch_webhook_event(
            "issues", {}, {"action": "opened", "issue": {"number": 10}}
        )
        mock_issue.assert_called_once()
