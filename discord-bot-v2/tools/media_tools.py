import time
import httpx
import urllib.parse
import logging
from typing import Any
from tools.registry import tool_registry, ToolExecutionContext

logger = logging.getLogger("PriestyAI.MediaTools")

@tool_registry.register(
    name="generate_image",
    description=(
        "Generates an AI image from a text prompt. "
        "Automatically displays the generated artwork with your response."
    )
)
async def generate_image(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    model: str = "flux",
    enhance: bool = True,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    logger.info(f"[generate_image] Prompt: '{prompt}' | Model: {model} | Resolution: {width}x{height}")

    encoded_prompt = urllib.parse.quote(prompt.strip())
    image_url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width={width}&height={height}&model={model}&nologo=true&enhance={'true' if enhance else 'false'}"
    )

    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            resp = await client.get(image_url)
            if resp.status_code == 200 and context:
                context.staged_image_bytes = resp.content
                context.staged_image_filename = f"image_{int(time.time())}.png"
                logger.info(f"[generate_image] Downloaded {len(resp.content)} bytes.")
            else:
                logger.warning(f"[generate_image] Download failed with status {resp.status_code}")
    except Exception as e:
        logger.error(f"[generate_image] Failed to download image bytes: {e}")

    return {
        "status": "generated",
        "image_url": image_url,
        "prompt": prompt,
        "dimensions": f"{width}x{height}"
    }