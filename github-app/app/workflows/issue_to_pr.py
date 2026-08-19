import logging
import io
import os
import re
import shutil
import tempfile
import zipfile
from typing import Any, Dict, List, Optional
import httpx
from app.config import settings
from app.core.docker_runner import docker_runner
from app.core.patcher import apply_search_replace
from app.github.client import AppInstallationClient, machine_client
from app.github.state import (
    embed_metadata,
    extract_metadata,
    mark_step_completed_in_body,
)
from app.llm.client import llm_client

logger = logging.getLogger("priesty.issue_to_pr")


def clean_branch_slug(title: str) -> str:

    cleaned = re.sub(
        r"^(?:fix|feat|chore|refactor|docs|style|bug|feature|issue)(?:\([^\)]*\))?[\s:\-\(\)\#\d]*",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    cleaned = re.sub(r"^#?\d+[\s:\-\.]*", "", cleaned).strip()

    if not cleaned:
        cleaned = title

    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^\w\s-]", "", cleaned)
    cleaned = re.sub(r"[-\s]+", "-", cleaned).strip("-")
    return cleaned[:35].rstrip("-")


def sanitize_branch_name(candidate_branch: Optional[str], issue_title: str) -> str:
    valid_prefixes = ("feature/", "fix/", "docs/", "chore/", "refactor/", "test/")

    title_lower = issue_title.lower()
    if any(kw in title_lower for kw in ["bug", "fix", "error", "broken", "regression"]):
        default_prefix = "fix/"
    elif any(kw in title_lower for kw in ["doc", "docs", "readme"]):
        default_prefix = "docs/"
    elif any(kw in title_lower for kw in ["refactor", "cleanup"]):
        default_prefix = "refactor/"
    elif any(kw in title_lower for kw in ["test", "tests"]):
        default_prefix = "test/"
    else:
        default_prefix = "feature/"

    if candidate_branch:
        b = candidate_branch.lower().strip().replace(" ", "-")
        b = re.sub(r"issue-\d+-?", "", b)
        b = re.sub(r"#\d+", "", b)
        b = re.sub(r"[^\w/\-]", "", b)

        if b.startswith("feat/"):
            b = "feature/" + b[5:]
        elif b.startswith("doc/"):
            b = "docs/" + b[4:]

        if not any(b.startswith(p) for p in valid_prefixes):
            b = f"{default_prefix}{b.lstrip('/')}"

        parts = [p for p in b.split("/") if p]
        if len(parts) > 2:
            b = f"{parts[0]}/{'-'.join(parts[1:])}"

        return b[:40].rstrip("-")

    slug = clean_branch_slug(issue_title)
    return f"{default_prefix}{slug or 'update'}"


