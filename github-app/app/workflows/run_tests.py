import io
import logging
import os
import shutil
import tempfile
import zipfile
from typing import Any, Dict, List
import httpx
from app.config import settings
from app.core.docker_runner import docker_runner
from app.github.client import AppInstallationClient, machine_client
from app.llm.client import llm_client

logger = logging.getLogger("priesty.run_tests")

MANIFEST_CANDIDATES = [
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "composer.json",
    "Gemfile",
]


async def handle_run_tests(
    installation_id: int,
    owner: str,
    repo: str,
    pull_number: int,
    requester_login: str,
    user_prompt: str,
) -> None:
    app_client = AppInstallationClient(installation_id)
    pr = await app_client.get_pull_request(owner, repo, pull_number)
    head_sha = pr.get("head", {}).get("sha")

    caller_perm = await app_client.get_user_permission(owner, repo, requester_login)
    is_maintainer = caller_perm in ("admin", "write", "maintain")
    head_repo = pr.get("head", {}).get("repo", {})
    base_repo = pr.get("base", {}).get("repo", {})
    is_fork = head_repo.get("full_name") != base_repo.get("full_name")

    if is_fork and not is_maintainer:
        logger.warning(
            f"Rejected on-demand test execution on fork PR #{pull_number} from non-maintainer @{requester_login}"
        )
        await machine_client.create_issue_comment(
            owner=owner,
            repo=repo,
            issue_number=pull_number,
            body="Running on-demand sandbox test runs on external fork PRs requires approval from a repository maintainer.",
        )
        return

    logger.info(
        f"Running on-demand tests for {owner}/{repo}#{pull_number} requested by @{requester_login}"
    )

    file_tree = await app_client.get_repository_tree(owner, repo, head_sha)

    manifest_snippets: List[str] = []
    for manifest_name in MANIFEST_CANDIDATES:
        if manifest_name in file_tree:
            content = await app_client.get_file_content(
                owner, repo, manifest_name, head_sha
            )
            if content:
                manifest_snippets.append(f"=== {manifest_name} ===\n{content[:600]}")

    manifest_context = (
        "\n\n".join(manifest_snippets) or "No common build manifests found."
    )

    plan_prompt = f"""You are determining the container environment and commands to run tests/linters for this repository.
Repository Files:
{file_tree[:60]}

Project Manifests:
{manifest_context}

Rules:
- Python with pytest/ruff: choose "python:3.11-slim" and commands like ["pip install -e .[dev]", "pytest"]
- Python with unittest: choose "python:3.11-slim" and commands like ["python -m unittest discover"]
- Node.js / TypeScript: choose "node:20-alpine" and commands like ["npm ci || npm install", "npm test"]
- Go: choose "golang:1.22-alpine" and commands like ["go test ./..."]
- Rust: choose "rust:1.78-alpine" and commands like ["cargo test"]
- Static assets without automated tests: set "has_automated_checks" to false and "docker_image" to null.

Return JSON:
{{
  "has_automated_checks": true,
  "docker_image": "node:20-alpine",
  "commands": ["npm test"]
}}
"""
    plan = await llm_client.generate_json(
        prompt=plan_prompt,
        system_prompt="You are a senior DevOps engineer. Return valid JSON only.",
        model_tier="routing",
    )

    has_checks = plan.get("has_automated_checks", True)
    docker_image = plan.get("docker_image")
    commands = plan.get("commands", [])

    docker_output = "No automated test suite configured for this stack (static analysis / documentation repo)."
    success = True

    if has_checks and docker_image and commands and docker_runner.available:
        temp_dir = tempfile.mkdtemp(prefix="priesty_run_tests_")
        try:
            zip_url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{head_sha}"
            token = await app_client._get_headers()
            async with httpx.AsyncClient(follow_redirects=True) as http_client:
                r = await http_client.get(zip_url, headers=token)
                if r.status_code == 200:
                    z = zipfile.ZipFile(io.BytesIO(r.content))
                    root_folder = z.namelist()[0]
                    z.extractall(temp_dir)
                    extracted_path = os.path.join(temp_dir, root_folder)

                    result = await docker_runner.run_commands(
                        workspace_dir=extracted_path,
                        commands=commands,
                        image=docker_image,
                    )
                    success = result["success"]
                    docker_output = (
                        f"Exit Code: {result['exit_code']}\n"
                        f"STDOUT:\n{result['stdout']}\n"
                        f"STDERR:\n{result['stderr']}"
                    )
        except Exception as e:
            logger.error(f"Error during on-demand test run: {e}")
            docker_output = f"Test execution error: {e}"
            success = False
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    conclusion = "success" if success else "failure"
    check_title = (
        f"Sandbox Tests {'Passed' if success else 'Failed'} ({docker_image or 'local'})"
    )
    await app_client.create_check_run(
        owner=owner,
        repo=repo,
        head_sha=head_sha,
        name="PriestyAI Test Suite",
        conclusion=conclusion,
        title=check_title,
        summary=f"On-demand test run requested by @{requester_login}.",
        text=docker_output,
    )

    summary_prompt = f"""You are PriestyAI, an engineer teammate.
You just ran the test suite locally on the latest PR commit for @{requester_login}.
Tone: Natural, direct teammate tone. Speak like an engineer reporting test results in chat.

USER REQUEST:
\"{user_prompt}\"

TEST EXECUTION RESULTS:
{docker_output}

INSTRUCTIONS:
Write a friendly, concise markdown reply to @{requester_login}:
- State clearly if the test suite passed or failed.
- Highlight specific failures (failed tests, tracebacks, or linter errors) if any occurred.
- If everything passed, give a quick teammate thumbs-up.
"""

    reply_text = await llm_client.generate(
        prompt=summary_prompt,
        system_prompt="You are a collaborative senior developer. Output natural markdown only.",
        model_tier="reasoning",
    )

    await machine_client.create_issue_comment(owner, repo, pull_number, reply_text)
    logger.info(f"On-demand test results posted to {owner}/{repo}#{pull_number}")
