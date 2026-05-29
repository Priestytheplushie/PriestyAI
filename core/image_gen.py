import logging
import aiohttp
import urllib.parse

logger = logging.getLogger("ImageGen")

class ImageGenerator:
    def __init__(self, api_key: str = None, model_name: str = None):
        self.base_url = "https://image.pollinations.ai/prompt/"

    async def generate(self, prompt: str) -> bytes:
        logger.info(f"Generating free image for prompt: {prompt}")
        encoded_prompt = urllib.parse.quote(prompt)
        request_url = f"{self.base_url}{encoded_prompt}?width=1024&height=1024&nologo=true"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(request_url) as response:
                    if response.status == 200:
                        return await response.read()
                    else:
                        error_text = await response.text()
                        raise ValueError(f"Free Image API failed with status {response.status}: {error_text}")
        except Exception as e:
            logger.error(f"Image generation failed: {e}")
            raise e