import os
import time
import base64
import logging
from typing import Any
import jwt
import httpx
from config.settings import (
    GITHUB_APP_ID,
    GITHUB_APP_PRIVATE_KEY_PATH,
    GITHUB_TOKEN
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
        self._app_slug: str = "priestyai"
        self._bot_user_id: str | None = None
        self._bot_user_name: str = "PriestyAI[bot]"

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
            "iat": now - 30,
            "exp": now + 500,
            "iss": str(self.app_id)
        }

        try:
            token = jwt.encode(payload, pem, algorithm="RS256")
            return token
        except Exception as e:
            logger.error(f"[GitHubApp] JWT RS256 signing failed: {e}")
            return None

    async def initialize_bot_identity(self):
        app_jwt = self.generate_app_jwt()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PriestyAI-DiscordBot"
        }

        async with httpx.AsyncClient(timeout=8.0) as client:
            if app_jwt:
                try:
                    auth_headers = {**headers, "Authorization": f"Bearer {app_jwt}"}
                    resp = await client.get(f"{GITHUB_API_BASE}/app", headers=auth_headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        self._app_slug = data.get("slug", "priestyai")
                        app_name = data.get("name", "PriestyAI")
                        self._bot_user_name = f"{app_name}[bot]"
                except Exception as ex:
                    logger.debug(f"[GitHubApp] App metadata check skipped: {ex}")

            target_slug = self._app_slug or "priestyai"
            try:
                u_headers = dict(headers)
                if GITHUB_TOKEN:
                    u_headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

                resp = await client.get(f"{GITHUB_API_BASE}/users/{target_slug}%5Bbot%5D", headers=u_headers)
                if resp.status_code == 200:
                    user_data = resp.json()
                    self._bot_user_id = str(user_data.get("id"))
                    logger.info(
                        f"[GitHubApp] Resolved Bot User: '{self._bot_user_name}' "
                        f"(ID: {self._bot_user_id}, Email: {self.get_bot_email()})"
                    )
                else:
                    logger.warning(f"[GitHubApp] Could not resolve bot user ID for {target_slug}[bot] ({resp.status_code}).")
            except Exception as e:
                logger.debug(f"[GitHubApp] Bot user lookup skipped: {e}")

    def get_bot_email(self) -> str:
        uid = self._bot_user_id or self.app_id or "4609597"
        slug = self._app_slug or "priestyai"
        return f"{uid}+{slug}[bot]@users.noreply.github.com"

    def get_bot_name(self) -> str:
        return self._bot_user_name or "PriestyAI[bot]"

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

    async def list_authorized_repositories(self) -> list[dict[str, Any]]:
        app_jwt = self.generate_app_jwt()
        if not app_jwt:
            return []

        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PriestyAI-DiscordBot"
        }

        all_repos = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{GITHUB_API_BASE}/app/installations", headers=headers)
                if resp.status_code != 200:
                    return []

                installations = resp.json()
                for inst in installations:
                    inst_id = inst.get("id")
                    if not inst_id:
                        continue

                    token = await self.get_installation_access_token(inst_id)
                    if not token:
                        continue

                    inst_headers = {
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                        "User-Agent": "PriestyAI-DiscordBot"
                    }
                    r_resp = await client.get(f"{GITHUB_API_BASE}/installation/repositories?per_page=50", headers=inst_headers)
                    if r_resp.status_code == 200:
                        repos = r_resp.json().get("repositories", [])
                        for r in repos:
                            all_repos.append({
                                "full_name": r.get("full_name"),
                                "private": r.get("private", False),
                                "html_url": r.get("html_url"),
                                "owner": r.get("owner", {}).get("login")
                            })

            return all_repos
        except Exception as e:
            logger.error(f"[GitHubApp] Failed to list authorized repositories: {e}")
            return []

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

    async def get_check_run_details(self, owner: str, repo: str, check_run_id: int | str, token: str | None = None) -> dict[str, Any]:
        if not token:
            token, _ = await self.get_installation_token_for_repo(owner, repo)

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PriestyAI-DiscordBot"
        }

        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/check-runs/{check_run_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.error(f"[GitHubApp] Failed to fetch check run details: {e}")
        return {}

    async def create_api_verified_commit_and_branch(
        self,
        owner: str,
        repo: str,
        branch_name: str,
        base_branch: str,
        commit_message: str,
        files_data: list[dict[str, str]],
        token: str
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PriestyAI-DiscordBot"
        }

        async with httpx.AsyncClient(timeout=25.0) as client:
            ref_resp = await client.get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/ref/heads/{base_branch}", headers=headers)
            if ref_resp.status_code != 200:
                return {"error": f"Base branch '{base_branch}' not found: {ref_resp.text}"}

            parent_commit_sha = ref_resp.json()["object"]["sha"]
            commit_resp = await client.get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/commits/{parent_commit_sha}", headers=headers)
            if commit_resp.status_code != 200:
                return {"error": f"Could not fetch parent commit: {commit_resp.text}"}

            base_tree_sha = commit_resp.json()["tree"]["sha"]

            tree_entries = []
            for f in files_data:
                tree_entries.append({
                    "path": f["path"],
                    "mode": "100644",
                    "type": "blob",
                    "content": f["content"]
                })

            tree_resp = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees",
                headers=headers,
                json={"base_tree": base_tree_sha, "tree": tree_entries}
            )
            if tree_resp.status_code not in (200, 201):
                return {"error": f"Failed to create Git tree ({tree_resp.status_code}): {tree_resp.text}"}

            new_tree_sha = tree_resp.json()["sha"]

            commit_payload = {
                "message": commit_message.strip(),
                "tree": new_tree_sha,
                "parents": [parent_commit_sha]
            }

            c_resp = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json=commit_payload
            )
            if c_resp.status_code not in (200, 201):
                return {"error": f"Failed to create Git commit ({c_resp.status_code}): {c_resp.text}"}

            new_commit_data = c_resp.json()
            new_commit_sha = new_commit_data["sha"]
            verification = new_commit_data.get("verification", {})
            logger.info(f"[GitHubApp] Created API commit {new_commit_sha[:7]} (Verified: {verification.get('verified')})")

            check_ref_resp = await client.get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/ref/heads/{branch_name}", headers=headers)
            if check_ref_resp.status_code == 200:
                update_ref_resp = await client.patch(
                    f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs/heads/{branch_name}",
                    headers=headers,
                    json={"sha": new_commit_sha, "force": True}
                )
                if update_ref_resp.status_code not in (200, 201):
                    return {"error": f"Failed to update branch ref: {update_ref_resp.text}"}
            else:
                create_ref_resp = await client.post(
                    f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs",
                    headers=headers,
                    json={"ref": f"refs/heads/{branch_name}", "sha": new_commit_sha}
                )
                if create_ref_resp.status_code not in (200, 201):
                    return {"error": f"Failed to create branch ref: {create_ref_resp.text}"}

            return {
                "status": "success",
                "commit_sha": new_commit_sha,
                "branch": branch_name,
                "verified": verification.get("verified", False)
            }

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