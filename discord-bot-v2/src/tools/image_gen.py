import urllib.parse
import aiohttp
import logging
from typing import Optional, Tuple

logger = logging.getLogger("PriestyAI.ImageGen")

class ImageGenerator:
    BASE_URL = "https://image.pollinations.ai/prompt/"

    @classmethod
    def get_image_url(
        cls,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None,
        model: str = "flux"
    ) -> str:
        encoded_prompt = urllib.parse.quote(prompt.strip())
        params = [
            f"width={width}",
            f"height={height}",
            f"model={model}",
            "nologo=true",
            "enhance=true"
        ]
        if seed is not None:
            params.append(f"seed={seed}")

        return f"{cls.BASE_URL}{encoded_prompt}?{'&'.join(params)}"

    @classmethod
    async def fetch_image_bytes(
        cls,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None
    ) -> Tuple[Optional[bytes], str]:
        url = cls.get_image_url(prompt, width, height, seed)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                    if resp.status == 200:
                        image_data = await resp.read()
                        return image_data, url
                    else:
                        logger.error(f"Pollinations returned status {resp.status}")
                        return None, url
        except Exception as e:
            logger.error(f"Failed to fetch generated image: {e}")
            return None, url