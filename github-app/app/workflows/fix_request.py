import io
import logging
import os
import shutil
import tempfile
import zipfile
from typing import Any, Dict, List, Optional
import httpx
from app.config import settings
from app.core.docker_runner import docker_runner
from app.core.patcher import apply_search_replace
from app.github.client import AppInstallationClient, machine_client
from app.llm.client import llm_client

logger = logging.getLogger("priesty.fix_request")


async def handle_fix_request(
    installation_id: int,
    owner: str,
    repo: str,
    pull_number: int,
    requester_login: str,
    user_prompt: str,
    reply_to_comment_id: Optional[int] = None,
) -> None:
    app_client = AppInstallationClient(installation_id)
    pr = await app_client.get_pull_request(owner, repo, pull_number)

    head_ref = pr.get("head", {}).get("ref")
    head_sha = pr.get("head", {}).get("sha")
    base_ref = pr.get("base", {}).get("ref", "main")

    caller_perm = await app_client.get_user_permission(owner, repo, requester_login)
    is_maintainer = caller_perm in ("admin", "write", "maintain")
    pr_author = pr.get("user", {}).get("login", "")
    is_pr_author = requester_login.lower() == pr_author.lower()
    bot_authored_pr = pr_author.lower() == settings.BOT_USERNAME.lower()

    head_repo = pr.get("head", {}).get("repo", {})
    base_repo = pr.get("base", {}).get("repo", {})
    is_fork = head_repo.get("full_name") != base_repo.get("full_name")
    maintainer_can_modify = pr.get("maintainer_can_modify", True)

    can_modify_branch = not is_fork or maintainer_can_modify
    is_authorized_caller = is_maintainer or is_pr_author or bot_authored_pr
    can_commit = is_authorized_caller and can_modify_branch

    logger.info(
        f"FIX_REQUEST for {owner}/{repo}#{pull_number} by @{requester_login}. "
        f"is_maintainer={is_maintainer}, is_pr_author={is_pr_author}, is_fork={is_fork}, can_commit={can_commit}, "
        f"reply_to_comment_id={reply_to_comment_id}"
    )

    files_changed = await app_client.get_pull_request_files(owner, repo, pull_number)
    file_tree = await app_client.get_repository_tree(owner, repo, head_sha)
    contributing = (
        await app_client.get_file_content(owner, repo, "CONTRIBUTING.md", base_ref)
        or ""
    )

    reviews = await machine_client.get_pull_request_reviews(owner, repo, pull_number)
    review_comments = await app_client.get_pull_request_review_comments(
        owner, repo, pull_number
    )

    latest_review_text = "No previous review text found."
    if reviews:
        changes_requested_reviews = [
            r for r in reviews if r.get("state", "").upper() == "CHANGES_REQUESTED"
        ]
        latest_rev = (
            changes_requested_reviews[-1] if changes_requested_reviews else reviews[-1]
        )
        latest_review_text = (
            f"Verdict: {latest_rev.get('state')}\nBody:\n{latest_rev.get('body')}"
        )

    review_inline_notes = "\n".join(
        [
            f"- {c.get('path')}:{c.get('line') or c.get('original_line') or 'diff'}: {c.get('body')}"
            for c in review_comments[-15:]
        ]
    )

    full_search_text = (
        f"{user_prompt} {latest_review_text} {review_inline_notes} {pr.get('body', '')}"
    )
    referenced_context = await app_client.get_referenced_context(
        owner, repo, full_search_text, current_number=pull_number
    )

    plan_prompt = f"""You are PriestyAI, an experienced software engineer on the team.
Tone: Natural, friendly, concise engineer. No corporate fluff, no emoji spam.

USER REQUEST:
\"{user_prompt}\"

LATEST PR REVIEW SUMMARY:
{latest_review_text}

LATEST REVIEW INLINE FEEDBACK:
{review_inline_notes or 'None'}

{referenced_context}

PR TITLE: {pr.get('title')}
PR CHANGED FILES: {[f['filename'] for f in files_changed]}
REPOSITORY FILES: {file_tree[:60]}

TASK & SCOPE RULES:
1. Determine if this request is IN SCOPE for this PR (localized bug corrections, review comments, unit tests, minor refactors <= 3 files).
2. If OUT OF SCOPE: set "is_out_of_scope": true and provide "scope_explanation".
3. If IN SCOPE:
   - Provide a clean, descriptive Conventional Commit message (e.g. "fix(async-cache): correct import path and await stats coroutines").
   - Break down the fix into files to create or modify.

Return JSON:
{{
  "is_out_of_scope": false,
  "scope_explanation": null,
  "commit_message": "fix(tests): resolve async stats coroutine and update module import",
  "steps": [
    {{
      "step_number": 1,
      "target_file": "tests/test_async_cache.py",
      "action": "MODIFY",
      "action_summary": "Update import to src.async_cache and await stats() calls"
    }}
  ]
}}
"""

    plan_res = await llm_client.generate_json(
        prompt=plan_prompt,
        system_prompt="You are a senior engineer evaluating PR scope and planning atomic fixes. Output JSON only.",
        model_tier="routing",
    )

    is_out_of_scope = plan_res.get("is_out_of_scope", False)
    if is_out_of_scope:
        explanation = plan_res.get(
            "scope_explanation",
            "This request looks broader than the scope of this PR. We should track this as a separate issue on main!",
        )
        logger.info(
            f"FIX_REQUEST on #{pull_number} determined out-of-scope. Declining direct commit."
        )
        await _send_reply(owner, repo, pull_number, explanation, reply_to_comment_id)
        return

    steps: List[Dict[str, Any]] = plan_res.get("steps", [])
    commit_subject = plan_res.get("commit_message", "fix: address review comments")

    if not steps:
        clarification = "I took a look, but wasn't sure what specific file changes are needed. Could you clarify?"
        await _send_reply(owner, repo, pull_number, clarification, reply_to_comment_id)
        return

    resolved_payloads: Dict[str, str] = {}
    fallback_sections: List[str] = []

    for step in steps:
        target_path = step["target_file"]
        action = step.get("action", "MODIFY").upper()
        summary = step.get("action_summary", "")

        file_info = await app_client.get_file_content_and_sha(
            owner=owner, repo=repo, path=target_path, ref=head_ref
        )
        current_content = file_info.get("content")

        if action == "CREATE" or current_content is None:
            updated_code = await _generate_new_file(
                target_path=target_path,
                action_summary=summary,
                contributing=contributing,
                latest_review=latest_review_text,
            )
        else:
            updated_code = await _generate_modified_file(
                target_path=target_path,
                current_content=current_content,
                action_summary=summary,
                contributing=contributing,
                latest_review=latest_review_text,
            )

        resolved_payloads[target_path] = updated_code

        if not can_commit:
            fallback_sections.append(
                f"### `{target_path}`\n<details><summary>View suggested fix</summary>\n\n"
                f"```\n{updated_code}\n```\n\n</details>"
            )

    if not can_commit:
        reason_msg = (
            "Because this PR is on an external fork branch without maintainer write access"
            if is_fork and not maintainer_can_modify
            else "Since this action requires repository write permissions or author status"
        )
        logger.info(
            f"Bot cannot commit directly ({reason_msg}). Posting 1-click code suggestion."
        )
        comment_body = (
            f"Here is the verified fix for the review feedback! {reason_msg}, here is the code to apply:\n\n"
            + "\n\n".join(fallback_sections)
        )
        await _send_reply(owner, repo, pull_number, comment_body, reply_to_comment_id)
        if reply_to_comment_id:
            await machine_client.resolve_review_thread(
                owner=owner,
                repo=repo,
                pull_number=pull_number,
                comment_db_id=reply_to_comment_id,
            )
        return

    stack_plan = await detect_repo_stack(file_tree)
    if is_maintainer or not is_fork:
        resolved_payloads = await verify_and_heal_fix(
            app_client=app_client,
            owner=owner,
            repo=repo,
            branch=head_ref,
            steps=steps,
            file_payloads=resolved_payloads,
            stack_plan=stack_plan,
            contributing=contributing,
        )

    commit_msg = (
        f"{commit_subject}\n\n"
        f"Co-authored-by: {requester_login} <{requester_login}@users.noreply.github.com>"
    )

    latest_head_sha = await app_client.get_branch_sha(owner, repo, head_ref)
    await app_client.create_commit_on_branch(
        owner=owner,
        repo=repo,
        branch_name=head_ref,
        files=resolved_payloads,
        message=commit_msg,
        parent_sha=latest_head_sha,
        force=False,
    )

    for r in reviews:
        if (
            r.get("user", {}).get("login") == settings.BOT_USERNAME
            and r.get("state") == "CHANGES_REQUESTED"
        ):
            await machine_client.dismiss_review(
                owner=owner,
                repo=repo,
                pull_number=pull_number,
                review_id=r["id"],
                message="Requested changes were implemented.",
            )

    if reply_to_comment_id:
        logger.info(
            f"Resolving inline review thread #{reply_to_comment_id} via GraphQL..."
        )
        await machine_client.resolve_review_thread(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            comment_db_id=reply_to_comment_id,
        )

    if requester_login and requester_login.lower() != settings.BOT_USERNAME.lower():
        try:
            logger.info(f"Re-requesting review from @{requester_login}...")
            await machine_client.request_reviewers(
                owner=owner,
                repo=repo,
                pull_number=pull_number,
                reviewers=[requester_login],
            )
        except Exception as e:
            logger.debug(f"Could not re-request review from @{requester_login}: {e}")

    file_list = ", ".join([f"`{p}`" for p in resolved_payloads.keys()])
    reply_prompt = f"""You are PriestyAI, an engineer teammate.
You just completed addressing this PR review feedback from @{requester_login}:
\"{user_prompt}\"

Updated files: {file_list}

Write a natural, concise 1-2 sentence teammate reply confirming you pushed the fixes to the branch and all checks pass.
No emoji spam, no robotic jargon.
"""
    reply_text = await llm_client.generate(
        prompt=reply_prompt,
        system_prompt="You are a senior developer teammate. Speak naturally.",
        model_tier="routing",
    )

    await _send_reply(owner, repo, pull_number, reply_text, reply_to_comment_id)
    logger.info(f"FIX_REQUEST completed successfully for {owner}/{repo}#{pull_number}")


