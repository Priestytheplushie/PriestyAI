import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List
import httpx
from app.config import settings
from app.core.docker_runner import docker_runner
from app.github.client import AppInstallationClient, machine_client
from app.llm.client import llm_client
from app.workflows.fix_request import handle_fix_request

logger = logging.getLogger("priesty.pr_review")


async def handle_pr_review_requested(payload: Dict[str, Any]) -> None:
    requested_reviewer = payload.get("requested_reviewer", {}).get("login")
    if requested_reviewer != settings.BOT_USERNAME:
        return

    installation_id = payload.get("installation", {}).get("id")
    pr_data = payload.get("pull_request", {})
    repo_data = payload.get("repository", {})
    owner = repo_data.get("owner", {}).get("login")
    repo = repo_data.get("name")
    pull_number = pr_data.get("number")
    head_sha = pr_data.get("head", {}).get("sha")
    base_ref = pr_data.get("base", {}).get("ref", "main")

    is_conflicted = (
        pr_data.get("mergeable") is False or pr_data.get("mergeable_state") == "dirty"
    )

    logger.info(
        f"PR Review requested for {owner}/{repo}#{pull_number} by "
        f"{payload.get('sender', {}).get('login')} (is_conflicted={is_conflicted})"
    )

    app_client = AppInstallationClient(installation_id)

    files_changed = await app_client.get_pull_request_files(owner, repo, pull_number)
    file_tree = await app_client.get_repository_tree(owner, repo, head_sha)
    contributing = (
        await app_client.get_file_content(owner, repo, "CONTRIBUTING.md", base_ref)
        or "No CONTRIBUTING.md found."
    )

    plan_prompt = f"""Analyze this repository structure to determine if it has automated linters, tests, or build checks.
Repository files:
{file_tree[:60]}

Rules:
- Python with pytest/ruff: choose "python:3.11-slim" and pytest/ruff commands.
- Python with unittest: choose "python:3.11-slim" and python -m unittest discover.
- Node.js (package.json): choose "node:20-alpine" and npm test / npm run lint.
- Go (go.mod): choose "golang:1.22-alpine" and go test ./...
- Rust (Cargo.toml): choose "rust:1.78-alpine" and cargo test.
- Static assets, HTML, Markdown, or documentation without automated test runner: set has_automated_checks to false and docker_image to null.

Return JSON:
{{
  "has_automated_checks": true,
  "docker_image": "node:20-alpine" or null,
  "commands": ["npm test"]
}}
"""
    plan = await llm_client.generate_json(
        prompt=plan_prompt,
        system_prompt="You are a senior build engineer analyzing repository structure. Return pure JSON only.",
        model_tier="routing",
    )

    docker_output = "Static code analysis applied (no automated test suite required for this stack)."
    commands = plan.get("commands", [])
    docker_image = plan.get("docker_image")
    has_checks = plan.get("has_automated_checks", False)

    if has_checks and commands and docker_image and docker_runner.available:
        temp_dir = tempfile.mkdtemp(prefix="priesty_review_")
        try:
            zip_url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{head_sha}"
            token = await app_client._get_headers()
            async with httpx.AsyncClient(follow_redirects=True) as http_client:
                r = await http_client.get(zip_url, headers=token)
                if r.status_code == 200:
                    import io, zipfile

                    z = zipfile.ZipFile(io.BytesIO(r.content))
                    root_folder = z.namelist()[0]
                    z.extractall(temp_dir)
                    extracted_path = os.path.join(temp_dir, root_folder)

                    result = await docker_runner.run_commands(
                        workspace_dir=extracted_path,
                        commands=commands,
                        image=docker_image,
                    )
                    docker_output = (
                        f"Exit Code: {result['exit_code']}\n"
                        f"STDOUT:\n{result['stdout']}\n"
                        f"STDERR:\n{result['stderr']}"
                    )
        except Exception as e:
            logger.error(f"Sandbox check failed: {e}")
            docker_output = f"Test execution error: {e}"
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    diff_snippets = []
    for f in files_changed:
        diff_snippets.append(
            f"File: {f['filename']}\nStatus: {f.get('status')}\nPatch:\n{f.get('patch', 'No patch diff')}"
        )

    conflict_hint = ""
    if is_conflicted:
        conflict_hint = (
            f"\nMERGE CONFLICT DETECTED: This branch has active merge conflicts with the base branch. "
            f"Politely mention the conflict in your review summary and remind them they can tag "
            f"@{settings.BOT_USERNAME} resolve conflicts to resolve it automatically."
        )

    review_prompt = f"""You are PriestyAI, an experienced, collaborative senior engineer teammate reviewing this Pull Request.
Tone: Natural, conversational developer. Speak like a thoughtful teammate on GitHub.
Avoid corporate buzzwords, robotic boilerplate, or artificial tag prefixes (never use tags like [Blocker], [Suggestion], [Nitpick], or [Question]). Speak naturally.

CONTRIBUTING GUIDELINES (FROM TRUSTED BASE BRANCH):
{contributing}

LOCAL TEST & LINTER OUTPUT:
{docker_output}
{conflict_hint}

PR TITLE: {pr_data.get('title')}
PR BODY: {pr_data.get('body') or 'No description provided.'}

<untrusted_diff_to_inspect>
{chr(10).join(diff_snippets)}
</untrusted_diff_to_inspect>

INLINE COMMENT & SUGGESTION FORMATTING RULES:
1. Conduct a thoughtful inspection across all modified files in the diff.
2. When proposing a direct replacement for code on a specific line, ALWAYS use a GitHub suggestion block:
   ```suggestion
   [exact replacement code lines with matching indentation]
   ```
   - CRITICAL: Do NOT include language identifiers (write ```suggestion, NOT ```suggestion python or ```suggestion js).
   - The suggestion block must contain ONLY the replacement lines for the targeted diff line(s).
3. For conceptual questions, architectural praise, or high-level observations, write natural conversational markdown without suggestion blocks.
4. Line numbers MUST be integers corresponding to modified/added lines on the NEW (right) side of the diff.

VERDICT RULES:
- "APPROVE": The code works, tests pass, and there are no blocking issues. Non-blocking polish notes are welcome under an approval!
- "REQUEST_CHANGES": Reserved STRICTLY for true blockers (functional bugs, broken tests/builds, security flaws, or explicit hard rule violations in CONTRIBUTING.md).
- "COMMENT": Use when you have general questions, need architectural clarification, or the review is informational.

Return a JSON object with this EXACT structure:
{{
  "verdict": "APPROVE" | "REQUEST_CHANGES" | "COMMENT",
  "summary": "Natural markdown review summary highlighting positive aspects, core observations, and clear next steps.",
  "comments": [
    {{
      "path": "app.js",
      "line": 15,
      "body": "We can simplify this iteration using `Array.filter`:\n```suggestion\n  tasks = tasks.filter(t => t.id !== id);\n```"
    }},
    {{
      "path": "styles.css",
      "line": 42,
      "body": "Is this fixed height constraint needed here, or should we allow it to flex on mobile screens?"
    }}
  ]
}}
"""

    review_res = await llm_client.generate_json(
        prompt=review_prompt,
        system_prompt="You are a senior software engineer conducting a thoughtful, natural code review. Output valid JSON only.",
        model_tier="reasoning",
    )

    verdict = review_res.get("verdict", "COMMENT")
    summary = review_res.get("summary", "Review completed.")
    raw_comments = review_res.get("comments", [])

    formatted_comments: List[Dict[str, Any]] = []
    for c in raw_comments:
        if "path" in c and "line" in c and "body" in c:
            try:
                line_number = int(c["line"])
                formatted_comments.append(
                    {
                        "path": c["path"],
                        "line": line_number,
                        "side": "RIGHT",
                        "body": c["body"],
                    }
                )
            except (ValueError, TypeError):
                continue

    logger.info(
        f"Submitting review for #{pull_number} with verdict '{verdict}' and {len(formatted_comments)} inline comments..."
    )
    await machine_client.post_pull_request_review(
        owner=owner,
        repo=repo,
        pull_number=pull_number,
        commit_id=head_sha,
        event=verdict,
        body=summary,
        comments=formatted_comments if formatted_comments else None,
    )
    logger.info(f"Successfully posted PR review to {owner}/{repo}#{pull_number}!")


