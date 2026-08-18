import logging
from typing import Any, Dict, List
from app.config import settings
from app.github.client import AppInstallationClient

logger = logging.getLogger("priesty.installation")


async def handle_installation_event(event_name: str, payload: Dict[str, Any]) -> None:
    """
    Handles 'installation.created' and 'installation_repositories.added' events.
    Verifies App installation access for all onboarded repositories.
    """
    installation_id = payload.get("installation", {}).get("id")
    sender = payload.get("sender", {}).get("login", "unknown")

    if not installation_id:
        logger.warning("Installation event missing installation.id")
        return

    repositories: List[Dict[str, Any]] = []

    if event_name == "installation" and payload.get("action") == "created":
        repositories = payload.get("repositories", [])
    elif event_name == "installation_repositories" and payload.get("action") == "added":
        repositories = payload.get("repositories_added", [])

    if not repositories:
        logger.info(
            f"Installation event ({event_name}) received with no repositories attached."
        )
        return

    app_client = AppInstallationClient(installation_id)

    logger.info(
        f"Processing installation #{installation_id} triggered by @{sender} "
        f"across {len(repositories)} repository/repositories."
    )

    for repo_info in repositories:
        full_name = repo_info.get("full_name", "")
        if "/" not in full_name:
            continue
        owner, repo = full_name.split("/", 1)

        try:

            branch_info = await app_client.get_default_branch_sha(owner, repo)
            logger.info(
                f"Successfully onboarded {full_name} on default branch '{branch_info.get('branch')}'."
            )
        except Exception as e:
            logger.warning(
                f"Onboarding check for {full_name} encountered an issue (may be an empty repository): {e}"
            )

    logger.info(f"Installation #{installation_id} onboarding completed successfully.")
