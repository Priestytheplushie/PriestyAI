import logging
import re
import time
from typing import Any, Dict, List, Optional
from app.config import settings
from app.github.client import AppInstallationClient, machine_client
from app.github.state import extract_metadata
from app.llm.client import llm_client
from app.workflows.chatops import handle_chatops
from app.workflows.create_issue import handle_create_issue
from app.workflows.decompose_epic import handle_decompose_epic
from app.workflows.fix_request import handle_fix_request
from app.workflows.issue_to_pr import execute_approved_plan, handle_issue_assigned
from app.workflows.pr_triage import handle_pr_summarize_request
from app.workflows.resolve_conflicts import handle_resolve_conflicts
from app.workflows.run_tests import handle_run_tests

logger = logging.getLogger("priesty.mention_router")


class SpamGuard:
    def __init__(self) -> None:
        self._strikes: Dict[str, List[float]] = {}
        self._cooldowns: Dict[str, float] = {}

    def is_rate_limited(self, username: str) -> bool:
        now = time.time()
        if username in self._cooldowns and now < self._cooldowns[username]:
            return True
        return False

    def record_none_strike(self, username: str) -> bool:
        now = time.time()
        if username not in self._strikes:
            self._strikes[username] = []
        self._strikes[username] = [t for t in self._strikes[username] if now - t < 300]
        self._strikes[username].append(now)
        if len(self._strikes[username]) >= 3:
            logger.warning(
                f"User '{username}' triggered 3 NONE strikes. 10-minute cooldown applied."
            )
            self._cooldowns[username] = now + 600
            return True
        return False


spam_guard = SpamGuard()


