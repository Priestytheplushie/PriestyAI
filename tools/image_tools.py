import urllib.parse
import aiohttp
import logging
from typing import Dict, Any

logger = logging.getLogger("PriestyAI.ImageTools")

async def execute_generate_image(
    prompt: str,
    model: str = "flux",
    width: int = 1024,
    height: int = 1024
) -> Dict[str, Any]:
    encoded_prompt = urllib.parse.quote(prompt)
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&model={model}&nologo=true"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(image_url, timeout=aiohttp.ClientTimeout(total=10.0)) as resp:
                if resp.status in (200, 302, 301):
                    return {
                        "status": "success",
                        "prompt": prompt,
                        "image_url": image_url,
                        "model": model,
                        "dimensions": f"{width}x{height}"
                    }
                else:
                    return {"status": "error", "error": f"Image API returned HTTP {resp.status}"}
    except Exception as e:
        return {
            "status": "success",
            "prompt": prompt,
            "image_url": image_url,
            "model": model,
            "dimensions": f"{width}x{height}"
        }