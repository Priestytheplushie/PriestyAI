
import os
import logging
import aiohttp
import urllib.parse
import asyncio
import base64

logger = logging.getLogger("ImageGen")

class SafetyBlockError(Exception):
    pass

class RateLimitError(Exception):
    pass

STYLE_MODIFIERS = {
    "photorealistic": "professional photorealistic 8k portrait, cinematic lighting, sharp focus, dramatic shadows, volumetric atmosphere, octane render, highly detailed, realistic textures",
    "anime": "stunning anime illustration, vibrant colors, clean hand-drawn lineart, gentle daylight watercolor aesthetic, studio ghibli style, whimsical, highly detailed",
    "cyberpunk": "futuristic cyberpunk aesthetic, glowing neon lights, rain-slicked dark streets, high contrast, blue and magenta accent hues, volumetric smog, octane render, industrial design",
    "clay": "charming 3D toy claymation style, adorable plasticine sculpture, soft and smooth clay textures, warm cozy studio lighting, minimalist composition, blender render, cute",
    "watercolor": "beautiful classic watercolor wash painting, elegant textured canvas paper, soft flowing bleeding pigments, expressive brushstrokes, delicate, pastel color palette",
    "pixel": "detailed retro 16-bit pixel art, vibrant color palette, precise block textures, classic video game layout, clean grids, isometric perspective",
    "sketch": "hand-drawn graphite pencil sketch, fine cross-hatching detail, soft charcoal shading, textured canvas sketch paper, elegant monochrome line-art",
    "origami": "delicate folded papercraft illustration, crisp geometric paper creases, layered dimensional cutouts, soft ambient drop shadows, craft paper texture",
    "neon": "glowing vibrant neon sign design, glass tubes with electric gas light, high-contrast black backdrop, colorful ambient neon reflections, cinematic moody atmosphere",
    "oilpainting": "masterpiece oil painting on thick canvas, rich impasto brush strokes, oil paint texture and cracks, chiaroscuro lighting, classic fine art gallery style",
    "fantasy": "mythical fantasy digital painting, epic high-fantasy landscape, towering ancient structures, majestic lighting, ethereal atmospheric golden glow"
}

class ImageGenerator:
    def __init__(self):
        self.pollinations_url = "https://image.pollinations.ai/prompt/"
        
        self.cf_worker_url = os.getenv("CF_WORKER_URL", "https://img-api.priestyinc.workers.dev/")
        self.cf_api_key = os.getenv("IMG_API_KEY")

    async def generate(self, prompt: str, width: int = 1024, height: int = 1024, seed: int = None, style_key: str = None, base_image_bytes: bytes = None, strength: str = "0.6") -> bytes:
        styled_prompt = prompt
        if style_key and style_key.lower() in STYLE_MODIFIERS:
            modifier = STYLE_MODIFIERS[style_key.lower()]
            styled_prompt = f"{prompt}, {modifier}"
            
        if base_image_bytes and self.cf_api_key:
            logger.info(f"Triggering Cloudflare Worker img2img with strength {strength} | Prompt: {prompt[:30]}...")
            try:
                encoded_string = base64.b64encode(base_image_bytes).decode("utf-8")
                
                try:
                    strength_float = float(strength)
                except Exception:
                    strength_float = 0.6
                
                payload = {
                    "prompt": styled_prompt,
                    "image_b64": encoded_string,
                    "strength": strength_float
                }
                headers = {
                    "Authorization": f"Bearer {self.cf_api_key}",
                    "Content-Type": "application/json"
                }
                
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.post(self.cf_worker_url, json=payload, headers=headers, timeout=180, ssl=False) as response:
                        if response.status == 200:
                            content = await response.read()
                            return content
                        elif response.status == 422:
                            text = await response.text()
                            logger.info(f"Cloudflare 422 Safety Block: {text}")
                            raise SafetyBlockError(text)
                        elif response.status == 429:
                            logger.warning("Cloudflare Worker Rate Limit 429 hit. Falling back to Pollinations.")
                        else:
                            text = await response.text()
                            logger.error(f"Cloudflare Worker error {response.status}: {text}")
            except SafetyBlockError:
                raise
            except Exception as e:
                logger.warning(f"Cloudflare Worker failed: {e}. Falling back to Pollinations txt2img.")
                
        logger.info(f"Generating via Pollinations. Aspect: {width}x{height} | Style: {style_key} | Seed: {seed}")
        encoded_prompt = urllib.parse.quote(styled_prompt)

        params = {
            "width": str(width),
            "height": str(height),
            "nologo": "true",
            "model": "flux"
        }
        if seed is not None:
            params["seed"] = str(seed)

        query_string = urllib.parse.urlencode(params)
        request_url = f"{self.pollinations_url}{encoded_prompt}?{query_string}"

        max_attempts = 3
        backoff = 1.0
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.get(request_url, timeout=45, ssl=False) as response:
                        content = await response.read()
                        if response.status == 200 and content:
                            return content
                        else:
                            text = await response.text()
                            raise ValueError(f"Pollinations API failed with status {response.status}: {text}")
            except Exception as e:
                last_exc = e
                logger.warning(f"Pollinations attempt {attempt} failed: {e}")
                if attempt < max_attempts:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                logger.error(f"Image generation failed after {max_attempts} attempts: {last_exc}")
                raise last_exc