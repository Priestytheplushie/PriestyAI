import logging
import time
from typing import Dict, Tuple
import httpx
import jwt
from app.config import settings

logger = logging.getLogger("priesty.auth")


class GitHubAuthManager:
    """Handles JWT generation and Installation Token caching for the GitHub App."""

    def __init__(self) -> None:
        self._token_cache: Dict[int, Tuple[str, float]] = {}

    def generate_jwt(self) -> str:
        now = int(time.time())

        payload = {
            "iat": now - 60,
            "exp": now + 500,
            "iss": str(settings.GITHUB_APP_ID).strip(),
        }
        return jwt.encode(payload, settings.private_key_pem, algorithm="RS256")

    async def get_installation_token(self, installation_id: int) -> str:
        now = time.time()
        if installation_id in self._token_cache:
            token, exp = self._token_cache[installation_id]
            if now < exp - 60:
                return token

        app_jwt = self.generate_jwt()
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers=headers,
            )
            if resp.is_error:
                logger.error(
                    f"GitHub App Auth failed ({resp.status_code}): {resp.text}"
                )
            resp.raise_for_status()
            data = resp.json()

            token = data["token"]

            self._token_cache[installation_id] = (token, now + (50 * 60))
            return token


auth_manager = GitHubAuthManager()
