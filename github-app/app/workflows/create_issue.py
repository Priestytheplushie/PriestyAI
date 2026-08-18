import logging
from typing import Any, Dict, List
from app.config import settings
from app.github.client import AppInstallationClient, machine_client
from app.llm.client import llm_client

logger = logging.getLogger("priesty.create_issue")


async def handle_create_issue(
    installation_id: int,
    owner: str,
    repo: str,
    current_issue_or_pr_number: int,
    requester_login: str,
    user_prompt: str,
) -> None:
    """
    Creates a new Issue based on conversation context or direct request, with natural teammate copy.
    """
    app_client = AppInstallationClient(installation_id)
    available_labels = await app_client.get_repo_labels(owner, repo)

    recent_comments = await app_client.get_issue_comments(
        owner, repo, current_issue_or_pr_number, limit=6
    )
    transcript = "\n".join(
        [
            f"{c.get('user', {}).get('login')}: {c.get('body', '')[:150]}"
            for c in recent_comments
        ]
    )

    prompt = f"""You are PriestyAI, an engineer teammate.
@{requester_login} asked you to spin off / create an issue on this repository.
Tone: Natural, professional engineer. No corporate fluff, no emoji spam.

USER PROMPT:
\"{user_prompt}\"

THREAD TRANSCRIPT (FOR CONTEXT):
{transcript}

AVAILABLE REPO LABELS:
{available_labels}

TASK:
1. Write a clean, descriptive issue title (e.g. feat: add Redis caching backend).
2. Write a clear markdown issue body detailing background and goals.
3. Select 1-2 appropriate labels strictly from AVAILABLE REPO LABELS.
4. Write a brief teammate comment to reply back in the current thread (e.g. "I've created #ISSUE_NUMBER to track this: #ISSUE_NUMBER"). Use '#ISSUE_NUMBER' as a placeholder.

Return JSON:
{{
  "title": "feat: <title>",
  "body": "Markdown issue description...",
  "selected_labels": ["enhancement"],
  "reply_text": "I've opened issue #ISSUE_NUMBER to track this follow-up work."
}}
"""

    res = await llm_client.generate_json(
        prompt=prompt,
        system_prompt="You are a senior developer creating clear issues. Output valid JSON only.",
        model_tier="routing",
    )

    title = res.get("title", f"Task from #{current_issue_or_pr_number}")
    body = res.get(
        "body",
        f"Spun off from discussion in #{current_issue_or_pr_number} by @{requester_login}",
    )
    selected_labels = res.get("selected_labels", [])
    valid_labels = [l for l in selected_labels if l in available_labels]
    reply_template = res.get(
        "reply_text", "I've opened issue #ISSUE_NUMBER to track this."
    )

    new_issue = await machine_client.create_issue(
        owner=owner,
        repo=repo,
        title=title,
        body=body,
        labels=valid_labels if valid_labels else None,
    )
    new_issue_number = new_issue["number"]

    reply_text = reply_template.replace("#ISSUE_NUMBER", f"#{new_issue_number}")
    await machine_client.create_issue_comment(
        owner, repo, current_issue_or_pr_number, reply_text
    )
    logger.info(
        f"Successfully created Issue #{new_issue_number} from #{current_issue_or_pr_number}"
    )