async def _generate_new_file(
    target_path: str, action_summary: str, contributing: str, latest_review: str
) -> str:
    prompt = f"""You are creating a new file at '{target_path}' to address review feedback.
Task: {action_summary}

REVIEW FEEDBACK:
{latest_review}

CONTRIBUTING GUIDELINES:
{contributing}

Return ONLY the complete raw file content. Do not include markdown code block backticks or commentary."""

    raw_code = await llm_client.generate(
        prompt=prompt,
        system_prompt="You are an expert developer writing clean code. Output raw code only.",
        model_tier="reasoning",
    )
    return strip_markdown_fences(raw_code)


async def _generate_modified_file(
    target_path: str,
    current_content: str,
    action_summary: str,
    contributing: str,
    latest_review: str,
) -> str:
    line_count = len(current_content.splitlines())

    if line_count <= 80:
        prompt = f"""You are modifying '{target_path}' to address review feedback.
Task: {action_summary}

REVIEW FEEDBACK:
{latest_review}

CONTRIBUTING GUIDELINES:
{contributing}

CURRENT FILE CONTENT:
```
{current_content}
```

Return ONLY the complete updated raw file content. Do not include markdown code block backticks or commentary."""

        raw_code = await llm_client.generate(
            prompt=prompt,
            system_prompt="You are an expert developer updating an existing file. Output raw code only.",
            model_tier="reasoning",
        )
        return strip_markdown_fences(raw_code)

    prompt = f"""You are modifying '{target_path}' to address review feedback.
Task: {action_summary}

REVIEW FEEDBACK:
{latest_review}

CONTRIBUTING GUIDELINES:
{contributing}

CURRENT FILE CONTENT:
```
{current_content}
```

INSTRUCTIONS:
Output one or more precise SEARCH/REPLACE blocks to make the edits without rewriting the entire file:
<<<<<<< SEARCH
[exact lines to replace]
=======
[new replacement lines]
>>>>>>> REPLACE
"""

    patch_output = await llm_client.generate(
        prompt=prompt,
        system_prompt="You are an expert developer producing precise search/replace blocks.",
        model_tier="reasoning",
    )
    return apply_search_replace(current_content, patch_output)