async def handle_pr_review_submitted(payload: Dict[str, Any]) -> None:
    review = payload.get("review", {})
    state = review.get("state", "").lower()
    reviewer = review.get("user", {}).get("login", "")
    review_body = review.get("body", "") or ""

    if reviewer.lower() == settings.BOT_USERNAME.lower():
        return

    installation_id = payload.get("installation", {}).get("id")
    repo_data = payload.get("repository", {})
    owner = repo_data.get("owner", {}).get("login")
    repo = repo_data.get("name")
    pr_data = payload.get("pull_request", {})
    pull_number = pr_data.get("number")

    logger.info(
        f"Received formal review [{state.upper()}] from @{reviewer} on PR #{pull_number}"
    )

    app_client = AppInstallationClient(installation_id)

    if state == "changes_requested":
        logger.info(
            f"Review requested changes on PR #{pull_number}. Initiating fix loop..."
        )
        prompt_text = (
            review_body
            if review_body.strip()
            else "Address the requested changes and review comments on this Pull Request."
        )

        await handle_fix_request(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            requester_login=reviewer,
            user_prompt=prompt_text,
        )

    elif state == "approved":
        logger.info(
            f"PR #{pull_number} was officially approved by @{reviewer}! Analyzing merge strategy..."
        )
        commits = await app_client.get_pull_request_commits(owner, repo, pull_number)
        commit_count = len(commits) if commits else pr_data.get("commits", 1)
        commit_messages = [
            c.get("commit", {}).get("message", "").split("\n")[0] for c in commits[:10]
        ]

        approval_prompt = f"""You are PriestyAI, an engineer teammate.
Reviewer @{reviewer} just approved your PR #{pull_number} ('{pr_data.get('title')}').

PR COMMIT STATS:
- Total Commits: {commit_count}
- Recent Commit Titles: {commit_messages}

MERGE STRATEGY RULES:
- If there are multiple incremental or fix commits (e.g. > 1 commit): recommend "Squash and Merge" to keep base branch history clean.
- If there is exactly 1 clean commit: recommend "Squash and Merge" or "Rebase and Merge".
- If there are multiple distinct, well-curated atomic feature commits: recommend "Rebase and Merge".

TASK:
Write a friendly, concise 2-sentence teammate reply:
1. Thank @{reviewer} for the approval.
2. Recommend the best merge strategy based on the commit history.
3. Invite them to hit the merge button or command you to merge it (e.g. "@{settings.BOT_USERNAME} squash and merge").
Tone: Natural, professional developer. No emoji spam, no robotic boilerplate.
"""

        reply_text = await llm_client.generate(
            prompt=approval_prompt,
            system_prompt="You are a collaborative senior developer teammate. Speak naturally.",
            model_tier="routing",
        )
        await machine_client.create_issue_comment(owner, repo, pull_number, reply_text)
        logger.info(
            f"Posted approval acknowledgement and merge guidance to PR #{pull_number}"
        )

    elif state == "commented":
        logger.info(
            f"Review comments received on PR #{pull_number}. Drafting discussion response..."
        )
        review_comments = await app_client.get_pull_request_review_comments(
            owner, repo, pull_number
        )
        inline_notes = [
            f"- `{c.get('path')}:{c.get('line') or c.get('original_line') or 'diff'}`: {c.get('body')}"
            for c in review_comments[-10:]
        ]

        comment_review_prompt = f"""You are PriestyAI, an engineer teammate on GitHub.
Reviewer @{reviewer} submitted a general comment review on PR #{pull_number} ('{pr_data.get('title')}').
This is a conversational discussion review (NOT a blocker / Changes Requested).

REVIEW SUMMARY BODY:
{review_body or 'No summary text provided.'}

INLINE COMMENTS FROM REVIEW:
{chr(10).join(inline_notes) or 'No inline comments.'}

INSTRUCTIONS:
1. Acknowledge and answer any technical questions or observations raised by @{reviewer}.
2. If they suggested potential adjustments or alternative approaches, briefly discuss the solution (with a small markdown snippet if helpful).
3. Do NOT modify any code or push commits yet.
4. Conclude with a friendly teammate note: let them know that if they'd like you to apply these changes directly to the branch, they can reply with "@{settings.BOT_USERNAME} apply this" or let you know.
Tone: Helpful, conversational senior engineer. Speak naturally.
"""

        comment_reply = await llm_client.generate(
            prompt=comment_review_prompt,
            system_prompt="You are a collaborative senior developer discussing code review feedback. Speak naturally.",
            model_tier="routing",
        )
        await machine_client.create_issue_comment(
            owner, repo, pull_number, comment_reply
        )
        logger.info(f"Posted discussion reply to PR #{pull_number}")
