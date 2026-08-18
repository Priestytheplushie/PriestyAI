import logging
from typing import Any, Dict, List
import httpx
from app.config import settings
from app.github.client import AppInstallationClient, machine_client
from app.llm.client import llm_client

logger = logging.getLogger("priesty.issue_triage")


async def handle_issue_opened(payload: Dict[str, Any]) -> None:
    """Triggered on webhook 'issues.opened'."""
    issue_data = payload.get("issue", {})
    sender = payload.get("sender", {}).get("login")

    if sender == settings.BOT_USERNAME or "pull_request" in issue_data:
        return

    installation_id = payload.get("installation", {}).get("id")
    repo_data = payload.get("repository", {})
    owner = repo_data.get("owner", {}).get("login")
    repo = repo_data.get("name")
    issue_number = issue_data.get("number")

    logger.info(
        f"Issue #{issue_number} opened in {owner}/{repo} by {sender}. Triaging..."
    )

    app_client = AppInstallationClient(installation_id)

    available_labels = await app_client.get_repo_labels(owner, repo)

    existing_issues_summary: List[str] = []
    headers = await app_client._get_headers()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/issues",
            headers=headers,
            params={"state": "all", "per_page": 30},
        )
        if resp.status_code == 200:
            for item in resp.json():
                if item.get("number") != issue_number and "pull_request" not in item:
                    existing_issues_summary.append(
                        f"#{item.get('number')}: {item.get('title')} ({item.get('state')}) - {item.get('body', '')[:100]}"
                    )

    triage_prompt = f"""You are PriestyAI, an engineer triaging a new GitHub Issue.
Tone: Helpful, polite teammate. Be cautious with duplicate detection—only flag if it is clearly describing the exact same underlying task or bug (prefer false negatives over false positives).

NEW ISSUE:
Number: #{issue_number}
Title: {issue_data.get('title')}
Author: {sender}
Body:
{issue_data.get('body') or 'No description provided.'}

RECENT REPOSITORY ISSUES:
{chr(10).join(existing_issues_summary[:20]) or 'None'}

AVAILABLE LABELS:
{available_labels}

TASK:
Determine:
1. Is this issue a duplicate of an existing issue? If yes, specify the duplicate issue number.
2. Is this issue too vague or missing critical details (e.g. no reproduction steps, no logs for a bug)? If yes, formulate 1-3 targeted questions.
3. Select 1-2 appropriate labels strictly from AVAILABLE LABELS.

Return JSON:
{{
  "is_duplicate": false,
  "duplicate_issue_number": null,
  "needs_info": false,
  "followup_message": "Friendly markdown comment to post if needs_info or is_duplicate is true",
  "selected_labels": ["bug"]
}}
"""

    triage_res = await llm_client.generate_json(
        prompt=triage_prompt,
        system_prompt="You are a senior engineer triaging issues. Output valid JSON only.",
        model_tier="routing",
    )

    selected_labels = triage_res.get("selected_labels", [])
    valid_labels = [l for l in selected_labels if l in available_labels]
    is_duplicate = triage_res.get("is_duplicate", False)
    needs_info = triage_res.get("needs_info", False)
    followup = triage_res.get("followup_message")

    if valid_labels:
        logger.info(f"Applying labels {valid_labels} to Issue #{issue_number}")
        await app_client.add_labels(owner, repo, issue_number, valid_labels)

    if (is_duplicate or needs_info) and followup:
        logger.info(f"Posting triage response to Issue #{issue_number}...")
        await machine_client.create_issue_comment(owner, repo, issue_number, followup)

    logger.info(f"Issue Triage completed for {owner}/{repo}#{issue_number}")