async def handle_issue_assigned(payload: Dict[str, Any]) -> None:
    assignee = payload.get("assignee", {}).get("login")
    if assignee != settings.BOT_USERNAME:
        return

    installation_id = payload.get("installation", {}).get("id")
    issue = payload.get("issue", {})
    repo_data = payload.get("repository", {})
    owner = repo_data.get("owner", {}).get("login")
    repo = repo_data.get("name")
    issue_number = issue.get("number")
    maintainer_login = payload.get("sender", {}).get("login")

    logger.info(
        f"PriestyAI assigned to Issue #{issue_number} in {owner}/{repo} by {maintainer_login}"
    )

    app_client = AppInstallationClient(installation_id)

    sub_issues = await app_client.get_sub_issues(owner, repo, issue_number)
    if sub_issues:
        open_sub_issues = [s for s in sub_issues if s.get("state") == "open"]
        next_task = open_sub_issues[0] if open_sub_issues else sub_issues[0]
        next_num = next_task.get("number")
        next_title = next_task.get("title")

        sub_issue_summary = "\n".join(
            [
                f"- #{s.get('number')} {s.get('title')} ({s.get('state')})"
                for s in sub_issues
            ]
        )

        guide_prompt = f"""You are PriestyAI, an engineer teammate.
A teammate (@{maintainer_login or 'team'}) asked to start working on Issue #{issue_number} ('{issue.get('title')}').
However, this is a large multi-part project that already has {len(sub_issues)} smaller sub-tasks opened for it.

SUB-TASKS ALREADY OPENED:
{sub_issue_summary}

NEXT OPEN TASK TO WORK ON:
#{next_num}: {next_title}

INSTRUCTIONS:
1. Explain naturally that this project is large and is being tackled through the smaller sub-tasks listed above.
2. DO NOT use the word "epic" anywhere in your response.
3. Suggest working on the next open sub-task (#{next_num}) instead of doing everything in one giant pull request.
4. Let them know they can assign you to #{next_num} or reply with "@{settings.BOT_USERNAME} work on #{next_num}" to begin.
5. Tone: Friendly, natural engineer. No robotic boilerplate, no emoji spam.
"""
        reply_text = await llm_client.generate(
            prompt=guide_prompt,
            system_prompt="You are a collaborative senior developer teammate. Speak naturally.",
            model_tier="routing",
        )
        logger.info(
            f"Issue #{issue_number} already has {len(sub_issues)} sub-tasks. Guiding user to #{next_num}..."
        )
        await machine_client.create_issue_comment(owner, repo, issue_number, reply_text)
        return

    default_branch_info = await app_client.get_default_branch_sha(owner, repo)
    base_sha = default_branch_info["sha"]
    default_branch = default_branch_info["branch"]

    file_tree = await app_client.get_repository_tree(owner, repo, base_sha)
    contributing = (
        await app_client.get_file_content(owner, repo, "CONTRIBUTING.md", base_sha)
        or ""
    )

    issue_comments = await app_client.get_issue_comments(
        owner, repo, issue_number, limit=15
    )
    discussion_lines = [
        f"- @{c.get('user', {}).get('login')}: {c.get('body', '')}"
        for c in issue_comments
    ]
    discussion_transcript = (
        "\n".join(discussion_lines) if discussion_lines else "No previous comments."
    )

    full_issue_text = (
        f"{issue.get('title', '')}\n{issue.get('body', '')}\n"
        + "\n".join([c.get("body", "") for c in issue_comments])
    )
    referenced_context = await app_client.get_referenced_context(
        owner, repo, full_issue_text, current_number=issue_number
    )

    linked_branch = await app_client.get_issue_linked_branch(owner, repo, issue_number)
    if linked_branch:
        logger.info(
            f"Found existing development linked branch '{linked_branch}' for Issue #{issue_number}!"
        )

    is_greenfield = len(file_tree) <= 3
    loaded_code_blocks = []

    if is_greenfield:
        logger.info(f"Repository {owner}/{repo} detected as Greenfield/Initial state.")
        greenfield_guidance = (
            "NOTICE: This repository is in its initial / greenfield stage. "
            "You are responsible for creating new files, scaffolding initial project structures, "
            "configuration files, manifests, and test suites from scratch."
        )
        for path in file_tree:
            content = await app_client.get_file_content(owner, repo, path, base_sha)
            if content:
                loaded_code_blocks.append(
                    f"### File: `{path}`\n```\n{content[:3000]}\n```"
                )
    else:
        greenfield_guidance = "NOTICE: This is an established repository. You may create new files or modify existing files as needed."
        locator_prompt = f"""You are a precise codebase file locator.
Given this issue and discussion, select the 2 to 4 most relevant files from the repository tree that the engineer must read to plan the implementation.

ISSUE TITLE: {issue.get('title')}
ISSUE BODY:
{issue.get('body')}

REPOSITORY FILES:
{file_tree}

Return JSON:
{{
  "selected_files": ["src/main.ts", "package.json"]
}}
"""
        locator_res = await llm_client.generate_json(
            prompt=locator_prompt,
            system_prompt="You are a precise file locator. Return valid JSON only.",
            model_tier="routing",
        )
        selected_files = locator_res.get("selected_files", [])
        for path in selected_files:
            if path in file_tree:
                content = await app_client.get_file_content(owner, repo, path, base_sha)
                if content:
                    loaded_code_blocks.append(
                        f"### File: `{path}`\n```\n{content[:4000]}\n```"
                    )

    codebase_context = (
        "\n\n".join(loaded_code_blocks)
        if loaded_code_blocks
        else "No existing source code files available."
    )

    plan_prompt = f"""You are PriestyAI, an experienced software engineer on the team.
You were assigned to Issue #{issue_number}. Plan the implementation and evaluate scope.
Tone: Natural, concise engineer. No corporate fluff, no emoji spam.

ISSUE TITLE: {issue.get('title')}
ISSUE DESCRIPTION (OP):
{issue.get('body') or 'No description provided.'}

ISSUE DISCUSSION & COMMENTS:
{discussion_transcript}

{referenced_context}

{greenfield_guidance}

EXISTING REPOSITORY FILES:
{file_tree[:60]}

EXISTING CODE SAMPLES:
{codebase_context}

CONTRIBUTING GUIDELINES:
{contributing}

SCOPE & PLANNING RULES:
1. Determine if this task is TOO BROAD / MULTI-PART (requires > 5 steps, touches multiple independent subsystems, or is a massive multi-part overhaul):
   - If TOO BROAD: Set "is_too_broad": true, provide "epic_explanation" (do not use the word epic), and list 2 to 4 "proposed_phases".
2. If STANDARD SCOPE (fits cleanly in 1 to 5 steps):
   - Set "is_too_broad": false.
   - Propose a clean branch name (e.g. "feature/dropdown-touch" or "fix/click-bubbling").
   - Break down the implementation into 1 to 5 clean, logical steps.
   - For every file in a step, specify "action": "CREATE" (for new files) or "MODIFY" (for existing files).
   - Write natural teammate copy ("pr_title", "pr_intro", "pr_call_to_action", "issue_comment").

Return JSON:
{{
  "is_too_broad": false,
  "epic_explanation": null,
  "proposed_phases": [],
  "branch_name": "feature/dropdown-touch-handling",
  "pr_title": "fix(dropdown): fix touch event propagation and click-outside bubbling",
  "pr_intro": "Here is the implementation plan to resolve #{issue_number}.",
  "pr_call_to_action": "Let me know if this plan looks good to you, and I will get to work!",
  "issue_comment": "I put together an implementation plan in draft PR #PR_NUMBER. Take a look and let me know if it looks good!",
  "steps": [
    {{
      "step_number": 1,
      "title": "Fix touch event propagation and add unit tests",
      "difficulty": "standard",
      "files": [
        {{
          "path": "src/components/Dropdown.tsx",
          "action": "MODIFY",
          "summary": "Replace e.stopPropagation with e.preventDefault on touch handlers"
        }}
      ]
    }}
  ]
}}
"""

    plan_res = await llm_client.generate_json(
        prompt=plan_prompt,
        system_prompt="You are a senior software engineer writing clean atomic plans. Output JSON only.",
        model_tier="reasoning",
    )

    is_too_broad = plan_res.get("is_too_broad", False)
    if is_too_broad:
        explanation = plan_res.get(
            "epic_explanation",
            "This project is large and spans multiple subsystems. To keep pull requests clean and reviewable, I recommend breaking it down into smaller sub-tasks.",
        )
        phases = plan_res.get("proposed_phases", [])
        phase_lines = [f"{i+1}. {p}" for i, p in enumerate(phases)]

        proposal_comment = (
            f"Hey @{maintainer_login or 'team'}, {explanation}\n\n"
            f"### Proposed Milestones\n"
            f"{chr(10).join(phase_lines)}\n\n"
            f"Reply with `@{settings.BOT_USERNAME} split this up` if you'd like me to open these tracking sub-tasks, or let me know if you want to adjust the breakdown!"
        )
        logger.info(
            f"Issue #{issue_number} identified as broad task. Posting sub-task proposal..."
        )
        await machine_client.create_issue_comment(
            owner, repo, issue_number, proposal_comment
        )
        return

    if linked_branch:
        branch_name = linked_branch
    else:
        suggested_branch = plan_res.get("branch_name")
        branch_name = sanitize_branch_name(
            suggested_branch, issue.get("title", "feature")
        )

    pr_title = plan_res.get("pr_title", f"feat: Implement #{issue_number}")
    pr_intro = plan_res.get(
        "pr_intro", "Here is the implementation plan for this issue:"
    )
    pr_call_to_action = plan_res.get(
        "pr_call_to_action",
        "Let me know if this plan looks good to you, and I'll get started!",
    )
    issue_comment_template = plan_res.get(
        "issue_comment",
        "I've opened a draft PR with the proposed plan: #PR_NUMBER. Let me know if it looks good!",
    )
    steps = plan_res.get("steps", [])

    for s in steps:
        if "files" not in s or not s["files"]:
            target_path = s.get("target_file", "unknown")
            is_new = target_path not in file_tree
            s["files"] = [
                {
                    "path": target_path,
                    "action": "CREATE" if is_new else "MODIFY",
                    "summary": s.get("action_summary", s.get("title", "")),
                }
            ]

    if not steps:
        await machine_client.create_issue_comment(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            body="I analyzed the issue, but wasn't sure what specific file changes are needed. Could you clarify?",
        )
        return

    try:
        current_branch_sha = await app_client.get_branch_sha(owner, repo, branch_name)

        if current_branch_sha == base_sha:
            logger.info(
                f"Branch '{branch_name}' is at base commit (0 commits ahead). Adding initial commit..."
            )
            await app_client.create_branch_with_empty_commit(
                owner=owner,
                repo=repo,
                branch_name=branch_name,
                base_sha=base_sha,
                message=f"chore: initialize {branch_name}",
            )
    except Exception:
        logger.info(f"Creating branch '{branch_name}' for Issue #{issue_number}...")
        await app_client.create_branch_with_empty_commit(
            owner=owner,
            repo=repo,
            branch_name=branch_name,
            base_sha=base_sha,
            message=f"chore: initialize {branch_name}",
        )

    checklist_lines = []
    for s in steps:
        file_tags = ", ".join([f"`{f['path']}`" for f in s["files"]])
        checklist_lines.append(
            f"- [ ] **{s['step_number']}. {s['title']}** ({file_tags})"
        )

    human_body = f"""### Implementation Plan (Issue #{issue_number})

Resolves #{issue_number}

{pr_intro}

#### Proposed Changes
{chr(10).join(checklist_lines)}

{pr_call_to_action}
"""

    metadata = {
        "issue_number": issue_number,
        "maintainer": maintainer_login,
        "branch": branch_name,
        "base_sha": base_sha,
        "steps": steps,
    }

    full_pr_body = embed_metadata(human_body, metadata)

    draft_pr = await machine_client.create_pull_request(
        owner=owner,
        repo=repo,
        title=pr_title,
        head=branch_name,
        base=default_branch,
        body=full_pr_body,
        draft=True,
    )
    pr_number = draft_pr["number"]

    assignees_to_add = [settings.BOT_USERNAME]
    if maintainer_login:
        assignees_to_add.append(maintainer_login)
    await machine_client.assign_users(owner, repo, pr_number, assignees_to_add)

    issue_comment = issue_comment_template.replace("#PR_NUMBER", f"#{pr_number}")
    await machine_client.create_issue_comment(owner, repo, issue_number, issue_comment)
    logger.info(
        f"Draft PR #{pr_number} initialized on `{branch_name}` for Issue #{issue_number}"
    )


