import re
import base64
import logging
from typing import Any
import httpx
from config.settings import GITHUB_TOKEN
from tools.registry import tool_registry, ToolExecutionContext

logger = logging.getLogger("PriestyAI.GitHubTools")

GITHUB_API_BASE = "https://api.github.com"
GITHUB_EMOJI = "<:github:1542000155371507802>"

PRUNED_DIRECTORIES = {
    ".git", "node_modules", "dist", "build", "target", "vendor",
    ".venv", "venv", "__pycache__", ".next", ".nuxt", ".output",
    "coverage", ".idea", ".vscode", "bin", "obj"
}

PRUNED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".mp3", ".mp4", ".wav", ".ogg", ".zip", ".tar", ".gz",
    ".exe", ".dll", ".so", ".dylib", ".class", ".pyc",
    ".lock", "-lock.json"
}

MANIFEST_FILES = [
    "package.json", "Cargo.toml", "pyproject.toml", "requirements.txt",
    "go.mod", "pom.xml", "build.gradle", "CMakeLists.txt", "Makefile",
    "composer.json", "Gemfile", "flake.nix"
]

def get_auth_headers(accept: str = "application/vnd.github.v3+json") -> dict[str, str]:
    headers = {
        "Accept": accept,
        "User-Agent": "PriestyAI-DiscordBot"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN.strip()}"
    return headers

def parse_repo_identifier(repo_str: str) -> tuple[str, str, str, str]:
    clean = repo_str.strip().rstrip("/")
    
    tree_match = re.search(r'github\.com\/([^\/]+)\/([^\/]+)\/(?:tree|blob)\/([^\/]+)\/?(.*)', clean, re.IGNORECASE)
    if tree_match:
        owner = tree_match.group(1)
        repo = tree_match.group(2).replace(".git", "")
        ref = tree_match.group(3)
        subpath = tree_match.group(4).strip()
        return owner, repo, ref, subpath

    url_match = re.search(r'github\.com\/([^\/]+)\/([^\/\?#]+)', clean, re.IGNORECASE)
    if url_match:
        owner = url_match.group(1)
        repo = url_match.group(2).replace(".git", "")
        return owner, repo, "", ""

    parts = clean.split("/")
    if len(parts) >= 2 and not clean.startswith("http"):
        return parts[0], parts[1].replace(".git", ""), "", ""

    return "", "", "", ""

def format_tree_text(tree_items: list[dict[str, Any]], max_items: int = 120) -> str:
    paths = []
    for item in tree_items:
        p = item.get("path", "")
        item_type = item.get("type", "blob")
        
        parts = p.split("/")
        if any(part in PRUNED_DIRECTORIES for part in parts[:-1]):
            continue
        if any(part in PRUNED_DIRECTORIES for part in parts):
            continue
        if any(p.endswith(ext) for ext in PRUNED_EXTENSIONS):
            continue

        paths.append(p + ("/" if item_type == "tree" else ""))

    if not paths:
        return "(Empty or fully pruned repository tree)"

    truncated = paths[:max_items]
    lines = ["```text"]
    for path_str in truncated:
        lines.append(f"• {path_str}")

    if len(paths) > max_items:
        lines.append(f"\n... and {len(paths) - max_items} more files/folders")
    lines.append("```")
    return "\n".join(lines)

