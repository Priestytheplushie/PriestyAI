import logging
from typing import Any, Dict, List
from app.config import settings
from app.github.client import AppInstallationClient, machine_client
from app.llm.client import llm_client

logger = logging.getLogger("priesty.pr_triage")


async def handle_pr_opened(payload: Dict[str, Any]) -> None:
    pr_data = payload.get("pull_request", {})
    sender = payload.get("sender", {}).get("login")

    if sender == settings.BOT_USERNAME:
        return

    installation_id = payload.get("installation", {}).get("id")
    repo_data = payload.get("repository", {})
    owner = repo_data.get("owner", {}).get("login")
    repo = repo_data.get("name")
    pull_number = pr_data.get("number")

    logger.info(
        f"PR #{pull_number} opened in {owner}/{repo} by {sender}. Initiating triage..."
    )
    await execute_pr_triage(installation_id, owner, repo, pull_number, pr_data)


async def handle_pr_summarize_request(
    installation_id: int, owner: str, repo: str, pull_number: int
) -> None:
    app_client = AppInstallationClient(installation_id)
    pr_data = await app_client.get_pull_request(owner, repo, pull_number)
    await execute_pr_triage(installation_id, owner, repo, pull_number, pr_data)


async def execute_pr_triage(
    installation_id: int,
    owner: str,
    repo: str,
    pull_number: int,
    pr_data: Dict[str, Any],
) -> None:
    app_client = AppInstallationClient(installation_id)

    files_changed = await app_client.get_pull_request_files(owner, repo, pull_number)
    available_labels = await app_client.get_repo_labels(owner, repo)

    diff_snippets: List[str] = []
    for f in files_changed[:15]:
        patch = f.get("patch", "")
        if len(patch) > 500:
            patch = patch[:500] + "\n...[truncated]"
        diff_snippets.append(f"File: {f['filename']} ({f.get('status')})\n{patch}")

    prompt = f"""You are PriestyAI, an engineer teammate reviewing changes on a PR.
Tone: Conversational, helpful teammate. No corporate buzzwords ("Automated Pull Request Triage System", "Key Observations"). Speak like a developer providing a quick summary to a colleague.

PR TITLE: {pr_data.get('title')}
PR AUTHOR: {pr_data.get('user', {}).get('login')}
PR BODY: {pr_data.get('body') or 'No description provided.'}

AVAILABLE REPO LABELS:
{available_labels}

CHANGED FILES & DIFFS:
{chr(10).join(diff_snippets)}

TASK:
1. Write a natural, friendly summary for the team:
   - What this PR introduces or changes.
   - Any key details or files to note.
2. Pick 1-3 appropriate labels strictly from AVAILABLE REPO LABELS list.

Return JSON:
{{
  "summary": "Natural markdown overview...",
  "selected_labels": ["enhancement"]
}}
"""

    result = await llm_client.generate_json(
        prompt=prompt,
        system_prompt="You are a friendly senior developer writing natural PR summaries. Output JSON only.",
        model_tier="routing",
    )

    summary_text = result.get("summary", "Here's a quick summary of this PR.")
    selected_labels = result.get("selected_labels", [])
    valid_labels = [l for l in selected_labels if l in available_labels]

    if valid_labels:
        await app_client.add_labels(owner, repo, pull_number, valid_labels)

    await machine_client.create_issue_comment(owner, repo, pull_number, summary_text)
    logger.info(f"PR Triage completed for {owner}/{repo}#{pull_number}")