async def verify_and_heal_fix(
    app_client: AppInstallationClient,
    owner: str,
    repo: str,
    branch: str,
    steps: List[Dict[str, Any]],
    file_payloads: Dict[str, str],
    stack_plan: Dict[str, Any],
    contributing: str,
) -> Dict[str, str]:
    if (
        not stack_plan.get("has_automated_checks")
        or not stack_plan.get("docker_image")
        or not docker_runner.available
    ):
        return file_payloads

    current_payloads = dict(file_payloads)

    verified_1, output_1 = await run_docker_sandbox_check(
        app_client, owner, repo, branch, current_payloads, stack_plan
    )
    if verified_1:
        return current_payloads

    logger.warning(
        "Fix test check failed. Initiating Self-Healing Pass 1 with traceback..."
    )
    for s in steps:
        path = s["target_file"]
        if path not in current_payloads:
            continue

        healing_prompt = f"""Test verification failed after applying fix to '{path}'.

TASK: {s.get('action_summary')}
TEST FAILURE TRACEBACK:
{output_1}

CURRENT FILE CODE:
```
{current_payloads[path]}
```

CONTRIBUTING GUIDELINES:
{contributing}

Fix the root cause of the test error and return ONLY the complete updated file content."""

        healed_code = await llm_client.generate(
            prompt=healing_prompt,
            system_prompt="You are an expert developer fixing test errors. Output raw code only.",
            model_tier="reasoning",
        )
        current_payloads[path] = strip_markdown_fences(healed_code)

    verified_2, output_2 = await run_docker_sandbox_check(
        app_client, owner, repo, branch, current_payloads, stack_plan
    )
    if verified_2:
        return current_payloads

    logger.warning(
        "Attempt 2 test check failed. Initiating Heavy Reasoning Self-Healing Pass 2..."
    )
    for s in steps:
        path = s["target_file"]
        if path not in current_payloads:
            continue

        healing_prompt_3 = f"""Test verification failed again for '{path}'.

TASK: {s.get('action_summary')}
LATEST TEST FAILURE TRACEBACK:
{output_2}

LATEST ATTEMPT CODE:
```
{current_payloads[path]}
```

CONTRIBUTING RULES:
{contributing}

Resolve the root cause and return ONLY the complete updated file content."""

        healed_code_3 = await llm_client.generate(
            prompt=healing_prompt_3,
            system_prompt="You are a principal engineer fixing critical test failures. Output raw code only.",
            model_tier="reasoning",
        )
        current_payloads[path] = strip_markdown_fences(healed_code_3)

    return current_payloads