@tool_registry.register(
    name="github_repo",
    description=(
        "Authenticated GitHub repository inspection and code reading engine.\n"
        "Parameters:\n"
        "- repo: Repository full URL (e.g. 'https://github.com/torvalds/linux') or 'owner/repo' string.\n"
        "- action: One of:\n"
        "  * 'digest': (Default) Returns repo metadata, dependencies, README, and folder tree in one turn.\n"
        "  * 'tree': Returns recursive file and directory layout (filter with 'path').\n"
        "  * 'read_file': Reads specific source file content (supports 'start_line', 'end_line', 'ref') and attaches for native inspection.\n"
        "  * 'search_code': Searches codebase for symbols, functions, or keywords (using 'query').\n"
        "  * 'commits': Fetches recent commit logs.\n"
        "  * 'issue': Reads an issue or pull request discussion by 'number'.\n"
        "  * 'pr_diff': Fetches the unified code diff of a pull request by 'number'.\n"
        "- path: File or subfolder path to inspect.\n"
        "- query: Keyword or symbol to search for across the repository.\n"
        "- ref: Branch, tag, or commit SHA (defaults to default branch).\n"
        "- number: Issue or PR numeric ID.\n"
        "- start_line: 1-indexed line to start reading from (for read_file).\n"
        "- end_line: Line to stop reading at (for read_file, 0 for full file)."
    )
)
async def github_repo(
    repo: str,
    action: str = "digest",
    path: str = "",
    query: str = "",
    ref: str = "",
    number: int = 0,
    start_line: int = 1,
    end_line: int = 0,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    owner, repo_name, url_ref, url_subpath = parse_repo_identifier(repo)
    if not owner or not repo_name:
        return {"error": f"Invalid GitHub repository identifier '{repo}'. Expected 'owner/repo' or a GitHub URL."}

    effective_ref = ref.strip() or url_ref
    effective_path = path.strip() or url_subpath
    action_clean = action.strip().lower()

    logger.info(f"[github_repo] Action='{action_clean}' on {owner}/{repo_name} (path='{effective_path}', ref='{effective_ref}')")

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        
        if action_clean in ["read_file", "file", "read"]:
            if not effective_path:
                return {"error": "Parameter 'path' is required when action='read_file'."}

            file_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/contents/{effective_path.lstrip('/')}"
            params = {"ref": effective_ref} if effective_ref else {}
            
            resp = await client.get(file_url, headers=get_auth_headers(), params=params)
            if resp.status_code == 404:
                return {"error": f"File '{effective_path}' not found in {owner}/{repo_name} (ref: {effective_ref or 'default'})."}
            elif resp.status_code != 200:
                return {"error": f"GitHub API error ({resp.status_code}): {resp.text}"}

            data = resp.json()
            if isinstance(data, list):
                return {
                    "type": "directory",
                    "path": effective_path,
                    "message": f"'{effective_path}' is a directory. Use action='tree' to inspect folder contents.",
                    "items": [item.get("name") for item in data[:30]]
                }

            content_b64 = data.get("content", "")
            try:
                raw_bytes = base64.b64decode(content_b64)
                raw_text = raw_bytes.decode("utf-8", errors="replace")
            except Exception:
                raw_bytes = b""
                raw_text = "(Binary file or unreadable encoding)"

            fname = effective_path.split("/")[-1] or "source_code.txt"

            if context and raw_bytes:
                if not hasattr(context, "staged_github_files"):
                    context.staged_github_files = []
                context.staged_github_files.append({
                    "filename": fname,
                    "bytes": raw_bytes,
                    "path": effective_path
                })

            lines = raw_text.splitlines()
            total_lines = len(lines)

            s_line = max(1, start_line)
            e_line = min(total_lines, end_line) if end_line > 0 else min(total_lines, s_line + 500)
            
            sliced_lines = lines[s_line - 1:e_line]
            formatted_content = "\n".join([f"{s_line + idx:4d} | {line}" for idx, line in enumerate(sliced_lines)])

            ext = effective_path.rsplit(".", 1)[-1].lower() if "." in effective_path else ""

            return {
                "status": "success",
                "repo": f"{owner}/{repo_name}",
                "path": effective_path,
                "filename": fname,
                "ref": effective_ref or data.get("sha", "default"),
                "total_lines": total_lines,
                "showing_lines": f"{s_line} - {e_line}",
                "language": ext,
                "content": formatted_content
            }

        elif action_clean in ["tree", "structure", "files"]:
            ref_param = effective_ref or "HEAD"
            tree_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/git/trees/{ref_param}?recursive=1"
            
            resp = await client.get(tree_url, headers=get_auth_headers())
            if resp.status_code != 200:
                return {"error": f"Failed to fetch directory tree for {owner}/{repo_name} ({resp.status_code}): {resp.text}"}

            tree_data = resp.json()
            raw_tree = tree_data.get("tree", [])

            if effective_path:
                norm_sub = effective_path.strip("/")
                raw_tree = [t for t in raw_tree if t.get("path", "").startswith(norm_sub)]

            tree_str = format_tree_text(raw_tree, max_items=120)
            return {
                "status": "success",
                "repo": f"{owner}/{repo_name}",
                "ref": ref_param,
                "subpath_filter": effective_path or "root",
                "total_nodes_found": len(raw_tree),
                "tree": tree_str
            }

        elif action_clean in ["search_code", "search", "find"]:
            if not query.strip():
                return {"error": "Parameter 'query' is required when action='search_code'."}

            search_query = f"repo:{owner}/{repo_name} {query.strip()}"
            search_url = f"{GITHUB_API_BASE}/search/code"
            
            resp = await client.get(search_url, headers=get_auth_headers(), params={"q": search_query, "per_page": 6})
            if resp.status_code != 200:
                return {"error": f"Code search failed ({resp.status_code}): {resp.text}"}

            data = resp.json()
            items = data.get("items", [])
            results = []

            for item in items:
                results.append({
                    "path": item.get("path"),
                    "name": item.get("name"),
                    "html_url": item.get("html_url")
                })

            return {
                "status": "success",
                "repo": f"{owner}/{repo_name}",
                "query": query,
                "total_matches": data.get("total_count", len(results)),
                "results": results
            }

        elif action_clean in ["commits", "history", "log"]:
            commits_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/commits"
            params = {"per_page": 8}
            if effective_ref:
                params["sha"] = effective_ref

            resp = await client.get(commits_url, headers=get_auth_headers(), params=params)
            if resp.status_code != 200:
                return {"error": f"Failed to fetch commit history ({resp.status_code}): {resp.text}"}

            commits_raw = resp.json()
            commit_list = []
            for c in commits_raw:
                commit_info = c.get("commit", {})
                author_info = commit_info.get("author", {})
                commit_list.append({
                    "sha": c.get("sha", "")[:7],
                    "message": commit_info.get("message", "").splitlines()[0] if commit_info.get("message") else "",
                    "author": author_info.get("name", "Unknown"),
                    "date": author_info.get("date", "")
                })

            return {
                "status": "success",
                "repo": f"{owner}/{repo_name}",
                "count": len(commit_list),
                "commits": commit_list
            }

        elif action_clean in ["issue", "pr", "pull"]:
            if number <= 0:
                return {"error": "Parameter 'number' (Issue or PR ID) is required when action='issue'."}

            issue_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/issues/{number}"
            resp = await client.get(issue_url, headers=get_auth_headers())
            if resp.status_code != 200:
                return {"error": f"Failed to fetch issue #{number} ({resp.status_code}): {resp.text}"}

            issue_data = resp.json()
            
            comments_resp = await client.get(f"{issue_url}/comments", headers=get_auth_headers(), params={"per_page": 5})
            comments = []
            if comments_resp.status_code == 200:
                for comm in comments_resp.json():
                    comments.append({
                        "author": comm.get("user", {}).get("login"),
                        "body": comm.get("body", "")[:600],
                        "created_at": comm.get("created_at")
                    })

            return {
                "status": "success",
                "repo": f"{owner}/{repo_name}",
                "number": number,
                "title": issue_data.get("title"),
                "state": issue_data.get("state"),
                "author": issue_data.get("user", {}).get("login"),
                "is_pull_request": "pull_request" in issue_data,
                "body": (issue_data.get("body") or "")[:2000],
                "comments_count": issue_data.get("comments", 0),
                "recent_comments": comments
            }

        elif action_clean in ["pr_diff", "diff"]:
            if number <= 0:
                return {"error": "Parameter 'number' (Pull Request ID) is required when action='pr_diff'."}

            diff_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/pulls/{number}"
            resp = await client.get(diff_url, headers=get_auth_headers(accept="application/vnd.github.v3.diff"))
            if resp.status_code != 200:
                return {"error": f"Failed to fetch diff for PR #{number} ({resp.status_code}): {resp.text}"}

            diff_text = resp.text[:4000]
            return {
                "status": "success",
                "repo": f"{owner}/{repo_name}",
                "pull_number": number,
                "diff": diff_text
            }

        else:
            meta_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}"
            meta_resp = await client.get(meta_url, headers=get_auth_headers())
            if meta_resp.status_code == 404:
                auth_hint = " (GITHUB_TOKEN is configured)" if GITHUB_TOKEN else " (No GITHUB_TOKEN configured; private repos cannot be accessed)"
                return {"error": f"Repository '{owner}/{repo_name}' was not found or is private{auth_hint}."}
            elif meta_resp.status_code != 200:
                return {"error": f"GitHub API error ({meta_resp.status_code}): {meta_resp.text}"}

            meta = meta_resp.json()
            default_branch = meta.get("default_branch", "main")
            target_ref = effective_ref or default_branch

            readme_text = "*No README found*"
            readme_resp = await client.get(f"{meta_url}/readme", headers=get_auth_headers(), params={"ref": target_ref})
            if readme_resp.status_code == 200:
                try:
                    r_b64 = readme_resp.json().get("content", "")
                    readme_text = base64.b64decode(r_b64).decode("utf-8", errors="replace")[:3000]
                except Exception:
                    pass

            tree_url = f"{meta_url}/git/trees/{target_ref}?recursive=1"
            tree_resp = await client.get(tree_url, headers=get_auth_headers())
            tree_str = "(Directory tree unavailable)"
            manifest_content = ""

            if tree_resp.status_code == 200:
                tree_nodes = tree_resp.json().get("tree", [])
                tree_str = format_tree_text(tree_nodes, max_items=80)

                for manifest_name in MANIFEST_FILES:
                    found_node = next((n for n in tree_nodes if n.get("path") == manifest_name), None)
                    if found_node:
                        man_resp = await client.get(f"{meta_url}/contents/{manifest_name}", headers=get_auth_headers(), params={"ref": target_ref})
                        if man_resp.status_code == 200:
                            try:
                                man_b64 = man_resp.json().get("content", "")
                                man_txt = base64.b64decode(man_b64).decode("utf-8", errors="replace")[:1200]
                                manifest_content += f"\n**{manifest_name}:**\n```text\n{man_txt}\n```\n"
                            except Exception:
                                pass
                        if len(manifest_content) > 1800:
                            break

            return {
                "status": "success",
                "repo": f"{owner}/{repo_name}",
                "description": meta.get("description") or "No description provided",
                "default_branch": default_branch,
                "ref_inspected": target_ref,
                "primary_language": meta.get("language") or "Unknown",
                "stars": meta.get("stargazers_count", 0),
                "forks": meta.get("forks_count", 0),
                "open_issues": meta.get("open_issues_count", 0),
                "manifest_dependencies": manifest_content.strip() or "No root manifest detected",
                "tree_preview": tree_str,
                "readme_preview": readme_text
            }

@tool_registry.register(
    name="fetch_github",
    description="Inspects a public or private GitHub repository directory tree and README (Alias for github_repo)."
)
async def fetch_github(repo_url: str, subpath: str = "", context: ToolExecutionContext = None) -> dict[str, Any]:
    return await github_repo(repo=repo_url, action="digest", path=subpath, context=context)