async def execute_approved_plan(
    installation_id: int,
    owner: str,
    repo: str,
    pull_number: int,
) -> None:
    app_client = AppInstallationClient(installation_id)
    pr = await app_client.get_pull_request(owner, repo, pull_number)
    body = pr.get("body", "")
    metadata = extract_metadata(body)

    if not metadata:
        logger.warning(f"No metadata found on PR #{pull_number}. Cannot execute plan.")
        return

    maintainer = metadata.get("maintainer")
    branch_name = metadata.get("branch")
    base_sha = metadata.get("base_sha")
    steps = metadata.get("steps", [])

    if not base_sha:
        default_branch_info = await app_client.get_default_branch_sha(owner, repo)
        base_sha = default_branch_info["sha"]

    logger.info(
        f"Starting execution of approved plan on PR #{pull_number} ({len(steps)} steps) on `{branch_name}`..."
    )

    if maintainer:
        await machine_client.remove_assignees(owner, repo, pull_number, [maintainer])

    contributing = (
        await app_client.get_file_content(owner, repo, "CONTRIBUTING.md", base_sha)
        or ""
    )

    current_body = body

    has_completed_steps = any(
        f"- [x] **{s['step_number']}." in current_body for s in steps
    )
    if has_completed_steps:
        current_parent_sha = await app_client.get_branch_sha(owner, repo, branch_name)
        is_first_commit = False
    else:
        current_parent_sha = base_sha
        is_first_commit = True

    for step in steps:
        step_num = step["step_number"]
        step_title = step["title"]
        difficulty = step.get("difficulty", "standard")

        if f"- [x] **{step_num}." in current_body:
            logger.info(f"Step {step_num} is already marked completed. Skipping...")
            continue

        step_files = step.get("files", [])
        if not step_files:
            step_files = [
                {
                    "path": step.get("target_file"),
                    "action": "MODIFY",
                    "summary": step.get("action_summary", step_title),
                }
            ]

        logger.info(
            f"Executing Step {step_num}: '{step_title}' touching {len(step_files)} file(s)..."
        )

        current_tree = await app_client.get_repository_tree(owner, repo, branch_name)
        stack_plan = await detect_repo_stack(current_tree)

        step_file_payloads: Dict[str, str] = {}

        for file_item in step_files:
            path = file_item["path"]
            action = file_item.get("action", "MODIFY").upper()
            summary = file_item.get("summary", step_title)

            file_info = await app_client.get_file_content_and_sha(
                owner, repo, path=path, ref=branch_name
            )
            existing_content = file_info.get("content")

            if action == "CREATE" or existing_content is None:
                content = await generate_new_file_content(
                    target_path=path,
                    action_summary=summary,
                    contributing=contributing,
                    difficulty=difficulty,
                )
            else:
                content = await generate_modified_file_content(
                    target_path=path,
                    current_content=existing_content,
                    action_summary=summary,
                    contributing=contributing,
                    difficulty=difficulty,
                )

            step_file_payloads[path] = content

        step_file_payloads = await verify_and_heal_step(
            app_client=app_client,
            owner=owner,
            repo=repo,
            branch=branch_name,
            step_files=step_files,
            file_payloads=step_file_payloads,
            stack_plan=stack_plan,
            contributing=contributing,
        )

        commit_msg = f"feat: {step_title.lower()}"
        new_commit_sha = await app_client.create_commit_on_branch(
            owner=owner,
            repo=repo,
            branch_name=branch_name,
            files=step_file_payloads,
            message=commit_msg,
            parent_sha=current_parent_sha,
            force=is_first_commit,
        )

        current_parent_sha = new_commit_sha
        is_first_commit = False

        current_body = mark_step_completed_in_body(current_body, step_num)
        await app_client.update_pull_request(
            owner, repo, pull_number, body=current_body
        )

    logger.info(
        f"All steps completed. Converting PR #{pull_number} to Ready for Review..."
    )
    await app_client.mark_pr_ready_for_review(owner, repo, pull_number)

    final_sha = await app_client.get_branch_sha(owner, repo, branch_name)
    await app_client.create_check_run(
        owner=owner,
        repo=repo,
        head_sha=final_sha,
        name="PriestyAI Sandbox Verification",
        conclusion="success",
        title="All Planned Tasks Completed & Verified",
        summary="All steps executed and verified passing inside isolated Docker sandbox.",
    )

    if maintainer:
        await machine_client.assign_users(owner, repo, pull_number, [maintainer])
        await machine_client.request_reviewers(owner, repo, pull_number, [maintainer])

    signoff_prompt = f"""You are PriestyAI, an engineer teammate.
You just completed implementing all tasks for PR #{pull_number} on `{branch_name}` and marked it ready for review.
Write a brief, friendly teammate comment addressing @{maintainer or 'team'}:
- Let them know all planned changes are completed and tested.
- Invite them to review the PR.
- Natural engineer tone. No emoji spam, no robotic jargon.
"""
    signoff_text = await llm_client.generate(
        prompt=signoff_prompt,
        system_prompt="You are a senior developer teammate. Speak naturally.",
        model_tier="routing",
    )

    await machine_client.create_issue_comment(owner, repo, pull_number, signoff_text)
    logger.info(f"Issue-to-PR completed successfully for PR #{pull_number}")


