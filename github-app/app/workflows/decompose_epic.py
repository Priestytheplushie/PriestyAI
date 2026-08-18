import logging
from typing import Any, Dict, List, Optional
from app.config import settings
from app.github.client import AppInstallationClient, machine_client
from app.llm.client import llm_client

logger = logging.getLogger("priesty.decompose_epic")


async def handle_decompose_epic(
    installation_id: int,
    owner: str,
    repo: str,
    parent_issue_number: int,
    requester_login: str,
    user_prompt: str,
) -> None:
    app_client = AppInstallationClient(installation_id)
    parent_issue = await app_client.get_issue_details(owner, repo, parent_issue_number)
    available_labels = await app_client.get_repo_labels(owner, repo)
    recent_comments = await app_client.get_issue_comments(
        owner, repo, parent_issue_number, limit=10
    )

    transcript = "\n".join(
        [
            f"- @{c.get('user', {}).get('login')}: {c.get('body', '')[:200]}"
            for c in recent_comments
        ]
    )

    logger.info(
        f"Decomposing Epic Issue #{parent_issue_number} in {owner}/{repo} requested by @{requester_login}..."
    )

    decompose_prompt = f"""You are PriestyAI, a senior tech lead and engineer teammate.
Maintainer @{requester_login} requested to decompose this broad Epic Issue into 2 to 4 focused, atomic Sub-Issues.

PARENT ISSUE #{parent_issue_number}:
Title: {parent_issue.get('title')}
Body:
{parent_issue.get('body') or 'No description provided.'}

DISCUSSION TRANSCRIPT:
{transcript or 'None'}

AVAILABLE REPO LABELS:
{available_labels}

INSTRUCTIONS:
1. Break down the parent issue into 2 to 4 logical, sequential, atomic Sub-Issues (e.g. Phase 1: Models, Phase 2: API, Phase 3: UI).
2. For each sub-issue:
   - Provide a conventional title (e.g. "feat(storage): implement SQLite persistence for TTLCache").
   - Provide a clear markdown body detailing the scope and tasks. Include "Part of #{parent_issue_number}" in the description.
   - Pick 1-2 relevant labels strictly from AVAILABLE REPO LABELS.
3. Write a friendly, natural teammate intro and outro for the reply comment on the parent issue.
   - In the outro, mention that they can assign you to Phase 1 or comment "@PriestyAI work on #<PHASE_1_NUM>" whenever they want to begin.

Return JSON:
{{
  "intro": "I've broken this epic down into the following tracking sub-issues:",
  "sub_issues": [
    {{
      "title": "feat(phase-1): <title>",
      "body": "Detailed scope and tasks...\\n\\nPart of #{parent_issue_number}",
      "selected_labels": ["enhancement"]
    }}
  ],
  "outro": "When you're ready to start Phase 1, assign me or comment @{settings.BOT_USERNAME} work on Phase 1!"
}}
"""

    res = await llm_client.generate_json(
        prompt=decompose_prompt,
        system_prompt="You are a senior tech lead decomposing software epics. Output valid JSON only.",
        model_tier="reasoning",
    )

    intro_text = res.get(
        "intro", "I've created the following sub-issues to track this epic:"
    )
    sub_issue_specs = res.get("sub_issues", [])
    outro_text = res.get(
        "outro", "Let me know when you'd like me to start on the first phase!"
    )

    if not sub_issue_specs:
        await machine_client.create_issue_comment(
            owner=owner,
            repo=repo,
            issue_number=parent_issue_number,
            body="I looked over the issue, but wasn't sure how to cleanly separate it into sub-tasks. Could you clarify the milestones?",
        )
        return

    created_sub_issue_lines: List[str] = []

    for spec in sub_issue_specs:
        title = spec.get("title", f"Sub-task for #{parent_issue_number}")
        body = spec.get("body", f"Part of #{parent_issue_number}")
        selected_labels = [
            l for l in spec.get("selected_labels", []) if l in available_labels
        ]

        new_issue = await machine_client.create_issue(
            owner=owner,
            repo=repo,
            title=title,
            body=body,
            labels=selected_labels if selected_labels else None,
        )
        new_issue_number = new_issue["number"]
        new_issue_id = new_issue["id"]

        try:
            await app_client.add_sub_issue(
                owner, repo, parent_issue_number, new_issue_id
            )
        except Exception as e:
            logger.debug(f"Native sub-issue attachment returned: {e}")

        created_sub_issue_lines.append(f"- #{new_issue_number} `{title}`")
        logger.info(
            f"Created and linked Sub-Issue #{new_issue_number} to Parent #{parent_issue_number}"
        )

    try:
        await machine_client.remove_assignees(
            owner, repo, parent_issue_number, [settings.BOT_USERNAME]
        )
        logger.info(
            f"Unassigned {settings.BOT_USERNAME} from parent Epic #{parent_issue_number}"
        )
    except Exception as e:
        logger.debug(f"Could not unassign bot from parent epic: {e}")

    comment_body = (
        f"{intro_text}\n\n" + "\n".join(created_sub_issue_lines) + f"\n\n{outro_text}"
    )

    await machine_client.create_issue_comment(
        owner=owner, repo=repo, issue_number=parent_issue_number, body=comment_body
    )
    logger.info(
        f"Successfully decomposed Epic #{parent_issue_number} into {len(sub_issue_specs)} sub-issues."
    )
