import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from app.config import settings
from app.core.lock_manager import lock_manager
from app.core.smee import run_smee_listener
from app.github.client import machine_client
from app.workflows.discussion_handler import (
    handle_discussion_comment,
    handle_discussion_opened,
)
from app.workflows.installation import handle_installation_event
from app.workflows.issue_triage import handle_issue_opened
from app.workflows.issue_to_pr import execute_approved_plan, handle_issue_assigned
from app.workflows.mention_router import handle_comment_created
from app.workflows.pr_review import (
    handle_pr_review_requested,
    handle_pr_review_submitted,
)
from app.workflows.pr_triage import handle_pr_opened

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
)
logger = logging.getLogger("priesty.main")


async def auto_accept_invitations_worker() -> None:
    logger.info("Starting background collaborator invitation sweeper...")
    while True:
        try:
            pending = await machine_client.list_pending_invitations()
            for inv in pending:
                inv_id = inv.get("id")
                repo_name = inv.get("repository", {}).get("full_name", "unknown")
                if inv_id:
                    success = await machine_client.accept_invitation(inv_id)
                    if success:
                        logger.info(
                            f"Successfully auto-accepted collaborator invitation for {repo_name} (ID: {inv_id})"
                        )
                    else:
                        logger.warning(
                            f"Failed to accept invitation ID {inv_id} for {repo_name}"
                        )
        except Exception as e:
            logger.debug(f"Error checking pending repository invitations: {e}")

        await asyncio.sleep(30)


def verify_signature(
    payload_bytes: bytes, secret: str, signature_header: Optional[str]
) -> bool:
    if not secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_sig = signature_header.split("sha256=", 1)[1].strip()
    mac = hmac.new(secret.encode("utf-8"), msg=payload_bytes, digestmod=hashlib.sha256)
    computed_sig = mac.hexdigest()
    return hmac.compare_digest(computed_sig, expected_sig)


def extract_resource_key(event_name: str, payload: Dict[str, Any]) -> str:
    repo_data = payload.get("repository", {})
    owner = repo_data.get("owner", {}).get("login", "")
    repo = repo_data.get("name", "")
    base_prefix = f"{owner}/{repo}" if owner and repo else "global"

    item_num = (
        payload.get("issue", {}).get("number")
        or payload.get("pull_request", {}).get("number")
        or payload.get("discussion", {}).get("number")
    )
    if item_num:
        return f"{base_prefix}#{item_num}"

    return base_prefix


async def dispatch_webhook_event(
    event_name: str, headers: Dict[str, Any], payload: Dict[str, Any]
) -> None:
    action = payload.get("action", "")
    lock_key = extract_resource_key(event_name, payload)
    logger.info(
        f"Received GitHub event: '{event_name}' (action: '{action}') for resource '{lock_key}'"
    )

    try:
        async with lock_manager.lock(lock_key):
            if event_name in ("installation", "installation_repositories"):
                await handle_installation_event(event_name, payload)

            elif event_name == "pull_request":
                if action == "opened":
                    await handle_pr_opened(payload)
                elif action == "review_requested":
                    await handle_pr_review_requested(payload)

            elif event_name == "pull_request_review" and action == "submitted":
                await handle_pr_review_submitted(payload)

            elif event_name == "issues":
                if action == "opened":
                    await handle_issue_opened(payload)
                elif action == "assigned":
                    await handle_issue_assigned(payload)

            elif (
                event_name in ("issue_comment", "pull_request_review_comment")
                and action == "created"
            ):
                await handle_comment_created(payload)

            elif event_name == "discussion" and action == "created":
                await handle_discussion_opened(payload)

            elif event_name == "discussion_comment" and action == "created":
                await handle_discussion_comment(payload)

            elif event_name == "reaction" and action == "created":
                content = payload.get("reaction", {}).get("content")
                if content in ("+1", "rocket", "eyes"):
                    issue = payload.get("issue", {})
                    if "pull_request" in issue:
                        installation_id = payload.get("installation", {}).get("id")
                        repo_data = payload.get("repository", {})
                        owner = repo_data.get("owner", {}).get("login")
                        repo = repo_data.get("name")
                        pull_number = issue.get("number")
                        logger.info(
                            f"Emoji reaction '{content}' detected on PR #{pull_number}. Triggering plan execution..."
                        )
                        await execute_approved_plan(
                            installation_id, owner, repo, pull_number
                        )

            else:
                logger.debug(f"Event '{event_name}.{action}' not handled.")
    except Exception as e:
        logger.error(
            f"Unhandled error in background event handler for {event_name}.{action} ({lock_key}): {e}",
            exc_info=True,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    invitation_task = asyncio.create_task(auto_accept_invitations_worker())

    smee_task = None
    if settings.SMEE_URL:
        logger.info("Starting Smee background worker...")
        smee_task = asyncio.create_task(
            run_smee_listener(settings.SMEE_URL, dispatch_webhook_event)
        )

    yield

    invitation_task.cancel()
    if smee_task:
        smee_task.cancel()
        try:
            await smee_task
        except asyncio.CancelledError:
            pass
    try:
        await invitation_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="PriestyAI GitHub Bot", lifespan=lifespan)


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "bot_username": settings.BOT_USERNAME,
        "app_id": settings.GITHUB_APP_ID,
    }


@app.post("/webhook")
async def webhook_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    if not x_github_event:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")

    body_bytes = await request.body()

    if settings.GITHUB_WEBHOOK_SECRET:
        if not verify_signature(
            body_bytes, settings.GITHUB_WEBHOOK_SECRET, x_hub_signature_256
        ):
            logger.warning(
                "Rejected webhook delivery with invalid HMAC-SHA256 signature."
            )
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    headers = dict(request.headers)

    background_tasks.add_task(dispatch_webhook_event, x_github_event, headers, payload)

    return Response(
        status_code=200, content='{"status": "ok"}', media_type="application/json"
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