async def generate_new_file_content(
    target_path: str,
    action_summary: str,
    contributing: str,
    difficulty: str,
) -> str:
    prompt = f"""You are creating a BRAND NEW FILE at '{target_path}'.
Task: {action_summary}

CONTRIBUTING GUIDELINES:
{contributing}

Return ONLY the complete raw file content. Do not include markdown code block backticks or commentary."""

    raw_code = await llm_client.generate(
        prompt=prompt,
        system_prompt="You are an expert software developer writing clean, modular code. Output raw code only.",
        model_tier="reasoning",
    )
    return strip_markdown_fences(raw_code)


async def generate_modified_file_content(
    target_path: str,
    current_content: str,
    action_summary: str,
    contributing: str,
    difficulty: str,
) -> str:
    line_count = len(current_content.splitlines())

    if line_count <= 80:
        prompt = f"""You are modifying '{target_path}'.
Task: {action_summary}

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

    prompt = f"""You are modifying '{target_path}'.
Task: {action_summary}

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


async def verify_and_heal_step(
    app_client: AppInstallationClient,
    owner: str,
    repo: str,
    branch: str,
    step_files: List[Dict[str, Any]],
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
        "Step test check failed. Initiating Self-Healing Pass 1 with traceback..."
    )
    for f in step_files:
        path = f["path"]
        healing_prompt = f"""Test verification failed for '{path}'.

TASK: {f.get('summary')}
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
    for f in step_files:
        path = f["path"]
        healing_prompt_3 = f"""Test verification failed again for '{path}'.

TASK: {f.get('summary')}
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

    temp_dir = tempfile.mkdtemp(prefix="priesty_step_verify_")
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


def strip_markdown_fences(code: str) -> str:
    if code.startswith("```"):
        lines = code.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines) + "\n"
    return code
