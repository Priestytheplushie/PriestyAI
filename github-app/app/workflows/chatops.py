import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
import httpx
from app.config import settings
from app.github.client import AppInstallationClient, machine_client
from app.llm.client import llm_client

logger = logging.getLogger("priesty.chatops")


async def handle_chatops(
    installation_id: int,
    owner: str,
    repo: str,
    issue_or_pr_number: int,
    is_pr: bool,
    requester_login: str,
    user_prompt: str,
    reply_to_comment_id: Optional[int] = None,
) -> None:
    """
    Parses and executes multi-action ChatOps commands with caller permission validation.
    Supports target issue assignment (e.g. "work on #11" or "take this issue").
    """
    app_client = AppInstallationClient(installation_id)

    author_login = ""
    if is_pr:
        item_data = await app_client.get_pull_request(owner, repo, issue_or_pr_number)
        author_login = item_data.get("user", {}).get("login", "")
    else:
        item_data = await app_client.get_issue_details(owner, repo, issue_or_pr_number)
        author_login = item_data.get("user", {}).get("login", "")

    caller_perm = await app_client.get_user_permission(owner, repo, requester_login)
    is_maintainer = caller_perm in ("admin", "write", "maintain")
    is_author = requester_login.lower() == author_login.lower()
    available_labels = await app_client.get_repo_labels(owner, repo)

    logger.info(
        f"ChatOps requested by '{requester_login}' on {owner}/{repo}#{issue_or_pr_number} "
        f"(is_maintainer={is_maintainer}, is_author={is_author})"
    )

    parse_prompt = f"""You are a precise ChatOps action parser for GitHub.
Analyze the user command and extract ALL requested actions into a list.

USER COMMAND:
\"{user_prompt}\"

CONTEXT:
Is Pull Request: {is_pr}
Current Issue/PR Number: #{issue_or_pr_number}
Available Labels: {available_labels}
Bot Username: {settings.BOT_USERNAME}
Requester: {requester_login}

SUPPORTED ACTION TYPES:
- ASSIGN: Assign one or more usernames (e.g. "assign @alice", "assign yourself", "work on this", "start #11", "take on #12")
- UNASSIGN: Remove assignees (e.g. "unassign @bob", "unassign yourself")
- ADD_LABELS: Add labels strictly matching available labels
- REMOVE_LABELS: Remove existing labels
- CLOSE: Close the issue/PR (state_reason: "completed" | "not_planned" | null)
- REOPEN: Reopen the issue/PR
- MERGE: Merge the PR (merge_method: "merge" | "squash" | "rebase")
- MARK_READY: Mark draft PR as ready for review
- CONVERT_DRAFT: Convert PR back to draft
- REQUEST_REVIEWERS: Request reviews from users
- LOCK: Lock the thread
- UNLOCK: Unlock the thread

SPECIAL ASSIGN RULES:
- If user says "work on this", "start this", "take this issue", or "assign yourself", resolve to action_type: "ASSIGN", users: ["{settings.BOT_USERNAME}"], target_number: {issue_or_pr_number}.
- If user says "work on #11", "start #11", or "take on Phase 1 (#11)", resolve to action_type: "ASSIGN", users: ["{settings.BOT_USERNAME}"], target_number: 11.
- If user says "assign me", resolve to users: ["{requester_login}"].

Return JSON:
{{
  "actions": [
    {{
      "action_type": "ASSIGN" | "UNASSIGN" | "ADD_LABELS" | "REMOVE_LABELS" | "CLOSE" | "REOPEN" | "MERGE" | "MARK_READY" | "CONVERT_DRAFT" | "REQUEST_REVIEWERS" | "LOCK" | "UNLOCK",
      "users": ["username1"],
      "target_number": {issue_or_pr_number},
      "labels": ["label1"],
      "merge_method": "squash" | "merge" | "rebase",
      "state_reason": "completed" | "not_planned",
      "lock_reason": "resolved"
    }}
  ]
}}
"""

    parsed = await llm_client.generate_json(
        prompt=parse_prompt,
        system_prompt="You are a fast, accurate ChatOps action parser. Return valid JSON only.",
        model_tier="routing",
    )

    actions = parsed.get("actions", [])
    if not actions:
        fallback_msg = "I didn't recognize any specific action in that command. Let me know what you'd like me to do!"
        await _send_reply(
            owner, repo, issue_or_pr_number, fallback_msg, reply_to_comment_id
        )
        return

    executed_summaries: List[str] = []
    denied_summaries: List[str] = []

    for act in actions:
        action_type = act.get("action_type")
        target_num = act.get("target_number") or issue_or_pr_number

        if action_type == "ASSIGN":
            raw_users = [u.lstrip("@") for u in act.get("users", []) if u]
            can_assign_all = is_maintainer
            bot_only_allowed = is_author or is_maintainer

            candidates = []
            for u in raw_users:
                if u.lower() == settings.BOT_USERNAME.lower() and bot_only_allowed:
                    candidates.append(settings.BOT_USERNAME)
                elif u.lower() == requester_login.lower() and (
                    is_author or is_maintainer
                ):
                    candidates.append(requester_login)
                elif can_assign_all:
                    candidates.append(u)
                else:
                    denied_summaries.append(
                        f"assigning @{u} (requires repository maintainer permissions)"
                    )

            if candidates:
                try:
                    resp_data = await machine_client.assign_users(
                        owner, repo, target_num, candidates
                    )
                    assigned_logins = [
                        a.get("login", "").lower()
                        for a in resp_data.get("assignees", [])
                    ]

                    actually_assigned = [
                        c for c in candidates if c.lower() in assigned_logins
                    ]
                    if actually_assigned:
                        target_str = (
                            f" on #{target_num}"
                            if target_num != issue_or_pr_number
                            else ""
                        )
                        executed_summaries.append(
                            f"Assigned {', '.join(['@' + u for u in actually_assigned])}{target_str}"
                        )
                except Exception as e:
                    logger.error(f"Error assigning users: {e}")
                    denied_summaries.append(
                        f"assigning {', '.join(['@' + u for u in candidates])}"
                    )

        elif action_type == "UNASSIGN":
            raw_users = [u.lstrip("@") for u in act.get("users", []) if u]
            valid_unassignees = [
                u
                for u in raw_users
                if u.lower() == settings.BOT_USERNAME.lower()
                or u.lower() == requester_login.lower()
                or is_maintainer
            ]
            if valid_unassignees:
                try:
                    await machine_client.remove_assignees(
                        owner, repo, target_num, valid_unassignees
                    )
                    executed_summaries.append(
                        f"Unassigned {', '.join(['@' + u for u in valid_unassignees])}"
                    )
                except Exception as e:
                    logger.error(f"Error unassigning users: {e}")

        elif action_type == "ADD_LABELS":
            labels_to_add = [l for l in act.get("labels", []) if l in available_labels]
            if is_maintainer or is_author:
                if labels_to_add:
                    try:
                        await app_client.add_labels(
                            owner, repo, target_num, labels_to_add
                        )
                        executed_summaries.append(
                            f"Added labels: {', '.join([f'`{l}`' for l in labels_to_add])}"
                        )
                    except Exception as e:
                        logger.error(f"Error adding labels: {e}")
            else:
                denied_summaries.append(
                    "adding labels (requires maintainer permissions)"
                )

        elif action_type == "REMOVE_LABELS":
            labels_to_remove = act.get("labels", [])
            if is_maintainer:
                for l in labels_to_remove:
                    try:
                        await app_client.remove_label(owner, repo, target_num, l)
                    except Exception:
                        pass
                if labels_to_remove:
                    executed_summaries.append(
                        f"Removed labels: {', '.join([f'`{l}`' for l in labels_to_remove])}"
                    )
            else:
                denied_summaries.append(
                    "removing labels (requires maintainer permissions)"
                )

        elif action_type == "CLOSE":
            if is_maintainer or is_author:
                reason = act.get("state_reason")
                try:
                    await app_client.set_issue_state(
                        owner, repo, target_num, state="closed", state_reason=reason
                    )
                    reason_str = f" as {reason}" if reason else ""
                    executed_summaries.append(f"Closed #{target_num}{reason_str}")
                except Exception as e:
                    logger.error(f"Error closing item: {e}")
            else:
                denied_summaries.append(
                    f"closing #{target_num} (requires author or maintainer permissions)"
                )

        elif action_type == "REOPEN":
            if is_maintainer or is_author:
                try:
                    await app_client.set_issue_state(
                        owner, repo, target_num, state="open"
                    )
                    executed_summaries.append(f"Reopened #{target_num}")
                except Exception as e:
                    logger.error(f"Error reopening item: {e}")
            else:
                denied_summaries.append(
                    f"reopening #{target_num} (requires author or maintainer permissions)"
                )

        elif action_type == "MARK_READY":
            if is_maintainer or is_author:
                try:
                    await app_client.mark_pr_ready_for_review(owner, repo, target_num)
                    executed_summaries.append(
                        f"Marked PR #{target_num} as ready for review"
                    )
                except Exception as e:
                    logger.error(f"Error marking ready: {e}")

        elif action_type == "MERGE":
            if not is_pr:
                denied_summaries.append("merging (can only merge Pull Requests)")
            elif is_maintainer:
                method = act.get("merge_method", "merge")
                try:
                    await app_client.merge_pull_request(
                        owner, repo, target_num, merge_method=method
                    )
                    executed_summaries.append(
                        f"Merged PR #{target_num} using `{method}`"
                    )
                except Exception as e:
                    logger.error(f"Merge failed: {e}")
                    denied_summaries.append(
                        f"merging PR #{target_num} (blocked by checks or conflicts)"
                    )
            else:
                denied_summaries.append("merging PRs (requires maintainer permissions)")

        elif action_type == "LOCK":
            if is_maintainer:
                reason = act.get("lock_reason", "resolved")
                try:
                    await app_client.lock_issue(
                        owner, repo, target_num, lock_reason=reason
                    )
                    executed_summaries.append(
                        f"Locked conversation ({reason or 'resolved'})"
                    )
                except Exception as e:
                    logger.error(f"Error locking: {e}")
            else:
                denied_summaries.append(
                    "locking threads (requires maintainer permissions)"
                )

        elif action_type == "UNLOCK":
            if is_maintainer:
                try:
                    await app_client.unlock_issue(owner, repo, target_num)
                    executed_summaries.append("Unlocked conversation")
                except Exception as e:
                    logger.error(f"Error unlocking: {e}")
            else:
                denied_summaries.append(
                    "unlocking threads (requires maintainer permissions)"
                )

    confirm_prompt = f"""You are PriestyAI, an engineer teammate.
A teammate (@{requester_login}) gave you this command: "{user_prompt}".

SUCCESSFULLY EXECUTED:
{executed_summaries or 'None'}

FAILED / DENIED / RESTRICTED:
{denied_summaries or 'None'}

Write a friendly, concise 1-2 sentence teammate reply:
- Confirm what you completed successfully.
- If any action was denied, explain politely.
- Natural engineer tone. No emoji spam, no robotic boilerplate.
"""

    reply_text = await llm_client.generate(
        prompt=confirm_prompt,
        system_prompt="You are a collaborative senior developer teammate. Speak naturally.",
        model_tier="routing",
    )

    await _send_reply(owner, repo, issue_or_pr_number, reply_text, reply_to_comment_id)
    logger.info(f"ChatOps completed for {owner}/{repo}#{issue_or_pr_number}")


async def _send_reply(
    owner: str,
    repo: str,
    issue_number: int,
    body: str,
    reply_to_comment_id: Optional[int],
) -> None:
    if reply_to_comment_id:
        await machine_client.reply_to_review_comment(
            owner=owner,
            repo=repo,
            pull_number=issue_number,
            comment_id=reply_to_comment_id,
            body=body,
        )
    else:
        await machine_client.create_issue_comment(
            owner=owner, repo=repo, issue_number=issue_number, body=body
        )
