import os
import time
import logging
from typing import Any
import jwt
import httpx
from config.settings import (
    GITHUB_APP_ID,
    GITHUB_APP_PRIVATE_KEY_PATH
)

logger = logging.getLogger("PriestyAI.GitHubAppClient")

GITHUB_API_BASE = "https://api.github.com"

class GitHubAppClient:
    def __init__(
        self,
        app_id: str = GITHUB_APP_ID,
        private_key_path: str = GITHUB_APP_PRIVATE_KEY_PATH
    ):
        self.app_id = app_id
        self.private_key_path = private_key_path
        self._private_key_pem: str | None = None
        self._token_cache: dict[int, dict[str, Any]] = {}

    def is_configured(self) -> bool:
        return bool(self.app_id and os.path.exists(self.private_key_path))

    def _get_private_key(self) -> str | None:
        if self._private_key_pem:
            return self._private_key_pem
        if os.path.exists(self.private_key_path):
            try:
                with open(self.private_key_path, "r", encoding="utf-8") as f:
                    self._private_key_pem = f.read()
                return self._private_key_pem
            except Exception as e:
                logger.error(f"[GitHubApp] Failed to read private key from {self.private_key_path}: {e}")
        return None

    def generate_app_jwt(self) -> str | None:
        pem = self._get_private_key()
        if not pem or not self.app_id:
            logger.warning("[GitHubApp] Cannot generate JWT: Missing App ID or Private Key PEM.")
            return None

        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + (10 * 60),
            "iss": str(self.app_id)
        }

        try:
            token = jwt.encode(payload, pem, algorithm="RS256")
            return token
        except Exception as e:
            logger.error(f"[GitHubApp] JWT RS256 signing failed: {e}")
            return None

    async def get_repo_installation(self, owner: str, repo: str) -> dict[str, Any] | None:
        app_jwt = self.generate_app_jwt()
        if not app_jwt:
            return None

        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PriestyAI-DiscordBot"
        }

        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/installation"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(f"[GitHubApp] Found installation #{data.get('id')} for repository {owner}/{repo}")
                    return data
                elif resp.status_code == 404:
                    logger.debug(f"[GitHubApp] App not installed on {owner}/{repo} (HTTP 404)")
                    return None
                else:
                    logger.warning(f"[GitHubApp] Installation lookup returned {resp.status_code}: {resp.text}")
                    return None
        except Exception as e:
            logger.error(f"[GitHubApp] Failed to query repo installation: {e}")
            return None

    async def get_installation_access_token(self, installation_id: int | str) -> str | None:
        inst_id_int = int(installation_id)
        now = time.time()

        cached = self._token_cache.get(inst_id_int)
        if cached and cached["expires_at"] > (now + 120):
            return cached["token"]

        app_jwt = self.generate_app_jwt()
        if not app_jwt:
            return None

        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PriestyAI-DiscordBot"
        }

        url = f"{GITHUB_API_BASE}/app/installations/{inst_id_int}/access_tokens"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    token = data["token"]
                    self._token_cache[inst_id_int] = {
                        "token": token,
                        "expires_at": now + 3500
                    }
                    logger.info(f"[GitHubApp] Generated fresh installation access token for installation #{inst_id_int}")
                    return token
                else:
                    logger.error(f"[GitHubApp] Failed to create access token ({resp.status_code}): {resp.text}")
                    return None
        except Exception as e:
            logger.error(f"[GitHubApp] Error requesting installation token: {e}")
            return None

    async def get_installation_token_for_repo(self, owner: str, repo: str) -> tuple[str | None, int | None]:
        inst_data = await self.get_repo_installation(owner, repo)
        if not inst_data:
            return None, None

        inst_id = inst_data.get("id")
        if not inst_id:
            return None, None

        token = await self.get_installation_access_token(inst_id)
        return token, inst_id

    async def get_default_branch(self, owner: str, repo: str, token: str | None = None) -> str:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PriestyAI-DiscordBot"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json().get("default_branch", "main")
        except Exception:
            pass
        return "main"

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str = "main",
        token: str | None = None
    ) -> dict[str, Any]:
        if not token:
            token, _ = await self.get_installation_token_for_repo(owner, repo)

        if not token:
            return {"error": "GitHub App is not installed on this repository or failed to authorize."}

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PriestyAI-DiscordBot"
        }

        payload = {
            "title": title.strip(),
            "body": body.strip(),
            "head": head_branch.strip(),
            "base": base_branch.strip(),
            "maintainer_can_modify": True
        }

        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    logger.info(f"[GitHubApp] Successfully created PR #{data.get('number')} on {owner}/{repo}: {data.get('html_url')}")
                    return {
                        "status": "created",
                        "pr_number": data.get("number"),
                        "html_url": data.get("html_url"),
                        "title": data.get("title"),
                        "state": data.get("state"),
                        "created_at": data.get("created_at")
                    }
                else:
                    err_msg = resp.json().get("message", resp.text)
                    logger.warning(f"[GitHubApp] PR creation returned {resp.status_code}: {err_msg}")
                    return {"error": f"GitHub API error ({resp.status_code}): {err_msg}"}
        except Exception as e:
            logger.error(f"[GitHubApp] Failed to create pull request: {e}")
            return {"error": f"Failed to connect to GitHub API: {str(e)}"}

github_app_client = GitHubAppClient()