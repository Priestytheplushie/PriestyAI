import base64
import logging
import re
from typing import Any, Dict, List, Optional
import httpx
from app.config import settings
from app.core.auth import auth_manager

logger = logging.getLogger("priesty.github")

GITHUB_API_BASE = "https://api.github.com"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class AppInstallationClient:

    def __init__(self, installation_id: int):
        self.installation_id = installation_id

    async def _get_headers(self) -> Dict[str, str]:
        token = await auth_manager.get_installation_token(self.installation_id)
        return {**GITHUB_HEADERS, "Authorization": f"Bearer {token}"}

    async def get_user_permission(self, owner: str, repo: str, username: str) -> str:
        if owner.lower() == username.lower():
            return "admin"
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/collaborators/{username}/permission"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json().get("permission", "none")
            return "none"

    async def invite_collaborator(
        self, owner: str, repo: str, username: str, permission: str = "push"
    ) -> Optional[Dict[str, Any]]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/collaborators/{username}"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.put(
                url, headers=headers, json={"permission": permission}
            )
            if resp.status_code == 201:
                return resp.json()
            elif resp.status_code == 204:
                return None
            resp.raise_for_status()
            return None

    async def get_issue_details(
        self, owner: str, repo: str, issue_number: int
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_number}"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def get_issue_linked_branch(
        self, owner: str, repo: str, issue_number: int
    ) -> Optional[str]:
        query = """
        query GetIssueLinkedBranches($owner: String!, $repo: String!, $issueNumber: Int!) {
          repository(owner: $owner, name: $repo) {
            issue(number: $issueNumber) {
              linkedBranches(first: 5) {
                nodes {
                  ref {
                    name
                  }
                }
              }
            }
          }
        }
        """
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(
                GITHUB_GRAPHQL_URL,
                headers=headers,
                json={
                    "query": query,
                    "variables": {
                        "owner": owner,
                        "repo": repo,
                        "issueNumber": issue_number,
                    },
                },
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            nodes = (
                data.get("data", {})
                .get("repository", {})
                .get("issue", {})
                .get("linkedBranches", {})
                .get("nodes", [])
            )
            for node in nodes:
                ref_name = node.get("ref", {}).get("name")
                if ref_name:
                    return ref_name
            return None

    async def get_sub_issues(
        self, owner: str, repo: str, issue_number: int
    ) -> List[Dict[str, Any]]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_number}/sub_issues"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return []
            data = resp.json()
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                return data.get("sub_issues", [])
            return []

    async def add_sub_issue(
        self, owner: str, repo: str, parent_issue_number: int, sub_issue_id: int
    ) -> bool:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{parent_issue_number}/sub_issues"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(
                url, headers=headers, json={"sub_issue_id": sub_issue_id}
            )
            return resp.status_code in (200, 201, 204)

    async def create_check_run(
        self,
        owner: str,
        repo: str,
        head_sha: str,
        name: str = "PriestyAI Sandbox Verification",
        status: str = "completed",
        conclusion: Optional[str] = "success",
        title: str = "Sandbox Verification Passed",
        summary: str = "Verified in isolated Docker container.",
        text: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/check-runs"
        headers = await self._get_headers()
        payload: Dict[str, Any] = {
            "name": name,
            "head_sha": head_sha,
            "status": status,
        }
        if conclusion:
            payload["conclusion"] = conclusion

        output_data: Dict[str, Any] = {
            "title": title[:250],
            "summary": summary[:65000],
        }
        if text:
            output_data["text"] = text[:65000]
        payload["output"] = output_data

        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                return resp.json()
            logger.debug(f"Check run creation returned {resp.status_code}: {resp.text}")
            return None

    async def get_pull_request(
        self, owner: str, repo: str, pull_number: int
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def get_pull_request_commits(
        self, owner: str, repo: str, pull_number: int
    ) -> List[Dict[str, Any]]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/commits"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return []
            return resp.json()

    async def get_referenced_context(
        self,
        owner: str,
        repo: str,
        text: str,
        current_number: int,
        max_refs: int = 3,
    ) -> str:
        if not text:
            return ""

        shorthand_matches = re.findall(r"(?<![\w&#])#(\d+)\b", text)
        url_matches = re.findall(
            rf"github\.com/{re.escape(owner)}/{re.escape(repo)}/(?:issues|pull)/(\d+)",
            text,
            re.IGNORECASE,
        )
        all_matches = shorthand_matches + url_matches

        target_ids: List[int] = []
        for m in all_matches:
            num = int(m)
            if num != current_number and num not in target_ids:
                target_ids.append(num)
            if len(target_ids) >= max_refs:
                break

        if not target_ids:
            return ""

        context_blocks = []
        for ref_num in target_ids:
            try:
                data = await self.get_issue_details(owner, repo, ref_num)
                ref_title = data.get("title", "")
                raw_state = data.get("state", "").upper()
                state_reason = data.get("state_reason")
                is_pr = "pull_request" in data
                labels = [
                    l.get("name") for l in data.get("labels", []) if l.get("name")
                ]

                if is_pr:
                    pr_info = await self.get_pull_request(owner, repo, ref_num)
                    if pr_info.get("merged") or raw_state == "MERGED":
                        status_str = "MERGED"
                    elif raw_state == "CLOSED":
                        status_str = "CLOSED (Unmerged)"
                    elif pr_info.get("draft", False):
                        status_str = "OPEN (Draft PR)"
                    else:
                        status_str = "OPEN (Ready for Review)"
                    item_type = "Pull Request"
                else:
                    if raw_state == "CLOSED":
                        status_str = f"CLOSED ({state_reason.replace('_', ' ') if state_reason else 'Completed'})"
                    else:
                        status_str = "OPEN"
                    item_type = "Issue"

                body_text = (data.get("body") or "").strip()
                preview_body = (
                    body_text[:500] + ("..." if len(body_text) > 500 else "")
                    if body_text
                    else "(No description)"
                )
                labels_str = f" | Labels: {', '.join(labels)}" if labels else ""

                context_blocks.append(
                    f"#### Referenced {item_type} #{ref_num} [Status: **{status_str}**{labels_str}]\n"
                    f"**Title**: {ref_title}\n"
                    f"**Summary / Description**:\n{preview_body}"
                )
            except Exception as ref_err:
                logger.debug(f"Could not resolve reference #{ref_num}: {ref_err}")
                continue

        if not context_blocks:
            return ""

        return "### REFERENCED ISSUES & PULL REQUESTS CONTEXT:\n" + "\n\n".join(
            context_blocks
        )

    async def merge_pull_request(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        merge_method: str = "merge",
        commit_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/merge"
        headers = await self._get_headers()
        payload: Dict[str, Any] = {"merge_method": merge_method}
        if commit_title:
            payload["commit_title"] = commit_title
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.put(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def set_issue_state(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        state: str = "closed",
        state_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_number}"
        headers = await self._get_headers()
        payload: Dict[str, Any] = {"state": state}
        if state_reason:
            payload["state_reason"] = state_reason
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.patch(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def lock_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        lock_reason: Optional[str] = "resolved",
    ) -> None:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_number}/lock"
        headers = await self._get_headers()
        payload = {"lock_reason": lock_reason} if lock_reason else {}
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.put(url, headers=headers, json=payload)
            resp.raise_for_status()

    async def unlock_issue(self, owner: str, repo: str, issue_number: int) -> None:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_number}/lock"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.delete(url, headers=headers)
            resp.raise_for_status()

    async def update_pull_request(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}"
        headers = await self._get_headers()
        payload: Dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body

        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.patch(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def get_pull_request_files(
        self, owner: str, repo: str, pull_number: int
    ) -> List[Dict[str, Any]]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/files"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def get_file_content_and_sha(
        self, owner: str, repo: str, path: str, ref: Optional[str] = None
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}"
        params = {"ref": ref} if ref else {}
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 404:
                return {"content": None, "sha": None}
            resp.raise_for_status()
            data = resp.json()
            raw_content = ""
            if "content" in data and data.get("encoding") == "base64":
                raw_content = base64.b64decode(data["content"]).decode(
                    "utf-8", errors="replace"
                )
            return {"content": raw_content, "sha": data.get("sha")}

    async def get_file_content(
        self, owner: str, repo: str, path: str, ref: Optional[str] = None
    ) -> Optional[str]:
        res = await self.get_file_content_and_sha(owner, repo, path, ref)
        return res["content"]

    async def get_repository_tree(
        self, owner: str, repo: str, tree_sha: str
    ) -> List[str]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return []
            tree = resp.json().get("tree", [])
            return [item["path"] for item in tree if item["type"] == "blob"]

    async def get_issue_comments(
        self, owner: str, repo: str, issue_number: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_number}/comments"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers=headers,
                params={"per_page": limit, "sort": "created", "direction": "desc"},
            )
            if resp.status_code != 200:
                return []
            comments = resp.json()
            comments.reverse()
            return comments

    async def get_pull_request_review_comments(
        self, owner: str, repo: str, pull_number: int
    ) -> List[Dict[str, Any]]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/comments"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def get_repo_labels(self, owner: str, repo: str) -> List[str]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/labels"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return []
            return [lbl["name"] for lbl in resp.json()]

    async def add_labels(
        self, owner: str, repo: str, issue_number: int, labels: List[str]
    ) -> List[Dict[str, Any]]:
        if not labels:
            return []
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_number}/labels"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(url, headers=headers, json={"labels": labels})
            if resp.status_code in (200, 201):
                return resp.json()
            return []

    async def remove_label(
        self, owner: str, repo: str, issue_number: int, label_name: str
    ) -> None:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_number}/labels/{label_name}"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.delete(url, headers=headers)
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()

    async def get_branch_sha(self, owner: str, repo: str, branch: str) -> str:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/ref/heads/{branch}"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()["object"]["sha"]

    async def get_default_branch_sha(self, owner: str, repo: str) -> Dict[str, str]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            default_branch = resp.json().get("default_branch", "main")

            sha = await self.get_branch_sha(owner, repo, default_branch)
            return {"branch": default_branch, "sha": sha}

    async def create_branch_with_empty_commit(
        self, owner: str, repo: str, branch_name: str, base_sha: str, message: str
    ) -> str:
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            commit_resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/commits/{base_sha}",
                headers=headers,
            )
            commit_resp.raise_for_status()
            tree_sha = commit_resp.json()["tree"]["sha"]

            bot_email = f"{settings.BOT_USERNAME}@users.noreply.github.com"
            commit_payload = {
                "message": message,
                "tree": tree_sha,
                "parents": [base_sha],
                "author": {"name": settings.BOT_USERNAME, "email": bot_email},
                "committer": {"name": settings.BOT_USERNAME, "email": bot_email},
            }
            new_commit_resp = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json=commit_payload,
            )
            new_commit_resp.raise_for_status()
            new_commit_sha = new_commit_resp.json()["sha"]

            ref_payload = {"ref": f"refs/heads/{branch_name}", "sha": new_commit_sha}
            ref_resp = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs",
                headers=headers,
                json=ref_payload,
            )
            if ref_resp.status_code == 422:
                await client.patch(
                    f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs/heads/{branch_name}",
                    headers=headers,
                    json={"sha": new_commit_sha, "force": True},
                )

            return new_commit_sha

    async def create_commit_on_branch(
        self,
        owner: str,
        repo: str,
        branch_name: str,
        files: Dict[str, str],
        message: str,
        parent_sha: str,
        force: bool = False,
    ) -> str:
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            commit_resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/commits/{parent_sha}",
                headers=headers,
            )
            commit_resp.raise_for_status()
            base_tree_sha = commit_resp.json()["tree"]["sha"]

            tree_items = [
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "content": content,
                }
                for path, content in files.items()
            ]
            tree_payload = {
                "base_tree": base_tree_sha,
                "tree": tree_items,
            }
            tree_resp = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees",
                headers=headers,
                json=tree_payload,
            )
            tree_resp.raise_for_status()
            new_tree_sha = tree_resp.json()["sha"]

            bot_email = f"{settings.BOT_USERNAME}@users.noreply.github.com"
            commit_payload = {
                "message": message,
                "tree": new_tree_sha,
                "parents": [parent_sha],
                "author": {"name": settings.BOT_USERNAME, "email": bot_email},
                "committer": {"name": settings.BOT_USERNAME, "email": bot_email},
            }
            new_commit_resp = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json=commit_payload,
            )
            new_commit_resp.raise_for_status()
            new_commit_sha = new_commit_resp.json()["sha"]

            ref_payload = {"sha": new_commit_sha, "force": force}
            ref_resp = await client.patch(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs/heads/{branch_name}",
                headers=headers,
                json=ref_payload,
            )
            ref_resp.raise_for_status()
            return new_commit_sha

    async def create_merge_commit(
        self,
        owner: str,
        repo: str,
        branch_name: str,
        head_sha: str,
        base_sha: str,
        resolved_files: Dict[str, str],
        message: str,
    ) -> str:
        headers = await self._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            head_commit_resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/commits/{head_sha}",
                headers=headers,
            )
            head_commit_resp.raise_for_status()
            head_tree_sha = head_commit_resp.json()["tree"]["sha"]

            tree_items = [
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "content": content,
                }
                for path, content in resolved_files.items()
            ]

            tree_resp = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees",
                headers=headers,
                json={"base_tree": head_tree_sha, "tree": tree_items},
            )
            tree_resp.raise_for_status()
            new_tree_sha = tree_resp.json()["sha"]

            bot_email = f"{settings.BOT_USERNAME}@users.noreply.github.com"
            commit_payload = {
                "message": message,
                "tree": new_tree_sha,
                "parents": [head_sha, base_sha],
                "author": {"name": settings.BOT_USERNAME, "email": bot_email},
                "committer": {"name": settings.BOT_USERNAME, "email": bot_email},
            }

            commit_resp = await client.post(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json=commit_payload,
            )
            commit_resp.raise_for_status()
            merge_commit_sha = commit_resp.json()["sha"]

            ref_resp = await client.patch(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs/heads/{branch_name}",
                headers=headers,
                json={"sha": merge_commit_sha},
            )
            ref_resp.raise_for_status()
            return merge_commit_sha

    async def mark_pr_ready_for_review(
        self, owner: str, repo: str, pull_number: int
    ) -> bool:
        headers = await self._get_headers()
        query_pr_id = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) {
              id
              isDraft
            }
          }
        }
        """
        mutation_ready = """
        mutation($id: ID!) {
          markPullRequestReadyForReview(input: {pullRequestId: $id}) {
            pullRequest {
              isDraft
            }
          }
        }
        """
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(
                GITHUB_GRAPHQL_URL,
                headers=headers,
                json={
                    "query": query_pr_id,
                    "variables": {
                        "owner": owner,
                        "repo": repo,
                        "number": pull_number,
                    },
                },
            )
            data = resp.json()
            pr_node_id = (
                data.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("id")
            )
            if not pr_node_id:
                return False

            mut_resp = await client.post(
                GITHUB_GRAPHQL_URL,
                headers=headers,
                json={"query": mutation_ready, "variables": {"id": pr_node_id}},
            )
            return mut_resp.status_code == 200

    async def update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        sha: Optional[str],
        branch: str,
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}"
        headers = await self._get_headers()
        bot_email = f"{settings.BOT_USERNAME}@users.noreply.github.com"

        payload: Dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
            "committer": {
                "name": settings.BOT_USERNAME,
                "email": bot_email,
            },
        }
        if sha:
            payload["sha"] = sha

        if author_name and author_email:
            payload["author"] = {"name": author_name, "email": author_email}
        else:
            payload["author"] = {"name": settings.BOT_USERNAME, "email": bot_email}

        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.put(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()


class MachineUserClient:

    def __init__(self):
        self.headers = {
            **GITHUB_HEADERS,
            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
        }
        self._invitations_etag: Optional[str] = None

    async def list_pending_invitations(self) -> List[Dict[str, Any]]:
        url = f"{GITHUB_API_BASE}/user/repository_invitations"
        headers = dict(self.headers)
        if self._invitations_etag:
            headers["If-None-Match"] = self._invitations_etag

        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)

            if resp.status_code == 304:
                return []

            resp.raise_for_status()

            etag = resp.headers.get("etag")
            if etag:
                self._invitations_etag = etag

            return resp.json()

    async def accept_invitation(self, invitation_id: int) -> bool:
        url = f"{GITHUB_API_BASE}/user/repository_invitations/{invitation_id}"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.patch(url, headers=self.headers)
            if resp.status_code == 204:

                self._invitations_etag = None
                return True
            return False

    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues"
        payload: Dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees

        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str,
        draft: bool = True,
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls"
        payload = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft,
        }
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            if resp.status_code == 422:
                logger.error(f"GitHub PR creation failed (422): {resp.text}")
            resp.raise_for_status()
            return resp.json()

    async def assign_users(
        self,
        owner: str,
        repo: str,
        issue_or_pr_number: int,
        assignees: List[str],
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_or_pr_number}/assignees"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(
                url, headers=self.headers, json={"assignees": assignees}
            )
            resp.raise_for_status()
            return resp.json()

    async def remove_assignees(
        self,
        owner: str,
        repo: str,
        issue_or_pr_number: int,
        assignees: List[str],
    ) -> None:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_or_pr_number}/assignees"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            await client.request(
                "DELETE", url, headers=self.headers, json={"assignees": assignees}
            )

    async def request_reviewers(
        self, owner: str, repo: str, pull_number: int, reviewers: List[str]
    ) -> None:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            await client.post(url, headers=self.headers, json={"reviewers": reviewers})

    async def get_pull_request_reviews(
        self, owner: str, repo: str, pull_number: int
    ) -> List[Dict[str, Any]]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/reviews"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def dismiss_review(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        review_id: int,
        message: str,
    ) -> bool:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/reviews/{review_id}/dismissals"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.put(
                url, headers=self.headers, json={"message": message}
            )
            return resp.status_code == 200

    async def post_pull_request_review(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        commit_id: str,
        event: str,
        body: str,
        comments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/reviews"
        payload: Dict[str, Any] = {
            "commit_id": commit_id,
            "body": body,
            "event": event,
        }
        if comments:
            payload["comments"] = comments

        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            if resp.status_code == 422 and comments:
                fallback_body = (
                    body
                    + "\n\n### Inline Feedback\n"
                    + "\n\n".join(
                        [
                            f"**{c.get('path')}:{c.get('line')}**:\n{c.get('body')}"
                            for c in comments
                        ]
                    )
                )
                payload.pop("comments", None)
                payload["body"] = fallback_body
                resp = await client.post(url, headers=self.headers, json=payload)

            resp.raise_for_status()
            return resp.json()

    async def create_issue_comment(
        self, owner: str, repo: str, issue_number: int, body: str
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{issue_number}/comments"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(url, headers=self.headers, json={"body": body})
            resp.raise_for_status()
            return resp.json()

    async def update_issue_comment(
        self, owner: str, repo: str, comment_id: int, body: str
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/comments/{comment_id}"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.patch(url, headers=self.headers, json={"body": body})
            resp.raise_for_status()
            return resp.json()

    async def reply_to_review_comment(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        comment_id: int,
        body: str,
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/comments/{comment_id}/replies"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(url, headers=self.headers, json={"body": body})
            resp.raise_for_status()
            return resp.json()

    async def update_review_comment(
        self, owner: str, repo: str, comment_id: int, body: str
    ) -> Dict[str, Any]:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/comments/{comment_id}"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.patch(url, headers=self.headers, json={"body": body})
            resp.raise_for_status()
            return resp.json()

    async def resolve_review_thread(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        comment_db_id: int,
    ) -> bool:
        query_threads = """
        query GetThreads($owner: String!, $repo: String!, $prNumber: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $prNumber) {
              reviewThreads(first: 50) {
                nodes {
                  id
                  isResolved
                  comments(first: 30) {
                    nodes {
                      databaseId
                    }
                  }
                }
              }
            }
          }
        }
        """
        mutation_resolve = """
        mutation ResolveThread($threadId: ID!) {
          resolveReviewThread(input: {threadId: $threadId}) {
            thread {
              id
              isResolved
            }
          }
        }
        """
        async with httpx.AsyncClient(follow_redirects=True) as client:
            res = await client.post(
                GITHUB_GRAPHQL_URL,
                headers=self.headers,
                json={
                    "query": query_threads,
                    "variables": {
                        "owner": owner,
                        "repo": repo,
                        "prNumber": pull_number,
                    },
                },
            )
            if res.status_code != 200:
                logger.warning(
                    f"Failed to fetch review threads for PR #{pull_number}: {res.text}"
                )
                return False

            data = res.json()
            threads = (
                data.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
                .get("nodes", [])
            )

            target_thread_id = None
            for thread in threads:
                comment_ids = [
                    c["databaseId"]
                    for c in thread.get("comments", {}).get("nodes", [])
                    if c.get("databaseId") is not None
                ]
                if comment_db_id in comment_ids:
                    if thread.get("isResolved"):
                        return True
                    target_thread_id = thread["id"]
                    break

            if not target_thread_id:
                return False

            res_mutation = await client.post(
                GITHUB_GRAPHQL_URL,
                headers=self.headers,
                json={
                    "query": mutation_resolve,
                    "variables": {"threadId": target_thread_id},
                },
            )
            return res_mutation.status_code == 200

    async def add_discussion_comment(
        self, discussion_id: str, body: str
    ) -> Optional[str]:
        mutation = """
        mutation AddDiscussionComment($discussionId: ID!, $body: String!) {
          addDiscussionComment(input: {discussionId: $discussionId, body: $body}) {
            comment {
              id
              url
            }
          }
        }
        """
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(
                GITHUB_GRAPHQL_URL,
                headers=self.headers,
                json={
                    "query": mutation,
                    "variables": {"discussionId": discussion_id, "body": body},
                },
            )
            data = resp.json()
            if "errors" in data:
                logger.error(
                    f"GraphQL error adding discussion comment: {data['errors']}"
                )
                return None
            return (
                data.get("data", {})
                .get("addDiscussionComment", {})
                .get("comment", {})
                .get("id")
            )

    async def add_discussion_reply(
        self, discussion_id: str, reply_to_id: str, body: str
    ) -> Optional[str]:
        mutation = """
        mutation AddDiscussionReply($discussionId: ID!, $replyToId: ID!, $body: String!) {
          addDiscussionComment(input: {discussionId: $discussionId, replyToId: $replyToId, body: $body}) {
            comment {
              id
              url
            }
          }
        }
        """
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.post(
                GITHUB_GRAPHQL_URL,
                headers=self.headers,
                json={
                    "query": mutation,
                    "variables": {
                        "discussionId": discussion_id,
                        "replyToId": reply_to_id,
                        "body": body,
                    },
                },
            )
            data = resp.json()
            if "errors" in data:
                logger.error(f"GraphQL error replying in discussion: {data['errors']}")
                return None
            return (
                data.get("data", {})
                .get("addDiscussionComment", {})
                .get("comment", {})
                .get("id")
            )


machine_client = MachineUserClient()