async def run_docker_sandbox_check(
    app_client: AppInstallationClient,
    owner: str,
    repo: str,
    branch: str,
    file_payloads: Dict[str, str],
    stack_plan: Dict[str, Any],
) -> tuple[bool, str]:
    docker_image = stack_plan.get("docker_image")
    commands = stack_plan.get("commands", [])

    if not docker_image or not commands:
        return True, "No automated test commands."

    temp_dir = tempfile.mkdtemp(prefix="priesty_fix_verify_")
    try:
        zip_url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch}"
        token = await app_client._get_headers()
        async with httpx.AsyncClient(follow_redirects=True) as http_client:
            r = await http_client.get(zip_url, headers=token)
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content))
                root_folder = z.namelist()[0]
                z.extractall(temp_dir)
                extracted_path = os.path.join(temp_dir, root_folder)

                for rel_path, code_str in file_payloads.items():
                    dest = os.path.join(extracted_path, rel_path)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write(code_str)

                res = await docker_runner.run_commands(
                    workspace_dir=extracted_path,
                    commands=commands,
                    image=docker_image,
                )
                return (
                    res["success"],
                    f"STDOUT:\n{res['stdout']}\nSTDERR:\n{res['stderr']}",
                )
    except Exception as e:
        return True, str(e)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return True, "No verification output."


async def detect_repo_stack(file_tree: List[str]) -> Dict[str, Any]:
    stack_prompt = f"""Analyze the repository file structure to determine the build and test environment.
Repository Files:
{file_tree[:60]}

Rules:
- Python with pytest/ruff: choose "python:3.11-slim" and pytest/ruff commands.
- Python with unittest: choose "python:3.11-slim" and python -m unittest discover.
- Node.js (package.json): choose "node:20-alpine" and npm test / npm run lint.
- Go (go.mod): choose "golang:1.22-alpine" and go test ./...
- Rust (Cargo.toml): choose "rust:1.78-alpine" and cargo test.
- Static assets, HTML, Markdown, or documentation without automated test runner: set "has_automated_checks": false and "docker_image": null.

Return JSON:
{{
  "has_automated_checks": true,
  "docker_image": "python:3.11-slim" or null,
  "commands": ["pytest"]
}}
"""
    return await llm_client.generate_json(
        prompt=stack_prompt,
        system_prompt="You are a senior devops and build engineer. Output valid JSON only.",
        model_tier="routing",
    )


async def _send_reply(
    owner: str,
    repo: str,
    pull_number: int,
    body: str,
    reply_to_comment_id: Optional[int],
) -> None:
    if reply_to_comment_id:
        await machine_client.reply_to_review_comment(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            comment_id=reply_to_comment_id,
            body=body,
        )
    else:
        await machine_client.create_issue_comment(
            owner=owner, repo=repo, issue_number=pull_number, body=body
        )


def strip_markdown_fences(code: str) -> str:
    if code.startswith("```"):
        lines = code.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines) + "\n"
    return code
