import hmac
import hashlib
import json
import asyncio
import logging
from typing import Callable, Any
import httpx
from config.settings import GITHUB_WEBHOOK_SECRET, SMEE_URL

logger = logging.getLogger("PriestyAI.SmeeService")

class SmeeWebhookService:
    def __init__(self, smee_url: str = SMEE_URL, webhook_secret: str = GITHUB_WEBHOOK_SECRET):
        self.smee_url = smee_url
        self.webhook_secret = webhook_secret
        self._listeners: list[Callable[[str, dict[str, Any]], Any]] = []
        self._running = False
        self._task: asyncio.Task | None = None

    def register_listener(self, callback: Callable[[str, dict[str, Any]], Any]):
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unregister_listener(self, callback: Callable[[str, dict[str, Any]], Any]):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def verify_signature(self, payload_bytes: bytes, signature_header: str | None) -> bool:
        if not self.webhook_secret:
            return True
        if not signature_header or not signature_header.startswith("sha256="):
            return False

        expected_sig = signature_header.replace("sha256=", "").strip()
        computed_sig = hmac.new(
            self.webhook_secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected_sig, computed_sig)

    async def start(self):
        if not self.smee_url:
            logger.info("[SmeeService] SMEE_URL is not set. Webhook SSE streaming disabled.")
            return

        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._sse_listener_loop())
        logger.info(f"[SmeeService] Started real-time SSE listener on: {self.smee_url}")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("[SmeeService] Stopped SSE webhook listener.")

    async def _sse_listener_loop(self):
        headers = {
            "Accept": "text/event-stream",
            "User-Agent": "PriestyAI-SmeeClient"
        }

        backoff = 2.0
        while self._running:
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("GET", self.smee_url, headers=headers) as response:
                        if response.status_code != 200:
                            logger.warning(f"[SmeeService] SMEE stream returned {response.status_code}. Retrying in {backoff}s...")
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 1.5, 30.0)
                            continue

                        backoff = 2.0
                        logger.info("[SmeeService] Connected to SMEE SSE stream. Listening for GitHub App events...")

                        event_name = "message"
                        data_buffer = []

                        async for line in response.aiter_lines():
                            if not self._running:
                                break

                            line = line.strip()
                            if not line:
                                if data_buffer:
                                    raw_data = "\n".join(data_buffer)
                                    data_buffer.clear()
                                    await self._handle_raw_event(event_name, raw_data)
                                event_name = "message"
                                continue

                            if line.startswith("event:"):
                                event_name = line[6:].strip()
                            elif line.startswith("data:"):
                                data_buffer.append(line[5:].strip())

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[SmeeService] Connection dropped: {e}. Reconnecting in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)

    async def _handle_raw_event(self, event_name: str, raw_data: str):
        try:
            if not raw_data or raw_data.strip() in ("", "{}", "ping"):
                return

            payload_json = json.loads(raw_data)
            body = payload_json.get("body", payload_json)
            headers = payload_json.get("headers", {})

            event_type = (
                headers.get("x-github-event")
                or headers.get("X-GitHub-Event")
                or payload_json.get("x-github-event")
                or ""
            )

            if not event_type or (event_type in ("ping", "unknown") and not body):
                return

            sig = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256")
            if sig:
                raw_body_bytes = json.dumps(body).encode("utf-8")
                if not self.verify_signature(raw_body_bytes, sig):
                    logger.warning("[SmeeService] Dropping webhook: Invalid HMAC signature.")
                    return

            action = body.get("action", "")
            logger.info(f"[SmeeService] Received GitHub Event: '{event_type}' (action='{action}')")

            for listener in list(self._listeners):
                try:
                    res = listener(event_type, body)
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
                except Exception as ex:
                    logger.warning(f"[SmeeService] Listener error: {ex}")

        except Exception as e:
            logger.debug(f"[SmeeService] Could not parse event: {e}")

smee_service = SmeeWebhookService()