async def handle_comment_created(payload: Dict[str, Any]) -> None:
    comment = payload.get("comment", {})
    body = comment.get("body", "")
    sender = payload.get("sender", {}).get("login", "")
    issue = payload.get("issue") or payload.get("pull_request", {})
    repo_data = payload.get("repository", {})
    owner = repo_data.get("owner", {}).get("login")
    repo = repo_data.get("name")
    issue_number = issue.get("number")
    is_pr = (
        "pull_request" in payload
        or "pull_request" in issue
        or "pull_request_url" in comment
    )
    installation_id = payload.get("installation", {}).get("id")

    if sender == settings.BOT_USERNAME:
        return

    if spam_guard.is_rate_limited(sender):
        logger.info(f"Ignoring comment from '{sender}' (active spam cooldown).")
        return

    bot_tag = f"@{settings.BOT_USERNAME}".lower()
    is_explicitly_tagged = bot_tag in body.lower()

    app_client = AppInstallationClient(installation_id)

    is_ai_draft_pr = False
    head_ref = None
    if is_pr:
        pr_data = await app_client.get_pull_request(owner, repo, issue_number)
        is_draft = pr_data.get("draft", False)
        has_meta = extract_metadata(pr_data.get("body", "")) is not None
        is_ai_draft_pr = is_draft and has_meta
        head_ref = pr_data.get("head", {}).get("ref")

    in_reply_to_id = comment.get("in_reply_to_id")
    is_review_comment = (
        "pull_request_review_id" in comment
        or in_reply_to_id is not None
        or "pull_request_url" in comment
    )

    is_inline_thread_reply = False
    if in_reply_to_id and not is_explicitly_tagged:
        review_comments = await app_client.get_pull_request_review_comments(
            owner, repo, issue_number
        )
        parent_comment = next(
            (c for c in review_comments if c.get("id") == in_reply_to_id), None
        )
        if (
            parent_comment
            and parent_comment.get("user", {}).get("login") == settings.BOT_USERNAME
        ):
            has_other_mentions = bool(
                re.search(
                    r"@(?!" + re.escape(settings.BOT_USERNAME) + r"\b)\w+",
                    body,
                    re.IGNORECASE,
                )
            )
            if not has_other_mentions:
                is_inline_thread_reply = True

    if not is_explicitly_tagged and not is_inline_thread_reply and not is_ai_draft_pr:
        return

    reply_target_comment_id: Optional[int] = None
    if is_review_comment:
        reply_target_comment_id = in_reply_to_id or comment.get("id")

    logger.info(
        f"Processing comment from '{sender}' on {owner}/{repo}#{issue_number} "
        f"(is_review_comment={is_review_comment}, reply_target={reply_target_comment_id})"
    )

    recent_comments = await app_client.get_issue_comments(
        owner, repo, issue_number, limit=8
    )
    transcript_lines = [
        f"{c.get('user', {}).get('login')}: \"{c.get('body', '')[:200]}\""
        for c in recent_comments
    ]

    full_context_text = f"{issue.get('title', '')} {body} " + " ".join(transcript_lines)
    referenced_context = await app_client.get_referenced_context(
        owner, repo, full_context_text, current_number=issue_number
    )

    router_prompt = f"""You are an intelligent intent router for a GitHub AI teammate named @{settings.BOT_USERNAME}.
Analyze the user's latest comment within the conversation context.

USER COMMENT:
\"{body}\"

CONVERSATION TRANSCRIPT:
{chr(10).join(transcript_lines) or 'No prior comments'}

{referenced_context}

CONTEXT:
Is Pull Request: {is_pr}
Is AI Draft PR Awaiting Approval: {is_ai_draft_pr}
Title: {issue.get('title')}

POSSIBLE INTENTS:
- PLAN_APPROVED: (Only if Is AI Draft PR is True) The maintainer is approving the draft plan (e.g. "approved", "LGTM", "looks good proceed", "go ahead").
- START_ISSUE: (On an Issue) The user is commanding the bot to begin working on, implement, or plan this issue (e.g. "work on this", "start working on this", "implement this", "take this issue", "create the plan").
- CREATE_SUB_ISSUES: (On an Issue) The user is asking to decompose, split up, or create sub-issues / sub-tasks for an Epic (e.g. "create sub-issues", "break this down", "split this up", "open the sub-tasks").
- CHATOPS: Administrative commands like close, reopen, merge, assign/unassign users, add/remove labels, request reviews, lock/unlock conversation.
- RESOLVE_CONFLICTS: The user is explicitly instructing the bot to merge/fix merge conflicts with the base branch (e.g. "resolve conflicts", "fix merge conflicts with main").
- CREATE_ISSUE: The user is asking to create, open, or track a single new standalone issue.
- FIX_REQUEST: (ONLY FOR PULL REQUESTS) The user wants code modified or commits pushed to this PR branch.
- RUN_TESTS: The user wants tests/linters executed locally.
- DEBUG_ANALYSIS: The user is asking an exploratory question ("How do I do X?", "Why is there a conflict?"), discussing a bug, or seeking technical advice without commanding a direct Git action.
- GENERAL_QA: The user is asking a general question.
- SUMMARIZE: The user wants a summary of the PR or issue.
- NONE: Spam, troll, insults, or meaningless comment.

Return JSON:
{{
  "intent": "PLAN_APPROVED" | "START_ISSUE" | "CREATE_SUB_ISSUES" | "CHATOPS" | "RESOLVE_CONFLICTS" | "CREATE_ISSUE" | "FIX_REQUEST" | "RUN_TESTS" | "DEBUG_ANALYSIS" | "GENERAL_QA" | "SUMMARIZE" | "NONE",
  "reason": "short explanation"
}}
"""

    route_res = await llm_client.generate_json(
        prompt=router_prompt,
        system_prompt="You are a fast, accurate intent router. Output valid JSON only.",
        model_tier="routing",
    )

    intent = route_res.get("intent", "DEBUG_ANALYSIS" if not is_pr else "GENERAL_QA")
    logger.info(f"Intent classified as: '{intent}' (reason: {route_res.get('reason')})")

    if intent == "PLAN_APPROVED" and is_ai_draft_pr:
        ack_prompt = f"""You are PriestyAI, an engineer teammate.
The maintainer (@{sender}) just approved your implementation plan on Draft PR #{issue_number}.
Their message: "{body}"

Write a brief, unique, 1-sentence teammate acknowledgement confirming you are getting right to work on the steps.
Tone: Natural, conversational developer. No emoji spam, no robotic phrases.
"""
        ack_text = await llm_client.generate(
            prompt=ack_prompt,
            system_prompt="You are a senior developer teammate. Speak naturally.",
            model_tier="routing",
        )

        await machine_client.create_issue_comment(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            body=ack_text,
        )

        await execute_approved_plan(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            pull_number=issue_number,
        )

    elif intent == "START_ISSUE" and not is_pr:
        logger.info(
            f"Direct start command received on Issue #{issue_number}. Initiating issue planning..."
        )

        synthetic_payload = {
            "installation": {"id": installation_id},
            "issue": issue,
            "repository": repo_data,
            "sender": {"login": sender},
            "assignee": {"login": settings.BOT_USERNAME},
        }
        await handle_issue_assigned(synthetic_payload)

    elif intent == "CREATE_SUB_ISSUES" and not is_pr:
        await handle_decompose_epic(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            parent_issue_number=issue_number,
            requester_login=sender,
            user_prompt=body,
        )

    elif intent == "CHATOPS":
        await handle_chatops(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            issue_or_pr_number=issue_number,
            is_pr=is_pr,
            requester_login=sender,
            user_prompt=body,
            reply_to_comment_id=reply_target_comment_id,
        )

    elif intent == "RESOLVE_CONFLICTS":
        if not is_pr:
            await machine_client.create_issue_comment(
                owner=owner,
                repo=repo,
                issue_number=issue_number,
                body="Merge conflicts can only be resolved on Pull Requests, not Issues.",
            )
            return

        await handle_resolve_conflicts(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            pull_number=issue_number,
            requester_login=sender,
            user_prompt=body,
        )

    elif intent == "CREATE_ISSUE":
        await handle_create_issue(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            current_issue_or_pr_number=issue_number,
            requester_login=sender,
            user_prompt=body,
        )

    elif intent == "FIX_REQUEST" and is_pr:
        await handle_fix_request(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            pull_number=issue_number,
            requester_login=sender,
            user_prompt=body,
            reply_to_comment_id=reply_target_comment_id,
        )

    elif intent == "RUN_TESTS":
        if not is_pr:
            await machine_client.create_issue_comment(
                owner=owner,
                repo=repo,
                issue_number=issue_number,
                body="Test execution can only be run on Pull Request branches.",
            )
            return

        await handle_run_tests(
            installation_id=installation_id,
            owner=owner,
            repo=repo,
            pull_number=issue_number,
            requester_login=sender,
            user_prompt=body,
        )

    elif intent == "SUMMARIZE":
        if is_pr:
            await handle_pr_summarize_request(
                installation_id=installation_id,
                owner=owner,
                repo=repo,
                pull_number=issue_number,
            )
        else:
            await machine_client.create_issue_comment(
                owner=owner,
                repo=repo,
                issue_number=issue_number,
                body=f"Summary for Issue #{issue_number}: {issue.get('title')}\n\n{issue.get('body')}",
            )

    elif intent in ("GENERAL_QA", "DEBUG_ANALYSIS") or (
        intent == "FIX_REQUEST" and not is_pr
    ):
        target_ref = head_ref
        if not target_ref:
            default_branch_info = await app_client.get_default_branch_sha(owner, repo)
            target_ref = default_branch_info.get("branch", "main")
            target_sha = default_branch_info.get("sha")
        else:
            target_sha = await app_client.get_branch_sha(owner, repo, target_ref)

        file_tree = await app_client.get_repository_tree(owner, repo, target_sha)

        file_select_prompt = f"""You are a precise code locator for a GitHub repository.
Analyze the user's issue, comments, and query to identify the 1 to 3 most relevant files in the repository tree to inspect.

ISSUE TITLE: {issue.get('title')}
USER QUERY: "{body}"
RECENT COMMENTS:
{chr(10).join(transcript_lines[-3:]) or 'None'}

REPOSITORY FILES:
{file_tree}

Return JSON with up to 3 exact file paths from the list:
{{
  "selected_files": ["src/components/Dropdown.tsx", "src/styles.css"]
}}
"""
        selected_files_res = await llm_client.generate_json(
            prompt=file_select_prompt,
            system_prompt="You are a precise file locator. Return valid JSON only.",
            model_tier="routing",
        )
        target_files = selected_files_res.get("selected_files", [])

        if not target_files:
            common_entrypoints = [
                "index.html",
                "src/App.tsx",
                "src/App.jsx",
                "src/App.vue",
                "app.js",
                "script.js",
                "src/index.js",
                "src/main.ts",
                "src/main.py",
            ]
            target_files = [f for f in common_entrypoints if f in file_tree][:2]

        loaded_code_blocks = []
        for path in target_files:
            file_content = await app_client.get_file_content(
                owner, repo, path, target_ref
            )
            if file_content:
                loaded_code_blocks.append(
                    f"### File: `{path}`\n```\n{file_content[:5000]}\n```"
                )

        code_context = (
            "\n\n".join(loaded_code_blocks)
            if loaded_code_blocks
            else "No specific code files found in repository."
        )

        issue_implementation_note = ""
        if not is_pr:
            issue_implementation_note = (
                "\n\n*Note to model: At the very end of your response, add a 1-sentence friendly peer note: "
                '"If you want me to write and test the code changes on a branch, feel free to assign me to this issue!"*'
            )

        answer_prompt = f"""You are PriestyAI, an engineer teammate working directly on this codebase.
Answer @{sender}'s inquiry accurately by referencing the ACTUAL repository files and code provided below.

RULES:
1. Reference exact file names and actual variable/function/handler names from the provided code.
2. Directly address their observation or question with clear, concrete explanations.
3. Tone: Direct, helpful peer engineer. Speak naturally.
{issue_implementation_note}

REPOSITORY FILES IN REPO:
{file_tree[:40]}

ACTUAL FILE CONTENTS FROM REPO:
{code_context}

CONVERSATION TRANSCRIPT:
{chr(10).join(transcript_lines)}

{referenced_context}

LATEST USER COMMENT FROM @{sender}:
\"{body}\"

Title: {issue.get('title')}
"""
        response_text = await llm_client.generate(
            prompt=answer_prompt,
            system_prompt="You are a senior developer teammate analyzing repository source code. Cite exact files, components, and functions.",
            model_tier="reasoning",
        )

        if reply_target_comment_id:
            await machine_client.reply_to_review_comment(
                owner=owner,
                repo=repo,
                pull_number=issue_number,
                comment_id=reply_target_comment_id,
                body=response_text,
            )
        else:
            await machine_client.create_issue_comment(
                owner=owner, repo=repo, issue_number=issue_number, body=response_text
            )

    elif intent == "NONE":
        spam_guard.record_none_strike(sender)
        logger.info(f"Intent NONE recorded for '{sender}'.")
