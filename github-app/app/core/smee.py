import asyncio
import json
import logging
from typing import Callable, Coroutine, Any
import httpx

logger = logging.getLogger("priesty.smee")


async def run_smee_listener(
    smee_url: str,
    dispatch_event: Callable[[str, dict, dict], Coroutine[Any, Any, None]],
) -> None:
    headers = {"Accept": "text/event-stream"}

    while True:
        try:
            logger.info(f"Connecting to Smee.io proxy at: {smee_url}")
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", smee_url, headers=headers) as response:
                    if response.status_code != 200:
                        logger.error(
                            f"Smee returned status code {response.status_code}. Retrying in 5s..."
                        )
                        await asyncio.sleep(5)
                        continue

                    logger.info(
                        "Connected to Smee.io SSE stream. Ready for GitHub events."
                    )
                    buffer = ""

                    async for chunk in response.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            message, buffer = buffer.split("\n\n", 1)
                            lines = message.split("\n")
                            event_type = "message"
                            data_lines = []

                            for line in lines:
                                if line.startswith("event:"):
                                    event_type = line[6:].strip()
                                elif line.startswith("data:"):
                                    data_lines.append(line[5:].strip())

                            if data_lines and event_type == "message":
                                raw_data = "\n".join(data_lines)
                                try:
                                    parsed = json.loads(raw_data)
                                    req_headers = parsed.get("headers", {})
                                    body = parsed.get("body", {})
                                    gh_event = req_headers.get(
                                        "x-github-event"
                                    ) or parsed.get("x-github-event", "")

                                    if gh_event:

                                        asyncio.create_task(
                                            dispatch_event(gh_event, req_headers, body)
                                        )
                                except json.JSONDecodeError:
                                    logger.warning(
                                        "Failed to decode Smee JSON payload."
                                    )
                                except Exception as err:
                                    logger.error(
                                        f"Error handling Smee event: {err}",
                                        exc_info=True,
                                    )

        except asyncio.CancelledError:
            logger.info("Smee listener cancelled.")
            break
        except Exception as e:
            logger.warning(f"Smee connection dropped ({e}). Reconnecting in 3s...")
            await asyncio.sleep(3)
